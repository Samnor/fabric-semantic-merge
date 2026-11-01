# Suggested refresh schedules

`fabric-merge suggest-schedule` observes both originals and proposes a weekly native schedule for their merged model. It never applies a schedule, starts a refresh, rebinds a report, retires an original schedule or writes a migration journal. Disjoint measures do not affect the schedule calculation: both sets of reports still have their original start-time expectations.

## Commands

From the repository root:

```sh
# Fresh read of the original identities in any valid old/new merge plan.
.venv/bin/fabric-merge suggest-schedule runs/PLAN \
  --capacity premium --out runs/FRESH_SCHEDULE

# Or supply both original identities; no target model is required.
.venv/bin/fabric-merge suggest-schedule \
  --left WORKSPACE_UUID/FIRST_MODEL_UUID \
  --right WORKSPACE_UUID/SECOND_MODEL_UUID \
  --capacity shared

# Offline proposal and replay; neither authenticates.
.venv/bin/fabric-merge suggest-schedule \
  --snapshot examples/refresh-schedules.json --capacity shared \
  --out runs/FRESH_EXAMPLE
.venv/bin/fabric-merge verify-schedule runs/FRESH_EXAMPLE

# Focused offline tests, including bounded exhaustive optimization oracles.
.venv/bin/python -B -m unittest tests.test_schedules -v
```

`--out` is optional. A supplied folder must be fresh. It contains the original GET bodies/errors and identities in `snapshot.json`, deterministic `suggestion.json`, readable `SCHEDULE.md` and file hashes in `manifest.json`. Files are private (0600, directory 0700). Evidence can contain private model identities. The verifier checks exact file hashes, recomputes every recommendation and re-renders the report; this is integrity/replay, not authenticated proof of a tenant observation or current settings.

New `plan` calls produce the same analysis automatically, with `--refresh-capacity shared|premium|unknown` (default unknown). Their authoritative schedule JSON files are included in the plan's artifact hashes. A missing/invalid schedule yields an incomplete informational suggestion without blocking the independent merge. Old plans still work. `suggest-schedule PLAN` reobserves live original schedules in a new snapshot without updating the old plan, creating a lock file, or requiring a staged target.

## Matching rule

Each enabled source contributes its `days × times` weekly events in its stated time zone; disabled sources contribute none. The raw disabled settings remain in the snapshot. Repeated day/time entries are deduplicated. Start events at the same day/time in both originals count twice as source expectations, but require one proposed merged run.

The default **coverage_first** candidate keeps the union of active days. Within the declared daily time-slot limit it maximizes the number of original-model start events matched exactly, then minimizes added weekly runs; time-of-day sorts break remaining ties. Slot weights are additive, so ranking them gives an exact solution for this stated objective with the day union fixed. All original times are retained when they fit. This is not an optimization of delays, source fairness, capacity cost or completion freshness. In particular, the cap may remove every time belonging to a rarely refreshed source; per-source coverage and warnings expose that tradeoff.

The **no_extra_runs** alternative enumerates every subset of the seven days, admitting only time slots whose entire day/time grid occurs in the original union. It maximizes retained original-model start events, then minimizes weekly merged runs. It may omit original events. This bounded search is an exact solution for that separate objective, not a promise that the alternative is operationally preferable.

For example, Monday 08:00 and Tuesday 16:00 produce a coverage proposal on both days at both times: four runs, two matched original events and two added slots. One native schedule cannot represent only those two original pairs. The no-extra alternative retains one. A separately designed orchestrator could express the exact union; this CLI does not create one.

Both candidates report old model runs/week, distinct original slots, proposed runs/week, per-source exact matches, every missing/extra slot and the maximum nominal delay from a missed start until the next proposed start (including wrap to next week). These delay values are wall-clock arithmetic, not elapsed minutes across DST transitions.

## Capacity, unknowns and scope

The declared target tier uses the documented daily bounds: `shared` 8 and `premium` 48 (including PPU/Fabric). It is a caller declaration, not automatic tenant/capacity discovery. With `unknown`, the report shows a conservative 8-slot candidate and a 48-slot alternative, marks `needs_capacity_review`, and withholds a PATCH body. A fully known suggestion contains `proposed_patch_body`; both-disabled sources yield only `{"value":{"enabled":false}}`. Notifications are omitted for separate owner review. `ready` means a concrete conditional proposal can be emitted, never authorization or successful deployment.

Enabled schedules require explicit days, times and a time-zone identifier. A failed GET, omitted default time, malformed schedule or different active time zones yields `incomplete` and no proposed body. Matching zones are preserved byte-for-byte, including Windows identifiers. Cross-zone/DST conversion is deliberately not guessed; even zones with the same offset today can diverge seasonally. Disabled schedules could reflect deliberate choice, inactivity or refresh failure, so their intended future use requires review.

This feature covers imported-model native weekly scheduling. It does not inspect external/API/pipeline triggers, incremental-refresh policy, Direct Lake framing, upstream readiness, refresh history/duration, effective credentials, or the target's actual refresh support. Snapshot reads are sequential and can become stale; reobserve before applying anything. Scheduled start times are service targets, not completion deadlines. Matching them does not establish freshness parity. Merged runs can process a wider dataset, so run-count reductions are not measured CU or billing savings. Source schedule retirement and actual deployment remain separate reviewed operations.

Exit codes: 0 concrete suggestion, 2 incomplete/capacity-review suggestion, 1 malformed input/transport setup/evidence failure. `verify-schedule` returns 2 for an intact replay whose underlying suggestion remains incomplete, with `verified:true` in JSON.

## Documentation and verification

Microsoft references checked 1 November 2026: [GET schedule and scopes](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-schedule-in-group), [native request fields/defaults](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/update-refresh-schedule-in-group), and [daily limits and service timing](https://learn.microsoft.com/en-us/power-bi/connect-data/refresh-scheduled-refresh). The API shape provides separate day/time arrays; treating them as a Cartesian grid is the conservative native representation used by this planner.

Tests use pure synthetic schedules and a loopback HTTP service. They compare both optimization objectives with exhaustive small-domain oracles, test overlap, Cartesian extras, limits, disabled/unknown/DST cases, original identities, file tampering/rehashed fabrication, deterministic rendering, fresh-folder protection, old plans and unchanged migration files. No live tenant schedule has been read or changed during this implementation. The existing finite TLA+ migration/recovery model does not formally prove this scheduler or universal freshness equivalence.

The public synthetic examples can be replayed with the commands above. Historical raw delivery evidence stays private. The existing finite recovery checks do not prove the schedule optimizer; exhaustive small-domain test oracles cover its stated objectives.

| Synthetic original schedules | Old model runs/week | Proposed merged runs/week | Interpretation |
| --- | --- | --- | --- |
| Both weekdays at 08:00 and 16:00 | 20 | 10 | All 20 start expectations retained; duplicate slots collapse |
| One weekdays at 08:00, the other weekdays at 16:00 | 10 | 10 | All times retained; no nominal run-count reduction |
| Monday 08:00 and Tuesday 16:00 | 2 | 4 | All times retained with two added native grid slots |

These are deterministic calendar calculations, not live measurements or capacity-saving estimates.
