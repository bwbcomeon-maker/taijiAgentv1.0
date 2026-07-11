# 太极 Agent 国产 x86 Linux 离线交付运行手册

## 1. 文档目的

本文档沉淀太极 Agent 在国产 `x86_64/amd64` Linux 终端上的制包、完全离线安装、Docker 演练、真实桌面验收和故障诊断经验。目标是把已经确认的失败原因固化为脚本门禁，减少现场依赖截图、临时命令和多轮往返。

本文面向研发、发布负责人和现场交付人员。目标终端操作员使用随交付目录提供的 [`操作说明.md`](../../taijiagent%20打包交付/操作说明.md)；本手册负责解释为什么这样做、Docker 能证明什么、哪些结论必须回真实 Kylin/UOS 终端验证。

本文不是某个 commit 的发布证明。最终发布身份必须由当前交付目录中的 `taiji-package-manifest.json`、`.build-success`、各级 SHA256、断网演练证据、目标机证据和签名共同确定。

## 2. 支持矩阵

当前 DEB 主线的目标范围：

- Linux `x86_64/amd64`。
- Debian-like Kylin、UOS、openKylin 类系统。
- `apt-get`、`apt-cache`、`dpkg`、`systemctl` 和 `sudo` 可用。
- 具备图形桌面会话，能够启动 Electron 应用。
- 使用完整重建的 `taijiagent 打包交付/` 目录进行离线安装。

上述是产品设计与制品约束，不等于所有目标发行版都已实测支持。某个 Kylin/UOS/openKylin 版本只有取得与当前产物绑定的真实目标机证据后，才能写成该版本“已验证”。

当前主线不支持：

- ARM/aarch64。
- 只有 RPM 包管理器的终端。
- 没有可用包管理器或管理员能力的终端。
- 禁止 `/opt`、systemd、本地 apt 仓库、Electron 沙箱修复或本地 loopback 服务的强隔离环境。
- 用同一个 DEB 覆盖所有“国产 x86”发行版和安全策略。

RPM-only 终端需要单独的 RPM 制品；无包管理器或强隔离终端需要单独的 `.run` 或现场定制方案。

## 3. 四级证据口径

状态汇报只能使用以下四级标签，不得跨级推导：

| 标签 | 必须具备的证据 |
| --- | --- |
| 源码包已准备 | 当前基线只有一个源码包；basename 与当前候选源提交一致；`SHA256SUMS.txt` 精确匹配；源码发布预检通过 |
| 制包机已构建 | 兼容 Linux amd64 制包机生成 DEB、sidecar、manifest、构建报告、完整离线仓库和 `.build-success`，最终发布预检通过 |
| 离线安装已演练 | 干净 Linux amd64 容器、VM 或 chroot 在断网状态下只使用本地交付物完成安装、验证、卸载和重装，并生成当前产物绑定证据 |
| 目标机已验证 | 真实 Kylin/UOS/openKylin 图形终端完成安装态 Electron 启动、CLI、真实模型对话、附件、关窗退出和诊断导出 |

源码测试、macOS Electron App、旧 commit 的 DEB、旧日志或截图都不能替代当前产物的后一级证据。最终销售放行还要求两类证据经过发布负责人复核、签名，并通过 `scripts/taiji-release-check.sh`。

## 4. 四类环境的职责

| 环境 | 负责内容 | 不能据此宣称 |
| --- | --- | --- |
| macOS/开发机 | 清理源码、生成唯一输入包、静态检查、单元测试 | 已生成目标 DEB、已完成离线安装、目标机已验证 |
| Linux amd64 制包机 | 构建 Linux Python/Node/Electron runtime、DEB、离线仓库、manifest 和报告 | 真实 Kylin 桌面 App 已通过 |
| Docker/VM 断网演练 | 校验本地 apt 仓库和安装→卸载→重装生命周期 | UKUI、kysec、Electron 桌面、真实模型已通过 |
| 真实国产终端 | 验证系统策略、桌面启动、真实业务链和关闭行为 | 其它未测试发行版也必然兼容 |

