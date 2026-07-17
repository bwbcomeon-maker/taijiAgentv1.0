"""Production-seam tests for managed-run runtime route admission."""

from agent import agent_init
from hermes_cli import runtime_provider


def test_resolved_runtime_constructibility_matches_agent_transport_semantics():
    helper = getattr(agent_init, "resolved_runtime_is_constructible", None)
    assert helper is not None

    assert helper(
        provider="custom",
        api_mode="chat_completions",
        base_url="http://127.0.0.1:11434/v1",
        api_key="no-key-required",
    )
    assert helper(
        provider="bedrock",
        api_mode="bedrock_converse",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        api_key=None,
    )
    assert helper(
        provider="bedrock",
        api_mode="anthropic_messages",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        api_key=None,
    )
    assert helper(
        provider="openai-codex",
        api_mode="codex_app_server",
        base_url=None,
        api_key=None,
    )
    assert helper(
        provider="azure-foundry",
        api_mode="chat_completions",
        base_url="https://example.services.ai.azure.com",
        api_key=lambda: "entra-token",
    )
    assert helper(
        provider="copilot-acp",
        api_mode="chat_completions",
        base_url="acp://copilot",
        api_key="copilot-acp",
    )
    assert not helper(
        provider="openrouter",
        api_mode="chat_completions",
        base_url="https://openrouter.ai/api/v1",
        api_key="",
    )


def test_real_openrouter_runtime_without_credentials_is_not_constructible(
    monkeypatch,
):
    monkeypatch.setattr(
        runtime_provider,
        "_get_model_config",
        lambda: {
            "provider": "openrouter",
            "default": "anthropic/claude-sonnet-4.6",
        },
    )
    monkeypatch.setattr(runtime_provider, "get_env_value", lambda _name: "")
    monkeypatch.setattr(runtime_provider, "load_pool", lambda _provider: None)

    runtime = runtime_provider.resolve_runtime_provider(
        requested="openrouter",
        target_model="anthropic/claude-sonnet-4.6",
    )

    assert runtime["provider"] == "openrouter"
    assert runtime["api_key"] == ""
    assert not agent_init.resolved_runtime_is_constructible(
        provider=runtime["provider"],
        api_mode=runtime["api_mode"],
        base_url=runtime["base_url"],
        api_key=runtime["api_key"],
    )


def test_bedrock_target_non_claude_overrides_claude_config_default(monkeypatch):
    monkeypatch.setattr(
        runtime_provider,
        "_get_model_config",
        lambda: {
            "provider": "bedrock",
            "default": "anthropic.claude-sonnet-4-6-v1:0",
        },
    )
    monkeypatch.setattr(
        runtime_provider,
        "load_config",
        lambda: {"bedrock": {"region": "us-east-1"}},
    )

    runtime = runtime_provider.resolve_runtime_provider(
        requested="bedrock",
        target_model="meta.llama3-3-70b-instruct-v1:0",
    )

    assert runtime["api_mode"] == "bedrock_converse"
    assert "bedrock_anthropic" not in runtime


def test_bedrock_target_claude_overrides_non_claude_config_default(monkeypatch):
    monkeypatch.setattr(
        runtime_provider,
        "_get_model_config",
        lambda: {
            "provider": "bedrock",
            "default": "meta.llama3-3-70b-instruct-v1:0",
        },
    )
    monkeypatch.setattr(
        runtime_provider,
        "load_config",
        lambda: {"bedrock": {"region": "us-east-1"}},
    )

    runtime = runtime_provider.resolve_runtime_provider(
        requested="bedrock",
        target_model="anthropic.claude-sonnet-4-6-v1:0",
    )

    assert runtime["api_mode"] == "anthropic_messages"
    assert runtime["bedrock_anthropic"] is True
