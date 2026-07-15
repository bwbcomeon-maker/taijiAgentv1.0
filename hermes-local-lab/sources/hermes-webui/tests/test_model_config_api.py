"""Tests for WebUI model configuration parity with Hermes CLI config."""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
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


def test_provider_credential_secret_stays_out_of_yaml_and_public_response(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", raising=False)
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=legacy-key\n", encoding="utf-8")

    result = model_config.upsert_provider_credential(
        {
            "id": "Alibaba Default",
            "provider": "alibaba",
            "label": "阿里云百炼默认凭据",
            "api_key": "named-secret-key",
        }
    )

    row = _read_config(tmp_path)["provider_credentials"][0]
    assert row == {
        "id": "alibaba-default",
        "provider_family": "alibaba_dashscope",
        "label": "阿里云百炼默认凭据",
        "auth_type": "api_key",
        "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
    }
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=legacy-key" in env_text
    assert "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=named-secret-key" in env_text
    assert set(result["credential"]) == {
        "id", "provider_family", "label", "auth_type", "configured", "used_by"
    }
    public_dump = json.dumps(result, ensure_ascii=False)
    assert "named-secret-key" not in public_dump
    assert "secret_env" not in public_dump
    assert "digest" not in public_dump


def test_provider_credentials_report_vision_and_image_gen_usage(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": "alibaba-default",
                        "provider_family": "alibaba_dashscope",
                        "label": "Alibaba default",
                        "auth_type": "api_key",
                        "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
                    }
                ],
                "auxiliary": {"vision": {"credential_ref": "alibaba-default"}},
                "image_gen": {"credential_ref": "alibaba-default"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=named-key\n", encoding="utf-8"
    )

    result = model_config.get_provider_credentials_config()

    assert result["credentials"] == [
        {
            "id": "alibaba-default",
            "provider_family": "alibaba_dashscope",
            "label": "Alibaba default",
            "auth_type": "api_key",
            "configured": True,
            "used_by": ["auxiliary.vision", "image_gen"],
        }
    ]


def test_provider_credential_in_use_cannot_be_deleted(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": "alibaba-default",
                        "provider_family": "alibaba_dashscope",
                        "label": "Alibaba default",
                        "auth_type": "api_key",
                        "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
                    }
                ],
                "image_gen": {"credential_ref": "alibaba-default"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="正在使用"):
        model_config.delete_provider_credential("alibaba-default")


def test_unknown_provider_credential_delete_fails_safely(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="不存在"):
        model_config.delete_provider_credential("missing")


@pytest.mark.parametrize("replacement_key", [None, "zhipu-secret"])
def test_existing_credential_id_cannot_change_provider_family(
    monkeypatch, tmp_path, replacement_key
):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "label": "Alibaba", "api_key": "alibaba-secret"}
    )
    body = {"id": "shared", "provider": "zhipu", "label": "Zhipu"}
    if replacement_key is not None:
        body["api_key"] = replacement_key

    with pytest.raises(ValueError, match="Provider"):
        model_config.upsert_provider_credential(body)

    assert _read_config(tmp_path)["provider_credentials"] == [
        {
            "id": "shared",
            "provider_family": "alibaba_dashscope",
            "label": "Alibaba",
            "auth_type": "api_key",
            "secret_env": "TAIJI_CREDENTIAL_SHARED_API_KEY",
        }
    ]
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TAIJI_CREDENTIAL_SHARED_API_KEY=alibaba-secret" in env_text
    assert "zhipu-secret" not in env_text


def test_blank_credential_label_falls_back_to_id(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    result = model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "label": "   ", "api_key": "secret"}
    )

    assert result["credential"]["label"] == "shared"
    assert _read_config(tmp_path)["provider_credentials"][0]["label"] == "shared"


def test_delete_rejects_tampered_secret_env_without_touching_unrelated_env(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": "shared",
                        "provider_family": "alibaba_dashscope",
                        "label": "Shared",
                        "auth_type": "api_key",
                        "secret_env": "UNRELATED_API_KEY",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("UNRELATED_API_KEY=keep-me\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Secret 环境变量"):
        model_config.delete_provider_credential("shared")

    assert _read_config(tmp_path)["provider_credentials"][0]["id"] == "shared"
    assert "UNRELATED_API_KEY=keep-me" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_concurrent_cross_family_upserts_cannot_mismatch_metadata_and_secret(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    barrier = threading.Barrier(2)

    def save(provider, label, secret):
        barrier.wait(timeout=5)
        try:
            model_config.upsert_provider_credential(
                {"id": "shared", "provider": provider, "label": label, "api_key": secret}
            )
            return "saved"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: save(*args),
                [("alibaba", "Alibaba", "alibaba-secret"), ("zhipu", "Zhipu", "zhipu-secret")],
            )
        )

    assert sorted(results) == ["rejected", "saved"]
    row = _read_config(tmp_path)["provider_credentials"][0]
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    if row["provider_family"] == "alibaba_dashscope":
        assert row["label"] == "Alibaba"
        assert "TAIJI_CREDENTIAL_SHARED_API_KEY=alibaba-secret" in env_text
        assert "zhipu-secret" not in env_text
    else:
        assert row["provider_family"] == "zhipu"
        assert row["label"] == "Zhipu"
        assert "TAIJI_CREDENTIAL_SHARED_API_KEY=zhipu-secret" in env_text
        assert "alibaba-secret" not in env_text


