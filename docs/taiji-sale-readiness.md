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

销售前最终放行时，必须复用本次断网演练和真实桌面 App 验收开始时保留的原 challenge：

```bash
export TAIJI_OFFLINE_REHEARSAL_CHALLENGE="<本次断网演练时保留的原 challenge>"
export TAIJI_TARGET_ACCEPTANCE_CHALLENGE="<本次真实桌面 App 验收时保留的原 challenge>"
bash scripts/taiji-release-check.sh
```

最终门禁只复用本次验收时保留的原 challenge，不得在最终门禁阶段重新执行 `openssl rand`；否则新值与已生成、已签名证据不一致，门禁必然失败。这里的“复用”仅指同一轮验收、签名和最终放行链路；开始新一轮验收时必须生成新 challenge，旧证据中的 challenge 不得复用。

离线生命周期演练证据目录默认是：

```text
taijiagent 打包交付/offline-install-rehearsal/offline-install-rehearsal.json
```

在受控发布机的仓库根目录中，可直接执行下面的完整流程生成该证据。当次 challenge 只生成一次，并必须保留原值供后续签名和最终门禁使用：

```bash
docker build --platform linux/amd64 \
  -t taiji-offline-rehearsal:local \
  tools/taiji-offline-rehearsal

export TAIJI_OFFLINE_REHEARSAL_CHALLENGE="$(openssl rand -hex 32)"
python3 scripts/produce-taiji-offline-rehearsal.py \
  --delivery-dir "taijiagent 打包交付" \
  --output-dir "taijiagent 打包交付/offline-install-rehearsal" \
  --image taiji-offline-rehearsal:local \
  --challenge "$TAIJI_OFFLINE_REHEARSAL_CHALLENGE"
```

`--output-dir` 指向的输出目录必须不存在，生产器拒绝覆盖旧证据。它会把完整交付目录只读挂载，并在容器运行时强制使用 `--network none`，执行 install → purge/uninstall → reinstall。该证据仅证明离线安装生命周期，不能替代真实 Electron 桌面 App 验收。

演练镜像固定使用与当前制包依赖链一致的 Ubuntu 20.04 x86_64 兼容基线，避免把 Debian 新版本的系统包冲突误判为交付包缺陷；它仍只是 Kylin/UOS 安装前的容器回归，不属于目标终端验收。

目标机证据目录默认是：

```text
taijiagent 打包交付/target-verification/target-verification.json
```

目标机证据必须在真实 Kylin/UOS/openKylin x86_64 图形桌面、普通登录用户下，使用当前完整交付目录生成：

```bash
export TAIJI_TARGET_ACCEPTANCE_CHALLENGE="<验收开始前独立生成的 challenge>"
bash "taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh"
```

该脚本固定调用 `/opt/taiji-agent` 安装态 Electron 和随包 Node/Python，通过 loopback CDP 操作真实 App 可见界面；要求真实模型从附件返回 challenge 绑定的唯一验收码，从设置中可见入口导出支持包，并在关窗后核对 Electron/Agent/界面服务进程与端口退出。不使用 Web 页面或手机端结果替代。输出只是待复核的未签名证据，目标机不接触发布私钥。

也可以分别通过环境变量指定：

```bash
TAIJI_OFFLINE_REHEARSAL_DIR=/path/to/offline-install-rehearsal \
TAIJI_TARGET_VERIFICATION_DIR=/path/to/target-verification \
bash scripts/taiji-release-check.sh
```

`offline-install-rehearsal.json` 必须由断网的 Linux amd64 环境生成，且 install → purge/uninstall → reinstall 三段都实际成功。它只能记录为离线安装演练，不能冒充桌面目标机验收：

