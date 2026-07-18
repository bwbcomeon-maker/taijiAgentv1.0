# Main Consolidation Task B 来源安全审计

**日期：** 2026-07-18

**当前结论：** `blocked_internal`

**审计对象：** `d1b65c51`、`c581bd3f` 与
`codex/main-consolidation-20260717@70e65085` 当前实现

**允许动作：** 只按 B1–B4 做 TDD hand-port

**禁止动作：** whole-commit cherry-pick 或旧文件整体覆盖

## 1. 执行摘要

两个来源提交包含有价值的 Provider 扩展和即时路由意图，但不能直接进入当前
整合线。根因不是普通文本冲突，而是它们跨越了凭据、网络、验证、长生命周期
Agent、WebUI streaming 和 Artifact 多个信任边界；当前基线在下载、Artifact
授权和配置事务方面已经更强，整段移植会产生安全倒退。

Task B 必须拆为四个独立提交：

| 子任务 | 只解决的问题 | 当前状态 |
|---|---|---|
| B1 | credential aliases/family/model fail-closed | 待先写 RED |
| B2 | custom image+vision credential/network transport | 待先写 RED |
| B3 | versioned verification、execution gate、schema refresh | 待先写 RED |
| B4 | WebUI streaming/routing/cache 与 CLI/Gateway/TUI 一致 | 待先写 RED |

`configured` 只表示字段和凭据存在；只有当前版本、当前 fingerprint 的真实验证
成功才可投影为 `available`。工具 schema 可见也不是最终执行授权。

## 2. 审计方法与证据面

本轮只读检查了：

- 当前计划第 2.2 节和旧 Task 3。
- 两个来源 commit 的完整文件清单、函数级 diff 和关联测试。
- 当前
  `agent/provider_credentials.py`、
  `agent/custom_image_providers.py`、
  `agent/custom_vision_providers.py`、
  `agent/auxiliary_client.py`、
  `agent/image_gen_provider.py`、
  `agent/image_gen_verification.py`、
  `agent/image_routing.py`、
  `tools/url_safety.py`、
  `tools/image_generation_tool.py`。
- 当前
  `api/model_config.py`、
  `api/streaming.py`、
  `model_tools.py`、
  `agent/agent_init.py`、
  `gateway/run.py`、
  `tui_gateway/server.py`、
  `cli.py`。

本报告是 source audit，不是渗透测试、真实 Provider 验证或发布批准。

## 3. P1 findings

### P1-1：自定义生图可把任意环境变量当 API Key 外送

**证据：**
`agent/custom_image_providers.py::normalize_custom_image_provider_entry`
接受配置提供的任意 `api_key_env`；
`ConfigurableOpenAIImageProvider.generate` 随后读取该环境变量，并将值作为
Authorization 发送到配置的 HTTPS endpoint。

**根因：** endpoint 身份与 Secret 身份没有绑定到 canonical credential
record，用户可控 env 名称跨越了进程 Secret 边界。

**影响：** 能修改该配置的人可选择与图片能力无关的进程 Secret，并把它发送
到自己控制的 endpoint。这是本机 Secret exfiltration。

**hand-port 边界：** B1 建立 canonical family/credential；B2 让 custom image
和 vision 只接受 canonical credential env。不得保留 caller-controlled
`api_key_env` 兼容分支。

### P1-2：custom vision DNS 预检与实际连接存在 TOCTOU

**证据：**
`agent/custom_vision_providers.py::is_custom_vision_base_url_safe` 使用
`tools.url_safety.is_safe_url` 做 DNS 预检；之后
`agent/auxiliary_client.py::_resolve_task_provider_model` 把原 hostname/base URL
交给 SDK，SDK 在连接时重新解析。

**根因：** 安全决定绑定的是“预检时地址”，网络连接使用的是“稍后再次解析的
地址”，两者不是同一个原子动作。

