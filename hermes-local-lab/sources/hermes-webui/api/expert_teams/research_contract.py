"""Versioned formal-report contract shared by future research/v3 stages.

The active public research profile remains ``research-report/v2``. This module
defines a separate v3 vocabulary and policy only; it does not activate a v3
profile, migrate existing runs, or change the v2 execution path.
"""

from __future__ import annotations

from copy import deepcopy


RESEARCH_REPORT_V2 = "research-report/v2"
RESEARCH_REPORT_V3 = "research-report/v3"
RESEARCH_ROUTE_V2 = "research_v2"
RESEARCH_ROUTE_V3 = "research_v3"
BOUNDED_REPORT_EXECUTION_POLICY = "bounded_report_v1"
RESEARCH_V3_START_FIELDS = frozenset({"writing_style", "depth"})

_WRITING_STYLES = {
    "central_enterprise": {
        "id": "central_enterprise",
        "label": "央国企",
        "audience": "央国企内部专题调研与决策参考",
        "tone": "正式、平实、完整论述，说明判断依据及其对工作的含义",
    },
    "government": {
        "id": "government",
        "label": "政府",
        "audience": "政府内部专题调研与决策参考",
        "tone": "正式、平实、完整论述，说明判断依据及其对工作的含义",
    },
}

_WORD_BUDGETS = {
    "standard": {
        "minimum": 8000,
        "target": 10000,
        "maximum": 12000,
        "chapter_unit_minimum": 1200,
        "chapter_unit_maximum": 2000,
    },
    "deep": {
        "minimum": 15000,
        "target": 17500,
        "maximum": 20000,
        "chapter_unit_minimum": 1200,
        "chapter_unit_maximum": 2000,
    },
}


class ResearchV3SpecError(ValueError):
    """A caller supplied a malformed v3 start specification."""

    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message


def research_contract_route(snapshot: object) -> str | None:
    """Return the one exact version route for a frozen profile or run snapshot."""
    version = (
        str(snapshot.get("research_contract_version") or "").strip()
        if isinstance(snapshot, dict)
        else ""
    )
    if version == RESEARCH_REPORT_V2:
        return RESEARCH_ROUTE_V2
    if version == RESEARCH_REPORT_V3:
        return RESEARCH_ROUTE_V3
    return None


def is_research_v3_run(run: object) -> bool:
    """Recognize only a fully bound v3 run; never infer it from source policy."""
    if not isinstance(run, dict):
        return False
    profile = run.get("launch_profile_snapshot")
    brief = run.get("document_brief")
    if not isinstance(profile, dict) or not isinstance(brief, dict):
        return False
    return bool(
        run.get("schema_version") == 3
        and str(run.get("launch_profile_id") or "") == "research-report"
        and str(run.get("team_id") or "") == "deep-research-team"
        and research_contract_route(profile) == RESEARCH_ROUTE_V3
        and str(profile.get("id") or "") == "research-report"
        and str(profile.get("team_id") or "") == "deep-research-team"
        and str(profile.get("document_type") or "") == "research_report"
        and str(run.get("product_mode") or "") == "standalone"
        and str(brief.get("product_mode") or "") == "standalone"
        and str(brief.get("document_type") or "") == "research_report"
        and str(brief.get("status") or "") == "confirmed"
    )


