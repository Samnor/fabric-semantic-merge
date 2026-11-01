"""Fresh, bounded parity checks and optional independently replayable evidence."""

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import time

from .api import query_rows
from .model import default_probes
from .parity_compare import compare_numeric, numeric_policy
from .transport import ApiError
from .util import MergeError, digest, encoded, now, require, save, uid


LIMITS = [
    "Passing sampled queries and field checks does not prove all DAX/M, report visuals or RLS/OLS contexts equivalent.",
    "Queries run sequentially as the signed-in caller; there is no atomic data snapshot across models.",
    "Keep source data/refresh state stable. Definition stability does not establish data freshness or equal refresh watermarks.",
    "Comparison defaults to exact JSON row multisets; numeric tolerance requires an explicit per-probe keyed_numeric policy. Keys, nulls, booleans and nonnumeric values remain exact.",
    "Optional stability checks repeat both queries; unchanged sampled answers do not prove an atomic snapshot or rule out intermediate changes.",
    "Default queries cover table row counts and unfiltered ordinary measures; add important filter/grouping/relationship contexts explicitly.",
]


def choose_probes(models, groups):
    result = [
        p for side in ("left", "right") for p in default_probes(models[side], side)
    ]
    for supplied in groups:
        require(isinstance(supplied, list), "Probes must be a list")
        names = set()
        for probe in supplied:
            require(isinstance(probe, dict), "Each probe must be an object")
            require(
                set(probe)
                <= {
                    "name",
                    "source",
                    "query",
                    "target_query",
                    "description",
                    "comparison",
                },
                "Unknown probe fields",
            )
            numeric_policy(probe.get("comparison"))
            name, source = probe.get("name"), probe.get("source")
            require(
                isinstance(name, str) and bool(name.strip()),
                "Probe needs a nonempty name",
            )
            require(name not in names, "Duplicate supplied probe name: " + name)
            names.add(name)
            require(source in ("left", "right"), "Probe source must be left/right")
            for field in ("query", "target_query"):
                if field == "query" or field in probe:
                    require(
                        isinstance(probe.get(field), str)
                        and bool(probe[field].strip()),
                        "Probe needs nonempty " + field,
                    )
            original = next(
                (i for i, p in enumerate(result) if p["name"] == name), None
            )
            if original is not None:
                require(
                    result[original]["source"] == source,
                    "A probe override cannot change its source model",
                )
                result[original] = dict(probe)
            else:
                result.append(dict(probe))
    require(result, "No validation queries; provide explicit probes")
    require(
        {p["source"] for p in result} == {"left", "right"},
        "Parity must include queries for both original models",
    )
    return result


def compare_rows(source, target, comparison=None):
    left, right = (
        Counter(encoded(r) for r in source),
        Counter(encoded(r) for r in target),
    )
    missing, extra = left - right, right - left

    def differences(values):
        return {
            "row_occurrences": sum(values.values()),
            "distinct_rows": len(values),
            "samples": [
                {"row": json.loads(row), "count": values[row]}
                for row in sorted(values)[:10]
            ],
            "samples_truncated": len(values) > 10,
        }

    result = {
        "equal": left == right,
        "source_rows": len(source),
        "target_rows": len(target),
        "source_sha256": digest(sorted(left.elements())),
        "target_sha256": digest(sorted(right.elements())),
        "missing_from_target": differences(missing),
        "unexpected_in_target": differences(extra),
    }
    if comparison is not None:
        result.update(compare_numeric(source, target, comparison))
    return result


def comparison_summary(value):
    return {
        k: v
        for k, v in value.items()
        if k not in ("missing_from_target", "unexpected_in_target", "cell_differences")
    }


def result_signal(source, target):
    if not source and not target:
        return "empty_both"
    values = [v for rows in (source, target) for row in rows for v in row.values()]
    if not values:
        return "no_values_both"
    if source and target and all(v is None for v in values):
        return "blank_both"
    return "values_observed"


def stability_result(rows):
    if any(value is None for value in rows):
        return {"status": "error"}, None
    source, target, source_repeat, target_repeat = rows
    comparisons = {
        "source": compare_rows(source, source_repeat),
        "target": compare_rows(target, target_repeat),
    }
    return {
        "status": "observed_stable"
        if all(v["equal"] for v in comparisons.values())
        else "changed",
        "source_unchanged": comparisons["source"]["equal"],
        "target_unchanged": comparisons["target"]["equal"],
    }, comparisons


