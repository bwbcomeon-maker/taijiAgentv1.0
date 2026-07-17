# 太极智能体会话、图片产物与安全链路发布门禁（2026-07-17）

## 结论

实现分支的六阶段范围已经落地并完成聚焦验证，但当前仓库**仍不允许直接发布**。

原因不是本次主链仍有已知 P0，而是两项发布条件尚未满足：

1. WebUI 全量快照仍有 `246 failed`；其中只逐项确认了与本分支改动测试文件重叠的 3 项，其余 243 项不得笼统称为历史基线。
2. 用户日常启动的主检出 Electron 仍运行旧源码。本次只在隔离 worktree 中使用脱敏的“日常导航等价配置”验证当前实现，没有覆盖或迁移真实用户数据。

因此，本报告区分：

- **实现完成**：本次约定的安全投影、上下文真值、幂等持久化、结构化图片、前端恢复、迁移/资源包及连带修复均已有代码与聚焦证据。
- **发布未放行**：全量失败尚未清零或逐项获准豁免，隔离分支也尚未合并/部署到用户日常入口。

## 分支与提交

隔离分支：`codex/chat-state-artifact-hardening`

| 阶段 | Commit | 状态 |
|---|---|---|
| 1 安全公共投影 | `ac4b2972` | 已提交 |
| 2 上下文与幂等持久化 | `4b99ae66` | 已提交 |
| 3 结构化图片产物 | `d79beb4c` | 已提交 |
| 4 前端产物恢复体验 | `c1223b71` | 已提交 |
| 5 安全迁移与资源包 | `b5f3b320` | 已提交 |
| 6 发布验证与连带修复 | 本报告所在的最终本地提交 | 待最终提交后由交付回复记录 hash |

## 已实时验证

### WebUI 聚焦门禁

| 检查 | 结果 |
|---|---:|
| 最新核心选择集（25 个文件） | `471 passed, 1 warning, 3 subtests passed` |
| Gateway runs/chat-completions 完整文件 | `46 passed, 1 warning` |
| Agent 图片/Provider/Gateway/SessionDB | `716 passed` |
| Agent 安全、品牌、Anthropic 回归 | `936 passed, 1 skipped` |
| Agent 换行与文件操作 | `238 passed` |
| `run_agent` 600 秒诊断 | `345 passed`，耗时 `410.4s` |
| Linux 桌面静态门禁 | `60 passed` |
| PublicProjection/Journal canary | `21 passed`，精确 canary 均未出现 |
| Journal/恢复/迁移相关选择集 | `165 passed` |
| 禁止实现模式扫描 | `10/10` |

### 本轮最后发现并修复的重复回复

真实 Electron 验收发现短回复在同一助手气泡内重复两次。逐层证据显示：

- 本地确定性 Provider 只收到 1 次 `/chat/completions` POST。
- Provider 返回标准 role/content/finish 三段 SSE。
- 数据库只有 1 条 user 和 1 条 assistant。
- 重复首先出现在 WebUI Gateway 的 `/v1/runs` 公共文本累计结果。

根因是短文本被跨块安全过滤器暂存在 tail 中，`run.completed` 又因 `public_final_text` 暂时为空而提前写入完整 output；随后 tail flush 再追加一次全文。

修复采用单一真值条件：只有在**没有收到任何 raw delta** 时，才使用 `run.completed.output` 作为公共输出兜底。回归测试先稳定复现双份文本，修复后：

- 精确回归：`2 passed`
- `test_webui_gateway_chat_backend.py`：`46 passed`
- Electron：Provider POST、API assistant、实时 DOM、移除 Worktree 后重载 DOM 均精确出现 1 次

### Electron 与日常界面来源

用户日常进程实时证据：

- Electron PID `71910`
- WebUI PID `71989`
- 来源：主检出 `/Users/bwb/Documents/工作/taiji-agentv1.0`
- 运行时配置：真实 `runtime-home/config.yaml`

本次验收进程：

- 来源：隔离 worktree
- 运行时：临时目录
- 配置：只复制 `feature_visibility` 语义的脱敏等价配置，不复制密钥、会话或附件
- 可见导航：聊天、任务、专家团、设置，与用户截图的日常产品形态一致
- 证据记录：分支、commit、dirty 状态、6 个关键静态文件 SHA-256、Electron 来源、运行时来源和 userData 类型

这解释了此前“验收界面比日常界面菜单更多”的原因：此前测试使用空白配置，导致默认功能全部可见。当前测试夹具已固定日常导航等价配置，并明确声明来源；真实日常进程全程未被终止、重启或写入。

