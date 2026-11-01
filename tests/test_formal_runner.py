"""Fail closed on model-checker errors and vacuous negative-control results."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "formal_check", Path(__file__).resolve().parents[1] / "formal" / "check.py"
)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class FormalRunnerTests(unittest.TestCase):
    def test_saved_evidence_for_different_sources_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "results.json").write_text(json.dumps({"source_sha256": {}}))
            with self.assertRaisesRegex(RuntimeError, "different source bytes"):
                CHECK.verify(directory)

    def test_expected_counterexample_needs_specific_invariant_and_exit_code(self):
        case = {"name": "negative", "violation": "AtMostOnce"}
        self.assertEqual(
            CHECK.check_result(case, 12, "Error: Invariant AtMostOnce is violated."),
            "AtMostOnce",
        )
        for code, log in (
            (75, "Error: Successor state is not completely specified"),
            (0, "Model checking completed. No error has been found."),
            (12, "Error: Invariant Other is violated."),
        ):
            with self.assertRaises(RuntimeError):
                CHECK.check_result(case, code, log)

    def test_positive_case_must_exhaust_queue(self):
        for log in (
            "",
            "Model checking completed. No error has been found.",
            "Error: Invariant AtMostOnce is violated.",
        ):
            with self.assertRaises(RuntimeError):
                CHECK.check_result({"name": "fixed"}, 0, log)

    def test_positive_and_negative_controls_are_distinct(self):
        cases = list(CHECK.cases())
        self.assertEqual(len({case["name"] for case in cases}), len(cases))
        self.assertEqual(
            [c["reports"] for c in cases if not c.get("violation")], [1, 2, 3]
        )
        self.assertTrue(all("Legacy = FALSE" in CHECK.config(c) for c in cases[:3]))
