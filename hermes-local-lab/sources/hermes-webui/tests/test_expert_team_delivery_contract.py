import json
from pathlib import Path

import pytest


RICH_MARKDOWN = """# 营业厅服务质效专项行动方案

## 一、工作目标
围绕业务办理效率和服务闭环能力开展专项行动。

| 序号 | 重点任务 | 责任单位 |
| --- | --- | --- |
| 1 | 梳理高频业务流程 | 营销部 |
| 2 | 建立问题闭环台账 | 服务中心 |

| 风险 | 表现 | 应对措施 |
| --- | --- | --- |
| 数据口径不一致 | 报表无法比对 | 统一指标口径 |
| 执行进度滞后 | 节点延期 | 周度督办 |

```mermaid
flowchart LR
  A[用户入口] --> B[智能编排]
  B --> C[业务系统]
  B --> D[监督闭环]
```

待人工补充事项：正式流转前确认责任单位和数据口径。
"""


FINAL_MARKDOWN = """# 营业厅服务质效专项行动方案

## 一、工作目标
围绕业务办理效率、客户诉求响应和服务闭环能力开展专项行动。

## 二、主要任务

| 序号 | 重点任务 | 责任单位 |
| --- | --- | --- |
| 1 | 梳理高频业务流程 | 营销部 |
| 2 | 建立问题闭环台账 | 服务中心 |

## 三、风险和保障

| 风险 | 应对措施 |
| --- | --- |
| 数据口径不一致 | 统一指标口径 |

```mermaid
flowchart LR
  A[需求确认] --> B[专家协作]
  B --> C[正式交付]
```

待人工补充事项：正式流转前确认责任单位和数据口径。
"""


MEETING_MINUTES_MARKDOWN = """# 供电服务质效提升专题会议纪要

## 一、会议基本信息
会议围绕营业厅业务办理效率、客户诉求响应和问题闭环进行专题研讨，并对后续责任分工、完成时限和复盘机制作出明确安排。

## 二、议定事项
1. 由营销部在本周内梳理高频业务办理流程，形成节点清单。
2. 由服务中心建立客户诉求台账，每日跟踪、每周复盘。
3. 各责任单位于下周三前反馈完成情况，逾期事项单独说明原因和整改计划。

## 三、跟踪安排
办公室统一汇总任务进展，下次会议逐项核验办理结果；需要跨部门协调的事项由分管负责人牵头处理。
"""


def _ready_at_stage(expert_teams, tmp_path: Path, *, team_id: str, stage_index: int, session_id: str):
    from api.expert_teams.storage import write_run

    prompt = (
        "帮我起草一份方案说明，主题是提升营业厅服务质效专项行动。"
        if team_id == "content-creator-team"
        else "帮我研究本地优先 AI 助理在企业办公场景的落地趋势。"
    )
    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": session_id, "team_id": team_id, "prompt": prompt},
    )
    stored = expert_teams.read_expert_team_run(tmp_path, run["run_id"])
    stored["questions"] = [
        {**question, "status": "answered", "answer": "已确认"}
        for question in stored.get("questions") or []
    ]
    stored["answers"] = [
        {"id": question["id"], "answer": "已确认"}
        for question in stored["questions"]
    ]
    stored["workflow_state"] = "ready_to_generate"
    stored["current_stage_index"] = stage_index
    write_run(tmp_path, stored)
    return expert_teams.read_expert_team_run(tmp_path, run["run_id"])


def _generate(expert_teams, tmp_path: Path, run: dict, content: str, delivery_id: str):
    reserved = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        run["run_id"],
        expected_version=run["version"],
    )
    stream_id = f"stream-{delivery_id}"
    generating = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        run["run_id"],
        {
            "stream_id": stream_id,
            "turn_id": f"turn-{delivery_id}",
            "execution_start_id": reserved["execution_start_id"],
        },
    )
    return expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        run["run_id"],
        delivery={
            "stream_id": stream_id,
            "stage_id": generating["execution_stage_id"],
            "attempt": generating["execution_attempt"],
            "id": delivery_id,
            "kind": "chat",
            "content": content,
        },
    )


