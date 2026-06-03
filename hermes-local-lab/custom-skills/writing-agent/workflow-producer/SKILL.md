---
name: workflow-producer
description: Use when the user wants a staged Chinese writing workflow, /writeflow actions, article drafting, topic ideation, style-guided writing, editor review, or a Writer Room style multi-agent process.
version: 0.6.0-hermes.1
author: xue1127/writing-agent, ported for Hermes Local Lab
license: MIT
metadata:
  hermes:
    tags: [writing, workflow, articles, subagents, webui, writeflow]
    related_skills: [style-modeler, web-article-extractor, humanizer]
    source_repo: https://github.com/xue1127/writing-agent
    source_commit: a296f1cdfb88887b336f8dd2776257c18acab99d
---

# Writing Workflow Producer for Hermes

## Overview

This is the Hermes-native port of `xue1127/writing-agent`'s workflow director.
It turns writing into a staged, file-backed process. It must not depend on
Claude Code project-level `.claude/agents`; in Hermes, load the stage prompts
from this skill's `references/agents/*.md` files and run isolated work with
`delegate_task`.

All user-visible workflow artifacts are written under the active WebUI
workspace:

```text
articles/[project-slug]/
articles/.writeflow/state.json
articles/.writeflow/runs/[run_id].json
articles/_styles/
```

## Entry Rules

When a user starts a new article or invokes `/writeflow start`, first ask them
to choose the workflow mode unless the WebUI compose message already includes a
mode:

```text
请选择写作工作流模式：

A. 轻量模式：需求澄清 -> 写作 -> 可选审稿
B. 协作模式：完整 10 阶段深度创作
C. 从选题开始：选题生成 -> 选题验证 -> 进入协作模式
```

Do not write the article directly before the mode and required inputs are clear.
For `/writeflow next`, `/writeflow redo`, `/writeflow skip`, or `/writeflow export`,
read `articles/.writeflow/state.json` first and continue from the recorded
project and stage.

## Hermes Subagent Protocol

The upstream project used named Claude Code subagents. In Hermes, emulate that
with this exact pattern:

1. Read the relevant stage prompt from `references/agents/<agent-name>.md`.
2. Call `delegate_task` with:
   - `goal`: concise stage objective plus project name.
   - `context`: the stage prompt, current state JSON, relevant prior artifact
     paths, output path, and acceptance criteria.
   - `toolsets`: `["file", "web", "terminal"]` for research-heavy stages,
     `["file", "terminal"]` for file-only writing and review stages.
3. The child must write the required artifact to `articles/[project-slug]/...`.
4. **Verify the output.** Subagent summaries are self-reports, not verified
   facts. After `delegate_task` returns, immediately verify every artifact file
   the subagent claims to have written with `search_files` or `read_file`. If
   files are missing, reconstruct them from the subagent's summary — do not
   rely on the claim and move on, or the next stage will break on missing
   inputs.
5. The parent summarizes the result, updates `articles/.writeflow/state.json`,
   and asks the user whether to continue, adjust, skip, or stop.

Never assume `delegate_task` knows the named subagent automatically. The stage
prompt must be passed in the `context`.

## Stage Map

| Stage | Agent prompt | Output |
| --- | --- | --- |
| 0a | `topic-generator.md` | candidate topics in chat and optional `00_topics.md` |
| 0b | `topic-research.md` | `00_topic_research.md` |
| 1 | `writing-clarifier.md` | `01_theme.md` |
| 2 | `research-expert.md` | `02_cases.md` |
| 3 | `outline-architect.md` | `03_outline.md` |
| 4 | `empathy-designer.md` | `04_empathy_map.md` |
| 5 | `concretizer.md` | `05_concrete_library.md` |
| 5.5 | `title-designer.md` | title candidates and confirmed title |
| 6 | `writing-executor.md` | `draft_v1.md` |
| 7 | `editor-review.md` | `review_report.md` and revised draft when requested |
| 8 | `pre-publish-review.md` | `pre_publish_report.md` |
| 9 | `toutiao-reader-test.md` | `reader_test.md` |
| 10 | `humanizer.md` | `draft_final.md` |

Mode A runs stages 1, 6, and optionally 7 or 10. Mode B runs stages 1-10.
Mode C runs 0a and 0b, then continues as Mode B after the user selects a topic.

## State Contract

Maintain `articles/.writeflow/state.json` with this minimum shape:

