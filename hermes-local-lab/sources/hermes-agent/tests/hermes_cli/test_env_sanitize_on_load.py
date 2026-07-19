"""Tests for .env sanitization during load to prevent token duplication (#8908)."""

import tempfile
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from agent.image_gen_verification import (
    CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    CAPABILITY_CONFIG_EPOCH_VISION,
    CAPABILITY_CONFIG_EPOCHS_KEY,
    capability_config_epoch,
)


def test_load_env_sanitizes_concatenated_lines():
    """Verify load_env() splits concatenated KEY=VALUE pairs.

    Reproduces the scenario from #8908 where a corrupted .env file
    contained multiple tokens on a single line, causing the bot token
    to be duplicated 8 times.
    """
    from hermes_cli.config import load_env

    token = "0123456789:test"
    # Simulate concatenated line: TOKEN=xxx followed immediately by another key
    corrupted = f"TELEGRAM_BOT_TOKEN={token}ANTHROPIC_API_KEY=sk-ant-test123\n"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8"
    ) as f:
        f.write(corrupted)
        env_path = Path(f.name)

    try:
        with patch("hermes_cli.config.get_env_path", return_value=env_path):
            result = load_env()
        assert result.get("TELEGRAM_BOT_TOKEN") == token, (
            f"Token should be exactly '{token}', got '{result.get('TELEGRAM_BOT_TOKEN')}'"
        )
        assert result.get("ANTHROPIC_API_KEY") == "sk-ant-test123"
    finally:
        env_path.unlink(missing_ok=True)


def test_load_env_normal_file_unchanged():
    """A well-formed .env file should be parsed identically."""
    from hermes_cli.config import load_env

    content = (
        "TELEGRAM_BOT_TOKEN=mytoken123\n"
        "ANTHROPIC_API_KEY=sk-ant-key\n"
        "# comment\n"
        "\n"
        "OPENAI_API_KEY=sk-openai\n"
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        env_path = Path(f.name)

    try:
        with patch("hermes_cli.config.get_env_path", return_value=env_path):
            result = load_env()
        assert result["TELEGRAM_BOT_TOKEN"] == "mytoken123"
        assert result["ANTHROPIC_API_KEY"] == "sk-ant-key"
        assert result["OPENAI_API_KEY"] == "sk-openai"
    finally:
        env_path.unlink(missing_ok=True)


def test_env_loader_sanitizes_before_dotenv(tmp_path: Path):
    """Verify env_loader._sanitize_env_file_if_needed fixes corrupted files."""
    from hermes_cli.env_loader import _sanitize_env_file_if_needed

    token = "0123456789:test"
    corrupted = f"TELEGRAM_BOT_TOKEN={token}ANTHROPIC_API_KEY=sk-ant-test\n"

    env_path = tmp_path / ".env"
    env_path.write_text(corrupted, encoding="utf-8")

    try:
        _sanitize_env_file_if_needed(env_path)
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
        # Should be split into two separate lines
        assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}: {lines}"
        assert lines[0].startswith("TELEGRAM_BOT_TOKEN=")
        assert lines[1].startswith("ANTHROPIC_API_KEY=")
        # Token should not contain the second key
        parsed_token = lines[0].strip().split("=", 1)[1]
        assert parsed_token == token
    finally:
        env_path.unlink(missing_ok=True)


def test_user_env_startup_sanitize_advances_active_capability_epochs(
    tmp_path: Path,
) -> None:
    from hermes_cli.env_loader import _sanitize_env_file_if_needed

    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
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
                "_taiji_profile_incarnation": "incarnation-current",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    env_path.write_text(
        "DASHSCOPE_API_KEY=secret-aTENOR_API_KEY=old\n",
        encoding="utf-8",
    )

    _sanitize_env_file_if_needed(
        env_path,
        config_path=config_path,
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert capability_config_epoch(
        saved,
        CAPABILITY_CONFIG_EPOCH_VISION,
    ) > 17
    assert capability_config_epoch(
        saved,
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    ) > 23
    assert env_path.read_text(encoding="utf-8") == (
        "DASHSCOPE_API_KEY=secret-a\nTENOR_API_KEY=old\n"
    )
    assert saved["_taiji_profile_incarnation"] == "incarnation-current"
    if os.name != "nt":
        assert env_path.stat().st_mode & 0o777 == 0o600

    stable_epochs = saved[CAPABILITY_CONFIG_EPOCHS_KEY].copy()
    _sanitize_env_file_if_needed(
        env_path,
        config_path=config_path,
    )
    saved_again = yaml.safe_load(
        config_path.read_text(encoding="utf-8")
    ) or {}
    assert saved_again[CAPABILITY_CONFIG_EPOCHS_KEY] == stable_epochs


def test_project_env_is_sanitized_in_memory_without_mutating_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermes_cli import env_loader

    hermes_home = tmp_path / "home"
    project_env = tmp_path / "project.env"
    hermes_home.mkdir()
    original = (
        "TELEGRAM_BOT_TOKEN=0123456789:test"
        "ANTHROPIC_API_KEY=sk-ant-test\n"
    )
    project_env.write_text(original, encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        env_loader,
        "_apply_external_secret_sources",
        lambda _home: None,
    )

    loaded = env_loader.load_hermes_dotenv(
        hermes_home=hermes_home,
        project_env=project_env,
    )

    assert loaded == [project_env]
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "0123456789:test"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert project_env.read_text(encoding="utf-8") == original


def test_startup_sanitize_cannot_lose_concurrent_credential_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.provider_credentials import mutate_env_unique
    from hermes_cli import config as config_mod
    from hermes_cli.env_loader import _sanitize_env_file_if_needed

    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text("{}\n", encoding="utf-8")
    env_path.write_text(
        "DASHSCOPE_API_KEY=secret-aTENOR_API_KEY=old\n",
        encoding="utf-8",
    )

    real_sanitize = config_mod._sanitize_env_lines
    worker_started = threading.Event()
    worker_done = threading.Event()
    worker_errors: list[BaseException] = []
    worker: threading.Thread | None = None
    injected = False

    def _write_concurrently() -> None:
        worker_started.set()
        try:
            mutate_env_unique(
                {"CONCURRENT_SENTINEL": "keep-me"},
                config_path=config_path,
            )
        except BaseException as exc:  # pragma: no cover - assertion below
            worker_errors.append(exc)
        finally:
            worker_done.set()

    def _sanitize_with_concurrent_writer(lines: list[str]) -> list[str]:
        nonlocal worker, injected
        if not injected:
            injected = True
            worker = threading.Thread(target=_write_concurrently)
            worker.start()
            assert worker_started.wait(timeout=1)
            # The canonical writer must remain blocked while the sanitizer
            # owns the shared transaction.
            assert worker_done.wait(timeout=0.1) is False
        return real_sanitize(lines)

    monkeypatch.setattr(
        config_mod,
        "_sanitize_env_lines",
        _sanitize_with_concurrent_writer,
    )

    _sanitize_env_file_if_needed(
        env_path,
        config_path=config_path,
    )
    assert worker is not None
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert worker_errors == []
    assert env_path.read_text(encoding="utf-8") == (
        "DASHSCOPE_API_KEY=secret-a\n"
        "TENOR_API_KEY=old\n"
        "CONCURRENT_SENTINEL=keep-me\n"
    )
