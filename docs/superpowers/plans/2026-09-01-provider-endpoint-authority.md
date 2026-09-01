# 主模型 Provider 地址权威与可观测性实施计划

> **For the implementation agent:** Use `executing-plans` with one writer. Code work also requires `andrej-karpathy-skill` and `test-driven-development`; Tasks 8–10 additionally require `frontend-ux-qa`; completion requires `verification-before-completion`. Subagents may perform read-only mapping, QA, and Sol review only.

**Goal:** 让 Provider 身份、实际请求地址、连接检查、辅助调用和设置页展示使用同一地址裁决结果；即使 `zai-cn` 配置残留 DeepSeek 地址，也只能访问智谱国内固定地址，并能在页面看到实际地址和地址来源。

**Architecture:** 在现有 `hermes_cli/providers.py` 增加静态、无 I/O 的 endpoint policy 和纯解析器。保留各 Provider 现有候选采集顺序，在公共 runtime 唯一出口、辅助客户端构造前、连接检查材料和 canonical 配置写入器处裁决。WebUI 两个 API 复用同一个叶子投影 helper；前端不维护 Provider→URL 表，内置 Provider 只读，只有通用 `provider=custom` 使用现有地址编辑器。

**Tech Stack:** Python 3.11、dataclasses、PyYAML、Flask API helpers、Vanilla JavaScript、Pytest、Node 假 DOM 测试、Playwright 隔离浏览器测试。

**Approved design:** `docs/superpowers/specs/2026-09-01-provider-endpoint-authority-design.md`

---

## 执行模型与门禁

- 本设计与计划必须先作为独立 docs 基线提交并推送；实施开始时工作树应干净。
- 一个实施 Agent 是唯一写入者。不得用多写者并行修改 `providers.py`、`runtime_provider.py`、`model_config.py` 或 `panels.js`。
- Tasks 1–4 构成 Agent 后端阶段，提交一次；Tasks 6–9 构成 WebUI/前端阶段，提交一次。
- 每个任务先 RED、再最小实现、再 GREEN；任务之间不做碎提交。
- 两个阶段都必须在精确暂存后、commit 前运行 change-safety、`scripts/verify.sh --full` 和完整 staged-bytes Sol 审核。
- 任何 staged bytes 变化都使已有测试绑定与 Sol 结论失效；修正后重新运行受影响测试、完整门禁和审核。
- 不改 API Key 复用开关、安全模式、图片/语音 Provider、onboarding 的 Provider 清单、制包、安装、麒麟终端或发布链。
- 不使用真实 API Key、真实 OAuth、真实 Provider 或用户默认浏览器做自动化测试。

实施开始前从仓库根目录运行：

```bash
pwd -P
git rev-parse --show-toplevel
git rev-parse --git-common-dir
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list --porcelain
git fetch origin main
git rev-list --left-right --count origin/main...main
git merge-base --is-ancestor origin/main main
```

Expected: physical root 与正式仓库一致，分支为 `main`，工作树无其他任务改动，远端不领先且不分叉。否则先按 `docs/runbooks/development-lifecycle.md` 处理。

## Phase A：Agent 地址权威与配置保护

### Task 1: 实现静态、无 I/O 的 endpoint policy

**Files:**
- Modify: `hermes-local-lab/sources/hermes-agent/hermes_cli/providers.py`
- Create: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_provider_endpoint_resolution.py`
- Verify: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_api_key_providers.py`

- [ ] **Step 1: 写静态策略和 Custom 边界 RED 测试**

至少覆盖：

```python
def test_zai_cn_fixed_endpoint_ignores_config_and_runtime_candidates():
    result = resolve_provider_endpoint(
        "zai-cn",
        configured_url="https://api.deepseek.com/v1",
        runtime_url="https://pool.example.test/v1",
        candidate_source="runtime",
        candidate_override_present=True,
    )
    assert result.effective_url == "https://open.bigmodel.cn/api/paas/v4"
    assert result.policy == "fixed"
    assert result.source == "system"
    assert result.editable is False
    assert result.candidate_ignored is True


@pytest.mark.parametrize("provider", ["custom:glmcode", "unknown-provider"])
def test_named_custom_and_unknown_default_to_read_only_configurable(provider):
    result = resolve_provider_endpoint(
        provider,
        configured_url="https://proxy.example.test/v1",
    )
    assert result.policy == "configurable"
    assert result.effective_url == "https://proxy.example.test/v1"
    assert result.editable is False


def test_only_exact_bare_custom_is_webui_editable():
    assert resolve_provider_endpoint("custom").editable is True
    assert resolve_provider_endpoint("ollama").editable is False


def test_empty_provider_returns_stable_unconfigured_result():
    result = resolve_provider_endpoint("")
    assert result.provider == ""
    assert result.effective_url == ""
    assert result.editable is False
```

再覆盖 `glm-cn`/`zhipu-cn` alias、fixed 缺内建地址 fail closed、runtime-managed 只接受 `runtime_url`、configurable 保留 `configured_url`、尾部 `/` 规范化和 `requires_endpoint`。

- [ ] **Step 2: 证明策略查找不会访问 models.dev 或配置**

测试把 models.dev fetch、`get_provider()`、`resolve_provider_full()` monkeypatch 成一旦调用即抛错，然后调用策略/解析器并断言仍成功。该测试防止 config transaction 或 GET API 因地址解析触发网络/缓存读取。

- [ ] **Step 3: 运行测试并证明 RED**

Run from `hermes-local-lab/sources/hermes-agent`:

```bash
venv/bin/python -m pytest tests/hermes_cli/test_provider_endpoint_resolution.py -q
```

Expected: collection/import failure because the endpoint policy contract does not exist.

- [ ] **Step 4: 增加静态元数据和纯解析结果**

在 `HermesOverlay`/`ProviderDef` 增加：

```python
EndpointPolicy = Literal["fixed", "configurable", "runtime_managed"]


@dataclass(frozen=True)
class EndpointResolution:
    provider: str
    policy: EndpointPolicy
    effective_url: str
    source: str
    editable: bool
    requires_endpoint: bool
    candidate_ignored: bool
```

`zai-cn` 是本轮唯一显式 `fixed` 条目：

```python
"zai-cn": HermesOverlay(
    transport="openai_chat",
    base_url_override="https://open.bigmodel.cn/api/paas/v4",
    endpoint_policy="fixed",
)
```

OAuth、AWS/Bedrock 和外部进程类条目按现有静态元数据明确标为 `runtime_managed`；其他默认 `configurable`。Z.AI 国际版保持 configurable 及现有显式地址/区域探测兼容；未知和用户 Provider 不做动态注册表查询。

- [ ] **Step 5: 实现纯策略与解析器**

```python
def endpoint_policy_for(provider_id: str) -> EndpointPolicy:
    canonical = normalize_provider(provider_id)
    overlay = HERMES_OVERLAYS.get(canonical)
    return overlay.endpoint_policy if overlay else "configurable"


def resolve_provider_endpoint(
    provider_id: str,
    *,
    configured_url: str = "",
    runtime_url: str = "",
    candidate_source: str = "managed",
    candidate_override_present: bool = False,
) -> EndpointResolution:
    raw = str(provider_id or "").strip().lower()
    canonical = normalize_provider(raw)
    policy = endpoint_policy_for(raw)
    # fixed reads only static overlay; runtime_managed reads only runtime_url;
    # configurable prefers a trusted runtime_url when supplied, otherwise
    # uses configured_url.
```

