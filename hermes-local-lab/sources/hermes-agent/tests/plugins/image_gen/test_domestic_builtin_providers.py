#!/usr/bin/env python3
"""Tests for built-in domestic image generation providers."""

from __future__ import annotations

import base64
import importlib
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
import yaml


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch, tmp_path):
    for key in (
        "ARK_API_KEY",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_WORKSPACE_ID",
        "DASHSCOPE_REGION",
        "DASHSCOPE_ENDPOINT_MODE",
        "DASHSCOPE_BASE_URL",
        "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY",
        "QIANFAN_API_KEY",
        "GLM_API_KEY",
        "MINIMAX_API_KEY",
        "TAIJI_RUNTIME_HOME",
        "HERMES_HOME",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(tmp_path / "missing-config.yaml"))


def _response(payload: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


def _http_error_response(secret: str):
    resp = MagicMock()
    resp.status_code = 401
    resp.raise_for_status.side_effect = requests.HTTPError(f"401 leaked {secret}")
    resp.text = f"auth failed {secret}"
    resp.json.return_value = {"error": {"message": f"bad key {secret}"}}
    return resp


def _download_response(*, status=200, headers=None, chunks=()):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    resp.iter_content.return_value = iter(chunks)
    return resp


def test_domestic_post_json_uses_pinned_transport_and_bounded_json(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:65535")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:65535")
    from plugins.image_gen import domestic_common

    response = MagicMock(status_code=200)
    response_context = MagicMock()
    response_context.__enter__.return_value = response
    payload = {"data": [{"url": "https://provider.example/result.png"}]}

    with (
        patch.object(
            domestic_common,
            "request_pinned_https",
            return_value=response_context,
            create=True,
        ) as pinned_post,
        patch.object(
            domestic_common,
            "read_bounded_json",
            return_value=payload,
            create=True,
        ) as bounded_reader,
        patch("requests.post") as legacy_post,
    ):
        body, error = domestic_common.post_json(
            url="https://provider.example/v1/images",
            headers={
                "Authorization": "Bearer provider-secret",
                "Content-Type": "application/json",
            },
            payload={"prompt": "grid control room"},
            timeout=180,
            provider="provider",
            model="image-model",
            prompt="grid control room",
            aspect_ratio="landscape",
            secrets=("provider-secret",),
        )

    assert error is None
    assert body == payload
    legacy_post.assert_not_called()
    pinned_post.assert_called_once()
    kwargs = pinned_post.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["url"] == "https://provider.example/v1/images"
    assert kwargs["network_scope"] == "public_direct"
    assert kwargs["follow_redirects"] is False
    assert kwargs["timeout"] == 180
    assert kwargs["json_body"] == {"prompt": "grid control room"}
    bounded_reader.assert_called_once_with(
        response,
        max_bytes=domestic_common.MAX_API_RESPONSE_BYTES,
    )


def test_domestic_post_json_redacts_http_error_body(monkeypatch):
    from plugins.image_gen import domestic_common

    secret = "provider-super-secret"
    response = MagicMock(status_code=401)
    response_context = MagicMock()
    response_context.__enter__.return_value = response
    with (
        patch.object(
            domestic_common,
            "request_pinned_https",
            return_value=response_context,
        ),
        patch.object(
            domestic_common,
            "read_bounded_json",
            return_value={"error": {"message": f"bad key {secret}"}},
        ),
    ):
        body, error = domestic_common.post_json(
            url="https://provider.example/v1/images",
            headers={"Authorization": f"Bearer {secret}"},
            payload={"prompt": "grid control room"},
            timeout=180,
            provider="provider",
            model="image-model",
            prompt="grid control room",
            aspect_ratio="landscape",
            secrets=(secret,),
        )

    assert body is None
    assert error is not None
    assert error["error_type"] == "api_error"
    assert secret not in error["error"]


def test_domestic_post_json_redacts_capability_tokens_from_error_body(
    monkeypatch,
):
    from plugins.image_gen import domestic_common

    signed_url = (
        "https://cdn.example/result.png"
        "?X-Amz-Credential=AKIAEXAMPLE"
        "&X-Amz-Signature=abcdef0123456789"
        "#fragment-value"
    )
    bearer = "opaque-bearer-value"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturepart"
    credential = "AKIASTANDALONE"
    token = "opaque-assignment-value"
    signature = "standalone-signature-value"
    detail = (
        f"failed {signed_url}\n"
        f"Authorization: Bearer {bearer}; jwt={jwt}; "
        f"credential={credential}; token: {token}; signature={signature}"
    )
    response = MagicMock(status_code=401)
    response_context = MagicMock()
    response_context.__enter__.return_value = response
    with (
        patch.object(
            domestic_common,
            "request_pinned_https",
            return_value=response_context,
        ),
        patch.object(
            domestic_common,
            "read_bounded_json",
            return_value={"error": {"message": detail}},
        ),
    ):
        body, error = domestic_common.post_json(
            url="https://provider.example/v1/images",
            headers={"Authorization": "Bearer configured-provider-key"},
            payload={"prompt": "grid control room"},
            timeout=180,
            provider="provider",
            model="image-model",
            prompt="grid control room",
            aspect_ratio="landscape",
            secrets=("configured-provider-key",),
        )

    assert body is None
    assert error is not None
    rendered = error["error"]
    for sensitive in (
        "X-Amz-Credential",
        "X-Amz-Signature",
        "fragment-value",
        bearer,
        jwt,
        credential,
        token,
        signature,
    ):
        assert sensitive not in rendered
    assert "\n" not in rendered
    assert "[redacted" in rendered


def test_domestic_error_message_redacts_named_credentials_without_false_positive():
    from plugins.image_gen.domestic_common import error_message_from_body

    sensitive_values = (
        "opaque-signature-value-123456789",
        "opaque-refresh-value-123456789",
        "opaque-access-value-123456789",
        "opaque-id-value-123456789",
        "AKIAEXAMPLE123456",
    )
    detail = (
        "request failed at "
        "https://api.example.com/v1/images"
        "?sig=query-signature-value#private-fragment; "
        f"sig={sensitive_values[0]}; "
        f"refresh_token={sensitive_values[1]}; "
        f"access-token={sensitive_values[2]}; "
        f"id.token={sensitive_values[3]}; "
        f"AccessKeyId={sensitive_values[4]}; "
        "secretary=public-role"
    )

    rendered = error_message_from_body(
        {"error": {"message": detail}},
        secrets=(),
    )

    for sensitive in (*sensitive_values, "query-signature-value", "private-fragment"):
        assert sensitive not in rendered
    assert "https://api.example.com/[redacted-path]" in rendered
    assert "secretary=public-role" in rendered


def test_domestic_error_message_redacts_path_and_camel_case_credentials():
    from plugins.image_gen.domestic_common import error_message_from_body

    path_capability = "opaque-path-capability-9f2c4b8e7d6a"
    credentials = (
        "opaque-aws-secret-key-123456789",
        "opaque-session-token-123456789",
        "opaque-security-token-123456789",
        "opaque-private-key-123456789",
    )
    detail = (
        "download failed at "
        f"https://cdn.example/download/{path_capability}/image.png"
        "?sig=query#fragment; "
        f"SecretAccessKey={credentials[0]}; "
        f"SessionToken={credentials[1]}; "
        f"security_token={credentials[2]}; "
        f"private-key={credentials[3]}; "
        "secretary=public-role"
    )

    rendered = error_message_from_body(
        {"error": {"message": detail}},
        secrets=(),
    )

    for sensitive in (path_capability, *credentials, "query", "fragment"):
        assert sensitive not in rendered
    assert "https://cdn.example/[redacted-path]" in rendered
    assert "secretary=public-role" in rendered


@pytest.mark.parametrize(
    ("wrapped_url", "expected_suffix"),
    (
        (
            "(https://user:pa'ssword-canary@example.com/path"
            "?sig=query-canary),",
            "), retry",
        ),
        (
            "[https://u'ser-canary:password-canary@example.com/path"
            "?sig=query-canary];",
            "]; retry",
        ),
    ),
)
def test_domestic_error_message_redacts_quoted_userinfo_and_keeps_wrapper_punctuation(
    wrapped_url,
    expected_suffix,
):
    from plugins.image_gen.domestic_common import error_message_from_body

    rendered = error_message_from_body(
        {"error": {"message": f"failed {wrapped_url} retry"}},
        secrets=(),
    )

    for sensitive in (
        "user",
        "pa'ssword-canary",
        "u'ser-canary",
        "password-canary",
        "query-canary",
    ):
        assert sensitive not in rendered
    assert "https://example.com/[redacted-path]" in rendered
    assert expected_suffix in rendered


def test_domestic_post_json_rejects_redirect_response(monkeypatch):
    from plugins.image_gen import domestic_common

    response = MagicMock(status_code=302)
    response_context = MagicMock()
    response_context.__enter__.return_value = response
    with (
        patch.object(
            domestic_common,
            "request_pinned_https",
            return_value=response_context,
        ),
        patch.object(
            domestic_common,
            "read_bounded_json",
            return_value={"data": [{"url": "https://redirect.example/image.png"}]},
        ),
    ):
        body, error = domestic_common.post_json(
            url="https://provider.example/v1/images",
            headers={"Authorization": "Bearer provider-secret"},
            payload={"prompt": "grid control room"},
            timeout=180,
            provider="provider",
            model="image-model",
            prompt="grid control room",
            aspect_ratio="landscape",
            secrets=("provider-secret",),
        )

    assert body is None
    assert error is not None
    assert error["error_type"] == "api_error"
    assert "HTTP 302" in error["error"]


def test_domestic_post_json_preserves_non_2xx_status_when_body_is_invalid(
    monkeypatch,
):
    from agent.safe_outbound_http import SafeOutboundError
    from plugins.image_gen import domestic_common

    response = MagicMock(status_code=429)
    response_context = MagicMock()
    response_context.__enter__.return_value = response
    with (
        patch.object(
            domestic_common,
            "request_pinned_https",
            return_value=response_context,
        ),
        patch.object(
            domestic_common,
            "read_bounded_json",
            side_effect=SafeOutboundError("provider_response_invalid_mime"),
        ),
    ):
        body, error = domestic_common.post_json(
            url="https://provider.example/v1/images",
            headers={"Authorization": "Bearer provider-key"},
            payload={"prompt": "grid control room"},
            timeout=180,
            provider="provider",
            model="image-model",
            prompt="grid control room",
            aspect_ratio="landscape",
            secrets=("provider-key",),
        )

    assert body is None
    assert error is not None
    assert error["error_type"] == "api_error"
    assert error["error"] == "provider image generation failed: HTTP 429"


def test_domestic_result_url_requires_https_and_has_bounded_depth():
    from plugins.image_gen.domestic_common import first_url

    assert first_url({"data": [{"url": "http://provider.example/image.png"}]}) == ""
    assert (
        first_url({"data": [{"url": "https://provider.example/image.png"}]})
        == "https://provider.example/image.png"
    )
    deeply_nested: dict = {"url": "https://provider.example/too-deep.png"}
    for _ in range(32):
        deeply_nested = {"data": deeply_nested}
    assert first_url(deeply_nested) == ""


def test_cached_success_rejects_http_and_enforces_https_on_redirects():
    from plugins.image_gen import domestic_common

    saver = MagicMock(return_value=Path("/tmp/safe.png"))
    rejected = domestic_common.cached_success(
        image_url="http://provider.example/image.png",
        cache_prefix="provider",
        model="image-model",
        prompt="grid control room",
        aspect_ratio="landscape",
        provider="provider",
        save_image=saver,
    )

    assert rejected["success"] is False
    assert rejected["error_type"] == "invalid_response"
    saver.assert_not_called()

    accepted = domestic_common.cached_success(
        image_url="https://provider.example/image.png",
        cache_prefix="provider",
        model="image-model",
        prompt="grid control room",
        aspect_ratio="landscape",
        provider="provider",
        save_image=saver,
    )
    assert accepted["success"] is True
    validator = saver.call_args.kwargs["url_validator"]
    assert validator("https://cdn.example/image.png") is True
    assert validator("http://cdn.example/image.png") is False


def _redacted_transport_error(provider: str):
    return (
        None,
        {
            "success": False,
            "error": f"{provider} image generation failed: HTTP 401: [redacted]",
            "error_type": "api_error",
        },
    )


_NAMED_CREDENTIAL_CASES = (
    (
        "plugins.image_gen.doubao",
        "DoubaoImageGenProvider",
        "doubao",
        "doubao",
        "ARK_API_KEY",
        {"data": [{"url": "https://doubao/result.png"}]},
    ),
    (
        "plugins.image_gen.qianfan",
        "QianfanImageGenProvider",
        "qianfan",
        "qianfan",
        "QIANFAN_API_KEY",
        {"data": [{"url": "https://qianfan/result.png"}]},
    ),
    (
        "plugins.image_gen.zhipu_image",
        "ZhipuImageGenProvider",
        "zhipu-image",
        "zhipu",
        "GLM_API_KEY",
        {"data": [{"url": "https://zhipu/result.png"}]},
    ),
    (
        "plugins.image_gen.minimax_image",
        "MinimaxImageGenProvider",
        "minimax-image",
        "minimax",
        "MINIMAX_API_KEY",
        {"data": {"image_urls": ["https://minimax/result.png"]}},
    ),
)


@pytest.mark.parametrize(
    (
        "module_name",
        "class_name",
        "_active_provider",
        "_provider_family",
        "legacy_env",
        "response_payload",
    ),
    _NAMED_CREDENTIAL_CASES,
)
def test_domestic_provider_public_entrypoint_never_passes_raw_requests_post(
    monkeypatch,
    module_name,
    class_name,
    _active_provider,
    _provider_family,
    legacy_env,
    response_payload,
):
    monkeypatch.setenv(legacy_env, "provider-secret")
    module = importlib.import_module(module_name)
    provider = getattr(module, class_name)()
    safe_post = MagicMock(return_value=(response_payload, None))
    monkeypatch.setattr(module, "post_json", safe_post, raising=False)
    monkeypatch.setattr(
        module,
        "save_url_image",
        MagicMock(return_value=Path("/tmp/safe-result.png")),
        raising=False,
    )

    with patch("requests.post", side_effect=AssertionError("legacy transport used")) as legacy_post:
        result = provider.generate(
            "A reliable grid control room",
            model=provider.default_model(),
        )

    assert result["success"] is True
    legacy_post.assert_not_called()
    safe_post.assert_called_once()
    assert "request_post" not in safe_post.call_args.kwargs


def test_dashscope_public_entrypoint_never_passes_raw_requests_post(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
    from plugins.image_gen import dashscope

    safe_post = MagicMock(
        return_value=(
            {"output": {"image": "https://dashscope/result.png"}},
            None,
        )
    )
    monkeypatch.setattr(dashscope, "post_json", safe_post)
    monkeypatch.setattr(
        dashscope,
        "_save_safe_image_url",
        MagicMock(return_value=Path("/tmp/dashscope-result.png")),
    )

    with patch("requests.post", side_effect=AssertionError("legacy transport used")) as legacy_post:
        result = dashscope.DashScopeQwenImageProvider().generate(
            "A reliable grid control room"
        )

    assert result["success"] is True
    legacy_post.assert_not_called()
    safe_post.assert_called_once()
    assert "request_post" not in safe_post.call_args.kwargs


@pytest.mark.parametrize(
    (
        "module_name",
        "class_name",
        "active_provider",
        "provider_family",
        "legacy_env",
        "response_payload",
    ),
    _NAMED_CREDENTIAL_CASES,
)
def test_domestic_provider_uses_named_credential_for_availability_and_generation(
    monkeypatch,
    tmp_path,
    module_name,
    class_name,
    active_provider,
    provider_family,
    legacy_env,
    response_payload,
):
    from agent.provider_credentials import credential_secret_env

    credential_id = f"{provider_family}-image"
    named_env = credential_secret_env(credential_id)
    config_path = tmp_path / f"{active_provider}-named.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": credential_id,
                        "provider_family": provider_family,
                        "auth_type": "api_key",
                        "secret_env": named_env,
                    }
                ],
                "image_gen": {
                    "provider": active_provider,
                    "credential_ref": credential_id,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setenv(named_env, "named-provider-secret")
    monkeypatch.setenv(legacy_env, "legacy-must-not-be-used")
    module = importlib.import_module(module_name)
    provider = getattr(module, class_name)()
    mock_post = MagicMock(return_value=(response_payload, None))
    monkeypatch.setattr(module, "post_json", mock_post)
    monkeypatch.setattr(
        module,
        "save_url_image",
        MagicMock(return_value=Path("/tmp/named-result.png")),
        raising=False,
    )

    assert provider.is_available() is True
    result = provider.generate(
        "A reliable grid control room",
        model=provider.default_model(),
    )

    assert result["success"] is True
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
        "Bearer named-provider-secret"
    )


