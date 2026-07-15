# 专家团 DocumentBrief 企业输入合同实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task by task. Any implementation, debugging, or review must also use `andrej-karpathy-skill`; frontend work must use `frontend-ux-qa`.

**Goal:** 为两个专家团建立版本化、可确认、可追溯的 `DocumentBrief`，让业务文种、精确标题、资料边界和交付控制成为后续提示词、阶段产物与 DOCX 的唯一权威输入。

**Architecture:** 保留专家团 run 的 `schema_version: 2`、现有锁、幂等、Journal 和结果恢复机制，在其上增加 `contract_version: "expert-team-contract/v1"` 覆盖层。新 run 使用严格合同；无 `contract_version` 的现行 v2 run 继续按原路径兼容和收尾，不回填无法可靠推断的企业字段。

**Tech Stack:** Python 3、自定义 HTTP route handler、JSON 文件持久化、现有专家团 runtime/view/API、原生 JavaScript、pytest。

---

## 1. 决策边界

### 1.1 本计划解决什么

当前入口把三个不同概念混在同一个 `template_id` 或自然语言里：

- `intake_example_id`：用户点击的示例，只用于预填和来源追踪；
- `document_type`：企业业务文种，例如 `work_report`、`research_report`；
- `render_template_id`：DOCX 引擎使用的版式模板，例如 `enterprise-work-report`。

B 路线首先拆开三者。模型不得再通过关键词从整段诉求猜业务文种，也不得从正文首个 H1 反推封面标题。

### 1.2 首批目标放行范围

| 专家团 | 首批企业目标文种 | 交付模板 | 状态 |
|---|---|---|---|
| 内容创作专家团 | `work_report` | `enterprise-work-report` | 四份计划、真实模型门和 WPS 终验通过后进入受控试点 |
| 深度材料研究团 | `research_report` | `enterprise-research-report` | 四份计划、真实资料链、真实模型门和 WPS 终验通过后进入受控试点 |
| 内容创作专家团 | 会议纪要、通知通报、方案说明、总结计划、材料润色 | 各自专用模板 | 合同和模板逐项完成后放行 |

上述两个 enterprise 模板当前尚不存在，由第三份计划创建；本计划单独完成时不能把任何文种标成企业正式放行。未放行文种可以保留为“AI 草稿能力”，但 UI、API 和验收报告不得宣称其已经达到企业正式交付标准。`public_account` 不属于这两个企业文档专家团。

### 1.3 明确不做

- 不把 run 升级为 schema v3；
- 不强行迁移或猜测历史任务的标题、密级和来源政策；
- 不引入数据库迁移、Pydantic、通用表单 DSL 或外部工作流引擎；
- 不重写现有并发锁、取消、恢复、Journal 和 Office token；
- 不在本计划里实现阶段产物、DOCX 模板和前端完整交互，它们分别由后三份计划负责。

## 2. 权威数据合同

### 2.1 run 新增字段

```json
{
  "schema_version": 2,
  "contract_version": "expert-team-contract/v1",
  "document_brief": {},
  "stage_artifacts": [],
  "canonical_document_ref": null
}
```

`stage_outputs` 原样保留作为模型原始回包和 legacy 审计证据；新合同 run 的业务流只消费已批准的 `stage_artifacts`。

### 2.2 `DocumentBriefV1`

