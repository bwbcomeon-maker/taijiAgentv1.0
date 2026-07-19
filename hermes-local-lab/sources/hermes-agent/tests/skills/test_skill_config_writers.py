from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from agent import provider_credentials


_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUTO_JAILBREAK = (
    _REPO_ROOT
    / "skills"
    / "red-teaming"
    / "godmode"
    / "scripts"
    / "auto_jailbreak.py"
)
_TOUCHDESIGNER_SETUP = (
    _REPO_ROOT
    / "skills"
    / "creative"
    / "touchdesigner-mcp"
    / "scripts"
    / "setup.sh"
)


def _write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _read_yaml(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(loaded, dict)
    return loaded


def _load_auto_jailbreak(
    monkeypatch: pytest.MonkeyPatch,
    hermes_home: Path,
) -> ModuleType:
    hermes_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    spec = importlib.util.spec_from_file_location(
        f"_test_auto_jailbreak_{id(hermes_home)}",
        _AUTO_JAILBREAK,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONFIG_PATH = hermes_home / "config.yaml"
    module.PREFILL_PATH = hermes_home / "prefill.json"
    return module


def _install_fake_touchdesigner_commands(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    for name, body in {
        "pgrep": "#!/bin/sh\nexit 0\n",
        "nc": "#!/bin/sh\nexit 0\n",
        "curl": "#!/bin/sh\nprintf '{}'\n",
    }.items():
        executable = fake_bin / name
        executable.write_text(body, encoding="utf-8")
        executable.chmod(0o755)
    (fake_bin / "python3").symlink_to(sys.executable)
    return fake_bin


def _touchdesigner_env(
    tmp_path: Path,
    hermes_home: Path,
) -> dict[str, str]:
    home = tmp_path / "home"
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)
    (downloads / "twozero.tox").write_bytes(b"test fixture")
    fake_bin = _install_fake_touchdesigner_commands(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(hermes_home),
            "HERMES_PYTHON": sys.executable,
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "PYTHONPATH": (
                f"{_REPO_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"
            ),
        }
    )
    return env


def _run_touchdesigner_setup(
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_TOUCHDESIGNER_SETUP)],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_auto_jailbreak_writer_serializes_and_preserves_unrelated_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hermes_home = tmp_path / "hermes"
    module = _load_auto_jailbreak(monkeypatch, hermes_home)
    config_path = hermes_home / "config.yaml"
    original = {
        "agent": {"keep": "unchanged"},
        "unrelated": {"preserved": True},
        "_taiji_profile_incarnation": "stable-incarnation",
        "_taiji_capability_epochs": {
            "vision": 8,
            "image_generation": 13,
        },
    }
    _write_yaml(config_path, original)

    original_read = provider_credentials._read_optional_bytes
    started = threading.Event()
    concurrent_threads: list[threading.Thread] = []

    def concurrent_writer() -> None:
        started.set()

        def mutate(config: dict[str, object]) -> None:
            config["concurrent"] = {"preserved": True}

        provider_credentials.mutate_config_strict(
            mutate,
            config_path=config_path,
        )

    def interleaved_read(path: Path, **kwargs: object) -> tuple[bool, bytes]:
        result = original_read(path, **kwargs)
        if Path(path) == config_path and not concurrent_threads:
            thread = threading.Thread(target=concurrent_writer)
            concurrent_threads.append(thread)
            thread.start()
            assert started.wait(timeout=5)
        return result

    monkeypatch.setattr(
        provider_credentials,
        "_read_optional_bytes",
        interleaved_read,
    )

    module._write_config(
        system_prompt="new prompt",
        prefill_file="/tmp/prefill.json",
    )
    assert concurrent_threads, "writer bypassed the canonical transaction"
    concurrent_threads[0].join(timeout=5)
    assert not concurrent_threads[0].is_alive()

    updated = _read_yaml(config_path)
    assert updated["agent"] == {
        "keep": "unchanged",
        "system_prompt": "new prompt",
        "prefill_messages_file": "/tmp/prefill.json",
    }
    assert updated["unrelated"] == {"preserved": True}
    assert updated["concurrent"] == {"preserved": True}
    assert updated["_taiji_profile_incarnation"] == "stable-incarnation"
    assert updated["_taiji_capability_epochs"] == {
        "vision": 8,
        "image_generation": 13,
    }


def test_auto_jailbreak_writer_fails_closed_on_invalid_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hermes_home = tmp_path / "hermes"
    module = _load_auto_jailbreak(monkeypatch, hermes_home)
    config_path = hermes_home / "config.yaml"
    config_path.write_text("agent: [unterminated\n", encoding="utf-8")
    before = config_path.read_bytes()

    with pytest.raises(ValueError, match="cannot be read safely"):
        module._write_config(system_prompt="must not publish")

    assert config_path.read_bytes() == before


