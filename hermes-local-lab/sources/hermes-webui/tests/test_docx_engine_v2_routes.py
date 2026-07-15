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
    assert "/api/docx-engine-v2/templates/install" in routes_py
    assert "/api/docx-engine-v2/jobs" in routes_py
    assert "/api/docx-engine-v2/drafts/package" in routes_py
    assert "/api/docx-engine-v2/quality/wps-visual" in routes_py
    assert "/api/docx-engine-v2/quality/wps-visual/begin" in routes_py
    assert "/api/docx-engine-v2/assets/rerender" in routes_py
    assert "/api/docx-engine-v2/assets/replace" in routes_py
    assert "/api/file/open" in routes_py


def test_expert_delivery_bridge_passes_canonical_contract_as_json(monkeypatch, tmp_path):
    from api import docx_engine_v2

    source = tmp_path / "canonical.md"
    assets = tmp_path / "assets"
    source.write_text("# 月度汇报\n", encoding="utf-8")
    assets.mkdir()
    out_dir = tmp_path / ".taiji" / "expert-team-deliveries" / "run-1" / "delivery" / "attempt-1" / "delivery"
    captured = {}

    def fake_run(args, **_kwargs):
        captured["args"] = args
        return SimpleNamespace(returncode=1, stdout='{"ok":false,"code":"brief_incomplete","message":"bad"}\n', stderr="")

    monkeypatch.setattr(docx_engine_v2, "run_engine", fake_run)
    payload, status = docx_engine_v2._create_expert_delivery_job(
        {
            "template_id": "enterprise-work-report",
            "source_path": str(source),
            "asset_dir": str(assets),
            "out_dir": str(out_dir),
            "document_metadata": {"title": "月度汇报"},
            "canonical_binding": {"artifactId": "polish:1"},
            "renderer_identity": {"name": "docx-engine-v2"},
            "render_input_binding": {"schemaVersion": "render-input-binding/v1"},
            "render_input_fingerprint": "f" * 64,
        },
        tmp_path,
        run_id="run-1",
        stage_id="delivery",
        attempt=1,
    )

    assert status == 400 and payload["code"] == "brief_incomplete"
    for flag in ("--document-metadata-json", "--canonical-binding-json", "--renderer-identity-json", "--render-input-binding-json", "--render-input-fingerprint"):
        assert flag in captured["args"]


def test_python_and_node_use_same_canonical_json_digest():
    from api.expert_teams.documents import _sha256_payload

    assert _sha256_payload({"b": 2, "nested": {"y": 2, "x": 1}, "a": 1}) == (
        "13c79d4b0b5375d4f715181ba6cedbb6603108855f68f061983f33693b87a75c"
    )


def test_begin_office_review_route_uses_server_trusted_profile_identity(monkeypatch, tmp_path):
    from api import routes

    monkeypatch.setattr(
        routes,
        "get_session_for_file_ops",
        lambda _session_id: SimpleNamespace(workspace=str(tmp_path), profile="finance"),
    )
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200: {"status": status, "payload": payload})
    captured = {}
    monkeypatch.setattr(
        routes.docx_engine_v2,
        "begin_office_review",
        lambda body, workspace, *, trusted_reviewer: (
            captured.update(body=dict(body), workspace=workspace, reviewer=trusted_reviewer)
            or ({"ok": True}, 200)
        ),
    )
    monkeypatch.setattr(
        "api.expert_teams.office_review.getpass.getuser",
        lambda: "localuser",
    )

    result = routes._handle_docx_engine_v2_begin_office_review(
        object(),
        {"session_id": "sid-1", "delivery_dir": ".taiji/expert-team-deliveries/x"},
    )

    assert result["status"] == 200
    assert captured["reviewer"] == "localuser@finance"


def test_legacy_figure_adjustment_routes_do_not_keep_skill_script_runner():
    routes_py = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")

    assert "def _docx_template_run_script(" not in routes_py
    assert "docx-template-skill script not found" not in routes_py


