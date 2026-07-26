# 《前端 UX QA 报告》：专家团 required_sections 全链路

## 状态

带限制完成。本轮已实时验证新建专家团的工作汇报与研究报告章节合同、真实 Electron 入口和服务端阻断链；未调用真实模型生成完整 DOCX，未执行 WPS/Word 打开验收、可访问性扫描器和像素基线回归。

## 变更范围

- 两类独立版文档能力的必备章节定义。
- Capability → Launch Profile 快照 → Document Brief → Prompt → 阶段产物 → 语义门禁 → DOCX 质量报告的服务端链路。
- 安全投影、Presenter 和 V3 工作台的只读展示。
- 专家团 Python/JS 合同测试及隔离 Electron smoke。

## 主要用户目标

用户在发起专家团后，能在需求确认和生成前确认两个关键节点看到不可由客户端改写的“必备章节”；任何阶段结构或最终正文缺章时，系统不得将其作为合格 DOCX 交付。

## 主内容 / 辅助内容 / 高级内容

- 主内容：文档标题、类型、必备章节和当前可执行操作。
- 辅助内容：资料数量、读者对象、章节合同的自动检查说明。
- 高级内容：冻结的 Launch Profile/Brief 快照、阶段 JSON 结构、语义门禁与质量报告证据。

## 已测试的主要用户路径

1. 内容创作专家团 → 工作汇报 → 真实 HTTP 启动 → 查看 3 个必备章节 → 填写必填字段 → 添加文字资料 → 保存 → 回答问题 → 确认规格 → 在生成前确认页再次核对 3 个章节。
2. 深度材料研究团 → 研究报告 → 真实 HTTP 启动 → 查看 5 个必备章节。
3. 1440×900 和 1024×768 的需求确认/生成前确认，以及 760×900 的交付窄屏状态。
4. 原有阶段复核、补充输入、交付确认、交付漂移恢复、对话框焦点返回/Escape，以及非专家团页面隔离。

## 功能契约摘要

| 能力 | 数据/API/状态存在 | UI 入口存在 | 用户反馈存在 | 错误处理存在 | 空/加载/禁用状态 | 键盘/可访问性支持 | E2E/浏览器测试 | 状态 | 备注 |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 工作汇报 3 个必备章节 | 是 | 是 | 是 | 是 | 是 | 是 | 真实 Electron/HTTP | 通过 | 入口页和生成前确认页均为只读列表 |
| 研究报告 5 个必备章节 | 是 | 是 | 是 | 是 | 是 | 是 | 真实 Electron/HTTP | 通过 | 已验证需求确认页 |
| Launch Profile 冻结并传入 Brief | 是 | 不适用 | 间接 | 失配时 fail closed | 不适用 | 不适用 | Python + 真实 HTTP | 通过 | 注册表漂移不改写已冻结快照 |
| 阶段结构或 Markdown 缺章阻断 | 是 | 自动门禁 | 已有生成失败状态面 | 是 | 是 | 不适用 | Python 合同测试 | 通过 | 代码块中伪造的 Markdown 标题不计入 |
| 语义门禁与 DOCX 质量报告阻断 | 是 | 交付状态面 | 是 | 是 | 是 | 不适用 | Python 合同测试 | 带限制通过 | 真实 provider → DOCX 未执行 |

## 真实浏览器测试证据

- 当轮新鲜证据目录：`/tmp/taiji-required-sections-electron.I1ixPe`
- 证据摘要：`result.json` 记录了开发 worktree、当时 HEAD、源文件 SHA-256、真实 HTTP 端点、两类章节列表、响应式尺寸和禁止请求计数。
- 工作汇报真实状态达到 `ready_to_generate`；Brief 与 Launch Profile 快照中的 3 个必备章节完全一致。
- 未发出任何企业版禁止请求；脚本结束后隔离 Electron 运行时已自动关闭。

## 截图情况

已生成并人工查看当轮新鲜截图，包括：

- `03-real-brief-intake.png`：工作汇报需求确认与 3 个必备章节。
- `04-real-ready-required-sections.png` 和 `05-real-ready-required-sections-1024.png`：生成前确认及紧凑宽度。
- `06-real-research-required-sections.png`：研究报告需求确认与 5 个必备章节。
- 其余截图覆盖专家团中心、团队详情、阶段复核、补充输入、交付/恢复和非专家团页面。

## 可访问性检查

