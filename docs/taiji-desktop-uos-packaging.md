# 太极 Agent 桌面 App 与统信/UOS 打包说明

## 当前实现

- 桌面壳：`apps/taiji-desktop`，Electron 启动本地 Agent API 和 WebUI，健康检查通过后加载本地页面。
- Runtime 脚本：`hermes-local-lab/scripts/runtime-env.sh` 统一处理开发态和安装态路径。
- 安装态用户目录：
  - 配置：`~/.config/taiji-agent/.env`
  - 试用授权：`~/.config/taiji-agent/license.jwt`
  - 数据：`~/.local/share/taiji-agent/hermes-home`
  - 工作区：`~/.local/share/taiji-agent/workspace`
  - 日志：`~/.local/state/taiji-agent/logs`
- 诊断命令：安装后为 `/opt/taiji-agent/bin/taiji-native-verify`、`/usr/bin/taiji-agent-diagnose`，源码态为 `hermes-local-lab/scripts/taiji-native-verify` 和 `hermes-local-lab/scripts/taiji-agent-diagnose`。

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

## 本轮目标机事实

用户已在目标机确认：

- 系统：Kylin V10 SP1，`ID_LIKE=debian`
- 架构：`uname -m` 为 `x86_64`，`dpkg --print-architecture` 为 `amd64`
- 包管理器：`apt`、`apt-get`、`dpkg`、`systemctl` 可用；未发现 `rpm/dnf/yum`
- Python：系统自带 Python 3.8.10；最终包不依赖系统 Python 运行应用
- glibc 2.31：`ldd (Ubuntu GLIBC 2.31-0kylin9.1k2.14) 2.31`
- 资源：根分区、`/opt`、`/home` 空间和内存余量满足离线完整包安装

因此本轮锁定交付 `amd64 .deb`。不做 RPM，不交付浏览器版 WebUI 包。

## Linux 构建流程

最终 DEB 必须在 Linux x86_64/amd64 构建，不允许在 macOS 上产最终包。构建策略为离线优先：包内预置 Linux Electron runtime、Agent Python venv、WebUI/Agent 源码和运行脚本。

面向目标终端的一键交付优先使用根目录下的 `taijiagent 打包交付/01_目标终端_构建安装包.sh`。该脚本会自动校验源码包、准备 `uv`，并在系统 Node/npm 过旧时使用交付目录内的隔离 Node.js Linux x64 构建工具，避免 Kylin V10 源里的 Node.js 10 / npm 6 无法处理 lockfile v3。

手动构建时执行：

```bash
cd /path/to/taiji-agentv1.0
cd hermes-local-lab
./scripts/setup-local.sh

cd ../apps/taiji-desktop
npm ci

cd ../..
TAIJI_AGENT_VERSION=0.1.0 ./packaging/linux/deb/build-deb.sh
```

构建脚本会拒绝以下情况：

- 在 macOS 或非 x86_64/amd64 主机上构建最终包。
- Electron runtime 不是 Linux x86_64 ELF，或 `ldd` 显示缺少共享库。
- 包内出现 `.env`、私钥、macOS metadata、`__pycache__`、`*.pyc`。公共 CA 证书类 PEM 可进入 Python venv，但 PEM/证书文件内容中出现 `BEGIN ... PRIVATE KEY` 会拒绝发布。
- 包内出现客户 `license.jwt` 或其他 `*.jwt` 授权文件。试用授权必须通过交付目录预置或设置页导入，不进入 DEB。
- 默认非密产品配置模板缺失、字段不完整，或 YAML 实际字段里出现敏感凭据形态。
- DEB 产物字符串中出现 `LIBARCHIVE`、`com.apple`、`PaxHeaders`、`SCHILY.xattr` 等历史失败标记。

`hermes-local-lab/scripts/setup-local.sh` 默认先执行 `uv sync --extra all --locked`。如果目标机构建工作区的 `uv.lock` 与当前 `pyproject.toml` 再次漂移，会打印警告并在该构建工作区内重试不带 `--locked` 的同步；需要强制锁文件校验时设置 `TAIJI_UV_LOCK_MODE=strict`。

目标终端交付脚本在每次构建前清理 `生成的安装包/` 旧输出，构建成功后写入 `.build-success`。安装脚本必须看到该成功标记并校验当前 DEB 的 SHA256，才会执行 `sudo apt install`，避免构建失败后误装历史残留包。

