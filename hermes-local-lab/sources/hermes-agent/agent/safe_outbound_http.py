"""Fail-closed, DNS-pinned outbound HTTP transports.

The public builders intentionally expose only policy names.  Resolver,
connector, proxy, and TLS details stay private so callers cannot bypass address
classification or peer verification.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
    cast,
)
from urllib.parse import urlsplit

import anyio
import httpcore
import httpx
import yaml
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver

from hermes_constants import get_config_path


class NetworkScope(str, Enum):
    PUBLIC_DIRECT = "public_direct"
    PRIVATE_DIRECT = "private_direct"
    TRUSTED_PROXY = "trusted_proxy"


class SafeOutboundError(RuntimeError):
    """Stable, redacted outbound failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or "safe_transport_unavailable")
        super().__init__(f"safe outbound request blocked: {self.reason_code}")


@dataclass(frozen=True)
class TrustedProxyProfile:
    name: str
    proxy_url: str
    approved: bool
    capabilities: frozenset[str]
    proxy_connect_scope: NetworkScope = NetworkScope.PUBLIC_DIRECT
    credential_env: str | None = None
    policy_denial_status: int = 451
    policy_denial_reason: str = "TAIJI_ORIGIN_BLOCKED"


@dataclass(frozen=True)
class PinnedAddress:
    family: int
    socktype: int
    protocol: int
    sockaddr: tuple[Any, ...]
    canonical_ip: str


_AddrInfo = tuple[int, int, int, str, tuple[Any, ...]]
_Resolver = Callable[..., Sequence[_AddrInfo]]
_SyncConnector = Callable[
    [
        PinnedAddress,
        float | None,
        str | None,
        Iterable[httpcore.SOCKET_OPTION] | None,
    ],
    httpcore.NetworkStream,
]
_AsyncConnector = Callable[
    [
        PinnedAddress,
        float | None,
        str | None,
        Iterable[httpcore.SOCKET_OPTION] | None,
    ],
    Awaitable[httpcore.AsyncNetworkStream],
]

_RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_IPV6_ULA = ipaddress.ip_network("fc00::/7")
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_DOCUMENTATION_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("2001:db8::/32"),
    ipaddress.ip_network("2001:2::/48"),
)
_IPV6_METADATA = ipaddress.ip_address("fd00:ec2::254")
_GCP_IPV6_METADATA = ipaddress.ip_address("fd20:ce::254")
_METADATA_HOSTNAMES = frozenset({
    "instance-data.ec2.internal",
    "metadata.google.internal",
    "metadata.goog",
})
_REQUIRED_PROXY_CAPABILITIES = frozenset({"public_egress", "dns_ip_classification"})
_PROXY_CONFIG_MAX_BYTES = 2 * 1024 * 1024
_PROXY_PROFILE_REQUIRED_FIELDS = frozenset({
    "name",
    "proxy_url",
    "approved",
    "capabilities",
    "proxy_connect_scope",
})
_PROXY_PROFILE_ALLOWED_FIELDS = _PROXY_PROFILE_REQUIRED_FIELDS | {
    "policy_denial_status",
    "policy_denial_reason",
}
_MEDIA_TOKEN_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&'*+-.^_`|~"
)


class _StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: Any,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError from error
        if duplicate:
            raise ConstructorError
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _unavailable() -> SafeOutboundError:
    return SafeOutboundError("safe_transport_unavailable")


def _system_resolver(host: str, port: int, **kwargs: Any) -> Sequence[_AddrInfo]:
    del kwargs
    return socket.getaddrinfo(
        host,
        port,
        socket.AF_UNSPEC,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
    )


def _connect_sync_address(
    address: PinnedAddress,
    timeout: float | None,
    local_address: str | None,
    socket_options: Iterable[httpcore.SOCKET_OPTION] | None,
) -> httpcore.NetworkStream:
    return httpcore.SyncBackend().connect_tcp(
        address.canonical_ip,
        int(address.sockaddr[1]),
        timeout=timeout,
        local_address=local_address,
        socket_options=socket_options,
    )


async def _connect_async_address(
    address: PinnedAddress,
    timeout: float | None,
    local_address: str | None,
    socket_options: Iterable[httpcore.SOCKET_OPTION] | None,
) -> httpcore.AsyncNetworkStream:
    backend = cast(httpcore.AsyncNetworkBackend, httpcore.AnyIOBackend())
    return await backend.connect_tcp(
        address.canonical_ip,
        int(address.sockaddr[1]),
        timeout=timeout,
        local_address=local_address,
        socket_options=socket_options,
    )


