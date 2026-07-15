# 专家团企业 DOCX 联合验收报告

> 验收日期：2026-07-15
>
> 代码基线：`37f516a3`；本文件为 Task 8 联合验收记录。

## 状态

**阻塞（`BLOCKED`）**。

DOCX 自动合同层已通过计划指定的 8 个 Node 测试文件，但真实活动环境不具备经审批的模型数据策略、可信身份和可预检 provider capability，因而本轮没有合法生成工作汇报与专题研究两条真实黄金 run。没有同源 run、canonical、binding 和 DOCX hash，就不能打开任意历史/fixture DOCX冒充 WPS 终验。

## 四层证据

| 层级 | 状态 | 证据 |
|---|---|---|
| 语义 / canonical 自动合同 | 已实时验证 | DOCX 8 文件 `167 passed`；专家团全量修复后新鲜复验 `558 passed` |
| 模板 / renderer / binding 自动合同 | 已实时验证 | `run-job`、domain、template、adapter、rich draft、render plan、delivery validation、WPS acceptance 测试均在上述 167 项内通过 |
| 两条真实黄金路径 | 未验证 | 无工作汇报和专题研究真实 run；无 session/stage attempt/document revision/delivery attempt/hash 链 |
| 目标 Office 人工终验 | 未验证 | WPS 与 Word 已安装，但没有本轮同源真实 DOCX，未启动应用、未执行视觉检查 |

## 自动验证结果

```text
node --test tests/run-job-contract.test.js tests/domain-contract.test.js \
  tests/template-package.test.js tests/template-data-adapter.test.js \
  tests/rich-draft-package.test.js tests/render-plan.test.js \
  tests/delivery-validation.test.js tests/wps-visual-acceptance.test.js

结果：167 passed, 0 failed
```

该结果证明 schema、模板、render plan、binding、replay、质量报告和 WPS acceptance 写入合同的确定性行为；不证明企业正文质量或 WPS 视觉通过。

## 黄金路径绑定表

| 路径 | session/run | Brief revision/hash | stage artifact attempt | document revision | delivery attempt | canonical hash | template package hash | binding hash | DOCX hash | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|
| 工作汇报 | 无 | 无 | 无 | 无 | 无 | 无 | 无 | 无 | 无 | 未验证 |
| 专题研究报告 | 无 | 无 | 无 | 无 | 无 | 无 | 无 | 无 | 无 | 未验证 |

## 目标 Office 可用性

- 已实时验证安装：WPS `12.1.26026`，bundle `com.kingsoft.wpsoffice.mac`。
- 已实时验证安装：Microsoft Word `16.110.3`，bundle `com.microsoft.Word`。
- WPS 实际打开两条黄金 DOCX：未验证；原因是没有合法生成的同源 DOCX。
- Word 实际打开：未验证；本轮也未确认产品是否把 Word 双兼容列为强制声明。

## 必检矩阵

| 检查项 | 工作汇报 | 专题研究 | 证据 |
|---|---|---|---|
| 标题与封面 | 未验证 | 未验证 | 无真实 DOCX |
| 文种结构与目录 | 未验证 | 未验证 | 无真实 DOCX |
| 正文顺序 | 未验证 | 未验证 | 无真实 DOCX |
| 图表唯一性与可读性 | 未验证 | 未验证 | 无真实 DOCX |
| 表格、分页、页眉页脚、页码 | 未验证 | 未验证 | 无真实 DOCX |
| 引用与事实可追溯 | 未验证 | 未验证 | 无真实模型样本 |
| 密级、占位符、流程话术 | 未验证 | 未验证 | 无真实 DOCX |
| 聊天/canonical/DOCX 同源 | 未验证 | 未验证 | 无共享 hash 链 |
| 返修一次与旧验收失效 | 未验证 | 未验证 | 无 delivery attempt |

## 阻塞原因

1. 活动环境 `expert_team_model_data_policies` 数量为 0，不能授权真实 provider/deployment/trust zone/retention。
2. 活动环境 `expert_team_trusted_identity` 为 disabled，不能取得真实 reviewer/authorizer 与职责分离证据。
3. 活动环境 `HERMES_WEBUI_RUNTIME_ADAPTER` 未设置，按代码为默认 legacy-direct；企业模型路径需要 runner provider capability 预检，不能用 legacy flatten 路径替代。
4. Stage §9.2 的真实模型样本为 0，不能先生成两份 DOCX绕过模型门。

## 风险与结论

- `document.docx` 文件存在、自动测试通过、WPS 已安装或文件能打开，任何单项都不等于企业交付完成。
- 在缺少真实身份和 model policy 时手工写 sidecar、测试 resolver、测试 policy 或复制历史 DOCX，会破坏可信验收链，本轮未这样做。
- 只有先补齐真实部署策略和 IdP、跑完 §9.2，再从真实召集入口生成两条黄金路径，才能填写上表并进行 WPS 终验。

**结论：Task 8 的自动合同与验收记录已完成，但企业 DOCX 人工放行仍被真实模型、可信身份和同源黄金 DOCX 三项前置条件阻塞；目标 rollout 不应启用。**