@pytest.mark.parametrize(
    (
        "module_name",
        "class_name",
        "active_provider",
        "provider_family",
        "legacy_env",
        "_response_payload",
    ),
    _NAMED_CREDENTIAL_CASES,
)
def test_domestic_provider_missing_named_secret_never_falls_back_to_legacy(
    monkeypatch,
    tmp_path,
    module_name,
    class_name,
    active_provider,
    provider_family,
    legacy_env,
    _response_payload,
):
    from agent.provider_credentials import credential_secret_env

    credential_id = f"{provider_family}-image"
    config_path = tmp_path / f"{active_provider}-missing-secret.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "provider_credentials": [
                    {
                        "id": credential_id,
                        "provider_family": provider_family,
                        "auth_type": "api_key",
                        "secret_env": credential_secret_env(credential_id),
                    }
                ],
                "image_gen": {
                    "provider": active_provider,
                    "credential_ref": credential_id,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setenv(legacy_env, "legacy-must-not-be-used")
    module = importlib.import_module(module_name)
    provider = getattr(module, class_name)()
    mock_post = MagicMock()
    monkeypatch.setattr(module, "post_json", mock_post, raising=False)

    assert provider.is_available() is False
    result = provider.generate(
        "A reliable grid control room",
        model=provider.default_model(),
    )

    assert result["success"] is False
    assert result["error_type"] == "auth_required"
    mock_post.assert_not_called()


@pytest.mark.parametrize(
    (
        "module_name",
        "class_name",
        "active_provider",
        "_provider_family",
        "legacy_env",
        "_response_payload",
    ),
    _NAMED_CREDENTIAL_CASES,
)
def test_domestic_provider_malformed_config_never_falls_back_to_legacy(
    monkeypatch,
    tmp_path,
    module_name,
    class_name,
    active_provider,
    _provider_family,
    legacy_env,
    _response_payload,
):
    config_path = tmp_path / f"{active_provider}-malformed.yaml"
    config_path.write_text("image_gen: [\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setenv(legacy_env, "legacy-must-not-be-used")
    module = importlib.import_module(module_name)
    provider = getattr(module, class_name)()
    mock_post = MagicMock()
    monkeypatch.setattr(module, "post_json", mock_post, raising=False)

    assert provider.is_available() is False
    result = provider.generate(
        "A reliable grid control room",
        model=provider.default_model(),
    )

    assert result["success"] is False
    assert result["error_type"] == "configuration_error"
    mock_post.assert_not_called()


class TestDashScopeQwenImageProvider:
    def test_safe_image_download_delegates_to_pinned_agent_transport(
        self, monkeypatch
    ):
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
        from plugins.image_gen import dashscope

        with (
            patch.object(
                dashscope, "save_url_image", return_value=Path("/tmp/safe.png")
            ) as safe_saver,
            patch("requests.get") as legacy_get,
        ):
            saved = dashscope._save_safe_image_url(
                "https://cdn.example/image.png"
            )

        assert saved == Path("/tmp/safe.png")
        legacy_get.assert_not_called()
        kwargs = safe_saver.call_args.kwargs
        assert kwargs["max_bytes"] == dashscope.MAX_IMAGE_BYTES
        assert kwargs["max_pixels"] == dashscope.MAX_IMAGE_PIXELS
        assert kwargs["max_redirects"] == dashscope.MAX_IMAGE_REDIRECTS
        assert kwargs["url_validator"] is dashscope._dashscope_url_shape_allowed
        assert kwargs["network_scope"] == "public_direct"
        assert "address_validator" not in kwargs

    def test_cached_success_uses_real_dashscope_saver_contract(
        self, monkeypatch
    ):
        from plugins.image_gen import dashscope, domestic_common

        safe_saver = MagicMock(return_value=Path("/tmp/safe.png"))
        monkeypatch.setattr(dashscope, "save_url_image", safe_saver)

        result = domestic_common.cached_success(
            image_url="https://cdn.example/image.png",
            cache_prefix="dashscope_qwen_image",
            model="qwen-image",
            prompt="A reliable grid control room",
            aspect_ratio="landscape",
            provider="dashscope",
            save_image=dashscope._save_safe_image_url,
        )

        assert result["success"] is True
        safe_saver.assert_called_once()
        kwargs = safe_saver.call_args.kwargs
        assert kwargs["network_scope"] == "public_direct"
        validator = kwargs["url_validator"]
        assert validator("https://cdn.example/image.png") is True
        assert validator("https://cdn.example:8443/image.png") is False
        assert validator("http://cdn.example/image.png") is False

    def test_dashscope_transport_policy_keeps_https_shape_validation(self):
        from plugins.image_gen import dashscope

        assert dashscope._dashscope_url_shape_allowed(
            "https://cdn.example/image.png"
        )
        assert not dashscope._dashscope_url_shape_allowed(
            "http://cdn.example/image.png"
        )
        assert not dashscope._dashscope_url_shape_allowed(
            "https://user@cdn.example/image.png"
        )

    def test_surface_and_setup_schema(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        schema = provider.get_setup_schema()

        assert provider.name == "dashscope"
        assert provider.default_model() == "qwen-image-2.0-pro"
        assert schema["domestic"] is True
        assert schema["integration_status"] == "stable"
        assert schema["supported_regions"] == ["cn-beijing", "ap-southeast-1"]
        assert [field["env_var"] for field in schema["credential_fields"]] == [
            "DASHSCOPE_API_KEY",
            "DASHSCOPE_ENDPOINT_MODE",
            "DASHSCOPE_WORKSPACE_ID",
            "DASHSCOPE_REGION",
            "DASHSCOPE_BASE_URL",
        ]
        fields = {field["name"]: field for field in schema["credential_fields"]}
        assert fields["endpoint_mode"]["placeholder"] == "public"
        assert fields["endpoint_mode"]["options"][0]["value"] == "public"
        assert fields["workspace_id"]["placeholder"] == "llm-demo"


    def test_normalized_schema_keeps_non_secret_access_key_id_in_credentials(self):
        from plugins.image_gen.domestic_common import normalized_setup_contract

        contract = normalized_setup_contract(
            {"auth_type": "access_key_secret"},
            provider_family="example",
            capabilities=("image_generation",),
            transport="vendor_signed_request",
        )

        assert [field["name"] for field in contract["credential_fields"]] == [
            "access_key_id",
            "access_key_secret",
        ]
        assert contract["endpoint_fields"] == []
        assert contract["auth_editable"] is False

    def test_missing_credentials_return_auth_required(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        result = DashScopeQwenImageProvider().generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "auth_required"
        assert "DASHSCOPE_API_KEY" in result["error"]
        assert "DASHSCOPE_WORKSPACE_ID" not in result["error"]

    def test_api_key_only_posts_to_beijing_public_endpoint(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        payload = {"output": {"image": "https://dashscope/result.png"}}
        with (
            patch(
                "plugins.image_gen.dashscope.post_json",
                return_value=(payload, None),
            ) as mock_post,
            patch(
                "plugins.image_gen.dashscope._save_safe_image_url",
                return_value=Path("/tmp/result.png"),
            ),
        ):
            provider = DashScopeQwenImageProvider()
            assert provider.is_available() is True
            result = provider.generate("A city skyline")

        assert result["success"] is True
        assert mock_post.call_args.kwargs["url"] == (
            "https://dashscope.aliyuncs.com/api/v1/services/"
            "aigc/multimodal-generation/generation"
        )

    def test_empty_prompt_returns_invalid_argument(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "llm-demo")
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        result = DashScopeQwenImageProvider().generate("   ")

        assert result["success"] is False
        assert result["error_type"] == "invalid_argument"

    def test_successful_generation_posts_to_workspace_endpoint(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "workspace")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "llm-demo")
        monkeypatch.setenv("DASHSCOPE_REGION", "cn-beijing")
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        payload = {
            "output": {
                "choices": [
                    {"message": {"content": [{"image": "https://dashscope/result.png"}]}}
                ]
            }
        }
        with (
            patch(
                "plugins.image_gen.dashscope.post_json",
                return_value=(payload, None),
            ) as mock_post,
            patch(
                "plugins.image_gen.dashscope._save_safe_image_url",
                return_value=Path("/tmp/dashscope-result.png"),
            ) as mock_save,
        ):
            result = DashScopeQwenImageProvider().generate(
                prompt="A precise enterprise dashboard illustration",
                aspect_ratio="landscape",
            )

        assert result["success"] is True
        assert result["image"] == "/tmp/dashscope-result.png"
        assert result["provider"] == "dashscope"
        assert result["model"] == "qwen-image-2.0-pro"
        assert mock_save.call_args.args[0] == "https://dashscope/result.png"
        assert (
            mock_post.call_args.kwargs["url"]
            == "https://llm-demo.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        )
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer dashscope-secret"
        assert mock_post.call_args.kwargs["payload"]["parameters"]["size"] == "1664*928"

    def test_named_credential_is_used_for_availability_and_generation(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://legacy.example.com")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "legacy-workspace")
        monkeypatch.setenv("DASHSCOPE_REGION", "ap-southeast-1")
        monkeypatch.setenv(
            "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY", "named-dashscope-secret"
        )
        config = {
            "provider_credentials": [
                {
                    "id": "alibaba-default",
                    "provider_family": "alibaba_dashscope",
                    "label": "Alibaba default",
                    "auth_type": "api_key",
                    "secret_env": "TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY",
                }
            ],
            "image_gen": {
                "provider": "dashscope",
                "credential_ref": "alibaba-default",
                "options": {"workspace_id": "llm-demo", "region": "cn-beijing"},
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        payload = {"output": {"image": "https://dashscope/result.png"}}
        with (
            patch(
                "plugins.image_gen.dashscope.post_json",
                return_value=(payload, None),
            ) as mock_post,
            patch(
                "plugins.image_gen.dashscope._save_safe_image_url",
                return_value=Path("/tmp/result.png"),
            ),
        ):
            provider = DashScopeQwenImageProvider()
            assert provider.is_available() is True
            result = provider.generate("A city skyline")

        assert result["success"] is True
        assert (
            mock_post.call_args.kwargs["headers"]["Authorization"]
            == "Bearer named-dashscope-secret"
        )
        assert mock_post.call_args.kwargs["url"] == (
            "https://llm-demo.cn-beijing.maas.aliyuncs.com/api/v1/services/"
            "aigc/multimodal-generation/generation"
        )

    def test_quick_credential_public_options_ignore_legacy_endpoint_env(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv(
            "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY", "quick-secret"
        )
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://legacy.example.com")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "legacy-workspace")
        monkeypatch.setenv("DASHSCOPE_REGION", "ap-southeast-1")
        config = {
            "provider_credentials": [
                {
                    "id": "taiji-alibaba-quick",
                    "provider_family": "alibaba_dashscope",
                    "label": "Alibaba quick setup",
                    "auth_type": "api_key",
                    "secret_env": (
                        "TAIJI_CREDENTIAL_TAIJI_ALIBABA_QUICK_API_KEY"
                    ),
                }
            ],
            "image_gen": {
                "provider": "dashscope",
                "credential_ref": "taiji-alibaba-quick",
                "options": {
                    "endpoint_mode": "public",
                    "region": "cn-beijing",
                },
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        payload = {"output": {"image": "https://dashscope/result.png"}}
        with (
            patch(
                "plugins.image_gen.dashscope.post_json",
                return_value=(payload, None),
            ) as mock_post,
            patch(
                "plugins.image_gen.dashscope._save_safe_image_url",
                return_value=Path("/tmp/result.png"),
            ),
        ):
            result = DashScopeQwenImageProvider().generate("A city skyline")

        assert result["success"] is True
        assert mock_post.call_args.kwargs["url"] == (
            "https://dashscope.aliyuncs.com/api/v1/services/"
            "aigc/multimodal-generation/generation"
        )
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
            "Bearer quick-secret"
        )

    def test_named_credential_config_load_failure_never_uses_legacy_state(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-must-not-be-used")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "legacy-workspace")
        config_path = tmp_path / "config.yaml"
        config_path.write_text("image_gen: [\n", encoding="utf-8")
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        with (
            patch("plugins.image_gen.dashscope.post_json") as mock_post,
        ):
            provider = DashScopeQwenImageProvider()
            assert provider.is_available() is False
            result = provider.generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "configuration_error"
        mock_post.assert_not_called()

    def test_named_credential_config_read_error_never_uses_legacy_state(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-must-not-be-used")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "legacy-workspace")
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "image_gen": {
                        "provider": "dashscope",
                        "credential_ref": "alibaba-default",
                        "options": {"workspace_id": "named-workspace"},
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
        original_read_text = Path.read_text

        def deny_config_read(path, *args, **kwargs):
            if path == config_path:
                raise PermissionError("config unreadable")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", deny_config_read)
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        with patch("plugins.image_gen.dashscope.post_json") as mock_post:
            provider = DashScopeQwenImageProvider()
            assert provider.is_available() is False
            result = provider.generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "configuration_error"
        mock_post.assert_not_called()

    def test_named_credential_never_falls_back_to_legacy_key(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-must-not-be-used")
        config = {
            "provider_credentials": [
                {
                    "id": "alibaba-image",
                    "provider_family": "alibaba_dashscope",
                    "label": "Alibaba image",
                    "auth_type": "api_key",
                    "secret_env": "TAIJI_CREDENTIAL_ALIBABA_IMAGE_API_KEY",
                }
            ],
            "image_gen": {
                "provider": "dashscope",
                "credential_ref": "alibaba-image",
                "options": {"workspace_id": "llm-demo"},
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        assert provider.is_available() is False
        result = provider.generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "auth_required"

    def test_selected_model_is_sent_unchanged(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "llm-demo")
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        with (
            patch(
                "plugins.image_gen.dashscope.post_json",
                return_value=(
                    {"output": {"image": "https://dashscope/result.png"}},
                    None,
                ),
            ) as mock_post,
            patch(
                "plugins.image_gen.dashscope._save_safe_image_url",
                return_value=Path("/tmp/result.png"),
            ),
        ):
            result = provider.generate("A city skyline", model="qwen-image")

        assert result["success"] is True
        assert mock_post.call_args.kwargs["payload"]["model"] == "qwen-image"

    def test_unknown_model_is_rejected(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        with pytest.raises(ValueError, match="Unsupported DashScope image model"):
            DashScopeQwenImageProvider()._model("unknown-model")

    def test_unknown_model_returns_structured_error_from_public_generate(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        result = DashScopeQwenImageProvider().generate(
            "A city skyline", model="unknown-model"
        )

        assert result["success"] is False
        assert result["error_type"] == "invalid_argument"
        assert result["provider"] == "dashscope"
        assert result["model"] == "unknown-model"

    def test_custom_endpoint_accepts_full_generation_url(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv(
            "DASHSCOPE_BASE_URL",
            "https://gateway.example.com/api/v1/services/aigc/multimodal-generation/generation",
        )
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))
            ],
        )
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        assert provider.is_available() is True
        assert provider._endpoint() == (
            "https://gateway.example.com/api/v1/services/aigc/multimodal-generation/generation"
        )

    @pytest.mark.parametrize(
        ("resolved_ip", "expected"),
        [("127.0.0.1", False), ("10.0.0.5", False), ("8.8.8.8", True)],
    )
    def test_custom_availability_uses_url_safety_dns_resolution(
        self, monkeypatch, resolved_ip, expected
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://gateway.example.com")
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (resolved_ip, 0))
            ],
        )
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        assert DashScopeQwenImageProvider().is_available() is expected

    def test_custom_availability_is_false_when_dns_fails(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://gateway.example.com")

        def fail_dns(*args, **kwargs):
            raise socket.gaierror("not found")

        monkeypatch.setattr(socket, "getaddrinfo", fail_dns)
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        assert DashScopeQwenImageProvider().is_available() is False

    def test_safe_image_download_rejects_direct_private_url(self, monkeypatch):
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image", side_effect=ValueError("unsafe image URL")
        ) as safe_saver, pytest.raises(ValueError, match="safety"):
            dashscope._save_safe_image_url("https://private.example/image.png")
        safe_saver.assert_called_once()

    def test_safe_image_download_rejects_public_redirect_to_private(self, monkeypatch):
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image", side_effect=ValueError("unsafe image URL")
        ) as safe_saver, pytest.raises(ValueError, match="safety"):
            dashscope._save_safe_image_url("https://public.example/image.png")
        assert safe_saver.call_args.kwargs["max_redirects"] == 3

    def test_safe_image_download_follows_public_redirect_and_saves_image(self, monkeypatch):
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image", return_value=Path("/tmp/safe.png")
        ) as safe_saver:
            saved = dashscope._save_safe_image_url(
                "https://public.example/image.png"
            )
        assert saved == Path("/tmp/safe.png")
        assert safe_saver.call_args.kwargs["url_validator"](
            "https://cdn.example/image.png"
        )

    @pytest.mark.parametrize(
        "fake_ip",
        ("198.18.0.0", "198.18.2.13", "198.19.255.255"),
    )
    def test_safe_image_download_rejects_dashscope_oss_fake_ip(
        self, monkeypatch, fake_ip
    ):
        from agent.image_gen_provider import save_url_image as agent_save_url_image
        from plugins.image_gen import dashscope

        request_get = MagicMock()

        def controlled_save(url, **kwargs):
            return agent_save_url_image(
                url,
                resolver=lambda host, port, **resolver_kwargs: [
                    (
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (fake_ip, port),
                    )
                ],
                request_get=request_get,
                **kwargs,
            )

        monkeypatch.setattr(dashscope, "save_url_image", controlled_save)
        with pytest.raises(ValueError, match="safety"):
            dashscope._save_safe_image_url(
                "https://dashscope-7c2c.oss-accelerate.aliyuncs.com/output.png"
            )
        request_get.assert_not_called()

    def test_safe_image_download_redacts_signed_url_on_connection_error(
        self, monkeypatch
    ):
        signed_url = (
            "https://dashscope-7c2c.oss-accelerate.aliyuncs.com/output.png"
            "?Signature=must-not-leak&AccessKeyId=must-not-leak"
        )
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image",
            side_effect=RuntimeError(f"connection failed for {signed_url}"),
        ), pytest.raises(ValueError) as exc_info:
            dashscope._save_safe_image_url(signed_url)

        assert str(exc_info.value) == "DashScope image download request failed"
        assert "Signature" not in str(exc_info.value)

    def test_safe_image_download_redacts_signed_url_on_http_error(
        self, monkeypatch
    ):
        signed_url = (
            "https://dashscope-7c2c.oss-accelerate.aliyuncs.com/output.png"
            "?Signature=must-not-leak"
        )
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image",
            side_effect=RuntimeError(f"403 Client Error for url: {signed_url}"),
        ), pytest.raises(ValueError) as exc_info:
            dashscope._save_safe_image_url(signed_url)

        assert str(exc_info.value) == "DashScope image download request failed"
        assert "Signature" not in str(exc_info.value)

    def test_safe_image_download_redacts_signed_url_on_stream_error(
        self, monkeypatch
    ):
        signed_url = (
            "https://dashscope-7c2c.oss-accelerate.aliyuncs.com/output.png"
            "?Signature=must-not-leak"
        )
        from plugins.image_gen import dashscope

        with patch.object(
            dashscope, "save_url_image",
            side_effect=RuntimeError(f"stream failed for {signed_url}"),
        ), pytest.raises(ValueError) as exc_info:
            dashscope._save_safe_image_url(signed_url)

        assert str(exc_info.value) == "DashScope image download request failed"
        assert "Signature" not in str(exc_info.value)

    @pytest.mark.parametrize(
        "url",
        [
            "http://dashscope-7c2c.oss-accelerate.aliyuncs.com/output.png",
            "https://user@dashscope-7c2c.oss-accelerate.aliyuncs.com/output.png",
            "https://dashscope-7c2c.oss-accelerate.aliyuncs.com:8443/output.png",
            "https://dashscope-7c2c.oss-accelerate.aliyuncs.com.evil.example/output.png",
            "https://metadata.google.internal/latest/meta-data",
        ],
    )
    def test_safe_image_download_never_weakens_url_safety_floor(
        self, monkeypatch, url
    ):
        from agent.image_gen_provider import save_url_image as agent_save_url_image
        from plugins.image_gen import dashscope

        request_get = MagicMock()

        def controlled_save(candidate, **kwargs):
            return agent_save_url_image(
                candidate,
                resolver=lambda host, port, **resolver_kwargs: [
                    (
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        ("198.18.2.13", port),
                    )
                ],
                request_get=request_get,
                **kwargs,
            )

        monkeypatch.setattr(dashscope, "save_url_image", controlled_save)
        with pytest.raises(ValueError, match="safety"):
            dashscope._save_safe_image_url(url)
        request_get.assert_not_called()

    @pytest.mark.parametrize(
        "base_url",
        ["https://gateway.example.com:not-a-port", "https://gateway.example.com:0"],
    )
    def test_custom_endpoint_with_bad_port_is_unavailable_and_never_requested(
        self, monkeypatch, base_url
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", base_url)
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        assert provider.is_available() is False
        with patch("plugins.image_gen.dashscope.post_json") as mock_post:
            result = provider.generate("A city skyline")
        assert result["success"] is False
        assert result["error_type"] == "endpoint_invalid"
        mock_post.assert_not_called()

    @pytest.mark.parametrize("hostname", ["localtest.me", "127.0.0.1.nip.io"])
    def test_custom_endpoint_resolving_to_loopback_is_rejected_before_request(
        self, monkeypatch, hostname
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", f"https://{hostname}")
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))
            ],
        )
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        provider = DashScopeQwenImageProvider()
        assert provider.is_available() is False
        with patch("plugins.image_gen.dashscope.post_json") as mock_post:
            result = provider.generate("A city skyline")

        assert result["success"] is False
        assert result["error_type"] == "endpoint_invalid"
        mock_post.assert_not_called()

    def test_custom_public_endpoint_uses_common_safe_post_contract(
        self, monkeypatch
    ):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://public.example")
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))
            ],
        )
        from plugins.image_gen import dashscope

        payload = {"output": {"image": "https://dashscope/result.png"}}
        with (
            patch.object(
                dashscope,
                "post_json",
                return_value=(payload, None),
            ) as safe_post,
            patch("requests.post") as legacy_post,
            patch.object(
                dashscope,
                "_save_safe_image_url",
                return_value=Path("/tmp/result.png"),
            ),
        ):
            result = dashscope.DashScopeQwenImageProvider().generate(
                "A city skyline"
            )

        assert result["success"] is True
        legacy_post.assert_not_called()
        safe_post.assert_called_once()
        request_kwargs = safe_post.call_args.kwargs
        assert request_kwargs["url"] == (
            "https://public.example/api/v1/services/"
            "aigc/multimodal-generation/generation"
        )
        assert request_kwargs["timeout"] == dashscope.TIMEOUT_SECONDS
        assert request_kwargs["headers"]["Authorization"] == (
            "Bearer dashscope-secret"
        )
        assert request_kwargs["payload"]["model"] == "qwen-image-2.0-pro"
        assert "request_post" not in request_kwargs

    def test_custom_endpoint_propagates_redacted_safe_transport_error(
        self, monkeypatch
    ):
        secret = "dashscope-super-secret"
        monkeypatch.setenv("DASHSCOPE_API_KEY", secret)
        monkeypatch.setenv("DASHSCOPE_ENDPOINT_MODE", "custom")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://public.example")
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))
            ],
        )
        from plugins.image_gen import dashscope

        with (
            patch.object(
                dashscope,
                "post_json",
                return_value=_redacted_transport_error("dashscope"),
            ) as safe_post,
            patch("requests.post") as legacy_post,
        ):
            result = dashscope.DashScopeQwenImageProvider().generate(
                "A city skyline"
            )

        assert result["success"] is False
        assert result["error_type"] == "api_error"
        assert secret not in result["error"]
        legacy_post.assert_not_called()
        safe_post.assert_called_once()

    @pytest.mark.parametrize(
        ("values", "available"),
        [
            ({"DASHSCOPE_API_KEY": "key"}, True),
            ({"DASHSCOPE_API_KEY": "key", "DASHSCOPE_ENDPOINT_MODE": "public"}, True),
            ({"DASHSCOPE_API_KEY": "key", "DASHSCOPE_ENDPOINT_MODE": "workspace"}, False),
            (
                {
                    "DASHSCOPE_API_KEY": "key",
                    "DASHSCOPE_ENDPOINT_MODE": "custom",
                    "DASHSCOPE_BASE_URL": "https://gateway.example.com",
                },
                True,
            ),
            ({"DASHSCOPE_API_KEY": "key", "DASHSCOPE_ENDPOINT_MODE": "custom"}, False),
        ],
    )
    def test_availability_requires_complete_endpoint_configuration(self, monkeypatch, values, available):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))
            ],
        )
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        assert DashScopeQwenImageProvider().is_available() is available

    def test_http_error_redacts_secret(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "ws-cn-test")
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider

        with patch(
            "plugins.image_gen.dashscope.post_json",
            return_value=_redacted_transport_error("dashscope"),
        ):
            result = DashScopeQwenImageProvider().generate("A city skyline")

        assert result["success"] is False
        assert "dashscope-secret" not in result["error"]

    def test_register_wires_provider(self):
        from plugins.image_gen.dashscope import DashScopeQwenImageProvider, register

        ctx = MagicMock()
        register(ctx)
        (registered,), _ = ctx.register_image_gen_provider.call_args
        assert isinstance(registered, DashScopeQwenImageProvider)


