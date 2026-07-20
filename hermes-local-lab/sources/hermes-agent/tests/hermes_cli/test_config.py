"""Tests for hermes_cli configuration management."""

import os
from contextlib import contextmanager
from pathlib import Path
import threading
from unittest.mock import patch, MagicMock

import pytest
import yaml

from agent.provider_credentials import credential_transaction
from hermes_cli.config import (
    ConfigurationConflictError,
    DEFAULT_CONFIG,
    get_hermes_home,
    get_config_path,
    get_env_path,
    ensure_hermes_home,
    get_compatible_custom_providers,
    load_config,
    load_config_snapshot,
    load_env,
    load_raw_config_snapshot,
    migrate_config,
    remove_env_value,
    save_config,
    save_anthropic_api_key,
    save_anthropic_oauth_token,
    save_env_value,
    save_env_value_secure,
    sanitize_env_file,
    use_anthropic_claude_code_credentials,
    _sanitize_env_lines,
)
from hermes_cli.default_soul import DEFAULT_SOUL_MD, LEGACY_DEFAULT_SOUL_MD


class TestGetHermesHome:
    def test_default_path(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERMES_HOME", None)
            home = get_hermes_home()
            assert home == Path.home() / ".hermes"

    def test_env_override(self):
        with patch.dict(os.environ, {"HERMES_HOME": "/custom/path"}):
            home = get_hermes_home()
            assert home == Path("/custom/path")


class TestCredentialPaths:
    def test_explicit_config_override_pairs_config_env_and_lock_directory(
        self,
        tmp_path,
        monkeypatch,
    ):
        home = tmp_path / "home"
        override = tmp_path / "override" / "custom-config.yaml"
        override.parent.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(override))
        monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)

        assert get_config_path() == override
        assert get_env_path() == override.parent / ".env"

        save_env_value("OVERRIDE_API_KEY", "override-secret")

        assert "OVERRIDE_API_KEY=override-secret" in (
            override.parent / ".env"
        ).read_text(encoding="utf-8")
        assert (
            override.parent / ".taiji-credential-transaction.lock"
        ).is_file()
        assert not (home / ".env").exists()
        assert not (home / ".taiji-credential-transaction.lock").exists()

    def test_taiji_runtime_home_wins_over_legacy_config_override(
        self,
        tmp_path,
        monkeypatch,
    ):
        runtime_home = tmp_path / "runtime-home"
        monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
        monkeypatch.setenv(
            "HERMES_CONFIG_PATH",
            str(tmp_path / "legacy" / "config.yaml"),
        )
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "legacy-home"))

        assert get_config_path() == runtime_home / "config.yaml"
        assert get_env_path() == runtime_home / ".env"


