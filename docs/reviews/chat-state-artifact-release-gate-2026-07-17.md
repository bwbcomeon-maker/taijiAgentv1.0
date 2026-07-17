# 太极智能体会话、图片产物与安全链路发布门禁（2026-07-17）

## 结论

六阶段约定范围及本轮发现的连带问题已经在隔离分支落地；分支自身没有发现新增的 WebUI/Agent 失败节点，三条真实 Electron 主路径也已在可审计源码上通过。

但当前状态仍是：**实现分支带限制完成，生产发布未放行。**

阻断发布的不是本次主链仍有已知 P0/P1，而是仓库全量基线仍红、Agent 仍有文件级超时、真实外部图片 Provider 未在线验收，以及用户日常启动入口尚未合并/部署本分支。

## 分支、提交和运行边界

隔离分支：`codex/chat-state-artifact-hardening`

| 范围 | Commit | 状态 |
|---|---|---|
| 安全公共投影 | `ac4b2972` | 已提交 |
| 上下文与幂等持久化 | `4b99ae66` | 已提交 |
| 结构化图片产物 | `d79beb4c` | 已提交 |
| 前端产物恢复体验 | `c1223b71` | 已提交 |
| 安全迁移与资源包 | `b5f3b320` | 已提交 |
| 阶段 6 门禁与连带修复 | `c21521a4` | 已提交 |
| 配置运行目录公共投影修复 | `f807804d` | 已提交 |
| 迁移 Electron 来源与截图审计 | `14273895` | 已提交 |

用户日常应用全程未被本次验收重启、终止或写入：

- Electron PID `71910`
- WebUI PID `71989`
- 来源为主检出 `/Users/bwb/Documents/工作/taiji-agentv1.0`

所有 Electron 验收均使用临时 runtime、workspace、userData 和脱敏的日常能力配置；结果文件记录分支、commit、dirty 状态、关键静态文件哈希、导航投影和测试进程清理结果。

## WebUI 全量与基线 A/B

当前分支最终隔离全量：

- `8609 passed`
- `168 failed`
- `14 skipped`
- `1 xfailed`
- `2 xpassed`
- `19 subtests passed`
- 耗时 `320.34s`

基线 `d4e06dab` 在同类隔离条件下：

- `8162 passed`
- `218 failed`
- `18 skipped`
- `1 xfailed`
- `2 xpassed`
- `16 subtests passed`
- 耗时 `185.11s`

按失败 node id 精确比较：

- 当前分支新增失败：`0`
- 仅基线失败、当前已通过：`50`
- 当前与基线共同失败：`168`

因此可以确认“本分支未新增 WebUI 全量失败”，但不能把共同的 168 项失败写成通过或自动豁免。

证据：

- 当前日志：`qa-evidence/phase6-final/logs/webui-full-final-isolated.log`
  - SHA-256：`046ea1049035dfea5469ce95e453312775cabb574a0b330271d169cd738df831`
- 基线日志：`qa-evidence/phase6-final/logs/webui-full-base-d4e06dab.log`
  - SHA-256：`b7aeb7fef44bc4716c94279eefc407ef57ba1893bb390021dbf808a9e57a22de`

## Agent 全量与基线 A/B

Agent 使用主检出完整 venv 和仓库正式并行 runner；正式文件级默认超时是 `600s`，不是旧报告中的 `300s`。

最终全量结果：

- `1260` 个测试文件
- `26830 passed`
- `49 failed`
- `13` 个失败文件
- `1` 个未完成文件：`tests/run_agent/test_run_agent.py` 超过 `600s`
- 耗时 `1079.8s`，4 workers

完整 venv 消除了先前因缺少 `acp`、`ptyprocess`、`lxml` 造成的 collection 假红；补齐忽略的 Kanban 构建产物后对应插件文件为 `94 passed`。

A/B 归因结果：

- 先前 27 个失败文件：当前 `1026 passed / 59 failed`，基线 `1025 passed / 60 failed`；当前新增失败 `0`，并修复 1 个 CRLF 回归。
- 全量新增出现的 5 个文件复跑：当前与基线均为 `276 passed / 6 failed`，失败节点完全相同。
- 全量中的两个 browser 依赖安装超时在缓存建立后未复现；仍保留原始全量红色证据，不把复跑通过倒推成全量通过。
- `test_run_agent.py` 的测试源文件相对基线未改动，但尚未完成同条件基线时长 A/B；该 600 秒超时保持“未关闭发布门禁”。

证据：

- 全量日志：`qa-evidence/phase6-final/logs/agent-full-final-complete-venv.log`
  - SHA-256：`7b7c7b74786767bbbde415973762fea9b688656e9b8f46d27a05409d49acf258`
- 旧失败文件当前日志：
  `qa-evidence/phase6-final/logs/agent-prior-failure-files-current-complete-venv.log`
  - SHA-256：`ff1c54eef56c7a9978f59f929522206252a699ae5c7d73f6265b4e225bc5cfab`
