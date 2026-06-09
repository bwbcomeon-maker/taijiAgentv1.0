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
- 安装态 `/opt/taiji-agent/bin/taiji-native-verify` 会检查 Electron runtime、desktop entry、图标、共享库缺失，并支持 `TAIJI_VERIFY_DESKTOP_SMOKE=1` 图形会话 smoke test。
- 安装包仍不内置模型 API Key、微信 token、企业微信 Secret、服务器地址或私钥。

## 状态边界

- 已实时验证：当前 macOS 源码态脚本静态检查和健康检查可在本机执行。
- 未实时验证：Linux amd64 构建、DEB 安装、目标机双击启动、窗口关闭后进程清理、真实模型对话。
- 历史线索：旧 `hermes-bwb` 的 `1kylin9` WebUI 包可复用失败经验，但不能替代本 Electron 完整包验收。

## 目标机构建/验收门槛

```bash
sha256sum -c taiji-agent_*.deb.sha256
sudo apt install -y ./taiji-agent_*_amd64.deb
/opt/taiji-agent/bin/taiji-native-verify
TAIJI_VERIFY_DESKTOP_SMOKE=1 /opt/taiji-agent/bin/taiji-native-verify
taiji --help
```

随后从开始菜单启动“太极 Agent”，确认首屏可用、重复双击只聚焦已有窗口、关闭窗口后本次 Agent/WebUI 进程退出。真实对话必须在目标机首启配置模型后再验收。
