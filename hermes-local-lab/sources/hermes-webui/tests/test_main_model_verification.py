import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from api.main_model_verification import (
    check_connection,
    configuration_fingerprint,
    record_chat_result,
    verification_for_material,
)


class Response:
    def __init__(self, payload=b'{"data":[]}'):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


def material(**overrides):
    value = {
        "profile": "default",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://mock.provider/v1",
        "api_key": "FAKE-test-key",
        "auth_type": "api_key",
    }
    value.update(overrides)
    return value


def test_configured_model_is_not_reported_as_available_before_verification(tmp_path):
    result = verification_for_material(material(), tmp_path / "state.json", now=100)
    assert result["state"] == "configured_unverified"
    assert result["level"] == "configured"
    assert result["checked_at"] is None


def test_main_model_material_resolves_legacy_provider_key_from_profile_env(monkeypatch, tmp_path):
    from api import model_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text("model: {}\n", encoding="utf-8")
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=FAKE-profile-key\n", encoding="utf-8")
    config_path.chmod(0o600)
    (tmp_path / ".env").chmod(0o600)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        model_config,
        "_safe_model_cfg",
        lambda _data: {
            "provider": "deepseek",
            "default": "deepseek-chat",
            "base_url": "https://mock.provider/v1",
        },
    )
    assert model_config._PROVIDER_ENV_VAR["deepseek"] == "DEEPSEEK_API_KEY"
    assert model_config._provider_is_oauth("deepseek") is False
    assert model_config.resolve_secret_env_value(
        "DEEPSEEK_API_KEY",
        config_path=config_path,
        allow_process_fallback=False,
    ) == "FAKE-profile-key"

    result = model_config._main_model_material({})

    assert result["api_key"] == "FAKE-profile-key"
    assert verification_for_material(result, tmp_path / "state.json")["state"] == "configured_unverified"


def test_main_model_material_uses_key_bound_zai_cache_without_probe(monkeypatch, tmp_path):
    from api import model_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n  provider: zai\n  default: glm-5\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("GLM_API_KEY=profile-zai-key\n", encoding="utf-8")
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        "hermes_cli.auth._resolve_zai_status_endpoint",
        lambda *_args, **_kwargs: (
            "https://open.bigmodel.cn/api/coding/paas/v4",
            "runtime",
            "resolved",
        ),
    )
    monkeypatch.setattr(
        "hermes_cli.config.get_env_value",
        lambda _name: "",
    )

    result = model_config._main_model_material(
        {"model": {"provider": "zai", "default": "glm-5"}}
    )

    assert result["base_url"] == "https://open.bigmodel.cn/api/coding/paas/v4"


def test_main_model_material_does_not_show_zai_registry_default_without_cache(
    monkeypatch,
    tmp_path,
):
    from api import model_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text("model: {}\n", encoding="utf-8")
    (tmp_path / ".env").write_text("GLM_API_KEY=profile-zai-key\n", encoding="utf-8")
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        "hermes_cli.auth._resolve_zai_status_endpoint",
        lambda *_args, **_kwargs: ("", "runtime", "runtime_unresolved"),
    )
    monkeypatch.setattr("hermes_cli.config.get_env_value", lambda _name: "")

    result = model_config._main_model_material(
        {"model": {"provider": "zai", "default": "glm-5"}}
    )

    assert result["base_url"] == ""


