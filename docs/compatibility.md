# Deployment compatibility and preflight

The supported first deployment path is a compatible **Import** model union in a non-personal workspace, using an accessible target and reviewed existing connections. Public APIs support the workflow, but local tests do not establish compatibility across tenants, gateways, licenses or effective users. Source definitions/ACLs/connections/labels still need export permissions, and the target/report operations still need their actual service permissions.

## Commands

```sh
# New plans declare deployment policy separately from schedule recommendations.
.venv/bin/fabric-merge plan --left WORKSPACE/MODEL --right WORKSPACE/MODEL \
  --name 'Reviewed Import merge' --out runs/FRESH_PLAN \
  --security REVIEWED_SECURITY.json --refresh-mode auto \
  --report-scope workspaces --report-workspace WORKSPACE_UUID

.venv/bin/fabric-merge preflight runs/FRESH_PLAN --out runs/FRESH_PREFLIGHT
.venv/bin/fabric-merge stage runs/FRESH_PLAN --yes

# Fast checks; complete suite is documented in AGENTS.md.
.venv/bin/python -B -m unittest tests.test_preflight tests.test_pacing -v
```

`preflight` makes metadata GETs only. It reads the target through Fabric and Power BI, reads both original Power BI model identities and up to 100 reviewed connection bindings, and checks local plan/model structure and query budgets. It does not execute DAX or write a migration journal, model, refresh, permission, report or schedule. Optional fresh output contains private `preflight.json` (0600, folder 0700). Exit 0 means eligible for staging under the listed limits, 2 means blocked, and 1 means malformed inputs/setup failure. The saved result includes its plan hash, observations, blockers, warnings and selected refresh body; it is a point-in-time observation, not an authorization or certificate.

Stage repeats these checks under its existing run lock after re-exporting/checking the original definitions, connections, access and labels against the reviewed plan. It records the result durably before any new DAX/create/refresh. Failed/mismatched required metadata, a read-only/personal target, unsettled/disagreeing capacity assignments, an unready plan or unsupported model structure block admission. A rejected/uncertain mutation remains unreplayed regardless of preflight results.

Before that live admission, stage also reassesses the pinned merge and security policy under current rules. Old ready plans with omitted-setting conflicts or unapproved mixed labels require a fresh reviewed plan. Metadata-only `preflight` describes staging eligibility; `apply` separately reserves remaining stage queries plus a fresh 2N cutover comparison, even when an earlier stage passed. See [algorithm safeguards](algorithm-safety.md).

## Refresh handling

| Policy | Selection |
| --- | --- |
| `auto` (also old plans) | Enhanced when the actual target Power BI workspace reports `isOnDedicatedCapacity=true`; standard otherwise |
| `standard` | Standard request on either tier; user body contains only `notifyOption=NoNotification`, service-principal body is empty |
| `enhanced` | Requires an observed dedicated-capacity flag; full transactional request with one-hour timeout and client-specified retry count zero |

An absent capacity flag after a successful, identity-checked workspace response is **unknown**, so auto uses the conservative standard request and warns. A failed GET is a blocker. We do not infer a license/capacity tier from the schedule suggestion or workspace name. Per-user Premium can use the standard path when dedicated capacity cannot be established. These metadata checks do not prove active capacity, sufficient memory, caller licensing or credentials. A service may perform internal processing retries even when the submitted enhanced `retryCount` is zero; at-most-once here means client submission.

Every submitted refresh body is pinned by its existing durable intent. On resume, the same body/accepted response/exact refresh ID is used even if capacity metadata changed; no replacement request is submitted. Polling also rejects an explicit mismatched response `requestId`. Changing a plan after its journal is pinned remains forbidden.

Live calibration showed that a standard `202` response can omit the handle used by enhanced refresh. Stage now saves a bounded history before-image before a new standard POST. With no response handle, it follows the unique new `ViaApi` history identity and its terminal status. This requires exclusive control of target refresh submissions: concurrent external requests can defeat attribution. Multiple new API records, missing identities, a changed tracked identity or a missing before-image stop progress without another POST. A legacy accepted refresh with no saved before-image requires scoped observation/reconciliation; taking a later snapshot and calling it a before-image is forbidden.

## Query pacing and scope

The real `Fabric` API adapter paces all `rows`/`query_response` DAX paths with one shared per-instance monotonic-clock limiter, at least 0.55 seconds between admissions. This includes baseline and target queries in staging, and independent parity. The limiter rechecks early wakeups and does not automatically retry POSTs. The parity command's longer requested interval can still add spacing. Other processes sharing the same user can consume the same service quota; this limiter cannot reserve account-wide capacity.

The preflight detects known service-principal RLS/SSO exclusions and refuses an unverified SSO state for its reviewed connections under a service principal. A delegated user still needs actual effective-user role/membership/result testing. Metadata access does not prove the tenant Execute Queries setting, model Create rights, connection Use rights, report Write/target Build rights, or every source's query eligibility. Some failures can therefore still occur during staging; their receipts remain diagnostic evidence.

Repeated relationship endpoints or identical partition sources are conservatively blocked for review. These checks are not a general DAX/M/filter-graph proof. Calculation groups trigger contextual-parity warnings. This adapter explicitly blocks incremental/hybrid policies and non-Import partitions until a corresponding path is qualified. Staging still compares observations from different times: stabilize source data or use a reviewed common snapshot, and add important report contexts. No metadata preflight can guarantee completion freshness, rendering parity or CU savings.

## Evidence and live admission

The hardening suite includes shared/standard refresh submission, interrupted refresh resumption after capacity changes, denied metadata, structural/security exclusions and unchanged plan/journal observations. A final documentation check refined the source `isRefreshable` flag into a warning rather than a definitive rejection: it also reflects refresh/schedule history, while Import eligibility comes from the exported partitions. Original RLS metadata is checked for service-principal validation even if a reviewed target resolution removed its roles. Tests are synthetic/loopback, not real tenant results. Fresh source-bound runs of the existing finite recovery checks are retained separately; they do not prove the new adapter or pacing implementation.

Subsequent live calibration exposed a missing calculated-table output type in the original synthetic fixtures. The corrected v2 fixtures use `calculatedTableColumn`; structural validation now rejects that invalid shape before creation. Original fixtures and failed-create receipts remain preserved. The live pilot also exposed the standard-response tracking difference described above; it was corrected and covered by history-delta and interrupted-resume tests. The public suite includes these regressions; [pilot results](pilots.md) distinguish live evidence from those unit tests.

Earlier compatibility checks deliberately blocked when CLI and browser identities differed. After the correct account was selected, disposable fixed-data Import calibration passed both refresh paths. See [pilot results and remaining qualification gaps](pilots.md). Metadata access alone is never write authorization or a semantic/security certificate. Raw account/tenant receipts remain private.

Primary references checked 1 November 2026: [refresh request constraints](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/refresh-dataset-in-group), [exact refresh observation](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-execution-details-in-group), [workspace capacity flag](https://learn.microsoft.com/en-us/rest/api/power-bi/groups/get-group), [model metadata field semantics](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-dataset-in-group), [connection metadata](https://learn.microsoft.com/en-us/rest/api/fabric/core/connections/get-connection), and [query permissions, rates and RLS/SSO limits](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries-in-group).
