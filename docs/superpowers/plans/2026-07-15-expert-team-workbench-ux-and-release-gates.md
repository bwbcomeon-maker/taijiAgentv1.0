# 专家团工作台 UX、状态保护与企业放行门禁实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task by task. Frontend implementation and completion review must use `frontend-ux-qa`; implementation, debugging, and review must also use `andrej-karpathy-skill`.

**Goal:** 在不建设完整在线文档编辑器的前提下，让用户在右侧工作台清楚确认文档规格、稳定完成阶段复核、获得唯一正式成果，并通过结构化 Office 验收闭环决定是否真正交付。

**Architecture:** 延续已确认的 Plan A：需求确认是 0/N 前置准备；成员、进度、确认和成果入口都在右侧工作台；聊天区不放可操作确认卡。前端继续每 5 秒获取权威状态，但把“获取新状态”和“重建可编辑 DOM”分离，通过身份键与 dirty draft store 防止输入面板被轮询关闭。完成态拆为内容、DOCX、Office 三道门。

**Tech Stack:** 原生 JavaScript、现有 presenter/actions/session polling、CSS、自定义 HTTP view/API、pytest 静态契约、Electron/Playwright smoke、目标 WPS 人工验收。

---

**Prerequisites:** 第一份计划已达到 `BRIEF_CONTRACT_IMPLEMENTED`（此处明确不要求尚待本计划验证的 `BRIEF_ENTERPRISE_USABLE`）；第二份 Task 1–9 与第三份 Task 1–7 的实现/API/自动 gate 已按交错顺序完成，并提供 Brief view、StageArtifact/canonical refs、七类 gate、可信 identity flow 和 OfficeAcceptanceV2 API。第三份 Task 8 的目标 Office 证据可在本计划 Task 7 的同一两条黄金路径中汇总，不要求先于 UI 存在。默认 contract-v1 rollout gate 在本计划的真实 Electron 验收通过前保持关闭。

## 1. 不可破坏的产品边界

1. **需求确认不计执行进度**：确认 Brief 时始终显示 0/N；第一模型阶段开始后才是 1/N。
2. **操作集中在右侧工作台**：Brief 确认、阶段批准、修改意见、Office 验收入口均在右侧；聊天区只显示对话、阶段结论和最终后续动作。
3. **收起后仍可发现**：工作台收起为状态胶囊，显示专家团、当前状态和待办数量；不能完全消失。
4. **完成后聊天仍有结论**：5/5 或 6/6 后，聊天区保留稳定最终结论、下一步和“查看完整成果”；右侧成果页保留正式文件入口。
5. **内部英文不外露**：`ready_to_generate`、`passed_with_warnings`、`result_unverified` 等必须映射为中文用户语义。
6. **不伪装独立专家**：同一模型阶段协作称“AI 阶段协作/AI 复核”，不能暗示真实独立专家审计。
7. **本计划不做正文在线编辑器**：正文查看和版本比较可以做；大段内容修改仍通过结构化修改意见和新 attempt 完成。

## 2. 目标主流程

```text
选择专家团
  → 创建任务
  → 确认 DocumentBrief（0/N）
  → 明确点击“开始生成”
  → 分阶段生成与复核（1/N…N/N）
  → 内容已确认
  → 生成唯一 DOCX
  → Office 人工验收
       ├─ passed → 企业交付完成
       ├─ passed_with_conditions + 全部非阻断问题合法 waiver → 企业交付完成
       ├─ passed_with_conditions + waiver 不完整 → 保持阻断，处理问题或完成授权
       └─ failed → 按服务端派生返修路由处理
```

系统不得在回答最后一个需求问题后自动预留第一阶段；这一步必须由用户明确触发，避免规格尚未看全就消耗模型额度。

## 3. 信息架构

### 3.1 展开态工作台

顶部固定区：

- 精确文档标题；
- 文种；
- Brief revision；
- 三道门：内容确认、DOCX 生成、Office 验收；
- 当前唯一下一步。

主标签调整为：

| 标签 | 首屏内容 | 次级内容 |
|---|---|---|
| `任务` | Brief 摘要、当前阶段、一个主操作 | 阶段轨迹、验证问题、修改意见 |
| `成果` | 唯一正式结果、DOCX、Office 摘要 | 完整成果、版本和验收详情 |
| `过程` | AI 阶段角色与协作轨迹 | 原“协作”详情、审计事件 |

“过程”降为辅助信息，不能和当前待办抢占首屏。

### 3.2 收起态状态胶囊

```text
[专家团图标] 内容创作专家团 · 待确认文档规格 · 1 个待办 [展开]
```

要求：可键盘聚焦、具有 `aria-expanded`/`aria-controls`、显示异常红点和完成绿标、点击后恢复到上次标签与滚动位置。

### 3.3 Brief 摘要卡

必须持续可见：原始诉求摘要、精确标题、文种、用途、读者、使用场景、资料边界、模板、密级、版本和安全的模型数据策略标签；提供“查看/编辑文档规格”。展开编辑器中 `original_request` 使用明确标签“原始诉求”，与 `additional_context` 分栏，不能隐形合并；它可在首阶段前修正、确认后随 Brief 冻结。不得暴露 provider 凭据、内部 base URL 或完整策略对象。

- draft：主按钮“补全文档规格”；
- valid draft：主按钮“确认文档规格”；
- confirmed 未启动：主按钮“开始生成”；
- 已启动：核心字段只读，入口改为“查看规格”；修改核心规格引导“基于当前规格创建新任务”；
- 首阶段启动后：整个 Brief 冻结；任何字段修改都引导“基于当前规格创建新任务”，不在当前 run 内重算或覆盖。

冻结测试矩阵：

| 时点 | 核心规格 | 交付控制字段 |
|---|---|---|
| 首阶段前 | 可修改，revision +1，清空确认 | 可修改 |
| 首阶段后 | 冻结，只能新建任务 | 冻结，只能新建任务 |
| 内容批准、DOCX 前 | 冻结 | 冻结 |
| DOCX 后 | 不覆盖旧交付物 | 新建任务；独立 delivery revision 留待后续版本 |

缺字段时必须就地显示字段错误，聚焦第一个错误字段；不能只在右上角弹一个通用 toast。

## 4. 功能契约表

