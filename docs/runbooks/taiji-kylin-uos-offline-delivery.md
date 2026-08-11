# 太极 Agent 国产 x86 Linux 离线交付运行手册

## 1. 文档目的

本文档沉淀太极 Agent 在国产 `x86_64/amd64` Linux 终端上的制包、完全离线安装、Docker 演练、真实桌面验收和故障诊断经验。目标是把已经确认的失败原因固化为脚本门禁，减少现场依赖截图、临时命令和多轮往返。

本文面向研发、发布负责人和现场交付人员。目标终端操作员使用随交付目录提供的 [`操作说明.md`](../../taijiagent%20打包交付/操作说明.md)；本手册负责解释为什么这样做、Docker 能证明什么、哪些结论必须回真实 Kylin/UOS 终端验证。

本文不是某个 commit 的发布证明。最终发布身份必须由当前交付目录中的 `taiji-package-manifest.json`、`.build-success`、各级 SHA256、断网演练证据、认证集、v3 发布回执和签名共同确定。

## 2. 支持矩阵

当前 DEB 主线的目标范围：

- Linux `x86_64/amd64`。
- Debian-like Kylin、UOS、openKylin 类系统。
- `apt-get`、`apt-cache`、`dpkg`、`systemctl` 和 `sudo` 可用。
- 具备图形桌面会话，能够启动 Electron 应用。
- 内部断网演练和真机验收使用完整重建的 `taijiagent 打包交付/` 工作区；门禁通过后客户只使用唯一统一 basename DEB。

上述是产品设计与制品约束，不等于所有目标发行版都已实测支持。某个 Kylin/UOS/openKylin 版本只有取得与当前产物绑定的真实目标机证据后，才能写成该版本“已验证”。

当前主线不支持：

- ARM/aarch64。
- 只有 RPM 包管理器的终端。
- 没有可用包管理器或管理员能力的终端。
- 禁止 `/opt`、systemd、本地 DEB 安装、Electron 沙箱修复或本地 loopback 服务的强隔离环境。
- 用同一个 DEB 覆盖所有“国产 x86”发行版和安全策略。

RPM-only 终端需要单独的 RPM 制品；无包管理器或强隔离终端需要单独的 `.run` 或现场定制方案。

## 3. 四级证据口径

状态汇报只能使用以下四级标签，不得跨级推导：

| 标签 | 必须具备的证据 |
| --- | --- |
| 源码包已准备 | 当前基线只有一个源码包及由其导出的成员清单；basename 与当前候选源提交一致；`SHA256SUMS.txt` 精确绑定两者；源码发布预检通过 |
| 制包机已构建 | 兼容 Linux amd64 制包机生成单一 DEB、sidecar、manifest、构建报告和 `.build-success`，最终发布预检通过 |
| 离线安装已演练 | 干净 Linux amd64 容器、VM 或 chroot 在断网状态下只使用本地交付物完成安装、验证、卸载和重装，并生成当前产物绑定证据 |
| 目标机已验证 | 真实 Kylin/UOS/openKylin 图形终端完成安装态 Electron 启动、CLI、真实模型对话、附件、关窗退出和诊断导出 |

源码测试、macOS Electron App、旧 commit 的 DEB、旧日志或截图都不能替代当前产物的后一级证据。最终销售放行还要求两类证据经过发布负责人复核、签名，并通过 `scripts/taiji-release-check.sh`。

### 3.1 单一 DEB 销售交付契约

客户侧的目标是双击一个安装文件，因此销售目录只交付一个 `.deb`；构建报告、摘要、断网演练和真机验收材料保留在内部发布档案。这个简化只发生在客户交付面，不得删除内部证据链。

单一 DEB 不是跨发行版通用包。必须遵守以下边界：

- 统一包由源码受控的 `packaging/linux/compatibility-policy.json` 定义支持架构、系统家族、glibc/内核下限和最小系统能力；不从每台终端采集 baseline，也不按 profile 生成 DEB。
- `packaging/linux/certification-matrix.json` 固定六个正向类别和六个负向边界；代表环境记录只绑定同一候选 DEB/policy/source SHA。
- 正式构建只接受 canonical policy，policy、Electron、Node、Python/native wheel 或生命周期变化后重新跑完整矩阵。
- DEB 内嵌 policy 和 ABI 报告，`preinst` 按能力合同做本地 `COMPATIBLE/BLOCKED` 预检；不捆绑 glibc，也不替换系统核心库。
- `packaging/linux/deb/publish-single-deb.sh` 只把经过审计的同一 DEB 字节复制到一个全新客户目录，并验证该目录恰好只有一个文件；摘要和 publication receipt 写入内部证据目录。
- 只有同一 DEB 在矩阵代表环境完成双击安装、首次配置、真实业务流、关闭、卸载/重装和适用升级验收，并形成 signed certification set 后，才能使用“目标机已验证”。

如需扩大 Kylin/UOS/openKylin 版本或策略范围，更新 policy/matrix 后重新构建、重新演练和重新验收，不能从相近品牌或旧截图外推。

## 4. 四类环境的职责

| 环境 | 负责内容 | 不能据此宣称 |
| --- | --- | --- |
| macOS/开发机 | 清理源码、生成唯一输入包、静态检查、单元测试 | 已生成目标 DEB、已完成离线安装、目标机已验证 |
| Linux amd64 制包机 | 构建 Linux Python/Node/Electron runtime、单一 DEB、manifest 和报告 | 真实 Kylin 桌面 App 已通过 |
| Docker/VM 断网演练 | 校验 manifest 绑定的唯一 DEB 和安装→卸载→重装生命周期 | UKUI、kysec、Electron 桌面、真实模型已通过 |
| 真实国产终端 | 验证系统策略、桌面启动、真实业务链和关闭行为 | 其它未测试发行版也必然兼容 |

## 5. 标准交付链

### 5.0 冻结 canonical policy 和认证矩阵

正式构建前只需要复核源码中的 canonical policy 和 matrix，不需要向每台终端收集 baseline：

```bash
python3 packaging/linux/compatibility_policy.py validate \
  --policy packaging/linux/compatibility-policy.json --print-sha256
python3 -m unittest tests.test_certification_matrix_contract
```

policy 是唯一 Maintainer、最小系统能力和 ELF ABI 来源；matrix 固定六个正向类别和六个负向边界。目标终端执行的是 `04_目标终端_桌面App验收并导出证据.sh`，输出环境记录时绑定当前候选 DEB、policy SHA 和 source commit，不回写或生成新的 DEB。

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

脚本只预先要求可用的 `apt`/`dpkg` 和 `sudo` 管理员能力，不要求制包机预装
Python；它会先通过 apt 安装 `python3`/`python3-dev` 和其余构建依赖，再按
`XDG_CACHE_HOME/taiji-agent-build-<uid>`、`$HOME/.cache/taiji-agent-build-<uid>`、`/var/tmp/taiji-agent-build-<uid>` 顺序选择构建根；显式 `TAIJI_BUILD_ROOT` 只接受绝对路径、当前用户 0700 专用目录。每个候选都必须通过“可执行文件运行 + 共享库动态加载”探针，探针结果和 `findmnt -T` 会写入失败诊断；不再把 `/tmp` 作为默认构建根，也不建议关闭麒麟安全策略。

制包前建议为选中的构建根所在文件系统预留至少 `20 GiB` 可用空间。脚本在清空并重建受控构建根后会强制检查至少 `12 GiB` 可用空间和 `100000` 个可用 inode，不满足时在解压源码和下载依赖前立即终止，避免运行数十分钟后才因 `No space left on device` 失败。

源码、Node/uv 工具链和所有 npm/Python 临时文件统一位于选中的构建根下，脚本导出 `TMPDIR`、`TMP`、`TEMP` 指向该根。只有脚本正常结束、最终发布预检通过，才可标记“制包机已构建”。脚本会在解包正式源码后再次逐字核对维护人；看到 DEB 文件但 manifest、报告、sidecar 或 `.build-success` 缺失时仍属于失败。Electron 下载归档必须与 canonical policy 固定的版本、basename 和 SHA256 一致，且实际写入 DEB 的整个 `dist/` 文件清单及逐文件内容必须与该归档一致；不再只检查 8 个 ELF。构建时还会验证蓝色太极 Logo 的 RGBA PNG、hicolor 多尺寸、AppStream、desktop-id/WM_CLASS、Electron 窗口图标和安装态资源同源。

正式 Python/Node 工具链采用“归档身份 + 实际可执行文件身份”双重绑定：Python `3.11.15` standalone 归档 SHA256 为 `2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a`，`python3.11` 可执行文件 SHA256 为 `5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba`；`uv 0.12.2` 归档 SHA256 为 `d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4`，可执行文件 SHA256 为 `72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2`；Node.js `22.23.1` 归档 SHA256 为 `9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578`，可执行文件 SHA256 为 `93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068`；Electron `39.8.10` 的实际 `electron` ELF SHA256 为 `c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d`。`00`、`build-deb`、`01`、manifest/marker、发布证据校验和组装器都必须精确匹配这些常量；只在现场伪造版本输出或 archive marker 不能通过。

源码身份不只是一个 commit 字符串。`99` 必须由原始 `git archive` 生成 `*.inventory.json`，记录每个成员的路径、类型、模式、大小和内容摘要；`00` 在解压前校验归档，并在解压后、构建前、DEB 打包前和最终 `01` 再次校验实体树。校验工具本身用源码固定 SHA256 验证，不允许可写树携带一个宽松验证器给自己作证。

