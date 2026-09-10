"""Windows configuration API persistence, without Provider network access."""
import json
from types import SimpleNamespace

import pytest

from agent import provider_credentials as store
from api import config, model_config, providers, routes


@pytest.fixture
def profile(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(store, "_credential_platform_name", lambda: "win32")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(path))
    monkeypatch.setattr(config, "_get_config_path", lambda: path)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: path)
    monkeypatch.setattr(providers, "_get_config_path", lambda: path)
    monkeypatch.setattr(model_config, "_invoke_durable_mutation_post_commit", lambda *_: [])
    return path


def test_provider_and_main_save_keep_receipt_and_secret_private(profile):
    secret = "TEST_ONLY_windows_provider_key"
    assert providers.set_provider_key("zai-cn", secret)["ok"]
    request_id = "a" * 32
    response = model_config.set_main_model_config({
        "provider": "zai-cn", "model": "glm-test", "request_id": request_id,
    })
    assert response["main_request_id"] == request_id
    assert response["main"]["key_status"]["configured"]
    assert secret not in json.dumps(response)
    assert store.load_credential_snapshot(profile).env["GLM_CN_API_KEY"] == secret


@pytest.mark.parametrize("exception_type", [store.WindowsCredentialStorageError, store.WindowsCredentialRecoveryError])
def test_storage_error_is_actionable_not_uncertain_500(monkeypatch, exception_type):
    responses = []
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200: responses.append((payload, status)))
    exc = exception_type("本机凭据保存失败")
    routes._configuration_mutation_error_response(object(), exc)
    assert responses == [({"error": str(exc), "error_code": exc.code}, 409)]


def test_provider_route_preserves_storage_error_code(monkeypatch):
    responses = []
    monkeypatch.setattr(routes, "_check_csrf", lambda *_: True)
    monkeypatch.setattr(routes, "read_body", lambda *_: {"provider": "zai-cn", "api_key": "synthetic-key"})
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200: responses.append((payload, status)) or True)
    def fail(*_):
        raise store.WindowsCredentialRecoveryError("本机凭据需要恢复")
    monkeypatch.setattr(routes, "set_provider_key", fail)
    routes.handle_post(object(), SimpleNamespace(path="/api/providers"))
    assert responses == [({"error": "本机凭据需要恢复", "error_code": "credential_recovery_required"}, 409)]
