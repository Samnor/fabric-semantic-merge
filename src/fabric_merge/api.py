"""Documented Fabric definition and Power BI report/refresh operations."""

import re
import time
import urllib.parse
import json

from .model import decode_definition
from .pacing import QueryPacer
from .transport import FABRIC, POWER_BI
from .util import MergeError, encoded, require, uid


def query_rows(response):
    """Reject incomplete/near-limit results before treating them as evidence."""
    require(
        isinstance(response, dict) and response.get("error") is None,
        "DAX query failed: " + str(response),
    )
    results = response.get("results")
    require(
        isinstance(results, list)
        and len(results) == 1
        and isinstance(results[0], dict)
        and results[0].get("error") is None,
        "DAX response incomplete or contains an error",
    )
    tables = results[0].get("tables")
    require(
        isinstance(tables, list)
        and len(tables) == 1
        and isinstance(tables[0], dict)
        and tables[0].get("error") is None
        and isinstance(tables[0].get("rows"), list),
        "DAX table missing/error/truncated",
    )
    rows = tables[0]["rows"]
    require(all(isinstance(row, dict) for row in rows), "Invalid DAX row objects")
    require(
        len(rows) < 100000,
        "Validation result reaches API row limit; use a bounded aggregate",
    )
    require(
        sum(len(row) for row in rows) < 1000000,
        "Validation result reaches API value limit",
    )
    require(
        len(
            json.dumps(
                response, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
        )
        < 14000000,
        "Validation result approaches API data limit; use a bounded aggregate",
    )
    return rows


class Fabric:
    def __init__(self, client, *, operation_timeout=1800, query_pacer=None):
        self.client = client
        self.operation_timeout = operation_timeout
        self.query_pacer = query_pacer or QueryPacer()

    def workspace(self, workspace):
        return self.client.request("GET", f"{FABRIC}/workspaces/{uid(workspace)}").body

    def group(self, workspace):
        return self.client.request("GET", f"{POWER_BI}/groups/{uid(workspace)}").body

    def dataset(self, workspace, model):
        return self.client.request(
            "GET", f"{POWER_BI}/groups/{uid(workspace)}/datasets/{uid(model)}"
        ).body

    def connection(self, connection):
        return self.client.request(
            "GET", f"{FABRIC}/connections/{uid(connection)}"
        ).body

    def workspaces(self):
        return self.client.values(FABRIC + "/workspaces")

    def models(self, workspace):
        return self.client.values(
            f"{FABRIC}/workspaces/{uid(workspace)}/semanticModels"
        )

    def definition(self, workspace, model):
        url = f"{FABRIC}/workspaces/{uid(workspace)}/semanticModels/{uid(model)}/getDefinition?format=TMSL"
        response = self.client.request("POST", url)
        return decode_definition(
            self.client.operation(response, timeout=self.operation_timeout)
        )

    def refresh_schedule(self, workspace, model):
        return self.client.request(
            "GET",
            f"{POWER_BI}/groups/{uid(workspace)}/datasets/{uid(model)}/refreshSchedule",
        ).body

    def source(self, workspace, model):
        workspace, model = uid(workspace), uid(model)
        return {
            "workspace_id": workspace,
            "id": model,
            "metadata": self.client.request(
                "GET", f"{FABRIC}/workspaces/{workspace}/semanticModels/{model}"
            ).body,
            "model": self.definition(workspace, model),
            "connections": sorted(
                self.client.values(
                    f"{FABRIC}/workspaces/{workspace}/items/{model}/connections"
                ),
                key=encoded,
            ),
            "permissions": sorted(
                self.client.values(
                    f"{POWER_BI}/groups/{workspace}/datasets/{model}/users"
                ),
                key=encoded,
            ),
        }

    def report_url(self, report):
        prefix = (
            POWER_BI
            if report.get("my_workspace")
            else f"{POWER_BI}/groups/{uid(report['workspace_id'])}"
        )
        return prefix + "/reports/" + uid(report["id"])

    def report(self, report):
        return self.client.request("GET", self.report_url(report)).body

    def reports(self, model_ids, *, scope="tenant", workspace_ids=None):
        model_ids = {uid(x) for x in model_ids}
        records, coverage = [], {}
        if scope == "tenant":
            # Admin collection supports explicit top/skip. Never silently fall back on 403.
            page_size, offset = 5000, 0
            next_page = None
            seen = set()
            while True:
                query = urllib.parse.urlencode({"$top": page_size, "$skip": offset})
                url = next_page or f"{POWER_BI}/admin/reports?{query}"
                require(url not in seen, "Admin report pagination loop")
                seen.add(url)
                self.client.validate_url(url)
                require(
                    url.startswith(POWER_BI + "/admin/reports?"),
                    "Unexpected admin report continuation URL",
                )
                page = self.client.request("GET", url).body
                require(
                    isinstance(page, dict) and isinstance(page.get("value"), list),
                    "Invalid admin report inventory",
                )
                for report in page["value"]:
                    if report.get("datasetId", "").lower() in model_ids:
                        records.append(
                            {
                                **report,
                                "workspace_id": report.get("workspaceId"),
                                "my_workspace": False,
                            }
                        )
                next_page = page.get("@odata.nextLink") or page.get("odata.nextLink")
                if not next_page and len(page["value"]) < page_size:
                    break
                offset += page_size
                require(offset < 5_000_000, "Tenant inventory limit exceeded")
            coverage = {
                "scope": "tenant",
                "complete_for_scope": True,
                "workspaces": "tenant admin report inventory",
            }
        else:
            require(
                scope in ("accessible", "workspaces"), "Unknown report discovery scope"
            )
            workspaces = (
                [w["id"] for w in self.workspaces() if w.get("type") != "Personal"]
                if scope == "accessible"
                else list(workspace_ids or [])
            )
            require(bool(workspaces), "No report workspaces supplied/discovered")
            for workspace in sorted(set(workspaces)):
                for report in self.client.values(
                    f"{POWER_BI}/groups/{uid(workspace)}/reports"
                ):
                    if report.get("datasetId", "").lower() in model_ids:
                        records.append(
                            {**report, "workspace_id": workspace, "my_workspace": False}
                        )
            if scope == "accessible":
                for report in self.client.values(POWER_BI + "/reports"):
                    if report.get("datasetId", "").lower() in model_ids:
                        records.append(
                            {**report, "workspace_id": None, "my_workspace": True}
                        )
            coverage = {
                "scope": scope,
                "complete_for_scope": True,
                "tenant_complete": False,
                "workspaces": sorted(set(workspaces)),
            }
        result, seen = [], set()
        for report in records:
            require(
                report.get("my_workspace") or report.get("workspace_id"),
                "Related report lacks workspace identity; cannot claim complete migration",
            )
            identity = (report.get("workspace_id"), uid(report["id"]))
            require(identity not in seen, "Duplicate report identity in inventory")
            seen.add(identity)
            actual = self.report(report)
            require(
                actual.get("datasetId", "").lower() in model_ids,
                "Report binding changed during discovery",
            )
            result.append(
                {
                    "id": uid(actual["id"]),
                    "name": actual.get("name", report.get("name", "")),
                    "workspace_id": report.get("workspace_id"),
                    "my_workspace": report["my_workspace"],
                    "dataset_id": uid(actual["datasetId"]),
                    "report_type": actual.get("reportType"),
                    "web_url": actual.get("webUrl"),
                }
            )
        return sorted(
            result, key=lambda r: (r.get("workspace_id") or "", r["id"])
        ), coverage

    def query_response(self, workspace, model, query):
        body = {
            "queries": [{"query": query}],
            "serializerSettings": {"includeNulls": True},
        }
        url = f"{POWER_BI}/groups/{uid(workspace)}/datasets/{uid(model)}/executeQueries"
        self.query_pacer.wait()
        return self.client.request(
            "POST",
            url,
            body,
        )

    def rows(self, workspace, model, query):
        return query_rows(self.query_response(workspace, model, query).body)

    def refresh_history(self, workspace, model):
        body = self.client.request(
            "GET",
            f"{POWER_BI}/groups/{uid(workspace)}/datasets/{uid(model)}/refreshes?$top=60",
        ).body
        require(
            isinstance(body, dict)
            and isinstance(body.get("value"), list)
            and len(body["value"]) <= 60,
            "Invalid bounded refresh history",
        )
        entries = body["value"]
        require(
            all(isinstance(e, dict) and e.get("requestId") for e in entries),
            "Refresh history lacks stable request identities",
        )
        identifiers = [uid(e["requestId"]) for e in entries]
        require(
            len(set(identifiers)) == len(identifiers),
            "Refresh history has duplicate request identities",
        )
        return entries

    def wait_refresh(self, workspace, model, response, *, history_before=None):
        location = response.headers.get("location", "")
        refresh_id = response.headers.get("x-ms-request-id")
        # Regional Location is used only to extract the exact refresh identity;
        # credentials are sent to the canonical documented endpoint.
        match = re.search(r"/refreshes/([0-9a-fA-F-]{36})(?:\?|$)", location)
        if match:
            refresh_id = match.group(1)
        require(
            refresh_id or history_before,
            "Accepted refresh has no exact request ID; reconcile from saved response, do not refresh again",
        )
        by_history = not refresh_id
        previous = set()
        if by_history:
            require(
                history_before["workspace_id"] == uid(workspace)
                and history_before["model_id"] == uid(model),
                "Refresh before-image belongs to another model",
            )
            previous = {uid(e["requestId"]) for e in history_before["entries"]}
        deadline = time.monotonic() + self.operation_timeout
        while time.monotonic() < deadline:
            if by_history:
                entries = self.refresh_history(workspace, model)
                candidates = [
                    e
                    for e in entries
                    if uid(e["requestId"]) not in previous
                    and e.get("refreshType") == "ViaApi"
                ]
                require(
                    len(candidates) <= 1,
                    "Ambiguous new standard refresh history; reconcile without another POST",
                )
                if candidates:
                    observed_id = uid(candidates[0]["requestId"])
                    require(
                        refresh_id is None or refresh_id == observed_id,
                        "Tracked standard refresh identity changed",
                    )
                    refresh_id, result = observed_id, candidates[0]
                else:
                    require(
                        refresh_id is None,
                        "Tracked standard refresh disappeared from bounded history",
                    )
                    time.sleep(min(2, max(0, deadline - time.monotonic())))
                    continue
            else:
                url = f"{POWER_BI}/groups/{uid(workspace)}/datasets/{uid(model)}/refreshes/{uid(refresh_id)}"
                result = self.client.request("GET", url).body
            require(isinstance(result, dict), "Refresh observation is not an object")
            if result.get("requestId") is not None:
                require(
                    uid(result["requestId"]) == uid(refresh_id),
                    "Refresh observation returned another request identity",
                )
            status = result.get("status")
            if status == "Completed":
                receipt = {"request_id": refresh_id, "result": result}
                if by_history:
                    from .util import digest

                    receipt.update(
                        tracking="unique ViaApi history delta under exclusive target control",
                        before_sha256=digest(history_before),
                    )
                return receipt
            require(
                status not in ("Failed", "Cancelled", "Canceled", "Disabled"),
                "Merged model refresh failed: " + str(result),
            )
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        raise MergeError(
            "Refresh still running; resume the same saved refresh, never submit another"
        )
