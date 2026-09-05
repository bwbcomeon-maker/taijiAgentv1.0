"""Focused contracts for durable Taiji Zhinang session roles."""

from __future__ import annotations

import copy
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
import urllib.error
from urllib.parse import urlparse
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from api.brand_privacy import public_session_projection
from api.models import Session
from api.zhinang import (
    SessionRoleSnapshotError,
    apply_session_role_to_agent,
    clone_session_role_state,
    make_session_role_snapshot,
    public_session_role_projection,
    record_session_role_acceptance,
    snapshot_role_from_catalog,
    validated_session_role_prompt,
)


ALPHA = "TAIJI_ZHINANG_SENTINEL_ALPHA_8F1C"
BETA = "TAIJI_ZHINANG_SENTINEL_BETA_4D72"


@pytest.fixture(autouse=True)
def _isolate_mock_provider_tests_from_installed_license(monkeypatch):
    """Provider contract tests must not consume the host's installed license."""
    import run_agent

    monkeypatch.setattr(run_agent.taiji_license, "require_valid_license", lambda: None)


def _snapshot(role_id: str, sentinel: str) -> dict:
    prompt = f"Act as the selected role. {sentinel}"
    return make_session_role_snapshot(
        role_id=role_id,
        catalog_version="test-catalog-v1",
        upstream_commit="a" * 40,
        source_path=f"testing/{role_id}.md",
        source_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        effective_prompt=prompt,
        display={
            "name": f"测试角色 {role_id}",
            "original_name": role_id,
            "summary": "仅用于逐轮注入契约测试。",
            "category": "产品与研发",
            "tags": ["测试"],
            "capabilities": ["验证系统上下文"],
            "limitations": "不授予工具或外部权限。",
            "deliverable_examples": [],
            "starter_examples": [],
            "raw_source": f"# {role_id}\n\n{sentinel}\n",
            "adaptation_note": "测试适配。",
            "license": "MIT",
        },
        created_at=1234.5,
    )


def test_session_role_snapshot_survives_save_load_and_public_projection(tmp_path, monkeypatch):
    import api.models as models

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", tmp_path / "_index.json")
    session = Session(
        session_id="zhinang-test-a",
        workspace=str(tmp_path),
        zhinang_role_snapshot=_snapshot("alpha", ALPHA),
        zhinang_create_request_id="request-a",
        zhinang_create_fingerprint="f" * 64,
    )
    session.composer_draft = {"text": "请分析这个问题", "files": []}
    session.save()

    loaded = Session.load(session.session_id)
    assert loaded is not None
    assert validated_session_role_prompt(loaded.zhinang_role_snapshot).endswith(ALPHA)
    assert loaded.zhinang_create_request_id == "request-a"
    assert loaded.composer_draft["text"] == "请分析这个问题"

    compact = loaded.compact()
    assert compact["zhinang_role"]["role_id"] == "alpha"
    projected = public_session_projection(compact)
    assert projected["zhinang_role"]["name"] == "测试角色 alpha"
    assert projected["zhinang_role"]["adapter_version"] == "taiji-zhinang-runtime-v3"
    assert ALPHA not in repr(projected)
    assert "effective_prompt" not in projected["zhinang_role"]
    assert "private" not in projected["zhinang_role"]
    assert "raw_source" not in projected["zhinang_role"]


def test_metadata_only_role_is_not_treated_as_missing_or_safe_to_mutate(tmp_path, monkeypatch):
    import api.models as models

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", tmp_path / "_index.json")
    session = Session(
        session_id="zhinang-metadata",
        workspace=str(tmp_path),
        messages=[{"role": "user", "content": "keep me"}],
        zhinang_role_snapshot=_snapshot("alpha", ALPHA),
    )
    session.save()

    stub = Session.load_metadata_only(session.session_id)
    assert stub is not None
    assert public_session_role_projection(stub.zhinang_role_snapshot)["role_id"] == "alpha"
    with pytest.raises(RuntimeError, match="metadata-only"):
        stub.save()


def test_corrupt_role_snapshot_fails_closed_instead_of_becoming_plain_chat():
    damaged = _snapshot("alpha", ALPHA)
    damaged["private"]["effective_prompt"] = f"tampered {BETA}"

    with pytest.raises(SessionRoleSnapshotError, match="智囊角色快照已损坏"):
        validated_session_role_prompt(damaged)


@pytest.mark.parametrize("snapshot_value", [[], "damaged", None])
def test_non_object_or_missing_bound_role_snapshot_stays_invalid(snapshot_value):
    session = Session(
        session_id="bound-role-invalid-shape",
        title="Bound role with invalid snapshot",
        messages=[{"role": "user", "content": "keep visible"}],
        zhinang_role_snapshot=snapshot_value,
        zhinang_create_request_id="bound-request",
        zhinang_create_fingerprint="f" * 64,
    )

    with pytest.raises(SessionRoleSnapshotError, match="智囊角色快照已损坏"):
        session.compact()

    compact = session.compact(sidebar_safe=True)
    marker = compact["zhinang_role"]
    assert marker == {
        "status": "invalid",
        "code": "zhinang_snapshot_invalid",
        "name": "智囊角色快照已损坏",
    }
    assert not ({"role_id", "source_path", "original_name"} & marker.keys())
    assert ALPHA not in repr(compact)


