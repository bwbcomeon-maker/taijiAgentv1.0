import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


def _profile():
    return {
        "id": "content-work-report",
        "team_id": "content-creator-team",
        "document_type": "work_report",
    }


def _run(workspace: Path) -> dict:
    return {
        "run_id": "run-standalone-provider",
        "session_id": "session-standalone-provider",
        "schema_version": 3,
        "product_mode": "standalone",
        "launch_profile_id": "content-work-report",
        "launch_profile_snapshot": _profile(),
        "workspace": str(workspace),
    }


def _session(workspace: Path):
    return SimpleNamespace(
        session_id="session-standalone-provider",
        workspace=str(workspace),
        profile="default",
        model="gpt-receipt",
        model_provider="openai",
        expert_team_launch_transaction_id="a" * 64,
    )


def _receipt(workspace: Path) -> dict:
    session = _session(workspace)
    return {
        "state": "committed",
        "transaction_id": "a" * 64,
        "run_id": "run-standalone-provider",
        "session_id": "session-standalone-provider",
        "workspace": str(workspace),
        "launch_profile_id": "content-work-report",
        "launch_profile_snapshot": _profile(),
        "session_options": {
            "workspace": str(workspace),
            "profile": "default",
            "model": "gpt-receipt",
            "model_provider": "openai",
        },
        "initial_session_snapshot": deepcopy(session.__dict__),
    }


def test_standalone_provider_binding_comes_from_committed_receipt_not_request_body(
    monkeypatch, tmp_path
):
    from api import routes
    from api.expert_teams import launch_storage

    receipt = _receipt(tmp_path)
    monkeypatch.setattr(
        launch_storage,
        "read_launch_transaction_for_run",
        lambda _run_id: deepcopy(receipt),
    )

    binding = routes._expert_team_standalone_execution_binding(
        tmp_path,
        _run(tmp_path),
        _session(tmp_path),
    )

    assert binding == {
        "workspace": str(tmp_path.resolve()),
        "profile": "default",
        "model": "gpt-receipt",
        "model_provider": "openai",
        "source": "committed_launch_receipt",
    }


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda receipt, session: receipt.update(state="reserved"), "committed"),
        (lambda receipt, session: receipt.update(session_id="other"), "identity"),
        (
            lambda receipt, session: receipt["launch_profile_snapshot"].update(team_id="forged"),
            "Profile",
        ),
        (lambda receipt, session: setattr(session, "model", "body-overridden"), "Session"),
        (lambda receipt, session: setattr(session, "model_provider", "fallback"), "Session"),
    ],
)
def test_standalone_provider_binding_rejects_receipt_or_session_drift_before_dispatch(
    monkeypatch, tmp_path, mutation, match
):
    from api import routes
    from api.expert_teams import launch_storage

    receipt = _receipt(tmp_path)
    session = _session(tmp_path)
    mutation(receipt, session)
    monkeypatch.setattr(
        launch_storage,
        "read_launch_transaction_for_run",
        lambda _run_id: deepcopy(receipt),
    )

    with pytest.raises(ValueError, match=match):
        routes._expert_team_standalone_execution_binding(
            tmp_path,
            _run(tmp_path),
            session,
        )


def _strict_request():
    from api.runtime_adapter import StartRunRequest

    return StartRunRequest(
        session_id="sid",
        message="",
        messages=[
            {"role": "system", "content": "strict system"},
            {"role": "user", "content": '{"payload":true}'},
        ],
        tools_disabled=True,
        provider="openai",
        model="gpt-receipt",
        source="expert-team",
        metadata={"expert_team_product_mode": "standalone"},
    )


@pytest.mark.parametrize(
    ("api_mode", "transport"),
    [
        ("chat_completions", "openai_chat_completions"),
        ("codex_responses", "openai_responses"),
        ("anthropic_messages", "anthropic_messages"),
    ],
)
def test_legacy_preflight_returns_complete_digest_bound_contract(api_mode, transport):
    from api.runtime_adapter import (
        LegacyJournalRuntimeAdapter,
        validate_strict_provider_context,
    )

    adapter = LegacyJournalRuntimeAdapter(
        provider_context_resolver=lambda _request: {
            "provider": "openai",
            "model": "gpt-receipt",
            "api_mode": api_mode,
            "transport": transport,
            "api_key": "must-not-leak",
            "base_url": "https://private.invalid/v1",
        }
    )

    context = adapter.resolve_provider_context(_strict_request())

    assert context == validate_strict_provider_context(
        context,
        expected_provider="openai",
        expected_model="gpt-receipt",
    )
    assert context["api_mode"] == api_mode
    assert context["transport"] == transport
    assert len(context["binding_sha256"]) == 64
    int(context["binding_sha256"], 16)
    assert all(
        context[field] is True
        for field in (
            "exact_system_prompt",
            "exact_user_payload",
            "preserves_message_roles",
            "supports_tools_disabled",
            "stateless",
            "fallback_disabled",
        )
    )
    assert "must-not-leak" not in repr(context)
    assert "private.invalid" not in repr(context)


