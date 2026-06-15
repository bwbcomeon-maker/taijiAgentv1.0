import json
from types import SimpleNamespace
from urllib.parse import urlparse


def test_expert_team_catalog_includes_brand_moodboard_template():
    from api import expert_teams

    data = expert_teams.expert_team_catalog()

    team = next(item for item in data["teams"] if item["id"] == "ai-content-creator-brand-moodboard")
    assert team["title"] == "品牌视觉策划与情绪板"
    assert [member["name"] for member in team["members"]] == ["司远", "策凌", "珀西"]
    assert [question["id"] for question in team["questions"]] == ["product_type", "audience", "brand_feeling"]
    assert "WorkBuddy" not in json.dumps(team, ensure_ascii=False)
    assert "Hermes" not in json.dumps(team, ensure_ascii=False)


def test_expert_team_start_persists_awaiting_questions(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-1",
            "team_id": "ai-content-creator-brand-moodboard",
            "prompt": "帮我确定新产品的品牌视觉方向，做一个情绪板",
        },
    )

    assert run["status"] == "awaiting_user"
    assert run["phase"] == "需求确认"
    assert run["questions"][0]["status"] == "pending"
    assert run["members"][0]["status"] == "待命"
    assert run["tasks"][0]["status"] == "pending"
    assert run["duration_seconds"] >= 0

    reloaded = expert_teams.read_expert_team_run(tmp_path, run["run_id"])
    assert reloaded["run_id"] == run["run_id"]
    assert reloaded["session_id"] == "sid-1"
    assert reloaded["questions"][1]["title"] == "目标受众是哪类人群？"


def test_expert_team_answer_creates_direction_gate_and_keeps_public_state(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-2",
            "team_id": "ai-content-creator-brand-moodboard",
            "prompt": "帮我确定新产品的品牌视觉方向，做一个情绪板",
        },
    )
    updated = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "product_type": "科技/数码",
                "audience": "Z世代/学生",
                "brand_feeling": "活力·年轻·大胆",
            },
        },
    )

    assert updated["status"] == "awaiting_user"
    assert updated["phase"] == "方向确认"
    assert updated["questions"][-1]["id"] == "visual_direction"
    assert updated["questions"][-1]["status"] == "pending"
    assert all(question["status"] == "answered" for question in updated["questions"][:-1])
    assert updated["members"][1]["status"] == "已完成"
    assert updated["tasks"][0]["status"] == "done"
    assert "赛博涂鸦" in updated["tasks"][0]["result_summary"]
    assert updated["tasks"][1]["status"] == "pending"
    assert updated["progress"] == {"done": 1, "total": 2}
    event_types = [event["type"] for event in updated["events"]]
    assert event_types[:3] == ["questions_answered", "team_created", "task_started"]
    assert "task_done" in event_types
    public_json = json.dumps(updated, ensure_ascii=False)
    for token in ("skill_view", "terminal", "profile", "HERMES_", "hermes-local-lab", "/Users/"):
        assert token not in public_json


def test_expert_team_second_gate_completes_moodboard_delivery(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-3",
            "team_id": "ai-content-creator-brand-moodboard",
            "prompt": "帮我确定新产品的品牌视觉方向，做一个情绪板",
        },
    )
    first = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "product_type": "科技/数码",
                "audience": "Z世代/学生",
                "brand_feeling": "活力·年轻·大胆",
            },
        },
    )
    gated = expert_teams.read_expert_team_run(tmp_path, first["run_id"])
    assert gated["status"] == "awaiting_user"
    assert gated["phase"] == "方向确认"
    assert gated["questions"][-1]["id"] == "visual_direction"
    assert gated["tasks"][0]["status"] == "done"

    moved = expert_teams.answer_expert_team(
        tmp_path,
        {"run_id": first["run_id"], "answers": {"visual_direction": "A 赛博涂鸦"}},
    )
    assert moved["status"] == "done"
    assert moved["phase"] == "交付"
    assert moved["members"][1]["status"] == "已完成"
    assert moved["members"][2]["status"] == "已完成"
    assert moved["tasks"][1]["status"] == "done"
    assert moved["progress"] == {"done": 2, "total": 2}
    assert len(moved["artifacts"]) == 5
    assert moved["artifacts"][0]["label"] == "情绪板方向说明"


def test_expert_team_writeflow_adapter_preserves_existing_runs():
    from api import expert_teams

    adapted = expert_teams.expert_team_from_writeflow_run(
        {
            "run_id": "wr-demo",
            "session_id": "sid-old",
            "team_id": "content-creator-team",
            "title": "公众号长文",
            "status": "running",
            "phase": "生成初稿",
            "members": [{"id": "writing-executor", "name": "文案创作专家", "role": "正文写作", "status": "执行中"}],
            "tasks": [{"id": "draft", "title": "撰写公众号长文", "status": "running"}],
            "artifacts": [],
            "events": [],
        }
    )

    assert adapted["run_id"] == "wr-demo"
    assert adapted["team_id"] == "content-creator-team"
    assert adapted["source"] == "writeflow"
    assert adapted["status"] == "running"
    assert adapted["members"][0]["name"] == "文案创作专家"


