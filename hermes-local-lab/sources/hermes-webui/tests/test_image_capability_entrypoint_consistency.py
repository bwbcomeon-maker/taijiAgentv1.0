from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

import api.streaming as streaming
from api import model_config
from agent import image_routing, image_runtime, tool_executor
from cli import HermesCLI
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from tui_gateway.server import get_tui_image_input_route


def _generation(
    *,
    public_suffix: str = "a",
    private_suffix: str = "a",
    stable: bool = True,
) -> image_runtime.CapabilityRuntimeGeneration:
    return image_runtime.CapabilityRuntimeGeneration(
        vision=(
            1,
            f"vision-fp-{public_suffix}",
            "verified",
            True,
            f"vision-auth-{private_suffix}",
        ),
        image_generation=(
            1,
            f"image-fp-{public_suffix}",
            "verified",
            True,
            f"image-auth-{private_suffix}",
        ),
        stable=stable,
    )


def _install_route_runtime(
    monkeypatch,
    generation: image_runtime.CapabilityRuntimeGeneration,
) -> None:
    monkeypatch.setattr(
        image_runtime,
        "capture_capability_runtime_generation",
        lambda: generation,
    )
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda capability="image_generation": {
            "schema_version": generation.vision[0],
            "fingerprint": generation.vision[1],
            "status": generation.vision[2],
            "available": generation.vision[3],
            "_authorization_generation": generation.vision[4],
            "reason_code": "",
            "provider": "aux-provider",
            "model": "aux-model",
        }
        if capability in {"vision", "image_analysis"}
        else {
            "schema_version": generation.image_generation[0],
            "fingerprint": generation.image_generation[1],
            "status": generation.image_generation[2],
            "available": generation.image_generation[3],
            "_authorization_generation": generation.image_generation[4],
            "reason_code": "",
            "provider": "image-provider",
            "model": "image-model",
        },
    )


def _verified_image_snapshot() -> dict[str, object]:
    state = {
        "schema_version": 1,
        "fingerprint": "image-fp-provider",
        "status": "verified",
        "checked_at": "2026-07-17T00:00:00Z",
        "diagnostic_id": "diag-provider",
        "authorization_generation": 17,
    }
    return {
        "schema_version": 1,
        "fingerprint": state["fingerprint"],
        "status": "verified",
        "available": True,
        "reason_code": "",
        "provider": "custom:test-provider",
        "model": "image-model-v1",
        "_authorization_generation": (
            image_runtime.verification_authorization_generation(
                state,
                expected_fingerprint=str(state["fingerprint"]),
                capability="image_generation",
            )
        ),
    }


def _auxiliary_route(
    generation: image_runtime.CapabilityRuntimeGeneration,
) -> image_runtime.ImageInputRouteDecision:
    return image_runtime.ImageInputRouteDecision(
        schema_version=1,
        fingerprint="public-auxiliary-route",
        status="verified",
        reason_code="auxiliary_vision_verified",
        route="auxiliary_vision",
        mode="text",
        provider="aux-provider",
        model="aux-model",
        _generation=generation,
    )


def _vision_snapshot(
    generation: image_runtime.CapabilityRuntimeGeneration,
) -> dict[str, object]:
    return {
        "schema_version": generation.vision[0],
        "fingerprint": generation.vision[1],
        "status": generation.vision[2],
        "available": generation.vision[3],
        "_authorization_generation": generation.vision[4],
        "reason_code": "",
        "provider": "aux-provider",
        "model": "aux-model",
    }