def normalize_research_v3_start_spec(spec: object) -> dict:
    """Validate the two caller-owned fields that a future v3 launch will freeze."""
    if type(spec) is not dict:
        raise ResearchV3SpecError(
            "research_v3_spec_invalid_type",
            "research_spec",
            "研究报告规格必须是对象",
        )
    unknown = sorted(set(spec) - RESEARCH_V3_START_FIELDS)
    if unknown:
        raise ResearchV3SpecError(
            "research_v3_spec_unknown_field",
            unknown[0],
            "研究报告规格包含未定义字段",
        )
    missing = sorted(RESEARCH_V3_START_FIELDS - set(spec))
    if missing:
        raise ResearchV3SpecError(
            "research_v3_spec_required",
            missing[0],
            "新版研究报告必须明确写作对象和深度",
        )

    normalized = {}
    for field, choices, label in (
        ("writing_style", _WRITING_STYLES, "写作对象"),
        ("depth", _WORD_BUDGETS, "研究深度"),
    ):
        value = spec[field]
        if type(value) is not str:
            raise ResearchV3SpecError(
                "research_v3_spec_invalid_type",
                field,
                f"{label}必须是字符串",
            )
        value = value.strip()
        if value not in choices:
            raise ResearchV3SpecError(
                "research_v3_spec_invalid_enum",
                field,
                f"{label}不支持该取值",
            )
        normalized[field] = value
    return normalized


def formal_report_writing_contract(*, writing_style: str, depth: str) -> dict:
    """Return detached v3 writing and outline policy for one normalized spec."""
    spec = normalize_research_v3_start_spec(
        {"writing_style": writing_style, "depth": depth}
    )
    return {
        "contract_version": RESEARCH_REPORT_V3,
        "execution_policy": BOUNDED_REPORT_EXECUTION_POLICY,
        "automatic_content_revision_limit": 0,
        "length_supplement_limit": 1,
        "target_result_grade": "formal_research_report",
        "writing_style": deepcopy(_WRITING_STYLES[spec["writing_style"]]),
        "depth": spec["depth"],
        "word_budget": deepcopy(_WORD_BUDGETS[spec["depth"]]),
        "sections": {
            "required_core": [
                "调研背景与目的",
                "基本情况",
                "主要问题及原因",
                "分析研判",
                "对策建议",
            ],
            "default_order": [
                "调研背景与目的",
                "基本情况",
                "主要问题及原因",
                "分析研判",
                "对策建议",
                "推进安排及需研究事项",
            ],
            "conditional": {
                "实践借鉴": "仅在存在可信案例时作为分析研判的子节纳入",
                "方案比较与适用条件": "按题目作为分析研判的子节纳入",
            },
        },
        "outline_budget": {
            "priority": [
                "主要问题及原因",
                "分析研判",
                "对策建议",
            ],
            "rule": "提纲的主要篇幅用于分析、比较和建议，不用通用科普替代研究判断。",
        },
        "evidence_trace_policy": {
            "body_binding": "实际正文片段须绑定可追溯来源；证据追踪与正文分别保存。",
            "declaration_storage": "原始声明保留在 metadata，不写入正文。",
            "user_background_origin": "user_background",
            "machine_check": "核验机器身份与引文出现，不将其表述为语义等价。",
            "review": "人工或模型内容审查为正式等级的必要门禁。",
        },
        "body_forbidden_tokens": [
            "source_id",
            "claim_id",
            "冻结Brief",
            "登记原声明",
        ],
        "integrity_rules": [
            "不得编造政策、职责、数字、事实或引用。",
            "虚构事实、伪引和关键矛盾必须阻断，不能以初步研究稿降级绕过。",
            "资料不足时先在既有授权检索边界补充；仍不足才标为初步研究稿。",
        ],
    }


def determine_research_result_grade(
    *,
    evidence_status: str,
    factual_blockers: object,
    word_count_passed: bool = False,
    structure_quality_passed: bool = False,
    human_or_model_review_passed: bool = False,
) -> str:
    """Classify a candidate result without letting an evidence failure hide blockers."""
    if type(factual_blockers) not in {list, tuple}:
        return "quality_review_required"
    if any(type(item) is not str or not item.strip() for item in factual_blockers):
        return "quality_review_required"
    if factual_blockers:
        return "blocked"
    if evidence_status == "insufficient_after_authorized_retrieval":
        return "preliminary_research_draft"
    if evidence_status != "sufficient":
        return "quality_review_required"
    if (
        word_count_passed is True
        and structure_quality_passed is True
        and human_or_model_review_passed is True
    ):
        return "formal_research_report"
    return "quality_review_required"
