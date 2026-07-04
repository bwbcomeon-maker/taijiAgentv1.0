from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]


def _patch_route_json(monkeypatch, tmp_path):
    from api import routes

    monkeypatch.setattr(routes, "get_session_for_file_ops", lambda session_id: SimpleNamespace(workspace=str(tmp_path)))
    monkeypatch.setattr(routes, "j", lambda handler, payload, status=200, **kwargs: {"status": status, "payload": payload})
    monkeypatch.setattr(routes, "bad", lambda handler, message, status=400, **kwargs: {"status": status, "payload": {"error": str(message)}})
    return routes


def test_docx_engine_v2_routes_are_registered_in_router():
    routes_py = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")

    assert "/api/docx-engine-v2/templates" in routes_py
    assert "/api/docx-engine-v2/jobs" in routes_py
    assert "/api/docx-engine-v2/assets/rerender" in routes_py
    assert "/api/docx-engine-v2/assets/replace" in routes_py
    assert "/api/file/open" in routes_py


def test_legacy_figure_adjustment_routes_do_not_keep_skill_script_runner():
    routes_py = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")

    assert "def _docx_template_run_script(" not in routes_py
    assert "docx-template-skill script not found" not in routes_py


def test_explicit_template_selection_returns_visible_workbench_payload():
    from api import routes

    result = routes._docx_template_invocation_result(
        "/docx-template-skill 请把当前成果套用通用方案模板（templateId: general-proposal）。"
    )

    assert result is not None
    assert result["docx_template_selected"] is True
    assert result["template_id"] == "general-proposal"
    assert result["template"]["id"] == "general-proposal"
    assert result["templates"]


def test_docx_template_non_streaming_turn_is_persisted_for_reload(monkeypatch):
    from api import routes

    saved = []
    published = []
    session = SimpleNamespace(
        session_id="sid-docx",
        title="Untitled",
        messages=[],
        context_messages=[],
        active_stream_id=None,
        pending_user_message=None,
        pending_attachments=[],
        pending_started_at=None,
        model="",
        model_provider=None,
        save=lambda: saved.append("saved"),
    )
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda reason: published.append(reason))
    monkeypatch.setattr(routes, "stamp_turn_duration_on_latest_assistant", lambda *args, **kwargs: None)

    result = routes._docx_template_invocation_result("套用通用方案模板")
    routes._record_docx_non_streaming_turn_for_session(session, "套用通用方案模板", result)

    assert saved == ["saved"]
    assert published == ["session_new"]
    assert session.active_stream_id is None
    assert session.pending_user_message is None
    assert session.messages[0]["role"] == "user"
    assert session.messages[0]["content"] == "套用通用方案模板"
    assert session.messages[1]["role"] == "assistant"
    assert session.messages[1]["docx_engine_workbench"]["template_id"] == "general-proposal"
    assert session.context_messages[-1]["content"] == "已选择模板，请在文档模板工作台生成 DOCX 交付包。"
    assert session.title != "Untitled"


def test_docx_engine_v2_lists_templates(monkeypatch, tmp_path):
    routes = _patch_route_json(monkeypatch, tmp_path)
    monkeypatch.setattr(
        routes.docx_engine_v2,
        "list_templates",
        lambda: {"ok": True, "templates": [{"id": "general-proposal"}]},
    )

    result = routes._handle_docx_engine_v2_templates(object())

    assert result["status"] == 200
    assert result["payload"]["templates"][0]["id"] == "general-proposal"


def test_docx_engine_v2_create_job_requires_template_selection(monkeypatch, tmp_path):
    routes = _patch_route_json(monkeypatch, tmp_path)

    def fake_create_job(payload, workspace):
        assert workspace == tmp_path.resolve()
        return {"ok": False, "code": "template_selection_required", "templates": [{"id": "general-proposal"}]}, 400

    monkeypatch.setattr(routes.docx_engine_v2, "create_job", fake_create_job)

    result = routes._handle_docx_engine_v2_create_job(
        object(),
        {"session_id": "sid-docx", "source_path": "source.md", "out_dir": "delivery"},
    )

    assert result["status"] == 400
    assert result["payload"]["code"] == "template_selection_required"


def test_docx_engine_v2_create_job_returns_delivery_package(monkeypatch, tmp_path):
    routes = _patch_route_json(monkeypatch, tmp_path)

    def fake_create_job(payload, workspace):
        assert payload["template_id"] == "general-proposal"
        assert workspace == tmp_path.resolve()
        return {
            "ok": True,
            "job_id": "job-001",
            "delivery_dir": str(tmp_path / "delivery"),
            "document_path": str(tmp_path / "delivery" / "document.docx"),
            "quality_status": "passed_with_warnings",
            "quality_report_path": str(tmp_path / "delivery" / "quality-report.json"),
            "quality_report": {
                "status": "passed_with_warnings",
                "checks": [{"id": "wps_visual", "status": "not_verified"}],
                "warnings": ["WPS visual inspection has not been performed."],
                "failures": [],
            },
        }, 200

    monkeypatch.setattr(routes.docx_engine_v2, "create_job", fake_create_job)

    result = routes._handle_docx_engine_v2_create_job(
        object(),
        {
            "session_id": "sid-docx",
            "template_id": "general-proposal",
            "source_path": "source.md",
            "out_dir": "delivery",
        },
    )

    assert result["status"] == 200
    payload = result["payload"]
    assert payload["document_path"].endswith("document.docx")
    assert payload["quality_status"] in {"passed", "passed_with_warnings"}
    assert payload["quality_report_path"].endswith("quality-report.json")
    assert payload["quality_report"]["checks"][0]["id"] == "wps_visual"