@pytest.mark.parametrize("api_mode", ["bedrock_converse", "codex_app_server", ""])
def test_legacy_preflight_rejects_unreleased_runtime_transports(api_mode):
    from api.runtime_adapter import LegacyJournalRuntimeAdapter

    adapter = LegacyJournalRuntimeAdapter(
        provider_context_resolver=lambda _request: {
            "provider": "openai",
            "model": "gpt-receipt",
            "api_mode": api_mode,
        }
    )

    with pytest.raises(ValueError, match="api_mode"):
        adapter.resolve_provider_context(_strict_request())


def test_routes_reject_missing_tampered_or_extra_provider_binding_fields():
    from api import routes
    from api.runtime_adapter import build_strict_provider_context

    valid = build_strict_provider_context(
        provider="openai",
        model="gpt-receipt",
        api_mode="chat_completions",
        transport="openai_chat_completions",
    )
    invalid = []
    for key in tuple(valid):
        candidate = deepcopy(valid)
        candidate.pop(key)
        invalid.append(candidate)
    for key, value in (
        ("provider", "other"),
        ("model", "other"),
        ("api_mode", "anthropic_messages"),
        ("transport", "anthropic_messages"),
        ("exact_system_prompt", False),
        ("binding_sha256", "0" * 64),
    ):
        candidate = deepcopy(valid)
        candidate[key] = value
        invalid.append(candidate)
    extra = deepcopy(valid)
    extra["api_key"] = "secret"
    invalid.append(extra)

    for context in invalid:
        with pytest.raises(ValueError, match="runtime"):
            routes._validate_standalone_runtime_provider_context(
                context,
                expected_provider="openai",
                expected_model="gpt-receipt",
            )


def test_legacy_dispatch_rechecks_preflight_binding_and_blocks_drift():
    from api.runtime_adapter import LegacyJournalRuntimeAdapter

    dispatches = []
    contexts = iter(
        [
            {
                "provider": "openai",
                "model": "gpt-receipt",
                "api_mode": "chat_completions",
                "transport": "openai_chat_completions",
            },
            {
                "provider": "openai",
                "model": "gpt-receipt",
                "api_mode": "anthropic_messages",
                "transport": "anthropic_messages",
            },
        ]
    )
    adapter = LegacyJournalRuntimeAdapter(
        provider_context_resolver=lambda _request: next(contexts),
        start_run_delegate=lambda request: dispatches.append(request) or {},
    )
    request = _strict_request()
    binding = adapter.resolve_provider_context(request)
    request = replace(
        request,
        metadata={**request.metadata, "strict_provider_binding": binding},
    )

    with pytest.raises(ValueError, match="drift"):
        adapter.start_run(request)
    assert dispatches == []


def test_legacy_strict_dispatch_requires_a_validated_preflight_binding():
    from api.runtime_adapter import LegacyJournalRuntimeAdapter

    dispatches = []
    adapter = LegacyJournalRuntimeAdapter(
        provider_context_resolver=lambda request: {
            "provider": request.provider,
            "model": request.model,
            "api_mode": "chat_completions",
            "transport": "openai_chat_completions",
        },
        start_run_delegate=lambda request: dispatches.append(request) or {},
    )

    with pytest.raises(ValueError, match="binding"):
        adapter.start_run(_strict_request())
    assert dispatches == []


