"""Unified target-dispatch CLI for the candidate pipeline."""

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

from .core.errors import PipelineError
from .core.models import canonical_json_sha256
from .core.orchestration import (
    _publish_fetched_outputs,
    _validate_online_plan,
    execute_build,
    execute_fetch,
)
from .core.registry import (
    load_target_reference,
    resolve_target_reference,
)
from .core.state import RunStateStore


TARGET_DIR = Path(__file__).resolve().parent / "targets"


def _parser():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--target", default=None)
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path.home() / ".local" / "state" / "taiji-package",
    )
    parser.add_argument("--ssh-config", type=Path)
    parser.add_argument("--json", action="store_true", dest="json_output")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", allow_abbrev=False)
    doctor.add_argument("--online", action="store_true")
    subparsers.add_parser("plan", allow_abbrev=False)
    subparsers.add_parser("build", allow_abbrev=False)
    status = subparsers.add_parser("status", allow_abbrev=False)
    status.add_argument("--run", required=True, dest="run_id")
    fetch = subparsers.add_parser("fetch", allow_abbrev=False)
    fetch.add_argument("--run", required=True, dest="run_id")
    return parser


def _load_validated_target(reference, adapter_factory):
    target_path = resolve_target_reference(reference, TARGET_DIR)
    payload = load_target_reference(target_path)
    target_id = payload.get("target_id")
    adapter = adapter_factory(target_id)
    try:
        target = adapter.validate_target(payload)
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(
            "target validation failed: {}".format(exc), category="TARGET_INVALID"
        ) from exc
    if not isinstance(target, dict):
        raise PipelineError("validated target must be an object", category="TARGET_INVALID")
    return target, adapter


def _doctor_blocked(local):
    categories = local.get("failure_categories")
    if isinstance(categories, list) and categories:
        return str(categories[0])
    return "PIPELINE_BLOCKED"


def _ensure_controller_ready(local):
    if local.get("controller_status") != "CONTROLLER_READY":
        raise PipelineError(
            "local doctor blocked: {}".format("; ".join(local.get("blockers", []))),
            category=_doctor_blocked(local),
        )


def _ensure_builder_ready(online):
    if online.get("builder_status") != "BUILDER_READY":
        status = str(online.get("builder_status", "BLOCKED"))
        category = "BUILDER_UNREACHABLE" if status == "BUILDER_UNREACHABLE" else "ONLINE_DOCTOR_BLOCKED"
        raise PipelineError(
            "online doctor did not report BUILDER_READY: {}".format(status),
            category=category,
        )


def _render(payload, json_output):
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if isinstance(payload, dict) and "controller_status" in payload:
        print(payload["controller_status"])
        if payload.get("online") is not None:
            print(payload["online"].get("builder_status", ""))
        else:
            print(payload.get("builder_status", ""))
        for blocker in payload.get("blockers", []):
            print("BLOCKER\t{}".format(blocker))
        return
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


def _confirm(input_reader):
    try:
        value = input_reader("输入 BUILD 以确认输入准备、远程传输和候选构建三个阶段：")
    except EOFError as exc:
        raise PipelineError(
            "interactive BUILD confirmation is required",
            category="CONFIRMATION_REQUIRED",
        ) from exc
    if value.strip() != "BUILD":
        raise PipelineError(
            "candidate build confirmation did not match BUILD",
            category="CONFIRMATION_REQUIRED",
        )


def _state_input_plan(state):
    plan = state.get("plan")
    if not isinstance(plan, dict):
        raise PipelineError("run state lacks its candidate plan", category="PLAN_INVALID")
    if state.get("schema") == "taiji-package-run-state/v2":
        if plan.get("input") != state.get("input"):
            raise PipelineError(
                "state and execution plan input differ", category="PLAN_INVALID"
            )
    return deepcopy(plan)


def _fetch_context(args, state, adapter_factory, command_runner):
    legacy = state.get("schema") != "taiji-package-run-state/v2"
    if legacy:
        adapter = adapter_factory("kylin-amd64")
        try:
            normalized = adapter.normalize_legacy_state(state)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                "legacy run state normalization failed: {}".format(exc),
                category="PLAN_INVALID",
            ) from exc
    else:
        target_id = state.get("target_id")
        if not isinstance(target_id, str) or not target_id:
            raise PipelineError("run state target id is invalid", category="PLAN_INVALID")
        adapter = adapter_factory(target_id)
        normalized = deepcopy(state)

    target_config = normalized.get("target_config")
    target_id = normalized.get("target_id")
    if not isinstance(target_config, dict) or not isinstance(target_id, str):
        raise PipelineError("run state target identity is incomplete", category="PLAN_INVALID")
    expected_sha = normalized.get("target_config_sha256")
    if expected_sha != canonical_json_sha256(target_config):
        raise PipelineError("run state target identity is inconsistent", category="PLAN_INVALID")

    if args.target is not None:
        explicit_target, explicit_adapter = _load_validated_target(args.target, adapter_factory)
        explicit_id = explicit_target.get("target_id")
        if explicit_id != target_id or canonical_json_sha256(explicit_target) != expected_sha:
            raise PipelineError(
                "explicit target differs from frozen run target", category="PLAN_INVALID"
            )
        adapter = explicit_adapter
        target_config = explicit_target

    plan = _state_input_plan(normalized)
    source = normalized.get("source")
    if isinstance(source, dict):
        repo_value = source.get("repo_root")
    else:
        repo_value = plan.get("repo_root")
    if not isinstance(repo_value, str) or not repo_value:
        raise PipelineError("run state source repository is missing", category="PLAN_INVALID")
    transport = adapter.create_transport(
        Path(repo_value),
        target_config,
        ssh_config=args.ssh_config,
        command_runner=command_runner,
    )
    return normalized, plan, adapter, transport, legacy