def _effective_ip(
    value: str | ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = (
        value
        if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address))
        else ipaddress.ip_address(value)
    )
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _legacy_ipv4_literal(host: str) -> ipaddress.IPv4Address | None:
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    if not all(
        part
        and (
            (part.isascii() and part.isdigit())
            or (
                part.lower().startswith("0x")
                and len(part) > 2
                and all(
                    character in "0123456789abcdef" for character in part.lower()[2:]
                )
            )
        )
        for part in parts
    ):
        return None

    values: list[int] = []
    for part in parts:
        if not part or len(part) > 12:
            raise ValueError
        lowered = part.lower()
        if lowered.startswith("0x"):
            digits = lowered[2:]
            if not digits or any(
                character not in "0123456789abcdef" for character in digits
            ):
                raise ValueError
            base = 16
        elif len(part) > 1 and part.startswith("0"):
            if any(character not in "01234567" for character in part):
                raise ValueError
            base = 8
        elif part.isascii() and part.isdigit():
            base = 10
        else:
            return None
        values.append(int(part, base))

    maxima = {
        1: (0xFFFFFFFF,),
        2: (0xFF, 0xFFFFFF),
        3: (0xFF, 0xFF, 0xFFFF),
        4: (0xFF, 0xFF, 0xFF, 0xFF),
    }[len(values)]
    if any(value > maximum for value, maximum in zip(values, maxima, strict=True)):
        raise ValueError

    if len(values) == 1:
        packed = values[0]
    elif len(values) == 2:
        packed = (values[0] << 24) | values[1]
    elif len(values) == 3:
        packed = (values[0] << 24) | (values[1] << 16) | values[2]
    else:
        packed = (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | values[3]
    return ipaddress.IPv4Address(packed)


def _canonical_origin_host(
    host: str,
) -> tuple[
    str,
    ipaddress.IPv4Address | ipaddress.IPv6Address | None,
]:
    if (
        not isinstance(host, str)
        or not host
        or "%" in host
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in host)
    ):
        raise ValueError
    host.encode("ascii")
    if host.endswith(".."):
        raise ValueError
    normalized = host[:-1] if host.endswith(".") else host
    normalized = normalized.lower()
    if not normalized or len(normalized) > 253:
        raise ValueError

    try:
        literal = ipaddress.ip_address(normalized)
    except ValueError:
        if _legacy_ipv4_literal(normalized) is not None:
            raise ValueError
        labels = normalized.split(".")
        if any(
            not label
            or len(label) > 63
            or label[0] == "-"
            or label[-1] == "-"
            or any(
                not (character.isascii() and (character.isalnum() or character == "-"))
                for character in label
            )
            for label in labels
        ):
            raise ValueError
        return normalized, None
    if isinstance(literal, ipaddress.IPv6Address) and literal.scope_id is not None:
        raise ValueError
    return normalized, literal