def test_legacy_dispatch_binds_actual_runtime_without_rewriting_session_selection():
    from api.runtime_adapter import LegacyJournalRuntimeAdapter

    dispatches = []
    adapter = LegacyJournalRuntimeAdapter(
        provider_context_resolver=lambda _request: {
            "provider": "openai-codex",
            "model": "gpt-runtime",
            "api_mode": "codex_responses",
            "transport": "openai_responses",
        },
        start_run_delegate=lambda request: dispatches.append(request) or {},
    )
    request = _strict_request()
    binding = adapter.resolve_provider_context(request)
    request = replace(
        request,
        metadata={**request.metadata, "strict_provider_binding": binding},
    )

    adapter.start_run(request)

    assert binding["provider"] == "openai-codex"
    assert binding["model"] == "gpt-runtime"
    assert dispatches[0].provider == "openai"
    assert dispatches[0].model == "gpt-receipt"


def test_route_legacy_preflight_resolves_runtime_inside_session_profile(
    monkeypatch, tmp_path
):
    from api import config, oauth, profiles, routes, streaming
    import hermes_constants

    calls = []
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda profile: calls.append(("profile", profile)) or tmp_path,
    )
    monkeypatch.setattr(
        hermes_constants,
        "set_hermes_home_override",
        lambda home: calls.append(("set", Path(home))) or "profile-token",
    )
    monkeypatch.setattr(
        hermes_constants,
        "reset_hermes_home_override",
        lambda token: calls.append(("reset", token)),
    )

    def resolve_with_lock(_resolver, **kwargs):
        calls.append(("resolve", kwargs))
        return {
            "provider": "openai-codex",
            "api_mode": "codex_responses",
            "base_url": "https://api.openai.com/v1",
            "api_key": "secret",
            "source": "test",
        }

    monkeypatch.setattr(
        oauth,
        "resolve_runtime_provider_with_anthropic_env_lock",
        resolve_with_lock,
    )
    monkeypatch.setattr(
        config,
        "model_with_provider_context",
        lambda model, provider: calls.append(("model_context", model, provider))
        or "@openai:gpt-receipt",
    )
    monkeypatch.setattr(
        config,
        "resolve_model_provider",
        lambda model: calls.append(("model_resolve", model))
        or ("gpt-actual", "openai-codex", None),
    )
    monkeypatch.setattr(
        streaming,
        "_resolve_custom_provider_runtime_overrides",
        lambda provider, api_key, base_url: (provider, api_key, base_url),
    )

    context = routes._resolve_standalone_legacy_provider_context(_strict_request())

    assert context == {
        "provider": "openai-codex",
        "model": "gpt-actual",
        "api_mode": "codex_responses",
        "transport": "openai_responses",
    }
    assert calls == [
        ("profile", None),
        ("set", tmp_path),
        ("model_context", "gpt-receipt", "openai"),
        ("model_resolve", "@openai:gpt-receipt"),
        ("resolve", {"requested": "openai-codex"}),
        ("reset", "profile-token"),
    ]


def _ready_direct_run(workspace: Path) -> dict:
    from api import expert_teams
    from api.expert_teams.contracts import confirm_document_brief

    run = expert_teams.build_standalone_expert_team_run(
        {
            "session_id": "direct-runtime-session",
            "launch_profile_id": "content-work-report",
            "prompt": "起草月度工作汇报",
            "idempotency_key": "direct-runtime-start",
        },
        run_id="et-direct-runtime",
    )
    brief = deepcopy(run["document_brief"])
    brief.update(
        {
            "exact_title": "迎峰度夏保供电重点工作月度汇报",
            "purpose": "汇报重点任务进展",
            "audience": "公司分管领导",
            "usage_scenario": "月度工作例会",
        }
    )
    brief["details"].update(
        {"reporting_period": "2026年7月", "reporting_unit": "生产运营部"}
    )
    run["document_brief"] = confirm_document_brief(
        brief,
        now="2026-07-25T10:00:00+08:00",
    )
    run["workflow_state"] = "ready_to_generate"
    return run


def _direct_session(workspace: Path):
    return SimpleNamespace(
        session_id="direct-runtime-session",
        workspace=str(workspace),
        profile="default",
        model="gpt-server",
        model_provider="openai",
        expert_team_launch_transaction_id=None,
        messages=[],
    )


