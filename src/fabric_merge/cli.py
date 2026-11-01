"""Command-line and interactive model selection."""

import argparse
from pathlib import Path
import sys

from . import __version__
from .api import Fabric
from .model import decode_definition, merge_models
from .parity import run_parity, verify_evidence
from .preflight import inspect_preflight
from .schedules import (
    observe_schedules,
    recommend_schedules,
    verify_suggestion,
    write_suggestion,
)
from .transport import AzureCliCredential, Client, POWER_BI
from .util import MergeError, digest, encoded, load, require, save, uid
from .workflow import Migration, create_plan, open_plan


def ref(value):
    parts = value.split("/")
    require(len(parts) == 2, "Model reference must be WORKSPACE_UUID/MODEL_UUID")
    return uid(parts[0]), uid(parts[1])


def catalog(api, workspace=None):
    workspaces = (
        [{"id": uid(workspace), "displayName": workspace}]
        if workspace
        else api.workspaces()
    )
    result = []
    for item in workspaces:
        if item.get("type") == "Personal":
            continue
        for model in api.models(item["id"]):
            result.append(
                {
                    "workspace_id": item["id"],
                    "workspace_name": item["displayName"],
                    "model_id": model["id"],
                    "model_name": model["displayName"],
                }
            )
    return result


def select_models(api, left=None, right=None, workspace=None, *, input_fn=input):
    if left and right:
        return ref(left), ref(right)
    require(
        sys.stdin.isatty(),
        "Pass --left WORKSPACE/MODEL and --right WORKSPACE/MODEL when not using an interactive terminal",
    )
    models = catalog(api, workspace)
    require(len(models) >= 2, "At least two selectable semantic models are required")
    for index, model in enumerate(models, 1):
        print(
            f"{index:>3}. {model['workspace_name']} / {model['model_name']}  [{model['model_id']}]",
            file=sys.stderr,
        )
    selected = []
    for name, existing in (("first", left), ("second", right)):
        if existing:
            selected.append(ref(existing))
            continue
        try:
            index = int(input_fn(f"Select the {name} model number: ")) - 1
        except ValueError as exc:
            raise MergeError("Enter a model number") from exc
        require(0 <= index < len(models), "Model number outside the displayed list")
        model = models[index]
        selected.append((model["workspace_id"], model["model_id"]))
    require(selected[0][1] != selected[1][1], "Choose two distinct models")
    return tuple(selected)