**影响：** DNS rebinding 可让预检看到公开地址、实际连接落到私网或受保护
地址；同步和异步 client 都受影响。

**hand-port 边界：** B2 必须在实际 socket connect 时绑定经全量校验的地址，
校验 connected peer equality，同时保留原 hostname 的 SNI/Host；不能只增强
`is_safe_url`。

### P1-3：网络范围没有显式信任模型，存在 private/proxy 降级和 Fake-IP 例外

**证据：**

- `tools/url_safety.py` 使用全局 allow-private 开关，并在模块说明中承认 DNS
  预检不能消除 TOCTOU。
- `agent/image_gen_provider.py` 当前 direct downloader 已有单次解析、peer pin、
  SNI/Host、redirect 重验、大小/类型和原子写入；这部分是必须保留的更强底座。
- `plugins/image_gen/dashscope/__init__.py::_dashscope_address_allowed` 对
  `198.18.0.0/15` 有 Provider 特例。

**根因：** “公开直连、显式私网直连、受信代理”被混在全局布尔开关和
Provider 特例中，安全边界不可组合、不可审计。

**影响：** ambient proxy、全局 private toggle 或 provider-specific exception
可能改变请求目的地；Fake-IP/benchmark 地址可能被误当真实公网地址。

**hand-port 边界：** B2 引入互斥的 `public_direct`、`private_direct`、
`trusted_proxy`。为保留本地自定义 endpoint，只有显式 `private_direct` 可把
RFC1918/loopback/ULA 当目标；`public_direct` 和 `trusted_proxy` 的目标不得
降级到这些地址。metadata、link-local、multicast、unspecified、其它
reserved/benchmark 等永久禁区在所有 scope 下禁止；`198.18.0.0/15` 地址输入
或 DNS answer 必须明确失败，direct 模式返回
`fake_ip_requires_trusted_proxy`，不得作为 connected peer 放行；缺少显式受信
proxy 返回 `trusted_proxy_unavailable`。不得读取 ambient proxy。

### P1-4：长生命周期 Agent 可持有 stale `image_generate`

**证据：**
`agent/agent_init.py` 初始化时一次性执行 `get_tool_definitions` 并把结果放入
`agent.tools`；memory provider/context engine 之后还会注入非 registry schema。
配置或验证变化后，`model_tools` 的缓存和 registry check_fn 即使更新，已存在
Agent 仍可能保留旧工具或缺少新工具。

**根因：** 动态能力状态、tool schema cache 和 Agent 实例生命周期没有共享
版本化 identity；schema refresh 若直接覆盖 `agent.tools` 又会丢失后注入工具。

**影响：** 已撤销/失效的生图能力仍可被模型调用，或新验证成功的能力必须重启
才出现；CLI、Gateway、TUI、WebUI 的表现可能不同。

**hand-port 边界：** B3 增加 next-turn 原子 refresh 和 call-time 最终门禁，
只替换上一版 registry 工具并保留 non-registry schema；B4 把相同快照接入四个
入口和 agent cache signature。

### P1-5：vision fingerprint 使用未展开的 `${ENV}` 配置

**证据：**
`api/model_config.py::_vision_config_fingerprint` 从 raw YAML 读取 base URL 和
custom identity；`api.config._load_yaml_config_file` 不做 runtime env 展开。
Gateway runtime 则通过 env 展开器读取 effective config。

**根因：** 验证 fingerprint 和实际运行使用不同配置解释器。

**影响：** 环境变量改变实际 endpoint 后，旧 `verified` 可能仍匹配 raw
placeholder；反向也可能造成不一致失效。该问题会放大 custom endpoint 的信任
边界风险。

**hand-port 边界：** B3 的 shared fingerprint 必须使用与 runtime 相同的 env
展开规则；未解析 token fail-closed，并让 vision/image verification 与 cache
使用同一 schema version。

## 4. P2 findings

### P2-1：fingerprint、状态文件和 tool cache 没有 schema version

