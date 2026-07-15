# 图片能力 Provider、凭据与端点解耦设计

## 1. 目标

让太极智能体同时满足两类场景：

1. 阿里云百炼同一业务空间的一份 API Key，可被识图和生图复用；识图与生图仍分别选择模型和接口。
2. 不同平台、同一平台不同业务空间或不同用途的专用 Key，可以并存且不会互相覆盖。

本设计只处理图片理解与图片生成配置链路，不改主聊天模型选择逻辑，不引入云端凭据同步。

## 2. 第一性原理

一次模型调用至少由四个独立维度决定：

- `provider/transport`：调用哪家平台、使用哪种协议。
- `credential_ref`：使用哪份鉴权凭据。
- `endpoint context`：地域、业务空间、Base URL、计费方案。
- `model`：明确调用哪个模型。

API Key 只负责鉴权，不能承担模型或能力自动路由。相同 Provider 的同一份凭据可以被多个能力引用；不同 Provider 的凭据默认隔离。

## 3. 当前根因

### 3.1 阿里识图地域错误风险

内置 `alibaba` Provider 默认使用国际端点，而 WebUI 对内置 Provider 隐藏并在保存时删除 `base_url`。华北 2 北京的 Key 因此无法可靠绑定北京公共端点或业务空间专属端点。

### 3.2 共享环境变量产生静默覆盖

识图 `alibaba`、生图 `dashscope` 和主模型都可能写入 `DASHSCOPE_API_KEY`。复用同一 Key 时可工作，但任何一个界面更新 Key 都会改变其他能力正在使用的凭据。

### 3.3 生图“可用”不等于真实可用

生图 `available` 仅检查配置字段或 `provider.is_available()`，未验证 Key、地域、端点、模型权限和真实图片返回。

### 3.4 自定义平台的兼容边界不明确

当前自定义识图只有一个全局 Key；自定义生图只实现 OpenAI Images 协议。仅粘贴某个平台专用 Key，不代表协议、签名和响应格式兼容。

## 4. 目标配置模型

```yaml
provider_credentials:
  - id: alibaba-default
    provider_family: alibaba_dashscope
    label: 阿里云百炼默认凭据
    auth_type: api_key
    secret_env: TAIJI_CREDENTIAL_ALIBABA_DEFAULT_API_KEY

auxiliary:
  vision:
    provider: alibaba
    credential_ref: alibaba-default
    endpoint_mode: workspace
    region: cn-beijing
    workspace_id: llm-example
    base_url: https://llm-example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
    model: qwen3-vl-plus

image_gen:
  provider: dashscope
  credential_ref: alibaba-default
  endpoint_mode: workspace
  model: qwen-image-2.0-pro
  options:
    region: cn-beijing
    workspace_id: llm-example
    base_url: https://llm-example.cn-beijing.maas.aliyuncs.com
```

配置文件只保存凭据元数据和环境变量名，不保存明文 Secret。明文继续保存在本机 `$HERMES_HOME/.env`。

## 5. 凭据解析规则

1. 配置存在 `credential_ref`：从 `provider_credentials` 找到对应凭据并读取其 `secret_env`。
2. 未设置 `credential_ref`：按 Provider 的旧环境变量读取，例如 `DASHSCOPE_API_KEY`。
3. 新配置默认创建或复用 `alibaba-default`。
4. 用户选择“独立凭据”时创建 `alibaba-vision` 或 `alibaba-image`，不覆盖默认凭据。
5. 不做一次性破坏性迁移；旧配置采用 lazy fallback，首次保存时再写入新引用。
6. API 响应只返回 `configured/source/credential_ref`，绝不返回 Secret、Secret 摘要或可反推信息。

## 6. Provider Schema

统一 Provider 公共字段：