def _patch_in_memory_execution_state(monkeypatch, expert_teams, run):
    reserved = deepcopy(run)
    reserved.update(
        {
            "workflow_state": "starting",
            "execution_start_id": "start-direct-runtime",
            "execution_runtime_adapter": "LegacyJournalRuntimeAdapter",
            "current_stage_attempt_reservation": {
                "reservation_id": "stage-direct-runtime",
                "stage_attempt": 1,
            },
        }
    )
    dispatching = deepcopy(reserved)
    dispatching["execution_start_state"] = "dispatching"
    started = deepcopy(dispatching)
    started["workflow_state"] = "generating"
    failed = deepcopy(reserved)
    failed["workflow_state"] = "ready_to_generate"
    failed["execution_start_state"] = "failed"

    monkeypatch.setattr(
        expert_teams,
        "reserve_expert_team_execution_start",
        lambda *_args, **_kwargs: deepcopy(reserved),
    )
    monkeypatch.setattr(
        expert_teams,
        "mark_expert_team_execution_start_dispatching",
        lambda *_args, **_kwargs: deepcopy(dispatching),
    )
    monkeypatch.setattr(
        expert_teams,
        "mark_expert_team_execution_started",
        lambda *_args, **_kwargs: deepcopy(started),
    )
    monkeypatch.setattr(
        expert_teams,
        "mark_expert_team_execution_start_failed",
        lambda *_args, **_kwargs: deepcopy(failed),
    )


def test_execution_route_ignores_client_provider_override_and_dispatches_exact_contract(
    monkeypatch, tmp_path
):
    from api import expert_teams, routes, runtime_adapter

    run = _ready_direct_run(tmp_path)
    session = _direct_session(tmp_path)
    calls = []
    monkeypatch.setattr(routes, "get_session", lambda _session_id: session)
    _patch_in_memory_execution_state(monkeypatch, expert_teams, run)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda model, provider: (model, provider, False),
    )
    monkeypatch.setattr(
        routes,
        "_resolve_standalone_legacy_provider_context",
        lambda request: {
            "provider": request.provider,
            "model": request.model,
            "api_mode": "chat_completions",
            "transport": "openai_chat_completions",
        },
    )

    def capture_start(self, request):
        calls.append(request)
        return runtime_adapter.RunStartResult(
            run_id="strict-runtime-run",
            session_id=request.session_id,
            stream_id="strict-runtime-stream",
            payload={"stream_id": "strict-runtime-stream", "turn_id": "strict-turn"},
        )

    monkeypatch.setattr(
        runtime_adapter.LegacyJournalRuntimeAdapter,
        "start_run",
        capture_start,
    )

    payload, status = routes._start_expert_team_execution(
        tmp_path,
        run,
        {"model": "client-forged", "model_provider": "client-forged-provider"},
    )

    assert status == 200, payload
    assert len(calls) == 1
    request = calls[0]
    assert request.model == "gpt-server"
    assert request.provider == "openai"
    assert request.profile == "default"
    assert request.tools_disabled is True
    assert [message["role"] for message in request.messages] == ["system", "user"]
    assert request.metadata["expert_team_product_mode"] == "standalone"
    assert request.metadata["runtime_binding_source"] == "committed_session"
    assert len(request.metadata["strict_provider_binding"]["binding_sha256"]) == 64
    assert "client-forged" not in repr(request)


def test_execution_route_provider_preflight_failure_makes_zero_runtime_calls(
    monkeypatch, tmp_path
):
    from api import expert_teams, routes, runtime_adapter

    run = _ready_direct_run(tmp_path)
    session = _direct_session(tmp_path)
    dispatches = []
    monkeypatch.setattr(routes, "get_session", lambda _session_id: session)
    _patch_in_memory_execution_state(monkeypatch, expert_teams, run)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda model, provider: (model, provider, False),
    )
    monkeypatch.setattr(
        runtime_adapter.LegacyJournalRuntimeAdapter,
        "resolve_provider_context",
        lambda self, request: {
            "provider": "fallback-provider",
            "model": request.model,
            "preserves_message_roles": True,
            "supports_tools_disabled": True,
        },
    )
    monkeypatch.setattr(
        runtime_adapter.LegacyJournalRuntimeAdapter,
        "start_run",
        lambda self, request: dispatches.append(request),
    )

    payload, status = routes._start_expert_team_execution(tmp_path, run, {})

    assert status == 503
    assert payload["code"] == "runtime_incompatible"
    assert dispatches == []


