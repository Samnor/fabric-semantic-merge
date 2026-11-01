"""Conservative capability checks before admitting a new model or refresh."""

from .model import summary
from .util import MergeError, digest, now, require, uid


REFRESH_MODES = ("auto", "standard", "enhanced")


def refresh_request(mode, dedicated, identity_type):
    require(mode in REFRESH_MODES, "Refresh mode must be auto, standard or enhanced")
    require(
        dedicated is None or type(dedicated) is bool, "Invalid dedicated-capacity flag"
    )
    chosen = (
        ("enhanced" if dedicated is True else "standard") if mode == "auto" else mode
    )
    require(
        chosen != "enhanced" or dedicated is True,
        "Enhanced refresh requires observed dedicated capacity; use standard or verify the target",
    )
    if chosen == "enhanced":
        body = {
            "type": "full",
            "commitMode": "transactional",
            "retryCount": 0,
            "timeout": "01:00:00",
        }
    else:
        require(
            identity_type in ("user", "service_principal"),
            "Standard refresh needs a known user/service-principal identity type",
        )
        body = (
            {}
            if identity_type == "service_principal"
            else {"notifyOption": "NoNotification"}
        )
    return {
        "mode": chosen,
        "body": body,
        "basis": "observed workspace dedicated-capacity flag"
        if mode == "auto"
        else "reviewed plan mode, checked against current workspace",
    }


def model_risks(model):
    """Detect structural ambiguity, without guessing intended data/filter semantics."""
    blocks, warnings = [], []
    info = summary(model["bim"])
    if not info["modes"] or any(mode.casefold() != "import" for mode in info["modes"]):
        blocks.append(
            "This staging adapter supports Import partitions only; Direct Lake, DirectQuery, live/composite or unknown modes need a separate qualified adapter"
        )
    tabular = model["bim"]["model"]
    endpoints = set()
    for relation in tabular.get("relationships", []):
        key = tuple(
            str(relation.get(k, "")).casefold()
            for k in ("fromTable", "fromColumn", "toTable", "toColumn")
        )
        if key in endpoints:
            blocks.append(
                "Repeated relationship endpoints require resolution: "
                + relation["name"]
            )
        endpoints.add(key)
    for table in tabular.get("tables", []):
        sources = set()
        for partition in table.get("partitions", []):
            source = partition.get("source")
            if source is not None:
                key = digest(source)
                if key in sources:
                    blocks.append(
                        "Repeated partition source may duplicate data: "
                        + table["name"]
                        + "/"
                        + partition["name"]
                    )
                sources.add(key)
        if table.get("refreshPolicy"):
            blocks.append(
                "Incremental/hybrid refresh policy needs a separately qualified deployment path: "
                + table["name"]
            )
        if table.get("calculationGroup"):
            warnings.append(
                "Calculation-group interaction requires contextual DAX parity: "
                + table["name"]
            )
    return blocks, warnings


