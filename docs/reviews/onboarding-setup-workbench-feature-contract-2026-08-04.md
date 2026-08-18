# 首次启动配置工作台功能契约

验证对象：`codex/linux-sales-grade-installer` 分支当前 worktree 中的 `hermes-webui` 首次启动流程。

| 能力 | 数据/API/状态存在 | UI 入口存在 | 用户反馈存在 | 错误处理存在 | 空/加载/禁用状态 | 键盘/可访问性支持 | E2E/浏览器测试 | 状态 | 备注 |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 展示授权、模型、工作区、安全策略四项检查 | 是 | 是 | 是 | 是 | 是 | 已覆盖 | Chromium 已执行 | 通过 | ID 固定为 `license/model/workspace/security`；API 不返回密钥。 |
| 单项重新检查 | 是 | 是 | 是 | 是 | 是 | 焦点返回已验证 | Chromium 已执行 | 通过 | 重试调用 `GET /api/setup/status`，后端保持幂等。 |
| 从失败项进入模型/工作区配置 | 是 | 是 | 是 | 是 | 是 | 明确按钮及表单 label | Chromium 已执行 | 通过 | 回到向导中对应步骤，不依赖隐藏快捷键。 |
| 从失败项进入授权/安全管理 | 是 | 是 | 是 | 是 | 是 | 跳转后焦点进入当前设置面板 | Chromium 已执行 | 通过 | 工作台关闭时不把焦点抢回恢复入口；处理后可从全局恢复入口返回并重新检查。 |
| 自托管 Base URL 探测 | 是 | 是 | 是 | 是 | 是 | 输入焦点保持、状态 live region 已验证 | API 集成与 Chromium 已执行 | 通过 | 探测原位更新；空模型列表按失败处理并禁用“继续”。 |
| 已有模型配置的覆盖确认 | 是 | 是 | 是 | 是 | 是 | `role=alert`，焦点已验证 | Chromium 已执行 | 通过 | 首次请求返回 409；只有显式确认才发送 `confirm_overwrite=true`。 |
| 检查未通过时禁止完成 | 是 | 是 | 是 | 是 | 是 | 禁用态保留 accessible name | 后端集成与 Chromium 已执行 | 通过 | `POST /api/onboarding/complete` 返回 409，不写 `onboarding_completed`。 |
| 四项全部就绪后显式完成 | 是 | 是 | 是 | 是 | 是 | 主按钮具备禁用/忙碌语义 | 后端集成与 Chromium 已执行 | 通过 | 完成端点是用户流程唯一持久化门禁。 |
| 暂时关闭检查窗口 | 是 | 是 | 是 | 是 | 是 | 焦点返回已验证 | Chromium 已执行 | 通过 | 只关闭本次对话框，不调用完成 API，下次启动仍出现。 |
| 关闭后无刷新恢复 | 是 | 是 | 是 | 是 | 是 | Escape/Enter、焦点和 44 px 入口已验证 | Chromium 已执行 | 通过 | 390×844 与 1440×960 均可发现；入口与 Toast 不重叠，完成后隐藏。 |
| 并发设置写入与完成状态一致性 | 是 | 不适用 | 是 | 是 | 是 | 不适用 | 线程交错自动化已执行 | 通过 | 共享锁、同目录原子替换和 generation/token CAS；被淘汰请求返回当前权威状态。 |

## 当前自动化证据

- 定向 `test_settings_persistence_concurrency.py`、`test_onboarding_static.py`、`test_onboarding_mvp.py`：40 项通过。
- 全部 `test_*settings*.py` 与 `test_*onboarding*.py`：213 项通过。
- 真实浏览器脚本 `tests/onboarding_workbench_browser_smoke.py` 已在当前 worktree 执行通过；它绑定当前 worktree 的 WebUI/Agent 来源并阻断非回环请求，覆盖 390 px 窄屏、1440 px 桌面、键盘关闭/恢复、初始状态失败恢复、单项重试、覆盖确认、完成态隐藏、自托管探测焦点/live 状态、空模型阻断和外部恢复焦点。

## 契约边界

- 该契约仅证明当前功能分支的 WebUI/API 实现和已执行自动化。
- 不代表正式 `main`、DEB 安装态、麒麟/UOS 目标机或 Windows 安装态已验收。
