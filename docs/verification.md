# Verification and practical limits

This experimental release supports compatible Import-model structural unions and a staged report migration. The public repository includes synthetic fixtures, a stateful loopback HTTP service, adversarial recovery/evidence checks and a finite TLA+ protocol model. No cloud credentials are needed for those checks.

## Reproduce locally

```sh
python -m pip install -e '.[dev]'
python -B -m unittest discover -s tests -t . -v
ruff check src tests scripts formal/check.py
ruff format --check src tests scripts formal/check.py
python -m build
python -m twine check --strict dist/*
python scripts/check_release.py dist
python -B -m tests.demo --out runs/NEW_DIRECTORY
```

The demo produces a fresh plan, hash-chained journal and results for a simulated migration and rollback. It uses real HTTP on loopback, not Fabric or a DAX execution engine. Tests cover compatible/disjoint measures, conflicts, API errors and long-running operations, refresh tracking, query pacing, scoped report inventories, access-review gates, uncertain outcomes, foreign binding changes, rollback fences, parity replay and schedule objectives. The release check validates distribution metadata and prevents private run files from entering archives.

[Formal checks](../formal/README.md) exercise seven safety invariants for one, two and three reports, plus negative/reachability controls. They do not prove Python refinement, arbitrary report counts, refreshed data, DAX/M semantics or effective-user permissions.

## Live qualification

Maintainer-run disposable calibration on 1 November 2026 passed auto/enhanced and standard refresh on a dedicated Fabric trial workspace. It used one fixed Sales table containing 10 and 20, disjoint SUM/COUNTROWS measures and two empty reports. Each path passed six probes with 24 repeated observations, switched two report bindings and restored them. Successful owned resources were deleted once and observed absent through exact-ID GET-404 receipts. Initial fixture/standard-refresh tracking failures were reconciled separately without replaying mutations.

These are maintainer-reported observations: raw tenant receipts, identities and historical study runners remain private and are not included in this repository or the package. [Pilot notes](pilots.md) explain scope and how to plan independent qualification. Live evidence does not certify arbitrary customer tenants.

## Remaining limits

- External cloud/gateway connections, effective-user RLS/OLS, cross-workspace live migration, populated report rendering, large models and real shared-capacity deployment remain unqualified.
- API binding readback does not prove every visual or slicer. Default aggregates do not prove universal semantic equivalence; add reviewed report contexts and effective-user tests.
- Concurrent GET/POST operations cannot exclude an external writer. Keep exclusive operational control of report bindings and of target refresh submissions used for history-delta tracking.
- Definitions and DAX observations are sequential, not atomic data snapshots. Stabilize data or use a common reviewed snapshot.
- M/DAX text is preserved, not locally executed or rewritten. The tool cannot retain contradictory same-name definitions simultaneously, infer query folding, append overlapping rows safely or migrate every composite dependency.
- Credentials are reused through reviewed accessible connections, not copied. Labels/audiences/role memberships still need owner review.
- Capacity/license, write/build/connection permissions and service settings must be verified for the selected deployment. A metadata pass cannot guarantee subsequent write success.
- Existing source refresh schedules remain active. Merging alone does not establish CU savings, billing savings or freshness parity. Billing was not measured.

Runtime uses the standard library and separately installed Azure CLI. CI is configured for Python 3.11–3.14 on Linux, a macOS test job and a Windows installation smoke check. Configured CI targets are not remote results until those jobs run. Platform filesystem permissions differ; protect private run folders with appropriate local access controls.
