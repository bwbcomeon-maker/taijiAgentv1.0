# 太极 Agent 销售就绪门禁

本文档定义对外销售或客户现场交付前必须满足的证据边界。没有实时证据时，不得把历史线索、本机源码检查或 macOS 结果说成目标机已验证。

## 支持矩阵

当前主线只承诺：

- Linux x86_64/amd64
- Debian-like Kylin/UOS/openKylin class system
- 具备 GUI 桌面会话、sudo、apt-get、apt-cache、dpkg、systemctl
- 交付完整离线 DEB 目录，而不是只拷贝单个 .deb

当前不承诺：

- RPM-only 终端
- ARM/aarch64 终端
- 没有包管理器或 sudo 的终端
- 强隔离、禁用 Electron 沙箱修复、禁用本地端口或禁用桌面双击启动的现场策略

## 证据标签

- 源码包已准备：当前 commit 只有一个源码包，basename hash 写入 SHA256SUMS.txt，并通过源码发布预检。
- 制包机已构建：Linux amd64 制包机产出 .deb、.deb.sha256、manifest、构建报告、离线依赖仓库和 .build-success。
- 离线安装已演练：干净 Linux amd64 VM/chroot/container 只使用本地交付物完成安装、验证、卸载、重装。
- 目标机已验证：真实 Kylin/UOS/openKylin 终端完成桌面启动、CLI、真实模型对话、附件流程、关窗退出和诊断导出。

## 一键门禁

销售前运行：

```bash
bash scripts/taiji-release-check.sh
```

离线生命周期演练证据目录默认是：

```text
taijiagent 打包交付/offline-install-rehearsal/offline-install-rehearsal.json
```

目标机证据目录默认是：

```text
taijiagent 打包交付/target-verification/target-verification.json
```

也可以分别通过环境变量指定：

```bash
TAIJI_OFFLINE_REHEARSAL_DIR=/path/to/offline-install-rehearsal \
TAIJI_TARGET_VERIFICATION_DIR=/path/to/target-verification \
bash scripts/taiji-release-check.sh
```

`offline-install-rehearsal.json` 必须由断网的 Linux amd64 环境生成，且 install → purge/uninstall → reinstall 三段都实际成功。它只能记录为离线安装演练，不能冒充桌面目标机验收：

```json
{
  "platform": "linux/amd64",
  "network": "none",
  "install": true,
  "uninstall": true,
  "reinstall": true,
  "target_verified": false
}
```

只有在容器/VM/chroot 使用 `--network none` 或等价断网策略、并且只读取本地交付目录时，才可生成该证据。该文件不是 `target-verification.json`，也不能把 `target_verified` 写成 `true`。

`target-verification.json` 至少需要包含：

```json
{
  "target_verified": true,
  "desktop_launch": true,
  "diagnostic_export": true
}
```

## 销售口径

可以说：

- 已在明确支持矩阵内完成源码、构建、离线安装和目标机验收。
- 授权、诊断、卸载重装和隐私扫描已纳入发布门禁。

不能说：

- macOS 本机检查通过，所以 Kylin/UOS 目标机已验证。
- 有源码包，所以能一键安装。
- 有 .deb，所以离线交付完整。
- 旧包或旧日志通过，所以当前 commit 可销售。
