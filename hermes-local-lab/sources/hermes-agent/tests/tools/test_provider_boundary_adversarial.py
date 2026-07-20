from __future__ import annotations

import base64
import importlib
import json
import logging
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0l"
    "EQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _write_cached_png(home: Path, name: str) -> Path:
    image = home / "cache" / "images" / name
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(_PNG_1X1)
    return image


def _verified_snapshot(
    capability: str,
    *,
    fingerprint: str,
    provider: str,
    model: str,
    status: str = "verified",
    authorization_generation: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "fingerprint": fingerprint,
        "_authorization_generation": (
            authorization_generation
            or f"authorization-generation:{fingerprint}"
        ),
        "status": status,
        "available": status == "verified",
        "reason_code": "" if status == "verified" else "verification_in_progress",
        "capability": capability,
        "provider": provider,
        "model": model,
    }


def _image_binding(
    *,
    fingerprint: str,
    provider: str = "codex",
    model: str = "gpt-image-2",
    secret: str = "pinned-image-secret",
    endpoint: str = "https://pinned-image.example.test/v1",
    provider_config: dict[str, Any] | None = None,
    authorization_generation: str | None = None,
):
    from agent.image_gen_verification import (
        ImageGenRequestBinding,
        authorize_image_gen_request_binding,
    )

    binding = ImageGenRequestBinding(
        provider=provider,
        model=model,
        api_key=secret,
        runtime_identity={
            "transport": "openai_images",
            "endpoint": endpoint,
            "identity_supported": True,
            "endpoint_resolved": True,
        },
        _provider_config=provider_config or {},
    )
    return authorize_image_gen_request_binding(
        binding,
        authorization_fingerprint=fingerprint,
        authorization_generation=(
            authorization_generation
            or f"authorization-generation:{fingerprint}"
        ),
    )


def _handle_image_generate_with_gate(
    image_generation_tool: Any,
    arguments: dict[str, Any],
    **kwargs: Any,
) -> str:
    """Invoke one Provider-boundary test through a legitimate one-shot gate."""
    from agent.image_intent import (
        begin_image_generation_task,
        cleanup_image_generation_task,
    )

    token = uuid.uuid4().hex
    task_id = f"provider-boundary-{token}"
    owner = f"provider-boundary-owner-{token}"
    assert (
        begin_image_generation_task(
            task_id,
            allow_generation=True,
            owner_token=owner,
        )
        is None
    )
    try:
        return image_generation_tool._handle_image_generate(
            arguments,
            image_generation_task_id=task_id,
            image_generation_gate_owner=owner,
            **kwargs,
        )
    finally:
        cleanup_image_generation_task(task_id, owner_token=owner)


def _vision_binding(
    *,
    fingerprint: str,
    provider: str = "alibaba",
    model: str = "qwen3-vl-plus",
    secret: str = "pinned-vision-secret",
    endpoint: str = "https://dashscope.example.test/v1",
    sealed: bool = True,
    authorization_generation: str | None = None,
):
    from agent.auxiliary_client import (
        VisionRequestBinding,
        authorize_vision_request_binding,
    )

    binding = VisionRequestBinding(
        provider=provider,
        model=model,
        base_url=endpoint,
        api_key=secret,
    )
    if sealed:
        authorized = authorize_vision_request_binding(
            binding,
            authorization_fingerprint=fingerprint,
            authorization_generation=(
                authorization_generation
                or f"authorization-generation:{fingerprint}"
            ),
        )
        return authorized
    object.__setattr__(
        binding,
        "_authorization_fingerprint",
        str(fingerprint or ""),
    )
    object.__setattr__(
        binding,
        "_authorization_generation",
        authorization_generation
        or f"authorization-generation:{fingerprint}",
    )
    return binding


def test_image_aba_snapshot_rejects_binding_from_intermediate_generation(
    monkeypatch,
):
    """Snapshot A -> binding B -> current A must not reach Provider I/O."""
    from agent import image_gen_registry, image_runtime
    from agent.image_gen_provider import ImageGenProvider
    from hermes_cli import plugins as plugins_module
    from tools import image_generation_tool

    snapshot_a = _verified_snapshot(
        "image_generation",
        fingerprint="image-generation-A",
        provider="codex",
        model="gpt-image-2",
    )
    binding_b = _image_binding(fingerprint="image-generation-B")
    provider_io: list[dict[str, Any]] = []

    class Provider(ImageGenProvider):
        _supports_pinned_image_request_binding = True

        @property
        def name(self) -> str:
            return "codex"

        def generate(self, prompt, aspect_ratio="landscape", **kwargs):
            provider_io.append(dict(kwargs))
            return {
                "success": True,
                "provider": self.name,
                "image": "/tmp/aba-should-not-run.png",
            }

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(snapshot_a),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kwargs: binding_b,
    )
    monkeypatch.setattr(
        plugins_module,
        "_ensure_plugins_discovered",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        image_gen_registry,
        "get_provider",
        lambda name: Provider() if name == "codex" else None,
    )

    result = json.loads(
        _handle_image_generate_with_gate(
            image_generation_tool,
            {"prompt": "aba image"},
            caller_capability_fingerprint=snapshot_a["fingerprint"],
            caller_capability_generation=snapshot_a[
                "_authorization_generation"
            ],
        )
    )

    assert provider_io == []
    assert result["success"] is False
    assert result["status"] == "blocked"
    assert result["error_code"] == "capability_binding_mismatch"


