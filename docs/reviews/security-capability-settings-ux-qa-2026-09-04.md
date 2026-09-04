# 前端 UX QA 报告：安全扩展配置

## 状态与来源

状态：带限制完成（源码与隔离浏览器层）。实现、聚焦测试与全量门禁通过；提交与推送以暂存后的 Sol 最终审核及任务回执为准。完整引导浏览器复验通过，保留首次偶发失败记录；麒麟安装态未验证。

仓库 `/Users/bwb/Documents/工作/taiji-agentv1.0`，main，起点 `6ca47eae53086ded62978424bf2e39bb75251fbe`，开始时工作树干净。主代理唯一写入；security_contract_review 只读审查覆盖面。未操作其他 worktree、麒麟服务或用户配置。

## 范围、目标与信息层级

用户目标：企业安全下显式配置脚本任务与委派，保存并重启后真实生效，开始使用检查不再误判扩展权限；本机调试仍明确阻止正式完成。

主内容为基础模式、独立开关及保存操作；辅助内容为能力范围、风险确认和重启反馈；高级内容是当前生效能力列表。不增加完全开放模式、不新增逐次审批，不改变模型配置。

## 功能契约

契约依据：用户确认的安全扩展修复方案；角色：有配置权限的本机桌面用户。

| 能力 / 入口 | 数据与反馈 | 错误、加载、禁用 | 键盘 / 可访问性 | 浏览器证据 | 状态 |
|---|---|---|---|---|---|
| 两项扩展开关 / 设置→系统 | 独立布尔值，草稿与生效值分离 | 初始禁用、保存锁、只读原因、失败保留草稿 | label、fieldset、说明关联；Space 切换 | 三视口真实点击 | 通过 |
| 保存 / 同页按钮 | 原子配置写入；当前进程不变；重启反馈 | 防重入、503 重试 | 复用托管确认框，取消初始焦点、Escape 取消 | 三视口确认/取消/保存中重复调用 | 通过 |
| 自动刷新 / 状态监控 | 不覆盖草稿；恢复已保存设置 | 只读禁用；生效列表不提前变绿 | polite live region | 三视口刷新/模拟重启 | 通过 |
| 开始使用检查 / 引导页 | strict 扩展允许；pending 阻止完成 | 调试模式与待重启给出具体原因 | 既有恢复入口 | 后端聚焦通过；完整浏览器复验通过 | 通过（保留首次偶发焦点失败） |

## 验证台账

- RED：16 项扩展配置测试因接口缺少 capabilities 失败；随后 GREEN。另有路由字段转发与非法参数 4 项 RED→GREEN。
- RED：Electron 与 Linux 启动链把 strict 下的扩展清零；修复后 Electron launch-profile 38/38，Linux 启动矩阵聚焦 1/1 通过。
- RED：待重启安全配置仍能通过正式安装检查；修复后安全配置、writer、onboarding MVP/static 合计 68/68 通过。使用当前仓库 Agent venv Python 3.11，显式绑定 HERMES_WEBUI_AGENT_DIR/HERMES_WEBUI_PYTHON；临时测试状态、无真实 Provider。
- 工具回归：canonical `scripts/run_tests.sh` 运行 security mode、cron script、delegate 三文件，195/195 通过。日志 `/private/tmp/taiji-security-tool-tests.log`。
- 新浏览器 `tests/security_extensions_browser_smoke.py`：Python 3.13 已有 Playwright，headless Chromium，临时回环静态服务，真实工作树 HTML/JS/CSS，全部 API mock，外网与 WebSocket 阻断。1280/768/390 均通过，pageerror 为空；每视口一次模拟失败保存、一次成功保存，取消无写入。日志 `/private/tmp/taiji-security-browser-final.log` 首行记录截图目录。
- `npm run lint:runtime` 退出 0，`git diff --check` 通过。
- `scripts/verify.sh --full` 使用 Node 24.19.0，停于本地安全检查：routes.py 原提交为 1,167,421 bytes，已超过既有 1 MiB 门限；本次修改后为 1,167,809 bytes。尚未改变扫描器或绕过门禁，其他 full 阶段未执行。日志 `/private/tmp/taiji-security-verify-full.log`。
- 用户随后明确授权大型已跟踪源码扫描兼容修复。新增 RED→GREEN：HEAD 中已有源码完整读取（含 1 MiB 后敏感内容、Python 语法错误、历史凭据基线及暂存/未暂存差异），新文件/非源码仍限制 1 MiB，源码及累计两视图仍限制 4 MiB，读取前检查累计预算。扫描器相关测试 17 passed、58 subtests passed，当前工作树安全扫描 PASS；未添加跳过、白名单文件路径或 diff-only 扫描。
- 授权修复后全量 `scripts/verify.sh --full` 退出 0，`verification: PASS`；Node 24.19.0，Agent venv Python 3.11。根级 1321 tests（2 skipped）、Desktop 79、DOCX 278、Agent 主套件 220、WebUI 952，branding/bootstrap/coexistence 后续套件全部通过。日志 `/private/tmp/taiji-security-verify-authorized.log`。这轮全量覆盖扫描器和启动链改动；专门的安全扩展配置 68 项、工具 195 项及浏览器证据另列，不冒充都由默认 full 注册执行。
- 首次完整引导浏览器回归失败于 760px 的 onboarding Escape 后工作台关闭按钮焦点断言；根因未确认，不宣称既有或新引入。日志 `/private/tmp/taiji-security-onboarding-browser.log`。随后仅给测试添加失败焦点诊断（不修改产品焦点行为、不改变等待条件），复验退出 0，mobile+desktop+keyboard+retry+conflict+completion 全流程通过，包含完成后隐藏入口；日志 `/private/tmp/taiji-security-onboarding-diagnostic.log`。这证明本次复验通过，不证明偶发问题已经修复。

## 视觉与可访问性

原生开关、可见标签、说明关联、fieldset 和焦点样式；确认框复用既有焦点循环。未开启能力用中性色，避免默认关闭被理解成故障；窄屏能力列表单列，避免名称挤压。初轮查看 390px 截图后调整了 fieldset 间距和窄屏列表，最终 390px 与 1280px 截图已人工复核，名称完整、层级清晰。768px 截图已查看，但内部滚动区域下半部分不在画面内，仅对已展示区域作视觉确认；完整控件操作有浏览器断言覆盖。

未验证：axe 自动化、像素级视觉回归、读屏软件、一小时连续使用、真实模型/脚本执行、麒麟安装态。不安装额外 QA 依赖。

## 问题与下一步

| 级别 | 问题 | 当前处理 |
|---|---|---|
| P1（已修复） | 安全门禁无法扫描此次必须修改的超限既有路由文件 | 用户明确授权后补齐有界完整扫描；聚焦安全测试及真实工作树扫描通过 |
| 未定级 | 首次完整引导浏览器的 760px 焦点断言偶发失败 | 增加失败现场诊断后完整复验通过；未修改焦点行为，根因仍未确认 |

下一步：闭合全量门禁、精确暂存并获得 Sol 对全部 staged bytes 的最终审核，再提交和正常推送。焦点偶发记录交由审核评估，不以未稳定复现的问题驱动无证据产品改动。制包、替换安装包及目标机验收仍未授权。