约束：

- 不调用 `get_provider()`、`resolve_provider_full()`、models.dev、环境、配置或网络；
- `source` 表示地址所有权：`system/managed/custom/runtime`，不复用 Key `source`；
- exact `custom` 才 editable；`custom:*`、alias-to-custom、unknown 均只读；
- fixed `candidate_ignored` 只使用调用方明确传入的 `candidate_override_present`；注册/default canonical URL 不算覆盖，即使显式存值恰好等于 canonical URL也算覆盖候选；
- `candidate_source` 与候选一起采集：显式参数、已选 pool、认证状态为 `runtime`，普通配置/注册表和命名式 Custom 为 `managed`，只有 exact Custom 为 `custom`；不得从 Key `source` 或 URL 形态反推；
- fixed API mode 直接使用 `TRANSPORT_TO_API_MODE[overlay.transport]`，不再经过 URL heuristic。

- [ ] **Step 6: 运行聚焦合同测试并证明 GREEN**

```bash
venv/bin/python -m pytest \
  tests/hermes_cli/test_provider_endpoint_resolution.py \
  tests/hermes_cli/test_api_key_providers.py -q
```

Expected: all selected tests pass; existing Provider registry behavior remains unchanged.

### Task 2: 在公共 runtime 唯一出口执行 finalizer

**Files:**
- Modify: `hermes-local-lab/sources/hermes-agent/hermes_cli/runtime_provider.py`
- Modify: `hermes-local-lab/sources/hermes-agent/hermes_cli/auth.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_runtime_provider_resolution.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_api_key_providers.py`
- Verify: `hermes-local-lab/sources/hermes-agent/tests/gateway/test_auth_fallback.py`

- [ ] **Step 1: 写当前故障的可执行 RED 测试**

使用当前真实签名和现有 fixture seam：

```python
def test_zai_cn_dirty_same_provider_config_is_finalized(monkeypatch):
    dirty_model = {
        "provider": "zai-cn",
        "default": "glm-5",
        "base_url": "https://api.deepseek.com/v1",
        "api_mode": "codex_responses",
    }
    monkeypatch.setattr(
        runtime_provider,
        "_get_model_config",
        lambda: dict(dirty_model),
    )
    monkeypatch.setattr(
        runtime_provider,
        "_read_runtime_raw_config",
        lambda: {"model": dict(dirty_model)},
    )
    monkeypatch.setenv("GLM_CN_API_KEY", "test-key")
    resolved = runtime_provider.resolve_runtime_provider(requested="zai-cn")
    assert resolved["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert resolved["api_mode"] == "chat_completions"
    assert resolved["endpoint_policy"] == "fixed"
    assert resolved["endpoint_source"] == "system"
    assert resolved["endpoint_candidate_ignored"] is True
```

再用真实参数 `explicit_api_key/explicit_base_url` 和现有 pool fixture 覆盖显式、凭据池、Gateway fallback binding 三类；控制用例固定 MiniMax、Anthropic 代理、Azure、Bedrock、LM Studio、`zai`、OpenCode、bare/named Custom 和 unknown Provider 现有地址/协议。

专门增加来源/覆盖存在性测试：干净 `zai-cn` 的 registry default → `candidate_ignored=false`；raw `model.base_url`、raw `providers.zai-cn.base_url`、实际选中 pool 地址、显式参数分别 → true；raw 值即使等于 canonical BigModel URL也 → true。禁止用 `bool(candidate["base_url"])` 推断。

- [ ] **Step 2: 运行聚焦测试并证明 RED**

```bash
venv/bin/python -m pytest \
  tests/hermes_cli/test_runtime_provider_resolution.py \
  -k 'zai_cn and (dirty or pool or explicit)' -q
```

Expected: current runtime returns a stale candidate in at least one case; no test may pass merely by raising `TypeError`.

- [ ] **Step 3: 把现有函数体变为私有 candidate resolver**

将当前大函数改名为 `_resolve_runtime_provider_candidate()`；额外接受 wrapper 预读的 raw model config，避免重复且不一致的读取。新增同签名公共 wrapper：

```python
def resolve_runtime_provider(
    *,
    requested=None,
    explicit_api_key=None,
    explicit_base_url=None,
    target_model=None,
):
    model_cfg = _get_model_config()
    raw_config = _read_runtime_raw_config()
    candidate = _resolve_runtime_provider_candidate(
        requested=requested,
        target_model=target_model,
        _model_cfg=model_cfg,
        **_runtime_explicit_inputs(explicit_api_key, explicit_base_url),
    )
    candidate_source = candidate.pop("_endpoint_candidate_source", "managed")
    candidate_override_present = candidate.pop(
        "_endpoint_candidate_override_present", False
    )
    actual_provider = candidate.get("provider") or resolve_requested_provider(requested)
    candidate_override_present = bool(
        candidate_override_present
        or _raw_config_endpoint_override_present(raw_config, actual_provider)
    )
    return finalize_runtime_endpoint(
        candidate,
        requested=requested,
        candidate_source=candidate_source,
        candidate_override_present=candidate_override_present,
    )
```

`_get_model_config()` 继续提供现有 effective model，保留字符串配置、`model`→`default` alias、本地模型自动探测及现有 monkeypatch seam；新增 raw helper 只判断字段是否真实存在，绝不把 raw `model` 直接注入候选解析。这样所有现有 return、Gateway/Cron/Delegate/TUI/模型切换传入的 fallback binding 都只能从一个公共出口离开。增加一个 monkeypatch candidate resolver 的合同测试，证明公共 wrapper 无论候选内容如何都调用 finalizer 一次，并保留 string model、alias 与本地模型探测回归。

- [ ] **Step 4: 实现 runtime finalizer**

- actual Provider 优先用 `candidate["provider"]`，缺失时用 `resolve_requested_provider(requested)`；
- fixed 最终只使用静态地址；raw 同 Provider `model.base_url`、`providers.<id>.base_url`、非空显式参数或实际选中 pool 地址标记候选覆盖，registry/default candidate 不计入；
- 所有候选分支同时写内部 `_endpoint_candidate_source` 和 `_endpoint_candidate_override_present`，公共 wrapper `pop` 后不得泄漏；显式/pool/auth 为 runtime，model/env/registry/admin（含命名式 Custom）为 managed，只有 exact Custom 为 custom；`_raw_config_endpoint_override_present()` 只查看 actual Provider 对应的 raw `model` 与 `providers.<id>` 字段，不看默认合并值；
- runtime-managed 把 candidate URL 作为 `runtime_url`；
- 对 configurable 统一按 `candidate_source` 分流：值为 `runtime`（explicit/pool/auth/区域缓存）时作为 `runtime_url`，其他来源作为 `configured_url`；不得只为某个认证分支特判；
- fixed `api_mode` 直接取静态 transport map；
- 追加 `endpoint_policy/endpoint_source/endpoint_candidate_ignored`，不改变已有 Key `source`。公开 API 的持久 residue 字段仍叫 `endpoint.override_ignored`，不从该瞬时字段推导。

