# 前端 UX QA 报告：专家团 contract-first 联合放行验收

> 验收日期：2026-07-15
>
> 代码基线：`37f516a3`；未启用目标配置，未创建 fixture 之外的伪造 run。

## 状态

**阻塞（`BLOCKED`）**。

自动合同与 Electron fixture 层已有较强证据，但真实活动环境缺少经审批的模型数据策略、可信 OIDC 身份和可预检 provider capability。第二份计划 §9.2 的真实模型样本门、两条真实黄金路径、目标 WPS 终验均未执行。不能据此启用 pilot，也不能声明 `BRIEF_ENTERPRISE_USABLE`、`PILOT_ONLY` 或 `ENTERPRISE_READY`。

## 变更范围与主要用户目标

- 范围：内容创作专家团的工作汇报、深度研究专家团的专题研究报告；Brief、阶段复核、canonical/DOCX、Office 二级验收、rollout off/pilot 与回退边界。
- 主要用户目标：从真实召集入口创建企业合同任务，在右侧工作台完成需求确认、阶段复核、正式 DOCX 验收和可信授权，最终只在三道门全部通过后显示完成。
- 主内容：唯一下一步、Brief 0/N、阶段结果、canonical 成果、Office 决策。
- 辅助内容：专家协作进度、短 hash、版本与问题摘要。
- 高级内容：完整 binding/hash、策略和身份 fingerprint、审计元数据；不应暴露 token、完整 claims 或凭据。

## 四层证据

| 层级 | 状态 | 已实时验证 | 未验证 / 边界 |
|---|---|---|---|
| 1. 自动合同层 | **通过** | DOCX 计划 8 文件：`167 passed`；`npm run lint:runtime`：退出 0；DOCX UI contract：`21 passed`；专家团全量：`558 passed`；rollout off：`14 passed`；rollout pilot：`14 passed` | 只证明自动合同，不代替真实模型/身份/Office |
| 2. 确定性 Electron UX 层 | **通过（fixture 边界）** | off 与 pilot 串行新鲜运行均输出 `EXPERT TEAM ELECTRON SMOKE OK`，各 20 张截图；覆盖 Brief、invalid、轮询恢复、Office 人工新增问题、1024/1280/1440 滚动前主操作 | 使用网络拦截/fixture，不证明真实模型、真实后端身份或真实 Office；≤900px 独立视口、axe、像素基线未验证 |
| 3. 真实后端 / 模型 / 身份层 | **阻塞** | 活动后端 `/health` 200；受保护 status 为 403，未绕过认证。脱敏读取活动进程有效配置：rollout env/config 均 absent，model policy 数量 0，trusted identity disabled，handoff false，runtime adapter env absent（默认 legacy-direct） | 无真实 provider/deployment/trust-zone/retention capability；无真实 IdP credential；无 10+10 普通样例和两团各 10 个注入样例；无两条真实黄金 run |
| 4. 目标 Office 层 | **阻塞** | 本机已安装 WPS `12.1.26026`（`com.kingsoft.wpsoffice.mac`）和 Word `16.110.3`（`com.microsoft.Word`） | 没有来自同一真实黄金 run 的两份 DOCX/binding/hash，未打开 WPS/Word，未做返修、旧验收失效与新 attempt 复验 |

## 自动化检查运行结果

| 检查项 | 命令 | 结果 |
|---|---|---|
| DOCX 计划 8 文件 | `node --test tests/run-job-contract.test.js ... tests/wps-visual-acceptance.test.js` | `167 passed, 0 failed` |
| Runtime lint | `npm run lint:runtime` | 退出 0 |
| 专家团全量 | `pytest -q tests/test_expert_team_*.py` | 最终新鲜全量复验：`558 passed`，退出 0，约 145 秒 |
| DOCX UI contract | `pytest -q tests/test_docx_engine_v2_ui_contract.py` | `21 passed, 1 warning` |
| rollout off | `TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT=off pytest -q tests/test_expert_team_rollout_gate.py` | `14 passed` |
| rollout pilot | `TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT=pilot pytest -q tests/test_expert_team_rollout_gate.py` | `14 passed` |
| Electron off | `...ROLLOUT=off ... node tests/expert_team_electron_artifact_smoke.js --out-dir /tmp/expert-team-final-fix-off-qa` | 串行复验退出 0，20 张截图 |
| Electron pilot | `...ROLLOUT=pilot ... node tests/expert_team_electron_artifact_smoke.js --out-dir /tmp/expert-team-final-fix-pilot-qa` | 串行复验退出 0，20 张截图 |

