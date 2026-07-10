import json
from types import SimpleNamespace
from urllib.parse import urlparse


def test_writeflow_status_without_state_file(tmp_path):
    import api.routes as routes

    data = routes._writeflow_public_status(tmp_path)

    assert data["ok"] is True
    assert data["state_exists"] is False
    assert data["state_error"] is None
    assert data["projects"] == []
    assert data["runs"] == []
    assert data["teams"][0]["id"] == "content-creator-team"
    assert data["teams"][0]["description"] == (
        "面向国网业务部门日常办公材料编制，支持通知通报、工作汇报、会议纪要、宣传稿、"
        "方案说明、总结计划等内容，从需求确认、初稿撰写、打磨发布到交付确认分阶段协作。"
    )
    assert data["teams"][0]["tags"] == ["工作汇报", "通知通报", "会议纪要", "总结计划", "宣传稿件", "方案说明"]
    assert [example["label"] for example in data["teams"][0]["examples"]] == [
        "工作汇报",
        "会议纪要",
        "通知通报",
        "方案说明",
        "总结计划",
        "材料润色",
    ]
    assert "迎峰度夏保供电重点工作推进情况" in data["teams"][0]["examples"][0]["prompt"]
    assert "优化供电服务质效提升措施" in data["teams"][0]["examples"][1]["prompt"]
    assert data["state_path"].endswith("articles/.writeflow/state.json")


def test_writeflow_image_generation_ready_uses_real_tool_readiness(monkeypatch):
    import api.routes as routes

    monkeypatch.setattr(
        "tools.image_generation_tool.get_image_generation_readiness",
        lambda: {
            "configured": True,
            "available": False,
            "reason_code": "authorization_required",
            "public_message": "图像生成未授权，请先在太极智能体中完成图像生成授权。",
        },
    )

    assert routes._writeflow_image_generation_ready() is False