def test_docx_engine_v2_rerender_asset_routes_to_service(monkeypatch, tmp_path):
    routes = _patch_route_json(monkeypatch, tmp_path)

    def fake_rerender_asset(payload, workspace):
        assert payload["figure_id"] == "fig-001"
        assert workspace == tmp_path.resolve()
        return {
            "ok": True,
            "figure_id": "fig-001",
            "display_path": "assets/fig-001/figure.svg",
            "output_path": str(tmp_path / "delivery" / "assets" / "fig-001" / "figure.svg"),
        }, 200

    monkeypatch.setattr(routes.docx_engine_v2, "rerender_asset", fake_rerender_asset)

    result = routes._handle_docx_engine_v2_rerender_asset(
        object(),
        {"session_id": "sid-docx", "delivery_dir": "delivery", "figure_id": "fig-001"},
    )

    assert result["status"] == 200
    assert result["payload"]["display_path"].endswith("figure.svg")


def test_docx_engine_v2_replace_asset_rejects_bad_figure_id(tmp_path):
    from api import docx_engine_v2

    (tmp_path / "document.docx").write_bytes(b"docx")
    (tmp_path / "replacement.svg").write_text("<svg></svg>", encoding="utf-8")

    payload, status = docx_engine_v2.replace_asset(
        {
            "docx_path": "document.docx",
            "figure_id": "../bad",
            "image_path": "replacement.svg",
            "out_path": "updated.docx",
        },
        tmp_path,
    )

    assert status == 400
    assert payload["ok"] is False
    assert "figure_id" in payload["message"]


def test_docx_engine_v2_package_rich_draft_returns_delivery_assets(tmp_path):
    from api import docx_engine_v2

    source = tmp_path / "draft.md"
    out_dir = tmp_path / "draft-package"
    source.write_text(
        "\n".join(
            [
                "# 初稿",
                "",
                "| 项目 | 状态 |",
                "| --- | --- |",
                "| 文档模板渲染 | 进行中 |",
                "",
                "```mermaid",
                "flowchart LR",
                "  A[初稿] --> B[图示资产]",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload, status = docx_engine_v2.package_rich_draft(
        {"source_path": "draft.md", "out_dir": "draft-package"},
        tmp_path,
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["action"] == "package"
    assert payload["out_dir"] == "draft-package"
    assert (out_dir / "draft.manifest.json").exists()
    assert (out_dir / "图片清单.md").exists()


def test_legacy_figure_adjustment_package_delegates_to_v2(monkeypatch, tmp_path):
    routes = _patch_route_json(monkeypatch, tmp_path)
    (tmp_path / "source.md").write_text("# 初稿\n\n```mermaid\nflowchart LR\nA-->B\n```\n", encoding="utf-8")
    called = {}

    def fake_package_rich_draft(payload, workspace):
        called["workspace"] = workspace
        called["payload"] = payload
        return {"ok": True, "action": "package", "out_dir": "draft-package"}, 200

    monkeypatch.setattr(routes.docx_engine_v2, "package_rich_draft", fake_package_rich_draft)
    monkeypatch.setattr(
        routes,
        "_docx_template_run_script",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy skill script must not run")),
        raising=False,
    )

    result = routes._handle_docx_figure_adjustment_package(
        object(),
        {"session_id": "sid-docx", "source_path": "source.md", "out_dir": "draft-package"},
    )

    assert result["status"] == 200
    assert result["payload"]["action"] == "package"
    assert called["workspace"] == tmp_path.resolve()
    assert called["payload"]["source_path"] == "source.md"


def test_legacy_figure_adjustment_rerender_delegates_to_v2(monkeypatch, tmp_path):
    routes = _patch_route_json(monkeypatch, tmp_path)
    called = {}

    def fake_rerender_asset(payload, workspace):
        called["workspace"] = workspace
        called["payload"] = payload
        return {"ok": True, "figure_id": "fig-001", "display_path": "assets/fig-001/figure.svg"}, 200

    monkeypatch.setattr(routes.docx_engine_v2, "rerender_asset", fake_rerender_asset)
    monkeypatch.setattr(
        routes,
        "_docx_template_run_script",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy skill script must not run")),
        raising=False,
    )

    result = routes._handle_docx_figure_adjustment_rerender(
        object(),
        {"session_id": "sid-docx", "manifest_path": "draft.manifest.json", "figure_id": "fig-001"},
    )

    assert result["status"] == 200
    assert result["payload"]["action"] == "rerender"
    assert called["workspace"] == tmp_path.resolve()
    assert called["payload"]["figure_id"] == "fig-001"


def test_legacy_figure_adjustment_replace_delegates_to_v2(monkeypatch, tmp_path):
    routes = _patch_route_json(monkeypatch, tmp_path)
    called = {}

    def fake_replace_asset(payload, workspace):
        called["workspace"] = workspace
        called["payload"] = payload
        return {"ok": True, "figure_id": "fig-001", "output_path": str(tmp_path / "updated.docx")}, 200

    monkeypatch.setattr(routes.docx_engine_v2, "replace_asset", fake_replace_asset)
    monkeypatch.setattr(
        routes,
        "_docx_template_run_script",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy skill script must not run")),
        raising=False,
    )

    result = routes._handle_docx_figure_adjustment_replace(
        object(),
        {
            "session_id": "sid-docx",
            "docx_path": "delivery/document.docx",
            "figure_id": "fig-001",
            "image_path": "replacement.png",
            "out_path": "delivery/updated.docx",
        },
    )

    assert result["status"] == 200
    assert result["payload"]["action"] == "replace"
    assert called["workspace"] == tmp_path.resolve()
    assert called["payload"]["out_path"] == "delivery/updated.docx"
