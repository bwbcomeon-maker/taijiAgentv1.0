import json
from types import SimpleNamespace
from urllib.parse import urlparse


def test_expert_team_catalog_only_exposes_public_content_and_research_teams():
    from api import expert_teams

    data = expert_teams.expert_team_catalog()

    assert [team["id"] for team in data["teams"]] == ["content-creator-team", "deep-research-team"]
    public_json = json.dumps(data, ensure_ascii=False)
    for removed in ("style-modeler", "web-article-extractor", "ai-content-creator-brand-moodboard", "风格", "网页", "情绪板"):
        assert removed not in public_json
    assert "WorkBuddy" not in public_json
    assert "Hermes" not in public_json


def test_content_creator_catalog_uses_sgcc_daily_office_copy():
    from api import expert_teams

    data = expert_teams.expert_team_catalog()
    team = next(item for item in data["teams"] if item["id"] == "content-creator-team")

    assert team["description"] == (
        "面向国网业务部门日常办公材料编制，支持通知通报、工作汇报、会议纪要、宣传稿、"
        "方案说明、总结计划等内容，从需求确认、初稿撰写、打磨发布到交付确认分阶段协作。"
    )
    assert team["tags"] == ["工作汇报", "通知通报", "会议纪要", "总结计划", "宣传稿件", "方案说明"]
    assert [example["label"] for example in team["examples"]] == ["工作汇报", "会议纪要"]
    assert "迎峰度夏保供电重点工作推进情况" in team["examples"][0]["prompt"]
    assert "优化供电服务质效提升措施" in team["examples"][1]["prompt"]
    assert [question["title"] for question in team["questions"]] == [
        "这次要编制哪类办公材料，主题是什么？",
        "材料面向哪些对象，使用场景是什么？",
        "有哪些已知素材、口径要求、篇幅或表述边界？",
    ]


def test_deep_research_expert_team_start_persists_structured_run(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-deep",
            "team_id": "deep-research-team",
            "prompt": "研究本地优先 AI 助理的企业落地趋势",
        },
    )

    assert run["team_id"] == "deep-research-team"
    assert run["team_title"] == "深度文章研究团"
    assert run["status"] == "awaiting_user"
    assert run["phase"] == "需求确认"
    assert [member["name"] for member in run["members"]] == ["研究总导演", "资料研究员", "结构架构师", "撰稿专家", "审稿专家"]
    assert [question["id"] for question in run["questions"]] == ["research_topic", "audience_goal", "source_boundary"]
    assert [task["id"] for task in run["tasks"]] == ["direction", "research", "outline", "draft", "review"]
    assert [task["phase"] for task in run["tasks"]] == ["资料调研", "资料调研", "结构提纲", "正文初稿", "审稿交付"]
    assert run["view"]["phase_progress"] == {"done": 0, "total": 5, "current": "需求确认"}


def test_expert_team_start_rejects_unknown_non_empty_team_id(tmp_path):
    from api import expert_teams

    try:
        expert_teams.start_expert_team(tmp_path, {"session_id": "sid-bad", "team_id": "style-modeler"})
    except ValueError as exc:
        assert "Unknown expert team" in str(exc)
    else:
        raise AssertionError("unknown team_id should not fallback to content creator")


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


def test_deep_research_expert_team_answer_starts_real_stream(monkeypatch, tmp_path):
    import api.routes as routes
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-deep-stream",
            "team_id": "deep-research-team",
            "prompt": "研究本地优先 AI 助理的企业落地趋势",
        },
    )
    sent = {}
    calls = {}
    session = SimpleNamespace(
        session_id="sid-deep-stream",
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
            "stream_id": "stream-deep-real-1",
            "session_id": s.session_id,
            "pending_started_at": 1781346300.0,
            "title": "深度文章研究团任务",
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
            "session_id": "sid-deep-stream",
            "run_id": run["run_id"],
            "answers": {
                "research_topic": "本地优先 AI 助理如何在企业落地",
                "audience_goal": "企业管理者，用于内部决策参考",
                "source_boundary": "优先真实案例，不写泛泛趋势",
            },
        },
    )

    response = routes.handle_post(object(), urlparse("/api/expert-teams/answer"))

    assert response["stream_id"] == "stream-deep-real-1"
    assert response["run"]["team_id"] == "deep-research-team"
    assert response["run"]["execution_stream_id"] == "stream-deep-real-1"
    assert response["run"]["execution_status"] == "running"
    assert calls["session"] is session
    assert calls["workspace"] == str(tmp_path)
    assert calls["display_msg"].startswith("专家团开始生成：")
    assert "深度文章研究团" in calls["msg"]
    assert "本地优先 AI 助理如何在企业落地" in calls["msg"]
    assert "企业管理者，用于内部决策参考" in calls["msg"]
    assert "优先真实案例，不写泛泛趋势" in calls["msg"]
    assert calls["msg"] != calls["display_msg"]
    assert response["run"]["view"]["actions"]["can_cancel"] is True
    assert response["run"]["view"]["phase_progress"]["total"] == 5
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
        "can_approve_stage": False,
        "can_request_revision": False,
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