- [ ] **Step 5: 对齐认证状态与 Z.AI 国际缓存边界**

`get_api_key_provider_status()` 和 `resolve_api_key_provider_credentials()` 使用共享解析结果，但凭据 `source` 不变。对 `zai`：

- 合法显式地址直接 resolved；与当前 Key 哈希匹配的有效区域缓存使用 `source=runtime`；
- 已有 Key 但没有匹配缓存时 GET/status 返回 `runtime_unresolved`，不显示 registry default，也不触发探测；尚无 Key 时可显示 registry default，但凭据状态必须仍为未配置；
- connection/runtime 保留现有完整探测并可更新缓存；
- 增加显式地址、当前 Key 匹配缓存、Key 已有但无缓存、无 Key 默认地址四种 status 测试，以及 cached CN/coding endpoint 的 connection/runtime 一致性测试。

- [ ] **Step 6: 运行 runtime、认证和 fallback 回归**

```bash
venv/bin/python -m pytest \
  tests/hermes_cli/test_runtime_provider_resolution.py \
  tests/hermes_cli/test_api_key_providers.py \
  tests/gateway/test_auth_fallback.py -q
```

Expected: all selected tests pass; `zai-cn` fixed，`zai` 与其他 configurable/runtime-managed Provider 保持现有能力。

### Task 3: 封住辅助客户端直连和缓存旁路

**Files:**
- Modify: `hermes-local-lab/sources/hermes-agent/agent/auxiliary_client.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/agent/test_auxiliary_client.py`
- Verify: `hermes-local-lab/sources/hermes-agent/tests/agent/test_auxiliary_config_bridge.py`
- Verify: `hermes-local-lab/sources/hermes-agent/tests/agent/test_minimax_auxiliary_url.py`
- Verify: `hermes-local-lab/sources/hermes-agent/tests/agent/test_auxiliary_named_custom_providers.py`

- [ ] **Step 1: 写 pool、credential、fully/partial explicit 与 cache 三路 RED 测试**

mock OpenAI client，捕获实际构造参数：

```python
def test_auxiliary_zai_cn_explicit_endpoint_is_finalized(monkeypatch):
    explicit_credentials = stub_explicit_credentials()
    resolve_provider_client(
        provider="zai-cn",
        model="glm-5",
        **explicit_credentials,
    )
    assert captured["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
```

参数化覆盖 `_resolve_api_key_provider()` 的 pool、credential/env，及 `resolve_provider_client()` 的 fully explicit 和 partial-explicit（只有 `explicit_base_url`、Key 来自 credentials）路径；控制用例断言 bare Custom、named Custom、MiniMax、Anthropic proxy 保持原 URL。再增加两条 cache 合同：两个不同脏 URL 对 fixed Provider 收敛到同一个 canonical cache entry；陈旧 `api_mode=codex_responses` 在 cache key 与客户端类型选择前被 fixed transport 改为 `chat_completions`。

- [ ] **Step 2: 证明 RED**

```bash
venv/bin/python -m pytest \
  tests/agent/test_auxiliary_client.py \
  -k 'zai_cn and endpoint' -q
```

Expected: at least one client constructor receives the DeepSeek candidate.

- [ ] **Step 3: 在 cache key 和客户端构造前裁决**

在 `_resolve_api_key_provider()` 的 pool/credential 直构造路径，以及 `resolve_provider_client()` fully/partial explicit 分支，最后一次选定候选后立即调用同一 resolver。在 `_get_cached_client()` 调用 `_client_cache_key()` 之前，统一裁决 canonical Provider、URL 和 fixed `api_mode`，并把同一组结果传给后续 `resolve_provider_client()`；禁止先用原始 URL/mode 建 key、构造时才修正。这样自动 fallback、profile fallback、同 Provider 重试和缓存重建都不复用旧地址或错误 wrapper。

- [ ] **Step 4: 运行辅助调用回归**

```bash
venv/bin/python -m pytest \
  tests/agent/test_auxiliary_client.py \
  tests/agent/test_auxiliary_config_bridge.py \
  tests/agent/test_minimax_auxiliary_url.py \
  tests/agent/test_auxiliary_named_custom_providers.py -q
```

Expected: all selected tests pass.

### Task 4: 在 canonical writer 清理 fixed 残留并迁移 24→25

**Files:**
- Modify: `hermes-local-lab/sources/hermes-agent/hermes_cli/providers.py`
- Modify: `hermes-local-lab/sources/hermes-agent/hermes_cli/config.py`
- Modify: `hermes-local-lab/sources/hermes-agent/agent/provider_credentials.py`
- Modify: `hermes-local-lab/sources/hermes-agent/hermes_cli/main.py`
- Modify: `hermes-local-lab/sources/hermes-agent/cli.py`
- Modify: `hermes-local-lab/sources/hermes-agent/hermes_cli/profiles.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_provider_endpoint_resolution.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_config.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_model_provider_persistence.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/cli/test_cli_global_persistence.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_profiles.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/agent/test_credential_store_transactions.py`
- Verify: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_fallback_cmd.py`
- Verify: `hermes-local-lab/sources/hermes-agent/tests/gateway/test_model_switch_persistence.py`
- Verify: `hermes-local-lab/sources/hermes-agent/tests/test_tui_gateway_server.py`

- [ ] **Step 1: 写单条目与已知配置路径的 RED 测试**

```python
def test_fixed_provider_model_normalization_removes_owned_fields():
    model = {
        "provider": "zai-cn",
        "default": "glm-5",
        "base_url": "https://api.deepseek.com/v1",
        "api_mode": "codex_responses",
        "request_receipt": {"id": "keep"},
    }
    assert normalize_model_endpoint_fields(model) is True
    assert model == {
        "provider": "zai-cn",
        "default": "glm-5",
        "request_receipt": {"id": "keep"},
    }
