# 前端 UX QA 报告：会话、图片产物与安全链路阶段 6（2026-07-17）

## 状态

**带限制完成（实现分支），不等于生产发布通过。**

隔离分支的日常导航等价 Electron 主路径已完成真实截图、键盘、窄屏、刷新恢复和 exact-once 验收。当前未发现阻断该分支主流程的 P0/P1。

限制是：用户日常启动的主检出应用仍是旧源码，本分支尚未合并/部署；外部图片 Provider、VoiceOver、axe/Lighthouse 和 200% 缩放未验证。

## 为什么此前两个界面不一致

用户日常应用读取真实配置，只显示聊天、任务、专家团和设置。此前验收使用临时空白配置，默认功能全部可见，所以出现看板、技能、记忆、工作区、待办、统计和日志等额外导航。

当前验收夹具已改为：

- 使用隔离 worktree 的当前源码。
- 使用脱敏的日常 `feature_visibility` 等价配置。
- 不复制真实密钥、会话、附件或运行时文件。
- 在结果中记录分支、commit、dirty、关键静态文件 SHA-256、Electron 来源、运行时来源和 userData 类型。

因此，源码差异与数据隔离仍存在，但已经显式、可审计，不再把临时默认界面误认为用户日常界面。

## 已实时验证

### Electron 主路径

证据目录：

`~/.local/share/taiji-agent/backups/chat-state-artifact-hardening-20260716-145503/qa-evidence/phase6-final/electron-worktree-exact-once-20260717-1405/`

| 场景 | 结果 |
|---|---|
| 日常导航等价性 | 聊天、任务、专家团、设置可见；其它导航隐藏 |
| Worktree 创建 | 可见 `WT` 标识，名称使用 `taiji-<8hex>` |
| 真实聊天链 | 本地确定性 Provider 经真实 Gateway 主链完成并持久化 |
| 回复 exact-once | Provider POST、API、实时 DOM、重载 DOM 均精确一次 |
| 终端/文件 | 终端显示 Taiji 标识，文件区能读取会话 Worktree |
| 公共路径 | DOM/API 中 Worktree 绝对路径匹配数均为 0 |
| 删除取消 | Escape 关闭，目录和会话状态保持 |
| 键盘删除 | Enter/Tab 可完成危险确认 |
| 删除后恢复 | Worktree 目录删除，会话及 2 条消息仍保留并可重新打开 |
| 窄屏 | 640×900 下确认内容和操作按钮不遮挡 |

人工查看截图确认：重复回复已经消失；实时画面与移除后重载画面都只显示一份助手文本。

### 图片与会话 UX

已有当前分支 Electron 证据覆盖：

- 结构化 Artifact 当前消息内联显示。
- 图片灯箱、下载、重试和关闭具有 accessible name。
- Tab、Enter、Space、Escape 可完成灯箱与操作。
- 图片缺失显示错误卡，不暴露绝对路径。
- 历史图片“重新生成”作为新消息发起，不截断既有 6 条历史；成功后变为 8 条且前缀完全保留。
- 图片晚到时，用户手动滚动的锚点漂移约 `0.023px`，未被强制拉到底部。
- 刷新/重启后 Artifact URL 和图片自然宽度保持。
- 640px 窄屏下图片与输入框不遮挡。
- 清空会话明确提示图片进入 7 天回收期。

### 自动化

| 检查 | 结果 |
|---|---:|
| 最新核心选择集 | `471 passed, 1 warning, 3 subtests passed` |
| Gateway runs/chat-completions | `46 passed, 1 warning` |
| Linux 桌面静态门禁 | `60 passed` |
| PublicProjection/Journal canary | `21 passed` |
| 禁止实现模式扫描 | `10/10` |

## 重复回复根因与验证

问题不是 CSS 或重复 DOM 节点，也不是 Provider 重试。

短回复小于跨块安全过滤器保留窗口时，`message.delta` 已进入 raw 累计，但公共累计仍为空。`run.completed.output` 错把“公共累计暂时为空”理解成“没有收到 delta”，先写入一次全文；随后安全 tail flush 再写一次。

修复后只有“raw delta 从未出现”才允许 completed output 兜底。测试和 Electron 均验证：

- Provider `/chat/completions` POST：1 次
- 会话总消息：2 条（1 user + 1 assistant）
- assistant API content：1 份
- 实时助手气泡：1 份
- Worktree 删除并重载后的助手气泡：1 份

## 可访问性

### 已验证

- 图片、下载、重试、关闭和 Worktree 操作有可发现文字或 accessible name。
- 危险操作默认焦点落在取消。
- Escape 可退出确认；Tab/Enter 可移动并执行确认。
- 错误、加载、完成和回收期不只依赖颜色表达。
- 640px 窄屏下确认框、图片和输入框没有遮挡。

### 未验证

- VoiceOver 完整朗读顺序。
- axe/Lighthouse 自动化。
- 200% 缩放、高对比度和减少动态效果。
- Windows/Linux 安装态的真实屏幕阅读器。

## 问题分级

| 级别 | 数量 | 说明 |
|---|---:|---|
| P0 | 0 | 未发现 |
| P1 | 0 | 分支主流程已关闭来源不明和重复回复问题 |
| P2 | 3 | 主检出日常应用尚未升级；外部图片 Provider 未在线验证；专项无障碍未执行 |
| P3 | 1 | 未配置像素级视觉基线 |

## 结论

实现分支的前端 UX QA 为**带限制完成**：会话、图片、Worktree、安全摘要、键盘和窄屏主路径均有真实 Electron 证据，P0/P1 为 0。

这不代表生产发布通过。合并/部署后仍需从用户日常启动入口复验，并且仓库全量测试红色状态必须按发布门禁报告继续处理。