| 能力 | 数据/API 契约 | 可见入口 | 用户动作 | 成功反馈 | 异常边界 | 当前判断 |
|---|---|---|---|---|---|---|
| 召集专家团 | `team_id/intake_example_id/document_type/prompt` | 专家团弹窗 | 选正式文种或草稿能力 | prompt 确定性进入 Brief `original_request` 并创建 run | 原始诉求不作事实来源/分类信号 | 部分具备 |
| Brief 确认 | Brief + revision + field errors | `任务`首屏 | 补充并确认 | 0/N、显示确认摘要 | 缺字段禁止开始 | 缺失，P1 |
| Brief 查看/修改 | edit policy + impact | 持久入口 | 查看或修改 | 新 revision/新任务提示 | 不静默覆盖已生成阶段 | 缺失，P1 |
| 阶段复核 | stage/attempt/brief ref/validation | 当前阶段卡 | 查看、修改、确认 | 进入下一阶段 | invalid/blocking 时禁确认 | 部分具备 |
| 修改意见 | expected version + idempotency | 展开面板 | 输入并提交 | 保留输入直至服务端接受 | 轮询/409 不丢草稿 | 当前不可靠，P1 |
| 唯一成果 | canonical ref + document revision | `成果`和聊天结论 | 查看全文/打开 DOCX | 明确正式版本 | 预览和 DOCX 不可双事实源 | 部分具备 |
| 三门完成态 | content/document/office gates | 顶部状态 | 查看待办 | 明确责任和下一步 | 不能仅凭阶段数变绿 | 缺失，P1 |
| Office 摘要 | hashes short + decision + issue count | `成果`紧凑卡 | 开始/继续验收 | 显示验收人和结论 | 技术详情折叠 | 当前过重，P2 |
| Office 二级验收 | AcceptanceV2 + issues | 抽屉/全高层 | 检查并提交 | 分步状态与回流入口 | 默认 pending | 当前不安全，P1 |

横切状态契约：

| 场景 | 空/加载/禁用 | 错误处理 | 键盘/a11y | E2E 证据 |
|---|---|---|---|---|
| Brief | skeleton、保存中、缺字段禁确认 | 字段错误 + 409 恢复 | label、错误关联、首错聚焦 | draft/confirm/frozen |
| 阶段复核 | 生成中、invalid 禁确认 | blocking issue + retry | 焦点保持、按钮原因可读 | success/invalid/revision |
| Office | pending、打开中、提交中 | token 过期、issue 校验、返修失败 | dialog、fieldset/legend、焦点圈定 | passed/conditions/failed |
| 完成态 | gate pending/running | invalidated/failed 显示 next action | 状态不只靠颜色 | 三门组合矩阵 |

### 4.1 Contract 与历史任务显示

- `contract_version=expert-team-contract/v1` 且文种为目标黄金路径：首批始终显示“企业合同试点”；四份计划的 release gate 全通过只获得 `PILOT_ONLY` 资格，必须另有真实企业样本稳定性证据和明确 `ENTERPRISE_READY` 决策后才可改为正式能力；
- 无 `contract_version` 的现行 v2 run：显示“历史任务，未按企业合同验证”，继续原路径收尾，不伪造 Brief、canonical 或三门 passed；
- 未放行文种：显示“AI 草稿能力”，不得出现企业正式交付承诺；
- view 缺少新字段时 presenter 使用显式历史分支，不抛异常、不猜默认 passed。

## 5. 阶段复核卡

每张复核卡固定包含：

- 阶段名称与 attempt；
- 依据的 Brief revision 和短 hash；
- 自动验证结论与阻断问题数；
- 与上一 attempt 的变化摘要；
- `查看本阶段成果`、`需要修改`、`确认并继续`。

规则：

- `generated_invalid` 主操作是“查看问题并重新生成”，不能显示“确认”；
- 有 unresolved blocking/error/warning 时禁用“确认并继续”，旁边解释原因；首批 pre-Office warning 只能修复，不显示 waiver；
- 当前 credential 没有 `document-approver` role 时禁用“确认并继续”，显示“需使用企业审批身份登录”；不允许用户在卡片中填写审批人或角色；
- 最终内容按钮写“确认内容并进入交付验收”，不能写“完成任务”；
- 提交修改意见时按钮有 loading、disabled、防双击和错误恢复；
- 用户反馈只发送给当前 stage/attempt，不自动带到下一阶段。

## 6. 三道完成门

View 层提供稳定字段：

```json
{
  "completion_gates": {
    "content": {"status": "passed", "label": "内容已确认", "reason_code": null, "blocking_issue_count": 0},
    "document": {"status": "passed", "label": "DOCX 自动检查通过", "reason_code": null, "blocking_issue_count": 0},
    "office": {"status": "pending", "label": "待 Office 验收", "reason_code": "office_review_required", "blocking_issue_count": 0}
  },
  "delivery_status": "office_review_required",
  "next_action": {
    "type": "open_office_review",
    "label": "开始 Office 验收"
  }
}
```

三门状态统一使用 `pending | running | failed | invalidated | passed`，每一门都带 `reason_code`、`blocking_issue_count` 和可执行 `next_action`。派生规则：

- `content.passed`：canonical artifact 已批准、Brief revision/hash 匹配、无 blocking issue；
- `document.passed`：Brief/Semantic/Evidence/Asset/Render 五类上游 gate 均满足放行规则，pre-Office delivery binding hash 闭合；仅文件存在不通过；
- `office.passed`：AcceptanceV2 与当前 delivery binding/canonical/template/renderer/DOCX hash 一致，required checklist 全 passed；上游 stage/semantic/automatic warning 必须已经由新 artifact/report 消除，不能在 Office UI 豁免；decision=`passed` 时必须零 unresolved Office warning/issue，decision=`passed_with_conditions` 时每一个非 blocking Office issue 必须命中服务端可信、当前有效的 `WaiverV1`；
- `delivery_status=passed`：三门均 passed，当前 binding/acceptance/waiver/proof 摘要闭合，completion transaction=`committed`，且权威 run `workflow_state=completed` 并指向同一 delivery attempt；缺一不可。

若 proof 已写但 run/transaction 尚未收敛，三门可以保持各自结果，但 `delivery_status=finalizing`、`next_action=reconcile_completion`，页面显示“正在完成交付，请稍候/恢复”，不能出现绿色“交付已通过”。前端不得仅凭 sidecar 文件存在自行推导 completed。

