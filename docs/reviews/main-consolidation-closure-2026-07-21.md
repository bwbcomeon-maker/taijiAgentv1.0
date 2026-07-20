# Main 最终整合与清理闭环审计

**日期：** 2026-07-21

**正式仓库：** `/Users/bwb/Documents/工作/taiji-agentv1.0`

**正式分支：** `main`

**本报告基线：** `e7e54064b1b3637a17ab222480a31d2b562f9e78`

**当前状态：** 有效成果已整合、产品与验收门禁已完成；破坏性清理将在本报告提交和恢复引用复核后执行。

## 1. 第一性原理结论

这次长期混乱不是“Git 合并慢”，而是以下事实被混为一谈：

1. 分支里有代码，不等于正式 `main` 已包含。
2. `main` 有代码，不等于正在运行、打包或截图的进程来自该 `main`。
3. 测试显示绿色，不等于测试自身不存在串源、残留结果或假绿。
4. worktree 干净，不等于分支内容仍然有效；旧实现可能已被 `main` 的后续实现演进替代。
5. 删除分支、worktree 或悬空对象前没有恢复引用，就无法证明清理可回滚。

因此闭环顺序必须是：

**冻结活跃任务 → 只读盘点 → 建立恢复引用 → 逐提交语义审计 → 整合到正式 `main` → 全量回归 → 真实 Electron 与对抗性门禁 → 归档证据 → 删除旧分支/worktree → 最终复核。**

本轮没有执行 `fetch`、`push`、远端分支删除、`git gc` 或 `git prune`。

## 2. 本轮进入正式 main 的提交

| 提交 | 作用 | 边界 |
|---|---|---|
| `39e5771c` | 补齐 ZAI 视觉与国产图片生成 Provider 的命名凭据元数据、默认凭据推断和运行态绑定 | 产品代码 |
| `812ccb6e` | 定义持久服务的源码、运行目录与 LaunchAgent 生命周期边界 | 项目规则 |
| `15519968` | LSP 子进程先等待自然退出，再按 TERM/KILL 分级清理 | Agent 产品代码与测试 |
| `8b9c859e` | 建立图片能力真实 Electron 验收、网络隔离、来源指纹和进程身份清理 | 验收工具 |
| `e7e54064` | 关闭 Electron 验收中的 CAS、幂等、串源、旧结果、终态 console、外联和 late-child 假绿 | 验收工具与专门测试 |

## 3. 旧图片能力分支的五个提交

待清理分支：

`codex/image-capability-center@d2e74b85d5ff9bf8978b1e4dcb47fd306b94f246`

恢复引用：

`refs/backup/pre-cleanup-20260720/heads/codex/image-capability-center`

| 旧提交 | 原始意图 | 实时语义判定 | 处置 |
|---|---|---|---|
| `d1b65c51` | Provider 凭据与安全通用化 | 主体已被 `00d428ef` 及后续安全实现演进；但 WebUI 命名凭据元数据曾遗漏 ZAI/国产生图族 | 只将遗漏以 `39e5771c` 最小补丁整合，不整段合并 |
| `c581bd3f` | 能力路由即时一致 | 当前 `main` 已有更新的 Agent/WebUI 运行态绑定和 fail-closed 路由测试 | 判为已演进替代 |
| `f8fb1d56` | 生图意图、流式 Artifact | 当前 `main` 已有图片 Artifact、流事件、历史重试与安全公共投影的后续实现 | 判为已演进替代 |
| `512c8231` | 通用配置和 Artifact UI | 当前 `main` 已有正式模型配置 UI、静态资源和 Electron 可见入口 | 判为已演进替代 |
| `d2e74b85` | 统一配置与路由收口 | 是前四项的旧聚合头；整体合并会把 98 个后续 main 提交上的实现拉回旧版本 | 保留恢复引用后删除本地分支/worktree |

这里纠正一条历史结论：旧报告曾称五个提交的有效能力均已 hand-port，P1 为 0。2026-07-20 的重新语义审计证明该结论不完整；命名凭据元数据确有一个 P1 缺口，现已由 `39e5771c` 修复并通过 WebUI 全量与真实 Electron 验证。

## 4. LSP 全量回归竞态

正式 Agent 全量首轮结果：

- 1,292 个测试文件；
- 28,367 通过；
- 2 失败；
- 两项失败均在 `tests/agent/lsp/test_client_e2e.py` 的 shutdown 清理；
- 失败表现为 live-system guard 拒绝向当时不属于测试子树的 PID 发送信号。

根因不是业务逻辑断言，而是 `_cleanup_process` 在已经发送 LSP `exit` 后立即 `terminate()`，与子进程自然退出、父子关系变化和 PID 状态观察形成 TOCTOU 竞态。不能把“PID 一定复用”写成已证明事实；已证明的是清理时序没有给自然退出留出窗口。