```

再覆盖 `normalize_config_endpoint_fields(config)` 的已知路径：主模型、`fallback_providers`、legacy fallback、`auxiliary.<task>` 及其 fallback chain。只删 fixed 条目的 `base_url/api_mode`；Custom、MiniMax、Azure、LM Studio、图片/语音和未知区段语义不变；第二次执行返回 `False`。另外对 before/after whole-config transition 增加 `fallback_providers[0]` 与 `auxiliary.compression` 的 configurable→configurable 旧地址清理测试，以及显式新地址保留测试。

- [ ] **Step 2: 写 v24→v25 原始配置迁移 RED 测试**

用 v24 原始 YAML 同时放入 active model、新旧 fallback、aux fallback 和无关字段，断言：

- 迁移输入来自 `read_raw_config()` 或 strict transaction 的原始树，不用 `load_config()` 默认合并结果；
- 缺省字段不会因迁移被物化；
- Provider、模型、Key 引用、安全模式、Key 复用、会话、请求回执和未知字段解析后语义相等；
- 第一次升级为 v25 并清理 fixed 字段；第二次 bytes 与 mtime 不变。

- [ ] **Step 3: 写 canonical writer 和绕行入口 RED 测试**

分别证明：

- `save_config()` 写任意配置前归一化；
- `mutate_config_strict()` 与 `mutate_config_env_strict()` 在 receipt/revision reconcile 前归一化；
- `config.set_config_value("model.provider", ...)` 在 configurable→configurable、fixed→configurable、configurable→fixed 三类切换中不继承旧地址；同一事务显式提供的新 configurable 地址仍保留；
- `cli.py::_persist_global_model_switch()` 切 Provider 不保留上一个 Provider 的地址；
- `hermes_cli.profiles.create_profile()` 的 clone/copy 完成后清理新副本，源 Profile 不变；
- CLI `zai-cn` picker 不提示通用 Base URL、不用旧地址探测、不写 `base_url/api_mode`；
- `config.set_config_value`、auth、Gateway/TUI 和 fallback command 通过 canonical writer 自动受保护，不要求逐个源文件复制判断。

- [ ] **Step 4: 运行测试并证明 RED**

```bash
venv/bin/python -m pytest \
  tests/hermes_cli/test_provider_endpoint_resolution.py \
  tests/hermes_cli/test_config.py \
  tests/hermes_cli/test_model_provider_persistence.py \
  tests/cli/test_cli_global_persistence.py \
  tests/hermes_cli/test_profiles.py \
  tests/agent/test_credential_store_transactions.py \
  -k 'endpoint or v24_to_v25 or zai_cn or normalize' -q
```

Expected: missing normalizer/version and at least one bypass produce RED for the asserted behavior, not an unrelated exception.

- [ ] **Step 5: 实现无 I/O 配置归一化**

在 `providers.py` 提供：

```python
def normalize_model_endpoint_fields(model_cfg: MutableMapping[str, Any]) -> bool:
    if endpoint_policy_for(str(model_cfg.get("provider") or "")) != "fixed":
        return False
    changed = False
    for key in ("base_url", "api_mode"):
        if key in model_cfg:
            model_cfg.pop(key, None)
            changed = True
    return changed
```

`normalize_config_endpoint_fields()` 只遍历当前已知结构，不做无界递归，不访问 models.dev，不触碰独立媒体配置。

另提供 `normalize_model_endpoint_transition(previous_model, next_model)`，并由 `normalize_config_endpoint_transitions(previous_config, next_config)` 对主模型、Fallback、legacy fallback、`auxiliary.<task>` 与其 fallback chain 等已知路径逐项配对复用：Provider 身份变化且 next 中 `base_url/api_mode` 与 previous 相同则删除；next 在同一事务中为 configurable Provider 显式写入不同的新值则保留；next 为 fixed 时仍由 fixed normalizer 无条件删除。列表只按当前稳定位置/标识配对，不做无界递归。

- [ ] **Step 6: 把归一化放进 durable primitives**

- `save_config()`：以落盘前可得的完整旧 raw tree 为 before snapshot，先执行 whole-config transition normalizer，再执行 fixed normalizer；
- `mutate_config_strict()` / `mutate_config_env_strict()`：保留 mutator 前的完整 raw config snapshot；mutator 返回后先执行 whole-config transition/fixed normalizer，再做 credential revision/receipt reconcile；
- strict writer 对纯 normalizer 使用函数内延迟导入，增加 import-smoke 测试，避免 `provider_credentials` 与 `hermes_cli` 初始化循环；
- 保持现有锁、compare-and-swap、原子写、权限和回滚语义；
- 无变化时不增加额外写盘。

- [ ] **Step 7: 实现 24→25 幂等迁移**

把配置版本提升到 25。迁移在既有事务内读取 raw tree，调用配置归一化，再写版本；GET、WebUI 启动和 `/api/providers` 不触发迁移。运行安全仍由 Tasks 1–3 保证。

- [ ] **Step 8: 只修真正绕过 canonical writer 的路径**

- `cli.py` round-trip 全局模型切换：Provider change 时通过同一配置归一化/清旧所有权字段；
- `hermes_cli/profiles.py` direct copy：新 Profile 完成复制后用其 config 路径执行 strict normalize；源 Profile 不变；
- `hermes_cli/main.py` fixed picker：不提示/探测/保存通用地址；
- `model_switch.py`、`fallback_cmd.py`、auth、Gateway 和 TUI 先只做回归验证；只有聚焦 RED 证明绕过 canonical writer 才修改源文件；已确认绕过的旧 Web 主模型 writer `api.config.set_hermes_default_model()` 在 Task 6 直接纳入 RED 与修复。

- [ ] **Step 9: 运行配置、CLI、Profile 和持久化回归**

```bash
venv/bin/python -m pytest \
  tests/hermes_cli/test_provider_endpoint_resolution.py \
  tests/hermes_cli/test_config.py \
  tests/hermes_cli/test_model_provider_persistence.py \
  tests/hermes_cli/test_fallback_cmd.py \
  tests/cli/test_cli_global_persistence.py \
  tests/hermes_cli/test_profiles.py \
  tests/agent/test_credential_store_transactions.py \
  tests/gateway/test_model_switch_persistence.py \
  tests/test_tui_gateway_server.py -q
```

Expected: all selected tests pass.

### Task 5: Phase A 完整门禁、Sol 审核与后端提交

**Files:**
- Stage: all Agent source/test files modified by Tasks 1–4
- Verify: `docs/superpowers/specs/2026-09-01-provider-endpoint-authority-design.md`
- Verify: `docs/superpowers/plans/2026-09-01-provider-endpoint-authority.md`

- [ ] **Step 1: 精确暂存 Phase A 文件**

Run from repository root:

```bash
git add \
  hermes-local-lab/sources/hermes-agent/hermes_cli/providers.py \
  hermes-local-lab/sources/hermes-agent/hermes_cli/runtime_provider.py \
  hermes-local-lab/sources/hermes-agent/hermes_cli/auth.py \
  hermes-local-lab/sources/hermes-agent/agent/auxiliary_client.py \
  hermes-local-lab/sources/hermes-agent/hermes_cli/config.py \
  hermes-local-lab/sources/hermes-agent/agent/provider_credentials.py \
  hermes-local-lab/sources/hermes-agent/hermes_cli/main.py \
  hermes-local-lab/sources/hermes-agent/cli.py \
  hermes-local-lab/sources/hermes-agent/hermes_cli/profiles.py \
  hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_provider_endpoint_resolution.py \
  hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_runtime_provider_resolution.py \
  hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_api_key_providers.py \
  hermes-local-lab/sources/hermes-agent/tests/agent/test_auxiliary_client.py \
  hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_config.py \
  hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_model_provider_persistence.py \
  hermes-local-lab/sources/hermes-agent/tests/cli/test_cli_global_persistence.py \
  hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_profiles.py \
  hermes-local-lab/sources/hermes-agent/tests/agent/test_credential_store_transactions.py