def test_writeflow_status_reads_state_file(tmp_path):
    import api.routes as routes

    state_path = tmp_path / "articles" / ".writeflow" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_project": "demo",
                "projects": {
                    "demo": {
                        "name": "Demo",
                        "mode": "B",
                        "stage": "3",
                        "status": "waiting_user",
                        "artifacts": {"03_outline": "articles/demo/03_outline.md"},
                        "updated_at": "2026-05-30T22:00:00+08:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    data = routes._writeflow_public_status(tmp_path)

    assert data["ok"] is True
    assert data["state_exists"] is True
    assert data["active_project"] == "demo"
    assert len(data["projects"]) == 1
    project = data["projects"][0]
    assert project["slug"] == "demo"
    assert project["name"] == "Demo"
    assert project["mode"] == "B"
    assert project["stage"] == "3"
    assert project["status"] == "waiting_user"
    assert project["artifacts"] == {"03_outline": "articles/demo/03_outline.md"}
    assert project["display_phase"] == "确定方向"
    assert project["display_status"] == "等待确认"
    assert project["display_artifacts"] == {"结构提纲": "articles/demo/03_outline.md"}
    assert project["display_team"]["title"] == "内容创作专家团"
    assert project["display_members"][0]["status"] == "等待确认"
    assert project["updated_at"] == "2026-05-30T22:00:00+08:00"


def test_writeflow_status_reports_invalid_json(tmp_path):
    import api.routes as routes

    state_path = tmp_path / "articles" / ".writeflow" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not-json", encoding="utf-8")

    data = routes._writeflow_public_status(tmp_path)

    assert data["ok"] is False
    assert data["state_exists"] is True
    assert "写作状态文件 JSON 无效" in data["state_error"]
    assert data["projects"] == []


def test_writeflow_status_rejects_invalid_session_workspace(monkeypatch):
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_session",
        lambda _sid, metadata_only=False: SimpleNamespace(workspace="/etc"),
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: {"status": status, "error": message},
    )

    response = routes.handle_get(object(), urlparse("/api/writeflow/status?session_id=badws"))

    assert response["status"] == 400
    assert "Invalid writeflow workspace" in response["error"]


def test_writeflow_compose_generates_stable_messages(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)

    cases = {
        "start": "workflow-producer",
        "status": "workflow-producer",
        "next": "workflow-producer",
        "redo": "workflow-producer",
        "skip": "workflow-producer",
        "export": "workflow-producer",
        "style": "style-modeler",
        "extract": "web-article-extractor",
    }
    expected_run_teams = {
        "start": "content-creator-team",
        "style": "style-modeler",
        "extract": "web-article-extractor",
    }
    for action, skill in cases.items():
        body = {
            "session_id": "sid",
            "action": action,
            "project": "My Project",
            "mode": "B",
            "stage": "3",
            "prompt": "写一篇测试文章",
        }

        data = routes._writeflow_compose_message(body)

        assert data["ok"] is True
        assert data["action"] == action
        assert data["skill"] == skill
        assert data["project"] == "My-Project"
        if action in expected_run_teams:
            assert data["run_id"]
            run_path = tmp_path / "articles" / ".writeflow" / "runs" / f"{data['run_id']}.json"
            assert run_path.exists()
            run = json.loads(run_path.read_text(encoding="utf-8"))
            assert run["team_id"] == expected_run_teams[action]
        assert data["message"].startswith("请【")
        assert "你可以把这次协作理解成把一个选题交给一间小型内容工作室" in data["message"]
        assert "本轮要做：" in data["message"]
        assert str(tmp_path) not in data["message"]
        assert "确定方向" in data["message"]
        assert "生成初稿" in data["message"]
        assert "打磨发布" in data["message"]
        assert "系统处理原则" in data["message"]
        assert "文件保存位置、进度同步、成果物登记和下载入口由当前系统根据会话自动处理" in data["message"]
        if action == "style":
            assert "风格模型" in data["message"]
            assert "审稿报告" not in data["message"]
            assert "配图提示词" not in data["message"]
        elif action == "extract":
            assert "正文 Markdown" in data["message"]
            assert "素材整理" in data["message"]
            assert "审稿报告" not in data["message"]
        else:
            assert "材料初稿" in data["message"]
            assert "审稿报告" in data["message"]
            assert "配图提示词" in data["message"]
            assert "定稿材料" in data["message"]
        assert "每轮结束必须用中文返回" in data["message"]
        assert "不要把用户带进内部工具、文件路径和技术术语里" in data["message"]
        forbidden_tokens = [
            "articles/",
            "articles/.writeflow",
            "runs/[run_id]",
            "[project-slug]",
            "run_id",
            "team_id",
            "skill",
            "prompt",
            "skill_view",
            "delegate_task",
            "Workspace::v1",
            "write_file",
            "workdir",
            "tasks[]",
            "members[]",
            "artifacts[]",
            "file_changes[]",
            "events[]",
            "状态文件",
            "illustration_prompts.md",
            "image_generate",
            "workflow-producer",
            "baoyu-article-illustrator",
        ]
        for token in forbidden_tokens:
            assert token not in data["message"]


def test_writeflow_compose_accepts_chinese_action_alias(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)

    data = routes._writeflow_compose_message(
        {
            "session_id": "sid",
            "action": "继续",
            "project": "中文项目",
            "mode": "A",
            "prompt": "继续生成初稿",
        }
    )

    assert data["ok"] is True
    assert data["action"] == "next"
    assert data["project"] == "中文项目"
    assert "本轮要做：继续下一步" in data["message"]


def test_writeflow_compose_accepts_team_template_fields(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)

    data = routes._writeflow_compose_message(
        {
            "session_id": "sid",
            "action": "start",
            "project": "专家团项目",
            "mode": "B",
            "team_id": "deep-research-team",
            "template_id": "market-research",
            "example_prompt": "示例问法",
            "prompt": "最终用户需求",
        }
    )

    assert data["ok"] is True
    assert data["team_id"] == "deep-research-team"
    assert data["template_id"] == "market-research"
    assert data["display_team"]["title"] == "深度材料研究团"
    assert "请【深度材料研究团】接手这个写作任务。" in data["message"]
    assert "专家团成员分工：" in data["message"]
    assert "材料起草专家（材料初稿）" in data["message"]
    assert "复核专家（材料复核）" in data["message"]
    assert "材料起草专家正在写初稿" in data["message"]
    assert "复核专家正在做流转前检查" in data["message"]
    assert "第一版固定两项任务" not in data["message"]
    assert "生成封面和文中配图" not in data["message"]
    assert "market-research" not in data["message"]
    assert "示例问法" not in data["message"]
    assert "必须以本次需求为准" in data["message"]


def test_writeflow_start_creates_team_run_and_status_restores_it(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: False)

    data = routes._writeflow_compose_message(
        {
            "session_id": "sid",
            "action": "start",
            "project": "公众号长文",
            "mode": "A",
            "prompt": "写一篇公众号长文并配图",
        }
    )

    run_id = data["run_id"]
    run_path = tmp_path / "articles" / ".writeflow" / "runs" / f"{run_id}.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    assert run["team_id"] == "content-creator-team"
    assert run["project_slug"] == "公众号长文"
    assert [task["id"] for task in run["tasks"]] == ["direction", "draft", "illustrations", "review"]
    assert [task["id"] for task in run["display_tasks"]] == ["draft", "illustrations"]
    assert run["tasks"][0]["title"] == "确定写作方向"
    assert run["tasks"][0]["status"] == "running"
    assert run["display_tasks"][0]["title"] == "起草办公材料初稿"
    assert run["display_tasks"][0]["status"] == "running"
    assert run["display_tasks"][0]["status_label"] == "主编正在定方向"
    assert run["artifacts"] == []
    assert run["reference_artifacts"] == []
    assert run["tasks"][2]["status"] == "pending"

    status = routes._writeflow_public_status(tmp_path)
    assert status["runs"][0]["run_id"] == run_id
    assert status["runs"][0]["progress"] == {"done": 0, "total": 4}
    assert status["runs"][0]["display_progress"] == {"done": 0, "total": 2}


def test_writeflow_start_recovers_existing_active_team_run(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: False)

    existing = routes._writeflow_run_from_project(
        tmp_path,
        "same-project",
        {"name": "same-project", "status": "running"},
        session_id="sid",
        team_id="content-creator-team",
        run_id="wr-existing",
    )
    routes._writeflow_write_json(routes._writeflow_run_path(tmp_path, existing["run_id"]), existing)

    run = routes._writeflow_ensure_run(
        tmp_path,
        {"session_id": "sid", "action": "start", "project": "same-project", "mode": "A"},
        {"name": "same-project", "status": "running"},
    )

    assert run["run_id"] == "wr-existing"
    assert len(list((tmp_path / "articles" / ".writeflow" / "runs").glob("*.json"))) == 1


def test_writeflow_start_does_not_reuse_run_from_another_session(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: False)

    existing = routes._writeflow_run_from_project(
        tmp_path,
        "same-project",
        {"name": "same-project", "status": "running"},
        session_id="old-session",
        team_id="deep-research-team",
        run_id="wr-existing",
    )
    routes._writeflow_write_json(routes._writeflow_run_path(tmp_path, existing["run_id"]), existing)

    run = routes._writeflow_ensure_run(
        tmp_path,
        {"session_id": "new-session", "action": "start", "project": "same-project", "mode": "B", "team_id": "deep-research-team"},
        {"name": "same-project", "status": "running", "team_id": "deep-research-team"},
    )

    assert run["run_id"] != "wr-existing"
    assert run["session_id"] == "new-session"
    assert run["team_id"] == "deep-research-team"
    assert len(list((tmp_path / "articles" / ".writeflow" / "runs").glob("*.json"))) == 2


def test_writeflow_runs_endpoint_does_not_materialize_missing_session_run_by_default(monkeypatch, tmp_path):
    import api.routes as routes

    class Session:
        messages = [
            {
                "role": "user",
                "content": (
                    "请【深度文章研究团】接手这个写作任务。\n\n"
                    "本轮要做：开始写作\n"
                    "稿件名称：企业为什么需要本地 AI Agent 工作台\n\n"
                    "本次需求：\n"
                    "围绕「企业为什么需要本地 AI Agent 工作台」做一篇深度文章。"
                ),
            }
        ]

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "get_session", lambda _sid, metadata_only=False: Session())
    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: False)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, extra_headers=None: {"status": status, "payload": payload})

    response = routes.handle_get(object(), urlparse("/api/writeflow/runs?session_id=new-session"))

    assert response["payload"]["ok"] is True
    assert response["payload"]["session_run"] is None
    assert response["payload"]["recovered_session_run"] is False
    assert response["payload"]["runs"] == []


