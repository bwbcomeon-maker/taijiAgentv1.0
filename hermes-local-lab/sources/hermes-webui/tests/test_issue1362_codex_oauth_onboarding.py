"""Regression tests for issue #1362 — Codex OAuth from onboarding."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def _run_anthropic_oauth_js(scenario: str) -> dict:
    if NODE is None:
        pytest.skip("node not on PATH")
    source = (REPO / "static" / "onboarding.js").read_text(encoding="utf-8")
    start = source.index("/* ── Anthropic / Claude Code credential-link flow ── */")
    anthropic_source = source[start:]
    driver = f"""
const vm=require('vm');
const source={json.dumps(anthropic_source)};
const flow={{style:{{}},innerHTML:''}};
const button={{disabled:true,textContent:'...'}};
const scheduled=[];
let cancelResolve=null;
let cancelReject=null;
const context={{
  console,
  JSON,
  Math,
  Number,
  encodeURIComponent,
  setTimeout:(fn)=>{{scheduled.push(fn);return scheduled.length;}},
  clearTimeout:()=>{{}},
  $:(id)=>id==='anthropicOAuthFlow'?flow:(id==='anthropicOAuthBtn'?button:null),
  esc:(value)=>String(value||''),
  showToast:()=>{{}},
  loadOnboardingWizard:async()=>{{}},
  api:(path,options)=>{{
    if(path==='/api/onboarding/oauth/cancel'){{
      return new Promise((resolve,reject)=>{{cancelResolve=resolve;cancelReject=reject;}});
    }}
    return Promise.resolve({{status:'pending'}});
  }},
}};
vm.createContext(context);
vm.runInContext(source,context);
(async()=>{{
  vm.runInContext("_anthropicOAuthFlowId='flow-1'",context);
  const cancelPromise=vm.runInContext('cancelAnthropicOAuth()',context);
  await Promise.resolve();
  const during={{
    flowId:vm.runInContext('_anthropicOAuthFlowId',context),
    disabled:button.disabled,
    html:flow.innerHTML,
  }};
  if({json.dumps(scenario)}==='committing'){{
    cancelResolve({{ok:true,provider:'anthropic',flow_id:'flow-1',status:'committing'}});
  }}else{{
    cancelReject(new Error('cancel transport failed'));
  }}
  await cancelPromise;
  const after={{
    flowId:vm.runInContext('_anthropicOAuthFlowId',context),
    disabled:button.disabled,
    html:flow.innerHTML,
    scheduled:scheduled.length,
  }};
  process.stdout.write(JSON.stringify({{during,after}}));
}})().catch((error)=>{{console.error(error);process.exit(1);}});
"""
    result = subprocess.run(
        [NODE, "-e", driver],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _run_onboarding_provider_focus_js() -> dict:
    if NODE is None:
        pytest.skip("node not on PATH")
    source = (REPO / "static" / "onboarding.js").read_text(encoding="utf-8")
    start = source.index("function syncOnboardingProvider(value)")
    end = source.index("\n\nasync function loadOnboardingWizard", start)
    function_source = source[start:end]
    driver = f"""
