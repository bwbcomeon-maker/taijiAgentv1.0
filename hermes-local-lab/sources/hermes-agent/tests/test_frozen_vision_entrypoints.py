from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent import image_runtime


def _generation(
    *,
    public_suffix: str,
    private_suffix: str,
) -> image_runtime.CapabilityRuntimeGeneration:
    return image_runtime.CapabilityRuntimeGeneration(
        vision=(
            1,
            f"vision-fingerprint-{public_suffix}",
            "verified",
            True,
            f"vision-generation-{private_suffix}",
        ),
        image_generation=(
            1,
            f"image-fingerprint-{public_suffix}",
            "verified",
            True,
            f"image-generation-{private_suffix}",
        ),
        stable=True,
    )


def _route(
    generation: image_runtime.CapabilityRuntimeGeneration,
) -> image_runtime.ImageInputRouteDecision:
    return image_runtime.ImageInputRouteDecision(
        schema_version=1,
        fingerprint="public-route-fingerprint",
        status="verified",
        reason_code="auxiliary_vision_verified",
        route="auxiliary_vision",
        mode="text",
        provider="aux-provider",
        model="aux-model",
        _generation=generation,
    )


def _snapshot(
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


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ("cli", "gateway", "tui"))
@pytest.mark.parametrize("transition", ("a_to_b", "a_to_b_to_a"))
async def test_frozen_vision_entrypoints_block_stale_generation_before_io(
    tmp_path,
    monkeypatch,
    entrypoint,
    transition,
):
    from cli import HermesCLI
    from gateway.run import GatewayRunner
    from tools import vision_tools
    from tui_gateway import server as tui_server

    frozen_a = _generation(public_suffix="a", private_suffix="first-a")
    current = (
        _generation(public_suffix="b", private_suffix="b")
        if transition == "a_to_b"
        else _generation(public_suffix="a", private_suffix="second-a")
    )
    route_a = _route(frozen_a)
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"not-read-before-authorization")
    provider_io = []
    main_model_io = []

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda capability="image_generation": _snapshot(current),
    )

    async def unexpected_vision_provider_io(**_kwargs):
        provider_io.append("vision")
        return '{"success": true, "analysis": "unexpected"}'

    monkeypatch.setattr(
        vision_tools,
        "vision_analyze_tool",
        unexpected_vision_provider_io,
    )

    with pytest.raises(RuntimeError, match="capability_caller_stale"):
        if entrypoint == "cli":
            HermesCLI._preprocess_images_with_vision(
                SimpleNamespace(),
                "describe",
                [image_path],
                announce=False,
                route_decision=route_a,
                capability_generation=frozen_a,
            )
        elif entrypoint == "gateway":
            await GatewayRunner._enrich_message_with_vision(
                SimpleNamespace(),
                "describe",
                [str(image_path)],
                route_decision=route_a,
                capability_generation=frozen_a,
            )
        else:
            tui_server._enrich_with_attached_images(
                "describe",
                [str(image_path)],
                route_decision=route_a,
                capability_generation=frozen_a,
            )
        main_model_io.append("main")

    assert provider_io == []
    assert main_model_io == []


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ("cli", "gateway", "tui"))
async def test_frozen_vision_entrypoints_forward_exact_strict_target_binding(
    tmp_path,
    monkeypatch,
    entrypoint,
):
    from cli import HermesCLI
    from gateway.run import GatewayRunner
    from tools import vision_tools
    from tui_gateway import server as tui_server

    generation = _generation(public_suffix="a", private_suffix="a")
    route = _route(generation)
    binding = object()
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"fixture")
    provider_calls = []

    monkeypatch.setattr(
        image_runtime,
        "capture_frozen_vision_request_binding",
        lambda decision, *, generation=None: (
            binding
            if decision is route
            and generation is route.generation
            else (_ for _ in ()).throw(AssertionError("wrong frozen route"))
        ),
    )

    async def capture_vision_provider_call(**kwargs):
        provider_calls.append(kwargs)
        return '{"success": true, "analysis": "visible"}'

    monkeypatch.setattr(
        vision_tools,
        "vision_analyze_tool",
        capture_vision_provider_call,
    )

    if entrypoint == "cli":
        result = await asyncio.to_thread(
            HermesCLI._preprocess_images_with_vision,
            SimpleNamespace(),
            "describe",
            [image_path],
            announce=False,
            route_decision=route,
            capability_generation=generation,
        )
    elif entrypoint == "gateway":
        result = await GatewayRunner._enrich_message_with_vision(
            SimpleNamespace(),
            "describe",
            [str(image_path)],
            route_decision=route,
            capability_generation=generation,
        )
    else:
        result = await asyncio.to_thread(
            tui_server._enrich_with_attached_images,
            "describe",
            [str(image_path)],
            route_decision=route,
            capability_generation=generation,
        )

    assert "visible" in result
    assert len(provider_calls) == 1
    call = provider_calls[0]
    assert call["provider"] == route.provider
    assert call["model"] == route.model
    assert call["strict_target"] is True
    assert call["_runtime_binding"] is binding


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ("cli", "gateway", "tui"))
@pytest.mark.parametrize("transition", ("a_to_b", "a_to_b_to_a"))
async def test_frozen_vision_entrypoints_block_post_binding_drift_before_io(
    tmp_path,
    monkeypatch,
    entrypoint,
    transition,
):
    from agent import auxiliary_client
    from cli import HermesCLI
    from gateway.run import GatewayRunner
    from tools import vision_tools
    from tui_gateway import server as tui_server

    frozen_a = _generation(public_suffix="a", private_suffix="first-a")
    current = (
        _generation(public_suffix="b", private_suffix="b")
        if transition == "a_to_b"
        else _generation(public_suffix="a", private_suffix="second-a")
    )
    route_a = _route(frozen_a)
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

    # Simulate the state transition after the entrypoint has already sealed
    # generation A's binding but before vision_analyze reaches Provider I/O.
    monkeypatch.setattr(
        image_runtime,
        "capture_frozen_vision_request_binding",
        lambda *_args, **_kwargs: binding_a,
    )
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda capability="image_generation": _snapshot(current),
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

    with pytest.raises(RuntimeError, match="vision_analysis_failed"):
        if entrypoint == "cli":
            await asyncio.to_thread(
                HermesCLI._preprocess_images_with_vision,
                SimpleNamespace(),
                "describe",
                [image_path],
                announce=False,
                route_decision=route_a,
                capability_generation=frozen_a,
            )
        elif entrypoint == "gateway":
            await GatewayRunner._enrich_message_with_vision(
                SimpleNamespace(),
                "describe",
                [str(image_path)],
                route_decision=route_a,
                capability_generation=frozen_a,
            )
        else:
            await asyncio.to_thread(
                tui_server._enrich_with_attached_images,
                "describe",
                [str(image_path)],
                route_decision=route_a,
                capability_generation=frozen_a,
            )
        main_model_io.append("main")

    assert provider_io == []
    assert main_model_io == []