def test_all_entrypoints_share_capability_snapshot_and_reason_codes(
    monkeypatch,
):
    generation = _generation()
    _install_route_runtime(monkeypatch, generation)
    monkeypatch.setattr(
        image_routing,
        "_lookup_supports_vision",
        lambda *_args, **_kwargs: True,
    )

    provider_io_calls = []
    monkeypatch.setattr(
        image_runtime,
        "emit_capability_route_event_at_provider_io",
        lambda *_args, **_kwargs: provider_io_calls.append("unexpected"),
    )

    cfg = {
        "model": {
            "provider": "main-provider",
            "default": "main-model",
        }
    }
    cli = SimpleNamespace(provider="main-provider", model="main-model")
    cli_route = HermesCLI._resolve_image_input_route(
        cli,
        cfg,
        generation=generation,
    )

    from agent import auxiliary_client
    from hermes_cli import config as hermes_config

    monkeypatch.setattr(
        auxiliary_client,
        "_read_main_provider",
        lambda: "main-provider",
    )
    monkeypatch.setattr(
        auxiliary_client,
        "_read_main_model",
        lambda: "main-model",
    )
    monkeypatch.setattr(hermes_config, "load_config", lambda: cfg)
    gateway_route = GatewayRunner._decide_image_input_route(
        SimpleNamespace(),
        generation=generation,
    )
    tui_route = get_tui_image_input_route(
        cfg,
        provider="main-provider",
        model="main-model",
        generation=generation,
    )
    webui_route = streaming.get_webui_image_input_route(
        cfg,
        generation=generation,
    )

    routes = (cli_route, gateway_route, tui_route, webui_route)
    public_snapshots = {
        (
            route.schema_version,
            route.fingerprint,
            route.status,
            route.reason_code,
            route.route,
            route.mode,
        )
        for route in routes
    }
    assert public_snapshots == {
        (
            1,
            cli_route.fingerprint,
            "verified",
            "main_model_supports_vision",
            "main_model",
            "native",
        )
    }
    assert all(
        route.generation.cache_identity == generation.cache_identity
        for route in routes
    )
    assert provider_io_calls == []


@pytest.mark.parametrize(
    "current_generation",
    (
        _generation(public_suffix="b", private_suffix="b"),
        _generation(public_suffix="a", private_suffix="second-a"),
    ),
    ids=("a_to_b", "a_to_b_to_a"),
)
def test_webui_frozen_vision_binding_blocks_stale_route_before_all_io(
    tmp_path,
    monkeypatch,
    current_generation,
):
    from tools import vision_tools

    frozen_a = _generation(public_suffix="a", private_suffix="first-a")
    route_a = _auxiliary_route(frozen_a)
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"not-read-before-authorization")
    provider_io = []
    main_model_io = []

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda capability="image_generation": _vision_snapshot(
            current_generation
        ),
    )

    async def unexpected_vision_provider_io(**_kwargs):
        provider_io.append("vision")
        return json.dumps({"success": True, "analysis": "unexpected"})

    monkeypatch.setattr(
        vision_tools,
        "vision_analyze_tool",
        unexpected_vision_provider_io,
    )

    with pytest.raises(RuntimeError, match="capability_caller_stale"):
        streaming._enrich_webui_images_with_vision(
            "describe",
            [
                {
                    "name": image_path.name,
                    "path": str(image_path),
                    "mime": "image/png",
                }
            ],
            route_decision=route_a,
            capability_generation=frozen_a,
        )
        main_model_io.append("main")

    assert provider_io == []
    assert main_model_io == []