def test_interleaved_upsert_delete_leaves_complete_or_absent_credential(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "api_key": "initial-secret"}
    )
    secret_written = threading.Event()
    original_write = model_config._write_env_file

    def delayed_write(env_path, updates):
        result = original_write(env_path, updates)
        if updates == {"TAIJI_CREDENTIAL_SHARED_API_KEY": "updated-secret"}:
            secret_written.set()
            time.sleep(0.1)
        return result

    monkeypatch.setattr(model_config, "_write_env_file", delayed_write)

    def upsert():
        model_config.upsert_provider_credential(
            {"id": "shared", "provider": "alibaba", "api_key": "updated-secret"}
        )

    def delete():
        assert secret_written.wait(timeout=5)
        model_config.delete_provider_credential("shared")

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), [upsert, delete]))

    rows = _read_config(tmp_path).get("provider_credentials", [])
    env_text = (tmp_path / ".env").read_text(encoding="utf-8") if (tmp_path / ".env").exists() else ""
    if rows:
        assert rows[0]["secret_env"] == "TAIJI_CREDENTIAL_SHARED_API_KEY"
        assert "TAIJI_CREDENTIAL_SHARED_API_KEY=updated-secret" in env_text
    else:
        assert "TAIJI_CREDENTIAL_SHARED_API_KEY" not in env_text


def test_upsert_restores_previous_secret_when_yaml_save_fails(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "label": "Before", "api_key": "before-secret"}
    )
    original_save = model_config._save_yaml_config_file
    failed = False

    def fail_after_save(*args, **kwargs):
        nonlocal failed
        result = original_save(*args, **kwargs)
        if not failed:
            failed = True
            raise OSError("simulated yaml failure")
        return result

    monkeypatch.setattr(model_config, "_save_yaml_config_file", fail_after_save)
    with pytest.raises(OSError, match="simulated"):
        model_config.upsert_provider_credential(
            {"id": "shared", "provider": "alibaba", "label": "After", "api_key": "after-secret"}
        )
    monkeypatch.setattr(model_config, "_save_yaml_config_file", original_save)

    assert _read_config(tmp_path)["provider_credentials"][0]["label"] == "Before"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TAIJI_CREDENTIAL_SHARED_API_KEY=before-secret" in env_text
    assert "after-secret" not in env_text


def test_delete_restores_metadata_when_secret_delete_fails(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "label": "Shared", "api_key": "shared-secret"}
    )
    original_write = model_config._write_env_file
    failed = False

    def fail_after_delete(env_path, updates):
        nonlocal failed
        result = original_write(env_path, updates)
        if not failed and updates == {"TAIJI_CREDENTIAL_SHARED_API_KEY": None}:
            failed = True
            raise OSError("simulated env failure")
        return result

    monkeypatch.setattr(model_config, "_write_env_file", fail_after_delete)
    with pytest.raises(OSError, match="simulated"):
        model_config.delete_provider_credential("shared")

    assert _read_config(tmp_path)["provider_credentials"][0]["label"] == "Shared"
    assert "TAIJI_CREDENTIAL_SHARED_API_KEY=shared-secret" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")


def test_legacy_dashscope_api_key_payload_remains_compatible(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=old-key\n", encoding="utf-8")

    model_config.set_vision_config(
        {"provider": "alibaba", "model": "qwen3-vl-plus", "api_key": "new-legacy-key"}
    )

    assert "DASHSCOPE_API_KEY=new-legacy-key" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "api_key" not in _read_config(tmp_path)["auxiliary"]["vision"]


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


def _dashscope_image_provider_row():
    return {
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
        ],
        "domestic": True,
        "integration_status": "stable",
    }


