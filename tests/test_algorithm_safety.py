"""Adversarial merge/default, classification and fresh-cutover regressions."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fabric_merge.model import encode_definition, merge_models
from fabric_merge.transport import UncertainRequest
from fabric_merge.util import MergeError
from fabric_merge.workflow import Migration, check_security, create_plan
from .support import LEFT, NEW, REPORTS, RIGHT, SECURITY, WS1, model, service


SETTING = "/bim/model/tables/Sales/isHidden"
LABEL_A = "70000000-0000-0000-0000-000000000001"
LABEL_B = "70000000-0000-0000-0000-000000000002"


class MergePresenceTests(unittest.TestCase):
    def test_one_sided_semantic_setting_blocks_in_either_direction(self):
        for side in (0, 1):
            with self.subTest(side=side):
                sources = [model(), model("Count")]
                sources[side]["bim"]["model"]["tables"][0]["isHidden"] = False
                result = merge_models(*sources, "Merged")
                self.assertFalse(result["ready"])
                conflict = next(c for c in result["conflicts"] if c["path"] == SETTING)
                self.assertEqual(conflict["left_present"], side == 0)
                self.assertEqual(conflict["right_present"], side == 1)

    def test_explicit_resolution_can_preserve_omission_or_take_value(self):
        left, right = model(), model("Count")
        right["bim"]["model"]["tables"][0]["isHidden"] = True
        for take in ("left", "right"):
            with self.subTest(take=take):
                result = merge_models(left, right, "Merged", {SETTING: {"take": take}})
                self.assertTrue(result["ready"])
                table = result["model"]["bim"]["model"]["tables"][0]
                self.assertEqual("isHidden" in table, take == "right")
                self.assertEqual(result["decisions"][0]["resolution"], {"take": take})

    def test_omission_is_distinct_from_explicit_null(self):
        left, right = model(), model("Count")
        right["bim"]["model"]["tables"][0]["description"] = None
        result = merge_models(left, right, "Merged")
        self.assertFalse(result["ready"])
        conflict = result["conflicts"][0]
        self.assertFalse(conflict["left_present"])
        self.assertTrue(conflict["right_present"])
        fixed = merge_models(left, right, "Merged", {conflict["path"]: {"value": None}})
        self.assertTrue(fixed["ready"])
        self.assertIn("description", fixed["model"]["bim"]["model"]["tables"][0])

    def test_optional_scalar_and_unknown_bag_settings_do_not_merge_silently(self):
        for key, value in [
            ("calculationGroup", {"precedence": 10}),
            ("unknownSetting", False),
        ]:
            with self.subTest(key=key):
                left, right = model(), model("Count")
                right["bim"]["model"]["tables"][0][key] = value
                self.assertFalse(merge_models(left, right, "Merged")["ready"])

    def test_additive_named_collections_and_lineage_remain_supported(self):
        left, right = model(), model("Count")
        right["bim"]["model"]["tables"][0]["lineageTag"] = "synthetic-lineage"
        right["bim"]["model"]["relationships"] = []
        right["bim"]["model"]["tables"].append({"name": "Dim", "columns": []})
        result = merge_models(left, right, "Merged")
        self.assertTrue(result["ready"])
        self.assertEqual(result["summary"]["measures"], 2)
        self.assertEqual(result["summary"]["tables"], 2)

    def test_same_named_relationship_missing_active_setting_requires_review(self):
        left, right = model(), model("Count")
        relation = {
            "name": "r",
            "fromTable": "Sales",
            "fromColumn": "Value",
            "toTable": "Dim",
            "toColumn": "Key",
        }
        for source in (left, right):
            source["bim"]["model"]["tables"].append(
                {"name": "Dim", "columns": [{"name": "Key", "dataType": "int64"}]}
            )
            source["bim"]["model"]["relationships"] = [copy.deepcopy(relation)]
        right["bim"]["model"]["relationships"][0]["isActive"] = False
        result = merge_models(left, right, "Merged")
        self.assertFalse(result["ready"])
        self.assertTrue(
            any(c["path"].endswith("/isActive") for c in result["conflicts"])
        )


class LabelPolicyTests(unittest.TestCase):
    def issues(self, labels, target, **extra):
        sources = {
            name: {"metadata": {"sensitivityLabel": {"id": label}}}
            for name, label in zip(("left", "right"), labels)
        }
        return check_security(
            {**SECURITY, "label_id": target, **extra},
            sources,
            merge_models(model(), model("Count"), "Merged"),
        )

    def test_mixed_labels_require_approval_even_when_target_matches_one_original(self):
        for target in (LABEL_A, LABEL_B, None):
            with self.subTest(target=target):
                self.assertTrue(self.issues((LABEL_A, LABEL_B), target))
                self.assertFalse(
                    self.issues((LABEL_A, LABEL_B), target, label_change_approved=True)
                )

    def test_same_label_and_unlabeled_sources_keep_existing_behavior(self):
        for labels, target in [
            ((LABEL_A, LABEL_A), LABEL_A),
            ((None, None), None),
            ((LABEL_A, None), LABEL_A),
        ]:
            with self.subTest(labels=labels):
                self.assertFalse(self.issues(labels, target))

    def test_label_removal_and_replacement_require_literal_true(self):
        for target in (None, LABEL_B):
            for approval in (None, False, 1, "yes"):
                with self.subTest(target=target, approval=approval):
                    self.assertTrue(
                        self.issues(
                            (LABEL_A, LABEL_A), target, label_change_approved=approval
                        )
                    )


class CutoverSafetyTests(unittest.TestCase):
    def plan(self, api, directory, **kwargs):
        return create_plan(
            api,
            directory,
            (WS1, LEFT),
            (WS1, RIGHT),
            WS1,
            "Merged",
            security=SECURITY,
            **kwargs,
        )

    def test_changed_data_after_stage_stops_before_grants_or_rebinds(self):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "plan"
            self.plan(api, directory)
            migration = Migration(api, directory)
            migration.stage()
            state.mismatch = True
            state.calls.clear()
            with self.assertRaisesRegex(
                MergeError, "Fresh cutover validation mismatch"
            ):
                Migration(api, directory).apply()
            self.assertFalse(
                any(
                    p.endswith(("/Rebind", "/users")) and method == "POST"
                    for method, p, _ in state.calls
                )
            )
            self.assertTrue(migration.journal.latest("cutover_validation_failed"))
            self.assertNotEqual(migration.status()["status"], "completed")

    def test_every_partial_resume_rechecks_data_and_does_not_replay_rebind(self):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "plan"
            self.plan(api, directory)
            migration = Migration(api, directory)
            state.drop_response = REPORTS[0]
            with self.assertRaises(UncertainRequest):
                migration.apply()
            migration.reconcile()
            before = sum(p.endswith("/Rebind") for _, p, _ in state.calls)
            state.mismatch = True
            with self.assertRaisesRegex(
                MergeError, "Fresh cutover validation mismatch"
            ):
                migration.apply()
            self.assertEqual(
                sum(p.endswith("/Rebind") for _, p, _ in state.calls), before
            )
            self.assertEqual(state.reports[REPORTS[1]]["datasetId"], LEFT)
            state.mismatch = False
            self.assertEqual(migration.apply()["status"], "completed")
            self.assertEqual(
                sum(p.endswith(REPORTS[0] + "/Rebind") for _, p, _ in state.calls), 1
            )

    def test_whole_apply_query_budget_is_checked_before_any_workload(self):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "plan"
            self.plan(api, directory)
            state.calls.clear()
            with self.assertRaisesRegex(MergeError, "cutover query budget"):
                Migration(api, directory).apply(max_validation_queries=8)
            self.assertFalse(
                any(
                    method == "POST" and not p.endswith("/getDefinition")
                    for method, p, _ in state.calls
                )
            )
            self.assertNotIn(NEW, state.models)

    def test_staged_apply_reserves_only_new_cutover_queries(self):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "plan"
            self.plan(api, directory)
            migration = Migration(api, directory)
            migration.stage(max_validation_queries=8)
            before = sum(p.endswith("/executeQueries") for _, p, _ in state.calls)
            result = migration.apply(max_validation_queries=8)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(
                sum(p.endswith("/executeQueries") for _, p, _ in state.calls) - before,
                8,
            )
            self.assertTrue(migration.journal.latest("cutover_validated"))

    def test_legacy_ready_plan_rechecks_label_policy_without_cloud_workload(self):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "plan"
            state.models[LEFT]["sensitivityLabel"] = {"id": LABEL_A}
            state.models[RIGHT]["sensitivityLabel"] = {"id": LABEL_B}
            with patch("fabric_merge.workflow.check_security", return_value=[]):
                create_plan(
                    api,
                    directory,
                    (WS1, LEFT),
                    (WS1, RIGHT),
                    WS1,
                    "Merged",
                    security={**SECURITY, "label_id": LABEL_A},
                )
            state.calls.clear()
            with self.assertRaisesRegex(MergeError, "label_change_approved"):
                Migration(api, directory).stage()
            self.assertFalse(
                any(
                    method == "POST" and not p.endswith("/getDefinition")
                    for method, p, _ in state.calls
                )
            )

    def test_legacy_ready_plan_rechecks_omitted_settings(self):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "plan"
            state.models[RIGHT]["model"]["bim"]["model"]["tables"][0]["isHidden"] = True
            # Build an actual old-style preview by concealing only its new blockers.
            original = merge_models

            def old_ready(*args, **kwargs):
                result = original(*args, **kwargs)
                result["model"]["bim"]["model"]["tables"][0]["isHidden"] = True
                result["definition"] = encode_definition(result["model"])
                result["conflicts"] = []
                result["ready"] = not result["issues"]
                return result

            with patch("fabric_merge.workflow.merge_models", side_effect=old_ready):
                self.plan(api, directory)
            state.calls.clear()
            with self.assertRaisesRegex(MergeError, "merge safeguards"):
                Migration(api, directory).stage()
            self.assertNotIn(NEW, state.models)
            self.assertFalse(
                any(
                    method == "POST" and not p.endswith("/getDefinition")
                    for method, p, _ in state.calls
                )
            )


if __name__ == "__main__":
    unittest.main()
