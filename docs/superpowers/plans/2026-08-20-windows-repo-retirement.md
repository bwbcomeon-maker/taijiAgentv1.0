# Windows Legacy Repository Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用已经集成正式 `main` 的静态退休工具审计、归档并实际恢复旧 Windows 仓；只有当前候选绑定当时正式 `main` HEAD 后，才在两次独立确认下移除 linked worktree 并把旧根仓原子移动到同卷废纸篓。

**Architecture:** Task 1—3 在独立退休分支中只实现静态 policy、运行时审计/归档 CLI、测试和通用 runbook，再走标准集成；policy 不保存会漂移的 candidate、main、archive 或本机路径。正式 main 复验后，主 Agent 用 CLI 显式传入实时身份，创建 mode 0700、无覆盖的全 refs bundle，并通过 mirror restore 证明可恢复。物理处理与代码开发完全分离：候选 commit 必须等于正式 main HEAD，phase-a 和旧根仓分别授权，根仓只做同卷 rename，并提前写出可执行回滚计划。

**Tech Stack:** Python 3.8+、Git bundle/mirror/worktree、JSON、`unittest`

---

## 分支拓扑、硬前置和禁止项

Task 1—3 固定在新分支和 worktree：

```text
branch: codex/windows-legacy-retirement
baseline: 执行时已完成 Plan 4 R3/R4 的正式 main HEAD
worktree: /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/windows-legacy-retirement
```

创建前必须核正式根目录是 clean `main`，再按 `using-git-worktrees` 从该精确 HEAD 创建；不得复用跨平台功能分支继续写退休代码。Task 1—3 完成后必须按 `docs/runbooks/development-lifecycle.md` 经单独授权的标准 push/PR/CI/merge 集成和正式 main 复验；在此之前禁止任何物理处理。

创建前按顺序运行并记录完整 HEAD：

```bash
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 status --porcelain=v2 --branch
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 rev-parse HEAD
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 worktree list --porcelain
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 check-ignore -q .worktrees
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 worktree add /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/windows-legacy-retirement -b codex/windows-legacy-retirement <recorded-formal-main-head>
```

`<recorded-formal-main-head>` 必须替换为刚展示并确认的 40 位字面量，不能原样执行。若 branch/path 已存在、main dirty 或 worktree 清单有归属不明项，停止，不复用、不删除、不 force。

开始 Task 1 还要求：

- Plan 4 已至少产生一个当前 Windows 候选，fake 全链和 Kylin 回归通过。
- Windows 产品源码、adapter、脚本、target 和 asset provenance 已进入唯一仓库；`product-import.json` 仍只存在于 `/Users/bwb/.local/state/taiji-package/imports/<import-id>/`，仓库内仅有 `docs/reviews/2026-08-20-windows-product-source-import.md` 及其中绑定的 manifest SHA，退休 CLI 必须接收该私有绝对路径并交叉验证。
- 旧仓仍为 `/Users/bwb/Documents/工作/taiji-agentv1.0-win`，common dir 为其 `.git`；已知 root tip=`f33663f7e3ffee672d39af7b4ecbe9fd2869a00b`、phase-a tip=`e4102f82798cafca664f128d0cab88cf0ab8ff41`、main tip=`ae1c02d7c59b27e6c23506396013026be980b6f7`，另有下文 policy 锁定的两条 Codex checkpoint ref。runtime audit 必须重新读取并逐字核对全部五条 ref；不得忽略或删除 checkpoint ref。

禁止 `rm -rf`、`git reset --hard`、`git clean`、force worktree removal、删除 branch/ref/reflog、删除 bundle、自动清理 `.DS_Store`、自动清理远端 `D:\tw`，以及把历史 1.0.3 当作当前 gate。任一实时证据缺失输出 `RETIREMENT_BLOCKED` 并停止。

## 静态 policy 与运行时事实边界

`packaging/windows/legacy-retirement-policy.json` 只能保存：

```text
schema
expected source branch/ref 名称和历史 tip
14 个 f33663f tracked path 的固定 disposition
phase-a 参考资产分类
允许的 untracked path 精确列表
禁止的运行时路径/literal
必需 evidence 类型；archive 不得由工具自动删除
```

policy 禁止保存：candidate run-id/commit/path、正式 main HEAD、archive path/SHA、Trash destination、操作员用户名、当前时间或当前 worktree 列表。上述事实必须通过 CLI 的显式参数和实时 probe 输入；不得从 sibling 目录扫描猜测默认值。

phase-a 的运行时状态只允许以下两个精确值，并由调用方显式选择，auditor 不自行猜测：

```text
PRESENT_CLEAN
  旧根仓和 /private/tmp/taijiagent-windows-packaging-phase-a 都在 git worktree list；
  phase-a worktree clean，branch=codex/phase-a-foundation，HEAD=e4102f82798cafca664f128d0cab88cf0ab8ff41。

REMOVED_REF_PRESERVED
  phase-a path 不存在且未出现在 git worktree list；
  refs/heads/codex/phase-a-foundation 仍精确指向 e4102f82798cafca664f128d0cab88cf0ab8ff41，archive inventory 和 mirror 也包含同一 ref/tip。
```

Task 4 的退休审计只接受 `PRESENT_CLEAN`；Task 5 移除并复核后，Task 6 的审计和 `plan-move` 只接受 `REMOVED_REF_PRESERVED`。任何第三种组合（path 消失但仍注册、path 存在但未注册、dirty、ref 漂移或多余 worktree）都返回 blocker。

### Task 1: 在独立退休分支实现静态 policy 与纯审计核

**Files:**
- Create: `packaging/windows/legacy-retirement-policy.json`
- Create: `scripts/audit_windows_legacy_retirement.py`
- Create: `tests/test_windows_legacy_retirement.py`
- Create: `docs/reviews/2026-08-20-windows-legacy-asset-disposition.md`

- [ ] **Step 1: 写无未定义 fixture 的 RED**

测试文件必须直接定义以下 helper，不允许 `self.fixture_policy()` 或未给实现的 fixture 名称：