@pytest.mark.parametrize("list_path", ["stale-index", "missing-index", "memory-overlay", "full-scan"])
def test_corrupt_role_isolated_from_every_sidebar_projection_path(
    tmp_path, monkeypatch, list_path
):
    from api import models

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", tmp_path / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setattr(models, "_start_session_index_rebuild_thread", lambda: None)
    monkeypatch.setattr(models, "_active_stream_ids", lambda: set())
    monkeypatch.setattr(models, "_enrich_sidebar_lineage_metadata", lambda _rows: None)

    ordinary = Session(
        session_id=f"ordinary-{list_path}",
        title="Ordinary task",
        messages=[{"role": "user", "content": "ordinary"}],
    )
    ordinary.save(skip_index=True)
    damaged = Session(
        session_id=f"damaged-{list_path}",
        title="Damaged role task",
        messages=[{"role": "user", "content": "role task"}],
        zhinang_role_snapshot=_snapshot("alpha", ALPHA),
        zhinang_create_request_id="bound-request",
        zhinang_create_fingerprint="a" * 64,
    )
    damaged.save(skip_index=True)
    stale_row = damaged.compact()
    payload = json.loads(damaged.path.read_text(encoding="utf-8"))
    payload["zhinang_role_snapshot"]["identity"]["role_id"] = "forged-role"
    damaged.path.write_text(json.dumps(payload), encoding="utf-8")

    if list_path == "stale-index":
        models.SESSION_INDEX_FILE.write_text(
            json.dumps([ordinary.compact(), stale_row]), encoding="utf-8"
        )
    elif list_path == "missing-index":
        models.SESSION_INDEX_FILE.write_text(json.dumps([ordinary.compact()]), encoding="utf-8")
    elif list_path == "memory-overlay":
        damaged.zhinang_role_snapshot["identity"]["role_id"] = "forged-role"
        models.SESSIONS[damaged.session_id] = damaged
        models.SESSION_INDEX_FILE.write_text(json.dumps([ordinary.compact()]), encoding="utf-8")
    else:
        models.SESSION_INDEX_FILE.write_text("not json", encoding="utf-8")

    rows = {row["session_id"]: row for row in models.all_sessions()}
    assert ordinary.session_id in rows
    marker = rows[damaged.session_id]["zhinang_role"]
    assert marker["status"] == "invalid"
    assert marker["code"] == "zhinang_snapshot_invalid"
    assert not ({"role_id", "source_path", "original_name"} & marker.keys())
    assert "forged-role" not in repr(rows[damaged.session_id])
    assert ALPHA not in repr(rows[damaged.session_id])
    public_marker = public_session_projection(rows[damaged.session_id])["zhinang_role"]
    assert public_marker == marker


def test_legacy_plain_session_without_role_binding_remains_plain():
    session = Session(session_id="legacy-plain", title="Ordinary task")
    assert "zhinang_role" not in session.compact()
    assert "zhinang_role" not in session.compact(sidebar_safe=True)


def test_generic_session_detail_returns_explicit_conflict_for_corrupt_role(
    monkeypatch
):
    from api import routes

    damaged = _snapshot("alpha", ALPHA)
    damaged["identity"]["role_id"] = "forged-role"
    session = Session(
        session_id="generic-detail-corrupt-role",
        title="Damaged role task",
        zhinang_role_snapshot=damaged,
        zhinang_create_request_id="bound-request",
        zhinang_create_fingerprint="a" * 64,
    )
    monkeypatch.setattr(routes, "get_session", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(routes, "_expert_team_launch_session_is_public", lambda *_args: True)
    monkeypatch.setattr(routes, "_clear_stale_stream_state", lambda _session: None)
    monkeypatch.setattr(routes, "_session_requires_cli_metadata_lookup", lambda _session: False)
    monkeypatch.setattr(
        routes,
        "_metadata_only_message_summary",
        lambda *_args, **_kwargs: {"message_count": 0, "last_message_at": 0},
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: {"status": status, **payload},
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400, **_kwargs: {
            "status": status,
            "error": message,
        },
    )

    result = routes.handle_get(
        object(),
        urlparse("/api/session?session_id=generic-detail-corrupt-role&messages=0&resolve_model=0"),
    )

    assert result["status"] == 409
    assert result["code"] == "zhinang_snapshot_invalid"
    assert "快照已损坏" in result["error"]


def test_missing_bound_snapshot_role_detail_and_personality_fail_closed(monkeypatch):
    from api import routes

    session = Session(
        session_id="missing-bound-role",
        profile="default",
        zhinang_role_snapshot=None,
        zhinang_create_request_id="bound-request",
        zhinang_create_fingerprint="b" * 64,
    )
    monkeypatch.setattr(routes, "get_session", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(routes, "_ensure_full_session_before_mutation", lambda _sid, value: value)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {
        "session_id": session.session_id,
        "name": "",
    })
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: {"status": status, **payload},
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400, **_kwargs: {
            "status": status,
            "error": message,
        },
    )

    role = routes.handle_get(
        object(), urlparse(f"/api/zhinang/session-role?session_id={session.session_id}")
    )
    personality = routes.handle_post(
        SimpleNamespace(), SimpleNamespace(path="/api/personality/set")
    )

    assert role["status"] == 409
    assert role["code"] == "zhinang_snapshot_invalid"
    assert personality["status"] == 409
    assert personality["code"] == "zhinang_personality_fixed"


def test_missing_bound_snapshot_sync_execution_rejects_before_provider(monkeypatch):
    from api import routes
    import run_agent

    session = Session(
        session_id="missing-bound-execution",
        workspace=".",
        zhinang_role_snapshot=None,
        zhinang_create_request_id="bound-request",
        zhinang_create_fingerprint="c" * 64,
    )
    provider = MagicMock()
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda _path: Path.cwd())
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: {"status": status, **payload},
    )

    with patch.object(run_agent, "OpenAI", provider):
        result = routes._handle_chat_sync(
            object(),
            {
                "session_id": session.session_id,
                "message": "must not reach provider",
                "request_id": "missing-bound-turn",
            },
        )

    assert result["status"] == 409
    assert result["code"] == "zhinang_snapshot_invalid"
    assert provider.call_count == 0


def test_missing_bound_snapshot_stream_start_rejects_before_worker(monkeypatch):
    from api import routes

    session = Session(
        session_id="missing-bound-stream-start",
        zhinang_role_snapshot=None,
        zhinang_create_request_id="bound-request",
        zhinang_create_fingerprint="d" * 64,
    )
    worker = MagicMock()
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_start_chat_stream_for_session", worker)
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: {"status": status, **payload},
    )

    result = routes._handle_chat_start(
        object(),
        {
            "session_id": session.session_id,
            "message": "must not start worker",
        },
    )

    assert result["status"] == 409
    assert result["code"] == "zhinang_snapshot_invalid"
    assert worker.call_count == 0


def test_frontend_draft_and_attachment_runtime_contract():
    root = Path(__file__).resolve().parents[1]
    node = os.environ.get("TAIJI_TEST_NODE") or "node"
    completed = subprocess.run(
        [node, str(root / "tests" / "zhinang_frontend_runtime_contract.cjs"), str(root / "static" / "sessions.js")],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("identity", "role_id", "beta"),
        ("identity", "source_path", "testing/beta.md"),
        ("identity", "source_sha256", "b" * 64),
        ("identity", "adapter_version", "tampered-adapter"),
        ("public", "name", "伪造角色 beta"),
        ("public", "raw_source", f"# beta\n\n{BETA}"),
        ("public", "limitations", "伪造限制"),
    ],
)
def test_complete_role_snapshot_tampering_fails_closed_before_provider_request(
    section, field, replacement
):
    damaged = _snapshot("alpha", ALPHA)
    damaged[section][field] = replacement
    agent = _real_mock_agent()
    session = Session(
        session_id=f"tampered-{section}-{field.replace('_', '-')}",
        zhinang_role_snapshot=damaged,
    )

    with pytest.raises(SessionRoleSnapshotError, match="智囊角色快照已损坏"):
        apply_session_role_to_agent(agent, session, base_ephemeral_prompt=None)

    assert agent.client.chat.completions.create.call_count == 0
    with pytest.raises(SessionRoleSnapshotError, match="智囊角色快照已损坏"):
        public_session_role_projection(damaged)


