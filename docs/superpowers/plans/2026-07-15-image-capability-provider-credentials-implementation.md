# 图片能力 Provider、凭据与端点解耦 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让阿里百炼的一份凭据可安全复用于识图和生图，同时允许同平台独立 Key 与多平台专用凭据并存，并用真实探测区分“已配置”和“已验证”。

**Architecture:** 新增本地凭据档案和 `credential_ref`；能力配置只引用凭据。阿里端点由统一 helper 构造；识图复用现有严格目标探测，生图复制同一状态机；旧 `DASHSCOPE_*` 采用 lazy fallback。

**Tech Stack:** Python、YAML、Hermes Provider/ImageGenProvider、原生 JavaScript/HTML/CSS、pytest、Node、Electron。

---

## Phase 0：文档发现基线

可复制 API：`resolve_vision_provider_client(..., base_url, api_key)`（`agent/auxiliary_client.py:3891-3934`）、识图探测状态机（`webui/api/model_config.py:656-965`）、动态 `credential_fields`（`plugins/image_gen/domestic_common.py:36-52`）、ImageGenProvider/registry（`agent/image_gen_provider.py:51-143`、`agent/image_gen_registry.py:36-147`）。

官方约束：Key 权限属于业务空间；Key、地域、计费方案、Base URL 必须匹配；模型由请求显式指定。参考 <https://help.aliyun.com/zh/model-studio/get-api-key/>、<https://help.aliyun.com/zh/model-studio/base-url>、<https://help.aliyun.com/en/model-studio/qwen-vl-compatible-with-openai>、<https://help.aliyun.com/zh/model-studio/qwen-image-api>。

当前不存在、不得假装已有：`credential_ref`、`/api/provider-credentials`、`/api/image-gen/test`、生图 verification、命名式自定义识图 Provider。

## 文件边界

新建：

- `hermes-local-lab/sources/hermes-agent/agent/provider_credentials.py`
- `hermes-local-lab/sources/hermes-agent/agent/alibaba_endpoints.py`
- `hermes-local-lab/sources/hermes-agent/agent/custom_vision_providers.py`
- 上述模块对应 tests
- `docs/reviews/image-capability-provider-credentials-ux-qa-2026-07-15.md`

修改：`webui/api/model_config.py`、`webui/api/routes.py`、`agent/auxiliary_client.py`、DashScope 生图插件、`tools/image_generation_tool.py`、`static/index.html/panels.js/style.css` 及对应测试。

---

### Task 1：凭据档案与旧配置 fallback

**Files:** Create `agent/provider_credentials.py`、`tests/agent/test_provider_credentials.py`; modify `webui/api/model_config.py`、`tests/test_model_config_api.py`。

- [ ] 写 RED：命名凭据优先旧变量；无 ref 回退 `DASHSCOPE_API_KEY`；Provider family 不匹配拒绝；Secret 不进 YAML/API；使用中不可删除。
- [ ] 运行 `pytest -q hermes-local-lab/sources/hermes-agent/tests/agent/test_provider_credentials.py`，确认缺模块失败。
- [ ] 实现以下核心契约：

```python
PROVIDER_FAMILY_ALIASES = {"alibaba": "alibaba_dashscope", "dashscope": "alibaba_dashscope"}
LEGACY_API_KEY_ENV = {"alibaba_dashscope": "DASHSCOPE_API_KEY"}

def credential_secret_env(credential_id: str) -> str:
    normalized = normalize_credential_id(credential_id)
    return f"TAIJI_CREDENTIAL_{normalized.upper().replace('-', '_')}_API_KEY"

def resolve_api_key(provider, credential_ref="", *, config_data=None) -> str:
    family = PROVIDER_FAMILY_ALIASES.get(str(provider).lower(), str(provider).lower())
    if not credential_ref:
        return os.getenv(LEGACY_API_KEY_ENV.get(family, ""), "").strip()
    row = find_credential(config_data, credential_ref)
    if row["provider_family"] != family:
        raise ValueError("所选凭据不属于当前 Provider。")
    return os.getenv(row["secret_env"], "").strip()
```

- [ ] 在 WebUI 增加 `get/upsert/delete_provider_credential`；Secret 只用 `_write_env_file()` 写 `.env`，YAML 只存 `id/provider_family/label/auth_type/secret_env`，响应只含 `configured/used_by`。
- [ ] 运行 provider credential 和 model config 定向测试。
- [ ] Commit：`feat: add provider credential references`。

反模式：不删除旧变量；不把 Secret/digest 返回前端；不把 credential pool 的轮换 entry 当固定绑定。

---

### Task 2：统一阿里地域与端点构造

**Files:** Create `agent/alibaba_endpoints.py`、`tests/agent/test_alibaba_endpoints.py`; modify DashScope 插件及测试。

- [ ] 写 RED：北京 Workspace VL URL、新加坡公共 VL URL、自定义 URL 必须 HTTPS、`llm-` Workspace 合法、未知地域拒绝、路径不重复追加。
- [ ] 实现：

