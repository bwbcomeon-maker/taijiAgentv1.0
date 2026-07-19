"""Tests for user-configured OpenAI-compatible image providers."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import Headers


_PNG_HEX = (
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"
    "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082"
)


def _b64_png() -> str:
    return base64.b64encode(bytes.fromhex(_PNG_HEX)).decode()


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = Headers({"Content-Type": "application/json"})
        self.text = "fake-response-text"

    def json(self) -> Any:
        return self._payload

    def iter_bytes(self):
        yield json.dumps(self._payload).encode()

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        del exc_type, exc_value, traceback
        return False


def _bind_custom_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    credentials: dict[str, str],
) -> dict[str, str]:
    from agent.provider_credentials import credential_secret_env

    rows = []
    secret_envs: dict[str, str] = {}
    for credential_ref, secret in credentials.items():
        secret_env = credential_secret_env(credential_ref)
        secret_envs[credential_ref] = secret_env
        rows.append({
            "id": credential_ref,
            "provider_family": "custom",
            "auth_type": "api_key",
            "secret_env": secret_env,
        })
        if secret:
            monkeypatch.setenv(secret_env, secret)
        else:
            monkeypatch.delenv(secret_env, raising=False)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"provider_credentials": rows}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    return secret_envs


def _install_safe_bridge(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    *,
    payload: dict[str, Any],
    status_code: int = 200,
) -> tuple[list[dict[str, Any]], list[_FakeResponse]]:
    request_calls: list[dict[str, Any]] = []
    read_calls: list[_FakeResponse] = []
    real_reader = module.read_bounded_json

    def fake_request_pinned_https(**kwargs: Any) -> _FakeResponse:
        request_calls.append(kwargs)
        return _FakeResponse(status_code=status_code, payload=payload)

    def bounded_reader(response: _FakeResponse) -> Any:
        read_calls.append(response)
        return real_reader(response)

    monkeypatch.setattr(module, "request_pinned_https", fake_request_pinned_https)
    monkeypatch.setattr(module, "read_bounded_json", bounded_reader)
    return request_calls, read_calls


def _entry(**overrides):
    data = {
        "id": "router",
        "name": "Router Images",
        "base_url": "https://images.example.com/v1",
        "credential_ref": "router-images",
        "models": ["gpt-image-custom"],
        "default_model": "gpt-image-custom",
        "size_map": {
            "landscape": "1536x1024",
            "square": "1024x1024",
            "portrait": "1024x1536",
        },
        "response_format": "b64_json",
        "timeout_seconds": 30,
    }
    data.update(overrides)
    return data


def test_configurable_provider_requires_key_and_base_url(monkeypatch, tmp_path):
    from agent.custom_image_providers import ConfigurableOpenAIImageProvider

    secret_envs = _bind_custom_credentials(
        monkeypatch,
        tmp_path,
        {"router-images": ""},
    )
    provider = ConfigurableOpenAIImageProvider(_entry())

    assert provider.name == "custom:router"
    assert provider.display_name == "Router Images"
    assert provider.default_model() == "gpt-image-custom"
    assert provider.is_available() is False

    monkeypatch.setenv(secret_envs["router-images"], "secret-key")
    assert provider.is_available() is True


def test_configurable_provider_rejects_insecure_http_base_url():
    from agent.custom_image_providers import normalize_custom_image_provider_entry

    with pytest.raises(ValueError, match="HTTPS"):
        normalize_custom_image_provider_entry(
            _entry(base_url="http://images.example.com/v1")
        )


def test_configurable_provider_posts_to_openai_compatible_endpoint(
    monkeypatch, tmp_path
):
    from agent import custom_image_providers
    from agent.custom_image_providers import ConfigurableOpenAIImageProvider

    _bind_custom_credentials(
        monkeypatch,
        tmp_path,
        {"router-images": "secret-key"},
    )
    calls, read_calls = _install_safe_bridge(
        monkeypatch,
        custom_image_providers,
        payload={"data": [{"b64_json": _b64_png()}]},
    )

    result = ConfigurableOpenAIImageProvider(_entry()).generate(
        "draw a quiet operations dashboard",
        aspect_ratio="landscape",
    )

    assert result["success"] is True
    assert result["provider"] == "custom:router"
    assert result["model"] == "gpt-image-custom"
    assert Path(result["image"]).exists()
    assert calls == [
        {
            "method": "POST",
            "url": "https://images.example.com/v1/images/generations",
            "network_scope": "public_direct",
            "headers": {
                "Authorization": "Bearer secret-key",
                "Content-Type": "application/json",
            },
            "json_body": {
                "model": "gpt-image-custom",
                "prompt": "draw a quiet operations dashboard",
                "size": "1536x1024",
                "n": 1,
                "response_format": "b64_json",
            },
            "timeout": 30,
            "follow_redirects": False,
        }
    ]
    assert len(read_calls) == 1


def test_configurable_provider_accepts_url_responses(monkeypatch, tmp_path):
    from agent import custom_image_providers
    from agent.custom_image_providers import ConfigurableOpenAIImageProvider

    saved = tmp_path / "cached.png"
    saved.write_bytes(b"png")
    _bind_custom_credentials(
        monkeypatch,
        tmp_path,
        {"router-images": "secret-key"},
    )
    _install_safe_bridge(
        monkeypatch,
        custom_image_providers,
        payload={"data": [{"url": "https://cdn.example.com/image.png"}]},
    )
    download_calls = []

    def fake_save_url_image(
        url,
        *,
        prefix,
        network_scope,
        trusted_proxy_profile,
    ):
        download_calls.append({
            "url": url,
            "prefix": prefix,
            "network_scope": network_scope,
            "trusted_proxy_profile": trusted_proxy_profile,
        })
        return saved

    monkeypatch.setattr(custom_image_providers, "save_url_image", fake_save_url_image)

    result = ConfigurableOpenAIImageProvider(_entry(response_format="url")).generate(
        "draw a field report",
        aspect_ratio="square",
    )

    assert result["success"] is True
    assert result["image"] == str(saved)
    assert result["provider"] == "custom:router"
    assert download_calls == [
        {
            "url": "https://cdn.example.com/image.png",
            "prefix": "custom_router_gpt-image-custom",
            "network_scope": "public_direct",
            "trusted_proxy_profile": None,
        }
    ]


def test_configurable_provider_sanitizes_model_id_for_cache_prefix(
    monkeypatch, tmp_path
):
    from agent import custom_image_providers
    from agent.custom_image_providers import ConfigurableOpenAIImageProvider

    saved = tmp_path / "cached.png"
    saved.write_bytes(b"png")
    prefixes = []

    _bind_custom_credentials(
        monkeypatch,
        tmp_path,
        {"router-images": "secret-key"},
    )
    _install_safe_bridge(
        monkeypatch,
        custom_image_providers,
        payload={"data": [{"url": "https://cdn.example.com/image.png"}]},
    )

    def fake_save_url_image(
        url,
        *,
        prefix,
        network_scope,
        trusted_proxy_profile,
    ):
        del url
        assert network_scope == "public_direct"
        assert trusted_proxy_profile is None
        prefixes.append(prefix)
        return saved

    monkeypatch.setattr(custom_image_providers, "save_url_image", fake_save_url_image)

    result = ConfigurableOpenAIImageProvider(
        _entry(
            models=["vendor/gpt-image-custom"], default_model="vendor/gpt-image-custom"
        )
    ).generate("draw a field report")

    assert result["success"] is True
    assert prefixes == ["custom_router_vendor_gpt-image-custom"]


def test_configurable_provider_maps_remote_errors_without_leaking_key(
    monkeypatch, tmp_path
):
    from agent import custom_image_providers
    from agent.custom_image_providers import ConfigurableOpenAIImageProvider

    _bind_custom_credentials(
        monkeypatch,
        tmp_path,
        {"router-images": "secret-key"},
    )
    _install_safe_bridge(
        monkeypatch,
        custom_image_providers,
        status_code=401,
        payload={"error": {"message": "bad api key secret-key"}},
    )

    result = ConfigurableOpenAIImageProvider(_entry()).generate("draw a chart")

    assert result["success"] is False
    assert result["error_type"] == "api_error"
    assert "secret-key" not in result["error"]
    assert "HTTP 401" in result["error"]


def test_register_configured_custom_image_providers_refreshes_stale_entries(
    monkeypatch, tmp_path
):
    from agent import image_gen_registry
    from agent.custom_image_providers import register_configured_custom_image_providers

    _bind_custom_credentials(
        monkeypatch,
        tmp_path,
        {
            "one-images": "one-secret",
            "two-images": "two-secret",
        },
    )
    image_gen_registry._reset_for_tests()
    register_configured_custom_image_providers({
        "custom_image_providers": [_entry(id="one", credential_ref="one-images")]
    })
    first = image_gen_registry.get_provider("custom:one")
    assert first is not None
    assert first.is_available() is True

    register_configured_custom_image_providers({
        "custom_image_providers": [_entry(id="two", credential_ref="two-images")]
    })

    assert image_gen_registry.get_provider("custom:one") is None
    second = image_gen_registry.get_provider("custom:two")
    assert second is not None
    assert second.is_available() is True
    image_gen_registry._reset_for_tests()
