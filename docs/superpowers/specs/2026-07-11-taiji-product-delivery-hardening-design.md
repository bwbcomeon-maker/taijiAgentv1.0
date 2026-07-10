# 太极 Agent 产品与交付全面加固设计

## 文档状态

- 日期：2026-07-11
- 状态：已获用户设计批准，待拆分实施计划
- 产品决策：文档成果优先
- 技术决策：保留现有 Python、Vanilla JavaScript、Electron 和单 DEB 主线，以跨层产品契约作为唯一真相脊柱
- 当前基线：`c1c0d532f258941b17095e4362d948a6cf788ee8`

## 目标

本次工作不是继续堆叠功能，而是把现有能力收口为可以被用户理解、被安装包完整携带、被目标机验证、被失败恢复机制保护的产品。

目标用户是不需要理解模型、Provider、Skill、命令行和源码路径的政企业务人员。用户应能够把主题、制度和已有材料交给太极，通过少量澄清和阶段复核，最终得到可编辑、可追溯、可在 WPS/Word 中继续使用的正式 DOCX。

管理员和实施人员需要另一条清楚但不干扰业务主流程的配置、授权、诊断、安装、升级和回滚路径。

北极星只约束内容创作和专题研究的文档主路径，不要求定时任务、诊断等辅助场景生成 DOCX。文档主路径的可测结果是：用户能够从首屏入口开始，完成材料收集、需求确认、阶段复核、DOCX 生成，并在指定办公软件中完成打开、编辑、保存和重开。

`可追溯` 至少表示最终成果能够关联输入材料摘要/hash、session、专家团 run、阶段与修改记录、模板 ID/版本/hash、生成作业、质量报告和 WPS/Word 验收记录。产品试用指标采用固定口径：首份可用文档耗时、人工修改轮次、事实性纠错次数、Office 打开编辑重开通过率和最终采纳率。试用目标、样本量和统计周期按下述固定协议执行，不能由代码测试推导或事后调整。

首轮商业验证采用固定试用协议：7–14 个自然日、至少 8 名目标岗位用户、至少 30 个真实脱敏文档任务且覆盖不少于 3 类文档。`首份可用文档耗时` 从用户创建文档任务并提交第一份材料开始，到 reviewer 首次把 `docx_ready` 标记为可进入 Office 验收结束；与同用户同类传统任务基线相比，中位耗时至少下降 30%。`Office 首次通过率` 为首次 review 直接 accepted 的任务数/完成首次 review 的任务数，目标不低于 90%。`最终采纳率` 为最终被业务采用的 artifact 数/完成文档主链的 artifact 数，目标不低于 70%。试用期间关键事实错误为 0、产品 P0/P1 为 0。样本不足或分母为 0 时指标记 `insufficient_evidence`，不能宣布商业验证完成。

## 当前证据基线

本设计基于当前 checkout、聚焦测试、浏览器审计和打包脚本静态审计，不把历史报告当作当前完成证据。

- WebUI Gateway 默认 `runs` transport 的聚焦测试当前为 `4 failed, 15 passed`。
- `/v1/runs` 同一 `session_id` 的第二轮不会从 `state.db` 恢复历史。
- 使用环境变量注入攻击者公钥后，攻击者自签 JWT 当前可以通过生产授权守卫。
- `TAIJI_LICENSE_REQUIRED=0` 当前可以令缺少授权文件的执行路径放行。
- 当前 DEB staging 没有 DOCX Engine、独立 Node 运行时和受控产品 Skills。
- 当前 DOCX 模板安装会把 registry 和用户模板写回 Engine 代码目录。
- 当前 release gate 的 60 项包装静态测试通过，但不检查最终解包 payload 是否含 DOCX、Node 和 Skills。
- 当前交付目录的源码包对应旧 commit，`生成的安装包/` 与 `离线依赖/` 没有当前构建产物。
- 当前 WebUI 首屏仍像通用 Agent，旗舰专家团和最终 DOCX 价值不可直接发现。
- 默认图片生成配置指向会被当前国产策略拒绝、且没有凭据的 Provider，fresh install 出现自相矛盾状态。
- Skills、memory、workspace 等技术复用能力默认隐藏，但没有等价的常用场景、单位规范和历史成果业务入口。
- 招标预审、项目启动和可研审查仍主要是演示脚本，没有结构化产品 contract。
- 专家团后端和前端都会使用 `current_index + 1` 推导完成数，导致 `done=0` 时显示 `1/5`。
- Onboarding 和专家详情弹窗缺少完整焦点生命周期；多个图标按钮和主输入框缺少稳定 accessible name/label。
- 普通用户错误面仍可能出现 Provider、环境变量、内部兼容名和原始异常。
- 当前根仓没有会对本仓提交生效的 CI。
- 根 README 仍以实验室口径描述项目，根项目 LICENSE/NOTICE/SBOM 与第三方许可闭包尚未形成。
- 当前分支比本地 main 领先数百个 commit，当前上游基线和补丁治理不足。

## 第一性原理与产品不变量

### 用户结果不变量

1. 用户必须能从首屏理解产品解决什么问题。
2. 用户必须能通过可见入口进入旗舰场景。
3. 用户看到的进度、完成态和成果入口必须来自真实后端状态。
4. 最终成果必须是可打开、可编辑、可复核的 DOCX，而不是聊天文本或中间目录。
5. 自动质量检查不能替代 WPS/Word 人工终验。

### 会话不变量

1. 同一托管 session 的第二轮必须包含第一轮 user/assistant 历史。
2. 托管 session 只有一个历史权威源。
3. 同一 session 的并发 run 不能交错读取和写入历史。
4. 无状态 OpenAI 兼容调用仍可由调用方显式提交历史。

### 安全不变量

1. 普通用户环境不能关闭生产授权、关闭机器绑定、开启旧绑定或替换验签公钥。
2. 所有真实执行入口都必须在模型和工具运行前通过最终授权守卫。
3. 普通用户界面、日志摘要和诊断导出不能泄露内部品牌、密钥线索、路径、环境变量或原始异常。
4. Electron IPC 只能信任当前产品 WebUI，而不是任意 localhost 页面。
5. 离线授权不会被描述为能够抵抗 root 修改二进制或完整系统镜像克隆。

### 交付不变量

1. 源码声明的产品能力必须存在于最终解包 payload。
2. `/opt/taiji-agent` 是可替换、不可变的程序层；用户数据只写入 XDG 用户层。
3. 制包机输入制品与客户目标机交付制品必须分离。
4. 目标机在任何特权和破坏性操作前必须验证签名与全部哈希。
5. Fresh install、upgrade、failed rollback 是不同生命周期，不得用 purge 重装模拟升级。
6. macOS 静态验证、Linux 制包、离线演练和真实 Kylin/UOS 验收必须使用不同证据标签。

## 非目标

- 不在本轮重写 WebUI 框架或迁移到 React/Vue。
- 不把 Agent、授权或 DOCX 拆成新的常驻系统服务。
- 不把单 DEB 主线改成多包依赖图。
- 不为了减少内部兼容名而机械重命名全部上游源码。
- 不在修复过程中擅自把产品版本从 `0.1.0` 提升为 `1.0.0`。
- 不承诺一个 DEB 支持所有国产 x86 终端；当前主线仍是 Debian-like amd64 桌面系统。
- 不使用测试通过数量代替真实 WPS/Word、离线 VM 或目标机证据。
- 不在本轮全面拆分所有巨型文件；只在触及边界处提取有明确单一职责的模块。

## 方案比较与决策

### 方案一：局部补丁

分别修复历史传递、授权变量、DOCX staging、焦点和文案。短期改动最少，但继续保留多套会话、配置、状态、版本和发布真相源，不能解决复发根因。

### 方案二：产品契约脊柱与纵向切片

保留现有技术栈和部署形态，建立统一产品版本、能力/payload contract、会话权威、授权权威、阶段权威和签名发布证据，然后以可独立验证的纵向切片实施。

这是本次选定方案。

### 方案三：全面服务化重构

新增 SessionService、License Daemon、DOCX Service 和新前端应用。边界更纯粹，但会引入 IPC、服务编排、升级顺序、目标机兼容和更大的回归面，不符合当前阶段的风险收益比。

## 总体架构

