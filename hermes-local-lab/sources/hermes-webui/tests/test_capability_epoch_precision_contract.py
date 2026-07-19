from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml


def _active_named_alibaba_config() -> dict[str, Any]:
    return {
        "provider_credentials": [
            {
                "id": "active-alibaba",
                "provider_family": "alibaba_dashscope",
                "label": "Active Alibaba",
                "auth_type": "api_key",
                "secret_env": (
                    "TAIJI_CREDENTIAL_ACTIVE_ALIBABA_API_KEY"
                ),
            },
            {
                "id": "inactive-credential",
                "provider_family": "zai",
                "label": "Inactive credential",
                "auth_type": "api_key",
                "secret_env": (
                    "TAIJI_CREDENTIAL_INACTIVE_CREDENTIAL_API_KEY"
                ),
            },
        ],
        "auxiliary": {
            "vision": {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "credential_ref": "active-alibaba",
            }
        },
        "image_gen": {
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "credential_ref": "active-alibaba",
        },
        "custom_vision_providers": [
            {
                "id": "inactive-vision",
                "name": "Inactive vision",
                "base_url": "https://vision.example.com/v1",
                "models": ["vision-model"],
                "secret_env": "TAIJI_CUSTOM_VISION_INACTIVE_VISION_API_KEY",
            }
        ],
        "custom_image_providers": [
            {
                "id": "inactive-image",
                "name": "Inactive image",
                "base_url": "https://images.example.com/v1",
                "models": ["image-model"],
                "secret_env": "TAIJI_CUSTOM_IMAGE_INACTIVE_IMAGE_API_KEY",
            }
        ],
        "_taiji_capability_epochs": {
            "vision": 7,
            "image_generation": 11,
        },
    }


def _read_epochs(config_path: Path) -> tuple[int, int]:
    from agent.image_gen_verification import (
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        CAPABILITY_CONFIG_EPOCH_VISION,
        capability_config_epoch,
    )

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return (
        capability_config_epoch(
            config,
            CAPABILITY_CONFIG_EPOCH_VISION,
        ),
        capability_config_epoch(
            config,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        ),
    )


