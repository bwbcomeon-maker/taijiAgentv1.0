# Taiji Agent 全栈审查报告

日期：2026-06-13
范围：`/Users/bwb/Documents/工作/taiji-agentv1.0`
方式：只读审查 + 本机静态检查 + 本机目标测试 + 本机 WebUI/Agent health + Playwright 桌面/移动 smoke。未调用真实模型或图像生成 API，未在 Linux/Kylin/UOS 目标机重新构建/安装 DEB。

## 证据边界

### 已实时验证

- 当前 HEAD：`892bf56 Improve expert team panel artifact interactions`。
- 当前服务监听：
  - Agent：`127.0.0.1:18642`，`/health` 返回 `{"status":"ok","platform":"taiji-agent"}`。
  - WebUI：`127.0.0.1:18787`，`/health` 返回 `status=ok`、`sessions=5`、`active_streams=0`。
- WebUI 授权状态：`/api/license/status` 返回 `status=valid`、`required=true`、`machine_matched=true`、剩余 29 天。
- WebUI 模型配置 API：`/api/model-config` 返回主模型 `deepseek/deepseek-v4-pro`，key 来源为 `env_file`；同时返回真实 `config_path=/Users/bwb/.local/share/taiji-agent/runtime-home/config.yaml`。
- Agent `/v1/toolsets` 与 `/v1/license/status` 在无认证 curl 下返回 401；本轮没有证明 live tool schema 或 Agent public license API payload。
- Playwright 桌面 smoke：`http://127.0.0.1:18787/?taiji_desktop=1` 状态 200，标题 `taiji Agent`，无 console/page error；首屏可见文本未命中 `Hermes`、`HERMES_`、`hermes-home`、`/Users/bwb`、`config.yaml`。
- Playwright 移动 smoke：状态 200，无 console/page error；但首屏实际为空白主区域，仅顶部栏可见。菜单按钮可点开会话抽屉。
- 关键截图：
  - `/tmp/taiji-audit-desktop.png`
  - `/tmp/taiji-audit-desktop-settings-attempt.png`
  - `/tmp/taiji-audit-mobile.png`
  - `/tmp/taiji-audit-mobile-menu-attempt.png`

### 本轮测试结果

- `python3 -m unittest tests.test_linux_desktop_packaging_static tests.test_kylin_install_script_simulation tests.test_taiji_license_issuer_gui`
  - 40 tests passed。
- WebUI：
  - `../hermes-agent/venv/bin/python -m pytest tests/test_webui_gateway_chat_backend.py tests/test_brand_privacy.py tests/test_issue1909_csrf_token.py tests/test_model_config_api.py tests/test_model_config_frontend.py tests/test_expert_team_frontend.py tests/test_ui_visibility_config.py -q`
  - 107 passed，1 个 `audioop` deprecation warning。
- Agent：
  - `scripts/run_tests.sh tests/test_taiji_license.py tests/gateway/test_api_server_license.py tests/gateway/test_sse_agent_cancel.py tests/tools/test_image_generation_readiness.py`
  - 39 tests passed。
- 静态检查：
  - 显式 shell/launcher 清单 `bash -n` 通过。
  - `find ... '*.js' | xargs node --check` 通过。
  - `python3 -m py_compile hermes-local-lab/scripts/sync-feature-visibility.py hermes-local-lab/scripts/sync-packaged-config.py hermes-local-lab/scripts/taiji_license_tool.py` 通过。
  - `npm run check` in `apps/taiji-desktop` 通过。
- 一次无效检查命令被排除：初始 `bash -n` 选择器过宽，把 `.py`、`.pyc`、`.tar.gz` 当 shell 文件执行，属于审查命令形状错误，不是源码失败。

### 未实时验证

