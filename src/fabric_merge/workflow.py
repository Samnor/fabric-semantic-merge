"""Reviewable plans, staged model creation, validation, cutover and rollback."""

import copy
from pathlib import Path
import uuid

from .journal import Journal
from .model import default_probes, merge_models
from .preflight import REFRESH_MODES, inspect_preflight
from .schedules import (
    observe_schedules,
    recommend_schedules,
    render_suggestion,
    write_report,
)
from .transport import FABRIC, POWER_BI
from .util import digest, encoded, load, now, require, save, uid


def connection_key(connection):
    details = connection.get("connectionDetails", {})
    return str(details.get("type", "")) + ":" + str(details.get("path", ""))


def connections(sources, overrides=None):
    result, conflicts = {}, []
    for source in sources.values():
        for item in source["connections"]:
            key = connection_key(item)
            value = {
                k: copy.deepcopy(item[k])
                for k in ("id", "connectivityType", "connectionDetails")
                if k in item
            }
            if key in result and result[key] != value:
                conflicts.append({"key": key, "left": result[key], "right": value})
            else:
                result[key] = value
    for key, value in (overrides or {}).items():
        require(
            key in result,
            "Connection override does not match an exported reference: " + key,
        )
        require(
            connection_key(value) == key,
            "Connection override changed the source reference",
        )
        result[key] = value
    conflicts = [x for x in conflicts if x["key"] not in (overrides or {})]
    for key, value in result.items():
        require(
            value.get("connectivityType")
            in (
                "ShareableCloud",
                "PersonalCloud",
                "OnPremisesGateway",
                "OnPremisesGatewayPersonal",
                "VirtualNetworkGateway",
                "StreamingVirtualNetworkGateway",
                "Automatic",
            ),
            "Unsupported/unbound connection " + key,
        )
        if value["connectivityType"] != "Automatic":
            require(value.get("id"), "Connection lacks reusable ID: " + key)
    return list(result.values()), conflicts


def check_security(policy, sources, merged):
    issues = []
    if not policy or policy.get("reviewed") is not True:
        return [
            "Provide a reviewed security policy; source ACLs/role membership are not safely unioned automatically"
        ]
    if not policy.get("audience_union_approved"):
        issues.append(
            "Review and approve the combined data audience in the security policy"
        )
    if (
        merged["summary"]["roles"]
        and policy.get("roles_and_membership_plan_reviewed") is not True
    ):
        issues.append(
            "Review the RLS/OLS definitions and membership plan before staging the model"
        )
    require(
        "label_id" in policy,
        "Security policy must explicitly specify label_id (UUID or null)",
    )
    if policy["label_id"] is not None:
        uid(policy["label_id"])
    for permission in policy.get("permissions", []):
        require(
            permission.get("principalType") in ("User", "Group"),
            "Dataset permission API supports User/Group grants, not App grants",
        )
        require(
            permission.get("datasetUserAccessRight")
            in ("Read", "ReadExplore", "ReadReshare", "ReadReshareExplore"),
            "Write access must be managed through workspace roles; do not silently downgrade it",
        )
        require(
            bool(permission.get("identifier")),
            "Permission needs a principal identifier",
        )
    source_labels = set()
    for source in sources.values():
        label = (source["metadata"].get("sensitivityLabel") or {}).get("id")
        source_labels.add(uid(label) if label else None)
    target_label = uid(policy["label_id"]) if policy["label_id"] else None
    if (
        any(label and label != target_label for label in source_labels)
        and policy.get("label_change_approved") is not True
    ):
        issues.append(
            "Target label differs from source labels; explicit label_change_approved is required"
        )
    return issues


