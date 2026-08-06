# Taiji Agent 项目规则

## 基本执行口径

- 修改前先核对实际仓库、分支、worktree、HEAD、源码入口和运行来源。
- 遇到 Bug 必须给出根因和影响面，不得只修当前表象。
- 没有实际验证，不得宣称“已完成”或“已修复”。
- 状态报告必须区分：`已实时验证`、`未实时验证`、`历史线索`。
- 修复后必须报告：修改内容、验证结果、剩余风险和下一步。

## 开发生命周期强制规则

凡涉及仓库文件修改、Git/GitHub、分支/worktree、PR/CI、源码启动、开发或运行目录、持久服务、打包或发布，必须先完整阅读并遵循：

[`docs/runbooks/development-lifecycle.md`](docs/runbooks/development-lifecycle.md)

该文档是本项目唯一的完整开发生命周期规范，本文件不重复维护其具体步骤。

## 标准收尾快捷授权

当用户明确说“按标准收尾”时，表示授权对当前独立成果连续执行本地验证与提交、push 功能分支、创建 PR、处理当前成果导致的 CI 红灯，并在全部门禁通过后合并、同步和复验正式 `main`，最后进入安全清理。该触发词不授权制包、安装、部署、持久服务、发布、绕过门禁、影响其它任务或删除归属不明的内容；具体动作、停止条件和证据要求以 [`docs/runbooks/development-lifecycle.md`](docs/runbooks/development-lifecycle.md) 第 4、8、9、10 节为准。

## Linux/Kylin/UOS 打包规则

凡涉及 Linux/Kylin/UOS 打包、安装脚本、桌面壳启动链、诊断脚本、运行时目录调整、离线交付或用户安装包去 Hermes 化，必须显式调用 `$taiji-kylin-packaging`。

Release gate、目标机验收命令和已确认的 Kylin/UOS 离线交付经验，以以下内容为准：

- `$taiji-kylin-packaging`
- `docs/runbooks/taiji-kylin-uos-offline-delivery.md`

后续交付变更必须同步更新该手册和当轮验证台账。没有绑定当前制品的目标机验证证据，不得宣称已完成离线交付或目标机验收。

## Taiji 前端 UX QA Gate

凡涉及前端、UI、UX、页面、组件、布局、样式、交互、表单、列表、表格、导航、弹窗、可访问性、浏览器测试、截图、视觉优化或功能完整性，必须显式使用 `$frontend-ux-qa`。

完成前必须输出中文《前端 UX QA 报告》。

前端任务不得只以“代码可以编译”作为完成标准：

- 用户可感知的能力没有可见、可发现、可访问的 UI 入口，至少标记为 P1；
- 主流程被阻塞时标记为 P0；
- 未执行的浏览器测试、截图测试、可访问性自动化或视觉回归，必须标记为“未验证”，不得写成“通过”；
- 分支或 worktree 中的页面效果，只能证明该开发来源的效果，不能代替正式 `main` 或安装态验证。
