# 专家团 Canonical 正文与 DOCX 企业交付合同实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task by task. Any implementation, debugging, or review must also use `andrej-karpathy-skill`; Office/UI work must use `frontend-ux-qa`.

**Goal:** 让聊天预览、批准正文、DOCX 渲染输入和最终交付文件绑定到同一个 canonical artifact，按明确文种选择专用模板，并通过语义、证据、资产、渲染和 Office 五类门禁阻止“外观正式、内容不可信”的文档交付。

**Architecture:** 不推倒现有 DOCX Engine v2。保留摘要、不可变快照、一次性 Office token、证据目录和 WPS/Word 人工验收；在其前面增加企业语义合同。`StageArtifactV1` JSON 是权威容器，批准后的 `deliverable_markdown` 是唯一正文；磁盘 `canonical/document.md` 只是该字段的确定性投影。DOCX 引擎从 Brief 接收独立 metadata 和显式模板，不再用万能 `general-proposal` 或 adapter 补造业务内容。

**Tech Stack:** Python 3、Node.js、DOCX Engine v2、Markdown AST、模板 registry/adapter、SHA-256、pytest、Node test runner、WPS/Word 人工验收。

---

**Prerequisites and cross-plan order:** 先达到第一份计划的 `BRIEF_CONTRACT_IMPLEMENTED`（不等待第四份计划负责的 `BRIEF_ENTERPRISE_USABLE`），以及第二份计划 Task 1–8（其中 Task 7 只建立 model/system dispatcher seam，不要求真实 renderer）。随后执行本计划 Task 1–6，建立 canonical、模板、renderer identity、binding 和质量合同；再回到第二份计划 Task 9 接通两团真实 delivery manifest；然后执行本计划 Task 7 的 Office/completion API，接着执行第四份计划 Task 1–6（rollout 仍 off）；本计划 Task 8 与第四份 Task 7 联合运行同一两条真实黄金路径，分别沉淀 Office 与 Electron/rollout 证据。不得与前两份计划并行修改同一 `runtime.py/documents.py` 区域，也不得用 mock manifest 通过真实集成门。

## 1. 当前能力的取舍

### 1.1 必须保留

- run/stage/attempt/turn/stream 的强身份绑定；
- delivery 文件摘要与不可变快照；
- Office 文件打开时间、证据目录、可信审核人和一次性 token；
- 完成前对 DOCX 与验收证据的再次摘要校验；
- DOCX XML、资产位置、尺寸和基础打开完整性检查；
- 现有 general proposal 与 meeting minutes 作为 legacy 模板继续可用。

### 1.2 必须纠正

- `final.md` 与富内容包 `draft.md` 同时成为不同正文源；
- 最终阶段再次让模型重写全文；
- 工作汇报、研究报告等全部落入 `general-proposal`；
- adapter 写死“技术方案、客户单位、北京太极、V1.0、内部资料、2026年7月”；
- adapter 为满足数量要求补造表格、图片、工程结论或“待补充”；
- Mermaid 源块和派生图片同时进入正文，导致同一逻辑图重复；
- `passed_with_warnings` 混淆语义问题、机械告警与 Office 未验收；
- Office 结论默认通过，问题没有结构化定位和返修闭环。

## 2. Canonical 磁盘合同

沿用当前交付根目录：

```text
.taiji/expert-team-deliveries/<run-id>/<stage-id>/attempt-<delivery-attempt>/
├── brief.json
├── canonical/
│   ├── artifact.json
│   └── document.md
├── assets/
│   └── asset-manifest.json
├── reviews/
│   └── semantic-gates.json
├── delivery/
│   ├── document.docx
│   └── quality-report.json
├── expert-team-delivery.json
├── expert-team-wps-acceptance.json
├── expert-team-waiver-ledger.json
├── expert-team-completion-transaction.json
└── expert-team-office-review-proof.json
```

规则：

1. `canonical/artifact.json` 是已批准 `reviewed_document` 或 `reviewed_research_document` 的不可变快照；
2. `canonical/document.md` 使用 UTF-8、LF、恰好一个尾换行，必须逐字节等于对 artifact `deliverable_markdown` 做同一规范化后的投影，不允许二次生成、填充或润色；
3. 聊天“查看完整成果”读取同一 artifact；
4. DOCX 引擎只读取 `canonical/document.md` 和独立 metadata，不读取最后消息、raw output 或另一份富内容草稿；
5. `expert-team-delivery.json` 是 Office 前不可变 binding；acceptance、服务端 waiver ledger 和 completion proof 分别独立写入，禁止循环引用；
6. 任一被绑定对象变化都会使 Office 验收失效：canonical pointer 变化同时增加 document revision 并产生新的 render input；模板、资产、renderer identity/profile 或其他渲染输入变化只产生新的 render input；Brief 启动后冻结，修改 Brief 需新建 run。任何情况都不能覆盖历史文件。

三类编号都由 run 内权威 counter/reservation 管理，不能用文件数或时间猜测：

- `stage_artifact_attempt`：按第二份计划的逐阶段 allocator 分配；
- `document_revision`：只有 canonical pointer 的 artifact hash 真正变化时，才在现有 run 锁内对 `document_revision_counter` 幂等 +1；相同 pointer、相同幂等键和崩溃恢复复用原 revision；
- `delivery_attempt`：先构造不可变 `RenderInputBindingV1` 并计算 `render_input_fingerprint`，其规范 JSON 至少包含 Brief revision/hash、canonical artifact/Markdown hash、asset manifest hash、semantic gates hash、template id/version/package hash、renderer name/version/build hash/profile id/profile hash。fingerprint 只与 **当前 active reservation/current binding 的同一幂等 lineage** 比较：同 lineage 的幂等请求、进程崩溃恢复和结果回调重放复用同一 attempt，当前成功 binding 的重复读取直接返回既有结果；一旦 current ref 已推进或旧 attempt 被 invalidated/superseded，后续渲染即使与某个历史 fingerprint 相同，也必须在 run 锁内对 `delivery_attempt_counter` +1 并原子写新 reservation，绝不能复活旧目录、acceptance 或 completion。

“系统重试”本身不是 +1 条件：当前 active lineage 内、相同 fingerprint 的恢复继续原 attempt；正常修复导致 fingerprint 相对 current binding 变化时产生新 attempt。A→B→A 这类回滚虽然新 fingerprint 与历史 A 相同，但历史 A 已 superseded，因此仍分配单调递增的新 attempt C，旧 Office/waiver/proof 永不重新生效。磁盘继续沿用现有 `attempt-N` 目录形状，N 明确解释为 delivery attempt，避免破坏路径解析器；同一 delivery attempt 绝不覆盖。stage/document/delivery 三类 counter 在同一 run 锁和幂等账本下分配，但不可互相推导。