def test_expert_team_routes_start_answer_and_read(monkeypatch, tmp_path):
    import api.routes as routes

    sent = {}

    def fake_j(_handler, payload, status=200, **_kwargs):
        sent["payload"] = payload
        sent["status"] = status
        return payload

    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: (_ for _ in ()).throw(KeyError("missing-session")))
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: tmp_path)

    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {
            "session_id": "sid-route",
            "team_id": "ai-content-creator-brand-moodboard",
            "prompt": "帮我确定新产品的品牌视觉方向，做一个情绪板",
        },
    )
    assert routes.handle_post(object(), urlparse("/api/expert-teams/start"))["ok"] is True
    run = sent["payload"]["run"]
    assert run["status"] == "awaiting_user"

    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {
            "run_id": run["run_id"],
            "answers": {
                "product_type": "科技/数码",
                "audience": "Z世代/学生",
                "brand_feeling": "活力·年轻·大胆",
            },
        },
    )
    assert routes.handle_post(object(), urlparse("/api/expert-teams/answer"))["ok"] is True
    assert sent["payload"]["run"]["status"] == "awaiting_user"
    assert sent["payload"]["run"]["questions"][-1]["id"] == "visual_direction"

    assert routes.handle_get(object(), urlparse("/api/expert-teams/run?session_id=sid-route"))["ok"] is True
    assert sent["payload"]["run"]["run_id"] == run["run_id"]
    assert sent["payload"]["run"]["phase"] == "方向确认"


def test_content_expert_team_answer_starts_real_stream_without_exposing_internal_prompt(monkeypatch, tmp_path):
    import api.routes as routes
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-stream",
            "team_id": "content-creator-team",
            "prompt": "帮我写一篇公众号长文",
        },
    )
    sent = {}
    calls = {}
    session = SimpleNamespace(
        session_id="sid-stream",
        workspace=str(tmp_path),
        model="deepseek-v4-pro",
        model_provider=None,
        messages=[],
        context_messages=[],
        active_stream_id=None,
        pending_user_message=None,
        pending_attachments=None,
        pending_started_at=None,
        title="Untitled",
        save=lambda *args, **kwargs: None,
    )

    def fake_j(_handler, payload, status=200, **_kwargs):
        sent["payload"] = payload
        sent["status"] = status
        return payload

    def fake_start_stream(s, **kwargs):
        calls["session"] = s
        calls.update(kwargs)
        return {
            "stream_id": "stream-real-1",
            "session_id": s.session_id,
            "pending_started_at": 1781346000.0,
            "title": "专家团任务",
        }

    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "get_session", lambda _sid, metadata_only=False: session)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda requested_model, requested_provider: (requested_model or "deepseek-v4-pro", requested_provider, False),
    )
    monkeypatch.setattr(routes, "_start_chat_stream_for_session", fake_start_stream)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {
            "session_id": "sid-stream",
            "run_id": run["run_id"],
            "answers": {
                "topic": "本地优先 AI 助理",
                "audience": "企业管理者",
                "boundary": "不要夸大能力",
            },
        },
    )

    response = routes.handle_post(object(), urlparse("/api/expert-teams/answer"))

    assert response["stream_id"] == "stream-real-1"
    assert response["run"]["execution_stream_id"] == "stream-real-1"
    assert response["run"]["execution_status"] == "running"
    assert calls["session"] is session
    assert calls["workspace"] == str(tmp_path)
    assert calls["model"] == "deepseek-v4-pro"
    assert calls["display_msg"].startswith("专家团开始生成：")
    assert "本地优先 AI 助理" in calls["msg"]
    assert calls["msg"] != calls["display_msg"]
    assert "需求确认" not in calls["display_msg"]
    assert "内部" not in calls["display_msg"]
    assert response["run"]["view"]["actions"]["can_cancel"] is True
    assert response["run"]["view"]["health"]["active_stream_id"] == "stream-real-1"
    assert response["run"]["view"]["health"]["needs_resume"] is False
    assert response["run"]["view"]["phase_progress"]["total"] == 4
    assert response["run"]["view"]["pending_questions"] == []


