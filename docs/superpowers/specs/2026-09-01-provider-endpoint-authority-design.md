# 主模型 Provider 地址权威与可观测性设计

**状态：** 已批准，待实施
**日期：** 2026-09-01
**基线：** `main@53e2af3ce3dc5e65f97a0feae6c37cef0b19a479`

## 1. 目标

建立主模型 Provider 地址的唯一权威，使以下入口始终使用同一个有效模型请求地址：

- Agent、Gateway 和 WebUI 实际对话；
- 连接检查与聊天验证指纹；
- 压缩、记忆、网页提取等复用主模型 Provider 的辅助调用；
- “设置 → 提供商”和“设置 → 模型配置”的地址展示；
- WebUI、CLI、模型切换、Fallback 和配置档写入。

同时在现有设置页面显示可用于排错的实际模型请求地址和地址来源。内置 Provider 地址对普通用户只读，只有现有通用 `Custom` 主模型地址可在页面编辑。

## 2. 背景与根因

已确认的故障形态是：配置身份为 `provider=zai-cn`、模型为 `glm-5`、凭据来自 `GLM_CN_API_KEY`，但同一 `model` 配置块残留 `https://api.deepseek.com/v1`。运行时优先采用了残留地址，因此智谱 Key 被发送到 DeepSeek 并收到 401。该 401 只能证明 DeepSeek 拒绝了这次请求，不能证明智谱 Key 在正确地址上无效。

现有代码已经包含正确的 `zai-cn` 国内地址，但地址决定权分散在多处：

- Provider 注册和认证注册分别保存地址或地址来源；
- `model.base_url` 可覆盖注册表默认值；
- WebUI 模型解析、连接检查和运行时各自解析地址；
- 凭据池、显式调用参数和辅助客户端存在旁路；
- 前端隐藏内置 Provider 的 Base URL 输入框，但仍读取或提交该字段；
- Provider 设置页当前只展示凭据和模型目录，不展示有效地址。

所以根因不是某一行 URL 写错，而是“原始配置值、Provider 默认值和运行时结果之间没有最终所有权仲裁”。

## 3. 第一性原则

一次模型请求的有效身份是一个完整元组：

```text
Provider ID + Model ID + Endpoint + Credential Family + Transport
```

修复必须满足以下原则：

1. **一个最终地址权威。** 保存值、默认值、环境变量、凭据池和显式参数都只是候选，只有共享仲裁结果可以进入请求。
2. **运行安全不依赖迁移。** 即使旧配置没有清理，固定 Provider 也不能访问错误地址。
3. **展示值来自运行规则。** 页面不得维护 Provider→URL 表，也不得展示未经仲裁的原始 `model.base_url`。
4. **地址所有权与 UI 编辑权分离。** 管理员可通过专用配置覆盖某些内置 Provider，不代表普通用户可在页面编辑。
5. **验证证据分层。** Key 已保存、连接检查成功、实际对话成功分别展示，不能互相替代。
6. **兼容优先。** 不因为修复 `zai-cn` 而破坏 Azure、Anthropic 代理、MiniMax 区域地址、远程 LM Studio、Kimi、OpenCode、Bedrock 或 Custom 的现有协议选择。

## 4. 范围

### 4.1 本次包含

- 主模型 Provider 地址策略和共享仲裁器；
- `zai-cn` 固定国内地址的最终保护；
- 实际运行、连接检查、辅助调用和设置页的地址一致性；
- 固定 Provider 旧地址的配置迁移和所有写入口归一化；
- Provider 页和主模型摘要的只读地址展示；
- 通用 `Custom` 地址的现有编辑入口；
- 地址变化后的验证状态失效；
- 聚焦回归、全量本地门禁和前端 UX QA。

### 4.2 本次不包含

- API Key 复用开关和安全模式开关的独立故障；
- 图片、语音、STT/TTS 等独立 Provider 子系统；
- 重写或删除 `auth.PROVIDER_REGISTRY`；
- 一次性重排所有可覆盖 Provider 的 URL 优先级；
- 新数据库、地址历史、测速、健康仪表盘或通知中心；
- 域名黑名单、DeepSeek 专用前端判断或自动猜测 Key 所属区域；
- `streaming.py`、`routes.py` 或整套 Provider 体系的大型重构；
- 新增命名式 `custom_providers` 的 CRUD；
- 制包、安装、麒麟终端恢复、目标机验收、Tag、Release 或发布。

