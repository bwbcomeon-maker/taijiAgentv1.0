"""Tests for named provider credential references and legacy fallback."""

from __future__ import annotations

import pytest

from agent.provider_credentials import (
    credential_secret_env,
    find_credential,
    load_credential,
    normalize_credential_id,
    provider_family,
    resolve_api_key,
)


def _config() -> dict:
    return {
        "provider_credentials": [
            {
                "id": "alibaba-default",
                "provider_family": "alibaba_dashscope",
                "label": "Alibaba default",
                "auth_type": "api_key",
                "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
            }
        ]
    }


def test_named_alibaba_credential_takes_precedence_over_legacy_env(monkeypatch):
    monkeypatch.setenv("TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", "named-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-key")

    assert resolve_api_key("alibaba", "alibaba-default", config_data=_config()) == "named-key"
    assert resolve_api_key("dashscope", "alibaba-default", config_data=_config()) == "named-key"


def test_missing_credential_ref_falls_back_to_legacy_provider_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-dashscope")
    monkeypatch.setenv("ZAI_API_KEY", "legacy-zai")

    assert resolve_api_key("alibaba", config_data={}) == "legacy-dashscope"
    assert resolve_api_key("zhipu-image", config_data={}) == "legacy-zai"


def test_zhipu_legacy_env_precedence(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "glm-primary")
    monkeypatch.setenv("ZAI_API_KEY", "zai-compatible")
    monkeypatch.setenv("Z_AI_API_KEY", "z-ai-compatible")

    assert resolve_api_key("zai", config_data={}) == "glm-primary"
    monkeypatch.delenv("GLM_API_KEY")
    assert resolve_api_key("zai", config_data={}) == "zai-compatible"
    monkeypatch.delenv("ZAI_API_KEY")
    assert resolve_api_key("zai", config_data={}) == "z-ai-compatible"


def test_provider_family_mismatch_is_rejected(monkeypatch):
    monkeypatch.setenv("TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", "named-key")

    with pytest.raises(ValueError, match="不属于当前 Provider"):
        resolve_api_key("zai", "alibaba-default", config_data=_config())


def test_unknown_credential_ref_fails_safely():
    with pytest.raises(ValueError, match="不存在"):
        resolve_api_key("alibaba", "missing", config_data=_config())


def test_tampered_secret_env_is_rejected_without_reading_unrelated_env(monkeypatch):
    config = _config()
    config["provider_credentials"][0]["secret_env"] = "UNRELATED_API_KEY"
    monkeypatch.setenv("UNRELATED_API_KEY", "must-not-be-read")

    with pytest.raises(ValueError, match="Secret 环境变量"):
        resolve_api_key("alibaba", "alibaba-default", config_data=config)


def test_credential_helpers_normalize_aliases_and_find_rows(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "provider_credentials:\n"
        "  - id: Alibaba Default\n"
        "    provider_family: alibaba_dashscope\n"
        "    secret_env: TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))

    assert normalize_credential_id(" Alibaba Default ") == "alibaba-default"
    assert provider_family("dashscope") == "alibaba_dashscope"
    assert provider_family("zhipu-image") == "zhipu"
    assert credential_secret_env("Alibaba Default") == "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY"
    assert find_credential(_config(), "ALIBABA DEFAULT")["id"] == "alibaba-default"
    assert load_credential("Alibaba Default")["provider_family"] == "alibaba_dashscope"


def test_invalid_credential_id_is_rejected():
    with pytest.raises(ValueError):
        normalize_credential_id("../../secret")