def create_plan(
    api,
    directory,
    left,
    right,
    target_workspace,
    display_name,
    *,
    scope="tenant",
    report_workspaces=None,
    resolutions=None,
    security=None,
    probes=None,
    connection_overrides=None,
    account=None,
    refresh_capacity="unknown",
    refresh_mode="auto",
):
    require(
        refresh_mode in REFRESH_MODES, "Refresh mode must be auto, standard or enhanced"
    )
    directory = Path(directory)
    require(not directory.exists(), "Choose a new plan directory")
    directory.mkdir(parents=True, mode=0o700)
    require(uid(left[1]) != uid(right[1]), "Select two distinct semantic models")
    require(
        bool(display_name.strip()) and len(display_name) <= 256,
        "New model name must be 1–256 characters",
    )
    sources = {"left": api.source(*left), "right": api.source(*right)}
    merged = merge_models(
        sources["left"]["model"], sources["right"]["model"], display_name, resolutions
    )
    reports, coverage = api.reports(
        [s["id"] for s in sources.values()],
        scope=scope,
        workspace_ids=report_workspaces,
    )
    bindings, binding_conflicts = connections(sources, connection_overrides)
    generated = [
        p
        for name, source in sources.items()
        for p in default_probes(source["model"], name)
    ]
    if probes:
        require(isinstance(probes, list), "Probes must be a list")
        provided_names = set()
        by_name = {p["name"]: index for index, p in enumerate(generated)}
        for probe in probes:
            require(
                probe.get("name") not in provided_names, "Duplicate supplied probe name"
            )
            provided_names.add(probe.get("name"))
            if probe.get("name") in by_name:
                index = by_name[probe["name"]]
                require(
                    probe.get("source") == generated[index]["source"],
                    "A probe override cannot change its source model",
                )
                generated[index] = copy.deepcopy(probe)
            else:
                generated.append(copy.deepcopy(probe))
    require(generated, "No validation queries; provide explicit probes")
    require(
        len({p["name"] for p in generated}) == len(generated),
        "Duplicate validation probe name",
    )
    for probe in generated:
        require(
            probe.get("source") in sources and isinstance(probe.get("query"), str),
            "Probe requires source=left/right and DAX query",
        )
    blockers = list(merged["issues"])
    if merged["conflicts"]:
        blockers.append("Resolve model-definition conflicts in merge-preview.json")
    if binding_conflicts:
        blockers.append("Resolve connection IDs in connection-conflicts.json")
    unsupported = [r for r in reports if r["report_type"] != "PowerBIReport"]
    if unsupported:
        blockers.append(
            "Unsupported/unknown report types; paginated reports cannot be rebound by this API"
        )
    blockers.extend(check_security(security, sources, merged))
    run_id = str(uuid.uuid4())
    schedule_snapshot = observe_schedules(api, left, right)
    schedule_suggestion = recommend_schedules(schedule_snapshot, refresh_capacity)
    artifacts = {
        "sources.json": sources,
        "merge-preview.json": merged,
        "merged-model.json": merged["model"],
        "merged-definition.json": merged["definition"],
        "reports.json": reports,
        "probes.json": generated,
        "connection-conflicts.json": binding_conflicts,
        "refresh-schedules.json": schedule_snapshot,
        "refresh-schedule-suggestion.json": schedule_suggestion,
    }
    for name, value in artifacts.items():
        save(directory / name, value, exclusive=True)
    plan = {
        "schema_version": 1,
        "run_id": run_id,
        "created_utc": now(),
        "account": account,
        "target_workspace": uid(target_workspace),
        "display_name": display_name,
        "ownership_marker": "fabric-semantic-merge:" + run_id,
        "sources": {
            k: {
                "workspace_id": v["workspace_id"],
                "id": v["id"],
                "model_sha256": digest(v["model"]),
                "connections_sha256": digest(v["connections"]),
                "permissions_sha256": digest(v["permissions"]),
                "label_sha256": digest(v["metadata"].get("sensitivityLabel")),
            }
            for k, v in sources.items()
        },
        "report_scope": scope,
        "report_workspaces": report_workspaces or [],
        "coverage": coverage,
        "connections": bindings,
        "security": security,
        "blockers": blockers,
        "ready": not blockers,
        "artifact_hashes": {k: digest(v) for k, v in artifacts.items()},
        "validation": {
            "probe_count": len(generated),
            "stage_query_count": 2 * len(generated),
            "fresh_cutover_query_count": 2 * len(generated),
            "initial_apply_query_count": 4 * len(generated),
            "default_probes": "row counts and every ordinary measure in each source, unfiltered; add report-specific context queries",
            "limits": "Passing sampled DAX probes is not proof that every visual/security context is equivalent",
        },
        "refresh_schedule": {
            "status": schedule_suggestion["status"],
            "informational_only": True,
            "report": "SCHEDULE.md",
        },
        "deployment": {"refresh_mode": refresh_mode, "adapter": "import-v1"},
    }
    save(directory / "plan.json", plan, exclusive=True)
    write_report(
        directory / "SCHEDULE.md",
        render_suggestion(schedule_suggestion, "refresh-schedule-suggestion.json"),
    )
    lines = [
        "# Semantic model merge plan",
        "",
        f"New model: **{display_name}**",
        "",
        f"Report scope: **{scope}**; directly related reports: **{len(reports)}**.",
        "",
        f"Merged tables: {merged['summary']['tables']}; measures: {merged['summary']['measures']}; validation probes: {len(generated)}.",
        "",
        "Status: "
        + ("Ready for staged creation and validation" if not blockers else "Blocked"),
        "",
    ]
    lines += ["- " + b for b in blockers]
    lines += ["", "| Report | Workspace | Original model |", "| --- | --- | --- |"]
    lines += [
        f"| {r['name'].replace('|', ' / ')} | {r.get('workspace_id') or 'My workspace'} | {r['dataset_id']} |"
        for r in reports
    ]
    lines += [
        "",
        "Sources and their refresh schedules remain unchanged. Creating the merged model alone does not retire duplicate refreshes.",
        "Refresh schedule suggestion: "
        + schedule_suggestion["status"]
        + ". Review SCHEDULE.md and refresh-schedule-suggestion.json; this informational proposal is never applied by stage/apply.",
        "Deployment refresh mode: "
        + refresh_mode
        + ". Stage runs fresh metadata preflight before DAX or cloud mutation; inspect with preflight first.",
        "",
    ]
    (directory / "PLAN.md").write_text("\n".join(lines))
    return plan