def test_dashscope_image_can_share_named_credential_with_vision(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [_dashscope_image_provider_row()],
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=legacy-must-stay\n", encoding="utf-8"
    )
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "shared-named-secret",
        }
    )
    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "credential_ref": "alibaba-default",
        }
    )

    model_config.set_image_gen_config(
        {
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "credential_ref": "alibaba-default",
            "credentials": {"workspace_id": "llm-demo"},
        }
    )

    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"]["credential_ref"] == "alibaba-default"
    assert saved["image_gen"]["credential_ref"] == "alibaba-default"
    assert saved["image_gen"]["options"]["workspace_id"] == "llm-demo"
    assert "api_key" not in saved["image_gen"]
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=legacy-must-stay" in env_text
    assert "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=shared-named-secret" in env_text


def test_dashscope_independent_credential_rotation_does_not_change_shared_or_legacy_key(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [_dashscope_image_provider_row()],
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=legacy-must-stay\n", encoding="utf-8"
    )
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "api_key": "shared-secret",
        }
    )
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-image",
            "provider": "dashscope",
            "api_key": "image-secret-before",
        }
    )
    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "credential_ref": "alibaba-default",
        }
    )
    model_config.set_image_gen_config(
        {
            "provider": "dashscope",
            "credential_ref": "alibaba-image",
            "credentials": {"workspace_id": "llm-demo"},
        }
    )

    model_config.upsert_provider_credential(
        {
            "id": "alibaba-image",
            "provider": "dashscope",
            "api_key": "image-secret-after",
        }
    )

    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"]["credential_ref"] == "alibaba-default"
    assert saved["image_gen"]["credential_ref"] == "alibaba-image"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=legacy-must-stay" in env_text
    assert "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=shared-secret" in env_text
    assert "TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY=image-secret-after" in env_text
    assert "image-secret-before" not in env_text


def test_dashscope_image_rejects_named_ref_with_inline_api_key(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [_dashscope_image_provider_row()],
    )
    model_config.upsert_provider_credential(
        {"id": "alibaba-default", "provider": "alibaba", "api_key": "named-secret"}
    )

    with pytest.raises(ValueError, match="credential_ref.*api_key"):
        model_config.set_image_gen_config(
            {
                "provider": "dashscope",
                "credential_ref": "alibaba-default",
                "api_key": "must-not-write",
            }
        )

    assert "must-not-write" not in (tmp_path / ".env").read_text(encoding="utf-8")


def test_legacy_dashscope_inline_key_rolls_back_when_yaml_save_fails(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [_dashscope_image_provider_row()],
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=old-legacy-secret\n", encoding="utf-8"
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "old-legacy-secret")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {"image_gen": {"provider": "dashscope", "model": "qwen-image"}}
        ),
        encoding="utf-8",
    )

    def fail_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(model_config, "_save_yaml_config_file", fail_save)

    with pytest.raises(OSError, match="disk full"):
        model_config.set_image_gen_config(
            {
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "api_key": "new-secret-must-roll-back",
            }
        )

    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "DASHSCOPE_API_KEY=old-legacy-secret\n"
    )
    assert os.environ["DASHSCOPE_API_KEY"] == "old-legacy-secret"
    assert _read_config(tmp_path)["image_gen"] == {
        "provider": "dashscope",
        "model": "qwen-image",
    }


def test_concurrent_image_binding_and_credential_delete_cannot_leave_dangling_ref(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [_dashscope_image_provider_row()],
    )
    model_config.upsert_provider_credential(
        {"id": "alibaba-image", "provider": "dashscope", "api_key": "named-secret"}
    )
    validated = threading.Event()
    continue_binding = threading.Event()
    original_load_credential = model_config.load_credential

    def pause_after_validation(*args, **kwargs):
        row = original_load_credential(*args, **kwargs)
        validated.set()
        assert continue_binding.wait(timeout=2)
        return row

    monkeypatch.setattr(model_config, "load_credential", pause_after_validation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        bind_future = pool.submit(
            model_config.set_image_gen_config,
            {"provider": "dashscope", "credential_ref": "alibaba-image"},
        )
        assert validated.wait(timeout=2)
        delete_future = pool.submit(
            model_config.delete_provider_credential, "alibaba-image"
        )
        time.sleep(0.1)
        continue_binding.set()
        bind_future.result(timeout=2)
        with pytest.raises(ValueError, match="正在使用"):
            delete_future.result(timeout=2)

    saved = _read_config(tmp_path)
    assert saved["image_gen"]["credential_ref"] == "alibaba-image"
    assert saved["provider_credentials"][0]["id"] == "alibaba-image"


def test_shared_credential_rotation_invalidates_every_referencing_capability(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "alibaba-default", "provider": "alibaba", "api_key": "before"}
    )
    saved = _read_config(tmp_path)
    saved["auxiliary"] = {"vision": {"credential_ref": "alibaba-default"}}
    saved["image_gen"] = {"credential_ref": "alibaba-default"}
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(saved), encoding="utf-8"
    )
    invalidated = []
    monkeypatch.setattr(
        model_config,
        "_invalidate_vision_verification",
        lambda: invalidated.append("vision"),
    )
    monkeypatch.setattr(
        model_config,
        "_invalidate_image_gen_verification",
        lambda: invalidated.append("image_gen"),
        raising=False,
    )

    model_config.upsert_provider_credential(
        {"id": "alibaba-default", "provider": "dashscope", "api_key": "after"}
    )

    assert invalidated == ["vision", "image_gen"]


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