```python
import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / "scripts/audit_windows_legacy_retirement.py"


def load_auditor(testcase):
    testcase.assertTrue(AUDITOR.is_file())
    spec = importlib.util.spec_from_file_location("legacy_retirement", str(AUDITOR))
    testcase.assertIsNotNone(spec)
    testcase.assertIsNotNone(spec.loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def static_policy():
    return {
        "schema": "taiji-windows-legacy-retirement-policy/v1",
        "expected_refs": {
            "refs/heads/main": "ae1c02d7c59b27e6c23506396013026be980b6f7",
            "refs/heads/codex/windows-fast-track": "f33663f7e3ffee672d39af7b4ecbe9fd2869a00b",
            "refs/heads/codex/phase-a-foundation": "e4102f82798cafca664f128d0cab88cf0ab8ff41",
            "refs/codex/turn-diffs/checkpoints/3a871434735be1ae40c4a189de26032ea4a4eac2d26d3edcd61b422aa7a429a3/1caa59a3d1338d34e81dc036a261921561c71e53ddc1ab1c8539e3ce8d943933/1787128662573/ed5886ba-2464-4ef2-956a-588a29a13e5a": "4404b6320f7d53ad2d68556d6b58b983c0147b56",
            "refs/codex/turn-diffs/checkpoints/d522a099a28c9a8ae63216aad470cb77c93549958033fde744cfa997db8d9572/d77e565745cd3a3e14222d757549264102326e1b7918befeb02536260ebb4dce/1787191393030/27ee63f1-d750-4ad0-8e93-1a9f24f6e2a3": "507965a41c115c652bf1a2355c9ebb3116716683",
        },
        "auxiliary_ref_dispositions": {
            "refs/codex/turn-diffs/checkpoints/3a871434735be1ae40c4a189de26032ea4a4eac2d26d3edcd61b422aa7a429a3/1caa59a3d1338d34e81dc036a261921561c71e53ddc1ab1c8539e3ce8d943933/1787128662573/ed5886ba-2464-4ef2-956a-588a29a13e5a": "reference-only-archive",
            "refs/codex/turn-diffs/checkpoints/d522a099a28c9a8ae63216aad470cb77c93549958033fde744cfa997db8d9572/d77e565745cd3a3e14222d757549264102326e1b7918befeb02536260ebb4dce/1787191393030/27ee63f1-d750-4ad0-8e93-1a9f24f6e2a3": "reference-only-archive",
        },
        "tracked_dispositions": {
            "docs/handoffs/2026-08-20-windows-packaging-agent-handoff.md": "reference-only",
            "docs/reviews/2026-08-19-windows-fast-track-retrospective.md": "reference-only",
            "docs/runbooks/windows-packaging-fast-path.md": "reference-only",
            "docs/runbooks/windows-user-acceptance.md": "reference-only",
            "docs/superpowers/plans/2026-08-18-taijiagent-windows-acceptance-release.md": "rejected-wholesale",
            "docs/superpowers/plans/2026-08-18-taijiagent-windows-builder-installer.md": "reference-only",
            "docs/superpowers/plans/2026-08-18-taijiagent-windows-foundation.md": "reference-only",
            "docs/superpowers/plans/2026-08-18-taijiagent-windows-packaging-implementation-index.md": "reference-only",
            "docs/superpowers/plans/2026-08-19-taijiagent-windows-fast-track.md": "reference-only",
            "docs/superpowers/specs/2026-08-18-taijiagent-windows-packaging-design.md": "reference-only",
            "docs/superpowers/specs/2026-08-19-taijiagent-windows-fast-track-design.md": "reference-only",
            "installer/TaijiAgent.iss": "migrated",
            "scripts/windows/Initialize-FastTrackSession.ps1": "migrated",
            "scripts/windows/Stage-WindowsPayload.ps1": "migrated",
        },
        "phase_a_dispositions": {
            ".gitignore": "reference-only-not-imported",
            "AGENTS.md": "reference-only-not-imported",
            "README.md": "reference-only-not-imported",
            "docs/superpowers/plans/2026-08-18-taijiagent-windows-acceptance-release.md": "reference-only-not-imported",
            "docs/superpowers/plans/2026-08-18-taijiagent-windows-builder-installer.md": "reference-only-not-imported",
            "docs/superpowers/plans/2026-08-18-taijiagent-windows-foundation.md": "reference-only-not-imported",
            "docs/superpowers/plans/2026-08-18-taijiagent-windows-packaging-implementation-index.md": "reference-only-not-imported",
            "docs/superpowers/specs/2026-08-18-taijiagent-windows-packaging-design.md": "reference-only-not-imported",
            "locks/control-git.json": "rejected-wholesale",
            "locks/control-lock-tool.json": "rejected-wholesale",
            "locks/control-python.json": "rejected-wholesale",
            "locks/control-requirements.txt": "rejected-wholesale",
            "policies/source-policy.json": "rejected-wholesale",
            "pyproject.toml": "rejected-wholesale",
            "requirements/control.in": "rejected-wholesale",
            "src/taiji_windows_packaging/__init__.py": "reference-only-not-imported",
            "src/taiji_windows_packaging/canonical_json.py": "reference-only-not-imported",
            "src/taiji_windows_packaging/domain.py": "reference-only-not-imported",
            "src/taiji_windows_packaging/errors.py": "reference-only-not-imported",
            "tests/unit/test_canonical_json.py": "reference-only-not-imported",
            "tests/unit/test_domain.py": "reference-only-not-imported",
            "tests/unit/test_package_import.py": "reference-only-not-imported",
        },
        "allowed_untracked": ["docs/.DS_Store"],
        "required_evidence_schemas": [
            "taiji-windows-legacy-asset-provenance/v1",
            "taiji-windows-product-import/v1",
            "taiji-package-run-state/v2",
            "taiji-windows-legacy-archive-inventory/v1",
            "taiji-windows-legacy-mirror-verification/v1",
        ],
        "forbidden_runtime_literals": [
            "/Users/bwb/Documents/工作/taiji-agentv1.0-win",
            r"D:\tw\payload", r"D:\tw\out", r"D:\tw\logs", r"D:\tw\packaging",
        ],
        "archive_auto_delete": False,
    }


def ready_runtime():
    return {
        "legacy": {
            "git_top_level": "/legacy",
            "common_dir": "/legacy/.git",
            "refs": copy.deepcopy(static_policy()["expected_refs"]),
            "tracked_paths": sorted(static_policy()["tracked_dispositions"]),
            "untracked_paths": ["docs/.DS_Store"],
            "worktrees": [
                {"path": "/legacy", "branch": "codex/windows-fast-track", "head": "f33663f7e3ffee672d39af7b4ecbe9fd2869a00b", "clean": False, "allowed_untracked": ["docs/.DS_Store"]},
                {"path": "/private/tmp/taijiagent-windows-packaging-phase-a", "branch": "codex/phase-a-foundation", "head": "e4102f82798cafca664f128d0cab88cf0ab8ff41", "clean": True, "allowed_untracked": []},
            ],
            "phase_a_status": "PRESENT_CLEAN",
            "remotes": [],
        },
        "formal": {"branch": "main", "clean": True, "head": "a" * 40},
        "candidate": {"schema": "taiji-package-run-state/v2", "target_id": "windows-x64", "stage": "CANDIDATE_BUILT", "status_label": "候选 EXE 已构建", "source": {"branch": "main", "commit": "a" * 40}, "artifact": {"kind": "exe", "sha256": "b" * 64}},
        "asset_provenance": {"schema": "taiji-windows-legacy-asset-provenance/v1", "sha256": "4" * 64, "verified": True},
        "product_import": {"schema": "taiji-windows-product-import/v1", "path": "/private-state/product-import.json", "sha256": "5" * 64, "review_recorded_sha256": "5" * 64, "verified": True},
        "archive": None,
        "runtime_dependency_hits": [],
    }


class WindowsLegacyRetirementTests(unittest.TestCase):
    def test_ready_inputs_are_blocked_until_archive_is_verified(self):
        result = load_auditor(self).audit_evidence(static_policy(), ready_runtime(), "PRESENT_CLEAN")
        self.assertEqual(result["status"], "RETIREMENT_BLOCKED")
        self.assertEqual(result["blockers"], ["ARCHIVE_NOT_VERIFIED"])

    def test_candidate_must_equal_formal_main_head(self):
        runtime = ready_runtime()
        runtime["candidate"]["source"]["commit"] = "c" * 40
        result = load_auditor(self).audit_evidence(static_policy(), runtime, "PRESENT_CLEAN")
        self.assertIn("CANDIDATE_NOT_FORMAL_MAIN_HEAD", result["blockers"])

    def test_unclassified_path_blocks(self):
        runtime = ready_runtime()
        runtime["legacy"]["tracked_paths"].append("unknown.txt")
        result = load_auditor(self).audit_evidence(static_policy(), runtime, "PRESENT_CLEAN")
        self.assertIn("UNCLASSIFIED_TRACKED_PATH", result["blockers"])

    def test_phase_a_state_must_match_the_explicit_pre_or_post_gate(self):
        module = load_auditor(self)
        before = ready_runtime()
        self.assertNotIn("PHASE_A_STATE_MISMATCH", module.audit_evidence(static_policy(), before, "PRESENT_CLEAN")["blockers"])
        self.assertIn("PHASE_A_STATE_MISMATCH", module.audit_evidence(static_policy(), before, "REMOVED_REF_PRESERVED")["blockers"])

        after = ready_runtime()
        after["legacy"]["worktrees"] = [after["legacy"]["worktrees"][0]]
        after["legacy"]["phase_a_status"] = "REMOVED_REF_PRESERVED"
        self.assertNotIn("PHASE_A_STATE_MISMATCH", module.audit_evidence(static_policy(), after, "REMOVED_REF_PRESERVED")["blockers"])
        self.assertIn("PHASE_A_STATE_MISMATCH", module.audit_evidence(static_policy(), after, "PRESENT_CLEAN")["blockers"])

    def test_all_live_refs_including_checkpoint_refs_are_classified(self):
        policy = static_policy()
        checkpoint_refs = {
            ref for ref in policy["expected_refs"]
            if ref.startswith("refs/codex/turn-diffs/checkpoints/")
        }
        self.assertEqual(checkpoint_refs, set(policy["auxiliary_ref_dispositions"]))
        self.assertEqual(len(checkpoint_refs), 2)

    def test_product_import_must_match_tracked_review_sha(self):
        runtime = ready_runtime()
        runtime["product_import"]["review_recorded_sha256"] = "6" * 64
        result = load_auditor(self).audit_evidence(static_policy(), runtime, "PRESENT_CLEAN")
        self.assertIn("PRODUCT_IMPORT_REVIEW_SHA_MISMATCH", result["blockers"])
```

