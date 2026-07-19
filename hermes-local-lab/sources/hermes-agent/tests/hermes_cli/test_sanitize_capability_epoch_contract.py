from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values

from agent.image_gen_verification import (
    CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    CAPABILITY_CONFIG_EPOCH_VISION,
    CAPABILITY_CONFIG_EPOCHS_KEY,
    capability_config_epoch,
)
from hermes_cli.config import save_env_value


def _read_dashscope_secret(env_path: Path) -> str:
    values = dotenv_values(
        dotenv_path=env_path,
        interpolate=False,
    )
    return str(values["DASHSCOPE_API_KEY"])


def _read_epochs(config_path: Path) -> tuple[int, int]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return (
        capability_config_epoch(config, CAPABILITY_CONFIG_EPOCH_VISION),
        capability_config_epoch(
            config,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        ),
    )


def test_unrelated_save_that_sanitizes_active_secret_a_b_a_advances_epochs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanitizing B back to a formerly authorized A must invalidate A's proof."""
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("TENOR_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

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
                CAPABILITY_CONFIG_EPOCHS_KEY: {
                    CAPABILITY_CONFIG_EPOCH_VISION: 17,
                    CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION: 23,
                },
            }
        ),
        encoding="utf-8",
    )

    env_path.write_text(
        "DASHSCOPE_API_KEY=secret-a\nTENOR_API_KEY=old\n",
        encoding="utf-8",
    )
    assert _read_dashscope_secret(env_path) == "secret-a"

    env_path.write_text(
        "DASHSCOPE_API_KEY=secret-aTENOR_API_KEY=old\n",
        encoding="utf-8",
    )
    assert _read_dashscope_secret(env_path) == (
        "secret-aTENOR_API_KEY=old"
    )
    before = _read_epochs(config_path)

    save_env_value("GITHUB_TOKEN", "unrelated-secret")

    assert _read_dashscope_secret(env_path) == "secret-a"
    after = _read_epochs(config_path)
    assert (
        after[0] > before[0],
        after[1] > before[1],
    ) == (True, True)