const vm=require('vm');
const source={json.dumps(function_source)};
let focused='';
let renders=0;
const controls={{
  onboardingProviderSelect:{{focus:()=>{{focused='provider-select';}}}},
  anthropicOAuthBtn:{{focus:()=>{{focused='oauth-button';}}}},
}};
const context={{
  ONBOARDING:{{form:{{provider:'openrouter',model:'',baseUrl:''}}}},
  _getOnboardingSetupProvider:(value)=>value==='anthropic'
    ? {{id:'anthropic',default_model:'claude-sonnet-4.6',requires_base_url:false,default_base_url:''}}
    : null,
  _getOnboardingProviderModelChoices:()=>[],
  _renderOnboardingBody:()=>{{renders+=1;}},
  $:(id)=>controls[id]||null,
}};
vm.createContext(context);
vm.runInContext(source,context);
vm.runInContext("syncOnboardingProvider('anthropic')",context);
process.stdout.write(JSON.stringify({{
  focused,
  renders,
  provider:context.ONBOARDING.form.provider,
}}));
"""
    result = subprocess.run(
        [NODE, "-e", driver],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_onboarding_codex_oauth_routes_use_post_start_cancel_and_get_poll():
    routes = (REPO / "api" / "routes.py").read_text(encoding="utf-8")
    get_idx = routes.find("def handle_get(")
    post_idx = routes.find("def handle_post(")
    assert get_idx != -1 and post_idx != -1
    get_body = routes[get_idx:post_idx]
    post_body = routes[post_idx:]

    assert '"/api/onboarding/oauth/poll"' in get_body
    assert '"/api/onboarding/oauth/start"' not in get_body
    assert '"/api/oauth/codex/start"' not in routes
    assert '"/api/oauth/codex/poll"' not in routes
    assert '"/api/onboarding/oauth/start"' in post_body
    assert '"/api/onboarding/oauth/cancel"' in post_body


def test_onboarding_oauth_rejects_unsupported_providers(monkeypatch):
    import api.oauth as oauth

    for provider in ("nous", "qwen-oauth", "copilot", "bogus"):
        with pytest.raises(ValueError):
            oauth.start_onboarding_oauth_flow({"provider": provider})


def test_start_payload_does_not_leak_provider_device_secrets(monkeypatch, tmp_path):
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(oauth, "_get_active_config_path", lambda: config_path)
    monkeypatch.setattr(oauth, "_request_codex_user_code", lambda: {
        "device_auth_id": "device-secret",
        "user_code": "ABCD-EFGH",
        "interval": 3,
    })
    monkeypatch.setattr(oauth, "_spawn_codex_oauth_worker", lambda flow_id: None)

    payload = oauth.start_onboarding_oauth_flow({"provider": "openai-codex"})

    assert payload["ok"] is True
    assert payload["provider"] == "openai-codex"
    assert payload["status"] == "pending"
    assert payload["verification_uri"] == "https://auth.openai.com/codex/device"
    assert payload["user_code"] == "ABCD-EFGH"
    serialized = json.dumps(payload)
    for forbidden in (
        "device_auth_id",
        "device-secret",
        "authorization_code",
        "code_verifier",
        "access_token",
        "refresh_token",
    ):
        assert forbidden not in serialized


def test_poll_returns_high_level_status_only(monkeypatch, tmp_path):
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    flow_id = "flow-test"
    oauth._OAUTH_FLOWS[flow_id] = {
        "provider": "openai-codex",
        "status": "pending",
        "device_auth_id": "device-secret",
        "user_code": "ABCD-EFGH",
        "code_verifier": "verifier-secret",
        "authorization_code": "auth-secret",
        "expires_at": time.time() + 60,
        "poll_interval_seconds": 3,
        "hermes_home": tmp_path,
    }

    payload = oauth.poll_onboarding_oauth_flow(flow_id)

    assert payload == {"ok": True, "provider": "openai-codex", "flow_id": flow_id, "status": "pending"}
    serialized = json.dumps(payload)
    for forbidden in ("device_auth_id", "device-secret", "code_verifier", "authorization_code"):
        assert forbidden not in serialized


def test_cancel_marks_flow_cancelled_and_poll_stops(tmp_path):
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    flow_id = "flow-cancel"
    oauth._OAUTH_FLOWS[flow_id] = {
        "provider": "openai-codex",
        "status": "pending",
        "expires_at": time.time() + 60,
        "hermes_home": tmp_path,
    }

    cancelled = oauth.cancel_onboarding_oauth_flow({"flow_id": flow_id})
    polled = oauth.poll_onboarding_oauth_flow(flow_id)

    assert cancelled["status"] == "cancelled"
    assert polled["status"] == "cancelled"


def test_cancel_during_token_exchange_does_not_persist_credentials(monkeypatch, tmp_path):
    """Cancel arriving while the worker is mid-network-call must win.

    Without the post-exchange status re-check, the worker would proceed to
    persist credentials to auth.json AND override the cancelled status with
    "success" — silently storing tokens the user explicitly aborted.
    """
    import threading
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()

    poll_started = threading.Event()
    poll_continue = threading.Event()

    def _slow_poll(device_auth_id, user_code):
        poll_started.set()
        assert poll_continue.wait(timeout=5)
        return {"authorization_code": "auth-code", "code_verifier": "verifier"}

    def _exchange(authorization_code, code_verifier):
        return {"access_token": "ACCESS", "refresh_token": "REFRESH"}

    monkeypatch.setattr(oauth, "_poll_codex_authorization", _slow_poll)
    monkeypatch.setattr(oauth, "_exchange_codex_authorization", _exchange)

    flow_id = "race-flow"
    oauth._OAUTH_FLOWS[flow_id] = {
        "provider": "openai-codex",
        "status": "pending",
        "device_auth_id": "device-secret",
        "user_code": "ABCD-EFGH",
        "expires_at": time.time() + 600,
        "poll_interval_seconds": 1,
        "hermes_home": str(tmp_path),
        "config_path": str(tmp_path / "config.yaml"),
        "created_at": time.time(),
        "updated_at": time.time(),
    }

    worker = threading.Thread(target=oauth._run_codex_oauth_worker, args=(flow_id,), daemon=True)
    worker.start()
    assert poll_started.wait(timeout=5)

    oauth.cancel_onboarding_oauth_flow({"flow_id": flow_id})
    assert oauth._OAUTH_FLOWS[flow_id]["status"] == "cancelled"

    poll_continue.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    assert oauth._OAUTH_FLOWS[flow_id]["status"] == "cancelled"
    assert not (tmp_path / "auth.json").exists()


def test_expired_flow_reports_expired_and_drops_sensitive_lifecycle(tmp_path):
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    flow_id = "flow-expired"
    oauth._OAUTH_FLOWS[flow_id] = {
        "provider": "openai-codex",
        "status": "pending",
        "device_auth_id": "device-secret",
        "expires_at": time.time() - 1,
        "hermes_home": tmp_path,
    }

    payload = oauth.poll_onboarding_oauth_flow(flow_id)

    assert payload["status"] == "expired"
    assert oauth._OAUTH_FLOWS[flow_id]["status"] == "expired"
    assert "device_auth_id" not in oauth._OAUTH_FLOWS[flow_id]


def test_codex_credentials_written_to_active_profile_auth_json(monkeypatch, tmp_path):
    import api.oauth as oauth
    from api.onboarding import _provider_oauth_authenticated

    active_home = tmp_path / "active-profile"
    realish_home = tmp_path / "process-home"
    active_home.mkdir()
    realish_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: realish_home)

    auth_path = oauth._persist_codex_credentials(
        active_home,
        {"access_token": "access-secret", "refresh_token": "refresh-secret"},
    )

    assert auth_path == active_home / "auth.json"
    assert auth_path.exists()
    assert not (realish_home / ".hermes" / "auth.json").exists()
    mode = stat.S_IMODE(auth_path.stat().st_mode)
    assert mode == 0o600
    store = json.loads(auth_path.read_text(encoding="utf-8"))
    entry = store["credential_pool"]["openai-codex"][0]
    assert entry["auth_type"] == "oauth"
    assert entry["source"] == "manual:device_code"
    assert entry["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert _provider_oauth_authenticated("openai-codex", active_home) is True


def test_frontend_uses_onboarding_oauth_endpoints_and_no_secret_poll_url():
    js = (REPO / "static" / "onboarding.js").read_text(encoding="utf-8")
    assert "/api/onboarding/oauth/start" in js
    assert "/api/onboarding/oauth/poll" in js
    assert "/api/onboarding/oauth/cancel" in js
    assert "window.open(verification_uri" not in js
    assert "device_code=" not in js
    assert "device_code" not in js
    assert "flow_id" in js
    assert "copyCodexOAuthCode" in js
    assert "cancelCodexOAuth" in js


def test_unsupported_note_mentions_codex_and_claude_as_in_app():
    src = (REPO / "api" / "onboarding.py").read_text(encoding="utf-8")
    start = src.find("_UNSUPPORTED_PROVIDER_NOTE")
    body = src[start:start + 500]
    assert "OpenAI Codex, and GitHub" not in body
    assert "OpenAI Codex" in body and "authenticated in this onboarding flow" in body
    assert "Claude" in body or "Anthropic" in body


# ── Claude / Anthropic OAuth slice ─────────────────────────────────────────


def test_claude_provider_aliases_normalize_to_anthropic(monkeypatch, tmp_path):
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(oauth, "_get_active_config_path", lambda: config_path)
    monkeypatch.setattr(oauth, "_read_claude_code_credentials", lambda: None)
    monkeypatch.setattr(oauth, "_spawn_anthropic_credential_worker", lambda fid: None)

    for alias in ("anthropic", "claude", "claude-code"):
        payload = oauth.start_onboarding_oauth_flow({"provider": alias})
        assert payload["ok"] is True
        assert payload["provider"] == "anthropic"
        assert payload["status"] == "pending"


def test_anthropic_immediate_success_when_credentials_exist(monkeypatch, tmp_path):
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(oauth, "_get_active_config_path", lambda: config_path)
    monkeypatch.setattr(oauth, "_read_claude_code_credentials", lambda: {
        "accessToken": "cc-access-secret",
        "refreshToken": "cc-refresh-secret",
        "expiresAt": 9999999999999,
    })
    linked = []
    monkeypatch.setattr(
        oauth,
        "_link_anthropic_credentials",
        lambda path: linked.append(str(path)),
    )

    payload = oauth.start_onboarding_oauth_flow({"provider": "anthropic"})

    assert payload["status"] == "success"
    assert payload["provider"] == "anthropic"
    assert linked == [str(config_path)]
    serialized = json.dumps(payload)
    for forbidden in ("cc-access-secret", "cc-refresh-secret", "accessToken", "refreshToken", "access_token", "refresh_token"):
        assert forbidden not in serialized


def test_anthropic_pending_payload_is_action_only_and_secret_free(monkeypatch, tmp_path):
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(oauth, "_get_active_config_path", lambda: config_path)
    monkeypatch.setattr(oauth, "_read_claude_code_credentials", lambda: None)
    monkeypatch.setattr(oauth, "_spawn_anthropic_credential_worker", lambda fid: None)

    payload = oauth.start_onboarding_oauth_flow({"provider": "anthropic"})

    assert payload["status"] == "pending"
    assert payload["provider"] == "anthropic"
    assert payload["flow_id"]
    assert "action_required" in payload
    assert "服务器上未找到 Claude Code 凭据" in payload["action_required"]
    assert "claude setup-token" in payload["action_required"]
    serialized = json.dumps(payload)
    for forbidden in (
        "access_token", "refresh_token", "accessToken", "refreshToken",
        ".credentials.json", ".claude", "hermes_home", str(tmp_path),
        "ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN",
    ):
        assert forbidden not in serialized


def test_anthropic_poll_and_cancel_return_high_level_status(tmp_path):
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    flow_id = "claude-flow-test"
    oauth._OAUTH_FLOWS[flow_id] = {
        "provider": "anthropic",
        "status": "pending",
        "expires_at": time.time() + 60,
        "poll_interval_seconds": 5,
        "hermes_home": str(tmp_path),
    }

    assert oauth.poll_onboarding_oauth_flow(flow_id) == {
        "ok": True,
        "provider": "anthropic",
        "flow_id": flow_id,
        "status": "pending",
    }
    assert oauth.cancel_onboarding_oauth_flow({"flow_id": flow_id}) == {
        "ok": True,
        "provider": "anthropic",
        "flow_id": flow_id,
        "status": "cancelled",
    }


def test_anthropic_worker_detects_credentials_and_cancel_wins(monkeypatch, tmp_path):
    import threading
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    started = threading.Event()
    proceed = threading.Event()
    linked = []

    def _slow_read_creds():
        started.set()
        assert proceed.wait(timeout=5)
        return {"accessToken": "cc-access-secret", "refreshToken": "cc-refresh-secret"}

    monkeypatch.setattr(oauth, "_read_claude_code_credentials", _slow_read_creds)
    monkeypatch.setattr(
        oauth,
        "_link_anthropic_credentials",
        lambda path: linked.append(str(path)),
    )

    flow_id = "claude-race-flow"
    oauth._OAUTH_FLOWS[flow_id] = {
        "provider": "anthropic",
        "status": "pending",
        "expires_at": time.time() + 600,
        "poll_interval_seconds": 1,
        "hermes_home": str(tmp_path),
        "config_path": str(tmp_path / "config.yaml"),
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    worker = threading.Thread(target=oauth._run_anthropic_credential_worker, args=(flow_id,), daemon=True)
    worker.start()
    assert started.wait(timeout=5)
    oauth.cancel_onboarding_oauth_flow({"flow_id": flow_id})
    proceed.set()
    worker.join(timeout=5)

    assert oauth._OAUTH_FLOWS[flow_id]["status"] == "cancelled"
    assert not linked


def test_anthropic_cancel_during_commit_reports_commit_and_finishes_success(
    monkeypatch,
    tmp_path,
):
    import threading
    import api.oauth as oauth
    from api.onboarding import _provider_oauth_authenticated

    oauth._OAUTH_FLOWS.clear()
    link_started = threading.Event()
    link_continue = threading.Event()
    monkeypatch.setattr(oauth.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(oauth, "_read_claude_code_credentials", lambda: {"accessToken": "cc-access-secret", "refreshToken": "cc-refresh-secret"})

    env_path = tmp_path / ".env"
    env_path.write_text("ANTHROPIC_API_KEY=old-key\nKEEP=value\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "old-key")
    original_clear = oauth._clear_anthropic_env_values

    def _slow_clear(config_path):
        link_started.set()
        assert link_continue.wait(timeout=5)
        original_clear(config_path)

    monkeypatch.setattr(oauth, "_clear_anthropic_env_values", _slow_clear)
    flow_id = "claude-link-cancel-race"
    oauth._OAUTH_FLOWS[flow_id] = {
        "provider": "anthropic",
        "status": "pending",
        "expires_at": time.time() + 60,
        "poll_interval_seconds": 1,
        "hermes_home": str(tmp_path),
        "config_path": str(tmp_path / "config.yaml"),
        "created_at": time.time(),
        "updated_at": time.time(),
    }

    worker = threading.Thread(target=oauth._run_anthropic_credential_worker, args=(flow_id,), daemon=True)
    worker.start()
    assert link_started.wait(timeout=5)
    assert oauth.poll_onboarding_oauth_flow(flow_id)["status"] == "committing"
    assert oauth.cancel_onboarding_oauth_flow({"flow_id": flow_id})["status"] == "committing"
    link_continue.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert oauth._OAUTH_FLOWS[flow_id]["status"] == "success"
    assert _provider_oauth_authenticated("anthropic", tmp_path) is True
    assert "ANTHROPIC_API_KEY" not in env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_anthropic_cancel_missing_flow_keeps_requested_provider():
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()

    assert oauth.cancel_onboarding_oauth_flow({"flow_id": "missing", "provider": "claude-code"}) == {
        "ok": True,
        "provider": "anthropic",
        "flow_id": "missing",
        "status": "cancelled",
    }


def test_anthropic_worker_expires_flow(tmp_path):
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    flow_id = "claude-expired-worker-flow"
    oauth._OAUTH_FLOWS[flow_id] = {
        "provider": "anthropic",
        "status": "pending",
        "expires_at": time.time() - 1,
        "poll_interval_seconds": 1,
        "hermes_home": str(tmp_path),
        "config_path": str(tmp_path / "config.yaml"),
        "created_at": time.time(),
        "updated_at": time.time(),
    }

    oauth._run_anthropic_credential_worker(flow_id)

    assert oauth._OAUTH_FLOWS[flow_id]["status"] == "expired"


def test_anthropic_worker_reports_link_errors(monkeypatch, tmp_path):
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    monkeypatch.setattr(oauth.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(oauth, "_read_claude_code_credentials", lambda: {"accessToken": "cc-access-secret", "refreshToken": "cc-refresh-secret"})

    def _raise_link_error(_home):
        raise RuntimeError("link failed without secrets")

    monkeypatch.setattr(oauth, "_link_anthropic_credentials", _raise_link_error)
    flow_id = "claude-link-error-flow"
    oauth._OAUTH_FLOWS[flow_id] = {
        "provider": "anthropic",
        "status": "pending",
        "expires_at": time.time() + 60,
        "poll_interval_seconds": 1,
        "hermes_home": str(tmp_path),
        "config_path": str(tmp_path / "config.yaml"),
        "created_at": time.time(),
        "updated_at": time.time(),
    }

    oauth._run_anthropic_credential_worker(flow_id)

    assert oauth._OAUTH_FLOWS[flow_id]["status"] == "error"
    assert "link failed" in oauth._OAUTH_FLOWS[flow_id]["error"]
    payload = oauth.poll_onboarding_oauth_flow(flow_id)
    assert payload == {
        "ok": True,
        "provider": "anthropic",
        "flow_id": flow_id,
        "status": "error",
        "error": "Claude Code 凭据关联失败，请查看服务器日志。",
    }


def test_anthropic_link_clears_env_and_writes_secret_free_marker(monkeypatch, tmp_path):
    import api.oauth as oauth
    from api.onboarding import _provider_oauth_authenticated

    env_path = tmp_path / ".env"
    env_path.write_text("ANTHROPIC_TOKEN=old-token\nANTHROPIC_API_KEY=old-key\nOTHER=value\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_TOKEN", "old-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "old-key")

    oauth._link_anthropic_credentials(tmp_path / "config.yaml")

    env_text = env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_TOKEN" not in env_text
    assert "ANTHROPIC_API_KEY" not in env_text
    assert "OTHER=value" in env_text
    assert "ANTHROPIC_TOKEN" not in os.environ
    assert "ANTHROPIC_API_KEY" not in os.environ
    auth = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    marker = auth["credential_pool"]["anthropic"][0]
    assert marker["auth_type"] == "oauth"
    assert marker["source"] == "claude_code_linked"
    assert "access_token" not in marker
    assert "refresh_token" not in marker
    assert _provider_oauth_authenticated("anthropic", tmp_path) is True
    assert _provider_oauth_authenticated("claude-code", tmp_path) is True


def test_anthropic_env_clear_failure_propagates_without_partial_commit(
    monkeypatch,
    tmp_path,
):
    import api.oauth as oauth
    import api.providers as providers

    env_path = tmp_path / ".env"
    original_env = b"ANTHROPIC_TOKEN=old-token\nANTHROPIC_API_KEY=old-key\n"
    env_path.write_bytes(original_env)
    monkeypatch.setenv("ANTHROPIC_TOKEN", "old-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "old-key")

    def _fail_before_env_lock(_env_path, _updates, *, config_path):
        assert config_path == tmp_path / "config.yaml"
        raise RuntimeError("env write failed before process-env clear")

    monkeypatch.setattr(providers, "_write_env_file", _fail_before_env_lock)

    with pytest.raises(RuntimeError, match="env write failed"):
        oauth._link_anthropic_credentials(tmp_path / "config.yaml")

    assert env_path.read_bytes() == original_env
    assert os.environ["ANTHROPIC_TOKEN"] == "old-token"
    assert os.environ["ANTHROPIC_API_KEY"] == "old-key"
    auth = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    entries = auth.get("credential_pool", {}).get("anthropic", [])
    assert all(
        entry.get("source")
        not in {"claude_code_link_pending", "claude_code_linked"}
        for entry in entries
        if isinstance(entry, dict)
    )


def test_anthropic_marker_commit_failure_restores_env_and_removes_pending_marker(
    monkeypatch,
    tmp_path,
):
    import api.oauth as oauth

    env_path = tmp_path / ".env"
    env_path.write_text(
        "ANTHROPIC_TOKEN=old-token\n"
        "ANTHROPIC_API_KEY=old-key\n"
        "KEEP=value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_TOKEN", "old-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "old-key")

    def fail_marker_commit(*_args, **_kwargs):
        raise RuntimeError("auth marker commit failed")

    monkeypatch.setattr(
        oauth,
        "_commit_anthropic_link_marker",
        fail_marker_commit,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="auth marker commit failed"):
        oauth._link_anthropic_credentials(tmp_path / "config.yaml")

    env_text = env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_TOKEN=old-token" in env_text
    assert "ANTHROPIC_API_KEY=old-key" in env_text
    assert "KEEP=value" in env_text
    assert "TAIJI_ANTHROPIC_LINK_BACKUP_" not in env_text
    assert os.environ["ANTHROPIC_TOKEN"] == "old-token"
    assert os.environ["ANTHROPIC_API_KEY"] == "old-key"
    auth = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    entries = auth.get("credential_pool", {}).get("anthropic", [])
    assert all(
        entry.get("source")
        not in {"claude_code_link_pending", "claude_code_linked"}
        for entry in entries
        if isinstance(entry, dict)
    )


@pytest.mark.skipif(os.name != "posix", reason="hard-crash recovery is POSIX-only")
def test_anthropic_link_recovers_hard_crash_after_env_stage(
    monkeypatch,
    tmp_path,
):
    import api.oauth as oauth

    env_path = tmp_path / ".env"
    env_path.write_text(
        "ANTHROPIC_TOKEN=old-token\n"
        "ANTHROPIC_API_KEY=old-key\n"
        "KEEP=value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_TOKEN", "old-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "old-key")
    script = """
