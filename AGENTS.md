# Agent instructions

This standalone Python 3.11+ CLI consolidates compatible Microsoft Fabric Import semantic models. Use four-space indentation, snake_case and Ruff formatting. Runtime has no third-party Python dependencies; Azure CLI supplies tokens. Never persist or print tokens.

## Development

- Install: `python -m pip install -e '.[dev]'` in a virtual environment.
- Offline suite: `python -B -m unittest discover -s tests -t . -v`.
- Style: `ruff check src tests scripts formal/check.py` and `ruff format --check src tests scripts formal/check.py`.
- Distribution: `python -m build`, `python -m twine check --strict dist/*`, then `python scripts/check_release.py dist`. The last command validates package contents and metadata without uploading anything. Use a clean/fresh distribution folder for each release candidate.
- Loopback demonstration: `python -B -m tests.demo --out runs/NEW_NAME`. It creates a fresh private plan/journal/results folder and does not call Fabric.
- Formal checks: `python -B formal/check.py --fetch-tool --out runs/formal-NEW_NAME` requires Java 17+ and downloads the pinned official TLC JAR only when absent. `python -B formal/check.py --verify runs/formal-NEW_NAME` replays saved hashes/outcomes without Java or network. See formal/README.md for the eleven finite configurations and correspondence limits.
- Release/tag checks: `python scripts/check_release.py dist --tag v0.1.0` requires a tag matching the package metadata. It checks wheel/sdist version agreement, license, runtime file bytes and excluded content. docs/releasing.md describes GitHub publication and direct Git URL installation; no upload is part of local verification.
- Algorithm safety: `python -B -m unittest tests.test_algorithm_safety -v` runs fifteen adversarial omitted-setting, mixed-label, stale-data, resumed-cutover, budget and legacy-plan checks. Shared-object omissions require explicit review except known additive collections/lineage metadata. Stage recomputes the reviewed merge/security checks locally without rewriting old artifacts. Every unfinished apply executes fresh 2N source/target queries before grants/rebinds; its total budget also reserves remaining staging work (4N initially). Failed fresh checks stop further cutover. See docs/algorithm-safety.md for compatibility and non-atomic data limits.
- Model-name validation: `python -B -m unittest tests.test_model -v` includes case-insensitive measure/column collisions within one home table, including collisions created by otherwise valid source unions. Such collisions block planning before any create; the same column name in a different table is allowed.

## Safety and scope

- Tests must use synthetic definitions and loopback services. TestClient rejects non-loopback hosts before acquiring any token. No live workload belongs in CI.
- `doctor`, `catalog`, `preflight`, `parity` and schedule suggestions perform scoped observations; definitions/results remain private. Sampled parity and matching refresh starts do not prove universal semantics, effective-user security, rendering or capacity savings.
- `stage`, `apply` and `rollback` mutate the live account. Require a reviewed concrete plan and user authorization. Never change license/capacity/identity/permissions merely to make a test pass.
- Preserve user plans, journals, receipts and original models/schedules. Unknown mutation outcomes require exact reconciliation, never automatic replay. Do not remove guards or edit pinned plans. Rollback durably fences forward work.
- Standard refresh with no handle requires a history before-image saved before POST and exactly one new ViaApi identity under exclusive target refresh control. Never invent a before-image after submission.
- Keep exclusive control of reports during cutover/recovery. Local locks do not prevent external writers or another run. Fail on detected foreign bindings.
- A structural union is not a DAX/M equivalence proof. Preserve explicit conflicts, report coverage and security reviews. Do not call accessible-workspace discovery tenant-complete.
- Keep private exports/evidence in ignored `runs/`; do not commit real tenant IDs, user identities or model definitions. The private lab pilot harness and historical raw evidence are deliberately outside the public package.
- Do not edit frozen sources or replay completed study/publication commands in private evidence directories. Preserve old bundles and use fresh output folders.
- Do not commit, push or publish unless requested. Public GitHub publication is a separate action from local preparation. Do not change schedulers or other projects.

Document exact commands and expected inputs/outputs here when adding automation. CI must not obtain cloud or publishing credentials. Distribution is directly from GitHub; there is no package-index publishing workflow.