## 5. 方案比较

### 方案 A：只在 `zai-cn` 分支覆盖 URL

改动最少，但凭据池、显式参数、辅助客户端和页面展示仍可能绕过，后续新增固定 Provider 时还会重复特殊判断。该方案不采用。

### 方案 B：重写 Provider、认证、模型和传输注册表

可以从根源去重，但会扩大到认证、OAuth、模型目录和动态协议，改动远超当前问题，回归风险高。该方案不采用。

### 方案 C：共享地址所有权仲裁器，保留现有候选采集

在现有 `hermes_cli/providers.py` 内增加轻量纯函数，把各分支已经算出的候选地址做最终裁决；所有运行和展示入口消费同一契约。该方案既能封住当前故障，又不重排其他 Provider 的合法行为，确定为实施方案。

## 6. 架构

```text
ProviderDef / Overlay
        +
现有配置、专用环境变量、凭据池、认证状态、区域推导
        │
        ▼
候选地址采集（保留各 Provider 现有行为）
        │
        ▼
共享 Endpoint 仲裁器（纯函数、无 I/O、无网络）
        │
        ├── 实际 Agent / Gateway / WebUI 请求
        ├── 连接检查与验证指纹
        ├── 辅助客户端
        ├── /api/model-config
        └── /api/providers
```

“设置 → 提供商”是统一展示面和操作入口，不是运行时解析器。地址权威必须位于 Agent Provider 层，避免前端、WebUI 后端和 Agent 各自维护地址规则。

## 7. 地址策略

`ProviderDef` 增加以下元数据：

```python
endpoint_policy: Literal["fixed", "configurable", "runtime_managed"]
requires_endpoint: bool = False
```

### 7.1 `fixed`

地址和 Provider 传输由代码内定义拥有。所有通用候选覆盖都被忽略。

- 首个显式条目只有 `zai-cn`；
- 固定地址为 `https://open.bigmodel.cn/api/paas/v4`；
- 传输为 OpenAI Chat 兼容模式；
- `model.base_url`、`providers.zai-cn.base_url`、通用环境覆盖、凭据池地址和显式调用参数都不能改变最终地址；
- 如果固定 Provider 没有内建地址，仲裁器必须 fail closed。

后续新增固定 Provider 时只增加元数据和测试，不增加调用方特殊分支。

### 7.2 `configurable`

Provider 允许合法地址覆盖。普通 API Key Provider、代理接入、本地服务和通用 Custom 属于此类。

- 保留当前 Provider 已有候选采集顺序；
- 本次不统一重排显式参数、专用环境变量、Provider 配置、`model.base_url` 和默认地址的优先级；
- Azure Foundry、Anthropic 代理、MiniMax、Z.AI 国际版、远程 LM Studio 等现有能力必须保持；
- `requires_endpoint=True` 只表示没有默认地址时必须提供地址，不增加第四种策略。

### 7.3 `runtime_managed`

地址由 OAuth、AWS SDK、外部进程或认证状态决定。

- 高层 `model.base_url` 不参与最终决策；
- 运行时已有可信地址时，仲裁器保留该地址和来源；
- 设置页不得为了展示地址触发 OAuth、刷新令牌或外部网络；
- 无法从本地状态安全解析时显示“运行时分配”，不能回退显示旧配置。

### 7.4 UI 编辑权

`editable` 不是 `endpoint_policy` 的同义词：

- 内置 `fixed`、`configurable` 和 `runtime_managed` 在普通页面都只读；
- 现有通用 `provider=custom` 为可编辑；
- 命名式 `custom:*` 和 `providers:` 用户条目继续由管理员配置管理，本轮只读展示，不新增第二套编辑状态机。

## 8. 内部解析契约

共享纯函数返回不可变结果：

```python
@dataclass(frozen=True)
class EndpointResolution:
    provider: str
    policy: str
    effective_url: str
    source: str
    editable: bool
    requires_endpoint: bool
    candidate_ignored: bool
```

解析器接收分开的 `configured_url`、`runtime_url`、`candidate_source` 和 `candidate_override_present`：`fixed` 只读静态 overlay；`configurable` 在有可信 runtime 结果时使用它，否则使用调用方按既有优先级选好的配置候选；`runtime_managed` 只接受 runtime 候选。`candidate_override_present` 只表示本次调用确实提交了一个会覆盖固定地址的配置、显式参数或凭据池候选；注册表自身的 canonical 默认地址不算覆盖。WebUI GET 不得把原始配置伪装成 runtime 候选。