```json
{
  "version": 1,
  "active_project": "project-slug",
  "projects": {
    "project-slug": {
      "name": "Human readable project name",
      "mode": "A",
      "stage": "1",
      "status": "waiting_user",
      "artifacts": {
        "01_theme": "articles/project-slug/01_theme.md"
      },
      "updated_at": "2026-05-30T12:00:00+08:00"
    }
  }
}
```

Use JSON parsing and rewriting; do not append ad hoc text. If the state file is
missing, create it. If it is corrupt, report the problem and ask whether to
repair by recreating state from files under `articles/`.

For `updated_at`, always write the actual current local timestamp in ISO 8601
format with timezone. Never copy the example timestamp from this document.

## Team Run Contract

When the WebUI compose message includes `团队运行 ID` / `run_id`, also maintain
`articles/.writeflow/runs/[run_id].json`. This file is the WebUI source of truth
for the expert-team run view and must be rewritten as valid JSON after every
meaningful phase change.

The top-level fields are fixed:

```json
{
  "run_id": "wr-...",
  "team_id": "content-creator-team",
  "session_id": "session-id",
  "project_slug": "project-slug",
  "title": "Human readable title",
  "status": "running",
  "phase": "确定方向",
  "tasks": [],
  "members": [],
  "artifacts": [],
  "file_changes": [],
  "events": [],
  "created_at": "2026-05-30T12:00:00+08:00",
  "updated_at": "2026-05-30T12:00:00+08:00",
  "error_message": "",
  "next_actions": ["继续", "调整要求", "导出"]
}
```

Use only these status values unless the WebUI compose message explicitly extends
the contract: `running`, `waiting_user`, `done`, `error`, `blocked`. Keep
`status_label` as optional display metadata; the canonical status is `status`.

For the first WorkBuddy-style release, always expose exactly two business tasks
in `tasks[]`:

1. `draft` / `撰写公众号长文`: title candidates, body, illustration suggestions,
   and publishing suggestions.
2. `illustrations` / `生成封面和文中配图`: cover and in-article images; if image
   generation is unavailable, reusable prompts instead of fake image artifacts.

Each task should include at least `id`, `title`, `worker_id`, `worker_name`,
`status`, `status_label`, `description`, and `artifacts`. `members[]` should
reflect the real current status of the workflow director, copywriter, image
worker, and reviewer instead of staying as static team metadata.

`artifacts[]` and `file_changes[]` must use workspace-relative paths such as
`articles/project-slug/draft_final.md`. Never record absolute paths in these
two arrays. If a step fails, set `status` to `error` or `blocked`, fill
`error_message`, append a failure event, and provide concrete `next_actions`
such as `继续`, `重试配图`, `跳过配图`, `导出当前产物`.

## Artifact Rules

- Keep every artifact in Markdown.
- Use stable relative paths so the WebUI workspace preview can open them.
- Include source links for researched data and mark uncertain claims.
- Do not invent facts, quotes, statistics, names, or citations.
- If web access fails, write a `缺口 / 待核验` section instead of fabricating.
- After every stage, return a short progress report with artifact paths and the
  next valid actions.
- For the fixed `内容创作专家团` long-form article flow, the draft stage must
  produce a Markdown artifact containing title options, full body, illustration
  suggestions, and publishing suggestions.
- For illustration work, prefer `baoyu-article-illustrator` plus the available
  `image_generate` capability only when the skill/tool and image provider are
  actually available. If they are missing or fail, write
  `articles/[project-slug]/illustration_prompts.md` with cover and in-article
  prompts, record it as a prompt artifact, and do not mark the illustration task
  as image-complete.

## WebUI Action Handling

The WebUI may send structured lines such as:

```text
Writeflow action: next
Project: project-slug
Mode: B
Stage: 3
Run ID: wr-...
User prompt: ...
```

Treat those lines as control metadata. Still follow this skill's workflow. For:

- `start`: initialize or resume project state, then ask mode if missing.
- `status`: read state and summarize current project and artifact paths.
- `next`: run the next valid stage.
- `redo`: rerun the requested stage and update affected artifacts.
- `skip`: mark the requested stage skipped only if the next stage has enough
  inputs; otherwise explain what is missing.
- `export`: assemble final draft, version notes, and source list into
  `articles/[project-slug]/export.md`.

For every action, if a run file is named in the compose message, update both
`state.json` and the run JSON before replying. If the run file is missing,
recreate it from current project state and explicitly mention that recovery in
the reply.

