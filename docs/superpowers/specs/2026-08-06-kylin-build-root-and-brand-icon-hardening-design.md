# 太极 Agent 麒麟制包目录兼容与品牌图标统一设计

日期：2026-08-06

适用成果：`codex/linux-sales-grade-installer` 国产 Linux 离线交付链

补充规格：[太极 Agent 统一国产 Linux amd64 DEB 设计](2026-08-05-unified-kylin-uos-amd64-deb-design.md)

## 1. 背景与当前证据

用户在 Kylin Linux Desktop V10 SP1 x86_64 制包机上运行统一 DEB 制包输入包。依赖安装、源码包校验、Python/Node 准备和 npm 安全审计已经继续通过，但 DOCX Engine V2 测试阶段中断，尚未进入最终 DEB 生成阶段。

当前日志中的首个业务失败为：

```text
.../node_modules/@resvg/resvg-js-linux-x64-gnu/resvgjs.linux-x64-gnu.node:
failed to map segment from shared object
```

同一轮测试中，测试夹具创建的临时假 `npm` 未被执行，进程继续命中了真实 npm。实际下载包的完整性摘要与正式 lockfile 一致、与测试夹具预期不一致。这两项现象与制包入口当前把源码、原生模块和测试临时文件固定放入 `/tmp/taiji-agent-build-<uid>` 的实现相互印证。

因此当前根因结论为：制包机的 `/tmp` 受到 `noexec` 或等效安全控制，允许写入文件但阻止从该位置执行临时程序或映射 Node/Electron 原生模块。现有日志没有直接保存 `findmnt` 的挂载标志，故“具体由 noexec 还是等效安全策略实施”仍需新版诊断在目标机上确认；修复不依赖区分二者。

本次错误与此前 npm audit registry 修复不是同一个问题。该修复已在本轮日志中通过，并暴露了下一层原生模块执行阻断。

## 2. 目标

本设计同时完成两个目标：

1. 让统一制包入口在符合第一版支持范围的 Kylin、UOS、openKylin amd64 图形桌面制包机上，避开不可执行的临时目录，并在真正构建前验证所选目录能运行程序和加载动态库。
2. 以用户批准的蓝色太极机器人为唯一产品 Logo，统一标准 DEB 的安装器元数据、桌面启动器、开始菜单、任务栏、Electron 窗口、Web favicon 和 PWA 图标。

本设计不放宽系统安全策略，不重新挂载 `/tmp`，不关闭 kysec，不跳过测试，也不把标准 DEB 改成 `.run` 或自定义 GUI 安装向导。

## 3. 已批准的产品决策

- 继续采用标准 DEB，不增加第二套安装器制品。
- 标准安装器通过 AppStream 与 Linux 图标主题规范尽力展示产品 Logo；文件管理器如何显示 `.deb` 文件本身由操作系统和 MIME 处理器控制，不作为可保证合同。
- 唯一标准图形为当前仓库的蓝色太极机器人，即 `hermes-local-lab/sources/hermes-webui/static/assets/taiji/logo/logo-mark.png` 所代表的产品标识。
- 浏览器 favicon 与 PWA 中残留的黑金旧图形一并收口。
- 构建目录采用真实能力探测，不采用只替换一个固定路径的快速止血方案，也不维护按发行版分叉的手工制包模式。

## 4. 构建目录兼容设计

### 4.1 候选目录

制包入口在安装构建依赖后、解压源码前选择工作目录。候选顺序为：

1. 用户显式设置的 `TAIJI_BUILD_ROOT`；
2. `${XDG_CACHE_HOME}/taiji-agent-build-<uid>`，仅当 `XDG_CACHE_HOME` 为安全的绝对路径时使用；
3. `${HOME}/.cache/taiji-agent-build-<uid>`；
4. `/var/tmp/taiji-agent-build-<uid>`。

自动候选必须去重。路径必须为绝对路径、归当前用户控制，构建根和内部临时目录使用 `0700`。不得接受 `/`、用户主目录本身、仓库根或包含现有非本任务内容的宽泛目录作为清理目标。

用户显式设置 `TAIJI_BUILD_ROOT` 时采用失败关闭：该目录不满足要求即中止并说明原因，不静默换到其他目录。自动选择时才按顺序继续探测下一个候选。