@pytest.mark.parametrize(
    ("team_id", "stage_index"),
    [("content-creator-team", 2), ("deep-research-team", 4)],
)
def test_rich_draft_stage_uses_canonical_v2_package(team_id, stage_index, tmp_path):
    from api import expert_teams

    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id=team_id,
        stage_index=stage_index,
        session_id=f"sid-rich-{team_id}",
    )
    completed = _generate(expert_teams, tmp_path, ready, RICH_MARKDOWN, f"draft-{team_id}")

    artifact = next(item for item in completed["artifacts"] if item["kind"] == "rich_draft")
    expected_prefix = f".taiji/rich-drafts/{ready['run_id']}/draft/attempt-1/package/"
    assert artifact["id"] == "draft:1:rich_draft"
    assert artifact["stage"] == "draft"
    assert artifact["attempt"] == 1
    assert artifact["status"] == "ready"
    assert artifact["path"].startswith(expected_prefix)
    assert artifact["manifest_path"].startswith(expected_prefix)
    assert artifact["exists"] is True
    manifest = json.loads((tmp_path / artifact["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == "rich-draft-package/v2"
    assert manifest["figures"]
    assert all((tmp_path / ".taiji" / "rich-drafts" / ready["run_id"] / "draft" / "attempt-1" / "package" / row["displayPath"]).exists() for row in manifest["figures"])


def test_rich_draft_revision_preserves_previous_attempt_and_artifact_history(tmp_path):
    from api import expert_teams

    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id="content-creator-team",
        stage_index=2,
        session_id="sid-rich-history",
    )
    first = _generate(expert_teams, tmp_path, ready, RICH_MARKDOWN, "draft-attempt-1")
    revised = expert_teams.request_expert_team_stage_revision(
        tmp_path,
        {
            "run_id": first["run_id"],
            "session_id": first["session_id"],
            "expected_version": first["version"],
            "stage_id": "draft",
            "idempotency_key": "revise-rich-history",
            "feedback": "补充风险台账。",
        },
    )
    second = _generate(
        expert_teams,
        tmp_path,
        revised,
        RICH_MARKDOWN.replace("风险和保障", "风险台账和保障"),
        "draft-attempt-2",
    )

    rich = [item for item in second["artifacts"] if item["kind"] == "rich_draft"]
    assert [item["id"] for item in rich] == ["draft:1:rich_draft", "draft:2:rich_draft"]
    assert rich[0]["path"] != rich[1]["path"]
    assert (tmp_path / rich[0]["path"]).exists()
    assert (tmp_path / rich[1]["path"]).exists()
    for item in second["artifacts"]:
        assert {"id", "kind", "path", "exists", "attempt", "stage", "status", "created_at"} <= set(item)


def _fake_successful_create_job(calls: list[dict]):
    def fake_create_job(payload, workspace, **_identity):
        calls.append(dict(payload))
        source_path = workspace / payload["source_path"]
        assert source_path.read_text(encoding="utf-8").lstrip().startswith("# ")
        delivery_dir = workspace / payload["out_dir"]
        delivery_dir.mkdir(parents=True, exist_ok=False)
        document = delivery_dir / "document.docx"
        document.write_bytes(b"PK\x03\x04test-docx")
        quality = delivery_dir / "quality-report.json"
        quality.write_text(json.dumps({"schemaVersion": "docx-engine-v2/quality-report", "status": "passed_with_warnings"}), encoding="utf-8")
        return {
            "ok": True,
            "job_id": "job-final",
            "delivery_dir": str(delivery_dir),
            "document_path": str(document),
            "quality_status": "passed_with_warnings",
            "quality_report_path": str(quality),
            "quality_report": {"schemaVersion": "docx-engine-v2/quality-report", "status": "passed_with_warnings"},
        }, 200

    return fake_create_job


def _fake_successful_validate_delivery(payload, workspace):
    from api.expert_teams.delivery_integrity import read_binding_manifest
    from api.expert_teams.storage import read_run
    from tests.test_expert_team_delivery_validation_gate import _write_bound_wps_sidecar

    delivery_dir = workspace / payload["delivery_dir"]
    binding = read_binding_manifest(delivery_dir.parent / "expert-team-delivery.json")
    wps_check = _write_bound_wps_sidecar(workspace, read_run(workspace, binding["run_id"]))
    report = {
        "schemaVersion": "docx-engine-v2/validation-report",
        "status": "passed",
        "checks": [
            wps_check
        ],
        "warnings": [],
        "failures": [],
    }
    return {
        "ok": True,
        "delivery_dir": str(delivery_dir),
        "quality_report_path": str(delivery_dir / "quality-report.json"),
        "quality_report": report,
        "failures": [],
    }, 200


@pytest.mark.parametrize(
    ("team_id", "stage_index", "stage_id", "template_id"),
    [
        ("content-creator-team", 4, "delivery", "general-proposal"),
        ("deep-research-team", 5, "review", "general-proposal"),
    ],
)
def test_final_stage_creates_versioned_docx_delivery_before_completion(
    monkeypatch,
    tmp_path,
    team_id,
    stage_index,
    stage_id,
    template_id,
):
    from api import docx_engine_v2, expert_teams

    calls = []
    monkeypatch.setattr(docx_engine_v2, "_create_expert_delivery_job", _fake_successful_create_job(calls))
    monkeypatch.setattr(docx_engine_v2, "validate_delivery", _fake_successful_validate_delivery)
    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id=team_id,
        stage_index=stage_index,
        session_id=f"sid-final-{team_id}",
    )
    reviewed = _generate(expert_teams, tmp_path, ready, FINAL_MARKDOWN, f"final-{team_id}")

    assert reviewed["workflow_state"] == "awaiting_review"
    assert calls and calls[0]["template_id"] == template_id
    assert calls[0]["source_path"].startswith(
        f".taiji/rich-drafts/{ready['run_id']}/{stage_id}/attempt-1/package/"
    )
    assert calls[0]["asset_dir"].endswith(f"/{stage_id}/attempt-1/package")
    assert calls[0]["out_dir"].endswith(f"/{stage_id}/attempt-1/delivery")
    artifacts = {item["kind"]: item for item in reviewed["artifacts"]}
    assert {"final_document", "delivery_package", "quality_report"} <= set(artifacts)
    assert artifacts["final_document"]["id"] == f"{stage_id}:1:final_document"
    assert artifacts["final_document"]["path"].endswith("document.docx")
    assert artifacts["final_document"]["exists"] is True
    assert all({"id", "kind", "path", "exists", "attempt", "stage", "status", "created_at"} <= set(item) for item in artifacts.values())

    approved = expert_teams.approve_expert_team_stage(
        tmp_path,
        {
            "run_id": reviewed["run_id"],
            "session_id": reviewed["session_id"],
            "expected_version": reviewed["version"],
            "stage_id": stage_id,
            "idempotency_key": f"approve-{team_id}",
        },
    )
    assert approved["workflow_state"] == "completed"


def test_final_docx_failure_is_recoverable_and_never_completes(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    monkeypatch.setattr(
        docx_engine_v2,
        "_create_expert_delivery_job",
        lambda _payload, _workspace, **_identity: (
            {"ok": False, "code": "render_failed", "message": "DOCX 渲染失败"},
            500,
        ),
    )
    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id="content-creator-team",
        stage_index=4,
        session_id="sid-final-failure",
    )
    failed = _generate(expert_teams, tmp_path, ready, FINAL_MARKDOWN, "final-failure")

    assert failed["workflow_state"] == "generated_invalid"
    assert failed["workflow_state"] != "completed"
    assert "DOCX" in failed["last_validation_error"]
    assert not any(item.get("kind") == "final_document" and item.get("exists") for item in failed["artifacts"])


def test_meeting_minutes_final_delivery_selects_meeting_minutes_template(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    calls = []
    monkeypatch.setattr(docx_engine_v2, "_create_expert_delivery_job", _fake_successful_create_job(calls))
    monkeypatch.setattr(docx_engine_v2, "validate_delivery", _fake_successful_validate_delivery)
    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id="content-creator-team",
        stage_index=4,
        session_id="sid-final-minutes",
    )
    ready["prompt"] = "帮我整理一份会议纪要，主题是优化供电服务质效提升措施专题会。"
    from api.expert_teams.storage import write_run

    write_run(tmp_path, ready)
    ready = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    reviewed = _generate(expert_teams, tmp_path, ready, MEETING_MINUTES_MARKDOWN, "final-minutes")

    assert calls[0]["template_id"] == "meeting-minutes"
    assert calls[0]["source_path"].endswith("/delivery/attempt-1/final.md")
    assert calls[0]["asset_dir"].endswith("/delivery/attempt-1")
    assert not any(item["kind"] == "final_rich_draft" for item in reviewed["artifacts"])
    assert reviewed["workflow_state"] == "awaiting_review"

    approved = expert_teams.approve_expert_team_stage(
        tmp_path,
        {
            "run_id": reviewed["run_id"],
            "session_id": reviewed["session_id"],
            "expected_version": reviewed["version"],
            "stage_id": "delivery",
            "idempotency_key": "approve-final-minutes",
        },
    )
    assert approved["workflow_state"] == "completed"


def test_final_stage_prompts_require_complete_markdown_and_real_figure_sources(tmp_path):
    from api import expert_teams, routes

    content = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id="content-creator-team",
        stage_index=4,
        session_id="sid-final-prompt-content",
    )
    research = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id="deep-research-team",
        stage_index=5,
        session_id="sid-final-prompt-research",
    )

    for prompt in (
        routes._content_expert_team_execution_prompt(content),
        routes._deep_research_expert_team_execution_prompt(research),
    ):
        assert "完整最终 Markdown" in prompt
        assert "Mermaid" in prompt
        assert "不得只输出阶段摘要" in prompt