class TestQianfanImageProvider:
    def test_successful_generation_posts_to_baidu_endpoint(self, monkeypatch):
        monkeypatch.setenv("QIANFAN_API_KEY", "qianfan-secret")
        from plugins.image_gen.qianfan import QianfanImageGenProvider

        with (
            patch(
                "plugins.image_gen.qianfan.post_json",
                return_value=(
                    {"data": [{"url": "https://qianfan/result.png"}]},
                    None,
                ),
            ) as mock_post,
            patch(
                "plugins.image_gen.qianfan.save_url_image",
                return_value=Path("/tmp/qianfan-result.png"),
            ),
        ):
            result = QianfanImageGenProvider().generate("A reliable grid control room")

        assert result["success"] is True
        assert result["image"] == "/tmp/qianfan-result.png"
        assert result["provider"] == "qianfan"
        assert mock_post.call_args.kwargs["url"] == "https://qianfan.baidubce.com/v2/images/generations"
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer qianfan-secret"
        assert mock_post.call_args.kwargs["payload"]["model"] == "qwen-image"
        assert mock_post.call_args.kwargs["payload"]["size"] == "1536x1024"

    def test_surface_auth_prompt_and_register(self, monkeypatch):
        from plugins.image_gen.qianfan import QianfanImageGenProvider, register

        provider = QianfanImageGenProvider()
        schema = provider.get_setup_schema()
        assert schema["domestic"] is True
        assert schema["integration_status"] == "stable"
        assert schema["credential_fields"][0]["env_var"] == "QIANFAN_API_KEY"
        assert provider.generate("text")["error_type"] == "auth_required"
        monkeypatch.setenv("QIANFAN_API_KEY", "qianfan-secret")
        assert provider.generate(" ")["error_type"] == "invalid_argument"
        ctx = MagicMock()
        register(ctx)
        (registered,), _ = ctx.register_image_gen_provider.call_args
        assert isinstance(registered, QianfanImageGenProvider)

    def test_http_error_redacts_secret(self, monkeypatch):
        monkeypatch.setenv("QIANFAN_API_KEY", "qianfan-secret")
        from plugins.image_gen.qianfan import QianfanImageGenProvider

        with patch(
            "plugins.image_gen.qianfan.post_json",
            return_value=_redacted_transport_error("qianfan"),
        ):
            result = QianfanImageGenProvider().generate("A city skyline")

        assert result["success"] is False
        assert "qianfan-secret" not in result["error"]