约束如下：

- Provider ID 先经过现有 alias 规范化；
- URL 统一去除两端空白和尾部 `/`；
- 纯函数不读取配置、环境变量或密钥，不联网，也不探测 `/models`；
- `fixed` 只接受静态 overlay 的内建地址；
- `configurable` 保留调用方传入的首个可信候选；
- `runtime_managed` 只接受认证/区域/运行时产生的可信候选；
- `candidate_ignored=True` 只说明本次解析拒绝了一个覆盖候选，不代表磁盘当前仍有残留，也不返回该候选；
- Endpoint 仲裁器不决定模型，不读取 API Key，不承担 SSRF 策略，也不静态化动态 Provider 的 `api_mode`。

策略查找本身也是静态纯函数：规范化 ID 后只读取本模块的静态 overlay/策略元数据，不调用 `get_provider()`、`resolve_provider_full()` 或 models.dev。未识别 Provider、`providers:` 用户条目、`custom` 和 `custom:*` 默认 `configurable`；只有原始 ID 精确等于 `custom` 时 `editable=True`。空 Provider 返回稳定的未配置结果，不抛出空引用异常；`ollama` 等映射到 Custom 的 alias 不能因此获得页面编辑权。

内部 `source` 表示地址所有权而不是 Key 来源：固定代码为 `system`，普通注册/管理员配置为 `managed`，通用 Custom 为 `custom`，显式调用参数、已选择的凭据池地址、认证状态或可信运行时结果为 `runtime`。候选采集分支必须独立携带该来源，不能根据最终 URL 或 Key 来源反推。凭据结果已有的 `source` 继续表示 Key 来源，二者不复用字段。

现有 `resolve_provider_full()` 对内置 Provider 的优先级保持不变：同名 `providers:` 用户条目不能静默覆盖内置定义。管理员合法地址覆盖继续使用各 Provider 已有的专用配置来源，本轮不顺手改变该优先级。

传输协议在地址裁决后继续由现有运行时逻辑计算。`zai-cn` 作为固定 Provider，最终地址与静态 overlay transport 必须通过 `TRANSPORT_TO_API_MODE` 直接覆盖陈旧 `model.api_mode`，不能再经过 URL heuristic；MiniMax、Kimi、Azure、OpenCode、Bedrock 和 Custom 继续使用现有动态协议规则。

## 9. 运行时接入

### 9.1 公共运行时最终保护

把现有 `resolve_runtime_provider()` 函数体保留为单一私有候选解析器，新增同签名公共 wrapper；wrapper 只调用候选解析器一次，并在唯一出口执行 finalizer。每个候选分支显式携带内部 `_endpoint_candidate_source` 与 `_endpoint_candidate_override_present`，公共 wrapper 消费并移除它们；不能从候选最终 URL、API Key 来源或“URL 非空”反推。注册默认地址不标记 override，`model.base_url`、`providers.<id>.base_url`、显式参数和实际选中的 pool 地址才按其真实来源标记。这样不需要在各业务调用方增加 `zai-cn` 判断。必须覆盖：

- 普通 API Key 路径；
- 凭据池路径；
- 显式 `api_key/base_url` 路径；
- OAuth 和认证状态路径；
- Azure、Bedrock 和其他专用分支；
- Fallback Provider；
- 会话中模型切换。

这样 CLI、Gateway、Cron、Delegate、TUI 和 WebUI 共用保护，而不需要逐个业务入口增加 `zai-cn` 判断。

公共 runtime 结果使用 `endpoint_candidate_ignored` 表示本次请求拒绝了候选；它是瞬时诊断字段。页面公开的 `endpoint.override_ignored` 是另一件事，只表示当前持久化状态仍有残留，两者不得互相代替。

### 9.2 WebUI 模型解析

`api/config.py` 的 `_get_provider_base_url()` 和 `resolve_model_provider()` 不得把原始 `model.base_url` 直接当作最终地址。为避免破坏现有大量三值解包调用方，`resolve_model_provider()` 保持 `(model, provider, base_url)` 返回合同；另外增加只供公开 API 使用的 sibling endpoint-view helper，接收调用方已经加载的本地 config/auth/pool 状态，并显式返回 `runtime_selector_unresolved`：

