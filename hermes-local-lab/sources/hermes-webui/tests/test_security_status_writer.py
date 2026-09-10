from __future__ import annotations

import json


def test_security_profile_uses_canonical_env_writer(
    monkeypatch,
    tmp_path,
) -> None:
    from agent import provider_credentials
    from api import security_status

    runtime_home = tmp_path / "runtime-home"
    calls = []

    def _record(updates, *, config_path=None, **_kwargs):
        calls.append((dict(updates), config_path, dict(_kwargs)))
        return {key: True for key in updates}

    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setattr(
        provider_credentials,
        "mutate_env_unique",
        _record,
    )

    result = security_status.set_security_profile("strict")

    assert result["ok"] is True
    assert result["pending_profile"] is None
    assert result["restart_required"] is False
    assert calls == [
        (
            {
                "TAIJI_SECURITY_PROFILE": "strict",
                "TAIJI_SECURITY_MODE": "restricted",
                "TAIJI_ALLOW_TERMINAL": "0",
                "TAIJI_ALLOW_EXECUTE_CODE": "0",
            },
            runtime_home / "config.yaml",
            {"project_process_env": False},
        )
    ]


def test_local_controlled_enables_terminal_and_code_without_broadening_other_capabilities(
    monkeypatch,
    tmp_path,
) -> None:
    from agent import provider_credentials
    from api import security_status

    calls = []
    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.setenv("TAIJI_ALLOW_DELEGATE_TASK", "0")
    monkeypatch.setenv("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS", "0")
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("TAIJI_SECURITY_PROFILE", "strict")
    monkeypatch.setenv("TAIJI_ALLOW_TERMINAL", "0")
    monkeypatch.setenv("TAIJI_ALLOW_EXECUTE_CODE", "0")
    monkeypatch.setattr(
        provider_credentials,
        "mutate_env_unique",
        lambda updates, **_kwargs: calls.append(dict(updates)),
    )

    result = security_status.set_security_profile("local_controlled")

    assert calls == [{
        "TAIJI_SECURITY_PROFILE": "local_controlled",
        "TAIJI_SECURITY_MODE": "restricted",
        "TAIJI_ALLOW_TERMINAL": "1",
        "TAIJI_ALLOW_EXECUTE_CODE": "1",
    }]
    assert result["pending_profile"] == "local_controlled"
    assert result["status"]["profile"] == "strict"
    assert result["status"]["capabilities"]["terminal"]["allowed"] is False
    assert result["status"]["capabilities"]["execute_code"]["allowed"] is False
    assert result["status"]["capabilities"]["delegate_task"]["allowed"] is False
    assert result["status"]["capabilities"]["unapproved_skill_scripts"]["allowed"] is False


def test_saving_strict_from_local_controlled_keeps_current_process_truthful(
    monkeypatch,
    tmp_path,
) -> None:
    from agent import provider_credentials
    from api import security_status

    calls = []
    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("TAIJI_SECURITY_PROFILE", "local_controlled")
    monkeypatch.setenv("TAIJI_ALLOW_TERMINAL", "1")
    monkeypatch.setenv("TAIJI_ALLOW_EXECUTE_CODE", "1")
    monkeypatch.setenv("TAIJI_ALLOW_DELEGATE_TASK", "0")
    monkeypatch.setenv("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS", "0")
    monkeypatch.setattr(
        provider_credentials,
        "mutate_env_unique",
        lambda updates, **_kwargs: calls.append(dict(updates)),
    )

    result = security_status.set_security_profile("strict")

    assert calls == [{
        "TAIJI_SECURITY_PROFILE": "strict",
        "TAIJI_SECURITY_MODE": "restricted",
        "TAIJI_ALLOW_TERMINAL": "0",
        "TAIJI_ALLOW_EXECUTE_CODE": "0",
    }]
    assert result["pending_profile"] == "strict"
    assert result["status"]["profile"] == "local_controlled"
    assert result["status"]["capabilities"]["terminal"]["allowed"] is True
    assert result["status"]["capabilities"]["execute_code"]["allowed"] is True
    assert result["status"]["capabilities"]["delegate_task"]["allowed"] is False
    assert result["status"]["capabilities"]["unapproved_skill_scripts"]["allowed"] is False
    assert security_status.os.environ["TAIJI_SECURITY_PROFILE"] == "local_controlled"
    assert security_status.os.environ["TAIJI_ALLOW_TERMINAL"] == "1"
    assert security_status.os.environ["TAIJI_ALLOW_EXECUTE_CODE"] == "1"