`agent/image_gen_verification.py` 的状态与 fingerprint 没有版本字段；
`api/model_config.py` 的 vision 状态同样只比对 fingerprint/status；
`model_tools._tool_defs_cache` 只包含 config stat、registry generation 等运行
输入，没有验证 schema version。

**影响：** 算法升级后，旧状态可能被当作当前 `verified`，旧 cache 也不能可靠
失效。

**边界：** B3 统一当前版本常量；缺失、旧版、未知新版全部降为
`configured_unverified`。

### P2-2：未知 model 存在 silent default fallback

`ConfigurableOpenAIImageProvider._model` 在 requested model 不在允许列表时会
返回 default；部分内置 Provider 和 WebUI 保存路径也没有使用同一 allowlist
校验。

**影响：** UI/路由宣告的模型与实际计费、权限和输出模型不一致，测试结果不能
证明用户选择的模型可用。

**边界：** B1 在保存、运行和插件适配器三层使用同一 fail-closed
model contract；只有显式 `allow_custom_model_id` 的 custom provider 可接受
额外 model id。

### P2-3：Provider API JSON 的 MIME 和读取大小无界

`ConfigurableOpenAIImageProvider.generate` 在解析 Provider 响应时直接调用
JSON 解析，没有在解析前强制 JSON MIME、`Content-Length` 和流式 hard limit。
后续图片 bytes 限制不能保护前面的 JSON body。

**影响：** 非 JSON/超大响应可造成内存压力、解析歧义或错误内容进入日志路径。

**边界：** B2 只接受 `application/json` 或 `application/*+json`，先检查
长度，再用 1–2 MiB hard limit 流式读取，之后才解析并投影固定脱敏错误。

## 5. 必须保留的当前更强语义

以下能力来自当前整合底座，不属于来源提交可覆盖范围：

1. `agent/image_gen_provider.py::save_url_image` 的单次 DNS、all-answer 校验、
   connected peer equality、原 hostname SNI/Host、redirect 每跳重验、无 ambient
   proxy、字节/MIME/magic/dimensions 限制和 atomic write。
2. `validated_cache_image_ref` 的 absolute-cache-only、`O_NOFOLLOW`、regular
   file、inode/hash 校验。
3. WebUI `ArtifactRegistry`、`message.artifacts`、session/turn/owner 授权、
   pending/commit/discard 和 save rollback。
4. `api/streaming.py` 的 turn envelope、journal、取消和 exact-once 语义。
5. `api/model_config.py` 当前 profile/config/credential transaction 与失败回滚。

旧 URL/base64/absolute-path artifact、第二套 image artifact API、旧 requests/
urllib3 downloader 都不得恢复。

## 6. B1–B4 hand-port boundary

| 子任务 | 允许修改 | 明确不做 |
|---|---|---|
| B1 | aliases、family、credential resolution、model allowlist、unknown capability | 网络 transport、verification version、streaming、Artifact、UI |
| B2 | canonical custom credential、三类 network scope、pinned sync/async transport、bounded JSON | tool lifecycle、Agent cache、Artifact 协议、全局 private/proxy 降级 |
| B3 | verification schema/version、effective fingerprint、cache identity、next-turn refresh、call-time gate | WebUI 路由文案、四入口编排、Artifact/下载重写 |
| B4 | WebUI/CLI/Gateway/TUI 相同 snapshot/reason code、agent signature、transaction 后 invalidation | 新 UI 信息架构、第二套状态、旧 artifact、Provider transport |

每个子任务只在其前置子任务的已审 commit 上继续；不得把 B2 的安全 transport
与 B4 streaming 大文件揉成一个 diff。

## 7. 27 类 must-first RED 测试

以下编号是 Task B 的验收索引。每类测试必须先证明当前实现因目标断言失败，再
做 GREEN；RED 不能来自测试语法、fixture、import 或环境错误。

### B1：凭据、family、model（1–6）

