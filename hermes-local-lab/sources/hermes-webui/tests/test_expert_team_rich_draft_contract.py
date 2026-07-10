import importlib.util
import hashlib
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from api.expert_teams.materials import validate_stage_output
from api.expert_teams.rich_draft import build_rich_draft_package


def _control(run, key: str, **extra) -> dict:
    return {
        "run_id": run["run_id"],
        "session_id": run.get("session_id"),
        "expected_version": run.get("version"),
        "stage_id": (run.get("current_stage") or {}).get("task_id"),
        "idempotency_key": key,
        **extra,
    }


def _answer_all_required(expert_teams, tmp_path, run):
    return expert_teams.answer_expert_team(
        tmp_path,
        _control(
            run,
            f"answer-required-{run['run_id']}",
            answers={
                "topic": "方案说明，主题是提升营业厅服务质效专项行动",
                "audience": "公司分管领导和营销服务部门，用于专项行动部署",
                "boundary": "正式方案口径，包含目标、任务、进度、责任分工和保障机制",
            },
        ),
    )


def _generate_stage(expert_teams, tmp_path, run, content, delivery_id):
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
        generating["run_id"],
        delivery={
            "stream_id": stream_id,
            "stage_id": generating["execution_stage_id"],
            "attempt": generating["execution_attempt"],
            "id": delivery_id,
            "kind": "chat",
            "content": content,
        },
    )


def _approve_stage(expert_teams, tmp_path, run, key: str):
    return expert_teams.approve_expert_team_stage(tmp_path, _control(run, key))


def _complete_stage(expert_teams, tmp_path, run, content, delivery_id):
    generated = _generate_stage(expert_teams, tmp_path, run, content, delivery_id)
    assert generated["workflow_state"] == "awaiting_review"
    return _approve_stage(expert_teams, tmp_path, generated, f"approve-{delivery_id}")


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


def test_build_rich_draft_package_writes_canonical_v2_manifest_and_assets(tmp_path):
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

```mermaid
flowchart LR
  A[用户入口] --> B[智能编排]
  B --> C[业务系统]
  B --> D[监督闭环]
```
""",
    }

    package = build_rich_draft_package(tmp_path, run, output)

    manifest = tmp_path / package["manifest_path"]
    draft = tmp_path / package["draft_path"]
    asset = tmp_path / package["assets"][0]["path"]
    assert manifest.exists()
    assert draft.exists()
    assert asset.exists()
    manifest_payload = __import__("json").loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["schemaVersion"] == "rich-draft-package/v2"
    assert package["version"] == 2
    assert package["schema_version"] == "rich-draft-package/v2"
    assert package["table_count"] >= 2
    assert package["figure_count"] >= 1
    provenance = tmp_path / package["rich_source_path"]
    assert provenance.read_text(encoding="utf-8") == output["content"].strip() + "\n"
    assert "![" in draft.read_text(encoding="utf-8")
    assert hashlib.sha256(provenance.read_bytes()).hexdigest() != hashlib.sha256(draft.read_bytes()).hexdigest()


def test_build_rich_draft_package_rejects_preexisting_package_for_different_source(tmp_path):
    from api.expert_teams.rich_draft import RichDraftPackagingError

    run = {
        "run_id": "et-stale-rich-package",
        "title": "旧版方案",
        "team_id": "content-creator-team",
    }
    old_output = {
        "task_id": "delivery",
        "stage_attempt": 1,
        "title": "旧版方案",
        "content": """# 旧版方案

| 任务 | 责任人 |
| --- | --- |
| 旧任务 | 甲 |

| 风险 | 对策 |
| --- | --- |
| 旧风险 | 旧对策 |

```mermaid
flowchart LR
  A[旧需求] --> B[旧交付]
```
""",
    }
    build_rich_draft_package(tmp_path, run, old_output)
    current_output = {
        **old_output,
        "title": "当前方案",
        "content": old_output["content"].replace("旧版方案", "当前方案").replace("旧任务", "当前任务"),
    }
    source = (
        tmp_path
        / ".taiji"
        / "rich-drafts"
        / run["run_id"]
        / "delivery"
        / "attempt-1"
        / "draft.md"
    )
    source.write_text(current_output["content"].strip() + "\n", encoding="utf-8")

    with pytest.raises(RichDraftPackagingError, match="不一致|变化"):
        build_rich_draft_package(tmp_path, run, current_output)


def test_build_rich_draft_package_does_not_trust_tampered_packaged_markdown(tmp_path):
    run = {
        "run_id": "et-tampered-rich-package",
        "title": "专项行动方案",
        "team_id": "content-creator-team",
    }
    output = {
        "task_id": "draft",
        "stage_attempt": 1,
        "title": "专项行动方案",
        "content": """# 专项行动方案

