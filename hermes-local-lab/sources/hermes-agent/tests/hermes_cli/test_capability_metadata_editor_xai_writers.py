from __future__ import annotations

import copy
from argparse import Namespace
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


def _config(*, vision_epoch: int, image_epoch: int) -> dict:
    return {
        "auxiliary": {
            "vision": {
                "provider": "xai",
                "model": "grok-4-fast-reasoning",
            }
        },
        "image_gen": {
            "provider": "dashscope",
            "model": "wanx2.1-t2i-turbo",
        },
        CAPABILITY_CONFIG_EPOCHS_KEY: {
            CAPABILITY_CONFIG_EPOCH_VISION: vision_epoch,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION: image_epoch,
        },
        CAPABILITY_PROFILE_INCARNATION_KEY: "incarnation-current",
    }


def _write_config(path: Path, config: dict) -> None:
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _read_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_config_edit_uses_temporary_copy_and_reconciles_capability_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hermes_cli.config as config_mod

    config_path = tmp_path / "config.yaml"
    before = _config(vision_epoch=17, image_epoch=23)
    _write_config(config_path, before)

    edited_paths: list[Path] = []

    def _fake_editor(argv, *args, **kwargs):
        editor_path = Path(argv[-1])
        edited_paths.append(editor_path)
        edited = _read_config(editor_path)
        edited["auxiliary"]["vision"]["model"] = "grok-4.3"
        edited[CAPABILITY_CONFIG_EPOCHS_KEY][
            CAPABILITY_CONFIG_EPOCH_VISION
        ] = 1
        edited[CAPABILITY_PROFILE_INCARNATION_KEY] = "incarnation-stale"
        _write_config(editor_path, edited)
        return config_mod.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(config_mod, "is_managed", lambda: False)
    monkeypatch.setattr(config_mod, "get_config_path", lambda: config_path)
    monkeypatch.setattr(config_mod.subprocess, "run", _fake_editor)
    monkeypatch.setenv("EDITOR", "fake-editor")

    config_mod.edit_config()

    assert edited_paths and edited_paths[0] != config_path
    after = _read_config(config_path)
    assert after["auxiliary"]["vision"]["model"] == "grok-4.3"
    assert (
        capability_config_epoch(after, CAPABILITY_CONFIG_EPOCH_VISION)
        == 18
    )
    assert (
        capability_config_epoch(
            after,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        )
        == 23
    )
    assert (
        after[CAPABILITY_PROFILE_INCARNATION_KEY]
        == "incarnation-current"
    )


def test_migrate_xai_apply_advances_vision_epoch_and_preserves_incarnation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hermes_cli.migrate as migrate_mod

    config_path = tmp_path / "config.yaml"
    before = _config(vision_epoch=31, image_epoch=37)
    _write_config(config_path, before)

    monkeypatch.setattr(
        migrate_mod,
        "load_config",
        lambda: copy.deepcopy(before),
    )
    monkeypatch.setattr(
        migrate_mod,
        "_resolve_config_path",
        lambda: config_path,
    )

    result = migrate_mod.cmd_migrate_xai(
        Namespace(apply=True, no_backup=True)
    )

    assert result == 0
    after = _read_config(config_path)
    assert after["auxiliary"]["vision"]["model"] == "grok-4.3"
    assert (
        capability_config_epoch(after, CAPABILITY_CONFIG_EPOCH_VISION)
        == 32
    )
    assert (
        capability_config_epoch(
            after,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        )
        == 37
    )
    assert (
        after[CAPABILITY_PROFILE_INCARNATION_KEY]
        == "incarnation-current"
    )
