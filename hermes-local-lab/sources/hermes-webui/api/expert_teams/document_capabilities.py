"""Server-owned Brief capabilities shared by standalone launch and UI views."""

from __future__ import annotations

from copy import deepcopy


_COMMON_FIELDS = (
    {
        "path": "exact_title",
        "label": "文档标题",
        "control": "text",
        "required": True,
        "placeholder": "例如：迎峰度夏保供电重点工作月度汇报",
        "help": "填写最终文档封面和正文使用的准确标题。",
    },
    {
        "path": "purpose",
        "label": "文档用途",
        "control": "textarea",
        "required": True,
        "placeholder": "例如：向分管领导汇报进展，并明确下一步安排",
        "help": "说明这份文档要帮助读者了解或决定什么。",
    },
    {
        "path": "audience",
        "label": "阅读对象",
        "control": "text",
        "required": True,
        "placeholder": "例如：公司分管领导、项目决策小组",
        "help": "填写主要阅读者，便于控制专业程度和表达方式。",
    },
    {
        "path": "usage_scenario",
        "label": "使用场景",
        "control": "text",
        "required": True,
        "placeholder": "例如：月度工作例会、专题研究评审会",
        "help": "说明文档将在什么场合阅读、汇报或评审。",
    },
)


_CAPABILITIES = {
    "content-work-report": {
        "capability_id": "content-work-report",
        "document_type": "work_report",
        "task_mode": "create",
        "releases": {
            "standalone": {
                "released": True,
                "render_template_id": "standalone-work-report",
            },
            "enterprise": {
                "released": True,
                "render_template_id": "enterprise-work-report",
            },
        },
        "brief_schema": (
            *_COMMON_FIELDS,
            {
                "path": "details.reporting_period",
                "label": "汇报周期",
                "control": "text",
                "required": True,
                "placeholder": "例如：2026年7月或2026年上半年",
                "help": "填写本次汇报覆盖的准确时间范围。",
            },
            {
                "path": "details.reporting_unit",
                "label": "汇报单位",
                "control": "text",
                "required": True,
                "placeholder": "例如：生产运营部",
                "help": "填写承担本次汇报的部门、项目组或单位。",
            },
        ),
        "standalone_defaults": {
            "source_policy": {
                "mode": "provided_only",
                "as_of_date": "",
                "citation_style": "none",
                "unknown_fact_action": "allow_labeled_placeholder",
                "source_refs": [],
            },
            "data_handling": {},
            "document_control": {},
            "content_constraints": {
                "required_sections": [],
                "must_include": [],
                "must_avoid": [],
            },
            "details": {"reporting_period": "", "reporting_unit": ""},
            "approval": {},
        },
        "source_requirement": {
            "minimum_ready": 0,
            "empty_help": "可以不添加资料；缺失事实和数据将明确标注为“待补充”或“需人工确认”。",
        },
    },
    "research-report": {
        "capability_id": "research-report",
        "document_type": "research_report",
        "task_mode": "create",
        "releases": {
            "standalone": {
                "released": True,
                "render_template_id": "standalone-research-report",
            },
            "enterprise": {
                "released": True,
                "render_template_id": "enterprise-research-report",
            },
        },
        "brief_schema": (
            *_COMMON_FIELDS,
            {
                "path": "details.core_question",
                "label": "核心研究问题",
                "control": "textarea",
                "required": True,
                "placeholder": "例如：人工智能辅助办公在现有业务中应如何落地",
                "help": "用一个可研究、可回答的问题限定报告主线。",
            },
            {
                "path": "details.time_range.start",
                "label": "研究起始日期",
                "control": "date",
                "required": True,
                "placeholder": "例如：2025-01-01",
                "help": "填写纳入研究材料和事实判断的起始日期。",
            },
            {
                "path": "details.time_range.end",
                "label": "研究截止日期",
                "control": "date",
                "required": True,
                "placeholder": "例如：2026-07-25",
                "help": "填写本次研究覆盖的截止日期。",
            },
            {
                "path": "source_policy.as_of_date",
                "label": "资料有效截至日期",
                "control": "date",
                "required": True,
                "placeholder": "例如：2026-07-25",
                "help": "报告中的事实与引用将以该日期前可核对资料为准。",
            },
        ),
        "standalone_defaults": {
            "source_policy": {
                "mode": "provided_only",
                "as_of_date": "",
                "citation_style": "source_id",
                "unknown_fact_action": "block_final",
                "source_refs": [],
            },
            "data_handling": {},
            "document_control": {},
            "content_constraints": {
                "required_sections": [],
                "must_include": [],
                "must_avoid": [],
            },
            "details": {
                "core_question": "",
                "time_range": {"start": "", "end": ""},
            },
            "approval": {},
        },
        "source_requirement": {
            "minimum_ready": 1,
            "empty_help": "研究报告必须至少添加一份可核对资料，并在正文中保留引用。",
        },
    },
}

