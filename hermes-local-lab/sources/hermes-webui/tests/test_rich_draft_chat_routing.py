from types import SimpleNamespace


class FakeSession:
    def __init__(self, tmp_path):
        self.session_id = "sid-rich-chat"
        self.path = tmp_path / "sessions" / f"{self.session_id}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.model = "deepseek/deepseek-chat"
        self.model_provider = "deepseek"
        self.profile = None
        self.brand_privacy_tainted = False
        self.messages = []
        self.tool_calls = []
        self.context_messages = []
        self.title = "Untitled"
        self.active_stream_id = None
        self.pending_user_message = None
        self.pending_attachments = []
        self.pending_started_at = None
        self.saved = False

    def save(self, **_kwargs):
        self.saved = True


def _patch_chat_start_happy_path(monkeypatch, tmp_path, started):
    import api.runtime_adapter as runtime_adapter
    from api import routes

    def fake_start(session, **kwargs):
        started.append(kwargs)
        return {"stream_id": "stream-rich", "session_id": session.session_id, "pending_started_at": 123.0}

    monkeypatch.setattr(routes, "get_session", lambda session_id: FakeSession(tmp_path))
    monkeypatch.setattr(routes, "_resolve_chat_workspace_with_recovery", lambda session, workspace: str(tmp_path))
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda model, provider: (model, provider, False))
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(routes, "classify_brand_safety_prompt", lambda *args, **kwargs: SimpleNamespace(action="run"))
    monkeypatch.setattr(routes, "_start_chat_stream_for_session", fake_start)
    monkeypatch.setattr(runtime_adapter, "runtime_adapter_enabled", lambda: False)
    monkeypatch.setattr(runtime_adapter, "runtime_adapter_runner_enabled", lambda: False)
    monkeypatch.setattr(routes, "j", lambda handler, payload, status=200, **kwargs: {"status": status, "payload": payload})
    return routes


def test_plan_like_chat_prompt_is_enriched_for_rich_draft():
    from api import routes

    prompt = "请帮我编制一份提升营业厅服务质效专项行动方案"

    enriched = routes._enrich_plan_like_chat_prompt(prompt)

    assert prompt in enriched
    assert "富内容初稿" in enriched
    assert "至少 2 个 Markdown 表格" in enriched
    assert "至少 1 个架构图、流程图、用例图或图示引用" in enriched
    assert "模板套用" in enriched


def test_non_generation_chat_prompt_is_not_enriched():
    from api import routes

    prompt = "请解释一下方案这个词是什么意思"

    assert routes._enrich_plan_like_chat_prompt(prompt) == prompt


def test_rich_draft_prompt_enrichment_is_idempotent():
    from api import routes

    prompt = "请帮我编制一份提升营业厅服务质效专项行动方案"

    enriched = routes._enrich_plan_like_chat_prompt(prompt)
    enriched_again = routes._enrich_plan_like_chat_prompt(enriched)

    assert enriched_again == enriched
    assert enriched_again.count("富内容初稿生成要求") == 1


def test_chat_start_sends_enriched_prompt_but_keeps_display_message(monkeypatch, tmp_path):
    started = []
    routes = _patch_chat_start_happy_path(monkeypatch, tmp_path, started)

    prompt = "请帮我编制一份提升营业厅服务质效专项行动方案"
    result = routes._handle_chat_start(
        object(),
        {"session_id": "sid-rich-chat", "message": prompt, "workspace": str(tmp_path)},
    )

    assert result["status"] == 200
    assert started, "chat start should delegate to stream startup"
    assert started[0]["display_msg"] == prompt
    assert started[0]["msg"] != prompt
    assert "富内容初稿" in started[0]["msg"]
    assert "至少 2 个 Markdown 表格" in started[0]["msg"]