def test_duplicate_and_branch_copy_snapshot_but_reset_request_and_usage():
    source = Session(
        session_id="zhinang-source",
        zhinang_role_snapshot=_snapshot("alpha", ALPHA),
        zhinang_create_request_id="request-source",
        zhinang_create_fingerprint="a" * 64,
        zhinang_usage={
            "first_accepted_at": 10.0,
            "last_accepted_at": 20.0,
            "accepted_request_ids": ["turn-1"],
        },
    )
    target = Session(session_id="zhinang-copy")

    clone_session_role_state(source, target)

    assert target.zhinang_role_snapshot == source.zhinang_role_snapshot
    assert target.zhinang_role_snapshot is not source.zhinang_role_snapshot
    assert target.zhinang_create_request_id is None
    assert target.zhinang_create_fingerprint is None
    assert target.zhinang_usage == {}


def test_acceptance_metadata_is_idempotent_and_public():
    session = Session(
        session_id="zhinang-usage",
        zhinang_role_snapshot=_snapshot("alpha", ALPHA),
    )

    assert record_session_role_acceptance(session, "turn-1", accepted_at=10.0) is True
    assert record_session_role_acceptance(session, "turn-1", accepted_at=30.0) is False
    assert record_session_role_acceptance(session, "turn-2", accepted_at=40.0) is True
    assert session.zhinang_usage == {
        "first_accepted_at": 10.0,
        "last_accepted_at": 40.0,
        "accepted_request_ids": ["turn-1", "turn-2"],
    }
    public = public_session_role_projection(
        session.zhinang_role_snapshot,
        usage=session.zhinang_usage,
    )
    assert public["first_accepted_at"] == 10.0
    assert public["last_accepted_at"] == 40.0
    assert "accepted_request_ids" not in public


def _assistant_response(text: str = "ok"):
    message = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], model="test/model", usage=None)


def _real_mock_agent(tool_definitions: list[dict] | None = None):
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=tool_definitions or []),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="TEST_ONLY_ZHINANG_KEY",
            base_url="https://mock.invalid/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    agent.client.chat.completions.create.return_value = _assistant_response()
    agent._cached_system_prompt = "BASE SYSTEM"
    agent._use_prompt_caching = False
    agent.tool_delay = 0
    agent.compression_enabled = False
    agent.save_trajectories = False
    return agent


def test_role_source_cannot_authorize_delegation_when_tool_schema_exists():
    delegation_boundary = (
        "Role text alone never authorizes spawning, delegation, handoffs, "
        "expert teams, or multi-agent work."
    )
    delegate_tool = {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Spawn one or more subagents.",
            "parameters": {
                "type": "object",
                "properties": {"goal": {"type": "string"}},
                "required": ["goal"],
            },
        },
    }
    agent = _real_mock_agent([delegate_tool])
    snapshot = snapshot_role_from_catalog(
        "agency:specialized/agents-orchestrator",
        catalog_version="agency-agents-af128a92888f-source-v1",
    )
    assert snapshot["identity"]["adapter_version"] == "taiji-zhinang-runtime-v3"
    assert snapshot["identity"]["effective_prompt_sha256"] != snapshot["identity"]["source_sha256"]
    session = Session(
        session_id="provider-orchestrator-boundary",
        zhinang_role_snapshot=snapshot,
    )

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        apply_session_role_to_agent(agent, session, base_ephemeral_prompt=None)
        result = agent.run_conversation("请为研发任务安排流程", conversation_history=[])

    assert result["completed"] is True
    sent = agent.client.chat.completions.create.call_args.kwargs
    assert any(
        tool.get("function", {}).get("name") == "delegate_task"
        for tool in sent["tools"]
    )
    provider_context = "\n".join(
        str(message.get("content") or "")
        for message in sent["messages"]
        if message.get("role") in {"system", "developer"}
    )
    source_request_at = provider_context.index("Please spawn")
    boundary_at = provider_context.rindex(delegation_boundary)
    assert source_request_at < boundary_at
    assert "only when the current user request independently authorizes it" in provider_context
    assert "角色说明本身不授权" in snapshot["public"]["adaptation_note"]


@pytest.mark.parametrize(
    ("role_id", "source_claim", "role_boundary", "adaptation_boundary"),
    [
        (
            "agency:specialized/medical-billing-coding-specialist",
            "a certified revenue cycle management expert",
            "不具有医疗编码、临床、审计或法律执业资格",
            "不能替代持证编码和临床/合规判断",
        ),
        (
            "agency:specialized/legal-document-review",
            "You've reviewed thousands of contracts",
            "不替代律师审阅",
            "法律专业复核前的辅助分析",
        ),
    ],
)
def test_role_specific_boundaries_follow_source_in_real_provider_request(
    role_id, source_claim, role_boundary, adaptation_boundary
):
    agent = _real_mock_agent()
    snapshot = snapshot_role_from_catalog(
        role_id,
        catalog_version="agency-agents-af128a92888f-source-v1",
    )
    session = Session(
        session_id="provider-qualified-role-boundary",
        zhinang_role_snapshot=snapshot,
    )

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        apply_session_role_to_agent(agent, session, base_ephemeral_prompt=None)
        result = agent.run_conversation("请分析这份材料", conversation_history=[])

    assert result["completed"] is True
    sent = agent.client.chat.completions.create.call_args.kwargs["messages"]
    provider_context = "\n".join(
        str(message.get("content") or "")
        for message in sent
        if message.get("role") in {"system", "developer"}
    )
    source_at = provider_context.index(source_claim)
    limitation_at = provider_context.index(role_boundary)
    adaptation_at = provider_context.index(adaptation_boundary)
    generic_at = provider_context.rindex("Taiji runtime adaptation:")
    assert source_at < limitation_at < adaptation_at < generic_at


