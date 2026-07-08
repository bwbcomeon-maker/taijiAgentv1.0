import importlib.util
from pathlib import Path

from api.expert_teams.materials import validate_stage_output
from api.expert_teams.rich_draft import build_rich_draft_package


def _answer_all_required(expert_teams, tmp_path, run):
    return expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": run["run_id"],
            "answers": {
                "topic": "方案说明，主题是提升营业厅服务质效专项行动",
                "audience": "公司分管领导和营销服务部门，用于专项行动部署",
                "boundary": "正式方案口径，包含目标、任务、进度、责任分工和保障机制",
            },
        },
    )


def _complete_stage(expert_teams, tmp_path, run, content, delivery_id):
    generated = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        run["run_id"],
        delivery={"id": delivery_id, "kind": "chat", "content": content},
    )
    assert generated["workflow_state"] == "awaiting_review"
    return expert_teams.approve_expert_team_stage(tmp_path, {"run_id": generated["run_id"]})


def test_generation_contract_names_rich_draft_requirements():
    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "_expert_team_catalog_rich_draft_test",
        repo_root / "api" / "expert_teams" / "catalog.py",
    )
    assert spec and spec.loader
    catalog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(catalog)

    content_draft = next(task for task in catalog.CONTENT_PHASES if task["id"] == "draft")
    research_draft = next(task for task in catalog.DEEP_RESEARCH_PHASES if task["id"] == "draft")
    assert "富内容初稿" in content_draft["title"]
    assert "富内容初稿" in content_draft["phase"]
    assert "富内容初稿" in research_draft["title"]
    assert "富内容初稿" in research_draft["phase"]

    skill_path = (
        Path(__file__).resolve().parents[3]
        / "custom-skills"
        / "writing-agent"
        / "workflow-producer"
        / "SKILL.md"
    )
    skill_text = skill_path.read_text(encoding="utf-8")
    for phrase in (
        "富内容初稿",
        "至少 2 个 Markdown 表格",
        "至少 1 个架构图、流程图、用例图或图示引用",
        "初稿生成阶段",
        "模板套用阶段不得承担补图补表",
    ):
        assert phrase in skill_text


def test_plan_draft_rejects_plain_prose_without_tables_or_figures():
    text = "这是一个纯文字方案。目标是提升服务质效。措施包括优化流程和强化监督。"

    result = validate_stage_output(text, "plan", "draft", "content-creator-team")

    assert result["status"] == "rewrite_required"
    assert "表格" in result["message"]
    assert "图" in result["message"]


def test_plan_draft_rejects_one_table_even_with_figure_reference():
    text = """# 提升营业厅服务质效专项行动方案

| 序号 | 重点任务 | 责任单位 |
| --- | --- | --- |
| 1 | 梳理高频业务流程 | 营销部 |

## 图示设计
请生成总体架构图。
"""

    result = validate_stage_output(text, "plan", "draft", "content-creator-team")

    assert result["status"] == "rewrite_required"
    assert "至少 2 个 Markdown 表格" in result["message"]


def test_plan_draft_rejects_tables_without_figure_reference():
    text = """# 提升营业厅服务质效专项行动方案

| 序号 | 重点任务 | 责任单位 |
| --- | --- | --- |
| 1 | 梳理高频业务流程 | 营销部 |

| 风险 | 表现 | 应对措施 |
| --- | --- | --- |
| 数据口径不一致 | 报表无法比对 | 统一指标口径 |
"""

    result = validate_stage_output(text, "plan", "draft", "content-creator-team")

    assert result["status"] == "rewrite_required"
    assert "架构图" in result["message"] or "图示" in result["message"]


def test_plan_draft_accepts_markdown_tables_and_figure_brief(tmp_path):
    svg = tmp_path / "architecture.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
    text = f"""# 提升营业厅服务质效专项行动方案

## 一、总体目标
提升业务办理效率和客户服务体验。

| 序号 | 重点任务 | 责任单位 |
| --- | --- | --- |
| 1 | 梳理高频业务流程 | 营销部 |
| 2 | 建立问题闭环台账 | 服务中心 |

| 风险 | 表现 | 应对措施 |
| --- | --- | --- |
| 数据口径不一致 | 报表无法比对 | 统一指标口径 |
| 执行进度滞后 | 节点延期 | 周度督办 |

## 图示设计
![总体架构图]({svg})
"""

    result = validate_stage_output(text, "plan", "draft", "content-creator-team")

    assert result["status"] == "pass"


