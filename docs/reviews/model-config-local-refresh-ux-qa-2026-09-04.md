# 前端 UX QA 报告：模型配置本机状态刷新

状态：源码及隔离验收通过；安装态带限制，麒麟新包交互尚未验证。

## 范围、身份与变更记录

- 用户目标：麒麟安装版的“刷新本机状态”应产生可见结果，不把未编辑页面误判为草稿。
- 本轮修复位于正式物理仓库 `/Users/bwb/Documents/工作/taiji-agentv1.0`，Git common dir 为该仓库 `.git`；分支 `main`，起点 `a321f7951d78d9956bbf900f41c609de2d91ea55`，修改前 clean。既有 prunable worktree 未操作。
- 唯一写入者为主代理；独立子代理只读审查刷新、回执及 Provider 共用缓存影响，暂存后的最终审核按项目 Sol 门禁执行。
- 修改：Provider 预加载改为受并发/草稿保护的缓存与表单同步渲染；无草稿再次进入模型页重新读取；默认选项不算用户编辑；本机刷新独立于图片忙碌状态并显示进度/部分结果/取消/失败；保存回执核对保留新编辑；主模型连接检查消费 HTTP 请求体并关闭拒绝请求。
- 未修改模型端点选择规则、凭据存储、安装包、麒麟配置或服务。不会通过本机刷新调用远程 Provider。
- WebUI 原 `CHANGELOG.md` 已超过安全扫描单文件 1 MiB 门限。本轮撤回对该历史文件的追加，不拆分历史、不放宽扫描；本节为独立变更记录，行为合同同步更新于 `hermes-local-lab/sources/hermes-webui/docs/rfcs/provider-failure-and-model-verification-contract.md`。

## 功能契约

依据用户故障报告及现有模型状态合同，角色均为本机配置用户。

| 能力 / 契约依据 | 入口 | 数据/API | UI 与反馈 | 错误 / 空 / 加载 / 禁用状态 | 键盘与可访问性 | 浏览器 / 回归证据 | 状态 |
|---|---|---|---|---|---|---|---|
| 本机状态刷新，不作远程验证 | 设置 → 模型配置 → 刷新本机状态 | GET model-config、image-capabilities | 原有按钮；附近持续状态文本 | 成功、HTTP 失败、重试、图片忙碌部分完成；自身请求防重复 | 原生 button、已有 aria-label、aria-busy、aria-live；Enter 重试 | 三视口实际点击；Node 并发回归 | 通过 |
| 未编辑页面显示当前模型 | 同上；从提供商页预加载 | 共享配置与表单初始值 | 已配置与未验证仍明确区分 | 空能力的默认选项不产生假草稿；旧预加载不得覆盖较新状态 | 保留原有字段标签和摘要 | Node 首次/再次进入/延迟预加载；真实 DOM 默认值 | 通过 |
| 丢弃编辑必须确认 | 同上刷新入口 | 当前表单及未保存输入 | 取消有文字反馈；确认后才丢弃 | 模型、密钥草稿跨刷新取消保留 | 取消按钮初始焦点；键盘可激活主操作 | 三视口输入、取消与确认 | 通过 |
| 保存结果待核对时保留后续草稿 | 同一刷新入口 | 原保存 receipt，只 GET、不重放 POST | 服务器摘要更新，新草稿仍在编辑区并提示未保存 | 点击前/核对期间的新编辑均保留 | 不强制关闭编辑区 | Node 两条精确回归；已有端点 smoke 复验 | 通过（新草稿竞态为代码执行测试） |
| 检查连接后仍能读取本机状态 | 原检查连接按钮对应 POST seam | 有限长 body；CSRF 与 I/O 拒绝关闭连接 | 不变更原远程检查语义 | 正常/非法 JSON、空体、CSRF、非法/负数/超长长度 | UI 未变 | 真实 BaseHTTPRequestHandler 内存连接连续 POST + GET | 通过（HTTP 层） |

## 验证记录与证据边界

已实时验证：