def test_chat_start_requires_template_selection_when_template_is_missing(monkeypatch, tmp_path):
    started = []
    routes = _patch_chat_start_happy_path(monkeypatch, tmp_path, started)

    result = routes._handle_chat_start(
        object(),
        {"session_id": "sid-rich-chat", "message": "将这份方案套用模板", "workspace": str(tmp_path)},
    )

    assert result["status"] == 200
    assert result["payload"]["code"] == "template_selection_required"
    assert result["payload"]["docx_template_selection_required"] is True
    assert [item["id"] for item in result["payload"]["templates"]] == ["general-proposal", "meeting-minutes"]
    assert started == []


def test_chat_start_requires_template_selection_for_bare_template_command(monkeypatch, tmp_path):
    started = []
    routes = _patch_chat_start_happy_path(monkeypatch, tmp_path, started)

    result = routes._handle_chat_start(
        object(),
        {"session_id": "sid-rich-chat", "message": "套用模板", "workspace": str(tmp_path)},
    )

    assert result["status"] == 200
    assert result["payload"]["code"] == "template_selection_required"
    assert result["payload"]["docx_template_selection_required"] is True
    assert started == []


def test_chat_start_with_explicit_template_requires_source_when_context_is_empty(monkeypatch, tmp_path):
    started = []
    routes = _patch_chat_start_happy_path(monkeypatch, tmp_path, started)

    prompt = "将这份方案套用通用方案模板"
    result = routes._handle_chat_start(
        object(),
        {"session_id": "sid-rich-chat", "message": prompt, "workspace": str(tmp_path)},
    )

    assert result["status"] == 200
    assert result["payload"]["docx_source_required"] is True
    assert result["payload"]["template_id"] == "general-proposal"
    assistant = routes._docx_non_streaming_assistant_message(result["payload"], 0)
    assert assistant["docx_source_request"]["template_id"] == "general-proposal"
    assert started == []


def test_chat_start_shows_figure_adjustment_workspace_instead_of_model_chat(monkeypatch, tmp_path):
    started = []
    routes = _patch_chat_start_happy_path(monkeypatch, tmp_path, started)

    result = routes._handle_chat_start(
        object(),
        {"session_id": "sid-rich-chat", "message": "我要调整这份 Word 里的图片", "workspace": str(tmp_path)},
    )

    assert result["status"] == 200
    assert result["payload"]["code"] == "docx_figure_adjustment_required"
    assert result["payload"]["docx_figure_adjustment_required"] is True
    assert "图片调整工作台" in result["payload"]["message"]
    assert started == []


def _patch_docx_adjustment_handlers(monkeypatch, tmp_path, calls):
    from api import routes

    monkeypatch.setattr(routes, "get_session_for_file_ops", lambda session_id: SimpleNamespace(workspace=str(tmp_path)))
    monkeypatch.setattr(routes, "j", lambda handler, payload, status=200, **kwargs: {"status": status, "payload": payload})
    monkeypatch.setattr(routes, "bad", lambda handler, message, status=400, **kwargs: {"status": status, "payload": {"error": str(message)}})

    def fake_package_rich_draft(payload, workspace):
        calls.append(("package_rich_draft", payload, workspace))
        return {"ok": True, "action": "package", "out_dir": str(payload.get("out_dir") or "")}, 200

    def fake_rerender_asset(payload, workspace):
        calls.append(("rerender_asset", payload, workspace))
        figure_id = str(payload.get("figure_id") or "")
        if "/" in figure_id or "\\" in figure_id or figure_id.startswith("."):
            return {"ok": False, "code": "validation_failed", "message": "figure_id is invalid"}, 400
        return {"ok": True, "figure_id": figure_id, "display_path": "assets/fig-001/figure.svg"}, 200

    def fake_replace_asset(payload, workspace):
        calls.append(("replace_asset", payload, workspace))
        return {"ok": True, "figure_id": str(payload.get("figure_id") or ""), "output_path": str(payload.get("out_path") or "")}, 200

    monkeypatch.setattr(routes.docx_engine_v2, "package_rich_draft", fake_package_rich_draft)
    monkeypatch.setattr(routes.docx_engine_v2, "rerender_asset", fake_rerender_asset)
    monkeypatch.setattr(routes.docx_engine_v2, "replace_asset", fake_replace_asset)
    return routes