import os
import sys
from pathlib import Path

import api.oauth as oauth

oauth._commit_anthropic_link_marker = lambda *_args, **_kwargs: os._exit(92)
oauth._link_anthropic_credentials(Path(sys.argv[1]))
"""

    crashed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "config.yaml")],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )

    assert crashed.returncode == 92, (crashed.stdout, crashed.stderr)
    staged_env = env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_TOKEN=old-token" not in staged_env
    assert "ANTHROPIC_API_KEY=old-key" not in staged_env
    assert "TAIJI_ANTHROPIC_LINK_BACKUP_" in staged_env
    staged_auth = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert "claude_code_link_pending" in staged_auth
    assert "old-token" not in staged_auth
    assert "old-key" not in staged_auth

    # A normal retry must recover the pending intent first, then finish the
    # link and remove every backup projection.
    oauth._link_anthropic_credentials(tmp_path / "config.yaml")

    final_env = env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_TOKEN" not in final_env
    assert "ANTHROPIC_API_KEY" not in final_env
    assert "TAIJI_ANTHROPIC_LINK_BACKUP_" not in final_env
    assert "KEEP=value" in final_env
    assert "ANTHROPIC_TOKEN" not in os.environ
    assert "ANTHROPIC_API_KEY" not in os.environ
    final_auth = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    entries = final_auth["credential_pool"]["anthropic"]
    assert any(entry.get("source") == "claude_code_linked" for entry in entries)
    assert all(
        entry.get("source") != "claude_code_link_pending"
        for entry in entries
        if isinstance(entry, dict)
    )


@pytest.mark.skipif(os.name != "posix", reason="hard-crash recovery is POSIX-only")
def test_anthropic_link_recovers_crash_before_env_stage(
    monkeypatch,
    tmp_path,
):
    import api.oauth as oauth

    env_path = tmp_path / ".env"
    env_path.write_text(
        "ANTHROPIC_TOKEN=old-token\n"
        "ANTHROPIC_API_KEY=old-key\n"
        "KEEP=value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_TOKEN", "old-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "old-key")
    script = """
