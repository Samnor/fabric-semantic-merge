# Fabric Semantic Merge

Select two Microsoft Fabric semantic models, create a new model containing their compatible tables and measures, and rebind every directly connected Power BI report in the selected discovery scope. Authentication comes from **Azure CLI**; no application secret or SDK dependency is required.

The workflow is `catalog → plan → stage → apply`, with `status`, `reconcile` and `rollback` commands. Existing models remain available for rollback. This tool does not delete them, change workspace settings or disable their refresh schedules.

## Install

Python 3.11+ and Git are required. Install directly from GitHub:

```sh
python -m pip install 'git+https://github.com/Samnor/fabric-semantic-merge.git'
fabric-merge --version
```

For a repeatable installation, pin the URL to a reviewed full commit SHA:

```sh
python -m pip install 'git+https://github.com/Samnor/fabric-semantic-merge.git@COMMIT_SHA'
```

From a local checkout, use `python -m pip install .`. See [pip's Git support](https://pip.pypa.io/en/stable/topics/vcs-support/) for supported URL forms.

Use a virtual environment or `pipx` for an isolated CLI. [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) is a separate prerequisite for live commands; it is not a Python runtime dependency of this package. Authenticate to the tenant containing your models:

```sh
az login --tenant YOUR_TENANT_UUID --allow-no-subscriptions
fabric-merge --tenant YOUR_TENANT_UUID doctor
fabric-merge --tenant YOUR_TENANT_UUID catalog
```

`--help`, `--version`, local `merge`, evidence replay and snapshot-based schedule suggestions need no Azure sign-in. `doctor` reads the selected account and both API workspace lists. `catalog --workspace UUID` limits model listing to one workspace. Global `--tenant UUID` and `--subscription ID_OR_NAME` options precede the command. Tokens come from `az account get-access-token --resource ...`, remain in memory and are never written to a plan or journal.

This is an **experimental** release for compatible Import models. Test on disposable models first. [Qualification and limits](https://github.com/Samnor/fabric-semantic-merge/blob/main/docs/verification.md) distinguish offline tests from the fixed-data live calibration; passing sampled checks is not production certification.

## Select and plan

In an interactive terminal, omit `--left` and `--right` to choose two models by number. They can be in different workspaces. Otherwise provide exact identities:

```sh
fabric-merge plan \
  --left SOURCE_WORKSPACE_UUID/FIRST_MODEL_UUID \
  --right OTHER_WORKSPACE_UUID/SECOND_MODEL_UUID \
  --target-workspace TARGET_WORKSPACE_UUID \
  --name 'Sales combined' \
  --out runs/sales-review
```

Planning exports both public definitions as **TMSL**, connections, source access lists, related reports and validation queries. It creates no model, refresh or report rebind. Read `runs/sales-review/PLAN.md`, `merge-preview.json`, `sources.json` and `reports.json`. A blocked plan exits with code 2. An API/validation failure exits 1. A successful command exits 0.

New plans also read both native refresh schedules and include `SCHEDULE.md`, `refresh-schedules.json` and a hashed `refresh-schedule-suggestion.json`. Pass `--refresh-capacity shared` or `--refresh-capacity premium` to declare the target scheduling tier. Schedule-read failures remain visible in the informational proposal and do not block the separate structural merge.

The default report scope is **tenant-wide**, using the admin report inventory, and needs Fabric admin access. It does not silently fall back when that access is unavailable. An explicit `--report-scope accessible` searches every accessible workspace plus My workspace; `--report-scope workspaces --report-workspace UUID ...` searches a declared list. Their saved coverage explicitly says they are not tenant-complete. A report discovered in another workspace is included in the same migration. Report write and target-model Build permissions are still required for rebind; tenant inventory access alone does not grant these.

“Related reports” means reports whose direct `datasetId` is either selected model. Composite models that depend on those models are a different dependency layer; their downstream reports must not be blindly rebound to a different schema. Paginated reports are not supported by Microsoft's Rebind API and block a plan when discovered as related. Unknown report types also block. New/deleted/rebound reports found during preflight block silent changes to the reviewed set.

## Resolve conflicts and review access

Compatible named objects are merged case-insensitively. Identical tables/partitions are deduplicated; disjoint ordinary measures and different tables are retained. DAX, M, table/column/measure names, relationships, perspectives, roles, cultures and other definition properties are preserved structurally. DAX/M text is not rewritten or executed locally. A different formula, source expression, partition definition, role rule or other conflicting property is reported instead of being silently replaced. A shared table name with different row filters is a real conflict, not an instruction to append potentially overlapping rows.

Use `--resolutions PATH.json` for reviewed JSON-pointer resolutions. For example:

```json
{
  "/bim/model/tables/Sales/measures/Quantity/expression": {"take": "left"}
}
```

`{"take":"right"}` or `{"value": CUSTOM_JSON_VALUE}` is also supported. Names in paths use the spelling in the conflict preview; JSON-pointer escaping applies. Unknown resolution paths fail. Choosing a side is an explicit semantic decision and can still fail the source/target query checks. The tool never silently renames a measure and then claims unchanged reports will work.

For shared objects, a setting present in only one source also requires review: omission can represent a meaningful default. Conflicts include `left_present`/`right_present` so omission is distinct from JSON null. A `take` resolution can explicitly preserve the selected source's omission. Known additive object collections and lineage metadata remain supported without guessing other defaults.

An access policy is required before creating the new model. Copy `examples/security-policy.json`, review the actual source ACLs, target workspace access, combined data audience and sensitivity labels, and set the review flags only when appropriate. Supply explicit desired User/Group grants rather than blindly unioning audiences. Source labels are not part of `model.bim`, so the policy explicitly specifies `label_id` (UUID or null). A target differing from any labeled source requires literal `label_change_approved: true`, including choosing one original's label when the two originals disagree. Source label changes after planning block deployment.

```sh
fabric-merge plan \
  --left SOURCE_WORKSPACE_UUID/FIRST_MODEL_UUID \
  --right OTHER_WORKSPACE_UUID/SECOND_MODEL_UUID \
  --name 'Sales combined' --out runs/sales-approved \
  --security my-reviewed-security.json \
  --resolutions my-resolutions.json \
  --probes my-report-probes.json
```

Use a new output directory for a revised plan. `--connection-overrides` maps an exported `type:path` reference to a reviewed binding when sources use different connection IDs for the same reference. It reuses accessible existing connections; it never copies passwords or provisions credentials. All definition exports and run artifacts can contain private query/source information: keep them under ignored `runs/`, whose directories/files use restrictive permissions.

## Create, validate, then switch reports

```sh
fabric-merge preflight runs/sales-approved --out runs/sales-preflight

# New model only: connect, full-refresh, read back definition and compare queries.
fabric-merge stage runs/sales-approved --yes

# Inspect the staged model and the exact report list, then authorize cutover.
fabric-merge apply runs/sales-approved --yes

fabric-merge status runs/sales-approved
```

Without `--yes`, these commands request confirmation in a terminal; unattended use requires the flag. `apply` also performs staging if it has not been done. It validates the actual new definition, connection bindings and requested sensitivity label; waits for the exact refresh to complete; compares source/target DAX result multisets; applies the reviewed access grants and verifies their visibility; then rebinds each report and reads its binding back. An error stops further changes and leaves the journal available for recovery or rollback. Cutover is sequential, not a Fabric transaction: a failure may leave a reported, recoverable partial migration.

Every unfinished `apply`, including a partial resume, compares fresh source/target answers before access grants or report writes. A saved staging pass never substitutes for this check. Failed fresh checks stop further cutover and remain in the journal; already switched reports may need rollback. Completed calls only verify their existing bindings and do not repeat workloads.

Default probes check each table's row count and every measure in each source. Add context-specific DAX from important reports through `--probes`; entries have `name`, `source` (`left`/`right`), `query`, and optionally `target_query`. An entry using an existing automatic probe name replaces that probe explicitly, keeping the same source; use this for measures that require a particular filter context. Read the final `probes.json` in the plan. All queries must return equal results under the proposed merge. The query budget defaults to 500 source-plus-target requests; raise `--max-validation-queries` explicitly for larger models. HTTP-200 responses containing DAX errors or partial-result errors fail validation. Default aggregate probes do not prove equivalence of every visual, slicer, security context or refresh instant.

With N probes, a fresh stage reserves 2N DAX calls and a fresh apply reserves 4N, covering staging plus the fresh cutover comparison. An already staged apply reserves 2N new calls; partial staging reserves its remaining work plus 2N. The budget is checked before workload admission. Older plans are reassessed locally against current merge/security rules before staging, without rewriting their artifacts. See [algorithm safety](https://github.com/Samnor/fabric-semantic-merge/blob/main/docs/algorithm-safety.md) for compatibility and snapshot limits.

RLS/OLS models can be staged after their definitions and membership plan are reviewed. Role memberships and all viewer access cannot be verified from a public model definition. Before cutover, configure the new model's memberships, test effective users, and supply `apply --access-proof reviewed-access.json`, following `examples/access-proof.json`. The receipt must identify the exact created model/workspace and security-policy hash. This is an operator review attestation, not automatic or cryptographic proof. The tool does not weaken security to make a merge pass.

Staging now runs metadata preflight before DAX or cloud mutations. This deployment adapter admits Import models; Direct Lake/DirectQuery/composite models, incremental policies, repeated relationship endpoints and repeated partition sources require separate qualification/resolution. A readable metadata response does not certify security, licensing or write permission. See [deployment compatibility](https://github.com/Samnor/fabric-semantic-merge/blob/main/docs/compatibility.md).

New plans accept `--refresh-mode auto|standard|enhanced` independently from `--refresh-capacity`, which controls schedule suggestions. Auto selects enhanced refresh only when the target Power BI workspace reports dedicated capacity; otherwise it selects a standard request. Denied workspace reads block staging rather than being interpreted as shared capacity. Explicit enhanced mode requires observed dedicated capacity. Existing plans without this policy use auto; existing refresh intents always retain their saved bodies/handles. Standard refresh uses `NoNotification` for a user and omits notification options for a known service principal. All real API DAX paths share a minimum 0.55-second spacing within that API instance, including staging and parity. This does not coordinate other processes/users' clients.

Standard responses can omit a refresh handle. Stage saves history before submission and can track the unique new API refresh record under exclusive target refresh control. Missing before-images or ambiguous new records require reconciliation, never another POST. Auto and standard migrations have now passed a fixed-data live trial calibration with report rollback and verified owned cleanup; [the live results and limits](https://github.com/Samnor/fabric-semantic-merge/blob/main/docs/pilots.md) describe the two defects that calibration exposed and the remaining untested environments.

Creation, refresh and definition exports support documented long-running responses. `--operation-timeout SECONDS` bounds each polling period; resuming uses the saved operation/refresh handle and never repeats the original POST. Enhanced refresh specifies full transactional processing and a one-hour per-attempt timeout; standard refresh uses the service's supported defaults. Connection/source/API limitations can still prevent a model from being exported, created, processed or queried; those failures block report cutover.

## Test parity and optionally save evidence

After the merged model exists, run fresh comparisons with both originals:

```sh
fabric-merge parity runs/sales-approved
fabric-merge parity runs/sales-approved --probes examples/probes.json --out runs/parity-001
fabric-merge verify-parity runs/parity-001
```

Omit `--out` to print JSON without creating parity artifacts. You can also supply `--left WORKSPACE/MODEL --right WORKSPACE/MODEL --target WORKSPACE/MODEL` instead of a plan. Checks cover source field/type compatibility, table row counts and every ordinary measure, including disjoint measure sets; add important report contexts with `--probes`. An evidence folder contains the exact DAX, captured API results, row differences, before/after definitions, readable report and hashes, with offline replay. Exit codes are 0 for sampled parity, 2 for mismatches and 1 for incomplete observations/errors. The command leaves migration validation and report bindings unchanged. See [parity usage and limits](https://github.com/Samnor/fabric-semantic-merge/blob/main/docs/parity.md).

Add `--check-stability` to repeat both model observations (four DAX calls per probe). Per-probe `keyed_numeric` policies support explicit tolerances with unique keys, while exact comparison remains the default. Coverage warnings highlight unfiltered-only and empty/all-null passes. See the [improvement report](https://github.com/Samnor/fabric-semantic-merge/blob/main/docs/parity-improvements.md) and [numeric examples](https://github.com/Samnor/fabric-semantic-merge/blob/main/examples/parity-numeric-probes.json).

## Suggest a merged refresh schedule

```sh
fabric-merge suggest-schedule runs/sales-approved \
  --capacity premium --out runs/sales-schedule-review
fabric-merge verify-schedule runs/sales-schedule-review
```

This rereads the two originals without changing the plan or journal, and works before the merged target is created. Without a plan, use `--left WORKSPACE/MODEL --right WORKSPACE/MODEL`. Optional `--out` saves a fresh private evidence folder; `verify-schedule` replays it offline. An offline example needs no credentials:

```sh
fabric-merge suggest-schedule \
  --snapshot examples/refresh-schedules.json --capacity shared \
  --out runs/example-schedule-review
```

The proposal preserves original start times where the target daily limit allows it, reports every missing/added weekly slot, and offers a native alternative with no added runs. Unknown capacity shows both 8/48-slot cases and withholds a PATCH body. Different active time zones or unknown service-selected times require review. Run counts do not establish capacity savings or completion freshness. Read [the schedule rules and limitations](https://github.com/Samnor/fabric-semantic-merge/blob/main/docs/refresh-schedules.md). No command applies the proposed schedule or retires the original schedules.

## Resume and rollback

```sh
fabric-merge status runs/sales-approved
fabric-merge reconcile runs/sales-approved
fabric-merge apply runs/sales-approved --yes
fabric-merge rollback runs/sales-approved --yes
```

The local journal records a durable intent before each mutation and the actual HTTP outcome afterward. An interrupted/uncertain request is not submitted again. `reconcile` makes observations only: a lost create response can be matched by the run's exact description marker plus definition; a lost rebind response can be matched by the exact report's observed target. Absence alone does not authorize resubmission. Unknown connection, permission or refresh submissions without an exact handle remain unresolved; inspect their saved evidence rather than deleting a guard. Confirmed HTTP failures also are not blindly replayed. Known 202 responses resume polling their original operation. A process-level file lock prevents two migration controllers sharing a run.

Rollback restores only reports for which this run attempted a rebind, using each report's recorded original model. Uncertain forward/restore outcomes must be reconciled before rollback continues. Starting rollback durably closes forward work for that run; status reports `rolling_back` until closure. The merged model is retained for diagnosis. Original models, schedules and connection resources are never deleted.

Pre-write observations reject detected foreign binding changes. GET and POST are separate requests, so a concurrent edit between them can still be overwritten. Keep exclusive operational control of the selected reports during cutover, reconciliation and rollback; the local run lock cannot enforce that across other runs, machines or administrators. HTTP 408/5xx responses also remain uncertain until observed, even though their HTTP receipt is saved.

Retire old refresh schedules only after checking all other consumers and configuring the new model's desired refresh schedule. This version deliberately leaves scheduling decisions to the owner; creating/rebinding alone does not guarantee capacity savings. Dashboards, published app packaging, external clients, original PBIX/PBIR deployment sources and composite-model upstream connections may need separate deployment updates. Rebinding reports with live connections changes their connection behavior as documented by Microsoft.

## Try the complete local demonstration

Clone the repository and install its development dependencies first; the loopback test service and synthetic examples are source-checkout assets.

```sh
python -B -m tests.demo --out runs/demo-1
fabric-merge merge \
  --left examples/left-definition-v2.json --right examples/right-definition-v2.json \
  --name 'Offline combined' --out runs/offline-merge.json
```

The demo creates one simulated merged model with disjoint measures, refreshes and validates it, rebinds three reports across two workspaces, then restores the original bindings. It exercises the real HTTP client and workflow against a stateful loopback service, **not real Fabric or a DAX execution engine**. Use a fresh demo output directory on repeat runs.

The v2 fixtures explicitly mark calculated-table outputs as `calculatedTableColumn`. A live calibration exposed that omission in the original fixtures; the originals are preserved as historical inputs. Structural validation now blocks that shape before creation. See [disposable pilot notes](https://github.com/Samnor/fabric-semantic-merge/blob/main/docs/pilots.md) for the live evidence and its limits.

## Development and verification

```sh
python -m pip install -e '.[dev]'
python -B -m unittest discover -s tests -t . -v
ruff check src tests scripts formal/check.py
ruff format --check src tests scripts formal/check.py
python -m build
python -m twine check --strict dist/*
python scripts/check_release.py dist
```

Python uses four spaces and snake_case, formatted with Ruff. Runtime dependencies: standard library and the installed Azure CLI. Tests use synthetic definitions and loopback HTTP; no cloud credentials, workspaces or production reports are changed. The suite covers disjoint measures, conflicts, additive tables, interactive selection, async creation, binding/refresh/query verification, cross-workspace inventories, RLS cutover, partial migration rollback, foreign changes, lost responses, no duplicate mutation, source drift and private artifact integrity.

See [API references](https://github.com/Samnor/fabric-semantic-merge/blob/main/docs/api-references.md) and [verification notes](https://github.com/Samnor/fabric-semantic-merge/blob/main/docs/verification.md). MIT licensed. Contributions are welcome; see [contributing](https://github.com/Samnor/fabric-semantic-merge/blob/main/CONTRIBUTING.md), [security reporting](https://github.com/Samnor/fabric-semantic-merge/blob/main/SECURITY.md), the [changelog](https://github.com/Samnor/fabric-semantic-merge/blob/main/CHANGELOG.md) and [release instructions](https://github.com/Samnor/fabric-semantic-merge/blob/main/docs/releasing.md). This project is independent of Microsoft and is not endorsed by Microsoft.

The [formal verification report](https://github.com/Samnor/fabric-semantic-merge/blob/main/formal/README.md) documents the TLA+ recovery model, finite bounds, counterexamples, resulting fixes and remaining assumptions. With Java 17+, run `python -B formal/check.py --fetch-tool --out runs/formal-NEW_NAME`; later use `--verify runs/formal-NEW_NAME` to check saved evidence against current sources. The formal model covers report cutover/recovery and does not prove the complete Python program or arbitrary model equivalence.