```text
文档成果优先首页
        │
        ▼
WebUI 会话 / 专家团工作台
        │
        ├── Agent state.db       托管会话历史唯一权威
        ├── Expert Team view     阶段与进度唯一权威
        ├── Product diagnostics  安全摘要唯一入口
        └── DOCX API
               │
               ▼
      包内 Node + DOCX Engine
               │
               ▼
      最终 DOCX + WPS/Word 终验

不可变安装层 /opt/taiji-agent
├── VERSION / 产品 manifest / 授权公钥
├── Agent / WebUI / Electron
├── Node / DOCX Engine / 内置模板
└── allowlist 产品 Skills

可写用户层 XDG
├── 配置、授权设备身份、会话、附件、workspace
├── 用户模板与用户 registry
├── 状态、诊断、升级记录和快照
└── Skills 用户态安装与缓存

源码 → 制包机输入包 → staged payload → 签名目标交付包
     → fresh install / upgrade / failed rollback
     → 离线演练 → Kylin/UOS 目标机证据
```

## 产品体验设计

### 首页信息架构

首页继续保留聊天输入作为工作台主心骨，但产品价值和旗舰场景前置。

建议首屏文案：

- 主标题：`让本地专家团把材料做成可交付成果`
- 副标题：`起草办公材料、整理本地文件、分阶段复核，并交付可打开的文档。`

首屏场景顺序：

1. 起草办公材料：打开内容创作专家团详情。
2. 开展专题研究：打开深度研究专家团详情。
3. 分析本地文件：填充安全提示并聚焦主输入框。
4. 创建定时任务：进入定时任务页面。

`运行系统命令` 不再作为旗舰场景。高级 Agent 能力保留在通用聊天和相应设置中。

专家团 CTA 只打开团队详情，不静默创建会话。用户查看团队职责、成果形态和需求后显式启动。

### 导航与术语

- 当前 Cron 页面所有用户可见 `任务` 统一为 `定时任务`。
- Kanban/工作事项保留 `工作任务` 或 `看板任务` 语义，不与定时任务混用。
- `running/done/error/generated_invalid` 等内部状态不得作为中文 UI 的兜底文本。
- 未知状态显示 `状态未知` 并提供安全恢复动作。
- Provider、模型 ID 等专有名称可以保留，但不得与配置文件、环境变量和内部命令一起展示。

### 主内容、辅助内容和高级内容

- 主内容：当前业务目标、所选专家团、真实阶段、当前下一步和最终 DOCX。
- 辅助内容：成员职责、阶段说明、修改意见、来源与质量摘要。
- 高级内容：本地路径、质量报告、运行诊断、完整交付目录、模型/Provider 技术配置。

高级内容使用设置、折叠区或管理员入口，不与普通用户主操作同权展示。

### 角色与权限边界

当前产品是单用户离线桌面应用，不在本轮引入应用内 RBAC。角色分层按产品表面与操作系统权限实现：

| 角色/表面 | 可见能力 | 禁止能力 | 权限边界 |
|---|---|---|---|
| 业务用户 WebUI | 文档主链、定时任务、安全诊断摘要、导出脱敏支持包 | 原始日志、密钥、路径、签名、升级内部步骤 | 当前桌面会话认证 |
| 本机支持人员 | 脱敏支持包、incident ID、产品健康检查 | License 私钥、release 私钥、未脱敏用户内容 | 显式用户确认 + 本机文件权限 |
| 系统管理员 CLI | 安装、升级、回滚、root-owned trust anchor 和系统日志 | 绕过签名、授权或数据快照 gate | `sudo` 与 OS 审计 |
| 制包/签发人员 | 构建、签名、密钥轮换、发布证据 | 客户 session、附件、workspace | 隔离制包机与外部密钥存储 |

`设置 → 系统` 不提供“打开原始日志”。业务用户只能运行安全诊断、复制安全摘要和导出经过预览确认的脱敏支持包。原始系统日志只通过管理员 CLI/OS 权限访问。

### 文档成果主链与成果对象

文档主链使用单一状态机：

```text
collecting_materials
→ requirements_pending
→ expert_running
→ phase_review_pending
→ content_ready
→ docx_generating
→ docx_ready
→ office_review_pending
→ accepted | revision_required | failed
```

规则：

- 需求确认是执行前门，不计入专家团阶段进度。
- 每个阶段完成后进入 `phase_review_pending`；用户可以确认、提交修改意见或取消后续执行。
- `5/5` 只说明内容阶段完成。只有 `content_ready` 后才能创建 DOCX 作业。
- DOCX 生成失败不得覆盖最后一份可用内容或旧 DOCX；修复输入/模板后创建新的生成版本。
- `docx_ready` 后成果区主操作是 `打开最终 DOCX`，完整交付目录和质量报告退居高级区域。
- Office 终验失败进入 `revision_required`，保留失败证据并回到内容或 DOCX 修订，不把失败文件标记为已交付。
- 新建同类任务创建新的 run/artifact，不复用已完成 run 的状态。

成果对象至少包含：

```json
{
  "artifact_id": "artifact_...",
  "session_id": "session_...",
  "expert_team_run_id": "run_...",
  "source_refs": [{"name": "材料名称", "sha256": "..."}],
  "phase_revision": 3,
  "template": {"id": "general-proposal", "version": "...", "sha256": "..."},
  "job_id": "docx_job_...",
  "document_version": 2,
  "status": "office_review_pending",
  "document_sha256": "...",
  "quality_status": "passed_with_warnings",
  "office_review_id": null,
  "created_at": "...",
  "updated_at": "..."
}
```

所有状态迁移带幂等 mutation ID。重试同一 mutation 不创建重复 run/job/artifact；重新生成文档必须显式递增 `document_version`。

### WPS/Word 人工终验协议

- 验收责任人由当前用户明确填写，不从系统用户名推断。
- 固定检查：打开、目录/标题/正文/表格/图片可见、编辑一处文字、保存、关闭、重开、修改仍存在。
- 记录 Office 产品和版本、OS、artifact/document hash、验收开始/结束时间、检查项、失败项、备注和证据文件 hash。
- 证据保存到 XDG 状态层的 artifact 专属目录，不能写 Engine root。
- 只有文档 hash 与验收开始时一致才允许提交通过；文档变化后旧验收自动失效。
- `passed_with_warnings` 仍要求人工终验；自动检查永远不能直接写 `accepted`。

### 定时任务产品契约

定时任务是辅助自动化能力，不在本轮自动驱动需要人工逐阶段复核的专家团/DOCX 主链。现有通用计划任务继续支持：创建、查看、编辑、启用/停用、立即运行、查看历史、失败重试和删除。删除需要确认，停用可恢复，失败历史不被新运行覆盖。

结果保存在定时任务运行历史，并可关联到对应会话；只有未来单独设计“无人值守文档流程”后，才允许定时任务自动产生待 Office 终验的 DOCX。

### 前端功能契约

| 能力 | 数据/API/状态 | UI 入口 | 用户反馈/错误 | 空/加载/禁用/破坏性状态 | 键盘/可访问性 | E2E/证据 | 当前状态 |
|---|---|---|---|---|---|---|---|
| 首页旗舰场景 | Catalog + feature visibility | 首屏四张场景卡 | 加载失败提供重试 | 无目录时解释并保留聊天 | 中文名称、Enter/Space | UX-01/02/12 | 失败，实施未开始 |
| 材料收集/需求确认 | session attachments + requirements view | 专家详情和需求表单 | 字段错误关联；保留输入 | 空材料、上传中、格式失败、提交禁用 | label、错误关联、焦点顺序 | UX-03 | 失败，状态覆盖不完整 |
| 阶段执行/复核/修改 | Expert Team View + mutation ID | 右侧工作台和成果区 | running/review/failed/retry | `0/5`、加载、取消、失败、重试 | 进度语义、复核控件名称、焦点保持 | UX-03/14 | 失败，进度误导 |
| DOCX 生成/重生成 | artifact + docx job API | 成果区主按钮 | 生成、版本、失败、incident ID | content 未就绪禁用；旧成果保留 | 状态播报；成功后焦点到成果 | UX-04/05 | 失败，专家团成果未绑定 artifact |
| 打开 DOCX/Office 终验 | artifact open + Office review API | 打开最终 DOCX、记录验收 | hash、检查项、失败退回 | 未生成禁用；hash 变化使旧验收失效 | 按钮名称、表单 label、错误关联 | OFFICE-01~04 | 失败，验收协议未实现 |
| 定时任务 CRUD | cron API + run history | 一级导航和首页卡 | 创建/编辑/启停/运行/重试/删除反馈 | 空、加载、禁用、删除确认/取消 | 所有图标名称、键盘表单、焦点返回 | UX-06 | 失败，命名与状态契约不完整 |
| 系统诊断/支持包 | safe diagnostics schema | 设置 → 系统 | 运行中、readiness、恢复动作 | 降级、导出中、取消、预览确认 | live region、可操作预览 | UX-09~11 | 失败，无安全 UI 入口 |
| Onboarding | onboarding state + config readiness | 首次进入和继续初始化 | 当前步骤、配置错误、恢复 | loading、缺凭据、关闭不完成 | 首焦点、Tab 环绕、Escape、焦点返回 | UX-07 | 失败，焦点/状态安全问题 |

