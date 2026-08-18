# 前端 UX QA 报告：首次启动配置工作台

## 状态

带限制完成（当前功能 worktree 源码态）。已完成三路独立复核，未发现剩余 P0/P1。

代码契约、设置持久化并发、API 一致性、真实 Chromium 主路径、键盘焦点和 390 px/1440 px 截图已实时验证。当前结果尚未合并并复验正式 `main`，也不代表 DEB 或麒麟/UOS 安装态通过；axe/Lighthouse 和像素级视觉回归仍未验证。

## 变更范围

- 后端：新增无密钥的 `taiji-setup-status/v1` 状态投影，提供 `/api/setup/status` 与 `/api/setup/preflight`。
- 持久化门禁：检查未通过时拒绝完成；不支持 Provider 与源码跳过开关不再通过 setup API 持久化完成标记。
- 安全写入：已有配置默认返回 409，必须显式确认才允许覆盖。
- 前端：首页及完成页提供四项检查、单项重试、恢复入口、加载/错误/空/成功/禁用/覆盖确认状态。
- 可恢复关闭：暂时关闭或按 Escape 后显示持久、可发现、可聚焦的“继续配置·开始使用检查”入口；Enter 无刷新重开，完成后隐藏。
- 设置持久化：所有 `save_settings()` 共享同一进程锁，使用同目录临时文件、文件 `flush/fsync`、`os.replace` 和父目录 `fsync` 原子提交，且不再修改调用者输入。
- 完成并发：每个完成请求使用仅进程内可见的 token/generation 所有权标记，commit/rollback 均按 marker 做 CAS；被显式设置或更新请求淘汰的旧请求重新返回当前权威状态。
- 测试：扩展单元、API 集成、静态契约，并新增显式绑定当前 worktree 的浏览器烟测脚本。

## 主要用户目标

用户第一次打开客户端时，能明确看到当前终端是否满足授权、模型、工作区和安全策略四项最低条件；任何失败都有明确原因与恢复路径，且不会在用户未确认或检查未通过时标记完成。

## 主内容 / 辅助内容 / 高级内容

- 主内容：四项就绪状态、原因、去处理、重新检查和最终完成。
- 辅助内容：提供商/模型、工作区、密码配置及完成前摘要。
- 高级内容：授权管理、安全设置、OAuth/自定义 Provider 恢复路径。

## 已测试的主要用户路径

1. 无配置启动：状态为未完成，四项检查提供固定 ID、原因和恢复数据。
2. 检查失败后完成：返回 409 `setup_not_ready`，`onboarding_completed` 不写入。
3. 已有配置再保存：返回 409 `config_exists`，配置、`.env` 和进程环境在确认前不变。
4. 显式确认覆盖：仅发送 `confirm_overwrite=true` 后进入现有凭据事务写入链。
5. 四项全部就绪：完成端点持久化标记，后续状态为完成。
6. 不支持 Provider/源码跳过分支：setup 端点不得写完成标记。
7. 前端暂时关闭：真实 Chromium 验证不调用 complete API，重新打开后仍进入检查。
8. 首次状态读取失败：显示可重试错误态，禁用“继续”，焦点进入重试按钮，恢复后回到四项检查。
9. 无刷新恢复：390×844 下按 Escape、1440×960 下键盘触发“暂时关闭”后，焦点自动落到恢复入口；按 Enter 重开并恢复四项检查，完成后入口隐藏。
10. 并发写入：两个设置更新可控交错时不丢字段；失败 completion 晚回滚不覆盖另一成功；显式重置后旧 commit 不复活且响应与磁盘一致；A 成功、B 失败仍返回并保持完成。
11. 自托管 Provider：Base URL 探测成功/失败均原位更新，不销毁已聚焦输入；状态通过 live region 播报；模型列表为空时视为失败并禁用“继续”。
12. 外部恢复路径：从失败项进入授权/安全设置后，焦点进入已选设置面板，不被工作台恢复入口抢回。

## 功能契约摘要

