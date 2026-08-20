# Taiji Agent Windows x64 候选制包运行手册

## 1. 当前范围

本手册描述单仓 `taiji-package` 的 Windows x64 候选流水线。Windows 与 Kylin 使用独立 adapter，共享平台中立的 plan、run-state、编排和 `FETCH_PENDING` 恢复合同；Windows 行为不得写入 Kylin transport 或 Linux `99/00/01`。

当前 Plan 1—3 只固化以下本地能力：

- `windows-x64` target、source branch/commit/tree 和版本绑定；
- 已选旧 Windows 资产的 Git object/provenance/snapshot 三方验证；
- 无 Git Windows session、cache、payload、review、EXE/PE/版本/AuthentiCode 的静态合同；
- fake online/cache/transport/review 全链及 `FETCH_PENDING` 只取回恢复；
- 七文件 review exact set 与独立 `logs/remote-build.log` 的边界。

真实 `doctor --online`、SSH/SCP、PowerShell、Inno Setup、真实 cache、真实 payload、真实 EXE 均未运行。当前没有安装、启动、GUI 或交互验收、production license、签名、发布证据。

## 2. 统一入口

统一入口是仓库根目录的 `taiji-package`。Windows 目标必须显式选择：

```bash
./taiji-package --target windows-x64 doctor
./taiji-package --target windows-x64 plan
./taiji-package --target windows-x64 build
./taiji-package --target windows-x64 status --run <run-id>
./taiji-package --target windows-x64 fetch --run <run-id>
```

`build` 的真实在线阶段和候选构建仍需要单独授权；当前阶段只运行 fake transport 测试，不把 fake `BUILDER_READY` 解释为真实 Windows 主机事实。

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
| Source | 控制器 source branch/commit/tree、版本和 selected asset 来源绑定 | 本地 fake/静态已验证 |
| Payload | 真实 Windows payload 内容、cache 复制和闭包结果 | 未验证；当前只有 fake payload/review |
| Installer | 真实 Inno 编译、EXE bytes/PE/版本/AuthentiCode | 未验证；PowerShell/Inno 未运行，候选 EXE 未构建 |
| Installed Runtime | 指定 Windows 主机上的安装、进程、端口、配置和卸载/升级 | 未验证；未安装 |
| Interactive Acceptance | 用户桌面会话中的启动、菜单、Logo、交互和业务流程 | 未验证；未启动、未做 GUI 验收 |

fake 链只证明 controller/source-contract 和 fake payload/installer orchestration 的本地边界，不能升级为真实 payload、真实 installer、安装态或 UI 通过。

## 5. 明确禁止的阶段

本阶段不运行 Kylin `99/00/01`，不连接真实 Linux/Windows，不下载或安装 Windows cache/依赖，不生成真实 DEB/EXE，不安装、启动或 GUI 验收，不配置 production license，不签名、不发布、不 Tag、不 Release。进入真实 Windows 阶段前必须由用户单独授权，并重新建立主机、工具、cache、来源和制品摘要证据。