def test_alibaba_vision_config_persists_named_credential_and_beijing_endpoint(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "named-alibaba-secret",
        }
    )

    result = model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "credential_ref": "alibaba-default",
            "endpoint_mode": "public",
            "region": "cn-beijing",
        }
    )

    saved = _read_config(tmp_path)["auxiliary"]["vision"]
    assert saved == {
        "provider": "alibaba",
        "model": "qwen3-vl-plus",
        "credential_ref": "alibaba-default",
        "endpoint_mode": "public",
        "region": "cn-beijing",
        "workspace_id": "",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }
    assert result["vision"]["credential_ref"] == "alibaba-default"
    assert result["vision"]["endpoint_mode"] == "public"
    assert result["vision"]["region"] == "cn-beijing"
    assert result["vision"]["workspace_id"] == ""
    assert result["vision"]["base_url"] == saved["base_url"]
    assert "named-alibaba-secret" not in json.dumps(result, ensure_ascii=False)


def test_alibaba_vision_config_requires_explicit_international_region(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    model_config.set_vision_config(
        {"provider": "alibaba", "model": "qwen3-vl-plus"}
    )
    assert (
        _read_config(tmp_path)["auxiliary"]["vision"]["base_url"]
        == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )

    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "endpoint_mode": "public",
            "region": "ap-southeast-1",
        }
    )
    assert (
        _read_config(tmp_path)["auxiliary"]["vision"]["base_url"]
        == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    )


def test_alibaba_vision_config_rejects_model_outside_server_allowlist(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="unknown Alibaba vision model"):
        model_config.set_vision_config(
            {"provider": "alibaba", "model": "unlisted-qwen-vl"}
        )


def test_alibaba_vision_rejects_credential_ref_with_inline_api_key(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "named-secret",
        }
    )

    with pytest.raises(ValueError, match="credential_ref.*api_key"):
        model_config.set_vision_config(
            {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "credential_ref": "alibaba-default",
                "api_key": "must-not-write",
            }
        )

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY" not in env_text
    assert "must-not-write" not in env_text


def test_concurrent_vision_binding_and_credential_delete_cannot_leave_dangling_ref(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "named-secret",
        }
    )
    validated = threading.Event()
    continue_binding = threading.Event()
    original_load_credential = model_config.load_credential

    def pause_after_validation(*args, **kwargs):
        row = original_load_credential(*args, **kwargs)
        validated.set()
        assert continue_binding.wait(timeout=2)
        return row

    monkeypatch.setattr(model_config, "load_credential", pause_after_validation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        bind_future = pool.submit(
            model_config.set_vision_config,
            {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "credential_ref": "alibaba-default",
            },
        )
        assert validated.wait(timeout=2)
        delete_future = pool.submit(
            model_config.delete_provider_credential, "alibaba-default"
        )
        time.sleep(0.1)
        continue_binding.set()
        bind_future.result(timeout=2)
        with pytest.raises(ValueError, match="正在使用"):
            delete_future.result(timeout=2)

    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"]["credential_ref"] == "alibaba-default"
    assert saved["provider_credentials"][0]["id"] == "alibaba-default"


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


def _write_saved_vision_config(tmp_path, *, provider="alibaba", model="qwen3-vl-plus"):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"auxiliary": {"vision": {"provider": provider, "model": model}}}),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=test-only-key\n", encoding="utf-8")


def test_vision_config_distinguishes_configured_from_verified(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: tmp_path / "vision-verification.json")
    _write_saved_vision_config(tmp_path)

    result = model_config.get_vision_config()

    assert result["vision"]["verification"]["status"] == "configured_unverified"
    assert result["vision"]["verification"]["checked_at"] == ""