def test_explicit_template_selection_still_returns_template_metadata():
    from api import routes

    result = routes._docx_template_invocation_result(
        "/docx-template-skill 请把当前成果套用通用方案模板（templateId: general-proposal）。"
    )

    assert result is not None
    assert result["docx_template_selected"] is True
    assert result["template_id"] == "general-proposal"
    assert result["template"]["id"] == "general-proposal"
    assert result["templates"]


def test_docx_template_selection_preserves_explicit_source_path(tmp_path):
    from api import routes

    source_path = "/Users/bwb/Desktop/OA国产化替代方案/OA系统国产化替代详细设计方案.md"
    session = SimpleNamespace(session_id="sid-docx", workspace=str(tmp_path), messages=[])

    result = routes._docx_template_invocation_result_for_session(
        f'将"{source_path}"套用模板',
        session,
    )
    assistant = routes._docx_non_streaming_assistant_message(result, 1)

    assert result["docx_template_selection_required"] is True
    assert result["source_path"] == source_path
    assert assistant["docx_template_selection"]["source_path"] == source_path


def test_docx_template_selected_uses_explicit_source_path(monkeypatch, tmp_path):
    from api import routes

    source_file = tmp_path / "OA系统国产化替代详细设计方案.md"
    source_file.write_text("# OA系统国产化替代详细设计方案\n\n| 项 | 值 |\n| --- | --- |\n| 范围 | 测试 |\n", encoding="utf-8")
    source_path = str(source_file)
    session = SimpleNamespace(session_id="sid-docx", workspace=str(tmp_path), messages=[])
    seen = {}

    def fake_create_job(payload, workspace):
        seen["payload"] = dict(payload)
        return {
            "ok": True,
            "delivery_dir": str(tmp_path / "OA系统国产化替代详细设计方案-模板交付包"),
            "document_path": str(tmp_path / "OA系统国产化替代详细设计方案-模板交付包" / "document.docx"),
            "quality_status": "passed_with_warnings",
        }, 200

    monkeypatch.setattr(routes.docx_engine_v2, "create_job", fake_create_job)

    result = routes._docx_template_invocation_result_for_session(
        f'/docx-template-skill 请将源文件 "{source_path}" 套用通用方案模板（templateId: general-proposal）。',
        session,
    )

    assert result["docx_template_applied"] is True
    assert seen["payload"]["source_path"] == source_path
    assert seen["payload"]["template_id"] == "general-proposal"


def test_docx_template_selected_missing_explicit_source_path_fails_before_job(monkeypatch, tmp_path):
    from api import routes

    source_path = "/Users/bwb/Desktop/OA国产化替代方案/不存在的方案.md"
    session = SimpleNamespace(session_id="sid-docx", workspace=str(tmp_path), messages=[])

    def fake_create_job(payload, workspace):
        raise AssertionError(f"missing explicit source should not start a DOCX job: {payload}")

    monkeypatch.setattr(routes.docx_engine_v2, "create_job", fake_create_job)

    result = routes._docx_template_invocation_result_for_session(
        f'/docx-template-skill 请将源文件 "{source_path}" 套用通用方案模板（templateId: general-proposal）。',
        session,
    )

    assert result["ok"] is False
    assert result["docx_source_required"] is True
    assert result["source_path"] == source_path
    assert "未读取到源文件" in result["message"]


