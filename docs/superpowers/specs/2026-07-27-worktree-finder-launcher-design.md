# Worktree Finder 双击启动设计

## 目标

用户在 `expert-team-standalone-core` worktree 中双击“启动太极Agent桌面端.app”时，直接打开该 worktree 当前提交的太极 Agent，用于验证最新专家团；不得停止、复用或污染其它 worktree、正式 `main` 和其它运行任务。

## 方案

- `.app` 从自身物理路径解析相邻的 `启动太极Agent桌面端.command`，再通过系统 Terminal 打开该脚本；不接受 Finder 当前目录等隐式来源。
- 源码判断、门禁和 Electron 启动全部留在相邻 `.command` 中，避免 Finder 启动的未签名 shell `.app` 被 macOS 隐私权限阻止读取“文稿”目录下的相邻源码脚本。
- 未显式指定 `TAIJI_SOURCE_MODE` 时，通过源码根的 `.git` 形态判断：primary checkout 的 `.git` 是目录，使用 `formal`；linked worktree 的 `.git` 是文件，使用 `development`。
- Electron user-data、XDG state、runtime-home、workspace、临时目录均按源码物理路径的哈希实例隔离。
- Electron 启动前执行现有 `check-clean-worktree.sh`：开发 worktree 只验证声明来源与实际 Git 顶层一致；正式入口继续要求干净的本地 `main`。
- `.app` 无法找到相邻脚本时使用 Finder 对话框报告；源码门禁或依赖失败时在 Terminal 和隔离日志中报告，不启动 Electron。

## 兼容边界

- 不修改专家团业务代码、正式根目录、安装态应用、端口策略或 Provider 配置。
- 显式传入的 `TAIJI_SOURCE_MODE` 仍优先于自动判断，便于自动化和故障诊断。
- 双击后会短暂显示 Terminal 窗口，这是为取得用户已授予 Terminal 的“文稿”目录访问权限所需的本机启动边界。
- 同一 worktree 的重复双击仍复用该源码实例的 Electron singleton；不同 worktree 使用不同 user-data 和状态目录。

## 验收

1. 自动化测试先证明 `.app` 未委托相邻 `.command`、源码脚本缺少 linked-worktree 自动 development 和完整状态隔离，再验证修改后通过。
2. Shell 语法、plist、现有桌面启动器合同测试通过。
3. 从 Finder 等价路径启动，日志记录当前 worktree、当前 HEAD、`development` 与隔离目录。
4. Electron 窗口加载专家团入口；关闭窗口后，仅本次隔离实例的 Agent/WebUI 和端口退出。
5. 正式根目录启动器行为保持 `formal`；未合入 `main` 前不宣称正式版本已更新。