def test_image_old_a_binding_cannot_revive_after_persisted_a_b_a(
    monkeypatch,
):
    """A re-verification changes generation even if material returns to A."""
    from agent import image_gen_registry, image_runtime
    from agent.image_gen_provider import ImageGenProvider
    from hermes_cli import plugins as plugins_module
    from tools import image_generation_tool

    fingerprint = "image-material-A"
    old_binding = _image_binding(
        fingerprint=fingerprint,
        authorization_generation="image-generation-A1",
    )
    final_snapshot = _verified_snapshot(
        "image_generation",
        fingerprint=fingerprint,
        provider=old_binding.provider,
        model=old_binding.model,
        authorization_generation="image-generation-A3",
    )
    provider_io: list[dict[str, Any]] = []

    class Provider(ImageGenProvider):
        _supports_pinned_image_request_binding = True

        @property
        def name(self) -> str:
            return "codex"

        def generate(self, prompt, aspect_ratio="landscape", **kwargs):
            provider_io.append(dict(kwargs))
            return {
                "success": True,
                "provider": self.name,
                "image": "/tmp/old-a-should-not-run.png",
            }

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(final_snapshot),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kwargs: old_binding,
    )
    monkeypatch.setattr(
        plugins_module,
        "_ensure_plugins_discovered",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        image_gen_registry,
        "get_provider",
        lambda name: Provider() if name == "codex" else None,
    )

    result = json.loads(
        _handle_image_generate_with_gate(
            image_generation_tool,
            {"prompt": "reject revived A binding"},
            caller_capability_fingerprint=fingerprint,
            caller_capability_generation=final_snapshot[
                "_authorization_generation"
            ],
        )
    )

    assert provider_io == []
    assert result["success"] is False
    assert result["status"] == "blocked"
    assert result["error_code"] == "capability_binding_mismatch"


def test_image_dispatch_passes_reauth_guard_to_provider_io_seam(
    monkeypatch,
    tmp_path,
):
    from agent import image_gen_registry, image_runtime
    from agent.image_gen_provider import ImageGenProvider
    from hermes_cli import plugins as plugins_module
    from tools import image_generation_tool

    fingerprint = "image-provider-io-guard-generation"
    snapshot = _verified_snapshot(
        "image_generation",
        fingerprint=fingerprint,
        provider="codex",
        model="gpt-image-2",
    )
    binding = _image_binding(fingerprint=fingerprint)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cached_image = _write_cached_png(tmp_path, "guarded-provider.png")
    provider_io: list[str] = []

    class Provider(ImageGenProvider):
        _supports_pinned_image_request_binding = True

        @property
        def name(self) -> str:
            return "codex"

        def generate(self, prompt, aspect_ratio="landscape", **kwargs):
            reauth_guard = kwargs.get("_reauth_guard")
            assert callable(reauth_guard)
            reauth_guard()
            provider_io.append("request")
            return {
                "success": True,
                "provider": self.name,
                "image": str(cached_image),
            }

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(snapshot),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kwargs: binding,
    )
    monkeypatch.setattr(
        plugins_module,
        "_ensure_plugins_discovered",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        image_gen_registry,
        "get_provider",
        lambda name: Provider() if name == "codex" else None,
    )

    result = json.loads(
        _handle_image_generate_with_gate(
            image_generation_tool,
            {"prompt": "guard provider io"},
            caller_capability_fingerprint=fingerprint,
            caller_capability_generation=snapshot[
                "_authorization_generation"
            ],
        )
    )

    assert result["success"] is True
    assert provider_io == ["request"]


@pytest.mark.parametrize(
    "mutation",
    ["bare_same_fingerprint", "secret", "endpoint", "provider_config"],
)
def test_forged_or_tampered_image_binding_fails_before_provider_io(
    monkeypatch,
    mutation,
):
    from agent import image_gen_registry, image_runtime
    from agent.image_gen_provider import ImageGenProvider
    from agent.image_gen_verification import ImageGenRequestBinding
    from hermes_cli import plugins as plugins_module
    from tools import image_generation_tool

    fingerprint = "image-material-seal-generation"
    secret = "sealed-image-secret"
    endpoint = "https://sealed-image.example.test/v1"
    binding = _image_binding(
        fingerprint=fingerprint,
        secret=secret,
        endpoint=endpoint,
        provider_config={"id": "sealed-provider"},
    )
    if mutation == "bare_same_fingerprint":
        binding = ImageGenRequestBinding(
            provider=binding.provider,
            model=binding.model,
            api_key=binding.api_key,
            runtime_identity=dict(binding.runtime_identity),
            _authorization_fingerprint=fingerprint,
            _provider_config={"id": "sealed-provider"},
        )
    elif mutation == "secret":
        object.__setattr__(binding, "api_key", "tampered-image-secret")
    elif mutation == "endpoint":
        object.__setattr__(
            binding,
            "runtime_identity",
            MappingProxyType(
                {
                    **dict(binding.runtime_identity),
                    "endpoint": "https://tampered-image.example.test/v1",
                }
            ),
        )
    else:
        object.__setattr__(
            binding,
            "_provider_config",
            MappingProxyType({"id": "tampered-provider"}),
        )

    snapshot = _verified_snapshot(
        "image_generation",
        fingerprint=fingerprint,
        provider=binding.provider,
        model=binding.model,
    )
    provider_io: list[dict[str, Any]] = []

    class Provider(ImageGenProvider):
        _supports_pinned_image_request_binding = True

        @property
        def name(self) -> str:
            return "codex"

        def generate(self, prompt, aspect_ratio="landscape", **kwargs):
            provider_io.append(dict(kwargs))
            return {
                "success": True,
                "provider": self.name,
                "image": "/tmp/forged-image-should-not-run.png",
            }

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(snapshot),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kwargs: binding,
    )
    monkeypatch.setattr(
        plugins_module,
        "_ensure_plugins_discovered",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        image_gen_registry,
        "get_provider",
        lambda name: Provider() if name == "codex" else None,
    )

    result = json.loads(
        _handle_image_generate_with_gate(
            image_generation_tool,
            {"prompt": "reject forged image binding"},
            caller_capability_fingerprint=fingerprint,
            caller_capability_generation=snapshot[
                "_authorization_generation"
            ],
        )
    )

    assert provider_io == []
    assert result["success"] is False
    assert result["status"] == "blocked"
    assert result["error_code"] == "capability_binding_mismatch"


