"""Contract coverage for the domestic Zhipu GLM main-model provider."""

from __future__ import annotations

import yaml

import api.config as config
import api.main_model_verification as main_model_verification
import api.profiles as profiles
import api.providers as providers
from api import model_config


def _isolate_main_model_config(monkeypatch, tmp_path):
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
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda _name: [],
    )
    config_path.write_text("model: {}\n", encoding="utf-8")
    return config_path


def test_zai_cn_provider_is_selectable_with_domestic_defaults(monkeypatch):
    monkeypatch.setattr(providers, "get_config", lambda: {})

    payload = providers.get_providers()
    domestic = next(row for row in payload["providers"] if row["id"] == "zai-cn")

    assert domestic["display_name"] == "智谱 GLM（国内）"
    assert domestic["configurable"] is True
    assert domestic["models"][0] == {"id": "glm-5", "label": "GLM-5"}
    assert providers._PROVIDER_ENV_VAR["zai-cn"] == "GLM_CN_API_KEY"
    assert profiles._PROVIDER_ENV_MAP["zai-cn"] == "GLM_CN_API_KEY"
    assert config._PROVIDER_ALIASES["glm-cn"] == "zai-cn"


def test_zai_cn_save_uses_isolated_api_key_and_no_base_url(monkeypatch, tmp_path):
    config_path = _isolate_main_model_config(monkeypatch, tmp_path)

    result = model_config.set_main_model_config(
        {
            "provider": "zai-cn",
            "model": "glm-5",
            "api_key": "TEST_ONLY-key",
            "base_url": "",
        }
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    material = model_config._main_model_material(saved)

    assert result["ok"] is True
    assert saved["model"]["provider"] == "zai-cn"
    assert saved["model"]["default"] == "glm-5"
    assert "base_url" not in saved["model"]
    assert "GLM_CN_API_KEY=TEST_ONLY-key" in env_text
    assert "GLM_API_KEY=" not in env_text
    assert material["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert material["api_key"] == "TEST_ONLY-key"


def test_zai_cn_connection_check_receives_domestic_material(monkeypatch, tmp_path):
    _isolate_main_model_config(monkeypatch, tmp_path)
    model_config.set_main_model_config(
        {
            "provider": "zai-cn",
            "model": "glm-5",
            "api_key": "TEST_ONLY-key",
        }
    )
    captured = {}

    def fake_check_connection(material, _state_path):
        captured.update(material)
        return {
            "state": "connection_verified",
            "provider": material["provider"],
            "model": material["model"],
        }

    monkeypatch.setattr(
        main_model_verification,
        "check_connection",
        fake_check_connection,
    )

    result = model_config.check_main_model_connection()

    assert result["verification"]["state"] == "connection_verified"
    assert captured["provider"] == "zai-cn"
    assert captured["model"] == "glm-5"
    assert captured["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert captured["api_key"] == "TEST_ONLY-key"
