# Rich Draft Assets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make方案类初稿在生成阶段同步产出正文、表格、图示资产和可被模板消费的 manifest，后续 `docx-template-skill` 只做模板嵌套和版式处理。

**Architecture:** Introduce a `rich_draft` contract in Taiji Agent WebUI as the boundary between content generation and document templating. Expert-team draft stages and normal plan-like chat requests produce a rich draft package (`draft.md`, `draft.manifest.json`, `assets/*`); the docx template skill consumes that package and rejects incomplete packages instead of generating missing content during template application.

**Tech Stack:** Python WebUI backend, existing expert-team state machine, Markdown artifacts, deterministic SVG diagram generation, existing Node-based `docx-template-skill` renderer, pytest, Node test runner.

---

## Current Evidence And Constraints

- Taiji Agent repo: `/Users/bwb/Documents/工作/taiji-agentv1.0`
- Template renderer repo: `/Users/bwb/Documents/工作/文档模板渲染引擎`
- Installed runtime skill: `/Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill`
- Existing Taiji worktree is dirty. Preserve unrelated changes:
  - `hermes-local-lab/sources/hermes-webui/static/messages.js`
  - `hermes-local-lab/sources/hermes-webui/tests/test_docx_template_skill_invocation.py`
  - `tools/demo_materials/`
  - `演示资料包/`
- Current `docx-template-skill` incorrectly contains a template-stage fallback that creates baseline proposal assets. That must be moved out of template application or turned into an explicit draft-stage utility.
- Frontend-touching steps that modify `static/messages.js` require the project `$frontend-ux-qa` gate before completion.

## File Structure

### Taiji Agent WebUI

- Create: `hermes-local-lab/sources/hermes-webui/api/expert_teams/rich_draft.py`
  - Owns rich draft detection, validation, deterministic SVG generation, and package writing.
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/materials.py`
  - Calls rich draft validation for plan-like draft stages.
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py`
  - Builds and attaches rich draft package during draft-stage completion.
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/catalog.py`
  - Renames draft tasks and descriptions to rich-content draft expectations.
- Modify: `hermes-local-lab/custom-skills/writing-agent/workflow-producer/SKILL.md`
  - Updates generation contract so writer stages output rich draft content, not plain prose.
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
  - Adds server-side chat-start enrichment for plan-like prompts, so normal chat can request rich drafts without a separate UI mode.
- Modify: `hermes-local-lab/sources/hermes-webui/static/messages.js`
  - Only if backend enrichment cannot cover the current browser flow; preserve existing `normalizeDocxTemplateInvocationText` changes.
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_rich_draft_contract.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_rich_draft_chat_routing.py`

### Document Template Renderer

- Modify: `/Users/bwb/Documents/工作/文档模板渲染引擎/carbone/scripts/apply-template.js`
  - Stops creating missing proposal assets by default; consumes existing rich draft manifest when supplied.
- Modify: `/Users/bwb/Documents/工作/文档模板渲染引擎/carbone/lib/proposal-assets.js`
  - Keep deterministic SVG helpers if reused by rich draft generation, but remove template-stage auto-fill ownership.
- Modify: `/Users/bwb/Documents/工作/文档模板渲染引擎/carbone/docs/skill-invocation-contract.md`
  - Documents that image/table generation belongs to rich draft generation, not template application.
- Modify installed skill copies under `/Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill`
  - Sync the same runtime behavior after source tests pass.
- Test: `/Users/bwb/Documents/工作/文档模板渲染引擎/carbone/test/proposal-assets.test.js`
- Test: `/Users/bwb/Documents/工作/文档模板渲染引擎/tests/skill-package.test.js`

---

## Task 0: Baseline Protection

**Files:**
- Inspect only: all touched files above

- [ ] **Step 1: Capture current dirty worktree state**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git status --short
git diff -- hermes-local-lab/sources/hermes-webui/static/messages.js
```

Expected:

```text
 M hermes-local-lab/sources/hermes-webui/static/messages.js
