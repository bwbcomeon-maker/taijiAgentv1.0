"""Tests for WebUI model configuration parity with Hermes CLI config."""

from __future__ import annotations

import json
import os

import yaml

import api.providers as providers
import api.profiles as profiles
from api import model_config


def _use_home(monkeypatch, tmp_path, *, stub_image_gen: bool = True):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(providers, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        model_config,
        "get_providers",
        lambda: {
            "providers": [
                {
                    "id": "deepseek",
                    "display_name": "DeepSeek",
                    "models": [{"id": "deepseek-chat", "label": "deepseek-chat"}],
                    "configurable": True,
                    "has_key": False,
                }
            ],
            "active_provider": "deepseek",
        },
    )
    if stub_image_gen:
        monkeypatch.setattr(
            model_config,
            "get_image_gen_config",
            lambda: {
                "image_gen": {},
                "providers": [],
                "config": {"label": "本机配置", "exists": True},
            },
        )


def _read_config(tmp_path):
    return yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8")) or {}


def test_main_model_config_writes_deepseek_key_without_echo(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    result = model_config.set_main_model_config(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_key": "sk-deepseek-test-key-123456",
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["model"]["provider"] == "deepseek"
    assert cfg["model"]["default"] == "deepseek-chat"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=sk-deepseek-test-key-123456" in env_text
    assert "sk-deepseek-test-key-123456" not in json.dumps(result)
    os.environ.pop("DEEPSEEK_API_KEY", None)


def test_custom_main_model_uses_key_env_not_inline_secret(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("HERMES_CUSTOM_MODEL_API_KEY", raising=False)

    result = model_config.set_main_model_config(
        {
            "provider": "custom",
            "model": "my-image-aware-model",
            "base_url": "https://custom.example.com/v1/",
            "api_key": "custom-secret-key-123456",
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["model"]["provider"] == "custom"
    assert cfg["model"]["default"] == "my-image-aware-model"
    assert cfg["model"]["base_url"] == "https://custom.example.com/v1"
    assert cfg["model"]["key_env"] == "HERMES_CUSTOM_MODEL_API_KEY"
    assert "api_key" not in cfg["model"]
    assert "HERMES_CUSTOM_MODEL_API_KEY=custom-secret-key-123456" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "custom-secret-key-123456" not in json.dumps(result)
    os.environ.pop("HERMES_CUSTOM_MODEL_API_KEY", None)


def test_oauth_main_provider_rejected_from_webui(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    try:
        model_config.set_main_model_config(
            {"provider": "openai-codex", "model": "gpt-5.1-codex"}
        )
    except ValueError as exc:
        assert "网页登录授权" in str(exc)
        assert "太极智能体" in str(exc)
        assert "hermes" not in str(exc)
        assert "Hermes" not in str(exc)
    else:
        raise AssertionError("OAuth provider accepted WebUI API-key setup")


def test_image_gen_config_rejects_non_domestic_provider(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [
            {
                "id": "fal",
                "name": "FAL",
                "models": [{"id": "fal-ai/flux-2-pro", "label": "Flux 2 Pro"}],
                "default_model": "fal-ai/flux-2-pro",
                "key_status": {"configured": False, "env_var": "FAL_KEY"},
                "domestic": False,
                "integration_status": "blocked",
                "policy_blocked": True,
            }
        ],
    )

    try:
        model_config.set_image_gen_config(
            {
                "provider": "fal",
                "model": "fal-ai/flux-2-pro",
                "api_key": "fal-test-key-123456",
            }
        )
    except ValueError as exc:
        assert "国产" in str(exc)
    else:
        raise AssertionError("non-domestic image generation provider was accepted")
    assert not (tmp_path / ".env").exists()


def test_image_gen_config_writes_multi_field_domestic_credentials_without_echo(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    for key in ("DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID", "DASHSCOPE_REGION"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [
            {
                "id": "dashscope",
                "name": "通义 Qwen-Image",
                "models": [{"id": "qwen-image-2.0-pro", "label": "Qwen Image 2 Pro"}],
                "default_model": "qwen-image-2.0-pro",
                "key_status": {"configured": False, "env_var": "DASHSCOPE_API_KEY"},
                "credential_fields": [
                    {
                        "name": "api_key",
                        "env_var": "DASHSCOPE_API_KEY",
                        "label": "API Key",
                        "required": True,
                        "secret": True,
                    },
                    {
                        "name": "workspace_id",
                        "env_var": "DASHSCOPE_WORKSPACE_ID",
                        "label": "Workspace ID",
                        "required": True,
                        "secret": False,
                    },
                    {
                        "name": "region",
                        "env_var": "DASHSCOPE_REGION",
                        "label": "Region",
                        "required": False,
                        "secret": False,
                    },
                ],
                "credential_status": {
                    "configured": False,
                    "missing": ["DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID"],
                },
                "domestic": True,
                "integration_status": "stable",
            }
        ],
    )

    result = model_config.set_image_gen_config(
        {
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "credentials": {
                "api_key": "dashscope-test-key-123456",
                "workspace_id": "ws-cn-test",
                "region": "cn-beijing",
            },
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["image_gen"]["provider"] == "dashscope"
    assert cfg["image_gen"]["model"] == "qwen-image-2.0-pro"
    assert cfg["image_gen"]["use_gateway"] is False
    assert cfg["image_gen"]["options"]["workspace_id"] == "ws-cn-test"
    assert cfg["image_gen"]["options"]["region"] == "cn-beijing"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=dashscope-test-key-123456" in env_text
    assert "DASHSCOPE_WORKSPACE_ID" not in env_text
    assert "DASHSCOPE_REGION" not in env_text
    dumped = json.dumps(result, ensure_ascii=False)
    assert "dashscope-test-key-123456" not in dumped
    assert "ws-cn-test" not in dumped
    for key in ("DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID", "DASHSCOPE_REGION"):
        os.environ.pop(key, None)


def test_image_gen_config_writes_doubao_ark_key_without_echo(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [
            {
                "id": "doubao",
                "name": "Doubao Seedream",
                "models": [
                    {
                        "id": "doubao-seedream-5-0-260128",
                        "label": "Doubao Seedream 5.0 Lite",
                    }
                ],
                "default_model": "doubao-seedream-5-0-260128",
                "key_status": {"configured": False, "env_var": "ARK_API_KEY"},
            }
        ],
    )

    result = model_config.set_image_gen_config(
        {
            "provider": "doubao",
            "model": "doubao-seedream-5-0-260128",
            "api_key": "ark-test-key-123456",
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["image_gen"]["provider"] == "doubao"
    assert cfg["image_gen"]["model"] == "doubao-seedream-5-0-260128"
    assert cfg["image_gen"]["use_gateway"] is False
    assert "ARK_API_KEY=ark-test-key-123456" in (tmp_path / ".env").read_text(
        encoding="utf-8"
    )
    assert "ark-test-key-123456" not in json.dumps(result)
    os.environ.pop("ARK_API_KEY", None)


def test_vision_config_writes_auxiliary_vision_and_key_without_echo(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    result = model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "api_key": "dashscope-test-key-123456",
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["auxiliary"]["vision"]["provider"] == "alibaba"
    assert cfg["auxiliary"]["vision"]["model"] == "qwen3-vl-plus"
    assert "api_key" not in cfg["auxiliary"]["vision"]
    assert "DASHSCOPE_API_KEY=dashscope-test-key-123456" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "dashscope-test-key-123456" not in json.dumps(result)
    assert result["vision"]["key_status"]["env_var"] == "DASHSCOPE_API_KEY"
    os.environ.pop("DASHSCOPE_API_KEY", None)


def test_model_config_includes_image_understanding_config(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "auxiliary": {
                    "vision": {
                        "provider": "zai",
                        "model": "glm-5v-turbo",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = model_config.get_model_config()

    assert result["vision"]["provider"] == "zai"
    assert result["vision"]["model"] == "glm-5v-turbo"
    assert any(row["id"] == "alibaba" for row in result["vision_providers"])
    assert any(row["id"] == "zai" for row in result["vision_providers"])


def test_custom_vision_config_without_key_does_not_write_placeholder(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("AUXILIARY_VISION_API_KEY", raising=False)

    model_config.set_vision_config(
        {
            "provider": "custom",
            "model": "qwen-vl-private",
            "base_url": "http://127.0.0.1:8000/v1",
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["auxiliary"]["vision"]["provider"] == "custom"
    assert cfg["auxiliary"]["vision"]["base_url"] == "http://127.0.0.1:8000/v1"
    assert "api_key" not in cfg["auxiliary"]["vision"]


def test_custom_image_provider_config_writes_secret_to_env_and_redacts(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.delenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", raising=False)
    monkeypatch.setattr(model_config, "_image_gen_provider_rows", lambda active: [])

    result = model_config.set_custom_image_provider_config(
        {
            "id": "router",
            "name": "Router Images",
            "base_url": "https://images.example.com/v1/",
            "models": ["gpt-image-custom"],
            "default_model": "gpt-image-custom",
            "api_key": "router-secret-key-123456",
            "timeout_seconds": 45,
        }
    )

    cfg = _read_config(tmp_path)
    providers = cfg["custom_image_providers"]
    assert providers == [
        {
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
            "response_format": "auto",
            "timeout_seconds": 45,
        }
    ]
    assert "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY=router-secret-key-123456" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    dumped = json.dumps(result, ensure_ascii=False)
    assert "router-secret-key-123456" not in dumped
    assert result["provider"]["id"] == "custom:router"
    assert result["provider"]["key_status"]["env_var"] == "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY"
    assert result["provider"]["base_url"] == "https://images.example.com/v1"
    assert result["provider"]["size_map"]["square"] == "1024x1024"
    os.environ.pop("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", None)


def test_custom_image_provider_appears_in_image_gen_config(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", "router-sensitive-value")
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": True,
            "reason_code": "ready",
            "public_message": "图像生成已就绪。",
        },
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {"provider": "custom:router", "model": "gpt-image-custom"},
                "custom_image_providers": [
                    {
                        "id": "router",
                        "name": "Router Images",
                        "base_url": "https://images.example.com/v1",
                        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
                        "models": ["gpt-image-custom"],
                        "default_model": "gpt-image-custom",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = model_config.get_image_gen_config()
    row = next(item for item in result["providers"] if item["id"] == "custom:router")

    assert result["image_gen"]["provider"] == "custom:router"
    assert row["name"] == "Router Images"
    assert row["active"] is True
    assert row["available"] is True
    assert row["oauth_managed"] is False
    assert row["custom"] is True
    assert row["key_status"]["configured"] is True
    assert "router-sensitive-value" not in json.dumps(result, ensure_ascii=False)
    os.environ.pop("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", None)


def test_custom_image_provider_reads_key_status_from_env_file(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.delenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": True,
            "reason_code": "ready",
            "public_message": "图像生成已就绪。",
        },
    )
    (tmp_path / ".env").write_text("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY=secret-from-file\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {"provider": "custom:router", "model": "gpt-image-custom"},
                "custom_image_providers": [
                    {
                        "id": "router",
                        "name": "Router Images",
                        "base_url": "https://images.example.com/v1",
                        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
                        "models": ["gpt-image-custom"],
                        "default_model": "gpt-image-custom",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = model_config.get_custom_image_provider_configs()
    row = result["providers"][0]

    assert row["available"] is True
    assert row["key_status"]["configured"] is True
    assert row["key_status"]["source"] == "env_file"
    assert "secret-from-file" not in json.dumps(result, ensure_ascii=False)


def test_custom_image_provider_delete_rejects_active_provider(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {"provider": "custom:router", "model": "gpt-image-custom"},
                "custom_image_providers": [
                    {
                        "id": "router",
                        "name": "Router Images",
                        "base_url": "https://images.example.com/v1",
                        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
                        "models": ["gpt-image-custom"],
                        "default_model": "gpt-image-custom",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    try:
        model_config.delete_custom_image_provider_config("router")
    except ValueError as exc:
        assert "正在使用" in str(exc)
    else:
        raise AssertionError("active custom image provider was deleted")


def test_custom_image_provider_delete_removes_inactive_provider(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {"provider": "fal", "model": "fal-ai/flux-2-pro"},
                "custom_image_providers": [
                    {
                        "id": "router",
                        "name": "Router Images",
                        "base_url": "https://images.example.com/v1",
                        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
                        "models": ["gpt-image-custom"],
                        "default_model": "gpt-image-custom",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = model_config.delete_custom_image_provider_config("router")

    assert result["ok"] is True
    assert _read_config(tmp_path)["custom_image_providers"] == []


def test_model_config_payload_hides_raw_config_path(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n  provider: deepseek\n  default: deepseek-chat\n",
        encoding="utf-8",
    )

    result = model_config.get_model_config()

    dumped = json.dumps(result, ensure_ascii=False)
    assert "config_path" not in result
    assert str(config_path) not in dumped
    assert "config.yaml" not in dumped
    assert result["config"]["label"] == "本机配置"
    assert result["config"]["exists"] is True


def test_image_gen_config_payload_hides_raw_config_path(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("image_gen:\n  provider: fal\n", encoding="utf-8")
    monkeypatch.setattr(model_config, "_image_gen_provider_rows", lambda active: [])

    result = model_config.get_image_gen_config()

    dumped = json.dumps(result, ensure_ascii=False)
    assert "config_path" not in result
    assert str(config_path) not in dumped
    assert "config.yaml" not in dumped
    assert result["config"]["label"] == "本机配置"
    assert result["config"]["exists"] is True


def test_image_gen_config_rejects_taiji_public_provider_from_domestic_policy(monkeypatch, tmp_path):
    real_get_image_gen_config = model_config.get_image_gen_config
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(model_config, "get_image_gen_config", real_get_image_gen_config)

    class _Provider:
        name = "openai-codex"
        display_name = "OpenAI 图像生成"

        def get_setup_schema(self):
            return {
                "name": "OpenAI 图像生成",
                "badge": "授权",
                "tag": "通过太极智能体授权使用图像生成",
                "env_vars": [],
            }

        def list_models(self):
            return [{"id": "gpt-image-2-medium", "display": "GPT Image 2"}]

        def default_model(self):
            return "gpt-image-2-medium"

        def is_available(self):
            return False

    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr(
        "agent.image_gen_registry.list_providers",
        lambda: [_Provider()],
    )
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": False,
            "reason_code": "authorization_required",
            "public_message": "图像生成未授权，请先在太极智能体中完成图像生成授权。",
        },
    )

    try:
        model_config.set_image_gen_config(
            {
                "provider": "taiji-image",
                "model": "gpt-image-2-medium",
            }
        )
    except ValueError as exc:
        assert "国产" in str(exc)
    else:
        raise AssertionError("taiji-image was accepted in domestic-only image config")


def test_image_gen_provider_rows_include_doubao(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("ARK_API_KEY", raising=False)

    rows = model_config._image_gen_provider_rows("doubao")
    doubao = next(row for row in rows if row["id"] == "doubao")

    assert doubao["name"] == "Doubao Seedream"
    assert doubao["active"] is True
    assert doubao["key_status"]["env_var"] == "ARK_API_KEY"
    assert doubao["key_status"]["configured"] is False
    model_ids = {item["id"] for item in doubao["models"]}
    assert "doubao-seedream-5-0-260128" in model_ids
    assert "doubao-seedream-5-0-lite-260128" in model_ids


def test_image_gen_provider_rows_are_domestic_stable_only(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    class _Provider:
        def __init__(self, name, domestic=True, status="stable"):
            self.name = name
            self.display_name = name
            self._domestic = domestic
            self._status = status

        def get_setup_schema(self):
            return {
                "name": self.display_name,
                "tag": f"{self.name} provider",
                "env_vars": [{"key": f"{self.name.upper().replace('-', '_')}_KEY"}],
                "domestic": self._domestic,
                "integration_status": self._status,
            }

        def list_models(self):
            return [{"id": f"{self.name}-model", "display": f"{self.name} model"}]

        def default_model(self):
            return f"{self.name}-model"

        def is_available(self):
            return False

    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr(
        "agent.image_gen_registry.list_providers",
        lambda: [
            _Provider("doubao"),
            _Provider("dashscope"),
            _Provider("qianfan"),
            _Provider("zhipu-image"),
            _Provider("minimax-image"),
            _Provider("fal", domestic=False, status="external"),
            _Provider("openai", domestic=False, status="external"),
            _Provider("kling", domestic=True, status="candidate"),
        ],
    )

    rows = model_config._image_gen_provider_rows("")
    ids = {row["id"] for row in rows}

    assert {"doubao", "dashscope", "qianfan", "zhipu-image", "minimax-image"} <= ids
    assert "fal" not in ids
    assert "openai" not in ids
    assert "kling" not in ids
    assert all(row.get("domestic") or row.get("custom") for row in rows)
    assert all(row.get("integration_status") in {"stable", "custom"} for row in rows)


def test_image_gen_provider_rows_expose_credential_status(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_WORKSPACE_ID", raising=False)
    monkeypatch.setenv("DASHSCOPE_REGION", "cn-beijing")

    class _Provider:
        name = "dashscope"
        display_name = "通义 Qwen-Image"

        def get_setup_schema(self):
            return {
                "name": self.display_name,
                "env_vars": [{"key": "DASHSCOPE_API_KEY"}],
                "credential_fields": [
                    {
                        "name": "api_key",
                        "env_var": "DASHSCOPE_API_KEY",
                        "label": "API Key",
                        "required": True,
                        "secret": True,
                    },
                    {
                        "name": "workspace_id",
                        "env_var": "DASHSCOPE_WORKSPACE_ID",
                        "label": "Workspace ID",
                        "required": True,
                        "secret": False,
                    },
                    {
                        "name": "region",
                        "env_var": "DASHSCOPE_REGION",
                        "label": "Region",
                        "required": False,
                        "secret": False,
                    },
                ],
                "domestic": True,
                "integration_status": "stable",
            }

        def list_models(self):
            return [{"id": "qwen-image-2.0-pro", "display": "Qwen Image 2 Pro"}]

        def default_model(self):
            return "qwen-image-2.0-pro"

        def is_available(self):
            return False

    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr("agent.image_gen_registry.list_providers", lambda: [_Provider()])

    rows = model_config._image_gen_provider_rows("dashscope")
    row = next(item for item in rows if item["id"] == "dashscope")

    assert [field["env_var"] for field in row["credential_fields"]] == [
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_WORKSPACE_ID",
        "DASHSCOPE_REGION",
    ]
    assert row["credential_status"]["configured"] is False
    assert set(row["credential_status"]["missing"]) == {
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_WORKSPACE_ID",
    }
    assert row["key_status"]["env_var"] == "DASHSCOPE_API_KEY"
    assert row["domestic"] is True
    assert row["integration_status"] == "stable"
    os.environ.pop("DASHSCOPE_REGION", None)


def test_openai_codex_image_provider_reflects_real_readiness(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    class _Provider:
        name = "openai-codex"
        display_name = "OpenAI 图像生成"

        def get_setup_schema(self):
            return {
                "name": "OpenAI 图像生成",
                "badge": "授权",
                "tag": "通过太极智能体授权使用图像生成",
                "env_vars": [],
            }

        def list_models(self):
            return [{"id": "gpt-image-2-medium", "display": "GPT Image 2"}]

        def default_model(self):
            return "gpt-image-2-medium"

        def is_available(self):
            return False

    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr(
        "agent.image_gen_registry.list_providers",
        lambda: [_Provider()],
    )
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": False,
            "reason_code": "authorization_required",
            "public_message": "图像生成未授权，请先在太极智能体中完成图像生成授权。",
        },
    )

    rows = model_config._image_gen_provider_rows("openai-codex")
    row = next(item for item in rows if item["id"] == "taiji-image")

    assert row["active"] is True
    assert row["available"] is False
    assert row["policy_blocked"] is True
    assert row["domestic"] is False
    assert row["integration_status"] == "blocked"
    assert row["key_status"]["configured"] is False
    assert row["key_status"]["source"] == "policy_blocked"
    assert row["reason_code"] == "authorization_required"
    assert row["status_message"] == "图像生成未授权，请先在太极智能体中完成图像生成授权。"
    visible = json.dumps(row, ensure_ascii=False)
    assert "Hermes" not in visible
    assert "Codex" not in visible
    assert "openai-codex" not in visible
    assert ("her" "mes tools") not in visible