### 4.2 真实能力探针

只检查可写权限不足以证明 Node/Electron 原生模块可以工作。每个候选至少执行：

- 创建并直接运行一个临时可执行文件；
- 编译一个最小共享库并通过 Python `ctypes.CDLL` 或等效本地方式实际加载；
- 对实际候选路径记录 `findmnt`、文件系统类型、目录属主和权限等只读诊断。

共享库探针放在系统构建依赖安装之后执行，避免把“编译器尚未安装”误判为目录不兼容。探针文件无论成功或失败都限定在候选目录的专用子目录内并按路径边界安全清理。

### 4.3 临时目录统一

选中工作目录后，在其中创建专用 `tmp` 子目录，并在 Python、uv、Node、npm、Electron 构建和测试启动前统一导出：

```text
TMPDIR=<selected-build-root>/tmp
TMP=<selected-build-root>/tmp
TEMP=<selected-build-root>/tmp
```

源码解压目录、虚拟环境、`node_modules`、测试夹具和子进程临时文件都必须继承该环境。仅移动源码而不调整这三个变量不满足本设计，因为测试和依赖工具仍可能通过系统临时目录回到受限的 `/tmp`。

### 4.4 错误和诊断

找不到合格目录时，制包在源码解压与依赖构建前停止，输出稳定中文错误、各候选失败阶段和日志路径。诊断应区分：

- 路径不安全或不是绝对路径；
- 无法创建、写入或执行文件；
- 共享库无法映射或加载；
- 目录所有权或权限不合格；
- 编译/加载探针本身缺少先决工具；
- 挂载或安全策略只读证据。

错误信息不得建议用户关闭 kysec、重新挂载 `/tmp` 为 exec、全程使用 root 或跳过测试。

## 5. 品牌图标与标准 DEB 设计

### 5.1 Canonical 图标与派生资产

蓝色太极机器人是唯一 canonical 产品图形。按照项目图片门禁，使用 GPT Image 2.0 以该图为参考制作适合 Linux 应用图标的小尺寸候选稿：不添加文字、不改变机器人核心识别形态、保持透明背景和安全留白，并重点检查 32、48、64 像素下的轮廓清晰度。

生成结果必须经过实际图片查看和并排视觉核验；出现形变、额外元素、文字、背景污染或品牌偏移时重新生成，不能直接进入制品。通过核验的候选作为应用图标派生源，确定性生成 32、48、64、128、256、512 像素 PNG。原始产品 Logo 保留为品牌基准，不被旧黑金图形替代。

### 5.2 Linux 桌面与窗口关联

DEB 至少安装以下内容：

- `/usr/share/icons/hicolor/<size>x<size>/apps/taiji-agent.png` 多尺寸图标；
- `/usr/share/applications/taiji-agent.desktop`，使用 `Icon=taiji-agent`；
- `/usr/share/metainfo/taiji-agent.metainfo.xml`，声明 desktop application、desktop-id、产品名称、摘要和 stock icon；
- `/opt/taiji-agent/resources/icons/taiji-agent.png`，供 Electron 窗口显式使用。

桌面启动链补齐统一应用类名：启动器、`.desktop` 的 `StartupWMClass` 和 Electron 的 desktop name/name 使用同一稳定标识。主窗口和登录/授权子窗口都显式使用安装态图标。安装和卸载阶段只在系统工具存在时刷新桌面数据库与图标缓存，不把某个桌面环境专有工具设为硬依赖。

这套关联用于降低 UKUI/DDE 中 Electron 通用图标、启动图标与运行图标分裂或重复分组的风险。最终效果仍需在真实 UKUI 和 DDE 桌面按当前 DEB 验证。

### 5.3 Web favicon 与 PWA

HTML favicon、ICO 回退、PWA manifest 和 maskable 图标全部改为蓝色太极机器人派生资产。旧黑金 SVG/ICO 不再出现在运行时引用链中。是否删除旧源文件由引用和历史兼容审计决定；不能仅因“不再使用”而在未确认归属时扩大删除范围。

### 5.4 标准 DEB 能力边界

AppStream 与 hicolor 图标能让遵循相关规范的麒麟、统信图形安装器和应用菜单发现产品 Logo，但标准 Debian control 没有可跨所有文件管理器强制 `.deb` 文件图标的通用字段。因此完成定义是：

