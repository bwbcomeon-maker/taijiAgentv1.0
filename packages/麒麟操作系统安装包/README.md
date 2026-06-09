# 太极 Agent 国产终端安装包交付目录

最终推荐安装包放在本目录。当前目标机已确认为 Kylin V10 SP1、Debian-like、`x86_64/amd64`、glibc 2.31，因此本轮只交付 `amd64 .deb`，不交付 RPM。

当前源码提供 DEB 构建脚本：

```bash
TAIJI_AGENT_VERSION=0.1.0 ./packaging/linux/deb/build-deb.sh
```

注意：

- 最终安装包必须在 Linux x86_64/amd64 构建，不能在 macOS 上产最终 DEB。
- 构建前必须在 Linux 构建机完成 `hermes-local-lab/scripts/setup-local.sh` 和 `apps/taiji-desktop/npm ci`，确保包内包含 Linux Python venv 和 Linux Electron runtime。
- 默认只给主 `.deb` 生成 `.sha256`；旧版本包应移动到 `旧版本归档/`，避免误装。
- 安装包不内置模型 API Key、微信 token、企业微信 Secret、服务器地址或私钥。
- 只有在目标机完成 `sudo apt install`、`/opt/taiji-agent/bin/taiji-native-verify`、双击图标启动、关闭窗口清理进程、首启配置模型后的真实对话验证后，才能标记为“目标机已验证”。