def test_image_probe_guard_accepts_only_same_persisted_verifying_generation(
    monkeypatch,
):
    from agent import image_runtime
    from agent.image_gen_verification import (
        ImageGenRequestAuthorizationError,
        build_image_gen_request_reauth_guard,
    )

    fingerprint = "image-probe-material"
    binding = _image_binding(
        fingerprint=fingerprint,
        authorization_generation="image-probe-generation-1",
    )
    expected = _verified_snapshot(
        "image_generation",
        fingerprint=fingerprint,
        provider=binding.provider,
        model=binding.model,
        status="verifying",
        authorization_generation="image-probe-generation-1",
    )
    current = dict(expected)
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(current),
    )
    guard = build_image_gen_request_reauth_guard(
        binding,
        expected_snapshot=expected,
    )

    guard()
    current["_authorization_generation"] = "image-probe-generation-2"

    with pytest.raises(
        ImageGenRequestAuthorizationError,
        match="capability_caller_stale",
    ):
        guard()

    current.clear()
    current.update(expected)
    current["status"] = "verified"
    current["available"] = True

    with pytest.raises(
        ImageGenRequestAuthorizationError,
        match="capability_caller_stale",
    ):
        guard()


def test_custom_image_uses_only_deep_frozen_binding_material(
    monkeypatch,
    tmp_path,
):
    """Endpoint, secret, network policy and timeout come from one immutable binding."""
    from agent import custom_image_providers, image_runtime
    from tools import image_generation_tool

    provider = "custom:router"
    model = "router-image-v1"
    fingerprint = "custom-image-pinned-generation"
    endpoint = "https://pinned-custom-image.example.test/v1"
    secret = "PINNED-CUSTOM-IMAGE-SECRET"
    provider_config = {
        "id": "router",
        "name": "Pinned router",
        "base_url": endpoint,
        "credential_ref": "custom-router-key",
        "allow_custom_model_id": False,
        "models": [model],
        "default_model": model,
        "size_map": {
            "landscape": "1400x900",
            "square": "900x900",
            "portrait": "900x1400",
        },
        "response_format": "b64_json",
        "timeout_seconds": 37,
        "network_scope": "public_direct",
        "trusted_proxy_profile": "",
    }
    binding = _image_binding(
        fingerprint=fingerprint,
        provider=provider,
        model=model,
        secret=secret,
        endpoint=f"{endpoint}/images/generations",
        provider_config=provider_config,
    )
    snapshot = _verified_snapshot(
        "image_generation",
        fingerprint=fingerprint,
        provider=provider,
        model=model,
    )
    outbound: list[dict[str, Any]] = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cached_image = _write_cached_png(tmp_path, "pinned-custom-image.png")

    @contextmanager
    def request_pinned_https(**kwargs):
        outbound.append(dict(kwargs))
        yield SimpleNamespace(status_code=200)

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(snapshot),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kwargs: binding,
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_load_image_gen_full_config",
        lambda: (_ for _ in ()).throw(
            AssertionError("ambient custom image config was re-read")
        ),
    )
    monkeypatch.setattr(
        custom_image_providers,
        "request_pinned_https",
        request_pinned_https,
    )
    monkeypatch.setattr(
        custom_image_providers,
        "read_bounded_json",
        lambda _response: {
            "data": [
                {
                    "b64_json": base64.b64encode(b"pinned-image").decode(
                        "ascii"
                    )
                }
            ]
        },
    )
    monkeypatch.setattr(
        custom_image_providers,
        "save_b64_image",
        lambda *_args, **_kwargs: str(cached_image),
    )

    result = json.loads(
        _handle_image_generate_with_gate(
            image_generation_tool,
            {"prompt": "pinned custom image", "aspect_ratio": "portrait"},
            caller_capability_fingerprint=fingerprint,
            caller_capability_generation=snapshot[
                "_authorization_generation"
            ],
        )
    )

    assert result["success"] is True
    assert len(outbound) == 1
    request = outbound[0]
    assert request["url"] == f"{endpoint}/images/generations"
    assert request["network_scope"] == "public_direct"
    assert request["timeout"] == 37
    assert request["headers"]["Authorization"] == f"Bearer {secret}"
    assert request["json_body"]["model"] == model
    assert request["json_body"]["size"] == "900x1400"
    assert request["json_body"]["response_format"] == "b64_json"