def coverage_report(models, probes, checks):
    defaults = {
        p["name"]: p
        for side in ("left", "right")
        if side in models
        for p in default_probes(models[side], side)
    }
    sources = {}
    warnings = []
    for side in ("left", "right"):
        selected = [p for p in probes if p["source"] == side]
        automatic = sum(
            p["name"] in defaults
            and p["query"] == defaults[p["name"]]["query"]
            and p.get("target_query", p["query"]) == p["query"]
            for p in selected
        )
        observed = [c for c in checks if c["source"] == side]
        sources[side] = {
            "planned": len(selected),
            "executed": len(observed),
            "default_queries": automatic,
            "custom_or_overridden": len(selected) - automatic,
            "numeric_tolerance_probes": sum(
                p.get("comparison") is not None for p in selected
            ),
            "passed_without_values": sum(
                c["status"] == "passed"
                and c.get("signal") in ("empty_both", "no_values_both", "blank_both")
                for c in observed
            ),
        }
        if selected and automatic == len(selected):
            warnings.append(
                side
                + ": only default unfiltered probes; add important report filters/grouped totals."
            )
        if sources[side]["passed_without_values"]:
            warnings.append(
                side
                + ": some passing probes returned no values or only nulls on both sides; add populated contexts."
            )
    return {
        "sources": sources,
        "warnings": warnings,
        "scope": "Custom query counts describe supplied probes, not assessed coverage of visuals, filters or effective users.",
    }


def schema_parity(models):
    target = {
        t["name"].casefold(): t
        for t in models["target"]["bim"]["model"].get("tables", [])
    }
    checks = {}
    for side in ("left", "right"):
        issues, counts = [], {"tables": 0, "columns": 0, "measures": 0}
        for table in models[side]["bim"]["model"].get("tables", []):
            counts["tables"] += 1
            actual = target.get(table["name"].casefold())
            if actual is None:
                issues.append({"kind": "missing_table", "table": table["name"]})
            for field, kind in (("columns", "column"), ("measures", "measure")):
                found = {c["name"].casefold(): c for c in (actual or {}).get(field, [])}
                for expected in table.get(field, []):
                    counts[field] += 1
                    candidate = found.get(expected["name"].casefold())
                    if candidate is None:
                        issues.append(
                            {
                                "kind": "missing_" + kind,
                                "table": table["name"],
                                "name": expected["name"],
                            }
                        )
                    elif kind == "column" and expected.get("dataType") != candidate.get(
                        "dataType"
                    ):
                        issues.append(
                            {
                                "kind": "column_type",
                                "table": table["name"],
                                "name": expected["name"],
                                "source": expected.get("dataType"),
                                "target": candidate.get("dataType"),
                            }
                        )
        checks[side] = {
            "status": "failed" if issues else "passed",
            "fields_checked": counts,
            "issues": issues,
        }
    return {
        "status": "failed" if any(c["issues"] for c in checks.values()) else "passed",
        "sources": checks,
    }


def _status(report):
    if (
        report["errors"]
        or report["schema"]["status"] == "not_run"
        or not report["planned_probes"]
        or len(report["checks"]) != report["planned_probes"]
        or any(c["status"] == "error" for c in report["checks"])
        or any(
            c.get("stability", {}).get("status") in ("changed", "error")
            for c in report["checks"]
        )
    ):
        return "incomplete"
    return (
        "failed"
        if report["schema"]["status"] == "failed"
        or any(c["status"] == "mismatch" for c in report["checks"])
        else "passed"
    )


class Evidence:
    def __init__(self, directory):
        self.directory = Path(directory) if directory is not None else None
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.files = {}

    def write(self, name, value, *, text=False):
        if self.directory is None:
            return
        path = self.directory / name
        if text:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
        else:
            save(path, value, exclusive=True)
        content = path.read_bytes()
        self.files[name] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }


def _query(api, identity, query, evidence, prefix, wait):
    wait()
    record = {"model": identity, "query": query, "started_utc": now()}
    start = time.monotonic()
    rows = None
    try:
        response = api.query_response(
            identity["workspace_id"], identity["model_id"], query
        )
        record.update({"http_status": response.status, "response_body": response.body})
        require(response.status == 200, "Unexpected DAX HTTP status")
        record["response_headers"] = {
            k: v
            for k, v in response.headers.items()
            if k.lower() in ("requestid", "activityid", "x-ms-request-id", "date")
        }
        rows = query_rows(response.body)
        record.update({"status": "ok", "row_count": len(rows)})
    except (MergeError, ValueError, OSError) as exc:
        record.update(
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__}
        )
        if isinstance(exc, ApiError):
            record.update({"http_status": exc.status, "response_body": exc.body})
    record["elapsed_ms"] = round((time.monotonic() - start) * 1000, 3)
    evidence.write("queries/" + prefix + ".dax", query + "\n", text=True)
    evidence.write("results/" + prefix + ".json", record)
    summary = {k: v for k, v in record.items() if k not in ("query", "response_body")}
    if evidence.directory is not None:
        summary["evidence"] = "results/" + prefix + ".json"
    return summary, rows