- 旧失败文件基线日志：
  `qa-evidence/phase6-final/logs/agent-prior-failure-files-base-d4e06-complete-venv.log`
  - SHA-256：`811bd47aa8c34ebd7933dc6323a283116e584f0cc6ee3a642e2bf9ee56eca56a`
- 新失败文件 A/B：`agent-new-failure-files-current.log` 与
  `agent-new-failure-files-base.log`

## 真实 Electron 验收

证据根目录：

`~/.local/share/taiji-agent/backups/chat-state-artifact-hardening-20260716-145503/qa-evidence/phase6-final/`

| 主路径 | 源码证据 | 结果 |
|---|---|---|
| Worktree、公共投影与 exact-once | `f807804d`，`dirty=false` | `passed` |
| 结构化图片、错误/取消/重试/重启 | `f807804d`，`dirty=false` | `passed_with_provider_fixture` |
| 资源包、旧 JSON、迁移、回滚 | `14273895`，`dirty=false` | `passed` |

对应结果文件：

- `electron-final-f807804d-worktree/electron-worktree-public-contract-result.json`
  - SHA-256：`08320be7bfb65197d5bb52531c1d71edf55e91a88df9b0db59927e8107a20b01`
- `electron-final-f807804d-artifacts/electron-chat-artifact-result.json`
  - SHA-256：`6a3cca60849eb36d661c1b73824151415ee5604d8d22172b8f94b0f4eb50e578`
- `electron-final-14273895-migration/electron-session-bundle-migration-result.json`
  - SHA-256：`5f00534062fd6e2cdbc634fd3c73ac9bf4d81cdcea4774581f5fedf2f787a521`

已验证：

- 日常能力投影仅显示聊天、任务、专家团和设置，其它导航隐藏。
- 公共 API、DOM 和资源包不暴露内部绝对路径。
- Provider POST、API、实时 DOM 和重载 DOM 的助手回复保持 exact-once。
- 结构化 Artifact 自动进入当前助手消息，图片刷新/重启后恢复。
- 缺失、失败、取消和历史重试均有确定状态；历史重试不截断旧消息。
- 晚到图片的阅读锚点漂移约 `0.023px`，低于 `2px` 容差。
- 资源包恢复文本与图片；旧 JSON 只恢复文本。
- 迁移取消不修改数据；成功先备份；二次 Apply 修改数为 0。
- 失败迁移完整回滚，Session、缓存图片和 `state.db` 校验和恢复。
- 迁移测试进程、服务 PID 和临时目录均完成清理。

迁移验收最初暴露了两个测试可信度问题，均已收口：

1. 成功回执等待条件把“正在只读检测”误判为完成，现改为等待复检结束且备份回执已渲染。
2. 旧脚本直接截图，无法拒绝 Electron 合成黑块；现与其它主路径统一记录源码指纹、导航投影和截图审计。最终 6 张迁移截图的 near-black 与透明像素比例均为 0。

## 安全与数据边界

- Run Journal、GET、SSE、replay、search、export 和 Gateway 完成事件统一经过公共投影。
- 工具卡只含脱敏摘要；原始参数、结果、token 和内部路径不进入浏览器或导出。
- 用户原文在语义真值层保留，展示层只做凭据遮罩。
- SessionDB 通过平台消息 ID 幂等保存，完成轮次不重复写 user。
- Artifact 通过 `session_id + artifact_id` 授权；导入文本中的 `MEDIA:` 不建立权限。
- 注册产物不再受 24 小时缓存清理影响；清空/删除进入 7 天回收期。
- 迁移默认 dry-run，Apply 前备份，失败按批次回滚。
- 真实外部图片 Provider 未调用；当前 Electron 图片验收使用安全本地夹具。

## 发布门禁

| 门禁 | 状态 |
|---|---|
| 本次上下文、幂等消息、Artifact 和公共投影契约 | 已验证 |
| WebUI 分支新增失败为 0 | 已验证 |
| Agent 已归因文件的分支新增失败为 0 | 已验证 |
| 三条 Electron 主路径及来源/截图审计 | 已验证 |
| P0/P1 分支主路径 | `0 / 0` |
| WebUI 全量零失败或逐项批准豁免 | **未通过：168 个共同失败** |
| Agent 全量零失败 | **未通过：49 failed + 1 文件超时** |
| 用户日常主检出已合并/部署本分支 | **未执行** |
| 真实外部图片 Provider 在线端到端 | 未验证 |
| VoiceOver、axe/Lighthouse、200% 缩放 | 未验证 |

## 下一步

1. 对 WebUI 168 个共同失败和 Agent 49 个共同/环境失败建立仓库级基线处置清单，修复或逐项审批豁免。
2. 拆分或优化 `tests/run_agent/test_run_agent.py`，让正式 600 秒文件级门禁能够完成。
3. 审核并合并隔离分支，再从用户日常启动入口执行升级后复验；当前不自动合并、不 push。
4. 使用真实已配置图片 Provider 完成在线生成、刷新、重启和 25 小时清理模拟。
5. 补做 VoiceOver、axe/Lighthouse 与 200% 缩放专项后，才能把“带限制完成”提升为“可发布”。
