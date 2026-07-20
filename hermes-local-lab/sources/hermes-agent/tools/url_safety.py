"""URL safety checks — blocks requests to private/internal network addresses.

Prevents SSRF (Server-Side Request Forgery) where a malicious prompt or
skill could trick the agent into fetching internal resources like cloud
metadata endpoints (169.254.169.254), localhost services, or private
network hosts.

The ordinary private-address check can be disabled via
``security.allow_private_urls: true`` for explicit local-network workflows.
Cloud metadata, link-local, and the benchmark/Fake-IP range
``198.18.0.0/15`` remain a non-negotiable floor when addressed directly.
Transparent proxies that demonstrably map multiple independent public
hostnames into distinct Fake-IP addresses are treated as hostname-preserving
proxy indirection; metadata hostnames and direct Fake-IP literals still block.

Limitations (documented, not fixable at pre-flight level):
  - DNS rebinding (TOCTOU): an attacker-controlled DNS server with TTL=0
    can return a public IP for the check, then a private IP for the actual
    connection. Fixing this requires connection-level validation (e.g.
    Python's Champion library or an egress proxy like Stripe's Smokescreen).
  - Redirect-based bypass is mitigated by httpx event hooks that re-validate
    each redirect target in vision_tools, gateway platform adapters, and
    media cache helpers. Web tools use third-party SDKs (Firecrawl/Tavily)
    where redirect handling is on their servers.
"""

import ipaddress
import logging
import os
import socket
import threading
from urllib.parse import urlparse

from utils import is_truthy_value

logger = logging.getLogger(__name__)

# Hostnames that should always be blocked regardless of IP resolution
# or any config toggle.  These are cloud metadata endpoints that an
# attacker could use to steal instance credentials.
_BLOCKED_HOSTNAMES = frozenset({
    "instance-data.ec2.internal",
    "metadata.google.internal",
    "metadata.goog",
})

# IPs and networks that should always be blocked regardless of the
# allow_private_urls toggle.  These are cloud metadata / credential
# endpoints — the #1 SSRF target — and the link-local range where
# they all live.
#
# IPv4-mapped IPv6 variants are included because DNS resolvers may
# return ``::ffff:x.x.x.x`` for IPv4-only hosts, and Python's
# ipaddress module treats these as distinct from the plain IPv4
# address (they won't match ``ip in frozenset`` or ``ip in network``).
_ALWAYS_BLOCKED_IPS = frozenset({
    ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure/DO/Oracle metadata
    ipaddress.ip_address("169.254.170.2"),     # AWS ECS task metadata (task IAM creds)
    ipaddress.ip_address("169.254.169.253"),   # Azure IMDS wire server
    ipaddress.ip_address("fd00:ec2::254"),     # AWS metadata (IPv6)
    ipaddress.ip_address("fd20:ce::254"),       # GCP metadata (IPv6)
    ipaddress.ip_address("100.100.100.200"),   # Alibaba Cloud metadata
    # IPv4-mapped IPv6 variants — same endpoints reachable via ::ffff:x.x.x.x
    ipaddress.ip_address("::ffff:169.254.169.254"),
    ipaddress.ip_address("::ffff:169.254.170.2"),
    ipaddress.ip_address("::ffff:169.254.169.253"),
    ipaddress.ip_address("::ffff:100.100.100.200"),
})
_FAKE_IP_NETWORKS = (
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("::ffff:198.18.0.0/111"),
)
_ALWAYS_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),    # Entire link-local range (no legit agent target)
    ipaddress.ip_network("::ffff:169.254.0.0/112"), # IPv4-mapped link-local range
    *_FAKE_IP_NETWORKS,  # Benchmark/Fake-IP must never be a literal origin peer
)

# Exact HTTPS hostnames allowed to resolve to ordinary private-space IPs.
# This exception never bypasses the permanent metadata/Fake-IP floor above.
_TRUSTED_PRIVATE_IP_HOSTS = frozenset({
    "multimedia.nt.qq.com.cn",
})

