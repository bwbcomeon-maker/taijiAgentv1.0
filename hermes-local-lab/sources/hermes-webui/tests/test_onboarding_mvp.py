"""Onboarding MVP tests — first-run wizard and provider config persistence.

Tests that call /api/onboarding/setup require PyYAML in the test server's
Python environment (the agent venv). They are skipped when hermes-agent is
not installed, since the server falls back to system Python which typically
lacks pyyaml.
"""
import importlib.util
import json
import pathlib
import urllib.error
import urllib.request
from unittest import mock

import pytest

from tests._pytest_port import BASE, TEST_STATE_DIR

# Check if pyyaml is available — onboarding setup tests need it on the server
_HAS_YAML = importlib.util.find_spec("yaml") is not None
_needs_yaml = pytest.mark.skipif(not _HAS_YAML, reason="PyYAML not installed — onboarding setup tests require it")


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def post(path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def _server_hermes_home() -> pathlib.Path:
    """Return the same isolated state directory used by the test server."""
    return TEST_STATE_DIR


@pytest.fixture(autouse=True)
def clean_hermes_config_files():
    hermes_home = _server_hermes_home()
    for rel in ("config.yaml", ".env"):
        (hermes_home / rel).unlink(missing_ok=True)
    # onboarding_completed lives in settings.json (not config.yaml/.env), so the
    # unlinks above don't reset it. A prior test that completes onboarding would
    # otherwise leak the flag and make tests asserting the pristine "incomplete"
    # default fail under sharded/reordered runs. Reset it via the settings API
    # (the source of truth the server reads) before AND after each test.
    post("/api/settings", {"onboarding_completed": False})
    yield
    for rel in ("config.yaml", ".env"):
        (hermes_home / rel).unlink(missing_ok=True)
    post("/api/settings", {"onboarding_completed": False})



def test_onboarding_status_defaults_incomplete():
    data, status = get("/api/onboarding/status")
    assert status == 200
    assert data["completed"] is False
    assert data["settings"]["password_enabled"] is False
    assert data["system"]["provider_configured"] is False
    assert data["system"]["chat_ready"] is False
    assert data["system"]["setup_state"] in {"needs_provider", "agent_unavailable"}
    assert "provider_note" in data["system"]
    assert isinstance(data["workspaces"]["items"], list)
    assert data["setup"]["providers"]


def test_setup_status_is_read_only_and_has_stable_four_item_contract():
    hermes_home = _server_hermes_home()
    tracked = [hermes_home / "config.yaml", hermes_home / ".env", hermes_home / "settings.json"]

    def snapshot():
        return {
            str(path): (path.exists(), path.read_bytes() if path.exists() else None)
            for path in tracked
        }

    before = snapshot()
    first, first_status = get("/api/setup/status")
    second, second_status = get("/api/setup/status")

    assert first_status == second_status == 200
    assert first["schema_version"] == "taiji-setup-status/v1"
    assert [item["id"] for item in first["items"]] == [
        "license",
        "model",
        "workspace",
        "security",
    ]
    assert all(item["status"] in {"ready", "action_required", "unavailable"} for item in first["items"])
    assert all(isinstance(item["ready"], bool) for item in first["items"])
    assert all(item.get("reason") for item in first["items"])
    assert all(item.get("recovery", {}).get("id") for item in first["items"])
    assert first["overall_ready"] is False
    assert second["items"] == first["items"]
    serialized = json.dumps(first, ensure_ascii=False)
    assert "OPENROUTER_API_KEY" not in serialized
    assert "existing-secret" not in serialized
    assert snapshot() == before


@_needs_yaml
def test_onboarding_setup_openrouter_writes_real_config_and_env():
    data, status = post(
        "/api/onboarding/setup",
        {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "api_key": "sk-or-test",
        },
    )
    assert status == 200
    assert data["system"]["provider_configured"] is True
    assert data["system"]["provider_ready"] is True
    if data["system"]["imports_ok"] and data["system"]["hermes_found"]:
        assert data["system"]["chat_ready"] is True
        assert data["system"]["setup_state"] == "ready"
    else:
        assert data["system"]["chat_ready"] is False
        assert data["system"]["setup_state"] == "agent_unavailable"

    cfg_text = (_server_hermes_home() / "config.yaml").read_text(encoding="utf-8")
    env_text = (_server_hermes_home() / ".env").read_text(encoding="utf-8")
    assert "provider: openrouter" in cfg_text
    assert "default: anthropic/claude-sonnet-4.6" in cfg_text
    assert "OPENROUTER_API_KEY=sk-or-test" in env_text


@_needs_yaml
def test_onboarding_setup_custom_endpoint_writes_runtime_files():
    data, status = post(
        "/api/onboarding/setup",
        {
            "provider": "custom",
            "model": "google/gemma-3-27b-it",
            "base_url": "http://localhost:4000/v1",
            "api_key": "sk-custom-test",
        },
    )
    assert status == 200
    assert data["system"]["provider_configured"] is True
    assert data["system"]["provider_ready"] is True
    if data["system"]["imports_ok"] and data["system"]["hermes_found"]:
        assert data["system"]["chat_ready"] is True
        assert data["system"]["setup_state"] == "ready"
    else:
        assert data["system"]["chat_ready"] is False
        assert data["system"]["setup_state"] == "agent_unavailable"
    assert data["system"]["current_provider"] == "custom"
    assert data["setup"]["current"]["base_url"] == "http://localhost:4000/v1"

    cfg_text = (_server_hermes_home() / "config.yaml").read_text(encoding="utf-8")
    env_text = (_server_hermes_home() / ".env").read_text(encoding="utf-8")
    assert "provider: custom" in cfg_text
    assert "default: google/gemma-3-27b-it" in cfg_text
    assert "base_url: http://localhost:4000/v1" in cfg_text
    assert "OPENAI_API_KEY=sk-custom-test" in env_text


@_needs_yaml
def test_onboarding_setup_detects_incomplete_saved_provider():
    status, code = post(
        "/api/onboarding/setup",
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4.6",
            "api_key": "sk-ant-test",
        },
    )
    assert code == 200

    (_server_hermes_home() / ".env").unlink(missing_ok=True)
    data, status_code = get("/api/onboarding/status")
    assert status_code == 200
    assert data["system"]["provider_configured"] is True
    assert data["system"]["provider_ready"] is False
    assert data["system"]["chat_ready"] is False
    assert data["system"]["setup_state"] in {"provider_incomplete", "agent_unavailable"}


