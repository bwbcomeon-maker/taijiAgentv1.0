# Windows Candidate Fast Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 180 分钟硬时间盒内，从 `codex/cross-platform-package-controller` 的 `7536afe6` 后续 docs-only 计划提交继续，解除 R2 十路径冲突、补完 Plan 4 Task 4—5、在不改变 Kylin 默认菜单的前提下集成本地正式 `main`，并在 `windows-direct` 构建、取回和验证一个绑定正式 `main` 的未签名 Windows x64 候选 EXE。

**Architecture:** 不改变已批准的双 adapter 设计：平台中立 core 不加入 Windows 分支，Windows 输入、SSH/PowerShell、缓存、review 和 EXE 验证仍只在 Windows adapter/transport/helper 中。为满足 2—3 小时墙钟目标，R2、本地输入安全实现和 Windows 缓存补齐并行；正式 `main` 只接收从 `0a4f756d` 开始的 Plan 4 提交，不合并 Plan 1—3 的重复历史。

**Tech Stack:** Python 3.8+ 标准库、Git、Node.js、pytest/unittest、SSH/SCP、Windows PowerShell 5.1、Inno Setup 6

---

## 0. 执行模型、时间盒和成功边界

本计划必须由一个协调 Agent 加三个隔离 worker 并行执行。单模型串行不得承诺 2—3 小时。Lane A、B、C 都可以使用低端模型，但协调 Agent 必须负责身份门禁、提交汇合、正式 `main` 和唯一真实 build session。

| 墙钟 | Lane A | Lane B | Lane C / 主 Agent |
| --- | --- | --- | --- |
| 00:00—00:15 | R2 allowlist RED/GREEN | Task 4 RED | Task 5 support RED；协调 Agent 无覆盖补缓存 |
| 00:15—00:55 | R2 verify/trial/cherry-pick | Task 4 GREEN | PowerShell/transport/evidence support GREEN |
| 00:55—01:25 | 菜单隔离与导入审计 | Task 4 提交后汇合 Task 5 support | Task 5 support 提交与双审 |
| 01:25—02:00 | 规格/质量审查 | `windows_x64.py` 最终接线并提交 | grammar gate 与聚焦回归 |
| 02:00—02:20 | 分支全门禁 | 汇合提交 | 正式 `main` 快速集成与复验 |
| 02:20—02:55 | — | — | 单次真实 build/fetch/evidence |
| 02:55—03:00 | 最终状态和 SHA 报告 | — | 仅在全部成功后 push `main` |

协调 Agent 在开始计时前用 `using-git-worktrees` 创建两个临时执行 worktree；现有主实施 worktree 归 Lane A：

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git branch codex/plan4-task4-fast codex/cross-platform-package-controller
git worktree add /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/plan4-task4-fast codex/plan4-task4-fast
git branch codex/plan4-task5-fast codex/cross-platform-package-controller
git worktree add /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/plan4-task5-fast codex/plan4-task5-fast
```

Lane B 从始至终独占 `packaging/pipeline/adapters/windows_x64.py`：先完成 Task 4，随后把 Lane C 的 GREEN support commit cherry-pick 到 Lane B，再做最终 adapter 接线。Lane C 禁止修改 `windows_x64.py`。协调 Agent 最后把 Lane B 上从 Task 4 开始的连续 GREEN commits 按顺序 cherry-pick 到 Lane A。不得删除这两个 worktree；清理不在本计划时间盒内。

唯一完成标签仍是：

```text
候选 EXE 已构建
```

它只证明 Source、Payload、Installer。以下项目不在本计划：安装、启动、GUI、交互验收、production license、签名、SmartScreen、Tag、Release、Plan 5 旧仓退休。

## Task 0: 固定身份、bundle 和 Windows 只读事实（协调 Agent，5 分钟）

执行前必须重新运行本节命令；值不一致立即停止，不把新值写成期望。

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller
git branch --show-current
git rev-parse HEAD
git rev-parse HEAD^
git log -1 --format='%s'
git status --short --branch
git rev-parse 43f1c51e96ca93ecfc0bba441ba77f85344f68f7^{tree}
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 rev-parse ebd012cbb7f079ba3fb563d4e84109ba91d0275b^{tree}
shasum -a 256 /Users/bwb/.local/state/taiji-package/imports/20260820T-r2-lf-c4b7789a/windows-product-89954e96d23cf43f266197813eb283475d5ff7e1.bundle
python3 -I -B packaging/windows/import_product_source.py probe --target-config packaging/pipeline/targets/windows-x64.json --host windows-direct --product-repo 'D:\tw\source\taijiAgentv1.0' --expected-branch codex/windows-local --expected-tip 89954e96d23cf43f266197813eb283475d5ff7e1 --expected-base 5364233e1297e5f2837382823d4e35a0d114aba7
```

Expected：

```text
branch=codex/cross-platform-package-controller
HEAD parent=7536afe6dbdf0c125e58e8e5dd500bf42b539b1c
latest subject=docs(packaging): define fast Windows candidate completion plan
worktree clean
43f1c51e tree=b43d17b92c19d0471fa13cd687189deece01c5bc
main ebd012cb tree=b43d17b92c19d0471fa13cd687189deece01c5bc
bundle SHA256=d8c015b3da586e9012ca7a292e98b42a628e495074f99acbf89d9fabe5cd6f31
product probe: clean=true, branch=codex/windows-local, tip=89954e96d23cf43f266197813eb283475d5ff7e1, base_present=true, blockers=[]
```

已实时验证的 Windows 事实：