- Linux x86_64/amd64 Kylin/UOS/openKylin 目标机构建、安装、双击启动、关闭清进程、升级/卸载、`/proc/<pid>/environ`。
- 最终 `.deb` 包内容、安装树 `/opt/taiji-agent`、实际进程命令行、目标机日志和诊断导出。
- 真实模型对话、真实图像生成、真实 `image_generate` schema 暴露、真实 provider quota/account。
- Agent `/v1/toolsets` live payload，因为当前无认证 curl 返回 401。

## 当前架构梳理

- 桌面壳：`apps/taiji-desktop/src/main.js` 负责解析 lab/root、分配端口、写用户态 `TAIJI_RUNTIME_HOME`、依次启动 `start-agent.sh` 和 `start-webui.sh`，等待 health 后加载 WebUI，`before-quit` 停本次 runtime。
- Runtime 脚本：`hermes-local-lab/scripts/runtime-env.sh` 统一开发态/安装态路径；安装态用户目录应为 `~/.config/taiji-agent`、`~/.local/share/taiji-agent/runtime-home`、`~/.local/state/taiji-agent`。
- WebUI：Python stdlib HTTP + vanilla JS，无构建链；主要入口是 `server.py`、`api/routes.py`、`static/index.html`、`static/ui.js`、`static/messages.js`、`static/panels.js`、`static/taiji-home.js`。
- Agent：API 主链路是 `taiji_runtime.main -> taiji_cli.main -> gateway run -> APIServerAdapter`；工具 schema 来自 `model_tools.get_tool_definitions()`；`image_generate` 是否暴露由 readiness `available` 决定，不等于 UI configured。
- 授权：WebUI `/api/chat/start` 和 Agent 执行端点都有 license guard；WebUI `/api/license/status` 已实时验证 valid，Agent 侧无认证接口未验证。
- 打包：`packaging/linux/deb/build-deb.sh` 做产品化 staging、sourceless Python、secret scan、Hermes token scan；macOS 只能做源码/静态/smoke，不能给目标机验证结论。

## 主要风险清单

### P1 - 移动端 Taiji 首页主区域不可见

现象：
- 390x844 Playwright 截图 `/tmp/taiji-audit-mobile.png` 只有顶部栏和空白主区域。
- 几何检查显示 `main.main`、`#mainChat`、`#composerWrap` 均存在 DOM 文本，但 `visible=false` 且 `bounding_box=null`。
- `.session-list` 有宽高但在 `x=-268`，不在可视主区域；点击菜单后只打开侧栏遮罩，主工作区仍不可用。

根因层级：
- 前端响应式布局问题。Taiji home 桌面化布局关键规则集中在 `static/style.css` 的 `@media (min-width:901px)`，移动断点没有对应恢复 `taiji-real-main/#mainChat/#composerWrap` 可见布局的规则。

影响面：
- 移动端或窄窗口无法直接输入消息或使用首页快捷操作。
- 当前 DOM 存在不能证明功能可用，必须用可见性/截图验收。

建议：
- 补移动端 Taiji home 布局规则，并增加 Playwright 移动 smoke：断言 `main.main/#mainChat/#composerWrap` 有可视 bounding box，首屏有 composer 或明确可操作入口。

### P1 - 交付清空对话脚本仍使用旧 `hermes-home`

证据：
- `taijiagent 打包交付/04_目标终端_清空对话记录.sh:39` 设置 `HERMES_HOME="$TAIJI_DATA_DIR/hermes-home"`。
- 同脚本 `:58` 输出 `Hermes home：...`，`:67` 输出 `$HERMES_HOME/config.yaml`。
- 当前 runtime-env 和桌面壳已经统一到 `runtime-home`。

根因层级：
- 旧交付脚本未跟随 `runtime-home` 迁移和去 Hermes 化 release gate 更新。

影响面：
- 普通用户可见日志继续暴露 Hermes。
- 在新安装态可能清理错误目录，导致“清空对话记录”无效，或者给用户错误的数据路径。

