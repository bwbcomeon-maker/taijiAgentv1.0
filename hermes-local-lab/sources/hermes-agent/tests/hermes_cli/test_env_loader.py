import importlib
import os
import sys
from pathlib import Path

import pytest

import hermes_cli.env_loader as env_loader
from hermes_cli.env_loader import load_hermes_dotenv


def test_user_env_overrides_stale_shell_values(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text("OPENAI_BASE_URL=https://new.example/v1\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")

    loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [env_file]
    assert os.getenv("OPENAI_BASE_URL") == "https://new.example/v1"


def test_user_env_cannot_override_group_shared_transaction_policy(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "hermes"
    home.mkdir()
    if hasattr(os, "chown"):
        os.chown(home, -1, os.getegid())
    home.chmod(0o2770)
    env_file = home / ".env"
    env_file.write_text(
        "HERMES_CREDENTIAL_GROUP_SHARED=0\n",
        encoding="utf-8",
    )
    env_file.chmod(0o640)
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")

    loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [env_file]
    assert os.environ["HERMES_CREDENTIAL_GROUP_SHARED"] == "1"


def test_user_env_repair_never_projects_group_shared_transaction_policy(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "hermes"
    home.mkdir()
    if hasattr(os, "chown"):
        os.chown(home, -1, os.getegid())
    home.chmod(0o2770)
    env_file = home / ".env"
    env_file.write_bytes(b"HERMES_CREDENTIAL_GROUP_SHARED=0\x00\n")
    env_file.chmod(0o640)
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")
    observed_values: list[str | None] = []
    original_load = env_loader._load_dotenv_with_fallback

    def observe_policy_before_dotenv(*args, **kwargs):
        observed_values.append(
            os.environ.get("HERMES_CREDENTIAL_GROUP_SHARED")
        )
        return original_load(*args, **kwargs)

    monkeypatch.setattr(
        env_loader,
        "_load_dotenv_with_fallback",
        observe_policy_before_dotenv,
    )

    loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [env_file]
    assert observed_values == ["1"]
    assert os.environ["HERMES_CREDENTIAL_GROUP_SHARED"] == "1"


def test_external_secret_failure_restores_group_shared_transaction_policy(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_CREDENTIAL_GROUP_SHARED", "1")

    def fail_after_overriding_policy(_home_path):
        os.environ["HERMES_CREDENTIAL_GROUP_SHARED"] = "0"
        raise RuntimeError("simulated external secret failure")

    monkeypatch.setattr(
        env_loader,
        "_apply_external_secret_sources",
        fail_after_overriding_policy,
    )

    with pytest.raises(RuntimeError, match="external secret failure"):
        load_hermes_dotenv(hermes_home=home)

    assert os.environ["HERMES_CREDENTIAL_GROUP_SHARED"] == "1"


def test_taiji_runtime_home_is_default_user_env(tmp_path, monkeypatch):
    taiji_home = tmp_path / "taiji-runtime"
    taiji_home.mkdir()
    taiji_env = taiji_home / ".env"
    taiji_env.write_text("DEEPSEEK_API_KEY=taiji-key\n", encoding="utf-8")

    legacy_home = tmp_path / "legacy"
    legacy_home.mkdir()
    (legacy_home / ".env").write_text("DEEPSEEK_API_KEY=legacy-key\n", encoding="utf-8")

    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(taiji_home))
    monkeypatch.setenv("HERMES_HOME", str(legacy_home))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    loaded = load_hermes_dotenv()

    assert loaded == [taiji_env]
    assert os.getenv("DEEPSEEK_API_KEY") == "taiji-key"


def test_project_env_overrides_stale_shell_values_when_user_env_missing(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    project_env = tmp_path / ".env"
    project_env.write_text("OPENAI_BASE_URL=https://project.example/v1\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [project_env]
    assert os.getenv("OPENAI_BASE_URL") == "https://project.example/v1"


def test_project_env_is_sanitized_before_loading(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    project_env = tmp_path / ".env"
    project_env.write_text(
        "TELEGRAM_BOT_TOKEN=0123456789:test"
        "ANTHROPIC_API_KEY=sk-ant-test123\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [project_env]
    assert os.getenv("TELEGRAM_BOT_TOKEN") == "0123456789:test"
    assert os.getenv("ANTHROPIC_API_KEY") == "sk-ant-test123"


def test_user_env_takes_precedence_over_project_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    user_env = home / ".env"
    project_env = tmp_path / ".env"
    user_env.write_text("OPENAI_BASE_URL=https://user.example/v1\n", encoding="utf-8")
    project_env.write_text("OPENAI_BASE_URL=https://project.example/v1\nOPENAI_API_KEY=project-key\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [user_env, project_env]
    assert os.getenv("OPENAI_BASE_URL") == "https://user.example/v1"
    assert os.getenv("OPENAI_API_KEY") == "project-key"


def test_null_bytes_in_user_env_are_stripped(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    # Null bytes can be introduced when copy-pasting API keys.
    env_file.write_text("GLM_API_KEY=abc\x00\x00\nOPENAI_API_KEY=sk-123\n", encoding="utf-8")

    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [env_file]
    assert os.getenv("GLM_API_KEY") == "abc"
    assert os.getenv("OPENAI_API_KEY") == "sk-123"


def test_main_import_applies_user_env_over_shell_values(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text(
        "OPENAI_BASE_URL=https://new.example/v1\nHERMES_INFERENCE_PROVIDER=custom\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("HERMES_INFERENCE_PROVIDER", "openrouter")

    sys.modules.pop("hermes_cli.main", None)
    importlib.import_module("hermes_cli.main")

    assert os.getenv("OPENAI_BASE_URL") == "https://new.example/v1"
    assert os.getenv("HERMES_INFERENCE_PROVIDER") == "custom"
