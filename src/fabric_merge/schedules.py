"""Read-only refresh schedule observations and deterministic weekly proposals."""

from collections import Counter
import hashlib
import itertools
import os
from pathlib import Path
import re

from .transport import ApiError
from .util import MergeError, digest, load, now, require, save, uid


DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
LIMITS = {"unknown": 8, "shared": 8, "premium": 48}
CAUTIONS = [
    "Nominal weekly start times only: source availability, refresh duration, queueing and completion freshness are not established.",
    "Weekly run counts are not capacity-cost savings; a merged refresh can process more data at each run.",
    "This covers the native imported-model weekly schedule, not API, pipeline, incremental-refresh policy or Direct Lake framing schedules.",
    "Confirm target Import-refresh eligibility, capacity, credentials and duration before manually applying a reviewed proposal.",
    "No schedule is applied or disabled; notification settings are left for separate review.",
]


def observe_schedules(api, left, right):
    """Exactly two logical GETs; unavailable is never interpreted as disabled."""
    require(uid(left[1]) != uid(right[1]), "Select two distinct semantic models")
    sources = {}
    for name, (workspace, model) in (("left", left), ("right", right)):
        record = {"workspace_id": uid(workspace), "id": uid(model)}
        try:
            record.update(
                status="observed", schedule=api.refresh_schedule(workspace, model)
            )
        except ApiError as exc:
            record.update(status="unavailable", http_status=exc.status, error=exc.body)
        except (MergeError, OSError) as exc:
            record.update(status="unavailable", error=str(exc))
        record["observed_utc"] = now()
        sources[name] = record
    return {"schema_version": 1, "sources": sources}


def normalize(value):
    require(isinstance(value, dict), "Schedule must be an object")
    require(
        type(value.get("enabled")) is bool, "Schedule needs explicit enabled=true/false"
    )
    if not value["enabled"]:
        # Retain the raw disabled schedule in the snapshot, without treating old slots as active.
        return {"enabled": False, "days": [], "times": [], "localTimeZoneId": None}
    days, times, zone = (
        value.get("days"),
        value.get("times"),
        value.get("localTimeZoneId"),
    )
    require(
        isinstance(days, list)
        and days
        and all(isinstance(d, str) and d in DAYS for d in days),
        "Enabled schedule needs explicit valid days",
    )
    require(
        isinstance(times, list) and times and len(times) <= 48,
        "Enabled schedule needs 1–48 explicit times; an omitted service-selected time is unknown",
    )
    require(
        all(
            isinstance(t, str) and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", t)
            for t in times
        ),
        "Times must be HH:MM, 00:00–23:59",
    )
    require(
        isinstance(zone, str)
        and bool(zone.strip())
        and zone == zone.strip()
        and not any(ord(c) < 32 for c in zone),
        "Enabled schedule needs an explicit localTimeZoneId",
    )
    return {
        "enabled": True,
        "days": [d for d in DAYS if d in days],
        "times": sorted(set(times)),
        "localTimeZoneId": zone,
    }


def events(schedule):
    return set(itertools.product(schedule["days"], schedule["times"]))


def event_key(event):
    return DAYS.index(event[0]), event[1]


def event_list(values):
    return [{"day": d, "time": t} for d, t in sorted(values, key=event_key)]


def minute(event):
    return DAYS.index(event[0]) * 1440 + int(event[1][:2]) * 60 + int(event[1][3:])


def candidate(days, times, zone, source_events):
    slots = set(itertools.product(days, times))
    union = set().union(*source_events.values())
    coverage = {}
    for name, old in source_events.items():
        missing = old - slots
        delays = (
            [
                min((minute(new) - minute(old_slot)) % 10080 for new in slots)
                for old_slot in missing
            ]
            if slots
            else []
        )
        coverage[name] = {
            "original_runs_per_week": len(old),
            "exactly_matched_runs": len(old & slots),
            "missing_runs": event_list(missing),
            "max_next_start_delay_wall_minutes": max(delays, default=0)
            if slots or not old
            else None,
        }
    return {
        "schedule": {
            "enabled": bool(slots),
            "days": days,
            "times": times,
            "localTimeZoneId": zone,
        }
        if slots
        else {"enabled": False},
        "runs_per_week": len(slots),
        "original_model_runs_per_week": sum(len(e) for e in source_events.values()),
        "original_distinct_slots_per_week": len(union),
        "exactly_matched_model_runs": sum(
            c["exactly_matched_runs"] for c in coverage.values()
        ),
        "extra_runs": event_list(slots - union),
        "coverage": coverage,
    }