def test_real_security_writer_persists_without_projecting_into_current_process(
    monkeypatch,
    tmp_path,
) -> None:
    from api import security_status

    runtime_home = tmp_path / "runtime-home"
    runtime_home.mkdir(mode=0o700)
    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("TAIJI_SECURITY_PROFILE", "strict")
    monkeypatch.setenv("TAIJI_ALLOW_TERMINAL", "0")
    monkeypatch.setenv("TAIJI_ALLOW_EXECUTE_CODE", "0")
    monkeypatch.setenv("TAIJI_ALLOW_DELEGATE_TASK", "0")
    monkeypatch.setenv("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS", "0")

    result = security_status.set_security_profile("local_controlled")

    persisted = (runtime_home / ".env").read_text(encoding="utf-8")
    assert "TAIJI_SECURITY_PROFILE=local_controlled\n" in persisted
    assert "TAIJI_ALLOW_TERMINAL=1\n" in persisted
    assert "TAIJI_ALLOW_EXECUTE_CODE=1\n" in persisted
    assert result["pending_profile"] == "local_controlled"
    assert result["status"]["profile"] == "strict"
    assert result["status"]["capabilities"]["terminal"]["allowed"] is False
    assert result["status"]["capabilities"]["execute_code"]["allowed"] is False
    assert result["status"]["pending_profile"] == "local_controlled"
    assert result["status"]["restart_required"] is True
    assert security_status.os.environ["TAIJI_SECURITY_PROFILE"] == "strict"
    assert security_status.os.environ["TAIJI_ALLOW_TERMINAL"] == "0"
    assert security_status.os.environ["TAIJI_ALLOW_EXECUTE_CODE"] == "0"

    refreshed = security_status.build_security_status_payload()
    assert refreshed["profile"] == "strict"
    assert refreshed["pending_profile"] == "local_controlled"
    assert refreshed["restart_required"] is True


def test_windows_security_profile_uses_dedicated_restart_store(
    monkeypatch,
    tmp_path,
) -> None:
    from agent import provider_credentials
    from api import security_status

    runtime_home = tmp_path / "runtime-home"
    runtime_home.mkdir(mode=0o700)
    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_WINDOWS_CANDIDATE", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("TAIJI_SECURITY_PROFILE", "strict")
    monkeypatch.setenv("TAIJI_ALLOW_TERMINAL", "0")
    monkeypatch.setenv("TAIJI_ALLOW_EXECUTE_CODE", "0")
    monkeypatch.setenv("TAIJI_ALLOW_DELEGATE_TASK", "0")
    monkeypatch.setenv("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS", "0")
    monkeypatch.setattr(
        provider_credentials,
        "mutate_env_unique",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Windows security settings must not use the credential store")
        ),
    )

    result = security_status.set_security_profile(
        "local_controlled",
        capabilities={
            "unapproved_skill_scripts": True,
            "delegate_task": False,
        },
    )

    saved = json.loads(
        (runtime_home / "security-settings.json").read_text(encoding="utf-8")
    )
    assert saved == {
        "schema": "taiji-security-settings/v1",
        "profile": "local_controlled",
        "capabilities": {
            "unapproved_skill_scripts": True,
            "delegate_task": False,
        },
    }
    assert not (runtime_home / ".env").exists()
    assert result["pending_profile"] == "local_controlled"
    assert result["restart_required"] is True
    assert result["status"]["configured"] == {
        "profile": "local_controlled",
        "capabilities": {
            "unapproved_skill_scripts": True,
            "delegate_task": False,
        },
    }