1. **Canonical alias：** 同一 Provider 的 vision/image/legacy alias 必须解析到
   唯一 family，未知 alias 不得借用相邻 family。
2. **新增国内 Provider 显式引用：** 新接入 named credential 的国内 Provider
   遇到 `credential_ref` 不存在或 Secret 缺失时，不读取 default/legacy env；
   已有核心 resolver 强语义作为 characterization guard。
3. **新增 Provider family mismatch：** 国内与 custom Provider 引用其它 family
   时，在保存、验证和运行三层都 fail-closed，且不泄露 Secret。
4. **custom alias/default 碰撞：** `custom:*` 归一化 alias、同 family 多个
   default、归一化 ID 冲突必须返回确定错误。
5. **未知生图模型：** 内置和未 opt-in 的 custom model 不得 silent fallback；
   Provider 网络调用次数必须为零。
6. **未知视觉能力/模型：** 未知 main model capability 不得猜 native 或偷偷走
   aux；未知 aux model 在保存/运行前停止。

### B2：凭据与 outbound transport（7–18）

7. **custom image env selector：** caller-controlled `api_key_env` 被拒绝，只能
   使用 canonical credential env。
8. **custom vision canonical env：** 已有 env tamper 拒绝作为 characterization
   guard；行为性 RED 必须覆盖当前尚缺的 named `credential_ref` 绑定与
   sync/async pinned transport，篡改 family/secret env 时请求仍为零。
9. **URL shape：** 非 HTTPS、userinfo、非法端口、fragment、混淆 hostname、
   IP literal 编码和不允许的 path/query 组合 fail-closed。
10. **`public_direct`：** DNS 所有 answer 都必须公开且非永久禁区，只解析一次；
    connected peer 必须等于 pinned address，SNI/Host 保持原 hostname。
11. **`private_direct`：** 仅显式选择时允许 RFC1918/loopback/ULA 目标，以
    保留本地自定义 endpoint；缺省或从 public scope 降级均失败，永久禁区仍
    失败。
12. **`trusted_proxy`：** 只使用显式受信 proxy；ambient proxy、缺失配置、
    不安全 proxy peer 和 scope 自动回退均失败并给稳定 reason code。
13. **永久网络禁区：** metadata、link-local、unspecified、multicast、其它
    reserved/benchmark 及其 IPv4-mapped/IPv6 变体在三个 scope 下保持拒绝；
    loopback 仅按第 11 类的显式 `private_direct` 规则处理。
14. **Fake-IP：** `198.18.0.0/15` literal、DNS answer、proxy address 和
    Provider 特例均明确失败；direct 模式可提示改用显式 trusted proxy，但不得
    连接该 Fake-IP。
15. **custom vision rebinding：** 预检公开、连接私网/永久禁区的 DNS 切换在
    sync 与 async client 都在发送凭据前失败。
16. **custom image API POST：** DNS/connect/TLS 固定；API redirect 不跟随，
    不把 Authorization 转发到另一 origin，错误日志脱敏。
17. **图片结果下载：** 当前 downloader 强语义先作为 characterization guard；
    行为性 RED 必须断言它尚未使用 shared safe transport/显式 scope 传播。GREEN
    后每个 redirect hop 重新执行 scope/peer 校验，并保留 MIME/magic/
    dimensions/size、inode/symlink 和 atomic write。
18. **bounded JSON：** 只接受 JSON MIME；缺失/伪造长度、chunked 超限和
    1–2 MiB 边界在解析前停止，Secret/原始 body 不进入公共错误。

### B3：版本、cache、长生命周期 Agent（19–23）

19. **verification schema version：** 缺失、旧版、未知新版状态均不能继承
    `verified`；当前版本正常匹配。
20. **effective `${ENV}` fingerprint：** env 展开后的 endpoint/transport/
    Secret digest 任一变化立即失效；未解析 token fail-closed。
