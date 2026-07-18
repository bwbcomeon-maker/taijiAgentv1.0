# 太极 Agent 主干整合与清理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已在多个分支实现但尚未进入日常运行入口的会话安全、图片能力、运行租约、授权、诊断、DOCX 和 Linux 交付能力，按可回滚、可验证、不中断日常应用的方式收敛到唯一主干。

**Architecture:** 以 `codex/chat-state-artifact-hardening@23770bc9` 为整合基线，保留它的公共投影、exact-once、迁移屏障和 `ArtifactRegistry` 作为数据真值层；其它分支只按提交簇移植，不做整分支合并。每一簇独立提交、独立测试，最终在隔离 Electron 和真实服务验收通过后，才允许用 `--ff-only` 更新 `main`。

**Tech Stack:** Git worktree、Python/pytest、Hermes Agent Gateway、Hermes WebUI、原生 JavaScript/HTML/CSS、Electron、Node.js、DOCX Engine v2、Shell、Debian/Kylin/UOS 离线打包链。

---

## 0. 已锁定基线、备份和非目标

### 0.1 冻结点

- 用户日常主检出：
  `/Users/bwb/Documents/工作/taiji-agentv1.0`
- 日常主检出冻结提交：
  `codex/sale-readiness-hardening@d4e06daba7247643a0532ceace91d1bdb2391c71`
- 本计划整合分支：
  `codex/main-consolidation-20260717`
- 本计划整合 worktree：
  `/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/main-consolidation-20260717`
- 整合起点：
  `codex/chat-state-artifact-hardening@23770bc9c433725beb81bdf64f25ff6cadafc5e7`

### 0.2 不可破坏回滚备份

有效备份固定为：

```text
/Users/bwb/Documents/工作/taiji-agentv1.0-backups/20260717-163359-rollback-freeze-d4e06daba724
```

备份门禁：

- 完整 Git 常规文件：`5695` 个，源/副本 SHA256 零差异。
- `git fsck --full`：退出码 `0`，源/副本输出零差异。
- 无 reflog 时的 `94` 个悬空 commit 已全部保留并可读取。
- 根目录 `66` 个未跟踪文件已归档。
- `.worktrees/app-desktop-qa` 的 `31` 个 tracked 变更、`9` 个未跟踪文件和 `3` 个符号链接已归档。
- 全局 SHA256 清单覆盖 `5790` 个文件，校验全部成功。
- `SHA256SUMS` 自身哈希：
  `be7f0bb32cf709a6e75741c6672707b59049e5d20f67b79fe1a3c5d23ad65c49`

每次进入新的高风险阶段前执行：

```bash
BACKUP=/Users/bwb/Documents/工作/taiji-agentv1.0-backups/20260717-163359-rollback-freeze-d4e06daba724
cd "$BACKUP"
shasum -a 256 -c SHA256SUMS.sha256
shasum -a 256 -c SHA256SUMS
```

预期：

- `SHA256SUMS: OK`
- 其余 `5790` 项均为 `OK`

### 0.3 回滚规则

1. 每一提交簇只在整合 worktree 中操作，用户日常主检出不参与 cherry-pick。
2. 每个阶段通过测试后立即形成独立本地 commit。
3. 尚未提交时失败：只允许在整合 worktree 中执行
   `git cherry-pick --abort`，然后确认 `git status --short` 为空。
4. 已提交阶段需要撤回：使用 `git revert` 生成可审计反向提交，不重写已验证历史。
5. 只有 Git 仓库元数据整体损坏时，才考虑从冻结备份恢复 `.git`；恢复前必须停止所有 Git/Codex 写入并取得用户明确确认。
6. 根目录未跟踪文件和脏 QA worktree 的恢复，只能从对应 tar/patch 定向恢复，不得整仓覆盖。

### 0.4 本轮非目标

- 不直接更新 `main`。
- 不 push 任何 remote。
- 不重启、终止或热切换用户日常 Electron、Gateway、WebUI。
- 不清理任何 worktree、分支、悬空对象、未跟踪文件或旧交付包。
- 不把历史测试报告直接当成新整合提交的通过证据。
- 不把 AccessKey/Secret、OAuth 或 Service Account 伪装成已经支持的 API Key。

## 1. 分支和 worktree 裁决

| 分支或工作树 | 当前身份 | 裁决 | 原因 |
|---|---|---|---|
| `main` / `origin/main` | `8e894296` | 暂不更新 | 比日常主检出落后，不能作为当前实现真值 |
| `codex/sale-readiness-hardening` | `d4e06dab` | 保持日常运行 | 当前用户实际入口，整合期间不改 |
| `codex/chat-state-artifact-hardening` | `23770bc9` | 整合基线 | 已包含公共投影、exact-once、持久 Artifact、资源包和迁移屏障 |
| `codex/main-consolidation-20260717` | 从 `23770bc9` 创建 | 唯一整合线 | 所有新移植、测试和证据只进入这里 |
| `codex/image-capability-center` | `d2e74b85` | 分层移植 | 功能完整，但图片产物协议与 chat-state 冲突 |
| `codex/universal-image-capabilities` | `09711e3b` | 不再移植 | 四个提交已有 patch-equivalent 后继 |
| `codex/image-provider-credentials` | `e289dfd3` | 不再移植 | 已是 sale 分支祖先 |
| `codex/full-product-hardening` | `3a76fec3` | 按簇移植 | 与当前主线双向大幅分叉，禁止整分支合并 |
| `codex/taiji-desktop-uos-package` | `521dd091` | 不再移植 | 已是 sale 分支祖先 |
| `.worktrees/app-desktop-qa` | detached `babe002e`，脏 | 只作冻结证据 | 不是源码真值，禁止从中复制整树或提交 |
| `94` 个悬空提交 | 已冻结 | 不进入整合 | `85` 个有等价实现，其余是旧中间态或上游全量快照 |

禁止执行：

```bash
git merge codex/full-product-hardening
git merge codex/image-capability-center
git merge codex/universal-image-capabilities
git add .worktrees/app-desktop-qa
git clean -fdx
git gc
git prune
```

## 2. 提交分组和移植顺序

### 2.1 第 A 组：Managed Runs 与生产授权

按以下顺序移植：

1. `540f410b` — `fix: make managed runs session-safe`
2. `8da8caa1` — `fix: serialize managed runs across processes`
3. `18f2427c` — `fix: make production licensing build-profile controlled`

保留目标：

- `/v1/runs` 多轮 session 连续性。
- SQLite 跨进程 managed-run lease。
- chat-state 的消息 exact-once 和迁移 worker lease 不被覆盖。
- build profile 控制生产授权。
- CLI、one-shot、Gateway、WebUI 和最终 Agent 执行点全部保留授权守卫。

### 2.2 第 B 组：统一 Provider、凭据和能力路由

`d1b65c51` 与 `c581bd3f` 只作为审计输入，不再作为可直接
cherry-pick 的移植单元。两者触及的 Provider、配置、下载、流式会话和
Agent cache 已被当前整合基线继续演进；直接套用会覆盖当前更强的
Artifact/session 授权、DNS 固定连接、inode/symlink 校验和原子写入语义。

Task B 改为四个分别复审、分别提交的 TDD hand-port，依赖顺序固定为
B1→B2→B3→B4：

1. **B1：Provider alias、family 与模型 fail-closed（不读取 Secret）。**
2. **B2：自定义生图/识图凭据与网络传输加固。**
3. **B3：版本化验证、运行时执行门禁与保留扩展工具的 schema refresh。**
4. **B4：WebUI streaming/routing/cache 与 CLI、Gateway、TUI 的一致性。**

四项必须分别先运行并落盘 must-first RED 测试证据，再做最小 GREEN 实现；
产品 commit 只能在独立规格复审和质量复审通过后形成。详细风险、
27 类 RED 测试和 hand-port 边界见
`docs/reviews/main-consolidation-task-b-source-audit-2026-07-18.md`。

最终保留目标：

- Secret 只存本机 canonical credential env，配置只存 `credential_ref`；
  任何显式引用缺失、family 不匹配或未知模型都 fail-closed。
- `public_direct`、`private_direct`、`trusted_proxy` 是互斥的显式网络范围；
  metadata/link-local 永久禁止，`198.18.0.0/15` Fake-IP 明确失败。
  `trusted_proxy` 是外部受控策略边界，只能引用预先批准且具备远端
  DNS/IP 分类能力声明的 named proxy profile；标准 CONNECT 下应用不声称能
  独立证明 proxy 远端解析出的 IP。
- direct 自定义 endpoint 的 DNS 解析、实际连接、TLS hostname 与 peer 必须
  绑定，不允许预检后由 SDK 二次解析；trusted-proxy endpoint 按 named
  profile 的 proxy-side DNS/IP policy 与应用侧 origin TLS 校验分工。
- 验证状态、fingerprint、tool cache 和 Agent schema 使用同一版本化能力快照；
  配置变更后的下一轮和实际工具调用都不接受 stale `image_generate`。
- 主模型原生视觉、已验证辅助视觉和生图路径在 WebUI、CLI、Gateway、TUI
  使用相同 reason code 与 fail-closed 裁决。

### 2.3 第 C 组：生图意图和会话产物

来源提交：

1. `f8fb1d56` — `feat(images): route generation intent and stream artifacts`

这个提交禁止直接作为最终产物层真值。只移植：

- `agent/image_intent.py` 的明确生图、明确非生图、模糊意图分类。
- 单轮只创建一个生图任务的约束。
- `image_generate` 工具结果到现有 ArtifactRegistry 的适配；Task C 只消费
  B4 已建立的 `capability_route` 事件契约，不创建、重定义或重复发送该事件。
- Agent 将远程图片先缓存到受信生成目录的行为。

明确舍弃：

- `hermes-local-lab/sources/hermes-webui/api/image_artifacts.py`
- `/api/image-artifacts/<session>/<id>`
- `message.image_artifacts`
- 任何“文件存在即授权”的产物读取方式。

所有生图结果必须进入现有：

- `api.artifacts.ArtifactRegistry`
- `ingest_image_artifact_candidates(...)`
- `message.artifacts`
- `/api/media?session_id=<id>&artifact_id=<id>`

### 2.4 第 D 组：统一图片配置 UI 和路由状态

按以下顺序移植：

1. `512c8231` — `feat(images): expose universal configuration and artifacts`
2. `d2e74b85` — `feat(images): unify capability configuration and routing`

只保留最终统一形态：

- `GET /api/image-capabilities`
- `POST /api/image-capabilities/configure`
- 两项能力卡：看图识别、生成图片。
- 一个“保存并验证”主动作。
- 阿里百炼是推荐平台选项，不再拥有独立永久快速配置卡。
- 已保存、已验证、当前实际路由三个状态分别显示。
- 前端只展示服务端元数据中 `support_level` 已开放的平台。

不得重新引入：

- “阿里云百炼快速配置”永久大卡。
- 静态“国产图片模型模板”名称条。
- 已有代码但不可调用的平台入口。
- 第二套图片产物 UI 状态。

### 2.5 第 E 组：诊断和聚焦 UX 修复

按以下子组移植，不整组 cherry-pick：

1. 专家团进度：
   - `3ebef028` — `fix: report expert progress from completed stages`
2. 定时任务术语：
   - `4a53f5c1`
   - `babe002e`
   - `7b3607a9`
   - `e9788e34`
3. 键盘和焦点：
   - `4947202d`
4. 安全产品诊断：
   - `0524a448`
   - `8f03121c`