?? hermes-local-lab/sources/hermes-webui/tests/test_docx_template_skill_invocation.py
```

The exact extra untracked demo directories may vary. Do not delete or revert them.

- [ ] **Step 2: Run current focused tests before edits**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest tests/test_docx_template_skill_invocation.py -q
python -m pytest tests/test_regressions.py::test_all_api_modules_importable -q
```

Expected:

```text
passed
```

If these fail before edits, record the failure and stop. Do not mix unrelated repair with this implementation.

- [ ] **Step 3: Verify template renderer baseline**

Run:

```bash
cd /Users/bwb/Documents/工作/文档模板渲染引擎/carbone
npm test
```

Expected:

```text
fail 0
```

---

## Task 1: Add Rich Draft Contract Tests

**Files:**
- Create: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_rich_draft_contract.py`
- Create later: `hermes-local-lab/sources/hermes-webui/api/expert_teams/rich_draft.py`

- [ ] **Step 1: Write the failing tests**

Create `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_rich_draft_contract.py`:

```python
from pathlib import Path

from api.expert_teams.materials import validate_stage_output


def test_plan_draft_rejects_plain_prose_without_tables_or_figures():
    text = "这是一个纯文字方案。目标是提升服务质效。措施包括优化流程和强化监督。"

    result = validate_stage_output(text, "plan", "draft", "content-creator-team")

    assert result["status"] == "rewrite_required"
    assert "表格" in result["message"]
    assert "图" in result["message"]


def test_plan_draft_accepts_markdown_tables_and_figure_brief(tmp_path):
    svg = tmp_path / "architecture.svg"
    svg.write_text("<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>", encoding="utf-8")
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
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest tests/test_expert_team_rich_draft_contract.py -q
```

Expected:

```text
FAILED
ModuleNotFoundError or AssertionError
```

The failure must prove plan drafts currently accept plain prose.

---

## Task 2: Implement Rich Draft Validation

**Files:**
- Create: `hermes-local-lab/sources/hermes-webui/api/expert_teams/rich_draft.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/materials.py`

- [ ] **Step 1: Add the rich draft module**

Create `api/expert_teams/rich_draft.py`:

```python
"""Rich draft contract for plan-like expert-team outputs."""

from __future__ import annotations

import re
from pathlib import Path


PLAN_LIKE_MATERIAL_TYPES = {"plan", "research_report"}
PLAN_LIKE_TASK_IDS = {"draft"}
MIN_TABLES = 2
MIN_FIGURES = 1


def is_rich_draft_required(material_type: str, task_id: str, team_id: str = "") -> bool:
    material = str(material_type or "").strip()
    task = str(task_id or "").strip()
    return material in PLAN_LIKE_MATERIAL_TYPES and task in PLAN_LIKE_TASK_IDS


def markdown_table_count(text: str) -> int:
    lines = (text or "").splitlines()
    count = 0
    for index in range(len(lines) - 1):
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        if "|" not in header or "|" not in separator:
            continue
        if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", separator):
            count += 1
    return count


def figure_reference_count(text: str) -> int:
    raw = text or ""
    markdown_images = len(re.findall(r"!\[[^\]]+\]\([^)]+\)", raw))
    figure_sections = len(re.findall(r"(架构图|流程图|用例图|部署图|路线图|图示设计|图示说明)", raw))
    return max(markdown_images, figure_sections)


def validate_rich_draft_text(text: str) -> dict:
    tables = markdown_table_count(text)
    figures = figure_reference_count(text)
    missing = []
    if tables < MIN_TABLES:
        missing.append(f"至少 {MIN_TABLES} 个 Markdown 表格")
    if figures < MIN_FIGURES:
        missing.append("至少 1 个架构图、流程图、用例图或图示引用")
    if missing:
        return {
            "status": "rewrite_required",
            "violations": [],
            "missing_sections": missing,
            "message": "方案类初稿必须在生成阶段包含表格和图示，请重新生成富内容初稿：" + "、".join(missing) + "。",
        }
    return {"status": "pass", "violations": [], "missing_sections": [], "message": ""}