def test_custom_image_transport_exception_redacts_bound_secret_and_endpoint(
    monkeypatch,
    caplog,
):
    from agent import custom_image_providers, image_runtime
    from tools import image_generation_tool

    provider = "custom:router"
    model = "router-image-v1"
    fingerprint = "custom-image-transport-redaction"
    secret = "SENTINEL-CUSTOM-IMAGE-SECRET-DO-NOT-LEAK"
    endpoint = "https://sentinel-custom-image.example.test/private"
    binding = _image_binding(
        fingerprint=fingerprint,
        provider=provider,
        model=model,
        secret=secret,
        endpoint=f"{endpoint}/images/generations",
        provider_config={
            "id": "router",
            "name": "Router",
            "base_url": endpoint,
            "credential_ref": "custom-router-key",
            "allow_custom_model_id": False,
            "models": [model],
            "default_model": model,
            "size_map": {},
            "response_format": "auto",
            "timeout_seconds": 30,
            "network_scope": "public_direct",
            "trusted_proxy_profile": "",
        },
    )
    snapshot = _verified_snapshot(
        "image_generation",
        fingerprint=fingerprint,
        provider=provider,
        model=model,
    )

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(snapshot),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kwargs: binding,
    )
    monkeypatch.setattr(
        custom_image_providers,
        "request_pinned_https",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError(f"{secret} {endpoint}")
        ),
    )

    with caplog.at_level(logging.DEBUG):
        result_text = _handle_image_generate_with_gate(
            image_generation_tool,
            {"prompt": "redact custom transport failure"},
            caller_capability_fingerprint=fingerprint,
            caller_capability_generation=snapshot[
                "_authorization_generation"
            ],
        )
    result = json.loads(result_text)
    evidence = f"{result_text}\n{caplog.text}"

    assert result["success"] is False
    assert result["error_code"] == "custom_provider_request_failed"
    assert result["diagnostic_id"]
    assert secret not in evidence
    assert endpoint not in evidence


def test_custom_image_reauthorizes_before_request_and_result_download(
    monkeypatch,
):
    from agent import custom_image_providers

    provider_name = "custom:router"
    model = "router-image-v1"
    binding = _image_binding(
        fingerprint="custom-image-per-io-authorization",
        provider=provider_name,
        model=model,
        provider_config={
            "id": "router",
            "name": "Router",
            "base_url": "https://router.example.test/v1",
            "credential_ref": "custom-router-key",
            "allow_custom_model_id": False,
            "models": [model],
            "default_model": model,
            "size_map": {},
            "response_format": "url",
            "timeout_seconds": 30,
            "network_scope": "public_direct",
            "trusted_proxy_profile": "",
        },
    )
    request_calls: list[dict[str, Any]] = []
    download_calls: list[str] = []
    guard_calls: list[int] = []

    @contextmanager
    def request_pinned_https(**kwargs):
        request_calls.append(dict(kwargs))
        yield SimpleNamespace(status_code=200)

    def guard():
        guard_calls.append(len(guard_calls) + 1)
        if len(guard_calls) > 1:
            raise RuntimeError("capability_caller_stale")

    monkeypatch.setattr(
        custom_image_providers,
        "request_pinned_https",
        request_pinned_https,
    )
    monkeypatch.setattr(
        custom_image_providers,
        "read_bounded_json",
        lambda _response: {
            "data": [{"url": "https://result.example.test/image.png"}]
        },
    )
    monkeypatch.setattr(
        custom_image_providers,
        "save_url_image",
        lambda url, **_kwargs: download_calls.append(url) or "/tmp/image.png",
    )
    provider = custom_image_providers.ConfigurableOpenAIImageProvider(
        dict(binding.provider_config)
    )

    with pytest.raises(RuntimeError, match="capability_caller_stale"):
        provider.generate(
            "reauthorize custom image",
            model=model,
            _runtime_binding=binding,
            _reauth_guard=guard,
        )

    assert len(request_calls) == 1
    assert download_calls == []
    assert guard_calls == [1, 2]