`passed_with_conditions` 不是 `failed` 的同义词，也不能因选择结论就直接变绿：waiver 不完整时 `office.status=pending`、`reason_code=office_waiver_required`、主操作为“处理问题/申请授权”；全部非 blocking condition 被合法豁免后才转 passed。`failed` 才强制进入返修。任何 blocking issue 永远不可 waiver，decision 即使错误提交为 passed/conditions 也由服务端拒绝。部署没有可信 reviewer identity 时，Office 企业完成整体阻断并显示“需配置可信验收身份”；已有 reviewer identity 但没有 waiver-authorizer role 时不显示“申请授权”，conditions 只能返修。

用户文案固定映射：

| 条件 | 状态文案 | 颜色 |
|---|---|---|
| 内容未全部确认 | 正在生成/待复核内容 | 蓝/橙 |
| 内容通过、DOCX 未完成 | 内容已确认，正在生成文档 | 蓝 |
| DOCX 自动门通过、Office pending | DOCX 自动检查通过，待 Office 验收 | 橙 |
| Office conditions 未完成授权 | Office 有条件通过，待处理问题或授权 | 橙 |
| Office failed | Office 验收不通过，待修改 | 红 |
| 三门通过 | 交付已通过 | 绿 |

只有三门全部 passed 才能使用绿色“交付已通过”。`5/5` 或 `6/6` 只表达专家团阶段计数已走完，不自动等于企业交付完成。

### 6.1 聊天完成态

聊天区最后必须有稳定系统结论，不依赖工作台是否展开：

```text
《迎峰度夏保供电重点工作月度汇报》内容已确认，DOCX 已生成，当前待 Office 验收。
[查看完整成果]  [打开 DOCX]
下一步：在右侧“成果”中完成 Office 验收。
```

按钮可打开成果或文件，但 Brief/阶段/Office 的确认动作仍只在右侧工作台进行。

## 7. Office 二级验收

成果页只显示紧凑摘要：正式版本、文档短 hash、验收状态、问题数、验收人和“开始/继续验收”。完整表单放入 620–760px 抽屉或全高二级界面；窄屏使用全屏层，避免把长表单塞进 380–500px 右栏。

步骤：

1. 打开绑定 DOCX；
2. 选择结论：通过、有条件通过、不通过；默认待选择；
3. 完成结构化 checklist；
4. 有条件通过或不通过时登记至少一项问题；
5. 检查摘要和提交；
6. `passed_with_conditions` 未完成 waiver 时可选“退回修改”或“完成授权”；`failed` 只能“退回专家团修改”。

问题字段至少包括位置、类别、严重程度、描述和预期修复，可选 section/block/logical asset/page。严重程度只呈现服务端允许的“阻断问题（blocking）/可接受条件（condition）”中文选项；未知类别或策略强制 blocking 时前端不能提供 condition，服务端仍必须复验并拒绝降级。有条件通过的授权使用第三份计划独立 `WaiverV1` mutation：用户只选择 severity=`condition` 的当前 Office issue 并填写理由，授权人 principal/role/auth source 和时间由服务端可信身份生成；前端不得提供可编辑“授权人/授权时间”。waiver 未完成前 Office gate 继续阻断。完整 hash、证据路径和 token 放入折叠的“验收详情”。

规则：

- 未打开绑定文件不能提交；
- 未选择结论不能提交；
- 任一 required checklist 为 `not_checked/failed` 不能提交 passed/conditions；`not_applicable` 只在服务端策略允许时可选；
- blocking issue 时不能通过；
- `passed` 时不得存在 unresolved warning/issue，不能绕过 semantic/automatic warning；
- `passed_with_conditions/failed` 无 issue 时不能提交；
- `passed_with_conditions` 的非 blocking issue 可逐项选择返修或合法 waiver，不能用一个总开关批量放行；
- token 过期时保留本地问题草稿，重新打开文件后只刷新 token 和证据绑定；
- canonical、模板或 DOCX hash 变化后旧验收显示“已失效”，不能继续沿用。

Office 返修使用独立 mutation：

```text
POST /api/expert-teams/office/return-for-revision
session_id, run_id, expected_version, office_review_id,
issue_ids[], idempotency_key
```

前端不提交也不决定任意 `target_stage_id`。服务端按当前 Brief、issue category/provenance、artifact 依赖和 delivery binding 派生并返回：

```text
repair_route:
  new_run_required
  new_canonical_attempt
  delivery_repair_required
  rerender_allowed
target_stage_id: string|null        # 仅服务端返回
reason_code: string
required_binding_changes: string[]
```

路由规则固定为：

- 标题、文种、客户/编制单位、密级、版本或日期等 Brief/metadata 错误 → `new_run_required`，因为首阶段后 Brief 全冻结；
- 来源缺失、原文 hash/提取错误、evidence/claim 追溯错误 → 首批 MVP `new_run_required`，因为尚未实现从资料层向后依赖级联的安全重算；
- 只需在现有获批 evidence/facts 内修改表达、结构、引用位置或正文纯净度 → `new_canonical_attempt`，服务端在同一 run 锁内原子失效当前 canonical pointer、下游 system reservation/current refs、delivery/Office gates 和未完成 completion transaction，保留历史审计后，再固定到内容团 `polish` 或研究团 `review` 的 N+1；
- 模板、资产或 renderer 本身需要修复但当前输入 binding 尚未改变 → `delivery_repair_required`，不执行相同输入重跑；
- 只有新的 `RenderInputBindingV1` fingerprint 已变化并完成校验才可 `rerender_allowed`；可导致变化的权威字段仅包括 canonical/Brief ref、template id/version/package hash、asset/semantic manifest hash、renderer name/version/build hash/profile id/profile hash。服务端在同一 run 锁内 supersede 旧 delivery system reservation/current manifest ref、立即失效 document/Office/completion，分别分配新的 system `stage_attempt` 与独立 `delivery_attempt`，再写新的不可变 manifest；两类编号都单调递增但不能互相推导。

