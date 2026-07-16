#!/usr/bin/env python3
"""Tests for built-in domestic image generation providers."""

from __future__ import annotations

import base64
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
import yaml


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in (
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_WORKSPACE_ID",
        "DASHSCOPE_REGION",
        "DASHSCOPE_ENDPOINT_MODE",
        "DASHSCOPE_BASE_URL",
        "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY",
        "QIANFAN_API_KEY",
        "GLM_API_KEY",
        "MINIMAX_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _response(payload: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


def _http_error_response(secret: str):
    resp = MagicMock()
    resp.status_code = 401
    resp.raise_for_status.side_effect = requests.HTTPError(f"401 leaked {secret}")
    resp.text = f"auth failed {secret}"
    resp.json.return_value = {"error": {"message": f"bad key {secret}"}}
    return resp


def _download_response(*, status=200, headers=None, chunks=()):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    resp.iter_content.return_value = iter(chunks)
    return resp


class TestDashScopeQwenImageProvider:
    def test_safe_image_download_delegates_to_pinned_agent_transport(
        self, monkeypatch
    ):
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
        from plugins.image_gen import dashscope

        with (
            patch.object(
                dashscope, "save_url_image", return_value=Path("/tmp/safe.png")
            ) as safe_saver,
            patch.object(dashscope.requests, "get") as legacy_get,
        ):
            saved = dashscope._save_safe_image_url(
                "https://cdn.example/image.png"
            )

        assert saved == Path("/tmp/safe.png")
        legacy_get.assert_not_called()
        kwargs = safe_saver.call_args.kwargs
        assert kwargs["max_bytes"] == dashscope.MAX_IMAGE_BYTES
        assert kwargs["max_pixels"] == dashscope.MAX_IMAGE_PIXELS
        assert kwargs["max_redirects"] == dashscope.MAX_IMAGE_REDIRECTS
        assert kwargs["url_validator"] is dashscope._dashscope_url_shape_allowed
        assert kwargs["address_validator"] is dashscope._dashscope_address_allowed

    def test_dashscope_transport_policy_allows_public_and_only_scoped_fake_ip(self):
        from plugins.image_gen import dashscope

        assert dashscope._dashscope_url_shape_allowed(
            "https://cdn.example/image.png"
        )
        assert not dashscope._dashscope_url_shape_allowed(
            "http://cdn.example/image.png"
        )
        assert not dashscope._dashscope_url_shape_allowed(
            "https://user@cdn.example/image.png"
        )
        assert dashscope._dashscope_address_allowed(
            "cdn.example", "93.184.216.34"
        )
        assert dashscope._dashscope_address_allowed(
            "dashscope-7c2c.oss-accelerate.aliyuncs.com", "198.18.2.13"
        )
        assert not dashscope._dashscope_address_allowed(
            "evil.example", "198.18.2.13"
        )
        assert not dashscope._dashscope_address_allowed(
            "dashscope-7c2c.oss-accelerate.aliyuncs.com", "127.0.0.1"
        )

    def test_surface_and_setup_schema(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        schema = provider.get_setup_schema()

        assert provider.name == "dashscope"
        assert provider.default_model() == "qwen-image-2.0-pro"
        assert schema["domestic"] is True
        assert schema["integration_status"] == "stable"
        assert schema["supported_regions"] == ["cn-beijing", "ap-southeast-1"]
        assert [field["env_var"] for field in schema["credential_fields"]] == [
            "DASHSCOPE_API_KEY",
            "DASHSCOPE_ENDPOINT_MODE",
            "DASHSCOPE_WORKSPACE_ID",
            "DASHSCOPE_REGION",
            "DASHSCOPE_BASE_URL",
        ]
        fields = {field["name"]: field for field in schema["credential_fields"]}
        assert fields["endpoint_mode"]["placeholder"] == "public"
        assert fields["endpoint_mode"]["options"][0]["value"] == "public"
        assert fields["workspace_id"]["placeholder"] == "llm-demo"


    def test_normalized_schema_keeps_non_secret_access_key_id_in_credentials(self):
        from plugins.image_gen.domestic_common import normalized_setup_contract

        contract = normalized_setup_contract(
            {"auth_type": "access_key_secret"},
            provider_family="example",
            capabilities=("image_generation",),
            transport="vendor_signed_request",
        )

        assert [field["name"] for field in contract["credential_fields"]] == [
            "access_key_id",
            "access_key_secret",
        ]
        assert contract["endpoint_fields"] == []
        assert contract["auth_editable"] is False

    def test_missing_credentials_return_auth_required(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        result = DashScopeQwenImageProvider().generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "auth_required"
        assert "DASHSCOPE_API_KEY" in result["error"]
        assert "DASHSCOPE_WORKSPACE_ID" not in result["error"]

    def test_api_key_only_posts_to_beijing_public_endpoint(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        payload = {"output": {"image": "https://dashscope/result.png"}}
        with (
            patch(
                "plugins.image_gen.dashscope.requests.post",
                return_value=_response(payload),
            ) as mock_post,
            patch(
                "plugins.image_gen.dashscope._save_safe_image_url",
                return_value=Path("/tmp/result.png"),
            ),
        ):
            provider = DashScopeQwenImageProvider()
            assert provider.is_available() is True
            result = provider.generate("A city skyline")

        assert result["success"] is True
        assert mock_post.call_args.args[0] == (
            "https://dashscope.aliyuncs.com/api/v1/services/"
            "aigc/multimodal-generation/generation"
        )

    def test_empty_prompt_returns_invalid_argument(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "llm-demo")
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        result = DashScopeQwenImageProvider().generate("   ")

        assert result["success"] is False
        assert result["error_type"] == "invalid_argument"

    def test_successful_generation_posts_to_workspace_endpoint(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "workspace")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "llm-demo")
        monkeypatch.setenv("DASHSCOPE_REGION", "cn-beijing")
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        payload = {
            "output": {
                "choices": [
                    {"message": {"content": [{"image": "https://dashscope/result.png"}]}}
                ]
            }
        }
        with (
            patch("plugins.image_gen.dashscope.requests.post", return_value=_response(payload)) as mock_post,
            patch(
                "plugins.image_gen.dashscope._save_safe_image_url",
                return_value=Path("/tmp/dashscope-result.png"),
            ) as mock_save,
        ):
            result = DashScopeQwenImageProvider().generate(
                prompt="A precise enterprise dashboard illustration",
                aspect_ratio="landscape",
            )

        assert result["success"] is True
        assert result["image"] == "/tmp/dashscope-result.png"
        assert result["provider"] == "dashscope"
        assert result["model"] == "qwen-image-2.0-pro"
        assert mock_save.call_args.args[0] == "https://dashscope/result.png"
        assert (
            mock_post.call_args.args[0]
            == "https://llm-demo.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        )
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer dashscope-secret"
        assert mock_post.call_args.kwargs["json"]["parameters"]["size"] == "1664*928"

    def test_named_credential_is_used_for_availability_and_generation(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://legacy.example.com")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "legacy-workspace")
        monkeypatch.setenv("DASHSCOPE_REGION", "ap-southeast-1")
        monkeypatch.setenv(
            "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", "named-dashscope-secret"
        )
        config = {
            "provider_credentials": [
                {
                    "id": "alibaba-default",
                    "provider_family": "alibaba_dashscope",
                    "label": "Alibaba default",
                    "auth_type": "api_key",
                    "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
                }
            ],
            "image_gen": {
                "provider": "dashscope",
                "credential_ref": "alibaba-default",
                "options": {"workspace_id": "llm-demo", "region": "cn-beijing"},
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        payload = {"output": {"image": "https://dashscope/result.png"}}
        with (
            patch(
                "plugins.image_gen.dashscope.requests.post",
                return_value=_response(payload),
            ) as mock_post,
            patch(
                "plugins.image_gen.dashscope._save_safe_image_url",
                return_value=Path("/tmp/result.png"),
            ),
        ):
            provider = DashScopeQwenImageProvider()
            assert provider.is_available() is True
            result = provider.generate("A city skyline")

        assert result["success"] is True
        assert (
            mock_post.call_args.kwargs["headers"]["Authorization"]
            == "Bearer named-dashscope-secret"
        )
        assert mock_post.call_args.args[0] == (
            "https://llm-demo.cn-beijing.maas.aliyuncs.com/api/v1/services/"
            "aigc/multimodal-generation/generation"
        )

    def test_quick_credential_public_options_ignore_legacy_endpoint_env(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv(
            "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY", "quick-secret"
        )
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://legacy.example.com")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "legacy-workspace")
        monkeypatch.setenv("DASHSCOPE_REGION", "ap-southeast-1")
        config = {
            "provider_credentials": [
                {
                    "id": "taiji-alibaba-quick",
                    "provider_family": "alibaba_dashscope",
                    "label": "Alibaba quick setup",
                    "auth_type": "api_key",
                    "secret_env": (
                        "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY"
                    ),
                }
            ],
            "image_gen": {
                "provider": "dashscope",
                "credential_ref": "taiji-alibaba-quick",
                "options": {
                    "endpoint_mode": "public",
                    "region": "cn-beijing",
                },
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        payload = {"output": {"image": "https://dashscope/result.png"}}
        with (
            patch(
                "plugins.image_gen.dashscope.requests.post",
                return_value=_response(payload),
            ) as mock_post,
            patch(
                "plugins.image_gen.dashscope._save_safe_image_url",
                return_value=Path("/tmp/result.png"),
            ),
        ):
            result = DashScopeQwenImageProvider().generate("A city skyline")

        assert result["success"] is True
        assert mock_post.call_args.args[0] == (
            "https://dashscope.aliyuncs.com/api/v1/services/"
            "aigc/multimodal-generation/generation"
        )
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
            "Bearer quick-secret"
        )

    def test_named_credential_config_load_failure_never_uses_legacy_state(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-must-not-be-used")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "legacy-workspace")
        config_path = tmp_path / "config.yaml"
        config_path.write_text("image_gen: [\n", encoding="utf-8")
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        with (
            patch("plugins.image_gen.dashscope.requests.post") as mock_post,
        ):
            provider = DashScopeQwenImageProvider()
            assert provider.is_available() is False
            result = provider.generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "configuration_error"
        mock_post.assert_not_called()

    def test_named_credential_config_read_error_never_uses_legacy_state(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-must-not-be-used")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "legacy-workspace")
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "image_gen": {
                        "provider": "dashscope",
                        "credential_ref": "alibaba-default",
                        "options": {"workspace_id": "named-workspace"},
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
        original_read_text = Path.read_text

        def deny_config_read(path, *args, **kwargs):
            if path == config_path:
                raise PermissionError("config unreadable")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", deny_config_read)
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        with patch("plugins.image_gen.dashscope.requests.post") as mock_post:
            provider = DashScopeQwenImageProvider()
            assert provider.is_available() is False
            result = provider.generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "configuration_error"
        mock_post.assert_not_called()

    def test_named_credential_never_falls_back_to_legacy_key(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-must-not-be-used")
        config = {
            "provider_credentials": [
                {
                    "id": "alibaba-image",
                    "provider_family": "alibaba_dashscope",
                    "label": "Alibaba image",
                    "auth_type": "api_key",
                    "secret_env": "TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY",
                }
            ],
            "image_gen": {
                "provider": "dashscope",
                "credential_ref": "alibaba-image",
                "options": {"workspace_id": "llm-demo"},
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        assert provider.is_available() is False
        result = provider.generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "auth_required"

    def test_selected_model_is_sent_unchanged(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "llm-demo")
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        with (
            patch(
                "plugins.image_gen.dashscope.requests.post",
                return_value=_response({"output": {"image": "https://dashscope/result.png"}}),
            ) as mock_post,
            patch(
                "plugins.image_gen.dashscope._save_safe_image_url",
                return_value=Path("/tmp/result.png"),
            ),
        ):
            result = provider.generate("A city skyline", model="qwen-image")

        assert result["success"] is True
        assert mock_post.call_args.kwargs["json"]["model"] == "qwen-image"

    def test_unknown_model_is_rejected(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        with pytest.raises(ValueError, match="Unsupported DashScope image model"):
            DashScopeQwenImageProvider()._model("unknown-model")

    def test_unknown_model_returns_structured_error_from_public_generate(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        result = DashScopeQwenImageProvider().generate(
            "A city skyline", model="unknown-model"
        )

        assert result["success"] is False
        assert result["error_type"] == "invalid_argument"
        assert result["provider"] == "dashscope"
        assert result["model"] == "unknown-model"

    def test_custom_endpoint_accepts_full_generation_url(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv(
            "DASHSCOPE_BASE_URL",
            "https://gateway.example.com/api/v1/services/aigc/multimodal-generation/generation",
        )
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))
            ],
        )
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        assert provider.is_available() is True
        assert provider._endpoint() == (
            "https://gateway.example.com/api/v1/services/aigc/multimodal-generation/generation"
        )

    @pytest.mark.parametrize(
        ("resolved_ip", "expected"),
        [("127.0.0.1", False), ("10.0.0.5", False), ("8.8.8.8", True)],
    )
    def test_custom_availability_uses_url_safety_dns_resolution(
        self, monkeypatch, resolved_ip, expected
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://gateway.example.com")
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (resolved_ip, 0))
            ],
        )
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        assert DashScopeQwenImageProvider().is_available() is expected

    def test_custom_availability_is_false_when_dns_fails(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://gateway.example.com")

        def fail_dns(*args, **kwargs):
            raise socket.gaierror("not found")

        monkeypatch.setattr(socket, "getaddrinfo", fail_dns)
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        assert DashScopeQwenImageProvider().is_available() is False

    def test_safe_image_download_rejects_direct_private_url(self, monkeypatch):
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image", side_effect=ValueError("unsafe image URL")
        ) as safe_saver, pytest.raises(ValueError, match="safety"):
            dashscope._save_safe_image_url("https://private.example/image.png")
        safe_saver.assert_called_once()

    def test_safe_image_download_rejects_public_redirect_to_private(self, monkeypatch):
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image", side_effect=ValueError("unsafe image URL")
        ) as safe_saver, pytest.raises(ValueError, match="safety"):
            dashscope._save_safe_image_url("https://public.example/image.png")
        assert safe_saver.call_args.kwargs["max_redirects"] == 3

    def test_safe_image_download_follows_public_redirect_and_saves_image(self, monkeypatch):
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image", return_value=Path("/tmp/safe.png")
        ) as safe_saver:
            saved = dashscope._save_safe_image_url(
                "https://public.example/image.png"
            )
        assert saved == Path("/tmp/safe.png")
        assert safe_saver.call_args.kwargs["url_validator"](
            "https://cdn.example/image.png"
        )

    def test_safe_image_download_allows_dashscope_oss_fake_ip(self, monkeypatch):
        from plugins.image_gen import dashscope

        assert dashscope._dashscope_address_allowed(
            "dashscope-7c2c.oss-accelerate.aliyuncs.com", "198.18.2.13"
        )

    def test_safe_image_download_redacts_signed_url_on_connection_error(
        self, monkeypatch
    ):
        signed_url = (
            "https://dashscope-7c2c.oss-accelerate.aliyuncs.com/output.png"
            "?Signature=must-not-leak&AccessKeyId=must-not-leak"
        )
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image",
            side_effect=RuntimeError(f"connection failed for {signed_url}"),
        ), pytest.raises(ValueError) as exc_info:
            dashscope._save_safe_image_url(signed_url)

        assert str(exc_info.value) == "DashScope image download request failed"
        assert "Signature" not in str(exc_info.value)

    def test_safe_image_download_redacts_signed_url_on_http_error(
        self, monkeypatch
    ):
        signed_url = (
            "https://dashscope-7c2c.oss-accelerate.aliyuncs.com/output.png"
            "?Signature=must-not-leak"
        )
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image",
            side_effect=RuntimeError(f"403 Client Error for url: {signed_url}"),
        ), pytest.raises(ValueError) as exc_info:
            dashscope._save_safe_image_url(signed_url)

        assert str(exc_info.value) == "DashScope image download request failed"
        assert "Signature" not in str(exc_info.value)

    def test_safe_image_download_redacts_signed_url_on_stream_error(
        self, monkeypatch
    ):
        signed_url = (
            "https://dashscope-7c2c.oss-accelerate.aliyuncs.com/output.png"
            "?Signature=must-not-leak"
        )
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image",
            side_effect=RuntimeError(f"stream failed for {signed_url}"),
        ), pytest.raises(ValueError) as exc_info:
            dashscope._save_safe_image_url(signed_url)

        assert str(exc_info.value) == "DashScope image download request failed"
        assert "Signature" not in str(exc_info.value)

    @pytest.mark.parametrize("private_ip", ["127.0.0.1", "10.0.0.8", "192.168.1.9"])
    def test_dashscope_oss_exception_only_allows_proxy_benchmark_range(
        self, monkeypatch, private_ip
    ):
        from plugins.image_gen.dashscope import _dashscope_address_allowed

        assert not _dashscope_address_allowed(
            "dashscope-7c2c.oss-accelerate.aliyuncs.com", private_ip
        )

    @pytest.mark.parametrize(
        "url",
        [
            "http://dashscope-7c2c.oss-accelerate.aliyuncs.com/output.png",
            "https://user@dashscope-7c2c.oss-accelerate.aliyuncs.com/output.png",
            "https://dashscope-7c2c.oss-accelerate.aliyuncs.com:8443/output.png",
            "https://dashscope-7c2c.oss-accelerate.aliyuncs.com.evil.example/output.png",
            "https://metadata.google.internal/latest/meta-data",
        ],
    )
    def test_safe_image_download_never_weakens_url_safety_floor(
        self, monkeypatch, url
    ):
        from urllib.parse import urlparse
        from plugins.image_gen.dashscope import (
            _dashscope_address_allowed,
            _dashscope_url_shape_allowed,
        )

        assert (
            not _dashscope_url_shape_allowed(url)
            or not _dashscope_address_allowed(
                str(urlparse(url).hostname or ""), "198.18.2.13"
            )
        )

    @pytest.mark.parametrize(
        "base_url",
        ["https://gateway.example.com:not-a-port", "https://gateway.example.com:0"],
    )
    def test_custom_endpoint_with_bad_port_is_unavailable_and_never_requested(
        self, monkeypatch, base_url
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", base_url)
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        assert provider.is_available() is False
        with patch("plugins.image_gen.dashscope.requests.post") as mock_post:
            result = provider.generate("A city skyline")
        assert result["success"] is False
        assert result["error_type"] == "endpoint_invalid"
        mock_post.assert_not_called()

    @pytest.mark.parametrize("hostname", ["localtest.me", "127.0.0.1.nip.io"])
    def test_custom_endpoint_resolving_to_loopback_is_rejected_before_request(
        self, monkeypatch, hostname
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", f"https://{hostname}")
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))
            ],
        )
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        assert provider.is_available() is False
        with patch("plugins.image_gen.dashscope.requests.post") as mock_post:
            result = provider.generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "endpoint_invalid"
        mock_post.assert_not_called()

    def test_custom_public_endpoint_disables_redirects(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://public.example")
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))
            ],
        )
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        with (
            patch(
                "plugins.image_gen.dashscope.requests.post",
                return_value=_response({"output": {"image": "https://dashscope/result.png"}}),
            ) as mock_post,
            patch(
                "plugins.image_gen.dashscope._save_safe_image_url",
                return_value=Path("/tmp/result.png"),
            ),
        ):
            result = DashScopeQwenImageProvider().generate("A city skyline")

        assert result["success"] is True
        assert mock_post.call_args.kwargs["allow_redirects"] is False

    @pytest.mark.parametrize(
        ("values", "available"),
        [
            ({"DASHSCOPE_API_KEY": "key"}, True),
            ({"DASHSCOPE_API_KEY": "key", "DASHSCOPE_ENDPOINT_MODE": "public"}, True),
            ({"DASHSCOPE_API_KEY": "key", "DASHSCOPE_ENDPOINT_MODE": "workspace"}, False),
            (
                {
                    "DASHSCOPE_API_KEY": "key",
                    "DASHSCOPE_ENDPOINT_MODE": "custom",
                    "DASHSCOPE_BASE_URL": "https://gateway.example.com",
                },
                True,
            ),
            ({"DASHSCOPE_API_KEY": "key", "DASHSCOPE_ENDPOINT_MODE": "custom"}, False),
        ],
    )
    def test_availability_requires_complete_endpoint_configuration(self, monkeypatch, values, available):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))
            ],
        )
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        assert DashScopeQwenImageProvider().is_available() is available

    def test_http_error_redacts_secret(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "ws-cn-test")
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        with patch(
            "plugins.image_gen.dashscope.requests.post",
            return_value=_http_error_response("dashscope-secret"),
        ):
            result = DashScopeQwenImageProvider().generate("A city skyline")

        assert result["success"] is False
        assert "dashscope-secret" not in result["error"]

    def test_register_wires_provider(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider, register

        ctx = MagicMock()
        register(ctx)
        (registered,), _ = ctx.register_image_gen_provider.call_args
        assert isinstance(registered, DashScopeQwenImageProvider)


class TestQianfanImageProvider:
    def test_successful_generation_posts_to_baidu_endpoint(self, monkeypatch):
        monkeypatch.setenv("QIANFAN_API_KEY", "qianfan-secret")
        from plugins.image_gen.qianfan import QianfanImageGenProvider

        with (
            patch(
                "plugins.image_gen.qianfan.requests.post",
                return_value=_response({"data": [{"url": "https://qianfan/result.png"}]}),
            ) as mock_post,
            patch(
                "plugins.image_gen.qianfan.save_url_image",
                return_value=Path("/tmp/qianfan-result.png"),
            ),
        ):
            result = QianfanImageGenProvider().generate("A reliable grid control room")

        assert result["success"] is True
        assert result["image"] == "/tmp/qianfan-result.png"
        assert result["provider"] == "qianfan"
        assert mock_post.call_args.args[0] == "https://qianfan.baidubce.com/v2/images/generations"
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer qianfan-secret"
        assert mock_post.call_args.kwargs["json"]["model"] == "qwen-image"
        assert mock_post.call_args.kwargs["json"]["size"] == "1536x1024"

    def test_surface_auth_prompt_and_register(self, monkeypatch):
        from plugins.image_gen.qianfan import QianfanImageGenProvider, register

        provider = QianfanImageGenProvider()
        schema = provider.get_setup_schema()
        assert schema["domestic"] is True
        assert schema["integration_status"] == "stable"
        assert schema["credential_fields"][0]["env_var"] == "QIANFAN_API_KEY"
        assert provider.generate("text")["error_type"] == "auth_required"
        monkeypatch.setenv("QIANFAN_API_KEY", "qianfan-secret")
        assert provider.generate(" ")["error_type"] == "invalid_argument"
        ctx = MagicMock()
        register(ctx)
        (registered,), _ = ctx.register_image_gen_provider.call_args
        assert isinstance(registered, QianfanImageGenProvider)

    def test_http_error_redacts_secret(self, monkeypatch):
        monkeypatch.setenv("QIANFAN_API_KEY", "qianfan-secret")
        from plugins.image_gen.qianfan import QianfanImageGenProvider

        with patch(
            "plugins.image_gen.qianfan.requests.post",
            return_value=_http_error_response("qianfan-secret"),
        ):
            result = QianfanImageGenProvider().generate("A city skyline")

        assert result["success"] is False
        assert "qianfan-secret" not in result["error"]


class TestZhipuImageProvider:
    def test_successful_generation_posts_to_bigmodel_endpoint(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "glm-secret")
        from plugins.image_gen.zhipu_image import ZhipuImageGenProvider

        with (
            patch(
                "plugins.image_gen.zhipu_image.requests.post",
                return_value=_response({"data": [{"url": "https://zhipu/result.png"}]}),
            ) as mock_post,
            patch(
                "plugins.image_gen.zhipu_image.save_url_image",
                return_value=Path("/tmp/zhipu-result.png"),
            ),
        ):
            result = ZhipuImageGenProvider().generate("A reliable grid control room")

        assert result["success"] is True
        assert result["image"] == "/tmp/zhipu-result.png"
        assert result["provider"] == "zhipu-image"
        assert mock_post.call_args.args[0] == "https://open.bigmodel.cn/api/paas/v4/images/generations"
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer glm-secret"
        assert mock_post.call_args.kwargs["json"]["model"] == "glm-image"
        assert mock_post.call_args.kwargs["json"]["size"] == "1536x1024"

    def test_surface_auth_prompt_and_register(self, monkeypatch):
        from plugins.image_gen.zhipu_image import ZhipuImageGenProvider, register

        provider = ZhipuImageGenProvider()
        schema = provider.get_setup_schema()
        assert provider.name == "zhipu-image"
        assert schema["domestic"] is True
        assert schema["credential_fields"][0]["env_var"] == "GLM_API_KEY"
        assert provider.generate("text")["error_type"] == "auth_required"
        monkeypatch.setenv("GLM_API_KEY", "glm-secret")
        assert provider.generate(" ")["error_type"] == "invalid_argument"
        ctx = MagicMock()
        register(ctx)
        (registered,), _ = ctx.register_image_gen_provider.call_args
        assert isinstance(registered, ZhipuImageGenProvider)

    def test_http_error_redacts_secret(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "glm-secret")
        from plugins.image_gen.zhipu_image import ZhipuImageGenProvider

        with patch(
            "plugins.image_gen.zhipu_image.requests.post",
            return_value=_http_error_response("glm-secret"),
        ):
            result = ZhipuImageGenProvider().generate("A city skyline")

        assert result["success"] is False
        assert "glm-secret" not in result["error"]


class TestMiniMaxImageProvider:
    def test_successful_generation_posts_to_minimax_endpoint(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "minimax-secret")
        from plugins.image_gen.minimax_image import MinimaxImageGenProvider

        with (
            patch(
                "plugins.image_gen.minimax_image.requests.post",
                return_value=_response({"data": {"image_urls": ["https://minimax/result.png"]}}),
            ) as mock_post,
            patch(
                "plugins.image_gen.minimax_image.save_url_image",
                return_value=Path("/tmp/minimax-result.png"),
            ),
        ):
            result = MinimaxImageGenProvider().generate("A reliable grid control room")

        assert result["success"] is True
        assert result["image"] == "/tmp/minimax-result.png"
        assert result["provider"] == "minimax-image"
        assert mock_post.call_args.args[0] == "https://api.minimax.io/v1/image_generation"
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer minimax-secret"
        assert mock_post.call_args.kwargs["json"]["model"] == "image-01"
        assert mock_post.call_args.kwargs["json"]["aspect_ratio"] == "16:9"

    def test_surface_auth_prompt_and_register(self, monkeypatch):
        from plugins.image_gen.minimax_image import MinimaxImageGenProvider, register

        provider = MinimaxImageGenProvider()
        schema = provider.get_setup_schema()
        assert provider.name == "minimax-image"
        assert schema["domestic"] is True
        assert schema["credential_fields"][0]["env_var"] == "MINIMAX_API_KEY"
        assert provider.generate("text")["error_type"] == "auth_required"
        monkeypatch.setenv("MINIMAX_API_KEY", "minimax-secret")
        assert provider.generate(" ")["error_type"] == "invalid_argument"
        ctx = MagicMock()
        register(ctx)
        (registered,), _ = ctx.register_image_gen_provider.call_args
        assert isinstance(registered, MinimaxImageGenProvider)

    def test_http_error_redacts_secret(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "minimax-secret")
        from plugins.image_gen.minimax_image import MinimaxImageGenProvider

        with patch(
            "plugins.image_gen.minimax_image.requests.post",
            return_value=_http_error_response("minimax-secret"),
        ):
            result = MinimaxImageGenProvider().generate("A city skyline")

        assert result["success"] is False
        assert "minimax-secret" not in result["error"]
