import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fabric_merge.transport import Response
from fabric_merge.util import MergeError
from fabric_merge.workflow import Migration, create_plan
from .support import LEFT, NEW, RIGHT, SECURITY, WS1, WS2, service


class ApiTests(unittest.TestCase):
    def test_admin_inventory_follows_explicit_next_link(self):
        with service() as (api, state):
            original = api.client.request
            records = list(state.reports.values())
            url = (
                api.report_url({"workspace_id": WS1, "id": records[0]["id"]}).split(
                    "/groups/"
                )[0]
                + "/admin/reports?$skip=2"
            )
            pages = [
                Response(200, {}, {"value": records[:2], "@odata.nextLink": url}),
                Response(200, {}, {"value": records[2:]}),
            ]

            def request(method, endpoint, body=None):
                return (
                    pages.pop(0)
                    if "/admin/reports?" in endpoint
                    else original(method, endpoint, body)
                )

            with patch.object(api.client, "request", side_effect=request):
                reports, coverage = api.reports([LEFT, RIGHT])
            self.assertEqual(len(reports), 3)
            self.assertEqual(pages, [])
            self.assertTrue(coverage["complete_for_scope"])

    def test_connection_and_access_grants_are_read_back_before_rebind(self):
        with service() as (api, state), tempfile.TemporaryDirectory() as tmp:
            binding = {
                "id": "60000000-0000-0000-0000-000000000001",
                "connectivityType": "ShareableCloud",
                "connectionDetails": {
                    "type": "Web",
                    "path": "https://example.test/source.xlsx",
                },
            }
            for model_id in (LEFT, RIGHT):
                state.connections[model_id] = [copy.deepcopy(binding)]
            permission = {
                "identifier": "readers@example.test",
                "principalType": "User",
                "datasetUserAccessRight": "ReadExplore",
            }
            policy = {**SECURITY, "permissions": [permission]}
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
            Migration(api, directory).apply()
            self.assertEqual(state.connections[NEW], [binding])
            self.assertEqual(state.permissions[NEW], [permission])
            rebind_index = next(
                i for i, call in enumerate(state.calls) if call[1].endswith("/Rebind")
            )
            self.assertTrue(
                any(
                    call[0] == "GET" and call[1].endswith(NEW + "/users")
                    for call in state.calls[:rebind_index]
                )
            )

    def test_accessible_scope_includes_my_workspace_reports(self):
        with service() as (api, state):
            report_id = "50000000-0000-0000-0000-000000000010"
            state.reports[report_id] = {
                "id": report_id,
                "name": "Personal thin report",
                "datasetId": LEFT,
                "reportType": "PowerBIReport",
            }
            reports, coverage = api.reports([LEFT, RIGHT], scope="accessible")
            self.assertEqual(len(reports), 4)
            self.assertTrue(
                next(r for r in reports if r["id"] == report_id)["my_workspace"]
            )
            self.assertFalse(coverage["tenant_complete"])

    def test_explicit_scope_never_claims_tenant_completeness(self):
        with service() as (api, state):
            reports, coverage = api.reports(
                [LEFT, RIGHT], scope="workspaces", workspace_ids=[WS2]
            )
            self.assertEqual(len(reports), 1)
            self.assertFalse(coverage["tenant_complete"])

    def test_http_200_partial_dax_result_is_rejected(self):
        with service() as (api, state):
            malformed = {
                "results": [
                    {
                        "error": {"code": "More than allowed rows"},
                        "tables": [{"rows": []}],
                    }
                ]
            }
            with patch.object(
                api.client, "request", return_value=Response(200, {}, malformed)
            ):
                with self.assertRaisesRegex(MergeError, "incomplete"):
                    api.rows(WS1, LEFT, "EVALUATE Sales")

    def test_lro_polling_is_read_only_and_bounded(self):
        with service() as (api, state):
            response = Response(
                202,
                {
                    "x-ms-operation-id": "40000000-0000-0000-0000-000000000009",
                    "retry-after": "0",
                },
                None,
            )
            with patch.object(
                api.client,
                "request",
                return_value=Response(200, {"retry-after": "0"}, {"status": "Running"}),
            ) as requests:
                with self.assertRaisesRegex(MergeError, "still running"):
                    api.client.operation(response, timeout=0.002)
                self.assertTrue(
                    all(call.args[0] == "GET" for call in requests.call_args_list)
                )


if __name__ == "__main__":
    unittest.main()