@pytest.mark.parametrize(
    "current_generation",
    (
        _generation(public_suffix="b", private_suffix="b"),
        _generation(public_suffix="a", private_suffix="second-a"),
    ),
    ids=("a_to_b", "a_to_b_to_a"),
)
def test_webui_frozen_vision_binding_blocks_post_binding_drift_before_io(
    tmp_path,
    monkeypatch,
    current_generation,
):
    from agent import auxiliary_client
    from tools import vision_tools

    frozen_a = _generation(public_suffix="a", private_suffix="first-a")
    route_a = _auxiliary_route(frozen_a)
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"not-read-before-authorization")
    provider_io = []
    main_model_io = []
    binding_a = auxiliary_client.authorize_vision_request_binding(
        auxiliary_client.VisionRequestBinding(
            provider=route_a.provider,
            model=route_a.model,
            base_url="https://vision.invalid/v1",
            api_key="test-only-secret",
        ),
        authorization_fingerprint=frozen_a.vision[1],
        authorization_generation=frozen_a.vision[4],
    )

    monkeypatch.setattr(
        image_runtime,
        "capture_frozen_vision_request_binding",
        lambda *_args, **_kwargs: binding_a,
    )
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda capability="image_generation": _vision_snapshot(
            current_generation
        ),
    )

    async def unexpected_provider_io(**_kwargs):
        provider_io.append("vision")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="unexpected")
                )
            ]
        )

    monkeypatch.setattr(
        vision_tools,
        "async_call_llm",
        unexpected_provider_io,
    )

    with pytest.raises(streaming.WebUIChatInputError) as exc_info:
        streaming._enrich_webui_images_with_vision(
            "describe",
            [
                {
                    "name": image_path.name,
                    "path": str(image_path),
                    "mime": "image/png",
                }
            ],
            route_decision=route_a,
            capability_generation=frozen_a,
        )
        main_model_io.append("main")

    assert exc_info.value.payload["type"] == "vision_analysis_error"
    assert provider_io == []
    assert main_model_io == []


@pytest.mark.asyncio
async def test_webui_and_gateway_agent_cache_identity_tracks_capability_snapshot(
    monkeypatch,
):
    first_a = _generation(public_suffix="a", private_suffix="first-a")
    middle_b = _generation(public_suffix="b", private_suffix="b")
    second_a = _generation(public_suffix="a", private_suffix="second-a")

    assert first_a.vision[:4] == second_a.vision[:4]
    assert first_a.image_generation[:4] == second_a.image_generation[:4]
    assert first_a.cache_identity != middle_b.cache_identity
    assert first_a.cache_identity != second_a.cache_identity
    assert (
        streaming.image_capability_runtime_fingerprint(first_a)
        != streaming.image_capability_runtime_fingerprint(second_a)
    )

    def unexpected_recapture():
        raise AssertionError("an explicit per-turn generation must not be recaptured")

    monkeypatch.setattr(
        image_runtime,
        "capture_capability_runtime_generation",
        unexpected_recapture,
    )
    history_generation = {"value": first_a}
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda capability="image_generation": {
            "schema_version": history_generation["value"].vision[0],
            "fingerprint": history_generation["value"].vision[1],
            "status": history_generation["value"].vision[2],
            "available": history_generation["value"].vision[3],
            "_authorization_generation": (
                history_generation["value"].vision[4]
            ),
            "reason_code": "",
            "provider": "aux-provider",
            "model": "aux-model",
        },
    )
    monkeypatch.setattr(
        image_routing,
        "_lookup_supports_vision",
        lambda *_args, **_kwargs: True,
    )
    history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "remember this image"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AAAA"},
                },
            ],
        }
    ]
    for frozen_generation in (first_a, middle_b, second_a):
        history_generation["value"] = frozen_generation
        sanitized = streaming._sanitize_messages_for_api(
            history,
            cfg={
                "model": {
                    "provider": "main-provider",
                    "default": "main-model",
                }
            },
            capability_generation=frozen_generation,
        )
        assert sanitized[0]["content"][1]["type"] == "image_url"

    seen_route_generations = []
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    runner.adapters = {}
    runner._decide_image_input_route = (
        lambda **kwargs: (
            seen_route_generations.append(kwargs.get("generation"))
            or SimpleNamespace(
                mode="native",
                reason_code="main_model_supports_vision",
            )
        )
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-a",
        chat_type="private",
    )
    event = MessageEvent(
        text="inspect",
        message_type=MessageType.PHOTO,
        source=source,
        media_urls=["/tmp/image-a.png"],
        media_types=["image/png"],
    )
    await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
        capability_generation=first_a,
    )
    assert seen_route_generations == [first_a]

    first_cache = GatewayRunner._extract_cache_busting_config(
        {},
        capability_generation=first_a,
    )
    second_cache = GatewayRunner._extract_cache_busting_config(
        {},
        capability_generation=second_a,
    )
    assert (
        first_cache["image.capability_runtime_generation"]
        == first_a.cache_identity
    )
    assert (
        second_cache["image.capability_runtime_generation"]
        == second_a.cache_identity
    )
    assert (
        GatewayRunner._agent_config_signature(
            "main-model",
            {},
            [],
            "",
            cache_keys=first_cache,
        )
        != GatewayRunner._agent_config_signature(
            "main-model",
            {},
            [],
            "",
            cache_keys=second_cache,
        )
    )

    cached_agent = SimpleNamespace(
        _capability_runtime_identity=first_a.identity
    )
    assert streaming._agent_matches_capability_generation(
        cached_agent,
        first_a,
    )
    assert not streaming._agent_matches_capability_generation(
        cached_agent,
        second_a,
    )
    assert GatewayRunner._agent_matches_capability_cache(
        cached_agent,
        first_cache,
    )
    assert not GatewayRunner._agent_matches_capability_cache(
        cached_agent,
        second_cache,
    )