def test_content_expert_team_answer_waits_until_required_questions_are_done(monkeypatch, tmp_path):
    import api.routes as routes
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-wait", "team_id": "content-creator-team", "prompt": "帮我写一篇公众号长文"},
    )
    called = {"stream": False}

    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kwargs: payload)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "_start_chat_stream_for_session", lambda *args, **kwargs: called.update(stream=True))
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {
            "session_id": "sid-wait",
            "run_id": run["run_id"],
            "answers": {"topic": "本地优先 AI 助理"},
        },
    )

    response = routes.handle_post(object(), urlparse("/api/expert-teams/answer"))

    assert called["stream"] is False
    assert response["run"]["status"] == "awaiting_user"
    assert "stream_id" not in response


def test_expert_team_run_marks_legacy_running_without_stream_as_needs_resume(monkeypatch, tmp_path):
    import api.routes as routes
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-stale", "team_id": "content-creator-team", "prompt": "帮我写一篇公众号长文"},
    )
    stale = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "topic": "本地优先 AI 助理",
                "audience": "企业管理者",
                "boundary": "不要夸大能力",
            },
        },
    )
    assert stale["status"] == "running"

    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kwargs: payload)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "_active_stream_id_set", lambda: set())

    response = routes.handle_get(object(), urlparse("/api/expert-teams/run?session_id=sid-stale"))

    assert response["run"]["status"] == "awaiting_user"
    assert response["run"]["status_label"] == "等待继续"
    assert response["run"]["execution_status"] == "needs_resume"
    assert response["run"]["needs_resume"] is True
    assert response["run"]["tasks"][0]["status"] == "waiting_user"
    assert response["run"]["view"]["actions"]["can_resume"] is True
    assert response["run"]["view"]["actions"]["can_cancel"] is False
    assert response["run"]["view"]["health"]["needs_resume"] is True
    assert response["run"]["view"]["health"]["last_error"] == "执行流未启动或已中断"


def test_expert_team_resume_starts_legacy_stale_run_on_explicit_action(monkeypatch, tmp_path):
    import api.routes as routes
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-resume", "team_id": "content-creator-team", "prompt": "帮我写一篇公众号长文"},
    )
    stale = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "topic": "本地优先 AI 助理",
                "audience": "企业管理者",
                "boundary": "不要夸大能力",
            },
        },
    )
    session = SimpleNamespace(
        session_id="sid-resume",
        workspace=str(tmp_path),
        model="deepseek-v4-pro",
        model_provider=None,
        messages=[],
        context_messages=[],
        active_stream_id=None,
        pending_user_message=None,
        pending_attachments=None,
        pending_started_at=None,
        title="Untitled",
        save=lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kwargs: payload)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "get_session", lambda _sid, metadata_only=False: session)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda requested_model, requested_provider: (requested_model or "deepseek-v4-pro", requested_provider, False),
    )
    monkeypatch.setattr(
        routes,
        "_start_chat_stream_for_session",
        lambda s, **kwargs: {
            "stream_id": "stream-resumed",
            "session_id": s.session_id,
            "pending_started_at": 1781346100.0,
            "title": "专家团任务",
        },
    )
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {"session_id": "sid-resume", "run_id": stale["run_id"]},
    )

    response = routes.handle_post(object(), urlparse("/api/expert-teams/resume"))

    assert response["stream_id"] == "stream-resumed"
    assert response["run"]["status"] == "running"
    assert response["run"]["execution_status"] == "running"
    assert response["run"]["execution_stream_id"] == "stream-resumed"
    assert response["run"]["view"]["actions"]["can_cancel"] is True
    assert response["run"]["view"]["health"]["active_stream_id"] == "stream-resumed"


def test_expert_team_resume_retries_error_run_on_explicit_action(monkeypatch, tmp_path):
    import api.routes as routes
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-retry", "team_id": "content-creator-team", "prompt": "帮我写一篇公众号长文"},
    )
    answered = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "topic": "本地优先 AI 助理",
                "audience": "企业管理者",
                "boundary": "不要夸大能力",
            },
        },
    )
    started = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        answered["run_id"],
        {"stream_id": "stream-empty", "session_id": "sid-retry"},
    )
    failed = expert_teams.mark_content_expert_team_execution_complete(tmp_path, started["run_id"])
    session = SimpleNamespace(
        session_id="sid-retry",
        workspace=str(tmp_path),
        model="deepseek-v4-pro",
        model_provider=None,
        messages=[],
        context_messages=[],
        active_stream_id=None,
        pending_user_message=None,
        pending_attachments=None,
        pending_started_at=None,
        title="Untitled",
        save=lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kwargs: payload)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "get_session", lambda _sid, metadata_only=False: session)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda requested_model, requested_provider: (requested_model or "deepseek-v4-pro", requested_provider, False),
    )
    monkeypatch.setattr(
        routes,
        "_start_chat_stream_for_session",
        lambda s, **kwargs: {
            "stream_id": "stream-retry",
            "session_id": s.session_id,
            "pending_started_at": 1781346200.0,
            "title": "专家团任务",
        },
    )
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {"session_id": "sid-retry", "run_id": failed["run_id"]},
    )

    response = routes.handle_post(object(), urlparse("/api/expert-teams/resume"))

    assert response["stream_id"] == "stream-retry"
    assert response["run"]["status"] == "running"
    assert response["run"]["execution_status"] == "running"
    assert response["run"]["tasks"][0]["status"] == "running"


