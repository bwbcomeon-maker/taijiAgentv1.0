from __future__ import annotations

import copy
import io
import sys
import types
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


def _config(
    *,
    vision_epoch: int,
    image_epoch: int,
    incarnation: str,
    vision_model: str = "qwen3-vl-plus",
    model_provider: str = "openai-codex",
) -> dict:
    return {
        "_config_version": 24,
        "auxiliary": {
            "vision": {
                "provider": "alibaba",
                "model": vision_model,
            }
        },
        "image_gen": {
            "provider": "dashscope",
            "model": "wanx2.1-t2i-turbo",
        },
        "model": {
            "default": "gpt-5.3-codex",
            "provider": model_provider,
            "base_url": "https://chatgpt.com/backend-api/codex",
        },
        CAPABILITY_CONFIG_EPOCHS_KEY: {
            CAPABILITY_CONFIG_EPOCH_VISION: vision_epoch,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION: image_epoch,
        },
        CAPABILITY_PROFILE_INCARNATION_KEY: incarnation,
    }


def _use_isolated_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
    return config_path


def _write_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _read_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _assert_metadata(
    config: dict,
    *,
    vision_epoch: int,
    image_epoch: int,
    incarnation: str,
) -> None:
    assert (
        capability_config_epoch(config, CAPABILITY_CONFIG_EPOCH_VISION)
        == vision_epoch
    )
    assert (
        capability_config_epoch(
            config,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        )
        == image_epoch
    )
    assert config[CAPABILITY_PROFILE_INCARNATION_KEY] == incarnation


def test_config_set_vision_model_advances_only_vision_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermes_cli.config import set_config_value

    config_path = _use_isolated_home(monkeypatch, tmp_path)
    before = _config(
        vision_epoch=17,
        image_epoch=23,
        incarnation="incarnation-current",
    )
    _write_config(config_path, before)

    set_config_value("auxiliary.vision.model", "qwen-vl-max")

    after = _read_config(config_path)
    assert after["auxiliary"]["vision"]["model"] == "qwen-vl-max"
    _assert_metadata(
        after,
        vision_epoch=18,
        image_epoch=23,
        incarnation="incarnation-current",
    )


def test_auth_provider_update_cannot_restore_stale_capability_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hermes_cli.auth as auth_mod

    config_path = _use_isolated_home(monkeypatch, tmp_path)
    current = _config(
        vision_epoch=41,
        image_epoch=53,
        incarnation="incarnation-current",
    )
    stale = _config(
        vision_epoch=5,
        image_epoch=7,
        incarnation="incarnation-stale",
    )
    _write_config(config_path, current)
    monkeypatch.setattr(
        auth_mod,
        "read_raw_config",
        lambda: copy.deepcopy(stale),
    )

    auth_mod._update_config_for_provider(
        "openrouter",
        "https://openrouter.ai/api/v1",
    )

    after = _read_config(config_path)
    assert after["model"]["provider"] == "openrouter"
    _assert_metadata(
        after,
        vision_epoch=41,
        image_epoch=53,
        incarnation="incarnation-current",
    )


def test_auth_logout_reset_cannot_restore_stale_capability_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hermes_cli.auth as auth_mod

    config_path = _use_isolated_home(monkeypatch, tmp_path)
    current = _config(
        vision_epoch=61,
        image_epoch=67,
        incarnation="incarnation-current",
    )
    stale = _config(
        vision_epoch=11,
        image_epoch=13,
        incarnation="incarnation-stale",
    )
    _write_config(config_path, current)
    monkeypatch.setattr(
        auth_mod,
        "read_raw_config",
        lambda: copy.deepcopy(stale),
    )

    auth_mod._reset_config_provider()

    after = _read_config(config_path)
    assert after["model"]["provider"] == "auto"
    _assert_metadata(
        after,
        vision_epoch=61,
        image_epoch=67,
        incarnation="incarnation-current",
    )


def test_doctor_fix_stale_root_keys_cannot_restore_stale_capability_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hermes_cli.doctor as doctor_mod

    config_path = _use_isolated_home(monkeypatch, tmp_path)
    current = _config(
        vision_epoch=71,
        image_epoch=73,
        incarnation="incarnation-current",
        model_provider="",
    )
    current["provider"] = "openrouter"
    current["base_url"] = "https://openrouter.ai/api/v1"
    stale = copy.deepcopy(current)
    stale[CAPABILITY_CONFIG_EPOCHS_KEY] = {
        CAPABILITY_CONFIG_EPOCH_VISION: 2,
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION: 3,
    }
    stale[CAPABILITY_PROFILE_INCARNATION_KEY] = "incarnation-stale"
    _write_config(config_path, current)
    (tmp_path / ".env").write_text("", encoding="utf-8")

    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr(doctor_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(doctor_mod, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(doctor_mod, "_DHH", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(doctor_mod, "_safe_which", lambda _cmd: None)
    monkeypatch.setitem(
        sys.modules,
        "model_tools",
        types.SimpleNamespace(
            check_tool_availability=lambda: ([], []),
            TOOLSET_REQUIREMENTS={},
        ),
    )

    real_open = open
    stale_text = yaml.safe_dump(stale, sort_keys=False)

    def _open_with_stale_doctor_snapshot(path, *args, **kwargs):
        if Path(path) == config_path:
            return io.StringIO(stale_text)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(
        doctor_mod,
        "open",
        _open_with_stale_doctor_snapshot,
        raising=False,
    )

    doctor_mod.run_doctor(Namespace(fix=True, ack=None))

    after = _read_config(config_path)
    assert "provider" not in after
    assert "base_url" not in after
    assert after["model"]["provider"] == "openrouter"
    _assert_metadata(
        after,
        vision_epoch=71,
        image_epoch=73,
        incarnation="incarnation-current",
    )