详见 [onboarding-setup-workbench-feature-contract-2026-08-04.md](onboarding-setup-workbench-feature-contract-2026-08-04.md)。当前没有发现 P0/P1 问题；浏览器验证仅绑定当前功能 worktree，不代表正式 `main` 或安装态。

## 真实浏览器测试证据

真实浏览器测试：通过（当前 worktree 源码态）。

- 已准备：`hermes-local-lab/sources/hermes-webui/tests/onboarding_workbench_browser_smoke.py`。
- 显式运行绑定：脚本从 `__file__` 解析 WebUI/worktree，使用 `git rev-parse --show-toplevel` 校验，并将两个 agent 目录变量指向同一 worktree 内的 `hermes-agent`。
- 网络边界：服务端启用测试网络阻断，浏览器路由阻止非回环请求，不使用真实 API 密钥。
- 执行结果：退出码 0；输出 `PASS onboarding_workbench=mobile+desktop+keyboard+retry+conflict+completion`；除测试预期的 409 配置冲突资源日志外，无 console error/page error。
- 源码绑定：`source_worktree=/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/linux-sales-grade-installer`，`source_head=43ebed100b78ad294e0fc67d90c87474cdc2335d`。
- 运行器隔离：现有 Python 3.13 Playwright 1.58/Chromium 驱动浏览器，`TAIJI_ONBOARDING_SMOKE_SERVER_PYTHON` 显式指向当前 Agent venv 启动被测服务器；脚本保留 venv 的 `bin/python` 路径，不解析成丢失 site-packages 的基础解释器。
- 状态边界：上述 HEAD 是当前分支基线；本轮修复仍是该 worktree 的未提交修改，未以 commit/正式 `main` 身份验收。

## 截图情况

截图：已验证。本轮最终输出位于 `/private/tmp/taiji-onboarding-final-stable-20260804/`，共 7 张：390 px 工作台、390 px 恢复入口、1440 px 默认态、1440 px 恢复入口、状态失败恢复、覆盖确认和完成态。人工复核确认窄屏首屏可见标题、五步进度和工作台，按钮视觉顺序与 DOM/Tab 顺序一致；窄屏无横向溢出，恢复入口完整位于视口内。

## 可访问性检查

- 人工语义检查已执行：对话框使用 `role=dialog`/`aria-modal`，标题与说明关联；检查列表使用 list/listitem 语义；动态状态使用 `aria-live`、`aria-busy`；按钮有可见文本或 `aria-label`；覆盖确认使用 `role=alert`并将焦点移入确认按钮。
- 状态不只依赖颜色：同时显示“已就绪/需要处理/暂不可用”文本。
- 自动化可访问性：未验证。原因：项目当前未配置 axe/Lighthouse 等工具。
- 键盘完成主路径：已执行；Escape/“暂时关闭”后焦点落到恢复入口，Enter 无刷新重开；同时覆盖单项重试、状态失败重试和覆盖确认的焦点恢复。
- 自托管探测与外部恢复：已执行；探测期间输入焦点保持，状态 live region 更新，授权/安全设置跳转后焦点进入当前设置面板。

## 视觉层级检查

代码与真实截图复核显示：四项检查作为首页主内容，操作与原因共置；390 px 下步骤区改为五列紧凑进度，隐藏冗长描述，首屏可见工作台。恢复入口使用 44 px 最小高度、可见焦点环和本地化文本；桌面与窄屏均避开顶部关闭提示。1440 px 下主辅层级清晰。尚未做像素级回归和自动对比度扫描。

## 长时间工作体验检查

代码层人工检查显示：检查项保持四行稳定结构，原因与操作共置，不使用不可关闭的动画，窄屏 CSS 改为纵向布局。30 秒连续阅读、实际滚动与焦点可见性未验证。

## 空 / 加载 / 错误 / 成功 / 禁用 / 破坏性状态

- 空状态：无检查数据时显示原因与“重新检查”。
- 加载状态：读取四项状态时显示“正在检查”并设置忙碌语义。
- 错误状态：API 失败显示具体错误与重试；完成前 API 失败不继续。
- 成功状态：项内显示“已就绪”，总体显示可完成。
- 禁用状态：忙碌、完成前未就绪、覆盖冲突等情形禁用主操作。
- 破坏性状态：覆盖旧模型配置需二次确认，可取消，确认前服务端不写文件或环境。