def test_expert_team_start_returns_view_contract_for_pending_questions(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-view", "team_id": "content-creator-team", "prompt": "帮我写一篇公众号长文"},
    )

    view = run["view"]
    assert view["status"] == "awaiting_user"
    assert view["execution_status"] == "idle"
    assert view["phase_progress"] == {"done": 0, "total": 4, "current": "需求确认"}
    assert [question["id"] for question in view["pending_questions"]] == ["topic", "audience", "boundary"]
    assert view["actions"] == {
        "can_answer": True,
        "can_resume": False,
        "can_cancel": False,
        "can_retry": False,
        "can_open_artifact": False,
    }
    assert view["health"] == {
        "needs_resume": False,
        "active_stream_id": "",
        "last_error": "",
    }
    assert all(item["openable"] is False for item in view["artifacts"])


def test_content_expert_team_completion_requires_real_delivery_evidence(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-evidence", "team_id": "content-creator-team", "prompt": "帮我写一篇公众号长文"},
    )
    answered = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "topic": "本地优先 AI 助理",
                "audience": "企业管理者",
                "boundary": "不要夸大能力",
            },
        },
    )
    started = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        answered["run_id"],
        {"stream_id": "stream-without-result", "session_id": "sid-evidence"},
    )

    updated = expert_teams.mark_content_expert_team_execution_complete(tmp_path, started["run_id"])

    assert updated["status"] == "error"
    assert updated["execution_status"] == "error"
    assert updated["tasks"][0]["status"] == "error"
    assert updated["tasks"][1]["status"] == "pending"
    assert updated["view"]["actions"]["can_retry"] is True
    assert updated["view"]["health"]["last_error"] == "未检测到可交付结果"


def test_content_expert_team_completion_marks_done_when_chat_delivery_exists(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-delivery", "team_id": "content-creator-team", "prompt": "帮我写一篇公众号长文"},
    )
    answered = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "topic": "本地优先 AI 助理",
                "audience": "企业管理者",
                "boundary": "不要夸大能力",
            },
        },
    )
    started = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        answered["run_id"],
        {"stream_id": "stream-with-result", "session_id": "sid-delivery"},
    )

    updated = expert_teams.mark_content_expert_team_execution_complete(
        tmp_path,
        started["run_id"],
        delivery={"kind": "chat", "label": "专家团生成结果", "exists": True},
    )

    assert updated["status"] == "done"
    assert updated["execution_status"] == "done"
    assert [task["status"] for task in updated["tasks"]] == ["done", "done"]
    assert updated["artifacts"][0]["kind"] == "chat"
    assert updated["artifacts"][0]["openable"] is False
    assert updated["view"]["actions"]["can_open_artifact"] is False


def test_expert_team_cancel_signals_active_execution_stream(monkeypatch, tmp_path):
    import api.routes as routes
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-cancel", "team_id": "content-creator-team", "prompt": "帮我写一篇公众号长文"},
    )
    answered = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "topic": "本地优先 AI 助理",
                "audience": "企业管理者",
                "boundary": "不要夸大能力",
            },
        },
    )
    started = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        answered["run_id"],
        {"stream_id": "stream-cancel", "session_id": "sid-cancel"},
    )
    cancelled = {}

    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kwargs: payload)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "cancel_stream", lambda stream_id: cancelled.setdefault("stream_id", stream_id) or True)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {"session_id": "sid-cancel", "run_id": started["run_id"]},
    )

    response = routes.handle_post(object(), urlparse("/api/expert-teams/cancel"))

    assert cancelled["stream_id"] == "stream-cancel"
    assert response["cancelled_stream"] is True
    assert response["run"]["status"] == "cancelled"
    assert response["run"]["execution_status"] == "cancelled"
    assert response["run"]["view"]["health"]["active_stream_id"] == ""


def test_expert_team_start_rejects_invalid_existing_session_workspace(monkeypatch):
    import api.routes as routes

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "get_session",
        lambda _sid, metadata_only=False: SimpleNamespace(workspace="/etc"),
    )
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {
            "session_id": "badws",
            "team_id": "ai-content-creator-brand-moodboard",
            "prompt": "帮我确定新产品的品牌视觉方向，做一个情绪板",
        },
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: {"status": status, "error": message},
    )

    response = routes.handle_post(object(), urlparse("/api/expert-teams/start"))

    assert response["status"] == 400
    assert "Failed to start expert team" in response["error"]
