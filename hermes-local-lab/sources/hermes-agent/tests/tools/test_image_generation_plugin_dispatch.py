from __future__ import annotations

import base64
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent import image_gen_registry
from agent.image_gen_provider import ImageGenProvider


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _write_cached_png(home: Path, name: str = "codex-test.png") -> Path:
    image = home / "cache" / "images" / name
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(_PNG_1X1)
    return image


@pytest.fixture(autouse=True)
def _reset_registry():
    image_gen_registry._reset_for_tests()
    yield
    image_gen_registry._reset_for_tests()


class _FakeCodexProvider(ImageGenProvider):
    def __init__(self, image: str | None = None) -> None:
        self._image = image

    @property
    def name(self) -> str:
        return "codex"

    def generate(self, prompt, aspect_ratio="landscape", **kwargs):
        image = self._image or str(
            _write_cached_png(Path(os.environ["HERMES_HOME"]))
        )
        return {
            "success": True,
            "image": str(image),
            "model": "gpt-5.2-codex",
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "provider": "codex",
        }


class TestPluginDispatch:
    def test_dispatch_routes_to_codex_provider(self, monkeypatch, tmp_path):
        from tools import image_generation_tool
        from agent import image_gen_registry as registry_module
        from hermes_cli import plugins as plugins_module

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text("image_gen:\n  provider: codex\n")
        image_gen_registry.register_provider(_FakeCodexProvider())

        monkeypatch.setattr(image_generation_tool, "_read_configured_image_provider", lambda: "codex")
        monkeypatch.setattr(plugins_module, "_ensure_plugins_discovered", lambda: None)
        monkeypatch.setattr(registry_module, "get_provider", lambda name: _FakeCodexProvider() if name == "codex" else None)

        dispatched = image_generation_tool._dispatch_to_plugin_provider("draw cat", "square")
        payload = json.loads(dispatched)

        assert payload["success"] is True
        assert payload["provider"] == "codex"
        assert payload["image"] == str(
            tmp_path / "cache" / "images" / "codex-test.png"
        )
        assert payload["image_ref"] == "codex-test.png"
        assert len(payload["sha256"]) == 64
        assert payload["aspect_ratio"] == "square"

    def test_dispatch_rejects_success_outside_generated_image_cache(
        self,
        monkeypatch,
        tmp_path,
    ):
        from agent import image_gen_registry as registry_module
        from hermes_cli import plugins as plugins_module
        from tools import image_generation_tool

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(
            image_generation_tool,
            "_read_configured_image_provider",
            lambda: "codex",
        )
        monkeypatch.setattr(
            plugins_module,
            "_ensure_plugins_discovered",
            lambda: None,
        )
        monkeypatch.setattr(
            registry_module,
            "get_provider",
            lambda name: (
                _FakeCodexProvider("/tmp/outside-generated-cache.png")
                if name == "codex"
                else None
            ),
        )

        dispatched = image_generation_tool._dispatch_to_plugin_provider(
            "draw cat",
            "square",
        )
        payload = json.loads(dispatched)

        assert payload["success"] is False
        assert payload["image"] is None
        assert payload["error_code"] == "provider_contract"

    def test_dispatch_reports_missing_registered_provider(self, monkeypatch, tmp_path):
        from tools import image_generation_tool
        from hermes_cli import plugins as plugins_module

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text("image_gen:\n  provider: missing-codex\n")

        monkeypatch.setattr(image_generation_tool, "_read_configured_image_provider", lambda: "missing-codex")
        monkeypatch.setattr(plugins_module, "_ensure_plugins_discovered", lambda: None)

        dispatched = image_generation_tool._dispatch_to_plugin_provider("draw cat", "landscape")
        payload = json.loads(dispatched)

        assert payload["success"] is False
        assert payload["error_type"] == "provider_not_registered"
        assert "太极智能体" in payload["error"]
        assert "image_gen.provider" not in payload["error"]
        assert "hermes" not in payload["error"]

    def test_dispatch_force_refreshes_plugins_when_provider_initially_missing(self, monkeypatch, tmp_path):
        from tools import image_generation_tool
        from hermes_cli import plugins as plugins_module
        from agent import image_gen_registry as registry_module

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "config.yaml").write_text("image_gen:\n  provider: codex\n")

        monkeypatch.setattr(image_generation_tool, "_read_configured_image_provider", lambda: "codex")

        calls = []
        provider_state = {"provider": None}

        def fake_ensure_plugins_discovered(force=False):
            calls.append(force)
            if force:
                provider_state["provider"] = _FakeCodexProvider()

        monkeypatch.setattr(plugins_module, "_ensure_plugins_discovered", fake_ensure_plugins_discovered)
        monkeypatch.setattr(registry_module, "get_provider", lambda name: provider_state["provider"])

        dispatched = image_generation_tool._dispatch_to_plugin_provider("draw hammy", "portrait")
        payload = json.loads(dispatched)

        assert calls == [False, True]
        assert payload["success"] is True
        assert payload["provider"] == "codex"
        assert payload["aspect_ratio"] == "portrait"

    def test_concurrent_custom_dispatch_ignores_poisoned_global_registry(
        self,
        monkeypatch,
        tmp_path,
    ):
        from agent import custom_image_providers
        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )
        from tools import image_generation_tool

        credential_ref = "shared-custom-image"
        secret_env = (
            "TAIJI_CREDENTIAL_SHARED_CUSTOM_IMAGE_API_KEY"
        )
        profiles = {}
        configs = {}
        for name in ("A", "B"):
            home = tmp_path / f"profile-{name.lower()}"
            home.mkdir()
            model = f"image-model-{name.lower()}"
            base_url = f"https://profile-{name.lower()}.example.test/v1"
            config = {
                "provider_credentials": [
                    {
                        "id": credential_ref,
                        "provider_family": "custom",
                        "label": "Shared custom image",
                        "auth_type": "api_key",
                        "secret_env": secret_env,
                    }
                ],
                "image_gen": {
                    "provider": "custom:router",
                    "model": model,
                },
                "custom_image_providers": [
                    {
                        "id": "router",
                        "name": f"Router {name}",
                        "base_url": base_url,
                        "credential_ref": credential_ref,
                        "models": [
                            "image-model-a",
                            "image-model-b",
                        ],
                        "default_model": model,
                        "network_scope": "public_direct",
                    }
                ],
            }
            (home / "config.yaml").write_text(
                yaml.safe_dump(config, sort_keys=False),
                encoding="utf-8",
            )
            (home / ".env").write_text(
                f"{secret_env}=profile-{name.lower()}-secret\n",
                encoding="utf-8",
            )
            profiles[name] = {
                "home": home,
                "model": model,
                "endpoint": f"{base_url}/images/generations",
                "authorization": f"Bearer profile-{name.lower()}-secret",
            }
            configs[name] = config

        poisoned = custom_image_providers.build_configured_custom_image_provider(
            "custom:router",
            configs["B"],
        )
        assert poisoned is not None
        image_gen_registry.register_provider(poisoned)
        monkeypatch.setattr(
            custom_image_providers,
            "register_configured_custom_image_providers",
            lambda: None,
        )
        monkeypatch.setattr(
            image_generation_tool,
            "_same_authorization_snapshot",
            lambda _snapshot: True,
        )
        requests = {}

        @contextmanager
        def fake_request_pinned_https(**kwargs):
            requests[threading.current_thread().name] = {
                "url": kwargs["url"],
                "authorization": kwargs["headers"]["Authorization"],
                "model": kwargs["json_body"]["model"],
            }
            yield SimpleNamespace(status_code=200)

        monkeypatch.setattr(
            custom_image_providers,
            "request_pinned_https",
            fake_request_pinned_https,
        )
        probe_png = bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001"
            "0804000000b51c0c020000000b4944415478da6364f80f00"
            "010501012718e3660000000049454e44ae426082"
        )
        encoded_probe = base64.b64encode(probe_png).decode("ascii")
        monkeypatch.setattr(
            custom_image_providers,
            "read_bounded_json",
            lambda _response: {"data": [{"b64_json": encoded_probe}]},
        )
        start = threading.Barrier(2)
        results = {}

        def worker(profile: str):
            token = set_hermes_home_override(profiles[profile]["home"])
            try:
                start.wait(timeout=5)
                binding = (
                    image_generation_tool
                    ._capture_image_gen_request_binding(
                        authorization_generation=f"test-generation-{profile}",
                    )
                )
                assert binding is not None
                snapshot = {
                    "schema_version": 1,
                    "fingerprint": binding.authorization_fingerprint,
                    "_authorization_generation": (
                        binding.authorization_generation
                    ),
                    "status": "verified",
                    "available": True,
                    "provider": "custom:router",
                    "model": profiles[profile]["model"],
                }
                from agent.image_runtime import (
                    build_capability_route_decision,
                )

                decision = build_capability_route_decision(
                    "image_generation",
                    snapshot=snapshot,
                    route="provider",
                    request_binding=binding,
                )
                raw = image_generation_tool._dispatch_to_plugin_provider(
                    f"probe-{profile}",
                    "square",
                    runtime_snapshot=snapshot,
                    route_decision=decision,
                )
                results[profile] = json.loads(raw)
            finally:
                reset_hermes_home_override(token)

        first = threading.Thread(target=worker, args=("A",), name="A")
        second = threading.Thread(target=worker, args=("B",), name="B")
        first.start()
        second.start()
        first.join(timeout=10)
        second.join(timeout=10)

        assert not first.is_alive()
        assert not second.is_alive()
        assert results["A"]["success"] is True
        assert results["B"]["success"] is True
        assert requests == {
            profile: {
                "url": profiles[profile]["endpoint"],
                "authorization": profiles[profile]["authorization"],
                "model": profiles[profile]["model"],
            }
            for profile in ("A", "B")
        }

    def test_configured_fal_dispatch_consumes_pinned_binding(
        self,
        monkeypatch,
        tmp_path,
    ):
        from agent import image_gen_registry as registry_module
        from agent.image_gen_verification import (
            ImageGenRequestBinding,
            authorize_image_gen_request_binding,
            image_gen_runtime_identity,
        )
        from agent.image_runtime import build_capability_route_decision
        from hermes_cli import plugins as plugins_module
        from plugins.image_gen.fal import FalImageGenProvider
        from tools import image_generation_tool

        model = "fal-ai/flux-2/klein/9b"
        fingerprint = "configured-fal-dispatch-fingerprint"
        generation = "configured-fal-dispatch-generation"
        binding = authorize_image_gen_request_binding(
            ImageGenRequestBinding(
                provider="fal",
                model=model,
                api_key="pinned-fal-dispatch-secret",
                runtime_identity=image_gen_runtime_identity(
                    "fal",
                    {"provider": "fal", "model": model},
                ),
            ),
            authorization_fingerprint=fingerprint,
            authorization_generation=generation,
        )
        snapshot = {
            "schema_version": 1,
            "fingerprint": fingerprint,
            "_authorization_generation": generation,
            "status": "verified",
            "available": True,
            "provider": "fal",
            "model": model,
        }
        decision = build_capability_route_decision(
            "image_generation",
            snapshot=snapshot,
            route="provider",
            request_binding=binding,
        )
        monkeypatch.setenv("FAL_KEY", "ambient-key-must-not-be-used")
        monkeypatch.setattr(
            plugins_module,
            "_ensure_plugins_discovered",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            registry_module,
            "get_provider",
            lambda name: FalImageGenProvider() if name == "fal" else None,
        )
        reauth_calls = []
        monkeypatch.setattr(
            image_generation_tool,
            "_same_authorization_snapshot",
            lambda value: reauth_calls.append(value) or True,
        )
        captured = {}
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        generated_image = _write_cached_png(
            tmp_path,
            "configured-fal.png",
        )

        def fake_image_generate_tool(prompt, aspect_ratio, **kwargs):
            kwargs["_reauth_guard"]()
            captured.update(kwargs)
            return json.dumps(
                {
                    "success": True,
                    "image": str(generated_image),
                }
            )

        monkeypatch.setattr(
            image_generation_tool,
            "image_generate_tool",
            fake_image_generate_tool,
        )

        result = json.loads(
            image_generation_tool._dispatch_to_plugin_provider(
                "draw from pinned FAL",
                "square",
                runtime_snapshot=snapshot,
                route_decision=decision,
            )
        )

        assert result["success"] is True
        assert result["image"] == str(generated_image)
        assert result["model"] == model
        assert captured["_runtime_binding"] is binding
        assert captured["_runtime_binding"].api_key == (
            "pinned-fal-dispatch-secret"
        )
        assert len(reauth_calls) == 2


