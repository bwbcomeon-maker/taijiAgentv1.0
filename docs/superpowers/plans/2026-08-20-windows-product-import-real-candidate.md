# Windows Product Import and Real Candidate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把指定 Windows 产品提交安全收敛到唯一正式仓库，经正式 `main` 集成复验后，在 `windows-direct` 上构建并取回一个绑定该 `main` HEAD 的未签名单机候选 EXE。

**Architecture:** 产品源码取回、builder readiness、正式仓集成和真实构建分别由四个人工门禁控制。产品 bundle 先进入私有 staging，逐提交预审并在临时 clone 试应用；候选构建只使用正式 `main` 生成的冻结 tar 输入，不读取远端产品工作树。Windows transport 只负责唯一 run、离线构建、review/log 取回和验证，运行证据只写私有 state 目录，不再修改正式 `main`。

**Tech Stack:** Python 3.8+、Git bundle/archive、SSH/SCP、PowerShell 5.1、Node.js、Inno Setup、`unittest`

---

## 四个人工门禁、成功定义与禁止项

本计划有四个互不继承的强制门禁：

1. **Gate R1 — READ_ONLY：** 主 Agent 展示 `windows-direct`、两条只读 probe、预期字段和无写入边界；操作员输入精确字符串 `READ_ONLY` 后，才可读取 builder 能力和远端产品 Git 身份。
2. **Gate R2 — IMPORT：** R1 实时 product probe 精确匹配后，主 Agent 展示远端 repo、base/tip、bundle/sidecar、远近 staging，以及将验证 tip 的 object 写入正式主仓 common object store 并新建精确 `refs/archive/windows-product/<tip>` 的影响；操作员输入精确字符串 `IMPORT` 后，才可创建 import run、生成并取回 bundle，并在本地验证后按本门授权执行无覆盖 `install-ref`。失败时保留 bundle/object/ref 现场，不自动删除或回退；删除 archive ref 需要另行明确授权。
3. **Gate R3 — INTEGRATE：** 所有本地实现、产品提交试应用、测试和审计完成后，主 Agent 按 `docs/runbooks/development-lifecycle.md` 展示标准 push/PR/CI/merge 方案；操作员给出该规范要求的明确授权后，才可把成果集成到正式 `main`。本计划不把 `INTEGRATE` 字样本身当作 push、PR 或 merge 授权。
4. **Gate R4 — BUILD：** 正式 `main` clean、已同步、全回归通过，且 plan 绑定当前 `main` HEAD 后，操作员输入精确字符串 `BUILD`，才可准备三件套、传输并构建一次真实候选。

低级模型不得自行越过 R1—R4，不得把前一门授权复用于后一门。任何实时 branch、HEAD、clean、host、path、SHA、main 集成方式或缓存事实与计划不同时，输出差异并停止；不得自动改写期望。

唯一成功标签固定为：

```text
候选 EXE 已构建
```

它只证明 Source、Payload、Installer 三层；不安装、不启动、不做 health、交互验收、production license、签名、SmartScreen、发布、Tag 或 Release。缓存/工具缺失时停止，不下载、不安装、不联网降级。计划 1—5 的代码步骤只允许本地测试和本地提交；真实 SSH、bundle 取回、正式集成和真实 build 分别停在对应门禁。

## 固定远端与来源身份

```text
host alias: windows-direct
product repo: D:\tw\source\taijiAgentv1.0
historical branch: codex/windows-local
historical tip: 89954e96d23cf43f266197813eb283475d5ff7e1
comparison base: 5364233e1297e5f2837382823d4e35a0d114aba7
PowerShell: C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
builder run root: D:\tw\taiji-builds
cache root: D:\tw\cache
```

允许产品变化路径只能是：

```text
apps/taiji-desktop/src/main.js
apps/taiji-desktop/src/windows-runtime.js
apps/taiji-desktop/tests/windows-runtime.test.js
hermes-local-lab/sources/hermes-agent/taiji_runtime_profile.py
hermes-local-lab/sources/hermes-agent/tests/test_taiji_runtime_profile.py
packaging/windows/diagnose.ps1
```

### Task 1: 分离 builder doctor 与 product source probe

**Files:**
- Create: `packaging/pipeline/adapters/windows_ssh.py`
- Modify: `packaging/pipeline/adapters/windows_x64.py`
- Modify: `scripts/taiji-package-candidate.py`
- Modify: `tests/test_taiji_package_windows_adapter.py`
- Create: `tests/test_taiji_package_windows_real_transport.py`
- Create: `tests/test_windows_product_probe.py`

- [ ] **Step 1: 写绝对 PowerShell argv 与职责分离 RED**