_STANDALONE_SOURCE_PATCH_PATHS = frozenset(
    {
        "source_policy.mode",
        "source_policy.as_of_date",
        "source_policy.citation_style",
        "source_policy.unknown_fact_action",
        "source_policy.source_refs",
    }
)


def _matching_capabilities(document_type: str | None, task_mode: str | None) -> list[dict]:
    document = str(document_type or "").strip()
    mode = str(task_mode or "create").strip() or "create"
    return [
        capability
        for capability in _CAPABILITIES.values()
        if isinstance(capability, dict)
        and str(capability.get("document_type") or "") == document
        and str(capability.get("task_mode") or "") == mode
    ]


def _has_valid_capability_shape(capability: dict, *, product_mode: str) -> bool:
    """Fail closed when the mutable registry contains an incomplete entry."""
    if not str(capability.get("capability_id") or "").strip():
        return False
    if not str(capability.get("document_type") or "").strip():
        return False
    if not str(capability.get("task_mode") or "").strip():
        return False
    schema = capability.get("brief_schema")
    if not isinstance(schema, (list, tuple)):
        return False
    schema_paths = [
        str(field.get("path") or "").strip()
        for field in schema
        if isinstance(field, dict)
    ]
    if len(schema_paths) != len(schema) or any(not path for path in schema_paths):
        return False
    if len(schema_paths) != len(set(schema_paths)):
        return False
    requirement = capability.get("source_requirement")
    if not isinstance(requirement, dict):
        return False
    minimum_ready = requirement.get("minimum_ready")
    if (
        not isinstance(minimum_ready, int)
        or isinstance(minimum_ready, bool)
        or minimum_ready < 0
    ):
        return False
    if product_mode == "standalone" and not isinstance(
        capability.get("standalone_defaults"), dict
    ):
        return False
    return True


def get_document_capability(
    document_type: str | None,
    task_mode: str | None = "create",
) -> dict:
    """Return one detached business capability, independent of product mode."""
    matches = _matching_capabilities(document_type, task_mode)
    return deepcopy(matches[0]) if len(matches) == 1 else {}


def get_document_capability_by_id(capability_id: str | None) -> dict:
    capability = _CAPABILITIES.get(str(capability_id or "").strip())
    return deepcopy(capability) if isinstance(capability, dict) else {}


def has_document_capability(document_type: str | None) -> bool:
    document = str(document_type or "").strip()
    return any(
        isinstance(capability, dict)
        and str(capability.get("document_type") or "") == document
        for capability in _CAPABILITIES.values()
    )


def resolve_document_capability(
    document_type: str | None,
    task_mode: str | None,
    *,
    product_mode: str | None,
) -> dict | None:
    """Resolve one released mode-specific capability without leaking KeyError."""
    normalized_mode = str(product_mode or "").strip()
    if normalized_mode not in {"standalone", "enterprise"}:
        return None
    matches = _matching_capabilities(document_type, task_mode)
    if len(matches) != 1:
        return None
    capability = deepcopy(matches[0])
    if not _has_valid_capability_shape(capability, product_mode=normalized_mode):
        return None
    releases = capability.get("releases")
    release = releases.get(normalized_mode) if isinstance(releases, dict) else None
    if (
        not isinstance(release, dict)
        or release.get("released") is not True
        or not str(release.get("render_template_id") or "").strip()
    ):
        return None
    capability["product_mode"] = normalized_mode
    capability["render_template_id"] = str(release["render_template_id"])
    capability["brief_schema"] = list(deepcopy(capability.get("brief_schema") or ()))
    capability["source_requirement"] = deepcopy(
        capability.get("source_requirement") or {}
    )
    return capability


def standalone_brief_defaults(
    document_type: str | None,
    task_mode: str | None = "create",
) -> dict:
    return get_document_capability(document_type, task_mode).get(
        "standalone_defaults", {}
    )


def brief_schema(
    document_type: str | None,
    task_mode: str | None = "create",
) -> list[dict]:
    return list(
        get_document_capability(document_type, task_mode).get("brief_schema", ())
    )


def source_requirement(
    document_type: str | None,
    task_mode: str | None = "create",
) -> dict:
    return get_document_capability(document_type, task_mode).get(
        "source_requirement", {}
    )


def standalone_editable_brief_paths(
    document_type: str | None,
    task_mode: str | None = "create",
) -> frozenset[str]:
    schema_paths = {
        str(field.get("path") or "")
        for field in brief_schema(document_type, task_mode)
        if isinstance(field, dict) and str(field.get("path") or "")
    }
    return frozenset(schema_paths) | _STANDALONE_SOURCE_PATCH_PATHS
