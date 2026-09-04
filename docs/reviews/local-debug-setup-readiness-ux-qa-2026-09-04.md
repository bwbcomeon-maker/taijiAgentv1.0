# 前端 UX QA 报告：本机调试开始使用检查

## 状态与范围

带限制完成：源码聚焦验证、完整门禁及隔离浏览器通过，安装态未验证。来源为正式仓库 main，基线 `8455fa6a` 加本次工作树修改。只调整开始使用检查的安全策略判定、提示和测试；不修改权限、启动链或安全模式默认值。

## 用户目标与信息层级

用户选择企业安全或本机调试均可完成配置，不被强制切回企业安全。主内容为安全策略是否已生效；辅助内容为本机调试的终端/代码执行风险；原有模式设置是恢复入口。无新增布局或高级字段。

## 功能契约

| 能力 / 契约 | 角色与入口 | API / 状态 | UI / 反馈 | 错误 / 加载 / 禁用 | 键盘 | 浏览器 | 状态 |
|---|---|---|---|---|---|---|---|
| 本机调试是有效策略 | 桌面用户 / 开始使用检查 | 后端 ready | 绿色已就绪并显示风险说明 | 原有重试及状态保留 | 既有焦点路径 | mock 安装态响应 | 通过 |
| 企业安全仍可用 | 桌面用户 / 安全设置与检查 | 单元回归 | 原有已生效提示 | 不变 | 不变 | 本轮未单独重测企业安全页面 | 后端通过 |
| 待重启与未知策略不可冒充生效 | 桌面用户 / 检查及恢复入口 | 两模式待重启、未知/full/custom 均拒绝 | 后端输出可操作提示 | 状态读取失败为 unavailable | 既有恢复路径 | 恢复入口、错误重试回归 | 通过 |
| 全部就绪后完成配置 | 桌面用户 / 完成按钮 | 既有完成请求 | 完成后继续配置入口隐藏 | 冲突重新检查 | Escape / 焦点恢复 | 桌面、窄屏和移动端 | 通过 |

## 测试与截图

- RED：旧实现对 restricted/local_controlled 的 `overall_ready is True` 断言失败；先前沙箱端口探测失败不作为 RED。
- GREEN：WebUI 的 `test_onboarding_mvp.py`、`test_security_extensions.py`、`test_security_status_writer.py` 共 48 项通过。补齐异常读取拒绝及不泄露异常原文检查后复验仍通过。
- 完整门禁：`PATH=/Users/bwb/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH scripts/verify.sh --full` 退出 0，`verification: PASS`；根测试 1321 项、跳过 2 项。日志 `/private/tmp/taiji-local-debug-readiness-full.log`。测试期间补充的异常读取断言已由聚焦测试复验，最终 WebUI 测试阶段也在补充之后执行；生产代码未在测试期间变更。
- 浏览器：使用系统 Python 3.13 的 Playwright、仓库 Agent venv Python 3.11 服务端；固定当前 Agent 根、临时 HOME/state/workspace、mock API 和网络阻断，不访问真实 Provider。
- `onboarding_workbench_browser_smoke.py` 退出 0，mobile + desktop + keyboard + retry + conflict + completion 通过。本次 fixture 为安装态本机调试可用，不等于真实麒麟安装验收。
- 日志：`/private/tmp/taiji-local-debug-readiness-browser.log`。
- 截图：`/private/tmp/taiji-local-debug-readiness-browser/`；已人工检查 `onboarding-workbench-default.png`，本机调试安全项为绿色“已就绪”、风险文案可读，没有“需要处理”。其他截图由 smoke 留存，未全部人工复核。

## 可访问性、视觉与持续使用

原有表单/按钮语义及焦点管理未变；既有键盘和焦点 smoke 通过。绿色状态同时有文字，不只依赖颜色。没有新增弹窗、滚动区域、布局或动画，风险说明为次要文字，不抢占主要操作。未做一小时持续使用测试。

## 状态覆盖

覆盖已就绪、待重启、未知策略、读取失败、重试、完成冲突与成功；保留原加载与禁用行为。本轮无删除、覆盖配置等新增破坏性操作。

## 问题与边界

原安装态只接受企业安全，导致合法本机调试阻塞配置完成，已修正。当前未发现新增 P0/P1。依赖 `discord` 的既有 audioop 弃用警告未修改。

未验证：真实麒麟安装态、新制品、真实模型调用、axe 自动化可访问性、像素视觉回归及长期使用。浏览器 mock 只证明渲染和交互，后端判定由独立单元测试证明。不宣称目标机已修复。

## 后续

完整门禁和 Sol 审核通过后提交推送；制包、安装和目标机验收另行授权。