当前 active binding/fingerprint 的无变化重复渲染返回 `rerender_input_unchanged`，禁止形成确定性无限返修循环。若经历 A→B→A，新的 A 相对 current B 已变化，因此必须同时分配新的单调递增 stage attempt 与 delivery attempt；历史 A 的 manifest/acceptance/waiver/proof 永不复活。旧 canonical、DOCX、acceptance 保留审计；进入任何返修路线时当前 document/office gate 立即 invalidated。若下游失效 mutation 任一步失败，整个 run mutation 不推进且 reconciliation 不得暴露混合 current refs。`new_run_required` 的 UI 主操作是“基于当前规格创建新任务”，不是伪造当前 run 回退。

## 8. 轮询期间的可编辑状态保护

不能为了保护表单而停止每 5 秒获取权威状态；问题在于当前刷新会重建 DOM。目标状态键：

```text
sessionId/runId/stageId/attempt/briefRevision/reviewId/officeReviewId
```

### 8.1 本地 draft store

```javascript
{
  key: "session/run/stage/attempt/review",
  formKind: "stage_revision",
  value: "用户尚未提交的修改意见",
  dirty: true,
  expanded: true,
  focusedField: "revision-feedback",
  selectionStart: 12,
  selectionEnd: 12,
  scrollTop: 180,
  serverVersionSeen: 7,
  updatedAt: 1784080800000
}
```

首批 draft store 是当前窗口内存 `Map`，不写 localStorage、磁盘或日志。存储只保存有边界的非敏感 UI 草稿，不保存 Office token、附件绝对路径、attestation、证据绑定、打开状态或模型内部 prompt。

### 8.2 合并算法

1. 轮询继续请求服务端；
2. 每类表单加入 `formKind`，每个响应按单调 `run.version` 合并；比当前已应用 version 更旧的重叠轮询响应直接丢弃；
3. 身份键相同且 editable subtree dirty/focused：只更新进度、状态、问题计数等非表单节点，延迟替换表单子树；
4. 同身份但服务端 version 前进：显示“状态已更新，未提交内容已保留”，提供比较/重新载入；
5. stage/attempt 身份前进：旧草稿进入明确恢复区，不自动注入新阶段输入框；
6. mutation 成功：仅清除服务端已接受的对应草稿；
7. 409/version conflict：保留值、焦点、光标、展开态、滚动和标签，显示解决入口；
8. Office token 过期：只保留 issue 的非敏感文本；清空 reviewer、token、证据绑定、attestation、打开状态和旧 checklist；
9. 失焦且无 dirty 时应用最近一次延迟 view；
10. 离开 session/run 时清理过期、空白草稿；有内容的恢复草稿按 TTL 保留并明确归属。

禁止仅靠 `panel.innerHTML` 后再“尽力恢复值”；展开状态、焦点圈定和 DOM 身份也必须纳入契约。

## 9. 可访问性与人体工学

### 9.1 召集弹窗

- 主 textarea 有可见 label 和说明；
- 打开后首焦点落在标题/诉求字段；
- Tab/Shift+Tab 圈定在弹窗；
- Escape 关闭前对脏数据二次确认；
- 关闭后焦点归还触发按钮；
- 错误摘要可聚焦，并链接到具体字段。

### 9.2 Brief 与阶段表单

- 每个 input/textarea/select 有程序化 label；
- 字段错误使用 `aria-describedby`，错误区 `aria-live="polite"`；
- disabled 按钮旁有可读原因，不能只靠颜色；
- 展开控件具有 `aria-expanded` 和 `aria-controls`；
- loading 时使用 `aria-busy`，成功/失败反馈不抢走输入焦点。

### 9.3 Office 抽屉

- 使用 dialog 语义、标题关联和焦点圈定；
- Escape 对脏 issue 草稿做关闭保护；
- 关闭后焦点返回“开始/继续验收”；
- 每一条动态 issue 使用 `fieldset/legend`，增加/删除按钮有包含序号的可读名称，字段错误与具体控件关联；
- drawer 打开时背景 inert，只保留一个纵向滚动容器；
- 键盘可完成打开文档、选择 verdict、增加问题、提交和返修；
- 1024×720 不出现双重横向滚动，正文与底部操作可达。

## 10. 实施任务

### Task 1：先建立 presenter 与静态 UX RED 契约

**Files:**

- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v2.py`

**Step 1: 写失败测试**

覆盖：

- Brief 卡始终显示原始诉求摘要、精确标题、文种、revision 和查看入口；展开编辑器可见带 label 的 `original_request`；
- 需求确认显示 0/N；
- 三门严格从七类 gate 派生，文件存在不能使 document passed；
- 聊天无可操作确认卡，完成后仍有结论和查看完整成果入口；
- 收起态胶囊可发现且可键盘展开；
- 历史 run 和未放行文种使用诚实标签，不伪造企业通过。

**Step 2: 运行并确认 RED**

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_frontend_v2.py
```

**Step 3: 保留 RED 证据并继续 Task 2**

记录失败用例和错误摘要，不提交故意失败的分支；Task 2 presenter/view 最小实现转 GREEN 后一起提交。

### Task 2：扩展 view/presenter 的 Brief 和三门状态

**Files:**

- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/view.py`
- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-presenter.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-ui.js`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v2.py`

**Step 1: 后端只暴露稳定 UI 模型**

提供包含 `original_request` 安全摘要/完整编辑值的 Brief summary/edit policy、artifact validation、completion gates、delivery status 和一个 next action。英文内部状态集中映射，不散落在 DOM builder；legacy run 不猜造 original request。

**Step 2: presenter 生成视图模型**

把“什么状态显示什么按钮”做成纯函数，分别测试 draft、confirmed、generating、awaiting review、invalid、document pending、Office failed、delivered。

**Step 3: 运行和提交**

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q tests/test_expert_team_frontend_v2.py
npm run lint:runtime
git add api/expert_teams/view.py static/expert-team-presenter.js \
  static/expert-team-ui.js tests/test_expert_team_frontend_v2.py
git commit -m "feat(webui): present expert brief and delivery gates"
```

### Task 3：实现 Brief 工作台和 Plan A 布局

**Files:**

- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-ui.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-actions.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/panels.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/style.css`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v2.py`

**Step 1: 调整信息层级**