def test_docx_template_selected_auto_generates_from_latest_chat_result(monkeypatch, tmp_path):
    from api import routes

    session = SimpleNamespace(
        session_id="sid-docx",
        workspace=str(tmp_path),
        messages=[
            {"role": "user", "content": "生成一份方案"},
            {"role": "assistant", "content": "# 项目方案\n\n这里是需要套模板的正文内容。" * 4},
        ],
    )
    seen = {}

    def fake_create_job(payload, workspace):
        seen["payload"] = dict(payload)
        source_path = Path(payload["source_path"])
        assert source_path.exists()
        return {
            "ok": True,
            "job_id": "job-auto",
            "delivery_dir": str(tmp_path / "项目方案-模板交付包"),
            "document_path": str(tmp_path / "项目方案-模板交付包" / "document.docx"),
            "quality_status": "passed_with_warnings",
            "quality_report_path": str(tmp_path / "项目方案-模板交付包" / "quality-report.json"),
            "quality_report": {"checks": [{"id": "wps_visual", "status": "not_verified"}]},
        }, 200

    monkeypatch.setattr(routes.docx_engine_v2, "create_job", fake_create_job)

    result = routes._docx_template_invocation_result_for_session(
        "/docx-template-skill 请把当前成果套用通用方案模板（templateId: general-proposal）。",
        session,
    )

    assert result["ok"] is True
    assert result["docx_template_applied"] is True
    assert result["delivery_result"]["document_path"].endswith("document.docx")
    assert result["delivery_result"]["quality_status"] == "passed_with_warnings"
    assert seen["payload"]["template_id"] == "general-proposal"
    assert seen["payload"]["source_path"].endswith(".md")
    assert "docx-engine-v2-current-results" in seen["payload"]["source_path"]
    assert "docx_engine_workbench" not in routes._docx_non_streaming_assistant_message(result, 1)


def test_docx_template_selected_asks_for_source_when_context_is_unclear(tmp_path):
    from api import routes

    session = SimpleNamespace(
        session_id="sid-docx",
        workspace=str(tmp_path),
        messages=[{"role": "assistant", "content": "好的。"}],
    )

    result = routes._docx_template_invocation_result_for_session(
        "/docx-template-skill 请把当前成果套用通用方案模板（templateId: general-proposal）。",
        session,
    )
    assistant = routes._docx_non_streaming_assistant_message(result, 1)

    assert result["ok"] is False
    assert result["docx_source_required"] is True
    assert result["template_id"] == "general-proposal"
    assert "源文件" in result["message"]
    assert assistant["docx_source_request"]["template_id"] == "general-proposal"
    assert "docx_engine_workbench" not in assistant


def test_pending_docx_source_request_uses_next_user_path(monkeypatch, tmp_path):
    from api import routes

    source_path = tmp_path / "Desktop" / "ERP国产化替代详细设计方案.md"
    source_path.parent.mkdir()
    source_path.write_text("# ERP 国产化替代详细设计方案\n", encoding="utf-8")
    session = SimpleNamespace(
        session_id="sid-docx",
        workspace=str(tmp_path / "workspace"),
        messages=[
            {
                "role": "assistant",
                "content": "请把源文件路径发给我。",
                "docx_source_request": {"template_id": "general-proposal", "template": {"id": "general-proposal"}},
            },
        ],
    )
    seen = {}

    def fake_create_job(payload, workspace):
        seen["payload"] = dict(payload)
        return {
            "ok": True,
            "delivery_dir": str(source_path.parent / "ERP国产化替代详细设计方案-模板交付包"),
            "document_path": str(source_path.parent / "ERP国产化替代详细设计方案-模板交付包" / "document.docx"),
            "quality_status": "passed_with_warnings",
        }, 200

    monkeypatch.setattr(routes.docx_engine_v2, "create_job", fake_create_job)

    result = routes._docx_template_pending_source_result_for_session(
        f"源文件在 {source_path}",
        session,
        [],
    )

    assert result["docx_template_applied"] is True
    assert seen["payload"]["source_path"] == str(source_path)
    assert seen["payload"]["out_dir"].endswith("ERP国产化替代详细设计方案-模板交付包")


def test_docx_template_source_path_from_text_accepts_relative_path_in_sentence():
    from api import routes

    assert routes._docx_template_source_path_from_text("源文件：方案.md") == "方案.md"
    assert routes._docx_template_source_path_from_text("源文件在 docs/方案正文.docx") == "docs/方案正文.docx"


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

    result = routes._docx_template_invocation_result("套用模板")
    routes._record_docx_non_streaming_turn_for_session(session, "套用通用方案模板", result)

    assert saved == ["saved"]
    assert published == ["session_new"]
    assert session.active_stream_id is None
    assert session.pending_user_message is None
    assert session.messages[0]["role"] == "user"
    assert session.messages[0]["content"] == "套用通用方案模板"
    assert session.messages[1]["role"] == "assistant"
    assert session.messages[1]["docx_template_selection"]["code"] == "template_selection_required"
    assert session.context_messages[-1]["content"] == "请选择要套用的模板；在选择前不会生成 JSON 或渲染 DOCX。"
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