```python
PUBLIC_ROOTS = {
    "cn-beijing": "https://dashscope.aliyuncs.com",
    "ap-southeast-1": "https://dashscope-intl.aliyuncs.com",
}

def build_vision_base_url(mode, region, workspace_id, custom_url):
    region = normalize_region(region)
    if mode == "custom":
        return validate_https_url(custom_url)
    if mode == "workspace":
        workspace_id = validate_workspace_prefix(workspace_id)
        return f"https://{workspace_id}.{region}.maas.aliyuncs.com/compatible-mode/v1"
    return f"{PUBLIC_ROOTS[region]}/compatible-mode/v1"

def build_image_root_url(mode, region, workspace_id, custom_url):
    if mode == "custom":
        return validate_https_url(custom_url)
    return f"https://{validate_workspace_prefix(workspace_id)}.{normalize_region(region)}.maas.aliyuncs.com"
```

- [ ] DashScope schema 增加 `endpoint_mode/base_url`；Workspace 不要求 `ws-` 前缀；`_model()` 对未知模型抛错；请求显式携带选定 model。
- [ ] 运行 endpoint 与 DashScope Provider tests。
- [ ] Commit：`fix: resolve Alibaba regional image endpoints`。

---

### Task 3：阿里识图绑定 Regional/Workspace 与凭据

**Files:** Modify `webui/api/model_config.py`、`agent/auxiliary_client.py` 及测试。

- [ ] 写 RED：保存后含 `credential_ref/endpoint_mode/region/workspace_id/base_url`；Runtime OpenAI client 收到命名 Secret 和北京 URL；国际站不得被隐式使用。
- [ ] `set_vision_config()` 对 Alibaba 调用 `build_vision_base_url()`，不得再删除内置 Alibaba 的 `base_url`；内置模型必须在服务端列表中。
- [ ] `_resolve_task_provider_model()` 读取 `credential_ref`，调用 `resolve_api_key()`，继续走现有 `resolve_vision_provider_client()`。
- [ ] verification 指纹加入 credential ref、endpoint mode、region、workspace；共享 Key 轮换自动失效。
- [ ] 运行 `test_model_config_api.py -k vision` 和 `test_auxiliary_client.py -k vision`。
- [ ] Commit：`fix: bind Alibaba vision to regional credentials`。

反模式：探测必须 `strict_target=True`；端点失败不得 fallback 后显示成功；不新增平行 Alibaba HTTP 客户端。

---

### Task 4：生图绑定共享或独立凭据

**Files:** Modify DashScope 插件、`webui/api/model_config.py` 及测试。

- [ ] 写 RED：`alibaba-default` 可被 vision/image 同时引用；`alibaba-image` 更新不改变默认/识图/旧 `DASHSCOPE_API_KEY`。
- [ ] DashScope `is_available()` 与 `generate()` 通过 `resolve_api_key("dashscope", image_cfg.credential_ref)` 取 Key；无 ref 保持旧 fallback。
- [ ] `set_image_gen_config()` 只保存引用；选择“独立凭据”时先保存新凭据，不复制或覆盖共享 Secret。
- [ ] 共享凭据轮换使所有引用能力 verification 失效。
- [ ] 运行 DashScope 和 image credential tests。
- [ ] Commit：`feat: bind image generation to credential references`。

---

### Task 5：增加真实生图探测

**Files:** Modify `webui/api/model_config.py`、`webui/api/routes.py`、`tools/image_generation_tool.py` 及测试。

- [ ] 复制识图测试矩阵写 RED：未配置不调用、configured 未验证、成功不落盘图片/Secret、错误 Provider/model、无效文件、安全失败字段、配置变化失效、profile/并发 superseded、路由注册。
- [ ] 新增 `_ImageGenConfigSnapshot(profile, provider, model, credential_ref, endpoint_mode, region, workspace_id, base_url, configured, fingerprint)`。
- [ ] 状态固定为 `unconfigured/configured_unverified/verifying/verified/failed`；verification JSON 不含 prompt、图片路径、原始响应或 digest。
- [ ] 直接 `get_provider(snapshot.provider).generate()`，固定安全提示词、square、单图、选定模型。成功必须同时验证 result success、Provider/model 匹配、本地文件存在且文件头为 PNG/JPEG/WebP；随后删除探测图片。
- [ ] 注册 `POST /api/image-gen/test`。`is_available()` 只表示允许尝试，前端在 verified 前不得显示“可用”。
- [ ] 运行 model config image tests 和 image generation tool tests。
- [ ] Commit：`feat: add verified image generation probe`。

---

### Task 6：重构图片能力 UI

**Files:** Modify `static/index.html`、`static/panels.js`、`static/style.css`、`tests/test_model_config_frontend.py`。