def test_writeflow_runs_endpoint_keeps_legacy_recover_query_read_only(monkeypatch, tmp_path):
    import api.routes as routes

    class Session:
        messages = [
            {
                "role": "user",
                "content": (
                    "请【深度文章研究团】接手这个写作任务。\n\n"
                    "本轮要做：开始写作\n"
                    "稿件名称：企业为什么需要本地 AI Agent 工作台\n\n"
                    "本次需求：\n"
                    "围绕「企业为什么需要本地 AI Agent 工作台」做一篇深度文章。"
                ),
            }
        ]

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "get_session", lambda _sid, metadata_only=False: Session())
    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: False)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, extra_headers=None: {"status": status, "payload": payload})

    response = routes.handle_get(object(), urlparse("/api/writeflow/runs?session_id=new-session&recover=1"))

    assert response["payload"]["ok"] is True
    assert response["payload"]["recovered_session_run"] is False
    assert response["payload"]["session_run"] is None
    assert response["payload"]["runs"] == []
    assert not routes._writeflow_runs_dir(tmp_path).exists()


def test_writeflow_artifacts_payload_returns_relative_paths(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: True)
    project_dir = tmp_path / "articles" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "draft_final.md").write_text("# 最终稿\n", encoding="utf-8")
    (project_dir / "cover.png").write_bytes(b"png")

    run = routes._writeflow_run_from_project(
        tmp_path,
        "demo",
        {
            "name": "Demo",
            "status": "done",
            "artifacts": {
                "draft_final": "articles/demo/draft_final.md",
                "image_cover": "articles/demo/cover.png",
            },
        },
        session_id="sid",
        run_id="wr-demo",
    )
    routes._writeflow_write_json(routes._writeflow_run_path(tmp_path, "wr-demo"), run)

    payload = routes._writeflow_artifacts_payload(tmp_path, "wr-demo")

    assert payload["ok"] is True
    assert {item["path"] for item in payload["artifacts"]} == {
        "articles/demo/draft_final.md",
        "articles/demo/cover.png",
    }
    assert all(not item["path"].startswith("/") for item in payload["artifacts"])
    assert {item["path"] for item in payload["file_changes"]} == {
        "articles/demo/draft_final.md",
        "articles/demo/cover.png",
    }


def test_writeflow_run_endpoints_return_runs_and_artifacts(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200: {"status": status, "payload": payload})
    monkeypatch.setattr(routes, "bad", lambda _handler, message, status=400: {"status": status, "error": message})

    run = routes._writeflow_run_from_project(
        tmp_path,
        "api-demo",
        {"name": "API Demo", "status": "waiting_user"},
        session_id="sid",
        run_id="wr-api",
    )
    routes._writeflow_write_json(routes._writeflow_run_path(tmp_path, "wr-api"), run)

    runs_response = routes.handle_get(object(), urlparse("/api/writeflow/runs?session_id=sid"))
    run_response = routes.handle_get(object(), urlparse("/api/writeflow/run?session_id=sid&run_id=wr-api"))
    session_run_response = routes.handle_get(object(), urlparse("/api/writeflow/run?session_id=sid"))
    artifacts_response = routes.handle_get(object(), urlparse("/api/writeflow/artifacts?session_id=sid&run_id=wr-api"))

    assert runs_response["payload"]["runs"][0]["run_id"] == "wr-api"
    assert runs_response["payload"]["session_run"]["run_id"] == "wr-api"
    assert run_response["payload"]["run"]["run_id"] == "wr-api"
    assert session_run_response["payload"]["run"]["run_id"] == "wr-api"
    assert artifacts_response["payload"]["run_id"] == "wr-api"


def test_writeflow_run_endpoint_returns_null_for_plain_session(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200: {"status": status, "payload": payload})

    response = routes.handle_get(object(), urlparse("/api/writeflow/run?session_id=plain-session"))

    assert response["status"] == 200
    assert response["payload"]["ok"] is True
    assert response["payload"]["run"] is None


