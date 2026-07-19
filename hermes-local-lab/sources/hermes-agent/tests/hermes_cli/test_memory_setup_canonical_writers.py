from __future__ import annotations

import builtins
import copy
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from hermes_cli import memory_setup
from plugins.memory.hindsight import HindsightMemoryProvider
from plugins.memory.holographic import HolographicMemoryProvider


def _write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _pin_hermes_home(monkeypatch: pytest.MonkeyPatch, hermes_home: Path) -> Path:
    config_path = hermes_home / "config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    return config_path


def test_cmd_setup_provider_preserves_config_written_after_stale_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hermes_home = tmp_path / "hermes-home"
    config_path = _pin_hermes_home(monkeypatch, hermes_home)
    latest = {
        "model": "latest/model",
        "concurrent_writer": {"must_survive": True},
        "memory": {
            "provider": "old",
            "existing_provider": {"keep": "yes"},
        },
    }
    stale = {
        "model": "stale/model",
        "memory": {
            "provider": "old",
            "existing_provider": {"keep": "yes"},
        },
    }
    _write_yaml(config_path, latest)

    monkeypatch.setattr(
        memory_setup,
        "_get_available_providers",
        lambda: [("fake", "test provider", SimpleNamespace())],
    )
    monkeypatch.setattr(memory_setup, "_install_dependencies", lambda _name: None)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: copy.deepcopy(stale),
    )

    memory_setup.cmd_setup_provider("fake")

    saved = _read_yaml(config_path)
    assert saved["memory"]["provider"] == "fake"
    assert saved["memory"]["existing_provider"] == {"keep": "yes"}
    assert saved["model"] == "latest/model"
    assert saved["concurrent_writer"] == {"must_survive": True}


def test_cmd_setup_builtin_preserves_config_written_after_stale_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hermes_home = tmp_path / "hermes-home"
    config_path = _pin_hermes_home(monkeypatch, hermes_home)
    latest = {
        "concurrent_writer": {"must_survive": True},
        "memory": {"provider": "old", "settings": {"keep": 1}},
    }
    stale = {"memory": {"provider": "old", "settings": {"keep": 1}}}
    _write_yaml(config_path, latest)

    monkeypatch.setattr(
        memory_setup,
        "_get_available_providers",
        lambda: [("fake", "test provider", SimpleNamespace())],
    )
    monkeypatch.setattr(memory_setup, "_curses_select", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: copy.deepcopy(stale),
    )

    memory_setup.cmd_setup(None)

    saved = _read_yaml(config_path)
    assert saved["memory"]["provider"] == ""
    assert saved["memory"]["settings"] == {"keep": 1}
    assert saved["concurrent_writer"] == {"must_survive": True}


