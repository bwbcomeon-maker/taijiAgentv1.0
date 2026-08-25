# Taiji Agent 单人开发、提交与发布准备 Runbook

本文是 [`development-lifecycle.md`](./development-lifecycle.md) 的 supporting runbook，只提供日常命令、停止条件和恢复细节。授权边界、状态定义和证据口径以 canonical 生命周期为准。

## 1. 日常 `main` 流程

### 1.1 绑定仓库与唯一写入者

在正式仓库根目录检查：

```bash
pwd -P
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list --porcelain
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
```

当前分支必须是 `main`。开始修改前应确认本地与远端没有未知分叉，并明确一个写入 Agent；并行 Sol、规范和质量审查均保持只读。已有脏路径逐项标注归属，不能顺手清理或夹带。

默认顺序是：

1. 直接在 `main` 做最小改动；行为变更保留 RED → GREEN 证据。
2. 运行适用本地验证。
3. Sol 可预审工作树目标与风险。
4. 仅按明确文件路径暂存。
5. Sol 审核完整 staged 候选；任何 staged bytes 变化都重新审核。
6. 创建一个 Conventional Commit。
7. 刷新 `origin/main`，证明远端未领先，再正常推送。
8. 核对本地、remote-tracking ref 与远端 SHA。

用户要求修改、修复或完成仓库任务且未明确限定 `local-only` 时，上述 commit、刷新和正常 push 是默认收尾，无需再询问。“按标准收尾”只是对同一默认流程的明确快捷表达；Tag、GitHub Release、制包、安装和发布仍需单独授权。

## 2. 本地验证模式

```bash
# 自动汇总 staged、unstaged、untracked 路径并按风险分级
./scripts/verify.sh

# 高风险、不确定范围、发布相关改动：全部可离线套件
./scripts/verify.sh --full

# 单独的真实浏览器 smoke；不会被默认或 --full 隐式调用
./scripts/verify.sh --browser-smoke
```

- 每种模式先运行本地改动安全检查，拒绝私钥、明显令牌和临时/制包输出。
- 默认与 `--full` 不安装依赖、不访问网络、不启动浏览器或持久服务。
- 缺少 Python、Node、npm、准备好的依赖目录或测试入口时，脚本必须明确打印缺失项并失败；不得自动安装后把环境变化当作验证。
- `--browser-smoke` 缺少 Python Playwright 时以专用状态失败并写明前置缺失；记录为“未验证”，不能写 PASS。
- 自动化通过不能替代真实 Electron、OAuth/Provider、WPS/Word 或目标机验收。

## 3. Sol 暂存后最终审核

精确暂存前可以预审，但只有暂存后的最终审核能授权 commit。Sol 必须审核以下五个视图：

```bash
git status --short
git diff
git diff --cached --name-status
git diff --cached --check
git diff --cached
```

审核清单：

- diff 是否只实现本任务，是否有无关格式化、回退或依赖变化；
- RED/GREEN 或文档合同是否与实际需求一致；
- staged、unstaged、untracked 是否均有明确归属；
- 是否存在秘密、日志、缓存、安装包、归档、生成物或用户数据；
- 当前验证能证明什么，浏览器、真实桌面、目标机等哪些仍未验证；
- cached path/status、执行位和完整字节是否正是本任务提交候选；`git diff --cached` 为空时不能给出最终通过。

发现问题时回到修改和验证，再精确暂存并重跑全部五个视图。任何重新暂存都会改变 staged bytes 并使旧结论失效；必须取得绑定最新 cached patch 的新审核结论。

## 4. 精确暂存、提交与正常推送

```bash
# 仅列出本任务明确路径；不使用宽泛暂存
git add path/to/file another/explicit/path
git status --short
git diff
git diff --cached --name-status
git diff --cached --check
git diff --cached

# 以上五个视图由 Sol 对当前 staged bytes 给出最终 PASS 后才执行
git commit -m "type: concise purpose"

git fetch origin main
git rev-list --left-right --count HEAD...origin/main
git merge-base --is-ancestor origin/main HEAD

git push origin main
git fetch origin main
git rev-parse HEAD
git rev-parse origin/main
git status --short
```

