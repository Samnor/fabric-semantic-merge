# Disposable compatibility pilots

Disposable Import qualification now includes fixed-data auto/enhanced and standard refresh pilots, followed by a public Web-source pilot with two populated reports. Each successful path completed migration, parity and rollback, with all owned active resources verified absent after cleanup. These observations are maintainer-reported; raw tenant evidence is private. The broader environment matrix remains unrun beyond the specific cases below. No license/capacity/tenant setting or production resource change was made.

The original portable design is preserved in [pilot-matrix.json](../examples/pilot-matrix.json). The executed corrected source definitions are synthetic [left v2](../examples/left-definition-v2.json) and [right v2](../examples/right-definition-v2.json). Both have `Sales[Value] = {10,20}`, with disjoint `Quantity=SUM(...)` and `Count=COUNTROWS(...)` measures. Expected owner results are two rows, Quantity 30 and Count 2. The data is fictional and fixed, so changing snapshots do not confound this migration. The original fixtures remain unchanged as historical evidence.

## Live results on 1 November 2026

| Case | Parity | Report migration | Closeout |
| --- | --- | --- | --- |
| Auto/enhanced, version 2 | Six probes, 24 repeated observations; all passed | Two reports rebound and restored | Verified absence, including the separately reconciled version-2 standard target |
| Standard, version 3 | Six probes, 24 repeated observations; all passed | Two reports rebound and restored | All five owned items soft-deleted, exact GET-404 receipts, no remaining or pending identities |

Version 1 failed before producing an active model: the original fixture omitted `type=calculatedTableColumn`. A terminal failed-create receipt and complete marker-filtered inventory established no matching active item. The v2 fixtures and structural validator fix that shape.

Version 2 passed auto migration, then stopped safely when standard refresh returned `202` without the expected handle. It deleted five items and retained the unobserved target. Scoped history reconciliation confirmed that refresh completed, and the first DELETE plus exact absence GET closed the sixth item without replaying a workload. Original completion/journals remain unchanged.

Version 3 ran only the standard path with the corrected tracker: a durable pre-submission history snapshot, one unique new `ViaApi` identity, observed completion, DAX validation, repeated parity, apply and rollback. Before-image absence or ambiguous records still block progress. Exclusive target refresh control remains an operational assumption, not a remote lock.

The maintainer retains the original versioned results, source snapshots and receipts privately. The private offline audit replays both 69-file parity bundles, checks request/cleanup accounting and verifies the preserved runtime bytes and both supplemental closeouts. These raw identities/receipts and the lab-specific runner are not part of the public repository. The then-current 165-test suite included seven private runner checks; public release checks are documented in [verification](verification.md). Those checks do not formally prove complete-program, job-attribution or semantic correctness.

Those fixed-data cases establish owner-level fixed-data Import correctness and empty-report API binding recovery on this trial workspace. They do not test populated visual rendering, external data refresh, gateways, effective-user RLS, other capacities, large models or performance/CU savings. Billing remains unknown.

## Public Web source and populated reports

A subsequent cohort on 1 November installed the actual public GitHub commit `6aa8dff` into a fresh Python 3.11 environment. Two originals used the same immutable Microsoft AdventureWorks workbook over HTTPS, imported the 200 lowest Sales line keys and all 11 territories, and shared an active Sales-to-Territory relationship. Their ordinary measures were disjoint: `Revenue` and `Units`. The merged model retained two shared partitions and both measures.

A separate standard-library XLSX parser supplied expected totals, a Canada filter and country groups. All six source-oracle queries matched. Staging passed ten probes, independent stability parity passed ten probes with 40 actual DAX calls, and fresh cutover validation passed another 20 calls. This cohort submitted 86 DAX calls in total: six oracle, 20 staging, 40 repeated parity and 20 fresh cutover. The 109-file parity bundle replayed offline successfully.

Both owned reports were authored with a country table and their original measure, saved once, and independently reopened before cutover, after rebinding to the merged model, and after rollback. API binding readbacks and actual browser screenshots/accessibility text were preserved separately. Every phase displayed:

| Country | Revenue report | Units report |
| --- | ---: | ---: |
| Canada | $115,360.90 | 226 |
| United States | $130,363.86 | 212 |
| Total | $245,724.75 | 438 |

The cohort used one new disposable anonymous ShareableCloud Web connection. An earlier attempt had bound an existing PersonalCloud connection successfully but refresh failed with missing credentials; that model was deleted and verified absent. The existing connection was not changed. A still earlier fixture hit a measure/column name collision before model creation; the public validator now rejects that case locally, including collisions introduced by a union. Each stopped cohort retains its original receipts and separate closeout.