```json
{
  "schema_version": "document-brief/v1",
  "revision": 1,
  "status": "draft",
  "team_id": "content-creator-team",
  "task_mode": "create",
  "original_request": "起草工作汇报，不要写成公众号文章或发布稿",
  "document_type": "work_report",
  "intake_example_id": "work_report",
  "exact_title": "迎峰度夏保供电重点工作月度汇报",
  "purpose": "向公司分管领导汇报本月进展并申请协调事项",
  "audience": "公司分管领导、相关部门负责人",
  "usage_scenario": "月度例会汇报",
  "source_policy": {
    "mode": "provided_only",
    "as_of_date": "2026-07-15",
    "citation_style": "source_id",
    "unknown_fact_action": "block_final",
    "source_refs": [
      {
        "source_id": "SRC-001",
        "kind": "attachment",
        "label": "部门月度数据表",
        "locator": "artifact:example",
        "sha256": null
      }
    ]
  },
  "data_handling": {
    "model_policy_id": "enterprise-local-default",
    "requires_zero_retention": true
  },
  "document_control": {
    "client": null,
    "issuer": "某某部门",
    "compiler": "某某部门",
    "version_label": "V1.0",
    "classification": "internal",
    "classification_label": "内部资料",
    "document_date": "2026-07-15",
    "render_template_id": "enterprise-work-report"
  },
  "content_constraints": {
    "required_sections": ["工作开展情况", "存在问题", "下一步工作安排"],
    "must_include": ["重点指标", "需协调事项"],
    "must_avoid": ["未经确认的供应商名称"],
    "target_length_chars": {"min": 1500, "max": 3000},
    "tone": "正式、克制、面向管理层"
  },
  "details": {
    "reporting_period": "2026年7月",
    "reporting_unit": "某某部门"
  },
  "approval": {
    "human_final_review_required": true,
    "approver_roles": ["部门负责人"]
  },
  "additional_context": "领导特别关注跨部门协调事项",
  "confirmed_revision": null,
  "confirmed_at": null,
  "confirmed_sha256": null
}
```

### 2.3 固定枚举

```text
task_mode: create | polish
document_type:
  work_report | meeting_minutes | notice | plan |
  summary_plan | research_report | other_office_material
source_policy.mode: provided_only | approved_internal | approved_public
source_ref.kind: attachment | local_file | provided_text | approved_internal | approved_public
citation_style: none | source_id | footnote
unknown_fact_action: block_final | allow_labeled_placeholder
classification: public | internal | restricted | custom
brief.status: draft | confirmed
```

`approved_public` 只有在当前 run 确实具备获准联网检索能力时才可确认；否则返回可操作的阻断错误，不能让模型依靠记忆模拟调研。

`original_request` 是创建请求 `prompt` 的可确认、可摘要投影：服务端只做 Unicode NFC、换行统一和首尾空白规范化，逐字保留其余内容并纳入 Brief digest。它表达用户原始意图，但优先级低于显式 typed 字段，不能据此猜测/覆盖 `document_type`、模板、密级、来源政策或必填结构，也不能被当作事实来源。用户在其中写入的事实只有另行固化为 `provided_text` source 后才可进入证据链。`document_brief_seed.original_request` 不作为第二入口；若客户端同时传入且与 `prompt` 规范化值不同，返回 `original_request_conflict`。

`citation_style` 不是所有文种都可任选：`research_report` 确认时只能为 `source_id | footnote`，选择 `none` 必须返回 `citation_style_required`；`work_report` 可按企业场景选择 `none | source_id | footnote`。这条约束必须由 Brief validator 执行，不能只靠研究提示词提醒。

`data_handling.model_policy_id` 只引用服务端可信策略注册表，客户端不能通过提交 provider/base URL 自行放行。策略项至少绑定：允许的 classification、provider/deployment ID、信任域/部署位置、允许的数据类型、retention mode、training opt-out、有效期和审批来源。`restricted/custom` 默认 fail closed；只有 registry 中明确允许该密级的本地或企业受控部署才能确认，且 `requires_zero_retention=true` 必须由 provider capability 实际满足。策略缺失、过期、provider 漂移或信任域不匹配返回 `data_egress_not_authorized`。密级检查必须在 Brief 确认时和每次模型调用前各执行一次，不能等复核正文时才发现外发违规。

registry 归属现有 `config.yaml`，由 `data_egress.py` 唯一解释；不在 Brief/run 复制凭据：

```yaml
expert_team_model_data_policies:
  enterprise-local-default:
    allowed_classifications: [public, internal, restricted]
    provider_ids: [local-enterprise-model]
    deployment_ids: [taiji-onprem-01]
    trust_zones: [local, enterprise_private]
    retention_modes: [zero_retention]
    training_opt_out_required: true
    allowed_source_kinds: [attachment, local_file, provided_text, approved_internal]
    expires_at: 2027-07-15T00:00:00+08:00
    approval_ref: security-policy-2026-01
```