```

- [ ] **Step 2: Wire validation into materials.py**

In `api/expert_teams/materials.py`, import the helpers:

```python
from .rich_draft import is_rich_draft_required, validate_rich_draft_text
```

Inside `validate_stage_output`, before the deep-research branch and after the `plan/direction` branch, add:

```python
    if is_rich_draft_required(material_type, task, team_id):
        return validate_rich_draft_text(normalized)
```

- [ ] **Step 3: Run focused tests to verify GREEN**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest tests/test_expert_team_rich_draft_contract.py -q
```

Expected:

```text
2 passed
```

---

## Task 3: Build Rich Draft Package Files

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/rich_draft.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_rich_draft_contract.py`

- [ ] **Step 1: Add failing package test**

Append to `tests/test_expert_team_rich_draft_contract.py`:

```python
from api.expert_teams.rich_draft import build_rich_draft_package


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
```

- [ ] **Step 2: Run package test to verify RED**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest tests/test_expert_team_rich_draft_contract.py::test_build_rich_draft_package_writes_manifest_markdown_and_svg -q
```

Expected:

```text
FAILED
ImportError or AttributeError
```

- [ ] **Step 3: Implement deterministic package builder**

Add to `api/expert_teams/rich_draft.py`:

```python
import json
from datetime import datetime, timezone


def _safe_slug(value: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "-", str(value or "").strip())
    raw = raw.strip("-_")
    return raw[:48] or "rich-draft"


def _architecture_svg(title: str) -> str:
    safe_title = str(title or "方案总体架构图").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    boxes = [
        ("用户入口", 80, 110),
        ("智能编排", 260, 110),
        ("业务系统", 440, 110),
        ("数据台账", 260, 250),
        ("监督闭环", 440, 250),
    ]
    rects = []
    for label, x, y in boxes:
        rects.append(f'<rect x="{x}" y="{y}" width="130" height="54" rx="8" fill="#E8F3FF" stroke="#2F6BFF"/>')
        rects.append(f'<text x="{x + 65}" y="{y + 34}" text-anchor="middle" font-size="16" fill="#1F2937">{label}</text>')
    arrows = [
        '<path d="M210 137 L260 137" stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>',
        '<path d="M390 137 L440 137" stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>',
        '<path d="M325 164 L325 250" stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>',
        '<path d="M390 277 L440 277" stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>',
    ]
    return "\n".join([
        '<svg xmlns="http://www.w3.org/2000/svg" width="680" height="380" viewBox="0 0 680 380">',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L6,3 z" fill="#475569"/></marker></defs>',
        '<rect width="680" height="380" fill="#FFFFFF"/>',
        f'<text x="340" y="52" text-anchor="middle" font-size="22" font-weight="700" fill="#111827">{safe_title}</text>',
        *rects,
        *arrows,
        '<text x="340" y="344" text-anchor="middle" font-size="14" fill="#64748B">该图由富内容初稿阶段生成，供后续模板套用直接消费。</text>',
        '</svg>',
    ])


def build_rich_draft_package(workspace: Path, run: dict, output: dict) -> dict:
    root = Path(workspace) / ".taiji" / "rich-drafts" / _safe_slug(str(run.get("run_id") or "run"))
    assets_dir = root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    title = str(output.get("title") or run.get("title") or "方案初稿")
    content = str(output.get("content") or "").strip()
    draft_path = root / "draft.md"
    draft_path.write_text(content + "\n", encoding="utf-8")
    svg_path = assets_dir / "architecture.svg"
    svg_path.write_text(_architecture_svg(title), encoding="utf-8")
    package = {
        "version": 1,
        "kind": "rich_draft",
        "title": title,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "draft_path": str(draft_path.relative_to(workspace)),
        "manifest_path": str((root / "draft.manifest.json").relative_to(workspace)),
        "table_count": markdown_table_count(content),
        "figure_count": max(figure_reference_count(content), 1),
        "assets": [
            {
                "id": "architecture",
                "kind": "diagram",
                "title": "总体架构图",
                "path": str(svg_path.relative_to(workspace)),
                "status": "generated",
            }
        ],
    }
    manifest_path = root / "draft.manifest.json"
    manifest_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return package
```

