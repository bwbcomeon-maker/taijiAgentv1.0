from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
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
from agent.image_gen_verification import (
    CAPABILITY_VERIFICATION_SCHEMA_VERSION,
    image_gen_fingerprint,
    verification_state_path,
)
from tools import image_generation_tool as tool

cfg = {"image_gen": {"provider": "dashscope", "model": "qwen-image"}}
fp = image_gen_fingerprint(cfg["image_gen"], profile="named-profile", config_data=cfg, secret_value="agent-only-secret")
path = verification_state_path(None, "named-profile")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({"schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION, "status": "verified", "checked_at": "2030-01-01T00:00:00Z", "fingerprint": fp}), encoding="utf-8")
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


def test_dashscope_fingerprint_tracks_canonical_runtime_endpoint_env_drift(
    monkeypatch,
    tmp_path,
):
    """The verified identity must be the endpoint the Provider will call."""
    from agent import image_gen_verification as verification
    from plugins.image_gen.dashscope import DashScopeQwenImageProvider

    endpoint_envs = (
        "DASHSCOPE_ENDPOINT_MODE",
        "DASHSCOPE_REGION",
        "DASHSCOPE_WORKSPACE_ID",
        "DASHSCOPE_BASE_URL",
    )
    for name in endpoint_envs:
        monkeypatch.delenv(name, raising=False)

    image_cfg = {
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
        "options": {
            "endpoint_mode": "public",
            "region": "cn-beijing",
        },
    }
    config = {"image_gen": image_cfg}
    provider = DashScopeQwenImageProvider()
    monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "public")
    monkeypatch.setenv("DASHSCOPE_REGION", "cn-beijing")
    endpoint_before = provider._endpoint(image_cfg=image_cfg)
    fingerprint_before = verification.image_gen_fingerprint(
        image_cfg,
        profile="default",
        config_data=config,
        secret_value="dashscope-test-key",
    )
    monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "unused-public-workspace")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://unused-public.example.test")
    endpoint_with_unused_inputs = provider._endpoint(image_cfg=image_cfg)
    fingerprint_with_unused_inputs = verification.image_gen_fingerprint(
        image_cfg,
        profile="default",
        config_data=config,
        secret_value="dashscope-test-key",
    )
    monkeypatch.delenv("DASHSCOPE_WORKSPACE_ID")
    monkeypatch.delenv("DASHSCOPE_BASE_URL")
    state_path = verification.verification_state_path(tmp_path, "default")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": verification.CAPABILITY_VERIFICATION_SCHEMA_VERSION,
                "status": "verified",
                "fingerprint": fingerprint_before,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("DASHSCOPE_REGION", "ap-southeast-1")
    endpoint_after = provider._endpoint(image_cfg=image_cfg)
    fingerprint_after = verification.image_gen_fingerprint(
        image_cfg,
        profile="default",
        config_data=config,
        secret_value="dashscope-test-key",
    )
    status_after = verification.read_image_gen_verification_status(
        image_cfg,
        profile="default",
        config_data=config,
        secret_value="dashscope-test-key",
        state_root=tmp_path,
    )

    violations = []
    if endpoint_before == endpoint_after:
        violations.append("DashScope runtime endpoint did not follow REGION")
    if endpoint_with_unused_inputs != endpoint_before:
        violations.append("unused public-mode inputs changed the runtime endpoint")
    if fingerprint_with_unused_inputs != fingerprint_before:
        violations.append("fingerprint included unused public-mode endpoint inputs")
    if fingerprint_before == fingerprint_after:
        violations.append("fingerprint ignored the changed effective endpoint")
    if status_after == "verified":
        violations.append("old verified state survived effective endpoint drift")

    resolver = getattr(verification, "image_gen_runtime_identity", None)
    if not callable(resolver):
        violations.append("canonical image runtime identity resolver is missing")
    else:
        cases = (
            (
                {
                    "DASHSCOPE_ENDPOINT_MODE": "public",
                    "DASHSCOPE_REGION": "ap-southeast-1",
                },
                image_cfg,
                (
                    "https://dashscope-intl.aliyuncs.com/api/v1/services/"
                    "aigc/multimodal-generation/generation"
                ),
            ),
            (
                {
                    "DASHSCOPE_ENDPOINT_MODE": "workspace",
                    "DASHSCOPE_REGION": "cn-beijing",
                    "DASHSCOPE_WORKSPACE_ID": "b3-workspace",
                },
                image_cfg,
                (
                    "https://b3-workspace.cn-beijing.maas.aliyuncs.com/"
                    "api/v1/services/aigc/multimodal-generation/generation"
                ),
            ),
            (
                {
                    "DASHSCOPE_ENDPOINT_MODE": "custom",
                    "DASHSCOPE_BASE_URL": "https://gateway.example.test",
                },
                image_cfg,
                (
                    "https://gateway.example.test/api/v1/services/"
                    "aigc/multimodal-generation/generation"
                ),
            ),
            (
                {
                    "DASHSCOPE_ENDPOINT_MODE": "custom",
                    "DASHSCOPE_REGION": "ap-southeast-1",
                    "DASHSCOPE_WORKSPACE_ID": "legacy-workspace",
                    "DASHSCOPE_BASE_URL": "https://legacy.example.test",
                },
                {
                    **image_cfg,
                    "credential_ref": "alibaba-image",
                    "options": {
                        "endpoint_mode": "public",
                        "region": "cn-beijing",
                    },
                },
                (
                    "https://dashscope.aliyuncs.com/api/v1/services/"
                    "aigc/multimodal-generation/generation"
                ),
            ),
        )
        for env_values, candidate_cfg, expected_endpoint in cases:
            for name in endpoint_envs:
                monkeypatch.delenv(name, raising=False)
            for name, value in env_values.items():
                monkeypatch.setenv(name, value)
            runtime_identity = resolver(
                "dashscope",
                candidate_cfg,
            )
            actual_endpoint = provider._endpoint(image_cfg=candidate_cfg)
            if runtime_identity.get("transport") != "dashscope_native_image_generation":
                violations.append("DashScope canonical transport is missing")
            if runtime_identity.get("endpoint") != expected_endpoint:
                violations.append(
                    f"resolver endpoint mismatch: {runtime_identity.get('endpoint')!r}"
                )
            if actual_endpoint != expected_endpoint:
                violations.append(
                    f"Provider endpoint mismatch: {actual_endpoint!r}"
                )
        for builtin_provider, expected_transport, expected_endpoint in (
            (
                "doubao",
                "volcengine_ark_images",
                "https://ark.cn-beijing.volces.com/api/v3/images/generations",
            ),
            (
                "qianfan",
                "qianfan_images",
                "https://qianfan.baidubce.com/v2/images/generations",
            ),
            (
                "zhipu-image",
                "zhipu_images",
                "https://open.bigmodel.cn/api/paas/v4/images/generations",
            ),
            (
                "minimax-image",
                "minimax_images",
                "https://api.minimax.io/v1/image_generation",
            ),
        ):
            runtime_identity = resolver(
                builtin_provider,
                {"provider": builtin_provider},
            )
            if runtime_identity.get("transport") != expected_transport:
                violations.append(
                    f"{builtin_provider} canonical transport is missing"
                )
            if runtime_identity.get("endpoint") != expected_endpoint:
                violations.append(
                    f"{builtin_provider} canonical endpoint is missing"
                )
        for blocked_external_provider in ("xai", "fal"):
            runtime_identity = resolver(
                blocked_external_provider,
                {"provider": blocked_external_provider},
            )
            if runtime_identity.get("identity_supported"):
                violations.append(
                    f"{blocked_external_provider} claimed a supported runtime identity"
                )
            if runtime_identity.get("transport") or runtime_identity.get("endpoint"):
                violations.append(
                    f"{blocked_external_provider} exposed a non-canonical identity"
                )

    assert violations == [], "; ".join(violations)


