# Taiji Cross-platform Package Controller Implementation Plan Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用五份可独立验收的计划，把已完成的 Kylin 本地控制器演进为同仓、同入口、双 adapter 的 Kylin/Windows 候选制包流水线。

**Architecture:** 统一 core 只负责 CLI、状态、锁、阶段和恢复；`kylin-amd64` 与 `windows-x64` adapter 分别负责平台输入、远程执行和制品验证。先冻结 Linux 交接并守住兼容性，再做 Windows fake 全链，最后把真实远程构建和旧仓退休放到独立人工门禁后。

**Tech Stack:** Python 3.8+、Bash、PowerShell 5.1、Git、SSH/SCP、Inno Setup、`unittest`

---

## 固定身份

```text
repository: /Users/bwb/Documents/工作/taiji-agentv1.0
branch: codex/cross-platform-package-controller
worktree: /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller
stacked baseline: a5a36849bca009d1cfb07ac2309532a502c6bd70
```

## 执行前总规则

- [ ] 阅读 `docs/superpowers/specs/2026-08-20-cross-platform-package-controller-design.md` 全文。
- [ ] 运行 `git status --short --branch`，要求当前分支与 worktree 身份匹配且 clean。
- [ ] 不执行 push、PR、merge、Tag、Release、安装、签名或发布。
- [ ] 计划 1—3 不 SSH、不准备真实输入、不制包、不删除旧仓。
- [ ] 计划 4 的 R1 只读检查、R2 产品导入、R3 正式 `main` 集成和 R4 候选构建分别等待主 Agent/操作员明确授权。
- [ ] 计划 5 在所有前置证据满足后仍须再次确认精确退休路径。
- [ ] 任一身份漂移、归属不明的 dirty 文件、测试基线失败或计划外修改立即停止。

## 五份计划及产物

| 顺序 | 计划 | 可独立验收的产物 | 执行门禁 |
| --- | --- | --- | --- |
| 1 | [`2026-08-20-kylin-pipeline-pause-handoff.md`](2026-08-20-kylin-pipeline-pause-handoff.md) | tracked Kylin handoff 与暂停状态合同 | 纯本地 |
| 2 | [`2026-08-20-package-core-kylin-adapter.md`](2026-08-20-package-core-kylin-adapter.md) | 通用 core、Kylin adapter、v1/v2 兼容 | 纯本地 |
| 3 | [`2026-08-20-windows-assets-fake-adapter.md`](2026-08-20-windows-assets-fake-adapter.md) | 有来源约束的 Windows 资产、fake 全链、统一 CLI | 纯本地 |
| 4 | [`2026-08-20-windows-product-import-real-candidate.md`](2026-08-20-windows-product-import-real-candidate.md) | 产品源码收敛与指定主机候选 EXE | R1/R2/R3/R4 四次独立授权 |
| 5 | [`2026-08-20-windows-repo-retirement.md`](2026-08-20-windows-repo-retirement.md) | 可恢复归档、旧仓退出、唯一仓复验 | 破坏性动作前人工授权 |

### Task 0: 从索引启动执行

- [ ] **Step 1: 核对计划包启动提交和固定身份**

```bash
git branch --show-current
git log -1 --format='%s'
git merge-base --is-ancestor a5a36849bca009d1cfb07ac2309532a502c6bd70 HEAD
git diff --name-only a5a36849bca009d1cfb07ac2309532a502c6bd70..HEAD
git status --short --branch
```

Expected: branch 为 `codex/cross-platform-package-controller`，最新提交 subject 精确为 `docs(packaging): split cross-platform pipeline execution plans`，祖先检查退出 0，diff 只有本索引列出的 1 份 spec、1 份 index 和 5 份分计划，worktree clean。这笔 docs-only 启动提交是执行计划的已存在前置；Task 0 不创建、补做或修改它。

- [ ] **Step 2: 只打开 Plan 1 并从 Task 1 开始**

```bash
sed -n '1,260p' docs/superpowers/plans/2026-08-20-kylin-pipeline-pause-handoff.md
```

Expected: 读到 Plan 1 的前置、禁止、停止条件和 Task 1。不得跳到 Plan 4 的远程动作或 Plan 5 的退休动作。

## 依赖关系

```text
Plan 1 ──> Plan 2 ──> Plan 3 ──> Plan 4 ──> Plan 5
  handoff      core       fake        real       retire
```

不得并行执行计划 2 与计划 3，因为两者都会改变 CLI 和 adapter 合同。计划 4 不得在计划 3 的最终本地回归提交产生前启动。计划 5 不得用历史 1.0.3 EXE 代替计划 4 的当前候选证据；计划 5 的退休工具集成正式 `main` 后会产生新 HEAD，必须回到计划 4 的 R4 再构建一次，候选 source commit 与该新 HEAD 完全一致后才允许物理处理旧仓。

## 每份计划的完成口径

- [ ] 所列 RED 测试确实因目标能力缺失而失败，不是语法或导入错误。
- [ ] 只做使 RED 变 GREEN 的最小修改。
- [ ] 每个计划中明确标出的 GREEN 提交组使用精确路径暂存和独立本地提交；只读、RED 和最终核验 Task 不单独提交。
- [ ] 聚焦测试、相关回归、语法门禁和 `git diff --check` 通过。
- [ ] 输出当前 commit、验证命令、真实结果和未验证项。
- [ ] 计划完成只提升它声明的证据层级。

## 模型分工

计划 1—3 可以交给低级实现模型逐 Task 执行，但主 Agent必须在每个 GREEN commit 后做规格符合性和代码质量两次审查。计划 4—5 的人工门禁、真实远程命令、来源差异判断、制品身份结论和退休动作只能由主 Agent处理；低级模型只可执行门禁前已经完全锁死的本地测试或文档步骤。

## 总体验收命令

在计划 3 完成后、计划 4 授权前运行：

```bash
bash -n taiji-package
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q \
  tests.test_kylin_candidate_handoff \
  tests.test_taiji_package_core_boundaries \
  tests.test_taiji_package_orchestration \
  tests.test_windows_legacy_asset_provenance \
  tests.test_windows_packaging_script_contract \
  tests.test_taiji_package_candidate \
  tests.test_taiji_package_transport \
  tests.test_taiji_package_state_v2 \
  tests.test_taiji_package_target_dispatch \
  tests.test_taiji_package_windows_adapter \
  tests.test_taiji_package_windows_transport \
  tests.test_linux_golden_orchestrator
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q \
  tests.test_taiji_kylin_packaging_skill \
  tests.test_builder_input_package_contract
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/python38_linux_packaging_gate.py
git diff --check
```

预期：shell/Python 命令退出 0，unittest 输出 `OK`，`git diff --check` 无输出。该结果只证明本地控制器和模拟适配器，不证明任一真实候选已构建。

## 总停止条件

- 需要修改 Linux `99/00/01` 核心才能完成通用抽取。
- Windows adapter 试图调用旧 sibling 仓运行时文件。
- Windows 缓存或工具缺失但实现准备联网下载或自动安装。
- `fetch` 会再次准备输入、传输输入或运行构建。
- 计划外产品行为、安装、授权、签名或发布进入 diff。
- Windows 候选来源不是当时 clean、已复验的正式 `main` HEAD。
- 旧 Windows 仓在计划 5 的归档和再次确认前被删除或清理。