每个新的 render generation 还必须按第二份计划的 system-stage allocator 产生新的 delivery `stage_attempt` 和不可变 `delivery_manifest` StageArtifact：同一 active lineage 的恢复同时复用 stage/delivery 两类 attempt；rerender/new fingerprint 则在同一 run mutation 中 supersede 旧 system reservation/current manifest ref，并分别预留新的 stage attempt 与 delivery attempt。只增加 delivery attempt 而覆盖旧 manifest，或只增加 stage attempt 却复用 superseded delivery 目录，均为合同错误。

### 2.1 `expert-team-delivery.json`：Office 前不可变 binding

```json
{
  "schema_version": "expert-delivery-binding/v2",
  "session_id": "session-id",
  "run_id": "run-id",
  "stage_id": "delivery",
  "delivery_attempt": 1,
  "document_revision": 1,
  "render_input_fingerprint": "64-hex",
  "brief": {"revision": 3, "sha256": "64-hex"},
  "canonical_artifact": {"artifact_id": "polish:1", "sha256": "64-hex"},
  "canonical_markdown": {"path": "canonical/document.md", "sha256": "64-hex"},
  "asset_manifest": {"path": "assets/asset-manifest.json", "sha256": "64-hex"},
  "semantic_gates": {"path": "reviews/semantic-gates.json", "sha256": "64-hex"},
  "template": {"id": "enterprise-work-report", "version": "1.0.0", "package_sha256": "64-hex"},
  "renderer": {
    "name": "docx-engine-v2",
    "version": "exact-version",
    "build_sha256": "64-hex",
    "profile_id": "enterprise-default",
    "profile_sha256": "64-hex"
  },
  "document": {"path": "delivery/document.docx", "sha256": "64-hex"},
  "automatic_quality_report": {"path": "delivery/quality-report.json", "sha256": "64-hex"}
}
```

### 2.2 完成证明关系

```text
expert-team-delivery.json（不可变，Office 前）
  ↓ delivery_binding_sha256
expert-team-wps-acceptance.json（人工验收）
  + expert-team-waiver-ledger.json（可信授权快照，可为空）
  ↓ acceptance_sha256 + waiver_ledger_sha256
expert-team-office-review-proof.json（完成时原子写入）
```

- Acceptance 绑定 `delivery_binding_sha256` 以及 canonical/template/document 三类 hash；
- Completion proof 绑定 delivery binding、acceptance、可信 reviewer、可信 waiver ledger、token provenance 和最终 gate 汇总；
- pre-Office binding 不包含未来 acceptance hash；
- contract v1 继续沿用现有三个 sidecar 文件名并升级 schema reader，旧 schema 读取为 `legacy_unverified`；
- 完成采用可恢复提交协议：先写 `prepared` completion transaction，acceptance/waiver ledger 可以落盘但不代表完成，completion proof 固定最终证据集合；随后写权威 run `workflow_state=completed` 并把 transaction 标为 `committed`；
- 启动、读取或重试时对“acceptance 已写、token 已消费、proof 未写”等中间态做幂等 reconciliation，不能依赖同进程 try/except 回滚多文件。

Completion transaction 最小字段：

```json
{
  "schema_version": "expert-completion-transaction/v1",
  "transaction_id": "server-generated-id",
  "state": "prepared",
  "run_id": "run-id",
  "expected_run_version": 42,
  "delivery_binding_sha256": "64-hex",
  "office_acceptance_sha256": "64-hex",
  "waiver_ledger_sha256": "64-hex",
  "completion_proof_sha256": null,
  "prepared_at": "server-RFC3339",
  "committed_at": null
}
```

唯一企业完成谓词同时要求：当前 binding/acceptance/waiver/proof 摘要闭合；proof 的 transaction ID 匹配；transaction=`committed`；权威 run `workflow_state=completed` 且 current delivery refs 指向同一 attempt。proof 已写/run 未 completed、run completed/transaction 未 committed 等窗口都显示“正在完成/待恢复”，reconciliation 收敛前不能绿色通过。

Completion proof 最小字段：

```json
{
  "schema_version": "expert-completion-proof/v1",
  "session_id": "session-id",
  "run_id": "run-id",
  "stage_id": "delivery",
  "delivery_attempt": 1,
  "delivery_binding_sha256": "64-hex",
  "office_acceptance_sha256": "64-hex",
  "waiver_ledger_sha256": "64-hex",
  "completion_transaction_id": "server-generated-id",
  "gate_statuses": {"content": "passed", "document": "passed", "office": "passed"},
  "reviewer": {
    "principal_id": "trusted-reviewer",
    "role": "document-reviewer",
    "auth_source": "trusted-auth-provider",
    "identity_snapshot_sha256": "64-hex"
  },
  "completed_at": "ISO-8601"
}
```

## 3. DOCX 引擎输入合同

### 3.1 `runDocumentJob()` 新输入

```javascript
await runDocumentJob({
  sourcePath,
  sourceType: "markdown",
  templateId: brief.document_control.render_template_id,
  documentMetadata: {
    title: brief.exact_title,
    documentType: brief.document_type,
    client: brief.document_control.client,
    issuer: brief.document_control.issuer,
    compiler: brief.document_control.compiler,
    versionLabel: brief.document_control.version_label,
    classification: brief.document_control.classification,
    classificationLabel: brief.document_control.classification_label,
    documentDate: brief.document_control.document_date
  },
  canonicalBinding: {
    artifactId: canonical.artifact_id,
    artifactSha256: canonical.sha256,
    briefRevision: brief.confirmed_revision,
    briefSha256: brief.confirmed_sha256
  },
  rendererIdentity: {
    name: "docx-engine-v2",
    version: exactVersion,
    buildSha256,
    profileId: "enterprise-default",
    profileSha256
  },
  renderInputFingerprint,
  assetManifestPath,
  assetDir,
  deliveryDir
});
```

metadata 是输入合同的一部分，不从 H1、文件名或 adapter 默认值猜测。缺少模板必填字段时返回 typed failure，例如 `brief_incomplete`；模板与文种不兼容时返回 `template_selection_required`。Node CLI/Python bridge 必须提供一个无副作用的 `describeRendererIdentity()` 合同，在 reserve 前得到 exact version、构建 hash 和 profile hash；job 开始时再次核对，运行中漂移返回 `renderer_identity_changed` 并不得写 binding。`renderInputFingerprint` 必须可由两端按同一 canonical JSON 算法复算。

### 3.2 Template manifest

每个模板声明：