### 首轮 5 个失败及修复后复验

1. `tests/test_expert_team_delivery_integrity_hardening.py:410`：`test_real_first_pending_office_lifecycle_begin_get_safe_submit_and_replay`；默认 off 下 helper 直接 start，被 `contract_rollout_disabled` 拒绝。
2. `tests/test_expert_team_frontend.py:57`：`test_presenter_is_the_only_source_of_main_state_and_action`；presenter 仍出现 `statusLabel`。
3. `tests/test_expert_team_frontend.py:137`：`test_expert_team_workspace_uses_summary_tabs_and_confirmation_wizard`；静态合同未找到 todo tab 标记。
4. `tests/test_expert_team_frontend.py:202`：`test_workspace_panel_can_collapse_and_expand_without_becoming_chat_message`；静态合同未找到收起入口调用标记。
5. `tests/test_expert_team_frontend.py:325`：`test_default_user_visible_copy_has_no_public_account_language`；Brief audience 标签包含“读者”。

上述 5 项在实施任务提交 `e3d4de6a` 后先精确复验为 `5 passed`，本轮又补齐 OIDC flow/session 绑定、Office 人工问题与中等视口主操作，最终全量为 `558 passed`。它们仅作首轮诊断历史保留。

## 已测试的主要用户路径

- rollout off：召集弹窗显示 AI 草稿能力，新 contract-v1 入口不可绕过。
- rollout pilot：工作汇报与专题研究入口可发现，创建后进入 Brief 0/N。
- 需求确认、generated-invalid、两个轮询周期草稿保持、409/版本推进恢复。
- Office pending、九项检查、passed / passed-with-conditions / failed、waiver 与返修的确定性 UI 合同。
- 1024、1280、1440 宽度与键盘焦点、Escape、关闭后焦点归还（以 smoke 断言为准）。

## 功能契约摘要

| 能力 | API/状态 | 可见 UI | 用户反馈/错误 | 键盘 | 真实 E2E | 状态 |
|---|---:|---:|---:|---:|---:|---|
| Brief 编辑确认 0/N | 是 | 是 | 是 | fixture 已测 | 未测真实后端 | 未验证 |
| 阶段复核与草稿恢复 | 是 | 是 | 是 | fixture 已测 | 未测真实模型 | 未验证 |
| canonical/DOCX/Office | 是 | 是 | 是 | fixture 已测 | 未测真实 WPS | 未验证 |
| 独立 authorizer handoff | 是 | 是 | fixture 已测 | fixture 已测 | 真实 IdP 缺失 | 失败 |
| 企业模型策略门 | 是 | status/API | fail-closed | 不适用 | policy 数量 0 | 失败 |
| pilot 启用与回退 | 是 | off/pilot fixture 可见 | 是 | fixture 已测 | 目标配置未启用 | 未验证 |

## 浏览器、截图与视觉证据

- off：`/tmp/expert-team-final-fix-off-qa/`，20 张；pilot：`/tmp/expert-team-final-fix-pilot-qa/`，20 张。
- 重点人工查看：`expert-team-plan-a-confirmation-open.png`、`expert-team-plan-a-collaboration-tab-generated-invalid.png`、`expert-team-polling-draft-recovery.png`、`expert-team-office-review-drawer.png`、`expert-team-plan-a-stage-input-1024.png`、`expert-team-rollout-gate.png`。
- 当前图片查看工具直接解码部分 PNG 时出现黑块；同一无 alpha PNG 转为 JPEG 后内容正常。该现象按证据工具兼容性记录，不当作产品 UI 通过或失败的唯一依据。
- 视觉观察：工作台、Brief、错误状态和 Office 抽屉层级可辨；1024/1280/1440 在滚动前已可看到阶段确认主按钮；Office 新增问题表单无遮挡。
- 视觉回归基线：未配置。自动可访问性 axe/Lighthouse：未验证。