def test_standalone_runner_runtime_fails_before_any_client_or_reservation_call(
    monkeypatch, tmp_path
):
    from api import expert_teams, routes

    run = _ready_direct_run(tmp_path)
    session = _direct_session(tmp_path)
    client_calls = []
    reservation_calls = []

    class NoStandaloneRunnerProtocol:
        def __getattribute__(self, name):
            if name.startswith("_"):
                return object.__getattribute__(self, name)
            client_calls.append(name)
            raise AssertionError(f"Runner client method must not be called: {name}")

    monkeypatch.setenv("HERMES_WEBUI_RUNTIME_ADAPTER", "runner-local")
    monkeypatch.setattr(routes, "get_session", lambda _session_id: session)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda model, provider: (model, provider, False),
    )
    monkeypatch.setattr(
        routes,
        "_runtime_runner_client_factory",
        lambda: NoStandaloneRunnerProtocol(),
    )
    monkeypatch.setattr(
        expert_teams,
        "reserve_expert_team_execution_start",
        lambda *_args, **_kwargs: reservation_calls.append((_args, _kwargs)),
    )

    payload, status = routes._start_expert_team_execution(tmp_path, run, {})

    assert status == 503
    assert payload["code"] == "runtime_incompatible"
    assert client_calls == []
    assert reservation_calls == []


@pytest.mark.parametrize(
    "damage",
    ["schema", "contract", "messages", "tools_disabled"],
)
def test_standalone_damaged_contract_fails_before_reservation_or_dispatch(
    monkeypatch, tmp_path, damage
):
    from api import expert_teams, routes, runtime_adapter

    run = _ready_direct_run(tmp_path)
    session = _direct_session(tmp_path)
    reservations = []
    dispatches = []
    if damage == "schema":
        run["schema_version"] = 2
    elif damage == "contract":
        run["contract_version"] = "legacy-expert-team/v0"
    else:
        messages = [
            {"role": "system", "content": "strict system"},
            {"role": "user", "content": '{"payload":true}'},
        ]
        if damage == "messages":
            messages = messages[1:]
        monkeypatch.setattr(
            routes,
            "_expert_team_enterprise_gateway_request",
            lambda _workspace, _run: {
                "messages": messages,
                "tools_disabled": damage != "tools_disabled",
            },
        )

    monkeypatch.setenv("HERMES_WEBUI_RUNTIME_ADAPTER", "legacy-journal")
    monkeypatch.setattr(routes, "get_session", lambda _session_id: session)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda model, provider: (model, provider, False),
    )
    _patch_in_memory_execution_state(monkeypatch, expert_teams, run)

    def reserve(*_args, **_kwargs):
        reservations.append((_args, _kwargs))
        reserved = deepcopy(run)
        reserved.update(
            {
                "workflow_state": "starting",
                "execution_start_id": "start-damaged-contract",
                "execution_runtime_adapter": "LegacyJournalRuntimeAdapter",
                "current_stage_attempt_reservation": {
                    "reservation_id": "stage-damaged-contract",
                    "stage_attempt": 1,
                },
            }
        )
        return reserved

    monkeypatch.setattr(
        expert_teams,
        "reserve_expert_team_execution_start",
        reserve,
    )
    monkeypatch.setattr(
        runtime_adapter.LegacyJournalRuntimeAdapter,
        "start_run",
        lambda self, request: dispatches.append(request)
        or runtime_adapter.RunStartResult(
            run_id="must-not-run",
            session_id=request.session_id,
            stream_id="must-not-run",
        ),
    )

    payload, status = routes._start_expert_team_execution(tmp_path, run, {})

    assert status == 503
    assert payload["code"] == "runtime_incompatible"
    assert reservations == []
    assert dispatches == []


def test_standalone_damaged_schema_cannot_bypass_gate_through_system_stage(
    monkeypatch, tmp_path
):
    from api import routes

    run = _ready_direct_run(tmp_path)
    run["schema_version"] = 2
    run["pending_system_stage"] = {"executor": "system", "id": "delivery"}
    dispatches = []
    monkeypatch.setattr(
        routes,
        "_dispatch_expert_team_system_stage",
        lambda *_args, **_kwargs: dispatches.append((_args, _kwargs))
        or ({"ok": True}, 200),
    )

    payload, status = routes._start_expert_team_execution(tmp_path, run, {})

    assert status == 503
    assert payload["code"] == "runtime_incompatible"
    assert dispatches == []


