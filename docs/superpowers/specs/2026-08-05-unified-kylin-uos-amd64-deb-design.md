# 太极 Agent 统一国产 Linux amd64 DEB 设计

日期：2026-08-05

适用成果：`codex/linux-sales-grade-installer` 国产 Linux 离线交付链

取代规格：[2026-08-04 麒麟/统信单文件基线采集器与固定包身份设计](2026-08-04-kylin-baseline-collector-and-fixed-package-identity-design.md)

## 1. 产品目标

每个太极 Agent 正式版本只构建一个不可变安装文件：

```text
taiji-agent_<version>_amd64.deb
```

该 DEB 在支持矩阵中的所有环境保持逐字节相同、SHA256 相同。几百或上千台终端使用同一个文件，不要求每台机器采集基线，不根据客户机器生成不同依赖、`profile_id`、文件名或安装包。

发布流程调整为：

```text
正式 main
  -> 最低兼容 ABI 环境构建一次
  -> 单一 DEB 与 SHA256
  -> 同一制品在代表性矩阵认证
  -> 签名发布
  -> 单机双击或集中批量部署同一文件
```

本设计保留构建、离线演练和目标环境验证的证据分层。代表性认证不能证明所有国产 Linux，支持声明只能覆盖本规格定义的产品范围和已公开的兼容策略。

## 2. 第一版支持范围

第一版同时满足以下条件：

- CPU/系统架构为 Linux `x86_64`/Debian `amd64`；
- 带图形桌面；
- 使用 `dpkg` 与 `apt`；
- 属于麒麟 Kylin、统信 UOS 或 openKylin 系统家族；
- 满足产品维护的 glibc、内核、桌面基础库、systemd、loopback、磁盘和管理员能力下限。

第一版明确不覆盖：

- ARM/aarch64；
- 只有 RPM 的系统；
- 没有图形桌面的服务器；
- 没有可用 dpkg/apt 或管理员能力的终端；
- 禁止 `/opt`、systemd、loopback、Electron sandbox 或产品运行时的强隔离环境；
- Windows。

这些边界不得用“国产 X86 一个包通吃”表述。RPM、`.run`、ARM 和 Windows 分别作为后续独立制品线。

## 3. 方案选择

### 3.1 未采用：瘦 DEB 依赖系统仓库

瘦 DEB 保留当前大量 `Depends`，文件较小，但离线目标缺少任一依赖就无法只靠一个文件安装。该方案不满足“客户目录只有一个 DEB，断网双击安装”的核心合同。

### 3.2 采用：单兼容运行时胖 DEB

一个 DEB 内置 Electron、Node、Python、Python 包、应用代码和可安全私有化的用户态动态库，只保留不可安全捆绑的系统核心依赖。运行时固定在 `/opt/taiji-agent` 私有目录，不污染全局库路径。

这是第一版主线：一个制品、一个摘要、一套安全更新和一套矩阵证据。

### 3.3 后备：单 DEB 内多运行时 profile

只有真实矩阵证明某一受支持系统家族无法共享同一运行时，才允许在同一个 DEB 内增加经过隔离的兼容 profile，并由安装时能力检测选择。不得为预想兼容性提前引入多套运行时。

## 4. 兼容策略合同

新增源码受控的 `taiji-linux-compatibility-policy/v1`。该策略是产品拥有的发布合同，不来自任一客户终端，也不能由构建环境变量覆盖。

策略至少包含：

- `policy_id` 与 schema；
- 支持架构；
- 支持的 OS 家族标识规则；
- 固定最低 glibc/内核能力；
- dpkg/apt/systemd/图形桌面基础能力；
- 最小系统依赖合同；
- 私有运行库允许捆绑清单；
- 禁止捆绑的系统核心库清单；
- Electron sandbox、loopback 与安全策略边界；
- 固定包身份；
- 策略本身的 canonical SHA256。

固定 Debian 包身份为：

```text
Taiji Agent Product Team <noreply@localhost>
```

该值只满足 Debian 元数据要求，不表示可联系的售后邮箱。删除人工维护者审批文件、验证器和 `TAIJI_PACKAGE_MAINTAINER` 输入；构建脚本、DEB control、manifest、报告和内部发布回执必须逐字一致。