@pytest.mark.parametrize("result_format", ["b64_json", "url"])
def test_custom_image_result_io_exception_is_redacted_and_diagnostic(
    monkeypatch,
    caplog,
    result_format,
):
    from agent import custom_image_providers

    provider_name = "custom:router"
    model = "router-image-v1"
    secret = "SENTINEL-CUSTOM-RESULT-SECRET-DO-NOT-LEAK"
    endpoint = "https://sentinel-custom-result.example.test/private"
    binding = _image_binding(
        fingerprint=f"custom-result-{result_format}",
        provider=provider_name,
        model=model,
        secret=secret,
        endpoint=f"{endpoint}/images/generations",
        provider_config={
            "id": "router",
            "name": "Router",
            "base_url": endpoint,
            "credential_ref": "custom-router-key",
            "allow_custom_model_id": False,
            "models": [model],
            "default_model": model,
            "size_map": {},
            "response_format": result_format,
            "timeout_seconds": 30,
            "network_scope": "public_direct",
            "trusted_proxy_profile": "",
        },
    )
    body = (
        {
            "data": [
                {
                    "b64_json": base64.b64encode(b"result").decode("ascii")
                }
            ]
        }
        if result_format == "b64_json"
        else {"data": [{"url": "https://result.example.test/image.png"}]}
    )

    @contextmanager
    def request_pinned_https(**_kwargs):
        yield SimpleNamespace(status_code=200)

    def fail_result_io(*_args, **_kwargs):
        raise RuntimeError(f"{secret} {endpoint}")

    monkeypatch.setattr(
        custom_image_providers,
        "request_pinned_https",
        request_pinned_https,
    )
    monkeypatch.setattr(
        custom_image_providers,
        "read_bounded_json",
        lambda _response: body,
    )
    monkeypatch.setattr(
        custom_image_providers,
        "save_b64_image",
        fail_result_io,
    )
    monkeypatch.setattr(
        custom_image_providers,
        "save_url_image",
        fail_result_io,
    )
    provider = custom_image_providers.ConfigurableOpenAIImageProvider(
        dict(binding.provider_config)
    )

    with caplog.at_level(logging.DEBUG):
        result = provider.generate(
            "redact custom result failure",
            model=model,
            _runtime_binding=binding,
            _reauth_guard=lambda: None,
        )
    evidence = f"{json.dumps(result, ensure_ascii=False)}\n{caplog.text}"

    assert result["success"] is False
    assert result["error_code"] == "custom_provider_result_io_failed"
    assert result["diagnostic_id"]
    assert secret not in evidence
    assert endpoint not in evidence


def test_custom_image_b64_result_reauthorizes_before_cache_write(
    monkeypatch,
):
    """A revoked request cannot persist a base64 result after Provider I/O."""
    from agent import custom_image_providers

    provider_name = "custom:router"
    model = "router-image-v1"
    binding = _image_binding(
        fingerprint="custom-b64-per-io-authorization",
        provider=provider_name,
        model=model,
        provider_config={
            "id": "router",
            "name": "Router",
            "base_url": "https://router.example.test/v1",
            "credential_ref": "router-key",
            "allow_custom_model_id": False,
            "models": [model],
            "default_model": model,
            "size_map": {},
            "response_format": "b64_json",
            "timeout_seconds": 30,
            "network_scope": "public_direct",
            "trusted_proxy_profile": "",
        },
    )
    guard_calls: list[int] = []
    saved_payloads: list[str] = []

    @contextmanager
    def request_pinned_https(**_kwargs):
        yield SimpleNamespace(status_code=200)

    def guard() -> None:
        guard_calls.append(len(guard_calls) + 1)
        if len(guard_calls) > 1:
            raise RuntimeError("capability_caller_stale")

    monkeypatch.setattr(
        custom_image_providers,
        "request_pinned_https",
        request_pinned_https,
    )
    monkeypatch.setattr(
        custom_image_providers,
        "read_bounded_json",
        lambda _response: {
            "data": [
                {
                    "b64_json": base64.b64encode(
                        b"must-not-be-persisted"
                    ).decode("ascii")
                }
            ]
        },
    )
    monkeypatch.setattr(
        custom_image_providers,
        "save_b64_image",
        lambda payload, **_kwargs: (
            saved_payloads.append(payload) or "/tmp/forbidden.png"
        ),
    )
    provider = custom_image_providers.ConfigurableOpenAIImageProvider(
        dict(binding.provider_config)
    )

    with pytest.raises(RuntimeError, match="capability_caller_stale"):
        provider.generate(
            "reauthorize base64 result",
            model=model,
            _runtime_binding=binding,
            _reauth_guard=guard,
        )

    assert guard_calls == [1, 2]
    assert saved_payloads == []


@pytest.mark.parametrize(
    ("module_name", "class_name", "provider_name", "model"),
    [
        (
            "plugins.image_gen.dashscope",
            "DashScopeQwenImageProvider",
            "dashscope",
            "qwen-image-2.0-pro",
        ),
        (
            "plugins.image_gen.doubao",
            "DoubaoImageGenProvider",
            "doubao",
            "doubao-seedream-5-0-260128",
        ),
        (
            "plugins.image_gen.qianfan",
            "QianfanImageGenProvider",
            "qianfan",
            "qwen-image",
        ),
        (
            "plugins.image_gen.zhipu_image",
            "ZhipuImageGenProvider",
            "zhipu-image",
            "glm-image",
        ),
        (
            "plugins.image_gen.minimax_image",
            "MinimaxImageGenProvider",
            "minimax-image",
            "image-01",
        ),
    ],
)
def test_builtin_image_reauthorizes_before_request_and_result_download(
    monkeypatch,
    module_name,
    class_name,
    provider_name,
    model,
):
    from plugins.image_gen import domestic_common

    module = importlib.import_module(module_name)
    binding = _image_binding(
        fingerprint=f"{provider_name}-per-io-authorization",
        provider=provider_name,
        model=model,
        secret=f"{provider_name}-secret",
        endpoint="https://provider.example.test/images/generations",
    )
    request_calls: list[dict[str, Any]] = []
    download_calls: list[str] = []
    guard_calls: list[int] = []

    @contextmanager
    def request_pinned_https(**kwargs):
        request_calls.append(dict(kwargs))
        yield SimpleNamespace(status_code=200)

    def guard():
        guard_calls.append(len(guard_calls) + 1)
        if len(guard_calls) > 1:
            raise RuntimeError("capability_caller_stale")

    monkeypatch.setattr(
        domestic_common,
        "request_pinned_https",
        request_pinned_https,
    )
    monkeypatch.setattr(
        domestic_common,
        "read_bounded_json",
        lambda *_args, **_kwargs: {
            "data": [{"url": "https://result.example.test/image.png"}]
        },
    )
    monkeypatch.setattr(
        module,
        "save_url_image",
        lambda url, **_kwargs: download_calls.append(url) or "/tmp/image.png",
    )
    provider = getattr(module, class_name)()

    with pytest.raises(RuntimeError, match="capability_caller_stale"):
        provider.generate(
            "reauthorize builtin image",
            model=model,
            _runtime_binding=binding,
            _reauth_guard=guard,
        )

    assert len(request_calls) == 1
    assert download_calls == []
    assert guard_calls == [1, 2]