- `windows-direct` 可达，Windows 10 x64、NTFS、约 330 GiB 可用；
- 产品仓 clean，branch=`codex/windows-local`，tip=`89954e96d23cf43f266197813eb283475d5ff7e1`，base 存在；
- PowerShell、Git、tar、Node、npm、Inno Setup 6 存在；
- 正式 cache 当前只缺 `python-runtime` 和 `electron-v39.8.10-win32-x64.zip`；
- 可复用 Python 位于 `D:\tw\build\python-runtime`，实测 CPython 3.11.9、AMD64、64bit；
- 可复用 Electron dist 位于 `D:\tw\source\taijiAgentv1.0\apps\taiji-desktop\node_modules\electron\dist`，实测版本 39.8.10、`electron.exe` SHA256=`9ba4530b08adeae75c13324a95b0fc8e87c5aa2889cfdc894474f8684b9f6c59`。

## 1. 全局纪律

- [ ] 只用 `apply_patch` 修改文件；禁止 `reset/clean/stash/rebase`，禁止覆盖归属不明内容。
- [ ] 每个行为修改先 RED，确认因目标能力缺失失败，再做最小 GREEN。
- [ ] Lane 之间不得同时修改同一文件。Lane B 独占 `windows_x64.py`，Lane C 永远不改该文件。
- [ ] 每个提交前运行聚焦测试、`git diff --check`，并只 `git add` 当前任务明确列出的路径。
- [ ] 任一远端命令只能通过 `/usr/bin/ssh`/`/usr/bin/scp` 参数数组和 EncodedCommand；禁止 `shell=True`。
- [ ] 不下载、不安装；Windows cache 只从本机已验证既有字节复制，且目标必须事先不存在。
- [ ] `FETCH_PENDING` 只允许 fetch/review/validate/publish；不得重新 doctor、prepare、transfer 或 build。
- [ ] 真实构建只从 clean、已复验的正式 `/Users/bwb/Documents/工作/taiji-agentv1.0@main` 启动一次。
- [ ] 任何失败先记录首个稳定失败类别和日志，不反复启动长步骤。

## Task 1: 无下载补齐 Windows 两项缓存（主 Agent，15 分钟，可并行）

**Files:**
- Remote create only: `D:\tw\cache\python-runtime\`
- Remote create only: `D:\tw\cache\electron\electron-v39.8.10-win32-x64.zip`
- Do not modify: product repo、existing npm cache、Inno、PATH、registry

- [ ] **Step 1: 用 EncodedCommand 执行无覆盖准备脚本**

先用 `apply_patch` 把以下内容原样创建为 `/private/tmp/taiji-prepare-existing-windows-cache.ps1`。该临时文件不加入 Git。必须把脚本文本交给现有 `powershell_argv()`；不得直接交给 zsh 展开：

```powershell
$ErrorActionPreference = 'Stop'
$pythonSource = 'D:\tw\build\python-runtime'
$pythonDestination = 'D:\tw\cache\python-runtime'
$electronSource = 'D:\tw\source\taijiAgentv1.0\apps\taiji-desktop\node_modules\electron\dist'
$electronDirectory = 'D:\tw\cache\electron'
$electronDestination = 'D:\tw\cache\electron\electron-v39.8.10-win32-x64.zip'

foreach ($required in @(
  (Join-Path $pythonSource 'python.exe'),
  (Join-Path $pythonSource 'python311._pth'),
  (Join-Path $electronSource 'electron.exe')
)) {
  if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
    throw "existing cache source missing: $required"
  }
}
if (Test-Path -LiteralPath $pythonDestination) {
  throw "refusing to overwrite: $pythonDestination"
}
if (Test-Path -LiteralPath $electronDestination) {
  throw "refusing to overwrite: $electronDestination"
}

New-Item -ItemType Directory -Path $pythonDestination | Out-Null
Copy-Item -Path (Join-Path $pythonSource '*') -Destination $pythonDestination -Recurse
New-Item -ItemType Directory -Path $electronDirectory -Force | Out-Null
Compress-Archive -Path (Join-Path $electronSource '*') -DestinationPath $electronDestination -CompressionLevel Optimal

