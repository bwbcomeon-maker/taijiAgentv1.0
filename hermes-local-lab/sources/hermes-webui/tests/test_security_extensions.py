"""Independent opt-in permissions must survive save/restart without opening the base policy."""
import pytest


@pytest.fixture
def security(monkeypatch, tmp_path):
    from api import security_status

    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(tmp_path))
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("TAIJI_SECURITY_PROFILE", "strict")
    for name in security_status._CONTROLLED_ALLOW_VARS.values():
        monkeypatch.setenv(name, "0")
    return security_status


@pytest.mark.parametrize("profile", ["strict", "local_controlled"])
@pytest.mark.parametrize("scripts,delegate", [(False, False), (True, False), (False, True), (True, True)])
def test_save_restart_matrix(security, monkeypatch, profile, scripts, delegate):
    from tools.taiji_security_mode import security_profile

    choices = {"unapproved_skill_scripts": scripts, "delegate_task": delegate}
    result = security.set_security_profile(profile, capabilities=choices)
    pending = profile != "strict" or scripts or delegate
    assert result["restart_required"] is pending
    assert result["status"]["profile"] == "strict"
    assert result["status"]["capabilities"]["delegate_task"]["allowed"] is False
    assert result["status"]["configured"] == {"profile": profile, "capabilities": choices}
    assert security.os.environ["TAIJI_ALLOW_DELEGATE_TASK"] == "0"
    for line in security._env_file().read_text().splitlines():
        key, value = line.split("=", 1)
        monkeypatch.setenv(key, value)
    status = security.build_security_status_payload()
    assert status["restart_required"] is False
    assert status["profile"] == security_profile() == profile
    assert status["capabilities"]["terminal"]["allowed"] is (profile == "local_controlled")
    assert status["capabilities"]["execute_code"]["allowed"] is (profile == "local_controlled")
    assert status["capabilities"]["delegate_task"]["allowed"] is delegate
    assert status["capabilities"]["unapproved_skill_scripts"]["allowed"] is scripts


def test_profile_only_and_partial_updates_preserve_persisted_choices(security):
    security._env_file().write_text("UNRELATED_TOKEN=test-only\n")
    security.set_security_profile("strict", capabilities={"delegate_task": True})
    security.set_security_profile("local_controlled")
    result = security.set_security_profile("strict", capabilities={"unapproved_skill_scripts": True})
    assert result["status"]["configured"]["capabilities"] == {
        "delegate_task": True, "unapproved_skill_scripts": True,
    }
    assert "UNRELATED_TOKEN=test-only" in security._env_file().read_text()


@pytest.mark.parametrize("choices", [[], "yes", {"terminal": True}, {"delegate_task": 1}, {"delegate_task": "false"}, {"delegate_task": None}])
def test_invalid_capabilities_do_not_write(security, choices):
    with pytest.raises(ValueError):
        security.set_security_profile("strict", capabilities=choices)
    assert not security._env_file().exists()


def test_capability_revert_clears_pending_and_failure_preserves_store(security, monkeypatch):
    security.set_security_profile("strict", capabilities={"delegate_task": True})
    assert security.build_security_status_payload()["restart_required"] is True
    result = security.set_security_profile("strict", capabilities={"delegate_task": False})
    assert result["restart_required"] is False
    before = security._env_file().read_bytes()
    def fail(_values):
        raise OSError("test disk failure")
    monkeypatch.setattr(security, "_write_env", fail)
    with pytest.raises(OSError):
        security.set_security_profile("strict", capabilities={"delegate_task": True})
    assert security._env_file().read_bytes() == before


@pytest.mark.parametrize("body,code", [
    ({"profile": "strict", "capabilities": {"delegate_task": True}}, 200),
    ({"profile": "strict", "capabilities": None}, 400),
    ({"profile": "strict", "capabilities": {"delegate_task": "true"}}, 400),
    ({"profile": "strict", "unknown": True}, 400),
])
def test_security_route_validates_and_forwards_choices(security, monkeypatch, body, code):
    from types import SimpleNamespace
    from api import routes
    responses = []
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: body)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200: responses.append((payload, status)) or True)
    monkeypatch.setattr(routes, "bad", lambda _handler, message, status=400: responses.append(({"error": message}, status)) or True)
    assert routes.handle_post(SimpleNamespace(), SimpleNamespace(path="/api/security/profile")) is True
    assert responses[0][1] == code
    if code == 200:
        assert responses[0][0]["status"]["configured"]["capabilities"]["delegate_task"] is True
    else:
        assert not security._env_file().exists()


@pytest.mark.parametrize("desktop,mode", [("0", "restricted"), ("1", "full")])
def test_readonly_rejects_backend_write(security, monkeypatch, desktop, mode):
    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", desktop)
    monkeypatch.setenv("TAIJI_SECURITY_MODE", mode)
    with pytest.raises(PermissionError):
        security.set_security_profile("strict", capabilities={"delegate_task": True})
    assert not security._env_file().exists()
