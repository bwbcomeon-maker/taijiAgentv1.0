"""FAL_KEY env var normalization (whitespace-only treated as unset)."""


def test_fal_key_whitespace_is_unset(monkeypatch):
    # Whitespace-only FAL_KEY must NOT register as configured, and the managed
    # gateway fallback must be disabled for this assertion to be meaningful.
    monkeypatch.setenv("FAL_KEY", "   ")

    from tools import image_generation_tool

    monkeypatch.setattr(
        image_generation_tool, "_resolve_managed_fal_gateway", lambda: None
    )

    assert image_generation_tool.check_fal_api_key() is False


def test_fal_key_valid(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "sk-test")

    from tools import image_generation_tool

    monkeypatch.setattr(
        image_generation_tool, "_resolve_managed_fal_gateway", lambda: None
    )

    assert image_generation_tool.check_fal_api_key() is True


def test_fal_key_empty_is_unset(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "")

    from tools import image_generation_tool

    monkeypatch.setattr(
        image_generation_tool, "_resolve_managed_fal_gateway", lambda: None
    )

    assert image_generation_tool.check_fal_api_key() is False


# ---------------------------------------------------------------------------
# Actionable setup message when no FAL backend is reachable.
# Regression for the silent-drop UX gap described in issue #2543.
# ---------------------------------------------------------------------------


def test_no_backend_message_uses_taiji_authorization_copy(monkeypatch):
    from tools import image_generation_tool

    monkeypatch.setattr(
        image_generation_tool, "managed_nous_tools_enabled", lambda: False
    )

    msg = image_generation_tool._build_no_backend_setup_message()

    assert "太极智能体" in msg
    assert "图像生成授权" in msg
    assert "FAL_KEY" not in msg
    assert "hermes tools" not in msg
    assert "hermes plugins" not in msg


def test_no_backend_message_mentions_service_when_managed_gateway_enabled(monkeypatch):
    from tools import image_generation_tool

    monkeypatch.setattr(
        image_generation_tool, "managed_nous_tools_enabled", lambda: True
    )

    msg = image_generation_tool._build_no_backend_setup_message()

    assert "太极智能体" in msg
    assert "托管图像生成服务" in msg
    assert "hermes setup" not in msg


def test_image_generate_tool_returns_actionable_error_when_no_backend(monkeypatch):
    """End-to-end: handler must surface the actionable message, not a bare string."""
    import json

    from tools import image_generation_tool

    monkeypatch.setattr(
        image_generation_tool, "fal_key_is_configured", lambda: False
    )
    monkeypatch.setattr(
        image_generation_tool, "_resolve_managed_fal_gateway", lambda: None
    )
    monkeypatch.setattr(
        image_generation_tool, "managed_nous_tools_enabled", lambda: False
    )

    result = json.loads(
        image_generation_tool.image_generate_tool(prompt="a cat")
    )

    assert result["success"] is False
    assert "太极智能体" in result["error"]
    assert "图像生成授权" in result["error"]
    assert "https://fal.ai" not in result["error"]
    assert "FAL_KEY" not in result["error"]
    assert "hermes" not in result["error"]
