# Windows 原生菜单隐藏候选验收

## 来源与范围

- 用户授权从提交 `36a7a924afd74382805f6af81b3622fb0597c064` 重新执行 Windows 制包、安装并在 Windows 交互桌面验证。
- 制包开始时 `main`、本地 `origin/main` 与远端 `origin/main` 均指向该提交，工作树干净；source tree 为 `61e9cb4e9c53e361b54fd13c51dcea0b48626b90`。
- 本轮只覆盖 Windows 候选构建、安装态与受影响界面路径；未执行签名、Tag、Release、发布、模型配置或完整业务对话验收。

## 候选制品

- run：`20260910T015831Z-ce338114345c-36a7a924`。
- 正式制包检查 7/7 通过：source-session-identity、offline-npm-ci、electron-win32-x64、payload-import-menu-policy、payload-hygiene-closure、inno-compile、installer-pe-version-authenticode。
- EXE：`TaijiAgent-Setup-1.0.2-win-x64.exe`，`280525540` 字节，SHA-256 `f410d559466447f14415ef046c6792dc51bb5ac120d64c3529bd8aac0b2b28bf`，产品版本与文件版本均为 `1.0.2.0`，Authenticode 为 `NotSigned`。
- 本地 review：`~/.local/state/taiji-package/runs/20260910T015831Z-ce338114345c-36a7a924/review/`。

## 安装态验证

- Windows 目标：`windows-direct`，Windows `10.0.19044.0` x64；远端安装前复算 EXE SHA-256 与本地 review 一致。
- Inno 日志记录文件覆盖、快捷方式和卸载项创建成功，结论为 `Installation process succeeded`，无需重启。
- 安装位置 `C:\Program Files\Taiji Agent\`；卸载项 `{B2C40D2B-8F6D-4E30-9D6D-8E0C9FC2E824}_is1` 存在，DisplayVersion 为 `1.0.2`。
- 安装后 `resources\app\src\main.js` SHA-256 为 `f6c59040dcb5a7d0d1829ce3e7e53b442b9cc41b43d4bf888b1ace8eb0e7c2c5`，与候选 manifest 一致；文件包含 `process.platform === "linux" || process.platform === "win32"` 菜单策略。
- 应用以 `TAIJI_WINDOWS_CANDIDATE=1` 在交互 Session 1 启动；安装私有 Python 返回 `profile=windows-candidate`、`installed_materials=true`。私有服务监听 `127.0.0.1:18642` 与 `127.0.0.1:18787`，应用页面和本地 Chromium 调试端口均返回 HTTP 200。
- 取证结束后已关闭验收进程并无调试参数重新启动；应用继续运行在交互 Session 1，业务端口恢复监听，临时 Chromium 调试端口已关闭。

## 前端 UX QA 报告

状态：带限制完成。

- 真机已验证：真实 Windows 应用窗口标题为 `Agent`；标题栏下方直接进入“开始使用前检查”产品内容，没有出现旧版的“太极 Agent / 编辑 / 视图”原生菜单行；页面正常渲染。
- 页面状态：授权、工作区和安全策略显示已就绪；模型项显示“需要处理”，属于目标机尚未配置模型的既有状态，不是本次安装或菜单回归。
- 源码与安装树静态证据：菜单对象、edit roles 和既有快捷键实现未被移除；安装态只确认了菜单行默认不可见。
- 截图 `taiji-window-36a7a924.png` SHA-256：`deccd772b2b3aaa64653f9aec5b735b8857a6454f9074c34aefd98e7980d5029`；截图 `taiji-page-36a7a924.png` SHA-256：`a1f954f402f632f8d71910d08cb532cb0754e0bc9381b2dd0f666740d050c71d`。两张原图保存在本次 Codex 可视化工作区，未提交仓库。
- 未验证：安装态按 `Alt` 唤出菜单、既有编辑快捷键实操、模型配置、真实 Provider 调用、完整聊天业务、键盘遍历、长时间稳定性、普通用户令牌安装、签名与发布。

## 诊断记录

- 首次 online doctor 因 `windows-direct` 的旧地址不可达而阻断，未创建 run、未传输、未构建。控制机存在同网段双接口，默认路由选择了错误接口；本轮显式绑定可达源接口，并严格复用该逻辑目标已校验的主机密钥后通过 doctor，未修改用户 SSH 配置或系统路由。
- 初次桌面截图只得到壁纸；第二次窗口枚举命中 Electron 子进程继承的 PowerShell 控制台句柄，两者均未用作通过证据。最终改用隐藏启动器、Chromium 页面截图及标题为 `Agent` 的真实窗口句柄截图完成验收。
- 安装日志原件在核对成功结论后随远端临时探针清理，台账仅保留关键结论；候选构建报告、manifest 和本地 review 保留在对应 run 目录。
