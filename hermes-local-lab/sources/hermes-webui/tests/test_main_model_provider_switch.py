"""Regression coverage for switching the built-in main model provider."""

from __future__ import annotations

import yaml
import pytest

import api.profiles as profiles
import api.providers as providers
from api import model_config


def test_provider_switch_discards_hidden_base_url(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(providers, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(model_config, "get_providers", lambda: {"providers": []})
    monkeypatch.setattr(
        model_config,
        "get_image_gen_config",
        lambda: {"image_gen": {}, "providers": []},
    )
    config_path.write_text(
        yaml.safe_dump(
            {
                "model": {
                    "provider": "deepseek",
                    "default": "deepseek-chat",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_mode": "chat_completions",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    model_config.set_main_model_config(
        {
            "provider": "zai",
            "model": "glm-5",
            "base_url": "https://api.deepseek.com/v1",
        }
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "base_url" not in saved["model"]
    assert "api_mode" not in saved["model"]


def test_provider_switch_preserves_explicit_new_base_url(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(providers, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(model_config, "get_providers", lambda: {"providers": []})
    monkeypatch.setattr(
        model_config,
        "get_image_gen_config",
        lambda: {"image_gen": {}, "providers": []},
    )
    config_path.write_text(
        yaml.safe_dump(
            {
                "model": {
                    "provider": "deepseek",
                    "default": "deepseek-chat",
                    "base_url": "https://api.deepseek.com/v1",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    model_config.set_main_model_config(
        {
            "provider": "zai",
            "model": "glm-5",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
        }
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["model"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"


@pytest.mark.parametrize("previous_model_key", ["default", "model", "name"])
def test_provider_switch_rejects_unchanged_model_owned_by_previous_provider(
    monkeypatch,
    tmp_path,
    previous_model_key,
):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(providers, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    config_path.write_text(
        yaml.safe_dump(
            {
                "model": {
                    "provider": "deepseek",
                    previous_model_key: "deepseek-chat",
                    "base_url": "https://api.deepseek.com/v1",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="仍属于 DeepSeek"):
        model_config.set_main_model_config(
            {
                "provider": "zai",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
            }
        )


def test_provider_switch_allows_unknown_new_model(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(providers, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(model_config, "get_providers", lambda: {"providers": []})
    monkeypatch.setattr(
        model_config,
        "get_image_gen_config",
        lambda: {"image_gen": {}, "providers": []},
    )
    config_path.write_text(
        yaml.safe_dump(
            {
                "model": {
                    "provider": "deepseek",
                    "default": "deepseek-chat",
                    "api_mode": "chat_completions",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = model_config.set_main_model_config(
        {
            "provider": "zai",
            "model": "glm-next-preview",
            "base_url": "",
        }
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert saved["model"]["default"] == "glm-next-preview"
    assert "api_mode" not in saved["model"]


def test_same_configurable_provider_omitted_base_url_preserves_override(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(model_config, "get_providers", lambda: {"providers": []})
    monkeypatch.setattr(
        model_config,
        "get_image_gen_config",
        lambda: {"image_gen": {}, "providers": []},
    )
    config_path.write_text(
        yaml.safe_dump(
            {
                "model": {
                    "provider": "deepseek",
                    "default": "deepseek-chat",
                    "base_url": "https://proxy.example/v1",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    model_config.set_main_model_config(
        {"provider": "deepseek", "model": "deepseek-chat"}
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["model"]["base_url"] == "https://proxy.example/v1"


def test_provider_change_omitted_base_url_clears_previous_override(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(model_config, "get_providers", lambda: {"providers": []})
    monkeypatch.setattr(
        model_config,
        "get_image_gen_config",
        lambda: {"image_gen": {}, "providers": []},
    )
    config_path.write_text(
        "model:\n  provider: deepseek\n  default: deepseek-chat\n"
        "  base_url: https://proxy.example/v1\n",
        encoding="utf-8",
    )

    model_config.set_main_model_config(
        {"provider": "zai", "model": "glm-5"}
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "base_url" not in saved["model"]


def test_same_configurable_provider_explicit_empty_base_url_clears_override(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(model_config, "get_providers", lambda: {"providers": []})
    monkeypatch.setattr(
        model_config,
        "get_image_gen_config",
        lambda: {"image_gen": {}, "providers": []},
    )
    config_path.write_text(
        "model:\n  provider: deepseek\n  default: deepseek-chat\n"
        "  base_url: https://proxy.example/v1\n",
        encoding="utf-8",
    )

    model_config.set_main_model_config(
        {"provider": "deepseek", "model": "deepseek-chat", "base_url": ""}
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "base_url" not in saved["model"]
