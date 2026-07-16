"""Tests for user-configured OpenAI-compatible image providers."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest


_PNG_HEX = (
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"
    "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082"
)


def _b64_png() -> str:
    return base64.b64encode(bytes.fromhex(_PNG_HEX)).decode()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = {"content-type": "application/json"}
        self.text = "fake-response-text"

    def json(self):
        return self._payload


def _entry(**overrides):
    data = {
        "id": "router",
        "name": "Router Images",
        "base_url": "https://images.example.com/v1",
        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
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


def test_configurable_provider_requires_key_and_base_url(monkeypatch):
    from agent.custom_image_providers import ConfigurableOpenAIImageProvider

    monkeypatch.delenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", raising=False)
    provider = ConfigurableOpenAIImageProvider(_entry())

    assert provider.name == "custom:router"
    assert provider.display_name == "Router Images"
    assert provider.default_model() == "gpt-image-custom"
    assert provider.is_available() is False

    monkeypatch.setenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", "secret-key")
    assert provider.is_available() is True


def test_configurable_provider_rejects_insecure_http_base_url():
    from agent.custom_image_providers import normalize_custom_image_provider_entry

    with pytest.raises(ValueError, match="HTTPS"):
        normalize_custom_image_provider_entry(
            _entry(base_url="http://images.example.com/v1")
        )


def test_configurable_provider_posts_to_openai_compatible_endpoint(monkeypatch, tmp_path):
    from agent import custom_image_providers
    from agent.custom_image_providers import ConfigurableOpenAIImageProvider

    calls = []

    def fake_post(url, *, headers, json, timeout, allow_redirects):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        return _FakeResponse(payload={"data": [{"b64_json": _b64_png()}]})

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", "secret-key")
    monkeypatch.setattr(custom_image_providers.requests, "post", fake_post)

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
            "url": "https://images.example.com/v1/images/generations",
            "headers": {
                "Authorization": "Bearer secret-key",
                "Content-Type": "application/json",
            },
            "json": {
                "model": "gpt-image-custom",
                "prompt": "draw a quiet operations dashboard",
                "size": "1536x1024",
                "n": 1,
                "response_format": "b64_json",
            },
            "timeout": 30,
            "allow_redirects": False,
        }
    ]


def test_configurable_provider_accepts_url_responses(monkeypatch, tmp_path):
    from agent import custom_image_providers
    from agent.custom_image_providers import ConfigurableOpenAIImageProvider

    saved = tmp_path / "cached.png"
    saved.write_bytes(b"png")

    monkeypatch.setenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", "secret-key")
    monkeypatch.setattr(
        custom_image_providers.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(payload={"data": [{"url": "https://cdn.example.com/image.png"}]}),
    )
    monkeypatch.setattr(custom_image_providers, "save_url_image", lambda url, prefix: saved)

    result = ConfigurableOpenAIImageProvider(_entry(response_format="url")).generate(
        "draw a field report",
        aspect_ratio="square",
    )

    assert result["success"] is True
    assert result["image"] == str(saved)
    assert result["provider"] == "custom:router"


def test_configurable_provider_sanitizes_model_id_for_cache_prefix(monkeypatch, tmp_path):
    from agent import custom_image_providers
    from agent.custom_image_providers import ConfigurableOpenAIImageProvider

    saved = tmp_path / "cached.png"
    saved.write_bytes(b"png")
    prefixes = []

    monkeypatch.setenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", "secret-key")
    monkeypatch.setattr(
        custom_image_providers.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(payload={"data": [{"url": "https://cdn.example.com/image.png"}]}),
    )

    def fake_save_url_image(url, prefix):
        prefixes.append(prefix)
        return saved

    monkeypatch.setattr(custom_image_providers, "save_url_image", fake_save_url_image)

    result = ConfigurableOpenAIImageProvider(
        _entry(models=["vendor/gpt-image-custom"], default_model="vendor/gpt-image-custom")
    ).generate("draw a field report")

    assert result["success"] is True
    assert prefixes == ["custom_router_vendor_gpt-image-custom"]


def test_configurable_provider_maps_remote_errors_without_leaking_key(monkeypatch):
    from agent import custom_image_providers
    from agent.custom_image_providers import ConfigurableOpenAIImageProvider

    monkeypatch.setenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", "secret-key")
    monkeypatch.setattr(
        custom_image_providers.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            status_code=401,
            payload={"error": {"message": "bad api key secret-key"}},
        ),
    )

    result = ConfigurableOpenAIImageProvider(_entry()).generate("draw a chart")

    assert result["success"] is False
    assert result["error_type"] == "api_error"
    assert "secret-key" not in result["error"]
    assert "HTTP 401" in result["error"]


def test_register_configured_custom_image_providers_refreshes_stale_entries(monkeypatch):
    from agent import image_gen_registry
    from agent.custom_image_providers import register_configured_custom_image_providers

    image_gen_registry._reset_for_tests()
    register_configured_custom_image_providers({"custom_image_providers": [_entry(id="one")]})
    assert image_gen_registry.get_provider("custom:one") is not None

    register_configured_custom_image_providers({"custom_image_providers": [_entry(id="two")]})

    assert image_gen_registry.get_provider("custom:one") is None
    assert image_gen_registry.get_provider("custom:two") is not None
    image_gen_registry._reset_for_tests()
