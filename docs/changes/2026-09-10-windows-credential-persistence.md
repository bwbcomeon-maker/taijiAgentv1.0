# Windows API Key 与主模型配置保存修复

## 根因与实现

Windows 提供商 Key 和主模型保存进入统一凭据事务后，被仅接受 macOS/Linux 的平台门禁拒绝。安全模式修复使用独立 security-settings.json，不会改变此链路。

本次在统一凭据提交、恢复入口增加 Windows 后端，保留 config.yaml / .env、主模型保存回执、凭据修订与现有跨进程锁。Windows 在锁内发布恢复日志，逐个替换目标；读取前先恢复，完成成对文件校验及进程投影后才成功。两个文件不是一次原子重命名，不承诺无视文件系统/硬件保证的断电原子性。

Windows 临时文件通过 CreateFileW 在写入前配置受保护的 owner/SYSTEM DACL，替换使用 MoveFileExW 的 REPLACE_EXISTING / WRITE_THROUGH。依据：[文件移动接口](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)、[SID 定义](https://learn.microsoft.com/en-us/windows/win32/secauthz/sid-strings)。不改变 macOS/Linux 后端，不迁移安全模式文件，不包含真实凭据。

恢复日志与暂存文件属于机器本地事务状态：保留文件名不能作为 config 路径，备份不导出、导入不接受。发现外部冲突、链接、损坏或缺失恢复材料时保留证据并拒绝后续读取/写入，不能手工删除日志强行继续。

## API 行为

配置错误保留稳定错误码：credential_storage_error 表示未能提交；credential_recovery_required 表示需要恢复/人工检查。HTTP 409 将明确原因传给现有页面，避免归入泛化 500 后被“权威状态未确认”覆盖。超时及未知 5xx 的既有回执核对逻辑不变。

## 验证台账

- RED：虚构 Key 在模拟 win32 路径重现 durable credential transactions are not supported on this platform。
- Windows 逻辑测试：20 项通过，含更新/删除、子进程中断恢复、并发、文件占用、外部冲突、损坏/越界/超限日志及备份排除。
- 原凭据事务：104 项通过。沙箱内的 8 项 setgid 失败在沙箱外隔离重跑全部通过。
- 备份回归：110 项通过。
- 新增隔离 API 保存测试：4 项通过，覆盖提供商保存、主模型回执及两类明确存储错误。
- 完整门禁分段完成：`scripts/verify.sh --full` 的安全检查、基础检查、root（1338 项，跳过 3 项）、Desktop（85 项）和 DOCX 均通过；随后 Agent 的 23 项因沙箱禁止本机 socket 而失败，未把该次整命令标记为通过。
- 使用原门禁相同测试清单在沙箱外隔离补跑 Agent：236 项通过；继续 WebUI lint 通过、WebUI 961 项通过、Agent bootstrap 12 项通过/跳过 5 项、WebUI bootstrap/coexistence 75 项通过。
- branding 两文件因 CLI 导入阶段访问默认凭据锁被沙箱拒绝；保留底层 `run_tests_parallel.py` 和洁净环境、增加独立临时 HERMES_HOME 后重跑，24 项通过；没有放开真实配置访问或修改测试入口。
- 最终提交仍须通过绑定完整 staged bytes 的 Sol 审核；审核结论与提交身份记录在任务交付结果中。
- Windows 原生 API、真实安装态、真实 Provider 连接及聊天：未验证，需新候选包及目标端验收。

## 前端 UX QA 报告

状态：未完成（安装态核心路径待验收）。范围仅“提供商保存”和“主模型保存”的结果反馈；布局和入口未修改。

主要用户目标：在 Windows 输入 Key 后可保存，主模型配置和 Key 一致，失败原因明确且重启后可恢复。主内容为提供商/主模型编辑表单，辅助内容为保存反馈，高级内容为请求回执和本地恢复状态；本轮不改变信息层级。

| 功能契约 | 入口与反馈 | 验证状态 |
| --- | --- | --- |
| 提供商保存 Key | 保留现有保存/删除入口，明确存储错误 | 隔离 API 测试 |
| 主模型成对保存 | 保留配置和密钥一致性、回执与成功反馈 | 隔离 API 测试 |
| 重启后可继续配置 | 锁内恢复后读取 | 独立 Python 子进程模拟通过；Windows 未验证 |

真实浏览器测试：未验证。原因：本轮未启动 UI 验收环境。截图仅有用户提供的故障图，不能证明修复后效果。键盘、焦点、响应式、可访问性、视觉回归、空/加载/成功/禁用状态的真实 UI 检查均未验证。P0 原问题仍需以 Windows 新安装态闭环才能关闭；不将源码测试等同客户可用。

可访问性、视觉层级与长时间工作体验：未验证，未修改相关前端结构或样式。错误/成功反馈已做 API 检查，空/加载/禁用状态与删除确认/取消的实际交互未验证；删除 Key 的存储逻辑已测。

| 严重程度 | 问题与证据 | 修复与后续 |
| --- | --- | --- |
| P0 | 用户截图显示 Windows 保存因平台门禁失败 | 源码及模拟回归已修复；新安装态保存、重启、连接检查待验收 |
| P1/P2/P3 | 本次源码复核未发现新的确定性问题 | 不代表未执行的 UI 检查已通过 |

剩余风险与后续：需在 Windows 验证原生锁、文件 ACL、文件占用恢复及安装态主路径；未取得制包和安装授权前不触碰目标机。本轮不关闭客户故障单，不发布安装包。