- [ ] **Step 4: Run focused package tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest tests/test_expert_team_rich_draft_contract.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Integrate package builder in runtime**

In `api/expert_teams/runtime.py`, import:

```python
from .rich_draft import build_rich_draft_package, is_rich_draft_required
```

In `mark_expert_team_execution_complete`, after `task_id` is set and before `validation = validate_stage_output(...)`, add:

```python
    if is_rich_draft_required(material_type, task_id, str(run.get("team_id") or "")):
        rich_draft = build_rich_draft_package(workspace, run, output)
        output["rich_draft"] = rich_draft
        run.setdefault("artifacts", [])
        run["artifacts"].append(
            {
                "id": "rich_draft",
                "kind": "rich_draft",
                "label": "富内容初稿包",
                "path": rich_draft["manifest_path"],
                "exists": True,
            }
        )
```

- [ ] **Step 6: Add runtime integration test**

Append to the same test file:

```python
from api.expert_teams.runtime import answer_expert_team, approve_expert_team_stage, mark_expert_team_execution_complete, start_expert_team


def test_expert_team_draft_stage_records_rich_draft_artifact(tmp_path):
    run = start_expert_team(
        tmp_path,
        {
            "team_id": "content-creator-team",
            "session_id": "s1",
            "prompt": "帮我起草一份方案说明，主题是提升营业厅服务质效专项行动。",
        },
    )
    answers = {question["id"]: "测试答案" for question in run["questions"]}
    run = answer_expert_team(tmp_path, {"run_id": run["run_id"], "answers": answers, "skip_optional": True})
    run = mark_expert_team_execution_complete(tmp_path, run["run_id"], {"content": "阶段摘要：计划\\n待人工：无"})
    run = approve_expert_team_stage(tmp_path, {"run_id": run["run_id"]})
    run = mark_expert_team_execution_complete(tmp_path, run["run_id"], {"content": "阶段摘要：素材\\n待人工：无"})
    run = approve_expert_team_stage(tmp_path, {"run_id": run["run_id"]})
    rich_text = """# 方案初稿

| 序号 | 重点任务 | 责任单位 |
| --- | --- | --- |
| 1 | 梳理流程 | 营销部 |
| 2 | 建立台账 | 服务中心 |

| 风险 | 表现 | 应对措施 |
| --- | --- | --- |
| 口径不一 | 无法比对 | 统一指标 |
| 进度滞后 | 节点延期 | 周度督办 |

## 图示设计
请生成总体架构图。
"""
    run = mark_expert_team_execution_complete(tmp_path, run["run_id"], {"content": rich_text})

    output = run["stage_outputs"][-1]
    package = output["rich_draft"]
    assert package["table_count"] == 2
    assert (tmp_path / package["manifest_path"]).exists()
    assert any(artifact["kind"] == "rich_draft" for artifact in run["artifacts"])
```

- [ ] **Step 7: Verify runtime integration**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest tests/test_expert_team_rich_draft_contract.py -q
```

Expected:

```text
4 passed
```

---

## Task 4: Update Expert-Team Generation Contract

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/catalog.py`
- Modify: `hermes-local-lab/custom-skills/writing-agent/workflow-producer/SKILL.md`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_rich_draft_contract.py`

- [ ] **Step 1: Add failing contract text test**

Append:

```python
def test_catalog_and_skill_contract_name_rich_content_draft():
    repo = Path(__file__).resolve().parents[1]
    catalog = (repo / "api" / "expert_teams" / "catalog.py").read_text(encoding="utf-8")
    skill = (repo.parents[2] / "custom-skills" / "writing-agent" / "workflow-producer" / "SKILL.md").read_text(encoding="utf-8")

    assert "富内容初稿" in catalog
    assert "至少 2 个表格" in skill
    assert "至少 1 个图示" in skill
    assert "draft.manifest.json" in skill