```

若 RED 证明必须修改一个“Verify-only”文件，先把证据和最小改动加入本清单；不得目录级暂存。

- [ ] **Step 2: 在 commit 前运行 safety 与完整门禁**

Run from repository root:

```bash
hermes-local-lab/sources/hermes-agent/venv/bin/python scripts/check-local-change-safety.py
git diff --check
git diff --cached --check
scripts/verify.sh --full
```

Expected: safety check and full verification pass while Phase A bytes are staged.

- [ ] **Step 3: 由只读 Sol reviewer 审核五视图**

Run from repository root:

```bash
git status --short
git diff
git diff --cached --name-status
git diff --cached --check
git diff --cached
```

审核重点：静态 resolver 无 I/O；fixed transport 不走 heuristic；runtime wrapper 唯一出口；aux cache key 使用 canonical URL；normalizer 位于 receipt reconcile 前；迁移只读 raw tree；无媒体/安全模式/Key 复用改动。

- [ ] **Step 4: 提交 Phase A**

仅在 Step 2、Step 3 对当前 staged bytes 都通过后：

```bash
git commit -m "fix: enforce provider endpoint authority"
```

## Phase B：WebUI 投影、保存语义与可观测性

### Task 6: 统一 WebUI 主模型材料和安全公开投影

**Files:**
- Create: `hermes-local-lab/sources/hermes-webui/api/provider_endpoints.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/config.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/model_config.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_zai_cn_main_model_provider.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_main_model_verification.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_model_config_api.py`
- Verify: `hermes-local-lab/sources/hermes-webui/tests/test_minimax_provider.py`
- Verify: `hermes-local-lab/sources/hermes-webui/tests/test_pr1970_lmstudio_base_url_fallback.py`
- Verify: `hermes-local-lab/sources/hermes-webui/tests/test_issue1625_local_server_model_id_preservation.py`

- [ ] **Step 1: 写同 Provider 脏地址 RED 测试**

隔离配置：

```yaml
model:
  provider: zai-cn
  default: glm-5
  base_url: https://api.deepseek.com/v1
```

断言：

- `resolve_model_provider()` 与 `_main_model_material()` 使用 BigModel；
- connection mock 和 chat verification fingerprint 使用 BigModel；
- GET `/api/model-config` 的 `main.endpoint.display_url` 是 BigModel；
- `main.base_url == ""`，不把内置旧值交给浏览器；
- `main.endpoint.override_ignored is True` 表示当前磁盘 residue；
- `api.config.set_hermes_default_model()` 切换/保存 `zai-cn` 后不把 canonical 或旧 `base_url/api_mode` 重新写回 raw YAML；
- profile-local Key 读取和“不回退宿主 process env”合同不变。

- [ ] **Step 2: 写 endpoint 公开投影安全 RED 测试**

覆盖状态矩阵：

- fixed resolved；
- runtime-managed 无本地缓存 → `display_url=None/status=runtime_managed`；
- configurable 已知由运行时选择、但本地无法无副作用确定下一地址 → `display_url=None/status=runtime_unresolved`；
- `zai` 有显式地址或当前 Key 匹配缓存 → resolved；有 Key 无缓存 → runtime_unresolved；无 Key → registry default + 凭据未配置；
- configurable/Custom 缺必填地址 → `display_url=None/status=missing`；
- legacy Custom `https://u:p@host/v1?token=x#f` → 安全 `display_url`、`status=invalid_saved_value`、`main.base_url=""`；
- 合法 generic Custom 才返回可编辑 `main.base_url`；
- 两个公开响应不含 userinfo、query、fragment、Key、Authorization 或配置路径；当前 `zai-cn` 行、`main.endpoint` 及其诊断/错误不含被忽略的 DeepSeek 候选，但合法 DeepSeek Provider 行仍显示自己的官方地址。

- [ ] **Step 3: 运行测试并证明 RED**

Run from `hermes-local-lab/sources/hermes-webui`:

```bash
../hermes-agent/venv/bin/python -m pytest \
  tests/test_zai_cn_main_model_provider.py \
  tests/test_main_model_verification.py \
  tests/test_model_config_api.py \
  -k 'zai_cn or endpoint or base_url' -q
```

Expected: current model material returns stale config and public endpoint fields do not exist.

- [ ] **Step 4: 创建 leaf projection helper**

`api/provider_endpoints.py`：

- 只依赖 `hermes_cli.providers` 和标准库；
- 不导入 `api.config`、`api.providers`、`api.model_config`；
- 候选由调用方传入；
- 用 `urlsplit/urlunsplit` 删除 userinfo、query、fragment；
- `public_endpoint(..., runtime_selector_unresolved: bool=False, stored_main_override_present: bool=False)` 使用显式上下文，不改变 Agent resolver 或 `resolve_model_provider()` 返回形状；
- 只在 `policy=runtime_managed and no URL` 时返回 `runtime_managed`；configurable 且 `runtime_selector_unresolved=True` 时返回 `display_url=None/status=runtime_unresolved/source=runtime`；
- configurable 缺地址返回 `missing`；不安全 legacy Custom 返回 `invalid_saved_value`。

- [ ] **Step 5: 让 WebUI 材料消费共享解析结果**

保留 `_get_provider_base_url()` 对 configurable Provider 的现有候选顺序和 `resolve_model_provider()` 的三元返回合同，在其返回前继续执行地址裁决。新增 sibling `_resolve_public_endpoint_candidate(...)`，只消费调用方已加载的本地 config/auth/pool 状态：若可以无网络、无写入地确定所选运行时地址，则返回该地址和 `source=runtime`；若只能知道 runtime selector 接管、却不能确定下一地址，则显式返回 `runtime_selector_unresolved=True`，不得展示普通配置候选为“实际地址”。Z.AI 国际版仅在显式地址或当前 Key 匹配的有效缓存存在时 resolved；有 Key 无缓存时 unresolved；连接检查可以沿用现有探测。

公开 residue 只从 active fixed Provider 的 raw `model.base_url` 计算，因为这是本次 POST/迁移实际拥有并能清理的字段；不使用默认合并结果、effective URL、`providers.<id>`、credential-pool 或瞬时 explicit 参数。后面三类仍由 Phase A runtime finalizer 拒绝并记录瞬时 `endpoint_candidate_ignored`。connection/chat material 不再直接读取原始 `model.base_url`。

- [ ] **Step 6: 收紧 `main.base_url` 兼容字段和验证指纹**

- 合法 generic Custom：返回可编辑地址；
- fixed、其他内置、runtime-managed、named Custom：返回空；
- legacy unsafe Custom：返回空并通过 endpoint status 提示重新录入；
- verification material 使用 `effective_url`，地址/Provider/模型/凭据身份变化使旧验证失效。

同时修复已确认的旧写入旁路：`api.config.set_hermes_default_model()` 改走 strict writer，或在其原子写事务内调用同一 transition/fixed normalizer；对应 RED 证明保存 `zai-cn` 后 raw YAML 不出现 `base_url/api_mode`。

- [ ] **Step 7: 运行后端兼容矩阵**

```bash
../hermes-agent/venv/bin/python -m pytest \
  tests/test_zai_cn_main_model_provider.py \
  tests/test_main_model_verification.py \
  tests/test_model_config_api.py \
  tests/test_minimax_provider.py \
  tests/test_pr1970_lmstudio_base_url_fallback.py \
  tests/test_issue1625_local_server_model_id_preservation.py -q
```