def test_writeflow_run_endpoint_does_not_return_another_session_run(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200: {"status": status, "payload": payload})
    monkeypatch.setattr(routes, "bad", lambda _handler, message, status=400: {"status": status, "error": message})

    run = routes._writeflow_run_from_project(
        tmp_path,
        "api-demo",
        {"name": "API Demo", "status": "running"},
        session_id="sid-a",
        run_id="wr-api",
    )
    routes._writeflow_write_json(routes._writeflow_run_path(tmp_path, "wr-api"), run)

    response = routes.handle_get(object(), urlparse("/api/writeflow/run?session_id=sid-b&run_id=wr-api"))

    assert response["status"] == 404
    assert "does not belong" in response["error"]


def test_writeflow_missing_artifact_does_not_complete_task(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: True)

    run = routes._writeflow_run_from_project(
        tmp_path,
        "missing-artifact",
        {
            "name": "Missing Artifact",
            "status": "running",
            "artifacts": {"draft_v1": "articles/missing-artifact/draft_v1.md"},
        },
        session_id="sid",
        run_id="wr-missing",
    )
    routes._writeflow_write_json(routes._writeflow_run_path(tmp_path, "wr-missing"), run)

    refreshed = routes._writeflow_list_runs(tmp_path)[0]

    draft_task = next(task for task in refreshed["tasks"] if task["id"] == "draft")
    visible_draft = next(task for task in refreshed["display_tasks"] if task["id"] == "draft")
    assert draft_task["status"] != "done"
    assert visible_draft["status"] != "done"
    assert refreshed["artifacts"] == []


def test_writeflow_sidebar_titles_show_team_and_topic(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value: tmp_path)

    run = routes._writeflow_run_from_project(
        tmp_path,
        "ai-tech-writing",
        {"name": "帮我写一篇ai 技术科普文章。", "status": "running"},
        session_id="sid",
        team_id="content-creator-team",
        run_id="wr-sidebar",
    )
    routes._writeflow_write_json(routes._writeflow_run_path(tmp_path, "wr-sidebar"), run)

    rows = routes._writeflow_enrich_sidebar_sessions(
        [
            {
                "session_id": "sid",
                "workspace": str(tmp_path),
                "title": "请【内容创作专家团】接手这个写作任务。\n\n你可以把这次协作理解成把一个选题交给一间小型内容工作室：",
            },
            {
                "session_id": "manual",
                "workspace": str(tmp_path),
                "title": "用户手动命名的会话",
            },
        ]
    )

    assert rows[0]["display_title"] == "内容创作｜AI 技术科普文章"
    assert rows[0]["writeflow_team_id"] == "content-creator-team"
    assert rows[0]["writeflow_title"] == "帮我写一篇ai 技术科普文章。"
    assert "display_title" not in rows[1]


def test_writeflow_sidebar_title_falls_back_to_session_prompt(monkeypatch, tmp_path):
    import api.routes as routes

    class Session:
        messages = [
            {
                "role": "user",
                "content": (
                    "请【内容创作专家团】接手这个写作任务。\n\n"
                    "稿件名称：帮我写一篇公众号长文，主题是本地 AI Agent\n\n"
                    "本次需求：\n"
                    "帮我写一篇公众号长文，主题是「本地 AI Agent 如何把写作流程变成可控工作台」。"
                    "目标读者是独立开发者和企业技术负责人。\n\n"
                    "执行团队备忘：\n"
                    "- 专家团：内容创作专家团；team_id: `content-creator-team`；run_id: `wr-old`。\n"
                ),
            }
        ]

    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value: tmp_path)
    monkeypatch.setattr(routes, "get_session", lambda sid: Session())

    rows = routes._writeflow_enrich_sidebar_sessions(
        [
            {
                "session_id": "sid",
                "workspace": str(tmp_path),
                "title": "请【内容创作专家团】接手这个写作任务。",
            }
        ]
    )

    assert rows[0]["display_title"] == "内容创作｜本地 AI Agent 如何把写作流程变成可控工作台"


def test_writeflow_image_provider_missing_uses_prompt_placeholder(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: False)

    run = routes._writeflow_run_from_project(
        tmp_path,
        "no-image-provider",
        {"name": "No Image Provider", "status": "running", "artifacts": {"draft_v1": "articles/no-image-provider/draft_v1.md"}},
        run_id="wr-no-image",
    )

    image_task = next(task for task in run["tasks"] if task["id"] == "illustrations")
    assert all(item["id"] != "illustration_prompts" for item in run["artifacts"])
    assert image_task["status"] == "pending"
    assert image_task["artifacts"] == []
    assert any(event["type"] == "image_provider_missing" for event in run["events"])


def test_legacy_writeflow_style_run_does_not_scan_new_style_files(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)

    data = routes._writeflow_compose_message(
        {
            "session_id": "style-sid",
            "action": "style",
            "project": "专业实战风格",
            "prompt": "请把当前文章改成我常用的专业实战风格。",
        }
    )

    run_path = tmp_path / "articles" / ".writeflow" / "runs" / f"{data['run_id']}.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    assert run["team_id"] == "style-modeler"
    assert [task["id"] for task in run["tasks"]] == ["style_input", "style_model", "style_apply"]
    assert run["tasks"][0]["status"] == "running"
    before = run_path.read_bytes()

    style_dir = tmp_path / "articles" / "_styles"
    style_dir.mkdir(parents=True)
    (style_dir / "professional-practice.md").write_text("# 专业实战风格模型\n", encoding="utf-8")

    hydrated = routes._writeflow_list_runs(tmp_path)[0]
    assert hydrated["read_only"] is True
    assert hydrated["status"] == run["status"]
    assert hydrated["tasks"] == run["tasks"]
    assert hydrated["artifacts"] == run["artifacts"]
    assert run_path.read_bytes() == before


