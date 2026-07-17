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

按以下顺序移植：

1. `d1b65c51` — `feat(images): generalize provider credentials and safety`
2. `c581bd3f` — `fix(images): make capability routing immediately consistent`

保留目标：

- API Key 本机保存，配置只存 `credential_ref`。
- 平台元数据驱动模型、鉴权、能力和端点字段。
- 内置平台只要求平台、模型和 API Key。
- 主模型原生视觉快路径、辅助视觉路径和生图路径立即跟随配置版本。
- endpoint 安全校验、下载安全和凭据脱敏。

### 2.3 第 C 组：生图意图和会话产物

来源提交：

1. `f8fb1d56` — `feat(images): route generation intent and stream artifacts`

这个提交禁止直接作为最终产物层真值。只移植：

- `agent/image_intent.py` 的明确生图、明确非生图、模糊意图分类。
- 单轮只创建一个生图任务的约束。
- `image_generate` 工具事件和 `capability_route` 诊断事件。
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

## 5. 日常三进程不中断策略

### 5.1 日常进程边界

冻结时日常链路为：

- Electron：来源于根目录主检出。
- Gateway：`127.0.0.1:18642`。
- WebUI：`127.0.0.1:18787`。

PID 会变化，验收以“PID + CWD + 监听端口 + health + Server commit header”组合判定，不以历史 PID 判定。

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
export AGENT_PY="$ROOT/hermes-local-lab/sources/hermes-agent/venv/bin/python"
export WEBUI_PY="$ROOT/hermes-local-lab/sources/hermes-webui/.venv/bin/python"
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
- Python 只借用根目录 venv 的第三方依赖；Task 1 的 provenance guard 必须证明被测源码来自整合 worktree。
- 显式测试脚本使用 `19642/19787`。
- Electron 源码壳会从默认端口开始寻找空闲端口；因为日常端口已占用，它必须选择其它端口。
- Electron 启动前的 stale-stop 只允许看到上面显式测试端口，不能指向 `18642/18787`。
- Electron 最终 URL、Gateway URL、源码 commit 和 runtime home 必须写入证据 JSON。

### 5.3 每次 Electron 验收前后检查

```bash
lsof -nP -iTCP:18642 -sTCP:LISTEN
lsof -nP -iTCP:18787 -sTCP:LISTEN
curl -fsS http://127.0.0.1:18642/health
curl -fsS http://127.0.0.1:18787/health
```

前后必须满足：

- 两个日常监听 PID 未被 QA 脚本替换。
- 两个 health 仍成功。
- 日常进程 CWD 仍为根目录链路。
- QA 退出后，仅 QA 端口和 QA 临时进程被清理。

任一日常 PID、CWD、端口或 health 意外变化，立即停止整合，不继续下一个阶段。

## 6. 执行任务

### Task 1：建立整合来源和测试环境台账

**Files:**

- Create: `qa-evidence/main-consolidation-20260717/source-ledger.tsv`
- Create: `qa-evidence/main-consolidation-20260717/test-matrix.tsv`

- [ ] 记录整合分支、HEAD、dirty 状态、所有来源 commit 和冻结备份哈希。
- [ ] 记录日常 Electron/Gateway/WebUI 的 PID、CWD、端口和 health。
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
PYTHONPATH="$WT/hermes-local-lab/sources/hermes-webui:$WT/hermes-local-lab/sources/hermes-agent" \
  "$AGENT_PY" -m pytest -q \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/gateway/test_api_server_runs.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/hermes_state/test_managed_run_leases.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/gateway/test_api_server_license.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/run_agent/test_taiji_license_final_guard.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_webui_gateway_chat_backend.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_taiji_license_routes.py"
