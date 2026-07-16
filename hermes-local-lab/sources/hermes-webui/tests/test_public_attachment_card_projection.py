"""Public reload contracts for attachments and durable interactive cards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_public_attachment_projection_keeps_reload_fields_without_absolute_path(tmp_path, monkeypatch):
    from api.brand_privacy import public_attachment_projection

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    uploaded = session_dir / "screen.png"
    uploaded.write_bytes(b"png")
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))

    projected = public_attachment_projection(
        {
            "name": "screen.png",
            "filename": "screen.png",
            "path": str(uploaded),
            "mime": "image/png",
            "size": 3,
            "is_image": True,
            "token": "upload-secret",
        },
        session_id="session-a",
    )

    assert projected == {
        "name": "screen.png",
        "filename": "screen.png",
        "mime": "image/png",
        "size": 3,
        "is_image": True,
        "ref": "screen.png",
    }
    assert str(attachment_root) not in json.dumps(projected)


@pytest.mark.parametrize(
    ("raw_name", "forbidden"),
    [
        (r"C:\Users\alice\private\screen.png", r"C:\Users\alice"),
        (r"\\server\share\private\screen.png", r"\\server\share"),
    ],
)
def test_public_attachment_projection_uses_cross_platform_basename_for_name_and_filename(
    raw_name,
    forbidden,
):
    from api.brand_privacy import public_attachment_projection

    projected = public_attachment_projection(
        {"name": raw_name, "filename": raw_name, "mime": "image/png"},
        session_id="session-a",
    )

    assert projected == {
        "name": "screen.png",
        "filename": "screen.png",
        "mime": "image/png",
    }
    assert forbidden not in json.dumps(projected)
    assert "ref" not in projected


def test_session_projection_preserves_message_and_pending_attachments_for_reload(tmp_path, monkeypatch):
    from api.brand_privacy import public_session_projection

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    pending = session_dir / "pending.txt"
    pending.write_text("pending", encoding="utf-8")
    image = session_dir / "screen.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))

    projected = public_session_projection({
        "session_id": "session-a",
        "workspace": str(tmp_path / "workspace"),
        "pending_attachments": [{"name": pending.name, "path": str(pending), "mime": "text/plain"}],
        "messages": [{
            "role": "user",
            "content": "",
            "attachments": [{"name": image.name, "path": str(image), "mime": "image/png", "is_image": True}],
        }],
    })

    assert projected["messages"][0]["attachments"] == [{
        "name": "screen.png",
        "mime": "image/png",
        "is_image": True,
        "ref": "screen.png",
    }]
    assert projected["pending_attachments"] == [{
        "name": "pending.txt",
        "mime": "text/plain",
        "ref": "pending.txt",
    }]
    assert projected["messages"][0]["content"] == ""


def test_relative_attachment_ref_resolves_only_inside_own_session(tmp_path, monkeypatch):
    from api.attachment_context import build_attachment_context

    attachment_root = tmp_path / "attachments"
    own_dir = attachment_root / "session-a"
    other_dir = attachment_root / "session-b"
    own_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    own = own_dir / "own.txt"
    other = other_dir / "other.txt"
    own.write_text("own-session-canary", encoding="utf-8")
    other.write_text("cross-session-secret-canary", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_file = workspace / "workspace.txt"
    workspace_file.write_text("workspace-absolute-secret-canary", encoding="utf-8")
    symlink = own_dir / "linked.txt"
    symlink.symlink_to(other)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))

    result = build_attachment_context(
        [
            {"name": "own.txt", "ref": "own.txt", "mime": "text/plain"},
            {"name": "traversal.txt", "ref": "../session-b/other.txt", "mime": "text/plain"},
            {"name": "own-absolute.txt", "ref": str(own), "mime": "text/plain"},
            {"name": "absolute.txt", "ref": str(other), "mime": "text/plain"},
            {"name": "workspace.txt", "ref": str(workspace_file), "mime": "text/plain"},
            {"name": "linked.txt", "ref": "linked.txt", "mime": "text/plain"},
            {"name": "windows.txt", "ref": r"C:\Users\alice\private.txt", "mime": "text/plain"},
            {"name": "unc.txt", "ref": r"\\server\share\private.txt", "mime": "text/plain"},
        ],
        workspace=str(workspace),
        session_id="session-a",
        cfg={},
    )

    assert "own-session-canary" in result.text_context
    assert "cross-session-secret-canary" not in result.text_context
    assert "workspace-absolute-secret-canary" not in result.text_context
    assert {
        "traversal.txt", "own-absolute.txt", "absolute.txt", "workspace.txt",
        "linked.txt", "windows.txt", "unc.txt",
    } <= set(result.skipped_files)


def test_completed_turn_keeps_renamed_upload_ref_across_reload_and_vision_retry(
    tmp_path,
    monkeypatch,
):
    """A display name must not replace the server-selected upload ref."""
    from api import models
    from api.attachment_context import build_attachment_context
    from api.models import Session
    from api.streaming import _public_attachment_descriptors_for_persistence
    from api.upload import _upload_destination

    attachment_root = tmp_path / "attachments"
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    monkeypatch.setattr(models, "SESSION_DIR", sessions_root)
    monkeypatch.setattr(models, "_write_session_index", lambda *args, **kwargs: None)

    # The second upload has the same user-facing name, so storage allocates a
    # different single-file ref inside this session's inbox.
    first = _upload_destination("session-a", "screen.png")
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"old")
    renamed = _upload_destination("session-a", "screen.png")
    renamed.write_bytes(b"\x89PNG\r\n\x1a\nnew-image")
    assert renamed.name != "screen.png"

    persisted = _public_attachment_descriptors_for_persistence(
        [{
            "name": "screen.png",
            "ref": renamed.name,
            "path": str(renamed),
            "mime": "image/png",
            "size": renamed.stat().st_size,
            "is_image": True,
            "token": "must-not-persist",
        }],
        session_id="session-a",
    )
    assert persisted == [{
        "name": "screen.png",
        "ref": renamed.name,
        "mime": "image/png",
        "size": renamed.stat().st_size,
        "is_image": True,
    }]
    assert str(attachment_root) not in json.dumps(persisted)
    assert "must-not-persist" not in json.dumps(persisted)

    session = Session(
        session_id="session-a",
        workspace=str(tmp_path / "workspace"),
        messages=[{"role": "user", "content": "请识别", "attachments": persisted}],
    )
    session.save(skip_index=True)
    reloaded = Session.load("session-a")
    assert reloaded is not None
    reloaded_attachments = reloaded.messages[0]["attachments"]
    assert reloaded_attachments[0]["name"] == "screen.png"
    assert reloaded_attachments[0]["ref"] == renamed.name

    # Vision retry reuses the descriptor from the reloaded message.  The
    # backend must resolve the real renamed file, not the display name.
    retry_context = build_attachment_context(
        reloaded_attachments,
        workspace=reloaded.workspace,
        session_id="session-a",
        cfg={},
        image_mode="native",
    )
    assert retry_context.image_paths == [str(renamed)]
    assert retry_context.image_items[0]["name"] == "screen.png"


def test_public_display_projection_hides_assistant_paths_but_preserves_user_original():
    from api.brand_privacy import public_message_projection, sanitize_persisted_assistant_message

    raw_user = {
        "role": "user",
        "content": "请处理 /Users/customer/input/source.docx",
    }
    internal_assistant = {
        "role": "assistant",
        "content": (
            "结果在 /Users/agent/runtime/output/report.docx，"
            r"备份在 C:\Users\agent\private\report.docx 和 \\server\share\report.docx"
        ),
        "reasoning": "临时读取 /private/runtime/source.md",
        "tool_calls": [{
            "id": "call-1",
            "function": {
                "name": "read_file",
                "arguments": '{"path":"/private/runtime/source.md","token":"tool-secret"}',
            },
        }],
    }

    display_assistant = sanitize_persisted_assistant_message(internal_assistant)
    public_assistant = public_message_projection(display_assistant, session_id="session-a")
    public_user = public_message_projection(raw_user, session_id="session-a")

    assert "/Users/agent/runtime/output/report.docx" in internal_assistant["content"]
    assert internal_assistant["reasoning"].endswith("/private/runtime/source.md")
    assert internal_assistant["tool_calls"][0]["function"]["arguments"].startswith('{"path"')
    assert public_user["content"] == raw_user["content"]
    serialized_display = json.dumps(display_assistant, ensure_ascii=False)
    serialized_public = json.dumps(public_assistant, ensure_ascii=False)
    for forbidden in (
        "/Users/agent", "/private/runtime", "tool-secret", r"C:\Users\agent", r"\\server\share",
    ):
        assert forbidden not in serialized_display
        assert forbidden not in serialized_public

    internal_tool = {
        "role": "tool",
        "name": "read_file",
        "tool_call_id": "call-1",
        "content": "raw /private/runtime/source.md token=tool-secret",
        "summary": "已读取 /private/runtime/source.md",
        "result": {"path": "/private/runtime/source.md", "token": "tool-secret"},
    }
    display_tool = sanitize_persisted_assistant_message(internal_tool)
    assert internal_tool["content"].startswith("raw /private/runtime")
    serialized_tool = json.dumps(display_tool, ensure_ascii=False)
    assert "/private/runtime" not in serialized_tool
    assert "tool-secret" not in serialized_tool
    assert "result" not in display_tool


def test_complete_expert_presenter_card_survives_public_projection_with_mutation_identity(tmp_path):
    from api.brand_privacy import public_message_projection

    workspace = tmp_path / "customer-workspace"
    workspace.mkdir()
    source = {
        "type": "writeflow",
        "kind": "expert_team",
        "runId": "run-full",
        "sourceSessionId": "session-full",
        "schemaVersion": 2,
        "version": 11,
        "executionStreamId": "stream-full",
        "currentStageId": "draft",
        "pendingInputId": "input-full",
        "stageReviewId": "review-full",
        "draftIdentity": {
            "stageAttempt": 2,
            "artifactAttempt": 3,
            "executionAttempt": 4,
            "briefRevision": 5,
            "reviewId": "review-full",
            "officeReviewId": "office-full",
        },
        "presentation": {
            "state": "awaiting_review",
            "title": "阶段成果待复核",
            "statusLabel": "阶段成果待复核",
            "visibleTitle": "重点项目方案",
            "detail": "请检查本阶段成果",
            "brief": {
                "status": "confirmed", "revision": 5,
                "originalRequest": "写方案", "originalRequestSummary": "写一份方案",
                "exactTitle": "重点项目方案", "documentType": "proposal",
                "documentTypeLabel": "方案", "purpose": "立项", "audience": "领导",
                "usageScenario": "评审", "additionalContext": "紧急",
                "documentControl": {"pageCount": 20},
                "sourcePolicySummary": {"mode": "provided_only"},
                "editable": True, "editPolicy": "confirmed_only",
                "validation": {"status": "passed"},
                "viewAction": {"id": "view_brief", "label": "查看 Brief", "kind": "ghost"},
                "token": "brief-secret",
            },
            "completionGates": {
                "content": {"status": "passed", "label": "内容", "reasonCode": "ok", "blockingIssueCount": 0},
                "document": {"status": "pending", "label": "DOCX", "reasonCode": "waiting", "blockingIssueCount": 0},
                "office": {"status": "pending", "label": "Office", "reasonCode": "waiting", "blockingIssueCount": 0},
            },
            "deliveryStatus": "pending",
            "nextAction": {"id": "approve_stage", "label": "通过阶段", "kind": "primary"},
            "gateSummary": "内容已确认",
            "capabilityKind": "contract_v2",
            "capabilityLabel": "企业合同任务",
        },
        "brief": {
            "status": "confirmed", "revision": 5, "exactTitle": "重点项目方案",
            "documentType": "proposal", "documentTypeLabel": "方案",
            "editable": True, "validation": {"status": "passed"},
            "documentControl": {"pageCount": 20},
            "sourcePolicySummary": {"mode": "provided_only"},
        },
        "completionGates": {
            "content": {"status": "passed", "label": "内容", "reasonCode": "ok", "blockingIssueCount": 0},
            "document": {"status": "pending", "label": "DOCX", "reasonCode": "waiting", "blockingIssueCount": 0},
            "office": {"status": "pending", "label": "Office", "reasonCode": "waiting", "blockingIssueCount": 0},
        },
        "officeReview": {
            "reviewId": "office-full", "documentRevision": 2,
            "documentSha256": "a" * 64, "canonicalSha256": "b" * 64,
            "status": "pending", "decision": "pending", "validity": "active",
            "reviewSessionStatus": "active", "checklist": {"opened": True},
            "issues": [{
                "issueId": "issue-1", "severity": "major", "targetDomain": "layout",
                "category": "spacing", "sectionId": "s1", "blockId": "b1",
                "logicalAssetId": "asset-1", "page": 3, "description": "间距不足",
                "expectedFix": "增大间距", "path": "/private/office/raw.json",
            }],
            "issueCount": 1, "reviewerLabel": "Office 复核", "waivedIssueIds": [],
            "token": "office-secret",
        },
        "nextAction": {"id": "approve_stage", "label": "通过阶段", "kind": "primary"},
        "capability": {"kind": "contract_v2", "label": "企业合同任务", "raw": "drop"},
        "artifactValidation": {"status": "passed", "message": "校验通过", "blockingIssueCount": 0, "path": "/private/validation.json"},
        "workspace": {
            "visible": True, "title": "专家团工作台", "state": "awaiting_review",
            "currentStage": {"id": "draft", "title": "初稿", "status": "awaiting_review"},
            "currentWorker": {"id": "writer", "name": "撰稿专家", "status": "working"},
            "phases": ["需求确认", "初稿"],
            "members": [{"id": "writer", "name": "撰稿专家", "status": "working"}],
            "timeline": [{"type": "stage_started", "title": "开始初稿", "at": "2026-07-16T12:00:00Z"}],
            "stageResult": {"id": "result-1", "title": "初稿", "summary": "已生成", "path": "/private/result.md"},
            "pendingInput": {"id": "input-full", "title": "补充说明", "placeholder": "请输入"},
            "path": "/private/workspace.json",
        },
        "workflow": {
            "stages": [{"id": "draft", "title": "初稿", "status": "awaiting_review", "worker_name": "撰稿专家"}],
            "currentStage": {"id": "draft", "title": "初稿", "status": "awaiting_review"},
            "progress": {"done": 1, "total": 2, "current": "初稿"},
        },
        "pendingInput": {"id": "input-full", "title": "补充说明", "placeholder": "请输入", "required": True},
        "stageResult": {"id": "result-1", "title": "初稿", "summary": "已生成", "content": "完整初稿", "path": "/private/result.md"},
        "intake": {"status": "confirmed", "title": "需求确认", "summary": "已确认"},
        "timelineEvents": [{"type": "stage_started", "title": "开始初稿", "detail": "处理中", "memberId": "writer", "memberName": "撰稿专家", "at": "2026-07-16T12:00:00Z"}],
        "actions": {"can_approve_stage": True, "can_request_revision": True},
        "raw_args": {"path": "/private/raw", "token": "raw-secret"},
    }

    projected = public_message_projection(
        {"role": "assistant", "content": "待复核", "_statusCard": source},
        workspace=str(workspace),
        session_id="session-full",
    )["_statusCard"]

    for key in (
        "brief", "completionGates", "officeReview", "nextAction", "capability",
        "artifactValidation", "workspace", "workflow", "pendingInput", "stageResult",
        "intake", "timelineEvents",
    ):
        assert key in projected, key
    assert projected["runId"] == "run-full"
    assert projected["sourceSessionId"] == "session-full"
    assert projected["version"] == 11
    assert projected["schemaVersion"] == 2
    assert projected["draftIdentity"]["officeReviewId"] == "office-full"
    assert projected["officeReview"]["issues"][0]["issueId"] == "issue-1"
    assert projected["workflow"]["currentStage"]["id"] == "draft"
    assert projected["pendingInput"]["id"] == "input-full"
    assert projected["actions"]["can_approve_stage"] is True
    serialized = json.dumps(projected, ensure_ascii=False)
    for forbidden in ("/private/", "brief-secret", "office-secret", "raw-secret", '"raw"'):
        assert forbidden not in serialized


def test_durable_card_projectors_preserve_ui_contract_and_relativize_paths(tmp_path):
    from api.brand_privacy import public_session_projection

    workspace = tmp_path / "customer-workspace"
    delivery = workspace / "delivery"
    delivery.mkdir(parents=True)
    document = delivery / "document.docx"
    report = delivery / "quality-report.json"
    source = workspace / "source.md"
    artifact = workspace / "articles" / "draft.md"
    artifact.parent.mkdir(parents=True)
    for path in (document, report, source, artifact):
        path.write_text("ok", encoding="utf-8")

    projected = public_session_projection({
        "session_id": "cards-a",
        "workspace": str(workspace),
        "messages": [{
            "role": "assistant",
            "content": "cards",
            "_statusCard": {
                "type": "writeflow",
                "kind": "expert_team",
                "runId": "run-1",
                "sourceSessionId": "cards-a",
                "version": 7,
                "schemaVersion": 2,
                "title": "专家团状态",
                "phase": "生成初稿",
                "phases": ["需求确认", "生成初稿", "交付"],
                "progress": {"done": 1, "total": 3},
                "team": {"id": "team-a", "title": "文档团队", "secret": "drop-me"},
                "members": [{"id": "writer", "name": "撰稿专家", "status": "执行中", "token": "drop-me"}],
                "tasks": [{"id": "draft", "title": "生成初稿", "status": "running", "artifacts": [str(artifact)]}],
                "artifacts": [{"id": "draft", "label": "初稿", "path": str(artifact), "exists": True}],
                "referenceArtifacts": [{"id": "bad", "label": "外部产物", "path": "C:/private/internal.docx", "exists": True}],
                "rows": [{"label": "进度", "value": "1/3"}],
                "internal_path": "/private/internal/status.json",
            },
            "docx_template_selection": {
                "code": "template_selection_required",
                "source_path": str(source),
                "templates": [{"id": "general", "name": "通用模板", "version": "2", "description": "方案"}],
                "examples": ["套用通用模板"],
                "token": "drop-me",
            },
            "docx_template_delivery": {
                "template_id": "general",
                "template": {"id": "general", "name": "通用模板", "version": "2"},
                "document_path": str(document),
                "delivery_dir": str(delivery),
                "quality_status": "passed_with_warnings",
                "quality_report_path": str(report),
                "internal_path": "/private/internal/delivery.json",
            },
            "docx_source_request": {"template_id": "general", "template": {"id": "general", "name": "通用模板"}},
            "docx_engine_workbench": {
                "template_id": "general",
                "template": {"id": "general", "name": "通用模板", "version": "2"},
                "templates": [{"id": "general", "name": "通用模板", "version": "2"}],
            },
            "docx_figure_adjustment": {
                "code": "docx_figure_adjustment_required",
                "actions": [{"id": "package", "label": "打包初稿"}],
                "examples": ["重渲染 fig-001"],
            },
            "vision_recovery": {"id": "vision-1", "type": "image_attachment_error", "path": "/private/internal/image.png"},
        }],
    })

    message = projected["messages"][0]
    card = message["_statusCard"]
    assert card["runId"] == "run-1" and card["version"] == 7 and card["schemaVersion"] == 2
    assert card["team"]["title"] == "文档团队"
    assert card["members"][0]["name"] == "撰稿专家"
    assert card["tasks"][0]["artifacts"] == ["articles/draft.md"]
    assert card["artifacts"][0]["path"] == "articles/draft.md"
    assert "path" not in card["referenceArtifacts"][0]
    assert card["referenceArtifacts"][0]["openable"] is False
    assert card["progress"] == {"done": 1, "total": 3}
    assert message["docx_template_selection"]["source_path"] == "source.md"
    assert message["docx_template_selection"]["templates"][0]["version"] == "2"
    assert message["docx_template_delivery"]["document_path"] == "delivery/document.docx"
    assert message["docx_template_delivery"]["delivery_dir"] == "delivery"
    assert message["docx_template_delivery"]["quality_report_path"] == "delivery/quality-report.json"
    assert message["docx_source_request"]["template"]["name"] == "通用模板"
    assert message["docx_engine_workbench"]["templates"][0]["version"] == "2"
    assert message["docx_figure_adjustment"]["actions"] == [{"id": "package", "label": "打包初稿"}]
    assert message["vision_recovery"] == {"id": "vision-1", "type": "image_attachment_error"}
    serialized = json.dumps(message, ensure_ascii=False)
    assert str(workspace) not in serialized
    assert "/private/internal" not in serialized
    assert "drop-me" not in serialized


def test_reloaded_card_buttons_use_session_scoped_relative_paths():
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    assert "data-document-path" in ui
    assert "data-delivery-dir" in ui
    assert "session_id:sid,path:pathValue" in ui
    assert "data-writeflow-artifact-path" in ui
    assert "openWriteflowArtifact(this)" in ui


def test_expert_team_reload_projection_keeps_interactive_contract_but_drops_operational_fields(tmp_path):
    from api.brand_privacy import public_message_projection

    workspace = tmp_path / "customer-workspace"
    workspace.mkdir()
    projected = public_message_projection(
        {
            "role": "assistant",
            "content": "阶段成果待复核",
            "_statusCard": {
                "type": "writeflow",
                "kind": "expert_team",
                "runId": "run-7",
                "sourceSessionId": "session-7",
                "schemaVersion": 2,
                "version": 7,
                "currentStageId": "draft",
                "draftIdentity": {
                    "stageAttempt": 2,
                    "artifactAttempt": 3,
                    "executionAttempt": 4,
                    "briefRevision": 5,
                    "reviewId": "review-7",
                    "officeReviewId": "office-7",
                    "token": "draft-secret",
                },
                "presentation": {
                    "state": "awaiting_review",
                    "title": "待复核",
                    "statusLabel": "阶段成果待复核",
                    "visibleTitle": "重点项目方案",
                    "detail": "请检查本阶段成果",
                    "primaryAction": {"id": "review_stage", "label": "去复核", "kind": "primary"},
                    "secondaryActions": [
                        {"id": "view_result", "label": "查看成果", "kind": "ghost"},
                        {"id": "approve_stage", "label": "无修改，进入下一阶段", "kind": "primary"},
                    ],
                    "result": {
                        "title": "方案初稿",
                        "summary": "摘要",
                        "content": "完整阶段内容",
                        "path": "/private/runtime/result.md",
                        "raw_result": "do-not-publish",
                    },
                    "token": "presentation-secret",
                },
                "primaryConfirmation": {
                    "id": "confirm-7",
                    "type": "stage_review",
                    "kind": "stage_review",
                    "title": "阶段成果待复核",
                    "description": "请确认或提出修改",
                    "status": "pending",
                    "fields": [{"id": "decision", "label": "复核结论", "required": True}],
                    "args": {"command": "cat /private/runtime"},
                },
                "stageReview": {
                    "display_state": "awaiting_review",
                    "actionable": True,
                    "is_final_stage": False,
                    "task_id": "draft",
                    "title": "方案初稿",
                    "phase": "初稿",
                    "worker_name": "撰稿专家",
                    "revision_count": 1,
                    "output": {
                        "id": "output-7",
                        "task_id": "draft",
                        "title": "方案初稿",
                        "phase": "初稿",
                        "status": "generated",
                        "summary": "摘要",
                        "preview": "预览",
                        "content": "完整阶段内容",
                        "content_length": 8,
                        "path": "/private/runtime/output.md",
                        "result": {"token": "output-secret"},
                    },
                    "token": "review-secret",
                },
                "reviewItems": [
                    {"id": "item-1", "title": "核对数据", "phase": "事实核验", "status": "pending", "usedInRevision": False, "path": "/private/item"}
                ],
                "stageOutputs": [
                    {
                        "id": "output-7",
                        "task_id": "draft",
                        "title": "方案初稿",
                        "status": "generated",
                        "content": "完整阶段内容",
                        "summary": "摘要",
                        "preview": "预览",
                        "content_length": 8,
                        "has_long_content": False,
                        "note": "已生成",
                        "revision_count": 1,
                        "result": "raw-output",
                    }
                ],
                "actions": {
                    "can_start_generation": False,
                    "can_cancel": False,
                    "can_submit_stage_input": False,
                    "can_retry": True,
                    "can_approve_stage": True,
                    "can_request_revision": True,
                    "raw_args": {"token": "action-secret"},
                },
            },
        },
        workspace=str(workspace),
        session_id="session-7",
    )

    card = projected["_statusCard"]
    assert card["draftIdentity"] == {
        "stageAttempt": 2,
        "artifactAttempt": 3,
        "executionAttempt": 4,
        "briefRevision": 5,
        "reviewId": "review-7",
        "officeReviewId": "office-7",
    }
    assert card["presentation"]["state"] == "awaiting_review"
    assert card["presentation"]["primaryAction"]["id"] == "review_stage"
    assert card["presentation"]["secondaryActions"][1]["id"] == "approve_stage"
    assert card["presentation"]["result"]["content"] == "完整阶段内容"
    assert card["primaryConfirmation"]["fields"] == [
        {"id": "decision", "label": "复核结论", "required": True}
    ]
    assert card["stageReview"]["actionable"] is True
    assert card["stageReview"]["output"]["summary"] == "摘要"
    assert card["reviewItems"][0]["title"] == "核对数据"
    assert card["stageOutputs"][0]["content"] == "完整阶段内容"
    assert card["actions"] == {
        "can_start_generation": False,
        "can_cancel": False,
        "can_submit_stage_input": False,
        "can_retry": True,
        "can_approve_stage": True,
        "can_request_revision": True,
    }
    serialized = json.dumps(card, ensure_ascii=False)
    for forbidden in (
        "/private/runtime",
        "draft-secret",
        "presentation-secret",
        "do-not-publish",
        "output-secret",
        "review-secret",
        "raw-output",
        "action-secret",
    ):
        assert forbidden not in serialized
