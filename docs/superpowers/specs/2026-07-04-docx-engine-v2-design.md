# DOCX Engine V2 重构设计

## 目标

这次重构要做的是一个“文档生产引擎”，不是再补一个 DOCX 渲染脚本。

目标结果是：用户已有对话成果、Markdown、文本或 DOCX 后，引擎能够把它归一化成稳定的文档作业，绑定用户选择的模板和资产包，生成 DOCX，输出质量报告，并交付一个可检查、可调整、可重新生成的完整文档包。普通用户应能在 Taiji 桌面端完成主要流程，并能用 WPS/Word 打开和继续处理最终文档。

现有脚本只能作为可复用材料。新架构必须由目标功能反推，不能由旧脚本决定边界。

## 当前证据

本设计基于 2026-07-04 的当前状态检查。

- 当前模板引擎文件主要在 `/Users/bwb/Documents/工作/文档模板渲染引擎`。
- 该目录不是 git 仓库。
- 主仓库 `/Users/bwb/Documents/工作/taiji-agentv1.0` 当前已有未提交改动。
- `carbone` 与 `docx-template-skill/renderer` 存在核心代码复制。
- `apply-template.js`、`docx-image-inserter.js`、`normalize-source.js` 在两处内容一致。
- 当前最大脚本已经承担过多职责：
  - `docx-image-inserter.js`：879 行。
  - `apply-template.js`：653 行。
  - `package-rich-draft.js`：481 行。
- `docx-template-skill/renderer/package.json` 仍声明为 `carbone`，产品身份没有从早期 POC 收口。
- 现有测试能证明很多脚本级能力，但当前架构仍是“脚本链 + 文件约定 + 运行态同步”，不是统一的文档作业引擎。

## 第一性原理

文档模板引擎的基本职责只有五个：

1. 保留用户内容。
2. 保留文档结构。
3. 保留资产和可编辑性。
4. 通过用户选择的模板应用版式。
5. 证明最终交付物可用。

任何削弱这五件事的实现，即使能让当前测试通过，也不符合本次重构目标。

## 非目标

- 不先做大范围 Taiji 桌面 UI 重设计；必须先定义引擎契约。
- 不继续把现有大脚本修得更复杂。
- 不允许模板套用阶段凭空补方案内容、表格或图。
- 不把聊天文本、stdout 或临时路径当作长期契约。
- 不把 WPS/Word 视觉正确性当作可选项。

## 最终交付契约

每次成功运行必须输出一个完整交付包，而不是只输出单个 DOCX。

交付包至少包含：

```text
delivery/
  document.docx
  source.md
  assets/
  job.manifest.json
  template.manifest.json
  render-plan.json
  quality-report.json
  README-图片调整说明.md
```

`document.docx` 是交付包的一部分，不是全部交付。

## 核心领域模型

### DocumentJob

一次文档生产任务。

必备字段：

- `jobId`
- `createdAt`
- `sourceRef`
- `templateId`
- `status`
- `workspace`
- `inputs`
- `outputs`
- `warnings`
- `failures`

允许状态：

- `created`
- `source_normalized`
- `template_selected`
- `assets_packaged`
- `render_planned`
- `rendered`
- `validated`
- `delivered`
- `failed`

### SourcePackage

归一化后的输入内容。

负责保存：

- 文档标题。
- 有序内容块。
- 章节。
- 段落。
- 表格。
- 图示引用。
- 图片引用。
- 源文锚点。
- DOCX 来源里的嵌入媒体。

它只描述源内容，不包含模板排版决策。

### TemplatePackage

可复用模板包。

必备文件：

- `manifest.json`
- `template.docx`
- `schema.json`
- `prompt.md`
- `sample.json`

manifest 必备字段：

- `id`
- `name`
- `version`
- `description`
- `documentTypes`
- `capabilities`
- `requiredAssets`
- `qualityGates`
- `compatibility`

模板包声明自己能渲染什么，引擎在渲染前判断输入是否满足模板要求。

### AssetPackage

文档资产包。

负责保存：

- `figureId`
- `tableId`
- 图题或表题。
- 锚点。
- 源类型。
- 可编辑源文件。
- 展示文件。
- 尺寸。
- 质量元数据。
- 替换历史。

图片和图示是一等对象，不能只是 schema 里的一个路径字段。

### RenderPlan

确定性渲染计划。

负责保存：

- 输出章节顺序。
- 段落块。
- 表格落位。
- 图片/图示落位。
- caption 策略。
- 图目录/表目录策略。
- 资产到模板的绑定关系。

RenderPlan 必须在生成 DOCX 之前创建。DOCX XML 后处理只能执行 RenderPlan，不能靠图题、caption 或正文模糊猜测。

### RenderResult

具体渲染结果。

负责保存：

- 原始渲染 DOCX。
- 后处理 DOCX。
- 已插入资产。
- 已更新 relationships。
- XML 证据片段。
- 渲染警告。