@pytest.mark.asyncio
async def test_vision_old_a_binding_cannot_revive_after_persisted_a_b_a(
    monkeypatch,
    tmp_path,
):
    """No old guard call is needed while persistent state moves A -> B -> A."""
    from agent import image_runtime
    from tools import vision_tools

    fingerprint = "vision-material-A"
    old_binding = _vision_binding(
        fingerprint=fingerprint,
        authorization_generation="vision-generation-A1",
    )
    final_snapshot = _verified_snapshot(
        "vision",
        fingerprint=fingerprint,
        provider=old_binding.provider,
        model=old_binding.model,
        authorization_generation="vision-generation-A3",
    )
    image = tmp_path / "aba.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"aba")
    provider_io: list[dict[str, Any]] = []

    async def provider_call(**kwargs):
        provider_io.append(dict(kwargs))
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="old A call ran")
                )
            ]
        )

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(final_snapshot),
    )
    monkeypatch.setattr(vision_tools, "async_call_llm", provider_call)

    result = json.loads(
        await vision_tools.vision_analyze_tool(
            str(image),
            "reject revived A binding",
            old_binding.model,
            provider=old_binding.provider,
            strict_target=True,
            _runtime_binding=old_binding,
        )
    )

    assert provider_io == []
    assert result["success"] is False
    assert result["status"] == "blocked"
    assert result["error_code"] == "capability_binding_mismatch"


def test_generic_custom_vision_network_policy_changes_fingerprint():
    from agent.image_runtime import vision_fingerprint

    public_config = {
        "provider": "custom",
        "model": "vision-policy-v1",
        "base_url": "https://vision-policy.example.test/v1",
        "api_mode": "chat_completions",
        "credential_ref": "vision-policy-key",
        "network_scope": "public_direct",
        "trusted_proxy_profile": "",
    }
    trusted_config = {
        **public_config,
        "network_scope": "trusted_proxy",
        "trusted_proxy_profile": "corp-proxy",
    }
    public_fingerprint, public_resolved = vision_fingerprint(
        public_config,
        profile="default",
        config_data={"auxiliary": {"vision": public_config}},
        secret_value="vision-policy-secret",
        key_configured=True,
    )
    trusted_fingerprint, trusted_resolved = vision_fingerprint(
        trusted_config,
        profile="default",
        config_data={"auxiliary": {"vision": trusted_config}},
        secret_value="vision-policy-secret",
        key_configured=True,
    )

    assert public_resolved is True
    assert trusted_resolved is True
    assert public_fingerprint != trusted_fingerprint


@pytest.mark.asyncio
async def test_generic_custom_vision_policy_change_rejects_old_binding_before_io(
    monkeypatch,
    tmp_path,
):
    from agent import image_runtime
    from tools import vision_tools

    public_config = {
        "provider": "custom",
        "model": "vision-policy-v1",
        "base_url": "https://vision-policy.example.test/v1",
        "api_mode": "chat_completions",
        "credential_ref": "vision-policy-key",
        "network_scope": "public_direct",
        "trusted_proxy_profile": "",
    }
    trusted_config = {
        **public_config,
        "network_scope": "trusted_proxy",
        "trusted_proxy_profile": "corp-proxy",
    }
    old_fingerprint, _ = image_runtime.vision_fingerprint(
        public_config,
        profile="default",
        config_data={"auxiliary": {"vision": public_config}},
        secret_value="vision-policy-secret",
        key_configured=True,
    )
    current_fingerprint, _ = image_runtime.vision_fingerprint(
        trusted_config,
        profile="default",
        config_data={"auxiliary": {"vision": trusted_config}},
        secret_value="vision-policy-secret",
        key_configured=True,
    )
    authorization_generation = "vision-policy-generation"
    old_binding = _vision_binding(
        fingerprint=old_fingerprint,
        provider="custom",
        model=public_config["model"],
        secret="vision-policy-secret",
        endpoint=public_config["base_url"],
        authorization_generation=authorization_generation,
    )
    current_snapshot = _verified_snapshot(
        "vision",
        fingerprint=current_fingerprint,
        provider="custom",
        model=trusted_config["model"],
        authorization_generation=authorization_generation,
    )
    image = tmp_path / "vision-policy.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"vision-policy")
    provider_or_cache_io: list[dict[str, Any]] = []

    async def provider_call(**kwargs):
        provider_or_cache_io.append(dict(kwargs))
        kwargs["resolution_out"].update(
            {
                "provider": "custom",
                "model": trusted_config["model"],
            }
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="stale binding ran")
                )
            ]
        )

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(current_snapshot),
    )
    monkeypatch.setattr(vision_tools, "async_call_llm", provider_call)

    result = json.loads(
        await vision_tools.vision_analyze_tool(
            str(image),
            "reject stale network policy",
            old_binding.model,
            provider=old_binding.provider,
            strict_target=True,
            _runtime_binding=old_binding,
        )
    )

    assert provider_or_cache_io == []
    assert result["success"] is False
    assert result["status"] == "blocked"
    assert result["error_code"] == "capability_binding_mismatch"