- `fixed` 返回 Provider 固定地址；
- `runtime_managed` 返回本地已知运行时地址或空值，由公共 runtime 补齐；
- `configurable` 保持现有兼容优先级；若公开 view 可从已加载的本地状态确定凭据池/认证选择的实际地址，则把该地址和 `source=runtime` 交给仲裁器；若已知地址由运行时选择、但 GET 无法无副作用地确定下一次选择，则 sibling view 返回 `runtime_selector_unresolved=true`，不得改变原三元返回值，也不得把普通配置候选冒充实际地址；
- Custom 的模型 ID 和命名 Provider 解析保持不变。

`streaming.py` 继续通过 `resolve_model_provider()` 和公共 runtime 获取结果。除非 RED 测试证明仍有旁路，否则不直接重构其大型流式逻辑。

Z.AI 国际版只读页面/status 只在两种情况下把地址标为已解析：存在合法显式地址，或存在与当前 Key 哈希匹配的有效区域缓存。已有 Key 但没有匹配缓存时返回 `runtime_unresolved`，不显示注册默认地址，也不触发探测；尚无 Key 时可以显示注册默认地址，同时凭据状态明确为“未配置”，不能据此声称已完成实际路由。连接检查和实际 runtime 可以沿用现有完整区域探测并回填缓存，后续 GET 再显示缓存后的实际地址。

### 9.3 辅助客户端

`agent/auxiliary_client.py` 中直接构造 OpenAI/Anthropic 客户端的自动回退和显式 Fallback 两组路径必须调用同一 finalizer。只封堵复用主模型 Provider 的地址旁路，不改变图片、语音等独立能力的配置模型。

## 10. 配置写入与迁移

### 10.1 运行安全先于迁移

任何请求都必须先经过运行时 finalizer。即使配置迁移未运行、写盘失败或文件保持旧值，`zai-cn` 仍只能访问 BigModel 国内地址。

### 10.2 版本迁移

使用现有配置版本机制执行 `24 → 25` 幂等迁移：

- 读取原始配置，不把默认合并值误当用户值；
- 对已知的主模型、`fallback_providers`、legacy fallback 和辅助任务 fallback 配置逐项识别 Provider；
- 当条目 Provider 的策略为 `fixed` 时，删除该条目的 `base_url` 和不适用的陈旧 `api_mode`；
- 保留 Provider、模型、API Key/凭据引用、请求回执、`providers`、Custom、Fallback、会话和所有无关字段；
- 不按域名黑名单删除内容；
- 不删除 `providers.zai-cn` 等用户命名空间，只在运行时忽略不被允许的地址字段；
- 重复执行不继续改变文件；
- GET、页面加载和 `/api/providers` 不执行写迁移。

迁移只由现有 `hermes config migrate`、`hermes doctor --fix` 和 CLI 升级流程触发，不新增 WebUI 启动写盘。运行时 finalizer 是立即安全的主防线，迁移只负责在既有升级/修复流程中清理磁盘残留。

### 10.3 写入口归一化

同一个无 I/O 配置归一化函数接在两个 canonical durable primitives：通用 `save_config()` 与严格 config/config+env 事务写入器。写入器保留变更前的完整 raw config 快照，对主模型、Fallback 和辅助任务等已知 endpoint-owning 路径逐项配对，先执行 Provider 切换归一化，再执行 fixed 字段归一化，最后才做请求回执/credential revision 对比和写盘。业务入口保留回归测试，但只有真正绕过这两个 primitive 的路径单独改源码：CLI round-trip 全局模型切换、旧 Web 主模型直写以及 CLI/WebUI 配置档直接复制。

Provider 切换归一化采用 before/after 语义：同一已知逻辑路径上的 Provider 身份变化且 `base_url/api_mode` 仍与旧快照相同，视为旧 Provider 遗留并删除；新 configurable Provider 在同一事务中显式提交了不同的新地址/协议时保留；目标为 fixed 时无条件删除这些字段。列表按既有稳定位置/标识配对，不做无界递归。这样 `config set model.provider`、主模型/Fallback/辅助模型切换和旧客户端 omitted payload 都不会把上一个 Provider 的地址继承给下一个 Provider。

当前首次引导不支持 `zai-cn`，本轮不为它增加新的 Provider 能力；若未来引导开放固定 Provider，必须在其既有严格事务内调用同一归一化函数。