`git rev-list --left-right --count HEAD...origin/main` 的第二个数字必须为 `0`，且祖先检查必须成功，才说明远端没有本地尚未吸收的领先提交。任何检查失败都停止在本地 commit；禁止强推、reset 或用历史改写消除分叉。

提交信息使用 Conventional Commits。一个提交表达一个逻辑目的；文档规则、实现及其合同可在同一原子成果中提交，但不得夹带其它任务。

## 5. 失败与恢复

### 5.1 push 前修正

未提交时继续最小修改并重跑验证。已有本地提交但尚未推送时，优先创建后续修正提交；只有提交尚未共享、范围完全明确且协调者明确选择整理本地历史时，才评估非破坏性的本地整理。不得丢弃来源不明的工作树内容。

### 5.2 远端领先或分叉

1. 停止推送，记录 `HEAD`、`origin/main`、状态和分歧计数。
2. 为本地提交建立明确、可恢复的备份引用或 bundle。
3. 进入第 8 节例外流程，由一个写入者在新基线上重新集成并重跑验证。
4. 只有新结果通过审核且远端领先为零，才恢复正常 push。

不得自动 rebase/reset 共享 `main`，不得覆盖远端提交。

### 5.3 push 后回滚

```bash
git switch main
git fetch origin main
git revert <bad-commit>
./scripts/verify.sh
# Sol 重新审核后，按第 4 节精确检查并正常推送
```

`git revert` 创建可审计的反向提交，不移动远端分支或版本标签。若涉及数据库、配置迁移、安装态或已发布制品，代码 revert 不能自动回滚外部状态，必须另行授权相应恢复动作。

## 6. 异步 `Main Validation`

- GitHub workflow `Main Validation` 只由 push 到 `main` 或手动 dispatch 触发。
- 日常推送不把它设为 required check；它是异步补充证据，不能替代本地验证或 Sol 审核。
- 异步失败仍需调查，避免 `main` 长期积累已知失败。断言失败、编译错误和可重复超时按真实缺陷处理；只有明确的 runner/GitHub 服务故障才按基础设施问题记录。
- 正式发布证据链引用某次运行时，该运行必须是目标仓库、目标 `main` commit、`push` 事件、`CI Gate` job 及规定聚合步骤的成功结果；“非 required”不等于发布时可以忽略。

## 7. RC、稳定 Tag 与 GitHub Release

日常 `main` 是开发线，不是稳定版本。只有取得明确授权后才能执行版本动作：

- RC：附注标签 `vX.Y.Z-rc.N`；
- 稳定版：附注标签 `vX.Y.Z`；
- GitHub Release：绑定一个已授权的正式稳定标签。

1. **Tag 前候选检查**：普通候选先证明 clean main；已获授权的稳定版 hotfix 则证明 clean 非 `main` 临时分支/worktree 精确起于已发布稳定基线 Tag，且相对基线只含获批修复。两种来源都必须证明根目录与 Desktop 版本一致、Release Notes 为非空普通文件，并运行：

```bash
./scripts/verify.sh --full
```

2. 取得具体 Tag 的明确授权，再为通过候选检查的确定 commit 创建 annotated Tag。该授权不包含任何远端标签或 Release 动作。

3. **Tag 创建后**，普通 `main` 候选立即运行 `scripts/release-check.sh`：

```bash
./scripts/release-check.sh \
  --tag vX.Y.Z \
  --release-notes /absolute/path/to/release-notes.md
```

`release-check.sh` 验证附注标签指向 HEAD、版本与 notes 一致，并复验 `scripts/verify.sh --full`。只有复验通过，才可分别取得授权后推送 Tag、创建 GitHub Release。脚本本身只读，不创建/移动/推送标签，不创建 Release，不构建、不安装、不发布。

已获授权的稳定版 hotfix 临时分支/worktree 必须显式绑定已发布稳定基线：

```bash
./scripts/release-check.sh \
  --tag vX.Y.Z \
  --release-notes /absolute/path/to/release-notes.md \
  --hotfix-from vA.B.C
```

