# Kylin Candidate Pipeline Pause and Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Kylin amd64 候选流水线的已完成、未完成、恢复条件和跨平台接力关系写成可提交、可测试的长期 handoff，同时保持实现冻结。

**Architecture:** 在跨平台控制器 worktree 中新增 tracked handoff 和文档合同测试，不修改 `taiji-package`、控制器、Linux 打包实现或 `99/00/01`。原 Linux worktree继续作为 `a5a36849` 的只读实现现场。

**Tech Stack:** Markdown、Python 3.8+ `unittest`、Git

---

## 前置、禁止和停止条件

固定执行位置：

```text
/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller
```

- 允许修改：`docs/handoffs/2026-08-20-kylin-amd64-candidate-pipeline.md`、`docs/superpowers/plans/2026-08-19-kylin-amd64-candidate-pipeline.md`、`tests/test_kylin_candidate_handoff.py`。
- 唯一的 local-only 例外：允许用 `apply_patch` 只把暂停 worktree 的 `/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/kylin-amd64-candidate-pipeline/.codex/handoff.md` 缩短为 tracked handoff 指针。该文件仍被 ignore，不暂存、不提交；除此指针外，原 Linux worktree 保持只读。
- 禁止修改：`taiji-package`、`scripts/taiji-package-candidate.py`、`packaging/**`、`taijiagent 打包交付/**` 和现有测试。
- 禁止 SSH、`99/00/01`、制包、安装、push、PR、merge、Tag、Release。
- 若正式 `main`、暂停 worktree、跨平台 worktree的分支/HEAD/dirty 与本计划不符，停止；不得 reset、clean、rebase 或猜测新基线。
- 开始前 `git diff --name-only a5a36849..HEAD` 必须只包含本次 1 份 spec、1 份 index 和 5 份分计划；这 7 个文件由提交 `docs(packaging): split cross-platform pipeline execution plans` 引入。出现其他路径时停止。
- 本计划完成只证明“交接资料已固化”，不新增 Kylin 真机或候选 DEB 证据。

### Task 1: 重新绑定三个 Git 身份

**Files:**
- Inspect: `/Users/bwb/Documents/工作/taiji-agentv1.0`
- Inspect: `/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/kylin-amd64-candidate-pipeline`
- Inspect: `/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller`

- [ ] **Step 1: 核对正式 main**

```bash
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 status --porcelain=v2 --branch
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 rev-parse HEAD
```

Expected: branch 为 `main`，HEAD 为 `5364233e1297e5f2837382823d4e35a0d114aba7`，无文件项。

- [ ] **Step 2: 核对暂停的 Linux worktree**

```bash
git -C /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/kylin-amd64-candidate-pipeline status --porcelain=v2 --branch
git -C /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/kylin-amd64-candidate-pipeline rev-parse HEAD
```

Expected: branch 为 `codex/kylin-amd64-candidate-pipeline`，HEAD 为 `a5a36849bca009d1cfb07ac2309532a502c6bd70`，无文件项。

- [ ] **Step 3: 核对当前工作分支继承 Linux 基线**

```bash
git branch --show-current
git merge-base --is-ancestor a5a36849bca009d1cfb07ac2309532a502c6bd70 HEAD
git status --short --branch
```

Expected: branch 为 `codex/cross-platform-package-controller`，祖先检查退出 0，开始本 Task 前 worktree clean。

### Task 2: 用合同测试定义 durable handoff

**Files:**
- Create: `tests/test_kylin_candidate_handoff.py`
- Test: `tests/test_kylin_candidate_handoff.py`

- [ ] **Step 1: 创建失败测试**

```python
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "docs/handoffs/2026-08-20-kylin-amd64-candidate-pipeline.md"
PLAN = ROOT / "docs/superpowers/plans/2026-08-19-kylin-amd64-candidate-pipeline.md"


class KylinCandidateHandoffTests(unittest.TestCase):
    def test_handoff_binds_implementation_and_evidence_boundary(self):
        self.assertTrue(HANDOFF.is_file())
        text = HANDOFF.read_text(encoding="utf-8")
        for required in (
            "a5a36849bca009d1cfb07ac2309532a502c6bd70",
            "5364233e1297e5f2837382823d4e35a0d114aba7",
            "已实现，本地模拟通过",
            "真实麒麟连接未验证",
            "候选 DEB 未构建",
            "online doctor 未执行",
            "99/00/01 未执行",
        ):
            self.assertIn(required, text)

    def test_handoff_has_exact_resume_order_and_stop_boundary(self):
        self.assertTrue(HANDOFF.is_file())
        text = HANDOFF.read_text(encoding="utf-8")
        headings = (
            "## 目标", "## 冻结实现身份", "## 已完成", "## 未完成",
            "## 恢复前置条件", "## 精确恢复顺序", "## 跨平台接力",
            "## 证据边界", "## 状态卡",
        )
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        commands = (
            "git status --short --branch",
            "git rev-parse HEAD",
            "./taiji-package doctor",
            "./taiji-package plan",
            "./taiji-package doctor --online",
            "SSH 与传输",
            "依赖与网络",
            "候选构建",
            "./taiji-package build",
        )
        command_positions = [text.index(command) for command in commands]
        self.assertEqual(command_positions, sorted(command_positions))
        self.assertIn("主机不可达时必须在调用 99 前停止", text)

    def test_handoff_records_revised_single_repository_decision(self):
        self.assertTrue(HANDOFF.is_file())
        text = HANDOFF.read_text(encoding="utf-8")
        self.assertIn("codex/cross-platform-package-controller", text)
        self.assertIn("/Users/bwb/Documents/工作/taiji-agentv1.0", text)
        self.assertIn("旧 Windows 仓只作为有来源约束的迁移材料", text)
        self.assertNotIn("Windows 工作不得从 Linux 功能分支继续开发", text)

    def test_original_plan_no_longer_claims_it_is_unwritten(self):
        text = PLAN.read_text(encoding="utf-8")
        self.assertNotIn("当前处于 Plan Mode，尚未实际写入文件", text)
        self.assertIn("本地薄执行器已实现；真机阶段暂停", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行 RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_kylin_candidate_handoff
```