def test_vision_test_rejects_unconfigured_without_calling_provider(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: tmp_path / "vision-verification.json")
    calls = []

    async def should_not_run(**kwargs):
        calls.append(kwargs)
        return json.dumps({"success": True, "analysis": "TAIJI-VISION-CHECK-7319"})

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", should_not_run)
    result = model_config.test_vision_config()

    assert calls == []
    assert result["ok"] is False
    assert result["status"] == "unconfigured"
    assert result["error_code"] == "vision_not_configured"
    assert set(result) == {
        "ok", "status", "checked_at", "provider", "model",
        "error_code", "message", "diagnostic_id",
    }


def test_vision_test_persists_verified_result_without_model_text_or_secret(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)
    calls = []

    async def succeed(**kwargs):
        calls.append(kwargs)
        return json.dumps({
            "success": True,
            "analysis": "The image contains TAIJI-VISION-CHECK-7319 and secret-model-text.",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", succeed)
    result = model_config.test_vision_config()

    assert result["ok"] is True
    assert result["status"] == "verified"
    assert result["provider"] == "alibaba"
    assert result["model"] == "qwen3-vl-plus"
    assert result["error_code"] == ""
    assert calls and calls[0]["model"] == "qwen3-vl-plus"
    assert calls[0]["provider"] == "alibaba"
    assert calls[0]["strict_target"] is True
    assert Path(calls[0]["image_url"]).name == "vision-verification-probe.png"
    assert "识别图片" in calls[0]["user_prompt"]
    assert "TAIJI-VISION-CHECK-7319" not in calls[0]["user_prompt"]
    public_dump = json.dumps(result, ensure_ascii=False)
    persisted_dump = state_path.read_text(encoding="utf-8")
    for forbidden in ("test-only-key", "secret-model-text", str(tmp_path)):
        assert forbidden not in public_dump
        assert forbidden not in persisted_dump
    assert model_config.get_vision_config()["vision"]["verification"]["status"] == "verified"


def test_vision_probe_full_chain_uses_named_key_and_keeps_alibaba_identity(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(
        model_config, "_vision_verification_state_path", lambda *_: state_path
    )
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "named-probe-secret",
        }
    )
    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "credential_ref": "alibaba-default",
        }
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="TAIJI-VISION-CHECK-7319")
            )
        ]
    )
    fake_client = SimpleNamespace(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=response))
        ),
    )
    routed = []

    def route(provider, model=None, async_mode=False, **kwargs):
        routed.append((provider, model, async_mode, kwargs))
        return fake_client, model

    import agent.auxiliary_client as auxiliary_client

    monkeypatch.setattr(auxiliary_client, "resolve_provider_client", route)

    result = model_config.test_vision_config()

    assert result["status"] == "verified"
    assert result["provider"] == "alibaba"
    assert routed == [
        (
            "alibaba",
            "qwen3-vl-plus",
            True,
            {
                "explicit_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "explicit_api_key": "named-probe-secret",
                "api_mode": None,
            },
        )
    ]


def test_vision_test_failure_returns_only_safe_fields(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)

    async def fail(**_kwargs):
        return json.dumps({
            "success": False,
            "error": "401 leaked-test-only-key /private/provider/path",
            "analysis": "raw provider response",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", fail)
    result = model_config.test_vision_config()

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error_code"] == "vision_probe_failed"
    assert result["message"] == "识图验证失败，请检查网络、密钥、模型和账号状态后重试。"
    combined = json.dumps(result, ensure_ascii=False) + state_path.read_text(encoding="utf-8")
    for forbidden in ("leaked-test-only-key", "/private/provider/path", "raw provider response"):
        assert forbidden not in combined


def test_vision_verification_fingerprint_invalidates_when_key_changes(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)

    async def succeed(**_kwargs):
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", succeed)
    assert model_config.test_vision_config()["status"] == "verified"

    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=rotated-test-key\n", encoding="utf-8")

    assert model_config.get_vision_config()["vision"]["verification"]["status"] == "configured_unverified"


def test_vision_verification_fingerprint_invalidates_when_named_key_rotates(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "named-key-before",
        }
    )
    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "credential_ref": "alibaba-default",
        }
    )

    async def succeed(**_kwargs):
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", succeed)
    assert model_config.test_vision_config()["status"] == "verified"

    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=named-key-before",
            "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=named-key-after",
        ),
        encoding="utf-8",
    )

    assert model_config.get_vision_config()["vision"]["verification"]["status"] == "configured_unverified"


def test_saving_vision_config_invalidates_previous_verification(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)
    state_path.write_text('{"status":"verified"}', encoding="utf-8")

    model_config.set_vision_config({"provider": "alibaba", "model": "qwen3-vl-plus"})

    assert not state_path.exists()


