"""Adversarial test for the network-isolation fixture in conftest.py.

The autouse module-level monkey-patch in tests/conftest.py wraps the socket
connection primitives so that any non-loopback / non-documentation destination
raises OSError. This file proves:

  1. The block actually fires for outbound to a real public IP.
  2. Only loopback / RFC documentation / reserved test-TLD destinations pass.
  3. LAN, link-local, metadata, mDNS, and real example.* domains are blocked.
  4. The `allow_outbound_network` fixture re-enables real network for tests
     that legitimately need it.

Without this enforcement, a test that accidentally calls real outbound
(forgotten mock, leaked credential triggering an SDK initialisation, new
code path bypassing an existing mock) can leak production credentials,
slow the test suite into 10-minute waits on TLS handshakes, and produce
flaky failures depending on whether the destination is reachable.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest


def test_outbound_to_public_ipv4_is_blocked():
    """Attempting to connect to a public IP must raise OSError."""
    with pytest.raises(OSError, match="hermes test network isolation"):
        # 8.8.8.8 (Google DNS) is a stable real public IPv4.
        # If we accidentally connect, the test goes to 53/tcp which is
        # genuinely listening — so the block is what stops us, not lack of
        # destination.
        socket.create_connection(("8.8.8.8", 53), timeout=1)


def test_outbound_to_anthropic_ipv6_is_blocked():
    """The exact destination we observed leaking from earlier pytest runs."""
    with pytest.raises(OSError, match="hermes test network isolation"):
        socket.create_connection(("2607:6bc0::10", 443), timeout=1)


def test_outbound_to_amazon_is_blocked():
    """AWS endpoints (botocore / bedrock) must not reach the real service."""
    with pytest.raises(OSError, match="hermes test network isolation"):
        socket.create_connection(("3.173.21.63", 443), timeout=1)


def test_loopback_v4_is_allowed():
    """127.0.0.1 must continue to work — test_server fixture depends on it."""
    # Listen on a temporary port + connect via the wrapped create_connection.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen(1)
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=1)
        client.close()
    finally:
        listener.close()


def test_rfc1918_private_ipv4_is_blocked():
    """Unit tests must not reach services or devices on the developer's LAN."""
    import tests.conftest as _conftest
    assert _conftest._hermes_addr_is_local("10.0.0.5") is False
    assert _conftest._hermes_addr_is_local("172.16.5.1") is False
    assert _conftest._hermes_addr_is_local("172.31.255.254") is False
    assert _conftest._hermes_addr_is_local("192.168.1.22") is False


def test_link_local_and_metadata_are_blocked():
    """AWS IMDS and IPv6 link-local/unique-local routes are external effects."""
    import tests.conftest as _conftest
    assert _conftest._hermes_addr_is_local("169.254.169.254") is False
    assert _conftest._hermes_addr_is_local("fe80::7") is False
    assert _conftest._hermes_addr_is_local("fc00::7") is False


def test_reserved_tlds_are_allowed():
    """Non-resolving RFC-reserved test TLDs remain usable in unit tests."""
    import tests.conftest as _conftest
    assert _conftest._hermes_addr_is_local("my-mac.tailnet.example") is True
    assert _conftest._hermes_addr_is_local("anything.invalid") is True
    assert _conftest._hermes_addr_is_local("test-host.test") is True
    assert _conftest._hermes_addr_is_local("localhost") is True


def test_lan_mdns_real_example_domains_and_public_addresses_are_blocked():
    """Real-resolving names and non-documentation routes must stay blocked."""
    import tests.conftest as _conftest
    for host in (
        "8.8.8.8",
        "1.1.1.1",
        "198.18.0.1",
        "printer.local",
        "example.com",
        "www.example.com",
        "example.net",
        "example.org",
    ):
        assert _conftest._hermes_addr_is_local(host) is False


def test_rfc_documentation_addresses_are_allowed():
    """Explicit RFC documentation networks may be used as inert test data."""
    import tests.conftest as _conftest
    assert _conftest._hermes_addr_is_local("192.0.2.7") is True
    assert _conftest._hermes_addr_is_local("198.51.100.7") is True
    assert _conftest._hermes_addr_is_local("203.0.113.0") is True  # TEST-NET-3
    assert _conftest._hermes_addr_is_local("2001:db8::7") is True
    assert _conftest._hermes_addr_is_local("204.0.113.0") is False  # outside


