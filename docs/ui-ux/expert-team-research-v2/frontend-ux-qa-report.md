# 前端 UX QA 报告：深度材料研究团极简交互

## 状态

带限制完成（功能分支已通过定向自动化和隔离 Electron 真实运行验证；axe 自动化和独立像素级视觉回归未验证）。

## 变更范围

- `research-report/v2` 发起页：仅保留能力说明、“原始诉求”文本框和“开始研究”。
- `research-report/v2` 工作台：仅展示一次原始诉求、安全化研究进度、证据基础、结论级追问、故障恢复和最终交付。
- Presenter 仅投影后端 `research_progress` 和 `evidence_summary`；不从成员、阶段名或前端分母推断研究状态。
- 非研究团队和历史 v1 仍走原有页面与工作台分支。

## 主要用户目标

用户输入一次原始诉求后开始研究，无需选择来源模式或填写固定问卷；公网不可用时由系统自动降级，只有会改变核心结论的歧义才要求用户确认。

## 主内容 / 辅助内容 / 高级内容

- 主内容：原始诉求、当前用户可理解状态、必要追问、最终 DOCX 操作。
- 辅助内容：来源基础徽标、公网/本地/模型知识计数、安全化降级说明。
- 高级内容：内部 Prompt、检索词、原始检索日志、阶段产物、阶段成员和逐阶段确认不对用户展示。

## 已测试的主要用户路径

1. 键盘 Enter 从专家团卡片打开深度研究发起页。
2. 确认发起页只有一个语义化表单、一个带 label 的 textarea 和一个 submit 主按钮。
3. 在 1440×900、1024×800、760×760 检查横向溢出、对话框边界和底栏遮挡。
4. 用 Shift+Tab 检查焦点不逃出对话框，用 Escape 关闭并确认焦点归还到触发卡片。
5. 注入后端 v2 view，确认工作台只显示一次原始诉求、中文状态、来源徽标和降级理由，不出现内部日志/阶段产物/中间确认。
6. 键盘选中“全公司”并 Enter 提交关键追问，实际捕获到 `/api/expert-teams/stage/input` 请求中 `answer="全公司"`。
7. 从真实 worktree 桌面入口复现并修复 `.venv` 解释器未传递导致的启动失败；确认 Agent、WebUI 健康后，从主导航进入“专家团 → 深度材料研究团”，极简发起页可见。

## 功能契约摘要

| 能力 | 数据/API/状态存在 | UI 入口存在 | 用户反馈存在 | 错误处理存在 | 空/加载/禁用状态 | 键盘/可访问性支持 | E2E/浏览器测试 | 状态 | 备注 |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 提交原始诉求 | 是 | 是 | 是 | 是 | 是 | 是 | Electron 已验证 | 已实现 | 研究 v2 专用极简分支 |
| 查看研究进度与自动降级 | 是 | 是 | 是 | 是 | 是 | `aria-live` 已验证 | Electron 已验证 | 已实现 | 仅消费后端安全字段 |
| 回答结论级追问 | 是 | 是 | 是 | 是 | 是 | 全键盘已验证 | Electron 已验证 | 已实现 | form + fieldset + radio + submit |
| 故障后模型重试 | 是 | 是 | 是 | 是 | 是 | 代码与单测已覆盖 | 研究专属实例未验证 | 已实现 | 沿用 product error 恢复入口 |
| 打开/下载/修改/确认最终 DOCX | 是 | 是 | 是 | 是 | 是 | 既有合同已覆盖 | 既有 Electron 回归已验证 | 已实现 | 研究 v2 沿用最终交付面板 |

## 真实浏览器测试证据

- 运行环境：当前 worktree 的 Electron development 模式，隔离 runtime/user-data，独立 22042/22087 端口。
- 现有 Electron V3 回归脚本成功退出，证明非研究内容创作团路径未被新分支破坏。
- 研究专属 Electron 脚本结果：`{"ok": true}`；已检查发起页、工作台、追问键盘提交和三档视口。
- 2026-08-04 真实启动恢复：Agent `127.0.0.1:18642/health` 与 WebUI `127.0.0.1:18787/health` 均返回 `status=ok`；进程命令指向 `deep-research-online` 的 `.venv`、Agent 和 WebUI 源码。

