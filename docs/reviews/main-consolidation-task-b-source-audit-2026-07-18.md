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
| B1 | Provider aliases/family/model fail-closed，不读取 Secret | 待先写 RED |
| B2 | custom image+vision 完整 credential binding 与 network transport | 待先写 RED |
| B3 | WebUI/Agent versioned verification、execution gate、schema refresh | 待先写 RED |
| B4 | mutation hook、`capability_route` producer 与四入口一致性 | 待先写 RED |

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
  `agent/agent_runtime_helpers.py`、
  `agent/tool_executor.py`、
  `tools/url_safety.py`、
  `tools/image_generation_tool.py`、
  `tools/vision_tools.py`。
- 当前
  `api/model_config.py`、
  `api/streaming.py`、
  `model_tools.py`、
  `run_agent.py`、
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

**hand-port 边界：** B1 只建立 canonical aliases/family 和模型裁决，不做
Secret 解析；完整 credential binding 归 B2：必须覆盖
`provider_credentials.resolve_api_key`、domestic `provider_api_key`、
custom image `_entry_api_key`/public row/`is_available`/setup schema/`generate`、
custom vision credential-ref/public row，以及 WebUI custom image/vision
get/set/delete 与 `test_model_config_api.py`。不得保留 caller-controlled
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
RFC1918/loopback/ULA 当目标；`public_direct` 不得降级到这些地址。
metadata、link-local、multicast、unspecified、其它 reserved/benchmark 和
`198.18.0.0/15` 在 direct 模式由应用的 DNS/peer pin 明确拒绝。

`trusted_proxy` 必须被定义为外部受控安全策略边界：Provider 配置只能引用
控制面/运维预先批准的 named proxy profile，不能提交任意 URL，也不能读取
ambient proxy。profile 必须声明已验证的 `public_egress` 与
`dns_ip_classification` policy capability；缺失或未批准返回
`trusted_proxy_unavailable`，请求数为 0。应用校验 proxy 自身地址、origin
URL/literal/metadata hostname、CONNECT 状态、隧道内 origin TLS
certificate/hostname/SNI，但标准 CONNECT 不暴露 proxy 实际解析/连接的
origin IP，因此应用不能声称独立验证 remote resolved peer。

named proxy 的策略必须在远端 DNS 解析到 RFC1918、metadata、link-local、
其它永久禁区或 Fake-IP 时拒绝并给出结构化 policy denial；应用把它稳定映射为
`trusted_proxy_origin_blocked`，且不得 fallback direct/private。未来若 proxy
提供 resolved-peer attestation 可增强证明，但不是当前标准 CONNECT 契约。

### P1-4：长生命周期 Agent 可持有 stale `image_generate`

**证据：**
`agent/agent_init.py` 初始化时一次性执行 `get_tool_definitions` 并把结果放入
`agent.tools`；memory provider/context engine 之后还会注入非 registry schema。
配置或验证变化后，`model_tools` 的缓存和 registry check_fn 即使更新，已存在
Agent 仍可能保留旧工具或缺少新工具。当前顺序执行在
`agent/tool_executor.py::execute_tool_calls_sequential` 直接调用
`model_tools.handle_function_call`；并发执行经
`run_agent.py::AIAgent._invoke_tool` →
`agent/agent_runtime_helpers.py::invoke_tool` 再到同一 handler。两条路径都没有
携带“产生这次调用的 Agent/turn fingerprint”。

**根因：** 动态能力状态、tool schema cache 和 Agent 实例生命周期没有共享
版本化 identity；handler 若只读取最新 verified snapshot，会把旧 Agent 发出的
调用误当成新配置下的授权。并发 worker 若启动后再从 mutable Agent 读
fingerprint，也无法证明它使用的是分派时 identity。schema refresh 若直接覆盖
`agent.tools` 又会丢失后注入工具。

**影响：** 已撤销/失效的生图能力仍可被模型调用，或新验证成功的能力必须重启
才出现；CLI、Gateway、TUI、WebUI 的表现可能不同。

