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
STAGE_STATUSES = {"pending", "running", "awaiting_review", "approved", "revision_running", "done", "error", "cancelled"}
STATUS_LABELS = {
    "awaiting_user": "等待确认",
    "running": "执行中",
    "done": "已完成",
    "error": "执行异常",
    "cancelled": "已取消",
    "pending": "待执行",
    "waiting_user": "等待确认",
}
EXECUTION_STATUSES = {"idle", "running", "done", "error", "needs_resume", "cancelled"}
QUESTION_TERMINAL_STATUSES = {"answered", "skipped"}
PUBLIC_EXPERT_TEAM_IDS = ("content-creator-team", "deep-research-team")
STAGE_GATED_TEAM_IDS = set(PUBLIC_EXPERT_TEAM_IDS)
STAGE_OUTPUT_PREVIEW_LIMIT = 720
EXPERT_TEAM_PHASES = {
    "ai-content-creator-brand-moodboard": ["需求确认", "创意策划", "方向确认", "图像生成", "交付"],
    "content-creator-team": ["需求确认", "生成初稿", "材料打磨", "交付"],
    "deep-research-team": ["需求确认", "资料调研", "结构提纲", "材料初稿", "复核交付"],
}


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
        "description": "面向国网业务部门日常办公材料编制，支持通知通报、工作汇报、会议纪要、宣传稿、方案说明、总结计划等内容，从需求确认、初稿撰写、材料打磨到交付确认分阶段协作。",
        "estimated": "预计 3 个阶段",
        "status_label": "本地技能已接入",
        "default_mode": "A",
        "default_action": "start",
        "tags": ["工作汇报", "通知通报", "会议纪要", "总结计划", "宣传稿件", "方案说明"],
        "image": "static/assets/writeflow/team-content-creator.png",
        "examples": [
            {
                "id": "monthly-work-report",
                "label": "工作汇报",
                "prompt": "帮我起草一篇部门月度工作汇报，主题是“迎峰度夏保供电重点工作推进情况”。面向公司分管领导，内容包括已完成工作、存在问题、下一步安排，要求条理清晰、语气正式。",
            },
            {
                "id": "service-quality-meeting-minutes",
                "label": "会议纪要",
                "prompt": "帮我整理一份专题会议纪要，主题是“优化供电服务质效提升措施”。请按会议背景、主要议题、形成意见、责任分工和后续跟踪事项来组织，语气规范、便于内部流转。",
            },
            {
                "id": "notice-brief",
                "label": "通知通报",
                "prompt": "帮我起草一份内部通知，主题是“近期安全生产专项检查安排”。请明确背景、检查范围、时间节点、责任分工和报送要求，语言简洁正式。",
            },
            {
                "id": "implementation-plan",
                "label": "方案说明",
                "prompt": "帮我起草一份方案说明，主题是“提升营业厅服务质效专项行动”。请包含目标、现状问题、主要措施、进度安排和保障机制。",
            },
            {
                "id": "work-summary-plan",
                "label": "总结计划",
                "prompt": "帮我起草一份阶段性工作总结和下一步计划，主题是“数字化办公推广应用”。请按完成情况、成效亮点、问题不足、下一步计划组织。",
            },
            {
                "id": "material-polish",
                "label": "材料润色",
                "prompt": "帮我润色一份办公材料，要求保持原意，提升逻辑层次、正式表达和可读性，并列出需要人工核对的数据和表述。",
            },
        ],
        "members": [
            {"id": "workflow-producer", "name": "写作总导演", "role": "流程编排", "status": "待命"},
            {"id": "writing-executor", "name": "文案创作专家", "role": "正文写作", "status": "待命"},
            {"id": "article-illustrator", "name": "版式整理专家", "role": "版式结构和表达打磨", "status": "待命"},
            {"id": "editor-review", "name": "审稿专家", "role": "审稿润色", "status": "待命"},
        ],
        "questions": [
            {"id": "topic", "title": "这次要编制哪类办公材料，主题是什么？", "type": "text", "required": True, "options": []},
            {"id": "audience", "title": "材料面向哪些对象，使用场景是什么？", "type": "text", "required": True, "options": []},
            {"id": "boundary", "title": "有哪些已知素材、口径要求、篇幅或表述边界？", "type": "text", "required": False, "options": []},
        ],
        "tasks": [
            {
                "id": "draft",
                "title": "起草办公材料初稿",
                "worker_id": "writing-executor",
                "worker_name": "文案创作专家",
                "phase": "生成初稿",
                "description": "材料定位、标题方案、材料初稿、版式建议和流转提示。",
            },
            {
                "id": "illustrations",
                "title": "材料打磨方案",
                "worker_id": "article-illustrator",
                "worker_name": "版式整理专家",
                "phase": "材料打磨",
                "description": "表达润色、版式结构、流转建议和可执行修改清单。",
            },
            {
                "id": "delivery",
                "title": "交付确认",
                "worker_id": "editor-review",
                "worker_name": "审稿专家",
                "phase": "交付",
                "description": "定稿建议、事实核对项、流转风险和交付说明。",
            },
        ],
    },
    "deep-research-team": {
        "id": "deep-research-team",
        "title": "深度材料研究团",
        "category": "深度研究",
        "description": "适合需要资料检索、案例调研、观点归纳和结构化提纲的调研材料、专题报告和案例素材。",
        "estimated": "预计 5 个阶段",
        "status_label": "本地技能已接入",
        "default_mode": "B",
        "default_action": "start",
        "tags": ["调研材料", "专题报告", "案例素材", "结构提纲"],
        "image": "static/assets/writeflow/team-research.png",
        "examples": [
            {
                "id": "market-research",
                "label": "深度调研",
                "prompt": "围绕「企业为什么需要本地 AI Agent 工作台」起草一份专题调研材料。请先列出研究问题、资料范围、案例方向和结构提纲，不要直接写全文。",
            },
            {
                "id": "case-library",
                "label": "案例素材",
                "prompt": "帮我整理一份关于 AI Agent 在内容生产、研发协作、资料管理里的落地案例素材。先输出案例筛选标准和材料结构。",
            },
        ],
        "members": [
            {"id": "workflow-producer", "name": "研究总导演", "role": "研究编排", "status": "待命"},
            {"id": "research-expert", "name": "资料研究员", "role": "案例调研", "status": "待命"},
            {"id": "outline-architect", "name": "结构架构师", "role": "材料结构", "status": "待命"},
            {"id": "writing-executor", "name": "材料起草专家", "role": "材料初稿", "status": "待命"},
            {"id": "editor-review", "name": "复核专家", "role": "材料复核", "status": "待命"},
        ],
        "questions": [
            {
                "id": "research_topic",
                "title": "这份深度材料要研究的主题或核心问题是什么？",
                "type": "text",
                "required": True,
                "options": [],
            },
            {
                "id": "audience_goal",
                "title": "目标读者和使用场景是什么？",
                "type": "text",
                "required": True,
                "options": [],
            },
            {
                "id": "source_boundary",
                "title": "资料范围、案例偏好或需要避开的边界是什么？",
                "type": "text",
                "required": True,
                "options": [],
            },
        ],
        "tasks": [
            {
                "id": "direction",
                "title": "确定研究方向",
                "worker_id": "workflow-producer",
                "worker_name": "研究总导演",
                "phase": "资料调研",
                "description": "明确研究问题、目标读者、资料范围和论证边界。",
            },
            {
                "id": "research",
                "title": "补充案例素材",
                "worker_id": "research-expert",
                "worker_name": "资料研究员",
                "phase": "资料调研",
                "description": "整理事实、案例、论据和素材线索，标注待人工确认项。",
            },
            {
                "id": "outline",
                "title": "生成结构提纲",
                "worker_id": "outline-architect",
                "worker_name": "结构架构师",
                "phase": "结构提纲",
                "description": "把研究材料组织成可写作的结构提纲、段落顺序和关键观点。",
            },
            {
                "id": "draft",
                "title": "起草材料初稿",
                "worker_id": "writing-executor",
                "worker_name": "材料起草专家",
                "phase": "材料初稿",
                "description": "根据研究框架和结构提纲起草材料初稿、标题方案和表达建议。",
            },
            {
                "id": "review",
                "title": "复核润色和交付建议",
                "worker_id": "editor-review",
                "worker_name": "复核专家",
                "phase": "复核交付",
                "description": "检查事实、逻辑、表达和流转风险，形成交付建议。",
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


def _status_class(value: str | None) -> str:
    text = str(value or "").lower()
    if text in {"done", "complete", "completed"} or "完成" in text:
        return "done"
    if text in {"awaiting_user", "waiting_user", "needs_resume", "waiting"} or "等待" in text or "确认" in text:
        return "waiting"
    if text in {"error", "blocked", "failed"} or "异常" in text or "失败" in text:
        return "issue"
    if text in {"cancelled", "canceled"} or "取消" in text:
        return "cancelled"
    if text in {"running", "in_progress", "working"} or "执行" in text or "处理中" in text:
        return "running"
    return "idle"


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
        raise ValueError(f"Unknown expert team: {key}")
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


def _is_stage_gated_run(run: dict) -> bool:
    return str((run or {}).get("team_id") or "") in STAGE_GATED_TEAM_IDS


def _task_index(run: dict, task_id: str | None) -> int:
    tid = str(task_id or "")
    if not tid:
        return -1
    for idx, task in enumerate(run.get("tasks") or []):
        if isinstance(task, dict) and str(task.get("id") or "") == tid:
            return idx
    return -1


def _is_final_stage_task(run: dict, task_id: str | None) -> bool:
    idx = _task_index(run, task_id)
    tasks = run.get("tasks") or []
    return idx >= 0 and idx == len(tasks) - 1


def _task_by_id(run: dict, task_id: str | None) -> dict | None:
    idx = _task_index(run, task_id)
    if idx < 0:
        return None
    return (run.get("tasks") or [])[idx]


def _current_stage_task(run: dict) -> dict | None:
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    task = _task_by_id(run, current.get("task_id"))
    if task:
        return task
    for status in ("running", "waiting_user", "error"):
        for task in run.get("tasks") or []:
            if isinstance(task, dict) and str(task.get("status") or "") == status:
                return task
    for task in run.get("tasks") or []:
        if isinstance(task, dict) and str(task.get("status") or "") == "pending":
            return task
    tasks = run.get("tasks") or []
    return tasks[-1] if tasks else None


def _stage_output_for_task(run: dict, task_id: str | None) -> dict | None:
    tid = str(task_id or "")
    if not tid:
        return None
    for output in run.get("stage_outputs") or []:
        if isinstance(output, dict) and str(output.get("task_id") or "") == tid:
            return output
    return None


def _ensure_stage_output(run: dict, task: dict) -> dict:
    task_id = str(task.get("id") or "")
    output = _stage_output_for_task(run, task_id)
    if output is not None:
        return output
    output = {
        "id": f"stage-{task_id or uuid.uuid4().hex[:8]}",
        "task_id": task_id,
        "phase": str(task.get("phase") or run.get("phase") or ""),
        "title": str(task.get("title") or task_id or "阶段产物"),
        "worker_id": str(task.get("worker_id") or ""),
        "worker_name": str(task.get("worker_name") or ""),
        "status": "pending",
        "label": str(task.get("title") or task_id or "阶段产物"),
        "kind": "chat",
        "content": "",
        "note": "",
        "revision_count": 0,
        "feedback_history": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    run.setdefault("stage_outputs", []).append(output)
    return output


def _stage_confirmation_question_id(task_id: str, index: int) -> str:
    safe_task_id = re.sub(r"[^0-9A-Za-z_]+", "_", str(task_id or "stage")).strip("_") or "stage"
    return f"stage_{safe_task_id}_confirm_{index}"


def _is_stage_confirmation_question(question: dict) -> bool:
    return str((question or {}).get("origin") or "") == "stage_confirmation_points"


def _question_is_terminal(question: dict) -> bool:
    return str((question or {}).get("status") or "pending").lower() in QUESTION_TERMINAL_STATUSES


def _is_intake_question(question: dict) -> bool:
    if not isinstance(question, dict) or _is_stage_confirmation_question(question):
        return False
    return not str(question.get("source_task_id") or "").strip()


def _extract_stage_confirmation_points(content: str) -> list[str]:
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    lines = text.split("\n")
    start_idx = -1
    heading_re = re.compile(r"^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:待人工补充事项|需要(?:用户|你)?确认的点)\s*[:：]?\s*$")
    for idx, line in enumerate(lines):
        if heading_re.match(line):
            start_idx = idx + 1
            break
    if start_idx < 0:
        return []
    stop_re = re.compile(r"^\s*(?:#{1,6}\s*)?(?:下一阶段建议|下一步建议|阶段目标|阶段产物)\b")
    item_re = re.compile(r"^\s*(?:(?:[-*]\s*)?(?P<num>\d{1,2}|[一二三四五六七八九十]{1,3})[\.、．)]|[-*])\s*(?P<body>.+?)\s*$")
    points: list[str] = []
    current: list[str] = []
    for line in lines[start_idx:]:
        stripped = line.strip()
        if stop_re.match(stripped):
            break
        match = item_re.match(stripped)
        if match:
            if current:
                points.append(" ".join(current).strip())
            current = [match.group("body").strip()]
            continue
        if current and stripped and not re.match(r"^-{3,}$", stripped):
            current.append(stripped.lstrip("-* ").strip())
    if current:
        points.append(" ".join(current).strip())
    cleaned: list[str] = []
    for point in points:
        point = re.sub(r"\s+", " ", point).strip(" -；;")
        if point and point not in cleaned:
            cleaned.append(point[:240])
        if len(cleaned) >= 8:
            break
    return cleaned


def _normalize_result_text(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\\n", "\n").strip()


OFFICE_MATERIAL_FORBIDDEN_TERMS = [
    "公众号长文",
    "文章大纲",
    "开篇",
    "标题党",
    "你有没有",
    "读者",
    "发布版",
    "发布前检查",
    "封面",
    "配图",
]


def _explicit_public_article_context(run: dict) -> bool:
    text = " ".join(
        str(run.get(key) or "")
        for key in ("prompt", "prompt_summary")
    )
    return any(marker in text for marker in ("公众号", "文章", "发布"))


def _office_material_type(run: dict) -> str:
    if str(run.get("team_id") or "") != "content-creator-team":
        return ""
    if _explicit_public_article_context(run):
        return "public_account"
    text = " ".join(
        str(run.get(key) or "")
        for key in ("prompt", "prompt_summary", "title")
    )
    answers = {}
    for question in run.get("questions") or []:
        if isinstance(question, dict):
            answers[str(question.get("id") or "")] = str(question.get("answer") or "")
    text = " ".join([text, answers.get("topic", ""), answers.get("boundary", "")])
    if any(marker in text for marker in ("工作汇报", "情况汇报", "汇报")):
        return "work_report"
    if "会议纪要" in text or "纪要" in text:
        return "meeting_minutes"
    if any(marker in text for marker in ("通知", "通报")):
        return "notice"
    if any(marker in text for marker in ("方案说明", "方案")):
        return "plan"
    if any(marker in text for marker in ("总结计划", "工作总结", "下一步计划", "总结")):
        return "summary_plan"
    if "润色" in text or "修改" in text:
        return "polish"
    return "office_material"


def _office_material_type_title(material_type: str) -> str:
    return {
        "work_report": "起草工作汇报初稿",
        "meeting_minutes": "整理会议纪要初稿",
        "notice": "起草通知通报初稿",
        "plan": "起草方案说明初稿",
        "summary_plan": "起草总结计划初稿",
        "polish": "润色办公材料",
        "office_material": "起草办公材料初稿",
    }.get(material_type, "起草办公材料初稿")


def _office_material_visible_title(value: object, run: dict, task_id: str | None = "") -> str:
    title = str(value or "").strip()
    if str(run.get("team_id") or "") != "content-creator-team":
        return title
    material_type = _office_material_type(run)
    if material_type == "public_account":
        return title
    legacy_titles = {"公众号长文", "撰写公众号长文", "文章大纲", "发布版", "发布前检查"}
    if title not in legacy_titles and "公众号长文" not in title:
        return title
    task = str(task_id or "").strip()
    if task in {"draft", "writing", "article"}:
        return _office_material_type_title(material_type)
    if task in {"illustrations", "image", "layout"}:
        return "材料打磨与版式建议"
    if task in {"delivery", "review"}:
        return "交付整理"
    return _office_material_type_title(material_type)


def _business_context_for_view(run: dict) -> dict:
    material_type = _office_material_type(run)
    if not material_type:
        return {}
    visible_title = "撰写公众号长文" if material_type == "public_account" else _office_material_type_title(material_type)
    style_contract = (
        "按用户明确指定的公众号或文章发布场景处理。"
        if material_type == "public_account"
        else "默认采用企业内部正式办公材料口径；工作汇报使用正式工作汇报结构，避免公众号化表达。"
    )
    return {
        "material_type": material_type,
        "style_contract": style_contract,
        "visible_title": visible_title,
        "forbidden_terms": [] if material_type == "public_account" else list(OFFICE_MATERIAL_FORBIDDEN_TERMS),
    }


def _office_material_output_violation(run: dict, content: str, title: str = "") -> str:
    if str(run.get("team_id") or "") != "content-creator-team" or _office_material_type(run) == "public_account":
        return ""
    text = f"{title}\n{content}"
    matched = [term for term in OFFICE_MATERIAL_FORBIDDEN_TERMS if term and term in text]
    if matched:
        return f"生成结果不符合办公材料口径，包含公众号化表达：{'、'.join(matched[:4])}"
    return ""


def _stage_output_text_meta(content: str) -> dict:
    text = _normalize_result_text(content)
    compact = re.sub(r"\s+", " ", text).strip()
    summary = ""
    for line in text.split("\n"):
        line = re.sub(r"^\s*(?:#{1,6}\s*|[-*]\s*|\d+[\.、．)]\s*)", "", line).strip()
        if line:
            summary = line[:160]
            break
    if not summary and compact:
        summary = compact[:160]
    preview = compact[:STAGE_OUTPUT_PREVIEW_LIMIT]
    if len(compact) > STAGE_OUTPUT_PREVIEW_LIMIT:
        preview = preview.rstrip() + "..."
    return {
        "summary": summary or "阶段结果已写入当前对话。",
        "preview": preview or summary or "阶段结果已写入当前对话。",
        "content_length": len(text),
        "has_long_content": len(compact) > STAGE_OUTPUT_PREVIEW_LIMIT,
    }


def _replace_stage_confirmation_questions(run: dict, task: dict, content: str, now: str) -> list[dict]:
    task_id = str(task.get("id") or "")
    if not task_id:
        return []
    group = f"stage:{task_id}"
    existing_count = len(run.get("questions") or [])
    run["questions"] = [
        question
        for question in run.get("questions") or []
        if not (
            isinstance(question, dict)
            and str(question.get("origin") or "") == "stage_confirmation_points"
            and str(question.get("confirmation_group") or "") == group
        )
    ]
    if existing_count != len(run.get("questions") or []):
        _append_event(run, "stage_confirmation_questions_demoted", f"{task.get('title') or '当前阶段'}确认点已降级为待人工补充事项")
    return []


def _set_current_stage(run: dict, task: dict, status: str, *, stream_id: str = "", feedback: str = "") -> dict:
    output = _stage_output_for_task(run, task.get("id")) or {}
    revision_count = int(output.get("revision_count") or 0)
    stage = {
        "task_id": str(task.get("id") or ""),
        "phase": str(task.get("phase") or run.get("phase") or ""),
        "title": str(task.get("title") or task.get("id") or "阶段任务"),
        "worker_id": str(task.get("worker_id") or ""),
        "worker_name": str(task.get("worker_name") or ""),
        "status": status if status in STAGE_STATUSES else "running",
        "revision_count": revision_count,
        "stream_id": str(stream_id or ""),
        "feedback": str(feedback or ""),
        "updated_at": _now(),
    }
    run["current_stage"] = stage
    run["phase"] = stage["phase"] or run.get("phase") or "生成初稿"
    return stage


def _delivery_phase(run: dict) -> str:
    phases = EXPERT_TEAM_PHASES.get(str(run.get("team_id") or ""), EXPERT_TEAM_PHASES["content-creator-team"])
    return "交付" if "交付" in phases else (phases[-1] if phases else "交付")


def _activate_stage_task(run: dict, task: dict, *, status: str = "running", stream_id: str = "", feedback: str = "") -> None:
    _set_task_status(run, str(task.get("id") or ""), "running")
    _set_current_stage(run, task, status, stream_id=stream_id, feedback=feedback)
    worker_id = str(task.get("worker_id") or "")
    for idx, member in enumerate(run.get("members") or []):
        if not isinstance(member, dict):
            continue
        if str(member.get("id") or "") == worker_id:
            member["status"] = "执行中"
        elif idx == 0 and str(member.get("status") or "") != "已完成":
            member["status"] = "监督中"
        elif str(member.get("status") or "") not in {"已完成", "执行异常"}:
            member["status"] = "待命"


def _stage_review_for_view(run: dict) -> dict:
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    output = _stage_output_for_task(run, current.get("task_id")) if current else None
    output_view = {}
    current_task_id = str(current.get("task_id") or "")
    current_title = _office_material_visible_title(current.get("title") or "", run, current_task_id)
    current_status = str(current.get("status") or "")
    run_status = str(run.get("status") or "")
    execution_status = str(run.get("execution_status") or "")
    if not current_task_id:
        display_state = "none"
    elif current_status in {"running", "revision_running"} or run_status == "running" or execution_status == "running":
        display_state = "running"
    elif current_status == "awaiting_review" and run_status == "awaiting_user" and execution_status == "done":
        display_state = "awaiting_review"
    elif current_status == "error" or run_status == "error" or execution_status == "error":
        display_state = "error"
    elif current_status == "cancelled" or run_status == "cancelled" or execution_status == "cancelled":
        display_state = "cancelled"
    elif isinstance(output, dict):
        display_state = "history"
    else:
        display_state = "none"
    actionable = display_state == "awaiting_review"
    if current_task_id and _is_final_stage_task(run, current_task_id) and str(current.get("status") or "") == "awaiting_review":
        current_title = "最终成果待确认"
    if isinstance(output, dict):
        output_kind = str(output.get("kind") or "chat")
        artifact_id = str(output.get("artifact_id") or output.get("id") or "")
        locator = str(output.get("locator") or "")
        if not locator:
            locator = "artifact" if output.get("path") or output.get("artifact_id") else ("inline" if output_kind == "inline" else "chat")
        content = _normalize_result_text(output.get("content") or "")
        text_meta = _stage_output_text_meta(content)
        output_title = _office_material_visible_title(output.get("title") or output.get("label") or "阶段产物", run, output.get("task_id") or current_task_id)
        output_view = {
            "id": str(output.get("id") or ""),
            "task_id": str(output.get("task_id") or ""),
            "phase": str(output.get("phase") or ""),
            "title": output_title,
            "visible_title": output_title,
            "label": output_title,
            "kind": output_kind,
            "status": str(output.get("status") or ""),
            "content": content,
            **text_meta,
            "note": str(output.get("note") or ""),
            "locator": locator,
            "artifact_id": artifact_id if locator == "artifact" else "",
            "revision_count": int(output.get("revision_count") or 0),
            "updated_at": str(output.get("updated_at") or ""),
        }
    return {
        "task_id": str(current.get("task_id") or ""),
        "phase": str(current.get("phase") or run.get("phase") or ""),
        "title": current_title,
        "worker_id": str(current.get("worker_id") or ""),
        "worker_name": str(current.get("worker_name") or ""),
        "status": str(current.get("status") or ""),
        "display_state": display_state,
        "actionable": actionable,
        "is_final_stage": _is_final_stage_task(run, current_task_id),
        "revision_count": int(current.get("revision_count") or 0),
        "feedback": str(current.get("feedback") or ""),
        "output": output_view,
        "feedback_history": list((output or {}).get("feedback_history") or []) if isinstance(output, dict) else [],
    }


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
    run["pending_confirmations"] = (
        run.get("pending_confirmations") if isinstance(run.get("pending_confirmations"), list) else []
    )
    run["members"] = run.get("members") if isinstance(run.get("members"), list) else []
    run["tasks"] = run.get("tasks") if isinstance(run.get("tasks"), list) else []
    for task in run["tasks"]:
        if isinstance(task, dict):
            task_id = str(task.get("id") or "")
            task["title"] = _office_material_visible_title(task.get("title") or task_id or "", run, task_id)
    run["events"] = run.get("events") if isinstance(run.get("events"), list) else []
    run["artifacts"] = run.get("artifacts") if isinstance(run.get("artifacts"), list) else []
    run["stage_outputs"] = run.get("stage_outputs") if isinstance(run.get("stage_outputs"), list) else []
    for output in run["stage_outputs"]:
        if not isinstance(output, dict):
            continue
        output["status"] = str(output.get("status") or "pending")
        if output["status"] not in STAGE_STATUSES:
            output["status"] = "pending"
        output["revision_count"] = int(output.get("revision_count") or 0)
        output["feedback_history"] = output.get("feedback_history") if isinstance(output.get("feedback_history"), list) else []
    run["current_stage"] = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    if run["current_stage"]:
        run["current_stage"]["status"] = str(run["current_stage"].get("status") or "")
        if run["current_stage"]["status"] and run["current_stage"]["status"] not in STAGE_STATUSES:
            run["current_stage"]["status"] = "running"
        run["current_stage"]["revision_count"] = int(run["current_stage"].get("revision_count") or 0)
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
    for artifact in run["artifacts"]:
        if isinstance(artifact, dict):
            artifact["openable"] = _artifact_openable(artifact)
    run["view"] = expert_team_run_view(run)
    return run


def _artifact_openable(artifact: dict) -> bool:
    return bool(
        artifact
        and str(artifact.get("path") or "").strip()
        and not artifact.get("placeholder")
        and artifact.get("exists") is not False
    )


def _phase_progress(run: dict) -> dict:
    phases = EXPERT_TEAM_PHASES.get(str(run.get("team_id") or ""), EXPERT_TEAM_PHASES["content-creator-team"])
    current = str(run.get("phase") or phases[0])
    total = len(phases)
    state = _status_class(run.get("status_label") or run.get("status"))
    try:
        phase_idx = phases.index(current)
    except ValueError:
        phase_idx = 0
    if state == "done":
        done = total
    elif state == "cancelled":
        done = min(total, max(0, phase_idx))
    elif current == "交付" and any(_artifact_openable(item) for item in run.get("artifacts") or []):
        done = total
    else:
        done = min(total, max(0, phase_idx))
    return {"done": done, "total": total, "current": current}


def _pending_questions(run: dict) -> list[dict]:
    rows = []
    for question in run.get("questions") or []:
        if (
            not isinstance(question, dict)
            or _question_is_terminal(question)
            or _is_stage_confirmation_question(question)
        ):
            continue
        rows.append(
            {
                "id": str(question.get("id") or ""),
                "title": str(question.get("title") or question.get("id") or ""),
                "type": str(question.get("type") or "text"),
                "required": question.get("required") is not False,
                "status": str(question.get("status") or "pending"),
                "source_task_id": str(question.get("source_task_id") or ""),
                "origin": str(question.get("origin") or ""),
            }
        )
    return rows


def _intake_view(run: dict) -> dict:
    required_pending = []
    optional_pending = []
    optional_status = "answered"
    optional_seen = False
    optional_skipped = False
    optional_answered = False
    for question in run.get("questions") or []:
        if not _is_intake_question(question):
            continue
        required = question.get("required") is not False
        status = str(question.get("status") or "pending").lower()
        terminal = status in QUESTION_TERMINAL_STATUSES
        if required and not terminal:
            required_pending.append(str(question.get("id") or ""))
            continue
        if not required:
            optional_seen = True
            if not terminal:
                optional_pending.append(str(question.get("id") or ""))
            elif status == "skipped":
                optional_skipped = True
            elif status == "answered":
                optional_answered = True
    if optional_pending:
        optional_status = "pending"
    elif optional_skipped:
        optional_status = "skipped"
    elif optional_seen and optional_answered:
        optional_status = "answered"
    elif not optional_seen:
        optional_status = "answered"
    return {
        "required_pending": required_pending,
        "optional_pending": optional_pending,
        "optional_status": optional_status,
    }


def _question_confirmation_for_view(question: dict) -> dict:
    qid = str(question.get("id") or "")
    required = question.get("required") is not False
    description = str(question.get("description") or "")
    if not description:
        description = "请补充确认信息，专家团才会继续推进。" if required else "可选补充，补充后结果更准确；也可以跳过后开始生成。"
    return {
        "id": f"question:{qid}",
        "kind": "question",
        "title": str(question.get("title") or question.get("id") or ""),
        "description": description,
        "fields": [
            {
                "id": qid,
                "type": str(question.get("type") or "text"),
                "required": required,
                "options": list(question.get("options") or []) if isinstance(question.get("options"), list) else [],
            }
        ],
        "actions": {"submit": "answer", "skip": "answer/skip_optional"} if not required else {"submit": "answer"},
        "source_task_id": str(question.get("source_task_id") or ""),
        "origin": str(question.get("origin") or ""),
        "status": str(question.get("status") or "pending"),
    }


def _structured_pending_confirmations(run: dict) -> list[dict]:
    rows = []
    terminal_statuses = {"answered", "done", "approved", "cancelled", "canceled", "dismissed"}
    for item in run.get("pending_confirmations") or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "pending")
        if status.lower() in terminal_statuses:
            continue
        kind = str(item.get("kind") or "clarification")
        if kind not in {"question", "stage_review", "clarification"}:
            kind = "clarification"
        fields = item.get("fields") if isinstance(item.get("fields"), list) else []
        actions = item.get("actions") if isinstance(item.get("actions"), dict) else {}
        rows.append(
            {
                "id": str(item.get("id") or f"{kind}:{len(rows) + 1}"),
                "kind": kind,
                "title": str(item.get("title") or "待确认事项"),
                "description": str(item.get("description") or "聊天中有待确认事项，请查看最新专家团回复。"),
                "fields": deepcopy(fields),
                "actions": deepcopy(actions),
                "source_task_id": str(item.get("source_task_id") or ""),
                "status": status,
            }
        )
    return rows


def _stage_review_confirmation_for_view(stage_review: dict) -> dict:
    output = stage_review.get("output") if isinstance(stage_review.get("output"), dict) else {}
    task_id = str(stage_review.get("task_id") or output.get("task_id") or "")
    title = str(stage_review.get("title") or output.get("title") or output.get("label") or "阶段成果")
    description = str(output.get("summary") or output.get("preview") or output.get("note") or "阶段结果已写入当前对话，请检查后确认是否进入下一阶段。")
    return {
        "id": f"stage:{task_id}",
        "kind": "stage_review",
        "title": title,
        "description": description,
        "fields": [],
        "actions": {"approve": "stage/approve", "revise": "stage/revise"},
        "source_task_id": task_id,
        "status": str(stage_review.get("status") or "awaiting_review"),
    }


def _pending_confirmations_for_view(
    run: dict,
    stage_review: dict,
    can_review_stage: bool,
    structured_confirmations: list[dict] | None = None,
) -> list[dict]:
    rows = []
    for question in run.get("questions") or []:
        if (
            not isinstance(question, dict)
            or _question_is_terminal(question)
            or _is_stage_confirmation_question(question)
        ):
            continue
        rows.append(_question_confirmation_for_view(question))
    rows.extend(structured_confirmations if structured_confirmations is not None else _structured_pending_confirmations(run))
    if can_review_stage:
        rows.append(_stage_review_confirmation_for_view(stage_review))
    return rows


def _review_items_for_view(run: dict) -> list[dict]:
    rows = []
    seen = set()
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    current_task_id = str(current.get("task_id") or "")
    outputs = run.get("stage_outputs") if isinstance(run.get("stage_outputs"), list) else []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        task_id = str(output.get("task_id") or "")
        status = str(output.get("status") or "")
        if current_task_id and task_id != current_task_id and status != "awaiting_review":
            continue
        for idx, title in enumerate(_extract_stage_confirmation_points(str(output.get("content") or "")), start=1):
            key = (task_id, title)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "id": f"review:{task_id or 'stage'}:{idx}",
                    "kind": "review_item",
                    "title": title,
                    "source_task_id": task_id,
                    "phase": str(output.get("phase") or ""),
                    "status": "pending",
                    "used_in_revision": bool(output.get("used_in_revision") or False),
                }
            )
            if len(rows) >= 12:
                return rows
    return rows


def expert_team_run_view(run: dict) -> dict:
    pending = _pending_questions(run)
    status = str(run.get("status") or "awaiting_user")
    execution_status = str(run.get("execution_status") or "idle")
    needs_resume = bool(run.get("needs_resume") or execution_status == "needs_resume")
    stage_review = _stage_review_for_view(run)
    structured_confirmations = _structured_pending_confirmations(run)
    intake = _intake_view(run)
    review_items = _review_items_for_view(run)
    can_review_stage = bool(
        _is_stage_gated_run(run)
        and not pending
        and not structured_confirmations
        and stage_review.get("actionable") is True
        and stage_review.get("task_id")
    )
    artifacts = []
    for item in run.get("artifacts") or []:
        if not isinstance(item, dict):
            continue
        openable = _artifact_openable(item)
        artifacts.append(
            {
                "id": str(item.get("id") or ""),
                "label": _office_material_visible_title(
                    item.get("label") or item.get("path") or item.get("id") or "产物",
                    run,
                    item.get("task_id") or "",
                ),
                "kind": str(item.get("kind") or "file"),
                "path": str(item.get("path") or ""),
                "status": str(item.get("status") or ""),
                "exists": item.get("exists") is not False,
                "placeholder": bool(item.get("placeholder")),
                "openable": openable,
            }
        )
    can_cancel = status == "running" and execution_status == "running" and bool(run.get("execution_stream_id"))
    can_retry = status == "error" or execution_status == "error"
    can_restart_stage = status == "cancelled" or execution_status == "cancelled"
    pending_confirmations = _pending_confirmations_for_view(
        run,
        stage_review,
        can_review_stage,
        structured_confirmations,
    )
    return {
        "status": status,
        "status_label": str(run.get("status_label") or STATUS_LABELS.get(status, status)),
        "execution_status": execution_status,
        "phase_progress": _phase_progress(run),
        "business_context": _business_context_for_view(run),
        "intake": intake,
        "pending_questions": pending,
        "pending_confirmations": pending_confirmations,
        "primary_confirmation": pending_confirmations[0] if pending_confirmations else {},
        "review_items": review_items,
        "artifacts": artifacts,
        "actions": {
            "can_answer": bool(pending),
            "can_resume": bool((needs_resume or can_restart_stage) and not pending and not structured_confirmations),
            "can_cancel": can_cancel,
            "can_retry": can_retry,
            "can_restart_stage": can_restart_stage,
            "can_open_artifact": any(item["openable"] for item in artifacts),
            "can_approve_stage": can_review_stage,
            "can_request_revision": can_review_stage,
        },
        "stage_review": stage_review,
        "health": {
            "needs_resume": needs_resume,
            "active_stream_id": str(run.get("execution_stream_id") or "") if can_cancel else "",
            "last_error": str(run.get("last_execution_error") or ""),
        },
    }


def expert_team_catalog() -> dict:
    teams = []
    for team_id in PUBLIC_EXPERT_TEAM_IDS:
        template = EXPERT_TEAM_TEMPLATES[team_id]
        teams.append(
            {
                "id": template["id"],
                "title": template["title"],
                "category": template["category"],
                "description": template["description"],
                "estimated": template.get("estimated", ""),
                "status_label": template.get("status_label", ""),
                "default_mode": template.get("default_mode", "A"),
                "default_action": template.get("default_action", "start"),
                "tags": deepcopy(template.get("tags") or []),
                "image": template.get("image", ""),
                "examples": deepcopy(template.get("examples") or []),
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
        "stage_outputs": [],
        "current_stage": {},
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
    run["execution_stream_id"] = stream_id
    run["execution_session_id"] = session_id
    run["execution_started_at"] = now
    run["execution_status"] = "running"
    run["last_execution_error"] = ""
    if _is_stage_gated_run(run):
        task = _current_stage_task(run)
        if task:
            previous_stage = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
            stage_status = "revision_running" if previous_stage.get("status") == "revision_running" else "running"
            _activate_stage_task(run, task, status=stage_status, stream_id=stream_id, feedback=previous_stage.get("feedback") or "")
            task["result_summary"] = ""
    else:
        run["phase"] = run.get("phase") or "生成初稿"
        for task in run.get("tasks") or []:
            if task.get("status") in {"waiting_user", "error", "pending", "running"}:
                task["status"] = "running"
                task["status_label"] = STATUS_LABELS["running"]
                task["result_summary"] = ""
                break
    run["updated_at"] = now
    _append_event(run, "execution_started", f"专家团开始生成 {run.get('phase') or '当前阶段'}")
    return write_expert_team_run(Path(workspace), run)


def mark_expert_team_execution_complete(workspace: Path, run_id: str, *, delivery: dict | None = None) -> dict:
    run = read_expert_team_run(Path(workspace), run_id)
    now = _now()
    delivery = delivery if isinstance(delivery, dict) else None
    if _is_stage_gated_run(run):
        return _mark_stage_execution_complete(Path(workspace), run, delivery=delivery, now=now)
    has_delivery = bool(
        delivery
        or any(isinstance(item, dict) and not item.get("placeholder") and item.get("exists") is not False for item in run.get("artifacts") or [])
    )
    if not has_delivery:
        run["status"] = "error"
        run["phase"] = run.get("phase") or "生成初稿"
        run["execution_status"] = "error"
        run["last_execution_error"] = "未检测到可交付结果"
        run["updated_at"] = now
        for task in run.get("tasks") or []:
            if str(task.get("status") or "") == "running":
                task["status"] = "error"
                task["status_label"] = STATUS_LABELS["error"]
                if not task.get("result_summary"):
                    task["result_summary"] = "本轮生成没有返回可交付结果。"
                break
        for member in run.get("members") or []:
            if str(member.get("status") or "") in {"执行中", "监督中"}:
                member["status"] = "执行异常"
        if not _has_event(run, "execution_empty"):
            _append_event(run, "execution_empty", "本轮生成没有返回可交付结果")
        return write_expert_team_run(Path(workspace), run)
    for task in run.get("tasks") or []:
        if str(task.get("status") or "") in {"pending", "running", "waiting_user"}:
            task["status"] = "done"
            task["status_label"] = STATUS_LABELS["done"]
            if not task.get("result_summary"):
                task["result_summary"] = "已写入当前对话。"
    for member in run.get("members") or []:
        if str(member.get("status") or "") in {"待命", "执行中", "监督中", "等待继续"}:
            member["status"] = "已完成"
    if delivery and not run.get("artifacts"):
        run["artifacts"] = [
            {
                "id": str(delivery.get("id") or "expert-team-chat-delivery"),
                "label": str(delivery.get("label") or "专家团生成结果"),
                "kind": str(delivery.get("kind") or "chat"),
                "path": str(delivery.get("path") or ""),
                "status": str(delivery.get("status") or "ready"),
                "placeholder": bool(delivery.get("placeholder", False)),
                "exists": delivery.get("exists") is not False,
                "note": str(delivery.get("note") or "已写入当前对话"),
            }
        ]
    elif not run.get("artifacts"):
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
    run["last_execution_error"] = ""
    run["completed_at"] = now
    run["updated_at"] = now
    if not _has_event(run, "run_done"):
        _append_event(run, "run_done", "专家团任务已完成")
    return write_expert_team_run(Path(workspace), run)


def mark_content_expert_team_execution_complete(workspace: Path, run_id: str, *, delivery: dict | None = None) -> dict:
    return mark_expert_team_execution_complete(workspace, run_id, delivery=delivery)


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


def _mark_stage_execution_complete(workspace: Path, run: dict, *, delivery: dict | None, now: str) -> dict:
    task = _current_stage_task(run)
    has_delivery = bool(delivery)
    if not task:
        run["status"] = "error"
        run["execution_status"] = "error"
        run["last_execution_error"] = "未找到当前阶段任务"
        run["updated_at"] = now
        return write_expert_team_run(Path(workspace), run)
    if not has_delivery:
        run["status"] = "error"
        run["execution_status"] = "error"
        run["execution_stream_id"] = ""
        run["last_execution_error"] = "未检测到可交付结果"
        run["updated_at"] = now
        _set_task_status(run, str(task.get("id") or ""), "error", result_summary="本轮生成没有返回可交付结果。")
        current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
        current.update({"status": "error", "updated_at": now})
        run["current_stage"] = current
        for member in run.get("members") or []:
            if isinstance(member, dict) and str(member.get("status") or "") == "执行中":
                member["status"] = "执行异常"
        _append_event(run, "execution_empty", "本轮生成没有返回可交付结果")
        return write_expert_team_run(Path(workspace), run)

    output = _ensure_stage_output(run, task)
    is_final_stage = _is_final_stage_task(run, task.get("id"))
    default_delivery_content = (
        "最终成果已写入当前对话，请检查后确认是否完成任务。"
        if is_final_stage
        else "阶段结果已写入当前对话，请检查后确认是否进入下一阶段。"
    )
    delivery_content = str(
        delivery.get("content")
        or delivery.get("note")
        or output.get("content")
        or default_delivery_content
    )
    violation = _office_material_output_violation(
        run,
        delivery_content,
        str(delivery.get("label") or output.get("label") or task.get("title") or ""),
    )
    if violation:
        run["status"] = "error"
        run["execution_status"] = "error"
        run["execution_stream_id"] = ""
        run["last_execution_error"] = f"{violation}，请重新生成。"
        run["updated_at"] = now
        output["status"] = "error"
        output["label"] = _office_material_visible_title(delivery.get("label") or output.get("label") or task.get("title") or "阶段产物", run, task.get("id"))
        output["kind"] = str(delivery.get("kind") or output.get("kind") or "chat")
        output["content"] = delivery_content
        output["note"] = "生成结果不符合办公材料口径，已阻止进入阶段复核。"
        output["updated_at"] = now
        _set_task_status(run, str(task.get("id") or ""), "error", result_summary="生成结果不符合办公材料口径，需重新生成。")
        current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
        current.update({"status": "error", "updated_at": now})
        run["current_stage"] = current
        for member in run.get("members") or []:
            if isinstance(member, dict) and str(member.get("status") or "") == "执行中":
                member["status"] = "执行异常"
        _append_event(run, "execution_invalid_style", "生成结果不符合办公材料口径")
        return write_expert_team_run(Path(workspace), run)
    output["status"] = "awaiting_review"
    output["label"] = _office_material_visible_title(delivery.get("label") or output.get("label") or task.get("title") or "阶段产物", run, task.get("id"))
    output["kind"] = str(delivery.get("kind") or output.get("kind") or "chat")
    output["content"] = delivery_content
    output["note"] = str(delivery.get("note") or "已写入当前对话")
    output["updated_at"] = now
    _replace_stage_confirmation_questions(run, task, delivery_content, now)
    revision_count = int(output.get("revision_count") or 0)
    run["status"] = "awaiting_user"
    run["phase"] = str(task.get("phase") or run.get("phase") or "生成初稿")
    run["execution_status"] = "done"
    run["execution_stream_id"] = ""
    run["last_execution_error"] = ""
    _set_task_status(
        run,
        str(task.get("id") or ""),
        "waiting_user",
        result_summary=(
            "最终成果已写入当前对话，等待你确认完成任务或提出修改意见。"
            if is_final_stage
            else "阶段结果已写入当前对话，等待你确认或提出修改意见。"
        ),
    )
    run["current_stage"] = {
        "task_id": str(task.get("id") or ""),
        "phase": str(task.get("phase") or run.get("phase") or ""),
        "title": str(task.get("title") or task.get("id") or "阶段任务"),
        "worker_id": str(task.get("worker_id") or ""),
        "worker_name": str(task.get("worker_name") or ""),
        "status": "awaiting_review",
        "revision_count": revision_count,
        "stream_id": "",
        "feedback": "",
        "updated_at": now,
    }
    for member in run.get("members") or []:
        if isinstance(member, dict) and str(member.get("id") or "") == str(task.get("worker_id") or ""):
            member["status"] = "等待确认"
    _append_event(run, "stage_output_ready", f"{task.get('title') or '阶段产物'}等待确认")
    run["updated_at"] = now
    return write_expert_team_run(Path(workspace), run)


def approve_expert_team_stage(workspace: Path, run_id: str) -> dict:
    run = read_expert_team_run(Path(workspace), run_id)
    if not _is_stage_gated_run(run):
        raise ValueError("Expert team does not support staged approval")
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    if current.get("status") != "awaiting_review":
        raise ValueError("Current expert team stage is not awaiting review")
    task = _task_by_id(run, current.get("task_id"))
    if not task:
        raise ValueError("Current expert team stage task not found")
    now = _now()
    output = _ensure_stage_output(run, task)
    output["status"] = "approved"
    output["approved_at"] = now
    output["updated_at"] = now
    _set_task_status(run, str(task.get("id") or ""), "done", result_summary="阶段成果已确认。")
    _set_member_status(run, str(task.get("worker_id") or ""), "已完成")
    current_idx = _task_index(run, task.get("id"))
    next_task = None
    for candidate in (run.get("tasks") or [])[current_idx + 1 :]:
        if isinstance(candidate, dict) and str(candidate.get("status") or "") != "done":
            next_task = candidate
            break
    run["execution_stream_id"] = ""
    run["last_execution_error"] = ""
    if next_task:
        run["status"] = "running"
        run["execution_status"] = "idle"
        _activate_stage_task(run, next_task, status="running")
        _append_event(run, "stage_approved", f"{task.get('title') or '当前阶段'}已确认，进入下一阶段")
    else:
        run["status"] = "done"
        run["phase"] = _delivery_phase(run)
        run["execution_status"] = "done"
        run["completed_at"] = now
        run["current_stage"] = {**current, "status": "done", "updated_at": now}
        for member in run.get("members") or []:
            if isinstance(member, dict) and str(member.get("status") or "") in {"待命", "监督中", "执行中", "等待确认"}:
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
        _append_event(run, "run_done", "专家团任务已完成")
    run["updated_at"] = now
    return write_expert_team_run(Path(workspace), run)


def request_expert_team_stage_revision(workspace: Path, run_id: str, feedback: str) -> dict:
    feedback = str(feedback or "").strip()
    if not feedback:
        raise ValueError("feedback is required")
    run = read_expert_team_run(Path(workspace), run_id)
    if not _is_stage_gated_run(run):
        raise ValueError("Expert team does not support staged revision")
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    if current.get("status") != "awaiting_review":
        raise ValueError("Current expert team stage is not awaiting review")
    task = _task_by_id(run, current.get("task_id"))
    if not task:
        raise ValueError("Current expert team stage task not found")
    now = _now()
    output = _ensure_stage_output(run, task)
    revision_count = int(output.get("revision_count") or 0) + 1
    output["revision_count"] = revision_count
    output["status"] = "revision_running"
    output["updated_at"] = now
    output.setdefault("feedback_history", []).append({"feedback": feedback, "created_at": now})
    run["status"] = "running"
    run["phase"] = str(task.get("phase") or run.get("phase") or "生成初稿")
    run["execution_status"] = "idle"
    run["execution_stream_id"] = ""
    run["last_execution_error"] = ""
    _set_task_status(run, str(task.get("id") or ""), "running", result_summary="正在根据你的修改意见重做当前阶段。")
    _activate_stage_task(run, task, status="revision_running", feedback=feedback)
    run["current_stage"]["revision_count"] = revision_count
    _append_event(run, "stage_revision_requested", f"{task.get('title') or '当前阶段'}收到修改意见")
    run["updated_at"] = now
    return write_expert_team_run(Path(workspace), run)


def _pending_required_questions(run: dict) -> list[dict]:
    return [
        question
        for question in run.get("questions") or []
        if (
            isinstance(question, dict)
            and question.get("required", True)
            and not _question_is_terminal(question)
            and not _is_stage_confirmation_question(question)
        )
    ]


def _pending_optional_questions(run: dict) -> list[dict]:
    return [
        question
        for question in run.get("questions") or []
        if (
            isinstance(question, dict)
            and question.get("required") is False
            and not _question_is_terminal(question)
            and not _is_stage_confirmation_question(question)
        )
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
            if _is_stage_gated_run(run):
                _activate_stage_task(run, first_task, status="running")
        if not _is_stage_gated_run(run):
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
    skip_optional = bool(body.get("skip_optional"))
    now = _now()
    answered_ids = set()
    for question in run.get("questions") or []:
        qid = str(question.get("id") or "")
        if qid not in answers:
            continue
        if _is_stage_confirmation_question(question):
            continue
        answer = str(answers.get(qid) or "").strip()
        required = question.get("required", True) is not False
        if not answer and required:
            continue
        if not answer and not required and not skip_optional:
            continue
        question["answer"] = answer
        question["status"] = "skipped" if (not answer and not required and skip_optional) else "answered"
        question["answered_at"] = now
        answered_ids.add(qid)
    if answered_ids and not any(event.get("type") == "questions_answered" for event in run.get("events") or []):
        _append_event(run, "questions_answered", "需求问答已确认")
    if _pending_required_questions(run):
        run["status"] = "awaiting_user"
        run["phase"] = run.get("phase") or "需求确认"
    elif _pending_optional_questions(run):
        run["status"] = "awaiting_user"
        run["phase"] = run.get("phase") or "需求确认"
    elif "visual_direction" in answered_ids:
        _start_direction_task(run)
        _complete_brand_moodboard(run)
    elif (
        run.get("status") == "awaiting_user"
        and isinstance(run.get("current_stage"), dict)
        and str((run.get("current_stage") or {}).get("status") or "") == "awaiting_review"
    ):
        run["execution_status"] = "done"
        run["execution_stream_id"] = ""
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
    run["execution_status"] = "cancelled"
    run["execution_stream_id"] = ""
    run["last_execution_error"] = ""
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
    try:
        template = _team_template(run.get("team_id") or "content-creator-team")
    except ValueError:
        template = _team_template("content-creator-team")
    adapted = {
        "run_id": str(run.get("run_id") or f"wr-{uuid.uuid4().hex[:8]}"),
        "source": "writeflow",
        "team_id": str(run.get("team_id") or template["id"]),
        "team_title": str(run.get("team_title") or template["title"]),
        "team_category": str(run.get("team_category") or template["category"]),
        "session_id": str(run.get("session_id") or ""),
        "title": str(run.get("title") or "专家团任务"),
        "prompt": str(run.get("prompt") or ""),
        "prompt_summary": str(run.get("prompt_summary") or run.get("prompt") or ""),
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