def test_main_model_endpoint_consumes_sibling_candidate_view(monkeypatch):
    from api import model_config

    seen = {}

    def candidate_view(provider, **kwargs):
        seen.update(provider=provider, **kwargs)
        return {
            "provider": provider,
            "configured_url": "https://open.bigmodel.cn/api/paas/v4",
            "runtime_url": "",
            "candidate_source": "managed",
            "runtime_selector_unresolved": False,
        }

    monkeypatch.setattr(model_config, "_resolve_public_endpoint_candidate", candidate_view)
    endpoint = model_config._main_model_endpoint(
        {"model": {"provider": "zai-cn", "default": "glm-5"}},
        {"provider": "zai-cn", "model": "glm-5", "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key": ""},
    )

    assert seen["provider"] == "zai-cn"
    assert endpoint["display_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert endpoint["policy"] == "fixed"


@pytest.mark.parametrize(
    ("api_key", "status_result", "expected_url", "expected_status", "expected_source"),
    [
        (
            "profile-zai-key",
            ("https://open.bigmodel.cn/api/coding/paas/v4", "runtime", "resolved"),
            "https://open.bigmodel.cn/api/coding/paas/v4",
            "resolved",
            "runtime",
        ),
        ("profile-zai-key", ("", "runtime", "runtime_unresolved"), None, "runtime_unresolved", "runtime"),
        (
            "",
            ("https://api.z.ai/api/paas/v4", "managed", "resolved"),
            "https://api.z.ai/api/paas/v4",
            "resolved",
            "managed",
        ),
    ],
)
def test_main_model_endpoint_uses_zai_status_cache_without_network(
    monkeypatch,
    api_key,
    status_result,
    expected_url,
    expected_status,
    expected_source,
):
    from api import model_config

    monkeypatch.setattr(
        "hermes_cli.auth._resolve_zai_status_endpoint",
        lambda *_args, **_kwargs: status_result,
    )
    monkeypatch.setattr("hermes_cli.config.get_env_value", lambda _name: "")
    endpoint = model_config._main_model_endpoint(
        {"model": {"provider": "zai", "default": "glm-5"}},
        {"provider": "zai", "model": "glm-5", "base_url": status_result[0], "api_key": api_key},
    )

    assert endpoint["display_url"] == expected_url
    assert endpoint["status"] == expected_status
    assert endpoint["source"] == expected_source


def test_strict_connection_check_uses_models_get_and_never_completion(tmp_path):
    seen = []

    def opener(request, timeout):
        seen.append((request.full_url, request.method, timeout, dict(request.headers)))
        return Response()

    result = check_connection(material(), tmp_path / "state.json", opener=opener, now=100)

    assert result["state"] == "connection_verified"
    assert result["level"] == "connection"
    assert result["expires_at"] == 400
    assert seen[0][0] == "https://mock.provider/v1/models"
    assert seen[0][1] == "GET"
    assert seen[0][2] == 5
    assert "completion" not in seen[0][0]


def test_connection_check_does_not_contact_provider_without_required_api_key(tmp_path):
    seen = []

    def opener(*args, **kwargs):
        seen.append((args, kwargs))
        raise AssertionError("provider must not be contacted")

    result = check_connection(
        material(api_key=""),
        tmp_path / "state.json",
        opener=opener,
        now=100,
    )

    assert result["state"] == "unconfigured"
    assert result["code"] == "model_configuration_required"
    assert seen == []


def test_anthropic_base_url_does_not_duplicate_v1(tmp_path):
    seen = []

    def opener(request, timeout):
        seen.append((request.full_url, timeout))
        return Response()

    result = check_connection(
        material(provider="anthropic", base_url="https://api.anthropic.com/v1"),
        tmp_path / "state.json",
        opener=opener,
        now=100,
    )

    assert result["state"] == "connection_verified"
    assert seen == [("https://api.anthropic.com/v1/models", 5)]


def test_connection_check_classifies_auth_without_exposing_provider_body(tmp_path):
    secret = "FAKE-test-key"

    def opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            f"invalid {secret}",
            {},
            io.BytesIO(f'{{"error":"{secret}"}}'.encode()),
        )

    path = tmp_path / "state.json"
    result = check_connection(material(api_key=secret), path, opener=opener, now=100)

    assert result["state"] == "failed"
    assert result["code"] == "provider_authorization_failed"
    assert result["expires_at"] is None
    assert secret not in path.read_text(encoding="utf-8")


