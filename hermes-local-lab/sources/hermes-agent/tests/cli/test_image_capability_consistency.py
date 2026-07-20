from __future__ import annotations

import threading
from types import SimpleNamespace

from agent import image_routing, image_runtime


def _generation(
    *,
    vision_status: str = "verified",
    vision_available: bool = True,
    vision_generation: str = "vision-gen-1",
    image_generation: str = "image-gen-1",
) -> image_runtime.CapabilityRuntimeGeneration:
    return image_runtime.CapabilityRuntimeGeneration(
        vision=(
            1,
            "vision-fp",
            vision_status,
            vision_available,
            vision_generation,
        ),
        image_generation=(
            1,
            "image-fp",
            "verified",
            True,
            image_generation,
        ),
        stable=True,
    )


def _install_runtime(
    monkeypatch,
    *,
    generation: image_runtime.CapabilityRuntimeGeneration,
    vision_reason: str = "",
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
            "reason_code": vision_reason,
            "provider": "vision-provider",
            "model": "vision-model",
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


def test_image_input_route_fails_closed_for_unknown_main_model(monkeypatch):
    generation = _generation()
    _install_runtime(monkeypatch, generation=generation)
    monkeypatch.setattr(
        image_routing,
        "_lookup_supports_vision",
        lambda *_args, **_kwargs: None,
    )

    decision = image_runtime.resolve_image_input_route(
        "unknown-provider",
        "unknown-model",
        {},
    )

    assert decision.mode == "blocked"
    assert decision.status == "configured_unverified"
    assert decision.reason_code == "main_model_capability_unknown"
    assert decision.generation.cache_identity == generation.cache_identity


def test_text_route_requires_current_verified_auxiliary_snapshot(monkeypatch):
    generation = _generation(
        vision_status="configured_unverified",
        vision_available=False,
    )
    _install_runtime(
        monkeypatch,
        generation=generation,
        vision_reason="verification_required",
    )
    monkeypatch.setattr(
        image_routing,
        "_lookup_supports_vision",
        lambda *_args, **_kwargs: False,
    )

    decision = image_runtime.resolve_image_input_route(
        "text-provider",
        "text-model",
        {"agent": {"image_input_mode": "text"}},
    )

    assert decision.mode == "blocked"
    assert decision.status == "configured_unverified"
    assert decision.reason_code == "verification_required"
    assert decision.route == "blocked"


def test_auto_route_uses_verified_auxiliary_for_nonvision_main_model(monkeypatch):
    generation = _generation()
    _install_runtime(monkeypatch, generation=generation)
    monkeypatch.setattr(
        image_routing,
        "_lookup_supports_vision",
        lambda *_args, **_kwargs: False,
    )

    decision = image_runtime.resolve_image_input_route(
        "text-provider",
        "text-model",
        {},
    )

    assert decision.mode == "text"
    assert decision.status == "verified"
    assert decision.reason_code == "auxiliary_vision_verified"
    assert decision.route == "auxiliary_vision"


def test_native_route_uses_same_combined_generation(monkeypatch):
    generation = _generation()
    _install_runtime(monkeypatch, generation=generation)
    monkeypatch.setattr(
        image_routing,
        "_lookup_supports_vision",
        lambda *_args, **_kwargs: True,
    )

    decision = image_runtime.resolve_image_input_route(
        "vision-provider",
        "vision-model",
        {},
    )

    assert decision.mode == "native"
    assert decision.status == "verified"
    assert decision.reason_code == "main_model_supports_vision"
    assert decision.route == "main_model"
    assert decision.generation.cache_identity == generation.cache_identity


def test_known_text_only_main_cannot_force_native_with_stale_override(
    monkeypatch,
):
    generation = _generation(
        vision_status="configured_unverified",
        vision_available=False,
    )
    _install_runtime(
        monkeypatch,
        generation=generation,
        vision_reason="verification_required",
    )
    monkeypatch.setattr(
        image_routing,
        "_lookup_supports_vision",
        lambda *_args, **_kwargs: False,
    )

    decision = image_runtime.resolve_image_input_route(
        "custom-provider",
        "custom-model",
        {
            "agent": {"image_input_mode": "native"},
            "auxiliary": {
                "vision": {
                    "provider": "unverified-auxiliary",
                    "model": "unverified-model",
                }
            },
        },
        generation=generation,
    )

    assert decision.mode == "blocked"
    assert decision.status == "configured_unverified"
    assert decision.reason_code == "verification_required"
    assert decision.route == "blocked"
    assert decision.generation.cache_identity == generation.cache_identity


def test_disabled_auxiliary_does_not_block_native_main_route(
    monkeypatch,
):
    generation = _generation()
    _install_runtime(monkeypatch, generation=generation)
    monkeypatch.setattr(
        image_routing,
        "_lookup_supports_vision",
        lambda *_args, **_kwargs: True,
    )

    decision = image_runtime.resolve_image_input_route(
        "vision-provider",
        "vision-model",
        {
            "agent": {"image_input_mode": "native"},
            "auxiliary": {
                "vision": {
                    "enabled": False,
                    "provider": "vision-provider",
                    "model": "vision-model",
                }
            },
        },
        generation=generation,
    )

    assert decision.mode == "native"
    assert decision.status == "verified"
    assert decision.reason_code == "main_model_supports_vision"
    assert decision.route == "main_model"


def test_disabled_auxiliary_blocks_known_text_only_main_route(monkeypatch):
    generation = _generation()
    _install_runtime(monkeypatch, generation=generation)
    monkeypatch.setattr(
        image_routing,
        "_lookup_supports_vision",
        lambda *_args, **_kwargs: False,
    )

    decision = image_runtime.resolve_image_input_route(
        "text-provider",
        "text-model",
        {
            "auxiliary": {
                "vision": {
                    "enabled": False,
                    "provider": "vision-provider",
                    "model": "vision-model",
                }
            },
        },
        generation=generation,
    )

    assert decision.mode == "blocked"
    assert decision.status == "configured_unverified"
    assert decision.reason_code == "vision_disabled"
    assert decision.route == "blocked"


def test_frozen_auxiliary_route_seals_exact_generation_binding(monkeypatch):
    from agent import auxiliary_client

    generation = _generation()
    _install_runtime(monkeypatch, generation=generation)
    monkeypatch.setattr(
        image_routing,
        "_lookup_supports_vision",
        lambda *_args, **_kwargs: False,
    )
    decision = image_runtime.resolve_image_input_route(
        "text-provider",
        "text-model",
        {},
        generation=generation,
    )
    captures = []

    def capture_binding(
        *,
        authorization_fingerprint="",
        authorization_generation="",
    ):
        captures.append(
            (
                authorization_fingerprint,
                authorization_generation,
            )
        )
        return auxiliary_client.authorize_vision_request_binding(
            auxiliary_client.VisionRequestBinding(
                provider=decision.provider,
                model=decision.model,
                base_url="https://vision.invalid/v1",
                api_key="test-only-secret",
            ),
            authorization_fingerprint=authorization_fingerprint,
            authorization_generation=authorization_generation,
        )

    monkeypatch.setattr(
        auxiliary_client,
        "capture_vision_request_binding",
        capture_binding,
    )

    binding = image_runtime.capture_frozen_vision_request_binding(
        decision,
        generation=generation,
    )

    assert captures == [(generation.vision[1], generation.vision[4])]
    assert binding.provider == decision.provider
    assert binding.model == decision.model
    assert auxiliary_client.vision_request_binding_matches_authorization(
        binding,
        authorization_fingerprint=generation.vision[1],
        authorization_generation=generation.vision[4],
    )


def test_caller_generation_is_published_and_forwarded_for_aba_gate(monkeypatch):
    from agent import agent_runtime_helpers

    generation = _generation(image_generation="image-gen-after-aba")
    monkeypatch.setattr(
        image_runtime,
        "capture_capability_runtime_generation",
        lambda: generation,
    )
    agent = SimpleNamespace(
        _capability_runtime_lock=threading.RLock(),
        _image_runtime_lock=None,
        _capability_runtime_identity=None,
        _registry_tool_names=set(),
        tools=[],
        valid_tool_names=set(),
        enabled_toolsets=["image_gen"],
        disabled_toolsets=None,
        quiet_mode=True,
        _cached_system_prompt="old",
    )

    assert image_runtime.refresh_agent_capability_runtime(
        agent,
        definitions_loader=lambda **_kwargs: [
            {
                "type": "function",
                "function": {
                    "name": "image_generate",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )
    assert (
        agent._image_capability_authorization_generation
        == "image-gen-after-aba"
    )

    captured: dict[str, object] = {}

    def handle_function_call(name, args, task_id, **kwargs):
        captured.update(
            name=name,
            args=args,
            task_id=task_id,
            kwargs=kwargs,
        )
        return "{}"

    monkeypatch.setattr(
        agent_runtime_helpers,
        "_ra",
        lambda: SimpleNamespace(handle_function_call=handle_function_call),
    )
    runtime_agent = SimpleNamespace(
        _memory_manager=None,
        session_id="session-a",
        valid_tool_names={"image_generate"},
        platform=None,
    )
    result = agent_runtime_helpers.invoke_tool(
        runtime_agent,
        "image_generate",
        {"prompt": "test"},
        "task-a",
        "call-a",
        caller_capability_fingerprint="image-fp",
        caller_capability_generation="image-gen-before-aba",
    )

    assert result == "{}"
    assert captured["kwargs"]["caller_capability_fingerprint"] == "image-fp"
    assert (
        captured["kwargs"]["caller_capability_generation"]
        == "image-gen-before-aba"
    )


def _generation_test_cli(monkeypatch, generation_holder):
    import cli as cli_module

    shell = cli_module.HermesCLI(
        model="main-model",
        compact=True,
        max_turns=1,
    )
    shell.provider = "main-provider"
    shell.api_mode = "chat_completions"
    shell.base_url = "https://provider.invalid/v1"
    shell.api_key = "test-only-key"
    shell._ensure_runtime_credentials = lambda: True
    shell._install_tool_callbacks = lambda: None
    shell._ensure_tirith_security = lambda: None
    shell._session_db = SimpleNamespace()
    provider_io = []
    constructed = []

    class FakeAgent:
        def __init__(self, **_kwargs):
            self._capability_runtime_identity = (
                generation_holder["value"].identity
            )
            constructed.append(self)

        def run_conversation(self, **_kwargs):
            provider_io.append("main_model")
            return {"final_response": "unexpected"}

    monkeypatch.setattr(cli_module, "AIAgent", FakeAgent)
    monkeypatch.setattr(
        image_runtime,
        "capture_capability_runtime_generation",
        lambda: generation_holder["value"],
    )
    return shell, constructed, provider_io


def test_cli_agent_reuses_same_generation_and_rebuilds_on_generation_change(
    monkeypatch,
):
    holder = {"value": _generation(image_generation="image-gen-a")}
    shell, constructed, provider_io = _generation_test_cli(
        monkeypatch,
        holder,
    )

    first_route = shell._resolve_turn_agent_config("first")
    assert shell._init_agent(
        model_override=first_route["model"],
        runtime_override=first_route["runtime"],
        request_overrides=first_route["request_overrides"],
        capability_generation=first_route["capability_generation"],
        route_signature=first_route["signature"],
    )
    first_agent = shell.agent
    assert shell._active_agent_route_signature == first_route["signature"]

    same_route = shell._resolve_turn_agent_config("second")
    assert same_route["signature"] == first_route["signature"]
    assert shell._init_agent(
        model_override=same_route["model"],
        runtime_override=same_route["runtime"],
        request_overrides=same_route["request_overrides"],
        capability_generation=same_route["capability_generation"],
        route_signature=same_route["signature"],
    )
    assert shell.agent is first_agent
    assert len(constructed) == 1

    holder["value"] = _generation(image_generation="image-gen-b")
    changed_route = shell._resolve_turn_agent_config("third")
    assert changed_route["signature"] != first_route["signature"]
    if changed_route["signature"] != shell._active_agent_route_signature:
        shell.agent = None
    assert shell._init_agent(
        model_override=changed_route["model"],
        runtime_override=changed_route["runtime"],
        request_overrides=changed_route["request_overrides"],
        capability_generation=changed_route["capability_generation"],
        route_signature=changed_route["signature"],
    )
    assert shell.agent is not first_agent
    assert shell._active_agent_route_signature == changed_route["signature"]
    assert len(constructed) == 2
    assert provider_io == []


def test_cli_agent_construction_generation_race_fails_before_main_io(
    monkeypatch,
):
    holder = {"value": _generation(image_generation="image-gen-a")}
    shell, constructed, provider_io = _generation_test_cli(
        monkeypatch,
        holder,
    )
    frozen_route = shell._resolve_turn_agent_config("race")

    holder["value"] = _generation(image_generation="image-gen-b")
    assert not shell._init_agent(
        model_override=frozen_route["model"],
        runtime_override=frozen_route["runtime"],
        request_overrides=frozen_route["request_overrides"],
        capability_generation=frozen_route["capability_generation"],
        route_signature=frozen_route["signature"],
    )
    assert len(constructed) == 1
    assert shell.agent is None
    assert shell._active_agent_route_signature is None
    assert provider_io == []