所有能力的浏览器测试使用上述稳定用例 ID；未执行的状态必须标记 `未验证`，不能从静态合同推断通过。

## 会话与运行契约

### 权威边界

- `state.db` 是服务端托管 session 的唯一推理历史权威。
- WebUI session JSON 继续保存草稿、附件、UI 元数据和展示副本，不参与 Agent 历史恢复。
- Response Store 保存 Responses API 对象、索引和 `session_id`，不成为第二份会话历史。

### `/v1/runs` 规则

- 接受 `X-Hermes-Session-Id`，兼容现有 body `session_id`。
- Header 与 body 同时存在但不同，返回 `400 session_id_conflict`。
- 新托管 session 只能由已认证的 session 创建接口生成服务端 ID；`/v1/runs` 收到未知 ID 返回 `404 session_not_found`，不得隐式创建。
- 有已知 session ID 时为托管模式，历史从 `SessionDB.get_messages_as_conversation()` 加载。
- 认证通过后仍校验 session owner/profile；不属于当前主体时统一返回 `404 session_not_found`，不泄露存在性。
- 已存在的托管 session 同时提交显式历史时，只有与 DB 一致才接受；不一致返回 `409 session_history_conflict`。
- 同一 session 同时只能有一个 active run；第二个返回 `409 session_busy`。
- 202 响应 body 和响应头都返回实际 session ID。
- 未认证请求不得选择或续接任意 session ID。
- 没有 session ID 时继续支持显式 `conversation_history` 或多消息 input 的无状态模式。

历史一致性使用 `managed-session-history/v1` canonical schema。消息只包含固定字段：`role`、有序 content parts（text 或 image/file sha256 引用）、tool call 的稳定 ID/name/RFC 8785 arguments、tool result 的 call ID/content sha256，以及公开 assistant content；不包含 reasoning、模型名、时间戳、UI 字段或临时路径。对象使用 UTF-8、无 BOM、RFC 8785 JSON Canonicalization Scheme 序列化，并计算 SHA-256 digest。该 schema 纳入 versioned contracts。DB 写入由 run orchestration 在持有 session lease 时完成，每个 user/assistant/tool 消息按稳定 message ID至多写一次。

session busy 使用跨进程 DB lease，而不是进程内布尔值。lease 记录 run ID、owner、worker PID/process-start token、获取时间、续租时间和过期时间；权威 worker 状态来自 durable run registry 与 OS PID/start-token 双重核对。完成、失败、取消时释放，进程崩溃后由过期回收器恢复；回收前必须确认 registry 无活动 heartbeat 且 PID/start-token 不再匹配，避免双执行。

### WebUI Gateway 规则

- `runs` 托管模式只传 session ID、当前输入、指令和附件，不重复发送 WebUI 历史。
- `chat_completions` fallback 继续发送完整 `messages`，保持兼容。
- 附件转换、取消、工具事件、usage 和最终 assistant 消息在两种 transport 下使用同一产品事件契约。

## 专家团状态契约

- `view.workflow.progress.done` 只等于 `status == done` 的执行阶段数。
- 需求确认不计入正式执行阶段。
- 首阶段运行但未完成时仍显示 `0/5`，同时单独显示 `当前：流程安排 · 生成中`。
- 首阶段完成后才显示 `1/5`。
- 前端不得根据 `current_index + 1`、artifact 数量或聊天正文重新推导完成数。
- 后端 Catalog/View 是团队、成员、阶段、完成数和当前动作的唯一来源。
- 旧 `_WRITEFLOW_TEAMS` 和前端固定三阶段常量迁移为兼容适配或删除，不能继续作为平行领域模型。

## 错误、恢复与可观察性

### 用户错误信封

所有用户可见错误使用统一结构：

```json
{
  "ok": false,
  "code": "expert_team_start_failed",
  "message": "专家团暂时无法启动，请重试。",
  "recovery": "retry",
  "incident_id": "ET-20260711-AB12"
}
```

规则：

- `message` 必须是安全中文产品文案。
- `code` 是稳定机器可读值，不包含路径或第三方原始消息。
- `recovery` 只能使用已实现动作，例如 `retry`、`open_settings`、`open_diagnostics`、`contact_admin`。
- `incident_id` 关联受控原始日志。
- 前端不得把 `exception.message`、任意响应 body 或 SSE 原始 `error.message` 直接拼入 Toast/聊天正文。
- 阻断错误使用 `role=alert`；加载、保存成功和普通状态使用 `role=status` 与 `aria-live=polite`。
- 启动失败时保留用户需求、附件和当前会话，允许原地重试。

核心错误码和恢复动作：

| 场景 | code | recovery | 幂等/成果保留规则 |
|---|---|---|---|
| 需求字段不完整 | `requirements_invalid` | `fix_input` | 不创建 run，保留全部输入 |
| 专家团启动失败 | `expert_team_start_failed` | `retry` | 复用 mutation ID，不重复创建 run |
| 阶段生成失败 | `expert_phase_failed` | `retry` | 保留已完成阶段和复核意见 |
| DOCX Engine 不可用 | `docx_engine_unavailable` | `open_diagnostics` | 不改变 content/artifact 旧版本 |
| 模板缺失/不兼容 | `docx_template_unavailable` | `choose_template` | 不创建输出版本 |
| DOCX 生成失败 | `docx_generation_failed` | `retry` | 同 job 重试幂等；显式重生成才递增版本 |
| 文件打开失败 | `document_open_failed` | `retry_or_open_folder` | 不改变 artifact/验收状态 |
| Office hash 已变化 | `office_review_stale` | `restart_review` | 旧 review 保留为失效记录 |
| 定时任务保存失败 | `scheduled_task_save_failed` | `retry` | 表单和原任务保持不变 |
| 定时任务运行失败 | `scheduled_task_run_failed` | `retry` | 新历史记录追加，不覆盖旧失败 |
| 支持包导出失败 | `support_bundle_export_failed` | `retry` | 清理临时文件，不暴露原始错误 |

### 诊断摘要

诊断 API 使用 allowlist，仅返回：

- 产品版本和组件版本。
- Agent/WebUI 健康状态。
- 授权状态代码，不返回 license 原文或密钥线索。
- 当前配置是否就绪，不返回 API Key 后缀或环境变量名。
- 活动 run/stream 数量，不返回内部 ID。
- 包内 Node、DOCX Engine、内置模板和 allowlist Skills 是否就绪。
- XDG 配置/数据/状态/workspace 是否可读写及可用磁盘空间等级，不返回路径或精确容量。
- 定时任务调度器是否可用及失败任务数量，不返回任务正文。
- 可执行恢复动作。

以下信息禁止进入普通用户摘要或剪贴板：客户名、机器标签、用户名、HOME、完整路径、端口、完整进程命令、原始日志、内部兼容名、密钥后缀、JWT、PEM、URL 凭据、原始异常和提示词。

脱敏支持包使用 versioned allowlist schema。导出前显示包含项预览并要求用户确认；默认排除 session 正文、附件、workspace、配置原文、环境变量和完整进程信息。临时包权限为 `0600`，导出取消或失败时清理。日志写入前先脱敏并执行大小/保留期轮转，`incident_id` 的原始日志查询只允许管理员 CLI并记录审计事件。

## 授权与执行安全

### 生产策略

生产授权策略固定为：

- `required = true`
- `machine_binding_required = true`
- `allow_legacy_machine_binding = false`
- 验签公钥为产品内置公钥

生产 `require_valid_license()` 不再从用户环境读取这些安全决策。

以下生产覆盖被禁止：

- `TAIJI_LICENSE_REQUIRED`
- `TAIJI_LICENSE_MACHINE_BINDING_REQUIRED`
- `TAIJI_LICENSE_ALLOW_LEGACY_MACHINE_BINDING`
- `TAIJI_LICENSE_PUBLIC_KEY`
- `TAIJI_LICENSE_PUBLIC_KEY_FILE`

若生产启动环境包含覆盖意图，执行入口返回 `license_policy_override_forbidden` 并 fail-closed。测试和签发工具使用显式纯函数参数或测试 policy，不保留生产后门。

