# Formal verification of migration recovery

The report migration/recovery protocol has been specified in [TLA+](Migration.tla) and checked with the official TLC model checker. The revised finite model passes all seven invariants for **one, two and three reports**, exploring **83,941 distinct states** across those three configurations. This work also found and corrected recovery defects in the Python workflow.

This is finite safety model checking of a manually written abstraction. It is **not a machine-checked refinement proof of the Python application**, a proof for arbitrarily many reports, or a proof of DAX/M semantic equivalence. The source bindings and concrete regression tests make the scope reviewable; they do not eliminate that distinction.

## Reproduce

From the repository root, with Python 3.11+ and Java 17+:

```sh
.venv/bin/python -B formal/check.py --fetch-tool --out runs/formal-NEW_NAME
.venv/bin/python -B formal/check.py --verify runs/formal-NEW_NAME
.venv/bin/python -B -m unittest tests.test_formal_regressions tests.test_formal_runner -v
```

Choose a fresh output directory. `--fetch-tool` downloads the official `tla2tools.jar` only if absent, verifies its pinned SHA-256 before execution, and caches it under ignored `formal/.tools/`. With the cached tool, checking needs no network. `--verify` checks saved configs, log hashes, expected outcomes and current implementation/test/specification/runner hashes without Java or network.

The runner executes eleven configurations, saving every config, raw TLC log, command, state count, tool/runtime identity and source hash in the selected directory. A positive result requires normal completion and an empty unexplored-state queue. A negative control requires TLC's specific invariant-violation exit code and the exact expected invariant; a parser/tool failure does not count. Successful-migration and actual-restoration reachability controls prevent an implementation that simply refuses all work from passing unnoticed.

