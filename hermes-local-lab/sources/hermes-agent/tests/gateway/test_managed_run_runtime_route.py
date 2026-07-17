"""Production-seam tests for managed-run runtime route admission."""

import subprocess
from unittest.mock import patch

from agent import agent_init
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_cli import runtime_provider
from run_agent import AIAgent


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


def test_codex_app_server_route_constructs_real_agent_without_http_credentials(
    monkeypatch,
    tmp_path,
):
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={}))
    resolved_route = {
        "model": "gpt-5.4",
        "provider": "openai-codex",
        "runtime_kwargs": {
            "provider": "openai-codex",
            "api_mode": "codex_app_server",
            "base_url": "",
            "api_key": "",
        },
        "fallback_model": None,
    }

    agent = adapter._create_agent(
        session_id="session-codex-app-server-construction",
        resolved_route=resolved_route,
    )

    assert isinstance(agent, AIAgent)
    assert agent.provider == "openai-codex"
    assert agent.api_mode == "codex_app_server"
    assert agent.base_url == ""
    assert agent.api_key == ""
    assert agent.client is None
    assert agent._client_kwargs == {}
    assert not hasattr(agent, "_codex_session")


def test_codex_app_server_real_resolver_route_and_agent_chain_is_local_only(
    monkeypatch,
    tmp_path,
):
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "\n".join(
            (
                "model:",
                "  provider: openai-codex",
                "  default: gpt-5.4",
                "  openai_runtime: codex_app_server",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("local Codex route must not resolve HTTP credentials")

    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={}))
    with (
        patch.object(
            runtime_provider,
            "resolve_codex_runtime_credentials",
            side_effect=forbidden,
        ),
        patch.object(runtime_provider, "load_pool", side_effect=forbidden),
        patch.object(
            runtime_provider,
            "_resolve_named_custom_runtime",
            side_effect=forbidden,
        ),
        patch.object(subprocess, "Popen", side_effect=forbidden) as popen,
    ):
        runtime = runtime_provider.resolve_runtime_provider(
            requested="openai-codex",
            target_model="gpt-5.4",
        )
        route = adapter._resolve_agent_route(
            requested_model="gpt-5.4",
            requested_provider="openai-codex",
        )
        agent = adapter._create_agent(
            session_id="session-codex-real-route-chain",
            resolved_route=route,
        )

    assert runtime["provider"] == route["provider"] == agent.provider == "openai-codex"
    assert (
        runtime["api_mode"]
        == route["runtime_kwargs"]["api_mode"]
        == agent.api_mode
        == "codex_app_server"
    )
    assert route["model"] == agent.model == "gpt-5.4"
    assert runtime["api_key"] == route["runtime_kwargs"]["api_key"] == agent.api_key == ""
    assert runtime["base_url"] == route["runtime_kwargs"]["base_url"] == agent.base_url == ""
    assert runtime["credential_pool"] is None
    assert route["runtime_kwargs"]["credential_pool"] is None
    assert agent._credential_pool is None
    assert agent.client is None
    assert not hasattr(agent, "_codex_session")
    popen.assert_not_called()
