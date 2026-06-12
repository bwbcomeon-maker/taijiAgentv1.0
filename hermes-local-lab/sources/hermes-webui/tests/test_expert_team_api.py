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