固定 Provider 收到旧客户端提交的非空 `base_url` 时：

1. 保留本次 Provider、模型和凭据保存；
2. 丢弃地址覆盖，不把它写盘；
3. POST 返回一次性的 `endpoint_mutation.code=fixed_override_cleaned` 和安全提示；
4. 不回显被忽略的旧地址。

此行为避免旧前端因为 HTTP 400 无法切换 Provider，同时保证地址不生效。

`endpoint.override_ignored` 只描述当前主模型 raw `model.base_url` 中仍存在、且本次 POST/迁移拥有清理权的 fixed Provider 残留，不能根据默认合并结果、最终 URL、`providers.<id>`、凭据池或瞬时显式参数推导。后面三类被拒绝时只进入 runtime 的瞬时 `endpoint_candidate_ignored`。成功清理后的权威 `main.endpoint.override_ignored=false`；写盘失败时仍为 `true`。一次性保存事件不能塞进持久状态字段。

## 11. WebUI API 契约

`/api/providers` 的每个 Provider 行和 `/api/model-config` 的 `main` 使用同一公开结构：

```json
{
  "endpoint": {
    "display_url": "https://open.bigmodel.cn/api/paas/v4",
    "policy": "fixed",
    "source": "system",
    "editable": false,
    "status": "resolved",
    "override_ignored": true
  }
}
```

规则：

- `display_url` 来自内部 `effective_url` 的安全投影；
- 内置固定地址可完整显示；
- Custom 或管理员 URL 对外投影时移除 userinfo、查询参数和 fragment，保留用于定位的 scheme、host、port 和 path；
- `source` 对外只使用 `system`、`managed`、`custom`、`runtime`；
- 无法本地解析的运行时地址使用 `display_url=null`、`status=runtime_managed`；
- configurable Provider 已知由运行时选择地址、但 GET 无法无副作用确定下一次选择时，使用 `display_url=null`、`status=runtime_unresolved`、`source=runtime`；该状态由 sibling view 的显式布尔字段传入，不从空 URL 猜测；
- 缺少必填地址的 configurable/Custom 使用 `display_url=null`、`status=missing`，不能误报为运行时分配；
- legacy Custom 地址若包含 userinfo、query、fragment 或换行，`display_url` 仍使用安全投影，`status=invalid_saved_value`，编辑兼容字段返回空并提示重新录入；
- 不返回 API Key、Key 环境变量名、请求头、配置路径或凭据池 ID；当前 Provider 的 endpoint/status/error 不回显其被忽略候选，但 `/api/providers` 中合法 DeepSeek Provider 自己的官方地址仍正常显示；
- 当当前 Provider 在 `/api/providers` 有对应行时，它与 `/api/model-config.main` 的 endpoint 逐字段一致；通用 `custom` 没有凭据卡，不强造一行。
- 兼容字段 `main.base_url` 只对通过可公开编辑校验的通用 `provider=custom` 保留值；内置、运行时托管、命名式 Custom 和不安全 legacy Custom 返回空字符串，新前端只读取 `main.endpoint`。

公开投影集中放在一个轻量 WebUI API leaf helper 中，由 `model_config.py` 和 `providers.py` 共用；它只依赖 Agent domain 和标准库，不反向导入 `api.config`、`api.providers` 或 `api.model_config`。候选由调用方传入，Agent 领域层不依赖 UI 字段名，也不让两个 API 各复制一份脱敏规则。

## 12. 页面设计

### 12.1 “设置 → 提供商”

复用现有 Provider 卡片。展开后按以下顺序展示：

1. 实际模型请求地址；
2. 地址来源；
3. API Key/认证状态和现有管理操作；
4. 模型目录和刷新操作。

内置 Provider 使用有标签的只读 `<output>`，不使用禁用输入框，也不出现地址编辑按钮。运行时托管且地址未知时显示“运行时分配”，configurable Provider 的下一次地址尚不能本地确定时显示“请求时确定”，缺少必填地址时显示“尚未配置”，不能显示空白或旧地址。卡片标题与展开体必须维护稳定的 `aria-expanded/aria-controls`，OAuth 与命名 Custom 共用安全 toggle，只有输入框存在时才聚焦。

通用 Custom 不强行加入现有凭据卡列表，继续在“模型配置”的原有编辑器维护地址。Provider 页刷新不得覆盖模型配置页已有的未保存草稿。

### 12.2 “设置 → 模型配置”

