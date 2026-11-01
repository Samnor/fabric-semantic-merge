"""Parity correctness, partial evidence, read-only isolation and offline replay."""

import contextlib
import copy
import hashlib
import io
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

from fabric_merge.cli import main
from fabric_merge.api import query_rows
from fabric_merge.model import merge_models
from fabric_merge.parity import compare_rows, run_parity, verify_evidence
from fabric_merge.transport import Response, UncertainRequest
from fabric_merge.util import MergeError, load, save
from fabric_merge.workflow import Migration, create_plan
from .support import LEFT, NEW, RIGHT, SECURITY, WS1, model, service


class QueryAPI:
    def __init__(self):
        left, right = model(), model("Count", "COUNTROWS('Sales')")
        self.models = {
            LEFT: left,
            RIGHT: right,
            NEW: merge_models(left, right, "Merged")["model"],
        }
        self.calls, self.rows = [], {}
        self.failure = self.changed = self.definition_failure = None
        self.partial = False

    def definition(self, workspace, identity):
        previous = sum(c == ("definition", workspace, identity) for c in self.calls)
        self.calls.append(("definition", workspace, identity))
        if identity == self.definition_failure:
            raise MergeError("Definition access denied")
        value = copy.deepcopy(self.models[identity])
        if previous and identity == self.changed:
            value["bim"]["model"]["culture"] = "sv-SE"
        return value

    def query_response(self, workspace, identity, query):
        self.calls.append(("query", workspace, identity, query))
        if identity == self.failure:
            raise UncertainRequest("Query transport failed")
        if self.partial and identity == NEW:
            return Response(
                200,
                {},
                {
                    "results": [
                        {"error": {"code": "Truncated"}, "tables": [{"rows": []}]}
                    ]
                },
            )
        value = 2 if "COUNTROWS" in query or "[Count]" in query else 30
        rows = self.rows.get((identity, query), [{"[Value]": value}])
        return Response(
            200,
            {"requestid": "synthetic-receipt", "authorization": "must-not-export"},
            {"results": [{"tables": [{"rows": copy.deepcopy(rows)}]}]},
        )


class ParityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "evidence"
        self.api = QueryAPI()

    def run_parity(self, **options):
        return run_parity(
            self.api, (WS1, LEFT), (WS1, RIGHT), (WS1, NEW), query_interval=0, **options
        )

    def test_disjoint_measures_both_originals_and_no_folder_by_default(self):
        result = self.run_parity()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["counts"], {"passed": 4, "mismatch": 0, "error": 0})
        self.assertEqual(result["queries_submitted"], 8)
        self.assertEqual({c["source"] for c in result["checks"]}, {"left", "right"})
        self.assertFalse(self.directory.exists())
        self.assertIsNone(result["evidence_directory"])
        self.assertTrue(all(c[0] in ("query", "definition") for c in self.api.calls))

    def test_optional_evidence_is_private_complete_and_replayable(self):
        result = self.run_parity(out=self.directory)
        self.assertEqual(result["status"], "passed")
        self.assertTrue((self.directory / "README.md").exists())
        self.assertEqual(len(list((self.directory / "queries").iterdir())), 8)
        receipt = load(self.directory / "results/0001-source.json")
        self.assertEqual(
            receipt["response_headers"], {"requestid": "synthetic-receipt"}
        )
        self.assertNotIn(
            "must-not-export", (self.directory / "results/0001-source.json").read_text()
        )
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertTrue(
            all(
                stat.S_IMODE(p.stat().st_mode) == 0o600
                for p in self.directory.rglob("*")
                if p.is_file()
            )
        )
        self.assertEqual(verify_evidence(self.directory)["probes_replayed"], 4)

    def test_row_order_is_ignored_but_duplicate_counts_are_not(self):
        left = [{"x": 1}, {"x": 1}, {"x": 2}]
        self.assertTrue(compare_rows(left, list(reversed(left)))["equal"])
        difference = compare_rows(left, [{"x": 1}, {"x": 2}])
        self.assertFalse(difference["equal"])
        self.assertEqual(difference["missing_from_target"]["row_occurrences"], 1)
        self.assertEqual(
            difference["missing_from_target"]["samples"],
            [{"row": {"x": 1}, "count": 1}],
        )

    def test_null_numeric_types_and_column_aliases_remain_distinct(self):
        for left, right in ((None, 0), (True, 1), (1, 1.0)):
            self.assertFalse(compare_rows([{"x": left}], [{"x": right}])["equal"])
        self.assertFalse(compare_rows([{"x": 1}], [{"y": 1}])["equal"])

    def test_mismatch_keeps_rows_diffs_and_failure_report(self):
        query = "EVALUATE ROW(\"Value\", COUNTROWS('Sales'))"
        self.api.rows[(NEW, query)] = [{"[Value]": 99}]
        result = self.run_parity(out=self.directory)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["counts"]["mismatch"], 2)
        self.assertFalse(load(self.directory / "diffs/0001.json")["equal"])
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "failed")

    def test_query_errors_are_incomplete_not_false_mismatches(self):
        self.api.failure = RIGHT
        result = self.run_parity(out=self.directory)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["counts"]["error"], 2)
        self.assertEqual(result["queries_submitted"], 8)
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "incomplete")

    def test_http_200_partial_body_preserved_and_never_passes(self):
        self.api.partial = True
        result = self.run_parity(out=self.directory)
        self.assertEqual(result["status"], "incomplete")
        receipt = load(self.directory / "results/0001-target.json")
        self.assertEqual(receipt["http_status"], 200)
        self.assertEqual(
            receipt["response_body"]["results"][0]["error"]["code"], "Truncated"
        )
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "incomplete")

    def test_unexpected_http_success_status_cannot_pass_or_verify_as_parity(self):
        original = self.api.query_response

        def query(*args):
            response = original(*args)
            return Response(201, response.headers, response.body)

        with patch.object(self.api, "query_response", side_effect=query):
            result = self.run_parity(out=self.directory)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "incomplete")

    def test_schema_missing_fields_and_wrong_column_type_fail(self):
        table = self.api.models[NEW]["bim"]["model"]["tables"][0]
        table["measures"] = [m for m in table["measures"] if m["name"] != "Count"]
        table["columns"][0]["dataType"] = "string"
        result = self.run_parity(out=self.directory)
        self.assertEqual(result["status"], "failed")
        kinds = {
            i["kind"] for s in result["schema"]["sources"].values() for i in s["issues"]
        }
        self.assertEqual(kinds, {"missing_measure", "column_type"})
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "failed")

    def test_definitions_changing_during_queries_make_run_incomplete(self):
        self.api.changed = NEW
        result = self.run_parity(out=self.directory)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["errors"][0]["phase"], "definition_drift")
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "incomplete")

    def test_definition_failure_produces_incomplete_evidence_without_queries(self):
        self.api.definition_failure = LEFT
        result = self.run_parity(out=self.directory)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["queries_submitted"], 0)
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "incomplete")

    def test_budget_failure_happens_before_any_query(self):
        result = self.run_parity(out=self.directory, max_queries=7)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["queries_submitted"], 0)
        self.assertIn("budget exceeded", result["errors"][0]["error"])
        self.assertFalse(any(c[0] == "query" for c in self.api.calls))
        self.assertEqual(verify_evidence(self.directory)["parity_status"], "incomplete")

    def test_duplicate_identity_and_existing_folder_rejected_before_network(self):
        with self.assertRaisesRegex(MergeError, "distinct"):
            run_parity(self.api, (WS1, LEFT), (WS1, RIGHT), (WS1, LEFT))
        self.directory.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_parity(out=self.directory)
        self.assertEqual(self.api.calls, [])

    def test_custom_probes_override_default_and_support_target_query(self):
        probe = {
            "name": "left:measure:Sales:Quantity",
            "source": "left",
            "query": 'EVALUATE ROW("Value", 30)',
            "target_query": 'EVALUATE ROW("Value", 10 + 20)',
        }
        result = self.run_parity(probes=[probe], out=self.directory)
        self.assertEqual(result["planned_probes"], 4)
        queries = [c[-1] for c in self.api.calls if c[0] == "query"]
        self.assertIn(probe["query"], queries)
        self.assertIn(probe["target_query"], queries)
        self.assertEqual(verify_evidence(self.directory)["status"], "verified")

    def test_invalid_probes_admit_no_queries(self):
        good = {"name": "context", "source": "left", "query": "EVALUATE Sales"}
        for supplied in (
            [good, good],
            [{**good, "target_query": " "}],
            [{**good, "source": "third"}],
            [{**good, "name": "right:rows:Sales"}],
        ):
            with self.subTest(supplied=supplied):
                result = self.run_parity(probes=supplied)
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(result["queries_submitted"], 0)

    def test_unknown_tolerance_settings_are_rejected_instead_of_ignored(self):
        result = self.run_parity(
            probes=[
                {
                    "name": "context",
                    "source": "left",
                    "query": "EVALUATE Sales",
                    "absolute_tolerance": 100,
                }
            ]
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["queries_submitted"], 0)

    def test_malformed_and_near_limit_api_results_are_rejected(self):
        def body(rows):
            return {"results": [{"tables": [{"rows": rows}]}]}

        cases = [
            None,
            {"results": {}},
            body({}),
            body([None]),
            body([{}] * 100000),
            body([dict.fromkeys((str(n) for n in range(1000)), 0)] * 1000),
            body([{"large": "x" * 14000000}]),
        ]
        for value in cases:
            with self.subTest(kind=type(value)):
                with self.assertRaises(MergeError):
                    query_rows(value)

    def test_excess_diff_samples_are_bounded_without_losing_totals(self):
        result = compare_rows([{"x": n} for n in range(20)], [])
        self.assertEqual(result["missing_from_target"]["row_occurrences"], 20)
        self.assertEqual(len(result["missing_from_target"]["samples"]), 10)
        self.assertTrue(result["missing_from_target"]["samples_truncated"])

    def test_symlink_evidence_and_unhashed_extra_files_are_rejected(self):
        self.run_parity(out=self.directory)
        path = self.directory / "diffs/0001.json"
        original = path.read_bytes()
        path.unlink()
        alternative = Path(self.temp.name) / "outside.json"
        alternative.write_bytes(original)
        path.symlink_to(alternative)
        with self.assertRaisesRegex(MergeError, "Symlink"):
            verify_evidence(self.directory)
        path.unlink()
        path.write_bytes(original)
        (self.directory / "unhashed.txt").write_text("extra")
        with self.assertRaisesRegex(MergeError, "Unexpected/missing"):
            verify_evidence(self.directory)

    def test_default_pacing_has_positive_delay(self):
        with patch("fabric_merge.parity.time.sleep") as sleep:
            run_parity(self.api, (WS1, LEFT), (WS1, RIGHT), (WS1, NEW))
        self.assertEqual(sleep.call_count, 7)
        self.assertTrue(all(0 < call.args[0] <= 0.55 for call in sleep.call_args_list))

    def test_evidence_tampering_and_path_traversal_are_rejected(self):
        self.run_parity(out=self.directory)
        (self.directory / "results/0001-source.json").write_text("{}")
        with self.assertRaisesRegex(MergeError, "evidence changed"):
            verify_evidence(self.directory)
        manifest = load(self.directory / "manifest.json")
        manifest["files"] = {"../outside.json": {"sha256": "anything", "bytes": 0}}
        save(self.directory / "manifest.json", manifest)
        with self.assertRaisesRegex(MergeError, "Invalid evidence path"):
            verify_evidence(self.directory)

    def test_rehashed_but_inconsistent_diff_is_rejected_by_replay(self):
        self.run_parity(out=self.directory)
        relative = "diffs/0001.json"
        value = load(self.directory / relative)
        value["equal"] = False
        save(self.directory / relative, value)
        manifest = load(self.directory / "manifest.json")
        content = (self.directory / relative).read_bytes()
        manifest["files"][relative] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        save(self.directory / "manifest.json", manifest)
        with self.assertRaisesRegex(MergeError, "Diff replay"):
            verify_evidence(self.directory)

    def test_plan_cli_preserves_migration_journal_and_report_bindings(self):
        with service() as (api, state):
            directory = Path(self.temp.name) / "plan"
            create_plan(
                api,
                directory,
                (WS1, LEFT),
                (WS1, RIGHT),
                WS1,
                "Merged",
                security=SECURITY,
            )
            migration = Migration(api, directory)
            migration.stage()
            before_journal = migration.journal.path.read_bytes()
            before_reports = copy.deepcopy(state.reports)
            state.calls.clear()
            credential = Mock()
            credential.account.return_value = {"tenant_id": "synthetic-tenant"}
            with (
                patch("fabric_merge.cli.AzureCliCredential", return_value=credential),
                patch("fabric_merge.cli.Client", return_value=api.client),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                code = main(
                    [
                        "parity",
                        str(directory),
                        "--query-interval",
                        "0",
                        "--out",
                        str(self.directory),
                    ]
                )
            self.assertEqual(code, 0, output.getvalue())
            self.assertEqual(migration.journal.path.read_bytes(), before_journal)
            self.assertEqual(state.reports, before_reports)
            self.assertTrue(
                all(
                    c[0] == "GET"
                    or c[1].endswith(("/getDefinition", "/executeQueries"))
                    for c in state.calls
                )
            )
            self.assertEqual(verify_evidence(self.directory)["status"], "verified")

    def test_explicit_cli_exit_codes_and_local_verify_needs_no_auth(self):
        references = [
            "--left",
            WS1 + "/" + LEFT,
            "--right",
            WS1 + "/" + RIGHT,
            "--target",
            WS1 + "/" + NEW,
            "--query-interval",
            "0",
        ]
        with (
            patch("fabric_merge.cli.AzureCliCredential") as credential,
            patch("fabric_merge.cli.Fabric", return_value=self.api),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                main(["parity", *references, "--out", str(self.directory)]), 0
            )
            count = credential.call_count
            self.assertEqual(main(["verify-parity", str(self.directory)]), 0)
            self.assertEqual(credential.call_count, count)
            self.api.models[NEW]["bim"]["model"]["tables"][0]["columns"] = []
            self.assertEqual(main(["parity", *references]), 2)
            self.api.failure = NEW
            self.assertEqual(main(["parity", *references]), 1)
