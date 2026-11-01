"""Offline bundle contract checks and deterministic replay, including v1 evidence."""

import hashlib
from pathlib import Path

from .api import query_rows
from .parity import (
    _status,
    choose_probes,
    compare_rows,
    comparison_summary,
    coverage_report,
    render_report,
    result_signal,
    schema_parity,
    stability_result,
)
from .util import MergeError, digest, load, require, uid


SIDES = {"left", "right", "target"}


def _files(directory):
    manifest = load(directory / "manifest.json")
    require(
        isinstance(manifest, dict)
        and manifest.get("schema_version") in (1, 2)
        and manifest.get("kind") == "semantic_model_parity_evidence",
        "Unsupported parity evidence manifest",
    )
    files = manifest.get("files")
    require(
        isinstance(files, dict),
        "Malformed evidence file map",
    )
    for name, expected in files.items():
        require(isinstance(name, str) and bool(name), "Invalid evidence path")
        relative = Path(name)
        require(
            not relative.is_absolute()
            and ".." not in relative.parts
            and relative.as_posix() == name,
            "Invalid evidence path",
        )
        require(
            isinstance(expected, dict)
            and isinstance(expected.get("sha256"), str)
            and type(expected.get("bytes")) is int
            and expected["bytes"] >= 0,
            "Malformed file fingerprint",
        )
        path = directory
        for part in relative.parts:
            path = path / part
            require(not path.is_symlink(), "Symlink in evidence path")
        require(
            path.stat().st_size == expected["bytes"], "Parity evidence changed: " + name
        )
        sha = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sha.update(chunk)
        require(
            sha.hexdigest() == expected["sha256"], "Parity evidence changed: " + name
        )
    require(
        {"README.md", "report.json", "probes.json"} <= files.keys(),
        "Missing required evidence files",
    )
    require(
        set(files)
        == {
            p.relative_to(directory).as_posix()
            for p in directory.rglob("*")
            if p.is_file() and p != directory / "manifest.json"
        },
        "Unexpected/missing evidence files",
    )
    return manifest


def _definitions(directory, report, expected_files):
    captured = {}
    for phase in ("before", "after"):
        fingerprints = report.get("definition_sha256_" + phase, {})
        require(
            isinstance(fingerprints, dict) and set(fingerprints) <= SIDES,
            "Invalid definition fingerprint identities",
        )
        captured[phase] = {}
        for side, expected in fingerprints.items():
            name = f"definitions/{side}-{phase}.json"
            expected_files.add(name)
            value = load(directory / name)
            require(digest(value) == expected, "Definition fingerprint mismatch")
            captured[phase][side] = value
    for side in SIDES:
        if side not in captured["before"]:
            require(
                any(
                    e.get("model") == side and e["phase"] == "definition_before"
                    for e in report["errors"]
                ),
                "Missing original definition observation",
            )
        if report["checks"]:
            require(
                set(captured["before"]) == SIDES,
                "Queries require all three before definitions",
            )
            if side not in captured["after"]:
                require(
                    any(
                        e.get("model") == side and e["phase"] == "definition_after"
                        for e in report["errors"]
                    ),
                    "Missing final definition observation",
                )
            elif (
                digest(captured["after"][side])
                != report["definition_sha256_before"][side]
            ):
                require(
                    any(
                        e.get("model") == side and e["phase"] == "definition_drift"
                        for e in report["errors"]
                    ),
                    "Unreported definition drift",
                )
    return captured["before"]


def _record(directory, report, probe, check, suffix, expected_files):
    index = check["index"]
    stem = f"{index:04d}-{suffix}"
    source = suffix.startswith("source")
    query = probe["query"] if source else probe.get("target_query", probe["query"])
    json_name = "results/" + stem + ".json"
    dax_name = "queries/" + stem + ".dax"
    expected_files.update((json_name, dax_name))
    record = load(directory / json_name)
    require(
        isinstance(record, dict) and record.get("status") in ("ok", "error"),
        "Invalid query receipt",
    )
    require(
        record["query"] == query and (directory / dax_name).read_text() == query + "\n",
        "Query evidence mismatch",
    )
    require(
        record["model"] == report["models"][probe["source"] if source else "target"],
        "Model evidence mismatch",
    )
    field = suffix.replace("-", "_") + "_result"
    summary = {k: v for k, v in record.items() if k not in ("query", "response_body")}
    summary["evidence"] = json_name
    require(check[field] == summary, "Query summary evidence mismatch")
    if record["status"] == "ok":
        require(
            record["http_status"] == 200, "Successful probe has failed HTTP evidence"
        )
        rows = query_rows(record["response_body"])
        require(record["row_count"] == len(rows), "Query row count mismatch")
        return rows
    require(
        isinstance(record.get("error"), str) and bool(record["error"]),
        "Query error missing reason",
    )
    if record.get("http_status") == 200:
        try:
            query_rows(record["response_body"])
        except (MergeError, ValueError):
            pass
        else:
            raise MergeError("Query error contradicts valid HTTP-200 result")
    return None