21. **cache version identity：** verification version/fingerprint 变化立即
    清理 check_fn/tool defs cache，不等待 TTL 或 config mtime 偶然变化。
22. **long-lived schema refresh：** 同一 Agent 在验证成功后获得、撤销/失效后
    移除 `image_generate`，不丢消息/session/prompt 状态。
23. **call-time gate 与工具保留：** stale schema 调用在 Provider 前失败；
    refresh 失败保持旧原子状态，成功时保留 memory/context/MCP 等 non-registry
    schema 并去重 `valid_tool_names`。

### B4：四入口与 streaming/cache（24–27）

24. **四入口一致快照：** CLI、Gateway、TUI、WebUI 对相同配置返回同一
    capability version/fingerprint/status/reason code，且实际 Provider 调用次数
    一致。
25. **agent cache identity：** WebUI session cache 与 Gateway cache signature
    包含能力快照；变化后只在安全 session boundary 重建/刷新，不复用 stale
    Agent。
26. **streaming 实际路由：** native、verified aux、unknown/blocked 和生图
    工具事件与实际执行分支一致；普通文本历史不因未知图片能力失败，含 native
    image 的历史才触发清洗。
27. **配置事务传播：** RED 参数化覆盖 credential upsert/delete、vision/image
    test/set、Alibaba 组合保存、custom vision/image 增删、main-model set；
    所有成功事务都经唯一 post-commit hook 让四入口下一轮一致失效，失败事务
    不发布半状态；turn journal、Artifact pending/commit/discard、session
    authorization 和 non-registry tools 均保持。

## 8. B4 前端 feature contract

| 项 | 契约 |
|---|---|
| 用户意图 | 保存并验证图片能力后，下一条消息立即使用当前真实能力；失败时得到可操作且不泄密的原因 |
| 触发入口 | WebUI 图片附件/生图请求、CLI 图片输入、Gateway 附件、TUI 附件 |
| 前端路径 | 读取服务端公开状态和 reason code；不得自行从“字段已填”推断可用 |
| 后端路径 | versioned snapshot → route decision → next-turn schema refresh → call-time gate → Provider |
| 状态迁移 | unconfigured → configured_unverified → verifying → verified/failed；配置/版本变化回到 configured_unverified |
| 成功反馈 | 当前 route 与实际执行一致；不展示 Secret、Secret digest 或内部 endpoint |
| 失败反馈 | 稳定 reason code、持久可见状态、明确下一步；未知能力和未验证能力在调用前停止 |
| 边界条件 | cache 命中、长生命周期 Agent、并发保存/验证、取消、历史 native image、事务回滚 |
| 必需证据 | API/entrypoint tests、真实浏览器主路径、键盘/焦点、截图/视觉回归状态、无 P0/P1 |

本轮只修订计划和审计文档，没有修改页面或可见交互，因此不能把浏览器、
截图、可访问性自动化或视觉回归写成“通过”；它们是 B4 实施后的必做门禁。

## 9. 永久禁令

1. 不恢复旧 universal-image 计划作为实现真值。
2. 不 whole-commit cherry-pick `d1b65c51` 或 `c581bd3f`。
3. 不恢复旧 downloader 或第二套 artifact。
4. 不允许 ambient proxy/private 全局开关把 scope 静默降级。
5. 不覆盖当前 Artifact/session authorization/inode/symlink/atomic write。
6. 不恢复 URL/base64/absolute-path 旧 artifact。
7. 不接受 unversioned verification、fingerprint 或 cache identity。

## 10. 解锁条件

Provider L1 保持 `blocked_internal`，直到：

- 27 类测试均有真实 RED 原因和 GREEN 通过证据；
- B1–B4 各自形成可回滚 commit；
- 每个子任务的规格复审和独立质量复审均关闭 P0/P1；
- Task B 全量目标测试通过；
- 敏感信息扫描无 Secret/私有 hostname；
- `git status --short` 为空。
