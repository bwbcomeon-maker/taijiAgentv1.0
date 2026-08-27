# 智谱 GLM（国内）主模型 Provider 设计

## 目标

在“设置 → 模型配置”的 Provider 下拉框中新增独立选项“智谱 GLM（国内） · zai-cn”。用户选择后只需填写智谱 BigModel 国内平台签发的 API Key，即可保存、检查连接并用于新会话。

## 范围

- 仅新增主模型 Provider `zai-cn`。
- 固定使用国内通用 API 端点 `https://open.bigmodel.cn/api/paas/v4`。
- 默认模型为 `glm-5`，并提供与国内通用 API 相符的 GLM 模型目录。
- 使用独立凭据环境变量 `GLM_CN_API_KEY`，避免覆盖现有国际版 `zai` 凭据。
- 不包含 Coding Plan 专属端点，不调整图片、语音或辅助模型能力。
- 不改变现有 `Z.AI / GLM · zai` 及其他 Provider 的行为。

## 用户交互

1. 用户在 Provider 下拉框选择“智谱 GLM（国内） · zai-cn”。
2. 模型字段自动切换为 `glm-5`。
3. Base URL 不展示，也不要求用户填写；系统使用固定国内端点。
4. 用户粘贴完整 API Key，点击“保存主模型配置”。
5. 保存成功后刷新页面，Provider、模型和凭据配置状态保持一致。
6. 用户点击“检查连接”时，系统使用国内端点验证；后续新会话使用同一路由。

## 技术设计

- 在 Agent Provider 注册、模型目录、凭据映射和传输配置中注册 `zai-cn`。
- `zai-cn` 使用 OpenAI Chat 兼容传输，默认 Base URL 固定为国内通用端点。
- WebUI Provider 元数据展示国内标签、默认模型和 API-Key 鉴权方式。
- 主模型保存逻辑在切换到 `zai-cn` 时清除前一个 Provider 遗留的 Base URL/API mode，并由 Provider 默认值决定国内端点。
- 主模型连接检查从已提交配置解析 `zai-cn` 的固定端点，不根据 Key 内容猜测区域。

## 错误与状态

- 未填写 Key：显示“凭据未配置”。
- 国内端点拒绝 Key：显示“API Key 无效或已失效”。
- 连接成功：显示“连接已验证”。
- 配置完成但尚未检查：显示“已配置，尚未验证”。
- 不回显完整 API Key，不把密钥写入日志或前端响应。

## 验证标准

1. Provider 下拉框存在且可键盘选择“智谱 GLM（国内） · zai-cn”。
2. 选择后模型自动设置为 `glm-5`，API Key 草稿不会沿用其他 Provider。
3. 保存后配置持久化为 `provider: zai-cn`，刷新仍正确选中。
4. 连接检查请求命中国内通用端点，不命中 `api.z.ai`。
5. 实际新会话的 Provider、模型和端点与检查连接一致。
6. 国内和国际 GLM 凭据互不覆盖。
7. 相关单元测试先红后绿，WebUI 自动化门禁通过。
8. 在真实 Electron 页面完成选择、填写测试 Key、保存、刷新和错误反馈检查；不使用或记录用户真实密钥作为测试夹具。