## 自动化检查运行结果

| 检查项 | 命令/工具 | 结果 | 备注 |
|---|---|---|---|
| 本轮定向 onboarding/config 套件 | `pytest -q tests/test_settings_persistence_concurrency.py tests/test_onboarding_static.py tests/test_onboarding_mvp.py` | 40 通过 | 1 个上游 `audioop` 废弃警告 |
| 全部 settings/onboarding 相关回归 | `pytest -q tests/test_*settings*.py tests/test_*onboarding*.py` | 213 通过 | 验证共享设置锁、现有设置页与首次启动回归；1 个上游警告 |
| JavaScript 语法 | `node --check static/onboarding.js` | 通过 | 当前 worktree |
| Python 语法 | `<agent-python> -m py_compile api/config.py api/onboarding.py tests/onboarding_workbench_browser_smoke.py tests/test_settings_persistence_concurrency.py` | 通过 | 使用当前 worktree agent venv |
| 真实浏览器烟测 | `TAIJI_ONBOARDING_SMOKE_SERVER_PYTHON=<agent-python> TAIJI_ONBOARDING_SMOKE_EVIDENCE_DIR=/private/tmp/taiji-onboarding-final-stable-20260804 <playwright-python> tests/onboarding_workbench_browser_smoke.py` | 连续 3/3 通过 | 三次退出码均为 0；mobile+desktop+keyboard+retry+conflict+completion，并覆盖自托管探测焦点/live 状态、空模型阻断和外部恢复焦点 |

## 问题列表

| 严重程度 | 问题 | 证据 | 建议修复方式 | 是否已修复 |
|---|---|---|---|---|
| P1 | 暂时关闭后没有当前页面内持久、可发现、键盘可用的恢复入口 | 旧实现只关闭对话框，恢复依赖刷新/下次启动 | 增加全局恢复入口、完成态同步隐藏、焦点回送和真实浏览器回归 | 是 |
| P1 | `save_settings()` 并发 RMW 会丢更新，completion 失败回滚/晚 commit 可覆盖后续状态或返回过期成功 | 可控线程交错测试在旧实现复现丢字段、复活完成和响应/磁盘矛盾 | 共享锁、原子替换、进程内 marker CAS、淘汰后权威重读 | 是 |
| P2 | 自动可访问性和视觉回归缺失 | 已有 Playwright 交互与截图，无 axe/Lighthouse/像素差分 | 在目标 QA 机补 axe 与视觉基线 | 否 |
| P2 | 移动端次要“全部重新检查”和单项重试按钮占用较多纵向空间 | 390 px 截图中次要操作均为整行，主 CTA 需继续滚动 | 后续视觉打磨时收敛次要操作宽度与密度 | 否 |
| P2 | 新工作台部分中文文案未纳入完整 i18n 字典，字段错误关联和 Codex OAuth 动态播报仍可加强 | 独立可访问性/功能复核 | 补齐 locale key、`aria-invalid`/`aria-describedby` 与 OAuth live region | 否 |

## 已修复问题