- 必备章节使用带 `aria-labelledby` 的语义化 `section`，章节使用有序列表。
- Electron smoke 验证了必备章节区不含 `input` / `textarea` / `select`，避免客户端暗改服务端合同。
- 现有 smoke 继续验证对话框键盘焦点、Escape 关闭和焦点返回。
- axe 或同类可访问性自动扫描：未验证。

## 视觉层级检查

- “必备章节”置于文档类型/对象之后、可编辑问题之前，属于主流程信息。
- 只读面板的边框、浅色背景、标题和有序列表在现有浅色玻璃视觉中层级清晰，未压过主操作。
- 1440 和 1024 宽度均无横向裁切；1024 时工作台按既有规则切换为完整工作区。

## 长时间工作体验检查

- 长表单使用工作台内部纵向滚动，底部主操作保持可达。
- 证据列表为只读，不增加额外输入负担。
- 真实长时间连续编辑、休眠恢复与多轮生成：未验证。

## 空 / 加载 / 错误 / 成功 / 禁用 / 破坏性状态

- 空资料工作汇报：Python 合同测试通过，章节结构仍必须存在，事实保持待补充。
- 加载/禁用：工作台保留 `aria-busy` 和服务端 allowed-actions 门禁。
- 错误：阶段 JSON、Markdown 或语义门禁缺章时 fail closed；交付漂移/409 恢复分支由 Electron smoke 覆盖。
- 成功：真实 Brief 保存、回答、确认并进入 `ready_to_generate`。
- 破坏性操作：本功能无新增破坏性入口。

## 自动化检查运行结果

| 检查项 | 命令/工具 | 结果 | 备注 |
|---|---|---|---|
| 专家团全量回归 | `../hermes-agent/.venv/bin/python -m pytest -q tests/test_expert_team*.py` | 通过 | 1047 passed in 185.86s |
| 前端运行时规则 | `npm run lint:runtime` | 通过 | ESLint 0 |
| JS 语法 | `node --check` | 通过 | Presenter、V3 和 Electron smoke |
| Python 语法 | `python -m py_compile` | 通过 | 8 个变更的专家团模块 |
| 真实 Electron 验收 | `node tests/expert_team_v3_electron_smoke.js --out-dir /tmp/taiji-required-sections-electron.I1ixPe` | 通过 | 隔离运行时，结束后自动关闭 |
| Git 差异格式 | `git diff --check` | 通过 | 无空白错误 |

## 问题列表

| 严重程度 | 问题 | 证据 | 建议修复方式 | 是否已修复 |
|---|---|---|---|---|
| P2 | Electron smoke 的隔离工作区未声明为运行时默认工作区，会被安全契约以 HTTP 400 拒绝 | 首轮实时 Electron smoke | 显式设置 `TAIJI_WORKSPACE`，并在会话创建失败时输出 HTTP 正文 | 是 |
| P2 | smoke 先写入问题 DOM 值再保存 Brief，服务端重绘后该未提交值会丢失 | 第二轮实时 Electron smoke | 在 Brief 保存重绘后用 Playwright `fill()` 逐项填写并提交 | 是 |

## 已修复问题

- `required_sections` 不再只是空的 Brief 字段，而是两类能力的服务端单一真相源。
- 新启动任务使用 Launch Profile 快照，避免运行中注册表漂移改写已启动合同。
- 写作计划、研究提纲、草稿/复核 `section_map`、最终 Markdown、语义门禁和独立版 DOCX 质量报告均会阻断缺章。
- 用户在需求确认与生成前确认页均能发现且核对必备章节。

## 剩余风险

- 旧任务保留已冻结的旧 Brief；如其 `required_sections` 为空，不会在中途被静默改写。需要新章节合同时应重新发起任务。
- 真实模型是否稳定按原样章节名输出，仍需 provider 端到端运行验证；当前服务端会 fail closed，因此风险表现为生成被拒绝，而不是缺章文档流入交付。
- 实际 DOCX 排版及 WPS/Word 中的章节呈现未在本轮中打开验收。

## 未验证项目

- 真实 provider 完成全部生成阶段。
- 实际 DOCX 在 WPS/Word 中打开、排版和目录效果。
- axe 或同类可访问性自动化扫描。
- 基于稳定图片基线的像素级视觉回归。
- 多小时长时间连续编辑、休眠恢复和多轮生成。

## 后续建议

1. 在有可用 provider 的隔离环境中各跑一次工作汇报和研究报告，核对模型遵循章节合同的稳定性。
2. 对生成 DOCX 执行 WPS/Word 打开验收，再补一轮目录、标题样式和分页视觉检查。
3. 将可访问性扫描和截图基线纳入后续专家团发布门禁。