## 5. 标准交付链

### 5.1 准备制包机输入包

在干净的源码基线执行：

```bash
bash "taijiagent 打包交付/99_本机_准备制包输入包.sh"
```

输出的 `taijiagent-制包机输入-<commit>.tar.gz` 是推荐的 Linux 制包机输入。它用于隔离 Finder、聊天工具、U 盘和历史构建产物造成的元数据污染。

### 5.2 在兼容 Linux amd64 制包机生成完整交付目录

解压输入包后进入 `taijiagent 打包交付/`：

```bash
bash ./00_制包机_生成离线交付包.sh
```

只有脚本正常结束、最终发布预检通过，才可标记“制包机已构建”。看到 DEB 文件但 manifest、报告、离线仓库或 `.build-success` 缺失时仍属于失败。

### 5.3 在受控发布机执行断网生命周期演练

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

输出目录必须事先不存在。生产器应验证镜像角色和兼容基线、使用 `--network none`、只读挂载交付目录，并将证据绑定到当前 manifest、DEB、源码包和离线仓库摘要。

### 5.4 在真实目标机安装并验收

将完整的 `taijiagent 打包交付/` 目录复制到目标机，而不是只复制 `生成的安装包/`：

```bash
bash ./02_目标终端_安装并验证.sh
```

安装链通过后继续执行第 10 节的真实桌面验收。容器中的 `TAIJI_ALLOW_HEADLESS_REHEARSAL=1` 只允许生成非 GUI 演练结果，不能在真实交付报告中替代桌面验收。

### 5.5 签名与最终放行

断网演练和目标机验收使用不同 challenge。两类证据复制回受控发布机后，由发布负责人检查原始会话、截图和诊断内容，再用独立离线私钥签名。最终门禁必须复用当轮原 challenge，不能重新生成：

```bash
export TAIJI_OFFLINE_REHEARSAL_CHALLENGE="<当轮断网演练原值>"
export TAIJI_TARGET_ACCEPTANCE_CHALLENGE="<当轮真机验收原值>"
bash scripts/taiji-release-check.sh
```

## 6. 完整离线交付契约

完整目录至少包括：

```text
taiji-agentv1.0-kylin-build-src-<commit>.tar.gz
SHA256SUMS.txt
00_制包机_生成离线交付包.sh
01_制包机_发布预检.sh
02_目标终端_安装并验证.sh
03_目标终端_导出诊断报告.sh
04_目标终端_桌面App验收并导出证据.sh
生成的安装包/taiji-agent_<version>_amd64.deb
生成的安装包/taiji-agent_<version>_amd64.deb.sha256
生成的安装包/taiji-package-manifest.json
生成的安装包/构建报告.txt
生成的安装包/.build-success
离线依赖/Packages
离线依赖/Packages.gz
离线依赖/SHA256SUMS.txt
离线依赖/runtime-dependencies.txt
离线依赖/*.deb
验收工具/
```

必须同时满足：

- 当前源码包唯一且 SHA256 匹配。
- `生成的安装包/` 只有一套允许的当前产物。
- `.deb.sha256` 只记录 basename，不记录制包机绝对路径。
- `Packages.gz` 可解压且内容与 `Packages` 一致。
- 离线仓库文件集合、索引和每个 DEB 摘要闭合。
- 最终 DEB 真实解包后，Python、Linux Electron ELF、Web runtime、CLI、desktop entry、配置模板、诊断、授权公钥和产品 Skills 均满足 payload contract。
- 最终 Web 静态文件不依赖 jsDelivr、unpkg 等公网 CDN。
- 交付目录不包含旧 DEB、旧 zip、多个源码包、构建日志、macOS metadata、客户授权、私钥、API Key 或本地会话。

## 7. Docker 能覆盖与不能覆盖的边界

### 7.1 Docker 可以覆盖