@pytest.mark.parametrize(
    ("selected_snapshot", "expected", "forbidden"),
    [
        (_snapshot("alpha", ALPHA), ALPHA, BETA),
        (_snapshot("beta", BETA), BETA, ALPHA),
    ],
)
def test_real_aiagent_provider_request_uses_only_selected_role_each_turn(
    selected_snapshot, expected, forbidden
):
    agent = _real_mock_agent()
    session = Session(
        session_id=f"provider-{expected[-4:].lower()}",
        zhinang_role_snapshot=copy.deepcopy(selected_snapshot),
    )

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        for turn in ("first", "second"):
            apply_session_role_to_agent(
                agent,
                session,
                base_ephemeral_prompt="WEBUI RUNTIME GUIDANCE",
            )
            result = agent.run_conversation(turn, conversation_history=[])
            assert result["completed"] is True
            sent = agent.client.chat.completions.create.call_args.kwargs["messages"]
            provider_context = "\n".join(str(message.get("content") or "") for message in sent)
            assert expected in provider_context
            assert forbidden not in provider_context


def test_strict_team_turn_does_not_receive_zhinang_role():
    agent = SimpleNamespace(ephemeral_system_prompt="old")
    session = Session(
        session_id="strict-team",
        zhinang_role_snapshot=_snapshot("alpha", ALPHA),
    )

    apply_session_role_to_agent(
        agent,
        session,
        base_ephemeral_prompt="WEBUI RUNTIME GUIDANCE",
        strict_turn=True,
    )

    assert agent.ephemeral_system_prompt is None


def _post(path: str, body: dict, *, profile: str | None = None) -> tuple[dict, int]:
    base = f"http://127.0.0.1:{os.environ['HERMES_WEBUI_TEST_PORT']}"
    headers = {"Content-Type": "application/json"}
    if profile:
        headers["Cookie"] = f"hermes_profile={profile}"
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read()), response.status
    except urllib.error.HTTPError as error:
        return json.loads(error.read()), error.code


def _get(path: str, *, profile: str | None = None) -> tuple[dict, int]:
    base = f"http://127.0.0.1:{os.environ['HERMES_WEBUI_TEST_PORT']}"
    headers = {"Cookie": f"hermes_profile={profile}"} if profile else {}
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read()), response.status
    except urllib.error.HTTPError as error:
        return json.loads(error.read()), error.code


def test_role_session_create_is_idempotent_and_conflicts_on_changed_parameters():
    request_id = "zhinang-create-idempotency-a"
    body = {
        "zhinang_role_id": "agency:sales/sales-engineer",
        "catalog_version": "agency-agents-af128a92888f-source-v1",
        "request_id": request_id,
        "composer_draft": {"text": "请分析需求", "files": []},
    }

    first, first_status = _post("/api/session/new", body)
    replay, replay_status = _post("/api/session/new", body)
    conflict, conflict_status = _post(
        "/api/session/new",
        body | {"zhinang_role_id": "agency:product/product-manager"},
    )

    assert first_status == replay_status == 200
    assert first["session"]["session_id"] == replay["session"]["session_id"]
    assert first["session"]["zhinang_role"]["role_id"] == body["zhinang_role_id"]
    assert first["session"]["composer_draft"]["text"] == "请分析需求"
    assert conflict_status == 409
    assert conflict["code"] == "zhinang_create_idempotency_conflict"


@pytest.mark.parametrize(
    ("role_id", "catalog_version", "suffix"),
    [
        (
            "agency:sales/sales-engineer",
            "retired-catalog-version",
            "catalog-changed",
        ),
        (
            "agency:removed/retired-role",
            "agency-agents-af128a92888f-source-v1",
            "role-removed",
        ),
    ],
)
def test_durable_create_replay_precedes_current_catalog_resolution(
    role_id, catalog_version, suffix
):
    from api.routes import _zhinang_create_fingerprint

    request_id = f"zhinang-durable-replay-{suffix}"
    composer_draft = {"text": "服务已保存但响应丢失", "files": []}
    fingerprint = _zhinang_create_fingerprint({
        "role_id": role_id,
        "catalog_version": catalog_version,
        "workspace": None,
        "model": None,
        "model_provider": None,
        "profile": "default",
        "project_id": None,
        "composer_draft": composer_draft,
    })
    persisted = Session(
        session_id=f"durable-replay-{suffix}",
        zhinang_role_snapshot=_snapshot(role_id, ALPHA),
        zhinang_create_request_id=request_id,
        zhinang_create_fingerprint=fingerprint,
        profile="default",
        composer_draft=composer_draft,
    )
    persisted.save(skip_index=True)

    replay, status = _post(
        "/api/session/new",
        {
            "zhinang_role_id": role_id,
            "catalog_version": catalog_version,
            "request_id": request_id,
            "composer_draft": composer_draft,
        },
    )

    assert status == 200, replay
    assert replay["session"]["session_id"] == persisted.session_id
    assert replay["session"]["composer_draft"]["text"] == composer_draft["text"]


def test_durable_replay_scan_isolates_unrelated_corrupt_sidecar(
    tmp_path, monkeypatch
):
    from api import config, models, routes

    class OrderedSessionDir:
        def __init__(self, root, paths):
            self.root = root
            self.paths = paths

        def __fspath__(self):
            return str(self.root)

        def glob(self, _pattern):
            return self.paths

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", tmp_path / "_index.json")
    monkeypatch.setattr(routes, "SESSIONS", OrderedDict())
    target = Session(
        session_id="durable-scan-target",
        zhinang_role_snapshot=_snapshot("alpha", ALPHA),
        zhinang_create_request_id="durable-scan-request",
        zhinang_create_fingerprint="a" * 64,
        profile="default",
    )
    target.save(skip_index=True)
    corrupt_path = tmp_path / "000-corrupt.json"
    corrupt_path.write_text("{", encoding="utf-8")
    target_path = tmp_path / f"{target.session_id}.json"
    ordered_session_dir = OrderedSessionDir(tmp_path, [corrupt_path, target_path])
    monkeypatch.setattr(config, "SESSION_DIR", ordered_session_dir)

    replay = routes._find_zhinang_create_replay(
        "default", "durable-scan-request"
    )

    assert replay is not None
    assert replay.session_id == target.session_id
    assert getattr(replay, "_loaded_metadata_only", False) is False