正式 Python 依赖只能对提交态 `uv.lock` 做一次写入式 strict sync；DEB 构建器随后只允许用 `uv sync --locked --check` 做只读一致性复核。未设置 `TAIJI_UV_LOCK_MODE` 与显式 `strict` 等价；`auto`、`unlocked`、任何 `TAIJI_ALLOW_UV_LOCK_REFRESH` 和二次无锁 `uv pip install -r` 都必须 fail closed。sync 前后必须重算并保持同一 `uv.lock` SHA256。WebUI requirements 只能是 Agent 直接依赖在 lock 中的精确版本子集，并在生产 venv 中核对实际安装版本和 import。`01` 在尚未生成 DEB 的 source-only 阶段也会检查源码包中的严格入口，防止降级后的制包输入被传到 Linux 制包机。旧 v3 如果缺少这些字段，当前正式门禁会明确拒绝；如需查阅，只能在门禁之外把原文件作为历史资料只读查看，不得伪补字段升级为当前正式 v3。

最终 ELF 闭包审计必须采用 **payload closed-world** 口径：每个最终 ELF 的 `DT_NEEDED` 只能由 DEB payload 内的 ELF、policy 明确允许的 Electron companion、审计器固定的基础运行时边界或 `required_system_sonames` 解决。制包机 sysroot 可以作为私有库暂存阶段的受信来源，但绝不能替最终 DEB“证明运行时已经有这个库”。否则制包机安装的完整 GTK/Electron 构建依赖会掩盖 DEB 中实际缺失的传递依赖，直到干净终端的 `postinst` 才以 `ldd ... not found` 失败。需要随产品携带的库必须进入 `/opt/taiji-agent/runtime/lib` 并受 `allowed_private_sonames` 约束；由目标系统提供的少量核心图形/安全库必须进入 `required_system_sonames`，不能留作未分类依赖。

`00` 重试时只处理自己能够证明归属的路径：已知的上轮产物和安全的旧 PID `.验收工具.tmp-*` 会自动归档到内部 `旧版备份/`；符号链接、非当前用户节点、硬链接或其它不安全残留会 fail closed，不会静默覆盖。上轮输出目录整体移入本轮 PID 备份后，如果创建新目录失败或收到信号，只会在状态证明新目录由本轮创建、仍为当前用户所有的实体空目录时删除并恢复旧输出；出现任何未知内容则绝不覆盖。验收工具也先写入本轮临时目录，再把旧目录移入带本轮 PID 的备份；如果发布的第二次移动失败，或在替换窗口收到 `INT`、`TERM`、`HUP`，`EXIT` 清理会在目标路径仍缺失时恢复本轮备份。

### 5.3 在受控发布机执行断网生命周期演练

```bash
docker build --platform linux/amd64 \
  -t taiji-offline-rehearsal:local \
  tools/taiji-offline-rehearsal

export TAIJI_OFFLINE_REHEARSAL_CHALLENGE="$(openssl rand -hex 32)"
# 扩展生命周期的标准入口：candidate、N-1、当前 manifest 和 canonical policy 必须显式绑定。
python3 scripts/produce-taiji-offline-rehearsal.py \
  --deb "taijiagent 打包交付/生成的安装包/taiji-agent_<version>_amd64.deb" \
  --previous-deb "/受控归档/N-1/taiji-agent_<n-1-version>_amd64.deb" \
  --build-manifest "taijiagent 打包交付/生成的安装包/taiji-package-manifest.json" \
  --policy "packaging/linux/compatibility-policy.json" \
  --output-dir "taijiagent 打包交付/offline-install-rehearsal" \
  --image taiji-offline-rehearsal:local \
  --challenge "$TAIJI_OFFLINE_REHEARSAL_CHALLENGE"

# 当前 v3 单 DEB 目录入口：用于 fresh/reinstall 快速演练，不能替代上面的 N-1 全生命周期。
python3 scripts/produce-taiji-offline-rehearsal.py \
  --delivery-dir "taijiagent 打包交付" \
  --output-dir "taijiagent 打包交付/offline-install-rehearsal" \
  --image taiji-offline-rehearsal:local \
  --challenge "$TAIJI_OFFLINE_REHEARSAL_CHALLENGE"
```

输出目录必须事先不存在。显式全生命周期入口的 N-1 Debian 版本（包括 epoch、upstream version 和 revision）必须严格小于候选版本；同版本或更高版本不得冒充 N-1。生产器应验证镜像角色、`ubuntu-20.04` 兼容基线和 `kylin-os-release-v1` fixture label，使用 `--network none`、只读挂载交付目录，并在演练前后重新校验 v3 manifest、唯一 DEB、sidecar、canonical policy 和完整交付清单。runner 必须先在未改动的镜像状态核对真实 Ubuntu 20.04 和断网状态，之后才可在该一次性容器内原子激活 Kylin policy fixture：只把可信 `/usr/lib/os-release` 改成 `ID=kylin`、建立 canonical `/etc/os-release` 软链接并创建桌面会话目录；候选 DEB、canonical policy 和生产 `preinst` 均不得改动或绕过。

当前排序门禁只证明 Debian `previous < candidate`，尚不能独立证明 previous 是发布台账中紧邻的上一个已签名版本。在 prior release-evidence/signature 绑定或首发 signed waiver 门禁完成前，这项结果不得表述为“已证明紧邻 N-1”。

演练镜像不得安装一整套宽泛桌面依赖来制造假绿。`tools/taiji-offline-rehearsal/ubuntu20-required-system-packages.tsv` 必须与 canonical policy 的 `required_system_sonames` 一一对应；镜像只安装这组 Ubuntu 20.04 系统边界包，并用 `ctypes.CDLL` 在镜像构建阶段逐个确认 SONAME 可加载。其余非核心依赖必须由候选 DEB 自己闭合。映射缺项、多项或把 payload 私有库偷偷放进演练基线，都应在 Docker 启动候选安装前失败。

仓库官方 Docker producer 的正式输出为 `schema=taiji.offline-install-rehearsal.v1`、`status=PASS` 的结构化证据，绑定 source commit、version、DEB/policy 摘要、`delivery_inventory_sha256` 和同目录会话日志。该 producer 的 `environment` 固定为 `container-kylin-policy-fixture-v1`，`os_id/os_version` 仍如实记录容器基线 `ubuntu/20.04`；这只证明未修改候选 DEB 在兼容基线上的断网 `dpkg` install/remove/purge/reinstall 与维护脚本链，不证明运行了真实麒麟、统信或 openKylin。`desktop_app_verified` 和 `target_verified` 必须保持 `false`。通用 v1 schema/validator 为历史证据保留 `container/vm/chroot` 的读取与绑定校验兼容，但当前 certification set 组装门禁只接受上述新 fixture 身份与 Ubuntu 20.04 基线，旧 v1 不能进入当前发布认证集合，也不能充当真实目标机证据。validator 后续复核时会重算当前交付清单摘要；演练后替换验收工具、脚本或任一未排除交付文件，都会使旧证据失效。当前 v3 证据不包含 target baseline 字段。历史 v2 只能通过 validator 的显式 `--legacy-v2-read-only` 路径查看，不能作为当前发布证据。

### 5.4 在真实目标机安装并验收

真实验收必须覆盖两条路径，但不能在同一系统状态中混跑：

1. **客户单 DEB 路径是 `04` 的前置路径。**在符合 canonical policy、没有 `taiji-agent` dpkg 记录且当前用户没有太极 XDG/Electron 状态的干净图形终端，准备一个只有 manifest 同名候选 DEB 的实体目录。先启动 `验收工具/observe-single-deb-install.py observe`，再关闭全部非 loopback 网络并从文件管理器双击 DEB，由系统图形包安装器完成安装。观察器必须从安装前持续存活到 `install ok installed`。
2. **内部生命周期路径使用另一个干净环境或另一个恢复点。**完整工作区中的 `02_目标终端_安装并验证.sh` 用于 root staging、单 DEB、诊断、升级/同版本重装等验证。不得在准备运行 `04` 的同一目标状态上先执行 `02`，否则安装前观察合同已经失效。

`04` 在平台、图形环境和长流程输入检查之前，先以严格 JSON 解析确认 `schema=taiji-package-manifest/v3`；历史 `schema_version=2` 在入口立即拒绝，不得启动桌面验收流程。

单 DEB 路径的命令、人工见证和首次配置顺序见第 10 节。机器观察记录替代旧的事后自报环境变量；`04` 不接受 `TAIJI_TARGET_INSTALL_METHOD`、`TAIJI_TARGET_INSTALL_NETWORK`、`TAIJI_TARGET_DPKG_STATUS_BEFORE` 或 `TAIJI_TARGET_FIRST_LAUNCH`。

系统无法自动识别“鼠标双击”本身。机器记录只证明同机同启动周期、安装前无包记录、唯一同名同 hash DEB、持续断网、无首次用户状态及 dpkg 迁移；操作员必须另附至少 800x600、chunk/CRC/IHDR 有效的系统图形安装器 PNG，并使用严格确认语句出具人工见证。发布负责人复核这些原始材料后，才可对顶层目标证据签名。

### 5.5 签名与最终放行

