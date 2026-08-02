# Expert Team All Content Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让内容创作专家团的六个任务全部真实可启动、可确认、可生成和可交付，并保证任务弹窗底部主按钮在目标窗口高度内始终可见。

**Architecture:** 服务端继续以 document capability 和 launch profile 作为可用性的唯一真相源，新增五个任务的独立 Brief、来源与章节合同；内容阶段编排保持共享。DOCX 交付使用新建的 `standalone-meeting-minutes` 与 `standalone-office-material` 安全模板，避免旧模板注入固定企业内容；前端只消费 catalog 结果并把弹窗改成固定头尾、中部滚动的三行布局。

**Tech Stack:** Python 3、pytest、原生 JavaScript/CSS、Node.js `node:test`、DOCX Engine v2、Electron/Playwright smoke。

---

## 文件结构

- `hermes-local-lab/sources/hermes-webui/api/expert_teams/document_capabilities.py`：五类文种的 Brief、来源与 `required_sections` 真相源。
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/launch_profiles.py`：把五类文种绑定内容创作专家团、阶段编排和模板。
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/contracts.py`：能力驱动的通用字段/来源校验与润色约束。
- `hermes-local-lab/sources/hermes-webui/api/expert_teams/catalog.py`：保留任务展示元数据，由 profile 自动推导可用性。
- `hermes-local-lab/sources/hermes-webui/static/expert-team-v3.css`：三行弹窗与中部滚动。
- `hermes-local-lab/sources/hermes-webui/static/expert-team-v3.js`：保留可访问任务选择与字段级错误聚焦。
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-meeting-minutes/`：会议纪要单机安全模板包。
- `hermes-local-lab/sources/docx-engine-v2/templates/standalone-office-material/`：通知、方案、总结和润色共享的单机安全模板包。
- `hermes-local-lab/sources/docx-engine-v2/tools/build-standalone-templates.py`：确定性生成两个新模板的 DOCX 二进制。
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_capability_registry.py`：能力注册表与 fail-closed 测试。
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_brief_capabilities.py`：Brief schema、来源和启动配置测试。
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_contract.py`：catalog 六任务可用性和启动合同。
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_required_sections_contract.py`：五类章节全链路测试。
- `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_visual_assets.py`：按钮可见与六任务可访问 DOM/CSS 合同。
- `hermes-local-lab/sources/hermes-webui/tests/expert_team_v3_electron_smoke.js`：真实视口可见性和键盘触达。
- `hermes-local-lab/sources/docx-engine-v2/tests/template-data-adapter.test.js`：新文种模板适配。
- `hermes-local-lab/sources/docx-engine-v2/tests/standalone-template-contract.test.js`：两类模板的渲染与结构验收。

### Task 1: 用失败测试锁定五类 capability 与 launch profile

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_capability_registry.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_brief_capabilities.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_templates.py`

- [ ] **Step 1: 写 capability 参数化失败测试**

```python
@pytest.mark.parametrize(
    ("document_type", "task_mode", "capability_id", "template_id", "sections", "minimum_ready"),
    [
        ("meeting_minutes", "create", "content-meeting-minutes", "standalone-meeting-minutes", ["会议基本情况", "议定事项", "责任分工", "后续跟踪"], 0),
        ("notice", "create", "content-notice", "standalone-office-material", ["背景与总体要求", "通知事项", "时间安排", "责任分工", "报送要求"], 0),
        ("plan", "create", "content-plan", "standalone-office-material", ["目标", "现状与问题", "主要措施", "进度安排", "保障机制"], 0),
        ("summary_plan", "create", "content-summary-plan", "standalone-office-material", ["阶段性工作总结", "成效与亮点", "问题与不足", "下一步工作计划"], 0),
        ("other_office_material", "polish", "content-polish", "standalone-office-material", ["润色后正文", "修改说明"], 1),
    ],
)
def test_all_content_capabilities_are_released(...):
    capability = resolve_document_capability(document_type, task_mode, product_mode="standalone")
    assert capability["capability_id"] == capability_id
    assert capability["render_template_id"] == template_id
    assert capability["standalone_defaults"]["content_constraints"]["required_sections"] == sections
    assert capability["source_requirement"]["minimum_ready"] == minimum_ready
```

- [ ] **Step 2: 写 profile 绑定与顺序失败测试**