未知字段/枚举、空 allow-list、过期时间或缺 approval ref 使该 policy 无效；`restricted/custom` 不允许通过通配符 provider/trust zone。普通 view 只显示 policy 的安全 label 和有效/阻断状态。

每个本地/附件 `source_ref` 在确认前必须由受信 ingestion 计算并持久化 SHA-256；示例中的 `sha256: null` 仅代表 draft。无法解析或缺 hash 时返回 `source_unresolved`，不得确认。第二份计划在执行前再次核 hash 并提取实际 `source_context`。

### 2.4 条件必填字段

| 文种/模式 | 确认前必须具备 |
|---|---|
| 工作汇报 | `reporting_period`、`reporting_unit`、精确标题、目的、读者、使用场景、至少一个服务端解析为 ready 的来源、有效 `data_handling.model_policy_id` |
| 专题研究报告 | `details.core_question`、`details.time_range.start/end`、通用 `purpose`、`source_policy.as_of_date`、至少一个服务端解析为 ready 的来源、有效 `data_handling.model_policy_id`，且 `citation_style` 为 `source_id` 或 `footnote` |
| 会议纪要 | 会议名称、时间、地点、主持人、参会范围、议定事项来源 |
| 通知通报 | 发文单位、接收对象、执行时间、报送要求 |
| 方案说明 | 实施周期、责任单位、目标和约束 |
| 总结计划 | 总结周期、下一周期 |
| `task_mode=polish` | `details.original_source_id` 必须存在于 `source_policy.source_refs`，并具备 `details.original_document_type`、`details.preserve_facts=true` |

首批实现必须完整校验前两行；其余文种返回 `document_type_not_released`，不能悄悄套用通用方案模板。

contract-v1 首批试点不提供“无资料正式稿”：`work_report/research_report` 均至少需要一个由服务端受信 ingestion 解析并 hash 的 ready source。用户只给自然语言主题但没有可核对资料时，继续使用诚实标注的 legacy/AI 草稿能力，不能设置 `release_candidate=true`。用户在 intake textarea 提供的事实材料可由服务端先固化成不可变 `provided_text` source blob，再作为 opaque source ref 参与确认；不能把未固化聊天文本直接当来源。

## 3. 状态与版本语义

### 3.1 前置流程不计执行进度

```text
创建 run
  → workflow_state=collecting_required, brief=draft
  → workflow_state=collecting_required, view.brief_gate=needs_confirmation（仍显示 0/N）
  → workflow_state=ready_to_generate, brief=confirmed（仍显示 0/N）
  → 用户明确开始
  → 第一执行阶段 1/N
```

`needs_confirmation` 是 view 派生的 Brief gate，不新增 workflow state。需求确认是执行前准备，不能显示成“第 1 阶段”或伪造进度。新合同路径必须移除当前“最后一个问题回答完就自动 reserve 第一阶段”的行为；无 `contract_version` 的现行 v2 run 保持原状。

### 3.2 修改规则

- 首阶段启动前：允许基于 `expected_brief_revision` 更新；每次有效更新将 `revision + 1`，并清空旧确认信息。
- Brief 确认：服务端对规范化 JSON 计算 SHA-256，写入 `confirmed_revision`、`confirmed_at`、`confirmed_sha256`。
- 首阶段启动后：核心字段冻结。修改标题、文种、目的、读者、来源政策、正文约束或模板时，API 返回 `brief_frozen_new_run_required`，前端引导“基于当前规格创建新任务”。
- 首阶段启动后整个 Brief 冻结，包括客户单位、编制单位、版本号、密级和文档日期。任何修改都基于当前 Brief 新建任务，不能在原 run 中改变 hash 语义。
- DOCX 已生成后修改规格同样新建任务，不能覆盖已有文件和 hash。独立“仅重新出一个封面版本”的 delivery revision 留到后续版本设计，不进入首批 MVP。

