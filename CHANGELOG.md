# Changelog

## 0.1.0 — unreleased

- Install directly from a GitHub URL with pip; pin a reviewed commit for repeatability.
- Recheck fresh source/target answers before unfinished cutover attempts, with a combined query budget and failure receipts.
- Treat omitted settings on shared objects as explicit conflicts instead of guessing defaults; resolutions can preserve omission.
- Reject case-insensitive measure/column name collisions within a merged table before model creation.
- Require label-change approval when the target differs from either labeled original, and reassess legacy plans before staging.
- Plan compatible Import-model unions, including disjoint measures and reviewed conflicts.
- Authenticate using Azure CLI; check deployment eligibility before staging.
- Create, refresh and validate a merged target before migrating report bindings.
- Preserve originals and journal mutations for reconciliation and rollback.
- Compare parity with optional numeric policies, repeated observations and replayable evidence.
- Suggest merged native refresh schedules without applying them.
- Track standard refresh responses without handles using saved history under exclusive target control.
- Include a loopback demonstration, adversarial tests and a finite TLA+ recovery model.

The first public release is experimental. See docs/verification.md for qualification limits.