## 截图情况

- `/tmp/taiji-research-v2-electron/research-launch-1440.png`：已人工查看。
- `/tmp/taiji-research-v2-electron/research-launch-1024.png`：已生成，未单独人工放大检查。
- `/tmp/taiji-research-v2-electron/research-launch-760.png`：已人工查看。
- `/tmp/taiji-research-v2-electron/research-workbench-1024.png`：已人工查看（实际截图时维持 760 视口，文件名仅为场景标识）。
- `/tmp/taiji-research-v2-electron/research-question-keyboard.png`：已人工查看。
- `/tmp/taiji-deep-research-launch-fixed.png`：已人工查看，启动失败页消失并进入首次运行引导；随后通过真实 Electron 可访问性树确认进入深度材料研究团极简发起页。

## 可访问性检查

- 已实时验证：文本框具有可见 label，表单主操作使用 `type="submit"`，追问使用 fieldset/radio，进度使用 `role="status"` + `aria-live="polite"`，对话框关闭后焦点归还，键盘可完成关键追问提交。
- 自动化可访问性：未验证。原因：项目本轮未配置/未执行 axe、Lighthouse 或等价扫描。

## 视觉层级检查

- 已人工检查 1440 和 760 发起页：原始诉求是唯一编辑主内容，主按钮清晰，无成员名册和固定问卷喧宾夺主。
- 主按钮使用 `#06798d` 深色背景和白色文字，修正原浅青背景的文字对比度风险。
- 独立像素级视觉回归：未验证。原因：本轮没有 Chromatic/基准图对比或等价工具。

## 长时间工作体验检查

已通过截图人工检查信息密度、行宽、输入区高度、状态徽标与错误说明的层级。未做 30 分钟以上连续操作观察，因此长时间视觉疲劳结论标记为未验证。

## 空 / 加载 / 错误 / 成功 / 禁用 / 破坏性状态

- 已检查：正常加载进度、公网不可用的安全降级说明、模型知识未核验徽标、关键追问、不合法 pending input 的刷新恢复入口。
- 代码/回归已覆盖：product error 模型重试、交付文件变化恢复、最终 DOCX 打开/下载/修改/确认。
- 研究专属的真实 Provider 不可用和最终 DOCX 全链路：未验证。

## 自动化检查运行结果

| 检查项 | 命令/工具 | 结果 | 备注 |
|---|---|---|---|
| 研究 v2 前端合同 | `pytest ... tests/test_expert_team_research_frontend_v2.py` | 6 passed | Presenter、发起页、工作台、追问、对比度、兼容分支 |
| 前端聚合回归 | `pytest ... test_expert_team_frontend*.py test_expert_team_launch_frontend.py test_expert_team_research_frontend_v2.py` | 206 passed | 使用当前 worktree agent `.venv`，含 1 条上游 deprecation warning |
| 现有 Electron V3 回归 | `node tests/expert_team_v3_electron_smoke.js` | 通过 | 隔离 runtime，无错误输出 |
| 研究 v2 Electron 实测 | `/tmp/taiji_research_v2_electron_smoke.js` | 通过 | 发起、工作台、键盘追问、三档断点 |
| diff 格式 | `git diff --check` | 通过 | 无空白错误 |
| 自动续跑前端合同 | `pytest ... tests/test_expert_team_frontend_v2.py` | 88 passed | GET 只读，v2 由 POST `/resume` 自动续跑 |
| 桌面启动器 Python 运行时合同 | `node --test apps/taiji-desktop/tests/*.test.js` | 16 passed | 同时兼容 `venv` 与 `.venv`，向 Agent/WebUI 传递同一解释器 |
| 研究安全与兼容矩阵 | `pytest -q tests/test_expert_team_*.py tests/test_research_source_adapter_contract.py` | 1710 passed / 1 compatibility failure；修正后直接相关 360 passed | 唯一失败为零来源快照新增投影元数据；已改为零来源保持原合同并复跑通过，GitHub CI 尚待验证 |
| 最终研究安全定向矩阵 | `pytest -q`（Provider binding、检索、来源、模型知识、追问、零来源、runtime adapter） | 545 passed | 含 endpoint network scope、workspace roots、绑定来源计数、云端排除内部来源并继续降级；1 条上游 deprecation warning |
| 独立代码复核 | 第四轮只读复核 | PASS | Critical 与两个 Important 均关闭；不替代真实发布验收 |
| 模板级 DOCX 原生打开 | Word + WPS + LibreOffice | 带限制通过 | 两份 smoke DOCX 可打开；macOS 的 ChatGPT App Data 授权弹窗遮挡了部分原生页面检查 |