```

预期：全部通过；任何超时视为失败。

### Task 3：移植 Provider 凭据和即时路由

**Files:**

- Create: `hermes-local-lab/sources/hermes-agent/agent/provider_credentials.py`
- Modify: `hermes-local-lab/sources/hermes-agent/agent/auxiliary_client.py`
- Modify: `hermes-local-lab/sources/hermes-agent/agent/image_routing.py`
- Modify: `hermes-local-lab/sources/hermes-agent/agent/image_runtime.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/model_config.py`
- Test: `hermes-local-lab/sources/hermes-agent/tests/agent/test_provider_credentials.py`
- Test: `hermes-local-lab/sources/hermes-agent/tests/agent/test_image_routing.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_model_config_api.py`

- [ ] `git cherry-pick -n d1b65c51`，移除该提交携带的旧计划文档，只提交产品代码和测试。
- [ ] 运行 Provider、SSRF、凭据 family 和内置模型测试。
- [ ] 提交 `feat(images): consolidate provider credentials`。
- [ ] `git cherry-pick -n c581bd3f`。
- [ ] 解决 `streaming.py` 冲突时保留 chat-state turn envelope 和 journal。
- [ ] 验证配置 fingerprint 变化使旧验证失效，Agent cache 读取新版本。
- [ ] 运行：

```bash
PYTHONPATH="$WT/hermes-local-lab/sources/hermes-webui:$WT/hermes-local-lab/sources/hermes-agent" \
  "$AGENT_PY" -m pytest -q \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/agent/test_provider_credentials.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/agent/test_auxiliary_client.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/agent/test_image_gen_verification.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/agent/test_image_routing.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/agent/test_credential_pool_routing.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/tools/test_vision_native_fast_path.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/tools/test_vision_tools.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/tools/test_image_generation_readiness.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_model_config_api.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_image_capability_agent_signature.py"
```

预期：全部通过。

### Task 4：接入生图意图但保持唯一 ArtifactRegistry

**Files:**

- Create: `hermes-local-lab/sources/hermes-agent/agent/image_intent.py`
- Modify: `hermes-local-lab/sources/hermes-agent/agent/conversation_loop.py`
- Modify: `hermes-local-lab/sources/hermes-agent/agent/tool_executor.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tools/image_generation_tool.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/streaming.py`
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
- [ ] 保留 pending/commit/discard、session 授权、刷新和迁移语义。
- [ ] 增加测试：明确生图、明确非生图、模糊意图确认、重复 callback、取消、save 失败、远程下载失败和单轮一次任务。
- [ ] 运行：

```bash
PYTHONPATH="$WT/hermes-local-lab/sources/hermes-webui:$WT/hermes-local-lab/sources/hermes-agent" \
  "$AGENT_PY" -m pytest -q \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/agent/test_image_intent.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/tools/test_image_artifact_contract.py" \
  "$WT/hermes-local-lab/sources/hermes-agent/tests/tools/test_clarify_gateway.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_artifact_registry.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_image_artifact_stream_events.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_live_tool_callback_events.py"
```

预期：全部通过；重复产物实现扫描无匹配。

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
PYTHONPATH="$WT/hermes-local-lab/sources/hermes-webui:$WT/hermes-local-lab/sources/hermes-agent" \
  "$AGENT_PY" -m pytest -q \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_model_config_api.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_chat_attachment_context.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_image_artifact_stream_events.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_live_tool_callback_events.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_webui_gateway_chat_backend.py"
```

预期：全部通过；页面静态文本不再包含“阿里云百炼快速配置”和“国产图片模型模板”。

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
PYTHONPATH="$WT/hermes-local-lab/sources/hermes-webui:$WT/hermes-local-lab/sources/hermes-agent" \
  "$AGENT_PY" -m pytest -q \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_expert_team_api.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_chinese_locale.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_taiji_document_first_home.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_managed_dialog_static.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_onboarding_static.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_product_diagnostics.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_product_diagnostics_ui.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_product_error_envelope.py" \
  "$WT/hermes-local-lab/sources/hermes-webui/tests/test_product_error_mapping.py"
```

预期：全部通过。

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
| L6 日常不受扰动 | 18642/18787 和 Electron | QA 前后 PID/CWD/health 未被替换 | `qa-evidence/.../daily-runtime/` | 未执行 |

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
9. 日常三进程在整个 QA 周期未被测试替换。
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

1. 日常 `18642`、`18787` 的 PID/CWD/health 被 QA 改变。
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
- 日常三进程状态。
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