Expected: all selected tests pass.

### Task 7: 对齐 Provider API、保存语义和 Profile clone

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/api/providers.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/model_config.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/profiles.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_provider_management.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_custom_providers_in_panel.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_provider_mismatch.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_main_model_provider_switch.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_zai_cn_main_model_provider.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_issue749_profile_create_model_picker.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_issue2305_profile_create_seeds_skills.py`
- Verify: `hermes-local-lab/sources/hermes-webui/tests/test_issue1202_oauth_provider_status.py`

- [ ] **Step 1: 写 `/api/providers` 同源投影 RED 测试**

- 当前 `zai-cn` 行与 `main.endpoint` 逐字段一致；
- 非活动 Provider 只使用自身注册/管理员候选，绝不套用当前 `model.base_url`；
- named `custom:*` 地址只读；generic `custom` 不强造凭据卡；
- runtime-managed 无缓存和 configurable missing 状态正确；
- configurable runtime selector 无法本地确定时为 `runtime_unresolved`，不得回退展示注册/旧配置地址为实际地址；
- endpoint 投影本身不新增 OAuth、目录、网络或连接检查调用。测试 mock 现有 GET 已有的状态/目录调用，不把整个历史 `/api/providers` 宣称纯本地。

- [ ] **Step 2: 写 omitted、empty 和 Provider change 保存 RED 测试**

后端语义：

- same configurable Provider + body omitted `base_url`：保留现有合法代理地址；
- configurable Provider change + omitted：清除上一个 Provider 的地址；
- configurable Provider change + explicit 新地址：保留并校验新地址；
- fixed：无论 omitted/empty/nonempty 都清理 owned address；
- generic Custom：要求并保存合法地址；
- 旧客户端显式提交 configurable 地址继续保留，现有 `zai` 合同不修改；
- 独立增加 `zai-cn` fixed 测试，不把所有内置 Provider 泛化为 fixed。

- [ ] **Step 3: 写 mutation event 三态 RED 测试**

- dirty GET：`main.endpoint.override_ignored=true`；
- 成功 POST：`main.endpoint.override_ignored=false`，另有一次性 `endpoint_mutation.code=fixed_override_cleaned`；
- 后续 GET：false 且无 mutation event；
- 写盘失败：仍 true；
- 当前 `main.endpoint`、当前 `zai-cn` 行、`endpoint_mutation`、诊断和错误字段都不回显被忽略候选；合法 DeepSeek Provider 行不受影响。

- [ ] **Step 4: 写 Custom 新校验和 Profile clone RED 测试**

- 新保存拒绝缺 scheme/hostname、换行、userinfo、query、fragment；本机 HTTP 允许；
- `/api/model-config/main` 的 route 保留结构化字段错误：HTTP 400 返回 `{error,error_code:"invalid_base_url",field:"base_url"}`，消息不回显原 URL/query，不改磁盘；
- WebUI Profile 创建/克隆完成后，新副本 fixed residue 被清理；源 Profile、config+.env 原子回滚和其他字段不变；
- 当前 onboarding 不支持 `zai-cn`，本任务不增加入口、不修改清单。

- [ ] **Step 5: 运行测试并证明 RED**

```bash
../hermes-agent/venv/bin/python -m pytest \
  tests/test_provider_management.py \
  tests/test_custom_providers_in_panel.py \
  tests/test_provider_mismatch.py \
  tests/test_main_model_provider_switch.py \
  tests/test_zai_cn_main_model_provider.py \
  tests/test_issue749_profile_create_model_picker.py \
  tests/test_issue2305_profile_create_seeds_skills.py \
  -k 'endpoint or base_url or profile' -q
```

Expected: provider rows lack endpoint, omitted is conflated with empty, or profile clone retains residue.

- [ ] **Step 6: 实现 Provider API 投影**

复用 leaf helper，不复制 URL 表/脱敏。活动 Provider 复用 main candidate；非活动行只用自己的候选。Provider 地址投影不得增加现有 GET 之外的网络行为。

- [ ] **Step 7: 实现 POST 保存状态机**

用 `"base_url" in body` 区分 omitted；按 Step 2 四类语义更新配置。fixed 地址通过 Phase A strict writer 再做最终保护。POST 保留 Provider、模型、Key 和 request receipt，返回权威重读与一次性 mutation event。

- [ ] **Step 8: 实现 Custom 校验与 Profile clone 收口**

共享安全校验既服务 POST，也服务 `main.base_url` 公开兼容字段。为 field validation 使用稳定异常/结果类型，`api/routes.py` 只把该类型映射成 `invalid_base_url/base_url`，其他 ValueError 保持既有错误合同。Profile clone/copy 后在目标配置的 strict transaction 内归一化；不修改源。

- [ ] **Step 9: 运行 Provider、保存和 Profile 回归**

```bash
../hermes-agent/venv/bin/python -m pytest \
  tests/test_provider_management.py \
  tests/test_custom_providers_in_panel.py \
  tests/test_provider_mismatch.py \
  tests/test_main_model_provider_switch.py \
  tests/test_zai_cn_main_model_provider.py \
  tests/test_issue749_profile_create_model_picker.py \
  tests/test_issue2305_profile_create_seeds_skills.py \
  tests/test_issue1202_oauth_provider_status.py \
  tests/test_model_config_api.py -q