```json
{
  "id": "enterprise-work-report",
  "version": "1.0.0",
  "documentTypes": ["work_report"],
  "requiredMetadata": ["title", "issuer", "compiler", "versionLabel", "classification", "documentDate"],
  "contentPolicy": {
    "allowAdapterGeneratedBusinessContent": false,
    "allowPlaceholders": false
  }
}
```

模板 hash 放在 manifest 外部的 `template-package.binding.json`，避免 manifest 自我哈希循环：

```json
{
  "schemaVersion": "docx-template-package-binding/v1",
  "files": {
    "manifest.json": "64-hex",
    "schema.json": "64-hex",
    "data-adapter.js": "64-hex",
    "template.docx": "64-hex"
  },
  "packageSha256": "sha256(canonical files map)"
}
```

binding 文件自身不进入 files map。沿用当前 Engine 的 `documentTypes` 字段；package digest 对实际渲染包逐文件 hash map 的规范 JSON 计算，不能只 hash manifest 或 template.docx。新 `enterprise-*` adapter 只能映射字段和版式，不能补造单位、日期、密级、表格、图片、行动项或业务结论；legacy adapter 保持原路径，不因此被宣称满足新合同。

## 4. 首批模板策略

新增两个正式模板包：

```text
docx-engine-v2/templates/enterprise-work-report/
docx-engine-v2/templates/enterprise-research-report/
```

首批分流：

| document_type | template_id | 目录/结构策略 |
|---|---|---|
| `work_report` | `enterprise-work-report` | 管理层汇报结构，不采用公众号标题 |
| `research_report` | `enterprise-research-report` | 研究问题、证据、分析、结论边界、引用 |
| `meeting_minutes` | `meeting-minutes` | legacy-only，可继续使用但不标企业正式放行 |
| 技术方案 | `general-proposal` | legacy-only，不再作为 contract v1 fallback |

模板不得修改批准正文的章节标题或凑目录项；若正文结构与模板契约不匹配，应在渲染前失败并回到语义阶段。

## 5. 图表唯一身份合同

顺序号 `fig-001` 只能作为显示编号，不能承担跨修订业务身份。资产 manifest 使用：

```json
{
  "logical_asset_id": "stable-uuid",
  "asset_revision": 2,
  "asset_version_id": "sha256(logical-id+source-hash+render-profile)",
  "kind": "diagram",
  "source": {
    "type": "mermaid",
    "path": "source.mmd",
    "sha256": "64-hex"
  },
  "display": {
    "path": "figure.png",
    "sha256": "64-hex",
    "renderer": "name-and-version",
    "render_profile_sha256": "64-hex"
  },
  "derived_from": {"section_id": "sec-2", "block_id": "block-7"},
  "occurrences": [
    {"occurrence_id": "occ-1", "block_id": "block-7", "allow_repeated": false}
  ]
}
```

规则：

- runtime 根据 `document_id + asset_request_id + block_id` 首次确定并持久化 `logical_asset_id`，packager 只能消费，不能用随机 UUID 或顺序号重新分配；
- `logical_asset_id` 跨修订稳定，内容变化增加 `asset_revision`；
- Mermaid 可编辑源保存在资产目录，正文只保留一个 figure reference；
- 一个 occurrence 默认只生成一个 DOCX drawing；
- 合法重复必须有多个明确 occurrence 且 `allow_repeated=true`；
- 相同图片摘要但不同逻辑 ID 只产生 `duplicate_asset_suspected`，不能简单按 hash 删除 Logo 等合法复用；
- 图号“图 1、图 2”在 render plan 阶段生成，不写回 canonical 正文。

新合同路径完全绕过 `build_rich_draft_package()`，直接由 DOCX Engine 归一化 canonical Markdown、渲染 Mermaid 并生成 render plan；富内容 draft 只保留给 legacy/阶段预览。`assets/asset-manifest.json` 是逻辑身份源，DOCX Engine 的 `delivery/asset-package.json` 是确定性投影，二者不得各自维护另一套图身份或派生 Markdown 正文。

## 6. 分层质量门

不得再用一个模糊的 `passed_with_warnings` 表示全部质量。报告固定包含：

```text
brief_status
semantic_status
evidence_status
asset_status
render_status
office_status
delivery_status
```

| 门 | 阻断内容 |
|---|---|
| Brief | 标题、文种、模板、封面字段不完整或不兼容 |
| Semantic | 标题漂移、结构不符、流程话术、占位符、adapter 补造业务内容 |
| Evidence | 必须引用的 claim 缺来源、时间、定位或摘要 |
| Asset | 同一逻辑图意外重复、来源/派生关系丢失、出现次数不符 |
| Render | DOCX/XML 损坏、资产丢失、顺序/尺寸/分页机械错误 |
| Office | 人工未验收、存在 blocking issue、验收绑定已失效 |
| Delivery | 任一上游门未通过，或最终 binding hash 不一致 |

企业模式下每个 warning 必须有 severity、稳定 target ID、责任人和处置结果。首批 contract-v1 不允许豁免阶段、semantic 或 automatic warning：必须修复并由新 artifact/report 证明 warning 消失，否则 content/document gate 持续阻断。`WaiverV1` 仅用于 Office 人工验收发现的非 blocking condition；blocking issue 永远不可 waiver。

职责固定为：

- WebUI 校验 Brief/Semantic/Evidence，写不可变 `reviews/semantic-gates.json`；
- DOCX Engine 校验 Asset/Render，写 `delivery/quality-report.json`，并绑定上游 semantic gate hash，不自行猜测 Brief 或 claim；
- Office sidecar记录人工验收；
- WebUI 汇总七类 gate 并决定专家团是否 completed。

DOCX Engine 当前 `job.status=delivered` 保留，语义仅为“低层交付包已产生”。即使该状态为 delivered，只要 Office pending，专家团仍不得 completed，UI 不得显示绿色企业交付完成。

### 6.1 `TrustedPrincipalV1` 与角色来源

contract v1 不把当前 `getpass` 用户名、profile 名、客户端显示名或未经验证的代理 header 当作可信身份。Office 验收、阶段批准和 waiver mutation 统一从服务端 `TrustedIdentityResolver` 获取经过密码学验证且仍在有效期内的 principal：

```json
{
  "schema_version": "trusted-principal/v1",
  "principal_id": "enterprise-subject-id",
  "auth_source": "oidc-pkce:corp-idp",
  "assurance_level": "enterprise_authenticated",
  "roles": ["document-reviewer", "document-approver", "waiver-authorizer"],
  "authenticated_at": "server-RFC3339",
  "expires_at": "server-RFC3339",
  "credential_jti_sha256": "64-hex",
  "identity_snapshot_sha256": "64-hex"
}
```