## 可访问性与长时间工作体验

- Electron smoke 已断言部分 Tab、Enter、Escape、焦点圈定与归还；Office 抽屉有对话语义和可见操作。
- 未运行 axe/Lighthouse；颜色对比、完整读屏顺序、≤900px 全路径和 reduced-motion 未验证。
- 右侧工作台为独立滚动区，长表单可连续操作；真实长时业务使用、真实数据密度与一小时以上疲劳度未验证。

## 六角色联合审查结论

| 角色 | 结论 | 已收口 | 剩余边界 |
|---|---|---|---|
| 功能契约 | 代码/fixture 层通过 | OIDC flow 与会话绑定、Office 结构化问题、同人复核拒绝、rollout fail-closed | 真实 IdP、真实模型与目标 Office 未验证 |
| 信息架构 | 无 P0/P1 | 当前任务前置、主操作滚动前可见、Office 身份入口可发现 | 极窄视口未做完整路径 |
| 视觉层级 | 无 P0/P1 | 1024/1280/1440 主操作可见，Office 问题表单无遮挡 | 原生文件输入与关闭按钮一致性为 P3 |
| 可访问性 | 无已知 P0/P1 | 登录入口、删除后焦点、Escape 与焦点归还已有自动断言 | 完整背景 inert、axe、读屏与对比度未验证 |
| 长时工作 | 无 P0/P1 | 独立滚动区、轮询草稿恢复、错误后可继续 | 跨应用重启草稿持久化为 P2，真实一小时使用未验证 |
| 测试覆盖 | 自动层通过 | 全量 558、DOCX 167、UI 21、off/pilot Electron 各 20 张截图 | 无真实环境 E2E、像素基线和目标 Office 证据 |

联合结论：本轮代码与确定性 UX 回归没有剩余 P0/P1；外部企业放行门仍有三项 P0 阻塞，不属于可在本地伪造完成的代码缺口。

## 问题列表

| 严重程度 | 问题 | 证据 | 影响 | 修复状态 |
|---|---|---|---|---|
| P0 | 真实活动环境无法合法进入企业模型层 | policy=0；runtime adapter 默认 legacy-direct；无 provider capability | §9.2、黄金路径、DOCX 均不能启动 | 未修复，外部配置/运行时阻塞 |
| P0 | 真实可信身份和职责分离不可用 | trusted identity disabled；handoff false | 阶段批准、Office、waiver 不能作为企业证据 | 未修复，外部 IdP 阻塞 |
| P0 | 双黄金路径与 WPS 终验缺失 | 无真实 run/binding/DOCX hash | 不能完成企业交付 | 未修复 |
| P1 | 自动发布门首轮有 5 个失败 | 见失败清单 | 当时阻断 release regression | 已收口，精确 5 项与最终全量 558 项均复验通过 |
| P2 | Electron smoke 固定端口，不宜并行 | 首轮并行出现 19942/19987 竞争；串行通过 | CI 并行可能产生假失败 | 未修复；当前按串行执行 |
| P2 | axe/视觉基线未配置 | 无对应命令 | 无自动 a11y/像素回归证据 | 未修复 |
| P2 | 跨应用重启草稿持久化未覆盖 | 当前只验证轮询与错误恢复 | 长任务在应用异常退出后可能需要重填 | 未修复；需单独定义持久化与敏感数据边界 |
| P3 | Office 抽屉关闭按钮外观原生 | 截图人工检查 | 视觉一致性较弱 | 未修复 |

## 未验证项目与放行结论

- Brief §6.2 的真实后端两条黄金路径：未验证。
- Stage §9.2：普通样例 `0/10 + 0/10`；注入对抗 `0/10 + 0/10`，未验证。
- 真实 provider/deployment/trust-zone/retention/training-opt-out/role-preservation/tools-disabled：未验证。
- 真实 OIDC issuer/audience/key/role、过期 credential、独立账号切换：未验证。
- 目标 WPS 两份 DOCX、返修、旧验收失效、新 delivery attempt：未验证。
- 目标 rollout 启用后 status 与一键回退复验：未验证。

**放行结论：`BLOCKED`。保持目标配置为 off/未配置；不得写入 pilot。**
