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
- 安装脚本新增旧 hermes-bwb WebUI 版自动备份替换流程：先停止旧 WebUI/Gateway systemd 服务和 `/opt/taiji-agent` 相关旧进程，冻结旧运行态后再备份旧 `/opt/taiji-agent`、系统配置和旧服务文件；备份成功并通过 `tar -tzf` 校验后，才清理旧包状态和白名单内旧路径，最后安装 Electron 完整版。
- 针对目标机出现的 `tar: opt/taiji-agent/runtime/hermes-home: file changed as we read it`，根因收敛为旧 WebUI/Gateway 仍在写运行目录导致备份读写冲突。安装脚本现在会清理上次失败遗留的 `.tmp` 备份、备份失败自动重试一次；若仍失败，会保留旧安装，不执行 purge、删除或新版安装，并尽量恢复原本正在运行的旧服务。
- 安装脚本会检查 `8787`、`18642`、`18787` 端口；只清理命令行明确指向 `/opt/taiji-agent` 的旧进程，遇到非太极 Agent 进程占用会停止安装并打印诊断。
- 旧版备份保存在交付目录 `旧版备份/`，可能包含模型 Key、微信 token 或历史会话，只用于目标机本地排障，不进入新版 DEB。
- 安装包仍不内置模型 API Key、微信 token、企业微信 Secret、服务器地址或私钥。

## 状态边界

- 已实时验证：当前 macOS 源码态脚本静态检查和健康检查可在本机执行。
- 未实时验证：Linux amd64 构建、DEB 安装、目标机双击启动、窗口关闭后进程清理、真实模型对话。
- 历史线索：旧 `hermes-bwb` 的 `1kylin9` WebUI 包可复用失败经验，但不能替代本 Electron 完整包验收。

## 目标机构建/验收门槛

```bash
sha256sum -c taiji-agent_*.deb.sha256
bash ./02_目标终端_安装并验证.sh
/opt/taiji-agent/bin/taiji-native-verify
TAIJI_VERIFY_DESKTOP_SMOKE=1 /opt/taiji-agent/bin/taiji-native-verify
taiji --help
```

随后从开始菜单启动“太极 Agent”，确认首屏可用、重复双击只聚焦已有窗口、关闭窗口后本次 Agent/WebUI 进程退出。真实对话必须在目标机首启配置模型后再验收。