def test_transient_failure_expires_and_returns_to_unverified(tmp_path):
    path = tmp_path / "state.json"

    def opener(_request, timeout):
        assert timeout == 5
        raise TimeoutError("timeout")

    failed = check_connection(material(), path, opener=opener, now=100)
    assert failed["code"] == "provider_timeout"
    assert failed["expires_at"] == 160
    assert verification_for_material(material(), path, now=161)["state"] == "configured_unverified"


def test_unsupported_provider_is_neutral_not_failed(tmp_path):
    result = check_connection(
        material(provider="openai-codex", auth_type="oauth_external"),
        tmp_path / "state.json",
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no request")),
        now=100,
    )
    assert result["state"] == "unsupported"
    assert result["code"] == "connection_check_unsupported"


def test_real_chat_success_is_the_only_chat_verified_source(tmp_path):
    path = tmp_path / "state.json"
    check_connection(material(), path, opener=lambda *_args, **_kwargs: Response(), now=100)
    assert verification_for_material(material(), path, now=101)["level"] == "connection"

    record_chat_result(material(), path, success=True, now=110)

    result = verification_for_material(material(), path, now=111)
    assert result["state"] == "chat_verified"
    assert result["level"] == "chat"


def test_configuration_change_invalidates_old_success(tmp_path):
    path = tmp_path / "state.json"
    record_chat_result(material(), path, success=True, now=100)
    changed = material(model="deepseek-reasoner")
    assert verification_for_material(changed, path, now=101)["state"] == "configured_unverified"


def test_chat_verification_rejects_result_when_credentials_change_mid_turn(monkeypatch):
    from api import model_config

    started = material()
    changed = material(api_key="FAKE-replaced-key", base_url="https://replacement.provider/v1")
    recorded = []
    monkeypatch.setattr(model_config, "_get_config_path", lambda: SimpleNamespace())
    monkeypatch.setattr(model_config, "load_credential_config", lambda _path: {})
    monkeypatch.setattr(model_config, "_main_model_material", lambda _data: changed)
    monkeypatch.setattr(
        "api.main_model_verification.record_chat_result",
        lambda *_args, **_kwargs: recorded.append((_args, _kwargs)),
    )

    accepted = model_config.record_main_model_chat_verification(
        "deepseek",
        "deepseek-chat",
        expected_fingerprint=configuration_fingerprint(started),
        success=True,
    )

    assert accepted is False
    assert recorded == []


def test_small_post_router_handles_only_main_model_check(monkeypatch):
    from api import main_model_routes

    monkeypatch.setattr(
        "api.model_config.check_main_model_connection",
        lambda: {"ok": True, "verification": {"state": "configured_unverified"}},
    )
    monkeypatch.setattr(
        main_model_routes,
        "j",
        lambda _handler, payload, status=200: {**payload, "status": status},
    )

    assert main_model_routes.handle_main_model_post(
        SimpleNamespace(headers={}),
        SimpleNamespace(path="/api/model-config/main/check"),
    )["ok"] is True
    assert main_model_routes.handle_main_model_post(
        object(),
        SimpleNamespace(path="/api/other"),
    ) is False


def test_main_model_check_rejects_cross_origin_browser_post(monkeypatch):
    from api import main_model_routes

    monkeypatch.setattr(
        "api.model_config.check_main_model_connection",
        lambda: (_ for _ in ()).throw(AssertionError("probe must not run")),
    )
    monkeypatch.setattr(
        main_model_routes,
        "j",
        lambda _handler, payload, status=200: {"payload": payload, "status": status},
    )
    handler = SimpleNamespace(
        headers={
            "Origin": "https://attacker.example",
            "Host": "127.0.0.1:8787",
        }
    )

    result = main_model_routes.handle_main_model_post(
        handler,
        SimpleNamespace(path="/api/model-config/main/check"),
    )

    assert result["status"] == 403
    assert "Cross-origin" in result["payload"]["error"]