def _is_permanently_blocked(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str | None:
    effective = _effective_ip(address)
    if isinstance(effective, ipaddress.IPv4Address) and effective in _FAKE_IP_NETWORK:
        return "fake_ip_requires_trusted_proxy"
    # Reject IPv4-mapped IPv6 spellings instead of letting them inherit
    # ``private_direct`` allowances from their embedded IPv4 address.  Keeping
    # one canonical address family at the policy boundary prevents mapped
    # loopback/RFC1918 forms from becoming an alternate representation bypass.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return "outbound_address_blocked"
    if address in {_IPV6_METADATA, _GCP_IPV6_METADATA}:
        return "outbound_address_blocked"
    if (
        effective.is_unspecified
        or effective.is_multicast
        or effective.is_link_local
        or getattr(effective, "is_site_local", False)
        or (effective.is_reserved and not effective.is_loopback)
        or effective in _CGNAT_NETWORK
        or any(effective in network for network in _DOCUMENTATION_NETWORKS)
    ):
        return "outbound_address_blocked"
    return None


def _is_explicit_private(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    effective = _effective_ip(address)
    if isinstance(effective, ipaddress.IPv4Address):
        return effective.is_loopback or any(
            effective in network for network in _RFC1918_NETWORKS
        )
    return effective.is_loopback or effective in _IPV6_ULA


def _validate_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    scope: NetworkScope,
) -> None:
    blocked_reason = _is_permanently_blocked(address)
    if blocked_reason:
        raise SafeOutboundError(blocked_reason)
    effective = _effective_ip(address)
    if scope is NetworkScope.PUBLIC_DIRECT:
        if not effective.is_global:
            raise SafeOutboundError("outbound_address_blocked")
        return
    if scope is NetworkScope.PRIVATE_DIRECT:
        if not effective.is_global and not _is_explicit_private(address):
            raise SafeOutboundError("outbound_address_blocked")
        return
    raise _unavailable()


def normalize_network_scope(
    value: object,
    *,
    default: NetworkScope = NetworkScope.PUBLIC_DIRECT,
) -> NetworkScope:
    if value is None:
        return default
    if isinstance(value, NetworkScope):
        return value
    if isinstance(value, str):
        try:
            return NetworkScope(value)
        except ValueError:
            pass
    raise _unavailable()


def _canonical_config_path() -> Path:
    path = get_config_path()
    if not path.is_absolute():
        raise ValueError
    return path


def _load_trusted_proxy_profiles() -> dict[str, TrustedProxyProfile]:
    from agent.provider_credentials import credential_transaction

    config_path = _canonical_config_path()
    with credential_transaction(config_path):
        with config_path.open("rb") as config_stream:
            raw_config = config_stream.read(_PROXY_CONFIG_MAX_BYTES + 1)
        if len(raw_config) > _PROXY_CONFIG_MAX_BYTES:
            raise ValueError
        loaded = yaml.load(
            raw_config.decode("utf-8"),
            Loader=_StrictSafeLoader,
        )
    if loaded is None:
        return {}
    if type(loaded) is not dict:
        raise ValueError
    rows = loaded.get("trusted_proxy_profiles")
    if rows is None:
        return {}
    if type(rows) is not list:
        raise ValueError

    profiles: dict[str, TrustedProxyProfile] = {}
    for row in rows:
        if type(row) is not dict:
            raise ValueError
        fields = set(row)
        if not _PROXY_PROFILE_REQUIRED_FIELDS.issubset(fields) or not fields.issubset(
            _PROXY_PROFILE_ALLOWED_FIELDS
        ):
            raise ValueError

        name = row["name"]
        proxy_url = row["proxy_url"]
        approved = row["approved"]
        capabilities = row["capabilities"]
        proxy_connect_scope = row["proxy_connect_scope"]
        policy_denial_status = row.get("policy_denial_status", 451)
        policy_denial_reason = row.get(
            "policy_denial_reason",
            "TAIJI_ORIGIN_BLOCKED",
        )
        if (
            type(name) is not str
            or type(proxy_url) is not str
            or type(approved) is not bool
            or type(capabilities) is not list
            or not all(type(capability) is str for capability in capabilities)
            or len(capabilities) != len(set(capabilities))
            or type(proxy_connect_scope) is not str
            or type(policy_denial_status) is not int
            or type(policy_denial_reason) is not str
            or name in profiles
        ):
            raise ValueError

        profiles[name] = TrustedProxyProfile(
            name=name,
            proxy_url=proxy_url,
            approved=approved,
            capabilities=frozenset(capabilities),
            proxy_connect_scope=normalize_network_scope(proxy_connect_scope),
            policy_denial_status=policy_denial_status,
            policy_denial_reason=policy_denial_reason,
        )
    for profile_name in profiles:
        resolve_trusted_proxy_profile(profile_name, profiles=profiles)
    return profiles


def resolve_trusted_proxy_profile(
    name: object,
    *,
    profiles: Mapping[str, TrustedProxyProfile] | None = None,
) -> TrustedProxyProfile:
    try:
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 64
            or name.strip() != name
            or not name[0].isalnum()
            or not name[-1].isalnum()
            or any(
                not (
                    character.isascii() and (character.isalnum() or character in "._-")
                )
                for character in name
            )
        ):
            raise ValueError
        profile_map = (
            profiles if profiles is not None else _load_trusted_proxy_profiles()
        )
        profile = profile_map.get(name)
        if not isinstance(profile, TrustedProxyProfile):
            raise ValueError
        if profile.name != name or profile.approved is not True:
            raise ValueError
        if not _REQUIRED_PROXY_CAPABILITIES.issubset(profile.capabilities):
            raise ValueError
        if profile.proxy_connect_scope is None:
            raise ValueError
        scope = normalize_network_scope(profile.proxy_connect_scope)
        if scope not in (
            NetworkScope.PUBLIC_DIRECT,
            NetworkScope.PRIVATE_DIRECT,
        ):
            raise ValueError
        if not isinstance(profile.proxy_url, str) or any(
            ord(character) <= 0x20 or ord(character) == 0x7F
            for character in profile.proxy_url
        ):
            raise ValueError
        parsed = urlsplit(profile.proxy_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError
        hostname, literal = _canonical_origin_host(parsed.hostname)
        if hostname in _METADATA_HOSTNAMES:
            raise ValueError
        parsed_port = parsed.port
        port = 443 if parsed_port is None else parsed_port
        if port != 443 and not 1024 <= port <= 65535:
            raise ValueError
        if literal is not None:
            _validate_address(literal, scope)
        if profile.credential_env is not None:
            if not isinstance(profile.credential_env, str) or profile.credential_env:
                raise ValueError
        if (
            not isinstance(profile.policy_denial_status, int)
            or isinstance(profile.policy_denial_status, bool)
            or not 400 <= profile.policy_denial_status <= 599
            or not isinstance(profile.policy_denial_reason, str)
            or not profile.policy_denial_reason
            or any(
                character not in _MEDIA_TOKEN_CHARS
                for character in profile.policy_denial_reason
            )
        ):
            raise ValueError
        return profile
    except Exception:
        raise SafeOutboundError("trusted_proxy_unavailable") from None


def _resolve_pinned_addresses(
    host: str,
    port: int,
    *,
    network_scope: NetworkScope | str,
    resolver: _Resolver,
) -> tuple[PinnedAddress, ...]:
    scope = normalize_network_scope(network_scope)
    if scope is NetworkScope.TRUSTED_PROXY:
        raise _unavailable()
    if (
        not isinstance(host, str)
        or not host
        or not isinstance(port, int)
        or isinstance(port, bool)
    ):
        raise _unavailable()
    if not 1 <= port <= 65535:
        raise _unavailable()
    try:
        answers = resolver(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except SafeOutboundError:
        raise
    except Exception:
        raise _unavailable() from None
    if not answers:
        raise _unavailable()

    pinned: list[PinnedAddress] = []
    seen: set[tuple[int, str, int, int]] = set()
    for answer in answers:
        try:
            family, socktype, protocol, _canonname, sockaddr = answer
            if family not in (socket.AF_INET, socket.AF_INET6):
                raise ValueError
            if socktype not in (0, socket.SOCK_STREAM):
                raise ValueError
            if protocol not in (0, socket.IPPROTO_TCP):
                raise ValueError
            parsed = ipaddress.ip_address(str(sockaddr[0]))
            if family == socket.AF_INET and not isinstance(
                parsed, ipaddress.IPv4Address
            ):
                raise ValueError
            if family == socket.AF_INET6 and not isinstance(
                parsed, ipaddress.IPv6Address
            ):
                raise ValueError
            if family == socket.AF_INET6 and (
                getattr(parsed, "scope_id", None) is not None
                or (len(sockaddr) > 3 and int(sockaddr[3]) != 0)
            ):
                raise ValueError
            _validate_address(parsed, scope)
            canonical_ip = str(parsed)
            flowinfo = (
                int(sockaddr[2])
                if family == socket.AF_INET6 and len(sockaddr) > 2
                else 0
            )
            scope_id = (
                int(sockaddr[3])
                if family == socket.AF_INET6 and len(sockaddr) > 3
                else 0
            )
            normalized_sockaddr: tuple[Any, ...]
            if family == socket.AF_INET6:
                normalized_sockaddr = (
                    canonical_ip,
                    port,
                    flowinfo,
                    scope_id,
                )
            else:
                normalized_sockaddr = (canonical_ip, port)
            key = (family, canonical_ip, flowinfo, scope_id)
            if key in seen:
                continue
            seen.add(key)
            pinned.append(
                PinnedAddress(
                    family=family,
                    socktype=socket.SOCK_STREAM,
                    protocol=socket.IPPROTO_TCP,
                    sockaddr=normalized_sockaddr,
                    canonical_ip=canonical_ip,
                )
            )
        except SafeOutboundError:
            raise
        except Exception:
            raise _unavailable() from None
    if not pinned:
        raise _unavailable()
    return tuple(pinned)


def resolve_pinned_addresses(
    host: str,
    port: int,
    *,
    network_scope: NetworkScope | str,
) -> tuple[PinnedAddress, ...]:
    return _resolve_pinned_addresses(
        host,
        port,
        network_scope=network_scope,
        resolver=_system_resolver,
    )


def _stream_peer_ip(stream: object) -> str | None:
    get_extra_info = getattr(stream, "get_extra_info", None)
    if not callable(get_extra_info):
        return None
    try:
        server_addr = get_extra_info("server_addr")
        if isinstance(server_addr, tuple) and server_addr:
            return str(server_addr[0])
        sock = get_extra_info("socket")
        if sock is not None:
            peer = sock.getpeername()
            if isinstance(peer, tuple) and peer:
                return str(peer[0])
    except Exception:
        return None
    return None


def _peer_matches(stream: object, expected_ip: str) -> bool:
    peer_ip = _stream_peer_ip(stream)
    if peer_ip is None:
        return False
    try:
        return _effective_ip(peer_ip) == _effective_ip(expected_ip)
    except ValueError:
        return False


def _close_sync_stream(stream: object) -> None:
    try:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


async def _close_async_stream(stream: object) -> None:
    try:
        close = getattr(stream, "aclose", None)
        if callable(close):
            with anyio.CancelScope(shield=True):
                await close()
    except BaseException:
        pass


class _PinnedSyncStream(httpcore.NetworkStream):
    def __init__(
        self,
        stream: httpcore.NetworkStream,
        expected_ip: str,
    ) -> None:
        self._stream = stream
        self._expected_ip = expected_ip

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._stream.read(max_bytes, timeout=timeout)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._stream.write(buffer, timeout=timeout)

    def close(self) -> None:
        self._stream.close()

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        child: httpcore.NetworkStream | None = None
        try:
            child = self._stream.start_tls(
                ssl_context,
                server_hostname=server_hostname,
                timeout=timeout,
            )
            if not _peer_matches(child, self._expected_ip):
                _close_sync_stream(child)
                raise SafeOutboundError("connected_peer_mismatch")
            return _PinnedSyncStream(child, self._expected_ip)
        except SafeOutboundError:
            _close_sync_stream(self._stream)
            raise
        except BaseException:
            if child is not None:
                _close_sync_stream(child)
            _close_sync_stream(self._stream)
            raise

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


class _PinnedAsyncStream(httpcore.AsyncNetworkStream):
    def __init__(
        self,
        stream: httpcore.AsyncNetworkStream,
        expected_ip: str,
    ) -> None:
        self._stream = stream
        self._expected_ip = expected_ip

    async def read(
        self,
        max_bytes: int,
        timeout: float | None = None,
    ) -> bytes:
        return await self._stream.read(max_bytes, timeout=timeout)

    async def write(
        self,
        buffer: bytes,
        timeout: float | None = None,
    ) -> None:
        await self._stream.write(buffer, timeout=timeout)

    async def aclose(self) -> None:
        with anyio.CancelScope(shield=True):
            await self._stream.aclose()

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        child: httpcore.AsyncNetworkStream | None = None
        try:
            child = await self._stream.start_tls(
                ssl_context,
                server_hostname=server_hostname,
                timeout=timeout,
            )
            if not _peer_matches(child, self._expected_ip):
                await _close_async_stream(child)
                raise SafeOutboundError("connected_peer_mismatch")
            return _PinnedAsyncStream(child, self._expected_ip)
        except SafeOutboundError:
            await _close_async_stream(self._stream)
            raise
        except BaseException:
            if child is not None:
                await _close_async_stream(child)
            await _close_async_stream(self._stream)
            raise

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


class _PinnedSyncBackend(httpcore.NetworkBackend):
    def __init__(
        self,
        *,
        network_scope: NetworkScope,
        resolver: _Resolver = _system_resolver,
        connector: _SyncConnector = _connect_sync_address,
        policy_failure_reason: str | None = None,
    ) -> None:
        self.network_scope = network_scope
        self.resolver = resolver
        self.connector = connector
        self.policy_failure_reason = policy_failure_reason

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        try:
            addresses = _resolve_pinned_addresses(
                host,
                port,
                network_scope=self.network_scope,
                resolver=self.resolver,
            )
        except SafeOutboundError:
            if self.policy_failure_reason:
                raise SafeOutboundError(self.policy_failure_reason) from None
            raise
        address = addresses[0]
        stream = self.connector(
            address,
            timeout,
            local_address,
            socket_options,
        )
        if not _peer_matches(stream, address.canonical_ip):
            _close_sync_stream(stream)
            raise SafeOutboundError("connected_peer_mismatch")
        return _PinnedSyncStream(stream, address.canonical_ip)

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        del path, timeout, socket_options
        raise _unavailable()


class _PinnedAsyncBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        *,
        network_scope: NetworkScope,
        resolver: _Resolver = _system_resolver,
        connector: _AsyncConnector = _connect_async_address,
        policy_failure_reason: str | None = None,
    ) -> None:
        self.network_scope = network_scope
        self.resolver = resolver
        self.connector = connector
        self.policy_failure_reason = policy_failure_reason

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            addresses = _resolve_pinned_addresses(
                host,
                port,
                network_scope=self.network_scope,
                resolver=self.resolver,
            )
        except SafeOutboundError:
            if self.policy_failure_reason:
                raise SafeOutboundError(self.policy_failure_reason) from None
            raise
        address = addresses[0]
        stream = await self.connector(
            address,
            timeout,
            local_address,
            socket_options,
        )
        if not _peer_matches(stream, address.canonical_ip):
            await _close_async_stream(stream)
            raise SafeOutboundError("connected_peer_mismatch")
        return _PinnedAsyncStream(stream, address.canonical_ip)

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise _unavailable()


def _mapped_transport_error(
    error: Exception,
    proxy_profile: TrustedProxyProfile | None,
) -> SafeOutboundError:
    if isinstance(error, SafeOutboundError):
        return error
    if proxy_profile is not None:
        expected_denial = (
            f"{proxy_profile.policy_denial_status} {proxy_profile.policy_denial_reason}"
        )
        if isinstance(error, httpcore.ProxyError) and str(error) == expected_denial:
            return SafeOutboundError("trusted_proxy_origin_blocked")
        return SafeOutboundError("trusted_proxy_unavailable")
    return SafeOutboundError("safe_transport_unavailable")


def _sanitized_request_extensions(request: httpx.Request) -> dict[str, Any]:
    extensions = dict(request.extensions)
    sni_hostname = extensions.pop("sni_hostname", None)
    if sni_hostname is None:
        return extensions
    raw_sni = (
        sni_hostname
        if isinstance(sni_hostname, bytes)
        else str(sni_hostname).encode("ascii", errors="strict")
    )
    if raw_sni.rstrip(b".").lower() != request.url.raw_host.rstrip(b".").lower():
        raise _unavailable()
    return extensions


def _bound_core_request_headers(
    request: httpx.Request,
) -> list[tuple[bytes, bytes]]:
    expected_authority = request.url.netloc
    if any(
        name.lower() == b"proxy-authorization" for name, _value in request.headers.raw
    ):
        raise _unavailable()
    host_values = [
        value for name, value in request.headers.raw if name.lower() == b"host"
    ]
    if len(host_values) != 1 or host_values[0].lower() != expected_authority.lower():
        raise _unavailable()

    headers = [
        (name, value)
        for name, value in request.headers.raw
        if name.lower() not in {b"host", b"accept-encoding"}
    ]
    return [
        (b"Host", expected_authority),
        (b"Accept-Encoding", b"identity"),
        *headers,
    ]


def _validate_core_response_encoding(
    headers: Iterable[tuple[bytes, bytes]],
) -> None:
    content_encodings = [
        value.strip().lower()
        for name, value in headers
        if name.lower() == b"content-encoding"
    ]
    if len(content_encodings) > 1 or (
        content_encodings and content_encodings[0] != b"identity"
    ):
        raise SafeOutboundError("provider_response_invalid_encoding")


def _validate_trusted_proxy_origin(
    request: httpx.Request,
    proxy_profile: TrustedProxyProfile | None,
) -> None:
    port = 443 if request.url.port is None else request.url.port
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise _unavailable()
    if proxy_profile is None:
        return
    try:
        raw_host = request.url.raw_host.decode("ascii")
    except UnicodeError:
        raise _unavailable() from None
    try:
        normalized_host, literal = _canonical_origin_host(raw_host)
    except (UnicodeError, ValueError):
        raise _unavailable() from None
    if normalized_host in _METADATA_HOSTNAMES:
        raise SafeOutboundError("outbound_address_blocked")
    if literal is None:
        return
    _validate_address(literal, NetworkScope.PUBLIC_DIRECT)


class _CoreSyncResponseStream(httpx.SyncByteStream):
    def __init__(
        self,
        stream: Iterable[bytes],
        proxy_profile: TrustedProxyProfile | None,
    ) -> None:
        self._stream = stream
        self._proxy_profile = proxy_profile
        self._closed = False

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self._stream
        except Exception as error:
            self._close_quietly()
            raise _mapped_transport_error(error, self._proxy_profile) from None

    def _close_quietly(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self._stream, "close", None)
            if callable(close):
                close()
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self._stream, "close", None)
            if callable(close):
                close()
        except Exception as error:
            raise _mapped_transport_error(error, self._proxy_profile) from None