```python
def test_content_launch_profiles_cover_every_catalog_task():
    profiles = list_launch_profiles()
    assert [item["id"] for item in profiles[:6]] == [
        "content-work-report", "content-meeting-minutes", "content-notice",
        "content-plan", "content-summary-plan", "content-polish",
    ]
    for profile in profiles[:6]:
        assert profile["team_id"] == "content-creator-team"
        assert profile["stages"] == CONTENT_PHASES
```

- [ ] **Step 3: 运行测试并确认因能力缺失失败**

Run from `hermes-local-lab/sources/hermes-webui`:

```bash
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_capability_registry.py \
  tests/test_expert_team_brief_capabilities.py \
  tests/test_expert_team_standalone_templates.py
```

Expected: FAIL，错误指向新增 capability/profile 不存在，而非测试环境错误。

### Task 2: 实现五类能力、Brief 与来源合同

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/document_capabilities.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/launch_profiles.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/contracts.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_brief_capabilities.py`

- [ ] **Step 1: 在 `_CAPABILITIES` 增加五个完整声明**

每项使用同一结构，实际字段精确采用规格表。例如材料润色：

```python
"content-polish": {
    "capability_id": "content-polish",
    "document_type": "other_office_material",
    "task_mode": "polish",
    "releases": {"standalone": {"released": True, "render_template_id": "standalone-office-material"}},
    "brief_schema": (
        *_COMMON_FIELDS,
        {"path": "details.polish_goal", "label": "润色目标", "control": "textarea", "required": True, ...},
        {"path": "details.expression_boundary", "label": "表达边界", "control": "textarea", "required": True, ...},
    ),
    "standalone_defaults": {
        "source_policy": {"mode": "provided_only", "citation_style": "none", "unknown_fact_action": "block_final", "source_refs": []},
        "data_handling": {}, "document_control": {},
        "content_constraints": {"required_sections": ["润色后正文", "修改说明"], "must_include": [], "must_avoid": []},
        "details": {"polish_goal": "", "expression_boundary": ""}, "approval": {},
    },
    "source_requirement": {"minimum_ready": 1, "empty_help": "请先添加需要润色的原始材料。"},
},
```

- [ ] **Step 2: 增加五个 launch profile 并更新稳定顺序**

```python
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
```

其余 profile 使用各自 capability、example id 和模板；`_LAUNCH_PROFILE_ORDER` 将六个内容任务置于研究报告之前。

- [ ] **Step 3: 让 Brief 校验完全由 schema/source requirement 驱动**

```python
for field in capability.get("brief_schema") or []:
    if field.get("required") and not _value_at_path(normalized, field["path"]):
        errors.append(_error(field["path"], "required", f"请填写{field['label']}"))

minimum_ready = int((capability.get("source_requirement") or {}).get("minimum_ready", 0))
if len(normalized["source_policy"]["source_refs"]) < minimum_ready:
    errors.append(_error("source_policy.source_refs", "source_required", empty_help))
```

保留现有工作汇报/研究报告专属语义检查，只移除与通用 schema/source 重复的分支。

- [ ] **Step 4: 运行聚焦测试**

```bash
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_capability_registry.py \
  tests/test_expert_team_brief_capabilities.py \
  tests/test_expert_team_document_brief_contract.py \
  tests/test_expert_team_zero_source_contract.py \
  tests/test_expert_team_standalone_templates.py
```

Expected: PASS。

- [ ] **Step 5: 提交能力合同**

```bash
git add hermes-local-lab/sources/hermes-webui/api/expert_teams/document_capabilities.py \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/launch_profiles.py \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/contracts.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_capability_registry.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_brief_capabilities.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_templates.py
git commit -m "feat(expert-teams): release content task contracts"
```

### Task 3: 开放 catalog 并验证启动与 required_sections 全链路

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_contract.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_required_sections_contract.py`
- Modify only if tests expose a gap: `hermes-local-lab/sources/hermes-webui/api/expert_teams/catalog.py`
- Modify only if tests expose a gap: `hermes-local-lab/sources/hermes-webui/api/expert_teams/prompts.py`
- Modify only if tests expose a gap: `hermes-local-lab/sources/hermes-webui/api/expert_teams/stage_artifacts.py`
- Modify only if tests expose a gap: `hermes-local-lab/sources/hermes-webui/api/expert_teams/documents.py`

- [ ] **Step 1: 把旧的不可用断言改成六任务可用断言**

