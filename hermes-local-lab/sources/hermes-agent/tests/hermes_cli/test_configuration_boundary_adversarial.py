from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest


def _isolate_home(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
    monkeypatch.delenv("HERMES_MANAGED", raising=False)
    monkeypatch.delenv("HERMES_CONTAINER", raising=False)
    monkeypatch.delenv("HERMES_SKIP_CHMOD", raising=False)
    monkeypatch.delenv("HERMES_HOME_MODE", raising=False)


def test_root_managed_marker_applies_to_named_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.provider_credentials import mutate_config_strict
    from hermes_cli.config import ManagedConfigurationError, is_managed

    root = tmp_path / "hermes-root"
    profile = root / "profiles" / "worker"
    profile.mkdir(parents=True)
    (root / ".managed").write_text("nixos\n", encoding="utf-8")
    _isolate_home(monkeypatch, profile)

    assert is_managed() is True
    with pytest.raises(ManagedConfigurationError) as exc:
        mutate_config_strict(
            lambda config: config.update({"model": "forbidden"}),
            config_path=profile / "config.yaml",
        )

    assert exc.value.code == "managed_configuration"
    assert not (profile / "config.yaml").exists()
    assert not (profile / ".taiji-credential-transaction.lock").exists()


def test_exact_config_override_honors_target_root_managed_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.provider_credentials import mutate_config_strict
    from hermes_cli.config import ManagedConfigurationError

    unmanaged_home = tmp_path / "unmanaged-home"
    managed_root = tmp_path / "managed-root"
    managed_profile = managed_root / "profiles" / "worker"
    unmanaged_home.mkdir()
    managed_profile.mkdir(parents=True)
    (managed_root / ".managed").write_text("nixos\n", encoding="utf-8")
    target = managed_profile / "config.yaml"
    _isolate_home(monkeypatch, unmanaged_home)
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(target))

    with pytest.raises(ManagedConfigurationError) as exc:
        mutate_config_strict(
            lambda config: config.update({"model": "forbidden"}),
            config_path=target,
        )

    assert exc.value.code == "managed_configuration"
    assert not target.exists()
    assert not (
        managed_profile / ".taiji-credential-transaction.lock"
    ).exists()


def test_explicit_config_path_honors_managed_target_without_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.provider_credentials import mutate_config_strict
    from hermes_cli.config import ManagedConfigurationError

    unmanaged_home = tmp_path / "unmanaged-home"
    managed_root = tmp_path / "managed-root"
    managed_profile = managed_root / "profiles" / "worker"
    unmanaged_home.mkdir()
    managed_profile.mkdir(parents=True)
    (managed_root / ".managed").write_text("nixos\n", encoding="utf-8")
    target = managed_profile / "config.yaml"
    _isolate_home(monkeypatch, unmanaged_home)

    with pytest.raises(ManagedConfigurationError) as exc:
        mutate_config_strict(
            lambda config: config.update({"model": "forbidden"}),
            config_path=target,
        )

    assert exc.value.code == "managed_configuration"
    assert not target.exists()
    assert not (
        managed_profile / ".taiji-credential-transaction.lock"
    ).exists()


def test_config_file_symlink_cannot_bypass_managed_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.provider_credentials import mutate_config_strict
    from hermes_cli.config import ManagedConfigurationError

    unmanaged_home = tmp_path / "unmanaged-home"
    managed_root = tmp_path / "managed-root"
    managed_profile = managed_root / "profiles" / "worker"
    unmanaged_home.mkdir()
    managed_profile.mkdir(parents=True)
    (managed_root / ".managed").write_text("nixos\n", encoding="utf-8")
    managed_target = managed_profile / "config.yaml"
    managed_target.write_text("model: original\n", encoding="utf-8")
    logical_target = unmanaged_home / "config.yaml"
    logical_target.symlink_to(managed_target)
    _isolate_home(monkeypatch, unmanaged_home)

    with pytest.raises(ManagedConfigurationError) as exc:
        mutate_config_strict(
            lambda config: config.update({"model": "forbidden"}),
            config_path=logical_target,
        )

    assert exc.value.code == "managed_configuration"
    assert managed_target.read_text(encoding="utf-8") == "model: original\n"
    assert not (
        unmanaged_home / ".taiji-credential-transaction.lock"
    ).exists()
    assert not (
        managed_profile / ".taiji-credential-transaction.lock"
    ).exists()