实现 `任务/成果/过程`、顶部三门状态、Brief 摘要和收起胶囊。保留现有状态恢复入口和阶段结果入口；阶段复核卡读取服务端 identity status/当前 principal 安全 label，无 `document-approver` role 时显示可发现的“使用企业身份登录”动作，调用第二份计划的 identity start/status/logout flow 并在系统浏览器完成 PKCE。前端只保存非敏感 flow/UI 状态，不读取 token、不写 localStorage；登录取消、过期、角色不足和 callback 失败均可恢复，不能退回本机用户名 fallback。

**Step 2: 实现 Brief 编辑/确认**

字段分组、`original_request` 明确 label/帮助文本、就地错误、revision conflict、启动前确认和启动后全 Brief 冻结提示。确认摘要必须能看见原始诉求，最后一个字段填写完成后不自动启动模型。

**Step 3: 保持聊天边界**

聊天只追加阶段结论与完成结论；删除/禁止生成可操作确认卡。完成后查看成果入口仍可见。

**Step 4: 锁定审批身份和 warning 交互**

测试 review 卡在无 identity provider、未登录、登录取消/失败、已认证但缺 approver role、合法 approver、credential 过期和 unresolved warning 等状态：无合法 approver 或有 warning 均不可确认并有可发现说明；合法 approver 且零 unresolved warning 才可提交。覆盖登录按钮、系统浏览器回返、status 轮询、登出和焦点恢复；客户端 payload 不含 token/principal/role，localStorage 无 credential，pre-Office warning 不出现“申请授权”。

**Step 5: 运行和提交**

```bash
cd hermes-local-lab/sources/hermes-webui
npm run lint:runtime
../hermes-agent/venv/bin/python -m pytest -q tests/test_expert_team_frontend_v2.py
git add static/expert-team-ui.js static/expert-team-actions.js static/panels.js \
  static/style.css tests/test_expert_team_frontend_v2.py
git commit -m "feat(webui): add expert document brief workbench"
```

### Task 4：实现轮询 dirty subtree 保护

**Files:**

- Modify: `hermes-local-lab/sources/hermes-webui/static/ui.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/sessions.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-ui.js`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v2.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/expert_team_electron_artifact_smoke.js`

**Step 1: 保持权威轮询测试**

确认 dirty 时仍每 5 秒发请求，不能为了 UI 稳定而停掉服务端刷新。Electron 测试先为 smoke 增加真实 `--out-dir` 参数解析，并在启动前检查 `PLAYWRIGHT_NODE_PATH` 或项目可解析的 `playwright`；缺依赖时输出明确 preflight 失败，不能伪装成 UX 失败。

**Step 2: 建立 identity-scoped draft store**

使用当前窗口内存 `Map`，按 formKind + identity 保存值、dirty、展开态、焦点、selection、scroll、active tab 和 server version；不得写 localStorage，不得保存 token 或敏感路径。

**Step 3: 从整板重挂载改为受控合并**

同身份 dirty 时保留 editable subtree，只更新非编辑状态；按 run.version 丢弃重叠轮询的旧响应；身份推进时迁移到恢复区；409 保留全部草稿。

**Step 4: Electron 跨两个轮询周期测试**

对 `/api/expert-teams/run` 提供确定性响应序列。展开“需要修改”，输入唯一标记 `POLL-DRAFT-7F3A`，设置光标和滚动；断言实际收到至少两次轮询请求、状态徽标随新 version 更新，而面板、值、焦点、光标、标签和滚动保持。第三次响应推进 stage，断言旧草稿进入恢复区但不进入新阶段输入框；另测 409 草稿完整保留。

**Step 5: 运行和提交**

```bash
cd hermes-local-lab/sources/hermes-webui
npm run lint:runtime
../hermes-agent/venv/bin/python -m pytest -q tests/test_expert_team_frontend_v2.py
node -e 'require(process.env.PLAYWRIGHT_NODE_PATH || "playwright")'
node tests/expert_team_electron_artifact_smoke.js --out-dir /tmp/expert-team-polling-qa
git add static/ui.js static/sessions.js static/expert-team-ui.js \
  tests/test_expert_team_frontend_v2.py \
  tests/expert_team_electron_artifact_smoke.js
git commit -m "fix(webui): preserve expert review drafts across polling"
```

### Task 5：实现 Office 二级界面和返修闭环

**Files:**

- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-ui.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/ui.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-actions.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/style.css`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/view.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/office_review.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/waivers.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_ui_contract.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_api.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_waiver_contract.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_delivery_integrity_hardening.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/expert_team_electron_artifact_smoke.js`

**Step 1: 将内嵌长表单降为摘要卡**

成果页显示版本、短 hash、状态、问题数、验收人和入口；完整 token/path/hash 默认折叠。

**Step 2: 实现 drawer/dialog**

默认 pending，分步 checklist，结构化 issues 和独立 `POST /api/expert-teams/waivers/create` request，`passed_with_conditions/failed` 约束、loading/disabled、防重复提交和 token 过期恢复。required checklist 全 passed 才可提交 passed/conditions；passed 必须零 unresolved warning。UI 只读取服务端 identity capability 和当前已认证 principal 的安全 label：没有 reviewer role 时禁用提交并显示“需使用企业验收身份登录”，不得提供可编辑验收人/角色字段。需要独立 authorizer 时，显示“交由授权人处理”，保留非敏感问题草稿并以 run/review ID 调用服务端派生的 `authorizer_handoff` PKCE flow；前端不提交 reviewer/disallowed principal/required role。`authorizer_handoff_ready=false` 时只显示返修；IdP 持久 SSO 又返回原 reviewer 时显示“仍是原验收人，请换账号重试”，草稿不丢、零 waiver；不同且具备 authorizer role 的 principal 登录后才发送 waiver mutation。waiver 表单只发送 Office issue target ref/reason，服务端回传可信 principal/role/time；stage/semantic/automatic warning 只提供修复入口，不显示授权。`passed_with_conditions` 的可信 waiver 未闭合时保持 pending，闭合后才允许 Office gate passed，blocking issue 永不可豁免。动态 issue 使用 fieldset/legend，drawer 背景 inert 且只有一个纵向滚动容器。

**Step 3: 实现退回修改**

实现 `POST /api/expert-teams/office/return-for-revision`，沿用 expected version、idempotency 和 run 锁。请求不接受前端指定 stage；服务端按第 7 节派生 `repair_route/target_stage_id/reason_code/required_binding_changes`。覆盖 Brief/metadata 与来源/证据错误要求新 run、现有证据内正文问题创建 canonical N+1、交付输入未变化时阻断、绑定变化后才允许新的 system delivery attempt。用户确认前展示影响范围，不把问题自由文本直接拼接到任意 prompt。

补充 Office issue severity 对抗测试：UI 只显示策略允许的 blocking/condition；未知 severity、大小写变体、客户端把 blocking 改成 condition、对 blocking issue 显示/调用授权、以及 unknown category 均失败且保留草稿。只有服务端确认的 condition 出现逐项“申请授权”。

补充 authorizer handoff 契约/Electron 测试：模拟系统浏览器持久 SSO 第一次返回原 reviewer，UI 显示 typed 换账号提示、问题草稿/焦点保留且 waiver 调用为零；第二次通过受控 account switch 返回不同 authorizer 后才可逐项授权。另测 provider 不支持切换、handoff callback 过期、run/acceptance 已变化和用户取消，均提供返修或重试且不泄漏 identity token。

**Step 4: 可访问性检查**

实现焦点圈定、Escape 脏数据保护、焦点归还、label/error 关联、aria-live 和键盘全流程。

**Step 5: 运行和提交**

```bash
cd hermes-local-lab/sources/hermes-webui
npm run lint:runtime
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_docx_engine_v2_ui_contract.py \
  tests/test_expert_team_api.py \
  tests/test_expert_team_waiver_contract.py \
  tests/test_expert_team_delivery_integrity_hardening.py