import os
import sys
from pathlib import Path

import api.oauth as oauth

oauth._stage_anthropic_env_clear = lambda *_args, **_kwargs: os._exit(91)
oauth._link_anthropic_credentials(Path(sys.argv[1]))
"""

    crashed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "config.yaml")],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )

    assert crashed.returncode == 91, (crashed.stdout, crashed.stderr)
    untouched_env = env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_TOKEN=old-token" in untouched_env
    assert "ANTHROPIC_API_KEY=old-key" in untouched_env
    assert "TAIJI_ANTHROPIC_LINK_BACKUP_" not in untouched_env
    assert "claude_code_link_pending" in (
        tmp_path / "auth.json"
    ).read_text(encoding="utf-8")

    oauth._link_anthropic_credentials(tmp_path / "config.yaml")

    final_env = env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_TOKEN" not in final_env
    assert "ANTHROPIC_API_KEY" not in final_env
    assert "TAIJI_ANTHROPIC_LINK_BACKUP_" not in final_env
    final_auth = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert "claude_code_link_pending" not in final_auth
    assert "claude_code_linked" in final_auth


@pytest.mark.skipif(os.name != "posix", reason="hard-crash recovery is POSIX-only")
def test_anthropic_link_recovers_crash_after_marker_commit_before_cleanup(
    monkeypatch,
    tmp_path,
):
    import api.oauth as oauth

    env_path = tmp_path / ".env"
    env_path.write_text(
        "ANTHROPIC_TOKEN=old-token\n"
        "ANTHROPIC_API_KEY=old-key\n"
        "KEEP=value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_TOKEN", "old-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "old-key")
    script = """