## 5. 运行时与依赖闭包

### 5.1 DEB 内置内容

- Electron Linux x64；
- Node Linux x64；
- Python 运行时及锁定依赖；
- Python native wheels；
- WebUI、Agent API 和桌面壳；
- 应用资源、模板、许可证、公钥；
- 安装态 native verify、诊断和卸载组件；
- 通过闭包审计允许私有化的用户态动态库。

### 5.2 禁止私有化的核心

不得捆绑或替换：

- glibc 与动态加载器；
- Linux 内核及内核模块；
- 显卡驱动和 Mesa/厂商驱动核心；
- PAM、systemd、DBus 等系统核心；
- 需要写入 `/usr/lib`、`/lib` 或改变全局 loader 配置的库。

GTK/NSS/图形相关库是否私有化必须由 ELF 闭包、加载隔离和矩阵结果决定，不能只为减少 `Depends` 机械复制。

### 5.3 ABI 门禁

制包使用固定的最低兼容 Linux amd64 构建环境。每次构建对 Electron、Node、Python、native wheels 和全部 ELF 执行：

- 最大 `GLIBC_*`、`GLIBCXX_*`、`CXXABI_*` 符号需求审计；
- `ldd`/ELF 依赖闭包检查；
- RPATH/RUNPATH 和私有库目录检查；
- 构建宿主绝对路径、系统库和缓存泄漏检查；
- 未解析依赖和禁止私有化库检查；
- Linux x86_64 ELF 架构检查。

策略在构建前声明产品承诺的 glibc 下限。构建后的实际 payload 最大 ABI 需求不得高于该下限；否则构建失败，只有显式修改策略并重新构建才能提高下限。矩阵下界用于验证该承诺，不能从某台机器的当前版本自动复制或在构建后回写策略。

## 6. 安装时本地预检

通用 `preinst` 使用能力判断，不再精确比较某台机器的 `ID/VERSION_ID/VARIANT_ID/BUILD_ID/profile_id`。

硬门禁包括：

- `amd64/x86_64`；
- Kylin/UOS/openKylin 家族识别；
- dpkg/apt 基础可用；
- glibc/内核能力达到策略下限；
- 必需系统核心组件存在；
- `/opt`、systemd、loopback、磁盘和管理员能力可用；
- 已知 kysec/沙箱策略没有明确阻断。

`preinst` 的本地结果只分为：

- `COMPATIBLE`：属于支持系统家族且硬能力满足，允许安装；
- `BLOCKED`：硬能力不满足，安装前失败关闭。

`CERTIFIED` 是构建后外部认证集对环境类别的发布结论，不能由尚未包含认证集的 DEB 自行宣称。集中部署或支持人员可以用内部签名认证集把一台兼容终端归入已认证类别，但这不改变 DEB 字节。

支持家族内的补丁版本不因字符串不完全相等而拒绝。未知系统家族、ARM、RPM-only、旧 ABI 和强隔离环境直接 `BLOCKED`。

预检完全本地运行，不联网、不上传、不要求回传文件、不改变 DEB 内容。失败时输出稳定中文错误码、原因和单一步骤，并生成不含凭据的安装诊断记录；不得留下半安装服务或修改用户业务数据。

## 7. 单文件离线合同

客户交付目录必须且只能包含一个 DEB。双击和批量静默安装均满足：

- 不访问公网或内网软件源；
- 不下载依赖；
- 不需要第二个 DEB、脚本、压缩包或离线仓库；
- 安装完成后 `dpkg` 状态为 `install ok installed`；
- native verify 通过；
- 所有运行来源固定到 `/opt/taiji-agent`；
- 系统核心依赖缺失时安装前明确拒绝，不偷偷恢复网络。

内部构建、认证和签名目录可以包含 manifest、证据、截图、回执和历史版本，但这些不是客户安装输入。

## 8. 兼容认证矩阵

首次销售放行至少使用以下代表环境，所有环境使用同一个 DEB 和 SHA256：