首批正向 provider 固定为桌面可用的 `oidc_pkce`（或部署已存在、满足同等验证合同的企业认证 adapter）：后端发起 Authorization Code + PKCE，验证 state/nonce，交换 code 后校验 JWT 签名、固定算法、issuer、audience、时间窗口、subject 和 token ID，再通过管理员控制的 exact role mapping 得到角色。浏览器只持有随机 HttpOnly/SameSite 的本地 identity session handle；principal/role/auth source/assurance 不能由请求体提交，OIDC token 不暴露给普通前端状态。普通用户名只可作为 UI hint，永远不进入 `TrustedPrincipalV1`。配置最小形状为：

```yaml
expert_team_trusted_identity:
  mode: oidc_pkce
  issuer: https://id.example.internal
  audience: taiji-expert-team
  client_id: taiji-desktop-public-client
  authorization_endpoint: https://id.example.internal/oauth2/authorize
  token_endpoint: https://id.example.internal/oauth2/token
  redirect_uri: http://127.0.0.1:<reserved-port>/api/expert-teams/identity/callback
  scopes: [openid, profile]
  account_switch_mode: select_account
  account_switch_prompt: select_account
  end_session_endpoint: https://id.example.internal/oauth2/logout
  jwks_uri: https://id.example.internal/.well-known/jwks.json
  allowed_algorithms: [RS256]
  allowed_key_fingerprints: [sha256:operator-approved-key]
  subject_claim: sub
  role_claim: roles
  role_allowlist: [document-reviewer, document-approver, waiver-authorizer]
  max_token_age_seconds: 3600
  require_distinct_waiver_authorizer: true
```

配置缺失、未知 mode、PKCE/state/nonce/callback 不闭合、JWKS/签名/issuer/audience/key fingerprint 不闭合、token 过期、角色不在 allowlist 或认证服务不可达时一律 fail closed。JWKS 可缓存但必须受 TTL 和已批准 key fingerprint 双重约束；过期缓存不能继续放行。identity session 与普通 WebUI/model OAuth 完全隔离，token 只保存在后端短时内存或经企业批准的 OS secret store，绝不写 config/run/sidecar/localStorage/日志；登出、过期、进程重启或角色撤销使 session 失效。每个 mutation 都从后端 session 重新解析当前 credential，并把脱敏 identity snapshot/hash 写入 acceptance/approval/waiver 审计。

若 `require_distinct_waiver_authorizer=true`，不能把“本地 logout 后重新登录”当作账号已经切换：企业 IdP 的系统浏览器 SSO cookie 可能仍返回原 reviewer。`POST /api/expert-teams/identity/start` 支持服务端校验后的 `purpose=authorizer_handoff`；客户端只提交 run/review ID，服务端从当前 acceptance 派生并在 flow 内绑定 `required_role=waiver-authorizer`、`disallowed_principal_id=<reviewer>`、run/acceptance/delivery binding hash，不接受客户端提交这些身份约束。provider 必须配置并在 capability probe 中证明支持经批准的 `select_account`/强制重新认证，或 RP-initiated logout + reauth；否则 `authorizer_handoff_ready=false`，conditions 只能返修，不能建议用户手工清 Cookie。

handoff callback 重新校验全部 OIDC 条件；若仍返回原 reviewer、缺 authorizer role 或绑定对象已变化，返回 `authorizer_must_be_distinct` / `trusted_authorizer_required` / `identity_flow_stale`，不创建 authorizer session、不写 waiver，并允许前端保留非敏感问题草稿后显示“换账号重试”。只有不同 principal 且角色/flow/binding 全匹配才建立短时 authorizer session；成功或失败都消费该一次性 handoff flow。

服务端提供 `GET /api/expert-teams/identity/status`、`POST /api/expert-teams/identity/start`、loopback callback 和 `POST /api/expert-teams/identity/logout`；start/callback 必须绑定当前 WebUI session、一次性 flow ID、purpose、state、nonce、PKCE verifier 和严格 redirect allowlist。identity start/logout 及所有有权 mutation 还要校验现有 WebUI CSRF token、精确 loopback Host/Origin 和 session binding，阻断跨站请求与 DNS rebinding；callback 只接受一次并在完成/失败后清理 flow secret。status 只返回安全 principal label/角色 capability/过期时间，以及 `authorizer_handoff_ready`，不返回 token 或 claims。当前项目的模型 provider OAuth 不是人员认证，严禁复用为 reviewer identity。

后端 capability/status 只暴露 `identity_provider_ready`、`reviewer_role_available`、`approver_role_available`、`waiver_authorizer_role_available`、安全 auth source label 和稳定阻断码，不返回 token、完整 claims 或 key material。部署尚无可信 provider 时，contract v1 可以生成和返修文档，但企业 Office completion 保持阻断；不得用测试 resolver、固定用户名或客户端字段跑真实黄金路径。

### 6.2 可信 `WaiverV1`

warning/condition 的豁免不是模型字段，也不是客户端可提交的“负责人”文本。唯一有效形状由服务端生成：

```json
{
  "schema_version": "expert-waiver/v1",
  "waiver_id": "server-generated-id",
  "target_domain": "office_issue",
  "target_sha256": "64-hex",
  "target_id": "issue-or-check-id",
  "delivery_binding_sha256": "64-hex",
  "authorizer": {
    "principal_id": "authenticated-principal",
    "role": "waiver-authorizer",
    "auth_source": "trusted-auth-provider"
  },
  "reason": "具体业务接受理由",
  "authorized_at": "server-RFC3339",
  "validity": "active"
}
```

首批 `target_domain` 只允许 `office_issue`；`target_sha256` 必须绑定当前不可变 Office acceptance，`target_id` 必须是其中真实存在且 severity 精确为 `condition` 的 issue，且 `delivery_binding_sha256` 必填并匹配 current binding。`stage_artifact | semantic_report | automatic_check` 在 contract v1 pilot 返回 `waiver_target_not_released`，必须走修复/重生成路径；后续若开放需升级 policy、UI 和对抗测试，不能仅扩枚举。客户端 mutation 只提交 target ref 和 reason，服务端从 `TrustedIdentityResolver` 填写 waiver ID、principal、role、auth source 和时间；显示名、前端时间和模型输出一律不可信。target、acceptance、delivery binding、角色授权或当前 revision 变化时 waiver 变为 `invalidated`。

`POST /api/expert-teams/waivers/create` 使用 run 锁、expected version、幂等键和角色授权，写 run 的 append-only waiver ledger；完成前把当前有效集合确定性快照到 `expert-team-waiver-ledger.json`，completion proof 绑定其 hash。若部署未启用可信身份和角色授权，endpoint 返回 `trusted_authorizer_required`：首批只能修复 warning/condition，`passed_with_conditions` 可以保存为审计结论但永远不能完成。