def confirm(message, yes):
    if yes:
        return
    require(
        sys.stdin.isatty(), "Review PLAN.md and pass --yes to authorize this command"
    )
    require(input(message + " Type yes: ").strip().lower() == "yes", "Cancelled")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="fabric-merge",
        description="Merge two semantic models and migrate their reports using Azure CLI authentication",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--tenant", help="Azure tenant UUID; checked against the selected account"
    )
    parser.add_argument("--subscription", help="Azure CLI subscription ID/name")
    parser.add_argument("--http-timeout", type=float, default=45)
    parser.add_argument(
        "--operation-timeout",
        type=float,
        default=1800,
        help="Per async operation polling ceiling in seconds",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check Azure CLI account and read both API audiences")
    listing = sub.add_parser("catalog", help="List selectable semantic models")
    listing.add_argument("--workspace")
    plan = sub.add_parser(
        "plan",
        help="Select two models and save a merge preview/report migration plan; no create/rebind",
    )
    plan.add_argument(
        "--left", help="WORKSPACE_UUID/MODEL_UUID; omitted for interactive selection"
    )
    plan.add_argument("--right")
    plan.add_argument(
        "--workspace", help="Limit interactive selection to this workspace"
    )
    plan.add_argument(
        "--target-workspace", help="Defaults to the first model's workspace"
    )
    plan.add_argument("--name", required=True, help="New merged model display name")
    plan.add_argument("--out", type=Path, required=True)
    plan.add_argument(
        "--refresh-capacity",
        choices=("unknown", "shared", "premium"),
        default="unknown",
        help="Declared target schedule tier; informational suggestion only",
    )
    plan.add_argument(
        "--refresh-mode",
        choices=("auto", "standard", "enhanced"),
        default="auto",
        help="Deployment refresh API policy; independent from schedule suggestions",
    )
    plan.add_argument(
        "--report-scope",
        choices=("tenant", "accessible", "workspaces"),
        default="tenant",
    )
    plan.add_argument("--report-workspace", action="append", default=[])
    for name in ("resolutions", "security", "probes", "connection-overrides"):
        plan.add_argument(
            "--" + name, type=Path, help="Reviewed JSON configuration; see examples/"
        )
    merge = sub.add_parser(
        "merge",
        help="Offline structural merge of two exported Fabric TMSL definition envelopes",
    )
    merge.add_argument("--left", type=Path, required=True)
    merge.add_argument("--right", type=Path, required=True)
    merge.add_argument("--name", required=True)
    merge.add_argument("--resolutions", type=Path)
    merge.add_argument("--out", type=Path, required=True)
    parity = sub.add_parser(
        "parity", help="Compare a merged model with both originals; no refresh/rebind"
    )
    parity.add_argument(
        "directory",
        type=Path,
        nargs="?",
        help="Existing migration plan; uses its confirmed created target",
    )
    parity.add_argument(
        "--left", help="Original WORKSPACE_UUID/MODEL_UUID (without a plan)"
    )
    parity.add_argument("--right")
    parity.add_argument(
        "--target", help="Merged WORKSPACE_UUID/MODEL_UUID (without a plan)"
    )
    parity.add_argument(
        "--probes", type=Path, help="Extra or overriding context-specific DAX probes"
    )
    parity.add_argument(
        "--out", type=Path, help="Optional fresh private evidence directory"
    )
    parity.add_argument("--max-queries", type=int, default=500)
    parity.add_argument(
        "--check-stability",
        action="store_true",
        help="Repeat source and target observations; four DAX calls per probe",
    )
    parity.add_argument(
        "--query-interval",
        type=float,
        default=0.55,
        help="Minimum seconds between DAX requests; default stays below 120/minute",
    )
    verification = sub.add_parser(
        "verify-parity", help="Replay/check a parity evidence folder offline"
    )
    verification.add_argument("directory", type=Path)
    schedule = sub.add_parser(
        "suggest-schedule",
        help="Read both original schedules and suggest a merged weekly schedule; never apply",
    )
    schedule.add_argument(
        "directory",
        type=Path,
        nargs="?",
        help="Existing merge plan; reobserve its original identities without changing it",
    )
    schedule.add_argument(
        "--left", help="Original WORKSPACE_UUID/MODEL_UUID (without a plan)"
    )
    schedule.add_argument("--right")
    schedule.add_argument(
        "--snapshot",
        type=Path,
        help="Offline snapshot.json from a prior schedule bundle; no authentication",
    )
    schedule.add_argument(
        "--capacity",
        choices=("unknown", "shared", "premium"),
        default="unknown",
        help="Declared target tier: shared=8, premium/PPU/Fabric=48 slots/day",
    )
    schedule.add_argument(
        "--out", type=Path, help="Optional fresh private proposal/evidence directory"
    )
    verify_schedule = sub.add_parser(
        "verify-schedule", help="Verify and replay a saved schedule suggestion offline"
    )
    verify_schedule.add_argument("directory", type=Path)
    preflight = sub.add_parser(
        "preflight",
        help="Check workspace, Import mode, connections and validation eligibility; metadata GETs only",
    )
    preflight.add_argument("directory", type=Path)
    preflight.add_argument(
        "--out", type=Path, help="Optional fresh private metadata evidence folder"
    )
    preflight.add_argument("--max-validation-queries", type=int, default=500)
    for command in ("stage", "apply", "rollback", "reconcile", "status"):
        item = sub.add_parser(command)
        item.add_argument("directory", type=Path)
        if command in ("stage", "apply", "rollback"):
            item.add_argument("--yes", action="store_true")
        if command in ("stage", "apply"):
            item.add_argument("--max-validation-queries", type=int, default=500)
        if command == "apply":
            item.add_argument(
                "--access-proof",
                type=Path,
                help="Exact new-model RLS/OLS membership review receipt, if roles exist",
            )
    return parser


def run(args):
    if args.command == "preflight":
        require(
            not args.out or not args.out.exists(),
            "Choose a fresh preflight output directory",
        )
    if args.command == "verify-schedule":
        return verify_suggestion(args.directory)
    if args.command == "suggest-schedule":
        require(
            not args.out or not args.out.exists(),
            "Choose a fresh schedule evidence directory",
        )
        if args.snapshot:
            require(
                not any((args.directory, args.left, args.right)),
                "Use an offline snapshot or model identities, not both",
            )
            return schedule_result(args, load(args.snapshot))
    if args.command == "verify-parity":
        return verify_evidence(args.directory)
    if args.command == "merge":
        value = merge_models(
            decode_definition(load(args.left)),
            decode_definition(load(args.right)),
            args.name,
            load(args.resolutions) if args.resolutions else None,
        )
        save(args.out, value, exclusive=True)
        return {
            "output": str(args.out),
            "ready": value["ready"],
            "conflicts": value["conflicts"],
            "issues": value["issues"],
            "summary": value["summary"],
        }
    credential = AzureCliCredential(args.tenant, args.subscription)
    client = Client(credential, timeout=args.http_timeout)
    api = Fabric(client, operation_timeout=args.operation_timeout)
    if args.command == "status":
        return Migration(api, args.directory).status()  # Local, no az/network request.
    if args.command == "reconcile":
        migration = Migration(api, args.directory)
        account = credential.account()
        if migration.plan.get("account"):
            require(
                account["tenant_id"] == migration.plan["account"]["tenant_id"],
                "Plan belongs to a different Azure tenant",
            )
        with migration.journal.lock():
            return migration.reconcile()
    account = credential.account()
    if args.command == "doctor":
        return {
            "account": account,
            "fabric_workspaces": len(api.workspaces()),
            "power_bi_workspaces": len(client.values(POWER_BI + "/groups")),
            "mutations_submitted": 0,
        }
    if args.command == "catalog":
        return catalog(api, args.workspace)
    if args.command == "preflight":
        _, plan = open_plan(args.directory)
        if plan.get("account"):
            require(
                account["tenant_id"] == plan["account"]["tenant_id"],
                "Plan belongs to a different Azure tenant",
            )
        result = inspect_preflight(
            api,
            plan,
            load(args.directory / "merged-model.json"),
            max_validation_queries=args.max_validation_queries,
        )
        if args.out:
            args.out.mkdir(parents=True, mode=0o700)
            save(args.out / "preflight.json", result, exclusive=True)
        return result
    if args.command == "suggest-schedule":
        if args.directory:
            require(
                not any((args.left, args.right)),
                "Use a plan directory or two explicit references, not both",
            )
            _, plan = open_plan(args.directory)
            if plan.get("account"):
                require(
                    account["tenant_id"] == plan["account"]["tenant_id"],
                    "Plan belongs to a different Azure tenant",
                )
            originals = plan["sources"]
            left, right = (
                (originals[n]["workspace_id"], originals[n]["id"])
                for n in ("left", "right")
            )
        else:
            require(
                args.left and args.right,
                "Provide --left and --right, a plan directory, or --snapshot",
            )
            left, right = ref(args.left), ref(args.right)
        return schedule_result(args, observe_schedules(api, left, right))
    if args.command == "plan":
        left, right = select_models(api, args.left, args.right, args.workspace)
        return create_plan(
            api,
            args.out,
            left,
            right,
            args.target_workspace or left[0],
            args.name,
            scope=args.report_scope,
            report_workspaces=args.report_workspace,
            resolutions=load(args.resolutions) if args.resolutions else None,
            security=load(args.security) if args.security else None,
            probes=load(args.probes) if args.probes else None,
            connection_overrides=load(args.connection_overrides)
            if args.connection_overrides
            else None,
            account=account,
            refresh_capacity=args.refresh_capacity,
            refresh_mode=args.refresh_mode,
        )
    if args.command == "parity":
        probes = load(args.probes) if args.probes else None
        options = {
            "probes": probes,
            "out": args.out,
            "max_queries": args.max_queries,
            "query_interval": args.query_interval,
            "check_stability": args.check_stability,
        }
        if args.directory:
            require(
                not any((args.left, args.right, args.target)),
                "Use a plan directory or three explicit model references, not both",
            )
            migration = Migration(api, args.directory)
            if migration.plan.get("account"):
                require(
                    account["tenant_id"] == migration.plan["account"]["tenant_id"],
                    "Plan belongs to a different Azure tenant",
                )
            with migration.journal.lock():
                migration._check_plan()
                created = migration._body("created")
                require(
                    created,
                    "Plan has no confirmed created target; stage/reconcile first, or use explicit references",
                )
                models = migration.plan["sources"]
                return run_parity(
                    api,
                    (models["left"]["workspace_id"], models["left"]["id"]),
                    (models["right"]["workspace_id"], models["right"]["id"]),
                    (created["workspace_id"], created["id"]),
                    base_probes=load(args.directory / "probes.json"),
                    provenance={
                        "plan_sha256": digest(migration.plan),
                        "run_id": migration.plan["run_id"],
                    },
                    **options,
                )
        require(
            all((args.left, args.right, args.target)),
            "Provide --left, --right and --target, or a migration plan directory",
        )
        return run_parity(
            api, ref(args.left), ref(args.right), ref(args.target), **options
        )
    migration = Migration(api, args.directory)
    if migration.plan.get("account"):
        require(
            account["tenant_id"] == migration.plan["account"]["tenant_id"],
            "Plan belongs to a different Azure tenant",
        )
    message = {
        "stage": "Create, connect, refresh and validate the new model; retain existing report bindings?",
        "apply": "Create/validate the merged model and rebind every report listed in PLAN.md?",
        "rollback": "Restore the listed reports to their recorded original models?",
    }[args.command]
    confirm(message, args.yes)
    with migration.journal.lock():
        if args.command == "rollback":
            return migration.rollback()
        if args.command == "apply":
            return migration.apply(
                max_validation_queries=args.max_validation_queries,
                access_proof=load(args.access_proof) if args.access_proof else None,
            )
        return migration.stage(max_validation_queries=args.max_validation_queries)


def schedule_result(args, snapshot):
    result = recommend_schedules(snapshot, args.capacity)
    if args.out:
        write_suggestion(args.out, snapshot, result)
    return result


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        value = run(args)
        print(encoded(value))
        if isinstance(value, dict) and value.get("kind") == "semantic_model_parity":
            return {"passed": 0, "failed": 2, "incomplete": 1}[value["status"]]
        return 0 if not isinstance(value, dict) or value.get("ready", True) else 2
    except (MergeError, OSError, ValueError, KeyError) as exc:
        print("fabric-merge: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