def run_parity(
    api,
    left,
    right,
    target,
    *,
    probes=None,
    base_probes=None,
    out=None,
    max_queries=500,
    query_interval=0.55,
    check_stability=False,
    provenance=None,
):
    require(
        isinstance(max_queries, int) and max_queries > 0, "max-queries must be positive"
    )
    require(
        math.isfinite(query_interval) and query_interval >= 0,
        "query-interval must be finite and nonnegative",
    )
    require(type(check_stability) is bool, "check-stability must be boolean")
    identities = {
        side: {"workspace_id": uid(ref[0]), "model_id": uid(ref[1])}
        for side, ref in (("left", left), ("right", right), ("target", target))
    }
    require(
        len({r["model_id"] for r in identities.values()}) == 3,
        "Select two distinct originals and a distinct merged target",
    )
    evidence = Evidence(out)
    report = {
        "schema_version": 2,
        "renderer_version": 2,
        "kind": "semantic_model_parity",
        "status": "incomplete",
        "models": identities,
        "started_utc": now(),
        "provenance": provenance,
        "max_queries": max_queries,
        "queries_submitted": 0,
        "query_interval_seconds": query_interval,
        "check_stability": check_stability,
        "schema": {"status": "not_run"},
        "checks": [],
        "errors": [],
        "limits": LIMITS,
    }
    models = {}

    def definition(side, phase):
        try:
            ref = identities[side]
            value = api.definition(ref["workspace_id"], ref["model_id"])
            evidence.write(f"definitions/{side}-{phase}.json", value)
            return value
        except (MergeError, ValueError, OSError) as exc:
            report["errors"].append(
                {"phase": "definition_" + phase, "model": side, "error": str(exc)}
            )
            return None

    for side in identities:
        value = definition(side, "before")
        if value is not None:
            models[side] = value
    selected = []
    if len(models) == 3:
        try:
            selected = choose_probes(
                models, [base_probes or [], probes if probes is not None else []]
            )
            require(
                (4 if check_stability else 2) * len(selected) <= max_queries,
                "Parity query budget exceeded before query submission",
            )
            report["schema"] = schema_parity(models)
        except (MergeError, ValueError, KeyError, TypeError) as exc:
            report["errors"].append({"phase": "preflight", "error": str(exc)})
    evidence.write("probes.json", selected)
    report["definition_sha256_before"] = {k: digest(v) for k, v in models.items()}
    last_query = None

    def wait():
        nonlocal last_query
        if last_query is not None:
            time.sleep(max(0, query_interval - (time.monotonic() - last_query)))
        last_query = time.monotonic()

    if not report["errors"]:
        for index, probe in enumerate(selected, 1):
            prefix = f"{index:04d}"
            source, source_rows = _query(
                api,
                identities[probe["source"]],
                probe["query"],
                evidence,
                prefix + "-source",
                wait,
            )
            report["queries_submitted"] += 1
            target_result, target_rows = _query(
                api,
                identities["target"],
                probe.get("target_query", probe["query"]),
                evidence,
                prefix + "-target",
                wait,
            )
            report["queries_submitted"] += 1
            check = {
                "index": index,
                "name": probe["name"],
                "source": probe["source"],
                "status": "error",
                "source_result": source,
                "target_result": target_result,
            }
            if source_rows is not None and target_rows is not None:
                check["signal"] = result_signal(source_rows, target_rows)
                try:
                    comparison = compare_rows(
                        source_rows, target_rows, probe.get("comparison")
                    )
                    check.update(
                        {
                            "status": "passed" if comparison["equal"] else "mismatch",
                            "comparison": comparison_summary(comparison),
                        }
                    )
                    evidence.write("diffs/" + prefix + ".json", comparison)
                except (MergeError, ValueError) as exc:
                    check["comparison_error"] = str(exc)
            check["stability"] = {"status": "not_requested"}
            if check_stability:
                source_repeat, source_repeat_rows = _query(
                    api,
                    identities[probe["source"]],
                    probe["query"],
                    evidence,
                    prefix + "-source-repeat",
                    wait,
                )
                report["queries_submitted"] += 1
                target_repeat, target_repeat_rows = _query(
                    api,
                    identities["target"],
                    probe.get("target_query", probe["query"]),
                    evidence,
                    prefix + "-target-repeat",
                    wait,
                )
                report["queries_submitted"] += 1
                check["source_repeat_result"], check["target_repeat_result"] = (
                    source_repeat,
                    target_repeat,
                )
                check["stability"], changes = stability_result(
                    [source_rows, target_rows, source_repeat_rows, target_repeat_rows]
                )
                if changes is not None:
                    evidence.write("stability/" + prefix + ".json", changes)
            report["checks"].append(check)
        report["definition_sha256_after"] = {}
        for side in identities:
            value = definition(side, "after")
            if value is not None:
                report["definition_sha256_after"][side] = digest(value)
                if digest(value) != report["definition_sha256_before"][side]:
                    report["errors"].append(
                        {
                            "phase": "definition_drift",
                            "model": side,
                            "error": "Definition changed during parity checks",
                        }
                    )
    report["counts"] = {
        state: sum(c["status"] == state for c in report["checks"])
        for state in ("passed", "mismatch", "error")
    }
    report["planned_probes"] = len(selected)
    report["coverage"] = coverage_report(models, selected, report["checks"])
    report["status"] = _status(report)
    report["completed_utc"] = now()
    report["evidence_directory"] = (
        str(evidence.directory) if evidence.directory is not None else None
    )
    evidence.write("report.json", report)
    evidence.write("README.md", render_report(report), text=True)
    if evidence.directory is not None:
        save(
            evidence.directory / "manifest.json",
            {
                "schema_version": 2,
                "kind": "semantic_model_parity_evidence",
                "files": evidence.files,
            },
            exclusive=True,
        )
    return report