### ValidationReport

质量门禁报告。

必备检查：

- 输入完整性。
- 模板兼容性。
- schema 校验。
- 资产覆盖。
- 图片和表格落位。
- caption 和目录。
- 占位符残留。
- DOCX 包完整性。
- 用户可编辑性。
- WPS/Word 视觉验收状态。

允许状态：

- `passed`
- `passed_with_warnings`
- `failed`
- `not_verified`

### DeliveryPackage

最终用户交付包。

它是产品输出，也是后续调图、重新生成和追溯问题的依据。

## 新目录结构

新引擎应进入主仓库，成为可版本化、可测试、可提交的源码。

```text
hermes-local-lab/sources/docx-engine-v2/
  package.json
  src/
    domain/
      document-job.js
      source-package.js
      template-package.js
      asset-package.js
      render-plan.js
      validation-report.js
      delivery-package.js
    source/
      normalize-markdown.js
      normalize-text.js
      normalize-docx.js
      extract-docx-media.js
    templates/
      registry.js
      validate-template-package.js
      install-template-package.js
    assets/
      package-assets.js
      render-figure-asset.js
      replace-docx-asset.js
      inspect-asset-quality.js
    planning/
      build-render-plan.js
      map-source-to-template.js
    rendering/
      render-docx.js
      postprocess-docx.js
      write-docx-relationships.js
    validation/
      validate-job.js
      validate-render-result.js
      validate-delivery-package.js
    delivery/
      write-delivery-package.js
      summarize-delivery.js
    cli/
      run-job.js
      inspect-job.js
      install-template.js
      replace-asset.js
  templates/
    general-proposal/
    meeting-minutes/
  tests/
```

`docx-template-skill` 变成分发壳和兼容壳：

```text
docx-template-skill/
  SKILL.md
  skill.json
  scripts/
    run-job.js
    apply-template.js
    package-rich-draft.js
    render-figure-assets.js
    replace-docx-image.js
```

兼容脚本只调用 v2 引擎，不再拥有业务逻辑。

## 主流程

### 1. 创建作业

输入：

- 来源文件、消息或产物引用。
- 模板 id，或缺失的模板 id。
- 输出目录。

输出：

- `DocumentJob`，状态为 `created`。

如果模板 id 缺失，引擎返回 `template_selection_required` 和模板列表，不生成 JSON，也不渲染 DOCX。

### 2. 归一化来源

引擎生成 `SourcePackage`。

Markdown 来源必须保留：

- 标题和章节。
- 有序块。
- 表格。
- Mermaid。
- 图片引用。
- 锚点文本。

DOCX 来源必须保留：

- 段落。
- 表格。
- 嵌入图片。
- 图片附近正文锚点。
- 已有 `figureId` 元数据。

纯文本来源只能生成段落。若用户选择富方案模板，必须先回到富内容初稿阶段补齐表格和图示。

### 3. 解析模板

引擎从注册表解析 `TemplatePackage`。

校验包括：

- 必备文件存在。
- manifest id 与注册表一致。
- schema 能校验 sample。
- template 能渲染 sample。
- 模板能力声明明确。

### 4. 打包资产

引擎生成或读取 `AssetPackage`。

规则：

- 已合格展示图必须保留。
- Mermaid 源必须保留为可编辑源。
- 输出目录非空时拒写，避免旧资产污染。
- 每张图必须有稳定 `figureId`。
- 每张表必须有稳定 `tableId`。
- 资产质量不合格时，渲染前失败。

### 5. 生成 RenderPlan

RenderPlan 的规则：

- 输出顺序跟随源文顺序。
- 表格和图片留在原章节附近。
- 必填图片不能 fallback 到文末。
- 锚点缺失时渲染前失败。
- 模板 JSON 从 RenderPlan 生成，而不是从脚本里临时拼。

### 6. 渲染 DOCX

渲染输入：

- 模板 DOCX。
- 模板数据。
- 资产绑定。
- caption 策略。
- 目录策略。

DOCX XML 后处理必须以 RenderPlan 为依据。

### 7. 验证

引擎生成 `quality-report.json`。

报告必须明确区分：

- 通过。
- 失败。
- 带风险通过。
- 未验证。

必备门禁：

- schema 校验。
- DOCX zip 完整性。
- 无 `{d.` 占位符残留。
- 图片覆盖。
- 表格覆盖。
- `figureId` 元数据覆盖。
- 锚点落位。
- 图目录/表目录可读。
- 交付包完整性。

WPS/Word 视觉验收必须单独标记。没有打开过时只能写 `not_verified`。

### 8. 交付

宿主返回：

- DOCX 路径。
- 交付包路径。
- 质量报告状态。
- 用户可理解的风险。
- 下一步可用操作。

## Taiji 桌面 UX 契约