def test_docx_engine_v2_install_template_routes_to_service(monkeypatch, tmp_path):
    routes = _patch_route_json(monkeypatch, tmp_path)

    def fake_install_template(payload, workspace):
        assert payload["package_path"] == "templates/custom-proposal"
        assert workspace == tmp_path.resolve()
        return {
            "ok": True,
            "template_id": "custom-proposal",
            "registry_entry": {"templateId": "custom-proposal", "path": "installed/custom-proposal"},
            "templates": [{"id": "custom-proposal"}],
        }, 200

    monkeypatch.setattr(routes.docx_engine_v2, "install_template", fake_install_template)

    result = routes._handle_docx_engine_v2_install_template(
        object(),
        {"session_id": "sid-docx", "package_path": "templates/custom-proposal"},
    )

    assert result["status"] == 200
    assert result["payload"]["template_id"] == "custom-proposal"
    assert result["payload"]["templates"][0]["id"] == "custom-proposal"


def test_docx_engine_v2_package_draft_routes_to_service(monkeypatch, tmp_path):
    routes = _patch_route_json(monkeypatch, tmp_path)

    def fake_package_rich_draft(payload, workspace):
        assert payload["source_path"] == "source.md"
        assert payload["out_dir"] == "draft-package"
        assert workspace == tmp_path.resolve()
        return {"ok": True, "action": "package", "out_dir": "draft-package"}, 200

    monkeypatch.setattr(routes.docx_engine_v2, "package_rich_draft", fake_package_rich_draft)

    result = routes._handle_docx_engine_v2_package_draft(
        object(),
        {"session_id": "sid-docx", "source_path": "source.md", "out_dir": "draft-package"},
    )

    assert result["status"] == 200
    assert result["payload"]["action"] == "package"
    assert result["payload"]["out_dir"] == "draft-package"