def test_wps_acceptance_forwards_visual_checks_and_evidence_files(monkeypatch, tmp_path):
    import subprocess

    from api import docx_engine_v2

    delivery = tmp_path / "delivery"
    delivery.mkdir()
    evidence = tmp_path / "wps-evidence.png"
    evidence.write_bytes(b"png")
    quality = delivery / "quality-report.json"
    quality.write_text(json.dumps({"status": "passed_with_warnings", "checks": []}), encoding="utf-8")
    captured = {}

    def fake_run_engine(args):
        captured["args"] = [str(item) for item in args]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"qualityReportPath": str(quality)}) + "\n",
            stderr="",
        )

    monkeypatch.setattr(docx_engine_v2, "run_engine", fake_run_engine)
    payload, status = docx_engine_v2.record_wps_visual_acceptance(
        {
            "delivery_dir": "delivery",
            "status": "passed_with_warnings",
            "visual_checks": ["document_opened", "layout_reviewed"],
            "evidence_files": [str(evidence)],
        },
        tmp_path,
    )

    assert status == 200 and payload["ok"] is True
    assert captured["args"].count("--visual-check") == 2
    assert "document_opened" in captured["args"]
    assert "layout_reviewed" in captured["args"]
    assert captured["args"].count("--evidence-file") == 1
    assert str(evidence) in captured["args"]


