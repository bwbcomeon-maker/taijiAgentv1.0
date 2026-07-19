from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from agent.provider_credentials import (
    mutate_config_env_strict,
    mutate_config_strict,
    mutate_env_unique,
    replace_config_env_payload_strict,
    seed_config_payload_strict,
)
from hermes_cli.config import ManagedConfigurationError


def _mutate_config() -> None:
    mutate_config_strict(lambda config: config.update({"model": "forbidden"}))


def _seed_config() -> None:
    seed_config_payload_strict(b"model: forbidden\n")


def _mutate_env() -> None:
    mutate_env_unique({"TEST_API_KEY": "forbidden"})


def _mutate_config_and_env() -> None:
    mutate_config_env_strict(
        lambda config: config.update({"model": "forbidden"}),
        {"TEST_API_KEY": "forbidden"},
    )


def _replace_config_and_env() -> None:
    replace_config_env_payload_strict(
        lambda config: config.update({"model": "forbidden"}),
        b"TEST_API_KEY=forbidden\n",
        env_keys=("TEST_API_KEY",),
    )


@pytest.mark.parametrize(
    "writer",
    [
        _mutate_config,
        _seed_config,
        _mutate_env,
        _mutate_config_and_env,
        _replace_config_and_env,
    ],
    ids=[
        "config",
        "seed",
        "env",
        "config-env",
        "replace-config-env",
    ],
)
def test_low_level_credential_writers_reject_managed_before_mutable_state(
    writer: Callable[[], None],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "managed-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED", "nixos")

    with pytest.raises(ManagedConfigurationError) as exc:
        writer()

    assert exc.value.code == "managed_configuration"
    assert not home.exists()


def test_managed_marker_also_blocks_low_level_writer_before_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "managed-home"
    home.mkdir()
    (home / ".managed").write_text("nixos\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_MANAGED", raising=False)

    with pytest.raises(ManagedConfigurationError):
        mutate_config_strict(
            lambda config: config.update({"model": "forbidden"})
        )

    assert not (home / "config.yaml").exists()
    assert not (
        home / ".taiji-credential-transaction.lock"
    ).exists()