断网演练和代表环境验收使用不同 challenge。各环境记录聚合为 certification set 后，发布负责人检查原始会话、截图和诊断内容，再用独立离线私钥签名；发布回执另用独立 challenge。最终门禁必须复用当轮原 challenge，不能重新生成：

certification validator 会对六个正向和六个负向环境记录逐条比对 `source_commit/version/architecture/deb_basename/deb_sha256/compatibility_policy_id/compatibility_policy_sha256`，必须与顶层 v3 `BuildBinding` 完全一致；摘要和字段结构合法不能代替这项逐记录身份校验。认证集还必须把每条记录声明的 target/driver/screenshot/preflight 等附件和整个断网演练证据目录归档在固定子目录中，逐文件复算 SHA256/大小/清单摘要，再重跑基础会话、扩展生命周期原始日志、previous DEB basename/version/SHA256 以及 Debian `previous < candidate` 语义校验。签名器会在签名前执行同一完整实物校验；只剩顶层 JSON 或任意摘要字符串不能发布。

```bash
export TAIJI_CERTIFICATION_CHALLENGE="<当轮认证集原值>"
export TAIJI_PUBLICATION_CHALLENGE="<当轮发布回执原值>"
bash scripts/sign-taiji-release-evidence.sh \
  "taijiagent 打包交付/certification/certification-set.json" \
  "/受控离线路径/offline-release-private-key.pem"
bash scripts/sign-taiji-release-evidence.sh \
  "taijiagent 打包交付/release-evidence.json" \
  "/受控离线路径/offline-release-private-key.pem"
bash scripts/taiji-release-check.sh
```

门禁通过后才能生成客户目录；输出目录必须事先不存在，receipt 是内部档案，不交付客户：

```bash
mkdir -p customer-output internal-release-receipts
bash packaging/linux/deb/publish-single-deb.sh \
  --delivery-dir "$PWD/taijiagent 打包交付" \
  --candidate-deb "$PWD/taijiagent 打包交付/生成的安装包/taiji-agent_<version>_amd64.deb" \
  --policy "$PWD/packaging/linux/compatibility-policy.json" \
  --certification-set "$PWD/taijiagent 打包交付/certification/certification-set.json" \
  --certification-signature "$PWD/taijiagent 打包交付/certification/certification-set.json.sig" \
  --release-evidence "$PWD/taijiagent 打包交付/release-evidence.json" \
  --release-signature "$PWD/taijiagent 打包交付/release-evidence.json.sig" \
  --output-dir "$PWD/customer-output/taiji-agent-linux-amd64" \
  --receipt-root "$PWD/internal-release-receipts/single-deb"
```

发布脚本会先快照候选 DEB、policy、两组 signed evidence，以及认证集的全部 `records/` 附件和 `offline-rehearsal/` 原始证据，再执行正式 release-check；门禁期间任何实物增删、替换或改动都会失败且不生成客户目录。最后以不可替换 rename 原子生成新客户目录，并把 `release-evidence.json`、两组签名、policy 和 `deb.sha256` 六个文件归档到内部 receipt。这六文件是 publisher 回执，不是完整认证档案；完整 certification `records/`、`offline-rehearsal/` 及其它受控原始附件必须在内部认证归档中持续保留。客户只收到该目录中的固定 basename DEB，不收到内部工作区、私钥、receipt、manifest 或验收材料。

## 6. 完整离线交付契约

完整目录至少包括：

```text
taiji-agentv1.0-kylin-build-src-<commit>.tar.gz
taiji-agentv1.0-kylin-build-src-<commit>.inventory.json
source-archive-integrity.py
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
验收工具/
```

必须同时满足：

- 当前源码包和对应成员清单各自唯一且 SHA256 匹配，固定工具复验后的每个归档成员与解压树都与清单一致。
- v3 manifest 的完整 `source_commit` 必须唯一决定源码包和成员清单 basename；根 `SHA256SUMS.txt` 只能精确记录这两个 basename 和内容 SHA，`.build-success` 中的 source/inventory/DEB/policy/ABI/icon/maintainer 身份也必须与 manifest 和当前文件一致。即使旧源码包内容 SHA 正确，只要 basename 不是当前完整 commit，仍必须拒绝。`.build-success` 只能在所有最终门禁通过后原子发布，失败路径不得留下该文件。
- `生成的安装包/` 只有一套允许的当前产物。
- `.deb.sha256` 只记录 basename，不记录制包机绝对路径。
- v3 当前路径不得混入历史 `离线依赖/Packages*` 或第二个安装包；客户边界是 manifest 绑定的唯一 DEB。
- 最终 DEB 真实解包后，Python、Linux Electron ELF、Web runtime、CLI、desktop entry、配置模板、诊断、授权公钥和产品 Skills 均满足 payload contract。
- 最终 Web 静态文件不依赖 jsDelivr、unpkg 等公网 CDN。
- 当前发布清单路径不包含旧 DEB、旧 zip、多个源码包、构建日志、macOS metadata、客户授权、私钥、API Key 或本地会话。
- 内部 `旧版备份/` 可以保存 `00` 自动归档的历史 DEB、已知上轮产物或验收工具临时残留；`delivery_inventory_sha256` 明确排除该目录，因此其中内容不能充当当前候选、当前证据或客户输入。
- 客户发布目录仍严格只包含一个 DEB；内部备份、manifest、报告、sidecar、验收工具和签名证据均不得复制到客户目录。

## 7. Docker 能覆盖与不能覆盖的边界

### 7.1 Docker 可以覆盖

- Linux amd64 架构和 Ubuntu 20.04/glibc 2.31 兼容基线。
- Linux Python、Node 和 Electron runtime 的构建，以及不借用 build sysroot 的最终 payload closed-world ELF/共享库审计。
- DEB payload、manifest、sidecar、`.build-success` 和单 DEB 产物绑定完整性。
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

### 7.3 安装、升级与卸载生命周期边界

- `postinst configure` 必须在无图形、无用户 HOME 的 system-only 环境中执行安装态原生校验；脚本权限、Electron `chrome-sandbox` 或 native verify 任一失败都必须返回非零。重复执行 `dpkg --configure taiji-agent` 应可安全重试。
- 管理端静默部署在 `dpkg` 成功后的二次 native verify，以及 upgrade 失败后对 N-1 的恢复校验，必须与 `postinst` 共用同一个受控边界：`env -i` 保留安全 `PATH`/`LANG`，并显式调用 `taiji-native-verify --system-only`；N-1 恢复校验只额外允许 `TAIJI_AGENT_SYNC_PACKAGED_CONFIG=0`，避免验收过程重写已恢复的用户配置。不得在无 `HOME` 的 root 安装环境调用默认用户模式，也不得依赖会被安装态 wrapper 清理掉的外部模式变量。
- 安装态 payload 必须保持不可变。`installed-production` 在每一次清理外部 Python 环境变量后都必须由受信源码重新固定并导出 `PYTHONDONTWRITEBYTECODE=1`；`postinst`、静默部署二次校验和 N-1 恢复校验均不得在 `/opt/taiji-agent` 下生成未由 `dpkg` 登记的 `__pycache__/*.pyc`。断网生命周期必须比较安装清单与磁盘实物，并以 purge 后 `/opt/taiji-agent` 不存在作为放行条件。
- `dpkg` 成功不等于部署成功：安装后 verifier 缺失、不可执行、日志无法安全创建或校验返回非零时都必须 fail closed。`installed/reinstalled/upgraded/rolled_back` 回执必须同时满足 `native_verify=PASS`，回执校验器也必须拒绝“成功结果 + FAIL/NOT_RUN”的伪成功组合。
- `preinst` 必须在解包前探测 canonical policy 声明的全部 `required_system_sonames`：真实目标机使用受信 `ldconfig -p`，模拟根只检查受控的标准 x86_64 库目录；缺失任一项时以 `TAIJI-LINUX-E014-RUNTIME` 失败关闭，禁止先写入半包再碰运气。
- 静默部署器执行当前 DEB 或 N-1 DEB 的 `dpkg --install`，以及两者的 native verify 时，必须把完整输出写入权限 `0600` 的 root-only 临时日志；返回非零时在清理前向操作员回显最后 80 行。不得再把维护者脚本和 native verify 的关键错误重定向到 `/dev/null`，否则现场只能看到笼统失败并被迫重复制包。成功后立即删除这些临时日志，失败时在写回执后按固定路径清理。
- 已由 `dpkg` 管理的现有安装直接走 apt/dpkg 原生升级或同版本重装；`02` 不得预先 unhold、purge、强制删除包状态或手工删除 `/opt/taiji-agent`。
- `02` 提权后复制候选 DEB 或 N-1 DEB 时，必须把 DEB 与同名 `.sha256` 作为一组暂存，并保留 manifest/sidecar 绑定的原始 DEB basename；不得改名为 `candidate.deb`、`previous.deb` 等内部别名。sidecar 首行的 basename 与实际暂存 DEB 不同必须在调用 `dpkg` 前以 `DEB_SHA256_SIDECAR_MISMATCH` 或 `PREVIOUS_DEB_SHA256_SIDECAR_MISMATCH` 失败关闭。
- 没有 `dpkg` 状态但存在旧系统安装时，`02` 仅允许清理固定白名单内的 legacy 路径；用户 XDG 配置、授权、密钥、会话和附件不在清理范围。
- `prerm` 只能按 `/proc/<pid>/exe` 的物理路径识别 `/opt/taiji-agent/` 所属进程，并在发送 `SIGKILL` 前重新核验，禁止使用 `pkill/pgrep -f`。
- 普通 remove 不清用户状态；purge 只清理已知的 root-owned、非 symlink 系统状态目录。发现 symlink、非 root owner、mountpoint 或“白名单目录实际是普通文件”的类型不匹配时应保留并告警，不能扩大递归删除范围。`/opt/taiji-agent` 顶层空目录也必须通过目录类型、root owner、非 symlink 和非 mountpoint 门禁后才能 `rmdir`。
- 极简系统在 purge 后可能由 `dpkg` 一并移除已变空的 `/opt`。后续 reinstall 的 `preinst` 不得把“目标父目录尚不存在”误报为磁盘不足：`/opt` 缺失时只向上探测到受控根内最近存在、owner/mode 可信且非 symlink 的祖先，并在同一 probe 上执行 6144 MiB、noexec 和 canary 门禁；预检本身不创建 `/opt`，由 dpkg 正常解包创建。`/opt` 若已存在但为 symlink、断链 symlink 或非目录对象仍必须以 `TAIJI-LINUX-E009-DISK` 失败关闭。
- `02` 的 root-owned management staging 在运行任何 Python helper 前必须固定 `PYTHONDONTWRITEBYTECODE=1`，避免留下只含 `__pycache__` 的 `/var/tmp/taiji-agent-management.*` 空壳；不得以递归扫描和删除历史暂存目录代替当前调用的无残留合同。
- Debian 原生 `postinst configure` 失败仍会留下未配置完成状态；维护者脚本本身不承诺恢复旧二进制，也不会触碰用户数据。`02`/管理端静默部署的 `upgrade` 模式在同时拿到已校验的 N-1 DEB、SHA256 sidecar、detached signature、N-1 manifest/数据契约和业务用户时，才进入 root-owned transaction journal：先停受管运行时、快照配置/授权/会话/附件/workspace/Skills/模板及 SQLite，再执行 dpkg；失败会尝试安装 N-1 并恢复快照。`rollback` 是显式的单向降级：`PREVIOUS_DEB` 就是降级目标，当前版本 DEB 不作为第二个恢复制品绑定；因此降级期间的 dpkg/native verify 失败不会假称已恢复原版本，而是保留 `manual_recovery_required`，由运维用归档的当前版本材料处理。任一升级恢复动作失败同样保留 `manual_recovery_required`。该事务回滚边界尚未在真实麒麟/统信目标机以 dpkg 失败场景实时验证。
- N-1 detached signature、其 `.sha256` sidecar 和对应 manifest 属于受控运维材料，不进入客户“只含一个 DEB”的目录；发布负责人必须在受控签名机用与目标机内置公钥对应的离线私钥生成并归档，升级调用方显式传入 `TAIJI_PREVIOUS_DEB`、`TAIJI_PREVIOUS_SHA256`、`TAIJI_PREVIOUS_SIGNATURE` 和 `TAIJI_PREVIOUS_MANIFEST`。普通客户 fresh install 不需要这些升级输入。
- 从含旧 `prerm` 的历史包第一次直接升级时，dpkg 会先执行旧包脚本；新包无法追溯消除旧脚本行为。真实升级验收必须专门覆盖这一首跳边界。