@pytest.mark.parametrize(
    ("retry_anchor", "cache_marker", "run_marker"),
    [
        (
            "[webui] self-heal: retrying stream after credential refresh",
            "_SAC[session_id] = (agent, _agent_sig)",
            "_heal_result = agent.run_conversation(",
        ),
        (
            "[webui] self-heal (except path): retrying stream after credential refresh",
            "_SAC2[session_id] = (_heal_agent, _agent_sig)",
            "_heal_result = _heal_agent.run_conversation(",
        ),
    ],
    ids=("result-error-path", "exception-path"),
)
def test_webui_self_heal_generation_race_fails_before_cache_and_main_io(
    retry_anchor,
    cache_marker,
    run_marker,
):
    frozen = _generation(public_suffix="a", private_suffix="a")
    changed = _generation(public_suffix="b", private_suffix="b")
    main_io = []

    class ChangedGenerationAgent:
        _capability_runtime_identity = changed.identity

        def run_conversation(self, **_kwargs):
            main_io.append("unexpected")

    with pytest.raises(
        RuntimeError,
        match="capability runtime changed during agent construction",
    ):
        streaming._require_agent_capability_generation(
            ChangedGenerationAgent(),
            frozen,
        )
    assert main_io == []

    source = inspect.getsource(streaming._run_agent_streaming)
    anchor_index = source.index(retry_anchor)
    guard_index = source.index(
        "_require_agent_capability_generation(",
        anchor_index,
    )
    cache_index = source.index(cache_marker, guard_index)
    run_index = source.index(run_marker, cache_index)
    assert anchor_index < guard_index < cache_index < run_index