def test_durable_replay_scan_fails_closed_when_corruption_hides_absence(
    tmp_path, monkeypatch
):
    from api import config, models, routes

    class OrderedSessionDir:
        def __init__(self, root, paths):
            self.root = root
            self.paths = paths

        def __fspath__(self):
            return str(self.root)

        def glob(self, _pattern):
            return self.paths

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(routes, "SESSIONS", OrderedDict())
    corrupt_path = tmp_path / "000-corrupt.json"
    corrupt_path.write_text("{", encoding="utf-8")
    ordered_session_dir = OrderedSessionDir(tmp_path, [corrupt_path])
    monkeypatch.setattr(config, "SESSION_DIR", ordered_session_dir)

    with pytest.raises(RuntimeError, match="durable.*replay"):
        routes._find_zhinang_create_replay("default", "unknown-request")


def test_create_route_does_not_duplicate_when_durable_scan_is_incomplete():
    sessions_dir = Path(os.environ["HERMES_WEBUI_TEST_STATE_DIR"]) / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    corrupt_path = sessions_dir / "corrupt-create-replay.json"
    corrupt_path.write_text("{", encoding="utf-8")
    try:
        response, status = _post(
            "/api/session/new",
            {
                "zhinang_role_id": "agency:sales/sales-engineer",
                "catalog_version": "agency-agents-af128a92888f-source-v1",
                "request_id": "zhinang-scan-fail-closed-http",
            },
        )
    finally:
        corrupt_path.unlink(missing_ok=True)

    assert status == 409
    assert response["code"] == "zhinang_create_replay_unavailable"
    assert "session" not in response


def test_role_session_concurrent_create_returns_one_logical_session():
    body = {
        "zhinang_role_id": "agency:sales/sales-engineer",
        "catalog_version": "agency-agents-af128a92888f-source-v1",
        "request_id": "zhinang-create-concurrent-a",
        "composer_draft": {"text": "并发点击只保留一次", "files": []},
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: _post("/api/session/new", body), range(2)))

    payloads = [payload for payload, status in results if status == 200]
    assert len(payloads) == 2
    assert len({payload["session"]["session_id"] for payload in payloads}) == 1
    assert all(
        payload["session"]["composer_draft"]["text"] == "并发点击只保留一次"
        for payload in payloads
    )


def test_role_session_creation_keeps_runtime_config_and_rejects_illegal_context():
    config_path = Path(os.environ["HERMES_CONFIG_PATH"])
    before = config_path.read_bytes() if config_path.exists() else None
    base = {
        "zhinang_role_id": "agency:sales/sales-engineer",
        "catalog_version": "agency-agents-af128a92888f-source-v1",
        "request_id": "zhinang-create-no-model-a",
    }

    created, created_status = _post("/api/session/new", base)
    bad_profile, bad_profile_status = _post(
        "/api/session/new",
        base | {"request_id": "bad-profile-a", "profile": "../../invalid"},
    )
    bad_project, bad_project_status = _post(
        "/api/session/new",
        base | {"request_id": "bad-project-a", "project_id": "missing-project"},
    )
    bad_workspace, bad_workspace_status = _post(
        "/api/session/new",
        base | {"request_id": "bad-workspace-a", "workspace": "\x00invalid"},
    )
    after = config_path.read_bytes() if config_path.exists() else None

    assert created_status == 200
    assert created["session"]["zhinang_role"]["role_id"] == base["zhinang_role_id"]
    assert bad_profile_status == bad_project_status == bad_workspace_status == 400
    assert "profile" in bad_profile["error"]
    assert "project" in bad_project["error"]
    assert bad_workspace["error"]
    assert before == after


def test_role_session_creation_and_history_are_scoped_to_request_profile():
    named_profile = "zhinang-research"
    profile_created, profile_status = _post(
        "/api/profile/create",
        {"name": named_profile},
        profile="default",
    )
    assert profile_status == 200, profile_created

    base = {
        "zhinang_role_id": "agency:sales/sales-engineer",
        "catalog_version": "agency-agents-af128a92888f-source-v1",
        "profile": "default",
    }
    created, created_status = _post(
        "/api/session/new",
        base | {"request_id": "zhinang-profile-owner-a"},
        profile="default",
    )
    assert created_status == 200
    assert created["session"]["profile"] == "default"
    sid = created["session"]["session_id"]

    owner_view, owner_status = _get(
        f"/api/zhinang/session-role?session_id={sid}",
        profile="default",
    )
    cross_view, cross_status = _get(
        f"/api/zhinang/session-role?session_id={sid}",
        profile="research",
    )
    divergent, divergent_status = _post(
        "/api/session/new",
        base | {"request_id": "zhinang-profile-divergent-a"},
        profile="research",
    )

    assert owner_status == 200
    assert owner_view["role"]["role_id"] == base["zhinang_role_id"]
    assert cross_status == 404
    assert cross_view["error"] == "Session not found"
    assert divergent_status == 400
    assert "active request profile" in divergent["error"]

    named_created, named_status = _post(
        "/api/session/new",
        (base | {
            "profile": named_profile,
            "request_id": "zhinang-profile-named-a",
        }),
        profile=named_profile,
    )
    assert named_status == 200
    assert named_created["session"]["profile"] == named_profile
    named_role, named_role_status = _get(
        "/api/zhinang/session-role?session_id="
        + named_created["session"]["session_id"],
        profile=named_profile,
    )
    assert named_role_status == 200
    assert named_role["role"]["role_id"] == base["zhinang_role_id"]


def test_role_session_history_personality_duplicate_branch_and_clear_contract():
    created, status = _post(
        "/api/session/new",
        {
            "zhinang_role_id": "agency:sales/sales-engineer",
            "catalog_version": "agency-agents-af128a92888f-source-v1",
            "request_id": "zhinang-lifecycle-a",
            "composer_draft": {"text": "保留草稿", "files": []},
        },
    )
    assert status == 200
    sid = created["session"]["session_id"]

    role, role_status = _get(
        f"/api/zhinang/session-role?session_id={sid}"
    )
    personality, personality_status = _post(
        "/api/personality/set", {"session_id": sid, "name": ""}
    )
    duplicate, duplicate_status = _post(
        "/api/session/duplicate", {"session_id": sid}
    )
    branch, branch_status = _post(
        "/api/session/branch", {"session_id": sid, "keep_count": 0}
    )
    cleared, clear_status = _post("/api/session/clear", {"session_id": sid})

    assert role_status == 200
    assert role["role"]["role_id"] == "agency:sales/sales-engineer"
    assert "raw_source" in role["role"]
    assert "effective_prompt" not in role["role"]
    assert personality_status == 409
    assert "固定智囊角色" in personality["error"]
    assert duplicate_status == branch_status == clear_status == 200
    assert duplicate["session"]["zhinang_role"]["role_id"] == role["role"]["role_id"]
    branch_session, branch_get_status = _get(
        f"/api/session?session_id={branch['session_id']}"
    )
    assert branch_get_status == 200
    assert branch_session["session"]["zhinang_role"]["role_id"] == role["role"]["role_id"]
    assert cleared["session"]["zhinang_role"]["role_id"] == role["role"]["role_id"]