`覆盖意图` 定义为任一禁止变量存在于 `os.environ`，无论其值是否看似等于生产策略。生产 factory 不接受 policy/public-key 参数，也不允许配置、CLI、HTTP、IPC、插件或调用方选择 test policy。

授权 canonical 资源：

- 产品验签公钥：固定 `/opt/taiji-agent/resources/license/signing-public.pem`，安装时 root-owned、`0644`、父目录不可由普通用户写，并校验编译/发布期记录的 fingerprint。
- 用户授权文件：`~/.config/taiji-agent/licenses/active-license.jwt`，必须是当前用户拥有的普通文件、`0600`，拒绝 symlink/hardlink、越界 realpath 和宽松权限。
- 设备身份：`~/.config/taiji-agent/license-device.json`，同样要求当前用户、`0600` 和非链接文件。
- 防回拨状态：`~/.local/state/taiji-agent/license-state.json`，同样要求当前用户、`0600` 和非链接文件。

生产 `taiji_license.py` 只从固定产品公钥路径读取，不内嵌第二份 PEM。源码层可保存预期 fingerprint 用于检测包内资源被替换，但不提供另一个可用于验签的公钥来源。

纯验证函数可以显式接收测试 key/policy，以验证 JWT 算法和声明；生产执行守卫通过无参数 production factory 构造，无法调用测试 factory。测试/签发私钥、测试公钥、`--disable-license` 参数和 test policy selector 不进入目标 payload，payload contract 对这些标记做反向检查。

### 最终守卫

- HTTP/WebUI 授权检查负责友好反馈。
- `AIAgent.run_conversation()` 在 Provider 初始化、模型调用和工具执行前执行最终守卫。
- CLI、Cron、Gateway、Runs、Responses 和 WebUI 绕过 HTTP 路由时仍受最终守卫保护。
- 授权文件和状态文件路径由集中解析器决定，并限制在产品允许根目录内。
- 旧 v2/legacy 授权不通过环境临时放行；通过明确迁移提示重新签发 v3。
- 授权文件、公钥、设备身份或状态文件缺失、为空、解析失败、owner/mode 错误、路径越界或为链接时全部 fail-closed。

### 离线威胁模型边界

本设计阻断正常产品运行链中的环境关闭、公钥替换、路径重定向和直接入口绕过。它不宣称能够阻止 root 修改安装代码、调试进程、替换二进制或完整复制系统镜像。更强威胁模型需要 TPM/HSM、在线激活、局域网授权服务或 VDI 管控集成，属于后续独立项目。

### 资产—攻击者—控制矩阵

| 资产/风险 | 攻击者能力 | 本轮控制 | 明确非目标/剩余风险 |
|---|---|---|---|
| License 策略/公钥 | 普通用户改 env/config/用户文件 | 固定 production factory、root-owned 公钥、文件权限和最终守卫 | root 改二进制 |
| Release 私钥 | 制包机普通进程、日志或仓库泄露 | 外部 `0600` 密钥、独立签名环境、key ID/轮换/撤销 | 签名机完全失陷需组织级应急响应 |
| 目标交付包 | U盘/传输介质篡改、旧包重放 | 独立 trust anchor、签名闭包、sequence/version、root staging revalidation | 操作员同时信任被替换的外部根 |
| 构建依赖 | 镜像/依赖投毒 | locked/hash、批准缓存、断网阶段、SBOM | 上游已签名依赖本身存在未知漏洞 |
| 用户会话/附件 | 诊断导出、日志或路径泄露 | allowlist 支持包、写入前脱敏、权限/保留期 | 有权读取用户 HOME 的本机管理员 |
| Electron IPC | 恶意 localhost、导航、同源 XSS | webContents/frame 绑定、强制 CSP、channel/参数 allowlist | 已获得 root/代码执行的攻击者 |
| 升级数据 | 中断、磁盘不足、不可逆迁移 | 空间预检、快照、journal、旧 DEB、兼容迁移、恢复验证 | 跨文件系统无法全局原子，可能进入人工恢复 |
| 版本授权 | 旧包重放或越权升级 | root VERSION、manifest version/sequence、license max_version preflight | 离线撤销信息未送达前的窗口 |

## Electron 信任边界

- IPC sender 必须同时匹配当前 WebUI origin、实际端口和桌面访问令牌，而不是只判断 loopback。
- 敏感 IPC 还必须绑定创建窗口时记录的 `webContents.id`、top-level `senderFrame` 和当前 main frame；不信任 renderer 自报 URL。
- BrowserWindow 禁止导航到非当前 WebUI origin。
- 使用 `will-navigate` 和 `setWindowOpenHandler` 拒绝未授权跳转/新窗口。
- 目录选择、剪贴板读取和支持包保存等敏感 IPC 各自校验 sender、参数 schema、路径 allowlist、大小限制和必要用户手势。
- 默认拒绝未知 IPC channel，并禁用/限制 webview、非产品 iframe、权限申请、下载、外部协议和任意 `shell.openExternal`。
- desktop token 不进入 URL、日志或 localStorage；只通过受限启动握手传递，窗口销毁时清除。
- 将 CSP 从 Report-Only 迁移为经兼容测试的强制策略；保持 `webSecurity=true`、`allowRunningInsecureContent=false`，并设置显式 permission handler。
- 保留 `contextIsolation=true`、`nodeIntegration=false` 和 sandbox。

## 产品版本与能力 manifest

根目录新增 `VERSION`，当前保持 `0.1.0`。产品版本必须驱动：

- DEB Version。
- Desktop app/package metadata。
- `taiji --version`。
- `TAIJI_AGENT_VERSION` 与授权 max_version 判断。
- release manifest、构建报告、`.build-success`。
- 诊断与升级比较。

Agent upstream、DOCX Engine、模板等版本保留在 `component_versions`，不伪装成产品版本。

生产授权版本判断直接读取 root-owned `/opt/taiji-agent/VERSION`；`TAIJI_AGENT_VERSION` 只是由启动链派生的只读兼容输出，不能作为生产授权输入。开发 checkout 从仓库根 `VERSION` 读取。

新增可机器校验的 payload contract，声明：

- 每个组件的 source、destination、version、checksum、owner、group、mode、license 和是否可执行。
- 必需组件/路径、允许额外文件规则和禁止文件/路径。
- ELF/架构约束，以及禁止 setuid/setgid、world-writable、设备文件、FIFO、越界 symlink/hardlink 和绝对链接。
- 产品 Skills allowlist 和反向 allowlist 外文件检查。
- 离线资产、第三方许可、SBOM 与 NOTICE 要求。
- 产品版本和 contract schema 版本。

## 最终安装布局

```text
/opt/taiji-agent/
├── VERSION
├── runtime/
│   ├── agent/
│   │   ├── venv/
│   │   └── skills/
│   ├── web/
│   ├── node/bin/node
│   └── docx-engine-v2/
│       ├── src/
│       ├── templates/
│       ├── template-registry.json
│       └── node_modules/
├── apps/taiji-desktop/
├── scripts/
├── config/
└── resources/
```

用户可写层：

```text
~/.config/taiji-agent/
~/.local/share/taiji-agent/runtime-home/
~/.local/share/taiji-agent/workspace/
~/.local/share/taiji-agent/docx-engine-v2/
├── template-registry.json
└── installed/
~/.local/state/taiji-agent/
```

DEB metadata 从 root `VERSION` 和正式产品维护信息生成，不使用 `support@example.invalid`。目标 preflight 在任何状态变更前校验 Linux、amd64、Debian-like、apt/dpkg/systemd、GUI、管理员能力，以及 manifest 声明的发行版/glibc/依赖兼容范围；不兼容时返回明确支持矩阵结果，不进入清理或安装。

## DOCX Engine 与用户模板

- 包内提供固定 Linux x64 Node，目标机不依赖系统 Node。
- packaged mode 只接受 ownership、mode、ELF/架构和 manifest hash 均通过的包内 Node；缺失或损坏时 fail-closed。
- PATH fallback 仅允许显式 dev/portable mode，且该模式不能由普通产品运行环境切换。
- Engine root 和内置模板只读。
- 用户模板安装到 XDG `docx-engine-v2/installed/<templateId>`。
- 用户 registry 与内置 registry 在读取时合并；用户 registry 不覆盖同 ID 的系统保留模板，除非有显式版本/覆盖规则。
- portable skill 未传 state dir 时保留 package-local 兼容行为，不破坏已有独立交付包。
- 最终 payload gate 必须实际执行模板列表，并至少渲染一个最小文档。