@pytest.mark.parametrize(
    ("standalone_marker", "product_mode", "shortcut"),
    [
        pytest.param({"schema_version": 3}, None, "system_stage", id="schema-v3-missing-mode"),
        pytest.param(
            {"launch_profile_id": "content-work-report"},
            "",
            "enterprise_gateway",
            id="profile-id-empty-mode",
        ),
        pytest.param(
            {"launch_profile_snapshot": _profile()},
            "enterprise",
            "system_stage",
            id="profile-snapshot-enterprise-mode",
        ),
        pytest.param(
            {"document_brief": {"product_mode": "standalone"}},
            None,
            "enterprise_gateway",
            id="brief-contract-missing-mode",
        ),
        pytest.param(
            {"review_policy": {"kind": "local_confirmation"}},
            "",
            "enterprise_gateway",
            id="local-confirmation-empty-mode",
        ),
    ],
)
def test_standalone_v3_shape_with_invalid_product_mode_fails_closed_at_entry(
    monkeypatch,
    tmp_path,
    standalone_marker,
    product_mode,
    shortcut,
):
    from api import expert_teams, routes, runtime_adapter

    run = {
        "schema_version": 2,
        "contract_version": expert_teams.EXPERT_TEAM_CONTRACT_V1,
        "run_id": "run-damaged-product-mode",
        "session_id": "direct-runtime-session",
        "version": 1,
        "workflow_state": "ready_to_generate",
        **deepcopy(standalone_marker),
    }
    if product_mode is not None:
        run["product_mode"] = product_mode
    if shortcut == "system_stage":
        run["pending_system_stage"] = {"executor": "system", "id": "delivery"}

    forbidden_calls = []

    def forbidden(name):
        def fail(*_args, **_kwargs):
            forbidden_calls.append(name)
            raise AssertionError(f"damaged standalone Run reached {name}")

        return fail

    monkeypatch.setattr(routes, "get_session", lambda _session_id: _direct_session(tmp_path))
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda model, provider: (model, provider, False),
    )
    monkeypatch.setattr(
        routes,
        "_dispatch_expert_team_system_stage",
        forbidden("system_stage"),
    )
    monkeypatch.setattr(
        routes,
        "_expert_team_enterprise_gateway_request",
        forbidden("enterprise_gateway"),
    )
    monkeypatch.setattr(
        expert_teams,
        "reserve_expert_team_execution_start",
        forbidden("reservation"),
    )
    monkeypatch.setattr(
        runtime_adapter.LegacyJournalRuntimeAdapter,
        "start_run",
        forbidden("dispatch"),
    )

    payload, status = routes._start_expert_team_execution(tmp_path, run, {})

    assert status == 503
    assert payload["code"] == "runtime_incompatible"
    assert forbidden_calls == []


@pytest.mark.parametrize(
    "run_patch",
    [
        pytest.param(
            {"launch_profile_id": "content-work-report"},
            id="launch-profile-id",
        ),
        pytest.param(
            {"launch_profile_snapshot": _profile()},
            id="launch-profile-snapshot",
        ),
        pytest.param(
            {"document_brief": {"product_mode": "standalone"}},
            id="nested-document-brief-mode",
        ),
        pytest.param(
            {"review_policy": {"kind": "local_confirmation"}},
            id="local-review-policy",
        ),
    ],
)
def test_answer_route_rejects_weak_standalone_markers_from_real_storage_before_mutation(
    monkeypatch,
    tmp_path,
    run_patch,
):
    from api import expert_teams, routes
    from api.expert_teams import storage
    from tests.test_expert_team_v2_runtime import _post

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "session-answer-weak-marker",
            "team_id": "content-creator-team",
            "prompt": "起草工作汇报",
        },
    )
    assert run["schema_version"] == 2
    assert "product_mode" not in run
    run.update(deepcopy(run_patch))
    storage.write_run(tmp_path, run)

    mutation_calls = []
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _session_id: tmp_path)
    monkeypatch.setattr(
        expert_teams,
        "answer_and_reserve_expert_team_execution_start",
        lambda *_args, **_kwargs: mutation_calls.append("reservation")
        or (deepcopy(run), False),
    )
    monkeypatch.setattr(
        expert_teams,
        "answer_expert_team",
        lambda *_args, **_kwargs: mutation_calls.append("answer") or deepcopy(run),
    )

    handler = _post(
        routes,
        "/api/expert-teams/answer",
        {
            "run_id": run["run_id"],
            "session_id": run["session_id"],
            "expected_version": run["version"],
            "idempotency_key": "answer-weak-marker",
            "answers": {"optional_context": ""},
            "skip_optional": True,
        },
    )

    assert mutation_calls == []
    assert handler.status == 503
    assert handler.json_body()["code"] == "runtime_incompatible"