@pytest.mark.parametrize(
    ("module_name", "class_name", "provider_name", "model"),
    [
        (
            "plugins.image_gen.doubao",
            "DoubaoImageGenProvider",
            "doubao",
            "doubao-seedream-5-0-260128",
        ),
        (
            "plugins.image_gen.minimax_image",
            "MinimaxImageGenProvider",
            "minimax-image",
            "image-01",
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
    ],
)
def test_builtin_image_provider_consumes_pinned_probe_key(
    monkeypatch,
    module_name,
    class_name,
    provider_name,
    model,
):
    from agent.image_gen_verification import (
        ImageGenRequestBinding,
        authorize_image_gen_request_binding,
        image_gen_runtime_identity,
    )

    module = __import__(module_name, fromlist=[class_name])
    provider = getattr(module, class_name)()
    captured = {}

    def live_key_must_not_be_read(*_args, **_kwargs):
        raise AssertionError("pinned probe fell back to live credential")

    def fake_post_json(**kwargs):
        kwargs["reauth_guard"]()
        captured["authorization"] = kwargs["headers"]["Authorization"]
        captured["url"] = kwargs["url"]
        return {"data": [{"url": "https://cdn.example.test/image.png"}]}, None

    def fake_cached_success(**kwargs):
        kwargs["reauth_guard"]()
        return {
            "success": True,
            "image": "/tmp/pinned-probe.png",
            "provider": provider_name,
            "model": kwargs["model"],
        }

    monkeypatch.setattr(module, "provider_api_key", live_key_must_not_be_read)
    monkeypatch.setattr(module, "post_json", fake_post_json)
    monkeypatch.setattr(
        module,
        "cached_success",
        fake_cached_success,
    )
    binding = authorize_image_gen_request_binding(
        ImageGenRequestBinding(
            provider=provider_name,
            model=model,
            api_key="pinned-secret",
            runtime_identity=image_gen_runtime_identity(
                provider_name,
                {"provider": provider_name, "model": model},
            ),
        ),
        authorization_fingerprint="direct-provider-test",
        authorization_generation="direct-provider-generation",
    )

    result = provider.generate(
        prompt="probe",
        aspect_ratio="square",
        model=model,
        _runtime_binding=binding,
        _reauth_guard=lambda: None,
    )

    assert result["success"] is True
    assert captured["authorization"] == "Bearer pinned-secret"


@pytest.mark.parametrize(
    ("binding_provider", "binding_model"),
    [
        ("qianfan", "qwen-image-2.0-pro"),
        ("dashscope", "wrong-model"),
    ],
)
def test_dashscope_rejects_mismatched_pinned_probe_binding_without_io(
    monkeypatch,
    binding_provider,
    binding_model,
):
    from agent.image_gen_verification import (
        ImageGenRequestBinding,
        authorize_image_gen_request_binding,
        image_gen_runtime_identity,
    )
    from plugins.image_gen.dashscope import DashScopeQwenImageProvider
    import plugins.image_gen.dashscope as dashscope

    calls = []
    monkeypatch.setattr(
        dashscope,
        "post_json",
        lambda **kwargs: calls.append(kwargs),
    )
    binding = authorize_image_gen_request_binding(
        ImageGenRequestBinding(
            provider=binding_provider,
            model=binding_model,
            api_key="must-not-leak",
            runtime_identity=image_gen_runtime_identity(
                "dashscope",
                {
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                },
            ),
        ),
        authorization_fingerprint="mismatched-binding-test",
        authorization_generation="mismatched-binding-generation",
    )

    result = DashScopeQwenImageProvider().generate(
        prompt="probe",
        aspect_ratio="square",
        model="qwen-image-2.0-pro",
        _runtime_binding=binding,
        _reauth_guard=lambda: None,
    )

    assert result["success"] is False
    assert result["error_type"] == "configuration_error"
    assert calls == []
    assert "must-not-leak" not in json.dumps(result)


def test_image_probe_binding_repr_and_runtime_identity_are_secret_safe():
    from agent.image_gen_verification import ImageGenRequestBinding

    endpoint = "https://private-endpoint.example.test/generate"
    binding = ImageGenRequestBinding(
        provider="dashscope",
        model="qwen-image-2.0-pro",
        api_key="private-probe-secret",
        runtime_identity={
            "identity_supported": True,
            "endpoint_resolved": True,
            "endpoint": endpoint,
        },
        _authorization_fingerprint="repr-safety-test",
    )

    rendered = repr(binding)
    assert "private-probe-secret" not in rendered
    assert endpoint not in rendered
    with pytest.raises(TypeError):
        binding.runtime_identity["endpoint"] = "https://mutated.invalid"


def test_image_provider_boundary_final_reauth_blocks_state_drift_without_io(
    monkeypatch,
):
    """A route decision must be reauthorized after it is frozen, before Provider I/O."""
    from agent import image_runtime
    from agent import image_gen_registry as registry_module
    from agent.image_gen_verification import ImageGenRequestBinding
    from agent.image_intent import (
        begin_image_generation_task,
        cleanup_image_generation_task,
    )
    from hermes_cli import plugins as plugins_module
    from tools import image_generation_tool

    decision_factory = getattr(
        image_runtime,
        "build_capability_route_decision",
        None,
    )
    assert callable(decision_factory), "CapabilityRouteDecision factory is missing"

    verified = {
        "schema_version": 1,
        "fingerprint": "image-final-reauth-v1",
        "_authorization_generation": "image-final-reauth-generation-v1",
        "status": "verified",
        "available": True,
        "reason_code": "ready",
        "provider": "codex",
        "model": "gpt-image-2",
    }
    stale = {
        **verified,
        "status": "configured_unverified",
        "available": False,
        "reason_code": "image_generation_not_verified",
    }
    from agent.image_gen_verification import (
        authorize_image_gen_request_binding,
    )

    binding = authorize_image_gen_request_binding(
        ImageGenRequestBinding(
            provider="codex",
            model="gpt-image-2",
            api_key="pinned-before-drift",
            runtime_identity={
                "identity_supported": True,
                "endpoint_resolved": True,
                "endpoint": "https://pinned-before-drift.example.test/v1",
            },
        ),
        authorization_fingerprint=verified["fingerprint"],
        authorization_generation=verified[
            "_authorization_generation"
        ],
    )
    runtime = {"snapshot": dict(verified)}
    build_calls = []

    def freeze_then_drift(*args, **kwargs):
        build_calls.append((args, kwargs))
        # The verification state changes after the caller's initial read but
        # before the Provider-boundary final authorization.
        runtime["snapshot"] = dict(stale)
        return decision_factory(*args, **kwargs)

    monkeypatch.setattr(
        image_runtime,
        "build_capability_route_decision",
        freeze_then_drift,
    )
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(runtime["snapshot"]),
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
    provider_io = []

    class BoundaryProvider(ImageGenProvider):
        @property
        def name(self) -> str:
            return "codex"

        def generate(self, prompt, aspect_ratio="landscape", **kwargs):
            provider_io.append(
                {
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    **kwargs,
                }
            )
            return {
                "success": True,
                "provider": "codex",
                "model": "gpt-image-2",
                "image": "/tmp/should-not-exist.png",
            }

    provider = BoundaryProvider()
    monkeypatch.setattr(
        registry_module,
        "get_provider",
        lambda name: provider if name == "codex" else None,
    )

    turn_id = "provider-boundary-drift-turn"
    owner = "provider-boundary-drift-owner"
    begin_image_generation_task(
        turn_id,
        allow_generation=True,
        owner_token=owner,
    )
    try:
        result = json.loads(
            image_generation_tool._handle_image_generate(
                {"prompt": "draw the boundary", "aspect_ratio": "square"},
                caller_capability_fingerprint=verified["fingerprint"],
                caller_capability_generation=verified[
                    "_authorization_generation"
                ],
                image_generation_task_id=turn_id,
                image_generation_gate_owner=owner,
            )
        )
    finally:
        cleanup_image_generation_task(turn_id, owner_token=owner)

    violations = []
    if len(build_calls) != 1:
        violations.append("dispatch did not freeze one route decision")
    if provider_io:
        violations.append("state drift reached Provider I/O")
    if result.get("success") is not False:
        violations.append("state drift did not return a blocked result")
    if result.get("error_code") != "capability_caller_stale":
        violations.append("state drift did not return stable stale error_code")
    if result.get("error_type") != "capability_caller_stale":
        violations.append("state drift did not return stable stale error_type")
    assert violations == [], "; ".join(violations)


def test_image_dispatch_uses_private_pinned_binding_after_ambient_rotation(
    monkeypatch,
    tmp_path,
):
    """Ambient credentials changed after reauth must not replace the private binding."""
    from agent import image_runtime
    from agent import image_gen_registry as registry_module
    from agent.image_gen_verification import ImageGenRequestBinding
    from agent.image_intent import (
        begin_image_generation_task,
        cleanup_image_generation_task,
    )
    from hermes_cli import plugins as plugins_module
    from tools import image_generation_tool

    decision_factory = getattr(
        image_runtime,
        "build_capability_route_decision",
        None,
    )
    assert callable(decision_factory), "CapabilityRouteDecision factory is missing"

    verified = {
        "schema_version": 1,
        "fingerprint": "image-pinned-binding-v1",
        "_authorization_generation": "image-pinned-binding-generation-v1",
        "status": "verified",
        "available": True,
        "reason_code": "ready",
        "provider": "codex",
        "model": "gpt-image-2",
    }
    pinned_endpoint = "https://pinned-image.example.test/v1"
    pinned_secret = "pinned-image-secret"
    from agent.image_gen_verification import (
        authorize_image_gen_request_binding,
    )

    binding = authorize_image_gen_request_binding(
        ImageGenRequestBinding(
            provider="codex",
            model="gpt-image-2",
            api_key=pinned_secret,
            runtime_identity={
                "identity_supported": True,
                "endpoint_resolved": True,
                "endpoint": pinned_endpoint,
            },
        ),
        authorization_fingerprint=verified["fingerprint"],
        authorization_generation=verified[
            "_authorization_generation"
        ],
    )
    build_calls = []

    def build_decision(*args, **kwargs):
        build_calls.append((args, kwargs))
        return decision_factory(*args, **kwargs)

    monkeypatch.setattr(
        image_runtime,
        "build_capability_route_decision",
        build_decision,
    )
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(verified),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kwargs: binding,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-before-reauth")
    monkeypatch.setenv(
        "OPENAI_BASE_URL",
        "https://ambient-before-reauth.example.test/v1",
    )
    reauth_calls = []

    def final_reauth(_snapshot):
        reauth_calls.append(True)
        # Authorization has succeeded. Rotate the ambient process state before
        # the Provider consumes its request-local binding.
        monkeypatch.setenv("OPENAI_API_KEY", "ambient-after-reauth")
        monkeypatch.setenv(
            "OPENAI_BASE_URL",
            "https://ambient-after-reauth.example.test/v1",
        )
        return True

    monkeypatch.setattr(
        image_generation_tool,
        "_same_authorization_snapshot",
        final_reauth,
    )
    monkeypatch.setattr(
        plugins_module,
        "_ensure_plugins_discovered",
        lambda *args, **kwargs: None,
    )
    provider_calls = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    generated_image = _write_cached_png(
        tmp_path,
        "pinned-binding.png",
    )

    class BindingAwareProvider(ImageGenProvider):
        _supports_pinned_image_request_binding = True

        @property
        def name(self) -> str:
            return "codex"

        def generate(self, prompt, aspect_ratio="landscape", **kwargs):
            kwargs["_reauth_guard"]()
            provider_calls.append(kwargs)
            return {
                "success": True,
                "provider": "codex",
                "model": kwargs.get("model"),
                "image": str(generated_image),
            }

    provider = BindingAwareProvider()
    monkeypatch.setattr(
        registry_module,
        "get_provider",
        lambda name: provider if name == "codex" else None,
    )

    turn_id = "provider-pinned-binding-turn"
    owner = "provider-pinned-binding-owner"
    begin_image_generation_task(
        turn_id,
        allow_generation=True,
        owner_token=owner,
    )
    try:
        result = json.loads(
            image_generation_tool._handle_image_generate(
                {"prompt": "draw the pinned route", "aspect_ratio": "portrait"},
                caller_capability_fingerprint=verified["fingerprint"],
                caller_capability_generation=verified[
                    "_authorization_generation"
                ],
                image_generation_task_id=turn_id,
                image_generation_gate_owner=owner,
            )
        )
    finally:
        cleanup_image_generation_task(turn_id, owner_token=owner)

    observed_binding = (
        provider_calls[0].get("_runtime_binding")
        if len(provider_calls) == 1
        else None
    )
    violations = []
    if result.get("success") is not True:
        violations.append("pinned image dispatch unexpectedly failed")
    if len(build_calls) != 1:
        violations.append("dispatch did not freeze one route decision")
    if reauth_calls != [True, True]:
        violations.append(
            "dispatch did not reauthorize at final dispatch and Provider I/O"
        )
    if len(provider_calls) != 1:
        violations.append("Provider was not called exactly once")
    if observed_binding is not binding:
        violations.append("Provider did not receive the private pinned binding")
    if (
        observed_binding is not None
        and observed_binding.api_key != pinned_secret
    ):
        violations.append("Provider binding secret changed with ambient state")
    if (
        observed_binding is not None
        and observed_binding.runtime_identity["endpoint"] != pinned_endpoint
    ):
        violations.append("Provider binding endpoint changed with ambient state")
    if os.environ["OPENAI_API_KEY"] != "ambient-after-reauth":
        violations.append("test did not rotate the ambient secret after reauth")
    if (
        os.environ["OPENAI_BASE_URL"]
        != "https://ambient-after-reauth.example.test/v1"
    ):
        violations.append("test did not rotate the ambient endpoint after reauth")
    assert violations == [], "; ".join(violations)