class TestZhipuImageProvider:
    def test_successful_generation_posts_to_bigmodel_endpoint(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "glm-secret")
        from plugins.image_gen.zhipu_image import ZhipuImageGenProvider

        with (
            patch(
                "plugins.image_gen.zhipu_image.post_json",
                return_value=(
                    {"data": [{"url": "https://zhipu/result.png"}]},
                    None,
                ),
            ) as mock_post,
            patch(
                "plugins.image_gen.zhipu_image.save_url_image",
                return_value=Path("/tmp/zhipu-result.png"),
            ),
        ):
            result = ZhipuImageGenProvider().generate("A reliable grid control room")

        assert result["success"] is True
        assert result["image"] == "/tmp/zhipu-result.png"
        assert result["provider"] == "zhipu-image"
        assert mock_post.call_args.kwargs["url"] == "https://open.bigmodel.cn/api/paas/v4/images/generations"
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer glm-secret"
        assert mock_post.call_args.kwargs["payload"]["model"] == "glm-image"
        assert mock_post.call_args.kwargs["payload"]["size"] == "1536x1024"

    def test_surface_auth_prompt_and_register(self, monkeypatch):
        from plugins.image_gen.zhipu_image import ZhipuImageGenProvider, register

        provider = ZhipuImageGenProvider()
        schema = provider.get_setup_schema()
        assert provider.name == "zhipu-image"
        assert schema["domestic"] is True
        assert schema["credential_fields"][0]["env_var"] == "GLM_API_KEY"
        assert provider.generate("text")["error_type"] == "auth_required"
        monkeypatch.setenv("GLM_API_KEY", "glm-secret")
        assert provider.generate(" ")["error_type"] == "invalid_argument"
        ctx = MagicMock()
        register(ctx)
        (registered,), _ = ctx.register_image_gen_provider.call_args
        assert isinstance(registered, ZhipuImageGenProvider)

    def test_http_error_redacts_secret(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "glm-secret")
        from plugins.image_gen.zhipu_image import ZhipuImageGenProvider

        with patch(
            "plugins.image_gen.zhipu_image.post_json",
            return_value=_redacted_transport_error("zhipu-image"),
        ):
            result = ZhipuImageGenProvider().generate("A city skyline")

        assert result["success"] is False
        assert "glm-secret" not in result["error"]