@_needs_yaml
def test_onboarding_setup_rejects_missing_custom_base_url():
    data, status = post(
        "/api/onboarding/setup",
        {
            "provider": "custom",
            "model": "qwen2.5-coder",
            "api_key": "sk-test",
        },
    )
    assert status == 400
    assert "base_url is required" in data["error"]


def test_onboarding_complete_rejects_not_ready_without_persisting_flag():
    data, status = post("/api/onboarding/complete", {})
    assert status == 409
    assert data["error"] == "setup_not_ready"
    assert data["error_code"] == "setup_not_ready"
    assert data["preflight"]["overall_ready"] is False

    settings = json.loads(
        (_server_hermes_home() / "settings.json").read_text(encoding="utf-8")
    )
    assert settings["onboarding_completed"] is False

    data2, status2 = get("/api/onboarding/status")
    assert status2 == 200
    assert data2["completed"] is False


def test_complete_helper_never_writes_when_preflight_is_blocked(monkeypatch):
    import api.onboarding as onboarding

    begin = mock.Mock()
    license_item = {
        "id": "license",
        "label": "授权",
        "ready": False,
        "status": "action_required",
        "reason": "未安装有效授权",
        "code": "license_missing",
        "recovery": {"id": "open_license", "label": "导入授权"},
    }
    blocked = {
        "schema_version": "taiji-setup-status/v1",
        "installed_production": True,
        "overall_ready": False,
        "items": [license_item],
    }
    monkeypatch.setattr(
        onboarding,
        "get_setup_status",
        lambda: blocked,
    )
    monkeypatch.setattr(onboarding, "_begin_onboarding_completion", begin, raising=False)

    result = onboarding.complete_onboarding()

    assert result["error"] == "setup_not_ready"
    assert result["preflight"] == blocked
    assert result["preflight"]["items"][0]["code"] == "license_missing"
    begin.assert_not_called()


def test_complete_helper_rechecks_immediately_before_persisting(monkeypatch):
    import api.onboarding as onboarding

    ready = {
        "schema_version": "taiji-setup-status/v1",
        "installed_production": True,
        "overall_ready": True,
        "items": [],
    }
    blocked = {
        "schema_version": "taiji-setup-status/v1",
        "installed_production": True,
        "overall_ready": False,
        "items": [],
    }
    begin = mock.Mock()
    monkeypatch.setattr(onboarding, "get_setup_status", mock.Mock(side_effect=[ready, blocked]))
    monkeypatch.setattr(onboarding, "_begin_onboarding_completion", begin, raising=False)

    result = onboarding.complete_onboarding()

    assert result["error"] == "setup_not_ready"
    assert result["preflight"] == blocked
    begin.assert_not_called()