def test_custom_image_constructor_failure_never_falls_back_to_fal(
    monkeypatch,
):
    from agent import custom_image_providers, image_runtime
    from tools import image_generation_tool

    provider = "custom:router"
    model = "router-image-v1"
    fingerprint = "custom-image-constructor-failure"
    snapshot = _verified_snapshot(
        "image_generation",
        fingerprint=fingerprint,
        provider=provider,
        model=model,
    )
    binding = _image_binding(
        fingerprint=fingerprint,
        provider=provider,
        model=model,
        provider_config={
            "id": "router",
            "name": "Router",
            "base_url": "https://router.example.test/v1",
            "credential_ref": "router-key",
            "allow_custom_model_id": False,
            "models": [model],
            "default_model": model,
            "size_map": {},
            "response_format": "auto",
            "timeout_seconds": 30,
            "network_scope": "public_direct",
            "trusted_proxy_profile": "",
        },
    )
    fal_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(snapshot),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kwargs: binding,
    )
    monkeypatch.setattr(
        custom_image_providers,
        "ConfigurableOpenAIImageProvider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("constructor failed")
        ),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "image_generate_tool",
        lambda **kwargs: fal_calls.append(dict(kwargs))
        or {"success": True, "provider": "fal"},
    )

    result = json.loads(
        _handle_image_generate_with_gate(
            image_generation_tool,
            {"prompt": "do not fall back"},
            caller_capability_fingerprint=fingerprint,
            caller_capability_generation=snapshot[
                "_authorization_generation"
            ],
        )
    )

    assert fal_calls == []
    assert result["success"] is False
    assert result["error_code"] == "provider_configuration_invalid"
    assert result["diagnostic_id"]


@pytest.mark.parametrize("provider", ["fal", "openai", "xai", "krea"])
def test_unverifiable_image_provider_fails_closed_without_any_fallback_io(
    monkeypatch,
    provider,
):
    from agent import image_runtime
    from tools import image_generation_tool

    snapshot = _verified_snapshot(
        "image_generation",
        fingerprint=f"{provider}-unverifiable-image",
        provider=provider,
        model=f"{provider}-image-model",
        status="configured_unverified",
    )
    boundary_calls: list[str] = []

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(snapshot),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kwargs: boundary_calls.append("capture"),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_dispatch_to_plugin_provider",
        lambda *_args, **_kwargs: boundary_calls.append("provider"),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "image_generate_tool",
        lambda **_kwargs: boundary_calls.append("fallback"),
    )

    result = json.loads(
        image_generation_tool._handle_image_generate(
            {"prompt": "must fail closed"},
            caller_capability_fingerprint=snapshot["fingerprint"],
            caller_capability_generation=snapshot[
                "_authorization_generation"
            ],
        )
    )

    assert boundary_calls == []
    assert result["success"] is False
    assert result["error_code"] == "capability_caller_stale"
    assert result["error_type"] == "capability_caller_stale"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_error", "temperature", "max_tokens"),
    [
        (RuntimeError("unsupported parameter temperature"), 0.7, None),
        (RuntimeError("unsupported parameter max_tokens"), None, 256),
    ],
)
async def test_auxiliary_inner_retry_reauth_blocks_second_io(
    monkeypatch,
    first_error,
    temperature,
    max_tokens,
):
    from agent import auxiliary_client

    fingerprint = "vision-inner-retry-generation"
    binding = _vision_binding(fingerprint=fingerprint)
    io_calls: list[dict[str, Any]] = []
    guard_calls: list[int] = []

    class Completions:
        async def create(self, **kwargs):
            io_calls.append(dict(kwargs))
            raise first_error

    client = SimpleNamespace(
        base_url=binding.base_url,
        chat=SimpleNamespace(completions=Completions()),
    )

    def guard():
        guard_calls.append(len(guard_calls) + 1)
        if len(guard_calls) > 1:
            raise RuntimeError("capability_caller_stale")

    monkeypatch.setattr(
        auxiliary_client,
        "resolve_vision_provider_client",
        lambda **_kwargs: (binding.provider, client, binding.model),
    )

    with pytest.raises(RuntimeError, match="capability_caller_stale"):
        await auxiliary_client.async_call_llm(
            task="vision",
            messages=[{"role": "user", "content": "inspect"}],
            temperature=temperature,
            max_tokens=max_tokens,
            no_fallback=True,
            vision_binding=binding,
            vision_reauth_guard=guard,
        )

    assert len(io_calls) == 1
    assert guard_calls == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["verified", "verifying"])