规则：

- 先运行来源提交关联测试，确认当前基线是否仍缺该行为。
- 行为已经存在则不重复移植，只在台账记录“现状已覆盖”。
- `index.html`、`panels.js`、`ui.js`、`style.css` 只做局部合并。
- 诊断包不得包含 API Key、token、绝对隐私路径、用户正文或原始工具参数。
- 所有用户可感知能力必须有可见、可发现、可键盘访问的 UI 入口。

### 2.6 第 F 组：DOCX runtime 模板隔离与锁

按以下顺序移植：

1. `259306b3` — `feat: isolate docx runtime template state`
2. `4ecaafc5` — `fix: retry vanished registry lock races`
3. `1d56849a` — `fix: publish registry locks by generation`
4. `15c058b4` — `fix: bound registry lock retries and document delivery`

只保留：

- 内置模板只读源与可写 runtime store 隔离。
- generation lock。
- 有界重试。
- registry 原子发布。
- 对应 Node 自动化。

不直接带入 `15c058b4` 中已经过期的：

- `AGENTS.md`
- 旧 verification ledger
- 旧打包说明和旧目标机证据

当前专家团、单 DOCX 交付和模板选择契约必须保持。

### 2.7 第 G 组：Linux/Kylin/UOS 发布链

严格按以下顺序移植：

1. `99103770` — harden linux offline payload
2. `1d2f7e28` — verify package checksum sidecar
3. `77bcb526` — harden privileged offline installation
4. `4affbbff` — fail fast without desktop session
5. `394f0de7` — close privileged install race windows
6. `d749cf16` — gate release on offline install rehearsal
7. `5991c61e` — add offline rehearsal producer
8. `ea5c5599` — close desktop release evidence gaps
9. `1ccdf4fd` — keep package installs noninteractive
10. `7473da3c` — install Electron audit dependencies on builder
11. `1283467f` — clean read-only manifest payloads safely
12. `82e87d57` — make trusted build retries self-cleaning
13. `f94e13cb` — require complete offline dependency closure
14. `440372d0` — resolve offline dependencies with apt
15. `29c2cfd4` — clean readonly payload verification trees
16. `e128ebb5` — align rehearsal with build baseline
17. `7acea3ef` — compare source archives across gzip encoders
18. `3a76fec3` — harden Linux packaging rehearsal paths

旧包 `taiji-agentv1.0-kylin-build-src-584da26c.tar.gz` 永久标记为历史产物；任何源码变化后，旧 manifest、签名、离线演练和目标机截图全部失效。

### 2.8 永久排除的历史文档提交

以下提交只作历史背景，不移植：

```text
b5922d66 203b907e cd3676dd 0a3ce20d 5fd639a7
7c2e516f cd0ea0eb 4950e833 cf89c8e3 c0ba4e54
965feaa9
```

理由：它们记录的是旧提交、旧工作树或旧运行目录的计划和验收，不能证明最终整合提交通过。

## 3. 唯一 ArtifactRegistry 原则

### 3.1 唯一真值

唯一持久图片产物实现：

```text
hermes-local-lab/sources/hermes-webui/api/artifacts.py
```

唯一公共访问协议：

```text
GET /api/media?session_id=<session_id>&artifact_id=<artifact_id>
```

唯一消息字段：

```json
{
  "role": "assistant",
  "artifacts": [
    {
      "artifact_id": "public-id",
      "kind": "image",
      "mime": "image/png",
      "name": "generated-image"
    }
  ]
}
```

### 3.2 写入时序

1. Agent 生图工具返回受信 `image_ref`、SHA256 或受限 data URL。
2. WebUI streaming 收集 `_artifact_candidates`。
3. `ingest_image_artifact_candidates(...)` 去重同一 `tool_call_id`。
4. `ArtifactRegistry` 以当前 `session_id + turn_id + owner_run_id` 注册 pending 产物。
5. 产物公共描述写入最近一条 assistant 的 `message.artifacts`。
6. 先 `commit_artifacts(...)`，再和 Session save 形成一致结果。
7. save 失败时恢复消息快照并 `discard_pending_artifacts(...)`。
8. GET、SSE、刷新、重启、导出和导入都只使用公共描述。

### 3.3 必须删除或拒绝的重复实现

每次提交前运行：

```bash
WT=/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/main-consolidation-20260717
cd "$WT"
test ! -e hermes-local-lab/sources/hermes-webui/api/image_artifacts.py
! rg -n '/api/image-artifacts|message\\.image_artifacts|image_artifacts' \
  hermes-local-lab/sources/hermes-webui/api \
  hermes-local-lab/sources/hermes-webui/static
```

预期：两个命令均退出 `0`，且 `rg` 无匹配。

## 4. 冲突文件不变量

| 冲突文件或层 | 必须保留的底座 | 允许加入的内容 |
|---|---|---|
| `api/brand_privacy.py` | chat-state 公共投影、隐私意图和凭据遮罩 | 新事件字段白名单 |
| `api/gateway_chat.py` | 上下文重建、平台消息 ID、exact-once | managed-run session/lease |
| `api/routes.py` | 迁移屏障、Artifact 授权、公共错误投影 | 图片能力 API、诊断 API、授权最终守卫 |
| `api/streaming.py` | turn envelope、journal、pending/commit Artifact | 生图意图事件、`capability_route` |
| `api/artifacts.py` | 唯一 ArtifactRegistry | 新 Provider 结果的安全适配 |
| `server.py` | 只读审计、公共路由和媒体授权 | 统一图片能力路由注册 |
| `api/model_config.py` | 当前 profile/config 事务 | provider 元数据、凭据引用、双能力原子配置 |
| `api_server.py` | chat-state 请求语义 | `/v1/runs` 多轮连续性 |
| `hermes_state.py` | 当前持久状态 | 跨进程 managed-run lease |
| `messages.js` | `message.artifacts` 恢复、失败和重试 | `capability_route` 可见状态 |
| `ui.js` | chat-state 恢复和公共反馈 | 图片能力保存/验证反馈 |
| `index.html` / `panels.js` | 上方授权、主模型、其它设置区域不退化 | 统一两能力卡和诊断入口 |
| `style.css` | 当前全局设计系统 | 图片能力局部样式和可访问状态 |
| DOCX 文件 | 当前专家团、单 DOCX、模板选择契约 | runtime store 和 lock 算法 |
| `apps/taiji-desktop/src/main.js` | worktree 源码发现、隔离 userData | build-profile 授权和诊断入口 |

全局不变量：

1. API Key 不进入 API 响应、DOM、日志、诊断包、资源包或 Git。
2. `credential_ref` 与 provider family 必须匹配。
3. 保存、验证、实际运行路由是三个独立状态。
4. 配置更改后旧验证立即失效；新调用读取新版本，不要求重启。
5. 明确视觉分析不得触发生图；模糊生图意图必须确认；单轮最多一个生图任务。
6. 主模型支持视觉时走原生快路径；明确不支持时才走已启用且已验证的辅助视觉。
7. 平台列表由服务端元数据返回；未实现平台不能出现在页面。
8. SessionDB、sidecar、journal、Artifact manifest 和 public projection 保持 exact-once。
9. migration writer 排队后不得启动新 worker；运行中的 worker 正常结束、取消和异常都必须释放 lease。
10. 内置 DOCX 模板不可被 runtime 修改；runtime 更新不得写回源码树。
11. 生产授权最终守卫不能只在 UI；CLI、Gateway 和实际执行入口都要 fail-closed。
12. 不允许用大文件冲突的 `ours` 或 `theirs` 整体覆盖代替逐段合并。

## 5. 日常/占用进程 presence-aware 保护策略

### 5.1 日常进程边界

冻结记录中的日常链路目标曾是：

- Electron：来源于根目录主检出。
- Gateway：`127.0.0.1:18642`。
- WebUI：`127.0.0.1:18787`。

2026-07-18 当前实时采样与冻结记录不同：

- Electron：未运行。
- `127.0.0.1:18642`：无 listener。
- `127.0.0.1:18787`：无 listener。
- `127.0.0.1:18643`：存在不属于本整合范围的 listener；当前采样 PID/CWD
  为 `18302` /
  `/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/image-capability-center/hermes-local-lab/sources/hermes-agent`，
  只能作为当时证据，后续一律按 unknown/out-of-scope 占用进程保护，不停止、
  不复用、不发 health 请求。

该快照不是永久事实。每个 Task/子阶段都必须在开始前重新采样 `before`，结束后
采样 `after`，按 presence 分支验收：

- Electron、`18642`、`18787` 若 `before` 存在，才要求同一
  PID/CWD/listener、health 成功和 Server commit header 不变。
- 若 `before` 不存在，`after` 必须仍不存在；QA 不得绑定这些端口，也不得为了
  通过门禁启动日常服务。
- `18643` 无论身份是否可识别，都只做 listener/PID/CWD 保护；不得停止、复用或
  把它纳入 QA 服务。

整合期间禁止：

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab
./scripts/stop-all.sh
./scripts/start-agent.sh
./scripts/start-webui.sh
open "太极 Agent.command"
```

### 5.2 隔离测试环境

所有集成测试和 Electron QA 使用：

```bash
export ROOT=/Users/bwb/Documents/工作/taiji-agentv1.0
export WT="$ROOT/.worktrees/main-consolidation-20260717"
export QA_ROOT=/tmp/taiji-main-consolidation-20260717
export AGENT_ROOT="$WT/hermes-local-lab/sources/hermes-agent"
export WEBUI_ROOT="$WT/hermes-local-lab/sources/hermes-webui"
export AGENT_PY="$ROOT/hermes-local-lab/sources/hermes-agent/venv/bin/python"
export NODE_PATH="$ROOT/apps/taiji-desktop/node_modules:$ROOT/hermes-local-lab/sources/docx-engine-v2/node_modules"
export TAIJI_AGENT_ROOT="$WT/hermes-local-lab"
export TAIJI_RUNTIME_HOME="$QA_ROOT/runtime-home"
export TAIJI_WORKSPACE="$QA_ROOT/workspace"
export TAIJI_STATE_DIR="$QA_ROOT/state"
export TAIJI_AGENT_TMP_DIR="$QA_ROOT/tmp"
export TAIJI_DESKTOP_USER_DATA_DIR="$QA_ROOT/electron-user-data"
export XDG_CONFIG_HOME="$QA_ROOT/xdg-config"
export XDG_DATA_HOME="$QA_ROOT/xdg-data"
export XDG_STATE_HOME="$QA_ROOT/xdg-state"
export AGENT_API_PORT=19642
export API_SERVER_PORT=19642
export WEBUI_PORT=19787
export TAIJI_WEBUI_PORT=19787
export API_SERVER_CORS_ORIGINS=http://127.0.0.1:19787,http://localhost:19787
```

说明：

- 每个执行任务开始前都必须在当前 shell 运行完整的上述环境块。
- 所有 Agent/WebUI pytest 都只使用已存在的 `$AGENT_PY`；不定义、不使用
  worktree 中不存在的 WebUI venv。Python 只借用根目录 venv 的第三方依赖；
  Task 1 的 provenance guard 必须证明被测源码来自整合 worktree。
- 第一次运行任何 pytest 前必须执行解释器/import 前置；期望精确输出
  `pytest 9.0.2`，两个进程分别从 Agent/WebUI cwd 导入自己的源码：

```bash
test -x "$AGENT_PY"
test "$("$AGENT_PY" -m pytest --version)" = "pytest 9.0.2"
(
  cd "$AGENT_ROOT"
  PYTHONPATH="$AGENT_ROOT" \
    "$AGENT_PY" -c 'import pytest, model_tools; assert pytest.__version__ == "9.0.2"'
)
(
  cd "$WEBUI_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -c 'import pytest, api.model_config; assert pytest.__version__ == "9.0.2"'
)
```

- 显式测试脚本使用 `19642/19787`。
- 隔离 Gateway/WebUI 只允许显式绑定 `19642/19787`；不得扫描或复用
  `18642/18643/18787`，即使其中某个端口当前为空。
- Electron 启动前的 stale-stop 只允许看到显式 QA 端口，不能指向
  `18642/18643/18787`。
- Electron 最终 URL、Gateway URL、源码 commit 和 runtime home 必须写入证据 JSON。

### 5.3 每次 Electron 验收前后检查

```bash
PHASE=task-b1
SNAPSHOT=before
RUNTIME_EVIDENCE="$WT/qa-evidence/main-consolidation-20260717/daily-runtime/$PHASE"
mkdir -p "$RUNTIME_EVIDENCE"