def test_legacy_writeflow_extract_run_does_not_scan_new_markdown(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)

    data = routes._writeflow_compose_message(
        {
            "session_id": "extract-sid",
            "action": "extract",
            "project": "网页素材",
            "prompt": "请提取网页正文并整理成 Markdown。",
        }
    )

    run_path = tmp_path / "articles" / ".writeflow" / "runs" / f"{data['run_id']}.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    assert run["team_id"] == "web-article-extractor"
    assert [task["id"] for task in run["tasks"]] == ["parse", "extract", "organize"]
    assert run["tasks"][0]["status"] == "running"
    before = run_path.read_bytes()

    project_dir = tmp_path / "articles" / "网页素材"
    project_dir.mkdir(parents=True)
    (project_dir / "extracted_article.md").write_text("# 提取正文\n", encoding="utf-8")

    hydrated = routes._writeflow_list_runs(tmp_path)[0]
    assert hydrated["read_only"] is True
    assert hydrated["status"] == run["status"]
    assert hydrated["tasks"] == run["tasks"]
    assert hydrated["artifacts"] == run["artifacts"]
    assert run_path.read_bytes() == before


def test_writeflow_extract_ignores_unrelated_markdown(monkeypatch, tmp_path):
    import api.routes as routes

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)

    data = routes._writeflow_compose_message(
        {
            "session_id": "extract-random-md",
            "action": "extract",
            "project": "网页素材",
            "prompt": "请提取网页正文。",
        }
    )

    project_dir = tmp_path / "articles" / "网页素材"
    project_dir.mkdir(parents=True)
    (project_dir / "notes.md").write_text("# 普通笔记\n", encoding="utf-8")

    hydrated = routes._writeflow_list_runs(tmp_path)[0]
    tasks = {task["id"]: task for task in hydrated["tasks"]}
    paths = {item["path"] for item in hydrated["artifacts"]}

    assert data["run"]["team_id"] == "web-article-extractor"
    assert tasks["extract"]["status"] != "done"
    assert "articles/网页素材/notes.md" not in paths


def test_legacy_writeflow_list_does_not_materialize_session_artifacts(monkeypatch, tmp_path):
    import api.routes as routes

    external_articles = tmp_path / "agent-articles"
    draft_path = external_articles / "ai-tech-popular-science" / "draft_v1.md"
    draft_path.parent.mkdir(parents=True)
    draft_path.write_text("# 正文初稿\n\n测试内容", encoding="utf-8")

    class Session:
        messages = [
            {
                "role": "assistant",
                "content": "初稿已保存至 `articles/ai-tech-popular-science/draft_v1.md`，约 3000 字。",
            }
        ]
        tool_calls = []

    monkeypatch.setattr(routes, "get_session", lambda _sid: Session())
    monkeypatch.setattr(routes, "_writeflow_external_article_roots", lambda _workspace: [external_articles])
    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: False)

    run = routes._writeflow_run_from_project(
        tmp_path,
        "帮我写一篇ai-技术科普文章",
        {"name": "帮我写一篇ai 技术科普文章。", "status": "running"},
        session_id="sid",
        team_id="content-creator-team",
        run_id="wr-materialize",
    )
    run_path = routes._writeflow_run_path(tmp_path, "wr-materialize")
    routes._writeflow_write_json(run_path, run)
    before = run_path.read_bytes()

    hydrated = routes._writeflow_list_runs(tmp_path)[0]

    copied = tmp_path / "articles" / "ai-tech-popular-science" / "draft_v1.md"
    assert copied.exists() is False
    assert hydrated["read_only"] is True
    assert hydrated["artifacts"] == run["artifacts"]
    assert hydrated["reference_artifacts"] == run["reference_artifacts"]
    assert run_path.read_bytes() == before


def test_legacy_writeflow_run_does_not_copy_or_reconcile_external_artifacts(monkeypatch, tmp_path):
    import api.routes as routes

    external_articles = tmp_path / "agent-articles"
    project_dir = external_articles / "ai-tech-popular-science"
    project_dir.mkdir(parents=True)
    (project_dir / "draft_v1.md").write_text("# 正文初稿\n", encoding="utf-8")
    (project_dir / "review_report.md").write_text("# 审稿报告\n", encoding="utf-8")
    (project_dir / "illustration_prompts.md").write_text("# 配图提示词\n", encoding="utf-8")
    nested_prompt = project_dir / "imgs" / "prompts" / "01-cover.md"
    nested_prompt.parent.mkdir(parents=True)
    nested_prompt.write_text("# 封面提示词\n", encoding="utf-8")

    class Session:
        messages = [
            {
                "role": "assistant",
                "content": (
                    "终稿相关产物在 `articles/ai-tech-popular-science/` 下：\n"
                    "| 需要关注 | 路径 |\n"
                    "| --- | --- |\n"
                    "| 终稿正文 | draft_v2.md |\n"
                    "| 发布版 | export.md |\n"
                    "| 配图prompt | illustration_prompts.md |\n"
                    "| 审稿报告 | review_report.md |\n"
                ),
            }
        ]
        tool_calls = []

    monkeypatch.setattr(routes, "get_session", lambda _sid: Session())
    monkeypatch.setattr(routes, "_writeflow_external_article_roots", lambda _workspace: [external_articles])
    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: False)

    run = routes._writeflow_run_from_project(
        tmp_path,
        "帮我写一篇ai-技术科普文章",
        {"name": "AI 技术科普文章", "status": "running"},
        session_id="sid",
        team_id="content-creator-team",
        run_id="wr-reconcile",
    )
    run_path = routes._writeflow_run_path(tmp_path, "wr-reconcile")
    routes._writeflow_write_json(run_path, run)
    before = run_path.read_bytes()

    hydrated = routes._writeflow_list_runs(tmp_path)[0]
    assert hydrated["read_only"] is True
    assert hydrated["artifacts"] == run["artifacts"]
    assert hydrated["reference_artifacts"] == run["reference_artifacts"]
    assert not (tmp_path / "articles" / "ai-tech-popular-science" / "review_report.md").exists()
    assert not (tmp_path / "articles" / "ai-tech-popular-science" / "illustration_prompts.md").exists()
    assert not (tmp_path / "articles" / "ai-tech-popular-science" / "imgs" / "prompts" / "01-cover.md").exists()
    assert run_path.read_bytes() == before


