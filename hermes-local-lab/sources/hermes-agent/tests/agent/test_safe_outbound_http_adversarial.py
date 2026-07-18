"""Adversarial boundaries beyond the primary safe transport contract."""

from __future__ import annotations

import asyncio
import gzip
import socket
from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio
import httpcore
import httpx
import pytest

import agent.safe_outbound_http as safe_http


def _approved_profile() -> safe_http.TrustedProxyProfile:
    return safe_http.TrustedProxyProfile(
        name="approved",
        proxy_url="https://approved-proxy.example:8443",
        approved=True,
        capabilities=frozenset({"public_egress", "dns_ip_classification"}),
    )


def _assert_proxy_profile_rejected(
    profile: safe_http.TrustedProxyProfile,
) -> None:
    with pytest.raises(safe_http.SafeOutboundError) as captured:
        safe_http.resolve_trusted_proxy_profile(
            "approved",
            profiles={"approved": profile},
        )
    assert captured.value.reason_code == "trusted_proxy_unavailable"


def test_proxy_profile_rejects_ambiguous_authorization_and_url_shapes() -> None:
    valid = _approved_profile()
    assert (
        safe_http.resolve_trusted_proxy_profile(
            "approved",
            profiles={"approved": valid},
        )
        is valid
    )

    invalid_profiles = (
        replace(valid, approved=1),
        replace(valid, proxy_connect_scope=None),
        replace(
            valid,
            proxy_url="https://approved-proxy.example。evil:8443",
        ),
        replace(
            valid,
            proxy_url="https://approved_proxy.example:8443",
        ),
        replace(
            valid,
            proxy_url="https://approved-proxy.example:8443/\t",
        ),
        replace(valid, proxy_url="https://approved-proxy.example:0"),
        replace(
            valid,
            proxy_url="https://[2606:4700:4700::1111%25en0]:8443",
        ),
        replace(valid, proxy_url="https://127.1:8443"),
        replace(valid, proxy_url="https://metadata.google.internal:8443"),
        replace(valid, credential_env=False),
        replace(valid, credential_env=0),
        replace(valid, policy_denial_reason="DENIED\r\nX-Evil: 1"),
    )
    for profile in invalid_profiles:
        _assert_proxy_profile_rejected(profile)


def test_named_proxy_profile_loads_from_canonical_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
trusted_proxy_profiles:
  - name: approved-runtime
    proxy_url: https://approved-proxy.example:8443
    approved: true
    capabilities:
      - public_egress
      - dns_ip_classification
    proxy_connect_scope: public_direct
    policy_denial_status: 451
    policy_denial_reason: TAIJI_ORIGIN_BLOCKED
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))

    profile = safe_http.resolve_trusted_proxy_profile("approved-runtime")
    assert profile.name == "approved-runtime"
    assert profile.proxy_url == "https://approved-proxy.example:8443"
    assert profile.proxy_connect_scope is safe_http.NetworkScope.PUBLIC_DIRECT
    assert profile.capabilities == frozenset({"public_egress", "dns_ip_classification"})

    captured: list[safe_http.TrustedProxyProfile | None] = []
    sentinel = object()

    def builder(
        *,
        backend: httpcore.NetworkBackend,
        proxy_profile: safe_http.TrustedProxyProfile | None = None,
    ) -> object:
        del backend
        captured.append(proxy_profile)
        return sentinel

    monkeypatch.setattr(safe_http, "_build_sync_transport", builder)
    transport = safe_http.build_openai_sync_transport(
        network_scope=safe_http.NetworkScope.TRUSTED_PROXY,
        trusted_proxy_profile="approved-runtime",
    )
    assert transport is sentinel
    assert captured == [profile]


@pytest.mark.parametrize(
    ("profile_name", "config_body"),
    (
        ("missing", "[]\n"),
        ("missing", "trusted_proxy_profiles: {}\n"),
        (
            "duplicate",
            """
trusted_proxy_profiles:
  - &profile
    name: duplicate
    proxy_url: https://approved-proxy.example:8443
    approved: true
    capabilities: [public_egress, dns_ip_classification]
    proxy_connect_scope: public_direct
  - *profile
""",
        ),
        (
            "wrong-approved-type",
            """
trusted_proxy_profiles:
  - name: wrong-approved-type
    proxy_url: https://approved-proxy.example:8443
    approved: 1
    capabilities: [public_egress, dns_ip_classification]
    proxy_connect_scope: public_direct
""",
        ),
        (
            "wrong-status-type",
            """
trusted_proxy_profiles:
  - name: wrong-status-type
    proxy_url: https://approved-proxy.example:8443
    approved: true
    capabilities: [public_egress, dns_ip_classification]
    proxy_connect_scope: public_direct
    policy_denial_status: true
""",
        ),
        (
            "duplicate-capability",
            """
trusted_proxy_profiles:
  - name: duplicate-capability
    proxy_url: https://approved-proxy.example:8443
    approved: true
    capabilities:
      - public_egress
      - public_egress
      - dns_ip_classification
    proxy_connect_scope: public_direct
""",
        ),
        (
            "credential-bypass",
            """
trusted_proxy_profiles:
  - name: credential-bypass
    proxy_url: https://approved-proxy.example:8443
    approved: true
    capabilities: [public_egress, dns_ip_classification]
    proxy_connect_scope: public_direct
    credential_env: ATTACKER_PROXY_AUTH
""",
        ),
        (
            "unknown-field",
            """
trusted_proxy_profiles:
  - name: unknown-field
    proxy_url: https://approved-proxy.example:8443
    approved: true
    capabilities: [public_egress, dns_ip_classification]
    proxy_connect_scope: public_direct
    fallback_proxy_url: https://evil.example
""",
        ),
        (
            "valid-before-invalid",
            """
trusted_proxy_profiles:
  - name: valid-before-invalid
    proxy_url: https://approved-proxy.example:8443
    approved: true
    capabilities: [public_egress, dns_ip_classification]
    proxy_connect_scope: public_direct
  - name: invalid-unselected
    proxy_url: https://user:secret@evil.example:8443
    approved: true
    capabilities: [public_egress, dns_ip_classification]
    proxy_connect_scope: public_direct
""",
        ),
    ),
)
def test_named_proxy_registry_rejects_ambiguous_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    profile_name: str,
    config_body: str,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_body.lstrip(), encoding="utf-8")
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    with pytest.raises(safe_http.SafeOutboundError) as captured:
        safe_http.resolve_trusted_proxy_profile(profile_name)
    assert captured.value.reason_code == "trusted_proxy_unavailable"


