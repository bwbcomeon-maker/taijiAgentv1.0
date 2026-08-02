# 专家团商业可交付全量落地计划

> 实施约束：复用 `.worktrees/expert-team-standalone-core`，不创建新 worktree；不启动子 Agent；所有运行、模型产物、截图与原生 Office 证据写入源码树外隔离目录。

## 目标

最终用户应能稳定完成：

`选择任务 → 确认需求 → 阶段生成 → 人工复核 → 生成 DOCX → 打开/保存副本/下载 → 重启恢复`

“可销售”必须同时具备七类任务真实 DOCX 交付、无 P0/P1、PR/CI/main 闭环、正式授权和 Provider 验证、安装态与目标 Kylin/UOS 终端证据。Finder 中的 worktree `.app` 只作为开发预览，不作为销售放行证据。

## 冻结基线

- 工作目录：`.worktrees/expert-team-standalone-core`
- 分支：`codex/expert-team-standalone-core`
- 实施起点：`7a6746baf5dae3dce5c3d9392817e8441ffeaf46`
- 实施前状态：工作树干净；相对本地 `main` 多 32 个提交。
- 历史恢复样本：会议纪要任务在 `materials / generated_invalid`，合同允许零资料，产物仅含 `missing_sources / warning`。
- 运行来源必须绑定本 worktree 的 WebUI、Agent、Python 与独立 runtime-home；不得回退到正式根目录源码。

## 实施顺序

### 1. 问题分级单一真相源

- 新增纯后端 `issue_policy.py`。
- `blocking`、`error` 阻断；`warning` 进入人工复核；`info` 只展示。
- `validation_status` 仅表示结构、协议、摘要绑定和完整性。
- 零资料门槛只读取冻结的 `source_requirement.minimum_ready`。
- 旧版 warning-only invalid 产物在完整性校验通过后计算有效校验结果并原位恢复，不重调模型、不创建第二个权威 attempt。
- 结构损坏、哈希不符、Brief 不匹配继续 fail closed。

### 2. 状态机、幂等与恢复

- 固化需求待完善、准备生成、生成中、待复核、内容阻断、执行失败、文件失败、已完成的可执行动作。
- 启动、保存、确认、生成、恢复和交付使用幂等键与 CAS/version。
- 双击、丢响应、轮询延迟与双窗口并发不得重复 Provider 调用。
- 模型协议错误、超时和业务不合格由用户明确重试；DOCX 失败只重试渲染。
- 成功状态清除陈旧错误投影但保留诊断事件；新会话不得继承旧 run。

### 3. 七类任务合同

| 任务 | 最少资料 | 必备章节 | 模板 |
|---|---:|---|---|
| 工作汇报 | 0 | 工作开展情况、存在问题、下一步工作安排 | `standalone-work-report` |
| 会议纪要 | 0 | 会议基本情况、议定事项、责任分工、后续跟踪 | `standalone-meeting-minutes` |
| 通知通报 | 0 | 背景与总体要求、通知事项、时间安排、责任分工、报送要求 | `standalone-office-material` |
| 方案说明 | 0 | 目标、现状与问题、主要措施、进度安排、保障机制 | `standalone-office-material` |
| 总结计划 | 0 | 阶段性工作总结、成效与亮点、问题与不足、下一步工作计划 | `standalone-office-material` |
| 材料润色 | 1 | 润色后正文、修改说明 | `standalone-office-material` |
| 研究报告 | 1 | 研究问题、证据、分析、结论边界、引用 | `standalone-research-report` |

- capability、profile 快照、Brief、Prompt、artifact、Markdown、DOCX 共享同一合同。
- 前五类未知事实只能标注“待补充/需人工确认”；润色无原文、研究无证据时 Provider 调用为零。
- 历史 run 使用冻结合同恢复；配置异常使用明确中文错误，不展示“未放行文种”。

### 4. 模型协议与中文用户投影

- Prompt 注入标题、文种、资料政策、必备章节、不可编造边界和输入引用。
- 严格校验结构、标题、章节、引用、纯净正文和输入绑定。
- 原始模型 JSON、英文内部错误、artifact ID 和堆栈不进入普通用户界面。
- 统一错误目录覆盖许可证、Provider、协议、内容阻断、状态冲突、DOCX 与本地打开错误。
- 每个错误说明已保留结果、下一步动作，并提供脱敏诊断编号。