def _route_provider_writes_to(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
) -> None:
    import api.config as config
    import api.providers as providers

    monkeypatch.setenv("HERMES_HOME", str(config_path.parent))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(providers, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(providers, "invalidate_models_cache", lambda: None)
    monkeypatch.setattr(providers, "reload_config", lambda: None)


@pytest.mark.parametrize("operation", ("set", "remove"))
def test_generic_provider_key_writer_does_not_bump_explicit_named_credential(
    operation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A family legacy key is irrelevant when active sections name a credential."""
    import api.providers as providers

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            _active_named_alibaba_config(),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                (
                    "TAIJI_CREDENTIAL_ACTIVE_ALIBABA_API_KEY="
                    "named-secret"
                ),
                "DASHSCOPE_API_KEY=legacy-before",
                "",
            )
        ),
        encoding="utf-8",
    )
    _route_provider_writes_to(monkeypatch, config_path)
    before = _read_epochs(config_path)

    if operation == "set":
        result = providers.set_provider_key(
            "alibaba",
            "legacy-after",
        )
    else:
        result = providers.remove_provider_key("alibaba")

    assert result["ok"] is True
    assert _read_epochs(config_path) == before


@pytest.mark.parametrize(
    "mutation",
    (
        "inactive_credential_label",
        "inactive_custom_vision_provider",
        "inactive_custom_image_provider",
    ),
)
def test_reconcile_ignores_inactive_authorization_metadata(
    mutation: str,
) -> None:
    """Whole-config reconciliation only advances the active authorization."""
    from agent.image_gen_verification import (
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        CAPABILITY_CONFIG_EPOCH_VISION,
        capability_config_epoch,
        reconcile_capability_config_epochs,
    )

    previous = _active_named_alibaba_config()
    desired = copy.deepcopy(previous)
    if mutation == "inactive_credential_label":
        desired["provider_credentials"][1]["label"] = "Renamed inactive"
    elif mutation == "inactive_custom_vision_provider":
        desired["custom_vision_providers"][0]["name"] = (
            "Renamed inactive vision"
        )
    else:
        desired["custom_image_providers"][0]["name"] = (
            "Renamed inactive image"
        )

    reconcile_capability_config_epochs(previous, desired)

    assert capability_config_epoch(
        desired,
        CAPABILITY_CONFIG_EPOCH_VISION,
    ) == 7
    assert capability_config_epoch(
        desired,
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    ) == 11


def test_reconcile_ignores_active_custom_image_display_name_only() -> None:
    from agent.image_gen_verification import (
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        capability_config_epoch,
        reconcile_capability_config_epochs,
    )

    previous = {
        "image_gen": {
            "provider": "custom:router",
            "model": "image-model",
        },
        "custom_image_providers": [
            {
                "id": "router",
                "name": "Router Images",
                "base_url": "https://images.example.com/v1",
                "models": ["image-model"],
                "default_model": "image-model",
            }
        ],
        "_taiji_capability_epochs": {
            "image_generation": 11,
        },
    }
    desired = copy.deepcopy(previous)
    desired["custom_image_providers"][0]["name"] = "Renamed Router"

    reconcile_capability_config_epochs(previous, desired)

    assert capability_config_epoch(
        desired,
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    ) == 11


def _inactive_custom_credential_config() -> dict[str, Any]:
    return {
        "provider_credentials": [
            {
                "id": "active-alibaba",
                "provider_family": "alibaba_dashscope",
                "label": "Active Alibaba",
                "auth_type": "api_key",
                "secret_env": (
                    "TAIJI_CREDENTIAL_ACTIVE_ALIBABA_API_KEY"
                ),
            },
            {
                "id": "inactive-custom",
                "provider_family": "custom",
                "label": "Inactive custom",
                "auth_type": "api_key",
                "secret_env": (
                    "TAIJI_CREDENTIAL_INACTIVE_CUSTOM_API_KEY"
                ),
            },
        ],
        "auxiliary": {
            "vision": {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
                "credential_ref": "active-alibaba",
            }
        },
        "custom_vision_providers": [
            {
                "id": "router",
                "name": "Inactive router",
                "base_url": "https://vision.example.com/v1",
                "models": ["vision-model"],
                "credential_ref": "inactive-custom",
            }
        ],
        "_taiji_capability_epochs": {
            "vision": 17,
            "image_generation": 23,
        },
    }


def test_provider_credential_usage_ignores_inactive_custom_provider_entry(
) -> None:
    from api import model_config

    config = _inactive_custom_credential_config()

    assert model_config._provider_credential_used_by(
        config,
        "inactive-custom",
    ) == []


def test_rotating_inactive_custom_secret_preserves_active_vision_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import model_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            _inactive_custom_credential_config(),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                (
                    "TAIJI_CREDENTIAL_ACTIVE_ALIBABA_API_KEY="
                    "active-secret"
                ),
                (
                    "TAIJI_CREDENTIAL_INACTIVE_CUSTOM_API_KEY="
                    "inactive-before"
                ),
                "",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(
        model_config,
        "_get_hermes_home",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        model_config,
        "_get_config_path",
        lambda: config_path,
    )
    monkeypatch.setattr(
        model_config,
        "_active_profile_name",
        lambda: "default",
    )
    captured_invalidations: list[str] = []
    post_commit_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        model_config,
        "_capture_vision_verification_invalidation",
        lambda *_args, **_kwargs: (
            captured_invalidations.append("vision")
            or object()
        ),
    )

    def record_post_commit(
        _mutation: str,
        **kwargs: Any,
    ) -> list[str]:
        post_commit_calls.append(kwargs)
        return []

    monkeypatch.setattr(
        model_config,
        "_invoke_durable_mutation_post_commit",
        record_post_commit,
    )
    before = _read_epochs(config_path)

    result = model_config.upsert_provider_credential(
        {
            "id": "inactive-custom",
            "provider": "custom",
            "label": "Inactive custom",
            "api_key": "inactive-after",
        }
    )

    assert result["credential"]["used_by"] == []
    assert captured_invalidations == []
    assert post_commit_calls == [
        {
            "invalidate_vision": False,
            "invalidate_image": False,
            "vision_invalidation_token": None,
            "image_invalidation_token": None,
        }
    ]
    assert _read_epochs(config_path) == before