def test_write_env_vars_repairs_selected_duplicates_and_quotes_values(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / "hermes-home" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        "# keep this comment\n"
        "KEEP=present\n"
        "MEMORY_TOKEN=old\n"
        "MEMORY_TOKEN=stale-duplicate\n",
        encoding="utf-8",
    )

    memory_setup._write_env_vars(
        env_path,
        {"MEMORY_TOKEN": "token with spaces # and comment marker"},
    )

    env_text = env_path.read_text(encoding="utf-8")
    assert env_text.count("MEMORY_TOKEN=") == 1
    assert "MEMORY_TOKEN='token with spaces # and comment marker'\n" in env_text
    assert "# keep this comment\n" in env_text
    assert "KEEP=present\n" in env_text
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_hindsight_setup_updates_latest_main_config_and_unique_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hermes_home = tmp_path / "hermes-home"
    config_path = tmp_path / "active-profile" / "custom-config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    latest = {
        "concurrent_writer": {"must_survive": True},
        "memory": {"provider": "old", "settings": {"keep": 1}},
    }
    stale = {"memory": {"provider": "old", "settings": {"keep": 1}}}
    _write_yaml(config_path, latest)
    env_path = config_path.parent / ".env"
    env_path.write_text(
        "KEEP=present\n"
        "HINDSIGHT_TIMEOUT=30\n"
        "HINDSIGHT_TIMEOUT=60\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "hermes_cli.memory_setup._curses_select",
        lambda *args, **kwargs: 0,
    )
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": "cloud-secret")

    provider = HindsightMemoryProvider()
    provider.post_setup(str(hermes_home), copy.deepcopy(stale))

    saved = _read_yaml(config_path)
    assert saved["memory"]["provider"] == "hindsight"
    assert saved["memory"]["settings"] == {"keep": 1}
    assert saved["concurrent_writer"] == {"must_survive": True}
    env_text = env_path.read_text(encoding="utf-8")
    assert env_text.count("HINDSIGHT_TIMEOUT=") == 1
    assert "HINDSIGHT_TIMEOUT=120\n" in env_text
    assert "HINDSIGHT_API_KEY=cloud-secret\n" in env_text
    assert "KEEP=present\n" in env_text
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_holographic_save_config_mutates_latest_plugin_node_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hermes_home = tmp_path / "hermes-home"
    config_path = tmp_path / "active-profile" / "custom-config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    latest = {
        "concurrent_writer": {"must_survive": True},
        "plugins": {
            "another-plugin": {"keep": "yes"},
            "hermes-memory-store": {"old": "value"},
        },
    }
    stale = {"plugins": {"hermes-memory-store": {"old": "value"}}}
    _write_yaml(config_path, latest)

    real_open = builtins.open

    def stale_read_open(file, mode="r", *args, **kwargs):
        if Path(file) == config_path and "r" in mode:
            return io.StringIO(yaml.safe_dump(stale, sort_keys=False))
        return real_open(file, mode, *args, **kwargs)

    import plugins.memory.holographic as holographic_module

    monkeypatch.setattr(holographic_module, "open", stale_read_open, raising=False)

    HolographicMemoryProvider().save_config(
        {"db_path": "/new/memory.db"},
        str(hermes_home),
    )

    saved = _read_yaml(config_path)
    assert saved["concurrent_writer"] == {"must_survive": True}
    assert saved["plugins"]["another-plugin"] == {"keep": "yes"}
    assert saved["plugins"]["hermes-memory-store"] == {
        "db_path": "/new/memory.db",
    }


def test_holographic_save_config_propagates_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import provider_credentials

    def fail_write(*_args, **_kwargs):
        raise RuntimeError("simulated durable writer failure")

    monkeypatch.setattr(provider_credentials, "mutate_config_strict", fail_write)

    with pytest.raises(RuntimeError, match="simulated durable writer failure"):
        HolographicMemoryProvider().save_config(
            {"db_path": "/new/memory.db"},
            str(tmp_path / "hermes-home"),
        )


def test_generic_setup_saves_native_config_before_atomic_activation_and_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import provider_credentials

    hermes_home = tmp_path / "hermes-home"
    config_path = _pin_hermes_home(monkeypatch, hermes_home)
    _write_yaml(
        config_path,
        {
            "concurrent_writer": {"must_survive": True},
            "memory": {"provider": "old"},
        },
    )
    env_path = config_path.parent / ".env"
    env_path.write_text("KEEP=present\nMEMORY_TOKEN=old\n", encoding="utf-8")
    events: list[str] = []

    class GenericProvider:
        def get_config_schema(self) -> list[dict]:
            return [
                {"key": "endpoint", "description": "Endpoint"},
                {
                    "key": "token",
                    "description": "Token",
                    "secret": True,
                    "env_var": "MEMORY_TOKEN",
                },
            ]

        def save_config(self, provider_config: dict, _hermes_home: str) -> None:
            assert provider_config == {"endpoint": "https://memory.example"}
            events.append("native")

    real_pair_writer = provider_credentials.mutate_config_env_strict

    def record_pair_writer(*args, **kwargs):
        events.append("pair")
        return real_pair_writer(*args, **kwargs)

    def reject_split_writer(*_args, **_kwargs):
        raise AssertionError("generic setup must not use a split credential writer")

    monkeypatch.setattr(
        memory_setup,
        "_get_available_providers",
        lambda: [("generic", "test provider", GenericProvider())],
    )
    monkeypatch.setattr(memory_setup, "_install_dependencies", lambda _name: None)
    monkeypatch.setattr(memory_setup, "_curses_select", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        memory_setup,
        "_prompt",
        lambda _label, default=None, secret=False: (
            "new-secret" if secret else "https://memory.example"
        ),
    )
    monkeypatch.setattr(
        provider_credentials,
        "mutate_config_env_strict",
        record_pair_writer,
    )
    monkeypatch.setattr(
        provider_credentials,
        "mutate_config_strict",
        reject_split_writer,
    )
    monkeypatch.setattr(
        provider_credentials,
        "mutate_env_unique",
        reject_split_writer,
    )

    memory_setup.cmd_setup(None)

    assert events == ["native", "pair"]
    saved = _read_yaml(config_path)
    assert saved["memory"]["provider"] == "generic"
    assert saved["concurrent_writer"] == {"must_survive": True}
    assert "MEMORY_TOKEN=new-secret\n" in env_path.read_text(encoding="utf-8")
    assert "KEEP=present\n" in env_path.read_text(encoding="utf-8")