packaged mode 必须始终传入 XDG state dir，禁止进入 package-local 写入兼容分支。`templateId` 使用固定格式校验，realpath 后必须仍在 `installed/` 下。内置模板 ID 是保留 ID，用户同 ID 安装默认拒绝；本轮不提供覆盖系统模板功能。

## 产品 Skills 与离线要求

- 产品 Skills 使用明确 allowlist 进入 DEB。
- allowlist 之外的 skill 不得因复制整个上游目录意外进入产品包。
- 可执行运行资产不得包含 jsDelivr、unpkg、cdnjs 等运行时 CDN。
- 可执行运行资产不得包含 `/Users/<name>`、`/home/<name>` 等构建机绝对路径。
- web article extractor 的 Readability/Markdown 依赖必须本地 vendor，并附带许可证。
- `.backup`、缓存、测试、开发文档和本地状态不进入 staged payload。

### 运行时代码与源码保密边界

当前技术栈必须交付可读的 Python bytecode/模块、Vanilla JavaScript、Skills、DOCX Engine JavaScript 和第三方依赖。本轮安全目标是禁止完整仓库、Git 历史、测试、构建脚本、内部文档、签发工具、私密配置、无关源码和用户数据进入客户制品；不承诺客户无法阅读或逆向必要运行时代码。

若未来要求保护商业源码，需要单独评估编译、混淆、服务化和法律许可方案，不能在 payload gate 中把“无源码 archive”误写成“运行时代码不可读”。

### 可复现构建与供应链

- Linux 制包使用预先批准并带 hash 的 Node/uv/Electron/依赖制品，不执行未校验的 `curl | sh`。
- Agent 和 WebUI 依赖必须使用锁文件或带 hash 的约束；发布构建禁止从 locked 自动降级为 unlocked。
- 构建断网阶段只能访问制包工作区和批准的本地缓存/镜像。
- 对最终 Python、Node、Electron、Web 静态资产和 Skills 生成 SBOM、第三方许可证清单和 NOTICE。
- 构建后的品牌适配不得对已测试源码做全局盲目文本替换。必要兼容映射在源码或明确转换步骤中实现，并对最终 staged tree 执行语义回归。
- 根仓补齐自身 LICENSE/NOTICE 决策；在法律归属未确认前，release gate 不得把上游 MIT 文件误当作根项目授权结论。

## 制品分离、签名与发布

### 制包机输入包

仅提供给可信 Linux 构建环境：

```text
taijiagent-制包机输入-<commit>.tar.gz
└── 源码、构建脚本、构建配置
```

### 客户目标机交付包

```text
taiji-agent-offline-<version>-amd64.tar.gz
├── 生成的安装包/
├── 离线依赖/
├── 02_目标终端_安装并验证.sh
├── 03_目标终端_导出诊断报告.sh
├── 04_目标终端_升级并回滚.sh
├── taiji-package-manifest.json
├── taiji-package-manifest.sig
├── TARGET-SHA256SUMS.txt
├── release-signing-public.pem
├── release-key-certificate.json
├── release-key-certificate.sig
├── release-revocations.json
├── release-revocations.sig
├── release-minimum-policy.json
├── release-minimum-policy.sig
└── 构建报告.txt
```

目标包禁止包含源码 archive、00/01/99 制包脚本、`.git`、构建日志、构建工具、私钥、JWT、用户日志和 session。

### 签名 manifest

canonical manifest 固定在目标交付根目录，至少记录：

- `product_version`
- `source_commit` 与 source hash
- DEB 名称/hash
- `Packages` 与 `Packages.gz` hash
- `TARGET-SHA256SUMS.txt` hash
- payload contract 版本与 payload tree digest
- build OS/arch/glibc
- 支持矩阵和允许升级来源版本
- component versions

release signing key 与 License signing key 分离。私钥只从制包机外部 `0600` 文件读取，不能进入 repo、日志、源码包、DEB 或目标包。

发布签名密码套件固定为 RSA-PSS、3072-bit、SHA-256、MGF1-SHA256、salt length 32。key ID 是 DER SubjectPublicKeyInfo 的 SHA-256。所有 JSON 控制对象使用 UTF-8、无 BOM、LF、RFC 8785 canonicalization；签名域字符串作为前缀进入签名输入，例如 `TAIJI-RELEASE-MANIFEST-V2\0<canonical-json>`。验签实现和测试向量固定，不允许构建脚本临时选择算法。

信任层次：

```text
离线 recovery root public key（独立预置 trust anchor）
├── 签名 release-key-certificate / rotation / revocation
│     └── 授权 online release signing public key
│           └── 签名 canonical release manifest
│                 └── 通过 files[] hash 覆盖全部目标 payload
└── 签名 fresh-install minimum-policy（最低 sequence/version）
```

- recovery root 私钥离线保存，不参与日常构建；online release key 只用于签 manifest。
- `release-key-certificate` 记录 key ID、SPKI hash、有效期、允许产品/平台和 sequence 范围，由 recovery root 签名。
- rotation/revocation 只能由 recovery root 签名；被攻破的 release key 无权自我轮换或自我撤销。
- manifest 记录单调递增 `release_sequence`。Fresh install 的最低 sequence/version 由独立预置的 root-signed minimum-policy 提供；upgrade 同时拒绝低于本机 max-seen sequence 或 revocation policy 的候选包。

签名闭包使用无环 DAG：外部 recovery trust anchor → root-signed key certificate/revocation/minimum-policy → release public key → manifest signature → canonical manifest → `files[]` payload inventory。

`files[]` 列出安装、升级、诊断实际消费的全部文件：主 DEB、所有离线依赖 `.deb`、`Packages`、`Packages.gz`、02/03/04 脚本、构建报告、payload contract、SBOM、NOTICE 和 `TARGET-SHA256SUMS.txt`。checksum 清单只列普通 payload 文件，不包含自身、manifest、manifest signature、release public key、key certificate/signature、revocation/signature 或 minimum-policy/signature；manifest 单独记录 checksum 清单 hash，因此没有自引用。

控制文件允许集合仅为 manifest/signature、release public key、key certificate/signature、revocation/signature、minimum-policy/signature。除此之外，目标 archive 中所有文件必须位于 manifest `files[]`；任何未列配置、数据、脚本、包或附加文件均 hard-fail。

### Fresh install 信任根

客户目标包内的 `release-signing-public.pem` 只作为候选材料，不能自证可信。Fresh install 必须先具备以下任一独立信任根：

1. 组织管理员通过设备管理/可信介质预置 root-owned `/etc/taiji-agent/trust/recovery-root-public.pem`、root-signed minimum-policy 与 expected fingerprint；或
2. 操作员通过独立可信渠道取得 recovery root 公钥、minimum-policy 和 fingerprint，使用不属于候选目标 archive 的受信 bootstrap verifier 安装 trust anchor。

没有 root-owned trust anchor 时，目标安装器拒绝继续。不能使用候选包内脚本、候选公钥和候选文档互相证明可信。

Upgrade 只信任已安装、root-owned 的 recovery trust anchor 和 root-signed trust policy。候选包携带的新 release 公钥必须有 recovery root 签名的有效 certificate/rotation record，不能直接替换。max-seen sequence、有效/撤销 key ID 和 policy version 保存在 root-owned trust state；每个轮换、撤销和重放分支都有固定测试向量。

### 校验与 TOCTOU 防护

普通用户权限下先做无副作用预检。获得 sudo 后，将候选闭包安全复制到新建的 root-owned staging 目录；复制过程拒绝 symlink、hardlink、路径穿越和未列文件。随后在 root 权限下重新验证 trust anchor、signature、manifest、全部 hash、owner/mode 和 payload contract。

apt/dpkg 只读取 root-owned staging 中再次验证后的文件。验证完成后目录转为不可写；若复制后任一 inode/hash 变化则中止。`sudo` 本身可以发生在第二次验证前，但停止服务、修改 `/opt`、purge、安装和迁移等任何状态变更只能在 root revalidation 成功后发生。

目标脚本在任何服务停止、purge 或安装前依次验证：

1. 可信公钥 fingerprint。
2. detached manifest signature。
3. checksum 清单 hash。
4. 所有目标文件 hash。
5. 文件系统安全属性与未列文件。

## 安装、升级、备份与回滚

### Fresh install

- `02` 只负责 fresh install 和明确 legacy migration。
- 检测到当前产品版本时拒绝 destructive reinstall，并引导使用升级入口。
- preflight、签名/哈希检查和平台检查在任何清理前完成。

### Upgrade

新增独立升级入口，流程为：

