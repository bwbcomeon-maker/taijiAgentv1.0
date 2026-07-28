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


def _required_field(
    path: str,
    label: str,
    *,
    control: str = "text",
    placeholder: str,
    help_text: str,
) -> dict:
    return {
        "path": path,
        "label": label,
        "control": control,
        "required": True,
        "placeholder": placeholder,
        "help": help_text,
    }


def _content_defaults(
    *,
    required_sections: list[str],
    details: dict,
    unknown_fact_action: str = "allow_labeled_placeholder",
) -> dict:
    return {
        "source_policy": {
            "mode": "provided_only",
            "as_of_date": "",
            "citation_style": "none",
            "unknown_fact_action": unknown_fact_action,
            "source_refs": [],
        },
        "data_handling": {},
        "document_control": {},
        "content_constraints": {
            "required_sections": list(required_sections),
            "must_include": [],
            "must_avoid": [],
        },
        "details": deepcopy(details),
        "approval": {},
    }


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
                "required_sections": [
                    "工作开展情况",
                    "存在问题",
                    "下一步工作安排",
                ],
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
    "content-meeting-minutes": {
        "capability_id": "content-meeting-minutes",
        "document_type": "meeting_minutes",
        "task_mode": "create",
        "releases": {
            "standalone": {
                "released": True,
                "render_template_id": "meeting-minutes",
            },
        },
        "brief_schema": (
            *_COMMON_FIELDS,
            _required_field(
                "details.meeting_time",
                "会议时间",
                placeholder="例如：2026年7月28日 14:00",
                help_text="填写会议召开的准确日期和时间。",
            ),
            _required_field(
                "details.meeting_location",
                "会议地点",
                placeholder="例如：公司三楼第一会议室",
                help_text="填写线下地点或线上会议方式。",
            ),
            _required_field(
                "details.chairperson",
                "主持人",
                placeholder="例如：生产运营部负责人",
                help_text="填写会议主持人姓名或职务。",
            ),
            _required_field(
                "details.attendee_scope",
                "参会范围",
                control="textarea",
                placeholder="例如：相关部门负责人、项目组成员",
                help_text="填写参会人员名单或人员范围。",
            ),
        ),
        "standalone_defaults": _content_defaults(
            required_sections=["会议基本情况", "议定事项", "责任分工", "后续跟踪"],
            details={
                "meeting_time": "",
                "meeting_location": "",
                "chairperson": "",
                "attendee_scope": "",
            },
        ),
        "source_requirement": {
            "minimum_ready": 0,
            "empty_help": "可以不添加资料；不明确的议题、结论和责任信息将标注为“待补充”。",
        },
    },
    "content-notice": {
        "capability_id": "content-notice",
        "document_type": "notice",
        "task_mode": "create",
        "releases": {
            "standalone": {
                "released": True,
                "render_template_id": "general-proposal",
            },
        },
        "brief_schema": (
            *_COMMON_FIELDS,
            _required_field(
                "details.issuing_unit",
                "发文单位",
                placeholder="例如：安全生产部",
                help_text="填写通知或通报的发布部门、项目组或单位。",
            ),
            _required_field(
                "details.execution_deadline",
                "执行时间或截止时间",
                placeholder="例如：2026年8月15日前",
                help_text="填写事项开始执行的时间或报送截止时间。",
            ),
        ),
        "standalone_defaults": _content_defaults(
            required_sections=["背景与总体要求", "通知事项", "时间安排", "责任分工", "报送要求"],
            details={"issuing_unit": "", "execution_deadline": ""},
        ),
        "source_requirement": {
            "minimum_ready": 0,
            "empty_help": "可以不添加资料；不明确的对象、时间和责任要求将标注为“待补充”。",
        },
    },
    "content-plan": {
        "capability_id": "content-plan",
        "document_type": "plan",
        "task_mode": "create",
        "releases": {
            "standalone": {
                "released": True,
                "render_template_id": "general-proposal",
            },
        },
        "brief_schema": (
            *_COMMON_FIELDS,
            _required_field(
                "details.implementation_period",
                "实施周期",
                placeholder="例如：2026年8月至10月",
                help_text="填写方案实施的起止时间或阶段范围。",
            ),
            _required_field(
                "details.lead_unit",
                "牵头单位",
                placeholder="例如：营销服务部",
                help_text="填写负责统筹推进方案的部门、项目组或单位。",
            ),
        ),
        "standalone_defaults": _content_defaults(
            required_sections=["目标", "现状与问题", "主要措施", "进度安排", "保障机制"],
            details={"implementation_period": "", "lead_unit": ""},
        ),
        "source_requirement": {
            "minimum_ready": 0,
            "empty_help": "可以不添加资料；缺失的现状、数据和责任信息将标注为“待补充”。",
        },
    },
    "content-summary-plan": {
        "capability_id": "content-summary-plan",
        "document_type": "summary_plan",
        "task_mode": "create",
        "releases": {
            "standalone": {
                "released": True,
                "render_template_id": "general-proposal",
            },
        },
        "brief_schema": (
            *_COMMON_FIELDS,
            _required_field(
                "details.summary_period",
                "总结周期",
                placeholder="例如：2026年上半年",
                help_text="填写本次总结覆盖的准确时间范围。",
            ),
            _required_field(
                "details.responsible_unit",
                "责任单位",
                placeholder="例如：数字化工作部",
                help_text="填写承担本次总结和后续计划的部门、项目组或单位。",
            ),
        ),
        "standalone_defaults": _content_defaults(
            required_sections=["阶段性工作总结", "成效与亮点", "问题与不足", "下一步工作计划"],
            details={"summary_period": "", "responsible_unit": ""},
        ),
        "source_requirement": {
            "minimum_ready": 0,
            "empty_help": "可以不添加资料；缺失的成效、数据和计划信息将标注为“待补充”。",
        },
    },
    "content-polish": {
        "capability_id": "content-polish",
        "document_type": "other_office_material",
        "task_mode": "polish",
        "releases": {
            "standalone": {
                "released": True,
                "render_template_id": "general-proposal",
            },
        },
        "brief_schema": (
            *_COMMON_FIELDS,
            _required_field(
                "details.polish_goal",
                "润色目标",
                control="textarea",
                placeholder="例如：提升逻辑层次和正式表达，压缩重复内容",
                help_text="说明希望重点改善的结构、语气和可读性问题。",
            ),
            _required_field(
                "details.expression_boundary",
                "表达边界",
                control="textarea",
                placeholder="例如：保留原有事实、数字、专名和明确结论",
                help_text="填写不得改变、删除或新增的事实和表达边界。",
            ),
        ),
        "standalone_defaults": _content_defaults(
            required_sections=["润色后正文", "修改说明"],
            details={"polish_goal": "", "expression_boundary": ""},
            unknown_fact_action="block_final",
        ),
        "source_requirement": {
            "minimum_ready": 1,
            "empty_help": "请先添加需要润色的原始材料。",
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
                "required_sections": [
                    "研究问题",
                    "证据",
                    "分析",
                    "结论边界",
                    "引用",
                ],
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
    if product_mode == "standalone":
        defaults = capability.get("standalone_defaults")
        if not isinstance(defaults, dict):
            return False
        constraints = defaults.get("content_constraints")
        if not isinstance(constraints, dict):
            return False
        for key in ("required_sections", "must_include", "must_avoid"):
            values = constraints.get(key)
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) or not value.strip() for value in values)
                or len(values) != len(set(values))
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
