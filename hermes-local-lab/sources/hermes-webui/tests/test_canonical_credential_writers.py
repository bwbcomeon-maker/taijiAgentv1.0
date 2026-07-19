from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest
import yaml

from webui_test_spawn_helpers import (
    crash_main_model_pair_after_first_replace,
    exact_pair_process_writer,
    oauth_anthropic_clear_process,
)


def test_save_yaml_config_preserves_symlink(tmp_path: Path) -> None:
    from api.config import _save_yaml_config_file

    target = tmp_path / "real-config.yaml"
    target.write_text("model:\n  provider: old\n", encoding="utf-8")
    logical = tmp_path / "config.yaml"
    logical.symlink_to(target.name)

    _save_yaml_config_file(logical, {"model": {"provider": "new"}})

    assert logical.is_symlink()
    assert "provider: new" in target.read_text(encoding="utf-8")


def test_save_yaml_config_fails_closed_on_malformed_existing_file(
    tmp_path: Path,
) -> None:
    from api.config import _save_yaml_config_file

    config_path = tmp_path / "config.yaml"
    original = b"model: [\n"
    config_path.write_bytes(original)

    with pytest.raises(ValueError):
        _save_yaml_config_file(config_path, {"model": {"provider": "new"}})

    assert config_path.read_bytes() == original


def test_write_env_file_fails_closed_on_read_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.providers import _write_env_file

    env_path = tmp_path / ".env"
    original = b"KEY=\xff\n"
    env_path.write_bytes(original)
    monkeypatch.setenv("KEY", "process-before")
    config_path = tmp_path / "config.yaml"

    with pytest.raises(ValueError):
        _write_env_file(
            env_path,
            {"KEY": "new"},
            config_path=config_path,
        )

    assert env_path.read_bytes() == original
    assert os.environ["KEY"] == "process-before"


def test_write_env_file_syncs_process_only_after_durable_disk_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.provider_credentials as credential_store
    from api.providers import _write_env_file

    env_path = tmp_path / ".env"
    env_path.write_text("KEY=disk-before\n", encoding="utf-8")
    monkeypatch.setenv("KEY", "process-before")
    config_path = tmp_path / "config.yaml"

    def fail_write(*_args, **_kwargs):
        raise OSError("injected disk failure")

    monkeypatch.setattr(
        credential_store,
        "_atomic_write_credential_bytes",
        fail_write,
    )

    with pytest.raises(OSError, match="injected disk failure"):
        _write_env_file(
            env_path,
            {"KEY": "new"},
            config_path=config_path,
        )

    assert env_path.read_text(encoding="utf-8") == "KEY=disk-before\n"
    assert os.environ["KEY"] == "process-before"


def test_write_env_file_cas_uses_durable_disk_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.providers import _write_env_file

    env_path = tmp_path / ".env"
    env_path.write_text("KEY=old\n", encoding="utf-8")
    monkeypatch.setenv("KEY", "stale-process-value")
    config_path = tmp_path / "config.yaml"

    applied = _write_env_file(
        env_path,
        {"KEY": "new"},
        config_path=config_path,
        expected_values={"KEY": "old"},
    )

    assert applied == {"KEY": True}
    assert env_path.read_text(encoding="utf-8") == "KEY=new\n"
    assert os.environ["KEY"] == "new"


def test_write_env_file_uses_exact_custom_name_config_symlink_lock(
    tmp_path: Path,
) -> None:
    import agent.provider_credentials as credential_store
    from api.providers import _write_env_file

    alias_home = tmp_path / "alias"
    managed_home = tmp_path / "managed"
    alias_home.mkdir()
    managed_home.mkdir()
    real_config_path = managed_home / "real.yaml"
    real_config_path.write_text("{}\n", encoding="utf-8")
    config_path = alias_home / "custom-name.yaml"
    config_path.symlink_to(real_config_path)
    env_path = alias_home / ".env"
    env_path.write_text("KEEP=before\n", encoding="utf-8")

    with credential_store.credential_transaction(config_path):
        _write_env_file(
            env_path,
            {"EXACT_LOCK_KEY": "after"},
            config_path=config_path,
        )

    assert config_path.is_symlink()
    assert env_path.read_text(encoding="utf-8") == "KEEP=before\n"
    assert (managed_home / ".env").read_text(encoding="utf-8") == (
        "EXACT_LOCK_KEY=after\n"
    )
    assert not (alias_home / ".taiji-credential-transaction.lock").exists()
    assert (managed_home / ".taiji-credential-transaction.lock").exists()


