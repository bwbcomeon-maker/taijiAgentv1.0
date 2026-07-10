"""Single expert-team view contract consumed by the frontend presenter."""

from __future__ import annotations

from copy import deepcopy

from .materials import business_context_for_run, content_summary


STATE_LABELS = {
    "collecting_required": "必须需求待确认",
    "collecting_optional": "可选补充待处理",
    "ready_to_generate": "准备开始生成",
    "starting": "正在启动专家团",
    "start_failed": "启动失败",
    "generating": "专家团正在生成",
    "cancelling": "正在停止专家团",
    "awaiting_stage_input": "需要确认后继续",
    "generated_invalid": "草稿未通过校验",
    "awaiting_review": "阶段成果待复核",
    "revising": "正在按修改意见调整",
    "completed": "专家团任务已完成",
    "failed": "生成失败",
    "cancelled": "已取消",
    "completed_invalid": "已完成交付异常",
}


def _effective_state(run: dict) -> str:
    state = str(run.get("workflow_state") or "collecting_required")
    integrity = run.get("completion_integrity") if isinstance(run.get("completion_integrity"), dict) else {}
    if state == "completed" and str(integrity.get("status") or "") in {"drifted", "unverified"}:
        return "completed_invalid"
    return state


def _primary_action(state: str) -> dict | None:
    return {
        "collecting_required": {"id": "answer_required", "label": "去确认", "kind": "question_popover"},
        "collecting_optional": {"id": "answer_optional", "label": "补充或跳过", "kind": "question_popover"},
        "ready_to_generate": {"id": "start_generation", "label": "开始生成", "kind": "primary"},
        "starting": {"id": "cancel", "label": "停止启动", "kind": "danger"},
        "start_failed": {"id": "regenerate", "label": "重新尝试", "kind": "primary"},
        "generating": {"id": "cancel", "label": "停止生成", "kind": "danger"},
        "awaiting_stage_input": {"id": "submit_stage_input", "label": "确认并继续生成", "kind": "primary"},
        "generated_invalid": {"id": "regenerate", "label": "重新生成", "kind": "primary"},
        "awaiting_review": {"id": "review_stage", "label": "去复核", "kind": "primary"},
        "revising": {"id": "cancel", "label": "停止生成", "kind": "danger"},
        "completed": {"id": "view_result", "label": "查看成果", "kind": "primary"},
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
        return [{"id": "retry_cancel", "label": "重试停止", "kind": "ghost"}]
    if state == "awaiting_review":
        approve_label = "无修改，完成任务" if _is_final_stage(run or {}) else "无修改，进入下一阶段"
        return [
            {"id": "view_result", "label": "查看成果", "kind": "ghost"},
            {"id": "approve_stage", "label": approve_label, "kind": "primary"},
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


def _stage_result(run: dict) -> dict:
    results = [item for item in run.get("stage_results") or [] if isinstance(item, dict)]
    if results:
        return deepcopy(results[-1])
    result = run.get("stage_result")
    if isinstance(result, dict):
        return deepcopy(result)
    output = _stage_output(run)
    if not output:
        return {}
    return {
        "stage_id": str(output.get("task_id") or output.get("stage_id") or ""),
        "worker_id": str(output.get("worker_id") or ""),
        "summary": str(output.get("summary") or content_summary(str(output.get("content") or ""))),
        "deliverable": str(output.get("content") or ""),
        "review_items": [],
        "next_action": "请复核当前阶段成果。",
        "validation": deepcopy(run.get("validation") or {}),
    }


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
    elif state in {"generating", "revising"}:
        detail = "后台正在按当前阶段生成内容。"
    elif state == "cancelling":
        detail = "停止请求已提交，正在等待执行侧确认。"
    elif state == "awaiting_stage_input":
        pending = _pending_input(run)
        detail = str(pending.get("description") or pending.get("question") or "当前专家需要你确认后继续生成。")
    elif state == "generated_invalid":
        detail = str(run.get("last_validation_error") or "草稿未通过办公材料口径校验。")
    elif state == "awaiting_review":
        validation = run.get("validation") if isinstance(run.get("validation"), dict) else {}
        if str(validation.get("status") or "") == "office_acceptance_required":
            detail = str(run.get("last_validation_error") or "请完成 WPS/Word 验收后再确认交付。")
        else:
            detail = "阶段结果已生成，请查看后确认是否进入下一阶段。"
    elif state == "completed":
        detail = "所有阶段已完成，结果已写入当前对话。"
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
        primary_action = (
            {"id": "retry_cancel", "label": "重试停止", "kind": "danger"}
            if str(run.get("cancel_outcome") or "").strip().lower() in {"unknown", "retry_required"}
            else {"id": "refresh", "label": "刷新停止状态", "kind": "primary"}
        )
    return {
        "state": state,
        "title": STATE_LABELS.get(state, "专家团状态"),
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
    is_intake = state in {"collecting_required", "collecting_optional"}
    current_index = int(run.get("current_stage_index") or 0)
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
    index = int(progress.get("current_index") or 0)
    done = int(progress.get("done") or 0)
    current = min(total, max(done, index + 1))
    return f"{current}/{total}"


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
        rows.append(
            {
                "type": str(event.get("type") or "event"),
                "title": str(event.get("title") or event.get("type") or "专家团动态"),
                "detail": str(event.get("detail") or ""),
                "member_id": member_id,
                "member_name": str(member.get("name") or ""),
                "member_image": str(member.get("image") or ""),
                "at": str(event.get("at") or ""),
            }
        )
    return rows


def expert_team_run_view(run: dict) -> dict:
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
    return {
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
            "can_retry": state in {"start_failed", "generated_invalid"},
            "can_approve_stage": state == "awaiting_review",
            "can_request_revision": state == "awaiting_review",
        },
    }
