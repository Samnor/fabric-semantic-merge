"""Stateful loopback API server; no Microsoft call or credential is used."""

from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from threading import Thread
import urllib.parse
from unittest.mock import patch

from fabric_merge import api, transport, workflow
from fabric_merge.model import decode_definition, encode_definition
from fabric_merge.pacing import QueryPacer
from fabric_merge.util import MergeError

WS1 = "10000000-0000-0000-0000-000000000001"
WS2 = "10000000-0000-0000-0000-000000000002"
LEFT = "20000000-0000-0000-0000-000000000001"
RIGHT = "20000000-0000-0000-0000-000000000002"
NEW = "20000000-0000-0000-0000-000000000003"
REFRESH = "30000000-0000-0000-0000-000000000001"
OP = "40000000-0000-0000-0000-000000000001"
REPORTS = [f"50000000-0000-0000-0000-{n:012d}" for n in (1, 2, 3)]
SECURITY = {
    "reviewed": True,
    "audience_union_approved": True,
    "label_id": None,
    "permissions": [],
}


def model(measure="Quantity", expression="SUM('Sales'[Value])"):
    return {
        "bim": {
            "name": measure + " model",
            "compatibilityLevel": 1600,
            "model": {
                "culture": "en-US",
                "defaultPowerBIDataSourceVersion": "powerBI_V3",
                "tables": [
                    {
                        "name": "Sales",
                        "columns": [
                            {
                                "name": "Value",
                                "type": "calculatedTableColumn",
                                "dataType": "int64",
                                "sourceColumn": "[Value]",
                            }
                        ],
                        "partitions": [
                            {
                                "name": "Sales",
                                "mode": "import",
                                "source": {
                                    "type": "calculated",
                                    "expression": 'DATATABLE("Value", INTEGER, {{10}, {20}})',
                                },
                            }
                        ],
                        "measures": [{"name": measure, "expression": expression}],
                    }
                ],
            },
        },
        "pbism": {"version": "4.0", "settings": {}},
        "extras": {},
    }


class State:
    def __init__(self):
        self.models = {
            LEFT: {
                "id": LEFT,
                "displayName": "Quantity",
                "description": "source",
                "workspaceId": WS1,
                "model": model(),
            },
            RIGHT: {
                "id": RIGHT,
                "displayName": "Count",
                "description": "source",
                "workspaceId": WS1,
                "model": model("Count", "COUNTROWS('Sales')"),
            },
        }
        self.reports = {
            REPORTS[0]: {
                "id": REPORTS[0],
                "name": "Quantity report",
                "datasetId": LEFT,
                "workspaceId": WS1,
                "reportType": "PowerBIReport",
            },
            REPORTS[1]: {
                "id": REPORTS[1],
                "name": "Cross-workspace report",
                "datasetId": LEFT,
                "workspaceId": WS2,
                "reportType": "PowerBIReport",
            },
            REPORTS[2]: {
                "id": REPORTS[2],
                "name": "Count report",
                "datasetId": RIGHT,
                "workspaceId": WS1,
                "reportType": "PowerBIReport",
            },
        }
        self.calls = []
        self.connections = {}
        self.permissions = {}
        self.async_create = True
        self.operation_polls = 0
        self.mismatch = False
        self.fail_report = None
        self.drop_response = None
        self.get_definition_async = False
        self.dedicated = True
        self.group_read_only = False
        self.preflight_errors = {}
        self.refresh_history_entries = []
        self.headerless_standard = False
        self.schedules = {
            model_id: {
                "enabled": True,
                "days": ["Monday"],
                "times": ["08:00"],
                "localTimeZoneId": "UTC",
            }
            for model_id in (LEFT, RIGHT)
        }
        self.schedule_errors = {}


class Credential:
    identity_type = "user"

    def token(self, audience):
        return "loopback-test-only"


