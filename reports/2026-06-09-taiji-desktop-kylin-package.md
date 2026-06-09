# 太极 Agent 麒麟 x86_64 Electron DEB 打包实现报告

## 阶段定位

- 日期：2026-06-09
- 目标：把当前太极 Agent 完整前后端打成麒麟 x86_64 桌面 DEB，安装后通过开始菜单或桌面入口启动 Electron 桌面壳。
- 目标机事实：Kylin V10 SP1，`ID_LIKE=debian`，`uname -m=x86_64`，`dpkg --print-architecture=amd64`，glibc 2.31，`apt/apt-get/dpkg/systemctl` 可用。

## 本轮实现

- DEB 构建脚本继续拒绝 macOS 和非 x86_64/amd64 主机，最终包必须在 Linux amd64 构建。
- 构建脚本新增 Linux Electron runtime 校验：确认 Electron 是 Linux x86_64 ELF，并通过 `ldd` 检查缺失共享库。
- DEB `Depends` 扩展为覆盖 Electron 在 Kylin V10 SP1 上常见的 GTK、X11、DBus、CUPS、NSS、GBM、ALSA 等运行库。
- 构建脚本新增 desktop entry 校验、旧包归档、包树 secret/cache 扫描和 DEB 产物 macOS metadata 字符串扫描。
- 包树 secret 扫描区分公共 PEM 证书和私钥内容；`certifi/cacert.pem` 这类公共 CA 证书允许进入 venv，`BEGIN ... PRIVATE KEY` 仍拒绝发布。
- 安装脚本会修复 Electron `chrome-sandbox` root/setuid 权限，降低国产桌面 Electron 启动失败风险。
- 安装态 `/opt/taiji-agent/bin/taiji-native-verify` 会检查 Electron runtime、desktop entry、图标、共享库缺失，并支持 `TAIJI_VERIFY_DESKTOP_SMOKE=1` 图形会话 smoke test。
- 目标终端交付脚本会自动准备 `uv` 和现代 Node/npm；`setup-local.sh` 默认先使用锁文件同步，锁文件漂移时在构建工作区重试不带 `--locked` 的同步，避免目标机构建中途无 DEB 产物。
- 交付脚本在构建前清理旧 DEB 输出，构建成功后写入 `.build-success`；安装脚本只安装带有当前成功标记且 SHA256 匹配的 DEB。
- 安装脚本按用户最终选择改为旧 hermes-bwb WebUI 版彻底清除后安装新版：停止并禁用旧 WebUI/Gateway systemd 服务，清理 `/opt/taiji-agent` 相关旧进程，解除 hold，执行 `apt-get purge` 并用 `dpkg --remove` / `dpkg --purge` 收口旧包状态。
- 针对目标机反复出现的 `tar: opt/taiji-agent/runtime/hermes-home: file changed as we read it`，根因收敛为旧 WebUI/Gateway 持续写运行目录导致备份不可稳定完成。安装脚本不再备份旧 `/opt/taiji-agent`，而是删除旧系统安装、旧配置、旧服务和旧入口后再安装新版。
- 安装脚本会检查 `8787`、`18642`、`18787` 端口；只清理命令行明确指向 `/opt/taiji-agent` 的旧进程，遇到非太极 Agent 进程占用会停止安装并打印诊断。
- 旧版模型 Key、微信 token 和历史会话不再保留；普通用户家目录下的新版用户态目录不在旧系统安装清理范围内。
- 安装成功后双击启动失败的根因已收敛为 `venv/bin/hermes` 控制台脚本使用目标机构建工作区绝对 shebang；当交付目录路径包含空格时，Linux shebang 解析会把解释器路径截断。桌面启动脚本、`taiji` CLI、健康检查和 DEB 构建门禁已改为使用 `venv/bin/python -m hermes_cli.main`，不再依赖控制台脚本 shebang。
- Electron 启动失败弹窗会带最近脚本输出，安装态 `taiji-native-verify` 会提前验证 `python -m hermes_cli.main --help`，降低双击后才暴露启动链问题的风险。
- 新增非密默认产品配置模板，包内包含菜单显隐、默认主模型、图片模型和首页控件策略；启动时会同步到当前用户运行态，修复目标机菜单全显示、模型配置空白和首页快捷项不一致。
- Linux Electron 桌面端默认隐藏应用菜单栏，不再显示“太极 Agent / 视图”，保留麒麟原生窗口按钮。
- 新增 `/usr/bin/taiji-agent-diagnose` 和交付目录 `03_目标终端_导出诊断报告.sh`，可导出脱敏报告，包含包状态、端口、进程、桌面入口、配置摘要、WebUI API 状态和最近日志。
- 修复聊天附件链路：PDF、PPTX、DOCX、XLSX、TXT/MD/CSV 会在后端生成受控文本上下文；图片保留 native 多模态路径，并在 text/非视觉模式下给出明确视觉能力提示。前端不再把 `/home/...` 或 `/Users/...` 绝对路径拼进用户消息。
- 默认端口被非太极进程占用时只记录诊断，不阻断安装；新版桌面端会动态选择空闲本地端口。
- `taiji` CLI 默认使用与桌面端一致的用户态 `~/.local/share/taiji-agent/hermes-home`，避免界面配置模型后命令行仍读旧 `~/.hermes`。
- 安装包仍不内置模型 API Key、微信 token、企业微信 Secret、服务器地址或私钥。

## 状态边界

- 已实时验证：当前 macOS 源码态脚本静态检查和健康检查可在本机执行。
- 未实时验证：新版源码包在 Linux amd64 目标机重新构建、DEB 重装、目标机双击启动、窗口关闭后进程清理、真实模型对话。
- 历史线索：旧 `hermes-bwb` 的 `1kylin9` WebUI 包可复用失败经验，但不能替代本 Electron 完整包验收。

## 目标机构建/验收门槛

```bash
sha256sum -c taiji-agent_*.deb.sha256
bash ./02_目标终端_安装并验证.sh
/opt/taiji-agent/bin/taiji-native-verify
TAIJI_VERIFY_DESKTOP_SMOKE=1 /opt/taiji-agent/bin/taiji-native-verify
taiji --help
taiji-agent-diagnose
```

随后从开始菜单启动“太极 Agent”，确认首屏可用、重复双击只聚焦已有窗口、关闭窗口后本次 Agent/WebUI 进程退出。真实对话必须在目标机首启配置模型后再验收。