### 6.3 `OfficeAcceptanceV2`

```json
{
  "schema_version": "office-acceptance/v2",
  "delivery_binding_sha256": "64-hex",
  "document_id": "stable-id",
  "document_revision": 1,
  "canonical_sha256": "64-hex",
  "document_sha256": "64-hex",
  "template": {"id": "enterprise-work-report", "version": "1.0.0", "package_sha256": "64-hex"},
  "review_id": "server-generated-review-id",
  "decision": "pending",
  "validity": "active",
  "checklist": {
    "document_opened": "not_checked",
    "title_and_cover_match": "not_checked",
    "genre_and_structure_match": "not_checked",
    "content_order_correct": "not_checked",
    "figures_unique_and_readable": "not_applicable",
    "tables_readable": "not_applicable",
    "headers_footers_pagination": "not_checked",
    "no_placeholders_or_workflow_text": "not_checked",
    "citations_readable": "not_applicable"
  },
  "issues": [
    {
      "issue_id": "issue-1",
      "severity": "blocking",
      "category": "duplicate_figure",
      "section_id": "sec-2",
      "block_id": "block-7",
      "logical_asset_id": "stable-uuid",
      "page": 8,
      "description": "同一流程图重复出现",
      "expected_fix": "保留首次出现并重新检查图号"
    }
  ],
  "evidence": [
    {"path": "evidence/page-8.png", "sha256": "64-hex", "size_bytes": 12345, "media_type": "image/png"}
  ],
  "token_provenance": {"opened_at": "ISO-8601", "delivery_binding_sha256": "64-hex"},
  "reviewer": {
    "principal_id": "trusted-reviewer",
    "role": "document-reviewer",
    "auth_source": "trusted-auth-provider",
    "identity_snapshot_sha256": "64-hex"
  },
  "reviewed_at": null
}
```

`OfficeIssueV1.severity` 严格枚举只有 `blocking | condition`；未知值、大小写变体、空值和额外字段均拒绝。只有 `condition` 能进入 `passed_with_conditions` 和 WaiverV1；`blocking` 永远不可 waiver。服务端维护 versioned Office issue policy，把类别、required checklist 失败和上游 gate 问题映射为允许的 severity：文件/hash 不一致、required checklist failed、标题/文种/密级错误、占位符/流程话术、安全问题和上游 blocking/error/warning 一律强制为 `blocking`；只有策略明确列为可业务接受且 required checklist 全 passed 的视觉/表达类偏差才允许 `condition`。客户端 severity 只是请求值，不能把既有/派生 blocking issue 降级；target category 或 policy 未知时 fail closed。

decision 枚举固定为 `pending | passed | passed_with_conditions | failed`，`validity` 为 `active | invalidated`；checklist 值固定为 `not_checked | passed | failed | not_applicable`。legacy 投影为：pending→not_verified、passed→passed、passed_with_conditions→passed_with_warnings、failed→failed。

服务端放行条件固定为：reviewer 必须来自 `TrustedIdentityResolver` 并具备 `document-reviewer` role，否则返回 `trusted_reviewer_required`；所有策略 required checklist 必须 `passed`；只有服务端模板策略声明 optional/not-present 的项目才可 `not_applicable`，客户端不能任意跳过；任何 required `not_checked/failed` 均阻断。阶段/semantic/automatic warning 必须已经由新 artifact/report 消除；decision=`passed` 要求零 unresolved Office warning/error/issue；decision=`passed_with_conditions` 必须有非 blocking Office issue，且每项都命中当前有效 `WaiverV1` 后 Office gate 才 passed；decision=`failed` 必须有 issue 并进入返修。blocking issue 永不可豁免。canonical、template、renderer、delivery binding 或 DOCX hash 变化立即使旧验收和 waiver invalidated。

## 7. 实施任务

状态迁移固定为：

```text
正文生成完成
  → semantic_approval_required
  → 用户确认内容并写 canonical_document_ref
  → system rendering
  → office_acceptance_required
  → Office 通过并写 completion proof
  → completed
```

内容团由 system `delivery` 阶段承接渲染；研究团在第 6 阶段内部增加“确认内容并进入交付验收”。一次 `approve_stage` 不能同时承担正文批准、DOCX 生成、Office 验收和 completed。

### Task 1：用测试锁定单一正文和绑定关系

**Files:**

- Create: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_canonical_contract.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_delivery_contract.py`

**Step 1: 写 RED 用例**

- Brief 标题与正文 H1 不同，拒绝渲染；
- 正文含 Stage、负责专家、本阶段、复核交付，语义门失败；
- `canonical/document.md` 不等于 artifact `deliverable_markdown` 时失败；
- Markdown 字节规范固定为 UTF-8/LF/一个尾换行；
- pre-Office binding 必须有 session ID、三类 attempt/revision、semantic gate 和 asset manifest hash；
- renderer identity/profile 或 render input fingerprint 不闭合时失败；
- 相同 fingerprint 的幂等/崩溃恢复错误增加 delivery attempt，或新 fingerprint 覆盖旧 attempt 时失败；
- pre-Office binding 不得包含未来 acceptance hash；
- 历史交付只能标 `legacy_unverified`，不得自动升级为企业通过。

**Step 2: 运行并确认 RED**

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_document_canonical_contract.py \
  tests/test_expert_team_delivery_contract.py
```

**Step 3: 保留 RED 证据并继续 Task 2**

记录失败用例和错误摘要，不提交故意失败的分支。Task 2 最小实现完成、测试转 GREEN 后一起提交。

### Task 2：让 WebUI 只打包 canonical artifact

**Files:**

- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/documents.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/delivery_integrity.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/stage_artifacts.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_canonical_contract.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_delivery_contract.py`

**Step 1: 提取确定性投影**

```python
def write_canonical_snapshot(delivery_dir, *, brief, artifact): ...
def write_semantic_gates_snapshot(delivery_dir, *, brief, artifact, approved_inputs): ...
def reserve_document_revision_and_delivery_attempt(run, *, canonical_ref, render_input, idempotency_key): ...
def build_delivery_binding_v2(delivery_dir, *, session_id, brief, artifact, assets, semantic_gates, template, renderer, render_input_fingerprint, document, quality): ...
```

只允许已批准、hash 匹配的 canonical ref。先按 §2 的 run-lock allocator 分配 document revision/delivery attempt；相同 canonical/fingerprint 幂等复用。语义/证据 validator 的结果在此写成不可变 `semantic-gates.json`，使后续 binding 无需等待 Task 6 才获得上游 gate。删除新合同路径中从 `output["content"]`、最后消息或 rich-draft 另造正文的逻辑；legacy 分支原样保留。

**Step 2: 写入不可变目录**

相同 render input fingerprint 的重复/恢复必须返回同一 attempt/binding；内容或 identity 不同则必须形成新 fingerprint 和新 attempt，绝不覆盖。`delivery_integrity.py` 的新合同分支停止硬编码 `final.md` 和旧 schema，同时保留旧 reader。

**Step 3: 运行和提交**

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_document_canonical_contract.py \
  tests/test_expert_team_delivery_contract.py
git add api/expert_teams/documents.py api/expert_teams/runtime.py \
  api/expert_teams/delivery_integrity.py \
  api/expert_teams/stage_artifacts.py \
  tests/test_expert_team_document_canonical_contract.py \
  tests/test_expert_team_delivery_contract.py
git commit -m "feat(webui): bind delivery to canonical expert artifact"
```