真实 policy 把已核验的 14 个 tracked path 全部逐项列出，不用示例缩写：三个脚本/ISS=`migrated`；fast-path runbook、acceptance、handoff、review、旧 specs/plans=`reference-only`；phase-a domain/errors/canonical JSON 思想=`reference-only-not-imported`；Python 3.11、完整认证/客户发布和整分支方案=`rejected-wholesale`；`docs/.DS_Store`=`untracked-rejected-local-metadata`。两条 checkpoint ref 按 exact name/tip 分类为 `reference-only-archive`，只进入 `git bundle --all` 与 mirror 验证，不删除、不改名。

- [ ] **Step 2: 运行 RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_legacy_retirement
```

Expected: FAIL（AssertionFailure），module/policy 不存在；不得把 ImportError 或 syntax error 当 RED。

- [ ] **Step 3: 实现 `audit_evidence(policy, runtime, expected_phase_a)`**

纯函数只接收两个 dict，不读文件、不调用 Git、不使用当前工作目录。blocker 排序固定为字典序；全部满足且 archive 已验证时返回：

```json
{"schema":"taiji-windows-legacy-retirement-audit/v1","status":"RETIRE_READY","blockers":[],"unclassified_paths":[]}
```

必须验证：policy exact schema；每个 tracked/untracked path 有且仅有一个处置；三条 heads 与两条 checkpoint ref 的 name/tip/disposition 全相等，少、多、漂移都阻断；无 remote；显式 phase-a gate 与实时 worktree/ref/path 状态完全一致；候选为 v2 Windows `CANDIDATE_BUILT`；candidate `source.branch=main` 且 commit 等于 formal clean main HEAD；artifact kind/SHA；asset provenance；私有 product import manifest 的 schema/exact bytes SHA 与正式 main tracked import review 中唯一 `product-import-manifest-sha256:` 行相等；archive bundle/sidecar/mirror 全五 refs 已验证；生产代码、target、入口和 runbook 无旧仓运行时依赖。

fake Windows 全链和 Kylin 回归仍是进入本计划以及 Task 3/Task 4 的命令门禁，但不作为 auditor 的动态 JSON 输入：它们由当轮实际测试输出和 controller log 证明。`RETIRE_READY` 的纯函数不解析一份容易过期、也未定义生成者的“测试 evidence 文件”。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_legacy_retirement
git add packaging/windows/legacy-retirement-policy.json scripts/audit_windows_legacy_retirement.py tests/test_windows_legacy_retirement.py docs/reviews/2026-08-20-windows-legacy-asset-disposition.md
git commit -m "feat(packaging): define static Windows retirement policy"
```

