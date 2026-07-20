"""Self-tests for the suite-level browser and outbound-network guards."""

from __future__ import annotations

import socket
import webbrowser

import pytest

from tests import conftest


def test_browser_guard_blocks_all_standard_openers():
    assert webbrowser.open is conftest._reject_external_browser
    assert webbrowser.open_new is conftest._reject_external_browser
    assert webbrowser.open_new_tab is conftest._reject_external_browser

    for opener in (webbrowser.open, webbrowser.open_new, webbrowser.open_new_tab):
        with pytest.raises(AssertionError, match="must not open"):
            opener("https://browser-leak.invalid/")


def test_browser_guard_allows_explicit_test_override(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    assert webbrowser.open("https://explicit-mock.invalid/") is True
    assert opened == ["https://explicit-mock.invalid/"]


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "::1",
        "192.0.2.7",
        "198.51.100.7",
        "203.0.113.7",
        "2001:db8::7",
        "localhost",
        "service.test",
        "service.invalid",
        "service.example",
    ],
)
def test_network_guard_allows_local_and_explicit_test_destinations(host):
    assert conftest._test_address_is_allowed(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "8.8.8.8",
        "1.1.1.1",
        "0.0.0.0",
        "::",
        "10.0.0.7",
        "172.16.0.7",
        "192.168.0.7",
        "169.254.169.254",
        "fc00::7",
        "fe80::7",
        "198.18.0.1",
        "198.19.255.254",
        "service.local",
        "example.com",
        "www.example.com",
        "example.net",
        "example.org",
        "openrouter.ai",
        "accounts.x.ai",
        "api.openai.com",
        "2606:4700:4700::1111",
    ],
)
def test_network_guard_rejects_public_destinations(host):
    assert conftest._test_address_is_allowed(host) is False


def test_network_guard_blocks_public_create_connection_without_dialing():
    assert socket.create_connection is conftest._blocked_create_connection
    with pytest.raises(OSError, match="network isolation"):
        socket.create_connection(("openrouter.ai", 443))


def test_network_guard_blocks_public_socket_connect_without_dialing():
    assert socket.socket.connect is conftest._blocked_socket_connect
    with pytest.raises(OSError, match="network isolation"):
        conftest._blocked_socket_connect(None, ("8.8.8.8", 53))


def test_network_guard_blocks_public_socket_connect_ex_without_dialing():
    assert socket.socket.connect_ex is conftest._blocked_socket_connect_ex
    with pytest.raises(OSError, match="network isolation"):
        conftest._blocked_socket_connect_ex(None, ("1.1.1.1", 443))


def test_unix_domain_socket_addresses_remain_allowed():
    assert conftest._test_address_is_allowed("/tmp/hermes-test.sock") is True


def test_outbound_network_opt_in_restores_real_socket_primitives(
    allow_outbound_network,
):
    assert socket.create_connection is conftest._REAL_CREATE_CONNECTION
    assert socket.socket.connect is conftest._REAL_SOCKET_CONNECT
    assert socket.socket.connect_ex is conftest._REAL_SOCKET_CONNECT_EX