@pytest.mark.parametrize(
    "mutation",
    ["bare_same_fingerprint", "secret", "endpoint", "api_mode"],
)
async def test_forged_vision_binding_fails_closed_for_runtime_and_probe(
    monkeypatch,
    tmp_path,
    status,
    mutation,
):
    from agent import image_runtime
    from tools import vision_tools

    fingerprint = f"vision-{status}-generation-A"
    forged = _vision_binding(fingerprint=fingerprint)
    if mutation == "bare_same_fingerprint":
        forged = _vision_binding(
            fingerprint=fingerprint,
            sealed=False,
        )
    elif mutation == "secret":
        object.__setattr__(forged, "api_key", "tampered-vision-secret")
    elif mutation == "endpoint":
        object.__setattr__(
            forged,
            "base_url",
            "https://tampered-vision.example.test/v1",
        )
    else:
        object.__setattr__(
            forged,
            "api_mode",
            "tampered_vision_mode",
        )
    snapshot = _verified_snapshot(
        "vision",
        fingerprint=fingerprint,
        provider=forged.provider,
        model=forged.model,
        status=status,
    )
    image = tmp_path / "probe.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"probe")
    provider_io: list[dict[str, Any]] = []

    async def provider_call(**kwargs):
        provider_io.append(dict(kwargs))
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="forged call ran")
                )
            ]
        )
        return response

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(snapshot),
    )
    monkeypatch.setattr(vision_tools, "async_call_llm", provider_call)

    result = json.loads(
        await vision_tools.vision_analyze_tool(
            str(image),
            "forged binding",
            forged.model,
            provider=forged.provider,
            strict_target=True,
            _runtime_binding=forged,
        )
    )

    assert provider_io == []
    assert result["success"] is False
    assert result["status"] == "blocked"
    assert result["error_code"] == "capability_binding_mismatch"


def test_image_provider_exception_redacts_secret_and_endpoint(
    monkeypatch,
    caplog,
):
    from agent import image_gen_registry, image_runtime
    from agent.image_gen_provider import ImageGenProvider
    from hermes_cli import plugins as plugins_module
    from tools import image_generation_tool

    secret = "SENTINEL-IMAGE-SECRET-DO-NOT-LEAK"
    endpoint = "https://sentinel-image-endpoint.example.test/private"
    fingerprint = "image-redaction-generation"
    snapshot = _verified_snapshot(
        "image_generation",
        fingerprint=fingerprint,
        provider="codex",
        model="gpt-image-2",
    )
    binding = _image_binding(
        fingerprint=fingerprint,
        secret=secret,
        endpoint=endpoint,
    )

    class Provider(ImageGenProvider):
        _supports_pinned_image_request_binding = True

        @property
        def name(self) -> str:
            return "codex"

        def generate(self, prompt, aspect_ratio="landscape", **kwargs):
            raise RuntimeError(f"{secret} {endpoint}")

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(snapshot),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kwargs: binding,
    )
    monkeypatch.setattr(
        plugins_module,
        "_ensure_plugins_discovered",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        image_gen_registry,
        "get_provider",
        lambda name: Provider() if name == "codex" else None,
    )

    with caplog.at_level(logging.DEBUG):
        result_text = _handle_image_generate_with_gate(
            image_generation_tool,
            {"prompt": "redact image error"},
            caller_capability_fingerprint=fingerprint,
            caller_capability_generation=snapshot[
                "_authorization_generation"
            ],
        )
    result = json.loads(result_text)
    evidence = f"{result_text}\n{caplog.text}"

    assert result["success"] is False
    assert result["error_code"] == "provider_exception"
    assert result["diagnostic_id"]
    assert secret not in evidence
    assert endpoint not in evidence


@pytest.mark.asyncio
async def test_vision_provider_exception_redacts_secret_and_endpoint(
    monkeypatch,
    caplog,
    tmp_path,
):
    from agent import image_runtime
    from tools import vision_tools

    secret = "SENTINEL-VISION-SECRET-DO-NOT-LEAK"
    endpoint = "https://sentinel-vision-endpoint.example.test/private"
    fingerprint = "vision-redaction-generation"
    binding = _vision_binding(
        fingerprint=fingerprint,
        secret=secret,
        endpoint=endpoint,
    )
    snapshot = _verified_snapshot(
        "vision",
        fingerprint=fingerprint,
        provider=binding.provider,
        model=binding.model,
    )
    image = tmp_path / "vision.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"vision")

    async def provider_call(**_kwargs):
        raise RuntimeError(f"{secret} {endpoint}")

    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(snapshot),
    )
    monkeypatch.setattr(vision_tools, "async_call_llm", provider_call)

    with caplog.at_level(logging.DEBUG):
        result_text = await vision_tools.vision_analyze_tool(
            str(image),
            "redact vision error",
            binding.model,
            provider=binding.provider,
            strict_target=True,
            _runtime_binding=binding,
        )
    result = json.loads(result_text)
    evidence = f"{result_text}\n{caplog.text}"

    assert result["success"] is False
    assert result["error_code"] == "vision_provider_exception"
    assert result["diagnostic_id"]
    assert secret not in evidence
    assert endpoint not in evidence
