from __future__ import annotations


def test_security_profile_uses_canonical_env_writer(
    monkeypatch,
    tmp_path,
) -> None:
    from agent import provider_credentials
    from api import security_status

    runtime_home = tmp_path / "runtime-home"
    calls = []

    def _record(updates, *, config_path=None, **_kwargs):
        calls.append((dict(updates), config_path))
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
    assert calls == [
        (
            {
                "TAIJI_SECURITY_PROFILE": "strict",
                "TAIJI_SECURITY_MODE": "restricted",
                "TAIJI_ALLOW_TERMINAL": "0",
                "TAIJI_ALLOW_EXECUTE_CODE": "0",
                "TAIJI_ALLOW_DELEGATE_TASK": "0",
                "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS": "0",
            },
            runtime_home / "config.yaml",
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
        "TAIJI_ALLOW_DELEGATE_TASK": "0",
        "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS": "0",
    }]
    assert result["status"]["profile"] == "local_controlled"
    assert result["status"]["capabilities"]["delegate_task"]["allowed"] is False
    assert result["status"]["capabilities"]["unapproved_skill_scripts"]["allowed"] is False