def test_writeflow_deep_research_shows_researcher_when_research_starts(monkeypatch, tmp_path):
    import api.routes as routes

    class Session:
        messages = [
            {
                "role": "user",
                "content": (
                    "请【深度文章研究团】接手这个写作任务。\n"
                    "本次需求：帮我整理一篇关于 AI Agent 在内容生产、研发协作、资料管理里的落地案例文章。"
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "方向框架确认。\n"
                    "案例筛选四条标准：必须是公开报道过的真实企业部署案例。\n"
                    "下一步，资料研究员会按这个标准去搜索和整理真实案例。我现在启动调研。\n"
                    "Google 被拦截了，我换个方式搜索。"
                ),
            },
        ]
        tool_calls = []

    monkeypatch.setattr(routes, "get_session", lambda _sid: Session())

    run = routes._writeflow_run_from_project(
        tmp_path,
        "帮我整理一篇关于-AI-Agent-在内容生产-研发",
        {
            "name": "帮我整理一篇关于 AI Agent 在内容生产、研发",
            "status": "running",
            "team_id": "deep-research-team",
        },
        session_id="sid",
        team_id="deep-research-team",
        run_id="wr-deep-researching",
    )
    routes._writeflow_write_json(routes._writeflow_run_path(tmp_path, "wr-deep-researching"), run)

    hydrated = routes._writeflow_list_runs(tmp_path)[0]
    members = {item["id"]: item["status"] for item in hydrated["members"]}
    tasks = {item["id"]: item for item in hydrated["tasks"]}

    assert hydrated["phase"] == "确定方向"
    assert hydrated["status"] == "running"
    assert hydrated["progress"] == {"done": 1, "total": 5}
    assert members["workflow-producer"] == "已交接"
    assert members["research-expert"] == "执行中"
    assert members["outline-architect"] == "待命"
    assert members["writing-executor"] == "待命"
    assert members["editor-review"] == "待命"
    assert tasks["direction"]["status"] == "done"
    assert tasks["direction"]["artifacts"] == []
    assert tasks["research"]["status"] == "running"
    assert hydrated["artifacts"] == []


def test_legacy_writeflow_board_does_not_infer_state_from_session_or_files(monkeypatch, tmp_path):
    import api.routes as routes

    project_dir = tmp_path / "articles" / "qiye-bendi-ai-agent-workstation"
    project_dir.mkdir(parents=True)
    (project_dir / "01_theme.md").write_text("# 写作主题：企业为什么需要本地 AI Agent 工作台\n", encoding="utf-8")
    (project_dir / "02_cases.md").write_text("# 素材调研库：企业为什么需要本地 AI Agent 工作台\n", encoding="utf-8")
    (project_dir / "03_outline.md").write_text("# 文章大纲：企业为什么需要本地 AI Agent 工作台\n", encoding="utf-8")
    (project_dir / "draft_v1.md").write_text("# 正文初稿\n", encoding="utf-8")

    class Session:
        messages = [
            {
                "role": "user",
                "content": (
                    "请【深度文章研究团】接手这个写作任务。\n"
                    "稿件名称：围绕企业为什么需要本地 AI Agent 工作台做一\n"
                    "本次需求：\n围绕「企业为什么需要本地 AI Agent 工作台」做一篇深度文章。"
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "资料研究员已完成素材收集。素材已归集，16 个行业案例入库。"
                    "结构架构师完成大纲。"
                    "第一步「确定方向」已完成，等待进入第 2 步。"
                    "已产出的成果物：主题定义文件、素材调研库、文章大纲。"
                ),
            },
        ]
        tool_calls = []

    monkeypatch.setattr(routes, "get_session", lambda _sid: Session())
    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: False)

    run = routes._writeflow_run_from_project(
        tmp_path,
        "围绕企业为什么需要本地-AI-Agent-工作台做一",
        {"name": "围绕企业为什么需要本地 AI Agent 工作台做一", "status": "running", "team_id": "deep-research-team"},
        session_id="sid",
        team_id="deep-research-team",
        run_id="wr-deep",
    )
    run_path = routes._writeflow_run_path(tmp_path, "wr-deep")
    routes._writeflow_write_json(run_path, run)
    before = run_path.read_bytes()

    hydrated = routes._writeflow_list_runs(tmp_path)[0]

    assert hydrated["team_id"] == "deep-research-team"
    assert hydrated["read_only"] is True
    assert hydrated["status"] == run["status"]
    assert hydrated["phase"] == run["phase"]
    assert [task["status"] for task in hydrated["tasks"]] == [task["status"] for task in run["tasks"]]
    assert hydrated["artifacts"] == run["artifacts"]
    assert hydrated["reference_artifacts"] == run["reference_artifacts"]
    assert run_path.read_bytes() == before


