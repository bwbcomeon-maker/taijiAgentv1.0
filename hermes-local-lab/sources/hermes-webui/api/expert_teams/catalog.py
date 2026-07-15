"""Expert team catalog for the rebuilt office-material workflow."""

from __future__ import annotations

from copy import deepcopy


CONTENT_CREATOR_TEAM_ID = "content-creator-team"
DEEP_RESEARCH_TEAM_ID = "deep-research-team"
PUBLIC_EXPERT_TEAM_IDS = (CONTENT_CREATOR_TEAM_ID, DEEP_RESEARCH_TEAM_ID)

CONTENT_MATERIAL_TEMPLATES = [
    {
        "id": "work_report",
        "intake_example_id": "work_report",
        "document_type": "work_report",
        "task_mode": "create",
        "document_brief_seed": {"document_control": {"render_template_id": "enterprise-work-report"}},
        "label": "工作汇报",
        "summary": "围绕完成情况、存在问题和下一步安排起草正式汇报。",
        "prompt": "帮我起草一份部门月度工作汇报，主题是迎峰度夏保供电重点工作推进情况。",
    },
    {
        "id": "meeting_minutes",
        "intake_example_id": "meeting_minutes",
        "document_type": "meeting_minutes",
        "task_mode": "create",
        "label": "会议纪要",
        "summary": "整理议题、形成意见、责任分工和后续跟踪事项。",
        "prompt": "帮我整理一份会议纪要，主题是优化供电服务质效提升措施专题会。",
    },
    {
        "id": "notice",
        "intake_example_id": "notice",
        "document_type": "notice",
        "task_mode": "create",
        "label": "通知通报",
        "summary": "起草背景、事项安排、时间节点、责任分工和报送要求。",
        "prompt": "帮我起草一份内部通知，主题是近期安全生产专项检查安排。",
    },
    {
        "id": "plan",
        "intake_example_id": "plan",
        "document_type": "plan",
        "task_mode": "create",
        "label": "方案说明",
        "summary": "说明目标、现状问题、主要措施、进度安排和保障机制。",
        "prompt": "帮我起草一份方案说明，主题是提升营业厅服务质效专项行动。",
    },
    {
        "id": "summary_plan",
        "intake_example_id": "summary_plan",
        "document_type": "summary_plan",
        "task_mode": "create",
        "label": "总结计划",
        "summary": "形成阶段性总结、成效亮点、问题不足和下一步计划。",
        "prompt": "帮我起草一份阶段性工作总结和下一步计划，主题是数字化办公推广应用。",
    },
    {
        "id": "polish",
        "intake_example_id": "polish",
        "document_type": "other_office_material",
        "task_mode": "polish",
        "label": "材料润色",
        "summary": "在保持原意基础上提升逻辑层次、正式表达和可读性。",
        "prompt": "帮我润色一份办公材料，要求保持原意，提升逻辑层次、正式表达和可读性。",
    },
]

CONTENT_PHASES = [
    {"id": "plan", "title": "专家团计划", "phase": "流程安排", "worker_id": "director", "worker_name": "写作总导演"},
    {"id": "materials", "title": "素材整理", "phase": "素材整理", "worker_id": "material", "worker_name": "资料整理专家"},
    {"id": "draft", "title": "起草富内容初稿", "phase": "富内容初稿", "worker_id": "writer", "worker_name": "文案创作专家"},
    {"id": "polish", "title": "审稿打磨", "phase": "审稿打磨", "worker_id": "reviewer", "worker_name": "审稿专家"},
    {"id": "delivery", "title": "交付确认", "phase": "交付确认", "worker_id": "delivery", "worker_name": "交付复核专家"},
]

DEEP_RESEARCH_PHASES = [
    {"id": "direction", "title": "确定研究方向", "phase": "研究方向", "worker_id": "director", "worker_name": "研究总导演"},
    {"id": "research", "title": "补充案例素材", "phase": "资料调研", "worker_id": "researcher", "worker_name": "资料研究员"},
    {"id": "evidence", "title": "事实核验", "phase": "事实核验", "worker_id": "evidence", "worker_name": "事实核验专家"},
    {"id": "outline", "title": "结构提纲", "phase": "结构提纲", "worker_id": "architect", "worker_name": "结构架构师"},
    {"id": "draft", "title": "研究富内容初稿", "phase": "富内容初稿", "worker_id": "writer", "worker_name": "材料起草专家"},
    {"id": "review", "title": "复核交付", "phase": "复核交付", "worker_id": "reviewer", "worker_name": "复核专家"},
]

