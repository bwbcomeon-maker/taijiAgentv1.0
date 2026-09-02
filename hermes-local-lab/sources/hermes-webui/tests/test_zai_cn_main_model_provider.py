"""Contract coverage for the domestic Zhipu GLM main-model provider."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
import yaml

import api.config as config
import api.main_model_verification as main_model_verification
import api.profiles as profiles
import api.providers as providers
import api.routes as routes
from api import model_config
from api.provider_endpoints import public_endpoint


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
    config_path.write_text(
        "model:\n  provider: zai-cn\n  default: glm-5\n"
        "  base_url: https://candidate.example/v1\n"
        "  api_mode: chat_completions\n",
        encoding="utf-8",
    )

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
    assert "api_mode" not in saved["model"]
    assert result["main"]["endpoint"] == {
        "display_url": "https://open.bigmodel.cn/api/paas/v4",
        "policy": "fixed",
        "source": "system",
        "editable": False,
        "status": "resolved",
        "override_ignored": False,
    }
    assert "candidate.example" not in json.dumps(result)
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


def test_zai_cn_dirty_connection_and_chat_fingerprint_use_effective_endpoint(
    monkeypatch,
    tmp_path,
):
    config_path = _isolate_main_model_config(monkeypatch, tmp_path)
    dirty = {
        "model": {
            "provider": "zai-cn",
            "default": "glm-5",
            "base_url": "https://api.deepseek.com/v1",
        }
    }
    config_path.write_text(yaml.safe_dump(dirty), encoding="utf-8")
    captured = {}

    def fake_check_connection(material, _state_path):
        captured.update(material)
        return {"state": "connection_verified"}

    monkeypatch.setattr(main_model_verification, "check_connection", fake_check_connection)
    checked = model_config.check_main_model_connection()
    fingerprint = model_config.capture_main_model_chat_fingerprint("zai-cn", "glm-5")
    effective_material = model_config._main_model_material(dirty)
    expected_fingerprint = main_model_verification.configuration_fingerprint(
        effective_material
    )
    stale_material = dict(effective_material)
    stale_material["base_url"] = "https://api.deepseek.com/v1"
    stale_fingerprint = main_model_verification.configuration_fingerprint(
        stale_material
    )

    assert checked["verification"]["state"] == "connection_verified"
    assert captured["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert fingerprint == expected_fingerprint
    assert fingerprint != stale_fingerprint


def test_zai_cn_dirty_same_provider_uses_fixed_endpoint_everywhere(monkeypatch, tmp_path):
    config_path = _isolate_main_model_config(monkeypatch, tmp_path)
    config_path.write_text(
        "model:\n  provider: zai-cn\n  default: glm-5\n  base_url: https://api.deepseek.com/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        model_config,
        "load_credential_config",
        lambda _path: yaml.safe_load(config_path.read_text(encoding="utf-8")),
    )

    resolved = config.resolve_model_provider("glm-5")
    material = model_config._main_model_material(yaml.safe_load(config_path.read_text()))
    endpoint = public_endpoint(
        "zai-cn",
        configured_url="https://api.deepseek.com/v1",
        stored_main_override_present=True,
    )

    assert resolved == (
        "glm-5",
        "zai-cn",
        "https://open.bigmodel.cn/api/paas/v4",
    )
    assert material["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert endpoint["display_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert endpoint["policy"] == "fixed"
    assert endpoint["override_ignored"] is True
    assert endpoint["status"] == "resolved"


def test_public_endpoint_projection_redacts_unsafe_url_and_runtime_states():
    unsafe = public_endpoint(
        "custom",
        configured_url="https://u:p@host/v1?token=secret#fragment",
    )
    assert unsafe["display_url"] == "https://host/v1"
    assert unsafe["status"] == "invalid_saved_value"
    assert unsafe["editable"] is False
    assert unsafe["override_ignored"] is False
    assert "secret" not in str(unsafe)

    managed = public_endpoint("openai-codex", runtime_selector_unresolved=True)
    assert managed["display_url"] is None
    assert managed["status"] == "runtime_managed"

    unresolved = public_endpoint(
        "deepseek",
        runtime_selector_unresolved=True,
    )
    assert unresolved["display_url"] is None
    assert unresolved["status"] == "runtime_unresolved"
    assert unresolved["source"] == "runtime"

    custom = public_endpoint("custom", configured_url="https://proxy.example/v1")
    assert custom["display_url"] == "https://proxy.example/v1"
    assert custom["source"] == "custom"
    assert custom["editable"] is True
    assert custom["status"] == "resolved"

    unsafe_with_header = public_endpoint(
        "custom",
        configured_url=(
            "https://u:p@host/v1\n"
            "Authorization: Bearer TEST_ONLY_SECRET?token=secret#fragment"
        ),
    )
    assert unsafe_with_header["display_url"] == "https://host/v1"
    serialized = str(unsafe_with_header)
    for forbidden in (
        "Authorization",
        "Bearer",
        "TEST_ONLY_SECRET",
        "u:p",
        "token=secret",
        "fragment",
        "\\n",
    ):
        assert forbidden not in serialized


def test_fixed_endpoint_cleanup_returns_one_shot_mutation_event(monkeypatch, tmp_path):
    config_path = _isolate_main_model_config(monkeypatch, tmp_path)
    config_path.write_text(
        "model:\n  provider: zai-cn\n  default: glm-5\n"
        "  base_url: https://api.deepseek.com/v1\n",
        encoding="utf-8",
    )

    dirty = model_config.get_model_config()
    assert dirty["main"]["endpoint"]["override_ignored"] is True
    assert "api.deepseek.com" not in str(dirty)

    saved = model_config.set_main_model_config(
        {"provider": "zai-cn", "model": "glm-5"}
    )
    assert saved["main"]["endpoint"]["override_ignored"] is False
    assert saved["endpoint_mutation"] == {
        "code": "fixed_override_cleaned"
    }
    later = model_config.get_model_config()
    assert later["main"]["endpoint"]["override_ignored"] is False
    assert "endpoint_mutation" not in later


def test_clean_fixed_provider_explicit_candidate_is_reported_once_and_not_leaked(
    monkeypatch, tmp_path
):
    config_path = _isolate_main_model_config(monkeypatch, tmp_path)
    before = config_path.read_bytes()

    result = model_config.set_main_model_config(
        {
            "provider": "zai-cn",
            "model": "glm-5",
            "base_url": "https://candidate.example/v1?secret=NO_LEAK#fragment",
        }
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert result["main"]["endpoint"]["policy"] == "fixed"
    assert result["main"]["endpoint"]["override_ignored"] is False
    assert result["endpoint_mutation"] == {"code": "fixed_override_cleaned"}
    assert "base_url" not in saved["model"]
    assert "candidate.example" not in json.dumps(result)
    assert config_path.read_bytes() != before


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "example.invalid/v1",
        "https:///v1",
        "https://u:p@example.invalid/v1",
        "https://example.invalid/v1?token=secret",
        "https://example.invalid/v1#fragment",
        "https://example.invalid/v1\nAuthorization: Bearer TEST_ONLY_SECRET",
    ],
)
def test_custom_base_url_rejects_unsafe_values_without_disk_change(
    monkeypatch, tmp_path, base_url
):
    config_path = _isolate_main_model_config(monkeypatch, tmp_path)
    before = config_path.read_bytes()

    with pytest.raises(model_config.InvalidBaseUrlError) as exc_info:
        model_config.set_main_model_config(
            {"provider": "custom", "model": "local-model", "base_url": base_url}
        )

    assert exc_info.value.code == "invalid_base_url"
    assert exc_info.value.field == "base_url"
    assert config_path.read_bytes() == before


def test_custom_localhost_http_is_saved(monkeypatch, tmp_path):
    config_path = _isolate_main_model_config(monkeypatch, tmp_path)

    model_config.set_main_model_config(
        {
            "provider": "custom",
            "model": "local-model",
            "base_url": "http://localhost:1234/v1/",
        }
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["model"]["base_url"] == "http://localhost:1234/v1"


def test_custom_omitted_base_url_is_rejected_without_disk_change(monkeypatch, tmp_path):
    config_path = _isolate_main_model_config(monkeypatch, tmp_path)
    before = config_path.read_bytes()

    with pytest.raises(model_config.InvalidBaseUrlError):
        model_config.set_main_model_config(
            {"provider": "custom", "model": "local-model"}
        )

    assert config_path.read_bytes() == before


def test_model_config_route_returns_stable_custom_url_error_without_echo(
    monkeypatch, tmp_path
):
    config_path = _isolate_main_model_config(monkeypatch, tmp_path)
    before = config_path.read_bytes()

    class Handler:
        def __init__(self):
            self.wfile = io.BytesIO()
            self.status = None

        def send_response(self, status):
            self.status = status

        def send_header(self, *_args):
            return None

        def end_headers(self):
            return None

    handler = Handler()
    payload = {
        "provider": "custom",
        "model": "local-model",
        "base_url": "https://u:p@example.invalid/v1?token=TEST_ONLY_SECRET#frag",
    }
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: payload)

    routes.handle_post(handler, SimpleNamespace(path="/api/model-config/main"))
    response = json.loads(handler.wfile.getvalue())
    assert handler.status == 400
    assert set(response) == {"error", "error_code", "field"}
    assert response["error_code"] == "invalid_base_url"
    assert response["field"] == "base_url"
    assert all(secret not in json.dumps(response) for secret in (
        "example.invalid", "TEST_ONLY_SECRET", "Authorization", "u:p", "token"
    ))
    assert config_path.read_bytes() == before


def test_fixed_cleanup_write_failure_keeps_dirty_projection(monkeypatch, tmp_path):
    config_path = _isolate_main_model_config(monkeypatch, tmp_path)
    config_path.write_text(
        "model:\n  provider: zai-cn\n  default: glm-5\n"
        "  base_url: https://candidate.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        model_config,
        "_commit_expected_config_env",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated write failure")
        ),
    )

    with pytest.raises(RuntimeError, match="simulated write failure"):
        model_config.set_main_model_config(
            {"provider": "zai-cn", "model": "glm-5"}
        )

    current = model_config.get_model_config()
    assert current["main"]["endpoint"]["override_ignored"] is True
    assert "candidate.example" not in json.dumps(current)


def test_runtime_managed_projection_never_restores_configured_candidate():
    projected = public_endpoint(
        "openai-codex",
        configured_url="https://candidate.example/v1?token=TEST_ONLY_SECRET",
    )

    assert projected == {
        "display_url": None,
        "policy": "runtime_managed",
        "source": "runtime",
        "editable": False,
        "status": "runtime_managed",
        "override_ignored": False,
    }
    assert "candidate.example" not in json.dumps(projected)


def test_active_named_custom_row_reuses_main_endpoint_exactly(monkeypatch, tmp_path):
    cfg = {
        "model": {
            "provider": "custom:research-gateway",
            "default": "research-model",
            "base_url": "https://model.example/v1",
        },
        "custom_providers": [
            {
                "name": "Research Gateway",
                "base_url": "https://admin.example/v1",
                "model": "research-model",
            },
            {
                "name": "Other Gateway",
                "base_url": "https://other-admin.example/v1",
                "model": "other-model",
            },
        ],
    }
    monkeypatch.setattr(providers, "get_config", lambda: cfg)
    monkeypatch.setattr(providers, "_provider_has_key", lambda _pid: False)
    rows = {
        row["id"]: row
        for row in providers.get_providers()["providers"]
    }
    material = model_config._main_model_material(cfg)
    main = model_config._main_model_endpoint(cfg, material)

    assert rows["custom:research-gateway"]["endpoint"] == main
    assert rows["custom:research-gateway"]["endpoint"]["display_url"] == (
        "https://model.example/v1"
    )
    assert rows["custom:research-gateway"]["endpoint"]["editable"] is False
    assert rows["custom:other-gateway"]["endpoint"] == {
        "display_url": "https://other-admin.example/v1",
        "policy": "configurable",
        "source": "managed",
        "editable": False,
        "status": "resolved",
        "override_ignored": False,
    }


def test_connection_check_resolves_zai_runtime_endpoint_before_models_probe(
    monkeypatch, tmp_path
):
    _isolate_main_model_config(monkeypatch, tmp_path)
    model_config.set_main_model_config(
        {"provider": "zai", "model": "glm-5", "api_key": "TEST_ONLY-zai-key"}
    )
    monkeypatch.setattr(
        "hermes_cli.auth._resolve_zai_status_endpoint",
        lambda *_args, **_kwargs: ("", "runtime", "runtime_unresolved"),
    )
    runtime_calls = []

    def resolve_runtime(api_key, default_url, env_override):
        runtime_calls.append((api_key, default_url, env_override))
        return "https://api.z.ai/api/paas/v4", "runtime"

    monkeypatch.setattr("hermes_cli.auth._resolve_zai_runtime_endpoint", resolve_runtime)
    captured = {}

    def fake_check_connection(material, _state_path):
        captured.update(material)
        return {"state": "connection_verified"}

    monkeypatch.setattr(main_model_verification, "check_connection", fake_check_connection)

    result = model_config.check_main_model_connection()

    assert result["verification"]["state"] == "connection_verified"
    assert runtime_calls and runtime_calls[0][0] == "TEST_ONLY-zai-key"
    assert captured["base_url"] == "https://api.z.ai/api/paas/v4"