| 代表环境 | 必覆盖风险 | 必测重点 |
|---|---|---|
| 最低支持 Kylin/UKUI 标准桌面 | 最低 ABI、X11、基础依赖 | 断网双击、静默安装、首次启动 |
| 当前维护 Kylin 标准桌面 | 新补丁前向兼容 | 完整业务链、关窗退出 |
| Kylin 企业加固环境 | kysec、白名单、Electron sandbox、实际 CPU | 升级、失败恢复、数据保留 |
| 最低支持 UOS/DDE 标准桌面 | 发行版与桌面差异 | 断网双击、静默安装、首次启动 |
| 当前维护或加固 UOS | 企业权限、集中部署 | N-1 升级、重装、回滚 |
| openKylin 当前 LTS/维护版 | openKylin、X11/Wayland | 启动、交互、关窗退出 |
| 负向边界样本 | ARM、RPM-only、旧 glibc、缺核心依赖、无权限 | 安装前失败、零业务数据变更、明确错误码 |

矩阵按风险维度做代表性和成对覆盖，不做硬件、补丁和桌面组合的全排列。首次放行跑完整矩阵；Electron、Node、Python、native wheel、私有动态库、兼容策略或安装生命周期变化后重新跑完整矩阵。纯应用层改动仍至少跑三个系统家族的核心路径，具体风险升级规则写入发布手册。

每个正向环境必须覆盖：

- 图形安装器双击 fresh install；
- 无网络批量静默安装；
- 首次配置、授权和真实模型；
- 对话、附件、专家团队和 DOCX；
- 诊断导出与关窗进程退出；
- 同版本重装；
- N-1 到 N 升级；
- 注入安装失败后的恢复；
- 降级/回滚和用户数据保留；
- 卸载边界。

## 9. 证据与发布顺序

移除当前发布链中的单目标字段：

- `target_baseline_profile_id`；
- `target_baseline_sha256`；
- DEB 内 `target-baseline.json`；
- 客户文件名中的 profile ID；
- 目标机精确版本派生的 `Depends`。

引入三类合同：

1. `taiji-linux-compatibility-policy/v1`：构建前存在，摘要嵌入 DEB 与构建 manifest。
2. `taiji-linux-certification-set/v1`：构建后生成，绑定同一 DEB SHA256、多个代表环境、各自实际系统事实和验收证据。
3. `taiji-release-evidence/v3`：发布验证器和回执使用，绑定源码 commit、版本、架构、DEB SHA256、policy SHA256、certification-set SHA256 和签名。

顺序必须是：

```text
构建 DEB
  -> 固定 DEB SHA256
  -> 用该字节完成离线演练和代表矩阵验收
  -> 生成并签名 certification set
  -> publication receipt 绑定 DEB + policy + certification set
  -> 原子生成只含一个 DEB 的客户目录
```

认证集不得写回 DEB，否则会改变被认证字节并形成循环。历史 schema v2 证据只读保留，不得与 v3 混用证明当前发布。

## 10. 双安装通道与规模化部署

### 10.1 单机双击

用户从文件管理器双击唯一 DEB，由系统图形包安装器完成。人工见证记录双击事实；程序证据负责记录安装前状态、dpkg 状态、安装后来源和业务验收，不能声称程序独自证明鼠标双击。

### 10.2 集中静默安装

企业终端管理系统在安装前核对期望版本、DEB SHA256 和发布签名，以 noninteractive、no-download 方式安装同一 DEB，并输出机器可读 receipt。管理端编排工具不是终端侧第二个安装文件。

静默安装必须幂等：未安装、同版本、升级和明确允许的降级分别有稳定结果；并发安装、锁冲突和中断不得破坏 dpkg 状态。

### 10.3 灰度与停止条件

- Canary：每个兼容类别至少一台，总数至少五台；
- 第一环：5%，观察一个业务日；
- 第二环：25%，继续观察一个业务日；
- 最终环：100%；
- 后续环要求安装、native verify 和启动成功率至少 99.5%；
- 数据损坏、安全问题、无法回滚、未分类 P0/P1 或任一兼容类别失败时立即冻结扩容。

## 11. 升级、回滚和数据保护

保留上一正式版本 DEB、SHA256、兼容策略和迁移合同。升级前必须停止太极进程并对配置、授权、会话、附件、workspace 和状态做受控快照；禁止 purge 用户数据。