The successful cohort made 265 audited REST calls, created three models, two reports and one connection, completed three refreshes, and submitted two forward plus two restoring report rebinds. Each of the six owned resources received one DELETE followed by an exact GET-404 observation. Reports were removed before models, and connection deletion required observed dependent absence. Its offline audit verified 20 frozen source files, 28 journal events, all six browser observations and request accounting. Earlier controller/reconciliation and supported-type requests add 20 within the original execution horizon, for 285 scoped calls in that accounting. No remaining or uncertain identity was reported.

This qualifies one owner-level anonymous Web/Excel Import path and two simple table visuals on the trial workspace. It does not qualify every cloud connector, credentials configuration, gateway, effective-user security context or report interaction. The workbook is immutable, and the cohort measured correctness rather than comparative refresh cost; no CU or billing savings are established.

| Pilot | What must be observed live | Additional input needed |
| --- | --- | --- |
| Import calibration | Create two disposable originals; merge, refresh, query, rebind two disposable reports, rollback and verify. Use fresh plans for auto and forced standard refresh on the trial. | CLI access to the exact lab workspace; two disposable thin report definitions |
| Cloud connection | Anonymous Web/Excel is covered above; qualify each additional connector and credential configuration with the same migration and actual-data checks. | Reviewed test connection and immutable fictional/public source with known values |
| Gateway | Same through a reviewed non-production gateway connection; verify refresh and report parity. | Accessible gateway connection and fictional/read-only test dataset; no credential changes |
| RLS | Owner and restricted-user results before/after, role/membership review and rollback. Owner-only DAX is insufficient. | Reviewed test identities/roles and effective-user evidence; no automatic audience expansion |
| Cross-workspace | Model workspace A and an owned disposable report in B; verify grants already exist, rebind/readback/rendering and restore both originals. | A second accessible test workspace and a disposable report there |

The shared-capacity fix has offline coverage. Forced standard refresh on a trial exercises that request path, but a real shared workspace is still required before claiming shared-capacity live compatibility. Fabric trial capacity cannot establish every gateway, RLS, PPU or shared-capacity configuration.

Each cohort must have a fresh private directory, immutable fixture/source hashes, a random ownership marker, exact service-returned item IDs, actual receipts and expected DAX/results. Use explicitly scoped test-workspace report discovery rather than tenant-wide scans. Before any mutation, admit a concrete cohort-specific resource/request/time budget with a cleanup reserve. This document is not an executor and does not admit a workload by itself.

Once owned originals and reports exist and the concrete cohort is admitted, the existing commands are:

```sh
.venv/bin/fabric-merge plan --left LAB_WS/OWNED_LEFT --right LAB_WS/OWNED_RIGHT \
  --target-workspace LAB_WS --name UNIQUE_OWNED_TARGET --out runs/FRESH_PILOT_PLAN \
  --security REVIEWED_SECURITY.json --refresh-mode auto \
  --report-scope workspaces --report-workspace LAB_WS
.venv/bin/fabric-merge preflight runs/FRESH_PILOT_PLAN --out runs/FRESH_PILOT_PREFLIGHT
.venv/bin/fabric-merge stage runs/FRESH_PILOT_PLAN --yes
.venv/bin/fabric-merge parity runs/FRESH_PILOT_PLAN --check-stability \
  --probes REVIEWED_CONTEXT_PROBES.json --out runs/FRESH_PILOT_PARITY
.venv/bin/fabric-merge verify-parity runs/FRESH_PILOT_PARITY
.venv/bin/fabric-merge apply runs/FRESH_PILOT_PLAN --yes
.venv/bin/fabric-merge rollback runs/FRESH_PILOT_PLAN --yes
```

For cross-workspace discovery add `--report-workspace SECOND_LAB_WS`; for standard refresh use a separate fresh plan with `--refresh-mode standard`. RLS cutover additionally needs the existing exact-target access proof and effective-user result evidence. Never mark an unavailable test as passed, relax guards to run it or reuse a production report as a disposable fixture.

Rollback retains the merged model. Pilot cleanup is a separate exact-ownership closeout: restore/read back reports first, then delete only recorded owned test reports/models, at most one DELETE per identity, and observe absence. Preserve original receipts and reconcile unknown outcomes without replay. Reused connections, gateways, workspaces and source data remain intact. A connection created exclusively for an admitted disposable cohort requires its own exact ownership and dependency-safe cleanup evidence. If any state is uncertain, stop new cohorts and report the remaining IDs. A successful API binding is not a visual-rendering or user-access test; record those outcomes separately.
