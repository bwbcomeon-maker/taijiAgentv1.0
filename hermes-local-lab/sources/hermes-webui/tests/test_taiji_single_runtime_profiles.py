"""Taiji product-mode profile behavior.

Taiji packages expose one public runtime home. Legacy profile labels may remain
as historical metadata, but they must not hide records or allow runtime-home
switching through WebUI APIs.
"""

from __future__ import annotations

import pytest


def test_active_profile_is_forced_to_default_in_taiji_runtime(monkeypatch):
    import api.profiles as profiles

    monkeypatch.setenv("TAIJI_RUNTIME_HOME", "/tmp/taiji-runtime-active-profile")
    monkeypatch.setattr(profiles._tls, "profile", "legacy", raising=False)
    monkeypatch.setattr(profiles, "_active_profile", "legacy")

    assert profiles.get_active_profile_name() == "default"


def test_list_profiles_returns_single_canonical_profile(monkeypatch):
    import api.profiles as profiles

    monkeypatch.setenv("TAIJI_RUNTIME_HOME", "/tmp/taiji-runtime-list-profiles")
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", profiles.Path("/tmp/taiji-runtime-list-profiles"))

    listed = profiles.list_profiles_api()

    assert [item["name"] for item in listed] == ["default"]
    assert listed[0]["is_active"] is True


def test_taiji_runtime_config_path_ignores_legacy_override(tmp_path, monkeypatch):
    import api.config as config

    runtime_home = tmp_path / "taiji-runtime"
    legacy_config = tmp_path / "legacy" / "config.yaml"
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(legacy_config))

    assert config._get_config_path() == runtime_home / "config.yaml"


@pytest.mark.parametrize("operation,args", [
    ("switch_profile", ("legacy",)),
    ("create_profile_api", ("legacy",)),
    ("delete_profile_api", ("legacy",)),
])
def test_profile_mutations_are_disabled_in_taiji_runtime(tmp_path, monkeypatch, operation, args):
    import api.profiles as profiles

    isolated_home = tmp_path / "taiji-runtime-disabled-profiles"
    isolated_home.mkdir()
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(isolated_home))
    monkeypatch.setenv("HERMES_HOME", str(isolated_home))
    monkeypatch.setenv("HERMES_BASE_HOME", str(isolated_home))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", isolated_home)

    with pytest.raises(ValueError, match="single runtime"):
        getattr(profiles, operation)(*args)