## 问题列表

| 严重程度 | 问题 | 证据 | 建议修复方式 | 是否已修复 |
|---|---|---|---|---|
| P0 | 源码 worktree 只有 `.venv` 时桌面端查找 `venv/bin/python`，应用停在“启动失败” | 用户截图与实例日志均显示 `start-agent.sh exited with code 1` | 桌面源码启动器按 `venv → .venv` 解析并显式传给 Agent/WebUI | 是 |
| P0 | 自动本地资料可能在未经过模型数据边界授权时进入云 Provider 请求 | 独立代码审查定位 standalone 分支缺少内部来源 Provider 门禁 | 云 Provider 自动跳过内部层并降级；只有严格绑定且 endpoint 经服务端判定为 loopback 的 Ollama/LM Studio 可消费内部来源，LAN、公网和未知地址均拒绝；模型输入去重并限制为 96k 字符 | 是（代码与回归已验证，真实内网部署未验证） |
| P1 | 研究发起页暴露成员、任务选择与固定规格流程 | RED 测试失败；旧 DOM 包含“团队成员/选择文档任务” | 按后端 catalog 的 `research-report` 可用任务切换极简表单 | 是 |
| P1 | 工作台未消费安全化进度/证据字段，仍可见阶段产物和确认 | RED 测试失败 | Presenter 投影 view 字段，v2 独立信息分层 | 是 |
| P1 | 关键追问不是标准表单，未展示 impact | RED 测试失败 | 使用 form/fieldset/radio/submit 并关联 impact | 是 |
| P2 | 旧主按钮浅青背景存在对比度风险 | CSS 合同 RED | 改用 `#06798d` 高对比操作令牌 | 是 |

## 已修复问题

上表问题均已在功能分支修复；界面问题通过定向测试与 Electron 验证，数据外发问题通过代码门禁与回归验证，真实内网部署仍标记为未验证。

## 剩余风险

- 深度研究极简发起分支根据后端 catalog 返回的稳定公开 `launch_profile_id="research-report"` 识别；若后端将来改名，需同步升级 catalog 合同。
- 截图为功能分支的 development Electron 证据，不代表正式 `main`、安装态或发布态已通过。
- 未实测真实公网、真实本地知识库和真实 Provider 故障后的完整界面切换。
- 当前自动内部资料层仅在本地模型运行时获准使用；云 Provider 会安全跳过该层并明确降级到模型知识，未配置企业模型数据策略时不会静默外发内网资料。
- 两份 DOCX 是研究模板级 smoke 产物，不是真实 Provider 经正式产品路由生成的最终交付。

## 未验证项目

- 自动化可访问性（axe/Lighthouse 或等价工具）：未验证。
- 像素级视觉回归：未验证。
- 1024 截图独立人工放大检查：未验证（但 DOM 断点尺寸和溢出断言已通过）。
- 长时间连续使用疲劳测试：未验证。
- 真实 Provider/公网/内网知识库与最终 DOCX 的研究专属端到端：未验证。
- Word/WPS 打开模板级研究 DOCX：已验证可打开；完整逐页原生检查受 macOS 授权弹窗遮挡，未完成。
- 正式产品路由生成的 Word/WPS 最终验收：未验证。
- 正式 `main` 和安装态 Electron：未验证。

## 后续建议

1. 整合到正式 `main` 后从正式入口重跑同一 Electron 路径。
2. 在后端自动检索链稳定后，补一条真实断网 + 本地库命中 + Provider 恢复的端到端测试。
3. 在项目现有 Playwright 基础上接入 axe，并将 1440/1024/760 截图纳入可审计的视觉基线。
