# Main 全量整合、清理与发布门禁审计

**日期：** 2026-07-20

**正式仓库：** `/Users/bwb/Documents/工作/taiji-agentv1.0`

**正式分支：** `main`

**产品整合提交：** `00d428efa0eb802840dfa335ac4dfc3be8c25599`

**证据来源修复提交：** `3ca3beb0ed76913a950d02eb13eca3d64f7291e2`

**结论：** `main` 已包含本轮审计确认的有效且非重复成果；已合并、重复或废弃的临时分支和 worktree 已在可回滚备份后清理。仍有一个由活跃进程占用的 `codex/image-capability-center` worktree 按保护规则保留，不能把它报成“已经清理”。

## 1. 第一性原理与根因

这次问题不是一次普通合并冲突，而是五个基础约束没有同时成立：

1. **真相源不唯一。** 正式根目录、功能 worktree、运行进程和发布目录可能指向不同提交；只看某个面板或测试绿灯，不能证明用户运行的是该代码。
2. **成果没有及时闭环。** 分支实现、未提交 diff、未跟踪文件和历史导入长期分散存在，导致“功能已做”与“正式 `main` 已包含”混为一谈。
3. **证据没有绑定来源。** 旧 Electron 验收脚本即使从正式 `main` 启动，也曾把 `desktop_app_source` 固定写成 `isolated_worktree`；如果不记录分支、commit、脏状态和静态资源哈希，截图本身不能证明来源。
4. **测试环境可串库。** 显式仓库路径仍可能被 ambient `GIT_DIR`、`GIT_WORK_TREE` 等变量劫持；开发目录与实际 Git/模块来源不一致时，测试结论会失真。
5. **清理缺少先验保护。** 分支、worktree、悬空提交和未跟踪资产在语义审计、归档和校验和之前删除，会把“垃圾清理”变成不可逆的数据丢失。

因此本轮采用的顺序是：**冻结活跃任务 → 建立可回滚备份 → 全量盘点 → 逐项语义判定 → 整合到 `main` → 全量验证 → 再做清理**。任何破坏性动作都晚于证据和备份。

## 2. 正式 `main` 的整合结果

- `00d428ef` 是本轮产品能力整合提交，汇总了会话状态、Artifact、图片能力、安全投影、桌面壳、离线打包和发布门禁等经审计成果。
- `3ca3beb0` 修复 Electron 验收证据的来源判定：区分 primary/linked/detached checkout，清除六个 Git 定位环境变量，并增加“污染 ambient Git 环境后显式 repoRoot 仍优先”的对抗性测试。
- 2026-07-20 对源任务 `019f6909-4447-73e1-a2d7-fb975f89a367` 做了只读实时复核：最新任务状态已结束且有明确最终答复；其最终提交 `23770bc9`、干净状态和测试说明纳入本轮审计后，原临时分支/worktree 才被清理。
- 本轮没有执行 `fetch`、`push` 或远端分支删除。当前 `origin/main` 只是本机已有的远端跟踪快照，不代表 2026-07-20 的远端实时状态。
- 清理后的普通开发 heads 只剩 `main` 与受保护的 `codex/image-capability-center`；对应 worktree 也只剩正式根目录与受保护 worktree。`refs/backup/` 是恢复引用，不属于开发 heads。

## 3. 分支与 worktree 处置

清理前为 12 个本地 branch heads，加 3 个 detached worktree heads；所有 head 都先写入 `refs/backup/pre-cleanup-20260720/`，清单见备份目录的 `audit/pre-cleanup-refs.txt`。

### 3.1 已删除的本地临时分支

- `codex/chat-state-artifact-hardening`
- `codex/full-product-hardening`
- `codex/image-provider-credentials`
- `codex/main-consolidation-20260717`
- `codex/sale-readiness-hardening`
- `codex/taiji-desktop-uos-package`
- `codex/universal-image-capabilities`
- `hermes/hermes-468744ce`
- `local/full-import`
- `local/old-main-one-shot-import`

语义判定如下：