class _CoreAsyncResponseStream(httpx.AsyncByteStream):
    def __init__(
        self,
        stream: AsyncIterable[bytes],
        proxy_profile: TrustedProxyProfile | None,
    ) -> None:
        self._stream = stream
        self._proxy_profile = proxy_profile
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for part in self._stream:
                yield part
        except BaseException as error:
            await self._close_quietly()
            if not isinstance(error, Exception):
                raise
            raise _mapped_transport_error(error, self._proxy_profile) from None

    async def _close_quietly(self) -> None:
        if self._closed:
            return
        self._closed = True
        await _close_async_stream(self._stream)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self._stream, "aclose", None)
            if callable(close):
                with anyio.CancelScope(shield=True):
                    await close()
        except Exception as error:
            raise _mapped_transport_error(error, self._proxy_profile) from None


class _CoreSyncTransport(httpx.BaseTransport):
    def __init__(
        self,
        pool: httpcore.ConnectionPool | httpcore.HTTPProxy,
        proxy_profile: TrustedProxyProfile | None,
    ) -> None:
        self._pool = pool
        self._proxy_profile = proxy_profile

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.raw_scheme != b"https":
            raise _mapped_transport_error(_unavailable(), self._proxy_profile)
        core_response: httpcore.Response | None = None
        try:
            _validate_trusted_proxy_origin(
                request,
                self._proxy_profile,
            )
            core_request = httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=_bound_core_request_headers(request),
                content=request.stream,
                extensions=_sanitized_request_extensions(request),
            )
            core_response = self._pool.handle_request(core_request)
            try:
                _validate_core_response_encoding(core_response.headers)
                return httpx.Response(
                    status_code=core_response.status,
                    headers=core_response.headers,
                    stream=_CoreSyncResponseStream(
                        cast(Iterable[bytes], core_response.stream),
                        self._proxy_profile,
                    ),
                    extensions=core_response.extensions,
                )
            except Exception:
                _close_sync_stream(core_response.stream)
                raise
        except Exception as error:
            raise _mapped_transport_error(error, self._proxy_profile) from None

    def close(self) -> None:
        try:
            self._pool.close()
        except Exception as error:
            raise _mapped_transport_error(error, self._proxy_profile) from None