def test_auto_jailbreak_writer_creates_private_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hermes_home = tmp_path / "hermes"
    module = _load_auto_jailbreak(monkeypatch, hermes_home)
    config_path = hermes_home / "config.yaml"

    module._write_config(system_prompt="private prompt")

    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_auto_jailbreak_undo_serializes_node_removal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hermes_home = tmp_path / "hermes"
    module = _load_auto_jailbreak(monkeypatch, hermes_home)
    config_path = hermes_home / "config.yaml"
    _write_yaml(
        config_path,
        {
            "agent": {
                "keep": "unchanged",
                "system_prompt": "remove",
                "prefill_messages_file": "/tmp/remove.json",
            },
            "_taiji_profile_incarnation": "undo-incarnation",
            "_taiji_capability_epochs": {"vision": 21},
        },
    )
    module.PREFILL_PATH.write_text("[]", encoding="utf-8")

    original_read = provider_credentials._read_optional_bytes
    started = threading.Event()
    concurrent_threads: list[threading.Thread] = []

    def concurrent_writer() -> None:
        started.set()

        def mutate(config: dict[str, object]) -> None:
            config["concurrent_undo"] = "preserved"

        provider_credentials.mutate_config_strict(
            mutate,
            config_path=config_path,
        )

    def interleaved_read(path: Path, **kwargs: object) -> tuple[bool, bytes]:
        result = original_read(path, **kwargs)
        if Path(path) == config_path and not concurrent_threads:
            thread = threading.Thread(target=concurrent_writer)
            concurrent_threads.append(thread)
            thread.start()
            assert started.wait(timeout=5)
        return result

    monkeypatch.setattr(
        provider_credentials,
        "_read_optional_bytes",
        interleaved_read,
    )

    module.undo_jailbreak(verbose=False)
    assert concurrent_threads, "undo bypassed the canonical transaction"
    concurrent_threads[0].join(timeout=5)
    assert not concurrent_threads[0].is_alive()

    updated = _read_yaml(config_path)
    assert updated["agent"] == {"keep": "unchanged"}
    assert updated["concurrent_undo"] == "preserved"
    assert updated["_taiji_profile_incarnation"] == "undo-incarnation"
    assert updated["_taiji_capability_epochs"] == {"vision": 21}
    assert not module.PREFILL_PATH.exists()


def test_touchdesigner_writer_uses_canonical_node_mutation() -> None:
    source = _TOUCHDESIGNER_SETUP.read_text(encoding="utf-8")

    assert "mutate_config_strict" in source
    assert "HERMES_PYTHON" in source
    assert "choose_hermes_python" in source
    assert "-c 'import agent.provider_credentials'" in source
    assert "yaml.dump" not in source
    assert "open(cfg_path, 'w')" not in source
    assert 'open(cfg_path, "w")' not in source


def test_touchdesigner_writer_waits_for_canonical_lock_and_preserves_state(
    tmp_path: Path,
) -> None:
    hermes_home = tmp_path / "hermes"
    config_path = hermes_home / "config.yaml"
    initial = {
        "unrelated": {"preserved": True},
        "_taiji_profile_incarnation": "touch-incarnation",
        "_taiji_capability_epochs": {
            "vision": 5,
            "image_generation": 7,
        },
    }
    _write_yaml(config_path, initial)
    env = _touchdesigner_env(tmp_path, hermes_home)
    release_path = tmp_path / "release-lock"
    holder_source = """
import sys
import time
from pathlib import Path
from agent.provider_credentials import credential_transaction

config_path = Path(sys.argv[1])
release_path = Path(sys.argv[2])
with credential_transaction(config_path):
    print("READY", flush=True)
    deadline = time.monotonic() + 15
    while not release_path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("test lock release timed out")
        time.sleep(0.02)
"""
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            holder_source,
            str(config_path),
            str(release_path),
        ],
        cwd=_REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "READY"
    setup = subprocess.Popen(
        ["bash", str(_TOUCHDESIGNER_SETUP)],
        cwd=_REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    observed_while_locked: dict[str, object] | None = None
    setup_status_while_locked: int | None = None
    try:
        deadline = time.monotonic() + 2
        while setup.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        observed_while_locked = _read_yaml(config_path)
        setup_status_while_locked = setup.poll()
    finally:
        release_path.touch()

    holder_stdout, holder_stderr = holder.communicate(timeout=10)
    setup_stdout, setup_stderr = setup.communicate(timeout=10)
    assert observed_while_locked == initial
    assert (
        setup_status_while_locked is None
    ), "setup bypassed the canonical lock"
    assert holder.returncode == 0, holder_stderr or holder_stdout
    assert setup.returncode == 1, setup_stderr or setup_stdout

    updated = _read_yaml(config_path)
    assert updated["unrelated"] == {"preserved": True}
    assert updated["_taiji_profile_incarnation"] == "touch-incarnation"
    assert updated["_taiji_capability_epochs"] == {
        "vision": 5,
        "image_generation": 7,
    }
    assert updated["mcp_servers"] == {
        "twozero_td": {
            "url": "http://localhost:40404/mcp",
            "timeout": 120,
            "connect_timeout": 60,
        }
    }
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_touchdesigner_writer_rejects_hard_link_without_partial_overwrite(
    tmp_path: Path,
) -> None:
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text(
        "unrelated:\n  preserved: true\n",
        encoding="utf-8",
    )
    config_path = hermes_home / "config.yaml"
    os.link(outside, config_path)
    before = outside.read_bytes()
    env = _touchdesigner_env(tmp_path, hermes_home)

    result = _run_touchdesigner_setup(env)

    assert result.returncode == 1
    assert outside.read_bytes() == before
    assert config_path.read_bytes() == before


def test_touchdesigner_existing_entry_is_idempotent_and_byte_stable(
    tmp_path: Path,
) -> None:
    hermes_home = tmp_path / "hermes"
    config_path = hermes_home / "config.yaml"
    _write_yaml(
        config_path,
        {
            "mcp_servers": {
                "twozero_td": {
                    "url": "http://localhost:40404/mcp",
                    "timeout": 120,
                    "connect_timeout": 60,
                }
            },
            "unrelated": {"preserved": True},
            "_taiji_profile_incarnation": "idempotent-incarnation",
            "_taiji_capability_epochs": {"vision": 31},
        },
    )
    before = config_path.read_bytes()
    env = _touchdesigner_env(tmp_path, hermes_home)

    result = _run_touchdesigner_setup(env)

    assert result.returncode == 0, result.stderr or result.stdout
    assert config_path.read_bytes() == before
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