_CATALOG = {
    CONTENT_CREATOR_TEAM_ID: {
        "id": CONTENT_CREATOR_TEAM_ID,
        "title": "内容创作专家团",
        "description": (
            "面向国网业务部门日常办公材料编制，支持通知通报、工作汇报、会议纪要、"
            "方案说明、总结计划、材料润色等内容，从需求确认、初稿起草、材料打磨到交付确认分阶段协作。"
        ),
        "category": "内容创作",
        "image": "static/assets/writeflow/team-content-creator.png",
        "tags": ["工作汇报", "通知通报", "会议纪要", "方案说明", "总结计划", "材料润色"],
        "examples": CONTENT_MATERIAL_TEMPLATES,
        "questions": [
            {
                "id": "topic",
                "title": "这次要编制哪类办公材料，主题是什么？",
                "placeholder": "例如：部门月度工作汇报，主题是迎峰度夏保供电重点工作推进情况",
                "required": True,
            },
            {
                "id": "audience",
                "title": "材料面向哪些对象，使用场景是什么？",
                "placeholder": "例如：面向公司分管领导，用于月度例会汇报",
                "required": True,
            },
            {
                "id": "boundary",
                "title": "有哪些已知素材、口径要求、篇幅或表述边界？",
                "placeholder": "例如：包含已完成工作、存在问题、下一步安排，语气正式",
                "required": True,
            },
            {
                "id": "optional_context",
                "title": "还有没有可选补充材料或特别强调的点？",
                "placeholder": "可以补充数据、领导要求、禁用表述；没有可直接跳过",
                "required": False,
            },
        ],
        "members": [
            {
                "id": "director",
                "name": "写作总导演",
                "role": "流程编排",
                "image": "static/assets/writeflow/member-workflow-producer.png",
            },
            {
                "id": "material",
                "name": "资料整理专家",
                "role": "素材整理",
                "image": "static/assets/writeflow/member-research-expert.png",
            },
            {
                "id": "writer",
                "name": "文案创作专家",
                "role": "正文写作",
                "image": "static/assets/writeflow/member-writing-executor.png",
            },
            {
                "id": "reviewer",
                "name": "审稿专家",
                "role": "审稿润色",
                "image": "static/assets/writeflow/member-editor-review.png",
            },
            {
                "id": "delivery",
                "name": "交付复核专家",
                "role": "交付确认",
                "image": "static/assets/writeflow/member-outline-architect.png",
            },
        ],
        "tasks": CONTENT_PHASES,
    },
    DEEP_RESEARCH_TEAM_ID: {
        "id": DEEP_RESEARCH_TEAM_ID,
        "title": "深度材料研究团",
        "description": "面向调研材料、专题报告、案例素材和结构提纲，帮助用户完成资料边界、研究主线和材料初稿。",
        "category": "材料研究",
        "image": "static/assets/writeflow/team-research.png",
        "tags": ["调研材料", "专题报告", "案例素材", "结构提纲"],
        "examples": [
            {
                "id": "research_report",
                "intake_example_id": "research_report",
                "document_type": "research_report",
                "task_mode": "create",
                "document_brief_seed": {"document_control": {"render_template_id": "enterprise-research-report"}},
                "label": "专题报告",
                "summary": "围绕主题做材料研究、结构提纲和初稿建议。",
                "prompt": "帮我研究本地优先 AI 助理在企业内部办公场景的落地趋势。",
            }
        ],
        "questions": [
            {"id": "research_topic", "title": "本次要研究的主题或核心问题是什么？", "required": True},
            {"id": "audience_goal", "title": "材料面向谁，最终要支撑什么决策或汇报？", "required": True},
            {"id": "source_boundary", "title": "资料范围、案例偏好或需要避开的边界是什么？", "required": True},
            {"id": "optional_context", "title": "还有没有可选补充资料？", "required": False},
        ],
        "members": [
            {
                "id": "director",
                "name": "研究总导演",
                "role": "流程编排",
                "image": "static/assets/writeflow/member-workflow-producer.png",
            },
            {
                "id": "researcher",
                "name": "资料研究员",
                "role": "资料整理",
                "image": "static/assets/writeflow/member-research-expert.png",
            },
            {
                "id": "evidence",
                "name": "事实核验专家",
                "role": "事实核验",
                "image": "static/assets/writeflow/member-editor-review.png",
            },
            {
                "id": "architect",
                "name": "结构架构师",
                "role": "结构提纲",
                "image": "static/assets/writeflow/member-outline-architect.png",
            },
            {
                "id": "writer",
                "name": "材料起草专家",
                "role": "材料初稿",
                "image": "static/assets/writeflow/member-writing-executor.png",
            },
            {
                "id": "reviewer",
                "name": "复核专家",
                "role": "复核交付",
                "image": "static/assets/writeflow/member-editor-review.png",
            },
        ],
        "tasks": DEEP_RESEARCH_PHASES,
    },
}


def get_template(team_id: str | None) -> dict:
    tid = (team_id or CONTENT_CREATOR_TEAM_ID).strip() or CONTENT_CREATOR_TEAM_ID
    if tid not in _CATALOG:
        raise ValueError(f"Unknown expert team: {tid}")
    return deepcopy(_CATALOG[tid])


def expert_team_catalog() -> dict:
    return {
        "teams": [get_template(team_id) for team_id in PUBLIC_EXPERT_TEAM_IDS],
        "contract_rollout": {
            "mode": "off",
            "contract_version": "expert-team-contract/v1",
            "document_types": [],
        },
    }