- Linux amd64 架构和 Ubuntu 20.04/glibc 2.31 兼容基线。
- Linux Python、Node 和 Electron runtime 的构建与 ELF/共享库审计。
- DEB payload、manifest、sidecar、`.build-success` 和离线仓库完整性。
- `--network none` 下的安装、非 GUI 验证、卸载和重装。
- root-owned staging、同版本重装、旧包清理和 apt/dpkg 状态转换。
- 交付目录只读挂载、证据目录单独可写和 challenge/摘要绑定。

### 7.2 Docker 不能替代

- Kylin kysec、现场杀软、白名单、客户 ACL 和实际管理员策略。
- UKUI、X11/Wayland、开始菜单、双击启动、Electron chrome-sandbox 和窗口生命周期。
- GPU、字体、输入法、声音、U 盘或 FAT/exFAT 权限行为。
- 在 ARM Mac 上通过 `--platform linux/amd64` 运行时可能使用指令翻译；它不能证明国产 x86 真机的原生性能、CPU 指令兼容性或硬件驱动。
- 客户内网模型、DNS、代理、证书、时间同步和真实授权绑定。
- 真实模型对话、附件解析、图片能力、WPS/Word 视觉效果和用户体验。
- RPM-only、ARM 或其它不在支持矩阵内的系统。

因此 Docker 通过后的最高口径是“离线安装已演练”。

## 8. 已确认故障经验矩阵

下表只记录本轮已经出现的真实失败，或已由针对性负向测试证明的高风险缺口。未验证猜测不得升级为长期规则。

