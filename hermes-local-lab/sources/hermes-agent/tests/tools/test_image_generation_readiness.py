"""Regression tests for image generation tool readiness."""

from __future__ import annotations

import pytest

from model_tools import get_tool_definitions


def _tool_names(tool_defs):
    return {item["function"]["name"] for item in tool_defs}


def _verification_snapshot(status: str = "configured_unverified"):
    return {
        "schema_version": 1,
        "fingerprint": "test-image-config-fingerprint",
        "status": status,
        "effective_config_resolved": True,
    }


@pytest.mark.parametrize(
    (
        "image_cfg",
        "expected_effective_config_resolved",
        "expected_verification_status",
        "expected_configured",
        "expected_reason_code",
    ),
    [
        ({}, False, "unconfigured", False, "not_configured"),
        (
            {"provider": "disabled", "model": ""},
            False,
            "unconfigured",
            False,
            "disabled",
        ),
        (
            {
                "provider": "dashscope",
                "model": "${B3_GAP9_UNRESOLVED_IMAGE_MODEL}",
            },
            False,
            "configured_unverified",
            True,
            "unresolved_effective_config",
        ),
        (
            {
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
            },
            True,
            "configured_unverified",
            True,
            "authorization_required",
        ),
    ],
)
def test_readiness_classifies_empty_unresolved_and_unauthorized_targets(
    monkeypatch,
    tmp_path,
    image_cfg,
    expected_effective_config_resolved,
    expected_verification_status,
    expected_configured,
    expected_reason_code,
):
    from agent.image_gen_verification import (
        read_image_gen_verification_snapshot,
    )
    from tools import image_generation_tool as image_tool

    monkeypatch.delenv("B3_GAP9_UNRESOLVED_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("FAL_API_KEY", raising=False)
    config_data = {"image_gen": image_cfg}
    snapshot = read_image_gen_verification_snapshot(
        image_cfg,
        profile="default",
        config_data=config_data,
        secret_value="",
        state_root=tmp_path,
    )

    class _Provider:
        name = "dashscope"

        def is_available(self):
            return False

    monkeypatch.setattr(
        image_tool,
        "_load_image_gen_config",
        lambda: image_cfg,
    )
    monkeypatch.setattr(
        image_tool,
        "_read_image_gen_verification_snapshot",
        lambda *_: snapshot,
    )
    monkeypatch.setattr(image_tool, "check_fal_api_key", lambda: False)
    monkeypatch.setattr(
        image_tool,
        "_iter_image_generation_providers",
        lambda: [_Provider()] if image_cfg.get("provider") else [],
    )

    readiness = image_tool.get_image_generation_readiness()

    assert (
        snapshot["effective_config_resolved"]
        is expected_effective_config_resolved
    )
    assert snapshot["status"] == expected_verification_status
    assert readiness["verification_status"] == expected_verification_status
    assert readiness["configured"] is expected_configured
    assert readiness["available"] is False
    assert readiness["reason_code"] == expected_reason_code


def test_readiness_reports_configured_but_unavailable_without_provider_auth(monkeypatch):
    from tools import image_generation_tool as image_tool

    monkeypatch.setattr(
        image_tool,
        "_load_image_gen_config",
        lambda: {"provider": "openai-codex", "model": "gpt-image-2-medium"},
        raising=False,
    )
    monkeypatch.setattr(image_tool, "check_fal_api_key", lambda: False)

    class _Provider:
        name = "openai-codex"

        def is_available(self):
            return False

    monkeypatch.setattr(
        image_tool,
        "_iter_image_generation_providers",
        lambda: [_Provider()],
        raising=False,
    )
    monkeypatch.setattr(
        image_tool,
        "_read_image_gen_verification_snapshot",
        lambda *_: _verification_snapshot(),
    )

    status = image_tool.get_image_generation_readiness()

    assert status["configured"] is True
    assert status["available"] is False
    assert status["reason_code"] == "authorization_required"
    assert "太极智能体" in status["public_message"]
    assert "Hermes" not in status["public_message"]
    assert "Codex" not in status["public_message"]


def test_provider_availability_allows_probe_but_not_public_ready_before_verification(monkeypatch):
    from tools import image_generation_tool as image_tool

    monkeypatch.setattr(
        image_tool,
        "_load_image_gen_config",
        lambda: {"provider": "dashscope", "model": "qwen-image-2.0-pro"},
    )
    monkeypatch.setattr(image_tool, "_read_image_gen_verification_status", lambda *_: "configured_unverified", raising=False)

    class _Provider:
        name = "dashscope"

        def is_available(self):
            return True

    monkeypatch.setattr(image_tool, "_iter_image_generation_providers", lambda: [_Provider()])

    status = image_tool.get_image_generation_readiness()

    assert status["configured"] is True
    assert status["available"] is False
    assert status["reason_code"] == "verification_required"
    assert status["verification_status"] == "configured_unverified"


def test_verified_provider_is_publicly_ready(monkeypatch):
    from tools import image_generation_tool as image_tool

    monkeypatch.setattr(image_tool, "_load_image_gen_config", lambda: {"provider": "dashscope", "model": "qwen-image"})
    monkeypatch.setattr(
        image_tool,
        "_read_image_gen_verification_snapshot",
        lambda *_: _verification_snapshot("verified"),
    )

    class _Provider:
        name = "dashscope"

        def is_available(self):
            return True

    monkeypatch.setattr(image_tool, "_iter_image_generation_providers", lambda: [_Provider()])

    status = image_tool.get_image_generation_readiness()

    assert status["available"] is True
    assert status["reason_code"] == "ready"
    assert status["verification_status"] == "verified"


def test_readiness_supports_configured_custom_image_provider(monkeypatch):
    from tools import image_generation_tool as image_tool
    from agent.custom_image_providers import (
        ConfigurableOpenAIImageProvider,
        load_custom_image_provider_entries,
    )

    persisted_entry = {
        "id": "router",
        "name": "Router Images",
        "base_url": "https://images.example.com/v1",
        "api_key_env": "TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY",
        "models": ["gpt-image-custom"],
        "default_model": "gpt-image-custom",
    }
    entries = load_custom_image_provider_entries(
        {"custom_image_providers": [persisted_entry]}
    )
    assert len(entries) == 1
    monkeypatch.setattr(
        image_tool,
        "_load_image_gen_config",
        lambda: {"provider": "custom:router", "model": "gpt-image-custom"},
        raising=False,
    )
    monkeypatch.setattr(
        image_tool,
        "_iter_image_generation_providers",
        lambda: [ConfigurableOpenAIImageProvider(entries[0])],
        raising=False,
    )
    monkeypatch.setattr(
        image_tool,
        "_read_image_gen_verification_snapshot",
        lambda *_: _verification_snapshot("verified"),
    )
    monkeypatch.delenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", raising=False)

    unavailable = image_tool.get_image_generation_readiness()
    assert unavailable["configured"] is True
    assert unavailable["available"] is False
    assert unavailable["reason_code"] == "authorization_required"

    monkeypatch.setenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", "secret")
    available = image_tool.get_image_generation_readiness()
    assert available["configured"] is True
    assert available["available"] is True
    assert available["reason_code"] == "ready"
    assert available["provider"] == "custom:router"


def test_image_generate_schema_appears_only_when_provider_available(monkeypatch):
    from tools import image_generation_tool as image_tool
    from tools.registry import invalidate_check_fn_cache
    import model_tools

    monkeypatch.setattr(
        image_tool,
        "get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": False,
            "reason_code": "authorization_required",
            "public_message": "图像生成未授权，请先在太极智能体中完成图像生成授权。",
        },
    )
    invalidate_check_fn_cache()
    model_tools._clear_tool_defs_cache()
    unavailable = get_tool_definitions(enabled_toolsets=["image_gen"], quiet_mode=True)
    assert "image_generate" not in _tool_names(unavailable)

    monkeypatch.setattr(
        image_tool,
        "get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": True,
            "reason_code": "ready",
            "public_message": "图像生成已就绪。",
        },
    )
    invalidate_check_fn_cache()
    model_tools._clear_tool_defs_cache()
    available = get_tool_definitions(enabled_toolsets=["image_gen"], quiet_mode=True)
    assert "image_generate" in _tool_names(available)