- 首次启动只有分步表单，没有销售交付所需的可见就绪检查。
- 完成 API 可在必要状态未就绪时写入完成标记。
- 已有配置覆盖的服务端拒绝未映射为 HTTP 409，前端无明确确认路径。
- 不支持 Provider/源码跳过路径会在 setup API 内直接持久化完成标记。
- “跳过”会混淆“暂时关闭”与“已完成”。
- 检查列表缺少 listitem 语义。
- 保存后的失败项返回配置步骤修改时，主按钮仍被旧 `savedOnce` 状态禁用。
- 覆盖确认仅做真值判断且存在检查/写入时间窗；现已要求字面布尔 `true` 并在凭据事务锁内二次检查。
- 初始状态失败时“可继续”文案与实际禁用态矛盾；现明确提示恢复前不能继续。
- 初始错误态焦点会被对话框默认焦点覆盖；现在对话框打开后的 animation frame 将焦点放到重试按钮。
- 初始状态读取失败后，顶部“全部重新检查”曾只刷新子检查，造成“显示就绪但继续禁用”；现已强制先恢复权威状态源，并有 Chromium 回归。
- 完成标记写入后若最终状态投影抛异常，曾可留下错误的完成标记；现已在保留原异常前执行 best-effort 补偿回滚，并覆盖异常路径。
- 390 px 首屏被步骤说明占满；现改为紧凑五列进度，首屏可见检查工作台。
- 暂时关闭后只能刷新或下次启动才能返回检查；现有持续可见的恢复入口，Escape/Enter 和完成态隐藏均有 Chromium 回归。
- `save_settings()` 曾在共享 JSON 上执行无锁 RMW、直接写文件并修改传入字典；现用共享 `RLock`、同目录原子替换和深拷贝输入。
- completion 曾可能由失败旧请求回滚新成功，或由显式重置后的旧 commit 复活，并返回与磁盘冲突的成功状态；现用 generation/token marker 做 commit/rollback CAS，被淘汰请求返回当前权威状态。
- 首轮恢复入口与右上角关闭提示 Toast 重叠；现移入安全区并加入真实布局几何断言。
- 自托管 Base URL 探测曾通过整页重渲染销毁输入焦点，且状态无 live region；现改为原位同步并完成 Chromium 回归。
- 浏览器烟测曾在对话框初始 `requestAnimationFrame` 聚焦完成前开始填写 URL，形成偶发误报；现显式等待标题获得初始焦点后再进入探测，严格 URL 焦点断言保持不变并连续 3/3 通过。
- 自托管 `/models` 返回空列表曾被判定成功并允许继续；现按探测失败处理并禁用“继续”。
- 从授权/安全失败项跳转设置页时焦点曾被全局恢复入口抢回；现显式关闭工作台且不恢复其焦点，再聚焦当前设置面板。
- 移动端初始焦点曾落到“继续”并把标题/步骤滚出首屏，按钮视觉顺序与 DOM 顺序相反；现以标题为初始焦点并统一顺序，同时补齐步骤 list/listitem/aria-current 语义。

## 剩余风险

- 浏览器烟测在浏览器边界使用稳定 API 夹具；它需与已通过的真服务 API 集成测试组合解读，单独不代表安装态端到端。
- 安装态授权和 `restricted/strict` 策略已有单元门禁，但尚无绑定当前 DEB 制品的麒麟/UOS 图形界面验收证据。
- 本轮 Chromium 使用 macOS 测试宿主，不能替代麒麟/UOS 中 Electron 字体、桌面缩放、窗口管理和图形库验收。
- completion token/generation 按设计不写入 `settings.json`、不进入 UI/API；当前官方运行链是单进程 `ThreadingHTTPServer`，因此进程内锁可覆盖请求线程。进程崩溃后不存在仍可回调的旧请求，重启后的公开完成态仍由持久标记与实时 readiness 联合判定。若未来改为多进程服务器，需升级为跨进程文件锁/数据库事务，此场景当前不在支持范围内。
- 工作台部分新增文案尚未完整接入多语言字典；字段级错误关联、Codex OAuth 动态状态播报和次要重试按钮的移动端密度仍是 P2 改进项。

## 未验证项目

- axe/Lighthouse 或等价的自动可访问性。
- 像素级或组件级视觉回归。
- 真实自然 Tab 全链、高对比/深色主题、125%–200% 缩放、1366×768 和离线横幅叠加场景。
- 麒麟/UOS 目标机安装态与 Electron 宿主集成。

## 后续建议

1. 将已通过的脚本纳入 Linux 制品候选版门禁，保存脚本输出、截图、worktree 路径和 HEAD。
2. 在麒麟/UOS 安装态补授权失效、安全策略错误、无网络、已有配置覆盖与恢复的原生界面验收。
3. 补 axe/Lighthouse 及稳定截图差分基线。
4. 合并后从正式 `main` 重新执行 213 项相关回归与 Chromium 烟测，再把同一门禁纳入 Linux 候选制品构建。
