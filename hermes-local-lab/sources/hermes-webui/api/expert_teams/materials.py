"""Office-material business context and output validation."""

from __future__ import annotations

import re

from .rich_draft import is_rich_draft_required, validate_rich_draft_text


FORBIDDEN_TERMS = [
    "公众号长文",
    "文章大纲",
    "标题党",
    "你有没有",
    "封面配图",
    "发布前检查",
    "发布版",
]

MATERIAL_DEFINITIONS = {
    "work_report": {
        "visible_title": "起草工作汇报初稿",
        "style_contract": "采用正式工作汇报口径，结构包含标题、开头概述、工作开展情况、存在问题、下一步工作安排。",
        "keywords": ("工作汇报", "汇报", "月度", "季度", "年度", "迎峰度夏", "保供电"),
    },
    "meeting_minutes": {
        "visible_title": "整理会议纪要初稿",
        "style_contract": "采用会议纪要口径，突出会议基本信息、主要议题、形成意见、责任分工和后续跟踪。",
        "keywords": ("会议纪要", "纪要", "会议"),
    },
    "notice": {
        "visible_title": "起草通知通报初稿",
        "style_contract": "采用内部通知通报口径，突出背景、事项安排、时间节点、责任分工和报送要求。",
        "keywords": ("通知", "通报", "安排", "检查"),
    },
    "plan": {
        "visible_title": "起草方案说明初稿",
        "style_contract": "采用方案说明口径，突出目标、现状问题、主要措施、进度安排和保障机制。",
        "keywords": ("方案", "措施", "行动", "机制"),
    },
    "summary_plan": {
        "visible_title": "起草总结计划初稿",
        "style_contract": "采用总结计划口径，突出阶段总结、成效亮点、问题不足和下一步计划。",
        "keywords": ("总结", "计划", "阶段性"),
    },
    "polish": {
        "visible_title": "润色办公材料",
        "style_contract": "保持原意，提升逻辑层次、正式表达和可读性，不改变事实和业务口径。",
        "keywords": ("润色", "优化表达", "改写", "修改"),
    },
    "office_material": {
        "visible_title": "起草办公材料初稿",
        "style_contract": "采用企业内部正式办公材料口径，结构清晰、表述稳妥、事实不确定处标注待确认。",
        "keywords": (),
    },
    "research_report": {
        "visible_title": "梳理专题研究材料",
        "style_contract": "采用调研材料和专题报告口径，突出研究问题、资料边界、案例线索、结构提纲和事实待核项。",
        "keywords": ("研究", "调研", "专题", "趋势", "案例", "报告"),
    },
}

PUBLIC_ACCOUNT_KEYWORDS = ("公众号", "文章", "发布")


def explicit_public_account_requested(text: str) -> bool:
    return any(keyword in (text or "") for keyword in PUBLIC_ACCOUNT_KEYWORDS)


def detect_material_type(text: str) -> str:
    raw = text or ""
    if explicit_public_account_requested(raw):
        return "public_account"
    for material_type, definition in MATERIAL_DEFINITIONS.items():
        if material_type == "office_material":
            continue
        if any(keyword in raw for keyword in definition.get("keywords", ())):
            return material_type
    return "office_material"


def normalize_visible_title(material_type: str) -> str:
    return MATERIAL_DEFINITIONS.get(material_type, MATERIAL_DEFINITIONS["office_material"])["visible_title"]


def style_contract(material_type: str) -> str:
    if material_type == "public_account":
        return "用户明确要求公众号、文章或发布场景时，才可按该场景处理。"
    return MATERIAL_DEFINITIONS.get(material_type, MATERIAL_DEFINITIONS["office_material"])["style_contract"]


def business_context_for_run(run: dict) -> dict:
    if str(run.get("team_id") or "") == "deep-research-team":
        return {
            "material_type": "research_report",
            "style_contract": style_contract("research_report"),
            "visible_title": normalize_visible_title("research_report"),
            "forbidden_terms": list(FORBIDDEN_TERMS),
        }
    prompt = " ".join(
        str(part or "")
        for part in [
            run.get("prompt"),
            run.get("title"),
            *(str((answer or {}).get("answer") or "") for answer in run.get("answers") or [] if isinstance(answer, dict)),
        ]
    )
    material_type = detect_material_type(prompt)
    if material_type == "public_account":
        visible_title = "起草内容稿件初稿"
        forbidden_terms: list[str] = []
    else:
        visible_title = normalize_visible_title(material_type)
        forbidden_terms = list(FORBIDDEN_TERMS)
    return {
        "material_type": material_type,
        "style_contract": style_contract(material_type),
        "visible_title": visible_title,
        "forbidden_terms": forbidden_terms,
    }