def test_gateway_keeps_role_when_optional_prefill_loading_fails(tmp_path, monkeypatch):
    from api import config, gateway_chat, models, streaming
    from api.config import STREAMS, create_stream_channel

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.invalid")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "chat_completions")
    monkeypatch.setattr(
        streaming,
        "_load_webui_prefill_context",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("optional prefill failed")),
    )
    monkeypatch.setattr(gateway_chat, "RunJournalWriter", lambda *_a, **_k: None)

    captured = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b"data: [DONE]\n\n"

    def urlopen(request, *_args, **_kwargs):
        captured.append(json.loads(request.data.decode("utf-8")))
        return Response()

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", urlopen)
    session = Session(
        session_id="gateway-role-prefill-failure",
        workspace=str(tmp_path),
        model="test-model",
        zhinang_role_snapshot=_snapshot("alpha", ALPHA),
    )
    stream_id = "gateway-role-stream"
    session.active_stream_id = stream_id
    session.pending_user_message = "test"
    session.pending_started_at = time.time()
    session.save()
    STREAMS[stream_id] = create_stream_channel()

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        "test",
        "test-model",
        str(tmp_path),
        stream_id,
        [],
        turn_id="gateway-role-turn",
    )

    assert len(captured) == 1
    provider_context = "\n".join(
        str(message.get("content") or "") for message in captured[0]["messages"]
    )
    assert ALPHA in provider_context
    assert BETA not in provider_context