建议：
- 改为 `TAIJI_RUNTIME_HOME="$TAIJI_DATA_DIR/runtime-home"`，用户文案统一为“运行数据目录/本机数据目录”。
- 增加静态测试覆盖 `04_目标终端_清空对话记录.sh`：不得出现完整 `Hermes home`、`hermes-home`、`HERMES_HOME`。

### P1 - 会话导出文件名仍是 `hermes-{sid}.json`

证据：
- `hermes-local-lab/sources/hermes-webui/api/routes.py:11316` 设置 `Content-Disposition: attachment; filename="hermes-{sid}.json"`。

根因层级：
- WebUI 会话导出仍保留上游品牌默认命名，未纳入 Taiji 用户可见面扫描。

影响面：
- 用户导出会话时直接看到 Hermes 文件名，违反普通用户可见面去 Hermes 化规则。

建议：
- 改为 `taiji-session-{sid}.json` 或 `taiji-chat-{sid}.json`。
- 在 `tests/test_brand_privacy.py` 或包装层静态测试中加入 session export filename 断言。

### P1 - 目标机安装/操作文档存在旧入口和旧目录口径

证据：
- `docs/taiji-desktop-uos-packaging.md:10` 仍写数据目录 `~/.local/share/taiji-agent/hermes-home`。
- `docs/taiji-desktop-uos-packaging.md:76` 仍写 `python -m hermes_cli.main`，当前脚本已改为 `python -m taiji_runtime.main`。
- `taijiagent 打包交付/操作说明.md:173` 仍指导查看 `hermes-agent.log`。
- `taijiagent 打包交付/版本信息.txt:10` 仍写 `python -m hermes_cli.main` 和 `venv/bin/hermes`。

根因层级：
- 代码产品化后，交付文档没有同步更新。

影响面：
- 运维/客户侧按旧文档排障会走错路径或看到旧品牌。
- 文档若随交付目录发送，会直接破坏对外产品命名一致性。

建议：
- 把交付文档作为 release gate 的一部分扫描；文档中如需解释源码兼容层，只能放开发者说明，不放普通操作说明。

### P2 - WebUI 模型配置 API 返回真实 `config_path`

证据：
- `/api/model-config` 当前返回 `config_path=/Users/bwb/.local/share/taiji-agent/runtime-home/config.yaml`。
- 前端 `static/panels.js:8257-8259` 将 `modelConfigPath` 显示为“本机配置”，未把真实路径渲染到页面；本轮桌面可见文本也未命中真实路径。

根因层级：
- API schema 仍向前端返回调试/内部路径，前端做了隐藏/替换。

影响面：
- 页面当前不显示，但浏览器 DevTools、错误日志、第三方调用或未来 UI 改动可能暴露真实路径和 `config.yaml`。

建议：
- 对普通 UI API 改为返回 `config_scope` 或 `config_label`，真实路径只留给诊断接口，并由诊断接口脱敏输出。
- 增加测试：`/api/model-config` 的普通响应不得含绝对路径；如保留字段，至少前端不展示还不够。

### P2 - session chat stream generic exception 仍可能输出原始异常

证据：
- `hermes-local-lab/sources/hermes-agent/gateway/platforms/api_server.py:1737-1740` 在 session chat stream 失败时 `await queue.put(_event_payload("error", {"message": str(exc)}))`。
- `/v1/chat/completions` 流式路径已使用 `_public_agent_stream_error(str(exc))` 并输出 `finish_reason="error"`，对应测试已通过。

根因层级：
- 错误脱敏修复覆盖了 OpenAI chat completions 主路径，但 session chat stream 分支仍是直接异常字符串。

影响面：
- 如果该 stream 面向 WebUI 或第三方客户端，异常里可能包含 provider、路径、配置或旧 Hermes 文案。

建议：
- 统一复用 `_public_agent_stream_error()` 或同等脱敏函数。
- 增加 session chat stream raw exception 回归测试。

### P2 - WebUI/浏览器本地状态仍有大量 `hermes-*` key