Taiji 必须提供可见文档工作流，不能只暴露隐藏命令或脚本。

最小可见状态：

- 选择模板。
- 生成文档包。
- 查看质量报告。
- 打开 DOCX。
- 打开交付目录。
- 调整图片。
- 重渲染指定图。
- 替换 DOCX 图片。
- 从源包重新生成。
- 旧 DOCX 缺 `figureId` 时给出恢复路径。

任何后端已实现但桌面端没有可见、可发现、可访问入口的能力，都是 P1。

## 迁移策略

### Phase 1：契约先行

先为以下对象建立 schema 和失败测试：

- `DocumentJob`
- `SourcePackage`
- `TemplatePackage`
- `AssetPackage`
- `RenderPlan`
- `ValidationReport`
- `DeliveryPackage`

这些测试应能证明当前脚本链不满足新契约。

### Phase 2：引擎骨架

创建 `docx-engine-v2` 的领域模块和 CLI。

第一条可运行命令：

```bash
node src/cli/run-job.js --template-id general-proposal --source <source.md> --out-dir <delivery-dir>
```

成功标准是完整交付包存在，而不是只出现 DOCX。

### Phase 3：模板迁移

迁移 `general-proposal` 和 `meeting-minutes`。

兼容要求：

- 旧 self-test 能继续产出 DOCX。
- 新交付包契约比旧输出契约更强。

### Phase 4：资产生命周期

把富内容初稿打包、Mermaid 重渲染、`figureId` 替换迁入 v2 资产模块。

旧脚本保留，但只作为薄兼容入口。

### Phase 5：Taiji 接入

Taiji API 调用 v2 job 入口。

桌面端升级为文档工作台，并暴露完整文档工作流。

### Phase 6：移除重复

v2 测试和兼容脚本通过后，移除或冻结 `carbone` 与 `docx-template-skill/renderer` 的重复核心。

旧 POC 可以保留为历史 fixture，但不能再是活动引擎。

## 验收标准

只有当前证据证明以下全部成立，才能说重构完成：

- `docx-engine-v2` 存在于 git 跟踪源码中。
- 核心领域 schema 存在并有测试。
- `general-proposal` 和 `meeting-minutes` 通过 v2 跑通。
- 富 Markdown 来源能生成完整交付包。
- DOCX 来源中的嵌入图片能保留引用。
- 缺少模板选择时渲染前停止。
- 缺少富内容资产时渲染前失败。
- 旧 DOCX 缺 `figureId` 时替图安全失败。
- 含 `figureId` 的 DOCX 能精确替图。
- 每次运行都生成 `quality-report.json`。
- 警告和未验证项明确写出。
- 兼容脚本调用 v2，不再承载业务逻辑。
- Taiji 桌面端有可见入口。
- 自动化测试通过。
- 运行态 skill 自检通过。
- 至少一个生成 DOCX 已用 WPS/Word 打开目视验收。

## 验证计划

自动化验证：

- `node --test hermes-local-lab/sources/docx-engine-v2/tests/*.test.js`
- 更新后的 `docx-template-skill` 包测试。
- 更新后的 Taiji WebUI pytest。
- `npm run lint:runtime`。
- 重建 skill zip 后做完整性检查。

运行态验证：

- 同步或安装重构后的 skill 到 runtime home。
- 在已安装 skill 目录运行 self-test。
- 在 Taiji Desktop 发起文档作业。
- 验证模板选择可见。
- 验证文档工作台可见。
- 生成交付包。
- 用 WPS/Word 打开 DOCX。
- 按 `figureId` 替换图片。
- 再次用 WPS/Word 打开替换后的 DOCX。

## 前端 UX QA 要求

最终前端状态不能写“完成”，除非：

- 桌面端模板选择已测试。
- 桌面端文档作业生成已测试。
- 桌面端质量报告展示已测试。
- 桌面端图片调整已测试。
- 旧 DOCX 缺 `figureId` 的错误恢复已测试。
- 键盘和焦点检查已执行，或明确写“未验证”。
- 自动化可访问性已执行，或明确写“未验证”。
- 视觉回归已执行，或明确写“未验证”。

如果 v2 引擎能力存在但没有可见 UI 入口，发布状态是“未完成”，并至少记录一个 P1。

## 风险

- 当前模板引擎在主仓库外，实施时必须先确立唯一源码真值。
- 当前 Taiji 工作区已有未提交改动，实施时必须避免覆盖无关改动。
- WPS/Word 视觉正确性不能仅凭 DOCX XML 推断。
- 只拆脚本会让代码更整齐，但不能满足目标。
- 先做 UI 再补引擎契约，会继续放大当前耦合问题。

## 决策

采用“引擎内核优先”的彻底重构路线。

`docx-engine-v2` 是新的唯一核心。`docx-template-skill` 是分发壳和兼容壳。Taiji Desktop 是主要用户工作流入口。