## Simplified 3-Step Workflow (轻量三步法)

When the user explicitly requests a simplified workflow with only 3 user-facing
steps (e.g., "确定方向 → 生成初稿 → 打磨发布"), override the standard mode
selection dialog. Do NOT present the A/B/C mode choice — the user has already
decided.

### User-Facing Steps Only

The only steps shown to the user:
1. 确定方向: confirm topic, reader, goal, material boundaries
2. 生成初稿: produce the draft body and necessary structure
3. 打磨发布: review, polish, final draft, export

The internal 10-stage pipeline may still run underneath, but the user sees only
these three milestones.

### Communication Rules (强制)

When operating in this mode, all user-facing output MUST follow these rules:

- Use business language only: "主编正在定方向", "撰稿专家正在写初稿", "审稿专家正在做发布前检查" — never expose internal tool names, file paths, system architecture, or technical jargon.
- If information is insufficient, ask the minimum number of clarification questions. Do not fabricate material to fill gaps.
- When the user's explicit requirements conflict with template examples, the user's requirements take priority.

### Per-Round Output Format (强制)

Every round must end with this exact structure in Chinese:

1. 本轮结论 — what was decided or produced this round
2. 已产出的成果物类型 — artifact types produced (not file paths)
3. 当前三步位置 — which of the 3 steps we are in
4. 下一步可选操作 — concrete next actions for the user to choose from

### Content Creation Expert Team (内容创作专家团)

When the user invokes this team by name ("深度文章研究团" or similar), use these
five roles internally:

- 研究总导演 (research director): define research questions, material scope, article goals first — avoid dumping materials upfront
- 资料研究员 (case researcher): supplement facts, cases, evidence with traceable sources
- 结构架构师 (structure architect): organize materials into a clear outline and writing sequence, then advance to drafting
- 撰稿专家 (writer/drafter): produce the first-draft body from the research framework and outline; output title candidates, full draft, illustration suggestions, and publishing suggestions
- 审稿专家 (review editor): fact-check, logic-check, expression polish, and pre-publication risk assessment; produce a review report

The first three roles serve the 确定方向 phase; the fourth serves 生成初稿; the fifth serves 打磨发布.

### Research Framework Template (确定方向 Phase Deliverables)

When the user says "请先列出研究问题、资料范围、案例方向和文章大纲，不要直接写全文",
the 研究总导演 must produce these four items before any drafting:

1. **研究问题** — the core questions the article must answer (typically 4-6 questions), phrased as answerable inquiries with scope boundaries
2. **资料范围** — what sources, policies, standards, data, and industry players are in scope AND out of scope ("边界说明"); organized by layer (政策层/标准层/市场层/技术层/产业层)
3. **案例方向** — 3-5 case angles, each with: what it illustrates, which dimension it covers, the evidentiary value; apply the case selection criteria (publicly reported, diverse, quantified results preferred)
4. **文章大纲** — 5-7 part structure with one-line descriptions per section; include a title direction (a through-line or framing angle) but defer specific title candidates to the draft phase

Present the framework as a confirmation gate — do not proceed to drafting until the user says "确认".

### Direction Override Handling

When the user corrects the article direction after the framework is presented
(e.g., "更偏向营销策略，重点以配变终端在国家电网销售推广为主"), do NOT defend
the original framing. Immediately restructure — reorder research questions and
outline sections around the new emphasis, demoting the original focus to a
supporting role. Present the restructured framework for re-confirmation.

### Handling Firm User Parameters

When the user sets a parameter and explicitly says "不许调整" (do not adjust),
accept it without question. Do not suggest alternatives, adjustments, or
compromises for that parameter. This applies to: audience, word count, format,
style, naming, or any other specification the user locks down.

### Case Selection Criteria Framework

When researching enterprise/technology case studies for articles, apply these
filters:

1. Must be publicly reported real enterprise deployments (no concept demos, no PPT products)
2. Cover all target dimensions with at least one case each
3. Enterprise diversity: different sizes/industries, not all the same type
4. Prioritize cases with quantified results or outcome data

Always record source links for researched data. If web access fails, write a
`缺口 / 待核验` section instead of fabricating.

## Document Output Formats

When the user requests output in a specific format (e.g., Word .docx, corporate
template), produce the document in addition to — not instead of — the standard
Markdown artifact. The Markdown artifact remains the canonical workflow artifact;
the formatted document is the user-facing deliverable.