def test_explicit_proxy_profile_map_does_not_read_canonical_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("trusted_proxy_profiles: {}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    profile = _approved_profile()
    assert (
        safe_http.resolve_trusted_proxy_profile(
            "approved",
            profiles={"approved": profile},
        )
        is profile
    )


def test_named_proxy_registry_config_failures_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    marker_path = tmp_path / "unsafe-constructor-ran"
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    payloads = (
        b"trusted_proxy_profiles: [\n",
        b"trusted_proxy_profiles: []\n" + (b"#" * (2 * 1024 * 1024)),
        (
            b"trusted_proxy_profiles: "
            b"!!python/object/apply:pathlib.Path.touch "
            + repr(str(marker_path)).encode("utf-8")
            + b"\n"
        ),
        b"{}\n",
    )
    for payload in payloads:
        config_path.write_bytes(payload)
        with pytest.raises(safe_http.SafeOutboundError) as captured:
            safe_http.resolve_trusted_proxy_profile("missing")
        assert captured.value.reason_code == "trusted_proxy_unavailable"
        assert not marker_path.exists()

    config_path.unlink()
    with pytest.raises(safe_http.SafeOutboundError) as missing:
        safe_http.resolve_trusted_proxy_profile("missing")
    assert missing.value.reason_code == "trusted_proxy_unavailable"


def test_named_proxy_registry_uses_canonical_runtime_and_context_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from hermes_constants import (
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    def write_profile(home: Path, profile_name: str) -> None:
        home.mkdir()
        (home / "config.yaml").write_text(
            f"""
trusted_proxy_profiles:
  - name: {profile_name}
    proxy_url: https://approved-proxy.example:8443
    approved: true
    capabilities: [public_egress, dns_ip_classification]
    proxy_connect_scope: public_direct
""".lstrip(),
            encoding="utf-8",
        )

    runtime_home = tmp_path / "runtime-home"
    context_home = tmp_path / "context-home"
    write_profile(runtime_home, "runtime-approved")
    write_profile(context_home, "context-approved")
    monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))

    runtime_profile = safe_http.resolve_trusted_proxy_profile("runtime-approved")
    assert runtime_profile.name == "runtime-approved"

    token = set_hermes_home_override(context_home)
    try:
        context_profile = safe_http.resolve_trusted_proxy_profile("context-approved")
    finally:
        reset_hermes_home_override(token)
    assert context_profile.name == "context-approved"


def test_named_proxy_registry_runtime_and_context_override_legacy_config_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from hermes_constants import (
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    def write_profile(home: Path, profile_name: str) -> Path:
        home.mkdir()
        config_path = home / "config.yaml"
        config_path.write_text(
            f"""
trusted_proxy_profiles:
  - name: {profile_name}
    proxy_url: https://approved-proxy.example:8443
    approved: true
    capabilities: [public_egress, dns_ip_classification]
    proxy_connect_scope: public_direct
""".lstrip(),
            encoding="utf-8",
        )
        return config_path

    legacy_path = write_profile(tmp_path / "legacy-home", "legacy-approved")
    runtime_home = tmp_path / "runtime-home"
    context_home = tmp_path / "context-home"
    write_profile(runtime_home, "runtime-approved")
    write_profile(context_home, "context-approved")
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(legacy_path))
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))

    assert (
        safe_http.resolve_trusted_proxy_profile("runtime-approved").name
        == "runtime-approved"
    )
    with pytest.raises(safe_http.SafeOutboundError):
        safe_http.resolve_trusted_proxy_profile("legacy-approved")

    token = set_hermes_home_override(context_home)
    try:
        assert (
            safe_http.resolve_trusted_proxy_profile("context-approved").name
            == "context-approved"
        )
        with pytest.raises(safe_http.SafeOutboundError):
            safe_http.resolve_trusted_proxy_profile("legacy-approved")
    finally:
        reset_hermes_home_override(token)