def test_content_expert_team_stage_completion_waits_for_user_review(tmp_path):
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

    assert updated["status"] == "awaiting_user"
    assert updated["execution_status"] == "done"
    assert updated["phase"] == "生成初稿"
    assert [task["status"] for task in updated["tasks"]] == ["waiting_user", "pending", "pending"]
    assert updated["current_stage"]["task_id"] == "draft"
    assert updated["current_stage"]["status"] == "awaiting_review"
    assert updated["stage_outputs"][0]["task_id"] == "draft"
    assert updated["stage_outputs"][0]["status"] == "awaiting_review"
    assert updated["stage_outputs"][0]["revision_count"] == 0
    assert updated["view"]["actions"]["can_approve_stage"] is True
    assert updated["view"]["actions"]["can_request_revision"] is True
    assert updated["view"]["stage_review"]["status"] == "awaiting_review"


def test_expert_team_stage_approve_starts_next_stage_and_final_approve_finishes(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-approve", "team_id": "content-creator-team", "prompt": "帮我写一篇公众号长文"},
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
        {"stream_id": "stream-draft", "session_id": "sid-approve"},
    )
    reviewed = expert_teams.mark_content_expert_team_execution_complete(
        tmp_path,
        started["run_id"],
        delivery={"kind": "chat", "label": "初稿阶段结果", "exists": True},
    )

    next_run = expert_teams.approve_expert_team_stage(tmp_path, reviewed["run_id"])

    assert next_run["status"] == "running"
    assert next_run["phase"] == "打磨发布"
    assert next_run["current_stage"]["task_id"] == "illustrations"
    assert [task["status"] for task in next_run["tasks"]] == ["done", "running", "pending"]
    assert next_run["stage_outputs"][0]["status"] == "approved"
    assert next_run["view"]["actions"]["can_approve_stage"] is False

    second_started = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        next_run["run_id"],
        {"stream_id": "stream-polish", "session_id": "sid-approve"},
    )
    second_reviewed = expert_teams.mark_content_expert_team_execution_complete(
        tmp_path,
        second_started["run_id"],
        delivery={"kind": "chat", "label": "打磨阶段结果", "exists": True},
    )
    delivery_run = expert_teams.approve_expert_team_stage(tmp_path, second_reviewed["run_id"])

    assert delivery_run["status"] == "running"
    assert delivery_run["phase"] == "交付"
    assert delivery_run["current_stage"]["task_id"] == "delivery"
    assert [task["status"] for task in delivery_run["tasks"]] == ["done", "done", "running"]

    final_started = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        delivery_run["run_id"],
        {"stream_id": "stream-delivery", "session_id": "sid-approve"},
    )
    final_reviewed = expert_teams.mark_content_expert_team_execution_complete(
        tmp_path,
        final_started["run_id"],
        delivery={"kind": "chat", "label": "交付确认阶段结果", "exists": True},
    )
    done = expert_teams.approve_expert_team_stage(tmp_path, final_reviewed["run_id"])

    assert done["status"] == "done"
    assert done["phase"] == "交付"
    assert done["execution_status"] == "done"
    assert [task["status"] for task in done["tasks"]] == ["done", "done", "done"]
    assert all(output["status"] == "approved" for output in done["stage_outputs"])
    assert done["artifacts"][0]["kind"] == "chat"
    assert done["view"]["actions"]["can_approve_stage"] is False


