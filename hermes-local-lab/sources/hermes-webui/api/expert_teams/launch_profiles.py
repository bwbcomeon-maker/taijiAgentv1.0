"""Server-owned launch profiles for standalone expert-team runs."""

from __future__ import annotations

from copy import deepcopy

from .contracts import ContractError
from .document_capabilities import resolve_document_capability


CONTENT_CREATOR_TEAM_ID = "content-creator-team"
DEEP_RESEARCH_TEAM_ID = "deep-research-team"

CONTENT_PHASES = [
    {"id": "plan", "title": "专家团计划", "phase": "流程安排", "worker_id": "director", "worker_name": "写作总导演", "executor": "model", "artifact_type": "writing_plan", "depends_on": []},
    {"id": "materials", "title": "素材整理", "phase": "素材整理", "worker_id": "material", "worker_name": "资料整理专家", "executor": "model", "artifact_type": "material_ledger", "depends_on": ["plan"]},
    {"id": "draft", "title": "起草富内容初稿", "phase": "富内容初稿", "worker_id": "writer", "worker_name": "文案创作专家", "executor": "model", "artifact_type": "document_draft", "depends_on": ["plan", "materials"]},
    {"id": "polish", "title": "审稿打磨", "phase": "审稿打磨", "worker_id": "reviewer", "worker_name": "审稿专家", "executor": "model", "artifact_type": "reviewed_document", "depends_on": ["materials", "draft"]},
    {"id": "delivery", "title": "交付确认", "phase": "交付确认", "worker_id": "delivery", "worker_name": "交付复核专家", "executor": "system", "artifact_type": "delivery_manifest", "depends_on": ["polish"]},
]

DEEP_RESEARCH_PHASES = [
    {"id": "direction", "title": "确定研究方向", "phase": "研究方向", "worker_id": "director", "worker_name": "研究总导演", "executor": "model", "artifact_type": "research_charter", "depends_on": []},
    {"id": "research", "title": "补充案例素材", "phase": "资料调研", "worker_id": "researcher", "worker_name": "资料研究员", "executor": "model", "artifact_type": "source_register", "depends_on": ["direction"]},
    {"id": "evidence", "title": "事实核验", "phase": "事实核验", "worker_id": "evidence", "worker_name": "事实核验专家", "executor": "model", "artifact_type": "evidence_matrix", "depends_on": ["research"]},
    {"id": "outline", "title": "结构提纲", "phase": "结构提纲", "worker_id": "architect", "worker_name": "结构架构师", "executor": "model", "artifact_type": "research_outline", "depends_on": ["evidence"]},
    {"id": "draft", "title": "研究富内容初稿", "phase": "富内容初稿", "worker_id": "writer", "worker_name": "材料起草专家", "executor": "model", "artifact_type": "research_document_draft", "depends_on": ["outline", "evidence"]},
    {"id": "review", "title": "复核交付", "phase": "复核交付", "worker_id": "reviewer", "worker_name": "复核专家", "executor": "model", "artifact_type": "reviewed_research_document", "depends_on": ["evidence", "outline", "draft"]},
]


_LAUNCH_PROFILES = {
    "content-work-report": {
        "id": "content-work-report",
        "capability_id": "content-work-report",
        "team_id": CONTENT_CREATOR_TEAM_ID,
        "document_type": "work_report",
        "intake_example_id": "work_report",
        "task_mode": "create",
        "render_template_id": "standalone-work-report",
        "stages": CONTENT_PHASES,
        "review_policy": {"kind": "local_confirmation"},
    },
    "content-meeting-minutes": {
        "id": "content-meeting-minutes",
        "capability_id": "content-meeting-minutes",
        "team_id": CONTENT_CREATOR_TEAM_ID,
        "document_type": "meeting_minutes",
        "intake_example_id": "meeting_minutes",
        "task_mode": "create",
        "render_template_id": "standalone-meeting-minutes",
        "stages": CONTENT_PHASES,
        "review_policy": {"kind": "local_confirmation"},
    },
    "content-notice": {
        "id": "content-notice",
        "capability_id": "content-notice",
        "team_id": CONTENT_CREATOR_TEAM_ID,
        "document_type": "notice",
        "intake_example_id": "notice",
        "task_mode": "create",
        "render_template_id": "standalone-office-material",
        "stages": CONTENT_PHASES,
        "review_policy": {"kind": "local_confirmation"},
    },
    "content-plan": {
        "id": "content-plan",
        "capability_id": "content-plan",
        "team_id": CONTENT_CREATOR_TEAM_ID,
        "document_type": "plan",
        "intake_example_id": "plan",
        "task_mode": "create",
        "render_template_id": "standalone-office-material",
        "stages": CONTENT_PHASES,
        "review_policy": {"kind": "local_confirmation"},
    },
    "content-summary-plan": {
        "id": "content-summary-plan",
        "capability_id": "content-summary-plan",
        "team_id": CONTENT_CREATOR_TEAM_ID,
        "document_type": "summary_plan",
        "intake_example_id": "summary_plan",
        "task_mode": "create",
        "render_template_id": "standalone-office-material",
        "stages": CONTENT_PHASES,
        "review_policy": {"kind": "local_confirmation"},
    },
    "content-polish": {
        "id": "content-polish",
        "capability_id": "content-polish",
        "team_id": CONTENT_CREATOR_TEAM_ID,
        "document_type": "other_office_material",
        "intake_example_id": "polish",
        "task_mode": "polish",
        "render_template_id": "standalone-office-material",
        "stages": CONTENT_PHASES,
        "review_policy": {"kind": "local_confirmation"},
    },
    "research-report": {
        "id": "research-report",
        "research_contract_version": "research-report/v2",
        "capability_id": "research-report",
        "team_id": DEEP_RESEARCH_TEAM_ID,
        "document_type": "research_report",
        "intake_example_id": "research_report",
        "task_mode": "create",
        "render_template_id": "standalone-research-report",
        "stages": DEEP_RESEARCH_PHASES,
        "post_approval_system_steps": [
            {
                "id": "delivery",
                "executor": "system",
                "artifact_type": "delivery_manifest",
                "depends_on": ["review"],
                "trigger": "canonical_approved",
                "visible_progress": False,
            }
        ],
        "review_policy": {"kind": "local_confirmation"},
    },
}