| 症状 | 根因 | 修复 | 防复发门禁 | 验证边界 |
| --- | --- | --- | --- | --- |
| Linux 制包 `npm test` 多项失败并提示缺少 `@resvg/resvg-js-linux-*` | 普通 npm 安装只准备当前平台原生包，复制型 DOCX skill 却承诺多个 Linux CPU/ABI | 按 lockfile 下载、校验并原子物化 x64/arm64、gnu/musl 原生包 | lockfile integrity、包身份、ELF/架构校验、制包机真实 `npm test` | 已由真实制包失败暴露并修复 |
| apt 安装依赖时可能等待时区等交互输入 | 非交互环境没有稳定跨 sudo 传递 | 使用 `DEBIAN_FRONTEND=noninteractive` 和固定 `TZ` | 静态断言并在最小 Ubuntu 制包机实际执行 | 当前候选制包链已覆盖 |
| Electron `ldd` 审计报告缺共享库 | 最小制包容器没有安装执行 Electron 审计所需的系统库 | 制包依赖阶段安装 DEB 声明的 Electron runtime 库 | Electron 必须为 Linux amd64 ELF，`ldd` 不得出现 `not found` | 当前候选 payload audit 已覆盖 |
| manifest、最终预检或重试清理出现只读模板 `Permission denied` | 内置模板有意使用 `0444/0555`，普通 `rm -rf` 无法删除父目录 | 只清理受控 `/tmp` 专用目录；先恢复目录 owner 写权限再删除 | manifest、payload-preflight、build-root 三类只读树清理测试 | 已由真实制包失败暴露并修复 |
| 离线仓库看似生成，但依赖为空或不闭合 | 手写 Depends 解析和 `apt-rdepends` 不能等价于干净目标机上的 apt 求解 | 分开解析 Depends/Pre-Depends；用空 dpkg status 的 apt download-only 求解；按实际下载包建索引 | 直接依赖非空、依赖闭包、`Packages`/`Packages.gz`/每个 DEB 摘要一致 | 历史候选 `1d56849a` 生成 187 个索引项并完成断网生命周期；后续源码提交仍须重跑 |
| 使用 Debian 13 制包或演练会带来 glibc 2.41 和新系统包冲突 | 演练系统比 Kylin V10/glibc 2.31 更新，可能产生假绿或误报依赖冲突 | 固定 Ubuntu 20.04 amd64 兼容基线，并校验镜像 baseline label | manifest 记录 OS、arch、glibc；生产器核对镜像角色和版本 | 仅证明兼容基线，不证明 Kylin 真机 |
| Linux 签名预检误报“源码包内容与当前 Git HEAD 不一致” | macOS Apple gzip 与 Linux GNU gzip 会把同一 tar 压成不同字节；比较 `.tar.gz` 本身把编码器差异误判为源码漂移 | 仍用当前 Git HEAD 重建确定性 tar，但与源码包解压后的 tar 流逐字节比较 | 不同 gzip 编码器的同一 git archive 必须通过；解压后 tar 增加任意字节必须拒绝 | 在 `15c058b4` 签名前真实暴露；两端解压 tar SHA256 相同后修复 |
| `--network none` 被未启用的 tunnel 设备误报；sudo 提示 hostname 解析失败 | 只按网络节点存在判断；容器 hostname 未进入本地 hosts | 只拒绝启用链路、全局地址和非 loopback route；sudo 前确保本地 hostname 解析 | Docker inspect、网络负向测试和结构化会话记录 | 历史候选 `1d56849a` 已完成断网三阶段；后续源码提交仍须重跑 |
| 无图形容器执行安装后可能被误写成目标机成功 | CLI 和包状态不能证明 Electron/UKUI | 无图形会话默认失败；仅显式 headless rehearsal 可继续，并强制 `desktop_app_verified=false`、`target_verified=false` | release gate 分开验证离线证据与真机证据 | 目标机仍必须执行 `04` |
| 普通用户交付目录通过校验后、sudo 安装前可被替换 | 用户可写源文件存在 TOCTOU 窗口 | 复制到 root-owned `/var/tmp` staging 后重校验，再 purge/install | 拒绝 symlink、hardlink、路径穿越、未列入仓库文件和中途替换 | 安装脚本仿真与负向测试覆盖 |
| 并发首次初始化偶发 `Template registry lock not found`，制包 `npm test` 中断 | 旧 regular-file lock 在 owner 内容完整前已经公开；等待者可能看到空锁、消失锁或错误代锁 | 使用 candidate directory 写完整 owner 后原子发布；owner 绑定 generation token；release/stale 通过 tombstone 隔离 | 多进程初始化、旧 owner 不能释放新代、延迟 stale reaper 不能隔离新代、压力测试 | 源码与 Ubuntu 聚焦测试已通过；最终仍以当前 manifest 和证据为准 |
| `uv --locked` 提示 lockfile 需要更新 | Linux resolver 发现源码 lock 漂移 | 只在受控制包工作区按策略自动非 locked 重试，并写入构建报告 | Python relocation/import、payload audit；严格发布可显式使用 strict 模式 | fallback 是受控告警，不应被描述为 lock 已修复 |

## 9. Registry lock 的剩余风险和运维规则

directory-lock + generation token 解决了“未完整发布”和旧 owner 操作新代锁的问题，但以下风险仍必须保留为 P2，不得写成已经彻底解决。

### 9.1 candidate、release、stale 目录残留

进程异常退出或机器断电后，可能残留：

- `.candidate-*`：尚未发布的候选锁目录。
- `.release-*`：已经从主锁路径移出的释放中目录。
- `.stale-*`：已隔离的旧代锁 tombstone。

运行中的 App、Agent、DOCX worker 可能仍在观察或处理这些目录。禁止在产品运行时按名称批量删除。只有在完成以下条件后才能清理：

1. 正常关闭太极 Agent。
2. 确认 Electron、Agent、WebUI 和 DOCX worker 均退出。
3. 确认没有当前 `.lock` owner 和正在执行的模板安装/替换。
4. 对残留目录执行结构、类型、owner schema 和边界校验。
5. 只删除明确属于当前 runtime registry 的已隔离残留。

后续若实现自动清理，应放在停 App 后的维护阶段，并为“App 仍运行时拒绝清理”增加负向测试。

### 9.2 PID 复用和 PID namespace

generation token 能区分锁代，但当前活性判断仍可能主要依赖 PID：

