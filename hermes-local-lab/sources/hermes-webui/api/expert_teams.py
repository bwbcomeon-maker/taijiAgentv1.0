"""Product-facing expert-team runtime state.

This module keeps expert team UX state separate from model prompts. The data
stored here is safe for ordinary UI rendering: no tool names, command lines,
workspace paths, profile details, or private runtime parameters.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path


RUN_STATUSES = {"awaiting_user", "running", "done", "error", "cancelled"}
TASK_STATUSES = {"pending", "running", "waiting_user", "done", "error", "cancelled"}
STATUS_LABELS = {
    "awaiting_user": "等待确认",
    "running": "执行中",
    "done": "已完成",
    "error": "执行异常",
    "cancelled": "已取消",
    "pending": "待执行",
    "waiting_user": "等待确认",
}
EXECUTION_STATUSES = {"idle", "running", "done", "error", "needs_resume"}


EXPERT_TEAM_TEMPLATES: dict[str, dict] = {
    "ai-content-creator-brand-moodboard": {
        "id": "ai-content-creator-brand-moodboard",
        "title": "品牌视觉策划与情绪板",
        "category": "设计创意",
        "description": "先确认产品与受众，再完成品牌视觉方向策划、方向选择和情绪板产物交付。",
        "estimated": "预计 2 个阶段",
        "members": [
            {"id": "creative-director", "name": "司远", "role": "创意总监", "status": "待命"},
            {"id": "creative-strategist", "name": "策凌", "role": "创意策划师", "status": "待命"},
            {"id": "image-creator", "name": "珀西", "role": "图文创作专家", "status": "待命"},
        ],
        "questions": [
            {
                "id": "product_type",
                "title": "你的新产品是什么类型？",
                "type": "single_choice",
                "required": True,
                "options": ["消费品/快消", "科技/数码", "时尚/生活方式", "其他/告诉我"],
            },
            {
                "id": "audience",
                "title": "目标受众是哪类人群？",
                "type": "single_choice",
                "required": True,
                "options": ["年轻都市白领", "Z世代/学生", "中高端消费者", "大众/泛用户群"],
            },
            {
                "id": "brand_feeling",
                "title": "你希望品牌给人的整体感觉是？",
                "type": "single_choice",
                "required": True,
                "options": ["高端·极简·克制", "活力·年轻·大胆", "温暖·亲切·治愈", "未来·科技·前沿"],
            },
        ],
        "tasks": [
            {
                "id": "strategy",
                "title": "品牌视觉方向策划与情绪板方案",
                "worker_id": "creative-strategist",
                "worker_name": "策凌",
                "phase": "创意策划",
                "description": "受众洞察、竞品视觉参考、视觉方向提案和情绪板生成提示。",
            },
            {
                "id": "moodboard_images",
                "title": "生成情绪板图片",
                "worker_id": "image-creator",
                "worker_name": "珀西",
                "phase": "图像生成",
                "description": "基于已确认视觉方向生成情绪板图片并登记产物。",
            },
        ],
        "direction_question": {
            "id": "visual_direction",
            "title": "你想基于哪个方向制作情绪板？",
            "type": "single_choice",
            "required": True,
            "options": ["A 赛博涂鸦", "B Y2K数字浪", "C 元气脉冲"],
        },
    },
    "content-creator-team": {
        "id": "content-creator-team",
        "title": "内容创作专家团",
        "category": "内容创作",
        "description": "公众号长文从需求确认、正文初稿到配图和发布检查的结构化协作。",
        "estimated": "预计 2 个阶段",
        "members": [
            {"id": "workflow-producer", "name": "写作总导演", "role": "流程编排", "status": "待命"},
            {"id": "writing-executor", "name": "文案创作专家", "role": "正文写作", "status": "待命"},
            {"id": "article-illustrator", "name": "配图专家", "role": "封面和文中配图", "status": "待命"},
            {"id": "editor-review", "name": "审稿专家", "role": "审稿润色", "status": "待命"},
        ],
        "questions": [
            {"id": "topic", "title": "这篇内容的主题是什么？", "type": "text", "required": True, "options": []},
            {"id": "audience", "title": "目标读者是谁？", "type": "text", "required": True, "options": []},
            {"id": "boundary", "title": "有哪些素材、篇幅或表达边界？", "type": "text", "required": False, "options": []},
        ],
        "tasks": [
            {
                "id": "draft",
                "title": "撰写公众号长文",
                "worker_id": "writing-executor",
                "worker_name": "文案创作专家",
                "phase": "生成初稿",
                "description": "标题方案、正文初稿、配图建议和发布建议。",
            },
            {
                "id": "illustrations",
                "title": "生成封面和文中配图",
                "worker_id": "article-illustrator",
                "worker_name": "配图专家",
                "phase": "打磨发布",
                "description": "封面图、文中配图；图片能力不可用时产出可复用配图 prompt。",
            },
        ],
    },
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_time(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        return None


def _duration_seconds(run: dict) -> float:
    started = _parse_time(run.get("started_at") or run.get("created_at"))
    ended = _parse_time(run.get("completed_at") or run.get("updated_at")) or time.time()
    if not started or ended < started:
        return 0.0
    return round(ended - started, 3)


def _safe_run_id(value: str) -> str:
    run_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{3,120}", run_id):
        raise ValueError("Invalid expert team run_id")
    return run_id


def _runs_dir(workspace: Path) -> Path:
    return Path(workspace) / ".taiji" / "expert-teams" / "runs"


def _run_path(workspace: Path, run_id: str) -> Path:
    return _runs_dir(workspace) / f"{_safe_run_id(run_id)}.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _team_template(team_id: str | None) -> dict:
    key = str(team_id or "").strip() or "content-creator-team"
    if key not in EXPERT_TEAM_TEMPLATES:
        key = "content-creator-team"
    return deepcopy(EXPERT_TEAM_TEMPLATES[key])


def _question_rows(template: dict) -> list[dict]:
    rows = []
    for question in template.get("questions") or []:
        rows.append({**deepcopy(question), "status": "pending", "answer": "", "answered_at": ""})
    return rows


def _task_rows(template: dict) -> list[dict]:
    rows = []
    for task in template.get("tasks") or []:
        rows.append(
            {
                **deepcopy(task),
                "status": "pending",
                "status_label": STATUS_LABELS["pending"],
                "artifacts": [],
                "result_summary": "",
            }
        )
    return rows


def _normalize_run(run: dict) -> dict:
    run = dict(run or {})
    now = _now()
    run["run_id"] = _safe_run_id(run.get("run_id") or f"et-{int(time.time())}-{uuid.uuid4().hex[:8]}")
    run["team_id"] = str(run.get("team_id") or "content-creator-team")
    run["session_id"] = str(run.get("session_id") or "")
    run["title"] = str(run.get("title") or "专家团任务")
    run["status"] = str(run.get("status") or "awaiting_user")
    if run["status"] not in RUN_STATUSES:
        run["status"] = "running"
    run["status_label"] = STATUS_LABELS.get(run["status"], run["status"])
    run["phase"] = str(run.get("phase") or "需求确认")
    run["questions"] = run.get("questions") if isinstance(run.get("questions"), list) else []
    run["members"] = run.get("members") if isinstance(run.get("members"), list) else []
    run["tasks"] = run.get("tasks") if isinstance(run.get("tasks"), list) else []
    run["events"] = run.get("events") if isinstance(run.get("events"), list) else []
    run["artifacts"] = run.get("artifacts") if isinstance(run.get("artifacts"), list) else []
    run["execution_stream_id"] = str(run.get("execution_stream_id") or "")
    run["execution_session_id"] = str(run.get("execution_session_id") or run.get("session_id") or "")
    run["execution_started_at"] = str(run.get("execution_started_at") or "")
    run["execution_status"] = str(run.get("execution_status") or "idle")
    if run["execution_status"] not in EXECUTION_STATUSES:
        run["execution_status"] = "idle"
    run["last_execution_error"] = str(run.get("last_execution_error") or "")
    run["created_at"] = str(run.get("created_at") or now)
    run["started_at"] = str(run.get("started_at") or run["created_at"])
    run["updated_at"] = str(run.get("updated_at") or now)
    run["completed_at"] = str(run.get("completed_at") or "")
    run["duration_seconds"] = _duration_seconds(run)
    total = len(run["tasks"])
    done = sum(1 for task in run["tasks"] if str(task.get("status") or "") == "done")
    run["progress"] = {"done": done, "total": total}
    return run


def expert_team_catalog() -> dict:
    teams = []
    for template in EXPERT_TEAM_TEMPLATES.values():
        teams.append(
            {
                "id": template["id"],
                "title": template["title"],
                "category": template["category"],
                "description": template["description"],
                "estimated": template.get("estimated", ""),
                "members": deepcopy(template.get("members") or []),
                "questions": deepcopy(template.get("questions") or []),
                "tasks": deepcopy(template.get("tasks") or []),
            }
        )
    return {"ok": True, "teams": teams}


def read_expert_team_run(workspace: Path, run_id: str) -> dict:
    path = _run_path(Path(workspace), run_id)
    if not path.is_file():
        raise FileNotFoundError("Expert team run not found")
    return _normalize_run(json.loads(path.read_text(encoding="utf-8")))


def write_expert_team_run(workspace: Path, run: dict) -> dict:
    run = _normalize_run(run)
    _write_json(_run_path(Path(workspace), run["run_id"]), run)
    return run


def latest_expert_team_run_for_session(workspace: Path, session_id: str) -> dict | None:
    sid = str(session_id or "")
    if not sid:
        return None
    rows = []
    for path in _runs_dir(Path(workspace)).glob("*.json"):
        try:
            run = _normalize_run(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        if run.get("session_id") == sid:
            rows.append(run)
    if not rows:
        return None
    rows.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "")
    return rows[-1]


def start_expert_team(workspace: Path, body: dict) -> dict:
    template = _team_template(body.get("team_id"))
    now = _now()
    run = {
        "run_id": f"et-{int(time.time())}-{uuid.uuid4().hex[:8]}",
        "source": "expert_team",
        "team_id": template["id"],
        "team_title": template["title"],
        "team_category": template["category"],
        "session_id": str(body.get("session_id") or ""),
        "title": str(body.get("project") or body.get("title") or body.get("prompt") or template["title"])[:120],
        "prompt_summary": str(body.get("prompt") or "").strip()[:500],
        "status": "awaiting_user",
        "phase": "需求确认",
        "questions": _question_rows(template),
        "members": deepcopy(template.get("members") or []),
        "tasks": _task_rows(template),
        "events": [],
        "artifacts": [],
        "created_at": now,
        "started_at": now,
        "updated_at": now,
        "completed_at": "",
    }
    return write_expert_team_run(Path(workspace), run)


def mark_expert_team_execution_started(workspace: Path, run_id: str, stream_response: dict) -> dict:
    run = read_expert_team_run(Path(workspace), run_id)
    now = _now()
    stream_id = str((stream_response or {}).get("stream_id") or "")
    session_id = str((stream_response or {}).get("session_id") or run.get("session_id") or "")
    if not stream_id:
        raise ValueError("stream_id is required")
    run["status"] = "running"
    run["phase"] = run.get("phase") or "生成初稿"
    run["execution_stream_id"] = stream_id
    run["execution_session_id"] = session_id
    run["execution_started_at"] = now
    run["execution_status"] = "running"
    run["last_execution_error"] = ""
    for task in run.get("tasks") or []:
        if task.get("status") == "waiting_user":
            task["status"] = "running"
            task["status_label"] = STATUS_LABELS["running"]
            break
    run["updated_at"] = now
    if not _has_event(run, "execution_started"):
        _append_event(run, "execution_started", "专家团开始生成内容")
    return write_expert_team_run(Path(workspace), run)


def mark_content_expert_team_execution_complete(workspace: Path, run_id: str) -> dict:
    run = read_expert_team_run(Path(workspace), run_id)
    now = _now()
    for task in run.get("tasks") or []:
        if str(task.get("status") or "") in {"pending", "running", "waiting_user"}:
            task["status"] = "done"
            task["status_label"] = STATUS_LABELS["done"]
            if not task.get("result_summary"):
                task["result_summary"] = "已写入当前对话。"
    for member in run.get("members") or []:
        if str(member.get("status") or "") in {"待命", "执行中", "监督中", "等待继续"}:
            member["status"] = "已完成"
    if not run.get("artifacts"):
        run["artifacts"] = [
            {
                "id": "expert-team-chat-delivery",
                "label": "专家团生成结果",
                "kind": "chat",
                "path": "",
                "status": "ready",
                "placeholder": False,
                "exists": True,
                "note": "已写入当前对话",
            }
        ]
    run["status"] = "done"
    run["phase"] = "交付"
    run["execution_status"] = "done"
    run["completed_at"] = now
    run["updated_at"] = now
    if not _has_event(run, "run_done"):
        _append_event(run, "run_done", "专家团任务已完成")
    return write_expert_team_run(Path(workspace), run)


def _set_member_status(run: dict, member_id: str, status: str) -> None:
    for member in run.get("members") or []:
        if member.get("id") == member_id:
            member["status"] = status


def _set_task_status(run: dict, task_id: str, status: str, *, result_summary: str = "") -> None:
    for task in run.get("tasks") or []:
        if task.get("id") == task_id:
            task["status"] = status
            task["status_label"] = STATUS_LABELS.get(status, status)
            if result_summary:
                task["result_summary"] = result_summary


def _pending_required_questions(run: dict) -> list[dict]:
    return [
        question
        for question in run.get("questions") or []
        if question.get("required", True) and question.get("status") != "answered"
    ]


def _append_event(run: dict, event_type: str, label: str) -> None:
    run.setdefault("events", []).append({"type": event_type, "label": label, "created_at": _now()})


def _has_event(run: dict, event_type: str) -> bool:
    return any(event.get("type") == event_type for event in run.get("events") or [])


def _ensure_direction_question(run: dict) -> None:
    if run.get("team_id") != "ai-content-creator-brand-moodboard":
        return
    if any(question.get("id") == "visual_direction" for question in run.get("questions") or []):
        return
    template = _team_template(run.get("team_id"))
    question = deepcopy(template["direction_question"])
    question.update({"status": "pending", "answer": "", "answered_at": ""})
    run.setdefault("questions", []).append(question)
    _append_event(run, "question_created", "等待确认视觉方向")


def _start_first_task(run: dict) -> None:
    run["status"] = "running"
    if run.get("team_id") == "ai-content-creator-brand-moodboard":
        run["phase"] = "创意策划"
        _set_member_status(run, "creative-director", "监督中")
        _set_member_status(run, "creative-strategist", "执行中")
        _set_member_status(run, "image-creator", "待命")
        _set_task_status(run, "strategy", "running")
        if not _has_event(run, "questions_answered"):
            _append_event(run, "questions_answered", "需求问答已确认")
        if not _has_event(run, "team_created"):
            _append_event(run, "team_created", "创建团队 品牌视觉策划与情绪板")
        if not _has_event(run, "task_started"):
            _append_event(run, "task_started", "策凌开始品牌视觉策划")
    else:
        first_task = (run.get("tasks") or [{}])[0]
        run["phase"] = str(first_task.get("phase") or "生成初稿")
        if first_task.get("id"):
            _set_task_status(run, first_task["id"], "running")
        if run.get("members"):
            run["members"][0]["status"] = "监督中"
        if len(run.get("members") or []) > 1:
            run["members"][1]["status"] = "执行中"
        if not _has_event(run, "questions_answered"):
            _append_event(run, "questions_answered", "需求问答已确认")
        if not _has_event(run, "team_created"):
            _append_event(run, "team_created", f"创建团队 {run.get('team_title') or '专家团'}")
        if not _has_event(run, "task_started"):
            _append_event(run, "task_started", f"开始任务 {first_task.get('title') or '专家团任务'}")


def _start_direction_task(run: dict) -> None:
    run["status"] = "running"
    run["phase"] = "图像生成"
    _set_member_status(run, "creative-strategist", "已完成")
    _set_member_status(run, "image-creator", "执行中")
    _set_task_status(run, "strategy", "done")
    _set_task_status(run, "moodboard_images", "running")
    _append_event(run, "direction_selected", "视觉方向已确认")
    _append_event(run, "task_started", "珀西开始生成情绪板图片")


def _answers_by_id(run: dict) -> dict[str, str]:
    answers: dict[str, str] = {}
    for question in run.get("questions") or []:
        qid = str(question.get("id") or "")
        if qid and question.get("status") == "answered":
            answers[qid] = str(question.get("answer") or "")
    return answers


def _brand_strategy_summary(run: dict) -> str:
    answers = _answers_by_id(run)
    product = answers.get("product_type") or "新产品"
    audience = answers.get("audience") or "目标用户"
    feeling = answers.get("brand_feeling") or "清晰、有辨识度"
    return (
        f"基于 {product}、{audience} 和「{feeling}」的定位，推荐先比较三条视觉方向："
        "A 赛博涂鸦强调年轻张力和数码街头感；B Y2K数字浪强调复古科技和潮流符号；"
        "C 元气脉冲强调明快能量和高识别色块。"
    )


def _complete_brand_strategy(run: dict) -> None:
    summary = _brand_strategy_summary(run)
    _set_task_status(run, "strategy", "done", result_summary=summary)
    _set_member_status(run, "creative-director", "监督中")
    _set_member_status(run, "creative-strategist", "已完成")
    _set_member_status(run, "image-creator", "待命")
    run["status"] = "awaiting_user"
    run["phase"] = "方向确认"
    _ensure_direction_question(run)
    _append_event(run, "task_done", "品牌视觉方向策划已完成")


def _moodboard_artifacts(run: dict) -> list[dict]:
    direction = _answers_by_id(run).get("visual_direction") or "已确认方向"
    return [
        {
            "id": f"moodboard-{idx}",
            "label": label,
            "kind": "brief",
            "path": "",
            "status": "ready",
            "placeholder": False,
            "exists": True,
            "note": f"{direction} · {note}",
        }
        for idx, (label, note) in enumerate(
            [
                ("情绪板方向说明", "品牌关键词、视觉语气和版式建议"),
                ("主视觉色彩建议", "主色、辅助色和高亮色组合"),
                ("图形符号建议", "可用于包装、社媒和启动页的图形语言"),
                ("AI 图像生成提示", "可交给图像工具继续生成视觉素材"),
                ("发布应用建议", "头像、封面、海报和短视频首帧建议"),
            ],
            start=1,
        )
    ]


def _complete_brand_moodboard(run: dict) -> None:
    artifacts = _moodboard_artifacts(run)
    _set_task_status(
        run,
        "moodboard_images",
        "done",
        result_summary="情绪板产物清单已生成，可进入图片工具继续细化视觉素材。",
    )
    for task in run.get("tasks") or []:
        if task.get("id") == "moodboard_images":
            task["artifacts"] = deepcopy(artifacts)
    run["artifacts"] = deepcopy(artifacts)
    _set_member_status(run, "creative-director", "已完成")
    _set_member_status(run, "creative-strategist", "已完成")
    _set_member_status(run, "image-creator", "已完成")
    run["status"] = "done"
    run["phase"] = "交付"
    run["completed_at"] = _now()
    _append_event(run, "task_done", "情绪板产物清单已完成")
    _append_event(run, "run_done", "专家团任务已完成")


def answer_expert_team(workspace: Path, body: dict) -> dict:
    run = read_expert_team_run(Path(workspace), str(body.get("run_id") or ""))
    answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
    now = _now()
    answered_ids = set()
    for question in run.get("questions") or []:
        qid = str(question.get("id") or "")
        if qid not in answers:
            continue
        answer = str(answers.get(qid) or "").strip()
        if not answer and question.get("required", True):
            continue
        question["answer"] = answer
        question["status"] = "answered"
        question["answered_at"] = now
        answered_ids.add(qid)
    if answered_ids and not any(event.get("type") == "questions_answered" for event in run.get("events") or []):
        _append_event(run, "questions_answered", "需求问答已确认")
    if _pending_required_questions(run):
        run["status"] = "awaiting_user"
        run["phase"] = run.get("phase") or "需求确认"
    elif "visual_direction" in answered_ids:
        _start_direction_task(run)
        _complete_brand_moodboard(run)
    elif run.get("status") == "awaiting_user":
        _start_first_task(run)
        if run.get("team_id") == "ai-content-creator-brand-moodboard":
            _complete_brand_strategy(run)
    run["updated_at"] = now
    return write_expert_team_run(Path(workspace), run)


def mark_expert_team_task_done(workspace: Path, run_id: str, task_id: str, *, result_summary: str = "") -> dict:
    run = read_expert_team_run(Path(workspace), run_id)
    now = _now()
    _set_task_status(run, task_id, "done", result_summary=result_summary)
    _append_event(run, "task_done", result_summary or f"{task_id} 已完成")
    if run.get("team_id") == "ai-content-creator-brand-moodboard" and task_id == "strategy":
        run["status"] = "awaiting_user"
        run["phase"] = "方向确认"
        _set_member_status(run, "creative-strategist", "已完成")
        _set_member_status(run, "image-creator", "待命")
        _ensure_direction_question(run)
    elif all(str(task.get("status")) == "done" for task in run.get("tasks") or []):
        run["status"] = "done"
        run["phase"] = "交付"
        run["completed_at"] = now
    run["updated_at"] = now
    return write_expert_team_run(Path(workspace), run)


def cancel_expert_team(workspace: Path, run_id: str) -> dict:
    run = read_expert_team_run(Path(workspace), run_id)
    now = _now()
    run["status"] = "cancelled"
    run["status_label"] = STATUS_LABELS["cancelled"]
    run["completed_at"] = now
    run["updated_at"] = now
    for task in run.get("tasks") or []:
        if task.get("status") in {"pending", "running", "waiting_user"}:
            task["status"] = "cancelled"
            task["status_label"] = STATUS_LABELS["cancelled"]
    _append_event(run, "run_cancelled", "任务已取消")
    return write_expert_team_run(Path(workspace), run)


def expert_team_from_writeflow_run(run: dict) -> dict:
    run = dict(run or {})
    template = _team_template(run.get("team_id") or "content-creator-team")
    adapted = {
        "run_id": str(run.get("run_id") or f"wr-{uuid.uuid4().hex[:8]}"),
        "source": "writeflow",
        "team_id": str(run.get("team_id") or template["id"]),
        "team_title": str(run.get("team_title") or template["title"]),
        "team_category": str(run.get("team_category") or template["category"]),
        "session_id": str(run.get("session_id") or ""),
        "title": str(run.get("title") or "专家团任务"),
        "status": str(run.get("status") or "running"),
        "phase": str(run.get("phase") or "生成初稿"),
        "questions": [],
        "members": deepcopy(run.get("members") if isinstance(run.get("members"), list) else template.get("members") or []),
        "tasks": deepcopy(run.get("display_tasks") or run.get("tasks") or []),
        "events": deepcopy(run.get("events") if isinstance(run.get("events"), list) else []),
        "artifacts": deepcopy(run.get("artifacts") if isinstance(run.get("artifacts"), list) else []),
        "created_at": str(run.get("created_at") or _now()),
        "started_at": str(run.get("started_at") or run.get("created_at") or _now()),
        "updated_at": str(run.get("updated_at") or _now()),
        "completed_at": str(run.get("completed_at") or ""),
    }
    return _normalize_run(adapted)