**hand-port 边界：** B3 增加 next-turn 原子 refresh 和 caller-vs-current
call-time 最终门禁，只替换上一版 registry 工具并保留 non-registry schema。
Agent 初始化/每次成功 refresh 原子暴露 `_image_capability_fingerprint`；
顺序与并发 executor 都在分派前捕获它。顺序路径显式传给
`handle_function_call`，并发路径经 `_invoke_tool`/`invoke_tool` 透传同一不可变
值。handler 比较 caller fingerprint 与 current verified snapshot；不一致返回
稳定 `capability_caller_stale`，Provider 调用数为 0。B4 再把相同快照接入四个
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
使用同一 schema version。B3 同时独占 `api/model_config.py` 的 vision/image
verification state path、原子写入/读取、公开投影、测试结果写回和 effective
fingerprint；B4 只能在 exact mutation functions 成功提交后调用统一 hook，
不得重写这些状态语义。

### P1-6：vision 最终 handler 会在裁决失败后继续调用 Provider

**证据：**
`tools/vision_tools.py::_handle_vision_analyze` 捕获
`decide_image_input_mode` 的异常后会退到 auxiliary 路径，并继续调用
`vision_analyze_tool`；当前 handler 没有强制核对 verification schema version、
effective fingerprint 与严格 provider/model identity。

**根因：** 入口预处理的路由决定被当作提示而非调用时授权，异常回退跨过了
最终 Provider 门禁。

**影响：** 未知 main model、未验证 auxiliary 或 stale verification 可能在
长生命周期进程中触发 Provider 调用。

**hand-port 边界：** B3 必须给 `_handle_vision_analyze` 增加独立 RED 和
versioned call-time gate；上述三类状态都要在 Provider 调用次数为零时
fail-closed，只有已知 native 或当前 verified 且 provider/model 精确匹配的
aux 路径可继续。

### P1-7：`capability_route` 生产责任跨 Task B/Task C

**证据：**
当前基线没有 `capability_route`；来源 `f8fb1d56` 在生图意图/streaming 路径
同时引入诊断事件，若 Task C 再定义事件，B4 的四入口一致性无法独立成立。

**根因：** 能力裁决、实际工具执行与会话 Artifact 编排没有明确的事件所有者。

**影响：** 可能出现先宣告一个 route、实际执行另一分支，或同一轮重复发送两种
payload/reason code。

**hand-port 边界：** B4 在 Task C 前建立唯一 producer：由 B3 当前裁决和
`tool_executor` 的真实 `image_generate` 执行生成标准
`capability_route`，WebUI 映射 callback/SSE，CLI/Gateway/TUI 消费相同
payload/status/reason。Task C 只消费该事件并处理 Artifact，不得重定义。

## 4. P2 findings

### P2-1：credential default/fallback 矩阵未写成明确契约

当前 resolver 同时存在显式 ref、同 family default 与 canonical legacy env
三条路径；若不把优先级和 fail-closed 分支固定为参数化测试，新增 custom/
domestic binding 容易在缺 Secret 或篡改时错误 fallback。

**边界：** B2 类别 7 必须覆盖完整矩阵：显式 ref 缺失、Secret 空、
family mismatch 或 env tamper 均不得 fallback；没有 ref 且没有 default 时使用
canonical legacy env；唯一合法 default 即使 Secret 尚缺失，也允许按当前源码
意图回落 canonical legacy env；tampered 或重复 default 必须 fail-closed。

### P2-2：类别 17 把既有 downloader 保护与新增 scope 传播混为一个 RED

当前 `save_url_image` 已有 DNS/peer/redirect/MIME/magic/dimensions/size 与
atomic-write 保护，这些测试在实现前本应是 GREEN；新增缺口是 shared
`network_scope` 没有在每个 redirect hop 传播。

**边界：** 先独立运行既有 `test_save_url_image.py` 和
`test_image_gen_artifact_security.py` 作为初始 GREEN characterization；类别
17 的唯一 RED 是
`test_image_download_propagates_network_scope_on_every_hop`，不能靠破坏既有
断言制造 RED。

