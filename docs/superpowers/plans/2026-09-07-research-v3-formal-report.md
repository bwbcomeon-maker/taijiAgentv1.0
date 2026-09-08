# 深度材料研究团 v3 正式调研报告实施计划

## 状态与边界

- 本文件记录已批准的实施顺序和验收合同，不是上线、安装、DOCX 交付或真实 Provider 验收证据。
- 当前基础层只冻结 `research-report/v3` 规格、正式材料写作/提纲预算合同，以及启动字段的兼容接口；公开 `research-report` 仍为 `research-report/v2`，没有默认启用 v3。
- 不迁移既有任务。历史 v2 run 继续依赖其已冻结的 profile/Brief，并沿用原有门禁。
- 本基础子任务不执行真实 Provider、Electron、DOCX/WPS 验收或日常用户状态写入；后续按已批准的三组虚构夹具和隔离源码预览完成真实 Electron、现有 Provider、DOCX/WPS 验收，不扩大到日常用户数据。
- 本计划不授权制包、安装、签名、部署、发布、Tag 或 Release。

## 目标成果

“深度材料研究团”面向央国企内部专题调研，政府为可选写作对象。产物是服务决策的正式长报告，不是科普或公众号文章。

- `central_enterprise`：默认，面向央国企内部专题调研与决策参考。
- `government`：面向政府内部专题调研与决策参考。
- `standard`：正文 8,000–12,000 字，目标 10,000 字。
- `deep`：正文 15,000–20,000 字，目标 17,500 字。
- 正文按 1,200–2,000 字的分章单元生成并持久化 checkpoint；同一质量问题最多自动修订两轮。

资料不足时先在既有、已授权的检索边界补充资料；仍不能满足正式等级时，产物只能标为“初步研究稿”。虚构事实、伪引、关键矛盾或事实阻断项必须停止相应结果，不能靠降级绕过。

## 冻结的 v3 合同

### 版本和启动规格

1. 常量：`research-report/v2` 与 `research-report/v3` 是不同的精确版本，不使用“版本大于等于 v2”或把全部 v2 判定改成包含 v3 的方式分派。
2. 公开启动请求保留既有四个字段：`launch_profile_id`、`session_id`、`prompt`、`idempotency_key`。为 v3 预留并规范化 `writing_style`、`depth`；它们只能同时出现，均为字符串，去首尾空白后分别限定为：
   - `writing_style`: `central_enterprise` 或 `government`
   - `depth`: `standard` 或 `deep`
3. 当前 v2 profile 收到这两个字段时应以 `research_v3_profile_not_enabled` 拒绝启动，不能落入旧的 v2 自动确认/自动推进路径。未携带两个新字段的 v2 请求行为不变。
4. v3 启用必须与 v3 runtime、UI、阶段产物和验收实现同时合入；届时将规范化后的两字段和写作合同写入 run 的冻结快照。不能只把活动 profile 的版本字符串改为 v3。
5. 后续阶段仅可使用严格分派助手。`is_research_v3_run(run)` 的最小身份条件是：schema version 3、`launch_profile_id=research-report`、`team_id=deep-research-team`、匹配的冻结 profile、精确 v3 版本、独立模式以及已确认的 `research_report` Brief。不得从 `automatic_fallback`、来源数量或其他可变资料策略推断 v3 身份。

### 正式写作和提纲合同

默认章节顺序如下，核心四项不可缺失：

1. 背景、目的与范围
2. 现状
3. 问题与原因
4. 实践借鉴（仅存在可信案例时纳入）
5. 方案比较与适用条件（按题目适用）
6. 判断与建议
7. 推进保障与需研究事项

提纲的主要篇幅应留给现状/问题/原因分析、方案比较/适用条件和判断/建议；不以通用背景或科普内容替代研究判断。行文应正式、平实、完整，说明判断依据及其对工作的含义；不得编造政策、职责、数字、事实或引用。

正文与证据追踪分离：实际正文片段必须关联可追溯来源，原始声明放在 metadata，用户提供的背景单列 `user_background` origin，不称作“模型知识”。正文不得出现 `source_id`、`claim_id`、冻结 Brief 或登记原声明。机器核验只确认机器身份和引文出现，不能宣称语义等价；正式等级仍要求人工或模型内容审查。

“正式研究报告”不是默认标签。它至少要求资料状态充分、字数门禁、结构/质量门禁以及人工或模型审查均通过，且没有事实阻断项；资料在授权边界内补充后仍不足、且无事实阻断时，才可标为“初步研究稿”。

## 分阶段实施

### 1. 规格、版本兼容和合同基础（当前范围）

- 在 `api/expert_teams/research_contract.py` 固化版本常量、严格分派、v3 启动字段规范化、写作/提纲预算、证据边界和成果等级判定。
- 在现有 standalone 启动接口预留并校验两个字段，同时用活动 v2 profile 显式拒绝 v3 规格。
- 增加 v3 合同测试，保留 v2 intake 与 standalone 合同回归。
- 不新增活动 v3 profile，不改 v2 stages、自动确认、自动推进、检索、渲染或交付实现。

