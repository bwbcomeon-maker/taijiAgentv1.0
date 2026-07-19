from __future__ import annotations

import os
import stat
from argparse import Namespace
from pathlib import Path

import pytest

from hermes_cli import doctor as doctor_mod
from hermes_cli import profiles as profiles_mod


pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="exact POSIX permission modes are required",
)


class _StopDoctor(RuntimeError):
    """Stop doctor after the configuration checks under test."""


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _set_policy_env(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    *,
    group_shared: bool,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv(
        "HERMES_CREDENTIAL_GROUP_SHARED",
        "1" if group_shared else "0",
    )
    monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
    monkeypatch.delenv("HERMES_MANAGED", raising=False)
    monkeypatch.delenv("HERMES_CONTAINER", raising=False)
    monkeypatch.delenv("HERMES_SKIP_CHMOD", raising=False)


@pytest.mark.parametrize(
    ("group_shared", "home_mode", "expected_env_mode"),
    [
        (False, 0o700, 0o600),
        (True, 0o2770, 0o640),
    ],
)
def test_doctor_fix_creates_env_with_canonical_credential_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    group_shared: bool,
    home_mode: int,
    expected_env_mode: int,
) -> None:
    home = tmp_path / "runtime-home"
    home.mkdir(mode=home_mode)
    os.chown(home, -1, os.getegid())
    home.chmod(home_mode)
    config_path = home / "config.yaml"
    config_path.write_text("model: {}\n", encoding="utf-8")
    config_path.chmod(expected_env_mode)

    project_root = tmp_path / "project"
    project_root.mkdir()
    _set_policy_env(
        monkeypatch,
        home,
        group_shared=group_shared,
    )
    monkeypatch.setattr(doctor_mod, "HERMES_HOME", home)
    monkeypatch.setattr(doctor_mod, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(doctor_mod, "_DHH", str(home))
    monkeypatch.setattr(
        doctor_mod,
        "_safe_which",
        lambda _command: (_ for _ in ()).throw(_StopDoctor()),
    )

    original_umask = os.umask(0o777)
    try:
        with pytest.raises(_StopDoctor):
            doctor_mod.run_doctor(Namespace(fix=True, ack=None))
    finally:
        os.umask(original_umask)

    env_path = home / ".env"
    assert _mode(env_path) == expected_env_mode
    assert env_path.stat().st_gid == home.stat().st_gid


@pytest.mark.parametrize(
    ("group_shared", "home_mode", "source_mode", "expected_env_mode"),
    [
        (False, 0o700, 0o644, 0o600),
        (True, 0o2770, 0o600, 0o640),
    ],
)
def test_profile_clone_normalizes_env_to_canonical_credential_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    group_shared: bool,
    home_mode: int,
    source_mode: int,
    expected_env_mode: int,
) -> None:
    home = tmp_path / ".hermes"
    home.mkdir(mode=home_mode)
    os.chown(home, -1, os.getegid())
    home.chmod(home_mode)
    (home / "config.yaml").write_text("model: cloned\n", encoding="utf-8")
    source_env = home / ".env"
    source_env.write_text("SECRET=yes\n", encoding="utf-8")
    source_env.chmod(source_mode)

    _set_policy_env(
        monkeypatch,
        home,
        group_shared=group_shared,
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        profiles_mod,
        "_maybe_register_gateway_service",
        lambda _profile_name: None,
    )

    profile_dir = profiles_mod.create_profile(
        "cloned",
        clone_config=True,
        no_alias=True,
    )

    env_path = profile_dir / ".env"
    assert env_path.read_text(encoding="utf-8") == "SECRET=yes\n"
    assert _mode(env_path) == expected_env_mode
    assert env_path.stat().st_gid == profile_dir.stat().st_gid


def test_profile_clone_fails_closed_when_env_mode_cannot_be_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / ".hermes"
    home.mkdir(mode=0o700)
    (home / "config.yaml").write_text(
        "model: cloned\n",
        encoding="utf-8",
    )
    (home / ".env").write_text(
        "SECRET=yes\n",
        encoding="utf-8",
    )

    _set_policy_env(
        monkeypatch,
        home,
        group_shared=False,
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        profiles_mod,
        "_maybe_register_gateway_service",
        lambda _profile_name: None,
    )
    real_chmod = os.chmod

    def deny_cloned_env_mode(
        path: str,
        mode: int,
        *args: object,
        **kwargs: object,
    ) -> None:
        target = Path(path)
        if (
            target.name == ".env"
            and target.parent.name == "cloned"
            and not args
            and not kwargs
        ):
            raise PermissionError("mode enforcement denied")
        real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(profiles_mod.os, "chmod", deny_cloned_env_mode)

    with pytest.raises(
        PermissionError,
        match="mode enforcement denied",
    ):
        profiles_mod.create_profile(
            "cloned",
            clone_config=True,
            no_alias=True,
        )