@pytest.mark.parametrize(
    "run_patch",
    [
        pytest.param({"schema_version": 3}, id="schema-v3-missing-mode"),
        pytest.param(
            {"schema_version": 3, "product_mode": "standalone"},
            id="standalone-missing-contract",
        ),
        pytest.param(
            {"schema_version": 2, "product_mode": "standalone"},
            id="schema-v2-standalone",
        ),
    ],
)
def test_answer_route_hides_deep_standalone_corruption_from_real_storage(
    monkeypatch,
    tmp_path,
    run_patch,
):
    from api import expert_teams, routes
    from api.expert_teams import storage
    from tests.test_expert_team_v2_runtime import _post

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "session-answer-deep-corruption",
            "team_id": "content-creator-team",
            "prompt": "起草工作汇报",
        },
    )
    run.update(deepcopy(run_patch))
    storage.run_path(tmp_path, run["run_id"]).write_text(
        json.dumps(run, ensure_ascii=False),
        encoding="utf-8",
    )

    mutation_calls = []
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _session_id: tmp_path)
    monkeypatch.setattr(
        expert_teams,
        "answer_and_reserve_expert_team_execution_start",
        lambda *_args, **_kwargs: mutation_calls.append("reservation")
        or (deepcopy(run), False),
    )
    monkeypatch.setattr(
        expert_teams,
        "answer_expert_team",
        lambda *_args, **_kwargs: mutation_calls.append("answer") or deepcopy(run),
    )

    handler = _post(
        routes,
        "/api/expert-teams/answer",
        {
            "run_id": run["run_id"],
            "session_id": run["session_id"],
            "expected_version": run["version"],
            "idempotency_key": "answer-deep-corruption",
            "answers": {"optional_context": ""},
            "skip_optional": True,
        },
    )
    payload = handler.json_body()

    assert mutation_calls == []
    assert handler.status == 404
    assert "run" not in payload


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        pytest.param({"schema_version": 3}, True, id="schema-v3"),
        pytest.param(
            {"schema_version": 2, "product_mode": "standalone"},
            True,
            id="standalone-mode",
        ),
        pytest.param(
            {"schema_version": 2, "launch_profile_id": "content-work-report"},
            False,
            id="weak-marker-remains-route-owned",
        ),
    ],
)
def test_storage_public_standalone_integrity_classifier(run, expected):
    from api.expert_teams import storage

    assert storage.requires_standalone_run_integrity(run) is expected


def test_write_run_rejects_new_unbound_standalone_without_creating_file(tmp_path):
    from api import expert_teams
    from api.expert_teams import storage

    run = expert_teams.build_standalone_expert_team_run(
        {
            "session_id": "session-new-unbound-standalone",
            "launch_profile_id": "content-work-report",
            "prompt": "起草工作汇报",
            "idempotency_key": "new-unbound-standalone",
        },
        run_id="et-new-unbound-standalone",
    )
    path = storage.run_path(tmp_path, run["run_id"])

    with pytest.raises(
        storage.StartTransactionIntegrityError,
        match="start binding is missing",
    ):
        storage.write_run(tmp_path, run)

    assert not path.exists()


def test_write_run_rejects_legacy_to_unbound_standalone_upgrade_atomically(tmp_path):
    from api import expert_teams
    from api.expert_teams import storage

    legacy = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "session-legacy-upgrade",
            "team_id": "content-creator-team",
            "prompt": "起草旧版工作汇报",
        },
    )
    before = storage.read_run_raw(tmp_path, legacy["run_id"])
    upgraded = expert_teams.build_standalone_expert_team_run(
        {
            "session_id": legacy["session_id"],
            "launch_profile_id": "content-work-report",
            "prompt": "升级为单机专家团工作汇报",
            "idempotency_key": "legacy-unbound-upgrade",
        },
        run_id=legacy["run_id"],
    )

    with pytest.raises(
        storage.StartTransactionIntegrityError,
        match="start binding is missing",
    ):
        storage.write_run(tmp_path, upgraded)

    assert storage.read_run_raw(tmp_path, legacy["run_id"]) == before
    assert storage.read_run(tmp_path, legacy["run_id"]) == before
