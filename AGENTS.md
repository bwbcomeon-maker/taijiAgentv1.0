默认中文回答；
不刻意迎合，要实事求是；
遇到 bug 不只修当前点，要做根因分析和影响面分析；
没有验证不能说完成；
状态类问题必须区分“已实时验证 / 未实时验证 / 历史线索”；
修改代码前先理解现有结构，优先最小改动；
修复后必须给出验证结果、风险和下一步。

## Git hygiene

- 代码改动前先运行 `git status --short`，确认当前工作区状态。
- 如果已有未提交变更，先判断是否属于当前任务；无关变更不得覆盖，必须先收口、提交或隔离。
- 每次代码任务完成并验证通过后，必须及时创建本地 commit；不默认 push，除非用户明确要求或任务明确需要。
- 最终回复必须给出本轮 commit hash 和 `git status --short` 的干净状态；若无法保持干净，必须说明具体剩余路径和原因。

## 去 Hermes 化打包规则

本项目面向普通用户交付时，必须把“用户安装包和普通运行面不暴露 Hermes 痕迹”作为固定 release gate。以后每次做 Linux/Kylin/UOS 打包、安装脚本、桌面壳启动链、诊断脚本或运行时目录调整，都必须遵守以下规则，不需要用户重复提醒。

- 区分三层：开发源码层可以保留上游兼容命名；对外启动层和安装包层必须使用 Taiji 命名。不要为了去痕迹机械替换全仓所有 `hermes` 字符串，这会破坏内部兼容和上游代码；应只收紧普通用户可见层和打包副本。
- 最终安装包不得原样复制整个 `hermes-local-lab` 或 `sources/hermes-agent` / `sources/hermes-webui` 到 `/opt/taiji-agent`。必须通过产品化 staging 生成 `runtime/agent`、`runtime/web`、`scripts`、`config`、`resources/icons` 等安装目录。
- 普通用户可见入口必须使用 `taiji` / `taiji-agent` / `taiji_runtime.main` / `TAIJI_*` / `agent.log` / `web.log` / `agent.pid` / `web.pid` 等产品命名；不得在 launcher、`.desktop`、诊断脚本、验证脚本、启动日志、进程命令行、`/proc/<pid>/environ` 可见环境变量中暴露 `Hermes`、`HERMES_*`、`hermes_cli`、`hermes-agent`、`hermes-webui`、`hermes-home`。
- 打包构建必须保留并执行产品隐私扫描 gate：安装树中普通文本和可见路径不得命中 `hermes|Hermes|HERMES_|hermes_cli|hermes-agent|hermes-webui|hermes-home`。许可证、第三方合法声明如确需保留，应隔离在 `licenses/` 等明确目录，并在扫描规则中显式豁免。
- 打包副本里的 Python 运行时代码应优先通过产品化目录、模块别名、sourceless 编译、删除 editable install 元数据来降低普通文件查看和 grep 命中；不得把构建机绝对路径、`.env`、`config.yaml` 真实路径、API key、token、私钥、历史会话或本地日志打进包。
- Web 静态资源、前端 localStorage key、CSRF header、Server header、诊断输出和错误提示属于普通用户可见面。打包副本需要做一致的 Taiji 命名改写，改完必须保证前后端协议仍一致。
- 网关/模型错误链路属于普通用户可见面。Agent API server 的流式 agent 异常不得被吞成空的 `finish_reason: stop`，必须输出 `finish_reason: error` 和脱敏产品错误；WebUI 必须识别 SSE 顶层 `error`、`finish_reason: error`、空回复三类情况，并统一映射为 Taiji 文案，不得暴露旧环境变量、旧命令或 Hermes 文案。
- 旧版本运行态清理属于打包升级 release gate。`stop-all.sh` 必须同时处理新 `agent.pid` / `web.pid` 和旧 pid 文件，但旧文件名、旧模块名只能用拆分字符串在运行时拼接，不能把完整旧 token 写回启动面；杀进程前必须校验命令行属于当前 lab 或 `/opt/taiji-agent`，非 Taiji 端口占用只报告不杀。
- Web 前端状态 key 迁移必须新写 Taiji key、兼容读取旧 key。CSRF 主头使用 `X-Taiji-CSRF-Token`，服务端可兼容旧头；localStorage 可用兼容层把旧前缀读写重定向到 `taiji-*`，但最终安装树仍以打包副本隐私扫描为准。
- 每次改打包链路后至少运行：`python3 -m unittest tests.test_linux_desktop_packaging_static tests.test_kylin_install_script_simulation`、相关 shell 脚本 `bash -n`、桌面主进程 `node --check apps/taiji-desktop/src/main.js`、新增/改动 Python 的 `python3 -m py_compile`，以及针对启动面文件的 Hermes token 静态扫描。
- 每次触碰上述错误链路或旧进程清理，还要补跑对应回归：WebUI `tests/test_webui_gateway_chat_backend.py`、`tests/test_brand_privacy.py`、`tests/test_issue1909_csrf_token.py`；Agent venv 下 `tests/gateway/test_sse_agent_cancel.py`、`tests/gateway/test_api_server.py`。若系统 Python 缺少项目依赖，应使用 `hermes-local-lab/sources/hermes-agent/venv/bin/python` 跑 Agent 测试。
- 不得在 macOS 或非目标架构上声称最终 DEB 已验证。macOS 只能做源码修改、静态检查和部分本机 smoke；最终 DEB 必须在 Linux x86_64/amd64 的 Kylin/UOS/openKylin 类环境构建并安装验证。
- 验收结论必须分清：源码仓库 grep 仍可能看到 Hermes；重新构建并安装后的用户安装目录、普通启动入口、进程命令、环境变量、日志和诊断输出才是去 Hermes 化的验收对象。旧安装包不会自动变化，必须重新打包并重新安装。
- 若一次打包经过多轮目标机调试，调试成功并完成验证后，必须把新增经验同步回本项目 `AGENTS.md`。同步内容至少包括：实际问题根因、有效修复方式、必须保留的 release gate、目标机验收命令、仍需人工确认的风险；不要把未验证猜测写成长期规则。

