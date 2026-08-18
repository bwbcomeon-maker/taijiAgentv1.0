"""Regression tests for settings durability and onboarding completion races."""

from __future__ import annotations

import copy
import threading
from pathlib import Path

import pytest

import api.config as config
import api.onboarding as onboarding


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    settings_file = tmp_path / "webui" / "settings.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(config, "DEFAULT_WORKSPACE", workspace)
    monkeypatch.setattr(config, "get_effective_default_model", lambda: "test/model")
    monkeypatch.setattr(config, "get_config", lambda: {})
    monkeypatch.setattr(
        config,
        "resolve_default_workspace",
        lambda value=None: Path(value or workspace),
    )
    monkeypatch.setattr(
        "api.auth._invalidate_password_hash_cache",
        lambda: None,
    )

    # Completion ownership is process-local and must be reset with the isolated
    # settings file so this module is independent of test order.
    monkeypatch.setattr(config, "_settings_completion_generation", 0, raising=False)
    monkeypatch.setattr(config, "_settings_completion_current", None, raising=False)
    monkeypatch.setattr(
        config,
        "_settings_completion_committed_generation",
        0,
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "_settings_completion_invalidated_generation",
        0,
        raising=False,
    )
    return settings_file


def test_save_settings_keeps_input_and_commits_with_same_directory_atomic_replace(
    monkeypatch, isolated_settings
):
    replace_calls: list[tuple[Path, Path]] = []
    fsync_calls: list[int] = []
    original_replace = config.os.replace
    original_fsync = config.os.fsync

    def record_replace(source, target):
        replace_calls.append((Path(source), Path(target)))
        return original_replace(source, target)

    def record_fsync(descriptor):
        fsync_calls.append(descriptor)
        return original_fsync(descriptor)

    monkeypatch.setattr(config.os, "replace", record_replace)
    monkeypatch.setattr(config.os, "fsync", record_fsync)

    updates = {
        "_clear_password": True,
        "send_key": "ctrl+enter",
        "dashboard_plugins": {"local-demo": True},
    }
    original_updates = copy.deepcopy(updates)

    saved = config.save_settings(updates)

    assert updates == original_updates
    assert saved["send_key"] == "ctrl+enter"
    assert replace_calls == [
        (replace_calls[0][0], isolated_settings),
    ]
    assert replace_calls[0][0].parent == isolated_settings.parent
    assert replace_calls[0][0] != isolated_settings
    assert len(fsync_calls) >= 2, "file and parent directory must both be fsynced"
    assert list(isolated_settings.parent.glob("*.tmp")) == []


def test_concurrent_save_settings_serializes_read_modify_write_without_lost_updates(
    monkeypatch, isolated_settings
):
    config.save_settings({"send_key": "enter", "language": "zh"})
    original_load = config.load_settings
    first_read = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    errors: list[BaseException] = []

    def controlled_load():
        snapshot = original_load()
        if threading.current_thread().name == "settings-first":
            first_read.set()
            if not release_first.wait(timeout=3):
                raise AssertionError("timed out waiting to release first settings writer")
        return snapshot

    monkeypatch.setattr(config, "load_settings", controlled_load)

    def first_writer():
        try:
            config.save_settings({"send_key": "ctrl+enter"})
        except BaseException as error:  # surfaced on the main test thread below
            errors.append(error)

    def second_writer():
        try:
            config.save_settings({"language": "fr"})
        except BaseException as error:  # surfaced on the main test thread below
            errors.append(error)
        finally:
            second_finished.set()

    first = threading.Thread(target=first_writer, name="settings-first")
    second = threading.Thread(target=second_writer, name="settings-second")
    first.start()
    assert first_read.wait(timeout=2)
    second.start()

    # The broken implementation lets writer two finish from the same stale
    # snapshot. The fixed implementation keeps it outside the shared lock until
    # writer one commits; either way, release without relying on a sleep race.
    second_finished.wait(timeout=0.25)
    release_first.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    saved = original_load()
    assert saved["send_key"] == "ctrl+enter"
    assert saved["language"] == "fr"


def test_late_failed_completion_cannot_rollback_newer_success_or_drop_settings(
    monkeypatch, isolated_settings
):
    ready = {
        "schema_version": "taiji-setup-status/v1",
        "installed_production": True,
        "overall_ready": True,
        "items": [],
    }
    failed_projection_entered = threading.Event()
    release_failed_projection = threading.Event()
    results: dict[str, dict] = {}
    errors: dict[str, BaseException] = {}

    monkeypatch.setattr(onboarding, "get_setup_status", lambda: ready)

    def controlled_projection(*, allow_config_auto_complete=True):
        assert allow_config_auto_complete is False
        if threading.current_thread().name == "completion-fails":
            failed_projection_entered.set()
            if not release_failed_projection.wait(timeout=3):
                raise AssertionError("timed out waiting to release failed projection")
            raise RuntimeError("projection failed")
        return {"completed": True, "preflight": ready}

    monkeypatch.setattr(onboarding, "get_onboarding_status", controlled_projection)

    def complete(name: str):
        try:
            results[name] = onboarding.complete_onboarding()
        except BaseException as error:  # surfaced on the main test thread below
            errors[name] = error

    failed = threading.Thread(
        target=complete,
        args=("failed",),
        name="completion-fails",
    )
    successful = threading.Thread(
        target=complete,
        args=("successful",),
        name="completion-succeeds",
    )

    failed.start()
    assert failed_projection_entered.wait(timeout=2)

    # This ordinary settings write must survive both completion requests.
    config.save_settings({"send_key": "ctrl+enter"})
    successful.start()
    successful.join(timeout=3)
    assert not successful.is_alive()
    assert results["successful"]["completed"] is True

    release_failed_projection.set()
    failed.join(timeout=3)
    assert not failed.is_alive()
    assert isinstance(errors.get("failed"), RuntimeError)

    saved = config.load_settings()
    assert saved["onboarding_completed"] is True
    assert saved["send_key"] == "ctrl+enter"
    assert not any(
        "completion_token" in key or "completion_generation" in key
        for key in saved
    )


