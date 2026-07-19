from __future__ import annotations

import inspect
import io
from types import SimpleNamespace

import pytest
import yaml

import agent.onboarding as onboarding_mod
import gateway.platforms.yuanbao as yuanbao_mod
import gateway.platforms.telegram as telegram_mod
import gateway.run as gateway_run
import tui_gateway.server as tui_server
from agent.onboarding import BUSY_INPUT_FLAG
from gateway.config import Platform


_EPOCHS_KEY = "_taiji_capability_epochs"
_INCARNATION_KEY = "_taiji_profile_incarnation"


def _config(*, vision_epoch: int, image_epoch: int, incarnation: str) -> dict:
    return {
        "display": {
            "tool_progress_command": True,
            "tool_progress": "all",
        },
        _EPOCHS_KEY: {
            "vision": vision_epoch,
            "image_generation": image_epoch,
        },
        _INCARNATION_KEY: incarnation,
    }


def _write_config(path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _assert_current_metadata(path) -> None:
    saved = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert saved[_EPOCHS_KEY] == {
        "vision": 41,
        "image_generation": 53,
    }
    assert saved[_INCARNATION_KEY] == "incarnation-current"


def test_onboarding_mark_seen_uses_latest_locked_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    current = _config(
        vision_epoch=41,
        image_epoch=53,
        incarnation="incarnation-current",
    )
    stale = _config(
        vision_epoch=3,
        image_epoch=5,
        incarnation="incarnation-stale",
    )
    _write_config(config_path, current)
    stale_text = yaml.safe_dump(stale, sort_keys=False)

    monkeypatch.setattr(
        onboarding_mod,
        "open",
        lambda *_args, **_kwargs: io.StringIO(stale_text),
        raising=False,
    )

    assert onboarding_mod.mark_seen(config_path, BUSY_INPUT_FLAG) is True

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert saved["onboarding"]["seen"][BUSY_INPUT_FLAG] is True
    _assert_current_metadata(config_path)


@pytest.mark.asyncio
async def test_yuanbao_auto_sethome_uses_latest_locked_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    current = _config(
        vision_epoch=41,
        image_epoch=53,
        incarnation="incarnation-current",
    )
    stale = _config(
        vision_epoch=7,
        image_epoch=11,
        incarnation="incarnation-stale",
    )
    _write_config(config_path, current)
    stale_text = yaml.safe_dump(stale, sort_keys=False)

    monkeypatch.delenv("YUANBAO_HOME_CHANNEL", raising=False)
    monkeypatch.setattr(
        "hermes_constants.get_hermes_home",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        yuanbao_mod,
        "open",
        lambda *_args, **_kwargs: io.StringIO(stale_text),
        raising=False,
    )
    adapter = SimpleNamespace(
        _auto_sethome_done=False,
        name="yuanbao-test",
    )
    ctx = yuanbao_mod.InboundContext(
        adapter=adapter,
        chat_id="dm:user-1",
        chat_type="dm",
        chat_name="User One",
    )
    next_called = False

    async def _next() -> None:
        nonlocal next_called
        next_called = True

    await yuanbao_mod.AutoSetHomeMiddleware().handle(ctx, _next)

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert saved["YUANBAO_HOME_CHANNEL"] == "dm:user-1"
    assert next_called is True
    _assert_current_metadata(config_path)


@pytest.mark.asyncio
async def test_gateway_verbose_uses_latest_locked_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    current = _config(
        vision_epoch=41,
        image_epoch=53,
        incarnation="incarnation-current",
    )
    stale = _config(
        vision_epoch=13,
        image_epoch=17,
        incarnation="incarnation-stale",
    )
    _write_config(config_path, current)

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: yaml.safe_load(yaml.safe_dump(stale)),
    )
    runner = object.__new__(gateway_run.GatewayRunner)
    event = SimpleNamespace(
        source=SimpleNamespace(platform=Platform.TELEGRAM),
    )

    result = await runner._handle_verbose_command(event)

    assert "VERBOSE" in result
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert (
        saved["display"]["platforms"]["telegram"]["tool_progress"]
        == "verbose"
    )
    _assert_current_metadata(config_path)


@pytest.mark.asyncio
async def test_gateway_codex_runtime_uses_latest_locked_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    current = _config(
        vision_epoch=41,
        image_epoch=53,
        incarnation="incarnation-current",
    )
    current["model"] = {"openai_runtime": "codex_app_server"}
    current["unrelated"] = {"generation": 2}
    stale = _config(
        vision_epoch=3,
        image_epoch=5,
        incarnation="incarnation-stale",
    )
    stale["model"] = {"openai_runtime": "codex_app_server"}
    stale["unrelated"] = {"generation": 1}
    _write_config(config_path, current)

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: yaml.safe_load(yaml.safe_dump(stale)),
    )
    runner = object.__new__(gateway_run.GatewayRunner)
    event = SimpleNamespace(
        get_command_args=lambda: "auto",
        source=SimpleNamespace(platform=Platform.TELEGRAM),
    )

    result = await runner._handle_codex_runtime_command(event)

    assert result.startswith("✓")
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert saved["model"]["openai_runtime"] == "auto"
    assert saved["unrelated"] == {"generation": 2}
    _assert_current_metadata(config_path)