class _CoreAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        pool: httpcore.AsyncConnectionPool | httpcore.AsyncHTTPProxy,
        proxy_profile: TrustedProxyProfile | None,
    ) -> None:
        self._pool = pool
        self._proxy_profile = proxy_profile

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        if request.url.raw_scheme != b"https":
            raise _mapped_transport_error(_unavailable(), self._proxy_profile)
        core_response: httpcore.Response | None = None
        try:
            _validate_trusted_proxy_origin(
                request,
                self._proxy_profile,
            )
            core_request = httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=_bound_core_request_headers(request),
                content=request.stream,
                extensions=_sanitized_request_extensions(request),
            )
            core_response = await self._pool.handle_async_request(core_request)
            try:
                _validate_core_response_encoding(core_response.headers)
                return httpx.Response(
                    status_code=core_response.status,
                    headers=core_response.headers,
                    stream=_CoreAsyncResponseStream(
                        cast(AsyncIterable[bytes], core_response.stream),
                        self._proxy_profile,
                    ),
                    extensions=core_response.extensions,
                )
            except Exception:
                await _close_async_stream(core_response.stream)
                raise
        except Exception as error:
            raise _mapped_transport_error(error, self._proxy_profile) from None

    async def aclose(self) -> None:
        try:
            with anyio.CancelScope(shield=True):
                await self._pool.aclose()
        except Exception as error:
            raise _mapped_transport_error(error, self._proxy_profile) from None


