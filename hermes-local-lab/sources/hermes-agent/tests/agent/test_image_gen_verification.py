from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def test_stale_verifying_state_degrades_to_configured_unverified(tmp_path):
    from agent.image_gen_verification import (
        image_gen_fingerprint,
        read_image_gen_verification_status,
        verification_state_path,
    )

    config = {"image_gen": {"provider": "dashscope", "model": "qwen-image"}}
    fingerprint = image_gen_fingerprint(
        config["image_gen"], profile="default", config_data=config, secret_value="key"
    )
    state_path = verification_state_path(tmp_path, "default")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    state_path.write_text(
        json.dumps({"status": "verifying", "checked_at": stale, "fingerprint": fingerprint}),
        encoding="utf-8",
    )

    assert read_image_gen_verification_status(
        config["image_gen"],
        profile="default",
        config_data=config,
        secret_value="key",
        state_root=tmp_path,
    ) == "configured_unverified"


def test_agent_only_runtime_reads_matching_verified_webui_state(tmp_path):
    agent_root = Path(__file__).resolve().parents[2]
    home = tmp_path / "runtime" / "profiles" / "named-profile"
    state_root = tmp_path / "web-state"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        "image_gen:\n  provider: dashscope\n  model: qwen-image\n", encoding="utf-8"
    )
    script = r'''
import json
from agent.image_gen_verification import image_gen_fingerprint, verification_state_path
from tools import image_generation_tool as tool

cfg = {"image_gen": {"provider": "dashscope", "model": "qwen-image"}}
fp = image_gen_fingerprint(cfg["image_gen"], profile="named-profile", config_data=cfg, secret_value="agent-only-secret")
path = verification_state_path(None, "named-profile")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({"status": "verified", "checked_at": "2030-01-01T00:00:00Z", "fingerprint": fp}), encoding="utf-8")
tool._load_image_gen_config = lambda: cfg["image_gen"]
tool._load_image_gen_full_config = lambda: cfg
class Provider:
    name = "dashscope"
    def is_available(self): return True
tool._iter_image_generation_providers = lambda: [Provider()]
print(json.dumps(tool.get_image_generation_readiness()))
'''
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(agent_root),
            "HERMES_HOME": str(home),
            "TAIJI_WEBUI_STATE_DIR": str(state_root),
            "HERMES_PROFILE": "unrelated-worker-name",
            "DASHSCOPE_API_KEY": "agent-only-secret",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, text=True, capture_output=True, check=True
    )

    payload = json.loads(result.stdout.strip())
    assert payload["available"] is True
    assert payload["verification_status"] == "verified"


def test_image_gen_secret_env_named_ref_never_falls_back_to_legacy():
    from agent.image_gen_verification import image_gen_secret_env

    valid = {
        "id": "alibaba-image",
        "provider_family": "alibaba_dashscope",
        "secret_env": "TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY",
    }

    assert image_gen_secret_env(
        "dashscope", "missing", {"provider_credentials": []}
    ) == ""
    assert image_gen_secret_env(
        "dashscope",
        "alibaba-image",
        {"provider_credentials": [{**valid, "provider_family": "zhipu"}]},
    ) == ""
    assert image_gen_secret_env(
        "dashscope",
        "alibaba-image",
        {"provider_credentials": [{**valid, "secret_env": "DASHSCOPE_API_KEY"}]},
    ) == ""
    assert image_gen_secret_env(
        "dashscope",
        "alibaba-image",
        {"provider_credentials": [valid]},
    ) == "TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY"
    assert image_gen_secret_env("dashscope", "", {}) == "DASHSCOPE_API_KEY"


def test_custom_image_identity_reads_only_deterministic_legacy_secret_env():
    from agent.image_gen_verification import (
        active_custom_provider_identity,
        image_gen_secret_env,
    )

    config = {
        "custom_image_providers": [
            {
                "id": "router",
                "base_url": "https://images.example.com/v1",
                "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
                "models": ["image-model"],
                "default_model": "image-model",
            }
        ]
    }

    identity = active_custom_provider_identity("custom:router", config)

    assert identity["id"] == "router"
    assert identity["credential_ref"] == ""
    assert identity["secret_env"] == "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY"
    assert image_gen_secret_env("custom:router", "", config) == identity["secret_env"]
    assert "api_key" not in identity


def test_custom_image_identity_includes_canonical_credential_and_network_contract():
    from agent.image_gen_verification import (
        active_custom_provider_identity,
        image_gen_secret_env,
    )

    config = {
        "provider_credentials": [
            {
                "id": "router-image",
                "provider_family": "custom",
                "secret_env": "TAIJI_CREDENTIAL_ROUTER_IMAGE_API_KEY",
            }
        ],
        "custom_image_providers": [
            {
                "id": "router",
                "base_url": "https://images.example.com/v1",
                "credential_ref": "router-image",
                "models": ["image-model"],
                "default_model": "image-model",
                "network_scope": "trusted_proxy",
                "trusted_proxy_profile": "corp-egress",
            }
        ],
    }

    identity = active_custom_provider_identity("custom:router", config)

    assert identity["credential_ref"] == "router-image"
    assert identity["secret_env"] == "TAIJI_CREDENTIAL_ROUTER_IMAGE_API_KEY"
    assert identity["network_scope"] == "trusted_proxy"
    assert identity["trusted_proxy_profile"] == "corp-egress"
    assert image_gen_secret_env("custom:router", "", config) == identity["secret_env"]


def test_custom_image_identity_rejects_normalized_provider_id_collision():
    from agent.image_gen_verification import active_custom_provider_identity

    config = {
        "custom_image_providers": [
            {
                "id": "router@prod",
                "base_url": "https://first.example.com/v1",
                "credential_ref": "first-credential",
                "models": ["first-model"],
            },
            {
                "id": "router-prod",
                "base_url": "https://last.example.com/v1",
                "credential_ref": "last-credential",
                "models": ["last-model"],
            },
        ]
    }

    with pytest.raises(ValueError, match="重复"):
        active_custom_provider_identity("custom:router-prod", config)


def test_taiji_runtime_forces_default_verification_profile(monkeypatch, tmp_path):
    from agent.image_gen_verification import active_profile_name

    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("HERMES_PROFILE_NAME", "named-profile")
    monkeypatch.setenv("HERMES_PROFILE", "worker-profile")
    assert active_profile_name() == "default"

    monkeypatch.delenv("HERMES_PROFILE_NAME")
    assert active_profile_name() == "default"


def test_non_taiji_runtime_keeps_named_verification_profile(monkeypatch, tmp_path):
    from agent.image_gen_verification import active_profile_name

    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "runtime" / "profiles" / "named-profile"))
    monkeypatch.setenv("HERMES_PROFILE", "worker-profile")

    assert active_profile_name() == "named-profile"