class TestClient(transport.Client):
    def validate_url(self, url):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "http" and parsed.hostname == "127.0.0.1":
            return "loopback"
        raise MergeError("Test client permits loopback HTTP only")


@contextmanager
def service(state=None):
    state = state or State()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body=None, **headers):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for name, value in headers.items():
                self.send_header(name.replace("_", "-"), str(value))
            self.end_headers()
            if body is not None:
                self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def dispatch(self, method):
            path = urllib.parse.urlsplit(self.path).path
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length)) if length else None
            state.calls.append((method, path, body))
            if self.headers.get("Authorization") != "Bearer loopback-test-only":
                return self.reply(401, {"error": "test token required"})
            if path in state.preflight_errors:
                return self.reply(
                    state.preflight_errors[path], {"error": "metadata unavailable"}
                )
            if path == "/fabric/workspaces":
                return self.reply(
                    200,
                    {
                        "value": [
                            {"id": WS1, "displayName": "Models"},
                            {"id": WS2, "displayName": "Reports"},
                        ]
                    },
                )
            if method == "GET" and path in (
                f"/fabric/workspaces/{WS1}",
                f"/fabric/workspaces/{WS2}",
            ):
                return self.reply(
                    200, {"id": path.rsplit("/", 1)[-1], "type": "Workspace"}
                )
            if method == "GET" and path in (f"/pbi/groups/{WS1}", f"/pbi/groups/{WS2}"):
                return self.reply(
                    200,
                    {
                        "id": path.rsplit("/", 1)[-1],
                        "isReadOnly": state.group_read_only,
                        "isOnDedicatedCapacity": state.dedicated,
                    },
                )
            if method == "GET" and path.startswith("/fabric/connections/"):
                connection_id = path.rsplit("/", 1)[-1]
                found = next(
                    (
                        c
                        for connections in state.connections.values()
                        for c in connections
                        if c.get("id") == connection_id
                    ),
                    None,
                )
                return self.reply(
                    200 if found else 404, found or {"error": "not found"}
                )
            if method == "GET" and "/datasets/" in path and len(path.split("/")) == 6:
                model_id = path.rsplit("/", 1)[-1]
                return self.reply(200, {"id": model_id, "isRefreshable": True})
            if path.endswith("/semanticModels"):
                workspace = path.split("/")[-2]
                if method == "GET":
                    return self.reply(
                        200,
                        {
                            "value": [
                                {k: v for k, v in m.items() if k != "model"}
                                for m in state.models.values()
                                if m["workspaceId"] == workspace
                            ]
                        },
                    )
                state.models[NEW] = {
                    "id": NEW,
                    "displayName": body["displayName"],
                    "description": body["description"],
                    "workspaceId": workspace,
                    "model": decode_definition(body["definition"]),
                }
                if state.drop_response == "create":
                    state.drop_response = None
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return self.connection.close()
                if state.async_create:
                    return self.reply(202, x_ms_operation_id=OP, Retry_After=0)
                return self.reply(
                    201, {k: v for k, v in state.models[NEW].items() if k != "model"}
                )
            if path.startswith("/fabric/operations/"):
                if path.endswith("/result"):
                    return self.reply(
                        200,
                        {k: v for k, v in state.models[NEW].items() if k != "model"},
                    )
                state.operation_polls += 1
                return self.reply(
                    200,
                    {
                        "status": "Running"
                        if state.operation_polls == 1
                        else "Succeeded"
                    },
                    Retry_After=0,
                )
            if path.endswith("/getDefinition"):
                model_id = path.split("/")[-2]
                return self.reply(
                    200,
                    {"definition": encode_definition(state.models[model_id]["model"])},
                )
            if "/semanticModels/" in path and method == "GET":
                model_id = path.rsplit("/", 1)[-1]
                if model_id in state.models:
                    return self.reply(
                        200,
                        {
                            k: v
                            for k, v in state.models[model_id].items()
                            if k != "model"
                        },
                    )
            if path.endswith("/connections"):
                return self.reply(
                    200, {"value": state.connections.get(path.split("/")[-2], [])}
                )
            if path.endswith("/bindConnection"):
                model_id = path.split("/")[-2]
                state.connections.setdefault(model_id, []).append(
                    body["connectionBinding"]
                )
                return self.reply(200)
            if path.endswith("/admin/reports"):
                return self.reply(200, {"value": list(state.reports.values())})
            if path.endswith("/reports"):
                workspace = path.split("/")[-2] if "/groups/" in path else None
                return self.reply(
                    200,
                    {
                        "value": [
                            r
                            for r in state.reports.values()
                            if r.get("workspaceId") == workspace
                        ]
                    },
                )
            if path.endswith("/users"):
                model_id = path.split("/")[-2]
                if method == "GET":
                    return self.reply(
                        200, {"value": state.permissions.get(model_id, [])}
                    )
                state.permissions.setdefault(model_id, []).append(body)
                return self.reply(200)
            if path.endswith("/refreshSchedule") and method == "GET":
                model_id = path.split("/")[-2]
                if model_id in state.schedule_errors:
                    return self.reply(
                        state.schedule_errors[model_id],
                        {"error": "schedule unavailable"},
                    )
                return self.reply(200, state.schedules[model_id])
            if path.endswith("/refreshes") and method == "GET":
                return self.reply(200, {"value": state.refresh_history_entries})
            if path.endswith("/refreshes") and method == "POST":
                if state.dedicated is False and set(body or {}) - {"notifyOption"}:
                    return self.reply(
                        400,
                        {"error": "enhanced refresh unsupported on shared capacity"},
                    )
                if state.headerless_standard and "type" not in (body or {}):
                    state.refresh_history_entries.append(
                        {
                            "requestId": REFRESH,
                            "status": "Completed",
                            "refreshType": "ViaApi",
                        }
                    )
                    return self.reply(202)
                return self.reply(
                    202,
                    Location=f"https://wabi-test.analysis.windows.net/groups/{WS1}/datasets/{NEW}/refreshes/{REFRESH}",
                )
            if "/refreshes/" in path:
                return self.reply(200, {"status": "Completed", "requestId": REFRESH})
            if path.endswith("/executeQueries"):
                model_id = path.split("/")[-2]
                query = body["queries"][0]["query"]
                value = 2 if "COUNTROWS" in query or "[Count]" in query else 30
                if model_id == NEW and state.mismatch:
                    value = 999
                return self.reply(
                    200, {"results": [{"tables": [{"rows": [{"[Value]": value}]}]}]}
                )
            if "/reports/" in path:
                report_id = (
                    path.split("/")[-2]
                    if path.endswith("/Rebind")
                    else path.rsplit("/", 1)[-1]
                )
                if report_id not in state.reports:
                    return self.reply(404)
                report = state.reports[report_id]
                if method == "GET":
                    return self.reply(200, report)
                if report_id == state.fail_report:
                    return self.reply(403, {"error": "no report write permission"})
                report["datasetId"] = body["datasetId"]
                if state.drop_response == report_id:
                    state.drop_response = None
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return self.connection.close()
                return self.reply(200)
            return self.reply(404, {"error": path})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    with ExitStack() as stack:
        for module in (api, workflow, transport):
            stack.enter_context(patch.object(module, "FABRIC", origin + "/fabric"))
        for module in (api, workflow, transport):
            stack.enter_context(patch.object(module, "POWER_BI", origin + "/pbi"))
        client = TestClient(Credential(), timeout=2, read_retries=0)
        # Advance a deterministic query-only clock; keep production pacing active
        # without slowing every loopback test in real time.
        virtual_time = [0.0]

        def advance(seconds):
            virtual_time[0] += seconds

        pacer = QueryPacer(clock=lambda: virtual_time[0], sleep=advance)
        instance = api.Fabric(client, operation_timeout=4, query_pacer=pacer)
        try:
            yield instance, state
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