| 来源 | 判定 | 处置依据 |
|---|---|---|
| chat-state、image-provider、sale-readiness、UOS、旧 main | 已成为整合线祖先或已被整合提交包含 | 祖先关系、文件级差异和回归验证 |
| full-product-hardening | 少量拓扑独有内容已由 `main` 的后续实现演进替代；历史文档另行归档 | 语义对照、路径对照、归档后删除 |
| universal-image-capabilities | 有效补丁与 `main` 等价或已演进 | patch/行为等价检查 |
| local/full-import、old-main-one-shot-import | 相同旧导入快照 | 两分支同 head，且为旧源码快照 |
| image-capability-center | 5 个有效能力提交已按当前安全边界 hand-port 到整合线；原 worktree 仍被活跃进程使用 | 不整段覆盖，不清理活跃 worktree |

### 3.2 已删除的 worktree

- `/private/tmp/taiji-phase6-base-d4e06dab`
- `/private/tmp/taiji-webui-baseline-295be652`
- `.worktrees/app-desktop-qa`
- `.worktrees/chat-state-artifact-hardening`
- `.worktrees/full-product-hardening`
- `.worktrees/hermes-468744ce`
- `.worktrees/image-provider-credentials`
- `.worktrees/main-consolidation-20260717`
- `.worktrees/universal-image-capabilities`

其中 `.worktrees/app-desktop-qa` 原工作树有未提交内容，不能按普通已合并 worktree 处理。删除前已保存：

- 85 MB 完整工作树归档，SHA-256 `331176fc92440673b233493e1f52ec5f74c199e899d2c0b41cc7320359248ddb`
- 当前 tracked patch，SHA-256 `7d8fdb0c1b13a84891b1b75a0dbea7a30caf2852c9a4308456035d79bd02677d`
- 有意义的 untracked 归档，SHA-256 `41187260da9886500f8946d2a187bc60ebc278daf73c7a477bacdf70fa684c8f`
- 状态清单，SHA-256 `99db23b6a5c134144387d54605ff8ed39b4f386fabaaf418ff706e87ed380fd6`

逐文件判定为 9 项与 `main` 相同、27 项已被吸收或演进替代、3 个依赖链接和 1 个旧 launcher 属于环境/垃圾项，没有发现尚未进入 `main` 的有效产品能力。

### 3.3 唯一保留的非正式 worktree

`.worktrees/image-capability-center` 与分支 `codex/image-capability-center@d2e74b85` 被保留。最终复核时 PID `18302` 自 2026-07-16 15:55:31 起持续存活，使用正式根目录的 Python venv，但物理 cwd 位于该受保护 worktree 的 `hermes-agent`，并监听 `127.0.0.1:18643`；这是一个必须保留并显式报告的混合来源边界。本轮从未向该进程发送信号、重启它、修改该 worktree 或向对应任务发消息。

它只能在以下条件同时满足后进入后续清理：任务明确结束；最终提交与文件状态连续两次检查不变；工作树干净；相关测试、构建和服务进程退出；有效差异已证明进入 `main` 或已有归档。

## 4. 未提交、未跟踪与业务资产

- 正式根目录原 67 项未跟踪内容先迁移到 `formal-root-untracked-originals-before-main-ff/`，没有直接删除。
- 正式根目录未跟踪内容的总归档 `formal-untracked/formal-root-untracked.tar.gz` SHA-256 为 `d88541d41c39be436eac63d4844e29592dac92b7cfce083b7d638aab7ae82334`。
- 候选整合工作树的 tracked patch SHA-256 为 `58097f7a79520480052561ac3ce20f442691c332048b0a25b1fb281d40da6971`；26/26 个 untracked 文件已归档，归档 SHA-256 为 `34de82e84eaf643c5e499b49ce1a91bf4d9547e584896d346d710fbce3d2839f`。
- `演示资料包` 中可识别的业务材料保存在 `business-assets/consulting-demo-20260626/`，并带独立 `SHA256SUMS.txt`。
- Git 元数据中的异常 `.DS_Store` 已移动到 `git-metadata-quarantine/`，随后执行 `git worktree prune --verbose` 清理无效元数据入口。

