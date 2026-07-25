"""Server-owned launch profiles for standalone expert-team runs."""

from __future__ import annotations

from copy import deepcopy

from .contracts import ContractError


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
        "team_id": CONTENT_CREATOR_TEAM_ID,
        "document_type": "work_report",
        "intake_example_id": "work_report",
        "task_mode": "create",
        "render_template_id": "enterprise-work-report",
        "stages": CONTENT_PHASES,
        "review_policy": {"kind": "local_confirmation"},
    },
    "research-report": {
        "id": "research-report",
        "team_id": DEEP_RESEARCH_TEAM_ID,
        "document_type": "research_report",
        "intake_example_id": "research_report",
        "task_mode": "create",
        "render_template_id": "enterprise-research-report",
        "stages": DEEP_RESEARCH_PHASES,
        "review_policy": {"kind": "local_confirmation"},
    },
}

_LAUNCH_PROFILE_ORDER = (
    "content-work-report",
    "research-report",
)


def list_launch_profiles() -> list[dict]:
    """Return detached snapshots in stable catalog order."""
    return [
        deepcopy(_LAUNCH_PROFILES[profile_id])
        for profile_id in _LAUNCH_PROFILE_ORDER
        if profile_id in _LAUNCH_PROFILES
    ]


def get_launch_profile(profile_id: str | None) -> dict:
    """Resolve a launch profile without exposing the mutable source object."""
    normalized = str(profile_id or "").strip()
    profile = _LAUNCH_PROFILES.get(normalized)
    if profile is None:
        raise ContractError(
            "unknown_launch_profile",
            "launch_profile_id",
            "当前任务类型不可启动",
        )
    return deepcopy(profile)