def test_docx_engine_v2_install_template_validates_path_and_returns_templates(monkeypatch, tmp_path):
    from api import docx_engine_v2
    import subprocess
    import json

    package_dir = tmp_path / "templates" / "custom-proposal"
    package_dir.mkdir(parents=True)
    calls = []

    def fake_run_engine(args):
        calls.append(args)
        if str(args[0]).endswith("install-template.js"):
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "templateId": "custom-proposal",
                        "registryEntry": {"templateId": "custom-proposal", "path": "installed/custom-proposal"},
                    }
                )
                + "\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"ok": True, "templates": [{"id": "custom-proposal"}]}) + "\n",
            stderr="",
        )

    monkeypatch.setattr(docx_engine_v2, "run_engine", fake_run_engine)

    payload, status = docx_engine_v2.install_template(
        {"package_path": "templates/custom-proposal"},
        tmp_path,
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["template_id"] == "custom-proposal"
    assert payload["registry_entry"]["path"] == "installed/custom-proposal"
    assert payload["templates"] == [{"id": "custom-proposal"}]
    assert "--package" in calls[0]
    assert str(package_dir.resolve()) in calls[0]


def test_docx_engine_v2_install_template_passes_explicit_replace_flag(monkeypatch, tmp_path):
    from api import docx_engine_v2
    import subprocess
    import json

    package_dir = tmp_path / "templates" / "custom-proposal"
    package_dir.mkdir(parents=True)
    calls = []

    def fake_run_engine(args):
        calls.append(args)
        if str(args[0]).endswith("install-template.js"):
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "action": "replaced",
                        "templateId": "custom-proposal",
                        "registryEntry": {"templateId": "custom-proposal", "path": "installed/custom-proposal"},
                    }
                )
                + "\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"ok": True, "templates": [{"id": "custom-proposal"}]}) + "\n",
            stderr="",
        )

    monkeypatch.setattr(docx_engine_v2, "run_engine", fake_run_engine)

    payload, status = docx_engine_v2.install_template(
        {"package_path": "templates/custom-proposal", "replace_existing": True},
        tmp_path,
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["action"] == "replaced"
    assert "--replace" in calls[0]


def test_docx_engine_v2_install_template_rejects_outside_package_path(tmp_path):
    from api import docx_engine_v2

    outside_dir = tmp_path.parent / f"{tmp_path.name}-outside-package"

    payload, status = docx_engine_v2.install_template(
        {"package_path": str(outside_dir)},
        tmp_path,
    )

    assert status == 400
    assert payload["ok"] is False
    assert payload["code"] == "validation_failed"
    assert "package_path" in payload["message"]


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


def test_docx_engine_v2_create_job_can_use_latest_chat_result(monkeypatch, tmp_path):
    from api import routes

    session = SimpleNamespace(
        session_id="sid-docx",
        workspace=str(tmp_path),
        messages=[
            {"role": "user", "content": "生成一份方案"},
            {"role": "assistant", "content": "# 项目方案\n\n这里是可套用模板的正文内容。" * 4},
            {
                "role": "assistant",
                "content": "已选择模板，请在文档模板工作台生成 DOCX 交付包。",
                "docx_engine_workbench": {"template_id": "general-proposal"},
            },
        ],
    )
    monkeypatch.setattr(routes, "get_session_for_file_ops", lambda session_id: session)
    monkeypatch.setattr(routes, "j", lambda handler, payload, status=200, **kwargs: {"status": status, "payload": payload})
    monkeypatch.setattr(routes, "bad", lambda handler, message, status=400, **kwargs: {"status": status, "payload": {"error": str(message)}})

    seen = {}

    def fake_create_job(payload, workspace):
        seen["payload"] = dict(payload)
        source_path = Path(payload["source_path"])
        assert source_path.exists()
        assert source_path.read_text(encoding="utf-8").startswith("# 项目方案")
        return {"ok": True, "document_path": str(tmp_path / "delivery" / "document.docx")}, 200

    monkeypatch.setattr(routes.docx_engine_v2, "create_job", fake_create_job)

    result = routes._handle_docx_engine_v2_create_job(
        object(),
        {
            "session_id": "sid-docx",
            "template_id": "general-proposal",
            "use_current_result": True,
        },
    )

    assert result["status"] == 200
    assert seen["payload"]["source_path"].endswith(".md")
    assert "docx-engine-v2-current-results" in seen["payload"]["source_path"]


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


def test_docx_engine_v2_create_job_allows_user_absolute_source_paths(monkeypatch, tmp_path):
    from api import docx_engine_v2
    import subprocess
    import json

    user_home = tmp_path / "home"
    desktop = user_home / "Desktop"
    workspace = tmp_path / "workspace"
    source_path = desktop / "ERP国产化替代详细设计方案.md"
    source_path.parent.mkdir(parents=True)
    workspace.mkdir()
    source_path.write_text("# ERP 国产化替代详细设计方案\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user_home))
    calls = []

    def fake_run_engine(args):
        calls.append(args)
        out_dir = Path(args[args.index("--out-dir") + 1])
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "jobId": "job-desktop",
                    "deliveryDir": str(out_dir),
                    "documentPath": str(out_dir / "document.docx"),
                    "qualityStatus": "passed_with_warnings",
                }
            ) + "\n",
            stderr="",
        )

    monkeypatch.setattr(docx_engine_v2, "run_engine", fake_run_engine)

    payload, status = docx_engine_v2.create_job(
        {
            "template_id": "general-proposal",
            "source_path": str(source_path),
            "out_dir": str(desktop / "ERP国产化替代详细设计方案-模板交付包"),
        },
        workspace,
    )

    assert status == 200
    assert payload["document_path"].endswith("document.docx")
    assert str(source_path) in calls[0]