修复：

- 先有界等待自然退出；
- 再发送 TERM 并等待；
- 最后才允许 KILL；
- 每一阶段检查 `returncode`；
- 新增自然退出、TERM、KILL 三条测试。

验证：

- LSP 目录 146 项通过；
- 对抗性并发 3 轮、每轮 20 workers，0 失败；
- 最终 Agent 全量：1,293 个文件、28,372 通过、0 失败，504.9 秒。

## 5. 持久服务与 LaunchAgent 根因

旧 `ai.hermes.gateway` LaunchAgent 长期从 Documents 工作区直接启动源码。macOS TCC/xpcproxy 拒绝该后台上下文访问 Documents，服务以 `EX_CONFIG 78` 约每 10 秒重试，累计约 37,000 次。

处置：

- plist 已备份，SHA-256 为 `cf849025e9c46096a9ab7b976f5e3d2ae5b36075c366fcf55a363749bf4b24b2`；
- 经用户明确授权执行 `launchctl bootout gui/501/ai.hermes.gateway`；
- plist 已移出 `~/Library/LaunchAgents`；
- 服务、plist、日志增长和端口 18642/18643/18787 均复核为不存在或空闲。

长期规则要求：持久后台服务不能直接依赖 Documents 中的活跃 Git worktree；必须使用明确安装目录/运行目录、固定提交来源、独立日志和可撤销安装流程。

## 6. 自动化验证

### 6.1 WebUI

- 全量：9,252 passed、14 skipped、3 xpassed、1 warning、0 failed，359.75 秒。
- 图片能力专项：469 passed。
- Agent 受影响专项：78 passed。
- 隔离 Playwright Chromium 的桌面与移动 smoke 通过。

### 6.2 Agent

- 最终全量：1,293 个测试文件、28,372 passed、0 failed，504.9 秒，20 workers。
- 总收集数 28,622；runner 汇总没有把非执行项误写成通过。

### 6.3 根目录与桌面

- 根目录六个 unittest 模块：151 tests，OK。
- Desktop Node：13 passed。
- Installed Electron acceptance Node：26 passed。
- Target evidence Python：8 tests，OK。
- Electron provenance Node：8 passed。
- 最终 Electron/harness 联合 Node：44 passed。
- Git 来源门禁在六个污染定位变量同时注入时仍识别正式 primary `main`；对应 11 项测试通过。

## 7. Electron 验收的对抗性修复

初版真实 Electron 一度显示通过，但两轮独立审查共发现八类 P1 假绿：

1. fixture 只校验 revision/request_id 外形，没有实施 CAS 等值和幂等冲突。
2. 没有强制正式、干净、全程稳定的 primary `main`。
3. console 白名单只在流程中途检查。
4. 主进程、Shell、窗口和 Python 探针可掩盖产品自己的额外外联。
5. 旧输出目录中的 `passed` JSON 在前置失败时可能残留。
6. 关闭阶段只采集一次 late child，可能漏掉更晚进程。
7. fixture revision 固定，连续成功 mutation 没有推进 CAS。
8. 页面端口没有证明由已验明的 WebUI PID 独占监听，可能串到旧本地服务。

修复后的门禁包括：

- 规范状态哈希驱动的单调 revision；
- stale revision 拒绝且不推进；
- 同 UUID 同 payload 重放，同 UUID 不同 payload 冲突；
- 开始前清除旧 canonical/temporary passed 结果，终态后原子写入；
- 清除六类 Git 定位变量，强制正式 primary `main`、branch、clean、起止 commit/status/关键文件哈希稳定；
- 外联事件按 PID、role、type、target hash 精确校验；
- 除每角色一次主动自检外，任何额外 public network、Shell、window、navigation、redirect 都失败；
- page origin 端口必须由已核验 WebUI PID 唯一监听；
- 关闭后把严格验证的 guard-loaded PID 纳入所有权，连续两次有界稳定快照均为空才通过；
- console 在最终 `passed` 前再次精确复核；
- 清理只向 PID、启动时间、command、cwd 身份均未变化的自有进程发信号。

对抗过程中三轮失败均被正确拦截，没有写出假 `passed`：

- 终态 guard 白名单不匹配；
- 生成的 POSIX wrapper 语法错误；
- 关闭阶段晚到 Agent 解释器重复执行主动探针。

最终正式证据：

- commit：`e7e54064b1b3637a17ab222480a31d2b562f9e78`
- branch：`main`
- dirty：`false`
- checkout：`formal_main_primary_worktree`
- 验收脚本：正式根目录，SHA-256 `d72be9a09ab9c5701b1f82c7d1aa48ebaa45d5d717c36a1b961629abfab38cee`
- WebUI 监听：`127.0.0.1:18787` 的唯一 owner 等于已验明 WebUI PID
- route violation、page error、renderer external request、popup：均为空
- console：只有预期的 family mismatch HTTP 400
- 进程清理：连续 2 次 clean；live owned、live guard-loaded、baseline delta 均为空
- 640px 保存按钮：可见、启用、可聚焦且位于视口内