### Task 3：扩展 DOCX Engine 输入与领域 schema

**Files:**

- Modify: `hermes-local-lab/sources/docx-engine-v2/src/workflow/run-document-job.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/cli/run-job.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/domain/schemas.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/domain/document-job.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/planning/build-render-plan.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/delivery/write-delivery-package.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/run-job-contract.test.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/domain-contract.test.js`
- Modify: `hermes-local-lab/sources/hermes-webui/api/docx_engine_v2.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_routes.py`

**Step 1: 写 metadata/canonical binding RED 测试**

验证 metadata 独立于 Markdown；缺必填字段、模板不兼容、canonical hash 错误都有 typed failure；输入 JSON 顺序变化不改变规范化摘要。

**Step 2: 最小扩展 schema**

复用现有 schema helper，增加 `DocumentMetadataV1`、`CanonicalBindingV1`、`RendererIdentityV1` 和 `RenderInputBindingV1`；不引入新的验证框架。Python/Node 必须按同一 canonical JSON 算法复算 fingerprint，并把 renderer build/profile identity 写入 binding/quality report。

**Step 3: 贯穿 Python API、CLI、render plan 和 report**

Python bridge、Node CLI、render plan、quality report 和 binding 都可追溯到同一 metadata/canonical binding/asset manifest；adapter 不再解析首个 H1 作为封面唯一来源。

**Step 4: 运行和提交**

```bash
cd hermes-local-lab/sources/docx-engine-v2
node --test tests/run-job-contract.test.js tests/domain-contract.test.js
cd ../hermes-webui
../hermes-agent/venv/bin/python -m pytest -q tests/test_docx_engine_v2_routes.py
git add ../docx-engine-v2/src/workflow/run-document-job.js \
  ../docx-engine-v2/src/cli/run-job.js \
  ../docx-engine-v2/src/domain/schemas.js \
  ../docx-engine-v2/src/domain/document-job.js \
  ../docx-engine-v2/src/planning/build-render-plan.js \
  ../docx-engine-v2/src/delivery/write-delivery-package.js \
  ../docx-engine-v2/tests/run-job-contract.test.js \
  ../docx-engine-v2/tests/domain-contract.test.js \
  api/docx_engine_v2.py tests/test_docx_engine_v2_routes.py
git commit -m "feat(docx): accept canonical document metadata"
```

### Task 4：新增工作汇报与研究报告模板包

**Files:**

- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-work-report/manifest.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-work-report/schema.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-work-report/data-adapter.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-work-report/template.docx`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-work-report/prompt.md`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-work-report/sample.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-work-report/adapter-sample.render-plan.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-work-report/template-package.binding.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-research-report/manifest.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-research-report/schema.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-research-report/data-adapter.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-research-report/template.docx`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-research-report/prompt.md`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-research-report/sample.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-research-report/adapter-sample.render-plan.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/enterprise-research-report/template-package.binding.json`
- Modify: `hermes-local-lab/sources/docx-engine-v2/template-registry.json`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/template-package.test.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/template-data-adapter.test.js`

**Step 1: 先锁定 adapter 禁止行为**

测试缺封面字段时失败；输出中不得凭空出现“客户单位、暂无、待补充、北京太极、2026年7月”；adapter 不得增加业务 section、结论、表格或图片。

**Step 2: 建立两个最小模板包**

模板只处理企业版式、封面、页眉页脚、目录和批准正文的映射。manifest 沿用 `documentTypes` 并声明 required metadata 和 content policy；逐文件 hash 与 package hash 只写入外部 `template-package.binding.json`，不能回写 manifest 形成自哈希。

**Step 3: 注册且禁止 fallback**

新合同中未知或不兼容文种返回 `template_selection_required`；`general-proposal` 不再兜底。

**Step 4: 运行模板测试**

```bash
cd hermes-local-lab/sources/docx-engine-v2
node --test tests/template-package.test.js tests/template-data-adapter.test.js
```

**Step 5: 实际渲染两个 fixture**

使用真实工作汇报、研究报告 fixture，检查标题、封面字段、目录和正文无补造内容；保留生成物供后续目标 Office 人工验收。

**Step 6: 提交**

```bash
cd hermes-local-lab/sources/docx-engine-v2
git add templates/enterprise-work-report/{manifest.json,schema.json,data-adapter.js,template.docx,prompt.md,sample.json,adapter-sample.render-plan.json,template-package.binding.json} \
  templates/enterprise-research-report/{manifest.json,schema.json,data-adapter.js,template.docx,prompt.md,sample.json,adapter-sample.render-plan.json,template-package.binding.json} \
  template-registry.json tests/template-package.test.js tests/template-data-adapter.test.js
git commit -m "feat(docx): add enterprise report templates"
```

### Task 5：修复 Mermaid 和图片的逻辑身份

**Files:**

- Modify: `hermes-local-lab/sources/docx-engine-v2/src/assets/package-rich-draft.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/assets/package-assets.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/source/normalize-markdown.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/planning/build-render-plan.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/domain/schemas.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/delivery/write-delivery-package.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/validation/validate-delivery-package.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/rich-draft-package.test.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/render-plan.test.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/delivery-validation.test.js`

**Step 1: 写重复图 RED 测试**

- Mermaid-only 输入打包后正文只有一个 figure reference；
- manifest 保留 source.mmd、logical asset ID、derived relation 和 occurrence ID；
- 同一 logical asset 的源与派生图只生成一个 drawing；
- 显式多个 occurrence 时严格按数量渲染；
- 重新打包不改变 logical ID；
- 疑似重复图给 typed issue，不静默插入或删除。

**Step 2: 统一新合同图引用模型**

新合同绕过 rich-draft 派生 Markdown：normalize 从 canonical Markdown 和 runtime 提供的 asset manifest 读取稳定 identity，package-assets 渲染 Mermaid 并让 render plan 只消费一个 logical namespace；source.mmd 只进入 asset bundle。`package-rich-draft.js` 仅修复 legacy 重复图并加回归，不能成为新 canonical 来源。

