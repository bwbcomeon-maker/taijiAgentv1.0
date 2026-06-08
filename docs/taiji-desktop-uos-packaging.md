# 太极 Agent 桌面 App 与统信/UOS 打包说明

## 当前实现

- 桌面壳：`apps/taiji-desktop`，Electron 启动本地 Agent API 和 WebUI，健康检查通过后加载本地页面。
- Runtime 脚本：`hermes-local-lab/scripts/runtime-env.sh` 统一处理开发态和安装态路径。
- 安装态用户目录：
  - 配置：`~/.config/taiji-agent/.env`
  - 数据：`~/.local/share/taiji-agent/hermes-home`
  - 工作区：`~/.local/share/taiji-agent/workspace`
  - 日志：`~/.local/state/taiji-agent/logs`
- 诊断命令：安装后为 `/opt/taiji-agent/bin/taiji-native-verify`，源码态为 `hermes-local-lab/scripts/taiji-native-verify`。

## 目标机事实采集

在决定 DEB/RPM/RUN 前，必须先在目标机执行：

```bash
cat /etc/os-release
uname -m
command -v apt apt-get dpkg dnf rpm yum systemctl
dpkg --print-architecture 2>/dev/null || true
rpm --eval '%{_arch}' 2>/dev/null || true
python3 --version 2>/dev/null || true
```

如果目标机有 `apt/apt-get/dpkg`，优先交付 `.deb`。海光目标必须是 `x86_64` 或 `amd64`。

## Linux 构建流程

最终 DEB 必须在 Linux x86_64/amd64 构建，不允许在 macOS 上产最终包。

```bash
cd /path/to/taiji-agentv1.0
cd hermes-local-lab
./scripts/setup-local.sh

cd ../apps/taiji-desktop
npm ci

cd ../..
TAIJI_AGENT_VERSION=0.1.0 ./packaging/linux/deb/build-deb.sh
```

产物位于：

```text
packages/麒麟操作系统安装包/taiji-agent_0.1.0_amd64.deb
packages/麒麟操作系统安装包/taiji-agent_0.1.0_amd64.deb.sha256
```

## 安装与验证

```bash
sha256sum -c taiji-agent_0.1.0_amd64.deb.sha256
sudo apt install -y ./taiji-agent_0.1.0_amd64.deb
/usr/bin/taiji-agent
/opt/taiji-agent/bin/taiji-native-verify
taiji --help
```

未配置模型 key 时，只能证明桌面壳、Agent API、WebUI 和本地端口链路可用，不能证明真实模型对话已完成。

## 状态边界

- 已实时验证：当前 macOS 源码态健康检查曾通过；本文件只描述实现和构建流程。
- 未实时验证：海光 x86_64 + 统信/UOS 实机安装、双击启动、真实模型对话、卸载重装。
- 历史线索：此前一键启动脚本和 `18642/18787` 端口经验只作为设计依据，不能替代目标机验收。
