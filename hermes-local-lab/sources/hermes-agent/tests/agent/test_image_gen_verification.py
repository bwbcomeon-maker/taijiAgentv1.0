from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


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
