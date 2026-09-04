# Taiji Agent Windows x64 候选制包运行手册

## 1. 当前范围

本手册描述单仓 `taiji-package` 的 Windows x64 候选流水线。Windows 与 Kylin 使用独立 adapter，共享平台中立的 plan、run-state、编排和 `FETCH_PENDING` 恢复合同；Windows 行为不得写入 Kylin transport 或 Linux `99/00/01`。

Windows adapter 已实现以下能力；每次执行仍须分别记录本地合同、真机运行和制品证据：

- `windows-x64` target、source branch/commit/tree 和版本绑定；
- 已选旧 Windows 资产的 Git object/provenance/snapshot 三方验证；
- 无 Git Windows session、cache、payload、review、EXE/PE/版本/AuthentiCode 的静态合同；
- fake online/cache/transport/review 全链及 `FETCH_PENDING` 只取回恢复；
- 七文件 review exact set 与独立 `logs/remote-build.log` 的边界。

历史 run 已生成并取回过真实候选 EXE，不能继续把流水线描述为“仅 fake”。这不证明当前源码可重复制包，更不证明安装、启动、GUI/业务验收、production license、签名或发布完成。最新环境核查见 [Windows 验证台账](../verification/2026-09-04-windows-build-readiness.md)。

## 2. 统一入口

统一入口是仓库根目录的 `taiji-package`。Windows 目标必须显式选择：

```bash
./taiji-package --target windows-x64 doctor
./taiji-package --target windows-x64 plan
./taiji-package --target windows-x64 build
./taiji-package --target windows-x64 status --run <run-id>
./taiji-package --target windows-x64 fetch --run <run-id>
```

`build` 的真实在线阶段和候选构建仍需要单独授权并保留 BUILD 确认。`doctor --online` 用于已授权主机的只读核查，不构建、不下载、不安装。

### 2.1 主机连接与预检

目标文件使用 SSH 别名，个人 IP、私钥和密码不进入仓库。若当前网络入口与默认 `windows-direct` 不同，使用 Mac 本地的独立 SSH 配置映射该别名，保留旧连接：

```bash
./taiji-package --target windows-x64 --ssh-config "$HOME/.ssh/taiji-windows.conf" doctor --online
```

SSH 配置必须使用核对过的主机密钥和 `StrictHostKeyChecking yes`；网络地址变化后重新确认主机身份。`--ssh-config` 是全局参数，放在 `doctor`/`build` 之前。

在线预检先实际执行目标配置指定的 Node、npm、私有 Python 和 Inno 帮助探针。Node 必须是 22/24 x64，Python 必须是 3.11 x64，并成功导入 `aiohttp`、`fastapi`、`uvicorn`、`yaml`、`cryptography`、`psutil`、`pypdf`。失败返回 `WINDOWS_RUNTIME_NOT_READY`，不进入耗时缓存扫描；通过后才执行既有 NTFS、空间和缓存哈希检查。Inno 帮助响应不等于真实编译通过。

`BUILDER_READY` 只表示这些构建环境检查通过，不保证 npm 缓存满足新锁文件，也不表示负载功能完整。当前 Windows 负载尚缺 DOCX 引擎与私有 Node 的装配/启动闭包；详细解决顺序见 [修复计划](../superpowers/plans/2026-09-04-windows-build-readiness.md)。

## 3. Windows 流程合同

候选 run 绑定 clean `main` source、target config SHA、asset provenance SHA、输入三件套和唯一 local/remote run。离线 cache 由 requirements SHA、每轮 observation SHA 和 host facts SHA 绑定；共享 cache 只读，缺项只能阻断为 `WINDOWS_CACHE_MISSING`，不能自动下载或安装依赖。

远端 review 根只允许以下七个 regular file：

```text
TaijiAgent-Setup-<version>-win-x64.exe
TaijiAgent-Setup-<version>-win-x64.exe.sha256
taiji-package-manifest.json
formal-build-tests.log
构建报告.txt
.build-success
run-state.json
```

`logs/remote-build.log` 不属于 review exact set，必须经过独立 `fetch-log` 取回。只有 review 与 log 都取回成功，才可执行本地 review 校验。校验必须交叉核对实际 bytes/SHA、canonical JSON、source/input/cache/payload、远端 state、PE machine/optional magic、FileVersion/ProductVersion 和 AuthentiCode 状态，不能只相信 marker、manifest 或 inspector 中任一项。

远端构建成功而本地取回、校验或发布失败时，run 进入 `FETCH_PENDING`。`fetch` 只允许重新取回 review/log、重新校验和幂等发布；不得重新 online doctor、准备输入、创建 run、传输、验证输入或构建。

## 4. 五层证据边界

| 层级 | 能证明 | 当前状态 |
| --- | --- | --- |
| Source | 控制器 source branch/commit/tree、版本和 selected asset 来源绑定 | 有本地及历史真实 run 证据；每轮重新绑定 |
| Payload | 真实 Windows payload 内容、cache 复制和闭包结果 | 历史 run 有证据；最新完整功能负载未验证 |
| Installer | 真实 Inno 编译、EXE bytes/PE/版本/AuthentiCode | 历史候选 EXE 有记录；当前修复后候选未构建 |
| Installed Runtime | 指定 Windows 主机上的安装、进程、端口、配置和卸载/升级 | 未验证；未安装 |
| Interactive Acceptance | 用户桌面会话中的启动、菜单、Logo、交互和业务流程 | 未验证；未启动、未做 GUI 验收 |

fake 链只证明 controller/source-contract 和 fake payload/installer orchestration 的本地边界，不能升级为真实 payload、真实 installer、安装态或 UI 通过。

## 5. 明确禁止的阶段

Windows 路线不运行 Kylin `99/00/01`。只读 doctor 的授权不能延伸为下载/安装依赖、真实制包、产品安装/启动、GUI 验收、production license、签名或发布。依赖准备仅写明确的专用目录，保留旧缓存，复验后切换；真实候选生成仍绑定已复验 clean source 并执行 BUILD。各阶段分别记录授权、来源和证据，不自动 Tag/Release。