1. 校验签名、版本单调性和兼容范围。
2. 获取 `/run/lock/taiji-agent-upgrade.lock`。
3. 阻止新的 Taiji 写入，停止 Taiji-owned 进程并确认全部目标进程退出；不按端口盲杀。
4. 在静止状态下，对配置、授权、设备身份、防回拨状态、会话、附件、workspace、用户 Skills、用户模板和全部状态数据库建立带 hash 快照。
5. SQLite 使用 Python `sqlite3.backup()`，不在运行时裸复制。
6. 确认当前授权允许目标产品版本，并确认旧 DEB 可用于回滚；缺失时在任何变更前拒绝升级。
7. 使用正常 apt/dpkg upgrade，不 purge 用户数据。
8. 执行迁移、payload audit 和运行态 smoke。
9. 任一步失败，按恢复 journal 重装旧 DEB并分阶段恢复用户快照。
10. 写入 root-owned journal，并在 commit/rollback 后生成用户可读的脱敏状态副本，记录成功、失败和恢复结果。

`postinst` 只处理系统层权限和 desktop cache，不以 root 写用户 HOME，也不吞掉核心验证失败。

升级状态机固定为：

```text
preflight
→ trusted_staging
→ stopped
→ snapshotted
→ package_changed
→ migrated
→ verified
→ committed

任一阶段失败：rolling_back → rolled_back | manual_recovery_required
```

- 每次状态转换先 fsync journal，再执行下一步；重启后根据 journal 幂等恢复。
- root 恢复权威固定在 `/var/lib/taiji-agent/upgrades/<id>/journal.json` 与同目录 snapshot manifest，root:root、目录 `0700`、文件 `0600`；备份位于 `/var/lib/taiji-agent/backups/<id>/`。这些控制文件不属于用户数据快照，用户无法覆盖。
- `$TAIJI_STATE_DIR/upgrades/<id>.json` 只是完成后写入的脱敏展示副本，不参与 root 恢复决策。
- Fresh install 记录 canonical 单用户身份到 root-owned `/var/lib/taiji-agent/installation.json`：UID、GID、经 `getent passwd` 验证的 HOME 和 XDG roots。升级默认只操作该身份；显式管理员选择其他用户时必须重新验证 owner、realpath、非 symlink 和安装归属，绝不使用 root 的 HOME 推断。
- 当前产品安装时把自身 DEB 缓存到 root-owned `/var/cache/taiji-agent/packages/`，升级前验证其 hash 与已安装版本一致。
- 本轮不提供 `--no-binary-rollback` 或用户主动 downgrade；旧 DEB 缺失直接拒绝升级。
- 快照前检查空间、owner、mode 和目标文件系统。可重建缓存不备份，但必须列入恢复后重建清单。
- 多个 XDG 根可能跨文件系统，不能承诺全局原子 rename。恢复语义是 journal 驱动、逐根目录 staged restore；同一文件系统内使用 rename，SQLite 从一致备份恢复，全部 hash 对账后才标记 `rolled_back`。
- 迁移必须声明 forward/backward compatibility。不可逆迁移在旧二进制不能读取新数据时禁止发布。
- maintainer script 和系统依赖的副作用纳入升级测试；无法自动还原的系统级副作用使升级进入 `manual_recovery_required`，不得伪报成功回滚。
- 当前 `0.1.0` 只适用于尚未发布的开发过程；每个客户可安装 release 必须提升产品版本，same-version 替换默认拒绝。

## 配置与领域模型单一真相

- `TAIJI_*` 是产品配置主命名；旧兼容变量只能在单一适配层映射，不能反向成为权威。
- 配置同步失败不能被无条件 `|| true` 吞掉；必须返回可诊断状态或阻止启动。
- Catalog/View 是专家团团队、成员、阶段和动作的唯一领域模型。
- 根 `VERSION` 是产品版本唯一来源。
- payload contract 是包内能力唯一来源。
- release manifest 是目标交付证据唯一来源。
- `state.db` 是托管会话推理历史唯一来源。
- `taiji_license.py` 的 production policy 是授权策略唯一来源；`/opt/taiji-agent/resources/license/signing-public.pem` 是生产验签公钥唯一来源，并校验固定 fingerprint。

Fresh install 不预设一个按产品策略会被拒绝、且没有凭据的图片生成 Provider。`image_gen` 初始状态为 `not_configured`；Onboarding 只提供当前策略允许、集成状态稳定的国内 Provider，并明确 provider、model、key/base URL 和 readiness。历史 `openai-codex/taiji-image` 配置只显示迁移警告，不静默启用或删除。

技术 Skills、memory 和 workspace 可以继续隐藏，但必须提供业务语义替代入口：

- `常用场景`：可重复启动的产品化工作流。
- `单位规范`：模板、写作规范和受控知识材料。
- `历史成果`：按 artifact 展示可追溯成果和版本。

首个产品化场景固定为 `办公材料起草与交付`，输入清单、需求问题、五阶段复核、DOCX 生成和 Office 终验均使用本设计中的结构化契约。招标预审、项目启动、可研审查在未形成同等级 contract 前仍标为演示脚本，不宣传为可重复产品能力。

根 README、产品介绍和运行文档从“实验室”口径调整为产品仓库与兼容层说明，清楚区分上游兼容源码、太极用户表面、运行态和交付态。

## 上游同步与模块治理

- `SOURCE_REFS.md` 记录每个上游仓库的当前 baseline URL/tag/commit、导入时间和本地补丁边界，而不是只记录首次导入。
- 上游同步在临时分支执行，重放太极补丁并保存 `range-diff`、冲突清单和 Agent/WebUI/Desktop/packaging 回归证据；不在销售分支直接 pull。
- 当前分支与 main 的大量积累必须在正式发布前形成可审查基线，禁止把数百个未分层 commit 直接等同于 release branch。
- 本轮只提取触及边界的专用模块：诊断、安全错误、managed dialog、payload verifier、manifest、upgrade journal。`routes.py`、`ui.js`、`panels.js` 等其余拆分另立增量计划，避免混入行为修复。

## 商业与试用证据

代码修复同时提供可执行但不伪造数据的试用包：

- 固定的脱敏办公材料样例和成果验收表。
- 试用任务、前后耗时、修改次数、事实纠错、Office 结果和采纳状态的记录模板。
- 当前支持矩阵、实施前提、数据边界、培训和支持范围。
- SLA、报价、客户案例和 ROI 只有在负责人确认及真实数据存在后才能发布；仓库不得用占位数字或内部 85.1 分替代。

## 测试策略

所有行为修改严格执行 RED → GREEN → REFACTOR。每个修复先运行新增测试并确认因缺少目标行为而失败，再写最小实现。

RED/GREEN 是开发过程证据，不是产品发布结论。每个新增行为保存失败命令/预期原因和修复后命令/结果到实施记录；已有正确行为补回归测试时，不为了制造 RED而破坏代码。最终 release gate 只根据当前行为、制品和环境证据判断。

### 运行态与授权

- 同 session 第二轮从 DB 恢复第一轮历史。
- 响应 body/header 返回一致 session ID。
- 未认证请求不能续接任意 session。
- 同 session 并发返回 409。
- 显式历史与 DB 冲突返回 409。
- 无状态历史兼容不回归。
- `runs` 与 fallback 的附件、取消、工具事件、usage、错误 finish state 和最终消息持久化保持 parity。
- `TAIJI_LICENSE_REQUIRED=0` 不放行。
- 攻击者环境公钥不被信任。
- 关闭机器绑定/开启旧绑定的环境变量不生效。
- 直接 Agent、CLI、Cron、Gateway 和 HTTP 路径都在 Provider/工具前阻断。

### DOCX 与 payload

- staged payload 和真实解包 DEB都必须包含 Node、DOCX Engine、内置模板和 allowlist Skills。
- PATH 无系统 Node 时仍使用 packaged Node。
- Engine root `0555`、用户 state dir 可写时能安装并重新列出用户模板。
- 可执行 Skill 出现 CDN 或构建机绝对路径时 gate 失败。
- allowlist 外 skill 进入包时 gate 失败。
- 最终 payload 实际执行模板列表和最小渲染。

### 发布、签名与升级

