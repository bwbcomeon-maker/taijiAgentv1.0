# 太极 Agent 完全离线 Kylin/UOS 打包说明

## 当前实现

- 桌面壳：`apps/taiji-desktop`，Electron 启动本机对话运行时，健康检查通过后加载应用界面。
- Runtime 脚本统一处理开发态和安装态路径；对外安装面只暴露 Taiji 命名。
- 安装态用户目录：
  - 配置：当前用户的太极 Agent 配置目录
  - 试用授权：`~/.config/taiji-agent/license.jwt`
  - 数据：`~/.local/share/taiji-agent/runtime-home`
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

因此本轮锁定交付 `amd64 .deb`。不做 RPM，不交付独立网页入口包。

## Linux 离线交付流程

最终 DEB 必须在 Linux x86_64/amd64 制包机生成，不允许在 macOS 上产最终包。构建策略保持离线优先：目标机完全离线时不执行构建、不联网下载依赖，只安装制包机提前生成的 DEB 和本地 apt 依赖仓库。

完全离线交付优先使用根目录下的 `taijiagent 打包交付/00_制包机_生成离线交付包.sh`。该脚本在联网 Linux amd64 制包机上校验源码包、准备构建工具、生成 Linux Python venv、安装 Linux Electron runtime、执行 DEB 构建，并把 DEB 的直接和递归运行依赖收集到 `离线依赖/Packages.gz`。制包机脚本在系统 Node/npm 过旧时使用交付目录内的隔离 Node.js Linux x64 构建工具，避免 Kylin V10 源里的 Node.js 10 / npm 6 无法处理 lockfile v3；Python venv 生成阶段保持 `TAIJI_UV_LOCK_MODE=strict`。

`01_目标终端_构建安装包.sh` 只保留给“目标机本身可联网构建”的兼容路径，本轮完全离线交付说明不使用它。

固定交付物结构：

```text
taijiagent 打包交付/taiji-agentv1.0-kylin-build-src-<hash>.tar.gz
taijiagent 打包交付/生成的安装包/taiji-agent_<version>_amd64.deb
taijiagent 打包交付/生成的安装包/taiji-agent_<version>_amd64.deb.sha256
taijiagent 打包交付/离线依赖/Packages.gz
taijiagent 打包交付/离线依赖/*.deb
taijiagent 打包交付/SHA256SUMS.txt
```

构建脚本会拒绝以下情况：

- 在 macOS 或非 x86_64/amd64 主机上构建最终包。
- Electron runtime 不是 Linux x86_64 ELF，或 `ldd` 显示缺少共享库。
- 包内出现密钥文件、私钥、macOS metadata、`__pycache__`、`*.pyc`。公共 CA 证书类 PEM 可进入 Python venv，但 PEM/证书文件内容中出现 `BEGIN ... PRIVATE KEY` 会拒绝发布。
- 包内出现客户 `license.jwt` 或其他 `*.jwt` 授权文件。试用授权必须通过交付目录预置或设置页导入，不进入 DEB。
- 默认非密产品配置模板缺失、字段不完整，或 YAML 实际字段里出现敏感凭据形态。
- DEB 产物字符串中出现 `LIBARCHIVE`、`com.apple`、`PaxHeaders`、`SCHILY.xattr` 等历史失败标记。

本轮发布基线必须从干净 commit 生成，源码包使用 `git archive` 生成，文件名带 commit 短 hash。运行态目录、日志、缓存、Playwright 输出和本地模型密钥不得进入源码包。

制包机脚本构建成功后写入 `.build-success`。目标机安装脚本必须看到该成功标记并校验当前 DEB 的 SHA256，才会执行安装，避免误装历史残留包。

桌面启动链不依赖控制台脚本的绝对 shebang。目标机交付目录可能包含空格或中文路径，因此 `start-agent.sh`、`/usr/bin/taiji`、`health-check.sh` 和 DEB 构建门禁统一通过产品运行时入口启动。安装态 `taiji-native-verify` 也会提前验证该入口，避免安装成功但双击后 Agent 启动失败。

如果目标机已经安装过旧版 `taiji-agent`，新版交付不按两个产品并存处理。`02_目标终端_安装并验证.sh` 会停止并禁用旧 `taiji-agent-webui.service` / `taiji-agent-gateway.service`，清理命令行明确指向 `/opt/taiji-agent` 的旧进程，解除 `taiji-agent` hold 状态，并通过 `apt-get purge`、`dpkg --remove --force-remove-reinstreq`、`dpkg --purge --force-all` 收口旧包状态。只有旧包状态清理干净后，脚本才会删除白名单内旧路径并安装 Electron 完整版；如果旧包状态仍残留，脚本直接失败，不安装新版。旧 `/opt/taiji-agent`、旧系统配置、旧 systemd unit、旧命令入口和旧桌面入口会被删除，不再备份旧模型 Key、微信 token 或历史会话；普通用户家目录下的新版用户态目录不在清理范围内。运行资源被其他程序占用时只记录脱敏诊断，不阻断安装，因为桌面端会自动选择可用资源。

安装包内置非密产品配置模板。启动时同步菜单显隐、默认模型和图片模型展示；已有用户配置时只补空值，不覆盖用户已配置的密钥。Linux 桌面端默认隐藏 Electron 应用菜单栏，保留麒麟原生标题栏。

产物位于：

```text
taijiagent 打包交付/生成的安装包/taiji-agent_0.1.0_amd64.deb
taijiagent 打包交付/生成的安装包/taiji-agent_0.1.0_amd64.deb.sha256
taijiagent 打包交付/离线依赖/Packages.gz
```

## 安装与验证

```bash
bash ./02_目标终端_安装并验证.sh
/opt/taiji-agent/bin/taiji-native-verify
TAIJI_VERIFY_DESKTOP_SMOKE=1 /opt/taiji-agent/bin/taiji-native-verify
/usr/bin/taiji-agent
taiji-agent-diagnose
taiji --help
```

如果要预置试用授权，把我方签发的 `license.jwt` 放在 `02_目标终端_安装并验证.sh` 同目录，或执行时设置 `TAIJI_LICENSE_SOURCE=/path/to/license.jwt`。安装脚本会复制到当前用户配置目录并设置为 `0600`；没有授权文件时安装不失败，但首次执行 Agent 对话会提示授权缺失。

安装后从开始菜单双击“太极 Agent”。Electron 会以当前桌面用户启动应用运行时，运行目录位于当前用户的太极 Agent 配置、数据和状态目录；关闭窗口会停止本次会话对应的本机进程。

安装包不内置模型 API Key、微信 token、企业微信 Secret、服务器地址或私钥。未配置模型 key 时，只能证明桌面壳和本机对话运行时可用，不能证明真实模型对话已完成。真实对话必须在目标机首启配置模型后再验收。

如果目标机页面、菜单、模型下拉或对话异常，优先运行交付目录中的 `bash ./03_目标终端_导出诊断报告.sh`，保留生成的脱敏诊断报告。

## 状态边界

- 已实时验证：当前 macOS 源码态健康检查曾通过；本文件只描述实现和构建流程。
- 未实时验证：新版源码包在 Kylin V10 SP1 x86_64 实机重新构建、安装、双击启动、真实模型对话、卸载重装。
- 历史线索：此前一键启动脚本经验只作为设计依据，不能替代目标机验收。