### 7.4 本轮事务实现验证台账

- **已实时验证**：事务/维护脚本/部署回执/安装脚本/断网生命周期聚焦回归 `70` 项通过；Linux 静态门禁 `88` 项通过、`1` 项按平台条件跳过；相关 Bash 语法、Python 编译、JSON 校验和 `git diff --check` 通过。
- **本轮新增回归要求**：`tests/test_kylin_install_script_simulation.py` 必须证明候选 DEB 和 N-1 DEB 在 root management staging 中均保留原 basename、携带同名 sidecar，且不再出现 `candidate.deb`/`previous.deb`；同时执行 `bash -n taijiagent\ 打包交付/02_目标终端_安装并验证.sh`。冻结新提交并重建 DEB 后，还必须用完整交付目录重跑断网 fresh/reinstall（以及提供 N-1 材料时的 upgrade/rollback）生命周期。
- **未实时验证**：真实麒麟、统信或 openKylin 终端；真实 `dpkg` maintainer failure 后的 N-1 自动回滚；真实 detached signature 验签；真实图形桌面安装和升级/卸载。
- **验收边界**：上述聚焦测试只证明当前分支代码和模拟夹具的合同，不提升“制包机已构建”“离线安装已演练”或“目标机已验证”任一证据等级；冻结源码后仍须在 Linux amd64 制包机重建 DEB、执行断网生命周期，再绑定真实目标机证据。

### 7.5 本轮 ELF 闭包修复验证台账

- **真实失败证据**：旧候选 DEB 在干净 Ubuntu 20.04 兼容演练容器中执行 `dpkg --install` 返回非零并留下 `half-configured`；`postinst` native verify 日志显示 Electron 多项传递共享库 `not found`。旧 ABI 报告同时显示这些 SONAME 曾被制包机 sysroot 错误满足，因此旧候选不得继续交付。
- **已实时验证（分支源码级）**：修复前新增的 5 个负向合同稳定失败；独立复审提出的隐式非 glibc 系统边界和 i386-only provider 缺口也先由负向测试复现，再纳入 canonical policy 与 `preinst`。最终 486 项完整 Python 测试通过、2 项按平台条件跳过，全部受控 shell 脚本通过 `bash -n`，`git diff --check` 通过；独立复审为 P0=0、P1=0。Ubuntu 20.04 amd64 演练镜像已按 16 项 required-system 映射成功重建，但此时尚未向它投入本轮新 DEB，不能据此记为离线安装已演练。
- **冻结前仍须闭合**：从正式 `main` 生成新的制包输入包，在 Linux amd64 从头重建同一源码身份的 DEB；对实际 DEB 重跑 closed-world ABI 报告，并在新演练镜像中以 `--network none` 完成 install/remove/purge/reinstall。任何新建镜像或新产物结果都必须绑定最终 source commit、manifest 和 DEB SHA256 后才可提升证据等级。
- **未实时验证**：本轮新 DEB 的完整制包结果、断网生命周期结果，以及真实麒麟、统信或 openKylin 图形终端的双击安装、桌面启动、窗口图标、模型对话和附件流程。Docker 通过后的最高口径仍是“离线安装已演练”，不能写成“目标机已验证”。

### 7.6 本轮 Kysec 预安装修复验证台账

- **真实失败证据**：Kylin V10 SP1 x86_64 终端的 Kysec 总状态为 `enabled`，但 `/usr/sbin/getstatus` 明确返回 `exec control : off`；旧 `preinst` 仅因 Kysec 存在就返回 `TAIJI-LINUX-E011-KYSEC`，使安全策略未阻断执行的正常终端被误拒绝。
- **修复合同**：只使用固定 `/usr/sbin/getstatus`，校验 `/usr`、`/usr/sbin` 和工具的 owner、mode、类型、执行位与单硬链接；仅接受唯一 `exec control : off|on` 状态行。`off` 继续其他能力门禁，`on` 保持 E011 阻断，命令缺失、不可信、失败或输出不可识别时以 E011 失败关闭；不关闭、不修改、不绕过 Kysec。
- **已实时验证（分支源码级）**：首轮修复前新增用例稳定出现 13 个预期失败，随后新增的单独断链 `getstatus` 信号也先证明旧检测会错误放行；最小修复后 Kysec/preinst 聚焦回归 27/27 通过，覆盖 `off`、`on`、缺失/非零/重复/未知输出、symlink/断链 symlink、权限、硬链接、父目录、`PATH` 注入和其它门禁不被遮蔽。同一渲染脚本在真实 Kylin V10 SP1/amd64/glibc 2.31 根文件系统返回 `COMPATIBLE`；该机 Kysec 为 `enabled`且 `exec control : off`，预检前后均为 `install ok not-installed`且 `/opt/taiji-agent` 不存在。
- **未实时验证**：版本 1.0.1 完整制包、断网生命周期和真实图形安装仍须绑定最终冻结提交和 DEB SHA256 重跑。

### 7.7 厂商 GPU 子目录不得污染私有库收集

- **真实失败证据**：首个 1.0.1 冻结候选在真实 Kylin 制包到 DEB staging 时，递归扫描到 `/usr/lib/x86_64-linux-gnu/innogpu-fh2m/libepoxy.so.0.0.0`。该厂商 GPU 子目录归 uid 1000 所有，安全门禁正确拒绝复制。
- **根因**：旧收集器递归扫描整个 sysroot，把未进入 `/etc/ld.so.conf*` 且未被动态链接器选中的厂商私有副本也当成正式候选。真实系统的 `ldconfig` 选中标准 `/lib/x86_64-linux-gnu/libepoxy.so.0`，它对应 root 管理的 `libepoxy0` 文件。
- **修复合同**：Debian amd64 布局存在时，无论 sysroot 指向 `/` 还是直接指向 `/usr/lib/x86_64-linux-gnu`，都只扫描标准 `/usr/lib/x86_64-linux-gnu` 和 `/usr/lib64` 的直接文件，不递归进入显卡等厂商子目录；隔离 fixture/sysroot 无标准 Debian 目录时保留原有通用扫描。选中文件仍必须通过 root 属主、单硬链接、普通文件、权威 SONAME、allowlist 和原子复制检查。
- **已实时验证（候选源码级）**：新增测试先稳定复现普通用户厂商副本阻断，并使用正式构建的直接多架构目录 sysroot 参数固化回归；最小修复后 ELF/ABI 聚焦测试 26/26 通过。真实 Kylin 使用 `/usr/lib/x86_64-linux-gnu` 快速 staging 成功收集 63 个 policy 允许库；`libepoxy.so.0` SHA256 与 root 管理标准文件一致，报告中没有 `innogpu` 引用。
- **证据边界**：失败的冻结提交及其输入包已废弃；修复必须经新提交、新输入包和从头制包后才能产生有效 DEB 证据。

