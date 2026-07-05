from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_docx_engine_workbench_has_visible_controls_and_actions():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    messages_js = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    style_css = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "docx-engine-workbench" in ui_js
    assert "renderDocxEngineWorkbench" in ui_js
    assert "runDocxEngineJob" in ui_js
    assert "installDocxEngineTemplate" in ui_js
    assert "markDocxEngineWpsVisualAccepted" in ui_js
    assert "openDocxDeliveryFolder" in ui_js
    assert "replaceDocxEngineAsset" in ui_js
    assert "模板包目录" in ui_js
    assert "安装模板包" in ui_js
    assert "覆盖已安装模板" in ui_js
    assert "质量报告" in ui_js
    assert "打开 DOCX" in ui_js
    assert "打开交付目录" in ui_js
    assert "记录 WPS 验收通过" in ui_js
    assert "aria-label" in ui_js
    assert ".docx-engine-workbench" in style_css

    assert "/api/docx-engine-v2/templates" in ui_js
    assert "/api/docx-engine-v2/templates/install" in ui_js
    assert "/api/docx-engine-v2/jobs" in ui_js
    assert "/api/docx-engine-v2/drafts/package" in ui_js
    assert "/api/docx-engine-v2/quality/wps-visual" in ui_js
    assert "/api/docx-engine-v2/assets/rerender" in ui_js
    assert "/api/docx-engine-v2/assets/replace" in ui_js
    assert "/api/docx-template/figure-adjust" not in ui_js
    assert "/api/file/open" in ui_js
    assert "renderDocxEngineWorkbenchMessage" in messages_js


def test_docx_engine_workbench_prioritizes_one_click_template_application():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    messages_js = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    style_css = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "套用模板生成 DOCX" in ui_js
    assert "套用当前结果生成 DOCX" in ui_js
    assert "use_current_result" in ui_js
    assert "交付目录（可不填）" in ui_js
    assert "请选择方案文件，或点击“套用当前结果生成 DOCX”。" in ui_js
    assert "请填写模板、源文件路径和交付目录。" not in ui_js
    assert "docx-engine-advanced" in ui_js
    assert "<summary>高级操作" in ui_js
    assert ".docx-engine-advanced" in style_css
    assert "正在套用模板" in messages_js


def test_docx_engine_workbench_exposes_required_accessible_control_names():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    for label in [
        "选择模板",
        "套用当前结果生成 DOCX",
        "从源文件生成 DOCX",
        "查看质量报告",
        "打开 DOCX",
        "打开交付目录",
        "记录 WPS 验收通过",
        "重渲染图片",
        "替换 DOCX 图片",
        "模板包目录",
        "安装模板包",
        "覆盖已安装模板",
        "从源文件生成 DOCX",
        "刷新模板列表",
    ]:
        assert label in ui_js


def test_docx_engine_workbench_covers_feedback_and_recovery_states():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    assert "role=\"status\"" in ui_js
    assert "aria-live=\"polite\"" in ui_js
    assert "data-docx-engine-status" in ui_js
    assert "template_package_path" in ui_js
    assert "replace_existing" in ui_js
    assert "确定要覆盖已安装模板吗" in ui_js
    assert "确认已在 WPS/Word 打开 DOCX" in ui_js
    assert "confirm(" in ui_js
    assert "旧 DOCX 需要先重新套模板" in ui_js
    assert "passed_with_warnings" in ui_js
    assert "quality_status" in ui_js
    assert "quality_report" in ui_js
    assert "data-docx-engine-quality-detail" in ui_js
    assert "data-docx-engine-action=\"quality\"" in ui_js
    assert "aria-invalid" in ui_js
    assert "document_path" in ui_js
    assert "delivery_dir" in ui_js


def test_docx_engine_workbench_surfaces_traceable_failure_artifacts():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    workspace_js = (REPO_ROOT / "static" / "workspace.js").read_text(encoding="utf-8")

    assert "err.payload" in workspace_js
    assert "_docxEngineFailureEvidence" in ui_js
    assert "failure_report_path" in ui_js
    assert "job_manifest_path" in ui_js
    assert "失败报告" in ui_js
    assert "作业清单" in ui_js
    assert "失败阶段" in ui_js


def test_docx_engine_workbench_prevents_duplicate_and_premature_actions():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    style_css = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "aria-busy" in ui_js
    assert "_syncDocxEngineActionAvailability" in ui_js
    assert "data-docx-engine-action=\"document\"" in ui_js
    assert "data-docx-engine-action=\"delivery\"" in ui_js
    assert "disabled>打开 DOCX" in ui_js
    assert "_docxFigureAdjustmentSetBusy" in ui_js
    assert ".docx-figure-adjustment-actions button:disabled" in style_css