### 2026-06-16 Kylin V10 SP1 离线交付已确认经验

本轮最终状态：用户已确认最新离线交付包在 Kylin V10 SP1 x86_64/amd64 终端完成制包、安装部署并可正常使用。以后维护同一条交付链路时，把下面规则当作已验证 release gate。

- 离线交付目录必须只保留当前部署相关文件：`00_制包机_生成离线交付包.sh`、`02_目标终端_安装并验证.sh`、`03_目标终端_导出诊断报告.sh`、`操作说明.md`、`版本信息.txt`、`SHA256SUMS.txt`、当前 `taiji-agentv1.0-kylin-build-src-<hash>.tar.gz`，以及制包后生成的 `生成的安装包/`、`离线依赖/`、`构建日志/`。不要保留历史源码包、旧 DEB、旧清理脚本或清空对话 `.desktop`，避免现场误用。
- `SHA256SUMS.txt` 中源码包条目必须写成 `hash  taiji-agentv1.0-kylin-build-src-<hash>.tar.gz` 这种 basename 形式；制包脚本也必须兼容误带 `taijiagent 打包交付/` 前缀的旧格式，并在校验成功后自动归一化。不要在目标机脚本里直接 `sha256sum -c SHA256SUMS.txt` 依赖原始路径。
- 目标 Kylin 上的 `awk` 可能不支持 `[[:xdigit:]]{64}` 这类重复次数正则。交付 shell 中解析 checksum 时必须用 `length(hash) == 64` 加十六进制字符判断，或使用更保守的 POSIX 写法；每次改 `00_制包机_生成离线交付包.sh` 都要有真实 shell 级测试覆盖带中文/空格目录前缀的 checksum 行。
- 本地 apt 离线仓库不能直接把含中文和空格的交付目录写进 `file:` 源。`02_目标终端_安装并验证.sh` 必须把 `离线依赖/` 映射或 symlink 到 `/tmp/taiji-agent-offline-repo.*` 这类无空格 ASCII 路径，再执行 `apt-get update/install`。
- 打包副本不能整包排除 `plugins/`。Agent 初始化和 CLI/WebUI 对话需要 `plugins.memory`、`plugins.context_engine` 等模块；`taiji-native-verify` 必须实际 import `taiji_runtime.main`、`plugins`、`plugins.memory`、`plugins.context_engine`。只排除本轮桌面运行不需要且会触发旧品牌路径/文本扫描的插件目录。
- 打包副本必须排除开发/构建元数据和模板：`.env.example`、`*.example`、`uv.lock`、`pyproject.toml`、`package*.json`、editable install metadata、egg-info、上游 helper scripts、Docker/website/docs/tests 等。否则会触发隐私扫描，或把构建机路径、旧品牌文本、模板变量打进安装树。
- 授权问题要先区分“导入成功”和“runtime 公钥校验成功”。签发器私钥对应的 public key 必须和 `taiji_license.py` 默认公钥一致，并用测试证明签发器生成的 license 能被 runtime 验为 `valid`。
- DeepSeek V4 配置里 `reasoning_effort=max` 是有效链路；共享解析器、CLI、Gateway `/reasoning` 命令和 DeepSeek provider 都必须支持 `max`，不要把它降级成 unknown/default。
- 每次生成新源码包后，必须删除交付目录内旧的 `taiji-agentv1.0-kylin-build-src-*.tar.gz`，重新生成 `SHA256SUMS.txt`，并在最终回复里明确当前 hash；现场必须整体拷贝最新交付目录，不要混用旧脚本和新源码包。
- 目标机验收命令至少包括：在制包机运行 `bash ./00_制包机_生成离线交付包.sh`；在目标机断网或按离线条件运行 `bash ./02_目标终端_安装并验证.sh`；运行 `/opt/taiji-agent/bin/taiji-native-verify`；运行 `taiji --help` 和一次真实 `taiji` CLI 对话；双击“太极 Agent”完成 WebUI 对话、关闭窗口后确认本次 Agent/WebUI 进程退出；必要时运行 `bash ./03_目标终端_导出诊断报告.sh`。
- 当前仍需人工确认的风险：若目标机更换 apt 源、Kylin 小版本、CPU 架构、图形会话或模型服务/API key，必须重新跑上述验收；旧安装包、旧运行日志、旧诊断报告不会因为源码修复自动变成新结果。

## 前端 UX QA gate

任何涉及前端、UI、UX、页面、组件、布局、样式、交互、表单、列表、表格、导航、弹窗、可访问性、浏览器测试、截图、视觉优化或功能完整性的任务，都必须显式使用 `$frontend-ux-qa`。

前端任务不能只以“代码能编译”作为完成标准。若代码中存在用户可感知能力但页面没有可见、可发现、可访问的 UI 入口，至少标记为 P1；主流程被阻塞时标记为 P0。没有执行的浏览器测试、截图测试、可访问性自动化或视觉回归必须写为“未验证”，不得写成“通过”。