这套 MVP 规则宁可显式新建任务，也不在首批实现中引入复杂的跨阶段依赖图和静默重算。

### 3.3 并发与幂等

所有变更接口继续使用现有 mutation 约束：

```json
{
  "session_id": "session-id",
  "run_id": "run-id",
  "expected_version": 7,
  "expected_brief_revision": 2,
  "idempotency_key": "uuid"
}
```

- run `version` 防止状态并发覆盖；
- `expected_brief_revision` 防止规格草稿丢失更新；
- 同一幂等键重放返回相同业务结果；
- 409 必须返回权威 run version、brief revision 和可恢复错误码。

## 4. API 契约

### 4.1 创建新合同 run

沿用现有创建入口：`POST /api/expert-teams/start`

```json
{
  "session_id": "session-id",
  "team_id": "content-creator-team",
  "contract_version": "expert-team-contract/v1",
  "intake_example_id": "work_report",
  "document_type": "work_report",
  "prompt": "起草工作汇报，不要写成公众号文章或发布稿",
  "document_brief_seed": {
    "exact_title": "迎峰度夏保供电重点工作月度汇报"
  }
}
```

兼容规则：旧客户端发送的 `template_id` 只可映射为 `intake_example_id`；不得映射为 `document_type` 或 `render_template_id`。新合同请求缺少显式 `document_type` 时直接返回 400。

新合同 start 必须把顶层 `prompt` 确定性写入 `DocumentBrief.original_request`，在 Brief 确认界面以“原始诉求”展示并允许用户在首阶段前修正；确认后它与其他 Brief 字段一并冻结。后续阶段只读 confirmed Brief，不再回看 start payload 或聊天历史。缺失/空 `prompt` 返回 `original_request_required`；不得把 prompt 默默塞进 `additional_context`，也不得从否定句重新分类文种。

### 4.2 更新草稿

沿用现有扁平 mutation 风格：`POST /api/expert-teams/brief/update`，`run_id` 放在请求体中。

请求只接受白名单字段 patch。服务端返回完整规范化 Brief、缺失项和影响范围：

```json
{
  "document_brief": {},
  "validation": {
    "valid_for_confirmation": false,
    "field_errors": [
      {"field": "details.reporting_period", "code": "required", "message": "请填写汇报周期"}
    ]
  },
  "impact": {"requires_new_run": false, "invalidated_stage_ids": []}
}
```

### 4.3 确认 Brief

`POST /api/expert-teams/brief/confirm`，`run_id` 放在请求体中。

确认成功只把 run 推到 `ready_to_generate`，不自动消耗模型额度。用户随后点击右侧工作台的“开始生成”，前端复用现有 `POST /api/expert-teams/resume` 启动入口；对用户显示“开始生成”，不显示内部 action 名 `resume`。

### 4.4 View 模型

`expert_team_run_view()` 至少暴露：

```json
{
  "contract_version": "expert-team-contract/v1",
  "brief": {
    "revision": 3,
    "status": "confirmed",
    "original_request": "起草工作汇报，不要写成公众号文章或发布稿",
    "exact_title": "迎峰度夏保供电重点工作月度汇报",
    "document_type": "work_report",
    "render_template_id": "enterprise-work-report",
    "confirmed_sha256_short": "12ab34cd56ef",
    "editable": false,
    "edit_policy": "new_run_required",
    "validation": {"valid_for_confirmation": true, "field_errors": []}
  },
  "progress": {"done": 0, "total": 5}
}
```

响应不暴露附件本地绝对路径、完整安全 token 或不必要的内部诊断信息。

## 5. 实施任务

本计划使用两级状态，避免与第四份 UX 计划形成依赖环：Tasks 1–6 及 §6.1 自动/API/legacy gate 通过后记为 `BRIEF_CONTRACT_IMPLEMENTED`，允许第二、三份计划继续；§6.2 的真实 Electron/右侧工作台门由第四份计划 Task 7 集成验证，只有其通过后才记为 `BRIEF_ENTERPRISE_USABLE`。前者不是企业放行，只是下游实现前置。

