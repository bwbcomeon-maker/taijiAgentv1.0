"""Single expert-team view contract consumed by the frontend presenter."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy

from .contracts import (
    EXPERT_TEAM_CONTRACT_V1,
    TASK_CONFIGURATION_ERROR_MESSAGE,
    brief_summary,
    classify_contract_version,
    required_sections_for_brief,
)
from .document_capabilities import brief_schema, source_requirement

from .materials import business_context_for_run, content_summary


STATE_LABELS = {
    "collecting_required": "必须需求待确认",
    "collecting_optional": "可选补充待处理",
    "ready_to_generate": "准备开始生成",
    "starting": "正在启动专家团",
    "start_failed": "启动失败",
    "generation_failed": "生成失败",
    "result_unverified": "结果待核验",
    "legacy_result_unverified": "历史结果未绑定",
    "generating": "专家团正在生成",
    "cancelling": "正在停止专家团",
    "awaiting_stage_input": "等待补充阶段信息",
    "generated_invalid": "生成结果需要重新处理",
    "awaiting_review": "阶段成果待复核",
    "awaiting_local_confirmation": "等待本机确认",
    "delivery_validation_required": "正文已确认，等待文档交付",
    "revising": "正在按修改意见调整",
    "completed": "专家团任务已完成",
    "completion_reconciling": "正在恢复交付完成状态",
    "failed": "生成失败",
    "cancelled": "已取消",
    "completed_invalid": "已完成交付异常",
}

DOCUMENT_TYPE_LABELS = {
    "work_report": "工作汇报",
    "meeting_minutes": "会议纪要",
    "notice": "通知通报",
    "plan": "方案说明",
    "summary_plan": "总结计划",
    "other_office_material": "材料润色",
    "research_report": "研究报告",
}

_PUBLIC_BUILD_REF = re.compile(r"[A-Za-z0-9._-]{1,96}")


def _public_build_ref(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    return value if _PUBLIC_BUILD_REF.fullmatch(value) else "unknown"


def _stable_incident_id(run: dict, product_code: str) -> str:
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    reservation = (
        run.get("current_stage_attempt_reservation")
        if isinstance(run.get("current_stage_attempt_reservation"), dict)
        else {}
    )
    raw = ":".join(
        (
            str(run.get("run_id") or "unknown"),
            str(current.get("task_id") or current.get("id") or "unknown"),
            str(reservation.get("stage_attempt") or run.get("execution_attempt") or 0),
            str(product_code or "unknown_error"),
        )
    )
    return "inc-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
_GATE_STATUSES = {"pending", "running", "failed", "invalidated", "passed"}
_PROTOCOL_STAGE_ERROR_CODES = {
    "empty_response",
    "response_too_large",
    "invalid_block_count",
    "invalid_block_layout",
    "invalid_meta_json",
    "artifact_type_mismatch",
    "invalid_type",
    "unknown_field",
    "required_field_missing",
    "invalid_enum",
}


def _nested_brief_value(brief: dict, path: str):
    current = brief
    for segment in path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(segment)
    return current if isinstance(current, (str, int, float, bool)) else ""


def _has_current_local_delivery_confirmation(run: dict) -> bool:
    confirmation = run.get("local_delivery_confirmation")
    binding = run.get("current_delivery_manifest_ref")
    if not isinstance(confirmation, dict) or not isinstance(binding, dict):
        return False
    try:
        confirmation_attempt = int(confirmation.get("delivery_attempt") or 0)
        binding_attempt = int(binding.get("delivery_attempt") or 0)
    except (TypeError, ValueError):
        return False
    return (
        confirmation.get("schema_version") == "local-delivery-confirmation/v1"
        and confirmation_attempt > 0
        and confirmation_attempt == binding_attempt
        and str(confirmation.get("delivery_binding_sha256") or "")
        == str(binding.get("delivery_binding_sha256") or "")
        and len(str(confirmation.get("delivery_binding_sha256") or "")) == 64
        and len(str(confirmation.get("document_sha256") or "")) == 64
        and bool(str(confirmation.get("confirmed_at") or "").strip())
    )


def _effective_state(run: dict) -> str:
    state = str(run.get("workflow_state") or "collecting_required")
    if (
        state == "start_failed"
        and "本轮生成已结束，但没有检测到有效结果" in str(run.get("last_execution_error") or "")
    ):
        return "legacy_result_unverified"
    integrity = run.get("completion_integrity") if isinstance(run.get("completion_integrity"), dict) else {}
    if state == "completed" and run.get("completion_transaction_ref") and str(integrity.get("status") or "") != "passed":
        return "completion_reconciling"
    if state == "completed" and str(integrity.get("status") or "") in {"drifted", "unverified"}:
        return "completed_invalid"
    if (
        state == "completed"
        and str(run.get("product_mode") or "") == "standalone"
        and not _has_current_local_delivery_confirmation(run)
    ):
        return "awaiting_local_confirmation"
    return state


def _primary_action(state: str) -> dict | None:
    return {
        "collecting_required": {"id": "answer_required", "label": "去确认", "kind": "question_popover"},
        "collecting_optional": {"id": "answer_optional", "label": "补充或跳过", "kind": "question_popover"},
        "ready_to_generate": {"id": "start_generation", "label": "开始生成", "kind": "primary"},
        "starting": {"id": "cancel", "label": "停止启动", "kind": "danger"},
        "start_failed": {"id": "regenerate", "label": "重新尝试", "kind": "primary"},
        "generation_failed": {"id": "regenerate", "label": "重新生成", "kind": "primary"},
        "result_unverified": {"id": "refresh", "label": "重新核验结果", "kind": "primary"},
        "legacy_result_unverified": {"id": "regenerate_unverified", "label": "重新生成（已有结果保留）", "kind": "primary"},
        "generating": {"id": "cancel", "label": "停止生成", "kind": "danger"},
        "awaiting_stage_input": {"id": "submit_stage_input", "label": "确认并继续生成", "kind": "primary"},
        "generated_invalid": {"id": "regenerate", "label": "重新生成当前阶段", "kind": "primary"},
        "awaiting_review": {"id": "review_stage", "label": "去复核", "kind": "primary"},
        "revising": {"id": "cancel", "label": "停止生成", "kind": "danger"},
        "completed": {"id": "view_result", "label": "查看成果", "kind": "primary"},
        "completion_reconciling": {"id": "refresh", "label": "恢复完成状态", "kind": "primary"},
        "completed_invalid": {"id": "view_result", "label": "查看异常交付", "kind": "primary"},
    }.get(state)


def _is_final_stage(run: dict) -> bool:
    tasks = [task for task in run.get("tasks") or [] if isinstance(task, dict)]
    if not tasks:
        return False
    return int(run.get("current_stage_index") or 0) >= len(tasks) - 1


def _secondary_actions(state: str, run: dict | None = None) -> list[dict]:
    run = run or {}
    cleanup_status = str(run.get("execution_cleanup_status") or "").strip().lower()
    if str(run.get("orphan_runtime_run_id") or "").strip() and cleanup_status in {
        "pending",
        "unknown",
        "cancel_requested",
        "retry_required",
    }:
        if cleanup_status in {"unknown", "retry_required"}:
            return [{"id": "refresh", "label": "刷新清理状态", "kind": "ghost"}]
        return [{"id": "retry_cleanup", "label": "重试清理", "kind": "ghost"}]
    if state == "cancelling":
        if str(run.get("cancel_outcome") or "").strip().lower() in {"unknown", "retry_required"}:
            return [{"id": "refresh", "label": "刷新停止状态", "kind": "ghost"}]
        return []
    if state == "awaiting_review":
        standalone = str(run.get("product_mode") or "") == "standalone"
        if standalone and _stage_action_binding(run, "awaiting_stage_confirmation") is None:
            return [{"id": "view_result", "label": "查看成果", "kind": "ghost"}]
        approve_label = "确认当前成果" if standalone else (
            "无修改，完成任务" if _is_final_stage(run or {}) else "无修改，进入下一阶段"
        )
        return [
            {"id": "view_result", "label": "查看成果", "kind": "ghost"},
            {
                "id": "stage_confirm" if standalone else "approve_stage",
                "label": approve_label,
                "kind": "primary",
            },
            {
                "id": "stage_revise" if standalone else "revise_stage",
                "label": "需要修改",
                "kind": "ghost",
            },
        ]
    if state == "generated_invalid":
        return []
    if state == "result_unverified":
        return [{"id": "regenerate_unverified", "label": "放弃本次结果并重新生成", "kind": "ghost"}]
    return []


def _question_state(run: dict) -> dict:
    questions = [q for q in run.get("questions") or [] if isinstance(q, dict)]
    required_pending = [q for q in questions if q.get("required") and q.get("status") == "pending"]
    optional_pending = [q for q in questions if not q.get("required") and q.get("status") == "pending"]
    optional = next((q for q in questions if not q.get("required")), None)
    return {
        "required_pending": len(required_pending),
        "optional_pending": len(optional_pending),
        "optional_status": str((optional or {}).get("status") or "none"),
        "questions": deepcopy(questions),
    }


def _stage_output(run: dict) -> dict:
    outputs = [item for item in run.get("stage_outputs") or [] if isinstance(item, dict)]
    if not outputs:
        return {}
    output = deepcopy(outputs[-1])
    if str(output.get("status") or "") != "invalid":
        return output
    return {
        key: deepcopy(output[key])
        for key in (
            "task_id",
            "stage_id",
            "stage_attempt",
            "worker_id",
            "worker_name",
            "status",
            "created_at",
            "finished_at",
        )
        if key in output
    } | {
        "summary": _generated_invalid_detail(run),
        "content": "",
    }


def _latest_invalid_artifact_error(run: dict) -> dict:
    outputs = run.get("stage_outputs") if isinstance(run.get("stage_outputs"), list) else []
    for output in reversed(outputs):
        if not isinstance(output, dict) or str(output.get("status") or "") != "invalid":
            continue
        error = output.get("artifact_error") if isinstance(output.get("artifact_error"), dict) else {}
        return error
    return {}


def _latest_invalid_error_code(run: dict) -> str:
    validation = run.get("validation") if isinstance(run.get("validation"), dict) else {}
    code = str(validation.get("code") or "").strip()
    if code:
        return code
    return str(_latest_invalid_artifact_error(run).get("code") or "").strip()


def _is_protocol_stage_error(run: dict) -> bool:
    artifact_error = _latest_invalid_artifact_error(run)
    if str(artifact_error.get("code") or "").strip():
        return True
    return _latest_invalid_error_code(run) in _PROTOCOL_STAGE_ERROR_CODES
    return ""


def _generated_invalid_detail(run: dict) -> str:
    if _is_protocol_stage_error(run):
        return "本次生成结果格式不完整，系统没有采用这份内容。请重新生成当前阶段。"
    return "本次生成结果未满足当前阶段要求，系统没有采用这份内容。请重新生成当前阶段。"


def _generated_invalid_title(run: dict) -> str:
    if _is_protocol_stage_error(run):
        return "生成格式需要重新处理"
    return "阶段内容需要补充或调整"


def _public_validation(run: dict) -> dict:
    validation = run.get("validation") if isinstance(run.get("validation"), dict) else {}
    if str(run.get("workflow_state") or "") != "generated_invalid":
        return deepcopy(validation)
    return {
        "status": "rewrite_required",
        "code": _latest_invalid_error_code(run) or "generated_invalid",
        "message": _generated_invalid_detail(run),
    }


def _stage_result(run: dict) -> dict:
    output = _stage_output(run)
    if (
        str(run.get("workflow_state") or "") == "generated_invalid"
        and str(output.get("status") or "") == "invalid"
    ):
        return {
            "stage_id": str(output.get("task_id") or output.get("stage_id") or ""),
            "worker_id": str(output.get("worker_id") or ""),
            "summary": _generated_invalid_detail(run),
            "deliverable": "",
            "review_items": [],
            "next_action": "重新生成当前阶段。",
            "validation": _public_validation(run),
        }
    results = [item for item in run.get("stage_results") or [] if isinstance(item, dict)]
    if results:
        return deepcopy(results[-1])
    result = run.get("stage_result")
    if isinstance(result, dict):
        return deepcopy(result)
    if not output:
        return {}
    return {
        "stage_id": str(output.get("task_id") or output.get("stage_id") or ""),
        "worker_id": str(output.get("worker_id") or ""),
        "summary": str(output.get("summary") or content_summary(str(output.get("content") or ""))),
        "deliverable": str(output.get("content") or ""),
        "review_items": [],
        "next_action": "请复核当前阶段成果。",
        "validation": _public_validation(run),
    }


_REVIEW_STATUS_LABELS = {
    "included": "已纳入",
    "excluded": "未纳入",
    "verified": "已核验",
    "provided_unverified": "已提供，待核验",
    "missing": "缺失",
    "conflicted": "存在冲突",
    "insufficient": "证据不足",
    "open": "待处理",
    "resolved": "已解决",
    "covered_by_provided_sources": "已有资料覆盖",
    "accepted_out_of_scope": "已确认不在本次范围",
    "fact": "事实",
    "estimate": "估算",
    "judgment": "判断",
    "provided_only": "仅使用已提供资料",
    "approved_external": "允许使用已批准的外部资料",
    "footnote": "脚注",
    "inline": "正文内标注",
}


def _review_label(value) -> str:
    text = str(value or "").strip()
    return _REVIEW_STATUS_LABELS.get(text, text)


def _review_values(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _append_review_list(lines: list[str], title: str, value) -> None:
    values = _review_values(value)
    if not values:
        return
    lines.extend(["", f"{title}："])
    lines.extend(f"- {item}" for item in values)


def _append_source_assessments(lines: list[str], value) -> None:
    items = [item for item in value or [] if isinstance(item, dict)]
    if not items:
        return
    lines.extend(["", "资料评估："])
    for index, item in enumerate(items, start=1):
        status = _review_label(item.get("status")) or "待评估"
        grade = str(item.get("evidence_grade") or "").strip()
        heading = f"- 资料 {index}：{status}"
        if grade:
            heading += f"（证据等级 {grade}）"
        lines.append(heading)
        applicability = str(item.get("applicability") or item.get("reason") or "").strip()
        if applicability:
            lines.append(f"  适用性：{applicability}")
        exclusion_reason = str(item.get("exclusion_reason") or "").strip()
        if exclusion_reason:
            lines.append(f"  未纳入原因：{exclusion_reason}")


def _append_gaps(lines: list[str], title: str, value) -> None:
    items = [item for item in value or [] if isinstance(item, dict)]
    if not items:
        return
    lines.extend(["", f"{title}："])
    for index, item in enumerate(items, start=1):
        question = str(item.get("question") or item.get("description") or "").strip()
        lines.append(f"- {question or f'待补信息 {index}'}")
        reason = str(item.get("reason") or "").strip()
        if reason:
            lines.append(f"  原因：{reason}")
        status = _review_label(item.get("resolution_status"))
        if status:
            lines.append(f"  处理状态：{status}")
        if item.get("blocks_final") is True:
            lines.append("  影响：补充后才能形成最终结论")


def _artifact_review_content(artifact: dict) -> str:
    deliverable = str(artifact.get("deliverable_markdown") or "").strip()
    if deliverable:
        return deliverable
    payload = artifact.get("payload") if isinstance(artifact.get("payload"), dict) else {}
    summary = str(artifact.get("summary") or "").strip()
    lines = [summary] if summary else []
    artifact_type = str(artifact.get("artifact_type") or "")

    if artifact_type == "writing_plan":
        objective = str(payload.get("objective") or "").strip()
        if objective:
            lines.extend(["", f"写作目标：{objective}"])
        document_type = DOCUMENT_TYPE_LABELS.get(str(payload.get("document_type") or ""), "")
        if document_type:
            lines.append(f"文档类型：{document_type}")
        sections = [item for item in payload.get("section_plan") or [] if isinstance(item, dict)]
        if sections:
            lines.extend(["", "章节安排："])
            for index, item in enumerate(sections, start=1):
                heading = str(item.get("heading") or "").strip() or f"第 {index} 章"
                lines.append(f"{index}. {heading}")
                purpose = str(item.get("purpose") or "").strip()
                if purpose:
                    lines.append(f"   目的：{purpose}")
        facts = [item for item in payload.get("fact_requirements") or [] if isinstance(item, dict)]
        if facts:
            lines.extend(["", "事实与资料要求："])
            for item in facts:
                description = str(item.get("description") or "").strip()
                if not description:
                    continue
                requirement = "必需" if item.get("required") is True else "可选"
                lines.append(f"- {description}（{requirement}）")
        _append_review_list(lines, "假设与待确认", payload.get("assumptions"))
        _append_review_list(lines, "验收标准", payload.get("acceptance_checks"))

    elif artifact_type == "material_ledger":
        _append_source_assessments(lines, payload.get("source_assessments"))
        facts = [item for item in payload.get("facts") or [] if isinstance(item, dict)]
        if facts:
            lines.extend(["", "事实台账："])
            for item in facts:
                statement = str(item.get("statement") or "").strip()
                if statement:
                    status = _review_label(item.get("status")) or "待核验"
                    lines.append(f"- {statement}（{status}）")
        _append_gaps(lines, "待补信息", payload.get("gaps"))

    elif artifact_type == "research_charter":
        core_question = str(payload.get("core_question") or "").strip()
        decision = str(payload.get("decision_to_support") or "").strip()
        if core_question:
            lines.extend(["", f"核心问题：{core_question}"])
        if decision:
            lines.append(f"支持决策：{decision}")
        time_range = payload.get("time_range") if isinstance(payload.get("time_range"), dict) else {}
        start = str(time_range.get("start") or "").strip()
        end = str(time_range.get("end") or "").strip()
        if start or end:
            lines.append(f"时间范围：{start or '未指定'} 至 {end or '未指定'}")
        source_policy = payload.get("source_policy") if isinstance(payload.get("source_policy"), dict) else {}
        source_mode = _review_label(source_policy.get("mode"))
        as_of_date = str(source_policy.get("as_of_date") or "").strip()
        if source_mode:
            lines.append(f"资料范围：{source_mode}")
        if as_of_date:
            lines.append(f"资料截止日期：{as_of_date}")
        _append_review_list(lines, "纳入范围", payload.get("scope_in"))
        _append_review_list(lines, "不纳入范围", payload.get("scope_out"))
        _append_review_list(lines, "研究子问题", payload.get("subquestions"))
        _append_review_list(lines, "评估标准", payload.get("evaluation_criteria"))
        _append_review_list(lines, "停止条件", payload.get("stop_conditions"))

    elif artifact_type == "source_register":
        _append_source_assessments(lines, payload.get("source_assessments"))
        _append_gaps(lines, "待补资料", payload.get("search_gaps"))

    elif artifact_type == "evidence_matrix":
        claims = [item for item in payload.get("claims") or [] if isinstance(item, dict)]
        if claims:
            lines.extend(["", "核心判断与证据状态："])
            for item in claims:
                statement = str(item.get("statement") or "").strip()
                if not statement:
                    continue
                claim_type = _review_label(item.get("claim_type"))
                status = _review_label(item.get("status"))
                suffix = "、".join(part for part in (claim_type, status) if part)
                lines.append(f"- {statement}{f'（{suffix}）' if suffix else ''}")
        contradictions = [item for item in payload.get("contradictions") or [] if isinstance(item, dict)]
        if contradictions:
            lines.extend(["", "证据冲突："])
            for item in contradictions:
                description = str(item.get("description") or "").strip()
                if not description:
                    continue
                status = _review_label(item.get("resolution_status"))
                lines.append(f"- {description}{f'（{status}）' if status else ''}")
                resolution = str(item.get("resolution") or "").strip()
                if resolution:
                    lines.append(f"  处理结论：{resolution}")
        _append_gaps(lines, "证据缺口", payload.get("gaps"))

    elif artifact_type == "research_outline":
        sections = [item for item in payload.get("sections") or [] if isinstance(item, dict)]
        if sections:
            lines.extend(["", "报告提纲："])
            for index, item in enumerate(sections, start=1):
                heading = str(item.get("heading") or "").strip() or f"第 {index} 章"
                lines.append(f"{index}. {heading}")
                thesis = str(item.get("thesis") or "").strip()
                if thesis:
                    lines.append(f"   核心观点：{thesis}")
                questions = _review_values(item.get("open_questions"))
                if questions:
                    lines.append(f"   待核问题：{'；'.join(questions)}")
        _append_review_list(lines, "结论边界", payload.get("conclusion_boundaries"))

    return "\n".join(lines).strip()


def _enterprise_stage_result(run: dict) -> dict:
    from .issue_policy import effective_artifact_validation, public_stage_issues

    ref = run.get("current_stage_artifact_ref") if isinstance(run.get("current_stage_artifact_ref"), dict) else {}
    artifacts = [item for item in run.get("stage_artifacts") or [] if isinstance(item, dict)]
    artifact = next(
        (
            item for item in reversed(artifacts)
            if not ref or (item.get("artifact_id") == ref.get("artifact_id") and item.get("sha256") == ref.get("sha256"))
        ),
        None,
    )
    if not isinstance(artifact, dict):
        return {}
    quality = effective_artifact_validation(artifact)
    semantic_validation = _active_semantic_validation(run, artifact)
    semantic_issues = [
        {
            "code": str(item.get("code") or "semantic_issue"),
            "severity": "blocking",
            "message": str(item.get("message") or "内容检查未通过"),
            "suggested_action": str(
                item.get("suggested_action")
                or "提交修改意见后重新生成当前阶段"
            ),
        }
        for item in semantic_validation.get("issues") or []
        if isinstance(item, dict)
    ]
    blocking_count = int(quality["blocking_count"]) + len(semantic_issues)
    public_issues = public_stage_issues(artifact.get("blocking_issues") or [])
    public_issues.extend(semantic_issues)
    approved_ref = (run.get("approved_stage_artifact_refs") or {}).get(str(artifact.get("stage_id") or ""))
    return {
        "stage_id": str(artifact.get("stage_id") or ""),
        "artifact_type": str(artifact.get("artifact_type") or ""),
        "stage_attempt": int(artifact.get("stage_attempt") or 0),
        "summary": str(artifact.get("summary") or ""),
        "deliverable": str(artifact.get("deliverable_markdown") or ""),
        "content": _artifact_review_content(artifact),
        "validation": {
            "status": "invalid" if semantic_issues else quality["status"],
            "blocking_count": blocking_count,
            "warning_count": quality["warning_count"],
        },
        "stage_quality": {
            "state": "blocked" if semantic_issues else quality["state"],
            "blocking_count": blocking_count,
            "warning_count": quality["warning_count"],
            "issues": public_issues,
        },
        "approved_ref": deepcopy(approved_ref) if isinstance(approved_ref, dict) else None,
    }


def _active_semantic_validation(
    run: dict,
    artifact: dict | None = None,
) -> dict:
    validation = (
        run.get("semantic_validation")
        if isinstance(run.get("semantic_validation"), dict)
        else {}
    )
    if validation.get("status") != "failed":
        return {}
    candidate = artifact if isinstance(artifact, dict) else None
    if candidate is None:
        ref = (
            run.get("current_stage_artifact_ref")
            if isinstance(run.get("current_stage_artifact_ref"), dict)
            else {}
        )
        artifact_id = str(ref.get("artifact_id") or "")
        artifact_sha256 = str(ref.get("sha256") or "")
    else:
        artifact_id = str(candidate.get("artifact_id") or "")
        artifact_sha256 = str(candidate.get("sha256") or "")
    if (
        str(validation.get("artifact_id") or "") != artifact_id
        or str(validation.get("artifact_sha256") or "") != artifact_sha256
    ):
        return {}
    return validation


def _semantic_recheck_allowed(run: dict) -> bool:
    validation = _active_semantic_validation(run)
    if not validation:
        return False
    issues = [
        item
        for item in validation.get("issues") or []
        if isinstance(item, dict)
    ]
    if not issues or any(
        str(item.get("code") or "") != "review_issue_unresolved"
        for item in issues
    ):
        return False
    profile = (
        run.get("launch_profile_snapshot")
        if isinstance(run.get("launch_profile_snapshot"), dict)
        else {}
    )
    from .issue_policy import brief_allows_labeled_placeholders

    return brief_allows_labeled_placeholders(
        run.get("document_brief"),
        profile.get("source_requirement"),
        product_mode=str(run.get("product_mode") or ""),
    )


def _stage_review(run: dict, state: str) -> dict:
    output = _stage_output(run)
    actionable = state == "awaiting_review"
    display_state = "awaiting_review" if actionable else (
        "running" if state in {"ready_to_generate", "generating", "revising", "cancelling"} else state
    )
    return {
        "display_state": display_state,
        "actionable": actionable,
        "output": output,
    }


def _presentation(run: dict, business_context: dict) -> dict:
    state = _effective_state(run)
    standalone = str(run.get("product_mode") or "") == "standalone"
    output = _stage_output(run)
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    detail = ""
    if state in {"collecting_required", "collecting_optional"}:
        detail = "请先补充需求信息，专家团再继续推进。"
    elif state == "ready_to_generate":
        detail = str(run.get("last_execution_error") or "已准备好生成当前阶段内容。")
    elif state == "starting":
        detail = "正在建立当前阶段执行连接。"
    elif state == "start_failed":
        detail = str(run.get("last_execution_error") or "当前阶段启动失败，请重新尝试。")
    elif state == "generation_failed":
        detail = str(run.get("last_execution_error") or "当前阶段生成失败，请重新生成。")
    elif state == "result_unverified":
        detail = str(run.get("last_execution_error") or "生成已结束，正在核验结果归属；不会自动重复生成。")
    elif state == "legacy_result_unverified":
        detail = "已在对话中发现历史生成结果，但旧记录缺少安全绑定身份，系统不会自动认领。已有内容会保留。"
    elif state in {"generating", "revising"}:
        detail = "后台正在按当前阶段生成内容。"
    elif state == "cancelling":
        detail = "停止请求已提交，正在等待执行侧确认。"
    elif state == "awaiting_stage_input":
        pending = _pending_input(run)
        detail = str(pending.get("description") or pending.get("question") or "当前专家需要你确认后继续生成。")
    elif state == "generated_invalid":
        detail = _generated_invalid_detail(run)
    elif state == "awaiting_review":
        validation = run.get("validation") if isinstance(run.get("validation"), dict) else {}
        if standalone:
            detail = "阶段成果已生成，请在本机确认后继续。"
        elif str(validation.get("status") or "") == "office_acceptance_required":
            detail = str(run.get("last_validation_error") or "请完成 WPS/Word 验收后再确认交付。")
        else:
            detail = "阶段结果已生成，请查看后确认是否进入下一阶段。"
    elif state == "awaiting_local_confirmation":
        detail = "文档已生成，请在本机确认后完成任务。"
    elif state == "delivery_validation_required":
        detail = (
            "正文已在本机确认，正在等待系统生成并校验 DOCX 交付物。"
            if standalone
            else "正文语义已由受信人员确认，正在等待系统生成并校验唯一 DOCX 交付物。"
        )
    elif state == "completed":
        detail = "所有阶段已完成，结果已写入当前对话。"
    elif state == "completion_reconciling":
        detail = (
            "正在恢复本机交付状态，核验完成前不会显示任务完成。"
            if standalone
            else "Office 验收证据正在对账恢复，摘要闭合前不会显示企业完成。"
        )
    elif state == "completed_invalid":
        integrity = run.get("completion_integrity") if isinstance(run.get("completion_integrity"), dict) else {}
        detail = str(integrity.get("message") or "已完成交付文件缺失或摘要已变化，请勿继续按已验收结果使用。")
    elif state in {"failed", "cancelled"}:
        detail = str(run.get("last_execution_error") or STATE_LABELS.get(state) or "")
    primary_action = _primary_action(state)
    cleanup_status = str(run.get("execution_cleanup_status") or "").strip().lower()
    if str(run.get("orphan_runtime_run_id") or "").strip() and cleanup_status in {
        "pending",
        "unknown",
        "cancel_requested",
        "retry_required",
    }:
        primary_action = (
            {"id": "retry_cleanup", "label": "重试清理", "kind": "primary"}
            if cleanup_status in {"unknown", "retry_required"}
            else {"id": "refresh", "label": "刷新清理状态", "kind": "primary"}
        )
    elif state == "cancelling":
        retry_cancel = str(run.get("cancel_outcome") or "").strip().lower() in {
            "unknown",
            "retry_required",
        }
        if standalone:
            retry_cancel = _cancel_action_binding(run, state) is not None
        primary_action = (
            {"id": "retry_cancel", "label": "重试停止", "kind": "danger"}
            if retry_cancel
            else {"id": "refresh", "label": "刷新停止状态", "kind": "primary"}
        )
    title = STATE_LABELS.get(state, "专家团状态")
    if standalone:
        completion_integrity = (
            run.get("completion_integrity")
            if isinstance(run.get("completion_integrity"), dict)
            else {}
        )
        title = {
            "awaiting_review": "成果待本机确认",
            "generated_invalid": _generated_invalid_title(run),
            "completion_reconciling": "正在恢复交付状态",
            "completed_invalid": "交付文档已变化",
        }.get(state, title)
        if state == "completed_invalid" and str(completion_integrity.get("status") or "") == "unverified":
            title = "交付确认缺失"
            primary_action = None
        if state == "completed_invalid" and _delivery_recovery_binding(run, state) is not None:
            primary_action = {
                "id": "delivery_recover",
                "label": "重新生成 DOCX",
                "kind": "primary",
            }
        if state in {
            "start_failed",
            "generation_failed",
            "result_unverified",
            "legacy_result_unverified",
            "generated_invalid",
        }:
            primary_action = {
                "id": "resume",
                "label": "重新生成当前阶段" if state == "generated_invalid" else "重新尝试",
                "kind": "primary",
            }
    return {
        "state": state,
        "title": title,
        "visible_title": str(business_context.get("visible_title") or run.get("title") or "专家团任务"),
        "detail": detail,
        "primary_action": primary_action,
        "secondary_actions": _secondary_actions(state, run),
        "result": output,
        "summary": content_summary(str(output.get("content") or output.get("summary") or run.get("title") or "")),
        "current_stage": deepcopy(current),
        "progress_text": _progress_text(run, state),
    }


def _progress(run: dict) -> dict:
    tasks = [task for task in run.get("tasks") or [] if isinstance(task, dict)]
    done = sum(1 for task in tasks if str(task.get("status") or "") == "done")
    current = str(run.get("phase") or "")
    state = str(run.get("workflow_state") or "")
    current_index = int(run.get("current_stage_index") or 0)
    is_intake = current_index == 0 and state in {
        "collecting_required",
        "collecting_optional",
        "ready_to_generate",
        "starting",
        "start_failed",
    }
    if is_intake:
        done = 0
        current = "需求确认"
        current_index = 0
    return {
        "done": done,
        "total": len(tasks),
        "current": current,
        "current_index": current_index,
        "is_intake": is_intake,
    }


def _progress_text(run: dict, state: str | None = None) -> str:
    progress = _progress(run)
    total = int(progress.get("total") or 0)
    if not total:
        return "0/0"
    if progress.get("is_intake"):
        return f"0/{total}"
    if (state or str(run.get("workflow_state") or "")) == "completed":
        return f"{total}/{total}"
    done = int(progress.get("done") or 0)
    return f"{min(total, max(0, done))}/{total}"


def _current_worker(run: dict) -> dict:
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    worker_id = str(current.get("worker_id") or "")
    worker_name = str(current.get("worker_name") or "")
    for member in run.get("members") or []:
        if not isinstance(member, dict):
            continue
        if str(member.get("id") or "") == worker_id or str(member.get("name") or "") == worker_name:
            return deepcopy(member)
    return {"id": worker_id, "name": worker_name or "专家团", "role": "阶段负责", "status": ""}


def _workspace(run: dict) -> dict:
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    tasks = [deepcopy(task) for task in run.get("tasks") or [] if isinstance(task, dict)]
    members = [deepcopy(member) for member in run.get("members") or [] if isinstance(member, dict)]
    state = _effective_state(run)
    return {
        "visible": True,
        "title": "专家团工作台",
        "state": state,
        "current_stage": {
            "id": str(current.get("task_id") or current.get("id") or ""),
            "index": int(current.get("index") or 0),
            "title": str(current.get("title") or ""),
            "phase": str(current.get("phase") or ""),
            "status": str(current.get("status") or ""),
            "worker_id": str(current.get("worker_id") or ""),
            "worker_name": str(current.get("worker_name") or ""),
        },
        "current_worker": _current_worker(run),
        "phases": tasks,
        "members": members,
        "timeline": _timeline_events(run),
        "stage_result": _stage_result(run),
        "pending_input": _pending_input(run),
    }


def _team(run: dict) -> dict:
    return {
        "id": str(run.get("team_id") or ""),
        "title": str(run.get("team_title") or "专家团"),
        "image": str(run.get("team_image") or ""),
        "members": [deepcopy(member) for member in run.get("members") or [] if isinstance(member, dict)],
    }


def _workflow(run: dict) -> dict:
    tasks = [deepcopy(task) for task in run.get("tasks") or [] if isinstance(task, dict)]
    progress = _progress(run)
    progress["text"] = _progress_text(run, _effective_state(run))
    return {
        "stages": tasks,
        "current_stage": deepcopy(run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}),
        "current_index": int(progress.get("current_index") or 0),
        "total": len(tasks),
        "progress": progress,
    }


def _pending_input(run: dict) -> dict:
    pending = run.get("pending_input")
    if not isinstance(pending, dict):
        return {}
    return {
        "id": str(pending.get("id") or "stage-input"),
        "question": str(pending.get("question") or ""),
        "description": str(pending.get("description") or ""),
        "options": [str(item) for item in pending.get("options") or []],
        "required": pending.get("required", True) is not False,
        "stage_id": str(pending.get("stage_id") or ""),
        "worker_id": str(pending.get("worker_id") or ""),
        "created_at": str(pending.get("created_at") or ""),
    }


def _dock(run: dict, presentation: dict) -> dict:
    return {
        "state": presentation.get("state") or str(run.get("workflow_state") or ""),
        "title": presentation.get("title") or "专家团状态",
        "detail": presentation.get("detail") or "",
        "primary_action": deepcopy(presentation.get("primary_action")),
        "secondary_actions": deepcopy(presentation.get("secondary_actions") or []),
    }


def _timeline_events(run: dict) -> list[dict]:
    standalone = str(run.get("product_mode") or "") == "standalone"
    members = {
        str(member.get("id") or ""): member
        for member in run.get("members") or []
        if isinstance(member, dict)
    }
    rows = []
    for event in run.get("timeline_events") or run.get("events") or []:
        if not isinstance(event, dict):
            continue
        member_id = str(event.get("member_id") or "")
        member = members.get(member_id) or {}
        event_type = str(event.get("type") or "event")
        title = str(event.get("title") or event_type or "专家团动态")
        detail = str(event.get("detail") or "")
        if standalone and event_type == "office_acceptance_required":
            event_type = "local_confirmation_required"
            title = "等待本机确认"
            detail = "当前成果已生成，请在本机确认后继续。"
        rows.append(
            {
                "type": event_type,
                "title": title,
                "detail": detail,
                "member_id": member_id,
                "member_name": str(member.get("name") or ""),
                "member_image": str(member.get("image") or ""),
                "at": str(event.get("at") or ""),
            }
        )
    return rows


def _normalized_gate_status(value, default: str = "pending") -> str:
    status = str(value or "").strip().lower()
    if status == "passed_with_conditions":
        return "pending"
    if status in {"blocked", "error", "passed_with_warnings", "regeneration_required"}:
        return "failed"
    return status if status in _GATE_STATUSES else default


def _gate_issue_count(run: dict, names: set[str]) -> int:
    from .issue_policy import BLOCKING_SEVERITIES

    issues = run.get("enterprise_quality_issues") if isinstance(run.get("enterprise_quality_issues"), list) else []
    return sum(
        1
        for issue in issues
        if isinstance(issue, dict)
        and str(issue.get("gate") or issue.get("domain") or "") in names
        and str(issue.get("disposition") or "unresolved") != "resolved"
        and (
            issue.get("completion_blocking") is True
            or str(issue.get("severity") or "") in BLOCKING_SEVERITIES
        )
    )


def _canonical_content_passed(run: dict) -> bool:
    brief = run.get("document_brief") if isinstance(run.get("document_brief"), dict) else {}
    ref = run.get("canonical_document_ref") if isinstance(run.get("canonical_document_ref"), dict) else {}
    if (
        brief.get("status") != "confirmed"
        or not str(ref.get("artifact_id") or "")
        or not str(ref.get("sha256") or "")
        or int(ref.get("brief_revision") or 0) != int(brief.get("confirmed_revision") or 0)
        or str(ref.get("brief_sha256") or "") != str(brief.get("confirmed_sha256") or "")
    ):
        return False
    approvals = run.get("approved_stage_artifact_refs") if isinstance(run.get("approved_stage_artifact_refs"), dict) else {}
    expected = {"artifact_id": ref["artifact_id"], "sha256": ref["sha256"]}
    return any(value == expected for value in approvals.values())


def _completion_model(run: dict, *, enterprise: bool) -> tuple[dict, str, dict]:
    if not enterprise:
        gates = {
            name: {
                "status": "invalidated",
                "label": label,
                "reason_code": "legacy_contract_unverified",
                "blocking_issue_count": 0,
                "next_action": {"type": "view_result", "label": "查看历史成果"},
            }
            for name, label in (
                ("content", "历史内容未按企业合同验证"),
                ("document", "历史文档未按企业合同验证"),
                ("office", "历史任务无企业 Office 验收"),
            )
        }
        return gates, "legacy_unverified", {"type": "view_result", "label": "查看历史成果"}

    quality = run.get("enterprise_quality_gates") if isinstance(run.get("enterprise_quality_gates"), dict) else {}
    integrity = run.get("completion_integrity") if isinstance(run.get("completion_integrity"), dict) else {}
    content_blocking_count = _gate_issue_count(run, {"brief", "semantic", "evidence", "content"})
    if content_blocking_count:
        content_status = "failed"
    elif _canonical_content_passed(run):
        content_status = "passed"
    else:
        content_status = "failed" if run.get("canonical_document_ref") else "pending"

    upstream = [_normalized_gate_status(quality.get(name)) for name in ("brief", "semantic", "evidence", "asset", "render")]
    binding = run.get("current_delivery_manifest_ref") if isinstance(run.get("current_delivery_manifest_ref"), dict) else {}
    binding_closed = bool(str(binding.get("delivery_binding_sha256") or ""))
    if any(status in {"failed", "invalidated"} for status in upstream):
        document_status = "failed"
    elif any(status == "running" for status in upstream):
        document_status = "running"
    elif all(status == "passed" for status in upstream) and binding_closed:
        document_status = "passed"
    else:
        document_status = "pending"

    quality_office = _normalized_gate_status(quality.get("office"))
    transaction = run.get("completion_transaction_ref") if isinstance(run.get("completion_transaction_ref"), dict) else {}
    binding_attempt = int(binding.get("delivery_attempt") or 0)
    transaction_attempt = int(transaction.get("delivery_attempt") or 0)
    transaction_committed = (
        bool(str(transaction.get("transaction_id") or ""))
        and str(integrity.get("transaction_state") or "") == "committed"
        and integrity.get("summary_closed") is True
        and binding_attempt > 0
        and transaction_attempt == binding_attempt
    )
    if quality_office in {"failed", "invalidated"} or str(run.get("office_acceptance_status") or "") == "failed":
        office_status = "failed"
    elif transaction and binding_attempt > 0 and transaction_attempt != binding_attempt:
        office_status = "invalidated"
    elif (
        quality_office == "passed"
        and transaction_committed
        and str(integrity.get("status") or "") == "passed"
    ):
        office_status = "passed"
    elif str(run.get("office_acceptance_status") or "") == "running":
        office_status = "running"
    else:
        office_status = "pending"

    gates = {
        "content": {
            "status": content_status,
            "label": "内容已确认" if content_status == "passed" else "内容待确认",
            "reason_code": None if content_status == "passed" else (
                "content_blocking_issues" if content_blocking_count else "canonical_content_required"
            ),
            "blocking_issue_count": content_blocking_count,
            "next_action": {
                "type": "view_content" if content_status == "passed" else "review_content",
                "label": "查看已确认内容" if content_status == "passed" else "复核内容",
            },
        },
        "document": {
            "status": document_status,
            "label": "DOCX 自动检查通过" if document_status == "passed" else (
                "DOCX 自动检查未通过" if document_status == "failed" else "DOCX 自动检查待完成"
            ),
            "reason_code": None if document_status == "passed" else (
                "document_quality_failed" if document_status == "failed" else "document_quality_required"
            ),
            "blocking_issue_count": _gate_issue_count(run, {"asset", "render", "document"}),
            "next_action": {
                "type": "open_document" if document_status == "passed" else (
                    "repair_document" if document_status == "failed" else "wait_document"
                ),
                "label": "打开 DOCX" if document_status == "passed" else (
                    "处理 DOCX 自动检查问题" if document_status == "failed" else "等待生成文档"
                ),
            },
        },
        "office": {
            "status": office_status,
            "label": "Office 验收通过" if office_status == "passed" else (
                "Office 验收不通过" if office_status == "failed" else "待 Office 验收"
            ),
            "reason_code": None if office_status == "passed" else (
                "office_review_failed" if office_status == "failed" else (
                    "completion_transaction_mismatch" if office_status == "invalidated" else "office_review_required"
                )
            ),
            "blocking_issue_count": _gate_issue_count(run, {"office"}),
            "next_action": {
                "type": "view_office_acceptance" if office_status == "passed" else (
                    "repair_office" if office_status == "failed" else "open_office_review"
                ),
                "label": "查看 Office 验收" if office_status == "passed" else (
                    "处理 Office 验收问题" if office_status == "failed" else "开始 Office 验收"
                ),
            },
        },
    }
    all_passed = all(gate["status"] == "passed" for gate in gates.values())
    committed = (
        all_passed
        and str(run.get("workflow_state") or "") == "completed"
        and transaction_committed
        and str(integrity.get("status") or "") == "passed"
    )
    if committed:
        return gates, "passed", {"type": "view_result", "label": "查看完整成果"}
    if transaction and not transaction_committed:
        return gates, "finalizing", {"type": "reconcile_completion", "label": "恢复交付完成状态"}
    if content_status != "passed":
        return gates, "content_required", {"type": "review_content", "label": "复核内容"}
    if document_status == "failed":
        return gates, "document_failed", {"type": "repair_document", "label": "处理 DOCX 自动检查问题"}
    if document_status != "passed":
        return gates, "document_pending", {"type": "wait_document", "label": "等待生成文档"}
    if office_status == "failed":
        return gates, "office_failed", {"type": "repair_office", "label": "处理 Office 验收问题"}
    return gates, "office_review_required", {"type": "open_office_review", "label": "开始 Office 验收"}


def _standalone_completion_model(
    run: dict,
    delivery_binding: dict | None = None,
    delivery_recovery_binding: dict | None = None,
) -> tuple[dict, str, dict]:
    canonical = run.get("canonical_document_ref") if isinstance(run.get("canonical_document_ref"), dict) else {}
    content_blocking_count = _gate_issue_count(run, {"brief", "semantic", "evidence", "content"})
    content_passed = (
        bool(str(canonical.get("artifact_id") or ""))
        and len(str(canonical.get("sha256") or "")) == 64
        and content_blocking_count == 0
    )
    document_passed = delivery_binding is not None
    completed = (
        str(run.get("workflow_state") or "") == "completed"
        and _has_current_local_delivery_confirmation(run)
        and str((run.get("completion_integrity") or {}).get("status") or "") == "valid"
    )
    gates = {
        "content": {
            "status": "passed" if content_passed else "pending",
            "label": "内容已确认" if content_passed else "内容待确认",
            "reason_code": None if content_passed else "canonical_content_required",
            "blocking_issue_count": content_blocking_count,
            "next_action": {
                "type": "view_content" if content_passed else "review_content",
                "label": "查看已确认内容" if content_passed else "复核内容",
            },
        },
        "document": {
            "status": "passed" if document_passed else "pending",
            "label": "DOCX 自动检查通过" if document_passed else "DOCX 自动检查待完成",
            "reason_code": None if document_passed else "document_quality_required",
            "blocking_issue_count": 0,
            "next_action": {
                "type": "open_document" if document_passed else "wait_document",
                "label": "打开 DOCX" if document_passed else "等待生成文档",
            },
        },
        "local_confirmation": {
            "status": "passed" if completed else "pending",
            "label": "已在本机确认" if completed else "等待本机确认",
            "reason_code": None if completed else "local_confirmation_required",
            "blocking_issue_count": 0,
            "next_action": (
                {"type": "view_result", "label": "查看完整成果"}
                if completed
                else {"type": "wait_local_confirmation", "label": "等待本机确认"}
            ),
        },
    }
    integrity_status = str((run.get("completion_integrity") or {}).get("status") or "")
    if str(run.get("workflow_state") or "") == "completed" and integrity_status == "unverified":
        gates["document"] = {
            "status": "invalidated",
            "label": "DOCX 无法验证",
            "reason_code": "local_delivery_confirmation_missing",
            "blocking_issue_count": 1,
            "next_action": {"type": "none", "label": "当前交付无法自动恢复"},
        }
        gates["local_confirmation"] = {
            "status": "invalidated",
            "label": "本机确认缺失",
            "reason_code": "local_delivery_confirmation_missing",
            "blocking_issue_count": 1,
            "next_action": {"type": "none", "label": "请新建专家团任务"},
        }
        return gates, "delivery_unverified", {
            "type": "none",
            "label": "当前交付无法自动恢复，请新建专家团任务",
        }
    if delivery_recovery_binding is not None:
        gates["document"] = {
            "status": "invalidated",
            "label": "DOCX 已变化",
            "reason_code": "completed_delivery_digest_drift",
            "blocking_issue_count": 1,
            "next_action": {"type": "recover_delivery", "label": "重新生成 DOCX"},
        }
        gates["local_confirmation"] = {
            "status": "invalidated",
            "label": "原本机确认已失效",
            "reason_code": "completed_delivery_digest_drift",
            "blocking_issue_count": 1,
            "next_action": {"type": "recover_delivery", "label": "重新生成 DOCX"},
        }
        return gates, "delivery_drifted", {
            "type": "recover_delivery",
            "label": "重新生成 DOCX",
        }
    if completed:
        return gates, "passed", {"type": "view_result", "label": "查看完整成果"}
    content_status = str(gates["content"].get("status") or "pending")
    document_status = str(gates["document"].get("status") or "pending")
    if content_status != "passed":
        return gates, "content_required", {"type": "review_content", "label": "复核内容"}
    if document_status != "passed":
        return gates, "document_pending", {"type": "wait_document", "label": "等待生成文档"}
    return gates, "local_confirmation_required", {
        "type": "wait_local_confirmation",
        "label": "等待本机确认",
    }


def _brief_is_editable(run: dict) -> bool:
    state = str(run.get("workflow_state") or "collecting_required")
    if state not in {"collecting_required", "collecting_optional", "ready_to_generate"}:
        return False
    if run.get("stage_outputs"):
        return False
    if isinstance(run.get("current_stage_attempt_reservation"), dict):
        return False
    return not any(isinstance(item, dict) for item in run.get("stage_attempt_reservations") or [])


def _capability_model(run: dict, contract_version: str) -> dict:
    if str(run.get("product_mode") or "") == "standalone":
        return {"kind": "standalone", "label": "本机协作"}
    if contract_version != EXPERT_TEAM_CONTRACT_V1:
        return {"kind": "legacy", "label": "历史任务，未按企业合同验证"}
    document_type = str((run.get("document_brief") or {}).get("document_type") or run.get("document_type") or "")
    if document_type in DOCUMENT_TYPE_LABELS:
        return {"kind": "enterprise_pilot", "label": "企业合同试点"}
    return {"kind": "ai_draft", "label": "AI 草稿能力"}


def _current_stage_artifact(run: dict) -> dict:
    ref = run.get("current_stage_artifact_ref")
    if not isinstance(ref, dict):
        return {}
    candidates = [
        item
        for item in run.get("stage_artifacts") or []
        if isinstance(item, dict)
        and item.get("artifact_id") == ref.get("artifact_id")
        and item.get("sha256") == ref.get("sha256")
    ]
    return deepcopy(candidates[0]) if len(candidates) == 1 else {}


def _generated_invalid_needs_new_evidence(run: dict) -> bool:
    from .issue_policy import BLOCKING_SEVERITIES

    artifact = _current_stage_artifact(run)
    return any(
        isinstance(issue, dict)
        and str(issue.get("severity") or "").strip().lower() in BLOCKING_SEVERITIES
        and str(issue.get("category") or "").strip().lower() in {"evidence", "source"}
        for issue in artifact.get("blocking_issues") or []
    )


def _positive_int_or_zero(value) -> int:
    return value if type(value) is int and value > 0 else 0


def _public_state(run: dict, effective_state: str) -> str:
    artifact = _current_stage_artifact(run)
    if effective_state == "cancelling":
        return "cancelling"
    if effective_state == "failed":
        return "failed"
    if effective_state == "cancelled":
        return "cancelled"
    if effective_state in {"collecting_required", "collecting_optional"}:
        return "intake"
    if effective_state in {
        "ready_to_generate",
        "start_failed",
        "generation_failed",
        "result_unverified",
        "legacy_result_unverified",
        "awaiting_stage_input",
        "generated_invalid",
    }:
        return "ready"
    if effective_state in {"starting", "generating"}:
        return "executing"
    if effective_state == "revising":
        return "revising"
    if effective_state == "delivery_validation_required":
        return "generating_document"
    if effective_state == "awaiting_local_confirmation":
        return "awaiting_delivery_confirmation"
    if effective_state == "completed_invalid":
        integrity = run.get("completion_integrity") if isinstance(run.get("completion_integrity"), dict) else {}
        if str(integrity.get("status") or "") == "unverified":
            return "failed"
        return "awaiting_delivery_confirmation"
    if effective_state == "completion_reconciling":
        return "awaiting_delivery_confirmation"
    if effective_state == "awaiting_review":
        if str(artifact.get("artifact_type") or "") == "delivery_manifest":
            return "awaiting_delivery_confirmation"
        return "awaiting_stage_confirmation"
    if effective_state == "completed":
        return "completed"
    return "ready"


def _stage_action_binding(run: dict, public_state: str) -> dict | None:
    if str(run.get("product_mode") or "") != "standalone":
        return None
    if public_state != "awaiting_stage_confirmation":
        return None
    artifact = _current_stage_artifact(run)
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    if not artifact:
        return None
    ref = run.get("current_stage_artifact_ref")
    reservation = run.get("current_stage_attempt_reservation")
    stage_id = str(current.get("task_id") or current.get("id") or "")
    attempt = _positive_int_or_zero(artifact.get("stage_attempt"))
    try:
        from .issue_policy import effective_artifact_validation
        from .stage_artifacts import validate_stage_artifact

        validate_stage_artifact(
            artifact,
            brief=run.get("document_brief") or {},
            approved_inputs=artifact.get("input_refs") or [],
        )
        effective_validation = effective_artifact_validation(artifact)
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(ref, dict)
        or not attempt
        or _positive_int_or_zero(ref.get("stage_attempt")) != attempt
        or str(artifact.get("stage_id") or "") != stage_id
        or effective_validation["status"] != "valid"
        or effective_validation["blocking_count"] != 0
        or not isinstance(reservation, dict)
        or str(reservation.get("stage_id") or "") != stage_id
        or _positive_int_or_zero(reservation.get("stage_attempt")) != attempt
        or str(reservation.get("artifact_type") or "") != str(artifact.get("artifact_type") or "")
        or reservation.get("input_refs") != artifact.get("input_refs")
        or str(reservation.get("status") or "") != "generated_valid"
    ):
        return None
    reservation_id = str(reservation.get("reservation_id") or "")
    if not reservation_id:
        return None
    ledger_matches = [
        item
        for item in run.get("stage_attempt_reservations") or []
        if isinstance(item, dict)
        and str(item.get("reservation_id") or "") == reservation_id
        and str(item.get("stage_id") or "") == stage_id
        and _positive_int_or_zero(item.get("stage_attempt")) == attempt
        and str(item.get("artifact_type") or "") == str(artifact.get("artifact_type") or "")
        and item.get("input_refs") == artifact.get("input_refs")
        and str(item.get("status") or "") == "generated_valid"
    ]
    if len(ledger_matches) != 1:
        return None
    return {
        "session_id": str(run.get("session_id") or ""),
        "run_id": str(run.get("run_id") or ""),
        "expected_version": int(run.get("version") or 0),
        "stage_id": stage_id,
        "stage_attempt": attempt,
        "artifact_id": str(artifact.get("artifact_id") or ""),
        "artifact_sha256": str(artifact.get("sha256") or ""),
    }


def _delivery_action_binding(run: dict, public_state: str) -> dict | None:
    """Expose a standalone delivery action only when every durable identity agrees."""

    if str(run.get("product_mode") or "") != "standalone":
        return None
    if public_state not in {"awaiting_delivery_confirmation", "completed"}:
        return None
    ref = run.get("current_delivery_manifest_ref")
    stage_ref = run.get("current_stage_artifact_ref")
    if not isinstance(ref, dict) or not isinstance(stage_ref, dict):
        return None
    expected_ref_fields = {
        "artifact_id",
        "sha256",
        "stage_attempt",
        "delivery_attempt",
        "delivery_binding_path",
        "delivery_binding_sha256",
    }
    if set(ref) != expected_ref_fields:
        return None
    artifact_id = str(ref.get("artifact_id") or "")
    artifact_sha256 = str(ref.get("sha256") or "")
    stage_attempt = _positive_int_or_zero(ref.get("stage_attempt"))
    delivery_attempt = _positive_int_or_zero(ref.get("delivery_attempt"))
    binding_sha256 = str(ref.get("delivery_binding_sha256") or "")
    if (
        not artifact_id
        or not stage_attempt
        or not delivery_attempt
        or len(artifact_sha256) != 64
        or len(binding_sha256) != 64
        or str(stage_ref.get("artifact_id") or "") != artifact_id
        or str(stage_ref.get("sha256") or "") != artifact_sha256
        or _positive_int_or_zero(stage_ref.get("stage_attempt")) != stage_attempt
    ):
        return None
    artifacts = [
        item
        for item in run.get("stage_artifacts") or []
        if isinstance(item, dict)
        and str(item.get("artifact_id") or "") == artifact_id
        and str(item.get("sha256") or "") == artifact_sha256
    ]
    if len(artifacts) != 1:
        return None
    artifact = artifacts[0]
    payload = artifact.get("payload") if isinstance(artifact.get("payload"), dict) else {}
    try:
        from .issue_policy import effective_artifact_validation
        from .stage_artifacts import StageArtifactError, validate_stage_artifact

        validation = validate_stage_artifact(
            artifact,
            brief=run.get("document_brief") or {},
            approved_inputs=artifact.get("input_refs") or [],
        )
    except (StageArtifactError, TypeError, ValueError):
        return None
    effective_validation = effective_artifact_validation(artifact)
    summary = payload.get("automatic_check_summary") if isinstance(payload.get("automatic_check_summary"), dict) else {}
    stage_id = str(artifact.get("stage_id") or "")
    document_sha256 = str(payload.get("document_sha256") or "")
    if (
        artifact.get("artifact_type") != "delivery_manifest"
        or effective_validation["status"] != "valid"
        or int(validation.get("blocking_count") or 0) != 0
        or _positive_int_or_zero(artifact.get("stage_attempt")) != stage_attempt
        or payload.get("schema_version") != "delivery-manifest/v2"
        or payload.get("product_mode") != "standalone"
        or _positive_int_or_zero(payload.get("delivery_attempt")) != delivery_attempt
        or str(payload.get("delivery_binding_path") or "") != str(ref.get("delivery_binding_path") or "")
        or str(payload.get("delivery_binding_sha256") or "") != binding_sha256
        or len(document_sha256) != 64
        or summary.get("status") != "passed"
        or any(int(summary.get(field) or 0) != 0 for field in ("failed_count", "warning_count", "blocking_count"))
    ):
        return None
    descriptor = run.get("pending_system_stage")
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("executor") != "system"
        or str(descriptor.get("id") or "") != stage_id
        or descriptor.get("artifact_type") != "delivery_manifest"
    ):
        return None
    stage_reservation = run.get("current_stage_attempt_reservation")
    expected_stage_status = "confirmed" if public_state == "completed" else "generated_valid"
    if (
        not isinstance(stage_reservation, dict)
        or str(stage_reservation.get("stage_id") or "") != stage_id
        or _positive_int_or_zero(stage_reservation.get("stage_attempt")) != stage_attempt
        or str(stage_reservation.get("artifact_type") or "") != "delivery_manifest"
        or stage_reservation.get("input_refs") != artifact.get("input_refs")
        or str(stage_reservation.get("status") or "") != expected_stage_status
    ):
        return None
    stage_reservation_id = str(stage_reservation.get("reservation_id") or "")
    stage_matches = [
        item
        for item in run.get("stage_attempt_reservations") or []
        if isinstance(item, dict)
        and str(item.get("reservation_id") or "") == stage_reservation_id
        and item == stage_reservation
    ]
    if not stage_reservation_id or len(stage_matches) != 1:
        return None
    delivery_reservation = run.get("current_delivery_attempt_reservation")
    expected_delivery_status = "confirmed" if public_state == "completed" else "generated_valid"
    if (
        not isinstance(delivery_reservation, dict)
        or _positive_int_or_zero(delivery_reservation.get("delivery_attempt")) != delivery_attempt
        or str(delivery_reservation.get("render_input_fingerprint") or "")
        != str(payload.get("render_input_fingerprint") or "")
        or str(delivery_reservation.get("status") or "") != expected_delivery_status
    ):
        return None
    delivery_reservation_id = str(delivery_reservation.get("reservation_id") or "")
    delivery_matches = [
        item
        for item in run.get("delivery_attempt_reservations") or []
        if isinstance(item, dict)
        and str(item.get("reservation_id") or "") == delivery_reservation_id
        and item == delivery_reservation
    ]
    if not delivery_reservation_id or len(delivery_matches) != 1:
        return None
    return {
        "session_id": str(run.get("session_id") or ""),
        "run_id": str(run.get("run_id") or ""),
        "expected_version": int(run.get("version") or 0),
        "stage_id": stage_id,
        "stage_attempt": stage_attempt,
        "artifact_id": artifact_id,
        "artifact_sha256": artifact_sha256,
        "delivery_attempt": delivery_attempt,
        "delivery_binding_sha256": binding_sha256,
        "document_sha256": document_sha256,
    }


def _standalone_delivery_summary(run: dict, delivery_binding: dict | None) -> dict | None:
    if delivery_binding is None:
        return None
    artifacts = [
        item
        for item in run.get("stage_artifacts") or []
        if isinstance(item, dict)
        and item.get("artifact_id") == delivery_binding.get("artifact_id")
        and item.get("sha256") == delivery_binding.get("artifact_sha256")
    ]
    if len(artifacts) != 1:
        return None
    payload = artifacts[0].get("payload") if isinstance(artifacts[0].get("payload"), dict) else {}
    brief = run.get("document_brief") if isinstance(run.get("document_brief"), dict) else {}
    title = str(brief.get("exact_title") or run.get("title") or "最终文档").strip()
    return {
        "document_name": f"{title}.docx",
        "delivery_attempt": int(delivery_binding.get("delivery_attempt") or 0),
        "document_sha256": str(delivery_binding.get("document_sha256") or ""),
        "automatic_check_summary": deepcopy(payload.get("automatic_check_summary") or {}),
        "quality_report_sha256": str(payload.get("standalone_quality_report_sha256") or ""),
    }


def _delivery_recovery_binding(run: dict, effective_state: str) -> dict | None:
    """Bind drift recovery to the exact locally confirmed delivery identity."""

    integrity = run.get("completion_integrity") if isinstance(run.get("completion_integrity"), dict) else {}
    if (
        str(run.get("product_mode") or "") != "standalone"
        or str(run.get("workflow_state") or "") != "completed"
        or effective_state != "completed_invalid"
        or str(integrity.get("status") or "") != "drifted"
    ):
        return None
    confirmation = (
        run.get("local_delivery_confirmation")
        if isinstance(run.get("local_delivery_confirmation"), dict)
        else {}
    )
    if confirmation.get("schema_version") != "local-delivery-confirmation/v1":
        return None
    durable_binding = _delivery_action_binding(run, "completed")
    if durable_binding is None:
        return None
    confirmed_identity = {
        field: confirmation.get(field)
        for field in (
            "session_id",
            "run_id",
            "stage_id",
            "stage_attempt",
            "artifact_id",
            "artifact_sha256",
            "delivery_attempt",
            "delivery_binding_sha256",
            "document_sha256",
        )
    }
    authoritative_identity = {
        field: durable_binding.get(field)
        for field in confirmed_identity
    }
    if confirmed_identity != authoritative_identity:
        return None
    return durable_binding


def _cancel_action_binding(run: dict, effective_state: str) -> dict | None:
    """Return the only request that can safely retry an uncertain cancellation."""
    if str(run.get("product_mode") or "") != "standalone":
        return None
    if effective_state != "cancelling":
        return None
    if str(run.get("cancel_outcome") or "").strip().lower() not in {
        "unknown",
        "retry_required",
    }:
        return None

    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    task_id = str(current.get("task_id") or "").strip()
    legacy_id = str(current.get("id") or "").strip()
    if task_id and legacy_id and task_id != legacy_id:
        return None
    stage_id = task_id or legacy_id
    session_id = str(run.get("session_id") or "").strip()
    run_id = str(run.get("run_id") or "").strip()
    request_id = str(run.get("cancel_request_id") or "").strip()
    expected_version = _positive_int_or_zero(run.get("version"))
    if not all((session_id, run_id, stage_id, request_id, expected_version)):
        return None

    # runtime._request_fingerprint deliberately excludes expected_version and
    # idempotency_key.  Rebuilding the canonical original identity here makes
    # any missing field, extra-field request, or persisted drift fail closed.
    fingerprint_body = {
        "run_id": run_id,
        "session_id": session_id,
        "stage_id": stage_id,
    }
    raw = json.dumps(
        {"action": "cancel", "body": fingerprint_body},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    expected_fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if str(run.get("cancel_request_fingerprint") or "") != expected_fingerprint:
        return None

    return {
        "session_id": session_id,
        "run_id": run_id,
        "expected_version": expected_version,
        "stage_id": stage_id,
        "idempotency_key": request_id,
    }


def _allowed_actions(
    run: dict,
    effective_state: str,
    public_state: str,
    stage_binding: dict | None,
    cancel_binding: dict | None,
    delivery_binding: dict | None,
    delivery_recovery_binding: dict | None,
) -> list[str]:
    standalone = str(run.get("product_mode") or "") == "standalone"
    if not standalone:
        return []
    if effective_state in {"collecting_required", "collecting_optional"}:
        return ["answer"]
    if effective_state == "ready_to_generate":
        return ["start_generation"]
    if effective_state == "awaiting_stage_input":
        return ["submit_stage_input"]
    if effective_state in {
        "start_failed",
        "generation_failed",
        "result_unverified",
        "legacy_result_unverified",
        "generated_invalid",
    }:
        return ["resume"]
    if effective_state in {"starting", "generating", "revising"}:
        return ["cancel"]
    if effective_state == "cancelling":
        actions = ["refresh"]
        if cancel_binding is not None:
            actions.append("retry_cancel")
        return actions
    if effective_state == "completed_invalid" and delivery_recovery_binding is not None:
        return ["delivery_recover"]
    if public_state == "awaiting_stage_confirmation" and stage_binding is not None:
        if _active_semantic_validation(run):
            if _semantic_recheck_allowed(run):
                return ["stage_recheck", "stage_revise"]
            return ["stage_revise"]
        return ["stage_confirm", "stage_revise"]
    if public_state == "awaiting_delivery_confirmation" and delivery_binding is not None:
        return [
            "delivery_open_document",
            "delivery_save_copy",
            "delivery_open_folder",
            "delivery_open_quality_report",
            "delivery_rerender",
            "delivery_revise",
            "delivery_confirm",
        ]
    if public_state == "completed" and delivery_binding is not None:
        return [
            "delivery_open_document",
            "delivery_save_copy",
            "delivery_open_folder",
            "delivery_open_quality_report",
        ]
    return []


def expert_team_run_view(run: dict) -> dict:
    contract_version = classify_contract_version(run)
    business_context = business_context_for_run(run)
    state = _effective_state(run)
    intake = _question_state(run)
    stage_review = _stage_review(run, state)
    presentation = _presentation(run, business_context)
    primary_confirmation = None
    if state in {"collecting_required", "collecting_optional"}:
        pending = [
            q
            for q in intake["questions"]
            if q.get("status") == "pending"
            and ((state == "collecting_required" and q.get("required")) or (state == "collecting_optional" and not q.get("required")))
        ]
        if pending:
            primary_confirmation = {
                "type": "question",
                "question_id": pending[0].get("id"),
                "title": pending[0].get("title"),
            }
    elif state == "awaiting_review":
        primary_confirmation = {"type": "stage_review", "title": "阶段成果待复核"}
    elif state == "awaiting_stage_input":
        pending_input = _pending_input(run)
        primary_confirmation = {
            "type": "stage_input",
            "input_id": pending_input.get("id"),
            "title": pending_input.get("question") or "需要确认后继续",
        }
    else:
        pending_input = _pending_input(run)
    if state != "awaiting_stage_input":
        pending_input = _pending_input(run)
    standalone = str(run.get("product_mode") or "") == "standalone"
    document_contract = contract_version == EXPERT_TEAM_CONTRACT_V1
    public_state = _public_state(run, state)
    stage_action_binding = _stage_action_binding(run, public_state)
    cancel_action_binding = _cancel_action_binding(run, state)
    delivery_action_binding = _delivery_action_binding(run, public_state)
    delivery_recovery_binding = _delivery_recovery_binding(run, state)
    if standalone:
        completion_gates, delivery_status, next_action = _standalone_completion_model(
            run,
            delivery_action_binding,
            delivery_recovery_binding,
        )
    else:
        completion_gates, delivery_status, next_action = _completion_model(
            run,
            enterprise=document_contract,
        )
    result = {
        "public_state": public_state,
        "allowed_actions": _allowed_actions(
            run,
            state,
            public_state,
            stage_action_binding,
            cancel_action_binding,
            delivery_action_binding,
            delivery_recovery_binding,
        ),
        "stage_action_binding": stage_action_binding,
        "cancel_action_binding": cancel_action_binding,
        "delivery_action_binding": delivery_action_binding,
        "delivery_recovery_binding": delivery_recovery_binding,
        "standalone_delivery": _standalone_delivery_summary(run, delivery_action_binding),
        "business_context": business_context,
        "presentation": presentation,
        "team": _team(run),
        "workflow": _workflow(run),
        "workspace": _workspace(run),
        "dock": _dock(run, presentation),
        "stage_result": _stage_result(run),
        "pending_input": pending_input,
        "intake": intake,
        "primary_confirmation": primary_confirmation,
        "pending_confirmations": [primary_confirmation] if primary_confirmation else [],
        "review_items": deepcopy(run.get("review_items") or []),
        "stage_review": stage_review,
        "timeline_events": _timeline_events(run),
        "phase_progress": _progress(run),
        "actions": {
            "can_start_generation": state == "ready_to_generate",
            "can_cancel": state in {"generating", "revising"},
            "can_submit_stage_input": state == "awaiting_stage_input",
            "can_retry": state in {"start_failed", "generation_failed", "generated_invalid"},
            "can_confirm_stage": (
                standalone
                and public_state == "awaiting_stage_confirmation"
                and stage_action_binding is not None
                and not _active_semantic_validation(run)
            ),
            "can_recheck_stage": (
                standalone
                and public_state == "awaiting_stage_confirmation"
                and stage_action_binding is not None
                and _semantic_recheck_allowed(run)
            ),
            "can_approve_stage": (not standalone) and state == "awaiting_review",
            "can_request_revision": state == "awaiting_review",
            "can_refresh": state == "cancelling",
            "can_open_delivery": delivery_action_binding is not None,
            "can_revise_delivery": public_state == "awaiting_delivery_confirmation" and delivery_action_binding is not None,
            "can_confirm_delivery": public_state == "awaiting_delivery_confirmation" and delivery_action_binding is not None,
            "can_recover_delivery": delivery_recovery_binding is not None,
        },
        "completion_gates": completion_gates,
        "delivery_status": delivery_status,
        "next_action": next_action,
        "office_review": (
            None
            if standalone
            else deepcopy(run.get("office_review_view"))
            if isinstance(run.get("office_review_view"), dict)
            else None
        ),
        "capability": _capability_model(run, contract_version),
        "artifact_validation": {"status": "unavailable", "blocking_count": 0},
    }
    product_error_code = str(run.get("last_execution_error_code") or "").strip()
    if state == "generated_invalid" and _is_protocol_stage_error(run):
        product_error_code = "model_output_invalid"
    elif state == "generated_invalid" and _generated_invalid_needs_new_evidence(run):
        product_error_code = "expert_team_evidence_required"
    elif not product_error_code and state == "generated_invalid":
        product_error_code = "expert_team_content_blocked"
    if product_error_code:
        from api.product_contract import build_product_error
        from .error_projection import expert_team_product_error_code

        normalized_product_code = expert_team_product_error_code(product_error_code)
        result["product_error"] = build_product_error(
            normalized_product_code,
            incident_id=(
                run.get("last_execution_incident_id")
                or _stable_incident_id(run, normalized_product_code)
            ),
        )
        result["presentation"]["detail"] = result["product_error"]["message"]
        result["dock"]["detail"] = result["product_error"]["message"]
    if standalone:
        result["product_mode"] = "standalone"
    if document_contract:
        brief = brief_summary(run.get("document_brief") or {})
        full_brief = run.get("document_brief") if isinstance(run.get("document_brief"), dict) else {}
        original_request = str(brief.get("original_request") or "")
        brief["original_request_summary"] = content_summary(original_request)
        brief["document_type_label"] = DOCUMENT_TYPE_LABELS.get(
            str(brief.get("document_type") or ""),
            TASK_CONFIGURATION_ERROR_MESSAGE,
        )
        for field in ("purpose", "audience", "usage_scenario", "additional_context"):
            brief[field] = str(full_brief.get(field) or "")
        brief["document_control"] = deepcopy(
            full_brief.get("document_control") if isinstance(full_brief.get("document_control"), dict) else {}
        )
        brief["required_sections"] = required_sections_for_brief(full_brief)
        source_policy = full_brief.get("source_policy") if isinstance(full_brief.get("source_policy"), dict) else {}
        brief["source_policy_summary"] = {
            "mode": str(source_policy.get("mode") or ""),
            "citation_style": str(source_policy.get("citation_style") or ""),
            "source_count": len(source_policy.get("source_refs") or []),
        }
        registry = run.get("source_registry") if isinstance(run.get("source_registry"), dict) else {}
        sources = []
        for item in source_policy.get("source_refs") or []:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id") or "")
            authoritative = registry.get(source_id) if isinstance(registry.get(source_id), dict) else {}
            safe = {
                "source_id": source_id,
                "kind": str(item.get("kind") or ""),
                "label": str(item.get("label") or source_id),
                "status": str(authoritative.get("status") or ("ready" if item.get("sha256") else "pending")),
            }
            if authoritative.get("size_bytes") is not None:
                safe["size_bytes"] = int(authoritative.get("size_bytes") or 0)
            digest = str(authoritative.get("sha256") or item.get("sha256") or "")
            if digest:
                safe["sha256"] = digest
            sources.append(safe)
        brief["sources"] = sources
        brief["editable"] = _brief_is_editable(run)
        brief["edit_policy"] = "editable" if brief["editable"] else "new_run_required"
        brief["validation"] = deepcopy(
            run.get("brief_validation")
            if isinstance(run.get("brief_validation"), dict)
            else {"valid_for_confirmation": False, "field_errors": []}
        )
        if standalone:
            profile = (
                run.get("launch_profile_snapshot")
                if isinstance(run.get("launch_profile_snapshot"), dict)
                else {}
            )
            schema = profile.get("brief_schema")
            if not isinstance(schema, list):
                schema = brief_schema(
                    str(full_brief.get("document_type") or ""),
                    str(full_brief.get("task_mode") or "create"),
                )
            brief["field_schema"] = [
                {
                    **deepcopy(field),
                    "value": _nested_brief_value(full_brief, str(field.get("path") or "")),
                }
                for field in schema
                if isinstance(field, dict) and str(field.get("path") or "")
            ]
            requirement = profile.get("source_requirement")
            brief["source_requirement"] = deepcopy(
                requirement
                if isinstance(requirement, dict)
                else source_requirement(
                    str(full_brief.get("document_type") or ""),
                    str(full_brief.get("task_mode") or "create"),
                )
            )
            brief["field_errors"] = [
                {
                    "field": str(error.get("field") or ""),
                    "code": str(error.get("code") or ""),
                    "message": str(error.get("message") or ""),
                }
                for error in brief["validation"].get("field_errors") or []
                if isinstance(error, dict) and str(error.get("field") or "")
            ]
        brief["gate"] = "confirmed" if brief.get("status") == "confirmed" else "needs_confirmation"
        brief["view_action"] = {
            "type": "edit_brief" if brief["editable"] else "view_brief",
            "label": "查看/编辑文档规格" if brief["editable"] else "查看文档规格",
        }
        if not standalone:
            result["contract_version"] = contract_version
        result["brief"] = brief
        enterprise_result = _enterprise_stage_result(run)
        if standalone and state == "generated_invalid" and not enterprise_result:
            enterprise_result = _stage_result(run)
        result["artifact_validation"] = deepcopy(
            {"status": "invalid", "blocking_count": 1}
            if standalone and state == "generated_invalid"
            else enterprise_result.get("validation")
            if isinstance(enterprise_result.get("validation"), dict)
            else {"status": "unavailable", "blocking_count": 0}
        )
        result["stage_result"] = enterprise_result
        result["presentation"]["result"] = enterprise_result
        result["presentation"]["summary"] = str(enterprise_result.get("summary") or "")
        result["workspace"]["stage_result"] = enterprise_result
        result["stage_review"] = {
            "display_state": stage_review.get("display_state"),
            "actionable": stage_review.get("actionable"),
            "output": enterprise_result,
        }
    stage_result = result.get("stage_result") if isinstance(result.get("stage_result"), dict) else {}
    stage_quality = (
        stage_result.get("stage_quality")
        if isinstance(stage_result.get("stage_quality"), dict)
        else {}
    )
    reservation = (
        run.get("current_stage_attempt_reservation")
        if isinstance(run.get("current_stage_attempt_reservation"), dict)
        else {}
    )
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    public_error = result.get("product_error") if isinstance(result.get("product_error"), dict) else {}
    public_error_code = str(public_error.get("code") or "")
    result["diagnostics"] = {
        "schema": "expert-team-diagnostics/v1",
        "commit": _public_build_ref("TAIJI_SOURCE_COMMIT"),
        "source_mode": _public_build_ref("TAIJI_SOURCE_MODE"),
        "run_id": str(run.get("run_id") or ""),
        "stage_id": str(
            current.get("task_id")
            or current.get("id")
            or stage_result.get("stage_id")
            or ""
        ),
        "stage_attempt": int(
            reservation.get("stage_attempt")
            or stage_result.get("stage_attempt")
            or run.get("execution_attempt")
            or 0
        ),
        "error_code": public_error_code,
        "incident_id": str(public_error.get("incident_id") or ""),
        "blocking_count": int(stage_quality.get("blocking_count") or 0),
        "warning_count": int(stage_quality.get("warning_count") or 0),
        "provider_error_category": (
            public_error_code if public_error_code.startswith("provider_") else ""
        ),
        "delivery_state": str(result.get("delivery_status") or ""),
    }
    return result