主模型摘要持续显示：

- Provider 名称和 ID；
- 模型 ID；
- API Key 状态；
- 实际请求地址；
- 地址来源；
- 连接检查和最近对话验证状态。

编辑区选择内置 Provider 时，地址区域显示只读“保存后将使用的地址”；选择通用 Custom 时，同一位置切换为现有可编辑输入框。切换前预览来自 `/api/providers`；只有当前已保存的 `main.provider` 与所选项都精确等于 `custom` 时，才可回退使用 `main.endpoint`。从内置 Provider 新切到 Custom 时只显示草稿或“尚未配置”，不能复用当前内置地址。保存后摘要只使用 POST 返回的权威 `main.endpoint`。命名 `custom:*` 的地址和凭据均为管理员只读，只允许选择 Provider/模型，不显示可写 Key 或地址动作。

前端只在通用 Custom 时把 `base_url` 放入保存 payload 和脏状态比较；内置 Provider 不提交地址。所有保存继续携带现有 32 位 request ID，空 Key 不进入 payload。

后端必须区分 omitted 与 empty：同一 configurable Provider 且请求未包含 `base_url` 时保留现有合法覆盖；切换 Provider 时清除上一个 Provider 的地址；fixed 无条件清理；通用 Custom 要求非空；兼容旧客户端显式提交 configurable 地址的现有合同继续保留。

### 12.3 状态与反馈

必须分开显示：

- 凭据未配置；
- 凭据已保存，尚未验证；
- 连接已验证，尚未完成实际对话；
- 最近实际对话成功；
- 请求失败及安全错误说明。

GET 发现 fixed Provider 仍有磁盘残留时，页面显示：

> 检测到旧版地址覆盖，已停止使用。当前实际访问地址为系统地址；保存后将清理旧值。

POST 成功清理后改为一次性“旧版地址已清理”消息，后续 GET 不再显示；写盘失败继续显示当前残留状态。两种状态都不得回显旧地址。

连接失败提示可以显示本次安全投影后的目标地址或 host，但不得显示凭据。地址变化必须让旧验证指纹失效，状态回到“已配置，尚未验证”。

## 13. 错误与安全

- 固定 Provider 缺少内建地址：fail closed，返回稳定错误码；
- Custom 地址缺失或结构非法：阻止保存，字段内联报错、关联 `aria-describedby`、聚焦字段并保留草稿；
- Custom 继续允许本机 HTTP；新增共享结构校验拒绝换行、缺少 hostname、userinfo、query 和 fragment；
- 公开页面和日志不得输出完整 Key、Authorization、配置路径或包含敏感查询参数的 URL；
- endpoint 投影不得在 Provider 卡既有行为之外新增网络、OAuth、目录刷新或连接检查；
- 保存结果不确定时沿用现有 request ID 和权威重读，不自动重放写请求；
- Runtime refresh 失败时不得显示“已应用”，但固定地址 finalizer 仍继续保护后续新建请求。

## 14. 前端功能契约

| 能力 | 数据/API/状态存在 | UI 入口存在 | 用户反馈存在 | 错误处理存在 | 空/加载/禁用状态 | 键盘/可访问性支持 | E2E/浏览器测试 | 状态 | 备注 |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 查看内置实际地址 | 计划增加 | Provider 卡与主模型摘要 | 地址来源与状态 | 地址不可用提示 | 运行时分配/加载失败 | 有标签的 `<output>` | 计划覆盖 | 未验证 | 待实施 |
| 编辑通用 Custom 地址 | 现有保存 API | 模型配置原有编辑器 | 保存/失败/重读状态 | 字段内联错误 | 未配置/保存中/失败 | 标签、错误关联、焦点、键盘保存 | 计划覆盖 | 未验证 | 不新增 Provider 卡或第二套表单 |
| 查看旧覆盖处理状态 | 计划增加 | 两个页面地址旁 | 忽略与清理提示 | 写盘失败仍保持保护 | 提示持续到清理成功 | `role=status` | 计划覆盖 | 未验证 | 不显示旧地址 |
| 检查连接 | 已存在 | 现有检查按钮 | 分层验证状态 | 结构化产品错误 | 检查中/成功/失败 | 保持现有键盘路径 | 聚焦回归 | 未验证 | 目标地址改为共享解析结果 |

## 15. 测试与验收

### 15.1 核心故障回归

给定：