## 5. 悬空对象审计

`git fsck --connectivity-only --no-progress` 通过，未发现连接性破坏或 Git 报告的 garbage 文件。本轮不运行 `git gc` 或 `git prune`。

发现的 9 个 dangling commit 已全部提升为 `refs/backup/dangling-audit-20260720/<sha>`，避免它们被不可逆回收：

| commit | 判定 |
|---|---|
| `0711e973` | 旧 Hermes Agent 导入；其 4,129 个路径全部属于 `main` 现有 4,220 个路径的子集，已被新实现替代 |
| `1e6d8fd2` | 与 `main@e865961c` patch 等价 |
| `21e99635` | 与 `main@e289dfd3` patch 等价 |
| `8add2721` | 与 `main@c046fc8c` patch 等价 |
| `849bbe28`、`95088c7f` | 图片 readiness 旧变体；路径与行为已被 `100c7028` 及后续门禁演进覆盖 |
| `a924aab7`、`fd576065` | 两者 patch-id 相同；能力运行态验证已被 `b96139b3`、`927d6505`、`63e16a1a` 及整合提交演进覆盖 |
| `ff43a270` | 旧单文件专家团实现；`main` 已演进为 `api/expert_teams/` 包、路由、测试和 UI 完整链路 |

提升 9 个 commit 后再次检查为：`0 dangling commit / 3,126 dangling tree / 44 dangling blob`；`git count-objects -vH` 报告 `garbage: 0`。仍存在的 tree/blob 属于对象库历史残留，因保守恢复策略而暂不回收；本轮没有从中发现可判定为待合并提交的证据，但仍应保守保留，不得未经下一次发布审计就强制清除。

## 6. 可回滚保护

备份根目录：

`/Users/bwb/Documents/工作/taiji-agentv1.0-backups/20260720-main-consolidation-final`

关键保护：

- `refs/backup/main-before-consolidation-20260720` → `8e894296`
- `refs/backup/formal-root-before-consolidation-20260720` → `d4e06dab`
- `refs/backup/candidate-before-formal-main-20260720` → `00d428ef`
- `refs/backup/pre-cleanup-20260720/...`：清理前全部 branch/worktree heads
- `refs/backup/dangling-audit-20260720/...`：9 个原 dangling commits
- 清理前 refs 清单 SHA-256：`1957bd45ecf5b89c1a8a38d71aabf90284463864dd4afdff2450793b54e0f6a7`

回滚时应优先从 `refs/backup/` 创建新的恢复分支，或在新目录解包归档后比对；不要直接重置正式 `main`，也不要覆盖仍在运行的 worktree。以上保护至少保留到下一次完整发布验证后 30 天。

## 7. 自动化与对抗性验证

证据根目录：

`/Users/bwb/Documents/工作/taiji-agentv1.0-backups/20260720-main-consolidation-final/evidence`

| 门禁 | 实时结果 | 关键证据 SHA-256 |
|---|---|---|
| 正式源码来源门禁 | 通过，`main@3ca3beb0`、正式 primary worktree、干净来源 | `cb1e49586d921ff16c3976be9226fbcde2554d97342132b3dfeba5dbfc4fd3fd` |
| 根目录 unittest | 225/225 通过，112.067s | `7e99bfe08fcf6adafea1787158ab6c38cf2a78da06e1d337d55f8c0482f3884b` |
| WebUI 全量 | 9,236 通过、14 skipped、3 xpassed、0 failed，375.74s | log `9fe3bcb1da643140eb8f82c428ead8a14eb1f550bd4a1fbb9a9a14c83d4a552c`；XML `0845aeb126b5fd91e864914199098d9ee43545c1faa0a3d37e6c72c29f471b5c` |
| Agent 全量 | 28,366 通过、131 skipped、119 deselected、0 failed，517.8s | log `c51db470cb28211cb94fc91bde3202d9bc8d78bc6157ad6c793644e5cc32528b`；XML `9e03931416acb017952ef42b4acc4c596cb8b5eda7bc323e7c6f5fd5cb4d3f73` |
| DOCX 全量隔离重跑 | 258/258 通过，100,843ms | `2652195dbe6a76fd8ce84f31569495c60d34af1afcdbaeda23820dbe97d39b2e` |
| DOCX stale-lock 压测 | 目标用例连续 50/50 通过 | `76f9164cd46904061562507700236f9bba99389303c81deda1e65908909bb506` |
| Desktop/acceptance Node | 39/39 通过 | `0f1d89f290d8dbfb5a556e48157699efc1b0c5a524cd4198397826bb3e16090a` |
| Electron provenance Node | 8/8 通过，覆盖 primary/linked/detached 与污染 Git 环境 | `7fa146142d3db0d0a870037e69de0533b0779272a17b4afe22000e841bf4b814` |