## 8. 已确认故障经验矩阵

下表只记录本轮已经出现的真实失败，或已由针对性负向测试证明的高风险缺口。未验证猜测不得升级为长期规则。

| 症状 | 根因 | 修复 | 防复发门禁 | 验证边界 |
| --- | --- | --- | --- | --- |
| 真实 Kylin 制包拒绝 uid 1000 的 `innogpu-fh2m/libepoxy.so.0.0.0` | 私有库收集器递归扫描整个 sysroot，误把未被系统动态链接器选中的 GPU 厂商副本当作候选 | Debian amd64 只扫描标准多架构目录直接文件，不进入厂商子目录；不放宽文件信任校验 | 先红后绿回归 + 真实 sysroot staging + 新 commit 从头制包 | 候选源码快速验证已通过；新冻结提交完整制包待验证 |
| 真实 Kylin 终端安装在 `preinst` 返回 `TAIJI-LINUX-E011-KYSEC`，但 `getstatus` 显示 `exec control : off` | 旧逻辑把“存在 Kysec”等同于“执行控制已阻断”，未读取真实执行控制状态 | 信任固定且 root 管理的 `/usr/sbin/getstatus`；唯一 `off` 放行、`on` 阻断，未知或不可信状态失败关闭，不改动 Kysec | 27 项 preinst 聚焦回归 + 真实 Kylin 渲染脚本独立预检 + 最终 DEB 断网安装 | 当前源码回归已通过；最终制品、图形安装和其他 Kysec 版本仍需实物证据 |
| 太极 Agent 已在真实 Kylin 桌面安装并启动，`taiji-native-verify --system-only` 却因 `/api/settings`、`/api/model-config` 返回 `403` 失败 | 桌面版正确要求 Electron 持有私有桌面令牌；系统级校验器无令牌访问受保护接口，却把预期的拒绝误判为产品配置失败 | 系统级校验继续通过安装态配置文件校验产品默认值；当两个接口均返回精确的桌面访问拒绝时，改为确认安全门禁有效；不读取、传递或记录桌面令牌，也不放宽接口保护 | 回归模拟两个精确 `403` 响应必须成功；其它状态、拒绝内容不一致或只有单个接口被拒绝均失败关闭；新制品须在真实 Kylin 安装态重验 | 当前 1.0.1 制品暴露此校验误报；修复会生成新源码身份，必须重建 DEB，旧制品不能作为完整安装态通过证据 |
| 在 Kylin V10 SP1 中导入授权文件返回 `license_file_untrusted` | 目标授权目录和临时文件实际均为用户所有的 `0700/0600`；但该系统根目录 `/` 为 `root:root 0775`，1.0.1 把 root 特权组可写与普通组可写等同处理，从而同时误拒授权候选、安装态公钥和版本文件 | 仅对 `root:root` 所有的系统父目录允许组写；仍禁止所有人可写、root 配非 root 组可写、普通用户组可写、非当前用户所有和符号链接 | 三个 Kylin `root:root 0775` 回归分别覆盖授权候选、公钥和版本；负向用例覆盖普通组、所有人和异主体可写；在真实 Kylin 上使用不含授权正文的临时探针重验 | 1.0.1 不能完成授权导入；修复必须进入新版本并重建 DEB，不得要求现场修改 `/` 权限或关闭安全策略 |
| `npm audit` 向 `registry.npmmirror.com/-/npm/v1/security/audits/quick` 请求后返回 `404 NOT_IMPLEMENTED` | 依赖下载成功后把 install-only 镜像留在 `NPM_CONFIG_REGISTRY`，安全审计错误继承了不实现 audit API 的镜像；该响应不等于已经发现依赖漏洞 | 安装继续使用 `TAIJI_NPM_REGISTRIES`，审计单独使用 `TAIJI_NPM_AUDIT_REGISTRY`（默认 `https://registry.npmjs.org`，也可指定实现审计接口的 HTTPS 内网源）；URL 禁止内嵌凭据，需要认证时使用现场受控的标准 npm 配置 | 动态回归在继承 `npmmirror` 的环境中捕获 npm 参数，必须看到 audit 显式指定独立 registry；漏洞、网络和接口错误仍全部 fail closed | 已由 Kylin 制包机真实失败暴露；修复后的当前输入包仍须在制包机重新构建，不能据源码测试标记制包成功 |
| Linux 制包 `npm test` 多项失败并提示缺少 `@resvg/resvg-js-linux-*` | 普通 npm 安装只准备当前平台原生包，复制型 DOCX skill 却承诺多个 Linux CPU/ABI | 按 lockfile 下载、校验并原子物化 x64/arm64、gnu/musl 原生包 | lockfile integrity、包身份、ELF/架构校验、制包机真实 `npm test` | 已由真实制包失败暴露并修复 |
| apt 安装依赖时可能等待时区等交互输入 | 非交互环境没有稳定跨 sudo 传递 | 使用 `DEBIAN_FRONTEND=noninteractive` 和固定 `TZ` | 静态断言并在最小 Ubuntu 制包机实际执行 | 当前候选制包链已覆盖 |
| 极简 Ubuntu 20.04 制包镜像在 apt 安装前报 `缺少命令：python3` | 旧预检把本应由 apt 安装的 Python 和依赖 Python 的显式构建根探针放在依赖安装之前，形成自相矛盾的隐含前提 | 初始预检只核对 Linux amd64、apt/dpkg、摘要工具和 sudo；apt 明确安装 `python3`/`python3-dev` 后再选择并探测构建根 | 静态顺序回归 + 当前输入包最小 Ubuntu 20.04 amd64 端到端重放 | Docker 重放用于提前发现制包链问题，不能替代 Kylin/UOS 目标机安装验收 |
| 全新制包机安装 `uv` 时报 `mktemp .../tmp/tmp.XXXXXXXXXX: No such file or directory` | 选定构建根后已创建并导出 `TMPDIR`，但源码解压前的安全重置删除了整个构建根，只重建所有权标记，未恢复临时目录和工具目录 | 每次 `reset_build_root` 在重建可信根后都重新执行 `configure_build_tmp`，恢复目录、权限和 `TMPDIR/TMP/TEMP` 不变量 | 重置顺序静态回归 + 未预装 uv 的最小 Ubuntu 20.04 amd64 端到端重放 | 已由当前输入包的干净容器重放暴露；修复后的输入包须从头重跑 |
| DEB ELF 审计报 `payload symlink escapes audit root: /usr/lib/x86_64-linux-gnu/libpython3.8.a` | 报错路径实际属于可信构建机 sysroot，不是 payload；系统 `python3-dev` 的无关静态库别名会离开单个 multiarch 目录，旧的通用遍历器误套用了 payload 软链接边界 | payload 仍严格拒绝逃逸软链接；sysroot SONAME 查询不跟随也不消费任何软链接，只检查普通 ELF 目标及其权威 SONAME | sysroot 无关逃逸别名正向回归 + payload 逃逸软链接负向回归 + 真实 DEB 重放 | 不能据此放宽 payload 或 private-library 来源校验；当前输入包须继续重跑至最终预检 |
| DEB 已完成 Electron staging 后，`render-preinst.py` 在系统 Python 3.8 报 `TypeError: unsupported operand type(s) for \|` | 预安装脚本生成器的类型注解会在模块加载时求值，使用了 Python 3.10 才原生支持的 union 写法；既有 Python 3.8 门禁遗漏了这个真实 build-deb 入口 | 生成器启用 postponed annotations；Python 3.8 门禁补齐 preinst renderer 和 payload verifier 两个系统 Python 入口 | 修复前真实 Python 3.8 容器稳定复现；修复后门禁必须同时 compile、import 全部入口并继续端到端制包 | 由当前正式输入包的 Ubuntu 20.04 amd64 重放暴露；只证明生成器兼容，仍须继续到单一 DEB 和最终发布预检 |
| 构建已进入 DEB 真实解包时报 `No space left on device` | 旧脚本没有在重建构建根后核对可用 block/inode；最终预检又硬编码使用 `/tmp`，绕过已选中的 `BUILD_ROOT/TMPDIR` | `00` 在解匋源码前强制至少 12 GiB 和 100000 inode；`01` 的源码比对与 DEB 真实解包统一使用受控 `TMPDIR`，并在分配前复核空间和 inode；`df` 自身失败也转换为明确诊断 | 低 block、低 inode、`df` 非零和临界通过动态回归；静态保证容量门禁早于源码解压且预检不再硬编码 `/tmp` | 由 Ubuntu 20.04 amd64 端到端制包重放暴露；门禁值是制包下限，仍建议现场预留 20 GiB |
| DEB 已生成，最终发布预检报 `awk: unexpected character '\\'` | 单引号包围的 awk 程序内又对双引号加了多余反斜杠；macOS 侧未走到实物 sidecar 分支，Ubuntu/Kylin 常用 `mawk` 拒绝该语法 | 修正 sidecar 文件名和 ABI marker 两处 awk 程序；用真实 Bash/awk 执行 sidecar 解析夹具 | 回归要求预检脚本不含此类转义，且端到端重放必须到达“发布预检通过” | 修复后已在完整 DEB 上断网执行下游预检并返回 0；冻结新提交后仍须从头重建 |
| 旧候选 DEB 已生成，但干净演练容器的 `dpkg --install` 返回非零、包状态停在 `half-configured`，`postinst` 日志出现多项 Electron `ldd ... not found` | 旧 ELF closure 把制包机 sysroot 中的 provider 当成最终 payload 的运行时 provider；制包机安装的完整 GTK/Electron 构建依赖掩盖了 DEB 中缺失的传递库，旧报告仍留下未分类 external SONAME | 最终审计改为 payload closed-world；需随产品携带的库按 policy 暂存到私有目录，系统边界收敛为显式 `required_system_sonames`；演练镜像只按一一映射安装这组系统包，`preinst` 在解包前探测，静默部署失败时回显 dpkg 最后 80 行 | sysroot-only provider 必须被 final audit 拒绝；policy/Ubuntu 20.04 映射键集合必须完全相等；缺 required SONAME 必须返回 `TAIJI-LINUX-E014-RUNTIME`；新候选必须在 `--network none` 完成 install/remove/purge/reinstall 且 native verify 无 `not found` | 由旧候选实物安装失败暴露；源码回归不能证明新 DEB 已构建，更不能替代真实 Kylin/UOS/openKylin 图形终端验收 |
| DEB 的 `dpkg` 状态已是 `installed`，`postinst-verify.log` 也是 `FAIL=0`，但部署回执却显示 `NATIVE_VERIFY_FAILED` | 静默部署器在 `dpkg` 后又直接调用默认用户验证模式；上层为安全已用 `env -i` 清空环境，因此 `runtime-env.sh` 读取 `$HOME` 时在 `set -u` 下以 `HOME: unbound variable` 退出。恢复校验还尝试通过外部 `TAIJI_NATIVE_VERIFY_MODE` 切换模式，但安装态 wrapper 会主动清理该变量 | 安装后和恢复后验证统一改为受控 `env -i ... taiji-native-verify --system-only`，由 wrapper 内部固定 `HOME=/nonexistent`、系统状态目录、installed-production profile 和私有库路径 | 回归必须同时覆盖 fresh/reinstall 的安装后校验和 upgrade 的 N-1 恢复校验；实物仍必须在 `--network none` 演练中产生 `native_verify=PASS` 回执 | 已用本轮新 DEB 在演练容器实时复现：默认模式返回 1 并报 `HOME: unbound variable`，同一安装以 `--system-only` 返回 0、`OK=21 WARN=4 FAIL=0`；修复后最终交付证据仍需重建绑定 |
| `dpkg --purge taiji-agent` 后 `/opt/taiji-agent` 仍存在，dpkg 提示 Python 目录“非空” | `postinst` 和静默部署以 root 运行安装态 Python 校验；`runtime-env.sh` 两次清理全部 `PYTHON*` 变量后没有重建禁止字节码写入的受信值，CPython 因而在只应由 dpkg 管理的 payload 中生成数百个未登记 `.pyc` | `installed-production` 在两次环境清理之后都固定并导出 `PYTHONDONTWRITEBYTECODE=1`，确保后续配置同步、CLI 导入和 native verify 只读使用 `/opt` | 真实 runtime chain 必须证明 hostile/exact env 下配置同步阶段与最终校验阶段均得到 `dont_write_bytecode=1`；实物安装后连续执行 system-only verify，`comm -23` 对比 dpkg 清单必须为 0；断网 install/remove/purge/reinstall 必须完整通过 | 由提交 `8f842f1c` 的真实 DEB 在 `--network none` 演练中暴露；修复前实测 273 个未登记 `.pyc`。在该旧 DEB 的安装态临时替换修复脚本后连续校验，未登记文件降为 0，这只用于确认根因；最终证据仍须用包含本修复的新提交重新制包并重跑 |
| fresh install、native verify 和 purge 均成功，但 reinstall 的 `preinst` 返回 `TAIJI-LINUX-E009-DISK` | 极简兼容基线中的空 `/opt` 在 purge 时被 dpkg 一并移除；实际根文件系统仍有 20 GiB 以上空间，旧 preinst 却把“`/opt` 尚不存在”硬编码成磁盘不足。`02` 的 Python helper 还会生成极小的 management `__pycache__` 空壳，但不是 E009 根因 | `/opt` 缺失时在受控根内选择最近存在的受信祖先统一执行 df/noexec/canary，不创建 `/opt`，由 dpkg 解包创建；symlink/断链/非目录仍阻断。root management staging 在首个 Python helper 前禁写 bytecode | missing `/opt` 正向、symlink/断链/file、低磁盘、ancestor noexec/canary/unsafe mode 负向回归；真实 Linux purge 后在 `/opt` 不存在状态直接执行渲染 preinst 必须 `COMPATIBLE` 且仍不创建 `/opt`；最终 DEB 重跑断网三段生命周期 | 当前调试实测 fresh 前 20838 MiB、安装后 19994 MiB、purge 后恢复 20837 MiB且 `/opt` 消失，排除真实容量不足；新 preinst 源码级容器验证已通过，但仍须冻结新提交、重建 DEB 和生成绑定证据 |
| Electron 版本和 8 个 ELF 正确，但 `resources.pak`/ICU/snapshot/locales 可被替换 | policy 声明了整包 `archive_sha256`，旧 stager 却没有消费该字段，非 ELF 只检查“存在” | npm 使用受控私有 Electron cache；只选择 basename/version/SHA256 与 canonical policy 相同的 Linux x64 ZIP；stager 再把最终 staged `dist/` 清单及每个文件与固定 ZIP 比对 | 非 ELF 篡改、归档篡改、文件清单漂移回归；官方 `39.8.10 linux-x64` 归档实物验证 | 本机已实时核对官方 ZIP SHA256 为 `92e8b031...eabd1`；Kylin 制包仍待重跑 |
| manifest、最终预检或重试清理出现只读模板 `Permission denied` | 内置模板有意使用 `0444/0555`；普通制包用户能够校验内容，但不能直接删除无写权的解包目录；中断时原脚本也没有退出清理 | 构建根仍只按 owner marker 清理；发布预检只允许清理 `TMPDIR` 直接子级且 basename 为 `taiji-release-*` 的实体目录，不跟随软链接地逐目录恢复 owner 写权限；`EXIT/INT/TERM/HUP` 幂等清理保留原退出码 | `000` 嵌套 payload 清理、非受控路径拒绝和 `TERM` 中断动态回归 + 完整 DEB 断网下游预检 | 修复后已在上一轮完整 DEB 上返回 0；冻结新提交后仍须从头重建 |
| Kylin 制包在 `/tmp` 首个原生模块测试报 `failed to map segment from shared object` | 目标机安全策略对 `/tmp` 执行或动态库映射有限制，构建工具虽下载成功但 native `.node` 无法加载 | 默认不再使用 `/tmp`；选择 owner-only 用户缓存或 `/var/tmp` 构建根，并在解包/下载前真实运行 ELF 和 `ctypes.CDLL` 探针 | 候选根按顺序尝试；显式根探针失败立即退出；诊断包含候选、阶段、原始错误和 `findmnt` | 根因来自 2026-08-06 Kylin 日志；当前修复尚未在该制包机重建 |
| Web、开始菜单、Electron 窗口出现不同 Logo 或黑金 SVG | Web favicon、hicolor、desktop entry、Electron class 和安装态资源没有统一同源合同 | 以蓝色太极机器人 RGBA PNG 为 canonical；派生 32/48/64/128/192/256/512 PNG 与 ICO，AppStream/desktop/WM_CLASS/窗口图标/原生校验全部绑定 | `validate_icon_assets.py`、payload contract、native verify、图标链静态回归 | 源码资产和静态合同已实时验证；真实 Kylin/UOS 桌面缓存刷新和安装态仍未验证 |
| v3 单 DEB 发布门禁仍要求历史 `离线依赖/Packages*` | v2 内部 apt 仓库证据合同未与 v3 `exactly-one-deb` 客户边界同步 | v3 inventory 只允许 manifest 绑定的唯一 DEB 和 sidecar，显式拒绝混入历史 apt 仓库；v2 只保留为显式历史只读路径 | v3/v2 证据 schema 回归、release-check 静态合同 | 当前源码合同待 Linux 实际制包重跑；历史 v2 证据不升级为当前发布证据 |
| Kylin 制包在 276 个 DOCX 测试全部通过后报 `trusted /usr/bin/readelf is missing or unsafe` | Debian/Kylin 的 `/usr/bin/readelf` 通常是 root 管理的架构别名软链接，旧解析器只接受普通文件，误拒绝正常系统工具；Python runtime 的 libpython 消费者检查曾另外从 `PATH` 裸调 `readelf` | 同时校验别名目录、软链接归属、解析后实体路径、root owner、权限和可执行位，返回 canonical 实体路径；ABI audit、private-library staging 和 libpython 消费者检查全部复用同一 trusted resolver，执行时固定安全 `PATH` 与 C locale，逃逸到非受信目录仍 fail closed | root-managed symlink 正/负回归、恶意 `PATH` 不得被调用、实际 Linux `/usr/bin/readelf` 解析 | 根因来自 2026-08-06 Kylin 日志；源码回归不代表当前输入包已在制包机重建 |
| 修复 `readelf` 后实物 ELF 闭包会继续遇到 debugpy/Tcl-Tk/异架构 resvg/Electron companion 误报 | 开发 extras 被带入正式 venv，uv standalone 携带无用 GUI 组件，DOCX 源码测试与最终 x86_64 payload 未分层，Electron 自带配套 ELF 未精确建模 | 正式 production profile 不安装 dev；stager 裁剪 Tcl/Tk 和非 x64-glibc resvg；Electron companion 必须 SONAME+固定相对路径同时命中，安全 `$ORIGIN` RPATH 按 policy 校验 | 旧真实 DEB 裁剪回放、Python/Node smoke、39 个 ELF 完整 closure | 临时严格规则已在 Ubuntu 20.04 sysroot 实物回放通过；正式代码与当前 Kylin 制品仍须重跑 |
| 使用 Debian 13 制包或演练会带来 glibc 2.41 和新系统包冲突 | 演练系统比 Kylin V10/glibc 2.31 更新，可能产生假绿或误报依赖冲突 | 固定 Ubuntu 20.04 amd64 兼容基线，并校验镜像 baseline label | manifest 记录 OS、arch、glibc；生产器核对镜像角色和版本 | 仅证明兼容基线，不证明 Kylin 真机 |
| Linux 签名预检误报“源码包内容与当前 Git HEAD 不一致” | macOS Apple gzip 与 Linux GNU gzip 会把同一 tar 压成不同字节；比较 `.tar.gz` 本身把编码器差异误判为源码漂移 | 仍用当前 Git HEAD 重建确定性 tar，但与源码包解压后的 tar 流逐字节比较 | 不同 gzip 编码器的同一 git archive 必须通过；解压后 tar 增加任意字节必须拒绝 | 在 `15c058b4` 签名前真实暴露；两端解压 tar SHA256 相同后修复 |
| Linux `execute_code` 报 `OSError: AF_UNIX path too long` | `TAIJI_AGENT_TMP_DIR` 或工作树路径过深；`sockaddr_un.sun_path` 按编码字节计，Linux 约 108 B，旧逻辑只处理 Darwin 长路径 | 脚本和数据继续留在 Taiji 临时目录；仅 RPC socket 放入随机 owner-only `/tmp/taiji_rpc_*`，目录 `0700`、socket `0600`；POSIX 建立安全 UDS 失败时 fail closed，不降级到无鉴权 TCP | Ubuntu 多字节中文长路径必须成功且退出后无残留；短目录创建失败时必须返回错误且不得打开 AF_INET | Docker Linux release-check 中真实暴露；13 项聚焦测试已覆盖成功和失败路径，最终发布仍以冻结提交重跑为准 |
| Linux 统一 release-check 的安装仿真大量报“无法读取硬链接计数” | 产品安装脚本正确使用 GNU `stat -c`；测试 fake stat 却调用了 BSD/macOS `/usr/bin/stat -f` | 只把测试桩改为 Python `os.stat().st_nlink` 与 `stat.S_IMODE`，不修改目标机安装脚本 | 同一 31 项安装仿真在 macOS 与 Ubuntu 20.04 必须全部通过 | 两端均已 31/31；该问题属于测试基础设施兼容，不是目标包安装失败 |
| Linux 统一 release-check 的授权测试报 `node: not found` | 干净 gate clone 的 `PATH` 没有包含制包输入中准备好的固定 Node 工具链 | 在运行源码级门禁前显式检查并加入固定 Node/npm 工具链；不得因为 DEB 已生成而跳过授权和 WebUI 测试 | `command -v node npm` 后再运行完整 root/WebUI gate | 属于门禁环境准备问题，不代表安装态 Node 缺失 |
| 核心代码执行测试批量返回 `capability_blocked` | 产品默认 restricted 是正确策略，但沙箱机制测试没有显式进入受控 full profile，导致根本未执行被测逻辑 | 机制测试 fixture 显式设置 full；默认拒绝、显式授权和失败关闭继续由独立安全套件验证 | 代码执行机制 131 项通过、3 项平台预期跳过；安全/授权相关 80/80 | 只调整测试前置条件，不放宽产品默认安全模式 |
| `--network none` 被未启用的 tunnel 设备误报；sudo 提示 hostname 解析失败 | 只按网络节点存在判断；容器 hostname 未进入本地 hosts | 只拒绝启用链路、全局地址和非 loopback route；sudo 前确保本地 hostname 解析 | Docker inspect、网络负向测试和结构化会话记录 | 历史候选 `1d56849a` 已完成断网三阶段；后续源码提交仍须重跑 |
| 无图形容器执行安装后可能被误写成目标机成功 | CLI 和包状态不能证明 Electron/UKUI | 无图形会话默认失败；仅显式 headless rehearsal 可继续，并强制 `desktop_app_verified=false`、`target_verified=false` | release gate 分开验证离线证据与真机证据 | 目标机仍必须执行 `04` |
| 普通用户交付目录通过校验后、sudo 安装前可被替换 | 用户可写源文件存在 TOCTOU 窗口 | 复制到 root-owned `/var/tmp` staging 后重校验，再走原生 install/upgrade | 拒绝 symlink、hardlink、路径穿越、未列入仓库文件和中途替换 | 安装脚本仿真与负向测试覆盖 |
| `02` 已通过 manifest/摘要预检，却在安装前返回 `DEB_SHA256_SIDECAR_MISMATCH`（upgrade 的 N-1 输入还可能为 `PREVIOUS_DEB_SHA256_SIDECAR_MISMATCH`） | management staging 把原始 `taiji-agent_<version>_amd64.deb` 改名为 `candidate.deb`/`previous.deb`，但同名 sidecar 首行仍绑定原始 basename；摘要内容未坏，文件身份合同被暂存改名破坏 | 候选和 N-1 DEB 均以原 basename 连同同名 `.sha256` 成对复制到 root-owned management staging，静默部署器继续在 `dpkg` 前复核 basename 和摘要 | 安装脚本仿真必须覆盖候选与 N-1 两条参数路径、明确拒绝固定别名；Bash 语法通过后，用重建制品跑完整断网生命周期 | 源码回归只证明 staging 合同；Ubuntu 容器若被 canonical policy 拒绝，不得算安装失败回归通过，也不得替代真实 Kylin/UOS/openKylin 目标机安装和桌面验收 |
| Ubuntu 20.04 断网演练在 candidate `preinst` 返回 `TAIJI-LINUX-E002-OS`/`TAIJI-LINUX-E006-DESKTOP` | 演练 runner 强制真实 Ubuntu 20.04 基线，而生产 policy 正确只接受 `kylin/uos/openkylin` 图形系统；fake-Docker 单测没有执行真实 DEB，未发现两者矛盾 | 不放宽生产 policy；专用镜像绑定 `kylin-os-release-v1` label，runner 先验证真实 Ubuntu 和 `network none`，再只在一次性容器内激活 root-owned Kylin os-release/桌面目录 fixture | label 错误必须在 Docker start 前拒绝；静态顺序门禁必须证明 baseline/network 校验早于 fixture 和首次 dpkg；正式演练证据固定 `environment=container-kylin-policy-fixture-v1`、`os_id=ubuntu`、`target_verified=false` | fixture 演练只覆盖未修改 DEB 的 dpkg 生命周期和维护脚本，不是国产系统、图形桌面或目标机验收；真实目标仍执行 `04` |
| purge 白名单路径被替换为普通文件，或顶层 `/opt/taiji-agent` 绕过安全门禁直接 `rmdir` | 旧脚本把“路径在白名单”误当成“对象类型和身份已可信” | 目录类型不匹配一律保留告警；顶层空目录复用 owner/symlink/mountpoint/type 安全门禁 | 动态执行 ordinary remove、安全 purge、symlink、非 root owner、mountpoint、类型不匹配和顶层目录场景 | 当前只是脚本级动态回归，最终仍需当前制品的断网演练与真实目标机验收 |
| 并发首次初始化偶发 `Template registry lock not found`，制包 `npm test` 中断 | 旧 regular-file lock 在 owner 内容完整前已经公开；等待者可能看到空锁、消失锁或错误代锁 | 使用 candidate directory 写完整 owner 后原子发布；owner 绑定 generation token；release/stale 通过 tombstone 隔离 | 多进程初始化、旧 owner 不能释放新代、延迟 stale reaper 不能隔离新代、压力测试 | 源码与 Ubuntu 聚焦测试已通过；最终仍以当前 manifest 和证据为准 |
| `uv --locked` 提示 lockfile 需要更新，或制包机 shell 注入额外 Python 索引 | 制包脚本默认清华索引与旧 PyPI registry lock 身份不一致；`UV_INDEX`/`UV_EXTRA_INDEX_URL` 等现场变量还会以更高优先级覆盖或扩充受控索引 | 提交与默认清华镜像一致、版本和 SHA256 不变的 lock；使用专用 `TAIJI_UV_INDEX_URL`，构建前清除所有可注入额外索引/flat-link/策略的 uv 环境变量，再导出唯一受控 `UV_INDEX_URL` | 固定 uv/Python 的 `--locked --dry-run`、带恶意环境变量的负向静态门禁、完整制包日志确认未触发 non-locked fallback、Python relocation/import 和 payload audit | 显式覆盖索引仍必须与 lock 同步维护；任何 fallback 告警都不能描述为严格可复现构建 |

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