def _secure_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return context


def _build_sync_transport(
    *,
    backend: httpcore.NetworkBackend,
    proxy_profile: TrustedProxyProfile | None = None,
) -> httpx.BaseTransport:
    origin_context = _secure_ssl_context()
    if proxy_profile is None:
        pool: httpcore.ConnectionPool | httpcore.HTTPProxy = httpcore.ConnectionPool(
            ssl_context=origin_context,
            http1=True,
            http2=False,
            retries=0,
            network_backend=backend,
        )
    else:
        pool = httpcore.HTTPProxy(
            proxy_url=proxy_profile.proxy_url,
            ssl_context=origin_context,
            proxy_ssl_context=_secure_ssl_context(),
            http1=True,
            http2=False,
            retries=0,
            network_backend=backend,
        )
    return _CoreSyncTransport(pool, proxy_profile)


def _build_async_transport(
    *,
    backend: httpcore.AsyncNetworkBackend,
    proxy_profile: TrustedProxyProfile | None = None,
) -> httpx.AsyncBaseTransport:
    origin_context = _secure_ssl_context()
    if proxy_profile is None:
        pool: httpcore.AsyncConnectionPool | httpcore.AsyncHTTPProxy = (
            httpcore.AsyncConnectionPool(
                ssl_context=origin_context,
                http1=True,
                http2=False,
                retries=0,
                network_backend=backend,
            )
        )
    else:
        pool = httpcore.AsyncHTTPProxy(
            proxy_url=proxy_profile.proxy_url,
            ssl_context=origin_context,
            proxy_ssl_context=_secure_ssl_context(),
            http1=True,
            http2=False,
            retries=0,
            network_backend=backend,
        )
    return _CoreAsyncTransport(pool, proxy_profile)