class TestEnsureHermesHome:
    def test_creates_subdirs(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            ensure_hermes_home()
            assert (tmp_path / "cron").is_dir()
            assert (tmp_path / "sessions").is_dir()
            assert (tmp_path / "logs").is_dir()
            assert (tmp_path / "memories").is_dir()

    def test_creates_default_soul_md_if_missing(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            ensure_hermes_home()
            soul_path = tmp_path / "SOUL.md"
            assert soul_path.exists()
            content = soul_path.read_text(encoding="utf-8")
            assert content == DEFAULT_SOUL_MD
            assert "taiji Agent" in content
            assert "Hermes" not in content
            assert "Nous Research" not in content

    def test_migrates_legacy_default_soul_md(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            soul_path = tmp_path / "SOUL.md"
            soul_path.write_text(LEGACY_DEFAULT_SOUL_MD, encoding="utf-8")
            ensure_hermes_home()
            content = soul_path.read_text(encoding="utf-8")
            assert content == DEFAULT_SOUL_MD
            assert "taiji Agent" in content
            assert "Hermes" not in content
            assert "Nous Research" not in content

    def test_does_not_overwrite_existing_soul_md(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            soul_path = tmp_path / "SOUL.md"
            soul_path.write_text("custom soul", encoding="utf-8")
            ensure_hermes_home()
            assert soul_path.read_text(encoding="utf-8") == "custom soul"

    def test_does_not_migrate_modified_legacy_soul_md(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            soul_path = tmp_path / "SOUL.md"
            content = f"{LEGACY_DEFAULT_SOUL_MD}\n"
            soul_path.write_text(content, encoding="utf-8")
            ensure_hermes_home()
            assert soul_path.read_text(encoding="utf-8") == content


class TestLoadConfigDefaults:
    def test_returns_defaults_when_no_file(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config = load_config()
            assert config["model"] == DEFAULT_CONFIG["model"]
            assert config["agent"]["max_turns"] == DEFAULT_CONFIG["agent"]["max_turns"]
            assert "max_turns" not in config
            assert "terminal" in config
            assert config["terminal"]["backend"] == "local"
            assert config["display"]["interim_assistant_messages"] is True

    def test_legacy_root_level_max_turns_migrates_to_agent_config(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config_path = tmp_path / "config.yaml"
            config_path.write_text("max_turns: 42\n")

            config = load_config()
            assert config["agent"]["max_turns"] == 42
            assert "max_turns" not in config


class TestLoadConfigParseFailure:
    """A YAML parse failure must NOT silently fall back to defaults.

    Before issue #23570 this was a single ``print(...)`` that scrolled past
    on the first invocation — users saw aux-fallback misbehavior with no clue
    their config.yaml was being ignored. The helper must:
      * log at WARNING (so ``hermes logs`` surfaces it)
      * also write to stderr (so it's visible at startup even before
        ``setup_logging()`` has wired up file handlers)
      * dedup on (path, mtime_ns, size) so concurrent loads don't spam
      * re-warn after the user edits the file (different mtime)
    """

    def test_logs_and_warns_on_parse_failure(self, tmp_path, caplog, capsys):
        # Reset the dedup cache so this test isn't affected by other tests
        # that may have warned about a different broken config.
        from hermes_cli import config as cfg_mod
        cfg_mod._CONFIG_PARSE_WARNED.clear()

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            (tmp_path / "config.yaml").write_text("\tbroken tab indent:\n")

            import logging
            with caplog.at_level(logging.WARNING, logger="hermes_cli.config"):
                config = load_config()

            # Falls back to defaults — confirms the silent-fallback we're warning about
            assert config["model"] == DEFAULT_CONFIG["model"]

            # WARNING-level log was emitted with file path + reason
            assert any(
                str(tmp_path / "config.yaml") in rec.message
                and "Falling back to default config" in rec.message
                for rec in caplog.records
            ), f"expected WARNING log, got: {[r.message for r in caplog.records]}"

            # stderr also got a user-visible message (with the ⚠️ marker so it
            # stands out at hermes startup before logging is configured)
            captured = capsys.readouterr()
            assert "hermes config:" in captured.err
            assert str(tmp_path / "config.yaml") in captured.err

    def test_dedup_on_repeated_load_same_file(self, tmp_path, capsys):
        from hermes_cli import config as cfg_mod
        cfg_mod._CONFIG_PARSE_WARNED.clear()

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            (tmp_path / "config.yaml").write_text("\tbroken:\n")

            load_config()
            first = capsys.readouterr().err
            assert "hermes config:" in first

            load_config()
            second = capsys.readouterr().err
            assert second == "", "second load should NOT re-warn (same file, same mtime)"

    def test_rewarns_after_file_edit(self, tmp_path, capsys):
        import time
        from hermes_cli import config as cfg_mod
        cfg_mod._CONFIG_PARSE_WARNED.clear()

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            (tmp_path / "config.yaml").write_text("\tbroken:\n")
            load_config()
            capsys.readouterr()  # discard first warning

            # Edit the file (still broken, but different content) — mtime changes
            time.sleep(0.05)
            (tmp_path / "config.yaml").write_text("\tstill broken differently:\n")
            load_config()
            after_edit = capsys.readouterr().err
            assert "hermes config:" in after_edit, "edited file should re-warn"


class TestSaveAndLoadRoundtrip:
    def test_roundtrip(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config = load_config()
            config["model"] = "test/custom-model"
            config["agent"]["max_turns"] = 42
            save_config(config)

            reloaded = load_config()
            assert reloaded["model"] == "test/custom-model"
            assert reloaded["agent"]["max_turns"] == 42

            saved = yaml.safe_load((tmp_path / "config.yaml").read_text())
            assert saved["agent"]["max_turns"] == 42
            assert "max_turns" not in saved

    def test_pre_reload_save_reference_keeps_optional_sentinel_identity(
        self,
        tmp_path,
        monkeypatch,
    ):
        import importlib
        import hermes_cli.config as config_module

        old_save_config = config_module.save_config
        old_load_snapshot = config_module.load_config_snapshot
        old_sentinels = (
            config_module._CONFIG_MERGE_MISSING,
            config_module._NO_CONFIG_SNAPSHOT,
            config_module._NO_RAW_CONFIG_SNAPSHOT,
        )
        old_error_types = (
            config_module.ManagedConfigurationError,
            config_module.ConfigurationConflictError,
            config_module.WebConfigValidationError,
        )

        importlib.reload(config_module)

        assert old_sentinels == (
            config_module._CONFIG_MERGE_MISSING,
            config_module._NO_CONFIG_SNAPSHOT,
            config_module._NO_RAW_CONFIG_SNAPSHOT,
        )
        assert old_error_types == (
            config_module.ManagedConfigurationError,
            config_module.ConfigurationConflictError,
            config_module.WebConfigValidationError,
        )
        monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        old_save_config({"agent": {"max_turns": 17}})
        draft = old_load_snapshot()
        draft.config["agent"]["max_turns"] = 18
        old_save_config(draft.config, snapshot_token=draft.token)

        saved = yaml.safe_load(
            (tmp_path / "config.yaml").read_text(encoding="utf-8")
        )
        assert saved["agent"]["max_turns"] == 18

    def test_save_config_normalizes_legacy_root_level_max_turns(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            save_config({"model": "test/custom-model", "max_turns": 37})

            saved = yaml.safe_load((tmp_path / "config.yaml").read_text())
            assert saved["agent"]["max_turns"] == 37
            assert "max_turns" not in saved

    def test_nested_values_preserved(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config = load_config()
            config["terminal"]["timeout"] = 999
            save_config(config)

            reloaded = load_config()
            assert reloaded["terminal"]["timeout"] == 999

    def test_save_rejects_malformed_existing_config_without_overwriting(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config_path = tmp_path / "config.yaml"
            malformed = b"model:\n  provider: openrouter\nprovider_credentials: [\n"
            config_path.write_bytes(malformed)

            with pytest.raises(ValueError, match="cannot be read safely"):
                save_config({"model": "gpt-4o"})

            assert config_path.read_bytes() == malformed

    def test_save_rejects_duplicate_mapping_without_overwriting(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config_path = tmp_path / "config.yaml"
            ambiguous = b"model: gpt-4o\nmodel: attacker-shadow\n"
            config_path.write_bytes(ambiguous)

            with pytest.raises(ValueError, match="duplicate"):
                save_config({"model": "safe-replacement"})

            assert config_path.read_bytes() == ambiguous

    def test_stale_loaded_snapshot_preserves_unrelated_canonical_mutation(
        self,
        tmp_path,
    ):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            stale = load_config()
            stale["agent"]["max_turns"] = 37

            (tmp_path / "config.yaml").write_text(
                yaml.safe_dump(
                    {
                        "provider_credentials": {
                            "concurrent-provider": {
                                "kind": "api_key",
                                "secret_env": "CONCURRENT_PROVIDER_API_KEY",
                            }
                        }
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            save_config(stale)

            saved = yaml.safe_load(
                (tmp_path / "config.yaml").read_text(encoding="utf-8")
            )
            assert saved["agent"]["max_turns"] == 37
            assert saved["provider_credentials"]["concurrent-provider"] == {
                "kind": "api_key",
                "secret_env": "CONCURRENT_PROVIDER_API_KEY",
            }

    def test_deepcopied_stale_snapshot_preserves_unrelated_canonical_mutation(
        self,
        tmp_path,
    ):
        import copy

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            stale = copy.deepcopy(load_config())
            stale["agent"]["max_turns"] = 41

            (tmp_path / "config.yaml").write_text(
                yaml.safe_dump(
                    {"concurrent_unrelated": {"kept": True}},
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            save_config(stale)

            saved = yaml.safe_load(
                (tmp_path / "config.yaml").read_text(encoding="utf-8")
            )
            assert saved["agent"]["max_turns"] == 41
            assert saved["concurrent_unrelated"] == {"kept": True}

    def test_plain_dict_snapshot_survives_env_rotation_and_concurrent_update(
        self,
        tmp_path,
        monkeypatch,
    ):
        config_path = tmp_path / "config.yaml"
        original_secret = "secret-at-load-time"
        rotated_secret = "secret-after-rotation"
        raw = {
            "agent": {"max_turns": 10},
            "custom_providers": [
                {
                    "name": "rotating-provider",
                    "api_key": "${ROTATING_PROVIDER_API_KEY}",
                    "model": "test/model",
                }
            ],
        }
        config_path.write_text(
            yaml.safe_dump(raw, sort_keys=False),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            monkeypatch.setenv(
                "ROTATING_PROVIDER_API_KEY",
                original_secret,
            )
            draft = load_config_snapshot()
            stale_plain_dict = dict(draft.config)
            assert (
                stale_plain_dict["custom_providers"][0]["api_key"]
                == original_secret
            )

            concurrent = {
                **raw,
                "concurrent_unrelated": {"kept": True},
            }
            config_path.write_text(
                yaml.safe_dump(concurrent, sort_keys=False),
                encoding="utf-8",
            )
            monkeypatch.setenv(
                "ROTATING_PROVIDER_API_KEY",
                rotated_secret,
            )
            # A later reader refreshes the process-wide expanded cache. The
            # stale plain dict must still retain its own load-time base.
            assert (
                load_config()["custom_providers"][0]["api_key"]
                == rotated_secret
            )

            stale_plain_dict["agent"]["max_turns"] = 37
            save_config(
                stale_plain_dict,
                snapshot_token=draft.token,
            )

        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert saved["agent"]["max_turns"] == 37
        assert saved["concurrent_unrelated"] == {"kept": True}
        assert (
            saved["custom_providers"][0]["api_key"]
            == "${ROTATING_PROVIDER_API_KEY}"
        )
        persisted_text = config_path.read_text(encoding="utf-8")
        assert original_secret not in persisted_text
        assert rotated_secret not in persisted_text

    def test_independent_plain_dict_snapshots_do_not_share_mutable_base(
        self,
        tmp_path,
    ):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            first_draft = load_config_snapshot()
            second_draft = load_config_snapshot()
            assert first_draft.token == second_draft.token
            first = dict(first_draft.config)
            second = dict(second_draft.config)

            first["agent"]["max_turns"] = 20
            save_config(first, snapshot_token=first_draft.token)
            second["display"]["skin"] = "compact"
            save_config(second, snapshot_token=second_draft.token)

            saved = yaml.safe_load(
                (tmp_path / "config.yaml").read_text(encoding="utf-8")
            )
            assert saved["agent"]["max_turns"] == 20
            assert saved["display"]["skin"] == "compact"

    def test_loaded_snapshot_json_does_not_expose_internal_token(
        self,
        tmp_path,
    ):
        import json

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            loaded = load_config()
            encoded = json.dumps(loaded)
            dumped_yaml = yaml.safe_dump(loaded)

        assert "config_snapshot_token" not in encoded
        assert "config_snapshot_token" not in dumped_yaml

    def test_snapshot_repr_redacts_config_secret_and_token(
        self,
        tmp_path,
        monkeypatch,
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "secret: ${SNAPSHOT_REPR_SECRET}\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("SNAPSHOT_REPR_SECRET", "repr-secret-value")

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            snapshot = load_config_snapshot()

        rendered = repr(snapshot)
        assert "repr-secret-value" not in rendered
        assert snapshot.token not in rendered

    def test_raw_snapshot_repr_redacts_yaml_and_token(
        self,
        tmp_path,
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "model: safe-model\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            snapshot = load_raw_config_snapshot()

        rendered = repr(snapshot)
        assert "safe-model" not in rendered
        assert snapshot.token not in rendered

    def test_evicted_plain_dict_snapshot_fails_closed(
        self,
        tmp_path,
    ):
        from hermes_cli.config import _LOADED_CONFIG_SNAPSHOT_LIMIT

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config_path = tmp_path / "config.yaml"
            config_path.write_text("revision: -1\n", encoding="utf-8")
            stale_draft = load_config_snapshot()
            stale = dict(stale_draft.config)
            for revision in range(_LOADED_CONFIG_SNAPSHOT_LIMIT):
                config_path.write_text(
                    f"revision: {revision}\n",
                    encoding="utf-8",
                )
                load_config_snapshot()
            before = config_path.read_bytes()

            stale["agent"]["max_turns"] = 91
            with pytest.raises(ConfigurationConflictError) as exc:
                save_config(stale, snapshot_token=stale_draft.token)

        assert exc.value.code == "configuration_conflict"
        assert exc.value.path == ("<snapshot>",)
        after = config_path.read_bytes()
        assert after == before

    def test_explicit_missing_snapshot_cannot_replace_existing_config(
        self,
        tmp_path,
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("model: original\n", encoding="utf-8")

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            with pytest.raises(ConfigurationConflictError) as exc:
                save_config(
                    {"model": "replacement"},
                    snapshot_token=None,
                )

        assert exc.value.path == ("<snapshot>",)
        assert config_path.read_text(encoding="utf-8") == "model: original\n"

    def test_legacy_plain_dict_writer_remains_supported(
        self,
        tmp_path,
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("model: original\n", encoding="utf-8")

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            save_config({"model": "replacement"})

        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert saved["model"] == "replacement"

    def test_successful_direct_save_refreshes_only_that_draft_token(
        self,
        tmp_path,
    ):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            first = load_config()
            independent = load_config()
            shared_token = first._save_token
            assert independent._save_token == shared_token

            first["model"] = "first-save"
            save_config(first)
            assert first._save_token != shared_token
            assert independent._save_token == shared_token

            first["model"] = "second-save"
            save_config(first)

        saved = yaml.safe_load(
            (tmp_path / "config.yaml").read_text(encoding="utf-8")
        )
        assert saved["model"] == "second-save"

    def test_repeated_direct_save_keeps_concurrent_field_merged_on_first_save(
        self,
        tmp_path,
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "agent:\n  max_turns: 10\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            draft = load_config()
            draft["agent"]["max_turns"] = 11
            config_path.write_text(
                (
                    "agent:\n"
                    "  max_turns: 10\n"
                    "concurrent_unrelated:\n"
                    "  kept: true\n"
                ),
                encoding="utf-8",
            )

            save_config(draft)
            assert draft["concurrent_unrelated"] == {"kept": True}

            draft["agent"]["max_turns"] = 12
            save_config(draft)

        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert saved["agent"]["max_turns"] == 12
        assert saved["concurrent_unrelated"] == {"kept": True}

    def test_ordinary_reads_do_not_evict_same_revision_snapshot(
        self,
        tmp_path,
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("agent:\n  max_turns: 10\n", encoding="utf-8")

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            draft = load_config_snapshot()
            for _ in range(100):
                load_config()
            draft.config["agent"]["max_turns"] = 11
            save_config(draft.config, snapshot_token=draft.token)

        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert saved["agent"]["max_turns"] == 11

    def test_stale_snapshot_delete_conflicts_with_concurrent_update(
        self,
        tmp_path,
    ):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config_path = tmp_path / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {"conflict": {"delete_me": "base"}},
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            stale = load_config()
            del stale["conflict"]["delete_me"]
            concurrent = {"conflict": {"delete_me": "concurrent"}}
            config_path.write_text(
                yaml.safe_dump(concurrent, sort_keys=False),
                encoding="utf-8",
            )

            with pytest.raises(ConfigurationConflictError) as exc:
                save_config(stale)

            assert exc.value.code == "configuration_conflict"
            assert exc.value.path == ("conflict", "delete_me")
            assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == concurrent

    def test_stale_snapshot_add_conflicts_with_concurrent_add(
        self,
        tmp_path,
    ):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config_path = tmp_path / "config.yaml"
            config_path.write_text(
                yaml.safe_dump({"conflict": {}}, sort_keys=False),
                encoding="utf-8",
            )
            stale = load_config()
            stale["conflict"]["new_key"] = "caller-secret-value"
            concurrent = {
                "conflict": {"new_key": "concurrent-secret-value"}
            }
            config_path.write_text(
                yaml.safe_dump(concurrent, sort_keys=False),
                encoding="utf-8",
            )

            with pytest.raises(ConfigurationConflictError) as exc:
                save_config(stale)

            assert exc.value.code == "configuration_conflict"
            assert exc.value.path == ("conflict", "new_key")
            assert "caller-secret-value" not in str(exc.value)
            assert "concurrent-secret-value" not in str(exc.value)
            assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == concurrent

    def test_nested_type_change_conflicts_instead_of_overwrite(
        self,
        tmp_path,
    ):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config_path = tmp_path / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {"conflict": {"node": {"child": "base"}}},
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            stale = load_config()
            stale["conflict"]["node"] = "caller"
            concurrent = {"conflict": {"node": {"child": "concurrent"}}}
            config_path.write_text(
                yaml.safe_dump(concurrent, sort_keys=False),
                encoding="utf-8",
            )

            with pytest.raises(ConfigurationConflictError) as exc:
                save_config(stale)

            assert exc.value.code == "configuration_conflict"
            assert exc.value.path == ("conflict", "node")
            assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == concurrent

    def test_three_way_merge_treats_bool_and_int_as_distinct_values(
        self,
        tmp_path,
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {"typed": {"value": 1}},
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            stale = load_config()
            stale["typed"]["value"] = True
            save_config(stale)

        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert saved["typed"]["value"] is True

    def test_three_way_merge_treats_unchanged_nan_as_equal(
        self,
        tmp_path,
    ):
        import math

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "typed:\n  value: .nan\nagent:\n  max_turns: 10\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            draft = load_config()
            draft["agent"]["max_turns"] = 11
            save_config(draft)

        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert math.isnan(saved["typed"]["value"])
        assert saved["agent"]["max_turns"] == 11

    @pytest.mark.parametrize("writer", [save_config, save_env_value])
    def test_managed_mode_writers_raise_instead_of_reporting_success(
        self,
        writer,
        tmp_path,
    ):
        with patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(tmp_path),
                "HERMES_MANAGED": "nixos",
            },
        ):
            args = ({"model": "test/model"},) if writer is save_config else (
                "TEST_API_KEY",
                "secret",
            )
            with pytest.raises(RuntimeError, match="managed by NixOS") as exc:
                writer(*args)

            assert getattr(exc.value, "code", None) == "managed_configuration"
            assert not (tmp_path / "config.yaml").exists()
            assert not (tmp_path / ".env").exists()
            assert not (
                tmp_path / ".taiji-credential-transaction.lock"
            ).exists()


class TestSaveEnvValueSecure:
    def test_save_env_value_writes_without_stdout(self, tmp_path, capsys):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            save_env_value("TENOR_API_KEY", "sk-test-secret")
            captured = capsys.readouterr()
            assert captured.out == ""
            assert captured.err == ""

            env_values = load_env()
            assert env_values["TENOR_API_KEY"] == "sk-test-secret"

    def test_save_env_value_collapses_duplicate_keys_to_one_value(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            env_path = tmp_path / ".env"
            env_path.write_text(
                "ROTATE_KEY=older\n"
                "UNCHANGED_KEY=keep\n"
                "ROTATE_KEY=newer\n",
                encoding="utf-8",
            )

            save_env_value("ROTATE_KEY", "current")

            lines = env_path.read_text(encoding="utf-8").splitlines()
            assert [line for line in lines if line.startswith("ROTATE_KEY=")] == [
                "ROTATE_KEY=current"
            ]
            assert "UNCHANGED_KEY=keep" in lines

    def test_secure_save_returns_metadata_only(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            result = save_env_value_secure("GITHUB_TOKEN", "ghp_test_secret")
            assert result == {
                "success": True,
                "stored_as": "GITHUB_TOKEN",
                "validated": False,
            }
            assert "secret" not in str(result).lower()

    def test_secure_save_returns_machine_readable_managed_failure(self, tmp_path):
        with patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(tmp_path),
                "HERMES_MANAGED": "nixos",
            },
        ):
            result = save_env_value_secure(
                "GITHUB_TOKEN",
                "ghp_must_not_be_returned",
            )

        assert result == {
            "success": False,
            "stored_as": "GITHUB_TOKEN",
            "validated": False,
            "reason": "managed_configuration",
            "error_code": "managed_configuration",
            "message": (
                "Cannot set GITHUB_TOKEN: this Hermes installation is managed "
                "by NixOS (HERMES_MANAGED=nixos).\n"
                "Edit services.hermes-agent.settings in your configuration.nix "
                "and run:\n"
                "  sudo nixos-rebuild switch"
            ),
        }
        assert "ghp_must_not_be_returned" not in str(result)
        assert not (tmp_path / ".env").exists()

    def test_save_env_value_updates_process_environment(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}, clear=False):
            os.environ.pop("TENOR_API_KEY", None)
            save_env_value("TENOR_API_KEY", "sk-test-secret")
            assert os.environ["TENOR_API_KEY"] == "sk-test-secret"

    def test_save_env_value_hardens_file_permissions_on_posix(self, tmp_path):
        if os.name == "nt":
            return

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            save_env_value("TENOR_API_KEY", "sk-test-secret")
            env_mode = (tmp_path / ".env").stat().st_mode & 0o777
            assert env_mode == 0o600


@pytest.mark.parametrize(
    "invoke",
    [
        lambda writer: save_anthropic_oauth_token("oauth-token", writer),
        lambda writer: use_anthropic_claude_code_credentials(writer),
        lambda writer: save_anthropic_api_key("anthropic-key", writer),
    ],
)
def test_anthropic_pair_writers_hold_one_outer_transaction(invoke):
    from agent import provider_credentials

    observed_depths = []

    def writer(_key, _value):
        observed_depths.append(
            int(
                getattr(
                    provider_credentials._CREDENTIAL_TRANSACTION_STATE,
                    "depth",
                    0,
                )
            )
        )

    invoke(writer)

    assert observed_depths == [1, 1]


class TestRemoveEnvValue:
    def test_managed_mode_rejects_before_transaction_state_is_created(
        self,
        tmp_path,
    ):
        with patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(tmp_path),
                "HERMES_MANAGED": "nixos",
            },
        ):
            with pytest.raises(RuntimeError, match="managed by NixOS") as exc:
                remove_env_value("TEST_API_KEY")

        assert getattr(exc.value, "code", None) == "managed_configuration"
        assert not (tmp_path / ".env").exists()
        assert not (
            tmp_path / ".taiji-credential-transaction.lock"
        ).exists()

    def test_removes_key_from_env_file(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("KEY_A=value_a\nKEY_B=value_b\nKEY_C=value_c\n")
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path), "KEY_B": "value_b"}):
            result = remove_env_value("KEY_B")
            assert result is True
            content = env_path.read_text()
            assert "KEY_B" not in content
            assert "KEY_A=value_a" in content
            assert "KEY_C=value_c" in content

    def test_removes_every_duplicate_occurrence(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "DUPLICATE_KEY=older\n"
            "KEEP_KEY=value\n"
            "DUPLICATE_KEY=newer\n",
            encoding="utf-8",
        )
        with patch.dict(
            os.environ,
            {"HERMES_HOME": str(tmp_path), "DUPLICATE_KEY": "newer"},
        ):
            assert remove_env_value("DUPLICATE_KEY") is True
            lines = env_path.read_text(encoding="utf-8").splitlines()
            assert not any(line.startswith("DUPLICATE_KEY=") for line in lines)
            assert "KEEP_KEY=value" in lines

    def test_clears_os_environ(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("MY_KEY=my_value\n")
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path), "MY_KEY": "my_value"}):
            remove_env_value("MY_KEY")
            assert "MY_KEY" not in os.environ

    def test_returns_false_when_key_not_found(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("OTHER_KEY=value\n")
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            result = remove_env_value("MISSING_KEY")
            assert result is False
            # File should be untouched
            assert env_path.read_text() == "OTHER_KEY=value\n"

    def test_handles_missing_env_file(self, tmp_path):
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path), "GHOST_KEY": "ghost"}):
            result = remove_env_value("GHOST_KEY")
            assert result is False
            # os.environ should still be cleared
            assert "GHOST_KEY" not in os.environ

    def test_clears_os_environ_even_when_not_in_file(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("OTHER=stuff\n")
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path), "ORPHAN_KEY": "orphan"}):
            remove_env_value("ORPHAN_KEY")
            assert "ORPHAN_KEY" not in os.environ

    @pytest.mark.parametrize(
        ("writer", "initial"),
        [
            (lambda: remove_env_value("KEY_B"), "KEY_A=a\nKEY_B=b\n"),
            (sanitize_env_file, "KEY_A=aKEY_B=b\n"),
        ],
    )
    def test_env_rewriters_wait_for_the_shared_transaction(
        self,
        tmp_path,
        writer,
        initial,
    ):
        env_path = tmp_path / ".env"
        env_path.write_text(initial, encoding="utf-8")
        started = threading.Event()
        finished = threading.Event()
        errors = []

        def run_writer():
            started.set()
            try:
                writer()
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                finished.set()

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            with credential_transaction(tmp_path / "config.yaml"):
                thread = threading.Thread(target=run_writer, daemon=True)
                thread.start()
                assert started.wait(1)
                assert not finished.wait(0.1)

            thread.join(timeout=2)

        assert not thread.is_alive()
        assert errors == []


class TestSaveConfigAtomicity:
    """Verify save_config uses atomic writes (tempfile + os.replace)."""

    def test_no_partial_write_on_crash(self, tmp_path):
        """If save_config crashes mid-write, the previous file stays intact."""
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            # Write an initial config
            config = load_config()
            config["model"] = "original-model"
            save_config(config)

            config_path = tmp_path / "config.yaml"
            assert config_path.exists()

            # Simulate a crash during yaml.dump by making atomic_yaml_write's
            # yaml.dump raise after the temp file is created but before replace.
            with patch("utils.yaml.dump", side_effect=OSError("disk full")):
                try:
                    config["model"] = "should-not-persist"
                    save_config(config)
                except OSError:
                    pass

            # Original file must still be intact
            reloaded = load_config()
            assert reloaded["model"] == "original-model"

    def test_no_leftover_temp_files(self, tmp_path):
        """Failed writes must clean up their temp files."""
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config = load_config()
            save_config(config)

            with patch("utils.yaml.dump", side_effect=OSError("disk full")):
                try:
                    save_config(config)
                except OSError:
                    pass

            # No .tmp files should remain
            tmp_files = list(tmp_path.glob(".*config*.tmp"))
            assert tmp_files == []

    def test_atomic_write_creates_valid_yaml(self, tmp_path):
        """The written file must be valid YAML matching the input."""
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            config = load_config()
            config["model"] = "test/atomic-model"
            config["agent"]["max_turns"] = 77
            save_config(config)

            # Read raw YAML to verify it's valid and correct
            config_path = tmp_path / "config.yaml"
            with open(config_path) as f:
                raw = yaml.safe_load(f)
            assert raw["model"] == "test/atomic-model"
            assert raw["agent"]["max_turns"] == 77


class TestSanitizeEnvLines:
    """Tests for .env file corruption repair."""

    def test_splits_concatenated_keys(self):
        """Two KEY=VALUE pairs jammed on one line get split."""
        lines = ["ANTHROPIC_API_KEY=sk-ant-xxxOPENAI_BASE_URL=https://api.openai.com/v1\n"]
        result = _sanitize_env_lines(lines)
        assert result == [
            "ANTHROPIC_API_KEY=sk-ant-xxx\n",
            "OPENAI_BASE_URL=https://api.openai.com/v1\n",
        ]

    def test_preserves_clean_file(self):
        """A well-formed .env file passes through unchanged (modulo trailing newlines)."""
        lines = [
            "OPENROUTER_API_KEY=sk-or-xxx\n",
            "FIRECRAWL_API_KEY=fc-xxx\n",
            "# a comment\n",
            "\n",
        ]
        result = _sanitize_env_lines(lines)
        assert result == lines

    def test_preserves_comments_and_blanks(self):
        lines = ["# comment\n", "\n", "KEY=val\n"]
        result = _sanitize_env_lines(lines)
        assert result == lines

    def test_adds_missing_trailing_newline(self):
        """Lines missing trailing newline get one added."""
        lines = ["FOO_BAR=baz"]
        result = _sanitize_env_lines(lines)
        assert result == ["FOO_BAR=baz\n"]

    def test_three_concatenated_keys(self):
        """Three known keys on one line all get separated."""
        lines = ["FAL_KEY=111FIRECRAWL_API_KEY=222GITHUB_TOKEN=333\n"]
        result = _sanitize_env_lines(lines)
        assert result == [
            "FAL_KEY=111\n",
            "FIRECRAWL_API_KEY=222\n",
            "GITHUB_TOKEN=333\n",
        ]

    def test_value_with_equals_sign_not_split(self):
        """A value containing '=' shouldn't be falsely split (lowercase in value)."""
        lines = ["OPENAI_BASE_URL=https://api.example.com/v1?key=abc123\n"]
        result = _sanitize_env_lines(lines)
        assert result == lines

    def test_unknown_keys_not_split(self):
        """Unknown key names on one line are NOT split (avoids false positives)."""
        lines = ["CUSTOM_VAR=value123OTHER_THING=value456\n"]
        result = _sanitize_env_lines(lines)
        # Unknown keys stay on one line — no false split
        assert len(result) == 1

    def test_value_ending_with_digits_still_splits(self):
        """Concatenation is detected even when value ends with digits."""
        lines = ["OPENROUTER_API_KEY=sk-or-v1-abc123OPENAI_BASE_URL=https://api.openai.com/v1\n"]
        result = _sanitize_env_lines(lines)
        assert len(result) == 2
        assert result[0].startswith("OPENROUTER_API_KEY=")
        assert result[1].startswith("OPENAI_BASE_URL=")

    def test_glm_suffix_collision_not_split(self):
        """GLM_API_KEY / GLM_BASE_URL must not be mangled by LM_API_KEY / LM_BASE_URL suffixes (#17138)."""
        lines = [
            "GLM_API_KEY=glm-secret\n",
            "GLM_BASE_URL=https://api.z.ai/api/paas/v4\n",
        ]
        result = _sanitize_env_lines(lines)
        assert result == lines, f"GLM_* lines were corrupted by suffix collision: {result}"

    def test_suffix_collision_does_not_break_real_concatenation(self):
        """A genuine concatenation that happens to start with a suffix-superset key still splits."""
        lines = ["GLM_API_KEY=glmLM_API_KEY=lm-key\n"]
        result = _sanitize_env_lines(lines)
        assert len(result) == 2
        assert result[0].startswith("GLM_API_KEY=")
        assert result[1].startswith("LM_API_KEY=")

    def test_save_env_value_fixes_corruption_on_write(self, tmp_path):
        """save_env_value sanitizes corrupted lines when writing a new key."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "ANTHROPIC_API_KEY=sk-antOPENAI_BASE_URL=https://api.openai.com/v1\n"
            "FAL_KEY=existing\n"
        )
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            save_env_value("MESSAGING_CWD", "/tmp")

            content = env_file.read_text()
            lines = content.strip().split("\n")

            # Corrupted line should be split, new key added
            assert "ANTHROPIC_API_KEY=sk-ant" in lines
            assert "OPENAI_BASE_URL=https://api.openai.com/v1" in lines
            assert "MESSAGING_CWD=/tmp" in lines

    def test_sanitize_env_file_returns_fix_count(self, tmp_path):
        """sanitize_env_file reports how many entries were fixed."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "FAL_KEY=good\n"
            "OPENROUTER_API_KEY=valFIRECRAWL_API_KEY=val2\n"
        )
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            fixes = sanitize_env_file()
            assert fixes > 0

            # Verify file is now clean
            content = env_file.read_text()
            assert "OPENROUTER_API_KEY=val\n" in content
            assert "FIRECRAWL_API_KEY=val2\n" in content

    def test_sanitize_env_file_noop_on_clean_file(self, tmp_path):
        """No changes when file is already clean."""
        env_file = tmp_path / ".env"
        env_file.write_text("GOOD_KEY=good\nOTHER_KEY=other\n")
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            fixes = sanitize_env_file()
            assert fixes == 0


class TestOptionalEnvVarsRegistry:
    """Verify that key env vars are registered in OPTIONAL_ENV_VARS."""

    def test_tavily_api_key_registered(self):
        """TAVILY_API_KEY is listed in OPTIONAL_ENV_VARS."""
        from hermes_cli.config import OPTIONAL_ENV_VARS
        assert "TAVILY_API_KEY" in OPTIONAL_ENV_VARS

    def test_tavily_api_key_is_tool_category(self):
        """TAVILY_API_KEY is in the 'tool' category."""
        from hermes_cli.config import OPTIONAL_ENV_VARS
        assert OPTIONAL_ENV_VARS["TAVILY_API_KEY"]["category"] == "tool"

    def test_tavily_api_key_is_password(self):
        """TAVILY_API_KEY is marked as password."""
        from hermes_cli.config import OPTIONAL_ENV_VARS
        assert OPTIONAL_ENV_VARS["TAVILY_API_KEY"]["password"] is True

    def test_tavily_api_key_has_url(self):
        """TAVILY_API_KEY has a URL."""
        from hermes_cli.config import OPTIONAL_ENV_VARS
        assert OPTIONAL_ENV_VARS["TAVILY_API_KEY"]["url"] == "https://app.tavily.com/home"

    def test_tavily_in_env_vars_by_version(self):
        """TAVILY_API_KEY is listed in ENV_VARS_BY_VERSION."""
        from hermes_cli.config import ENV_VARS_BY_VERSION
        all_vars = []
        for vars_list in ENV_VARS_BY_VERSION.values():
            all_vars.extend(vars_list)
        assert "TAVILY_API_KEY" in all_vars


class TestConfigMigrationSecretPrompts:
    def test_noninteractive_config_rmw_holds_a_scoped_transaction(
        self,
        tmp_path,
        monkeypatch,
    ):
        from hermes_cli import config as cfg_mod

        depth = 0
        observations = []

        @contextmanager
        def tracked_transaction(_config_path=None):
            nonlocal depth
            depth += 1
            try:
                yield
            finally:
                depth -= 1

        def tracked_raw_load():
            observations.append(("raw-load", depth))
            return {"_config_version": 22}

        def tracked_merged_load():
            observations.append(("merged-load", depth))
            return {"_config_version": 22}

        def tracked_save(_config):
            observations.append(("save", depth))

        monkeypatch.setattr(
            cfg_mod,
            "_credential_files_transaction",
            tracked_transaction,
        )
        monkeypatch.setattr(cfg_mod, "sanitize_env_file", lambda: 0)
        monkeypatch.setattr(cfg_mod, "check_config_version", lambda: (22, 23))
        monkeypatch.setattr(cfg_mod, "read_raw_config", tracked_raw_load)
        monkeypatch.setattr(cfg_mod, "load_config", tracked_merged_load)
        monkeypatch.setattr(cfg_mod, "save_config", tracked_save)
        monkeypatch.setattr(cfg_mod, "get_missing_env_vars", lambda **_kwargs: [])
        monkeypatch.setattr(cfg_mod, "get_missing_config_fields", lambda: [])
        monkeypatch.setattr(cfg_mod, "get_missing_skill_config_vars", lambda: [])
        monkeypatch.setattr(cfg_mod, "get_hermes_home", lambda: tmp_path)

        cfg_mod.migrate_config(interactive=False, quiet=True)

        assert observations
        assert all(
            observed_depth > 0
            for _operation, observed_depth in observations
        ), observations

    def test_every_noninteractive_migration_config_write_is_scoped(
        self,
        tmp_path,
        monkeypatch,
    ):
        from copy import deepcopy
        from hermes_cli import config as cfg_mod

        depth = 0
        observations = []
        raw_template = {
            "_config_version": 0,
            "custom_providers": [
                {
                    "name": "Legacy Provider",
                    "base_url": "https://provider.example.com/v1",
                }
            ],
            "stt": {
                "provider": "local",
                "model": "base",
            },
            "display": {
                "tool_progress_overrides": {
                    "telegram": "off",
                }
            },
            "compression": {
                "summary_model": "summary-model",
            },
            "plugins": {},
        }

        @contextmanager
        def tracked_transaction(_config_path=None):
            nonlocal depth
            depth += 1
            try:
                yield
            finally:
                depth -= 1

        def tracked_raw_load():
            observations.append(("raw-load", depth))
            return deepcopy(raw_template)

        def tracked_merged_load():
            observations.append(("merged-load", depth))
            return deepcopy(raw_template)

        def tracked_save(_config):
            observations.append(("save", depth))

        monkeypatch.setattr(
            cfg_mod,
            "_credential_files_transaction",
            tracked_transaction,
        )
        monkeypatch.setattr(cfg_mod, "sanitize_env_file", lambda: 0)
        monkeypatch.setattr(cfg_mod, "check_config_version", lambda: (0, 23))
        monkeypatch.setattr(cfg_mod, "read_raw_config", tracked_raw_load)
        monkeypatch.setattr(cfg_mod, "load_config", tracked_merged_load)
        monkeypatch.setattr(cfg_mod, "save_config", tracked_save)
        monkeypatch.setattr(cfg_mod, "get_env_value", lambda _key: None)
        monkeypatch.setattr(cfg_mod, "get_missing_env_vars", lambda **_kwargs: [])
        monkeypatch.setattr(cfg_mod, "get_missing_config_fields", lambda: [])
        monkeypatch.setattr(cfg_mod, "get_missing_skill_config_vars", lambda: [])
        monkeypatch.setattr(cfg_mod, "get_hermes_home", lambda: tmp_path)

        cfg_mod.migrate_config(interactive=False, quiet=True)

        assert any(operation == "save" for operation, _depth in observations)
        assert all(
            observed_depth > 0
            for _operation, observed_depth in observations
        ), observations

    def test_required_secret_env_prompt_uses_masked_prompt(self, tmp_path, monkeypatch):
        from hermes_cli import config as cfg_mod

        saved = {}

        monkeypatch.setattr(cfg_mod, "sanitize_env_file", lambda: 0)
        monkeypatch.setattr(cfg_mod, "check_config_version", lambda: (999, 999))
        monkeypatch.setattr(cfg_mod, "get_missing_config_fields", lambda: [])
        monkeypatch.setattr(cfg_mod, "get_missing_skill_config_vars", lambda: [])
        monkeypatch.setattr(
            cfg_mod,
            "get_missing_env_vars",
            lambda required_only=True: [
                {
                    "name": "TEST_API_KEY",
                    "description": "Test key",
                    "prompt": "Test API key",
                    "password": True,
                }
            ]
            if required_only
            else [],
        )
        def fake_masked_secret_prompt(prompt):
            saved["prompt"] = prompt
            return "secret"

        monkeypatch.setattr(cfg_mod, "masked_secret_prompt", fake_masked_secret_prompt)
        monkeypatch.setattr(
            cfg_mod,
            "save_env_value",
            lambda name, value: saved.update({name: value}),
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            results = cfg_mod.migrate_config(interactive=True, quiet=True)

        assert saved["prompt"] == "  Test API key: "
        assert saved["TEST_API_KEY"] == "secret"
        assert results["env_added"] == ["TEST_API_KEY"]


class TestAnthropicTokenMigration:
    """Test that config version 8→9 clears ANTHROPIC_TOKEN."""

    def _write_config_version(self, tmp_path, version):
        config_path = tmp_path / "config.yaml"
        import yaml
        config_path.write_text(yaml.safe_dump({"_config_version": version}))

    def test_clears_token_on_upgrade_to_v9(self, tmp_path):
        """ANTHROPIC_TOKEN is cleared unconditionally when upgrading to v9."""
        self._write_config_version(tmp_path, 8)
        (tmp_path / ".env").write_text("ANTHROPIC_TOKEN=old-token\n")
        with patch.dict(os.environ, {
            "HERMES_HOME": str(tmp_path),
            "ANTHROPIC_TOKEN": "old-token",
        }):
            migrate_config(interactive=False, quiet=True)
            assert load_env().get("ANTHROPIC_TOKEN") == ""

    def test_skips_on_version_9_or_later(self, tmp_path):
        """Already at v9 — ANTHROPIC_TOKEN is not touched."""
        self._write_config_version(tmp_path, 9)
        (tmp_path / ".env").write_text("ANTHROPIC_TOKEN=current-token\n")
        with patch.dict(os.environ, {
            "HERMES_HOME": str(tmp_path),
            "ANTHROPIC_TOKEN": "current-token",
        }):
            migrate_config(interactive=False, quiet=True)
            assert load_env().get("ANTHROPIC_TOKEN") == "current-token"


class TestCustomProviderCompatibility:
    """Custom provider compatibility across legacy and v12+ config schemas."""

    def test_v11_upgrade_moves_custom_providers_into_providers(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "_config_version": 11,
                    "model": {
                        "default": "openai/gpt-5.4",
                        "provider": "openrouter",
                    },
                    "custom_providers": [
                        {
                            "name": "OpenAI Direct",
                            "base_url": "https://api.openai.com/v1",
                            "api_key": "test-key",
                            "api_mode": "codex_responses",
                            "model": "gpt-5-mini",
                        }
                    ],
                    "fallback_providers": [
                        {"provider": "openai-direct", "model": "gpt-5-mini"}
                    ],
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            migrate_config(interactive=False, quiet=True)
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        from hermes_cli.config import DEFAULT_CONFIG
        assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]
        assert raw["providers"]["openai-direct"] == {
            "api": "https://api.openai.com/v1",
            "api_key": "test-key",
            "default_model": "gpt-5-mini",
            "name": "OpenAI Direct",
            "transport": "codex_responses",
        }
        # custom_providers removed by migration — runtime reads via compat layer
        assert "custom_providers" not in raw

    def test_providers_dict_resolves_at_runtime(self, tmp_path):
        """After migration deleted custom_providers, get_compatible_custom_providers
        still finds entries from the providers dict."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "_config_version": 17,
                    "providers": {
                        "openai-direct": {
                            "api": "https://api.openai.com/v1",
                            "api_key": "test-key",
                            "default_model": "gpt-5-mini",
                            "name": "OpenAI Direct",
                            "transport": "codex_responses",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            compatible = get_compatible_custom_providers()

        assert len(compatible) == 1
        assert compatible[0]["name"] == "OpenAI Direct"
        assert compatible[0]["base_url"] == "https://api.openai.com/v1"
        assert compatible[0]["provider_key"] == "openai-direct"
        assert compatible[0]["api_mode"] == "codex_responses"

    def test_compatible_custom_providers_prefers_base_url_then_url_then_api(self, tmp_path):
        """URL field precedence is base_url > url > api (PR #9332)."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "_config_version": 17,
                    "providers": {
                        "my-provider": {
                            "name": "My Provider",
                            "api": "https://api.example.com/v1",
                            "url": "https://url.example.com/v1",
                            "base_url": "https://base.example.com/v1",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            compatible = get_compatible_custom_providers()

        assert compatible == [
            {
                "name": "My Provider",
                "base_url": "https://base.example.com/v1",
                "provider_key": "my-provider",
            }
        ]

    def test_dedup_across_legacy_and_providers(self, tmp_path):
        """Same name+url in both schemas should not produce duplicates."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "_config_version": 17,
                    "custom_providers": [
                        {
                            "name": "OpenAI Direct",
                            "base_url": "https://api.openai.com/v1",
                            "api_key": "legacy-key",
                        }
                    ],
                    "providers": {
                        "openai-direct": {
                            "api": "https://api.openai.com/v1",
                            "api_key": "new-key",
                            "name": "OpenAI Direct",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            compatible = get_compatible_custom_providers()

        assert len(compatible) == 1
        # Legacy entry wins (read first)
        assert compatible[0]["api_key"] == "legacy-key"

    def test_dedup_preserves_entries_with_different_models(self, tmp_path):
        """Entries with same name+URL but different models must not be collapsed."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "_config_version": 17,
                    "custom_providers": [
                        {"name": "Ollama Cloud", "base_url": "https://ollama.com/v1", "model": "qwen3-coder"},
                        {"name": "Ollama Cloud", "base_url": "https://ollama.com/v1", "model": "glm-5.1"},
                        {"name": "Ollama Cloud", "base_url": "https://ollama.com/v1", "model": "kimi-k2.5"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            compatible = get_compatible_custom_providers()

        assert len(compatible) == 3
        models = [e.get("model") for e in compatible]
        assert models == ["qwen3-coder", "glm-5.1", "kimi-k2.5"]


class TestInterimAssistantMessageConfig:
    """Test the explicit gateway interim-message config gate."""

    def test_default_config_enables_interim_assistant_messages(self):
        assert DEFAULT_CONFIG["display"]["interim_assistant_messages"] is True

    def test_migrate_to_v15_adds_interim_assistant_message_gate(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"_config_version": 14, "display": {"tool_progress": "off"}}),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            migrate_config(interactive=False, quiet=True)
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        from hermes_cli.config import DEFAULT_CONFIG
        assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]
        assert raw["display"]["tool_progress"] == "off"
        assert raw["display"]["interim_assistant_messages"] is True


class TestDiscordChannelPromptsConfig:
    def test_default_config_includes_discord_channel_prompts(self):
        assert DEFAULT_CONFIG["discord"]["channel_prompts"] == {}

    def test_migrate_adds_discord_channel_prompts_default(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"_config_version": 17, "discord": {"auto_thread": True}}),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            migrate_config(interactive=False, quiet=True)
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        from hermes_cli.config import DEFAULT_CONFIG
        assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]
        assert raw["discord"]["auto_thread"] is True
        assert raw["discord"]["channel_prompts"] == {}


class TestUserMessagePreviewConfig:
    def test_default_config_preview_line_counts(self):
        preview = DEFAULT_CONFIG["display"]["user_message_preview"]
        assert preview["first_lines"] == 2
        assert preview["last_lines"] == 2


class TestEnvWriteDenylist:
    """``save_env_value`` refuses to persist env-var names that
    influence how subprocesses execute — ``LD_PRELOAD``, ``PYTHONPATH``,
    ``PATH``, ``EDITOR``, etc. — or any ``HERMES_*`` runtime flag.

    The dashboard exposes ``PUT /api/env`` to any authed caller (and
    the session token lives in the SPA's HTML where any future plugin
    XSS or local process could exfiltrate it). Without this gate, an
    attacker who steals the token could plant
    ``LD_PRELOAD=/tmp/evil.so`` in ``.env`` and own the next Hermes
    process on next startup via the dotenv → ``os.environ`` chain in
    ``hermes_cli/env_loader.py``.

    Regression test for the dashboard pentest finding filed alongside
    the ``web-pentest`` skill (PR #32265 / issue #32267).
    """

    @pytest.fixture(autouse=True)
    def _hermes_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        ensure_hermes_home()

    @pytest.mark.parametrize(
        "denied_key",
        [
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "LD_AUDIT",
            "DYLD_INSERT_LIBRARIES",
            "DYLD_LIBRARY_PATH",
            "PYTHONPATH",
            "PYTHONHOME",
            "PYTHONSTARTUP",
            "NODE_OPTIONS",
            "NODE_PATH",
            "PATH",
            "SHELL",
            "EDITOR",
            "VISUAL",
            "PAGER",
            "BROWSER",
            "GIT_SSH_COMMAND",
            "GIT_EXEC_PATH",
            "HERMES_HOME",
            "HERMES_PROFILE",
            "HERMES_CONFIG",
            "HERMES_ENV",
        ],
    )
    def test_denylisted_keys_rejected(self, denied_key):
        """Each denylisted name raises ``ValueError`` and never reaches
        the on-disk ``.env`` file."""
        with pytest.raises(ValueError, match="denylist"):
            save_env_value(denied_key, "anything")

        # And nothing landed on disk either.
        env = load_env()
        assert denied_key not in env

    @pytest.mark.parametrize(
        "allowed_key",
        [
            "HERMES_GEMINI_CLIENT_ID",
            "HERMES_LANGFUSE_PUBLIC_KEY",
            "HERMES_SPOTIFY_CLIENT_ID",
            "HERMES_QWEN_BASE_URL",
            "HERMES_MAX_ITERATIONS",
        ],
    )
    def test_hermes_integration_keys_still_writable(self, allowed_key):
        """``HERMES_*`` overall is NOT blocked — only the four runtime
        location names (HOME/PROFILE/CONFIG/ENV) are. Integration
        credentials following the ``HERMES_*`` convention must keep
        working or we'd regress every provider setup wizard that
        currently writes one of these (auth.py, Spotify, Langfuse, …)."""
        save_env_value(allowed_key, "test-value-123")
        env = load_env()
        assert env[allowed_key] == "test-value-123"

    def test_legitimate_provider_key_still_works(self):
        """The denylist must not regress on real provider key writes."""
        save_env_value("OPENROUTER_API_KEY", "sk-or-test-1234")
        env = load_env()
        assert env["OPENROUTER_API_KEY"] == "sk-or-test-1234"

    def test_arbitrary_user_key_still_works(self):
        """Plugin / user-defined env vars (anything outside the
        denylist and outside ``HERMES_*``) keep working. The denylist
        is narrow on purpose."""
        save_env_value("MY_PLUGIN_TOKEN", "plugin-secret-123")
        env = load_env()
        assert env["MY_PLUGIN_TOKEN"] == "plugin-secret-123"

    def test_save_env_value_secure_inherits_denylist(self):
        """The ``_secure`` variant goes through ``save_env_value`` so
        it inherits the gate — verify, don't assume."""
        with pytest.raises(ValueError, match="denylist"):
            save_env_value_secure("LD_PRELOAD", "/tmp/evil.so")

    def test_pre_existing_value_in_env_file_is_left_alone(self, tmp_path):
        """The gate is on *write*. If ``.env`` already contains
        ``LD_PRELOAD`` (set out-of-band by the operator before this
        change shipped, or hand-edited), we don't blow up — we just
        refuse to add or update it via the API."""
        env_path = tmp_path / ".env"
        env_path.write_text("LD_PRELOAD=/something/legit.so\n")

        # load_env returns it (the read path is intentionally permissive)
        env = load_env()
        assert env["LD_PRELOAD"] == "/something/legit.so"

        # But the write path still refuses to update it
        with pytest.raises(ValueError, match="denylist"):
            save_env_value("LD_PRELOAD", "/tmp/evil.so")