### Task 1：先用测试锁定三个 ID 的分离

**Files:**

- Create: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_brief_contract.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_api.py`

**Step 1: 写失败测试**

覆盖：

1. 现有 `prompt="起草工作汇报，不要写成公众号文章"` 且 `document_type="work_report"` 时，结果始终是工作汇报；
2. 唯一 canary 字符串从 start prompt 原样进入 `DocumentBrief.original_request`、digest 和确认 view，且不被当作事实或分类信号；进入 role-separated Stage envelope 的跨计划断言由第二份计划 Task 4 在 builder 实现后负责；
3. `template_id` 仅落为 `intake_example_id`；
4. `document_type/render_template_id` 不兼容时拒绝创建；
5. 新合同缺少 `document_type` 或空 `prompt` 时返回稳定错误码；
6. legacy run 读取不写盘、不自动补 Brief。

**Step 2: 运行并确认 RED**

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_document_brief_contract.py \
  tests/test_expert_team_api.py
```

预期：新增合同断言失败，既有 legacy 断言保持通过。

**Step 3: 保留 RED 证据并继续 Task 2**

记录失败用例和错误摘要，但不提交故意失败的分支。Task 2 完成最小实现、测试转 GREEN 后，再把测试与实现一起提交。

### Task 2：实现纯函数 Brief 合同

**Files:**

- Create: `hermes-local-lab/sources/hermes-webui/api/expert_teams/contracts.py`
- Create: `hermes-local-lab/sources/hermes-webui/api/expert_teams/data_egress.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_brief_contract.py`

**Step 1: 实现最小纯函数接口**

```python
EXPERT_TEAM_CONTRACT_V1 = "expert-team-contract/v1"
DOCUMENT_BRIEF_V1 = "document-brief/v1"

def build_document_brief(team_id, payload, *, now): ...
def classify_contract_version(mapping): ...
def normalize_document_brief(brief): ...
def validate_document_brief(brief, *, runtime_capabilities, source_registry, model_policy_registry): ...
def patch_document_brief(brief, patch, *, expected_revision, stage_started): ...
def confirm_document_brief(brief, *, now): ...
def brief_digest(brief): ...
def brief_summary(brief): ...
def validate_model_policy_reference(brief, *, model_policy_registry, now): ...
```

实现约束：

- SHA-256 输入采用固定键排序、UTF-8、紧凑分隔符；
- digest 排除生命周期字段 `revision/status/confirmed_revision/confirmed_at/confirmed_sha256`，`revision` 在 artifact/binding 中单独绑定；
- 确认顺序固定为“规范化业务字段 → 计算 digest → 写 confirmed revision/time/hash”，后续重算必须得到同一 digest；
- 错误使用稳定 `code/field/message`，不让 UI 解析异常字符串；
- 只对 `work_report` 和 `research_report` 返回 `release_candidate=true`；最终 `enterprise_released` 必须由四份计划的能力 gate 和真实验收共同派生，不能由本模块单独返回；
- `approved_public` 必须检查真实 runtime capability。
- `research_report` 必须拒绝 `citation_style=none`，返回稳定的 `citation_style_required` 字段错误。
- 两个 contract-v1 目标文种至少有一个 server-resolved ready source；hash/ready 状态只能来自 `source_registry`，不能信任客户端字段。
- `data_handling.model_policy_id` 必须在 server registry 中存在、未过期、覆盖当前密级并满足 zero-retention 等约束；验证结果只返回安全 label/code，不暴露凭据。
- `classify_contract_version()` 必须用 sentinel 区分“字段缺失”和“字段存在但为 null/空/未知”；仅前者返回 legacy，未知值返回 `unsupported_contract_version`。start、answer、resume、view 和 recovery 的合同分流统一调用它，不能各写一套 `if/else`。

**Step 2: 补齐边界测试**