import os
import sys
from pathlib import Path

import api.oauth as oauth

oauth._cleanup_anthropic_env_backup = lambda *_args, **_kwargs: os._exit(93)
oauth._link_anthropic_credentials(Path(sys.argv[1]))
"""

    crashed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "config.yaml")],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )

    assert crashed.returncode == 93, (crashed.stdout, crashed.stderr)
    staged_env = env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_TOKEN=old-token" not in staged_env
    assert "ANTHROPIC_API_KEY=old-key" not in staged_env
    assert "TAIJI_ANTHROPIC_LINK_BACKUP_" in staged_env
    staged_auth = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert "claude_code_linked" in staged_auth
    assert "claude_code_link_pending" not in staged_auth

    oauth._link_anthropic_credentials(tmp_path / "config.yaml")

    final_env = env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_TOKEN" not in final_env
    assert "ANTHROPIC_API_KEY" not in final_env
    assert "TAIJI_ANTHROPIC_LINK_BACKUP_" not in final_env
    assert "KEEP=value" in final_env
    final_auth = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert "claude_code_link_pending" not in final_auth
    assert final_auth.count('"source": "claude_code_linked"') == 1


def test_runtime_provider_reads_use_anthropic_env_lock():
    streaming_src = (REPO / "api" / "streaming.py").read_text(encoding="utf-8")
    routes_src = (REPO / "api" / "routes.py").read_text(encoding="utf-8")

    assert "resolve_runtime_provider_with_anthropic_env_lock" in streaming_src
    assert "resolve_runtime_provider_with_anthropic_env_lock" in routes_src


def test_runtime_provider_lock_order_is_credential_then_process_env(monkeypatch):
    import agent.provider_credentials as provider_credentials
    import api.oauth as oauth
    import api.streaming as streaming

    events = []

    @contextmanager
    def tracked_transaction(_config_path):
        events.append("credential-enter")
        try:
            yield
        finally:
            events.append("credential-exit")

    class TrackedEnvLock:
        def __enter__(self):
            events.append("env-enter")

        def __exit__(self, _exc_type, _exc, _tb):
            events.append("env-exit")

    monkeypatch.setattr(
        provider_credentials,
        "credential_transaction",
        tracked_transaction,
    )
    monkeypatch.setattr(streaming, "_ENV_LOCK", TrackedEnvLock())

    result = oauth.resolve_runtime_provider_with_anthropic_env_lock(
        lambda: events.append("resolver") or {"ok": True}
    )

    assert result == {"ok": True}
    assert events == [
        "credential-enter",
        "env-enter",
        "resolver",
        "env-exit",
        "credential-exit",
    ]


def test_anthropic_onboarding_setup_allows_linked_oauth_without_api_key(monkeypatch, tmp_path):
    import api.config as webui_config
    import api.onboarding as onboarding

    # apply_onboarding_setup() short-circuits when HERMES_WEBUI_SKIP_ONBOARDING
    # is set in the environment (hosting providers like Agent37 use it to ship
    # a pre-configured WebUI). Local test runs may also set it for the same
    # reason. The test exercises the file-writing branch, so delete the var
    # for the test's scope. monkeypatch.delenv is a no-op if the var is unset.
    monkeypatch.delenv("HERMES_WEBUI_SKIP_ONBOARDING", raising=False)

    home = tmp_path / "home"
    home.mkdir()
    cfg_path = home / "config.yaml"
    (home / "auth.json").write_text(json.dumps({
        "credential_pool": {"anthropic": [{"auth_type": "oauth", "source": "claude_code_linked"}]}
    }), encoding="utf-8")
    monkeypatch.setattr(onboarding, "_get_config_path", lambda: cfg_path)
    monkeypatch.setattr(webui_config, "_get_config_path", lambda: cfg_path)
    monkeypatch.setattr(onboarding, "get_onboarding_status", lambda: {"ok": True})
    monkeypatch.setattr(onboarding, "reload_config", lambda: None)

    result = onboarding.apply_onboarding_setup({"provider": "anthropic", "model": "claude-sonnet-4.6"})

    assert result == {"ok": True}
    saved = cfg_path.read_text(encoding="utf-8")
    assert "provider: anthropic" in saved
    assert "default: claude-sonnet-4.6" in saved


def test_auth_json_thread_race_preserves_codex_and_anthropic_entries(
    monkeypatch,
    tmp_path,
):
    import api.oauth as oauth

    original_mutate = oauth._mutate_auth_json
    read_barrier = threading.Barrier(2)
    local = threading.local()

    def synchronized_mutate(auth_path, mutator):
        if not getattr(local, "synchronized", False):
            local.synchronized = True
            read_barrier.wait(timeout=5)
        return original_mutate(auth_path, mutator)

    monkeypatch.setattr(oauth, "_mutate_auth_json", synchronized_mutate)
    monkeypatch.setattr(oauth, "_clear_anthropic_env_values", lambda _path: None)
    errors = []

    def write_codex():
        try:
            oauth._persist_codex_credentials(
                tmp_path,
                {"access_token": "codex-access", "refresh_token": "codex-refresh"},
            )
        except Exception as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    def write_anthropic():
        try:
            oauth._link_anthropic_credentials(tmp_path / "config.yaml")
        except Exception as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    workers = [
        threading.Thread(target=write_codex),
        threading.Thread(target=write_anthropic),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    store = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    assert set(store["credential_pool"]) >= {"openai-codex", "anthropic"}


@pytest.mark.skipif(os.name != "posix", reason="cross-process auth locking is POSIX-only")
def test_auth_json_process_race_preserves_codex_and_anthropic_entries(tmp_path):
    worker_script = """
