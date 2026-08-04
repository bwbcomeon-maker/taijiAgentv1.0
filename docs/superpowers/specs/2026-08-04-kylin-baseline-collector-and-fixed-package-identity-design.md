# 麒麟/统信单文件基线采集器与固定包身份设计

日期：2026-08-04
适用成果：`codex/linux-sales-grade-installer` 国产 Linux 单 DEB 离线交付链

## 1. 背景与目标

太极 Agent 的国产 Linux 候选包必须绑定真实 Kylin/UOS/openKylin x86_64 目标基线后再构建。现有采集入口依赖三个相邻文件，不满足现场人员“复制一个文件到桌面、双击运行、拿回一个结果文件”的操作要求。同时，现有构建链要求人工提供并审批维护者身份；本轮决定取消该人工输入，但不能删除 Debian 二进制包必需的 `Maintainer` 控制字段。

本设计实现两个收敛目标：

1. 提供一个自包含、可双击执行、离线且无需管理员权限的目标基线采集器；成功时只生成一个需回传的 JSON 文件。
2. 用不可由环境覆盖的固定非个人技术身份替代人工维护者审批链：`Taiji Agent Product Team <noreply@localhost>`。

本设计不生成 DEB，不在目标机安装太极 Agent，也不把基线采集成功表述为目标机验收通过。

## 2. 方案选择

采用单文件自包含 Shell 采集器，不采用 `.desktop + helper` 双文件方案，也不采用需安装和提权的采集器 DEB。

交付文件命名为：

```text
太极Agent_目标机基线采集器.sh
```

该文件保持可执行位。用户将其复制到 Kylin/UOS 图形桌面后双击，并在文件管理器提示中选择“运行”或“在终端运行”。不同桌面环境可能要求用户先在文件属性中允许执行；采集器不能绕过系统的可信文件策略。

## 3. 采集器架构

### 3.1 单一来源与生成方式

仓库新增一个生成器，由以下 canonical 输入确定性生成自包含采集器：

- `packaging/linux/target_baseline.py`
- `packaging/linux/deb/runtime-depends.txt`
- 采集器启动壳模板

生成器把 Python 采集逻辑和依赖契约以完整性受保护的内嵌负载写入 Shell 文件。生成结果必须可重复：相同输入生成逐字节相同的采集器。测试必须阻止提交与 canonical 输入不一致的陈旧采集器。

### 3.2 运行环境

采集器只使用目标 Debian-like 系统的基础能力：

- `/bin/bash`
- `/usr/bin/python3`
- `/usr/bin/dpkg`、`/usr/bin/dpkg-query`
- `/usr/bin/apt-get`、`/usr/bin/apt-cache`
- `/usr/bin/uname`、`/usr/bin/ldd`、`/usr/bin/systemctl`

不访问网络，不执行 `sudo`，不安装依赖，不修改系统配置。缺少任何非协商基础能力时失败关闭，并告诉用户该目标系统不能进入当前 DEB 基线。

### 3.3 数据流

1. Shell 壳固定 `PATH`、locale 和 `umask 077`，清除 Python、动态链接和 dpkg 定位类污染变量。
2. 在用户临时目录中创建权限受限的工作目录。
3. 校验内嵌负载摘要，再释放 canonical Python 采集器和依赖契约。
4. 调用系统 `/usr/bin/python3` 执行既有 `capture` 路径。
5. 对结果再次运行既有 `validate` 路径，确保结构、依赖契约、时间和 profile ID 一致。
6. 仅在全部检查通过后，将结果原子移动到目标桌面；临时目录由 trap 清理。

### 3.4 输出位置与命名

输出目录按以下顺序选择：

1. `xdg-user-dir DESKTOP` 返回的现有、可写、属于当前用户的目录；
2. `$HOME/Desktop`；
3. `$HOME/桌面`；
4. 采集器所在目录。

成功结果命名为：

```text
太极Agent目标机基线_<profile-id>.json
```

若同名文件已存在，只有内容摘要完全一致时才复用并报告成功；内容不同则停止并提示用户先移走旧文件，禁止静默覆盖。结果权限为 `0600`。采集过程产生的 `.sha256` sidecar 只在临时目录用于校验，成功后不要求用户回传，桌面只保留一个 JSON 结果文件。

## 4. 用户反馈与失败处理

