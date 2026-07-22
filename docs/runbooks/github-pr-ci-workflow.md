# 单人开发的 GitHub、PR 与 CI 流程

本文是 [`development-lifecycle.md`](./development-lifecycle.md) 的 supporting runbook，只提供 GitHub、PR 与 CI 的动态执行细节。所有操作受 canonical 规范约束；若有冲突，以 `development-lifecycle.md` 为准。

目标只有三个：`main` 始终可发布、改动可审查可回滚、验证成本随风险增长。

## 日常流程

```bash
git switch main
git pull --ff-only
git worktree add .worktrees/<task> -b codex/<task> main

# 在 worktree 中修改、执行相关本地测试
git add <明确的文件>
git commit -m "type: concise purpose"
```

默认停在本地提交。只有用户明确要求“提交 PR”时，才执行：

```bash
git push -u origin codex/<task>
# 创建 Draft 或 Ready PR；状态取决于验收是否完成
```

只有适用本地验证、审查准备和用户验收均已有通过证据时才创建 Ready PR；状态未知或仍有未完成项时默认创建 Draft PR。

提交 PR 默认停在 PR，不因 `CI Gate` 通过而自动合并。正常流程中，只有用户另行或预先明确授权合并，且 canonical 规范中的本地验证、审查/验收、依赖顺序和 `CI Gate` 全部满足后，才按约定方式 merge；紧急绕过只能按本文“失败与恢复”和 canonical 规范的例外条件处理。

## 合并后同步与安全清理

合并后先在正式根目录同步，不在默认命令块机械删除 worktree 或分支：

```bash
git switch main
git pull --ff-only
git status --short
```

随后按顺序完成：

1. 记录正式 `main` commit，以祖先关系证明其包含分支提交；squash/rebase 场景按下述 exact path/tree/blob/mode proof 或逐路径人工审查证明成果已被吸收。
2. 从正式入口执行非破坏性复验，并核对源码、进程、runtime/config 来源。
3. 按 canonical 规范审计 refs、全部 worktree、未提交/未跟踪内容、仅分支提交、reflog/悬空提交、关联进程和运行/发布目录。
4. 确认 worktree 干净、关联进程退出和备份闭合后，移除 worktree。
5. 按实际合并方式删除分支：merge/fast-forward 后，只有祖先关系成立才使用 `git branch -d`；squash merge 后，原提交通常不是 `main` 的祖先，按下述精确证明和备份门禁处理。不得把 `-d` 失败机械升级为 `-D`。

### Squash 自动等价证明

先记录 branch base、branch tip 和 GitHub 返回的 squash commit。`<branch-base>` 必须来自创建分支时的状态卡/基线记录，或已审计的 PR 差异基线，且能证明覆盖本成果的完整起点；不得临时选择分支中途 commit。随后确认 branch base 是 branch tip 的祖先，且 squash commit 是正式 `main` 的祖先：

```bash
git merge-base --is-ancestor <branch-base> <branch-tip>
git merge-base --is-ancestor <squash-commit> main
git diff --name-status --no-renames <branch-base> <branch-tip>
git diff --name-status --no-renames <squash-commit>^ <squash-commit>
```

只有两条 `--name-status --no-renames` 结果中的路径集合和每个路径状态完全一致，才继续自动比较；`--no-renames` 必须把重命名展开为删除和新增。对两侧变更涉及的每一个路径执行：

```bash
git ls-tree <branch-tip> -- '<path>'
git ls-tree <squash-commit> -- '<path>'
```

两条树项输出必须逐字节一致，从而同时证明路径存在性、对象类型、文件模式/执行位以及 blob 或子模块对象 ID 一致；删除路径必须在两侧都无树项输出。只有所有路径均满足上述条件，且 squash commit 是正式 `main` 的祖先，自动等价证明才通过。

`git patch-id --stable` 会忽略部分空白差异，最多用于辅助筛查可疑变化；即使 patch ID 相同，也不能授权“已完成”声明、分支/worktree 删除或任何清理。任一路径/状态/树项不一致、branch base 来源无法证明、发生 rebase/基线漂移、冲突解决，或无法自动比较时，必须停止删除，逐路径记录人工等价审查并重跑受影响测试。无论自动证明还是人工审查，正式入口复验、完整清理审计以及指向原 branch tip 的 `refs/backup/<task>-<YYYYMMDD>` 或等价备份都必须闭合，之后才允许显式强制删除本地 squash 分支。

## 自动测试分级

| 等级 | 典型改动 | 自动验证 |
| --- | --- | --- |
| 文档快车道 | `README.md`、`docs/**` | 分类器测试、补丁完整性 |
| 普通改动 | 单一模块代码 | 根合同 + 受影响模块 |
| 高风险 | CI、依赖锁、认证/凭据、Provider、授权、迁移、打包、发布 | 全部自动化套件 |

未知路径至少运行根合同；`full-ci` 标签强制高风险模式。分类器只允许
自动升级验证范围，不接受“跳过 CI”标签。

## 自动化与人工门禁边界

- PR 自动化：纯净 Linux runner 上的根合同、Desktop 静态/Node 测试、
  DOCX Node 测试、Agent/WebUI 聚焦回归。
- 发布前人工/目标环境：正式根目录的真实 Electron、真实 OAuth/Provider、
  WPS/Word 文档终验、Kylin/UOS 离线安装和目标机证据。

## 失败与恢复

- 代码或测试导致 CI 失败：保留分支，修复后重新执行适用本地验证并推送；不合并红灯。
- 日志明确指向 runner 启动/丢失、GitHub 服务事件、网络/依赖源不可用，或测试尚未执行即中断时：由当前任务负责人记录证据，同一 commit 最多重跑一次。断言失败、编译错误、可稳定复现的超时或拿不准的失败不属于偶发故障。相同问题重复失败后停止重跑并登记 CI 基础设施排查；修复后仍须取得成功的 `CI Gate`，不得把失败视为绿灯或合并。
- 合并后发现问题：优先 `git revert <commit>` 创建可审计回滚，不改写历史。
- GitHub 不可用：默认停止 push、PR、合并和发布。只有用户本人依据明确截止时间或业务影响判定紧急，并授权具体绕过动作与对象，且 canonical 规范要求的备份、验证、回滚和记录均闭合，才可临时绕过；默认只允许本地 `main` 整合，禁止直接推远端 `main`，制包/目标机/持久服务/发布须另行授权。不得宣称 CI 已通过。
- GitHub 恢复后的优先路径：推送包含紧急整合 commit 的功能/审计分支，补建 PR 并运行 CI；使用保留该 commit 为祖先的 merge commit 合并，使远端 `main` 成为本地紧急 `main` 的后代，随后本地才能 `git pull --ff-only` 并正式复验。
- 若仓库只允许 squash/rebase，或远端历史已无法保留该祖先关系，立即停止自动同步。为本地紧急 `main` 建立 `refs/backup/` 和 bundle，从 `origin/main` 新建专用 reconciliation 分支/worktree，在其中重放/核对成果并重新走 PR/CI。补丁相似不能当作 fast-forward 证明；只有远端成果已通过上述 exact path/tree/blob/mode proof，或完成逐路径人工等价审查与受影响测试，且正式复验通过、备份闭合、用户明确授权重对齐本地 `main` 后，才可执行经审计的非快进重对齐。不得自动 `reset`；补审计失败则停止后续发布并按回滚边界处理。