lsof -c Electron -a -d cwd -Fpcn \
  > "$RUNTIME_EVIDENCE/$SNAPSHOT-electron.txt" 2>&1 || true

for PORT in 18642 18787; do
  LISTENER="$RUNTIME_EVIDENCE/$SNAPSHOT-$PORT-listener.txt"
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -Fpcn > "$LISTENER" 2>&1 \
      && test -s "$LISTENER"; then
    PID="$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN | head -n 1)"
    lsof -p "$PID" -a -d cwd -Fn \
      > "$RUNTIME_EVIDENCE/$SNAPSHOT-$PORT-cwd.txt"
    curl -fsS -D "$RUNTIME_EVIDENCE/$SNAPSHOT-$PORT-headers.txt" \
      -o "$RUNTIME_EVIDENCE/$SNAPSHOT-$PORT-health.json" \
      "http://127.0.0.1:$PORT/health"
    rg -i '^(x-.*commit|server-commit|x-source-revision):' \
      "$RUNTIME_EVIDENCE/$SNAPSHOT-$PORT-headers.txt" \
      > "$RUNTIME_EVIDENCE/$SNAPSHOT-$PORT-commit-header.txt"
    test -s "$RUNTIME_EVIDENCE/$SNAPSHOT-$PORT-health.json"
    test -s "$RUNTIME_EVIDENCE/$SNAPSHOT-$PORT-commit-header.txt"
  else
    : > "$RUNTIME_EVIDENCE/$SNAPSHOT-$PORT-absent"
  fi
done

lsof -nP -iTCP:18643 -sTCP:LISTEN -Fpcn \
  > "$RUNTIME_EVIDENCE/$SNAPSHOT-18643-protected-listener.txt" 2>&1 || true
PID_18643="$(lsof -t -iTCP:18643 -sTCP:LISTEN | head -n 1 || true)"
if test -n "$PID_18643"; then
  lsof -p "$PID_18643" -a -d cwd -Fn \
    > "$RUNTIME_EVIDENCE/$SNAPSHOT-18643-protected-cwd.txt"
fi
```

每个阶段开始时使用 `SNAPSHOT=before`，结束时原样重跑并改为
`SNAPSHOT=after`。只有 listener 存在才执行对应 `curl`；禁止为生成 health
证据启动服务。前后必须满足：

- `before` 存在的 Electron/`18642`/`18787` 保持 PID、CWD、listener、health 和
  commit header 不变。
- `before` 不存在的 Electron/`18642`/`18787` 保持 absent，且 QA 证据证明只
  绑定 `19642/19787`。
- `18643` 的受保护 listener/PID/CWD 不变；不产生 health 请求。
- QA 退出后，仅 QA 端口和 QA 临时进程被清理。

任一 presence、PID、CWD、listener、health 或 commit header 意外变化，立即
停止整合，不继续下一个阶段。

## 6. 执行任务

### Task 1：建立整合来源和测试环境台账

**Files:**

- Create: `qa-evidence/main-consolidation-20260717/source-ledger.tsv`
- Create: `qa-evidence/main-consolidation-20260717/test-matrix.tsv`

- [ ] 记录整合分支、HEAD、dirty 状态、所有来源 commit 和冻结备份哈希。
- [ ] 按第 5 节重新采样 presence-aware 基线：存在的日常
  Electron/Gateway/WebUI 记录 PID、CWD、listener、health 和 commit header；
  不存在的保持 absent；另记录并保护 `18643` 的 listener/PID/CWD，不探测其
  health、不启动任何日常服务补证据。
- [ ] 验证 Python 从 worktree 加载源码，而不是 editable install 指回根目录：

```bash
ROOT=/Users/bwb/Documents/工作/taiji-agentv1.0
WT="$ROOT/.worktrees/main-consolidation-20260717"
AGENT_PY="$ROOT/hermes-local-lab/sources/hermes-agent/venv/bin/python"
PYTHONPATH="$WT/hermes-local-lab/sources/hermes-webui:$WT/hermes-local-lab/sources/hermes-agent" \
  "$AGENT_PY" - <<'PY'
from pathlib import Path
import api.routes
import hermes_state
expected = Path("/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/main-consolidation-20260717")
assert expected in Path(api.routes.__file__).resolve().parents
assert expected in Path(hermes_state.__file__).resolve().parents
print("worktree import provenance: OK")
PY
```

- [ ] 提交台账：

```bash
git add qa-evidence/main-consolidation-20260717
git commit -m "test(consolidation): record source and runtime baseline"
```

### Task 2：移植 Managed Runs 和生产授权

**Files:**

- Modify: `hermes-local-lab/sources/hermes-agent/gateway/platforms/api_server.py`
- Modify: `hermes-local-lab/sources/hermes-agent/hermes_state.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/gateway_chat.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
- Modify: `apps/taiji-desktop/src/main.js`
- Test: `hermes-local-lab/sources/hermes-agent/tests/gateway/test_api_server_runs.py`
- Test: `hermes-local-lab/sources/hermes-agent/tests/hermes_state/test_managed_run_leases.py`
- Test: `hermes-local-lab/sources/hermes-agent/tests/gateway/test_api_server_license.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_taiji_license_routes.py`

- [ ] 分别使用 `git cherry-pick -n 540f410b`、`8da8caa1`、`18f2427c`。
- [ ] 每个来源提交单独解决冲突、单独测试、单独形成新 commit。
- [ ] 在 `gateway_chat.py` 冲突中同时保留平台消息 ID exact-once 与 managed-run session id。
- [ ] 在 `api_server.py`/`hermes_state.py` 中同时覆盖同 session 串行、不同 session 并行、进程崩溃 lease 过期和重复完成。
- [ ] 在授权冲突中验证 UI 状态不能绕过 Agent 最终守卫。
- [ ] 运行：

```bash
mkdir -p "$WT/qa-evidence/main-consolidation-20260717/managed-runs"
(
  cd "$AGENT_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/gateway/test_api_server_runs.py" \
    "tests/hermes_state/test_managed_run_leases.py" \
    "tests/gateway/test_api_server_license.py" \
    "tests/run_agent/test_taiji_license_final_guard.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/managed-runs/task2-agent.xml"
)
(
  cd "$WEBUI_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/test_webui_gateway_chat_backend.py" \
    "tests/test_taiji_license_routes.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/managed-runs/task2-webui.xml"
)
```

预期：两个进程分别通过；绝不在同一解释器收集 Agent/WebUI 两个 `tests`
package，任何超时视为失败。

### Task 3：按 B1–B4 安全 hand-port Provider 凭据和即时路由

**来源裁决：**

- `d1b65c51` 与 `c581bd3f` 只允许用 `git show`/`git diff` 读取测试意图和
  小段算法，不允许 `git cherry-pick`、`git checkout <commit> -- <file>` 或
  整文件复制。
- 开始 B1 前先阅读
  `docs/reviews/main-consolidation-task-b-source-audit-2026-07-18.md`，并把其中
  27 类 must-first RED 测试映射到 B1–B4 的测试 node id。
- 每个子任务都按“RED 证据 → 最小 GREEN → 目标回归 → 暂存 diff 规格复审
  → 独立质量复审 → 独立 commit”闭环。任一复审有开放 P0/P1，不得进入下一项。
- B1→B2→B3→B4 是严格依赖栈：每个子任务仍单独复审、单独形成 commit，
  但后项可以依赖前项，不能把任一中间 commit 宣称为在保留后续提交时可独立
  回滚。

#### Task B1：Provider alias、family 与模型 fail-closed

**Files and functions:**

- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/provider_credentials.py`
  (`PROVIDER_FAMILY_ALIASES`, `provider_family`；不得修改 Secret 解析)
- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/custom_image_providers.py`
  (`normalize_custom_image_provider_entry` 中仅限 models/
  `allow_custom_model_id`，`ConfigurableOpenAIImageProvider._model`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/image_routing.py`
  (`_supports_vision_override`, `_lookup_supports_vision`,
  `decide_image_input_mode`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/plugins/image_gen/dashscope/__init__.py`
  (`DashScopeQwenImageProvider._model`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/plugins/image_gen/doubao/__init__.py`
  (`_resolve_model`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/plugins/image_gen/qianfan/__init__.py`
  (`QianfanImageGenProvider._model`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/plugins/image_gen/zhipu_image/__init__.py`
  (`ZhipuImageGenProvider._model`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/plugins/image_gen/minimax_image/__init__.py`
  (`MinimaxImageGenProvider._model`)
- Modify:
  `hermes-local-lab/sources/hermes-webui/api/model_config.py`
  (新增 `_validate_provider_model_choice`；`set_vision_config`、
  `set_image_gen_config` 只接入模型校验，不改变 credential 行为)
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/agent/test_provider_credentials.py`
- Create:
  `hermes-local-lab/sources/hermes-agent/tests/agent/test_provider_fail_closed_contract.py`
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/plugins/image_gen/test_configurable_openai_provider.py`
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/plugins/image_gen/test_domestic_builtin_providers.py`
- Test:
  `hermes-local-lab/sources/hermes-webui/tests/test_model_config_api.py`

- [ ] **RED（审计类别 1–6）：** 先新增以下六个测试并运行；必须因为目标行为
  尚未实现而失败，不得用 import error、fixture error 或放宽断言制造 RED：

```bash
mkdir -p "$WT/qa-evidence/main-consolidation-20260717/providers"
(
  cd "$AGENT_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/agent/test_provider_fail_closed_contract.py::test_provider_family_aliases_are_canonical" \
    "tests/agent/test_provider_fail_closed_contract.py::test_custom_provider_aliases_are_canonical_without_secret_lookup" \
    "tests/agent/test_provider_fail_closed_contract.py::test_builtin_known_image_models_resolve_exactly" \
    "tests/agent/test_provider_fail_closed_contract.py::test_builtin_unknown_image_models_fail_before_credential_lookup" \
    "tests/agent/test_provider_fail_closed_contract.py::test_custom_image_model_requires_explicit_allow_custom_model_id" \
    "tests/agent/test_provider_fail_closed_contract.py::test_unknown_vision_model_and_capability_fail_closed" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b1-red-agent.xml"
)
```

B1 RED 是单一 Agent-tests 进程；即使 `PYTHONPATH` 可导入 WebUI production
module，也不得加入 WebUI `tests/`。

- [ ] **GREEN：** 只扩充 canonical aliases、family 和统一 model allowlist；
  已知模型必须原样解析，未知内置模型和未显式 opt-in 的 custom model 在读取
  credential 或发起网络前 fail-closed，不回退 default model，不猜测视觉能力。
- [ ] B1 测试必须通过 monkeypatch 断言 `_credential_secret_value`、环境变量和
  Provider HTTP seam 均未被调用；显式 ref/default/legacy/tamper 的完整矩阵留给
  B2，B1 不新增 `_entry_api_key`、`provider_api_key` 或 Secret fallback。
- [ ] **目标回归：**

```bash
(
  cd "$AGENT_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/agent/test_provider_credentials.py" \
    "tests/agent/test_provider_fail_closed_contract.py" \
    "tests/agent/test_image_routing.py" \
    "tests/plugins/image_gen/test_configurable_openai_provider.py" \
    "tests/plugins/image_gen/test_domestic_builtin_providers.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b1-green-agent.xml"
)
(
  cd "$WEBUI_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/test_model_config_api.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b1-green-webui.xml"
)
```

- [ ] **规格复审 gate：** 独立 reviewer 对照审计类别 1–6、当前 Provider
  schema 和 staged diff，确认 alias/family/model/unknown capability 的
  fail-closed 分支逐项命中，且没有 Secret/legacy binding 越界。
- [ ] **质量复审 gate：** 另一 reviewer 检查 secret redaction、异常类型、
  重复逻辑、测试反例和未触及文件；两份结论均为 `APPROVED` 且无开放 P0/P1。
- [ ] **禁止：** 不引入旧 universal-image 计划；不复制旧 Provider 文件；
  不把未知模型替换成默认模型；不实现 credential binding；不扩张到 endpoint
  transport、Artifact 或 UI。
- [ ] 独立提交：

```bash
git commit -m "feat(images): hand-port fail-closed provider models"
```

#### Task B2：自定义生图/识图凭据与网络传输加固

**Files and functions:**

- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/provider_credentials.py`
  (`LEGACY_API_KEY_ENV`, `default_credential_ref`, `resolve_api_key`；
  B1 的 `PROVIDER_FAMILY_ALIASES`/`provider_family` 只作为既有输入)
- Modify:
  `hermes-local-lab/sources/hermes-agent/plugins/image_gen/domestic_common.py`
  (新增 `provider_api_key`)
- Create:
  `hermes-local-lab/sources/hermes-agent/agent/safe_outbound_http.py`
  (`NetworkScope`, `TrustedProxyProfile`, `normalize_network_scope`,
  `resolve_trusted_proxy_profile`, `resolve_pinned_addresses`,
  `request_pinned_https`, `request_via_trusted_proxy`, `read_bounded_json`,
  `build_openai_sync_transport`, `build_openai_async_transport`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/custom_image_providers.py`
  (`normalize_custom_image_provider_entry`, 新增 `_entry_api_key`,
  `custom_image_provider_public_row`,
  `ConfigurableOpenAIImageProvider.is_available`,
  `ConfigurableOpenAIImageProvider.get_setup_schema`,
  `ConfigurableOpenAIImageProvider.generate`, `_response_error_message`；
  `_model` 归 B1)
- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/custom_vision_providers.py`
  (`_normalize_base_url`, `is_custom_vision_base_url_safe`,
  `normalize_custom_vision_provider_entry`,
  `custom_vision_provider_public_row` 与 credential-ref Secret binding)
- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/auxiliary_client.py`
  (`_resolve_task_provider_model`, `resolve_provider_client`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/image_gen_provider.py`
  (`_resolved_image_addresses`, `_validate_image_url`, `_pinned_http_get`,
  `save_url_image`) only to extract/reuse characterization-protected primitives
- Modify:
  `hermes-local-lab/sources/hermes-agent/tools/url_safety.py`
  (`is_always_blocked_url`, `_is_blocked_ip`, `is_safe_url`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/plugins/image_gen/dashscope/__init__.py`
  (`DashScopeQwenImageProvider.is_available`,
  `DashScopeQwenImageProvider.generate`, `_dashscope_address_allowed`,
  `_save_safe_image_url`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/plugins/image_gen/doubao/__init__.py`,
  `hermes-local-lab/sources/hermes-agent/plugins/image_gen/qianfan/__init__.py`,
  `hermes-local-lab/sources/hermes-agent/plugins/image_gen/zhipu_image/__init__.py`,
  `hermes-local-lab/sources/hermes-agent/plugins/image_gen/minimax_image/__init__.py`
  (各 Provider 的 `is_available`/`generate` credential binding；模型函数仍归 B1)
- Modify:
  `hermes-local-lab/sources/hermes-webui/api/model_config.py`
  (`get_custom_vision_provider_configs`,
  `set_custom_vision_provider_config`,
  `delete_custom_vision_provider_config`,
  `get_custom_image_provider_configs`,
  `set_custom_image_provider_config`,
  `delete_custom_image_provider_config`)
- Create:
  `hermes-local-lab/sources/hermes-agent/tests/agent/test_safe_outbound_http.py`
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/agent/test_auxiliary_client.py`
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/plugins/image_gen/test_configurable_openai_provider.py`
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/plugins/image_gen/test_domestic_builtin_providers.py`
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/agent/test_save_url_image.py`
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/agent/test_image_gen_artifact_security.py`
- Test:
  `hermes-local-lab/sources/hermes-webui/tests/test_model_config_api.py`

- [ ] **characterization（类别 17 的既有 downloader 基线，预期初始 GREEN）：**
  先独立运行当前 `test_save_url_image.py` 与
  `test_image_gen_artifact_security.py`，证明单次 DNS、peer pin、每跳重验、
  MIME/magic/dimensions/size、inode/symlink 与 atomic write 语义仍成立；这组
  既有测试不计作类别 17 的行为性 RED：

```bash
(
  cd "$AGENT_ROOT"
  PYTHONPATH="$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/agent/test_save_url_image.py" \
    "tests/agent/test_image_gen_artifact_security.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b2-downloader-characterization-agent.xml"
)
```

- [ ] **RED（审计类别 7–18）：** 先写以下 12 个行为测试。B2 RED 必须导入并
  调用当前可用的 public seams（`resolve_api_key`、custom image/vision public
  config 与 Provider 调用、`resolve_provider_client`、`save_url_image`、WebUI
  custom config API）；不得在 RED 阶段导入尚不存在的
  `safe_outbound_http.py`。新模块只在 GREEN 创建。失败必须来自 reason code、
  调用次数或安全断言，不得来自 ImportError/fixture error：

```bash
(
  cd "$AGENT_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/agent/test_safe_outbound_http.py::test_credential_binding_default_matrix_is_fail_closed_and_legacy_compatible" \
    "tests/agent/test_safe_outbound_http.py::test_custom_provider_credential_binding_is_canonical_across_runtime_and_webui" \
    "tests/agent/test_safe_outbound_http.py::test_endpoint_url_shape_is_fail_closed" \
    "tests/agent/test_safe_outbound_http.py::test_public_direct_pins_all_answers_peer_sni_and_host" \
    "tests/agent/test_safe_outbound_http.py::test_private_direct_requires_explicit_scope_and_keeps_permanent_blocks" \
    "tests/agent/test_safe_outbound_http.py::test_trusted_proxy_uses_connect_origin_tls_and_never_falls_back" \
    "tests/agent/test_safe_outbound_http.py::test_network_scopes_block_metadata_link_local_and_mapped_variants" \
    "tests/agent/test_safe_outbound_http.py::test_fake_ip_range_is_never_connected" \
    "tests/agent/test_safe_outbound_http.py::test_custom_vision_sync_and_async_resist_dns_rebinding" \
    "tests/agent/test_safe_outbound_http.py::test_custom_image_post_is_pinned_and_never_redirects_auth" \
    "tests/agent/test_safe_outbound_http.py::test_image_download_propagates_network_scope_on_every_hop" \
    "tests/agent/test_safe_outbound_http.py::test_provider_json_is_mime_checked_and_bounded_before_parse" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b2-red-agent.xml"
)
```

B2 RED 只收集 Agent `tests` package；允许导入 WebUI production seam，但不把
WebUI tests root 加入该进程。

- [ ] **credential default matrix（类别 7）：** 参数化覆盖：显式 ref 缺失、
  Secret 为空、family mismatch 或 credential env 被篡改时均不得 fallback；
  未提供 ref 且没有 default 时使用该 family 的 canonical legacy env；唯一且
  合法的 default 即使 Secret 尚缺失，也允许按当前源码意图回落 canonical
  legacy env；被篡改或重复 default 必须 fail-closed。类别 8 再覆盖 custom
  image+vision 的 `_entry_api_key`/public row/`is_available`/setup schema/
  `generate` 与 WebUI get/set/delete 全链路，并拒绝 caller-controlled
  `api_key_env`。
- [ ] **trusted proxy matrix（类别 12–14，仍复用三个既有 RED node）：**
  参数化覆盖未批准 named profile（请求数为 0）、已批准 profile 模拟
  remote-public（允许）、proxy 模拟 remote-blocked（拒绝），以及应用把 proxy
  的结构化拒绝映射为稳定 `trusted_proxy_origin_blocked` 且 direct/private
  fallback 调用数为 0。
- [ ] **GREEN：** 先实现上述完整 credential binding，再从当前
  `image_gen_provider.py` 抽取单次 DNS、全部 answer
  校验、connected peer equality、原 hostname SNI/Host、每跳重验和 bounded-read
  语义作为 direct/redirect transport 到 `safe_outbound_http.py`；先用
  characterization tests 证明
  `save_url_image` 的现有 public contract、inode/symlink 和 atomic write 行为不变。
- [ ] `public_direct` 只允许直连公开地址且 `trust_env=False`；
  `private_direct` 仅在配置显式选择时允许 RFC1918/loopback/ULA 目标，保留
  本地自定义 endpoint。`trusted_proxy` 不接受任意 proxy URL，也不读取
  ambient proxy；Provider 配置只能引用控制面/运维预先批准的 named proxy
  profile，且 profile 必须声明已验证的 `public_egress` 与
  `dns_ip_classification` policy capability，缺失或未批准统一返回
  `trusted_proxy_unavailable`，请求数为 0。
- [ ] 标准 CONNECT 不向应用暴露 proxy 实际解析/连接的 origin IP，因此应用
  只校验 proxy 自身地址、origin URL/literal/metadata hostname、CONNECT 状态，
  以及隧道内 origin TLS certificate/hostname/SNI；不得声称应用独立验证了
  remote resolved peer。named proxy profile 必须在远端 DNS 解析到 RFC1918、
  metadata、link-local、其它永久禁区或 Fake-IP 时拒绝，并返回可映射的结构化
  policy denial；应用映射为 `trusted_proxy_origin_blocked`，任何 proxy 缺失、
  不可达、CONNECT/TLS/policy 失败均不得回退 direct/private。未来若 proxy
  提供 resolved-peer attestation 可增强此证明，但不作为当前标准 CONNECT
  契约。三种 scope 均永久禁止
  metadata、link-local、unspecified、multicast、其它 reserved/benchmark 和
  显式 `198.18.0.0/15` 地址；direct 模式解析到 Fake-IP 时返回
  `fake_ip_requires_trusted_proxy` 并停止，缺少显式 proxy 返回
  `trusted_proxy_unavailable`。应用必须拒绝 Fake-IP origin literal 和不安全的
  proxy 自身地址；hostname 的远端解析结果由上述 proxy-side policy 保证。
- [ ] 自定义生图/识图只从 canonical credential env 取 Secret；先完成路由
  安全决策和连接绑定，再附加 Authorization。API POST 不跟随 redirect；
  图片 GET redirect 每跳重新解析、固定、校验，跨 origin 不转发凭据。
- [ ] custom vision 的同步和异步 OpenAI-compatible client 都注入固定连接
  transport，禁止“`is_safe_url` 预检一次、SDK 再按 hostname 解析”的 TOCTOU。
- [ ] API JSON 只接受 `application/json` 或 `application/*+json`；先检查
  `Content-Length`，再以流式 1–2 MiB hard limit 读取后解析，超限/未知 MIME
  返回固定脱敏错误。图片 body 继续使用当前 magic、尺寸、字节上限和原子写入。
- [ ] **目标回归：**

```bash
(
  cd "$AGENT_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/agent/test_safe_outbound_http.py" \
    "tests/agent/test_auxiliary_client.py" \
    "tests/agent/test_save_url_image.py" \
    "tests/agent/test_image_gen_artifact_security.py" \
    "tests/tools/test_url_safety.py" \
    "tests/plugins/image_gen/test_configurable_openai_provider.py" \
    "tests/plugins/image_gen/test_domestic_builtin_providers.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b2-green-agent.xml"
)
(
  cd "$WEBUI_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/test_model_config_api.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b2-green-webui.xml"
)
```

- [ ] **规格复审 gate：** 独立 reviewer 对照审计类别 7–18，逐项核验
  credential default/fallback 矩阵、custom image+vision binding、三个 scope、
  named proxy profile/capability、proxy-side remote DNS/IP policy、
  `trusted_proxy_origin_blocked` 映射与 no-fallback、永久禁区、Fake-IP、
  sync/async peer pin、redirect 和 bounded JSON；不得把标准 CONNECT 写成应用
  已独立验证 remote resolved IP。
- [ ] **质量复审 gate：** 另一 reviewer 做 SSRF/secret-exfiltration 对抗审查，
  并确认 `image_gen_provider` 当前 downloader 与 Artifact 边界没有退化。
- [ ] **禁止：** 不整段移植 `d1b65c51` 的 urllib3 transport；不允许
  ambient proxy/private 全局开关降级；不以预检替代连接时 peer 验证；不覆盖
  当前 Artifact/session 授权、inode/symlink/atomic write；不恢复旧 downloader。
- [ ] 独立提交：

```bash
git commit -m "fix(images): bind custom credentials and harden outbound transport"
```

#### Task B3：版本化验证、运行时执行门禁与 schema refresh

**Files and functions:**

- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/image_gen_verification.py`
  (新增 `CAPABILITY_VERIFICATION_SCHEMA_VERSION`,
  `verification_runtime_snapshot`, `vision_fingerprint`；修改
  `image_gen_fingerprint`, `verification_status_from_state`)
- Modify:
  `hermes-local-lab/sources/hermes-webui/api/model_config.py`
  (`_vision_verification_state_root`, `_vision_verification_state_path`,
  `_atomic_write_json`, `_read_vision_verification_state`,
  `_vision_config_fingerprint`, `_capture_vision_config_snapshot`,
  `_public_vision_verification`, `test_vision_config`,
  `_image_gen_verification_state_root`, `_image_gen_verification_state_path`,
  `_read_image_gen_verification_state`, `_image_gen_config_fingerprint`,
  `_capture_image_gen_config_snapshot`, `_public_image_gen_verification`,
  `test_image_gen_config`；B3 独占状态读写、公开投影和 effective fingerprint，
  B4 只接 mutation hook)
- Create:
  `hermes-local-lab/sources/hermes-agent/agent/image_runtime.py`
  (`current_image_runtime_snapshot`, `_tool_name`,
  `refresh_agent_image_runtime`；初始化/成功 refresh 后原子暴露
  `agent._image_capability_fingerprint`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/agent_init.py`
  (`init_agent` 中 registry schema 基线和
  `agent._image_capability_fingerprint` 记录)
- Modify:
  `hermes-local-lab/sources/hermes-agent/model_tools.py`
  (`_tool_defs_cache`, `_clear_tool_defs_cache`, `get_tool_definitions`,
  `handle_function_call` 新增 `caller_capability_fingerprint` 并与当前 verified
  snapshot 比较)
- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/agent_runtime_helpers.py`
  (`invoke_tool` 新增并透传不可变的 `caller_capability_fingerprint`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/run_agent.py`
  (`AIAgent._invoke_tool` wrapper 透传 caller fingerprint)
- Modify:
  `hermes-local-lab/sources/hermes-agent/tools/image_generation_tool.py`
  (`get_image_generation_readiness`, `image_generate_tool`,
  `_handle_image_generate`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/tools/vision_tools.py`
  (`_handle_vision_analyze`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/tool_executor.py`
  (`execute_tool_calls_sequential` 与 `execute_tool_calls_concurrent` 在开始分派
  前捕获 Agent 初始化/上次成功 refresh 暴露的 fingerprint；顺序路径直接传给
  `handle_function_call`，并发路径经 `_invoke_tool`/`invoke_tool` 透传)
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/agent/test_image_gen_verification.py`
- Create:
  `hermes-local-lab/sources/hermes-agent/tests/agent/test_image_runtime_refresh.py`
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/tools/test_image_generation_readiness.py`
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/run_agent/test_run_agent.py`
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/tools/test_vision_tools.py`
- Test:
  `hermes-local-lab/sources/hermes-webui/tests/test_model_config_api.py`

- [ ] **RED seam 约束：** B3 RED 只调用当前可导入的 public seams：
  `api.model_config` 的 `get_vision_config`/`get_image_gen_config`/
  `test_vision_config`/`test_image_gen_config`，`model_tools.get_tool_definitions`，
  当前 Agent 下一轮/tool registry 路径、
  `agent_runtime_helpers.invoke_tool`/`AIAgent._invoke_tool`，以及
  `tools.vision_tools._handle_vision_analyze`；不得在 RED 阶段导入尚不存在的
  `image_runtime.py` 或新增 helper。新模块只在 GREEN 创建；RED 必须由行为
  断言失败，不得来自 ImportError。
- [ ] **RED（审计类别 19–23）：** 先增加 WebUI vision/image 状态的版本化
  读写/公开投影、effective fingerprint、cache identity、长生命周期 schema
  refresh + stale image gate，以及独立 vision 调用时门禁。类别 22 的同一个
  node 必须参数化 `sequential`/`concurrent`：构造旧 Agent caller fingerprint，
  再让新配置获得当前 `verified` 快照；两路都应返回稳定
  `capability_caller_stale`、Provider 调用数为 0，不新增第 28 个 node：

```bash
(
  cd "$WEBUI_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/test_model_config_api.py::test_webui_verification_state_requires_current_schema_version" \
    "tests/test_model_config_api.py::test_webui_effective_fingerprint_expands_env_or_fails_unresolved" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b3-red-webui.xml"
)
(
  cd "$AGENT_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/agent/test_image_runtime_refresh.py::test_tool_cache_key_tracks_versioned_webui_verification_snapshot" \
    "tests/agent/test_image_runtime_refresh.py::test_long_lived_agent_refresh_and_image_call_gate_preserve_non_registry_tools" \
    "tests/tools/test_vision_tools.py::test_vision_handle_call_time_gate_blocks_unknown_unverified_and_stale_before_provider" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b3-red-agent.xml"
)
```

B3 RED 必须保留两个独立 RED 证据进程；任一进程 collection/import 失败都不能
替代行为 RED。

- [ ] **GREEN：** WebUI vision/image 验证状态文件必须写入当前
  `schema_version`；缺失、旧版或未知新版均只可投影为
  `configured_unverified`，不能继承 `verified`。B3 同时收口两类 state path、
  原子写入/读取、公开投影、测试写回与 effective fingerprint。fingerprint 和
  cache key 同时包含 schema version、profile、canonical provider/family、
  model、credential ref、Secret digest、transport 和展开后的 effective endpoint。
- [ ] `${ENV}` 在 fingerprint 前使用与 runtime 相同的 env 展开器；未解析
  token 直接 fail-closed 并进入固定 reason code，不允许 raw placeholder 与运行
  时 effective endpoint 产生相同“已验证”状态。
- [ ] `refresh_agent_image_runtime` 先构造新 registry schemas，再以工具名原子
  合并；只替换上一次记录的 registry 工具，保留 memory provider、context
  engine、MCP/插件等非 registry 注入 schema，成功后同步 `valid_tool_names` 并
  失效 system prompt cache。
- [ ] 即使 long-lived Agent 仍携带旧 schema，`image_generate_tool` 的实际调用
  也必须同时接收 caller/Agent fingerprint 并读取当前版本化快照；初始化或上次
  成功 refresh 暴露的 caller fingerprint 与 current verified fingerprint、
  version/status 任一不匹配，都在 Provider 调用前返回
  `capability_caller_stale`。handler 只读取当前快照会把旧 Agent 误判为新状态，
  不能视为关闭此门禁；schema 可发现性也不是最终授权。
- [ ] `execute_tool_calls_sequential` 与 `execute_tool_calls_concurrent` 必须在
  分派前捕获 caller fingerprint。顺序路径每个
  `model_tools.handle_function_call` 调用都显式传入；并发路径把同一不可变值经
  `AIAgent._invoke_tool` → `agent_runtime_helpers.invoke_tool` →
  `handle_function_call` 透传，不能在线程中重新从 mutable Agent 读取 current
  fingerprint。
- [ ] `tools/vision_tools.py::_handle_vision_analyze` 也必须在每次调用时读取同一
  版本化快照：未知 main model、未验证 auxiliary、旧 schema 或 fingerprint
  stale 均在任何 Provider 调用前 fail-closed；只有已知 native 路由或当前
  verified 且 provider/model 精确匹配的 aux 路由可以继续。
- [ ] **目标回归：**

```bash
(
  cd "$AGENT_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/agent/test_image_gen_verification.py" \
    "tests/agent/test_image_runtime_refresh.py" \
    "tests/tools/test_image_generation_readiness.py" \
    "tests/tools/test_vision_tools.py" \
    "tests/run_agent/test_run_agent.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b3-green-agent.xml"
)
(
  cd "$WEBUI_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/test_model_config_api.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b3-green-webui.xml"
)
```

- [ ] **规格复审 gate：** 独立 reviewer 对照审计类别 19–23，检查状态机、
  版本迁移、effective fingerprint、next-turn refresh，以及顺序/并发两路
  caller-vs-current call-time 最终门禁。
- [ ] **质量复审 gate：** 另一 reviewer 对工具去重、异常回滚、并发可见性、
  prompt cache 和 non-registry schema 做破坏性测试审查。
- [ ] **禁止：** 不接受 unversioned verification；不直接
  `agent.tools = get_tool_definitions(...)` 丢弃扩展工具；不以 cache TTL 代替
  invalidation；不把“configured”当“available”。
- [ ] 独立提交：

```bash
git commit -m "fix(images): version capability verification and runtime gates"
```

#### Task B4：WebUI streaming/routing/cache 与四入口一致性

**Files and functions:**

- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/image_runtime.py`
  (新增 `build_capability_route_event`；只基于 B3 当前实际裁决生成统一事件)
- Modify:
  `hermes-local-lab/sources/hermes-agent/agent/tool_executor.py`
  (`execute_tool_calls_concurrent`, `execute_tool_calls_sequential`；
  在 `image_generate` 实际 Provider 调用前通过既有 callback 发送
  `capability_route`)
- Modify:
  `hermes-local-lab/sources/hermes-webui/api/model_config.py`
  (只新增统一 `_post_capability_mutation_commit`/
  `_invalidate_image_runtime_caches`；
  `upsert_provider_credential`, `delete_provider_credential`,
  `test_vision_config`, `test_image_gen_config`, `set_vision_config`,
  `set_alibaba_image_capabilities`, `set_custom_vision_provider_config`,
  `delete_custom_vision_provider_config`, `set_custom_image_provider_config`,
  `delete_custom_image_provider_config`, `set_image_gen_config`,
  `set_main_model_config` 只接 post-commit hook；状态路径、读写、公开投影、
  effective fingerprint 与测试结果写回均归 B3)
- Modify:
  `hermes-local-lab/sources/hermes-webui/api/streaming.py`
  (`_resolve_image_input_mode`；新增 `get_vision_runtime_state`,
  `image_capability_runtime_fingerprint`,
  `_image_capability_start_events`, `_image_capability_complete_events`；修改
  `_enrich_webui_images_with_vision`, `prepare_webui_chat_input`,
  `_sanitize_messages_for_api`, `_run_agent_streaming`，把执行 callback 映射到
  SSE；不得预先伪造 route event)
- Modify:
  `hermes-local-lab/sources/hermes-agent/cli.py`
  (`HermesCLI._resolve_turn_agent_config`,
  `HermesCLI._preprocess_images_with_vision`, `HermesCLI.chat`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/gateway/run.py`
  (`GatewayRunner._decide_image_input_mode`,
  `GatewayRunner._enrich_message_with_vision`,
  `GatewayRunner._extract_cache_busting_config`,
  `GatewayRunner._agent_config_signature`)
- Modify:
  `hermes-local-lab/sources/hermes-agent/tui_gateway/server.py`
  (`_enrich_with_attached_images`，新增 `_refresh_image_runtime_agent`，
  `_run_prompt_submit`)
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/gateway/test_agent_cache.py`
- Test:
  `hermes-local-lab/sources/hermes-agent/tests/test_tui_gateway_server.py`
- Create:
  `hermes-local-lab/sources/hermes-agent/tests/cli/test_image_capability_consistency.py`
- Test:
  `hermes-local-lab/sources/hermes-webui/tests/test_chat_attachment_context.py`
- Create:
  `hermes-local-lab/sources/hermes-webui/tests/test_image_capability_agent_signature.py`
- Test:
  `hermes-local-lab/sources/hermes-webui/tests/test_native_image_attachments.py`
- Create:
  `hermes-local-lab/sources/hermes-webui/tests/test_image_capability_entrypoint_consistency.py`
- Create:
  `hermes-local-lab/sources/hermes-webui/tests/test_image_artifact_stream_events.py`
- Test:
  `hermes-local-lab/sources/hermes-webui/tests/test_live_tool_callback_events.py`

- [ ] **RED（审计类别 24–27）：** 先增加四入口相同 snapshot/reason code、
  WebUI agent signature 失效、由实际 decision/tool execution 生产的标准事件、
  配置事务后的跨入口 invalidation 测试：

```bash
(
  cd "$WEBUI_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/test_image_capability_entrypoint_consistency.py::test_all_entrypoints_share_capability_snapshot_and_reason_codes" \
    "tests/test_image_capability_entrypoint_consistency.py::test_webui_and_gateway_agent_cache_identity_tracks_capability_snapshot" \
    "tests/test_image_capability_entrypoint_consistency.py::test_capability_route_event_matches_decision_and_actual_tool_execution" \
    "tests/test_image_capability_entrypoint_consistency.py::test_config_transaction_propagates_invalidation_without_losing_state" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b4-red-webui.xml"
)
```

B4 RED 是单一 WebUI-tests 进程；不得同时收集 Agent tests root。

- [ ] **GREEN：** CLI、Gateway、TUI、WebUI 只消费 B3 的同一版本化能力快照，
  不各自推断“已配置/可用”；unknown main-model capability、辅助视觉未验证、
  生图未验证和 stale version 使用相同稳定 reason code，并在主模型/Provider
  调用前停止。
- [ ] WebUI session agent signature 和 Gateway cache-busting signature 纳入
  capability snapshot version+fingerprint；配置保存、验证成功/失败、凭据更新/
  删除只在当前 config transaction 成功后通过唯一
  `_post_capability_mutation_commit` 失效 model/tool/agent cache。类别 27 的
  RED 必须参数化覆盖上述 exact mutation functions 的成功与回滚路径，证明
  任何入口都不能绕过该 hook，失败事务也不能发布半状态。
- [ ] `streaming.py` 只在历史确有 native image 时做图片能力清洗；普通文本历史
  不因未知图片能力失败。B4 在 Task C 之前建立并拥有 `capability_route` 的
  唯一生产契约：事件由 B3 当前裁决和 `tool_executor` 的实际
  `image_generate` 执行共同产生，WebUI 只映射 callback/SSE，CLI、Gateway、
  TUI 消费同一 payload/status/reason；不得先宣告路由再走另一分支。Task C
  后续只复用此事件，不得创建第二个 producer。
- [ ] 保留 `streaming.py` 当前 turn envelope、journal、pending/commit/discard
  Artifact、session authorization、取消和 save rollback；B4 不改消息 Artifact
  协议，也不创建第二套 image artifact 状态。
- [ ] **目标回归：**

```bash
(
  cd "$AGENT_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/cli/test_image_capability_consistency.py" \
    "tests/agent/test_credential_pool_routing.py" \
    "tests/gateway/test_agent_cache.py" \
    "tests/test_tui_gateway_server.py" \
    "tests/tools/test_vision_native_fast_path.py" \
    "tests/tools/test_vision_tools.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b4-green-agent.xml"
)
(
  cd "$WEBUI_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/test_model_config_api.py" \
    "tests/test_chat_attachment_context.py" \
    "tests/test_image_capability_agent_signature.py" \
    "tests/test_native_image_attachments.py" \
    "tests/test_image_capability_entrypoint_consistency.py" \
    "tests/test_image_artifact_stream_events.py" \
    "tests/test_live_tool_callback_events.py" \
    "tests/test_webui_gateway_chat_backend.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/providers/task-b4-green-webui.xml"
)
```

- [ ] **规格复审 gate：** 独立 reviewer 对照审计类别 24–27 与 B4 feature
  contract，核对四入口状态、reason code、缓存和真实调用一致。
- [ ] **质量/UX 复审 gate：** 另一 reviewer 同时执行 `$frontend-ux-qa`：
  检查错误可发现性、键盘/焦点、状态文案和浏览器证据；未执行的截图、可访问性
  自动化和视觉回归必须写“未验证”，不得写通过。
- [ ] **禁止：** 不恢复旧 URL/base64/absolute-path artifact；不覆盖当前
  Artifact/session 授权、inode/symlink/atomic write；不丢失 memory/context/MCP
  非 registry 工具；不把 WebUI cache 命中当运行时授权。
- [ ] 独立提交：

```bash
git commit -m "fix(images): align capability routing across entrypoints"
```

#### Task B 总门禁

- [ ] B1–B4 各自只包含该子任务的产品代码、测试和对应证据，不得揉成一个大
  提交；最终栈的回滚契约只允许从栈顶按 B4→B3→B2→B1 回滚**连续后缀**：
  `B4`、`B4+B3`、`B4+B3+B2`、`B4+B3+B2+B1`。禁止在保留依赖它的后续
  commit 时从栈中间抽离回滚 B1、B2 或 B3。
- [ ] 在一次性 detached 临时 worktree 对四种反向连续后缀逐一执行
  `git revert --no-commit`；每次从同一最终栈 HEAD 重置，revert 后分别做
  Agent/WebUI import smoke 和最小目标测试，确认没有冲突、导入断裂或
  collection error。演练只作用于临时 worktree：

```bash
B1=<task-b1-commit>
B2=<task-b2-commit>
B3=<task-b3-commit>
B4=<task-b4-commit>
STACK_HEAD="$(git rev-parse HEAD)"
REHEARSAL_TMP="$(mktemp -d /tmp/taiji-task-b-revert.XXXXXX)"
REHEARSAL_WT="$REHEARSAL_TMP/worktree"
REHEARSAL_EVIDENCE="$WT/qa-evidence/main-consolidation-20260717/providers/revert-rehearsal"
mkdir -p "$REHEARSAL_EVIDENCE"

git worktree add --detach "$REHEARSAL_WT" "$STACK_HEAD"
cleanup_rehearsal() {
  git worktree remove --force "$REHEARSAL_WT"
  rmdir "$REHEARSAL_TMP"
}
trap cleanup_rehearsal EXIT

verify_rehearsed_stack() {
  local LABEL="$1"
  (
    cd "$REHEARSAL_WT/hermes-local-lab/sources/hermes-agent"
    PYTHONPATH="$PWD" \
      "$AGENT_PY" -c 'import model_tools, agent.image_gen_verification'
    PYTHONPATH="$PWD" \
      "$AGENT_PY" -m pytest -q \
      "tests/agent/test_provider_credentials.py" \
      "tests/agent/test_image_gen_verification.py" \
      --junitxml="$REHEARSAL_EVIDENCE/$LABEL-agent.xml"
  )
  (
    cd "$REHEARSAL_WT/hermes-local-lab/sources/hermes-webui"
    PYTHONPATH="$PWD:$REHEARSAL_WT/hermes-local-lab/sources/hermes-agent" \
      "$AGENT_PY" -c 'import api.model_config, api.streaming'
    PYTHONPATH="$PWD:$REHEARSAL_WT/hermes-local-lab/sources/hermes-agent" \
      "$AGENT_PY" -m pytest -q \
      "tests/test_model_config_api.py" \
      "tests/test_live_tool_callback_events.py" \
      --junitxml="$REHEARSAL_EVIDENCE/$LABEL-webui.xml"
  )
  git -C "$REHEARSAL_WT" diff --cached --check
}

rehearse_suffix() {
  local LABEL="$1"
  shift
  git -C "$REHEARSAL_WT" reset --hard "$STACK_HEAD"
  git -C "$REHEARSAL_WT" revert --no-commit "$@"
  verify_rehearsed_stack "$LABEL"
}

rehearse_suffix b4 "$B4"
rehearse_suffix b4-b3 "$B4" "$B3"
rehearse_suffix b4-b3-b2 "$B4" "$B3" "$B2"
rehearse_suffix b4-b3-b2-b1 "$B4" "$B3" "$B2" "$B1"
```

- [ ] 重跑 B1–B4 的全部 GREEN 命令和第 3.3 节重复 Artifact 扫描。
- [ ] 对四个 commit 做一次 source-to-integration 映射复核；只有全部目标测试
  通过、四组双审关闭、`git status --short` 为空，才可把 source ledger 中
  `integration_commit=pending` 更新为实际 commit 列表并将 Provider L1 解锁。
- [ ] 以下禁令贯穿 B1–B4：旧 universal-image 计划、whole-commit cherry-pick、
  旧 downloader/artifact、ambient proxy/private downgrade、覆盖当前
  Artifact/session authorization/inode/symlink/atomic write、URL/base64/
  absolute-path 旧 artifact、unversioned verification，全部禁止。

### Task 4：接入生图意图但保持唯一 ArtifactRegistry

**Files:**

- Create: `hermes-local-lab/sources/hermes-agent/agent/image_intent.py`
- Modify: `hermes-local-lab/sources/hermes-agent/agent/conversation_loop.py`
  （生图意图编排，只消费 B4 `capability_route`）
- Modify: `hermes-local-lab/sources/hermes-agent/agent/tool_executor.py`
  （只做现有工具结果/Artifact candidate 适配，不生产第二套 route event）
- Modify: `hermes-local-lab/sources/hermes-agent/tools/image_generation_tool.py`
  （结果契约适配）
- Modify: `hermes-local-lab/sources/hermes-webui/api/streaming.py`
  （Artifact ingest 与既有 B4 event 消费，不重定义事件）
- Test: `hermes-local-lab/sources/hermes-agent/tests/agent/test_image_intent.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_image_artifact_stream_events.py`

- [ ] `git cherry-pick -n f8fb1d56`。
- [ ] 立即移除重复实现：

```bash
git rm -f --ignore-unmatch \
  hermes-local-lab/sources/hermes-webui/api/image_artifacts.py
```

- [ ] 不接受 `/api/image-artifacts`、`message.image_artifacts` 和独立 artifact 根目录的冲突解法。
- [ ] 将 image tool 结果转换为现有 `_artifact_candidates`，交给 `ingest_image_artifact_candidates(...)`。
- [ ] 明确生图直达路径必须复用 B4 的 `build_capability_route_event` 和 callback/
  SSE 映射；Task C 不得新增 `capability_route` producer、payload 或 reason code。
- [ ] 保留 pending/commit/discard、session 授权、刷新和迁移语义。
- [ ] 增加测试：明确生图、明确非生图、模糊意图确认、重复 callback、取消、save 失败、远程下载失败和单轮一次任务。
- [ ] 运行：

```bash
mkdir -p "$WT/qa-evidence/main-consolidation-20260717/routing"
(
  cd "$AGENT_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/agent/test_image_intent.py" \
    "tests/tools/test_image_artifact_contract.py" \
    "tests/tools/test_clarify_gateway.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/routing/task4-agent.xml"
)
(
  cd "$WEBUI_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/test_artifact_registry.py" \
    "tests/test_image_artifact_stream_events.py" \
    "tests/test_live_tool_callback_events.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/routing/task4-webui.xml"
)
```

预期：两个进程分别通过，且不跨 Agent/WebUI 的 `tests` package 收集；重复
产物实现扫描无匹配。

### Task 5：移植统一图片能力中心 UI

**Files:**

- Modify: `hermes-local-lab/sources/hermes-webui/api/model_config.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
- Modify: `hermes-local-lab/sources/hermes-webui/static/index.html`
- Modify: `hermes-local-lab/sources/hermes-webui/static/panels.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/messages.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/ui.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/style.css`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py`

- [ ] `git cherry-pick -n 512c8231`，逐段处理 chat-state 前端冲突。
- [ ] 保留 `message.artifacts` 的刷新、错误、取消、重试和阅读锚点。
- [ ] `git cherry-pick -n d2e74b85`，只保留最终统一能力中心。
- [ ] 页面默认只显示两张摘要卡和一个保存验证流程；多账号、自定义 endpoint 放入页面内高级区域。
- [ ] 保证授权、主模型、辅助模型和其它设置区域结构不变。
- [ ] 运行：

```bash
mkdir -p "$WT/qa-evidence/main-consolidation-20260717/frontend"
(
  cd "$WEBUI_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/test_model_config_api.py" \
    "tests/test_model_config_frontend.py" \
    "tests/test_chat_attachment_context.py" \
    "tests/test_image_artifact_stream_events.py" \
    "tests/test_live_tool_callback_events.py" \
    "tests/test_webui_gateway_chat_backend.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/frontend/task5-webui.xml"
)
```

预期：全部通过；页面静态文本不再包含“阿里云百炼快速配置”和“国产图片模型模板”。
该命令只收集 WebUI `tests` package，并从 `$WEBUI_ROOT` 固定 cwd 运行。

### Task 6：移植仍然缺失的诊断和聚焦 UX 行为

**Files:**

- Create: `hermes-local-lab/sources/hermes-webui/api/product_diagnostics.py`
- Create: `hermes-local-lab/sources/hermes-webui/static/managed-dialog.js`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/view.py`
- Modify: `hermes-local-lab/sources/hermes-webui/static/i18n.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/onboarding.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/panels.js`

- [ ] 对第 E 组每个行为先运行对应测试；已经通过的行为不重复移植。
- [ ] 专家团进度只按已完成阶段计算，不把需求确认计入执行进度。
- [ ] 定时任务统一用户术语，不重新暴露内部 cron/heartbeat 术语。
- [ ] 关键对话框支持焦点进入、焦点回收、Escape、Tab 循环和可访问名称。
- [ ] 诊断 API 和导出包统一使用公共错误映射和脱敏。
- [ ] 运行：

```bash
mkdir -p "$WT/qa-evidence/main-consolidation-20260717/frontend"
(
  cd "$WEBUI_ROOT"
  PYTHONPATH="$WEBUI_ROOT:$AGENT_ROOT" \
    "$AGENT_PY" -m pytest -q \
    "tests/test_expert_team_api.py" \
    "tests/test_chinese_locale.py" \
    "tests/test_taiji_document_first_home.py" \
    "tests/test_managed_dialog_static.py" \
    "tests/test_onboarding_static.py" \
    "tests/test_product_diagnostics.py" \
    "tests/test_product_diagnostics_ui.py" \
    "tests/test_product_error_envelope.py" \
    "tests/test_product_error_mapping.py" \
    --junitxml="$WT/qa-evidence/main-consolidation-20260717/frontend/task6-webui.xml"
)
```

预期：全部通过。
该命令只收集 WebUI `tests` package，并从 `$WEBUI_ROOT` 固定 cwd 运行。

### Task 7：移植 DOCX runtime store 和锁算法

**Files:**

- Create: `hermes-local-lab/sources/docx-engine-v2/src/templates/template-store.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/templates/registry.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/rendering/render-docx.js`
- Test: `hermes-local-lab/sources/docx-engine-v2/tests/template-runtime-layout.test.js`

- [ ] 依次 staged cherry-pick 第 F 组四个提交。
- [ ] 从 `15c058b4` 中恢复当前分支的 `AGENTS.md` 和旧证据文档，不把旧报告提交进来。
- [ ] 使用只读依赖目录：

```bash
export NODE_PATH=/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/node_modules
cd "$WT/hermes-local-lab/sources/docx-engine-v2"
node --test \
  tests/template-runtime-layout.test.js \
  tests/template-package.test.js \
  tests/install-template-cli.test.js \
  tests/workflow-contract.test.js
```

预期：全部通过。

- [ ] 生成一个隔离临时 DOCX，确认源码模板哈希不变、runtime store 可写。
- [ ] 使用 WPS 或 Word 打开最终 `document.docx`；仅自动化通过不能关闭此门禁。

### Task 8：按顺序移植 Linux 发布链

**Files:**

- Modify: 第 G 组提交实际涉及的打包脚本、安装脚本、manifest 和发布测试。
- Create: `qa-evidence/main-consolidation-20260717/linux-release/`

- [ ] 开始前显式使用 `$taiji-kylin-packaging`。
- [ ] 每个提交使用 `git cherry-pick -n`，检查它是否依赖旧源码路径或旧 commit 常量。
- [ ] 每个提交单独运行关联测试并单独 commit。
- [ ] 从最终整合 commit 重新生成源码包、DEB、SHA256 sidecar、manifest 和离线依赖闭包。
- [ ] 在隔离环境执行安装、升级、卸载、断网启动和重复安装演练。
- [ ] 在真实 Kylin/UOS x86_64 目标机执行 skill 规定的 release gate。
- [ ] 任何旧包、旧签名、旧截图和旧 target-verification 均不得复制为新证据。

### Task 9：完成全量回归、真实 Electron 和真实 Provider 验收

**Files:**

- Create: `qa-evidence/main-consolidation-20260717/final/`
- Create: `docs/reviews/main-consolidation-release-gate-2026-07-17.md`
- Create: `docs/reviews/main-consolidation-frontend-ux-qa-2026-07-17.md`

- [ ] 跑完第 7 节矩阵。
- [ ] 真实 Electron 必须从整合 worktree 的 clean commit 启动。
- [ ] 记录 Electron URL、实际 Gateway/WebUI 端口、Server commit header、源码哈希和 `dirty=false`。
- [ ] 使用未在聊天或截图中泄露的新阿里百炼临时 Key，完成一次真实识图和一次真实生图。
- [ ] 在聊天窗口验证识图正文、`capability_route` 和生成图片，不只看设置卡状态。
- [ ] 检查厂商控制台模型、次数和计费与本地证据一致。
- [ ] 其它厂商无授权 Key 时只能写“适配已实现、真实服务未验证”。
- [ ] 输出中文《前端 UX QA 报告》，未执行项明确写“未验证”。

## 7. 分层测试矩阵和门禁

所有状态初始均为“未执行”；只有新整合 commit 上的当前证据才能更新状态。

| 层 | 必测范围 | 通过标准 | 证据位置 | 初始状态 |
|---|---|---|---|---|
| L0 来源 | branch、HEAD、dirty、import provenance | 整合 worktree clean；源码从该 worktree 加载 | `qa-evidence/.../source-ledger.tsv` | 未执行 |
| L1 Managed Runs | session 连续性、跨进程 lease、exact-once | 目标 pytest 全通过，无超时 | `qa-evidence/.../managed-runs/` | 未执行 |
| L1 授权 | build profile、CLI、Gateway、WebUI、最终 guard | 所有未授权路径 fail-closed | `qa-evidence/.../license/` | 未执行 |
| L1 Provider | 凭据、family、endpoint、SSRF、模型权限 | 目标 pytest 全通过，密钥零泄露 | `qa-evidence/.../providers/` | 未执行 |
| L1 路由 | 原生视觉、辅助视觉、生图、模糊意图 | 路由事件与实际调用一致，单轮单任务 | `qa-evidence/.../routing/` | 未执行 |
| L1 Artifact | pending/commit/discard、授权、恢复、迁移 | 只有 ArtifactRegistry；目标 pytest 全通过 | `qa-evidence/.../artifacts/` | 未执行 |
| L1 UI | 图片能力、诊断、键盘、响应式 | 静态契约和 UI 自动化全通过 | `qa-evidence/.../frontend/` | 未执行 |
| L1 DOCX | runtime 隔离、锁、单 DOCX | Node 测试和隔离确定性渲染通过 | `qa-evidence/.../docx/` | 未执行 |
| L2 WebUI 全量 | 全部 WebUI pytest | 0 新失败；遗留失败必须与冻结基线 node id 完全一致且逐项有批准记录 | `qa-evidence/.../webui-full/` | 未执行 |
| L2 Agent 全量 | 正式并行 runner | 0 新失败；`test_run_agent.py` 必须在正式时限内完成 | `qa-evidence/.../agent-full/` | 未执行 |
| L3 Electron | 设置、聊天、产物、迁移、恢复、诊断 | clean commit；三条真实主路径通过 | `qa-evidence/.../electron/` | 未执行 |
| L3 UX | 1440/1120/900、键盘、焦点、200% | 无 P0/P1；未做项写“未验证” | `docs/reviews/main-consolidation-frontend-ux-qa-2026-07-17.md` | 未执行 |
| L4 真实 Provider | 阿里识图、生图 | 两次真实调用、聊天产物、控制台一致 | `qa-evidence/.../providers-live/` | 未执行 |
| L4 Office | 最终 DOCX | WPS/Word 打开、结构和图片正确 | `qa-evidence/.../office/` | 未执行 |
| L5 Linux 自动化 | 构建、manifest、依赖闭包、隔离演练 | 最终 commit 制包，自动化演练通过 | `qa-evidence/.../linux-release/automated/` | 未执行 |
| L5 Linux 目标机 | 安装、升级、卸载、断网启动 | 真实 Kylin/UOS gate 通过 | `qa-evidence/.../linux-release/target/` | 未执行 |
| L6 日常不受扰动 | Electron、18642/18787 presence 与受保护 18643 | before 存在者的 PID/CWD/listener/health/commit 不变；before 缺席者仍缺席；18643 未停止或复用 | `qa-evidence/.../daily-runtime/` | 未执行 |

门禁分层：

- 本地 `main` 收敛门禁：L0、全部 L1、L2、L3、L5 Linux 自动化、L6。
- 生产发布门禁：在本地 `main` 收敛门禁之上，再要求 L4 真实 Provider、L4 Office、L5 Linux 目标机以及最终安装包签名/校验。
- 真实外部 Key、WPS/Word 或 Kylin/UOS 目标机暂不可用时，必须在 release gate 中记录为 blocker；它们阻断“可生产发布”声明，但不阻断已经满足本地门禁的 `main` 收敛。

全量 A/B 规则：

1. 使用相同依赖、相同环境变量、相同并发度、相同 timeout。
2. 比较 pytest node id，不比较单纯数量。
3. 当前新增失败必须为 `0`。
4. 共同失败不能自动写成“通过”；必须修复或逐项批准豁免。
5. collection error、文件级 timeout、浏览器依赖缺失不能当成普通已知失败。
6. fixture Provider 通过不能替代真实阿里端到端。

## 8. 本地 `main` 收敛与生产发布条件

### 8.1 本地 `main` 收敛门禁

只有同时满足以下条件，才允许建立临时 main release worktree 并 fast-forward 本地 `main`：

1. 整合分支 `git status --short` 为空。
2. 第 A 至 G 组都有独立提交和来源映射。
3. `api/image_artifacts.py`、`/api/image-artifacts`、`message.image_artifacts` 不存在。
4. 所有 L0/L1 测试通过。
5. WebUI/Agent 全量无新增失败，遗留失败全部有明确处置；Agent 文件级 timeout 已关闭。
6. 隔离 Electron 来源为最终整合 commit、`dirty=false`，图片配置、聊天、Artifact、迁移、恢复和诊断功能契约通过。
7. Linux 构建、manifest、依赖闭包和隔离离线演练通过。
8. 中文《前端 UX QA 报告》已输出，当前已测范围无 P0/P1；未执行项明确标记为“未验证”。
9. 每阶段 presence-aware 复采样证明：before 存在的日常进程未被替换，
   before 缺席的 Electron/`18642`/`18787` 未被 QA 启动或占用，受保护
   `18643` 未被停止、复用或探测 health。
10. 冻结备份 SHA256 再次验证通过。
11. 所有尚缺的真实外部 Provider、WPS/Word 和 Kylin/UOS 目标机证据已明确登记为 production release blocker。

更新方式只允许 fast-forward：

```bash
ROOT=/Users/bwb/Documents/工作/taiji-agentv1.0
git -C "$ROOT" merge-base --is-ancestor main codex/main-consolidation-20260717
git -C "$ROOT" worktree add "$ROOT/.worktrees/main-release-20260717" main
git -C "$ROOT/.worktrees/main-release-20260717" merge --ff-only \
  codex/main-consolidation-20260717
```

禁止：

```bash
git branch -f main codex/main-consolidation-20260717
git merge --no-ff codex/main-consolidation-20260717
git rebase --onto main
```

本轮用户已经授权：门禁满足后更新本地 `main`、切换日常入口、定向清理并重启验证，不需要重复询问。只有出现计划外破坏性动作、remote push、数据恢复或新风险时才暂停请求方向。

### 8.2 生产发布门禁

本地 `main` 收敛后，只有再满足以下条件，才能写“可生产发布”或生成正式 release：

1. 使用未泄露的新阿里百炼 Key，真实识图和真实生图均在聊天中端到端成功。
2. 其它宣称正式支持的平台有真实服务证据；无 Key 的平台明确记录“适配已实现、真实服务未验证”。
3. 最终 DOCX 已在 WPS 或 Word 真实打开并完成内容、图片和版式检查。
4. 最终 commit 的安装包、SHA256、manifest、签名和离线依赖闭包一致。
5. 真实 Kylin/UOS x86_64 目标机完成安装、升级、卸载、断网启动和授权验收。
6. release 文档没有复用旧 commit、旧包、旧截图或旧目标机结果。

任一项缺失时，允许状态只能是：

```text
本地 main 已收敛；生产发布未放行。
```

## 9. 垃圾、未跟踪文件和脏 worktree 裁决

### 9.1 根目录 66 个未跟踪文件

禁止批量 `git add` 或 `git clean`。按三类处理：

1. 运行/生成垃圾，最终可定向清理：
   - `logs/*.log`
   - `logs/*.pid`
   - `docx-engine-v2/.qa/**`
   - 演示材料生成后的 DOCX/PDF/XLSX
2. 本地协作信息，不进入产品：
   - `.codex/handoff.md`
3. 源码候选，必须独立审查后决定：
   - `hermes-webui/uv.lock`
   - `hermes-agent/oa-architecture.html`
   - `tools/demo_materials/*.py`
   - `tools/demo_materials/*.mjs`
   - 演示资料包中的 `_gen_*.py`、`build_*.py`

源码候选不能因为“像代码”就进入主干；必须证明来源、用途、测试和许可，并形成独立 commit。

### 9.2 `.worktrees/app-desktop-qa`

以下依赖目录永久视为生成物：

- `apps/taiji-desktop/node_modules`
- `docx-engine-v2/node_modules`
- `hermes-agent/venv`

以下源码外观文件不能从脏 QA worktree 复制：

- `template-store.js`
- `taiji-runtime-profile.json`
- `taiji_runtime_profile.py`
- `product_contract.py`
- `product_diagnostics.py`
- `managed-dialog.js`

这些能力只能从已裁决的 full-product commit 按测试移植。

### 9.3 清理顺序

本轮已经授权在 `main` 更新、日常切换和最终复验之后执行下列定向清理；无需重复询问。若实际状态超出冻结清单、需要删除未归档数据或出现新的风险，则立即停止并请求方向：

1. 验证冻结备份 SHA256。
2. 确认整合分支和 `main` 包含所有保留能力。
3. 停止并确认残留的 image worktree 测试进程只属于测试端口。
4. 先移除已被替代且 clean 的 `universal-image-capabilities` worktree。
5. 再移除 clean 的 `image-provider-credentials`、phase base 和其它历史 QA worktree。
6. 确认 image-center 提交均已进入整合线后，移除 image-center worktree。
7. 确认 full-product 所选提交和 Linux 链均已进入整合线后，移除 full-product worktree。
8. chat-state 和 consolidation worktree 最后处理。
9. 脏 `app-desktop-qa` 必须再次比对冻结 patch/tar 后，才允许 `worktree remove --force`。
10. 根目录未跟踪文件逐路径处理，不使用 `git clean -fdx`。
11. 至少保留冻结备份七天并再次校验后，才讨论 `git gc`/`prune`；它们不属于本计划自动执行项。

## 10. 失败停止条件

出现以下任一情况，立即停止当前阶段，不进入下一提交簇：

1. 日常保护边界发生任一变化：before 存在的 Electron、`18642`、`18787`
   的 PID/CWD/listener/health/commit header 被 QA 改变；before 缺席者被
   QA 启动或占用；或受保护 `18643` 被停止、复用、改变 PID/CWD/listener。
2. 根目录出现新的 tracked 改动。
3. 整合 worktree 出现无法解释的文件或密钥。
4. 业务 refs 在非预定 commit 操作时变化。
5. `git fsck` 报 missing/corrupt object。
6. 冻结备份 SHA256 失败。
7. 冲突只能通过整文件 `ours`/`theirs` 才能暂时通过。
8. 第二套 artifact registry、media endpoint 或消息字段重新出现。
9. API Key 出现在响应、日志、DOM、诊断或测试快照。
10. targeted test 新失败、collection error 或 timeout。
11. 配置卡“已验证”但聊天实际没有走对应模型。
12. Electron 证据无法证明 worktree、commit 和 dirty 状态。
13. DOCX 自动化或隔离确定性渲染失败。
14. 打包证据引用旧 commit、旧 manifest 或旧目标机结果。
15. 任何人试图在缺少真实 Provider、WPS/Word 或 Kylin/UOS 目标机证据时声明“可生产发布”。

停止后必须记录：

- 当前 branch/HEAD。
- `git status --short`。
- 失败命令和退出码。
- 冲突文件。
- 日常 Electron/`18642`/`18787` 的 presence-aware 状态，以及受保护
  `18643` 的 listener/PID/CWD。
- 可回滚到的上一阶段 commit。

## 11. Remote 和发布边界

- 本计划所有 commit 只保存在本地。
- 不执行 `git push`、不创建 PR、不更新 `origin/main`。
- 只有本地 `main` fast-forward、日常切换和最终复验完成后，用户再次明确批准，才允许 push。
- push 前必须比较：

```bash
git fetch --prune origin
git log --oneline --left-right --cherry-pick origin/main...main
git merge-base --is-ancestor origin/main main
```

- 若远端在整合期间前进，停止 push；重新审计远端提交并做新的 A/B，不允许 force push。
- 不创建 release tag，不覆盖旧 tag，不上传旧 Kylin 包。

## 12. 完成定义

### 12.1 本地 `main` 收敛完成

只有同时具备以下证据，才能写“本地 main 已收敛”：

- 最终 commit 和来源提交映射。
- 整合 worktree clean。
- 根目录 tracked 仍为 0，并已按本轮授权完成正式切换和重启复验。
- 唯一 ArtifactRegistry 静态扫描通过。
- L0、L1、L2、L3、L5 Linux 自动化和 L6 均有当前证据并满足门禁。
- 中文《前端 UX QA 报告》。
- 隔离 Electron 截图、功能契约和来源审计。
- 最终 commit 重新生成的 Linux 自动化演练产物。
- 冻结备份最终 SHA256 复验。
- `main` 只通过 `--ff-only` 更新。
- 没有任何 remote push 或 GC；清理只覆盖已冻结、已裁决的路径。

### 12.2 生产发布完成

只有在“本地 main 已收敛”基础上，再具备以下证据，才能写“生产发布已放行”：

- 阿里真实识图、生图聊天端到端及厂商控制台一致性。
- 所有宣称正式支持的其它 Provider 真实调用证据，或明确收窄正式支持范围。
- WPS/Word 最终 DOCX 验收。
- 最终 commit 重新制成的 Linux 包、签名、manifest 和完整离线依赖闭包。
- 真实 Kylin/UOS 目标机安装、升级、卸载、断网启动和授权验收。

缺少任一真实外部证据时，不回退已经通过门禁的本地 `main`；只保持对应 release blocker，禁止发布声明和正式交付。