覆盖规范化顺序、重复确认幂等、revision 冲突、冻结字段、交付控制字段、未知字段拒绝、密级 custom 标签、source ID 去重、无 ready source 被拒绝、客户端伪造 hash 被覆盖/拒绝、受限密级无合规模型策略被拒绝，以及 `research_report + citation_style=none` 被拒绝。

**Step 3: 运行定向测试并确认 GREEN**

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q tests/test_expert_team_document_brief_contract.py
```

预期：全部通过，无新增或未解释 warning；既有 `audioop` deprecation warning 单独记录。

**Step 4: 提交**

```bash
cd "$(git rev-parse --show-toplevel)"
git add hermes-local-lab/sources/hermes-webui/api/expert_teams/contracts.py \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/data_egress.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_brief_contract.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_api.py
git commit -m "feat(webui): add versioned document brief contract"
```

### Task 3：把合同接入 catalog 和 run 创建

**Files:**

- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/catalog.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/__init__.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_brief_contract.py`

**Step 1: 给 catalog 示例增加显式语义**

示例结构至少包含 `intake_example_id`、`document_type`、`task_mode`、预填字段；不得继续以自然语言关键词作为唯一分类信号。

**Step 2: 在 `start_expert_team()` 中建立双路径**

```python
_MISSING = object()
requested_contract = payload.get("contract_version", _MISSING)
if requested_contract is _MISSING:
    # 只有字段真正缺失才进入现有 legacy 路径
    return start_legacy_expert_team(payload)
elif requested_contract == EXPERT_TEAM_CONTRACT_V1:
    run["contract_version"] = EXPERT_TEAM_CONTRACT_V1
    run["document_brief"] = build_document_brief(team_id, payload, now=now)
    run["stage_artifacts"] = []
    run["canonical_document_ref"] = None
else:
    raise ContractError(
        code="unsupported_contract_version",
        field="contract_version",
    )
```

新合同 run 创建后停在 Brief 确认前，不调用 `answer_and_reserve_expert_team_execution_start()`。

不能使用 `if v1 / else legacy`：空字符串、拼错版本和未来版本都必须 fail closed，不能静默降级为 legacy。只有请求中完全缺失 `contract_version` 才具备历史兼容语义。

**Step 3: 证明历史兼容**

增加 fixture：无 `contract_version` 的现行 v2 run 可打开、可按旧路径完成；GET 不改文件 mtime 和内容 hash。另测显式 `null`、`""`、`expert-team-contract/v2` 和拼错版本均返回 `unsupported_contract_version`，且不得创建 run 文件或调用 legacy start。构造一个含未知版本的落盘 run，证明 view/resume/recovery 均 fail closed 且不改写它。

**Step 4: 运行回归**

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_document_brief_contract.py \
  tests/test_expert_team_api.py \
  tests/test_expert_team_v2_runtime.py
