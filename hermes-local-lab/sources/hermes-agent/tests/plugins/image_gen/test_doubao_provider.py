#!/usr/bin/env python3
"""Tests for the Doubao Seedream image generation provider."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_model_override(monkeypatch):
    monkeypatch.delenv("DOUBAO_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)


def _ark_response(url: str = "https://ark-content/result.png"):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "data": [
            {
                "url": url,
                "size": "2560x1440",
            }
        ]
    }
    return resp


class TestDoubaoProviderSurface:
    def test_name_and_default_model(self):
        from plugins.image_gen.doubao import DoubaoImageGenProvider

        provider = DoubaoImageGenProvider()
        assert provider.name == "doubao"
        assert provider.display_name == "Doubao Seedream"
        assert provider.default_model() == "doubao-seedream-5-0-260128"

    def test_list_models_includes_seedream_5_and_alias(self):
        from plugins.image_gen.doubao import DoubaoImageGenProvider

        ids = {item["id"] for item in DoubaoImageGenProvider().list_models()}
        assert "doubao-seedream-5-0-260128" in ids
        assert "doubao-seedream-5-0-lite-260128" in ids

    def test_setup_schema_advertises_ark_key(self):
        from plugins.image_gen.doubao import DoubaoImageGenProvider

        schema = DoubaoImageGenProvider().get_setup_schema()
        assert schema["name"] == "Doubao Seedream"
        assert schema["badge"] == "paid"
        env_keys = {entry["key"] for entry in schema.get("env_vars", [])}
        assert "ARK_API_KEY" in env_keys

    def test_is_available_requires_ark_key(self, monkeypatch):
        from plugins.image_gen.doubao import DoubaoImageGenProvider

        assert DoubaoImageGenProvider().is_available() is False
        monkeypatch.setenv("ARK_API_KEY", "ark-test-key")
        assert DoubaoImageGenProvider().is_available() is True


class TestDoubaoGenerate:
    def test_missing_api_key_returns_auth_required(self):
        from plugins.image_gen.doubao import DoubaoImageGenProvider

        result = DoubaoImageGenProvider().generate("A clean product photo")
        assert result["success"] is False
        assert result["error_type"] == "auth_required"
        assert "ARK_API_KEY" in result["error"]
        assert result["provider"] == "doubao"

    def test_empty_prompt_returns_invalid_argument(self, monkeypatch):
        monkeypatch.setenv("ARK_API_KEY", "ark-test-key")
        from plugins.image_gen.doubao import DoubaoImageGenProvider

        result = DoubaoImageGenProvider().generate("   ")
        assert result["success"] is False
        assert result["error_type"] == "invalid_argument"

    def test_successful_generation_posts_to_ark_images_api(self, monkeypatch):
        monkeypatch.setenv("ARK_API_KEY", "ark-test-key")
        from plugins.image_gen.doubao import DoubaoImageGenProvider

        response_body = _ark_response().json()
        with (
            patch(
                "plugins.image_gen.doubao.post_json",
                return_value=(response_body, None),
            ) as mock_post,
            patch(
                "plugins.image_gen.doubao.save_url_image",
                return_value=Path("/tmp/doubao-result.png"),
            ),
        ):
            result = DoubaoImageGenProvider().generate(
                prompt="A precise enterprise dashboard illustration",
                aspect_ratio="landscape",
            )

        assert result["success"] is True
        assert result["image"] == "/tmp/doubao-result.png"
        assert result["provider"] == "doubao"
        assert result["model"] == "doubao-seedream-5-0-260128"
        assert result["aspect_ratio"] == "landscape"
        assert result["size"] == "2560x1440"

        url = mock_post.call_args.kwargs["url"]
        headers = mock_post.call_args.kwargs["headers"]
        payload = mock_post.call_args.kwargs["payload"]
        assert url == "https://ark.cn-beijing.volces.com/api/v3/images/generations"
        assert headers["Authorization"] == "Bearer ark-test-key"
        assert payload == {
            "model": "doubao-seedream-5-0-260128",
            "prompt": "A precise enterprise dashboard illustration",
            "size": "2560x1440",
            "output_format": "png",
            "response_format": "url",
            "watermark": False,
        }

    def test_aspect_ratio_mapping(self, monkeypatch):
        monkeypatch.setenv("ARK_API_KEY", "ark-test-key")
        from plugins.image_gen.doubao import DoubaoImageGenProvider

        with (
            patch(
                "plugins.image_gen.doubao.post_json",
                return_value=(_ark_response().json(), None),
            ) as mock_post,
            patch(
                "plugins.image_gen.doubao.save_url_image",
                return_value=Path("/tmp/doubao-result.png"),
            ),
        ):
            DoubaoImageGenProvider().generate(prompt="portrait", aspect_ratio="portrait")

        assert mock_post.call_args.kwargs["payload"]["size"] == "1440x2560"

    def test_http_result_url_is_rejected_before_download(self, monkeypatch):
        monkeypatch.setenv("ARK_API_KEY", "ark-test-key")
        from plugins.image_gen import doubao

        saver = MagicMock()
        with (
            patch.object(
                doubao,
                "post_json",
                return_value=(
                    {"data": [{"url": "http://ark-content/result.png"}]},
                    None,
                ),
            ),
            patch.object(doubao, "save_url_image", saver),
        ):
            result = doubao.DoubaoImageGenProvider().generate(
                "A clean product photo"
            )

        assert result["success"] is False
        assert result["error_type"] == "invalid_response"
        saver.assert_not_called()

    def test_register_wires_provider(self):
        from plugins.image_gen.doubao import DoubaoImageGenProvider, register

        ctx = MagicMock()
        register(ctx)
        ctx.register_image_gen_provider.assert_called_once()
        (registered,), _ = ctx.register_image_gen_provider.call_args
        assert isinstance(registered, DoubaoImageGenProvider)