def render_report(report):
    renderer = report.get("renderer_version", 1)
    require(renderer in (1, 2), "Unsupported parity report renderer")
    error_label = "probe errors" if renderer >= 2 else "query errors"
    setup_label = "Setup/definition errors" if renderer >= 2 else "Setup/drift errors"
    lines = [
        "# Semantic model parity",
        "",
        "Status: **" + report["status"] + "**",
        "",
        f"Queries submitted: {report['queries_submitted']}/{report['max_queries']}. Passed probes: {report['counts']['passed']}; mismatches: {report['counts']['mismatch']}; {error_label}: {report['counts']['error']}.",
        "",
        "Field compatibility: **"
        + report["schema"]["status"]
        + "**. "
        + setup_label
        + ": "
        + str(len(report["errors"]))
        + ".",
        "",
        "| # | Probe | Original | Outcome |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {c['index']:04d} | {c['name'].replace('|', '/').replace(chr(10), ' ')} | {c['source']} | {c['status']} |"
        for c in report["checks"]
    )
    if report.get("schema_version", 1) >= 2:
        lines += [
            "",
            "## Coverage and stability",
            "",
            "Repeated data observations: **"
            + ("requested" if report["check_stability"] else "not requested")
            + "**.",
            "",
            "| Original | Planned | Executed | Custom/overridden | Empty/null passes |",
            "| --- | --- | --- | --- | --- |",
        ]
        for side, coverage in report["coverage"]["sources"].items():
            lines.append(
                f"| {side} | {coverage['planned']} | {coverage['executed']} | {coverage['custom_or_overridden']} | {coverage['passed_without_values']} |"
            )
        lines += [
            "",
            *["- " + warning for warning in report["coverage"]["warnings"]],
            "",
        ]
        if report["check_stability"]:
            if renderer >= 2:
                changed = sum(
                    c["stability"]["status"] == "changed" for c in report["checks"]
                )
                missing = sum(
                    c["stability"]["status"] == "error" for c in report["checks"]
                )
                lines += [
                    f"Data observations changed: **{changed}**; unavailable: **{missing}**.",
                    "",
                ]
            lines += ["| Probe | Data observations |", "| --- | --- |"]
            lines += [
                f"| {c['index']:04d} | {c['stability']['status']} |"
                for c in report["checks"]
            ]
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            *["- " + value for value in report["limits"]],
            "",
            "See report.json for field/type issues, setup errors and capture times; results/ for API bodies; queries/ for exact DAX; diffs/ for row differences; definitions/ for before/after definitions.",
            "",
            "This folder contains private model definitions and query data. It contains no access tokens. Verify locally with `fabric-merge verify-parity THIS_FOLDER`. This command does not mark a migration validated or authorize report cutover.",
            "",
        ]
    )
    return "\n".join(lines)


def verify_evidence(directory):
    from .parity_verify import verify_evidence as verify

    try:
        return verify(directory)
    except MergeError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise MergeError("Malformed parity evidence: " + str(exc)) from exc