def test_vision_probe_does_not_persist_success_after_key_rotation(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)

    async def rotate_key_during_probe(**_kwargs):
        (tmp_path / ".env").write_text(
            "DASHSCOPE_API_KEY=rotated-during-probe\n", encoding="utf-8"
        )
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", rotate_key_during_probe)
    result = model_config.test_vision_config()

    assert result["ok"] is False
    assert result["status"] == "configured_unverified"
    assert result["error_code"] == "vision_probe_superseded"
    assert not state_path.exists()


def test_vision_verification_is_isolated_per_profile(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    active_profile = {"name": "profile-a"}
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: active_profile["name"])
    monkeypatch.setattr(
        model_config,
        "_vision_verification_state_root",
        lambda: tmp_path / "vision-verification",
    )
    _write_saved_vision_config(tmp_path)

    async def succeed(**_kwargs):
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", succeed)
    assert model_config.test_vision_config()["status"] == "verified"
    active_profile["name"] = "profile-b"
    assert model_config.test_vision_config()["status"] == "verified"
    active_profile["name"] = "profile-a"

    assert model_config.get_vision_config()["vision"]["verification"]["status"] == "verified"
    assert len(list((tmp_path / "vision-verification").glob("*.json"))) == 2


def test_vision_probe_does_not_persist_after_profile_switch(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    active_profile = {"name": "profile-a"}
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: active_profile["name"])
    monkeypatch.setattr(
        model_config,
        "_vision_verification_state_root",
        lambda: tmp_path / "vision-verification",
    )
    _write_saved_vision_config(tmp_path)

    async def switch_profile(**_kwargs):
        active_profile["name"] = "profile-b"
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", switch_profile)
    result = model_config.test_vision_config()

    assert result["ok"] is False
    assert result["status"] == "configured_unverified"
    assert result["error_code"] == "vision_probe_superseded"
    assert list((tmp_path / "vision-verification").glob("*.json")) == []


def test_vision_probe_rejects_success_from_wrong_resolved_backend(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)

    async def fallback_success(**_kwargs):
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "openrouter",
            "resolved_model": "backup-vision",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", fallback_success)
    result = model_config.test_vision_config()

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error_code"] == "vision_probe_failed"


def test_newer_vision_probe_prevents_older_request_overwrite(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)
    first_started = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    call_count = {"value": 0}

    async def ordered_probe(**_kwargs):
        with call_lock:
            call_count["value"] += 1
            call_number = call_count["value"]
        if call_number == 1:
            first_started.set()
            assert release_first.wait(timeout=5)
            return json.dumps({
                "success": False,
                "error": "old failure",
                "analysis": "old failure",
                "resolved_provider": "alibaba",
                "resolved_model": "qwen3-vl-plus",
            })
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", ordered_probe)
    results = {}
    first = threading.Thread(
        target=lambda: results.setdefault("first", model_config.test_vision_config())
    )
    first.start()
    assert first_started.wait(timeout=5)
    results["second"] = model_config.test_vision_config()
    release_first.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert results["second"]["status"] == "verified"
    assert results["first"]["status"] == "configured_unverified"
    assert results["first"]["error_code"] == "vision_probe_superseded"
    assert model_config.get_vision_config()["vision"]["verification"]["status"] == "verified"


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


def test_image_gen_config_returns_named_ref_and_safe_endpoint_options(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                    "credential_ref": "alibaba-default",
                    "options": {
                        "endpoint_mode": "workspace",
                        "workspace_id": "llm-demo",
                        "region": "cn-beijing",
                        "api_key": "must-never-be-public",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_config, "_image_gen_provider_rows", lambda active: [])
    monkeypatch.setattr(
        model_config,
        "get_custom_image_provider_configs",
        lambda: {"providers": []},
    )

    result = model_config.get_image_gen_config()

    assert result["image_gen"]["credential_ref"] == "alibaba-default"
    assert result["image_gen"]["options"] == {
        "endpoint_mode": "workspace",
        "workspace_id": "llm-demo",
        "region": "cn-beijing",
    }
    assert "must-never-be-public" not in json.dumps(result)


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


def test_active_dashscope_provider_row_uses_named_credential_status(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "api_key": "named-secret",
        }
    )
    saved = _read_config(tmp_path)
    saved["image_gen"] = {
        "provider": "dashscope",
        "credential_ref": "alibaba-default",
        "options": {"workspace_id": "llm-demo"},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(saved), encoding="utf-8")

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
                ],
                "domestic": True,
                "integration_status": "stable",
            }

        def list_models(self):
            return [{"id": "qwen-image-2.0-pro", "display": "Qwen Image 2 Pro"}]

        def default_model(self):
            return "qwen-image-2.0-pro"

        def is_available(self):
            return True

    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr("agent.image_gen_registry.list_providers", lambda: [_Provider()])
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness", lambda: {}
    )

    row = next(
        item
        for item in model_config._image_gen_provider_rows("dashscope")
        if item["id"] == "dashscope"
    )

    assert row["credential_status"]["configured"] is True
    assert row["credential_status"]["missing"] == []
    assert row["key_status"] == {
        "configured": True,
        "source": "provider_credential",
        "env_var": "",
    }


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


