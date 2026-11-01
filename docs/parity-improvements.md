# Parity improvements

Four improvements strengthen the parity tool. Their tests use synthetic observations and offline replay; they do not measure capacity savings.

| Improvement | Earlier limitation | Implemented behavior | Validation |
| --- | --- | --- | --- |
| Evidence contract | A consistently rehashed bundle containing queries for only one original was accepted; malformed manifest shapes could raise AttributeError. | Verify both original identities/coverage, all expected default probes, file-set/accounting contracts and report consistency; reject malformed shapes with a validation error. | The preserved before/after replay now rejects the missing-original bundle. Original passed/failed/incomplete v1 bundles still verify. |
| Numeric comparison | Exact JSON comparison treats `1` and `1.0` as different and has no explicit tolerance policy. | Keep exact default; opt in to numeric tolerances with unique exact row keys, or explicitly one scalar row. Preserve raw differences and record every policy. | Boundary cases, large integers, ambiguous keys, null/bool/string strictness, changed-policy replay and tolerance diagnostics. |
| Data observations | Stable definitions cannot detect data-only changes during a comparison. | Optional `--check-stability` reserves and captures four DAX calls per probe; differences within either model make the result incomplete. | Source-only changes with identical definitions, target changes inside numeric tolerance, missing repeated receipts, correct budgets and offline replay. |
| Interpretation | A pass did not distinguish default-only queries or empty/all-null answers. | Per-original coverage and warnings, plus repeated-data outcomes in the readable report; JSON and README must agree. | Empty/blank output diagnostics, preserved defaults, custom query counts and rehashed contradictory README rejection. |

The public regressions exercise missing-original coverage, malformed manifests, numeric boundary/large-integer behavior, changed data with stable definitions, missing repeated observations and empty-output warnings. Run `python -B -m unittest tests.test_parity tests.test_parity_hillclimb -v`. Historical before/after bundles are retained privately, not distributed with tenant evidence.

The finite migration model covers recovery, not comparator or evidence correctness. A replay verifies internal consistency and hashes, not the authenticity of a cloud observation.

Use:

```sh
fabric-merge parity runs/PLAN --check-stability \
  --probes my-reviewed-probes.json --out runs/parity-NEW_NAME
fabric-merge verify-parity runs/parity-NEW_NAME
```

Without the flag or a numeric policy, query count and exact comparison behavior remain unchanged. The evidence folder remains optional. See [full usage and limits](parity.md) and [example numeric policies](../examples/parity-numeric-probes.json).

Passing still concerns selected observations. Unique keys and tolerances require review; empty answers may be valid but uninformative; repeated equal answers cannot prove an atomic snapshot or complete RLS/report equivalence. A rehashed bundle is not authenticated evidence of a cloud execution.
