"""Regression tests for atomic CLI global-setting persistence."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from hermes_cli.model_switch import ModelSwitchResult


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    config_path.write_text(
        "# keep this header\n"
        "model:\n"
        "  # keep the model note\n"
        "  default: old-model\n"
        "  provider: old-provider\n"
        "agent:\n"
        "  system_prompt: 你好，保持中文输出\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("cli._hermes_home", hermes_home)
    return config_path


def _switch_result() -> ModelSwitchResult:
    return ModelSwitchResult(
        success=True,
        new_model="new-model",
        target_provider="new-provider",
        provider_changed=True,
        provider_label="New Provider",
    )


def _switch_stub():
    return SimpleNamespace(
        agent=None,
        model="old-model",
        provider="old-provider",
        requested_provider="old-provider",
        api_key="",
        _explicit_api_key="",
        base_url="",
        _explicit_base_url="",
        api_mode="",
        _pending_model_switch_note="",
    )


def test_save_config_values_commits_model_and_provider_together(
    isolated_config,
):
    from cli import save_config_values

    assert save_config_values(
        {
            "model.default": "new-model",
            "model.provider": "new-provider",
        }
    )

    text = isolated_config.read_text(encoding="utf-8")
    saved = yaml.safe_load(text)
    assert saved["model"] == {
        "default": "new-model",
        "provider": "new-provider",
    }
    assert saved["agent"]["system_prompt"] == "你好，保持中文输出"
    assert "# keep this header" in text
    assert "# keep the model note" in text
    assert "你好，保持中文输出" in text


def test_save_config_values_is_all_or_nothing_on_replace_failure(
    isolated_config,
    monkeypatch,
):
    original = isolated_config.read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("utils.atomic_replace", fail_replace)

    from cli import save_config_values

    assert (
        save_config_values(
            {
                "model.default": "new-model",
                "model.provider": "new-provider",
            }
        )
        is False
    )
    assert isolated_config.read_bytes() == original


def test_picker_global_failure_is_explicit_and_never_claims_saved(
    monkeypatch,
):
    import cli as cli_mod

    captured: list[str] = []
    persist = MagicMock(return_value=False)
    monkeypatch.setattr(cli_mod, "_cprint", lambda value, *a, **k: captured.append(str(value)))
    monkeypatch.setattr(cli_mod, "save_config_values", persist)

    cli_mod.HermesCLI._apply_model_switch_result(
        _switch_stub(),
        _switch_result(),
        True,
    )

    persist.assert_called_once_with(
        {
            "model.default": "new-model",
            "model.provider": "new-provider",
        }
    )
    output = "\n".join(captured)
    assert "Failed to save" in output
    assert "session only" in output
    assert "Saved to config.yaml" not in output


def test_typed_model_global_failure_is_explicit_and_never_claims_saved(
    monkeypatch,
):
    import cli as cli_mod

    captured: list[str] = []
    persist = MagicMock(return_value=False)
    monkeypatch.setattr(cli_mod, "_cprint", lambda value, *a, **k: captured.append(str(value)))
    monkeypatch.setattr(cli_mod, "save_config_values", persist)
    monkeypatch.setattr(
        "hermes_cli.model_switch.parse_model_flags",
        lambda _raw: ("new-model", "new-provider", True, False),
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.switch_model",
        lambda **_kwargs: _switch_result(),
    )
    monkeypatch.setattr(
        "hermes_cli.inventory.load_picker_context",
        MagicMock(side_effect=RuntimeError("no inventory")),
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.resolve_display_context_length",
        lambda *_args, **_kwargs: None,
    )

    cli_mod.HermesCLI._handle_model_switch(
        _switch_stub(),
        "/model new-model --provider new-provider --global",
    )

    persist.assert_called_once_with(
        {
            "model.default": "new-model",
            "model.provider": "new-provider",
        }
    )
    output = "\n".join(captured)
    assert "Failed to save" in output
    assert "session only" in output
    assert "Saved to config.yaml" not in output


@pytest.mark.parametrize(
    ("command", "starting_state", "expected_state"),
    [
        ("/reasoning show", False, True),
        ("/reasoning hide", True, False),
    ],
)
def test_reasoning_display_save_failure_is_reported_as_session_only(
    command,
    starting_state,
    expected_state,
    monkeypatch,
):
    import cli as cli_mod

    captured: list[str] = []
    stub = SimpleNamespace(
        reasoning_config=None,
        show_reasoning=starting_state,
        agent=None,
    )
    monkeypatch.setattr(cli_mod, "_cprint", lambda value, *a, **k: captured.append(str(value)))
    monkeypatch.setattr(cli_mod, "save_config_value", lambda *_args, **_kwargs: False)

    cli_mod.HermesCLI._handle_reasoning_command(stub, command)

    assert stub.show_reasoning is expected_state
    output = "\n".join(captured)
    assert "session only" in output
    assert "(saved)" not in output