```

Expected: all selected tests pass.

### Task 8: 显示实际地址并修正前端保存/可访问性合同

> **Required gate:** Invoke `frontend-ux-qa` before editing and use its feature-contract, browser, accessibility, responsive, error-state, and Chinese report requirements. Keep one writer; read-only QA agents may review.

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/static/index.html`
- Modify: `hermes-local-lab/sources/hermes-webui/static/panels.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/style.css`
- Modify: `hermes-local-lab/sources/hermes-webui/static/i18n.js`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_custom_providers_in_panel.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_model_config_responsive.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_issue1202_oauth_provider_status.py`
- Verify: `hermes-local-lab/sources/hermes-webui/tests/test_chinese_locale.py`
- Verify: `hermes-local-lab/sources/hermes-webui/tests/test_japanese_locale.py`

- [ ] **Step 1: 更新前端功能契约并写 Provider 卡 RED Node 测试**

覆盖 API Key、OAuth、named Custom：

- endpoint block 在 OAuth 早返回前创建；
- URL 用 `textContent`，恶意文本不成为 HTML；
- body 有稳定 ID；header 初始 `aria-expanded=false/aria-controls=id`；
- 保持 header 为原生 `<button>`，只绑定 click toggle；Enter/Space 依赖浏览器原生合成 click，避免额外 keydown 双切换，并同步 ARIA；
- named Custom 没有 input 时不得执行 `input.focus()`；
- source/status 文案来自 i18n key；runtime-managed、configurable runtime-unresolved 与 missing 三种文案不同。

- [ ] **Step 2: 写主模型 payload/reconcile RED 测试**

内置 Provider 仍必须保留幂等合同：

```javascript
assert.equal(payload.provider,'zai-cn');
assert.equal(payload.model,'glm-5');
assert.match(payload.request_id,/^[0-9a-f]{32}$/);
assert.equal(Object.hasOwn(payload,'base_url'),false);
assert.equal(Object.hasOwn(payload,'api_key'),false);
```

通用 Custom payload 含 `base_url`；有非空 Key 时才含 `api_key`。`expected`、receipt match、uncertain reconcile 和 authoritative reread 均保留同一个 request ID，不能因删 `base_url` 进入虚假“待核对”。

- [ ] **Step 3: 写地址编辑、named Custom 和字段错误 RED 测试**

- exact `custom` 显示 Base URL 输入；named `custom:*` 地址和 Key 均只读，隐藏 Key 输入/更新动作并显示管理员提示；
- 从已保存 `zai-cn` 新切换到 exact Custom 时不复用 BigModel 的 `main.endpoint`，只显示 Custom 草稿或 missing；只有已保存 main 本身为 Custom 才可用 main endpoint 回填；
- 内置 dirty 比较、回填、结果匹配不比较 `base_url`；
- configurable 同 Provider 保存不因 payload omitted 丢失后端代理地址；
- 新增 `modelConfigBaseUrlError`，输入关联 `aria-describedby`；本地/400 字段错误保留草稿、显示 `role=alert`、聚焦地址、清除旧错误；
- `invalid_saved_value` 提示重新录入但不显示 legacy secret；
- mutation event 显示一次性成功清理提示，current residue 使用持续状态提示。

- [ ] **Step 4: 运行测试并证明 RED**

```bash
../hermes-agent/venv/bin/python -m pytest \
  tests/test_model_config_frontend.py \
  tests/test_custom_providers_in_panel.py \
  tests/test_model_config_responsive.py \
  tests/test_issue1202_oauth_provider_status.py \
  -k 'endpoint or base_url or provider_card or request_id' -q
```

Expected: no endpoint UI exists and current payload sends `base_url` for every Provider.

- [ ] **Step 5: 实现两个页面的只读地址展示**

- Provider 卡公共分支创建带标签的 `<output>` 和 source/status；
- 主模型摘要只读 `main.endpoint`；
- 通用 Custom 不新增 Provider 凭据卡，继续使用模型配置原表单；
- 保存后摘要只用 POST 权威 `main.endpoint`；
- 使用 DOM API/textContent，不把 URL 拼入 `innerHTML`。

- [ ] **Step 6: 实现 payload、草稿和错误状态**

```javascript
const expected={provider,model,request_id:_newMainModelConfigRequestId()};
const payload={provider,model,request_id:expected.request_id};
if(apiKey) payload.api_key=apiKey;
if(provider==='custom'){
  payload.base_url=baseInput.value.trim();
  expected.base_url=payload.base_url;
}
```

实现 Step 3 的字段内联错误、exact Custom/named Custom 分流和 mutation/current-status 分流；保存不确定仍权威重读，不自动重放。

- [ ] **Step 7: 实现 ARIA、响应式和 i18n**

- Provider card value `overflow-wrap:anywhere; user-select:text`；
- 稳定 `aria-expanded/aria-controls` 和无 input 安全 focus；
- 地址错误 label/description/alert 完整；
- 新文案加入当前 `i18n.js` 所有语言结构，至少运行已有中/日 locale 解析合同；
- `1280×900`、`768×900`、`390×844` 均不横向溢出。

- [ ] **Step 8: 运行前端合同和 lint**

```bash
../hermes-agent/venv/bin/python -m pytest \
  tests/test_model_config_frontend.py \
  tests/test_custom_providers_in_panel.py \
  tests/test_model_config_responsive.py \
  tests/test_issue1202_oauth_provider_status.py \
  tests/test_chinese_locale.py \
  tests/test_japanese_locale.py -q
npm run lint:runtime
```

Expected: all selected tests and runtime lint pass.

### Task 9: 跨层回归、隔离浏览器和中文 UX QA 报告

**Files:**
- Create: `hermes-local-lab/sources/hermes-webui/tests/test_provider_endpoint_authority.py`
- Create: `hermes-local-lab/sources/hermes-webui/tests/provider_endpoint_authority_browser_smoke.py`
- Create: `docs/reviews/provider-endpoint-authority-ux-qa-2026-09-01.md`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_provider_mismatch.py`

- [ ] **Step 1: 写最小跨层集成测试**

同一临时 `HERMES_HOME` 使用 `zai-cn + glm-5 + DeepSeek residue` 和假的 Key，禁止真实网络。只做三个高价值集成断言：

1. 实际 chat material 使用 BigModel；
2. `/api/model-config.main.endpoint` 使用 BigModel；
3. `/api/providers` 当前 `zai-cn` 行与 main endpoint 一致。

runtime、pool、explicit、auxiliary 和 migration 不在此重复九层耦合，沿用 Phase A 聚焦测试。所有公开响应可扫描测试 Key、Authorization、配置路径、query token 和 userinfo；被忽略的 DeepSeek 候选只在当前 `zai-cn` 行、`main.endpoint` 及其诊断/错误中断言不存在，合法 DeepSeek Provider 行不受影响。

- [ ] **Step 2: 创建隔离 Playwright smoke**

复用现有 browser smoke 安全模式：

- `tempfile.mkdtemp()` 创建 state 与 evidence，输出证据路径；
- 清除所有 `*_API_KEY`；
- `HERMES_HOME/HERMES_BASE_HOME/HERMES_WEBUI_STATE_DIR` 指向临时目录；
- `TAIJI_WEBUI_TEST_NETWORK_BLOCK=1`；
- Chromium headless 独立 context，不打开默认浏览器；
- 分别拦截并固定 GET `/api/providers`、GET `/api/model-config`、POST `/api/model-config/main`、GET `/api/provider/quota`；若驱动连接检查，再加 POST `/api/model-config/main/check`；
- 阻断所有非本机 HTTP/WS；退出时只停止本脚本进程。

- [ ] **Step 3: 驱动真实 UI 场景**

至少验证：

- 两个页面的当前 `zai-cn` 区域显示同一 BigModel 地址，内置地址不可编辑，被忽略候选不可见；合法 DeepSeek Provider 卡仍可显示其官方地址；
- OAuth 与 named Custom 卡 click/Enter/Space 可展开，无 console/page error；
- generic Custom 在模型配置编辑，payload 有 request ID/base_url，无空 Key；
- 非法 Custom 保留草稿、字段报错、聚焦、磁盘不变；
- mutation success、dirty residue、save uncertain、runtime refresh pending 不产生虚假成功；
- `1280×900`、`768×900`、`390×844` 下实际 DOM 无横向溢出并保存截图。

- [ ] **Step 4: 运行集成与浏览器测试**

Run from `hermes-local-lab/sources/hermes-webui`:

```bash
../hermes-agent/venv/bin/python -m pytest \
  tests/test_provider_endpoint_authority.py \
  tests/test_provider_mismatch.py -q
../hermes-agent/venv/bin/python tests/provider_endpoint_authority_browser_smoke.py
```

Expected: tests pass, browser exits 0, no real network/Key/OAuth/default-browser use.