def open_plan(directory):
    directory = Path(directory)
    plan = load(directory / "plan.json")
    require(plan.get("schema_version") == 1, "Unsupported plan version")
    for name, expected in plan["artifact_hashes"].items():
        require(Path(name).name == name, "Invalid artifact path")
        require(
            digest(load(directory / name)) == expected, "Plan artifact changed: " + name
        )
    return directory, plan


def normalized_rows(rows):
    return sorted(encoded(row) for row in rows)


class Migration:
    def __init__(self, api, directory):
        self.api = api
        self.directory, self.plan = open_plan(directory)
        self.journal = Journal(self.directory)

    def _marker(self, kind, body):
        self.journal.append(kind, body=body)
        return body

    def _body(self, kind):
        value = self.journal.latest(kind)
        return value["body"] if value else None

    def _same_sources(self):
        for source in self.plan["sources"].values():
            actual = self.api.source(source["workspace_id"], source["id"])
            for key, expected in (
                ("model", "model_sha256"),
                ("connections", "connections_sha256"),
                ("permissions", "permissions_sha256"),
            ):
                require(
                    digest(actual[key]) == source[expected],
                    "Source definition/connections/access changed; create a fresh reviewed plan",
                )
            require(
                digest(actual["metadata"].get("sensitivityLabel"))
                == source["label_sha256"],
                "Source sensitivity label changed; review a fresh plan",
            )

    def _inventory(self, *, migrated=False):
        sources = [s["id"] for s in self.plan["sources"].values()]
        if migrated and self._body("created"):
            sources.append(self._body("created")["id"])
        actual, _ = self.api.reports(
            sources,
            scope=self.plan["report_scope"],
            workspace_ids=self.plan["report_workspaces"],
        )
        original = load(self.directory / "reports.json")
        expected = {(r.get("workspace_id"), r["id"]) for r in original}
        found = {(r.get("workspace_id"), r["id"]) for r in actual}
        require(
            expected == found,
            "Related-report inventory changed; cutover cannot silently omit/add reports",
        )
        return actual

    def _mutation(self, key, url, body):
        return self.journal.mutate(self.api.client, key, "POST", url, body)

    def _check_plan(self):
        prior = self.journal.latest("plan")
        if prior:
            require(
                prior["sha256"] == digest(self.plan),
                "Plan changed after migration started",
            )

    def _review_plan(self):
        """Reassess old ready plans locally; never rewrite their pinned artifacts."""
        sources = load(self.directory / "sources.json")
        preview = load(self.directory / "merge-preview.json")
        resolutions = {
            item["path"]: item["resolution"]
            for item in preview.get("decisions", [])
            if isinstance(item.get("resolution"), dict)
        }
        reviewed = merge_models(
            sources["left"]["model"],
            sources["right"]["model"],
            self.plan["display_name"],
            resolutions,
        )
        require(
            reviewed["ready"],
            "Plan fails current merge safeguards; create a fresh reviewed plan: "
            + str([c["path"] for c in reviewed["conflicts"]] + reviewed["issues"]),
        )
        require(
            reviewed["model"] == load(self.directory / "merged-model.json"),
            "Current merge differs from the pinned model; create a fresh reviewed plan",
        )
        issues = check_security(self.plan["security"], sources, reviewed)
        require(
            not issues, "Current security review blocks staging: " + "; ".join(issues)
        )

    def _rollback_begun(self):
        # Include pre-fence journals written by earlier versions of the CLI.
        return any(
            e["kind"] in ("rollback_started", "rolled_back")
            or (e["kind"] == "intent" and e["key"].startswith("rollback-"))
            for e in self.journal.events()
        )

    def _unresolved(self):
        return [
            e["key"]
            for e in self.journal.events()
            if e["kind"] == "intent" and self.journal.outcome(e["key"]) == "unknown"
        ]

    def _confirmed_rebind(self, key):
        require(
            self.journal.latest("intent", key),
            "Report was rebound by another actor; inspect before continuing",
        )
        require(
            self.journal.outcome(key) != "unknown",
            "Unresolved rebind; run reconcile before continuing: " + key,
        )
        require(
            self.journal.outcome(key) == "succeeded",
            "Failed rebind cannot establish ownership of this binding: " + key,
        )

    def stage(self, *, max_validation_queries=500):
        require(
            self.plan["ready"], "Plan is blocked: " + "; ".join(self.plan["blockers"])
        )
        require(
            not self._rollback_begun(),
            "Rollback has started; finish rollback and create a new plan",
        )
        self._check_plan()
        self._review_plan()
        prior = self.journal.latest("plan")
        if not prior:
            self.journal.append("plan", sha256=digest(self.plan))
        probes = load(self.directory / "probes.json")
        require(
            2 * len(probes) <= max_validation_queries,
            "Validation query budget exceeded; raise --max-validation-queries deliberately",
        )
        self._same_sources()
        refresh_intent = self.journal.latest("intent", "refresh")
        preflight = inspect_preflight(
            self.api,
            self.plan,
            load(self.directory / "merged-model.json"),
            max_validation_queries=max_validation_queries,
            refresh_already_submitted=bool(refresh_intent),
        )
        self._marker("preflight", preflight)
        require(
            preflight["ready"],
            "Deployment preflight blocked: " + "; ".join(preflight["blockers"]),
        )
        if not self._body("baseline"):
            self._inventory()
            baseline = {}
            for probe in probes:
                source = self.plan["sources"][probe["source"]]
                baseline[probe["name"]] = self.api.rows(
                    source["workspace_id"], source["id"], probe["query"]
                )
            self._marker("baseline", baseline)
        created = self._body("created")
        if not created:
            if not self.journal.latest("intent", "create"):
                existing = self.api.models(self.plan["target_workspace"])
                require(
                    not any(
                        m["displayName"].casefold()
                        == self.plan["display_name"].casefold()
                        for m in existing
                    ),
                    "Target model name already exists; do not overwrite it",
                )
            body = {
                "displayName": self.plan["display_name"],
                "description": self.plan["ownership_marker"],
                "definition": load(self.directory / "merged-definition.json"),
            }
            if self.plan["security"]["label_id"]:
                body["sensitivityLabelSettings"] = {
                    "labelId": self.plan["security"]["label_id"],
                    "sensitivityLabelApplyStrategy": "ApplyOrFail",
                }
            response = self._mutation(
                "create",
                f"{FABRIC}/workspaces/{self.plan['target_workspace']}/semanticModels",
                body,
            )
            result = self.api.client.operation(
                response, timeout=self.api.operation_timeout
            )
            require(
                result and result.get("id"),
                "Create operation returned no model identity; reconcile without another POST",
            )
            created = self._marker(
                "created",
                {
                    "id": uid(result["id"]),
                    "workspace_id": self.plan["target_workspace"],
                },
            )
        workspace, model = created["workspace_id"], created["id"]
        require(
            model not in {s["id"] for s in self.plan["sources"].values()},
            "Creation identity is a source model",
        )
        # Readback checks the actual server definition, not just the submitted payload.
        actual = self.api.definition(workspace, model)
        expected = load(self.directory / "merged-model.json")
        require(
            compatible_readback(actual, expected),
            "Created model definition differs materially from the reviewed merge",
        )
        for index, binding in enumerate(self.plan["connections"]):
            self._mutation(
                "bind-" + str(index),
                f"{FABRIC}/workspaces/{workspace}/semanticModels/{model}/bindConnection",
                {"connectionBinding": binding},
            )
        bound = self.api.client.values(
            f"{FABRIC}/workspaces/{workspace}/items/{model}/connections"
        )
        actual_bindings = {connection_key(b): b for b in bound}
        for binding in self.plan["connections"]:
            observed = actual_bindings.get(connection_key(binding), {})
            require(
                all(observed.get(k) == v for k, v in binding.items()),
                "New model connection readback differs from reviewed binding",
            )
        target_metadata = self.api.client.request(
            "GET", f"{FABRIC}/workspaces/{workspace}/semanticModels/{model}"
        ).body
        label_id = self.plan["security"]["label_id"]
        if label_id:
            require(
                (target_metadata.get("sensitivityLabel") or {}).get("id") == label_id,
                "Target sensitivity label was not applied",
            )
        if not self._body("refreshed"):
            refresh_body = (
                refresh_intent["body"]
                if refresh_intent
                else preflight["selected_refresh"]["body"]
            )
            if not refresh_intent and "type" not in refresh_body:
                self._marker(
                    "refresh_history_before",
                    {
                        "workspace_id": workspace,
                        "model_id": model,
                        "captured_utc": now(),
                        "entries": self.api.refresh_history(workspace, model),
                    },
                )
            response = self._mutation(
                "refresh",
                f"{POWER_BI}/groups/{workspace}/datasets/{model}/refreshes",
                refresh_body,
            )
            self._marker(
                "refreshed",
                self.api.wait_refresh(
                    workspace,
                    model,
                    response,
                    history_before=self._body("refresh_history_before"),
                ),
            )
        if not self._body("validated"):
            baseline = self._body("baseline")
            checks = []
            for probe in probes:
                rows = self.api.rows(
                    workspace, model, probe.get("target_query", probe["query"])
                )
                require(
                    normalized_rows(rows) == normalized_rows(baseline[probe["name"]]),
                    "Validation mismatch: "
                    + probe["name"]
                    + "; no report has been rebound",
                )
                checks.append(
                    {
                        "probe": probe["name"],
                        "rows_sha256": digest(normalized_rows(rows)),
                        "passed": True,
                    }
                )
            self._marker("validated", checks)
        return {
            "status": "staged_and_validated",
            "model": created,
            "validation_probes": len(probes),
            "reports_rebound": 0,
        }

    def _validate_cutover(self, probes, created):
        """Fresh observations on every cutover attempt, including partial resumes."""
        attempt = str(uuid.uuid4())
        self._marker(
            "cutover_validation_started",
            {
                "attempt": attempt,
                "started_utc": now(),
                "probe_count": len(probes),
                "query_count_reserved": 2 * len(probes),
            },
        )
        checks = []
        for probe in probes:
            source = self.plan["sources"][probe["source"]]
            source_rows = normalized_rows(
                self.api.rows(source["workspace_id"], source["id"], probe["query"])
            )
            target_rows = normalized_rows(
                self.api.rows(
                    created["workspace_id"],
                    created["id"],
                    probe.get("target_query", probe["query"]),
                )
            )
            check = {
                "probe": probe["name"],
                "source_rows_sha256": digest(source_rows),
                "target_rows_sha256": digest(target_rows),
                "passed": source_rows == target_rows,
            }
            if not check["passed"]:
                self._marker(
                    "cutover_validation_failed",
                    {"attempt": attempt, "observed_utc": now(), **check},
                )
            require(
                check["passed"],
                "Fresh cutover validation mismatch: "
                + probe["name"]
                + "; cutover stopped. Inspect status and rollback if needed.",
            )
            checks.append(check)
        self._marker(
            "cutover_validated",
            {
                "attempt": attempt,
                "observed_utc": now(),
                "checks": checks,
                "query_count": 2 * len(probes),
            },
        )

    def apply(self, *, max_validation_queries=500, access_proof=None):
        self._check_plan()
        require(
            not self._rollback_begun(),
            "Rollback has started; finish rollback and create a new plan",
        )
        if self._body("completed"):
            require(not self._unresolved(), "Unresolved operations; run reconcile")
            created = self._body("created")
            for report in load(self.directory / "reports.json"):
                require(
                    self.api.report(report).get("datasetId", "").lower()
                    == created["id"],
                    "A completed report binding changed; inspect before taking action",
                )
            self._inventory(migrated=True)
            return self._body("completed")
        probes = load(self.directory / "probes.json")
        stage_queries = len(probes) * (
            int(not self._body("baseline")) + int(not self._body("validated"))
        )
        require(
            type(max_validation_queries) is int
            and max_validation_queries > 0
            and stage_queries + 2 * len(probes) <= max_validation_queries,
            "Stage plus fresh cutover query budget exceeded; raise --max-validation-queries deliberately",
        )
        self.stage(max_validation_queries=max_validation_queries)
        created = self._body("created")
        self._inventory(migrated=True)
        workspace, model = created["workspace_id"], created["id"]
        merged = load(self.directory / "merged-model.json")
        if merged["bim"]["model"].get("roles"):
            require(
                access_proof
                and access_proof.get("model_id") == model
                and access_proof.get("workspace_id") == workspace
                and access_proof.get("security_policy_sha256")
                == digest(self.plan["security"])
                and access_proof.get("memberships_verified") is True
                and bool(access_proof.get("reviewer")),
                "RLS/OLS cutover requires --access-proof for this exact new model and security policy; stage first, configure memberships, then record the review",
            )
            if not self.journal.latest("access_review"):
                self.journal.append(
                    "access_review",
                    proof=access_proof,
                    verification="operator attestation; role assignments are not exported by the definition API",
                )
        self._validate_cutover(probes, created)
        for index, permission in enumerate(
            self.plan["security"].get("permissions", [])
        ):
            self._mutation(
                "permission-" + str(index),
                f"{POWER_BI}/groups/{workspace}/datasets/{model}/users",
                permission,
            )
        actual_permissions = self.api.client.values(
            f"{POWER_BI}/groups/{workspace}/datasets/{model}/users"
        )
        for permission in self.plan["security"].get("permissions", []):
            matches = [
                p
                for p in actual_permissions
                if p.get("principalType") == permission["principalType"]
                and p.get("identifier", "").casefold()
                == permission["identifier"].casefold()
            ]
            require(
                matches
                and all(
                    word not in permission["datasetUserAccessRight"]
                    or word in matches[0].get("datasetUserAccessRight", "")
                    for word in ("Read", "Explore", "Reshare")
                ),
                "Granted access is not visible yet; resume after propagation, before rebinding reports",
            )
        for report in load(self.directory / "reports.json"):
            key = "rebind-" + report["id"]
            current = self.api.report(report)
            if current.get("datasetId", "").lower() == model:
                self._confirmed_rebind(key)
                if not self.journal.latest("report_verified", key):
                    self.journal.append(
                        "report_verified",
                        key=key,
                        report=report,
                        target_dataset_id=model,
                    )
                continue
            require(
                current.get("datasetId", "").lower() == report["dataset_id"],
                "Concurrent report binding change; refusing overwrite",
            )
            self._mutation(
                key, self.api.report_url(report) + "/Rebind", {"datasetId": model}
            )
            require(
                self.api.report(report).get("datasetId", "").lower() == model,
                "Rebind readback failed; reconcile before continuing",
            )
            self.journal.append(
                "report_verified", key=key, report=report, target_dataset_id=model
            )
        # Verify the entire original set again, including cross-workspace reports.
        for report in load(self.directory / "reports.json"):
            require(
                self.api.report(report).get("datasetId", "").lower() == model,
                "Final report inventory is not fully migrated",
            )
        self._inventory(migrated=True)
        require(not self._unresolved(), "Unresolved operations; run reconcile")
        result = {
            "status": "completed",
            "model": created,
            "reports_rebound": len(load(self.directory / "reports.json")),
            "coverage": self.plan["coverage"],
            "source_models_preserved": True,
            "source_schedules_preserved": True,
        }
        self._marker("completed", result)
        save(self.directory / "result.json", result)
        return result

    def rollback(self):
        self._check_plan()
        created = self._body("created")
        require(
            created, "No confirmed created model; reconcile the create outcome first"
        )
        require(
            not any(
                key.startswith(("rebind-", "rollback-")) for key in self._unresolved()
            ),
            "Unresolved report operation; run reconcile before rollback",
        )
        if not self.journal.latest("rollback_started"):
            self._marker("rollback_started", {"model": created})
        restored = []
        for report in reversed(load(self.directory / "reports.json")):
            original_intent = self.journal.latest("intent", "rebind-" + report["id"])
            if not original_intent:
                continue
            current = self.api.report(report).get("datasetId", "").lower()
            if current == report["dataset_id"]:
                # An unresolved mutation could still be in flight; don't call it undone.
                require(
                    self.journal.outcome("rebind-" + report["id"])
                    in ("succeeded", "failed"),
                    "Unresolved rebind still points to old model; cannot prove rollback yet",
                )
            else:
                require(
                    current == created["id"],
                    "Report changed by another actor; refusing rollback overwrite",
                )
                self._confirmed_rebind("rebind-" + report["id"])
                self._mutation(
                    "rollback-" + report["id"],
                    self.api.report_url(report) + "/Rebind",
                    {"datasetId": report["dataset_id"]},
                )
                require(
                    self.api.report(report).get("datasetId", "").lower()
                    == report["dataset_id"],
                    "Rollback readback failed",
                )
            restored.append(report["id"])
        # Earlier readbacks can drift while later reports are being restored.
        for report in load(self.directory / "reports.json"):
            if report["id"] in restored:
                require(
                    self.api.report(report).get("datasetId", "").lower()
                    == report["dataset_id"],
                    "Final rollback binding changed; inspect before continuing",
                )
        result = {
            "status": "rolled_back",
            "reports_restored": restored,
            "merged_model_retained": created,
        }
        if not self._body("rolled_back"):
            self._marker("rolled_back", result)
        return result

    def reconcile(self):
        """Observe exact resources; never issue a workload/mutation."""
        self._check_plan()
        outcomes = []
        for event in self.journal.events():
            if (
                event["kind"] != "intent"
                or self.journal.outcome(event["key"]) != "unknown"
            ):
                continue
            key = event["key"]
            if key == "create":
                found = [
                    m
                    for m in self.api.models(self.plan["target_workspace"])
                    if m.get("description") == self.plan["ownership_marker"]
                ]
                require(
                    len(found) <= 1,
                    "Multiple items match ownership marker; do not guess",
                )
                if found:
                    actual = self.api.definition(
                        self.plan["target_workspace"], found[0]["id"]
                    )
                    require(
                        compatible_readback(
                            actual, load(self.directory / "merged-model.json")
                        ),
                        "Owned item definition mismatch",
                    )
                    self.journal.append(
                        "reconciled",
                        key=key,
                        body=found[0],
                        observed="exact marker and model definition",
                    )
                    outcomes.append(
                        {
                            "key": key,
                            "status": "confirmed_created",
                            "id": found[0]["id"],
                        }
                    )
                else:
                    outcomes.append(
                        {
                            "key": key,
                            "status": "unknown; absence does not authorize resubmission",
                        }
                    )
            elif key.startswith(("rebind-", "rollback-")):
                report_id = key.split("-", 1)[1]
                report = next(
                    r
                    for r in load(self.directory / "reports.json")
                    if r["id"] == report_id
                )
                actual = self.api.report(report)
                if actual.get("datasetId", "").lower() == event["body"]["datasetId"]:
                    self.journal.append(
                        "reconciled",
                        key=key,
                        body=None,
                        observed="report points to requested model",
                    )
                    outcomes.append({"key": key, "status": "confirmed_applied"})
                else:
                    outcomes.append(
                        {
                            "key": key,
                            "status": "unknown; not observed at requested target",
                        }
                    )
            else:
                outcomes.append(
                    {
                        "key": key,
                        "status": "requires exact operation-specific observation; no retry permitted",
                    }
                )
        return {"outcomes": outcomes, "mutations_submitted": 0}

    def status(self):
        events = self.journal.events()
        unresolved = self._unresolved()
        return {
            "plan_ready": self.plan["ready"],
            "blockers": self.plan["blockers"],
            "model": self._body("created"),
            "status": "rolled_back"
            if self._body("rolled_back")
            else "rolling_back"
            if self._rollback_begun()
            else "completed"
            if self._body("completed")
            else "staged"
            if self._body("validated")
            else "in_progress"
            if events
            else "planned",
            "unresolved_operations": unresolved,
            "journal_records": len(events),
            "coverage": self.plan["coverage"],
        }


def compatible_readback(actual, expected):
    """Ignore only service-owned database IDs/names and object lineage metadata."""

    def clean(value):
        if isinstance(value, dict):
            return {
                k: clean(v)
                for k, v in value.items()
                if k not in ("lineageTag", "sourceLineageTag")
            }
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    left, right = copy.deepcopy(actual), copy.deepcopy(expected)
    for model in (left, right):
        model["bim"].pop("id", None)
        model["bim"].pop("name", None)
    return clean(left) == clean(right)
