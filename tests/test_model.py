import copy
import unittest

from fabric_merge.model import decode_definition, merge_models
from fabric_merge.util import MergeError
from .support import model


class ModelTests(unittest.TestCase):
    def test_invalid_calculated_output_column_is_blocked_before_live_creation(self):
        left, right = model(), model("Count")
        for value in (left, right):
            value["bim"]["model"]["tables"][0]["columns"][0].pop("type")
        result = merge_models(left, right, "Merged")
        self.assertFalse(result["ready"])
        self.assertTrue(
            any("calculatedTableColumn" in issue for issue in result["issues"])
        )

    def test_disjoint_measures_share_one_import_partition(self):
        left, right = model(), model("Count", "COUNTROWS('Sales')")
        original = copy.deepcopy(left)
        result = merge_models(left, right, "Merged")
        self.assertTrue(result["ready"])
        self.assertEqual(
            result["summary"],
            {
                "tables": 1,
                "columns": 1,
                "measures": 2,
                "relationships": 0,
                "roles": 0,
                "partitions": 1,
                "modes": ["import"],
            },
        )
        self.assertEqual(left, original)
        self.assertEqual(decode_definition(result["definition"]), result["model"])

    def test_conflicting_measure_requires_explicit_resolution(self):
        left, right = model(), model(expression="MAX('Sales'[Value])")
        result = merge_models(left, right, "Merged")
        self.assertFalse(result["ready"])
        path = result["conflicts"][0]["path"]
        fixed = merge_models(left, right, "Merged", {path: {"take": "right"}})
        self.assertTrue(fixed["ready"])
        self.assertEqual(
            fixed["model"]["bim"]["model"]["tables"][0]["measures"][0]["expression"],
            "MAX('Sales'[Value])",
        )

    def test_table_partition_conflict_not_silently_concatenated(self):
        left, right = model(), model("Count")
        right["bim"]["model"]["tables"][0]["partitions"][0]["source"]["expression"] = (
            "different rows"
        )
        result = merge_models(left, right, "Merged")
        self.assertFalse(result["ready"])
        self.assertTrue(any("partitions" in c["path"] for c in result["conflicts"]))

    def test_additive_tables_and_relationships_preserved(self):
        left, right = model(), model("Count")
        right["bim"]["model"]["tables"].append(
            {"name": "Dim", "columns": [{"name": "Key", "dataType": "int64"}]}
        )
        right["bim"]["model"]["relationships"] = [
            {
                "name": "Sales_Dim",
                "fromTable": "Sales",
                "fromColumn": "Value",
                "toTable": "Dim",
                "toColumn": "Key",
            }
        ]
        result = merge_models(left, right, "Merged")
        self.assertTrue(result["ready"])
        self.assertEqual(result["summary"]["tables"], 2)
        self.assertEqual(result["summary"]["relationships"], 1)

    def test_case_insensitive_name_collision(self):
        left, right = model(), model("quantity", "MAX('Sales'[Value])")
        result = merge_models(left, right, "Merged")
        self.assertFalse(result["ready"])
        self.assertEqual(
            len(result["model"]["bim"]["model"]["tables"][0]["measures"]), 1
        )

    def test_invalid_relationship_is_blocked(self):
        right = model("Count")
        right["bim"]["model"]["relationships"] = [
            {
                "name": "broken",
                "fromTable": "Missing",
                "fromColumn": "x",
                "toTable": "Sales",
                "toColumn": "Value",
            }
        ]
        self.assertFalse(merge_models(model(), right, "Merged")["ready"])

    def test_unknown_array_and_metadata_conflicts_require_review(self):
        left, right = model(), model("Count")
        left["extras"]["Copilot/settings.json"] = "left"
        right["extras"]["Copilot/settings.json"] = "right"
        self.assertFalse(merge_models(left, right, "Merged")["ready"])

    def test_unused_resolution_rejected(self):
        with self.assertRaises(MergeError):
            merge_models(
                model(), model("Count"), "Merged", {"/misspelled": {"take": "left"}}
            )

    def test_global_duplicate_measure_in_different_tables_rejected(self):
        left, right = model(), model()
        right["bim"]["model"]["tables"][0]["name"] = "Other"
        self.assertFalse(merge_models(left, right, "Merged")["ready"])


if __name__ == "__main__":
    unittest.main()