```

**Step 5: 提交**

```bash
cd "$(git rev-parse --show-toplevel)"
git add hermes-local-lab/sources/hermes-webui/api/expert_teams/catalog.py \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/__init__.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_brief_contract.py
git commit -m "feat(webui): persist expert team document briefs"
```

### Task 4：增加 Brief 更新与确认 mutation

**Files:**

- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/view.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/storage.py`
- Create: `hermes-local-lab/sources/hermes-webui/api/expert_teams/source_registry.py`
- Create: `hermes-local-lab/sources/hermes-webui/api/expert_teams/source_context.py`
- Create: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_source_context_contract.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_brief_contract.py`

**Step 1: 写 API RED 测试**

覆盖：更新、确认、重复提交、expected version 冲突、字段错误、首阶段前进度仍为 0/N、确认后不自动启动、首阶段后冻结；另覆盖 opaque attachment/local/provided-text source 的 workspace 边界、symlink 逃逸、原始字节 SHA-256、客户端伪造 hash、无 ready source、提取/分段失败，以及密级/模型策略不兼容。

**Step 2: 复用现有 run 锁和幂等账本**

新增 runtime 函数：

```python
def update_expert_team_document_brief(...): ...
def confirm_expert_team_document_brief(...): ...
```

不得为 Brief 另建第二套锁或第二套版本号存储。现有 `_prepare_mutation()` 强制要求 stage identity，因此需要最小扩展为 `scope="brief"` 或 `require_stage=False`：继续复用 run 锁、expected version、幂等账本和 action journal，但 Brief mutation 不伪造 `stage_id`，也不做当前执行阶段校验。

`source_registry.py` 在 run 锁内把 attachment/local/provided-text opaque ref 解析为受控 registry entry，服务端计算原始字节 hash，并把 sanitized locator/hash 回写 Brief；客户端传来的 hash 只作冲突检测，绝不成为权威值。`provided_text` 先原子固化为只读 blob 再 hash。

确认 mutation 必须在首阶段前完成第二份计划 §3.3 的完整 `SourceContextSnapshotV1` 提取/分段预检，而不是只看文件存在：固定流程为“解析 registry → 规范化 Brief → 校验模型数据策略 → 计算拟确认 revision/hash → 构建并持久化不可变 source snapshot → 原子写 run 的 confirmed Brief + snapshot ref”。snapshot 先落盘、run 后落盘的崩溃窗口允许留下不可达 orphan，但 reconciliation 只能在 hash/revision 全匹配时复用，绝不能把半成品视为 confirmed。任何来源不可读、空、超限或 segment 无法复算都使确认失败且模型零调用。

同时在现有 `/api/expert-teams/answer` 处理器中按 `contract_version` 分流：contract v1 只保存答案，workflow 仍为 `collecting_required`，由 view 派生 `brief_gate=needs_confirmation`，不得调用自动 reserve 分支；无 `contract_version` 的现行 v2 run 继续使用原 `answer_and_reserve_expert_team_execution_start()`。Brief 确认后，现有 `/api/expert-teams/resume` 才负责显式启动。

**Step 3: 增加两个路由**

```text
POST /api/expert-teams/brief/update
POST /api/expert-teams/brief/confirm
```

沿用 mutation 的 `session_id`、`expected_version`、`idempotency_key` 校验；错误响应提供稳定 code。

**Step 4: View 暴露展示所需摘要**

只返回前端真正需要的规格摘要、验证错误、编辑策略和短摘要，不返回内部附件路径。

**Step 5: 运行测试**

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_document_brief_contract.py \
  tests/test_expert_team_source_context_contract.py \
  tests/test_expert_team_frontend_v2.py
```

**Step 6: 提交**

```bash
cd "$(git rev-parse --show-toplevel)"
git add hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/view.py \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/storage.py \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/source_registry.py \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/source_context.py \
  hermes-local-lab/sources/hermes-webui/api/routes.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_brief_contract.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_source_context_contract.py
git commit -m "feat(webui): add document brief confirmation mutations"
```

### Task 5：接入启动弹窗的显式字段，不实现完整工作台

**Files:**

- Modify: `hermes-local-lab/sources/hermes-webui/static/panels.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/commands.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-actions.js`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v2.py`

**Step 1: 先写静态与 presenter RED 测试**

断言受控试点 payload 明确发送 `contract_version/intake_example_id/document_type/document_brief_seed`，并停止把示例 ID 当模板 ID；默认正式入口在第四份工作台计划完成前仍走原路径，防止用户创建后卡在没有 UI 的 Brief 状态。

**Step 2: 最小调整入口**

- 示例卡只负责预填；
- 只有后端显式返回 `contract_rollout.mode=pilot` 时，两个目标文种才显示“企业合同试点”并发送 v1；capability 缺失或 off 时继续原入口，不提前宣称已正式放行；
- 其余能力明确显示“草稿能力”；
- textarea 有可见 label；
- 创建后进入 Brief 确认，不自动开跑。

完整的右侧工作台、轮询保护、Office 二级界面和 rollout policy 按第四份计划实施。本任务只让前端对缺失/off capability fail closed，并预留受控 pilot payload；不在此重复实现第二套 feature flag。只有第四份计划的 Brief UI、轮询保护和真实 Electron gate 通过后才可在目标环境打开试点入口。

**Step 3: 运行前端静态检查**

```bash
cd hermes-local-lab/sources/hermes-webui
npm run lint:runtime
../hermes-agent/venv/bin/python -m pytest -q tests/test_expert_team_frontend_v2.py
```

**Step 4: 提交**

```bash
cd "$(git rev-parse --show-toplevel)"
git add hermes-local-lab/sources/hermes-webui/static/panels.js \
  hermes-local-lab/sources/hermes-webui/static/commands.js \
  hermes-local-lab/sources/hermes-webui/static/expert-team-actions.js \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v2.py