def content_summary(text: str, limit: int = 140) -> str:
    clean = re.sub(r"\s+", " ", (text or "").replace("\\n", "\n")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def normalize_output_text(text: str) -> str:
    return (text or "").replace("\\n", "\n").strip()


def validate_office_material_output(text: str, material_type: str) -> dict:
    normalized = normalize_output_text(text)
    if material_type == "public_account":
        return {"status": "pass", "violations": [], "message": ""}
    violations = [term for term in FORBIDDEN_TERMS if term in normalized]
    if material_type == "work_report":
        required = ["一、工作开展情况", "二、存在问题", "三、下一步工作安排"]
    elif material_type == "notice":
        required = ["时间", "责任", "报送"]
    else:
        required = []
    missing_sections = [section for section in required if section not in normalized]
    if violations or missing_sections:
        return {
            "status": "rewrite_required",
            "violations": violations,
            "missing_sections": missing_sections,
            "message": "草稿未通过办公材料口径校验，请重新生成。",
        }
    return {"status": "pass", "violations": [], "missing_sections": [], "message": ""}


def validate_final_document_text(text: str, material_type: str) -> dict:
    normalized = normalize_output_text(text)
    missing = []
    if not re.search(r"(?m)^#\s+\S+", normalized):
        missing.append("Markdown 一级标题")
    if len(normalized) < 160:
        missing.append("完整最终正文")
    if material_type != "meeting_minutes":
        if "|" not in normalized or not re.search(r"(?m)^\s*\|?.*---.*\|", normalized):
            missing.append("至少 1 个 Markdown 表格")
        if not re.search(r"!\[[^\]]+\]\([^)]+\)|```\s*mermaid\b", normalized, flags=re.IGNORECASE):
            missing.append("至少 1 个可追溯图片引用或 Mermaid 图源")
    violations = [term for term in FORBIDDEN_TERMS if term in normalized]
    if missing or violations:
        return {
            "status": "rewrite_required",
            "violations": violations,
            "missing_sections": missing,
            "message": "最终交付必须是可直接生成 DOCX 的完整 Markdown 正文，不能只有阶段摘要或建议。",
        }
    return {"status": "pass", "violations": [], "missing_sections": [], "message": ""}


def validate_stage_output(
    text: str,
    material_type: str,
    task_id: str,
    team_id: str = "",
    *,
    contract_version: str = "legacy",
    artifact: dict | None = None,
    brief: dict | None = None,
) -> dict:
    if contract_version == "expert-team-contract/v1":
        from .stage_artifacts import StageArtifactError, validate_stage_artifact

        if not isinstance(artifact, dict) or not isinstance(brief, dict):
            return {
                "status": "rewrite_required",
                "violations": [],
                "missing_sections": ["结构化阶段产物"],
                "message": "企业合同阶段必须使用结构化 Artifact 校验。",
            }
        try:
            validation = validate_stage_artifact(
                artifact,
                brief=brief,
                approved_inputs=artifact.get("input_refs") or [],
            )
        except StageArtifactError as exc:
            return {
                "status": "rewrite_required",
                "violations": [exc.code],
                "missing_sections": [],
                "message": "阶段产物未通过企业语义合同校验。",
            }
        if artifact.get("validation_status") != "valid" or int(validation.get("blocking_count") or 0):
            return {
                "status": "rewrite_required",
                "violations": ["blocking_issues"],
                "missing_sections": [],
                "message": "阶段产物仍有未解决的阻断问题。",
            }
        constraints = brief.get("content_constraints") if isinstance(brief.get("content_constraints"), dict) else {}
        required_assets = set(constraints.get("required_asset_kinds") or [])
        asset_requests = (artifact.get("payload") or {}).get("asset_requests") or []
        actual_assets = {
            str(item.get("kind") or "") for item in asset_requests if isinstance(item, dict)
        }
        missing_assets = sorted(required_assets - actual_assets)
        if missing_assets:
            return {
                "status": "rewrite_required",
                "violations": [],
                "missing_sections": [f"asset_request:{kind}" for kind in missing_assets],
                "message": "Brief 明确要求的视觉资产尚未形成可追溯 asset request。",
            }
        return {"status": "pass", "violations": [], "missing_sections": [], "message": ""}

    normalized = normalize_output_text(text)
    task = str(task_id or "")
    team = str(team_id or "")
    if material_type == "public_account":
        return {"status": "pass", "violations": [], "missing_sections": [], "message": ""}
    if (team == "content-creator-team" and task == "delivery") or (
        team == "deep-research-team" and task == "review"
    ):
        return validate_final_document_text(normalized, material_type)
    if task in {"plan", "direction"}:
        missing = []
        if "阶段摘要" not in normalized and "阶段目标" not in normalized:
            missing.append("阶段摘要/阶段目标")
        if "待补充" not in normalized and "待人工" not in normalized:
            missing.append("待补充/待人工")
        if missing:
            return {
                "status": "rewrite_required",
                "violations": [],
                "missing_sections": missing,
                "message": "阶段计划不完整，请重新生成。",
            }
        return {"status": "pass", "violations": [], "missing_sections": [], "message": ""}
    if is_rich_draft_required(material_type, task, team_id):
        return validate_rich_draft_text(normalized)
    if team == "deep-research-team":
        required = ["阶段", "待人工"] if task in {"research", "evidence", "outline", "draft", "review"} else []
        missing = [section for section in required if section not in normalized]
        violations = [term for term in FORBIDDEN_TERMS if term in normalized]
        if missing or violations:
            return {
                "status": "rewrite_required",
                "violations": violations,
                "missing_sections": missing,
                "message": "研究材料阶段产物不完整，请重新生成。",
            }
        return {"status": "pass", "violations": [], "missing_sections": [], "message": ""}
    if task in {"materials", "polish", "delivery"}:
        violations = [term for term in FORBIDDEN_TERMS if term in normalized]
        if violations:
            return {
                "status": "rewrite_required",
                "violations": violations,
                "missing_sections": [],
                "message": "阶段产物存在文章化表达，请重新生成。",
            }
        return {"status": "pass", "violations": [], "missing_sections": [], "message": ""}
    return validate_office_material_output(normalized, material_type)


def structured_output_from_delivery(delivery: dict, business_context: dict) -> dict:
    content = normalize_output_text(str((delivery or {}).get("content") or ""))
    visible_title = str(business_context.get("visible_title") or "专家团成果").strip()
    title = str((delivery or {}).get("label") or visible_title).strip()
    summary = content_summary(content)
    return {
        "id": str((delivery or {}).get("id") or "expert-team-chat-delivery"),
        "kind": str((delivery or {}).get("kind") or "chat"),
        "title": title,
        "visible_title": visible_title,
        "summary": summary,
        "preview": summary,
        "content": content,
        "content_length": len(content),
        "has_long_content": len(content) > 120,
        "locator": "artifact" if (delivery or {}).get("artifact_id") else "chat",
        "artifact_id": (delivery or {}).get("artifact_id") or "",
    }


def stage_result_from_output(output: dict, validation: dict | None = None) -> dict:
    content = normalize_output_text(str((output or {}).get("content") or ""))
    stage_id = str((output or {}).get("task_id") or "")
    worker_id = str((output or {}).get("worker_id") or "")
    summary = str((output or {}).get("summary") or content_summary(content)).strip()
    review_items = []
    for idx, line in enumerate(re.findall(r"(?:^|\n)\s*(?:[-*]|\d+[.、])\s*([^\n]{4,160})", content), 1):
        if any(marker in line for marker in ("补充", "确认", "待核", "待提供")):
            review_items.append({"id": f"{stage_id or 'stage'}-review-{idx}", "title": line.strip(), "status": "pending"})
    return {
        "stage_id": stage_id,
        "worker_id": worker_id,
        "summary": summary,
        "deliverable": content,
        "review_items": review_items[:8],
        "next_action": str((output or {}).get("next_action") or "请复核当前阶段成果。"),
        "validation": validation or {},
    }
