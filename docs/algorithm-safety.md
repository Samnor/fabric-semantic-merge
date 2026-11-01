# Merge and cutover safeguards

The merger combines compatible named TMSL objects; it does not execute DAX/M locally or prove semantic equivalence. The safeguards below address three synthetic counterexamples identified in review.

## Omitted settings on shared objects

When both originals contain the same object, a property missing from one side is not assumed equivalent to the other's explicit value. Unknown/default-sensitive one-sided properties become conflicts with separate `left_present` and `right_present` flags. Explicit JSON null remains distinct from omission.

Known model/table object collections can still add disjoint tables, measures and other named objects. Available lineage metadata is retained. Other one-sided settings require a reviewed resolution. For example:

```json
{
  "/bim/model/relationships/Sales_Dim/isActive": {"take": "left"}
}
```

If the left source omitted that setting, the result preserves its omission. `take:right` retains the right value; `value:null` explicitly emits null. A reviewed choice does not establish relationship/filter equivalence. This conservative rule deliberately avoids a guessed universal table of defaults; even apparently equivalent omissions may need review.

## Fresh cutover validation

Staging retains its baseline/refresh/validation evidence for resumability. Every unfinished `apply` separately runs all probes against current source and target data, before permission grants or report rebinds. It never reuses a prior `cutover_validated` marker to skip this check, including after partial migration and reconciliation.

The journal records each attempt, completed sample hashes and any mismatching probe. A mismatch stops further cutover; it does not automatically reverse reports already switched in an earlier attempt. Inspect status and use reviewed rollback when needed. An already completed migration performs binding readbacks without new refresh, DAX or report mutations.

For N probes, initial staging requires up to 2N DAX calls and the fresh cutover comparison adds 2N. Initial apply therefore reserves 4N under the same `--max-validation-queries` ceiling; an already staged apply reserves 2N. A partially staged attempt reserves uncaptured baseline/target work plus 2N. Budget failure blocks new workload. Query pacing and the no-mutation-replay journal rules remain active.

Fresh observations are sequential. They cannot provide an atomic snapshot, detect every intervening change or guarantee results throughout a long report migration. Keep data stable and supply important filter/grouping/effective-user contexts. Neither fresh equality nor the finite recovery model proves universal parity.

## Labels and older plans

If the target label differs from any labeled original, `label_change_approved` must be literal JSON true. With originals labeled A and B, selecting A still needs approval because it differs from B. Retaining a common source label and leaving two unlabeled originals unlabeled need no label-change approval. Source label IDs are normalized as UUIDs; no label hierarchy is inferred.

Stage reassesses pinned original definitions and explicit resolution decisions under the current merger, confirms the recomputed model matches the pinned target, and reruns security policy checks before cloud workload. A formerly ready plan can therefore now stop and require a new reviewed plan. The tool does not silently rewrite that plan or its journal. Rollback remains available under its existing recovery rules; unknown mutations still require exact reconciliation.

## Reproduce

```sh
python -B -m unittest tests.test_algorithm_safety -v
```

The fifteen focused tests cover both missing-property directions, omission/null distinction, resolution choices, retained additive collections, mixed labels, old ready plans, stale stage results, partial resume, combined query reserves and no duplicate rebind. Tests use synthetic data and a loopback HTTP service, not live Fabric. The existing TLA+ model covers the report recovery protocol, not these default/classification/comparison algorithms.