1. RED：最初聚焦用例 `13 failed, 21 passed`，其中 POST 后 GET 从 200 变为 501，与安装态日志中的 `{}GET` 相符；预加载及按钮反馈失败可重复。回执新草稿另有 `2 failed` 的 RED。真实 DOM 补证默认图片选项仍会误判草稿，之后修正比较基准。
2. 聚焦 GREEN：`test_model_config_refresh.py`、`test_main_model_verification.py`、`test_model_config_frontend.py` 共 `147 passed, 1 warning`。警告为 Python `audioop` 弃用，非本轮失败。日志：`/private/tmp/taiji-model-refresh-focused.log`。
3. 新增真实浏览器：`tests/model_config_refresh_browser_smoke.py`，系统 Python 3.13 已有 Playwright + headless Chromium；只提供仓库静态资源，所有 API 为假响应，外部请求禁止，无真实配置读取/写入。1280×900、768×900、390×844 均 PASS，`page_errors=[]`；主模型/图片 POST 为零。设置页既有 `/api/auth/passkeys` 列表读取 POST 也被模拟，不是凭据写入。
4. 浏览器日志：`/private/tmp/taiji-model-refresh-browser.log`；最终截图目录：`/private/var/folders/5h/f2nd43h57ll1gn08pqvv722c0000gn/T/taiji-model-refresh-browser-ibtplgxw`。每个视口包含 initial、image-busy、failure、success；图中授权状态不可用来自空授权 fixture，不是终端授权结论。
5. 既有 `tests/provider_endpoint_authority_browser_smoke.py` 在最终源码上三视口复验 exit 0；替换其原先断言“假草稿出现”的期望，改为首次正常显示及刷新成功。证据 `/private/tmp/task9-browser-evidence-sy6og0xy`，日志 `/private/tmp/taiji-endpoint-refresh-browser-final.log`。自定义地址、保存、错误恢复、Provider 展开仍通过。
6. 完整离线门禁：`scripts/verify.sh --full`。首轮在 1319 项 root 测试中只有验证计划快照一项失败（运行开始时已导入旧测试期望，而新增入口在运行期间接入）；最新合同单独复验通过后，冻结代码重新执行完整门禁，最终 `exit_code=0`、`verification: PASS`。日志 `/private/tmp/taiji-model-refresh-full-final.log`；新增刷新及 HTTP 连接回归已加入 WebUI 校验清单。
7. 门禁计划精确合同同步加入两个回归入口；`tests/test_solo_development_workflow.py` 的 `43 tests` 全部通过，日志 `/private/tmp/taiji-refresh-workflow.log`。本轮最终安全扫描 PASS，未修改扫描规则。

测试解释器：Agent venv Python 3.11；命令 PATH 首位为已准备的 Node `v24.19.0`，不改全局 Node 链接、不安装依赖。源码路径下测试使用临时状态目录和回环端口；默认浏览器、真实 OAuth/Provider、麒麟服务均未启动或修改。

## 页面与可访问性检查

- 主内容仍为当前生效主模型；授权与图片能力为辅助信息，端点和编辑内容保持既有展开结构。本轮不改布局和视觉设计。
- 状态出现在刷新按钮下方，并通过已有 `aria-live="polite"` 输出；图片忙碌时按钮仍可用，自身刷新期间禁用且标记 busy，结束后恢复。
- 已实际检查：按钮可见可点、错误不是仅靠颜色表达、Enter 重试、确认框初始焦点在取消、取消和确认路径、三视口滚动与截图。
- 截图人工审查：桌面部分成功、窄屏失败反馈均可见，没有新增遮挡主操作；状态沿用既有小号提示样式。本轮未声称完成全站可访问性或一小时舒适度验收。

## 问题与剩余风险

已修复本轮确认的 P1：假草稿、图片忙碌拦截主刷新、失败缺少持久提示、HTTP 请求残留、待核对保存覆盖新草稿。预加载整表同步还增加了基线/generation/保存状态保护，避免迟到响应回退新状态。

未验证：自动化 axe、独立截图像素回归、长时间使用体验；实际麒麟安装包内交互、真实 Provider/模型请求、安装/升级和离线交付。先前麒麟只读日志与安装文件检查只证明旧安装态原因线索，不能代替本次修改后的目标机验收。

本轮交付为源码修复。后续需要另行授权新候选制包、安装及麒麟真实点击验收，才能确认终端问题闭环。

## 最终门禁回执

- root：`1319 tests`，`OK (skipped=2)`；不将跳过项表述为已验证。
- Desktop：79 passed；DOCX：278 passed；Agent：220 passed。
- WebUI：951 passed；branding：24 passed；Agent bootstrap：12 passed；WebUI bootstrap：69 passed；coexistence：6 passed。
- 全量安全检查、shell/JS 检查及组件 lint 通过。除最终回执文档外，复验期间实现和测试保持冻结。
- Sol 已审查前一暂存候选，未发现阻断问题；本回执追加后必须对完整新 staged bytes 重新取得 Sol 审核，方可 commit/push。实际提交与推送结果以本次任务的最终执行回执为准。