def native_candidates(normalized, limit):
    source_events = {name: events(s) for name, s in normalized.items()}
    union = set().union(*source_events.values())
    days = [d for d in DAYS if any(slot[0] == d for slot in union)]
    zone = next(
        (s["localTimeZoneId"] for s in normalized.values() if s["enabled"]), None
    )
    weights = Counter(slot for values in source_events.values() for slot in values)
    times = sorted({t for _, t in union})

    # The objective is additive across times for the fixed union of source days.
    # Selecting the largest weights exactly maximizes original model start events retained.
    def rank(time, selected_days):
        weight = sum(weights[d, time] for d in selected_days)
        extras = sum((d, time) not in union for d in selected_days)
        return -weight, extras, time

    chosen = sorted(sorted(times, key=lambda t: rank(t, days))[:limit])
    cover = candidate(days, chosen, zone, source_events)
    # Enumerate all 127 possible day subsets to find the best native rectangle
    # contained in the union, so the alternative never introduces extra runs.
    best_days, best_times = [], []
    best_score = (0, 0)
    for mask in range(1, 1 << len(days)):
        subset = [d for i, d in enumerate(days) if mask & (1 << i)]
        allowed = [t for t in times if all((d, t) in union for d in subset)]
        selected = sorted(sorted(allowed, key=lambda t: rank(t, subset))[:limit])
        score = (
            sum(weights[d, t] for d in subset for t in selected),
            -len(subset) * len(selected),
        )
        if score > best_score:
            best_days, best_times, best_score = subset, selected, score
    return {
        "daily_limit": limit,
        "coverage_first": cover,
        "no_extra_runs": candidate(best_days, best_times, zone, source_events),
    }


def recommend_schedules(snapshot, capacity="unknown"):
    require(
        isinstance(capacity, str) and capacity in LIMITS,
        "Capacity must be unknown, shared or premium",
    )
    require(
        isinstance(snapshot, dict) and snapshot.get("schema_version") == 1,
        "Unsupported schedule snapshot",
    )
    sources = snapshot.get("sources")
    require(
        isinstance(sources, dict) and set(sources) == {"left", "right"},
        "Snapshot needs both originals",
    )
    normalized, problems = {}, []
    identities = []
    for name in ("left", "right"):
        source = sources[name]
        require(isinstance(source, dict), "Source must be an object")
        identities.append((uid(source["workspace_id"]), uid(source["id"])))
        if source.get("status") != "observed":
            problems.append(
                name
                + ": refresh schedule could not be observed; it is not assumed disabled"
            )
            continue
        try:
            normalized[name] = normalize(source.get("schedule"))
        except MergeError as exc:
            problems.append(name + ": " + str(exc))
    require(identities[0][1] != identities[1][1], "Select two distinct semantic models")
    zones = {s["localTimeZoneId"] for s in normalized.values() if s["enabled"]}
    if len(zones) > 1:
        problems.append(
            "Active source time zones differ. A fixed-offset conversion can be wrong across daylight-saving changes; align reviewed zones or design an explicit multi-zone orchestration."
        )
    result = {
        "kind": "refresh_schedule_suggestion",
        "schema_version": 1,
        "snapshot_sha256": digest(snapshot),
        "originals": {
            name: {key: sources[name][key] for key in ("workspace_id", "id")}
            for name in ("left", "right")
        },
        "capacity": capacity,
        "capacity_basis": "caller-declared; not tenant-verified"
        if capacity != "unknown"
        else "unknown; show conservative 8-slot proposal and 48-slot alternative",
        "normalized_sources": normalized,
        "problems": problems,
        "cautions": list(CAUTIONS),
        "ready": False,
        "status": "incomplete",
        "recommended": None,
        "alternatives": {},
        "proposed_patch_body": None,
        "objective": "Keep the union of active source days; maximize exactly retained original-model weekly start events under the daily time-slot limit, then minimize added runs; lexical time breaks ties. No claim of optimal completion freshness or cost.",
    }
    if problems:
        return result
    active = any(s["enabled"] for s in normalized.values())
    variants = {
        str(k): native_candidates(normalized, k)
        for k in sorted(
            {LIMITS[capacity], 48} if capacity == "unknown" else {LIMITS[capacity]}
        )
    }
    selected = variants[str(LIMITS[capacity])]["coverage_first"]
    result.update(
        alternatives=variants,
        recommended=selected,
        status="needs_capacity_review"
        if active and capacity == "unknown"
        else "suggested",
        ready=not active or capacity != "unknown",
    )
    if any(not s["enabled"] for s in normalized.values()):
        result["cautions"].append(
            "Disabled source schedules contribute no active slots. Verify whether they were deliberately disabled or paused by the service before migration."
        )
    if len(zones) == 1:
        result["cautions"].append(
            "The original time-zone identifier is preserved unchanged; delay metrics are nominal wall-clock minutes, not elapsed minutes across DST transitions."
        )
    if any(c["missing_runs"] for c in selected["coverage"].values()):
        result["cautions"].append(
            "The daily limit prevents retaining every original time. Missing slots and their next-start delays require review; tied slot weights favor earlier times rather than source fairness."
        )
    if selected["extra_runs"]:
        result["cautions"].append(
            "Native days × times adds refreshes absent from the originals. Compare the no-extra-runs alternative or use separately designed orchestration for an exact union."
        )
    if result["ready"]:
        result["proposed_patch_body"] = {"value": selected["schedule"]}
    return result


