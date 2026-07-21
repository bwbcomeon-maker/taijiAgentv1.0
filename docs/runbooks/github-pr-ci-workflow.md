# 单人开发的 GitHub、PR 与 CI 流程

目标只有三个：`main` 始终可发布、改动可审查可回滚、验证成本随风险增长。

## 日常流程

```bash
git switch main
git pull --ff-only
git worktree add .worktrees/<task> -b codex/<task> main

# 在 worktree 中修改、执行相关本地测试
git add <明确的文件>
git commit -m "type: concise purpose"
git push -u origin codex/<task>
```

随后在 GitHub 创建 PR。`CI Gate` 通过后 squash merge；回到正式目录：

```bash
git switch main
git pull --ff-only
git status --short
git worktree remove .worktrees/<task>
git branch -d codex/<task>
```

删除前必须确认 PR 已合并、worktree 干净、没有相关测试/构建进程。

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

- CI 失败：保留分支，修复后重新推送；不合并红灯。
- 合并后发现问题：优先 `git revert <commit>` 创建可审计回滚，不改写历史。
- GitHub 不可用：停止发布；先创建带日期的本地标签和外部 bundle，待恢复后
  补齐远端同步、PR/CI 记录。