```json
{
  "schema_version": 1,
  "evidence_type": "offline-install-rehearsal",
  "generated_at_utc": "最近 7 天内的 UTC ISO8601 时间",
  "rehearsal_session_id": "32 位小写十六进制会话 ID",
  "challenge_nonce": "本次 TAIJI_OFFLINE_REHEARSAL_CHALLENGE",
  "release_artifacts_sha256": "整个交付目录确定性文件清单的 SHA256",
  "source_commit": "当前 git rev-parse --short HEAD",
  "deb_basename": "taiji-agent_0.1.0-preview_amd64.deb",
  "deb_sha256": "当前 DEB 的 64 位小写 SHA256",
  "platform": "linux/amd64",
  "environment": "container",
  "os_id": "ubuntu",
  "os_version": "20.04",
  "network": "none",
  "install": true,
  "uninstall": true,
  "reinstall": true,
  "desktop_app_verified": false,
  "target_verified": false,
  "log_basename": "offline-install-rehearsal-session.json",
  "log_sha256": "同目录结构化演练会话的 64 位小写 SHA256"
}
```

只有在容器/VM/chroot 使用 `--network none` 或等价断网策略、并且只读取本地交付目录时，才可生成该证据。结构化会话必须使用 `taiji.offline-install-rehearsal.v1` schema，重复记录当前 commit、DEB 摘要、challenge、断网方式和三段退出结果。门禁会严格拒绝未知/重复字段，并把证据绑定到 `生成的安装包/taiji-package-manifest.json`、`.build-success`、DEB sidecar、当前源码包和 `Packages/Packages.gz`；旧包或手写一个布尔值文件不能复用。该文件不是 `target-verification.json`，也不能把 `desktop_app_verified` 或 `target_verified` 写成 `true`。

`target-verification.json` 只接受真实 Kylin/UOS/openKylin 桌面终端上的 Electron App 验收，不接受 Web 页面或手机端结果。完整字段为：

```json
{
  "schema_version": 1,
  "evidence_type": "target-desktop-verification",
  "application": "taiji-electron-desktop",
  "generated_at_utc": "最近 7 天内的 UTC ISO8601 时间",
  "acceptance_session_id": "32 位小写十六进制会话 ID",
  "challenge_nonce": "本次 TAIJI_TARGET_ACCEPTANCE_CHALLENGE",
  "machine_fingerprint_sha256": "目标机指纹的隐私化 SHA256",
  "release_artifacts_sha256": "整个交付目录确定性文件清单的 SHA256",
  "electron_executable_sha256": "安装态 Electron executable SHA256",
  "desktop_entry_sha256": "安装态 desktop entry SHA256",
  "installed_package_version": "0.1.0-preview",
  "source_commit": "当前 git rev-parse --short HEAD",
  "deb_basename": "taiji-agent_0.1.0-preview_amd64.deb",
  "deb_sha256": "当前 DEB 的 64 位小写 SHA256",
  "platform": "linux/amd64",
  "os_id": "kylin",
  "os_version": "V10",
  "desktop_environment": "UKUI",
  "target_verified": true,
  "desktop_launch": true,
  "real_model_conversation": true,
  "attachment_flow": true,
  "window_close_exit": true,
  "diagnostic_export": true,
  "session_log_basename": "desktop-acceptance-session.json",
  "session_log_sha256": "同目录结构化 Electron 验收会话的 64 位小写 SHA256",
  "screenshot_basename": "desktop-app.png",
  "screenshot_sha256": "同目录桌面 App 截图的 64 位小写 SHA256",
  "diagnostic_basename": "taiji-support-bundle.json",
  "diagnostic_sha256": "同目录桌面 App 诊断导出 JSON 的 64 位小写 SHA256",
  "driver_result_basename": "desktop-driver-result.json",
  "driver_result_sha256": "同目录严格驱动原始结果的 64 位小写 SHA256"
}
```

