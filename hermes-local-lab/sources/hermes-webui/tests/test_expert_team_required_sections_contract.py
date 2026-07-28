import json

import pytest


META_START = "<<<TAIJI_META_V1>>>"
META_END = "<<<TAIJI_META_END>>>"
DOC_START = "<<<TAIJI_DOCUMENT_V1>>>"
DOC_END = "<<<TAIJI_DOCUMENT_END>>>"


def _brief(required_sections):
    return {
        "schema_version": "document-brief/v1",
        "revision": 1,
        "status": "confirmed",
        "confirmed_revision": 1,
        "confirmed_sha256": "b" * 64,
        "exact_title": "专家团章节合同测试",
        "document_type": "work_report",
        "content_constraints": {
            "required_sections": list(required_sections),
            "must_include": [],
            "must_avoid": [],
        },
    }


def _raw(artifact_type, payload, markdown=None):
    meta = {
        "artifact_type": artifact_type,
        "summary": "章节合同测试",
        "payload": payload,
        "blocking_issues": [],
    }
    raw = f"{META_START}\n{json.dumps(meta, ensure_ascii=False)}\n{META_END}"
    if markdown is not None:
        raw += f"\n{DOC_START}\n{markdown}\n{DOC_END}"
    return raw


def _draft_payload(headings):
    return {
        "title": "专家团章节合同测试",
        "document_type": "work_report",
        "section_map": [
            {"section_id": f"SEC-{index}", "heading": heading}
            for index, heading in enumerate(headings, start=1)
        ],
        "fact_usage": [],
        "asset_requests": [],
        "open_issues": [],
    }


@pytest.mark.parametrize(
    ("profile_id", "expected_sections"),
    [
        (
            "content-meeting-minutes",
            ["会议基本情况", "议定事项", "责任分工", "后续跟踪"],
        ),
        (
            "content-notice",
            ["背景与总体要求", "通知事项", "时间安排", "责任分工", "报送要求"],
        ),
        (
            "content-plan",
            ["目标", "现状与问题", "主要措施", "进度安排", "保障机制"],
        ),
        (
            "content-summary-plan",
            ["阶段性工作总结", "成效与亮点", "问题与不足", "下一步工作计划"],
        ),
        ("content-polish", ["润色后正文", "修改说明"]),
    ],
)
def test_new_content_required_sections_flow_from_profile_to_run_and_prompt(
    profile_id,
    expected_sections,
):
    from api import expert_teams
    from api.expert_teams.prompts import _system_message

    run = expert_teams.build_standalone_expert_team_run(
        {
            "session_id": f"required-sections-{profile_id}",
            "launch_profile_id": profile_id,
            "prompt": "生成章节合同测试文档",
            "idempotency_key": f"required-sections-{profile_id}-1",
        },
        run_id=f"et-required-sections-{profile_id}",
    )
    brief = run["document_brief"]

    assert run["launch_profile_snapshot"]["content_constraints"][
        "required_sections"
    ] == expected_sections
    assert brief["content_constraints"]["required_sections"] == expected_sections
    assert json.dumps(expected_sections, ensure_ascii=False, separators=(",", ":")) in _system_message(
        "writing_plan",
        brief,
    )


def test_document_artifact_requires_every_brief_section_in_map_and_markdown():
    from api.expert_teams.stage_artifacts import (
        StageArtifactError,
        build_stage_artifact,
        parse_stage_response,
    )

    brief = _brief(["工作开展情况", "存在问题"])
    missing_map = parse_stage_response(
        _raw(
            "document_draft",
            _draft_payload(["工作开展情况"]),
            "# 专家团章节合同测试\n\n## 工作开展情况\n\n正文\n\n## 存在问题\n\n无。",
        ),
        artifact_type="document_draft",
        requires_document=True,
    )
    with pytest.raises(StageArtifactError) as map_error:
        build_stage_artifact(
            missing_map,
            stage_id="draft",
            stage_attempt=1,
            brief=brief,
            input_refs=[],
            now="2026-07-26T10:00:00+08:00",
        )
    assert map_error.value.code == "required_section_missing"
    assert map_error.value.field == "payload.section_map"

    missing_markdown = parse_stage_response(
        _raw(
            "document_draft",
            _draft_payload(["工作开展情况", "存在问题"]),
            "# 专家团章节合同测试\n\n## 工作开展情况\n\n正文。",
        ),
        artifact_type="document_draft",
        requires_document=True,
    )
    with pytest.raises(StageArtifactError) as markdown_error:
        build_stage_artifact(
            missing_markdown,
            stage_id="draft",
            stage_attempt=1,
            brief=brief,
            input_refs=[],
            now="2026-07-26T10:00:00+08:00",
        )
    assert markdown_error.value.code == "required_section_missing"
    assert markdown_error.value.field == "deliverable_markdown"