def test_socket_connect_ex_is_blocked():
    """connect_ex must not bypass the create_connection/connect wrappers."""
    with pytest.raises(OSError, match="hermes test network isolation"):
        socket.socket.connect_ex(None, ("1.1.1.1", 443))


def test_allow_outbound_network_fixture_unswaps_the_wrappers(allow_outbound_network):
    """When a test opts in to the fixture, socket.create_connection and
    socket.socket.connect are restored to their real (unwrapped) implementations
    for this test only.

    Check by qname so this is robust against pytest re-importing conftest
    under multiple roots (which produces two distinct function objects with
    the same __qualname__ but different `is` identity).
    """
    # Inside the fixture, the symbol should NOT be the blocked wrapper.
    assert "_hermes_blocked_create_connection" not in getattr(
        socket.create_connection, "__qualname__", ""
    ), "allow_outbound_network fixture did not restore the real create_connection"
    assert "_hermes_blocked_socket_connect" not in getattr(
        socket.socket.connect, "__qualname__", ""
    ), "allow_outbound_network fixture did not restore the real socket.connect"
    assert "_hermes_blocked_socket_connect_ex" not in getattr(
        socket.socket.connect_ex, "__qualname__", ""
    ), "allow_outbound_network fixture did not restore the real socket.connect_ex"


def test_block_is_active_outside_the_fixture():
    """Sanity: a test that does NOT request the fixture has the wrapped
    socket.create_connection installed.

    Check by qname so this is robust against pytest re-importing conftest
    under multiple roots (which produces two distinct function objects with
    the same __qualname__ but different `is` identity)."""
    assert "_hermes_blocked_create_connection" in getattr(
        socket.create_connection, "__qualname__", ""
    ), "default state should have the blocked wrapper installed on socket.create_connection"
    assert "_hermes_blocked_socket_connect" in getattr(
        socket.socket.connect, "__qualname__", ""
    ), "default state should have the blocked wrapper installed on socket.socket.connect"
    assert "_hermes_blocked_socket_connect_ex" in getattr(
        socket.socket.connect_ex, "__qualname__", ""
    ), "default state should have the blocked wrapper installed on socket.socket.connect_ex"


def test_server_subprocess_uses_the_same_strict_network_policy():
    """The test_server child must not reopen routes blocked in pytest."""
    webui_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["TAIJI_WEBUI_TEST_NETWORK_BLOCK"] = "1"
    probe = """
import socket
import server

for host in (
    "10.0.0.7",
    "192.168.0.7",
    "169.254.169.254",
    "fc00::7",
    "fe80::7",
    "198.18.0.1",
    "printer.local",
    "example.com",
    "www.example.com",
    "example.net",
    "example.org",
):
    assert server._addr_is_local(host) is False, host
for host in (
    "127.0.0.1",
    "::1",
    "192.0.2.7",
    "198.51.100.7",
    "203.0.113.7",
    "2001:db8::7",
    "service.test",
    "service.invalid",
    "service.example",
):
    assert server._addr_is_local(host) is True, host
assert "_blocked_create_connection" in socket.create_connection.__qualname__
assert "_blocked_socket_connect" in socket.socket.connect.__qualname__
assert "_blocked_socket_connect_ex" in socket.socket.connect_ex.__qualname__
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=webui_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_server_preserves_an_existing_marked_network_guard():
    """Importing server in pytest must not replace the outer hermetic guard."""
    webui_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["TAIJI_WEBUI_TEST_NETWORK_BLOCK"] = "1"
    probe = """
import socket

def outer_create(address, *args, **kwargs):
    raise RuntimeError('outer-create')
def outer_connect(self, address):
    raise RuntimeError('outer-connect')
def outer_connect_ex(self, address):
    raise RuntimeError('outer-connect-ex')
for guard in (outer_create, outer_connect, outer_connect_ex):
    guard._taiji_test_network_block = True
socket.create_connection = outer_create
socket.socket.connect = outer_connect
socket.socket.connect_ex = outer_connect_ex

import server

assert socket.create_connection is outer_create
assert socket.socket.connect is outer_connect
assert socket.socket.connect_ex is outer_connect_ex
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=webui_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