```python
import base64
import importlib
import json
from pathlib import Path
import subprocess
import unittest


POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SSH = ROOT / "packaging/pipeline/adapters/windows_ssh.py"


def load_windows_ssh(testcase):
    testcase.assertTrue(WINDOWS_SSH.is_file())
    return importlib.import_module("packaging.pipeline.adapters.windows_ssh")


class WindowsRealTransportTests(unittest.TestCase):
    def test_encoded_command_uses_target_absolute_powershell(self):
        windows_ssh = load_windows_ssh(self)
        self.assertTrue(hasattr(windows_ssh, "powershell_argv"))
        encoded = base64.b64encode(
            "$env:PROCESSOR_ARCHITECTURE".encode("utf-16le")
        ).decode("ascii")
        argv = windows_ssh.powershell_argv(
            "windows-direct", POWERSHELL, "$env:PROCESSOR_ARCHITECTURE"
        )
        self.assertEqual(argv[:6], [
            "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "windows-direct",
        ])
        expected_remote = subprocess.list2cmdline([
            POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive",
            "-EncodedCommand", encoded,
        ])
        self.assertEqual(argv[6], expected_remote)
        self.assertEqual(base64.b64decode(encoded).decode("utf-16le"), "$env:PROCESSOR_ARCHITECTURE")

    def test_builder_doctor_never_reads_product_repo(self):
        windows_ssh = load_windows_ssh(self)
        self.assertTrue(hasattr(windows_ssh, "builder_probe_script"))
        script = windows_ssh.builder_probe_script({
            "remote_root": r"D:\tw\taiji-builds",
            "cache_root": r"D:\tw\cache",
            "minimum_free_gib": 20,
        })
        self.assertNotIn(r"D:\tw\source\taijiAgentv1.0", script)

    def test_product_probe_never_checks_or_mutates_builder_run(self):
        windows_ssh = load_windows_ssh(self)
        self.assertTrue(hasattr(windows_ssh, "product_probe_script"))
        script = windows_ssh.product_probe_script(
            r"D:\tw\source\taijiAgentv1.0",
            "codex/windows-local",
            "89954e96d23cf43f266197813eb283475d5ff7e1",
            "5364233e1297e5f2837382823d4e35a0d114aba7",
        )
        self.assertNotIn(r"D:\tw\taiji-builds", script)
        for forbidden in ("New-Item", "Set-Content", "Remove-Item", "git bundle create"):
            self.assertNotIn(forbidden, script)

    def test_cache_missing_is_parsed_without_build(self):
        windows_ssh = load_windows_ssh(self)
        self.assertTrue(hasattr(windows_ssh, "parse_builder_probe"))
        payload = json.dumps({
            "schema": "taiji-windows-builder-doctor/v1",
            "architecture": "AMD64",
            "filesystem": "NTFS",
            "free_bytes": 30 * 1024 * 1024 * 1024,
            "cache_checks": [{"name": "electron", "present": False}],
        })
        result = windows_ssh.parse_builder_probe(payload)
        self.assertEqual(result["builder_status"], "BLOCKED")
        self.assertEqual(result["failure_categories"], ["WINDOWS_CACHE_MISSING"])
```

`powershell_argv(host_alias, powershell_path, script, ssh_config=None)` 返回 `/usr/bin/ssh` 参数数组，最后只有一个由 `subprocess.list2cmdline()` 生成的远端命令参数；禁止 `shell=True`。测试 fixture 使用注入的 command runner，必须在测试文件内完整定义，不得连接远端。

再写 `test_facade_windows_factory_wires_real_transport_at_call_time`、`test_facade_windows_online_doctor_uses_recording_runner` 和 `test_facade_windows_build_and_fetch_reuse_same_transport_contract`。在 patch context 中把 facade 模块全局 `WindowsSshTransport` 换成 recording class，经 `_facade_adapter_factory("windows-x64")` 和真实 facade `main()` 调用 doctor/build/fetch；断言构造参数精确包含 validated target、`ssh_config`、本次 `command_runner`，且没有外部 argv。fetch fixture 必须是 v2 `FETCH_PENDING`，事件只含 fetch-review/fetch-log/validate/publish，不能重新 online/build。

同时新增 `test_ready_online_result_finalizes_windows_plan_once` 与 `test_online_cache_or_host_drift_fails_before_confirmation_and_state`：前者断言 `bind_online_plan` 新增键集合精确等于 `WindowsX64Adapter.online_plan_keys`，finalized plan 的 full cache/host object 与三个重算 SHA 一致，原 plan 不变；后者分别篡改 observation member、requirements SHA、host fact，均以 `PLAN_INVALID` 停止，input reader/state/remote events 为 0。

- [ ] **Step 2: 运行 RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_package_windows_real_transport tests.test_windows_product_probe
```

Expected: FAIL，Windows real transport 和职责分离尚不存在。

- [ ] **Step 3: 实现两个互不依赖的只读结果**

`WindowsSshTransport.online_doctor()` 只输出 builder readiness：

```text
schema, builder_status, host_alias, os, os_version, architecture,
powershell_version, git_path, tar_path, node_path, npm_path,
python_path, iscc_path, filesystem, free_bytes, cache_root,
cache_checks, cache_requirements_sha256, cache_observation,
cache_observation_sha256, host_facts, host_facts_sha256,
remote_root_parent_exists, blockers, failure_categories
```

它验证 Windows/x64、PowerShell、Git、tar、Node/npm、Python、ISCC、NTFS、20 GiB 和 target 所列离线缓存；不得访问 `D:\tw\source\taijiAgentv1.0`，不得创建探针文件。`cache_observation` 必须是 Plan 3 定义的完整 exact object，`cache_requirements_sha256` 绑定仓内 requirements，`cache_observation_sha256` 对删除 `observed_at` 后的观测基对象做 canonical SHA256。`host_facts` 必须是 Plan 3 的 `taiji-windows-host-facts/v1` exact object，`host_facts_sha256` 是其 canonical SHA；parser 必须重算而不是相信远端字符串。Windows adapter 的 `bind_online_plan()` 只新增其固定五键，完整 cache/host object 只写入 finalized plan 与 run-state 冻结 plan；run-state identity 保存 requirements/observation/host 三个 SHA，session 只保存 observation path+SHA，package manifest 只保存 requirements/observation 两个 SHA。后续重核只比较内容 identity。缺缓存返回 `builder_status=BLOCKED` 和 `WINDOWS_CACHE_MISSING`。

`probe_product_source()` 只输出：

```text
schema, host_alias, product_repo, product_branch, product_commit,
product_clean, base_present, expected_tip_present, blockers
```

它只运行 Git 读取命令，不读取完整环境、settings、token、密钥或用户文件，不检查/创建 builder run。

本计划从这里开始把**统一入口**的 Windows real transport 接通，但不改变 Plan 3 的安全默认：直接构造 `WindowsX64Adapter()` 且未注入 factory 时仍为 `BUILDER_UNREACHABLE`。facade 的 `_facade_adapter_factory(target_id)` 在 `windows-x64` 分支必须于每次调用时读取当前模块全局 `WindowsSshTransport`，并注入一个 factory；该 factory 的唯一构造合同为 `WindowsSshTransport(target, *, ssh_config, command_runner)`。adapter 的 `create_transport(repo, target, *, ssh_config, command_runner)` 只调用该 factory，不缓存实例。Kylin 分支和既有 monkeypatch seam 不变；未知 target 仍交固定 registry 拒绝。

- [ ] **Step 4: 运行 GREEN 并提交本地实现**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_package_windows_real_transport tests.test_windows_product_probe tests.test_taiji_package_windows_adapter tests.test_taiji_package_windows_transport tests.test_taiji_package_core_boundaries
git add packaging/pipeline/adapters/windows_ssh.py packaging/pipeline/adapters/windows_x64.py scripts/taiji-package-candidate.py tests/test_taiji_package_windows_real_transport.py tests/test_windows_product_probe.py tests/test_taiji_package_windows_adapter.py
git commit -m "feat(packaging): separate Windows builder and product probes"
```