def test_legacy_writeflow_does_not_advance_from_draft_text_and_file(monkeypatch, tmp_path):
    import api.routes as routes

    class Session:
        messages = [
            {
                "role": "user",
                "content": "本次需求：\n围绕「企业为什么需要本地 AI Agent 工作台」做一篇深度文章。",
            },
            {
                "role": "assistant",
                "content": (
                    "第一步「确定方向」已完成。资料研究员已完成素材收集，素材已归集，16 个行业案例入库。"
                    "结构架构师完成大纲。"
                    "第 2 步「生成初稿」完成，正文初稿已完成，等待进入第 3 步。"
                ),
            },
        ]
        tool_calls = []

    monkeypatch.setattr(routes, "get_session", lambda _sid: Session())

    run = routes._writeflow_run_from_project(
        tmp_path,
        "围绕企业为什么需要本地-AI-Agent-工作台做一",
        {"name": "围绕企业为什么需要本地 AI Agent 工作台做一", "status": "running", "team_id": "deep-research-team"},
        session_id="sid",
        team_id="deep-research-team",
        run_id="wr-deep-draft",
    )
    project_dir = tmp_path / run["artifact_root"]
    project_dir.mkdir(parents=True)
    (project_dir / "01_theme.md").write_text("# 写作主题：企业为什么需要本地 AI Agent 工作台\n", encoding="utf-8")
    (project_dir / "02_cases.md").write_text("# 素材调研库：企业为什么需要本地 AI Agent 工作台\n", encoding="utf-8")
    (project_dir / "03_outline.md").write_text("# 文章大纲：企业为什么需要本地 AI Agent 工作台\n", encoding="utf-8")
    (project_dir / "draft_v1.md").write_text("# 正文初稿：企业为什么需要本地 AI Agent 工作台\n", encoding="utf-8")
    run_path = routes._writeflow_run_path(tmp_path, "wr-deep-draft")
    routes._writeflow_write_json(run_path, run)
    before = run_path.read_bytes()

    hydrated = routes._writeflow_list_runs(tmp_path)[0]

    assert hydrated["read_only"] is True
    assert hydrated["status"] == run["status"]
    assert hydrated["phase"] == run["phase"]
    assert [task["status"] for task in hydrated["tasks"]] == [task["status"] for task in run["tasks"]]
    assert hydrated["artifacts"] == run["artifacts"]
    assert run_path.read_bytes() == before


def test_legacy_writeflow_draft_role_does_not_start_from_session_text(monkeypatch, tmp_path):
    import api.routes as routes

    class Session:
        messages = [
            {
                "role": "assistant",
                "content": (
                    "第一步「确定方向」已完成。"
                    "资料研究员已完成素材收集，素材已归集。"
                    "结构架构师完成大纲。"
                    "撰稿专家开始工作，启动初稿写作。"
                ),
            },
        ]
        tool_calls = []

    monkeypatch.setattr(routes, "get_session", lambda _sid: Session())

    run = routes._writeflow_run_from_project(
        tmp_path,
        "围绕企业为什么需要本地-AI-Agent-工作台做一",
        {"name": "围绕企业为什么需要本地 AI Agent 工作台做一", "status": "running", "team_id": "deep-research-team"},
        session_id="sid",
        team_id="deep-research-team",
        run_id="wr-deep-drafting",
    )
    run_path = routes._writeflow_run_path(tmp_path, "wr-deep-drafting")
    routes._writeflow_write_json(run_path, run)
    before = run_path.read_bytes()

    hydrated = routes._writeflow_list_runs(tmp_path)[0]

    assert hydrated["read_only"] is True
    assert hydrated["status"] == run["status"]
    assert hydrated["phase"] == run["phase"]
    assert [task["status"] for task in hydrated["tasks"]] == [task["status"] for task in run["tasks"]]
    assert hydrated["artifacts"] == run["artifacts"]
    assert run_path.read_bytes() == before


def test_legacy_writeflow_review_role_does_not_start_from_session_text(monkeypatch, tmp_path):
    import api.routes as routes

    class Session:
        messages = [
            {
                "role": "assistant",
                "content": (
                    "第一步「确定方向」已完成。"
                    "资料研究员已完成素材收集，素材已归集。"
                    "结构架构师完成大纲。"
                    "正文初稿已完成。"
                    "审稿专家接手。先加载审稿规范。"
                ),
            },
        ]
        tool_calls = []

    monkeypatch.setattr(routes, "get_session", lambda _sid: Session())

    run = routes._writeflow_run_from_project(
        tmp_path,
        "围绕企业为什么需要本地-AI-Agent-工作台做一",
        {"name": "围绕企业为什么需要本地 AI Agent 工作台做一", "status": "running", "team_id": "deep-research-team"},
        session_id="sid",
        team_id="deep-research-team",
        run_id="wr-deep-reviewing",
    )
    run_path = routes._writeflow_run_path(tmp_path, "wr-deep-reviewing")
    routes._writeflow_write_json(run_path, run)
    before = run_path.read_bytes()

    hydrated = routes._writeflow_list_runs(tmp_path)[0]

    assert hydrated["read_only"] is True
    assert hydrated["status"] == run["status"]
    assert hydrated["phase"] == run["phase"]
    assert [task["status"] for task in hydrated["tasks"]] == [task["status"] for task in run["tasks"]]
    assert hydrated["artifacts"] == run["artifacts"]
    assert run_path.read_bytes() == before


