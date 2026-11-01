import copy
from pathlib import Path
import tempfile
import unittest

from fabric_merge.transport import ApiError, UncertainRequest
from fabric_merge.util import MergeError, load
from fabric_merge.workflow import Migration, create_plan
from .support import LEFT, NEW, REPORTS, RIGHT, SECURITY, WS1, WS2, service


class WorkflowTests(unittest.TestCase):
    def test_synchronous_creation_and_repeat_apply_do_not_duplicate_mutations(self):
        with service() as (api, state):
            state.async_create = False
            self.plan(api)
            migration = Migration(api, self.directory)
            first = migration.apply()
            mutations = len(
                [
                    r
                    for r in state.calls
                    if r[0] == "POST" and not r[1].endswith("getDefinition")
                ]
            )
            self.assertEqual(migration.apply(), first)
            self.assertEqual(
                len(
                    [
                        r
                        for r in state.calls
                        if r[0] == "POST" and not r[1].endswith("getDefinition")
                    ]
                ),
                mutations,
            )

    def test_sources_can_come_from_different_workspaces(self):
        with service() as (api, state):
            state.models[RIGHT]["workspaceId"] = WS2
            plan = create_plan(
                api,
                self.directory,
                (WS1, LEFT),
                (WS2, RIGHT),
                WS1,
                "Merged",
                security=SECURITY,
            )
            self.assertEqual(plan["sources"]["right"]["workspace_id"], WS2)
            self.assertEqual(
                Migration(api, self.directory).apply()["reports_rebound"], 3
            )
            self.assertTrue(
                any(
                    "/groups/" + WS2 + "/datasets/" + RIGHT + "/executeQueries" in path
                    for _, path, _ in state.calls
                )
            )

    def test_context_specific_probe_can_override_default_by_exact_name(self):
        with service() as (api, state):
            probe = {
                "name": "left:measure:Sales:Quantity",
                "source": "left",
                "query": 'EVALUATE ROW("Value", CALCULATE([Quantity], Sales[Value] > 10))',
            }
            self.plan(api, probes=[probe])
            actual = load(self.directory / "probes.json")
            self.assertEqual(len(actual), 4)
            self.assertEqual(
                next(p for p in actual if p["name"] == probe["name"]), probe
            )
            self.assertEqual(
                Migration(api, self.directory).apply()["reports_rebound"], 3
            )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "plan"

    def plan(self, api, **kwargs):
        return create_plan(
            api,
            self.directory,
            (WS1, LEFT),
            (WS1, RIGHT),
            WS1,
            "Merged",
            security=kwargs.pop("security", copy.deepcopy(SECURITY)),
            **kwargs,
        )

    def test_create_refresh_validate_rebind_all_cross_workspace_and_rollback(self):
        with service() as (api, state):
            plan = self.plan(api)
            self.assertTrue(plan["ready"])
            self.assertEqual(len(load(self.directory / "reports.json")), 3)
            self.assertNotIn(NEW, state.models)
            migration = Migration(api, self.directory)
            with migration.journal.lock():
                result = migration.apply()
            self.assertEqual(result["reports_rebound"], 3)
            self.assertTrue(all(r["datasetId"] == NEW for r in state.reports.values()))
            self.assertIn(LEFT, state.models)
            self.assertIn(RIGHT, state.models)
            self.assertEqual(
                len(
                    [
                        r
                        for r in state.calls
                        if r[0] == "POST" and r[1].endswith("/semanticModels")
                    ]
                ),
                1,
            )
            self.assertEqual(
                len(
                    [
                        r
                        for r in state.calls
                        if r[0] == "POST" and r[1].endswith("/refreshes")
                    ]
                ),
                1,
            )
            self.assertGreaterEqual(state.operation_polls, 2)
            migration.rollback()
            self.assertEqual(
                [state.reports[r]["datasetId"] for r in REPORTS], [LEFT, LEFT, RIGHT]
            )

    def test_staging_never_rebinds_reports(self):
        with service() as (api, state):
            self.plan(api)
            result = Migration(api, self.directory).stage()
            self.assertEqual(result["status"], "staged_and_validated")
            self.assertFalse(any(r[1].endswith("/Rebind") for r in state.calls))

    def test_validation_mismatch_prevents_any_report_cutover(self):
        with service() as (api, state):
            self.plan(api)
            state.mismatch = True
            with self.assertRaisesRegex(MergeError, "Validation mismatch"):
                Migration(api, self.directory).apply()
            self.assertFalse(any(r[1].endswith("/Rebind") for r in state.calls))

    def test_source_drift_prevents_model_creation(self):
        with service() as (api, state):
            self.plan(api)
            state.models[LEFT]["model"]["bim"]["model"]["culture"] = "sv-SE"
            with self.assertRaisesRegex(MergeError, "Source definition"):
                Migration(api, self.directory).apply()
            self.assertNotIn(NEW, state.models)

    def test_new_related_report_cannot_be_silently_omitted(self):
        with service() as (api, state):
            self.plan(api)
            report = copy.deepcopy(state.reports[REPORTS[0]])
            report["id"] = "50000000-0000-0000-0000-000000000009"
            state.reports[report["id"]] = report
            with self.assertRaisesRegex(MergeError, "inventory changed"):
                Migration(api, self.directory).apply()
            self.assertNotIn(NEW, state.models)

    def test_lost_create_receipt_reconciles_without_second_create(self):
        with service() as (api, state):
            self.plan(api)
            state.drop_response = "create"
            migration = Migration(api, self.directory)
            with self.assertRaises(UncertainRequest):
                migration.stage()
            self.assertEqual(migration.status()["unresolved_operations"], ["create"])
            self.assertEqual(
                migration.reconcile()["outcomes"][0]["status"], "confirmed_created"
            )
            migration.apply()
            self.assertEqual(
                len(
                    [
                        r
                        for r in state.calls
                        if r[0] == "POST" and r[1].endswith("/semanticModels")
                    ]
                ),
                1,
            )

    def test_lost_rebind_receipt_reconciles_without_duplicate_post(self):
        with service() as (api, state):
            self.plan(api)
            state.drop_response = REPORTS[0]
            migration = Migration(api, self.directory)
            with self.assertRaises(UncertainRequest):
                migration.apply()
            self.assertEqual(
                migration.reconcile()["outcomes"][0]["status"], "confirmed_applied"
            )
            migration.apply()
            first = [
                r
                for r in state.calls
                if r[0] == "POST" and r[1].endswith(REPORTS[0] + "/Rebind")
            ]
            self.assertEqual(len(first), 1)
            self.assertTrue(all(r["datasetId"] == NEW for r in state.reports.values()))

    def test_partial_failure_can_restore_every_successful_report(self):
        with service() as (api, state):
            self.plan(api)
            state.fail_report = REPORTS[1]
            migration = Migration(api, self.directory)
            with self.assertRaises(ApiError):
                migration.apply()
            state.fail_report = None
            migration.rollback()
            self.assertEqual(
                [state.reports[r]["datasetId"] for r in REPORTS], [LEFT, LEFT, RIGHT]
            )

    def test_foreign_binding_change_is_not_overwritten(self):
        with service() as (api, state):
            self.plan(api)
            migration = Migration(api, self.directory)
            migration.apply()
            state.reports[REPORTS[0]]["datasetId"] = (
                "20000000-0000-0000-0000-000000000099"
            )
            with self.assertRaisesRegex(MergeError, "another actor"):
                migration.rollback()

    def test_security_review_missing_blocks_mutation(self):
        with service() as (api, state):
            result = self.plan(api, security=None)
            self.assertFalse(result["ready"])
            with self.assertRaisesRegex(MergeError, "blocked"):
                Migration(api, self.directory).apply()
            self.assertNotIn(NEW, state.models)

    def test_paginated_report_blocks_complete_plan(self):
        with service() as (api, state):
            state.reports[REPORTS[0]]["reportType"] = "PaginatedReport"
            self.assertFalse(self.plan(api)["ready"])

    def test_artifact_tampering_rejected(self):
        with service() as (api, state):
            self.plan(api)
            (self.directory / "merged-definition.json").write_text("{}")
            with self.assertRaisesRegex(MergeError, "artifact changed"):
                Migration(api, self.directory)


if __name__ == "__main__":
    unittest.main()