测试提交归属需要严格区分：根目录 225 项、WebUI 9,236 项和 Agent 28,366 项全量日志对应产品整合头 `00d428ef`；`3ca3beb0` 只修改 4 个 Electron 测试/证据 JavaScript 文件，没有改变产品静态文件或 Agent 产品代码。该后续提交由 8/8 provenance Node、39/39 Desktop/acceptance Node，以及 Chat/Artifact 与 worktree public contract 两条真实 Electron 重跑覆盖，不能误称为在 `3ca3beb0` 上重新跑了 28,000 余项 Agent 全量。

WebUI 全量在显式外部网络隔离下运行。浏览器验证使用隔离 Playwright Chromium，未控制系统默认 Chrome，也未打开真实 OAuth/xAI 登录页：

- `/`、`/#settings`、`/#sessions` 基本 smoke 无 console error，log SHA-256 `23f843e4f523defd9c78987015ab50126c5e1c4f63aef01e4c90daed3c9e77aa`
- 图片能力桌面/移动端 smoke 通过，log SHA-256 `91707d722dc0cc73dd2c1a66ff54fdae6f61dee4607d061f5c9aa6aa0e87f045`
- 项目过滤与 Profile 卡键盘路径通过，log SHA-256 `2905cad3b8f549f97e5f5e41796078b706957471a81c2d69a2e74937584cce27`

对抗性门禁还覆盖：污染 Git 环境变量、无外部网络、路径/来源指纹、DOCX stale lock 并发、跨会话 Artifact 权限、内部路径公共投影、窄屏与键盘操作。失败过的并发 DOCX 首轮没有被隐藏：它被定位为 stale-lock 时序问题，随后先跑专项 50 次，再做 258 项全量隔离重跑。

## 8. 真实 Electron 与运行目录证据

来源修复后的两条真实 Electron 主路径都记录为：

- branch：`main`
- commit：`3ca3beb0ed76913a950d02eb13eca3d64f7291e2`
- dirty：`false`
- checkout/source：`formal_main_primary_worktree`
- user data：隔离临时目录
- 静态资源：逐文件 SHA-256 指纹

实际 Electron 入口来自正式根目录 `apps/taiji-desktop/src/main.js`，WebUI 和 Agent 源码目录分别固定在正式根目录的 `hermes-local-lab/sources/hermes-webui` 与 `hermes-local-lab/sources/hermes-agent`。但验收使用 `sanitized_daily_equivalent_fixture` / `sanitized_daily_nav_equivalent_fixture` 配置与隔离临时 user data/runtime；因此它证明的是正式源码入口和日常可见性等价契约，不是对用户真实日常 runtime/config 的无隔离复验。验收结果记录了 Electron/Agent/WebUI 自有 PID 和清理边界；Python 解释器由测试明确传入，但结果 JSON 未持久化其绝对路径，故绝对解释器路径标记为未验证。

结果：