```yaml
model:
  provider: zai-cn
  default: glm-5
  base_url: https://api.deepseek.com/v1
```

以下结果必须全部是 `https://open.bigmodel.cn/api/paas/v4`：

- 共享纯仲裁器；
- 普通 runtime；
- 凭据池、显式参数和 Fallback runtime；
- WebUI `resolve_model_provider()`；
- 连接检查材料；
- 实际 WebUI 对话构造参数；
- 辅助客户端自动回退和显式构造；
- `/api/model-config.main.endpoint`；
- `/api/providers` 中当前 `zai-cn` 的 endpoint。

### 15.2 兼容矩阵

- `zai` 的国际、国内或凭据推导路径保持；
- DeepSeek 专用环境地址保持；
- MiniMax / MiniMax-CN 的 `/anthropic`、`/v1` 和区域地址保持；
- Anthropic 官方、代理和 Azure Anthropic 保持；
- Azure Foundry 地址必填和两种协议保持；
- LM Studio 本机默认、远程/LAN 和无 Key 模式保持；
- Kimi、OpenCode、Bedrock 的动态协议保持；
- 裸 Custom、命名 Custom、Ollama/local/vLLM/llama.cpp 的地址与模型 ID 保持。

### 15.3 迁移与数据保护

- `24 → 25` 迁移先 RED 后 GREEN；
- 主模型、Fallback 和辅助任务配置中的固定 Provider 污染字段均被精确清理；
- 连续执行两次只在第一次改变固定 Provider 污染字段；
- GET 不改变配置内容或 mtime；
- API Key、凭据引用、请求回执、安全模式、Key 复用、会话、验证记录和其他配置在解析后语义相等；第二次迁移 bytes 与 mtime 不变；
- 写盘失败时运行时仍使用固定地址。

### 15.4 浏览器与可访问性

使用隔离 runtime/config 和本地 Mock，不使用真实 Key、OAuth 或 Provider，不打开用户默认浏览器。至少验证：

1. `zai-cn + DeepSeek 残留` 在两个页面显示同一 BigModel 地址且不可编辑；
2. 通用 Custom 在模型配置原编辑器保存并显示规范化地址，不新增 Provider 凭据卡；
3. 非法 Custom URL 保留草稿、字段报错、磁盘不变；
4. 保存失败、结果不确定和 runtime refresh pending 不产生虚假成功；
5. Provider 卡可用 Enter/Space 展开，`aria-expanded/aria-controls` 正确；
6. `1280×900`、`768×900` 和 `390×844` 下长地址不横向溢出；
7. 输出中文《前端 UX QA 报告》，未执行的自动化可访问性或视觉回归明确标记“未验证”。

### 15.5 完成门禁

- 聚焦 Agent、WebUI、迁移和前端测试通过；
- `scripts/verify.sh --full` 通过；
- 最终 staged bytes 通过 Sol 审核；
- 正常提交并推送 `main`；
- 未经另行授权不创建 Tag、Release、制品，不安装或连接麒麟终端。

## 16. 风险与控制

| 风险 | 控制 |
|---|---|
| 把可覆盖 Provider 误标为固定 | 首轮只显式固定 `zai-cn`；其余保持现有策略并做兼容矩阵 |
| 地址固定但协议仍被旧 `api_mode` 污染 | `zai-cn` finalizer 使用静态 overlay transport 映射，并覆盖陈旧模式 |
| 只修主聊天，辅助任务仍串线 | 覆盖公共 runtime 和 `auxiliary_client` 两组直连路径 |
| 迁移误删用户配置 | 只在已知主模型/Fallback/辅助条目中删除 fixed Provider 的 `base_url/api_mode`，使用原始配置和幂等测试 |
| 设置页泄露带 Token 的 URL | 公开 API 使用安全投影，移除 userinfo、query 和 fragment |
| 缓存继续复用旧地址 | 地址进入验证/运行缓存身份；改变后失效并创建新 runtime |
| 大文件改动扩大风险 | 优先在公共入口 finalizer 收口，除 RED 证据外不重构流式文件 |

## 17. 参考

- `docs/superpowers/specs/2026-08-27-zai-cn-main-model-provider-design.md`
- `docs/superpowers/specs/2026-08-27-main-model-provider-switch-consistency-design.md`
- `docs/runbooks/development-lifecycle.md`
- `.agents/skills/frontend-ux-qa/SKILL.md`