def test_legacy_writeflow_does_not_recover_or_copy_workspace_root_artifacts(monkeypatch, tmp_path):
    import api.routes as routes

    class Session:
        messages = [
            {
                "role": "assistant",
                "content": (
                    "第一步「确定方向」已完成。"
                    "资料研究员已完成素材收集，素材已归集。"
                    "结构架构师完成大纲。"
                    "正文初稿已完成。"
                    "审稿完成。审稿报告已生成。发布版已生成并打开。"
                ),
            },
        ]
        tool_calls = []

    monkeypatch.setattr(routes, "get_session", lambda _sid: Session())

    run = routes._writeflow_run_from_project(
        tmp_path,
        "围绕国家电网关于配电网的配变终端安全分析及营销推广模",
        {"name": "围绕国家电网关于配电网的配变终端安全分析及营销推广模", "status": "running", "team_id": "deep-research-team"},
        session_id="sid",
        team_id="deep-research-team",
        run_id="wr-root-recovery",
    )
    run_path = routes._writeflow_run_path(tmp_path, "wr-root-recovery")
    routes._writeflow_write_json(run_path, run)
    before = run_path.read_bytes()
    (tmp_path / "TTU安全分析及营销推广_素材收集.md").write_text("# 素材收集\n", encoding="utf-8")
    (tmp_path / "配变终端营销推广与安全竞争力分析_初稿V1.docx").write_bytes(b"draft")
    (tmp_path / "配变终端营销推广文章_审稿报告.docx").write_bytes(b"review")
    (tmp_path / "配变终端营销推广与安全竞争力分析_发布版V2.docx").write_bytes(b"export")

    hydrated = routes._writeflow_list_runs(tmp_path)[0]
    assert hydrated["read_only"] is True
    assert hydrated["phase"] == run["phase"]
    assert hydrated["artifacts"] == run["artifacts"]
    assert not (tmp_path / run["artifact_root"] / "draft_v1.docx").exists()
    assert not (tmp_path / run["artifact_root"] / "review_report.docx").exists()
    assert not (tmp_path / run["artifact_root"] / "export.docx").exists()
    assert run_path.read_bytes() == before


def test_legacy_writeflow_file_write_never_routes_or_registers_artifact(monkeypatch, tmp_path):
    import api.routes as routes

    class Session:
        messages = [
            {
                "role": "assistant",
                "content": (
                    "请【深度文章研究团】接手这个写作任务。\n"
                    "稿件名称：国家电网配变终端文章\n"
                    "资料研究员已完成，素材已归集。"
                    "结构架构师完成大纲，文章大纲已就位。"
                    "撰稿专家已完成初稿，正文初稿已完成。"
                ),
            },
        ]
        tool_calls = []

    monkeypatch.setattr(routes, "get_session", lambda _sid, **_kwargs: Session())

    run = routes._writeflow_run_from_project(
        tmp_path,
        "国家电网配变终端文章",
        {"name": "国家电网配变终端文章", "status": "running", "team_id": "deep-research-team"},
        session_id="sid-current",
        team_id="deep-research-team",
        run_id="wr-file-route",
    )
    run_path = routes._writeflow_run_path(tmp_path, "wr-file-route")
    routes._writeflow_write_json(run_path, run)
    before = run_path.read_bytes()

    routing = routes._writeflow_artifact_target_for_file_write(
        tmp_path,
        "sid-current",
        "配变终端营销推广与安全竞争力分析_初稿V1.docx",
    )

    assert routing is None
    assert run_path.read_bytes() == before


def test_writeflow_file_write_does_not_route_plain_workspace_file(monkeypatch, tmp_path):
    import api.routes as routes

    class Session:
        messages = [{"role": "user", "content": "请【内容创作专家团】接手这个写作任务。\n稿件名称：普通测试"}]
        tool_calls = []

    monkeypatch.setattr(routes, "get_session", lambda _sid, **_kwargs: Session())
    run = routes._writeflow_run_from_project(
        tmp_path,
        "普通测试",
        {"name": "普通测试", "status": "running", "team_id": "content-creator-team"},
        session_id="sid-plain",
        team_id="content-creator-team",
        run_id="wr-plain",
    )
    routes._writeflow_write_json(routes._writeflow_run_path(tmp_path, "wr-plain"), run)

    assert routes._writeflow_artifact_target_for_file_write(tmp_path, "sid-plain", "notes.md") is None
    assert routes._writeflow_artifact_target_for_file_write(tmp_path, "sid-plain", "tmp/random.txt") is None


def test_legacy_writeflow_tool_env_only_exposes_workspace(monkeypatch, tmp_path):
    import api.routes as routes

    class Session:
        messages = [{"role": "user", "content": "请【内容创作专家团】接手这个写作任务。\n稿件名称：环境变量测试"}]
        tool_calls = []

    monkeypatch.setattr(routes, "get_session", lambda _sid, **_kwargs: Session())
    run = routes._writeflow_run_from_project(
        tmp_path,
        "环境变量测试",
        {"name": "环境变量测试", "status": "running", "team_id": "content-creator-team"},
        session_id="sid-env",
        team_id="content-creator-team",
        run_id="wr-env",
    )
    routes._writeflow_write_json(routes._writeflow_run_path(tmp_path, "wr-env"), run)

    env = routes._writeflow_tool_env_for_session(tmp_path, "sid-env")

    assert env == {"HERMES_WORKSPACE": str(tmp_path.resolve())}


def test_skills_list_includes_external_writeflow_skills(monkeypatch, tmp_path):
    import api.routes as routes

    primary = tmp_path / "skills"
    external = tmp_path / "custom-skills"
    primary.mkdir()
    for name in ("workflow-producer", "style-modeler", "web-article-extractor"):
        skill_dir = external / "writing-agent" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} test skill\n---\n# {name}\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        routes,
        "_active_skill_search_dirs",
        lambda skills_dir: [skills_dir, external],
    )

    data = routes._skills_list_from_dir(primary)
    names = {skill["name"] for skill in data["skills"]}

    assert {"workflow-producer", "style-modeler", "web-article-extractor"} <= names