def test_capability_route_event_matches_decision_and_actual_tool_execution(
    monkeypatch,
):
    import model_tools

    snapshot = _verified_image_snapshot()
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda _capability="image_generation": dict(snapshot),
    )

    provider_io_calls = []

    def dispatch(name, args, **kwargs):
        provider_io_calls.append(
            (name, dict(args), dict(kwargs))
        )
        decision = image_runtime.build_capability_route_decision(
            "image_generation",
            snapshot=snapshot,
            route="provider",
            tool_call_id=kwargs["tool_call_id"],
        )
        assert image_runtime.emit_capability_route_event_at_provider_io(
            decision
        )
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", dispatch)
    emitted = []
    agent = SimpleNamespace(
        tool_progress_callback=lambda *args, **kwargs: emitted.append(
            (args, kwargs)
        )
    )

    with tool_executor._capability_route_scope(
        agent,
        "image_generate",
        "call-provider-1",
    ):
        assert emitted == []
        result = model_tools.handle_function_call(
            "image_generate",
            {"prompt": "test image"},
            task_id="task-1",
            tool_call_id="call-provider-1",
            skip_pre_tool_call_hook=True,
            caller_capability_fingerprint=str(snapshot["fingerprint"]),
            caller_capability_generation=str(
                snapshot["_authorization_generation"]
            ),
        )

    assert json.loads(result) == {"ok": True}
    assert len(provider_io_calls) == 1
    assert provider_io_calls[0][2]["tool_call_id"] == "call-provider-1"
    assert len(emitted) == 1
    callback_args, callback_kwargs = emitted[0]
    assert callback_args == (
        "capability_route",
        "image_generate",
        None,
        None,
    )
    public_event = callback_kwargs["route_event"]
    assert callback_kwargs["tool_call_id"] == "call-provider-1"
    assert public_event == {
        "schema_version": 1,
        "capability": "image_generation",
        "status": "verified",
        "reason_code": "ready",
        "route": "provider",
        "provider": "custom:test-provider",
        "model": "image-model-v1",
        "tool_call_id": "call-provider-1",
    }
    assert (
        image_runtime.project_capability_route_progress_event(public_event)
        == public_event
    )
    assert "_authorization_generation" not in repr(emitted)
    assert "authorization_fingerprint" not in repr(emitted)


def test_config_transaction_propagates_invalidation_without_losing_state(
    monkeypatch,
):
    committed_state = {
        "ok": True,
        "provider": "custom:test-provider",
        "model": "image-model-v1",
        "revision": 4,
    }
    frozen_state = dict(committed_state)
    calls = []
    vision_token = model_config._VerificationInvalidationToken(
        capability="vision",
        profile="default",
        generation=4,
        state_identity="vision-state-4",
    )
    image_token = model_config._VerificationInvalidationToken(
        capability="image",
        profile="default",
        generation=4,
        state_identity="image-state-4",
    )

    def post_commit_hook(mutation, **kwargs):
        calls.append((mutation, kwargs))
        return [
            "models_cache_refresh_pending",
            "models_cache_refresh_pending",
        ]

    monkeypatch.setattr(
        model_config,
        "_run_durable_mutation_post_commit_hook",
        post_commit_hook,
    )
    warnings = model_config._invoke_durable_mutation_post_commit(
        "set_custom_image_provider_config",
        invalidate_vision=True,
        invalidate_image=True,
        vision_invalidation_token=vision_token,
        image_invalidation_token=image_token,
    )
    response = model_config._merge_post_commit_warnings(
        dict(committed_state),
        warnings,
    )

    assert calls == [
        (
            "set_custom_image_provider_config",
            {
                "invalidate_vision": True,
                "invalidate_image": True,
                "vision_invalidation_token": vision_token,
                "image_invalidation_token": image_token,
            },
        )
    ]
    assert warnings == ["models_cache_refresh_pending"]
    assert response["ok"] is True
    assert response["provider"] == frozen_state["provider"]
    assert response["model"] == frozen_state["model"]
    assert response["revision"] == frozen_state["revision"]
    assert response["refresh_pending"] is True
    assert response["warnings"] == ["models_cache_refresh_pending"]
    assert committed_state == frozen_state

    monkeypatch.setattr(
        model_config,
        "_run_durable_mutation_post_commit_hook",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("post-commit refresh unavailable")
        ),
    )
    degraded_warnings = model_config._invoke_durable_mutation_post_commit(
        "set_custom_image_provider_config",
    )
    degraded_response = model_config._merge_post_commit_warnings(
        dict(committed_state),
        degraded_warnings,
    )
    assert degraded_warnings == ["durable_mutation_refresh_pending"]
    assert degraded_response["ok"] is True
    assert degraded_response["provider"] == frozen_state["provider"]
    assert degraded_response["revision"] == frozen_state["revision"]
    assert committed_state == frozen_state
