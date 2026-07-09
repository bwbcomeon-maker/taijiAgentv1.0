#!/usr/bin/env python3
"""Tests for built-in domestic image generation providers."""

from __future__ import annotations

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


class TestDashScopeQwenImageProvider:
    def test_surface_and_setup_schema(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        schema = provider.get_setup_schema()

        assert provider.name == "dashscope"
        assert provider.default_model() == "qwen-image-2.0-pro"
        assert schema["domestic"] is True
        assert schema["integration_status"] == "stable"
        assert [field["env_var"] for field in schema["credential_fields"]] == [
            "DASHSCOPE_API_KEY",
            "DASHSCOPE_WORKSPACE_ID",
            "DASHSCOPE_REGION",
        ]

    def test_missing_credentials_return_auth_required(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        result = DashScopeQwenImageProvider().generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "auth_required"
        assert "DASHSCOPE_API_KEY" in result["error"]
        assert "DASHSCOPE_WORKSPACE_ID" in result["error"]

    def test_empty_prompt_returns_invalid_argument(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "ws-cn-test")
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        result = DashScopeQwenImageProvider().generate("   ")

        assert result["success"] is False
        assert result["error_type"] == "invalid_argument"

    def test_successful_generation_posts_to_workspace_endpoint(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "ws-cn-test")
        monkeypatch.setenv("DASHSCOPE_REGION", "cn-shanghai")
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
                "plugins.image_gen.dashscope.save_url_image",
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
            == "https://ws-cn-test.cn-shanghai.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        )
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer dashscope-secret"
        assert mock_post.call_args.kwargs["json"]["parameters"]["size"] == "1664*928"

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