def test_complete_helper_rolls_back_if_post_write_status_is_not_ready(monkeypatch):
    import api.onboarding as onboarding

    ready = {
        "schema_version": "taiji-setup-status/v1",
        "installed_production": True,
        "overall_ready": True,
        "items": [],
    }
    blocked = {
        "schema_version": "taiji-setup-status/v1",
        "installed_production": True,
        "overall_ready": False,
        "items": [],
    }
    marker = object()
    begin = mock.Mock(return_value=marker)
    commit = mock.Mock()
    rollback = mock.Mock(return_value=True)
    monkeypatch.setattr(onboarding, "get_setup_status", mock.Mock(side_effect=[ready, ready]))
    monkeypatch.setattr(onboarding, "_begin_onboarding_completion", begin, raising=False)
    monkeypatch.setattr(onboarding, "_commit_onboarding_completion", commit, raising=False)
    monkeypatch.setattr(onboarding, "_rollback_onboarding_completion", rollback, raising=False)
    status = mock.Mock(return_value={"completed": False, "preflight": blocked})
    monkeypatch.setattr(onboarding, "get_onboarding_status", status)

    result = onboarding.complete_onboarding()

    assert result["error"] == "setup_not_ready"
    assert result["preflight"] == blocked
    begin.assert_called_once_with()
    rollback.assert_called_once_with(marker)
    commit.assert_not_called()
    status.assert_called_once_with(allow_config_auto_complete=False)


def test_complete_helper_rolls_back_if_post_write_projection_raises(monkeypatch):
    import api.onboarding as onboarding

    ready = {
        "schema_version": "taiji-setup-status/v1",
        "installed_production": True,
        "overall_ready": True,
        "items": [],
    }
    marker = object()
    begin = mock.Mock(return_value=marker)
    commit = mock.Mock()
    rollback = mock.Mock(return_value=True)
    monkeypatch.setattr(onboarding, "get_setup_status", mock.Mock(side_effect=[ready, ready]))
    monkeypatch.setattr(onboarding, "_begin_onboarding_completion", begin, raising=False)
    monkeypatch.setattr(onboarding, "_commit_onboarding_completion", commit, raising=False)
    monkeypatch.setattr(onboarding, "_rollback_onboarding_completion", rollback, raising=False)
    status = mock.Mock(side_effect=RuntimeError("projection failed"))
    monkeypatch.setattr(onboarding, "get_onboarding_status", status)

    with pytest.raises(RuntimeError, match="projection failed"):
        onboarding.complete_onboarding()

    begin.assert_called_once_with()
    rollback.assert_called_once_with(marker)
    commit.assert_not_called()
    status.assert_called_once_with(allow_config_auto_complete=False)


def test_unsupported_provider_setup_cannot_persist_completion(monkeypatch):
    import api.onboarding as onboarding

    save = mock.Mock()
    status = mock.Mock(return_value={"completed": False})
    monkeypatch.setattr(onboarding, "save_settings", save)
    monkeypatch.setattr(onboarding, "get_onboarding_status", status)

    result = onboarding.apply_onboarding_setup(
        {"provider": "openai-codex", "model": "gpt-5.5"}
    )

    assert result == {"completed": False}
    save.assert_not_called()
    status.assert_called_once_with(allow_config_auto_complete=False)


def test_source_skip_override_setup_cannot_persist_completion(monkeypatch):
    import api.onboarding as onboarding

    save = mock.Mock()
    status = mock.Mock(return_value={"completed": True})
    monkeypatch.setenv("HERMES_WEBUI_SKIP_ONBOARDING", "1")
    monkeypatch.setattr(onboarding, "save_settings", save)
    monkeypatch.setattr(onboarding, "get_onboarding_status", status)

    result = onboarding.apply_onboarding_setup(
        {"provider": "openrouter", "model": "model", "api_key": "secret"}
    )

    assert result == {"completed": True}
    save.assert_not_called()
    status.assert_called_once_with(allow_config_auto_complete=False)