def test_generic_setup_native_config_failure_stops_without_activation_or_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hermes_home = tmp_path / "hermes-home"
    config_path = _pin_hermes_home(monkeypatch, hermes_home)
    original_config = {"memory": {"provider": "old"}}
    _write_yaml(config_path, original_config)
    env_path = config_path.parent / ".env"
    env_path.write_text("MEMORY_TOKEN=old\n", encoding="utf-8")

    class FailingProvider:
        def get_config_schema(self) -> list[dict]:
            return [
                {"key": "endpoint", "description": "Endpoint"},
                {
                    "key": "token",
                    "description": "Token",
                    "secret": True,
                    "env_var": "MEMORY_TOKEN",
                },
            ]

        def save_config(self, _provider_config: dict, _hermes_home: str) -> None:
            raise RuntimeError("simulated native config failure")

    monkeypatch.setattr(
        memory_setup,
        "_get_available_providers",
        lambda: [("generic", "test provider", FailingProvider())],
    )
    monkeypatch.setattr(memory_setup, "_install_dependencies", lambda _name: None)
    monkeypatch.setattr(memory_setup, "_curses_select", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        memory_setup,
        "_prompt",
        lambda _label, default=None, secret=False: (
            "new-secret" if secret else "https://memory.example"
        ),
    )

    with pytest.raises(RuntimeError, match="simulated native config failure"):
        memory_setup.cmd_setup(None)

    assert _read_yaml(config_path) == original_config
    assert env_path.read_text(encoding="utf-8") == "MEMORY_TOKEN=old\n"
    output = capsys.readouterr().out
    assert "Activation saved" not in output
    assert "Provider config saved" not in output
    assert "API keys saved" not in output


def test_generic_setup_atomic_writer_failure_keeps_activation_and_env_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agent import provider_credentials

    hermes_home = tmp_path / "hermes-home"
    config_path = _pin_hermes_home(monkeypatch, hermes_home)
    original_config = {"memory": {"provider": "old"}}
    _write_yaml(config_path, original_config)
    env_path = config_path.parent / ".env"
    env_path.write_text("MEMORY_TOKEN=old\n", encoding="utf-8")
    native_saves: list[dict] = []

    class GenericProvider:
        def get_config_schema(self) -> list[dict]:
            return [
                {"key": "endpoint", "description": "Endpoint"},
                {
                    "key": "token",
                    "description": "Token",
                    "secret": True,
                    "env_var": "MEMORY_TOKEN",
                },
            ]

        def save_config(self, provider_config: dict, _hermes_home: str) -> None:
            native_saves.append(dict(provider_config))

    def fail_pair_writer(*_args, **_kwargs):
        raise RuntimeError("simulated config/env transaction failure")

    monkeypatch.setattr(
        memory_setup,
        "_get_available_providers",
        lambda: [("generic", "test provider", GenericProvider())],
    )
    monkeypatch.setattr(memory_setup, "_install_dependencies", lambda _name: None)
    monkeypatch.setattr(memory_setup, "_curses_select", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        memory_setup,
        "_prompt",
        lambda _label, default=None, secret=False: "new-secret",
    )
    monkeypatch.setattr(
        provider_credentials,
        "mutate_config_env_strict",
        fail_pair_writer,
    )

    with pytest.raises(
        RuntimeError,
        match="simulated config/env transaction failure",
    ):
        memory_setup.cmd_setup(None)

    assert native_saves == [{"endpoint": "new-secret"}]
    assert _read_yaml(config_path) == original_config
    assert env_path.read_text(encoding="utf-8") == "MEMORY_TOKEN=old\n"
    output = capsys.readouterr().out
    assert "Activation saved" not in output
    assert "Provider config saved" not in output
    assert "API keys saved" not in output