def test_config_cache_reexpands_current_env_for_vision_and_image_verification(
    monkeypatch,
    tmp_path,
):
    """A YAML cache hit must not preserve a changed or removed endpoint env."""
    from agent.image_gen_verification import (
        CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        image_gen_fingerprint,
        verification_status_from_state,
    )
    from agent.image_runtime import vision_fingerprint
    from hermes_cli.config import load_config

    def capability_fingerprint(capability, config):
        if capability == "vision":
            fingerprint, _resolved = vision_fingerprint(
                config["auxiliary"]["vision"],
                profile="default",
                config_data=config,
                secret_value="vision-test-key",
                key_configured=True,
            )
            return fingerprint
        return image_gen_fingerprint(
            config["image_gen"],
            profile="default",
            config_data=config,
            secret_value="image-test-key",
        )

    violations = []
    for capability in ("vision", "image"):
        for mutation in ("change", "unset"):
            case_root = tmp_path / f"{capability}-{mutation}"
            case_root.mkdir()
            config_path = case_root / "config.yaml"
            env_name = f"B3_{capability.upper()}_{mutation.upper()}_ENDPOINT"
            placeholder = f"${{{env_name}}}"
            endpoint_before = f"https://{capability}-before.example.test/v1"
            endpoint_after = f"https://{capability}-after.example.test/v1"
            config_path.write_text(
                json.dumps(
                    {
                        "auxiliary": {
                            "vision": {
                                "provider": "custom",
                                "model": "vision-model",
                                "base_url": placeholder,
                                "api_mode": "openai_chat_completions",
                            }
                        },
                        "image_gen": {
                            "provider": "dashscope",
                            "model": "qwen-image-2.0-pro",
                            "options": {
                                "endpoint_mode": "custom",
                                "base_url": placeholder,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
            monkeypatch.setenv("HERMES_HOME", str(case_root))
            monkeypatch.setenv(env_name, endpoint_before)

            config_before = load_config()
            fingerprint_before = capability_fingerprint(
                capability,
                config_before,
            )
            state = {
                "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
                "status": "verified",
                "fingerprint": fingerprint_before,
            }

            if mutation == "change":
                monkeypatch.setenv(env_name, endpoint_after)
                expected_endpoint = endpoint_after
            else:
                monkeypatch.delenv(env_name)
                expected_endpoint = placeholder
            config_after = load_config()
            fingerprint_after = capability_fingerprint(
                capability,
                config_after,
            )
            if capability == "vision":
                effective_endpoint = config_after["auxiliary"]["vision"]["base_url"]
            else:
                effective_endpoint = config_after["image_gen"]["options"]["base_url"]

            label = f"{capability}/{mutation}"
            if effective_endpoint != expected_endpoint:
                violations.append(
                    f"{label} load_config reused {effective_endpoint!r}"
                )
            if fingerprint_after == fingerprint_before:
                violations.append(f"{label} fingerprint did not change")
            if (
                verification_status_from_state(
                    state,
                    expected_fingerprint=fingerprint_after,
                )
                == "verified"
            ):
                violations.append(f"{label} inherited old verified state")

    assert violations == [], "; ".join(violations)


def test_verification_schema_requires_exact_current_integer_version():
    from agent.image_gen_verification import (
        CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        verification_status_from_state,
    )

    fingerprint = "b3-exact-schema-fingerprint"
    violations = []
    for label, version in (
        ("bool", True),
        ("float", 1.0),
        ("old", CAPABILITY_VERIFICATION_SCHEMA_VERSION - 1),
        ("unknown_new", CAPABILITY_VERIFICATION_SCHEMA_VERSION + 1),
    ):
        status = verification_status_from_state(
            {
                "schema_version": version,
                "status": "verified",
                "fingerprint": fingerprint,
            },
            expected_fingerprint=fingerprint,
        )
        if status == "verified":
            violations.append(f"{label} schema value inherited verified")

    assert violations == [], "; ".join(violations)


def test_image_secret_value_matches_runtime_default_alias_and_custom_resolution(
    monkeypatch,
    tmp_path,
):
    """Fingerprint secrets must come from the same resolver as Providers."""
    from agent import image_gen_verification as verification
    from plugins.image_gen.domestic_common import provider_api_key

    resolver = getattr(verification, "image_gen_secret_value", None)
    violations = []
    if not callable(resolver):
        violations.append("shared image secret-value resolver is missing")
        resolver = lambda *_args, **_kwargs: ""

    def fingerprint(image_cfg, config_data, secret):
        return verification.image_gen_fingerprint(
            image_cfg,
            profile="default",
            config_data=config_data,
            secret_value=secret,
        )

    default_root = tmp_path / "family-default"
    default_root.mkdir()
    default_path = default_root / "config.yaml"
    default_env = "TAIJI_CREDENTIAL_ZHIPU_DEFAULT_API_KEY"
    default_cfg = {
        "provider_credentials": [
            {
                "id": "zhipu-default",
                "provider_family": "zhipu",
                "secret_env": default_env,
                "default": True,
            }
        ],
        "image_gen": {
            "provider": "zhipu-image",
            "model": "cogview-4-250304",
        },
    }
    default_path.write_text(json.dumps(default_cfg), encoding="utf-8")
    (default_root / ".env").write_text(
        f"{default_env}=default-before\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(default_root))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(default_path))
    monkeypatch.setenv("GLM_API_KEY", "stale-legacy-key")
    actual_default = provider_api_key(
        "zhipu-image",
        config_data=default_cfg,
    )
    resolved_default = resolver(
        "zhipu-image",
        "",
        default_cfg,
        config_path=default_path,
    )
    default_before_fp = fingerprint(
        default_cfg["image_gen"],
        default_cfg,
        resolved_default,
    )
    (default_root / ".env").write_text(
        f"{default_env}=default-after\n",
        encoding="utf-8",
    )
    rotated_default = resolver(
        "zhipu-image",
        "",
        default_cfg,
        config_path=default_path,
    )
    default_after_fp = fingerprint(
        default_cfg["image_gen"],
        default_cfg,
        rotated_default,
    )
    if resolved_default != actual_default or resolved_default != "default-before":
        violations.append("family default credential did not match Provider resolution")
    if rotated_default != "default-after" or default_before_fp == default_after_fp:
        violations.append("family default credential rotation did not change fingerprint")

    alias_root = tmp_path / "zhipu-alias"
    alias_root.mkdir()
    alias_path = alias_root / "config.yaml"
    alias_cfg = {
        "image_gen": {
            "provider": "zhipu-image",
            "model": "cogview-4-250304",
        }
    }
    alias_path.write_text(json.dumps(alias_cfg), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(alias_root))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(alias_path))
    monkeypatch.delenv("GLM_API_KEY")
    monkeypatch.setenv("ZAI_API_KEY", "alias-before")
    monkeypatch.setenv("Z_AI_API_KEY", "lower-priority-alias")
    actual_alias = provider_api_key(
        "zhipu-image",
        config_data=alias_cfg,
    )
    resolved_alias = resolver(
        "zhipu-image",
        "",
        alias_cfg,
        config_path=alias_path,
    )
    alias_before_fp = fingerprint(
        alias_cfg["image_gen"],
        alias_cfg,
        resolved_alias,
    )
    monkeypatch.setenv("ZAI_API_KEY", "alias-after")
    rotated_alias = resolver(
        "zhipu-image",
        "",
        alias_cfg,
        config_path=alias_path,
    )
    alias_after_fp = fingerprint(
        alias_cfg["image_gen"],
        alias_cfg,
        rotated_alias,
    )
    if resolved_alias != actual_alias or resolved_alias != "alias-before":
        violations.append("ZAI_API_KEY alias did not match Provider resolution")
    if rotated_alias != "alias-after" or alias_before_fp == alias_after_fp:
        violations.append("ZAI_API_KEY rotation did not change fingerprint")

    custom_root = tmp_path / "custom-entry"
    custom_root.mkdir()
    custom_path = custom_root / "config.yaml"
    entry_env = "TAIJI_CREDENTIAL_ENTRY_IMAGE_API_KEY"
    stale_env = "TAIJI_CREDENTIAL_STALE_TOP_LEVEL_API_KEY"
    custom_cfg = {
        "provider_credentials": [
            {
                "id": "entry-image",
                "provider_family": "custom",
                "secret_env": entry_env,
            },
            {
                "id": "stale-top-level",
                "provider_family": "custom",
                "secret_env": stale_env,
            },
        ],
        "custom_image_providers": [
            {
                "id": "router",
                "base_url": "https://images.example.test/v1",
                "credential_ref": "entry-image",
                "models": ["image-model"],
                "default_model": "image-model",
            }
        ],
        "image_gen": {
            "provider": "custom:router",
            "model": "image-model",
            "credential_ref": "stale-top-level",
        },
    }
    custom_path.write_text(json.dumps(custom_cfg), encoding="utf-8")
    (custom_root / ".env").write_text(
        f"{entry_env}=entry-secret\n{stale_env}=stale-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(custom_root))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(custom_path))
    resolved_custom = resolver(
        "custom:router",
        "stale-top-level",
        custom_cfg,
        config_path=custom_path,
    )
    if resolved_custom != "entry-secret":
        violations.append("custom entry credential did not beat stale top-level ref")

    assert violations == [], "; ".join(violations)


def test_config_cache_miss_pairs_expansion_with_same_env_snapshot(
    monkeypatch,
    tmp_path,
):
    """A mid-load env change must not pair stale expansion with a new digest."""
    from hermes_cli import config as config_module

    config_path = tmp_path / "config.yaml"
    env_name = "B3_CACHE_MISS_ENDPOINT"
    endpoint_before = "https://before.example.test/v1"
    endpoint_after = "https://after.example.test/v1"
    config_path.write_text(
        json.dumps(
            {
                "image_gen": {
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                    "options": {
                        "endpoint_mode": "custom",
                        "base_url": f"${{{env_name}}}",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setenv(env_name, endpoint_before)

    original_expand = config_module._expand_env_vars
    state = {"depth": 0, "mutated": False}

    def racing_expand(value, *args, **kwargs):
        top_level = state["depth"] == 0
        state["depth"] += 1
        try:
            expanded = original_expand(value, *args, **kwargs)
        finally:
            state["depth"] -= 1
        if top_level and not state["mutated"]:
            state["mutated"] = True
            monkeypatch.setenv(env_name, endpoint_after)
        return expanded

    monkeypatch.setattr(config_module, "_expand_env_vars", racing_expand)

    first = config_module.load_config()
    second = config_module.load_config()

    assert first["image_gen"]["options"]["base_url"] == endpoint_before
    assert second["image_gen"]["options"]["base_url"] == endpoint_after


def test_dashscope_raw_and_expanded_endpoint_config_share_runtime_identity(
    monkeypatch,
):
    """Provider raw config and verifier-expanded config must resolve identically."""
    from agent.image_gen_verification import (
        expand_effective_config,
        image_gen_fingerprint,
        image_gen_runtime_identity,
    )
    from plugins.image_gen.dashscope import DashScopeQwenImageProvider

    for name in (
        "DASHSCOPE_ENDPOINT_MODE",
        "DASHSCOPE_REGION",
        "DASHSCOPE_WORKSPACE_ID",
        "DASHSCOPE_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("B3_RAW_ENDPOINT_MODE", "workspace")
    monkeypatch.setenv("B3_RAW_REGION", "cn-beijing")
    monkeypatch.setenv("B3_RAW_WORKSPACE", "raw-config-workspace")

    raw_cfg = {
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
        "credential_ref": "alibaba-image",
        "options": {
            "endpoint_mode": "${B3_RAW_ENDPOINT_MODE}",
            "region": "${B3_RAW_REGION}",
            "workspace_id": "${B3_RAW_WORKSPACE}",
        },
    }
    expanded_cfg, resolved = expand_effective_config(raw_cfg)
    assert resolved is True
    assert isinstance(expanded_cfg, dict)

    raw_identity = image_gen_runtime_identity("dashscope", raw_cfg)
    expanded_identity = image_gen_runtime_identity("dashscope", expanded_cfg)
    expected_endpoint = (
        "https://raw-config-workspace.cn-beijing.maas.aliyuncs.com/"
        "api/v1/services/aigc/multimodal-generation/generation"
    )
    provider = DashScopeQwenImageProvider()
    violations = []
    try:
        provider_endpoint = provider._endpoint(image_cfg=raw_cfg)
    except ValueError:
        provider_endpoint = ""
    if raw_identity != expanded_identity:
        violations.append("raw and expanded runtime identities diverged")
    if raw_identity.get("endpoint") != expected_endpoint:
        violations.append("raw config did not resolve the effective endpoint")
    if provider_endpoint != expected_endpoint:
        violations.append("Provider raw config did not use the effective endpoint")

    raw_fp = image_gen_fingerprint(
        raw_cfg,
        profile="default",
        config_data={"image_gen": raw_cfg},
        secret_value="image-secret",
    )
    expanded_fp = image_gen_fingerprint(
        expanded_cfg,
        profile="default",
        config_data={"image_gen": expanded_cfg},
        secret_value="image-secret",
    )
    if raw_fp != expanded_fp:
        violations.append("raw and expanded fingerprints diverged")

    assert violations == [], "; ".join(violations)


def test_unsupported_xai_identity_fails_closed_and_tracks_runtime_options(tmp_path):
    """Unsupported Provider state cannot inherit verification across call changes."""
    from agent.image_gen_verification import (
        CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        image_gen_fingerprint,
        read_image_gen_verification_snapshot,
        read_image_gen_verification_status,
        verification_state_path,
    )

    def config(*, model: str, resolution: str) -> dict:
        image_cfg = {
            "provider": "xai",
            "model": "legacy-top-level-model",
            "xai": {
                "model": model,
                "resolution": resolution,
            },
        }
        return {"image_gen": image_cfg}

    baseline = config(
        model="grok-imagine-image",
        resolution="1024x1024",
    )
    model_changed = config(
        model="grok-imagine-image-pro",
        resolution="1024x1024",
    )
    resolution_changed = config(
        model="grok-imagine-image",
        resolution="2048x2048",
    )
    baseline_fingerprint = image_gen_fingerprint(
        baseline["image_gen"],
        profile="default",
        config_data=baseline,
        secret_value="xai-secret",
    )
    state_path = verification_state_path(tmp_path, "default")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
                "fingerprint": baseline_fingerprint,
                "status": "verified",
                "checked_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )

    baseline_snapshot = read_image_gen_verification_snapshot(
        baseline["image_gen"],
        profile="default",
        config_data=baseline,
        secret_value="xai-secret",
        state_root=tmp_path,
    )
    model_fingerprint = image_gen_fingerprint(
        model_changed["image_gen"],
        profile="default",
        config_data=model_changed,
        secret_value="xai-secret",
    )
    resolution_fingerprint = image_gen_fingerprint(
        resolution_changed["image_gen"],
        profile="default",
        config_data=resolution_changed,
        secret_value="xai-secret",
    )
    violations = []
    if baseline_snapshot["effective_config_resolved"] is not False:
        violations.append("unsupported xAI identity was treated as resolved")
    if baseline_snapshot["status"] != "configured_unverified":
        violations.append("unsupported xAI snapshot inherited verified state")
    if (
        read_image_gen_verification_status(
            baseline["image_gen"],
            profile="default",
            config_data=baseline,
            secret_value="xai-secret",
            state_root=tmp_path,
        )
        != "configured_unverified"
    ):
        violations.append("unsupported xAI status reader inherited verified state")
    if model_fingerprint == baseline_fingerprint:
        violations.append("xAI runtime model change retained the same fingerprint")
    if resolution_fingerprint == baseline_fingerprint:
        violations.append("xAI runtime resolution change retained the same fingerprint")

    assert violations == [], "; ".join(violations)


def test_fixed_domestic_runtime_identity_matches_actual_provider_dispatch(
    monkeypatch,
):
    """Verifier identity and real request URL must derive from one neutral contract."""
    import importlib

    from agent.image_gen_verification import image_gen_runtime_identity

    try:
        contracts = importlib.import_module("agent.image_gen_runtime_contracts")
    except ImportError:
        contracts = None

    provider_cases = (
        (
            "doubao",
            "plugins.image_gen.doubao",
            "DoubaoImageGenProvider",
            "doubao-seedream-5-0-260128",
        ),
        (
            "qianfan",
            "plugins.image_gen.qianfan",
            "QianfanImageGenProvider",
            "qwen-image",
        ),
        (
            "zhipu-image",
            "plugins.image_gen.zhipu_image",
            "ZhipuImageGenProvider",
            "glm-image",
        ),
        (
            "minimax-image",
            "plugins.image_gen.minimax_image",
            "MinimaxImageGenProvider",
            "image-01",
        ),
    )
    violations = []
    if contracts is None:
        violations.append("provider-neutral runtime contract module is missing")

    for provider_name, module_name, class_name, model in provider_cases:
        module = importlib.import_module(module_name)
        captured = {}

        def fake_post_json(**kwargs):
            captured["url"] = kwargs.get("url")
            return {"data": [{"url": "https://cdn.example.test/generated.png"}]}, None

        monkeypatch.setattr(module, "provider_api_key", lambda *_args, **_kwargs: "key")
        monkeypatch.setattr(module, "post_json", fake_post_json)
        monkeypatch.setattr(
            module,
            "cached_success",
            lambda **kwargs: {
                "success": True,
                "provider": kwargs.get("provider"),
            },
        )
        result = getattr(module, class_name)().generate(
            "contract test",
            aspect_ratio="square",
            model=model,
        )
        identity = image_gen_runtime_identity(
            provider_name,
            {"provider": provider_name, "model": model},
        )
        if not result.get("success"):
            violations.append(f"{provider_name} dynamic dispatch did not complete")
        if identity.get("endpoint") != captured.get("url"):
            violations.append(
                f"{provider_name} verifier endpoint diverged from actual dispatch"
            )
        if contracts is not None:
            contract = contracts.builtin_image_runtime_contract(provider_name)
            if identity.get("endpoint") != contract.get("endpoint"):
                violations.append(f"{provider_name} verifier bypassed shared endpoint")
            if identity.get("transport") != contract.get("transport"):
                violations.append(f"{provider_name} verifier bypassed shared transport")
            if getattr(module, "RUNTIME_TRANSPORT", "") != contract.get("transport"):
                violations.append(f"{provider_name} Provider bypassed shared transport")

    assert violations == [], "; ".join(violations)


def test_custom_runtime_identity_uses_actual_endpoint_and_ignores_raw_transport():
    """Persisted transport text cannot alter an OpenAI Images call identity."""
    from agent.custom_image_providers import ConfigurableOpenAIImageProvider
    from agent.image_gen_verification import (
        image_gen_fingerprint,
        image_gen_runtime_identity,
    )

    def config(raw_transport: str) -> dict:
        return {
            "custom_image_providers": [
                {
                    "id": "router",
                    "name": "Router Images",
                    "base_url": "https://images.example.test/v1/",
                    "models": ["image-model"],
                    "default_model": "image-model",
                    "transport": raw_transport,
                }
            ],
            "image_gen": {
                "provider": "custom:router",
                "model": "image-model",
            },
        }

    first = config("raw-transport-one")
    second = config("raw-transport-two")
    provider = ConfigurableOpenAIImageProvider(first["custom_image_providers"][0])
    identity = image_gen_runtime_identity(
        "custom:router",
        first["image_gen"],
        config_data=first,
    )
    first_fingerprint = image_gen_fingerprint(
        first["image_gen"],
        profile="default",
        config_data=first,
        secret_value="custom-secret",
    )
    second_fingerprint = image_gen_fingerprint(
        second["image_gen"],
        profile="default",
        config_data=second,
        secret_value="custom-secret",
    )

    assert identity["transport"] == "openai_images"
    assert identity["endpoint"] == provider._endpoint()
    assert identity["endpoint"] == (
        "https://images.example.test/v1/images/generations"
    )
    assert first_fingerprint == second_fingerprint


def test_verification_snapshot_uses_one_env_generation_for_all_material(
    monkeypatch,
):
    """A mid-expansion env rotation cannot create a hybrid authorization identity."""
    from agent.image_gen_verification import (
        image_gen_fingerprint,
        read_image_gen_verification_snapshot,
    )
    from hermes_cli import config as config_module

    base_env = "B3_GAP5_CUSTOM_BASE_URL"
    model_env = "B3_GAP5_CUSTOM_MODEL"
    base_before = "https://before.example.test/v1"
    base_after = "https://after.example.test/v1"
    model_before = "image-before"
    model_after = "image-after"
    raw_config = {
        "custom_image_providers": [
            {
                "base_url": f"${{{base_env}}}",
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
    raw_image_cfg = raw_config["image_gen"]

    monkeypatch.setenv(base_env, base_before)
    monkeypatch.setenv(model_env, model_before)
    expected_before = image_gen_fingerprint(
        raw_image_cfg,
        profile="default",
        config_data=raw_config,
        secret_value="custom-secret",
    )
    monkeypatch.setenv(base_env, base_after)
    monkeypatch.setenv(model_env, model_after)
    expected_after = image_gen_fingerprint(
        raw_image_cfg,
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

    snapshot = read_image_gen_verification_snapshot(
        raw_image_cfg,
        profile="default",
        config_data=raw_config,
        secret_value="custom-secret",
    )

    assert switched["value"] is True
    assert snapshot["effective_config_resolved"] is True
    assert snapshot["fingerprint"] == expected_before
    assert snapshot["fingerprint"] != expected_after


def test_dashscope_implicit_endpoint_env_uses_material_snapshot(
    monkeypatch,
):
    """Implicit DashScope env options cannot rotate after material capture."""
    from agent.image_gen_verification import (
        image_gen_fingerprint,
        read_image_gen_verification_snapshot,
    )
    from hermes_cli import config as config_module

    image_cfg = {
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
    }
    config_data = {"image_gen": image_cfg}
    monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
    monkeypatch.setenv(
        "DASHSCOPE_BASE_URL",
        "https://before.example.test/api/v1/services/aigc/"
        "multimodal-generation/generation",
    )
    expected_before = image_gen_fingerprint(
        image_cfg,
        profile="default",
        config_data=config_data,
        secret_value="dashscope-secret",
    )
    monkeypatch.setenv(
        "DASHSCOPE_BASE_URL",
        "https://after.example.test/api/v1/services/aigc/"
        "multimodal-generation/generation",
    )
    expected_after = image_gen_fingerprint(
        image_cfg,
        profile="default",
        config_data=config_data,
        secret_value="dashscope-secret",
    )
    assert expected_before != expected_after
    monkeypatch.setenv(
        "DASHSCOPE_BASE_URL",
        "https://before.example.test/api/v1/services/aigc/"
        "multimodal-generation/generation",
    )

    original_snapshot = config_module._referenced_env_snapshot
    switched = {"value": False}

    def racing_snapshot(value):
        captured = original_snapshot(value)
        if not switched["value"]:
            switched["value"] = True
            monkeypatch.setenv(
                "DASHSCOPE_BASE_URL",
                "https://after.example.test/api/v1/services/aigc/"
                "multimodal-generation/generation",
            )
        return captured

    monkeypatch.setattr(
        config_module,
        "_referenced_env_snapshot",
        racing_snapshot,
    )

    snapshot = read_image_gen_verification_snapshot(
        image_cfg,
        profile="default",
        config_data=config_data,
        secret_value="dashscope-secret",
    )

    assert switched["value"] is True
    assert snapshot["fingerprint"] == expected_before
    assert snapshot["fingerprint"] != expected_after


def test_custom_legacy_secret_prefers_exact_config_env_over_process_env(
    monkeypatch,
    tmp_path,
):
    """A profile-local legacy key must not fall back to another profile's env."""
    from agent.custom_image_providers import custom_image_provider_env_var
    from agent.image_gen_verification import image_gen_secret_value

    secret_env = custom_image_provider_env_var("router")

    def profile(root, secret):
        root.mkdir()
        config_path = root / "profile-config.yaml"
        config_data = {
            "custom_image_providers": [
                {
                    "id": "router",
                    "name": "Router Images",
                    "base_url": "https://images.example.test/v1",
                    "api_key_env": secret_env,
                    "models": ["image-model"],
                    "default_model": "image-model",
                }
            ],
            "image_gen": {
                "provider": "custom:router",
                "model": "image-model",
            },
        }
        config_path.write_text(json.dumps(config_data), encoding="utf-8")
        (root / ".env").write_text(
            f"{secret_env}={secret}\n",
            encoding="utf-8",
        )
        return config_path, config_data

    path_a, _config_a = profile(tmp_path / "profile-a", "profile-a-secret")
    path_b, config_b = profile(tmp_path / "profile-b", "profile-b-secret")
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(path_a))
    monkeypatch.setenv(secret_env, "profile-a-secret")

    resolved = image_gen_secret_value(
        "custom:router",
        "",
        config_b,
        config_path=path_b,
    )

    assert resolved == "profile-b-secret"


def test_home_override_selects_matching_profile_config_and_state(
    monkeypatch,
    tmp_path,
):
    """A context-local profile home must select the same profile's state."""
    from agent.image_gen_verification import (
        CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        image_gen_fingerprint,
        verification_state_path,
    )
    from hermes_constants import (
        get_config_path,
        reset_hermes_home_override,
        set_hermes_home_override,
    )
    from tools import image_generation_tool

    root = tmp_path / ".hermes"
    profile_a = root / "profiles" / "A"
    profile_b = root / "profiles" / "B"
    profile_a.mkdir(parents=True)
    profile_b.mkdir(parents=True)
    (root / "active_profile").write_text("A\n", encoding="utf-8")
    image_cfg = {
        "provider": "qianfan",
        "model": "qwen-image",
    }
    config_b = {"image_gen": image_cfg}
    (profile_a / "config.yaml").write_text(
        json.dumps(
            {
                "image_gen": {
                    "provider": "qianfan",
                    "model": "profile-a-unused-model",
                }
            }
        ),
        encoding="utf-8",
    )
    (profile_b / "config.yaml").write_text(
        json.dumps(config_b),
        encoding="utf-8",
    )
    (profile_b / ".env").write_text(
        "QIANFAN_API_KEY=shared-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
    monkeypatch.setenv("QIANFAN_API_KEY", "shared-secret")
    state_root = tmp_path / "webui-state" / "image-gen-verification"
    monkeypatch.setenv("TAIJI_WEBUI_STATE_DIR", str(tmp_path / "webui-state"))

    fingerprint_a = image_gen_fingerprint(
        image_cfg,
        profile="A",
        config_data=config_b,
        secret_value="shared-secret",
    )
    fingerprint_b = image_gen_fingerprint(
        image_cfg,
        profile="B",
        config_data=config_b,
        secret_value="shared-secret",
    )
    for profile, fingerprint, status in (
        ("A", fingerprint_a, "verified"),
        ("B", fingerprint_b, "failed"),
    ):
        path = verification_state_path(state_root, profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
                    "fingerprint": fingerprint,
                    "status": status,
                    "checked_at": "2030-01-01T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )

    token = set_hermes_home_override(profile_b)
    try:
        assert get_config_path() == profile_b / "config.yaml"
        snapshot = image_generation_tool._read_image_gen_verification_snapshot(
            image_cfg
        )
    finally:
        reset_hermes_home_override(token)

    assert snapshot["fingerprint"] == fingerprint_b
    assert snapshot["status"] == "failed"


def test_dashscope_legacy_key_uses_exact_config_env_for_actual_dispatch(
    monkeypatch,
    tmp_path,
):
    """Actual built-in dispatch must read B's paired env, not process A."""
    import plugins.image_gen.dashscope as dashscope
    from hermes_constants import (
        reset_hermes_config_path_override,
        set_hermes_config_path_override,
    )

    profile_a = tmp_path / "profile-a"
    profile_b = tmp_path / "profile-b"
    profile_a.mkdir()
    profile_b.mkdir()
    endpoint_b = (
        "https://profile-b.example.test/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )
    config_b = {
        "image_gen": {
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "options": {
                "endpoint_mode": "custom",
                "base_url": endpoint_b,
            },
        }
    }
    path_a = profile_a / "profile-a.yaml"
    path_b = profile_b / "profile-b.yaml"
    path_a.write_text(
        json.dumps(
            {
                "image_gen": {
                    "provider": "dashscope",
                    "model": "qwen-image-2.0-pro",
                }
            }
        ),
        encoding="utf-8",
    )
    path_b.write_text(json.dumps(config_b), encoding="utf-8")
    (profile_a / ".env").write_text(
        "DASHSCOPE_API_KEY=profile-a-secret\n",
        encoding="utf-8",
    )
    (profile_b / ".env").write_text(
        "DASHSCOPE_API_KEY=profile-b-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(profile_a))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(path_a))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "profile-a-secret")
    monkeypatch.setattr(dashscope, "is_safe_url", lambda *_: True)
    captured = {}

    def fake_post_json(**kwargs):
        captured["url"] = kwargs["url"]
        captured["authorization"] = kwargs["headers"]["Authorization"]
        return {"data": [{"url": "https://cdn.example.test/probe.png"}]}, None

    monkeypatch.setattr(dashscope, "post_json", fake_post_json)
    monkeypatch.setattr(
        dashscope,
        "cached_success",
        lambda **kwargs: {
            "success": True,
            "provider": kwargs["provider"],
            "model": kwargs["model"],
        },
    )
    token = set_hermes_config_path_override(path_b)
    try:
        result = dashscope.DashScopeQwenImageProvider().generate(
            "legacy exact-path probe",
            aspect_ratio="square",
            model="qwen-image-2.0-pro",
        )
    finally:
        reset_hermes_config_path_override(token)

    assert result["success"] is True
    assert captured["url"] == endpoint_b
    assert captured["authorization"] == "Bearer profile-b-secret"


def test_config_and_home_context_overrides_are_thread_local(tmp_path):
    """Concurrent profile probes must not see each other's path or cache home."""
    from hermes_constants import (
        get_config_path,
        get_hermes_home,
        reset_hermes_config_path_override,
        reset_hermes_home_override,
        set_hermes_config_path_override,
        set_hermes_home_override,
    )

    barrier = threading.Barrier(2)
    observed = {}

    def worker(profile: str):
        home = tmp_path / profile
        config_path = home / f"{profile}.yaml"
        home.mkdir()
        home_token = set_hermes_home_override(home)
        config_token = set_hermes_config_path_override(config_path)
        try:
            barrier.wait(timeout=5)
            observed[profile] = (get_hermes_home(), get_config_path())
        finally:
            reset_hermes_config_path_override(config_token)
            reset_hermes_home_override(home_token)

    first = threading.Thread(target=worker, args=("A",))
    second = threading.Thread(target=worker, args=("B",))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert observed == {
        "A": (tmp_path / "A", tmp_path / "A" / "A.yaml"),
        "B": (tmp_path / "B", tmp_path / "B" / "B.yaml"),
    }