@pytest.mark.parametrize(
    "config_body",
    (
        """
trusted_proxy_profiles:
  - name: duplicate-approved-key
    proxy_url: https://approved-proxy.example:8443
    approved: false
    approved: true
    capabilities: [public_egress, dns_ip_classification]
    proxy_connect_scope: public_direct
""",
        """
trusted_proxy_profiles: []
trusted_proxy_profiles:
  - name: duplicate-approved-key
    proxy_url: https://approved-proxy.example:8443
    approved: true
    capabilities: [public_egress, dns_ip_classification]
    proxy_connect_scope: public_direct
""",
    ),
)
def test_named_proxy_registry_rejects_duplicate_yaml_mapping_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_body: str,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_body.lstrip(), encoding="utf-8")
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))
    with pytest.raises(safe_http.SafeOutboundError) as captured:
        safe_http.resolve_trusted_proxy_profile("duplicate-approved-key")
    assert captured.value.reason_code == "trusted_proxy_unavailable"


def test_scoped_ipv6_dns_answer_is_rejected() -> None:
    def resolver(
        host: str,
        port: int,
        **_kwargs: Any,
    ) -> list[tuple[Any, ...]]:
        del host
        return [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("2606:4700::1111%en0", port, 0, 1),
            )
        ]

    original = safe_http._system_resolver
    safe_http._system_resolver = resolver
    try:
        with pytest.raises(safe_http.SafeOutboundError):
            safe_http.resolve_pinned_addresses(
                "scoped.example",
                443,
                network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
            )
    finally:
        safe_http._system_resolver = original


@pytest.mark.parametrize(
    "scope",
    (
        safe_http.NetworkScope.PUBLIC_DIRECT,
        safe_http.NetworkScope.PRIVATE_DIRECT,
    ),
)
def test_deprecated_ipv6_site_local_is_never_allowed(
    monkeypatch: pytest.MonkeyPatch,
    scope: safe_http.NetworkScope,
) -> None:
    def resolver(host: str, port: int, **_kwargs: Any):
        del host
        return [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("2606:4700::1111", port, 0, 0),
            ),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("fec0::1", port, 0, 0),
            ),
        ]

    monkeypatch.setattr(safe_http, "_system_resolver", resolver)
    with pytest.raises(safe_http.SafeOutboundError) as captured:
        safe_http.resolve_pinned_addresses(
            "mixed.example",
            443,
            network_scope=scope,
        )
    assert captured.value.reason_code == "outbound_address_blocked"


@pytest.mark.parametrize(
    "scope",
    (
        safe_http.NetworkScope.PUBLIC_DIRECT,
        safe_http.NetworkScope.PRIVATE_DIRECT,
    ),
)
def test_gcp_ipv6_metadata_is_permanently_blocked(
    monkeypatch: pytest.MonkeyPatch,
    scope: safe_http.NetworkScope,
) -> None:
    def resolver(host: str, port: int, **_kwargs: Any):
        del host
        return [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("fd20:ce::254", port, 0, 0),
            )
        ]

    monkeypatch.setattr(safe_http, "_system_resolver", resolver)
    with pytest.raises(safe_http.SafeOutboundError) as captured:
        safe_http.resolve_pinned_addresses(
            "metadata-origin.example",
            443,
            network_scope=scope,
        )
    assert captured.value.reason_code == "outbound_address_blocked"


def test_boolean_port_is_rejected_before_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    def resolver(host: str, port: int, **_kwargs: Any):
        calls.append((host, port))
        return []

    monkeypatch.setattr(safe_http, "_system_resolver", resolver)
    with pytest.raises(safe_http.SafeOutboundError):
        safe_http.resolve_pinned_addresses(
            "port.example",
            True,
            network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
        )
    assert calls == []


def test_sync_and_async_transports_reject_plain_http_before_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_resolves: list[tuple[str, int]] = []
    async_resolves: list[tuple[str, int]] = []

    def sync_resolver(host: str, port: int, **_kwargs: Any):
        sync_resolves.append((host, port))
        return []

    def async_resolver(host: str, port: int, **_kwargs: Any):
        async_resolves.append((host, port))
        return []

    monkeypatch.setattr(safe_http, "_system_resolver", sync_resolver)
    transport = safe_http.build_openai_sync_transport(
        network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
    )
    with pytest.raises(safe_http.SafeOutboundError):
        with httpx.Client(transport=transport, trust_env=False) as client:
            client.get("http://plain.example/resource")
    assert sync_resolves == []

    async def scenario() -> None:
        monkeypatch.setattr(safe_http, "_system_resolver", async_resolver)
        async_transport = safe_http.build_openai_async_transport(
            network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
        )
        with pytest.raises(safe_http.SafeOutboundError):
            async with httpx.AsyncClient(
                transport=async_transport,
                trust_env=False,
            ) as client:
                await client.get("http://plain.example/resource")

    asyncio.run(scenario())
    assert async_resolves == []


class _CloseCountingSyncCoreStream:
    def __init__(self) -> None:
        self.close_calls = 0

    def __iter__(self):
        yield b""

    def close(self) -> None:
        self.close_calls += 1


class _CloseCountingAsyncCoreStream:
    def __init__(self) -> None:
        self.aclose_calls = 0

    async def __aiter__(self):
        yield b""

    async def aclose(self) -> None:
        self.aclose_calls += 1