Expected: `OK`。本 Task 不读取或改变旧仓。

### Task 2: 实现显式 runtime CLI 和安全全 refs archive/mirror restore

**Files:**
- Modify: `scripts/audit_windows_legacy_retirement.py`
- Modify: `tests/test_windows_legacy_retirement.py`

CLI 只提供：

```text
audit           读取显式 repo/evidence/archive 参数并输出 RETIREMENT_BLOCKED/RETIRE_READY
archive         创建不存在的私有 archive dir、全 refs bundle、sidecar、inventory
verify-archive  git bundle verify + mirror clone + exact refs/blob/mode/hash 对账
reverify-archive 只读重核已存 bundle/sidecar/inventory/mirror/verification，不新建或改写文件
plan-move       只验证同卷/source/destination 并写 rollback plan，不移动
move            要求精确 confirmation 后只做一次同卷 os.rename
```

所有子命令必须要求 `--policy`；`audit/archive/verify-archive` 还要求显式 `--legacy-repo --formal-repo --candidate-state --asset-provenance --product-import-manifest --archive-dir`。`audit` 额外强制 `--expected-phase-a {PRESENT_CLEAN,REMOVED_REF_PRESERVED}`；参数缺失或其他值在读取 Git/文件前拒绝。`reverify-archive` 只再要求显式 `--archive-dir`，restore path 必须从已验证且 mode/owner/link-count 合法的 `mirror-verification.json` 读取，不接受命令行替换。不得从 policy 推导这些 runtime path。

- [ ] **Step 1: 写实际临时 Git repo archive RED**

在测试文件内完整定义：

```python
import os
from pathlib import Path
import subprocess


def git(repo, *args):
    return subprocess.run(["/usr/bin/git", "-C", str(repo)] + list(args), check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def make_legacy_repo(root):
    repo = root / "legacy"
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "docs").mkdir()
    (repo / "docs/example.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", "docs/example.md")
    git(repo, "commit", "-q", "-m", "fixture")
    tip = git(repo, "rev-parse", "HEAD")
    git(repo, "branch", "codex/windows-fast-track", tip)
    git(repo, "branch", "codex/phase-a-foundation", tip)
    return repo, tip
```

测试调用 `archive_repository()`、`verify_archive()` 和 `reverify_existing_archive()`，断言：archive dir 原先必须不存在；创建后 mode `0700`；bundle/sidecar/inventory/mirror-verification 均为 owned regular non-symlink、nlink=1、mode `0600`；第二次 archive/initial verify 调用拒绝；`reverify_existing_archive()` 对已存 mirror 连续调用两次均只读成功且全部文件 bytes/mtime 不变；sidecar 错、bundle 错、少 ref、额外 ref、mirror blob/mode 不同均失败；源 repo 不变。

另加 `test_auditor_help_runs_isolated_from_external_cwd`：从临时非仓库 cwd 用 `/usr/bin/python3 -I -B <absolute audit_windows_legacy_retirement.py> --help`，断言 exit 0、无 `ModuleNotFoundError`、cwd 无新增文件。auditor 若复用仓库模块，只能从自身绝对路径 bootstrap 精确 repo root 并核实际 import 来源。

- [ ] **Step 2: 实现无覆盖 archive**

`archive_repository()` 使用显式绝对路径，拒绝 source/archive parent symlink。state root、`legacy-archives` 父目录不存在时逐级以 mode `0700` 创建；已存在时逐级 `lstat`，要求当前用户所有、directory、非 symlink 且无 group/other 权限。最终 archive dir 以 `os.mkdir(path, 0o700)` 独占创建；临时 umask `077`，使用参数数组执行：

```text
/usr/bin/git -C <legacy-repo> bundle create <archive-dir>/taiji-agentv1.0-win.bundle --all
```

创建后逐个 `lstat`，文件 chmod `0600`；sidecar exact 为 `<sha256>  taiji-agentv1.0-win.bundle\n`；inventory schema 固定为 `taiji-windows-legacy-archive-inventory/v1`，以 O_EXCL 临时文件、fsync、`os.replace` 原子写，绑定全部 refs、HEAD、worktrees、dirty 分类、bundle basename/bytes/SHA、asset provenance SHA、candidate state SHA、创建时间和 `auto_delete=false`。任何已有 archive dir/file 都停止，不覆盖、不自动选新名字；工具不提供删除 archive 的子命令。

- [ ] **Step 3: 实现 mirror restore 证明**

`verify_archive()` 先核 sidecar 和 `git bundle verify`，再要求显式 restore path 不存在并执行：

```text
/usr/bin/git clone --mirror <bundle> <restore-path>
```