def test_build_rich_draft_package_writes_manifest_markdown_and_svg(tmp_path):
    run = {
        "run_id": "et-richdraft",
        "title": "提升营业厅服务质效专项行动方案",
        "team_id": "content-creator-team",
        "current_stage": {"task_id": "draft"},
    }
    output = {
        "id": "draft-output",
        "title": "提升营业厅服务质效专项行动方案",
        "content": """# 提升营业厅服务质效专项行动方案

| 序号 | 重点任务 | 责任单位 |
| --- | --- | --- |
| 1 | 梳理高频业务流程 | 营销部 |
| 2 | 建立问题闭环台账 | 服务中心 |

| 风险 | 表现 | 应对措施 |
| --- | --- | --- |
| 数据口径不一致 | 报表无法比对 | 统一指标口径 |
| 执行进度滞后 | 节点延期 | 周度督办 |

## 图示设计
请生成总体架构图，包含用户入口、智能编排、业务系统、数据台账和监督闭环。
""",
    }

    package = build_rich_draft_package(tmp_path, run, output)

    manifest = tmp_path / package["manifest_path"]
    draft = tmp_path / package["draft_path"]
    asset = tmp_path / package["assets"][0]["path"]
    assert manifest.exists()
    assert draft.exists()
    assert asset.exists()
    assert asset.read_text(encoding="utf-8").startswith("<svg")
    assert package["table_count"] >= 2
    assert package["figure_count"] >= 1


def test_expert_team_draft_completion_registers_rich_draft_artifact(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-rich-draft",
            "team_id": "content-creator-team",
            "prompt": "帮我起草一份方案说明，主题是提升营业厅服务质效专项行动。",
        },
    )
    ready = expert_teams.answer_expert_team(
        tmp_path,
        {
            "run_id": _answer_all_required(expert_teams, tmp_path, run)["run_id"],
            "answers": {"optional_context": ""},
            "skip_optional": True,
        },
    )
    materials_ready = _complete_stage(
        expert_teams,
        tmp_path,
        ready,
        (
            "阶段摘要：已形成专家团执行计划。\n"
            "正文草稿：本阶段确认方案说明的编制路径和素材缺口，后续进入素材整理和富内容初稿生成。\n"
            "待补充事项：请确认营业厅清单、办理量和投诉问题台账。\n"
            "建议下一步：进入素材整理。"
        ),
        "plan-stage",
    )
    draft_ready = _complete_stage(
        expert_teams,
        tmp_path,
        materials_ready,
        "阶段摘要：已整理行动目标、问题台账、责任单位和进度节点。\n待补充事项：数据口径待人工确认。",
        "materials-stage",
    )
    assert draft_ready["current_stage"]["task_id"] == "draft"

    completed = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        draft_ready["run_id"],
        delivery={
            "id": "draft-stage",
            "kind": "chat",
            "content": """# 提升营业厅服务质效专项行动方案

## 一、总体目标
围绕业务办理效率、客户诉求响应和服务闭环能力开展专项行动。

| 序号 | 重点任务 | 责任单位 |
| --- | --- | --- |
| 1 | 梳理高频业务流程 | 营销部 |
| 2 | 建立问题闭环台账 | 服务中心 |

| 风险 | 表现 | 应对措施 |
| --- | --- | --- |
| 数据口径不一致 | 报表无法比对 | 统一指标口径 |
| 执行进度滞后 | 节点延期 | 周度督办 |

## 图示设计
请生成总体架构图，包含用户入口、智能编排、业务系统、数据台账和监督闭环。
""",
        },
    )

    rich_artifact = next(item for item in completed["artifacts"] if item["kind"] == "rich_draft")
    assert completed["workflow_state"] == "awaiting_review"
    assert rich_artifact["manifest_path"].endswith("draft.manifest.json")
    assert rich_artifact["path"].endswith("draft.md")
    assert (tmp_path / rich_artifact["manifest_path"]).exists()
    assert (tmp_path / rich_artifact["path"]).exists()
    assert (tmp_path / rich_artifact["assets"][0]["path"]).exists()

    polish_ready = expert_teams.approve_expert_team_stage(tmp_path, {"run_id": completed["run_id"]})
    assert polish_ready["workflow_state"] == "ready_to_generate"
    assert polish_ready["current_stage"]["task_id"] == "polish"

    delivery_ready = _complete_stage(
        expert_teams,
        tmp_path,
        polish_ready,
        (
            "阶段摘要：已完成方案说明的表达打磨和流转检查。\n"
            "阶段产物：保留富内容初稿的两张表格和总体图示结构，补齐目标、措施、进度和责任闭环表述。\n"
            "待人工补充事项：请最终确认数据口径和责任单位。\n"
            "下一阶段建议：进入交付确认。"
        ),
        "polish-stage",
    )
    assert delivery_ready["current_stage"]["task_id"] == "delivery"

    delivery_review = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        delivery_ready["run_id"],
        delivery={
            "id": "delivery-stage",
            "kind": "chat",
            "content": (
                "阶段摘要：已完成交付确认。\n"
                "阶段产物：形成可供流转的提升营业厅服务质效专项行动方案，包含总体目标、任务表、风险应对表和总体图示设计。\n"
                "待人工补充事项：正式流转前确认责任单位名称和数据口径。\n"
                "交付后核对事项：核对附件编号、会议口径和责任分工。"
            ),
        },
    )
    assert delivery_review["workflow_state"] == "awaiting_review"

    final = expert_teams.approve_expert_team_stage(tmp_path, {"run_id": delivery_review["run_id"]})
    assert final["workflow_state"] == "completed"
    assert final["view"]["presentation"]["state"] == "completed"
    assert final["view"]["presentation"]["primary_action"]["id"] == "view_result"
