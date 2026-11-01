"""Admission before mutation, refresh selection and migration compatibility."""

import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fabric_merge.cli import main
from fabric_merge.preflight import inspect_preflight, model_risks, refresh_request
from fabric_merge.transport import Response
from fabric_merge.util import MergeError, load, save
from fabric_merge.workflow import Migration, create_plan
from .support import LEFT, NEW, RIGHT, SECURITY, WS1, service


CONNECTION = "60000000-0000-0000-0000-000000000001"


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / "plan"

    def plan(self, api, **kwargs):
        return create_plan(
            api,
            self.directory,
            (WS1, LEFT),
            (WS1, RIGHT),
            WS1,
            "Merged",
            security=kwargs.pop("security", SECURITY),
            **kwargs,
        )

    def inspect(self, api, plan, **kwargs):
        return inspect_preflight(
            api, plan, load(self.directory / "merged-model.json"), **kwargs
        )

    def assert_no_workload(self, state):
        self.assertFalse(
            any(
                m == "POST" and not path.endswith("/getDefinition")
                for m, path, _ in state.calls
            )
        )

    def test_shared_uses_standard_request_and_exact_refresh_observation(self):
        with service() as (api, state):
            state.dedicated = False
            self.plan(api)
            original_schedules = copy.deepcopy(state.schedules)
            migration = Migration(api, self.directory)
            migration.stage()
            submitted = [
                body
                for method, path, body in state.calls
                if method == "POST" and path.endswith("/refreshes")
            ]
            self.assertEqual(submitted, [{"notifyOption": "NoNotification"}])
            self.assertTrue(
                any(
                    method == "GET" and "/refreshes/" in path
                    for method, path, _ in state.calls
                )
            )
            self.assertEqual(
                migration._body("preflight")["selected_refresh"]["mode"], "standard"
            )
            self.assertEqual(state.schedules, original_schedules)

    def test_headerless_standard_resume_uses_durable_before_image_without_repeating_refresh(
        self,
    ):
        with service() as (api, state):
            state.dedicated = False
            state.headerless_standard = True
            self.plan(api)
            migration = Migration(api, self.directory)
            with patch.object(
                api, "wait_refresh", side_effect=MergeError("Observation interrupted")
            ):
                with self.assertRaisesRegex(MergeError, "Observation interrupted"):
                    migration.stage()
            before = migration.journal.latest("refresh_history_before")
            intent = migration.journal.latest("intent", "refresh")
            self.assertLess(before["sequence"], intent["sequence"])
            self.assertEqual(before["body"]["entries"], [])
            migration.stage()
            self.assertIn("history delta", migration._body("refreshed")["tracking"])
            self.assertEqual(
                sum(
                    m == "POST" and p.endswith("/refreshes") for m, p, _ in state.calls
                ),
                1,
            )

    def test_dedicated_auto_uses_enhanced_and_explicit_standard_is_valid(self):
        self.assertEqual(refresh_request("auto", True, "user")["mode"], "enhanced")
        self.assertEqual(
            refresh_request("auto", True, "user")["body"]["commitMode"], "transactional"
        )
        self.assertEqual(
            refresh_request("standard", True, "user")["body"],
            {"notifyOption": "NoNotification"},
        )
        self.assertEqual(
            refresh_request("standard", True, "service_principal")["body"], {}
        )

    def test_unknown_capacity_falls_back_to_standard_but_unknown_identity_does_not_guess(
        self,
    ):
        self.assertEqual(refresh_request("auto", None, "user")["mode"], "standard")
        for capacity in (False, None):
            with self.assertRaisesRegex(MergeError, "Enhanced refresh requires"):
                refresh_request("enhanced", capacity, "user")
        with self.assertRaisesRegex(MergeError, "known user/service"):
            refresh_request("standard", False, "unknown")

    def test_forced_enhanced_on_shared_blocks_before_create_query_or_refresh(self):
        with service() as (api, state):
            state.dedicated = False
            plan = self.plan(api, refresh_mode="enhanced")
            report = self.inspect(api, plan)
            self.assertFalse(report["ready"])
            with self.assertRaisesRegex(MergeError, "preflight blocked"):
                Migration(api, self.directory).stage()
            self.assertNotIn(NEW, state.models)
            self.assert_no_workload(state)

    def test_denied_or_read_only_target_blocks_without_treating_absence_as_shared(self):
        with service() as (api, state):
            plan = self.plan(api)
            state.preflight_errors[f"/pbi/groups/{WS1}"] = 403
            report = self.inspect(api, plan)
            self.assertFalse(report["ready"])
            self.assertEqual(report["observations"][1]["http_status"], 403)
            self.assert_no_workload(state)
            state.preflight_errors.clear()
            state.group_read_only = True
            self.assertIn(
                "Target workspace is read-only", self.inspect(api, plan)["blockers"]
            )

    def test_metadata_identity_mismatch_blocks(self):
        with service() as (api, state):
            plan = self.plan(api)
            with patch.object(
                api, "group", return_value={"id": LEFT, "isOnDedicatedCapacity": True}
            ):
                self.assertFalse(self.inspect(api, plan)["ready"])
            self.assert_no_workload(state)

    def test_unrefreshed_import_is_not_rejected_from_is_refreshable_flag_alone(self):
        with service() as (api, state):
            plan = self.plan(api)
            with patch.object(
                api,
                "dataset",
                side_effect=lambda ws, model: {"id": model, "isRefreshable": False},
            ):
                report = self.inspect(api, plan)
                self.assertTrue(report["ready"])
                self.assertTrue(
                    any(
                        "isRefreshable=false" in warning
                        for warning in report["warnings"]
                    )
                )

    def test_service_principal_original_rls_flag_is_checked_even_if_target_has_no_roles(
        self,
    ):
        with service() as (api, state):
            plan = self.plan(api)
            api.client.credential.identity_type = "service_principal"
            with patch.object(
                api,
                "dataset",
                side_effect=lambda ws, model: {
                    "id": model,
                    "isEffectiveIdentityRolesRequired": True,
                },
            ):
                report = self.inspect(api, plan)
                self.assertTrue(
                    any("original RLS" in block for block in report["blockers"])
                )

    def test_capacity_observations_must_agree_when_both_ids_exist(self):
        with service() as (api, state):
            plan = self.plan(api)
            with (
                patch.object(
                    api, "workspace", return_value={"id": WS1, "capacityId": LEFT}
                ),
                patch.object(
                    api,
                    "group",
                    return_value={
                        "id": WS1,
                        "capacityId": RIGHT,
                        "isOnDedicatedCapacity": True,
                    },
                ),
            ):
                report = self.inspect(api, plan)
                self.assertTrue(any("disagree" in b for b in report["blockers"]))

    def test_unsettled_capacity_assignment_blocks_admission(self):
        with service() as (api, state):
            plan = self.plan(api)
            with patch.object(
                api,
                "workspace",
                return_value={"id": WS1, "capacityAssignmentProgress": "Assigning"},
            ):
                self.assertIn(
                    "Target capacity assignment is not settled",
                    self.inspect(api, plan)["blockers"],
                )

    def test_non_import_and_incremental_models_are_not_silently_admitted(self):
        with service() as (api, state):
            for source in (LEFT, RIGHT):
                state.models[source]["model"]["bim"]["model"]["tables"][0][
                    "partitions"
                ][0]["mode"] = "directLake"
            plan = self.plan(api)
            self.assertTrue(
                any(
                    "Import partitions only" in b
                    for b in self.inspect(api, plan)["blockers"]
                )
            )
            self.assert_no_workload(state)
        from .support import model

        bim = model()
        bim["bim"]["model"]["tables"][0]["refreshPolicy"] = {"policyType": "basic"}
        self.assertTrue(any("Incremental" in b for b in model_risks(bim)[0]))

    def test_duplicate_generated_partition_and_relationship_names_need_resolution(self):
        from .support import model

        m = model()
        table = m["bim"]["model"]["tables"][0]
        table["partitions"].append(
            {**copy.deepcopy(table["partitions"][0]), "name": "AnotherGeneratedGuid"}
        )
        relation = {
            "name": "GuidA",
            "fromTable": "Sales",
            "fromColumn": "Value",
            "toTable": "Sales",
            "toColumn": "Value",
        }
        m["bim"]["model"]["relationships"] = [relation, {**relation, "name": "GuidB"}]
        blocks, _ = model_risks(m)
        self.assertTrue(any("partition source" in b for b in blocks))
        self.assertTrue(any("relationship endpoints" in b for b in blocks))

    def test_service_principal_rls_blocked_and_user_has_explicit_security_warning(self):
        with service() as (api, state):
            for source in (LEFT, RIGHT):
                state.models[source]["model"]["bim"]["model"]["roles"] = [
                    {"name": "Viewer", "modelPermission": "read"}
                ]
            plan = self.plan(
                api, security={**SECURITY, "roles_and_membership_plan_reviewed": True}
            )
            api.client.credential.identity_type = "service_principal"
            self.assertTrue(
                any(
                    "cannot validate RLS" in b
                    for b in self.inspect(api, plan)["blockers"]
                )
            )
            api.client.credential.identity_type = "user"
            report = self.inspect(api, plan)
            self.assertTrue(report["ready"])
            self.assertTrue(any("Effective-user" in w for w in report["warnings"]))
            self.assert_no_workload(state)

    def test_connection_readability_and_sso_status_are_observed_without_credential_values(
        self,
    ):
        binding = {
            "id": CONNECTION,
            "connectivityType": "ShareableCloud",
            "connectionDetails": {"type": "Web", "path": "https://example.test"},
        }
        with service() as (api, state):
            state.connections[LEFT] = [binding]
            state.connections[RIGHT] = [binding]
            plan = self.plan(api)
            self.assertTrue(self.inspect(api, plan)["ready"])
            api.client.credential.identity_type = "service_principal"
            self.assertTrue(
                any(
                    "SSO state was not verified" in b
                    for b in self.inspect(api, plan)["blockers"]
                )
            )
            state.connections[LEFT][0]["credentialDetails"] = {
                "singleSignOnType": "None"
            }
            self.assertTrue(self.inspect(api, plan)["ready"])
            state.connections[LEFT][0]["credentialDetails"] = {
                "singleSignOnType": "Kerberos"
            }
            self.assertTrue(
                any(
                    "unsupported with SSO" in b
                    for b in self.inspect(api, plan)["blockers"]
                )
            )
            state.preflight_errors[f"/fabric/connections/{CONNECTION}"] = 403
            self.assertFalse(self.inspect(api, plan)["ready"])
            self.assert_no_workload(state)

    def test_preflight_does_not_claim_a_plan_with_security_blockers_is_ready(self):
        with service() as (api, state):
            plan = self.plan(api, security=None)
            self.assertFalse(plan["ready"])
            self.assertFalse(self.inspect(api, plan)["ready"])

    def test_query_budget_checked_before_any_workload(self):
        with service() as (api, state):
            plan = self.plan(api)
            report = self.inspect(api, plan, max_validation_queries=1)
            self.assertFalse(report["ready"])
            self.assertEqual(report["validation_query_count"], 8)
            self.assert_no_workload(state)

    def test_old_plan_without_deployment_policy_uses_current_auto_detection(self):
        with service() as (api, state):
            state.dedicated = False
            plan = self.plan(api)
            del plan["deployment"]
            save(self.directory / "plan.json", plan)
            Migration(api, self.directory).stage()
            self.assertEqual(
                next(
                    body
                    for method, path, body in state.calls
                    if method == "POST" and path.endswith("/refreshes")
                ),
                {"notifyOption": "NoNotification"},
            )

    def test_accepted_refresh_resumes_original_body_after_capacity_changes(self):
        with service() as (api, state):
            self.plan(api)
            migration = Migration(api, self.directory)
            with patch.object(
                api, "wait_refresh", side_effect=MergeError("Still running")
            ):
                with self.assertRaisesRegex(MergeError, "Still running"):
                    migration.stage()
            original_body = migration.journal.latest("intent", "refresh")["body"]
            state.dedicated = False
            migration.stage()
            self.assertEqual(
                migration.journal.latest("intent", "refresh")["body"], original_body
            )
            self.assertEqual(
                sum(
                    m == "POST" and p.endswith("/refreshes") for m, p, _ in state.calls
                ),
                1,
            )

    def test_exact_poll_rejects_wrong_refresh_identity(self):
        with service() as (api, state):
            initial = Response(202, {"x-ms-request-id": LEFT}, None)
            with patch.object(
                api.client,
                "request",
                return_value=Response(
                    200, {}, {"requestId": RIGHT, "status": "Completed"}
                ),
            ):
                with self.assertRaisesRegex(MergeError, "another request"):
                    api.wait_refresh(WS1, NEW, initial)

    def test_cli_preflight_has_only_gets_and_does_not_create_or_change_a_journal(self):
        with service() as (api, state):
            self.plan(api)
            before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
            state.calls.clear()
            cred = Mock()
            cred.account.return_value = {"tenant_id": "test"}
            out = Path(self.tmp.name) / "preflight"
            with (
                patch("fabric_merge.cli.AzureCliCredential", return_value=cred),
                patch("fabric_merge.cli.Client", return_value=api.client),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main(["preflight", str(self.directory), "--out", str(out)]), 0
                )
            self.assertEqual(
                before, {p.name: p.read_bytes() for p in self.directory.iterdir()}
            )
            self.assertTrue(all(c[0] == "GET" for c in state.calls))
            self.assertTrue(load(out / "preflight.json")["ready"])


if __name__ == "__main__":
    unittest.main()
