from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from agent.image_gen_verification import (
    CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    CAPABILITY_CONFIG_EPOCH_VISION,
    CAPABILITY_CONFIG_EPOCHS_KEY,
    CAPABILITY_PROFILE_INCARNATION_KEY,
    capability_config_epoch,
)


def _shared_zhipu_capability_config() -> dict:
    return {
        CAPABILITY_PROFILE_INCARNATION_KEY: "current-incarnation",
        CAPABILITY_CONFIG_EPOCHS_KEY: {
            CAPABILITY_CONFIG_EPOCH_VISION: 7,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION: 11,
        },
        "model": {
            "provider": "deepseek",
            "default": "deepseek-chat",
        },
        "auxiliary": {
            "vision": {
                "provider": "zai",
                "model": "glm-4.5v",
            }
        },
        "image_gen": {
            "provider": "zhipu-image",
            "model": "cogview-4",
        },
    }


def _write_config(config_path: Path, config_data: dict) -> None:
    config_path.write_text(
        yaml.safe_dump(config_data, sort_keys=False),
        encoding="utf-8",
    )


def _read_config(config_path: Path) -> dict:
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert isinstance(loaded, dict)
    return loaded


def test_save_yaml_config_file_reconciles_vision_epoch_and_incarnation(
    tmp_path: Path,
) -> None:
    from api import config

    config_path = tmp_path / "config.yaml"
    current = _shared_zhipu_capability_config()
    _write_config(config_path, current)

    stale_replacement = copy.deepcopy(current)
    stale_replacement["auxiliary"]["vision"]["model"] = "glm-4.6v"
    stale_replacement[CAPABILITY_PROFILE_INCARNATION_KEY] = (
        "stale-incarnation"
    )
    stale_replacement[CAPABILITY_CONFIG_EPOCHS_KEY][
        CAPABILITY_CONFIG_EPOCH_VISION
    ] = 2

    config._save_yaml_config_file(config_path, stale_replacement)

    persisted = _read_config(config_path)
    assert (
        persisted[CAPABILITY_PROFILE_INCARNATION_KEY]
        == "current-incarnation"
    )
    assert (
        capability_config_epoch(
            persisted,
            CAPABILITY_CONFIG_EPOCH_VISION,
        )
        == 8
    )
    assert (
        capability_config_epoch(
            persisted,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        )
        == 11
    )


def test_set_auxiliary_model_advances_vision_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import config

    config_path = tmp_path / "config.yaml"
    _write_config(config_path, _shared_zhipu_capability_config())
    monkeypatch.setattr(config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(config, "reload_config", lambda: None)

    result = config.set_auxiliary_model(
        "vision",
        "zai",
        "glm-4.6v",
    )

    persisted = _read_config(config_path)
    assert result["ok"] is True
    assert (
        capability_config_epoch(
            persisted,
            CAPABILITY_CONFIG_EPOCH_VISION,
        )
        == 8
    )
    assert (
        persisted[CAPABILITY_PROFILE_INCARNATION_KEY]
        == "current-incarnation"
    )


def test_onboarding_shared_zhipu_secret_bumps_epochs_inside_paired_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.provider_credentials as credential_store
    from api import onboarding
    from api import profiles
    from hermes_cli import config as cli_config

    config_path = tmp_path / "config.yaml"
    current = _shared_zhipu_capability_config()
    _write_config(config_path, current)
    (tmp_path / ".env").write_text(
        "GLM_API_KEY=old-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(onboarding, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(onboarding, "reload_config", lambda: None)
    monkeypatch.setattr(
        onboarding,
        "get_onboarding_status",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(profiles, "_reload_dotenv", lambda _home: None)
    monkeypatch.setattr(cli_config, "reload", lambda: None, raising=False)

    captured: dict[str, object] = {}

    def capture_paired_commit(
        config_mutator,
        env_updates,
        *,
        config_path,
    ) -> None:
        staged = copy.deepcopy(_read_config(Path(config_path)))
        config_mutator(staged)
        captured["config"] = staged
        captured["env_updates"] = dict(env_updates)

    monkeypatch.setattr(
        credential_store,
        "mutate_config_env_strict",
        capture_paired_commit,
    )

    result = onboarding.apply_onboarding_setup(
        {
            "provider": "zai",
            "model": "glm-5.1",
            "api_key": "new-secret",
            "confirm_overwrite": True,
        }
    )

    staged = captured["config"]
    assert isinstance(staged, dict)
    assert result == {"ok": True}
    assert captured["env_updates"] == {"GLM_API_KEY": "new-secret"}
    assert (
        capability_config_epoch(
            staged,
            CAPABILITY_CONFIG_EPOCH_VISION,
        )
        == 8
    )
    assert (
        capability_config_epoch(
            staged,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        )
        == 12
    )
