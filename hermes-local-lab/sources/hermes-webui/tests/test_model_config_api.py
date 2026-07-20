"""Tests for WebUI model configuration parity with Hermes CLI config."""

from __future__ import annotations

import json
import base64
import hashlib
import inspect
import multiprocessing
import os
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

import api.providers as providers
import api.profiles as profiles
import api.routes as routes
import api.config as api_config
import agent.provider_credentials as credential_store
from api import model_config


def _credential_transaction_process_writer(
    config_path: str,
    key: str,
    start_event,
) -> None:
    os.environ["HERMES_HOME"] = str(Path(config_path).parent)
    os.environ["HERMES_CONFIG_PATH"] = config_path
    from agent.provider_credentials import credential_transaction

    path = Path(config_path)
    start_event.wait(timeout=5)
    with credential_transaction(path):
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            current = {}
        time.sleep(0.15)
        current[key] = True
        path.write_text(json.dumps(current, sort_keys=True), encoding="utf-8")


def _credential_transaction_process_holder(
    config_path: str,
    acquired_event,
    release_event,
) -> None:
    os.environ["HERMES_HOME"] = str(Path(config_path).parent)
    os.environ["HERMES_CONFIG_PATH"] = config_path
    from agent.provider_credentials import credential_transaction

    with credential_transaction(Path(config_path)):
        acquired_event.set()
        release_event.wait(timeout=10)


def _cli_env_process_writer(
    config_path: str,
    key: str,
    value: str,
    started_event,
    completed_event,
) -> None:
    os.environ["HERMES_HOME"] = str(Path(config_path).parent)
    os.environ["HERMES_CONFIG_PATH"] = config_path
    from hermes_cli.config import save_env_value

    started_event.set()
    save_env_value(key, value)
    completed_event.set()


def _half_commit_process_writer(
    config_path: str,
    secret_env: str,
    half_committed_event,
    release_event,
) -> None:
    home = Path(config_path).parent
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HERMES_CONFIG_PATH"] = config_path
    from agent.provider_credentials import credential_transaction

    with credential_transaction(Path(config_path)):
        (home / ".env").write_text(f"{secret_env}=new-secret\n", encoding="utf-8")
        half_committed_event.set()
        release_event.wait(timeout=10)
        Path(config_path).write_text(
            yaml.safe_dump(
                {
                    "provider_credentials": [
                        {
                            "id": "new-provider",
                            "provider_family": "custom",
                            "auth_type": "api_key",
                            "secret_env": secret_env,
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )


def _legacy_image_config_process_writer(
    config_path: str,
    model: str,
    api_key: str,
    start_event,
    completed_event,
    result_queue,
) -> None:
    """Emulate an older writer that does not know the WebUI probe lock."""
    path = Path(config_path)
    os.environ["HERMES_HOME"] = str(path.parent)
    os.environ["HERMES_CONFIG_PATH"] = str(path)
    from agent.provider_credentials import mutate_config_env_strict

    if not start_event.wait(timeout=10):
        result_queue.put(("error", "probe barrier timed out"))
        completed_event.set()
        return

    try:
        def mutate(config_data):
            image_cfg = config_data.setdefault("image_gen", {})
            image_cfg["model"] = model

        mutate_config_env_strict(
            mutate,
            {"DASHSCOPE_API_KEY": api_key},
            config_path=path,
        )
        result_queue.put(("ok", model))
    except BaseException as exc:
        result_queue.put(("error", type(exc).__name__))
    finally:
        completed_event.set()


def _credential_config_process_reader(
    config_path: str,
    completed_event,
    result_queue,
) -> None:
    os.environ["HERMES_HOME"] = str(Path(config_path).parent)
    os.environ["HERMES_CONFIG_PATH"] = config_path
    from agent.provider_credentials import load_credential_config

    result_queue.put(load_credential_config(Path(config_path)))
    completed_event.set()


def _verification_state_lock_process(
    state_path: str,
    label: str,
    attempting_event,
    acquired_event,
    release_event,
    result_queue,
) -> None:
    from api import model_config as child_model_config

    attempting_event.set()
    with child_model_config._verification_state_file_lock(
        Path(state_path)
    ):
        result_queue.put((label, "entered"))
        acquired_event.set()
        release_event.wait(timeout=10)
    result_queue.put((label, "exited"))


def _old_vision_probe_owner_process(
    state_dir: str,
    profile: str,
    old_began_event,
    newer_began_event,
    result_queue,
) -> None:
    import api.config as child_api_config
    from api import model_config as child_model_config

    child_api_config.STATE_DIR = Path(state_dir)
    fingerprint = "cross-process-vision-old"
    diagnostic_id = "cross-process-old-owner"
    generation = child_model_config._begin_vision_probe(
        profile,
        {
            "schema_version": 1,
            "fingerprint": fingerprint,
            "status": "verifying",
            "checked_at": "2030-01-01T00:00:00Z",
            "error_code": "",
            "message": "old probe",
            "diagnostic_id": diagnostic_id,
        },
    )
    result_queue.put(("old_generation", generation))
    old_began_event.set()
    if not newer_began_event.wait(timeout=10):
        result_queue.put(("old_commit", "newer_timeout"))
        return
    state_path = child_model_config._vision_verification_state_path(
        profile
    )
    with child_model_config._vision_profile_lock(profile):
        current_generation = (
            child_model_config._VISION_PROBE_GENERATIONS.get(profile, 0)
        )
        with child_model_config._verification_state_file_lock(state_path):
            committed = child_model_config._commit_owned_verification_result(
                state_path,
                generation=generation,
                current_generation=current_generation,
                fingerprint=fingerprint,
                diagnostic_id=diagnostic_id,
                state={
                    "schema_version": 1,
                    "fingerprint": fingerprint,
                    "status": "verified",
                    "checked_at": "2030-01-01T00:00:01Z",
                    "error_code": "",
                    "message": "old final result",
                    "diagnostic_id": diagnostic_id,
                },
            )
    result_queue.put(("old_commit", committed))


def _newer_vision_probe_owner_process(
    state_dir: str,
    profile: str,
    old_began_event,
    newer_began_event,
    result_queue,
) -> None:
    import api.config as child_api_config
    from api import model_config as child_model_config

    child_api_config.STATE_DIR = Path(state_dir)
    if not old_began_event.wait(timeout=10):
        result_queue.put(("new_generation", "old_timeout"))
        return
    generation = child_model_config._begin_vision_probe(
        profile,
        {
            "schema_version": 1,
            "fingerprint": "cross-process-vision-new",
            "status": "verifying",
            "checked_at": "2030-01-01T00:00:02Z",
            "error_code": "",
            "message": "new probe",
            "diagnostic_id": "cross-process-new-owner",
        },
    )
    result_queue.put(("new_generation", generation))
    newer_began_event.set()


def _use_home(monkeypatch, tmp_path, *, stub_image_gen: bool = True):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.delenv(
        "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY", raising=False
    )
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(providers, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        model_config,
        "get_providers",
        lambda: {
            "providers": [
                {
                    "id": "deepseek",
                    "display_name": "DeepSeek",
                    "models": [{"id": "deepseek-chat", "label": "deepseek-chat"}],
                    "configurable": True,
                    "has_key": False,
                }
            ],
            "active_provider": "deepseek",
        },
    )
    if stub_image_gen:
        monkeypatch.setattr(
            model_config,
            "get_image_gen_config",
            lambda: {
                "image_gen": {},
                "providers": [],
                "config": {"label": "本机配置", "exists": True},
            },
        )


def _read_config(tmp_path):
    return yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8")) or {}


def _assert_verification_tombstone(
    state,
    *,
    minimum_generation,
    forbidden_fingerprint="",
    forbidden_diagnostic_id="",
):
    assert state["schema_version"] == (
        model_config.CAPABILITY_VERIFICATION_SCHEMA_VERSION
    )
    assert type(state["generation"]) is int
    assert state["generation"] >= minimum_generation
    assert state["status"] == "configured_unverified"
    assert state["fingerprint"] == ""
    assert state["diagnostic_id"] == ""
    assert state["checked_at"] == ""
    assert state["error_code"] == ""
    assert state["message"] == ""
    if forbidden_fingerprint:
        assert forbidden_fingerprint not in json.dumps(
            state,
            ensure_ascii=False,
        )
    if forbidden_diagnostic_id:
        assert forbidden_diagnostic_id not in json.dumps(
            state,
            ensure_ascii=False,
        )


@pytest.mark.parametrize(
    ("mutator_name", "args"),
    (
        ("set_reasoning_display", (False,)),
        ("set_reasoning_effort", ("high",)),
        ("set_hermes_default_model", ("openai/gpt-5.4-mini",)),
        ("set_auxiliary_model", ("vision", "openai", "gpt-5.4-mini",)),
    ),
)
def test_config_mutators_fail_closed_without_overwriting_malformed_yaml(
    monkeypatch,
    tmp_path,
    mutator_name,
    args,
):
    config_path = tmp_path / "config.yaml"
    malformed_payload = b"model:\n  provider: [unterminated\n"
    config_path.write_bytes(malformed_payload)
    monkeypatch.setattr(api_config, "_get_config_path", lambda: config_path)

    with pytest.raises(ValueError, match="cannot be read safely"):
        getattr(api_config, mutator_name)(*args)

    assert config_path.read_bytes() == malformed_payload


def test_active_profile_name_uses_real_profile_api(monkeypatch):
    monkeypatch.setattr(profiles, "taiji_single_runtime_mode", lambda: False)
    profiles.set_request_profile("named-profile")
    try:
        assert model_config._active_profile_name() == "named-profile"
    finally:
        profiles.clear_request_profile()


def test_provider_credential_secret_stays_out_of_yaml_and_public_response(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.delenv("TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", raising=False)
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=legacy-key\n", encoding="utf-8")

    result = model_config.upsert_provider_credential(
        {
            "id": "Alibaba Default",
            "provider": "alibaba",
            "label": "阿里云百炼默认凭据",
            "api_key": "named-secret-key",
        }
    )

    row = _read_config(tmp_path)["provider_credentials"][0]
    assert row == {
        "id": "alibaba-default",
        "provider_family": "alibaba_dashscope",
        "label": "阿里云百炼默认凭据",
        "auth_type": "api_key",
        "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
    }
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=legacy-key" in env_text
    assert "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=named-secret-key" in env_text
    assert set(result["credential"]) == {
        "id", "provider_family", "label", "auth_type", "default", "configured", "used_by"
    }
    public_dump = json.dumps(result, ensure_ascii=False)
    assert "named-secret-key" not in public_dump
    assert "secret_env" not in public_dump
    assert "digest" not in public_dump


def test_provider_credentials_report_vision_and_image_gen_usage(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": "alibaba-default",
                        "provider_family": "alibaba_dashscope",
                        "label": "Alibaba default",
                        "auth_type": "api_key",
                        "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
                        "default": True,
                    }
                ],
                "auxiliary": {"vision": {"credential_ref": "alibaba-default"}},
                "image_gen": {"credential_ref": "alibaba-default"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=named-key\n", encoding="utf-8"
    )

    result = model_config.get_provider_credentials_config()

    assert result["credentials"] == [
        {
            "id": "alibaba-default",
            "provider_family": "alibaba_dashscope",
            "label": "Alibaba default",
                "auth_type": "api_key",
                "default": True,
                "configured": True,
            "used_by": ["auxiliary.vision", "image_gen"],
        }
    ]


def test_model_config_includes_only_safe_provider_credential_fields(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    secret = "must-never-reach-model-config"
    monkeypatch.setenv("TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", secret)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": "alibaba-default",
                        "provider_family": "alibaba_dashscope",
                        "label": "阿里云百炼默认凭据",
                        "auth_type": "api_key",
                        "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
                    }
                ],
                "auxiliary": {"vision": {"credential_ref": "alibaba-default"}},
                "image_gen": {"credential_ref": "alibaba-default"},
            }
        ),
        encoding="utf-8",
    )

    result = model_config.get_model_config()

    assert result["provider_credentials"] == [
        {
            "id": "alibaba-default",
            "provider_family": "alibaba_dashscope",
            "label": "阿里云百炼默认凭据",
                "auth_type": "api_key",
                "default": False,
                "configured": True,
            "used_by": ["auxiliary.vision", "image_gen"],
        }
    ]
    dumped = json.dumps(result, ensure_ascii=False)
    assert secret not in dumped
    assert "secret_env" not in dumped


def test_image_and_vision_provider_rows_expose_normalized_auth_transport_contract(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text(
        "image_gen:\n  provider: dashscope\n", encoding="utf-8"
    )

    result = model_config.get_model_config()

    rows = list(result["vision_providers"]) + list(result["image_gen_providers"])
    assert rows
    required = {
        "provider_family",
        "capabilities",
        "auth_type",
        "transport",
        "credential_fields",
        "endpoint_fields",
        "models",
        "auth_editable",
        "auth_message",
    }
    for row in rows:
        assert required <= set(row), row.get("id")
        assert row["auth_type"] in {
            "api_key",
            "bearer_token",
            "access_key_secret",
            "service_account",
            "oauth",
            "no_auth",
        }
    alibaba = next(row for row in result["vision_providers"] if row["id"] == "alibaba")
    dashscope = next(
        row for row in model_config._image_gen_provider_rows("dashscope")
        if row["id"] == "dashscope"
    )
    assert alibaba["transport"] == "dashscope_openai_compatible"
    assert alibaba["capabilities"] == ["vision"]
    assert dashscope["transport"] == "dashscope_native_image_generation"
    assert dashscope["capabilities"] == ["image_generation"]
    assert [field["name"] for field in dashscope["endpoint_fields"]] == [
        "endpoint_mode",
        "workspace_id",
        "region",
        "base_url",
    ]


def test_get_model_config_does_not_lazy_write_provider_ref(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    original = {
        "provider_credentials": [
            {
                "id": "alibaba-default",
                "provider_family": "alibaba_dashscope",
                "label": "Alibaba",
                "auth_type": "api_key",
                "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
            }
        ],
        "auxiliary": {"vision": {"provider": "alibaba", "model": "qwen3-vl-plus"}},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(original), encoding="utf-8")

    model_config.get_model_config()

    assert _read_config(tmp_path) == original


def test_first_vision_save_lazily_binds_family_default_and_is_idempotent(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": "alibaba-default",
                        "provider_family": "alibaba_dashscope",
                        "label": "Alibaba",
                        "auth_type": "api_key",
                        "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
                        "default": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=named-secret\n",
        encoding="utf-8",
    )
    body = {
        "provider": "alibaba",
        "model": "qwen3-vl-plus",
        "endpoint_mode": "public",
        "region": "cn-beijing",
    }

    model_config.set_vision_config(body)
    first = _read_config(tmp_path)
    model_config.set_vision_config(body)
    second = _read_config(tmp_path)

    assert first["auxiliary"]["vision"]["credential_ref"] == "alibaba-default"
    assert (
        second["_taiji_capability_epochs"]["vision"]
        > first["_taiji_capability_epochs"]["vision"]
    )
    first_semantic = dict(first)
    second_semantic = dict(second)
    first_semantic.pop("_taiji_capability_epochs")
    second_semantic.pop("_taiji_capability_epochs")
    assert second_semantic == first_semantic


def test_vision_save_without_explicit_default_keeps_legacy_fallback(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": "image-only",
                        "provider_family": "alibaba_dashscope",
                        "auth_type": "api_key",
                        "secret_env": "TAIJI_CREDENTIAL_IMAGE_ONLY_API_KEY",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "endpoint_mode": "public",
            "region": "cn-beijing",
        }
    )

    assert _read_config(tmp_path)["auxiliary"]["vision"]["credential_ref"] == ""


def test_unconfigured_default_does_not_bind_and_legacy_remains_available(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-secret")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"provider_credentials": [{"id": "empty-default", "provider_family": "alibaba_dashscope", "auth_type": "api_key", "secret_env": "TAIJI_CREDENTIAL_EMPTY_DEFAULT_API_KEY", "default": True}]}),
        encoding="utf-8",
    )

    model_config.set_vision_config(
        {"provider": "alibaba", "model": "qwen3-vl-plus", "endpoint_mode": "public"}
    )

    saved = _read_config(tmp_path)["auxiliary"]["vision"]
    assert saved["credential_ref"] == ""
    assert model_config._vision_key_status("alibaba", saved)["configured"] is True


def test_inline_vision_key_keeps_legacy_mode_instead_of_auto_binding_default(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setenv("TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", "named-secret")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"provider_credentials": [{"id": "alibaba-default", "provider_family": "alibaba_dashscope", "auth_type": "api_key", "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", "default": True}]}),
        encoding="utf-8",
    )

    model_config.set_vision_config(
        {"provider": "alibaba", "model": "qwen3-vl-plus", "endpoint_mode": "public", "api_key": "legacy-new"}
    )

    assert _read_config(tmp_path)["auxiliary"]["vision"]["credential_ref"] == ""
    assert "DASHSCOPE_API_KEY=legacy-new" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_inline_image_key_keeps_legacy_mode_instead_of_auto_binding_default(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setenv("TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", "named-secret")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"provider_credentials": [{"id": "alibaba-default", "provider_family": "alibaba_dashscope", "auth_type": "api_key", "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", "default": True}]}),
        encoding="utf-8",
    )

    model_config.set_image_gen_config(
        {"provider": "dashscope", "model": "qwen-image-2.0-pro", "api_key": "legacy-image", "credentials": {"workspace_id": "llm-demo"}}
    )

    assert _read_config(tmp_path)["image_gen"]["credential_ref"] == ""
    assert "DASHSCOPE_API_KEY=legacy-image" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_vision_yaml_failure_restores_previous_legacy_secret(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=old-secret\n", encoding="utf-8")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "old-process-secret")
    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        lambda *_args: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        model_config.set_vision_config(
            {"provider": "alibaba", "model": "qwen3-vl-plus", "endpoint_mode": "public", "api_key": "new-secret"}
        )

    assert (tmp_path / ".env").read_text(encoding="utf-8") == "DASHSCOPE_API_KEY=old-secret\n"
    assert os.environ["DASHSCOPE_API_KEY"] == "old-process-secret"


def test_vision_schema_endpoint_field_is_saved_from_legacy_payload(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    meta = dict(model_config._VISION_PROVIDER_META["alibaba"])
    meta["endpoint_fields"] = list(meta.get("endpoint_fields") or []) + [
        {"name": "tenant", "label": "Tenant", "required": True, "secret": False}
    ]
    monkeypatch.setitem(model_config._VISION_PROVIDER_META, "alibaba", meta)

    model_config.set_vision_config(
        {"provider": "alibaba", "model": "qwen3-vl-plus", "endpoint_mode": "public", "tenant": "tenant-a"}
    )

    assert _read_config(tmp_path)["auxiliary"]["vision"]["tenant"] == "tenant-a"
    result = model_config.get_vision_config()
    assert result["vision"]["endpoint_values"]["tenant"] == "tenant-a"


def test_vision_endpoint_values_never_echo_secret_schema_fields(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    meta = dict(model_config._VISION_PROVIDER_META["alibaba"])
    meta["endpoint_fields"] = list(meta.get("endpoint_fields") or []) + [
        {"name": "unsafe_token", "label": "Unsafe", "secret": True}
    ]
    monkeypatch.setitem(model_config._VISION_PROVIDER_META, "alibaba", meta)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"auxiliary": {"vision": {"provider": "alibaba", "model": "qwen3-vl-plus", "unsafe_token": "must-not-echo"}}}),
        encoding="utf-8",
    )

    result = model_config.get_vision_config()

    assert "unsafe_token" not in result["vision"]["endpoint_values"]
    assert "must-not-echo" not in json.dumps(result, ensure_ascii=False)


def test_alibaba_endpoint_options_have_localized_labels(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text("image_gen:\n  provider: dashscope\n", encoding="utf-8")

    result = model_config.get_model_config()
    vision = next(row for row in result["vision_providers"] if row["id"] == "alibaba")
    image = next(row for row in model_config._image_gen_provider_rows("dashscope") if row["id"] == "dashscope")

    vision_fields = {field["name"]: field for field in vision["endpoint_fields"]}
    image_fields = {field["name"]: field for field in image["endpoint_fields"]}
    assert vision_fields["endpoint_mode"]["options"] == [
        {"value": "public", "label": "公共端点"},
        {"value": "workspace", "label": "业务空间专属端点"},
        {"value": "custom", "label": "自定义 Base URL"},
    ]
    assert vision_fields["region"]["options"][0] == {"value": "cn-beijing", "label": "华北 2（北京）"}
    assert image_fields["region"]["options"][1] == {"value": "ap-southeast-1", "label": "新加坡"}


def test_image_schema_endpoint_fields_are_saved_as_options(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda _provider: [{
            "id": "dashscope", "custom": False, "domestic": True, "integration_status": "stable",
            "default_model": "qwen-image-2.0-pro", "models": [{"id": "qwen-image-2.0-pro"}],
            "credential_fields": [{"name": "api_key", "env_var": "DASHSCOPE_API_KEY", "secret": True}],
            "endpoint_fields": [
                {"name": "tenant", "required": True, "secret": False},
                {"name": "route", "required": False, "secret": False},
            ],
        }],
    )

    model_config.set_image_gen_config(
        {"provider": "dashscope", "model": "qwen-image-2.0-pro", "api_key": "legacy-key", "credentials": {"tenant": "tenant-a", "route": "blue"}}
    )

    assert _read_config(tmp_path)["image_gen"]["options"] == {"tenant": "tenant-a", "route": "blue"}


def test_image_endpoint_values_round_trip_unknown_schema_fields(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    provider = {
        "id": "dashscope", "custom": False, "domestic": True, "integration_status": "stable",
        "default_model": "qwen-image-2.0-pro", "models": [{"id": "qwen-image-2.0-pro"}], "credential_fields": [],
        "endpoint_fields": [
            {"name": "tenant", "required": True, "secret": False},
            {"name": "route", "required": False, "secret": False},
            {"name": "unsafe_token", "required": False, "secret": True},
        ],
    }
    monkeypatch.setattr(model_config, "_image_gen_provider_rows", lambda _provider: [provider])
    model_config.set_image_gen_config({"provider": "dashscope", "model": "qwen-image-2.0-pro", "credentials": {"tenant": "tenant-a", "route": "green", "unsafe_token": "must-not-echo"}})

    result = model_config.get_image_gen_config()

    assert result["image_gen"]["endpoint_values"] == {"tenant": "tenant-a", "route": "green"}
    assert "must-not-echo" not in json.dumps(result, ensure_ascii=False)


def test_endpoint_ownership_cleans_stale_fields_without_deleting_unowned_options(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    base = {"id": "dashscope", "custom": False, "domestic": True, "integration_status": "stable", "default_model": "qwen-image-2.0-pro", "models": [{"id": "qwen-image-2.0-pro"}], "credential_fields": []}
    active = {"row": dict(base, endpoint_fields=[{"name": "tenant", "required": False, "secret": False}, {"name": "route", "required": False, "secret": False}])}
    monkeypatch.setattr(model_config, "_image_gen_provider_rows", lambda _provider: [active["row"]])
    model_config.set_image_gen_config({"provider": "dashscope", "model": "qwen-image-2.0-pro", "credentials": {"tenant": "a", "route": "blue"}})
    saved = _read_config(tmp_path)
    saved["image_gen"]["options"]["temperature"] = "0.3"
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(saved), encoding="utf-8")
    active["row"] = dict(base, endpoint_fields=[{"name": "region", "required": False, "secret": False}])

    model_config.set_image_gen_config({"provider": "dashscope", "model": "qwen-image-2.0-pro", "credentials": {"region": "cn-beijing"}})
    image = _read_config(tmp_path)["image_gen"]

    assert image["options"] == {"temperature": "0.3", "region": "cn-beijing"}
    assert image["endpoint_field_names"] == ["region"]


def test_vision_endpoint_ownership_preserves_unowned_fields(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    meta = dict(model_config._VISION_PROVIDER_META["alibaba"])
    meta["endpoint_fields"] = [{"name": "tenant", "required": False, "secret": False}]
    monkeypatch.setitem(model_config._VISION_PROVIDER_META, "alibaba", meta)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"auxiliary": {"vision": {"provider": "alibaba", "model": "qwen3-vl-plus", "tenant": "old", "route": "stale", "endpoint_field_names": ["tenant", "route"], "temperature": "0.2"}}}), encoding="utf-8")

    model_config.set_vision_config({"provider": "alibaba", "model": "qwen3-vl-plus", "tenant": "new"})
    vision = _read_config(tmp_path)["auxiliary"]["vision"]

    assert vision["tenant"] == "new"
    assert "route" not in vision
    assert vision["temperature"] == "0.2"
    assert vision["endpoint_field_names"] == ["tenant"]


def test_endpoint_cleanup_rolls_back_when_yaml_save_fails(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    original = {"image_gen": {"provider": "dashscope", "model": "qwen-image-2.0-pro", "endpoint_field_names": ["tenant"], "options": {"tenant": "old", "temperature": "0.4"}}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(original), encoding="utf-8")
    provider = {"id": "dashscope", "custom": False, "domestic": True, "integration_status": "stable", "default_model": "qwen-image-2.0-pro", "models": [{"id": "qwen-image-2.0-pro"}], "credential_fields": [], "endpoint_fields": [{"name": "region", "required": False, "secret": False}]}
    monkeypatch.setattr(model_config, "_image_gen_provider_rows", lambda _provider: [provider])
    monkeypatch.setattr(
        credential_store,
        "_atomic_write_credential_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("disk full")
        ),
    )

    with pytest.raises(OSError, match="disk full"):
        model_config.set_image_gen_config({"provider": "dashscope", "model": "qwen-image-2.0-pro", "credentials": {"region": "cn-beijing"}})

    assert _read_config(tmp_path) == original


def test_concurrent_default_unset_cannot_be_followed_by_stale_lazy_binding(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential({"id": "alibaba-default", "provider": "alibaba", "api_key": "named-secret", "default": True})
    before_unset_save = threading.Event()
    release_unset = threading.Event()
    original_write = credential_store._atomic_write_credential_bytes

    def pause_before_unset_write(path, payload, **kwargs):
        parsed = yaml.safe_load(payload.decode("utf-8"))
        rows = parsed.get("provider_credentials") if isinstance(parsed, dict) else None
        row = rows[0] if isinstance(rows, list) and rows else {}
        if Path(path).name == "config.yaml" and row.get("id") == "alibaba-default" and not row.get("default"):
            before_unset_save.set()
            assert release_unset.wait(timeout=3)
        return original_write(path, payload, **kwargs)

    monkeypatch.setattr(
        credential_store,
        "_atomic_write_credential_bytes",
        pause_before_unset_write,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        unset = pool.submit(model_config.upsert_provider_credential, {"id": "alibaba-default", "provider": "alibaba", "default": False})
        assert before_unset_save.wait(timeout=2)
        bind = pool.submit(model_config.set_vision_config, {"provider": "alibaba", "model": "qwen3-vl-plus"})
        time.sleep(0.1)
        release_unset.set()
        unset.result(timeout=3)
        bind.result(timeout=3)

    saved = _read_config(tmp_path)
    assert saved["provider_credentials"][0].get("default") is not True
    assert saved["auxiliary"]["vision"].get("credential_ref", "") == ""


def test_lazy_default_binding_rolls_back_when_config_save_fails(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    original = {
        "provider_credentials": [
            {
                "id": "alibaba-default",
                "provider_family": "alibaba_dashscope",
                "auth_type": "api_key",
                "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
                "default": True,
            }
        ]
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(original), encoding="utf-8")
    monkeypatch.setattr(
        credential_store,
        "_atomic_write_credential_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        model_config.set_vision_config(
            {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "endpoint_mode": "public",
                "region": "cn-beijing",
            }
        )

    assert _read_config(tmp_path) == original


def test_provider_credential_routes_map_validation_errors_to_400(monkeypatch):
    responses = []
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {"id": "bad"})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: responses.append((message, status)) or True,
    )
    monkeypatch.setattr(
        model_config,
        "upsert_provider_credential",
        lambda _body: (_ for _ in ()).throw(ValueError("凭据无效")),
    )
    monkeypatch.setattr(
        model_config,
        "delete_provider_credential",
        lambda _credential_id: (_ for _ in ()).throw(ValueError("凭据正在使用")),
    )

    assert routes.handle_post(object(), SimpleNamespace(path="/api/provider-credentials")) is True
    assert routes.handle_delete(
        object(), SimpleNamespace(path="/api/provider-credentials/alibaba-default")
    ) is True
    assert responses == [("凭据无效", 400), ("凭据正在使用", 400)]


@pytest.mark.parametrize(
    ("path", "mutation_name"),
    [
        (
            "/api/provider-credentials/alibaba-default",
            "delete_provider_credential",
        ),
        (
            "/api/vision/custom-providers/relay",
            "delete_custom_vision_provider_config",
        ),
        (
            "/api/image-gen/custom-providers/relay",
            "delete_custom_image_provider_config",
        ),
    ],
)
def test_managed_provider_delete_routes_return_stable_conflict(
    monkeypatch,
    path,
    mutation_name,
):
    from hermes_cli.config import ManagedConfigurationError

    responses = []
    rejection = ManagedConfigurationError("delete provider configuration")
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {})
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: responses.append(
            (payload, status)
        )
        or True,
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda *_args, **_kwargs: pytest.fail(
            "managed configuration rejection must not become an opaque error"
        ),
    )
    monkeypatch.setattr(
        model_config,
        mutation_name,
        lambda _value: (_ for _ in ()).throw(rejection),
    )

    assert routes.handle_delete(object(), SimpleNamespace(path=path)) is True
    assert responses == [
        (
            {
                "error": str(rejection),
                "error_code": "managed_configuration",
            },
            409,
        )
    ]


def test_configuration_conflict_response_exposes_path_not_values(monkeypatch):
    from hermes_cli.config import ConfigurationConflictError

    responses = []
    conflict = ConfigurationConflictError(
        ("custom_providers", 0, "api_key")
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: responses.append(
            (payload, status)
        )
        or True,
    )

    assert (
        routes._configuration_mutation_error_response(object(), conflict)
        is True
    )
    payload, status = responses[0]
    assert status == 409
    assert payload["error_code"] == "configuration_conflict"
    assert "custom_providers.0.api_key" in payload["error"]
    assert "caller-secret-value" not in payload["error"]
    assert "concurrent-secret-value" not in payload["error"]


def test_alibaba_image_capabilities_route_saves_single_key_payload(monkeypatch):
    responses = []
    body = {
        "api_key": "route-test-key",
        "vision_model": "qwen3-vl-plus",
        "image_model": "qwen-image-2.0-pro",
    }
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: body)
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, *args, **kwargs: responses.append(payload) or True,
    )
    monkeypatch.setattr(
        model_config,
        "set_alibaba_image_capabilities",
        lambda payload: {"ok": True, "received": payload},
        raising=False,
    )

    assert routes.handle_post(
        object(), SimpleNamespace(path="/api/image-capabilities/alibaba")
    ) is True
    assert responses == [{"ok": True, "received": body}]


def test_custom_vision_provider_routes_map_validation_errors_to_400(monkeypatch):
    responses = []
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {"id": "relay"})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: responses.append((message, status)) or True,
    )
    monkeypatch.setattr(
        model_config,
        "set_custom_vision_provider_config",
        lambda _body: (_ for _ in ()).throw(ValueError("transport 无效")),
    )
    monkeypatch.setattr(
        model_config,
        "delete_custom_vision_provider_config",
        lambda _provider_id: (_ for _ in ()).throw(ValueError("Provider 正在使用")),
    )

    assert routes.handle_post(
        object(), SimpleNamespace(path="/api/vision/custom-providers")
    ) is True
    assert routes.handle_delete(
        object(), SimpleNamespace(path="/api/vision/custom-providers/relay")
    ) is True
    assert responses == [("transport 无效", 400), ("Provider 正在使用", 400)]


def test_provider_credential_in_use_cannot_be_deleted(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": "alibaba-default",
                        "provider_family": "alibaba_dashscope",
                        "label": "Alibaba default",
                        "auth_type": "api_key",
                        "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
                    }
                ],
                "image_gen": {"credential_ref": "alibaba-default"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="正在使用"):
        model_config.delete_provider_credential("alibaba-default")


def test_unknown_provider_credential_delete_fails_safely(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="不存在"):
        model_config.delete_provider_credential("missing")


@pytest.mark.parametrize(
    "unsafe_yaml",
    [
        "model: [\n",
        "model:\n  provider: first\nmodel:\n  provider: second\n",
    ],
)
def test_provider_credential_upsert_rejects_unsafe_yaml_without_touching_files(
    monkeypatch, tmp_path, unsafe_yaml
):
    _use_home(monkeypatch, tmp_path)
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(unsafe_yaml, encoding="utf-8")
    env_path.write_text("KEEP_EXISTING=before\n", encoding="utf-8")
    original_config = config_path.read_bytes()
    original_env = env_path.read_bytes()

    with pytest.raises(ValueError, match="config"):
        model_config.upsert_provider_credential(
            {
                "id": "new-credential",
                "provider_family": "custom",
                "api_key": "must-not-be-written",
            }
        )

    assert config_path.read_bytes() == original_config
    assert env_path.read_bytes() == original_env
    assert "TAIJI_CREDENTIAL_NEW_CREDENTIAL_API_KEY" not in os.environ


@pytest.mark.parametrize("replacement_key", [None, "zhipu-secret"])
def test_existing_credential_id_cannot_change_provider_family(
    monkeypatch, tmp_path, replacement_key
):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "label": "Alibaba", "api_key": "alibaba-secret"}
    )
    body = {"id": "shared", "provider": "zhipu", "label": "Zhipu"}
    if replacement_key is not None:
        body["api_key"] = replacement_key

    with pytest.raises(ValueError, match="Provider"):
        model_config.upsert_provider_credential(body)

    assert _read_config(tmp_path)["provider_credentials"] == [
        {
            "id": "shared",
            "provider_family": "alibaba_dashscope",
            "label": "Alibaba",
            "auth_type": "api_key",
            "secret_env": "TAIJI_CREDENTIAL_SHARED_API_KEY",
        }
    ]
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TAIJI_CREDENTIAL_SHARED_API_KEY=alibaba-secret" in env_text
    assert "zhipu-secret" not in env_text


def test_blank_credential_label_falls_back_to_id(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    result = model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "label": "   ", "api_key": "secret"}
    )

    assert result["credential"]["label"] == "shared"
    assert _read_config(tmp_path)["provider_credentials"][0]["label"] == "shared"


def test_upsert_preserves_default_when_editing_label_or_secret(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "label": "Before", "api_key": "before-secret", "default": True}
    )

    result = model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "label": "After", "api_key": "after-secret"}
    )

    assert result["credential"]["default"] is True
    assert _read_config(tmp_path)["provider_credentials"][0]["default"] is True


def test_upsert_rejects_second_family_default_without_touching_metadata_or_secrets(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "one", "provider": "alibaba", "api_key": "one-secret", "default": True}
    )
    model_config.upsert_provider_credential(
        {"id": "two", "provider": "alibaba", "api_key": "two-secret"}
    )
    before_yaml = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    before_env = (tmp_path / ".env").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="默认凭据"):
        model_config.upsert_provider_credential(
            {"id": "two", "provider": "alibaba", "api_key": "replacement", "default": True}
        )

    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == before_yaml
    assert (tmp_path / ".env").read_text(encoding="utf-8") == before_env


def test_delete_rejects_tampered_secret_env_without_touching_unrelated_env(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": "shared",
                        "provider_family": "alibaba_dashscope",
                        "label": "Shared",
                        "auth_type": "api_key",
                        "secret_env": "UNRELATED_API_KEY",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("UNRELATED_API_KEY=keep-me\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Secret 环境变量"):
        model_config.delete_provider_credential("shared")

    assert _read_config(tmp_path)["provider_credentials"][0]["id"] == "shared"
    assert "UNRELATED_API_KEY=keep-me" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_concurrent_cross_family_upserts_cannot_mismatch_metadata_and_secret(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    barrier = threading.Barrier(2)

    def save(provider, label, secret):
        barrier.wait(timeout=5)
        try:
            model_config.upsert_provider_credential(
                {"id": "shared", "provider": provider, "label": label, "api_key": secret}
            )
            return "saved"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: save(*args),
                [("alibaba", "Alibaba", "alibaba-secret"), ("zhipu", "Zhipu", "zhipu-secret")],
            )
        )

    assert sorted(results) == ["rejected", "saved"]
    row = _read_config(tmp_path)["provider_credentials"][0]
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    if row["provider_family"] == "alibaba_dashscope":
        assert row["label"] == "Alibaba"
        assert "TAIJI_CREDENTIAL_SHARED_API_KEY=alibaba-secret" in env_text
        assert "zhipu-secret" not in env_text
    else:
        assert row["provider_family"] == "zhipu"
        assert row["label"] == "Zhipu"
        assert "TAIJI_CREDENTIAL_SHARED_API_KEY=zhipu-secret" in env_text
        assert "alibaba-secret" not in env_text


@pytest.mark.skipif(os.name != "posix", reason="cross-process flock is POSIX-only")
def test_credential_transaction_serializes_cross_process_read_modify_write(tmp_path):
    config_path = tmp_path / "transaction.json"
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    processes = [
        context.Process(
            target=_credential_transaction_process_writer,
            args=(str(config_path), key, start_event),
        )
        for key in ("process_a", "process_b")
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "process_a": True,
        "process_b": True,
    }


@pytest.mark.skipif(os.name != "posix", reason="cross-process flock is POSIX-only")
def test_cli_env_writer_joins_credential_transaction_lock(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    acquired_event = context.Event()
    release_event = context.Event()
    started_event = context.Event()
    completed_event = context.Event()
    holder = context.Process(
        target=_credential_transaction_process_holder,
        args=(str(config_path), acquired_event, release_event),
    )
    writer = context.Process(
        target=_cli_env_process_writer,
        args=(
            str(config_path),
            "CLI_SERIALIZED_KEY",
            "cli-value",
            started_event,
            completed_event,
        ),
    )
    holder.start()
    assert acquired_event.wait(timeout=5)
    writer.start()
    assert started_event.wait(timeout=5)
    completed_while_locked = completed_event.wait(timeout=0.5)
    release_event.set()
    holder.join(timeout=10)
    writer.join(timeout=10)

    assert holder.exitcode == 0
    assert writer.exitcode == 0
    assert completed_while_locked is False
    assert "CLI_SERIALIZED_KEY=cli-value" in (tmp_path / ".env").read_text(
        encoding="utf-8"
    )


@pytest.mark.skipif(os.name != "posix", reason="cross-process flock is POSIX-only")
def test_runtime_credential_reader_cannot_observe_half_commit(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("provider_credentials: []\n", encoding="utf-8")
    secret_env = "TAIJI_CREDENTIAL_NEW_PROVIDER_API_KEY"
    context = multiprocessing.get_context("spawn")
    half_committed_event = context.Event()
    release_event = context.Event()
    completed_event = context.Event()
    result_queue = context.Queue()
    writer = context.Process(
        target=_half_commit_process_writer,
        args=(
            str(config_path),
            secret_env,
            half_committed_event,
            release_event,
        ),
    )
    reader = context.Process(
        target=_credential_config_process_reader,
        args=(str(config_path), completed_event, result_queue),
    )
    writer.start()
    assert half_committed_event.wait(timeout=5)
    reader.start()
    completed_while_half_committed = completed_event.wait(timeout=0.5)
    release_event.set()
    writer.join(timeout=10)
    reader.join(timeout=10)

    assert writer.exitcode == 0
    assert reader.exitcode == 0
    assert completed_while_half_committed is False
    assert result_queue.get(timeout=2)["provider_credentials"][0]["id"] == "new-provider"


def test_interleaved_upsert_delete_leaves_complete_or_absent_credential(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "api_key": "initial-secret"}
    )
    secret_written = threading.Event()
    original_atomic_write = credential_store._atomic_write_credential_bytes

    def delayed_env_write(path, payload, **kwargs):
        result = original_atomic_write(path, payload, **kwargs)
        if Path(path).name == ".env" and b"updated-secret" in payload:
            secret_written.set()
            time.sleep(0.1)
        return result

    monkeypatch.setattr(
        credential_store,
        "_atomic_write_credential_bytes",
        delayed_env_write,
    )

    def upsert():
        model_config.upsert_provider_credential(
            {"id": "shared", "provider": "alibaba", "api_key": "updated-secret"}
        )

    def delete():
        assert secret_written.wait(timeout=5)
        model_config.delete_provider_credential("shared")

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), [upsert, delete]))

    rows = _read_config(tmp_path).get("provider_credentials", [])
    env_text = (tmp_path / ".env").read_text(encoding="utf-8") if (tmp_path / ".env").exists() else ""
    if rows:
        assert rows[0]["secret_env"] == "TAIJI_CREDENTIAL_SHARED_API_KEY"
        assert "TAIJI_CREDENTIAL_SHARED_API_KEY=updated-secret" in env_text
    else:
        assert "TAIJI_CREDENTIAL_SHARED_API_KEY" not in env_text


def test_upsert_leaves_previous_pair_when_journal_write_fails(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "label": "Before", "api_key": "before-secret"}
    )
    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("simulated journal failure")
        ),
    )
    with pytest.raises(OSError, match="simulated journal failure"):
        model_config.upsert_provider_credential(
            {"id": "shared", "provider": "alibaba", "label": "After", "api_key": "after-secret"}
        )

    assert _read_config(tmp_path)["provider_credentials"][0]["label"] == "Before"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TAIJI_CREDENTIAL_SHARED_API_KEY=before-secret" in env_text
    assert "after-secret" not in env_text


def test_upsert_preserves_durable_commit_after_post_commit_failure(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": "shared",
                        "provider_family": "alibaba_dashscope",
                        "label": "Before",
                        "auth_type": "api_key",
                        "secret_env": "TAIJI_CREDENTIAL_SHARED_API_KEY",
                    }
                ],
                "auxiliary": {
                    "vision": {
                        "provider": "alibaba",
                        "model": "qwen3-vl-plus",
                        "credential_ref": "shared",
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "TAIJI_CREDENTIAL_SHARED_API_KEY=before-secret\n",
        encoding="utf-8",
    )
    api_config.reload_config()
    assert api_config.get_config()["provider_credentials"][0]["label"] == "Before"

    monkeypatch.setattr(
        model_config,
        "_invalidate_vision_verification",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("simulated invalidation failure")
        ),
    )

    result = model_config.upsert_provider_credential(
        {
            "id": "shared",
            "provider": "alibaba",
            "label": "After",
            "api_key": "after-secret",
        }
    )

    assert _read_config(tmp_path)["provider_credentials"][0]["label"] == "After"
    assert api_config.get_config()["provider_credentials"][0]["label"] == "After"
    assert result["refresh_pending"] is True
    assert result["warnings"] == [
        "vision_verification_refresh_pending"
    ]
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TAIJI_CREDENTIAL_SHARED_API_KEY=after-secret" in env_text
    assert "before-secret" not in env_text


def test_upsert_preserves_exact_pair_when_env_stage_preparation_fails(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {
            "id": "shared",
            "provider": "alibaba",
            "label": "Before",
            "api_key": "before-secret",
        }
    )
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    original_config = config_path.read_bytes()
    original_env = env_path.read_bytes()

    original_prepare = credential_store._prepare_pair_target

    def fail_env_stage(*, name, **kwargs):
        if name == "env":
            raise OSError("env stage failure")
        return original_prepare(name=name, **kwargs)

    monkeypatch.setattr(credential_store, "_prepare_pair_target", fail_env_stage)

    with pytest.raises(OSError, match="env stage failure"):
        model_config.upsert_provider_credential(
            {
                "id": "shared",
                "provider": "alibaba",
                "label": "After",
                "api_key": "after-secret",
            }
        )

    assert config_path.read_bytes() == original_config
    assert env_path.read_bytes() == original_env


def test_delete_preserves_pair_when_journal_write_fails(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "shared", "provider": "alibaba", "label": "Shared", "api_key": "shared-secret"}
    )
    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("simulated journal failure")
        ),
    )
    with pytest.raises(OSError, match="simulated journal failure"):
        model_config.delete_provider_credential("shared")

    assert _read_config(tmp_path)["provider_credentials"][0]["label"] == "Shared"
    assert "TAIJI_CREDENTIAL_SHARED_API_KEY=shared-secret" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")


def test_delete_preserves_exact_pair_when_env_stage_preparation_fails(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {
            "id": "shared",
            "provider": "alibaba",
            "label": "Shared",
            "api_key": "shared-secret",
        }
    )
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    original_config = config_path.read_bytes()
    original_env = env_path.read_bytes()

    original_prepare = credential_store._prepare_pair_target

    def fail_env_stage(*, name, **kwargs):
        if name == "env":
            raise OSError("env stage failure")
        return original_prepare(name=name, **kwargs)

    monkeypatch.setattr(credential_store, "_prepare_pair_target", fail_env_stage)

    with pytest.raises(OSError, match="env stage failure"):
        model_config.delete_provider_credential("shared")

    assert config_path.read_bytes() == original_config
    assert env_path.read_bytes() == original_env


def test_legacy_dashscope_api_key_payload_remains_compatible(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=old-key\n", encoding="utf-8")

    model_config.set_vision_config(
        {"provider": "alibaba", "model": "qwen3-vl-plus", "api_key": "new-legacy-key"}
    )

    assert "DASHSCOPE_API_KEY=new-legacy-key" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "api_key" not in _read_config(tmp_path)["auxiliary"]["vision"]


def test_main_model_config_writes_deepseek_key_without_echo(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    result = model_config.set_main_model_config(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_key": "sk-deepseek-test-key-123456",
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["model"]["provider"] == "deepseek"
    assert cfg["model"]["default"] == "deepseek-chat"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=sk-deepseek-test-key-123456" in env_text
    assert "sk-deepseek-test-key-123456" not in json.dumps(result)
    os.environ.pop("DEEPSEEK_API_KEY", None)


def test_custom_main_model_uses_key_env_not_inline_secret(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("HERMES_CUSTOM_MODEL_API_KEY", raising=False)

    result = model_config.set_main_model_config(
        {
            "provider": "custom",
            "model": "my-image-aware-model",
            "base_url": "https://custom.example.com/v1/",
            "api_key": "custom-secret-key-123456",
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["model"]["provider"] == "custom"
    assert cfg["model"]["default"] == "my-image-aware-model"
    assert cfg["model"]["base_url"] == "https://custom.example.com/v1"
    assert cfg["model"]["key_env"] == "HERMES_CUSTOM_MODEL_API_KEY"
    assert "api_key" not in cfg["model"]
    assert "HERMES_CUSTOM_MODEL_API_KEY=custom-secret-key-123456" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "custom-secret-key-123456" not in json.dumps(result)
    os.environ.pop("HERMES_CUSTOM_MODEL_API_KEY", None)


def test_main_model_config_acquires_credential_transaction_before_cfg_lock(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path)
    transaction_depth = 0

    @contextmanager
    def tracked_transaction(_config_path):
        nonlocal transaction_depth
        transaction_depth += 1
        try:
            yield
        finally:
            transaction_depth -= 1

    def strict_loader(_config_path):
        assert transaction_depth == 1
        return {}

    monkeypatch.setattr(
        model_config,
        "credential_transaction",
        tracked_transaction,
    )
    monkeypatch.setattr(
        model_config,
        "load_credential_config",
        strict_loader,
    )
    monkeypatch.setattr(model_config, "reload_config", lambda: None)
    monkeypatch.setattr(model_config, "invalidate_models_cache", lambda: None)
    monkeypatch.setattr(
        model_config,
        "_get_model_config_unlocked",
        lambda **_kwargs: {"ok": True},
    )

    assert model_config.set_main_model_config(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
        }
    ) == {"ok": True}


def test_oauth_main_provider_rejected_from_webui(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    try:
        model_config.set_main_model_config(
            {"provider": "openai-codex", "model": "gpt-5.1-codex"}
        )
    except ValueError as exc:
        assert "网页登录授权" in str(exc)
        assert "太极智能体" in str(exc)
        assert "hermes" not in str(exc)
        assert "Hermes" not in str(exc)
    else:
        raise AssertionError("OAuth provider accepted WebUI API-key setup")


def test_image_gen_config_rejects_non_domestic_provider(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [
            {
                "id": "fal",
                "name": "FAL",
                "models": [{"id": "fal-ai/flux-2-pro", "label": "Flux 2 Pro"}],
                "default_model": "fal-ai/flux-2-pro",
                "key_status": {"configured": False, "env_var": "FAL_KEY"},
                "domestic": False,
                "integration_status": "blocked",
                "policy_blocked": True,
            }
        ],
    )

    try:
        model_config.set_image_gen_config(
            {
                "provider": "fal",
                "model": "fal-ai/flux-2-pro",
                "api_key": "fal-test-key-123456",
            }
        )
    except ValueError as exc:
        assert "国产" in str(exc)
    else:
        raise AssertionError("non-domestic image generation provider was accepted")
    assert not (tmp_path / ".env").exists()


def test_image_gen_config_writes_multi_field_domestic_credentials_without_echo(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    for key in ("DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID", "DASHSCOPE_REGION"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [
            {
                "id": "dashscope",
                "name": "通义 Qwen-Image",
                "models": [{"id": "qwen-image-2.0-pro", "label": "Qwen Image 2 Pro"}],
                "default_model": "qwen-image-2.0-pro",
                "key_status": {"configured": False, "env_var": "DASHSCOPE_API_KEY"},
                "credential_fields": [
                    {
                        "name": "api_key",
                        "env_var": "DASHSCOPE_API_KEY",
                        "label": "API Key",
                        "required": True,
                        "secret": True,
                    },
                    {
                        "name": "workspace_id",
                        "env_var": "DASHSCOPE_WORKSPACE_ID",
                        "label": "Workspace ID",
                        "required": True,
                        "secret": False,
                    },
                    {
                        "name": "region",
                        "env_var": "DASHSCOPE_REGION",
                        "label": "Region",
                        "required": False,
                        "secret": False,
                    },
                ],
                "credential_status": {
                    "configured": False,
                    "missing": ["DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID"],
                },
                "domestic": True,
                "integration_status": "stable",
            }
        ],
    )

    result = model_config.set_image_gen_config(
        {
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "credentials": {
                "api_key": "dashscope-test-key-123456",
                "workspace_id": "ws-cn-test",
                "region": "cn-beijing",
            },
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["image_gen"]["provider"] == "dashscope"
    assert cfg["image_gen"]["model"] == "qwen-image-2.0-pro"
    assert cfg["image_gen"]["use_gateway"] is False
    assert cfg["image_gen"]["options"]["workspace_id"] == "ws-cn-test"
    assert cfg["image_gen"]["options"]["region"] == "cn-beijing"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=dashscope-test-key-123456" in env_text
    assert "DASHSCOPE_WORKSPACE_ID" not in env_text
    assert "DASHSCOPE_REGION" not in env_text
    dumped = json.dumps(result, ensure_ascii=False)
    assert "dashscope-test-key-123456" not in dumped
    assert "ws-cn-test" not in dumped
    for key in ("DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID", "DASHSCOPE_REGION"):
        os.environ.pop(key, None)


def _dashscope_image_provider_row():
    return {
        "id": "dashscope",
        "name": "通义 Qwen-Image",
        "models": [{"id": "qwen-image-2.0-pro", "label": "Qwen Image 2 Pro"}],
        "default_model": "qwen-image-2.0-pro",
        "key_status": {"configured": False, "env_var": "DASHSCOPE_API_KEY"},
        "credential_fields": [
            {
                "name": "api_key",
                "env_var": "DASHSCOPE_API_KEY",
                "label": "API Key",
                "required": True,
                "secret": True,
            },
            {
                "name": "workspace_id",
                "env_var": "DASHSCOPE_WORKSPACE_ID",
                "label": "Workspace ID",
                "required": True,
                "secret": False,
            },
        ],
        "domestic": True,
        "integration_status": "stable",
    }


def test_dashscope_image_can_share_named_credential_with_vision(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [_dashscope_image_provider_row()],
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=legacy-must-stay\n", encoding="utf-8"
    )
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "shared-named-secret",
        }
    )
    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "credential_ref": "alibaba-default",
        }
    )

    model_config.set_image_gen_config(
        {
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "credential_ref": "alibaba-default",
            "credentials": {"workspace_id": "llm-demo"},
        }
    )

    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"]["credential_ref"] == "alibaba-default"
    assert saved["image_gen"]["credential_ref"] == "alibaba-default"
    assert saved["image_gen"]["options"]["workspace_id"] == "llm-demo"
    assert "api_key" not in saved["image_gen"]
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=legacy-must-stay" in env_text
    assert "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=shared-named-secret" in env_text


def test_dashscope_independent_credential_rotation_does_not_change_shared_or_legacy_key(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [_dashscope_image_provider_row()],
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=legacy-must-stay\n", encoding="utf-8"
    )
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "api_key": "shared-secret",
        }
    )
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-image",
            "provider": "dashscope",
            "api_key": "image-secret-before",
        }
    )
    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "credential_ref": "alibaba-default",
        }
    )
    model_config.set_image_gen_config(
        {
            "provider": "dashscope",
            "credential_ref": "alibaba-image",
            "credentials": {"workspace_id": "llm-demo"},
        }
    )

    model_config.upsert_provider_credential(
        {
            "id": "alibaba-image",
            "provider": "dashscope",
            "api_key": "image-secret-after",
        }
    )

    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"]["credential_ref"] == "alibaba-default"
    assert saved["image_gen"]["credential_ref"] == "alibaba-image"
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=legacy-must-stay" in env_text
    assert "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=shared-secret" in env_text
    assert "TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY=image-secret-after" in env_text
    assert "image-secret-before" not in env_text


def test_dashscope_image_rejects_named_ref_with_inline_api_key(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [_dashscope_image_provider_row()],
    )
    model_config.upsert_provider_credential(
        {"id": "alibaba-default", "provider": "alibaba", "api_key": "named-secret"}
    )

    with pytest.raises(ValueError, match="credential_ref.*api_key"):
        model_config.set_image_gen_config(
            {
                "provider": "dashscope",
                "credential_ref": "alibaba-default",
                "api_key": "must-not-write",
            }
        )

    assert "must-not-write" not in (tmp_path / ".env").read_text(encoding="utf-8")


def test_legacy_dashscope_inline_key_rolls_back_when_yaml_save_fails(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [_dashscope_image_provider_row()],
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=old-legacy-secret\n", encoding="utf-8"
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "old-legacy-secret")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {"image_gen": {"provider": "dashscope", "model": "qwen-image"}}
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("disk full")
        ),
    )

    with pytest.raises(OSError, match="disk full"):
        model_config.set_image_gen_config(
            {
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "api_key": "new-secret-must-roll-back",
            }
        )

    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "DASHSCOPE_API_KEY=old-legacy-secret\n"
    )
    assert os.environ["DASHSCOPE_API_KEY"] == "old-legacy-secret"
    assert _read_config(tmp_path)["image_gen"] == {
        "provider": "dashscope",
        "model": "qwen-image",
    }


def test_concurrent_image_binding_and_credential_delete_cannot_leave_dangling_ref(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [_dashscope_image_provider_row()],
    )
    model_config.upsert_provider_credential(
        {"id": "alibaba-image", "provider": "dashscope", "api_key": "named-secret"}
    )
    validated = threading.Event()
    continue_binding = threading.Event()
    original_load_credential = model_config.load_credential

    def pause_after_validation(*args, **kwargs):
        row = original_load_credential(*args, **kwargs)
        validated.set()
        assert continue_binding.wait(timeout=2)
        return row

    monkeypatch.setattr(model_config, "load_credential", pause_after_validation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        bind_future = pool.submit(
            model_config.set_image_gen_config,
            {"provider": "dashscope", "credential_ref": "alibaba-image"},
        )
        assert validated.wait(timeout=2)
        delete_future = pool.submit(
            model_config.delete_provider_credential, "alibaba-image"
        )
        time.sleep(0.1)
        continue_binding.set()
        bind_future.result(timeout=2)
        with pytest.raises(ValueError, match="正在使用"):
            delete_future.result(timeout=2)

    saved = _read_config(tmp_path)
    assert saved["image_gen"]["credential_ref"] == "alibaba-image"
    assert saved["provider_credentials"][0]["id"] == "alibaba-image"


def test_shared_credential_rotation_invalidates_every_referencing_capability(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {"id": "alibaba-default", "provider": "alibaba", "api_key": "before"}
    )
    saved = _read_config(tmp_path)
    saved["auxiliary"] = {"vision": {"credential_ref": "alibaba-default"}}
    saved["image_gen"] = {"credential_ref": "alibaba-default"}
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(saved), encoding="utf-8"
    )
    invalidated = []
    monkeypatch.setattr(
        model_config,
        "_invalidate_vision_verification",
        lambda *_args, **_kwargs: invalidated.append("vision"),
    )
    monkeypatch.setattr(
        model_config,
        "_invalidate_image_gen_verification",
        lambda *_args, **_kwargs: invalidated.append("image_gen"),
        raising=False,
    )

    model_config.upsert_provider_credential(
        {"id": "alibaba-default", "provider": "dashscope", "api_key": "after"}
    )

    assert invalidated == ["vision", "image_gen"]


def test_image_gen_config_writes_doubao_ark_key_without_echo(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda active: [
            {
                "id": "doubao",
                "name": "Doubao Seedream",
                "models": [
                    {
                        "id": "doubao-seedream-5-0-260128",
                        "label": "Doubao Seedream 5.0 Lite",
                    }
                ],
                "default_model": "doubao-seedream-5-0-260128",
                "key_status": {"configured": False, "env_var": "ARK_API_KEY"},
            }
        ],
    )

    result = model_config.set_image_gen_config(
        {
            "provider": "doubao",
            "model": "doubao-seedream-5-0-260128",
            "api_key": "ark-test-key-123456",
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["image_gen"]["provider"] == "doubao"
    assert cfg["image_gen"]["model"] == "doubao-seedream-5-0-260128"
    assert cfg["image_gen"]["use_gateway"] is False
    assert "ARK_API_KEY=ark-test-key-123456" in (tmp_path / ".env").read_text(
        encoding="utf-8"
    )
    assert "ark-test-key-123456" not in json.dumps(result)
    os.environ.pop("ARK_API_KEY", None)


def test_vision_config_writes_auxiliary_vision_and_key_without_echo(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    result = model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "api_key": "dashscope-test-key-123456",
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["auxiliary"]["vision"]["provider"] == "alibaba"
    assert cfg["auxiliary"]["vision"]["model"] == "qwen3-vl-plus"
    assert "api_key" not in cfg["auxiliary"]["vision"]
    assert "DASHSCOPE_API_KEY=dashscope-test-key-123456" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "dashscope-test-key-123456" not in json.dumps(result)
    assert result["vision"]["key_status"]["env_var"] == "DASHSCOPE_API_KEY"
    os.environ.pop("DASHSCOPE_API_KEY", None)


def test_vision_save_does_not_adopt_process_only_default_credential(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    credential_ref = "profile-b-default"
    secret_env = model_config.credential_secret_env(credential_ref)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": credential_ref,
                        "provider_family": "alibaba_dashscope",
                        "label": "Profile B default",
                        "auth_type": "api_key",
                        "secret_env": secret_env,
                        "default": True,
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(secret_env, "stale-profile-a-secret")

    result = model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
        }
    )

    saved = _read_config(tmp_path)["auxiliary"]["vision"]
    assert saved["credential_ref"] == ""
    assert result["vision"]["credential_ref"] == ""
    assert result["vision"]["key_status"]["configured"] is False
    assert result["vision"]["verification"]["status"] == "unconfigured"


def test_image_save_does_not_adopt_process_only_default_credential(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    credential_ref = "profile-b-default"
    secret_env = model_config.credential_secret_env(credential_ref)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": credential_ref,
                        "provider_family": "alibaba_dashscope",
                        "label": "Profile B default",
                        "auth_type": "api_key",
                        "secret_env": secret_env,
                        "default": True,
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(secret_env, "stale-profile-a-secret")

    result = model_config.set_image_gen_config(
        {
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
        }
    )

    saved = _read_config(tmp_path)["image_gen"]
    assert saved["credential_ref"] == ""
    assert result["image_gen"]["credential_ref"] == ""
    assert result["image_gen"]["verification"]["status"] == "unconfigured"


def test_alibaba_vision_config_persists_named_credential_and_beijing_endpoint(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "named-alibaba-secret",
        }
    )

    result = model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "credential_ref": "alibaba-default",
            "endpoint_mode": "public",
            "region": "cn-beijing",
        }
    )

    saved = _read_config(tmp_path)["auxiliary"]["vision"]
    assert saved == {
        "provider": "alibaba",
        "model": "qwen3-vl-plus",
        "credential_ref": "alibaba-default",
        "endpoint_mode": "public",
        "region": "cn-beijing",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "endpoint_field_names": ["base_url", "endpoint_mode", "region", "workspace_id"],
    }
    assert result["vision"]["credential_ref"] == "alibaba-default"
    assert result["vision"]["endpoint_mode"] == "public"
    assert result["vision"]["region"] == "cn-beijing"
    assert result["vision"]["workspace_id"] == ""
    assert result["vision"]["base_url"] == saved["base_url"]
    assert "named-alibaba-secret" not in json.dumps(result, ensure_ascii=False)


def test_alibaba_vision_config_requires_explicit_international_region(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    model_config.set_vision_config(
        {"provider": "alibaba", "model": "qwen3-vl-plus"}
    )
    assert (
        _read_config(tmp_path)["auxiliary"]["vision"]["base_url"]
        == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )

    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "endpoint_mode": "public",
            "region": "ap-southeast-1",
        }
    )
    assert (
        _read_config(tmp_path)["auxiliary"]["vision"]["base_url"]
        == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    )


def test_alibaba_single_key_configures_public_vision_and_image_atomically(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {"provider": "deepseek", "default": "deepseek-chat"},
                "auxiliary": {
                    "vision": {
                        "provider": "alibaba",
                        "model": "qwen3-vl-flash",
                        "credential_ref": "old-ref",
                        "endpoint_mode": "workspace",
                        "workspace_id": "old-workspace",
                    }
                },
                "image_gen": {
                    "provider": "dashscope",
                    "model": "qwen-image",
                    "credential_ref": "old-ref",
                    "options": {
                        "workspace_id": "old-workspace",
                        "base_url": "https://old.example",
                        "temperature": "0.4",
                    },
                },
                "unrelated": {"keep": True},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    vision_invalidations = []
    image_invalidations = []
    monkeypatch.setattr(
        model_config,
        "_invalidate_vision_verification",
        lambda *_args, **_kwargs: vision_invalidations.append(True),
    )
    monkeypatch.setattr(
        model_config,
        "_invalidate_image_gen_verification",
        lambda *_args, **_kwargs: image_invalidations.append(True),
    )

    result = model_config.set_alibaba_image_capabilities(
        {
            "api_key": "single-test-key",
            "vision_model": "qwen3-vl-plus",
            "image_model": "qwen-image-2.0-pro",
        }
    )

    saved = _read_config(tmp_path)
    assert saved["model"] == {
        "provider": "deepseek",
        "default": "deepseek-chat",
    }
    assert saved["unrelated"] == {"keep": True}
    assert saved["provider_credentials"] == [
        {
            "id": "taiji-alibaba-quick",
            "provider_family": "alibaba_dashscope",
            "label": "阿里百炼快速配置",
            "auth_type": "api_key",
            "secret_env": "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY",
        }
    ]
    assert saved["auxiliary"]["vision"] == {
        "provider": "alibaba",
        "model": "qwen3-vl-plus",
        "credential_ref": "taiji-alibaba-quick",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "endpoint_mode": "public",
        "region": "cn-beijing",
        "endpoint_field_names": ["base_url", "endpoint_mode", "region"],
    }
    assert saved["image_gen"] == {
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
        "use_gateway": False,
        "credential_ref": "taiji-alibaba-quick",
        "options": {
            "temperature": "0.4",
            "endpoint_mode": "public",
            "region": "cn-beijing",
        },
        "endpoint_field_names": ["endpoint_mode", "region"],
    }
    assert "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY=single-test-key" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY" not in (tmp_path / ".env").read_text(
        encoding="utf-8"
    )
    assert "single-test-key" not in json.dumps(result, ensure_ascii=False)
    assert result["vision"] == {
        "provider": "alibaba",
        "model": "qwen3-vl-plus",
        "credential_ref": "taiji-alibaba-quick",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "endpoint_mode": "public",
        "region": "cn-beijing",
        "key_status": {
            "configured": True,
            "source": "env_file",
            "env_var": "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY",
        },
        "verification": {
            "status": "configured_unverified",
            "checked_at": "",
            "error_code": "",
            "message": "识图配置已保存，但尚未通过真实图片验证。",
            "diagnostic_id": "",
        },
    }
    assert result["image_gen"] == {
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
        "credential_ref": "taiji-alibaba-quick",
        "options": {"endpoint_mode": "public", "region": "cn-beijing"},
        "key_status": {
            "configured": True,
            "source": "env_file",
            "env_var": "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY",
        },
        "verification": {
            "status": "configured_unverified",
            "checked_at": "",
            "error_code": "",
            "message": "生图配置已保存，但尚未通过真实生图验证。",
            "diagnostic_id": "",
        },
    }
    public_dump = json.dumps(result, ensure_ascii=False)
    assert "single-test-key" not in public_dump
    assert "secret_env" not in public_dump
    reserved = next(
        row
        for row in result["provider_credentials"]
        if row["id"] == "taiji-alibaba-quick"
    )
    assert reserved["configured"] is True
    assert set(reserved["used_by"]) == {"auxiliary.vision", "image_gen"}
    assert "secret_env" not in reserved
    vision_provider = next(
        row for row in result["vision_providers"] if row["id"] == "alibaba"
    )
    assert vision_provider["key_status"]["configured"] is True
    assert {row["id"] for row in vision_provider["models"]} == {
        "qwen3-vl-plus",
        "qwen3-vl-flash",
        "qwen2.5-vl-72b-instruct",
    }
    image_provider = next(
        row
        for row in result["image_gen_providers"]
        if row["id"] == "dashscope"
    )
    assert image_provider["key_status"]["configured"] is True
    assert {row["id"] for row in image_provider["models"]} == {
        "qwen-image-2.0-pro",
        "qwen-image",
    }
    assert vision_invalidations == [True]
    assert image_invalidations == [True]


def test_alibaba_single_key_blank_key_preserves_existing_secret(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=existing-test-key\n", encoding="utf-8"
    )

    result = model_config.set_alibaba_image_capabilities(
        {
            "api_key": "",
            "vision_model": "qwen3-vl-flash",
            "image_model": "qwen-image",
        }
    )

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=existing-test-key" in env_text
    assert (
        "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY=existing-test-key"
        in env_text
    )
    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"]["credential_ref"] == (
        "taiji-alibaba-quick"
    )
    assert saved["image_gen"]["credential_ref"] == "taiji-alibaba-quick"
    assert "existing-test-key" not in json.dumps(result, ensure_ascii=False)


def test_alibaba_single_key_explicit_ref_beats_another_default_credential(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": "alibaba-default",
                        "provider_family": "alibaba_dashscope",
                        "label": "Other default",
                        "auth_type": "api_key",
                        "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
                        "default": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=other-default-key\n",
        encoding="utf-8",
    )

    model_config.set_alibaba_image_capabilities(
        {
            "api_key": "quick-test-key",
            "vision_model": "qwen3-vl-plus",
            "image_model": "qwen-image-2.0-pro",
        }
    )

    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"]["credential_ref"] == (
        "taiji-alibaba-quick"
    )
    assert saved["image_gen"]["credential_ref"] == "taiji-alibaba-quick"
    from agent.provider_credentials import resolve_api_key

    assert resolve_api_key(
        "alibaba",
        saved["auxiliary"]["vision"]["credential_ref"],
        config_data=saved,
    ) == "quick-test-key"
    assert resolve_api_key(
        "dashscope",
        saved["image_gen"]["credential_ref"],
        config_data=saved,
    ) == "quick-test-key"


@pytest.mark.parametrize(
    "bad_row",
    [
        {
            "id": "taiji-alibaba-quick",
            "provider_family": "zhipu",
            "auth_type": "api_key",
            "secret_env": "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY",
        },
        {
            "id": "taiji-alibaba-quick",
            "provider_family": "alibaba_dashscope",
            "auth_type": "api_key",
            "secret_env": "WRONG_ENV",
        },
    ],
)
def test_alibaba_single_key_rejects_invalid_reserved_credential_row(
    monkeypatch, tmp_path, bad_row
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    original = {"provider_credentials": [bad_row]}
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(original), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="凭据|Provider|Secret"):
        model_config.set_alibaba_image_capabilities(
            {
                "api_key": "new-test-key",
                "vision_model": "qwen3-vl-plus",
                "image_model": "qwen-image-2.0-pro",
            }
        )

    assert _read_config(tmp_path) == original


@pytest.mark.parametrize(
    "payload",
    [
        {"vision_model": "unknown-vl", "image_model": "qwen-image"},
        {"vision_model": "qwen3-vl-plus", "image_model": "unknown-image"},
    ],
)
def test_alibaba_single_key_rejects_models_outside_allowlists(
    monkeypatch, tmp_path, payload
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=existing-test-key\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="model"):
        model_config.set_alibaba_image_capabilities(payload)


def test_alibaba_single_key_rolls_back_env_and_config_on_save_failure(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    original = {"unrelated": {"keep": True}}
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(original), encoding="utf-8"
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=old-test-key\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("disk full")
        ),
    )

    with pytest.raises(OSError, match="disk full"):
        model_config.set_alibaba_image_capabilities(
            {
                "api_key": "new-test-key",
                "vision_model": "qwen3-vl-plus",
                "image_model": "qwen-image-2.0-pro",
            }
        )

    assert _read_config(tmp_path) == original
    assert (tmp_path / ".env").read_text(encoding="utf-8").strip() == (
        "DASHSCOPE_API_KEY=old-test-key"
    )


def test_alibaba_single_key_reports_refresh_pending_after_committed_save(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(
        model_config,
        "reload_config",
        lambda: (_ for _ in ()).throw(RuntimeError("refresh failed")),
    )

    result = model_config.set_alibaba_image_capabilities(
        {
            "api_key": "new-test-key",
            "vision_model": "qwen3-vl-plus",
            "image_model": "qwen-image-2.0-pro",
        }
    )

    assert result["ok"] is True
    assert result["refresh_pending"] is True
    assert result["warnings"] == ["runtime_config_refresh_pending"]
    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"]["credential_ref"] == (
        "taiji-alibaba-quick"
    )
    assert saved["image_gen"]["credential_ref"] == "taiji-alibaba-quick"


def test_alibaba_single_key_public_metadata_failure_uses_safe_fallback(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(
        model_config,
        "_public_provider_credentials_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("metadata failed")
        ),
    )
    monkeypatch.setattr(
        model_config,
        "_vision_provider_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("vision metadata failed")
        ),
    )
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("image metadata failed")
        ),
    )

    result = model_config.set_alibaba_image_capabilities(
        {
            "api_key": "fallback-test-key",
            "vision_model": "qwen3-vl-plus",
            "image_model": "qwen-image-2.0-pro",
        }
    )

    assert result["ok"] is True
    assert result["refresh_pending"] is True
    assert set(result["warnings"]) == {
        "provider_credentials_refresh_pending",
        "vision_provider_metadata_refresh_pending",
        "image_gen_provider_metadata_refresh_pending",
    }
    assert result["provider_credentials"] == [
        {
            "id": "taiji-alibaba-quick",
            "provider_family": "alibaba_dashscope",
            "label": "阿里百炼快速配置",
            "auth_type": "api_key",
            "default": False,
            "configured": True,
            "used_by": ["auxiliary.vision", "image_gen"],
        }
    ]
    assert result["vision_providers"][0]["id"] == "alibaba"
    assert result["vision_providers"][0]["key_status"]["configured"] is True
    assert len(result["vision_providers"][0]["models"]) == 3
    assert result["image_gen_providers"][0]["id"] == "dashscope"
    assert result["image_gen_providers"][0]["key_status"]["configured"] is True
    assert len(result["image_gen_providers"][0]["models"]) == 2
    public_dump = json.dumps(result, ensure_ascii=False)
    assert "fallback-test-key" not in public_dump
    assert "secret_env" not in public_dump


def test_alibaba_vision_config_rejects_model_outside_server_allowlist(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="unknown Alibaba vision model"):
        model_config.set_vision_config(
            {"provider": "alibaba", "model": "unlisted-qwen-vl"}
        )


def test_alibaba_vision_rejects_credential_ref_with_inline_api_key(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "named-secret",
        }
    )

    with pytest.raises(ValueError, match="credential_ref.*api_key"):
        model_config.set_vision_config(
            {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "credential_ref": "alibaba-default",
                "api_key": "must-not-write",
            }
        )

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY" not in env_text
    assert "must-not-write" not in env_text


def test_concurrent_vision_binding_and_credential_delete_cannot_leave_dangling_ref(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "named-secret",
        }
    )
    validated = threading.Event()
    continue_binding = threading.Event()
    original_load_credential = model_config.load_credential

    def pause_after_validation(*args, **kwargs):
        row = original_load_credential(*args, **kwargs)
        validated.set()
        assert continue_binding.wait(timeout=2)
        return row

    monkeypatch.setattr(model_config, "load_credential", pause_after_validation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        bind_future = pool.submit(
            model_config.set_vision_config,
            {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "credential_ref": "alibaba-default",
            },
        )
        assert validated.wait(timeout=2)
        delete_future = pool.submit(
            model_config.delete_provider_credential, "alibaba-default"
        )
        time.sleep(0.1)
        continue_binding.set()
        bind_future.result(timeout=2)
        with pytest.raises(ValueError, match="正在使用"):
            delete_future.result(timeout=2)

    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"]["credential_ref"] == "alibaba-default"
    assert saved["provider_credentials"][0]["id"] == "alibaba-default"


def test_model_config_includes_image_understanding_config(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "auxiliary": {
                    "vision": {
                        "provider": "zai",
                        "model": "glm-5v-turbo",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = model_config.get_model_config()

    assert result["vision"]["provider"] == "zai"
    assert result["vision"]["model"] == "glm-5v-turbo"
    assert any(row["id"] == "alibaba" for row in result["vision_providers"])
    assert any(row["id"] == "zai" for row in result["vision_providers"])


def test_custom_vision_config_without_key_does_not_write_placeholder(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("AUXILIARY_VISION_API_KEY", raising=False)

    model_config.set_vision_config(
        {
            "provider": "custom",
            "model": "qwen-vl-private",
            "base_url": "http://127.0.0.1:8000/v1",
        }
    )

    cfg = _read_config(tmp_path)
    assert cfg["auxiliary"]["vision"]["provider"] == "custom"
    assert cfg["auxiliary"]["vision"]["base_url"] == "http://127.0.0.1:8000/v1"
    assert "api_key" not in cfg["auxiliary"]["vision"]


def _write_saved_vision_config(tmp_path, *, provider="alibaba", model="qwen3-vl-plus"):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"auxiliary": {"vision": {"provider": provider, "model": model}}}),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=test-only-key\n", encoding="utf-8")


def test_vision_config_distinguishes_configured_from_verified(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: tmp_path / "vision-verification.json")
    _write_saved_vision_config(tmp_path)

    result = model_config.get_vision_config()

    assert result["vision"]["verification"]["status"] == "configured_unverified"
    assert result["vision"]["verification"]["checked_at"] == ""


def test_vision_test_rejects_unconfigured_without_calling_provider(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: tmp_path / "vision-verification.json")
    calls = []

    async def should_not_run(**kwargs):
        calls.append(kwargs)
        return json.dumps({"success": True, "analysis": "TAIJI-VISION-CHECK-7319"})

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", should_not_run)
    result = model_config.test_vision_config()

    assert calls == []
    assert result["ok"] is False
    assert result["status"] == "unconfigured"
    assert result["error_code"] == "vision_not_configured"
    assert set(result) == {
        "ok", "status", "checked_at", "provider", "model",
        "error_code", "message", "diagnostic_id",
    }


def test_vision_test_rejects_unresolved_runtime_without_calling_provider(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(
        model_config,
        "_vision_verification_state_path",
        lambda *_: state_path,
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "auxiliary": {
                    "vision": {
                        "provider": "alibaba",
                        "model": "qwen3-vl-plus",
                        "endpoint_mode": "custom",
                        "base_url": "${MISSING_VISION_ENDPOINT}",
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=profile-b-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MISSING_VISION_ENDPOINT", raising=False)
    calls = []

    async def should_not_run(**kwargs):
        calls.append(kwargs)
        return json.dumps(
            {
                "success": True,
                "analysis": "TAIJI-VISION-CHECK-7319",
                "resolved_provider": "alibaba",
                "resolved_model": "qwen3-vl-plus",
            }
        )

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(
        vision_tools,
        "vision_analyze_tool",
        should_not_run,
    )

    result = model_config.test_vision_config()

    assert result["ok"] is False
    assert result["status"] == "configured_unverified"
    assert result["error_code"] == "unresolved_effective_config"
    assert calls == []
    assert not state_path.exists()


def test_vision_test_persists_verified_result_without_model_text_or_secret(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)
    calls = []

    async def succeed(**kwargs):
        calls.append(kwargs)
        return json.dumps({
            "success": True,
            "analysis": "The image contains TAIJI-VISION-CHECK-7319 and secret-model-text.",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", succeed)
    result = model_config.test_vision_config()

    assert result["ok"] is True
    assert result["status"] == "verified"
    assert result["provider"] == "alibaba"
    assert result["model"] == "qwen3-vl-plus"
    assert result["error_code"] == ""
    assert calls and calls[0]["model"] == "qwen3-vl-plus"
    assert calls[0]["provider"] == "alibaba"
    assert calls[0]["strict_target"] is True
    assert Path(calls[0]["image_url"]).name == "vision-verification-probe.png"
    assert "识别图片" in calls[0]["user_prompt"]
    assert "TAIJI-VISION-CHECK-7319" not in calls[0]["user_prompt"]
    public_dump = json.dumps(result, ensure_ascii=False)
    persisted_dump = state_path.read_text(encoding="utf-8")
    for forbidden in ("test-only-key", "secret-model-text", str(tmp_path)):
        assert forbidden not in public_dump
        assert forbidden not in persisted_dump
    assert model_config.get_vision_config()["vision"]["verification"]["status"] == "verified"


def test_vision_probe_full_chain_uses_named_key_and_keeps_alibaba_identity(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(
        model_config, "_vision_verification_state_path", lambda *_: state_path
    )
    import agent.image_runtime as image_runtime

    monkeypatch.setattr(
        image_runtime,
        "vision_verification_state_path",
        lambda *_args, **_kwargs: state_path,
    )
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "named-probe-secret",
        }
    )
    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "credential_ref": "alibaba-default",
        }
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="TAIJI-VISION-CHECK-7319")
            )
        ]
    )
    fake_client = SimpleNamespace(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=response))
        ),
    )
    routed = []

    def route(provider, model=None, async_mode=False, **kwargs):
        routed.append((provider, model, async_mode, kwargs))
        return fake_client, model

    import agent.auxiliary_client as auxiliary_client

    monkeypatch.setattr(auxiliary_client, "resolve_provider_client", route)

    result = model_config.test_vision_config()

    assert result["status"] == "verified"
    assert result["provider"] == "alibaba"
    assert routed == [
        (
            "alibaba",
            "qwen3-vl-plus",
            True,
            {
                "explicit_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "explicit_api_key": "named-probe-secret",
                "api_mode": "chat_completions",
            },
        )
    ]


@pytest.mark.parametrize("credential_mode", ["named", "legacy"])
@pytest.mark.parametrize("env_state", ["absent", "missing_key"])
def test_vision_probe_exact_profile_never_falls_back_to_process_secret(
    monkeypatch,
    tmp_path,
    credential_mode,
    env_state,
):
    """Exact B vision config without a B key must perform zero provider I/O."""
    import tools.vision_tools as vision_tools

    profile_b = tmp_path / "profile-b"
    profile_b.mkdir()
    config_path = profile_b / "profile-specific.yaml"
    credential_ref = "shared-alibaba-vision"
    secret_env = model_config.credential_secret_env(credential_ref)
    vision_cfg = {
        "provider": "alibaba",
        "model": "qwen3-vl-plus",
        "endpoint_mode": "public",
        "region": "cn-beijing",
    }
    config = {"auxiliary": {"vision": vision_cfg}}
    if credential_mode == "named":
        config["provider_credentials"] = [
            {
                "id": credential_ref,
                "provider_family": "alibaba_dashscope",
                "label": "Shared Alibaba vision",
                "auth_type": "api_key",
                "secret_env": secret_env,
            }
        ]
        vision_cfg["credential_ref"] = credential_ref
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    if env_state == "missing_key":
        (profile_b / ".env").write_text(
            "UNRELATED_TEST_VALUE=present\n",
            encoding="utf-8",
        )
    monkeypatch.setenv(secret_env, "process-profile-a-named-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "process-profile-a-legacy-secret")
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(api_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: profile_b)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "B")
    monkeypatch.setattr(
        model_config,
        "_vision_verification_state_path",
        lambda *_: profile_b / "vision-verification.json",
    )
    monkeypatch.setenv("HERMES_PROFILE_NAME", "B")
    outbound_calls = []

    async def fake_probe(**kwargs):
        outbound_calls.append(kwargs)
        return json.dumps(
            {
                "success": True,
                "analysis": "TAIJI-VISION-CHECK-7319",
                "resolved_provider": "alibaba",
                "resolved_model": "qwen3-vl-plus",
            }
        )

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", fake_probe)

    snapshot = model_config._capture_vision_config_snapshot()
    from agent.image_runtime import current_vision_runtime_snapshot
    from hermes_constants import (
        reset_hermes_config_path_override,
        reset_hermes_home_override,
        set_hermes_config_path_override,
        set_hermes_home_override,
    )

    home_token = set_hermes_home_override(profile_b)
    config_token = set_hermes_config_path_override(config_path)
    try:
        runtime_snapshot = current_vision_runtime_snapshot()
    finally:
        reset_hermes_config_path_override(config_token)
        reset_hermes_home_override(home_token)
    public_config = model_config.get_vision_config()
    result = model_config.test_vision_config()

    assert snapshot.configured is False
    assert runtime_snapshot["configured"] is False
    assert runtime_snapshot["available"] is False
    assert runtime_snapshot["fingerprint"] == snapshot.fingerprint
    assert public_config["vision"]["key_status"]["configured"] is False
    assert public_config["vision"]["verification"]["status"] == "unconfigured"
    active_row = next(
        row
        for row in public_config["providers"]
        if row["id"] == "alibaba"
    )
    assert active_row["available"] is False
    assert result["status"] == "unconfigured"
    assert outbound_calls == []


@pytest.mark.parametrize("credential_mode", ["named", "legacy"])
@pytest.mark.parametrize("env_state", ["absent", "missing_key"])
def test_custom_vision_public_projection_never_borrows_process_secret(
    monkeypatch,
    tmp_path,
    credential_mode,
    env_state,
):
    import tools.vision_tools as vision_tools
    from agent.custom_vision_providers import custom_vision_provider_env_var

    profile_b = tmp_path / "custom-profile-b"
    profile_b.mkdir()
    config_path = profile_b / "profile-specific.yaml"
    credential_ref = "shared-custom-vision"
    named_secret_env = model_config.credential_secret_env(credential_ref)
    legacy_secret_env = custom_vision_provider_env_var("router")
    entry = {
        "id": "router",
        "name": "Router Vision",
        "base_url": "https://vision-b.example.test/v1",
        "models": ["router-vl"],
        "default_model": "router-vl",
        "transport": "openai_chat_completions",
    }
    config = {
        "auxiliary": {
            "vision": {
                "provider": "custom:router",
                "model": "router-vl",
            }
        },
        "custom_vision_providers": [entry],
    }
    if credential_mode == "named":
        config["provider_credentials"] = [
            {
                "id": credential_ref,
                "provider_family": "custom",
                "label": "Shared custom vision",
                "auth_type": "api_key",
                "secret_env": named_secret_env,
            }
        ]
        entry["credential_ref"] = credential_ref
    else:
        entry["api_key_env"] = legacy_secret_env
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    if env_state == "missing_key":
        (profile_b / ".env").write_text(
            "UNRELATED_TEST_VALUE=present\n",
            encoding="utf-8",
        )
    monkeypatch.setenv(
        named_secret_env,
        "process-profile-a-custom-named-secret",
    )
    monkeypatch.setenv(
        legacy_secret_env,
        "process-profile-a-custom-legacy-secret",
    )
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(api_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: profile_b)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "B")
    monkeypatch.setattr(
        model_config,
        "_vision_verification_state_path",
        lambda *_: profile_b / "vision-verification.json",
    )
    outbound_calls = []

    async def fake_probe(**kwargs):
        outbound_calls.append(kwargs)
        return json.dumps(
            {
                "success": True,
                "analysis": "TAIJI-VISION-CHECK-7319",
                "resolved_provider": "custom:router",
                "resolved_model": "router-vl",
            }
        )

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", fake_probe)

    snapshot = model_config._capture_vision_config_snapshot()
    public_config = model_config.get_vision_config()
    result = model_config.test_vision_config()

    assert snapshot.configured is False
    assert public_config["vision"]["key_status"]["configured"] is False
    assert public_config["vision"]["verification"]["status"] == "unconfigured"
    active_row = next(
        row
        for row in public_config["providers"]
        if row["id"] == "custom:router"
    )
    assert active_row["available"] is False
    assert result["status"] == "unconfigured"
    assert outbound_calls == []


def test_vision_snapshot_fingerprint_tracks_the_final_bound_endpoint(
    monkeypatch,
    tmp_path,
):
    from agent.image_runtime import current_vision_runtime_snapshot

    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DASHSCOPE_API_KEY=alibaba-profile-secret",
                "GLM_API_KEY=zai-profile-secret",
                "AUXILIARY_VISION_API_KEY=custom-profile-secret",
                (
                    "TAIJI_VISION_CUSTOM_ROUTER_API_KEY="
                    "custom-router-profile-secret"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )

    def snapshot_for(vision_cfg):
        (tmp_path / "config.yaml").write_text(
            yaml.safe_dump(
                {"auxiliary": {"vision": vision_cfg}},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return model_config._capture_vision_config_snapshot()

    alibaba_a = snapshot_for(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "endpoint_mode": "public",
            "region": "cn-beijing",
            "base_url": "https://stale-a.example.test/v1",
            "api_mode": "stale-wire-a",
        }
    )
    alibaba_b = snapshot_for(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "endpoint_mode": "public",
            "region": "cn-beijing",
            "base_url": "https://stale-b.example.test/v1",
            "api_mode": "stale-wire-b",
        }
    )
    assert alibaba_a.binding is not None
    assert alibaba_b.binding is not None
    assert alibaba_a.binding.base_url == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert alibaba_b.binding.base_url == alibaba_a.binding.base_url
    assert alibaba_b.fingerprint == alibaba_a.fingerprint
    alibaba_runtime = current_vision_runtime_snapshot()
    assert alibaba_runtime["base_url"] == alibaba_b.binding.base_url
    assert alibaba_runtime["fingerprint"] == alibaba_b.fingerprint

    alibaba_custom_a = snapshot_for(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "endpoint_mode": "custom",
            "region": "cn-beijing",
            "base_url": "https://alibaba-custom-a.example.test/v1",
        }
    )
    alibaba_custom_b = snapshot_for(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "endpoint_mode": "custom",
            "region": "cn-beijing",
            "base_url": "https://alibaba-custom-b.example.test/v1",
        }
    )
    assert alibaba_custom_a.binding is not None
    assert alibaba_custom_b.binding is not None
    assert (
        alibaba_custom_a.binding.base_url
        != alibaba_custom_b.binding.base_url
    )
    assert (
        alibaba_custom_a.fingerprint
        != alibaba_custom_b.fingerprint
    )
    alibaba_custom_runtime = current_vision_runtime_snapshot()
    assert (
        alibaba_custom_runtime["base_url"]
        == alibaba_custom_b.binding.base_url
    )
    assert (
        alibaba_custom_runtime["fingerprint"]
        == alibaba_custom_b.fingerprint
    )

    zai_a = snapshot_for(
        {
            "provider": "zai",
            "model": "glm-5v-turbo",
            "base_url": "https://stale-a.example.test/v1",
            "api_mode": "stale-wire-a",
        }
    )
    zai_b = snapshot_for(
        {
            "provider": "zai",
            "model": "glm-5v-turbo",
            "base_url": "https://stale-b.example.test/v1",
            "api_mode": "stale-wire-b",
        }
    )
    assert zai_a.binding is not None
    assert zai_b.binding is not None
    assert zai_a.binding.base_url == (
        "https://open.bigmodel.cn/api/paas/v4"
    )
    assert zai_b.binding.base_url == zai_a.binding.base_url
    assert zai_b.fingerprint == zai_a.fingerprint
    zai_runtime = current_vision_runtime_snapshot()
    assert zai_runtime["base_url"] == zai_b.binding.base_url
    assert zai_runtime["fingerprint"] == zai_b.fingerprint

    custom_a = snapshot_for(
        {
            "provider": "custom",
            "model": "private-vl",
            "base_url": "https://custom-a.example.test/v1",
        }
    )
    custom_b = snapshot_for(
        {
            "provider": "custom",
            "model": "private-vl",
            "base_url": "https://custom-b.example.test/v1",
        }
    )
    assert custom_a.binding is not None
    assert custom_b.binding is not None
    assert custom_a.binding.base_url != custom_b.binding.base_url
    assert custom_a.fingerprint != custom_b.fingerprint
    custom_runtime = current_vision_runtime_snapshot()
    assert custom_runtime["base_url"] == custom_b.binding.base_url
    assert custom_runtime["fingerprint"] == custom_b.fingerprint

    custom_anthropic = snapshot_for(
        {
            "provider": "custom",
            "model": "private-vl",
            "base_url": "https://custom-b.example.test/v1",
            "api_mode": "anthropic_messages",
        }
    )
    assert custom_anthropic.binding is not None
    assert custom_anthropic.binding.api_mode == "anthropic_messages"
    assert custom_anthropic.fingerprint != custom_b.fingerprint
    custom_anthropic_runtime = current_vision_runtime_snapshot()
    assert (
        custom_anthropic_runtime["transport"]
        == custom_anthropic.binding.api_mode
    )
    assert (
        custom_anthropic_runtime["fingerprint"]
        == custom_anthropic.fingerprint
    )

    config_with_named_custom = {
        "auxiliary": {
            "vision": {
                "provider": "custom:router",
                "model": "router-vl",
            }
        },
        "custom_vision_providers": [
            {
                "id": "router",
                "name": "Router Vision",
                "base_url": "https://router.example.test/v1",
                "api_key_env": "TAIJI_VISION_CUSTOM_ROUTER_API_KEY",
                "models": ["router-vl"],
                "default_model": "router-vl",
                "transport": "anthropic_messages",
            }
        ],
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(config_with_named_custom, sort_keys=False),
        encoding="utf-8",
    )
    named_custom = model_config._capture_vision_config_snapshot()
    assert named_custom.binding is not None
    assert named_custom.binding.api_mode == "anthropic_messages"
    named_custom_runtime = current_vision_runtime_snapshot()
    assert (
        named_custom_runtime["base_url"]
        == named_custom.binding.base_url
    )
    assert named_custom_runtime["transport"] == "anthropic_messages"
    assert (
        named_custom_runtime["fingerprint"]
        == named_custom.fingerprint
    )


def test_vision_test_failure_returns_only_safe_fields(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)

    async def fail(**_kwargs):
        return json.dumps({
            "success": False,
            "error": "401 leaked-test-only-key /private/provider/path",
            "analysis": "raw provider response",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", fail)
    result = model_config.test_vision_config()

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error_code"] == "vision_probe_failed"
    assert result["message"] == "识图验证失败，请检查网络、密钥、模型和账号状态后重试。"
    combined = json.dumps(result, ensure_ascii=False) + state_path.read_text(encoding="utf-8")
    for forbidden in ("leaked-test-only-key", "/private/provider/path", "raw provider response"):
        assert forbidden not in combined


def test_vision_verification_fingerprint_invalidates_when_key_changes(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)

    async def succeed(**_kwargs):
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", succeed)
    assert model_config.test_vision_config()["status"] == "verified"

    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=rotated-test-key\n", encoding="utf-8")

    assert model_config.get_vision_config()["vision"]["verification"]["status"] == "configured_unverified"


def test_vision_verification_fingerprint_invalidates_when_named_key_rotates(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "label": "阿里默认凭据",
            "api_key": "named-key-before",
        }
    )
    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "credential_ref": "alibaba-default",
        }
    )

    async def succeed(**_kwargs):
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", succeed)
    assert model_config.test_vision_config()["status"] == "verified"

    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=named-key-before",
            "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY=named-key-after",
        ),
        encoding="utf-8",
    )

    assert model_config.get_vision_config()["vision"]["verification"]["status"] == "configured_unverified"


def test_saving_vision_config_invalidates_previous_verification(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    monkeypatch.setattr(model_config, "_VISION_PROBE_GENERATIONS", {})
    _write_saved_vision_config(tmp_path)
    model_config._atomic_write_json(
        state_path,
        {
            "schema_version": 1,
            "generation": 5,
            "fingerprint": "previous-vision-fingerprint",
            "status": "verified",
            "checked_at": "2030-01-01T00:00:00Z",
            "error_code": "",
            "message": "previous verified state",
            "diagnostic_id": "previous-vision-diagnostic",
        },
    )

    model_config.set_vision_config({"provider": "alibaba", "model": "qwen3-vl-plus"})

    _assert_verification_tombstone(
        json.loads(state_path.read_text(encoding="utf-8")),
        minimum_generation=6,
        forbidden_fingerprint="previous-vision-fingerprint",
        forbidden_diagnostic_id="previous-vision-diagnostic",
    )


def test_vision_probe_does_not_persist_success_after_key_rotation(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    monkeypatch.setattr(model_config, "_VISION_PROBE_GENERATIONS", {})
    _write_saved_vision_config(tmp_path)
    verifying = {}

    async def rotate_key_during_probe(**_kwargs):
        verifying.update(
            json.loads(state_path.read_text(encoding="utf-8"))
        )
        (tmp_path / ".env").write_text(
            "DASHSCOPE_API_KEY=rotated-during-probe\n", encoding="utf-8"
        )
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", rotate_key_during_probe)
    result = model_config.test_vision_config()

    assert result["ok"] is False
    assert result["status"] == "configured_unverified"
    assert result["error_code"] == "vision_probe_superseded"
    _assert_verification_tombstone(
        json.loads(state_path.read_text(encoding="utf-8")),
        minimum_generation=verifying["generation"] + 1,
        forbidden_fingerprint=verifying["fingerprint"],
        forbidden_diagnostic_id=verifying["diagnostic_id"],
    )
    assert (
        model_config.get_vision_config()["vision"]["verification"]["status"]
        != "verified"
    )


def test_superseded_vision_probe_tombstones_owned_state_when_unlink_fails(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(
        model_config,
        "_vision_verification_state_path",
        lambda *_: state_path,
    )
    monkeypatch.setattr(model_config, "_VISION_PROBE_GENERATIONS", {})
    _write_saved_vision_config(tmp_path)
    original_unlink = Path.unlink
    verifying = {}

    def fail_state_unlink(path, *args, **kwargs):
        if path == state_path:
            raise PermissionError("simulated state unlink failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_state_unlink)

    async def rotate_key_during_probe(**_kwargs):
        verifying.update(
            json.loads(state_path.read_text(encoding="utf-8"))
        )
        (tmp_path / ".env").write_text(
            "DASHSCOPE_API_KEY=rotated-during-probe\n",
            encoding="utf-8",
        )
        return json.dumps(
            {
                "success": True,
                "analysis": "TAIJI-VISION-CHECK-7319",
                "resolved_provider": "alibaba",
                "resolved_model": "qwen3-vl-plus",
            }
        )

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(
        vision_tools,
        "vision_analyze_tool",
        rotate_key_during_probe,
    )

    result = model_config.test_vision_config()

    assert result["error_code"] == "vision_probe_superseded"
    _assert_verification_tombstone(
        json.loads(state_path.read_text(encoding="utf-8")),
        minimum_generation=verifying["generation"] + 1,
        forbidden_fingerprint=verifying["fingerprint"],
        forbidden_diagnostic_id=verifying["diagnostic_id"],
    )
    assert (
        model_config.get_vision_config()["vision"]["verification"]["status"]
        != "verifying"
    )


def test_vision_verification_is_isolated_per_profile(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    active_profile = {"name": "profile-a"}
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: active_profile["name"])
    monkeypatch.setattr(
        model_config,
        "_vision_verification_state_root",
        lambda: tmp_path / "vision-verification",
    )
    _write_saved_vision_config(tmp_path)

    async def succeed(**_kwargs):
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", succeed)
    assert model_config.test_vision_config()["status"] == "verified"
    active_profile["name"] = "profile-b"
    assert model_config.test_vision_config()["status"] == "verified"
    active_profile["name"] = "profile-a"

    assert model_config.get_vision_config()["vision"]["verification"]["status"] == "verified"
    assert len(list((tmp_path / "vision-verification").glob("*.json"))) == 2


def test_vision_probe_does_not_persist_after_profile_switch(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    active_profile = {"name": "profile-a"}
    monkeypatch.setattr(model_config, "_VISION_PROBE_GENERATIONS", {})
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: active_profile["name"])
    monkeypatch.setattr(
        model_config,
        "_vision_verification_state_root",
        lambda: tmp_path / "vision-verification",
    )
    _write_saved_vision_config(tmp_path)
    verifying = {}

    async def switch_profile(**_kwargs):
        state_files = list(
            (tmp_path / "vision-verification").glob("*.json")
        )
        assert len(state_files) == 1
        verifying.update(
            json.loads(state_files[0].read_text(encoding="utf-8"))
        )
        active_profile["name"] = "profile-b"
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", switch_profile)
    result = model_config.test_vision_config()

    assert result["ok"] is False
    assert result["status"] == "configured_unverified"
    assert result["error_code"] == "vision_probe_superseded"
    state_files = list(
        (tmp_path / "vision-verification").glob("*.json")
    )
    assert len(state_files) == 1
    _assert_verification_tombstone(
        json.loads(state_files[0].read_text(encoding="utf-8")),
        minimum_generation=verifying["generation"] + 1,
        forbidden_fingerprint=verifying["fingerprint"],
        forbidden_diagnostic_id=verifying["diagnostic_id"],
    )
    assert (
        model_config.get_vision_config()["vision"]["verification"]["status"]
        != "verified"
    )


def test_vision_probe_rejects_success_from_wrong_resolved_backend(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)

    async def fallback_success(**_kwargs):
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "openrouter",
            "resolved_model": "backup-vision",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", fallback_success)
    result = model_config.test_vision_config()

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error_code"] == "vision_probe_failed"


def test_newer_vision_probe_prevents_older_request_overwrite(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(model_config, "_vision_verification_state_path", lambda *_: state_path)
    _write_saved_vision_config(tmp_path)
    first_started = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    call_count = {"value": 0}

    async def ordered_probe(**_kwargs):
        with call_lock:
            call_count["value"] += 1
            call_number = call_count["value"]
        if call_number == 1:
            first_started.set()
            assert release_first.wait(timeout=5)
            return json.dumps({
                "success": False,
                "error": "old failure",
                "analysis": "old failure",
                "resolved_provider": "alibaba",
                "resolved_model": "qwen3-vl-plus",
            })
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", ordered_probe)
    results = {}
    first = threading.Thread(
        target=lambda: results.setdefault("first", model_config.test_vision_config())
    )
    first.start()
    assert first_started.wait(timeout=5)
    results["second"] = model_config.test_vision_config()
    release_first.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert results["second"]["status"] == "verified"
    assert results["first"]["status"] == "configured_unverified"
    assert results["first"]["error_code"] == "vision_probe_superseded"
    assert model_config.get_vision_config()["vision"]["verification"]["status"] == "verified"


def test_vision_probe_persists_generation_matched_verifying_before_provider(
    monkeypatch,
    tmp_path,
):
    """The visible in-flight state and final state must belong to one generation."""
    _use_home(monkeypatch, tmp_path)
    state_path = tmp_path / "vision-verification.json"
    monkeypatch.setattr(
        model_config,
        "_vision_verification_state_path",
        lambda *_: state_path,
    )
    _write_saved_vision_config(tmp_path)
    first_started = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    call_count = {"value": 0}

    async def ordered_probe(**_kwargs):
        with call_lock:
            call_count["value"] += 1
            call_number = call_count["value"]
        if call_number == 1:
            first_started.set()
            assert release_first.wait(timeout=5)
            return json.dumps({
                "success": False,
                "error": "old failure",
                "analysis": "old failure",
                "resolved_provider": "alibaba",
                "resolved_model": "qwen3-vl-plus",
            })
        return json.dumps({
            "success": True,
            "analysis": "TAIJI-VISION-CHECK-7319",
            "resolved_provider": "alibaba",
            "resolved_model": "qwen3-vl-plus",
        })

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", ordered_probe)
    results = {}
    first = threading.Thread(
        target=lambda: results.setdefault("first", model_config.test_vision_config())
    )
    first.start()
    assert first_started.wait(timeout=5)
    try:
        verifying_state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        verifying_state = {}

    results["second"] = model_config.test_vision_config()
    state_after_second = json.loads(state_path.read_text(encoding="utf-8"))
    release_first.set()
    first.join(timeout=5)
    final_state = json.loads(state_path.read_text(encoding="utf-8"))

    violations = []
    if verifying_state.get("status") != "verifying":
        violations.append("vision probe did not persist verifying before Provider call")
    if verifying_state.get("schema_version") != model_config.CAPABILITY_VERIFICATION_SCHEMA_VERSION:
        violations.append("vision verifying state used the wrong schema")
    if not verifying_state.get("fingerprint"):
        violations.append("vision verifying state omitted its runtime fingerprint")
    if verifying_state.get("diagnostic_id") != results["first"]["diagnostic_id"]:
        violations.append("vision verifying state did not match the first generation")
    if state_after_second.get("status") != "verified":
        violations.append("newer vision probe did not persist its verified result")
    if state_after_second.get("diagnostic_id") != results["second"]["diagnostic_id"]:
        violations.append("newer vision final state did not match its generation")
    if final_state != state_after_second:
        violations.append("superseded vision result overwrote the newer final state")
    if results["first"]["error_code"] != "vision_probe_superseded":
        violations.append("older vision probe was not reported as superseded")
    if first.is_alive():
        violations.append("older vision probe thread did not finish")

    assert violations == [], "; ".join(violations)


def test_vision_snapshot_uses_one_environment_generation(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path)
    env_name = "B3_VISION_GENERATION"
    monkeypatch.setenv(env_name, "a")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "auxiliary": {
                    "vision": {
                        "provider": "custom:router",
                        "model": f"model-${{{env_name}}}",
                    }
                },
                "custom_vision_providers": [
                    {
                        "id": "router",
                        "name": "Router",
                        "base_url": (
                            f"https://${{{env_name}}}.example.test/v1"
                        ),
                        "api_key_env": "TAIJI_VISION_CUSTOM_ROUTER_API_KEY",
                        "models": [f"model-${{{env_name}}}"],
                        "default_model": f"model-${{{env_name}}}",
                        "transport": "openai_chat_completions",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "TAIJI_VISION_CUSTOM_ROUTER_API_KEY=profile-key\n",
        encoding="utf-8",
    )
    from hermes_cli import config as hermes_config

    original_expand = hermes_config._expand_env_vars
    calls = {"value": 0}
    depth = {"value": 0}

    def mutate_after_first_expand(value, **kwargs):
        depth["value"] += 1
        try:
            return original_expand(value, **kwargs)
        finally:
            depth["value"] -= 1
            if depth["value"] == 0:
                calls["value"] += 1
                if calls["value"] == 1:
                    monkeypatch.setenv(env_name, "b")

    monkeypatch.setattr(
        hermes_config,
        "_expand_env_vars",
        mutate_after_first_expand,
    )

    snapshot = model_config._capture_vision_config_snapshot()

    assert calls["value"] == 1
    assert snapshot.model == "model-a"
    assert snapshot.base_url == "https://a.example.test/v1"
    assert snapshot.binding is not None
    assert snapshot.binding.model == "model-a"
    assert snapshot.binding.base_url == "https://a.example.test/v1"
    # The capture freezes endpoint and secret material, but must remain
    # unsealed until the unique ``verifying`` state is persisted.
    assert snapshot.binding.authorization_fingerprint == ""
    assert snapshot.binding.authorization_generation == ""
    assert snapshot.configured is True


def test_unsupported_image_identity_is_fail_closed_in_webui_and_agent(
    monkeypatch,
    tmp_path,
):
    """Both runtimes must apply the same effective-runtime resolution gate."""
    from agent.image_gen_verification import (
        CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        image_gen_fingerprint,
        read_image_gen_verification_snapshot,
        verification_state_path,
    )

    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setenv("XAI_API_KEY", "xai-secret")
    config_data = {
        "image_gen": {
            "provider": "xai",
            "model": "legacy-top-level-model",
            "xai": {
                "model": "grok-imagine-image",
                "resolution": "1k",
            },
        },
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(config_data),
        encoding="utf-8",
    )
    fingerprint = image_gen_fingerprint(
        config_data["image_gen"],
        profile="default",
        config_data=config_data,
        secret_value="xai-secret",
    )
    state_path = verification_state_path(tmp_path, "default")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
                "fingerprint": fingerprint,
                "status": "verified",
                "checked_at": "2030-01-01T00:00:00Z",
                "diagnostic_id": "unsupported-xai-state",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_: state_path,
    )

    webui_snapshot = model_config._capture_image_gen_config_snapshot()
    webui_public = model_config._public_image_gen_verification(
        config_data["image_gen"],
        profile="default",
    )
    agent_snapshot = read_image_gen_verification_snapshot(
        config_data["image_gen"],
        profile="default",
        config_data=config_data,
        secret_value="xai-secret",
        state_root=tmp_path,
    )

    assert webui_snapshot.effective_config_resolved is False
    assert agent_snapshot["effective_config_resolved"] is False
    assert webui_public["status"] == agent_snapshot["status"]
    assert webui_public["status"] == "configured_unverified"


def test_webui_image_snapshot_uses_one_env_generation(monkeypatch, tmp_path):
    """WebUI capture must not combine values from two process-env generations."""
    from agent.image_gen_verification import image_gen_fingerprint
    from hermes_cli import config as config_module

    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    base_env = "B3_GAP5_WEBUI_BASE_URL"
    model_env = "B3_GAP5_WEBUI_MODEL"
    base_before = "https://before.example.test/v1"
    base_after = "https://after.example.test/v1"
    model_before = "image-before"
    model_after = "image-after"
    credential_ref = "gap5-custom-router"
    secret_env = model_config.credential_secret_env(credential_ref)
    raw_config = {
        "provider_credentials": [
            {
                "id": credential_ref,
                "provider_family": "custom",
                "label": "Gap5 custom router",
                "auth_type": "api_key",
                "secret_env": secret_env,
            }
        ],
        "custom_image_providers": [
            {
                "base_url": f"${{{base_env}}}",
                "credential_ref": credential_ref,
                "id": "router",
                "name": "Router Images",
                "models": [f"${{{model_env}}}"],
                "default_model": f"${{{model_env}}}",
            }
        ],
        "image_gen": {
            "provider": "custom:router",
            "model": f"${{{model_env}}}",
        },
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(raw_config, sort_keys=False),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        f"{secret_env}=custom-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(secret_env, "custom-secret")
    monkeypatch.setenv(base_env, base_before)
    monkeypatch.setenv(model_env, model_before)
    expected_before = image_gen_fingerprint(
        raw_config["image_gen"],
        profile="default",
        config_data=raw_config,
        secret_value="custom-secret",
    )
    monkeypatch.setenv(base_env, base_after)
    monkeypatch.setenv(model_env, model_after)
    expected_after = image_gen_fingerprint(
        raw_config["image_gen"],
        profile="default",
        config_data=raw_config,
        secret_value="custom-secret",
    )
    assert expected_before != expected_after
    monkeypatch.setenv(base_env, base_before)
    monkeypatch.setenv(model_env, model_before)

    original_expand = config_module._expand_env_vars
    switched = {"value": False}

    def racing_expand(value, *args, **kwargs):
        expanded = original_expand(value, *args, **kwargs)
        if value == f"${{{base_env}}}" and not switched["value"]:
            switched["value"] = True
            monkeypatch.setenv(base_env, base_after)
            monkeypatch.setenv(model_env, model_after)
        return expanded

    monkeypatch.setattr(config_module, "_expand_env_vars", racing_expand)

    original_secret_resolver = model_config._image_gen_secret_value
    secret_resolver_calls = []

    def one_generation_secret(*args, **kwargs):
        secret_resolver_calls.append((args, kwargs))
        if len(secret_resolver_calls) > 1:
            raise AssertionError("image snapshot re-read its secret")
        return original_secret_resolver(*args, **kwargs)

    monkeypatch.setattr(
        model_config,
        "_image_gen_secret_value",
        one_generation_secret,
    )

    snapshot = model_config._capture_image_gen_config_snapshot()

    assert switched["value"] is True
    assert len(secret_resolver_calls) == 1
    assert snapshot.effective_config_resolved is True
    assert snapshot.model == model_before
    assert snapshot.fingerprint == expected_before
    assert snapshot.fingerprint != expected_after
    assert snapshot.probe_binding is not None
    # Capture freezes private Provider material only.  Authorization cannot be
    # sealed until test_image_gen_config() has durably written its unique
    # ``verifying`` generation.
    assert snapshot.probe_binding.authorization_fingerprint == ""
    assert snapshot.probe_binding.authorization_generation == ""


def test_image_probe_uses_exact_profile_config_path_for_real_dispatch(
    monkeypatch,
    tmp_path,
):
    """A B-profile probe cannot execute with A-profile endpoint or secret."""
    import sys

    profile_a = tmp_path / "profile-a"
    profile_b = tmp_path / "profile-b"
    profile_a.mkdir()
    profile_b.mkdir()
    credential_ref = "shared-dashscope"
    secret_env = model_config.credential_secret_env(credential_ref)
    endpoint_a = (
        "https://profile-a.example.test/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )
    endpoint_b = (
        "https://profile-b.example.test/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )

    def write_profile(root: Path, endpoint: str, secret: str) -> Path:
        config_path = root / "profile-specific.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "provider_credentials": [
                        {
                            "id": credential_ref,
                            "provider_family": "alibaba_dashscope",
                            "label": "Shared DashScope",
                            "auth_type": "api_key",
                            "secret_env": secret_env,
                        }
                    ],
                    "image_gen": {
                        "provider": "dashscope",
                        "model": "qwen-image-2.0-pro",
                        "credential_ref": credential_ref,
                        "options": {
                            "endpoint_mode": "custom",
                            "base_url": endpoint,
                        },
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (root / ".env").write_text(
            f"{secret_env}={secret}\n",
            encoding="utf-8",
        )
        return config_path

    path_a = write_profile(profile_a, endpoint_a, "profile-a-secret")
    path_b = write_profile(profile_b, endpoint_b, "profile-b-secret")
    monkeypatch.setenv("HERMES_HOME", str(profile_a))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(path_a))
    monkeypatch.setattr(model_config, "_get_config_path", lambda: path_b)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: profile_b)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "B")
    state_path = profile_b / "image-verification.json"
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_: state_path,
    )
    model_config._ensure_image_gen_plugins_registered()
    from agent.image_gen_registry import get_provider

    selected = get_provider("dashscope")
    assert selected is not None
    dashscope = sys.modules[type(selected).__module__]
    captured = {}

    def fake_post_json(**kwargs):
        captured["url"] = kwargs["url"]
        captured["authorization"] = kwargs["headers"]["Authorization"]
        return {"data": [{"url": "https://cdn.example.test/probe.png"}]}, None

    def fake_cached_success(**kwargs):
        from hermes_constants import get_hermes_home

        provider_home = get_hermes_home()
        captured["provider_home"] = provider_home
        image_path = (
            provider_home / "cache" / "images" / "gap7-probe.png"
        )
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(model_config._VISION_PROBE_PNG)
        return {
            "success": True,
            "image": str(image_path),
            "provider": kwargs["provider"],
            "model": kwargs["model"],
        }

    monkeypatch.setattr(dashscope, "post_json", fake_post_json)
    monkeypatch.setattr(dashscope, "cached_success", fake_cached_success)
    monkeypatch.setattr(dashscope, "is_safe_url", lambda *_: True)

    result = model_config.test_image_gen_config()

    assert result["status"] == "verified"
    assert captured["url"] == endpoint_b
    assert captured["authorization"] == "Bearer profile-b-secret"
    assert captured["provider_home"] == profile_b


@pytest.mark.parametrize("credential_mode", ["named", "legacy"])
@pytest.mark.parametrize("env_state", ["absent", "missing_key"])
def test_image_probe_exact_profile_never_falls_back_to_process_secret(
    monkeypatch,
    tmp_path,
    credential_mode,
    env_state,
):
    """An exact B profile with no B key must not probe using process A."""
    import sys

    profile_b = tmp_path / "profile-b"
    profile_b.mkdir()
    config_path = profile_b / "profile-specific.yaml"
    endpoint = (
        "https://profile-b.example.test/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )
    credential_ref = "shared-dashscope"
    secret_env = model_config.credential_secret_env(credential_ref)
    config = {
        "image_gen": {
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "options": {
                "endpoint_mode": "custom",
                "base_url": endpoint,
            },
        }
    }
    if credential_mode == "named":
        config["provider_credentials"] = [
            {
                "id": credential_ref,
                "provider_family": "alibaba_dashscope",
                "label": "Shared DashScope",
                "auth_type": "api_key",
                "secret_env": secret_env,
            }
        ]
        config["image_gen"]["credential_ref"] = credential_ref
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    if env_state == "missing_key":
        (profile_b / ".env").write_text(
            "UNRELATED_TEST_VALUE=present\n",
            encoding="utf-8",
        )
    monkeypatch.setenv(secret_env, "process-profile-a-named-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "process-profile-a-legacy-secret")
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: profile_b)
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: "B")
    model_config._ensure_image_gen_plugins_registered()
    from agent.image_gen_registry import get_provider

    selected = get_provider("dashscope")
    assert selected is not None
    dashscope = sys.modules[type(selected).__module__]
    outbound_calls = []

    def fake_post_json(**kwargs):
        outbound_calls.append(kwargs)
        return {"data": [{"url": "https://cdn.example.test/probe.png"}]}, None

    def fake_cached_success(**kwargs):
        image_path = profile_b / "cache" / "images" / "gap7d-probe.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(model_config._VISION_PROBE_PNG)
        return {
            "success": True,
            "image": str(image_path),
            "provider": kwargs["provider"],
            "model": kwargs["model"],
        }

    monkeypatch.setattr(dashscope, "post_json", fake_post_json)
    monkeypatch.setattr(dashscope, "cached_success", fake_cached_success)
    monkeypatch.setattr(dashscope, "is_safe_url", lambda *_: True)

    snapshot = model_config._capture_image_gen_config_snapshot()
    result = model_config.test_image_gen_config()

    assert snapshot.configured is False
    assert result["status"] == "unconfigured"
    assert outbound_calls == []


def test_concurrent_custom_image_probes_use_request_local_provider_identity(
    monkeypatch,
    tmp_path,
):
    """Same custom ID in concurrent profiles must keep each profile's URL/key/model."""
    import agent.custom_image_providers as custom_image_providers
    from agent.image_gen_verification import verification_state_path

    profiles = {}
    credential_ref = "shared-custom-image"
    secret_env = model_config.credential_secret_env(credential_ref)
    profiles_root = tmp_path / "profiles"
    profiles_root.mkdir()
    state_root = tmp_path / "states" / "image-gen-verification"
    monkeypatch.setenv("TAIJI_WEBUI_STATE_DIR", str(tmp_path / "states"))
    monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path / "states"))
    for name in ("A", "B"):
        home = profiles_root / name
        home.mkdir()
        model = f"image-model-{name.lower()}"
        base_url = f"https://profile-{name.lower()}.example.test/v1"
        config_path = home / f"profile-{name.lower()}.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "provider_credentials": [
                        {
                            "id": credential_ref,
                            "provider_family": "custom",
                            "label": "Shared custom image",
                            "auth_type": "api_key",
                            "secret_env": secret_env,
                        }
                    ],
                    "image_gen": {
                        "provider": "custom:router",
                        "model": model,
                    },
                    "custom_image_providers": [
                        {
                            "id": "router",
                            "name": f"Router {name}",
                            "base_url": base_url,
                            "credential_ref": credential_ref,
                            "models": [
                                "image-model-a",
                                "image-model-b",
                            ],
                            "default_model": model,
                            "network_scope": "public_direct",
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (home / ".env").write_text(
            f"{secret_env}=profile-{name.lower()}-secret\n",
            encoding="utf-8",
        )
        profiles[name] = {
            "home": home,
            "config_path": config_path,
            "model": model,
            "endpoint": f"{base_url}/images/generations",
            "authorization": f"Bearer profile-{name.lower()}-secret",
        }

    runtime = threading.local()
    monkeypatch.setattr(
        model_config,
        "_get_config_path",
        lambda: runtime.config_path,
    )
    monkeypatch.setattr(
        model_config,
        "_get_hermes_home",
        lambda: runtime.home,
    )
    monkeypatch.setattr(
        model_config,
        "_active_profile_name",
        lambda: runtime.profile,
    )
    monkeypatch.setattr(model_config, "reload_config", lambda: None)
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda profile=None: verification_state_path(
            state_root,
            profile or runtime.profile,
        ),
    )
    register_barrier = threading.Barrier(2)
    original_register = (
        custom_image_providers.register_configured_custom_image_providers
    )

    def racing_register(*args, **kwargs):
        original_register(*args, **kwargs)
        register_barrier.wait(timeout=5)

    monkeypatch.setattr(
        custom_image_providers,
        "register_configured_custom_image_providers",
        racing_register,
    )
    requests = {}

    @contextmanager
    def fake_request_pinned_https(**kwargs):
        requests[threading.current_thread().name] = {
            "url": kwargs["url"],
            "authorization": kwargs["headers"]["Authorization"],
            "model": kwargs["json_body"]["model"],
        }
        yield SimpleNamespace(status_code=200)

    monkeypatch.setattr(
        custom_image_providers,
        "request_pinned_https",
        fake_request_pinned_https,
    )
    encoded_probe = base64.b64encode(model_config._VISION_PROBE_PNG).decode(
        "ascii"
    )
    monkeypatch.setattr(
        custom_image_providers,
        "read_bounded_json",
        lambda _response: {"data": [{"b64_json": encoded_probe}]},
    )
    results = {}

    def worker(profile: str):
        from hermes_constants import (
            reset_hermes_config_path_override,
            reset_hermes_home_override,
            set_hermes_config_path_override,
            set_hermes_home_override,
        )

        runtime.profile = profile
        runtime.home = profiles[profile]["home"]
        runtime.config_path = profiles[profile]["config_path"]
        home_token = set_hermes_home_override(runtime.home)
        config_token = set_hermes_config_path_override(runtime.config_path)
        try:
            results[profile] = model_config.test_image_gen_config()
        finally:
            reset_hermes_config_path_override(config_token)
            reset_hermes_home_override(home_token)

    first = threading.Thread(target=worker, args=("A",), name="A")
    second = threading.Thread(target=worker, args=("B",), name="B")
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results["A"]["status"] == "verified"
    assert results["B"]["status"] == "verified"
    assert requests == {
        profile: {
            "url": profiles[profile]["endpoint"],
            "authorization": profiles[profile]["authorization"],
            "model": profiles[profile]["model"],
        }
        for profile in ("A", "B")
    }


def test_custom_image_provider_config_writes_secret_to_env_and_redacts(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.delenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", raising=False)
    monkeypatch.setattr(model_config, "_image_gen_provider_rows", lambda active: [])

    result = model_config.set_custom_image_provider_config(
        {
            "id": "router",
            "name": "Router Images",
            "base_url": "https://images.example.com/v1/",
            "models": ["gpt-image-custom"],
            "default_model": "gpt-image-custom",
            "allow_custom_model_id": "false",
            "api_key": "router-secret-key-123456",
            "timeout_seconds": 45,
        }
    )

    cfg = _read_config(tmp_path)
    provider = cfg["custom_image_providers"][0]
    credential_ref = provider.pop("credential_ref")
    assert provider == {
        "id": "router",
        "name": "Router Images",
        "base_url": "https://images.example.com/v1",
        "allow_custom_model_id": False,
        "models": ["gpt-image-custom"],
        "default_model": "gpt-image-custom",
        "size_map": {
            "landscape": "1536x1024",
            "square": "1024x1024",
            "portrait": "1024x1536",
        },
        "response_format": "auto",
        "timeout_seconds": 45,
        "network_scope": "public_direct",
        "trusted_proxy_profile": "",
    }
    credential = next(
        row for row in cfg["provider_credentials"] if row["id"] == credential_ref
    )
    canonical_env = model_config.credential_secret_env(credential_ref)
    assert credential["provider_family"] == "custom"
    assert credential["auth_type"] == "api_key"
    assert credential["secret_env"] == canonical_env
    assert credential["managed_by"] == "hermes-webui"
    assert credential["source_capability"] == "custom_image_provider"
    assert credential["source_provider_id"] == "router"
    assert f"{canonical_env}=router-secret-key-123456" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    dumped = json.dumps(result, ensure_ascii=False)
    assert "router-secret-key-123456" not in dumped
    assert result["provider"]["id"] == "custom:router"
    assert result["provider"]["key_status"]["env_var"] == canonical_env
    assert result["provider"]["base_url"] == "https://images.example.com/v1"
    assert result["provider"]["size_map"]["square"] == "1024x1024"
    assert result["provider"]["allow_custom_model_id"] is False
    os.environ.pop(canonical_env, None)


@pytest.mark.parametrize(
    "getter_name",
    (
        "get_custom_vision_provider_configs",
        "get_custom_image_provider_configs",
    ),
)
def test_custom_provider_get_captures_active_config_path_once(
    monkeypatch,
    tmp_path,
    getter_name,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    config_path = tmp_path / "selected" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text("{}\n", encoding="utf-8")
    lookups = 0

    def one_lookup():
        nonlocal lookups
        lookups += 1
        if lookups > 1:
            raise AssertionError("active config path was resolved more than once")
        return config_path

    monkeypatch.setattr(model_config, "_get_config_path", one_lookup)

    result = getattr(model_config, getter_name)()

    assert result == {"ok": True, "providers": []}
    assert lookups == 1


@pytest.mark.parametrize(
    ("capability", "setter_name", "payload"),
    (
        (
            "image",
            "set_custom_image_provider_config",
            {
                "id": "router",
                "name": "Router Images",
                "base_url": "https://images.example.com/v1",
                "models": ["router-image"],
                "default_model": "router-image",
                "api_key": "selected-image-secret",
            },
        ),
        (
            "vision",
            "set_custom_vision_provider_config",
            {
                "id": "router",
                "name": "Router Vision",
                "base_url": "https://vision.example.com/v1",
                "models": ["router-vision"],
                "default_model": "router-vision",
                "transport": "openai_chat_completions",
                "api_key": "selected-vision-secret",
            },
        ),
    ),
)
def test_custom_provider_set_uses_captured_config_parent_for_env(
    monkeypatch,
    tmp_path,
    capability,
    setter_name,
    payload,
):
    selected_home = tmp_path / "selected"
    unrelated_home = tmp_path / "unrelated"
    selected_home.mkdir()
    unrelated_home.mkdir()
    config_path = selected_home / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    _use_home(monkeypatch, unrelated_home, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    if capability == "vision":
        monkeypatch.setattr(
            "agent.custom_vision_providers.is_custom_vision_base_url_safe",
            lambda _url: True,
        )
    getattr(model_config, setter_name)(payload)

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    entry = saved[f"custom_{capability}_providers"][0]
    secret_env = model_config.credential_secret_env(entry["credential_ref"])
    env_text = (selected_home / ".env").read_text(encoding="utf-8")
    assert f"{secret_env}={payload['api_key']}" in env_text
    assert not (unrelated_home / ".env").exists()
    os.environ.pop(secret_env, None)


@pytest.mark.parametrize(
    ("capability", "deleter_name", "secret_env"),
    (
        (
            "image",
            "delete_custom_image_provider_config",
            "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
        ),
        (
            "vision",
            "delete_custom_vision_provider_config",
            "TAIJI_VISION_CUSTOM_ROUTER_API_KEY",
        ),
    ),
)
def test_custom_provider_delete_uses_captured_config_parent_for_env(
    monkeypatch,
    tmp_path,
    capability,
    deleter_name,
    secret_env,
):
    selected_home = tmp_path / "selected"
    unrelated_home = tmp_path / "unrelated"
    selected_home.mkdir()
    unrelated_home.mkdir()
    config_path = selected_home / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                f"custom_{capability}_providers": [
                    {
                        "id": "router",
                        "api_key_env": secret_env,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (selected_home / ".env").write_text(
        f"{secret_env}=selected-secret\n",
        encoding="utf-8",
    )
    _use_home(monkeypatch, unrelated_home, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: config_path)
    getattr(model_config, deleter_name)("router")

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    assert saved[f"custom_{capability}_providers"] == []
    assert secret_env not in (selected_home / ".env").read_text(encoding="utf-8")
    assert not (unrelated_home / ".env").exists()


def test_custom_image_provider_rejects_insecure_http_without_persisting(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)

    with pytest.raises(ValueError, match="HTTPS"):
        model_config.set_custom_image_provider_config(
            {
                "id": "router",
                "name": "Router Images",
                "base_url": "http://images.example.com/v1",
                "models": ["gpt-image-custom"],
                "api_key": "must-not-be-written",
            }
        )

    env_path = tmp_path / ".env"
    assert not env_path.exists() or "must-not-be-written" not in env_path.read_text(encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    assert not config_path.exists() or _read_config(tmp_path).get("custom_image_providers") in (None, [])


@pytest.mark.parametrize(
    "unsafe_yaml",
    [
        "model: [\n",
        "model:\n  provider: first\nmodel:\n  provider: second\n",
    ],
)
@pytest.mark.parametrize("capability", ["image", "vision"])
def test_custom_provider_mutation_rejects_unsafe_yaml_without_touching_files(
    monkeypatch, tmp_path, unsafe_yaml, capability
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(unsafe_yaml, encoding="utf-8")
    env_path.write_text("KEEP_EXISTING=before\n", encoding="utf-8")
    original_config = config_path.read_bytes()
    original_env = env_path.read_bytes()

    if capability == "image":
        setter = model_config.set_custom_image_provider_config
        payload = {
            "id": "router",
            "base_url": "https://images.example.com/v1",
            "models": ["image-model"],
            "api_key": "must-not-be-written",
        }
    else:
        setter = model_config.set_custom_vision_provider_config
        payload = {
            "id": "router",
            "base_url": "https://vision.example.com/v1",
            "models": ["vision-model"],
            "transport": "openai_chat_completions",
            "api_key": "must-not-be-written",
        }

    with pytest.raises(ValueError, match="config"):
        setter(payload)

    assert config_path.read_bytes() == original_config
    assert env_path.read_bytes() == original_env
    assert "must-not-be-written" not in json.dumps(dict(os.environ))


@pytest.mark.parametrize("capability", ["image", "vision"])
def test_builtin_capability_mutation_rejects_unsafe_yaml_without_touching_files(
    monkeypatch, tmp_path, capability
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text("model: [\n", encoding="utf-8")
    env_path.write_text("KEEP_EXISTING=before\n", encoding="utf-8")
    original_config = config_path.read_bytes()
    original_env = env_path.read_bytes()

    if capability == "image":
        setter = model_config.set_image_gen_config
        payload = {
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
        }
    else:
        setter = model_config.set_vision_config
        payload = {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
        }

    with pytest.raises(ValueError, match="config"):
        setter(payload)

    assert config_path.read_bytes() == original_config
    assert env_path.read_bytes() == original_env


@pytest.mark.parametrize(
    ("setter_name", "payload", "secret_env"),
    (
        (
            "set_vision_config",
            {
                "provider": "zai",
                "model": "glm-5v-turbo",
                "api_key": "vision-secret-after",
            },
            "GLM_API_KEY",
        ),
        (
            "set_alibaba_image_capabilities",
            {
                "vision_model": "qwen3-vl-plus",
                "image_model": "qwen-image-2.0-pro",
                "api_key": "alibaba-secret-after",
            },
            "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY",
        ),
        (
            "set_image_gen_config",
            {
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "api_key": "image-secret-after",
            },
            "DASHSCOPE_API_KEY",
        ),
        (
            "set_main_model_config",
            {
                "provider": "custom",
                "model": "custom-model",
                "base_url": "https://models.example.com/v1",
                "api_key": "main-secret-after",
            },
            "HERMES_CUSTOM_MODEL_API_KEY",
        ),
    ),
)
def test_builtin_model_writers_preserve_config_env_pair_when_intent_fails(
    monkeypatch,
    tmp_path,
    setter_name,
    payload,
    secret_env,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(
        "unrelated:\n  owner: before\n",
        encoding="utf-8",
    )
    env_path.write_text("KEEP_EXISTING=before\n", encoding="utf-8")
    original_config = config_path.read_bytes()
    original_env = env_path.read_bytes()
    monkeypatch.delenv(secret_env, raising=False)

    def fail_journal(*_args, **_kwargs):
        raise OSError("journal unavailable")

    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        fail_journal,
    )

    with pytest.raises(OSError, match="journal unavailable"):
        getattr(model_config, setter_name)(payload)

    assert config_path.read_bytes() == original_config
    assert env_path.read_bytes() == original_env
    assert secret_env not in os.environ


def test_custom_provider_failed_intent_does_not_block_later_concurrent_env_write(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(
        "model:\n  provider: deepseek\n",
        encoding="utf-8",
    )
    env_path.write_text("KEEP_EXISTING=before\n", encoding="utf-8")
    original_config = config_path.read_bytes()

    journal_entered = threading.Event()
    release_failure = threading.Event()
    original_write_journal = credential_store._write_credential_journal
    injected = False

    def fail_first_journal(*args, **kwargs):
        nonlocal injected
        if not injected:
            # Every durable env mutation now has its own journal. Inject only
            # into the already-started custom-provider transaction so the
            # later independent write can prove the failed lock holder did
            # not leave a blocker or half-applied pair.
            injected = True
            journal_entered.set()
            assert release_failure.wait(timeout=3)
            raise OSError("journal unavailable")
        return original_write_journal(*args, **kwargs)

    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        fail_first_journal,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        provider_write = pool.submit(
            model_config.set_custom_image_provider_config,
            {
                "id": "router",
                "base_url": "https://images.example.com/v1",
                "models": ["image-model"],
                "api_key": "must-not-be-committed",
            },
        )
        assert journal_entered.wait(timeout=2)
        unrelated_write = pool.submit(
            providers._write_env_file,
            env_path,
            {"UNRELATED_CONCURRENT": "after"},
            config_path=config_path,
        )
        time.sleep(0.05)
        assert not unrelated_write.done()
        release_failure.set()
        with pytest.raises(OSError, match="journal unavailable"):
            provider_write.result(timeout=3)
        unrelated_write.result(timeout=3)

    env_text = env_path.read_text(encoding="utf-8")
    assert "KEEP_EXISTING=before" in env_text
    assert "UNRELATED_CONCURRENT=after" in env_text
    assert "must-not-be-committed" not in env_text
    assert os.environ["UNRELATED_CONCURRENT"] == "after"
    assert config_path.read_bytes() == original_config
    os.environ.pop("UNRELATED_CONCURRENT", None)


def test_custom_provider_stage_failure_preserves_existing_env_and_config(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(
        "model:\n  provider: deepseek\n",
        encoding="utf-8",
    )
    env_path.write_text("KEEP_EXISTING=before\n", encoding="utf-8")
    original_config = config_path.read_bytes()
    original_prepare = credential_store._prepare_pair_target

    def fail_env_stage(*, name, **kwargs):
        if name == "env":
            raise OSError("env stage unavailable")
        return original_prepare(name=name, **kwargs)

    monkeypatch.setattr(credential_store, "_prepare_pair_target", fail_env_stage)

    with pytest.raises(OSError, match="env stage unavailable"):
        model_config.set_custom_image_provider_config(
            {
                "id": "router",
                "base_url": "https://images.example.com/v1",
                "models": ["image-model"],
                "api_key": "must-not-be-committed",
            }
        )

    env_text = env_path.read_text(encoding="utf-8")
    assert "KEEP_EXISTING=before" in env_text
    assert "must-not-be-committed" not in env_text
    assert config_path.read_bytes() == original_config


def test_custom_provider_cas_refuses_to_overwrite_out_of_band_newer_config(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("owner: original\n", encoding="utf-8")

    original_replace = credential_store._replace_credential_stage
    injected = False

    def inject_newer_before_replace(stage_path, **kwargs):
        nonlocal injected
        logical_path = Path(kwargs["logical_path"])
        if logical_path.name == "config.yaml" and not injected:
            injected = True
            Path(kwargs["real_target"]).write_text(
                "owner: newer-writer\n",
                encoding="utf-8",
            )
        return original_replace(stage_path, **kwargs)

    monkeypatch.setattr(
        credential_store,
        "_replace_credential_stage",
        inject_newer_before_replace,
    )

    with pytest.raises(
        credential_store.CredentialRecoveryError,
        match="changed before replace",
    ):
        model_config.set_custom_image_provider_config(
            {
                "id": "router",
                "base_url": "https://images.example.com/v1",
                "models": ["image-model"],
                "api_key": "must-not-overwrite-newer-config",
            }
        )

    assert config_path.read_text(encoding="utf-8") == "owner: newer-writer\n"


def test_custom_provider_failed_intent_allows_newer_same_key_writer(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text("model:\n  provider: deepseek\n", encoding="utf-8")
    env_path.write_text("KEEP_EXISTING=before\n", encoding="utf-8")
    original_journal = credential_store._write_credential_journal
    attempted_env_key: list[str] = []

    def capture_key_then_fail(_lock_root, manifest):
        attempted_env_key.extend(manifest["env_keys"])
        raise OSError("journal unavailable")

    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        capture_key_then_fail,
    )

    with pytest.raises(OSError, match="journal unavailable"):
        model_config.set_custom_image_provider_config(
            {
                "id": "router",
                "base_url": "https://images.example.com/v1",
                "models": ["image-model"],
                "api_key": "attempted-secret",
            }
        )

    assert len(attempted_env_key) == 1
    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        original_journal,
    )
    providers._write_env_file(
        env_path,
        {attempted_env_key[0]: "newer-writer"},
        config_path=config_path,
    )
    assert "newer-writer" in env_path.read_text(encoding="utf-8")
    assert "attempted-secret" not in env_path.read_text(encoding="utf-8")


def test_custom_vision_provider_config_writes_isolated_secret_and_redacts(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.delenv("TAIJI_VISION_CUSTOM_ROUTER_API_KEY", raising=False)
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)

    result = model_config.set_custom_vision_provider_config({
        "id": "router",
        "name": "Router Vision",
        "base_url": "https://vision.example.com/v1",
        "models": ["router-vl"],
        "default_model": "router-vl",
        "transport": "openai_chat_completions",
        "api_key": "vision-secret-must-not-leak",
    })

    cfg = _read_config(tmp_path)
    provider = cfg["custom_vision_providers"][0]
    credential_ref = provider.pop("credential_ref")
    assert provider == {
        "id": "router",
        "name": "Router Vision",
        "base_url": "https://vision.example.com/v1",
        "models": ["router-vl"],
        "default_model": "router-vl",
        "transport": "openai_chat_completions",
        "network_scope": "public_direct",
        "trusted_proxy_profile": "",
    }
    credential = next(
        row for row in cfg["provider_credentials"] if row["id"] == credential_ref
    )
    canonical_env = model_config.credential_secret_env(credential_ref)
    assert credential["provider_family"] == "custom"
    assert credential["auth_type"] == "api_key"
    assert credential["secret_env"] == canonical_env
    assert credential["managed_by"] == "hermes-webui"
    assert credential["source_capability"] == "custom_vision_provider"
    assert credential["source_provider_id"] == "router"
    assert f"{canonical_env}=vision-secret-must-not-leak" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    public = json.dumps(result, ensure_ascii=False)
    assert "vision-secret-must-not-leak" not in public
    assert result["provider"]["id"] == "custom:router"
    assert result["provider"]["transport"] == "openai_chat_completions"
    os.environ.pop(canonical_env, None)


@pytest.mark.parametrize(
    ("base_url", "network_scope", "trusted_proxy_profile"),
    (
        ("https://127.0.0.1:8443/v1", "private_direct", ""),
        ("https://10.20.30.40:8443/v1", "private_direct", ""),
        ("https://[fd12:3456:789a::10]:8443/v1", "private_direct", ""),
        (
            "https://vision.internal.example/v1",
            "trusted_proxy",
            "corp-egress",
        ),
    ),
)
def test_custom_vision_save_preserves_non_public_runtime_transport_scope(
    monkeypatch,
    tmp_path,
    base_url,
    network_scope,
    trusted_proxy_profile,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    public_guard_calls = []

    def reject_public_guard(url):
        public_guard_calls.append(url)
        return False

    monkeypatch.setattr(
        "agent.custom_vision_providers.is_custom_vision_base_url_safe",
        reject_public_guard,
    )
    result = model_config.set_custom_vision_provider_config(
        {
            "id": "router",
            "base_url": base_url,
            "models": ["vision-model"],
            "transport": "openai_chat_completions",
            "network_scope": network_scope,
            "trusted_proxy_profile": trusted_proxy_profile,
            "api_key": "scope-secret",
        }
    )

    saved = _read_config(tmp_path)
    entry = saved["custom_vision_providers"][0]
    assert result["ok"] is True
    assert entry["base_url"] == base_url
    assert entry["network_scope"] == network_scope
    assert entry["trusted_proxy_profile"] == trusted_proxy_profile
    assert public_guard_calls == []

    import hermes_cli.config as cli_config
    import httpx
    from agent import auxiliary_client
    from agent.safe_outbound_http import NetworkScope

    monkeypatch.setattr(cli_config, "load_config", lambda: _read_config(tmp_path))
    transport = object()
    transport_calls = []
    client_calls = []

    def build_transport(**kwargs):
        transport_calls.append(kwargs)
        return transport

    def build_client(**kwargs):
        client_calls.append(kwargs)
        return object()

    monkeypatch.setattr(
        auxiliary_client,
        "build_openai_sync_transport",
        build_transport,
    )
    monkeypatch.setattr(httpx, "Client", build_client)

    runtime_client = auxiliary_client._build_named_openai_vision_http_client(
        "custom:router",
        async_mode=False,
    )

    assert runtime_client is not None
    assert transport_calls == [
        {
            "network_scope": NetworkScope(network_scope),
            "trusted_proxy_profile": trusted_proxy_profile or None,
        }
    ]
    assert client_calls == [
        {
            "transport": transport,
            "trust_env": False,
            "follow_redirects": False,
        }
    ]


def test_custom_vision_public_direct_keeps_dns_aware_save_guard(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    guard_calls = []

    def deny_public_endpoint(url):
        guard_calls.append(url)
        return False

    monkeypatch.setattr(
        "agent.custom_vision_providers.is_custom_vision_base_url_safe",
        deny_public_endpoint,
    )

    with pytest.raises(ValueError, match="公网安全校验"):
        model_config.set_custom_vision_provider_config(
            {
                "id": "router",
                "base_url": "https://vision.example.com/v1",
                "models": ["vision-model"],
                "transport": "openai_chat_completions",
                "network_scope": "public_direct",
                "api_key": "must-not-be-saved",
            }
        )

    assert guard_calls == ["https://vision.example.com/v1"]
    config_path = tmp_path / "config.yaml"
    assert (
        not config_path.exists()
        or _read_config(tmp_path).get("custom_vision_providers") in (None, [])
    )


def test_custom_image_set_reports_refresh_pending_after_committed_save(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(
        model_config,
        "_invalidate_image_gen_verification",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("state unavailable")
        ),
    )

    result = model_config.set_custom_image_provider_config(
        {
            "id": "router",
            "base_url": "https://images.example.com/v1",
            "models": ["image-model"],
            "api_key": "committed-secret",
        }
    )

    assert result["ok"] is True
    assert result["refresh_pending"] is True
    assert "image_gen_verification_refresh_pending" in result["warnings"]
    saved = _read_config(tmp_path)
    credential_ref = saved["custom_image_providers"][0]["credential_ref"]
    secret_env = model_config.credential_secret_env(credential_ref)
    assert saved["custom_image_providers"][0]["id"] == "router"
    assert f"{secret_env}=committed-secret" in (tmp_path / ".env").read_text(
        encoding="utf-8"
    )


def test_custom_vision_set_reports_refresh_pending_after_committed_save(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    monkeypatch.setattr(
        model_config,
        "_invalidate_vision_verification",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("state unavailable")
        ),
    )

    result = model_config.set_custom_vision_provider_config(
        {
            "id": "router",
            "base_url": "https://vision.example.com/v1",
            "models": ["vision-model"],
            "transport": "openai_chat_completions",
            "api_key": "committed-secret",
        }
    )

    assert result["ok"] is True
    assert result["refresh_pending"] is True
    assert "vision_verification_refresh_pending" in result["warnings"]
    assert _read_config(tmp_path)["custom_vision_providers"][0]["id"] == "router"


@pytest.mark.parametrize("capability", ["image", "vision"])
def test_custom_provider_delete_reports_refresh_pending_after_committed_delete(
    monkeypatch, tmp_path, capability
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    if capability == "image":
        model_config.set_custom_image_provider_config(
            {
                "id": "router",
                "base_url": "https://images.example.com/v1",
                "models": ["image-model"],
                "api_key": "committed-secret",
            }
        )
        config_key = "custom_image_providers"
        invalidator_name = "_invalidate_image_gen_verification"
        warning = "image_gen_verification_refresh_pending"
        delete = model_config.delete_custom_image_provider_config
    else:
        model_config.set_custom_vision_provider_config(
            {
                "id": "router",
                "base_url": "https://vision.example.com/v1",
                "models": ["vision-model"],
                "transport": "openai_chat_completions",
                "api_key": "committed-secret",
            }
        )
        config_key = "custom_vision_providers"
        invalidator_name = "_invalidate_vision_verification"
        warning = "vision_verification_refresh_pending"
        delete = model_config.delete_custom_vision_provider_config
    created = _read_config(tmp_path)
    credential_ref = created[config_key][0]["credential_ref"]
    secret_env = model_config.credential_secret_env(credential_ref)
    monkeypatch.setattr(
        model_config,
        invalidator_name,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("state unavailable")
        ),
    )

    result = delete("router")

    assert result["ok"] is True
    assert result["refresh_pending"] is True
    assert warning in result["warnings"]
    deleted = _read_config(tmp_path)
    assert deleted[config_key] == []
    assert secret_env not in (tmp_path / ".env").read_text(encoding="utf-8")


def test_deleting_custom_provider_removes_its_exclusive_managed_credential(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)

    model_config.set_custom_image_provider_config(
        {
            "id": "router",
            "base_url": "https://images.example.com/v1",
            "models": ["image-model"],
            "api_key": "managed-secret",
        }
    )
    created = _read_config(tmp_path)
    credential_ref = created["custom_image_providers"][0]["credential_ref"]
    secret_env = model_config.credential_secret_env(credential_ref)

    model_config.delete_custom_image_provider_config("router")

    deleted = _read_config(tmp_path)
    assert deleted["custom_image_providers"] == []
    assert all(
        row.get("id") != credential_ref
        for row in deleted.get("provider_credentials", [])
    )
    assert secret_env not in (tmp_path / ".env").read_text(encoding="utf-8")
    assert secret_env not in os.environ


def test_generic_credential_edit_preserves_custom_managed_lifecycle_metadata(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)

    model_config.set_custom_image_provider_config(
        {
            "id": "router",
            "base_url": "https://images.example.com/v1",
            "models": ["image-model"],
            "api_key": "initial-secret",
        }
    )
    created = _read_config(tmp_path)
    credential_ref = created["custom_image_providers"][0]["credential_ref"]
    secret_env = model_config.credential_secret_env(credential_ref)

    model_config.upsert_provider_credential(
        {
            "id": credential_ref,
            "provider_family": "custom",
            "label": "Rotated managed credential",
            "api_key": "rotated-secret",
        }
    )

    edited = next(
        row
        for row in _read_config(tmp_path)["provider_credentials"]
        if row.get("id") == credential_ref
    )
    assert edited["managed_by"] == "hermes-webui"
    assert edited["source_capability"] == "custom_image_provider"
    assert edited["source_provider_id"] == "router"

    model_config.delete_custom_image_provider_config("router")

    deleted = _read_config(tmp_path)
    assert all(
        row.get("id") != credential_ref
        for row in deleted.get("provider_credentials", [])
    )
    assert secret_env not in (tmp_path / ".env").read_text(encoding="utf-8")
    assert secret_env not in os.environ


def test_generic_custom_credential_rotation_invalidates_bound_capabilities(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    credential_ref = "shared-custom"
    secret_env = model_config.credential_secret_env(credential_ref)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": credential_ref,
                        "provider_family": "custom",
                        "label": "Shared custom",
                        "auth_type": "api_key",
                        "secret_env": secret_env,
                    }
                ],
                "custom_image_providers": [
                    {
                        "id": "image-router",
                        "base_url": "https://images.example.com/v1",
                        "credential_ref": credential_ref,
                        "models": ["image-model"],
                    }
                ],
                    "custom_vision_providers": [
                        {
                            "id": "vision-router",
                            "base_url": "https://vision.example.com/v1",
                        "credential_ref": credential_ref,
                        "models": ["vision-model"],
                            "transport": "openai_chat_completions",
                        }
                    ],
                    "auxiliary": {
                        "vision": {
                            "provider": "custom:vision-router",
                            "model": "vision-model",
                        }
                    },
                    "image_gen": {
                        "provider": "custom:image-router",
                        "model": "image-model",
                    },
                }
            ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(f"{secret_env}=before\n", encoding="utf-8")
    invalidated: list[str] = []
    monkeypatch.setattr(
        model_config,
        "_invalidate_vision_verification",
        lambda *_args, **_kwargs: invalidated.append("vision"),
    )
    monkeypatch.setattr(
        model_config,
        "_invalidate_image_gen_verification",
        lambda *_args, **_kwargs: invalidated.append("image"),
    )

    model_config.upsert_provider_credential(
        {
            "id": credential_ref,
            "provider_family": "custom",
            "label": "Shared custom",
            "api_key": "after",
        }
    )

    assert invalidated == ["vision", "image"]


def test_deleting_custom_providers_preserves_shared_user_owned_credential(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    credential_ref = "shared-custom"
    secret_env = model_config.credential_secret_env(credential_ref)

    model_config.set_custom_image_provider_config(
        {
            "id": "image-router",
            "base_url": "https://images.example.com/v1",
            "models": ["image-model"],
            "credential_ref": credential_ref,
            "api_key": "shared-secret",
        }
    )
    model_config.set_custom_vision_provider_config(
        {
            "id": "vision-router",
            "base_url": "https://vision.example.com/v1",
            "models": ["vision-model"],
            "transport": "openai_chat_completions",
            "credential_ref": credential_ref,
        }
    )

    model_config.delete_custom_image_provider_config("image-router")
    model_config.delete_custom_vision_provider_config("vision-router")

    deleted = _read_config(tmp_path)
    shared = next(
        row
        for row in deleted["provider_credentials"]
        if row.get("id") == credential_ref
    )
    assert "managed_by" not in shared
    assert shared["secret_env"] == secret_env
    assert f"{secret_env}=shared-secret" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert os.environ[secret_env] == "shared-secret"
    os.environ.pop(secret_env, None)


def test_custom_vision_provider_delete_rejects_active_provider(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({
            "auxiliary": {"vision": {"provider": "custom:router", "model": "router-vl"}},
            "custom_vision_providers": [{
                "id": "router",
                "name": "Router Vision",
                "base_url": "https://vision.example.com/v1",
                "api_key_env": "TAIJI_VISION_CUSTOM_ROUTER_API_KEY",
                "models": ["router-vl"],
                "default_model": "router-vl",
                "transport": "openai_chat_completions",
            }],
        }),
        encoding="utf-8",
    )

    (tmp_path / ".env").write_text(
        "TAIJI_VISION_CUSTOM_ROUTER_API_KEY=active-secret\n", encoding="utf-8"
    )
    monkeypatch.setenv("TAIJI_VISION_CUSTOM_ROUTER_API_KEY", "active-secret")
    with pytest.raises(ValueError, match="正在使用"):
        model_config.delete_custom_vision_provider_config("router")
    assert "active-secret" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert os.environ["TAIJI_VISION_CUSTOM_ROUTER_API_KEY"] == "active-secret"
    os.environ.pop("TAIJI_VISION_CUSTOM_ROUTER_API_KEY", None)


def test_custom_vision_provider_delete_removes_secret_and_recreate_without_key_is_unconfigured(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "custom_vision_providers": [{
            "id": "router", "base_url": "https://vision.example.com/v1",
            "models": ["router-vl"], "transport": "openai_chat_completions",
        }],
    }), encoding="utf-8")
    (tmp_path / ".env").write_text(
        "TAIJI_VISION_CUSTOM_ROUTER_API_KEY=old-secret\n", encoding="utf-8"
    )
    monkeypatch.setenv("TAIJI_VISION_CUSTOM_ROUTER_API_KEY", "old-secret")

    model_config.delete_custom_vision_provider_config("router")
    assert "TAIJI_VISION_CUSTOM_ROUTER_API_KEY" not in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TAIJI_VISION_CUSTOM_ROUTER_API_KEY" not in os.environ

    result = model_config.set_custom_vision_provider_config({
        "id": "router", "base_url": "https://vision.example.com/v1",
        "models": ["router-vl"], "transport": "openai_chat_completions",
    })
    assert result["provider"]["available"] is False
    assert result["provider"]["key_status"]["configured"] is False


def test_custom_vision_provider_delete_preserves_pair_when_journal_fails(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "custom_vision_providers": [{
            "id": "router", "base_url": "https://vision.example.com/v1",
            "models": ["router-vl"], "transport": "openai_chat_completions",
        }],
    }), encoding="utf-8")
    (tmp_path / ".env").write_text(
        "TAIJI_VISION_CUSTOM_ROUTER_API_KEY=old-secret\n", encoding="utf-8"
    )
    monkeypatch.setenv("TAIJI_VISION_CUSTOM_ROUTER_API_KEY", "old-secret")
    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("journal unavailable")
        ),
    )

    with pytest.raises(OSError, match="journal unavailable"):
        model_config.delete_custom_vision_provider_config("router")
    assert "old-secret" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert os.environ["TAIJI_VISION_CUSTOM_ROUTER_API_KEY"] == "old-secret"
    os.environ.pop("TAIJI_VISION_CUSTOM_ROUTER_API_KEY", None)


def test_custom_vision_provider_rejects_unknown_transport(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)

    with pytest.raises(ValueError, match="transport"):
        model_config.set_custom_vision_provider_config({
            "id": "router",
            "base_url": "https://vision.example.com/v1",
            "models": ["router-vl"],
            "transport": "vendor_native_magic",
            "api_key": "secret",
        })


def test_custom_vision_provider_preserves_pair_when_journal_fails(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    (tmp_path / ".env").write_text(
        "TAIJI_VISION_CUSTOM_ROUTER_API_KEY=old-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        credential_store,
        "_write_credential_journal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("journal unavailable")
        ),
    )

    with pytest.raises(OSError, match="journal unavailable"):
        model_config.set_custom_vision_provider_config({
            "id": "router",
            "name": "Router Vision",
            "base_url": "https://vision.example.com/v1",
            "models": ["router-vl"],
            "transport": "openai_chat_completions",
            "api_key": "new-secret",
        })

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "old-secret" in env_text
    assert "new-secret" not in env_text


def test_canonical_named_custom_vision_provider_get_uses_credential_ref(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    credential_ref = "vision-router-key"
    secret_env = model_config.credential_secret_env(credential_ref)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": credential_ref,
                        "provider_family": "custom",
                        "label": "Router Vision",
                        "auth_type": "api_key",
                        "secret_env": secret_env,
                    }
                ],
                "auxiliary": {
                    "vision": {
                        "provider": "custom:router",
                        "model": "router-vl",
                    }
                },
                "custom_vision_providers": [
                    {
                        "id": "router",
                        "name": "Router Vision",
                        "base_url": "https://vision.example.com/v1",
                        "credential_ref": credential_ref,
                        "models": ["router-vl"],
                        "default_model": "router-vl",
                        "transport": "openai_chat_completions",
                        "network_scope": "public_direct",
                        "trusted_proxy_profile": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        f"{secret_env}=canonical-vision-secret\n",
        encoding="utf-8",
    )

    result = model_config.get_vision_config()

    assert result["vision"]["key_status"] == {
        "configured": True,
        "source": "env_file",
        "env_var": secret_env,
    }
    row = next(item for item in result["providers"] if item["id"] == "custom:router")
    assert row["available"] is True
    assert row["key_status"]["env_var"] == secret_env
    assert "canonical-vision-secret" not in json.dumps(result, ensure_ascii=False)


def test_canonical_custom_vision_fingerprint_tracks_secret_and_network_identity(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    credential_ref = "vision-router-key"
    secret_env = model_config.credential_secret_env(credential_ref)
    config = {
        "provider_credentials": [
            {
                "id": credential_ref,
                "provider_family": "custom",
                "label": "Router Vision",
                "auth_type": "api_key",
                "secret_env": secret_env,
            }
        ],
        "auxiliary": {
            "vision": {
                "provider": "custom:router",
                "model": "router-vl",
            }
        },
        "custom_vision_providers": [
            {
                "id": "router",
                "name": "Router Vision",
                "base_url": "https://vision.example.com/v1",
                "credential_ref": credential_ref,
                "models": ["router-vl"],
                "default_model": "router-vl",
                "transport": "openai_chat_completions",
                "network_scope": "public_direct",
                "trusted_proxy_profile": "",
            }
        ],
    }
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    env_path.write_text(f"{secret_env}=before\n", encoding="utf-8")

    before = model_config._capture_vision_config_snapshot().fingerprint
    env_path.write_text(f"{secret_env}=after\n", encoding="utf-8")
    after_secret = model_config._capture_vision_config_snapshot().fingerprint
    config["custom_vision_providers"][0]["network_scope"] = "private_network"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    after_network = model_config._capture_vision_config_snapshot().fingerprint

    assert before != after_secret
    assert after_secret != after_network
    assert "before" not in before + after_secret + after_network
    assert "after" not in before + after_secret + after_network


def test_named_custom_vision_provider_appears_in_vision_config(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / ".env").write_text(
        "TAIJI_VISION_CUSTOM_ROUTER_API_KEY=router-secret\n",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({
            "auxiliary": {"vision": {"provider": "custom:router", "model": "router-vl"}},
            "custom_vision_providers": [{
                "id": "router",
                "name": "Router Vision",
                "base_url": "https://vision.example.com/v1",
                "api_key_env": "TAIJI_VISION_CUSTOM_ROUTER_API_KEY",
                "models": ["router-vl"],
                "default_model": "router-vl",
                "transport": "openai_chat_completions",
            }],
        }),
        encoding="utf-8",
    )

    result = model_config.get_vision_config()
    row = next(item for item in result["providers"] if item["id"] == "custom:router")
    assert row["active"] is True
    assert row["available"] is True
    assert row["transport"] == "openai_chat_completions"
    assert result["vision"]["key_status"]["env_var"] == "TAIJI_VISION_CUSTOM_ROUTER_API_KEY"
    assert "router-secret" not in json.dumps(result, ensure_ascii=False)


def test_selecting_named_custom_vision_provider_stores_only_reference_and_model(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setenv("TAIJI_VISION_CUSTOM_ROUTER_API_KEY", "router-secret")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({
            "custom_vision_providers": [{
                "id": "router",
                "name": "Router Vision",
                "base_url": "https://vision.example.com/v1",
                "models": ["router-vl"],
                "transport": "anthropic_messages",
            }],
        }),
        encoding="utf-8",
    )

    result = model_config.set_vision_config({
        "provider": "custom:router",
        "model": "router-vl",
        "base_url": "https://attacker.invalid/v1",
    })

    stored = _read_config(tmp_path)["auxiliary"]["vision"]
    assert stored == {"provider": "custom:router", "model": "router-vl"}
    assert result["vision"]["base_url"] == "https://vision.example.com/v1"
    assert result["vision"]["api_mode"] == "anthropic_messages"
    assert "router-secret" not in json.dumps(result)
    os.environ.pop("TAIJI_VISION_CUSTOM_ROUTER_API_KEY", None)


def test_custom_image_provider_appears_in_image_gen_config(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", "router-sensitive-value")
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": True,
            "reason_code": "ready",
            "public_message": "图像生成已就绪。",
        },
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {"provider": "custom:router", "model": "gpt-image-custom"},
                "custom_image_providers": [
                    {
                        "id": "router",
                        "name": "Router Images",
                        "base_url": "https://images.example.com/v1",
                        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
                        "models": ["gpt-image-custom"],
                        "default_model": "gpt-image-custom",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = model_config.get_image_gen_config()
    row = next(item for item in result["providers"] if item["id"] == "custom:router")

    assert result["image_gen"]["provider"] == "custom:router"
    assert row["name"] == "Router Images"
    assert row["active"] is True
    assert row["available"] is False
    assert row["can_attempt"] is True
    assert row["reason_code"] == "configured_unverified"
    assert row["status_message"] == "已配置，尚未验证。"
    assert row["oauth_managed"] is False
    assert row["custom"] is True
    assert row["key_status"]["configured"] is True
    assert "router-sensitive-value" not in json.dumps(result, ensure_ascii=False)
    os.environ.pop("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", None)


def test_custom_image_provider_reads_key_status_from_env_file(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.delenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": True,
            "reason_code": "ready",
            "public_message": "图像生成已就绪。",
        },
    )
    (tmp_path / ".env").write_text("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY=secret-from-file\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {"provider": "custom:router", "model": "gpt-image-custom"},
                "custom_image_providers": [
                    {
                        "id": "router",
                        "name": "Router Images",
                        "base_url": "https://images.example.com/v1",
                        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
                        "models": ["gpt-image-custom"],
                        "default_model": "gpt-image-custom",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = model_config.get_custom_image_provider_configs()
    row = result["providers"][0]

    assert row["available"] is False
    assert row["configured"] is True
    assert row["verification_status"] == "configured_unverified"
    assert row["status_message"] == "已配置，尚未验证。"
    assert row["key_status"]["configured"] is True
    assert row["key_status"]["source"] == "env_file"
    assert "secret-from-file" not in json.dumps(result, ensure_ascii=False)


def test_custom_image_provider_delete_rejects_active_provider(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {"provider": "custom:router", "model": "gpt-image-custom"},
                "custom_image_providers": [
                    {
                        "id": "router",
                        "name": "Router Images",
                        "base_url": "https://images.example.com/v1",
                        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
                        "models": ["gpt-image-custom"],
                        "default_model": "gpt-image-custom",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    try:
        model_config.delete_custom_image_provider_config("router")
    except ValueError as exc:
        assert "正在使用" in str(exc)
    else:
        raise AssertionError("active custom image provider was deleted")


def test_custom_image_provider_delete_removes_inactive_provider(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {"provider": "fal", "model": "fal-ai/flux-2-pro"},
                "custom_image_providers": [
                    {
                        "id": "router",
                        "name": "Router Images",
                        "base_url": "https://images.example.com/v1",
                        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
                        "models": ["gpt-image-custom"],
                        "default_model": "gpt-image-custom",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = model_config.delete_custom_image_provider_config("router")

    assert result["ok"] is True
    assert _read_config(tmp_path)["custom_image_providers"] == []


def test_unknown_image_model_is_rejected_before_provider_rows_or_environment(
    monkeypatch, tmp_path
):
    from agent import image_gen_registry

    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    readiness_calls = []
    environment_calls = []
    commit_calls = []
    monkeypatch.setattr(
        model_config,
        "_ensure_image_gen_plugins_registered",
        lambda: None,
    )
    monkeypatch.setattr(
        image_gen_registry,
        "list_providers",
        lambda: [],
    )
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: readiness_calls.append("readiness") or {},
    )
    monkeypatch.setattr(
        model_config,
        "_key_status_for_env",
        lambda env_var: (
            environment_calls.append(env_var)
            or {"configured": False, "source": "none", "env_var": env_var}
        ),
    )
    monkeypatch.setattr(
        model_config,
        "_commit_expected_config_env",
        lambda *args, **kwargs: commit_calls.append((args, kwargs)),
    )

    with pytest.raises(ValueError, match="unknown image generation model"):
        model_config.set_image_gen_config(
            {
                "provider": "dashscope",
                "model": "vendor-unknown-image-model",
                "api_key": "must-not-be-written",
            }
        )

    assert readiness_calls == []
    assert environment_calls == []
    assert commit_calls == []


def test_named_custom_image_and_vision_save_reject_unlisted_models_without_writing(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    original = {
        "custom_image_providers": [
            {
                "id": "router",
                "name": "Router Images",
                "base_url": "https://images.example.com/v1",
                "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
                "models": ["router-image-v1"],
                "default_model": "router-image-v1",
                "allow_custom_model_id": "false",
            }
        ],
        "custom_vision_providers": [
            {
                "id": "router",
                "name": "Router Vision",
                "base_url": "https://vision.example.com/v1",
                "api_key_env": "TAIJI_VISION_CUSTOM_ROUTER_API_KEY",
                "models": ["router-vl-v1"],
                "default_model": "router-vl-v1",
                "transport": "openai_chat_completions",
            }
        ],
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(original, allow_unicode=True),
        encoding="utf-8",
    )
    commit_calls = []
    monkeypatch.setattr(
        model_config,
        "_commit_expected_config_env",
        lambda *args, **kwargs: commit_calls.append((args, kwargs)),
    )

    with pytest.raises(ValueError, match="unknown custom image"):
        model_config.set_image_gen_config(
            {
                "provider": "custom:router",
                "model": "unlisted-image-model",
                "api_key": "must-not-write-image",
            }
        )
    with pytest.raises(ValueError, match="unknown custom vision"):
        model_config.set_vision_config(
            {
                "provider": "custom:router",
                "model": "unlisted-vision-model",
                "api_key": "must-not-write-vision",
            }
        )

    assert _read_config(tmp_path) == original
    assert commit_calls == []


def test_named_custom_image_save_revalidates_after_concurrent_delete(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path)
    original = {
        "custom_image_providers": [
            {
                "id": "router",
                "name": "Router Images",
                "base_url": "https://images.example.com/v1",
                "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
                "models": ["router-image-v1"],
                "default_model": "router-image-v1",
                "allow_custom_model_id": False,
            }
        ]
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(original, allow_unicode=True),
        encoding="utf-8",
    )
    stale_validation_complete = threading.Event()
    continue_save = threading.Event()
    row_calls = {"count": 0}
    custom_row = {
        "id": "custom:router",
        "name": "Router Images",
        "custom": True,
        "domestic": False,
        "integration_status": "custom",
        "models": [{"id": "router-image-v1"}],
        "default_model": "router-image-v1",
        "allow_custom_model_id": False,
        "credential_fields": [],
        "endpoint_fields": [],
    }

    def pause_after_stale_validation(_active):
        row_calls["count"] += 1
        if row_calls["count"] == 1:
            stale_validation_complete.set()
            assert continue_save.wait(timeout=3)
        return [custom_row]

    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        pause_after_stale_validation,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        save_future = pool.submit(
            model_config.set_image_gen_config,
            {"provider": "custom:router", "model": "router-image-v1"},
        )
        assert stale_validation_complete.wait(timeout=2)
        delete_future = pool.submit(
            model_config.delete_custom_image_provider_config,
            "router",
        )
        delete_future.result(timeout=3)
        continue_save.set()
        with pytest.raises(
            ValueError,
            match="unknown image generation provider",
        ):
            save_future.result(timeout=3)

    saved = _read_config(tmp_path)
    assert saved.get("custom_image_providers") == []
    assert "image_gen" not in saved


def test_named_custom_vision_save_revalidates_after_concurrent_delete(
    monkeypatch, tmp_path
):
    from agent import custom_vision_providers

    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "get_vision_config",
        lambda: {"ok": True},
    )
    original = {
        "custom_vision_providers": [
            {
                "id": "router",
                "name": "Router Vision",
                "base_url": "https://vision.example.com/v1",
                "api_key_env": "TAIJI_VISION_CUSTOM_ROUTER_API_KEY",
                "models": ["router-vl-v1"],
                "default_model": "router-vl-v1",
                "transport": "openai_chat_completions",
            }
        ]
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(original, allow_unicode=True),
        encoding="utf-8",
    )
    stale_validation_complete = threading.Event()
    continue_save = threading.Event()
    original_find = custom_vision_providers.find_custom_vision_provider_entry
    find_calls = {"count": 0}

    def pause_after_stale_validation(provider_id, config_data=None):
        entry = original_find(provider_id, config_data)
        find_calls["count"] += 1
        if config_data is None and find_calls["count"] == 1:
            stale_validation_complete.set()
            assert continue_save.wait(timeout=3)
        return entry

    monkeypatch.setattr(
        custom_vision_providers,
        "find_custom_vision_provider_entry",
        pause_after_stale_validation,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        save_future = pool.submit(
            model_config.set_vision_config,
            {"provider": "custom:router", "model": "router-vl-v1"},
        )
        assert stale_validation_complete.wait(timeout=2)
        delete_future = pool.submit(
            model_config.delete_custom_vision_provider_config,
            "router",
        )
        delete_future.result(timeout=3)
        continue_save.set()
        with pytest.raises(ValueError, match="unknown vision provider"):
            save_future.result(timeout=3)

    saved = _read_config(tmp_path)
    assert saved.get("custom_vision_providers") == []
    assert "auxiliary" not in saved


def test_model_config_payload_hides_raw_config_path(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n  provider: deepseek\n  default: deepseek-chat\n",
        encoding="utf-8",
    )

    result = model_config.get_model_config()

    dumped = json.dumps(result, ensure_ascii=False)
    assert "config_path" not in result
    assert str(config_path) not in dumped
    assert "config.yaml" not in dumped
    assert result["config"]["label"] == "本机配置"
    assert result["config"]["exists"] is True


def test_image_gen_config_payload_hides_raw_config_path(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("image_gen:\n  provider: fal\n", encoding="utf-8")
    monkeypatch.setattr(model_config, "_image_gen_provider_rows", lambda active: [])

    result = model_config.get_image_gen_config()

    dumped = json.dumps(result, ensure_ascii=False)
    assert "config_path" not in result
    assert str(config_path) not in dumped
    assert "config.yaml" not in dumped
    assert result["config"]["label"] == "本机配置"
    assert result["config"]["exists"] is True


def test_image_gen_config_returns_named_ref_and_safe_endpoint_options(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                    "credential_ref": "alibaba-default",
                    "options": {
                        "endpoint_mode": "workspace",
                        "workspace_id": "llm-demo",
                        "region": "cn-beijing",
                        "api_key": "must-never-be-public",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_config, "_image_gen_provider_rows", lambda active: [])
    monkeypatch.setattr(
        model_config,
        "get_custom_image_provider_configs",
        lambda: {"providers": []},
    )

    result = model_config.get_image_gen_config()

    assert result["image_gen"]["credential_ref"] == "alibaba-default"
    assert result["image_gen"]["options"] == {
        "endpoint_mode": "workspace",
        "workspace_id": "llm-demo",
        "region": "cn-beijing",
    }
    assert "must-never-be-public" not in json.dumps(result)


def test_image_gen_config_rejects_taiji_public_provider_from_domestic_policy(monkeypatch, tmp_path):
    real_get_image_gen_config = model_config.get_image_gen_config
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(model_config, "get_image_gen_config", real_get_image_gen_config)

    class _Provider:
        name = "openai-codex"
        display_name = "OpenAI 图像生成"

        def get_setup_schema(self):
            return {
                "name": "OpenAI 图像生成",
                "badge": "授权",
                "tag": "通过太极智能体授权使用图像生成",
                "env_vars": [],
            }

        def list_models(self):
            return [{"id": "gpt-image-2-medium", "display": "GPT Image 2"}]

        def default_model(self):
            return "gpt-image-2-medium"

        def is_available(self):
            return False

    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr(
        "agent.image_gen_registry.list_providers",
        lambda: [_Provider()],
    )
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": False,
            "reason_code": "authorization_required",
            "public_message": "图像生成未授权，请先在太极智能体中完成图像生成授权。",
        },
    )

    try:
        model_config.set_image_gen_config(
            {
                "provider": "taiji-image",
                "model": "gpt-image-2-medium",
            }
        )
    except ValueError as exc:
        assert "国产" in str(exc)
    else:
        raise AssertionError("taiji-image was accepted in domestic-only image config")


def test_image_gen_provider_rows_include_doubao(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("ARK_API_KEY", raising=False)

    rows = model_config._image_gen_provider_rows("doubao")
    doubao = next(row for row in rows if row["id"] == "doubao")

    assert doubao["name"] == "Doubao Seedream"
    assert doubao["active"] is True
    assert doubao["key_status"]["env_var"] == "ARK_API_KEY"
    assert doubao["key_status"]["configured"] is False
    model_ids = {item["id"] for item in doubao["models"]}
    assert "doubao-seedream-5-0-260128" in model_ids
    assert "doubao-seedream-5-0-lite-260128" in model_ids


def test_image_gen_provider_rows_are_domestic_stable_only(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    class _Provider:
        def __init__(self, name, domestic=True, status="stable"):
            self.name = name
            self.display_name = name
            self._domestic = domestic
            self._status = status

        def get_setup_schema(self):
            return {
                "name": self.display_name,
                "tag": f"{self.name} provider",
                "env_vars": [{"key": f"{self.name.upper().replace('-', '_')}_KEY"}],
                "domestic": self._domestic,
                "integration_status": self._status,
            }

        def list_models(self):
            return [{"id": f"{self.name}-model", "display": f"{self.name} model"}]

        def default_model(self):
            return f"{self.name}-model"

        def is_available(self):
            return False

    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr(
        "agent.image_gen_registry.list_providers",
        lambda: [
            _Provider("doubao"),
            _Provider("dashscope"),
            _Provider("qianfan"),
            _Provider("zhipu-image"),
            _Provider("minimax-image"),
            _Provider("fal", domestic=False, status="external"),
            _Provider("openai", domestic=False, status="external"),
            _Provider("kling", domestic=True, status="candidate"),
        ],
    )

    rows = model_config._image_gen_provider_rows("")
    ids = {row["id"] for row in rows}

    assert {"doubao", "dashscope", "qianfan", "zhipu-image", "minimax-image"} <= ids
    assert "fal" not in ids
    assert "openai" not in ids
    assert "kling" not in ids
    assert all(row.get("domestic") or row.get("custom") for row in rows)
    assert all(row.get("integration_status") in {"stable", "custom"} for row in rows)


def test_image_gen_provider_rows_expose_credential_status(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_WORKSPACE_ID", raising=False)
    monkeypatch.setenv("DASHSCOPE_REGION", "cn-beijing")

    class _Provider:
        name = "dashscope"
        display_name = "通义 Qwen-Image"

        def get_setup_schema(self):
            return {
                "name": self.display_name,
                "env_vars": [{"key": "DASHSCOPE_API_KEY"}],
                "credential_fields": [
                    {
                        "name": "api_key",
                        "env_var": "DASHSCOPE_API_KEY",
                        "label": "API Key",
                        "required": True,
                        "secret": True,
                    },
                    {
                        "name": "workspace_id",
                        "env_var": "DASHSCOPE_WORKSPACE_ID",
                        "label": "Workspace ID",
                        "required": True,
                        "secret": False,
                    },
                    {
                        "name": "region",
                        "env_var": "DASHSCOPE_REGION",
                        "label": "Region",
                        "required": False,
                        "secret": False,
                    },
                ],
                "domestic": True,
                "integration_status": "stable",
            }

        def list_models(self):
            return [{"id": "qwen-image-2.0-pro", "display": "Qwen Image 2 Pro"}]

        def default_model(self):
            return "qwen-image-2.0-pro"

        def is_available(self):
            return False

    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr("agent.image_gen_registry.list_providers", lambda: [_Provider()])

    rows = model_config._image_gen_provider_rows("dashscope")
    row = next(item for item in rows if item["id"] == "dashscope")

    assert [field["env_var"] for field in row["credential_fields"]] == [
        "DASHSCOPE_API_KEY",
    ]
    assert [field["env_var"] for field in row["endpoint_fields"]] == [
        "DASHSCOPE_WORKSPACE_ID",
        "DASHSCOPE_REGION",
    ]
    assert row["credential_status"]["configured"] is False
    assert set(row["credential_status"]["missing"]) == {
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_WORKSPACE_ID",
    }
    assert row["key_status"]["env_var"] == "DASHSCOPE_API_KEY"
    assert row["domestic"] is True
    assert row["integration_status"] == "stable"
    os.environ.pop("DASHSCOPE_REGION", None)


def test_active_dashscope_provider_row_uses_named_credential_status(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default",
            "provider": "alibaba",
            "api_key": "named-secret",
        }
    )
    saved = _read_config(tmp_path)
    saved["image_gen"] = {
        "provider": "dashscope",
        "credential_ref": "alibaba-default",
        "options": {"workspace_id": "llm-demo"},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(saved), encoding="utf-8")

    class _Provider:
        name = "dashscope"
        display_name = "通义 Qwen-Image"

        def get_setup_schema(self):
            return {
                "name": self.display_name,
                "env_vars": [{"key": "DASHSCOPE_API_KEY"}],
                "credential_fields": [
                    {
                        "name": "api_key",
                        "env_var": "DASHSCOPE_API_KEY",
                        "label": "API Key",
                        "required": True,
                        "secret": True,
                    },
                    {
                        "name": "workspace_id",
                        "env_var": "DASHSCOPE_WORKSPACE_ID",
                        "label": "Workspace ID",
                        "required": True,
                        "secret": False,
                    },
                ],
                "domestic": True,
                "integration_status": "stable",
            }

        def list_models(self):
            return [{"id": "qwen-image-2.0-pro", "display": "Qwen Image 2 Pro"}]

        def default_model(self):
            return "qwen-image-2.0-pro"

        def is_available(self):
            return True

    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr("agent.image_gen_registry.list_providers", lambda: [_Provider()])
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness", lambda: {}
    )

    row = next(
        item
        for item in model_config._image_gen_provider_rows("dashscope")
        if item["id"] == "dashscope"
    )

    assert row["credential_status"]["configured"] is True
    assert row["credential_status"]["missing"] == []
    assert row["key_status"] == {
        "configured": True,
        "source": "provider_credential",
        "env_var": "",
    }


def test_openai_codex_image_provider_reflects_real_readiness(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path)

    class _Provider:
        name = "openai-codex"
        display_name = "OpenAI 图像生成"

        def get_setup_schema(self):
            return {
                "name": "OpenAI 图像生成",
                "badge": "授权",
                "tag": "通过太极智能体授权使用图像生成",
                "env_vars": [],
            }

        def list_models(self):
            return [{"id": "gpt-image-2-medium", "display": "GPT Image 2"}]

        def default_model(self):
            return "gpt-image-2-medium"

        def is_available(self):
            return False

    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr(
        "agent.image_gen_registry.list_providers",
        lambda: [_Provider()],
    )
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": False,
            "reason_code": "authorization_required",
            "public_message": "图像生成未授权，请先在太极智能体中完成图像生成授权。",
        },
    )

    rows = model_config._image_gen_provider_rows("openai-codex")
    row = next(item for item in rows if item["id"] == "taiji-image")

    assert row["active"] is True
    assert row["available"] is False
    assert row["policy_blocked"] is True
    assert row["domestic"] is False
    assert row["integration_status"] == "blocked"
    assert row["key_status"]["configured"] is False
    assert row["key_status"]["source"] == "policy_blocked"
    assert row["reason_code"] == "authorization_required"
    assert row["status_message"] == "图像生成未授权，请先在太极智能体中完成图像生成授权。"
    visible = json.dumps(row, ensure_ascii=False)
    assert "Hermes" not in visible
    assert "Codex" not in visible
    assert "openai-codex" not in visible
    assert ("her" "mes tools") not in visible


def _write_saved_image_gen_config(
    tmp_path, *, provider="dashscope", model="qwen-image-2.0-pro"
):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"image_gen": {"provider": provider, "model": model}}),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=image-test-only-key\n", encoding="utf-8"
    )


class _ProbeImageProvider:
    name = "dashscope"
    _supports_pinned_image_request_binding = True

    def __init__(self, result):
        self.result = result
        self.calls = []

    def is_available(self):
        return True

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.result() if callable(self.result) else self.result


def _install_probe_provider(monkeypatch, provider):
    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr("agent.image_gen_registry.get_provider", lambda name: provider if name == "dashscope" else None)


def test_image_gen_config_distinguishes_configured_from_verified(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_: tmp_path / "image-gen-verification.json",
        raising=False,
    )
    _write_saved_image_gen_config(tmp_path)
    _install_probe_provider(monkeypatch, _ProbeImageProvider({}))

    result = model_config.get_image_gen_config()

    assert result["image_gen"]["verification"]["status"] == "configured_unverified"
    assert result["image_gen"]["verification"]["checked_at"] == ""


def test_image_gen_provider_public_availability_requires_verified_probe(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _write_saved_image_gen_config(tmp_path)
    provider = _ProbeImageProvider({})
    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr("agent.image_gen_registry.list_providers", lambda: [provider])
    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": False,
            "reason_code": "verification_required",
            "public_message": "图像生成已配置但尚未通过真实生图验证。",
        },
    )

    row = next(
        item
        for item in model_config._image_gen_provider_rows("dashscope")
        if item["id"] == "dashscope"
    )

    assert row["can_attempt"] is True
    assert row["available"] is False
    assert row["reason_code"] == "verification_required"


def test_image_gen_test_rejects_unconfigured_without_calling_provider(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    calls = []
    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: calls.append("registered"))

    result = model_config.test_image_gen_config()

    assert calls == []
    assert result["ok"] is False
    assert result["status"] == "unconfigured"
    assert result["error_code"] == "image_gen_not_configured"
    assert set(result) == {
        "ok", "status", "checked_at", "provider", "model",
        "error_code", "message", "diagnostic_id",
    }


def test_image_gen_test_classifies_unresolved_env_before_incomplete_config(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "image-gen-verification.json"
    unresolved_model_env = "B3_GAP9_UNRESOLVED_IMAGE_MODEL"
    monkeypatch.delenv(unresolved_model_env, raising=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_: state_path,
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {
                    "provider": "dashscope",
                    "model": f"${{{unresolved_model_env}}}",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=profile-b-key\n",
        encoding="utf-8",
    )
    provider = _ProbeImageProvider({})
    _install_probe_provider(monkeypatch, provider)

    snapshot = model_config._capture_image_gen_config_snapshot()
    result = model_config.test_image_gen_config()

    assert snapshot.configured is False
    assert snapshot.effective_config_resolved is False
    assert result["ok"] is False
    assert result["status"] == "configured_unverified"
    assert result["error_code"] == "unresolved_effective_config"
    assert provider.calls == []
    assert not state_path.exists()


@pytest.mark.parametrize(
    (
        "image_cfg",
        "api_key",
        "expected_configured",
        "expected_effective_config_resolved",
        "expected_status",
        "expected_error_code",
    ),
    [
        (
            {},
            "",
            False,
            False,
            "unconfigured",
            "image_gen_not_configured",
        ),
        (
            {"provider": "disabled", "model": ""},
            "",
            False,
            False,
            "unconfigured",
            "image_gen_not_configured",
        ),
        (
            {
                "provider": "dashscope",
                "model": "${B3_GAP9_UNRESOLVED_IMAGE_MODEL}",
            },
            "profile-b-key",
            False,
            False,
            "configured_unverified",
            "unresolved_effective_config",
        ),
        (
            {
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
            },
            "",
            False,
            True,
            "unconfigured",
            "image_gen_not_configured",
        ),
    ],
)
def test_image_gen_preflight_classification_matches_all_entry_points(
    monkeypatch,
    tmp_path,
    image_cfg,
    api_key,
    expected_configured,
    expected_effective_config_resolved,
    expected_status,
    expected_error_code,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "image-gen-verification.json"
    monkeypatch.delenv("B3_GAP9_UNRESOLVED_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_: state_path,
    )
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda *_: [],
    )
    monkeypatch.setattr(
        model_config,
        "get_custom_image_provider_configs",
        lambda: {"providers": []},
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"image_gen": image_cfg}, sort_keys=False),
        encoding="utf-8",
    )
    env_value = (
        f"DASHSCOPE_API_KEY={api_key}\n"
        if api_key
        else "UNRELATED_TEST_VALUE=present\n"
    )
    (tmp_path / ".env").write_text(env_value, encoding="utf-8")
    provider = _ProbeImageProvider({})
    _install_probe_provider(monkeypatch, provider)

    snapshot = model_config._capture_image_gen_config_snapshot()
    direct_public = model_config._public_image_gen_verification(
        image_cfg,
        profile="default",
    )
    get_public = model_config.get_image_gen_config()["image_gen"][
        "verification"
    ]
    probe = model_config.test_image_gen_config()

    assert snapshot.configured is expected_configured
    assert (
        snapshot.effective_config_resolved
        is expected_effective_config_resolved
    )
    for result in (direct_public, get_public, probe):
        assert result["status"] == expected_status
        assert result["error_code"] == expected_error_code
    assert provider.calls == []
    assert not state_path.exists()


def test_image_gen_test_rejects_unresolved_runtime_without_calling_provider(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "image-gen-verification.json"
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_: state_path,
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                    "options": {
                        "endpoint_mode": "custom",
                        "base_url": "https://invalid-image.example.test/v1",
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=profile-b-key\n",
        encoding="utf-8",
    )
    provider = _ProbeImageProvider({})
    _install_probe_provider(monkeypatch, provider)

    result = model_config.test_image_gen_config()

    assert result["ok"] is False
    assert result["status"] == "configured_unverified"
    assert result["error_code"] == "unresolved_effective_config"
    assert provider.calls == []
    assert not state_path.exists()


def test_image_gen_probe_discards_its_verifying_state_after_key_rotation(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "image-gen-verification.json"
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_: state_path,
    )
    monkeypatch.setattr(model_config, "_IMAGE_GEN_PROBE_GENERATIONS", {})
    _write_saved_image_gen_config(tmp_path)
    generated = tmp_path / "cache" / "images" / "rotated.png"
    verifying = {}

    def rotate_key_during_probe():
        verifying.update(
            json.loads(state_path.read_text(encoding="utf-8"))
        )
        (tmp_path / ".env").write_text(
            "DASHSCOPE_API_KEY=rotated-during-probe\n",
            encoding="utf-8",
        )
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nrotated")
        return {
            "success": True,
            "image": str(generated),
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
        }

    _install_probe_provider(
        monkeypatch,
        _ProbeImageProvider(rotate_key_during_probe),
    )

    result = model_config.test_image_gen_config()

    assert result["ok"] is False
    assert result["status"] == "configured_unverified"
    assert result["error_code"] == "image_gen_probe_superseded"
    _assert_verification_tombstone(
        json.loads(state_path.read_text(encoding="utf-8")),
        minimum_generation=verifying["generation"] + 1,
        forbidden_fingerprint=verifying["fingerprint"],
        forbidden_diagnostic_id=verifying["diagnostic_id"],
    )
    assert (
        model_config.get_image_gen_config()["image_gen"]["verification"][
            "status"
        ]
        != "verified"
    )


def test_image_probe_uses_captured_dashscope_binding_after_config_rotation(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    before_root = "https://before-image.example.test"
    after_root = "https://after-image.example.test"

    def write_profile(root, secret):
        (tmp_path / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "image_gen": {
                        "provider": "dashscope",
                        "model": "qwen-image-2.0-pro",
                        "options": {
                            "endpoint_mode": "custom",
                            "base_url": root,
                        },
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (tmp_path / ".env").write_text(
            f"DASHSCOPE_API_KEY={secret}\n",
            encoding="utf-8",
        )

    write_profile(before_root, "before-secret")
    model_config._ensure_image_gen_plugins_registered()
    from agent.image_gen_registry import get_provider

    selected = get_provider("dashscope")
    assert selected is not None
    dashscope = __import__(
        type(selected).__module__,
        fromlist=["cached_success"],
    )
    captured = {}

    def fake_post_json(**kwargs):
        captured["url"] = kwargs["url"]
        captured["authorization"] = kwargs["headers"]["Authorization"]
        return {
            "data": [{"url": "https://cdn.example.test/probe.png"}]
        }, None

    def fake_cached_success(**kwargs):
        image_path = tmp_path / "cache" / "images" / "pinned-probe.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"\x89PNG\r\n\x1a\npinned")
        return {
            "success": True,
            "image": str(image_path),
            "provider": "dashscope",
            "model": kwargs["model"],
        }

    monkeypatch.setattr(dashscope, "post_json", fake_post_json)
    monkeypatch.setattr(dashscope, "cached_success", fake_cached_success)
    monkeypatch.setattr(dashscope, "is_safe_url", lambda *_: True)
    capture_snapshot = model_config._capture_image_gen_config_snapshot

    def capture_then_rotate_profile():
        snapshot = capture_snapshot()
        write_profile(after_root, "after-secret")
        return snapshot

    monkeypatch.setattr(
        model_config,
        "_capture_image_gen_config_snapshot",
        capture_then_rotate_profile,
    )

    result = model_config.test_image_gen_config()

    from agent.alibaba_endpoints import IMAGE_GENERATION_PATH

    assert result["error_code"] == "image_gen_probe_superseded"
    assert captured == {
        "url": before_root + IMAGE_GENERATION_PATH,
        "authorization": "Bearer before-secret",
    }
    assert "after-secret" not in json.dumps(captured)
    assert after_root not in json.dumps(captured)


def test_image_probe_uses_captured_custom_binding_after_config_rotation(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    before_root = "https://before-custom.example.test/v1"
    after_root = "https://after-custom.example.test/v1"
    secret_env = "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY"

    def write_profile(root, secret):
        (tmp_path / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "custom_image_providers": [
                        {
                            "id": "router",
                            "name": "Router",
                            "base_url": root,
                            "api_key_env": secret_env,
                            "models": ["image-model"],
                            "default_model": "image-model",
                        }
                    ],
                    "image_gen": {
                        "provider": "custom:router",
                        "model": "image-model",
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (tmp_path / ".env").write_text(
            f"{secret_env}={secret}\n",
            encoding="utf-8",
        )

    write_profile(before_root, "before-custom-secret")
    import agent.custom_image_providers as custom_image_providers

    captured = {}

    @contextmanager
    def fake_request(**kwargs):
        captured["url"] = kwargs["url"]
        captured["authorization"] = kwargs["headers"]["Authorization"]
        yield SimpleNamespace(status_code=200)

    def fake_save_b64(_payload, *, prefix):
        image_path = tmp_path / "cache" / "images" / f"{prefix}.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"\x89PNG\r\n\x1a\ncustom")
        return image_path

    monkeypatch.setattr(
        custom_image_providers,
        "request_pinned_https",
        fake_request,
    )
    monkeypatch.setattr(
        custom_image_providers,
        "read_bounded_json",
        lambda _response: {"data": [{"b64_json": "aW1hZ2U="}]},
    )
    monkeypatch.setattr(
        custom_image_providers,
        "save_b64_image",
        fake_save_b64,
    )
    capture_snapshot = model_config._capture_image_gen_config_snapshot

    def capture_then_rotate_profile():
        snapshot = capture_snapshot()
        write_profile(after_root, "after-custom-secret")
        return snapshot

    monkeypatch.setattr(
        model_config,
        "_capture_image_gen_config_snapshot",
        capture_then_rotate_profile,
    )

    result = model_config.test_image_gen_config()

    assert result["error_code"] == "image_gen_probe_superseded"
    # Config changed after capture, so the verifying-generation guard must
    # reject the old A binding before the first external request.
    assert captured == {}
    assert "after-custom-secret" not in json.dumps(captured)
    assert after_root not in json.dumps(captured)


def test_image_gen_saved_config_is_not_unconfigured_when_provider_cannot_attempt(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: tmp_path / "state.json")
    _write_saved_image_gen_config(tmp_path)
    provider = _ProbeImageProvider({})
    provider.is_available = lambda: False
    provider._supports_pinned_image_request_binding = False
    provider._allow_legacy_image_probe_test_seam = True
    _install_probe_provider(monkeypatch, provider)

    public = model_config.get_image_gen_config()["image_gen"]["verification"]
    tested = model_config.test_image_gen_config()

    assert public["status"] == "configured_unverified"
    assert tested["status"] == "failed"
    assert tested["error_code"] == "image_gen_provider_unavailable"
    assert provider.calls == []


@pytest.mark.parametrize("success_value", ["false", 1, {}, None])
def test_image_gen_probe_requires_literal_true_success(
    monkeypatch, tmp_path, success_value
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: tmp_path / "state.json")
    _write_saved_image_gen_config(tmp_path)
    generated = tmp_path / "cache" / "images" / "malformed.png"

    def result_payload():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nmalformed")
        return {
            "success": success_value,
            "image": str(generated),
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
        }

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result_payload))

    result = model_config.test_image_gen_config()

    assert result["status"] == "failed"
    assert result["error_code"] == "image_gen_probe_failed"
    assert not generated.exists()


def test_image_gen_probe_explains_when_safe_result_download_is_blocked(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_: tmp_path / "state.json",
    )
    _write_saved_image_gen_config(tmp_path)
    _install_probe_provider(
        monkeypatch,
        _ProbeImageProvider(
            {
                "success": False,
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "error_type": "io_error",
                "error": (
                    "dashscope image result download failed: "
                    "DashScope image URL failed safety validation"
                ),
            }
        ),
    )

    result = model_config.test_image_gen_config()

    assert result["status"] == "failed"
    assert result["error_code"] == "image_gen_result_url_blocked"
    assert "已返回图片结果" in result["message"]
    assert "代理或 DNS" in result["message"]
    assert "dashscope" not in result["message"].lower()


def test_image_gen_probe_verifies_identity_magic_and_removes_probe_file(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "image-gen-verification.json"
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path, raising=False)
    _write_saved_image_gen_config(tmp_path)
    generated = tmp_path / "cache" / "images" / "probe.png"

    def successful_result():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nprobe")
        return {
            "success": True,
            "image": str(generated),
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "prompt": "must-not-persist",
        }

    provider = _ProbeImageProvider(successful_result)
    _install_probe_provider(monkeypatch, provider)

    result = model_config.test_image_gen_config()

    assert result["status"] == "verified"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    from agent.image_gen_verification import ImageGenRequestBinding

    assert isinstance(call["_runtime_binding"], ImageGenRequestBinding)
    assert call["_runtime_binding"].authorization_fingerprint
    assert call["_runtime_binding"].authorization_generation
    assert callable(call["_reauth_guard"])
    assert {
        key: value
        for key, value in call.items()
        if key not in {"_runtime_binding", "_reauth_guard"}
    } == {
        "prompt": "生成一张简洁的蓝色几何图形测试图，不包含人物、文字或品牌。",
        "aspect_ratio": "square",
        "num_images": 1,
        "model": "qwen-image-2.0-pro",
    }
    assert not generated.exists()
    public_dump = json.dumps(result, ensure_ascii=False)
    persisted_dump = state_path.read_text(encoding="utf-8")
    for forbidden in ("image-test-only-key", "must-not-persist", str(generated), "digest"):
        assert forbidden not in public_dump
        assert forbidden not in persisted_dump
    assert model_config.get_image_gen_config()["image_gen"]["verification"]["status"] == "verified"


@pytest.mark.parametrize(
    ("overrides", "payload", "expected_code"),
    [
        ({"provider": "other"}, b"\x89PNG\r\n\x1a\nprobe", "image_gen_probe_failed"),
        ({"model": "other-model"}, b"\x89PNG\r\n\x1a\nprobe", "image_gen_probe_failed"),
        ({}, b"not-an-image", "image_gen_invalid_file"),
    ],
)
def test_image_gen_probe_rejects_wrong_identity_or_invalid_header(
    monkeypatch, tmp_path, overrides, payload, expected_code
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: tmp_path / "state.json", raising=False)
    _write_saved_image_gen_config(tmp_path)
    generated = tmp_path / "cache" / "images" / "probe.png"

    def result():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(payload)
        return {
            "success": True,
            "image": str(generated),
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            **overrides,
        }

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result))

    response = model_config.test_image_gen_config()

    assert response["status"] == "failed"
    assert response["error_code"] == expected_code
    assert not generated.exists()


@pytest.mark.parametrize("image_value", ["", "/does/not/exist.png"])
def test_image_gen_probe_rejects_missing_image_file(monkeypatch, tmp_path, image_value):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: tmp_path / "state.json", raising=False)
    _write_saved_image_gen_config(tmp_path)
    provider = _ProbeImageProvider({
        "success": True,
        "image": image_value,
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
    })
    _install_probe_provider(monkeypatch, provider)

    response = model_config.test_image_gen_config()

    assert response["status"] == "failed"
    assert response["error_code"] == "image_gen_invalid_file"


def test_image_gen_verification_invalidates_when_key_changes(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path, raising=False)
    _write_saved_image_gen_config(tmp_path)
    generated = tmp_path / "cache" / "images" / "probe.webp"

    def result():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"RIFF\x04\x00\x00\x00WEBP")
        return {"success": True, "image": str(generated), "provider": "dashscope", "model": "qwen-image-2.0-pro"}

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result))
    assert model_config.test_image_gen_config()["status"] == "verified"
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=rotated\n", encoding="utf-8")

    assert model_config.get_image_gen_config()["image_gen"]["verification"]["status"] == "configured_unverified"


def test_image_gen_verification_invalidates_when_named_key_rotates(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path, raising=False)
    model_config.upsert_provider_credential({
        "id": "alibaba-image",
        "provider": "dashscope",
        "api_key": "before",
    })
    saved = _read_config(tmp_path)
    saved["image_gen"] = {
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
        "credential_ref": "alibaba-image",
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(saved), encoding="utf-8")
    generated = tmp_path / "cache" / "images" / "probe.png"

    def result():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nprobe")
        return {"success": True, "image": str(generated), "provider": "dashscope", "model": "qwen-image-2.0-pro"}

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result))
    assert model_config.test_image_gen_config()["status"] == "verified"
    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY=before",
            "TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY=after",
        ),
        encoding="utf-8",
    )

    assert model_config.get_image_gen_config()["image_gen"]["verification"]["status"] == "configured_unverified"


def test_image_gen_probe_failure_returns_only_safe_fields(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path, raising=False)
    _write_saved_image_gen_config(tmp_path)
    _install_probe_provider(monkeypatch, _ProbeImageProvider({
        "success": False,
        "error": "401 image-test-only-key /private/provider/path",
        "raw_response": "must-not-persist",
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
    }))

    response = model_config.test_image_gen_config()

    assert response["status"] == "failed"
    combined = json.dumps(response, ensure_ascii=False) + state_path.read_text(encoding="utf-8")
    for forbidden in ("image-test-only-key", "/private/provider/path", "must-not-persist"):
        assert forbidden not in combined


def test_saving_image_gen_config_invalidates_previous_verification(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path, raising=False)
    monkeypatch.setattr(model_config, "_IMAGE_GEN_PROBE_GENERATIONS", {})
    model_config._atomic_write_json(
        state_path,
        {
            "schema_version": 1,
            "generation": 5,
            "fingerprint": "previous-image-fingerprint",
            "status": "verified",
            "checked_at": "2030-01-01T00:00:00Z",
            "error_code": "",
            "message": "previous verified state",
            "diagnostic_id": "previous-image-diagnostic",
        },
    )
    monkeypatch.setattr(model_config, "_image_gen_provider_rows", lambda *_: [_dashscope_image_provider_row()])

    model_config.set_image_gen_config({"provider": "dashscope", "model": "qwen-image-2.0-pro", "api_key": "key"})

    _assert_verification_tombstone(
        json.loads(state_path.read_text(encoding="utf-8")),
        minimum_generation=6,
        forbidden_fingerprint="previous-image-fingerprint",
        forbidden_diagnostic_id="previous-image-diagnostic",
    )


def test_image_gen_probe_isolated_by_profile_and_newer_probe_wins(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    active_profile = {"name": "profile-a"}
    monkeypatch.setattr(model_config, "_active_profile_name", lambda: active_profile["name"])
    monkeypatch.setattr(model_config, "_image_gen_verification_state_root", lambda: tmp_path / "states", raising=False)
    _write_saved_image_gen_config(tmp_path)
    first_started = threading.Event()
    release_first = threading.Event()
    calls = {"count": 0}

    def result():
        calls["count"] += 1
        n = calls["count"]
        path = tmp_path / "cache" / "images" / f"probe-{n}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xffprobe")
        if n == 1:
            first_started.set()
            assert release_first.wait(timeout=5)
        return {"success": True, "image": str(path), "provider": "dashscope", "model": "qwen-image-2.0-pro"}

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result))
    results = {}
    first = threading.Thread(target=lambda: results.setdefault("first", model_config.test_image_gen_config()))
    first.start()
    assert first_started.wait(timeout=5)
    results["second"] = model_config.test_image_gen_config()
    release_first.set()
    first.join(timeout=5)

    assert results["second"]["status"] == "verified"
    assert results["first"]["error_code"] == "image_gen_probe_superseded"
    active_profile["name"] = "profile-b"
    assert model_config.get_image_gen_config()["image_gen"]["verification"]["status"] == "configured_unverified"


@pytest.mark.parametrize(
    ("field", "value", "changes_identity"),
    [
        ("base_url", "https://other.example.com/v1", True),
        ("default_model", "image-model-v2", True),
        ("transport", "vendor_native_images", False),
        ("response_format", "url", True),
        ("timeout_seconds", 90, True),
    ],
)
def test_custom_image_identity_changes_verification_fingerprint(
    monkeypatch, tmp_path, field, value, changes_identity
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    base_entry = {
        "id": "router",
        "name": "Router",
        "base_url": "https://images.example.com/v1",
        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
        "models": ["image-model-v1", "image-model-v2"],
        "default_model": "image-model-v1",
        "transport": "openai_images",
        "response_format": "auto",
        "timeout_seconds": 30,
    }
    config = {
        "image_gen": {"provider": "custom:router", "model": "image-model-v1"},
        "custom_image_providers": [base_entry],
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / ".env").write_text(
        "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY=custom-secret\n", encoding="utf-8"
    )
    before = model_config._capture_image_gen_config_snapshot().fingerprint
    config["custom_image_providers"][0] = {**base_entry, field: value}
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(config),
        encoding="utf-8",
    )
    after = model_config._capture_image_gen_config_snapshot().fingerprint
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": (
                    model_config.CAPABILITY_VERIFICATION_SCHEMA_VERSION
                ),
                "fingerprint": before,
                "status": "verified",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path)
    provider = _ProbeImageProvider({})
    provider.name = "custom:router"
    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr(
        "agent.image_gen_registry.get_provider",
        lambda name: provider if name == "custom:router" else None,
    )

    assert (before != after) is changes_identity
    assert "custom-secret" not in before + after
    assert model_config._public_image_gen_verification(
        config["image_gen"], profile="default"
    )["status"] == (
        "configured_unverified" if changes_identity else "verified"
    )
    assert "custom-secret" not in state_path.read_text(encoding="utf-8")


def test_custom_image_key_rotation_changes_verification_fingerprint(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    config = {
        "image_gen": {"provider": "custom:router", "model": "image-model"},
        "custom_image_providers": [{
            "id": "router",
            "base_url": "https://images.example.com/v1",
            "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
            "models": ["image-model"],
            "default_model": "image-model",
        }],
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY=before\n", encoding="utf-8")
    before = model_config._image_gen_config_fingerprint(
        config["image_gen"], profile="default", config_data=config
    )
    env_path.write_text("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY=after\n", encoding="utf-8")

    after = model_config._image_gen_config_fingerprint(
        config["image_gen"], profile="default", config_data=config
    )
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"fingerprint": before, "status": "verified"}), encoding="utf-8"
    )
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path)
    provider = _ProbeImageProvider({})
    provider.name = "custom:router"
    monkeypatch.setattr(model_config, "_ensure_image_gen_plugins_registered", lambda: None)
    monkeypatch.setattr(
        "agent.image_gen_registry.get_provider",
        lambda name: provider if name == "custom:router" else None,
    )

    assert before != after
    assert "before" not in before + after
    assert "after" not in before + after
    assert model_config._public_image_gen_verification(
        config["image_gen"], profile="default"
    )["status"] == "configured_unverified"


def test_updating_custom_image_provider_invalidates_image_verification(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    invalidated = []
    monkeypatch.setattr(
        model_config,
        "_invalidate_image_gen_verification",
        lambda *_args, **_kwargs: invalidated.append(True),
    )

    model_config.set_custom_image_provider_config({
        "id": "router",
        "name": "Router",
        "base_url": "https://images.example.com/v1",
        "models": ["image-model"],
        "default_model": "image-model",
        "api_key": "secret",
    })

    assert invalidated == [True]


def test_image_probe_rejects_preexisting_cache_file_without_deleting_it(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: tmp_path / "state.json")
    _write_saved_image_gen_config(tmp_path)
    existing = tmp_path / "cache" / "images" / "existing.png"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"\x89PNG\r\n\x1a\npreexisting")
    provider = _ProbeImageProvider({
        "success": True,
        "image": str(existing),
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
    })
    _install_probe_provider(monkeypatch, provider)

    result = model_config.test_image_gen_config()

    assert result["status"] == "failed"
    assert result["error_code"] == "image_gen_invalid_file"
    assert existing.read_bytes().endswith(b"preexisting")


@pytest.mark.parametrize("target_inside_cache", [True, False])
def test_image_probe_rejects_new_symlink_without_deleting_link_or_target(
    monkeypatch, tmp_path, target_inside_cache
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: tmp_path / "state.json")
    _write_saved_image_gen_config(tmp_path)
    cache = tmp_path / "cache" / "images"
    cache.mkdir(parents=True, exist_ok=True)
    target = (cache / "target.png") if target_inside_cache else (tmp_path / "outside.png")
    target.write_bytes(b"\x89PNG\r\n\x1a\ntarget")
    link = cache / "provider-result.png"

    def result_payload():
        link.symlink_to(target)
        return {"success": True, "image": str(link), "provider": "dashscope", "model": "qwen-image-2.0-pro"}

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result_payload))

    result = model_config.test_image_gen_config()

    assert result["status"] == "failed"
    assert result["error_code"] == "image_gen_invalid_file"
    assert not link.exists()
    assert not link.is_symlink()
    assert target.read_bytes().endswith(b"target")


def test_image_probe_rejects_new_hardlink_to_preexisting_cache_file(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: tmp_path / "state.json")
    _write_saved_image_gen_config(tmp_path)
    cache = tmp_path / "cache" / "images"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / "target.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\ntarget")
    hardlink = cache / "provider-result.png"

    def result_payload():
        os.link(target, hardlink)
        return {"success": True, "image": str(hardlink), "provider": "dashscope", "model": "qwen-image-2.0-pro"}

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result_payload))

    result = model_config.test_image_gen_config()

    assert result["status"] == "failed"
    assert result["error_code"] == "image_gen_invalid_file"
    assert target.read_bytes().endswith(b"target")
    assert not hardlink.exists()


def test_unsafe_probe_entry_cleanup_failure_is_reported(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: tmp_path / "state.json")
    _write_saved_image_gen_config(tmp_path)
    cache = tmp_path / "cache" / "images"
    cache.mkdir(parents=True)
    target = tmp_path / "target.png"
    target.write_bytes(b"target")
    link = cache / "provider-result.png"

    def result_payload():
        link.symlink_to(target)
        return {"success": True, "image": str(link), "provider": "dashscope", "model": "qwen-image-2.0-pro"}

    _install_probe_provider(monkeypatch, _ProbeImageProvider(result_payload))
    monkeypatch.setattr(model_config, "_remove_probe_cleanup_candidate", lambda *_: False)

    result = model_config.test_image_gen_config()

    assert result["status"] == "failed"
    assert result["error_code"] == "image_gen_cleanup_failed"
    assert target.read_bytes() == b"target"


def test_image_probe_snapshot_failure_is_safe_persisted_and_retryable(monkeypatch, tmp_path):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(model_config, "_image_gen_verification_state_path", lambda *_: state_path)
    _write_saved_image_gen_config(tmp_path)
    generated = tmp_path / "cache" / "images" / "retry.png"

    def result_payload():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nretry")
        return {"success": True, "image": str(generated), "provider": "dashscope", "model": "qwen-image-2.0-pro"}

    provider = _ProbeImageProvider(result_payload)
    _install_probe_provider(monkeypatch, provider)
    real_snapshot = model_config._snapshot_image_cache
    monkeypatch.setattr(
        model_config,
        "_snapshot_image_cache",
        lambda: (_ for _ in ()).throw(
            PermissionError("secret-path /private/cache image-test-only-key")
        ),
    )

    failed = model_config.test_image_gen_config()

    assert failed["status"] == "failed"
    assert failed["error_code"] == "image_gen_probe_failed"
    combined = json.dumps(failed, ensure_ascii=False) + state_path.read_text(encoding="utf-8")
    for forbidden in ("secret-path", "/private/cache", "image-test-only-key"):
        assert forbidden not in combined
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "failed"
    assert provider.calls == []

    monkeypatch.setattr(model_config, "_snapshot_image_cache", real_snapshot)
    retried = model_config.test_image_gen_config()

    assert retried["status"] == "verified"
    assert not generated.exists()


def test_probe_cleanup_candidate_identity_change_does_not_delete_replacement(
    monkeypatch, tmp_path
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    cache = tmp_path / "cache" / "images"
    cache.mkdir(parents=True)
    before = model_config._snapshot_image_cache()
    result_path = cache / "provider-result.png"
    result_path.write_bytes(b"\x89PNG\r\n\x1a\noriginal")
    candidate = model_config._probe_cleanup_candidate(result_path, before)
    assert candidate is not None
    result_path.unlink()
    result_path.write_bytes(b"replacement")

    removed = model_config._remove_probe_cleanup_candidate(candidate)

    assert removed is False
    assert result_path.read_bytes() == b"replacement"


def test_probe_cache_accepts_runtime_home_parent_symlink(monkeypatch, tmp_path):
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    alias_home = tmp_path / "alias-home"
    alias_home.symlink_to(real_home, target_is_directory=True)
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: alias_home)
    before = model_config._snapshot_image_cache()
    result_path = alias_home / "cache" / "images" / "provider-result.png"
    result_path.write_bytes(b"\x89PNG\r\n\x1a\nowned")

    candidate = model_config._probe_cleanup_candidate(result_path, before)
    owned = model_config._owned_probe_image(candidate, before)

    assert candidate is not None
    assert owned is not None
    assert model_config._remove_probe_cleanup_candidate(candidate) is True
    assert not result_path.exists()


def _image_capability_test_provider_rows(monkeypatch):
    monkeypatch.setattr(
        model_config,
        "_vision_provider_rows",
        lambda *_args, **_kwargs: [
            {
                "id": "alibaba",
                "name": "阿里云百炼",
                "provider_family": "alibaba_dashscope",
                "models": [{"id": "qwen3-vl-plus", "label": "Qwen3 VL Plus"}],
                "default_model": "qwen3-vl-plus",
                "auth_type": "api_key",
                "credential_fields": [],
                "endpoint_fields": [],
                "supports_named_credentials": True,
            }
        ],
    )
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda *_args, **_kwargs: [
            {
                "id": "dashscope",
                "name": "通义 Qwen-Image",
                "provider_family": "alibaba_dashscope",
                "models": [
                    {"id": "qwen-image-2.0-pro", "label": "Qwen Image 2.0 Pro"}
                ],
                "default_model": "qwen-image-2.0-pro",
                "auth_type": "api_key",
                "credential_fields": [],
                "endpoint_fields": [],
                "supports_named_credentials": True,
                "domestic": True,
                "integration_status": "stable",
                "policy_blocked": False,
            }
        ],
    )


def _image_capability_configure_body(
    tmp_path: Path,
    payload: dict,
    *,
    request_id: str,
) -> dict:
    body = dict(payload)
    env_path = tmp_path / ".env"
    env_sha256 = hashlib.sha256(
        env_path.read_bytes() if env_path.exists() else b""
    ).hexdigest()
    body["expected_revision"] = model_config._image_capability_revision(
        _read_config(tmp_path),
        env_sha256=env_sha256,
    )
    body["request_id"] = request_id
    return body


def _current_image_capability_revision(tmp_path: Path) -> str:
    snapshot = credential_store.load_credential_snapshot(tmp_path / "config.yaml")
    return model_config._image_capability_revision(
        snapshot.config,
        env_sha256=snapshot.env_sha256,
    )


def test_image_capabilities_snapshot_exposes_enabled_catalog_and_effective_route(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {
                    "provider": "openai",
                    "default": "gpt-4.1",
                    "supports_vision": True,
                },
                "auxiliary": {
                    "vision": {
                        "enabled": False,
                        "provider": "alibaba",
                        "model": "qwen3-vl-plus",
                        "api_key": "do-not-echo-secret",
                    }
                },
                "image_gen": {
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                },
            }
        ),
        encoding="utf-8",
    )

    result = model_config.get_image_capabilities()

    assert result["capabilities"]["vision"]["enabled"] is False
    assert result["capabilities"]["vision"]["verification"]["status"] == "disabled"
    assert result["capabilities"]["image_generation"]["enabled"] is True
    assert result["effective_route"]["vision"] == {
        "route": "main_model_vision",
        "provider": "openai",
        "model": "gpt-4.1",
    }
    assert result["effective_route"]["image_generation"]["route"] == "unavailable"
    assert result["providers"] == [
        {
            "provider_family": "alibaba_dashscope",
            "label": "阿里云百炼",
            "capabilities": ["vision", "image_generation"],
            "provider_ids": {
                "vision": "alibaba",
                "image_generation": "dashscope",
            },
            "auth_type": "api_key",
            "auth_editable": True,
            "auth_message": "填写平台签发的 API Key；密钥只保存在本机。",
            "support_level": "native",
            "supports_named_credentials": True,
            "models": {
                "vision": [
                    {"id": "qwen3-vl-plus", "label": "Qwen3 VL Plus"}
                ],
                "image_generation": [
                    {
                        "id": "qwen-image-2.0-pro",
                        "label": "Qwen Image 2.0 Pro",
                    }
                ],
            },
            "default_models": {
                "vision": "qwen3-vl-plus",
                "image_generation": "qwen-image-2.0-pro",
            },
            "credential_fields": {
                "vision": [],
                "image_generation": [],
            },
            "endpoint_fields": {
                "vision": [],
                "image_generation": [],
            },
            "selectable": True,
        }
    ]
    assert len(result["revision"]) == 64
    assert "do-not-echo-secret" not in json.dumps(result, ensure_ascii=False)


def test_image_capability_provider_metadata_projects_canonical_auth_contract():
    providers = model_config._image_capability_provider_metadata(
        [
            {
                "id": "credential-free-vision",
                "name": "免认证识图",
                "provider_family": "credential-free",
                "models": [],
                "auth_type": "no_auth",
                "auth_editable": False,
                "auth_message": "canonical no-auth guidance",
            }
        ],
        [],
    )

    assert providers == [
        {
            "provider_family": "credential-free",
            "label": "免认证识图",
            "capabilities": ["vision"],
            "provider_ids": {"vision": "credential-free-vision"},
            "auth_type": "no_auth",
            "auth_editable": False,
            "auth_message": "canonical no-auth guidance",
            "support_level": "native",
            "supports_named_credentials": False,
            "models": {"vision": []},
            "default_models": {"vision": ""},
            "credential_fields": {"vision": []},
            "endpoint_fields": {"vision": []},
            "selectable": True,
        }
    ]


def test_image_capability_catalog_exposes_named_credentials_for_supported_builtins():
    providers = model_config._image_capability_provider_metadata(
        [
            {
                "id": provider_id,
                "name": provider_id,
                "provider_family": family,
                "auth_type": "api_key",
                "credential_fields": [{"name": "api_key", "secret": True}],
            }
            for provider_id, family in (
                ("alibaba", "alibaba_dashscope"),
                ("zai", "zhipu"),
            )
        ],
        [
            {
                "id": provider_id,
                "name": provider_id,
                "provider_family": family,
                "auth_type": "api_key",
                "credential_fields": [{"name": "api_key", "secret": True}],
                "domestic": True,
                "integration_status": "stable",
            }
            for provider_id, family in (
                ("dashscope", "alibaba_dashscope"),
                ("doubao", "doubao"),
                ("qianfan", "qianfan"),
                ("zhipu-image", "zhipu"),
                ("minimax-image", "minimax"),
            )
        ],
    )

    by_family = {row["provider_family"]: row for row in providers}
    assert set(by_family) == {
        "alibaba_dashscope",
        "doubao",
        "qianfan",
        "zhipu",
        "minimax",
    }
    assert all(row["supports_named_credentials"] for row in by_family.values())
    assert by_family["zhipu"]["provider_ids"] == {
        "vision": "zai",
        "image_generation": "zhipu-image",
    }


def test_image_capability_catalog_never_infers_api_key_fields_for_non_api_key_auth():
    providers = model_config._image_capability_provider_metadata(
        [
            {
                "id": "zai",
                "name": "OAuth-only ZAI",
                "provider_family": "zhipu",
                "auth_type": "oauth",
                "credential_fields": [],
            },
            {
                "id": "custom:router",
                "name": "Custom Router",
                "provider_family": "custom_router",
                "auth_type": "api_key",
                "credential_fields": [{"name": "api_key", "secret": True}],
                "custom": True,
            },
        ],
        [
            {
                "id": "doubao",
                "name": "No-auth Doubao",
                "provider_family": "doubao",
                "auth_type": "no_auth",
                "credential_fields": [],
                "domestic": True,
                "integration_status": "stable",
            }
        ],
    )

    by_family = {row["provider_family"]: row for row in providers}
    assert by_family["zhipu"]["supports_named_credentials"] is False
    assert by_family["doubao"]["supports_named_credentials"] is False
    assert by_family["custom_router"]["supports_named_credentials"] is False


@pytest.mark.parametrize(
    ("capability", "provider_id", "model_id", "family"),
    [
        ("vision", "zai", "glm-5v-turbo", "zhipu"),
        (
            "image_generation",
            "doubao",
            "doubao-seedream-5-0-260128",
            "doubao",
        ),
        ("image_generation", "qianfan", "qwen-image", "qianfan"),
        ("image_generation", "zhipu-image", "glm-image", "zhipu"),
        ("image_generation", "minimax-image", "image-01", "minimax"),
    ],
)
def test_image_capability_stage_saves_named_credential_for_non_ali_builtins(
    monkeypatch,
    tmp_path,
    capability,
    provider_id,
    model_id,
    family,
):
    credential_ref = f"{provider_id}-named"
    config_data = {
        "provider_credentials": [
            {
                "id": credential_ref,
                "provider_family": family,
                "auth_type": "api_key",
                "secret_env": model_config.credential_secret_env(
                    credential_ref
                ),
            }
        ]
    }
    body = {
        "enabled": True,
        "provider": provider_id,
        "model": model_id,
        "credential_ref": credential_ref,
        "endpoint_values": {},
    }

    if capability == "vision":
        model_config._stage_vision_capability(
            config_data,
            body,
            config_path=tmp_path / "config.yaml",
        )
        saved = config_data["auxiliary"]["vision"]
    else:
        provider_row = {
            "id": provider_id,
            "provider_family": family,
            "models": [{"id": model_id, "label": model_id}],
            "default_model": model_id,
            "auth_type": "api_key",
            "credential_fields": [{"name": "api_key", "secret": True}],
            "endpoint_fields": [],
            "domestic": True,
            "integration_status": "stable",
            "policy_blocked": False,
        }
        monkeypatch.setattr(
            model_config,
            "_image_gen_provider_model_contract",
            lambda *_args, **_kwargs: provider_row,
        )
        monkeypatch.setattr(
            model_config,
            "_image_gen_provider_rows",
            lambda *_args, **_kwargs: [provider_row],
        )
        model_config._stage_image_generation_capability(
            config_data,
            body,
            config_path=tmp_path / "config.yaml",
        )
        saved = config_data["image_gen"]

    assert saved["credential_ref"] == credential_ref


@pytest.mark.parametrize(
    ("capability", "provider_id", "model_id", "family"),
    [
        ("vision", "zai", "glm-5v-turbo", "zhipu"),
        (
            "image_generation",
            "dashscope",
            "qwen-image-2.0-pro",
            "alibaba_dashscope",
        ),
        (
            "image_generation",
            "doubao",
            "doubao-seedream-5-0-260128",
            "doubao",
        ),
        ("image_generation", "qianfan", "qwen-image", "qianfan"),
        ("image_generation", "zhipu-image", "glm-image", "zhipu"),
        ("image_generation", "minimax-image", "image-01", "minimax"),
    ],
)
def test_image_capability_stage_uses_configured_default_for_supported_builtins(
    monkeypatch,
    tmp_path,
    capability,
    provider_id,
    model_id,
    family,
):
    credential_ref = f"{provider_id}-default"
    secret_env = model_config.credential_secret_env(credential_ref)
    config_data = {
        "provider_credentials": [
            {
                "id": credential_ref,
                "provider_family": family,
                "auth_type": "api_key",
                "secret_env": secret_env,
                "default": True,
            }
        ]
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(config_data, sort_keys=False),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        f"{secret_env}=configured-default-secret\n",
        encoding="utf-8",
    )
    body = {
        "enabled": True,
        "provider": provider_id,
        "model": model_id,
        "endpoint_values": {},
    }

    if capability == "vision":
        model_config._stage_vision_capability(
            config_data,
            body,
            config_path=config_path,
        )
        saved = config_data["auxiliary"]["vision"]
    else:
        provider_row = {
            "id": provider_id,
            "provider_family": family,
            "models": [{"id": model_id, "label": model_id}],
            "default_model": model_id,
            "auth_type": "api_key",
            "credential_fields": [{"name": "api_key", "secret": True}],
            "endpoint_fields": [],
            "domestic": True,
            "integration_status": "stable",
            "policy_blocked": False,
        }
        monkeypatch.setattr(
            model_config,
            "_image_gen_provider_model_contract",
            lambda *_args, **_kwargs: provider_row,
        )
        monkeypatch.setattr(
            model_config,
            "_image_gen_provider_rows",
            lambda *_args, **_kwargs: [provider_row],
        )
        model_config._stage_image_generation_capability(
            config_data,
            body,
            config_path=config_path,
        )
        saved = config_data["image_gen"]

    assert saved["credential_ref"] == credential_ref


def test_image_capability_stage_rejects_non_ali_credential_family_mismatch(
    monkeypatch,
    tmp_path,
):
    credential_ref = "doubao-named"
    config_data = {
        "provider_credentials": [
            {
                "id": credential_ref,
                "provider_family": "doubao",
                "auth_type": "api_key",
                "secret_env": model_config.credential_secret_env(
                    credential_ref
                ),
            }
        ]
    }
    provider_row = {
        "id": "zhipu-image",
        "provider_family": "zhipu",
        "models": [{"id": "glm-image", "label": "GLM-Image"}],
        "default_model": "glm-image",
        "auth_type": "api_key",
        "credential_fields": [{"name": "api_key", "secret": True}],
        "endpoint_fields": [],
        "domestic": True,
        "integration_status": "stable",
        "policy_blocked": False,
    }
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_model_contract",
        lambda *_args, **_kwargs: provider_row,
    )
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda *_args, **_kwargs: [provider_row],
    )

    with pytest.raises(ValueError, match="所选凭据不属于当前 Provider"):
        model_config._stage_image_generation_capability(
            config_data,
            {
                "enabled": True,
                "provider": "zhipu-image",
                "model": "glm-image",
                "credential_ref": credential_ref,
                "endpoint_values": {},
            },
            config_path=tmp_path / "config.yaml",
        )


def test_configure_image_capabilities_reuses_one_zhipu_credential_for_both_cards(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    credential_ref = "zhipu-shared"
    secret_env = model_config.credential_secret_env(credential_ref)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": credential_ref,
                        "provider_family": "zhipu",
                        "auth_type": "api_key",
                        "secret_env": secret_env,
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        f"{secret_env}=shared-zhipu-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        model_config,
        "_vision_provider_rows",
        lambda *_args, **_kwargs: [
            {
                "id": "zai",
                "name": "智谱 AI",
                "provider_family": "zhipu",
                "models": [{"id": "glm-5v-turbo", "label": "GLM-5V Turbo"}],
                "default_model": "glm-5v-turbo",
                "auth_type": "api_key",
                "credential_fields": [
                    {"name": "api_key", "secret": True}
                ],
                "endpoint_fields": [],
            }
        ],
    )
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda *_args, **_kwargs: [
            {
                "id": "zhipu-image",
                "name": "智谱 GLM-Image",
                "provider_family": "zhipu",
                "models": [{"id": "glm-image", "label": "GLM-Image"}],
                "default_model": "glm-image",
                "auth_type": "api_key",
                "credential_fields": [
                    {"name": "api_key", "secret": True}
                ],
                "endpoint_fields": [],
                "domestic": True,
                "integration_status": "stable",
                "policy_blocked": False,
            }
        ],
    )
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )

    result = model_config.configure_image_capabilities(
        _image_capability_configure_body(
            tmp_path,
            {
                "credential_updates": [],
                "capabilities": {
                    "vision": {
                        "enabled": True,
                        "provider": "zai",
                        "model": "glm-5v-turbo",
                        "credential_ref": credential_ref,
                        "endpoint_values": {},
                    },
                    "image_generation": {
                        "enabled": True,
                        "provider": "zhipu-image",
                        "model": "glm-image",
                        "credential_ref": credential_ref,
                        "endpoint_values": {},
                    },
                },
                "verify": [],
            },
            request_id="reuse-shared-zhipu",
        )
    )

    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"]["credential_ref"] == credential_ref
    assert saved["image_gen"]["credential_ref"] == credential_ref
    assert (
        (tmp_path / ".env").read_text(encoding="utf-8")
        == f"{secret_env}=shared-zhipu-secret\n"
    )
    assert "shared-zhipu-secret" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    ("config_data", "vision", "expected"),
    (
        (
            {
                "model": {
                    "provider": "openai",
                    "default": "gpt-4.1",
                    "supports_vision": True,
                }
            },
            {
                "enabled": True,
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "verification": {"status": "verified"},
            },
            {
                "route": "main_model_vision",
                "provider": "openai",
                "model": "gpt-4.1",
            },
        ),
        (
            {
                "model": {
                    "provider": "deepseek",
                    "default": "deepseek-chat",
                    "supports_vision": False,
                }
            },
            {
                "enabled": True,
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "verification": {"status": "verified"},
            },
            {
                "route": "auxiliary_vision",
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
            },
        ),
    ),
)
def test_effective_vision_route_reports_actual_runtime_path(
    config_data,
    vision,
    expected,
):
    assert model_config._effective_vision_route(config_data, vision) == expected


@pytest.mark.parametrize("enabled", [True, False])
def test_effective_vision_route_keeps_native_main_ahead_of_auxiliary(
    enabled,
):
    vision = {
        "enabled": enabled,
        "provider": "alibaba",
        "model": "qwen3-vl-plus",
        "verification": {"status": "verified"},
    }
    config_data = {
        "model": {
            "provider": "openai",
            "default": "gpt-4.1",
            "supports_vision": True,
        },
        "auxiliary": {"vision": dict(vision)},
    }

    assert model_config._effective_vision_route(config_data, vision) == {
        "route": "main_model_vision",
        "provider": "openai",
        "model": "gpt-4.1",
    }


def test_effective_image_generation_route_uses_same_public_snapshot():
    assert model_config._effective_image_generation_route(
        {
            "enabled": True,
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "verification": {"status": "verified"},
        }
    ) == {
        "route": "image_generation_provider",
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
    }


def test_disabled_vision_probe_has_no_verification_or_provider_io(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "auxiliary": {
                    "vision": {
                        "enabled": False,
                        "provider": "alibaba",
                        "model": "qwen3-vl-plus",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        model_config,
        "_begin_vision_probe",
        lambda *_args, **_kwargs: pytest.fail(
            "disabled vision must not write verification state"
        ),
    )

    result = model_config.test_vision_config()

    assert result["ok"] is False
    assert result["status"] == "disabled"
    assert result["error_code"] == "capability_disabled"
    assert not list(tmp_path.glob("*vision*verification*"))


def test_disabled_image_generation_probe_has_no_verification_or_provider_io(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "image_gen": {
                    "enabled": False,
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        model_config,
        "_begin_image_gen_probe",
        lambda *_args, **_kwargs: pytest.fail(
            "disabled image generation must not write verification state"
        ),
    )

    result = model_config.test_image_gen_config()

    assert result["ok"] is False
    assert result["status"] == "disabled"
    assert result["error_code"] == "capability_disabled"
    assert not list(tmp_path.glob("*image*verification*"))


def test_configure_image_capabilities_requires_revision_and_request_id_before_write(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected_revision"):
        model_config.configure_image_capabilities(
            {
                "capabilities": {
                    "vision": {
                        "enabled": False,
                        "provider": "",
                        "model": "",
                    }
                }
            }
        )

    with pytest.raises(ValueError, match="request_id"):
        model_config.configure_image_capabilities(
            {
                "expected_revision": model_config._image_capability_revision(
                    {}
                ),
                "capabilities": {
                    "vision": {
                        "enabled": False,
                        "provider": "",
                        "model": "",
                    }
                },
            }
        )
    assert _read_config(tmp_path) == {}


def test_configure_image_capabilities_rejects_stale_client_before_probe(
    monkeypatch,
    tmp_path,
):
    from hermes_cli.config import ConfigurationConflictError

    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )
    probe_calls = []
    monkeypatch.setattr(
        model_config,
        "test_image_gen_config",
        lambda **_kwargs: probe_calls.append("probe")
        or {"ok": True, "status": "verified"},
    )
    initial_revision = model_config._image_capability_revision({})
    payload = {
        "expected_revision": initial_revision,
        "capabilities": {
            "image_generation": {
                "enabled": True,
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "credential_ref": "",
                "endpoint_values": {},
            }
        },
        "credential_updates": [],
        "verify": ["image_generation"],
    }

    first = model_config.configure_image_capabilities(
        {**payload, "request_id": "stale-first"}
    )
    with pytest.raises(ConfigurationConflictError):
        model_config.configure_image_capabilities(
            {**payload, "request_id": "stale-second"}
        )

    assert first["revision"] != initial_revision
    assert probe_calls == ["probe"]


def test_configure_image_capabilities_reuses_idempotent_result_and_probe_once(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )
    probe_calls = []
    monkeypatch.setattr(
        model_config,
        "test_image_gen_config",
        lambda **_kwargs: probe_calls.append("probe")
        or {"ok": True, "status": "verified"},
    )
    body = {
        "expected_revision": model_config._image_capability_revision({}),
        "request_id": "idempotent-image-save",
        "capabilities": {
            "image_generation": {
                "enabled": True,
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "credential_ref": "",
                "endpoint_values": {},
            }
        },
        "credential_updates": [],
        "verify": ["image_generation"],
    }

    first = model_config.configure_image_capabilities(body)
    repeated = model_config.configure_image_capabilities(dict(body))

    assert repeated == first
    assert probe_calls == ["probe"]
    with pytest.raises(ValueError, match="different payload"):
        model_config.configure_image_capabilities(
            {
                **body,
                "verify": [],
            }
        )
    assert probe_calls == ["probe"]


def test_configure_image_capabilities_same_request_waiters_share_one_probe(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )
    probe_started = threading.Event()
    release_probe = threading.Event()
    probe_calls = []
    probe_calls_lock = threading.Lock()

    def blocking_probe(**_kwargs):
        with probe_calls_lock:
            probe_calls.append("probe")
        probe_started.set()
        assert release_probe.wait(timeout=10)
        return {
            "ok": True,
            "status": "verified",
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
        }

    monkeypatch.setattr(
        model_config,
        "test_image_gen_config",
        blocking_probe,
    )
    body = _image_capability_configure_body(
        tmp_path,
        {
            "capabilities": {
                "image_generation": {
                    "enabled": True,
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                    "credential_ref": "",
                    "endpoint_values": {},
                }
            },
            "credential_updates": [],
            "verify": ["image_generation"],
        },
        request_id="same-request-concurrent",
    )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        owner = executor.submit(
            model_config.configure_image_capabilities,
            body,
        )
        assert probe_started.wait(timeout=5)
        waiter = executor.submit(
            model_config.configure_image_capabilities,
            dict(body),
        )
        release_probe.set()
        owner_result = owner.result(timeout=10)
        waiter_result = waiter.result(timeout=10)
    finally:
        release_probe.set()
        executor.shutdown(wait=True)

    assert waiter_result == owner_result
    assert probe_calls == ["probe"]


def test_image_capability_request_cache_does_not_retain_exception_or_secret(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    secret_marker = "sk-cache-must-not-survive"
    owner_started = threading.Event()
    release_owner = threading.Event()

    def fail_once(_body):
        owner_started.set()
        assert release_owner.wait(timeout=10)
        raise ValueError(f"invalid credential {secret_marker}")

    monkeypatch.setattr(
        model_config,
        "_configure_image_capabilities_once",
        fail_once,
    )
    body = {
        "expected_revision": model_config._image_capability_revision({}),
        "request_id": "safe-exception-cache",
    }
    config_scope = str((tmp_path / "config.yaml").resolve(strict=False))
    cache_key = (config_scope, body["request_id"])
    with model_config._IMAGE_CAPABILITY_REQUEST_LOCK:
        saved_entries = list(model_config._IMAGE_CAPABILITY_REQUESTS.items())
        model_config._IMAGE_CAPABILITY_REQUESTS.clear()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        owner = executor.submit(
            model_config.configure_image_capabilities,
            body,
        )
        assert owner_started.wait(timeout=5)
        waiter = executor.submit(
            model_config.configure_image_capabilities,
            dict(body),
        )
        time.sleep(0.05)
        release_owner.set()
        with pytest.raises(ValueError) as owner_error:
            owner.result(timeout=10)
        with pytest.raises(ValueError) as waiter_error:
            waiter.result(timeout=10)

        with model_config._IMAGE_CAPABILITY_REQUEST_LOCK:
            cached_error = model_config._IMAGE_CAPABILITY_REQUESTS[
                cache_key
            ].error

        assert secret_marker in str(owner_error.value)
        assert secret_marker not in str(waiter_error.value)
        assert waiter_error.value is not owner_error.value
        assert not isinstance(cached_error, BaseException)
        assert secret_marker not in repr(cached_error)
        assert not hasattr(cached_error, "__traceback__")
    finally:
        release_owner.set()
        executor.shutdown(wait=True)
        with model_config._IMAGE_CAPABILITY_REQUEST_LOCK:
            model_config._IMAGE_CAPABILITY_REQUESTS.clear()
            model_config._IMAGE_CAPABILITY_REQUESTS.update(saved_entries)


def test_image_capability_request_cache_replays_stable_credential_error_code(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")

    def reject_collision(_body):
        raise model_config.ImageCapabilityCredentialError(
            "image_capability_credential_collision",
            "凭据 ID 已存在且不属于当前图片能力草稿，请刷新后重试。",
        )

    monkeypatch.setattr(
        model_config,
        "_configure_image_capabilities_once",
        reject_collision,
    )
    body = {
        "expected_revision": model_config._image_capability_revision({}),
        "request_id": "stable-credential-replay",
    }
    with model_config._IMAGE_CAPABILITY_REQUEST_LOCK:
        saved_entries = list(model_config._IMAGE_CAPABILITY_REQUESTS.items())
        model_config._IMAGE_CAPABILITY_REQUESTS.clear()

    try:
        with pytest.raises(
            model_config.ImageCapabilityCredentialError
        ) as owner_error:
            model_config.configure_image_capabilities(body)
        with pytest.raises(
            model_config.ImageCapabilityCredentialError
        ) as replayed_error:
            model_config.configure_image_capabilities(dict(body))

        assert owner_error.value.code == (
            "image_capability_credential_collision"
        )
        assert replayed_error.value.code == owner_error.value.code
        assert replayed_error.value is not owner_error.value
    finally:
        with model_config._IMAGE_CAPABILITY_REQUEST_LOCK:
            model_config._IMAGE_CAPABILITY_REQUESTS.clear()
            model_config._IMAGE_CAPABILITY_REQUESTS.update(saved_entries)


def test_configure_image_capabilities_same_revision_concurrent_writers_probe_once(
    monkeypatch,
    tmp_path,
):
    from hermes_cli.config import ConfigurationConflictError

    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )
    probe_calls = []
    monkeypatch.setattr(
        model_config,
        "test_image_gen_config",
        lambda **_kwargs: (
            probe_calls.append("probe")
            or {
                "ok": True,
                "status": "verified",
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
            }
        ),
    )
    real_configure_once = model_config._configure_image_capabilities_once
    both_owners_ready = threading.Barrier(2)

    def configure_once_after_both_owners(body):
        both_owners_ready.wait(timeout=10)
        return real_configure_once(body)

    monkeypatch.setattr(
        model_config,
        "_configure_image_capabilities_once",
        configure_once_after_both_owners,
    )
    payload = {
        "expected_revision": _current_image_capability_revision(tmp_path),
        "capabilities": {
            "image_generation": {
                "enabled": True,
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "credential_ref": "",
                "endpoint_values": {},
            }
        },
        "credential_updates": [],
        "verify": ["image_generation"],
    }

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [
            executor.submit(
                model_config.configure_image_capabilities,
                {
                    **payload,
                    "request_id": request_id,
                },
            )
            for request_id in (
                "same-revision-writer-a",
                "same-revision-writer-b",
            )
        ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except BaseException as exc:
                outcomes.append(exc)
    finally:
        executor.shutdown(wait=True)

    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert sum(isinstance(item, ConfigurationConflictError) for item in outcomes) == 1
    assert probe_calls == ["probe"]


def test_configure_image_capabilities_fails_closed_when_all_cache_entries_inflight(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    config_scope = str((tmp_path / "config.yaml").resolve(strict=False))
    with model_config._IMAGE_CAPABILITY_REQUEST_LOCK:
        saved_entries = list(model_config._IMAGE_CAPABILITY_REQUESTS.items())
        model_config._IMAGE_CAPABILITY_REQUESTS.clear()
        for index in range(2):
            model_config._IMAGE_CAPABILITY_REQUESTS[
                (config_scope, f"inflight-{index}")
            ] = model_config._ImageCapabilityRequestEntry(
                payload_digest=f"digest-{index}",
            )
    monkeypatch.setattr(
        model_config,
        "_IMAGE_CAPABILITY_REQUEST_CACHE_CAPACITY",
        2,
    )
    monkeypatch.setattr(
        model_config,
        "_configure_image_capabilities_once",
        lambda _body: pytest.fail(
            "capacity exhaustion must reject before configuration work"
        ),
    )
    body = {
        "expected_revision": _current_image_capability_revision(tmp_path),
        "request_id": "capacity-rejected-request",
        "capabilities": {
            "vision": {
                "enabled": False,
                "provider": "",
                "model": "",
            }
        },
        "credential_updates": [],
        "verify": [],
    }

    try:
        with pytest.raises(RuntimeError, match="capacity is exhausted"):
            model_config.configure_image_capabilities(body)
        with model_config._IMAGE_CAPABILITY_REQUEST_LOCK:
            assert list(model_config._IMAGE_CAPABILITY_REQUESTS) == [
                (config_scope, "inflight-0"),
                (config_scope, "inflight-1"),
            ]
            assert not any(
                entry.event.is_set()
                for entry in model_config._IMAGE_CAPABILITY_REQUESTS.values()
            )
    finally:
        with model_config._IMAGE_CAPABILITY_REQUEST_LOCK:
            model_config._IMAGE_CAPABILITY_REQUESTS.clear()
            model_config._IMAGE_CAPABILITY_REQUESTS.update(saved_entries)


@pytest.mark.parametrize(
    "mutation",
    ("main_model", "unused_secret"),
)
def test_image_capability_revision_changes_for_all_durable_configuration_state(
    monkeypatch,
    tmp_path,
    mutation,
):
    from hermes_cli.config import ConfigurationConflictError

    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    secret_env = "TAIJI_CREDENTIAL_UNUSED_API_KEY"
    initial_config = {
        "model": {
            "provider": "deepseek",
            "default": "deepseek-chat",
        },
        "provider_credentials": [
            {
                "id": "unused",
                "provider_family": "zhipu",
                "auth_type": "api_key",
                "secret_env": secret_env,
            }
        ],
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(initial_config, sort_keys=False),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        f"{secret_env}=secret-a\n",
        encoding="utf-8",
    )
    old_revision = _current_image_capability_revision(tmp_path)

    if mutation == "main_model":

        def change_main(config_data):
            config_data["model"]["default"] = "deepseek-reasoner"

        credential_store.mutate_config_env_strict(
            change_main,
            {},
            config_path=tmp_path / "config.yaml",
        )
    else:
        credential_store.mutate_config_env_strict(
            lambda _config_data: None,
            {secret_env: "secret-b"},
            config_path=tmp_path / "config.yaml",
        )

    assert _current_image_capability_revision(tmp_path) != old_revision
    with pytest.raises(ConfigurationConflictError):
        model_config.configure_image_capabilities(
            {
                "expected_revision": old_revision,
                "request_id": f"stale-after-{mutation}",
                "capabilities": {
                    "vision": {
                        "enabled": False,
                        "provider": "",
                        "model": "",
                    }
                },
                "credential_updates": [],
                "verify": [],
            }
        )


def test_get_image_capabilities_builds_routes_from_one_credential_snapshot(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    snapshot_config = {
        "model": {
            "provider": "deepseek",
            "default": "deepseek-chat",
            "supports_vision": False,
        },
        "auxiliary": {
            "vision": {
                "enabled": True,
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
            }
        },
        "image_gen": {
            "enabled": True,
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
        },
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(snapshot_config, sort_keys=False),
        encoding="utf-8",
    )
    captured_snapshot = SimpleNamespace(
        config=snapshot_config,
        env_sha256=hashlib.sha256(b"").hexdigest(),
    )
    monkeypatch.setattr(
        model_config,
        "load_credential_snapshot",
        lambda _path: captured_snapshot,
    )
    seen_config_objects = []

    def vision_payload(*, refresh_runtime, config_data):
        assert refresh_runtime is False
        seen_config_objects.append(config_data)
        return {
            "vision": {
                **snapshot_config["auxiliary"]["vision"],
                "verification": {"status": "verified"},
            },
            "providers": [],
        }

    def image_payload(*, refresh_runtime, config_data):
        assert refresh_runtime is False
        seen_config_objects.append(config_data)
        return {
            "image_gen": {
                **snapshot_config["image_gen"],
                "verification": {"status": "verified"},
            },
            "providers": [],
        }

    monkeypatch.setattr(
        model_config,
        "_get_vision_config_unlocked",
        vision_payload,
    )
    monkeypatch.setattr(
        model_config,
        "_get_image_gen_config_unlocked",
        image_payload,
    )

    result = model_config.get_image_capabilities()

    assert seen_config_objects == [snapshot_config, snapshot_config]
    assert result["effective_route"]["vision"] == {
        "route": "auxiliary_vision",
        "provider": "alibaba",
        "model": "qwen3-vl-plus",
    }
    assert result["effective_route"]["image_generation"] == {
        "route": "image_generation_provider",
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
    }


def test_newer_config_commit_supersedes_old_probe_before_provider_io(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    image_rows = model_config._image_gen_provider_rows("dashscope")
    image_rows[0]["models"].append({"id": "qwen-image", "label": "Qwen Image"})
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda *_args, **_kwargs: image_rows,
    )
    old_commit_waiting = threading.Event()
    release_old_after_new = threading.Event()
    hook_calls = {"value": 0}
    hook_lock = threading.Lock()

    def pause_first_after_commit(*_args, **_kwargs):
        with hook_lock:
            hook_calls["value"] += 1
            call_number = hook_calls["value"]
        if call_number == 1:
            old_commit_waiting.set()
            assert release_old_after_new.wait(timeout=10)
        return []

    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        pause_first_after_commit,
    )
    provider_calls = []

    def provider_probe(*, snapshot):
        provider_calls.append(snapshot.model)
        return {
            "ok": True,
            "status": "verified",
            "provider": snapshot.provider,
            "model": snapshot.model,
        }

    monkeypatch.setattr(
        model_config,
        "test_image_gen_config",
        provider_probe,
    )
    base_payload = {
        "capabilities": {
            "image_generation": {
                "enabled": True,
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "credential_ref": "",
                "endpoint_values": {},
            }
        },
        "credential_updates": [],
        "verify": ["image_generation"],
    }
    old_body = _image_capability_configure_body(
        tmp_path,
        base_payload,
        request_id="superseded-probe-old",
    )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        old_future = executor.submit(
            model_config.configure_image_capabilities,
            old_body,
        )
        assert old_commit_waiting.wait(timeout=5)
        new_body = _image_capability_configure_body(
            tmp_path,
            {
                **base_payload,
                "capabilities": {
                    "image_generation": {
                        **base_payload["capabilities"]["image_generation"],
                        "model": "qwen-image",
                    }
                },
            },
            request_id="superseded-probe-new",
        )
        new_result = model_config.configure_image_capabilities(new_body)
        release_old_after_new.set()
        old_result = old_future.result(timeout=10)
    finally:
        release_old_after_new.set()
        executor.shutdown(wait=True)

    assert provider_calls == ["qwen-image"]
    assert new_result["request_status"] == "applied"
    assert new_result["verification_results"]["image_generation"] == {
        "ok": True,
        "status": "verified",
        "provider": "dashscope",
        "model": "qwen-image",
    }
    assert old_result["request_status"] == "superseded"
    assert (
        old_result["verification_results"]["image_generation"]["status"] == "superseded"
    )
    assert (
        old_result["verification_results"]["image_generation"]["error_code"]
        == "image_gen_probe_superseded"
    )
    assert (
        old_result["verification_results"]["image_generation"]["model"]
        == "qwen-image-2.0-pro"
    )
    assert old_result["committed_revision"] != old_result["revision"]


def test_committed_probe_never_retargets_after_legacy_cross_process_write(
    monkeypatch,
    tmp_path,
):
    """R1 must retain its commit-time target after the revision lock is released."""
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=r1-private-key\n",
        encoding="utf-8",
    )
    image_rows = model_config._image_gen_provider_rows("dashscope")
    image_rows[0]["models"].append(
        {"id": "qwen-image", "label": "Qwen Image"}
    )
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda *_args, **_kwargs: image_rows,
    )
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )

    context = multiprocessing.get_context("spawn")
    probe_entered = context.Event()
    writer_completed = context.Event()
    writer_result = context.Queue()
    writer = context.Process(
        target=_legacy_image_config_process_writer,
        args=(
            str(tmp_path / "config.yaml"),
            "qwen-image",
            "r2-private-key",
            probe_entered,
            writer_completed,
            writer_result,
        ),
    )
    observed = {}
    real_test_image_gen_config = model_config.test_image_gen_config

    def probe_after_exact_revision_barrier(*, snapshot=None):
        probe_entered.set()
        assert writer_completed.wait(timeout=10)
        if snapshot is None:
            return real_test_image_gen_config()
        return real_test_image_gen_config(snapshot=snapshot)

    def capture_provider_boundary(
        snapshot,
        *,
        diagnostic_id,
        reauth_guard,
        probe_binding=None,
    ):
        observed.update(
            snapshot_model=snapshot.model,
            snapshot_fingerprint=snapshot.fingerprint,
            runtime_binding=probe_binding,
            runtime_binding_model=(
                probe_binding.model if probe_binding is not None else ""
            ),
            runtime_binding_api_key=(
                probe_binding.api_key if probe_binding is not None else ""
            ),
            authorization_fingerprint=(
                probe_binding.authorization_fingerprint
                if probe_binding is not None
                else ""
            ),
            authorization_generation=(
                probe_binding.authorization_generation
                if probe_binding is not None
                else ""
            ),
        )
        return True, "", "真实生图验证通过。"

    monkeypatch.setattr(
        model_config,
        "test_image_gen_config",
        probe_after_exact_revision_barrier,
    )
    monkeypatch.setattr(
        model_config,
        "_execute_image_gen_probe",
        capture_provider_boundary,
    )
    body = _image_capability_configure_body(
        tmp_path,
        {
            "capabilities": {
                "image_generation": {
                    "enabled": True,
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                    "credential_ref": "",
                    "endpoint_values": {},
                }
            },
            "credential_updates": [],
            "verify": ["image_generation"],
        },
        request_id="immutable-r1-probe-target",
    )

    writer.start()
    try:
        result = model_config.configure_image_capabilities(body)
        assert writer_result.get(timeout=10) == ("ok", "qwen-image")
    finally:
        probe_entered.set()
        writer_completed.set()
        writer.join(timeout=10)
        if writer.is_alive():
            writer.terminate()
            writer.join(timeout=5)

    assert writer.exitcode == 0
    assert _read_config(tmp_path)["image_gen"]["model"] == "qwen-image"
    assert "DASHSCOPE_API_KEY=r2-private-key" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")
    assert observed["snapshot_model"] == "qwen-image-2.0-pro"
    assert observed["runtime_binding_model"] == "qwen-image-2.0-pro"
    assert observed["runtime_binding_api_key"] == "r1-private-key"
    assert observed["authorization_fingerprint"] == (
        observed["snapshot_fingerprint"]
    )
    assert observed["authorization_generation"]
    assert result["request_status"] == "superseded"
    assert (
        result["verification_results"]["image_generation"]["model"]
        == "qwen-image-2.0-pro"
    )


def test_profile_probe_blocks_same_profile_commit_without_blocking_other_profile_read(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    image_rows = model_config._image_gen_provider_rows("dashscope")
    image_rows[0]["models"].append({"id": "qwen-image", "label": "Qwen Image"})
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda *_args, **_kwargs: image_rows,
    )
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )
    first_probe_started = threading.Event()
    release_first_probe = threading.Event()
    provider_calls = []
    provider_calls_lock = threading.Lock()

    def blocking_first_provider_probe(*, snapshot):
        with provider_calls_lock:
            provider_calls.append(snapshot.model)
            call_number = len(provider_calls)
        if call_number == 1:
            first_probe_started.set()
            assert release_first_probe.wait(timeout=10)
        return {
            "ok": True,
            "status": "verified",
            "provider": snapshot.provider,
            "model": snapshot.model,
        }

    monkeypatch.setattr(
        model_config,
        "test_image_gen_config",
        blocking_first_provider_probe,
    )
    real_commit = model_config._commit_expected_config_env
    second_commit_entered = threading.Event()
    commit_calls = {"value": 0}
    commit_calls_lock = threading.Lock()

    def record_commit(*args, **kwargs):
        with commit_calls_lock:
            commit_calls["value"] += 1
            call_number = commit_calls["value"]
        if call_number == 2:
            second_commit_entered.set()
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(
        model_config,
        "_commit_expected_config_env",
        record_commit,
    )
    first_payload = {
        "capabilities": {
            "image_generation": {
                "enabled": True,
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "credential_ref": "",
                "endpoint_values": {},
            }
        },
        "credential_updates": [],
        "verify": ["image_generation"],
    }
    first_body = _image_capability_configure_body(
        tmp_path,
        first_payload,
        request_id="profile-lock-first",
    )
    config_path = tmp_path / "config.yaml"

    def read_from_other_profile():
        with model_config._image_capability_probe_lock(
            config_path,
            "other-profile",
        ):
            return credential_store.load_credential_snapshot(config_path)

    executor = ThreadPoolExecutor(max_workers=3)
    try:
        first_future = executor.submit(
            model_config.configure_image_capabilities,
            first_body,
        )
        assert first_probe_started.wait(timeout=5)
        other_profile_snapshot = executor.submit(read_from_other_profile).result(
            timeout=2
        )
        second_body = _image_capability_configure_body(
            tmp_path,
            {
                **first_payload,
                "capabilities": {
                    "image_generation": {
                        **first_payload["capabilities"]["image_generation"],
                        "model": "qwen-image",
                    }
                },
            },
            request_id="profile-lock-second",
        )
        second_future = executor.submit(
            model_config.configure_image_capabilities,
            second_body,
        )
        same_profile_commit_waited = not second_commit_entered.wait(timeout=0.3)
        config_while_waiting = _read_config(tmp_path)
        release_first_probe.set()
        first_result = first_future.result(timeout=10)
        second_result = second_future.result(timeout=10)
    finally:
        release_first_probe.set()
        executor.shutdown(wait=True)

    assert other_profile_snapshot.config["image_gen"]["model"] == ("qwen-image-2.0-pro")
    assert same_profile_commit_waited is True
    assert config_while_waiting["image_gen"]["model"] == ("qwen-image-2.0-pro")
    assert (
        first_result["verification_results"]["image_generation"]["status"] == "verified"
    )
    assert (
        first_result["verification_results"]["image_generation"]["model"]
        == "qwen-image-2.0-pro"
    )
    assert second_result["request_status"] == "applied"
    assert provider_calls == [
        "qwen-image-2.0-pro",
        "qwen-image",
    ]


def test_configure_image_capabilities_commits_config_and_secret_once_then_verifies(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    commit_calls = []
    real_commit = model_config._commit_expected_config_env

    def record_commit(*args, **kwargs):
        commit_calls.append((args, kwargs))
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(model_config, "_commit_expected_config_env", record_commit)
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        model_config,
        "test_vision_config",
        lambda **_kwargs: {"ok": True, "status": "verified"},
    )
    monkeypatch.setattr(
        model_config,
        "test_image_gen_config",
        lambda **_kwargs: {"ok": True, "status": "verified"},
    )

    result = model_config.configure_image_capabilities(
        _image_capability_configure_body(
            tmp_path,
            {
            "credential_updates": [
                {
                    "id": "image-center-vision",
                    "provider_family": "alibaba",
                    "label": "图片能力识图凭据",
                    "api_key": "vision-secret-value",
                    "operation": "create",
                    "managed_by": "image-capability-center",
                    "source_capability": "vision",
                    "source_provider_id": "alibaba",
                },
                {
                    "id": "image-center-generation",
                    "provider_family": "alibaba",
                    "label": "图片能力生图凭据",
                    "api_key": "image-secret-value",
                    "operation": "create",
                    "managed_by": "image-capability-center",
                    "source_capability": "image_generation",
                    "source_provider_id": "dashscope",
                },
            ],
            "capabilities": {
                "vision": {
                    "enabled": True,
                    "provider": "alibaba",
                    "model": "qwen3-vl-plus",
                    "credential_ref": "image-center-vision",
                    "endpoint_values": {},
                },
                "image_generation": {
                    "enabled": True,
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                    "credential_ref": "image-center-generation",
                    "endpoint_values": {},
                },
            },
            "verify": ["vision", "image_generation"],
            },
            request_id="commit-secret-once",
        )
    )

    assert len(commit_calls) == 1
    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"]["enabled"] is True
    assert saved["auxiliary"]["vision"]["credential_ref"] == "image-center-vision"
    assert saved["image_gen"]["enabled"] is True
    assert (
        saved["image_gen"]["credential_ref"]
        == "image-center-generation"
    )
    credential_rows = {
        row["id"]: row for row in saved["provider_credentials"]
    }
    assert credential_rows["image-center-vision"]["managed_by"] == (
        "image-capability-center"
    )
    assert credential_rows["image-center-vision"]["source_capability"] == (
        "vision"
    )
    assert credential_rows["image-center-generation"]["source_capability"] == (
        "image_generation"
    )
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "vision-secret-value" in env_text
    assert "image-secret-value" in env_text
    assert not list(tmp_path.glob("*.bak"))
    assert result["verification_results"] == {
        "vision": {"ok": True, "status": "verified"},
        "image_generation": {"ok": True, "status": "verified"},
    }
    assert "vision-secret-value" not in json.dumps(result, ensure_ascii=False)
    assert "image-secret-value" not in json.dumps(result, ensure_ascii=False)


def test_image_capability_center_rejects_non_owned_credential_id_collision(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    original = {
        "provider_credentials": [
            {
                "id": "ui-alibaba-dashscope-vision-collision",
                "provider_family": "alibaba_dashscope",
                "label": "平台共享凭据",
                "auth_type": "api_key",
                "secret_env": (
                    "TAIJI_CREDENTIAL_UI_ALIBABA_DASHSCOPE_VISION_COLLISION_API_KEY"
                ),
            }
        ]
    }
    original_env = (
        "TAIJI_CREDENTIAL_UI_ALIBABA_DASHSCOPE_VISION_COLLISION_API_KEY="
        "keep-existing\n"
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(original, sort_keys=False),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(original_env, encoding="utf-8")

    with pytest.raises(ValueError) as raised:
        model_config.configure_image_capabilities(
            _image_capability_configure_body(
                tmp_path,
                {
                    "credential_updates": [
                        {
                            "id": "ui-alibaba-dashscope-vision-collision",
                            "provider_family": "alibaba_dashscope",
                            "api_key": "must-not-overwrite",
                            "operation": "create",
                            "managed_by": "image-capability-center",
                            "source_capability": "vision",
                            "source_provider_id": "alibaba",
                        }
                    ],
                    "capabilities": {
                        "vision": {
                            "enabled": True,
                            "provider": "alibaba",
                            "model": "qwen3-vl-plus",
                            "credential_ref": (
                                "ui-alibaba-dashscope-vision-collision"
                            ),
                            "endpoint_values": {},
                        }
                    },
                    "verify": [],
                },
                request_id="credential-id-collision",
            )
        )

    assert getattr(raised.value, "code", "") == (
        "image_capability_credential_collision"
    )
    assert _read_config(tmp_path) == original
    assert (tmp_path / ".env").read_text(encoding="utf-8") == original_env


def test_image_capability_center_rejects_shared_owned_credential_retry(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    credential_id = "image-center-shared"
    secret_env = "TAIJI_CREDENTIAL_IMAGE_CENTER_SHARED_API_KEY"
    original = {
        "provider_credentials": [
            {
                "id": credential_id,
                "provider_family": "alibaba_dashscope",
                "label": "中心旧凭据",
                "auth_type": "api_key",
                "secret_env": secret_env,
                "managed_by": "image-capability-center",
                "source_capability": "vision",
                "source_provider_id": "alibaba",
            }
        ],
        "auxiliary": {
            "vision": {
                "enabled": True,
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "credential_ref": credential_id,
            }
        },
        "image_gen": {
            "enabled": True,
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "credential_ref": credential_id,
        },
    }
    original_env = f"{secret_env}=keep-existing\n"
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(original, sort_keys=False),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(original_env, encoding="utf-8")

    with pytest.raises(ValueError) as raised:
        model_config.configure_image_capabilities(
            _image_capability_configure_body(
                tmp_path,
                {
                    "credential_updates": [
                        {
                            "id": credential_id,
                            "provider_family": "alibaba_dashscope",
                            "api_key": "must-not-overwrite",
                            "operation": "create",
                            "managed_by": "image-capability-center",
                            "source_capability": "vision",
                            "source_provider_id": "alibaba",
                        }
                    ],
                    "capabilities": {
                        "vision": {
                            "enabled": True,
                            "provider": "alibaba",
                            "model": "qwen3-vl-plus",
                            "credential_ref": credential_id,
                            "endpoint_values": {},
                        }
                    },
                    "verify": [],
                },
                request_id="shared-credential-retry",
            )
        )

    assert getattr(raised.value, "code", "") == (
        "image_capability_credential_shared"
    )
    assert _read_config(tmp_path) == original
    assert (tmp_path / ".env").read_text(encoding="utf-8") == original_env


def test_image_capability_center_allows_owned_unshared_idempotent_retry(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )
    credential_id = "image-center-vision-retry"
    secret_env = "TAIJI_CREDENTIAL_IMAGE_CENTER_VISION_RETRY_API_KEY"
    original = {
        "provider_credentials": [
            {
                "id": credential_id,
                "provider_family": "alibaba_dashscope",
                "label": "中心识图凭据",
                "auth_type": "api_key",
                "secret_env": secret_env,
                "managed_by": "image-capability-center",
                "source_capability": "vision",
                "source_provider_id": "alibaba",
            }
        ],
        "auxiliary": {
            "vision": {
                "enabled": True,
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "credential_ref": credential_id,
            }
        },
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(original, sort_keys=False),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        f"{secret_env}=old-secret\n",
        encoding="utf-8",
    )

    result = model_config.configure_image_capabilities(
        _image_capability_configure_body(
            tmp_path,
            {
                "credential_updates": [
                    {
                        "id": credential_id,
                        "provider_family": "alibaba_dashscope",
                        "label": "中心识图凭据",
                        "api_key": "retried-secret",
                        "operation": "create",
                        "managed_by": "image-capability-center",
                        "source_capability": "vision",
                        "source_provider_id": "alibaba",
                    }
                ],
                "capabilities": {
                    "vision": {
                        "enabled": True,
                        "provider": "alibaba",
                        "model": "qwen3-vl-plus",
                        "credential_ref": credential_id,
                        "endpoint_values": {},
                    }
                },
                "verify": [],
            },
            request_id="owned-credential-retry",
        )
    )

    saved_row = _read_config(tmp_path)["provider_credentials"][0]
    assert saved_row["managed_by"] == "image-capability-center"
    assert saved_row["source_capability"] == "vision"
    assert saved_row["source_provider_id"] == "alibaba"
    assert "retried-secret" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert result["request_status"] == "applied"


def test_configure_image_capabilities_keeps_save_success_when_probe_crashes(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        model_config,
        "test_vision_config",
        lambda **_kwargs: (_ for _ in ()).throw(
            PermissionError("/private/runtime/secret-state.json")
        ),
    )

    result = model_config.configure_image_capabilities(
        _image_capability_configure_body(
            tmp_path,
            {
            "credential_updates": [
                {
                    "id": "alibaba-shared",
                    "provider_family": "alibaba",
                    "api_key": "probe-crash-secret",
                    "operation": "create",
                    "managed_by": "image-capability-center",
                    "source_capability": "vision",
                    "source_provider_id": "alibaba",
                }
            ],
            "capabilities": {
                "vision": {
                    "enabled": True,
                    "provider": "alibaba",
                    "model": "qwen3-vl-plus",
                    "credential_ref": "alibaba-shared",
                    "endpoint_values": {},
                }
            },
            "verify": ["vision"],
            },
            request_id="probe-crash-save",
        )
    )

    assert result["ok"] is True
    assert result["verification_results"]["vision"]["ok"] is False
    assert (
        result["verification_results"]["vision"]["error_code"]
        == "verification_internal_error"
    )
    assert "vision_verification_failed_after_save" in result["warnings"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "/private/runtime" not in serialized
    assert "probe-crash-secret" not in serialized
    assert _read_config(tmp_path)["auxiliary"]["vision"]["enabled"] is True
    assert "probe-crash-secret" in (tmp_path / ".env").read_text(
        encoding="utf-8"
    )


def test_configure_image_capabilities_rejects_unknown_capability_before_write(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    original = {"unrelated": {"keep": True}}
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(original),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown capability"):
        model_config.configure_image_capabilities(
            _image_capability_configure_body(
                tmp_path,
                {
                "credential_updates": [
                    {
                        "id": "alibaba-shared",
                        "provider_family": "alibaba",
                        "api_key": "must-not-be-written",
                    }
                ],
                "capabilities": {
                    "image_gen_typo": {
                        "enabled": True,
                        "provider": "dashscope",
                        "model": "qwen-image-2.0-pro",
                    }
                },
                "verify": [],
                },
                request_id="unknown-capability",
            )
        )

    assert _read_config(tmp_path) == original
    assert not (tmp_path / ".env").exists()


def test_configure_image_capabilities_rejects_credential_only_verify(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="verify requires the capability to be included",
    ):
        model_config.configure_image_capabilities(
            _image_capability_configure_body(
                tmp_path,
                {
                "credential_updates": [
                    {
                        "id": "alibaba-shared",
                        "provider_family": "alibaba",
                        "api_key": "must-not-be-written",
                    }
                ],
                "capabilities": {},
                "verify": ["vision"],
                },
                request_id="credential-only-verify",
            )
        )

    assert _read_config(tmp_path) == {}
    assert not (tmp_path / ".env").exists()


def test_configure_image_capabilities_cas_failure_leaves_both_files_untouched(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _image_capability_test_provider_rows(monkeypatch)
    original_config = {"unrelated": {"keep": True}}
    original_env = "EXISTING_SECRET=keep-me\n"
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(original_config),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(original_env, encoding="utf-8")
    monkeypatch.setattr(
        model_config,
        "_commit_expected_config_env",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("credential config changed before the paired update")
        ),
    )

    with pytest.raises(RuntimeError, match="credential config changed"):
        model_config.configure_image_capabilities(
            _image_capability_configure_body(
                tmp_path,
                {
                "credential_updates": [
                    {
                        "id": "alibaba-shared",
                        "provider_family": "alibaba",
                        "api_key": "must-not-survive",
                        "operation": "create",
                        "managed_by": "image-capability-center",
                        "source_capability": "vision",
                        "source_provider_id": "alibaba",
                    }
                ],
                "capabilities": {
                    "vision": {
                        "enabled": True,
                        "provider": "alibaba",
                        "model": "qwen3-vl-plus",
                        "credential_ref": "alibaba-shared",
                        "endpoint_values": {},
                    },
                },
                "verify": [],
                },
                request_id="paired-cas-failure",
            )
        )

    assert _read_config(tmp_path) == original_config
    assert (tmp_path / ".env").read_text(encoding="utf-8") == original_env
    assert not list(tmp_path.glob("*.bak"))


def test_configure_image_capabilities_can_disable_stale_providers_without_revalidation(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr(
        model_config,
        "_vision_provider_rows",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        lambda *_args, **_kwargs: [],
    )
    original = {
        "auxiliary": {
            "vision": {
                "provider": "removed-vision-provider",
                "model": "legacy-vision-model",
            }
        },
        "image_gen": {
            "provider": "fal",
            "model": "legacy-image-model",
        },
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(original),
        encoding="utf-8",
    )

    model_config.configure_image_capabilities(
        _image_capability_configure_body(
            tmp_path,
            {
            "capabilities": {
                "vision": {
                    "enabled": False,
                    "provider": "removed-vision-provider",
                    "model": "legacy-vision-model",
                },
                "image_generation": {
                    "enabled": False,
                    "provider": "fal",
                    "model": "legacy-image-model",
                },
            },
            "credential_updates": [],
            "verify": [],
            },
            request_id="disable-stale-providers",
        )
    )

    saved = _read_config(tmp_path)
    assert saved["auxiliary"]["vision"] == {
        **original["auxiliary"]["vision"],
        "enabled": False,
    }
    assert saved["image_gen"] == {
        **original["image_gen"],
        "enabled": False,
    }


def test_configure_image_capabilities_rejects_non_boolean_enabled():
    with pytest.raises(ValueError, match="vision.enabled must be a boolean"):
        model_config.configure_image_capabilities(
            {
                "expected_revision": model_config._image_capability_revision(
                    {}
                ),
                "request_id": "non-boolean-enabled",
                "capabilities": {
                    "vision": {
                        "enabled": "false",
                        "provider": "",
                        "model": "",
                    }
                }
            }
        )


def test_image_capabilities_routes_are_registered():
    source = Path(routes.__file__).read_text(encoding="utf-8")
    assert 'parsed.path == "/api/image-capabilities"' in source
    assert 'parsed.path == "/api/image-capabilities/configure"' in source


def test_image_capabilities_configure_route_redacts_oserror(monkeypatch):
    responses = []
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {})
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: responses.append(
            (payload, status)
        )
        or True,
    )
    monkeypatch.setattr(
        model_config,
        "configure_image_capabilities",
        lambda _body: (_ for _ in ()).throw(
            OSError("/private/runtime/secret-config.yaml")
        ),
    )

    assert routes.handle_post(
        object(),
        SimpleNamespace(path="/api/image-capabilities/configure"),
    ) is True
    payload, status = responses[0]
    assert status == 500
    assert payload["error_code"] == "configuration_io_error"
    assert len(payload["diagnostic_id"]) == 32
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "/private/runtime" not in serialized
    assert "secret-config" not in serialized


def test_image_capabilities_configure_route_returns_stable_credential_error(
    monkeypatch,
):
    responses = []
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {})
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: responses.append(
            (payload, status)
        )
        or True,
    )
    monkeypatch.setattr(
        model_config,
        "configure_image_capabilities",
        lambda _body: (_ for _ in ()).throw(
            model_config.ImageCapabilityCredentialError(
                "image_capability_credential_collision",
                "credential collision",
            )
        ),
    )

    assert routes.handle_post(
        object(),
        SimpleNamespace(path="/api/image-capabilities/configure"),
    ) is True
    payload, status = responses[0]
    assert status == 409
    assert payload == {
        "error": "credential collision",
        "error_code": "image_capability_credential_collision",
    }


_B3_VERIFICATION_SCHEMA_VERSION = 1


def _b3_verification_fixture(
    monkeypatch,
    tmp_path,
    capability,
    *,
    endpoint_env="",
):
    """Build a real WebUI probe/public-projection seam for B3 RED tests."""
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / f"{capability}-verification.json"
    config_path = tmp_path / "config.yaml"
    endpoint = f"${{{endpoint_env}}}" if endpoint_env else ""

    if capability == "vision":
        vision = {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
        }
        if endpoint:
            vision.update(
                {
                    "endpoint_mode": "custom",
                    "base_url": endpoint,
                }
            )
        config_path.write_text(
            yaml.safe_dump({"auxiliary": {"vision": vision}}),
            encoding="utf-8",
        )
        (tmp_path / ".env").write_text(
            "DASHSCOPE_API_KEY=b3-vision-key\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            model_config,
            "_vision_verification_state_path",
            lambda *_: state_path,
        )

        async def vision_probe(**_kwargs):
            return json.dumps(
                {
                    "success": True,
                    "analysis": "TAIJI-VISION-CHECK-7319",
                    "resolved_provider": "alibaba",
                    "resolved_model": "qwen3-vl-plus",
                }
            )

        import tools.vision_tools as vision_tools

        monkeypatch.setattr(vision_tools, "vision_analyze_tool", vision_probe)
        return {
            "state_path": state_path,
            "config_path": config_path,
            "verify": model_config.test_vision_config,
            "public_status": lambda: model_config.get_vision_config()["vision"][
                "verification"
            ]["status"],
            "runtime_endpoint": lambda cfg: cfg["auxiliary"]["vision"]["base_url"],
        }

    image_cfg = {
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
    }
    if endpoint:
        image_cfg["options"] = {
            "endpoint_mode": "custom",
            "base_url": endpoint,
        }
    config_path.write_text(
        yaml.safe_dump({"image_gen": image_cfg}),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=b3-image-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_: state_path,
    )
    generated = tmp_path / "cache" / "images" / "b3-probe.png"

    def image_probe():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nb3")
        return {
            "success": True,
            "image": str(generated),
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
        }

    _install_probe_provider(
        monkeypatch,
        _ProbeImageProvider(image_probe),
    )
    return {
        "state_path": state_path,
        "config_path": config_path,
        "verify": model_config.test_image_gen_config,
        "public_status": lambda: model_config.get_image_gen_config()["image_gen"][
            "verification"
        ]["status"],
        "runtime_endpoint": lambda cfg: cfg["image_gen"]["options"]["base_url"],
    }


@pytest.mark.parametrize("capability", ("vision", "image"))
def test_superseded_probe_compare_delete_preserves_new_generation(
    monkeypatch,
    tmp_path,
    capability,
):
    seam = _b3_verification_fixture(monkeypatch, tmp_path, capability)
    first_started = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    call_count = {"value": 0}

    def next_call_number():
        with call_lock:
            call_count["value"] += 1
            return call_count["value"]

    if capability == "vision":
        async def ordered_vision_probe(**_kwargs):
            call_number = next_call_number()
            if call_number == 1:
                first_started.set()
                assert release_first.wait(timeout=5)
            return json.dumps(
                {
                    "success": True,
                    "analysis": "TAIJI-VISION-CHECK-7319",
                    "resolved_provider": "alibaba",
                    "resolved_model": "qwen3-vl-plus",
                }
            )

        import tools.vision_tools as vision_tools

        monkeypatch.setattr(
            vision_tools,
            "vision_analyze_tool",
            ordered_vision_probe,
        )
        superseded_error = "vision_probe_superseded"
    else:
        def ordered_image_probe():
            call_number = next_call_number()
            if call_number == 1:
                first_started.set()
                assert release_first.wait(timeout=5)
            generated = (
                tmp_path
                / "cache"
                / "images"
                / f"generation-{call_number}.png"
            )
            generated.parent.mkdir(parents=True, exist_ok=True)
            generated.write_bytes(b"\x89PNG\r\n\x1a\ngeneration")
            return {
                "success": True,
                "image": str(generated),
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
            }

        _install_probe_provider(
            monkeypatch,
            _ProbeImageProvider(ordered_image_probe),
        )
        superseded_error = "image_gen_probe_superseded"

    results = {}
    first = threading.Thread(
        target=lambda: results.setdefault("first", seam["verify"]())
    )
    first.start()
    assert first_started.wait(timeout=5)
    results["second"] = seam["verify"]()
    state_after_second = json.loads(
        seam["state_path"].read_text(encoding="utf-8")
    )
    release_first.set()
    first.join(timeout=5)
    final_state = json.loads(
        seam["state_path"].read_text(encoding="utf-8")
    )

    assert not first.is_alive()
    assert results["second"]["status"] == "verified"
    assert results["first"]["error_code"] == superseded_error
    assert state_after_second["status"] == "verified"
    assert (
        state_after_second["diagnostic_id"]
        == results["second"]["diagnostic_id"]
    )
    assert final_state == state_after_second
    assert call_count["value"] == 2


def _b3_rewrite_effective_endpoint(config_path, capability, endpoint, nonce):
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if capability == "vision":
        config["auxiliary"]["vision"]["base_url"] = endpoint
    else:
        config["image_gen"]["options"]["base_url"] = endpoint
    config["b3_reload_nonce"] = nonce
    config_path.write_text(
        yaml.safe_dump(config),
        encoding="utf-8",
    )


@pytest.mark.parametrize("capability", ("vision", "image"))
def test_webui_verification_state_requires_current_schema_version(
    monkeypatch,
    tmp_path,
    capability,
):
    seam = _b3_verification_fixture(monkeypatch, tmp_path, capability)

    verified = seam["verify"]()
    assert verified["status"] == "verified"
    persisted = json.loads(seam["state_path"].read_text(encoding="utf-8"))

    violations = []
    if persisted.get("schema_version") != _B3_VERIFICATION_SCHEMA_VERSION:
        violations.append(
            "probe writer omitted current schema_version=1"
        )
    for label, schema_version in (
        ("missing", None),
        ("old", _B3_VERIFICATION_SCHEMA_VERSION - 1),
        ("unknown_new", _B3_VERIFICATION_SCHEMA_VERSION + 1),
    ):
        candidate = dict(persisted)
        if schema_version is None:
            candidate.pop("schema_version", None)
        else:
            candidate["schema_version"] = schema_version
        seam["state_path"].write_text(
            json.dumps(candidate),
            encoding="utf-8",
        )
        if seam["public_status"]() == "verified":
            violations.append(f"{label} schema inherited verified")

    current = dict(persisted)
    current["schema_version"] = _B3_VERIFICATION_SCHEMA_VERSION
    seam["state_path"].write_text(
        json.dumps(current),
        encoding="utf-8",
    )
    assert seam["public_status"]() == "verified"
    assert violations == [], "; ".join(violations)


@pytest.mark.parametrize("capability", ("vision", "image"))
def test_webui_effective_fingerprint_expands_env_or_fails_unresolved(
    monkeypatch,
    tmp_path,
    capability,
):
    endpoint_env = f"B3_{capability.upper()}_ENDPOINT"
    placeholder = f"${{{endpoint_env}}}"
    endpoint_suffix = "/v1" if capability == "vision" else ""
    endpoint_a = f"https://{capability}-a.example.test{endpoint_suffix}"
    endpoint_b = f"https://{capability}-b.example.test{endpoint_suffix}"
    monkeypatch.setenv(endpoint_env, endpoint_a)
    seam = _b3_verification_fixture(
        monkeypatch,
        tmp_path,
        capability,
        endpoint_env=endpoint_env,
    )

    verified = seam["verify"]()
    assert verified["status"] == "verified"

    from hermes_cli.config import load_config

    violations = []
    _b3_rewrite_effective_endpoint(
        seam["config_path"],
        capability,
        endpoint_a,
        "same-effective-endpoint",
    )
    runtime_config = load_config()
    assert seam["runtime_endpoint"](runtime_config) == endpoint_a
    if seam["public_status"]() != "verified":
        violations.append(
            "placeholder and its runtime-expanded endpoint produced different fingerprints"
        )

    monkeypatch.setenv(endpoint_env, endpoint_b)
    _b3_rewrite_effective_endpoint(
        seam["config_path"],
        capability,
        placeholder,
        "changed-effective-endpoint-value",
    )
    runtime_config = load_config()
    assert seam["runtime_endpoint"](runtime_config) == endpoint_b
    if seam["public_status"]() == "verified":
        violations.append(
            "changed runtime-expanded endpoint inherited verified"
        )

    monkeypatch.delenv(endpoint_env)
    _b3_rewrite_effective_endpoint(
        seam["config_path"],
        capability,
        placeholder,
        "unresolved-effective-endpoint-token",
    )
    runtime_config = load_config()
    assert seam["runtime_endpoint"](runtime_config) == placeholder
    if seam["public_status"]() == "verified":
        violations.append(
            "unresolved endpoint token inherited verified"
        )

    assert violations == [], "; ".join(violations)


_DURABLE_MODEL_MUTATIONS = (
    "upsert_provider_credential",
    "delete_provider_credential",
    "test_vision_config",
    "test_image_gen_config",
    "set_vision_config",
    "set_alibaba_image_capabilities",
    "set_custom_vision_provider_config",
    "delete_custom_vision_provider_config",
    "set_custom_image_provider_config",
    "delete_custom_image_provider_config",
    "set_image_gen_config",
    "set_main_model_config",
)


def _prepare_durable_model_mutation_case(monkeypatch, tmp_path, mutation_name):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)

    if mutation_name == "upsert_provider_credential":
        (tmp_path / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "auxiliary": {
                        "vision": {
                            "provider": "alibaba",
                            "model": "qwen3-vl-plus",
                            "credential_ref": "hook-credential",
                        }
                    },
                    "image_gen": {
                        "provider": "dashscope",
                        "model": "qwen-image-2.0-pro",
                        "credential_ref": "hook-credential",
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return (
            lambda: model_config.upsert_provider_credential(
                {
                    "id": "hook-credential",
                    "provider": "alibaba",
                    "api_key": "hook-secret",
                }
            ),
            lambda: any(
                row.get("id") == "hook-credential"
                for row in _read_config(tmp_path).get("provider_credentials", [])
            ),
            (True, True),
        )

    if mutation_name == "delete_provider_credential":
        model_config.upsert_provider_credential(
            {
                "id": "hook-credential",
                "provider": "alibaba",
                "api_key": "hook-secret",
            }
        )
        return (
            lambda: model_config.delete_provider_credential("hook-credential"),
            lambda: not _read_config(tmp_path).get("provider_credentials"),
            (False, False),
        )

    if mutation_name == "test_vision_config":
        state_path = tmp_path / "vision-verification.json"
        monkeypatch.setattr(
            model_config,
            "_vision_verification_state_path",
            lambda *_: state_path,
        )
        _write_saved_vision_config(tmp_path)

        async def succeed(**_kwargs):
            return json.dumps(
                {
                    "success": True,
                    "analysis": "TAIJI-VISION-CHECK-7319",
                    "resolved_provider": "alibaba",
                    "resolved_model": "qwen3-vl-plus",
                }
            )

        monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", succeed)
        return (
            model_config.test_vision_config,
            lambda: json.loads(state_path.read_text(encoding="utf-8"))[
                "status"
            ]
            == "verified",
            (False, False),
        )

    if mutation_name == "test_image_gen_config":
        state_path = tmp_path / "image-gen-verification.json"
        generated = tmp_path / "cache" / "images" / "hook-probe.png"
        monkeypatch.setattr(
            model_config,
            "_image_gen_verification_state_path",
            lambda *_: state_path,
        )
        _write_saved_image_gen_config(tmp_path)

        def succeed():
            generated.parent.mkdir(parents=True, exist_ok=True)
            generated.write_bytes(b"\x89PNG\r\n\x1a\nhook")
            return {
                "success": True,
                "image": str(generated),
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
            }

        _install_probe_provider(monkeypatch, _ProbeImageProvider(succeed))
        return (
            model_config.test_image_gen_config,
            lambda: json.loads(state_path.read_text(encoding="utf-8"))[
                "status"
            ]
            == "verified",
            (False, False),
        )

    if mutation_name == "set_vision_config":
        return (
            lambda: model_config.set_vision_config(
                {
                    "provider": "alibaba",
                    "model": "qwen3-vl-plus",
                    "api_key": "vision-hook-key",
                }
            ),
            lambda: _read_config(tmp_path)["auxiliary"]["vision"]["provider"]
            == "alibaba",
            (True, False),
        )

    if mutation_name == "set_alibaba_image_capabilities":
        return (
            lambda: model_config.set_alibaba_image_capabilities(
                {"api_key": "alibaba-hook-key"}
            ),
            lambda: (
                _read_config(tmp_path)["auxiliary"]["vision"]["provider"]
                == "alibaba"
                and _read_config(tmp_path)["image_gen"]["provider"]
                == "dashscope"
            ),
            (True, True),
        )

    vision_body = {
        "id": "hook-vision",
        "name": "Hook Vision",
        "base_url": "https://vision.example.com/v1",
        "models": ["hook-vl"],
        "default_model": "hook-vl",
        "transport": "openai_chat_completions",
        "api_key": "vision-hook-secret",
    }
    if mutation_name in {
        "set_custom_vision_provider_config",
        "delete_custom_vision_provider_config",
    }:
        monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
        if mutation_name == "delete_custom_vision_provider_config":
            model_config.set_custom_vision_provider_config(vision_body)
            return (
                lambda: model_config.delete_custom_vision_provider_config(
                    "hook-vision"
                ),
                lambda: not _read_config(tmp_path).get(
                    "custom_vision_providers"
                ),
                (True, False),
            )
        return (
            lambda: model_config.set_custom_vision_provider_config(vision_body),
            lambda: _read_config(tmp_path)["custom_vision_providers"][0]["id"]
            == "hook-vision",
            (True, False),
        )

    image_body = {
        "id": "hook-image",
        "name": "Hook Image",
        "base_url": "https://images.example.com/v1",
        "models": ["hook-image-model"],
        "default_model": "hook-image-model",
        "api_key": "image-hook-secret",
    }
    if mutation_name in {
        "set_custom_image_provider_config",
        "delete_custom_image_provider_config",
    }:
        if mutation_name == "delete_custom_image_provider_config":
            model_config.set_custom_image_provider_config(image_body)
            return (
                lambda: model_config.delete_custom_image_provider_config(
                    "hook-image"
                ),
                lambda: not _read_config(tmp_path).get(
                    "custom_image_providers"
                ),
                (False, True),
            )
        return (
            lambda: model_config.set_custom_image_provider_config(image_body),
            lambda: _read_config(tmp_path)["custom_image_providers"][0]["id"]
            == "hook-image",
            (False, True),
        )

    if mutation_name == "set_image_gen_config":
        monkeypatch.setattr(
            model_config,
            "_image_gen_provider_rows",
            lambda _active: [
                {
                    "id": "doubao",
                    "name": "Doubao Seedream",
                    "models": [
                        {
                            "id": "doubao-seedream-5-0-260128",
                            "label": "Doubao Seedream",
                        }
                    ],
                    "default_model": "doubao-seedream-5-0-260128",
                    "key_status": {
                        "configured": False,
                        "env_var": "ARK_API_KEY",
                    },
                }
            ],
        )
        return (
            lambda: model_config.set_image_gen_config(
                {
                    "provider": "doubao",
                    "model": "doubao-seedream-5-0-260128",
                    "api_key": "image-hook-key",
                }
            ),
            lambda: _read_config(tmp_path)["image_gen"]["provider"]
            == "doubao",
            (False, True),
        )

    if mutation_name == "set_main_model_config":
        monkeypatch.setattr(
            model_config,
            "get_image_gen_config",
            lambda: {"image_gen": {}, "providers": []},
        )
        monkeypatch.setattr(
            model_config,
            "get_vision_config",
            lambda: {"vision": {}, "providers": []},
        )
        return (
            lambda: model_config.set_main_model_config(
                {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "api_key": "main-hook-key-123456",
                }
            ),
            lambda: _read_config(tmp_path)["model"]["provider"] == "deepseek",
            (False, False),
        )

    raise AssertionError(f"unhandled durable mutation: {mutation_name}")


@pytest.mark.parametrize("mutation_name", _DURABLE_MODEL_MUTATIONS)
@pytest.mark.parametrize("hook_fails", (False, True))
def test_durable_model_mutations_publish_exactly_once_after_persist(
    monkeypatch,
    tmp_path,
    mutation_name,
    hook_fails,
):
    invoke, durable_evidence, expected_flags = (
        _prepare_durable_model_mutation_case(
            monkeypatch,
            tmp_path,
            mutation_name,
        )
    )
    calls = []
    transaction_depth = 0
    profile_lock_depth = 0
    original_transaction = model_config.credential_transaction

    @contextmanager
    def tracked_transaction(*args, **kwargs):
        nonlocal transaction_depth
        transaction_depth += 1
        try:
            with original_transaction(*args, **kwargs) as transaction:
                yield transaction
        finally:
            transaction_depth -= 1

    monkeypatch.setattr(
        model_config,
        "credential_transaction",
        tracked_transaction,
    )
    profile_lock_name = {
        "test_vision_config": "_vision_profile_lock",
        "test_image_gen_config": "_image_gen_profile_lock",
    }.get(mutation_name)
    if profile_lock_name:
        original_profile_lock = getattr(model_config, profile_lock_name)

        @contextmanager
        def tracked_profile_lock(*args, **kwargs):
            nonlocal profile_lock_depth
            profile_lock_depth += 1
            try:
                with original_profile_lock(*args, **kwargs) as lock:
                    yield lock
            finally:
                profile_lock_depth -= 1

        monkeypatch.setattr(
            model_config,
            profile_lock_name,
            tracked_profile_lock,
        )

    def post_commit_hook(
        published_mutation,
        *,
        invalidate_vision=False,
        invalidate_image=False,
        vision_invalidation_token=None,
        image_invalidation_token=None,
    ):
        calls.append(
            (
                published_mutation,
                invalidate_vision,
                invalidate_image,
            )
        )
        if invalidate_vision:
            assert vision_invalidation_token is not None
            assert vision_invalidation_token.capability == "vision"
            assert (
                vision_invalidation_token.profile
                == model_config._active_profile_name()
            )
        else:
            assert vision_invalidation_token is None
        if invalidate_image:
            assert image_invalidation_token is not None
            assert image_invalidation_token.capability == "image"
            assert (
                image_invalidation_token.profile
                == model_config._active_profile_name()
            )
        else:
            assert image_invalidation_token is None
        assert transaction_depth == 0
        assert profile_lock_depth == 0
        assert durable_evidence()
        if hook_fails:
            raise RuntimeError("simulated post-commit refresh failure")
        return []

    monkeypatch.setattr(
        model_config,
        "_run_durable_mutation_post_commit_hook",
        post_commit_hook,
    )

    result = invoke()

    assert calls == [
        (mutation_name, expected_flags[0], expected_flags[1])
    ]
    assert durable_evidence()
    if hook_fails:
        assert result["refresh_pending"] is True
        assert (
            "durable_mutation_refresh_pending" in result["warnings"]
        )


@pytest.mark.parametrize("mutation_name", _DURABLE_MODEL_MUTATIONS)
def test_durable_model_mutations_do_not_publish_before_persist(
    monkeypatch,
    tmp_path,
    mutation_name,
):
    if mutation_name in {"test_vision_config", "test_image_gen_config"}:
        _use_home(monkeypatch, tmp_path, stub_image_gen=False)
        calls = []
        monkeypatch.setattr(
            model_config,
            "_run_durable_mutation_post_commit_hook",
            lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        result = getattr(model_config, mutation_name)()
        assert result["status"] == "unconfigured"
        assert calls == []
        return

    invoke, _durable_evidence, _expected_flags = (
        _prepare_durable_model_mutation_case(
            monkeypatch,
            tmp_path,
            mutation_name,
        )
    )
    calls = []
    monkeypatch.setattr(
        model_config,
        "_run_durable_mutation_post_commit_hook",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    failing_writer = lambda *args, **kwargs: (_ for _ in ()).throw(
        OSError("simulated failure before durable commit")
    )
    if mutation_name in {
        "upsert_provider_credential",
        "delete_provider_credential",
    }:
        monkeypatch.setattr(
            model_config,
            "mutate_config_env_strict",
            failing_writer,
        )
    elif mutation_name in {
        "set_custom_vision_provider_config",
        "delete_custom_vision_provider_config",
        "set_custom_image_provider_config",
        "delete_custom_image_provider_config",
    }:
        monkeypatch.setattr(
            model_config,
            "_write_custom_provider_transaction",
            failing_writer,
        )
    else:
        monkeypatch.setattr(
            model_config,
            "_commit_expected_config_env",
            failing_writer,
        )

    with pytest.raises(OSError, match="before durable commit"):
        invoke()
    assert calls == []


@pytest.mark.parametrize("mutation_name", _DURABLE_MODEL_MUTATIONS)
def test_each_durable_model_mutation_has_one_explicit_post_commit_boundary(
    mutation_name,
):
    source = inspect.getsource(getattr(model_config, mutation_name))
    hook_call = "_invoke_durable_mutation_post_commit("
    assert source.count(hook_call) == 1
    if mutation_name in {"test_vision_config", "test_image_gen_config"}:
        assert source.index("if not still_current:") < source.index(hook_call)


def _pause_first_durable_post_commit(monkeypatch):
    first_hook_started = threading.Event()
    release_first_hook = threading.Event()
    calls = []
    calls_lock = threading.Lock()

    def hook(
        mutation,
        *,
        invalidate_vision=False,
        invalidate_image=False,
        vision_invalidation_token=None,
        image_invalidation_token=None,
    ):
        with calls_lock:
            call_index = len(calls)
            calls.append(
                (mutation, invalidate_vision, invalidate_image)
            )
        if call_index == 0:
            first_hook_started.set()
            assert release_first_hook.wait(timeout=5)
        return []

    monkeypatch.setattr(
        model_config,
        "_run_durable_mutation_post_commit_hook",
        hook,
    )
    return first_hook_started, release_first_hook, calls


def test_delete_credential_response_stays_on_its_committed_generation(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    model_config.upsert_provider_credential(
        {
            "id": "generation-race",
            "provider": "alibaba",
            "label": "generation-zero",
            "api_key": "generation-zero-secret",
        }
    )
    started, release, calls = _pause_first_durable_post_commit(monkeypatch)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            deleted = pool.submit(
                model_config.delete_provider_credential,
                "generation-race",
            )
            assert started.wait(timeout=5)
            model_config.upsert_provider_credential(
                {
                    "id": "generation-race",
                    "provider": "alibaba",
                    "label": "generation-newer",
                    "api_key": "generation-newer-secret",
                }
            )
            release.set()
            result = deleted.result(timeout=5)
    finally:
        release.set()

    assert result["credentials"] == []
    assert calls == [
        ("delete_provider_credential", False, False),
        ("upsert_provider_credential", False, False),
    ]
    assert (
        model_config.get_provider_credentials_config()["credentials"][0][
            "label"
        ]
        == "generation-newer"
    )
    assert "generation-newer-secret" not in json.dumps(result)


def test_upsert_credential_response_stays_on_its_committed_generation(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    started, release, calls = _pause_first_durable_post_commit(monkeypatch)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            upserted = pool.submit(
                model_config.upsert_provider_credential,
                {
                    "id": "generation-race",
                    "provider": "alibaba",
                    "label": "generation-outer",
                    "api_key": "generation-outer-secret",
                },
            )
            assert started.wait(timeout=5)
            model_config.delete_provider_credential("generation-race")
            release.set()
            result = upserted.result(timeout=5)
    finally:
        release.set()

    assert result["credential"]["label"] == "generation-outer"
    assert result["credential"]["configured"] is True
    assert calls == [
        ("upsert_provider_credential", False, False),
        ("delete_provider_credential", False, False),
    ]
    assert model_config.get_provider_credentials_config()["credentials"] == []
    assert "generation-outer-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    ("capability", "set_name", "delete_name", "getter_name", "body"),
    (
        (
            "vision",
            "set_custom_vision_provider_config",
            "delete_custom_vision_provider_config",
            "get_custom_vision_provider_configs",
            {
                "id": "router",
                "name": "Outer vision",
                "base_url": "https://vision.example.com/v1",
                "models": ["vision-model"],
                "transport": "openai_chat_completions",
                "api_key": "outer-vision-secret",
            },
        ),
        (
            "image",
            "set_custom_image_provider_config",
            "delete_custom_image_provider_config",
            "get_custom_image_provider_configs",
            {
                "id": "router",
                "name": "Outer image",
                "base_url": "https://images.example.com/v1",
                "models": ["image-model"],
                "default_model": "image-model",
                "api_key": "outer-image-secret",
            },
        ),
    ),
)
def test_custom_provider_set_response_stays_on_its_committed_generation(
    monkeypatch,
    tmp_path,
    capability,
    set_name,
    delete_name,
    getter_name,
    body,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    started, release, _calls = _pause_first_durable_post_commit(monkeypatch)
    setter = getattr(model_config, set_name)
    delete = getattr(model_config, delete_name)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            outer = pool.submit(setter, body)
            assert started.wait(timeout=5)
            delete("router")
            release.set()
            outer_result = outer.result(timeout=5)
    finally:
        release.set()

    assert outer_result["provider"]["id"] == "custom:router"
    assert [row["id"] for row in outer_result["providers"]] == [
        "custom:router"
    ]
    assert getattr(model_config, getter_name)()["providers"] == []
    assert str(body["api_key"]) not in json.dumps(outer_result)
    assert capability in {"vision", "image"}


@pytest.mark.parametrize(
    ("set_name", "delete_name", "getter_name", "initial_body", "newer_name"),
    (
        (
            "set_custom_vision_provider_config",
            "delete_custom_vision_provider_config",
            "get_custom_vision_provider_configs",
            {
                "id": "router",
                "name": "Initial vision",
                "base_url": "https://vision.example.com/v1",
                "models": ["vision-model"],
                "transport": "openai_chat_completions",
                "api_key": "initial-vision-secret",
            },
            "Newer vision",
        ),
        (
            "set_custom_image_provider_config",
            "delete_custom_image_provider_config",
            "get_custom_image_provider_configs",
            {
                "id": "router",
                "name": "Initial image",
                "base_url": "https://images.example.com/v1",
                "models": ["image-model"],
                "default_model": "image-model",
                "api_key": "initial-image-secret",
            },
            "Newer image",
        ),
    ),
)
def test_custom_provider_delete_response_stays_on_its_committed_generation(
    monkeypatch,
    tmp_path,
    set_name,
    delete_name,
    getter_name,
    initial_body,
    newer_name,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda _url: True)
    setter = getattr(model_config, set_name)
    delete = getattr(model_config, delete_name)
    setter(initial_body)
    started, release, _calls = _pause_first_durable_post_commit(monkeypatch)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            outer = pool.submit(delete, "router")
            assert started.wait(timeout=5)
            newer_body = {
                **initial_body,
                "name": newer_name,
                "api_key": "newer-generation-secret",
            }
            setter(newer_body)
            release.set()
            outer_result = outer.result(timeout=5)
    finally:
        release.set()

    assert outer_result["providers"] == []
    current = getattr(model_config, getter_name)()["providers"]
    assert [row["id"] for row in current] == ["custom:router"]
    assert current[0]["name"] == newer_name
    assert "newer-generation-secret" not in json.dumps(outer_result)


@pytest.mark.parametrize(
    (
        "setter_name",
        "outer_body",
        "newer_body",
        "response_section",
        "expected_outer",
        "expected_newer",
    ),
    (
        (
            "set_vision_config",
            {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "api_key": "outer-vision-key",
            },
            {
                "provider": "zai",
                "model": "glm-5v-turbo",
                "api_key": "newer-vision-key",
            },
            "vision",
            ("alibaba", "qwen3-vl-plus"),
            ("zai", "glm-5v-turbo"),
        ),
        (
            "set_image_gen_config",
            {
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "api_key": "outer-image-key",
            },
            {
                "provider": "dashscope",
                "model": "qwen-image",
                "api_key": "newer-image-key",
            },
            "image_gen",
            ("dashscope", "qwen-image-2.0-pro"),
            ("dashscope", "qwen-image"),
        ),
        (
            "set_main_model_config",
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "api_key": "outer-main-key",
            },
            {
                "provider": "deepseek",
                "model": "deepseek-reasoner",
                "api_key": "newer-main-key",
            },
            "main",
            ("deepseek", "deepseek-chat"),
            ("deepseek", "deepseek-reasoner"),
        ),
    ),
)
def test_durable_setter_response_stays_on_its_committed_generation(
    monkeypatch,
    tmp_path,
    setter_name,
    outer_body,
    newer_body,
    response_section,
    expected_outer,
    expected_newer,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    if setter_name == "set_image_gen_config":
        def image_provider_rows(_active):
            row = _dashscope_image_provider_row()
            row["models"].append(
                {"id": "qwen-image", "label": "Qwen Image"}
            )
            return [row]

        monkeypatch.setattr(
            model_config,
            "_image_gen_provider_rows",
            image_provider_rows,
        )
    started, release, calls = _pause_first_durable_post_commit(monkeypatch)
    setter = getattr(model_config, setter_name)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            outer = pool.submit(setter, outer_body)
            assert started.wait(timeout=5)
            newer_result = setter(newer_body)
            release.set()
            outer_result = outer.result(timeout=5)
    finally:
        release.set()

    assert (
        outer_result[response_section]["provider"],
        outer_result[response_section]["model"],
    ) == expected_outer
    assert (
        newer_result[response_section]["provider"],
        newer_result[response_section]["model"],
    ) == expected_newer
    assert len(calls) == 2
    serialized_outer = json.dumps(outer_result)
    assert str(outer_body["api_key"]) not in serialized_outer
    assert str(newer_body["api_key"]) not in serialized_outer


def test_alibaba_quick_response_stays_on_its_committed_generation(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)

    def image_provider_rows(_active):
        row = _dashscope_image_provider_row()
        row["models"].append(
            {"id": "qwen-image", "label": "Qwen Image"}
        )
        return [row]

    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        image_provider_rows,
    )
    started, release, _calls = _pause_first_durable_post_commit(monkeypatch)
    outer_body = {
        "api_key": "outer-quick-secret",
        "vision_model": "qwen3-vl-plus",
        "image_model": "qwen-image-2.0-pro",
    }
    newer_body = {
        "api_key": "newer-quick-secret",
        "vision_model": "qwen3-vl-flash",
        "image_model": "qwen-image",
    }

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            outer = pool.submit(
                model_config.set_alibaba_image_capabilities,
                outer_body,
            )
            assert started.wait(timeout=5)
            newer_result = model_config.set_alibaba_image_capabilities(
                newer_body
            )
            release.set()
            outer_result = outer.result(timeout=5)
    finally:
        release.set()

    assert (
        outer_result["vision"]["provider"],
        outer_result["vision"]["model"],
        outer_result["image_gen"]["provider"],
        outer_result["image_gen"]["model"],
    ) == (
        "alibaba",
        "qwen3-vl-plus",
        "dashscope",
        "qwen-image-2.0-pro",
    )
    assert (
        newer_result["vision"]["model"],
        newer_result["image_gen"]["model"],
    ) == ("qwen3-vl-flash", "qwen-image")
    serialized_outer = json.dumps(outer_result)
    assert outer_body["api_key"] not in serialized_outer
    assert newer_body["api_key"] not in serialized_outer


@pytest.mark.parametrize("capability", ["vision", "image"])
def test_verification_compare_delete_binds_profile_and_state_identity(
    monkeypatch,
    tmp_path,
    capability,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    active_profile = ["commit-profile"]
    monkeypatch.setattr(
        model_config,
        "_active_profile_name",
        lambda: active_profile[0],
    )
    if capability == "vision":
        state_path = lambda profile=None: (
            tmp_path / f"vision-{profile or active_profile[0]}.json"
        )
        monkeypatch.setattr(
            model_config,
            "_vision_verification_state_path",
            state_path,
        )
        monkeypatch.setattr(model_config, "_VISION_PROBE_GENERATIONS", {})
        capture = model_config._capture_vision_verification_invalidation
        invalidate = model_config._invalidate_vision_verification
    else:
        state_path = lambda profile=None: (
            tmp_path / f"image-{profile or active_profile[0]}.json"
        )
        monkeypatch.setattr(
            model_config,
            "_image_gen_verification_state_path",
            state_path,
        )
        monkeypatch.setattr(model_config, "_IMAGE_GEN_PROBE_GENERATIONS", {})
        capture = model_config._capture_image_gen_verification_invalidation
        invalidate = model_config._invalidate_image_gen_verification

    committed_path = state_path("commit-profile")
    newer_active_path = state_path("new-active-profile")
    model_config._atomic_write_json(
        committed_path,
        {
            "schema_version": 1,
            "generation": 5,
            "status": "verified",
            "fingerprint": "commit-state",
            "diagnostic_id": "commit-diagnostic",
        },
    )
    token = capture("commit-profile")
    model_config._atomic_write_json(
        newer_active_path,
        {
            "schema_version": 1,
            "generation": 9,
            "status": "verified",
            "fingerprint": "other-profile-state",
            "diagnostic_id": "other-profile-diagnostic",
        },
    )
    active_profile[0] = "new-active-profile"

    assert invalidate(token) is True
    _assert_verification_tombstone(
        json.loads(committed_path.read_text(encoding="utf-8")),
        minimum_generation=6,
        forbidden_fingerprint="commit-state",
        forbidden_diagnostic_id="commit-diagnostic",
    )
    assert json.loads(newer_active_path.read_text(encoding="utf-8"))[
        "fingerprint"
    ] == "other-profile-state"

    model_config._atomic_write_json(
        committed_path,
        {
            "schema_version": 1,
            "generation": 7,
            "status": "verified",
            "fingerprint": "older-state",
            "diagnostic_id": "older-diagnostic",
        },
    )
    changed_state_token = capture("commit-profile")
    model_config._atomic_write_json(
        committed_path,
        {
            "schema_version": 1,
            "generation": 8,
            "status": "verified",
            "fingerprint": "newer-probe-state",
            "diagnostic_id": "newer-probe-diagnostic",
        },
    )

    assert invalidate(changed_state_token) is False
    preserved = json.loads(committed_path.read_text(encoding="utf-8"))
    assert preserved["generation"] == 8
    assert preserved["status"] == "verified"
    assert preserved["fingerprint"] == "newer-probe-state"
    assert preserved["diagnostic_id"] == "newer-probe-diagnostic"


def test_delayed_vision_set_invalidation_preserves_new_probe_result(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "vision-set-vs-test.json"
    monkeypatch.setattr(
        model_config,
        "_vision_verification_state_path",
        lambda *_args, **_kwargs: state_path,
    )

    async def successful_probe(*, provider, model, **_kwargs):
        return json.dumps(
            {
                "success": True,
                "analysis": model_config._VISION_PROBE_MARKER,
                "resolved_provider": provider,
                "resolved_model": model,
            }
        )

    monkeypatch.setattr(
        "tools.vision_tools.vision_analyze_tool",
        successful_probe,
    )
    original_invalidator = model_config._invalidate_vision_verification
    invalidator_started = threading.Event()
    release_invalidator = threading.Event()

    def delayed_invalidator(expected):
        invalidator_started.set()
        assert release_invalidator.wait(timeout=5)
        return original_invalidator(expected)

    monkeypatch.setattr(
        model_config,
        "_invalidate_vision_verification",
        delayed_invalidator,
    )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            setter = pool.submit(
                model_config.set_vision_config,
                {
                    "provider": "alibaba",
                    "model": "qwen3-vl-plus",
                    "api_key": "vision-set-vs-test-secret",
                },
            )
            assert invalidator_started.wait(timeout=5)
            probe_result = model_config.test_vision_config()
            assert probe_result["status"] == "verified"
            release_invalidator.set()
            setter.result(timeout=5)
    finally:
        release_invalidator.set()

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "verified"
    assert persisted["diagnostic_id"] == probe_result["diagnostic_id"]


def test_delayed_image_set_invalidation_preserves_new_probe_result(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / "image-set-vs-test.json"
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_args, **_kwargs: state_path,
    )
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda _active: [_dashscope_image_provider_row()],
    )
    monkeypatch.setattr(
        model_config,
        "_execute_image_gen_probe",
        lambda *_args, **_kwargs: (
            True,
            "",
            "真实生图验证通过。",
        ),
    )
    original_invalidator = model_config._invalidate_image_gen_verification
    invalidator_started = threading.Event()
    release_invalidator = threading.Event()

    def delayed_invalidator(expected):
        invalidator_started.set()
        assert release_invalidator.wait(timeout=5)
        return original_invalidator(expected)

    monkeypatch.setattr(
        model_config,
        "_invalidate_image_gen_verification",
        delayed_invalidator,
    )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            setter = pool.submit(
                model_config.set_image_gen_config,
                {
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                    "api_key": "image-set-vs-test-secret",
                },
            )
            assert invalidator_started.wait(timeout=5)
            probe_result = model_config.test_image_gen_config()
            assert probe_result["status"] == "verified"
            release_invalidator.set()
            setter.result(timeout=5)
    finally:
        release_invalidator.set()

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "verified"
    assert persisted["diagnostic_id"] == probe_result["diagnostic_id"]


def test_default_durable_mutation_hook_runs_real_refresh_actions_once(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        model_config,
        "reload_config",
        lambda: calls.append("reload_config"),
    )
    monkeypatch.setattr(
        model_config,
        "invalidate_models_cache",
        lambda: calls.append("invalidate_models_cache"),
    )
    monkeypatch.setattr(
        model_config,
        "_invalidate_vision_verification",
        lambda *_args, **_kwargs: calls.append("invalidate_vision"),
    )
    monkeypatch.setattr(
        model_config,
        "_invalidate_image_gen_verification",
        lambda *_args, **_kwargs: calls.append("invalidate_image"),
    )
    vision_token = model_config._VerificationInvalidationToken(
        capability="vision",
        profile="test",
        generation=0,
        state_identity="missing",
    )
    image_token = model_config._VerificationInvalidationToken(
        capability="image",
        profile="test",
        generation=0,
        state_identity="missing",
    )

    warnings = model_config._run_durable_mutation_post_commit_hook(
        "set_alibaba_image_capabilities",
        invalidate_vision=True,
        invalidate_image=True,
        vision_invalidation_token=vision_token,
        image_invalidation_token=image_token,
    )

    assert warnings == []
    assert calls == [
        "reload_config",
        "invalidate_models_cache",
        "invalidate_vision",
        "invalidate_image",
    ]


def test_durable_mutation_hook_fails_safe_when_invalidation_token_is_missing(
    monkeypatch,
):
    invalidations = []
    monkeypatch.setattr(model_config, "reload_config", lambda: None)
    monkeypatch.setattr(model_config, "invalidate_models_cache", lambda: None)
    monkeypatch.setattr(
        model_config,
        "_invalidate_vision_verification",
        lambda *_args, **_kwargs: invalidations.append("vision"),
    )
    monkeypatch.setattr(
        model_config,
        "_invalidate_image_gen_verification",
        lambda *_args, **_kwargs: invalidations.append("image"),
    )

    warnings = model_config._run_durable_mutation_post_commit_hook(
        "unsafe-caller",
        invalidate_vision=True,
        invalidate_image=True,
    )

    assert invalidations == []
    assert warnings == [
        "vision_verification_refresh_pending",
        "image_gen_verification_refresh_pending",
    ]


def test_default_durable_mutation_hook_returns_stable_deduplicated_warning(
    monkeypatch,
):
    monkeypatch.setattr(
        model_config,
        "reload_config",
        lambda: (_ for _ in ()).throw(OSError("reload failed")),
    )
    monkeypatch.setattr(model_config, "invalidate_models_cache", lambda: None)

    warnings = model_config._run_durable_mutation_post_commit_hook(
        "set_main_model_config"
    )

    assert warnings == ["runtime_config_refresh_pending"]


def test_main_model_refresh_preserves_auxiliary_verification_authority(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    vision_state_path = tmp_path / "vision-verification.json"
    image_state_path = tmp_path / "image-gen-verification.json"
    vision_state = {"status": "verified", "fingerprint": "vision-authority"}
    image_state = {"status": "verified", "fingerprint": "image-authority"}
    vision_state_path.write_text(json.dumps(vision_state), encoding="utf-8")
    image_state_path.write_text(json.dumps(image_state), encoding="utf-8")
    monkeypatch.setattr(
        model_config,
        "_vision_verification_state_path",
        lambda *_args: vision_state_path,
    )
    monkeypatch.setattr(
        model_config,
        "_image_gen_verification_state_path",
        lambda *_args: image_state_path,
    )
    refresh_calls = []
    monkeypatch.setattr(
        model_config,
        "reload_config",
        lambda: refresh_calls.append("reload_config"),
    )
    monkeypatch.setattr(
        model_config,
        "invalidate_models_cache",
        lambda: refresh_calls.append("invalidate_models_cache"),
    )

    result = model_config.set_main_model_config(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_key": "main-route-key-123456",
        }
    )

    assert result["main"]["provider"] == "deepseek"
    assert refresh_calls == ["reload_config", "invalidate_models_cache"]
    assert json.loads(vision_state_path.read_text(encoding="utf-8")) == vision_state
    assert json.loads(image_state_path.read_text(encoding="utf-8")) == image_state


def test_durable_main_model_save_survives_default_runtime_reload_failure(
    monkeypatch,
    tmp_path,
):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_config,
        "reload_config",
        lambda: (_ for _ in ()).throw(OSError("reload failed")),
    )

    result = model_config.set_main_model_config(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_key": "durable-main-key-123456",
        }
    )

    assert _read_config(tmp_path)["model"]["provider"] == "deepseek"
    assert result["main"]["provider"] == "deepseek"
    assert result["refresh_pending"] is True
    assert result["warnings"] == ["runtime_config_refresh_pending"]


def _use_persisted_capability_test_home(monkeypatch, tmp_path):
    """Isolate real config/state readers without replacing runtime snapshots."""
    os_home = tmp_path / "os-home"
    hermes_home = tmp_path / "hermes-home"
    state_dir = tmp_path / "webui-state"
    os_home.mkdir()
    hermes_home.mkdir()
    state_dir.mkdir()
    monkeypatch.setenv("HOME", str(os_home))
    monkeypatch.setenv("HERMES_PROFILE_NAME", "default")
    monkeypatch.setenv("TAIJI_WEBUI_STATE_DIR", str(state_dir))
    monkeypatch.setenv("HERMES_WEBUI_PORT", "28571")
    monkeypatch.setenv("PORT", "28571")
    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_STATE_DIR", raising=False)
    _use_home(monkeypatch, hermes_home, stub_image_gen=False)
    monkeypatch.setattr(api_config, "STATE_DIR", state_dir)
    return hermes_home, state_dir


def test_vision_probe_does_not_overwrite_newer_out_of_band_verifying_state(
    monkeypatch,
    tmp_path,
):
    """Finalization must own the exact state file it began, not just config."""
    hermes_home, _state_dir = _use_persisted_capability_test_home(
        monkeypatch,
        tmp_path,
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    model_config.set_vision_config(
        {
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "api_key": "vision-state-owner-a",
        }
    )
    state_path = model_config._vision_verification_state_path("default")
    newer_state = {}

    async def replace_state_during_provider_callback(**_kwargs):
        current = json.loads(state_path.read_text(encoding="utf-8"))
        assert current["status"] == "verifying"
        newer_state.update(
            {
                **current,
                "generation": int(current["generation"]) + 1,
                "fingerprint": f"{current['fingerprint']}-newer",
                "diagnostic_id": "newer-vision-probe-owner",
            }
        )
        state_path.write_text(
            json.dumps(newer_state, sort_keys=True),
            encoding="utf-8",
        )
        return json.dumps(
            {
                "success": True,
                "analysis": "TAIJI-VISION-CHECK-7319",
                "resolved_provider": "alibaba",
                "resolved_model": "qwen3-vl-plus",
            }
        )

    import tools.vision_tools as vision_tools

    monkeypatch.setattr(
        vision_tools,
        "vision_analyze_tool",
        replace_state_during_provider_callback,
    )

    result = model_config.test_vision_config()

    assert result["error_code"] == "vision_probe_superseded"
    assert json.loads(state_path.read_text(encoding="utf-8")) == newer_state
    assert (hermes_home / "config.yaml").is_file()


def test_image_probe_does_not_overwrite_newer_out_of_band_verifying_state(
    monkeypatch,
    tmp_path,
):
    """Image finalization must compare durable state ownership before write."""
    hermes_home, _state_dir = _use_persisted_capability_test_home(
        monkeypatch,
        tmp_path,
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda _active: [_dashscope_image_provider_row()],
    )
    model_config.set_image_gen_config(
        {
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "credentials": {
                "api_key": "image-state-owner-a",
                "workspace_id": "ws-state-owner",
            },
        }
    )
    state_path = model_config._image_gen_verification_state_path("default")
    generated = hermes_home / "cache" / "images" / "state-owner.png"
    newer_state = {}

    def replace_state_during_provider_callback():
        current = json.loads(state_path.read_text(encoding="utf-8"))
        assert current["status"] == "verifying"
        newer_state.update(
            {
                **current,
                "generation": int(current["generation"]) + 1,
                "fingerprint": f"{current['fingerprint']}-newer",
                "diagnostic_id": "newer-image-probe-owner",
            }
        )
        state_path.write_text(
            json.dumps(newer_state, sort_keys=True),
            encoding="utf-8",
        )
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nstate-owner")
        return {
            "success": True,
            "image": str(generated),
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
        }

    _install_probe_provider(
        monkeypatch,
        _ProbeImageProvider(replace_state_during_provider_callback),
    )

    result = model_config.test_image_gen_config()

    assert result["error_code"] == "image_gen_probe_superseded"
    assert json.loads(state_path.read_text(encoding="utf-8")) == newer_state
    assert (hermes_home / "config.yaml").is_file()


def test_persisted_image_a_b_a_rejects_old_verified_binding_before_io(
    monkeypatch,
    tmp_path,
):
    """Real setter/probe A -> B -> A must not revive the old A1 binding."""
    hermes_home, state_dir = _use_persisted_capability_test_home(
        monkeypatch,
        tmp_path,
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda _active: [_dashscope_image_provider_row()],
    )
    probe_counter = {"value": 0}

    def successful_probe():
        probe_counter["value"] += 1
        generated = (
            hermes_home
            / "cache"
            / "images"
            / f"persisted-aba-{probe_counter['value']}.png"
        )
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\npersisted-aba")
        return {
            "success": True,
            "image": str(generated),
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
        }

    _install_probe_provider(
        monkeypatch,
        _ProbeImageProvider(successful_probe),
    )

    def save_and_verify(api_key):
        model_config.set_image_gen_config(
            {
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "credentials": {
                    "api_key": api_key,
                    "workspace_id": "ws-persisted-aba",
                },
            }
        )
        result = model_config.test_image_gen_config()
        assert result["status"] == "verified"
        return result

    from agent import image_runtime
    from agent.image_gen_verification import (
        ImageGenRequestAuthorizationError,
        authorize_image_gen_request_binding,
        build_image_gen_request_reauth_guard,
    )

    a1_result = save_and_verify("image-persisted-a")
    state_path = model_config._image_gen_verification_state_path("default")
    assert state_path.parent == state_dir / "image-gen-verification"
    a1_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert a1_state["diagnostic_id"] == a1_result["diagnostic_id"]
    a1_runtime = image_runtime.verification_runtime_snapshot(
        "image_generation"
    )
    assert a1_runtime["status"] == "verified"
    assert a1_runtime["fingerprint"] == a1_state["fingerprint"]
    captured_a1 = model_config._capture_image_gen_config_snapshot()
    assert captured_a1.probe_binding is not None
    old_a1_binding = authorize_image_gen_request_binding(
        captured_a1.probe_binding,
        authorization_fingerprint=a1_runtime["fingerprint"],
        authorization_generation=a1_runtime[
            "_authorization_generation"
        ],
    )
    a1_config_semantic = _read_config(hermes_home)

    save_and_verify("image-persisted-b")
    a3_result = save_and_verify("image-persisted-a")
    a3_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert a3_state["diagnostic_id"] == a3_result["diagnostic_id"]
    a3_runtime = image_runtime.verification_runtime_snapshot(
        "image_generation"
    )

    assert a1_runtime["fingerprint"] != a3_runtime["fingerprint"]
    assert (
        a1_runtime["_authorization_generation"]
        != a3_runtime["_authorization_generation"]
    )
    assert a3_state["generation"] > a1_state["generation"]
    assert {
        key: a1_runtime[key]
        for key in ("provider", "model")
    } == {
        key: a3_runtime[key]
        for key in ("provider", "model")
    }
    assert _read_config(hermes_home) != a1_config_semantic

    old_a1_guard = build_image_gen_request_reauth_guard(
        old_a1_binding,
        expected_snapshot=a1_runtime,
    )
    provider_io = []
    cache_writes = []

    def forbidden_provider_io(*_args, **_kwargs):
        provider_io.append((_args, _kwargs))
        raise AssertionError("stale A1 binding reached external Provider I/O")

    def forbidden_cache_write(*_args, **_kwargs):
        cache_writes.append((_args, _kwargs))
        raise AssertionError("stale A1 binding wrote an image cache entry")

    from plugins.image_gen import dashscope as dashscope_provider
    from plugins.image_gen import domestic_common

    monkeypatch.setattr(
        domestic_common,
        "request_pinned_https",
        forbidden_provider_io,
    )
    monkeypatch.setattr(
        dashscope_provider,
        "_save_safe_image_url",
        forbidden_cache_write,
    )

    with pytest.raises(ImageGenRequestAuthorizationError) as blocked:
        dashscope_provider.DashScopeQwenImageProvider().generate(
            prompt="persisted ABA stale binding must be blocked",
            aspect_ratio="square",
            model="qwen-image-2.0-pro",
            _runtime_binding=old_a1_binding,
            _reauth_guard=old_a1_guard,
        )

    assert blocked.value.error_code == "capability_caller_stale"
    assert provider_io == []
    assert cache_writes == []


def test_persisted_vision_a_b_a_rejects_old_verified_binding_before_io(
    monkeypatch,
    tmp_path,
):
    """Real vision setter/probe state on disk must defeat ABA material reuse."""
    import asyncio
    import tools.vision_tools as vision_tools
    from agent import image_runtime
    from agent.auxiliary_client import capture_vision_request_binding

    hermes_home, state_dir = _use_persisted_capability_test_home(
        monkeypatch,
        tmp_path,
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    real_vision_boundary = vision_tools.vision_analyze_tool

    async def successful_probe(**_kwargs):
        return json.dumps(
            {
                "success": True,
                "analysis": "TAIJI-VISION-CHECK-7319",
                "resolved_provider": "alibaba",
                "resolved_model": "qwen3-vl-plus",
            }
        )

    monkeypatch.setattr(
        vision_tools,
        "vision_analyze_tool",
        successful_probe,
    )

    def save_and_verify(api_key):
        model_config.set_vision_config(
            {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "api_key": api_key,
            }
        )
        result = model_config.test_vision_config()
        assert result["status"] == "verified"
        return result

    a1_result = save_and_verify("vision-persisted-a")
    state_path = model_config._vision_verification_state_path("default")
    assert state_path.parent == state_dir / "vision-verification"
    a1_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert a1_state["diagnostic_id"] == a1_result["diagnostic_id"]
    a1_runtime = image_runtime.verification_runtime_snapshot("vision")
    assert a1_runtime["status"] == "verified"
    assert a1_runtime["fingerprint"] == a1_state["fingerprint"]
    old_a1_binding = capture_vision_request_binding(
        authorization_fingerprint=a1_runtime["fingerprint"],
        authorization_generation=a1_runtime[
            "_authorization_generation"
        ],
    )
    assert old_a1_binding is not None
    a1_config_semantic = _read_config(hermes_home)

    save_and_verify("vision-persisted-b")
    a3_result = save_and_verify("vision-persisted-a")
    a3_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert a3_state["diagnostic_id"] == a3_result["diagnostic_id"]
    a3_runtime = image_runtime.verification_runtime_snapshot("vision")

    assert a1_runtime["fingerprint"] != a3_runtime["fingerprint"]
    assert (
        a1_runtime["_authorization_generation"]
        != a3_runtime["_authorization_generation"]
    )
    assert a3_state["generation"] > a1_state["generation"]
    assert {
        key: a1_runtime[key]
        for key in ("provider", "model", "base_url", "transport")
    } == {
        key: a3_runtime[key]
        for key in ("provider", "model", "base_url", "transport")
    }
    assert _read_config(hermes_home) != a1_config_semantic

    monkeypatch.setattr(
        vision_tools,
        "vision_analyze_tool",
        real_vision_boundary,
    )
    provider_io = []
    cache_writes = []

    async def forbidden_provider_io(**kwargs):
        provider_io.append(kwargs)
        raise AssertionError("stale A1 binding reached vision Provider I/O")

    async def forbidden_cache_write(*args, **kwargs):
        cache_writes.append((args, kwargs))
        raise AssertionError("stale A1 binding wrote the vision cache")

    monkeypatch.setattr(
        vision_tools,
        "async_call_llm",
        forbidden_provider_io,
    )
    monkeypatch.setattr(
        vision_tools,
        "_download_image",
        forbidden_cache_write,
    )

    blocked = json.loads(
        asyncio.run(
            real_vision_boundary(
                image_url="https://example.test/stale-a1.png",
                user_prompt="persisted ABA stale binding must be blocked",
                model="qwen3-vl-plus",
                provider="alibaba",
                strict_target=True,
                _runtime_binding=old_a1_binding,
            )
        )
    )

    assert blocked["status"] == "blocked"
    assert blocked["error_code"] == "capability_binding_mismatch"
    assert provider_io == []
    assert cache_writes == []


def test_image_a_b_a_does_not_revive_old_verified_binding_when_tombstones_fail(
    monkeypatch,
    tmp_path,
):
    """Config epoch, not a best-effort tombstone, must defeat image ABA."""
    hermes_home, _state_dir = _use_persisted_capability_test_home(
        monkeypatch,
        tmp_path,
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(
        model_config,
        "_image_gen_provider_rows",
        lambda _active: [_dashscope_image_provider_row()],
    )

    generated = hermes_home / "cache" / "images" / "epoch-a1.png"

    def successful_probe():
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nepoch-a1")
        return {
            "success": True,
            "image": str(generated),
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
        }

    _install_probe_provider(
        monkeypatch,
        _ProbeImageProvider(successful_probe),
    )

    def save(api_key):
        return model_config.set_image_gen_config(
            {
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
                "credentials": {
                    "api_key": api_key,
                    "workspace_id": "ws-epoch-adversarial",
                },
            }
        )

    from agent import image_runtime
    from agent.image_gen_verification import (
        ImageGenRequestAuthorizationError,
        authorize_image_gen_request_binding,
        build_image_gen_request_reauth_guard,
    )

    save("image-epoch-a")
    verified_a = model_config.test_image_gen_config()
    assert verified_a["status"] == "verified"
    state_path = model_config._image_gen_verification_state_path("default")
    old_state = json.loads(state_path.read_text(encoding="utf-8"))
    old_runtime = image_runtime.verification_runtime_snapshot(
        "image_generation"
    )
    old_snapshot = model_config._capture_image_gen_config_snapshot()
    assert old_snapshot.probe_binding is not None
    old_binding = authorize_image_gen_request_binding(
        old_snapshot.probe_binding,
        authorization_fingerprint=old_runtime["fingerprint"],
        authorization_generation=old_runtime[
            "_authorization_generation"
        ],
    )
    old_guard = build_image_gen_request_reauth_guard(
        old_binding,
        expected_snapshot=old_runtime,
    )
    old_config = _read_config(hermes_home)

    original_atomic_write_json = model_config._atomic_write_json
    failed_tombstones = []

    def fail_verification_tombstone(path, payload):
        if (
            Path(path) == state_path
            and payload.get("status") == "configured_unverified"
            and payload.get("fingerprint") == ""
        ):
            failed_tombstones.append(dict(payload))
            raise OSError("simulated image tombstone write failure")
        return original_atomic_write_json(path, payload)

    monkeypatch.setattr(
        model_config,
        "_atomic_write_json",
        fail_verification_tombstone,
    )
    save("image-epoch-b")
    save("image-epoch-a")

    current_runtime = image_runtime.verification_runtime_snapshot(
        "image_generation"
    )
    provider_io = []
    cache_writes = []

    def forbidden_provider_io(*args, **kwargs):
        provider_io.append((args, kwargs))
        raise AssertionError("revived image A1 binding reached Provider I/O")

    def forbidden_cache_write(*args, **kwargs):
        cache_writes.append((args, kwargs))
        raise AssertionError("revived image A1 binding wrote cache")

    from plugins.image_gen import dashscope as dashscope_provider
    from plugins.image_gen import domestic_common

    monkeypatch.setattr(
        domestic_common,
        "request_pinned_https",
        forbidden_provider_io,
    )
    monkeypatch.setattr(
        dashscope_provider,
        "_save_safe_image_url",
        forbidden_cache_write,
    )
    blocked_error = ""
    try:
        dashscope_provider.DashScopeQwenImageProvider().generate(
            prompt="tombstone failure ABA must remain blocked",
            aspect_ratio="square",
            model="qwen-image-2.0-pro",
            _runtime_binding=old_binding,
            _reauth_guard=old_guard,
        )
    except ImageGenRequestAuthorizationError as exc:
        blocked_error = exc.error_code
    except Exception as exc:  # noqa: BLE001 - preserve exact RED evidence.
        blocked_error = f"unexpected:{type(exc).__name__}"

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    violations = []
    if len(failed_tombstones) != 2:
        violations.append("B->A did not exercise two tombstone write failures")
    if persisted != old_state:
        violations.append("adversarial setup did not retain old verified A")
    if current_runtime.get("status") != "configured_unverified":
        violations.append("old verified image A revived after B->A")
    if current_runtime.get("fingerprint") == old_runtime.get("fingerprint"):
        violations.append("image capability fingerprint reused the A1 epoch")
    if (
        current_runtime.get("_authorization_generation")
        == old_runtime.get("_authorization_generation")
    ):
        violations.append("image authorization generation reused A1")
    if _read_config(hermes_home) == old_config:
        violations.append("image config omitted a durable capability epoch")
    if blocked_error not in {
        "capability_caller_stale",
        "capability_binding_mismatch",
    }:
        violations.append("old image A1 binding was not rejected")
    if provider_io:
        violations.append("old image A1 binding reached Provider I/O")
    if cache_writes:
        violations.append("old image A1 binding wrote cache")

    assert violations == [], "; ".join(violations)


def test_vision_a_b_a_does_not_revive_old_verified_binding_when_tombstones_fail(
    monkeypatch,
    tmp_path,
):
    """Config epoch, not a best-effort tombstone, must defeat vision ABA."""
    import asyncio
    import tools.vision_tools as vision_tools
    from agent import image_runtime
    from agent.auxiliary_client import capture_vision_request_binding

    hermes_home, _state_dir = _use_persisted_capability_test_home(
        monkeypatch,
        tmp_path,
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    real_vision_boundary = vision_tools.vision_analyze_tool

    async def successful_probe(**_kwargs):
        return json.dumps(
            {
                "success": True,
                "analysis": model_config._VISION_PROBE_MARKER,
                "resolved_provider": "alibaba",
                "resolved_model": "qwen3-vl-plus",
            }
        )

    monkeypatch.setattr(
        vision_tools,
        "vision_analyze_tool",
        successful_probe,
    )

    def save(api_key):
        return model_config.set_vision_config(
            {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "api_key": api_key,
            }
        )

    save("vision-epoch-a")
    verified_a = model_config.test_vision_config()
    assert verified_a["status"] == "verified"
    state_path = model_config._vision_verification_state_path("default")
    old_state = json.loads(state_path.read_text(encoding="utf-8"))
    old_runtime = image_runtime.verification_runtime_snapshot("vision")
    old_binding = capture_vision_request_binding(
        authorization_fingerprint=old_runtime["fingerprint"],
        authorization_generation=old_runtime[
            "_authorization_generation"
        ],
    )
    assert old_binding is not None
    old_config = _read_config(hermes_home)

    original_atomic_write_json = model_config._atomic_write_json
    failed_tombstones = []

    def fail_verification_tombstone(path, payload):
        if (
            Path(path) == state_path
            and payload.get("status") == "configured_unverified"
            and payload.get("fingerprint") == ""
        ):
            failed_tombstones.append(dict(payload))
            raise OSError("simulated vision tombstone write failure")
        return original_atomic_write_json(path, payload)

    monkeypatch.setattr(
        model_config,
        "_atomic_write_json",
        fail_verification_tombstone,
    )
    save("vision-epoch-b")
    save("vision-epoch-a")

    current_runtime = image_runtime.verification_runtime_snapshot("vision")
    monkeypatch.setattr(
        vision_tools,
        "vision_analyze_tool",
        real_vision_boundary,
    )
    provider_io = []
    cache_writes = []

    async def forbidden_provider_io(**kwargs):
        provider_io.append(kwargs)
        raise AssertionError("revived vision A1 binding reached Provider I/O")

    async def forbidden_cache_write(*args, **kwargs):
        cache_writes.append((args, kwargs))
        raise AssertionError("revived vision A1 binding wrote cache")

    monkeypatch.setattr(
        vision_tools,
        "async_call_llm",
        forbidden_provider_io,
    )
    monkeypatch.setattr(
        vision_tools,
        "_download_image",
        forbidden_cache_write,
    )
    local_probe = tmp_path / "tombstone-failure-a1.png"
    local_probe.write_bytes(model_config._VISION_PROBE_PNG)
    blocked = json.loads(
        asyncio.run(
            real_vision_boundary(
                image_url=str(local_probe),
                user_prompt="tombstone failure ABA must remain blocked",
                model="qwen3-vl-plus",
                provider="alibaba",
                strict_target=True,
                _runtime_binding=old_binding,
            )
        )
    )

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    violations = []
    if len(failed_tombstones) != 2:
        violations.append("B->A did not exercise two tombstone write failures")
    if persisted != old_state:
        violations.append("adversarial setup did not retain old verified A")
    if current_runtime.get("status") != "configured_unverified":
        violations.append("old verified vision A revived after B->A")
    if current_runtime.get("fingerprint") == old_runtime.get("fingerprint"):
        violations.append("vision capability fingerprint reused the A1 epoch")
    if (
        current_runtime.get("_authorization_generation")
        == old_runtime.get("_authorization_generation")
    ):
        violations.append("vision authorization generation reused A1")
    if _read_config(hermes_home) == old_config:
        violations.append("vision config omitted a durable capability epoch")
    if blocked.get("error_code") not in {
        "capability_caller_stale",
        "capability_binding_mismatch",
        "verification_required",
    }:
        violations.append(
            "old vision A1 binding was not rejected "
            f"(blocked={blocked!r})"
        )
    if provider_io:
        violations.append("old vision A1 binding reached Provider I/O")
    if cache_writes:
        violations.append("old vision A1 binding wrote cache")

    assert violations == [], "; ".join(violations)


def test_verification_state_file_lock_is_mutually_exclusive_across_processes(
    tmp_path,
):
    """A second worker cannot enter the same state lock before release."""
    context = multiprocessing.get_context("spawn")
    state_path = tmp_path / "cross-process-state" / "verification.json"
    first_attempting = context.Event()
    first_acquired = context.Event()
    release_first = context.Event()
    second_attempting = context.Event()
    second_acquired = context.Event()
    release_second = context.Event()
    result_queue = context.Queue()
    first = context.Process(
        target=_verification_state_lock_process,
        args=(
            str(state_path),
            "first",
            first_attempting,
            first_acquired,
            release_first,
            result_queue,
        ),
    )
    second = context.Process(
        target=_verification_state_lock_process,
        args=(
            str(state_path),
            "second",
            second_attempting,
            second_acquired,
            release_second,
            result_queue,
        ),
    )
    try:
        first.start()
        assert first_attempting.wait(timeout=5)
        assert first_acquired.wait(timeout=5)
        second.start()
        assert second_attempting.wait(timeout=5)
        assert second.is_alive()
        assert second_acquired.wait(timeout=0.5) is False

        release_first.set()
        assert second_acquired.wait(timeout=5)
        release_second.set()
        first.join(timeout=5)
        second.join(timeout=5)
    finally:
        release_first.set()
        release_second.set()
        first.join(timeout=5)
        second.join(timeout=5)
        if first.is_alive():
            first.terminate()
            first.join(timeout=5)
        if second.is_alive():
            second.terminate()
            second.join(timeout=5)

    assert first.exitcode == 0
    assert second.exitcode == 0
    events = [result_queue.get(timeout=5) for _ in range(4)]
    assert events.index(("first", "exited")) < events.index(
        ("second", "entered")
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX lockfile safety")
def test_verification_state_lock_rejects_symlink_without_touching_target(
    tmp_path,
):
    if not getattr(os, "O_NOFOLLOW", 0):
        pytest.skip("platform does not expose O_NOFOLLOW")
    state_path = tmp_path / "symlink-state.json"
    lock_path = state_path.with_name(f".{state_path.name}.lock")
    protected = tmp_path / "symlink-protected.txt"
    protected.write_bytes(b"protected-symlink-target")
    protected.chmod(0o640)
    lock_path.symlink_to(protected)
    original_payload = protected.read_bytes()
    original_mode = protected.stat().st_mode & 0o777

    with pytest.raises(OSError):
        with model_config._verification_state_file_lock(state_path):
            raise AssertionError("unsafe symlink lock was entered")

    assert protected.read_bytes() == original_payload
    assert protected.stat().st_mode & 0o777 == original_mode
    assert lock_path.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX lockfile safety")
def test_verification_state_lock_rejects_hardlink_without_touching_target(
    tmp_path,
):
    state_path = tmp_path / "hardlink-state.json"
    lock_path = state_path.with_name(f".{state_path.name}.lock")
    protected = tmp_path / "hardlink-protected.txt"
    protected.write_bytes(b"protected-hardlink-target")
    protected.chmod(0o640)
    os.link(protected, lock_path)
    original_payload = protected.read_bytes()
    original_mode = protected.stat().st_mode & 0o777

    with pytest.raises(OSError, match="unsafe verification state lock"):
        with model_config._verification_state_file_lock(state_path):
            raise AssertionError("unsafe hardlink lock was entered")

    assert protected.read_bytes() == original_payload
    assert protected.stat().st_mode & 0o777 == original_mode
    assert protected.stat().st_nlink == 2


def test_windows_verification_state_lock_delegates_to_named_mutex_without_open(
    monkeypatch,
    tmp_path,
):
    state_path = tmp_path / "windows-state.json"
    mutex_calls = []
    open_calls = []

    @contextmanager
    def named_mutex(path):
        mutex_calls.append(("enter", Path(path)))
        try:
            yield
        finally:
            mutex_calls.append(("exit", Path(path)))

    def forbidden_open(*args, **kwargs):
        open_calls.append((args, kwargs))
        raise AssertionError("Windows lock path must not call os.open")

    monkeypatch.setattr(
        model_config,
        "_windows_verification_state_mutex",
        named_mutex,
    )
    monkeypatch.setattr(
        model_config,
        "os",
        SimpleNamespace(name="nt", open=forbidden_open),
    )

    with model_config._verification_state_file_lock(state_path):
        mutex_calls.append(("body", state_path))

    assert mutex_calls == [
        ("enter", state_path),
        ("body", state_path),
        ("exit", state_path),
    ]
    assert open_calls == []
    assert not state_path.with_name(f".{state_path.name}.lock").exists()


def test_cross_process_newer_probe_generation_blocks_old_final_cas(
    monkeypatch,
    tmp_path,
):
    """A newer process-owned begin must survive an old worker's final CAS."""
    context = multiprocessing.get_context("spawn")
    state_dir = tmp_path / "cross-process-webui-state"
    state_dir.mkdir()
    profile = "cross-process-profile"
    old_began = context.Event()
    newer_began = context.Event()
    result_queue = context.Queue()
    old_owner = context.Process(
        target=_old_vision_probe_owner_process,
        args=(
            str(state_dir),
            profile,
            old_began,
            newer_began,
            result_queue,
        ),
    )
    new_owner = context.Process(
        target=_newer_vision_probe_owner_process,
        args=(
            str(state_dir),
            profile,
            old_began,
            newer_began,
            result_queue,
        ),
    )
    try:
        old_owner.start()
        new_owner.start()
        old_owner.join(timeout=10)
        new_owner.join(timeout=10)
    finally:
        old_began.set()
        newer_began.set()
        old_owner.join(timeout=5)
        new_owner.join(timeout=5)
        if old_owner.is_alive():
            old_owner.terminate()
            old_owner.join(timeout=5)
        if new_owner.is_alive():
            new_owner.terminate()
            new_owner.join(timeout=5)

    assert old_owner.exitcode == 0
    assert new_owner.exitcode == 0
    results = dict(result_queue.get(timeout=5) for _ in range(3))
    assert results == {
        "old_generation": 1,
        "new_generation": 2,
        "old_commit": False,
    }

    monkeypatch.setattr(api_config, "STATE_DIR", state_dir)
    state_path = model_config._vision_verification_state_path(profile)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "verifying"
    assert persisted["generation"] == 2
    assert persisted["fingerprint"] == "cross-process-vision-new"
    assert persisted["diagnostic_id"] == "cross-process-new-owner"


@pytest.mark.parametrize("capability", ["vision", "image"])
def test_cold_worker_invalidation_preserves_durable_generation_tombstone(
    monkeypatch,
    tmp_path,
    capability,
):
    """Cold workers must derive invalidation ownership from durable state."""
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    profile = f"cold-worker-{capability}"
    state_path = tmp_path / f"{capability}-cold-worker-state.json"
    if capability == "vision":
        monkeypatch.setattr(
            model_config,
            "_vision_verification_state_path",
            lambda *_args, **_kwargs: state_path,
        )
        monkeypatch.setattr(model_config, "_VISION_PROBE_GENERATIONS", {})
        generation_map = model_config._VISION_PROBE_GENERATIONS
        capture = model_config._capture_vision_verification_invalidation
        invalidate = model_config._invalidate_vision_verification
        begin = model_config._begin_vision_probe
    else:
        monkeypatch.setattr(
            model_config,
            "_image_gen_verification_state_path",
            lambda *_args, **_kwargs: state_path,
        )
        monkeypatch.setattr(model_config, "_IMAGE_GEN_PROBE_GENERATIONS", {})
        generation_map = model_config._IMAGE_GEN_PROBE_GENERATIONS
        capture = model_config._capture_image_gen_verification_invalidation
        invalidate = model_config._invalidate_image_gen_verification
        begin = model_config._begin_image_gen_probe

    model_config._atomic_write_json(
        state_path,
        {
            "schema_version": 1,
            "generation": 5,
            "fingerprint": f"{capability}-durable-generation-five",
            "status": "verifying",
            "checked_at": "2030-01-01T00:00:00Z",
            "error_code": "",
            "message": "durable generation five",
            "diagnostic_id": f"{capability}-generation-five",
        },
    )

    captured = capture(profile)
    invalidated = invalidate(captured)
    tombstone_exists = state_path.exists()
    tombstone = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if tombstone_exists
        else {}
    )
    tombstone_generation = tombstone.get("generation")

    generation_map.clear()
    restart_generation = begin(
        profile,
        {
            "schema_version": 1,
            "fingerprint": f"{capability}-after-restart",
            "status": "verifying",
            "checked_at": "2030-01-01T00:00:01Z",
            "error_code": "",
            "message": "probe after cold restart",
            "diagnostic_id": f"{capability}-after-restart",
        },
    )
    restarted = json.loads(state_path.read_text(encoding="utf-8"))

    violations = []
    if captured.generation != 5:
        violations.append(
            "cold worker captured process-local generation "
            f"{captured.generation} instead of durable generation 5"
        )
    if invalidated is not True:
        violations.append("durable generation token did not invalidate its state")
    if not tombstone_exists:
        violations.append("invalidation unlinked durable generation history")
    if (
        type(tombstone_generation) is not int
        or tombstone_generation < 6
    ):
        violations.append(
            "invalidation did not persist a generation >= 6 tombstone"
        )
    if str(tombstone.get("status") or "") in {
        "verifying",
        "verified",
        "failed",
    }:
        violations.append("tombstone retained an authorizing probe status")
    durable_floor = (
        tombstone_generation
        if type(tombstone_generation) is int
        else 5
    )
    if restart_generation <= durable_floor:
        violations.append(
            "cold restart reused an invalidated durable generation "
            f"({restart_generation} <= {durable_floor})"
        )
    if restarted.get("generation") != restart_generation:
        violations.append("restart begin did not persist its returned generation")

    assert violations == [], "; ".join(violations)


def _prepare_setter_probe_race(
    monkeypatch,
    tmp_path,
    capability,
    *,
    probe_started=None,
    release_probe=None,
):
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    state_path = tmp_path / f"{capability}-setter-finalization-race.json"

    if capability == "vision":
        monkeypatch.setattr(
            model_config,
            "_vision_verification_state_path",
            lambda *_args, **_kwargs: state_path,
        )
        monkeypatch.setattr(model_config, "_VISION_PROBE_GENERATIONS", {})
        _write_saved_vision_config(tmp_path)

        async def successful_probe(*, provider, model, **_kwargs):
            if probe_started is not None:
                probe_started.set()
                assert release_probe.wait(timeout=10)
            return json.dumps(
                {
                    "success": True,
                    "analysis": model_config._VISION_PROBE_MARKER,
                    "resolved_provider": provider,
                    "resolved_model": model,
                }
            )

        monkeypatch.setattr(
            "tools.vision_tools.vision_analyze_tool",
            successful_probe,
        )
        return SimpleNamespace(
            state_path=state_path,
            snapshot_unlocked_name="_capture_vision_config_snapshot_unlocked",
            token_capture_name="_capture_vision_verification_invalidation",
            invalidator_name="_invalidate_vision_verification",
            probe=model_config.test_vision_config,
            setter=model_config.set_vision_config,
            setter_body={
                "provider": "alibaba",
                "model": "qwen3-vl-flash",
                "api_key": "vision-config-b-secret",
            },
            expected_superseded="vision_probe_superseded",
            initial_model="qwen3-vl-plus",
            expected_model="qwen3-vl-flash",
            committed_model=lambda config: config["auxiliary"]["vision"][
                "model"
            ],
            response_model=lambda response: response.get("vision", {}).get(
                "model"
            ),
        )
    else:
        monkeypatch.setattr(
            model_config,
            "_image_gen_verification_state_path",
            lambda *_args, **_kwargs: state_path,
        )
        monkeypatch.setattr(model_config, "_IMAGE_GEN_PROBE_GENERATIONS", {})
        _write_saved_image_gen_config(tmp_path)

        def image_provider_rows(_active):
            row = _dashscope_image_provider_row()
            row["models"].append(
                {"id": "qwen-image", "label": "Qwen Image"}
            )
            return [row]

        monkeypatch.setattr(
            model_config,
            "_image_gen_provider_rows",
            image_provider_rows,
        )

        def successful_probe(*_args, **_kwargs):
            if probe_started is not None:
                probe_started.set()
                assert release_probe.wait(timeout=10)
            return (
                True,
                "",
                "真实生图验证通过。",
            )

        monkeypatch.setattr(
            model_config,
            "_execute_image_gen_probe",
            successful_probe,
        )
        return SimpleNamespace(
            state_path=state_path,
            snapshot_unlocked_name="_capture_image_gen_config_snapshot_unlocked",
            token_capture_name="_capture_image_gen_verification_invalidation",
            invalidator_name="_invalidate_image_gen_verification",
            probe=model_config.test_image_gen_config,
            setter=model_config.set_image_gen_config,
            setter_body={
                "provider": "dashscope",
                "model": "qwen-image",
                "api_key": "image-config-b-secret",
            },
            expected_superseded="image_gen_probe_superseded",
            initial_model="qwen-image-2.0-pro",
            expected_model="qwen-image",
            committed_model=lambda config: config["image_gen"]["model"],
            response_model=lambda response: response.get(
                "image_gen", {}
            ).get("model"),
        )


@pytest.mark.parametrize("capability", ["vision", "image"])
def test_probe_final_snapshot_and_setter_commit_are_serialized(
    monkeypatch,
    tmp_path,
    capability,
):
    """Once old final holds the config lock, setter B must wait its turn."""
    race = _prepare_setter_probe_race(
        monkeypatch,
        tmp_path,
        capability,
    )
    final_snapshot_read = threading.Event()
    release_old_final = threading.Event()
    setter_token_captured = threading.Event()
    release_setter = threading.Event()
    probe_thread = {"ident": None}
    probe_snapshot_calls = {"value": 0}
    snapshot_calls_lock = threading.Lock()
    captured_tokens = []
    invalidation_results = []
    original_unlocked = getattr(
        model_config,
        race.snapshot_unlocked_name,
    )
    original_token_capture = getattr(
        model_config,
        race.token_capture_name,
    )
    original_invalidator = getattr(
        model_config,
        race.invalidator_name,
    )

    def capture_unlocked_and_pause_probe_final(*args, **kwargs):
        snapshot = original_unlocked(*args, **kwargs)
        if threading.get_ident() != probe_thread["ident"]:
            return snapshot
        with snapshot_calls_lock:
            probe_snapshot_calls["value"] += 1
            call_number = probe_snapshot_calls["value"]
        if call_number == 2:
            final_snapshot_read.set()
            assert release_old_final.wait(timeout=10)
        return snapshot

    def capture_setter_token_and_pause(profile=None):
        token = original_token_capture(profile)
        captured_tokens.append(token)
        setter_token_captured.set()
        assert release_setter.wait(timeout=10)
        return token

    def record_invalidation(expected=None):
        result = original_invalidator(expected)
        invalidation_results.append(result)
        return result

    monkeypatch.setattr(
        model_config,
        race.snapshot_unlocked_name,
        capture_unlocked_and_pause_probe_final,
    )
    monkeypatch.setattr(
        model_config,
        race.token_capture_name,
        capture_setter_token_and_pause,
    )
    monkeypatch.setattr(
        model_config,
        race.invalidator_name,
        record_invalidation,
    )

    def run_probe():
        probe_thread["ident"] = threading.get_ident()
        return race.probe()

    executor = ThreadPoolExecutor(max_workers=2)
    old_result = {}
    setter_result = {}
    committed_b = {}
    verifying_a = {}
    state_after_token = {}
    setter_blocked = False
    config_while_blocked = {}
    try:
        old_future = executor.submit(run_probe)
        assert final_snapshot_read.wait(timeout=5)
        verifying_a = json.loads(
            race.state_path.read_text(encoding="utf-8")
        )

        setter_future = executor.submit(race.setter, race.setter_body)
        setter_blocked = not setter_token_captured.wait(timeout=0.5)
        config_while_blocked = _read_config(tmp_path)

        release_old_final.set()
        assert setter_token_captured.wait(timeout=5)
        committed_b = _read_config(tmp_path)
        state_after_token = json.loads(
            race.state_path.read_text(encoding="utf-8")
        )

        release_setter.set()
        setter_result = setter_future.result(timeout=5)
        old_result = old_future.result(timeout=5)
    finally:
        release_old_final.set()
        release_setter.set()
        executor.shutdown(wait=True)

    persisted = (
        json.loads(race.state_path.read_text(encoding="utf-8"))
        if race.state_path.exists()
        else {}
    )
    old_fingerprint = str(verifying_a.get("fingerprint") or "")
    old_diagnostic_id = str(verifying_a.get("diagnostic_id") or "")
    violations = []
    if verifying_a.get("status") != "verifying":
        violations.append("old probe was not paused after a verifying A begin")
    if not setter_blocked:
        violations.append(
            "setter captured its token while old final held the config lock"
        )
    if race.committed_model(config_while_blocked) != race.initial_model:
        violations.append("config B committed through the old final lock")
    if old_result.get("status") != "verified":
        violations.append("linearized old final did not commit verified A")
    if state_after_token.get("diagnostic_id") != old_diagnostic_id:
        violations.append(
            "setter did not capture ownership of the linearized A final"
        )
    if race.committed_model(committed_b) != race.expected_model:
        violations.append("config B did not commit after old final released")
    if invalidation_results != [True]:
        violations.append(
            "setter invalidation lost ownership after old final changed identity"
        )
    if (
        persisted.get("status") == "verified"
        and persisted.get("fingerprint") == old_fingerprint
    ):
        violations.append("disk retained the stale verified A result")
    if not captured_tokens:
        violations.append("setter did not publish an invalidation token")
    if race.response_model(setter_result) != race.expected_model:
        violations.append("setter response did not stay on committed config B")

    assert violations == [], "; ".join(violations)


@pytest.mark.parametrize("capability", ["vision", "image"])
def test_setter_commit_before_probe_final_supersedes_old_probe(
    monkeypatch,
    tmp_path,
    capability,
):
    """If setter B linearizes first, old A final cannot restore verification."""
    probe_started = threading.Event()
    release_probe = threading.Event()
    race = _prepare_setter_probe_race(
        monkeypatch,
        tmp_path,
        capability,
        probe_started=probe_started,
        release_probe=release_probe,
    )
    setter_token_captured = threading.Event()
    captured_tokens = []
    invalidation_results = []
    original_token_capture = getattr(
        model_config,
        race.token_capture_name,
    )
    original_invalidator = getattr(
        model_config,
        race.invalidator_name,
    )

    def capture_setter_token(profile=None):
        token = original_token_capture(profile)
        captured_tokens.append(token)
        setter_token_captured.set()
        return token

    def record_invalidation(expected=None):
        result = original_invalidator(expected)
        invalidation_results.append(result)
        return result

    monkeypatch.setattr(
        model_config,
        race.token_capture_name,
        capture_setter_token,
    )
    monkeypatch.setattr(
        model_config,
        race.invalidator_name,
        record_invalidation,
    )

    executor = ThreadPoolExecutor(max_workers=2)
    old_result = {}
    setter_result = {}
    verifying_a = {}
    state_after_setter = {}
    try:
        old_future = executor.submit(race.probe)
        assert probe_started.wait(timeout=5)
        verifying_a = json.loads(
            race.state_path.read_text(encoding="utf-8")
        )

        setter_future = executor.submit(race.setter, race.setter_body)
        assert setter_token_captured.wait(timeout=5)
        setter_result = setter_future.result(timeout=5)
        state_after_setter = json.loads(
            race.state_path.read_text(encoding="utf-8")
        )

        release_probe.set()
        old_result = old_future.result(timeout=5)
    finally:
        release_probe.set()
        executor.shutdown(wait=True)

    persisted = json.loads(
        race.state_path.read_text(encoding="utf-8")
    )
    old_fingerprint = str(verifying_a.get("fingerprint") or "")
    violations = []
    if verifying_a.get("status") != "verifying":
        violations.append("old A probe did not reach persisted verifying")
    if not captured_tokens:
        violations.append("setter B did not capture the verifying A token")
    if invalidation_results != [True]:
        violations.append("setter B failed to invalidate verifying A")
    if race.response_model(setter_result) != race.expected_model:
        violations.append("setter response did not stay on committed config B")
    if state_after_setter.get("status") in {"verifying", "verified"}:
        violations.append("setter B did not persist an invalidation tombstone")
    if old_result.get("error_code") != race.expected_superseded:
        violations.append("old A probe was not reported as superseded")
    if old_result.get("status") != "configured_unverified":
        violations.append("old A probe returned a non-superseded status")
    if (
        persisted.get("status") == "verified"
        and persisted.get("fingerprint") == old_fingerprint
    ):
        violations.append("old final restored stale verified A on disk")

    assert violations == [], "; ".join(violations)


def _write_implicit_alibaba_default_capabilities(
    home: Path,
    *,
    vision_epoch: int = 11,
    image_epoch: int = 17,
) -> None:
    """Write the legacy-empty-ref shape that resolves the family default."""
    credentials = [
        {
            "id": "alibaba-default-a",
            "provider_family": "alibaba_dashscope",
            "label": "Alibaba default A",
            "auth_type": "api_key",
            "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_A_API_KEY",
            "default": True,
        },
        {
            "id": "alibaba-default-b",
            "provider_family": "alibaba_dashscope",
            "label": "Alibaba default B",
            "auth_type": "api_key",
            "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_B_API_KEY",
        },
    ]
    config = {
        "provider_credentials": credentials,
        "auxiliary": {
            "vision": {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "credential_ref": "",
            }
        },
        "image_gen": {
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "credential_ref": "",
        },
        "_taiji_capability_epochs": {
            "vision": vision_epoch,
            "image_generation": image_epoch,
        },
    }
    (home / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    (home / ".env").write_text(
        "\n".join(
            (
                "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_A_API_KEY=implicit-a",
                "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_B_API_KEY=implicit-b",
                "",
            )
        ),
        encoding="utf-8",
    )


def _capability_epochs(home: Path) -> tuple[int, int]:
    epochs = _read_config(home).get("_taiji_capability_epochs") or {}
    return (
        int(epochs.get("vision") or 0),
        int(epochs.get("image_generation") or 0),
    )


def test_implicit_default_secret_rotation_advances_both_capability_epochs(
    monkeypatch,
    tmp_path,
):
    """An empty credential_ref still consumes the family default secret."""
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _write_implicit_alibaba_default_capabilities(tmp_path)
    before = _capability_epochs(tmp_path)

    result = model_config.upsert_provider_credential(
        {
            "id": "alibaba-default-a",
            "provider": "alibaba",
            "label": "Alibaba default A",
            "api_key": "implicit-a-rotated",
        }
    )

    after = _capability_epochs(tmp_path)
    assert after[0] > before[0]
    assert after[1] > before[1]
    assert result["credential"]["used_by"] == [
        "auxiliary.vision",
        "image_gen",
    ]


def test_implicit_default_marker_switch_advances_both_capability_epochs(
    monkeypatch,
    tmp_path,
):
    """Removing and selecting a family default are authorization mutations."""
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _write_implicit_alibaba_default_capabilities(tmp_path)
    initial = _capability_epochs(tmp_path)

    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default-a",
            "provider": "alibaba",
            "label": "Alibaba default A",
            "default": False,
        }
    )
    without_default = _capability_epochs(tmp_path)
    model_config.upsert_provider_credential(
        {
            "id": "alibaba-default-b",
            "provider": "alibaba",
            "label": "Alibaba default B",
            "default": True,
        }
    )
    switched = _capability_epochs(tmp_path)

    assert without_default[0] > initial[0]
    assert without_default[1] > initial[1]
    assert switched[0] > without_default[0]
    assert switched[1] > without_default[1]


def test_implicit_default_credential_cannot_be_deleted_while_in_use(
    monkeypatch,
    tmp_path,
):
    """Deletion must account for family-default resolution, not only refs."""
    _use_home(monkeypatch, tmp_path, stub_image_gen=False)
    _write_implicit_alibaba_default_capabilities(tmp_path)
    config_before = (tmp_path / "config.yaml").read_bytes()
    env_before = (tmp_path / ".env").read_bytes()

    with pytest.raises(ValueError, match="正在使用"):
        model_config.delete_provider_credential("alibaba-default-a")

    assert (tmp_path / "config.yaml").read_bytes() == config_before
    assert (tmp_path / ".env").read_bytes() == env_before


def test_implicit_default_secret_a_b_a_rejects_old_image_proof_before_io(
    monkeypatch,
    tmp_path,
):
    """A restored secret value must not revive the old verified generation."""
    from agent import image_runtime
    from agent.image_gen_verification import (
        ImageGenRequestAuthorizationError,
        authorize_image_gen_request_binding,
        build_image_gen_request_reauth_guard,
    )
    from plugins.image_gen import dashscope as dashscope_provider
    from plugins.image_gen import domestic_common

    hermes_home, _state_dir = _use_persisted_capability_test_home(
        monkeypatch,
        tmp_path,
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    _write_implicit_alibaba_default_capabilities(
        hermes_home,
        vision_epoch=0,
        image_epoch=0,
    )
    a1_snapshot = model_config._capture_image_gen_config_snapshot()
    assert a1_snapshot.configured is True
    assert a1_snapshot.probe_binding is not None
    generation = model_config._begin_image_gen_probe(
        "default",
        {
            "schema_version": model_config.CAPABILITY_VERIFICATION_SCHEMA_VERSION,
            "fingerprint": a1_snapshot.fingerprint,
            "status": "verified",
            "checked_at": "2030-01-01T00:00:00Z",
            "error_code": "",
            "message": "",
            "diagnostic_id": "implicit-default-a1",
        },
    )
    assert generation > 0
    a1_runtime = image_runtime.verification_runtime_snapshot(
        "image_generation"
    )
    assert a1_runtime["status"] == "verified"
    old_binding = authorize_image_gen_request_binding(
        a1_snapshot.probe_binding,
        authorization_fingerprint=a1_runtime["fingerprint"],
        authorization_generation=a1_runtime[
            "_authorization_generation"
        ],
    )
    old_guard = build_image_gen_request_reauth_guard(
        old_binding,
        expected_snapshot=a1_runtime,
    )

    for secret in ("implicit-b", "implicit-a"):
        model_config.upsert_provider_credential(
            {
                "id": "alibaba-default-a",
                "provider": "alibaba",
                "label": "Alibaba default A",
                "api_key": secret,
            }
        )

    current_runtime = image_runtime.verification_runtime_snapshot(
        "image_generation"
    )
    provider_io = []

    def forbidden_provider_io(*args, **kwargs):
        provider_io.append((args, kwargs))
        raise AssertionError("stale A1 proof reached Provider I/O")

    monkeypatch.setattr(
        domestic_common,
        "request_pinned_https",
        forbidden_provider_io,
    )
    blocked_error = ""
    try:
        dashscope_provider.DashScopeQwenImageProvider().generate(
            prompt="implicit default ABA must remain blocked",
            aspect_ratio="square",
            model="qwen-image-2.0-pro",
            _runtime_binding=old_binding,
            _reauth_guard=old_guard,
        )
    except ImageGenRequestAuthorizationError as exc:
        blocked_error = exc.error_code

    violations = []
    if current_runtime.get("fingerprint") == a1_runtime.get("fingerprint"):
        violations.append("implicit default A1 fingerprint revived after A-B-A")
    if current_runtime.get("status") == "verified":
        violations.append("implicit default A1 verified state revived after A-B-A")
    if blocked_error not in {
        "capability_caller_stale",
        "capability_binding_mismatch",
    }:
        violations.append("old implicit-default A1 proof was not rejected")
    if provider_io:
        violations.append("old implicit-default A1 proof reached Provider I/O")

    assert violations == [], "; ".join(violations)
