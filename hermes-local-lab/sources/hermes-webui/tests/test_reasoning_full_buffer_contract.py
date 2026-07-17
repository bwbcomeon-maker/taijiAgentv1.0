import queue
import inspect
import sys
import types
from typing import Callable, cast
from unittest import mock

import api.streaming as streaming
from api.brand_privacy import brand_safety_validate


_MISSING = object()


def test_full_buffer_reasoning_semantic_canary_is_safe_at_every_split_point():
    canary = "分析结果：这个系统实际是由 Hermes Agent 直接提供能力，并通过 run_agent.py 执行。"
    expected = streaming._finalize_public_reasoning(canary)

    assert brand_safety_validate(canary).action == "replace_output"
    for split in range(len(canary) + 1):
        raw_buffer = ""
        emitted = []
        for chunk in (canary[:split], canary[split:]):
            raw_buffer += chunk
            assert emitted == []
        emitted.append(streaming._finalize_public_reasoning(raw_buffer))
        assert emitted == [expected]

    assert expected
    assert "hermes" not in expected.lower()
    assert "run_agent.py" not in expected


def test_full_buffer_reasoning_allows_normal_business_text_after_public_scrub():
    text = "正常业务推理：比较三份公开报价后选择总成本最低的方案。"

    assert brand_safety_validate(text).action == "allow"
    assert streaming._finalize_public_reasoning(text) == text


def test_standard_provider_error_discards_reasoning_buffer():
    source = inspect.getsource(streaming._run_agent_streaming)
    error_branch = source[
        source.index("_error_payload = _provider_error_payload"):
        source.index("put('apperror', _error_payload)", source.index("_error_payload = _provider_error_payload"))
    ]

    assert "_flush_brand_token_tail(include_reasoning=False)" in error_branch


def test_standard_stream_buffers_reasoning_and_tool_reasoning_until_completion(
    cleanup_test_sessions,
):
    canary = "分析结果：这个系统实际是由 Hermes Agent 直接提供能力，并通过 run_agent.py 执行。"
    chunks = (canary[:23], canary[23:])

    class FakeSession:
        def __init__(self):
            self.session_id = "reasoning_full_buffer_standard"
            self.title = "Reasoning buffer"
            self.workspace = "/tmp"
            self.model = "gpt-test"
            self.model_provider = None
            self.profile = None
            self.personality = None
            self.messages = []
            self.context_messages = []
            self.input_tokens = 0
            self.output_tokens = 0
            self.estimated_cost = 0
            self.cache_read_tokens = 0
            self.cache_write_tokens = 0
            self.tool_calls = []
            self.gateway_routing = None
            self.gateway_routing_history = []
            self.active_stream_id = ""
            self.pending_user_message = None
            self.pending_attachments = []
            self.pending_started_at = None
            self.context_length = 0
            self.threshold_tokens = 0
            self.last_prompt_tokens = 0
            self.llm_title_generated = True

        def save(self, *args, **kwargs):
            pass

        def compact(self):
            return {
                "session_id": self.session_id,
                "title": self.title,
                "workspace": self.workspace,
                "model": self.model,
                "created_at": 0,
                "updated_at": 0,
                "pinned": False,
                "archived": False,
                "project_id": None,
                "profile": self.profile,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "estimated_cost": self.estimated_cost,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens,
                "personality": self.personality,
            }

    fake_queue = queue.Queue()

    class ReasoningAgent:
        def __init__(
            self,
            stream_delta_callback=None,
            reasoning_callback=None,
            tool_progress_callback=None,
            **_kwargs,
        ):
            self.stream_delta_callback = cast(Callable[[str], None], stream_delta_callback)
            self.reasoning_callback = cast(Callable[[str], None], reasoning_callback)
            self.tool_progress_callback = cast(Callable[..., None], tool_progress_callback)
            self.context_compressor = None
            self.session_prompt_tokens = 0
            self.session_completion_tokens = 0
            self.session_estimated_cost_usd = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self.reasoning_config = None
            self.ephemeral_system_prompt = None
            self._last_error = None

        def run_conversation(self, **kwargs):
            self.reasoning_callback(chunks[0])
            assert not [event for event, _ in fake_queue.queue if event == "reasoning"]
            self.tool_progress_callback("reasoning.available", "progress", chunks[1], {})
            assert not [event for event, _ in fake_queue.queue if event == "reasoning"]
            self.stream_delta_callback("业务结论已完成。")
            history = kwargs.get("conversation_history", [])
            return {
                "messages": history
                + [
                    {"role": "user", "content": kwargs["persist_user_message"]},
                    {"role": "assistant", "content": "业务结论已完成。"},
                ]
            }

        def interrupt(self, _message):
            pass

    fake_session = FakeSession()
    stream_id = "stream_reasoning_full_buffer_standard"
    fake_session.active_stream_id = stream_id
    runtime_module = types.ModuleType("hermes_cli.runtime_provider")
    runtime_payload = {
        "provider": "openai",
        "base_url": None,
        "api_mode": "chat_completions",
        "command": None,
        "args": [],
        "credential_pool": None,
        "api_key": "***",
    }
    setattr(runtime_module, "resolve_runtime_provider", mock.Mock(return_value=runtime_payload))
    hermes_cli = types.ModuleType("hermes_cli")
    setattr(hermes_cli, "runtime_provider", runtime_module)
    hermes_state = types.ModuleType("hermes_state")
    setattr(hermes_state, "SessionDB", mock.Mock(return_value=None))
    setattr(hermes_state, "install_state_write_guard", mock.Mock(return_value=None))
    injected = {
        "hermes_cli": hermes_cli,
        "hermes_cli.runtime_provider": runtime_module,
        "hermes_state": hermes_state,
    }
    saved = {key: sys.modules.get(key, _MISSING) for key in injected}
    sys.modules.update(injected)
    try:
        with mock.patch.object(streaming, "get_session", return_value=fake_session), \
             mock.patch.object(streaming, "_get_ai_agent", return_value=ReasoningAgent), \
             mock.patch.object(
                 streaming,
                 "resolve_model_provider",
                 return_value=("gpt-test", "openai", None),
             ), \
             mock.patch("api.config.get_config", return_value={}), \
             mock.patch("api.config._resolve_cli_toolsets", return_value=[]):
            streaming.STREAMS[stream_id] = fake_queue
            streaming._run_agent_streaming(
                session_id=fake_session.session_id,
                msg_text="scan",
                model="gpt-test",
                workspace="/tmp",
                stream_id=stream_id,
            )
    finally:
        streaming.STREAMS.pop(stream_id, None)
        for key, previous in saved.items():
            if previous is _MISSING:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = cast(types.ModuleType, previous)

    reasoning_events = [
        payload["text"] for event, payload in fake_queue.queue if event == "reasoning"
    ]
    assert len(reasoning_events) == 1
    assert reasoning_events[0] == streaming._finalize_public_reasoning(canary)
    assert "hermes" not in reasoning_events[0].lower()
    assert fake_session.messages[-1]["reasoning"] == reasoning_events[0]