当前实现分支 Electron 证据：

`~/.local/share/taiji-agent/backups/chat-state-artifact-hardening-20260716-145503/qa-evidence/phase6-final/electron-worktree-exact-once-20260717-1405/`

其中已验证：

- Worktree 公共 API/DOM 不含绝对路径。
- 新 Worktree 使用 `taiji-<8hex>`，终端可见标识不再暴露 Hermes。
- 会话内真实聊天经过本地确定性模型主链并持久化。
- Worktree 删除后会话和消息仍保留。
- 危险确认在窄屏可读，默认焦点停在取消，键盘可完成确认。
- 回复在实时和重载后均只显示一次。

## 全量测试现状

WebUI 全量最终快照：

`8529 passed / 246 failed / 14 skipped / 3 xpassed / 1 warning / 19 subtests passed`

耗时 `190.79s`，没有迁移屏障 hang，也没有 60 秒逐测试超时。

与本分支改动测试文件重叠的失败只有 3 项：

1. DOCX 富草稿：隔离 worktree 缺 `docx-engine-v2/node_modules`，无法解析 `@resvg/resvg-js`；指定主检出已有依赖路径后该完整测试文件通过。
2. Provider mismatch：测试仍断言旧 `hermes model`；主分支产品源码早已是 `taiji Agent model`，现只更新测试契约，完整测试文件通过。
3. `server.py < 750`：merge-base 已有 784 行，当前 845 行；保留为公开架构债，没有抬阈值掩盖。

三文件复跑结果：`128 passed / 1 failed`，唯一剩余为行数门禁。

机器可审计分类：

`qa-evidence/phase6-final/logs/webui-full-failure-classification.json`

其 SHA-256 为：

`f7bda9db2de9f04705dda47e8e2761b755b4c4f5a768bdfbdd55c961169bf979`

注意：其余 243 项没有逐项分类，不能据此推算“本次分支只剩 1 项失败”。

Agent 全量在 CRLF 修复前的快照为：

- `26,580 passed`
- `70 failed`
- `126 skipped`
- 9 个 ACP collection error
- 官方 300 秒门禁中的 `test_run_agent.py` 超时

随后已修复其中唯一确认由本分支引入的 CRLF 回归，并对相关 238 项测试全部复跑通过。`test_run_agent.py` 在 600 秒诊断窗口内 `345/345` 通过，但这不能替代官方 300 秒门禁。

## 迁移与安全边界

- Run Journal 在落盘边界统一经过公共投影。
- `CANARY/RAW_ARGS/ABS_PATH/RESULT` 不进入公共 API、SSE、Journal 或导出投影。
- 迁移独占锁默认最多等待 30 秒；超时发生在备份和真值修改前，API 返回 `migration_state_busy` 可重试错误。
- 测试不再全局替换 `threading.Thread`，避免 guarded worker 的 reader lease 永久泄漏。
- Artifact 按 `session_id + artifact_id` 授权；公共 URL 不含绝对路径。
- 历史图片重试以新消息发起，不截断既有会话。
- 真实外部图片 Provider 在线调用未执行，仍以确定性夹具验证协议与 UI。

## 发布门禁

| 门禁 | 状态 |
|---|---|
| 本次核心上下文、幂等消息和 Artifact 契约 | 已验证 |
| 公共 canary 为 0 | 已验证 |
| 迁移 reader lease 与 busy 超时 | 已验证 |
| Electron 分支主路径与日常导航等价性 | 已验证 |
| 回复 exact-once | 已验证 |
| WebUI 全量零失败或完整豁免 | **未通过** |
| Agent 官方 300 秒全量门禁 | **未通过/环境超时** |
| 主检出日常 Electron 已升级为本分支 | **未执行** |
| 外部图片 Provider 在线端到端 | 未验证 |
| axe/Lighthouse/VoiceOver 专项 | 未验证 |

## 发布前下一步

1. 逐项分类或修复 WebUI 剩余 243 个未判定失败，并处理 `server.py` 行数架构债。
2. 决定 Agent 官方 300 秒门禁是优化运行时间、拆分进程还是批准新的时限。
3. 审核并合并本隔离分支，再从用户日常启动入口执行一次不带临时 userData 的升级后复验。
4. 使用真实已配置图片 Provider 运行在线生成、刷新、重启和 25 小时清理模拟。
5. 完成后再标记“可发布”；本报告当前只允许本地提交，不允许发布或默认 push。
