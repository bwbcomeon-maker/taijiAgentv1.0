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
from hermes_cli.config import (
    remove_env_value,
    save_config,
    save_env_value,
)


def _capability_config(
    *,
    vision_model: str = "qwen3-vl-plus",
    image_model: str = "wanx2.1-t2i-turbo",
) -> dict:
    return {
        "auxiliary": {
            "vision": {
                "provider": "alibaba",
                "model": vision_model,
            }
        },
        "image_gen": {
            "provider": "dashscope",
            "model": image_model,
        },
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


def _read_epochs(config_path: Path) -> tuple[int, int]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return (
        capability_config_epoch(config, CAPABILITY_CONFIG_EPOCH_VISION),
        capability_config_epoch(
            config,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        ),
    )


def _assert_both_epochs_strictly_increase(
    observed: list[tuple[int, int]],
) -> None:
    assert observed[0][0] > 0
    assert observed[0][1] > 0
    assert all(
        current[0] > previous[0] and current[1] > previous[1]
        for previous, current in zip(
            observed[:-1],
            observed[1:],
            strict=True,
        )
    )


def test_save_config_a_b_a_keeps_both_capability_epochs_monotonic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _use_isolated_home(monkeypatch, tmp_path)
    config_a = _capability_config()
    config_b = _capability_config(
        vision_model="qwen-vl-max",
        image_model="wanx2.1-t2i-plus",
    )

    observed = []
    for config in (config_a, config_b, config_a):
        save_config(copy.deepcopy(config))
        observed.append(_read_epochs(config_path))

    _assert_both_epochs_strictly_increase(observed)


def test_setup_reset_advances_instead_of_rolling_back_capability_epochs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hermes_cli.setup import run_setup_wizard

    config_path = _use_isolated_home(monkeypatch, tmp_path)
    config = _capability_config()
    config[CAPABILITY_CONFIG_EPOCHS_KEY] = {
        CAPABILITY_CONFIG_EPOCH_VISION: 7,
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION: 11,
    }
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    before = _read_epochs(config_path)

    run_setup_wizard(
        Namespace(
            non_interactive=True,
            section=None,
            reset=True,
            reconfigure=False,
            quick=False,
        )
    )

    after = _read_epochs(config_path)
    assert after[0] > before[0]
    assert after[1] > before[1]
    assert "Configuration reset to defaults." in capsys.readouterr().out


def test_save_config_cannot_roll_back_profile_incarnation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _use_isolated_home(monkeypatch, tmp_path)
    current = _capability_config()
    current[CAPABILITY_PROFILE_INCARNATION_KEY] = "current-incarnation"
    config_path.write_text(yaml.safe_dump(current), encoding="utf-8")
    restored = _capability_config()
    restored[CAPABILITY_PROFILE_INCARNATION_KEY] = "stale-incarnation"

    save_config(restored)

    persisted = yaml.safe_load(
        config_path.read_text(encoding="utf-8")
    )
    assert (
        persisted[CAPABILITY_PROFILE_INCARNATION_KEY]
        == "current-incarnation"
    )


def test_dashscope_env_a_b_a_advances_both_capability_epochs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _use_isolated_home(monkeypatch, tmp_path)
    config_path.write_text(
        yaml.safe_dump(_capability_config()),
        encoding="utf-8",
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    observed = []
    for secret in (
        "dashscope-secret-a",
        "dashscope-secret-b",
        "dashscope-secret-a",
    ):
        save_env_value("DASHSCOPE_API_KEY", secret)
        observed.append(_read_epochs(config_path))

    _assert_both_epochs_strictly_increase(observed)


def test_dashscope_env_remove_restore_advances_both_capability_epochs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _use_isolated_home(monkeypatch, tmp_path)
    config_path.write_text(
        yaml.safe_dump(_capability_config()),
        encoding="utf-8",
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    save_env_value("DASHSCOPE_API_KEY", "dashscope-secret-a")
    after_set = _read_epochs(config_path)

    assert remove_env_value("DASHSCOPE_API_KEY") is True
    after_remove = _read_epochs(config_path)

    save_env_value("DASHSCOPE_API_KEY", "dashscope-secret-a")
    after_restore = _read_epochs(config_path)

    _assert_both_epochs_strictly_increase(
        [after_set, after_remove, after_restore]
    )


def test_same_dashscope_env_save_does_not_advance_capability_epochs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _use_isolated_home(monkeypatch, tmp_path)
    config_path.write_text(
        yaml.safe_dump(_capability_config()),
        encoding="utf-8",
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    save_env_value("DASHSCOPE_API_KEY", "dashscope-secret-a")
    after_first_save = _read_epochs(config_path)

    save_env_value("DASHSCOPE_API_KEY", "dashscope-secret-a")
    after_same_value_save = _read_epochs(config_path)

    assert after_same_value_save == after_first_save


def test_missing_dashscope_env_remove_does_not_advance_capability_epochs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _use_isolated_home(monkeypatch, tmp_path)
    config_path.write_text(
        yaml.safe_dump(_capability_config()),
        encoding="utf-8",
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    before = _read_epochs(config_path)

    assert remove_env_value("DASHSCOPE_API_KEY") is False

    assert _read_epochs(config_path) == before


def test_unrelated_env_save_and_remove_do_not_advance_capability_epochs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _use_isolated_home(monkeypatch, tmp_path)
    config_path.write_text(
        yaml.safe_dump(_capability_config()),
        encoding="utf-8",
    )
    monkeypatch.delenv("TENOR_API_KEY", raising=False)

    save_env_value("TENOR_API_KEY", "unrelated-secret")
    after_set = _read_epochs(config_path)
    assert remove_env_value("TENOR_API_KEY") is True
    after_remove = _read_epochs(config_path)

    assert after_set == (0, 0)
    assert after_remove == (0, 0)
