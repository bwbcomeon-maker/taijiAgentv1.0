from types import SimpleNamespace

import pytest


def test_exact_system_prompt_mode_returns_caller_contract_byte_for_byte():
    from agent.system_prompt import build_system_prompt, build_system_prompt_parts

    agent = SimpleNamespace(exact_system_prompt=True)
    contract = "[STRICT SYSTEM]\n只执行当前专家阶段。\n不得调用工具。"

    assert build_system_prompt(agent, contract) == contract
    assert build_system_prompt_parts(agent, contract) == {
        "stable": "",
        "context": contract,
        "volatile": "",
    }


@pytest.mark.parametrize("invalid", [None, "", "   \n"])
def test_exact_system_prompt_mode_rejects_empty_contract(invalid):
    from agent.system_prompt import build_system_prompt

    with pytest.raises(ValueError, match="exact system prompt"):
        build_system_prompt(SimpleNamespace(exact_system_prompt=True), invalid)


def test_anthropic_oauth_preserves_exact_system_contract():
    from agent.anthropic_adapter import build_anthropic_kwargs

    contract = "  Hermes Agent 专家执行合同\n不得改写。  "
    kwargs = build_anthropic_kwargs(
        model="claude-sonnet-4-6",
        messages=[
            {"role": "system", "content": contract},
            {"role": "user", "content": "执行当前阶段"},
        ],
        tools=[],
        max_tokens=1024,
        reasoning_config=None,
        is_oauth=True,
        exact_system_prompt=True,
    )

    assert kwargs["system"] == contract
    assert "Claude Code" not in kwargs["system"]
    assert "tools" not in kwargs


def test_chat_completions_exact_mode_preserves_roles_and_reserved_payload():
    from agent.transports.chat_completions import ChatCompletionsTransport

    messages = [
        {"role": "system", "content": "strict system"},
        {"role": "user", "content": "strict user"},
    ]
    kwargs = ChatCompletionsTransport().build_kwargs(
        model="gpt-5",
        messages=messages,
        tools=[],
        exact_system_prompt=True,
        request_overrides={
            "messages": [{"role": "user", "content": "injected"}],
            "tools": [{"type": "function"}],
            "temperature": 0,
        },
    )

    assert kwargs["messages"] == messages
    assert "tools" not in kwargs
    assert kwargs["temperature"] == 0


def test_codex_responses_exact_mode_preserves_instruction_bytes():
    from agent.transports.codex import ResponsesApiTransport

    contract = "  strict system\nkeep whitespace  "
    kwargs = ResponsesApiTransport().build_kwargs(
        model="gpt-5",
        messages=[
            {"role": "system", "content": contract},
            {"role": "user", "content": "strict user"},
        ],
        tools=[],
        exact_system_prompt=True,
        request_overrides={
            "instructions": "injected",
            "input": "injected",
            "service_tier": "default",
        },
    )

    assert kwargs["instructions"] == contract
    assert kwargs["input"] == [{"role": "user", "content": "strict user"}]
    assert kwargs["service_tier"] == "default"


def test_exact_mode_never_issues_iteration_summary_request():
    from agent.chat_completion_helpers import handle_max_iterations

    with pytest.raises(RuntimeError, match="exact system prompt turn exhausted"):
        handle_max_iterations(
            SimpleNamespace(exact_system_prompt=True),
            [{"role": "user", "content": "strict user"}],
            1,
        )
