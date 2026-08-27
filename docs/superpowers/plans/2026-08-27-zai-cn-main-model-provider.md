# 智谱 GLM（国内）主模型 Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增可直接选择的 `zai-cn` 主模型 Provider，固定使用智谱 BigModel 国内通用端点，并与国际版 `zai` 隔离凭据。

**Architecture:** 在现有 Provider 注册表中增加独立 `zai-cn` 条目，由注册表向运行时提供固定国内 Base URL、`GLM_CN_API_KEY` 和 OpenAI Chat 传输；WebUI 复用现有 Provider 下拉框、保存与连接检查链路，只补充显示、模型目录和密钥映射。保存后的配置保留 `provider: zai-cn`，因此刷新、连接检查和实际新会话共享同一身份与端点。

**Tech Stack:** Python 3.11、pytest、原生 JavaScript、Electron/WebUI、YAML 配置与本地 `.env` 凭据存储。

---

### Task 1: 用失败测试固定 Agent Provider 契约

**Files:**
- Modify: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_api_key_providers.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_model_validation.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_runtime_provider_resolution.py`

- [x] **Step 1: 写 Provider 注册失败测试**

新增断言：`PROVIDER_REGISTRY["zai-cn"]` 的名称为“智谱 GLM（国内）”，认证为 `api_key`，端点为 `https://open.bigmodel.cn/api/paas/v4`，密钥变量仅为 `("GLM_CN_API_KEY",)`，且没有可覆盖 Base URL 的环境变量。

- [x] **Step 2: 写模型目录和运行路由失败测试**

断言 `provider_model_ids("zai-cn")` 包含并默认优先返回 `glm-5`；调用 `resolve_runtime_provider(requested="zai-cn")` 时读取 `GLM_CN_API_KEY`，返回 `chat_completions` 和固定国内端点，不读取 `GLM_API_KEY` 或 `GLM_BASE_URL`。

- [x] **Step 3: 运行测试并确认 RED**

Run:

```bash
hermes-local-lab/sources/hermes-agent/venv/bin/python -m pytest \
  hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_api_key_providers.py \
  hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_model_validation.py \
  hermes-local-lab/sources/hermes-agent/tests/hermes_cli/test_runtime_provider_resolution.py -q
```

Expected: 仅新增的 `zai-cn` 断言因 Provider 尚未注册而失败。

### Task 2: 最小实现 Agent Provider 与运行时路由

**Files:**
- Modify: `hermes-local-lab/sources/hermes-agent/hermes_cli/auth.py`
- Modify: `hermes-local-lab/sources/hermes-agent/hermes_cli/providers.py`
- Modify: `hermes-local-lab/sources/hermes-agent/hermes_cli/models.py`
- Modify: `hermes-local-lab/sources/hermes-agent/agent/provider_credentials.py`
- Modify: `hermes-local-lab/sources/hermes-agent/agent/model_metadata.py`

- [x] **Step 1: 注册固定国内 Provider**

在 `PROVIDER_REGISTRY` 增加：

```python
"zai-cn": ProviderConfig(
    id="zai-cn",
    name="智谱 GLM（国内）",
    auth_type="api_key",
    inference_base_url="https://open.bigmodel.cn/api/paas/v4",
    api_key_env_vars=("GLM_CN_API_KEY",),
),
```

不为它调用 `_resolve_zai_base_url()`，避免自动探测到国际或 Coding Plan 端点。

- [x] **Step 2: 注册传输、目录和显示名**

为 `zai-cn` 增加 `openai_chat` overlay；模型目录首项固定 `glm-5`；Canonical Provider 显示名为“智谱 GLM（国内）”；将 `zhipu-cn`、`glm-cn` 作为别名；将 `zai-cn` 加入模型前缀识别集合。

- [x] **Step 3: 隔离凭据家族**

将 `zai-cn` 映射为独立 `zhipu_cn` 家族，并只允许 `GLM_CN_API_KEY` 作为兼容凭据来源，防止与国际版 `zai` 共用默认密钥。

- [x] **Step 4: 运行 Agent 聚焦测试并确认 GREEN**

重复 Task 1 的 pytest 命令。Expected: 新增测试全部通过，既有测试无回归。

### Task 3: 用失败测试固定 WebUI 选择、保存与检查契约

**Files:**
- Create: `hermes-local-lab/sources/hermes-webui/tests/test_zai_cn_main_model_provider.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py`

- [x] **Step 1: 写 WebUI 后端失败测试**