# 100.64.0.0/10 (CGNAT / Shared Address Space, RFC 6598) is NOT covered by
# ipaddress.is_private — it returns False for both is_private and is_global.
# Must be blocked explicitly. Used by carrier-grade NAT, Tailscale/WireGuard
# VPNs, and some cloud internal networks.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# ---------------------------------------------------------------------------
# Global toggle: allow private/internal IP resolution
# ---------------------------------------------------------------------------
# Cached after first read so we don't hit the filesystem on every URL check.
_allow_private_resolved = False
_cached_allow_private: bool = False
_synthetic_dns_mode_resolved = False
_cached_synthetic_dns_mode = False
_synthetic_dns_mode_lock = threading.Lock()
_SYNTHETIC_DNS_PROBE_HOSTS = ("example.com", "github.com")


def _is_fake_ip_address(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return any(ip in network for network in _FAKE_IP_NETWORKS)


def _synthetic_fake_ip_dns_active() -> bool:
    """Detect transparent-proxy DNS that maps public names into 198.18/15.

    A single Fake-IP answer is not sufficient: an attacker-controlled DNS
    record could otherwise opt itself out of SSRF checks. Both independent
    public probes must resolve exclusively into the Fake-IP range and produce
    at least two distinct synthetic addresses. Direct Fake-IP URL literals
    remain blocked regardless of this result.
    """
    global _synthetic_dns_mode_resolved, _cached_synthetic_dns_mode
    with _synthetic_dns_mode_lock:
        if _synthetic_dns_mode_resolved:
            return _cached_synthetic_dns_mode

        override = os.getenv("HERMES_SYNTHETIC_DNS_MODE", "").strip().lower()
        if override in {"true", "1", "yes"}:
            _cached_synthetic_dns_mode = True
            _synthetic_dns_mode_resolved = True
            return True
        if override in {"false", "0", "no"}:
            _synthetic_dns_mode_resolved = True
            return False

        synthetic_addresses: set[str] = set()
        try:
            for probe in _SYNTHETIC_DNS_PROBE_HOSTS:
                probe_addresses: set[str] = set()
                for _, _, _, _, sockaddr in socket.getaddrinfo(
                    probe,
                    None,
                    socket.AF_UNSPEC,
                    socket.SOCK_STREAM,
                ):
                    try:
                        resolved = ipaddress.ip_address(sockaddr[0])
                    except ValueError:
                        continue
                    if not _is_fake_ip_address(resolved):
                        _synthetic_dns_mode_resolved = True
                        return False
                    probe_addresses.add(str(resolved))
                if not probe_addresses:
                    _synthetic_dns_mode_resolved = True
                    return False
                synthetic_addresses.update(probe_addresses)
        except (OSError, socket.gaierror):
            _synthetic_dns_mode_resolved = True
            return False

        _cached_synthetic_dns_mode = len(synthetic_addresses) >= 2
        _synthetic_dns_mode_resolved = True
        return _cached_synthetic_dns_mode


def _reset_synthetic_dns_mode_cache() -> None:
    """Reset transparent-proxy DNS detection (tests only)."""
    global _synthetic_dns_mode_resolved, _cached_synthetic_dns_mode
    with _synthetic_dns_mode_lock:
        _synthetic_dns_mode_resolved = False
        _cached_synthetic_dns_mode = False


def is_synthetic_fake_ip_hostname(
    hostname: str,
    addresses: list[str] | tuple[str, ...],
) -> bool:
    """Return whether all answers are proxy-owned Fake-IP mappings.

    Literal IP hostnames are deliberately excluded. The browser will connect
    using the original hostname, allowing the already-active transparent proxy
    to recover the real destination without treating its synthetic address as
    an origin peer.
    """
    normalized = str(hostname or "").strip().lower().rstrip(".")
    if not normalized or normalized in _BLOCKED_HOSTNAMES:
        return False
    try:
        ipaddress.ip_address(normalized)
        return False
    except ValueError:
        pass
    parsed_addresses = []
    for address in addresses:
        try:
            parsed_addresses.append(ipaddress.ip_address(address))
        except ValueError:
            return False
    return (
        bool(parsed_addresses)
        and all(_is_fake_ip_address(ip) for ip in parsed_addresses)
        and _synthetic_fake_ip_dns_active()
    )


def _global_allow_private_urls() -> bool:
    """Return True when the user has opted out of private-IP blocking.

    Checks (in priority order):
    1. ``HERMES_ALLOW_PRIVATE_URLS`` env var  (``true``/``1``/``yes``)
    2. ``security.allow_private_urls`` in config.yaml
    3. ``browser.allow_private_urls`` in config.yaml  (legacy / backward compat)

    Result is cached for the process lifetime.
    """
    global _allow_private_resolved, _cached_allow_private
    if _allow_private_resolved:
        return _cached_allow_private

    _allow_private_resolved = True
    _cached_allow_private = False  # safe default

    # 1. Env var override (highest priority)
    env_val = os.getenv("HERMES_ALLOW_PRIVATE_URLS", "").strip().lower()
    if env_val in {"true", "1", "yes"}:
        _cached_allow_private = True
        return _cached_allow_private
    if env_val in {"false", "0", "no"}:
        # Explicit false — don't fall through to config
        return _cached_allow_private

    # 2. Config file
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config()
        # security.allow_private_urls (preferred)
        sec = cfg.get("security", {})
        if isinstance(sec, dict) and is_truthy_value(
            sec.get("allow_private_urls"), default=False
        ):
            _cached_allow_private = True
            return _cached_allow_private
        # browser.allow_private_urls (legacy fallback)
        browser = cfg.get("browser", {})
        if isinstance(browser, dict) and is_truthy_value(
            browser.get("allow_private_urls"), default=False
        ):
            _cached_allow_private = True
            return _cached_allow_private
    except Exception:
        # Config unavailable (e.g. tests, early import) — keep default
        pass

    return _cached_allow_private


def _reset_allow_private_cache() -> None:
    """Reset the cached toggle — only for tests."""
    global _allow_private_resolved, _cached_allow_private
    _allow_private_resolved = False
    _cached_allow_private = False


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if the IP should be blocked for SSRF protection."""
    # IPv4-mapped IPv6 addresses (``::ffff:x.x.x.x``) should be checked
    # by their embedded IPv4 address, not as IPv6
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        embedded_ip = ip.ipv4_mapped
        return (embedded_ip.is_private or embedded_ip.is_loopback or
                embedded_ip.is_link_local or embedded_ip.is_reserved or
                embedded_ip.is_multicast or embedded_ip.is_unspecified or
                embedded_ip in _CGNAT_NETWORK)

    # Standard IPv4/IPv6 address checking
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    # CGNAT range not covered by is_private
    if ip in _CGNAT_NETWORK:
        return True
    return False


def is_always_blocked_url(url: str) -> bool:
    """Return True when the URL targets an always-blocked endpoint.

    This is the security floor — cloud metadata IPs / hostnames
    (169.254.169.254, metadata.google.internal, ECS task metadata, etc.)
    that have no legitimate agent use regardless of backend, routing, or
    the ``allow_private_urls`` toggle.  Used by callers that bypass the
    full ``is_safe_url`` check for their own reasons (e.g. hybrid cloud
    browser routing to a local Chromium sidecar for private URLs) and
    still need to enforce the non-negotiable floor before letting the
    request proceed.

    Returns True (= blocked) on:
      - Hostnames in ``_BLOCKED_HOSTNAMES``
      - IPs / networks in ``_ALWAYS_BLOCKED_IPS`` / ``_ALWAYS_BLOCKED_NETWORKS``
      - URLs whose hostname resolves to any of the above

    Returns False (= not in the always-blocked floor) on:
      - Benign public / private / loopback URLs (whether or not they'd
        be blocked by the ordinary SSRF check)
      - DNS-resolution failures for non-sentinel hostnames (these are
        someone else's problem — the caller's ordinary fail-closed path
        will catch them if applicable)
      - Parse errors (caller decides fail-open vs fail-closed)

    Intentionally narrower than ``is_safe_url``: only blocks the sentinel
    set, not ordinary private addresses.  Callers that want the full
    SSRF check should still use ``is_safe_url``.
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        if not hostname:
            return False

        # Blocked-hostname check fires regardless of DNS resolution
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning(
                "Blocked request to internal hostname (always-blocked floor): %s",
                hostname,
            )
            return True

        # Literal IP → check directly against the always-blocked set
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            ip = None

        if ip is not None:
            if ip in _ALWAYS_BLOCKED_IPS or any(
                ip in net for net in _ALWAYS_BLOCKED_NETWORKS
            ):
                logger.warning(
                    "Blocked request to cloud metadata address "
                    "(always-blocked floor): %s",
                    hostname,
                )
                return True
            return False

        # Hostname → resolve and check every answer.  DNS failure is NOT
        # always-blocked (caller's ordinary path handles that).
        try:
            addr_info = socket.getaddrinfo(
                hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
        except socket.gaierror:
            return False

        resolved_ip_strings = [
            sockaddr[0]
            for _family, _, _, _, sockaddr in addr_info
        ]
        synthetic_fake_ip_mapping = is_synthetic_fake_ip_hostname(
            hostname,
            resolved_ip_strings,
        )
        for _family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                resolved = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if (
                synthetic_fake_ip_mapping
                and _is_fake_ip_address(resolved)
            ):
                continue
            if resolved in _ALWAYS_BLOCKED_IPS or any(
                resolved in net for net in _ALWAYS_BLOCKED_NETWORKS
            ):
                logger.warning(
                    "Blocked request to cloud metadata address "
                    "(always-blocked floor): %s -> %s",
                    hostname,
                    ip_str,
                )
                return True

        return False

    except Exception as exc:
        # Parse failures or unexpected errors — don't claim the URL is
        # always-blocked.  Caller decides what to do with a malformed URL.
        logger.debug("is_always_blocked_url error for %s: %s", url, exc)
        return False


def _allows_private_ip_resolution(hostname: str, scheme: str) -> bool:
    """Return True when a trusted HTTPS hostname may bypass IP-class blocking."""
    return scheme == "https" and hostname in _TRUSTED_PRIVATE_IP_HOSTS


def is_safe_url(url: str) -> bool:
    """Return True if the URL target is not a private/internal address.

    Resolves the hostname to an IP and checks against private ranges.
    Fails closed: DNS errors and unexpected exceptions block the request.

    When ``security.allow_private_urls`` is enabled (or the env var
    ``HERMES_ALLOW_PRIVATE_URLS=true``), private-IP blocking is skipped.
    Cloud metadata endpoints (169.254.169.254, metadata.google.internal)
    remain blocked regardless — they are never legitimate agent targets.
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        scheme = (parsed.scheme or "").strip().lower()
        if scheme not in {"http", "https"}:
            logger.warning("Blocked request — unsupported URL scheme: %s", scheme or "<empty>")
            return False
        if not hostname:
            return False

        # Block known internal hostnames — ALWAYS, even with toggle on
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("Blocked request to internal hostname: %s", hostname)
            return False

        # Check the global toggle AFTER blocking metadata hostnames
        allow_all_private = _global_allow_private_urls()

        allow_private_ip = _allows_private_ip_resolution(hostname, scheme)

        # Try to resolve and check IP
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            # DNS resolution failed — fail closed. If DNS can't resolve it,
            # the HTTP client will also fail, so blocking loses nothing.
            logger.warning("Blocked request — DNS resolution failed for: %s", hostname)
            return False

        resolved_ip_strings = [
            sockaddr[0]
            for _, _, _, _, sockaddr in addr_info
        ]
        synthetic_fake_ip_mapping = is_synthetic_fake_ip_hostname(
            hostname,
            resolved_ip_strings,
        )
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            if synthetic_fake_ip_mapping and _is_fake_ip_address(ip):
                continue

            # Always block cloud metadata IPs and link-local, even with toggle on
            if ip in _ALWAYS_BLOCKED_IPS or any(ip in net for net in _ALWAYS_BLOCKED_NETWORKS):
                logger.warning(
                    "Blocked request to cloud metadata address: %s -> %s",
                    hostname, ip_str,
                )
                return False

            if not allow_all_private and not allow_private_ip and _is_blocked_ip(ip):
                logger.warning(
                    "Blocked request to private/internal address: %s -> %s",
                    hostname, ip_str,
                )
                return False

        if allow_all_private:
            logger.debug(
                "Allowing private/internal resolution (security.allow_private_urls=true): %s",
                hostname,
            )
        elif allow_private_ip:
            logger.debug(
                "Allowing trusted hostname despite private/internal resolution: %s",
                hostname,
            )

        return True

    except Exception as exc:
        # Fail closed on unexpected errors — don't let parsing edge cases
        # become SSRF bypass vectors
        logger.warning("Blocked request — URL safety check error for %s: %s", url, exc)
        return False