### P2-3：内置 Provider 模型函数落点必须精确

`ConfigurableOpenAIImageProvider._model` 在 requested model 不在允许列表时会
返回 default；部分内置 Provider 和 WebUI 保存路径也没有使用同一 allowlist
校验。内置函数必须按现有源码精确落点修订：
`DashScopeQwenImageProvider._model`、Doubao 顶层 `_resolve_model`、
`QianfanImageGenProvider._model`、`ZhipuImageGenProvider._model`、
`MinimaxImageGenProvider._model`。

**影响：** UI/路由宣告的模型与实际计费、权限和输出模型不一致，测试结果不能
证明用户选择的模型可用。

**边界：** B1 在保存、运行和插件适配器三层使用同一 fail-closed
model contract；只有显式 `allow_custom_model_id` 的 custom provider 可接受
额外 model id。

### P2-4：fingerprint、状态文件和 tool cache 没有 schema version

`agent/image_gen_verification.py` 的状态与 fingerprint 没有版本字段；
`api/model_config.py` 的 vision 状态同样只比对 fingerprint/status；
`model_tools._tool_defs_cache` 只包含 config stat、registry generation 等运行
输入，没有验证 schema version。

**影响：** 算法升级后，旧状态可能被当作当前 `verified`，旧 cache 也不能可靠
失效。

**边界：** B3 统一当前版本常量；缺失、旧版、未知新版全部降为
`configured_unverified`。需覆盖 `api/model_config.py` 的
`_read_vision_verification_state`/`_public_vision_verification`/
`test_vision_config`、对应 image-gen read/public/test 与
`test_model_config_api.py`；B4 只拥有 mutation hook/传播。

### P2-5：Provider API JSON 的 MIME 和读取大小无界

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
| B1 | aliases、family、model allowlist、unknown capability；不读取 Secret | credential binding、网络 transport、verification version、streaming、Artifact、UI |
| B2 | domestic/custom image+vision 完整 credential binding、WebUI custom CRUD、三类 network scope、named trusted-proxy policy boundary、pinned direct sync/async transport、bounded JSON | tool lifecycle、Agent cache、Artifact 协议、任意/ambient proxy、声称应用可由标准 CONNECT attest remote IP |
| B3 | WebUI/Agent verification state schema/version、effective fingerprint/public projection、cache identity、next-turn refresh、sequential/concurrent caller-vs-current image gate、vision call-time gate | mutation hook、四入口编排、事件生产、Artifact/下载重写 |
| B4 | post-mutation hook、唯一 `capability_route` producer、WebUI/CLI/Gateway/TUI 相同 snapshot/reason code、agent signature | verification 核心读写/投影、新 UI 信息架构、第二套状态、旧 artifact、Provider transport |

每个子任务只在其前置子任务的已审 commit 上继续；不得把 B2 的安全 transport
与 B4 streaming 大文件揉成一个 diff。

## 7. 27 类 must-first RED 测试

以下编号是 Task B 的验收索引。每类测试必须先证明当前实现因目标断言失败，再
做 GREEN；RED 不能来自测试语法、fixture、import 或环境错误。

### B1：alias、family、model（1–6）

B1 测试不得解析 Secret；需 monkeypatch 证明 credential/env/HTTP seam 调用数
为零。

1. **Canonical family aliases** —
   `test_provider_family_aliases_are_canonical`：vision/image/legacy alias
   收敛到唯一 family，未知 alias 不借用相邻 family。
2. **Custom aliases without Secret lookup** —
   `test_custom_provider_aliases_are_canonical_without_secret_lookup`：
   `custom:*` 归一化稳定，alias 冲突确定失败，过程不读取 Secret。
3. **Known built-in model exactness** —
   `test_builtin_known_image_models_resolve_exactly`：五个内置 Provider 使用
   各自精确 `_model`/Doubao `_resolve_model` 返回所选 model。