def _capability_fields(profile: dict, *, product_mode: str) -> dict:
    capability = resolve_document_capability(
        profile.get("document_type"),
        profile.get("task_mode"),
        product_mode=product_mode,
    )
    if capability is None:
        raise ContractError(
            "launch_profile_capability_mismatch",
            "launch_profile_id",
            "专家团启动配置未绑定已放行的文档能力",
        )
    standalone_defaults = capability.get("standalone_defaults")
    constraints = (
        standalone_defaults.get("content_constraints")
        if isinstance(standalone_defaults, dict)
        else {}
    )
    return {
        "capability_id": capability["capability_id"],
        "document_type": capability["document_type"],
        "task_mode": capability["task_mode"],
        "render_template_id": capability["render_template_id"],
        "brief_schema": deepcopy(capability.get("brief_schema") or ()),
        "source_requirement": deepcopy(capability.get("source_requirement") or {}),
        "content_constraints": deepcopy(constraints or {}),
    }


def validate_launch_profiles(
    profiles: list[dict] | None = None,
    *,
    product_mode: str = "standalone",
) -> list[dict]:
    """Return only launch profiles exactly bound to one released capability."""
    candidates = (
        [deepcopy(item) for item in profiles]
        if profiles is not None
        else [
            deepcopy(_LAUNCH_PROFILES[profile_id])
            for profile_id in _LAUNCH_PROFILE_ORDER
            if profile_id in _LAUNCH_PROFILES
        ]
    )
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    validated = []
    for profile in candidates:
        if not isinstance(profile, dict):
            raise ContractError(
                "launch_profile_capability_mismatch",
                "launch_profile_id",
                "专家团启动配置无效",
            )
        profile_id = str(profile.get("id") or "").strip()
        team_id = str(profile.get("team_id") or "").strip()
        example_id = str(profile.get("intake_example_id") or "").strip()
        pair = (team_id, example_id)
        if (
            not profile_id
            or not team_id
            or not example_id
            or profile_id in seen_ids
            or pair in seen_pairs
        ):
            raise ContractError(
                "launch_profile_capability_mismatch",
                "launch_profile_id",
                "专家团启动配置存在空值或重复绑定",
            )
        expected = _capability_fields(profile, product_mode=product_mode)
        for field, value in expected.items():
            current = profile.get(field)
            if field in {
                "brief_schema",
                "source_requirement",
                "content_constraints",
            } and current is None:
                profile[field] = deepcopy(value)
            elif current != value:
                raise ContractError(
                    "launch_profile_capability_mismatch",
                    field,
                    "专家团启动配置与文档能力合同不一致",
                )
        seen_ids.add(profile_id)
        seen_pairs.add(pair)
        validated.append(profile)
    return validated

_LAUNCH_PROFILE_ORDER = (
    "content-work-report",
    "content-meeting-minutes",
    "content-notice",
    "content-plan",
    "content-summary-plan",
    "content-polish",
    "research-report",
)


def list_launch_profiles() -> list[dict]:
    """Return detached snapshots in stable catalog order."""
    return validate_launch_profiles(product_mode="standalone")


def get_launch_profile(profile_id: str | None) -> dict:
    """Resolve a launch profile without exposing the mutable source object."""
    normalized = str(profile_id or "").strip()
    profile = next(
        (item for item in list_launch_profiles() if item.get("id") == normalized),
        None,
    )
    if profile is None:
        raise ContractError(
            "unknown_launch_profile",
            "launch_profile_id",
            "当前任务类型不可启动",
        )
    return deepcopy(profile)