def build_openai_sync_transport(
    *,
    network_scope: NetworkScope | str,
    trusted_proxy_profile: str | None = None,
) -> httpx.BaseTransport:
    resolver = _system_resolver
    connector = _connect_sync_address
    scope = normalize_network_scope(network_scope)
    if scope is NetworkScope.TRUSTED_PROXY:
        profile = resolve_trusted_proxy_profile(trusted_proxy_profile)
        backend = _PinnedSyncBackend(
            network_scope=profile.proxy_connect_scope,
            resolver=resolver,
            connector=connector,
            policy_failure_reason="trusted_proxy_unavailable",
        )
        try:
            return _build_sync_transport(
                backend=backend,
                proxy_profile=profile,
            )
        except SafeOutboundError:
            raise
        except Exception:
            raise SafeOutboundError("trusted_proxy_unavailable") from None
    if trusted_proxy_profile is not None:
        raise _unavailable()
    backend = _PinnedSyncBackend(
        network_scope=scope,
        resolver=resolver,
        connector=connector,
    )
    try:
        return _build_sync_transport(backend=backend)
    except SafeOutboundError:
        raise
    except Exception:
        raise _unavailable() from None


def build_openai_async_transport(
    *,
    network_scope: NetworkScope | str,
    trusted_proxy_profile: str | None = None,
) -> httpx.AsyncBaseTransport:
    resolver = _system_resolver
    connector = _connect_async_address
    scope = normalize_network_scope(network_scope)
    if scope is NetworkScope.TRUSTED_PROXY:
        profile = resolve_trusted_proxy_profile(trusted_proxy_profile)
        backend = _PinnedAsyncBackend(
            network_scope=profile.proxy_connect_scope,
            resolver=resolver,
            connector=connector,
            policy_failure_reason="trusted_proxy_unavailable",
        )
        try:
            return _build_async_transport(
                backend=backend,
                proxy_profile=profile,
            )
        except SafeOutboundError:
            raise
        except Exception:
            raise SafeOutboundError("trusted_proxy_unavailable") from None
    if trusted_proxy_profile is not None:
        raise _unavailable()
    backend = _PinnedAsyncBackend(
        network_scope=scope,
        resolver=resolver,
        connector=connector,
    )
    try:
        return _build_async_transport(backend=backend)
    except SafeOutboundError:
        raise
    except Exception:
        raise _unavailable() from None


def _require_https_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = 443 if parsed.port is None else parsed.port
        hostname = parsed.hostname
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or not 1 <= port <= 65535
        ):
            raise ValueError
        normalized_host, _literal = _canonical_origin_host(hostname)
        if normalized_host in _METADATA_HOSTNAMES:
            raise ValueError
    except Exception:
        raise _unavailable() from None


def _identity_request_headers(
    headers: Mapping[str, str] | None,
) -> dict[str, str]:
    if any(str(key).lower() == "proxy-authorization" for key in (headers or {})):
        raise _unavailable()
    result = {
        str(key): str(value)
        for key, value in (headers or {}).items()
        if str(key).lower() != "accept-encoding"
    }
    result["Accept-Encoding"] = "identity"
    return result