```

- [ ] **Step 2: Run to verify RED**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest tests/test_expert_team_rich_draft_contract.py::test_catalog_and_skill_contract_name_rich_content_draft -q
```

Expected: FAIL because current catalog and skill text do not contain the full contract.

- [ ] **Step 3: Update catalog draft task labels**

In `api/expert_teams/catalog.py`, change the content draft phase:

```python
{"id": "draft", "title": "生成富内容办公材料初稿", "phase": "富内容初稿", "worker_id": "writer", "worker_name": "文案创作专家"},
```

Change the deep research draft phase:

```python
{"id": "draft", "title": "生成富内容研究材料初稿", "phase": "富内容初稿", "worker_id": "writer", "worker_name": "材料起草专家"},
```

- [ ] **Step 4: Update workflow producer skill contract**

In `hermes-local-lab/custom-skills/writing-agent/workflow-producer/SKILL.md`, replace the draft-stage artifact rule with:

```markdown
- For plan-like office materials and research reports, the draft stage must produce a rich content draft, not plain prose. The draft must include:
  - a complete Markdown body saved as `draft.md`;
  - at least 2 Markdown tables for plan, risk, responsibility, comparison, schedule, or acceptance content;
  - at least 1 diagram or figure brief for architecture, process, use case, deployment, or roadmap content;
  - a `draft.manifest.json` contract that lists draft path, table count, figure count, and generated assets;
  - generated local diagram assets under `assets/` when image generation is unavailable.
- Template application is a later formatting step. Do not defer table or figure creation to `docx-template-skill`.
```

- [ ] **Step 5: Verify contract tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest tests/test_expert_team_rich_draft_contract.py -q
```

Expected:

```text
5 passed
```

---

## Task 5: Add Normal Chat Rich-Draft Routing

**Files:**
- Prefer modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
- Fallback modify: `hermes-local-lab/sources/hermes-webui/static/messages.js`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_rich_draft_chat_routing.py`

- [ ] **Step 1: Write failing backend routing test**

Create `tests/test_rich_draft_chat_routing.py`:

```python
from pathlib import Path


ROUTES = (Path(__file__).resolve().parents[1] / "api" / "routes.py").read_text(encoding="utf-8")


def test_chat_start_has_plan_like_rich_draft_enrichment_hook():
    assert "def _enrich_plan_like_chat_prompt(" in ROUTES
    assert "富内容初稿" in ROUTES
    assert "draft.manifest.json" in ROUTES
    assert "_enrich_plan_like_chat_prompt(msg_text)" in ROUTES
```

- [ ] **Step 2: Run to verify RED**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest tests/test_rich_draft_chat_routing.py -q
```

Expected: FAIL because no enrichment hook exists.

- [ ] **Step 3: Add server-side prompt enrichment helper**

In `api/routes.py`, near chat-start helper functions, add:

```python
def _enrich_plan_like_chat_prompt(msg_text: str) -> str:
    text = str(msg_text or "")
    stripped = text.strip()
    if not stripped:
        return text
    if "富内容初稿" in stripped or "/docx-template-skill" in stripped:
        return text
    has_action = any(keyword in stripped for keyword in ("生成", "起草", "编写", "制定", "输出"))
    has_document = any(keyword in stripped for keyword in ("方案", "实施方案", "建设方案", "可研", "报告", "材料初稿"))
    if not (has_action and has_document):
        return text
    rich_contract = (
        "\n\n[太极富内容初稿要求]\n"
        "请把本次方案类输出作为富内容初稿交付。正文可以在聊天中摘要展示，但必须同步生成或明确列出可落地文件内容：\n"
        "1. draft.md：完整正文，包含至少 2 个 Markdown 表格；\n"
        "2. assets/：至少 1 个架构图、流程图、用例图、部署图或路线图资产；图片模型不可用时生成 SVG 图示；\n"
        "3. draft.manifest.json：记录标题、正文路径、表格数量、图示数量和资产路径；\n"
        "4. 不要把表格和配图任务留到后续套模板阶段。\n"
    )
    return stripped + rich_contract