### Word Document Output (国网/央企/政府风格)

When the user requests 国网 or similar state-enterprise document formatting:

- Body font: 仿宋, 12pt, with 0.74cm first-line indent
- Heading font: 黑体, bold; H1=22pt, H2=16pt, H3=14pt
- Color scheme: dark green (#1B5E20 for main, #2E7D32 for sub, #388E3C for detail)
- Margins: top/bottom 2.54cm, left/right 3.17cm (standard Chinese official document)
- Title page with centered title, subtitle, date — no logo image needed in first draft
- Use python-docx library; see `references/guowang-docx-format.md` for full font/color/layout specification and implementation notes
- Before publishing, run the audit checklist in `references/guowang-audit-patterns.md` to catch domain-specific risks (policy citation gaps, enterprise ranking liability, bid evaluation data sourcing, language sensitivity)

### General Corporate Document Principles

- The document is a deliverable, not a working file — format it for reading, not editing
- Include title candidates, illustration suggestions, and publishing suggestions as appendices or separate sections at the end
- Do not expose internal tool names, file paths, or workflow metadata in the document
- Use the client's corporate color palette, not generic defaults

## Common Pitfalls

1. **Using Claude Code subagent names as if Hermes loads them.** Hermes does
   not. Load the prompt file and pass it through `delegate_task`.
2. **Skipping user confirmations.** The source workflow is deliberately staged.
   Preserve the mode, title, outline, and final humanizer confirmation points.
3. **Losing state in chat history.** The state file is the source of truth.
4. **Writing outside the workspace.** All artifacts belong under `articles/`.
5. **Overwriting user drafts silently.** For redo, keep the previous file or
   include a revision note before replacing content.
6. **Trusting subagent write_file results without verification.** `delegate_task`
   subagents may report successful `write_file` calls in their summary even when
   files did not actually persist to disk. Always verify output files exist
   with `search_files` or `read_file` before updating state or proceeding to the
   next stage. If files are missing, reconstruct them from the subagent summary
   using `write_file` or `execute_code` directly from the parent agent.

7. **Chinese fonts in python-docx require dual font registration.** When setting a Chinese font (仿宋, 黑体, 楷体), you MUST set both `run.font.name` (western) and `run._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)` (east-Asian). Without the eastAsia attribute, the font only applies to Latin characters and Chinese text renders in the default font.

8. **Subagent fact attribution errors when merging multi-source data.** When a
   writing executor or research subagent draws from multiple cases that share a
   common context (e.g. two different enterprises both using 钉钉 AI 知识库),
   it may incorrectly merge their data points into a single entity. Common
   pattern: "企业A的指标X + 企业B的指标Y → 写成同一家企业的效果"。The editor
   review stage must cross-check every attributed data point against its source
   in the research report — do not assume the writer preserved correct
   attribution. This is especially high-risk when multiple cases share the same
   platform or vendor name.

9. **Subagent timeout during review stages.** When a `delegate_task` subagent
   running the editor review or pre-publish review stage times out (common with
   slow web search or large context), check if it partially wrote an artifact
   before timing out with `search_files(target='files')`. If a partial file
   exists, read it to salvage findings, then either re-run the subagent with a
   tighter scope or complete the review inline. Do not declare the stage
   successful until the full review report artifact exists on disk.

10. **Reviewing 国网/央企-style articles requires domain-specific audit
    patterns.** Beyond general fact-checking and logic review, articles about
    State Grid or similar central-enterprise procurement and policy carry unique
    risks: enterprise ranking liability, policy document citation completeness,
    bid evaluation weight data sourcing, and "关系营销" language sensitivity.
    See `references/guowang-audit-patterns.md` for the full checklist.

## Verification Checklist

- [ ] `articles/.writeflow/state.json` exists and parses.
- [ ] If `run_id` is present, `articles/.writeflow/runs/[run_id].json` exists
      and parses.
- [ ] The active project has mode, stage, status, artifacts, and updated_at.
- [ ] The run file has the fixed top-level fields, two business tasks, member
      statuses, artifact rows, file change rows, and current events.
- [ ] After every `delegate_task` call, verify all claimed artifact files exist
      on disk with `search_files` or `read_file` before updating state.
- [ ] Image fallback is honest: prompt artifacts are marked as prompts and image
      tasks are not marked complete unless image files actually exist.
- [ ] The chat response names the next available action.
- [ ] Research claims either cite sources or are marked for verification.