用 `git for-each-ref --format=%(refname)%00%(objectname)` 比较 source inventory 与 mirror 的全部 refs，而不是假设普通 clone 会创建本地 branches；再从 mirror 核三个迁移 blob 的 type/mode/SHA。成功后以 O_EXCL、mode `0600` 新建 `mirror-verification.json`，schema 固定为 `taiji-windows-legacy-mirror-verification/v1`，绑定 restore path、refs SHA 和 verified_at；不得改写 bundle、sidecar 或 inventory。失败保留 bundle 和 restore 现场。

`reverify_existing_archive()` 只接受已存在 archive dir 和 `mirror-verification.json` 中绑定的 restore path；先核五个现有对象的 owner/type/link-count/mode，再核 sidecar、bundle verify、inventory SHA、mirror 全 refs、三个 blob/mode/SHA 和原 verification 中的 refs SHA。它不接受新 restore path，不 clone，不写 `verified_at`，不更新任何文件；用于后续物理动作前的可重入只读门禁。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_legacy_retirement
git add scripts/audit_windows_legacy_retirement.py tests/test_windows_legacy_retirement.py
git commit -m "feat(packaging): archive and restore legacy Windows refs"
```

### Task 3: 锁死零运行时依赖、同卷移动计划和标准集成门禁

**Files:**
- Modify: `scripts/audit_windows_legacy_retirement.py`
- Modify: `tests/test_windows_legacy_retirement.py`
- Modify: `tests/python38_linux_packaging_gate.py`
- Modify: `docs/runbooks/taiji-windows-candidate-pipeline.md`
- Create: `docs/runbooks/taiji-windows-legacy-retirement.md`

- [ ] **Step 1: 写 runtime dependency 与 move-plan RED**

测试精确扫描生产代码、target、入口和 `docs/runbooks/taiji-windows-candidate-pipeline.md`，禁止候选运行时引用：

```text
/Users/bwb/Documents/工作/taiji-agentv1.0-win
D:\tw\payload
D:\tw\out
D:\tw\logs
D:\tw\packaging
```

只允许 provenance、asset disposition、archive review、retired evidence 和专用 `docs/runbooks/taiji-windows-legacy-retirement.md` 引用旧绝对路径；该专用 runbook 不得被 `taiji-package` 或候选 runbook 导入为运行依赖。

`plan_move(source, destination, source_stat, destination_parent_stat)` 测试覆盖：source symlink/非目录、destination 已存在、父目录不存在、`st_dev` 不同、source 非预期 Git top-level 均返回 blocker；同卷只返回 exact source/destination/rollback，不执行 rename。

另加 `test_move_plan_binds_archive_inventory_absolute_path_and_sha` 与 `test_move_rejects_missing_or_wrong_expected_plan_sha_before_rename`：前者断言 schema 同时含 `archive_dir`、`archive_inventory_path`、`mirror_verification_path` 及后两者 exact-bytes SHA；后者用 recording rename 证明缺失/错误 `--expected-plan-sha` 时调用数为 0，只有 SHA、confirmation 和所有实时复核均匹配时才恰好调用一次。

再加三个 phase-a 边界测试：

- `test_plan_move_rejects_present_phase_a_worktree`：实时状态仍为 `PRESENT_CLEAN` 时，`plan_move` 返回 `PHASE_A_NOT_REMOVED` 且不写 plan；
- `test_plan_move_binds_removed_phase_a_ref_and_worktree_inventory`：只有 `REMOVED_REF_PRESERVED` 时，plan 包含 canonical `legacy_worktree_inventory` 和其 SHA256；
- `test_move_rejects_phase_a_reappearance_or_ref_drift_before_rename`：plan 生成后 phase-a path/registration 重新出现或 ref tip 漂移时，recording rename 调用数始终为 0。
- `test_move_reverifies_bundle_sidecar_and_mirror_immediately_before_rename`：分别在 confirmation 前改 bundle、sidecar、inventory、mirror ref/blob 或 mirror-verification 任一 bytes，`move` 必须经 `reverify_existing_archive()` 失败且 recording rename 为 0；全部未变时才为 1。
- `test_move_recovers_state_write_failure_without_second_rename`：第一次 recording rename 成功后让 retirement-state writer 失败，断言 source 已无、destination identity 正确、状态为 `MOVE_COMPLETED_STATE_PENDING`；用同一 plan SHA/confirmation 重试时 rename 调用总数仍为 1，只在完整重核后写 state。两端同时存在、两端都不存在或 destination identity 漂移均阻断。

- [ ] **Step 2: 实现 `plan-move` 与 `move` 安全边界**

`plan-move` 要求显式 `--source --destination --expected-head --formal-repo --candidate-state --archive-inventory --expected-phase-a REMOVED_REF_PRESERVED --output`。它先实时读取旧根仓 refs、`git worktree list --porcelain` 和 phase-a path lstat，构造 canonical `legacy_worktree_inventory`；只有根 worktree 一项、phase-a path 不存在且未注册、phase-a ref 仍为固定 tip 时才继续。随后验证 source lstat、Git top-level/common dir/HEAD、destination parent lstat、两者 `st_dev` 相同、destination 不存在、archive verify 和 candidate/main gate，并以 mode `0600` 无覆盖写：

```json
{
  "schema": "taiji-windows-legacy-move-plan/v1",
  "source": "<exact path>",
  "destination": "<exact same-volume Trash path>",
  "source_head": "<40 hex>",
  "source_device": 1,
  "destination_device": 1,
  "formal_repo": "/Users/bwb/Documents/工作/taiji-agentv1.0",
  "formal_main_head": "<40 hex>",
  "candidate_state_path": "<absolute path>",
  "candidate_state_sha256": "<64 hex>",
  "candidate_source_commit": "<same 40 hex>",
  "archive_inventory_path": "<absolute path>",
  "archive_inventory_sha256": "<64 hex>",
  "archive_dir": "<absolute parent containing bundle, sidecar, inventory and mirror-verification>",
  "mirror_verification_path": "<absolute archive-dir/mirror-verification.json>",
  "mirror_verification_sha256": "<64 hex>",
  "phase_a_status": "REMOVED_REF_PRESERVED",
  "phase_a_path": "/private/tmp/taijiagent-windows-packaging-phase-a",
  "phase_a_ref": "refs/heads/codex/phase-a-foundation",
  "phase_a_tip": "e4102f82798cafca664f128d0cab88cf0ab8ff41",
  "legacy_worktree_inventory": {
    "worktrees": [{"path":"/Users/bwb/Documents/工作/taiji-agentv1.0-win","head":"f33663f7e3ffee672d39af7b4ecbe9fd2869a00b","branch":"codex/windows-fast-track"}],
    "phase_a_path_exists": false,
    "phase_a_ref_tip": "e4102f82798cafca664f128d0cab88cf0ab8ff41"
  },
  "legacy_worktree_inventory_sha256": "<sha256 of canonical legacy_worktree_inventory>",
  "retirement_state_path": "/Users/bwb/.local/state/taiji-package/retirements/<literal retirement-id>/retirement-state.json",
  "rollback": ["/bin/mv", "<destination>", "<source>"],
  "created_at": "<UTC ISO-8601>"
}
```

`legacy_worktree_inventory_sha256` 的 canonical 算法固定为：UTF-8 JSON、`sort_keys=True`、`separators=(",", ":")`、`ensure_ascii=False`、**无尾随换行**；SHA256 覆盖这些 exact bytes，落盘 JSON 才另加一个 LF。`plan-move` 在写 plan 前先对绑定 archive dir 调用一次 `reverify_existing_archive()`，再绑定 inventory 与 mirror-verification exact bytes SHA。`move` 先对 plan 文件 exact bytes 计算 SHA256，必须与显式 `--expected-plan-sha <64 lowercase hex>` 字面值一致；再从 plan 中的绝对路径重新读取 formal main、candidate state 和 archive identity，复核各自 SHA、clean main、candidate source commit、source/destination lstat 与同卷条件。它必须在实际 `os.rename` 前、收到 confirmation 后再次直接调用 `reverify_existing_archive(plan.archive_dir)`，重核 bundle、sidecar、inventory、mirror、verification 的 owner/type/mode/bytes/SHA/refs/blob；不得依赖 Task 6 Step 2 或 plan-move 的旧结果。随后重新读取旧根仓 refs/worktree list/path lstat，重建 canonical inventory 并要求 SHA 与 plan 相同，同时再次证明 phase-a 为 `REMOVED_REF_PRESERVED`。所有复核通过才只调用一次 `os.rename(source, destination)`；禁止 copy/delete fallback、跨卷或自动修复 archive。phase-a worktree 不使用该子命令，仍由 Git worktree remove 单独处理。

rename 后 `move` 必须 fsync destination parent，再调用 `write_retirement_state(plan, destination, clock)`。state path 只能取 plan 中同目录固定的 `retirement-state.json`，不能由 `move` CLI 覆盖；父目录 mode 0700，文件以 O_EXCL temp、0600、fsync、`os.replace`、目录 fsync 原子发布。exact schema 为：

```json
{
  "schema": "taiji-windows-legacy-retirement-state/v1",
  "status": "LEGACY_REPOSITORY_MOVED",
  "source": "/Users/bwb/Documents/工作/taiji-agentv1.0-win",
  "destination": "<exact Trash destination>",
  "legacy_head": "f33663f7e3ffee672d39af7b4ecbe9fd2869a00b",
  "preserved_refs": {"<all five exact refs>": "<tip>"},
  "phase_a_status": "REMOVED_REF_PRESERVED",
  "move_plan_path": "<absolute path>",
  "move_plan_sha256": "<64 hex>",
  "archive_dir": "<absolute path>",
  "archive_inventory_sha256": "<64 hex>",
  "candidate_state_path": "<absolute path>",
  "candidate_state_sha256": "<64 hex>",
  "formal_main_head": "<same as candidate source commit>",
  "recorded_at": "<UTC ISO-8601>"
}
```

若 rename 成功但 state 写失败，命令退出非零并只输出 `MOVE_COMPLETED_STATE_PENDING`，不得把目录移回或再次 rename。用同一 plan/SHA/confirmation 重跑时，只有 `source` 不存在、`destination` 是同一 owner 的真实 Git root且 HEAD/全部五 refs/phase-a 状态与 plan 相等、archive/main/candidate 仍全绿，才跳过 rename并补写 state；若 state 已存在且 canonical identity（除 `recorded_at`）与重算完全相同则幂等输出 `RETIREMENT_STATE_READY`，不同则停止不覆盖。

`preserved_refs` 必须是 policy `expected_refs` 的完整深拷贝，恰好五项；JSON 示例中的 `<all five exact refs>` 只是版面缩写，执行时不得保留占位符、漏掉 checkpoint ref 或另加 ref。`recorded_at` 表示 retirement state 成功落盘时间，不冒充 rename 的精确发生时间。

- [ ] **Step 3: 写只描述流程、不声称已退休的 runbook**

runbook 必须说明：Task 1—3 是开发成果，不等于旧仓已移动；实际 archive、mirror restore、fresh main candidate、phase-a removal 和 root move 都在标准集成后执行；物理动作各自需要新确认；rollback 只在 destination 未变化且 original source 仍不存在时执行。

- [ ] **Step 4: 运行 GREEN、精确提交并复跑独立分支全门禁**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/taiji-package-pycache-20260820 python3 -m py_compile scripts/audit_windows_legacy_retirement.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_kylin_candidate_handoff tests.test_taiji_package_state_v2 tests.test_taiji_package_core_boundaries tests.test_taiji_package_orchestration tests.test_windows_legacy_retirement tests.test_taiji_package_candidate tests.test_taiji_package_transport tests.test_taiji_package_windows_adapter tests.test_taiji_package_windows_transport tests.test_linux_golden_orchestrator
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_kylin_packaging_skill tests.test_builder_input_package_contract
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/python38_linux_packaging_gate.py
git diff --check
git status --short --branch
```