- 我方可控的包元数据、桌面入口、运行窗口和 Web/PWA 图标统一；
- 在目标安装器上实测并记录实际预览；
- 不承诺所有系统的 `.deb` 文件缩略图或安装器实现完全一致。

所有图标随 DEB 离线安装，不依赖安装后联网获取。

## 6. 测试与验证

### 6.1 自动化回归

先补失败测试，再实现修复。自动化至少覆盖：

- 默认不再硬编码 `/tmp/taiji-agent-build-*`；
- 候选顺序、去重、危险路径拒绝和显式覆盖失败关闭；
- 执行探针、共享库加载探针及失败诊断；
- `TMPDIR/TMP/TEMP` 在源码解压、Python、npm 和测试前生效；
- 测试夹具创建的临时可执行文件不会绕回系统 `/tmp`；
- `.desktop`、AppStream、Electron 类名和窗口图标合同；
- hicolor 多尺寸 PNG 的尺寸、格式、路径和来源清单；
- favicon/PWA 不再引用黑金旧 Logo；
- DEB payload audit、manifest/native verify 对图标资产做存在性、格式和摘要校验；
- 既有构建、DOCX、Electron 启动和离线交付合同不回归。

macOS 自动化只能证明脚本、资产和制品合同，不能替代 Linux 原生构建或真实桌面显示。

### 6.2 麒麟制包机

新输入包在 Kylin 制包机运行时必须记录：选中目录、三个临时目录变量、实际探针、`findmnt` 诊断、完整测试、DEB 生成和离线交付审计。原生 resvg 加载与此前失败的测试族必须转绿，才能继续生成候选制品。

### 6.3 制品与离线目标机

最终 DEB 生成后解包核对多尺寸图标、AppStream、desktop-id、窗口资源和 Web favicon。随后在断网的 Kylin/UKUI 与 UOS/DDE 代表终端上验证双击安装、菜单入口、任务栏分组、主/登录窗口图标、Web favicon、启动退出、重装和卸载。

图形安装器中的实际 Logo、任务栏分组和窗口图标需要截图或人工见证；没有绑定当前 DEB SHA256 的目标机证据时，状态只能是“源码验证通过”或“候选制品已生成”，不能宣称离线交付已通过。

## 7. 交付物与状态口径

本轮本地实现完成并通过适用门禁后，先生成新的制包机输入包与 SHA256。该输入包不是客户最终安装文件。联网 Linux 制包机从经复验的正式来源生成统一 amd64 DEB；最终客户目录仍遵循一个不可变 DEB 的既有规格。

交付状态分层：

1. 分支源码与自动化通过；
2. 正式 `main` 包含成果并复验；
3. Linux 制包机生成当前 DEB；
4. 当前 DEB 通过目标机安装态验收；
5. 代表矩阵和发布证据闭合。

前一状态不能自动推出后一状态。

## 8. 非目标

- 不为每台终端采集基线或生成不同安装包；
- 不覆盖 ARM、RPM-only、无图形桌面或 Windows；
- 不增加 `.run`、AppImage 或自定义 GUI 安装器；
- 不修改目标机挂载参数、kysec 或全局安全策略；
- 不跳过 DOCX、Electron、native addon 或发布测试；
- 不保证操作系统文件管理器给 `.deb` 文件本身展示产品 Logo；
- 不在 macOS 上宣称生成了可销售的最终 Linux DEB。

## 9. 完成定义

只有以下条件均有当前证据，才可以把本设计对应成果标记为完成：

- 构建目录和全链临时目录不再依赖可执行的 `/tmp`；
- 真实执行与共享库加载探针、错误诊断和回归测试通过；
- 蓝色太极机器人贯通标准 DEB、桌面、任务栏、Electron 窗口和 Web/PWA；
- 黑金旧图标不再位于运行时引用链；
- 离线交付手册与当轮验证台账同步更新；
- 新制包机输入包绑定明确 commit 与 SHA256；
- 当前候选 DEB 在 Kylin 制包机完成构建与制品审计；
- 当前候选 DEB 在约定 Kylin/UOS 代表终端完成断网安装与桌面验收。

在最后两项完成前，只能报告源码或候选制品层级的真实状态。