### 5. 连续、可恢复的前端工作流

- 选择文种、输入诉求、补齐 Brief/资料、确认规格、分阶段复核、生成文档、打开或保存交付文件保持为一条连续路径。
- 任务弹窗固定头部和操作区，中部单一滚动；目标视口内主按钮始终可见。
- 主操作 150ms 内出现按下/忙碌反馈；保存展示保存中/已保存/失败并聚焦首个字段错误。
- 工作台只保留一个滚动上下文，顶部同时显示阶段、位置、状态和下一步。
- 轮询局部更新；IME composition 期间冻结重绘，结束后合并且保持焦点、光标、选区和滚动。
- 关闭重开恢复草稿；覆盖 Tab、Enter、Escape、焦点返回、可见焦点与语义标签。

### 6. DOCX 交付

- 四个 standalone 模板为唯一独立版来源。
- 文件名使用确认标题；manifest 绑定 run、Brief、artifact、模板版本、哈希和质量报告。
- 自动检查标题、章节、内部协议泄漏、占位资产、企业元数据、分页/列表/表格/字体与来源合同。
- 主操作为“打开最终 DOCX”，辅助操作为“保存副本”“打开文件夹”“查看质量报告”；浏览器环境降级为明确下载。
- 七类任务逐份在 WPS 和 Word 中原生验收。

### 7. 授权、安全与诊断

- 明确区分开发源码授权、产品许可证和 Provider 凭据。
- 密钥、正文、绝对路径和系统密码不得进入提交、截图、PR、诊断或日志。
- 附件服务端校验类型、大小、路径、哈希与可读状态。
- 脱敏诊断仅含 commit、source mode、run/阶段/attempt、错误码、问题数、Provider 错误类别和交付状态。
- 进程操作必须绑定 worktree、runtime-home、端口和父子来源。

### 8. 代码与兼容收口

- 不重写大型 `runtime.py`；只抽取问题策略和错误投影两个高内聚模块。
- V3 View 增加 `stage_quality.state`、`blocking_count`、`warning_count`、脱敏问题与服务端允许动作。
- 保留路由、历史字段和旧 run 读取；V1/V2 只读兼容。
- 对 `main...HEAD` 全路径按直接实现、必要基础设施、测试/模板/文档、无关变更分类；排除无关内容。

## 验证门禁

每项按 RED → GREEN → REFACTOR：

1. warning/info 不阻断，blocking/error 阻断；
2. warning-only 历史 run 原位恢复且模型调用不增加；
3. 七类资料政策、章节和 Prompt/DOCX 传播；
4. 幂等启动、双击、丢响应和 CAS；
5. Provider 401/429/超时/协议错误；
6. 重启恢复、陈旧错误清理和跨会话隔离；
7. IME、焦点、滚动和局部重绘；
8. 四模板、七 DOCX 与四个交付动作；
9. 旧版只读兼容和隐私投影。

本地门禁包含专家团 Python 全量、WebUI runtime lint、JS/Python 语法、`git diff --check`、DOCX Engine、Desktop check/Node tests 和根合同测试。真实 UX QA 使用 Electron，在 1024×768、1280×800、1440×900 下覆盖七类任务、中文 IME、恢复和文件打开，证据保存于源码树外。

## Git、CI 与发布闭环

- 本地、真实 Electron、七类 Provider、DOCX 原生验收全部通过后，恢复 `gh` 授权并创建单一 Draft PR，运行 `full-ci`。
- CI Gate 绿色且审查闭环后转 Ready；推荐 squash merge。
- 合并前保存 branch tip backup ref 和 bundle；合并后正式根目录 `git pull --ff-only`，证明 squash commit 与 branch tip 的 exact tree/blob/mode 等价。
- 从正式 `main` 复验来源、窗口、服务、七类入口和代表性生成后才清理 worktree/分支。
- Kylin/UOS 严格按“源码包已准备 → 制包机已构建 → 离线安装已演练 → 目标机已验证”放行。缺目标机证据时只能称“发布候选”，不能称“可销售”。

## 实施前实时证据

- 专家团 Python：`1113 passed, 1 warning`。
- DOCX Engine：`270 passed, 0 failed`。
- WebUI：`npm run lint:runtime` 通过。
- Desktop：`npm run check` 通过，Node tests `15/15` 通过。
- 以上均绑定实施起点和 worktree 源码；历史记录不替代后续修改后的重新验证。