首次运行 Expected: 全部通过，branch=`codex/windows-legacy-retirement`，status 只含本 Task 五个明确路径。随后精确提交：

```bash
git add scripts/audit_windows_legacy_retirement.py tests/test_windows_legacy_retirement.py tests/python38_linux_packaging_gate.py docs/runbooks/taiji-windows-candidate-pipeline.md docs/runbooks/taiji-windows-legacy-retirement.md
git commit -m "feat(packaging): gate recoverable Windows repository retirement"
```

提交后完整重跑本 Step 的所有命令；最终 `git status --short` 必须无输出。任一额外路径、测试失败或 gate 文件未进入提交均停止，不进入标准集成。

`tests/python38_linux_packaging_gate.py` 必须把 `scripts/audit_windows_legacy_retirement.py` 加入显式清单；无真实 Python 3.8 时只报告 grammar gate，不把当前解释器结果冒充 3.8 runtime。

- [ ] **Step 5: 停止并走标准集成**

主 Agent 按开发生命周期展示 branch tip、提交清单、验证、push/PR/CI/merge 和正式 main 复验。未获明确授权、CI/审查不通过或正式 main 未包含成果时，停止；低级模型不得自行 merge。集成后从正式 `/Users/bwb/Documents/工作/taiji-agentv1.0` 重跑 Step 4 适用门禁，确认 runtime CLI 来源是正式 main。