| 任务 | 责任人 |
| --- | --- |
| 梳理流程 | 甲 |

| 风险 | 对策 |
| --- | --- |
| 口径不一致 | 统一口径 |

```mermaid
flowchart LR
  A[当前需求] --> B[当前交付]
```
""",
    }
    first = build_rich_draft_package(tmp_path, run, output)
    packaged_draft = tmp_path / first["draft_path"]
    source = tmp_path / first["rich_source_path"]
    original_source = source.read_text(encoding="utf-8")
    packaged_draft.write_text("# 恶意篡改\n\n不应进入交付。\n", encoding="utf-8")

    rebuilt = build_rich_draft_package(tmp_path, run, output)

    rebuilt_text = (tmp_path / rebuilt["draft_path"]).read_text(encoding="utf-8")
    assert "恶意篡改" not in rebuilt_text
    assert "当前需求" in rebuilt_text
    assert "![" in rebuilt_text
    assert source.read_text(encoding="utf-8") == original_source
    assert rebuilt["package_files"] == first["package_files"]


def test_build_rich_draft_package_rebuilds_old_package_when_source_hash_is_forged(tmp_path):
    run = {
        "run_id": "et-forged-rich-package",
        "title": "方案",
        "team_id": "content-creator-team",
    }
    old_output = {
        "task_id": "draft",
        "stage_attempt": 1,
        "title": "旧方案",
        "content": """# 旧方案

| 任务 | 责任人 |
| --- | --- |
| 旧任务 | 甲 |

| 风险 | 对策 |
| --- | --- |
| 旧风险 | 旧对策 |

```mermaid
flowchart LR
  A[旧需求] --> B[旧交付]
```
""",
    }
    first = build_rich_draft_package(tmp_path, run, old_output)
    current_output = {
        **old_output,
        "title": "当前方案",
        "content": old_output["content"]
        .replace("旧方案", "当前方案")
        .replace("旧任务", "当前任务")
        .replace("旧需求", "当前需求")
        .replace("旧交付", "当前交付"),
    }
    source = tmp_path / first["rich_source_path"]
    source.write_text(current_output["content"].strip() + "\n", encoding="utf-8")
    manifest_path = tmp_path / first["manifest_path"]
    manifest = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inputSourceSha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest_path.write_text(
        __import__("json").dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    rebuilt = build_rich_draft_package(tmp_path, run, current_output)

    rebuilt_text = (tmp_path / rebuilt["draft_path"]).read_text(encoding="utf-8")
    assert "当前方案" in rebuilt_text
    assert "当前任务" in rebuilt_text
    assert "旧方案" not in rebuilt_text
    assert "旧任务" not in rebuilt_text
    assert source.read_text(encoding="utf-8") == current_output["content"].strip() + "\n"


def test_build_rich_draft_package_failed_rebuild_preserves_valid_package_and_cleans_temp(
    monkeypatch,
    tmp_path,
):
    from api import docx_engine_v2
    from api.expert_teams.rich_draft import RichDraftPackagingError

    run = {"run_id": "et-failed-rich-rebuild", "title": "方案"}
    output = {
        "task_id": "draft",
        "stage_attempt": 1,
        "content": """# 方案

| 任务 | 责任人 |
| --- | --- |
| 当前任务 | 甲 |

| 风险 | 对策 |
| --- | --- |
| 当前风险 | 当前对策 |

```mermaid
flowchart LR
  A[当前需求] --> B[当前交付]
```
""",
    }
    first = build_rich_draft_package(tmp_path, run, output)
    package_dir = tmp_path / first["package_dir"]
    before = {
        path.relative_to(package_dir).as_posix(): path.read_bytes()
        for path in package_dir.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        docx_engine_v2,
        "package_rich_draft",
        lambda *_args, **_kwargs: ({"ok": False, "message": "模拟打包失败"}, 400),
    )

    with pytest.raises(RichDraftPackagingError, match="模拟打包失败"):
        build_rich_draft_package(tmp_path, run, output)

    after = {
        path.relative_to(package_dir).as_posix(): path.read_bytes()
        for path in package_dir.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not list(package_dir.parent.glob(".package-build-*"))
    assert not list(package_dir.parent.glob(".package-backup-*"))


def test_build_rich_draft_package_serializes_concurrent_rebuilds(monkeypatch, tmp_path):
    from api import docx_engine_v2

    run = {"run_id": "et-concurrent-rich-rebuild", "title": "方案"}
    output = {
        "task_id": "draft",
        "stage_attempt": 1,
        "content": """# 方案