4. **Unknown built-in model before credentials** —
   `test_builtin_unknown_image_models_fail_before_credential_lookup`：
   未知内置 model 在 credential/网络前失败，不回落 default。
5. **Custom model explicit opt-in** —
   `test_custom_image_model_requires_explicit_allow_custom_model_id`：
   custom model 只有显式 `allow_custom_model_id` 才可接受。
6. **Unknown vision model/capability** —
   `test_unknown_vision_model_and_capability_fail_closed`：未知 main capability
   不猜 native/aux，未知 aux model 在保存/运行前停止。

### B2：凭据与 outbound transport（7–18）

B2 RED 只能调用当前 public seams（`resolve_api_key`、custom Provider/config、
`resolve_provider_client`、`save_url_image`、WebUI custom CRUD）；不得导入
尚不存在的 `safe_outbound_http.py`，该模块只在 GREEN 创建。

7. **Credential default matrix** —
   `test_credential_binding_default_matrix_is_fail_closed_and_legacy_compatible`：
   覆盖显式 missing/empty/mismatch/tamper 不 fallback；no-ref/no-default 走
   canonical legacy；唯一合法 default 且 Secret 缺失按当前意图允许 legacy；
   tampered/duplicate default fail-closed。
8. **Custom image+vision complete binding** —
   `test_custom_provider_credential_binding_is_canonical_across_runtime_and_webui`：
   覆盖 `_entry_api_key`、public row、`is_available`、setup schema、`generate`、
   custom vision resolver 与 WebUI image/vision get/set/delete；拒绝任意
   `api_key_env`，family/env tamper 时请求为零。
9. **URL shape** —
   `test_endpoint_url_shape_is_fail_closed`：非 HTTPS、userinfo、非法端口、
   fragment、混淆 hostname、IP literal 编码和非法 path/query fail-closed。
10. **`public_direct`** —
    `test_public_direct_pins_all_answers_peer_sni_and_host`：全量 DNS answer、
    单次解析、peer equality、原 hostname SNI/Host 全部成立。
11. **`private_direct`** —
    `test_private_direct_requires_explicit_scope_and_keeps_permanent_blocks`：
    只有显式 scope 允许 RFC1918/loopback/ULA；不允许隐式降级，永久禁区仍拒绝。

类别 12–14 的三个既有 node 共同参数化 trusted-proxy 边界：未批准 profile
请求数为 0；已批准 profile 模拟 remote-public 时允许；proxy 模拟
remote-blocked 时返回结构化拒绝；应用映射为稳定
`trusted_proxy_origin_blocked`，direct/private fallback 调用数为 0。

12. **`trusted_proxy`** —
    `test_trusted_proxy_uses_connect_origin_tls_and_never_falls_back`：只接受预先
    批准且声明 `public_egress`/`dns_ip_classification` capability 的 named
    profile；拒绝任意 URL/ambient proxy；应用校验 proxy 地址、origin
    URL/literal/metadata hostname、CONNECT 与 origin TLS/SNI/certificate，
    但不声称标准 CONNECT 可 attest remote resolved IP。
13. **Permanent network blocks** —
    `test_network_scopes_block_metadata_link_local_and_mapped_variants`：
    direct 模式由应用拒绝 metadata/link-local/unspecified/multicast/其它
    reserved/benchmark 及映射变体；trusted proxy 的 origin literal/metadata
    hostname 由应用先拒绝，hostname 的远端 DNS 分类由 proxy policy 拒绝并让
    应用映射稳定 reason。
14. **Fake-IP** —
    `test_fake_ip_range_is_never_connected`：`198.18.0.0/15` literal、DNS answer、
    proxy address、connected peer 与 Provider 特例均不得连接；应用拒绝
    origin literal/不安全 proxy 自身地址，trusted-proxy hostname 的远端
    Fake-IP answer 由 proxy policy 拒绝并映射
    `trusted_proxy_origin_blocked`，无 direct fallback。