```python
{
    "provider_family": "alibaba_dashscope",
    "capabilities": ["vision", "image_generation"],
    "auth_types": ["api_key"],
    "transport": "openai_chat_completions",
    "endpoint_modes": ["public", "workspace", "custom"],
    "supported_regions": ["cn-beijing", "ap-southeast-1"],
    "credential_fields": [...],
    "endpoint_fields": [...],
    "models": [...],
}
```

首期内置实现支持：

- `api_key`
- `bearer_token`
- `access_key_secret`
- `oauth`
- `service_account`
- `no_auth`

协议支持由适配器显式声明。自定义入口只承诺其标注的兼容协议，不承诺任意平台通用。

## 7. 阿里百炼端点规则

### 7.1 识图

- 北京公共兼容地址：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- 北京业务空间专属地址：`https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`
- 新加坡公共兼容地址：`https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- 自定义模式：保存用户提供的 HTTPS Base URL。

### 7.2 生图

- 业务空间根地址：`https://{WorkspaceId}.{Region}.maas.aliyuncs.com`
- 同步生图路径：`/api/v1/services/aigc/multimodal-generation/generation`
- UI 允许粘贴完整根地址，不猜测 Workspace 必须以 `ws-` 开头。

### 7.3 计费方案

Provider schema 保留 `billing_mode`/`endpoint_mode`，用于约束按量付费、Token Plan、Coding Plan 与对应 Base URL。首期图片能力只开放已由官方接口和真实探测验证的组合，不通过 Key 前缀静默猜测能力。

## 8. 验证状态机

识图和生图统一采用：

```text
unconfigured
configured_unverified
verifying
verified
failed
```

验证指纹必须包含：

- profile
- provider
- credential_ref
- Secret SHA-256（仅用于本机状态匹配，不对外返回）
- endpoint mode
- region
- workspace/base URL
- transport
- model

配置在验证期间发生变化时，本次结果返回 `configured_unverified`，不得覆盖新配置的状态。

生图探测使用固定安全提示词、最低可用尺寸和单图输出；按钮明确提示会产生少量外部调用费用。成功条件不是 HTTP 200，而是返回并成功保存一张真实图片。

## 9. 目标 UI

“图片能力”顶部增加“平台凭据”区：

```text
阿里云百炼默认凭据  已保存
使用范围：识图、生图
[更新凭据] [新增独立凭据]
```

识图、生图能力卡只绑定：

- 平台凭据
- 接入方式
- 地域
- Workspace/Base URL
- 模型
- 保存
- 真实测试
- 验证状态与字段级错误

保存后焦点回到卡片标题或状态区；错误通过持久状态和 `aria-describedby` 关联字段，Toast 只作补充。

## 10. 错误与安全契约

- 禁止在日志、API、验证状态文件、诊断 ID 和前端数据中回显 Secret。
- 禁止把未知内置模型静默替换为默认模型。
- 内置 Provider 的模型必须在服务端允许列表中；自定义 Provider 可手填并标记“自定义模型 ID”。
- URL 仅允许 `https`；开发环境的自定义本地 Provider 可显式允许 loopback HTTP。
- 错误码至少区分：`credential_missing`、`endpoint_invalid`、`region_mismatch`、`model_not_allowed`、`authentication_failed`、`rate_limited`、`provider_unavailable`、`probe_superseded`。
- 修改被多个能力引用的凭据前显示影响范围；保存成功后所有引用该凭据的验证状态立即失效。

## 11. 兼容与发布策略

发布分两条可独立验收的切片：

1. 阿里百炼可靠接入：凭据档案、北京/Workspace 端点、识图和生图真实探测、旧配置 fallback。
2. 多平台扩展：识图多实例 Provider、统一 auth/transport schema、专用适配器契约。

切片 1 完成即可解决当前用户问题；切片 2 完成后才能宣称支持“各平台专用 Key”。

## 12. 非目标

- 不将本地凭据上传云端。
- 不允许 API Key 自动选择模型。
- 不在本次重构主聊天 Provider 配置。
- 不一次性重写全部历史图片 Provider。
- 不把“字段齐全”表述为“真实可用”。

