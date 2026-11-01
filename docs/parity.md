# Compare a merged model with its two originals

`fabric-merge parity` runs fresh comparisons between each original and the merged model. It does not refresh a model, rebind a report, change a migration journal or mark a migration validated. The queries consume capacity, so this command runs only when invoked.

After staging a migration:

```sh
# Print a JSON report without saving parity artifacts.
fabric-merge parity runs/sales-approved

# Also save a fresh evidence folder.
fabric-merge parity runs/sales-approved --out runs/sales-parity-001

# Check/replay that folder later, with no Azure login or cloud requests.
fabric-merge verify-parity runs/sales-parity-001
```

The plan form uses the original identities and confirmed created target from its verified artifacts/journal, checks the pinned plan and tenant, and holds the run lock while observing. The target must already exist; this command does not create or refresh it. It uses the plan's custom probes while also covering the currently exported originals' default probes. Its results are separate from the migration's recorded staging validation.

For a model created elsewhere, supply all three exact references:

```sh
fabric-merge parity \
  --left FIRST_WORKSPACE_UUID/FIRST_MODEL_UUID \
  --right SECOND_WORKSPACE_UUID/SECOND_MODEL_UUID \
  --target MERGED_WORKSPACE_UUID/MERGED_MODEL_UUID \
  --out runs/independent-parity-001
```

The models may reside in different workspaces. Use either a plan directory or three explicit references. `--out` is optional; an existing evidence directory is never overwritten.

## What is tested

For both originals, the command checks that the target retains their table, column and ordinary measure names, with matching exported column data types. Names are compared case-insensitively. Extra target fields are allowed. Field presence does not prove identical relationships, formulas, calculation groups, formats, partitions or security behavior.

Default DAX probes compare each source table's row count and every ordinary source measure with the same query against the target. Disjoint measure sets are covered independently: each original's measures are checked against the merged model. Source and target results are compared as exact JSON row multisets, retaining duplicate multiplicities, nulls, column names and numeric JSON representations while ignoring row order. Thus integer `1` and decimal `1.0` are conservatively distinct. No implicit tolerance or column remapping is applied.

Before and after the probes, all three decoded TMSL definitions are captured and fingerprinted. A changed definition, missing final observation, query error, HTTP-200 partial error, malformed result or near-limit result makes the run incomplete. The report retains observed mismatches even if another error prevents an overall conclusion.

## Add meaningful report contexts

Use `--probes examples/probes.json` or a reviewed file of your own:

```json
[
  {
    "name": "left:filtered-quantity",
    "source": "left",
    "query": "EVALUATE ROW(\"Value\", CALCULATE('Sales'[Quantity], 'Sales'[Value] > 10))"
  },
  {
    "name": "right:filtered-count",
    "source": "right",
    "query": "EVALUATE ROW(\"Value\", CALCULATE('Sales'[Count], 'Sales'[Value] > 10))"
  }
]
```

Each additional probe has `name`, `source` (`left` or `right`) and `query`, optionally `target_query`, `description` and an explicit `comparison` policy. A matching name overrides an existing probe, preserving its original source. Duplicate names within a supplied file, empty queries and unsupported settings fail before any DAX is submitted. Both source models must have probe coverage.

Use `target_query` when the merged model needs an explicitly different expression, and give both queries the same output aliases. For rounding-sensitive measures, express the intended rounding in both DAX queries or use the explicit numeric policy below. Unknown settings are rejected rather than silently ignored. Add representative date ranges, slicer filters, grouping/grand totals and relationship paths from important reports; matching one unfiltered total provides limited assurance.

```sh
fabric-merge parity runs/sales-approved \
  --probes my-report-contexts.json \
  --max-queries 500 \
  --out runs/sales-parity-contexts-001
```

Every probe uses two DAX calls by default, or four with `--check-stability`. The default budget permits 500 calls; exceeding it stops before any DAX submission. Definition export/polling calls are additional observations governed by `--operation-timeout`. Queries run sequentially with a default minimum interval of 0.55 seconds. Microsoft documents one query/table per request, row/value/data limits and a shared per-user request limit; even HTTP 200 can contain a partial-result error. This client rejects those errors and conservatively rejects results at 100,000 rows, 1,000,000 values or 14,000,000 serialized bytes. Keep queries to bounded aggregates/groupings. [Microsoft Execute Queries documentation](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries-in-group).

`--query-interval SECONDS` changes pacing; it must be finite and nonnegative. Shared account activity can still cause throttling. Failed query POSTs are not automatically retried. A new invocation is a new observation, with a fresh optional evidence directory.

## Explicit numeric tolerances

Exact multiset comparison remains the default. Opt in per probe with this configuration:

```json
{
  "name": "left:grouped-quantity",
  "source": "left",
  "query": "EVALUATE SUMMARIZECOLUMNS('Sales'[Value], \"Observed\", 'Sales'[Quantity])",
  "comparison": {
    "mode": "keyed_numeric",
    "key_columns": ["Sales[Value]"],
    "absolute_tolerance": "0.000001",
    "relative_tolerance": "0.000000001"
  }
}
```

Keys use exact returned JSON column names, including brackets/qualification. They must uniquely identify every row on each side. Missing or duplicate keys make comparison incomplete; there is no greedy approximate row matching. An explicit empty `key_columns: []` permits exactly one row on each side for scalar measures. See [numeric probe examples](../examples/parity-numeric-probes.json); adapt their names/fields and units to the actual models. Use an automatic probe's name to override its default exact policy.

