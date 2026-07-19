"""Tests for named provider credential references and legacy fallback."""

from __future__ import annotations

import pytest
import yaml

from agent.provider_credentials import (
    AUTH_TYPES,
    auth_schema,
    credential_transaction,
    credential_secret_env,
    default_credential_ref,
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


@pytest.mark.parametrize(
    ("auth_type", "field_names", "editable"),
    [
        ("api_key", ["api_key"], True),
        ("bearer_token", ["bearer_token"], False),
        ("access_key_secret", ["access_key_id", "access_key_secret"], False),
        ("service_account", ["service_account_json"], False),
        ("oauth", [], False),
        ("no_auth", [], False),
    ],
)
def test_auth_schema_expresses_supported_shapes_without_claiming_adapter_support(
    auth_type, field_names, editable
):
    schema = auth_schema(auth_type)

    assert set(AUTH_TYPES) == {
        "api_key",
        "bearer_token",
        "access_key_secret",
        "service_account",
        "oauth",
        "no_auth",
    }
    assert schema["auth_type"] == auth_type
    assert [field["name"] for field in schema["credential_fields"]] == field_names
    assert schema["editable"] is editable
    assert isinstance(schema["message"], str) and schema["message"]


def test_named_default_credential_precedes_legacy_env_without_mutating_config(monkeypatch):
    config = _config()
    config["provider_credentials"][0]["default"] = True
    before = yaml.safe_dump(config, sort_keys=False)
    monkeypatch.setenv("TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", "named-default")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-key")

    assert default_credential_ref("alibaba", config_data=config) == "alibaba-default"
    assert resolve_api_key("alibaba", config_data=config) == "named-default"
    assert yaml.safe_dump(config, sort_keys=False) == before


def test_explicit_config_path_keeps_nested_profile_transaction_on_same_lock(
    monkeypatch, tmp_path
):
    active_config_path = tmp_path / "active-profile" / "config.yaml"
    unrelated_config_path = tmp_path / "default-profile" / "config.yaml"
    active_config_path.parent.mkdir(parents=True)
    unrelated_config_path.parent.mkdir(parents=True)
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(unrelated_config_path))
    monkeypatch.setenv(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
        "named-default",
    )
    config = _config()
    config["provider_credentials"][0]["default"] = True

    with credential_transaction(active_config_path):
        assert (
            default_credential_ref(
                "alibaba",
                config_data=config,
                config_path=active_config_path,
            )
            == "alibaba-default"
        )
        assert (
            resolve_api_key(
                "alibaba",
                config_data=config,
                config_path=active_config_path,
            )
            == "named-default"
        )


def test_explicit_config_path_reads_secret_from_the_same_directory(
    monkeypatch,
    tmp_path,
):
    active_config_path = tmp_path / "active-profile" / "config.yaml"
    unrelated_home = tmp_path / "default-profile"
    active_config_path.parent.mkdir(parents=True)
    unrelated_home.mkdir(parents=True)
    config = _config()
    config["provider_credentials"][0]["default"] = True
    (active_config_path.parent / ".env").write_text(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=active-profile-key\n",
        encoding="utf-8",
    )
    (unrelated_home / ".env").write_text(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=wrong-profile-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(unrelated_home))
    monkeypatch.delenv(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
        raising=False,
    )

    assert (
        default_credential_ref(
            "alibaba",
            config_data=config,
            config_path=active_config_path,
        )
        == "alibaba-default"
    )
    assert (
        resolve_api_key(
            "alibaba",
            "alibaba-default",
            config_data=config,
            config_path=active_config_path,
        )
        == "active-profile-key"
    )


def test_persisted_named_secret_wins_over_stale_process_environment(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "active-profile" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    (config_path.parent / ".env").write_text(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=fresh-disk-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
        "stale-process-key",
    )

    assert (
        resolve_api_key(
            "alibaba",
            "alibaba-default",
            config_data=_config(),
            config_path=config_path,
        )
        == "fresh-disk-key"
    )


def test_deleted_persisted_named_secret_is_not_revived_from_stale_process_environment(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "active-profile" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    (config_path.parent / ".env").write_text(
        "UNRELATED_KEY=still-present\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
        "stale-process-key",
    )

    assert (
        resolve_api_key(
            "alibaba",
            "alibaba-default",
            config_data=_config(),
            config_path=config_path,
        )
        == ""
    )


def test_duplicate_persisted_named_secret_fails_closed(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "active-profile" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    (config_path.parent / ".env").write_text(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=older-key\n"
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=newer-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
        raising=False,
    )

    with pytest.raises(ValueError, match="duplicate"):
        resolve_api_key(
            "alibaba",
            "alibaba-default",
            config_data=_config(),
            config_path=config_path,
        )


def test_named_secret_can_use_process_environment_when_persisted_env_is_absent(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "active-profile" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    monkeypatch.setenv(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
        "injected-process-key",
    )

    assert (
        resolve_api_key(
            "alibaba",
            "alibaba-default",
            config_data=_config(),
            config_path=config_path,
        )
        == "injected-process-key"
    )


def test_unmarked_named_credential_does_not_take_over_legacy_config(monkeypatch):
    monkeypatch.setenv("TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", "named-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-key")

    assert default_credential_ref("alibaba", config_data=_config()) == ""
    assert resolve_api_key("alibaba", config_data=_config()) == "legacy-key"


def test_default_credential_can_be_configured_only_in_persisted_env(monkeypatch, tmp_path):
    config = _config()
    config["provider_credentials"][0]["default"] = True
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=persisted-key\n", encoding="utf-8"
    )

    assert default_credential_ref("alibaba", config_data=config) == "alibaba-default"


def test_explicit_ref_precedes_family_default(monkeypatch):
    config = _config()
    config["provider_credentials"].append(
        {
            "id": "alibaba-image",
            "provider_family": "alibaba_dashscope",
            "label": "Image",
            "auth_type": "api_key",
            "secret_env": "TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY",
        }
    )
    monkeypatch.setenv("TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", "default-key")
    monkeypatch.setenv("TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY", "image-key")

    assert resolve_api_key("alibaba", "alibaba-image", config_data=config) == "image-key"


def test_multiple_explicit_family_defaults_fail_closed():
    config = _config()
    config["provider_credentials"][0]["default"] = True
    config["provider_credentials"].append(
        {
            "id": "alibaba-other",
            "provider_family": "alibaba_dashscope",
            "auth_type": "api_key",
            "secret_env": "TAIJI_CREDENTIAL_ALIBABA_OTHER_API_KEY",
            "default": True,
        }
    )

    with pytest.raises(ValueError, match="默认凭据"):
        default_credential_ref("alibaba", config_data=config)
