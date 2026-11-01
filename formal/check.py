"""Run pinned TLC over finite recovery models; never access Fabric or Azure."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal"
TOOL_URL = "https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar"
TOOL_SHA256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
SAFETY = (
    "TypeOK AtMostOnce CutoverGated NoFalseCompletion NoFalseRollback "
    "RollbackFence NoForeignOverwrite"
)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cases():
    for count in (1, 2, 3):
        yield {"name": f"fixed-{count}", "reports": count, "invariants": SAFETY}
    for name, invariant in (
        ("completion", "NoFalseCompletion"),
        ("rollback", "NoFalseRollback"),
        ("direction", "RollbackFence"),
    ):
        yield {
            "name": f"legacy-{name}",
            "reports": 2,
            "legacy": True,
            "invariants": invariant,
            "violation": invariant,
        }
    for name, option, invariant in (
        ("foreign-race", "foreign", "NoForeignOverwrite"),
        ("mutant-retry", "retry", "AtMostOnce"),
        ("mutant-gate", "skip_validation", "CutoverGated"),
    ):
        yield {
            "name": name,
            "reports": 1,
            option: True,
            "invariants": invariant,
            "violation": invariant,
        }
    for name, invariant in (
        ("reachable-complete", "NeverComplete"),
        ("reachable-restore", "NeverRollback"),
    ):
        yield {
            "name": name,
            "reports": 1,
            "invariants": invariant,
            "violation": invariant,
        }


def config(case):
    reports = ", ".join(f"r{n + 1}" for n in range(case["reports"]))
    lines = ["SPECIFICATION Spec", "CONSTANTS", f"  Reports = {{{reports}}}"]
    for constant, option in (
        ("Legacy", "legacy"),
        ("AllowForeign", "foreign"),
        ("ReplayUnknown", "retry"),
        ("SkipValidation", "skip_validation"),
    ):
        lines.append(f"  {constant} = {'TRUE' if case.get(option) else 'FALSE'}")
    lines.extend(
        [
            "INVARIANTS " + case["invariants"],
            "CHECK_DEADLOCK FALSE",
        ]
    )
    return "\n".join(lines) + "\n"


def inputs():
    paths = [
        *sorted((ROOT / "src").rglob("*.py")),
        *sorted((ROOT / "tests").rglob("*.py")),
        FORMAL / "Migration.tla",
        FORMAL / "check.py",
    ]
    return {str(p.relative_to(ROOT)): sha256(p) for p in paths}


def check_result(case, returncode, log):
    """An expected counterexample must be an invariant violation, not a tool error."""
    expected = case.get("violation")
    violations = re.findall(r"Error: Invariant (\w+) is violated\.", log)
    if expected:
        valid = returncode == 12 and violations == [expected]
    else:
        valid = (
            returncode == 0
            and "Model checking completed. No error has been found." in log
            and not violations
            and "Error:" not in log
            and "0 states left on queue" in log
        )
    if not valid:
        raise RuntimeError(
            f"Unexpected TLC outcome for {case['name']}; inspect its log"
        )
    return expected or "all_invariants_hold"


def verify(directory):
    report = json.loads((directory / "results.json").read_text())
    if report["source_sha256"] != inputs():
        raise RuntimeError("Evidence is for different source bytes; run a fresh check")
    if report["tool"]["sha256"] != TOOL_SHA256:
        raise RuntimeError("Tool pin mismatch")
    expected = list(cases())
    if [r["name"] for r in report["cases"]] != [c["name"] for c in expected]:
        raise RuntimeError("Incomplete or reordered case evidence")
    for case, row in zip(expected, report["cases"]):
        cfg = directory / (case["name"] + ".cfg")
        log = directory / (case["name"] + ".log")
        if cfg.read_text() != config(case) or sha256(cfg) != row["config_sha256"]:
            raise RuntimeError("Config evidence changed: " + case["name"])
        if sha256(log) != row["log_sha256"]:
            raise RuntimeError("Log evidence changed: " + case["name"])
        if check_result(case, row["returncode"], log.read_text()) != row["outcome"]:
            raise RuntimeError("Case outcome changed: " + case["name"])
    print("Saved model-check evidence matches current sources and all case outcomes.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", type=Path, help="Fresh evidence directory")
    mode.add_argument("--verify", type=Path, help="Verify saved evidence offline")
    parser.add_argument(
        "--fetch-tool", action="store_true", help="Fetch pinned official JAR"
    )
    parser.add_argument("--java", default="java")
    args = parser.parse_args()
    if args.verify:
        verify(args.verify)
        return
    jar = FORMAL / ".tools" / "tla2tools.jar"
    if not jar.exists() and args.fetch_tool:
        jar.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(TOOL_URL, timeout=60) as response:
            content = response.read(10 * 1024 * 1024 + 1)
        if hashlib.sha256(content).hexdigest() != TOOL_SHA256:
            raise RuntimeError("Downloaded TLC checksum mismatch; no tool executed")
        with jar.open("xb") as handle:
            handle.write(content)
    if not jar.exists() or sha256(jar) != TOOL_SHA256:
        raise RuntimeError(
            "Missing/pin mismatch for TLC; use --fetch-tool or supply pinned JAR"
        )
    java = shutil.which(args.java)
    if not java:
        raise RuntimeError("Java 17+ is required")
    args.out.mkdir(parents=True, exist_ok=False)
    before = inputs()
    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "finite safety model checking; no Python refinement or liveness proof",
        "tool": {"url": TOOL_URL, "sha256": TOOL_SHA256},
        "java": subprocess.run(
            [java, "-version"], capture_output=True, text=True, check=True
        ).stderr,
        "source_sha256": before,
        "cases": [],
    }
    for case in cases():
        name = case["name"]
        cfg = args.out.resolve() / (name + ".cfg")
        cfg.write_text(config(case))
        with tempfile.TemporaryDirectory(
            prefix="tlc-", dir=args.out.resolve()
        ) as states:
            command = [
                java,
                "-XX:+UseParallelGC",
                "-Xmx2g",
                "-cp",
                str(jar),
                "tlc2.TLC",
                "-workers",
                "1",
                "-seed",
                "1",
                "-fp",
                "0",
                "-config",
                str(cfg),
                "-metadir",
                states,
                "formal/Migration.tla",
            ]
            process = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True, timeout=120
            )
        log = process.stdout + process.stderr
        log_path = args.out / (name + ".log")
        log_path.write_text(log)
        outcome = check_result(case, process.returncode, log)
        counts = re.search(
            r"([\d,]+) states generated, ([\d,]+) distinct states found, "
            r"([\d,]+) states left on queue",
            log,
        )
        if not counts:
            raise RuntimeError("Missing state counts: " + name)
        generated, distinct, remaining = (
            int(v.replace(",", "")) for v in counts.groups()
        )
        row = {
            **case,
            "outcome": outcome,
            "returncode": process.returncode,
            "generated": generated,
            "distinct": distinct,
            "remaining": remaining,
            "log_sha256": sha256(log_path),
            "config_sha256": sha256(cfg),
            "command": command,
        }
        report["cases"].append(row)
        print(f"{name}: {outcome}, {distinct} distinct states", flush=True)
    if inputs() != before:
        raise RuntimeError("Sources changed during model checking; results not sealed")
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (args.out / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print("All finite checks and required counterexamples verified.")


if __name__ == "__main__":
    main()