import sys
import time
from pathlib import Path

import api.oauth as oauth

kind = sys.argv[1]
home = Path(sys.argv[2])
original_mutate = oauth._mutate_auth_json
first_mutation = True

def synchronized_mutate(auth_path, mutator):
    global first_mutation
    if first_mutation:
        first_mutation = False
        (home / f"{kind}.ready").write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 10
        while not all((home / f"{name}.ready").exists() for name in ("codex", "anthropic")):
            if time.monotonic() >= deadline:
                raise RuntimeError("timed out waiting for auth mutation barrier")
            time.sleep(0.01)
    return original_mutate(auth_path, mutator)

oauth._mutate_auth_json = synchronized_mutate
oauth._clear_anthropic_env_values = lambda _config_path: None
if kind == "codex":
    oauth._persist_codex_credentials(
        home,
        {"access_token": "codex-access", "refresh_token": "codex-refresh"},
    )
else:
    oauth._link_anthropic_credentials(home / "config.yaml")
"""
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", worker_script, kind, str(tmp_path)],
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for kind in ("codex", "anthropic")
    ]

    results = [worker.communicate(timeout=15) for worker in workers]
    assert [worker.returncode for worker in workers] == [0, 0], results
    store = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    assert set(store["credential_pool"]) >= {"openai-codex", "anthropic"}


def test_auth_json_symlink_target_is_rejected_without_touching_victim(
    monkeypatch,
    tmp_path,
):
    import api.oauth as oauth

    victim = tmp_path / "victim.json"
    original = b'{"do_not_touch": true}\n'
    victim.write_bytes(original)
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").symlink_to(victim)

    with pytest.raises(OSError):
        oauth._persist_codex_credentials(
            home,
            {"access_token": "codex-access", "refresh_token": "codex-refresh"},
        )

    assert victim.read_bytes() == original
    assert (home / "auth.json").is_symlink()


def test_auth_json_parent_replacement_fails_closed(tmp_path):
    import api.oauth as oauth

    home = tmp_path / "home"
    home.mkdir()
    auth_path = home / "auth.json"
    moved = tmp_path / "moved-home"

    def replace_parent(auth):
        home.rename(moved)
        home.mkdir()
        auth.setdefault("credential_pool", {})["anthropic"] = []

    with pytest.raises(OSError, match="directory"):
        oauth._mutate_auth_json(auth_path, replace_parent)

    assert not (home / "auth.json").exists()
    assert not (moved / "auth.json").exists()


def test_anthropic_cancel_ui_keeps_polling_when_server_owns_commit():
    result = _run_anthropic_oauth_js("committing")

    assert result["during"]["flowId"] == "flow-1"
    assert result["during"]["disabled"] is True
    assert result["after"]["flowId"] == "flow-1"
    assert result["after"]["disabled"] is True
    assert result["after"]["scheduled"] >= 1
    assert "正在完成" in result["after"]["html"]


def test_anthropic_cancel_ui_recovers_from_cancel_transport_error():
    result = _run_anthropic_oauth_js("error")

    assert result["during"]["flowId"] == "flow-1"
    assert result["after"]["flowId"] == "flow-1"
    assert result["after"]["disabled"] is True
    assert result["after"]["scheduled"] >= 1
    assert "cancel transport failed" in result["after"]["html"]


def test_anthropic_oauth_visible_copy_stays_simplified_chinese():
    js = (REPO / "static" / "onboarding.js").read_text(encoding="utf-8")
    start = js.index("/* ── Anthropic / Claude Code credential-link flow ── */")
    flow_source = js[start:]

    for expected in (
        "使用 Claude Code 登录",
        "正在完成 Claude Code OAuth 关联",
        "正在等待 Claude Code 凭据",
        "取消",
        "Claude Code OAuth 已关联",
        "Claude Code 轮询已过期",
        "Claude Code OAuth 已取消",
        "Claude Code OAuth 失败",
        "正在检查 Claude Code 凭据",
    ):
        assert expected in flow_source

    for forbidden in (
        "Login with Claude Code",
        "Finishing Claude Code OAuth link",
        "Waiting for Claude Code credentials",
        ">Cancel</button>",
        "Claude Code OAuth linked",
        "Claude Code polling expired",
        "Claude Code OAuth cancelled",
        "Claude Code OAuth failed",
        "Checking Claude Code credentials",
    ):
        assert forbidden not in flow_source


def test_frontend_has_anthropic_oauth_support():
    js = (REPO / "static" / "onboarding.js").read_text(encoding="utf-8")
    assert "startAnthropicOAuth" in js
    assert "cancelAnthropicOAuth" in js
    assert "anthropicOAuthBtn" in js
    assert "使用 Claude Code 登录" in js
    assert "Anthropic API 密钥路径" in js
    assert "Claude Code 订阅凭据" in js
    assert "不同于 Anthropic API 密钥" in js
    assert "/api/onboarding/oauth/start" in js
    assert "/api/onboarding/oauth/poll" in js
    assert "/api/onboarding/oauth/cancel" in js
    assert "window.open(" not in js[js.find("startAnthropicOAuth"):]
    assert "accessToken" not in js[js.find("startAnthropicOAuth"):]
    assert "refreshToken" not in js[js.find("startAnthropicOAuth"):]


def test_onboarding_oauth_status_reuses_bounded_safe_auth_reader(
    monkeypatch,
    tmp_path,
):
    import api.oauth as oauth
    import api.onboarding as onboarding

    calls = []

    def safe_reader(path):
        calls.append(Path(path))
        return {
            "credential_pool": {
                "anthropic": [
                    {
                        "auth_type": "oauth",
                        "source": "claude_code_linked",
                    }
                ]
            }
        }

    monkeypatch.setattr(oauth, "_read_auth_json", safe_reader)

    assert onboarding._provider_oauth_authenticated(
        "anthropic",
        tmp_path,
    ) is True
    assert calls == [tmp_path / "auth.json"]


def test_safe_auth_reader_rejects_oversized_json_before_parsing(
    monkeypatch,
    tmp_path,
):
    import api.oauth as oauth

    auth_path = tmp_path / "auth.json"
    payload = {
        "credential_pool": {
            "openai-codex": [
                {
                    "access_token": "secret",
                    "padding": "x" * (1024 * 1024 + 256),
                }
            ]
        }
    }
    auth_path.write_text(json.dumps(payload), encoding="utf-8")
    parse_calls = []
    original_loads = oauth.json.loads

    def tracked_loads(raw):
        parse_calls.append(len(raw))
        return original_loads(raw)

    monkeypatch.setattr(oauth.json, "loads", tracked_loads)

    assert oauth._read_auth_json(auth_path) == {}
    assert parse_calls == []


def test_anthropic_link_does_not_restore_secret_after_auth_replace_fsync_failure(
    monkeypatch,
    tmp_path,
):
    import api.oauth as oauth

    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    auth_path = tmp_path / "auth.json"
    env_path.write_text(
        "ANTHROPIC_TOKEN=old-token\n"
        "ANTHROPIC_API_KEY=old-key\n"
        "KEEP=value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_TOKEN", "old-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "old-key")
    original_fsync = oauth.os.fsync
    injected = False

    def fail_linked_marker_directory_sync(fd):
        nonlocal injected
        mode = oauth.os.fstat(fd).st_mode
        linked_marker_visible = (
            auth_path.exists()
            and "claude_code_linked" in auth_path.read_text(encoding="utf-8")
        )
        if not injected and stat.S_ISDIR(mode) and linked_marker_visible:
            injected = True
            raise OSError("injected auth directory fsync failure")
        return original_fsync(fd)

    monkeypatch.setattr(oauth.os, "fsync", fail_linked_marker_directory_sync)

    with pytest.raises(
        OSError,
        match="injected auth directory fsync failure",
    ):
        oauth._link_anthropic_credentials(config_path)

    assert injected is True
    uncertain_auth = auth_path.read_text(encoding="utf-8")
    uncertain_env = env_path.read_text(encoding="utf-8")
    assert "claude_code_linked" in uncertain_auth
    assert "ANTHROPIC_TOKEN=old-token" not in uncertain_env
    assert "ANTHROPIC_API_KEY=old-key" not in uncertain_env
    assert "TAIJI_ANTHROPIC_LINK_BACKUP_" in uncertain_env

    oauth._link_anthropic_credentials(config_path)

    final_auth = auth_path.read_text(encoding="utf-8")
    final_env = env_path.read_text(encoding="utf-8")
    assert final_auth.count('"source": "claude_code_linked"') == 1
    assert "ANTHROPIC_TOKEN" not in final_env
    assert "ANTHROPIC_API_KEY" not in final_env
    assert "TAIJI_ANTHROPIC_LINK_BACKUP_" not in final_env
    assert "KEEP=value" in final_env


def test_active_oauth_config_resolution_fails_closed_without_home_fallback(
    monkeypatch,
):
    import api.config as config
    import api.oauth as oauth

    private_path = "/Users/private/profile/config.yaml"

    def fail_config_resolution():
        raise ValueError(f"invalid active config at {private_path}")

    def forbidden_fallback():
        raise AssertionError("OAuth config resolution must not fall back")

    monkeypatch.setattr(config, "_get_config_path", fail_config_resolution)
    monkeypatch.setattr(oauth, "_get_active_hermes_home", forbidden_fallback)

    with pytest.raises(
        RuntimeError,
        match="Active OAuth configuration is unavailable",
    ) as error:
        oauth._get_active_config_path()

    assert private_path not in str(error.value)


def test_pending_anthropic_flow_pins_physical_config_before_symlink_retarget(
    monkeypatch,
    tmp_path,
):
    import api.config as config
    import api.oauth as oauth

    oauth._OAUTH_FLOWS.clear()
    original_home = tmp_path / "original"
    replacement_home = tmp_path / "replacement"
    original_home.mkdir()
    replacement_home.mkdir()
    original_config = original_home / "config.yaml"
    replacement_config = replacement_home / "config.yaml"
    original_config.write_text("{}\n", encoding="utf-8")
    replacement_config.write_text("{}\n", encoding="utf-8")
    active_config = tmp_path / "active-config.yaml"
    active_config.symlink_to(original_config)

    credential_reads = iter(
        [
            None,
            {
                "accessToken": "cc-access-secret",
                "refreshToken": "cc-refresh-secret",
            },
        ]
    )
    linked_paths: list[Path] = []
    monkeypatch.setattr(config, "_get_config_path", lambda: active_config)
    monkeypatch.setattr(
        oauth,
        "_read_claude_code_credentials",
        lambda: next(credential_reads),
    )
    monkeypatch.setattr(
        oauth,
        "_spawn_anthropic_credential_worker",
        lambda _flow_id: None,
    )
    monkeypatch.setattr(oauth.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        oauth,
        "_link_anthropic_credentials",
        lambda path: linked_paths.append(Path(path)),
    )

    started = oauth.start_onboarding_oauth_flow(
        {"provider": "anthropic"}
    )
    active_config.unlink()
    active_config.symlink_to(replacement_config)
    oauth._run_anthropic_credential_worker(started["flow_id"])

    assert linked_paths == [original_config]
    assert replacement_config not in linked_paths
    assert oauth._OAUTH_FLOWS[started["flow_id"]]["status"] == "success"
    public_payloads = (
        started,
        oauth.poll_onboarding_oauth_flow(started["flow_id"]),
    )
    serialized = json.dumps(public_payloads)
    for private_path in (
        active_config,
        original_config,
        replacement_config,
    ):
        assert str(private_path) not in serialized


def test_codex_public_error_payload_redacts_internal_paths():
    import api.oauth as oauth

    private_path = "/Users/private/profile/config.yaml"
    payload = oauth._codex_public_status_payload(
        "flow-redaction",
        {
            "provider": "openai-codex",
            "status": "error",
            "error": f"failed to persist token at {private_path}",
        },
    )

    assert payload["error"] == oauth.CODEX_PUBLIC_OAUTH_ERROR
    assert private_path not in json.dumps(payload)


def test_codex_start_error_redacts_internal_paths(monkeypatch, tmp_path):
    import api.oauth as oauth

    private_path = "/Users/private/profile/config.yaml"
    monkeypatch.setattr(
        oauth,
        "_get_active_config_path",
        lambda: tmp_path / "config.yaml",
    )
    monkeypatch.setattr(
        oauth,
        "_request_codex_user_code",
        lambda: (_ for _ in ()).throw(
            RuntimeError(f"failed to read {private_path}")
        ),
    )

    with pytest.raises(RuntimeError) as error:
        oauth.start_onboarding_oauth_flow(
            {"provider": "openai-codex"}
        )

    assert str(error.value) == oauth.CODEX_PUBLIC_OAUTH_ERROR
    assert private_path not in str(error.value)


def test_anthropic_oauth_control_is_reachable_from_current_oauth_setup():
    js = (REPO / "static" / "onboarding.js").read_text(encoding="utf-8")
    setup_start = js.index("if(key==='setup')")
    setup_end = js.index("if(key==='workspace')", setup_start)
    setup_source = js[setup_start:setup_end]
    oauth_start = js.index("function _renderOnboardingProviderOAuthField")
    oauth_end = js.index("\n\nfunction _providerStatusLabel", oauth_start)
    oauth_source = js[oauth_start:oauth_end]

    assert "const selectedProvider=_getOnboardingSetupProvider(selectedId)" in setup_source
    assert "if(currentIsOauth&&!selectedProvider)" in setup_source
    assert "${_renderOnboardingProviderOAuthField(provider)}" in setup_source
    assert '<button class="sm-btn" id="anthropicOAuthBtn"' in oauth_source
    assert 'type="button"' in oauth_source
    assert 'aria-controls="anthropicOAuthFlow"' in oauth_source
    assert 'role="status"' in oauth_source
    assert 'aria-live="polite"' in oauth_source
    assert 'aria-atomic="true"' in oauth_source


def test_provider_rerender_preserves_keyboard_focus():
    result = _run_onboarding_provider_focus_js()

    assert result == {
        "focused": "provider-select",
        "renders": 1,
        "provider": "anthropic",
    }
