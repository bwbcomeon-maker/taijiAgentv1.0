from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml


def _read_config(config_path: Path) -> dict[str, Any]:
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


def _capability_epochs(config: dict[str, Any]) -> tuple[int, int]:
    from agent.image_gen_verification import (
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        CAPABILITY_CONFIG_EPOCH_VISION,
        capability_config_epoch,
    )

    return (
        capability_config_epoch(
            config,
            CAPABILITY_CONFIG_EPOCH_VISION,
        ),
        capability_config_epoch(
            config,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        ),
    )


def test_main_model_shared_legacy_key_bumps_each_active_capability_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import model_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "model": {
                    "provider": "deepseek",
                    "default": "deepseek-chat",
                },
                "auxiliary": {
                    "vision": {
                        "provider": "alibaba",
                        "model": "qwen3-vl-plus",
                    }
                },
                "image_gen": {
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                },
                "_taiji_capability_epochs": {
                    "vision": 13,
                    "image_generation": 29,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DASHSCOPE_API_KEY=legacy-before\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(
        model_config,
        "_get_hermes_home",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        model_config,
        "_get_config_path",
        lambda: config_path,
    )
    monkeypatch.setattr(
        model_config,
        "_get_model_config_unlocked",
        lambda **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )

    paired_commits: list[
        tuple[dict[str, Any], dict[str, Any], dict[str, str | None]]
    ] = []
    original_commit = model_config._commit_expected_config_env

    def record_paired_commit(
        path: Path,
        *,
        expected_config: dict[str, Any],
        desired_config: dict[str, Any],
        env_updates: dict[str, str | None],
    ) -> None:
        paired_commits.append(
            (
                copy.deepcopy(expected_config),
                copy.deepcopy(desired_config),
                dict(env_updates),
            )
        )
        original_commit(
            path,
            expected_config=expected_config,
            desired_config=desired_config,
            env_updates=env_updates,
        )

    monkeypatch.setattr(
        model_config,
        "_commit_expected_config_env",
        record_paired_commit,
    )
    payload = {
        "provider": "alibaba",
        "model": "qwen-plus",
        "api_key": "legacy-after-secret",
    }

    assert model_config.set_main_model_config(payload)["ok"] is True

    first_expected, first_desired, first_env_updates = paired_commits[0]
    assert _capability_epochs(first_expected) == (13, 29)
    assert _capability_epochs(first_desired) == (14, 30)
    assert first_env_updates == {
        "DASHSCOPE_API_KEY": "legacy-after-secret"
    }
    assert _capability_epochs(_read_config(config_path)) == (14, 30)
    assert env_path.read_text(encoding="utf-8") == (
        "DASHSCOPE_API_KEY=legacy-after-secret\n"
    )

    assert model_config.set_main_model_config(payload)["ok"] is True

    second_expected, second_desired, second_env_updates = paired_commits[1]
    assert _capability_epochs(second_expected) == (14, 30)
    assert _capability_epochs(second_desired) == (14, 30)
    assert second_env_updates == {
        "DASHSCOPE_API_KEY": "legacy-after-secret"
    }
    assert _capability_epochs(_read_config(config_path)) == (14, 30)
    assert env_path.read_text(encoding="utf-8") == (
        "DASHSCOPE_API_KEY=legacy-after-secret\n"
    )