def test_explicit_invalidation_prevents_older_completion_commit_from_reviving_flag(
    monkeypatch, isolated_settings
):
    ready = {
        "schema_version": "taiji-setup-status/v1",
        "installed_production": True,
        "overall_ready": True,
        "items": [],
    }
    projection_entered = threading.Event()
    release_projection = threading.Event()
    projection_calls: list[bool] = []
    result: dict[str, dict] = {}
    errors: list[BaseException] = []

    monkeypatch.setattr(onboarding, "get_setup_status", lambda: ready)

    def controlled_projection(*, allow_config_auto_complete=True):
        assert allow_config_auto_complete is False
        projection_calls.append(allow_config_auto_complete)
        if len(projection_calls) == 1:
            projection_entered.set()
            if not release_projection.wait(timeout=3):
                raise AssertionError("timed out waiting to release completion projection")
            return {"completed": True, "preflight": ready}
        return {
            "completed": config.load_settings()["onboarding_completed"],
            "preflight": ready,
        }

    monkeypatch.setattr(onboarding, "get_onboarding_status", controlled_projection)

    def complete():
        try:
            result["response"] = onboarding.complete_onboarding()
        except BaseException as error:  # surfaced on the main test thread below
            errors.append(error)

    completing = threading.Thread(target=complete, name="completion-invalidated")
    completing.start()
    assert projection_entered.wait(timeout=2)

    # A later explicit reset owns the setting. The older request must neither
    # revive the persisted flag nor return its now-stale successful projection.
    config.save_settings({"onboarding_completed": False})
    release_projection.set()
    completing.join(timeout=3)

    assert not completing.is_alive()
    assert errors == []
    assert len(projection_calls) == 2
    assert result["response"]["completed"] is False
    assert config.load_settings()["onboarding_completed"] is False


def test_older_success_is_preserved_when_newer_completion_rolls_back(
    isolated_settings,
):
    older = config._begin_onboarding_completion()
    newer = config._begin_onboarding_completion()

    # The older success no longer owns the active marker, so it must not clear
    # the newer request. It still records a committed generation so the newer
    # failure's compensating rollback keeps the successful completion.
    assert config._commit_onboarding_completion(older) is False
    assert config._rollback_onboarding_completion(newer) is True

    assert config.load_settings()["onboarding_completed"] is True
    assert config._settings_completion_current is None


def test_older_success_response_and_flag_survive_newer_request_failure(
    monkeypatch, isolated_settings
):
    ready = {
        "schema_version": "taiji-setup-status/v1",
        "installed_production": True,
        "overall_ready": True,
        "items": [],
    }
    older_projection_entered = threading.Event()
    newer_projection_entered = threading.Event()
    release_older_projection = threading.Event()
    release_newer_projection = threading.Event()
    results: dict[str, dict] = {}
    errors: dict[str, BaseException] = {}

    monkeypatch.setattr(onboarding, "get_setup_status", lambda: ready)

    def controlled_projection(*, allow_config_auto_complete=True):
        assert allow_config_auto_complete is False
        if threading.current_thread().name == "completion-older-success":
            older_projection_entered.set()
            if not release_older_projection.wait(timeout=3):
                raise AssertionError("timed out waiting to release older completion")
            return {"completed": True, "preflight": ready}
        newer_projection_entered.set()
        if not release_newer_projection.wait(timeout=3):
            raise AssertionError("timed out waiting to release newer completion")
        raise RuntimeError("newer projection failed")

    monkeypatch.setattr(onboarding, "get_onboarding_status", controlled_projection)

    def complete(name: str):
        try:
            results[name] = onboarding.complete_onboarding()
        except BaseException as error:  # surfaced on the main test thread below
            errors[name] = error

    older = threading.Thread(
        target=complete,
        args=("older",),
        name="completion-older-success",
    )
    newer = threading.Thread(
        target=complete,
        args=("newer",),
        name="completion-newer-failure",
    )

    older.start()
    assert older_projection_entered.wait(timeout=2)
    newer.start()
    assert newer_projection_entered.wait(timeout=2)

    # Let the older success commit while the newer marker still owns the CAS.
    release_older_projection.set()
    older.join(timeout=3)
    assert not older.is_alive()
    assert results["older"]["completed"] is True

    # The newer request fails afterwards. Its rollback must preserve both the
    # successful response contract and the persisted completion flag.
    release_newer_projection.set()
    newer.join(timeout=3)
    assert not newer.is_alive()
    assert isinstance(errors.get("newer"), RuntimeError)
    assert config.load_settings()["onboarding_completed"] is True