可读证据目录：

`/Users/bwb/Documents/工作/taiji-agentv1.0-backups/20260720-main-consolidation-final/evidence/formal-main-image-capability-electron-e7e54064`

对抗失败、中间成功和历史 JUnit 归档：

`/Users/bwb/Documents/工作/taiji-agentv1.0-backups/20260720-main-consolidation-final/evidence/main-closure-adversarial-20260721.tar.gz`

归档 SHA-256：

`eab4e15f2e4c973f62a9c47ce91a19db44760e21e4669de163d0203596d9c7b0`

## 8. 恢复引用与悬空对象

关键引用：

- `refs/backup/20260720-215747/pre-closure-main`
- `refs/backup/20260721/pre-electron-terminal-gates-main`
- `refs/backup/20260721/main-consolidation-closure`
- `refs/backup/pre-cleanup-20260720/heads/codex/image-capability-center`
- `refs/backup/20260720-pre-cleanup-unreachable/<short-sha>`：6 个原 unreachable commit

新增保护的六个提交：

- `21d4d0a1`
- `36b2d10e`
- `5458fccc`
- `88112d10`
- `a9115f09`
- `c1405227`

它们都能在 `main` 找到同主题的后续实现，但稳定 patch-id 不同，不能声称字节或补丁等价。因此只建立恢复引用，不执行对象回收。

## 9. 项目与长期规则

项目 `AGENTS.md` 已要求：

- 正式 `main` 是日常启动、打包、发布唯一真相源；
- 分支实现必须报作“分支已实现”，直到整合进正式 `main` 并复验；
- 工作开始和结束都要检查 branch/worktree/status/source；
- 活跃 worktree 和进程受保护；
- 清理前创建 `refs/backup/`，至少保留到下一次完整发布验证后 30 天；
- 禁止未经审计的 `git gc --prune`；
- 持久服务不得直接从 Documents 活跃 worktree 启动。

全局长期规则没有直接修改生成型 `MEMORY.md`。按记忆更新机制写入：

- `/Users/bwb/.codex/memories/extensions/ad_hoc/notes/20260720-200500-canonical-main-worktree-lifecycle.md`
- `/Users/bwb/.codex/memories/extensions/ad_hoc/notes/20260720-persistent-service-runtime-boundary.md`

## 10. 《前端 UX QA 报告》

**状态：通过，但带明确外部边界。**

- P0：0。
- P1：0。
- 已验证：正式 Electron 的设置入口、模型配置可见性、命名凭据创建、刷新后绑定恢复、密钥不回显、错误反馈、键盘操作、640px 窄屏保存入口。
- 已人眼复核：三张正式 `main` PNG，未见黑屏、主流程不可达或保存入口遮挡。
- 已验证：无意外 popup、外部导航、renderer 公网请求和 page error。
- 未验证：真实 Provider 鉴权与生图请求。
- 未验证：真实 OAuth 完成。
- 未验证：真实后端持久化/加密；Electron 使用 renderer-only 安全 fixture，后端契约由配套 Python 测试覆盖。
- 未验证：axe、VoiceOver、非 Chromium 浏览器、跨 DPI 视觉回归。
- 未验证：Kylin/UOS 目标真机安装与桌面验收。

## 11. 清理执行门禁

本报告提交后，只有在以下条件仍成立时才执行删除：

1. 正式 `main` 和三个待清理 worktree 均为预期提交且干净。
2. 无相关测试、Electron、Agent、WebUI 或构建进程。
3. 端口 18642、18643、18787 空闲。
4. 上述恢复引用可解析。
5. 最终 Electron 和对抗性归档的 SHA-256 复核通过。

待执行：

- 删除 `codex/lsp-cleanup-hardening` worktree/branch；
- 删除 `codex/main-consolidation-closure` worktree/branch；
- 删除 `codex/image-capability-center` worktree/branch；
- 删除已归档 `.worktrees/qa-evidence` 和 `.DS_Store`；
- 精确删除已归档的 Electron、pytest 与旧 WebUI 基线临时目录；
- 再次复核 branch/worktree/status/fsck/process/port/LaunchAgent。

## 12. 剩余外部风险

1. 本轮未联网刷新远端，也未 push；`origin/main` 是本机缓存快照。
2. 真实 Provider、OAuth、后端持久化加密、Kylin/UOS 真机仍是独立发布门禁。
3. backup refs 和残留 tree/blob 是有意保留的恢复面，保留期内不得 prune。
4. 用户真实日常 runtime/config 未做无隔离覆盖；本轮真实 Electron 使用隔离临时 user data 和安全 fixture。

