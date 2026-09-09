# Windows 机器码创建修复验证

## 来源与范围

基线：main `8e5d5eddbb89cd1b82713ff7b33979c4a2ce0c1d`，开始时工作树干净且远端一致。本任务仅修复全新安装的 Windows 授权资源创建与读取，不迁移旧安装、不手工修理现有设备身份。

## 已确认事实

- 旧安装设备身份内容有效，但所有者为 Administrators，当前账号为 Administrator，所有者校验拒绝读取。
- 使用安装私有 Python 的管理员令牌（TokenElevation=1）在临时目录复现安全写入失败。
- 异常跟踪进一步发现 pywin32 的 win32con 不提供 FILE_FLAG_OPEN_REPARSE_POINT；win32file 提供值 2097152。旧读写路径因此在目录句柄打开前就可能失败，不能将全部症状归因于所有者。
- 补充独立对照：同一管理员令牌用原来的 CreateFile(securityAttributes=None) 创建全新临时文件，实测 owner=Administrators、owner!=当前用户。修复后的显式安全属性创建链返回 owner=当前用户。

## 本次修改

Windows 安全创建显式提供当前用户 owner 和受保护 DACL；不放宽读取校验。所有 Windows 资源打开使用 Win32 标准 reparse 标志。写入失败日志只记录阶段、异常类型和 Win32 错误码。制包新增调用真实安全写入路径的隔离负载探针。

后续安装态机器码文件虽然可以成功导出，但签发工具以 `no_stable_hardware` 拒绝。实际请求显示设备密钥和物理 MAC 可用，主机 UUID、主板序列号、Linux machine-id 和 macOS platform UUID 均不可用。根因是采集器只实现 Linux 文件与 macOS `ioreg`，Windows 没有稳定硬件采集入口。后续修复使用 Windows PowerShell CIM 读取主机 UUID 与主板序列号；固定命令参数、`shell=False`、五秒超时，查询失败仍保持弱指纹并由既有签发门禁拒绝，不把物理 MAC 提升为稳定硬件。

## 验证记录