Only non-key numeric cells use tolerances. Acceptance requires `abs(source - target) <= max(absolute_tolerance, relative_tolerance * max(abs(source), abs(target)))`. Tolerances must be finite and nonnegative. Defaults within this explicit mode are zero; integer/decimal representations of the same numeric value can match. Keys, nulls, booleans, strings, missing columns and missing rows stay strict. The same numeric policy applies to all non-key numeric columns in that probe, so split measures into separate probes when they need different units/tolerances.

Decisions use exact rational arithmetic over the decimal representations of the parsed JSON numbers, avoiding subtraction/threshold rounding inside the comparator. This does not recover precision already lost by a service or JSON numeric parsing. Raw row hashes/differences remain available even when the numeric policy accepts a difference; `numeric_comparison` records the policy and tolerated-cell count, while bounded `cell_differences` explain violations. Large tolerances can hide material changes and should be reviewed for the measure's units.

## Observe data stability

```sh
fabric-merge parity runs/sales-approved --check-stability \
  --probes my-reviewed-contexts.json --out runs/stability-parity-001
```

This deliberately executes source, target, source again, target again for each probe. It reserves **four calls per probe** before any query and captures each observation independently. The 500-call default permits at most 125 probes in this mode. These are planned observations, not automatic retries of failed requests.

Within each model, the two results must be exactly equal even if cross-model numeric tolerance is enabled. Changed observed data or unavailable repeated observations makes the overall run incomplete; the initial source/target comparison remains visible for diagnosis. Stable answers are labeled `observed_stable`. They cannot exclude changes between reads, an ABA change, stale cached answers or different refresh watermarks. This option provides more evidence about data changes while preserving the snapshot limitation.

## Coverage that explains a pass

Version-2 reports show each original's planned/executed probes, retained default queries, custom/overridden queries and numeric policies. Query counts do not assess whether the selected business contexts are representative. Reports warn when a source has only default unfiltered queries, or passing probes returned no values/all-null values on both sides. Empty/all-null comparisons can still pass; the warning prompts testing a populated, meaningful context. Grouping keys that contain values can prevent the all-null-output warning, so review the actual results as well.

## Outcomes and evidence

| Status | Exit code | Meaning |
| --- | ---: | --- |
| `passed` | 0 | All selected probes and field checks passed, with unchanged definitions and any requested repeated data observations unchanged. |
| `failed` | 2 | Complete observations contain a result mismatch or missing/incompatible field. |
| `incomplete` | 1 | Errors, invalid/ambiguous comparison keys, missing observations, budget failure or observed definition/data drift prevent a complete conclusion. |

Local validation, authentication and filesystem failures also exit 1; command-line syntax errors use argparse's exit 2. If an evidence directory was requested, setup/query failures are retained when the directory remains writable. An interruption or disk failure may leave unsealed partial files; without a complete manifest they do not constitute verified evidence. The offline verifier's exit 0 means evidence integrity/replay succeeds, even for a recorded failed/incomplete parity run; its `parity_status` preserves that distinction.

```text
sales-parity-001/
  README.md                 readable outcome and per-probe table
  report.json               identities, counts, timing, scope and results
  manifest.json             SHA-256 and byte count for every other file
  probes.json               the exact expanded probe set
  definitions/              decoded before/after model definitions
  queries/                  source and target DAX, using numbered filenames
  results/                  parsed API bodies, errors, selected receipt IDs and timing
  diffs/                    row hashes/counts and up to ten differing-row samples
  stability/                optional comparisons of repeated source/target observations
```

Full returned rows are retained in the API bodies; diff samples are bounded without losing the total differing-row counts. Offline verification checks the file manifest, model/query bindings, source/target result comparisons, field checks, counts and reported definition fingerprints. It checks the captured evidence without contacting Fabric or requiring the original installation's source hash. The manifest detects changes relative to its recorded hashes; it is not a signed attestation against somebody replacing the complete bundle.

New bundles use schema version 2. The verifier still supports original version-1 exact bundles. It also enforces three distinct model identities, all default probes (with explicit overrides), coverage of both originals, the declared observation count, repeated-data comparisons and the numeric policy. For version 2, coverage and the readable README must agree with replayed JSON results. Readable formatting has its own `renderer_version`; earlier formatting is retained so ordinary wording changes can be introduced without breaking saved evidence. File hashing streams in bounded chunks. Invalid bundle shapes fail with a readable validation error.

Files are created with mode 0600 and directories with mode 0700. Keep bundles under ignored `runs/`: they can contain private model expressions, source references, user-supplied query text and business data. Authorization headers/access tokens are not exported.

## Scope of a passing result

Tests execute under the signed-in caller. No effective-user impersonation is performed, so the command does not certify every RLS/OLS role or viewer. Definitions and query data need appropriate export/Read/Build access and the relevant tenant API settings.

Keep source data, refreshes and target data stable during comparison. Definition hashes do not establish common refresh watermarks or atomic snapshots: a data-only refresh can occur with identical definitions. A passing result means parity for the chosen observations, not proof of all model semantics, rendered visuals or capacity savings. Review important report contexts and effective-user behavior before cutover.

Implementation checks: `.venv/bin/python -B -m unittest tests.test_parity -v` exercises exact/disjoint comparisons, evidence replay/tampering, failures, drift, budgets and read-only isolation through synthetic models and loopback HTTP. Live tenant parity was not executed during implementation.

The public offline tests cover passing, mismatched and incomplete bundles, both-original coverage, repeated observations, numeric policies and evidence tampering. Run `python -B -m unittest tests.test_parity tests.test_parity_hillclimb -v` to reproduce them without cloud access. Historical raw delivery bundles remain private. The finite migration/recovery model does not prove the parity comparator or universal semantic equivalence.