def verify_evidence(directory):
    directory = Path(directory).resolve()
    manifest = _files(directory)
    report = load(directory / "report.json")
    probes = load(directory / "probes.json")
    require(
        isinstance(report, dict)
        and report.get("schema_version") == manifest["schema_version"]
        and report.get("kind") == "semantic_model_parity",
        "Unsupported parity report",
    )
    version = report["schema_version"]
    require(
        isinstance(probes, list)
        and isinstance(report["checks"], list)
        and isinstance(report["errors"], list),
        "Invalid parity report collections",
    )
    require(
        isinstance(report["models"], dict) and set(report["models"]) == SIDES,
        "Evidence must identify both originals and the target",
    )
    for ref in report["models"].values():
        require(
            uid(ref["model_id"]) == ref["model_id"]
            and uid(ref["workspace_id"]) == ref["workspace_id"],
            "Noncanonical model identity",
        )
    require(
        len({r["model_id"] for r in report["models"].values()}) == 3,
        "Evidence requires three distinct model identities",
    )
    stable = report.get("check_stability", False)
    require(
        type(stable) is bool and (not stable or version >= 2),
        "Invalid stability policy",
    )
    multiplier = 4 if stable else 2
    require(
        type(report["max_queries"]) is int
        and report["max_queries"] > 0
        and report["planned_probes"] == len(probes)
        and report["queries_submitted"] == multiplier * len(report["checks"])
        and report["queries_submitted"] <= report["max_queries"],
        "Parity accounting mismatch",
    )
    expected_files = {"README.md", "report.json", "probes.json"}
    models = _definitions(directory, report, expected_files)
    if probes:
        require(set(models) == SIDES, "Probe coverage lacks definitions")
        require(
            {p["source"] for p in probes} == {"left", "right"},
            "Probe coverage must include both originals",
        )
        require(
            choose_probes(models, [probes]) == probes,
            "Default probe coverage missing or reordered",
        )
        require(
            version >= 2 or all("comparison" not in p for p in probes),
            "Numeric policy requires version 2 evidence",
        )
    for position, check in enumerate(report["checks"], 1):
        require(
            check["index"] == position and position <= len(probes),
            "Probe order/index mismatch",
        )
        probe = probes[position - 1]
        require(
            check["name"] == probe["name"] and check["source"] == probe["source"],
            "Probe evidence mismatch",
        )
        rows = [
            _record(directory, report, probe, check, side, expected_files)
            for side in ("source", "target")
        ]
        if all(r is not None for r in rows):
            if version >= 2:
                require(
                    check["signal"] == result_signal(*rows), "Result signal mismatch"
                )
            try:
                comparison = compare_rows(*rows, probe.get("comparison"))
            except (MergeError, ValueError) as exc:
                require(
                    version >= 2
                    and check["status"] == "error"
                    and check.get("comparison_error") == str(exc),
                    "Comparison failure replay mismatch",
                )
            else:
                diff_file = f"diffs/{position:04d}.json"
                expected_files.add(diff_file)
                require(
                    comparison == load(directory / diff_file), "Diff replay mismatch"
                )
                require(
                    check["status"]
                    == ("passed" if comparison["equal"] else "mismatch"),
                    "Parity outcome replay mismatch",
                )
                require(
                    check["comparison"] == comparison_summary(comparison),
                    "Comparison summary replay mismatch",
                )
        else:
            require(
                check["status"] == "error" and "comparison_error" not in check,
                "Query error evidence mismatch",
            )
        if stable:
            rows.extend(
                _record(
                    directory, report, probe, check, side + "-repeat", expected_files
                )
                for side in ("source", "target")
            )
            observed, changes = stability_result(rows)
            require(check["stability"] == observed, "Data stability replay mismatch")
            if changes is not None:
                filename = f"stability/{position:04d}.json"
                expected_files.add(filename)
                require(
                    load(directory / filename) == changes,
                    "Data stability differences mismatch",
                )
        elif version >= 2:
            require(
                check["stability"] == {"status": "not_requested"},
                "Unexpected stability evidence",
            )
    require(
        set(manifest["files"]) == expected_files,
        "Evidence file set contradicts report contract",
    )
    require(report["status"] == _status(report), "Parity overall status mismatch")
    require(
        report["counts"]
        == {
            state: sum(c["status"] == state for c in report["checks"])
            for state in ("passed", "mismatch", "error")
        },
        "Parity counts mismatch",
    )
    if report["schema"]["status"] != "not_run":
        require(
            set(models) == SIDES and schema_parity(models) == report["schema"],
            "Schema replay mismatch",
        )
    if version >= 2:
        require(
            report["coverage"] == coverage_report(models, probes, report["checks"]),
            "Coverage replay mismatch",
        )
        require(
            (directory / "README.md").read_text() == render_report(report),
            "Readable report contradicts machine report",
        )
    return {
        "kind": "parity_evidence_verification",
        "status": "verified",
        "parity_status": report["status"],
        "files_verified": len(manifest["files"]),
        "probes_replayed": len(report["checks"]),
        "cloud_requests": 0,
    }
