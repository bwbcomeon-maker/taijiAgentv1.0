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
- 每次改打包链路后至少运行：`python3 -m unittest tests.test_linux_desktop_packaging_static tests.test_kylin_install_script_simulation`、相关 shell 脚本 `bash -n`、桌面主进程 `node --check apps/taiji-desktop/src/main.js`、新增/改动 Python 的 `python3 -m py_compile`，以及针对启动面文件的 Hermes token 静态扫描。
- 不得在 macOS 或非目标架构上声称最终 DEB 已验证。macOS 只能做源码修改、静态检查和部分本机 smoke；最终 DEB 必须在 Linux x86_64/amd64 的 Kylin/UOS/openKylin 类环境构建并安装验证。
- 验收结论必须分清：源码仓库 grep 仍可能看到 Hermes；重新构建并安装后的用户安装目录、普通启动入口、进程命令、环境变量、日志和诊断输出才是去 Hermes 化的验收对象。旧安装包不会自动变化，必须重新打包并重新安装。
- 若一次打包经过多轮目标机调试，调试成功并完成验证后，必须把新增经验同步回本项目 `AGENTS.md`。同步内容至少包括：实际问题根因、有效修复方式、必须保留的 release gate、目标机验收命令、仍需人工确认的风险；不要把未验证猜测写成长期规则。
