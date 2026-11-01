"""Concrete Python/HTTP reproductions of the formal recovery counterexamples."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fabric_merge.transport import ApiError, UncertainRequest
from fabric_merge.util import MergeError, load, save
from fabric_merge.workflow import Migration, create_plan

from .support import LEFT, NEW, REPORTS, RIGHT, SECURITY, WS1, TestClient, service


class FormalRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "plan"

    def plan(self, api):
        return create_plan(
            api,
            self.directory,
            (WS1, LEFT),
            (WS1, RIGHT),
            WS1,
            "Merged",
            security=copy.deepcopy(SECURITY),
        )

    def test_loopback_client_blocks_production_urls_before_requesting_a_token(self):
        from unittest.mock import Mock

        credential = Mock()
        client = TestClient(credential)
        with self.assertRaisesRegex(MergeError, "loopback HTTP only"):
            client.request("POST", "https://api.fabric.microsoft.com/v1/workspaces", {})
        credential.token.assert_not_called()

    def test_unknown_forward_receipt_cannot_be_silently_completed(self):
        with service() as (api, state):
            self.plan(api)
            state.drop_response = REPORTS[0]
            migration = Migration(api, self.directory)
            with self.assertRaises(UncertainRequest):
                migration.apply()
            with self.assertRaisesRegex(MergeError, "Unresolved rebind"):
                migration.apply()
            self.assertIsNone(migration.journal.latest("completed"))
            migration.reconcile()
            migration.apply()
            self.assertEqual(migration.status()["unresolved_operations"], [])
            posts = [
                c
                for c in state.calls
                if c[0] == "POST" and c[1].endswith(REPORTS[0] + "/Rebind")
            ]
            self.assertEqual(len(posts), 1)

    def test_unknown_forward_outcome_blocks_rollback_before_any_restore(self):
        with service() as (api, state):
            self.plan(api)
            state.drop_response = REPORTS[0]
            migration = Migration(api, self.directory)
            with self.assertRaises(UncertainRequest):
                migration.apply()
            before = len(state.calls)
            with self.assertRaisesRegex(MergeError, "Unresolved report"):
                migration.rollback()
            self.assertEqual(len(state.calls), before)
            migration.reconcile()
            migration.rollback()
            self.assertEqual(migration.status()["status"], "rolled_back")

    def test_lost_rollback_receipt_needs_reconciliation_before_sealing(self):
        with service() as (api, state):
            self.plan(api)
            migration = Migration(api, self.directory)
            migration.apply()
            state.drop_response = REPORTS[2]
            with self.assertRaises(UncertainRequest):
                migration.rollback()
            with self.assertRaisesRegex(MergeError, "Unresolved report"):
                migration.rollback()
            self.assertIsNone(migration.journal.latest("rolled_back"))
            self.assertEqual(migration.status()["status"], "rolling_back")
            migration.reconcile()
            migration.rollback()
            self.assertEqual(migration.status()["unresolved_operations"], [])
            self.assertEqual(
                len(
                    [
                        c
                        for c in state.calls
                        if c[0] == "POST" and c[1].endswith(REPORTS[2] + "/Rebind")
                    ]
                ),
                2,
            )  # One forward and one restore, despite both resumes.

    def test_partial_rollback_fences_forward_commands(self):
        with service() as (api, state):
            self.plan(api)
            migration = Migration(api, self.directory)
            migration.apply()
            state.fail_report = REPORTS[1]
            with self.assertRaises(ApiError):
                migration.rollback()
            self.assertEqual(migration.status()["status"], "rolling_back")
            before = len(state.calls)
            for command in (migration.apply, migration.stage):
                with self.assertRaisesRegex(MergeError, "Rollback has started"):
                    command()
            self.assertEqual(len(state.calls), before)

    def test_pre_fence_partial_rollback_journal_also_blocks_forward_commands(self):
        with service() as (api, state):
            self.plan(api)
            migration = Migration(api, self.directory)
            migration.apply()
            report = load(self.directory / "reports.json")[-1]
            migration.journal.mutate(
                api.client,
                "rollback-" + report["id"],
                "POST",
                api.report_url(report) + "/Rebind",
                {"datasetId": report["dataset_id"]},
            )
            self.assertIsNone(migration.journal.latest("rollback_started"))
            self.assertEqual(migration.status()["status"], "rolling_back")
            before = len(state.calls)
            for command in (migration.stage, migration.apply):
                with self.assertRaisesRegex(MergeError, "Rollback has started"):
                    command()
            self.assertEqual(len(state.calls), before)

    def test_rolled_back_run_cannot_return_cached_completion(self):
        with service() as (api, state):
            self.plan(api)
            migration = Migration(api, self.directory)
            migration.apply()
            migration.rollback()
            for report in state.reports.values():
                report["datasetId"] = NEW
            with self.assertRaisesRegex(MergeError, "Rollback has started"):
                migration.apply()

    def test_known_failed_forward_does_not_own_foreign_target_binding(self):
        with service() as (api, state):
            self.plan(api)
            state.fail_report = REPORTS[0]
            migration = Migration(api, self.directory)
            with self.assertRaises(ApiError):
                migration.apply()
            state.reports[REPORTS[0]]["datasetId"] = NEW
            with self.assertRaisesRegex(MergeError, "Failed rebind"):
                migration.apply()
            with self.assertRaisesRegex(MergeError, "Failed rebind"):
                migration.rollback()

    def test_plan_digest_checked_before_rollback_or_reconciliation(self):
        with service() as (api, state):
            self.plan(api)
            Migration(api, self.directory).stage()
            plan = load(self.directory / "plan.json")
            plan["display_name"] = "Changed after staging"
            save(self.directory / "plan.json", plan)
            migration = Migration(api, self.directory)
            before = len(state.calls)
            for command in (migration.rollback, migration.reconcile):
                with self.assertRaisesRegex(MergeError, "Plan changed"):
                    command()
            self.assertEqual(len(state.calls), before)

    def test_final_rollback_readback_catches_earlier_report_drift(self):
        with service() as (api, state):
            self.plan(api)
            migration = Migration(api, self.directory)
            migration.apply()
            original = api.report

            def read(report):
                result = original(report)
                # Last restore's readback, after the first restore was verified.
                if report["id"] == REPORTS[0] and result["datasetId"] != NEW:
                    state.reports[REPORTS[2]]["datasetId"] = "foreign"
                return result

            with patch.object(api, "report", side_effect=read):
                with self.assertRaisesRegex(MergeError, "Final rollback binding"):
                    migration.rollback()
            self.assertIsNone(migration.journal.latest("rolled_back"))

    def test_external_writer_between_get_and_post_is_an_api_limitation(self):
        with service() as (api, state):
            self.plan(api)
            migration = Migration(api, self.directory)
            original = migration._mutation
            races = []

            def mutate(key, url, body):
                if key == "rebind-" + REPORTS[0]:
                    state.reports[REPORTS[0]]["datasetId"] = "foreign"
                    races.append(key)
                return original(key, url, body)

            with patch.object(migration, "_mutation", side_effect=mutate):
                migration.apply()
            self.assertEqual(len(races), 1)
            self.assertEqual(state.reports[REPORTS[0]]["datasetId"], NEW)

    def test_server_error_after_committed_write_is_unknown_until_observed(self):
        with service() as (api, state):
            self.plan(api)
            migration = Migration(api, self.directory)
            original = api.client.request

            def request(method, url, body=None):
                result = original(method, url, body)
                if method == "POST" and url.endswith(REPORTS[0] + "/Rebind"):
                    raise ApiError(method, url, 503, {"error": "lost upstream result"})
                return result

            with patch.object(api.client, "request", side_effect=request):
                with self.assertRaises(ApiError):
                    migration.apply()
            self.assertEqual(state.reports[REPORTS[0]]["datasetId"], NEW)
            key = "rebind-" + REPORTS[0]
            self.assertEqual(migration.status()["unresolved_operations"], [key])
            with self.assertRaisesRegex(MergeError, "Unresolved report"):
                migration.rollback()
            migration.reconcile()
            self.assertEqual(migration.journal.outcome(key), "succeeded")
            migration.apply()
            self.assertEqual(migration.status()["unresolved_operations"], [])

    def test_server_error_without_effect_cannot_be_declared_rolled_back(self):
        with service() as (api, _):
            self.plan(api)
            migration = Migration(api, self.directory)
            original = api.client.request

            def request(method, url, body=None):
                if method == "POST" and url.endswith(REPORTS[0] + "/Rebind"):
                    raise ApiError(method, url, 503, {})
                return original(method, url, body)

            with patch.object(api.client, "request", side_effect=request):
                with self.assertRaises(ApiError):
                    migration.apply()
            with self.assertRaisesRegex(MergeError, "Unresolved report"):
                migration.rollback()
            self.assertIn("unknown", migration.reconcile()["outcomes"][0]["status"])
            self.assertIsNone(migration.journal.latest("rolled_back"))
