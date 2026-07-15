# 前端 UX QA 报告：专家团 contract-v1 rollout gate

## 状态

带限制完成。Task 6 的默认 off、隔离 pilot、服务端写前门禁、前端 capability 呈现和两轮真实 Electron smoke 已验证；Task 7 的真实模型、真实企业身份与目标 WPS/Word 终验不属于本次完成声明。

## 变更范围

- 后端：唯一 rollout 配置解释器、catalog/status capability、runtime 新建 v1 写前门禁。
- 前端：专家团召集弹窗只消费服务端 example capability，off 发送 legacy，pilot 两条黄金入口才发送 v1。
- 验证：Python 契约、runtime ESLint、off/pilot Electron、截图与 JSON 证据。

## 主要用户目标

管理员未明确启用试点时，用户只能创建历史兼容的 AI 草稿任务；隔离进程启用 pilot 后，用户能辨认并用键盘选择工作汇报或专题研究报告的“企业合同试点”入口，创建后先进入 Brief 0/N。

## 主内容 / 辅助内容 / 高级内容

- 主内容：模板名称、能力标签、原始诉求和召唤按钮。
- 辅助内容：模板示例、团队成员和能力介绍。
- 高级内容：effective source、允许组合和 warning 只在后端 status/JSON 证据中出现，不挤占用户首屏。

## 已测试的主要用户路径

1. 默认 off：打开内容创作专家团，键盘聚焦并 Enter 选择工作汇报，看到“AI 草稿能力”，创建结果无 `contract_version`/Brief。
2. 隔离 pilot：键盘选择工作汇报，创建 contract-v1 Brief draft，进度 0/5。
3. 隔离 pilot：键盘选择专题报告，创建 contract-v1 Brief draft，进度 0/6。
4. API 绕过：off 下伪造 v1、pilot 下错配 team/document/intake 均在写盘前拒绝。
5. 回退：pilot 创建的既有 v1 在恢复 off 后仍可读取；新 v1 被禁止。

## 功能契约摘要

| 能力 | API/状态 | UI 入口 | 反馈/错误 | 键盘 | Electron | 状态 |
|---|---|---|---|---|---|---|
| 默认 off | 是 | AI 草稿能力 | legacy 创建成功；伪造 v1 拒绝 | Enter | 通过 | 通过 |
| 两条 pilot | 是 | 企业合同试点 | Brief draft 0/N | Enter | 通过 | 通过 |
| 未放行组合 | 是 | AI 草稿能力 | API fail-closed | Enter | 通过 | 通过 |
| 配置诊断 | status | 不作为普通用户控件 | warning + effective source | 不适用 | JSON 通过 | 通过 |

## 真实浏览器测试证据

- off：`/tmp/expert-team-rollout-off-qa/expert-team-rollout-gate.json`，`effective_mode=off`、`effective_source=default`，legacy 0/5。
- pilot：`/tmp/expert-team-rollout-pilot-qa/expert-team-rollout-gate.json`，`effective_mode=pilot`、`effective_source=environment`，两条 v1 分别 0/5、0/6。
- 两轮完整脚本均输出 `EXPERT TEAM ELECTRON SMOKE OK`。

## 截图情况

- off：`/tmp/expert-team-rollout-off-qa/expert-team-rollout-gate.png`。
- pilot：`/tmp/expert-team-rollout-pilot-qa/expert-team-rollout-gate.png`。
- 已人工查看：能力标签清晰，键盘焦点框可见，弹窗主次关系未因 rollout 改变。

## 可访问性检查

- 自动 smoke 已验证模板按钮可聚焦并由 Enter 选择。
- 模板按钮保留明确 `aria-label`，原始诉求保留可见 label。
- 自动化 axe/Lighthouse：未验证，项目未配置本任务可复用命令，未新增依赖。

## 视觉层级检查

“工作汇报 · AI 草稿能力/企业合同试点”与模板名称同处首行，差异可见但没有抢过原始诉求和主召唤按钮。其余未放行文种在 pilot 中继续显示“AI 草稿能力”。

## 长时间工作体验检查

本次没有新增常驻面板、动画或额外弹窗；能力信息复用原模板首行，不增加扫描层级。现有 smoke 继续覆盖 1024、1280、1440 桌面宽度。

## 空 / 加载 / 错误 / 成功 / 禁用 / 破坏性状态

- 空/加载：catalog 既有加载和不可用提示保持不变。
- 错误：非精确配置值安全回退 off 并提供 status warning；API 绕过返回稳定错误码。
- 成功：off 创建 legacy；pilot 创建 Brief draft 0/N。
- 禁用：本功能以能力降级而非禁用死按钮呈现，避免可点击后才报错。
- 破坏性操作：无。

## 自动化检查运行结果

| 检查项 | 命令/工具 | 结果 | 备注 |
|---|---|---|---|
| rollout + frontend | `pytest ...test_expert_team_rollout_gate.py ...test_expert_team_frontend_v2.py` | 97 passed | 1 条第三方 audioop 弃用 warning |
| 影响面回归 | 5 个专家团核心测试模块 | 155 passed | 旧 v1 测试已显式声明 pilot 前置条件 |
| JS runtime lint | `npm run lint:runtime` | 通过 | 无 ESLint 错误 |
| Electron off | rollout smoke | 通过 | effective source=default |
| Electron pilot | rollout smoke | 通过 | effective source=environment |

## 问题列表

| 严重程度 | 问题 | 证据 | 建议修复方式 | 是否已修复 |
|---|---|---|---|---|
| P2 | 未配置自动化可访问性扫描 | 无 axe/Lighthouse 命令 | 后续统一接入现有 Electron E2E | 否 |
| P3 | 第一次预检未找到顶层 Playwright | `MODULE_NOT_FOUND` | 已使用本机现有 `PLAYWRIGHT_NODE_PATH` 重跑 | 是 |

## 已修复问题

- 修复只隐藏前端但 API 可绕过的风险。
- 修复前端按文种硬编码 pilot、可能与服务端组合漂移的风险。
- 修复未知版本可能被 rollout off 错误覆盖的风险。
- 修复旧合同测试暗含“v1 默认开启”的错误前置条件。

## 剩余风险

- 自动化可访问性与视觉基线尚未配置。
- Task 6 只证明确定性 gate 和 UX；不证明真实模型、真实身份、真实企业资料或 WPS/Word 交付质量。

## 未验证项目

- axe/Lighthouse 自动化可访问性：未验证。
- 像素级视觉回归基线：未配置；本轮完成截图人工审查。
- 真实模型与目标 WPS/Word：未验证，留给 Task 7 联合 gate。

## 后续建议

保持持久配置为 off；Task 7 仅在隔离进程临时设置 pilot，并在真实模型、身份和 Office 联合 gate 全部通过后再做独立启用决策。