def test_config_set_rejects_managed_mode_before_home_or_lock_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermes_cli.config import ManagedConfigurationError, set_config_value

    home = tmp_path / "missing-managed-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED", "nixos")
    monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)

    with pytest.raises(ManagedConfigurationError) as exc:
        set_config_value("display.skin", "mono")

    assert exc.value.code == "managed_configuration"
    assert not home.exists()


def test_config_set_command_reports_managed_error_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hermes_cli.config import config_command

    home = tmp_path / "missing-managed-home"
    _isolate_home(monkeypatch, home)
    monkeypatch.setenv("HERMES_MANAGED", "nixos")

    with pytest.raises(SystemExit) as exc:
        config_command(
            SimpleNamespace(
                config_command="set",
                key="display.skin",
                value="mono",
            )
        )

    captured = capsys.readouterr()
    assert exc.value.code == 2
    assert "Cannot set display.skin" in captured.err
    assert "Traceback" not in captured.err
    assert not home.exists()


def test_sanitize_env_rejects_managed_mode_before_lock_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermes_cli.config import (
        ManagedConfigurationError,
        sanitize_env_file,
    )

    home = tmp_path / "managed-home"
    home.mkdir()
    (home / ".env").write_text(
        "DASHSCOPE_API_KEY=managed-secret\n",
        encoding="utf-8",
    )
    _isolate_home(monkeypatch, home)
    monkeypatch.setenv("HERMES_MANAGED", "nixos")

    with pytest.raises(ManagedConfigurationError) as exc:
        sanitize_env_file()

    assert exc.value.code == "managed_configuration"
    assert not (
        home / ".taiji-credential-transaction.lock"
    ).exists()


@pytest.mark.parametrize(
    ("writer_name", "args"),
    [
        ("save_anthropic_oauth_token", ("oauth-secret",)),
        ("use_anthropic_claude_code_credentials", ()),
        ("save_anthropic_api_key", ("api-secret",)),
    ],
)
def test_anthropic_writers_reject_managed_mode_before_mutable_state(
    writer_name: str,
    args: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hermes_cli.config as config_module

    home = tmp_path / "missing-managed-home"
    _isolate_home(monkeypatch, home)
    monkeypatch.setenv("HERMES_MANAGED", "nixos")
    writer = getattr(config_module, writer_name)

    with pytest.raises(config_module.ManagedConfigurationError) as exc:
        writer(*args)

    assert exc.value.code == "managed_configuration"
    assert not home.exists()


def test_remove_env_value_recognizes_export_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yaml

    from agent.image_gen_verification import (
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        CAPABILITY_CONFIG_EPOCH_VISION,
        capability_config_epoch,
    )
    from hermes_cli.config import (
        invalidate_env_cache,
        load_env,
        remove_env_value,
    )

    _isolate_home(monkeypatch, tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "auxiliary": {
                    "vision": {
                        "provider": "alibaba",
                        "model": "qwen3-vl-plus",
                    }
                },
                "image_gen": {
                    "provider": "dashscope",
                    "model": "wanx2.1-t2i-turbo",
                },
            }
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "export DASHSCOPE_API_KEY=exported-secret\n"
        "KEEP_KEY=keep\n",
        encoding="utf-8",
    )
    invalidate_env_cache()
    before = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert load_env()["DASHSCOPE_API_KEY"] == "exported-secret"
    assert remove_env_value("DASHSCOPE_API_KEY") is True
    assert "DASHSCOPE_API_KEY" not in load_env()
    assert "KEEP_KEY=keep" in env_path.read_text(encoding="utf-8")
    after = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert capability_config_epoch(
        after,
        CAPABILITY_CONFIG_EPOCH_VISION,
    ) > capability_config_epoch(
        before,
        CAPABILITY_CONFIG_EPOCH_VISION,
    )
    assert capability_config_epoch(
        after,
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    ) > capability_config_epoch(
        before,
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    )


@pytest.mark.skipif(
    os.name != "posix",
    reason="exact POSIX permission modes are required",
)
def test_project_fallback_preserves_group_shared_config_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cli

    home = tmp_path / "empty-home"
    project = tmp_path / "project"
    project.mkdir(mode=0o2770)
    os.chown(project, -1, os.getegid())
    project.chmod(0o2770)
    _isolate_home(monkeypatch, home)
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")
    monkeypatch.setattr(cli, "_hermes_home", home)
    monkeypatch.setattr(cli, "__file__", str(project / "cli.py"))

    assert cli.save_config_value("display.skin", "mono") is True

    config_path = project / "cli-config.yaml"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o640
    assert config_path.stat().st_gid == project.stat().st_gid