Expected: `OK`，没有真实 SSH。

### Task 2: 实现五子命令 bundle helper，并在 Gate R2 后取回

**Files:**
- Create: `packaging/windows/import_product_source.py`
- Create: `tests/test_windows_product_import.py`
- Runtime only: `/Users/bwb/.local/state/taiji-package/imports/<import-id>/`

helper 只能提供以下子命令，不提供隐式“一键导入”：

```text
probe       只读远端 branch/HEAD/clean/base
fetch       R2 后创建唯一 import run、生成 bundle/sidecar 并 SCP 到私有 staging
verify      本地 bundle/sidecar/commit/path/mode 审计并原子写 product-import.json
install-ref 将已验证 tip 安装到不覆盖的 refs/archive/windows-product/<full-tip>
inventory   从 product-import.json 输出确定 commit 序列和逐 commit inventory
```

`probe` 与 `fetch` 必须要求显式 `--target-config`，通过固定 registry/Windows adapter 验证 `target_id=windows-x64`，并从该配置取得 host alias、remote root、Git 和 PowerShell 绝对路径；不得使用远端 PATH 或脚本内第二份工具默认值。

远端 bundle 命令固定为对已验证分支完整可达历史执行 `git bundle create <new-bundle> refs/heads/codex/windows-local`，不使用 `base..tip` prerequisite bundle、不创建临时 ref、不修改产品仓。这样全新本地 bare repo 可独立 `git bundle verify` 和 fetch；本地 verifier 仍只允许/审计 `base..tip` 的确定提交序列，其他祖先对象不进入主仓 archive ref 的可执行范围。

- [ ] **Step 1: 写完整 synthetic RED**

`tests/test_windows_product_import.py` 必须在临时目录内定义 `run_git()`、`make_bundle_fixture()` 和 `write_sidecar()`，并先用 `self.assertTrue((ROOT / "packaging/windows/import_product_source.py").is_file())` 把缺文件 RED 转成 AssertionFailure，再通过 `importlib.util.spec_from_file_location()` 加载；不得引用未定义的 `self.fixture_*`。还要从非仓库 cwd 以 `/usr/bin/python3 -I -B <absolute-helper> --help` 运行，断言退出 0 且实际导入模块位于同一仓库根。测试覆盖：