### 10.1 干净单 DEB 验收机与 challenge

```bash
cat /etc/os-release
uname -m
dpkg --print-architecture 2>/dev/null || true
ldd --version 2>/dev/null | head -1 || true
command -v apt-get apt-cache dpkg systemctl sudo
printf 'DISPLAY=%s\nWAYLAND_DISPLAY=%s\n' "${DISPLAY:-}" "${WAYLAND_DISPLAY:-}"
```

同时确认磁盘、内存、桌面类型、管理员能力、kysec/杀软/白名单策略和模型访问条件。该环境不得先执行 `02`；生命周期验收应使用另一个 VM/快照/终端。候选目录只能有 manifest 指定 basename 的单个 DEB。

```bash
export TAIJI_CERTIFICATION_CHALLENGE="$(openssl rand -hex 32)"
export TAIJI_SINGLE_DEB_CUSTOMER_DIR="/只有manifest同名候选DEB的绝对目录"
export TAIJI_INSTALL_EVIDENCE_DIR="$PWD/install-observation-$TAIJI_CERTIFICATION_CHALLENGE"
mkdir -m 0700 "$TAIJI_INSTALL_EVIDENCE_DIR"
```

challenge 必须由发布负责人当轮生成并保存；安装观察、人工见证、App 驱动、签名和最终 release-check 必须复用同一值。