### Task 4: 正式 main 上创建 archive，并重新绑定当前候选

**Files:**
- Runtime only: `/Users/bwb/.local/state/taiji-package/legacy-archives/<literal archive-id>/`
- Runtime only: `/Users/bwb/.local/state/taiji-package/retirements/<literal retirement-id>/`

- [ ] **Step 1: 只读采集所有 runtime 参数**

```bash
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 status --porcelain=v2 --branch
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 rev-parse HEAD
git -C /Users/bwb/Documents/工作/taiji-agentv1.0-win status --porcelain=v2 --branch
git -C /Users/bwb/Documents/工作/taiji-agentv1.0-win show-ref --head
git -C /Users/bwb/Documents/工作/taiji-agentv1.0-win worktree list --porcelain
git -C /Users/bwb/Documents/工作/taiji-agentv1.0-win remote -v
```

Expected: formal repo clean main；旧 root/phase-a/main tips 与 policy 精确匹配；remote 空；dirty 只允许 `docs/.DS_Store`；无额外 worktree/ref/untracked path。任一漂移停止并更新审计计划，不更新静态 policy 来掩盖。

- [ ] **Step 2: 确保 candidate commit 等于当前正式 main HEAD**

读取候选 state 并要求：

```text
schema=taiji-package-run-state/v2
target_id=windows-x64
stage=CANDIDATE_BUILT
status_label=候选 EXE 已构建
source.branch=main
source.commit=<当前正式 main HEAD>
artifact.kind=exe
本地 artifact SHA=review/manifest/sidecar SHA
无未闭合 FETCH_PENDING
```

Task 1—3 集成通常会改变 main HEAD，因此旧候选预计不再满足此条件。此时必须返回 Plan 4 Gate R4，展示新 main plan 并取得新的 `BUILD` 授权后构建；不得复用旧 EXE、仅比较 tree 或改写旧 state。

- [ ] **Step 3: 展示 archive 目录并等待精确 `ARCHIVE` 后创建和 mirror verify**

主 Agent 展示 `<literal archive-id>`、`<candidate-state>`、`<product-import-manifest>` 的字面量绝对值、预计写入文件和失败保留；archive dir 必须不存在。未收到操作员当轮输入的 `ARCHIVE` 不执行以下命令。

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
python3 -I -B scripts/audit_windows_legacy_retirement.py archive --policy packaging/windows/legacy-retirement-policy.json --legacy-repo /Users/bwb/Documents/工作/taiji-agentv1.0-win --formal-repo /Users/bwb/Documents/工作/taiji-agentv1.0 --candidate-state <candidate-state> --asset-provenance packaging/windows/asset-provenance.json --product-import-manifest <product-import-manifest> --archive-dir /Users/bwb/.local/state/taiji-package/legacy-archives/<literal archive-id>
python3 -I -B scripts/audit_windows_legacy_retirement.py verify-archive --policy packaging/windows/legacy-retirement-policy.json --legacy-repo /Users/bwb/Documents/工作/taiji-agentv1.0-win --formal-repo /Users/bwb/Documents/工作/taiji-agentv1.0 --candidate-state <candidate-state> --asset-provenance packaging/windows/asset-provenance.json --product-import-manifest <product-import-manifest> --archive-dir /Users/bwb/.local/state/taiji-package/legacy-archives/<literal archive-id> --restore-path /private/tmp/taiji-win-legacy-mirror-<literal archive-id>.git
```

Expected: bundle/sidecar/inventory modes正确，mirror 全 refs 和三个 blob/mode/hash 一致，archive 不会被工具自动删除。

- [ ] **Step 4: 运行最终 audit**

```bash
python3 -I -B scripts/audit_windows_legacy_retirement.py audit --policy packaging/windows/legacy-retirement-policy.json --legacy-repo /Users/bwb/Documents/工作/taiji-agentv1.0-win --formal-repo /Users/bwb/Documents/工作/taiji-agentv1.0 --candidate-state <candidate-state> --asset-provenance packaging/windows/asset-provenance.json --product-import-manifest <product-import-manifest> --archive-dir /Users/bwb/.local/state/taiji-package/legacy-archives/<literal archive-id> --expected-phase-a PRESENT_CLEAN --json
```

Expected: `RETIRE_READY`。否则不进入物理处理。

### Task 5: 独立授权后移除 phase-a linked worktree

**Files:**
- Destructive target: `/private/tmp/taijiagent-windows-packaging-phase-a`

- [ ] **Step 1: 再核 exact identity 和 archive**

要求 path 是旧仓 `worktree list` 中的 linked worktree、clean、branch=`codex/phase-a-foundation`、HEAD=`e4102f82798cafca664f128d0cab88cf0ab8ff41`；bundle mirror 中同 ref/tip 可恢复；candidate/main gate 仍成立。

- [ ] **Step 2: 展示唯一目标并等待精确 `REMOVE_PHASE_A`**

展示 exact path、tip、bundle path/SHA、mirror restore、失败保留和“不删除 branch/ref”。该确认不授权 root move。

- [ ] **Step 3: 不带 force 移除并复核 ref**

```bash
git -C /Users/bwb/Documents/工作/taiji-agentv1.0-win worktree remove /private/tmp/taijiagent-windows-packaging-phase-a
git -C /Users/bwb/Documents/工作/taiji-agentv1.0-win show e4102f82798cafca664f128d0cab88cf0ab8ff41 --no-patch --oneline
git -C /Users/bwb/Documents/工作/taiji-agentv1.0-win worktree list --porcelain
```

Expected: worktree path 消失，branch/ref 仍存在。失败即停止；不得 `--force` 或删 branch。

### Task 6: 再次授权后同卷移动旧根仓，并保留回滚

**Files:**
- Recoverable source: `/Users/bwb/Documents/工作/taiji-agentv1.0-win`
- Recoverable destination: `/Users/bwb/.Trash/taiji-agentv1.0-win-retired-<literal retirement-id>`
- Runtime evidence: `/Users/bwb/.local/state/taiji-package/retirements/<literal retirement-id>/{move-plan.json,retirement-state.json}`

- [ ] **Step 1: 生成不执行的同卷 move plan**

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
python3 -I -B scripts/audit_windows_legacy_retirement.py plan-move --policy packaging/windows/legacy-retirement-policy.json --source /Users/bwb/Documents/工作/taiji-agentv1.0-win --destination /Users/bwb/.Trash/taiji-agentv1.0-win-retired-<literal retirement-id> --expected-head f33663f7e3ffee672d39af7b4ecbe9fd2869a00b --formal-repo /Users/bwb/Documents/工作/taiji-agentv1.0 --candidate-state <candidate-state> --archive-inventory /Users/bwb/.local/state/taiji-package/legacy-archives/<literal archive-id>/inventory.json --expected-phase-a REMOVED_REF_PRESERVED --output /Users/bwb/.local/state/taiji-package/retirements/<literal retirement-id>/move-plan.json
```