- 未签名、错误签名、篡改 manifest、Packages、DEB 或安装脚本在任何服务停止、安装或迁移前失败；普通用户预检先报错，root-owned staging 中再次验证。
- RSA-PSS 参数、RFC 8785 canonical bytes、key ID 和签名域使用固定正/负测试向量。
- 缺 recovery trust anchor、错误 minimum-policy、被撤销 release key、过期 certificate 和低 sequence 重放均拒绝。
- 被攻破 release key 不能签发 rotation/revocation；只有 recovery root 签名记录生效。
- manifest/checksum/control files 的无环 DAG 可构造，任意未列数据文件和自引用闭包均拒绝。
- 只从交付根目录加载 canonical manifest。
- 目标包包含源码或制包脚本时失败。
- 陈旧源码包、commit/hash 不一致、空 `生成的安装包/`、空离线仓库和多份历史制品使 freshness gate 失败。
- `1.0 → 1.1` 模拟保留配置、授权、会话、附件、workspace、Skills 和用户模板。
- 注入安装后验证失败时恢复旧 DEB与全部用户数据 hash。
- SIGKILL/断电注入覆盖每个 upgrade journal 状态；重启后只读取 root-owned journal 并幂等恢复。
- 用户篡改 XDG 升级状态副本不影响 root 恢复；sudo 环境切到 root HOME时仍定位 canonical 用户。
- 无旧 DEB时明确拒绝自动 rollback 声明。
- downgrade 默认拒绝。

### 前端与 UX

- 首页旗舰卡可见、可键盘触发并进入正确目标。
- `任务` 到 `定时任务` 的导航、标题、空态和动作一致。
- `done=0` 时显示 `0/5`，第一阶段完成后才显示 `1/5`。
- 内部状态和原始异常不进入用户界面。
- Onboarding 和专家详情满足首焦点、Tab 环绕、Escape 和焦点返回。
- 活跃图标按钮有中文 accessible name，表单有 label。
- 阻断错误和普通状态使用正确 live region。
- 设置只有一个纵向滚动容器，200% 缩放时焦点控件不被遮挡。
- 诊断摘要 UI 可见且不包含敏感字段。
- 定时任务创建、查看、编辑、启停、立即运行、历史、失败重试和确认删除均有状态/恢复测试。
- 文档主链覆盖 `collecting_materials → accepted/revision_required`、版本重生成、旧成果保留和幂等 mutation。
- WPS/Word 提交覆盖 document hash 变化后旧验收失效。

### 根 CI

根仓 CI 至少串联：

- Desktop syntax/security contract。
- Agent runs/session tests。
- Agent license/执行守卫测试。
- WebUI Gateway、专家团、错误和可访问性契约。
- DOCX Engine 单元/契约测试。
- 根包装、签名、升级模拟和 payload contract。
- `git diff --check`、相关 lint 和静态品牌/隐私 gate。

Linux-only staged payload、DEB、离线 repo 和安装测试在 Linux amd64 runner 执行；macOS job 不冒充 Linux 证据。

## Versioned contracts 与证据记录

以下对象各有独立 JSON Schema 和 `schema_version`：

- 产品 capability/payload contract。
- `managed-session-history/v1` canonical message schema。
- release manifest、release-key certificate、minimum-policy、key rotation 和 revocation record。
- document artifact、DOCX job、quality report 和 Office review。
- upgrade journal、snapshot manifest 和 rollback report。
- safe diagnostics summary 和 support bundle manifest。
- verification evidence record。

每份验证证据至少绑定：source commit、artifact hash、产品/组件版本、OS/arch、执行命令或人工步骤、fixture、开始/结束时间、退出码/结论、日志/截图/报告路径和执行人/验收人。`.build-success` 只是 manifest 引用的一个结果文件，不能独立证明构建成功。

## 真实浏览器验收矩阵

| ID | 角色/环境 | Given | When | Then | 证据 |
|---|---|---|---|---|---|
| UX-01 | 业务用户 / Electron 1440×900 | 全新隔离状态 | 首次打开首页 | 首屏出现文档成果定位、四个场景和专家团 CTA | 截图、可访问树、录屏 |
| UX-02 | 业务用户 / Chromium 1280×800 | 首页已加载 | 键盘触发内容创作卡 | 进入团队详情，不创建 run，焦点进入 dialog | Playwright trace、截图 |
| UX-03 | 业务用户 / Chromium 1440×900 | 三个必填需求已回答 | 确认启动并完成五阶段复核 | 状态依次 `0/5 → 5/5 → content_ready`，最终结论仍在聊天区 | API fixture、trace、截图 |
| UX-04 | 业务用户 / Chromium 1440×900 | `content_ready` | 生成 DOCX、失败一次、重试成功 | 旧成果保留，新 artifact version 生成，主按钮为打开最终 DOCX | API/文件 hash、trace |
| UX-05 | 业务用户 / Electron | `docx_ready` | 打开 DOCX并进入 Office 验收 | artifact 进入 `office_review_pending`，结果焦点移动到主操作 | Electron trace、artifact JSON |
| UX-06 | 业务用户 / Chromium 1024×768 | 无定时任务 | 创建、编辑、停用、启用、立即运行、重试失败、删除并取消/确认 | 所有 CRUD、历史、反馈和恢复路径可见，术语统一为定时任务 | Playwright trace、截图 |
| UX-07 | 仅键盘 / 1280×800 | Onboarding 未完成 | Tab、Shift+Tab、Escape、重新打开 | 焦点循环；Escape 不提交完成；关闭后焦点返回；可继续初始化 | trace、焦点日志 |
| UX-08 | 仅键盘 / 1280×800 | 专家中心已加载 | 打开/关闭团队详情 | 首焦点、Tab 环绕、Escape/遮罩关闭、触发卡焦点返回 | trace、焦点日志 |
| UX-09 | 业务用户 / 1024×768、200% 缩放 | 设置 → 系统 | 遍历所有控件并导出支持包 | 只有一个纵向滚动容器；焦点不被遮挡；支持包先预览确认 | 截图、trace |
| UX-10 | 业务用户 / Chromium 1440×900 | 后端注入带路径、env、内部名的 500 | 执行专家团/DOCX/诊断 | 只显示安全中文、恢复动作和 incident ID，哨兵均不出现 | 哨兵扫描、trace |
| UX-11 | 业务用户 / Chromium 1280×800 | Node/Engine/模板/Skill分别降级 | 运行安全诊断 | 显示聚合 readiness 和恢复动作，不显示路径/端口/原始日志 | API响应、截图 |
| UX-12 | 业务用户 / 移动 390×844 | 首页及两个 dialog | 完成场景选择和关闭 | 单列、无横向滚动、按钮可见、焦点不逃逸 | 移动截图、trace |
| UX-13 | VoiceOver + Safari/产品支持组合 | 首页、表单、错误和成果状态 | 完成核心键盘路径 | 控件有名称；进度、DOCX状态和阻断错误正确播报且不重复 | 人工验收记录 |
| UX-14 | 业务用户 / Electron 重启 | 已有未完成 run 和 `docx_ready` 成果 | 关闭并重启应用 | 会话、阶段、输入、artifact 和主操作恢复，不重复执行 mutation | 前后状态 hash、录屏 |

所有自动浏览器用例使用隔离 runtime/state/workspace，记录 fixture 与清理命令。Electron/VoiceOver/Office 用例明确标记为人工或半自动，不用 Chromium DOM 测试替代。

### WPS/Word 桌面验收矩阵

| ID | Office/OS | Given | 操作 | 通过标准 | 证据 |
|---|---|---|---|---|---|
| OFFICE-01 | 目标环境 WPS | 固定 artifact/hash | 打开、检查结构、编辑文字、保存、关闭、重开 | 结构可读、修改保留、hash/验收记录绑定 | 验收 JSON、截图、文件 hash |
| OFFICE-02 | 支持环境 Word | 同一固定 artifact/hash | 同上 | 与 WPS 相同，不出现格式阻断 | 验收 JSON、截图、文件 hash |
| OFFICE-03 | WPS/Word | 验收中的文档被重新生成 | 尝试提交旧验收 | 系统拒绝并要求对新 hash 重新验收 | API记录、截图 |
| OFFICE-04 | WPS/Word | 任一检查项失败 | 提交失败项和备注 | artifact 进入 `revision_required`，旧 DOCX仍可追溯 | artifact/验收 JSON |

## 交付证据标签

只有满足对应实时证据时才能使用以下标签：

- **源码包已准备**：当前 commit 的唯一源码包、basename checksum 和 source preflight 通过。
- **制包机已构建**：Linux amd64 产生 DEB、签名 manifest、构建报告、离线 repo、hash 和 `.build-success`。
- **离线安装已演练**：干净 Linux amd64 环境只用本地制品完成安装、验证、卸载和重装。
- **目标机已验证**：真实 Kylin/UOS/openKylin 通过桌面启动、CLI、真实模型对话、附件、DOCX、窗口退出和诊断导出。