- 系统重启或长时间运行后 PID 可能被复用。
- Docker/容器内 PID 与宿主机 PID 不属于同一 namespace。
- 只在宿主机执行 `kill -0 <容器PID>` 或反向判断没有意义。

PID 复用时应优先安全超时，不得删除可能属于新进程的锁。后续可在 owner 身份中加入 boot ID、PID namespace 标识和进程启动时间，并增加 PID 复用与跨 namespace 测试。

### 9.3 旧 regular-file lock 兼容

升级前的版本可能留下内容为 PID 的普通文件锁，新版本要求目录锁。如果直接把普通文件当损坏目录，可能造成升级后首次启动失败。

兼容策略应 fail closed：

- 旧锁为符号链接、硬链接、畸形内容或不可稳定读取时拒绝自动处理。
- 旧锁 PID 仍存活或身份不明时不删除，导出诊断。
- 只有 inode、内容和 owner 状态在复核前后稳定，且明确确认进程已死，才允许隔离到旧锁 tombstone。
- 真实目标机升级验收必须覆盖“无旧锁、死进程旧锁、活进程旧锁、畸形旧锁”四类场景。

## 10. 真实 Kylin/UOS App 最终验收

### 10.1 安装前事实采集

```bash
cat /etc/os-release
uname -m
dpkg --print-architecture 2>/dev/null || true
ldd --version 2>/dev/null | head -1 || true
command -v apt-get apt-cache dpkg systemctl sudo
printf 'DISPLAY=%s\nWAYLAND_DISPLAY=%s\n' "${DISPLAY:-}" "${WAYLAND_DISPLAY:-}"
```

同时确认磁盘、内存、桌面类型、管理员能力、kysec/杀软/白名单策略和模型访问条件。

### 10.2 安装态检查

```bash
bash ./02_目标终端_安装并验证.sh
/opt/taiji-agent/bin/taiji-native-verify
taiji --help
bash ./03_目标终端_导出诊断报告.sh
```

### 10.3 真实 Electron 自动验收

在 App 内先配置可用真实模型。完全隔离终端需要可访问的本地或内网模型：

```bash
export TAIJI_TARGET_ACCEPTANCE_CHALLENGE="<64 位小写十六进制 challenge>"
bash ./04_目标终端_桌面App验收并导出证据.sh
```

必须证明：

- 使用 `/opt/taiji-agent` 安装态 Electron，而不是浏览器或源码 App。
- 从开始菜单可见并启动“太极 Agent”。
- 同一应用重复双击只聚焦已有窗口。
- 真实模型完成 challenge 绑定的附件对话。
- 支持包从用户可见入口导出。
- 关闭窗口后 Electron、Agent、WebUI 进程和端口退出。
- `target-verification/` 中的 JSON、截图、支持包和 driver result 摘要互相绑定。

### 10.4 人工业务复核

- 首屏、设置、模型配置和对话区可正常使用。
- 上传 PDF、PPTX、DOCX、XLSX 或 TXT/MD/CSV 后，回答基于真实文件内容。
- 图片能力按当前模型配置给出真实结果或明确能力不足提示。
- 若交付包含 DOCX 结果，使用目标环境的 WPS/Word 完成人工视觉检查。
- 卸载、同版本重装、旧版升级和异常中断恢复按本次交付范围分别验收。

## 11. 一次性诊断包流程

### 11.1 当前已实现的现场动作

- `00` 制包失败：制包机日志保存在 `~/.local/state/taiji-agent/build-logs/`，不应随客户交付目录外发。
- `02` 安装失败：脚本自动生成当前交付目录下的 `构建日志/失败诊断-<时间>.txt`。
- 任一安装态或桌面异常：现场只执行一次：

```bash
bash ./03_目标终端_导出诊断报告.sh
```

当前脚本生成 `诊断报告/taiji-agent-diagnose-<时间>.txt`。优先发送该文件，不再只发截图。截图只用于补充可见 UI 异常，不能替代日志和发布身份。

### 11.2 建议的一文件支持包契约（待脚本实现）