证据：
- `static/messages.js`、`static/panels.js`、`static/i18n.js` 仍有 `hermes-webui-session`、`hermes-theme`、`hermes-skin`、`hermes-kanban-active-board`、`hermes-lang` 等 key。

根因层级：
- 为上游兼容和迁移保留旧 key；当前未形成“新写 Taiji key、兼容读取旧 key”的完整收口。

影响面：
- 普通用户如果打开浏览器存储或导出诊断，仍可能看到 Hermes 前缀。
- 全量直接改名有迁移风险，必须分层处理。

建议：
- 新 key 统一 `taiji-*`；兼容读取旧 key；写入时迁移到新 key。
- 诊断导出必须脱敏/改写旧 key。

### P2 - 工作区存在高敏未跟踪运行态目录

证据：
- `hermes-local-lab/runtime-home`：20M，包含 `config.yaml`、`state.db`、`response_store.db`、`kanban.db`、多份日志。
- `hermes-local-lab/sources/hermes-agent/.playwright-mcp`：564K。
- `output`：3.1M。
- 当前 `git status --short` 显示这些目录未跟踪。

影响面：
- 误用 `git add .` 可能把运行态、日志、截图或本地会话数据混进提交。

建议：
- 明确忽略当前运行态目录和审查输出目录；本轮不直接清理，因为它们是既有用户状态。
- 后续提交只用显式路径 stage。

### P3 - 产品文案存在 mixed casing

证据：
- 页面标题为 `taiji Agent`。
- 桌面首屏 composer placeholder 为 `输入消息给 taiji Agent...`。

影响面：
- 不影响功能，但对外产品感不一致。

建议：
- 统一为“太极智能体”或“太极 Agent”，避免 lowercase English 混用。

## 正向结论

- 本轮重点自动化测试均通过，说明当前授权、WebUI gateway chat 错误映射、CSRF、品牌隐私、模型配置、专家团输入态、UI visibility、Agent license/API/SSE/image readiness 的主要已有回归仍在。
- WebUI 设置页没有把 `/api/model-config` 返回的真实 `config_path` 直接渲染到桌面首屏或设置页可见文本。
- 桌面首屏当前没有可见 Hermes、真实路径、`config.yaml` 命中。
- `taiji-image -> openai-codex` 前端别名和后端内部 provider 分层在源码中存在，图像生成 readiness 目标测试通过。
- 授权状态当前在 WebUI 代理层实时显示有效、机器码匹配。

## 建议修复顺序

1. 修移动端 Taiji home 可见布局，并补 Playwright 移动断言。
2. 修 `04_目标终端_清空对话记录.sh` 的 `runtime-home` 和用户文案，并加静态 release gate。
3. 修 session export 文件名 `hermes-{sid}.json`。
4. 修 session chat stream 原始异常输出，统一脱敏。
5. 更新交付文档和版本信息的旧入口/旧目录/旧日志名。
6. 收口 `/api/model-config` 普通响应中的真实 `config_path`。
7. 制定 localStorage key 迁移方案：新写 `taiji-*`，兼容读旧 key。
8. 补 `.gitignore` 或提交规范，防止运行态目录、Playwright 快照、`output/` 误提交。

## 当前工作区状态

本轮报告生成前，工作区已有以下非本轮源码改动/运行态产物；本轮未覆盖：

- Modified：
  - `docs/product-benchmark/国网日常办公智能化应用试用评价体系-建议稿.docx`
  - `docs/product-benchmark/智能体类产品试用评价体系-建议稿.docx`
- Untracked：
  - `hermes-local-lab/runtime-home/`
  - `hermes-local-lab/sources/hermes-agent/.playwright-mcp/`
  - `hermes-local-lab/sources/hermes-agent/ai智能体_公众号长文.md`
  - `hermes-local-lab/sources/hermes-agent/hn-snapshot.md`
  - `output/`

本报告新增：`reports/2026-06-13-taiji-agent-full-audit.md`。