def inspect_preflight(
    api, plan, model, *, max_validation_queries=500, refresh_already_submitted=False
):
    """Metadata GETs only; never probe by creating, querying, refreshing or rebinding."""
    require(
        type(max_validation_queries) is int and max_validation_queries > 0,
        "Positive validation query budget required",
    )
    policy = plan.get("deployment", {"refresh_mode": "auto"})
    require(
        isinstance(policy, dict) and policy.get("refresh_mode") in REFRESH_MODES,
        "Invalid deployment policy",
    )
    require(
        len(plan.get("connections", [])) <= 100,
        "Preflight supports at most 100 reviewed connection bindings",
    )
    blocks, warnings = model_risks(model)
    blocks = list(plan.get("blockers", [])) + blocks
    if not plan.get("ready") and not blocks:
        blocks.append("The reviewed merge plan is not ready")
    records = []
    identity = getattr(api.client.credential, "identity_type", "unknown")
    if identity not in ("user", "service_principal"):
        identity = "unknown"

    def observe(label, operation, expected_id, *, fields=None):
        record = {"check": label}
        try:
            body = operation()
            require(isinstance(body, dict), "Metadata response must be an object")
            require(
                uid(body.get("id")) == uid(expected_id),
                "Metadata identity does not match requested identity",
            )
            value = {k: body[k] for k in fields if k in body} if fields else body
            record.update(status="observed", value=value)
        except (MergeError, OSError) as exc:
            record.update(status="unavailable", error=str(exc))
            if hasattr(exc, "status"):
                record["http_status"] = exc.status
            blocks.append(label + ": required metadata unavailable or inconsistent")
        records.append(record)
        return record.get("value")

    workspace_id = plan["target_workspace"]
    workspace = observe(
        "target_fabric_workspace", lambda: api.workspace(workspace_id), workspace_id
    )
    group = observe(
        "target_power_bi_workspace", lambda: api.group(workspace_id), workspace_id
    )
    dedicated = None
    if workspace and workspace.get("type") == "Personal":
        blocks.append(
            "A non-personal target workspace is required by this deployment adapter"
        )
    if workspace and workspace.get("capacityAssignmentProgress") not in (
        None,
        "Completed",
    ):
        blocks.append("Target capacity assignment is not settled")
    if group:
        if group.get("isReadOnly") is True:
            blocks.append("Target workspace is read-only")
        if type(group.get("isOnDedicatedCapacity")) is bool:
            dedicated = group["isOnDedicatedCapacity"]
        else:
            warnings.append(
                "Target capacity tier is unknown; auto mode uses standard refresh conservatively"
            )
    if (
        workspace
        and group
        and workspace.get("capacityId")
        and group.get("capacityId")
        and uid(workspace["capacityId"]) != uid(group["capacityId"])
    ):
        blocks.append(
            "Workspace capacity observations disagree; reobserve after assignment settles"
        )
    for name, source in plan["sources"].items():
        metadata = observe(
            name + "_power_bi_model",
            lambda s=source: api.dataset(s["workspace_id"], s["id"]),
            source["id"],
        )
        if metadata and metadata.get("isRefreshable") is False:
            warnings.append(
                name
                + ": isRefreshable=false is not a definitive capability test; it can reflect refresh/schedule history. Import eligibility is checked from the actual partition definitions"
            )
        if (
            metadata
            and identity == "service_principal"
            and metadata.get("isEffectiveIdentityRolesRequired") is True
        ):
            blocks.append(
                name
                + ": original RLS model cannot be validated through service-principal Execute Queries"
            )

    sso_unknown = False
    for binding in plan.get("connections", []):
        if binding.get("connectivityType") == "Automatic":
            sso_unknown = True
            warnings.append(
                "Automatic/SSO connection needs identity-specific verification"
            )
            continue
        connection_id = uid(binding["id"])
        connection = observe(
            "connection:" + connection_id,
            lambda c=connection_id: api.connection(c),
            connection_id,
            fields=(
                "id",
                "connectivityType",
                "connectionDetails",
                "myRole",
                "credentialDetails",
            ),
        )
        if connection:
            if connection.get("connectivityType") != binding.get(
                "connectivityType"
            ) or connection.get("connectionDetails") != binding.get(
                "connectionDetails"
            ):
                blocks.append(
                    "Connection definition differs from reviewed binding: "
                    + connection_id
                )
            sso = (connection.get("credentialDetails") or {}).get("singleSignOnType")
            if identity == "service_principal" and sso not in (None, "None"):
                blocks.append(
                    "Service-principal Execute Queries is unsupported with SSO: "
                    + connection_id
                )
            if sso is None:
                sso_unknown = True
    roles = model["bim"]["model"].get("roles", [])
    if identity == "service_principal" and roles:
        blocks.append("Service-principal Execute Queries cannot validate RLS models")
    if identity == "service_principal" and sso_unknown:
        blocks.append(
            "Service-principal query eligibility is unknown because SSO state was not verified"
        )
    if roles:
        warnings.append(
            "Effective-user RLS/OLS membership and result tests remain required before cutover"
        )
    query_count = 2 * plan["validation"]["probe_count"]
    if query_count > max_validation_queries:
        blocks.append("Validation query budget exceeded before workload admission")
    selected = None
    if not refresh_already_submitted:
        try:
            selected = refresh_request(policy["refresh_mode"], dedicated, identity)
        except MergeError as exc:
            blocks.append(str(exc))
    else:
        warnings.append(
            "A refresh intent already exists; resume only its exact saved receipt/handle, preserving its request body"
        )
    warnings += [
        "Stage separately re-exports source definitions/connections/access/labels and checks the plan hashes; metadata visibility alone does not establish source exportability",
        "Metadata visibility does not prove target create/license/connection-use rights or report Write/target Build rights; actual operations still verify these",
        "Execute Queries tenant setting and effective-user security are not established by metadata GETs",
        "Control the data snapshot: source observations before a new target refresh may legitimately differ from refreshed target data",
        "Exclusive report control is required during cutover/rollback; a local journal lock cannot exclude external writers",
    ]
    return {
        "kind": "deployment_preflight",
        "schema_version": 1,
        "observed_utc": now(),
        "plan_sha256": digest(plan),
        "identity_type": identity,
        "status": "blocked" if blocks else "eligible_for_staging",
        "ready": not blocks,
        "blockers": blocks,
        "warnings": warnings,
        "observations": records,
        "selected_refresh": selected,
        "validation_query_count": query_count,
        "minimum_query_interval_seconds": 0.55,
        "scope": "Metadata-only admission checks; no end-to-end or security certification",
        "mutations_submitted": 0,
        "dax_queries_submitted": 0,
    }