15. **Custom vision rebinding** —
    `test_custom_vision_sync_and_async_resist_dns_rebinding`：sync/async 均在发送
    凭据前阻断公开预检/私网连接切换。
16. **Custom image API POST** —
    `test_custom_image_post_is_pinned_and_never_redirects_auth`：
    DNS/connect/TLS 固定，POST 不跟随 redirect/跨 origin 转发 Authorization。
17. **Downloader scope propagation** —
    `test_image_download_propagates_network_scope_on_every_hop`：这是唯一行为性
    RED；既有 downloader 与 artifact-security suites 先单独作为初始 GREEN
    characterization，GREEN 后每个 hop 都传播并重验 scope。
18. **Bounded JSON** —
    `test_provider_json_is_mime_checked_and_bounded_before_parse`：JSON MIME、
    `Content-Length`/chunked 与 1–2 MiB hard limit 在 parse 前执行，错误脱敏。

### B3：版本、cache、长生命周期 Agent（19–23）

B3 RED 调用当前 `api.model_config` get/test、`model_tools`、Agent/tool
registry、sequential executor、`AIAgent._invoke_tool`/
`agent_runtime_helpers.invoke_tool` 和 `_handle_vision_analyze` public seams；
不得靠导入拟新增 `image_runtime.py`/helper 产生 ImportError。

19. **WebUI verification state version** —
    `test_webui_verification_state_requires_current_schema_version`：
    参数化 vision/image 状态读写与 public projection，缺失/旧版/未知新版不能
    继承 `verified`。
20. **WebUI effective fingerprint** —
    `test_webui_effective_fingerprint_expands_env_or_fails_unresolved`：
    vision/image fingerprint 使用 runtime 同一 env 展开器，未解析 token
    fail-closed。
21. **Cache identity** —
    `test_tool_cache_key_tracks_versioned_webui_verification_snapshot`：
    WebUI verification version/fingerprint 变化立即失效 check_fn/tool defs
    cache。
22. **Long-lived Agent + image call gate** —
    `test_long_lived_agent_refresh_and_image_call_gate_preserve_non_registry_tools`：
    同一 node 参数化 sequential/concurrent；先捕获旧 Agent fingerprint，再让
    新配置获得当前 verified snapshot，两路均在 Provider 前返回稳定
    `capability_caller_stale`、调用数为 0。并同时覆盖增删 `image_generate`、
    refresh 原子回滚和 memory/context/MCP 等 non-registry schema 保留；只读
    当前 snapshot 不算关闭 stale caller。
23. **Vision final-handler call-time gate** —
    `test_vision_handle_call_time_gate_blocks_unknown_unverified_and_stale_before_provider`：
    unknown main、unverified aux、旧 version/fingerprint 在任何 Provider 调用前
    失败；known native 与当前 verified、精确 provider/model aux 才可继续。

### B4：四入口、事件与 mutation propagation（24–27）

24. **四入口一致快照** —
    `test_all_entrypoints_share_capability_snapshot_and_reason_codes`：
    CLI/Gateway/TUI/WebUI 返回相同 version/fingerprint/status/reason 和实际
    Provider 调用次数。
25. **Agent cache identity** —
    `test_webui_and_gateway_agent_cache_identity_tracks_capability_snapshot`：
    WebUI session/Gateway signature 包含能力快照，不复用 stale Agent。
26. **唯一 `capability_route` producer** —
    `test_capability_route_event_matches_decision_and_actual_tool_execution`：
    B4 从 B3 实际裁决和 `tool_executor` 真实执行生产标准事件，WebUI
    callback/SSE 与 CLI/Gateway/TUI 消费相同 payload；Task C 不重复生产。
27. **配置事务传播** —
    `test_config_transaction_propagates_invalidation_without_losing_state`：
    参数化 credential upsert/delete、vision/image test/set、Alibaba 组合保存、
    custom vision/image 增删、main-model set；成功事务统一 post-commit，
    失败不发布半状态，且保留 journal/Artifact/session/non-registry 语义。

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