**Step 3: 运行和提交**

```bash
cd hermes-local-lab/sources/docx-engine-v2
node --test tests/rich-draft-package.test.js tests/render-plan.test.js tests/delivery-validation.test.js
git add src/assets/package-rich-draft.js src/source/normalize-markdown.js \
  src/assets/package-assets.js src/planning/build-render-plan.js \
  src/domain/schemas.js src/delivery/write-delivery-package.js \
  src/validation/validate-delivery-package.js \
  tests/rich-draft-package.test.js tests/render-plan.test.js tests/delivery-validation.test.js
git commit -m "fix(docx): preserve unique logical figure identity"
```

### Task 6：实现分层质量报告

**Files:**

- Modify: `hermes-local-lab/sources/docx-engine-v2/src/validation/validate-delivery-package.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/domain/schemas.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/delivery-validation.test.js`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/documents.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/stage_artifacts.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/delivery_integrity.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_document_canonical_contract.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_delivery_contract.py`

**Step 1: 写独立检查项 RED 测试**

WebUI 测试标题不符、文种不符、占位符、工作流泄漏和无来源 claim，并写不可变 semantic-gates；Node 测试重复逻辑图、模板不兼容、DOCX/XML/资产完整性。两层分别产生稳定 code/severity，并通过 hash 绑定，Node 不重新解释 Brief 或 claim。

**Step 2: 拆分 status**

WebUI 汇总报告包含 Brief、semantic、evidence、asset、render、Office、delivery 七个 status。Node 只产生 asset/render 自动报告；保留旧 `passed_with_warnings` 只作为 legacy 兼容投影，不作为新合同完成判断。

**Step 3: 为返修准备稳定 target**

所有 semantic/automatic warning 生成稳定 issue/check ID 并绑定不可变 report hash，供责任定位和新 report 证明已修复；本任务禁止解析模型或客户端内联的 `approved_by/approved_at`。首批这些 warning 一律阻断且不接受 waiver；Task 7 的 `WaiverV1` 只接受 Office acceptance 中的非 blocking issue。

**Step 4: 运行和提交**

```bash
cd hermes-local-lab/sources/docx-engine-v2
node --test tests/delivery-validation.test.js
cd ../hermes-webui
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_expert_team_document_canonical_contract.py \
  tests/test_expert_team_delivery_contract.py
git add ../docx-engine-v2/src/validation/validate-delivery-package.js \
  ../docx-engine-v2/src/domain/schemas.js \
  ../docx-engine-v2/tests/delivery-validation.test.js \
  api/expert_teams/documents.py api/expert_teams/stage_artifacts.py \
  api/expert_teams/delivery_integrity.py \
  tests/test_expert_team_document_canonical_contract.py \
  tests/test_expert_team_delivery_contract.py
git commit -m "feat(docx): add enterprise delivery quality gates"
```

### Task 7：升级 Office acceptance 数据合同

**Prerequisite:** 第二份计划 Task 9 已接通并验证两团真实 delivery binding/manifest；否则不得开始 Office completion 集成。

**Files:**

- Modify: `hermes-local-lab/sources/docx-engine-v2/src/validation/record-wps-visual-acceptance.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/validation/validate-delivery-package.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/src/cli/validate-delivery.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/wps-visual-acceptance.test.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/delivery-validation.test.js`
- Modify: `hermes-local-lab/sources/hermes-webui/api/docx_engine_v2.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/runtime.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/view.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/office_review.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/trusted_identity.py`
- Create: `hermes-local-lab/sources/hermes-webui/api/expert_teams/waivers.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/expert_teams/delivery_integrity.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_routes.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_trusted_identity_contract.py`
- Create: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_waiver_contract.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_delivery_integrity_hardening.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_terminal_reconciliation.py`

**Step 1: 先落地唯一可信身份解析器并保持默认 pending**

第二份计划 Task 6 已创建 `trusted_identity.py`、完成 `document-approver` 阶段批准和默认 fail-closed。本任务必须复用同一个 `resolve_trusted_principal(request_context, required_role, now)`，扩展 `document-reviewer` 与 `waiver-authorizer` 的 action policy、职责分离、安全 capability/status 和审计 snapshot；`office_review.py` 现有 getpass/profile 只能保留给 legacy 显示，contract v1 调用必须显式绕开。routes、stage approval、Office review、waiver 均只消费同一 resolver 返回的不可变 snapshot，不各自解析 header 或请求体，也不得创建 Office 专用身份 fallback。

扩展正/负契约测试：有效签名且具备精确角色时通过；错误 issuer/audience/algorithm/key fingerprint、过期/未来 token、JWKS 过期或不可达、角色缺失、客户端伪造 principal/role/header、普通 getpass/profile 以及 test resolver 进入 production 时全部拒绝。普通 OIDC bearer 的同一有效 jti 允许跨多个独立 mutation 使用，重放控制仍由 expected version + idempotency key 负责；不得误当一次性 Office token。启用职责分离时增加 `authorizer_handoff` 测试：持久 IdP SSO 第一次仍返回 reviewer 必须 typed fail 且零 waiver；经受控 select-account/reauth 返回不同、具备 authorizer role 的 principal 才成功；provider 无账号切换能力、客户端伪造 disallowed principal、run/acceptance/binding 漂移都 fail closed。不得把 bearer token 或完整 claims 写入 run、sidecar、日志或 view。

Office 构造器、API 和 view 默认都返回 pending；任何旧默认 passed 测试必须改成 pending 契约。第四份 UX 计划负责实际抽屉、认证能力提示和 waiver UI，本任务提供稳定数据/API。identity provider 未就绪时返回 `trusted_identity_provider_required`，缺 reviewer/approver/authorizer 角色分别返回稳定角色错误，企业完成状态保持 pending/blocked 而不是 500。

**Step 2: 绑定和问题约束**

验收绑定 `delivery_binding_sha256` 及 canonical/document/template/renderer 四类 identity/hash；Office token 也绑定 delivery binding，不再只绑定 document hash。服务端只从 `TrustedIdentityResolver` 填写 reviewer identity/role/time/snapshot hash；客户端不能提交审核人显示名，无可信 identity 时企业 completion 稳定阻断。required checklist 必须 passed，只有策略声明 optional/not-present 才能 not_applicable。`passed` 要求零未处置 warning/issue；`passed_with_conditions/failed` 必须有结构化 issues；blocking issue 时禁止 passed/conditions。

测试必须证明：`job.status=delivered` 且 Office pending 时专家团不 completed；Brief/canonical/template/DOCX/delivery binding 任一变化使旧 acceptance invalidated；unknown/空/大小写错误 severity、客户端把 policy-derived blocking 降级为 condition、以及对 blocking target 申请 waiver 全部被拒绝，只有 policy 明确允许的 `condition` 可进入 waiver。