TLC is the official [TLA+ model checker](https://github.com/tlaplus/tlaplus/blob/master/general/docs/current-tools.md). This package pins the [v1.7.4 release](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4), with JAR SHA-256 `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`. The downloaded bytes also matched that release page's published SHA-1. The JAR identifies TLC 2.19, revision `5a47802`. Checking uses one worker, breadth-first exploration, fixed seed/fingerprint polynomial, no state constraints and no symmetry reduction. TLC uses finite fingerprints, so these are standard model-checking results subject to its reported collision estimates, not collision-free theorem proofs.

## Model and assumptions

Each report has its own `old` binding, one shared `new` binding, an optional `foreign` binding, forward/restore operation records and a volatile observed binding. `old` abstracts each report's recorded original identity; it does not mean every original report used the same model. Journal state, server application and receipt delivery are separate. The model admits crashes before transmission, missing/delayed receipts, definitive rejection, exact-target reconciliation, interrupted rollback and arbitrary interleavings between reports. A crash discards volatile observations while preserving journal records. A submitted operation can remain pending forever.

Positive checks depend on these assumptions:

1. A single controller holds the same run lock. Nobody copies/removes/edits its journal or runs another plan against the same reports. Direct library callers must acquire `journal.lock()` themselves.
2. The journal preserves completed intent records across process crashes. Partial/corrupt records stop execution. Power loss, torn filesystem writes, directory durability and hostile tampering are outside the model. A hash chain is not an authenticated audit log.
3. Each request has at most one server effect, and observed bindings are sufficiently current/linearizable for reconciliation. These are service assumptions, not facts established about Fabric by this study. Definitive rejection is separate from an ambiguous timeout/server error; HTTP 408/5xx now stay unknown until exact observation.
4. The reviewed report set, original identities and target identity are correct and fixed. No external writer changes bindings during migration, reconciliation or rollback. The local lock cannot exclude a different run, another computer or an administrator.
5. `validated` and `access` mean the application's validation/security gates passed for the fixed staged target. The model does not prove the contents or freshness of those checks, effective RLS/OLS permissions, or data equivalence.

The model begins after target creation has been established. Target creation/async polling, refresh processing, connection setup, token transport, ACL propagation, inventory discovery, filesystem implementation and the structural merger are covered by ordinary tests and code review, **not this formal model**. No liveness/fairness property is claimed: an unknown operation may correctly block progress indefinitely. Completion describes the seal-time state under the assumptions; later external changes cannot be prevented.

## Checked properties and correspondence

| Invariant | Meaning in the abstraction | Implementation correspondence |
| --- | --- | --- |
| `TypeOK` | Every reachable state stays in its declared domains. | Specification sanity check; not Python type verification. |
| `AtMostOnce` | At most one forward and one restore transmission per report operation key. | `Journal.mutate` writes an intent first, rejects a changed payload, caches known outcomes, and never resends an unknown key. |
| `CutoverGated` | A forward mutation requires validation and access approval. | `Migration.apply` stages, validates, checks exact-model access proof and reads back grants before its rebind loop. |
| `NoFalseCompletion` | Sealing success requires every report at the target and every forward operation confirmed. | `_confirmed_rebind`, final report readbacks, inventory check and unresolved-operation guard. |
| `NoFalseRollback` | Sealing rollback requires attempted reports at their originals and settled forward/restore outcomes. | Rollback's unresolved-operation guard and final complete-set readback. |
| `RollbackFence` | No new forward operation after rollback starts. | Durable `rollback_started` plus legacy rollback-intent detection; `stage`/`apply` refuse forward work. |
| `NoForeignOverwrite` | No own effect overwrites a foreign binding, under the exclusive-writer assumption. | Pre-write observations detect existing drift. The relaxed-writer counterexample below shows the limit. |

Operations can occur in more report orders than the Python loop permits. This is a conservative abstraction for positive safety checks. `Legacy` deliberately weakens protocol guards; it is not generated from the old source. In particular its direction counterexample demonstrates the missing phase guarantee rather than claiming its entire interleaving is an exact execution of the former sequential Python loop. Concrete HTTP tests separately reproduce the old workflow's incorrect status and missing phase guard.

Changing Python can invalidate this correspondence even if an unchanged TLA+ model still passes. Review this table and the regression tests when changing guards or operation order. `--verify` rejects evidence bound to different source bytes; running fresh checks alone does not prove a new correspondence.

## Results and fixes

| Configuration | Distinct states | Result |
| --- | ---: | --- |
| `fixed-1` | 95 | All seven invariants hold; queue exhausted. |
| `fixed-2` | 2,253 | All seven invariants hold; queue exhausted. |
| `fixed-3` | 81,593 | All seven invariants hold; queue exhausted. |
| `legacy-completion` | Counterexample | A report reaches the target, its receipt is lost, and success is sealed without resolving the intent. |
| `legacy-rollback` | Counterexample | A restore takes effect, its receipt is lost, and rollback is sealed with an unresolved restore. |
| `legacy-direction` | Counterexample | Without a durable direction fence, the abstract controller can resume forward work during rollback. |
| `foreign-race` | Counterexample | A foreign writer changes a binding after observation and before the admitted request takes effect. |
| `mutant-retry` | Counterexample | Allowing an unknown request to be replayed breaks at-most-once submission. |
| `mutant-gate` | Counterexample | Bypassing validation/access breaks the cutover gate. |
| `reachable-complete` | Witness | The fixed protocol can finish migration. |
| `reachable-restore` | Witness | The fixed protocol can finish after an actual restore, not merely an empty rollback. |

The implementation now requires explicit reconciliation before accepting an uncertain target binding or starting/resuming rollback; refuses ownership based on a failed rebind; fences forward work once rollback starts, including older partial journals; prevents returning cached completion after rollback; rechecks the whole restored set; and checks the pinned plan before rollback/reconciliation. HTTP 408/5xx receipts retain their evidence but no longer masquerade as definitive non-application.

The concrete suite also covers the remaining race and prevents test clients from contacting production hosts. Eight selected recovery regressions fail against preserved original workflow/journal source and pass against the corrected implementation. This baseline replay uses actual HTTP against a loopback simulator. It is not real Fabric behavior or a DAX engine.

Run the commands above to produce fresh, source-bound evidence. Historical baseline replays and raw delivery bundles remain private; the public tests and model are sufficient to reproduce the current finite checks. They are distinct from live Fabric qualification and do not establish that the Python implementation refines the model.

## Remaining deployment limit

The documented [Rebind API](https://learn.microsoft.com/en-us/rest/api/power-bi/reports/rebind-report-in-group) exposes a target `datasetId`; its documented request has no expected-current-binding condition. This client performs separate GET and POST calls. Therefore a foreign edit can occur between them and be overwritten despite the precheck. A final readback also cannot make the complete report set atomic. The model and a concrete loopback test both demonstrate this race. Use exclusive operational control of the selected reports throughout cutover/recovery; this tool cannot enforce that condition remotely. Concurrent cutovers are not certified by these results.

These results also cannot establish capacity savings, all-report rendering equivalence, arbitrary DAX/M equivalence, effective-user security or successful operation against a real tenant.

## Verification harness incident

The first historical-source replay loaded a separate workflow module without inheriting the loopback URL patches. It attempted nine create requests using synthetic workspace/model data and the dummy token `loopback-test-only`; all were rejected with HTTP 401. No valid Azure credential was requested and no live resource mutation succeeded. That private failed log is not used as bug evidence. The test client now rejects every non-loopback URL before obtaining a token. The corrected replay explicitly patches both API origins and completes with the expected eight failures and no harness errors. Subsequent verification is local.