Hotfix 模式只接受 clean 非 `main` 分支/worktree，要求基线与候选都是 annotated 稳定语义化 Tag、候选是相同主次版本下更高的修订版本、基线是当前 HEAD 的祖先。未传 `--hotfix-from` 的 linked worktree 仍会拒绝。脚本不联网判断基线是否已有 GitHub Release；开始 hotfix 前记录的已发布身份仍须单独核验。

正式 Tag 必须不可移动。发现错误时创建新版本/RC 或按明确撤回流程处理，不能强推同名标签。创建 GitHub Release、上传制品和公开发布分别记录标签、commit、制品 SHA256、渠道和回滚入口。

GitHub Release 的 name 必须与稳定 Tag 完全一致；正文必须包含 Release Notes 以及安装或升级说明。所有 Release 资产必须来自该 Tag 对应的 commit，上传前后逐项核对 basename、字节数和 SHA256。已发布资产不得静默覆盖、替换或用同名文件改变摘要；发现问题时停止传播旧资产并发布新的修订版本，重新取得所需授权和门禁证据。

## 8. 分支/worktree 例外流程

任何分支/worktree 例外开始前都必须先取得用户对具体例外的明确授权。仅可为 hotfix、高风险重写/大升级、多个源码写入者、并行维护多个已发布版本，或远端领先后的安全重集成提出申请；列举或符合这些场景不构成授权，也不能自动创建分支或 worktree。

以下 `origin/main` 示例只适用于已获授权的非 hotfix 例外：

```bash
git fetch origin main
git worktree add .worktrees/<task> -b codex/<task> origin/main
```

开始前记录基线、分支、worktree 绝对路径、唯一写入者、依赖、预计整合顺序和回滚。例外成果完成验证和审核后，以明确方式整合回最新 `main`；再次运行完整适用验证和 Sol 审核，再走正常 push 检查。

hotfix 必须从目标版本的已发布稳定 Tag 起步，不得使用 `origin/main`；不得把 `main` 中尚未发布的功能或改动混入修订版。取得例外授权后按以下顺序执行：

1. 从已发布稳定 Tag 创建临时 branch/worktree：先核对 Tag 为目标不可移动稳定版本并记录其 commit，再以该 Tag 为 worktree 基线。
2. 只实施单一 hotfix，保持补丁可独立审查和回滚。
3. 完成完整验证和 Sol 审核，核对相对原稳定 Tag 的完整差异和发布门禁证据。
4. 另行授权创建修订版 annotated Tag，使用相同主次版本下更高的新修订版本号；禁止移动或复用原 Tag。
5. 在 clean 临时分支/worktree 中执行 `scripts/release-check.sh --tag <新修订Tag> --release-notes <绝对路径> --hotfix-from <已发布稳定基线Tag>`；未显式声明基线的 linked worktree 必须拒绝。
6. 复验通过后另行授权推送 Tag；再另行授权创建对应 GitHub Release，并按第 7 节核对名称、正文、资产来源和摘要。Tag 创建、Tag 推送和 Release 不互相授权。
7. 将相同修复同步回 `main`，重新执行适用验证、Sol 审核和正常提交推送流程。
8. 审计后删除临时 branch/worktree；先确认修订版与 `main` 均已吸收修复，且临时资源没有独有内容或关联进程。

清理前核对全部 worktree、refs、未跟踪/未提交内容、仅分支提交、关联进程和备份。任何内容价值或归属不明时停止；不得机械升级为强删。

## 9. Linux 候选的额外门禁

`scripts/taiji-release-check.sh` 保持为 Linux 候选的额外 DEB、签名、认证矩阵和 GitHub CI 证据门禁，不属于日常 `scripts/verify.sh`，也不得被通用 `release-check.sh` 弱化或替代。

真实 Kylin/UOS 制包、断网生命周期、首次安装、升级/卸载、桌面启动、诊断和目标机证据遵循 [`taiji-kylin-uos-offline-delivery.md`](./taiji-kylin-uos-offline-delivery.md)。本地测试、异步 CI 或已有 DEB 都不能自动证明这些目标机门禁。