git commit -m "feat(webui): send explicit expert team document intent"
```

### Task 6：合同级回归和迁移证明

**Files:**

- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_brief_contract.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_delivery_contract.py`

**Step 1: 增加迁移矩阵**

| run 类型 | GET | 继续执行 | Brief 编辑 | 企业正式交付标识 |
|---|---|---|---|---|
| 无 `contract_version` 的现行 v2 run | 可读且不写盘 | 原路径可收尾 | 不可 | 否 |
| 未知/空 `contract_version` 的新请求 | 拒绝 `unsupported_contract_version` | 不创建 run | 不可 | 否 |
| contract v1 draft | 可读 | 不可启动 | 可 | 否 |
| contract v1 confirmed | 可读 | 可启动 | 首阶段前可改，开始后冻结 | 候选，仍需下游 gate |

**Step 2: 运行专家团后端回归**

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q tests/test_expert_team_*.py
```

预期：所有测试通过；任何无 `contract_version` 现行 run 的行为变化都必须有明确迁移断言。

**Step 3: 检查源码边界**

```bash
rg -n "template_id|document_type|render_template_id|intake_example_id" \
  api/expert_teams api/routes.py static tests
```

逐项确认：旧 `template_id` 只存在于兼容适配或 DOCX 引擎真实模板语义中，没有新的歧义写入。

**Step 4: 提交**

```bash
cd "$(git rev-parse --show-toplevel)"
git add hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_brief_contract.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_delivery_contract.py
git commit -m "test(webui): lock document brief migration behavior"
```

## 6. 验收门禁

### 6.1 自动门禁

- 新合同 run 必须有显式 `document_type`；
- Brief 未确认，第一阶段绝不预留或生成；
- `exact_title`、`additional_context`、密级、禁止表述和来源政策可从 run 原样追溯；
- `work_report` 与 `research_report` 的条件字段缺失时阻断确认；
- 未放行文种不能获得企业正式交付标记；
- legacy run GET 不写盘，仍可按旧路径收尾；
- 冲突更新保留用户草稿并返回可处理的 409；
- run schema 保持 2，现有结果绑定/恢复测试必须继续通过。

### 6.2 人工门禁

以下门禁不在本计划 Task 5 的最小入口中伪造，由第四份 UX/rollout 计划实现工作台后统一执行并把证据回填到最终 QA 报告：

- 真实 Electron 中创建两个黄金路径，确认需求确认显示为 0/N；
- 用户能在右侧工作台看见精确标题、文种和 Brief 版本；
- 否定句“不要写成公众号文章”不会改变显式文种；
- 缺字段时能定位到具体字段，不能只弹通用错误；
- 启动后修改核心规格时明确提示新建任务，不静默污染已有阶段。

### 6.3 完成定义

- `BRIEF_CONTRACT_IMPLEMENTED`：Tasks 1–6、自动/API/legacy gate 通过，默认 rollout 仍 off；只表示可安全进入 Stage/Canonical 实现。
- `BRIEF_ENTERPRISE_USABLE`：在前者基础上，第四份计划的真实 Electron 中完成 §6.2、两个黄金路径和 rollout 复验；才表示企业输入合同对用户可见、可发现、可使用。

最终对外只能使用第二级结论。仅有单元测试、接口 200、run 文件出现 `document_brief` 或第一级状态，都不等于企业输入合同已经可用。