结构化桌面会话必须使用 `taiji.desktop.acceptance.v1` schema，记录安装态 Electron PID/绝对 executable、executable 与 desktop entry 摘要、`electron-cdp` 连接、桌面 token、明确未使用 Web fallback，并把模型对话、附件、关窗退出、诊断导出全部记录为真实通过。`desktop-driver-result.json` 必须保留严格 `taiji.desktop.acceptance-driver.v1` 原始结果，使模型身份、附件探针摘要、Agent/WebUI PID 与 Electron 退出码可追溯；门禁会对其字段集、摘要及与会话/顶层证据的 challenge、session、Electron、desktop entry、checks 等关键字段做交叉校验。截图必须是至少 800×600 的完整 RGB8 或 RGBA8 PNG；诊断文件必须是 App 导出的 `taiji.product.support-bundle.v1` JSON，且 `overall` 必须与七个组件状态按产品诊断规则一致。会话、截图、诊断导出和驱动原始结果的 basename 和 inode 必须均不同，证据目录本身不能是符号链接。

门禁会对交付目录生成确定性文件清单摘要：源码包、DEB、sidecar、manifest、`.build-success`、构建报告、安装/诊断/桌面验收脚本、`验收工具/`、说明文档、`Packages/Packages.gz`、离线仓库 `SHA256SUMS.txt` 以及每一个依赖 DEB 都在签名闭包内；只排除证据目录和易变构建/诊断日志。目标证据闭包还会校验结构化会话、桌面 App 截图、诊断导出和驱动原始结果摘要，并要求 commit 与当前仓库 HEAD、challenge 与本次放行命令一致。签名后任一交付文件或目标证据绑定文件被替换，旧签名都会失效。没有这些当前产物的真实证据时，状态必须写成“目标机桌面 App 未实时验证”。

两类证据完成后都必须回到受控发布机，由发布负责人核对原始会话、截图和诊断内容，再使用离线发布私钥生成 detached signature：

```bash
export TAIJI_OFFLINE_REHEARSAL_CHALLENGE="<断网演练开始前独立生成的原 challenge>"
bash scripts/sign-taiji-release-evidence.sh \
  "taijiagent 打包交付/offline-install-rehearsal/offline-install-rehearsal.json" \
  /secure/offline-release-signing-private.pem

export TAIJI_TARGET_ACCEPTANCE_CHALLENGE="<桌面验收开始前独立生成的原 challenge>"
bash scripts/sign-taiji-release-evidence.sh \
  "taijiagent 打包交付/target-verification/target-verification.json" \
  /secure/offline-release-signing-private.pem
```

验收证据使用独立密钥域，不复用软件授权签发密钥。私钥必须与仓库 `tools/taiji-release-evidence/signing-public.pem` 的固定 fingerprint `839b6c58…d51ec1da` 匹配，不得复制到目标终端、安装包或交付目录。门禁必须同时验证 `<evidence.json>.sig`；缺少签名、证据签名后被改动、或使用其他密钥签名都会失败。这一步代表发布负责人对真实 App 验收原始证据的复核批准，不是由目标机自报即可自动放行。

发布私钥文件及其父目录必须由当前发布负责人独占：目录权限 `0700`，私钥权限 `0400` 或 `0600`，且私钥不能是符号链接或硬链接。签名器会在私钥同目录的 `.taiji-release-evidence-used-challenges/` 中以原子方式登记已使用 challenge；签名失败后该 challenge 也视为作废，必须重新生成并重新验收，不能删除登记文件后复用旧证据。正式销售前还必须由两名责任人完成加密离线备份和一次恢复演练；当前没有恢复演练记录时，只能标为“签名密钥恢复能力未验证”，不得把本机存在私钥当成灾备完成。

密钥轮换时应先生成新密钥、更新仓库固定公钥与完整 fingerprint、用新旧门禁各验证一份专用轮换样例，再撤销旧私钥；已签历史证据与对应旧公钥/fingerprint 必须成套归档，不能只覆盖公钥文件。

## 销售口径

可以说：

- 已在明确支持矩阵内完成源码、构建、离线安装和目标机验收。
- 授权、诊断、卸载重装和隐私扫描已纳入发布门禁。

不能说：

- macOS 本机检查通过，所以 Kylin/UOS 目标机已验证。
- 有源码包，所以能一键安装。
- 有 .deb，所以离线交付完整。
- 旧包或旧日志通过，所以当前 commit 可销售。
