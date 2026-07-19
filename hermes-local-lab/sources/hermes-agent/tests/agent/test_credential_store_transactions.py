from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import agent.provider_credentials as credentials
import pytest
from agent.provider_credentials import (
    CredentialRecoveryError,
    CredentialSnapshot,
    load_credential_snapshot,
    mutate_config_strict,
    mutate_config_env_strict,
    mutate_env_unique,
    recover_credential_transaction,
)


def test_snapshot_reads_exact_disk_state_without_exposing_secret(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir(parents=True)
    config_bytes = b"provider: test\n"
    env_bytes = b"API_KEY=disk-secret\n"
    config_path.write_bytes(config_bytes)
    env_path.write_bytes(env_bytes)
    monkeypatch.setenv("API_KEY", "stale-process-secret")

    snapshot = load_credential_snapshot(config_path)

    assert isinstance(snapshot, CredentialSnapshot)
    assert snapshot.config == {"provider": "test"}
    assert snapshot.env == {"API_KEY": "disk-secret"}
    assert snapshot.config_sha256 == hashlib.sha256(config_bytes).hexdigest()
    assert snapshot.env_sha256 == hashlib.sha256(env_bytes).hexdigest()
    assert snapshot.config_exists is True
    assert snapshot.env_exists is True
    assert "disk-secret" not in repr(snapshot)
    assert "stale-process-secret" not in repr(snapshot)


def test_snapshot_hash_and_config_are_parsed_from_the_same_captured_bytes(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    captured = b"source: captured-bytes\n"
    config_path.write_bytes(captured)
    original_read_text = Path.read_text

    def return_different_path_read(path, *args, **kwargs):
        if path == config_path:
            return "source: later-path-read\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", return_different_path_read)

    snapshot = load_credential_snapshot(config_path)

    assert snapshot.config == {"source": "captured-bytes"}
    assert snapshot.config_sha256 == hashlib.sha256(captured).hexdigest()


def test_strict_config_mutation_rejects_duplicate_yaml_without_writing(tmp_path):
    config_path = tmp_path / "profile" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    original = b"provider: first\nprovider: second\n"
    config_path.write_bytes(original)

    def mutate(config):
        config["provider"] = "changed"

    try:
        mutate_config_strict(mutate, config_path=config_path)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:  # pragma: no cover - makes a silent fail-open explicit.
        raise AssertionError("duplicate YAML was accepted")

    assert config_path.read_bytes() == original


def test_env_mutation_repairs_target_duplicates_and_preserves_other_lines(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir(parents=True)
    env_path.write_text(
        "# retained\n"
        "API_KEY=old-first\n"
        "UNRELATED=value\n"
        "export API_KEY=old-second\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("API_KEY", "stale-process-value")

    applied = mutate_env_unique(
        {"API_KEY": "fresh-secret"},
        config_path=config_path,
    )

    written = env_path.read_text(encoding="utf-8")
    assert applied == {"API_KEY": True}
    assert written.count("API_KEY=") == 1
    assert "API_KEY=fresh-secret\n" in written
    assert "# retained\n" in written
    assert "UNRELATED=value\n" in written
    assert os.environ["API_KEY"] == "fresh-secret"


def test_env_mutation_rejects_untouched_duplicate_without_writing(tmp_path):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir(parents=True)
    original = b"OTHER=first\nOTHER=second\n"
    env_path.write_bytes(original)

    try:
        mutate_env_unique({"API_KEY": "fresh"}, config_path=config_path)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:  # pragma: no cover - makes a silent fail-open explicit.
        raise AssertionError("untouched duplicate env key was accepted")

    assert env_path.read_bytes() == original


def test_pair_mutation_commits_both_files_and_removes_intent(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    monkeypatch.delenv("API_KEY", raising=False)

    snapshot = mutate_config_env_strict(
        lambda config: config.update(provider="after"),
        {"API_KEY": "after-secret"},
        config_path=config_path,
    )

    assert snapshot.config["provider"] == "after"
    assert snapshot.env["API_KEY"] == "after-secret"
    assert not (
        config_path.parent / ".taiji-credential-pair-intent.json"
    ).exists()
    assert not list(config_path.parent.glob(".taiji-credential-*.stage"))


def test_durable_intent_rolls_forward_after_crash_between_replaces(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    monkeypatch.delenv("API_KEY", raising=False)
    original_replace = credentials._replace_credential_stage

    def crash_after_config_replace(
        stage_path,
        *,
        logical_path,
        real_target,
        mode,
        **replace_options,
    ):
        if logical_path == config_path:
            intent_text = intent_path.read_text(encoding="utf-8")
            assert "after-secret" not in intent_text
            assert stat.S_IMODE(intent_path.stat().st_mode) == 0o600
            stages = list(
                config_path.parent.glob(".taiji-credential-*.stage")
            )
            assert len(stages) == 2
            assert all(
                stat.S_IMODE(stage.stat().st_mode) == 0o600
                for stage in stages
            )
            original_replace(
                stage_path,
                logical_path=logical_path,
                real_target=real_target,
                mode=mode,
                **replace_options,
            )
            raise SystemExit("simulated process crash")
        return original_replace(
            stage_path,
            logical_path=logical_path,
            real_target=real_target,
            mode=mode,
            **replace_options,
        )

    monkeypatch.setattr(
        credentials,
        "_replace_credential_stage",
        crash_after_config_replace,
    )
    with pytest.raises(SystemExit, match="simulated process crash"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=config_path,
        )

    assert "provider: after" in config_path.read_text(encoding="utf-8")
    assert env_path.read_text(encoding="utf-8") == "API_KEY=before\n"
    assert intent_path.exists()

    monkeypatch.setattr(
        credentials,
        "_replace_credential_stage",
        original_replace,
    )
    assert recover_credential_transaction(config_path) == "recovered"
    snapshot = load_credential_snapshot(config_path)
    assert snapshot.config["provider"] == "after"
    assert snapshot.env["API_KEY"] == "after-secret"
    assert os.environ["API_KEY"] == "after-secret"
    assert not intent_path.exists()
    assert not list(config_path.parent.glob(".taiji-credential-*.stage"))


def test_recovery_fails_closed_and_keeps_intent_for_unknown_target_state(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_replace = credentials._replace_credential_stage

    def crash_after_config_replace(
        stage_path,
        *,
        logical_path,
        real_target,
        mode,
        **replace_options,
    ):
        result = original_replace(
            stage_path,
            logical_path=logical_path,
            real_target=real_target,
            mode=mode,
            **replace_options,
        )
        if logical_path == config_path:
            raise SystemExit("simulated process crash")
        return result

    monkeypatch.setattr(
        credentials,
        "_replace_credential_stage",
        crash_after_config_replace,
    )
    with pytest.raises(SystemExit):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=config_path,
        )
    monkeypatch.setattr(
        credentials,
        "_replace_credential_stage",
        original_replace,
    )
    env_path.write_text("API_KEY=externally-tampered\n", encoding="utf-8")

    with pytest.raises(CredentialRecoveryError, match="unknown state"):
        recover_credential_transaction(config_path)

    assert intent_path.exists()
    assert env_path.read_text(encoding="utf-8") == (
        "API_KEY=externally-tampered\n"
    )
    assert "provider: after" in config_path.read_text(encoding="utf-8")


def test_pair_does_not_publish_intent_when_stage_directory_sync_fails(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")

    def fail_directory_sync(_path):
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(
        credentials,
        "_fsync_directory",
        fail_directory_sync,
    )
    with pytest.raises(OSError, match="directory fsync failure"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=config_path,
        )

    assert not intent_path.exists()
    assert config_path.read_text(encoding="utf-8") == "provider: before\n"


def test_mutation_resolves_alias_spec_once_and_never_locks_b_to_write_a(
    monkeypatch,
    tmp_path,
):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    alias_root = tmp_path / "alias"
    first_root.mkdir()
    second_root.mkdir()
    alias_root.mkdir()
    first_config = first_root / "config.yaml"
    second_config = second_root / "config.yaml"
    first_config.write_text("provider: first\n", encoding="utf-8")
    second_config.write_text("provider: second\n", encoding="utf-8")
    first_env = first_root / ".env"
    second_env = second_root / ".env"
    first_env.write_text("API_KEY=first\n", encoding="utf-8")
    second_env.write_text("API_KEY=second\n", encoding="utf-8")
    alias_config = alias_root / "active.yaml"
    alias_config.symlink_to(first_config)
    original_spec = credentials._credential_transaction_spec
    spec_calls = 0

    def retarget_after_only_allowed_spec(path=None):
        nonlocal spec_calls
        spec = original_spec(path)
        spec_calls += 1
        if spec_calls == 1:
            alias_config.unlink()
            alias_config.symlink_to(second_config)
        return spec

    monkeypatch.setattr(
        credentials,
        "_credential_transaction_spec",
        retarget_after_only_allowed_spec,
    )

    with pytest.raises(CredentialRecoveryError, match="target changed"):
        mutate_env_unique(
            {"API_KEY": "must-not-write"},
            config_path=alias_config,
        )

    assert spec_calls == 1
    assert first_env.read_text(encoding="utf-8") == "API_KEY=first\n"
    assert second_env.read_text(encoding="utf-8") == "API_KEY=second\n"


def test_snapshot_never_combines_config_and_env_from_different_specs(
    monkeypatch,
    tmp_path,
):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    alias_root = tmp_path / "alias"
    first_root.mkdir()
    second_root.mkdir()
    alias_root.mkdir()
    first_config = first_root / "config.yaml"
    second_config = second_root / "config.yaml"
    first_config.write_text("provider: first\n", encoding="utf-8")
    second_config.write_text("provider: second\n", encoding="utf-8")
    (first_root / ".env").write_text(
        "PROFILE_ORIGIN=first\n",
        encoding="utf-8",
    )
    (second_root / ".env").write_text(
        "PROFILE_ORIGIN=second\n",
        encoding="utf-8",
    )
    alias_config = alias_root / "active.yaml"
    alias_config.symlink_to(first_config)
    original_spec = credentials._credential_transaction_spec
    spec_calls = 0

    def retarget_after_only_allowed_spec(path=None):
        nonlocal spec_calls
        spec = original_spec(path)
        spec_calls += 1
        if spec_calls == 1:
            alias_config.unlink()
            alias_config.symlink_to(second_config)
        return spec

    monkeypatch.setattr(
        credentials,
        "_credential_transaction_spec",
        retarget_after_only_allowed_spec,
    )

    with pytest.raises(CredentialRecoveryError, match="target changed"):
        load_credential_snapshot(alias_config)

    assert spec_calls == 1


@pytest.mark.parametrize(
    ("change_config", "change_env"),
    [
        pytest.param(True, False, id="config-only"),
        pytest.param(False, True, id="env-only"),
        pytest.param(True, True, id="config-and-env"),
    ],
)
def test_all_disk_change_combinations_keep_intent_on_projection_failure(
    monkeypatch,
    tmp_path,
    change_config,
    change_env,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_project = credentials._project_process_env_batch

    def fail_projection(_updates):
        raise RuntimeError("matrix projection failed")

    monkeypatch.setattr(
        credentials,
        "_project_process_env_batch",
        fail_projection,
    )

    def mutate_config(config):
        if change_config:
            config["provider"] = "after"

    with pytest.raises(RuntimeError, match="matrix projection failed"):
        mutate_config_env_strict(
            mutate_config,
            {"API_KEY": "after" if change_env else "before"},
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == (
        "provider: after\n" if change_config else "provider: before\n"
    )
    assert env_path.read_text(encoding="utf-8") == (
        "API_KEY=after\n" if change_env else "API_KEY=before\n"
    )
    assert intent_path.exists()

    monkeypatch.setattr(
        credentials,
        "_project_process_env_batch",
        original_project,
    )
    assert recover_credential_transaction(config_path) == "recovered"
    assert not intent_path.exists()


def test_projection_only_failure_has_retriable_durable_intent(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_project = credentials._project_process_env_batch

    def fail_projection(_updates):
        raise RuntimeError("projection-only failed")

    monkeypatch.setattr(
        credentials,
        "_project_process_env_batch",
        fail_projection,
    )

    with pytest.raises(RuntimeError, match="projection-only failed"):
        mutate_config_env_strict(
            lambda _config: None,
            {"API_KEY": "before"},
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == "provider: before\n"
    assert env_path.read_text(encoding="utf-8") == "API_KEY=before\n"
    assert intent_path.exists()

    monkeypatch.setattr(
        credentials,
        "_project_process_env_batch",
        original_project,
    )
    assert recover_credential_transaction(config_path) == "recovered"
    assert not intent_path.exists()
    assert env_path.read_text(encoding="utf-8") == "API_KEY=before\n"
    assert not list(config_path.parent.glob(".taiji-credential-*.stage"))


def test_committed_pair_intent_unlink_failure_returns_success_and_is_replayable(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_unlink = credentials._unlink_active_target
    failed = False

    def fail_first_intent_unlink(path, **kwargs):
        nonlocal failed
        if Path(path) == intent_path and not failed:
            failed = True
            raise OSError("simulated committed intent unlink failure")
        return original_unlink(path, **kwargs)

    monkeypatch.setattr(
        credentials,
        "_unlink_active_target",
        fail_first_intent_unlink,
    )

    snapshot = mutate_config_env_strict(
        lambda config: config.update(provider="after"),
        {"API_KEY": "after"},
        config_path=config_path,
    )

    assert snapshot.config["provider"] == "after"
    assert snapshot.env["API_KEY"] == "after"
    assert intent_path.exists()

    monkeypatch.setattr(
        credentials,
        "_unlink_active_target",
        original_unlink,
    )
    assert recover_credential_transaction(config_path) == "recovered"
    assert not intent_path.exists()


def test_committed_pair_final_directory_sync_failure_is_not_retryable_failure(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_sync = credentials._fsync_directory
    failed = False

    def fail_only_post_commit_sync(path):
        nonlocal failed
        if (
            not failed
            and Path(path) == config_path.parent
            and not intent_path.exists()
            and config_path.read_text(encoding="utf-8")
            == "provider: after\n"
            and env_path.read_text(encoding="utf-8") == "API_KEY=after\n"
        ):
            failed = True
            raise OSError("simulated post-commit directory sync failure")
        return original_sync(path)

    monkeypatch.setattr(
        credentials,
        "_fsync_directory",
        fail_only_post_commit_sync,
    )

    snapshot = mutate_config_env_strict(
        lambda config: config.update(provider="after"),
        {"API_KEY": "after"},
        config_path=config_path,
    )

    assert failed is True
    assert snapshot.config["provider"] == "after"
    assert snapshot.env["API_KEY"] == "after"


def test_single_file_parent_sync_failure_keeps_durable_intent(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    original_sync = credentials._fsync_directory
    failed = False

    def fail_after_single_replace(path):
        nonlocal failed
        if (
            not failed
            and Path(path) == config_path.parent
            and config_path.read_text(encoding="utf-8")
            == "provider: after\n"
        ):
            failed = True
            raise OSError("simulated single target directory sync failure")
        return original_sync(path)

    monkeypatch.setattr(
        credentials,
        "_fsync_directory",
        fail_after_single_replace,
    )

    with pytest.raises(OSError, match="single target directory sync"):
        mutate_config_strict(
            lambda config: config.update(provider="after"),
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == "provider: after\n"
    assert intent_path.exists()

    monkeypatch.setattr(
        credentials,
        "_fsync_directory",
        original_sync,
    )
    assert recover_credential_transaction(config_path) == "recovered"
    assert not intent_path.exists()


def test_stage_entry_swap_is_rejected_without_overwriting_target(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    original_exchange = getattr(
        credentials,
        "_atomic_exchange_entries",
        None,
    )
    injected = False

    def swap_stage_entry(source, target):
        nonlocal injected
        if not injected:
            attacker = config_path.parent / ".attacker-stage"
            attacker.write_text("provider: attacker\n", encoding="utf-8")
            os.replace(attacker, source)
            injected = True
        if original_exchange is None:
            raise AssertionError("atomic exchange primitive was not used")
        return original_exchange(source, target)

    monkeypatch.setattr(
        credentials,
        "_atomic_exchange_entries",
        swap_stage_entry,
        raising=False,
    )

    with pytest.raises(
        CredentialRecoveryError,
        match="stage changed|atomic exchange primitive",
    ):
        mutate_config_strict(
            lambda config: config.update(provider="after"),
            config_path=config_path,
        )

    assert injected is True
    assert config_path.read_text(encoding="utf-8") == "provider: before\n"


def test_late_target_retarget_during_failed_exchange_never_loses_external_payload(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    abort_path = (
        config_path.parent / ".taiji-credential-pair-abort.json"
    )
    external_payload = b"provider: external-late-retarget\n"
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    original_exchange = credentials._atomic_exchange_entries
    original_stat = credentials._stat_active_target
    stage_swapped = False
    late_retargeted = False

    def swap_stage_before_exchange(source, target):
        nonlocal stage_swapped
        if Path(target) == config_path and not stage_swapped:
            attacker = config_path.parent / ".attacker-stage"
            attacker.write_text("provider: attacker\n", encoding="utf-8")
            os.replace(attacker, source)
            stage_swapped = True
        return original_exchange(source, target)

    def retarget_after_post_target_stat(
        target_path,
        *,
        follow_symlinks=False,
    ):
        nonlocal late_retargeted
        result = original_stat(
            target_path,
            follow_symlinks=follow_symlinks,
        )
        exchange_expectation = getattr(
            credentials._CREDENTIAL_TRANSACTION_STATE,
            "exchange_expectation",
            None,
        )
        if (
            not late_retargeted
            and isinstance(exchange_expectation, tuple)
            and Path(target_path) == config_path
        ):
            replacement = config_path.parent / ".external-late-retarget"
            replacement.write_bytes(external_payload)
            os.replace(replacement, config_path)
            late_retargeted = True
        return result

    monkeypatch.setattr(
        credentials,
        "_atomic_exchange_entries",
        swap_stage_before_exchange,
    )
    monkeypatch.setattr(
        credentials,
        "_stat_active_target",
        retarget_after_post_target_stat,
    )

    with pytest.raises(
        CredentialRecoveryError,
        match="atomic exchange rollback|stage changed|unknown state",
    ):
        mutate_config_strict(
            lambda config: config.update(provider="after"),
            config_path=config_path,
        )

    assert stage_swapped is True
    assert late_retargeted is True
    payload_locations = [
        candidate
        for candidate in config_path.parent.iterdir()
        if candidate.is_file()
        and candidate.read_bytes() == external_payload
    ]
    assert payload_locations, "late external payload was irreversibly lost"
    if config_path not in payload_locations:
        assert any(
            path.name.endswith(".stage") for path in payload_locations
        )
        assert intent_path.exists() or abort_path.exists()
    with pytest.raises(CredentialRecoveryError, match="unknown state"):
        recover_credential_transaction(config_path)
    assert intent_path.exists() or abort_path.exists()
    assert any(
        candidate.is_file()
        and candidate.read_bytes() == external_payload
        for candidate in config_path.parent.iterdir()
    )


def test_external_target_retarget_after_atomic_swap_is_preserved_with_marker(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    original_exchange = getattr(
        credentials,
        "_atomic_exchange_entries",
        None,
    )
    injected = False

    def retarget_after_exchange(source, target):
        nonlocal injected
        if original_exchange is None:
            raise AssertionError("atomic exchange primitive was not used")
        result = original_exchange(source, target)
        if Path(target) == config_path and not injected:
            replacement = config_path.parent / ".external-replacement"
            replacement.write_text(
                "provider: external-after-cas\n",
                encoding="utf-8",
            )
            os.replace(replacement, target)
            injected = True
        return result

    monkeypatch.setattr(
        credentials,
        "_atomic_exchange_entries",
        retarget_after_exchange,
        raising=False,
    )

    with pytest.raises(
        CredentialRecoveryError,
        match="changed after atomic replace|atomic exchange primitive",
    ):
        mutate_config_strict(
            lambda config: config.update(provider="after"),
            config_path=config_path,
        )

    assert injected is True
    assert config_path.read_text(encoding="utf-8") == (
        "provider: external-after-cas\n"
    )
    assert intent_path.exists()
    with pytest.raises(CredentialRecoveryError, match="unknown state"):
        recover_credential_transaction(config_path)
    assert config_path.read_text(encoding="utf-8") == (
        "provider: external-after-cas\n"
    )
    assert intent_path.exists()


def test_noop_mutation_does_not_create_empty_config_or_env(tmp_path):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"

    snapshot = mutate_config_env_strict(
        lambda _config: None,
        {},
        config_path=config_path,
    )

    assert snapshot.config == {}
    assert snapshot.env == {}
    assert not config_path.exists()
    assert not env_path.exists()
    assert not (
        config_path.parent / ".taiji-credential-pair-intent.json"
    ).exists()


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="requires the Darwin renameatx_np kernel primitive",
)
def test_darwin_atomic_entry_primitives_swap_and_refuse_overwrite(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    credentials._atomic_exchange_entries(first, second)

    assert first.read_text(encoding="utf-8") == "second"
    assert second.read_text(encoding="utf-8") == "first"

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_text("source", encoding="utf-8")
    credentials._atomic_rename_noreplace(source, destination)
    assert destination.read_text(encoding="utf-8") == "source"
    assert not source.exists()

    occupied_source = tmp_path / "occupied-source"
    occupied_source.write_text("must-stay", encoding="utf-8")
    with pytest.raises(FileExistsError):
        credentials._atomic_rename_noreplace(
            occupied_source,
            destination,
        )
    assert occupied_source.read_text(encoding="utf-8") == "must-stay"
    assert destination.read_text(encoding="utf-8") == "source"


def test_linux_atomic_entry_primitives_use_exchange_and_noreplace_flags(
    monkeypatch,
    tmp_path,
):
    calls = []

    def fake_renameat2(source, target, flags):
        calls.append((Path(source), Path(target), flags))

    monkeypatch.setattr(
        credentials,
        "_credential_platform_name",
        lambda: "linux",
    )
    monkeypatch.setattr(
        credentials,
        "_linux_renameat2",
        fake_renameat2,
        raising=False,
    )
    first = tmp_path / "first"
    second = tmp_path / "second"

    credentials._atomic_exchange_entries(first, second)
    credentials._atomic_rename_noreplace(first, second)

    assert calls == [
        (first, second, 0x2),
        (first, second, 0x1),
    ]


def test_atomic_entry_primitives_fail_closed_on_unsupported_platform(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        credentials,
        "_credential_platform_name",
        lambda: "unsupported",
    )

    with pytest.raises(CredentialRecoveryError, match="not supported"):
        credentials._atomic_exchange_entries(
            tmp_path / "first",
            tmp_path / "second",
        )
    with pytest.raises(CredentialRecoveryError, match="not supported"):
        credentials._atomic_rename_noreplace(
            tmp_path / "first",
            tmp_path / "second",
        )


def test_pair_cas_failure_rolls_back_prior_target_across_resource_roots(
    monkeypatch,
    tmp_path,
):
    config_root = tmp_path / "config-root"
    env_root = tmp_path / "env-root"
    config_root.mkdir()
    env_root.mkdir()
    config_path = config_root / "config.yaml"
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_target = env_root / "shared.env"
    env_target.write_text("API_KEY=before\n", encoding="utf-8")
    env_path = config_root / ".env"
    env_path.symlink_to(env_target)
    original_replace = credentials._replace_credential_stage

    def tamper_second_target(
        stage_path,
        *,
        logical_path,
        real_target,
        mode,
        **replace_options,
    ):
        if Path(real_target) == env_target:
            env_target.write_text(
                "API_KEY=external-writer\n",
                encoding="utf-8",
            )
        return original_replace(
            stage_path,
            logical_path=logical_path,
            real_target=real_target,
            mode=mode,
            **replace_options,
        )

    monkeypatch.setattr(
        credentials,
        "_replace_credential_stage",
        tamper_second_target,
    )

    with pytest.raises(CredentialRecoveryError, match="changed before"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after"},
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == "provider: before\n"
    assert env_target.read_text(encoding="utf-8") == (
        "API_KEY=external-writer\n"
    )
    assert not (
        config_root / ".taiji-credential-pair-intent.json"
    ).exists()
    assert not (
        config_root / ".taiji-credential-pair-abort.json"
    ).exists()


def test_hard_crash_during_abort_rollback_resumes_without_overwriting_external(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    abort_path = (
        config_path.parent / ".taiji-credential-pair-abort.json"
    )
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    monkeypatch.delenv("API_KEY", raising=False)
    script = """
import os
import sys
from pathlib import Path

import agent.provider_credentials as credentials

config_path = Path(sys.argv[1])
env_path = config_path.parent / ".env"
original_replace = credentials._replace_credential_stage
original_rollback = credentials._rollback_applied_target

def tamper_env_before_cas(
    stage_path,
    *,
    logical_path,
    real_target,
    mode,
    **replace_options,
):
    if Path(logical_path) == env_path:
        env_path.write_text(
            "API_KEY=external-writer\\n",
            encoding="utf-8",
        )
    return original_replace(
        stage_path,
        logical_path=logical_path,
        real_target=real_target,
        mode=mode,
        **replace_options,
    )

def crash_after_first_rollback(target):
    original_rollback(target)
    os._exit(94)

credentials._replace_credential_stage = tamper_env_before_cas
credentials._rollback_applied_target = crash_after_first_rollback
credentials.mutate_config_env_strict(
    lambda config: config.update(provider="after"),
    {"API_KEY": "after"},
    config_path=config_path,
)
"""

    result = subprocess.run(
        [sys.executable, "-c", script, str(config_path)],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 94, result.stderr
    assert not intent_path.exists()
    assert abort_path.exists()
    assert config_path.read_text(encoding="utf-8") == "provider: before\n"
    assert env_path.read_text(encoding="utf-8") == (
        "API_KEY=external-writer\n"
    )

    assert recover_credential_transaction(config_path) == "recovered"
    assert config_path.read_text(encoding="utf-8") == "provider: before\n"
    assert env_path.read_text(encoding="utf-8") == (
        "API_KEY=external-writer\n"
    )
    assert not intent_path.exists()
    assert not abort_path.exists()
    assert not list(config_path.parent.glob(".taiji-credential-*.stage"))


def test_env_compare_and_set_does_not_overwrite_a_newer_disk_value(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir(parents=True)
    env_path.write_text("API_KEY=newer\n", encoding="utf-8")
    monkeypatch.setenv("API_KEY", "process-value")

    applied = mutate_env_unique(
        {"API_KEY": "replacement"},
        config_path=config_path,
        expected_values={"API_KEY": "older"},
    )

    assert applied == {"API_KEY": False}
    assert env_path.read_text(encoding="utf-8") == "API_KEY=newer\n"
    assert os.environ["API_KEY"] == "process-value"


def test_pair_mutation_preserves_config_symlink_and_updates_real_target(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    managed_root = tmp_path / "managed"
    profile_root.mkdir()
    managed_root.mkdir()
    real_config_path = managed_root / "config.yaml"
    real_config_path.write_text("provider: before\n", encoding="utf-8")
    config_path = profile_root / "config.yaml"
    config_path.symlink_to(real_config_path)
    alias_env_path = profile_root / ".env"
    alias_env_path.write_text("API_KEY=alias-only\n", encoding="utf-8")
    env_path = managed_root / ".env"
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    monkeypatch.delenv("API_KEY", raising=False)

    mutate_config_env_strict(
        lambda config: config.update(provider="after"),
        {"API_KEY": "after-secret"},
        config_path=config_path,
    )

    assert config_path.is_symlink()
    assert real_config_path.read_text(encoding="utf-8") == (
        "provider: after\n"
    )
    assert env_path.read_text(encoding="utf-8") == (
        "API_KEY=after-secret\n"
    )
    assert alias_env_path.read_text(encoding="utf-8") == (
        "API_KEY=alias-only\n"
    )
    assert not list(managed_root.glob(".taiji-credential-*.stage"))


@pytest.mark.parametrize("use_pair_mutation", [False, True])
def test_strict_mutation_parses_the_same_bytes_it_compares(
    monkeypatch,
    tmp_path,
    use_pair_mutation,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("source: captured-bytes\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_read_text = Path.read_text

    def return_different_path_read(path, *args, **kwargs):
        if path == config_path:
            return "source: later-path-read\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", return_different_path_read)
    monkeypatch.delenv("API_KEY", raising=False)
    if use_pair_mutation:
        mutate_config_env_strict(
            lambda config: config.update(mutated=True),
            {"API_KEY": "after"},
            config_path=config_path,
        )
    else:
        mutate_config_strict(
            lambda config: config.update(mutated=True),
            config_path=config_path,
        )

    written = config_path.read_bytes().decode("utf-8")
    assert "source: captured-bytes\n" in written
    assert "source: later-path-read\n" not in written
    assert "mutated: true\n" in written


def test_existing_unreadable_env_fails_closed_without_legacy_fallback(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir(parents=True)
    env_path.write_text("API_KEY=unreadable\n", encoding="utf-8")
    secret_env = credentials.credential_secret_env("alibaba-default")
    config_data = {
        "provider_credentials": [
            {
                "id": "alibaba-default",
                "provider_family": "alibaba_dashscope",
                "auth_type": "api_key",
                "secret_env": secret_env,
                "default": True,
            }
        ]
    }
    monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-must-not-be-used")
    original_open = credentials._os.open

    def fail_env_read(path, *args, **kwargs):
        if (
            Path(path) == env_path
            or (
                Path(path).name == ".env"
                and kwargs.get("dir_fd") is not None
            )
        ):
            raise OSError("simulated unreadable env")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(credentials._os, "open", fail_env_read)

    with pytest.raises(ValueError, match="cannot be read safely"):
        credentials.resolve_api_key(
            "alibaba",
            config_data=config_data,
            config_path=config_path,
        )


def test_hard_crash_after_config_replace_recovers_pair_and_original_mode(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("provider: before\n", encoding="utf-8")
    config_path.chmod(0o640)
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    monkeypatch.delenv("API_KEY", raising=False)

    script = """
import os
import sys
from pathlib import Path

import agent.provider_credentials as credentials

config_path = Path(sys.argv[1])
original_exchange = credentials._atomic_exchange_entries

def crash_after_atomic_exchange(source, target):
    original_exchange(source, target)
    if Path(target) == config_path:
        os._exit(91)

credentials._atomic_exchange_entries = crash_after_atomic_exchange
credentials.mutate_config_env_strict(
    lambda config: config.update(provider="after"),
    {"API_KEY": "after-secret"},
    config_path=config_path,
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(config_path)],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 91, result.stderr
    assert "provider: after" in config_path.read_text(encoding="utf-8")
    assert env_path.read_text(encoding="utf-8") == "API_KEY=before\n"

    assert recover_credential_transaction(config_path) == "recovered"
    snapshot = load_credential_snapshot(config_path)
    assert snapshot.config["provider"] == "after"
    assert snapshot.env["API_KEY"] == "after-secret"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o640


def test_named_secret_round_trips_dotenv_special_characters(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    credential_id = "alibaba-special"
    secret_env = credentials.credential_secret_env(credential_id)
    secret = 'token # fragment ${MUST_NOT_EXPAND} "quoted" \\ tail'
    monkeypatch.setenv("MUST_NOT_EXPAND", "ambient-value")

    mutate_config_env_strict(
        lambda config: config.update(
            provider_credentials=[
                {
                    "id": credential_id,
                    "provider_family": "alibaba_dashscope",
                    "auth_type": "api_key",
                    "secret_env": secret_env,
                    "default": True,
                }
            ]
        ),
        {secret_env: secret},
        config_path=config_path,
    )
    monkeypatch.delenv(secret_env, raising=False)

    assert credentials.resolve_api_key(
        "alibaba",
        credential_id,
        config_path=config_path,
    ) == secret


def test_transaction_works_when_os_fchmod_is_unavailable(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    monkeypatch.delattr(credentials._os, "fchmod")

    mutate_config_env_strict(
        lambda config: config.update(provider="after"),
        {"API_KEY": "after-secret"},
        config_path=config_path,
    )

    snapshot = load_credential_snapshot(config_path)
    assert snapshot.config["provider"] == "after"
    assert snapshot.env["API_KEY"] == "after-secret"


def test_lock_open_recovers_from_darwin_concurrent_creator_enoent(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    config_path = profile_root / "config.yaml"
    config_path.write_text("provider: before\n", encoding="utf-8")
    real_open = credentials._os.open
    injected = False

    def simulate_losing_nonexclusive_create(
        path,
        flags,
        mode=0o777,
        *,
        dir_fd=None,
    ):
        nonlocal injected
        if (
            path == credentials._CREDENTIAL_LOCK_NAME
            and flags & credentials._os.O_CREAT
            and not flags & credentials._os.O_EXCL
            and not injected
        ):
            winner_fd = real_open(path, flags, mode, dir_fd=dir_fd)
            credentials._os.close(winner_fd)
            injected = True
            raise FileNotFoundError("simulated Darwin concurrent-create loser")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(
        credentials._os,
        "open",
        simulate_losing_nonexclusive_create,
    )

    with credentials.credential_transaction(config_path):
        pass

    lock_path = profile_root / credentials._CREDENTIAL_LOCK_NAME
    lock_stat = lock_path.stat()
    assert injected is True
    assert stat.S_ISREG(lock_stat.st_mode)
    assert lock_stat.st_nlink == 1


def test_lock_open_darwin_race_does_not_follow_symlink_winner(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    config_path = profile_root / "config.yaml"
    config_path.write_text("provider: before\n", encoding="utf-8")
    victim_path = tmp_path / "victim"
    victim_path.write_text("do-not-open\n", encoding="utf-8")
    real_open = credentials._os.open
    injected = False

    def inject_symlink_winner(
        path,
        flags,
        mode=0o777,
        *,
        dir_fd=None,
    ):
        nonlocal injected
        if (
            path == credentials._CREDENTIAL_LOCK_NAME
            and flags & credentials._os.O_CREAT
            and not flags & credentials._os.O_EXCL
            and not injected
        ):
            credentials._os.symlink(
                victim_path,
                path,
                dir_fd=dir_fd,
            )
            injected = True
            raise FileNotFoundError("simulated Darwin concurrent-create loser")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(credentials._os, "open", inject_symlink_winner)

    with pytest.raises(OSError):
        with credentials.credential_transaction(config_path):
            pass

    assert injected is True
    assert (profile_root / credentials._CREDENTIAL_LOCK_NAME).is_symlink()
    assert victim_path.read_text(encoding="utf-8") == "do-not-open\n"


def test_lock_open_darwin_race_rejects_replaced_resource_directory(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    displaced_root = tmp_path / "profile-displaced"
    profile_root.mkdir()
    config_path = profile_root / "config.yaml"
    config_path.write_text("provider: before\n", encoding="utf-8")
    real_open = credentials._os.open
    injected = False

    def replace_directory_before_fallback(
        path,
        flags,
        mode=0o777,
        *,
        dir_fd=None,
    ):
        nonlocal injected
        if (
            path == credentials._CREDENTIAL_LOCK_NAME
            and flags & credentials._os.O_CREAT
            and not flags & credentials._os.O_EXCL
            and not injected
        ):
            profile_root.rename(displaced_root)
            profile_root.mkdir()
            config_path.write_text("provider: victim\n", encoding="utf-8")
            injected = True
            raise FileNotFoundError("simulated Darwin concurrent-create loser")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(
        credentials._os,
        "open",
        replace_directory_before_fallback,
    )

    with pytest.raises(CredentialRecoveryError, match="directory changed"):
        with credentials.credential_transaction(config_path):
            pass

    assert injected is True
    assert config_path.read_text(encoding="utf-8") == "provider: victim\n"
    assert (displaced_root / "config.yaml").read_text(
        encoding="utf-8"
    ) == "provider: before\n"


def test_symlink_alias_and_direct_config_share_one_lock_identity(tmp_path):
    managed_root = tmp_path / "managed"
    alias_root = tmp_path / "alias"
    managed_root.mkdir()
    alias_root.mkdir()
    real_config_path = managed_root / "config.yaml"
    real_config_path.write_text("provider: before\n", encoding="utf-8")
    alias_config_path = alias_root / "config.yaml"
    alias_config_path.symlink_to(real_config_path)

    assert credentials._credential_lock_root(alias_config_path) == (
        credentials._credential_lock_root(real_config_path)
    )
    with credentials.credential_transaction(alias_config_path):
        with credentials.credential_transaction(real_config_path):
            pass


def test_direct_config_recovers_pending_intent_created_through_alias(
    monkeypatch,
    tmp_path,
):
    managed_root = tmp_path / "managed"
    alias_root = tmp_path / "alias"
    managed_root.mkdir()
    alias_root.mkdir()
    real_config_path = managed_root / "config.yaml"
    real_config_path.write_text("provider: before\n", encoding="utf-8")
    alias_config_path = alias_root / "config.yaml"
    alias_config_path.symlink_to(real_config_path)
    alias_env_path = alias_root / ".env"
    alias_env_path.write_text("API_KEY=before\n", encoding="utf-8")
    intent_path = (
        managed_root / ".taiji-credential-pair-intent.json"
    )
    monkeypatch.delenv("API_KEY", raising=False)
    original_replace = credentials._replace_credential_stage

    def crash_after_config_replace(
        stage_path,
        *,
        logical_path,
        real_target,
        mode,
        **replace_options,
    ):
        result = original_replace(
            stage_path,
            logical_path=logical_path,
            real_target=real_target,
            mode=mode,
            **replace_options,
        )
        if Path(real_target) == real_config_path:
            raise SystemExit("simulated alias crash")
        return result

    monkeypatch.setattr(
        credentials,
        "_replace_credential_stage",
        crash_after_config_replace,
    )
    with pytest.raises(SystemExit, match="simulated alias crash"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=alias_config_path,
        )
    monkeypatch.setattr(
        credentials,
        "_replace_credential_stage",
        original_replace,
    )

    assert intent_path.exists()
    assert recover_credential_transaction(real_config_path) == "recovered"
    assert "provider: after" in real_config_path.read_text(encoding="utf-8")
    assert load_credential_snapshot(alias_config_path).env["API_KEY"] == (
        "after-secret"
    )


def test_single_file_mutation_rechecks_disk_state_at_replace(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("provider: before\n", encoding="utf-8")
    original_replace = credentials._replace_credential_stage

    def inject_external_write(
        stage_path,
        *,
        logical_path,
        real_target,
        mode,
        **replace_options,
    ):
        if logical_path == config_path:
            config_path.write_text(
                "provider: external-writer\n",
                encoding="utf-8",
            )
        return original_replace(
            stage_path,
            logical_path=logical_path,
            real_target=real_target,
            mode=mode,
            **replace_options,
        )

    monkeypatch.setattr(
        credentials,
        "_replace_credential_stage",
        inject_external_write,
    )

    with pytest.raises(CredentialRecoveryError, match="changed before replace"):
        mutate_config_strict(
            lambda config: config.update(provider="after"),
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == (
        "provider: external-writer\n"
    )


def test_single_file_mutation_verifies_target_after_replace(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("provider: before\n", encoding="utf-8")
    original_exchange = credentials._atomic_exchange_entries

    def exchange_then_tamper(source, target):
        result = original_exchange(source, target)
        if Path(target) == config_path:
            replacement = config_path.parent / ".external-after-replace"
            replacement.write_text(
                "provider: external-after-replace\n",
                encoding="utf-8",
            )
            os.replace(replacement, target)
        return result

    monkeypatch.setattr(
        credentials,
        "_atomic_exchange_entries",
        exchange_then_tamper,
    )

    with pytest.raises(CredentialRecoveryError, match="changed after replace"):
        mutate_config_strict(
            lambda config: config.update(provider="after"),
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == (
        "provider: external-after-replace\n"
    )


def test_pair_mutation_rechecks_classified_env_at_replace(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        credentials._credential_lock_root(config_path)
        / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_replace = credentials._replace_credential_stage

    def inject_external_write(
        stage_path,
        *,
        logical_path,
        real_target,
        mode,
        **replace_options,
    ):
        if logical_path == env_path:
            env_path.write_text(
                "API_KEY=external-writer\n",
                encoding="utf-8",
            )
        return original_replace(
            stage_path,
            logical_path=logical_path,
            real_target=real_target,
            mode=mode,
            **replace_options,
        )

    monkeypatch.setattr(
        credentials,
        "_replace_credential_stage",
        inject_external_write,
    )

    with pytest.raises(CredentialRecoveryError, match="changed before replace"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=config_path,
        )

    assert not intent_path.exists()
    assert not (
        config_path.parent / ".taiji-credential-pair-abort.json"
    ).exists()
    assert config_path.read_text(encoding="utf-8") == "provider: before\n"
    assert env_path.read_text(encoding="utf-8") == (
        "API_KEY=external-writer\n"
    )


def test_runtime_profile_projection_does_not_leak_to_profile_without_env(
    monkeypatch,
    tmp_path,
):
    alice_config = tmp_path / "alice" / "config.yaml"
    bob_config = tmp_path / "bob" / "config.yaml"
    alice_config.parent.mkdir()
    bob_config.parent.mkdir()
    secret_env = credentials.credential_secret_env("profile-isolation")
    monkeypatch.setenv(secret_env, "startup-baseline")

    mutate_env_unique(
        {secret_env: "alice-runtime-secret"},
        config_path=alice_config,
    )

    assert os.environ[secret_env] == "alice-runtime-secret"
    assert credentials._credential_secret_value(
        secret_env,
        alice_config,
    ) == "alice-runtime-secret"
    assert credentials._credential_secret_value(
        secret_env,
        bob_config,
    ) == "startup-baseline"


@pytest.mark.parametrize(
    ("startup_value", "expected_fallback"),
    [
        (None, ""),
        ("startup-baseline", "startup-baseline"),
    ],
)
def test_legacy_api_key_fallback_ignores_another_profile_runtime_projection(
    monkeypatch,
    tmp_path,
    startup_value,
    expected_fallback,
):
    alice_config = tmp_path / "alice" / "config.yaml"
    bob_config = tmp_path / "bob" / "config.yaml"
    alice_config.parent.mkdir()
    bob_config.parent.mkdir()
    legacy_env = "DASHSCOPE_API_KEY"
    monkeypatch.delitem(
        credentials._RUNTIME_ENV_BASELINES,
        legacy_env,
        raising=False,
    )
    monkeypatch.delitem(
        credentials._RUNTIME_ENV_PROJECTIONS,
        legacy_env,
        raising=False,
    )
    if startup_value is None:
        monkeypatch.delenv(legacy_env, raising=False)
    else:
        monkeypatch.setenv(legacy_env, startup_value)

    mutate_env_unique(
        {legacy_env: "alice-runtime-secret"},
        config_path=alice_config,
    )

    assert os.environ[legacy_env] == "alice-runtime-secret"
    assert credentials.resolve_api_key(
        "alibaba",
        config_path=bob_config,
    ) == expected_fallback

    monkeypatch.setenv(legacy_env, "external-override")

    assert credentials.resolve_api_key(
        "alibaba",
        config_path=bob_config,
    ) == "external-override"


def test_dangling_env_symlink_fails_closed_without_process_fallback(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    config_path.parent.mkdir()
    env_path = config_path.parent / ".env"
    env_path.symlink_to(tmp_path / "missing-secret-env")
    secret_env = credentials.credential_secret_env("dangling-profile")
    monkeypatch.setenv(secret_env, "must-not-fallback")

    with pytest.raises(ValueError, match="cannot be read safely"):
        credentials._credential_secret_value(secret_env, config_path)


def test_pre_intent_hard_exit_reclaims_only_stale_private_orphan_stages(
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        credentials._credential_lock_root(config_path)
        / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    script = """
import os
import sys
from pathlib import Path

import agent.provider_credentials as credentials

config_path = Path(sys.argv[1])

def crash_before_intent(*_args, **_kwargs):
    os._exit(92)

credentials._write_credential_journal = crash_before_intent
credentials.mutate_config_env_strict(
    lambda config: config.update(provider="after"),
    {"API_KEY": "orphan-secret"},
    config_path=config_path,
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(config_path)],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 92, result.stderr
    stages = sorted(
        config_path.parent.glob(".taiji-credential-*.stage")
    )
    assert len(stages) == 2
    assert not intent_path.exists()
    assert all(
        stat.S_IMODE(stage.stat().st_mode) == 0o600
        for stage in stages
    )

    assert recover_credential_transaction(config_path) == "not_needed"
    assert stages == sorted(
        config_path.parent.glob(".taiji-credential-*.stage")
    )

    fresh_stage = (
        config_path.parent
        / (
            ".taiji-credential-unrelated-"
            "11111111111111111111111111111111.stage"
        )
    )
    fresh_stage.write_bytes(b"fresh-stage")
    fresh_stage.chmod(0o600)
    outside_stage = (
        tmp_path
        / (
            ".taiji-credential-outside-"
            "22222222222222222222222222222222.stage"
        )
    )
    outside_stage.write_bytes(b"outside-stage")
    outside_stage.chmod(0o600)
    hard_linked_stage = (
        config_path.parent
        / (
            ".taiji-credential-hard-linked-"
            "44444444444444444444444444444444.stage"
        )
    )
    hard_linked_stage.write_bytes(b"hard-linked-stage")
    hard_linked_sibling = config_path.parent / "hard-linked-stage.sibling"
    os.link(hard_linked_stage, hard_linked_sibling)
    os.utime(hard_linked_stage, (1, 1))
    for stage in stages:
        os.utime(stage, (1, 1))

    assert recover_credential_transaction(config_path) == "not_needed"
    assert not any(stage.exists() for stage in stages)
    assert fresh_stage.exists()
    assert outside_stage.exists()
    assert hard_linked_stage.exists()
    assert hard_linked_sibling.exists()
    assert config_path.read_text(encoding="utf-8") == "provider: before\n"
    assert env_path.read_text(encoding="utf-8") == "API_KEY=before\n"


def test_config_symlink_retargeted_after_lock_cannot_escape_locked_target(
    monkeypatch,
    tmp_path,
):
    alias_root = tmp_path / "alias"
    first_root = tmp_path / "managed-first"
    second_root = tmp_path / "managed-second"
    alias_root.mkdir()
    first_root.mkdir()
    second_root.mkdir()
    first_config = first_root / "real.yaml"
    second_config = second_root / "real.yaml"
    first_config.write_text("provider: first\n", encoding="utf-8")
    second_config.write_text("provider: second\n", encoding="utf-8")
    alias_config = alias_root / "custom-name.yaml"
    alias_config.symlink_to(first_config)
    original_recover = credentials._recover_pending_transaction_unlocked
    retargeted = False

    def retarget_after_lock(lock_root, *args, **kwargs):
        nonlocal retargeted
        if not retargeted:
            alias_config.unlink()
            alias_config.symlink_to(second_config)
            retargeted = True
        return original_recover(lock_root, *args, **kwargs)

    monkeypatch.setattr(
        credentials,
        "_recover_pending_transaction_unlocked",
        retarget_after_lock,
    )

    with pytest.raises(CredentialRecoveryError, match="target changed"):
        mutate_config_strict(
            lambda config: config.update(provider="mutated"),
            config_path=alias_config,
        )

    assert first_config.read_text(encoding="utf-8") == "provider: first\n"
    assert second_config.read_text(encoding="utf-8") == "provider: second\n"
    assert not list(first_root.glob(".taiji-credential-*.stage"))
    assert not list(second_root.glob(".taiji-credential-*.stage"))


def test_unowned_stage_in_shared_env_root_is_not_deleted_as_orphan(
    tmp_path,
):
    profile_root = tmp_path / "profile"
    shared_root = tmp_path / "shared"
    profile_root.mkdir()
    shared_root.mkdir()
    config_path = profile_root / "config.yaml"
    config_path.write_text("provider: before\n", encoding="utf-8")
    shared_env = shared_root / "secrets.env"
    shared_env.write_text("API_KEY=before\n", encoding="utf-8")
    (profile_root / ".env").symlink_to(shared_env)
    unowned_stage = (
        shared_root
        / (
            ".taiji-credential-secrets.env-"
            "66666666666666666666666666666666.stage"
        )
    )
    unowned_stage.write_bytes(b"pending-other-config-transaction")
    unowned_stage.chmod(0o600)
    os.utime(unowned_stage, (1, 1))

    assert recover_credential_transaction(config_path) == "not_needed"

    assert unowned_stage.read_bytes() == (
        b"pending-other-config-transaction"
    )


def test_nested_alias_is_pinned_to_the_outer_physical_lock(
    monkeypatch,
    tmp_path,
):
    first_root = tmp_path / "managed-first"
    second_root = tmp_path / "managed-second"
    alias_root = tmp_path / "alias"
    first_root.mkdir()
    second_root.mkdir()
    alias_root.mkdir()
    first_config = first_root / "real.yaml"
    second_config = second_root / "real.yaml"
    first_config.write_text("provider: first\n", encoding="utf-8")
    second_config.write_text("provider: second\n", encoding="utf-8")
    alias_config = alias_root / "custom-name.yaml"
    alias_config.symlink_to(first_config)
    original_read = credentials._read_optional_bytes
    retargeted = False

    def retarget_before_nested_read(path, **kwargs):
        nonlocal retargeted
        if Path(path) == alias_config and not retargeted:
            alias_config.unlink()
            alias_config.symlink_to(second_config)
            retargeted = True
        return original_read(path, **kwargs)

    monkeypatch.setattr(
        credentials,
        "_read_optional_bytes",
        retarget_before_nested_read,
    )

    with credentials.credential_transaction(first_config):
        with pytest.raises(CredentialRecoveryError, match="target changed"):
            mutate_config_strict(
                lambda config: config.update(provider="mutated"),
                config_path=alias_config,
            )

    assert first_config.read_text(encoding="utf-8") == "provider: first\n"
    assert second_config.read_text(encoding="utf-8") == "provider: second\n"


def test_process_env_projection_failure_keeps_recovery_marker(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        credentials._credential_lock_root(config_path)
        / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_project = credentials._project_process_env_batch

    def fail_projection(_updates):
        raise RuntimeError("simulated projection failure")

    monkeypatch.setattr(
        credentials,
        "_project_process_env_batch",
        fail_projection,
    )

    with pytest.raises(RuntimeError, match="projection failure"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == "provider: after\n"
    assert env_path.read_text(encoding="utf-8") == "API_KEY=after-secret\n"
    assert intent_path.exists()

    with pytest.raises(RuntimeError, match="projection failure"):
        recover_credential_transaction(config_path)
    assert intent_path.exists()

    monkeypatch.setattr(
        credentials,
        "_project_process_env_batch",
        original_project,
    )
    assert recover_credential_transaction(config_path) == "recovered"
    assert not intent_path.exists()
    assert not list(config_path.parent.glob(".taiji-credential-*.stage"))
    assert os.environ["API_KEY"] == "after-secret"


@pytest.mark.parametrize(
    ("oversized_target", "expected_message"),
    [
        ("config", "config exceeds"),
        ("env", "env exceeds"),
    ],
)
def test_snapshot_rejects_oversized_credential_files(
    monkeypatch,
    tmp_path,
    oversized_target,
    expected_message,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    if oversized_target == "config":
        monkeypatch.setattr(credentials, "_MAX_CREDENTIAL_CONFIG_BYTES", 8)
    else:
        monkeypatch.setattr(credentials, "_MAX_CREDENTIAL_ENV_BYTES", 8)

    with pytest.raises(ValueError, match=expected_message):
        load_credential_snapshot(config_path)


def test_recovery_rejects_oversized_journal(monkeypatch, tmp_path):
    config_path = tmp_path / "profile" / "config.yaml"
    config_path.parent.mkdir()
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    monkeypatch.setattr(credentials, "_MAX_CREDENTIAL_JOURNAL_BYTES", 32)
    intent_path.write_bytes(b"x" * 33)
    intent_path.chmod(0o600)

    with pytest.raises(CredentialRecoveryError, match="intent exceeds"):
        recover_credential_transaction(config_path)


def test_recovery_rejects_oversized_stage(monkeypatch, tmp_path):
    stage_path = (
        tmp_path
        / (
            ".taiji-credential-config.yaml-"
            "33333333333333333333333333333333.stage"
        )
    )
    stage_path.write_bytes(b"x" * 17)
    stage_path.chmod(0o600)
    monkeypatch.setattr(credentials, "_MAX_CREDENTIAL_STAGE_BYTES", 16)

    with pytest.raises(CredentialRecoveryError, match="stage exceeds"):
        credentials._read_stage_bytes(stage_path)


def test_pair_transaction_fails_closed_on_unproven_windows_platform(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    monkeypatch.setattr(
        credentials,
        "_credential_platform_name",
        lambda: "nt",
    )

    with pytest.raises(CredentialRecoveryError, match="not supported"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == "provider: before\n"
    assert env_path.read_text(encoding="utf-8") == "API_KEY=before\n"
    assert not list(config_path.parent.glob(".taiji-credential-*.stage"))
    assert not (
        config_path.parent / ".taiji-credential-pair-intent.json"
    ).exists()


@pytest.mark.parametrize("hard_link_target", ["config", "env"])
def test_mutation_rejects_hard_linked_targets_without_overwriting_siblings(
    tmp_path,
    hard_link_target,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    target = config_path if hard_link_target == "config" else env_path
    sibling = target.with_name(f"{target.name}.hard-link")
    os.link(target, sibling)

    with pytest.raises(ValueError, match="hard-linked"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == "provider: before\n"
    assert env_path.read_text(encoding="utf-8") == "API_KEY=before\n"
    assert sibling.read_bytes() == target.read_bytes()


def test_aliases_of_one_physical_config_use_one_canonical_env_store(
    tmp_path,
):
    managed_root = tmp_path / "managed"
    first_alias_root = tmp_path / "alias-first"
    second_alias_root = tmp_path / "alias-second"
    managed_root.mkdir()
    first_alias_root.mkdir()
    second_alias_root.mkdir()
    real_config = managed_root / "real.yaml"
    real_config.write_text("provider: before\n", encoding="utf-8")
    canonical_env = managed_root / ".env"
    canonical_env.write_text("API_KEY=canonical\n", encoding="utf-8")
    first_alias = first_alias_root / "first.yaml"
    second_alias = second_alias_root / "second.yaml"
    first_alias.symlink_to(real_config)
    second_alias.symlink_to(real_config)
    (first_alias_root / ".env").write_text(
        "API_KEY=alias-first\n",
        encoding="utf-8",
    )
    (second_alias_root / ".env").write_text(
        "API_KEY=alias-second\n",
        encoding="utf-8",
    )

    assert load_credential_snapshot(first_alias).env["API_KEY"] == (
        "canonical"
    )
    assert load_credential_snapshot(second_alias).env["API_KEY"] == (
        "canonical"
    )

    mutate_env_unique(
        {"API_KEY": "updated"},
        config_path=first_alias,
    )

    assert canonical_env.read_text(encoding="utf-8") == "API_KEY=updated\n"
    assert (first_alias_root / ".env").read_text(encoding="utf-8") == (
        "API_KEY=alias-first\n"
    )
    assert (second_alias_root / ".env").read_text(encoding="utf-8") == (
        "API_KEY=alias-second\n"
    )


def test_shared_physical_env_root_is_locked_across_config_roots(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    shared_root = tmp_path / "shared"
    first_root.mkdir()
    second_root.mkdir()
    shared_root.mkdir()
    first_config = first_root / "config.yaml"
    second_config = second_root / "config.yaml"
    first_config.write_text("provider: first\n", encoding="utf-8")
    second_config.write_text("provider: second\n", encoding="utf-8")
    shared_env = shared_root / "secrets.env"
    shared_env.write_text("API_KEY=before\n", encoding="utf-8")
    (first_root / ".env").symlink_to(shared_env)
    (second_root / ".env").symlink_to(shared_env)
    script = """
import sys
from pathlib import Path

from agent.provider_credentials import mutate_env_unique

mutate_env_unique(
    {"API_KEY": "child"},
    config_path=Path(sys.argv[1]),
)
print("child-finished", flush=True)
"""

    child = None
    try:
        with credentials.credential_transaction(first_config):
            child = subprocess.Popen(
                [sys.executable, "-c", script, str(second_config)],
                cwd=Path(__file__).parents[2],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            with pytest.raises(subprocess.TimeoutExpired):
                child.communicate(timeout=0.3)
        stdout, stderr = child.communicate(timeout=3)
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=3)
    assert child.returncode == 0, stderr
    assert "child-finished" in stdout
    assert shared_env.read_text(encoding="utf-8") == "API_KEY=child\n"


def test_journal_symlink_race_cannot_escape_pinned_lock_directory(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    victim_path = tmp_path / "victim.txt"
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    victim_before = b"victim-must-not-change\n"
    victim_path.write_bytes(victim_before)
    journal_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    original_lexists = credentials._os.path.lexists
    raced = False

    def inject_journal_symlink(path):
        nonlocal raced
        if Path(path) == journal_path and not raced:
            journal_path.symlink_to(victim_path)
            raced = True
            return False
        return original_lexists(path)

    monkeypatch.setattr(
        credentials._os.path,
        "lexists",
        inject_journal_symlink,
    )

    with pytest.raises(CredentialRecoveryError):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=config_path,
        )

    assert victim_path.read_bytes() == victim_before
    assert config_path.read_text(encoding="utf-8") == "provider: before\n"
    assert env_path.read_text(encoding="utf-8") == "API_KEY=before\n"


def test_multi_key_projection_failure_rolls_back_environ_and_bookkeeping(
    monkeypatch,
    tmp_path,
):
    first_key = "TAIJI_BATCH_FIRST"
    second_key = "TAIJI_BATCH_SECOND"
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text(
        f"{first_key}=before-first\n{second_key}=before-second\n",
        encoding="utf-8",
    )

    class FailingEnvironment(dict):
        fail_projection = True

        def __setitem__(self, key, value):
            if (
                self.fail_projection
                and key == second_key
                and value == "after-second"
            ):
                raise RuntimeError("second projection failed")
            return super().__setitem__(key, value)

    fake_environ = FailingEnvironment(
        {
            first_key: "runtime-first",
            second_key: "runtime-second",
        }
    )
    monkeypatch.setattr(credentials._os, "environ", fake_environ)
    monkeypatch.setitem(
        credentials._RUNTIME_ENV_BASELINES,
        first_key,
        "baseline-first",
    )
    monkeypatch.setitem(
        credentials._RUNTIME_ENV_BASELINES,
        second_key,
        "baseline-second",
    )
    monkeypatch.setitem(
        credentials._RUNTIME_ENV_PROJECTIONS,
        first_key,
        "projection-first",
    )
    monkeypatch.setitem(
        credentials._RUNTIME_ENV_PROJECTIONS,
        second_key,
        "projection-second",
    )
    environ_before = dict(fake_environ)
    baselines_before = dict(credentials._RUNTIME_ENV_BASELINES)
    projections_before = dict(credentials._RUNTIME_ENV_PROJECTIONS)

    with pytest.raises(RuntimeError, match="second projection failed"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {
                first_key: "after-first",
                second_key: "after-second",
            },
            config_path=config_path,
        )

    assert dict(fake_environ) == environ_before
    assert credentials._RUNTIME_ENV_BASELINES == baselines_before
    assert credentials._RUNTIME_ENV_PROJECTIONS == projections_before
    assert config_path.read_text(encoding="utf-8") == "provider: after\n"
    assert env_path.read_text(encoding="utf-8") == (
        f"{first_key}=after-first\n{second_key}=after-second\n"
    )
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    assert intent_path.exists()

    fake_environ.fail_projection = False
    assert recover_credential_transaction(config_path) == "recovered"
    assert fake_environ[first_key] == "after-first"
    assert fake_environ[second_key] == "after-second"
    assert not intent_path.exists()
    assert not list(config_path.parent.glob(".taiji-credential-*.stage"))


def test_projection_failure_in_child_is_recovered_by_parent_process(
    monkeypatch,
    tmp_path,
):
    first_key = "TAIJI_CHILD_RECOVERY_FIRST"
    second_key = "TAIJI_CHILD_RECOVERY_SECOND"
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text(
        f"{first_key}=before-first\n{second_key}=before-second\n",
        encoding="utf-8",
    )
    monkeypatch.delenv(first_key, raising=False)
    monkeypatch.delenv(second_key, raising=False)
    script = """
import sys
from pathlib import Path

import agent.provider_credentials as credentials

config_path = Path(sys.argv[1])
first_key = sys.argv[2]
second_key = sys.argv[3]

class FailingEnvironment(dict):
    def __setitem__(self, key, value):
        if key == second_key and value == "after-second":
            raise RuntimeError("child projection failed")
        return super().__setitem__(key, value)

credentials._os.environ = FailingEnvironment(
    {
        first_key: "runtime-first",
        second_key: "runtime-second",
    }
)
try:
    credentials.mutate_config_env_strict(
        lambda config: config.update(provider="after"),
        {
            first_key: "after-first",
            second_key: "after-second",
        },
        config_path=config_path,
    )
except RuntimeError as exc:
    if str(exc) == "child projection failed":
        sys.exit(91)
    raise
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(config_path),
            first_key,
            second_key,
        ],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 91, result.stderr
    assert config_path.read_text(encoding="utf-8") == "provider: after\n"
    assert env_path.read_text(encoding="utf-8") == (
        f"{first_key}=after-first\n{second_key}=after-second\n"
    )
    assert intent_path.exists()

    assert recover_credential_transaction(config_path) == "recovered"
    assert os.environ[first_key] == "after-first"
    assert os.environ[second_key] == "after-second"
    assert not intent_path.exists()
    assert not list(config_path.parent.glob(".taiji-credential-*.stage"))


def test_hard_crash_after_projection_replays_idempotently_in_parent(
    monkeypatch,
    tmp_path,
):
    first_key = "TAIJI_CRASH_REPLAY_FIRST"
    second_key = "TAIJI_CRASH_REPLAY_SECOND"
    config_path = tmp_path / "profile" / "config.yaml"
    env_path = config_path.parent / ".env"
    intent_path = (
        config_path.parent / ".taiji-credential-pair-intent.json"
    )
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text(
        f"{first_key}=before-first\n{second_key}=before-second\n",
        encoding="utf-8",
    )
    monkeypatch.delenv(first_key, raising=False)
    monkeypatch.delenv(second_key, raising=False)
    script = """
import os
import sys
from pathlib import Path

import agent.provider_credentials as credentials

config_path = Path(sys.argv[1])
first_key = sys.argv[2]
second_key = sys.argv[3]
intent_path = (
    config_path.resolve().parent
    / ".taiji-credential-pair-intent.json"
)
original_unlink = credentials._unlink_active_target

def crash_before_intent_cleanup(path, **kwargs):
    if Path(path) == intent_path:
        os._exit(92)
    return original_unlink(path, **kwargs)

credentials._unlink_active_target = crash_before_intent_cleanup
credentials.mutate_config_env_strict(
    lambda config: config.update(provider="after"),
    {
        first_key: "after-first",
        second_key: "after-second",
    },
    config_path=config_path,
)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(config_path),
            first_key,
            second_key,
        ],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 92, result.stderr
    assert config_path.read_text(encoding="utf-8") == "provider: after\n"
    assert env_path.read_text(encoding="utf-8") == (
        f"{first_key}=after-first\n{second_key}=after-second\n"
    )
    assert intent_path.exists()

    assert recover_credential_transaction(config_path) == "recovered"
    assert os.environ[first_key] == "after-first"
    assert os.environ[second_key] == "after-second"
    assert not intent_path.exists()
    assert recover_credential_transaction(config_path) == "not_needed"


def test_pair_commit_projects_each_runtime_key_exactly_once(
    monkeypatch,
    tmp_path,
):
    first_key = "TAIJI_PROJECT_ONCE_FIRST"
    second_key = "TAIJI_PROJECT_ONCE_SECOND"
    config_path = tmp_path / "profile" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text("provider: before\n", encoding="utf-8")

    class CountingEnvironment(dict):
        def __init__(self):
            super().__init__()
            self.assignments = []

        def __setitem__(self, key, value):
            if key in {first_key, second_key}:
                self.assignments.append((key, value))
            return super().__setitem__(key, value)

    fake_environ = CountingEnvironment()
    monkeypatch.setattr(credentials._os, "environ", fake_environ)

    mutate_config_env_strict(
        lambda config: config.update(provider="after"),
        {
            first_key: "first",
            second_key: "second",
        },
        config_path=config_path,
    )

    assert fake_environ.assignments == [
        (first_key, "first"),
        (second_key, "second"),
    ]


def test_directory_rename_and_recreate_after_lock_fails_closed(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    displaced_root = tmp_path / "profile-displaced"
    profile_root.mkdir()
    config_path = profile_root / "config.yaml"
    env_path = profile_root / ".env"
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_recover = credentials._recover_pending_transaction_unlocked
    replaced = False

    def replace_directory_after_lock(lock_root, *args, **kwargs):
        nonlocal replaced
        if not replaced:
            profile_root.rename(displaced_root)
            profile_root.mkdir()
            config_path.write_text("provider: victim\n", encoding="utf-8")
            env_path.write_text("API_KEY=victim\n", encoding="utf-8")
            replaced = True
        return original_recover(lock_root, *args, **kwargs)

    monkeypatch.setattr(
        credentials,
        "_recover_pending_transaction_unlocked",
        replace_directory_after_lock,
    )

    with pytest.raises(CredentialRecoveryError, match="directory changed"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == "provider: victim\n"
    assert env_path.read_text(encoding="utf-8") == "API_KEY=victim\n"
    assert (displaced_root / "config.yaml").read_text(
        encoding="utf-8"
    ) == "provider: before\n"
    assert (displaced_root / ".env").read_text(encoding="utf-8") == (
        "API_KEY=before\n"
    )


@pytest.mark.parametrize(
    "reserved_name",
    [
        ".env",
        ".taiji-credential-transaction.lock",
        ".taiji-credential-pair-intent.json",
        (
            ".taiji-credential-config-"
            "55555555555555555555555555555555.stage"
        ),
    ],
)
def test_reserved_credential_paths_are_rejected(tmp_path, reserved_name):
    config_path = tmp_path / "profile" / reserved_name
    config_path.parent.mkdir()

    with pytest.raises(ValueError, match="reserved"):
        mutate_config_strict(
            lambda config: config.update(provider="after"),
            config_path=config_path,
        )


def test_config_and_canonical_env_cannot_resolve_to_same_target(tmp_path):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    env_path = profile_root / ".env"
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    config_path = profile_root / "config.yaml"
    config_path.symlink_to(env_path)

    with pytest.raises(ValueError, match="same target"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=config_path,
        )

    assert env_path.read_text(encoding="utf-8") == "API_KEY=before\n"


def test_config_and_canonical_env_cannot_be_hardlinks_to_same_inode(
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    config_path = profile_root / "config.yaml"
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path = profile_root / ".env"
    os.link(config_path, env_path)

    with pytest.raises(ValueError, match="same target"):
        mutate_config_env_strict(
            lambda config: config.update(provider="after"),
            {"API_KEY": "after-secret"},
            config_path=config_path,
        )

    assert config_path.read_text(encoding="utf-8") == "provider: before\n"


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX ownership and mode policy only",
)
def test_group_shared_transaction_uses_exact_group_modes(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    profile_root.chmod(0o2770)
    config_path = profile_root / "config.yaml"
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")

    mutate_config_env_strict(
        lambda config: config.update(provider="shared"),
        {"API_KEY": "shared-secret"},
        config_path=config_path,
    )

    lock_path = (
        profile_root / credentials._CREDENTIAL_LOCK_NAME
    )
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o660
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o640
    assert stat.S_IMODE((profile_root / ".env").stat().st_mode) == 0o640

    with credentials.credential_transaction(config_path):
        stage_path, _real_path = credentials._stage_credential_bytes(
            config_path,
            b"provider: staged\n",
        )
        assert stat.S_IMODE(stage_path.stat().st_mode) == 0o640
        stage_path.unlink()


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX ownership and mode policy only",
)
def test_group_shared_transaction_skips_cross_owner_chmod_when_mode_matches(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    profile_root.chmod(0o2770)
    config_path = profile_root / "config.yaml"
    config_path.write_text("provider: shared\n", encoding="utf-8")
    config_path.chmod(0o640)
    lock_path = profile_root / credentials._CREDENTIAL_LOCK_NAME
    lock_path.touch(mode=0o660)
    lock_path.chmod(0o660)
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")

    def reject_redundant_chmod(_fd, _mode):
        raise PermissionError("simulated group-only cross-owner descriptor")

    monkeypatch.setattr(credentials._os, "fchmod", reject_redundant_chmod)

    snapshot = load_credential_snapshot(config_path)

    assert snapshot.config == {"provider": "shared"}


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX ownership and mode policy only",
)
def test_group_shared_transaction_rejects_world_accessible_root(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    profile_root.chmod(0o2777)
    config_path = profile_root / "config.yaml"
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")

    with pytest.raises(
        CredentialRecoveryError,
        match="shared credential resource root",
    ):
        load_credential_snapshot(config_path)


def test_private_transaction_modes_remain_private_by_default(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    config_path = profile_root / "config.yaml"
    monkeypatch.delenv("HERMES_CREDENTIAL_GROUP_SHARED", raising=False)

    mutate_config_env_strict(
        lambda config: config.update(provider="private"),
        {"API_KEY": "private-secret"},
        config_path=config_path,
    )

    lock_path = profile_root / credentials._CREDENTIAL_LOCK_NAME
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE((profile_root / ".env").stat().st_mode) == 0o600


def test_invalid_group_shared_policy_fails_closed(monkeypatch, tmp_path):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    config_path = profile_root / "config.yaml"
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "true")

    with pytest.raises(
        CredentialRecoveryError,
        match="must be exactly 0 or 1",
    ):
        load_credential_snapshot(config_path)

    assert not (
        profile_root / credentials._CREDENTIAL_LOCK_NAME
    ).exists()


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX ownership and mode policy only",
)
def test_group_shared_policy_is_frozen_for_nested_transactions(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    profile_root.chmod(0o2770)
    config_path = profile_root / "config.yaml"
    monkeypatch.delenv("HERMES_CREDENTIAL_GROUP_SHARED", raising=False)

    with credentials.credential_transaction(config_path):
        monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")
        with credentials.credential_transaction(config_path):
            active_policy = credentials._active_credential_access_policy()
            assert active_policy.group_shared is False
            assert active_policy.lock_mode == 0o600

    lock_path = profile_root / credentials._CREDENTIAL_LOCK_NAME
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX ownership and mode policy only",
)
def test_group_shared_manifest_rejects_world_readable_target_mode(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    profile_root.chmod(0o2770)
    config_path = profile_root / "config.yaml"
    transaction_id = "1" * 32
    digest = hashlib.sha256(b"").hexdigest()
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")
    manifest = {
        "schema": credentials._CREDENTIAL_JOURNAL_SCHEMA,
        "transaction_id": transaction_id,
        "env_keys": [],
        "targets": [
            {
                "name": "config",
                "logical_path": str(config_path),
                "real_path": str(config_path),
                "stage_path": str(
                    profile_root
                    / (
                        ".taiji-credential-config.yaml-"
                        f"{transaction_id}.stage"
                    )
                ),
                "before_exists": False,
                "before_sha256": digest,
                "target_sha256": digest,
                "mode": 0o777,
            }
        ],
    }

    with credentials.credential_transaction(config_path):
        with pytest.raises(
            CredentialRecoveryError,
            match="target mode is invalid",
        ):
            credentials._validated_credential_manifest(
                profile_root,
                manifest,
            )


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX ownership and mode policy only",
)
@pytest.mark.parametrize("journal_mode", [0o600, 0o640])
def test_group_shared_recovery_rejects_unsafe_pending_target_mode(
    monkeypatch,
    tmp_path,
    journal_mode,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    profile_root.chmod(0o2770)
    config_path = profile_root / "config.yaml"
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path = profile_root / ".env"
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_finalize = credentials._finalize_committed_transaction
    monkeypatch.delenv("HERMES_CREDENTIAL_GROUP_SHARED", raising=False)
    monkeypatch.setattr(
        credentials,
        "_finalize_committed_transaction",
        lambda *_args, **_kwargs: None,
    )

    mutate_config_env_strict(
        lambda config: config.update(provider="after"),
        {"API_KEY": "after"},
        config_path=config_path,
    )

    monkeypatch.setattr(
        credentials,
        "_finalize_committed_transaction",
        original_finalize,
    )
    intent_path = profile_root / credentials._CREDENTIAL_JOURNAL_NAME
    manifest = json.loads(intent_path.read_text(encoding="utf-8"))
    for target in manifest["targets"]:
        target["mode"] = 0o777
    intent_path.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    intent_path.chmod(journal_mode)
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")

    with pytest.raises(
        CredentialRecoveryError,
        match="target mode is invalid",
    ):
        recover_credential_transaction(config_path)

    assert intent_path.exists()
    assert stat.S_IMODE(intent_path.stat().st_mode) == journal_mode


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX ownership and mode policy only",
)
def test_group_shared_pending_artifacts_use_exact_mode_and_gid(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    profile_root.chmod(0o2770)
    config_path = profile_root / "config.yaml"
    config_path.write_text("provider: before\n", encoding="utf-8")
    config_path.chmod(0o640)
    env_path = profile_root / ".env"
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    env_path.chmod(0o640)
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")
    original_finalize = credentials._finalize_committed_transaction
    monkeypatch.setattr(
        credentials,
        "_finalize_committed_transaction",
        lambda *_args, **_kwargs: None,
    )

    mutate_config_env_strict(
        lambda config: config.update(provider="after"),
        {"API_KEY": "after"},
        config_path=config_path,
    )

    intent_path = (
        profile_root / credentials._CREDENTIAL_JOURNAL_NAME
    )
    stages = list(profile_root.glob(".taiji-credential-*.stage"))
    assert intent_path.exists()
    assert stages
    manifest = json.loads(intent_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "taiji-credential-pair-intent/v2"
    expected_gid = profile_root.stat().st_gid
    for artifact in [intent_path, *stages]:
        artifact_stat = artifact.stat()
        assert stat.S_IMODE(artifact_stat.st_mode) == 0o640
        assert artifact_stat.st_gid == expected_gid

    monkeypatch.setattr(
        credentials,
        "_finalize_committed_transaction",
        original_finalize,
    )
    assert recover_credential_transaction(config_path) == "recovered"
    assert not intent_path.exists()
    assert not list(profile_root.glob(".taiji-credential-*.stage"))


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX ownership and mode policy only",
)
def test_group_shared_transaction_migrates_legacy_private_pending_intent(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    profile_root.chmod(0o2770)
    config_path = profile_root / "config.yaml"
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path = profile_root / ".env"
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_finalize = credentials._finalize_committed_transaction
    monkeypatch.delenv("HERMES_CREDENTIAL_GROUP_SHARED", raising=False)
    monkeypatch.setattr(
        credentials,
        "_finalize_committed_transaction",
        lambda *_args, **_kwargs: None,
    )

    mutate_config_env_strict(
        lambda config: config.update(provider="after"),
        {"API_KEY": "after"},
        config_path=config_path,
    )

    intent_path = profile_root / credentials._CREDENTIAL_JOURNAL_NAME
    stages = list(profile_root.glob(".taiji-credential-*.stage"))
    assert stat.S_IMODE(intent_path.stat().st_mode) == 0o600
    assert stages
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in stages)

    monkeypatch.setattr(
        credentials,
        "_finalize_committed_transaction",
        original_finalize,
    )
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")

    assert recover_credential_transaction(config_path) == "recovered"
    snapshot = load_credential_snapshot(config_path)
    assert snapshot.config["provider"] == "after"
    assert snapshot.env["API_KEY"] == "after"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o640
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o640
    assert not intent_path.exists()
    assert not list(profile_root.glob(".taiji-credential-*.stage"))


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX ownership and mode policy only",
)
def test_group_shared_legacy_migration_persists_marker_across_interruption(
    monkeypatch,
    tmp_path,
):
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    profile_root.chmod(0o2770)
    config_path = profile_root / "config.yaml"
    config_path.write_text("provider: before\n", encoding="utf-8")
    env_path = profile_root / ".env"
    env_path.write_text("API_KEY=before\n", encoding="utf-8")
    original_finalize = credentials._finalize_committed_transaction
    original_classify = credentials._classify_commit_target_state
    monkeypatch.delenv("HERMES_CREDENTIAL_GROUP_SHARED", raising=False)
    monkeypatch.setattr(
        credentials,
        "_finalize_committed_transaction",
        lambda *_args, **_kwargs: None,
    )

    mutate_config_env_strict(
        lambda config: config.update(provider="after"),
        {"API_KEY": "after"},
        config_path=config_path,
    )

    intent_path = profile_root / credentials._CREDENTIAL_JOURNAL_NAME
    monkeypatch.setattr(
        credentials,
        "_finalize_committed_transaction",
        original_finalize,
    )
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")
    monkeypatch.setattr(
        credentials,
        "_classify_commit_target_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated migration interruption")
        ),
    )

    with pytest.raises(RuntimeError, match="migration interruption"):
        recover_credential_transaction(config_path)

    assert stat.S_IMODE(intent_path.stat().st_mode) == 0o640
    migrated_manifest = json.loads(
        intent_path.read_text(encoding="utf-8")
    )
    assert (
        migrated_manifest["schema"]
        == "taiji-credential-pair-intent/v2"
    )
    monkeypatch.setattr(
        credentials,
        "_classify_commit_target_state",
        original_classify,
    )

    assert recover_credential_transaction(config_path) == "recovered"
    snapshot = load_credential_snapshot(config_path)
    assert snapshot.config["provider"] == "after"
    assert snapshot.env["API_KEY"] == "after"