node -e 'require(process.env.PLAYWRIGHT_NODE_PATH || "playwright")'
node tests/expert_team_electron_artifact_smoke.js --out-dir /tmp/expert-team-office-qa
git add static/expert-team-ui.js static/ui.js static/expert-team-actions.js static/style.css \
  api/expert_teams/runtime.py api/expert_teams/view.py \
  api/expert_teams/office_review.py api/expert_teams/waivers.py api/routes.py \
  tests/test_docx_engine_v2_ui_contract.py \
  tests/test_expert_team_api.py \
  tests/test_expert_team_waiver_contract.py \
  tests/test_expert_team_delivery_integrity_hardening.py \
  tests/expert_team_electron_artifact_smoke.js
git commit -m "feat(webui): add structured Office review drawer"
```

### Task 6：先实现默认关闭的 rollout gate

**Files:**

- Create: `hermes-local-lab/sources/hermes-webui/api/expert_teams/rollout.py`
- Create: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_rollout_gate.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/catalog.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
- Modify: `hermes-local-lab/sources/hermes-webui/static/panels.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-actions.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-presenter.js`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v2.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/expert_team_electron_artifact_smoke.js`
- Modify: `hermes-local-lab/sources/hermes-webui/.env.example`

**Step 1: 冻结配置归属和 fail-closed 语义**

`rollout.py` 是唯一配置解释器，读取优先级为环境变量 `TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT` > `config.yaml` 顶层 `expert_team_contract_v1_rollout` > 默认 `off`。首批只接受：

```text
off    # 默认；不允许创建新的 contract-v1 run
pilot  # 仅 content-creator-team/work_report 与 deep-research-team/research_report
```

空值、布尔值、未知值和大小写拼错一律 fail closed 为 `off` 并记录可诊断 warning，不能猜成 pilot。catalog/status 向前端返回稳定 capability；前端不得自行读取环境或硬编码认为 gate 已开。

**Step 2: 写 gate off/on RED 测试**

- off：正式入口继续创建 legacy run，不发送 `contract_version`；直接伪造 contract-v1 start 返回 `contract_rollout_disabled` 且不落盘；
- pilot：只对两个黄金 team/document_type 组合显示“企业合同试点”并发送 v1，其余组合仍是草稿/legacy，绕过 UI 也返回 `document_type_not_in_pilot`；
- 未知版本仍返回 `unsupported_contract_version`，不能因 gate off 降级 legacy；
- gate 从 pilot 恢复 off 后禁止新建 v1，但已存在 v1 run 仍可 read/resume/review/complete，避免把在途任务锁死；
- Electron 证明 pilot 入口可发现、可键盘选择、创建后进入 Brief 0/N；off 时入口不存在且不会创建不可操作的 v1 run。

**Step 3: 在服务端创建点强制执行，在前端只做呈现**

`runtime.start_expert_team()` 先按第一份计划分类 contract version，再在写 run 之前对已识别的 v1 调用 rollout policy；因此未知版本始终是 `unsupported_contract_version`，已知 v1 在 off 时才是 `contract_rollout_disabled`。catalog 只暴露允许的 pilot 能力，routes 返回模式和可用组合。UI 根据服务端 capability 决定是否显示/发送 v1。不能只隐藏按钮而让 API 可绕过，也不能只封 API 而留下可点击后报错的入口。

**Step 4: 运行 off/pilot 确定性检查并提交默认关闭实现**

```bash
cd hermes-local-lab/sources/hermes-webui
npm run lint:runtime
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_rollout_gate.py \
  tests/test_expert_team_frontend_v2.py
node -e 'require(process.env.PLAYWRIGHT_NODE_PATH || "playwright")'
node tests/expert_team_electron_artifact_smoke.js --out-dir /tmp/expert-team-rollout-off-qa
TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT=pilot \
  node tests/expert_team_electron_artifact_smoke.js --out-dir /tmp/expert-team-rollout-pilot-qa
cd ../../..
git add hermes-local-lab/sources/hermes-webui/api/expert_teams/rollout.py \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/catalog.py \
  hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py \
  hermes-local-lab/sources/hermes-webui/api/routes.py \
  hermes-local-lab/sources/hermes-webui/static/panels.js \
  hermes-local-lab/sources/hermes-webui/static/expert-team-actions.js \
  hermes-local-lab/sources/hermes-webui/static/expert-team-presenter.js \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_rollout_gate.py \
  hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v2.py \
  hermes-local-lab/sources/hermes-webui/tests/expert_team_electron_artifact_smoke.js \
  hermes-local-lab/sources/hermes-webui/.env.example
git commit -m "feat(webui): gate expert contract pilot rollout"
```

提交后默认有效模式必须仍为 `off`。这一步只证明 gate 与两种确定性 UI/API 行为存在，不能宣称试点已启用或真实模型已通过。

