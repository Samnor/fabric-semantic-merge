import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fabric_merge.cli import main, select_models
from fabric_merge.util import digest, save
from fabric_merge.workflow import Migration, create_plan
from .support import LEFT, NEW, RIGHT, SECURITY, WS1, service


class CliTests(unittest.TestCase):
    def test_interactive_selection(self):
        with (
            service() as (api, state),
            patch("sys.stdin.isatty", return_value=True),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            selections = iter(["1", "2"])
            left, right = select_models(api, input_fn=lambda _: next(selections))
            self.assertEqual(left, (WS1, LEFT))
            self.assertEqual(right, (WS1, RIGHT))

    def test_actual_cli_plan_stage_apply_and_status(self):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "plan"
            policy = Path(tmp) / "policy.json"
            save(policy, SECURITY)
            credential = Mock()
            credential.account.return_value = {
                "tenant_id": "test-tenant",
                "subscription_id": "test-subscription",
            }
            with (
                patch("fabric_merge.cli.AzureCliCredential", return_value=credential),
                patch("fabric_merge.cli.Client", return_value=api.client),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "plan",
                            "--left",
                            WS1 + "/" + LEFT,
                            "--right",
                            WS1 + "/" + RIGHT,
                            "--name",
                            "Merged",
                            "--out",
                            str(directory),
                            "--security",
                            str(policy),
                        ]
                    ),
                    0,
                )
                self.assertEqual(main(["stage", str(directory), "--yes"]), 0)
                self.assertEqual(main(["apply", str(directory), "--yes"]), 0)
                self.assertTrue(
                    all(r["datasetId"] == NEW for r in state.reports.values())
                )
                count = credential.account.call_count
                self.assertEqual(main(["status", str(directory)]), 0)
                self.assertEqual(credential.account.call_count, count)

    def test_rls_model_can_stage_but_cannot_cut_over_without_bound_review(self):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            for source in (LEFT, RIGHT):
                state.models[source]["model"]["bim"]["model"]["roles"] = [
                    {"name": "Viewer", "modelPermission": "read"}
                ]
            policy = {**SECURITY, "roles_and_membership_plan_reviewed": True}
            directory = Path(tmp) / "plan"
            create_plan(
                api,
                directory,
                (WS1, LEFT),
                (WS1, RIGHT),
                WS1,
                "Merged",
                security=policy,
            )
            migration = Migration(api, directory)
            migration.stage()
            from fabric_merge.util import MergeError

            with self.assertRaisesRegex(MergeError, "access-proof"):
                migration.apply()
            self.assertFalse(any(c[1].endswith("/Rebind") for c in state.calls))
            proof = {
                "model_id": NEW,
                "workspace_id": WS1,
                "security_policy_sha256": digest(policy),
                "memberships_verified": True,
                "reviewer": "offline-test-only",
            }
            self.assertEqual(migration.apply(access_proof=proof)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
