"""RED contracts for versioned image capability state in long-lived agents."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} test tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _tool_names(definitions) -> set[str]:
    return {
        item["function"]["name"]
        for item in definitions
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }


@pytest.fixture(autouse=True)
def _reset_tool_definition_caches():
    import model_tools
    from tools.registry import invalidate_check_fn_cache

    model_tools._clear_tool_defs_cache()
    invalidate_check_fn_cache()
    yield
    model_tools._clear_tool_defs_cache()
    invalidate_check_fn_cache()


def test_tool_cache_key_tracks_versioned_webui_verification_snapshot(
    monkeypatch,
):
    """A changed WebUI verification identity must bypass both schema caches."""
    import model_tools
    from tools import image_generation_tool

    snapshot = {
        "schema_version": 0,
        "fingerprint": "webui-verification-v0-unverified",
        "available": False,
    }

    def readiness():
        return {
            "configured": True,
            "available": snapshot["available"],
            "reason_code": (
                "ready" if snapshot["available"] else "verification_required"
            ),
            "verification_status": (
                "verified"
                if snapshot["available"]
                else "configured_unverified"
            ),
            "verification_schema_version": snapshot["schema_version"],
            "verification_fingerprint": snapshot["fingerprint"],
            "capability_fingerprint": snapshot["fingerprint"],
        }

    monkeypatch.setattr(
        image_generation_tool,
        "get_image_generation_readiness",
        readiness,
    )

    before = model_tools.get_tool_definitions(
        enabled_toolsets=["image_gen"],
        quiet_mode=True,
    )
    assert "image_generate" not in _tool_names(before)

    snapshot.update(
        schema_version=1,
        fingerprint="webui-verification-v1-current",
        available=True,
    )
    after = model_tools.get_tool_definitions(
        enabled_toolsets=["image_gen"],
        quiet_mode=True,
    )

    assert "image_generate" in _tool_names(after), (
        "model_tools reused a tool-definition/check_fn cache entry after the "
        "WebUI verification schema_version and fingerprint changed"
    )

    model_tools._clear_tool_defs_cache()
    snapshot.update(
        schema_version=1,
        fingerprint="webui-verification-same-config",
        available=False,
    )
    non_quiet_before = model_tools.get_tool_definitions(
        enabled_toolsets=["image_gen"],
        quiet_mode=False,
    )
    assert "image_generate" not in _tool_names(non_quiet_before)
    snapshot["available"] = True
    non_quiet_after = model_tools.get_tool_definitions(
        enabled_toolsets=["image_gen"],
        quiet_mode=False,
    )
    assert "image_generate" in _tool_names(non_quiet_after), (
        "non-quiet schema resolution reused the old registry check_fn result"
    )


def _make_long_lived_agent():
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=[_tool("read_file")]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("hermes_cli.config.load_config", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="b3-test-key-1234567890",
            base_url="https://example.test/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            enabled_toolsets=["image_gen"],
        )
    agent.client = MagicMock()
    agent._cached_system_prompt = "cached before capability refresh"
    agent._use_prompt_caching = False
    agent.tool_delay = 0
    agent.compression_enabled = False
    agent.save_trajectories = False
    return agent


def _tool_call(name: str, arguments: dict, call_id: str):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments),
        ),
    )


@pytest.mark.parametrize("execution_mode", ("sequential", "concurrent"))
def test_long_lived_agent_refresh_and_image_call_gate_preserve_non_registry_tools(
    monkeypatch,
    execution_mode,
):
    """Refresh is atomic, and an old caller cannot spend a new verification."""
    import model_tools
    import run_agent
    from agent import conversation_loop
    from tools import image_generation_tool

    agent = _make_long_lived_agent()
    non_registry = {
        "memory_recall": _tool("memory_recall"),
        "lcm_grep": _tool("lcm_grep"),
        "mcp_weather": _tool("mcp_weather"),
    }
    agent.tools = [_tool("read_file"), *non_registry.values()]
    agent.valid_tool_names = set(_tool_names(agent.tools))
    agent._registry_tool_names = {"read_file"}
    agent._image_capability_fingerprint = "capability-before-verification"

    runtime = {
        "schema_version": 1,
        "fingerprint": "capability-config-static",
        "available": True,
        "definitions": [_tool("read_file"), _tool("image_generate")],
        "raise_on_definitions": False,
        "definition_attempts": 0,
    }

    def readiness():
        return {
            "configured": True,
            "available": runtime["available"],
            "reason_code": (
                "ready" if runtime["available"] else "verification_required"
            ),
            "verification_status": (
                "verified"
                if runtime["available"]
                else "configured_unverified"
            ),
            "verification_schema_version": runtime["schema_version"],
            "verification_fingerprint": runtime["fingerprint"],
            "capability_fingerprint": runtime["fingerprint"],
            "runtime_fingerprint": runtime["fingerprint"],
        }

    def current_definitions(**_kwargs):
        runtime["definition_attempts"] += 1
        if runtime["raise_on_definitions"]:
            raise RuntimeError("registry refresh failed before assignment")
        return list(runtime["definitions"])

    monkeypatch.setattr(
        image_generation_tool,
        "get_image_generation_readiness",
        readiness,
    )
    monkeypatch.setattr(model_tools, "get_tool_definitions", current_definitions)
    monkeypatch.setattr(run_agent, "get_tool_definitions", current_definitions)
    conversation_calls = []
    monkeypatch.setattr(
        conversation_loop,
        "run_conversation",
        lambda *_args, **_kwargs: conversation_calls.append("turn")
        or {"completed": True, "final_response": "stubbed"},
    )

    violations = []
    agent.run_conversation("refresh image capability after verification")
    names_after_add = _tool_names(agent.tools)
    if "image_generate" not in names_after_add:
        violations.append("next-turn refresh did not add image_generate")
    if not set(non_registry).issubset(names_after_add):
        violations.append("add refresh dropped memory/context/MCP tools")
    if getattr(agent, "_image_capability_fingerprint", "") != runtime["fingerprint"]:
        violations.append("add refresh did not publish the current capability fingerprint")

    # Keep removal independently observable even while this RED test runs
    # against the pre-refresh implementation.
    if "image_generate" not in _tool_names(agent.tools):
        agent.tools.insert(1, _tool("image_generate"))
        agent.valid_tool_names.add("image_generate")
        agent._registry_tool_names = {"read_file", "image_generate"}
        agent._image_capability_fingerprint = "capability-current-add"

    runtime.update(
        available=False,
        definitions=[_tool("read_file")],
    )
    agent.run_conversation("refresh image capability after revocation")
    names_after_remove = _tool_names(agent.tools)
    if "image_generate" in names_after_remove:
        violations.append("next-turn refresh did not remove image_generate")
    if not set(non_registry).issubset(names_after_remove):
        violations.append("remove refresh dropped memory/context/MCP tools")
    if getattr(agent, "_image_capability_fingerprint", "") != runtime["fingerprint"]:
        violations.append(
            "remove refresh did not publish the revoked capability fingerprint"
        )

    before_failure = {
        "tools": list(agent.tools),
        "valid_tool_names": set(agent.valid_tool_names),
        "registry_tool_names": set(agent._registry_tool_names),
        "fingerprint": getattr(agent, "_image_capability_fingerprint", ""),
        "runtime_identity": getattr(agent, "_image_runtime_identity", None),
        "cached_system_prompt": agent._cached_system_prompt,
    }
    attempts_before_failure = runtime["definition_attempts"]
    runtime.update(
        schema_version=2,
        raise_on_definitions=True,
    )
    try:
        agent.run_conversation("refresh must roll back atomically")
    except RuntimeError:
        violations.append("refresh build failure escaped the next-turn boundary")
    if runtime["definition_attempts"] != attempts_before_failure + 1:
        violations.append("next-turn refresh did not attempt to rebuild registry schemas")
    if (
        agent.tools != before_failure["tools"]
        or set(agent.valid_tool_names) != before_failure["valid_tool_names"]
        or set(agent._registry_tool_names) != before_failure["registry_tool_names"]
        or getattr(agent, "_image_capability_fingerprint", "")
        != before_failure["fingerprint"]
        or getattr(agent, "_image_runtime_identity", None)
        != before_failure["runtime_identity"]
        or agent._cached_system_prompt != before_failure["cached_system_prompt"]
    ):
        violations.append("failed refresh partially mutated the live Agent")

    # A model call was produced under this old fingerprint. The WebUI state
    # becomes verified for a different current fingerprint before dispatch.
    runtime.update(
        schema_version=1,
        fingerprint="capability-current-verified",
        available=True,
        definitions=[_tool("read_file"), _tool("image_generate")],
        raise_on_definitions=False,
    )
    caller_fingerprint = "capability-caller-before-config-change"
    agent._image_capability_fingerprint = caller_fingerprint
    agent._image_generation_runtime_fingerprint = caller_fingerprint
    if "image_generate" not in _tool_names(agent.tools):
        agent.tools.append(_tool("image_generate"))
    agent.valid_tool_names.add("image_generate")
    agent._registry_tool_names.add("image_generate")
    provider_calls = []

    def provider_call(*args, **kwargs):
        provider_calls.append((args, kwargs))
        return json.dumps(
            {
                "success": True,
                "provider": "dashscope",
                "model": "qwen-image-2.0-pro",
            }
        )

    monkeypatch.setattr(
        image_generation_tool,
        "_dispatch_to_plugin_provider",
        provider_call,
    )
    assistant_message = SimpleNamespace(
        content="",
        tool_calls=[
            _tool_call(
                "image_generate",
                {"prompt": "draw a stale caller"},
                f"b3-{execution_mode}",
            )
        ],
    )
    messages = []
    if execution_mode == "sequential":
        agent._execute_tool_calls_sequential(
            assistant_message,
            messages,
            "b3-task",
        )
    else:
        agent._execute_tool_calls_concurrent(
            assistant_message,
            messages,
            "b3-task",
        )

    serialized_results = json.dumps(messages, ensure_ascii=False, default=str)
    if "capability_caller_stale" not in serialized_results:
        violations.append(
            f"{execution_mode} path did not return capability_caller_stale"
        )
    if provider_calls:
        violations.append(
            f"{execution_mode} path called Provider for a stale caller fingerprint"
        )

    assert conversation_calls == ["turn", "turn", "turn"]
    assert violations == [], "; ".join(violations)


def test_image_handler_missing_caller_fingerprint_fails_before_provider(
    monkeypatch,
):
    from agent import image_runtime
    from tools import image_generation_tool

    snapshot = {
        "schema_version": 1,
        "fingerprint": "verified-image-config",
        "status": "verified",
        "available": True,
        "provider": "dashscope",
        "model": "qwen-image-2.0-pro",
    }
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(snapshot),
    )
    provider_calls = []
    monkeypatch.setattr(
        image_generation_tool,
        "_dispatch_to_plugin_provider",
        lambda *_args, **_kwargs: provider_calls.append(True) or "{}",
    )

    result = image_generation_tool._handle_image_generate(
        {"prompt": "missing caller identity"}
    )

    assert "capability_caller_stale" in result
    assert provider_calls == []


def test_forced_refresh_rejects_drift_before_publishing_identity(
    monkeypatch,
):
    """A sentinel init retries until definitions and identity are one generation."""
    import model_tools
    from agent import image_runtime

    before = {
        "schema_version": 1,
        "fingerprint": "config-before",
        "status": "configured_unverified",
        "available": False,
    }
    after = {
        "schema_version": 1,
        "fingerprint": "config-after",
        "status": "verified",
        "available": True,
    }
    snapshots = iter((before, after, after, after))
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_args, **_kwargs: dict(next(snapshots)),
    )
    attempts = []
    monkeypatch.setattr(
        model_tools,
        "get_tool_definitions",
        lambda **_kwargs: attempts.append("build")
        or [_tool("read_file"), _tool("image_generate")],
    )
    agent = SimpleNamespace(
        _image_runtime_lock=threading.RLock(),
        _image_runtime_identity=None,
        _image_capability_fingerprint="",
        _registry_tool_names={"read_file"},
        tools=[_tool("read_file"), _tool("memory_recall")],
        valid_tool_names={"read_file", "memory_recall"},
        enabled_toolsets=["image_gen"],
        disabled_toolsets=None,
        quiet_mode=True,
        _cached_system_prompt="OLD",
        _force_system_prompt_rebuild=False,
    )
    original_tools = list(agent.tools)

    assert image_runtime.refresh_agent_image_runtime(agent) is False
    assert agent.tools == original_tools
    assert agent._image_runtime_identity is None

    assert image_runtime.refresh_agent_image_runtime(agent) is True
    assert "image_generate" in _tool_names(agent.tools)
    assert "memory_recall" in _tool_names(agent.tools)
    assert agent._image_runtime_identity == (
        1,
        "config-after",
        "verified",
        True,
    )
    assert attempts == ["build", "build"]


def test_old_concurrent_tool_schema_read_cannot_restore_revoked_identity(
    monkeypatch,
):
    """A paused old reader cannot republish an invalidated outer cache entry."""
    import model_tools

    old_identity = (1, "effective-fingerprint-old", "verified", True)
    new_identity = (2, "effective-fingerprint-new", "configured_unverified", False)
    runtime = {
        "identity": old_identity,
        "definitions": [_tool("read_file"), _tool("image_generate")],
    }
    old_snapshot_captured = threading.Event()
    release_old_reader = threading.Event()
    pause_old_reader = {"enabled": False, "done": False}

    def current_identity():
        captured = runtime["identity"]
        if (
            pause_old_reader["enabled"]
            and not pause_old_reader["done"]
            and threading.current_thread().name == "b3-old-schema-reader"
        ):
            pause_old_reader["done"] = True
            old_snapshot_captured.set()
            assert release_old_reader.wait(timeout=5)
        return captured

    monkeypatch.setattr(
        model_tools,
        "_image_capability_cache_identity",
        current_identity,
    )
    monkeypatch.setattr(
        model_tools,
        "_compute_tool_definitions",
        lambda *_args, **_kwargs: list(runtime["definitions"]),
    )

    warmed = model_tools.get_tool_definitions(
        enabled_toolsets=["image_gen"],
        quiet_mode=True,
    )
    assert "image_generate" in _tool_names(warmed)

    pause_old_reader["enabled"] = True
    old_result = {}
    old_reader = threading.Thread(
        name="b3-old-schema-reader",
        target=lambda: old_result.setdefault(
            "definitions",
            model_tools.get_tool_definitions(
                enabled_toolsets=["image_gen"],
                quiet_mode=True,
            ),
        ),
    )
    old_reader.start()
    assert old_snapshot_captured.wait(timeout=5)

    runtime.update(
        identity=new_identity,
        definitions=[_tool("read_file")],
    )
    current = model_tools.get_tool_definitions(
        enabled_toolsets=["image_gen"],
        quiet_mode=True,
    )
    assert "image_generate" not in _tool_names(current)
    assert model_tools._last_image_capability_identity == new_identity

    release_old_reader.set()
    old_reader.join(timeout=5)
    assert not old_reader.is_alive()

    violations = []
    if "image_generate" in _tool_names(old_result["definitions"]):
        violations.append("old reader returned revoked image_generate schema")
    if model_tools._last_image_capability_identity != new_identity:
        violations.append("old reader rolled the global identity marker backward")

    assert violations == [], "; ".join(violations)


def test_cache_hit_rechecks_identity_before_returning_old_schema(
    monkeypatch,
):
    """A capability change after initial sync must invalidate a pending hit."""
    import hermes_cli.config
    import model_tools

    old_identity = (1, "effective-fingerprint-old", "verified", True)
    new_identity = (2, "effective-fingerprint-new", "configured_unverified", False)
    runtime = {
        "identity": old_identity,
        "definitions": [_tool("read_file"), _tool("image_generate")],
    }

    monkeypatch.setattr(
        model_tools,
        "_image_capability_cache_identity",
        lambda: runtime["identity"],
    )
    monkeypatch.setattr(
        model_tools,
        "_compute_tool_definitions",
        lambda *_args, **_kwargs: list(runtime["definitions"]),
    )

    warmed = model_tools.get_tool_definitions(
        enabled_toolsets=["image_gen"],
        quiet_mode=True,
    )
    assert "image_generate" in _tool_names(warmed)

    before_cache_lookup = threading.Event()
    release_reader = threading.Event()
    original_get_config_path = hermes_cli.config.get_config_path

    def pausing_get_config_path():
        if threading.current_thread().name == "b3-pending-cache-hit":
            before_cache_lookup.set()
            assert release_reader.wait(timeout=5)
        return original_get_config_path()

    monkeypatch.setattr(
        hermes_cli.config,
        "get_config_path",
        pausing_get_config_path,
    )

    result = {}
    reader = threading.Thread(
        name="b3-pending-cache-hit",
        target=lambda: result.setdefault(
            "definitions",
            model_tools.get_tool_definitions(
                enabled_toolsets=["image_gen"],
                quiet_mode=True,
            ),
        ),
    )
    reader.start()
    assert before_cache_lookup.wait(timeout=5)

    runtime.update(
        identity=new_identity,
        definitions=[_tool("read_file")],
    )
    release_reader.set()
    reader.join(timeout=5)
    assert not reader.is_alive()

    violations = []
    if "image_generate" in _tool_names(result["definitions"]):
        violations.append("pending cache hit returned revoked image_generate schema")
    if model_tools._last_image_capability_identity != new_identity:
        violations.append("pending cache hit did not publish the current identity")

    assert violations == [], "; ".join(violations)


def test_identity_oscillation_fail_closed_preserves_unrelated_schemas(
    monkeypatch,
):
    """Exhausted identity retries remove only the gated image schema."""
    import model_tools

    old_identity = (1, "effective-fingerprint-old", "verified", True)
    new_identity = (2, "effective-fingerprint-new", "configured_unverified", False)
    identities = iter(
        (
            old_identity,
            old_identity,
            new_identity,
            old_identity,
            new_identity,
        )
    )
    compute_calls = []
    monkeypatch.setattr(
        model_tools,
        "_image_capability_cache_identity",
        lambda: next(identities),
    )
    monkeypatch.setattr(
        model_tools,
        "_compute_tool_definitions",
        lambda *_args, **_kwargs: compute_calls.append(True)
        or [_tool("read_file"), _tool("image_generate")],
    )

    definitions = model_tools.get_tool_definitions(
        enabled_toolsets=["image_gen"],
        quiet_mode=True,
    )

    assert _tool_names(definitions) == {"read_file"}
    assert compute_calls == [True]


@pytest.mark.parametrize("invalid_schema_version", [True, 1.0, "1"])
def test_current_image_runtime_snapshot_requires_literal_integer_schema(
    monkeypatch,
    invalid_schema_version,
):
    """JSON lookalikes must not authorize a versioned image capability."""
    from agent import image_runtime
    from tools import image_generation_tool

    monkeypatch.setattr(
        image_generation_tool,
        "get_image_generation_readiness",
        lambda: {
            "verification_schema_version": invalid_schema_version,
            "verification_status": "verified",
            "capability_fingerprint": "invalid-schema-fingerprint",
            "available": True,
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "reason_code": "ready",
        },
    )

    snapshot = image_runtime.current_image_runtime_snapshot()

    assert snapshot["schema_version"] == 0
    assert snapshot["status"] == "configured_unverified"
    assert snapshot["available"] is False
    assert snapshot["reason_code"] == "verification_schema_mismatch"


def test_verification_runtime_snapshot_rejects_boolean_schema(monkeypatch):
    """``True == 1`` must not bypass the outer authorization snapshot gate."""
    from agent import image_runtime

    monkeypatch.setattr(
        image_runtime,
        "current_image_runtime_snapshot",
        lambda: {
            "schema_version": True,
            "fingerprint": "boolean-schema-fingerprint",
            "status": "verified",
            "available": True,
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "reason_code": "ready",
        },
    )

    snapshot = image_runtime.verification_runtime_snapshot("image_generation")

    assert snapshot["status"] == "configured_unverified"
    assert snapshot["available"] is False
    assert snapshot["reason_code"] == "verification_schema_mismatch"


def test_vision_runtime_fails_closed_when_secure_secret_resolution_rejects_env(
    monkeypatch,
    tmp_path,
):
    from agent import image_gen_verification, image_runtime
    from agent import provider_credentials
    from hermes_cli import config as hermes_config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(
        hermes_config,
        "load_config",
        lambda: {
            "auxiliary": {
                "vision": {
                    "provider": "alibaba",
                    "model": "qwen3-vl-plus",
                    "endpoint_mode": "public",
                    "region": "cn-beijing",
                }
            }
        },
    )
    monkeypatch.setattr(
        hermes_config,
        "load_env",
        lambda: {"DASHSCOPE_API_KEY": "unsafe-fallback-secret"},
    )
    monkeypatch.setenv(
        "DASHSCOPE_API_KEY",
        "unsafe-process-fallback-secret",
    )
    monkeypatch.setattr(
        provider_credentials,
        "resolve_secret_env_value",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("credential env contains duplicate keys")
        ),
    )
    monkeypatch.setattr(
        image_gen_verification,
        "image_gen_runtime_context",
        lambda: SimpleNamespace(config_path=config_path),
    )
    monkeypatch.setattr(
        image_runtime,
        "active_profile_name",
        lambda: "default",
    )
    monkeypatch.setattr(
        image_runtime,
        "vision_verification_state_path",
        lambda _profile: tmp_path / "vision-state.json",
    )

    snapshot = image_runtime.current_vision_runtime_snapshot()

    assert snapshot["configured"] is False
    assert snapshot["available"] is False
    assert snapshot["reason_code"] == "vision_not_configured"
