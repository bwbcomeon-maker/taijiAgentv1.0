# 专家团工作台功能契约（2026-07-15）

范围：`专家团工作台 UX、状态保护与企业放行门禁实施计划` Tasks 1–3。本文审计稳定 view/presenter、Plan A 工作台、Brief 编辑确认和可信审批身份入口；不提前宣称 Tasks 4–7 已完成。

## 功能契约

| 能力 | 数据/API/状态存在 | UI 入口存在 | 用户反馈存在 | 错误处理存在 | 空/加载/禁用状态 | 键盘/可访问性支持 | E2E/浏览器测试 | 状态 | 备注 |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| Brief 持久摘要 | 是 | 是 | 是 | 部分 | 是 | 是 | 未验证 | 通过 | 始终展示原始诉求摘要、精确标题、文种、revision 与规格详情入口；legacy 不补造 Brief。 |
| 原始诉求完整查看 | 是 | 是 | 是 | 不适用 | 是 | 是 | 未验证 | 通过 | `original_request` 使用明确 `label`；当前为只读详情。 |
| Brief 编辑与确认 | 是 | 是 | 是 | 是 | 是 | label、错误关联、首错聚焦 | 未验证 | 通过 | 原始诉求与补充背景分栏；保存、确认和开始生成相互独立。 |
| Brief revision conflict | 是 | 是 | 是 | 是 | 是 | 草稿保留、首错可聚焦 | 未验证 | 通过 | 409 应用权威 run，明确提示核对后重试，不自动启动。 |
| 需求确认 0/N | 是 | 是 | 是 | 不适用 | 是 | 是 | 未验证 | 通过 | intake 阶段固定 0/N，首模型阶段开始后才进入 1/N。 |
| 三道完成门 | 是 | 是 | 是 | 是 | 是 | 状态不只靠颜色 | 未验证 | 通过 | content/document/office 只接受稳定状态；document 不以 DOCX 文件存在或旧 `delivery_gate=passed` 推导通过。 |
| 七类质量状态到三门派生 | 是 | 是 | 是 | fail-closed | 是 | 不适用 | 未验证 | 通过 | brief/semantic/evidence/asset/render 任一缺失、pending、failed 均阻止 document passed；Office failed 不被完整性摘要覆盖。 |
| 完成事务绑定 | 是 | 是 | 是 | fail-closed | 是 | 不适用 | 未验证 | 通过 | 以 `completion_integrity.transaction_state/summary_closed` 为权威；只有 committed 且 transaction ref 的 delivery attempt 与当前 binding 一致时，Office/企业交付才可 passed。 |
| 内容阻断问题一致性 | 是 | 是 | 是 | fail-closed | 是 | 不适用 | 未验证 | 通过 | brief/semantic/evidence/content 任一 unresolved completion-blocking issue 非零时，content gate 同步 failed 并返回一致 count/reason。 |
| Brief 启动后冻结 | 是 | 是 | 是 | 是 | 是 | 是 | 未验证 | 通过 | starting、generating、当前或历史首阶段 reservation 均返回 `editable=false/new_run_required`。 |
| 唯一下一步 | 是 | 是 | 是 | 是 | 是 | 可见文字按钮/状态 | 未验证 | 通过 | view 输出单一 `next_action`；presenter 纯映射。 |
| 历史任务诚实标签 | 是 | 是 | 是 | fail-closed | 是 | 是 | 未验证 | 通过 | 显示“历史任务，未按企业合同验证”，三门 invalidated。 |
| 未放行文种诚实标签 | 是 | 是 | 是 | fail-closed | 是 | 是 | 未验证 | 通过 | 显示“AI 草稿能力”，不承诺企业交付。 |
| 收起态状态胶囊 | 是 | 是 | 是 | 不适用 | 是 | 是 | 未验证 | 通过 | 展开按钮具有 accessible name、`aria-expanded` 与 `aria-controls`；点击展开/切换时使用实际工作台返回状态同步 ARIA。 |
| 企业审批身份 | start/status/logout | 是 | 是 | 是 | provider/取消/过期/缺 role 均禁用 | 可见文字入口、焦点恢复 | 未验证 | 通过 | UI mutation 不提交 token/principal/role，不把 credential 写入 localStorage。 |
| 阶段批准门禁 | 是 | 是 | 是 | fail-closed | 无合法 approver 或有 unresolved warning 时禁用并解释 | 禁用原因可读 | 未验证 | 通过 | pre-Office warning 不提供“申请授权”。 |
| 聊天区无可操作确认卡 | 是 | 是 | 是 | 不适用 | 不适用 | 是 | 未验证 | 通过 | 生命周期提示只引导右侧工作台；完成成果入口继续保留。 |
| 真实 Brief 编辑与阶段复核 | 是 | 是 | 是 | 是 | 是 | 人工语义检查完成 | 未验证 | 未验证 | 代码与自动化契约已覆盖；真实 Electron 留待 Task 7。 |
| Office 操作路径 | API 部分存在 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 由计划 Task 5 实现和验证。 |

## Tasks 1–3 QA 摘要

- 变更范围：专家团后端 view、纯 JavaScript presenter、工作台最小状态渲染、静态/Node 契约测试。
- 主要目标：用户能持续看见规格真值、阶段 0/N、企业能力边界、三道门和唯一下一步。
- 主内容：任务、Brief、交付三门和唯一下一步；辅助内容：成果；高级内容：过程轨迹和完整规格详情。
- 自动化检查：目标 pytest、runtime ESLint、`git diff --check` 已执行。
- 真实浏览器测试：未验证。原因：计划将真实 Electron/浏览器验收集中在 Task 7；本轮不启动 rollout。
- 截图与视觉回归：未验证。Task 3 已改布局/样式，真实视觉验收按计划留待 Task 7。
- 自动化可访问性：未验证。未发现本轮计划要求可直接复用的 axe 检查命令，且未新增依赖。
- 长时间工作体验与响应式布局：未验证。本轮不改变布局，待 Task 3 与 Task 7 在真实视口审查。

## 当前问题分级

| 严重程度 | 问题 | 当前处理 |
|---|---|---|
| P1 | Office 验收 API 尚未在本轮工作台提供完整可访问入口 | 明确留给 Task 5。 |
| P2 | 未进行真实浏览器、截图、响应式和长时间使用验证 | Task 7 统一执行；本轮只能标记代码层人工语义检查。 |

## 规格审查补修

- 已修复旧 delivery gate 绕过七类上游质量状态的问题。
- 已修复 `completion_integrity=passed` 覆盖明确 Office failed 的问题。
- 已修复未 committed 或 delivery attempt 漂移的事务错误显示企业完成的问题。
- 已修复启动/生成/reservation 已发生但 `stage_outputs=[]` 时 Brief 仍可编辑的问题。
- 已补充胶囊 `aria-expanded` 与展开/切换结果同步，真实浏览器焦点与持久化恢复仍待 Task 7 验证。
- 已改用生产实际的 `completion_integrity.transaction_state`，不再依赖 transaction ref 中不存在的虚构 status。
- 已让内容门状态、阻断计数和 reason code 从同一组 unresolved blocking issues 派生。

结论：Tasks 1–2 的稳定状态模型与最小可见入口通过自动化契约；完整前端工作仍为“未完成”，不得据此开启企业 contract-v1 rollout。
