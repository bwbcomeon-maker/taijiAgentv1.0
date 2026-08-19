def test_windows_candidate_requires_explicit_flag(monkeypatch):
    import taiji_runtime_profile as profile

    monkeypatch.setattr(profile.sys, "platform", "win32")
    monkeypatch.setattr(
        profile,
        "_read_profile",
        lambda: {"schema_version": profile.PROFILE_SCHEMA_VERSION, "profile": "source-development"},
    )
    monkeypatch.setattr(profile, "_is_trusted_source_checkout", lambda: False)
    monkeypatch.delenv("TAIJI_WINDOWS_CANDIDATE", raising=False)
    assert profile.installation_profile() == profile.INSTALLED_PRODUCTION_PROFILE

    monkeypatch.setenv("TAIJI_WINDOWS_CANDIDATE", "1")
    assert profile.installation_profile() == profile.WINDOWS_CANDIDATE_PROFILE


def test_windows_candidate_never_overrides_installed_production(monkeypatch):
    import taiji_runtime_profile as profile

    monkeypatch.setattr(profile.sys, "platform", "win32")
    monkeypatch.setenv("TAIJI_WINDOWS_CANDIDATE", "1")
    monkeypatch.setattr(
        profile,
        "_read_profile",
        lambda: {"schema_version": profile.PROFILE_SCHEMA_VERSION, "profile": profile.INSTALLED_PRODUCTION_PROFILE},
    )
    assert profile.installation_profile() == profile.INSTALLED_PRODUCTION_PROFILE


def test_linux_source_profile_behavior_is_unchanged(monkeypatch):
    import taiji_runtime_profile as profile

    monkeypatch.setattr(profile.sys, "platform", "linux")
    monkeypatch.setenv("TAIJI_WINDOWS_CANDIDATE", "1")
    monkeypatch.setattr(
        profile,
        "_read_profile",
        lambda: {"schema_version": profile.PROFILE_SCHEMA_VERSION, "profile": "source-development"},
    )
    monkeypatch.setattr(profile, "_is_trusted_source_checkout", lambda: True)
    assert profile.installation_profile() == "source-development"
