# 前端 UX QA 报告：智谱 GLM（国内）主模型 Provider

## 结论

新增的“智谱 GLM（国内）”主模型 Provider 已在开发模式 Electron 中真实可见，可从现有主模型配置入口选择；选择后默认模型为 `glm-5`，仅显示 API Key，不显示 Base URL，并明确提示使用智谱 BigModel 国内通用 API。未发现本次范围内的 P0–P3 问题。

## 功能契约

- 入口：设置 → 模型配置 → 修改主模型配置。
- Provider：`zai-cn`，显示名“智谱 GLM（国内）”。
- 默认模型：`glm-5`。
- 国内端点：固定为 `https://open.bigmodel.cn/api/paas/v4`，不暴露可编辑 Base URL。
- 凭据：仅使用 `GLM_CN_API_KEY`，不复用国际版 `zai` 的密钥。
- 非范围：图片、语音、Coding Plan、其他 Provider 均不改动。

## UX 核验

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 可发现性 | 通过 | 开发模式 Electron 的既有“修改主模型配置”入口中可直接选择“智谱 GLM（国内）”。 |
| 默认值 | 通过 | 切换后模型自动显示 `glm-5`。 |
| 表单简洁性 | 通过 | 表单只显示 Provider、模型和 API Key；Base URL 行未出现。 |
| 操作提示 | 通过 | 页面显示“使用智谱 BigModel 国内通用 API；请填写国内平台 API Key。” |
| 密钥安全 | 通过 | 桌面验收未填写或保存真实密钥；自动化仅使用隔离测试值。 |
| 键盘与可访问性 | 无回归 | 继续使用原生 Provider/模型选择控件和既有保存按钮，没有新增覆盖层或仅鼠标入口。 |
| 视觉布局 | 通过 | 1600 级桌面窗口中字段、提示和保存按钮均在主卡片内，无遮挡或横向溢出。 |
| 错误状态 | 通过（自动化） | 连接检查使用国内端点材料，既有失败状态仍提供明确鉴权反馈。未用真实密钥发起联网请求。 |

## 当前实时验证

- Agent Provider 聚焦契约：385 项通过。
- 国内 GLM、主模型切换和前端交互相关测试：103 项通过。
- 新增 WebUI 国内 GLM 契约：4 项通过（元数据、密钥保存、国内连接材料、前端提示）。
- 完整项目门禁 `scripts/verify.sh --full`：`verification: PASS`。
- 根测试 1296 项通过、2 项跳过；Desktop 68 项、DOCX 276 项、Agent 205 项、WebUI 692 项通过。
- 开发模式 Electron：窗口可见，本地 `/health` 可用。
- Electron 截图证据：`/private/tmp/taiji-zai-cn-form-proof.png`。
- `scripts/check-local-change-safety.py`：通过；既有历史凭据样式不再重复误报，新增或替换敏感值仍由回归测试确认会被拦截。
- `git diff --check`：通过。

## 探索性检查

- 从已有国际版 `zai / glm-5` 打开编辑表单，再切换到国内 Provider：草稿 Provider 与模型正确联动，当前生效摘要保持未保存状态，没有误改运行配置。
- 在已有“API Key 无效”状态下打开编辑表单：新增 Provider 和提示仍可见，错误卡片没有遮挡表单入口或保存按钮。

## 未验证与边界

- 用户真实智谱 API Key 的在线连接和真实对话：未执行；本次不读取、不复用用户真实密钥。
- 安装态、制品态和发布态：未验证。
- 完整门禁只证明当前源码开发线；不等同于安装包、正式 Tag、Release 或目标机验收。