```text
probe 不写入；fetch argv 固定且不使用 shell；bundle/sidecar basename 漂移；
sidecar SHA 错；base 非 tip 祖先；tip 不精确；merge commit；
逐 commit 越界 path；symlink；gitlink；异常 mode；已有 staging；
archive ref 不存在时成功；同 ref 同 tip 幂等；同 ref 不同 tip 拒绝覆盖；
inventory 顺序等于 git rev-list --reverse --topo-order <base>..<tip>。
```

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_product_import
```

Expected: FAIL，helper 不存在。

- [ ] **Step 2: 锁死 `product-import.json` schema**

```json
{
  "schema": "taiji-windows-product-import/v1",
  "import_id": "<literal import id>",
  "host_alias": "windows-direct",
  "product_repo": "D:\\tw\\source\\taijiAgentv1.0",
  "base_commit": "5364233e1297e5f2837382823d4e35a0d114aba7",
  "tip_commit": "89954e96d23cf43f266197813eb283475d5ff7e1",
  "bundle": {"basename": "windows-product-89954e96.bundle", "bytes": 1, "sha256": "<64 hex>", "path": "<absolute private path>"},
  "sidecar": {"basename": "windows-product-89954e96.bundle.sha256", "bytes": 1, "sha256": "<64 hex>"},
  "allowed_paths": ["<the six fixed paths>"],
  "commits": [{"old_sha": "<40 hex>", "parents": ["<one parent>"], "subject": "<text>", "patch_id": "<40 hex>", "paths": [{"path": "<allowed path>", "status": "A|M|D", "old_mode": "<100644|100755|null>", "new_mode": "<100644|100755|null>", "old_blob": "<40 hex|null>", "new_blob": "<40 hex|null>", "sha256": "<64 hex|null>"}]}],
  "verified_at": "<UTC ISO-8601>"
}
```

每个待移入 commit 必须恰有一个 parent且至少改变一个允许 path；diff 固定 `--no-renames`，status 只允许 A/M/D，新增项的 old 字段为 null、删除项的 new/sha256 为 null。每个 commit 的变化路径单独审计，不能只审计 base→tip 最终差异。`patch_id` 使用 `git show --pretty=format: --binary <sha> | git patch-id --stable` 的第一列。

- [ ] **Step 3: 实现 verify/install-ref/inventory**

`verify` 先核 sidecar，再 `git bundle verify`，然后只 fetch 到 import 目录下 mode `0700` 的临时 bare repo。它拒绝 absolute/escaping staging、symlink/hardlink、非当前用户和已存在输出；成功时以 mode `0600` 原子写 manifest。该 helper 以 `-I` 直接执行时只 bootstrap 自己所属仓库根，并有从非仓库 cwd 执行 `--help` 的 subprocess 测试。

`install-ref` 必须从 manifest 重新核 bundle SHA，使用 `git fetch <bundle> refs/heads/codex/windows-local` 让对象进入显式 `--repo` 的 common object store，并核 `FETCH_HEAD` 等于 manifest tip；然后仅在 ref 不存在或已指向同 tip 时执行带 old-value 保护的 `git update-ref`：

```text
refs/archive/windows-product/89954e96d23cf43f266197813eb283475d5ff7e1
```

不得覆盖不同 tip，不得写 `refs/heads/*`，不得删除 archive ref。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_product_import
git add packaging/windows/import_product_source.py tests/test_windows_product_import.py
git commit -m "feat(packaging): verify Windows product bundle import"
```

Expected: `OK`。

- [ ] **Step 5: Gate R1 后执行两条只读 probe**

```bash
./taiji-package --target windows-x64 --json doctor --online
python3 -I -B packaging/windows/import_product_source.py probe --target-config packaging/pipeline/targets/windows-x64.json --host windows-direct --product-repo 'D:\tw\source\taijiAgentv1.0' --expected-branch codex/windows-local --expected-tip 89954e96d23cf43f266197813eb283475d5ff7e1 --expected-base 5364233e1297e5f2837382823d4e35a0d114aba7
```

Expected: builder 为 `BUILDER_READY`；product probe 为 clean、branch/tip/base 精确匹配。该步不创建目录、不取回源码、不更新 candidate run-state。任一不符进入 `SOURCE_DRIFT` 并停止。

- [ ] **Step 6: 展示 R2 并等待精确 `IMPORT`**

R2 还必须明确展示：正式主仓 common object store 将接收已验证 bundle objects；只允许无覆盖创建完整 `refs/archive/windows-product/89954e96d23cf43f266197813eb283475d5ff7e1`；不得修改 `refs/heads/*`；失败时保留 object/ref，默认不删除，删除需另行授权。

授权文本必须逐项列出 R1 实时 branch/HEAD/clean、base/tip、六条 allowlist、bundle/sidecar basename、方向、远程 `D:\tw\taiji-builds\<tip>\<import-id>\import`、本地 import state、失败保留和无远端源码修改边界。

- [ ] **Step 7: R2 后依次执行 fetch 和 verify**

主 Agent 把 `<import-id>` 替换为当轮展示并获授权的字面量；不得复制尖括号文本执行。

```bash
python3 -I -B packaging/windows/import_product_source.py fetch --target-config packaging/pipeline/targets/windows-x64.json --import-id <import-id> --host windows-direct --product-repo 'D:\tw\source\taijiAgentv1.0' --base 5364233e1297e5f2837382823d4e35a0d114aba7 --tip 89954e96d23cf43f266197813eb283475d5ff7e1 --state-root /Users/bwb/.local/state/taiji-package
python3 -I -B packaging/windows/import_product_source.py verify --import-dir /Users/bwb/.local/state/taiji-package/imports/<import-id> --base 5364233e1297e5f2837382823d4e35a0d114aba7 --tip 89954e96d23cf43f266197813eb283475d5ff7e1
```

Expected: `product-import.json` 精确绑定 bundle 和逐 commit inventory。远端只允许在唯一 import run 中创建 bundle/sidecar；不得修改产品 repo、删除失败现场或覆盖任何路径。

### Task 3: 预审、临时 clone 试应用，再映射到功能分支

**Files:**
- Modify only: Task 2 六条 allowlist path
- Create: `docs/reviews/2026-08-20-windows-product-source-import.md`
- Test: product Node/Python tests and Linux/Windows packaging tests

- [ ] **Step 1: 安装 archive ref 并输出 inventory**

```bash
python3 -I -B packaging/windows/import_product_source.py install-ref --manifest /Users/bwb/.local/state/taiji-package/imports/<import-id>/product-import.json --repo /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller --ref refs/archive/windows-product/89954e96d23cf43f266197813eb283475d5ff7e1
python3 -I -B packaging/windows/import_product_source.py inventory --manifest /Users/bwb/.local/state/taiji-package/imports/<import-id>/product-import.json
git rev-parse refs/archive/windows-product/89954e96d23cf43f266197813eb283475d5ff7e1
```

Expected: ref 为完整 tip；inventory 中每个 commit 已预审且只有一个 parent、允许 path/mode/type。

- [ ] **Step 2: 在全新临时 clone 逐提交试应用**

创建前要求 `/private/tmp/taiji-win-product-trial-89954e96` 不存在：

```bash
git clone --no-local --branch codex/cross-platform-package-controller /Users/bwb/Documents/工作/taiji-agentv1.0 /private/tmp/taiji-win-product-trial-89954e96
git -C /private/tmp/taiji-win-product-trial-89954e96 fetch /Users/bwb/.local/state/taiji-package/imports/<import-id>/windows-product-89954e96.bundle 89954e96d23cf43f266197813eb283475d5ff7e1:refs/archive/windows-product/89954e96d23cf43f266197813eb283475d5ff7e1
```

按 inventory 顺序逐个运行 `git cherry-pick <old_sha>`。每次成功后记录：

```text
old_sha | stable_patch_id | trial_new_sha | exact changed paths/modes
```

任一冲突执行 `git cherry-pick --abort` 并停止；预审后仍出现越界 path/mode、patch-id 不同或产品测试失败，保留 trial clone，不修改真实功能分支。

- [ ] **Step 3: 在 trial clone 运行完整适用回归**

```bash
node --check /private/tmp/taiji-win-product-trial-89954e96/apps/taiji-desktop/src/main.js
node --check /private/tmp/taiji-win-product-trial-89954e96/apps/taiji-desktop/src/windows-runtime.js
node --test /private/tmp/taiji-win-product-trial-89954e96/apps/taiji-desktop/tests/windows-runtime.test.js
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q /private/tmp/taiji-win-product-trial-89954e96/hermes-local-lab/sources/hermes-agent/tests/test_taiji_runtime_profile.py
```

Expected: 全部通过。缺 Node/pytest 或 runtime 依赖时停止，不联网安装并冒充通过。

- [ ] **Step 4: 真实功能分支逐提交应用并记录最终映射**

只有 trial 全绿才在 `codex/cross-platform-package-controller` 逐个 cherry-pick 相同 old SHA。每次前要求 worktree clean；每次后核 exact paths/modes 和 stable patch-id，记录：

```text
old_sha | stable_patch_id | trial_new_sha | branch_new_sha
```

开始正式应用前记录 `pre_import_head`。冲突只允许 `git cherry-pick --abort` 后停止，不选 ours/theirs；此时此前已成功的 cherry-pick 仍然存在，必须在产品导入审计中把独立运行状态记为 `IMPORT_PARTIAL`（不是 `PipelineError.category`），列出 pre-import HEAD、已应用 old/new/patch-id 映射和下一 old SHA，并禁止进入 Task 4，不得 reset。完整序列成功后运行：

```bash
node --check apps/taiji-desktop/src/main.js
node --check apps/taiji-desktop/src/windows-runtime.js
node --test apps/taiji-desktop/tests/windows-runtime.test.js
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q hermes-local-lab/sources/hermes-agent/tests/test_taiji_runtime_profile.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_package_candidate tests.test_taiji_package_transport tests.test_taiji_package_windows_adapter tests.test_taiji_package_windows_transport tests.test_linux_golden_orchestrator
git diff --check
```

- [ ] **Step 5: 提交导入映射审计**

审计文档必须列出 base/tip/import manifest SHA、每行四列映射、allowlist、trial 测试和真实分支测试；说明候选仍为 `windows-candidate/not_required`，不是 production license。它还必须包含且只包含一行机器可读绑定：`product-import-manifest-sha256: <64 lowercase hex>`，值为私有 `/Users/bwb/.local/state/taiji-package/imports/<import-id>/product-import.json` 的 exact file SHA256；只提交 review 和该摘要，不提交 runtime manifest/bundle。

```bash
git add docs/reviews/2026-08-20-windows-product-source-import.md
git commit -m "docs(packaging): record Windows product commit mapping"
```

### Task 4: 实现 tar 输入三件套与安全解压合同

**Files:**
- Create: `packaging/windows/builder_input_package.py`
- Create: `packaging/windows/safe_tar.py`
- Create: `tests/test_windows_builder_input_package.py`
- Create: `tests/test_windows_safe_tar.py`
- Modify: `packaging/pipeline/adapters/windows_x64.py`

解压 bootstrap 不从尚未解开的 tar 中取。Windows adapter 在 plan 中新增并冻结 exact `controller_bootstrap.safe_tar` object：`source_path,remote_path,bytes,sha256,python_path`。`source_path` 必须是正式 clean main 的 `packaging/windows/safe_tar.py`，工作树 regular file 的 bytes/SHA 必须与 `git show <source-commit>:packaging/windows/safe_tar.py` 一致；`remote_path` 固定为本轮 `input\controller-safe-tar.py`；`python_path` 必须是 online doctor 已验证的 target 绝对 Python 路径。run-state 创建后只有 Plan 2 `bind_verified_input()` 可把 `plan.input` 原子 MISSING→REUSABLE 一次；`controller_bootstrap` 和其余全部 plan 字段不可修改，因此 bootstrap identity 不能被 fetch 或后续阶段替换。

三件套固定为：

```text
taijiagent-windows-builder-input-<commit>.tar.gz
taijiagent-windows-builder-input-<commit>.manifest.json
taijiagent-windows-builder-input-<commit>.tar.gz.sha256
```

- [ ] **Step 1: 写 create/verify 和首次 plan RED**

测试先对两个 helper 路径执行 `self.assertTrue(path.is_file())`，再用显式 file-location loader 导入，保证缺能力时是 AssertionFailure 而非 ImportError；同时从非仓库 cwd 用 `-I -B <absolute-helper> --help` 验证直接入口。覆盖：全不存在=`MISSING`；首次 `plan` 只报告 `MISSING` 且磁盘仍无三件套；合法三件=`REUSABLE`；缺任一时 status=`PARTIAL`、类别=`INPUT_TRIPLET_PARTIAL`；wrong commit/tree/archive/manifest SHA=`INPUT_VERIFICATION_FAILED`；symlink/hardlink/非当前用户；同 commit create 不覆盖；dirty、非 `main` 或非完整 commit 拒绝。另建 `test_version_is_read_only_from_bound_commit_and_cross_checked`：`git show <commit>:VERSION` 必须是唯一单行 `X.Y.Z\n`，同 commit 的 desktop package JSON version 必须相等；缺失、额外行、非 semver、不相等、工作树值与 commit 值漂移均在 plan/input 创建前停止，且 CLI 不存在版本覆盖参数。

manifest exact fields：

```json
{
  "schema": "taiji-windows-builder-input/v1",
  "source_commit": "<40 hex formal main HEAD>",
  "source_tree": "<40 hex>",
  "version": "<VERSION single-line X.Y.Z>",
  "source_branch": "main",
  "archive_basename": "<exact name>",
  "archive_bytes": 1,
  "archive_sha256": "<64 hex>",
  "target_config_sha256": "<SHA256 of validated canonical target JSON bytes>",
  "asset_provenance_sha256": "<SHA256 of canonical provenance JSON bytes>",
  "created_at": "<UTC ISO-8601>"
}
```

sidecar exact bytes 为两行，顺序固定：

```text
<archive sha256>  taijiagent-windows-builder-input-<commit>.tar.gz
<manifest sha256>  taijiagent-windows-builder-input-<commit>.manifest.json
```

- [ ] **Step 2: 写 hostile tar RED**

`safe_tar.py` 只允许 UTF-8 名称的 regular file/directory；拒绝 absolute、drive/UNC、`..`、symlink、hardlink、device/FIFO、反斜杠、ADS 冒号、NUL、Windows 保留设备名、大小写折叠重复、尾随点/空格和 file/parent 冲突。目标目录必须不存在且位于 run 的 `source` 子目录；校验全部 member 后才逐个创建，文件 mode 不从 tar 继承。

真实 transport 先传输三件套、plan 中的完整 `cache-observation.json` 和单独的 `controller-safe-tar.py`；先 strict parse observation、核 exact schema/canonical serialization，并对删除 `observed_at` 后的基对象重算 SHA 与 plan identity 比较，再用 PowerShell `Get-Item`/`Get-FileHash -Algorithm SHA256` 核 helper 的 bytes/SHA，最后以 target 的绝对 Python 执行 `<python_path> -I -B <remote_path> extract --archive <archive> --destination <new-source-dir> --manifest <manifest>`。helper 必须是 Python 标准库自包含文件，不导入尚未解压的源码；任一 identity 不符在执行 helper 前停止。测试必须证明执行顺序是“核 observation/bootstrap → 列出并安全验证所有 tar member → 解压”，不得退回 `tar.exe -xf`。session/package manifest 的 `tools` 同时绑定 `python` 和 `safe_tar` 的绝对路径、bytes、SHA 与非空版本标识 `taiji-safe-tar/v1`。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_builder_input_package tests.test_windows_safe_tar
```

Expected: FAIL，helper 不存在。

- [ ] **Step 3: 实现确定性 archive 和无覆盖 verify**

archive 只从显式 formal repo/完整 `main` commit 使用 `git archive --format=tar`，再以固定 gzip mtime 压缩；创建前从该 commit 读取 `VERSION` 并与同 commit 的 desktop package JSON 交叉验证，把结果写入 local plan 和输入 manifest。`created_at` 只影响 manifest，不声称 manifest 跨运行字节相同。任何 partial/invalid 输入不删、不覆盖、不修复。`plan` 永不 create；只有 R4 的 `build` 确认后才调用 create。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_builder_input_package tests.test_windows_safe_tar tests.test_taiji_package_windows_adapter
git add packaging/windows/builder_input_package.py packaging/windows/safe_tar.py packaging/pipeline/adapters/windows_x64.py tests/test_windows_builder_input_package.py tests/test_windows_safe_tar.py
git commit -m "feat(packaging): freeze and safely extract Windows builder input"
```

### Task 5: 完成 real transport、review/log 取回和本地验证

**Files:**
- Modify: `packaging/pipeline/adapters/windows_ssh.py`
- Modify: `packaging/pipeline/adapters/windows_x64.py`
- Create: `packaging/windows/candidate_evidence.py`
- Modify: `tests/test_taiji_package_windows_real_transport.py`
- Create: `tests/test_windows_candidate_evidence.py`

- [ ] **Step 1: 写完整阶段 RED**

```python
REAL_BUILD_STAGES = [
    "online-doctor", "create-remote-run", "transfer-input",
    "remote-input-verify", "remote-candidate-build",
    "fetch-review", "fetch-log", "local-review-verify", "publish",
]
REAL_FETCH_STAGES = ["fetch-review", "fetch-log", "local-review-verify", "publish"]
```

所有 SSH/SCP 使用 `/usr/bin/ssh`、`/usr/bin/scp` 参数数组；PowerShell 使用 target 的绝对路径和 UTF-16LE EncodedCommand；禁止本地 shell 执行。fixture 必须在测试文件内定义 recording runner、review factory 和 corruption helpers。

测试覆盖：remote run 已存在、输入 sidecar/manifest 漂移、安全解压拒绝、SCP review/log 任一中断、marker 缺失、额外/缺少 review 文件、manifest schema/source/input 错、formal check 缺失/重排/非零/空日志/伪 PASS、EXE SHA 错、PE machine 错、Authenticode 非 `NotSigned`、filename/PE version 错、本地 output 占用，以及 `FETCH_PENDING` 精确调用 `REAL_FETCH_STAGES`。

`tests/test_windows_candidate_evidence.py` 另用临时 state root 覆盖：只有 `target_id=windows-x64,stage=CANDIDATE_BUILT` 且 state/artifact/review identity 完整时可生成；Kylin、FAILED、FETCH_PENDING、symlink/hardlink/错误 mode/owner、artifact SHA 漂移均在写入前拒绝。成功时 destination 是 run 下单个 `evidence/` 目录，内含且只含 `windows-candidate-evidence.json` 与 `windows-candidate-handoff.md`，目录 mode 0700、文件 0600；崩溃前 staging 不影响重试，最终目录通过一次同文件系统 `os.rename` 原子发布。目标已存在时只在两文件 exact bytes 与当前 state 重算结果一致时幂等返回 `EVIDENCE_READY`，否则 `LOCAL_OUTPUT_OCCUPIED`，不覆盖。通用 core/Kylin source 不得包含两个 Windows basename。

同一测试模块必须有 `test_candidate_evidence_help_runs_isolated_from_external_cwd`：从临时非仓库 cwd 用 `/usr/bin/python3 -I -B <absolute candidate_evidence.py> --help` 运行，断言 exit 0、无 `ModuleNotFoundError`、无 cwd 输出。若 helper 复用仓库模块，只能按总设计从自身绝对路径 bootstrap 精确 repo root，并断言实际 import 位于该 root。

- [ ] **Step 2: 锁死 review exact set 与 schema**

review 根只能包含：

```text
TaijiAgent-Setup-<version>-win-x64.exe
TaijiAgent-Setup-<version>-win-x64.exe.sha256
taiji-package-manifest.json
formal-build-tests.log
构建报告.txt
.build-success
run-state.json
```

`taiji-package-manifest.json` exact top-level fields：

```text
schema, run_id, target_id, source, input, target_config_sha256,
asset_provenance_sha256, cache_requirements_sha256,
cache_observation_sha256, tools, payload, formal_tests,
artifact, boundaries, started_at, finished_at
```

其中 `schema=taiji-package-manifest/v2`；`source` 绑定 commit/tree；`input` 绑定三件 basename/bytes/SHA；`tools` 绑定 target 绝对工具路径和观察身份，并含经核的 `python`/`safe_tar`；`payload` 内嵌有序 file entries 及其 canonical manifest SHA；`artifact` 绑定 basename/bytes/SHA256、version、file_version、product_version、PE machine=`0x8664`、PE optional magic=`0x20b`、authenticode_status=`NotSigned`；filename version、Inno `AppVersion`、FileVersion、ProductVersion 必须一致。`formal_tests` 必须逐字符合 Plan 3 §1.5 的七项 ordered checks、八行 log、全部 exit 0 与 `status=PASS`；任一 check 缺失、重排、非零、log 行不匹配或空 log 都失败。`boundaries` 固定排除 installation、interactive-acceptance、production-license、signing、publication。

远端 `run-state.json` exact fields：

```text
schema, run_id, target_id, source_commit, host_facts_sha256,
stage_history, terminal_status, started_at, finished_at
```

其中 `schema=taiji-package-remote-run/v1`、`terminal_status=REMOTE_BUILD_SUCCEEDED`。`.build-success` 是 `taiji-package-build-success/v1` canonical JSON，exact key 名必须逐字采用 Plan 3 §1.7（包括五组 basename/bytes/SHA，不能缩写或换别名），精确绑定 package manifest、artifact、formal tests log、build report 和 remote state；必须在其余文件全部 fsync 后最后原子创建。EXE sidecar exact 一行为：

```text
<exe sha256>  <exe basename>
```

`remote-build.log` 位于 review 外的 run `logs`，必须单独 fetch 到本地 staging。

- [ ] **Step 3: 实现 PE/AuthentiCode/version 验证**

远端 PowerShell 使用 `Get-AuthenticodeSignature` 和 `VersionInfo` 写入 manifest；不是 `NotSigned` 即失败。远端和本地都验证 `MZ`、PE signature、machine `0x8664`、optional header `0x20b`；本地再核 manifest、文件名和 sidecar，不信任 marker 单独成立。

顺序固定为：唯一目录 → 三件套、cache observation 与独立 safe-tar bootstrap → observation schema+projection SHA、sidecar/manifest/bootstrap bytes+SHA → 绝对 Python 执行 `safe_tar.py` → `Build-CandidateReview.ps1` → 远端 schema/marker/PE/SHA → fetch-review → fetch-log → 本地 exact-set/schema/PE/SHA/version → 无覆盖 publish。只读使用 `D:\tw\cache`；失败目录和日志保留，不自动清理。

`packaging/windows/candidate_evidence.py` 是纯 Windows 专属派生 helper，不被通用 core import。公开 API 固定为 `build_evidence_payload(state)`、`render_handoff(state)`、`publish_evidence_bundle(run_dir, state)`、`main(argv=None)`。它只读取已持久化 terminal state，在 run 内独占创建 `.evidence-<random>/`，写两文件、逐文件 fsync、目录 fsync 后，以一次 `os.rename(staging, run_dir/evidence)` 发布；失败保留 staging 并允许用新 staging 重试，不改变 run-state。候选成功与证据 bundle 是两个明确层级：helper 失败不把候选降级，也不伪称 handoff 已写；操作员重跑同一 helper即可收敛。

- [ ] **Step 4: 运行 GREEN 与回归**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_package_windows_real_transport tests.test_taiji_package_windows_transport tests.test_taiji_package_transport tests.test_windows_safe_tar tests.test_windows_candidate_evidence tests.test_taiji_package_core_boundaries tests.test_taiji_package_orchestration
```

Expected: `OK`，无真实 SSH。

- [ ] **Step 5: 提交**

```bash
git add packaging/pipeline/adapters/windows_ssh.py packaging/pipeline/adapters/windows_x64.py packaging/windows/candidate_evidence.py tests/test_taiji_package_windows_real_transport.py tests/test_windows_candidate_evidence.py
git commit -m "feat(packaging): verify Windows candidate review and logs"
```

### Task 6: Gate R3 集成正式 main，再经 Gate R4 构建

**Files:**
- Verify only before R3: feature branch files from Tasks 1—5
- Modify: `tests/python38_linux_packaging_gate.py`
- Runtime state after R4: `/Users/bwb/.local/state/taiji-package/runs/<run-id>/`
- Remote run after R4: `D:\tw\taiji-builds\<formal-main-commit>\<run-id>\`

- [ ] **Step 1: 更新 Python 3.8 固定清单并提交 GREEN gate**

把 `windows_ssh.py`、`import_product_source.py`、`builder_input_package.py`、`safe_tar.py` 和 `candidate_evidence.py` 逐项加入固定清单，不允许用目录扫描代替。先运行 grammar gate；通过后只提交该 gate：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/python38_linux_packaging_gate.py
git diff --check
git add tests/python38_linux_packaging_gate.py
git commit -m "test(packaging): gate real Windows candidate pipeline"
git status --short
```

Expected: gate 退出 0，提交后 worktree clean。没有真实 Python 3.8 时只能报告 grammar gate，通过当前 Python 不得冒充真实 3.8 runtime。

- [ ] **Step 2: 在 R3 前完成分支全门禁**

```bash
bash -n taiji-package
PYTHONPYCACHEPREFIX=/private/tmp/taiji-package-pycache-20260820 python3 -m py_compile scripts/taiji-package-candidate.py packaging/pipeline/cli.py packaging/pipeline/core/errors.py packaging/pipeline/core/registry.py packaging/pipeline/core/state.py packaging/pipeline/core/orchestration.py packaging/pipeline/core/models.py packaging/pipeline/adapters/base.py packaging/pipeline/adapters/kylin_amd64.py packaging/pipeline/adapters/windows_x64.py packaging/pipeline/adapters/windows_ssh.py packaging/windows/import_product_source.py packaging/windows/builder_input_package.py packaging/windows/safe_tar.py packaging/windows/candidate_evidence.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_kylin_candidate_handoff tests.test_taiji_package_state_v2 tests.test_taiji_package_core_boundaries tests.test_taiji_package_orchestration tests.test_windows_legacy_asset_provenance tests.test_windows_packaging_script_contract tests.test_windows_product_probe tests.test_windows_product_import tests.test_windows_builder_input_package tests.test_windows_safe_tar tests.test_windows_candidate_evidence tests.test_taiji_package_candidate tests.test_taiji_package_transport tests.test_taiji_package_windows_adapter tests.test_taiji_package_windows_transport tests.test_taiji_package_windows_real_transport tests.test_linux_golden_orchestrator
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_kylin_packaging_skill tests.test_builder_input_package_contract
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/python38_linux_packaging_gate.py
git diff --check
git status --short --branch
```

Expected: 全部退出 0，功能 worktree clean。

- [ ] **Step 3: 停在 Gate R3，由主 Agent执行标准集成**

主 Agent 必须按开发生命周期展示 feature tip、提交清单、验证、push/PR/CI/merge 影响和正式 main 复验。未获授权或 CI/审查/合并门禁未闭合时，本计划停在“分支已实现，本地验证通过”，不得进入 R4。

- [ ] **Step 4: 从正式 main 重新验证身份和全门禁**

只有标准集成完成后，才在 `/Users/bwb/Documents/工作/taiji-agentv1.0` 验证：branch=`main`、clean、HEAD 为完整 commit、包含集成成果，并重跑 Step 2 的适用门禁。后续所有 `plan/build` 必须从该正式根目录执行；不得从功能 worktree 构建。

- [ ] **Step 5: 首次 plan 必须仍为 MISSING 且无写入**

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
./taiji-package --target windows-x64 plan
```

Expected: source branch=`main`，source commit=`当前正式 main HEAD`，input status=`MISSING`，三件套路径仍不存在；输出 host、remote/local run、三块授权、离线缓存、review/log、停止/恢复点。

- [ ] **Step 6: 停在 Gate R4，等待精确 `BUILD` 后只执行一次**

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
./taiji-package --target windows-x64 build
```

build 内部再次在线 doctor，先显示 formal main commit、三件套 `MISSING`/`REUSABLE` 状态、预期 basename 和三块影响边界，然后要求操作员输入一次 `BUILD`。若为 `MISSING`，确认后才生成三件套，把实际 basename/bytes/SHA 以 write-once 方式写 state/controller log，再继续传输；若为 `REUSABLE`，确认后复核原 identity 且不重建。长步骤只启动一次；保留 session，每 60 秒报告实际 stage，不因无输出重复启动。

- [ ] **Step 7: 只按状态恢复，不重建**

成功时 label=`候选 EXE 已构建`、stage=`CANDIDATE_BUILT`。远端成功而 review/log 取回、验证或本地发布失败时 label=`候选 EXE 取回待恢复`、stage=`FETCH_PENDING`，只允许：

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
./taiji-package fetch --run <run-id>
```

`fetch` 从 state 决定 `windows-x64`；不得重新 doctor、prepare、transfer、PowerShell build 或 ISCC。远端成功前失败为 `FAILED`，不得 fetch。

### Task 7: 把候选证据只固化到私有 state，不修改正式 main

**Files:**
- Runtime only: `/Users/bwb/.local/state/taiji-package/runs/<run-id>/run-state.json`
- Runtime only: `/Users/bwb/.local/state/taiji-package/runs/<run-id>/evidence/windows-candidate-evidence.json`
- Runtime only: `/Users/bwb/.local/state/taiji-package/runs/<run-id>/evidence/windows-candidate-handoff.md`

- [ ] **Step 1: 核 candidate commit 等于构建时正式 main HEAD**

state 必须绑定 target=`windows-x64`、source.branch=`main`、source.commit、target config SHA、输入三件 SHA、artifact basename/bytes/SHA、host、remote/local run、review/log。若 `source.commit` 不等于 R4 开始时记录的正式 main HEAD，不得写成功证据。

- [ ] **Step 2: 原子写外部证据**

成功构建后由 Windows 专属 helper 从同一 terminal state 原子发布 evidence bundle：

```bash
python3 -I -B /Users/bwb/Documents/工作/taiji-agentv1.0/packaging/windows/candidate_evidence.py write --state-root /Users/bwb/.local/state/taiji-package --run <run-id>
```

该命令必须输出 `EVIDENCE_READY`；失败时保留 candidate state 和 staging，可原命令重试，不运行 fetch/build。JSON 固定包含五层：

```text
Source=CURRENT_VERIFIED
Payload=CURRENT_VERIFIED
Installer=CURRENT_VERIFIED
Installed Runtime=NOT_VERIFIED
Interactive Acceptance=NOT_VERIFIED
Production License=NOT_COMPLETED
Release=NOT_EXECUTED
```

handoff 明确未安装、未启动、未验收、未授权生产化、未签名、未发布；历史 1.0.3 只能列为历史线索。只有最终 state 已持久化为 `CANDIDATE_BUILT` 时才创建这两份成功证据；失败或 `FETCH_PENDING` 时两文件必须不存在，实际最高层只记录在 `run-state.json` 和 controller/remote log，不创建同名“成功”handoff。

- [ ] **Step 3: 验证没有任何 Git 修改**

```bash
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 status --short --branch
./taiji-package status --run <run-id>
```

Expected: 正式 `main` clean，HEAD 仍为候选 source commit；证据只存在于 run state。Task 7 禁止修改 runbook、tracked handoff、测试或任何正式 main 文件，不创建提交。