### Task 7：隔离试点验收、受控启用与可验证回退

**Joint gate:** 与第三份计划 Task 8 联合执行，复用同一两条真实 run、DOCX、binding/hash 和目标 WPS 证据；同时产出第三份 Office 验收报告与本任务 UX/rollout QA 报告，不能各用一套 fixture 冒充端到端闭环。

**Files:**

- Create: `docs/reviews/expert-team-contract-first-ux-qa-2026-07-15.md`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/expert_team_electron_artifact_smoke.js`

**Step 1: 在隔离验收环境临时开启 pilot**

Task 6 的 gate 代码已存在但默认 off。只在隔离验收进程设置 `TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT=pilot`，重启后先从后端 status 断言 `effective_mode=pilot`、`effective_source=environment`，再从真实召集入口创建两条 contract-v1 run。不能先改目标试点环境的持久配置，也不能通过直接造 run 文件绕过入口。

同时由有权限的部署负责人在隔离环境 `config.yaml` 配置第一份计划定义的 `expert_team_model_data_policies`：policy 必须指向本次真实 Gateway 实际使用的 provider/deployment/trust zone，覆盖试验资料密级并满足声明的 retention/training 约束；审批来源、有效期和经过脱敏的策略 fingerprint 写入 QA 报告，凭据不得写入报告或仓库。后端 status/Brief 预检必须证明 policy 有效；fallback 或模型切换也要重新命中同一授权边界。无法取得真实能力证明或安全审批时，本步骤结论直接为 `BLOCKED`，不得用硬编码测试 policy 进入真实模型层。

同一隔离环境还要配置第三份计划 §6.1 的真实 `expert_team_trusted_identity` provider，并由真实企业 credential 登录。先从后端 status 断言 `identity_provider_ready=true` 和 `authorizer_handoff_ready=true`，再用至少一个 `document-approver/document-reviewer` principal 和一个独立 `waiver-authorizer` principal 验证阶段批准、Office passed 与 conditions waiver；启用职责分离时两者必须是不同 principal，并真实覆盖“持久 SSO 先返回原 reviewer 被拒 → 受控 select-account/reauth 切换到 authorizer”的 handoff。错误 issuer/audience/key/role、过期 credential、客户端伪造身份以及 production test resolver 都要 fail closed。只记录 auth source label、脱敏 key/config fingerprint 和 principal pseudonym，不记录 bearer token/完整 claims。当前本机用户名/profile、手工清 IdP cookie 或仅退出本地 session 不能满足本门；没有真实认证、角色或账号切换能力时 QA 结论直接为 `BLOCKED`。

**Step 2: 自动合同层**

运行 Python/Node 契约测试，证明 view、mutation、canonical/DOCX、rollout 和状态派生；这层不声称页面真实可用。

**Step 3: 确定性 Electron UX 层**

使用网络拦截/fixture 提供可重复的 Brief、轮询、409、token 过期、Office 和完成态响应。当前 smoke 主要是 fixture renderer，报告必须明确它只证明 UX，不等于真实模型或真实 Office。

**Step 4: 真实后端与真实模型层**

- 内容创作：工作汇报；
- 深度研究：专题研究报告。

两条黄金路径都从隔离环境的真实召集弹窗开始，经过 Brief、真实模型阶段复核、canonical 成果和真实 DOCX；覆盖 Office pending/failed/passed 与最终完成态。fixture 层不能代替这一层。

目标 pilot 启用前还必须完成第二份计划 §9.2 的真实模型统计门：至少 10 个工作汇报 + 10 个专题研究普通样例，以及两团各至少 10 个覆盖 original request/source/artifact/revision feedback 的 prompt-injection 对抗样例。记录样例集版本、run IDs、实际 provider/deployment、system/envelope hash、首次/重试解析率、标题/事实/引用/污染/注入关键失败指标；任何 §9.2 关键阈值不达标均为 `BLOCKED`。这组批量证据可以复用上述两条黄金 run，但不能用同一 run 重复计数，也不能用 fixture/mock Gateway 充数。

**Step 5: 目标 WPS 外部终验层**

打开两条真实 DOCX，记录 binding/hash、视觉检查、结构化问题、返修一次、旧验收失效和新 delivery attempt。只有宣称 Word 双兼容时才追加 Word 终验。

**Step 6: 覆盖视口与交互状态**

- 至少 1440×900、1280×720、1024×720 和 ≤900px 四档，并真的设置各自宽高；
- 保存默认态、Brief 错误态、复核态、polling dirty 态、Office 抽屉、验收失败和最终完成态截图；
- 覆盖 default/hover/focus/disabled/loading/error/success；
- 覆盖 Tab 顺序、焦点圈定、Escape、关闭后焦点归还；
- 两个轮询周期内草稿保持，覆盖 409、token 过期和服务端阶段推进；
- 工作台收起/展开与 active tab 恢复；最终聊天结论和右侧成果一致。

**Step 7: 输出中文 UX QA 报告**

报告必须逐项标记“已实时验证 / 未验证 / 历史线索”，并分别列出四层证据。列出 P0/P1/P2、截图路径、复现步骤、修复状态、剩余风险和放行结论；同一轮按第三份计划 Task 8 更新 `docs/reviews/expert-team-enterprise-docx-acceptance-2026-07-15.md`，两份报告引用相同 run/binding/DOCX hash。axe/视觉回归未配置或未执行时必须写“未验证”，没有实际操作的项不得写通过。

**Step 8: 证据通过后才启用目标试点环境**

只有 QA 报告满足 P0/P1 为 0、第二份计划 §9.2 全部真实模型样本/阈值门通过、两条真实黄金路径通过、真实 Electron 通过、目标 WPS 通过，才先查询目标环境后端 status 的 `effective_mode/effective_source`。确认没有更高优先级环境变量覆盖后，在目标 `TAIJI_RUNTIME_HOME/config.yaml` 写入：

```yaml
expert_team_contract_v1_rollout: pilot
```

目标环境还必须预先配置并验证经审批的 `expert_team_model_data_policies` 和真实 `expert_team_trusted_identity` provider；前者的实际 provider/deployment/trust zone/retention capability 与隔离验收记录一致或经过独立复核，后者的 issuer/audience/key fingerprint、角色 allowlist、职责分离与 account-switch/reauth 能力有效。不能只复制 policy ID 或 auth source label 而不验证真实部署。重启并再次从后端断言 `effective_mode=pilot`、`effective_source=config`、model policy 有效、`identity_provider_ready=true`、`authorizer_handoff_ready=true` 以及三类角色 capability，再以真实不同角色 credential 重新执行两条召集/批准/Office/waiver 路径和原 reviewer→独立 authorizer handoff，把配置文件 hash、脱敏 policy/identity fingerprint、两条 run ID、截图和结果追加到 QA 报告。若运维选择环境变量作为正式控制源，则必须显式记录该选择和进程配置来源，不能同时写一个无效的 config 值。没有启用后复验证据时仍是“实现但不可见”，按 P1 不得宣称完成。

**Step 9: 按有效来源执行一键回退并复验**

发现 P0/P1、模型合同遵循率回落、DOCX binding 错误或 WPS 阻断时，先从后端 status 获取 `effective_mode/effective_source`：

- source 为 `environment`：把环境变量改为 `off` 或从进程配置中移除；
- source 为 `config`：把 `expert_team_contract_v1_rollout` 改为 `off`；
- 来源未知或冲突：直接停止新流量，修正到唯一明确的 off 来源后再重启。

重启后必须从后端实际响应验证 `effective_mode=off`，并验证 UI 新合同入口消失、直接 v1 start 返回 `contract_rollout_disabled`、在途 v1 run 仍可 read/resume/review/complete。回退只关闭新流量，不删除 run、artifact、DOCX 或验收证据。

**Step 10: 提交验收证据**

```bash
cd "$(git rev-parse --show-toplevel)"
git add docs/reviews/expert-team-contract-first-ux-qa-2026-07-15.md \
  hermes-local-lab/sources/hermes-webui/tests/expert_team_electron_artifact_smoke.js