def test_figure_adjustment_package_handler_runs_packaging_script(monkeypatch, tmp_path):
    calls = []
    routes = _patch_docx_adjustment_handlers(monkeypatch, tmp_path, calls)
    (tmp_path / "draft.md").write_text("# 初稿\n\n![图](assets/figure.svg)\n", encoding="utf-8")
    (tmp_path / "assets").mkdir()

    result = routes._handle_docx_figure_adjustment_package(
        object(),
        {"session_id": "sid-rich-chat", "source_path": "draft.md", "out_dir": "draft-package", "asset_dir": "assets"},
    )

    assert result["status"] == 200
    assert result["payload"]["action"] == "package"
    assert result["payload"]["out_dir"] == "draft-package"
    assert calls and calls[0][0] == "package_rich_draft"
    assert calls[0][1]["source_path"] == "draft.md"
    assert calls[0][1]["out_dir"] == "draft-package"
    assert calls[0][1]["asset_dir"] == "assets"
    assert calls[0][2] == tmp_path.resolve()


def test_figure_adjustment_package_handler_accepts_allowed_absolute_paths(monkeypatch, tmp_path):
    calls = []
    workspace = tmp_path / "workspace"
    local_root = tmp_path / "desktop"
    workspace.mkdir()
    local_root.mkdir()
    source = local_root / "draft.md"
    out_dir = local_root / "draft-package"
    source.write_text("# 初稿\n\n![图](assets/figure.svg)\n", encoding="utf-8")
    routes = _patch_docx_adjustment_handlers(monkeypatch, workspace, calls)

    result = routes._handle_docx_figure_adjustment_package(
        object(),
        {"session_id": "sid-rich-chat", "source_path": str(source), "out_dir": str(out_dir)},
    )

    assert result["status"] == 200
    assert result["payload"]["action"] == "package"
    assert result["payload"]["out_dir"] == str(out_dir)
    assert calls and calls[0][0] == "package_rich_draft"
    assert calls[0][1]["source_path"] == str(source)
    assert calls[0][1]["out_dir"] == str(out_dir)
    assert calls[0][2] == workspace.resolve()


def test_figure_adjustment_replace_handler_runs_replace_script(monkeypatch, tmp_path):
    calls = []
    routes = _patch_docx_adjustment_handlers(monkeypatch, tmp_path, calls)
    (tmp_path / "方案.docx").write_bytes(b"docx")
    (tmp_path / "figure.svg").write_text("<svg></svg>", encoding="utf-8")

    result = routes._handle_docx_figure_adjustment_replace(
        object(),
        {
            "session_id": "sid-rich-chat",
            "docx_path": "方案.docx",
            "figure_id": "fig-001",
            "image_path": "figure.svg",
            "out_path": "方案-图片已调整.docx",
        },
    )

    assert result["status"] == 200
    assert result["payload"]["action"] == "replace"
    assert result["payload"]["figure_id"] == "fig-001"
    assert calls and calls[0][0] == "replace_asset"
    assert calls[0][1]["figure_id"] == "fig-001"
    assert calls[0][1]["out_path"] == "方案-图片已调整.docx"


def test_figure_adjustment_rerender_rejects_invalid_figure_id(monkeypatch, tmp_path):
    calls = []
    routes = _patch_docx_adjustment_handlers(monkeypatch, tmp_path, calls)
    (tmp_path / "draft.manifest.json").write_text("{}", encoding="utf-8")

    result = routes._handle_docx_figure_adjustment_rerender(
        object(),
        {"session_id": "sid-rich-chat", "manifest_path": "draft.manifest.json", "figure_id": "../bad"},
    )

    assert result["status"] == 400
    assert "figure_id" in result["payload"]["code"] or "figure_id" in result["payload"]["message"]
    assert calls and calls[0][0] == "rerender_asset"