class _CoreResponse:
    def __init__(
        self,
        stream: object,
        *,
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        self.status = 200
        self.headers = [] if headers is None else headers
        self.stream = stream
        self.extensions: dict[str, Any] = {}


class _SyncPoolReturningResponse:
    def __init__(self, stream: object) -> None:
        self.stream = stream

    def handle_request(self, request: httpcore.Request) -> _CoreResponse:
        del request
        return _CoreResponse(self.stream)

    def close(self) -> None:
        return None


class _AsyncPoolReturningResponse:
    def __init__(self, stream: object) -> None:
        self.stream = stream

    async def handle_async_request(
        self,
        request: httpcore.Request,
    ) -> _CoreResponse:
        del request
        return _CoreResponse(self.stream)

    async def aclose(self) -> None:
        return None


class _SyncCapturingPool:
    def __init__(
        self,
        stream: _CloseCountingSyncCoreStream,
        *,
        response_headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        self.stream = stream
        self.response_headers = response_headers
        self.requests: list[httpcore.Request] = []

    def handle_request(self, request: httpcore.Request) -> _CoreResponse:
        self.requests.append(request)
        return _CoreResponse(
            self.stream,
            headers=self.response_headers,
        )

    def close(self) -> None:
        return None


class _AsyncCapturingPool:
    def __init__(
        self,
        stream: _CloseCountingAsyncCoreStream,
        *,
        response_headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        self.stream = stream
        self.response_headers = response_headers
        self.requests: list[httpcore.Request] = []

    async def handle_async_request(
        self,
        request: httpcore.Request,
    ) -> _CoreResponse:
        self.requests.append(request)
        return _CoreResponse(
            self.stream,
            headers=self.response_headers,
        )

    async def aclose(self) -> None:
        return None


def _header_values(
    request: httpcore.Request,
    name: bytes,
) -> list[bytes]:
    return [
        value
        for header_name, value in request.headers
        if header_name.lower() == name.lower()
    ]


@pytest.mark.parametrize("proxy_profile", (None, _approved_profile()))
def test_core_transports_bind_identity_and_reject_compressed_response(
    proxy_profile: safe_http.TrustedProxyProfile | None,
) -> None:
    response_headers = [
        (b"Content-Type", b"application/json"),
        (b"Content-Encoding", b"gzip"),
    ]
    sync_stream = _CloseCountingSyncCoreStream()
    sync_pool = _SyncCapturingPool(
        sync_stream,
        response_headers=response_headers,
    )
    sync_transport = safe_http._CoreSyncTransport(
        sync_pool,
        proxy_profile,
    )
    sync_request = httpx.Request(
        "GET",
        "https://public.example:8443/data",
        headers={"Accept-Encoding": "gzip, deflate, br"},
    )
    with pytest.raises(safe_http.SafeOutboundError) as sync_error:
        sync_transport.handle_request(sync_request)
    assert sync_error.value.reason_code == "provider_response_invalid_encoding"
    assert len(sync_pool.requests) == 1
    assert _header_values(sync_pool.requests[0], b"host") == [b"public.example:8443"]
    assert _header_values(sync_pool.requests[0], b"accept-encoding") == [b"identity"]
    assert sync_stream.close_calls == 1

    async def scenario() -> None:
        async_stream = _CloseCountingAsyncCoreStream()
        async_pool = _AsyncCapturingPool(
            async_stream,
            response_headers=response_headers,
        )
        async_transport = safe_http._CoreAsyncTransport(
            async_pool,
            proxy_profile,
        )
        async_request = httpx.Request(
            "GET",
            "https://public.example:8443/data",
            headers={"Accept-Encoding": "gzip, deflate, br"},
        )
        with pytest.raises(safe_http.SafeOutboundError) as async_error:
            await async_transport.handle_async_request(async_request)
        assert async_error.value.reason_code == "provider_response_invalid_encoding"
        assert len(async_pool.requests) == 1
        assert _header_values(async_pool.requests[0], b"host") == [
            b"public.example:8443"
        ]
        assert _header_values(
            async_pool.requests[0],
            b"accept-encoding",
        ) == [b"identity"]
        assert async_stream.aclose_calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("url", "expected_authority"),
    (
        ("https://public.example:8443/data", b"public.example:8443"),
        (
            "https://[2606:4700::1111]:8443/data",
            b"[2606:4700::1111]:8443",
        ),
    ),
)
def test_core_transports_reject_host_override_before_pool(
    url: str,
    expected_authority: bytes,
) -> None:
    sync_stream = _CloseCountingSyncCoreStream()
    sync_pool = _SyncCapturingPool(sync_stream)
    sync_transport = safe_http._CoreSyncTransport(sync_pool, None)
    sync_request = httpx.Request(
        "GET",
        url,
        headers={"Host": "evil.example"},
    )
    with pytest.raises(safe_http.SafeOutboundError):
        sync_transport.handle_request(sync_request)
    assert sync_pool.requests == []
    assert sync_stream.close_calls == 0

    canonical_sync_request = httpx.Request("GET", url)
    sync_response = sync_transport.handle_request(canonical_sync_request)
    assert _header_values(sync_pool.requests[0], b"host") == [expected_authority]
    sync_response.close()

    async def scenario() -> None:
        async_stream = _CloseCountingAsyncCoreStream()
        async_pool = _AsyncCapturingPool(async_stream)
        async_transport = safe_http._CoreAsyncTransport(async_pool, None)
        async_request = httpx.Request(
            "GET",
            url,
            headers={"Host": "evil.example"},
        )
        with pytest.raises(safe_http.SafeOutboundError):
            await async_transport.handle_async_request(async_request)
        assert async_pool.requests == []
        assert async_stream.aclose_calls == 0

        canonical_async_request = httpx.Request("GET", url)
        async_response = await async_transport.handle_async_request(
            canonical_async_request
        )
        assert _header_values(async_pool.requests[0], b"host") == [expected_authority]
        await async_response.aclose()

    asyncio.run(scenario())


def test_proxy_authorization_is_rejected_at_transport_and_wrapper_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_headers = {"Proxy-Authorization": "Basic proxy-secret"}
    sync_stream = _CloseCountingSyncCoreStream()
    sync_pool = _SyncCapturingPool(sync_stream)
    sync_transport = safe_http._CoreSyncTransport(sync_pool, None)
    with pytest.raises(safe_http.SafeOutboundError):
        sync_transport.handle_request(
            httpx.Request(
                "GET",
                "https://public.example/data",
                headers=secret_headers,
            )
        )
    assert sync_pool.requests == []
    assert sync_stream.close_calls == 0

    builder_calls: list[dict[str, Any]] = []

    def forbidden_sync_builder(**kwargs: Any) -> httpx.BaseTransport:
        builder_calls.append(kwargs)
        raise AssertionError("wrapper built transport before header rejection")

    def forbidden_async_builder(**kwargs: Any) -> httpx.AsyncBaseTransport:
        builder_calls.append(kwargs)
        raise AssertionError("wrapper built transport before header rejection")

    monkeypatch.setattr(
        safe_http,
        "build_openai_sync_transport",
        forbidden_sync_builder,
    )
    for wrapper, kwargs in (
        (
            safe_http.request_pinned_https,
            {"network_scope": safe_http.NetworkScope.PUBLIC_DIRECT},
        ),
        (
            safe_http.request_via_trusted_proxy,
            {"trusted_proxy_profile": "approved"},
        ),
    ):
        with pytest.raises(safe_http.SafeOutboundError):
            with wrapper(
                "GET",
                "https://public.example/data",
                headers=secret_headers,
                **kwargs,
            ):
                pass

    async def scenario() -> None:
        async_stream = _CloseCountingAsyncCoreStream()
        async_pool = _AsyncCapturingPool(async_stream)
        async_transport = safe_http._CoreAsyncTransport(async_pool, None)
        with pytest.raises(safe_http.SafeOutboundError):
            await async_transport.handle_async_request(
                httpx.Request(
                    "GET",
                    "https://public.example/data",
                    headers=secret_headers,
                )
            )
        assert async_pool.requests == []
        assert async_stream.aclose_calls == 0

        monkeypatch.setattr(
            safe_http,
            "build_openai_async_transport",
            forbidden_async_builder,
        )
        for wrapper, kwargs in (
            (
                safe_http.request_pinned_https_async,
                {"network_scope": safe_http.NetworkScope.PUBLIC_DIRECT},
            ),
            (
                safe_http.request_via_trusted_proxy_async,
                {"trusted_proxy_profile": "approved"},
            ),
        ):
            with pytest.raises(safe_http.SafeOutboundError):
                async with wrapper(
                    "GET",
                    "https://public.example/data",
                    headers=secret_headers,
                    **kwargs,
                ):
                    pass

    asyncio.run(scenario())
    assert builder_calls == []


@pytest.mark.parametrize(
    "url",
    (
        "https://public.example:sk-url-secret/data",
        "https://public.example:0/data",
        "https://public.example:99999/data",
    ),
)
def test_wrappers_reject_invalid_port_before_builder(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    builder_calls: list[dict[str, Any]] = []

    def forbidden_sync_builder(**kwargs: Any) -> httpx.BaseTransport:
        builder_calls.append(kwargs)
        raise AssertionError("wrapper built transport before URL rejection")

    def forbidden_async_builder(**kwargs: Any) -> httpx.AsyncBaseTransport:
        builder_calls.append(kwargs)
        raise AssertionError("wrapper built transport before URL rejection")

    monkeypatch.setattr(
        safe_http,
        "build_openai_sync_transport",
        forbidden_sync_builder,
    )
    for wrapper, kwargs in (
        (
            safe_http.request_pinned_https,
            {"network_scope": safe_http.NetworkScope.PUBLIC_DIRECT},
        ),
        (
            safe_http.request_via_trusted_proxy,
            {"trusted_proxy_profile": "approved"},
        ),
    ):
        with pytest.raises(safe_http.SafeOutboundError) as captured:
            with wrapper("GET", url, **kwargs):
                pass
        assert captured.value.reason_code == "safe_transport_unavailable"
        assert "sk-url-secret" not in str(captured.value)

    async def scenario() -> None:
        monkeypatch.setattr(
            safe_http,
            "build_openai_async_transport",
            forbidden_async_builder,
        )
        for wrapper, kwargs in (
            (
                safe_http.request_pinned_https_async,
                {"network_scope": safe_http.NetworkScope.PUBLIC_DIRECT},
            ),
            (
                safe_http.request_via_trusted_proxy_async,
                {"trusted_proxy_profile": "approved"},
            ),
        ):
            with pytest.raises(safe_http.SafeOutboundError) as captured:
                async with wrapper("GET", url, **kwargs):
                    pass
            assert captured.value.reason_code == "safe_transport_unavailable"
            assert "sk-url-secret" not in str(captured.value)

    asyncio.run(scenario())
    assert builder_calls == []


@pytest.mark.parametrize(
    "url",
    (
        "https://169.254.169.254/latest/meta-data",
        "https://[fd00:ec2::254]/latest/meta-data",
        "https://198.18.0.1/provider",
        "https://[::ffff:169.254.169.254]/latest/meta-data",
        "https://10.0.0.8/private",
        "https://127.0.0.1/private",
        "https://[fd00::8]/private",
        "https://[fd20:ce::254]/computeMetadata/v1",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://metadata.goog/computeMetadata/v1",
        "https://instance-data.ec2.internal/latest/meta-data",
        "https://2130706433/private",
        "https://127.1/private",
        "https://0x7f000001/private",
        "https://0177.0.0.1/private",
        "https://017700000001/private",
        "https://[2606:4700:4700::1111%25en0]/scoped",
        "https://%31%32%37.0.0.1/encoded-loopback",
        "https://%6detadata.google.internal/encoded-metadata",
        "https://user:secret@public.example/userinfo",
        "https://public.example:0/invalid-port",
    ),
)
def test_trusted_proxy_rejects_unsafe_origin_target_before_pool(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    profile = _approved_profile()
    sync_stream = _CloseCountingSyncCoreStream()
    sync_pool = _SyncCapturingPool(sync_stream)
    sync_transport = safe_http._CoreSyncTransport(sync_pool, profile)
    monkeypatch.setattr(
        safe_http,
        "build_openai_sync_transport",
        lambda **_kwargs: sync_transport,
    )
    with pytest.raises(safe_http.SafeOutboundError):
        with safe_http.request_via_trusted_proxy(
            "GET",
            url,
            trusted_proxy_profile="approved",
        ):
            pass
    assert sync_pool.requests == []
    assert sync_stream.close_calls == 0

    async def scenario() -> None:
        async_stream = _CloseCountingAsyncCoreStream()
        async_pool = _AsyncCapturingPool(async_stream)
        async_transport = safe_http._CoreAsyncTransport(
            async_pool,
            profile,
        )
        monkeypatch.setattr(
            safe_http,
            "build_openai_async_transport",
            lambda **_kwargs: async_transport,
        )
        with pytest.raises(safe_http.SafeOutboundError):
            async with safe_http.request_via_trusted_proxy_async(
                "GET",
                url,
                trusted_proxy_profile="approved",
            ):
                pass
        assert async_pool.requests == []
        assert async_stream.aclose_calls == 0

    asyncio.run(scenario())


def test_response_construction_failure_closes_core_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_stream = _CloseCountingSyncCoreStream()
    sync_transport = safe_http._CoreSyncTransport(
        _SyncPoolReturningResponse(sync_stream),
        None,
    )
    sync_request = httpx.Request("GET", "https://public.example/")

    def fail_response(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("sensitive constructor failure")

    monkeypatch.setattr(safe_http.httpx, "Response", fail_response)
    with pytest.raises(safe_http.SafeOutboundError):
        sync_transport.handle_request(sync_request)
    assert sync_stream.close_calls == 1

    async def scenario() -> None:
        async_stream = _CloseCountingAsyncCoreStream()
        async_transport = safe_http._CoreAsyncTransport(
            _AsyncPoolReturningResponse(async_stream),
            None,
        )
        async_request = httpx.Request("GET", "https://public.example/")
        with pytest.raises(safe_http.SafeOutboundError):
            await async_transport.handle_async_request(async_request)
        assert async_stream.aclose_calls == 1

    asyncio.run(scenario())


def test_response_stream_close_is_idempotent() -> None:
    sync_stream = _CloseCountingSyncCoreStream()
    wrapped_sync = safe_http._CoreSyncResponseStream(sync_stream, None)
    wrapped_sync.close()
    wrapped_sync.close()
    assert sync_stream.close_calls == 1

    async def scenario() -> None:
        async_stream = _CloseCountingAsyncCoreStream()
        wrapped_async = safe_http._CoreAsyncResponseStream(
            async_stream,
            None,
        )
        await wrapped_async.aclose()
        await wrapped_async.aclose()
        assert async_stream.aclose_calls == 1

    asyncio.run(scenario())


def test_async_response_cancellation_closes_core_stream() -> None:
    class _CancelledAsyncCoreStream:
        def __init__(self) -> None:
            self.aclose_calls = 0

        async def __aiter__(self):
            raise asyncio.CancelledError
            yield b""  # pragma: no cover

        async def aclose(self) -> None:
            self.aclose_calls += 1

    async def scenario() -> None:
        stream = _CancelledAsyncCoreStream()
        wrapped = safe_http._CoreAsyncResponseStream(stream, None)
        with pytest.raises(asyncio.CancelledError):
            async for _part in wrapped:
                pass
        assert stream.aclose_calls == 1

    asyncio.run(scenario())


def test_anyio_level_cancellation_shields_async_response_cleanup() -> None:
    class _LevelCancelledAsyncCoreStream:
        def __init__(self) -> None:
            self.started = anyio.Event()
            self.close_entered = False
            self.closed = False

        async def __aiter__(self):
            self.started.set()
            await anyio.sleep_forever()
            yield b""  # pragma: no cover

        async def aclose(self) -> None:
            self.close_entered = True
            await anyio.sleep(0)
            self.closed = True

    async def scenario() -> None:
        stream = _LevelCancelledAsyncCoreStream()
        wrapped = safe_http._CoreAsyncResponseStream(stream, None)

        async with anyio.create_task_group() as task_group:

            async def consume() -> None:
                async for _part in wrapped:
                    pass

            task_group.start_soon(consume)
            await stream.started.wait()
            task_group.cancel_scope.cancel()

        assert stream.close_entered is True
        assert stream.closed is True

    anyio.run(scenario)


def test_anyio_level_cancellation_shields_explicit_async_closes() -> None:
    class _CheckpointingCloseStream(httpcore.AsyncNetworkStream):
        def __init__(self) -> None:
            self.close_entered = False
            self.closed = False

        async def __aiter__(self):
            if False:  # pragma: no cover
                yield b""

        async def read(
            self,
            max_bytes: int,
            timeout: float | None = None,
        ) -> bytes:
            del max_bytes, timeout
            return b""

        async def write(
            self,
            buffer: bytes,
            timeout: float | None = None,
        ) -> None:
            del buffer, timeout

        async def aclose(self) -> None:
            self.close_entered = True
            await anyio.sleep(0)
            self.closed = True

        async def start_tls(
            self,
            ssl_context: Any,
            server_hostname: str | None = None,
            timeout: float | None = None,
        ) -> httpcore.AsyncNetworkStream:
            del ssl_context, server_hostname, timeout
            return self

        def get_extra_info(self, info: str) -> Any:
            del info
            return None

    async def scenario() -> None:
        response_raw = _CheckpointingCloseStream()
        response = safe_http._CoreAsyncResponseStream(response_raw, None)
        with anyio.CancelScope() as response_scope:
            response_scope.cancel()
            await response.aclose()
        assert response_raw.close_entered is True
        assert response_raw.closed is True

        pinned_raw = _CheckpointingCloseStream()
        pinned = safe_http._PinnedAsyncStream(pinned_raw, "8.8.8.8")
        with anyio.CancelScope() as pinned_scope:
            pinned_scope.cancel()
            await pinned.aclose()
        assert pinned_raw.close_entered is True
        assert pinned_raw.closed is True

    anyio.run(scenario)


def test_anyio_level_cancellation_shields_async_transport_pool_close() -> None:
    class _CheckpointingAsyncPool:
        def __init__(self) -> None:
            self.close_entered = False
            self.closed = False

        async def aclose(self) -> None:
            self.close_entered = True
            await anyio.sleep(0)
            self.closed = True

    async def scenario() -> None:
        pool = _CheckpointingAsyncPool()
        transport = safe_http._CoreAsyncTransport(pool, None)
        transport_returned = False
        post_close_checkpoint_reached = False

        with anyio.CancelScope() as cancel_scope:
            cancel_scope.cancel()
            await transport.aclose()
            transport_returned = True
            await anyio.sleep(0)
            post_close_checkpoint_reached = True

        assert pool.close_entered is True
        assert pool.closed is True
        assert transport_returned is True
        assert post_close_checkpoint_reached is False

    anyio.run(scenario)


def test_sni_extension_cannot_override_pinned_origin() -> None:
    stream = _CloseCountingSyncCoreStream()
    pool = _SyncPoolReturningResponse(stream)
    transport = safe_http._CoreSyncTransport(pool, None)
    request = httpx.Request(
        "GET",
        "https://public.example/",
        extensions={"sni_hostname": "evil.example"},
    )
    with pytest.raises(safe_http.SafeOutboundError):
        transport.handle_request(request)
    assert stream.close_calls == 0


def test_async_tls_cancellation_closes_parent_stream() -> None:
    class _CancelledTLSStream(httpcore.AsyncNetworkStream):
        def __init__(self) -> None:
            self.aclose_calls = 0

        async def read(
            self,
            max_bytes: int,
            timeout: float | None = None,
        ) -> bytes:
            del max_bytes, timeout
            return b""

        async def write(
            self,
            buffer: bytes,
            timeout: float | None = None,
        ) -> None:
            del buffer, timeout

        async def aclose(self) -> None:
            self.aclose_calls += 1

        async def start_tls(
            self,
            ssl_context: Any,
            server_hostname: str | None = None,
            timeout: float | None = None,
        ) -> httpcore.AsyncNetworkStream:
            del ssl_context, server_hostname, timeout
            raise asyncio.CancelledError

        def get_extra_info(self, info: str) -> Any:
            del info
            return None

    async def scenario() -> None:
        raw = _CancelledTLSStream()
        wrapped = safe_http._PinnedAsyncStream(raw, "8.8.8.8")
        with pytest.raises(asyncio.CancelledError):
            await wrapped.start_tls(
                safe_http._secure_ssl_context(),
                server_hostname="public.example",
            )
        assert raw.aclose_calls == 1

    asyncio.run(scenario())


def test_anyio_level_cancellation_shields_async_tls_parent_cleanup() -> None:
    class _LevelCancelledTLSStream(httpcore.AsyncNetworkStream):
        def __init__(self) -> None:
            self.started = anyio.Event()
            self.close_entered = False
            self.closed = False

        async def read(
            self,
            max_bytes: int,
            timeout: float | None = None,
        ) -> bytes:
            del max_bytes, timeout
            return b""

        async def write(
            self,
            buffer: bytes,
            timeout: float | None = None,
        ) -> None:
            del buffer, timeout

        async def aclose(self) -> None:
            self.close_entered = True
            await anyio.sleep(0)
            self.closed = True

        async def start_tls(
            self,
            ssl_context: Any,
            server_hostname: str | None = None,
            timeout: float | None = None,
        ) -> httpcore.AsyncNetworkStream:
            del ssl_context, server_hostname, timeout
            self.started.set()
            await anyio.sleep_forever()
            raise AssertionError("unreachable")

        def get_extra_info(self, info: str) -> Any:
            del info
            return None

    async def scenario() -> None:
        raw = _LevelCancelledTLSStream()
        wrapped = safe_http._PinnedAsyncStream(raw, "8.8.8.8")

        async with anyio.create_task_group() as task_group:

            async def start_tls() -> None:
                await wrapped.start_tls(
                    safe_http._secure_ssl_context(),
                    server_hostname="public.example",
                )

            task_group.start_soon(start_tls)
            await raw.started.wait()
            task_group.cancel_scope.cancel()

        assert raw.close_entered is True
        assert raw.closed is True

    anyio.run(scenario)


def test_wrappers_request_identity_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_headers: list[str] = []
    async_headers: list[str] = []

    def sync_handler(request: httpx.Request) -> httpx.Response:
        sync_headers.append(request.headers["accept-encoding"])
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=b"{}",
            request=request,
        )

    async def async_handler(request: httpx.Request) -> httpx.Response:
        async_headers.append(request.headers["accept-encoding"])
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=b"{}",
            request=request,
        )

    monkeypatch.setattr(
        safe_http,
        "build_openai_sync_transport",
        lambda **_kwargs: httpx.MockTransport(sync_handler),
    )
    with safe_http.request_pinned_https(
        "GET",
        "https://public.example/data",
        network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
    ):
        pass

    monkeypatch.setattr(
        safe_http,
        "build_openai_async_transport",
        lambda **_kwargs: httpx.MockTransport(async_handler),
    )

    async def scenario() -> None:
        async with safe_http.request_pinned_https_async(
            "GET",
            "https://public.example/data",
            network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
        ):
            pass

    asyncio.run(scenario())
    assert sync_headers == ["identity"]
    assert async_headers == ["identity"]


def test_bounded_json_rejects_compressed_body_before_consumption() -> None:
    compressed = gzip.compress(b'{"value":"' + (b"A" * 262_144) + b'"}')

    class _AsyncCompressedBody(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.consumed = 0

        async def __aiter__(self):
            self.consumed += 1
            yield compressed

    sync_response = httpx.Response(
        200,
        headers={
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
        },
        content=compressed,
    )
    with pytest.raises(safe_http.SafeOutboundError) as sync_error:
        safe_http.read_bounded_json(sync_response, max_bytes=1024)
    assert sync_error.value.reason_code == "provider_response_invalid_encoding"

    async def scenario() -> None:
        body = _AsyncCompressedBody()
        async_response = httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
            },
            stream=body,
        )
        with pytest.raises(safe_http.SafeOutboundError) as async_error:
            await safe_http.read_bounded_json_async(
                async_response,
                max_bytes=1024,
            )
        assert async_error.value.reason_code == "provider_response_invalid_encoding"
        assert body.consumed == 0

    asyncio.run(scenario())


def test_bounded_json_rejects_pathological_content_length_before_body() -> None:
    pathological_length = "9" * 5000
    sync_response = httpx.Response(
        200,
        headers={
            "Content-Type": "application/json",
            "Content-Length": pathological_length,
        },
        content=b"{}",
    )
    with pytest.raises(safe_http.SafeOutboundError) as sync_error:
        safe_http.read_bounded_json(sync_response, max_bytes=64)
    assert sync_error.value.reason_code == "provider_response_too_large"

    class _NeverConsumedAsyncBody(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.consumed = 0

        async def __aiter__(self):
            self.consumed += 1
            yield b"{}"

    async def scenario() -> None:
        body = _NeverConsumedAsyncBody()
        async_response = httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "Content-Length": pathological_length,
            },
            stream=body,
        )
        with pytest.raises(safe_http.SafeOutboundError) as async_error:
            await safe_http.read_bounded_json_async(
                async_response,
                max_bytes=64,
            )
        assert async_error.value.reason_code == "provider_response_too_large"
        assert body.consumed == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "content_type",
    (
        "application/bad space+json",
        "application/(bad)+json",
        "application/bad@name+json",
    ),
)
def test_bounded_json_rejects_invalid_structured_suffix_token(
    content_type: str,
) -> None:
    response = httpx.Response(
        200,
        headers={"Content-Type": content_type},
        content=b"{}",
    )
    with pytest.raises(safe_http.SafeOutboundError) as captured:
        safe_http.read_bounded_json(response)
    assert captured.value.reason_code == "provider_response_invalid_mime"
