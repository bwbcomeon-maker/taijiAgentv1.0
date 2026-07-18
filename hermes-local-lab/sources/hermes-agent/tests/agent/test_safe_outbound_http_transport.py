"""Behavior-first RED contracts for the safe outbound transport.

Exactly eight pytest nodes cover the public sync/async builders, the four
uniform request context managers, proxy tunnel framing, fail-closed transport
errors, and bounded JSON response parsing.  Private backend constructors are
intentionally not part of this contract.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import socket
import ssl
from collections import deque
from typing import Any, Awaitable, Callable

import httpcore
import httpx

import agent.safe_outbound_http as safe_http


PUBLIC_IP = "8.8.8.8"
OTHER_PUBLIC_IP = "8.8.4.4"
PRIVATE_IP = "10.0.0.7"
ORIGIN_HOST = "origin.example"
PROXY_HOST = "approved-proxy.example"


def _permanent_blocked_cases() -> tuple[tuple[str, str, str], ...]:
    outbound = "outbound_address_blocked"
    fake_ip = "fake_ip_requires_trusted_proxy"
    ipv4_cases = (
        ("metadata", "169.254.169.254", outbound),
        ("link-local-v4", "169.254.1.1", outbound),
        ("unspecified-v4", "0.0.0.0", outbound),
        ("multicast-v4", "224.0.0.1", outbound),
        ("reserved-v4", "240.0.0.1", outbound),
        ("documentation-v4", "192.0.2.1", outbound),
        ("cgnat", "100.64.0.1", outbound),
        ("fake-ip", "198.18.0.1", fake_ip),
    )
    mapped = tuple(
        (f"{label}-mapped", f"::ffff:{ip}", reason)
        for label, ip, reason in ipv4_cases
    )
    ipv6_cases = (
        ("link-local-v6", "fe80::1", outbound),
        ("unspecified-v6", "::", outbound),
        ("multicast-v6", "ff02::1", outbound),
        ("documentation-v6", "2001:db8::1", outbound),
        ("benchmark-v6", "2001:2::1", outbound),
    )
    return ipv4_cases + mapped + ipv6_cases


def _public_only_blocked_cases() -> tuple[tuple[str, str], ...]:
    ipv4_cases = (
        ("rfc1918-10", "10.23.4.5"),
        ("rfc1918-172", "172.20.4.5"),
        ("rfc1918-192", "192.168.4.5"),
        ("loopback-v4", "127.0.0.7"),
    )
    mapped = tuple(
        (f"{label}-mapped", f"::ffff:{ip}") for label, ip in ipv4_cases
    )
    ipv6_cases = (
        ("loopback-v6", "::1"),
        ("ula-fc00", "fc00::7"),
        ("ula-fd00", "fd00::7"),
    )
    return ipv4_cases + mapped + ipv6_cases


def _addrinfo(ip: str, port: int) -> tuple[Any, ...]:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr


class _FakeSocket:
    def __init__(self, peer_ip: str, port: int = 443) -> None:
        self.peer_ip = peer_ip
        self.port = port

    def getpeername(self) -> tuple[str, int]:
        return self.peer_ip, self.port


class _FakeSSLObject:
    def selected_alpn_protocol(self) -> None:
        return None


class _ScriptedSyncStream(httpcore.NetworkStream):
    def __init__(
        self,
        peer_ip: str,
        reads: list[bytes] | None = None,
        *,
        fail_tls_for: str | None = None,
        peer_after_tls: dict[int, str] | None = None,
        _shared: dict[str, Any] | None = None,
    ) -> None:
        self.peer_ip = peer_ip
        self._shared = _shared or {
            "reads": deque(reads or []),
            "pending": b"",
            "writes": [],
            "sni_hostnames": [],
            "tls_snapshots": [],
            "tls_calls": 0,
            "tls_children": [],
            "closed": False,
            "fail_tls_for": fail_tls_for,
            "peer_after_tls": dict(peer_after_tls or {}),
        }

    @property
    def writes(self) -> list[bytes]:
        return self._shared["writes"]

    @property
    def sni_hostnames(self) -> list[str | None]:
        return self._shared["sni_hostnames"]

    @property
    def tls_snapshots(self) -> list[tuple[ssl.VerifyMode, bool]]:
        return self._shared["tls_snapshots"]

    @property
    def tls_calls(self) -> int:
        return self._shared["tls_calls"]

    @property
    def tls_children(self) -> list["_ScriptedSyncStream"]:
        return self._shared["tls_children"]

    @property
    def closed(self) -> bool:
        return self._shared["closed"]

    @property
    def fail_tls_for(self) -> str | None:
        return self._shared["fail_tls_for"]

    @property
    def peer_after_tls(self) -> dict[int, str]:
        return self._shared["peer_after_tls"]

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del timeout
        if not self._shared["pending"] and self._shared["reads"]:
            self._shared["pending"] = self._shared["reads"].popleft()
        pending = self._shared["pending"]
        chunk, self._shared["pending"] = (
            pending[:max_bytes],
            pending[max_bytes:],
        )
        return chunk

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(bytes(buffer))

    def close(self) -> None:
        self._shared["closed"] = True

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> "_ScriptedSyncStream":
        del timeout
        self._shared["tls_calls"] += 1
        self.tls_snapshots.append(
            (ssl_context.verify_mode, ssl_context.check_hostname)
        )
        self.sni_hostnames.append(server_hostname)
        if server_hostname == self.fail_tls_for:
            raise ssl.SSLError("sk-live-secret must never escape")
        child = _ScriptedSyncStream(
            self.peer_after_tls.get(self.tls_calls, self.peer_ip),
            _shared=self._shared,
        )
        self.tls_children.append(child)
        return child

    def get_extra_info(self, info: str) -> Any:
        if info == "server_addr":
            return self.peer_ip, 443
        if info == "socket":
            return _FakeSocket(self.peer_ip)
        if info == "ssl_object":
            return _FakeSSLObject()
        if info == "is_readable":
            return bool(self._shared["pending"] or self._shared["reads"])
        return None


class _ScriptedAsyncStream(httpcore.AsyncNetworkStream):
    def __init__(
        self,
        peer_ip: str,
        reads: list[bytes] | None = None,
        *,
        fail_tls_for: str | None = None,
        peer_after_tls: dict[int, str] | None = None,
        _shared: dict[str, Any] | None = None,
    ) -> None:
        self.peer_ip = peer_ip
        self._shared = _shared or {
            "reads": deque(reads or []),
            "pending": b"",
            "writes": [],
            "sni_hostnames": [],
            "tls_snapshots": [],
            "tls_calls": 0,
            "tls_children": [],
            "closed": False,
            "fail_tls_for": fail_tls_for,
            "peer_after_tls": dict(peer_after_tls or {}),
        }

    @property
    def writes(self) -> list[bytes]:
        return self._shared["writes"]

    @property
    def sni_hostnames(self) -> list[str | None]:
        return self._shared["sni_hostnames"]

    @property
    def tls_snapshots(self) -> list[tuple[ssl.VerifyMode, bool]]:
        return self._shared["tls_snapshots"]

    @property
    def tls_calls(self) -> int:
        return self._shared["tls_calls"]

    @property
    def tls_children(self) -> list["_ScriptedAsyncStream"]:
        return self._shared["tls_children"]

    @property
    def closed(self) -> bool:
        return self._shared["closed"]

    @property
    def fail_tls_for(self) -> str | None:
        return self._shared["fail_tls_for"]

    @property
    def peer_after_tls(self) -> dict[int, str]:
        return self._shared["peer_after_tls"]

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del timeout
        if not self._shared["pending"] and self._shared["reads"]:
            self._shared["pending"] = self._shared["reads"].popleft()
        pending = self._shared["pending"]
        chunk, self._shared["pending"] = (
            pending[:max_bytes],
            pending[max_bytes:],
        )
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(bytes(buffer))

    async def aclose(self) -> None:
        self._shared["closed"] = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> "_ScriptedAsyncStream":
        del timeout
        self._shared["tls_calls"] += 1
        self.tls_snapshots.append(
            (ssl_context.verify_mode, ssl_context.check_hostname)
        )
        self.sni_hostnames.append(server_hostname)
        if server_hostname == self.fail_tls_for:
            raise ssl.SSLError("sk-live-secret must never escape")
        child = _ScriptedAsyncStream(
            self.peer_after_tls.get(self.tls_calls, self.peer_ip),
            _shared=self._shared,
        )
        self.tls_children.append(child)
        return child

    def get_extra_info(self, info: str) -> Any:
        if info == "server_addr":
            return self.peer_ip, 443
        if info == "socket":
            return _FakeSocket(self.peer_ip)
        if info == "ssl_object":
            return _FakeSSLObject()
        if info == "is_readable":
            return bool(self._shared["pending"] or self._shared["reads"])
        return None


class _CountingSyncBody(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


class _CountingAsyncBody(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _CloseCountingSyncBody(httpx.SyncByteStream):
    def __init__(self, body: bytes = b"redirect") -> None:
        self.body = body
        self.close_calls = 0

    def __iter__(self):
        yield self.body

    def close(self) -> None:
        self.close_calls += 1


class _CloseCountingAsyncBody(httpx.AsyncByteStream):
    def __init__(self, body: bytes = b"redirect") -> None:
        self.body = body
        self.close_calls = 0

    async def __aiter__(self):
        yield self.body

    async def aclose(self) -> None:
        self.close_calls += 1


class _TrackingSyncTransport(httpx.BaseTransport):
    def __init__(self, handler_error: Exception | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.response_bodies: list[_CloseCountingSyncBody] = []
        self.close_calls = 0
        self.handler_error = handler_error

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.handler_error is not None:
            raise self.handler_error
        body = request.read()
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": body,
            }
        )
        response_body = _CloseCountingSyncBody()
        self.response_bodies.append(response_body)
        return httpx.Response(
            302,
            headers={"Location": "https://redirect.invalid/blocked"},
            stream=response_body,
            request=request,
        )

    def close(self) -> None:
        self.close_calls += 1


class _TrackingAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler_error: Exception | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.response_bodies: list[_CloseCountingAsyncBody] = []
        self.aclose_calls = 0
        self.handler_error = handler_error

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        if self.handler_error is not None:
            raise self.handler_error
        body = await request.aread()
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": body,
            }
        )
        response_body = _CloseCountingAsyncBody()
        self.response_bodies.append(response_body)
        return httpx.Response(
            302,
            headers={"Location": "https://redirect.invalid/blocked"},
            stream=response_body,
            request=request,
        )

    async def aclose(self) -> None:
        self.aclose_calls += 1


class _IntentionalWrapperError(RuntimeError):
    pass


def _http_ok(body: bytes = b"{}") -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )


def _connect_ok() -> bytes:
    return (
        b"HTTP/1.1 200 Connection Established\r\n"
        b"Content-Length: 0\r\n"
        b"Connection: keep-alive\r\n\r\n"
    )


def _proxy_failure(status_line: str) -> bytes:
    return (
        f"HTTP/1.1 {status_line}\r\n".encode()
        + b"Content-Length: 0\r\n"
        + b"Connection: close\r\n\r\n"
    )


def _capture_sync(call: Callable[[], Any]) -> tuple[Any, Exception | None]:
    try:
        return call(), None
    except Exception as exc:  # noqa: BLE001 - converted into final assertions
        return None, exc


async def _capture_async(
    call: Callable[[], Awaitable[Any]],
) -> tuple[Any, Exception | None]:
    try:
        return await call(), None
    except Exception as exc:  # noqa: BLE001 - converted into final assertions
        return None, exc


def _assert_safe_error(error: Exception | None, reason_code: str) -> None:
    assert isinstance(error, safe_http.SafeOutboundError), (
        f"expected SafeOutboundError({reason_code}), got {error!r}"
    )
    assert error.reason_code == reason_code


def _proxy_profile(
    *,
    name: str = "approved-test",
    proxy_url: str = f"https://{PROXY_HOST}:8443",
    approved: bool = True,
    capabilities: frozenset[str] = frozenset(
        {"public_egress", "dns_ip_classification"}
    ),
    policy_denial_status: int = 451,
    policy_denial_reason: str = "TAIJI_ORIGIN_BLOCKED",
) -> safe_http.TrustedProxyProfile:
    return safe_http.TrustedProxyProfile(
        name=name,
        proxy_url=proxy_url,
        approved=approved,
        capabilities=capabilities,
        policy_denial_status=policy_denial_status,
        policy_denial_reason=policy_denial_reason,
    )


def _assert_secure_tls_snapshot(
    snapshot: tuple[ssl.VerifyMode, bool],
) -> None:
    assert snapshot == (ssl.CERT_REQUIRED, True)


def _http_request_frames(writes: list[bytes]) -> list[bytes]:
    pending = b"".join(writes)
    frames: list[bytes] = []
    marker = b"\r\n\r\n"
    while marker in pending:
        head, pending = pending.split(marker, 1)
        frames.append(head + marker)
    assert pending == b"", f"unexpected bytes outside HTTP header frames: {pending!r}"
    return frames


def _assert_request_frame(frame: bytes, request_line: bytes, host: bytes) -> None:
    assert frame.endswith(b"\r\n\r\n")
    lines = frame[:-4].split(b"\r\n")
    assert lines[0] == request_line
    host_lines = [line for line in lines[1:] if line.lower().startswith(b"host:")]
    assert host_lines == [b"Host: " + host]


def _proxy_profiles() -> dict[str, safe_http.TrustedProxyProfile]:
    return {
        "approved-test": _proxy_profile(),
        "unapproved": _proxy_profile(name="unapproved", approved=False),
        "missing-public-egress": _proxy_profile(
            name="missing-public-egress",
            capabilities=frozenset({"dns_ip_classification"}),
        ),
        "missing-dns-classification": _proxy_profile(
            name="missing-dns-classification",
            capabilities=frozenset({"public_egress"}),
        ),
        "custom-policy": _proxy_profile(
            name="custom-policy",
            policy_denial_status=499,
            policy_denial_reason="CUSTOM_ORIGIN_DENIED",
        ),
    }


def _assert_invalid_proxy_profile_mappings_are_rejected() -> None:
    invalid_profiles = (
        (
            "http-scheme",
            _proxy_profile(
                name="http-scheme",
                proxy_url=f"http://{PROXY_HOST}:8443",
            ),
        ),
        (
            "userinfo",
            _proxy_profile(
                name="userinfo",
                proxy_url=f"https://user:secret@{PROXY_HOST}:8443",
            ),
        ),
        (
            "query",
            _proxy_profile(
                name="query",
                proxy_url=f"https://{PROXY_HOST}:8443?route=evil",
            ),
        ),
        (
            "fragment",
            _proxy_profile(
                name="fragment",
                proxy_url=f"https://{PROXY_HOST}:8443#evil",
            ),
        ),
        (
            "metadata-literal",
            _proxy_profile(
                name="metadata-literal",
                proxy_url="https://169.254.169.254:8443",
            ),
        ),
        (
            "fake-ip-literal",
            _proxy_profile(
                name="fake-ip-literal",
                proxy_url="https://198.18.0.1:8443",
            ),
        ),
        (
            "dangerous-port",
            _proxy_profile(
                name="dangerous-port",
                proxy_url=f"https://{PROXY_HOST}:22",
            ),
        ),
        (
            "mapping-name",
            _proxy_profile(name="different-profile-name"),
        ),
    )
    for mapping_name, profile in invalid_profiles:
        _profile, error = _capture_sync(
            lambda key=mapping_name, value=profile: (
                safe_http.resolve_trusted_proxy_profile(
                    key,
                    profiles={key: value},
                )
            )
        )
        _assert_safe_error(error, "trusted_proxy_unavailable")


def _install_named_profile_resolver(
    monkeypatch,
    calls: list[str],
) -> dict[str, safe_http.TrustedProxyProfile]:
    profiles = _proxy_profiles()
    real_resolver = safe_http.resolve_trusted_proxy_profile

    def resolve_named_profile(name: object, **_kwargs: Any):
        calls.append(str(name))
        return real_resolver(name, profiles=profiles)

    monkeypatch.setattr(
        safe_http,
        "resolve_trusted_proxy_profile",
        resolve_named_profile,
    )
    return profiles


def _assert_public_api_signatures() -> None:
    expected_builder = ("network_scope", "trusted_proxy_profile")
    forbidden = {"resolver", "connector", "backend", "proxy_url"}
    for builder in (
        safe_http.build_openai_sync_transport,
        safe_http.build_openai_async_transport,
    ):
        signature = inspect.signature(builder)
        assert tuple(signature.parameters) == expected_builder
        assert forbidden.isdisjoint(signature.parameters)
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in signature.parameters.values()
        )

    expected_direct = (
        "method",
        "url",
        "network_scope",
        "headers",
        "json_body",
        "timeout",
        "follow_redirects",
    )
    expected_proxy = (
        "method",
        "url",
        "trusted_proxy_profile",
        "headers",
        "json_body",
        "timeout",
        "follow_redirects",
    )
    for wrapper, expected in (
        (safe_http.request_pinned_https, expected_direct),
        (safe_http.request_pinned_https_async, expected_direct),
        (safe_http.request_via_trusted_proxy, expected_proxy),
        (safe_http.request_via_trusted_proxy_async, expected_proxy),
    ):
        signature = inspect.signature(wrapper)
        assert tuple(signature.parameters) == expected
        assert (
            signature.parameters["follow_redirects"].default is False
        )


def _assert_sync_wrapper_contract(monkeypatch, *, proxy: bool) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    real_client = httpx.Client
    client_calls: list[dict[str, Any]] = []

    def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
        client_calls.append(dict(kwargs))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    transport = _TrackingSyncTransport()
    handler_transport = _TrackingSyncTransport(
        _IntentionalWrapperError("request handler failed")
    )
    transports = deque([transport, handler_transport])
    builder_calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> httpx.BaseTransport:
        builder_calls.append(kwargs)
        assert transports, "wrapper built more transports than expected"
        return transports.popleft()

    monkeypatch.setattr(safe_http, "build_openai_sync_transport", builder)
    payload = {"prompt": "exact-body", "count": 2}
    headers = {
        "Authorization": "Bearer wrapper-secret",
        "X-Contract": "preserved",
    }

    def wrapper_context(*, follow_redirects: bool):
        if proxy:
            return safe_http.request_via_trusted_proxy(
                "POST",
                f"https://{ORIGIN_HOST}/v1/images",
                trusted_proxy_profile="approved-test",
                headers=headers,
                json_body=payload,
                follow_redirects=follow_redirects,
            )
        return safe_http.request_pinned_https(
            "POST",
            f"https://{ORIGIN_HOST}/v1/images",
            network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
            headers=headers,
            json_body=payload,
            follow_redirects=follow_redirects,
        )

    response: httpx.Response | None = None
    try:
        context = wrapper_context(follow_redirects=False)
        with context as response:
            assert response.status_code == 302
            assert response.headers["location"] == (
                "https://redirect.invalid/blocked"
            )
            raise _IntentionalWrapperError("prove finally closes resources")
    except _IntentionalWrapperError:
        pass

    assert response is not None
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == f"https://{ORIGIN_HOST}/v1/images"
    assert request["headers"]["authorization"] == "Bearer wrapper-secret"
    assert request["headers"]["x-contract"] == "preserved"
    assert request["body"] == json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    assert len(transport.response_bodies) == 1
    assert transport.response_bodies[0].close_calls == 1
    assert transport.close_calls == 1
    assert len(builder_calls) == 1
    expected_builder_kwargs: dict[str, Any]
    if proxy:
        expected_builder_kwargs = {
            "network_scope": safe_http.NetworkScope.TRUSTED_PROXY,
            "trusted_proxy_profile": "approved-test",
        }
    else:
        expected_builder_kwargs = {
            "network_scope": safe_http.NetworkScope.PUBLIC_DIRECT,
        }
    assert builder_calls[0] == expected_builder_kwargs
    assert len(client_calls) == 1
    assert client_calls[0].get("trust_env") is False
    assert client_calls[0].get("transport") is transport

    def handler_failure_call():
        with wrapper_context(follow_redirects=False):
            return None

    _value, handler_error = _capture_sync(handler_failure_call)
    assert isinstance(handler_error, _IntentionalWrapperError)
    assert handler_transport.requests == []
    assert handler_transport.response_bodies == []
    assert handler_transport.close_calls == 1
    assert builder_calls == [
        expected_builder_kwargs,
        expected_builder_kwargs,
    ]
    assert len(client_calls) == 2
    assert [call.get("transport") for call in client_calls] == [
        transport,
        handler_transport,
    ]
    assert all(call.get("trust_env") is False for call in client_calls)
    assert not transports

    def redirect_enabled_call():
        context = wrapper_context(follow_redirects=True)
        with context:
            return None

    _value, redirect_error = _capture_sync(redirect_enabled_call)
    _assert_safe_error(redirect_error, "redirects_not_allowed")
    assert len(builder_calls) == 2
    assert len(client_calls) == 2
    assert len(transport.requests) == 1


async def _assert_async_wrapper_contract(monkeypatch, *, proxy: bool) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    real_client = httpx.AsyncClient
    client_calls: list[dict[str, Any]] = []

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        client_calls.append(dict(kwargs))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    transport = _TrackingAsyncTransport()
    handler_transport = _TrackingAsyncTransport(
        _IntentionalWrapperError("async request handler failed")
    )
    transports = deque([transport, handler_transport])
    builder_calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> httpx.AsyncBaseTransport:
        builder_calls.append(kwargs)
        assert transports, "async wrapper built more transports than expected"
        return transports.popleft()

    monkeypatch.setattr(safe_http, "build_openai_async_transport", builder)
    payload = {"prompt": "async-exact-body", "items": [1, 2, 3]}
    headers = {
        "Authorization": "Bearer async-wrapper-secret",
        "X-Contract": "async-preserved",
    }

    def wrapper_context(*, follow_redirects: bool):
        if proxy:
            return safe_http.request_via_trusted_proxy_async(
                "POST",
                f"https://{ORIGIN_HOST}/v1/images",
                trusted_proxy_profile="approved-test",
                headers=headers,
                json_body=payload,
                follow_redirects=follow_redirects,
            )
        return safe_http.request_pinned_https_async(
            "POST",
            f"https://{ORIGIN_HOST}/v1/images",
            network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
            headers=headers,
            json_body=payload,
            follow_redirects=follow_redirects,
        )

    response: httpx.Response | None = None
    try:
        context = wrapper_context(follow_redirects=False)
        async with context as response:
            assert response.status_code == 302
            assert response.headers["location"] == (
                "https://redirect.invalid/blocked"
            )
            raise _IntentionalWrapperError("prove async finally closes")
    except _IntentionalWrapperError:
        pass

    assert response is not None
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == f"https://{ORIGIN_HOST}/v1/images"
    assert (
        request["headers"]["authorization"]
        == "Bearer async-wrapper-secret"
    )
    assert request["headers"]["x-contract"] == "async-preserved"
    assert request["body"] == json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    assert len(transport.response_bodies) == 1
    assert transport.response_bodies[0].close_calls == 1
    assert transport.aclose_calls == 1
    assert len(builder_calls) == 1
    expected_builder_kwargs: dict[str, Any]
    if proxy:
        expected_builder_kwargs = {
            "network_scope": safe_http.NetworkScope.TRUSTED_PROXY,
            "trusted_proxy_profile": "approved-test",
        }
    else:
        expected_builder_kwargs = {
            "network_scope": safe_http.NetworkScope.PUBLIC_DIRECT,
        }
    assert builder_calls[0] == expected_builder_kwargs
    assert len(client_calls) == 1
    assert client_calls[0].get("trust_env") is False
    assert client_calls[0].get("transport") is transport

    async def handler_failure_call():
        async with wrapper_context(follow_redirects=False):
            return None

    _value, handler_error = await _capture_async(handler_failure_call)
    assert isinstance(handler_error, _IntentionalWrapperError)
    assert handler_transport.requests == []
    assert handler_transport.response_bodies == []
    assert handler_transport.aclose_calls == 1
    assert builder_calls == [
        expected_builder_kwargs,
        expected_builder_kwargs,
    ]
    assert len(client_calls) == 2
    assert [call.get("transport") for call in client_calls] == [
        transport,
        handler_transport,
    ]
    assert all(call.get("trust_env") is False for call in client_calls)
    assert not transports

    async def redirect_enabled_call():
        context = wrapper_context(follow_redirects=True)
        async with context:
            return None

    _value, redirect_error = await _capture_async(redirect_enabled_call)
    _assert_safe_error(redirect_error, "redirects_not_allowed")
    assert len(builder_calls) == 2
    assert len(client_calls) == 2
    assert len(transport.requests) == 1


def test_sync_public_direct_builder_and_request_context_enforce_policy(
    monkeypatch,
) -> None:
    _assert_public_api_signatures()

    for answers in (
        [PRIVATE_IP, PUBLIC_IP],
        [PUBLIC_IP, PRIVATE_IP],
    ):
        resolves: list[str] = []
        connects: list[safe_http.PinnedAddress] = []

        def mixed_resolver(host: str, port: int, **_kwargs: Any):
            resolves.append(host)
            return [_addrinfo(ip, port) for ip in answers]

        def blocked_connector(address, timeout, local_address, socket_options):
            del timeout, local_address, socket_options
            connects.append(address)
            return _ScriptedSyncStream(address.canonical_ip)

        monkeypatch.setattr(safe_http, "_system_resolver", mixed_resolver)
        monkeypatch.setattr(safe_http, "_connect_sync_address", blocked_connector)

        def issue_mixed_request():
            transport = safe_http.build_openai_sync_transport(
                network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
            )
            with httpx.Client(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                return client.get(f"https://{ORIGIN_HOST}/v1/models")

        _response, mixed_error = _capture_sync(issue_mixed_request)
        _assert_safe_error(mixed_error, "outbound_address_blocked")
        assert resolves == [ORIGIN_HOST]
        assert connects == [], (
            f"mixed DNS answers {answers!r} must reject before every connect"
        )

    direct_block_cases = tuple(
        (label, ip, "outbound_address_blocked")
        for label, ip in _public_only_blocked_cases()
    ) + _permanent_blocked_cases()
    for label, blocked_ip, expected_reason in direct_block_cases:
        blocked_resolves: list[str] = []
        blocked_connects: list[safe_http.PinnedAddress] = []

        def permanently_blocked_resolver(
            host: str,
            port: int,
            **_kwargs: Any,
        ):
            blocked_resolves.append(host)
            return [
                _addrinfo(PUBLIC_IP, port),
                _addrinfo(blocked_ip, port),
                _addrinfo(OTHER_PUBLIC_IP, port),
            ]

        def permanently_blocked_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del timeout, local_address, socket_options
            blocked_connects.append(address)
            return _ScriptedSyncStream(address.canonical_ip)

        monkeypatch.setattr(
            safe_http,
            "_system_resolver",
            permanently_blocked_resolver,
        )
        monkeypatch.setattr(
            safe_http,
            "_connect_sync_address",
            permanently_blocked_connector,
        )
        _response, blocked_error = _capture_sync(issue_mixed_request)
        _assert_safe_error(blocked_error, expected_reason)
        assert blocked_resolves == [ORIGIN_HOST]
        assert blocked_connects == [], (
            f"PUBLIC_DIRECT connected {label} answer in mixed set"
        )

    mismatch_stream = _ScriptedSyncStream(OTHER_PUBLIC_IP)
    mismatch_connects: list[safe_http.PinnedAddress] = []

    def public_resolver(host: str, port: int, **_kwargs: Any):
        del host, _kwargs
        return [_addrinfo(PUBLIC_IP, port)]

    def mismatch_connector(address, timeout, local_address, socket_options):
        del timeout, local_address, socket_options
        mismatch_connects.append(address)
        return mismatch_stream

    monkeypatch.setattr(safe_http, "_system_resolver", public_resolver)
    monkeypatch.setattr(safe_http, "_connect_sync_address", mismatch_connector)

    def issue_mismatch_request():
        transport = safe_http.build_openai_sync_transport(
            network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
        )
        with httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            return client.get(f"https://{ORIGIN_HOST}/v1/models")

    _response, mismatch_error = _capture_sync(issue_mismatch_request)
    _assert_safe_error(mismatch_error, "connected_peer_mismatch")
    assert len(mismatch_connects) == 1
    assert mismatch_stream.closed is True
    assert mismatch_stream.writes == []
    assert mismatch_stream.sni_hostnames == []
    assert mismatch_stream.tls_snapshots == []

    tls_drift_stream = _ScriptedSyncStream(
        PUBLIC_IP,
        [_http_ok()],
        peer_after_tls={1: OTHER_PUBLIC_IP},
    )

    def tls_drift_connector(address, timeout, local_address, socket_options):
        del address, timeout, local_address, socket_options
        return tls_drift_stream

    monkeypatch.setattr(safe_http, "_system_resolver", public_resolver)
    monkeypatch.setattr(
        safe_http,
        "_connect_sync_address",
        tls_drift_connector,
    )
    _response, tls_drift_error = _capture_sync(issue_mismatch_request)
    _assert_safe_error(tls_drift_error, "connected_peer_mismatch")
    assert len(tls_drift_stream.tls_children) == 1
    tls_child = tls_drift_stream.tls_children[0]
    assert tls_child is not tls_drift_stream
    assert tls_drift_stream.peer_ip == PUBLIC_IP
    assert tls_child.peer_ip == OTHER_PUBLIC_IP
    assert tls_drift_stream.sni_hostnames == [ORIGIN_HOST]
    assert tls_drift_stream.tls_snapshots == [
        (ssl.CERT_REQUIRED, True)
    ]
    assert tls_drift_stream.closed is True
    assert tls_drift_stream.writes == []

    resolves: list[tuple[str, int]] = []
    success_connects: list[safe_http.PinnedAddress] = []
    streams: list[_ScriptedSyncStream] = []

    def success_resolver(host: str, port: int, **_kwargs: Any):
        del _kwargs
        resolves.append((host, port))
        return [_addrinfo(PUBLIC_IP, port)]

    def success_connector(address, timeout, local_address, socket_options):
        del timeout, local_address, socket_options
        success_connects.append(address)
        stream = _ScriptedSyncStream(address.canonical_ip, [_http_ok()])
        streams.append(stream)
        return stream

    monkeypatch.setattr(safe_http, "_system_resolver", success_resolver)
    monkeypatch.setattr(safe_http, "_connect_sync_address", success_connector)

    def issue_success_request():
        transport = safe_http.build_openai_sync_transport(
            network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
        )
        with httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            return client.get(f"https://{ORIGIN_HOST}:8443/v1/models")

    response, success_error = _capture_sync(issue_success_request)
    assert success_error is None, f"safe direct request failed: {success_error!r}"
    assert response.status_code == 200
    assert resolves == [(ORIGIN_HOST, 8443)]
    assert len(success_connects) == 1
    assert success_connects[0].sockaddr[1] == 8443
    assert len(streams) == 1
    assert len(streams[0].tls_children) == 1
    assert streams[0].tls_children[0] is not streams[0]
    assert streams[0].sni_hostnames == [ORIGIN_HOST]
    assert len(streams[0].tls_snapshots) == 1
    _assert_secure_tls_snapshot(streams[0].tls_snapshots[0])
    frames = _http_request_frames(streams[0].writes)
    assert len(frames) == 1
    _assert_request_frame(
        frames[0],
        b"GET /v1/models HTTP/1.1",
        b"origin.example:8443",
    )
    assert PUBLIC_IP.encode() not in frames[0]
    assert streams[0].closed is True

    allowed_private = (
        ("rfc1918-10", PRIVATE_IP, 9441),
        ("rfc1918-172", "172.20.1.7", 9442),
        ("rfc1918-192", "192.168.1.7", 9444),
        ("loopback-v4", "127.0.0.1", 9445),
        ("loopback-v6", "::1", 9446),
        ("ula-fc00", "fc00::7", 9447),
        ("ula-fd00", "fd00::7", 9448),
    )
    for private_label, allowed_ip, private_port in allowed_private:
        private_resolves: list[tuple[str, int]] = []
        private_connects: list[safe_http.PinnedAddress] = []
        private_streams: list[_ScriptedSyncStream] = []

        def private_resolver(host: str, port: int, **_kwargs: Any):
            del _kwargs
            private_resolves.append((host, port))
            return [_addrinfo(allowed_ip, port)]

        def private_connector(address, timeout, local_address, socket_options):
            del timeout, local_address, socket_options
            private_connects.append(address)
            stream = _ScriptedSyncStream(
                address.canonical_ip,
                [_http_ok(b'{"private":true}')],
            )
            private_streams.append(stream)
            return stream

        monkeypatch.setattr(safe_http, "_system_resolver", private_resolver)
        monkeypatch.setattr(
            safe_http,
            "_connect_sync_address",
            private_connector,
        )

        def issue_private_request():
            transport = safe_http.build_openai_sync_transport(
                network_scope=safe_http.NetworkScope.PRIVATE_DIRECT,
            )
            with httpx.Client(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                return client.get(
                    f"https://{ORIGIN_HOST}:{private_port}/private-health"
                )

        private_response, private_error = _capture_sync(
            issue_private_request
        )
        assert private_error is None, (
            f"PRIVATE_DIRECT rejected allowed {allowed_ip}: {private_error!r}"
        )
        assert private_response.status_code == 200
        assert private_resolves == [(ORIGIN_HOST, private_port)]
        assert [address.canonical_ip for address in private_connects] == [
            allowed_ip
        ]
        assert private_connects[0].sockaddr[1] == private_port, (
            f"PRIVATE_DIRECT lost {private_label} port {private_port}"
        )
        assert private_streams[0].sni_hostnames == [ORIGIN_HOST]
        assert len(private_streams[0].tls_children) == 1
        assert private_streams[0].tls_children[0] is not private_streams[0]
        _assert_secure_tls_snapshot(private_streams[0].tls_snapshots[0])
        private_frames = _http_request_frames(private_streams[0].writes)
        _assert_request_frame(
            private_frames[0],
            b"GET /private-health HTTP/1.1",
            f"origin.example:{private_port}".encode(),
        )
        assert private_streams[0].closed is True

    def private_single_resolver(host: str, port: int, **_kwargs: Any):
        del host, _kwargs
        return [_addrinfo(PRIVATE_IP, port)]

    private_peer_stream = _ScriptedSyncStream("10.0.0.8")

    def private_peer_connector(
        address,
        timeout,
        local_address,
        socket_options,
    ):
        del address, timeout, local_address, socket_options
        return private_peer_stream

    monkeypatch.setattr(
        safe_http,
        "_system_resolver",
        private_single_resolver,
    )
    monkeypatch.setattr(
        safe_http,
        "_connect_sync_address",
        private_peer_connector,
    )
    _response, peer_error = _capture_sync(issue_private_request)
    _assert_safe_error(peer_error, "connected_peer_mismatch")
    assert private_peer_stream.closed is True
    assert private_peer_stream.tls_snapshots == []
    assert private_peer_stream.writes == []

    private_tls_drift_stream = _ScriptedSyncStream(
        PRIVATE_IP,
        [_http_ok()],
        peer_after_tls={1: "10.0.0.8"},
    )

    def private_tls_drift_connector(
        address,
        timeout,
        local_address,
        socket_options,
    ):
        del address, timeout, local_address, socket_options
        return private_tls_drift_stream

    monkeypatch.setattr(
        safe_http,
        "_connect_sync_address",
        private_tls_drift_connector,
    )
    _response, drift_error = _capture_sync(issue_private_request)
    _assert_safe_error(drift_error, "connected_peer_mismatch")
    assert len(private_tls_drift_stream.tls_children) == 1
    private_tls_child = private_tls_drift_stream.tls_children[0]
    assert private_tls_child is not private_tls_drift_stream
    assert private_tls_drift_stream.peer_ip == PRIVATE_IP
    assert private_tls_child.peer_ip == "10.0.0.8"
    assert private_tls_drift_stream.closed is True
    assert private_tls_drift_stream.tls_snapshots == [
        (ssl.CERT_REQUIRED, True)
    ]
    assert private_tls_drift_stream.writes == []

    for label, blocked_ip, expected_reason in _permanent_blocked_cases():
        blocked_resolves: list[str] = []
        blocked_connects: list[safe_http.PinnedAddress] = []

        def blocked_private_resolver(
            host: str,
            port: int,
            **_kwargs: Any,
        ):
            del _kwargs
            blocked_resolves.append(host)
            return [
                _addrinfo(PRIVATE_IP, port),
                _addrinfo(blocked_ip, port),
                _addrinfo("192.168.1.7", port),
            ]

        def forbidden_private_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del timeout, local_address, socket_options
            blocked_connects.append(address)
            return _ScriptedSyncStream(address.canonical_ip)

        monkeypatch.setattr(
            safe_http,
            "_system_resolver",
            blocked_private_resolver,
        )
        monkeypatch.setattr(
            safe_http,
            "_connect_sync_address",
            forbidden_private_connector,
        )
        _response, blocked_error = _capture_sync(issue_private_request)
        _assert_safe_error(blocked_error, expected_reason)
        assert blocked_resolves == [ORIGIN_HOST]
        assert blocked_connects == [], (
            f"PRIVATE_DIRECT connected {label} answer in mixed set"
        )

    _assert_sync_wrapper_contract(monkeypatch, proxy=False)


def test_async_public_direct_builder_and_request_context_enforce_policy(
    monkeypatch,
) -> None:
    _assert_public_api_signatures()

    async def scenario() -> None:
        for answers in (
            [PRIVATE_IP, PUBLIC_IP],
            [PUBLIC_IP, PRIVATE_IP],
        ):
            resolves: list[str] = []
            connects: list[safe_http.PinnedAddress] = []

            def mixed_resolver(host: str, port: int, **_kwargs: Any):
                resolves.append(host)
                return [_addrinfo(ip, port) for ip in answers]

            async def blocked_connector(
                address,
                timeout,
                local_address,
                socket_options,
            ):
                del timeout, local_address, socket_options
                connects.append(address)
                return _ScriptedAsyncStream(address.canonical_ip)

            monkeypatch.setattr(safe_http, "_system_resolver", mixed_resolver)
            monkeypatch.setattr(
                safe_http,
                "_connect_async_address",
                blocked_connector,
            )

            async def issue_mixed_request():
                transport = safe_http.build_openai_async_transport(
                    network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
                )
                async with httpx.AsyncClient(
                    transport=transport,
                    trust_env=False,
                    follow_redirects=False,
                ) as client:
                    return await client.get(
                        f"https://{ORIGIN_HOST}/v1/models"
                    )

            _response, mixed_error = await _capture_async(issue_mixed_request)
            _assert_safe_error(mixed_error, "outbound_address_blocked")
            assert resolves == [ORIGIN_HOST]
            assert connects == [], (
                f"mixed DNS answers {answers!r} must reject before every connect"
            )

        direct_block_cases = tuple(
            (label, ip, "outbound_address_blocked")
            for label, ip in _public_only_blocked_cases()
        ) + _permanent_blocked_cases()
        for label, blocked_ip, expected_reason in direct_block_cases:
            blocked_resolves: list[str] = []
            blocked_connects: list[safe_http.PinnedAddress] = []

            def permanently_blocked_resolver(
                host: str,
                port: int,
                **_kwargs: Any,
            ):
                blocked_resolves.append(host)
                return [
                    _addrinfo(PUBLIC_IP, port),
                    _addrinfo(blocked_ip, port),
                    _addrinfo(OTHER_PUBLIC_IP, port),
                ]

            async def permanently_blocked_connector(
                address,
                timeout,
                local_address,
                socket_options,
            ):
                del timeout, local_address, socket_options
                blocked_connects.append(address)
                return _ScriptedAsyncStream(address.canonical_ip)

            monkeypatch.setattr(
                safe_http,
                "_system_resolver",
                permanently_blocked_resolver,
            )
            monkeypatch.setattr(
                safe_http,
                "_connect_async_address",
                permanently_blocked_connector,
            )
            _response, blocked_error = await _capture_async(
                issue_mixed_request
            )
            _assert_safe_error(blocked_error, expected_reason)
            assert blocked_resolves == [ORIGIN_HOST]
            assert blocked_connects == [], (
                f"async PUBLIC_DIRECT connected {label} mixed answer"
            )

        mismatch_stream = _ScriptedAsyncStream(OTHER_PUBLIC_IP)
        mismatch_connects: list[safe_http.PinnedAddress] = []

        def public_resolver(host: str, port: int, **_kwargs: Any):
            del host, _kwargs
            return [_addrinfo(PUBLIC_IP, port)]

        async def mismatch_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del timeout, local_address, socket_options
            mismatch_connects.append(address)
            return mismatch_stream

        monkeypatch.setattr(safe_http, "_system_resolver", public_resolver)
        monkeypatch.setattr(
            safe_http,
            "_connect_async_address",
            mismatch_connector,
        )

        async def issue_mismatch_request():
            transport = safe_http.build_openai_async_transport(
                network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
            )
            async with httpx.AsyncClient(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                return await client.get(f"https://{ORIGIN_HOST}/v1/models")

        _response, mismatch_error = await _capture_async(issue_mismatch_request)
        _assert_safe_error(mismatch_error, "connected_peer_mismatch")
        assert len(mismatch_connects) == 1
        assert mismatch_stream.closed is True
        assert mismatch_stream.writes == []
        assert mismatch_stream.sni_hostnames == []
        assert mismatch_stream.tls_snapshots == []

        tls_drift_stream = _ScriptedAsyncStream(
            PUBLIC_IP,
            [_http_ok()],
            peer_after_tls={1: OTHER_PUBLIC_IP},
        )

        async def tls_drift_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del address, timeout, local_address, socket_options
            return tls_drift_stream

        monkeypatch.setattr(safe_http, "_system_resolver", public_resolver)
        monkeypatch.setattr(
            safe_http,
            "_connect_async_address",
            tls_drift_connector,
        )
        _response, tls_drift_error = await _capture_async(
            issue_mismatch_request
        )
        _assert_safe_error(tls_drift_error, "connected_peer_mismatch")
        assert len(tls_drift_stream.tls_children) == 1
        tls_child = tls_drift_stream.tls_children[0]
        assert tls_child is not tls_drift_stream
        assert tls_drift_stream.peer_ip == PUBLIC_IP
        assert tls_child.peer_ip == OTHER_PUBLIC_IP
        assert tls_drift_stream.sni_hostnames == [ORIGIN_HOST]
        assert tls_drift_stream.tls_snapshots == [
            (ssl.CERT_REQUIRED, True)
        ]
        assert tls_drift_stream.closed is True
        assert tls_drift_stream.writes == []

        resolves: list[tuple[str, int]] = []
        success_connects: list[safe_http.PinnedAddress] = []
        streams: list[_ScriptedAsyncStream] = []

        def success_resolver(host: str, port: int, **_kwargs: Any):
            del _kwargs
            resolves.append((host, port))
            return [_addrinfo(PUBLIC_IP, port)]

        async def success_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del timeout, local_address, socket_options
            success_connects.append(address)
            stream = _ScriptedAsyncStream(address.canonical_ip, [_http_ok()])
            streams.append(stream)
            return stream

        monkeypatch.setattr(safe_http, "_system_resolver", success_resolver)
        monkeypatch.setattr(
            safe_http,
            "_connect_async_address",
            success_connector,
        )

        async def issue_success_request():
            transport = safe_http.build_openai_async_transport(
                network_scope=safe_http.NetworkScope.PUBLIC_DIRECT,
            )
            async with httpx.AsyncClient(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                return await client.get(
                    f"https://{ORIGIN_HOST}:8443/v1/models"
                )

        response, success_error = await _capture_async(issue_success_request)
        assert success_error is None, (
            f"safe async direct request failed: {success_error!r}"
        )
        assert response.status_code == 200
        assert resolves == [(ORIGIN_HOST, 8443)]
        assert len(success_connects) == 1
        assert success_connects[0].sockaddr[1] == 8443
        assert len(streams) == 1
        assert len(streams[0].tls_children) == 1
        assert streams[0].tls_children[0] is not streams[0]
        assert streams[0].sni_hostnames == [ORIGIN_HOST]
        assert len(streams[0].tls_snapshots) == 1
        _assert_secure_tls_snapshot(streams[0].tls_snapshots[0])
        frames = _http_request_frames(streams[0].writes)
        assert len(frames) == 1
        _assert_request_frame(
            frames[0],
            b"GET /v1/models HTTP/1.1",
            b"origin.example:8443",
        )
        assert PUBLIC_IP.encode() not in frames[0]
        assert streams[0].closed is True

        allowed_private = (
            ("rfc1918-10", PRIVATE_IP, 9541),
            ("rfc1918-172", "172.20.1.7", 9542),
            ("rfc1918-192", "192.168.1.7", 9544),
            ("loopback-v4", "127.0.0.1", 9545),
            ("loopback-v6", "::1", 9546),
            ("ula-fc00", "fc00::7", 9547),
            ("ula-fd00", "fd00::7", 9548),
        )
        for private_label, allowed_ip, private_port in allowed_private:
            private_resolves: list[tuple[str, int]] = []
            private_connects: list[safe_http.PinnedAddress] = []
            private_streams: list[_ScriptedAsyncStream] = []

            def private_resolver(host: str, port: int, **_kwargs: Any):
                del _kwargs
                private_resolves.append((host, port))
                return [_addrinfo(allowed_ip, port)]

            async def private_connector(
                address,
                timeout,
                local_address,
                socket_options,
            ):
                del timeout, local_address, socket_options
                private_connects.append(address)
                stream = _ScriptedAsyncStream(
                    address.canonical_ip,
                    [_http_ok(b'{"private":"async"}')],
                )
                private_streams.append(stream)
                return stream

            monkeypatch.setattr(
                safe_http,
                "_system_resolver",
                private_resolver,
            )
            monkeypatch.setattr(
                safe_http,
                "_connect_async_address",
                private_connector,
            )

            async def issue_private_request():
                transport = safe_http.build_openai_async_transport(
                    network_scope=safe_http.NetworkScope.PRIVATE_DIRECT,
                )
                async with httpx.AsyncClient(
                    transport=transport,
                    trust_env=False,
                    follow_redirects=False,
                ) as client:
                    return await client.get(
                        f"https://{ORIGIN_HOST}:{private_port}/private-health"
                    )

            private_response, private_error = await _capture_async(
                issue_private_request
            )
            assert private_error is None, (
                f"async PRIVATE_DIRECT rejected {allowed_ip}: "
                f"{private_error!r}"
            )
            assert private_response.status_code == 200
            assert private_resolves == [(ORIGIN_HOST, private_port)]
            assert [
                address.canonical_ip for address in private_connects
            ] == [allowed_ip]
            assert private_connects[0].sockaddr[1] == private_port, (
                f"async PRIVATE_DIRECT lost {private_label} port "
                f"{private_port}"
            )
            assert private_streams[0].sni_hostnames == [ORIGIN_HOST]
            assert len(private_streams[0].tls_children) == 1
            assert (
                private_streams[0].tls_children[0]
                is not private_streams[0]
            )
            _assert_secure_tls_snapshot(
                private_streams[0].tls_snapshots[0]
            )
            private_frames = _http_request_frames(
                private_streams[0].writes
            )
            _assert_request_frame(
                private_frames[0],
                b"GET /private-health HTTP/1.1",
                f"origin.example:{private_port}".encode(),
            )
            assert private_streams[0].closed is True

        def private_single_resolver(
            host: str,
            port: int,
            **_kwargs: Any,
        ):
            del host, _kwargs
            return [_addrinfo(PRIVATE_IP, port)]

        private_peer_stream = _ScriptedAsyncStream("10.0.0.8")

        async def private_peer_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del address, timeout, local_address, socket_options
            return private_peer_stream

        monkeypatch.setattr(
            safe_http,
            "_system_resolver",
            private_single_resolver,
        )
        monkeypatch.setattr(
            safe_http,
            "_connect_async_address",
            private_peer_connector,
        )
        _response, peer_error = await _capture_async(
            issue_private_request
        )
        _assert_safe_error(peer_error, "connected_peer_mismatch")
        assert private_peer_stream.closed is True
        assert private_peer_stream.tls_snapshots == []
        assert private_peer_stream.writes == []

        private_tls_drift_stream = _ScriptedAsyncStream(
            PRIVATE_IP,
            [_http_ok()],
            peer_after_tls={1: "10.0.0.8"},
        )

        async def private_tls_drift_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del address, timeout, local_address, socket_options
            return private_tls_drift_stream

        monkeypatch.setattr(
            safe_http,
            "_connect_async_address",
            private_tls_drift_connector,
        )
        _response, drift_error = await _capture_async(
            issue_private_request
        )
        _assert_safe_error(drift_error, "connected_peer_mismatch")
        assert len(private_tls_drift_stream.tls_children) == 1
        private_tls_child = private_tls_drift_stream.tls_children[0]
        assert private_tls_child is not private_tls_drift_stream
        assert private_tls_drift_stream.peer_ip == PRIVATE_IP
        assert private_tls_child.peer_ip == "10.0.0.8"
        assert private_tls_drift_stream.closed is True
        assert private_tls_drift_stream.tls_snapshots == [
            (ssl.CERT_REQUIRED, True)
        ]
        assert private_tls_drift_stream.writes == []

        for label, blocked_ip, expected_reason in _permanent_blocked_cases():
            blocked_resolves: list[str] = []
            blocked_connects: list[safe_http.PinnedAddress] = []

            def blocked_private_resolver(
                host: str,
                port: int,
                **_kwargs: Any,
            ):
                del _kwargs
                blocked_resolves.append(host)
                return [
                    _addrinfo(PRIVATE_IP, port),
                    _addrinfo(blocked_ip, port),
                    _addrinfo("192.168.1.7", port),
                ]

            async def forbidden_private_connector(
                address,
                timeout,
                local_address,
                socket_options,
            ):
                del timeout, local_address, socket_options
                blocked_connects.append(address)
                return _ScriptedAsyncStream(address.canonical_ip)

            monkeypatch.setattr(
                safe_http,
                "_system_resolver",
                blocked_private_resolver,
            )
            monkeypatch.setattr(
                safe_http,
                "_connect_async_address",
                forbidden_private_connector,
            )
            _response, blocked_error = await _capture_async(
                issue_private_request
            )
            _assert_safe_error(blocked_error, expected_reason)
            assert blocked_resolves == [ORIGIN_HOST]
            assert blocked_connects == [], (
                f"async PRIVATE_DIRECT connected {label} mixed answer"
            )

        await _assert_async_wrapper_contract(monkeypatch, proxy=False)

    asyncio.run(scenario())


def test_sync_bounded_json_reader_rejects_invalid_lengths_and_stops_early() -> None:
    def run(headers: Any, chunks: list[bytes], max_bytes: int):
        body = _CountingSyncBody(chunks)
        response = httpx.Response(200, headers=headers, stream=body)
        result, error = _capture_sync(
            lambda: safe_http.read_bounded_json(
                response,
                max_bytes=max_bytes,
            )
        )
        response.close()
        return result, error, body

    for headers in (
        {},
        {"Content-Type": "text/html"},
        {"Content-Type": "application/jsonp"},
        {"Content-Type": "text/json"},
        {"Content-Type": "application/problem+json.evil"},
        {"Content-Type": "application/jsonx"},
        [
            ("Content-Type", "application/json"),
            ("Content-Type", "application/problem+json"),
        ],
    ):
        _result, error, body = run(
            headers,
            [b'{"token":"sk-mime-secret"}'],
            64,
        )
        _assert_safe_error(error, "provider_response_invalid_mime")
        assert body.consumed == 0
        assert "sk-mime-secret" not in f"{error!s} {error!r}"

    _result, error, body = run(
        {"Content-Type": "application/json", "Content-Length": "65"},
        [b"{}"],
        64,
    )
    _assert_safe_error(error, "provider_response_too_large")
    assert body.consumed == 0

    for headers in (
        {"Content-Type": "application/json", "Content-Length": "abc"},
        {"Content-Type": "application/json", "Content-Length": "-1"},
        [
            ("Content-Type", "application/json"),
            ("Content-Length", "1"),
            ("Content-Length", "2"),
        ],
    ):
        _result, error, body = run(
            headers,
            [b'{"must":"not be read or parsed","token":"sk-length-secret"}'],
            256,
        )
        _assert_safe_error(error, "provider_response_invalid_length")
        assert body.consumed == 0
        assert "sk-length-secret" not in f"{error!s} {error!r}"

    _result, error, body = run(
        {"Content-Type": "application/json", "Content-Length": "1"},
        [b"12345678", b"123456789", b"must-not-be-consumed"],
        16,
    )
    _assert_safe_error(error, "provider_response_too_large")
    assert body.consumed == 2

    _result, error, body = run(
        {"Content-Type": "application/json"},
        [b"12345678", b"123456789", b"must-not-be-consumed"],
        16,
    )
    _assert_safe_error(error, "provider_response_too_large")
    assert body.consumed == 2

    invalid = b'{"token":"sk-invalid-secret"'
    _result, error, body = run(
        {
            "Content-Type": "application/json",
            "Content-Length": str(len(invalid)),
        },
        [invalid],
        128,
    )
    _assert_safe_error(error, "provider_response_invalid_json")
    assert body.consumed == 1
    assert "sk-invalid-secret" not in f"{error!s} {error!r}"

    chunked_valid = b'{"sync":{"items":[1,{"nested":true}]}}'
    result, error, body = run(
        {"Content-Type": "application/json"},
        [chunked_valid[:11], chunked_valid[11:]],
        128,
    )
    assert error is None, f"valid no-length JSON failed: {error!r}"
    assert result == {"sync": {"items": [1, {"nested": True}]}}
    assert body.consumed == 2

    valid = b'["sync",{"ok":true}]'
    result, error, body = run(
        {
            "Content-Type": "application/problem+json; charset=utf-8",
            "Content-Length": str(len(valid)),
        },
        [valid],
        128,
    )
    assert error is None, f"valid +json response failed: {error!r}"
    assert result == ["sync", {"ok": True}]
    assert body.consumed == 1


def test_async_bounded_json_reader_rejects_invalid_lengths_and_stops_early() -> None:
    async def scenario() -> None:
        async def run(headers: Any, chunks: list[bytes], max_bytes: int):
            body = _CountingAsyncBody(chunks)
            response = httpx.Response(200, headers=headers, stream=body)
            result, error = await _capture_async(
                lambda: safe_http.read_bounded_json_async(
                    response,
                    max_bytes=max_bytes,
                )
            )
            await response.aclose()
            return result, error, body

        for headers in (
            {},
            {"Content-Type": "text/html"},
            {"Content-Type": "application/jsonp"},
            {"Content-Type": "text/json"},
            {"Content-Type": "application/problem+json.evil"},
            {"Content-Type": "application/jsonx"},
            [
                ("Content-Type", "application/json"),
                ("Content-Type", "application/vnd.api+json"),
            ],
        ):
            _result, error, body = await run(
                headers,
                [b'{"token":"sk-mime-secret"}'],
                64,
            )
            _assert_safe_error(error, "provider_response_invalid_mime")
            assert body.consumed == 0
            assert "sk-mime-secret" not in f"{error!s} {error!r}"

        _result, error, body = await run(
            {"Content-Type": "application/json", "Content-Length": "65"},
            [b"{}"],
            64,
        )
        _assert_safe_error(error, "provider_response_too_large")
        assert body.consumed == 0

        for headers in (
            {"Content-Type": "application/json", "Content-Length": "abc"},
            {"Content-Type": "application/json", "Content-Length": "-1"},
            [
                ("Content-Type", "application/json"),
                ("Content-Length", "1"),
                ("Content-Length", "2"),
            ],
        ):
            _result, error, body = await run(
                headers,
                [
                    b'{"must":"not be read or parsed",'
                    b'"token":"sk-length-secret"}'
                ],
                256,
            )
            _assert_safe_error(error, "provider_response_invalid_length")
            assert body.consumed == 0
            assert "sk-length-secret" not in f"{error!s} {error!r}"

        _result, error, body = await run(
            {"Content-Type": "application/json", "Content-Length": "1"},
            [b"12345678", b"123456789", b"must-not-be-consumed"],
            16,
        )
        _assert_safe_error(error, "provider_response_too_large")
        assert body.consumed == 2

        _result, error, body = await run(
            {"Content-Type": "application/vnd.api+json"},
            [b"abcdefgh", b"ijklmnopq", b"must-not-be-consumed"],
            16,
        )
        _assert_safe_error(error, "provider_response_too_large")
        assert body.consumed == 2

        invalid = b'{"token":"sk-invalid-secret"'
        _result, error, body = await run(
            {
                "Content-Type": "application/json",
                "Content-Length": str(len(invalid)),
            },
            [invalid],
            128,
        )
        _assert_safe_error(error, "provider_response_invalid_json")
        assert body.consumed == 1
        assert "sk-invalid-secret" not in f"{error!s} {error!r}"

        chunked_valid = b'{"async":{"flags":[false,true],"count":2}}'
        result, error, body = await run(
            {"Content-Type": "application/vnd.api+json"},
            [chunked_valid[:13], chunked_valid[13:]],
            128,
        )
        assert error is None, (
            f"valid async no-length +json failed: {error!r}"
        )
        assert result == {
            "async": {"flags": [False, True], "count": 2}
        }
        assert body.consumed == 2

        valid = b'{"async-valid":{"value":42}}'
        result, error, body = await run(
            {
                "Content-Type": "application/problem+json; charset=utf-8",
                "Content-Length": str(len(valid)),
            },
            [valid],
            128,
        )
        assert error is None, f"valid async +json response failed: {error!r}"
        assert result == {"async-valid": {"value": 42}}
        assert body.consumed == 1

    asyncio.run(scenario())


def test_sync_public_proxy_failures_are_sanitized_and_never_fall_back(
    monkeypatch,
) -> None:
    profile_calls: list[str] = []
    _install_named_profile_resolver(monkeypatch, profile_calls)
    scenarios = [
        (
            "custom-policy",
            "499 CUSTOM_ORIGIN_DENIED",
            None,
            None,
            "trusted_proxy_origin_blocked",
            1,
            1,
        ),
        (
            "custom-policy",
            "499 WRONG_REASON",
            None,
            None,
            "trusted_proxy_unavailable",
            1,
            1,
        ),
        (
            "custom-policy",
            "451 CUSTOM_ORIGIN_DENIED",
            None,
            None,
            "trusted_proxy_unavailable",
            1,
            1,
        ),
        (
            "approved-test",
            "407 Proxy Authentication Required",
            None,
            None,
            "trusted_proxy_unavailable",
            1,
            1,
        ),
        (
            "approved-test",
            "502 Bad Gateway",
            None,
            None,
            "trusted_proxy_unavailable",
            1,
            1,
        ),
        (
            "approved-test",
            None,
            httpcore.ConnectTimeout("sk-timeout-secret"),
            None,
            "trusted_proxy_unavailable",
            0,
            0,
        ),
        (
            "approved-test",
            None,
            None,
            PROXY_HOST,
            "trusted_proxy_unavailable",
            1,
            0,
        ),
        (
            "approved-test",
            None,
            None,
            ORIGIN_HOST,
            "trusted_proxy_unavailable",
            2,
            1,
        ),
    ]
    for (
        profile_name,
        status_line,
        connector_error,
        fail_tls_for,
        expected_reason,
        expected_tls_count,
        expected_frame_count,
    ) in scenarios:
        events: list[tuple[str, str, int]] = []
        streams: list[_ScriptedSyncStream] = []

        def resolver(host: str, port: int, **_kwargs: Any):
            events.append(("resolve", host, port))
            assert host == PROXY_HOST, (
                "proxy failure must never resolve the origin locally"
            )
            return [_addrinfo(PUBLIC_IP, port)]

        def connector(address, timeout, local_address, socket_options):
            del timeout, local_address, socket_options
            events.append(
                ("connect", address.canonical_ip, address.sockaddr[1])
            )
            if connector_error is not None:
                raise connector_error
            if status_line is not None:
                reads = [_proxy_failure(status_line)]
            elif fail_tls_for == ORIGIN_HOST:
                reads = [_connect_ok()]
            else:
                reads = []
            stream = _ScriptedSyncStream(
                address.canonical_ip,
                reads,
                fail_tls_for=fail_tls_for,
            )
            streams.append(stream)
            return stream

        monkeypatch.setattr(safe_http, "_system_resolver", resolver)
        monkeypatch.setattr(safe_http, "_connect_sync_address", connector)

        def issue_request():
            transport = safe_http.build_openai_sync_transport(
                network_scope=safe_http.NetworkScope.TRUSTED_PROXY,
                trusted_proxy_profile=profile_name,
            )
            with httpx.Client(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                return client.get(
                    f"https://{ORIGIN_HOST}:9443/v1/models",
                    headers={"Authorization": "Bearer origin-secret"},
                )

        _response, error = _capture_sync(issue_request)
        _assert_safe_error(error, expected_reason)
        rendered = f"{error!s} {error!r}"
        assert "sk-" not in rendered
        assert "secret" not in rendered.lower()
        assert events == [
            ("resolve", PROXY_HOST, 8443),
            ("connect", PUBLIC_IP, 8443),
        ]
        contexts = [
            context
            for stream in streams
            for context in stream.tls_snapshots
        ]
        assert len(contexts) == expected_tls_count
        for context in contexts:
            _assert_secure_tls_snapshot(context)
        if streams:
            assert streams[0].closed is True
            frames = _http_request_frames(streams[0].writes)
            assert len(frames) == expected_frame_count
            if frames:
                _assert_request_frame(
                    frames[0],
                    b"CONNECT origin.example:9443 HTTP/1.1",
                    b"origin.example:9443",
                )
                connect_lower = frames[0].lower()
                assert b"authorization:" not in connect_lower
                assert b"proxy-authorization:" not in connect_lower
                assert b"origin-secret" not in connect_lower
                assert all(
                    not frame.startswith(b"GET ")
                    for frame in frames
                )
        assert all(
            event[1] != ORIGIN_HOST
            for event in events
        )
    assert profile_calls == [scenario[0] for scenario in scenarios]


def test_async_public_proxy_failures_are_sanitized_and_never_fall_back(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        profile_calls: list[str] = []
        _install_named_profile_resolver(monkeypatch, profile_calls)
        scenarios = [
            (
                "custom-policy",
                "499 CUSTOM_ORIGIN_DENIED",
                None,
                None,
                "trusted_proxy_origin_blocked",
                1,
                1,
            ),
            (
                "custom-policy",
                "499 WRONG_REASON",
                None,
                None,
                "trusted_proxy_unavailable",
                1,
                1,
            ),
            (
                "custom-policy",
                "451 CUSTOM_ORIGIN_DENIED",
                None,
                None,
                "trusted_proxy_unavailable",
                1,
                1,
            ),
            (
                "approved-test",
                "407 Proxy Authentication Required",
                None,
                None,
                "trusted_proxy_unavailable",
                1,
                1,
            ),
            (
                "approved-test",
                "502 Bad Gateway",
                None,
                None,
                "trusted_proxy_unavailable",
                1,
                1,
            ),
            (
                "approved-test",
                None,
                httpcore.ConnectTimeout("sk-timeout-secret"),
                None,
                "trusted_proxy_unavailable",
                0,
                0,
            ),
            (
                "approved-test",
                None,
                None,
                PROXY_HOST,
                "trusted_proxy_unavailable",
                1,
                0,
            ),
            (
                "approved-test",
                None,
                None,
                ORIGIN_HOST,
                "trusted_proxy_unavailable",
                2,
                1,
            ),
        ]
        for (
            profile_name,
            status_line,
            connector_error,
            fail_tls_for,
            expected_reason,
            expected_tls_count,
            expected_frame_count,
        ) in scenarios:
            events: list[tuple[str, str, int]] = []
            streams: list[_ScriptedAsyncStream] = []

            def resolver(host: str, port: int, **_kwargs: Any):
                events.append(("resolve", host, port))
                assert host == PROXY_HOST, (
                    "proxy failure must never resolve the origin locally"
                )
                return [_addrinfo(PUBLIC_IP, port)]

            async def connector(
                address,
                timeout,
                local_address,
                socket_options,
            ):
                del timeout, local_address, socket_options
                events.append(
                    ("connect", address.canonical_ip, address.sockaddr[1])
                )
                if connector_error is not None:
                    raise connector_error
                if status_line is not None:
                    reads = [_proxy_failure(status_line)]
                elif fail_tls_for == ORIGIN_HOST:
                    reads = [_connect_ok()]
                else:
                    reads = []
                stream = _ScriptedAsyncStream(
                    address.canonical_ip,
                    reads,
                    fail_tls_for=fail_tls_for,
                )
                streams.append(stream)
                return stream

            monkeypatch.setattr(safe_http, "_system_resolver", resolver)
            monkeypatch.setattr(
                safe_http,
                "_connect_async_address",
                connector,
            )

            async def issue_request():
                transport = safe_http.build_openai_async_transport(
                    network_scope=safe_http.NetworkScope.TRUSTED_PROXY,
                    trusted_proxy_profile=profile_name,
                )
                async with httpx.AsyncClient(
                    transport=transport,
                    trust_env=False,
                    follow_redirects=False,
                ) as client:
                    return await client.get(
                        f"https://{ORIGIN_HOST}:9443/v1/models",
                        headers={"Authorization": "Bearer origin-secret"},
                    )

            _response, error = await _capture_async(issue_request)
            _assert_safe_error(error, expected_reason)
            rendered = f"{error!s} {error!r}"
            assert "sk-" not in rendered
            assert "secret" not in rendered.lower()
            assert events == [
                ("resolve", PROXY_HOST, 8443),
                ("connect", PUBLIC_IP, 8443),
            ]
            contexts = [
                context
                for stream in streams
                for context in stream.tls_snapshots
            ]
            assert len(contexts) == expected_tls_count
            for context in contexts:
                _assert_secure_tls_snapshot(context)
            if streams:
                assert streams[0].closed is True
                frames = _http_request_frames(streams[0].writes)
                assert len(frames) == expected_frame_count
                if frames:
                    _assert_request_frame(
                        frames[0],
                        b"CONNECT origin.example:9443 HTTP/1.1",
                        b"origin.example:9443",
                    )
                    connect_lower = frames[0].lower()
                    assert b"authorization:" not in connect_lower
                    assert b"proxy-authorization:" not in connect_lower
                    assert b"origin-secret" not in connect_lower
                    assert all(
                        not frame.startswith(b"GET ")
                        for frame in frames
                    )
            assert all(
                event[1] != ORIGIN_HOST
                for event in events
            )
        assert profile_calls == [scenario[0] for scenario in scenarios]

    asyncio.run(scenario())


def test_sync_public_proxy_builder_and_request_context_preserve_tunnel(
    monkeypatch,
) -> None:
    _assert_public_api_signatures()
    _assert_invalid_proxy_profile_mappings_are_rejected()
    profile_calls: list[str] = []
    _install_named_profile_resolver(monkeypatch, profile_calls)
    resolve_calls: list[tuple[str, int]] = []
    success_connects: list[safe_http.PinnedAddress] = []
    streams: list[_ScriptedSyncStream] = []

    def resolver(host: str, port: int, **_kwargs: Any):
        resolve_calls.append((host, port))
        assert host == PROXY_HOST, "origin hostname must not be resolved locally"
        return [_addrinfo(PUBLIC_IP, port)]

    def success_connector(address, timeout, local_address, socket_options):
        del timeout, local_address, socket_options
        success_connects.append(address)
        stream = _ScriptedSyncStream(
            address.canonical_ip,
            [_connect_ok(), _http_ok()],
        )
        streams.append(stream)
        return stream

    monkeypatch.setattr(safe_http, "_system_resolver", resolver)
    monkeypatch.setattr(
        safe_http,
        "_connect_sync_address",
        success_connector,
    )

    def issue_request(profile_name: object = "approved-test"):
        transport = safe_http.build_openai_sync_transport(
            network_scope=safe_http.NetworkScope.TRUSTED_PROXY,
            trusted_proxy_profile=profile_name,
        )
        with httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            return client.get(
                f"https://{ORIGIN_HOST}:9443/v1/models",
                headers={"Authorization": "Bearer origin-secret"},
            )

    for invalid_profile in (
        None,
        "",
        "missing",
        "unapproved",
        "missing-public-egress",
        "missing-dns-classification",
        "https://caller.invalid:9443",
    ):
        before_resolve = len(resolve_calls)
        before_streams = len(streams)
        _response, invalid_error = _capture_sync(
            lambda name=invalid_profile: issue_request(name)
        )
        _assert_safe_error(invalid_error, "trusted_proxy_unavailable")
        assert len(resolve_calls) == before_resolve
        assert len(streams) == before_streams
    assert profile_calls == [
        "None",
        "",
        "missing",
        "unapproved",
        "missing-public-egress",
        "missing-dns-classification",
        "https://caller.invalid:9443",
    ]

    proxy_answer_cases = [
        ("private-first", [PRIVATE_IP, PUBLIC_IP]),
        ("private-last", [PUBLIC_IP, PRIVATE_IP]),
    ]
    proxy_answer_cases.extend(
        (
            label,
            [PUBLIC_IP, blocked_ip, OTHER_PUBLIC_IP],
        )
        for label, blocked_ip in _public_only_blocked_cases()
    )
    proxy_answer_cases.extend(
        (
            label,
            [PUBLIC_IP, blocked_ip, OTHER_PUBLIC_IP],
        )
        for label, blocked_ip, _direct_reason in _permanent_blocked_cases()
    )
    for label, proxy_answers in proxy_answer_cases:
        blocked_resolves: list[tuple[str, int]] = []
        blocked_connects: list[safe_http.PinnedAddress] = []

        def blocked_proxy_resolver(host: str, port: int, **_kwargs: Any):
            blocked_resolves.append((host, port))
            return [_addrinfo(ip, port) for ip in proxy_answers]

        def blocked_proxy_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del timeout, local_address, socket_options
            blocked_connects.append(address)
            return _ScriptedSyncStream(address.canonical_ip)

        monkeypatch.setattr(
            safe_http,
            "_system_resolver",
            blocked_proxy_resolver,
        )
        monkeypatch.setattr(
            safe_http,
            "_connect_sync_address",
            blocked_proxy_connector,
        )
        _response, blocked_error = _capture_sync(issue_request)
        _assert_safe_error(blocked_error, "trusted_proxy_unavailable")
        assert blocked_resolves == [(PROXY_HOST, 8443)]
        assert blocked_connects == [], (
            f"unsafe proxy answers connected for {label}: {proxy_answers!r}"
        )

    monkeypatch.setattr(safe_http, "_system_resolver", resolver)
    peer_mismatch_stream = _ScriptedSyncStream(OTHER_PUBLIC_IP)

    def peer_mismatch_connector(
        address,
        timeout,
        local_address,
        socket_options,
    ):
        del address, timeout, local_address, socket_options
        return peer_mismatch_stream

    monkeypatch.setattr(
        safe_http,
        "_connect_sync_address",
        peer_mismatch_connector,
    )
    _response, peer_error = _capture_sync(issue_request)
    _assert_safe_error(peer_error, "connected_peer_mismatch")
    assert peer_mismatch_stream.closed is True
    assert peer_mismatch_stream.tls_snapshots == []
    assert peer_mismatch_stream.writes == []

    proxy_tls_drift_stream = _ScriptedSyncStream(
        PUBLIC_IP,
        [_connect_ok(), _http_ok()],
        peer_after_tls={1: OTHER_PUBLIC_IP},
    )

    def proxy_tls_drift_connector(
        address,
        timeout,
        local_address,
        socket_options,
    ):
        del address, timeout, local_address, socket_options
        return proxy_tls_drift_stream

    monkeypatch.setattr(
        safe_http,
        "_connect_sync_address",
        proxy_tls_drift_connector,
    )
    _response, drift_error = _capture_sync(issue_request)
    _assert_safe_error(drift_error, "connected_peer_mismatch")
    assert len(proxy_tls_drift_stream.tls_children) == 1
    proxy_tls_child = proxy_tls_drift_stream.tls_children[0]
    assert proxy_tls_child is not proxy_tls_drift_stream
    assert proxy_tls_drift_stream.peer_ip == PUBLIC_IP
    assert proxy_tls_child.peer_ip == OTHER_PUBLIC_IP
    assert proxy_tls_drift_stream.closed is True
    assert proxy_tls_drift_stream.tls_snapshots == [
        (ssl.CERT_REQUIRED, True)
    ]
    assert proxy_tls_drift_stream.writes == []

    origin_tls_drift_stream = _ScriptedSyncStream(
        PUBLIC_IP,
        [_connect_ok(), _http_ok()],
        peer_after_tls={2: OTHER_PUBLIC_IP},
    )

    def origin_tls_drift_connector(
        address,
        timeout,
        local_address,
        socket_options,
    ):
        del address, timeout, local_address, socket_options
        return origin_tls_drift_stream

    monkeypatch.setattr(
        safe_http,
        "_connect_sync_address",
        origin_tls_drift_connector,
    )
    _response, drift_error = _capture_sync(issue_request)
    _assert_safe_error(drift_error, "connected_peer_mismatch")
    assert len(origin_tls_drift_stream.tls_children) == 2
    proxy_tls_child, origin_tls_child = (
        origin_tls_drift_stream.tls_children
    )
    assert proxy_tls_child is not origin_tls_drift_stream
    assert origin_tls_child is not origin_tls_drift_stream
    assert origin_tls_child is not proxy_tls_child
    assert origin_tls_drift_stream.peer_ip == PUBLIC_IP
    assert proxy_tls_child.peer_ip == PUBLIC_IP
    assert origin_tls_child.peer_ip == OTHER_PUBLIC_IP
    assert origin_tls_drift_stream.closed is True
    assert origin_tls_drift_stream.tls_snapshots == [
        (ssl.CERT_REQUIRED, True),
        (ssl.CERT_REQUIRED, True),
    ]
    drift_frames = _http_request_frames(origin_tls_drift_stream.writes)
    assert len(drift_frames) == 1
    _assert_request_frame(
        drift_frames[0],
        b"CONNECT origin.example:9443 HTTP/1.1",
        b"origin.example:9443",
    )

    resolve_calls.clear()
    success_connects.clear()
    streams.clear()
    monkeypatch.setattr(safe_http, "_system_resolver", resolver)
    monkeypatch.setattr(
        safe_http,
        "_connect_sync_address",
        success_connector,
    )

    response, error = _capture_sync(issue_request)
    assert error is None, f"approved proxy request failed: {error!r}"
    assert response.status_code == 200
    assert resolve_calls == [(PROXY_HOST, 8443)]
    assert len(success_connects) == 1
    assert success_connects[0].sockaddr[1] == 8443
    assert len(streams) == 1
    assert streams[0].peer_ip == PUBLIC_IP
    assert len(streams[0].tls_children) == 2
    assert streams[0].tls_children[0] is not streams[0]
    assert streams[0].tls_children[1] is not streams[0]
    assert (
        streams[0].tls_children[1] is not streams[0].tls_children[0]
    )
    assert streams[0].sni_hostnames == [PROXY_HOST, ORIGIN_HOST]
    assert len(streams[0].tls_snapshots) == 2
    for snapshot in streams[0].tls_snapshots:
        _assert_secure_tls_snapshot(snapshot)
    frames = _http_request_frames(streams[0].writes)
    assert len(frames) == 2
    _assert_request_frame(
        frames[0],
        b"CONNECT origin.example:9443 HTTP/1.1",
        b"origin.example:9443",
    )
    _assert_request_frame(
        frames[1],
        b"GET /v1/models HTTP/1.1",
        b"origin.example:9443",
    )
    connect_lower = frames[0].lower()
    assert b"authorization:" not in connect_lower
    assert b"proxy-authorization:" not in connect_lower
    assert b"origin-secret" not in connect_lower
    origin_lines = frames[1][:-4].split(b"\r\n")
    authorization_lines = [
        line
        for line in origin_lines[1:]
        if line.lower().startswith(b"authorization:")
    ]
    assert authorization_lines == [b"Authorization: Bearer origin-secret"]
    assert all(
        not line.lower().startswith(b"proxy-authorization:")
        for line in origin_lines[1:]
    )
    assert PUBLIC_IP.encode() not in b"".join(frames)
    assert streams[0].closed is True
    _assert_sync_wrapper_contract(monkeypatch, proxy=True)


def test_async_public_proxy_builder_and_request_context_preserve_tunnel(
    monkeypatch,
) -> None:
    _assert_public_api_signatures()
    _assert_invalid_proxy_profile_mappings_are_rejected()

    async def scenario() -> None:
        profile_calls: list[str] = []
        _install_named_profile_resolver(monkeypatch, profile_calls)
        resolve_calls: list[tuple[str, int]] = []
        success_connects: list[safe_http.PinnedAddress] = []
        streams: list[_ScriptedAsyncStream] = []

        def resolver(host: str, port: int, **_kwargs: Any):
            resolve_calls.append((host, port))
            assert host == PROXY_HOST, (
                "origin hostname must not be resolved locally"
            )
            return [_addrinfo(PUBLIC_IP, port)]

        async def success_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del timeout, local_address, socket_options
            success_connects.append(address)
            stream = _ScriptedAsyncStream(
                address.canonical_ip,
                [_connect_ok(), _http_ok()],
            )
            streams.append(stream)
            return stream

        monkeypatch.setattr(safe_http, "_system_resolver", resolver)
        monkeypatch.setattr(
            safe_http,
            "_connect_async_address",
            success_connector,
        )

        async def issue_request(profile_name: object = "approved-test"):
            transport = safe_http.build_openai_async_transport(
                network_scope=safe_http.NetworkScope.TRUSTED_PROXY,
                trusted_proxy_profile=profile_name,
            )
            async with httpx.AsyncClient(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                return await client.get(
                    f"https://{ORIGIN_HOST}:9443/v1/models",
                    headers={"Authorization": "Bearer origin-secret"},
                )

        for invalid_profile in (
            None,
            "",
            "missing",
            "unapproved",
            "missing-public-egress",
            "missing-dns-classification",
            "https://caller.invalid:9443",
        ):
            before_resolve = len(resolve_calls)
            before_streams = len(streams)
            _response, invalid_error = await _capture_async(
                lambda name=invalid_profile: issue_request(name)
            )
            _assert_safe_error(
                invalid_error,
                "trusted_proxy_unavailable",
            )
            assert len(resolve_calls) == before_resolve
            assert len(streams) == before_streams
        assert profile_calls == [
            "None",
            "",
            "missing",
            "unapproved",
            "missing-public-egress",
            "missing-dns-classification",
            "https://caller.invalid:9443",
        ]

        proxy_answer_cases = [
            ("private-first", [PRIVATE_IP, PUBLIC_IP]),
            ("private-last", [PUBLIC_IP, PRIVATE_IP]),
        ]
        proxy_answer_cases.extend(
            (
                label,
                [PUBLIC_IP, blocked_ip, OTHER_PUBLIC_IP],
            )
            for label, blocked_ip in _public_only_blocked_cases()
        )
        proxy_answer_cases.extend(
            (
                label,
                [PUBLIC_IP, blocked_ip, OTHER_PUBLIC_IP],
            )
            for (
                label,
                blocked_ip,
                _direct_reason,
            ) in _permanent_blocked_cases()
        )
        for label, proxy_answers in proxy_answer_cases:
            blocked_resolves: list[tuple[str, int]] = []
            blocked_connects: list[safe_http.PinnedAddress] = []

            def blocked_proxy_resolver(
                host: str,
                port: int,
                **_kwargs: Any,
            ):
                blocked_resolves.append((host, port))
                return [_addrinfo(ip, port) for ip in proxy_answers]

            async def blocked_proxy_connector(
                address,
                timeout,
                local_address,
                socket_options,
            ):
                del timeout, local_address, socket_options
                blocked_connects.append(address)
                return _ScriptedAsyncStream(address.canonical_ip)

            monkeypatch.setattr(
                safe_http,
                "_system_resolver",
                blocked_proxy_resolver,
            )
            monkeypatch.setattr(
                safe_http,
                "_connect_async_address",
                blocked_proxy_connector,
            )
            _response, blocked_error = await _capture_async(issue_request)
            _assert_safe_error(
                blocked_error,
                "trusted_proxy_unavailable",
            )
            assert blocked_resolves == [(PROXY_HOST, 8443)]
            assert blocked_connects == [], (
                f"unsafe async proxy answers for {label}: "
                f"{proxy_answers!r}"
            )

        monkeypatch.setattr(safe_http, "_system_resolver", resolver)
        peer_mismatch_stream = _ScriptedAsyncStream(OTHER_PUBLIC_IP)

        async def peer_mismatch_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del address, timeout, local_address, socket_options
            return peer_mismatch_stream

        monkeypatch.setattr(
            safe_http,
            "_connect_async_address",
            peer_mismatch_connector,
        )
        _response, peer_error = await _capture_async(issue_request)
        _assert_safe_error(peer_error, "connected_peer_mismatch")
        assert peer_mismatch_stream.closed is True
        assert peer_mismatch_stream.tls_snapshots == []
        assert peer_mismatch_stream.writes == []

        proxy_tls_drift_stream = _ScriptedAsyncStream(
            PUBLIC_IP,
            [_connect_ok(), _http_ok()],
            peer_after_tls={1: OTHER_PUBLIC_IP},
        )

        async def proxy_tls_drift_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del address, timeout, local_address, socket_options
            return proxy_tls_drift_stream

        monkeypatch.setattr(
            safe_http,
            "_connect_async_address",
            proxy_tls_drift_connector,
        )
        _response, drift_error = await _capture_async(issue_request)
        _assert_safe_error(drift_error, "connected_peer_mismatch")
        assert len(proxy_tls_drift_stream.tls_children) == 1
        proxy_tls_child = proxy_tls_drift_stream.tls_children[0]
        assert proxy_tls_child is not proxy_tls_drift_stream
        assert proxy_tls_drift_stream.peer_ip == PUBLIC_IP
        assert proxy_tls_child.peer_ip == OTHER_PUBLIC_IP
        assert proxy_tls_drift_stream.closed is True
        assert proxy_tls_drift_stream.tls_snapshots == [
            (ssl.CERT_REQUIRED, True)
        ]
        assert proxy_tls_drift_stream.writes == []

        origin_tls_drift_stream = _ScriptedAsyncStream(
            PUBLIC_IP,
            [_connect_ok(), _http_ok()],
            peer_after_tls={2: OTHER_PUBLIC_IP},
        )

        async def origin_tls_drift_connector(
            address,
            timeout,
            local_address,
            socket_options,
        ):
            del address, timeout, local_address, socket_options
            return origin_tls_drift_stream

        monkeypatch.setattr(
            safe_http,
            "_connect_async_address",
            origin_tls_drift_connector,
        )
        _response, drift_error = await _capture_async(issue_request)
        _assert_safe_error(drift_error, "connected_peer_mismatch")
        assert len(origin_tls_drift_stream.tls_children) == 2
        proxy_tls_child, origin_tls_child = (
            origin_tls_drift_stream.tls_children
        )
        assert proxy_tls_child is not origin_tls_drift_stream
        assert origin_tls_child is not origin_tls_drift_stream
        assert origin_tls_child is not proxy_tls_child
        assert origin_tls_drift_stream.peer_ip == PUBLIC_IP
        assert proxy_tls_child.peer_ip == PUBLIC_IP
        assert origin_tls_child.peer_ip == OTHER_PUBLIC_IP
        assert origin_tls_drift_stream.closed is True
        assert origin_tls_drift_stream.tls_snapshots == [
            (ssl.CERT_REQUIRED, True),
            (ssl.CERT_REQUIRED, True),
        ]
        drift_frames = _http_request_frames(
            origin_tls_drift_stream.writes
        )
        assert len(drift_frames) == 1
        _assert_request_frame(
            drift_frames[0],
            b"CONNECT origin.example:9443 HTTP/1.1",
            b"origin.example:9443",
        )

        resolve_calls.clear()
        success_connects.clear()
        streams.clear()
        monkeypatch.setattr(safe_http, "_system_resolver", resolver)
        monkeypatch.setattr(
            safe_http,
            "_connect_async_address",
            success_connector,
        )

        response, error = await _capture_async(issue_request)
        assert error is None, f"approved async proxy request failed: {error!r}"
        assert response.status_code == 200
        assert resolve_calls == [(PROXY_HOST, 8443)]
        assert len(success_connects) == 1
        assert success_connects[0].sockaddr[1] == 8443
        assert len(streams) == 1
        assert streams[0].peer_ip == PUBLIC_IP
        assert len(streams[0].tls_children) == 2
        assert streams[0].tls_children[0] is not streams[0]
        assert streams[0].tls_children[1] is not streams[0]
        assert (
            streams[0].tls_children[1]
            is not streams[0].tls_children[0]
        )
        assert streams[0].sni_hostnames == [PROXY_HOST, ORIGIN_HOST]
        assert len(streams[0].tls_snapshots) == 2
        for snapshot in streams[0].tls_snapshots:
            _assert_secure_tls_snapshot(snapshot)
        frames = _http_request_frames(streams[0].writes)
        assert len(frames) == 2
        _assert_request_frame(
            frames[0],
            b"CONNECT origin.example:9443 HTTP/1.1",
            b"origin.example:9443",
        )
        _assert_request_frame(
            frames[1],
            b"GET /v1/models HTTP/1.1",
            b"origin.example:9443",
        )
        connect_lower = frames[0].lower()
        assert b"authorization:" not in connect_lower
        assert b"proxy-authorization:" not in connect_lower
        assert b"origin-secret" not in connect_lower
        origin_lines = frames[1][:-4].split(b"\r\n")
        authorization_lines = [
            line
            for line in origin_lines[1:]
            if line.lower().startswith(b"authorization:")
        ]
        assert authorization_lines == [
            b"Authorization: Bearer origin-secret"
        ]
        assert all(
            not line.lower().startswith(b"proxy-authorization:")
            for line in origin_lines[1:]
        )
        assert PUBLIC_IP.encode() not in b"".join(frames)
        assert streams[0].closed is True
        await _assert_async_wrapper_contract(monkeypatch, proxy=True)

    asyncio.run(scenario())
