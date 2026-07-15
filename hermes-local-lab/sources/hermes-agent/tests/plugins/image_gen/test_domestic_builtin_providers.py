#!/usr/bin/env python3
"""Tests for built-in domestic image generation providers."""

from __future__ import annotations

import base64
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in (
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_WORKSPACE_ID",
        "DASHSCOPE_REGION",
        "DASHSCOPE_ENDPOINT_MODE",
        "DASHSCOPE_BASE_URL",
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
        assert fields["endpoint_mode"]["placeholder"] == "workspace"
        assert fields["workspace_id"]["placeholder"] == "llm-demo"

    def test_missing_credentials_return_auth_required(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        result = DashScopeQwenImageProvider().generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "auth_required"
        assert "DASHSCOPE_API_KEY" in result["error"]
        assert "DASHSCOPE_WORKSPACE_ID" in result["error"]

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
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))
            ],
        )
        from plugins.image_gen.dashscope import _save_safe_image_url

        with (
            patch("plugins.image_gen.dashscope.requests.get") as mock_get,
            pytest.raises(ValueError, match="safety"),
        ):
            _save_safe_image_url("https://private.example/image.png")
        mock_get.assert_not_called()

    def test_safe_image_download_rejects_public_redirect_to_private(self, monkeypatch):
        def resolve(hostname, *args, **kwargs):
            ip = "8.8.8.8" if hostname == "public.example" else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

        monkeypatch.setattr(socket, "getaddrinfo", resolve)
        redirect = _download_response(
            status=302, headers={"Location": "https://private.example/image.png"}
        )
        from plugins.image_gen.dashscope import _save_safe_image_url

        with (
            patch("plugins.image_gen.dashscope.requests.get", return_value=redirect) as mock_get,
            pytest.raises(ValueError, match="safety"),
        ):
            _save_safe_image_url("https://public.example/image.png")
        assert mock_get.call_count == 1
        assert mock_get.call_args.kwargs["allow_redirects"] is False

    def test_safe_image_download_follows_public_redirect_and_saves_image(self, monkeypatch):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))
            ],
        )
        redirect = _download_response(
            status=302, headers={"Location": "https://cdn.example/image.png"}
        )
        image = _download_response(
            headers={"Content-Type": "image/png", "Content-Length": "4"},
            chunks=(b"safe",),
        )
        from plugins.image_gen.dashscope import _save_safe_image_url

        with (
            patch(
                "plugins.image_gen.dashscope.requests.get",
                side_effect=[redirect, image],
            ) as mock_get,
            patch(
                "plugins.image_gen.dashscope.save_b64_image",
                return_value=Path("/tmp/safe.png"),
            ) as mock_save,
        ):
            saved = _save_safe_image_url("https://public.example/image.png")

        assert saved == Path("/tmp/safe.png")
        assert mock_get.call_count == 2
        assert all(call.kwargs["allow_redirects"] is False for call in mock_get.call_args_list)
        assert base64.b64decode(mock_save.call_args.args[0]) == b"safe"
        assert mock_save.call_args.kwargs["extension"] == "png"

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
            ({"DASHSCOPE_API_KEY": "key"}, False),
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