后续应让 `03` 同时生成：

```text
诊断报告/taiji-agent-support-<时间>.tar.gz
诊断报告/taiji-agent-support-<时间>.tar.gz.sha256
```

压缩包建议包含：

- `summary.txt`：失败阶段、错误码和四级证据状态。
- `release/`：manifest、`.build-success`、构建报告和 SHA 清单；不复制大 DEB。
- `system/`：OS、架构、glibc、桌面会话、sudo/systemd、kysec 摘要。
- `package/`：dpkg/apt 状态、离线仓库文件集合与摘要。
- `runtime/`：native verify、CLI、Electron `file/ldd`、desktop entry、权限、服务、进程和端口。
- `logs/`：自动失败诊断和经过脱敏的日志尾部。
- `app/`：已有的产品支持包；不能静默收集完整会话或附件。
- `bundle-manifest.json` 与 `collection-errors.txt`。

诊断收集必须 best-effort：某个命令失败时继续收集其它证据，并把失败记录到 `collection-errors.txt`。压缩包和 sidecar 权限应为 `0600`。

禁止收集或外发：API Key、token、密码、私钥、授权 JWT 正文、模型完整请求、完整用户数据库、附件正文、客户 IP/域名和未脱敏绝对用户路径。需要额外材料时必须单独取得用户确认。

## 12. 候选证据与发布身份

候选 `15c058b4` 也完成了 Ubuntu 20.04 amd64 全量制包、187 项离线仓库和 `--network none` 安装→卸载→重装。签名前预检阻止了签名流程，但暴露的不是源码漂移，而是 Apple gzip 与 GNU gzip 的压缩结果不同；两端解压后的原始 git-archive tar SHA256 完全一致。门禁已改为比较解压后的 tar 流。因为门禁代码本身改变了源码身份，`15c058b4` 的 DEB 与离线证据只能保留为历史候选，不能签给后续提交。

2026-07-11 曾以源码候选 `1d56849a` 在 Ubuntu 20.04 amd64/glibc 2.31 环境完成一次 `00` 制包和最终发布预检。该次 manifest 记录了源码、DEB、Electron、desktop entry、`Packages` 和 `Packages.gz` 摘要，payload contract 与 187 项离线仓库索引通过；随后在 `--network none` 容器中完成安装、验证、卸载和重装，并生成通过预签内容校验的结构化证据。该证据明确记录 `desktop_app_verified=false`、`target_verified=false`，且未作为最终发布完成双证据签名。

这只是历史候选构建证据，不是最终 release：

- 候选之后的任何源码或交付文件变化都会改变发布身份。
- 候选之后的锁超时和文档改动使该证据不能绑定后续提交；最终源码提交仍须重新制包和重新断网演练。
- 真实 Kylin/UOS 目标机验收和签名证据仍必须单独完成。
- 最终报告应从实际交付目录读取 manifest 和 evidence，不得把 `1d56849a` 硬编码为最终发布版本。

另有旧候选 `29c2cfd4` 曾产生 `--network none` 安装、卸载和重装均成功的结构化证据。它只证明旧候选链路曾经跑通，不能复用为当前产物证据。

## 13. 经验沉淀规则

每次真实制包、断网演练或目标机故障收敛后，应在同一轮工作中完成：

1. 保存原始失败诊断和对应候选身份。
2. 写清症状、失败阶段、根因和影响面。
3. 将有效修复编码进公共脚本，不只给现场临时命令。
4. 增加能在修复前失败、修复后通过的门禁。
5. 在本手册故障矩阵中补充稳定经验。
6. 在当轮验证台账中记录 commit、manifest、命令、结果和未验证项。
7. 只有跨轮稳定、已真实验证的规则才进入 `AGENTS.md` 或个人 packaging skill；猜测和一次性路径留在验证台账。

现场问题的默认反馈顺序是：自动失败诊断 → `03` 诊断文件/包 → 必要时补截图。不得重新回到“截图一张、猜一个命令、再打一次包”的循环。