def test_windows_profile_only_save_preserves_extension_choices(
    monkeypatch,
    tmp_path,
) -> None:
    from api import security_status

    runtime_home = tmp_path / "runtime-home"
    runtime_home.mkdir(mode=0o700)
    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_WINDOWS_CANDIDATE", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("TAIJI_SECURITY_PROFILE", "strict")
    monkeypatch.setenv("TAIJI_ALLOW_TERMINAL", "0")
    monkeypatch.setenv("TAIJI_ALLOW_EXECUTE_CODE", "0")
    monkeypatch.setenv("TAIJI_ALLOW_DELEGATE_TASK", "0")
    monkeypatch.setenv("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS", "0")

    security_status.set_security_profile(
        "strict",
        capabilities={
            "unapproved_skill_scripts": True,
            "delegate_task": True,
        },
    )
    security_status.set_security_profile("local_controlled")

    saved = json.loads(
        (runtime_home / "security-settings.json").read_text(encoding="utf-8")
    )
    assert saved["profile"] == "local_controlled"
    assert saved["capabilities"] == {
        "unapproved_skill_scripts": True,
        "delegate_task": True,
    }


def test_windows_profile_only_save_fails_closed_after_invalid_store(
    monkeypatch,
    tmp_path,
) -> None:
    from api import security_status

    runtime_home = tmp_path / "runtime-home"
    runtime_home.mkdir(mode=0o700)
    (runtime_home / "security-settings.json").write_text(
        '{"schema":"invalid"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_WINDOWS_CANDIDATE", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("TAIJI_SECURITY_PROFILE", "local_controlled")
    monkeypatch.setenv("TAIJI_ALLOW_TERMINAL", "1")
    monkeypatch.setenv("TAIJI_ALLOW_EXECUTE_CODE", "1")
    monkeypatch.setenv("TAIJI_ALLOW_DELEGATE_TASK", "1")
    monkeypatch.setenv("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS", "1")

    status = security_status.build_security_status_payload()
    assert status["configured"] == {
        "profile": "strict",
        "capabilities": {
            "unapproved_skill_scripts": False,
            "delegate_task": False,
        },
    }
    assert status["pending_profile"] == "strict"
    assert status["restart_required"] is True

    security_status.set_security_profile("strict")

    saved = json.loads(
        (runtime_home / "security-settings.json").read_text(encoding="utf-8")
    )
    assert saved["profile"] == "strict"
    assert saved["capabilities"] == {
        "unapproved_skill_scripts": False,
        "delegate_task": False,
    }


def test_windows_security_replace_failure_preserves_previous_store(
    monkeypatch,
    tmp_path,
) -> None:
    import pytest
    from api import security_status

    runtime_home = tmp_path / "runtime-home"
    runtime_home.mkdir(mode=0o700)
    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_WINDOWS_CANDIDATE", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("TAIJI_SECURITY_PROFILE", "strict")
    monkeypatch.setenv("TAIJI_ALLOW_TERMINAL", "0")
    monkeypatch.setenv("TAIJI_ALLOW_EXECUTE_CODE", "0")
    monkeypatch.setenv("TAIJI_ALLOW_DELEGATE_TASK", "0")
    monkeypatch.setenv("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS", "0")
    security_status.set_security_profile("strict")
    settings_path = runtime_home / "security-settings.json"
    before = settings_path.read_bytes()

    def fail_replace(_source, _target):
        raise PermissionError("test replacement failure")

    monkeypatch.setattr(security_status.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="test replacement failure"):
        security_status.set_security_profile("local_controlled")

    assert settings_path.read_bytes() == before
    assert not list(runtime_home.glob(".security-settings.json.*.tmp"))
