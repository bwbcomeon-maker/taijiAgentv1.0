"""Clean expert-team state machine."""

from __future__ import annotations

import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .catalog import CONTENT_CREATOR_TEAM_ID, DEEP_RESEARCH_TEAM_ID, get_template
from .materials import business_context_for_run, structured_output_from_delivery, validate_office_material_output
from .storage import latest_run_for_session, read_run, write_run
from .view import expert_team_run_view


TERMINAL_STATES = {"completed", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _duration(started_at: str | None) -> int:
    if not started_at:
        return 0
    try:
        return max(0, int(time.time() - datetime.fromisoformat(started_at).timestamp()))
    except Exception:
        return 0


def _with_view(run: dict) -> dict:
    run["duration_seconds"] = _duration(str(run.get("created_at") or ""))
    run["view"] = expert_team_run_view(run)
    return run


def _task_statuses(tasks: list[dict], index: int, state: str) -> list[dict]:
    rows = []
    for idx, task in enumerate(tasks):
        item = deepcopy(task)
        if idx < index:
            item["status"] = "done"
        elif idx == index:
            item["status"] = {
                "collecting_required": "pending",
                "collecting_optional": "pending",
                "ready_to_generate": "pending",
                "generating": "running",
                "generated_invalid": "error",
                "awaiting_review": "awaiting_review",
                "revising": "running",
                "completed": "done",
                "failed": "error",
                "cancelled": "cancelled",
            }.get(state, "pending")
        else:
            item["status"] = "pending"
        return_label = {
            "done": "完成",
            "pending": "待执行",
            "running": "执行中",
            "awaiting_review": "待复核",
            "error": "需重试",
            "cancelled": "已取消",
        }.get(str(item.get("status")), str(item.get("status")))
        item["status_label"] = return_label
        rows.append(item)
    return rows


def _members(template: dict) -> list[dict]:
    return [{**deepcopy(member), "status": "待命"} for member in template.get("members") or []]


def _initial_timeline_events(template: dict) -> list[dict]:
    now = _now()
    events = [
        {
            "type": "team_created",
            "title": f"{template.get('title') or '专家团'}已创建",
            "detail": "等待需求确认后开始协作。",
            "member_id": "director",
            "at": now,
        }
    ]
    for member in template.get("members") or []:
        events.append(
            {
                "type": "member_joined",
                "title": f"{member.get('name') or '专家'}已加入",
                "detail": str(member.get("role") or "专家协作"),
                "member_id": str(member.get("id") or ""),
                "at": now,
            }
        )
    events.append(
        {
            "type": "intake_requested",
            "title": "等待需求确认",
            "detail": "请先补充必填需求，可选补充需要提交或跳过。",
            "member_id": "director",
            "at": now,
        }
    )
    events.append(
        {
            "type": "phase_plan_created",
            "title": "已生成专家团阶段计划",
            "detail": "将按计划、初稿、打磨、交付确认推进。",
            "member_id": "director",
            "at": now,
        }
    )
    return events


def _questions(template: dict, prompt: str) -> list[dict]:
    rows = []
    for question in template.get("questions") or []:
        item = deepcopy(question)
        item["status"] = "pending"
        item["answer"] = ""
        item["confirmation_group"] = "intake_required" if item.get("required") else "intake_optional"
        rows.append(item)
    return rows


def _current_stage(run: dict) -> dict:
    tasks = run.get("tasks") if isinstance(run.get("tasks"), list) else []
    index = int(run.get("current_stage_index") or 0)
    if not tasks:
        return {}
    index = min(max(index, 0), len(tasks) - 1)
    task = deepcopy(tasks[index])
    return {
        "index": index,
        "task_id": task.get("id"),
        "title": task.get("title"),
        "phase": task.get("phase"),
        "worker_name": task.get("worker_name"),
        "status": str(task.get("status") or "pending"),
    }


def _sync_derived(run: dict) -> dict:
    state = str(run.get("workflow_state") or "collecting_required")
    tasks_template = [deepcopy(task) for task in run.get("_tasks_template") or run.get("tasks") or []]
    if tasks_template:
        run["_tasks_template"] = tasks_template
        run["tasks"] = _task_statuses(tasks_template, int(run.get("current_stage_index") or 0), state)
    run["current_stage"] = _current_stage(run)
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    run["phase"] = str(current.get("phase") or "需求确认")
    current_worker = str(current.get("worker_name") or "")
    member_status = []
    for member in run.get("members") or []:
        item = deepcopy(member) if isinstance(member, dict) else {}
        name = str(item.get("name") or "")
        if state in {"collecting_required", "collecting_optional", "ready_to_generate"} and item.get("id") == "director":
            item["status"] = "等待确认" if state != "ready_to_generate" else "待启动"
        elif state in {"generating", "revising"} and name == current_worker:
            item["status"] = "执行中"
        elif state in {"awaiting_review", "generated_invalid"} and name == current_worker:
            item["status"] = "待复核"
        elif state == "completed":
            item["status"] = "已完成"
        elif not item.get("status"):
            item["status"] = "待命"
        member_status.append(item)
    if member_status:
        run["members"] = member_status
    run["status"] = {
        "collecting_required": "awaiting_user",
        "collecting_optional": "awaiting_user",
        "ready_to_generate": "awaiting_user",
        "generating": "running",
        "generated_invalid": "awaiting_user",
        "awaiting_review": "awaiting_user",
        "revising": "running",
        "completed": "done",
        "failed": "error",
        "cancelled": "cancelled",
    }.get(state, "awaiting_user")
    run["execution_status"] = {
        "ready_to_generate": "idle",
        "generating": "running",
        "revising": "running",
        "completed": "done",
        "failed": "error",
        "cancelled": "cancelled",
    }.get(state, "idle")
    return _with_view(run)


def _transition(workspace: Path, run: dict, state: str, event: str, patch: dict | None = None) -> dict:
    previous = str(run.get("workflow_state") or "")
    if previous in TERMINAL_STATES and state not in {"ready_to_generate"}:
        raise ValueError(f"Cannot transition terminal expert team run from {previous} to {state}")
    next_run = deepcopy(run)
    next_run["workflow_state"] = state
    next_run["updated_at"] = _now()
    if patch:
        next_run.update(patch)
    events = list(next_run.get("events") or [])
    events.append({"type": event, "from": previous, "to": state, "at": next_run["updated_at"]})
    next_run["events"] = events
    timeline = list(next_run.get("timeline_events") or [])
    timeline_title = {
        "questions_answered": "需求信息已更新",
        "generation_started": "专家开始执行当前阶段",
        "generation_completed": "阶段成果已生成",
        "generation_invalid": "草稿未通过办公材料校验",
        "stage_approved": "阶段成果已确认",
        "stage_revision_requested": "已收到修改意见",
        "generation_resumed": "准备重新生成当前阶段",
        "generation_failed": "生成失败",
        "generation_cancelled": "本轮生成已停止",
    }.get(event)
    if timeline_title:
        timeline.append(
            {
                "type": event,
                "title": timeline_title,
                "detail": str(next_run.get("phase") or ""),
                "member_id": "director",
                "at": next_run["updated_at"],
            }
        )
        next_run["timeline_events"] = timeline
    return write_run(workspace, _sync_derived(next_run))


def start_expert_team(workspace: Path, body: dict) -> dict:
    template = get_template(str(body.get("team_id") or CONTENT_CREATOR_TEAM_ID))
    prompt = str(body.get("prompt") or body.get("message") or "").strip()
    if not prompt:
        prompt = "请起草一份办公材料。"
    run = {
        "run_id": "et-" + uuid.uuid4().hex[:16],
        "session_id": str(body.get("session_id") or "").strip(),
        "team_id": template["id"],
        "team_title": template["title"],
        "team_image": template.get("image") or "",
        "title": prompt[:120],
        "prompt": prompt,
        "created_at": _now(),
        "updated_at": _now(),
        "workflow_state": "collecting_required",
        "current_stage_index": 0,
        "questions": _questions(template, prompt),
        "answers": [],
        "members": _members(template),
        "_tasks_template": deepcopy(template.get("tasks") or []),
        "tasks": deepcopy(template.get("tasks") or []),
        "artifacts": [],
        "stage_outputs": [],
        "review_items": [],
        "events": [{"type": "team_created", "to": "collecting_required", "at": _now()}],
        "timeline_events": _initial_timeline_events(template),
    }
    return write_run(workspace, _sync_derived(run))


def read_expert_team_run(workspace: Path, run_id: str) -> dict:
    return _sync_derived(read_run(workspace, run_id))


def latest_expert_team_run_for_session(workspace: Path, session_id: str) -> dict:
    return _sync_derived(latest_run_for_session(workspace, session_id))


def _apply_answers(run: dict, answers: dict, skip_optional: bool) -> dict:
    rows = []
    answer_rows = list(run.get("answers") or [])
    for question in run.get("questions") or []:
        item = deepcopy(question)
        qid = str(item.get("id") or "")
        if qid in answers:
            raw = "" if answers.get(qid) is None else str(answers.get(qid)).strip()
            if item.get("required") and not raw:
                rows.append(item)
                continue
            if not item.get("required") and not raw and skip_optional:
                item["status"] = "skipped"
                item["answer"] = ""
            elif not item.get("required") and not raw:
                rows.append(item)
                continue
            else:
                item["status"] = "answered"
                item["answer"] = raw
            answer_rows = [row for row in answer_rows if not isinstance(row, dict) or row.get("question_id") != qid]
            answer_rows.append({"question_id": qid, "answer": item.get("answer") or "", "status": item["status"]})
        rows.append(item)
    run["questions"] = rows
    run["answers"] = answer_rows
    return run


def _intake_state(run: dict) -> str:
    questions = [q for q in run.get("questions") or [] if isinstance(q, dict)]
    if any(q.get("required") and q.get("status") == "pending" for q in questions):
        return "collecting_required"
    if any((not q.get("required")) and q.get("status") == "pending" for q in questions):
        return "collecting_optional"
    return "ready_to_generate"


def answer_expert_team(workspace: Path, body: dict) -> dict:
    run = read_run(workspace, str(body.get("run_id") or ""))
    if str(run.get("workflow_state") or "") not in {"collecting_required", "collecting_optional", "ready_to_generate"}:
        raise ValueError("Expert team is not collecting requirements")
    answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
    run = _apply_answers(run, answers, bool(body.get("skip_optional")))
    return _transition(workspace, run, _intake_state(run), "questions_answered")


def mark_expert_team_execution_started(workspace: Path, run_id: str, stream_response: dict | None = None) -> dict:
    run = read_run(workspace, run_id)
    patch = {
        "execution_started_at": _now(),
        "execution_stream_id": str((stream_response or {}).get("stream_id") or ""),
        "pending_user_message": str((stream_response or {}).get("pending_user_message") or ""),
        "last_execution_error": "",
    }
    return _transition(workspace, run, "generating", "generation_started", patch)


def mark_expert_team_execution_complete(workspace: Path, run_id: str, delivery: dict | None = None) -> dict:
    run = read_run(workspace, run_id)
    business_context = business_context_for_run(run)
    output = structured_output_from_delivery(delivery or {}, business_context)
    current = _current_stage(_sync_derived(deepcopy(run)))
    output["task_id"] = current.get("task_id") or ""
    output["phase"] = current.get("phase") or ""
    output["worker_name"] = current.get("worker_name") or ""
    if str(current.get("task_id") or "") == "plan":
        output["title"] = current.get("title") or "专家团计划"
        output["visible_title"] = current.get("title") or "专家团计划"
    material_type = str(business_context.get("material_type") or "office_material")
    validation_material_type = material_type if str(current.get("task_id") or "") == "draft" else "office_material"
    validation = validate_office_material_output(output.get("content") or "", validation_material_type)
    run.setdefault("stage_outputs", [])
    run["stage_outputs"].append(output)
    run.setdefault("artifacts", [])
    if output.get("kind") == "chat":
        run["artifacts"] = [{"id": output["id"], "kind": "chat", "label": "结果已写入对话", "exists": True}]
    if validation.get("status") != "pass":
        run["last_validation_error"] = str(validation.get("message") or "草稿未通过校验")
        run["validation"] = validation
        return _transition(workspace, run, "generated_invalid", "generation_invalid")
    run["last_validation_error"] = ""
    run["validation"] = validation
    return _transition(workspace, run, "awaiting_review", "generation_completed")


def mark_content_expert_team_execution_complete(workspace: Path, run_id: str, delivery: dict | None = None) -> dict:
    return mark_expert_team_execution_complete(workspace, run_id, delivery)


def approve_expert_team_stage(workspace: Path, body: dict) -> dict:
    run = read_run(workspace, str(body.get("run_id") or ""))
    if str(run.get("workflow_state") or "") != "awaiting_review":
        raise ValueError("Expert team stage is not awaiting review")
    index = int(run.get("current_stage_index") or 0)
    total = len(run.get("_tasks_template") or run.get("tasks") or [])
    current_task_id = str((run.get("current_stage") or {}).get("task_id") or "")
    outputs = [deepcopy(output) for output in run.get("stage_outputs") or [] if isinstance(output, dict)]
    for output in reversed(outputs):
        if not current_task_id or str(output.get("task_id") or "") == current_task_id:
            output["status"] = "approved"
            output["approved_at"] = _now()
            break
    run["stage_outputs"] = outputs
    run["current_stage_index"] = index + 1
    if index + 1 >= total:
        return _transition(workspace, run, "completed", "stage_approved")
    return _transition(workspace, run, "ready_to_generate", "stage_approved")


def request_expert_team_stage_revision(workspace: Path, body: dict) -> dict:
    run = read_run(workspace, str(body.get("run_id") or ""))
    feedback = str(body.get("feedback") or "").strip()
    run.setdefault("revision_feedback", []).append({"feedback": feedback, "at": _now()})
    return _transition(workspace, run, "ready_to_generate", "stage_revision_requested")


def resume_expert_team(workspace: Path, run_id: str) -> dict:
    run = read_run(workspace, run_id)
    return _transition(workspace, run, "ready_to_generate", "generation_resumed", {"last_execution_error": ""})


def fail_expert_team_execution(workspace: Path, run_id: str, message: str) -> dict:
    run = read_run(workspace, run_id)
    return _transition(
        workspace,
        run,
        "failed",
        "generation_failed",
        {"last_execution_error": str(message or "未检测到生成结果，请重新尝试。")},
    )


def cancel_expert_team(workspace: Path, run_id: str) -> dict:
    run = read_run(workspace, run_id)
    return _transition(workspace, run, "cancelled", "generation_cancelled")


def _business_context_for_view(run: dict) -> dict:
    return business_context_for_run(run)
