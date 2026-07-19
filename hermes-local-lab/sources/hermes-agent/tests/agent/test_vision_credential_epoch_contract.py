from __future__ import annotations

from agent.image_gen_verification import (
    CAPABILITY_CONFIG_EPOCH_VISION,
    capability_epochs_for_secret_env,
)
from agent.image_runtime import _vision_secret_env
from agent.provider_credentials import credential_secret_env


def _credential(
    credential_id: str,
    provider_family: str,
    *,
    default: bool = False,
) -> dict[str, object]:
    return {
        "id": credential_id,
        "provider_family": provider_family,
        "auth_type": "api_key",
        "secret_env": credential_secret_env(credential_id),
        "default": default,
    }


def test_custom_vision_entry_credential_secret_advances_vision_epoch() -> None:
    credential_id = "vision-router-key"
    secret_env = credential_secret_env(credential_id)
    config = {
        "provider_credentials": [
            _credential(credential_id, "custom"),
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
                "base_url": "https://vision.example.test/v1",
                "credential_ref": credential_id,
                "models": ["router-vl"],
                "default_model": "router-vl",
                "transport": "openai_chat_completions",
                "network_scope": "public_direct",
                "trusted_proxy_profile": "",
            }
        ],
    }

    assert capability_epochs_for_secret_env(config, secret_env) == (
        CAPABILITY_CONFIG_EPOCH_VISION,
    )


def test_builtin_vision_empty_ref_uses_family_default_secret_env() -> None:
    credential_id = "dashscope-default"
    secret_env = credential_secret_env(credential_id)
    config = {
        "provider_credentials": [
            _credential(credential_id, "alibaba", default=True),
        ],
    }
    vision_config = {
        "provider": "alibaba",
        "model": "qwen3-vl-plus",
    }

    assert _vision_secret_env(
        "alibaba",
        vision_config,
        config,
    ) == secret_env