def test_streaming_path_reuses_real_aiagent_and_reapplies_only_fixed_role(tmp_path, monkeypatch):
    import queue
    import sys
    import types

    from api import config, oauth, profiles, streaming

    personality_sentinel = "TAIJI_PERSONALITY_SENTINEL_MUST_NOT_APPEAR"
    sessions = {}
    constructed = []

    class MemorySession(Session):
        def save(self, *args, **kwargs):
            return None

    def agent_factory(**kwargs):
        agent = _real_mock_agent()
        for key, value in kwargs.items():
            if key.endswith("callback") or hasattr(agent, key):
                setattr(agent, key, value)
        agent.session_id = kwargs.get("session_id")
        agent.model = kwargs.get("model")
        agent.provider = kwargs.get("provider")
        agent.base_url = kwargs.get("base_url")
        from agent.image_runtime import capture_capability_runtime_generation

        generation = capture_capability_runtime_generation()
        assert generation.stable
        agent._capability_runtime_identity = generation.identity
        constructed.append(agent)
        return agent

    profile_home = tmp_path / "profile-home"
    profile_home.mkdir()
    fake_runtime_module = types.ModuleType("hermes_cli.runtime_provider")
    fake_runtime_module.resolve_runtime_provider = lambda requested=None: {
        "provider": requested or "test-provider",
        "api_key": "TEST_ONLY_ZHINANG_KEY",
        "base_url": None,
    }
    fake_hermes_cli = types.ModuleType("hermes_cli")
    fake_hermes_cli.runtime_provider = fake_runtime_module
    fake_hermes_state = types.ModuleType("hermes_state")
    fake_hermes_state.SessionDB = lambda **_kwargs: None
    fake_hermes_state.install_state_write_guard = lambda _guard: None

    monkeypatch.setattr(streaming, "get_session", lambda sid: sessions[sid])
    monkeypatch.setattr(streaming, "_get_ai_agent", lambda: agent_factory)
    monkeypatch.setattr(
        streaming,
        "resolve_model_provider",
        lambda _model: ("test-model", "test-provider", None),
    )
    monkeypatch.setattr(streaming, "_maybe_schedule_title_refresh", lambda *_a, **_k: None)
    monkeypatch.setattr(streaming, "get_state_db_session_messages", lambda *_a, **_k: [])
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda _profile: profile_home)
    monkeypatch.setattr(profiles, "get_profile_runtime_env", lambda _home: {})
    monkeypatch.setattr(
        oauth,
        "resolve_runtime_provider_with_anthropic_env_lock",
        lambda _resolver, requested=None: {
            "provider": requested or "test-provider",
            "api_key": "TEST_ONLY_ZHINANG_KEY",
            "base_url": None,
        },
    )
    monkeypatch.setattr(
        config,
        "get_config",
        lambda *args, **kwargs: {
            "agent": {"personalities": {"legacy": personality_sentinel}}
        },
    )
    monkeypatch.setattr(config, "_resolve_cli_toolsets", lambda _cfg: [])
    monkeypatch.setattr(config, "load_settings", lambda: {})
    monkeypatch.setitem(sys.modules, "hermes_cli", fake_hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.runtime_provider", fake_runtime_module)
    monkeypatch.setitem(sys.modules, "hermes_state", fake_hermes_state)

    with config.SESSION_AGENT_CACHE_LOCK:
        config.SESSION_AGENT_CACHE.clear()
    streaming.STREAMS.clear()
    streaming.CANCEL_FLAGS.clear()
    streaming.AGENT_INSTANCES.clear()
    streaming.STREAM_PARTIAL_TEXT.clear()
    streaming.STREAM_REASONING_TEXT.clear()
    streaming.STREAM_LIVE_TOOL_CALLS.clear()

    for role_id, sentinel, forbidden in (
        ("alpha", ALPHA, BETA),
        ("beta", BETA, ALPHA),
    ):
        session = MemorySession(
            session_id=f"stream-role-{role_id}",
            title="Pinned title",
            workspace=str(tmp_path),
            model="test-model",
            model_provider="test-provider",
            profile="default",
            personality="legacy",
            zhinang_role_snapshot=_snapshot(role_id, sentinel),
        )
        session.llm_title_generated = True
        sessions[session.session_id] = session
        for turn_number in (1, 2):
            stream_id = f"{session.session_id}-{turn_number}"
            session.active_stream_id = stream_id
            streaming.STREAMS[stream_id] = queue.Queue()
            streaming._run_agent_streaming(
                session_id=session.session_id,
                msg_text=f"turn {turn_number}",
                model="test-model",
                model_provider="test-provider",
                workspace=str(tmp_path),
                stream_id=stream_id,
            )
            agent = next(item for item in constructed if item.session_id == session.session_id)
            sent = agent.client.chat.completions.create.call_args.kwargs["messages"]
            provider_context = "\n".join(
                str(message.get("content") or "") for message in sent
            )
            assert sentinel in provider_context
            assert forbidden not in provider_context
            assert personality_sentinel not in provider_context
            assert "Brand privacy policy for ordinary WebUI conversations" in provider_context

    assert len(constructed) == 2
    assert all(len(agent.client.chat.completions.create.call_args_list) == 2 for agent in constructed)


@pytest.mark.parametrize(
    ("failure_mode", "role_id", "expected", "forbidden"),
    [
        ("result_error", "alpha", ALPHA, BETA),
        ("result_error", "beta", BETA, ALPHA),
        ("exception", "alpha", ALPHA, BETA),
        ("exception", "beta", BETA, ALPHA),
    ],
)
def test_streaming_credential_self_heal_rebuilt_aiagent_sends_fixed_role(
    tmp_path, monkeypatch, failure_mode, role_id, expected, forbidden
):
    import queue
    import sys
    import types

    from api import config, oauth, profiles, streaming

    personality_sentinel = "TAIJI_SELF_HEAL_PERSONALITY_MUST_NOT_APPEAR"
    constructed = []

    class MemorySession(Session):
        def save(self, *args, **kwargs):
            return None

    session = MemorySession(
        session_id=f"self-heal-{failure_mode}",
        title="Pinned title",
        workspace=str(tmp_path),
        model="test-model",
        model_provider="test-provider",
        profile="default",
        personality="legacy",
        zhinang_role_snapshot=_snapshot(role_id, expected),
    )
    session.llm_title_generated = True

    def agent_factory(**kwargs):
        agent = _real_mock_agent()
        for key, value in kwargs.items():
            if key.endswith("callback") or hasattr(agent, key):
                setattr(agent, key, value)
        agent.session_id = kwargs.get("session_id")
        agent.model = kwargs.get("model")
        agent.provider = kwargs.get("provider")
        agent.base_url = kwargs.get("base_url")
        from agent.image_runtime import capture_capability_runtime_generation

        generation = capture_capability_runtime_generation()
        assert generation.stable
        agent._capability_runtime_identity = generation.identity
        constructed.append(agent)
        if len(constructed) == 1:
            if failure_mode == "result_error":
                agent.run_conversation = MagicMock(
                    return_value={
                        "completed": False,
                        "messages": [],
                        "error": "401 Unauthorized synthetic initial credential",
                    }
                )
            else:
                agent.run_conversation = MagicMock(
                    side_effect=RuntimeError(
                        "401 Unauthorized synthetic initial credential"
                    )
                )
        return agent

    profile_home = tmp_path / "profile-home"
    profile_home.mkdir()
    fake_runtime_module = types.ModuleType("hermes_cli.runtime_provider")
    fake_runtime_module.resolve_runtime_provider = lambda requested=None: {
        "provider": requested or "test-provider",
        "api_key": "TEST_ONLY_ZHINANG_INITIAL",
        "base_url": "https://mock.invalid/v1",
    }
    fake_hermes_cli = types.ModuleType("hermes_cli")
    fake_hermes_cli.runtime_provider = fake_runtime_module
    fake_hermes_state = types.ModuleType("hermes_state")
    fake_hermes_state.SessionDB = lambda **_kwargs: None
    fake_hermes_state.install_state_write_guard = lambda _guard: None

    monkeypatch.setattr(streaming, "get_session", lambda _sid: session)
    monkeypatch.setattr(streaming, "_get_ai_agent", lambda: agent_factory)
    monkeypatch.setattr(
        streaming,
        "resolve_model_provider",
        lambda _model: ("test-model", "test-provider", "https://mock.invalid/v1"),
    )
    monkeypatch.setattr(streaming, "_maybe_schedule_title_refresh", lambda *_a, **_k: None)
    monkeypatch.setattr(streaming, "get_state_db_session_messages", lambda *_a, **_k: [])
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda _profile: profile_home)
    monkeypatch.setattr(profiles, "get_profile_runtime_env", lambda _home: {})
    monkeypatch.setattr(
        oauth,
        "resolve_runtime_provider_with_anthropic_env_lock",
        lambda _resolver, requested=None: {
            "provider": requested or "test-provider",
            "api_key": "TEST_ONLY_ZHINANG_INITIAL",
            "base_url": "https://mock.invalid/v1",
        },
    )
    heal = MagicMock(
        return_value={
            "provider": "test-provider",
            "api_key": "TEST_ONLY_ZHINANG_REFRESHED",
            "base_url": "https://mock.invalid/v1",
        }
    )
    monkeypatch.setattr(streaming, "_attempt_credential_self_heal", heal)
    monkeypatch.setattr(
        config,
        "get_config",
        lambda *args, **kwargs: {
            "agent": {"personalities": {"legacy": personality_sentinel}}
        },
    )
    monkeypatch.setattr(config, "_resolve_cli_toolsets", lambda _cfg: [])
    monkeypatch.setattr(config, "load_settings", lambda: {})
    monkeypatch.setitem(sys.modules, "hermes_cli", fake_hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.runtime_provider", fake_runtime_module)
    monkeypatch.setitem(sys.modules, "hermes_state", fake_hermes_state)

    with config.SESSION_AGENT_CACHE_LOCK:
        config.SESSION_AGENT_CACHE.clear()
    streaming.STREAMS.clear()
    streaming.CANCEL_FLAGS.clear()
    streaming.AGENT_INSTANCES.clear()
    streaming.STREAM_PARTIAL_TEXT.clear()
    streaming.STREAM_REASONING_TEXT.clear()
    streaming.STREAM_LIVE_TOOL_CALLS.clear()

    stream_id = f"{session.session_id}-stream"
    session.active_stream_id = stream_id
    streaming.STREAMS[stream_id] = queue.Queue()
    streaming._run_agent_streaming(
        session_id=session.session_id,
        msg_text="trigger controlled credential recovery",
        model="test-model",
        model_provider="test-provider",
        workspace=str(tmp_path),
        stream_id=stream_id,
    )

    assert heal.call_count == 1
    assert len(constructed) == 2
    assert constructed[0] is not constructed[1]
    assert constructed[0].run_conversation.call_count == 1
    healed_agent = constructed[1]
    assert healed_agent.api_key == "TEST_ONLY_ZHINANG_REFRESHED"
    assert healed_agent.client.chat.completions.create.call_count == 1
    sent = healed_agent.client.chat.completions.create.call_args.kwargs["messages"]
    provider_context = "\n".join(
        str(message.get("content") or "") for message in sent
        if message.get("role") in {"system", "developer"}
    )
    assert expected in provider_context
    assert forbidden not in provider_context
    assert personality_sentinel not in provider_context
    assert "Brand privacy policy for ordinary WebUI conversations" in provider_context


@pytest.mark.parametrize(
    ("snapshot", "expected", "forbidden"),
    [
        (_snapshot("alpha", ALPHA), ALPHA, BETA),
        (_snapshot("beta", BETA), BETA, ALPHA),
    ],
)
def test_sync_chat_route_sends_selected_role_to_real_aiagent_each_turn(
    tmp_path, monkeypatch, snapshot, expected, forbidden
):
    from api import config, oauth, routes
    import run_agent

    session = Session(
        session_id=f"sync-route-{expected[-4:].lower()}",
        workspace=str(tmp_path),
        model="test-model",
        model_provider="test-provider",
        zhinang_role_snapshot=copy.deepcopy(snapshot),
    )
    responses = []
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda model, provider: (model, provider, None),
    )
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda path: tmp_path)
    monkeypatch.setattr(routes, "_rewrite_existing_session_truth", lambda _s, fn, **_k: fn())
    monkeypatch.setattr(routes, "load_settings", lambda: {})
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: responses.append((status, payload)),
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400, **_kwargs: responses.append(
            (status, {"error": message})
        ),
    )
    monkeypatch.setattr(
        config,
        "resolve_model_provider",
        lambda _model: ("test-model", "test-provider", "https://mock.invalid/v1"),
    )
    monkeypatch.setattr(config, "get_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(config, "_resolve_cli_toolsets", lambda _cfg: [])
    monkeypatch.setattr(routes, "_resolve_cli_toolsets", lambda: [])
    monkeypatch.setattr(
        oauth,
        "resolve_runtime_provider_with_anthropic_env_lock",
        lambda _resolver, requested=None: {
            "provider": requested or "test-provider",
            "api_key": "TEST_ONLY_ZHINANG_KEY",
            "base_url": None,
        },
    )
    client = MagicMock()
    client.chat.completions.create.return_value = _assistant_response()

    with (
        patch.object(run_agent, "get_tool_definitions", return_value=[]),
        patch.object(run_agent, "check_toolset_requirements", return_value={}),
        patch.object(run_agent, "OpenAI", return_value=client),
        patch.object(run_agent.AIAgent, "_persist_session"),
        patch.object(run_agent.AIAgent, "_save_trajectory"),
        patch.object(run_agent.AIAgent, "_cleanup_task_resources"),
    ):
        for number in (1, 2):
            routes._handle_chat_sync(
                object(),
                {
                    "session_id": session.session_id,
                    "message": f"turn {number}",
                    "model": "test-model",
                    "model_provider": "test-provider",
                    "request_id": f"sync-turn-{number}",
                },
            )
            sent = client.chat.completions.create.call_args.kwargs["messages"]
            provider_context = "\n".join(
                str(message.get("content") or "") for message in sent
            )
            assert expected in provider_context
            assert forbidden not in provider_context

    assert len(client.chat.completions.create.call_args_list) == 2
    assert [row[0] for row in responses] == [200, 200]
    assert session.zhinang_usage["accepted_request_ids"] == [
        "sync-turn-1",
        "sync-turn-2",
    ]


def test_compression_snapshot_and_restart_tip_keep_exact_role(tmp_path, monkeypatch):
    from api import config, models, streaming

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", tmp_path / "_index.json")
    monkeypatch.setattr(streaming, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(config, "SESSION_DIR", tmp_path)
    original_id = "zhinang-compress-parent"
    role_snapshot = _snapshot("alpha", ALPHA)
    session = Session(
        session_id=original_id,
        workspace=str(tmp_path),
        messages=[{"role": "user", "content": "before compression"}],
        zhinang_role_snapshot=copy.deepcopy(role_snapshot),
        zhinang_create_request_id="compression-create",
        zhinang_create_fingerprint="c" * 64,
    )
    session.save()
    session.messages.append({"role": "assistant", "content": "completed"})

    streaming._preserve_pre_compression_snapshot(session, original_id)
    session.session_id = "zhinang-compress-tip"
    session.__dict__.pop("_loaded_sidecar_sha256", None)
    session.pre_compression_snapshot = False
    session.parent_session_id = original_id
    session.save()

    parent = Session.load(original_id)
    tip = Session.load_metadata_only("zhinang-compress-tip")
    assert parent is not None and tip is not None
    assert parent.pre_compression_snapshot is True
    assert tip.pre_compression_snapshot is False
    assert parent.zhinang_role_snapshot == role_snapshot
    assert tip.zhinang_role_snapshot == role_snapshot
    assert tip.compact()["zhinang_role"]["effective_prompt_sha256"] == (
        role_snapshot["identity"]["effective_prompt_sha256"]
    )


def test_both_streaming_credential_self_heal_rebuilds_reapply_role_before_retry():
    source = (
        Path(__file__).resolve().parents[1] / "api" / "streaming.py"
    ).read_text(encoding="utf-8")

    retries = (
        ("agent = _require_agent_capability_generation(", "_heal_result = agent.run_conversation("),
        ("_heal_agent = _require_agent_capability_generation(", "_heal_result = _heal_agent.run_conversation("),
    )
    for constructor, retry in retries:
        retry_at = source.index(retry)
        constructor_at = source.rfind(constructor, 0, retry_at)
        role_at = source.rfind("apply_session_role_to_agent(", constructor_at, retry_at)
        assert constructor_at >= 0
        assert constructor_at < role_at < retry_at


def test_frontend_role_transition_waits_for_old_draft_before_mutating_ui():
    source = (Path(__file__).resolve().parents[1] / "static" / "sessions.js").read_text(
        encoding="utf-8"
    )
    start = source.index("async function newSession(flash, options={})")
    end = source.index("async function loadSession", start)
    block = source[start:end]
    wait_at = block.index("await _saveComposerDraftBeforeTransition")
    mutate_at = block.index("_resetWriteflowDockForSessionChange('new-session-start')")
    request_at = block.index("const data=await api('/api/session/new'")
    assert wait_at < mutate_at < request_at
    for field in (
        "zhinang_role_id",
        "catalog_version",
        "request_id",
        "composer_draft",
    ):
        assert field in block
    assert "async function createZhinangSession" in source
