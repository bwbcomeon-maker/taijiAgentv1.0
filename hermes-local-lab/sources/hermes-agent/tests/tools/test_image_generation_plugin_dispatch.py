from __future__ import annotations

import base64
import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import yaml

from agent import image_gen_registry
from agent.image_gen_provider import ImageGenProvider


@pytest.fixture(autouse=True)
def _reset_registry():
    image_gen_registry._reset_for_tests()
    yield
    image_gen_registry._reset_for_tests()


class _FakeCodexProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "codex"

    def generate(self, prompt, aspect_ratio="landscape", **kwargs):
        return {
            "success": True,
            "image": "/tmp/codex-test.png",
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
        assert payload["image"] == "/tmp/codex-test.png"
        assert payload["aspect_ratio"] == "square"

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
                raw = image_generation_tool._dispatch_to_plugin_provider(
                    f"probe-{profile}",
                    "square",
                    runtime_snapshot={
                        "provider": "custom:router",
                        "model": profiles[profile]["model"],
                    },
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
        image_gen_runtime_identity,
    )

    module = __import__(module_name, fromlist=[class_name])
    provider = getattr(module, class_name)()
    captured = {}

    def live_key_must_not_be_read(*_args, **_kwargs):
        raise AssertionError("pinned probe fell back to live credential")

    def fake_post_json(**kwargs):
        captured["authorization"] = kwargs["headers"]["Authorization"]
        captured["url"] = kwargs["url"]
        return {"data": [{"url": "https://cdn.example.test/image.png"}]}, None

    monkeypatch.setattr(module, "provider_api_key", live_key_must_not_be_read)
    monkeypatch.setattr(module, "post_json", fake_post_json)
    monkeypatch.setattr(
        module,
        "cached_success",
        lambda **kwargs: {
            "success": True,
            "image": "/tmp/pinned-probe.png",
            "provider": provider_name,
            "model": kwargs["model"],
        },
    )
    binding = ImageGenRequestBinding(
        provider=provider_name,
        model=model,
        api_key="pinned-secret",
        runtime_identity=image_gen_runtime_identity(
            provider_name,
            {"provider": provider_name, "model": model},
        ),
    )

    result = provider.generate(
        prompt="probe",
        aspect_ratio="square",
        model=model,
        _runtime_binding=binding,
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
    binding = ImageGenRequestBinding(
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
    )

    result = DashScopeQwenImageProvider().generate(
        prompt="probe",
        aspect_ratio="square",
        model="qwen-image-2.0-pro",
        _runtime_binding=binding,
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
    )

    rendered = repr(binding)
    assert "private-probe-secret" not in rendered
    assert endpoint not in rendered
    with pytest.raises(TypeError):
        binding.runtime_identity["endpoint"] = "https://mutated.invalid"