git commit -m "docs: record expert team contract-first UX acceptance"
```

## 11. Release Gate

### 11.1 自动检查

```bash
cd hermes-local-lab/sources/docx-engine-v2
node --test tests/run-job-contract.test.js tests/domain-contract.test.js \
  tests/template-package.test.js tests/template-data-adapter.test.js \
  tests/rich-draft-package.test.js tests/render-plan.test.js \
  tests/delivery-validation.test.js tests/wps-visual-acceptance.test.js

cd ../hermes-webui
npm run lint:runtime
../hermes-agent/venv/bin/python -m pytest -q tests/test_expert_team_*.py
../hermes-agent/venv/bin/python -m pytest -q tests/test_docx_engine_v2_ui_contract.py
node -e 'require(process.env.PLAYWRIGHT_NODE_PATH || "playwright")'
node tests/expert_team_electron_artifact_smoke.js --out-dir /tmp/expert-team-release-qa
```

上述 Electron 命令只属于确定性 UX 层；真实后端/模型与目标 WPS 的证据必须另行记录，不能由 fixture 测试替代。

Release 执行必须分别以 rollout `off` 和 `pilot` 跑 `tests/test_expert_team_rollout_gate.py` 与 Electron 入口断言；最终试点环境还要从后端 status 读取实际 mode，不能只依据进程环境字符串推定已启用。

### 11.2 前端 UX 放行清单

- Brief 持续可见、可发现、可审计；
- 需求确认始终 0/N；
- 主区域始终只有一个明确 next action；
- 修改意见跨两个真实轮询周期不关闭、不丢值、不丢焦点；
- 409 和阶段推进不污染新阶段；
- Office 默认 pending，详细表单不挤在窄右栏；
- 有条件通过/不通过均有结构化问题与返修入口；
- 历史 run 和未放行文种显示诚实能力边界；
- rollout off 时无不可操作的新合同入口，pilot 时两个黄金入口真实可发现且 API 不可绕过；
- pilot 环境具备经审批且与真实 Gateway 部署相符的 model data policy；密级、retention 或 fallback 不匹配时模型调用为零；
- pilot 环境具备经密码学验证的真实 identity provider、approver/reviewer/authorizer 角色和可验证 authorizer handoff；持久 SSO 返回原 reviewer 时必须拒绝，本机用户名、伪造 claims、手工清 Cookie 或 test resolver 均不能放行；
- 只有三道门通过才出现绿色交付完成；
- 聊天完成结论和右侧 canonical 成果一致；
- 弹窗、工作台、抽屉均可纯键盘完成；
- 1024×720 与窄屏无不可达操作、双横向滚动或被遮挡主按钮。

### 11.3 企业放行判定

| 判定 | 条件 |
|---|---|
| `BLOCKED` | 任一 P0/P1、第二份计划 §9.2 样本量/模型遵循率/注入阈值未满足、任一黄金路径未走通、Office 未真实验收、canonical/DOCX 不一致 |
| `PILOT_ONLY` | §9.2 和两个黄金路径全部通过，但长期运行样本、真实业务多样性或多环境稳定性证据仍不足 |
| `ENTERPRISE_READY` | 四份合同全部落地，自动门、真实模型门、Electron UX、目标 WPS 和发布回归全部通过 |

首批目标应是 `PILOT_ONLY`，经真实企业样本稳定运行后再评估 `ENTERPRISE_READY`；不能因为一次 5/5、6/6 或一个 DOCX 打开成功直接跨级。

## 12. 本计划的《前端 UX QA 报告》完成要求

最终实施回复必须包含：

- **审查结论**：通过、有限通过或阻断；
- **范围**：本轮真实检查了哪些页面、状态、视口和专家团；
- **P0/P1/P2**：每项含证据、影响、修复状态；
- **可发现性**：代码中的能力是否都有真实 UI 入口；
- **交互状态**：default/hover/focus/disabled/loading/error/success；
- **可访问性**：键盘、焦点、标签、错误关联、颜色以外反馈；
- **视觉/响应式**：真实截图和视口；
- **验证结果**：命令、测试数、Electron/WPS/Word 结果；
- **未验证项**：明确写“未验证”；
- **剩余风险与下一步**。

### 完成定义

本计划只有在两个黄金路径的真实 Electron 操作、跨轮询草稿保护、三道门、Office 返修、键盘流程和真实 Office 交付全部有证据后才算完成。仅修改 DOM、静态测试通过、截图看起来正常或工作台显示绿色，均不足以放行。