```

Find the chat-start path where `msg_text` is passed into `_start_chat_stream_for_session`, and insert:

```python
msg_text = _enrich_plan_like_chat_prompt(msg_text)
```

This must occur before `_start_chat_stream_for_session(...)` is called for `/api/chat/start`.

- [ ] **Step 4: Run routing test**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest tests/test_rich_draft_chat_routing.py -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Only if backend enrichment is not reached, extend messages.js**

If backend tests or manual traces show `/api/chat/start` bypasses the new helper, extend the existing uncommitted `normalizeDocxTemplateInvocationText` pattern without deleting it:

```javascript
function normalizeRichDraftInvocationText(text){
  const value=String(text||'').trim();
  if(!value||value.includes('富内容初稿')||value.includes('/docx-template-skill'))return text;
  const hasAction=/(生成|起草|编写|制定|输出)/.test(value);
  const hasDocument=/(方案|实施方案|建设方案|可研|报告|材料初稿)/.test(value);
  if(!hasAction||!hasDocument)return text;
  return `${value}\n\n请按富内容初稿交付：同步生成完整正文、至少 2 个 Markdown 表格、至少 1 个架构图/流程图/用例图/路线图资产，并生成 draft.manifest.json。后续套模板只做版式处理，不再补图补表。`;
}
```

Then change send flow from:

```javascript
let msgText=normalizeDocxTemplateInvocationText(text);
```

to:

```javascript
let msgText=normalizeRichDraftInvocationText(normalizeDocxTemplateInvocationText(text));
```

Because this touches frontend code, run the required `$frontend-ux-qa` gate before final completion and include a Chinese frontend UX QA report.

---

## Task 6: Remove Template-Stage Asset Generation Ownership

**Files:**
- Modify: `/Users/bwb/Documents/工作/文档模板渲染引擎/carbone/scripts/apply-template.js`
- Modify: `/Users/bwb/Documents/工作/文档模板渲染引擎/carbone/test/proposal-assets.test.js`
- Modify: `/Users/bwb/Documents/工作/文档模板渲染引擎/carbone/docs/skill-invocation-contract.md`
- Sync installed runtime skill after source tests pass.

- [ ] **Step 1: Write failing template-boundary test**

In `carbone/test/proposal-assets.test.js`, replace the old plain-text auto-asset expectation with:

```javascript
test('plain text general proposal wrapper rejects missing rich draft assets', async () => {
  const tmpDir = makeTempDir();
  const sourcePath = path.join(tmpDir, 'source.txt');
  const outPath = path.join(tmpDir, 'proposal.docx');
  fs.writeFileSync(sourcePath, '太极 Agent 建设方案\\n这里只有纯文字，没有表格和图片。', 'utf8');

  const result = runNode('scripts/apply-template.js', [
    '--template-id',
    'general-proposal',
    '--source',
    sourcePath,
    '--out',
    outPath,
  ]);

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /富内容初稿|tables|figures|images|表格|图片/i);
  assert.equal(fs.existsSync(outPath), false);
});
```

- [ ] **Step 2: Run to verify RED**

Run:

```bash
cd /Users/bwb/Documents/工作/文档模板渲染引擎/carbone
node --test test/proposal-assets.test.js
```

Expected: FAIL because current wrapper still auto-creates assets.

- [ ] **Step 3: Change apply-template.js to consume, not create**

In `carbone/scripts/apply-template.js`, remove the call path that unconditionally invokes:

```javascript
ensureGeneralProposalAssets(data, normalized, options)
```

Replace it with validation that fails when source lacks required rich draft assets:

```javascript
function assertRichDraftAssets(data) {
  const tables = Array.isArray(data.tables) ? data.tables : [];
  const figures = Array.isArray(data.figures) ? data.figures : [];
  const images = Array.isArray(data.images) ? data.images : [];
  if (tables.length < 2 || figures.length < 1 || images.length < 1) {
    throw new Error('general-proposal requires a rich draft package with at least two tables, one figure, and one insertable image before template application.');
  }
}
```

Call `assertRichDraftAssets(data)` for `general-proposal` before rendering. Keep `proposal-assets.js` only as a reusable deterministic diagram helper if another task imports it explicitly.

- [ ] **Step 4: Update contract docs**

In `carbone/docs/skill-invocation-contract.md`, replace the line that says plain text sources may use `scripts/apply-template.js` to create deterministic architecture SVG with:

```markdown
For `general-proposal`, `scripts/apply-template.js` consumes an existing rich draft package. It must not create missing tables, figures, or images during template application. If a source is plain prose, generate a rich draft first, then apply the template.
```

- [ ] **Step 5: Run renderer tests**

Run:

```bash
cd /Users/bwb/Documents/工作/文档模板渲染引擎/carbone
node --test test/proposal-assets.test.js
npm test
```

Expected:

```text
fail 0
```

- [ ] **Step 6: Sync installed runtime skill**

Run these copy commands only after source tests pass:

```bash
cp carbone/scripts/apply-template.js /Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill/scripts/apply-template.js
cp carbone/docs/skill-invocation-contract.md /Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill/references/skill-invocation-contract.md
```

Edit `/Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill/SKILL.md` so it states:

```markdown
For `general-proposal`, do not generate missing tables or images during template application. Require a rich draft package or a source document that already contains the needed tables and image assets.
```

---

## Task 7: Adversarial Tests

**Files:**
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_rich_draft_contract.py`
- Test: `/Users/bwb/Documents/工作/文档模板渲染引擎/carbone/test/proposal-assets.test.js`