- Chat/Artifact：`passed_with_provider_fixture`，10 张截图，run log SHA-256 `331719973d437e0de74b8b320fb25c738ab760c7f0bb3a987973e3fa59c0f427`，结果 JSON SHA-256 `e3c0bda9951f11102a77acda923b0ce61772e771fd0c08cce66a41ce99643bce`。
- Worktree public contract：`passed`，4 张截图，公共 DOM/API 路径匹配数均为 0，run log SHA-256 `ea4c7aabf41f9a347464ae71fe2b2b7d9a9bcf910b167a381ec8abc69927c846`，结果 JSON SHA-256 `967d1623483a7b4301dcba281c5c45f0d922a85a2aa5edb63effa4c62777fa67`。
- Session migration：`passed`，6 张截图，Electron、服务 PID 与临时目录均完成清理；结果 JSON SHA-256 `f10a3ee002f37af3b735ad295818ba209d46631bf6345784d3fb5d6ca9ebf33c`。这条证据的 `source_fingerprint.commit` 是 `00d428ef`；`3ca3beb0` 未改产品静态文件，结果中的静态 SHA 与后续两条重跑一致，但不能把该迁移运行改报为 `3ca3beb0`。

验收后没有残留本轮 Electron 或自有测试服务进程；受保护 PID `18302` 的启动时间、cwd 和监听端口未被改变。

## 9. 《前端 UX QA 报告》

**状态：带限制完成。**

- P0：0。
- P1：0。
- 桌面宽度、640px 窄屏、图片加载/缺失/重试、灯箱、下载、历史图片重试、刷新/重启恢复、滚动锚点、会话清理提示和 worktree 公共投影已实际验证。
- 键盘路径实际覆盖 Tab、Enter、Space、Escape；截图 sanity 拒绝过小或黑屏证据。
- 代表性截图人工检查未发现黑屏、遮挡或主流程不可达。
- P2：中文界面中仍混有 `git worktree`、`Worktree 标识` 以及模型/Provider 技术名称；不阻断主流程，但可在后续文案迭代统一。
- 未验证：真实外部图片 Provider 调用。本轮 Electron 使用安全本地 fixture，结果文件明确记录 `provider_realtime_verified=false`。
- 未验证：真实 Kylin/UOS 目标终端的新一轮安装和桌面验收。本轮只证明 macOS 正式源码、自动化、离线打包逻辑和本地 Electron 门禁，不等于目标机发布批准。
- 未验证：屏幕阅读器、axe/完整 WCAG、非 Chromium 浏览器与跨 DPI 视觉回归；已执行的键盘路径和截图检查不能替代完整可访问性认证。

## 10. 规则沉淀

- 项目级 `AGENTS.md` 已补充：显式 repoRoot 必须压过六类 ambient Git 定位变量，并用污染环境对抗测试证明；清理前恢复引用统一放入 `refs/backup/`，至少保留到下一次完整发布验证后 30 天，未经审计禁止 `git gc --prune`。
- 全局长期规则没有直接修改生成型 `MEMORY.md`，而是按记忆更新机制写入 `/Users/bwb/.codex/memories/extensions/ad_hoc/notes/20260720-200500-canonical-main-worktree-lifecycle.md`，请求长期系统吸收 canonical main、worktree 保护、来源指纹、备份清理和隔离浏览器规则。该 note 已落盘，但生成型记忆何时重新编译不在本轮验证范围内。

## 11. 剩余风险与后续动作

1. `codex/image-capability-center` 及其 worktree 仍由活跃 PID 占用；它是受保护例外，不是垃圾。进程结束后按双快照稳定性门禁再次审计，再决定整合/归档/删除。
2. 本轮没有联网刷新远端，也没有 push；如需发布，须在用户明确授权后 fetch，审计新远端 refs，确认无新分叉，再单独执行 push。
3. WebUI 的 14 个 skip、3 个 xpass，以及 Agent 的 131 个 skip、119 个 deselected 已如实保留，不能解释成“零跳过”。
4. 外部 Provider 与 Kylin/UOS 真机仍是发布前独立门禁，不能由 fixture、macOS Electron 或静态包检查替代。
5. dangling tree/blob 和 `refs/backup/` 是有意保留的恢复面；未到保留期且未经复审，不运行破坏性对象回收。

本轮可宣称的是：**正式 `main` 的有效成果整合、可清理分支/worktree 的安全收口、来源一致性修复及本机完整回归已经完成；远端发布、真实 Provider、Kylin/UOS 真机，以及仍在运行的受保护 worktree 尚未完成。**