def test_gateway_config_commands_do_not_write_loaded_snapshots() -> None:
    handlers = (
        gateway_run.GatewayRunner._handle_personality_command,
        gateway_run.GatewayRunner._handle_reasoning_command,
        gateway_run.GatewayRunner._handle_fast_command,
        gateway_run.GatewayRunner._handle_verbose_command,
        gateway_run.GatewayRunner._handle_footer_command,
        gateway_run.GatewayRunner._handle_model_command,
        gateway_run.GatewayRunner._handle_codex_runtime_command,
    )

    for handler in handlers:
        source = inspect.getsource(handler)
        assert "atomic_yaml_write" not in source
        assert "save_config(" not in source


def test_tui_config_commands_do_not_write_loaded_snapshots() -> None:
    model_source = inspect.getsource(tui_server._persist_model_switch)
    tools_source = inspect.getsource(tui_server._methods["tools.configure"])

    assert "save_config(" not in model_source
    assert "_mutate_cfg" in model_source
    assert "save_config(" not in tools_source
    assert "_mutate_cfg" in tools_source


def test_tui_model_switch_uses_latest_locked_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    current = _config(
        vision_epoch=41,
        image_epoch=53,
        incarnation="incarnation-current",
    )
    current["concurrent_sentinel"] = "keep-me"
    current["model"] = {"base_url": "https://stale.example.test"}
    stale = _config(
        vision_epoch=3,
        image_epoch=5,
        incarnation="incarnation-stale",
    )
    _write_config(config_path, current)

    monkeypatch.setattr(tui_server, "_hermes_home", tmp_path)
    with tui_server._cfg_lock:
        tui_server._cfg_cache = stale
        tui_server._cfg_mtime = config_path.stat().st_mtime
        tui_server._cfg_path = config_path

    tui_server._persist_model_switch(
        SimpleNamespace(
            new_model="model-new",
            target_provider="provider-new",
            base_url="",
        )
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert saved["model"] == {
        "default": "model-new",
        "provider": "provider-new",
    }
    assert saved["concurrent_sentinel"] == "keep-me"
    _assert_current_metadata(config_path)


def test_gateway_model_switch_uses_latest_locked_config(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    current = _config(
        vision_epoch=41,
        image_epoch=53,
        incarnation="incarnation-current",
    )
    current["concurrent_sentinel"] = "keep-me"
    current["model"] = "legacy-model"
    _write_config(config_path, current)

    gateway_run._persist_gateway_model_switch(
        SimpleNamespace(
            new_model="model-new",
            target_provider="provider-new",
            base_url="https://provider.example.test/v1",
        ),
        config_path=config_path,
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert saved["model"] == {
        "default": "model-new",
        "provider": "provider-new",
        "base_url": "https://provider.example.test/v1",
    }
    assert saved["concurrent_sentinel"] == "keep-me"
    _assert_current_metadata(config_path)


def test_tui_config_key_write_uses_latest_locked_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    current = _config(
        vision_epoch=41,
        image_epoch=53,
        incarnation="incarnation-current",
    )
    current["concurrent_sentinel"] = "keep-me"
    stale = _config(
        vision_epoch=3,
        image_epoch=5,
        incarnation="incarnation-stale",
    )
    _write_config(config_path, current)

    monkeypatch.setattr(tui_server, "_hermes_home", tmp_path)
    with tui_server._cfg_lock:
        tui_server._cfg_cache = stale
        tui_server._cfg_mtime = config_path.stat().st_mtime
        tui_server._cfg_path = config_path

    tui_server._write_config_key("display.tui_statusbar", "bottom")

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert saved["display"]["tui_statusbar"] == "bottom"
    assert saved["concurrent_sentinel"] == "keep-me"
    _assert_current_metadata(config_path)


def test_telegram_dm_topic_persistence_uses_latest_locked_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    current = _config(
        vision_epoch=41,
        image_epoch=53,
        incarnation="incarnation-current",
    )
    current["concurrent_sentinel"] = "keep-me"
    stale = _config(
        vision_epoch=3,
        image_epoch=5,
        incarnation="incarnation-stale",
    )
    _write_config(config_path, current)
    stale_text = yaml.safe_dump(stale, sort_keys=False)

    monkeypatch.setattr(
        "hermes_constants.get_hermes_home",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        telegram_mod,
        "open",
        lambda *_args, **_kwargs: io.StringIO(stale_text),
        raising=False,
    )

    telegram_mod.TelegramAdapter._persist_dm_topic_thread_id(
        SimpleNamespace(name="telegram-test"),
        chat_id=123,
        topic_name="General",
        thread_id=456,
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert saved["platforms"]["telegram"]["extra"]["dm_topics"] == [
        {
            "chat_id": 123,
            "topics": [{"name": "General", "thread_id": 456}],
        }
    ]
    assert saved["concurrent_sentinel"] == "keep-me"
    _assert_current_metadata(config_path)