### 2. v3 runtime 和冻结状态（待实现）

- 新增明确的 v3 activation profile/切换点，并将规范化后的写作规格、写作合同、资料边界和成果等级规则冻结到新 run。
- 保留六个外部阶段：方向、资料、证据、提纲、初稿、复核；方向、资料、证据、提纲保持现有阶段形态，仅初稿和复核内部实现分章生成、持久 checkpoint、续接与最多两轮自动质量修订。
- 定义 v3 stage artifact schema：正文片段与来源绑定、证据追踪独立保存、metadata 保存原始声明，用户背景采用 `user_background` origin。
- 将 v3 专属门禁接到严格分派，逐处替换“可推断 v3”的条件；v2 的既有保护逻辑保持精确 v2 语义。
- 实现资料不足处理：先调授权检索，仍不足时生成有明确边界的初步研究稿；事实/伪引/关键矛盾继续阻断。

### 3. 提纲、章节写作和复核（章节核心已实现；runtime 接入待实现）

- 已新增纯状态核心 `research_chapters.py` 与 `research_provenance.py`：它们从冻结合同构造确定性章节/单元预算，维护 JSON 章节账本、父阶段身份、调用/流绑定、幂等回执、两轮定向修订和系统组装；同时从已核验 source context、已批准 evidence matrix 与确认的用户背景输入派生旁表、机械绑定报告和独立复核的当前哈希/覆盖绑定。模块不写 run、不发起调用，也不启用 v3。
- 该核心仍须在后续 runtime 的既有 run mutation lock 内持久化，并由 v3 draft/review 接缝调用；现有 v2 路径没有变化。

- 提纲按正式章节合同分配预算，优先分析、比较和建议。
- 逐章生成 1,200–2,000 字单元，累计字数满足 `standard` 或 `deep` 目标范围；checkpoint 应保存已验证章节、待写章节和引用绑定状态。
- 对每个正文片段建立来源引用；内容层不输出内部 ID、Brief 或原始登记说明。
- 复核执行机器 identity/quote-presence 检查和人工或模型内容审查；区分“引文存在”与“语义得到支持”。

### 4. UI 和进度可见性（待实现）

- 研究启动界面提供写作对象与深度两个可发现、可访问的选项，默认央国企与标准深度。
- 展示六阶段和章节级进度、累计字数、结果等级，以及“初步研究稿/待复核/阻断”的原因。
- 在独立浏览器/Electron 配置下执行前端 UX QA；不以静态脚本或编译结果替代真实交互证据。

### 5. DOCX 与来源附件（待实现）

- DOCX 使用清晰目录层级，正文与来源附件边界清晰，不泄漏内部 source/claim 标识。
- 交付前执行内容确认、DOCX 自动检查、WPS/Word 本机打开确认；分别记录源码、制品、安装态和人工验收证据，不混用。

### 6. 回归、审核与交付（待实现）

- 自动化至少覆盖：央国企标准、政府深度、授权检索后仍资料不足的初步研究稿；另覆盖虚构/伪引/关键矛盾不能降级、超时、截断、取消、重放、刷新、输入失效、GET 禁止 dispatch、字数排除附录与不得凑字。
- 执行受影响 Python/JS 回归、`scripts/verify.sh --full`、Sol staged 审核，以及已批准三组虚构夹具下的真实 Electron、现有 Provider、DOCX/WPS 验收后，才进入 commit/push 流程。
- 本计划不包含制包、安装、签名、部署、Tag、Release 或发布；这些阶段需要单独授权和各自证据。

## 后续对接点

| 对接面 | 当前基础接口 | 后续责任 |
| --- | --- | --- |
| 启动请求 | `validate_standalone_start_request()` 规范化两字段；v2 显式拒绝 | v3 profile 启用时冻结到 run/Brief |
| 版本分派 | `research_contract_route()`、`is_research_v3_run()` | runtime、source context、prompts、view、documents 逐处精确接入 |
| 写作合同 | `formal_report_writing_contract()` | 提纲、章节生成、质量复核与 DOCX 读取同一冻结合同 |
| 成果等级 | `determine_research_result_grade()` | 由资料、字数、结构质量、审查和事实阻断实测结果驱动 |
| UI | 无 v3 激活入口 | 添加两选项、章节进度、字数和成果等级并完成 UX QA |
| DOCX | 无 v3 render 接入 | 目录、来源附件、自动检查与 WPS/Word 确认 |

## 本阶段验收

- v3 规格仅有明确枚举，未知版本或伪造/不匹配冻结 profile 不能获得 v3 路由。
- 旧 `research-report/v2` 的无新字段启动、自动确认与既有回归维持原行为；带 v3 字段不可能静默进入旧路径。
- 正式材料合同明确字数、章节、提纲重心、来源边界、禁止正文泄漏内容与成果等级门禁。
- 本阶段通过的测试只能证明源码基础层；未证明 v3 runtime、UI、真实 Provider、Electron、DOCX/WPS、安装或发布。
