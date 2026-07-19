from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _write_dashscope_capability_config(config_path: Path) -> None:
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


def _capability_epochs(config_path: Path) -> tuple[int, int]:
    from agent.image_gen_verification import (
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        CAPABILITY_CONFIG_EPOCH_VISION,
        capability_config_epoch,
    )

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return (
        capability_config_epoch(config, CAPABILITY_CONFIG_EPOCH_VISION),
        capability_config_epoch(
            config,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        ),
    )


def _route_provider_writes_to(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
) -> None:
    import api.config as config
    import api.providers as providers

    monkeypatch.setattr(config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(providers, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(providers, "invalidate_models_cache", lambda: None)
    monkeypatch.setattr(providers, "reload_config", lambda: None)


def test_alibaba_provider_key_a_b_a_advances_both_capability_epochs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.providers as providers

    config_path = tmp_path / "config.yaml"
    _write_dashscope_capability_config(config_path)
    _route_provider_writes_to(monkeypatch, config_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    observed = []
    for secret in (
        "dashscope-secret-a",
        "dashscope-secret-b",
        "dashscope-secret-a",
    ):
        result = providers.set_provider_key("alibaba", secret)
        assert result["ok"] is True
        observed.append(_capability_epochs(config_path))

    assert observed == [(1, 1), (2, 2), (3, 3)]


def test_alibaba_provider_key_remove_restore_advances_both_capability_epochs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.providers as providers

    config_path = tmp_path / "config.yaml"
    _write_dashscope_capability_config(config_path)
    _route_provider_writes_to(monkeypatch, config_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    assert providers.set_provider_key(
        "alibaba",
        "dashscope-secret-a",
    )["ok"] is True
    after_set = _capability_epochs(config_path)

    assert providers.remove_provider_key("alibaba")["ok"] is True
    after_remove = _capability_epochs(config_path)

    assert providers.set_provider_key(
        "alibaba",
        "dashscope-secret-a",
    )["ok"] is True
    after_restore = _capability_epochs(config_path)

    assert (after_set, after_remove, after_restore) == (
        (1, 1),
        (2, 2),
        (3, 3),
    )


def test_unrelated_provider_key_does_not_advance_dashscope_capability_epochs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.providers as providers

    config_path = tmp_path / "config.yaml"
    _write_dashscope_capability_config(config_path)
    _route_provider_writes_to(monkeypatch, config_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    result = providers.set_provider_key(
        "deepseek",
        "deepseek-secret-a",
    )

    assert result["ok"] is True
    assert _capability_epochs(config_path) == (0, 0)