def test_expert_team_stage_revise_restarts_same_stage_with_feedback(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-revise", "team_id": "deep-research-team", "prompt": "研究本地优先 AI 助理"},
    )
    answered = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "research_topic": "本地优先 AI 助理如何在企业落地",
                "audience_goal": "企业管理者，用于内部决策参考",
                "source_boundary": "优先真实案例，不写泛泛趋势",
            },
        },
    )
    started = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        answered["run_id"],
        {"stream_id": "stream-direction", "session_id": "sid-revise"},
    )
    reviewed = expert_teams.mark_content_expert_team_execution_complete(
        tmp_path,
        started["run_id"],
        delivery={"kind": "chat", "label": "研究方向阶段结果", "exists": True},
    )

    revised = expert_teams.request_expert_team_stage_revision(
        tmp_path,
        reviewed["run_id"],
        "研究问题还太宽，请收窄到企业内网知识库场景。",
    )

    assert revised["status"] == "running"
    assert revised["phase"] == "资料调研"
    assert revised["current_stage"]["task_id"] == "direction"
    assert revised["current_stage"]["status"] == "revision_running"
    assert revised["current_stage"]["revision_count"] == 1
    assert revised["tasks"][0]["status"] == "running"
    assert revised["stage_outputs"][0]["status"] == "revision_running"
    assert revised["stage_outputs"][0]["revision_count"] == 1
    assert revised["stage_outputs"][0]["feedback_history"][-1]["feedback"] == "研究问题还太宽，请收窄到企业内网知识库场景。"


def test_expert_team_stage_approve_route_starts_next_stage_stream(monkeypatch, tmp_path):
    import api.routes as routes
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-stage-route", "team_id": "content-creator-team", "prompt": "帮我写一篇公众号长文"},
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
        {"stream_id": "stream-stage-route-1", "session_id": "sid-stage-route"},
    )
    reviewed = expert_teams.mark_content_expert_team_execution_complete(
        tmp_path,
        started["run_id"],
        delivery={"kind": "chat", "label": "初稿阶段结果", "exists": True},
    )
    session = SimpleNamespace(
        session_id="sid-stage-route",
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
    calls = {}

    def fake_start_stream(s, **kwargs):
        calls.update(kwargs)
        return {"stream_id": "stream-stage-route-2", "session_id": s.session_id}

    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kwargs: payload)
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
        lambda _handler: {"session_id": "sid-stage-route", "run_id": reviewed["run_id"]},
    )

    response = routes.handle_post(object(), urlparse("/api/expert-teams/stage/approve"))

    assert response["stream_id"] == "stream-stage-route-2"
    assert response["run"]["phase"] == "打磨发布"
    assert response["run"]["current_stage"]["task_id"] == "illustrations"
    assert response["run"]["execution_stream_id"] == "stream-stage-route-2"
    assert "打磨发布" in calls["msg"]
    assert "已确认的前置阶段产物" in calls["msg"]


def test_expert_team_stage_revise_route_starts_same_stage_stream_with_feedback(monkeypatch, tmp_path):
    import api.routes as routes
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-revise-route", "team_id": "deep-research-team", "prompt": "研究本地优先 AI 助理"},
    )
    answered = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "research_topic": "本地优先 AI 助理如何在企业落地",
                "audience_goal": "企业管理者，用于内部决策参考",
                "source_boundary": "优先真实案例，不写泛泛趋势",
            },
        },
    )
    started = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        answered["run_id"],
        {"stream_id": "stream-revise-route-1", "session_id": "sid-revise-route"},
    )
    reviewed = expert_teams.mark_content_expert_team_execution_complete(
        tmp_path,
        started["run_id"],
        delivery={"kind": "chat", "label": "研究方向阶段结果", "exists": True},
    )
    session = SimpleNamespace(
        session_id="sid-revise-route",
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
    calls = {}

    def fake_start_stream(s, **kwargs):
        calls.update(kwargs)
        return {"stream_id": "stream-revise-route-2", "session_id": s.session_id}

    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kwargs: payload)
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
            "session_id": "sid-revise-route",
            "run_id": reviewed["run_id"],
            "feedback": "研究问题还太宽，请收窄到企业内网知识库场景。",
        },
    )

    response = routes.handle_post(object(), urlparse("/api/expert-teams/stage/revise"))

    assert response["stream_id"] == "stream-revise-route-2"
    assert response["run"]["current_stage"]["task_id"] == "direction"
    assert response["run"]["current_stage"]["revision_count"] == 1
    assert response["run"]["execution_stream_id"] == "stream-revise-route-2"
    assert "研究问题还太宽，请收窄到企业内网知识库场景。" in calls["msg"]
    assert "请只重做当前阶段" in calls["msg"]


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