- [ ] **Step 1: Add adversarial tests for Taiji validation**

Append:

```python
def test_rich_draft_rejects_fake_table_words_without_markdown_table():
    text = "本方案包含风险表和进度表。风险表如下：风险很多。进度表如下：尽快完成。图示说明：见后续。"

    result = validate_stage_output(text, "plan", "draft", "content-creator-team")

    assert result["status"] == "rewrite_required"
    assert "Markdown 表格" in "、".join(result["missing_sections"])


def test_rich_draft_rejects_single_table_even_with_figure_text():
    text = """# 方案

| 风险 | 措施 |
| --- | --- |
| 进度滞后 | 周度督办 |

## 架构图
请生成架构图。
"""

    result = validate_stage_output(text, "plan", "draft", "content-creator-team")

    assert result["status"] == "rewrite_required"
    assert any("至少 2" in item for item in result["missing_sections"])
```

- [ ] **Step 2: Run adversarial tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest tests/test_expert_team_rich_draft_contract.py -q
```

Expected:

```text
all passed
```

- [ ] **Step 3: Add adversarial renderer test**

In `carbone/test/proposal-assets.test.js`, add:

```javascript
test('general proposal renderer rejects JSON with image title but missing image file', () => {
  const tmpDir = makeTempDir();
  const dataPath = path.join(tmpDir, 'data.json');
  const outPath = path.join(tmpDir, 'proposal.docx');
  const sample = JSON.parse(fs.readFileSync(path.join(projectDir, 'templates/general-proposal/sample.json'), 'utf8'));
  sample.images = [{ title: '架构图', path: path.join(tmpDir, 'missing.svg'), width: 500, height: 260, required: true }];
  fs.writeFileSync(dataPath, JSON.stringify(sample), 'utf8');

  const result = runNode('render.js', [
    '--template',
    path.join(projectDir, 'templates/general-proposal/template.docx'),
    '--data',
    dataPath,
    '--schema',
    path.join(projectDir, 'templates/general-proposal/schema.json'),
    '--out',
    outPath,
  ]);

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /missing|ENOENT|图片|image/i);
});
```

- [ ] **Step 4: Run renderer adversarial tests**

Run:

```bash
cd /Users/bwb/Documents/工作/文档模板渲染引擎/carbone
node --test test/proposal-assets.test.js
```

Expected:

```text
all passed
```

---

## Task 8: End-To-End Regression

**Files:**
- All modified files

- [ ] **Step 1: Run Taiji focused tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
python -m pytest \
  tests/test_expert_team_rich_draft_contract.py \
  tests/test_rich_draft_chat_routing.py \
  tests/test_docx_template_skill_invocation.py \
  tests/test_regressions.py::test_all_api_modules_importable \
  -q
```

