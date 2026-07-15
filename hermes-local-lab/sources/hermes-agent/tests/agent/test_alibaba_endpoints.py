"""Tests for Alibaba Model Studio regional endpoint construction."""

from __future__ import annotations

import pytest

from agent.alibaba_endpoints import (
    build_image_generation_url,
    build_image_root_url,
    build_vision_base_url,
    normalize_region,
    validate_https_url,
    validate_workspace_prefix,
)


def test_builds_beijing_workspace_vision_url():
    assert build_vision_base_url(
        endpoint_mode="workspace",
        region="cn-beijing",
        workspace_prefix="llm-demo",
    ) == "https://llm-demo.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"


@pytest.mark.parametrize(
    ("region", "expected"),
    [
        ("cn-beijing", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("ap-southeast-1", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    ],
)
def test_builds_public_vision_url_for_supported_regions(region, expected):
    assert build_vision_base_url(endpoint_mode="public", region=region) == expected


def test_custom_vision_url_is_normalized():
    assert build_vision_base_url(
        endpoint_mode="custom",
        custom_url=" https://gateway.example.com/compatible-mode/v1/ ",
    ) == "https://gateway.example.com/compatible-mode/v1"


@pytest.mark.parametrize(
    "url",
    [
        "http://gateway.example.com",
        "https://user:password@gateway.example.com",
        "https://gateway.example.com?token=secret",
        "https://gateway.example.com#section",
        "https://127.0.0.1",
        "https://localhost",
        "https://LOCALHOST.",
        "https://127.1",
        "https://2130706433",
        "https://0x7f000001",
        "https://[::1]",
        "https://[fc00::1]",
        "https://[fe80::1]",
        "https://999999999999",
        "https://0xnotanaddress",
    ],
)
def test_https_url_rejects_unsafe_or_ambiguous_values(url):
    with pytest.raises(ValueError):
        validate_https_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://gateway.example.com:not-a-port",
        "https://gateway.example.com:65536",
        "https://gateway.example.com:-1",
    ],
)
def test_https_url_rejects_invalid_ports(url):
    with pytest.raises(ValueError, match="port"):
        validate_https_url(url)


def test_https_url_rejects_port_zero():
    with pytest.raises(ValueError, match="port"):
        validate_https_url("https://gateway.example.com:0")


def test_https_url_accepts_numeric_port():
    assert validate_https_url(
        "https://gateway.example.com:8443"
    ) == "https://gateway.example.com:8443"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTPS://Gateway.Example.COM.:443/", "https://gateway.example.com"),
        ("https://Gateway.Example.COM.:8443/", "https://gateway.example.com:8443"),
        ("https://[2001:4860:4860::8888]:443/", "https://[2001:4860:4860::8888]"),
    ],
)
def test_https_url_canonicalizes_equivalent_authorities(url, expected):
    assert validate_https_url(url) == expected


def test_workspace_prefix_allows_safe_dns_prefixes():
    assert validate_workspace_prefix("llm-demo") == "llm-demo"
    assert validate_workspace_prefix("ws-cn-test") == "ws-cn-test"


@pytest.mark.parametrize("value", ["", "ws/path", "ws.example", "https://ws", "-workspace", "workspace-"])
def test_workspace_prefix_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_workspace_prefix(value)


def test_region_is_limited_to_explicit_mapping():
    assert normalize_region(" CN-BEIJING ") == "cn-beijing"
    with pytest.raises(ValueError):
        normalize_region("cn-shanghai")


def test_image_root_and_generation_path_are_not_duplicated():
    generation_url = (
        "https://gateway.example.com"
        "/api/v1/services/aigc/multimodal-generation/generation"
    )
    assert build_image_root_url(
        endpoint_mode="custom", custom_url=generation_url
    ) == "https://gateway.example.com"
    assert build_image_generation_url(
        endpoint_mode="custom", custom_url=generation_url
    ) == generation_url


def test_builds_workspace_image_urls_and_rejects_public_mode():
    root = build_image_root_url(
        endpoint_mode="workspace",
        region="ap-southeast-1",
        workspace_prefix="llm-demo",
    )
    assert root == "https://llm-demo.ap-southeast-1.maas.aliyuncs.com"
    assert build_image_generation_url(
        endpoint_mode="workspace",
        region="ap-southeast-1",
        workspace_prefix="llm-demo",
    ) == root + "/api/v1/services/aigc/multimodal-generation/generation"
    with pytest.raises(ValueError, match="public"):
        build_image_generation_url(endpoint_mode="public", region="cn-beijing")