def test_docx_engine_v2_create_job_preserves_traceable_failure_paths(monkeypatch, tmp_path):
    from api import docx_engine_v2
    import subprocess
    import json

    source_path = tmp_path / "source.md"
    source_path.write_text("# Proposal\n", encoding="utf-8")
    failure_payload = {
        "ok": False,
        "code": "validation_failed",
        "stage": "validation",
        "message": "模板输入不满足要求。",
        "failures": ["模板输入不满足要求。"],
        "jobManifestPath": str(tmp_path / "delivery" / "job.manifest.json"),
        "failureReportPath": str(tmp_path / "delivery" / "failure-report.json"),
        "failureReport": {
            "schemaVersion": "docx-engine-v2/failure-report",
            "ok": False,
            "code": "validation_failed",
            "stage": "validation",
            "message": "模板输入不满足要求。",
            "failures": ["模板输入不满足要求。"],
            "jobId": "job-failed",
            "jobManifest": "job.manifest.json",
        },
    }

    def fake_run_engine(args):
        return subprocess.CompletedProcess(
            args=args,
            returncode=3,
            stdout=json.dumps(failure_payload) + "\n",
            stderr="",
        )

    monkeypatch.setattr(docx_engine_v2, "run_engine", fake_run_engine)

    payload, status = docx_engine_v2.create_job(
        {"template_id": "general-proposal", "source_path": "source.md", "out_dir": "delivery"},
        tmp_path,
    )

    assert status == 400
    assert payload["ok"] is False
    assert payload["code"] == "validation_failed"
    assert payload["stage"] == "validation"
    assert payload["job_manifest_path"].endswith("job.manifest.json")
    assert payload["failure_report_path"].endswith("failure-report.json")
    assert payload["failure_report"]["schemaVersion"] == "docx-engine-v2/failure-report"


def test_docx_engine_v2_records_wps_visual_acceptance(monkeypatch, tmp_path):
    routes = _patch_route_json(monkeypatch, tmp_path)

    def fake_record_wps_visual_acceptance(payload, workspace):
        assert workspace == tmp_path.resolve()
        assert payload["delivery_dir"] == "delivery"
        assert payload["status"] == "passed"
        return {
            "ok": True,
            "delivery_dir": str(tmp_path / "delivery"),
            "quality_status": "passed",
            "quality_report_path": str(tmp_path / "delivery" / "quality-report.json"),
            "quality_report": {
                "status": "passed",
                "checks": [{"id": "wps_visual", "status": "passed", "reviewedBy": "user"}],
                "warnings": [],
                "failures": [],
            },
        }, 200

    monkeypatch.setattr(routes.docx_engine_v2, "record_wps_visual_acceptance", fake_record_wps_visual_acceptance)

    result = routes._handle_docx_engine_v2_wps_visual_acceptance(
        object(),
        {"session_id": "sid-docx", "delivery_dir": "delivery", "status": "passed"},
    )

    assert result["status"] == 200
    assert result["payload"]["quality_status"] == "passed"
    assert result["payload"]["quality_report"]["checks"][0]["status"] == "passed"


def test_docx_engine_v2_wps_visual_cli_validation_failure_returns_400(monkeypatch, tmp_path):
    from api import docx_engine_v2
    import json
    import subprocess

    delivery_dir = tmp_path / "delivery"
    delivery_dir.mkdir()

    def fake_run_engine(args):
        return subprocess.CompletedProcess(
            args=args,
            returncode=3,
            stdout=json.dumps(
                {
                    "ok": False,
                    "code": "wps_visual_record_failed",
                    "message": "quality-report.json not found",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr(docx_engine_v2, "run_engine", fake_run_engine)

    payload, status = docx_engine_v2.record_wps_visual_acceptance(
        {"delivery_dir": "delivery", "status": "passed"},
        tmp_path,
    )

    assert status == 400
    assert payload["ok"] is False
    assert payload["code"] == "wps_visual_record_failed"


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
