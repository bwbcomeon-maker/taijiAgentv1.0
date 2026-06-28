"""Single expert-team view contract consumed by the frontend presenter."""

from __future__ import annotations

from copy import deepcopy

from .materials import business_context_for_run, content_summary


STATE_LABELS = {
    "collecting_required": "必须需求待确认",
    "collecting_optional": "可选补充待处理",
    "ready_to_generate": "准备开始生成",
    "generating": "专家团正在生成",
    "generated_invalid": "草稿未通过校验",
    "awaiting_review": "阶段成果待复核",
    "revising": "正在按修改意见调整",
    "completed": "专家团任务已完成",
    "failed": "生成失败",
    "cancelled": "已取消",
}


def _primary_action(state: str) -> dict | None:
    return {
        "collecting_required": {"id": "answer_required", "label": "去确认", "kind": "question_popover"},
        "collecting_optional": {"id": "answer_optional", "label": "补充或跳过", "kind": "question_popover"},
        "ready_to_generate": {"id": "start_generation", "label": "开始生成", "kind": "primary"},
        "generating": {"id": "cancel", "label": "停止生成", "kind": "danger"},
        "generated_invalid": {"id": "regenerate", "label": "重新生成", "kind": "primary"},
        "awaiting_review": {"id": "review_stage", "label": "去复核", "kind": "primary"},
        "revising": {"id": "cancel", "label": "停止生成", "kind": "danger"},
        "completed": {"id": "view_result", "label": "查看成果", "kind": "primary"},
        "failed": {"id": "regenerate", "label": "重新尝试", "kind": "primary"},
        "cancelled": {"id": "regenerate", "label": "重新开始本阶段", "kind": "primary"},
    }.get(state)


def _secondary_actions(state: str) -> list[dict]:
    if state == "awaiting_review":
        return [
            {"id": "view_result", "label": "查看成果", "kind": "ghost"},
            {"id": "approve_stage", "label": "无修改，进入下一阶段", "kind": "primary"},
            {"id": "revise_stage", "label": "需要修改", "kind": "ghost"},
        ]
    if state == "generated_invalid":
        return [{"id": "view_result", "label": "查看草稿", "kind": "ghost"}]
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
    return deepcopy(outputs[-1])


def _stage_review(run: dict, state: str) -> dict:
    output = _stage_output(run)
    actionable = state == "awaiting_review"
    display_state = "awaiting_review" if actionable else ("running" if state in {"ready_to_generate", "generating", "revising"} else state)
    return {
        "display_state": display_state,
        "actionable": actionable,
        "output": output,
    }


def _presentation(run: dict, business_context: dict) -> dict:
    state = str(run.get("workflow_state") or "collecting_required")
    output = _stage_output(run)
    detail = ""
    if state in {"collecting_required", "collecting_optional"}:
        detail = "请先补充需求信息，专家团再继续推进。"
    elif state in {"ready_to_generate", "generating", "revising"}:
        detail = "后台正在按当前阶段生成内容。"
    elif state == "generated_invalid":
        detail = str(run.get("last_validation_error") or "草稿未通过办公材料口径校验。")
    elif state == "awaiting_review":
        detail = "阶段结果已生成，请查看后确认是否进入下一阶段。"
    elif state == "completed":
        detail = "所有阶段已完成，结果已写入当前对话。"
    elif state in {"failed", "cancelled"}:
        detail = str(run.get("last_execution_error") or STATE_LABELS.get(state) or "")
    return {
        "state": state,
        "title": STATE_LABELS.get(state, "专家团状态"),
        "visible_title": str(business_context.get("visible_title") or run.get("title") or "专家团任务"),
        "detail": detail,
        "primary_action": _primary_action(state),
        "secondary_actions": _secondary_actions(state),
        "result": output,
        "summary": content_summary(str(output.get("content") or output.get("summary") or run.get("title") or "")),
    }


def _progress(run: dict) -> dict:
    tasks = [task for task in run.get("tasks") or [] if isinstance(task, dict)]
    done = sum(1 for task in tasks if str(task.get("status") or "") == "done")
    current = str(run.get("phase") or "")
    return {"done": done, "total": len(tasks), "current": current}


def expert_team_run_view(run: dict) -> dict:
    business_context = business_context_for_run(run)
    state = str(run.get("workflow_state") or "collecting_required")
    intake = _question_state(run)
    stage_review = _stage_review(run, state)
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
    return {
        "business_context": business_context,
        "presentation": _presentation(run, business_context),
        "intake": intake,
        "primary_confirmation": primary_confirmation,
        "pending_confirmations": [primary_confirmation] if primary_confirmation else [],
        "review_items": deepcopy(run.get("review_items") or []),
        "stage_review": stage_review,
        "phase_progress": _progress(run),
        "actions": {
            "can_start_generation": state == "ready_to_generate",
            "can_cancel": state in {"generating", "revising"},
            "can_retry": state in {"generated_invalid", "failed", "cancelled"},
            "can_approve_stage": state == "awaiting_review",
        },
    }