def test_final_stage_registers_canonical_final_rich_package(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    calls = []
    monkeypatch.setattr(docx_engine_v2, "_create_expert_delivery_job", _fake_successful_create_job(calls))
    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id="content-creator-team",
        stage_index=4,
        session_id="sid-final-rich-package",
    )
    reviewed = _generate(expert_teams, tmp_path, ready, FINAL_MARKDOWN, "final-rich-package")

    artifact = next(item for item in reviewed["artifacts"] if item["kind"] == "final_rich_draft")
    assert artifact["id"] == "delivery:1:final_rich_draft"
    assert artifact["path"].startswith(f".taiji/rich-drafts/{ready['run_id']}/delivery/attempt-1/package/")
    manifest = json.loads((tmp_path / artifact["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == "rich-draft-package/v2"


def test_final_approval_rechecks_filesystem_and_refuses_missing_docx(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    calls = []
    monkeypatch.setattr(docx_engine_v2, "_create_expert_delivery_job", _fake_successful_create_job(calls))
    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id="content-creator-team",
        stage_index=4,
        session_id="sid-final-missing-docx",
    )
    reviewed = _generate(expert_teams, tmp_path, ready, FINAL_MARKDOWN, "final-missing-docx")
    document = next(item for item in reviewed["artifacts"] if item["kind"] == "final_document")
    (tmp_path / document["path"]).unlink()

    after_approve = expert_teams.approve_expert_team_stage(
        tmp_path,
        {
            "run_id": reviewed["run_id"],
            "session_id": reviewed["session_id"],
            "expected_version": reviewed["version"],
            "stage_id": "delivery",
            "idempotency_key": "approve-missing-docx",
        },
    )

    assert after_approve["workflow_state"] == "generated_invalid"
    assert "DOCX" in after_approve["last_validation_error"]
    refreshed = next(item for item in after_approve["artifacts"] if item["kind"] == "final_document")
    assert refreshed["exists"] is False
    assert refreshed["status"] == "missing"


def test_run_view_refreshes_artifact_exists_from_filesystem(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    calls = []
    monkeypatch.setattr(docx_engine_v2, "_create_expert_delivery_job", _fake_successful_create_job(calls))
    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id="content-creator-team",
        stage_index=4,
        session_id="sid-refresh-artifact-exists",
    )
    reviewed = _generate(expert_teams, tmp_path, ready, FINAL_MARKDOWN, "refresh-artifact-exists")
    document = next(item for item in reviewed["artifacts"] if item["kind"] == "final_document")
    (tmp_path / document["path"]).unlink()

    refreshed = expert_teams.read_expert_team_run(tmp_path, reviewed["run_id"])

    artifact = next(item for item in refreshed["artifacts"] if item["kind"] == "final_document")
    assert artifact["exists"] is False
    assert artifact["status"] == "missing"


def test_final_revision_preserves_every_delivery_attempt(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    calls = []
    monkeypatch.setattr(docx_engine_v2, "_create_expert_delivery_job", _fake_successful_create_job(calls))
    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id="content-creator-team",
        stage_index=4,
        session_id="sid-final-history",
    )
    first = _generate(expert_teams, tmp_path, ready, FINAL_MARKDOWN, "final-history-1")
    revised = expert_teams.request_expert_team_stage_revision(
        tmp_path,
        {
            "run_id": first["run_id"],
            "session_id": first["session_id"],
            "expected_version": first["version"],
            "stage_id": "delivery",
            "idempotency_key": "revise-final-history",
            "feedback": "补充最终核对说明。",
        },
    )
    second = _generate(expert_teams, tmp_path, revised, FINAL_MARKDOWN + "\n最终核对：已补充。\n", "final-history-2")

    for kind in ("final_rich_draft", "final_document", "delivery_package", "quality_report"):
        rows = [item for item in second["artifacts"] if item["kind"] == kind]
        assert [item["id"] for item in rows] == [f"delivery:1:{kind}", f"delivery:2:{kind}"]
        assert rows[0]["path"] != rows[1]["path"]
        assert (tmp_path / rows[0]["path"]).exists()
        assert (tmp_path / rows[1]["path"]).exists()


@pytest.mark.parametrize(
    ("team_id", "stage_index"),
    [("content-creator-team", 4), ("deep-research-team", 5)],
)
def test_real_engine_produces_openable_docx_for_both_expert_teams(team_id, stage_index, tmp_path):
    import zipfile
    from xml.etree import ElementTree

    from api import expert_teams

    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id=team_id,
        stage_index=stage_index,
        session_id=f"sid-real-docx-{team_id}",
    )
    reviewed = _generate(expert_teams, tmp_path, ready, FINAL_MARKDOWN, f"real-docx-{team_id}")

    assert reviewed["workflow_state"] == "awaiting_review", reviewed.get("last_validation_error")
    artifacts = {item["kind"]: item for item in reviewed["artifacts"]}
    document = tmp_path / artifacts["final_document"]["path"]
    delivery = tmp_path / artifacts["delivery_package"]["path"]
    quality = tmp_path / artifacts["quality_report"]["path"]
    assert zipfile.is_zipfile(document)
    with zipfile.ZipFile(document) as archive:
        document_xml = archive.read("word/document.xml")
    ElementTree.fromstring(document_xml)
    report = json.loads(quality.read_text(encoding="utf-8"))
    assert delivery.is_dir()
    assert report["schemaVersion"] == "docx-engine-v2/validation-report"
    assert report["status"] in {"passed", "passed_with_warnings"}
    assert artifacts["final_document"]["exists"] is True


def test_real_engine_uses_canonical_package_for_existing_image_reference(tmp_path):
    import shutil
    import zipfile
    from xml.etree import ElementTree

    from api import expert_teams

    image_name = "service-flow.png"
    shutil.copy(
        Path(__file__).resolve().parents[1] / "static" / "assets" / "writeflow" / "team-content-creator.png",
        tmp_path / image_name,
    )
    markdown = FINAL_MARKDOWN.replace(
        "```mermaid\nflowchart LR\n  A[需求确认] --> B[专家协作]\n  B --> C[正式交付]\n```",
        f"![服务流程图]({image_name})",
    )
    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id="content-creator-team",
        stage_index=4,
        session_id="sid-real-existing-image",
    )

    reviewed = _generate(expert_teams, tmp_path, ready, markdown, "real-existing-image")

    assert reviewed["workflow_state"] == "awaiting_review", reviewed.get("last_validation_error")
    artifacts = {item["kind"]: item for item in reviewed["artifacts"]}
    document = tmp_path / artifacts["final_document"]["path"]
    assert zipfile.is_zipfile(document)
    with zipfile.ZipFile(document) as archive:
        ElementTree.fromstring(archive.read("word/document.xml"))
        assert any(name.startswith("word/media/") for name in archive.namelist())


def test_real_engine_produces_openable_meeting_minutes_without_rich_package(tmp_path):
    import zipfile
    from xml.etree import ElementTree

    from api import expert_teams
    from api.expert_teams.storage import write_run

    ready = _ready_at_stage(
        expert_teams,
        tmp_path,
        team_id="content-creator-team",
        stage_index=4,
        session_id="sid-real-meeting-minutes",
    )
    ready["prompt"] = "帮我整理一份会议纪要，主题是优化供电服务质效提升措施专题会。"
    write_run(tmp_path, ready)
    ready = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])

    reviewed = _generate(
        expert_teams,
        tmp_path,
        ready,
        MEETING_MINUTES_MARKDOWN,
        "real-meeting-minutes",
    )

    assert reviewed["workflow_state"] == "awaiting_review", reviewed.get("last_validation_error")
    assert not any(item["kind"] == "final_rich_draft" for item in reviewed["artifacts"])
    document_artifact = next(item for item in reviewed["artifacts"] if item["kind"] == "final_document")
    document = tmp_path / document_artifact["path"]
    assert zipfile.is_zipfile(document)
    with zipfile.ZipFile(document) as archive:
        ElementTree.fromstring(archive.read("word/document.xml"))
