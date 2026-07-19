from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from hermes_cli import config


pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="exact POSIX permission modes are required",
)


def _isolate_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
    monkeypatch.delenv("HERMES_MANAGED", raising=False)
    monkeypatch.delenv("HERMES_CONTAINER", raising=False)
    monkeypatch.delenv("HERMES_SKIP_CHMOD", raising=False)
    monkeypatch.delenv("HERMES_HOME_MODE", raising=False)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_private_callers_keep_home_and_canonical_files_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "private-home"
    _isolate_home(monkeypatch, home)
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "0")

    config.ensure_hermes_home()
    config.save_config({"model": "private-model"})
    config.save_env_value("PRIVATE_TEST_KEY", "private-secret")

    assert _mode(home) == 0o700
    assert _mode(home / "config.yaml") == 0o600
    assert _mode(home / ".env") == 0o600


def test_group_shared_home_is_setgid_and_group_writable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "shared-home"
    _isolate_home(monkeypatch, home)
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")
    home.mkdir(mode=0o2770)
    os.chown(home, -1, os.getegid())
    home.chmod(0o2770)

    config.ensure_hermes_home()

    assert _mode(home) == 0o2770
    assert home.stat().st_gid == os.getegid()


def test_group_shared_callers_preserve_canonical_mode_and_trusted_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "shared-home"
    _isolate_home(monkeypatch, home)
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")
    home.mkdir(mode=0o2770)
    os.chown(home, -1, os.getegid())
    home.chmod(0o2770)

    config.save_config({"model": "shared-model"})
    config.save_env_value("SHARED_TEST_KEY", "shared-secret")

    root_group = home.stat().st_gid
    assert _mode(home) == 0o2770
    assert root_group == os.getegid()
    assert _mode(home / "config.yaml") == 0o640
    assert _mode(home / ".env") == 0o640
    assert (home / "config.yaml").stat().st_gid == root_group
    assert (home / ".env").stat().st_gid == root_group


def test_managed_permission_helpers_leave_activation_owned_modes_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "managed-home"
    home.mkdir(mode=0o2750)
    os.chown(home, -1, os.getegid())
    home.chmod(0o2750)
    config_path = home / "config.yaml"
    config_path.write_text("model: managed\n", encoding="utf-8")
    config_path.chmod(0o660)
    _isolate_home(monkeypatch, home)
    monkeypatch.setenv("HERMES_MANAGED", "nixos")
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")

    config._secure_dir(home)
    config._secure_file(config_path)

    assert _mode(home) == 0o2750
    assert home.stat().st_gid == os.getegid()
    assert _mode(config_path) == 0o660