class TestMiniMaxImageProvider:
    def test_successful_generation_posts_to_minimax_endpoint(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "minimax-secret")
        from plugins.image_gen.minimax_image import MinimaxImageGenProvider

        with (
            patch(
                "plugins.image_gen.minimax_image.post_json",
                return_value=(
                    {"data": {"image_urls": ["https://minimax/result.png"]}},
                    None,
                ),
            ) as mock_post,
            patch(
                "plugins.image_gen.minimax_image.save_url_image",
                return_value=Path("/tmp/minimax-result.png"),
            ),
        ):
            result = MinimaxImageGenProvider().generate("A reliable grid control room")

        assert result["success"] is True
        assert result["image"] == "/tmp/minimax-result.png"
        assert result["provider"] == "minimax-image"
        assert mock_post.call_args.kwargs["url"] == "https://api.minimax.io/v1/image_generation"
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer minimax-secret"
        assert mock_post.call_args.kwargs["payload"]["model"] == "image-01"
        assert mock_post.call_args.kwargs["payload"]["aspect_ratio"] == "16:9"

    def test_surface_auth_prompt_and_register(self, monkeypatch):
        from plugins.image_gen.minimax_image import MinimaxImageGenProvider, register

        provider = MinimaxImageGenProvider()
        schema = provider.get_setup_schema()
        assert provider.name == "minimax-image"
        assert schema["domestic"] is True
        assert schema["credential_fields"][0]["env_var"] == "MINIMAX_API_KEY"
        assert provider.generate("text")["error_type"] == "auth_required"
        monkeypatch.setenv("MINIMAX_API_KEY", "minimax-secret")
        assert provider.generate(" ")["error_type"] == "invalid_argument"
        ctx = MagicMock()
        register(ctx)
        (registered,), _ = ctx.register_image_gen_provider.call_args
        assert isinstance(registered, MinimaxImageGenProvider)

    def test_http_error_redacts_secret(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "minimax-secret")
        from plugins.image_gen.minimax_image import MinimaxImageGenProvider

        with patch(
            "plugins.image_gen.minimax_image.post_json",
            return_value=_redacted_transport_error("minimax-image"),
        ):
            result = MinimaxImageGenProvider().generate("A city skyline")

        assert result["success"] is False
        assert "minimax-secret" not in result["error"]