升级事务由部署/升级编排层控制，不允许 `postinst` 递归调用 apt。失败时重新安装已归档的 N-1 DEB，按明确的前后向数据兼容合同恢复，并重新运行 native verify。若数据迁移不可逆或旧版本不能读取新状态，发布必须阻断，不得假装支持自动回滚。

首次销售放行必须验证 fresh install、同版本重装、N-1 升级、注入 `postinst` 失败、回滚和再次升级。

## 12. 诊断和隐私

安装包内置一次性诊断导出，输出脱敏 support bundle 和机器可读 receipt。错误至少分类为：

- 制品摘要/签名；
- 不支持的架构或系统家族；
- ABI/系统核心依赖；
- dpkg/apt 锁或权限；
- kysec/沙箱策略；
- preinst/postinst；
- 首次启动、服务和 loopback；
- 升级或回滚。

支持包可以包含版本、DEB SHA256、policy SHA256、错误阶段、稳定错误码、OS 兼容字段和依赖状态；不得包含 API Key、Token、密码、附件正文、完整数据库、浏览器会话、用户名、主机名、IP、MAC 或序列号。

## 13. 自动化验收

实施至少新增或调整以下测试族：

- 兼容策略 schema、canonical hash 和禁止环境覆盖；
- 固定 Maintainer 全链一致性；
- 通用 preinst 的 `COMPATIBLE/BLOCKED` 判定，以及外部认证集的 `CERTIFIED` 分类；
- 不再精确绑定 OS 补丁、客户 profile 或客户依赖版本；
- ELF/ABI、RPATH、架构和动态库闭包；
- 单文件断网安装合同；
- release evidence schema v3 和 v2 只读隔离；
- certification set 多环境、同一 DEB SHA256 绑定；
- publisher 原子性、并发、回滚和客户目录单文件；
- fresh install、重装、升级、失败恢复、回滚和卸载；
- 安装与诊断隐私扫描；
- 既有 Electron 来源隔离、首次配置和业务功能回归。

自动化只能证明代码合同；“目标机已验证”必须来自上述真实代表矩阵的安装态 Electron 和业务验收。

## 14. 实施顺序

1. 固化 compatibility policy、固定 Maintainer 和新 schema 的失败测试。
2. 解除 build、preinst、manifest、marker、证据、签名和 publisher 对单 profile 的耦合。
3. 实现 ELF/ABI 闭包和允许私有化运行库策略，收敛系统 `Depends`。
4. 升级离线演练、目标验收和 publication receipt 到 v3。
5. 增加批量静默 receipt、升级/回滚和脱敏 support bundle。
6. 更新销售边界、操作手册、版本信息和发布门禁。
7. 完成全量自动化、本地审查和开发态必要复验。
8. 按已授权的标准收尾完成 push、PR、CI、合并、正式 `main` 同步和非破坏性复验。
9. 从已复验正式 `main` 在兼容 Linux amd64 制包机生成唯一候选 DEB。
10. 使用同一候选完成断网演练与代表矩阵认证，签署认证集并生成只含一个 DEB 的客户目录。

任何制品修复导致 DEB SHA256 变化时，旧认证集立即失效，必须对新字节重新完成适用矩阵。

## 15. 完成定义

只有同时满足以下条件，才能宣称“统一国产 Linux 安装包可交付”：

- 正式 `main` 包含已批准实现且 CI 通过；
- 兼容 Linux amd64 制包机只构建一次并生成唯一 DEB；
- 客户目录只有 `taiji-agent_<version>_amd64.deb`；
- 断网双击和静默路径不需要第二个文件或网络；
- Kylin、UOS、openKylin 代表矩阵使用同一 SHA256 并全部通过；
- upgrade/rollback/data-preservation 门禁通过；
- policy、认证集、发布回执和签名绑定同一制品；
- 真实业务链、诊断、关窗退出和隐私检查通过；
- 支持范围、已认证环境和未支持边界明确交付。

在这些证据闭合前，只能使用“分支已实现”“源码包已准备”“制包机已构建”或“离线安装已演练”等与实时证据一致的状态，不得提前宣称“目标机已验证”或“已发布”。
