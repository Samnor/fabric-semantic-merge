"""Adversarial tests for evidence coverage, numeric policies and data drift."""

import hashlib
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fabric_merge.parity import compare_rows, render_report, run_parity, verify_evidence
from fabric_merge.cli import main
from fabric_merge.transport import Response, UncertainRequest
from fabric_merge.util import MergeError, load, save
from .support import LEFT, NEW, RIGHT, WS1
from .test_parity import QueryAPI


def policy(keys=None, absolute="0", relative="0"):
    return {
        "mode": "keyed_numeric",
        "key_columns": keys if keys is not None else [],
        "absolute_tolerance": absolute,
        "relative_tolerance": relative,
    }


class ParityHillclimbTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "evidence"
        self.api = QueryAPI()

    def run_parity(self, **kwargs):
        return run_parity(
            self.api, (WS1, LEFT), (WS1, RIGHT), (WS1, NEW), query_interval=0, **kwargs
        )

    def reseal(self):
        manifest = load(self.directory / "manifest.json")
        manifest["files"] = {
            p.relative_to(self.directory).as_posix(): {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "bytes": p.stat().st_size,
            }
            for p in self.directory.rglob("*")
            if p.is_file() and p.name != "manifest.json"
        }
        save(self.directory / "manifest.json", manifest)

    def test_missing_right_coverage_rejected_even_with_consistent_new_hashes(self):
        self.run_parity(out=self.directory)
        report = load(self.directory / "report.json")
        report["checks"] = report["checks"][:2]
        report.update(
            {
                "planned_probes": 2,
                "queries_submitted": 4,
                "counts": {"passed": 2, "mismatch": 0, "error": 0},
            }
        )
        save(self.directory / "report.json", report)
        save(self.directory / "probes.json", load(self.directory / "probes.json")[:2])
        for subdir in ("queries", "results", "diffs"):
            for path in (self.directory / subdir).iterdir():
                if path.name.startswith(("0003", "0004")):
                    path.unlink()
        self.reseal()
        with self.assertRaisesRegex(MergeError, "both originals"):
            verify_evidence(self.directory)

    def test_two_references_to_same_model_cannot_be_verified_as_three_models(self):
        self.run_parity(out=self.directory)
        report = load(self.directory / "report.json")
        report["models"]["right"] = report["models"]["left"]
        save(self.directory / "report.json", report)
        self.reseal()
        with self.assertRaisesRegex(MergeError, "three distinct"):
            verify_evidence(self.directory)

    def test_malformed_manifests_fail_as_actionable_merge_errors(self):
        self.directory.mkdir()
        for manifest in (
            [],
            None,
            {
                "schema_version": 2,
                "kind": "semantic_model_parity_evidence",
                "files": [],
            },
        ):
            save(self.directory / "manifest.json", manifest)
            with self.assertRaises(MergeError):
                verify_evidence(self.directory)

    def test_readable_report_must_agree_with_machine_results(self):
        self.run_parity(out=self.directory)
        (self.directory / "README.md").write_text("# Everything failed\n")
        self.reseal()
        with self.assertRaisesRegex(MergeError, "Readable report"):
            verify_evidence(self.directory)

    def test_v1_exact_bundle_still_replays(self):
        report = self.run_parity(out=self.directory)
        report["schema_version"] = 1
        for field in ("check_stability", "coverage"):
            report.pop(field)
        for check in report["checks"]:
            check.pop("signal")
            check.pop("stability")
        save(self.directory / "report.json", report)
        (self.directory / "README.md").write_text(render_report(report))
        manifest = load(self.directory / "manifest.json")
        manifest["schema_version"] = 1
        save(self.directory / "manifest.json", manifest)
        self.reseal()
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "passed")

    def test_numeric_comparison_is_opt_in_and_retains_raw_hash_differences(self):
        a, b = [{"v": 1}], [{"v": 1.0}]
        self.assertFalse(compare_rows(a, b)["equal"])
        result = compare_rows(a, b, policy())
        self.assertTrue(result["equal"])
        self.assertNotEqual(result["source_sha256"], result["target_sha256"])
        self.assertEqual(result["numeric_comparison"]["tolerated_cells"], 1)

    def test_absolute_relative_boundaries_and_large_integers_are_exact(self):
        for x, y, allowed in (
            ("0.1", "0.11", True),
            ("0.1", "0.11000000000000001", False),
        ):
            result = compare_rows(
                [{"v": float(x)}], [{"v": float(y)}], policy(absolute="0.01")
            )
            self.assertEqual(result["equal"], allowed)
        self.assertTrue(
            compare_rows(
                [{"v": 1000000}], [{"v": 1000001}], policy(relative="0.000001")
            )["equal"]
        )
        large = 10**50
        self.assertFalse(
            compare_rows([{"v": large}], [{"v": large + 1}], policy())["equal"]
        )

    def test_key_alignment_is_deterministic_without_greedy_pairing(self):
        a = [{"k": "a", "v": 1}, {"k": "b", "v": 2}]
        b = [{"k": "b", "v": 2.001}, {"k": "a", "v": 1.001}]
        result = compare_rows(a, b, policy(["k"], absolute="0.01"))
        self.assertTrue(result["equal"])
        self.assertEqual(result["numeric_comparison"]["numeric_cells"], 2)
        b[1]["k"] = "c"
        self.assertFalse(compare_rows(a, b, policy(["k"], absolute="100"))["equal"])

    def test_duplicate_missing_keys_and_multiline_scalar_results_refuse_tolerance(self):
        for a, b, config in (
            ([{"k": 1, "v": 2}] * 2, [{"k": 1, "v": 2}], policy(["k"])),
            ([{"v": 1}], [{"v": 1}], policy(["missing"])),
            ([{"v": 1}] * 2, [{"v": 1}] * 2, policy()),
        ):
            with self.assertRaises(MergeError):
                compare_rows(a, b, config)

    def test_booleans_nulls_missing_fields_and_strings_never_use_numeric_tolerance(
        self,
    ):
        for a, b in (
            ({"v": True}, {"v": 1}),
            ({"v": None}, {"v": 0}),
            ({"v": None}, {}),
            ({"v": "1"}, {"v": 1}),
        ):
            self.assertFalse(compare_rows([a], [b], policy(absolute="999999"))["equal"])

    def test_invalid_numeric_policy_admits_zero_queries(self):
        for config in (
            policy(absolute="NaN"),
            policy(absolute="-0.1"),
            policy(relative=True),
            {"mode": "keyed_numeric"},
            {**policy(), "unknown": 1},
        ):
            probe = {
                "name": "numeric",
                "source": "left",
                "query": "EVALUATE Sales",
                "comparison": config,
            }
            result = self.run_parity(probes=[probe])
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["queries_submitted"], 0)

    def test_tolerated_scalar_bundle_replays_its_explicit_policy(self):
        probe = {
            "name": "numeric",
            "source": "left",
            "query": "EVALUATE scalar",
            "comparison": policy(absolute="0.01"),
        }
        self.api.rows[(LEFT, probe["query"])] = [{"v": 30}]
        self.api.rows[(NEW, probe["query"])] = [{"v": 30.001}]
        result = self.run_parity(probes=[probe], out=self.directory)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(
            result["checks"][-1]["comparison"]["numeric_comparison"]["tolerated_cells"],
            1,
        )
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "passed")

    def test_numeric_policy_changes_cannot_reuse_old_comparison_evidence(self):
        probe = {
            "name": "numeric",
            "source": "left",
            "query": "EVALUATE scalar",
            "comparison": policy(absolute="0.1"),
        }
        self.api.rows[(LEFT, probe["query"])] = [{"v": 30}]
        self.api.rows[(NEW, probe["query"])] = [{"v": 30.01}]
        self.run_parity(probes=[probe], out=self.directory)
        probes = load(self.directory / "probes.json")
        probes[-1]["comparison"]["absolute_tolerance"] = "0"
        save(self.directory / "probes.json", probes)
        self.reseal()
        with self.assertRaisesRegex(MergeError, "Diff replay"):
            verify_evidence(self.directory)

    def test_ambiguous_key_error_preserves_successful_api_receipts_and_replays(self):
        probe = {
            "name": "ambiguous",
            "source": "left",
            "query": "EVALUATE duplicate",
            "comparison": policy(["k"]),
        }
        for identity in (LEFT, NEW):
            self.api.rows[(identity, probe["query"])] = [{"k": 1, "v": 2}] * 2
        result = self.run_parity(probes=[probe], out=self.directory)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("not unique", result["checks"][-1]["comparison_error"])
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "incomplete")

    def test_stability_budget_is_four_requests_per_probe_before_submission(self):
        result = self.run_parity(
            check_stability=True, max_queries=15, out=self.directory
        )
        self.assertEqual(result["queries_submitted"], 0)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "incomplete")

    def test_repeated_stable_data_observations_replay(self):
        result = self.run_parity(check_stability=True, out=self.directory)
        self.assertEqual(result["queries_submitted"], 16)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(
            all(c["stability"]["status"] == "observed_stable" for c in result["checks"])
        )
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "passed")

    def test_data_only_source_drift_is_incomplete_with_unchanged_definitions(self):
        original = self.api.query_response
        counts = {}

        def query(workspace, identity, dax):
            result = original(workspace, identity, dax)
            key = identity, dax
            counts[key] = counts.get(key, 0) + 1
            if identity == LEFT and counts[key] > 1:
                result.body["results"][0]["tables"][0]["rows"] = [{"[Value]": 999}]
            return result

        with patch.object(self.api, "query_response", side_effect=query):
            result = self.run_parity(check_stability=True, out=self.directory)
        self.assertEqual(
            result["definition_sha256_before"], result["definition_sha256_after"]
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["checks"][0]["stability"]["status"], "changed")
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "incomplete")

    def test_target_drift_inside_numeric_tolerance_still_makes_run_incomplete(self):
        probe = {
            "name": "left:measure:Sales:Quantity",
            "source": "left",
            "query": "EVALUATE scalar",
            "comparison": policy(absolute="0.1"),
        }
        original = self.api.query_response
        counter = 0

        def query(workspace, identity, dax):
            nonlocal counter
            result = original(workspace, identity, dax)
            if identity == NEW and dax == probe["query"]:
                counter += 1
                result.body["results"][0]["tables"][0]["rows"] = [
                    {"[Value]": 30 + counter * 0.001}
                ]
            return result

        with patch.object(self.api, "query_response", side_effect=query):
            result = self.run_parity(
                probes=[probe], check_stability=True, out=self.directory
            )
        self.assertEqual(result["checks"][1]["status"], "passed")
        self.assertEqual(result["checks"][1]["stability"]["status"], "changed")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "incomplete")
        report = load(self.directory / "report.json")
        report["checks"][1]["stability"]["status"] = "observed_stable"
        save(self.directory / "report.json", report)
        self.reseal()
        with self.assertRaisesRegex(MergeError, "Data stability replay"):
            verify_evidence(self.directory)

    def test_cli_stability_flag_admits_the_explicit_four_query_plan(self):
        with (
            patch("fabric_merge.cli.AzureCliCredential"),
            patch("fabric_merge.cli.Fabric", return_value=self.api),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = main(
                [
                    "parity",
                    "--left",
                    WS1 + "/" + LEFT,
                    "--right",
                    WS1 + "/" + RIGHT,
                    "--target",
                    WS1 + "/" + NEW,
                    "--check-stability",
                    "--query-interval",
                    "0",
                    "--out",
                    str(self.directory),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(load(self.directory / "report.json")["queries_submitted"], 16)
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "passed")

    def test_repeat_transport_failure_is_incomplete_and_replayable(self):
        original = self.api.query_response
        counts = {}

        def query(workspace, identity, dax):
            key = identity, dax
            counts[key] = counts.get(key, 0) + 1
            if identity == NEW and counts[key] == 2:
                raise UncertainRequest("Repeated observation unavailable")
            return original(workspace, identity, dax)

        with patch.object(self.api, "query_response", side_effect=query):
            result = self.run_parity(check_stability=True, out=self.directory)
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(
            any(c["stability"]["status"] == "error" for c in result["checks"])
        )
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "incomplete")

    def test_empty_and_blank_answers_raise_coverage_warnings(self):
        for rows, signal in (([], "empty_both"), ([{"v": None}], "blank_both")):
            response = Response(200, {}, {"results": [{"tables": [{"rows": rows}]}]})
            with patch.object(self.api, "query_response", return_value=response):
                result = self.run_parity()
            self.assertEqual(result["status"], "passed")
            self.assertTrue(all(c["signal"] == signal for c in result["checks"]))
            self.assertEqual(
                result["coverage"]["sources"]["right"]["passed_without_values"], 2
            )
            self.assertTrue(
                any("populated contexts" in w for w in result["coverage"]["warnings"])
            )
