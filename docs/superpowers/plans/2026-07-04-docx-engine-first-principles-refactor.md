# 文档模板渲染引擎第一性原理重构计划

## 目标模型

文档模板渲染引擎不是“把 Markdown 跑进某个脚本生成 DOCX”，而是一个可验证、可追踪、可产品化的文档交付流水线：

1. 用户先选择模板，再进入内容生成和渲染。
2. 输入源被规范化为 SourcePackage，模板被解析为 TemplatePackage，资产被打包为 AssetPackage。
3. 引擎生成 RenderPlan，并用同一个作业状态机推进 normalize -> template -> assets -> plan -> render -> validate -> deliver。
4. 最终交付包必须包含 document.docx、原始 source、assets、render-plan、quality-report、template manifest 和完整 job manifest。
5. WPS/Word 视觉验收是显式质量门，不用“生成成功”冒充最终验收。

## 当前主要差距

1. `DocumentJob` 状态机已存在，但 `src/cli/run-job.js` 仍直接串联所有步骤，核心执行链没有真正使用领域模型。
2. `job.manifest.json` 只有最小字段，缺少 createdAt、workspace、inputs、outputs、warnings、failures 等可追踪信息。
3. 失败路径主要靠抛错和 CLI exit code 表达，缺少可供 WebUI/API 复用的结构化作业失败结果。
4. WebUI 中仍并存 v2 工作台和旧 `docx-template` 调整链，存在两套入口、两套路由、两套错误语义。
5. 模板注册表已经预留 installed 区域，但当前只加载 builtin，模板安装/治理还没有进入产品闭环。

## 重构切片

1. 抽出核心 `runDocumentJob` 作业流水线，让 CLI 只负责参数和退出码。
2. 让成功和失败都返回结构化 job 结果，并写入完整 job manifest。
3. 收口 WebUI 后端旧接口为 v2 引擎兼容层，避免继续依赖旧 skill 脚本链。
4. 补齐 installed 模板注册读取和测试。
5. 最后再做真实浏览器/WPS 侧验收，明确自动化通过与人工未验证边界。

## 已推进进展

- 已抽出 `runDocumentJob` 统一作业流水线，CLI 退回参数适配层。
- 已让交付包写入完整 `job.manifest.json`，并保持 WPS/Word 视觉验收为显式 `not_verified` gate。
- 已把 WebUI 旧图片调整接口收敛为 v2 服务兼容层，不再执行旧 `docx-template-skill` 脚本。
- 已支持 registry 同时读取 `builtin` 和 `installed`，并拒绝重复模板 ID。
- 已补齐模板安装器：外部模板包必须通过校验后才会复制到 `installed/<templateId>` 并写入 registry；copyable skill 也暴露 `scripts/install-template.js`。
- 已支持显式更新已安装模板：用户必须传 `--replace` 或在工作台勾选“覆盖已安装模板”，内置模板仍拒绝覆盖，避免把模板维护能力变成隐式破坏操作。