@contextmanager
def request_pinned_https(
    method: str,
    url: str,
    *,
    network_scope: NetworkScope | str,
    headers: Mapping[str, str] | None = None,
    json_body: object | None = None,
    timeout: float = 30.0,
    follow_redirects: bool = False,
) -> Iterator[httpx.Response]:
    if follow_redirects:
        raise SafeOutboundError("redirects_not_allowed")
    _require_https_url(url)
    safe_headers = _identity_request_headers(headers)
    transport = build_openai_sync_transport(network_scope=network_scope)
    request_kwargs: dict[str, Any] = {
        "headers": safe_headers,
        "timeout": timeout,
    }
    if json_body is not None:
        request_kwargs["json"] = json_body
    with httpx.Client(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        with client.stream(method, url, **request_kwargs) as response:
            yield response


@contextmanager
def request_via_trusted_proxy(
    method: str,
    url: str,
    *,
    trusted_proxy_profile: str,
    headers: Mapping[str, str] | None = None,
    json_body: object | None = None,
    timeout: float = 30.0,
    follow_redirects: bool = False,
) -> Iterator[httpx.Response]:
    if follow_redirects:
        raise SafeOutboundError("redirects_not_allowed")
    _require_https_url(url)
    safe_headers = _identity_request_headers(headers)
    transport = build_openai_sync_transport(
        network_scope=NetworkScope.TRUSTED_PROXY,
        trusted_proxy_profile=trusted_proxy_profile,
    )
    request_kwargs: dict[str, Any] = {
        "headers": safe_headers,
        "timeout": timeout,
    }
    if json_body is not None:
        request_kwargs["json"] = json_body
    with httpx.Client(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        with client.stream(method, url, **request_kwargs) as response:
            yield response


@asynccontextmanager
async def request_pinned_https_async(
    method: str,
    url: str,
    *,
    network_scope: NetworkScope | str,
    headers: Mapping[str, str] | None = None,
    json_body: object | None = None,
    timeout: float = 30.0,
    follow_redirects: bool = False,
) -> AsyncIterator[httpx.Response]:
    if follow_redirects:
        raise SafeOutboundError("redirects_not_allowed")
    _require_https_url(url)
    safe_headers = _identity_request_headers(headers)
    transport = build_openai_async_transport(network_scope=network_scope)
    request_kwargs: dict[str, Any] = {
        "headers": safe_headers,
        "timeout": timeout,
    }
    if json_body is not None:
        request_kwargs["json"] = json_body
    async with httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        async with client.stream(method, url, **request_kwargs) as response:
            yield response


@asynccontextmanager
async def request_via_trusted_proxy_async(
    method: str,
    url: str,
    *,
    trusted_proxy_profile: str,
    headers: Mapping[str, str] | None = None,
    json_body: object | None = None,
    timeout: float = 30.0,
    follow_redirects: bool = False,
) -> AsyncIterator[httpx.Response]:
    if follow_redirects:
        raise SafeOutboundError("redirects_not_allowed")
    _require_https_url(url)
    safe_headers = _identity_request_headers(headers)
    transport = build_openai_async_transport(
        network_scope=NetworkScope.TRUSTED_PROXY,
        trusted_proxy_profile=trusted_proxy_profile,
    )
    request_kwargs: dict[str, Any] = {
        "headers": safe_headers,
        "timeout": timeout,
    }
    if json_body is not None:
        request_kwargs["json"] = json_body
    async with httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        async with client.stream(method, url, **request_kwargs) as response:
            yield response


def _validated_json_headers(
    response: httpx.Response,
    max_bytes: int,
) -> None:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise SafeOutboundError("provider_response_invalid_length")
    if max_bytes < 0:
        raise SafeOutboundError("provider_response_invalid_length")

    content_encodings = response.headers.get_list("content-encoding")
    if len(content_encodings) > 1 or (
        content_encodings and content_encodings[0].strip().lower() != "identity"
    ):
        raise SafeOutboundError("provider_response_invalid_encoding")

    content_types = response.headers.get_list("content-type")
    if len(content_types) != 1:
        raise SafeOutboundError("provider_response_invalid_mime")
    media_type = content_types[0].split(";", 1)[0].strip().lower()
    application_subtype = (
        media_type[len("application/") :]
        if media_type.startswith("application/")
        else ""
    )
    valid_subtype_token = bool(application_subtype) and all(
        character in _MEDIA_TOKEN_CHARS for character in application_subtype
    )
    if not (
        valid_subtype_token
        and (
            media_type == "application/json"
            or (
                application_subtype.endswith("+json")
                and bool(application_subtype[: -len("+json")])
            )
        )
    ):
        raise SafeOutboundError("provider_response_invalid_mime")

    content_lengths = response.headers.get_list("content-length")
    if len(content_lengths) > 1:
        raise SafeOutboundError("provider_response_invalid_length")
    if content_lengths:
        raw_length = content_lengths[0].strip()
        if not raw_length.isascii() or not raw_length.isdigit():
            raise SafeOutboundError("provider_response_invalid_length")
        declared_length = 0
        for character in raw_length:
            digit = ord(character) - ord("0")
            if declared_length > (max_bytes - digit) // 10:
                raise SafeOutboundError("provider_response_too_large")
            declared_length = declared_length * 10 + digit


def _decode_bounded_json(body: bytes | bytearray) -> Any:
    try:
        return json.loads(body)
    except Exception:
        raise SafeOutboundError("provider_response_invalid_json") from None


def read_bounded_json(
    response: httpx.Response,
    *,
    max_bytes: int = 2 * 1024 * 1024,
) -> Any:
    _validated_json_headers(response, max_bytes)
    body = bytearray()
    size = 0
    try:
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise SafeOutboundError("provider_response_too_large")
            body.extend(chunk)
    except SafeOutboundError:
        raise
    except Exception:
        raise SafeOutboundError("safe_transport_unavailable") from None
    return _decode_bounded_json(body)


async def read_bounded_json_async(
    response: httpx.Response,
    *,
    max_bytes: int = 2 * 1024 * 1024,
) -> Any:
    _validated_json_headers(response, max_bytes)
    body = bytearray()
    size = 0
    try:
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise SafeOutboundError("provider_response_too_large")
            body.extend(chunk)
    except SafeOutboundError:
        raise
    except Exception:
        raise SafeOutboundError("safe_transport_unavailable") from None
    return _decode_bounded_json(body)