```python
def test_content_catalog_releases_all_six_tasks():
    content = next(team for team in expert_team_catalog()["teams"] if team["id"] == "content-creator-team")
    assert [item["id"] for item in content["examples"]] == [
        "work_report", "meeting_minutes", "notice", "plan", "summary_plan", "polish",
    ]
    assert all(item["available"] is True for item in content["examples"])
    assert all(item.get("launch_profile_id") for item in content["examples"])
    assert all("disabled_reason" not in item for item in content["examples"])
```

- [ ] **Step 2: 参数化五类 Brief 的章节传播测试**

对每个 profile 断言：catalog seed → build Brief → confirm Brief → stage prompt JSON → canonical artifact → delivery manifest 的章节列表精确等于 capability 默认值；删除任一章节必须得到 `missing_required_section`。

- [ ] **Step 3: 运行失败测试并仅修暴露出的共享链路缺口**

```bash
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_standalone_contract.py \
  tests/test_expert_team_required_sections_contract.py
```

Expected before fix: 若既有链路存在 work-report 硬编码则 FAIL；修复后 PASS。不得为每种文种复制一条新流程。

- [ ] **Step 4: 运行启动/恢复回归**

```bash
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_start_atomicity.py \
  tests/test_expert_team_standalone_state_machine.py \
  tests/test_expert_team_recovery_protocol.py \
  tests/test_expert_team_delivery_validation_gate.py
```

Expected: PASS。

- [ ] **Step 5: 提交 catalog 与链路合同**

```bash
git add hermes-local-lab/sources/hermes-webui/api/expert_teams \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_contract.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_required_sections_contract.py
git commit -m "feat(expert-teams): launch all content document tasks"
```

### Task 4: 修复弹窗主按钮可见性与可访问性

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_visual_assets.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v3.py`
- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-v3.css`
- Modify if field-error focus is absent: `hermes-local-lab/sources/hermes-webui/static/expert-team-v3.js`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/expert_team_v3_electron_smoke.js`

- [ ] **Step 1: 写固定头尾和六任务可选失败测试**

```python
assert "grid-template-rows: auto minmax(0, 1fr) auto" in style
assert ".et3-dialog-body" in style and "overflow-y: auto" in style
assert ".et3-dialog-actions" in style and "background:" in style
assert "暂未开放" not in rendered_available_catalog_html
assert 'data-et3-action="summon"' in rendered_available_catalog_html
```

- [ ] **Step 2: 最小 CSS 实现 A 方案**

```css
.et3-dialog {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  max-height: min(820px, calc(100vh - 64px));
  overflow: hidden;
}
.et3-dialog-body { min-height: 0; overflow-y: auto; }
.et3-dialog-actions {
  padding: 14px 24px;
  border-top: 1px solid #e6f0f6;
  background: #f9fcff;
}
```

移动端媒体查询继续使用页面自然滚动，桌面/平板断点使用固定操作区。

- [ ] **Step 3: Electron smoke 增加几何断言**

```javascript
const layout = await page.locator('[data-et3-dialog]').evaluate((dialog) => {
  const actions = dialog.querySelector('.et3-dialog-actions').getBoundingClientRect();
  const viewport = { width: innerWidth, height: innerHeight };
  return { actionsBottom: actions.bottom, actionsTop: actions.top, viewport };
});
assert(layout.actionsBottom <= layout.viewport.height, 'Summon action is outside viewport');
assert(layout.actionsTop >= 0, 'Summon action is clipped above viewport');
```

- [ ] **Step 4: 运行静态与浏览器合同测试**

```bash
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_visual_assets.py \
  tests/test_expert_team_frontend_v3.py
```

Expected: PASS。

- [ ] **Step 5: 提交前端修复**

```bash
git add hermes-local-lab/sources/hermes-webui/static/expert-team-v3.css \
  hermes-local-lab/sources/hermes-webui/static/expert-team-v3.js \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_visual_assets.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v3.py \
  hermes-local-lab/sources/hermes-webui/tests/expert_team_v3_electron_smoke.js
git commit -m "fix(expert-teams): keep summon action visible"
```

### Task 5: 新建并验证两类 standalone 安全 DOCX 模板

**Files:**
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/standalone-meeting-minutes/`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/standalone-office-material/`
- Modify: `hermes-local-lab/sources/docx-engine-v2/template-registry.json`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tools/build-standalone-templates.py`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/template-data-adapter.test.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/standalone-template-contract.test.js`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/document_capabilities.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/launch_profiles.py`

- [ ] **Step 1: 参数化模板适配与渲染测试**