Expected: FAIL，因为 tracked handoff 尚不存在且旧计划仍声明“尚未实际写入”。若出现导入或语法错误，先修正测试；不得把它当作有效 RED。

### Task 3: 编写 tracked handoff 并纠正旧计划状态

**Files:**
- Create: `docs/handoffs/2026-08-20-kylin-amd64-candidate-pipeline.md`
- Modify: `docs/superpowers/plans/2026-08-19-kylin-amd64-candidate-pipeline.md:1-6`
- Modify local-only: `/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/kylin-amd64-candidate-pipeline/.codex/handoff.md`
- Test: `tests/test_kylin_candidate_handoff.py`

- [ ] **Step 1: 写 handoff 的固定事实**

handoff 必须逐项写明：

```text
实现基线：a5a36849bca009d1cfb07ac2309532a502c6bd70
正式 main 基线：5364233e1297e5f2837382823d4e35a0d114aba7
已完成：CLI、local doctor/plan、三件套合同、fake transport、FETCH_PENDING、本地测试
历史验证：71 个核心/传输/编排测试和 56 个 Skill/输入合同测试
未完成：online doctor、SSH、三件套真实生成、99/00/01、候选 DEB、安装、验收、签名、发布
恢复顺序：Git 身份 → local doctor → plan → 主机恢复后 online doctor → 三块专项授权 → build
跨平台接力：统一控制器在同一主仓开发；旧 Windows 仓只作为有来源约束的迁移材料
```

历史测试必须标注为绑定 `a5a36849` 的历史证据；执行 handoff 时的新身份检查单独标为实时证据。

- [ ] **Step 2: 替换旧计划顶部的陈旧状态**

把“尚未实际写入”的两行替换为：

```markdown
> **实施状态（2026-08-20）：** 本地薄执行器已实现；真机阶段暂停。
> 可恢复现场、已完成项、未完成项和接力顺序见 `docs/handoffs/2026-08-20-kylin-amd64-candidate-pipeline.md`。
```

- [ ] **Step 3: 运行 GREEN**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_kylin_candidate_handoff
```

Expected: `Ran 4 tests` 和 `OK`。

- [ ] **Step 4: 刷新暂停 worktree 的本地 handoff 指针**

使用 `apply_patch` 把本地 `.codex/handoff.md` 缩短为：Linux 实现冻结在 `a5a36849`、真机未验证、tracked handoff 绝对路径、统一控制器 branch/worktree、Windows 旧仓仅为迁移材料。该文件继续被 ignore，不加入任何 commit。

- [ ] **Step 5: 提交 GREEN handoff 与测试**

```bash
git add tests/test_kylin_candidate_handoff.py docs/handoffs/2026-08-20-kylin-amd64-candidate-pipeline.md docs/superpowers/plans/2026-08-19-kylin-amd64-candidate-pipeline.md
git commit -m "docs(packaging): checkpoint paused Kylin candidate pipeline"
```

### Task 4: 冻结范围并输出暂停状态卡

**Files:**
- Verify: `docs/handoffs/2026-08-20-kylin-amd64-candidate-pipeline.md`
- Verify: `tests/test_kylin_candidate_handoff.py`

- [ ] **Step 1: 证明没有实现文件变化**

```bash
git diff a5a36849bca009d1cfb07ac2309532a502c6bd70 -- taiji-package scripts/taiji-package-candidate.py packaging "taijiagent 打包交付"
```

Expected: 无输出。

- [ ] **Step 2: 跑 handoff 与既有模拟回归**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  tests.test_kylin_candidate_handoff \
  tests.test_taiji_package_candidate \
  tests.test_taiji_package_transport
git diff --check
```

Expected: unittest 为 `OK`，`git diff --check` 无输出。

- [ ] **Step 3: 核对 clean 并报告**

```bash
git status --short --branch
git log -2 --oneline
```

最终状态卡必须逐字包含：

```text
已实现，本地模拟通过
真实麒麟连接未验证
候选 DEB 未构建
Kylin 真机阶段已暂停，可按 tracked handoff 恢复
```
