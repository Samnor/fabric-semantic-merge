# Disposable compatibility pilots

The first fixed-data Import calibration has now run live after the CLI was signed in as the Fabric Lab user. Auto/enhanced and standard refresh each completed a successful migration, parity check and rollback of two disposable empty-report bindings. All owned active items were verified absent after cleanup. The observations below are maintainer-reported; raw tenant evidence is not published. The broader matrix below remains unrun beyond that calibration. No license/capacity/tenant setting or production resource change was made.

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

These live cases establish owner-level fixed-data Import correctness and empty-report API binding recovery on this trial workspace. They do not test populated visual rendering, external data refresh, gateways, effective-user RLS, other capacities, large models or performance/CU savings. Billing remains unknown.

| Pilot | What must be observed live | Additional input needed |
| --- | --- | --- |
| Import calibration | Create two disposable originals; merge, refresh, query, rebind two disposable reports, rollback and verify. Use fresh plans for auto and forced standard refresh on the trial. | CLI access to the exact lab workspace; two disposable thin report definitions |
| Cloud connection | Same migration through an existing reviewed test cloud connection; validate actual data and connection readback. | Connection ID and immutable fictional test source with known expected values |
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

Rollback retains the merged model. Pilot cleanup is a separate exact-ownership closeout: restore/read back reports first, then delete only recorded owned test reports/models, at most one DELETE per identity, and observe absence. Preserve original receipts and reconcile unknown outcomes without replay. Reused connections, gateways, workspaces and source data remain intact. If any state is uncertain, stop new cohorts and report the remaining IDs. A successful API binding is not a visual-rendering or user-access test; record those outcomes separately.