- [ ] **Step 5: 由只读 QA reviewers 复核并输出中文报告**

`docs/reviews/provider-endpoint-authority-ux-qa-2026-09-01.md` 必须包含：

- 功能合同表：数据/API、UI 入口、反馈、错误、空/加载/禁用、键盘/可访问性、E2E；
- 状态矩阵：fixed/configurable/runtime-managed、runtime-unresolved/missing、generic/named Custom、dirty/clean/mutation、保存中/失败/不确定；
- P0/P1/P2/P3 问题清单；
- 已修复、残留风险、未知项；
- 实际命令、结果、截图/证据路径；
- 未执行的 axe、视觉回归、长时运行明确标记“未验证”；
- 证据只证明本地源码隔离 WebUI，不等于真实 Key、安装态或麒麟终端验收。

### Task 10: Phase B 完整门禁、Sol 审核、提交与推送

**Files:**
- Stage: all WebUI/frontend/test/report files modified by Tasks 6–9
- Verify: Phase A commit and cumulative `origin/main...HEAD`

- [ ] **Step 1: 运行 Phase B 聚焦回归**

Run from `hermes-local-lab/sources/hermes-webui`:

```bash
../hermes-agent/venv/bin/python -m pytest \
  tests/test_provider_endpoint_authority.py \
  tests/test_zai_cn_main_model_provider.py \
  tests/test_main_model_provider_switch.py \
  tests/test_main_model_verification.py \
  tests/test_model_config_api.py \
  tests/test_model_config_frontend.py \
  tests/test_provider_management.py \
  tests/test_custom_providers_in_panel.py \
  tests/test_provider_mismatch.py \
  tests/test_minimax_provider.py \
  tests/test_pr1970_lmstudio_base_url_fallback.py \
  tests/test_issue1625_local_server_model_id_preservation.py -q
```

```bash
npm run lint:runtime
../hermes-agent/venv/bin/python tests/provider_endpoint_authority_browser_smoke.py
```

Expected: all focused tests, lint, and isolated browser pass.

- [ ] **Step 2: 精确暂存 Phase B 文件**

Run from repository root:

```bash
git add \
  hermes-local-lab/sources/hermes-webui/api/provider_endpoints.py \
  hermes-local-lab/sources/hermes-webui/api/config.py \
  hermes-local-lab/sources/hermes-webui/api/model_config.py \
  hermes-local-lab/sources/hermes-webui/api/providers.py \
  hermes-local-lab/sources/hermes-webui/api/profiles.py \
  hermes-local-lab/sources/hermes-webui/api/routes.py \
  hermes-local-lab/sources/hermes-webui/static/index.html \
  hermes-local-lab/sources/hermes-webui/static/panels.js \
  hermes-local-lab/sources/hermes-webui/static/style.css \
  hermes-local-lab/sources/hermes-webui/static/i18n.js \
  hermes-local-lab/sources/hermes-webui/tests/test_provider_endpoint_authority.py \
  hermes-local-lab/sources/hermes-webui/tests/provider_endpoint_authority_browser_smoke.py \
  hermes-local-lab/sources/hermes-webui/tests/test_zai_cn_main_model_provider.py \
  hermes-local-lab/sources/hermes-webui/tests/test_main_model_provider_switch.py \
  hermes-local-lab/sources/hermes-webui/tests/test_main_model_verification.py \
  hermes-local-lab/sources/hermes-webui/tests/test_model_config_api.py \
  hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py \
  hermes-local-lab/sources/hermes-webui/tests/test_provider_management.py \
  hermes-local-lab/sources/hermes-webui/tests/test_custom_providers_in_panel.py \
  hermes-local-lab/sources/hermes-webui/tests/test_provider_mismatch.py \
  hermes-local-lab/sources/hermes-webui/tests/test_model_config_responsive.py \
  hermes-local-lab/sources/hermes-webui/tests/test_issue1202_oauth_provider_status.py \
  hermes-local-lab/sources/hermes-webui/tests/test_issue749_profile_create_model_picker.py \
  hermes-local-lab/sources/hermes-webui/tests/test_issue2305_profile_create_seeds_skills.py \
  docs/reviews/provider-endpoint-authority-ux-qa-2026-09-01.md
```

- [ ] **Step 3: 在 commit 前运行 safety 与完整门禁**

Run from repository root:

```bash
hermes-local-lab/sources/hermes-agent/venv/bin/python scripts/check-local-change-safety.py
git diff --check
git diff --cached --check
scripts/verify.sh --full
```

Expected: safety check and full verification pass while Phase B bytes are staged.

- [ ] **Step 4: 由只读 Sol reviewer 审核五视图**

Run from repository root:

```bash
git status --short
git diff
git diff --cached --name-status
git diff --cached --check
git diff --cached
```

审核重点：两个 API 同源且不泄密；omitted/empty/change 语义正确；`zai` 合法 override 未回归；request ID/reconcile 保留；named Custom 只读；ARIA/错误/窄屏合同完整；staged 内容无其他任务文件。

- [ ] **Step 5: 提交 Phase B**

仅在 Steps 1、3、4 对当前 bytes 都通过后：

```bash
git commit -m "feat: expose effective provider endpoints"
```

- [ ] **Step 6: 执行最终累计只读审核**

Run from repository root:

```bash
git status --short
git diff
git diff --cached --name-status
git diff --cached --check
git diff --stat origin/main...HEAD
git diff origin/main...HEAD
```

Expected: 工作树和暂存区为空，累计差异只有本方案 docs、Phase A、Phase B。若有修正，回到对应阶段，重新聚焦测试、完整门禁、staged Sol 和累计审核。

- [ ] **Step 7: 刷新远端并安全推送**

Run from repository root:

```bash
git fetch origin main
git rev-list --left-right --count origin/main...main
git merge-base --is-ancestor origin/main main
git status --short
git push origin main
git rev-parse HEAD
git rev-parse origin/main
git status --short
```

Expected: fetch 后 remote ahead count 为 `0`、不分叉；push 后 local HEAD 与 `origin/main` 相同，工作树干净。远端领先或分叉时停止，禁止强推。

- [ ] **Step 8: 报告证据边界**

- **已实时验证：** 源码聚焦测试、隔离浏览器、两阶段完整本地门禁、staged Sol、commit/push；
- **未实时验证：** 真实智谱 Key 对话、安装包、安装态、麒麟终端；
- **剩余风险：** 只写未执行证据层，不把设计、历史截图或 mock 当安装态事实。

未经另行授权，不创建 Tag、GitHub Release、DEB，不连接、安装或恢复麒麟终端。

## 任务依赖

```text
Task 1 静态 endpoint policy
  ├── Task 2 runtime/auth finalizer
  ├── Task 3 auxiliary finalizer
  └── Task 4 canonical writers + migration
          └── Task 5 Phase A full gate + Sol + commit

Task 5
  └── Task 6 WebUI material + safe projection
          └── Task 7 API/save/profile semantics
                  └── Task 8 frontend + accessibility
                          └── Task 9 integration/browser/UX report
                                  └── Task 10 Phase B full gate + Sol + push
```