### 10.2 安装前启动观察器，再由图形安装器安装

在终端 A 执行并保持进程运行：

```bash
/usr/bin/python3 -B ./验收工具/observe-single-deb-install.py observe \
  --customer-dir "$TAIJI_SINGLE_DEB_CUSTOMER_DIR" \
  --manifest "$PWD/生成的安装包/taiji-package-manifest.json" \
  --challenge "$TAIJI_CERTIFICATION_CHALLENGE" \
  --output-dir "$TAIJI_INSTALL_EVIDENCE_DIR"
```

观察器会自动拒绝：启动时已存在 dpkg 状态、候选 basename/hash 不符、目录或文件被替换、当前用户已有太极状态、机器/boot 改变、任一采样发现非 loopback 网络，或未观察到 absent→installed。终端 B 保持断网，从文件管理器双击唯一 DEB，并使用系统图形包安装器完成安装；等待终端 A 正常结束。

保存完整系统安装器成功画面的有效 PNG（至少 800x600），再由现场操作员生成范围明确的人工见证：

```bash
/usr/bin/python3 -B ./验收工具/observe-single-deb-install.py attest \
  --observation "$TAIJI_INSTALL_EVIDENCE_DIR/single-deb-install-observation.json" \
  --graphical-evidence "/绝对路径/系统图形安装器成功界面.png" \
  --challenge "$TAIJI_CERTIFICATION_CHALLENGE" \
  --operator-id "<受控操作员编号>" \
  --confirmation "I-observed-desktop-double-click-and-system-installer" \
  --output-dir "$TAIJI_INSTALL_EVIDENCE_DIR"
```