def _write_saved_image_gen_config(
    tmp_path, *, provider="dashscope", model="qwen-image-2.0-pro"
):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"image_gen": {"provider": provider, "model": model}}),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=image-test-only-key\n", encoding="utf-8"
    )


class _ProbeImageProvider:
    name = "dashscope"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def is_available(self):
        return True

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.result() if callable(self.result) else self.result


def _install_probe_provider(monkeypatch, provider):
    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr("agent.image_gen_registry.get_provider", lambda name: provider if name == "dashscope" else None)


def test_image_gen_config_distinguishes_configured_from_verified(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_: tmp_path / "image-gen-verification.json",
        raising=False,
    )
    _write_saved_image_gen_config(tmp_path)
    _install_probe_provider(monkeypatch, _ProbeImageProvider({}))

    result = model_config.get_image_gen_config()

    assert result["image_gen"]["verification"]["status"] == "configured_unverified"
    assert result["image_gen"]["verification"]["checked_at"] == ""


def test_image_gen_provider_public_availability_requires_verified_probe(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _write_saved_image_gen_config(tmp_path)
    provider = _ProbeImageProvider({})
    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr("agent.image_gen_registry.list_providers", lambda: [provider])
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": False,
            "reason_code": "verification_required",
            "public_message": "图像生成已配置但尚未通过真实生图验证。",
        },
    )

    row = next(
        item
        for item in model_config._image_gen_provider_rows("dashscope")
        if item["id"] == "dashscope"
    )

    assert row["can_attempt"] is True
    assert row["available"] is False
    assert row["reason_code"] == "verification_required"


def test_image_gen_test_rejects_unconfigured_without_calling_provider(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    calls = []
    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: calls.append("registered"))

    result = model_config.test_image_gen_config()

    assert calls == []
    assert result["ok"] is False
    assert result["status"] == "unconfigured"
    assert result["error_code"] == "image_gen_not_configured"
    assert set(result) == {
        "ok", "status", "checked_at", "provider", "model",
        "error_code", "message", "diagnostic_id",
    }


def test_image_gen_probe_verifies_identity_magic_and_removes_probe_file(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "image-gen-verification.json"
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path, raising=False)
    _write_saved_image_gen_config(tmp_path)
    generated = tmp_path / "cache" / "images" / "probe.png"

    def successful_result():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nprobe")
        return {
            "success": True,
            "image": str(generated),
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "prompt": "must-not-persist",
        }

    provider = _ProbeImageProvider(successful_result)
    _install_probe_provider(monkeypatch, provider)

    result = model_config.test_image_gen_config()

    assert result["status"] == "verified"
    assert provider.calls == [{
        "prompt": "生成一张简洁的蓝色几何图形测试图，不包含人物、文字或品牌。",
        "aspect_ratio": "square",
        "num_images": 1,
        "model": "qwen-image-2.0-pro",
    }]
    assert not generated.exists()
    public_dump = json.dumps(result, ensure_ascii=False)
    persisted_dump = state_path.read_text(encoding="utf-8")
    for forbidden in ("image-test-only-key", "must-not-persist", str(generated), "digest"):
        assert forbidden not in public_dump
        assert forbidden not in persisted_dump
    assert model_config.get_image_gen_config()["image_gen"]["verification"]["status"] == "verified"


@pytest.mark.parametrize(
    ("overrides", "payload", "expected_code"),
    [
        ({"provider": "other"}, b"\x89PNG\r\n\x1a\nprobe", "image_gen_probe_failed"),
        ({"model": "other-model"}, b"\x89PNG\r\n\x1a\nprobe", "image_gen_probe_failed"),
        ({}, b"not-an-image", "image_gen_invalid_file"),
    ],
)
def test_image_gen_probe_rejects_wrong_identity_or_invalid_header(
    monkeypatch, tmp_path, overrides, payload, expected_code
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: tmp_path / "state.json", raising=False)
    _write_saved_image_gen_config(tmp_path)
    generated = tmp_path / "cache" / "images" / "probe.png"

    def result():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(payload)
        return {
            "success": True,
            "image": str(generated),
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            **overrides,
        }

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result))

    response = model_config.test_image_gen_config()

    assert response["status"] == "failed"
    assert response["error_code"] == expected_code
    assert not generated.exists()


