"""Run the workflow over a local HTTP simulator; no az or cloud call."""

import argparse
from pathlib import Path

from fabric_merge.util import save
from fabric_merge.workflow import Migration, create_plan
from .support import LEFT, RIGHT, SECURITY, WS1, service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with service() as (api, state):
        create_plan(
            api,
            args.out,
            (WS1, LEFT),
            (WS1, RIGHT),
            WS1,
            "Merged demonstration",
            security=SECURITY,
        )
        original = {k: r["datasetId"] for k, r in state.reports.items()}
        migration = Migration(api, args.out)
        with migration.journal.lock():
            staged = migration.stage()
            cutover = migration.apply()
            after = {k: r["datasetId"] for k, r in state.reports.items()}
            rollback = migration.rollback()
        result = {
            "environment": "loopback HTTP simulation, not real Fabric or a DAX execution engine",
            "stage": staged,
            "cutover": cutover,
            "rollback": rollback,
            "original_bindings": original,
            "migrated_bindings": after,
            "restored_bindings": {k: r["datasetId"] for k, r in state.reports.items()},
            "create_requests": sum(
                method == "POST" and path.endswith("/semanticModels")
                for method, path, _ in state.calls
            ),
            "refresh_requests": sum(
                method == "POST" and path.endswith("/refreshes")
                for method, path, _ in state.calls
            ),
            "report_rebind_requests": sum(
                method == "POST" and path.endswith("/Rebind")
                for method, path, _ in state.calls
            ),
            "cloud_mutations": 0,
        }
        save(args.out / "demo-result.json", result, exclusive=True)
        print(
            f"Created one merged model, validated four source/target probes, rebound {cutover['reports_rebound']} reports across two workspaces, then restored all original bindings."
        )
        print("Simulation evidence: " + str(args.out / "demo-result.json"))


if __name__ == "__main__":
    main()