实现独立 `/api/expert-teams/waivers/create` 和 `WaiverV1` ledger：服务端生成 ID/authorizer/role/auth source/time，只接受当前 Office acceptance 中 severity 精确为 `condition` 的 issue 并绑定 target/acceptance/delivery binding hash；stage/semantic/automatic target 返回 `waiver_target_not_released`，blocking/未知 severity 返回稳定拒绝码。无可信认证/角色授权时返回 `trusted_authorizer_required`。`passed_with_conditions` sidecar 可先作为审计落盘，但必须等所有 Office condition 获得有效 waiver ledger ref 才能 Office passed；waiver mutation 不复用一次性 Office open token，也不改写 acceptance 内容。

**Step 3: 返修引用**

每个 issue 可选绑定 section/block/logical asset/page，服务端生成明确的 revision request，不把 Office 自由文本直接拼进新阶段 prompt。

新合同路径不得再通过修改 pre-Office quality report 来记录验收。Acceptance 单独写入；token consume、proof 和 run gate 通过 prepared journal 组织为可恢复提交，不能把跨文件动作描述为原子事务。

给 validator 增加 `automated-only/external-office` 模式：contract v1 的自动质量报告不要求内嵌 WPS 结论，WebUI 单独验证 acceptance/proof 后聚合；legacy/generic 路径继续使用当前 `requireWpsVisualAcceptance`。自动报告必须在 delivery binding 前生成，binding 后只读验证，禁止再用 `--write-report` 改写。

完成采用 prepared transaction + reconciliation，不声称多文件事务回滚。proof 绑定当前 acceptance、waiver ledger 和 transaction ID。增加异常注入并覆盖：acceptance 已写/proof 未写、waiver ledger 已写/proof 未写、token 已消费/proof 未写、proof 已写/run 未 completed、run completed/transaction 仍 prepared；重试后必须幂等收敛到“run completed + transaction committed + 摘要闭合”，任何中间态都不显示企业通过。

**Step 4: 运行和提交**

```bash
cd hermes-local-lab/sources/docx-engine-v2
node --test tests/wps-visual-acceptance.test.js tests/delivery-validation.test.js
cd ../hermes-webui
../hermes-agent/venv/bin/python -m pytest -q \
  tests/test_docx_engine_v2_routes.py \
  tests/test_expert_team_trusted_identity_contract.py \
  tests/test_expert_team_waiver_contract.py \
  tests/test_expert_team_delivery_integrity_hardening.py \
  tests/test_expert_team_terminal_reconciliation.py
git add ../docx-engine-v2/src/validation/record-wps-visual-acceptance.js \
  ../docx-engine-v2/src/validation/validate-delivery-package.js \
  ../docx-engine-v2/src/cli/validate-delivery.js \
  ../docx-engine-v2/tests/wps-visual-acceptance.test.js \
  ../docx-engine-v2/tests/delivery-validation.test.js \
  api/docx_engine_v2.py api/expert_teams/runtime.py api/expert_teams/view.py \
  api/expert_teams/office_review.py api/expert_teams/trusted_identity.py \
  api/expert_teams/waivers.py \
  api/expert_teams/delivery_integrity.py api/routes.py \
  tests/test_docx_engine_v2_routes.py \
  tests/test_expert_team_trusted_identity_contract.py \
  tests/test_expert_team_waiver_contract.py \
  tests/test_expert_team_delivery_integrity_hardening.py \
  tests/test_expert_team_terminal_reconciliation.py
git commit -m "feat(docx): bind structured Office acceptance"
```

### Task 8：双黄金路径真实 Office 终验

**Joint gate:** 先完成第四份计划 Task 1–6；本任务与第四份计划 Task 7 使用同一隔离 pilot、同一两条黄金 run 和同一 binding/hash 证据联合执行，并在启用目标 pilot 前共同收齐第二份计划 §9.2 的 10+10 普通样例与两团各 10 个注入对抗样例阈值证据。不得在工作台/真实身份入口尚不存在时用手工 sidecar 代替端到端验收，也不得只用两条 WPS 黄金路径绕过真实模型样本门。

**Files:**

- Create: `docs/reviews/expert-team-enterprise-docx-acceptance-2026-07-15.md`

**Step 1: 生成真实交付物**

分别走完一个工作汇报和一个专题研究报告，记录 session/run/stage artifact attempt、document revision、delivery attempt、Brief revision/hash、canonical hash、模板 package hash、delivery binding hash 和 DOCX hash。

**Step 2: 目标 Office 视觉检查**

目标企业环境使用 WPS 时，WPS 是强制终验；只有产品明确宣称 Word 双兼容时才把 Word 也列为强制矩阵。至少检查：标题与封面、文种结构、目录、正文顺序、图表唯一性、表格、页眉页脚、分页、引用、密级、无占位符和无流程话术。

**Step 3: 对照聊天和 DOCX**

抽查标题、章节和关键段落，证明聊天完整成果与 `canonical/document.md`、DOCX 的内容一致；不能只证明文件可打开。

**Step 4: 保存验收证据并提交报告**

```bash
cd "$(git rev-parse --show-toplevel)"
git add docs/reviews/expert-team-enterprise-docx-acceptance-2026-07-15.md
git commit -m "docs: record expert team enterprise DOCX acceptance"
```

## 8. Release Gate

### 8.1 自动验证

```bash
cd hermes-local-lab/sources/docx-engine-v2
node --test tests/*.test.js

cd ../hermes-webui
../hermes-agent/venv/bin/python -m pytest -q tests/test_expert_team_*.py
npm run lint:runtime
```

所有测试通过只证明自动合同成立，不等于 Office 视觉终验通过。

### 8.2 正式放行条件

- canonical artifact、Markdown 投影、DOCX 输入的 hash 链闭合；
- work report 和 research report 使用各自模板，零 general proposal fallback；
- adapter 不生成任何业务内容或占位符；
- Mermaid 一个逻辑 occurrence 只渲染一次；
- 七类质量状态可分别解释；
- Office decision 初始 pending，结构化问题可回流；
- 两个黄金路径都在目标 WPS 完成人工验收；若宣称 Word 双兼容，另有 Word 证据；
- 任一绑定对象变化会让旧验收失效；
- legacy 交付清楚标记为 `legacy_unverified`。

### 8.3 完成定义

只有“语义同源 + 模板匹配 + 物理完整 + Office 人工通过”同时成立，才能显示企业交付完成。`document.docx` 已生成、WPS 能打开、自动检查有 warning 或工作台显示绿色，都不能单独作为完成证据。