Expected: source/destination `st_dev` 相同、destination 不存在、source 非 symlink 且是 exact Git top-level；plan 写出 exact rollback。若不同卷，停止，不 copy+delete。

- [ ] **Step 2: 最后只读重核 archive、candidate/main 和 RETIRE_READY**

不得重跑首次 `verify-archive`，因为 restore path 和 O_EXCL verification 已存在。使用可重入只读子命令：

```bash
python3 -I -B /Users/bwb/Documents/工作/taiji-agentv1.0/scripts/audit_windows_legacy_retirement.py reverify-archive --policy /Users/bwb/Documents/工作/taiji-agentv1.0/packaging/windows/legacy-retirement-policy.json --archive-dir /Users/bwb/.local/state/taiji-package/legacy-archives/<literal archive-id>
python3 -I -B /Users/bwb/Documents/工作/taiji-agentv1.0/scripts/audit_windows_legacy_retirement.py audit --policy /Users/bwb/Documents/工作/taiji-agentv1.0/packaging/windows/legacy-retirement-policy.json --legacy-repo /Users/bwb/Documents/工作/taiji-agentv1.0-win --formal-repo /Users/bwb/Documents/工作/taiji-agentv1.0 --candidate-state <candidate-state> --asset-provenance /Users/bwb/Documents/工作/taiji-agentv1.0/packaging/windows/asset-provenance.json --product-import-manifest <product-import-manifest> --archive-dir /Users/bwb/.local/state/taiji-package/legacy-archives/<literal archive-id> --expected-phase-a REMOVED_REF_PRESERVED --json
```

两条必须退出 0，第二条仍输出 `RETIRE_READY`；正式 main 必须仍 clean 且 HEAD 等于 candidate source commit；phase-a 已移除但 branch/ref 仍在 mirror/source bundle；Trash destination 仍不存在。

- [ ] **Step 3: 展示 move plan 并等待独立 `MOVE_LEGACY_REPO`**

确认必须明确允许移动整个旧根仓，包括已分类的 `docs/.DS_Store`，列 source/destination、same-volume device、bundle SHA、archive 不自动删除和 rollback argv，并展示 `shasum -a 256 <move-plan.json>` 的 64 位字面结果。Task 5 的确认不继承。

- [ ] **Step 4: 只执行一次同卷 rename**

主 Agent把字面量 path 传入；低级模型不得代替操作员提供 confirmation：

```bash
python3 -I -B /Users/bwb/Documents/工作/taiji-agentv1.0/scripts/audit_windows_legacy_retirement.py move --policy /Users/bwb/Documents/工作/taiji-agentv1.0/packaging/windows/legacy-retirement-policy.json --plan /Users/bwb/.local/state/taiji-package/retirements/<literal retirement-id>/move-plan.json --expected-plan-sha <displayed-64-lowercase-hex> --confirmation MOVE_LEGACY_REPO
```

Expected: source 不存在、destination 存在且 HEAD/全部五 refs 可读、bundle/mirror 已由 `move` 内部再次 reverify、`retirement-state.json` schema/identity 正确、正式主仓测试仍通过。若输出 `MOVE_COMPLETED_STATE_PENDING`，按 Task 3 的同一命令重入合同重跑；不得手工伪造 state、再次 rename、自动移回或清空 Trash。

- [ ] **Step 5: 复核由 move 原子固化的外部状态和回滚边界**

只读核 `retirement-state.json` 为 Task 3 exact schema、mode `0600`、owner/nlink 正确，且 state 与 move plan、destination Git、archive、candidate/main 交叉一致；该文件已由 `move` 写入，Step 5 不再另写第二份状态，也不提交到 Git。回滚仅在 destination 未发生任何变化、original source 仍不存在、同卷和 archive 仍验证时由主 Agent再次授权执行 move-plan 中：

```text
/bin/mv <destination> /Users/bwb/Documents/工作/taiji-agentv1.0-win
```

最终状态只能写：

```text
taiji-agentv1.0 的正式 main 是唯一制包权威
旧 Windows 仓已移动到同卷可恢复废纸篓路径
全 refs bundle 和 mirror restore 已验证，archive 未自动删除
历史 1.0.3 仍只是历史未签名单机候选
当前候选的安装、UI、production license、签名和发布状态未提升
```
