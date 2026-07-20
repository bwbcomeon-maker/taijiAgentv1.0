"""Behavioral RED tests for custom-provider credentials and outbound HTTP.

These tests deliberately exercise only public entry points that exist before
``agent.safe_outbound_http`` is introduced.  Planned transport bridges are
installed on the existing consumer modules with ``raising=False``; an absent
bridge therefore produces a security assertion failure, never an import or
future-keyword ``TypeError``.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import pytest
import requests
import yaml


_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_PNG_1PX_B64 = base64.b64encode(_PNG_1PX).decode("ascii")


class _ImageDownloadResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = _PNG_1PX,
    ) -> None:
        self.status_code = status
        self.headers = headers or {"Content-Type": "image/png"}
        self._body = body

    def iter_content(self, chunk_size: int = 64 * 1024):
        del chunk_size
        yield self._body

    def close(self) -> None:
        return None


class _ProviderResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        payload: Any = None,
        chunks: list[bytes] | None = None,
        json_calls: list[str] | None = None,
        label: str = "response",
    ) -> None:
        self.status_code = status
        self.headers = headers or {"Content-Type": "application/json"}
        self._payload = {"data": []} if payload is None else payload
        self._chunks = chunks if chunks is not None else [json.dumps(self._payload).encode()]
        self._json_calls = json_calls
        self._label = label

    def json(self) -> Any:
        if self._json_calls is not None:
            self._json_calls.append(self._label)
        return self._payload

    def iter_bytes(self):
        yield from self._chunks

    def iter_content(self, chunk_size: int = 64 * 1024):
        del chunk_size
        yield from self._chunks

    def __enter__(self) -> "_ProviderResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        del exc_type, exc_value, traceback
        self.close()
        return False

    def close(self) -> None:
        return None


class _ClientCapture:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _HTTPXCapture:
    def __init__(self, *, kind: str, events: list[str], **kwargs: Any) -> None:
        self.kind = kind
        self.kwargs = kwargs
        self._events = events

    def close(self) -> None:
        self._events.append(f"{self.kind}-close")

    async def aclose(self) -> None:
        self._events.append(f"{self.kind}-aclose")


def _valid_image_entry(provider_id: str = "router", **overrides: Any) -> dict[str, Any]:
    entry = {
        "id": provider_id,
        "name": f"{provider_id} images",
        "base_url": "https://images.example.test/v1",
        "models": ["image-model"],
        "default_model": "image-model",
        "response_format": "b64_json",
    }
    entry.update(overrides)
    return entry


def _valid_vision_entry(provider_id: str = "router", **overrides: Any) -> dict[str, Any]:
    entry = {
        "id": provider_id,
        "name": f"{provider_id} vision",
        "base_url": "https://vision.example.test/v1",
        "models": ["vision-model"],
        "default_model": "vision-model",
        "transport": "openai_chat_completions",
    }
    entry.update(overrides)
    return entry


def _rejection(call: Callable[[], Any]) -> Exception | None:
    try:
        call()
    except Exception as exc:  # the final assertion converts this to test evidence
        return exc
    return None


def _assert_security_contract(violations: list[str]) -> None:
    assert not violations, "\n".join(violations)


def _set_webui_home(monkeypatch: pytest.MonkeyPatch, model_config: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(model_config, "_get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_get_config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(model_config, "reload_config", lambda: None)
    monkeypatch.setattr(model_config, "invalidate_models_cache", lambda: None)
    monkeypatch.setattr(model_config, "_invalidate_vision_verification", lambda: None)
    monkeypatch.setattr(model_config, "_invalidate_image_gen_verification", lambda: None)


def _public_resolver_for(address: str) -> Callable[..., list[tuple[Any, ...]]]:
    def resolver(host: str, port: int, *args: Any, **kwargs: Any):
        del host, args, kwargs
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, port))]

    return resolver


def _bind_custom_credential(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    credential_ref: str,
    secret: str,
) -> dict[str, Any]:
    """Install canonical named metadata and its dedicated Secret."""
    from agent.provider_credentials import credential_secret_env

    secret_env = credential_secret_env(credential_ref)
    config_data = {
        "provider_credentials": [
            {
                "id": credential_ref,
                "provider_family": "custom",
                "auth_type": "api_key",
                "secret_env": secret_env,
            }
        ]
    }
    config_path = tmp_path / f"{credential_ref}-config.yaml"
    config_path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    monkeypatch.setenv(secret_env, secret)
    return config_data


def test_trusted_proxy_config_reader_joins_shared_credential_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.provider_credentials import credential_transaction
    from agent.safe_outbound_http import _load_trusted_proxy_profiles

    config_path = tmp_path / "runtime" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        yaml.safe_dump(
            {
                "trusted_proxy_profiles": [
                    {
                        "name": "approved",
                        "proxy_url": "https://proxy.example.com",
                        "approved": True,
                        "capabilities": [
                            "public_egress",
                            "dns_ip_classification",
                        ],
                        "proxy_connect_scope": "public_direct",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    started = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        started.set()
        try:
            _load_trusted_proxy_profiles()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            finished.set()

    with credential_transaction(config_path):
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        assert started.wait(1)
        assert not finished.wait(0.1)

    thread.join(timeout=2)
    assert not thread.is_alive()
    assert errors == []


def test_credential_binding_default_matrix_is_fail_closed_and_legacy_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.provider_credentials import credential_secret_env, resolve_api_key

    violations: list[str] = []
    legacy_env = "DASHSCOPE_API_KEY"
    legacy_value = "legacy-dashscope-key"
    monkeypatch.setenv(legacy_env, legacy_value)
    for name in (
        "missing-ref",
        "empty-ref",
        "mismatch-ref",
        "tampered-ref",
        "default-one",
        "default-two",
        "tampered-default",
    ):
        monkeypatch.delenv(credential_secret_env(name), raising=False)

    valid_empty = {
        "id": "empty-ref",
        "provider_family": "alibaba_dashscope",
        "auth_type": "api_key",
        "secret_env": credential_secret_env("empty-ref"),
    }
    mismatch = {
        "id": "mismatch-ref",
        "provider_family": "zhipu",
        "auth_type": "api_key",
        "secret_env": credential_secret_env("mismatch-ref"),
    }
    tampered = {
        "id": "tampered-ref",
        "provider_family": "alibaba_dashscope",
        "auth_type": "api_key",
        "secret_env": "ATTACKER_CONTROLLED_API_KEY",
    }
    explicit_cases = [
        ("missing", "missing-ref", [], True, None),
        ("empty", "empty-ref", [valid_empty], False, ""),
        ("family-mismatch", "mismatch-ref", [mismatch], True, None),
        ("secret-env-tamper", "tampered-ref", [tampered], True, None),
    ]
    for label, ref, rows, should_reject, expected in explicit_cases:
        error = None
        value = None
        try:
            value = resolve_api_key(
                "alibaba",
                ref,
                config_data={"provider_credentials": rows},
            )
        except Exception as exc:
            error = exc
        if should_reject and not isinstance(error, ValueError):
            violations.append(f"{label}: explicit invalid ref did not fail closed")
        if not should_reject and (error is not None or value != expected):
            violations.append(f"{label}: explicit empty Secret fell back to legacy")

    no_default = resolve_api_key(
        "alibaba", config_data={"provider_credentials": []}
    )
    if no_default != legacy_value:
        violations.append("no-ref/no-default did not use canonical legacy env")

    missing_secret_default = {
        "id": "default-one",
        "provider_family": "alibaba_dashscope",
        "auth_type": "api_key",
        "secret_env": credential_secret_env("default-one"),
        "default": True,
    }
    value = resolve_api_key(
        "alibaba",
        config_data={"provider_credentials": [missing_secret_default]},
    )
    if value != legacy_value:
        violations.append("unique valid default with missing Secret lost legacy compatibility")

    tampered_default = {
        **missing_secret_default,
        "id": "tampered-default",
        "secret_env": "ATTACKER_CONTROLLED_API_KEY",
    }
    error = _rejection(
        lambda: resolve_api_key(
            "alibaba",
            config_data={"provider_credentials": [tampered_default]},
        )
    )
    if not isinstance(error, ValueError):
        violations.append("tampered default was ignored and silently fell back to legacy")

    defaults = []
    for name in ("default-one", "default-two"):
        secret_env = credential_secret_env(name)
        monkeypatch.setenv(secret_env, f"{name}-secret")
        defaults.append(
            {
                "id": name,
                "provider_family": "alibaba_dashscope",
                "auth_type": "api_key",
                "secret_env": secret_env,
                "default": True,
            }
        )
    error = _rejection(
        lambda: resolve_api_key(
            "alibaba", config_data={"provider_credentials": defaults}
        )
    )
    if not isinstance(error, ValueError):
        violations.append("duplicate valid defaults did not fail closed")

    _assert_security_contract(violations)


def test_credential_config_source_and_structure_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.provider_credentials import credential_secret_env, resolve_api_key

    legacy_value = "canonical-legacy-key"
    hidden_env = credential_secret_env("hidden-default")
    hidden_default = {
        "id": "hidden-default",
        "provider_family": "alibaba_dashscope",
        "auth_type": "api_key",
        "secret_env": hidden_env,
        "default": True,
    }
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    (runtime_home / "config.yaml").write_text(
        "provider_credentials: []\n",
        encoding="utf-8",
    )
    hidden_config = tmp_path / "hidden-config.yaml"
    hidden_config.write_text(
        yaml.safe_dump({"provider_credentials": [hidden_default]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(hidden_config))
    monkeypatch.setenv(hidden_env, "hidden-named-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", legacy_value)

    assert resolve_api_key("alibaba") == legacy_value

    for invalid_auth_type in (False, 0, "", None):
        invalid_default = {
            **hidden_default,
            "auth_type": invalid_auth_type,
        }
        with pytest.raises(ValueError, match="认证类型"):
            resolve_api_key(
                "alibaba",
                config_data={"provider_credentials": [invalid_default]},
            )

    monkeypatch.delenv("TAIJI_RUNTIME_HOME")
    malformed_config = tmp_path / "malformed-config.yaml"
    malformed_config.write_text("provider_credentials: [\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(malformed_config))
    with pytest.raises(ValueError, match="credential config"):
        resolve_api_key("alibaba")

    wrong_root_config = tmp_path / "wrong-root-config.yaml"
    wrong_root_config.write_text("- provider_credentials\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(wrong_root_config))
    with pytest.raises(ValueError, match="credential config"):
        resolve_api_key("alibaba")

    duplicate_mapping_cases = (
        (
            "duplicate-provider-credentials.yaml",
            (
                "provider_credentials: []\n"
                "provider_credentials:\n"
                "  - id: hidden-default\n"
                "    provider_family: alibaba_dashscope\n"
                "    auth_type: api_key\n"
                f"    secret_env: {hidden_env}\n"
                "    default: true\n"
            ),
        ),
        (
            "duplicate-credential-id.yaml",
            (
                "provider_credentials:\n"
                "  - id: attacker-shadow\n"
                "    id: hidden-default\n"
                "    provider_family: alibaba_dashscope\n"
                "    auth_type: api_key\n"
                f"    secret_env: {hidden_env}\n"
                "    default: true\n"
            ),
        ),
    )
    for filename, content in duplicate_mapping_cases:
        duplicate_config = tmp_path / filename
        duplicate_config.write_text(content, encoding="utf-8")
        monkeypatch.setenv("HERMES_CONFIG_PATH", str(duplicate_config))
        with pytest.raises(ValueError, match="duplicate"):
            resolve_api_key("alibaba")

    for invalid_rows in (None, {}, "not-a-list"):
        with pytest.raises(ValueError, match="provider_credentials"):
            resolve_api_key(
                "alibaba",
                config_data={"provider_credentials": invalid_rows},
            )


def test_custom_provider_credential_binding_is_canonical_across_runtime_and_webui(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent import auxiliary_client
    from agent import custom_image_providers as custom_image
    from agent import custom_vision_providers as custom_vision
    from agent import provider_credentials as credential_store
    from agent.provider_credentials import credential_secret_env
    webui_root = Path(__file__).resolve().parents[3] / "hermes-webui"
    monkeypatch.syspath_prepend(str(webui_root))
    sys.modules.pop("api.model_config", None)
    from api import model_config

    violations: list[str] = []
    credential_ref = "custom-router"
    canonical_env = credential_secret_env(credential_ref)
    named_secret = "named-custom-secret"
    _bind_custom_credential(
        monkeypatch,
        tmp_path,
        credential_ref=credential_ref,
        secret=named_secret,
    )
    monkeypatch.delenv("TAIJI_IMAGE_CUSTOM_ROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TAIJI_VISION_CUSTOM_ROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ATTACKER_CONTROLLED_API_KEY", raising=False)

    for label, normalizer, entry in (
        (
            "image",
            custom_image.normalize_custom_image_provider_entry,
            _valid_image_entry(
                credential_ref=credential_ref,
                api_key_env="ATTACKER_CONTROLLED_API_KEY",
            ),
        ),
        (
            "vision",
            custom_vision.normalize_custom_vision_provider_entry,
            _valid_vision_entry(
                credential_ref=credential_ref,
                api_key_env="ATTACKER_CONTROLLED_API_KEY",
            ),
        ),
    ):
        if not isinstance(_rejection(lambda n=normalizer, e=entry: n(e)), ValueError):
            violations.append(f"{label}: caller-controlled api_key_env was accepted")

    normalized = custom_image.normalize_custom_image_provider_entry(
        _valid_image_entry(credential_ref=credential_ref)
    )
    if normalized.get("credential_ref") != credential_ref:
        violations.append("image runtime dropped canonical credential_ref")
    if "api_key_env" in normalized:
        violations.append("image runtime persisted a capability-specific api_key_env")

    row = custom_image.custom_image_provider_public_row(
        _valid_image_entry(credential_ref=credential_ref)
    )
    if (row.get("key_status") or {}).get("env_var") != canonical_env:
        violations.append("image public row did not project canonical credential env")
    if not (row.get("key_status") or {}).get("configured"):
        violations.append("image public row ignored the named credential Secret")

    provider = custom_image.ConfigurableOpenAIImageProvider(
        _valid_image_entry(credential_ref=credential_ref)
    )
    if not provider.is_available():
        violations.append("image Provider availability ignored canonical credential binding")
    schema_keys = {
        item.get("key") for item in provider.get_setup_schema().get("env_vars", [])
    }
    if schema_keys != {canonical_env}:
        violations.append("image setup schema advertised a non-canonical Secret env")

    bridge_calls: list[dict[str, Any]] = []
    legacy_calls: list[dict[str, Any]] = []

    def pinned_bridge(*args: Any, **kwargs: Any) -> _ProviderResponse:
        bridge_calls.append({"args": args, **kwargs})
        return _ProviderResponse(
            payload={"data": [{"b64_json": _PNG_1PX_B64}]}
        )

    def legacy_post(url: str, **kwargs: Any) -> _ProviderResponse:
        legacy_calls.append({"url": url, **kwargs})
        return _ProviderResponse(
            payload={"data": [{"b64_json": _PNG_1PX_B64}]}
        )

    monkeypatch.setattr(
        custom_image, "request_pinned_https", pinned_bridge, raising=False
    )
    monkeypatch.setattr(
        custom_image,
        "read_bounded_json",
        lambda response, *_args, **_kwargs: response._payload,
        raising=False,
    )
    monkeypatch.setattr(requests, "post", legacy_post)
    monkeypatch.setattr(
        custom_image, "save_b64_image", lambda *_args, **_kwargs: tmp_path / "image.png"
    )
    result = provider.generate("draw a canonical key")
    if len(bridge_calls) != 1:
        violations.append("image generate did not reach the pinned bridge exactly once")
    elif bridge_calls[0].get("headers", {}).get(
        "Authorization"
    ) != f"Bearer {named_secret}":
        violations.append("pinned image bridge did not receive the canonical named Secret")
    if legacy_calls:
        violations.append("canonical credential path used legacy requests.post")
    if result.get("status") == "error" and result.get("error_type") == "auth_required":
        violations.append("image generate incorrectly reported canonical Secret as missing")

    monkeypatch.setattr(
        custom_vision,
        "find_custom_vision_provider_entry",
        lambda *_args, **_kwargs: _valid_vision_entry(
            credential_ref=credential_ref
        ),
    )
    monkeypatch.setattr(custom_vision, "is_custom_vision_base_url_safe", lambda _url: True)
    monkeypatch.setattr(auxiliary_client, "_get_auxiliary_task_config", lambda _task: {})
    vision_error = None
    vision_result = None
    try:
        vision_result = auxiliary_client.resolve_vision_provider_client(
            provider="custom:router", model="vision-model"
        )
    except Exception as exc:
        vision_error = exc
    if vision_error is not None or not vision_result or vision_result[1] is None:
        violations.append("custom vision resolver ignored canonical credential binding")

    _set_webui_home(monkeypatch, model_config, tmp_path)
    monkeypatch.setattr(custom_vision, "is_custom_vision_base_url_safe", lambda _url: True)
    attacker_body = _valid_image_entry(
        api_key_env="ATTACKER_CONTROLLED_API_KEY",
        api_key="attacker-value",
    )
    if not isinstance(
        _rejection(lambda: model_config.set_custom_image_provider_config(attacker_body)),
        ValueError,
    ):
        violations.append("WebUI image set accepted caller-controlled api_key_env")
    attacker_vision = _valid_vision_entry(
        provider_id="vision-attacker",
        api_key_env="ATTACKER_CONTROLLED_API_KEY",
        api_key="attacker-value",
    )
    if not isinstance(
        _rejection(lambda: model_config.set_custom_vision_provider_config(attacker_vision)),
        ValueError,
    ):
        violations.append("WebUI vision set accepted caller-controlled api_key_env")

    image_body = _valid_image_entry(
        provider_id="web-image",
        credential_ref="custom-web-image",
        api_key="web-image-secret",
    )
    image_set = None
    try:
        image_set = model_config.set_custom_image_provider_config(image_body)
    except Exception as exc:
        violations.append(f"WebUI image set failed through public CRUD seam: {type(exc).__name__}")
    config_path = tmp_path / "config.yaml"
    config_data = (
        yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if config_path.exists()
        else {}
    )
    saved_images = config_data.get("custom_image_providers") or []
    saved_image = saved_images[-1] if saved_images else {}
    if saved_image.get("credential_ref") != "custom-web-image":
        violations.append("WebUI image set did not persist canonical credential_ref")
    if "api_key_env" in saved_image:
        violations.append("WebUI image set persisted capability-specific api_key_env")
    image_credential = next(
        (
            item
            for item in config_data.get("provider_credentials") or []
            if isinstance(item, dict) and item.get("id") == "custom-web-image"
        ),
        None,
    )
    if image_credential is None:
        violations.append("WebUI image set did not create provider credential metadata")
    else:
        if image_credential.get("provider_family") != "custom":
            violations.append("WebUI image credential has the wrong provider family")
        if image_credential.get("auth_type") != "api_key":
            violations.append("WebUI image credential has the wrong auth type")
        if image_credential.get("secret_env") != credential_secret_env(
            "custom-web-image"
        ):
            violations.append("WebUI image credential has a non-canonical Secret env")
    if os.getenv(credential_secret_env("custom-web-image")) != "web-image-secret":
        violations.append("WebUI image set did not store its canonical credential Secret")
    image_get = model_config.get_custom_image_provider_configs()
    if image_set is not None and not image_get.get("providers"):
        violations.append("WebUI image get lost the saved provider")
    try:
        image_delete = model_config.delete_custom_image_provider_config("web-image")
        if any(
            row.get("id") == "custom:web-image"
            for row in image_delete.get("providers", [])
        ):
            violations.append("WebUI image delete left the provider visible")
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not any(
            isinstance(item, dict) and item.get("id") == "custom-web-image"
            for item in config_data.get("provider_credentials") or []
        ):
            violations.append("WebUI image delete removed a user-owned credential")
        if os.getenv(credential_secret_env("custom-web-image")) != "web-image-secret":
            violations.append("WebUI image delete removed a user-owned Secret")
    except Exception as exc:
        violations.append(f"WebUI image delete failed: {type(exc).__name__}")

    vision_body = _valid_vision_entry(
        provider_id="web-vision",
        credential_ref="custom-web-vision",
        api_key="web-vision-secret",
    )
    try:
        model_config.set_custom_vision_provider_config(vision_body)
        vision_get = model_config.get_custom_vision_provider_configs()
        if not vision_get.get("providers"):
            violations.append("WebUI vision get lost the saved provider")
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        saved_vision = (config_data.get("custom_vision_providers") or [{}])[-1]
        if saved_vision.get("credential_ref") != "custom-web-vision":
            violations.append("WebUI vision set did not persist canonical credential_ref")
        if "api_key_env" in saved_vision:
            violations.append("WebUI vision set persisted capability-specific api_key_env")
        vision_credential = next(
            (
                item
                for item in config_data.get("provider_credentials") or []
                if isinstance(item, dict) and item.get("id") == "custom-web-vision"
            ),
            None,
        )
        if vision_credential is None:
            violations.append("WebUI vision set did not create provider credential metadata")
        else:
            if vision_credential.get("provider_family") != "custom":
                violations.append("WebUI vision credential has the wrong provider family")
            if vision_credential.get("auth_type") != "api_key":
                violations.append("WebUI vision credential has the wrong auth type")
            if vision_credential.get("secret_env") != credential_secret_env(
                "custom-web-vision"
            ):
                violations.append("WebUI vision credential has a non-canonical Secret env")
        if os.getenv(credential_secret_env("custom-web-vision")) != "web-vision-secret":
            violations.append("WebUI vision set did not store its canonical credential Secret")
        vision_delete = model_config.delete_custom_vision_provider_config("web-vision")
        if any(
            row.get("id") == "custom:web-vision"
            for row in vision_delete.get("providers", [])
        ):
            violations.append("WebUI vision delete left the provider visible")
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not any(
            isinstance(item, dict) and item.get("id") == "custom-web-vision"
            for item in config_data.get("provider_credentials") or []
        ):
            violations.append("WebUI vision delete removed a user-owned credential")
        if os.getenv(credential_secret_env("custom-web-vision")) != "web-vision-secret":
            violations.append("WebUI vision delete removed a user-owned Secret")
    except Exception as exc:
        violations.append(f"WebUI vision CRUD failed: {type(exc).__name__}")

    shared_ref = "custom-web-shared"
    try:
        for provider_id in ("web-shared-one", "web-shared-two"):
            model_config.set_custom_image_provider_config(
                _valid_image_entry(
                    provider_id=provider_id,
                    credential_ref=shared_ref,
                    api_key="shared-secret",
                )
            )
        model_config.delete_custom_image_provider_config("web-shared-one")
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        shared_entries = config_data.get("custom_image_providers") or []
        if not any(
            isinstance(item, dict)
            and item.get("id") == "web-shared-two"
            and item.get("credential_ref") == shared_ref
            for item in shared_entries
        ):
            violations.append("WebUI image delete damaged the remaining shared consumer")
        if not any(
            isinstance(item, dict) and item.get("id") == shared_ref
            for item in config_data.get("provider_credentials") or []
        ):
            violations.append("WebUI image delete removed a shared credential")
        if os.getenv(credential_secret_env(shared_ref)) != "shared-secret":
            violations.append("WebUI image delete removed a shared credential Secret")
    except Exception as exc:
        violations.append(f"WebUI shared credential CRUD failed: {type(exc).__name__}")

    managed_provider_id = "web-managed-image"
    managed_ref = ""
    try:
        model_config.set_custom_image_provider_config(
            _valid_image_entry(
                provider_id=managed_provider_id,
                api_key="managed-secret",
            )
        )
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        managed_entry = next(
            (
                item
                for item in config_data.get("custom_image_providers") or []
                if isinstance(item, dict) and item.get("id") == managed_provider_id
            ),
            {},
        )
        managed_ref = str(managed_entry.get("credential_ref") or "")
        managed_row = next(
            (
                item
                for item in config_data.get("provider_credentials") or []
                if isinstance(item, dict) and item.get("id") == managed_ref
            ),
            None,
        )
        if not managed_ref or managed_row is None:
            violations.append("WebUI image set did not create an exclusive managed credential")
        else:
            for field in ("managed_by", "source_capability", "source_provider_id"):
                if not managed_row.get(field):
                    violations.append(
                        f"WebUI managed credential lacks {field} ownership metadata"
                    )
            if managed_row.get("source_provider_id") != managed_provider_id:
                violations.append(
                    "WebUI managed credential source_provider_id does not match its provider"
                )
            if managed_row.get("provider_family") != "custom":
                violations.append("WebUI managed credential has the wrong provider family")
            if managed_row.get("secret_env") != credential_secret_env(managed_ref):
                violations.append("WebUI managed credential has a non-canonical Secret env")
        model_config.delete_custom_image_provider_config(managed_provider_id)
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if managed_ref and any(
            isinstance(item, dict) and item.get("id") == managed_ref
            for item in config_data.get("provider_credentials") or []
        ):
            violations.append("WebUI image delete kept an exclusive managed credential")
        if managed_ref and os.getenv(credential_secret_env(managed_ref)):
            violations.append("WebUI image delete kept an exclusive managed Secret")
    except Exception as exc:
        violations.append(f"WebUI managed credential CRUD failed: {type(exc).__name__}")

    yaml_fault_root = tmp_path / "rollback-yaml"
    yaml_fault_root.mkdir()
    yaml_fault_ref = "rollback-yaml-ref"
    yaml_fault_env = credential_secret_env(yaml_fault_ref)
    yaml_fault_config = {
        "provider_credentials": [
            {
                "id": yaml_fault_ref,
                "provider_family": "custom",
                "auth_type": "api_key",
                "secret_env": yaml_fault_env,
            }
        ],
        "custom_image_providers": [],
    }
    yaml_fault_path = yaml_fault_root / "config.yaml"
    yaml_fault_path.write_bytes(
        yaml.safe_dump(
            yaml_fault_config,
            sort_keys=False,
            allow_unicode=True,
        ).encode("utf-8")
    )
    yaml_fault_env_path = yaml_fault_root / ".env"
    yaml_fault_env_path.write_text(
        f"{yaml_fault_env}=before-secret\n",
        encoding="utf-8",
    )
    with monkeypatch.context() as fault:
        _set_webui_home(fault, model_config, yaml_fault_root)
        fault.setenv(yaml_fault_env, "before-secret")
        fault.setenv(custom_image.custom_image_provider_env_var("rollback-yaml"), "")
        before_config_bytes = yaml_fault_path.read_bytes()
        before_env_bytes = yaml_fault_env_path.read_bytes()
        before_metadata = yaml.safe_load(before_config_bytes).get(
            "provider_credentials"
        )

        original_prepare = credential_store._prepare_pair_target

        def fail_config_stage(*, name: str, **kwargs: Any) -> Any:
            if name == "config":
                raise OSError("injected YAML write failure")
            return original_prepare(name=name, **kwargs)

        fault.setattr(credential_store, "_prepare_pair_target", fail_config_stage)
        error = _rejection(
            lambda: model_config.set_custom_image_provider_config(
                _valid_image_entry(
                    provider_id="rollback-yaml",
                    credential_ref=yaml_fault_ref,
                    api_key="after-secret",
                )
            )
        )
        after_config_bytes = yaml_fault_path.read_bytes()
        after_config = yaml.safe_load(after_config_bytes) or {}
        if error is None:
            violations.append("WebUI YAML write fault did not abort public SET")
        if after_config_bytes != before_config_bytes:
            violations.append("WebUI YAML write fault did not restore config bytes")
        if after_config.get("provider_credentials") != before_metadata:
            violations.append("WebUI YAML write fault did not restore credential metadata")
        if os.getenv(yaml_fault_env) != "before-secret":
            violations.append("WebUI YAML write fault did not restore process Secret")
        if yaml_fault_env_path.read_bytes() != before_env_bytes:
            violations.append("WebUI YAML write fault did not restore persisted Secret")

    env_fault_root = tmp_path / "rollback-env"
    env_fault_root.mkdir()
    env_fault_ref = "rollback-env-ref"
    env_fault_env = credential_secret_env(env_fault_ref)
    env_fault_config = {
        "provider_credentials": [
            {
                "id": env_fault_ref,
                "provider_family": "custom",
                "auth_type": "api_key",
                "secret_env": env_fault_env,
            }
        ],
        "custom_image_providers": [],
    }
    env_fault_path = env_fault_root / "config.yaml"
    env_fault_path.write_bytes(
        yaml.safe_dump(
            env_fault_config,
            sort_keys=False,
            allow_unicode=True,
        ).encode("utf-8")
    )
    env_fault_env_path = env_fault_root / ".env"
    env_fault_env_path.write_text(
        f"{env_fault_env}=before-secret\n",
        encoding="utf-8",
    )
    with monkeypatch.context() as fault:
        _set_webui_home(fault, model_config, env_fault_root)
        fault.setenv(env_fault_env, "before-secret")
        before_config_bytes = env_fault_path.read_bytes()
        before_env_bytes = env_fault_env_path.read_bytes()
        before_metadata = yaml.safe_load(before_config_bytes).get(
            "provider_credentials"
        )
        original_prepare = credential_store._prepare_pair_target

        def fail_env_stage(*, name: str, **kwargs: Any) -> Any:
            if name == "env":
                raise OSError("injected env write failure")
            return original_prepare(name=name, **kwargs)

        fault.setattr(credential_store, "_prepare_pair_target", fail_env_stage)
        error = _rejection(
            lambda: model_config.set_custom_image_provider_config(
                _valid_image_entry(
                    provider_id="rollback-env",
                    credential_ref=env_fault_ref,
                    api_key="after-secret",
                )
            )
        )
        after_config_bytes = env_fault_path.read_bytes()
        after_config = yaml.safe_load(after_config_bytes) or {}
        if error is None:
            violations.append("WebUI env write fault did not abort public SET")
        if after_config_bytes != before_config_bytes:
            violations.append("WebUI env write fault did not restore config bytes")
        if after_config.get("provider_credentials") != before_metadata:
            violations.append("WebUI env write fault did not restore credential metadata")
        if os.getenv(env_fault_env) != "before-secret":
            violations.append("WebUI env write fault did not restore process Secret")
        if env_fault_env_path.read_bytes() != before_env_bytes:
            violations.append("WebUI env write fault did not restore persisted Secret")

    _assert_security_contract(violations)


def test_endpoint_url_shape_is_fail_closed() -> None:
    from agent.custom_image_providers import normalize_custom_image_provider_entry
    from agent.custom_vision_providers import normalize_custom_vision_provider_entry

    violations: list[str] = []
    normalizers = (
        ("image", normalize_custom_image_provider_entry, _valid_image_entry),
        ("vision", normalize_custom_vision_provider_entry, _valid_vision_entry),
    )
    allowed = (
        "https://api.example.test/v1",
        "https://api.example.test/images/generations",
    )
    rejected = (
        "http://api.example.test/v1",
        "https://user:secret@api.example.test/v1",
        "https://api.example.test:99999/v1",
        "https://api.example.test/v1?next=https://evil.test",
        "https://api.example.test/v1#fragment",
        "https://api.example.test/v1/../admin",
        "https://api.example.test/v1/%2f..%2fadmin",
        "https://api.example.test/v1\\..\\admin",
        "https://api.example.test/%0d%0aX-Evil:1",
        "https://api.example.test\n.evil.test/v1",
    )
    for label, normalizer, factory in normalizers:
        for url in allowed:
            error = _rejection(lambda n=normalizer, f=factory, u=url: n(f(base_url=u)))
            if error is not None:
                violations.append(f"{label}: clean HTTPS endpoint rejected: {url}")
        for url in rejected:
            error = _rejection(lambda n=normalizer, f=factory, u=url: n(f(base_url=u)))
            if not isinstance(error, ValueError):
                violations.append(f"{label}: unsafe URL shape accepted: {url!r}")

    _assert_security_contract(violations)


def test_public_direct_pins_all_answers_peer_sni_and_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent import custom_image_providers as custom_image

    violations: list[str] = []
    credential_ref = "custom-public"
    _bind_custom_credential(
        monkeypatch,
        tmp_path,
        credential_ref=credential_ref,
        secret="public-secret",
    )
    bridge_calls: list[dict[str, Any]] = []
    legacy_calls: list[dict[str, Any]] = []

    def pinned_bridge(*args: Any, **kwargs: Any) -> _ProviderResponse:
        bridge_calls.append({"args": args, **kwargs})
        return _ProviderResponse(payload={"data": [{"b64_json": _PNG_1PX_B64}]})

    def legacy_post(url: str, **kwargs: Any) -> _ProviderResponse:
        legacy_calls.append({"url": url, **kwargs})
        return _ProviderResponse(payload={"data": [{"b64_json": _PNG_1PX_B64}]})

    monkeypatch.setattr(custom_image, "request_pinned_https", pinned_bridge, raising=False)
    monkeypatch.setattr(
        custom_image,
        "read_bounded_json",
        lambda response, **_kwargs: response._payload,
        raising=False,
    )
    monkeypatch.setattr(requests, "post", legacy_post)
    monkeypatch.setattr(
        custom_image, "save_b64_image", lambda *_args, **_kwargs: tmp_path / "public.png"
    )
    provider = custom_image.ConfigurableOpenAIImageProvider(
        _valid_image_entry(
            provider_id="public",
            credential_ref=credential_ref,
            network_scope="public_direct",
        )
    )
    provider.generate("public direct")

    if len(bridge_calls) != 1:
        violations.append("public_direct did not use exactly one pinned request bridge")
    if legacy_calls:
        violations.append("public_direct used legacy hostname-re-resolving requests.post")
    if bridge_calls:
        call = bridge_calls[0]
        rendered = json.dumps(call, default=str)
        if "https://images.example.test/v1/images/generations" not in rendered:
            violations.append("public_direct bridge did not receive the original endpoint URL")
        if call.get("network_scope") != "public_direct":
            violations.append("public_direct bridge received the wrong scope")
        if call.get("follow_redirects") is not False:
            violations.append("public_direct API POST did not disable redirects")

    # This initial consumer RED intentionally stops at the safe bridge.  Once
    # safe_outbound_http exists, a separate module-level RED file must exercise
    # both sync and async backends for all-answer validation, peer equality,
    # origin SNI and Host before the implementation can be considered GREEN.

    _assert_security_contract(violations)


def test_private_direct_requires_explicit_scope_and_keeps_permanent_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent.image_gen_provider import save_url_image

    violations: list[str] = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HERMES_IMAGE_ALLOW_PRIVATE_NETWORK", raising=False)
    network_calls: list[str] = []

    def request_get(url: str, **kwargs: Any) -> _ImageDownloadResponse:
        del kwargs
        network_calls.append(url)
        return _ImageDownloadResponse()

    private_cases = (
        ("rfc1918", "10.0.0.8"),
        ("loopback", "127.0.0.1"),
        ("ula", "fd00::8"),
    )
    signature = inspect.signature(save_url_image)
    supports_scope = "network_scope" in signature.parameters
    for label, address in private_cases:
        before = len(network_calls)
        error = _rejection(
            lambda a=address: save_url_image(
                "https://private.example.test/image.png",
                resolver=_public_resolver_for(a),
                request_get=request_get,
            )
        )
        if error is None or len(network_calls) != before:
            violations.append(f"{label}: private address worked without explicit scope")

        if supports_scope:
            before = len(network_calls)
            error = _rejection(
                lambda a=address: save_url_image(
                    "https://private.example.test/image.png",
                    resolver=_public_resolver_for(a),
                    request_get=request_get,
                    network_scope="private_direct",
                )
            )
            if error is not None or len(network_calls) != before + 1:
                violations.append(f"{label}: explicit private_direct did not allow target")

    permanent = (
        ("metadata", "169.254.169.254"),
        ("link-local-v6", "fe80::1"),
        ("unspecified", "0.0.0.0"),
        ("multicast", "224.0.0.1"),
        ("benchmark", "192.0.2.1"),
    )
    if not supports_scope:
        violations.append("save_url_image has no explicit network_scope contract")
    else:
        for label, address in permanent:
            before = len(network_calls)
            _rejection(
                lambda a=address: save_url_image(
                    "https://blocked.example.test/image.png",
                    resolver=_public_resolver_for(a),
                    request_get=request_get,
                    network_scope="private_direct",
                )
            )
            if len(network_calls) != before:
                violations.append(f"{label}: permanent block was relaxed by private_direct")

    _assert_security_contract(violations)


def test_trusted_proxy_uses_connect_origin_tls_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent import custom_image_providers as custom_image

    violations: list[str] = []
    credential_ref = "custom-proxy"
    _bind_custom_credential(
        monkeypatch,
        tmp_path,
        credential_ref=credential_ref,
        secret="proxy-secret",
    )
    proxy_calls: list[dict[str, Any]] = []
    direct_calls: list[dict[str, Any]] = []
    legacy_calls: list[dict[str, Any]] = []

    profiles = {
        "unapproved": None,
        "missing-capability": {
            "approved": True,
            "capabilities": ["public_egress"],
        },
        "approved": {
            "approved": True,
            "capabilities": ["public_egress", "dns_ip_classification"],
        },
        "policy-denied": {
            "approved": True,
            "capabilities": ["public_egress", "dns_ip_classification"],
        },
    }

    def resolve_profile(name: str) -> Any:
        return profiles.get(name)

    def proxy_bridge(*args: Any, **kwargs: Any) -> _ProviderResponse:
        proxy_calls.append({"args": args, **kwargs})
        profile = kwargs.get("trusted_proxy_profile") or kwargs.get("profile")
        if profile == "policy-denied":
            raise ValueError("trusted_proxy_origin_blocked")
        return _ProviderResponse(payload={"data": []})

    def direct_bridge(*args: Any, **kwargs: Any) -> _ProviderResponse:
        direct_calls.append({"args": args, **kwargs})
        return _ProviderResponse(payload={"data": []})

    def legacy_post(url: str, **kwargs: Any) -> _ProviderResponse:
        legacy_calls.append({"url": url, **kwargs})
        return _ProviderResponse(payload={"data": []})

    monkeypatch.setattr(
        custom_image, "resolve_trusted_proxy_profile", resolve_profile, raising=False
    )
    monkeypatch.setattr(
        custom_image, "request_via_trusted_proxy", proxy_bridge, raising=False
    )
    monkeypatch.setattr(
        custom_image, "request_pinned_https", direct_bridge, raising=False
    )
    monkeypatch.setattr(
        custom_image,
        "read_bounded_json",
        lambda response, *_args, **_kwargs: response._payload,
        raising=False,
    )
    monkeypatch.setattr(requests, "post", legacy_post)

    outcomes: dict[str, Any] = {}
    for profile_name in profiles:
        provider = custom_image.ConfigurableOpenAIImageProvider(
            _valid_image_entry(
                provider_id="proxy",
                credential_ref=credential_ref,
                network_scope="trusted_proxy",
                trusted_proxy_profile=profile_name,
            )
        )
        try:
            outcomes[profile_name] = provider.generate(f"proxy {profile_name}")
        except Exception as exc:
            outcomes[profile_name] = exc

    if legacy_calls:
        violations.append("trusted_proxy fell back to ambient/legacy direct requests")
    if direct_calls:
        violations.append("trusted_proxy fell back to public/private direct transport")
    approved_calls = [
        call
        for call in proxy_calls
        if (call.get("trusted_proxy_profile") or call.get("profile")) == "approved"
    ]
    if len(approved_calls) != 1:
        violations.append("approved named proxy profile did not call the proxy bridge once")
    if approved_calls:
        call = approved_calls[0]
        if "https://images.example.test/v1/images/generations" not in json.dumps(
            call,
            default=str,
        ):
            violations.append("trusted proxy bridge did not receive the original URL")
    for unavailable in ("unapproved", "missing-capability"):
        unavailable_calls = [
            call
            for call in proxy_calls
            if (call.get("trusted_proxy_profile") or call.get("profile")) == unavailable
        ]
        if unavailable_calls:
            violations.append(f"{unavailable}: proxy bridge was called")
        result = outcomes[unavailable]
        text = json.dumps(result, default=str, ensure_ascii=False)
        if "trusted_proxy_unavailable" not in text:
            violations.append(f"{unavailable}: missing stable unavailable reason")
    denied_calls = [
        call
        for call in proxy_calls
        if (call.get("trusted_proxy_profile") or call.get("profile"))
        == "policy-denied"
    ]
    if len(denied_calls) != 1:
        violations.append("proxy remote-policy denial did not call its bridge once")
    denied_text = json.dumps(outcomes["policy-denied"], default=str, ensure_ascii=False)
    if "trusted_proxy_origin_blocked" not in denied_text:
        violations.append("proxy-side DNS/IP denial was not mapped to stable origin-blocked reason")

    # Actual CONNECT target and tunnel TLS behavior belong to the second-stage
    # safe_outbound_http module-level RED, not this consumer-to-bridge contract.
    _assert_security_contract(violations)


def test_network_scopes_block_metadata_link_local_and_mapped_variants(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent import custom_image_providers as custom_image
    from agent.image_gen_provider import save_url_image

    violations: list[str] = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HERMES_IMAGE_ALLOW_PRIVATE_NETWORK", raising=False)
    blocked_addresses = (
        "169.254.169.254",
        "169.254.1.1",
        "0.0.0.0",
        "::",
        "224.0.0.1",
        "ff02::1",
        "192.0.2.1",
        "::ffff:169.254.169.254",
        "::ffff:127.0.0.1",
    )
    direct_network_calls: list[str] = []

    def request_get(url: str, **kwargs: Any) -> _ImageDownloadResponse:
        del kwargs
        direct_network_calls.append(url)
        return _ImageDownloadResponse(status=403)

    signature = inspect.signature(save_url_image)
    if "network_scope" not in signature.parameters:
        violations.append("save_url_image cannot enforce permanent blocks per network scope")
    else:
        for scope in ("public_direct", "private_direct"):
            for address in blocked_addresses:
                before = len(direct_network_calls)
                _rejection(
                    lambda a=address, selected_scope=scope: save_url_image(
                        "https://blocked.example.test/image.png",
                        resolver=_public_resolver_for(a),
                        request_get=request_get,
                        network_scope=selected_scope,
                    )
                )
                if len(direct_network_calls) != before:
                    violations.append(
                        f"{scope}: permanent network class reached HTTP: {address}"
                    )

    credential_ref = "custom-permanent-scope"
    _bind_custom_credential(
        monkeypatch,
        tmp_path,
        credential_ref=credential_ref,
        secret="permanent-secret",
    )
    proxy_calls: list[dict[str, Any]] = []
    direct_bridge_calls: list[dict[str, Any]] = []
    legacy_calls: list[dict[str, Any]] = []

    def proxy_bridge(*args: Any, **kwargs: Any) -> _ProviderResponse:
        call = {"args": args, **kwargs}
        proxy_calls.append(call)
        if "remote-policy.example.test" in json.dumps(call, default=str):
            raise ValueError("trusted_proxy_origin_blocked")
        return _ProviderResponse(payload={"data": []})

    def direct_bridge(*args: Any, **kwargs: Any) -> _ProviderResponse:
        direct_bridge_calls.append({"args": args, **kwargs})
        return _ProviderResponse(payload={"data": []})

    def legacy_post(url: str, **kwargs: Any) -> _ProviderResponse:
        legacy_calls.append({"url": url, **kwargs})
        return _ProviderResponse(payload={"data": []})

    monkeypatch.setattr(
        custom_image,
        "resolve_trusted_proxy_profile",
        lambda name: (
            {
                "name": name,
                "approved": True,
                "capabilities": ["public_egress", "dns_ip_classification"],
            }
            if name == "approved-policy-test"
            else None
        ),
        raising=False,
    )
    monkeypatch.setattr(
        custom_image, "request_via_trusted_proxy", proxy_bridge, raising=False
    )
    monkeypatch.setattr(
        custom_image, "request_pinned_https", direct_bridge, raising=False
    )
    monkeypatch.setattr(
        custom_image,
        "read_bounded_json",
        lambda response, *_args, **_kwargs: response._payload,
        raising=False,
    )
    monkeypatch.setattr(requests, "post", legacy_post)

    for index, address in enumerate(blocked_addresses):
        literal_host = f"[{address}]" if ":" in address else address
        before = len(proxy_calls)
        entry = _valid_image_entry(
                provider_id=f"permanent-{index}",
                credential_ref=credential_ref,
                base_url=f"https://{literal_host}/v1",
                network_scope="trusted_proxy",
                trusted_proxy_profile="approved-policy-test",
        )
        _rejection(
            lambda selected=entry: custom_image.ConfigurableOpenAIImageProvider(
                selected
            ).generate("blocked literal")
        )
        if len(proxy_calls) != before:
            violations.append(
                f"trusted_proxy: permanent literal reached proxy bridge: {address}"
            )

    remote_policy_provider = custom_image.ConfigurableOpenAIImageProvider(
        _valid_image_entry(
            provider_id="remote-policy",
            credential_ref=credential_ref,
            base_url="https://remote-policy.example.test/v1",
            network_scope="trusted_proxy",
            trusted_proxy_profile="approved-policy-test",
        )
    )
    try:
        remote_result: Any = remote_policy_provider.generate("remote policy")
    except Exception as exc:
        remote_result = exc
    remote_calls = [
        call
        for call in proxy_calls
        if "remote-policy.example.test" in json.dumps(call, default=str)
    ]
    if len(remote_calls) != 1:
        violations.append("trusted_proxy remote DNS policy was not consulted exactly once")
    if "trusted_proxy_origin_blocked" not in json.dumps(
        remote_result,
        default=str,
    ):
        violations.append("trusted_proxy remote DNS policy lost its stable rejection")
    if direct_bridge_calls or legacy_calls:
        violations.append("trusted_proxy permanent-policy path fell back to direct transport")

    _assert_security_contract(violations)


def test_fake_ip_range_is_never_connected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent import custom_image_providers as custom_image
    from agent.image_gen_provider import save_url_image

    violations: list[str] = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HERMES_IMAGE_ALLOW_PRIVATE_NETWORK", raising=False)
    image_calls: list[str] = []

    def image_get(url: str, **kwargs: Any) -> _ImageDownloadResponse:
        del kwargs
        image_calls.append(url)
        return _ImageDownloadResponse(status=403)

    signature = inspect.signature(save_url_image)
    for label, url, address in (
        ("literal", "https://198.18.0.1/image.png", "198.18.0.1"),
        ("dns-answer", "https://fake-ip.example.test/image.png", "198.19.255.254"),
        ("mapped", "https://fake-ip.example.test/image.png", "::ffff:198.18.0.1"),
    ):
        scopes = ("public_direct", "private_direct") if "network_scope" in signature.parameters else (None,)
        for scope in scopes:
            before = len(image_calls)
            kwargs = {"network_scope": scope} if scope else {}
            _rejection(
                lambda u=url, a=address, options=kwargs: save_url_image(
                    u,
                    resolver=_public_resolver_for(a),
                    request_get=image_get,
                    **options,
                )
            )
            if len(image_calls) != before:
                violations.append(f"Fake-IP {label}/{scope or 'default'} reached transport")

    credential_ref = "custom-fake"
    _bind_custom_credential(
        monkeypatch,
        tmp_path,
        credential_ref=credential_ref,
        secret="fake-secret",
    )
    proxy_calls: list[dict[str, Any]] = []
    direct_calls: list[dict[str, Any]] = []
    legacy_calls: list[dict[str, Any]] = []

    def proxy_bridge(*args: Any, **kwargs: Any) -> _ProviderResponse:
        proxy_calls.append({"args": args, **kwargs})
        raise ValueError("trusted_proxy_origin_blocked")

    def legacy_post(url: str, **kwargs: Any) -> _ProviderResponse:
        legacy_calls.append({"url": url, **kwargs})
        return _ProviderResponse(payload={"data": []})

    monkeypatch.setattr(
        custom_image,
        "resolve_trusted_proxy_profile",
        lambda name: (
            {
                "name": name,
                "approved": True,
                "capabilities": ["public_egress", "dns_ip_classification"],
            }
            if name == "fake-ip-policy-test"
            else None
        ),
        raising=False,
    )
    monkeypatch.setattr(
        custom_image, "request_via_trusted_proxy", proxy_bridge, raising=False
    )
    monkeypatch.setattr(
        custom_image,
        "request_pinned_https",
        lambda *args, **kwargs: direct_calls.append({"args": args, **kwargs}),
        raising=False,
    )
    monkeypatch.setattr(requests, "post", legacy_post)
    provider = custom_image.ConfigurableOpenAIImageProvider(
        _valid_image_entry(
            provider_id="fake",
            credential_ref=credential_ref,
            network_scope="trusted_proxy",
            trusted_proxy_profile="fake-ip-policy-test",
        )
    )
    result = provider.generate("fake ip")
    if legacy_calls:
        violations.append("Fake-IP provider path bypassed proxy policy via direct request")
    if direct_calls:
        violations.append("Fake-IP trusted-proxy denial fell back to direct transport")
    if len(proxy_calls) != 1:
        violations.append("Fake-IP trusted-proxy path did not consult proxy policy exactly once")
    if "trusted_proxy_origin_blocked" not in json.dumps(result, default=str):
        violations.append("Fake-IP proxy denial was not mapped to stable reason")

    _assert_security_contract(violations)


def test_custom_vision_sync_and_async_resist_dns_rebinding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent import auxiliary_client
    from agent import custom_vision_providers as custom_vision
    import httpx
    import openai

    violations: list[str] = []
    events: list[str] = []
    sync_transports: list[Any] = []
    async_transports: list[Any] = []
    openai_clients: list[_ClientCapture] = []
    httpx_clients: list[_HTTPXCapture] = []

    credential_ref = "custom-vision"
    _bind_custom_credential(
        monkeypatch,
        tmp_path,
        credential_ref=credential_ref,
        secret="vision-secret",
    )
    sync_sentinel = object()
    async_sentinel = object()

    def build_sync(*args: Any, **kwargs: Any) -> object:
        del args, kwargs
        events.append("sync-build")
        sync_transports.append(sync_sentinel)
        return sync_sentinel

    def build_async(*args: Any, **kwargs: Any) -> object:
        del args, kwargs
        events.append("async-build")
        async_transports.append(async_sentinel)
        return async_sentinel

    def sync_openai_factory(**kwargs: Any) -> _ClientCapture:
        events.append("sync-openai")
        client = _ClientCapture(**kwargs)
        openai_clients.append(client)
        return client

    def async_openai_factory(**kwargs: Any) -> _ClientCapture:
        events.append("async-openai")
        client = _ClientCapture(**kwargs)
        openai_clients.append(client)
        return client

    def sync_httpx_factory(**kwargs: Any) -> _HTTPXCapture:
        events.append("sync-httpx")
        client = _HTTPXCapture(kind="sync", events=events, **kwargs)
        httpx_clients.append(client)
        return client

    def async_httpx_factory(**kwargs: Any) -> _HTTPXCapture:
        events.append("async-httpx")
        client = _HTTPXCapture(kind="async", events=events, **kwargs)
        httpx_clients.append(client)
        return client

    monkeypatch.setattr(
        auxiliary_client, "build_openai_sync_transport", build_sync, raising=False
    )
    monkeypatch.setattr(
        auxiliary_client, "build_openai_async_transport", build_async, raising=False
    )
    monkeypatch.setattr(auxiliary_client, "OpenAI", sync_openai_factory)
    monkeypatch.setattr(openai, "AsyncOpenAI", async_openai_factory)
    monkeypatch.setattr(httpx, "Client", sync_httpx_factory)
    monkeypatch.setattr(httpx, "AsyncClient", async_httpx_factory)
    monkeypatch.setattr(
        auxiliary_client,
        "_resolve_task_provider_model",
        lambda *_args, **_kwargs: (
            "custom:router",
            "vision-model",
            "https://vision.example.test/v1",
            "vision-secret",
            "chat_completions",
        ),
    )
    monkeypatch.setattr(
        auxiliary_client, "_register_transient_named_vision_client", lambda _client: None
    )
    monkeypatch.setattr(
        custom_vision,
        "find_custom_vision_provider_entry",
        lambda *_args, **_kwargs: _valid_vision_entry(
            credential_ref=credential_ref
        ),
    )

    for async_mode in (False, True):
        error = _rejection(
            lambda mode=async_mode: auxiliary_client.resolve_vision_provider_client(
                provider="custom:router",
                model="vision-model",
                async_mode=mode,
            )
        )
        if error is not None:
            violations.append(
                f"{'async' if async_mode else 'sync'} vision resolver failed: "
                f"{type(error).__name__}"
            )

    if len(sync_transports) != 1 or len(async_transports) != 1:
        violations.append("sync/async custom vision did not invoke both transport builders")
    if len(httpx_clients) != 2:
        violations.append("sync/async custom vision did not construct two hardened httpx clients")
    else:
        expected = (sync_sentinel, async_sentinel)
        for client, transport in zip(httpx_clients, expected):
            if client.kwargs.get("transport") is not transport:
                violations.append("httpx client did not wrap the pinned transport")
            if client.kwargs.get("trust_env") is not False:
                violations.append("custom vision httpx client inherited ambient proxy env")
            if client.kwargs.get("follow_redirects") is not False:
                violations.append("custom vision httpx client enabled redirects")
    if len(openai_clients) != 2:
        violations.append("sync/async custom vision did not construct exactly two SDK clients")
    elif len(httpx_clients) == 2:
        for sdk_client, http_client in zip(openai_clients, httpx_clients):
            if sdk_client.kwargs.get("http_client") is not http_client:
                violations.append("OpenAI SDK received the transport instead of hardened client")
    expected_order = [
        "sync-build",
        "sync-httpx",
        "sync-openai",
        "async-build",
        "async-httpx",
        "async-openai",
    ]
    observed = [event for event in events if not event.endswith(("close", "aclose"))]
    if observed[:6] != expected_order:
        violations.append("vision attached credentials before building hardened transport/client")
    for client in httpx_clients:
        if client.kind == "sync":
            client.close()
        else:
            asyncio.run(client.aclose())

    # This node proves consumer wiring only: both builders must feed hardened
    # httpx Client wrappers, and the SDK must receive those wrappers.  Actual
    # sync/async DNS-rebinding and peer-equality behavior requires a separate
    # second-stage safe_outbound_http module-level RED before GREEN completion.
    _assert_security_contract(violations)


def test_custom_image_post_is_pinned_and_never_redirects_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent import custom_image_providers as custom_image

    violations: list[str] = []
    credential_ref = "custom-post"
    _bind_custom_credential(
        monkeypatch,
        tmp_path,
        credential_ref=credential_ref,
        secret="post-secret",
    )
    bridge_calls: list[dict[str, Any]] = []
    legacy_calls: list[dict[str, Any]] = []

    def bridge_url(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        candidate = kwargs.get("url")
        if candidate is None and args:
            candidate = args[0]
        return str(candidate or "")

    def pinned_bridge(*args: Any, **kwargs: Any) -> _ProviderResponse:
        call = {"args": args, **kwargs}
        bridge_calls.append(call)
        if "redirect.example.test" in bridge_url(args, kwargs):
            return _ProviderResponse(
                status=302,
                headers={
                    "Content-Type": "application/json",
                    "Location": "https://evil.example.test/steal",
                },
                payload={"data": []},
            )
        return _ProviderResponse(payload={"data": [{"b64_json": _PNG_1PX_B64}]})

    def legacy_post(url: str, **kwargs: Any) -> _ProviderResponse:
        legacy_calls.append({"url": url, **kwargs})
        if "redirect.example.test" in url:
            return _ProviderResponse(
                status=302,
                headers={
                    "Content-Type": "application/json",
                    "Location": "https://evil.example.test/steal",
                },
                payload={"data": []},
            )
        return _ProviderResponse(payload={"data": [{"b64_json": _PNG_1PX_B64}]})

    monkeypatch.setattr(custom_image, "request_pinned_https", pinned_bridge, raising=False)
    monkeypatch.setattr(requests, "post", legacy_post)
    monkeypatch.setattr(
        custom_image,
        "read_bounded_json",
        lambda response, *_args, **_kwargs: response._payload,
        raising=False,
    )
    monkeypatch.setattr(
        custom_image, "save_b64_image", lambda *_args, **_kwargs: tmp_path / "post.png"
    )
    for label, base_url in (
        ("safe", "https://public.example.test/v1"),
        ("redirect", "https://redirect.example.test/v1"),
    ):
        provider = custom_image.ConfigurableOpenAIImageProvider(
            _valid_image_entry(
                provider_id="post",
                credential_ref=credential_ref,
                base_url=base_url,
                network_scope="public_direct",
            )
        )
        provider.generate(label)

    if legacy_calls:
        violations.append("custom image POST bypassed pinned transport")
    if len(bridge_calls) != 2:
        violations.append("safe and redirect origins did not each use the pinned bridge once")
    for call in bridge_calls:
        if call.get("follow_redirects") is not False:
            violations.append("custom image POST enabled redirects")
        headers = call.get("headers") or {}
        if "Authorization" not in headers:
            violations.append("safe custom image POST lost Authorization after pin decision")
        if "evil.example.test" in bridge_url(call.get("args", ()), call):
            violations.append("Authorization was forwarded to a cross-origin redirect")
    if any("evil.example.test" in call.get("url", "") for call in bridge_calls + legacy_calls):
        violations.append("302 response was followed to the evil origin")
    if any(call.get("headers", {}).get("Authorization") for call in legacy_calls):
        violations.append("legacy path attached Authorization before safe routing decision")

    _assert_security_contract(violations)


def test_image_download_propagates_network_scope_on_every_hop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent import custom_image_providers as custom_image
    from agent import image_gen_provider

    violations: list[str] = []
    credential_ref = "custom-scope"
    _bind_custom_credential(
        monkeypatch,
        tmp_path,
        credential_ref=credential_ref,
        secret="scope-secret",
    )
    scope_calls: list[dict[str, Any]] = []

    def api_response(*args: Any, **kwargs: Any) -> _ProviderResponse:
        del args, kwargs
        return _ProviderResponse(
            payload={
                "data": [
                    {"url": "https://cdn.example.test/start.png"}
                ]
            }
        )

    def scoped_downloader(url: str, *args: Any, **kwargs: Any) -> Path:
        del args
        scope_calls.append({"url": url, **kwargs})
        return tmp_path / "downloaded.png"

    monkeypatch.setattr(requests, "post", api_response)
    monkeypatch.setattr(custom_image, "request_via_trusted_proxy", api_response, raising=False)
    monkeypatch.setattr(
        custom_image,
        "resolve_trusted_proxy_profile",
        lambda name: {
            "name": name,
            "approved": True,
            "capabilities": ["public_egress", "dns_ip_classification"],
        },
        raising=False,
    )
    monkeypatch.setattr(
        custom_image,
        "read_bounded_json",
        lambda response, *_args, **_kwargs: response._payload,
        raising=False,
    )
    monkeypatch.setattr(custom_image, "save_url_image", scoped_downloader)
    provider = custom_image.ConfigurableOpenAIImageProvider(
        _valid_image_entry(
            provider_id="scope",
            credential_ref=credential_ref,
            response_format="url",
            network_scope="trusted_proxy",
            trusted_proxy_profile="approved-download",
        )
    )
    error = _rejection(lambda: provider.generate("scoped redirect"))
    if error is not None:
        violations.append(f"provider URL result failed: {type(error).__name__}")
    if len(scope_calls) != 1:
        violations.append("provider did not invoke downloader exactly once")
    elif (
        scope_calls[0].get("network_scope") != "trusted_proxy"
        or scope_calls[0].get("trusted_proxy_profile") != "approved-download"
    ):
        violations.append("provider did not pass scope/profile once to downloader")

    save_signature = inspect.signature(image_gen_provider.save_url_image)
    required = {"network_scope", "trusted_proxy_profile"}
    if not required <= set(save_signature.parameters):
        violations.append("save_url_image lacks scope/profile propagation contract")
    else:
        hop_calls: list[dict[str, Any]] = []

        def pinned_hop(url: str, **kwargs: Any) -> tuple[int, dict[str, str], bytes]:
            hop_calls.append({"url": url, **kwargs})
            if len(hop_calls) == 1:
                return 302, {"location": "/final.png"}, b""
            return 200, {"content-type": "image/png"}, _PNG_1PX

        monkeypatch.setattr(image_gen_provider, "_pinned_http_get", pinned_hop)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "download-home"))
        error = _rejection(
            lambda: image_gen_provider.save_url_image(
                "https://cdn.example.test/start.png",
                network_scope="trusted_proxy",
                trusted_proxy_profile="approved-download",
            )
        )
        if error is not None:
            violations.append(f"real downloader redirect path failed: {type(error).__name__}")
        if len(hop_calls) != 2:
            violations.append("real downloader did not execute exactly two redirect hops")
        for call in hop_calls:
            if (
                call.get("network_scope") != "trusted_proxy"
                or call.get("trusted_proxy_profile") != "approved-download"
            ):
                violations.append("real downloader changed scope/profile across redirect hop")

    _assert_security_contract(violations)


def test_provider_json_is_mime_checked_and_bounded_before_parse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent import custom_image_providers as custom_image

    violations: list[str] = []
    credential_ref = "custom-json"
    _bind_custom_credential(
        monkeypatch,
        tmp_path,
        credential_ref=credential_ref,
        secret="json-secret",
    )
    bridge_calls: list[dict[str, Any]] = []
    bounded_calls: list[Any] = []
    legacy_calls: list[dict[str, Any]] = []
    events: list[str] = []
    pinned_response = _ProviderResponse(payload={"data": []})

    def bridge(*args: Any, **kwargs: Any) -> _ProviderResponse:
        events.append("pinned-bridge")
        bridge_calls.append({"args": args, **kwargs})
        return pinned_response

    def legacy_post(url: str, **kwargs: Any) -> _ProviderResponse:
        legacy_calls.append({"url": url, **kwargs})
        return _ProviderResponse(payload={"data": []})

    def provider_bounded_json(response: Any, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        events.append("bounded-json")
        bounded_calls.append(response)
        return response._payload

    monkeypatch.setattr(custom_image, "request_pinned_https", bridge, raising=False)
    monkeypatch.setattr(requests, "post", legacy_post)
    monkeypatch.setattr(
        custom_image, "read_bounded_json", provider_bounded_json, raising=False
    )
    provider = custom_image.ConfigurableOpenAIImageProvider(
        _valid_image_entry(
            provider_id="json",
            credential_ref=credential_ref,
            network_scope="public_direct",
        )
    )
    provider.generate("bounded provider response")
    if len(bridge_calls) != 1:
        violations.append("provider JSON response did not arrive through pinned bridge")
    if len(bounded_calls) != 1:
        violations.append("provider did not hand the pinned response to bounded JSON once")
    elif bounded_calls[0] is not pinned_response:
        violations.append("provider bounded JSON seam received a different response object")
    if legacy_calls:
        violations.append("provider JSON path used legacy unbounded requests.post")
    if events != ["pinned-bridge", "bounded-json"]:
        violations.append("provider did not run pinned bridge before bounded JSON seam")

    _assert_security_contract(violations)
