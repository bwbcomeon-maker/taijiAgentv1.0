# 前端 UX QA 报告：移除左侧编号步骤

## 状态与范围

带限制完成（源码 UI / 隔离浏览器范围）。基线 `main@fc5b1696`，本次工作树仅移除 `index.html` 中不可点击的 `onboardingSteps` 展示容器，保留标题、说明、底部按钮和完整配置流程。原步骤渲染器已有容器缺失即返回的保护，保留内部步骤状态与渲染调用，不为删除展示扩展重构。

## 用户目标与信息层级

用户明确同意删除左侧整组 1–5 编号，而非删除提供商、工作区/模型、密码和最终复核功能。右侧当前配置内容与底部操作为主；左侧标题及说明为辅助，不再展示容易误认导航的步骤卡。

## 功能契约

| 能力 / 契约 | 角色 / 入口 | API / 状态 | UI / 反馈 | 错误 / 加载 / 禁用 | 键盘 | 浏览器 | 结果 |
|---|---|---|---|---|---|---|---|
| 不展示编号步骤 | 桌面用户 / 开始使用检查 | 无 API 变更 | DOM 容器移除 | 不适用 | 不增加焦点项 | 无步骤节点断言 | 通过 |
| 配置流程仍可完成 | 桌面用户 / 底部继续、返回、完成 | 内部五步状态保留 | 各表单仍由底部按钮进入 | 既有加载、错误重试、冲突保留 | Tab/Escape/焦点回归 | 完整 smoke | 通过 |
| 可暂时关闭并返回 | 桌面用户 / 关闭及继续配置入口 | 完成状态未变 | 完成前入口保留、完成后隐藏 | 原有失败关闭策略 | 焦点恢复 | 桌面/移动/窄屏 | 通过 |

## 验证

- RED：静态回归在旧页面出现 `id="onboardingSteps"` 上失败，其他 19 项通过。首次未绑定服务端解释器导致 fixture 启动失败，不计入 RED；显式绑定仓库 Agent 根及 venv 后获得有效 RED。
- GREEN：`test_onboarding_static.py` 与 `test_onboarding_mvp.py` 共 42 项通过；仅既有 discord/audioop 弃用警告。
- 浏览器：现有 `onboarding_workbench_browser_smoke.py` 退出 0，mobile + desktop + keyboard + retry + conflict + completion 全部通过。服务为仓库 Agent venv Python 3.11，浏览器驱动为系统 Python 3.13 Playwright；临时配置和 mock API，不访问真实 Provider。
- 日志 `/private/tmp/taiji-sidebar-steps-browser.log`；截图目录 `/private/tmp/taiji-sidebar-steps-browser/`。人工检查 `onboarding-workbench-default.png` 与 `onboarding-workbench-mobile.png`，编号列表消失，标题与右侧内容可读，底部操作由测试实际执行。移动截图只展示滚动区当前可见部分，不据此宣称全页截图验收。
- 分级统一验证：`scripts/verify.sh` 退出 0，`verification: PASS`；根测试 1321 项、跳过 2 项，WebUI lint 与所选回归通过。日志 `/private/tmp/taiji-sidebar-steps-verify.log`；本轮未运行 `--full`，不复用上次完整验证结果。

## 可访问性、视觉与长时间体验

保留 dialog 的标题、说明关联及标题初始焦点；删除不可操作的展示项，不改变按钮顺序。既有键盘/焦点 smoke 通过。桌面保留左侧留白和原两栏布局，不新增装饰或替代导航。移动端继续使用现有滚动布局；减少无效信息，不增加动画或弹窗。未执行一小时持续使用。

## 状态与问题

加载、错误/重试、冲突、完成成功及关闭重入由 smoke 覆盖；无新增破坏性操作。未发现本次新增 P0/P1；编号似可点击导航的误导已移除。

未验证：axe 自动化可访问性、像素视觉回归、真实麒麟安装态、真实 Provider 与新版安装包。本轮仅源码 UI 调整，不制包、不安装；不能宣称目标机页面已更新。