def test_provider_endpoint_uses_exact_custom_name_config_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.providers as providers

    alias_home = tmp_path / "alias"
    managed_home = tmp_path / "managed"
    alias_home.mkdir()
    managed_home.mkdir()
    real_config_path = managed_home / "real.yaml"
    real_config_path.write_text("{}\n", encoding="utf-8")
    config_path = alias_home / "custom-name.yaml"
    config_path.symlink_to(real_config_path)
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("HERMES_HOME", str(alias_home))
    monkeypatch.setattr(providers, "invalidate_models_cache", lambda: None)

    result = providers.set_provider_key("deepseek", "deepseek-secret-123456")

    assert result["ok"] is True
    assert "DEEPSEEK_API_KEY=deepseek-secret-123456" in (
        managed_home / ".env"
    ).read_text(encoding="utf-8")
    assert not (alias_home / ".env").exists()
    assert not (alias_home / ".taiji-credential-transaction.lock").exists()
    assert (managed_home / ".taiji-credential-transaction.lock").exists()


def test_remove_lmstudio_key_clears_canonical_and_legacy_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.config as config
    import api.providers as providers

    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LM_API_KEY=canonical-secret\n"
        "LMSTUDIO_API_KEY=legacy-secret\n"
        "KEEP=before\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LM_API_KEY", "canonical-secret")
    monkeypatch.setenv("LMSTUDIO_API_KEY", "legacy-secret")
    monkeypatch.setattr(config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(providers, "invalidate_models_cache", lambda: None)
    monkeypatch.setattr(providers, "reload_config", lambda: None)

    result = providers.remove_provider_key("lmstudio")

    assert result["ok"] is True
    env_text = env_path.read_text(encoding="utf-8")
    assert "LM_API_KEY" not in env_text
    assert "LMSTUDIO_API_KEY" not in env_text
    assert "KEEP=before" in env_text
    assert "LM_API_KEY" not in os.environ
    assert "LMSTUDIO_API_KEY" not in os.environ


@pytest.mark.parametrize(
    ("removed_provider", "remaining_env_var"),
    [
        ("opencode-zen", "OPENCODE_GO_API_KEY"),
        ("opencode-go", "OPENCODE_ZEN_API_KEY"),
    ],
)
def test_remove_opencode_shared_alias_preserves_other_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    removed_provider: str,
    remaining_env_var: str,
) -> None:
    import api.config as config
    import api.providers as providers

    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENCODE_API_KEY=shared-secret\nKEEP=before\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_API_KEY", "shared-secret")
    monkeypatch.delenv("OPENCODE_ZEN_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.setattr(config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(providers, "invalidate_models_cache", lambda: None)
    monkeypatch.setattr(providers, "reload_config", lambda: None)

    result = providers.remove_provider_key(removed_provider)

    assert result["ok"] is True
    removed_env_var = providers._PROVIDER_ENV_VAR[removed_provider]
    env_text = env_path.read_text(encoding="utf-8")
    assert "OPENCODE_API_KEY" not in env_text
    assert removed_env_var not in env_text
    assert f"{remaining_env_var}=shared-secret" in env_text
    assert "KEEP=before" in env_text
    assert "OPENCODE_API_KEY" not in os.environ
    assert removed_env_var not in os.environ
    assert os.environ[remaining_env_var] == "shared-secret"


def test_provider_delete_preserves_config_env_pair_when_intent_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.provider_credentials as credential_store
    import api.config as config
    import api.providers as providers

    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(
        "model:\n"
        "  provider: deepseek\n"
        "  api_key: inline-model-secret\n"
        "providers:\n"
        "  deepseek:\n"
        "    api_key: inline-provider-secret\n",
        encoding="utf-8",
    )
    env_path.write_text(
        "DEEPSEEK_API_KEY=env-secret\nKEEP=before\n",
        encoding="utf-8",
    )
    original_config = config_path.read_bytes()
    original_env = env_path.read_bytes()
    monkeypatch.setattr(config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(providers, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(providers, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(providers, "invalidate_models_cache", lambda: None)
    monkeypatch.setattr(providers, "reload_config", lambda: None)

    def fail_journal(*_args, **_kwargs):
        raise OSError("journal unavailable")

    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        fail_journal,
    )

    result = providers.remove_provider_key("deepseek")

    assert result["ok"] is False
    assert "journal unavailable" in result["error"]
    assert config_path.read_bytes() == original_config
    assert env_path.read_bytes() == original_env


@pytest.mark.skipif(
    os.name != "posix",
    reason="hard-exit pair recovery requires POSIX process semantics",
)
def test_main_model_public_writer_recovers_after_first_pair_replace_crash(
    tmp_path: Path,
) -> None:
    from agent.provider_credentials import recover_credential_transaction

    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(
        "model:\n  provider: deepseek\n  default: deepseek-chat\n",
        encoding="utf-8",
    )
    env_path.write_text("KEEP=before\n", encoding="utf-8")

    # Use a fresh interpreter: forking the full WebUI suite can inherit a
    # background thread's credential/import lock and deadlock before os._exit.
    context = multiprocessing.get_context("spawn")
    writer = context.Process(
        target=crash_main_model_pair_after_first_replace,
        args=(str(config_path),),
    )
    writer.start()
    writer.join(timeout=10)

    assert writer.exitcode == 91
    assert recover_credential_transaction(config_path) == "recovered"
    config_text = config_path.read_text(encoding="utf-8")
    env_text = env_path.read_text(encoding="utf-8")
    assert "provider: custom" in config_text
    assert "default: crash-recovery-model" in config_text
    assert "base_url: https://models.example.com/v1" in config_text
    assert "HERMES_CUSTOM_MODEL_API_KEY=crash-recovery-secret" in env_text
    assert "KEEP=before" in env_text
    assert not (tmp_path / ".taiji-credential-pair-intent.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="cross-process flock is POSIX-only")
def test_oauth_env_clear_serializes_with_exact_pair_for_custom_config_symlink(
    tmp_path: Path,
) -> None:
    alias_home = tmp_path / "alias"
    managed_home = tmp_path / "managed"
    alias_home.mkdir()
    managed_home.mkdir()
    real_config_path = managed_home / "real.yaml"
    real_config_path.write_text("{}\n", encoding="utf-8")
    config_path = alias_home / "custom-name.yaml"
    config_path.symlink_to(real_config_path)
    env_path = managed_home / ".env"
    env_path.write_text(
        "ANTHROPIC_API_KEY=must-be-cleared\nKEEP=before\n",
        encoding="utf-8",
    )
    alias_env_path = alias_home / ".env"
    alias_env_path.write_text("KEEP=alias\n", encoding="utf-8")

    context = multiprocessing.get_context("spawn")
    pair_entered = context.Event()
    release_pair = context.Event()
    oauth_started = context.Event()
    oauth_completed = context.Event()
    pair_writer = context.Process(
        target=exact_pair_process_writer,
        args=(str(config_path), pair_entered, release_pair),
    )
    oauth_writer = context.Process(
        target=oauth_anthropic_clear_process,
        args=(str(config_path), oauth_started, oauth_completed),
    )

    pair_writer.start()
    assert pair_entered.wait(timeout=5)
    oauth_writer.start()
    assert oauth_started.wait(timeout=5)
    completed_while_pair_locked = oauth_completed.wait(timeout=0.5)
    release_pair.set()
    pair_writer.join(timeout=10)
    oauth_writer.join(timeout=10)

    assert pair_writer.exitcode == 0
    assert oauth_writer.exitcode == 0
    assert completed_while_pair_locked is False
    env_text = env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in env_text
    assert "PAIR_WRITER_KEY=pair-value" in env_text
    assert "KEEP=before" in env_text
    assert alias_env_path.read_text(encoding="utf-8") == "KEEP=alias\n"


def test_onboarding_captures_active_config_path_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import onboarding

    config_path = tmp_path / "config.yaml"
    calls = 0

    def one_lookup() -> Path:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("active config path was resolved more than once")
        return config_path

    monkeypatch.setattr(onboarding, "_get_config_path", one_lookup)
    monkeypatch.setattr(onboarding, "reload_config", lambda: None)
    monkeypatch.setattr(onboarding, "get_onboarding_status", lambda: {"ok": True})

    result = onboarding.apply_onboarding_setup(
        {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "api_key": "secret",
        }
    )

    assert result == {"ok": True}
    assert calls == 1
    assert config_path.exists()
    assert "OPENROUTER_API_KEY=secret" in (tmp_path / ".env").read_text()


def test_onboarding_pair_failure_leaves_config_and_env_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.provider_credentials as credential_store
    from api import onboarding

    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_before = b"model:\n  provider: old\n  default: old-model\n"
    env_before = b"OPENROUTER_API_KEY=old\n"
    config_path.write_bytes(config_before)
    env_path.write_bytes(env_before)
    monkeypatch.setattr(onboarding, "_get_config_path", lambda: config_path)

    def fail_journal(*_args, **_kwargs):
        raise OSError("injected journal failure")

    monkeypatch.setattr(credential_store, "_write_credential_journal", fail_journal)

    with pytest.raises(OSError, match="injected journal failure"):
        onboarding.apply_onboarding_setup(
            {
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4.6",
                "api_key": "new",
                "confirm_overwrite": True,
            }
        )

    assert config_path.read_bytes() == config_before
    assert env_path.read_bytes() == env_before


def test_provider_credential_endpoint_pair_failure_is_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.provider_credentials as credential_store
    from api import model_config

    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_before = b"provider_credentials: []\n"
    env_before = b"KEEP=value\n"
    config_path.write_bytes(config_before)
    env_path.write_bytes(env_before)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)

    def fail_journal(*_args, **_kwargs):
        raise OSError("injected journal failure")

    monkeypatch.setattr(credential_store, "_write_credential_journal", fail_journal)

    with pytest.raises(OSError, match="injected journal failure"):
        model_config.upsert_provider_credential(
            {
                "id": "atomic-test",
                "provider_family": "openai",
                "api_key": "new-secret",
            }
        )

    assert config_path.read_bytes() == config_before
    assert env_path.read_bytes() == env_before


def test_anthropic_link_uses_physical_config_home_for_auth_and_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.oauth as oauth

    alias_home = tmp_path / "alias"
    managed_home = tmp_path / "managed"
    alias_home.mkdir()
    managed_home.mkdir()
    config_target = managed_home / "real.yaml"
    config_target.write_text("{}\n", encoding="utf-8")
    config_path = alias_home / "config.yaml"
    config_path.symlink_to(config_target)
    (managed_home / ".env").write_text(
        "ANTHROPIC_TOKEN=old-token\n"
        "ANTHROPIC_API_KEY=old-key\n"
        "KEEP=physical\n",
        encoding="utf-8",
    )
    (alias_home / ".env").write_text("KEEP=alias\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_TOKEN", "old-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "old-key")

    oauth._link_anthropic_credentials(config_path)

    assert config_path.is_symlink()
    assert not (alias_home / "auth.json").exists()
    auth_text = (managed_home / "auth.json").read_text(encoding="utf-8")
    assert auth_text.count('"source": "claude_code_linked"') == 1
    physical_env = (managed_home / ".env").read_text(encoding="utf-8")
    assert "ANTHROPIC_TOKEN" not in physical_env
    assert "ANTHROPIC_API_KEY" not in physical_env
    assert "TAIJI_ANTHROPIC_LINK_BACKUP_" not in physical_env
    assert "KEEP=physical" in physical_env
    assert (alias_home / ".env").read_text(encoding="utf-8") == "KEEP=alias\n"


@pytest.mark.parametrize("capability", ["image", "vision"])
def test_custom_provider_legacy_secret_migrates_from_physical_env_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capability: str,
) -> None:
    import api.model_config as model_config

    alias_home = tmp_path / "alias"
    managed_home = tmp_path / "managed"
    alias_home.mkdir()
    managed_home.mkdir()
    config_target = managed_home / "real.yaml"
    config_path = alias_home / "config.yaml"
    config_path.symlink_to(config_target)
    legacy_env = (
        "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY"
        if capability == "image"
        else "TAIJI_VISION_CUSTOM_ROUTER_API_KEY"
    )
    config_key = (
        "custom_image_providers"
        if capability == "image"
        else "custom_vision_providers"
    )
    legacy_entry = {
        "id": "router",
        "name": f"Router {capability}",
        "base_url": f"https://{capability}.example.com/v1",
        "models": [f"{capability}-model"],
        "default_model": f"{capability}-model",
        "api_key_env": legacy_env,
    }
    if capability == "vision":
        legacy_entry["transport"] = "openai_chat_completions"
    config_target.write_text(
        yaml.safe_dump({config_key: [legacy_entry]}),
        encoding="utf-8",
    )
    (managed_home / ".env").write_text(
        f"{legacy_env}=physical-secret\nKEEP=physical\n",
        encoding="utf-8",
    )
    (alias_home / ".env").write_text("KEEP=alias\n", encoding="utf-8")
    monkeypatch.delenv(legacy_env, raising=False)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(
        model_config,
        "_refresh_custom_provider_commit",
        lambda _capability, *, fallback_providers: {
            "providers": list(fallback_providers),
            "refresh_pending": False,
            "warnings": [],
        },
    )
    if capability == "vision":
        monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    credential_ref = f"migration-{capability}-router"
    secret_env = model_config.credential_secret_env(credential_ref)
    body = {
        "id": "router",
        "name": f"Router {capability}",
        "base_url": f"https://{capability}.example.com/v1",
        "models": [f"{capability}-model"],
        "default_model": f"{capability}-model",
        "credential_ref": credential_ref,
    }
    if capability == "vision":
        body["transport"] = "openai_chat_completions"

    try:
        setter = getattr(
            model_config,
            f"set_custom_{capability}_provider_config",
        )
        result = setter(body)

        saved = yaml.safe_load(config_target.read_text(encoding="utf-8"))
        assert saved[config_key][0]["credential_ref"] == credential_ref
        assert "api_key_env" not in saved[config_key][0]
        physical_env = (managed_home / ".env").read_text(encoding="utf-8")
        assert f"{secret_env}=physical-secret" in physical_env
        assert legacy_env not in physical_env
        assert "KEEP=physical" in physical_env
        assert (alias_home / ".env").read_text(encoding="utf-8") == "KEEP=alias\n"
        assert result["provider"]["key_status"]["configured"] is True
        if capability == "vision":
            assert result["provider"]["available"] is True
        else:
            assert result["provider"]["configured"] is True
    finally:
        os.environ.pop(secret_env, None)
        os.environ.pop(legacy_env, None)


@pytest.mark.parametrize(
    ("provider_id", "env_values", "removed_keys"),
    [
        (
            "lmstudio",
            {
                "LM_API_KEY": "canonical-secret",
                "LMSTUDIO_API_KEY": "legacy-secret",
            },
            {"LM_API_KEY", "LMSTUDIO_API_KEY"},
        ),
        (
            "opencode-zen",
            {
                "OPENCODE_ZEN_API_KEY": "canonical-secret",
                "OPENCODE_API_KEY": "legacy-secret",
            },
            {"OPENCODE_ZEN_API_KEY", "OPENCODE_API_KEY"},
        ),
    ],
)
def test_set_provider_key_none_removes_canonical_and_legacy_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    env_values: dict[str, str],
    removed_keys: set[str],
) -> None:
    import api.config as config
    import api.providers as providers

    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "".join(f"{key}={value}\n" for key, value in env_values.items())
        + "KEEP=before\n",
        encoding="utf-8",
    )
    for key, value in env_values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(providers, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(providers, "invalidate_models_cache", lambda: None)
    monkeypatch.setattr(providers, "reload_config", lambda: None)

    result = providers.set_provider_key(provider_id, None)

    assert result["ok"] is True
    assert result["action"] == "removed"
    env_text = env_path.read_text(encoding="utf-8")
    for key in removed_keys:
        assert key not in env_text
        assert key not in os.environ
    assert "KEEP=before" in env_text