`installation_method_machine_observed=false` 是固定事实：人工见证不能包装成机器检测。最终真实性由发布负责人结合 PNG 和现场记录复核，并通过顶层 evidence detached signature 承担。

### 10.3 由真实 Electron 验收驱动完成可见首次配置

安装完成后不要先手工启动 App，否则“驱动从首次配置开始”的证据合同失效。可按现场策略恢复批准的本地/内网模型访问，并通过受控环境为验收进程提供已批准的真实 Provider 凭据；凭据不得写入脚本、命令历史、安装包或证据目录。驱动会自行启动安装态 Electron，必须先观察到可见首次配置工作台，再通过真实可点击控件完成授权、模型、工作区和安全策略检查。不得使用 Escape、关闭覆盖层或直接调用完成 API 跳过。

随后执行：

```bash
export TAIJI_SINGLE_DEB_INSTALL_OBSERVATION="$TAIJI_INSTALL_EVIDENCE_DIR/single-deb-install-observation.json"
export TAIJI_SINGLE_DEB_METHOD_ATTESTATION="$TAIJI_INSTALL_EVIDENCE_DIR/single-deb-install-method-attestation.json"
export TAIJI_SINGLE_DEB_GRAPHICAL_INSTALLER_EVIDENCE="$TAIJI_INSTALL_EVIDENCE_DIR/single-deb-graphical-installer.png"
bash ./04_目标终端_桌面App验收并导出证据.sh
```

必须证明：

- 使用 `/opt/taiji-agent` 安装态 Electron，而不是浏览器或源码 App。
- 从开始菜单可见并启动“太极 Agent”。
- 同一应用重复双击只聚焦已有窗口。
- 真实模型完成 challenge 绑定的附件对话。
- 支持包从用户可见入口导出。
- 关闭窗口后 Electron、Agent、WebUI 进程和端口退出。
- 观察器证明首次启动前用户状态为空；驱动证明同一验收会话从可见工作台开始，并在服务端确认 `completed=true` 且 preflight 全部就绪后才继续；二者组合支持 `first_configuration_cycle_completed=true`。
- 软件采样和文件摘要用于受控现场追溯，不是 TPM/远程证明；恶意 root 管理员不在本证据链的可对抗威胁模型内。
- `target-verification/` 中的机器观察、人工见证、图形安装器 PNG、App JSON/截图、支持包和 driver result 摘要互相绑定。

### 10.4 人工业务复核

- 首屏、设置、模型配置和对话区可正常使用。
- 上传 PDF、PPTX、DOCX、XLSX 或 TXT/MD/CSV 后，回答基于真实文件内容。
- 图片能力按当前模型配置给出真实结果或明确能力不足提示。
- 若交付包含 DOCX 结果，使用目标环境的 WPS/Word 完成人工视觉检查。
- 卸载、同版本重装、旧版升级和异常中断恢复按本次交付范围分别验收。

`02_目标终端_安装并验证.sh` 的内部生命周期检查在独立环境执行；不要在上述 `04` 单 DEB 安装观察之前运行。最终客户目录由 publisher 在 certification-set 与 v3 双签名门禁后生成，basename 固定为 `taiji-agent_${VERSION}_amd64.deb`，输出 DEB 必须与本节已认证候选逐字节、SHA256 完全一致，receipt 只归档六个白名单文件。

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
- `package/`：dpkg 状态、唯一 DEB/manifest/sidecar 摘要与安装日志。
- `runtime/`：native verify、CLI、Electron `file/ldd`、desktop entry、权限、服务、进程和端口。
- `logs/`：自动失败诊断和经过脱敏的日志尾部。
- `app/`：已有的产品支持包；不能静默收集完整会话或附件。
- `bundle-manifest.json` 与 `collection-errors.txt`。

诊断收集必须 best-effort：某个命令失败时继续收集其它证据，并把失败记录到 `collection-errors.txt`。压缩包和 sidecar 权限应为 `0600`。

禁止收集或外发：API Key、token、密码、私钥、授权 JWT 正文、模型完整请求、完整用户数据库、附件正文、客户 IP/域名和未脱敏绝对用户路径。需要额外材料时必须单独取得用户确认。

## 12. 候选证据与发布身份

候选 `15c058b4` 也完成了 Ubuntu 20.04 amd64 全量制包、187 项离线仓库和 `--network none` 安装→卸载→重装。签名前预检阻止了签名流程，但暴露的不是源码漂移，而是 Apple gzip 与 GNU gzip 的压缩结果不同；两端解压后的原始 git-archive tar SHA256 完全一致。门禁已改为比较解压后的 tar 流。因为门禁代码本身改变了源码身份，`15c058b4` 的 DEB 与离线证据只能保留为历史候选，不能签给后续提交。

候选 `7acea3ef` 随后完成了 Ubuntu 20.04 amd64 制包、断网安装→卸载→重装和离线证据签名，但 Linux 全量门禁又真实暴露了 AF_UNIX 多字节长路径问题和测试桩跨平台问题。修复这些问题会产生新的源码身份，因此 `7acea3ef` 的已签离线证据也只能作为历史演练记录，不能绑定最终冻结提交，更不能替代 Kylin/UOS 桌面 App 证据。

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

发布必须设置一次明确的“源码冻结点”：

1. 先把代码修复、稳定经验、门禁和文档提交完整。
2. 冻结源码后再执行制包、断网演练、签名和目标机验收。
3. 签名后不得把当前 commit、manifest 摘要或“最终通过”结果再写回受 Git 跟踪文件；否则源码身份会再次变化，使刚生成的 DEB 和证据立即变成历史候选。
4. 当前产物的精确 commit、manifest、摘要和签名保存在交付目录证据，以及仓库外 append-only 项目记忆中。
5. 区分产品缺陷、打包缺陷、演练环境问题和测试桩兼容问题；不得通过削弱生产安装/校验脚本让宿主机专用测试变绿。

现场问题的默认反馈顺序是：自动失败诊断 → `03` 诊断文件/包 → 必要时补截图。不得重新回到“截图一张、猜一个命令、再打一次包”的循环。