```javascript
const contentCases = [
  ['meeting_minutes', 'standalone-meeting-minutes'],
  ['notice', 'standalone-office-material'],
  ['plan', 'standalone-office-material'],
  ['summary_plan', 'standalone-office-material'],
  ['other_office_material', 'standalone-office-material'],
];
for (const [documentType, templateId] of contentCases) {
  test(`${documentType} renders with ${templateId}`, async () => {
    const result = await renderValidatedSample({ documentType, templateId });
    assert.equal(result.validation.ok, true);
    assert.ok(result.docxBytes.length > 0);
  });
}
```

- [ ] **Step 2: 运行测试确认实际兼容边界**

Run from `hermes-local-lab/sources/docx-engine-v2`:

```bash
node --test tests/template-data-adapter.test.js tests/standalone-template-contract.test.js
```

Expected before implementation: FAIL，错误指向模板未登记或模板包不存在。实现后 PASS，并断言适配数据与 DOCX XML 不包含“客户单位、北京太极、内部资料”及固定日期。

- [ ] **Step 3: 运行 WebUI 真实引擎交付测试**

Run from `hermes-local-lab/sources/hermes-webui`:

```bash
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_delivery_contract.py \
  tests/test_expert_team_standalone_delivery_contract.py
```

Expected: PASS，产物是可解包的 DOCX 且必备章节存在。

- [ ] **Step 4: 提交必要的适配与测试**

```bash
git add hermes-local-lab/sources/docx-engine-v2 \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/documents.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_delivery_contract.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_standalone_delivery_contract.py
git commit -m "test(expert-teams): verify content task docx delivery"
```

### Task 6: 全量回归、真实桌面 UX QA 与收口

**Files:**
- Modify: `docs/ui-ux/expert-team-v3/` 下当轮 QA 证据文档（沿用现有命名模式）
- Modify: `docs/superpowers/plans/2026-07-28-expert-team-all-content-tasks.md`（勾选实际完成项）

- [ ] **Step 1: 运行专家团 Python 回归**

Run from `hermes-local-lab/sources/hermes-webui`:

```bash
../hermes-agent/venv/bin/python -m pytest -q tests/test_expert_team_*.py
```

Expected: 全部 PASS；任何既有失败需先判定是否由本分支引入，不能直接忽略。

- [ ] **Step 2: 运行 DOCX Engine 回归**

Run from `hermes-local-lab/sources/docx-engine-v2`:

```bash
npm test
```

Expected: PASS。

- [ ] **Step 3: 仅重启当前 worktree Electron 实例**

先用进程命令行、cwd、启动日志和源码 commit 精确识别当前 worktree 实例；只停止并重启路径包含 `.worktrees/expert-team-standalone-core` 且 runtime/user-data 属于本实例的进程。不得按端口或模糊进程名清理其它任务。

- [ ] **Step 4: 真实桌面验证六任务入口和按钮布局**

在用户截图等价尺寸和较小尺寸逐项检查：六任务可选择、主按钮可见、键盘可达、必填错误聚焦、材料润色缺原文可行动提示。

- [ ] **Step 5: 验证五类真实业务流程**

在 Provider 授权可用时，每类完成：发起 → Brief 确认 → 阶段生成/确认 → DOCX 交付 → 打开产物核对章节。若授权或模型能力阻断，记录具体停止点并标记“未验证”，不得改写为通过。

- [ ] **Step 6: 输出中文《前端 UX QA 报告》并提交**

报告必须包含：执行摘要、范围、环境证据、需求追踪矩阵、关键流程、发现、无障碍/响应式、浏览器/桌面矩阵、视觉证据、未验证项、结论。随后：

```bash
git add docs/ui-ux/expert-team-v3 docs/superpowers/plans/2026-07-28-expert-team-all-content-tasks.md
git commit -m "docs(expert-teams): record all content task QA"
```

## 自检结果

- 规格覆盖：弹窗 P0、五任务 capability/profile、Brief 字段、来源规则、`required_sections`、DOCX 模板、恢复兼容、真实桌面 QA 均有对应任务。
- 占位扫描：已逐段检查，没有未落实的代码或验证步骤；条件修改均要求先由失败测试证明缺口。
- 类型一致性：`document_type`、`task_mode`、capability/profile id、模板 id 与设计规格一致；catalog 继续以 `intake_example_id` 绑定 profile。
- 执行方式：用户明确禁止子 Agent，因此使用 `executing-plans` 在当前会话内联执行。