def test_installed_preflight_fails_closed_unless_security_is_restricted_strict(monkeypatch, tmp_path):
    import api.onboarding as onboarding

    ready_license = onboarding._setup_item(
        "license",
        "授权",
        ready=True,
        reason="ready",
        recovery={"id": "open_license", "label": "open"},
    )
    runtime = {
        "chat_ready": True,
        "provider_note": "ready",
        "current_provider": "openrouter",
        "current_model": "model",
    }
    monkeypatch.setattr(onboarding, "_license_setup_item", lambda: (ready_license, True))
    monkeypatch.setattr(onboarding, "get_config", lambda: {})
    monkeypatch.setattr(onboarding, "verify_hermes_imports", lambda: (True, [], {}))
    monkeypatch.setattr(onboarding, "_status_from_runtime", lambda _cfg, _imports_ok: runtime)
    monkeypatch.setattr(onboarding, "load_settings", lambda: {"default_workspace": str(tmp_path)})
    monkeypatch.setattr(onboarding, "validate_workspace_to_add", lambda _path: tmp_path)
    monkeypatch.setattr(
        onboarding,
        "build_security_status_payload",
        lambda: {"mode": "restricted", "profile": "local_controlled"},
    )

    blocked = onboarding.get_setup_status()
    assert blocked["overall_ready"] is False
    assert next(item for item in blocked["items"] if item["id"] == "security")["ready"] is False

    monkeypatch.setattr(
        onboarding,
        "build_security_status_payload",
        lambda: {"mode": "restricted", "profile": "strict"},
    )
    ready = onboarding.get_setup_status()
    assert ready["overall_ready"] is True


def test_license_setup_is_ready_only_for_required_valid_status(monkeypatch):
    import api.onboarding as onboarding
    import api.product_diagnostics as product_diagnostics

    class Status:
        def __init__(self, payload):
            self.payload = payload

        def to_public_dict(self):
            return dict(self.payload)

    class Profile:
        @staticmethod
        def is_installed_production():
            return False

    class Module:
        taiji_runtime_profile = Profile()

        def __init__(self, payload):
            self.payload = payload

        def load_license_status(self):
            return Status(self.payload)

    cases = [
        ({"status": "valid", "required": True}, True),
        ({"status": "valid", "required": False}, False),
        ({"status": "not_required", "required": False}, False),
        ({"status": "missing", "required": True}, False),
        ({"status": "expired", "required": True}, False),
        ({"status": "invalid", "required": True}, False),
        ({"status": "unknown", "required": True}, False),
    ]
    for payload, expected_ready in cases:
        monkeypatch.setattr(
            product_diagnostics,
            "_license_module",
            lambda payload=payload: Module(payload),
        )
        item, installed = onboarding._license_setup_item()
        assert item["ready"] is expected_ready
        assert item["status"] == ("ready" if expected_ready else "action_required")
        assert installed is False


def test_license_setup_exception_fails_closed_without_secret_details(monkeypatch):
    import api.onboarding as onboarding
    import api.product_diagnostics as product_diagnostics

    monkeypatch.setattr(
        product_diagnostics,
        "_license_module",
        lambda: (_ for _ in ()).throw(RuntimeError("secret-token /Users/private")),
    )
    item, _installed = onboarding._license_setup_item()
    assert item["ready"] is False
    assert item["status"] == "unavailable"
    assert "secret-token" not in item["reason"]
    assert "/Users/private" not in item["reason"]


@_needs_yaml
def test_onboarding_setup_rejects_api_key_with_newline():
    """API keys containing embedded newlines must be rejected to prevent .env injection."""
    injected_key = "sk-bad" + chr(10) + "OTHER_KEY=injected"
    data, status = post(
        "/api/onboarding/setup",
        {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "api_key": injected_key,
        },
    )
    assert status == 400
    assert "newline" in data["error"].lower()


@_needs_yaml
def test_onboarding_setup_rejects_api_key_control_chars_before_config_exists_guard():
    """Credential validation must win even when an existing config would early-return."""
    config_path = _server_hermes_home() / "config.yaml"
    original = "model:\n  provider: deepseek\n"
    config_path.write_text(original, encoding="utf-8")

    data, status = post(
        "/api/onboarding/setup",
        {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "api_key": "sk-bad\x00OTHER_KEY=injected",
        },
    )

    assert status == 400
    assert "nul" in data["error"].lower()
    assert config_path.read_text(encoding="utf-8") == original