覆盖以下行为：Provider 元数据返回 `id=zai-cn`、标签“智谱 GLM（国内）”、首个模型 `glm-5`；保存 API Key 只写 `GLM_CN_API_KEY`；配置持久化为 `provider: zai-cn`；连接检查解析出的 Base URL 为国内端点。

- [x] **Step 2: 写前端失败测试**

复用 `_syncMainModelConfigControls()` 的 Node 驱动，断言从其他 Provider 切换到 `zai-cn` 时模型变为 `glm-5`、旧 Key 草稿被清空、Base URL 行保持隐藏，且 Provider 提示说明“使用智谱 BigModel 国内通用 API”。

- [x] **Step 3: 运行测试并确认 RED**

Run:

```bash
hermes-local-lab/sources/hermes-agent/venv/bin/python -m pytest \
  hermes-local-lab/sources/hermes-webui/tests/test_zai_cn_main_model_provider.py \
  hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py -q
```

Expected: 新增 `zai-cn` 元数据、密钥映射或提示断言失败。

### Task 4: 最小实现 WebUI Provider

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/api/config.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/providers.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/profiles.py`
- Modify: `hermes-local-lab/sources/hermes-webui/static/panels.js`

- [x] **Step 1: 增加显示、目录和密钥映射**

在 WebUI 注册：

```python
"zai-cn": "智谱 GLM（国内）"
"zai-cn": "GLM_CN_API_KEY"
"zai-cn": [
    {"id": "glm-5", "label": "GLM-5"},
    ...
]
```

模型目录仅包含国内通用 API 当前支持的文本模型，不包含 Coding Plan 专属项。

- [x] **Step 2: 增加可操作提示**

在 `_syncMainModelConfigControls()` 中为 `zai-cn` 显示“使用智谱 BigModel 国内通用 API；请填写国内平台 API Key”，不展示 Base URL，不改变其他 Provider 提示。

- [x] **Step 3: 运行 WebUI 聚焦测试并确认 GREEN**

重复 Task 3 的 pytest 命令。Expected: 全部通过。

### Task 5: 回归验证与真实 Electron UX QA

**Files:**
- Create: `docs/reviews/zai-cn-main-model-provider-ux-qa-2026-08-27.md`

- [x] **Step 1: 运行相关自动化门禁**

Run:

```bash
hermes-local-lab/sources/hermes-agent/venv/bin/python -m pytest hermes-local-lab/sources/hermes-agent/tests/hermes_cli -q
hermes-local-lab/sources/hermes-agent/venv/bin/python -m pytest hermes-local-lab/sources/hermes-webui/tests -q
node --check hermes-local-lab/sources/hermes-webui/static/panels.js
scripts/check-local-change-safety.sh
git diff --check
```

Expected: 命令退出码均为 0；若完整门禁出现与本次无关的既有失败，必须单独列出，不得宣称完整通过。

- [x] **Step 2: 以开发模式重启当前源码**

使用项目启动器的 `TAIJI_SOURCE_MODE=development`，确认 Electron 窗口和 `/health` 可用。

- [x] **Step 3: 完成真实用户路径（不保存凭据）**

在“设置 → 模型配置”中验证：下拉选项可发现；切换后自动填入 `glm-5`；Base URL 不出现；国内 API 提示可见。为避免改变用户当前生效配置，桌面验收不填写或保存凭据；保存、刷新、连接材料和鉴权失败反馈由隔离自动化覆盖。不得使用用户真实 Key 作为自动化夹具。

- [x] **Step 4: 输出中文《前端 UX QA 报告》**

按项目模板记录功能契约、真实浏览器/桌面证据、截图、可访问性、状态反馈、P0-P3、未验证项和剩余风险。

### Task 6: 标准收尾

**Files:**
- Stage only files owned by this feature plus previously approved overlapping main-model switch files.

- [x] **Step 1: 运行完整项目验证**

Run: `scripts/verify.sh --full`

Expected: 全部门禁通过；若失败则停在本地验证状态，不提交实现或推送。

- [x] **Step 2: 精确暂存并审核 cached diff**

检查 `git status --short`、未暂存 diff、`git diff --cached --name-status`、`git diff --cached --check` 和完整 cached diff，确认不包含两份图片能力 Figma 文档。

- [ ] **Step 3: 提交并同步 main**

完整验证和最终审核通过后提交 `feat: add domestic GLM provider`，刷新远端、确认快进后正常推送 `main`；不创建 Tag、Release 或安装包。