| 任务 | 责任人 |
| --- | --- |
| 当前任务 | 甲 |

| 风险 | 对策 |
| --- | --- |
| 当前风险 | 当前对策 |

```mermaid
flowchart LR
  A[当前需求] --> B[当前交付]
```
""",
    }
    real_package = docx_engine_v2.package_rich_draft
    counter_lock = threading.Lock()
    active = 0
    max_active = 0

    def slow_package(*args, **kwargs):
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return real_package(*args, **kwargs)
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(docx_engine_v2, "package_rich_draft", slow_package)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(build_rich_draft_package, tmp_path, run, output) for _ in range(2)]
        results = [future.result() for future in futures]

    assert max_active == 1
    assert all((tmp_path / result["draft_path"]).is_file() for result in results)
    package_dir = tmp_path / results[-1]["package_dir"]
    assert not list(package_dir.parent.glob(".package-build-*"))
    assert not list(package_dir.parent.glob(".package-backup-*"))


def test_expert_team_draft_completion_registers_rich_draft_artifact(monkeypatch, tmp_path):
    from api import expert_teams
    from tests.test_expert_team_api import _patch_successful_docx_delivery

    _patch_successful_docx_delivery(monkeypatch, tmp_path)

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-rich-draft",
            "team_id": "content-creator-team",
            "prompt": "帮我起草一份方案说明，主题是提升营业厅服务质效专项行动。",
        },
    )
    required = _answer_all_required(expert_teams, tmp_path, run)
    ready = expert_teams.answer_expert_team(
        tmp_path,
        _control(
            required,
            f"answer-optional-{run['run_id']}",
            answers={"optional_context": ""},
            skip_optional=True,
        ),
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

    completed = _generate_stage(
        expert_teams,
        tmp_path,
        draft_ready,
        """# 提升营业厅服务质效专项行动方案

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

```mermaid
flowchart LR
  A[用户入口] --> B[智能编排]
  B --> C[业务系统]
  B --> D[监督闭环]
```
""",
        "draft-stage",
    )

    rich_artifact = next(item for item in completed["artifacts"] if item["kind"] == "rich_draft")
    assert completed["workflow_state"] == "awaiting_review"
    assert rich_artifact["id"] == "draft:1:rich_draft"
    assert rich_artifact["stage"] == "draft"
    assert rich_artifact["attempt"] == 1
    assert rich_artifact["manifest_path"].endswith("draft.manifest.json")
    assert rich_artifact["path"].endswith("draft.md")
    assert (tmp_path / rich_artifact["manifest_path"]).exists()
    assert (tmp_path / rich_artifact["path"]).exists()
    assert (tmp_path / rich_artifact["assets"][0]["path"]).exists()

    polish_ready = _approve_stage(expert_teams, tmp_path, completed, "approve-draft-stage")
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

    delivery_review = _generate_stage(
        expert_teams,
        tmp_path,
        delivery_ready,
        """# 提升营业厅服务质效专项行动方案

## 一、总体目标
围绕业务办理效率、客户诉求响应和服务闭环能力开展专项行动。

| 序号 | 重点任务 | 责任单位 |
| --- | --- | --- |
| 1 | 梳理高频业务流程 | 营销部 |
| 2 | 建立问题闭环台账 | 服务中心 |

| 风险 | 应对措施 |
| --- | --- |
| 数据口径不一致 | 统一指标口径 |

```mermaid
flowchart LR
  A[用户入口] --> B[业务系统]
  B --> C[监督闭环]
```

待人工补充事项：正式流转前确认责任单位名称和数据口径。
""",
        "delivery-stage",
    )
    assert delivery_review["workflow_state"] == "awaiting_review"

    final = _approve_stage(expert_teams, tmp_path, delivery_review, "approve-delivery-stage")
    assert final["workflow_state"] == "completed"
    assert final["view"]["presentation"]["state"] == "completed"
    assert final["view"]["presentation"]["primary_action"]["id"] == "view_result"