WPS/Word 打开、编辑、保存、重开是独立人工证据。任何一项未执行都必须写 `未实时验证`。

## 兼容与迁移

- 依赖生产 `TAIJI_LICENSE_REQUIRED=0` 的开发流程迁移到显式测试 policy。
- 旧机器绑定通过重新签发 v3 迁移，不保留环境后门。
- 旧 WebUI 和新 Agent 混用时可能发生 session history conflict，因此 Gateway 与 Agent 会话契约必须同版本交付。
- 老客户端忽略新增 session ID 时单轮行为不变；多轮客户端必须保存响应 session ID。
- Response Store 历史记录保持只读兼容，以现有 `session_id` 过渡到 `state.db`，不进行一次性破坏性重写。
- 内置模板保持只读，首次启动时为用户创建空用户 registry；已有 package-local 用户模板通过显式迁移工具复制到 XDG 并记录 hash。
- 现有用户配置继续读取旧兼容字段，但写回只使用 `TAIJI_*` canonical 字段。
- 当前产品版本保持 `0.1.0`，版本提升属于发布决策，不与修复混在一起。

## 逐项问题覆盖

| 审计问题 | 设计归属 | 完成证据 |
|---|---|---|
| 多轮历史丢失 | 会话与运行契约 | 真实双轮测试 + DB 消息唯一性 |
| runs/fallback 的附件、取消、工具事件、usage、错误和持久化不一致 | Gateway transport parity | 两种 transport 共用 fixture 的行为测试 |
| 授权环境/公钥绕过 | 授权与执行安全 | 对抗签名与所有执行入口测试 |
| DEB 缺 DOCX/Node | 最终安装布局 | 解包 payload + 最小真实渲染 |
| DEB 缺产品 Skills | Skills allowlist | 解包 allowlist 正反向审计 |
| 用户模板写 `/opt` | DOCX 用户状态层 | root 0555 + XDG 安装/重启测试 |
| 首页定位分散 | 首页信息架构 | 桌面/移动浏览器主路径 |
| 专家团、DOCX 和 WPS 结果链断开 | 文档成果主链 | `0/5 → accepted/revision_required` artifact 状态证据 |
| 技术复用能力隐藏且无业务入口 | 常用场景/单位规范/历史成果 | 可发现 UI + 数据真值契约 |
| 咨询演示脚本冒充产品场景 | 产品化场景边界 | 只有具备结构化 contract 的场景进入正式目录 |
| 新装图片 Provider 与国产策略冲突 | 配置单一真相 | fresh install `not_configured` + 国内 Provider onboarding |
| 任务/Cron 歧义 | 导航与术语 | UI 文案与入口契约 |
| `done=0` 显示 `1/5` | 专家团状态契约 | 后端与前端进度回归测试 |
| 原始错误泄露 | 错误信封/诊断 | 哨兵脱敏与浏览器错误态 |
| 弹窗焦点问题 | 前端功能契约 | 键盘焦点生命周期测试 |
| 图标/表单无名称 | 可访问性契约 | 静态契约 + 浏览器语义检查 |
| 设置滚动遮挡 | UX 布局契约 | 200% 缩放和键盘聚焦 |
| 诊断无 UI 入口 | 诊断摘要 | 设置页浏览器路径 |
| Electron localhost IPC 过宽 | Electron 信任边界 | sender/navigation 安全测试 |
| CSP 仅 Report-Only | Electron/Web 安全边界 | 强制 CSP + 兼容/负向浏览器测试 |
| manifest 路径/哈希不一致 | 签名 manifest | 篡改与 canonical path 测试 |
| 首次安装公钥循环信任 | Fresh install trust anchor | 独立 trust anchor + 错误/缺失根负向测试 |
| 校验后文件可被替换 | root-owned staging | root revalidation + symlink/hardlink/TOCTOU 测试 |
| source archive 泄露 | 制品分离 | 目标 archive 反向内容 gate |
| 陈旧源码包、空安装包/离线仓库 | freshness gate | commit/hash/唯一制品/非空闭包审计 |
| 静态包装测试误绿 | real payload verifier | staged 与 `dpkg-deb -x` 双重行为测试 |
| 升级 purge 无回滚 | Upgrade | 失败注入恢复测试 |
| 诊断输出敏感信息 | 诊断 allowlist | 全类别哨兵测试 |
| 运行时 CDN/绝对路径 | Skills 离线 gate | 最终 payload 可执行资产扫描 |
| 构建联网脚本、unlocked fallback 和范围依赖 | 可复现供应链 | locked/hash/断网构建 + SBOM |
| 第三方许可证和根项目授权不完整 | SBOM/NOTICE/法律 gate | 最终 payload 许可证闭包 + 审核记录 |
| 版本多源 | 根 VERSION | 全产品表面一致性测试 |
| 配置/团队多真相 | canonical adapters | 旧字段兼容与唯一写入测试 |
| 根仓无 CI | 根 CI | CI 配置与本地同命令通过 |
| 巨型模块与上游漂移 | 有界提取/治理 | 触及模块边界与 upstream baseline 文档 |
| README 仍是实验室口径 | 产品仓库文档 | README/架构/运行/交付表面一致性审查 |
| 商业证据缺失 | 真实试用阶段 | 7–14 天试用指标，不由代码测试代替 |

## 完成定义

状态分层，禁止把低层完成升级表述成高层完成：

### 设计完成

- 规格覆盖全部审计问题，无占位、矛盾或未决安全选择。
- 实施计划逐项映射规格，并定义文件、RED/GREEN 命令、commit 和证据。

### 实现完成

- 所有行为修改遵守 TDD，保留相应过程记录。
- 所有新增/修改代码通过局部和相关全量回归。
- 根 CI 覆盖关键产品契约，本地执行同一命令通过。
- 真实浏览器完成桌面、窄屏、移动端、键盘、错误、恢复和重启路径。
- macOS 可执行的静态、签名、schema、前端和单元证据全部完成。
- 该状态不证明 Linux 包或目标机可用。

### 交付候选已验证

- 当前 commit 的源码包和 hash 通过 freshness gate。
- Linux amd64 产生真实 staged payload/DEB/离线 repo/签名 manifest/SBOM/构建报告。
- 真实解包 DEB通过 payload contract、Node/DOCX/Skills smoke。
- 干净 Linux amd64 环境断网完成安装、验证、卸载、重装、upgrade 和 failed rollback。
- 任何一项未实时验证都不能使用该状态。

### 目标交付完成

- 绑定同一 source commit、artifact hash 和产品版本的真实 Kylin/UOS/openKylin 目标通过桌面启动、CLI、真实模型、附件、文档主链、窗口退出和诊断导出。
- 指定 WPS/Word 完成打开、编辑、保存、重开和失败退回协议。
- 没有未关闭 P0/P1；P2/P3 已列明并经用户接受。
- 任何一项未实时验证都不能使用该状态。

### 发布可放行

- `目标交付完成` 成立。
- Trust anchor、签名、版本、支持矩阵、升级/回滚、隐私、第三方许可和操作文档完成独立发布审查。
- 最终 tracked 工作区干净，所有逻辑任务有独立 commit。
- 最终回复提供修改内容、验证结果、剩余风险、证据标签、commit hash 和 `git status --short`。

### 商业验证完成

- 7–14 天真实岗位试用具有明确样本、任务和统计周期。
- 记录首份可用文档耗时、修改轮次、事实纠错、Office 通过率和采纳率。
- 客户案例、SLA、培训、支持边界、报价和 ROI 只使用经负责人批准的真实数据。

本次“修复以上所有问题”的总体目标只有达到相应外部环境证据并关闭商业证据缺口后才可宣称全部完成。代码实现可以先达到 `实现完成`，但不得借此宣称 `发布可放行` 或 `商业验证完成`。

## 剩余外部证据边界

代码和本机验证可以完成实现、单元/契约测试、浏览器测试、签名逻辑和 macOS 静态 gate。以下证据需要相应环境，不能由本机推断：

- Linux Node/Electron/native npm ABI。
- 真实 `dpkg-deb` maintainer-script 生命周期与 glibc 兼容。
- 完全离线 apt repository。
- Kylin/UOS kysec、ACL、桌面双击和窗口退出。
- 真实模型对话与客户附件流程。
- WPS/Word 打开、编辑、保存和重开。
- 真实用户采纳率、任务耗时、人工修改次数和 ROI。

这些不是从完成定义中删除的项目；环境不可用时必须保持目标未完成或明确处于等待外部验证状态。