def _facade_publisher(store, run_id, fetched, artifact):
    published_paths = _publish_fetched_outputs(store, run_id, fetched)
    published = dict(artifact)
    published["path"] = str(Path(published_paths["review_path"]) / artifact["relative_path"])
    return published


def main(argv=None, *, adapter_factory, command_runner, input_reader, publisher):
    args = _parser().parse_args(argv)
    try:
        if args.command == "status":
            payload = RunStateStore(args.state_root).load(args.run_id)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "fetch":
            store = RunStateStore(args.state_root)
            state = store.load(args.run_id)
            if (
                state.get("stage") != "FETCH_PENDING"
                or not state.get("remote_build_succeeded")
                or not state.get("fetch_allowed")
            ):
                raise PipelineError(
                    "fetch is allowed only after remote build success and local retrieval failure",
                    category="FETCH_NOT_ALLOWED",
                )
            _normalized, plan, fetch_adapter, transport, _legacy = _fetch_context(
                args, state, adapter_factory, command_runner
            )
            recovered = execute_fetch(
                state,
                plan,
                fetch_adapter,
                transport,
                store,
                publisher=publisher,
            )
            print(json.dumps(recovered, ensure_ascii=False, sort_keys=True))
            return 0

        target_reference = args.target
        if target_reference is None and args.command in ("doctor", "plan", "build"):
            target_reference = "kylin-amd64"
        target, adapter = _load_validated_target(target_reference, adapter_factory)

        if args.command == "doctor":
            local = adapter.local_doctor(
                args.repo, target, args.state_root, ssh_config=args.ssh_config
            )
            online = None
            if args.online and local.get("controller_status") == "CONTROLLER_READY":
                transport = adapter.create_transport(
                    args.repo,
                    target,
                    ssh_config=args.ssh_config,
                    command_runner=command_runner,
                )
                online = transport.online_doctor()
            payload = {"local": local, "online": online}
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            else:
                print(local.get("controller_status", ""))
                print(online.get("builder_status", "") if online else local.get("builder_status", ""))
                for blocker in local.get("blockers", []):
                    print("BLOCKER\t{}".format(blocker))
                if online:
                    for blocker in online.get("blockers", []):
                        print("BLOCKER\t{}".format(blocker))
            ready = local.get("controller_status") == "CONTROLLER_READY"
            if args.online:
                ready = ready and online is not None and online.get("builder_status") == "BUILDER_READY"
            return 0 if ready else 2

        _ensure_controller_ready(
            adapter.local_doctor(args.repo, target, args.state_root, ssh_config=args.ssh_config)
        )
        if args.command == "plan":
            plan = adapter.build_plan(
                args.repo, target, args.state_root, run_id=None, ssh_config=args.ssh_config
            )
            _render(plan, args.json_output)
            return 0

        if args.command == "build":
            plan = adapter.build_plan(
                args.repo, target, args.state_root, run_id=None, ssh_config=args.ssh_config
            )
            transport = adapter.create_transport(
                args.repo, target, ssh_config=args.ssh_config, command_runner=command_runner
            )
            online = transport.online_doctor()
            _ensure_builder_ready(online)
            finalized = _validate_online_plan(plan, adapter.bind_online_plan(plan, online), adapter)
            _render({"online_doctor": online, "plan": finalized}, args.json_output)
            _confirm(input_reader)
            state = execute_build(
                finalized,
                online,
                adapter,
                transport,
                RunStateStore(args.state_root),
                command_runner=command_runner,
                input_reader=input_reader,
                publisher=publisher,
            )
            print(json.dumps(state, ensure_ascii=False, sort_keys=True))
            return 0

        raise PipelineError("{} is not implemented yet".format(args.command))
    except PipelineError as exc:
        print("BLOCKED\t{}\t{}".format(exc.category, exc), file=sys.stderr)
        return 2
