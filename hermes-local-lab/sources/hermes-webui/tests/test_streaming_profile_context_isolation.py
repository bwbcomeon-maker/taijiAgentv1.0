"""Concurrent WebUI streams must keep profile-scoped runtime state isolated."""

from __future__ import annotations

import concurrent.futures
import contextvars
import os
import queue
import sys
import threading
import types
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_concurrent_streams_isolate_profile_config_secret_proxy_and_agent_worker(
    tmp_path,
    monkeypatch,
):
    """Two streams must never route through each other's profile.

    The barriers deliberately hold both streams after profile setup and before
    their reads.  A process-global ``os.environ`` implementation therefore
    deterministically collapses both readers onto whichever profile wrote
    last, while a context-local implementation lets each stream proceed with
    its own home/config/.env and propagates that scope into an Agent worker.
    """
    from api import config as cfg
    from api import profiles
    from api import streaming
    from agent.provider_credentials import credential_secret_env, resolve_api_key
    from agent.safe_outbound_http import resolve_trusted_proxy_profile
    from hermes_constants import get_config_path, get_env_path, get_hermes_home

    profile_root = tmp_path / "profiles"
    sentinel_home = tmp_path / "process-sentinel"
    sentinel_home.mkdir()
    sentinel_config = sentinel_home / "config.yaml"
    sentinel_config.write_text("{}\n", encoding="utf-8")

    credential_ref = "shared-custom-provider"
    secret_env = credential_secret_env(credential_ref)
    specs = {
        "stream-alpha-session": {
            "profile": "alpha",
            "home": profile_root / "alpha",
            "marker": "ALPHA_CONFIG",
            "secret": "alpha-secret",
            "proxy_url": "https://alpha-proxy.example.test",
            "max_turns": 11,
        },
        "stream-beta-session": {
            "profile": "beta",
            "home": profile_root / "beta",
            "marker": "BETA_CONFIG",
            "secret": "beta-secret",
            "proxy_url": "https://beta-proxy.example.test",
            "max_turns": 22,
        },
    }
    for spec in specs.values():
        spec["home"].mkdir(parents=True)
        config_data = {
            "profile_marker": spec["marker"],
            "agent": {"max_turns": spec["max_turns"]},
            "provider_credentials": [
                {
                    "id": credential_ref,
                    "provider_family": "custom",
                    "auth_type": "api_key",
                    "secret_env": secret_env,
                }
            ],
            "trusted_proxy_profiles": [
                {
                    "name": "approved",
                    "proxy_url": spec["proxy_url"],
                    "approved": True,
                    "capabilities": [
                        "public_egress",
                        "dns_ip_classification",
                    ],
                    "proxy_connect_scope": "public_direct",
                }
            ],
        }
        (spec["home"] / "config.yaml").write_text(
            yaml.safe_dump(config_data, sort_keys=False),
            encoding="utf-8",
        )
        (spec["home"] / ".env").write_text(
            f"{secret_env}={spec['secret']}\n",
            encoding="utf-8",
        )

    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(sentinel_home))
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(sentinel_config))
    monkeypatch.delenv(secret_env, raising=False)

    class FakeSession:
        def __init__(self, session_id, profile, active_stream_id):
            self.session_id = session_id
            self.title = f"Profile {profile}"
            self.workspace = str(tmp_path)
            self.model = "test-model"
            self.model_provider = None
            self.profile = profile
            self.personality = None
            self.messages = []
            self.context_messages = []
            self.tool_calls = []
            self.input_tokens = 0
            self.output_tokens = 0
            self.estimated_cost = None
            self.context_length = 0
            self.threshold_tokens = 0
            self.last_prompt_tokens = 0
            self.active_stream_id = active_stream_id
            self.pending_user_message = None
            self.pending_attachments = []
            self.pending_started_at = None
            self.llm_title_generated = True

        def save(self, *args, **kwargs):
            return None

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
                "personality": self.personality,
            }

    sessions = {
        session_id: FakeSession(
            session_id,
            spec["profile"],
            f"{spec['profile']}-stream",
        )
        for session_id, spec in specs.items()
    }

    class FakeSessionDB:
        def __init__(self, db_path=None):
            self.db_path = db_path

        def close(self):
            return None

    # Four rendezvous points make the cross-profile overlap deterministic and
    # also fail fast if an implementation tries to hold _ENV_LOCK for the full
    # duration of one stream.
    config_read_enter = threading.Barrier(2)
    config_read_exit = threading.Barrier(2)
    agent_read_enter = threading.Barrier(2)
    agent_read_exit = threading.Barrier(2)
    worker_read_enter = threading.Barrier(2)
    worker_read_exit = threading.Barrier(2)
    captures = {}

    def profile_snapshot():
        config_path = get_config_path()
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        proxy = resolve_trusted_proxy_profile("approved")
        return {
            "home": str(get_hermes_home()),
            "config_path": str(config_path),
            "env_path": str(get_env_path()),
            "config_marker": config_data.get("profile_marker"),
            "api_key": resolve_api_key("custom", credential_ref),
            "proxy_url": proxy.proxy_url,
            "process_home": os.environ.get("HERMES_HOME"),
            "process_config_path": os.environ.get("HERMES_CONFIG_PATH"),
        }

    def load_stream_config(config_path):
        config_read_enter.wait(timeout=10)
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        config_read_exit.wait(timeout=10)
        return loaded

    class CapturingAgent:
        def __init__(
            self,
            *,
            session_id=None,
            session_db=None,
            enabled_toolsets=None,
            max_iterations=None,
            **kwargs,
        ):
            runtime_api_key = kwargs.get("api_key")
            self.session_id = session_id
            self.session_db = session_db
            self._session_db = session_db
            self.session_prompt_tokens = 0
            self.session_completion_tokens = 0
            self.session_estimated_cost_usd = None
            self.context_compressor = None
            self._last_error = None
            self.ephemeral_system_prompt = None

            agent_read_enter.wait(timeout=10)
            main_snapshot = profile_snapshot()
            main_snapshot["enabled_toolsets"] = list(enabled_toolsets or [])
            main_snapshot["max_iterations"] = max_iterations
            main_snapshot["runtime_api_key"] = runtime_api_key
            captures[session_id] = {"agent": main_snapshot}
            agent_read_exit.wait(timeout=10)

        def run_conversation(self, **kwargs):
            worker_read_enter.wait(timeout=10)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                context = contextvars.copy_context()
                worker_snapshot = executor.submit(
                    context.run,
                    profile_snapshot,
                ).result(timeout=10)
            captures[self.session_id]["worker"] = worker_snapshot
            worker_read_exit.wait(timeout=10)

            history = list(kwargs.get("conversation_history") or [])
            return {
                "messages": history
                + [
                    {"role": "user", "content": kwargs.get("persist_user_message", "")},
                    {"role": "assistant", "content": "ok"},
                ]
            }

        def interrupt(self, _message):
            return None

    fake_runtime_module = types.ModuleType("hermes_cli.runtime_provider")

    def fake_resolve_runtime_provider(requested=None):
        return {
            "provider": requested or "test-provider",
            "api_key": resolve_api_key("custom", credential_ref),
            "base_url": None,
        }

    fake_runtime_module.resolve_runtime_provider = fake_resolve_runtime_provider
    fake_hermes_cli = types.ModuleType("hermes_cli")
    fake_hermes_cli.runtime_provider = fake_runtime_module
    fake_hermes_state = types.ModuleType("hermes_state")
    fake_hermes_state.SessionDB = FakeSessionDB
    fake_hermes_state.install_state_write_guard = lambda _guard: None

    homes_by_profile = {
        spec["profile"]: spec["home"]
        for spec in specs.values()
    }
    monkeypatch.setattr(streaming, "get_session", sessions.__getitem__)
    monkeypatch.setattr(streaming, "_get_ai_agent", lambda: CapturingAgent)
    monkeypatch.setattr(
        streaming,
        "resolve_model_provider",
        lambda _model: ("test-model", "test-provider", None),
    )
    monkeypatch.setattr(streaming, "_maybe_schedule_title_refresh", lambda *args, **kwargs: None)
    monkeypatch.setattr(streaming, "safe_toolsets_for_workspace", lambda toolsets, _workspace: toolsets)
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", homes_by_profile.__getitem__)
    monkeypatch.setattr(
        profiles,
        "get_profile_runtime_env",
        lambda home: {"HERMES_CONFIG_PATH": str(Path(home) / "config.yaml")},
    )
    monkeypatch.setattr("api.config._load_yaml_config_file", load_stream_config)
    monkeypatch.setattr(
        "api.config._resolve_cli_toolsets",
        lambda config_data: [config_data["profile_marker"]],
    )
    monkeypatch.setattr("api.config.load_settings", lambda: {})
    monkeypatch.setitem(sys.modules, "hermes_cli", fake_hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.runtime_provider", fake_runtime_module)
    monkeypatch.setitem(sys.modules, "hermes_state", fake_hermes_state)

    with cfg.SESSION_AGENT_CACHE_LOCK:
        cfg.SESSION_AGENT_CACHE.clear()
    streaming.STREAMS.clear()
    streaming.CANCEL_FLAGS.clear()
    streaming.AGENT_INSTANCES.clear()
    streaming.STREAM_PARTIAL_TEXT.clear()
    streaming.STREAM_REASONING_TEXT.clear()
    streaming.STREAM_LIVE_TOOL_CALLS.clear()
    for session in sessions.values():
        streaming.STREAMS[session.active_stream_id] = queue.Queue()

    failures = []

    def run_stream(session):
        try:
            streaming._run_agent_streaming(
                session_id=session.session_id,
                msg_text=f"hello from {session.profile}",
                model="test-model",
                model_provider="test-provider",
                workspace=str(tmp_path),
                stream_id=session.active_stream_id,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    threads = [
        threading.Thread(
            target=run_stream,
            args=(session,),
            name=f"profile-stream-{session.profile}",
        )
        for session in sessions.values()
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert failures == []
    assert not [thread.name for thread in threads if thread.is_alive()]
    assert set(captures) == set(specs)

    for session_id, spec in specs.items():
        expected = {
            "home": str(spec["home"]),
            "config_path": str(spec["home"] / "config.yaml"),
            "env_path": str(spec["home"] / ".env"),
            "config_marker": spec["marker"],
            "api_key": spec["secret"],
            "proxy_url": spec["proxy_url"],
            "process_home": str(sentinel_home),
            "process_config_path": str(sentinel_config),
        }
        assert captures[session_id]["agent"] == {
            **expected,
            "enabled_toolsets": [spec["marker"]],
            "max_iterations": spec["max_turns"],
            "runtime_api_key": spec["secret"],
        }
        assert captures[session_id]["worker"] == expected

    assert os.environ.get("HERMES_HOME") == str(sentinel_home)
    assert os.environ.get("HERMES_CONFIG_PATH") == str(sentinel_config)