foreach ($required in @(
  (Join-Path $pythonDestination 'python.exe'),
  (Join-Path $pythonDestination 'python311._pth'),
  $electronDestination
)) {
  if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
    throw "prepared cache member missing: $required"
  }
}
$temporary = Join-Path $env:TEMP ('taiji-electron-cache-check-' + [Guid]::NewGuid().ToString('N'))
try {
  Expand-Archive -LiteralPath $electronDestination -DestinationPath $temporary
  if (-not (Test-Path -LiteralPath (Join-Path $temporary 'electron.exe') -PathType Leaf)) {
    throw 'Electron cache archive lacks root electron.exe'
  }
} finally {
  if (Test-Path -LiteralPath $temporary) {
    Remove-Item -LiteralPath $temporary -Recurse -Force
  }
}
[ordered]@{
  python = (& (Join-Path $pythonDestination 'python.exe') -I -B -c "import platform;print(platform.python_version()+' '+platform.machine())" | Select-Object -Last 1)
  python_sha256 = (Get-FileHash -LiteralPath (Join-Path $pythonDestination 'python.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
  electron_zip_bytes = (Get-Item -LiteralPath $electronDestination).Length
  electron_zip_sha256 = (Get-FileHash -LiteralPath $electronDestination -Algorithm SHA256).Hash.ToLowerInvariant()
} | ConvertTo-Json -Compress
```

在 Lane A 仓库根执行唯一一次：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'import json, subprocess; from pathlib import Path; from packaging.pipeline.adapters.windows_ssh import powershell_argv; target=json.loads(Path("packaging/pipeline/targets/windows-x64.json").read_text(encoding="utf-8")); script=Path("/private/tmp/taiji-prepare-existing-windows-cache.ps1").read_text(encoding="utf-8"); assert len(script) <= 6000, "temporary cache script unexpectedly exceeds EncodedCommand limit"; result=subprocess.run(powershell_argv(target["host_alias"], target["powershell"], script), check=False); raise SystemExit(result.returncode)'
```

Expected：exit 0；Python 输出包含 `3.11.9 AMD64`；记录 Electron ZIP bytes/SHA。失败时保留已创建目标，不重跑、不删除，转人工核验。

- [ ] **Step 2: 只读重跑 builder doctor**

在功能分支入口会先被 `BRANCH_NOT_MAIN` 拦截，因此此时只运行下列精确的 repo-internal 只读调用：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'import json; from pathlib import Path; from packaging.pipeline.adapters.windows_ssh import WindowsSshTransport; target=json.loads(Path("packaging/pipeline/targets/windows-x64.json").read_text(encoding="utf-8")); result=WindowsSshTransport(target, ssh_config=None, command_runner=None).online_doctor(); print(json.dumps(result, ensure_ascii=False, sort_keys=True))'
```

Expected：exit 0，`builder_status=BUILDER_READY`、`blockers=[]`、三个 cache checks 均为 `present=true`。正式 main 集成后必须再用统一入口复验。

## Task 2: 把 R2 allowlist 从错误六路径修正为固定十路径（Lane A，15 分钟）

**Files:**
- Modify: `packaging/windows/import_product_source.py`
- Modify: `tests/test_windows_product_import.py`

- [ ] **Step 1: 写精确十路径 RED**

在 `tests/test_windows_product_import.py` 增加：

```python
EXPECTED_PRODUCT_PATHS = [
    "apps/taiji-desktop/src/main.js",
    "apps/taiji-desktop/src/windows-runtime.js",
    "apps/taiji-desktop/tests/windows-runtime.test.js",
    "apps/taiji-desktop/tests/windows-startup-scope.test.js",
    "hermes-local-lab/config/taiji-default-config.yaml",
    "hermes-local-lab/sources/hermes-agent/taiji_runtime_profile.py",
    "hermes-local-lab/sources/hermes-agent/tests/test_taiji_runtime_profile.py",
    "hermes-local-lab/sources/hermes-webui/api/config.py",
    "hermes-local-lab/sources/hermes-webui/tests/test_ui_visibility_config.py",
    "packaging/windows/diagnose.ps1",
]


class WindowsProductPathContractTests(unittest.TestCase):
    def test_fixed_product_tip_uses_the_reviewed_ten_path_allowlist(self):
        helper = load_helper(self)
        self.assertEqual(helper.ALLOWED_PATHS, EXPECTED_PRODUCT_PATHS)
```

Run：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_product_import
```

Expected：FAIL，显示当前只有六路径；不是导入或语法错误。

- [ ] **Step 2: 只把 `ALLOWED_PATHS` 改成上述十项并转 GREEN**

禁止动态拼接路径、通配符、目录前缀和 `setattr`。重跑同一命令，Expected：`OK`。

- [ ] **Step 3: 提交**

```bash
git add packaging/windows/import_product_source.py tests/test_windows_product_import.py
git commit -m "fix(packaging): accept verified Windows product path set"
```

## Task 3: 完成 R2 verify、trial、产品提交映射和 Kylin 菜单隔离（Lane A，45 分钟）

**Files:**
- Runtime input: `/Users/bwb/.local/state/taiji-package/imports/20260820T-r2-lf-c4b7789a/`
- Product changes: exact ten paths from Task 2
- Create: `packaging/windows/taiji-default-config.yaml`
- Modify: `hermes-local-lab/config/taiji-default-config.yaml`
- Modify: `apps/taiji-desktop/tests/windows-runtime.test.js`
- Create: `tests/test_windows_menu_policy_isolation.py`
- Create: `docs/reviews/2026-08-21-windows-product-source-import.md`

- [ ] **Step 1: 验证现有 LF sidecar bundle 并安装无覆盖 archive ref**

```bash
python3 -I -B packaging/windows/import_product_source.py verify --import-dir /Users/bwb/.local/state/taiji-package/imports/20260820T-r2-lf-c4b7789a --base 5364233e1297e5f2837382823d4e35a0d114aba7 --tip 89954e96d23cf43f266197813eb283475d5ff7e1
python3 -I -B packaging/windows/import_product_source.py install-ref --manifest /Users/bwb/.local/state/taiji-package/imports/20260820T-r2-lf-c4b7789a/product-import.json --repo /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller --ref refs/archive/windows-product/89954e96d23cf43f266197813eb283475d5ff7e1
python3 -I -B packaging/windows/import_product_source.py inventory --manifest /Users/bwb/.local/state/taiji-package/imports/20260820T-r2-lf-c4b7789a/product-import.json
git rev-parse refs/archive/windows-product/89954e96d23cf43f266197813eb283475d5ff7e1
```

Expected：manifest 记录四个单父提交和十条 allowlist；ref 精确等于 tip。

- [ ] **Step 2: 在全新 trial clone 逐提交应用**

要求 `/private/tmp/taiji-win-product-trial-89954e96-fast` 不存在：

```bash
git clone --no-local --branch codex/cross-platform-package-controller /Users/bwb/Documents/工作/taiji-agentv1.0 /private/tmp/taiji-win-product-trial-89954e96-fast
git -C /private/tmp/taiji-win-product-trial-89954e96-fast fetch /Users/bwb/.local/state/taiji-package/imports/20260820T-r2-lf-c4b7789a/windows-product-89954e96d23cf43f266197813eb283475d5ff7e1.bundle 89954e96d23cf43f266197813eb283475d5ff7e1:refs/archive/windows-product/89954e96d23cf43f266197813eb283475d5ff7e1
git -C /private/tmp/taiji-win-product-trial-89954e96-fast cherry-pick 8b2fb10bd219695e6643d9d10f764f16e6b47799
git -C /private/tmp/taiji-win-product-trial-89954e96-fast cherry-pick 39f7e908a886effaa1bcba773c84e313ff2bed38
git -C /private/tmp/taiji-win-product-trial-89954e96-fast cherry-pick a2206deedb029a1cf4fa221b1c794f6900157b1c
git -C /private/tmp/taiji-win-product-trial-89954e96-fast cherry-pick 89954e96d23cf43f266197813eb283475d5ff7e1
```

运行：

```bash
node --check /private/tmp/taiji-win-product-trial-89954e96-fast/apps/taiji-desktop/src/main.js
node --check /private/tmp/taiji-win-product-trial-89954e96-fast/apps/taiji-desktop/src/windows-runtime.js
node --test /private/tmp/taiji-win-product-trial-89954e96-fast/apps/taiji-desktop/tests/windows-runtime.test.js /private/tmp/taiji-win-product-trial-89954e96-fast/apps/taiji-desktop/tests/windows-startup-scope.test.js
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q /private/tmp/taiji-win-product-trial-89954e96-fast/hermes-local-lab/sources/hermes-agent/tests/test_taiji_runtime_profile.py /private/tmp/taiji-win-product-trial-89954e96-fast/hermes-local-lab/sources/hermes-webui/tests/test_ui_visibility_config.py
```

Expected：全部通过。冲突或失败保留 trial，停止，不选 ours/theirs。

- [ ] **Step 3: 在真实功能分支按同一顺序 cherry-pick**

每次前后要求 clean，并把每组三条输出记录到审计草稿：

```bash
git rev-parse HEAD
git show --format=email --patch 8b2fb10bd219695e6643d9d10f764f16e6b47799 | git patch-id --stable
git cherry-pick 8b2fb10bd219695e6643d9d10f764f16e6b47799
git rev-parse HEAD

git show --format=email --patch 39f7e908a886effaa1bcba773c84e313ff2bed38 | git patch-id --stable
git cherry-pick 39f7e908a886effaa1bcba773c84e313ff2bed38
git rev-parse HEAD

git show --format=email --patch a2206deedb029a1cf4fa221b1c794f6900157b1c | git patch-id --stable
git cherry-pick a2206deedb029a1cf4fa221b1c794f6900157b1c
git rev-parse HEAD

git show --format=email --patch 89954e96d23cf43f266197813eb283475d5ff7e1 | git patch-id --stable
git cherry-pick 89954e96d23cf43f266197813eb283475d5ff7e1
git rev-parse HEAD
git status --short --branch
```

四个 cherry-pick 任一冲突执行 `git cherry-pick --abort` 并停止，不 reset。每行映射必须记录 old SHA、stable patch-id、新 SHA 和 exact paths。

- [ ] **Step 4: 写 Kylin 菜单兼容 RED**

新增 Python/Node 合同，精确断言：

```python
shared = yaml.safe_load(Path("hermes-local-lab/config/taiji-default-config.yaml").read_text())
windows = yaml.safe_load(Path("packaging/windows/taiji-default-config.yaml").read_text())
assert shared["webui"]["feature_visibility"]["nav"]["profiles"] is True
assert windows["webui"]["feature_visibility"]["nav"]["profiles"] is False
normalized = copy.deepcopy(windows)
normalized["webui"]["feature_visibility"]["nav"]["profiles"] = True
assert normalized == shared
```

该测试还要逐项扫描 `taijiagent 打包交付/99_本机_准备制包输入包.sh`、`taijiagent 打包交付/00_制包机_生成离线交付包.sh`、`taijiagent 打包交付/01_制包机_发布预检.sh` 和 `packaging/linux/deb/build-deb.sh`，断言它们不引用 `packaging/windows/taiji-default-config.yaml`；其中 `build-deb.sh` 仍引用 shared config。Stage 的 Windows 专属复制由 Task 5 Lane C 实现。

- [ ] **Step 5: 最小 GREEN 隔离**

1. 把产品提交后的 false 版本完整保存为 `packaging/windows/taiji-default-config.yaml`；
2. 只把 shared `hermes-local-lab/config/taiji-default-config.yaml` 的 `profiles` 恢复为 `true`；
3. 把 `windows-runtime.test.js` 的菜单策略 fixture 改为读取 `packaging/windows/taiji-default-config.yaml`；
4. 不修改 Linux 99/00/01、Linux build 脚本或通用 core。

运行产品与 Kylin 回归后提交：

```bash
node --check apps/taiji-desktop/src/main.js
node --check apps/taiji-desktop/src/windows-runtime.js
node --test apps/taiji-desktop/tests/windows-runtime.test.js apps/taiji-desktop/tests/windows-startup-scope.test.js
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q hermes-local-lab/sources/hermes-agent/tests/test_taiji_runtime_profile.py hermes-local-lab/sources/hermes-webui/tests/test_ui_visibility_config.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_menu_policy_isolation tests.test_linux_golden_orchestrator
git add packaging/windows/taiji-default-config.yaml hermes-local-lab/config/taiji-default-config.yaml apps/taiji-desktop/tests/windows-runtime.test.js tests/test_windows_menu_policy_isolation.py
git commit -m "fix(packaging): isolate Windows menu policy from Kylin"
```

- [ ] **Step 6: 写导入审计并提交**

先运行下列命令；它会输出可直接复制到审计中的唯一 manifest 摘要行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'import hashlib; from pathlib import Path; path=Path("/Users/bwb/.local/state/taiji-package/imports/20260820T-r2-lf-c4b7789a/product-import.json"); print("product-import-manifest-sha256: " + hashlib.sha256(path.read_bytes()).hexdigest())'
```

审计必须写入：base/tip、上条命令输出的 exact manifest SHA、十路径、四行 old/patch-id/trial/branch 映射、全部命令结果、Kylin 菜单隔离说明。禁止把正则、方括号或占位符写进审计。

```bash
git add docs/reviews/2026-08-21-windows-product-source-import.md
git commit -m "docs(packaging): record Windows product commit mapping"
```

## Task 4: 冻结 Windows tar 输入并安全解压（Lane B，55 分钟）

**Canonical contract:** `docs/superpowers/plans/2026-08-20-windows-product-import-real-candidate.md` Task 4，任何字段、失败类别和 hostile tar 边界不得放宽。

**Files:**
- Create: `packaging/windows/builder_input_package.py`
- Create: `packaging/windows/safe_tar.py`
- Create: `tests/test_windows_builder_input_package.py`
- Create: `tests/test_windows_safe_tar.py`
- Modify: `packaging/pipeline/adapters/windows_x64.py`
- Modify: `.gitignore`

- [ ] **Step 1: 写 RED**

测试必须用 `repo_root = Path(__file__).resolve().parents[1]` 计算两个 helper，再对每个 helper 先 `assertTrue(helper_path.is_file())`，随后从 `TemporaryDirectory()` 的非仓库 cwd 运行 `[/usr/bin/python3, -I, -B, str(helper_path.resolve()), --help]`。这样同一测试在 worker、Lane A 和正式 main 都检查当前 checkout，禁止硬编码某个 worktree。覆盖 MISSING/REUSABLE/PARTIAL、错误 commit/tree/version/hash、owner/mode/link、无覆盖 create、dirty/非 main、VERSION 与 desktop package version 不一致，以及 Plan 4 全部 hostile tar。

`.gitignore` RED 还要断言三项运行时输入不会污染正式 main：

```text
/taijiagent-windows-builder-input-*.tar.gz
/taijiagent-windows-builder-input-*.manifest.json
/taijiagent-windows-builder-input-*.tar.gz.sha256
```

Run：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_builder_input_package tests.test_windows_safe_tar
```

Expected：FAIL，两个 helper 不存在。

- [ ] **Step 2: 实现最小 GREEN**

固定 public callable 名称和参数为 `inspect_input(repo, source_commit)`、`create_input(repo, source_commit, target_config_path, asset_provenance_path)`、`verify_input(repo, source_commit)`、`main(argv=None)`；不得再增加第二套入口。

固定原则：只使用标准库；`git archive --format=tar` 的最后一个参数必须是 plan 中的完整 source commit；gzip `mtime=0`；manifest/sidecar exact schema；不覆盖、不修 partial；`plan` 不创建输入；`build` 确认后才 create。`safe_tar.py` 先验证全部 member 再创建目标，目标必须不存在，只允许 regular file/directory，mode 由 helper 固定。

Windows plan 新增 exact `controller_bootstrap.safe_tar={source_path,remote_path,bytes,sha256,python_path}`；source bytes 必须等于从 plan 的完整 source commit 执行 `git show source_commit:packaging/windows/safe_tar.py` 得到的 bytes。`prepare_input()` 调用 helper；`inspect_input()` 不再只看三文件存在，必须完整 verify。

- [ ] **Step 3: GREEN、回归、提交**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_builder_input_package tests.test_windows_safe_tar tests.test_taiji_package_windows_adapter tests.test_taiji_package_orchestration
git diff --check
git add .gitignore packaging/windows/builder_input_package.py packaging/windows/safe_tar.py packaging/pipeline/adapters/windows_x64.py tests/test_windows_builder_input_package.py tests/test_windows_safe_tar.py
git commit -m "feat(packaging): freeze and safely extract Windows builder input"
```

## Task 5: 让 PowerShell 合同真实可执行，并完成 real transport/evidence（Lane C support + Lane B adapter，60—75 分钟）

**Canonical contract:** 原 Plan 4 Task 5；本任务只修复已确认的“静态文字通过但真机会失败”的实现缺口，不改变 schema、阶段或失败类别。

**Files:**
- Modify: `packaging/windows/Initialize-CandidateSession.ps1`
- Modify: `packaging/windows/Stage-CandidatePayload.ps1`
- Modify: `packaging/windows/Build-CandidateReview.ps1`
- Modify: `packaging/pipeline/adapters/windows_ssh.py`
- Create: `packaging/windows/candidate_evidence.py`
- Modify: `tests/test_windows_packaging_script_contract.py`
- Modify: `tests/test_taiji_package_windows_real_transport.py`
- Create: `tests/test_windows_candidate_evidence.py`
- Lane B only modify after Task 4: `packaging/pipeline/adapters/windows_x64.py`
- Lane B only modify: `tests/test_taiji_package_windows_adapter.py`

- [ ] **Step 1: 写实机语义 RED**

Lane C 先逐项复制原 Plan 4 Task 5 的 RED 矩阵，不得遗漏：`REAL_BUILD_STAGES`/`REAL_FETCH_STAGES`、review exact set/schema、marker-last、PE/version/NotSigned、SCP 中断与 `FETCH_PENDING`、evidence owner/mode/link/原子发布/幂等、从外部 cwd 运行当前 checkout 的 absolute `candidate_evidence.py --help`。随后再增加以下已由真机静态审计发现的语义合同；本步骤禁止修改或测试 `windows_x64.py`：

1. Stage 从 safe source 组装 Electron 根、把 `electron.exe` 重命名为 `TaijiAgent.exe`，只把 desktop `package.json/src` 放入 `resources/app`，不把 `node_modules/tests` 放入 payload；
2. Stage 从 safe source 复制 Agent/WebUI、把 Windows 专属 config 放入 payload 内原 shared config 的相对路径、复制 `diagnose.ps1`，重写 `python311._pth`，然后跑私有 Python import/menu gate；
3. session/package manifest 的七个 `tools` 都是 `{path,bytes,sha256,version}`，version 只要求非空，不错误套用三段 semver；
4. `host_facts_sha256` 来自 finalized plan，不得等于 cache observation SHA；
5. package/remote state 的 `started_at/finished_at` 都是 UTC，不得写 source commit；
6. success remote log 非空并逐项记录七个 formal checks；
7. ISCC `/DOutputBaseFilename` 不含 `.exe`，最终 artifact basename 只含一个 `.exe`；
8. review exact set、Inno x86 PE32 bootstrap 身份、独立的 x64 payload 证据、版本、NotSigned、marker-last 和 fetch-only 合同保持不变。

Run：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_packaging_script_contract tests.test_taiji_package_windows_real_transport tests.test_windows_candidate_evidence
```

Expected：FAIL，失败分别命中上述现存缺口。

- [ ] **Step 2: 实现 PowerShell 最小 GREEN**

禁止恢复旧仓 Git 依赖。只从已安全解压的 `$session.paths.source_root` 读源；所有 staging 位于唯一 remote run；共享 cache 只读。PowerShell 在运行外部工具前记录实际 path/bytes/SHA/non-empty version，输出 canonical JSON；成功日志逐项 append，`.build-success` 最后创建。

- [ ] **Step 3: 实现 real transport 的五个阶段方法**

`WindowsSshTransport` 必须直接实现且只实现五个执行方法：`create_remote_run(self, plan)`、`transfer_input(self, plan)`、`verify_remote_input(self, plan)`、`build_remote_candidate(self, plan)`、`fetch(self, plan, staging_dir)`。

固定顺序：unique run → transfer triplet/observation/safe_tar → bootstrap/hash/schema → safe extract → initialize/stage/build PowerShell → fetch review → fetch log。SCP 中断可重试；远端 build 成功后任何本地失败必须进入 `FETCH_PENDING`。

- [ ] **Step 4: 实现 candidate evidence**

固定 public API 为 `build_evidence_payload(state)`、`render_handoff(state)`、`publish_evidence_bundle(run_dir, state)`、`main(argv=None)`；不得让 common core 或 Kylin adapter import 此 helper。

只有 `windows-x64 + CANDIDATE_BUILT` 可发布。原子目录内只能有 `windows-candidate-evidence.json` 和 `windows-candidate-handoff.md`；Source/Payload/Installer=`CURRENT_VERIFIED`，Installed Runtime/Interactive Acceptance=`NOT_VERIFIED`，Production License=`NOT_COMPLETED`，Release=`NOT_EXECUTED`。

- [ ] **Step 5: Lane C support GREEN、回归、提交**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_windows_packaging_script_contract tests.test_taiji_package_windows_real_transport tests.test_windows_candidate_evidence
git diff --check
git add packaging/windows/Initialize-CandidateSession.ps1 packaging/windows/Stage-CandidatePayload.ps1 packaging/windows/Build-CandidateReview.ps1 packaging/pipeline/adapters/windows_ssh.py packaging/windows/candidate_evidence.py tests/test_windows_packaging_script_contract.py tests/test_taiji_package_windows_real_transport.py tests/test_windows_candidate_evidence.py
git commit -m "feat(packaging): implement Windows build transport and evidence"
```

Expected：聚焦测试全部通过、commit 只含上述 support 文件。协调 Agent 立即对该 GREEN commit 做规格与 P0/P1 代码质量审查。

- [ ] **Step 6: Lane B 汇合 support，再写 adapter RED/GREEN**

Lane B 的 Task 4 commit 已完成且 worktree clean 后执行：

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/plan4-task4-fast
git log -1 --format='%s' codex/plan4-task5-fast
git cherry-pick codex/plan4-task5-fast
```

Expected subject=`feat(packaging): implement Windows build transport and evidence`。然后只在 `tests/test_taiji_package_windows_adapter.py` 写 RED，锁死：Task 4 input helper 被 adapter 调用、finalized plan 的 `safe_tar` bootstrap 被传给 transport、真实 review validator 接受七个 non-empty tool versions 但仍精确拒绝缺字段/错摘要。确认 RED 后只修改 `windows_x64.py` 转 GREEN。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_package_windows_adapter tests.test_taiji_package_windows_transport tests.test_taiji_package_windows_real_transport tests.test_taiji_package_transport tests.test_windows_builder_input_package tests.test_windows_safe_tar tests.test_windows_candidate_evidence tests.test_taiji_package_core_boundaries tests.test_taiji_package_orchestration
git diff --check
git add packaging/pipeline/adapters/windows_x64.py tests/test_taiji_package_windows_adapter.py
git commit -m "feat(packaging): verify Windows candidate review and logs"
```

Expected：聚焦测试全部通过。协调 Agent 对 Task 4、support、最终 adapter 三个 GREEN commits 分别完成规格与 P0/P1 代码质量审查后才允许汇合 Lane A。

## Task 6: Python 3.8 gate、全回归和双审（协调 Agent，40 分钟）

**Files:**
- Modify: `tests/python38_linux_packaging_gate.py`

- [ ] **Step 0: 把 Lane B 的三个 GREEN commits 汇合到 Lane A**

Lane A 的 Task 3 已提交且 clean 后运行：

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller
git rev-list --count codex/cross-platform-package-controller..codex/plan4-task4-fast
git log --reverse --format='%s' codex/cross-platform-package-controller..codex/plan4-task4-fast
```

Expected：count=`3`，subject 顺序精确为：

```text
feat(packaging): freeze and safely extract Windows builder input
feat(packaging): implement Windows build transport and evidence
feat(packaging): verify Windows candidate review and logs
```

然后逐个执行，禁止用 merge：

```bash
git cherry-pick codex/plan4-task4-fast~2
git cherry-pick codex/plan4-task4-fast~1
git cherry-pick codex/plan4-task4-fast
git status --short --branch
```

任一冲突执行 `git cherry-pick --abort` 并停止；成功后 Lane A clean。

- [ ] **Step 1: 把五个 Plan 4 Python 文件逐项加入固定 grammar 清单**

```text
packaging/pipeline/adapters/windows_ssh.py
packaging/windows/import_product_source.py
packaging/windows/builder_input_package.py
packaging/windows/safe_tar.py
packaging/windows/candidate_evidence.py
```

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/python38_linux_packaging_gate.py
git diff --check
git add tests/python38_linux_packaging_gate.py
git commit -m "test(packaging): gate real Windows candidate pipeline"
```

没有 Python 3.8 时只记录“grammar gate 通过，真实 3.8 runtime 未验证”，不得下载安装。

- [ ] **Step 2: 运行分支全门禁**

```bash
bash -n taiji-package
PYTHONPYCACHEPREFIX=/private/tmp/taiji-package-pycache-20260821 python3 -m py_compile scripts/taiji-package-candidate.py packaging/pipeline/cli.py packaging/pipeline/core/errors.py packaging/pipeline/core/registry.py packaging/pipeline/core/state.py packaging/pipeline/core/orchestration.py packaging/pipeline/core/models.py packaging/pipeline/adapters/base.py packaging/pipeline/adapters/kylin_amd64.py packaging/pipeline/adapters/windows_x64.py packaging/pipeline/adapters/windows_ssh.py packaging/windows/import_product_source.py packaging/windows/builder_input_package.py packaging/windows/safe_tar.py packaging/windows/candidate_evidence.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_kylin_candidate_handoff tests.test_taiji_package_state_v2 tests.test_taiji_package_core_boundaries tests.test_taiji_package_orchestration tests.test_windows_legacy_asset_provenance tests.test_windows_packaging_script_contract tests.test_windows_menu_policy_isolation tests.test_windows_product_probe tests.test_windows_product_import tests.test_windows_builder_input_package tests.test_windows_safe_tar tests.test_windows_candidate_evidence tests.test_taiji_package_candidate tests.test_taiji_package_transport tests.test_taiji_package_windows_adapter tests.test_taiji_package_windows_transport tests.test_taiji_package_windows_real_transport tests.test_linux_golden_orchestrator
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_kylin_packaging_skill tests.test_builder_input_package_contract
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/python38_linux_packaging_gate.py
node --check apps/taiji-desktop/src/main.js
node --check apps/taiji-desktop/src/windows-runtime.js
node --test apps/taiji-desktop/tests/windows-runtime.test.js apps/taiji-desktop/tests/windows-startup-scope.test.js
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q hermes-local-lab/sources/hermes-agent/tests/test_taiji_runtime_profile.py hermes-local-lab/sources/hermes-webui/tests/test_ui_visibility_config.py
git diff --check
git status --short --branch
```

Expected：全部 exit 0，worktree clean。

- [ ] **Step 3: 每个 GREEN commit 做两次审查**

规格审查逐条对照本计划和原 Plan 4；代码质量审查只报 P0/P1。任一 P0/P1 先写 RED、修复、重跑相关与全门禁，再进入集成。

## Task 7: 不合并重复历史，快速集成本地正式 main（协调 Agent，20 分钟）

用户已明确选择开发期本地直整合、跳过 PR/CI。本任务仍保留可恢复集成分支，不 force push。

- [ ] **Step 1: 证明 patch 基线树仍完全一致**

```bash
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 rev-parse main^{tree}
git -C /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller rev-parse 43f1c51e96ca93ecfc0bba441ba77f85344f68f7^{tree}
git -C /Users/bwb/Documents/工作/taiji-agentv1.0 status --short --branch
```

两个 tree 必须都为 `b43d17b92c19d0471fa13cd687189deece01c5bc`，正式 main clean。否则停止。

- [ ] **Step 2: 从 main 建本地集成分支并只 cherry-pick Plan 4 区间**

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git switch -c codex/windows-candidate-fast-integration main
git cherry-pick 0a4f756d217dec5fbea75855014fd73429aa777c^..codex/cross-platform-package-controller
```

禁止 `git merge codex/cross-platform-package-controller`，否则会重新引入 Plan 1—3 的重复历史。

- [ ] **Step 3: 在集成分支重跑 Task 6 全门禁**

全部通过后：

```bash
git switch main
git merge --ff-only codex/windows-candidate-fast-integration
git status --short --branch
```

Expected：main clean，包含 Plan 4 全部提交。

## Task 8: 正式 main 单次真实构建、恢复和证据（主 Agent，30—45 分钟）

### Step 0: 真实构建前最小充分验证裁决（2026-08-21）

在正式 doctor/build 前先完成以下收敛；本节优先于本任务后文的旧轮询口径：

- **必须保留：** source commit/tree 与缓存 observation 身份绑定、共享缓存只读、最终 payload manifest 和文件 SHA256、安全卫生检查、Electron/Python 实际运行检查，以及 `FETCH_PENDING` 只能 fetch；
- **每候选一次：** 隔离 Stage、Stage GREEN 后的 Inno 预演、二者通过后的正式 doctor/build、候选 EXE 取回后的完整回归；
- **应优化：** Stage 持久化 stdout/stderr/exit code/开始结束时间/失败阶段；UTF-8 byte-order 排序改为语义等价 O(n log n)；共享缓存直接作为只读输入，取消共享缓存到临时缓存的整树复制与重复逐文件摘要，Python runtime 只复制到最终 payload；
- **可以删除：** 45 秒 SSH 轮询、因终端输出丢失而整轮重跑、重复复制相同文件、重复泛化审查、第三个 Stage 或并行 build。
- **真实主机 PE 裁决：** Inno Setup 生成的安装器 bootstrap 是 x86 PE32（machine=`0x014c`、optional magic=`0x10b`）；`windows-x64` 目标身份由 Electron `win32-x64` 实际运行检查和 Inno 的 `ArchitecturesAllowed=x64compatible`、`ArchitecturesInstallIn64BitMode=x64compatible` 独立证明。本裁决优先于旧计划中要求安装器 bootstrap 为 AMD64 PE32+（`0x8664`、`0x20b`）的文字，不放宽 x64 payload 合同。
- **版本裁决：** Windows `VersionInfo` 可能返回带尾随空格的固定宽度字符串；比较前只做 `.Trim()`，随后仍与 `$Version.0` 精确比较。

执行顺序固定为：上述三项分别完成聚焦 RED→最小 GREEN；只运行一次隔离 Stage；只运行一次 Inno 预演；二者通过后再运行一次正式 doctor/build；候选 EXE 成功取回后才运行一次完整回归。若只读检查发现 Stage 仍在运行，不得打断或并发；若已结束，先读取持久化结果，不得因 SSH 输出缺失盲目重跑。

- [ ] **Step 1: 从正式 main 运行统一 online doctor**

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
./taiji-package --target windows-x64 --json doctor --online
```

Expected：controller=`CONTROLLER_READY`、builder=`BUILDER_READY`、blockers 空、三个 cache checks true。不是 READY 则停止，不进入 build。

- [ ] **Step 2: plan 必须无写入且 input=MISSING**

```bash
./taiji-package --target windows-x64 plan
git status --short --branch
```

Expected：source branch=`main`、source commit=当前 main HEAD、input=`MISSING`；Git clean。

- [ ] **Step 3: build 只启动一次**

以 TTY 启动：

```bash
./taiji-package --target windows-x64 build
```

只在程序自己的确认提示出现后输入一次：

```text
BUILD
```

不得使用固定 45/60 秒 SSH 轮询；以持久化阶段结果和本地长命令完成状态为证据，不得重新执行 build。成功必须得到 stage=`CANDIDATE_BUILT`、label=`候选 EXE 已构建`。

- [ ] **Step 4: 仅在 FETCH_PENDING 时恢复**

从 build 输出逐字复制实际 run ID，在同一个 TTY 中执行下列两行；`read` 出现等待后只粘贴一次该 run ID：

```bash
read -r TAIJI_RUN_ID
./taiji-package fetch --run "$TAIJI_RUN_ID"
```

不得按目录时间猜测 run ID。

若远端尚未 `REMOTE_BUILD_SUCCEEDED`，禁止 fetch；若 fetch 又触发 doctor/prepare/transfer/build，立即停止并判定合同回归。

- [ ] **Step 5: 写候选证据并验证 Git 不变**

在同一个 TTY 中执行以下命令；`read` 出现等待后粘贴与 build 完全相同的 run ID：

```bash
read -r TAIJI_RUN_ID
python3 -I -B /Users/bwb/Documents/工作/taiji-agentv1.0/packaging/windows/candidate_evidence.py write --state-root /Users/bwb/.local/state/taiji-package --run "$TAIJI_RUN_ID"
./taiji-package status --run "$TAIJI_RUN_ID"
git status --short --branch
```

Expected：`EVIDENCE_READY`；Git clean；EXE basename/bytes/SHA 与 review、sidecar、state 完全一致。

- [ ] **Step 6: 最终本地复验后才同步 GitHub**

```bash
git log -1 --format='%H %s'
git status --short --branch
git push origin main
```

禁止 force push、Tag、Release。push 失败只报告 GitHub 未同步，不影响已经验证的本地候选证据。

## 3. 硬停止条件

- 当前身份、R2 base/tip、bundle SHA、产品仓 clean、Windows cache 来源版本任一漂移；
- trial 或真实分支 cherry-pick 冲突；
- 为通过测试需要修改 Linux 99/00/01 或把 Windows 分支塞入 common core/Kylin transport；
- safe tar、review exact set、PE/版本/NotSigned、fetch-only 任一合同需要放宽；
- Windows cache 需要联网下载或安装；
- 正式 main 不 clean、不是当前 source commit，或输入运行文件未被 ignore；
- 同一真实 build 已经启动但暂时无输出；
- 140 分钟到时仍未进入唯一 build session。此时停止并报告最高已验证层级，因为剩余 40 分钟已不足以可靠完成构建、取回和证据闭环。
- 180 分钟到时唯一 build session 仍在运行：不得杀进程或启动第二次，只报告 `BUILD_RUNNING`、run ID 和最后阶段；不得把它称为候选 EXE。

## 4. 最终交付报告模板

```text
branch/main commit:
feature tip:
R2 import manifest SHA256:
Windows host facts SHA256:
cache requirements/observation SHA256:
input archive/manifest/sidecar SHA256:
run id:
EXE basename/bytes/SHA256:
测试命令与测试数量:
已验证: Source / Payload / Installer
未验证: Installed Runtime / Interactive Acceptance / Production License / Signing / Release
GitHub main 同步状态:
阻塞或剩余风险:
```