@pytest.mark.parametrize("image_value", ["", "/does/not/exist.png"])
def test_image_gen_probe_rejects_missing_image_file(monkeypatch, tmp_path, image_value):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: tmp_path / "state.json", raising=False)
    _write_saved_image_gen_config(tmp_path)
    provider = _ProbeImageProvider({
        "success": True,
        "image": image_value,
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
    })
    _install_probe_provider(monkeypatch, provider)

    response = model_config.test_image_gen_config()

    assert response["status"] == "failed"
    assert response["error_code"] == "image_gen_invalid_file"


def test_image_gen_verification_invalidates_when_key_changes(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path, raising=False)
    _write_saved_image_gen_config(tmp_path)
    generated = tmp_path / "cache" / "images" / "probe.webp"

    def result():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"RIFF\x04\x00\x00\x00WEBP")
        return {"success": True, "image": str(generated), "provider": "dashscope", "model": "qwen-image-2.0-pro"}

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result))
    assert model_config.test_image_gen_config()["status"] == "verified"
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=rotated\n", encoding="utf-8")

    assert model_config.get_image_gen_config()["image_gen"]["verification"]["status"] == "configured_unverified"


def test_image_gen_verification_invalidates_when_named_key_rotates(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path, raising=False)
    model_config.upsert_provider_credential({
        "id": "alibaba-image",
        "provider": "dashscope",
        "api_key": "before",
    })
    saved = _read_config(tmp_path)
    saved["image_gen"] = {
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
        "credential_ref": "alibaba-image",
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(saved), encoding="utf-8")
    generated = tmp_path / "cache" / "images" / "probe.png"

    def result():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nprobe")
        return {"success": True, "image": str(generated), "provider": "dashscope", "model": "qwen-image-2.0-pro"}

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result))
    assert model_config.test_image_gen_config()["status"] == "verified"
    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY=before",
            "TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY=after",
        ),
        encoding="utf-8",
    )

    assert model_config.get_image_gen_config()["image_gen"]["verification"]["status"] == "configured_unverified"


def test_image_gen_probe_failure_returns_only_safe_fields(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path, raising=False)
    _write_saved_image_gen_config(tmp_path)
    _install_probe_provider(monkeypatch, _ProbeImageProvider({
        "success": False,
        "error": "401 image-test-only-key /private/provider/path",
        "raw_response": "must-not-persist",
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
    }))

    response = model_config.test_image_gen_config()

    assert response["status"] == "failed"
    combined = json.dumps(response, ensure_ascii=False) + state_path.read_text(encoding="utf-8")
    for forbidden in ("image-test-only-key", "/private/provider/path", "must-not-persist"):
        assert forbidden not in combined


def test_saving_image_gen_config_invalidates_previous_verification(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path, raising=False)
    state_path.write_text('{"status":"verified"}', encoding="utf-8")
    monkeypatch.setattr(model_config, "_image_gen_provider_rows", lambda *_: [_dashscope_image_provider_row()])

    model_config.set_image_gen_config({"provider": "dashscope", "model": "qwen-image-2.0-pro", "api_key": "key"})

    assert not state_path.exists()


def test_image_gen_probe_isolated_by_profile_and_newer_probe_wins(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    active_profile = {"name": "profile-a"}
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: active_profile["name"])
    monkeypatch.setattr(model_config, "_image_gen_verification_state_root", lambda: tmp_path / "states", raising=False)
    _write_saved_image_gen_config(tmp_path)
    first_started = threading.Event()
    release_first = threading.Event()
    calls = {"count": 0}

    def result():
        calls["count"] += 1
        n = calls["count"]
        path = tmp_path / "cache" / "images" / f"probe-{n}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xffprobe")
        if n == 1:
            first_started.set()
            assert release_first.wait(timeout=5)
        return {"success": True, "image": str(path), "provider": "dashscope", "model": "qwen-image-2.0-pro"}

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result))
    results = {}
    first = threading.Thread(target=lambda: results.setdefault("first", model_config.test_image_gen_config()))
    first.start()
    assert first_started.wait(timeout=5)
    results["second"] = model_config.test_image_gen_config()
    release_first.set()
    first.join(timeout=5)

    assert results["second"]["status"] == "verified"
    assert results["first"]["error_code"] == "image_gen_probe_superseded"
    active_profile["name"] = "profile-b"
    assert model_config.get_image_gen_config()["image_gen"]["verification"]["status"] == "configured_unverified"