def test_research_outline_requires_every_brief_section():
    from api.expert_teams.stage_artifacts import (
        StageArtifactError,
        build_stage_artifact,
        parse_stage_response,
    )

    brief = _brief(["研究问题", "证据", "分析"])
    brief["document_type"] = "research_report"
    payload = {
        "sections": [
            {
                "section_id": "SEC-1",
                "heading": "研究问题",
                "thesis": "明确研究问题",
                "claim_ids": [],
                "source_ids": [],
                "open_questions": [],
            }
        ],
        "conclusion_boundaries": [],
    }
    parsed = parse_stage_response(
        _raw("research_outline", payload),
        artifact_type="research_outline",
        requires_document=False,
    )

    with pytest.raises(StageArtifactError) as error:
        build_stage_artifact(
            parsed,
            stage_id="outline",
            stage_attempt=1,
            brief=brief,
            input_refs=[],
            now="2026-07-26T10:00:00+08:00",
        )

    assert error.value.code == "required_section_missing"
    assert error.value.field == "payload.sections"


@pytest.mark.parametrize(
    ("artifact_type", "structure_field"),
    [
        ("writing_plan", "section_plan[].heading"),
        ("research_outline", "sections[].heading"),
        ("reviewed_document", "section_map[].heading"),
        ("reviewed_research_document", "section_map[].heading"),
    ],
)
def test_stage_prompt_explicitly_names_required_sections_and_structure_field(
    artifact_type,
    structure_field,
):
    from api.expert_teams.prompts import _system_message

    message = _system_message(
        artifact_type,
        _brief(["工作开展情况", "存在问题"]),
    )

    assert structure_field in message
    assert 'Brief required_sections：["工作开展情况","存在问题"]' in message


def test_markdown_headings_inside_code_fences_cannot_satisfy_required_sections():
    from api.expert_teams.stage_artifacts import document_section_headings

    markdown = """# 标题

## 工作开展情况

正文。

```markdown
## 存在问题
```
"""

    assert document_section_headings(markdown) == ["工作开展情况"]


def test_missing_required_section_blocks_semantic_and_standalone_docx_quality(tmp_path):
    from api.expert_teams.documents import (
        write_semantic_gates_snapshot,
        write_standalone_quality_report,
    )

    brief = _brief(["工作开展情况", "存在问题"])
    artifact = {
        "artifact_id": "polish:1",
        "artifact_type": "reviewed_document",
        "sha256": "a" * 64,
        "payload": {"document_type": "work_report"},
        "deliverable_markdown": "# 专家团章节合同测试\n\n## 工作开展情况\n\n正文。",
        "blocking_issues": [],
    }
    semantic = write_semantic_gates_snapshot(
        tmp_path,
        brief=brief,
        artifact=artifact,
        approved_inputs=[],
        product_mode="standalone",
    )

    assert semantic["status"] == "failed"
    assert semantic["required_sections"] == ["工作开展情况", "存在问题"]
    assert semantic["document_sections"] == ["工作开展情况"]
    missing = [item for item in semantic["issues"] if item["code"] == "required_section_missing"]
    assert [item["target_id"] for item in missing] == ["section:存在问题"]

    quality, _path = write_standalone_quality_report(
        tmp_path,
        semantic_gates=semantic,
        automatic_quality={
            "checks": [{"id": "docx_zip", "status": "passed"}],
            "automaticQuality": {
                "assetStatus": "passed",
                "renderStatus": "passed",
                "issues": [],
            },
        },
        document_sha256="d" * 64,
    )
    assert quality["status"] == "blocked"
    assert quality["statuses"]["semantic"] == "failed"
    assert any(item["code"] == "required_section_missing" for item in quality["issues"])
