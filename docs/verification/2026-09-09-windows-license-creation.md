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

## 验证记录

- RED：旧安装隔离调用 _write_license_device 失败；源码回归断言显式安全属性缺失失败。
- GREEN：授权模块 135 项通过（包含显式安全属性、现有安全拒绝与原子写入回归）。
- 全量本地验证：`scripts/verify.sh --full` 最终 exit=0、`verification: PASS`。使用已准备的 Node 24.19.0 和 Agent venv。第一轮 Agent 23 项因沙箱禁止本地 socket/进程操作失败；定位 PermissionError 后在允许本地测试操作的环境重跑同一全量入口，通过全部注册门禁。
- 修改后真实 Windows 管理员令牌（TokenElevation=1）及安装私有 pywin32 311：增强探针通过，包括 canonical 机器码创建、新进程稳定性、真实相对目录的授权资源替换和正式状态写入、protected DACL、hardlink/Everyone-write/junction 拒绝。
- 普通令牌隔离源码验证：通过。经授权的临时标准账号在随机私有 station/desktop 中 TokenElevation=0，安装私有 Python/pywin32 311 正常加载，增强探针全部通过。私有桌面仅授权 QA/管理员/SYSTEM，未修改或切换现有交互桌面；测试账号、profile、一次性任务已清理。此证据不代表普通用户桌面安装态。
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

唯一写入者：当前任务主 Agent。修复源码已作为 `1d5fd0adcecfc071fdcfacc0592a39cedde9f04b` 推送到 `main`；未触碰原安装模块，其诊断时摘要保持不变。当前交付口径为“候选 EXE 已生成并经本地交叉校验”；安装态 UI、签名与发布均未完成。