桌面启动链不直接执行 `sources/hermes-agent/venv/bin/hermes` 控制台脚本。目标机交付目录可能包含空格或中文路径，venv 控制台脚本的绝对 shebang 在 Linux 上会被空格截断；因此 `start-agent.sh`、`/usr/bin/taiji`、`health-check.sh` 和 DEB 构建门禁统一使用 `sources/hermes-agent/venv/bin/python -m hermes_cli.main`。安装态 `taiji-native-verify` 也会提前验证这个模块入口，避免安装成功但双击后 Agent 启动失败。

如果目标机已经安装过旧 hermes-bwb 浏览器 WebUI 版 `taiji-agent`，新版交付不按两个产品并存处理。`02_目标终端_安装并验证.sh` 会停止并禁用旧 `taiji-agent-webui.service` / `taiji-agent-gateway.service`，清理命令行明确指向 `/opt/taiji-agent` 的旧进程，解除 `taiji-agent` hold 状态，并通过 `apt-get purge`、`dpkg --remove --force-remove-reinstreq`、`dpkg --purge --force-all` 收口旧包状态。只有旧包状态清理干净后，脚本才会删除白名单内旧路径并安装 Electron 完整版；如果旧包状态仍残留，脚本直接失败，不安装新版。旧 `/opt/taiji-agent`、旧系统配置、旧 systemd unit、旧命令入口和旧桌面入口会被删除，不再备份旧模型 Key、微信 token 或历史会话；普通用户家目录下的新版用户态目录不在清理范围内。默认端口被非太极进程占用时只记录诊断，不阻断安装，因为桌面端会自动选择空闲本地端口。

安装包内置 `hermes-local-lab/config/taiji-default-config.yaml` 作为非密产品配置模板。启动时同步菜单显隐、默认模型和图片模型展示；已有用户配置时只补空值，不覆盖用户已配置的密钥。Linux 桌面端默认隐藏 Electron 应用菜单栏，保留麒麟原生标题栏。

产物位于：

```text
packages/麒麟操作系统安装包/taiji-agent_0.1.0_amd64.deb
packages/麒麟操作系统安装包/taiji-agent_0.1.0_amd64.deb.sha256
```

## 安装与验证

```bash
sha256sum -c taiji-agent_0.1.0_amd64.deb.sha256
bash ./02_目标终端_安装并验证.sh
/opt/taiji-agent/bin/taiji-native-verify
TAIJI_VERIFY_DESKTOP_SMOKE=1 /opt/taiji-agent/bin/taiji-native-verify
/usr/bin/taiji-agent
taiji-agent-diagnose
taiji --help
```

如果要预置试用授权，把我方签发的 `license.jwt` 放在 `02_目标终端_安装并验证.sh` 同目录，或执行时设置 `TAIJI_LICENSE_SOURCE=/path/to/license.jwt`。安装脚本会复制到 `~/.config/taiji-agent/license.jwt` 并设置为 `0600`；没有授权文件时安装不失败，但首次执行 Agent 对话会提示授权缺失。

安装后从开始菜单双击“太极 Agent”。Electron 会以当前桌面用户启动本地 Agent API 和 WebUI，运行目录位于 `~/.config/taiji-agent`、`~/.local/share/taiji-agent`、`~/.local/state/taiji-agent`；关闭窗口会停止本次会话对应的本地进程。

安装包不内置模型 API Key、微信 token、企业微信 Secret、服务器地址或私钥。未配置模型 key 时，只能证明桌面壳、Agent API、WebUI 和本地端口链路可用，不能证明真实模型对话已完成。真实对话必须在目标机首启配置模型后再验收。

如果目标机页面、菜单、模型下拉或对话异常，优先运行交付目录中的 `bash ./03_目标终端_导出诊断报告.sh`，保留生成的脱敏诊断报告。

## 状态边界

- 已实时验证：当前 macOS 源码态健康检查曾通过；本文件只描述实现和构建流程。
- 未实时验证：新版源码包在 Kylin V10 SP1 x86_64 实机重新构建、安装、双击启动、真实模型对话、卸载重装。
- 历史线索：此前一键启动脚本和 `18642/18787` 端口经验只作为设计依据，不能替代目标机验收。