def render_suggestion(result, artifact_name="suggestion.json"):
    lines = [
        "# Suggested merged refresh schedule",
        "",
        "Status: " + result["status"],
        "",
        "Capacity: " + result["capacity"] + "; " + result["capacity_basis"],
        "",
    ]
    for name in ("left", "right"):
        original = result["originals"][name]
        lines += [f"{name}: {original['workspace_id']}/{original['id']}", ""]
    for problem in result["problems"]:
        lines += ["- " + problem]
    selected = result["recommended"]
    if selected:
        schedule = selected["schedule"]
        if schedule["enabled"]:
            lines += [
                "Time zone: " + schedule["localTimeZoneId"],
                "",
                "Days: " + ", ".join(schedule["days"]),
                "",
                "Times: " + ", ".join(schedule["times"]),
                "",
            ]
        else:
            lines += [
                "Both source schedules are disabled; proposed schedule stays disabled.",
                "",
            ]
        lines += [
            f"Old model runs/week: {selected['original_model_runs_per_week']}. Proposed merged runs/week: {selected['runs_per_week']}. Added weekly slots: {len(selected['extra_runs'])}.",
            "",
            "| Original | Start events matched | Missing | Maximum next-start delay (wall minutes) |",
            "| --- | --- | --- | --- |",
        ]
        for name in ("left", "right"):
            c = selected["coverage"][name]
            lines += [
                f"| {name} | {c['exactly_matched_runs']}/{c['original_runs_per_week']} | {len(c['missing_runs'])} | {c['max_next_start_delay_wall_minutes']} |"
            ]
        lines += [
            "",
            "| Limit/day | Policy | Merged runs/week | Left matched | Right matched | Added slots |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for limit in sorted(result["alternatives"], key=int):
            alternatives = result["alternatives"][limit]
            for policy in ("coverage_first", "no_extra_runs"):
                alt = alternatives[policy]
                lines += [
                    f"| {limit} | {policy} | {alt['runs_per_week']} | {alt['coverage']['left']['exactly_matched_runs']} | {alt['coverage']['right']['exactly_matched_runs']} | {len(alt['extra_runs'])} |"
                ]
        lines += [
            "",
            "Per-slot differences are in "
            + artifact_name
            + ". The no_extra_runs alternative maximizes retained original-model start events among all native grids contained in the original union, then minimizes weekly runs.",
            "",
        ]
    lines += [
        result["objective"],
        "",
        *["- " + c for c in result["cautions"]],
        "",
        "Proposal only. No PATCH, refresh, rebind or journal change was submitted.",
        "",
    ]
    return "\n".join(lines)


def write_suggestion(directory, snapshot, result):
    directory = Path(directory)
    require(not directory.exists(), "Choose a fresh schedule evidence directory")
    directory.mkdir(parents=True, mode=0o700)
    save(directory / "snapshot.json", snapshot, exclusive=True)
    save(directory / "suggestion.json", result, exclusive=True)
    write_report(directory / "SCHEDULE.md", render_suggestion(result))
    manifest = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in ("snapshot.json", "suggestion.json", "SCHEDULE.md")
    }
    save(
        directory / "manifest.json",
        {"schema_version": 1, "files": manifest},
        exclusive=True,
    )


def write_report(path, content):
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
        encoding="utf-8",
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def verify_suggestion(directory):
    directory = Path(directory)
    manifest = load(directory / "manifest.json")
    require(
        isinstance(manifest, dict)
        and manifest.get("schema_version") == 1
        and isinstance(manifest.get("files"), dict)
        and set(manifest["files"])
        == {"snapshot.json", "suggestion.json", "SCHEDULE.md"},
        "Invalid schedule manifest",
    )
    for name, expected in manifest["files"].items():
        require(
            hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected,
            "Schedule evidence changed: " + name,
        )
    snapshot, result = (
        load(directory / "snapshot.json"),
        load(directory / "suggestion.json"),
    )
    require(
        isinstance(result, dict) and isinstance(result.get("capacity"), str),
        "Invalid schedule result",
    )
    require(
        result == recommend_schedules(snapshot, result["capacity"]),
        "Schedule recommendation does not replay",
    )
    require(
        (directory / "SCHEDULE.md").read_text() == render_suggestion(result),
        "Schedule report does not replay",
    )
    return {
        "verified": True,
        "status": result["status"],
        "ready": result["ready"],
        "scope": "Offline integrity/replay, not live schedule or capacity verification",
    }