- RED：旧安装隔离调用 _write_license_device 失败；源码回归断言显式安全属性缺失失败。
- GREEN：授权模块 135 项通过（包含显式安全属性、现有安全拒绝与原子写入回归）。
- 全量本地验证：`scripts/verify.sh --full` 最终 exit=0、`verification: PASS`。使用已准备的 Node 24.19.0 和 Agent venv。第一轮 Agent 23 项因沙箱禁止本地 socket/进程操作失败；定位 PermissionError 后在允许本地测试操作的环境重跑同一全量入口，通过全部注册门禁。
- 修改后真实 Windows 管理员令牌（TokenElevation=1）及安装私有 pywin32 311：增强探针通过，包括 canonical 机器码创建、新进程稳定性、真实相对目录的授权资源替换和正式状态写入、protected DACL、hardlink/Everyone-write/junction 拒绝。
- 普通令牌隔离源码验证：通过。经授权的临时标准账号在随机私有 station/desktop 中 TokenElevation=0，安装私有 Python/pywin32 311 正常加载，增强探针全部通过。私有桌面仅授权 QA/管理员/SYSTEM，未修改或切换现有交互桌面；测试账号、profile、一次性任务已清理。此证据不代表普通用户桌面安装态。
- Windows 硬件采集 RED/GREEN：新增回归测试在旧实现上因从未调用 Windows 查询而失败；Sol 首审发现全 F UUID 等占位值可能被误判为强指纹，补充的失败闭合测试再次先失败。类型化校验拒绝空值、全 0、全 F、无效 UUID、常见 OEM 序列号占位、查询非零退出与超时；只有物理 MAC 时仍保持 `weak` 和 `no_stable_hardware`。实现后授权模块 142 项通过。目标机 CIM 只读预检确认主机 UUID与主板序列号均可用；把当前工作树源码放入临时目录、使用已安装私有 Python 运行后，组件名为 `dmi_board_serial`、`dmi_product_uuid`，风险标记为空。探针没有输出硬件值，临时目录已清理。
- 安装态 PATH 缺口复现：提交 `749b4f28` 的安装目录模块摘要与源码一致，SSH 环境直接调用为 `strong`；但安装态 Electron 把后端 `PATH` 收紧为私有 Node、私有 Python 和 `System32`，裸 `powershell.exe` 无法解析，实际 UI 导出的请求因此仍为 `weak` 与 `no_stable_hardware`。目标机在相同收紧 PATH 下最小复现确认 `System32\powershell.exe` 不存在、`System32\WindowsPowerShell\v1.0\powershell.exe` 存在。回归测试先以裸命令失败，修复后从绝对 `SystemRoot` 构造 PowerShell 路径，并在 `SystemRoot` 缺失或非绝对路径时失败闭合；制包负载探针同步采用安装态 PATH，防止构建环境再次掩盖缺口。定向授权模块 144 项与 Windows 桌面环境 6 项通过；目标机第二次探针明确加载当前临时源码，在相同收紧 PATH 下得到 `strong`、空风险标记和两项稳定硬件信号，探针目录已清理。
- 完整源码验证：首次 `scripts/verify.sh --full` 仅在未改动的 DOCX 模板运行时出现一次并发陈旧锁时序失败；该用例独立复跑通过。第二次完整运行退出码为 0 并输出 `verification: PASS`，其中 Python 主套件 1334 项、桌面 79 项、DOCX 279 项、授权与安全定向套件 230 项及其余验证分组均无失败。
- 测试启动失败与恢复：最初 SSH 跨用户继承的桌面上下文出现扩展 DLL 初始化失败；独立用户环境块未解决，任务调度器路径返回未执行 0x41303 后停止。仅切换到专用私有测试桌面的 launcher 后模块加载及探针成功，没有修改或放宽产品校验。
- Windows 候选制包：已生成。run `20260909T034850Z-e90c008006f6-1d5fd0ad` 绑定 `main@1d5fd0adcecfc071fdcfacc0592a39cedde9f04b` 与 tree `0b8980feab3fe24935f3e275efc66b7b864a851b`，输入、传输、远端输入复核、远端构建、review 取回和本地交叉校验均通过。
- 候选 EXE：`TaijiAgent-Setup-1.0.2-win-x64.exe`，280522225 字节，SHA-256 `3d934ab1c3537a39cd288a8f134dc50197ea6a1f229d9d36e962d259a96c46ad`；产品与文件版本均为 `1.0.2.0`，Authenticode 为 `NotSigned`。
- 正式制包检查：7/7 通过，包括 source-session-identity、offline-npm-ci、electron-win32-x64、payload-import-menu-policy、payload-hygiene-closure、inno-compile、installer-pe-version-authenticode。冻结输入归档包含 `Test-LicensePayload.ps1` 与 `license_payload_smoke.py`；其调用位于第 04 项检查内。
- 全新安装、授权导入及桌面交互验收：未验证。

## 前端 UX QA 报告

状态：未完成（新包安装态 UI 尚未执行）。按项目 frontend-ux-qa 技能界定受影响路径：设置 → 模型配置 → 机器码导出 → 导入授权 → 刷新授权状态。普通用户可见入口已有，前端布局与公共 API 未改动。

机器码导出、文件下载、重启后重复导出、有效授权导入及成功反馈的真实界面操作均未验证。截图、键盘、可访问性自动化、视觉层级、长时间使用、空/加载/错误/禁用状态均未验证。旧包导出主路径已有阻塞；本地源码测试不能证明其安装态消除。

后续：候选制品已生成。安装前须明确测试状态清理范围、绑定该 SHA-256 的制品和目标机动作；完成后再验收机器码导出、重启稳定性、授权导入及 UI 路径。普通令牌与管理员令牌的隔离源码证据均不能替代新包桌面验收。

## 开发收尾状态

唯一写入者：当前任务主 Agent。Windows 安全创建（ACL）修复已作为 `1d5fd0adcecfc071fdcfacc0592a39cedde9f04b` 推送，初版 CIM 硬件采集已作为 `749b4f2812288611478e1cf02c0e3bcacf5281e4` 推送；安装态 PATH 与绝对 PowerShell 路径修复当前仅完成暂存和本地验证，尚未 commit/push，也没有包含该修复的新候选、安装或 UI 验收。旧候选 EXE 只证明旧提交对应的制包结果；签名与发布均未完成。