Expected:

```text
all passed
```

- [ ] **Step 2: Run template renderer tests**

Run:

```bash
cd /Users/bwb/Documents/工作/文档模板渲染引擎/carbone
npm test
cd /Users/bwb/Documents/工作/文档模板渲染引擎
node --test tests/*.test.js
unzip -tq docx-template-skill.zip
```

Expected:

```text
fail 0
No errors detected in compressed data of docx-template-skill.zip
```

- [ ] **Step 3: Manual package smoke**

Create a rich draft package through the Taiji helper test or a temporary script, then apply the template to a JSON/source that already contains two tables and one image path.

Expected checks:

```text
draft.md exists
draft.manifest.json exists
assets/architecture.svg exists
rendered DOCX contains at least 2 tables
rendered DOCX contains at least 1 drawing/media item
```

- [ ] **Step 4: Required frontend UX QA gate if messages.js changed**

If `hermes-local-lab/sources/hermes-webui/static/messages.js` was modified beyond the existing dirty change, run the project `$frontend-ux-qa` process and produce a Chinese report with:

```text
《前端 UX QA 报告》
已验证：
- 普通聊天输入不会被无关改写。
- “生成方案/起草报告”类输入会进入富内容初稿要求。
- “套用模板”仍保持模板选择必选。
未验证：
- 真实 Electron 长链路，除非本轮实际打开 Electron 并完成一次生成。
P1：
- 如果已实现富内容生成功能但聊天或专家团没有可见入口，视为 P1。
```

- [ ] **Step 5: Real Taiji workflow smoke**

Run in the actual Taiji Agent UI:

```text
帮我生成一份“提升营业厅服务质效专项行动”的方案初稿
```

Expected:

```text
聊天窗口展示摘要或阶段结果
工作区生成富内容初稿包
draft.md 包含正文和至少 2 个 Markdown 表格
assets/ 包含至少 1 个 SVG 或图片资产
draft.manifest.json 可打开并记录资产路径
后续输入“套用模板”时，只进入模板选择和渲染，不再生成新图表内容
```

---

## Task 9: Completion Criteria

The implementation is complete only when all of these are true:

- Plain prose plan drafts fail validation before becoming completed expert-team draft outputs.
- Rich plan drafts with two Markdown tables and a figure brief pass validation.
- Draft-stage completion writes `draft.md`, `draft.manifest.json`, and at least one SVG asset.
- Normal plan-like chat prompts are enriched or routed into rich draft generation.
- `docx-template-skill` no longer owns missing table/image generation during template application.
- Template rendering succeeds only when the source package already has the needed tables and image assets.
- Adversarial tests pass for fake table wording, single-table drafts, missing image files, and template-stage prose-only input.
- Existing docx-template skill invocation behavior still keeps template selection mandatory.
- If frontend was touched, the Chinese frontend UX QA report is produced.
- Real Taiji UI smoke is either verified or explicitly marked unverified with reason.

## Self-Review

- Spec coverage: The plan covers content-generation boundary, rich draft file package, expert-team draft stages, normal chat trigger, template-skill responsibility reduction, adversarial tests, and regression gates.
- Placeholder scan: No task depends on unspecified later work; every code change step names concrete files, functions, commands, and expected output.
- Type consistency: The rich draft package consistently uses `draft_path`, `manifest_path`, `table_count`, `figure_count`, and `assets[]`; runtime stores it under `output["rich_draft"]` and `run["artifacts"]`.
- Scope check: This remains one coherent feature because each task builds toward one contract boundary: rich draft generation before template application.