成功或失败均写入标准输出/错误。图形双击且没有可见终端时，按以下顺序提供一次结果提示：

1. `zenity`；
2. `kdialog`；
3. `xmessage`；
4. 若均不可用，在输出目录生成一个明确命名的错误文本并退出非零；失败时不生成或保留半成品 JSON。

成功提示包含结果文件绝对路径、profile ID 和 JSON SHA256，不包含主机或用户身份。失败提示包含分类、原因和单一下一步，不输出环境变量、凭据或任意用户文件内容。

## 5. 隐私与安全边界

允许采集：

- `/etc/os-release` 中的发行版兼容字段；
- dpkg 架构；
- glibc 版本；
- apt/dpkg/systemd 基础能力；
- 固定运行依赖的已安装版本和架构；
- 采集时间、依赖契约摘要和派生 profile ID。

禁止采集：

- 用户名、主机名、IP、MAC、序列号、磁盘标识；
- 文件列表、用户目录内容、进程参数、浏览器或应用会话；
- API Key、Token、密码、模型地址和许可证私密材料。

采集器不得跟随可疑输出路径符号链接，不得把结果写入 group/other 可写目录中的既有目标，不得接受环境变量覆盖 canonical 采集逻辑或依赖契约。

## 6. 固定 Debian 包身份

删除以下人工输入和审批要求：

- `packaging/linux/approved-maintainer.json` 及示例文件；
- `validate-approved-maintainer.py`；
- `TAIJI_PACKAGE_MAINTAINER` 环境变量；
- 相关构建、预检、发布参数和说明。

唯一包身份常量为：

```text
Taiji Agent Product Team <noreply@localhost>
```

该值是 Debian 元数据所需的非个人技术标识，不声明可联系的售后邮箱。构建脚本、DEB `control`、manifest、构建报告和内部 publication receipt 必须逐字一致。任何环境变量或调用参数都不能改变该值；最终门禁从生成的 DEB 控制信息反向校验该常量。

## 7. 测试与验收标准

### 7.1 自动化

- 生成器确定性及陈旧产物检测；
- 内嵌负载摘要篡改后失败关闭；
- Shell 语法和可执行位；
- 输出目录选择、同名幂等和冲突拒绝；
- 原子输出、权限 `0600`、失败无半成品；
- 成功结果通过 canonical `target_baseline.py validate`；
- 输出 schema 不包含禁止隐私字段；
- 无网络、sudo、安装命令或可覆盖采集逻辑；
- 固定包身份贯穿 build/control/manifest/report/receipt；
- 旧维护者文件、环境变量和参数不再是发布合同的一部分；
- 既有静态打包、payload、发布证据和销售单 DEB 回归继续通过。

### 7.2 目标机人工验收

在用户已准备的 Kylin/UOS x86_64 图形终端执行：

1. 将单个采集器复制到桌面并确认可执行；
2. 断开外网或确认采集期间无网络请求；
3. 双击并选择运行；
4. 确认桌面只新增一个 `太极Agent目标机基线_<profile-id>.json`；
5. 将 JSON 回传；
6. 在正式 `main` 使用 canonical validator 校验 schema、依赖契约、最长 30 天时效和 SHA256。

该验收通过后，证据只升级为“目标基线已采集并验证”，不能升级为“制包机已构建”“离线安装已演练”或“目标机已验证”。

## 8. Git 与发布顺序

1. 在现有 `codex/linux-sales-grade-installer` worktree 实施并验证。
2. 创建本地提交，按用户已授权的“标准收尾”push、创建 PR、等待 `CI Gate` 通过后合并。
3. 同步并复验正式 `main`，证明其包含本成果。
4. 从正式 `main` 提供最终采集器给用户；接收并校验目标 JSON。
5. 目标基线、制包对象、环境和回滚边界闭合后，另行执行 Linux amd64 制包、离线演练及目标机安装验收。

标准收尾不授权提前制包、安装、部署或发布。Windows 阶段继续冻结，直到 Linux 目标机安装态证据闭合。

## 9. 明确不在本轮范围

- RPM 或 `.run` 安装包；
- Windows 安装包；
- 绕过 Kylin/UOS 的文件信任或执行策略；
- 自动上传采集结果；
- 远程操作目标终端；
- 在采集阶段安装、卸载或修改任何系统软件。