- [ ] 写 RED：凭据区可见；两卡都有 credential/endpoint/region/workspace/preview/test；生图状态读取 verification；测试按钮有 aria-label，状态区 aria-live。
- [ ] 图片能力顶部增加“平台凭据”，显示 label、配置状态、使用范围、更新/独立凭据；不显示 Key 片段。
- [ ] 两卡字段顺序统一：凭据→接入方式→地域→Workspace/Base URL→模型→端点预览→保存→真实测试→状态。
- [ ] `workspace/custom/public` 动态显示字段；Region 使用下拉，不用自由文本。
- [ ] 复制识图 identity snapshot、busy、edit invalidation 和 stale response 防护到生图；测试生图文案明确“可能产生少量费用”。
- [ ] 字段错误用 `aria-describedby`；验证中 controls disabled/按钮 aria-busy；保存收起后焦点回到可见按钮；错误不能只靠 Toast 或颜色。
- [ ] Node 测试覆盖晚到响应、编辑失效、草稿保留、共享/独立切换、焦点恢复、endpoint preview announce。
- [ ] 运行 `pytest -q hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py`。
- [ ] Commit：`feat: unify image capability configuration UX`。

---

### Task 7：命名式自定义识图 Provider

**Files:** Create `agent/custom_vision_providers.py`; modify model config、routes、panels 及测试。

- [ ] 写 RED：两个自定义识图 Provider 使用不同 `TAIJI_VISION_CUSTOM_<ID>_API_KEY`；删除 active Provider 被拒；未知 transport 被拒。
- [ ] 复制 `custom_image_providers.py` 的 ID/URL/model/env/删除保护，但仅允许 `openai_chat_completions`、`anthropic_messages`。
- [ ] 增加 GET/POST/DELETE `/api/vision/custom-providers`。
- [ ] UI 明确显示兼容协议；不得写“任意平台”。原生协议必须走内置专用适配器。
- [ ] 运行 custom vision API/frontend tests。
- [ ] Commit：`feat: add named custom vision providers`。

---

### Task 8：统一 auth/transport schema 与 lazy migration

**Files:** Modify `provider_credentials.py`、`domestic_common.py`、model config、config sync 及测试。

- [ ] Schema 可表达 `api_key/bearer_token/access_key_secret/service_account/oauth/no_auth`；每个 Provider 明确 `provider_family/capabilities/auth_type/transport/credential_fields/endpoint_fields/models`。
- [ ] 前端只按 schema 渲染；OAuth/no-auth 显示不可编辑说明，不隐藏整行。
- [ ] 读取顺序固定：显式 ref→新默认凭据→旧 env。首次保存才补 ref；启动时不强制改写或删除旧字段。
- [ ] 如发行模板增加 `provider_credentials: []`，sync 只补缺失，绝不覆盖用户列表，模板不得包含 Secret。
- [ ] 测试六类 auth 字段、旧 payload、配置保留、模板无 Secret。
- [ ] Commit：`refactor: normalize provider authentication schemas`。

说明：schema 能表达某种 auth type 不等于每个平台已实现该适配器；产品只声明已实现并验证的平台/协议。

---

### Task 9：自动化、真实浏览器和百炼 E2E

**Files:** Create `docs/reviews/image-capability-provider-credentials-ux-qa-2026-07-15.md`。

- [ ] 运行 provider credentials、Alibaba endpoints、domestic providers、auxiliary vision、model config API/frontend、config sync 全部定向测试。
- [ ] 扫描硬编码 Secret、错误国际端点和未经验证的“图片生成可用”文案。
- [ ] 真实 Electron 桌面宽度与 375px 验证：入口可发现、共享/独立不丢草稿、字段/preview 正确、键盘/焦点、重复提交、持久错误、窄屏无溢出；保存截图。
- [ ] 使用用户授权测试凭据验证：同一北京 Key 的 Qwen-VL/Qwen-Image、错地域安全失败、共享 Key 轮换双状态失效、独立生图 Key 不影响识图。生图前提示可能计费。
- [ ] 没有真实 Key 时明确写“未验证”，不得写“通过”。
- [ ] 输出中文《前端 UX QA 报告》，含功能契约、P0-P3、证据、未验证项、风险。
- [ ] Commit：`test: verify image provider credential UX`。

---

### Task 10：发布门禁

- [ ] 运行 `git diff --check`、全部定向测试和项目相关全量回归；历史结果不能代替本轮输出。
- [ ] 确认同 Key 复用、独立 Key、北京/Workspace、显式模型、真实双探测、旧配置、命名 custom、schema、脱敏全部有测试对应。
- [ ] 不触碰任务开始前未跟踪路径：`.codex/`、`logs/`、`tools/demo_materials/`、`演示资料包/`、`oa-architecture.html`、`uv.lock`。
- [ ] 最终回复给出修改、验证、未验证、风险、下一步、所有 commit hash 和真实 `git status --short`。
- [ ] 没有真实百炼双能力证据时，只能说“代码和模拟测试通过”，不能说“百炼已正常可用”。

## 自检

- 需求覆盖：Key 复用、独立 Key、多平台字段、地域/Workspace、真实验证、兼容迁移、UX 均有任务。
- 类型统一：`credential_ref/provider_family/endpoint_mode/region/workspace_id/base_url/transport` 全程一致。
- 发布切片：Tasks 1-6 解决百炼可靠接入；Tasks 7-8 完成多平台扩展；Tasks 9-10 完成验收。
- 计划中没有要求调用不存在的旧 API；所有新 API 均明确列为待实现。

