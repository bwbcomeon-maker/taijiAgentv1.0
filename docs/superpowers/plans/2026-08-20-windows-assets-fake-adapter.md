# Windows Assets and Fake Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Use `test-driven-development` before production changes and `verification-before-completion` before reporting success. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在唯一正式仓中，以可复核的 Git object 证据吸收三项 Windows 快车道资产，新增 `windows-x64` target、adapter、静态 PowerShell/Inno 合同和完整 fake 构建/恢复链；本计划不连接 Windows、不执行 PowerShell/Inno、不生成真实 EXE。

**Architecture:** 旧 Windows 仓只作为固定 commit 的 Git object 来源，不参与最终运行。专用 verifier 将 hard-coded truth 与 source Git object、provenance lock、仓内 snapshot 四方核对。Windows adapter 复用 Plan 2 已完成的平台中立 core，通过显式 target 注册、共享 fake fixture、七文件 review exact set 和单独日志取回证明编排合同。候选 source 被定义为 tar 解压后的无 `.git` 普通目录；所有 Windows 脚本只接受显式路径和摘要，不执行 Git。离线缓存按提交的 requirements 和每轮只读 observation 绑定，缺项只允许 `WINDOWS_CACHE_MISSING/BLOCKED`。

**Tech Stack:** Python 3.8+ 标准库、PowerShell 5.1 静态脚本、Inno Setup 6 脚本、Git、`unittest`

---

## 0. 开始条件、允许范围与硬停止条件

本计划只能在 Plan 2 完成后开始。每个 Task 开始前执行：

```bash
git branch --show-current
git status --short
git log -1 --format='%H %s'
git merge-base --is-ancestor a5a36849bca009d1cfb07ac2309532a502c6bd70 HEAD
```

Expected：

- branch=`codex/cross-platform-package-controller`；
- worktree clean；
- Plan 2 最后一个提交 subject=`test(packaging): gate cross-platform candidate core`；
- `a5a36849bca009d1cfb07ac2309532a502c6bd70` 是 HEAD 祖先。

任一不满足立即停止，不在不明来源上继续。

进入 Task 1 前必须重跑 Plan 2 的完整终门，不得只依赖 commit subject：

```bash
bash -n taiji-package
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q \
  tests.test_kylin_candidate_handoff \
  tests.test_taiji_package_target_dispatch \
  tests.test_taiji_package_state_v2 \
  tests.test_taiji_package_core_boundaries \
  tests.test_taiji_package_orchestration \
  tests.test_taiji_package_candidate \
  tests.test_taiji_package_transport \
  tests.test_linux_golden_orchestrator
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q \
  tests.test_taiji_kylin_packaging_skill \
  tests.test_builder_input_package_contract
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/python38_linux_packaging_gate.py
git diff --check
```

Expected：全部退出 0，unittest=`OK`，且仍 clean。任一 core/Kylin/handoff 合同回归都立即停止。

本计划允许：

- 读取 `/Users/bwb/Documents/工作/taiji-agentv1.0-win/.git` 中固定 commit 的三个 Git object；
- 为这三个 object 创建仓内 snapshot、provenance lock 和专用 verifier；
- 新增 `windows-x64` target、adapter、纯内存 fake、静态 PowerShell/Inno 合同和测试；
- 修改通用 registry/CLI 的固定映射和 Python 3.8 grammar gate；
- 运行本地 Python/Git/Bash 静态测试。

本计划禁止：

- `/usr/bin/ssh`、`/usr/bin/scp`、任何 SSH alias 探测；
- 执行 `powershell.exe`、`pwsh`、`ISCC.exe`、Inno 编译、真实 npm/Electron/Python staging；
- 生成真实 Windows 输入三件套或真实 EXE；
- 访问或修改 Windows 主机、安装/卸载/启动应用、图形验收、签名、发布；
- 从旧仓 dirty worktree 递归复制文件、merge/cherry-pick 旧仓历史；
- push、PR、merge、tag、Release 或删除旧仓/旧 worktree；
- 修改 `packaging/linux/**`、`taijiagent 打包交付/**` 或 `99/00/01`。

fake 测试中的 runner 一旦收到 `/usr/bin/ssh`、`/usr/bin/scp`、`powershell.exe`、`pwsh` 或 `ISCC.exe` 立即使测试失败。selected-object verifier 是唯一允许调用本地 `/usr/bin/git` 的新增路径。

平台来源规则固定为：

- 内置 `windows-x64` 的 `allowed_source_branches` **只能**是 `["main"]`；
- 当前 feature branch 只允许开发和 fake 验证，不能生成正式 Windows 输入或候选；
- 真实候选必须等后续计划将成果进入正式 `main` 后，再从该 `main` commit 冻结输入。

任何 Kylin CLI、v1 state、v1 `FETCH_PENDING`、三件套验证或 fake transport 行为回归时立即停止，不以修改旧断言的方式掩盖回归。

---

## 1. 固定数据合同

以下合同不是示例；实施时必须逐字段照此落地。变更合同必须先修改总设计和后续真实候选计划，不能只改测试让其通过。

### 1.1 唯一远程 run 布局

```text
D:\tw\taiji-builds\<source-commit>\<run-id>\
├── input\
├── source\
├── staging\
│   ├── cache\
│   └── payload\
├── output\
├── review\
└── logs\
    └── remote-build.log
```

约束：

- `<source-commit>` 是 40 位 lowercase hex；
- `<run-id>` 匹配 `^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{8}$`；
- run 根、各子目录和 review 必须全新，不覆盖、不复用；
- `source\` 是安全解压 tar 后的普通目录，必须不存在 `.git` 文件、目录或 gitlink；
- `D:\tw\cache` 只读；使用前复制到本轮 `staging\cache`，构建只读写本轮副本；
- 失败保留唯一 run 和日志，不自动清理。

### 1.2 review exact set 与单独日志

`review\` 根只允许以下七个 regular file，不允许目录、symlink/reparse point、hardlink、alternate data stream、额外文件或缺失文件：

```text
TaijiAgent-Setup-<version>-win-x64.exe
TaijiAgent-Setup-<version>-win-x64.exe.sha256
taiji-package-manifest.json
formal-build-tests.log
构建报告.txt
.build-success
run-state.json
```

`logs\remote-build.log` 不属于 review exact set，必须以独立的 `fetch-log` 阶段取回。本地只有在 `fetch-review` 和 `fetch-log` 都成功后才可进入 `local-review-verify`。

### 1.3 canonical JSON

所有 JSON 使用 UTF-8、无 BOM、对象 key 排序、`ensure_ascii=False`、分隔符 `(',', ':')`。用于 SHA256 的 canonical bytes **无尾随换行**；落盘文件为同一 canonical bytes 加一个 `\n`。任何未知 key、缺 key、错误类型、uppercase hash、路径逃逸或非 canonical 文件都失败。

Python 定义必须只有一个：

```python
def canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
```

### 1.4 内嵌 payload manifest

不新增第八个独立 `payload-manifest.json`。`taiji-package-manifest.json` 的 `payload` 对象内嵌排序后的 entries，并绑定 canonical payload manifest SHA。

计算 `payload.manifest_sha256` 时，先构造以下 exact object：

```json
{
  "entries": [
    {"bytes": 123, "path": "relative/posix/path", "sha256": "<64 lowercase hex>"}
  ],
  "file_count": 1,
  "schema": "taiji-windows-payload-manifest/v1",
  "source_commit": "<40 lowercase hex>",
  "source_tree": "<40 lowercase hex>",
  "total_bytes": 123
}
```

`entries` 按 UTF-8 encoded POSIX relative path 升序；path 必须是 NFC、使用 `/`、非空、不以 `/` 开头、不含 `.`、`..`、反斜杠、冒号、NUL、Windows 保留设备名或尾随点/空格。`manifest_sha256=SHA256(canonical_json_bytes(上述 object))`。package manifest 中的 `payload` 等于上述字段再加 `manifest_sha256`。

### 1.5 `taiji-package-manifest.json`

exact top-level keys：

```text
schema, run_id, target_id, source, input, target_config_sha256,
asset_provenance_sha256, cache_requirements_sha256,
cache_observation_sha256, tools, payload, formal_tests,
artifact, boundaries, started_at, finished_at
```

exact object：

```json
{
  "artifact": {
    "authenticode_status": "NotSigned",
    "basename": "TaijiAgent-Setup-1.0.4-win-x64.exe",
    "bytes": 123,
    "file_version": "1.0.4.0",
    "kind": "exe",
    "pe_machine": "0x8664",
    "pe_optional_magic": "0x20b",
    "product_version": "1.0.4.0",
    "sha256": "<64 lowercase hex>",
    "version": "1.0.4"
  },
  "asset_provenance_sha256": "<64 lowercase hex>",
  "boundaries": {
    "installation": false,
    "interactive_acceptance": false,
    "production_license": false,
    "publication": false,
    "signing": false
  },
  "cache_observation_sha256": "<64 lowercase hex>",
  "cache_requirements_sha256": "<64 lowercase hex>",
  "finished_at": "<UTC ISO-8601>",
  "formal_tests": {
    "checks": [
      {"exit_code": 0, "id": "source-session-identity", "result": "PASS"},
      {"exit_code": 0, "id": "offline-npm-ci", "result": "PASS"},
      {"exit_code": 0, "id": "electron-win32-x64", "result": "PASS"},
      {"exit_code": 0, "id": "payload-import-menu-policy", "result": "PASS"},
      {"exit_code": 0, "id": "payload-hygiene-closure", "result": "PASS"},
      {"exit_code": 0, "id": "inno-compile", "result": "PASS"},
      {"exit_code": 0, "id": "installer-pe-version-authenticode", "result": "PASS"}
    ],
    "log_basename": "formal-build-tests.log",
    "log_bytes": 123,
    "log_sha256": "<64 lowercase hex>",
    "status": "PASS"
  },
  "input": {
    "archive": {"basename": "<archive>", "bytes": 123, "sha256": "<64 lowercase hex>"},
    "manifest": {"basename": "<manifest>", "bytes": 123, "sha256": "<64 lowercase hex>"},
    "sidecar": {"basename": "<sidecar>", "bytes": 123, "sha256": "<64 lowercase hex>"}
  },
  "payload": {
    "entries": [{"bytes": 123, "path": "relative/path", "sha256": "<64 lowercase hex>"}],
    "file_count": 1,
    "manifest_sha256": "<64 lowercase hex>",
    "schema": "taiji-windows-payload-manifest/v1",
    "source_commit": "<40 lowercase hex>",
    "source_tree": "<40 lowercase hex>",
    "total_bytes": 123
  },
  "run_id": "<run-id>",
  "schema": "taiji-package-manifest/v2",
  "source": {"branch": "main", "commit": "<40 lowercase hex>", "tree": "<40 lowercase hex>"},
  "started_at": "<UTC ISO-8601>",
  "target_config_sha256": "<64 lowercase hex>",
  "target_id": "windows-x64",
  "tools": {
    "iscc": {"bytes": 123, "path": "<absolute>", "sha256": "<64 lowercase hex>", "version": "<non-empty>"},
    "node": {"bytes": 123, "path": "<absolute>", "sha256": "<64 lowercase hex>", "version": "<non-empty>"},
    "npm": {"bytes": 123, "path": "<absolute>", "sha256": "<64 lowercase hex>", "version": "<non-empty>"},
    "powershell": {"bytes": 123, "path": "<absolute>", "sha256": "<64 lowercase hex>", "version": "<non-empty>"},
    "python": {"bytes": 123, "path": "<absolute>", "sha256": "<64 lowercase hex>", "version": "<non-empty>"},
    "safe_tar": {"bytes": 123, "path": "<absolute>", "sha256": "<64 lowercase hex>", "version": "taiji-safe-tar/v1"},
    "tar": {"bytes": 123, "path": "<absolute>", "sha256": "<64 lowercase hex>", "version": "<non-empty>"}
  }
}
```

`<version>` 必须匹配 `^[0-9]+\.[0-9]+\.[0-9]+$`。EXE filename version、Inno `AppVersion`、`artifact.version` 必须相等；`FileVersion` 与 `ProductVersion` 必须精确等于 `<version>.0`。

`formal_tests.checks` 必须恰好是上列七项、顺序不变、每项 exact keys=`id,result,exit_code`；`result=PASS` 且 `exit_code=0`。`formal-build-tests.log` 必须是 UTF-8 no BOM 的八行文本，前七行逐字为 `01 source-session-identity PASS exit=0`、`02 offline-npm-ci PASS exit=0`、`03 electron-win32-x64 PASS exit=0`、`04 payload-import-menu-policy PASS exit=0`、`05 payload-hygiene-closure PASS exit=0`、`06 inno-compile PASS exit=0`、`07 installer-pe-version-authenticode PASS exit=0`，最后一行 `SUMMARY PASS checks=7`，每行一个 LF。每项只能在对应实际动作退出 0 且正向断言成立后写 PASS；任一失败写 FAIL 到 remote-build.log、停止构建且不得生成 package manifest、remote success state 或 marker，不能创建空 log 后自行填 `status=PASS`。

### 1.6 远端 `run-state.json`

exact top-level keys：

```text
schema, run_id, target_id, source_commit, host_facts_sha256,
stage_history, terminal_status, started_at, finished_at
```

约束：

- `schema=taiji-package-remote-run/v1`；
- `target_id=windows-x64`；
- `source_commit` 与 package manifest 的 `source.commit` 精确一致；
- `terminal_status=REMOTE_BUILD_SUCCEEDED`；
- `stage_history` 是非空、按实际完成时间排序的 `{stage,started_at,finished_at,result}` object 数组，最后一项 `stage=review-ready,result=PASS`；
- 时间均为 UTC ISO-8601，`finished_at >= started_at`。

### 1.7 `.build-success`

`.build-success` 是最后原子创建的 canonical JSON marker，exact keys：

```text
schema, run_id, target_id, source_commit,
artifact_basename, artifact_bytes, artifact_sha256,
package_manifest_basename, package_manifest_bytes, package_manifest_sha256,
formal_build_tests_log_basename, formal_build_tests_log_bytes,
formal_build_tests_log_sha256,
report_basename, report_bytes, report_sha256,
remote_state_basename, remote_state_bytes, remote_state_sha256
```

固定值：

- `schema=taiji-package-build-success/v1`；
- `target_id=windows-x64`；
- 所有 basename/bytes/SHA 与实际 regular file 一致；
- marker 自身不进入任何被它绑定的摘要对象，避免循环摘要；
- 创建 marker 前必须完成 EXE、sidecar、package manifest、formal log、报告和远端 state 的落盘、flush、重新读取与摘要验证。

### 1.8 EXE sidecar、PE、版本与 Authenticode

- sidecar exact 一行为 `<exe sha256>  <exe basename>\n`；
- EXE 必须以 `MZ` 开头，PE signature=`PE\0\0`；
- COFF machine 必须为 `0x8664`（AMD64），optional header magic 必须为 `0x20b`（PE32+）；
- `Get-AuthenticodeSignature` 的 status 必须精确为 `NotSigned`；其他状态，包括 `Valid`、`UnknownError`、`HashMismatch`、`NotTrusted`，在本轮都失败；
- FileVersion 和 ProductVersion 必须与 `<version>.0` 一致；
- fake fixture 必须分别提供正确值和每一种错误值；不能只搜索字符串“x64”或相信文件名。

---

### Task 1: 用 hard-coded truth 真核 selected Git object 与 snapshot

**Files:**

- Create: `packaging/windows/__init__.py`
- Create: `packaging/windows/asset-provenance.json`
- Create: `packaging/windows/verify_legacy_assets.py`
- Create: `packaging/windows/legacy-assets/scripts/windows/Initialize-FastTrackSession.ps1`
- Create: `packaging/windows/legacy-assets/scripts/windows/Stage-WindowsPayload.ps1`
- Create: `packaging/windows/legacy-assets/installer/TaijiAgent.iss`
- Create: `tests/test_windows_legacy_asset_provenance.py`

来源 hard-coded truth：

| source path | mode | blob | bytes | SHA256 | decision |
| --- | --- | --- | ---: | --- | --- |
| `scripts/windows/Initialize-FastTrackSession.ps1` | `100644` | `f792452ab6b3d2b95a1d2fd9e9badc5c71923cf2` | `4954` | `49b5081d36ece563db5ecaafc9696dde31e86a4f73f60a3fe5e6898b2cbd4ee0` | `derive-parameterized-session` |
| `scripts/windows/Stage-WindowsPayload.ps1` | `100644` | `17ba9b8fde890a112aa9882d17bf097247d4c910` | `18021` | `fbe32f4494d97e00b37e67627b106b08b840e34f449b2b2ebffedfcddcc54198` | `derive-parameterized-staging` |
| `installer/TaijiAgent.iss` | `100644` | `ce11f481b6399deec0b436e0e13326d6a692253d` | `1820` | `f6e1934c4aa8cffd948896cd7c72524138aaf1fa7515193637d6af9863cb0505` | `derive-candidate-installer` |

固定 source commit：

```text
f33663f7e3ffee672d39af7b4ecbe9fd2869a00b
```

- [ ] **Step 1: 写 verifier 与 snapshot RED**

`verify_legacy_assets.py` 必须在代码中 hard-code `EXPECTED_SOURCE_COMMIT` 和 `EXPECTED_ASSETS`；不得把 `asset-provenance.json` 当真值。公开 API 固定为：

```python
def git_blob_sha1(data): ...
def verify_git_objects(source_git_dir, source_commit, expected_assets, *, runner=None): ...
def verify_snapshots(repo_root, expected_assets): ...
def verify_lock(lock_path, expected_assets): ...
def verify_selected_assets(source_git_dir, repo_root, lock_path, *, runner=None): ...
def main(argv=None): ...
```

测试先对 verifier、lock 和三个 snapshot 分别执行 `self.assertTrue(path.is_file())`，再通过 `importlib.util.spec_from_file_location()` 加载 verifier；缺文件的 RED 必须是 AssertionFailure，不得在测试模块顶层导入不存在的模块。

`verify_git_objects()` 的默认 runner 只能以参数数组调用 `/usr/bin/git`：

```text
/usr/bin/git --git-dir <source_git_dir> rev-parse --verify <commit>^{commit}
/usr/bin/git --git-dir <source_git_dir> ls-tree -z <commit> -- <source-path>
/usr/bin/git --git-dir <source_git_dir> cat-file blob <blob>
```

逐项验证：commit 输出精确相等；`ls-tree` 恰好一条且 type=`blob`、mode/blob/path 全相等；`cat-file` bytes、SHA256 和 Git blob SHA1 全相等。

snapshot 用 `os.lstat()` 验证：owner 是当前 uid、regular file、非 symlink、link count=1、permission bits=`0644`、bytes/SHA256/Git blob SHA1 全相等。lock exact top-level keys 为 `schema,source_repository,source_commit,assets`；`schema="taiji-windows-legacy-asset-provenance/v1"`，`source_repository="taiji-agentv1.0-win"`，`source_commit` 为上述完整 40 位 SHA。每个 asset exact keys 为 `source_path,snapshot_path,mode,blob,bytes,sha256,decision`，数组顺序与 hard-coded truth 相同；`snapshot_path` 分别是 `packaging/windows/legacy-assets/` 下保留 source 相对层级的三个固定路径，其他值逐项取自上表，不允许自行补字段或绝对源路径。

测试必须包含：

- committed snapshot/lock 与 hard-coded truth 相等；
- 真实旧仓 Git object 与 hard-coded truth 相等；
- 临时 synthetic Git repo 对 wrong commit/path/mode/blob/bytes/hash 的逐项拒绝；
- snapshot symlink、mode `0755`、hardlink、hash 漂移逐项拒绝；
- lock 改写 blob/hash/decision 不能改变 verifier 的 truth。
- `test_verifier_help_runs_isolated_from_external_cwd`：从临时非仓库 cwd 用参数数组运行 `/usr/bin/python3 -I -B <absolute verify_legacy_assets.py> --help`，断言 exit 0、stderr 不含 `ModuleNotFoundError`，且执行前后 cwd 无新文件。该 helper 只用 Python 标准库，不依赖 `PYTHONPATH` 或 repo cwd。

测试 synthetic repo 时在临时目录内设置本地 `user.name` 和 `user.email`；不读取全局 Git identity，不联网。

- [ ] **Step 2: 运行 RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_windows_legacy_asset_provenance
```

Expected：FAIL，消息指出 verifier/lock/snapshot 不存在；不得以测试文件 import error 作为有效 RED。

- [ ] **Step 3: 读取固定 Git object 并用 `apply_patch` 落 snapshot**

先只读核验：

```bash
/usr/bin/git --git-dir=/Users/bwb/Documents/工作/taiji-agentv1.0-win/.git rev-parse --verify f33663f7e3ffee672d39af7b4ecbe9fd2869a00b^{commit}
/usr/bin/git --git-dir=/Users/bwb/Documents/工作/taiji-agentv1.0-win/.git ls-tree -r --long f33663f7e3ffee672d39af7b4ecbe9fd2869a00b -- scripts/windows/Initialize-FastTrackSession.ps1 scripts/windows/Stage-WindowsPayload.ps1 installer/TaijiAgent.iss
/usr/bin/git --git-dir=/Users/bwb/Documents/工作/taiji-agentv1.0-win/.git show f33663f7e3ffee672d39af7b4ecbe9fd2869a00b:scripts/windows/Initialize-FastTrackSession.ps1
/usr/bin/git --git-dir=/Users/bwb/Documents/工作/taiji-agentv1.0-win/.git show f33663f7e3ffee672d39af7b4ecbe9fd2869a00b:scripts/windows/Stage-WindowsPayload.ps1
/usr/bin/git --git-dir=/Users/bwb/Documents/工作/taiji-agentv1.0-win/.git show f33663f7e3ffee672d39af7b4ecbe9fd2869a00b:installer/TaijiAgent.iss
```

使用 `apply_patch` 创建三个 snapshot；禁止 `cp`、shell 重定向、从旧 worktree 读取或格式化内容。执行 verifier：

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I -B packaging/windows/verify_legacy_assets.py --source-git-dir /Users/bwb/Documents/工作/taiji-agentv1.0-win/.git --repo-root . --lock packaging/windows/asset-provenance.json
```

Expected：单行 `SELECTED_WINDOWS_ASSETS_VERIFIED`，exit 0。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_windows_legacy_asset_provenance
git diff --check
git add packaging/windows/__init__.py packaging/windows/asset-provenance.json packaging/windows/verify_legacy_assets.py packaging/windows/legacy-assets tests/test_windows_legacy_asset_provenance.py
git commit -m "chore(packaging): import verified Windows fast-track assets"
```

Expected：unittest=`OK`；提交只含所列路径。

### Task 2: 固定 cache requirements、Windows target、resolver 与 adapter 标签

**Files:**

- Create: `packaging/windows/cache-requirements.json`
- Create: `packaging/pipeline/targets/windows-x64.json`
- Create: `packaging/pipeline/adapters/windows_x64.py`
- Modify: `packaging/pipeline/core/registry.py`
- Modify: `packaging/pipeline/cli.py`
- Modify: `tests/test_taiji_package_target_dispatch.py`
- Create: `tests/test_taiji_package_windows_adapter.py`

- [ ] **Step 1: 写 target/cache/resolver RED**

`cache-requirements.json` exact object：

```json
{
  "entries": [
    {
      "architecture": "any",
      "id": "npm-cache",
      "relative_path": "npm",
      "required_members": ["_cacache"],
      "type": "directory",
      "version": "package-lock-bound"
    },
    {
      "architecture": "x64",
      "id": "electron-39.8.10-win32-x64",
      "relative_path": "electron/electron-v39.8.10-win32-x64.zip",
      "required_members": ["electron.exe"],
      "type": "regular-file",
      "version": "39.8.10"
    },
    {
      "architecture": "x64",
      "id": "private-python-runtime",
      "relative_path": "python-runtime",
      "required_members": ["python.exe", "python311._pth"],
      "type": "directory",
      "version": "3.11"
    }
  ],
  "schema": "taiji-windows-cache-requirements/v1",
  "target_id": "windows-x64"
}
```

语义固定为：

- `relative_path` 和 `required_members` 使用安全 POSIX relative path；
- directory entry 的 members 相对该目录；regular-file Electron zip 的 member 相对 zip root；
- requirements 文件不记录机器特定 hash；online doctor 逐文件观测后写私有 `cache-observation.json`；
- observation exact top-level keys 为 `schema,target_id,requirements_sha256,cache_root,entries,observed_at`；schema=`taiji-windows-cache-observation/v1`；
- observation 中每项 exact 为 `id,type,relative_path,bytes,sha256,members`，members 按 path 排序且 exact 为 `path,bytes,sha256`；
- `cache_observation_sha256` 的基对象精确为 observation 删除顶层 `observed_at` 后的其余 object，再按通用 canonical JSON 算法计算；完整 observation 保留 `observed_at` 作为运行证据，但不让时间造成内容 identity 漂移；
- observation 的 `entries` 顺序必须与 requirements 的三项数组顺序完全相同。对 `type=directory`：先逐个验证 `required_members` 相对路径存在且为 regular file 或 directory，再递归枚举该 entry 根下**全部** regular file；拒绝 reparse point、非 regular/directory、反斜杠、绝对/逃逸路径、NFC 或大小写折叠后重复路径；member path 是相对 entry 根的 NFC POSIX path，按其 UTF-8 bytes 升序，`bytes` 为文件长度，`sha256` 为文件原始 bytes SHA256；entry `bytes` 是全部 member bytes 之和，entry `sha256` 是 exact 有序 members 数组 canonical JSON bytes 的 SHA256。空目录、mtime、ACL 不进入 identity。
- 对 `type=regular-file` 的 Electron zip：entry `bytes/sha256` 是 zip 文件原始 bytes 的长度/SHA256；用只读 ZIP API 拒绝 absolute、drive/UNC、`..`、反斜杠、NUL、尾随点/空格和 NFC/大小写折叠重复 member；`members` 只含 requirements 中列出的 required regular-file members，按同一 UTF-8 排序，bytes/SHA 对解压后的 member 原始 bytes 计算。不得把 ZIP 时间戳、压缩算法或枚举顺序另行混入 digest。
- 完整 observation 只保存在 controller plan 和 run-state 冻结的 `plan.cache_observation`，并作为 `RunRoot\input\cache-observation.json` 传到远端；session 的 `cache` 只存 `observation_path,observation_sha256`，package manifest 只存 `cache_requirements_sha256,cache_observation_sha256`，两者都不内嵌完整 object。run-state identity 同时保存这两个 SHA。
- `host_facts` exact object 只含 `schema,host_alias,os,os_version,architecture,filesystem,powershell_version`，其中 schema=`taiji-windows-host-facts/v1`、architecture=`AMD64`、filesystem=`NTFS`，其 canonical JSON bytes SHA256 为 `host_facts_sha256`；不得把时间、缓存、磁盘余量或 blocker 混入这个稳定身份。
- fake observation 使用固定测试 bytes；真实 observation 留到后续计划；
- 缺 entry/member、类型不符、路径不安全、内容在 run 创建前后漂移均为 `WINDOWS_CACHE_MISSING`，builder status=`BLOCKED`；不得下载、安装、修复或写共享 cache。

内置 `windows-x64.json` exact object：

```json
{
  "allowed_source_branches": ["main"],
  "architecture": "x64",
  "cache_requirements": "packaging/windows/cache-requirements.json",
  "cache_root": "D:\\tw\\cache",
  "git": "C:\\Program Files\\Git\\cmd\\git.exe",
  "host_alias": "windows-direct",
  "iscc": "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe",
  "minimum_free_gib": 20,
  "node": "C:\\Program Files\\nodejs\\node.exe",
  "npm": "C:\\Program Files\\nodejs\\npm.cmd",
  "powershell": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
  "python": "D:\\tw\\cache\\python-runtime\\python.exe",
  "remote_root": "D:\\tw\\taiji-builds",
  "schema": "taiji-package-target/v2",
  "target_id": "windows-x64",
  "tar": "C:\\Windows\\System32\\tar.exe"
}
```

测试必须直接调用 Plan 2 的公开 API，不复制 resolver：

```python
from packaging.pipeline.cli import main, parse_args
from packaging.pipeline.core.registry import create_adapter, resolve_target_reference
```

每个新增 target/cache/adapter 文件先用 `self.assertTrue(path.is_file())` 断言存在，再调用公开 API；这样 RED 来自 Windows 能力缺失，而不是 `FileNotFoundError` 或 `ImportError`。

覆盖：

- `parse_args(["--target", "windows-x64", "doctor"])` 得到 target=`windows-x64`；
- registered ID 精确解析内置 target；
- unknown ID、relative JSON、路径逃逸、ID 注入拒绝为 `TARGET_INVALID`；
- absolute JSON 只有 payload 中 registered `target_id=windows-x64` 且完整 schema 通过时可用；
- registry 固定映射 `windows-x64 -> WindowsX64Adapter`，不扫描目录/entry point；
- `allowed_source_branches` exact 为 `['main']`；feature branch 返回既有稳定类别 `BRANCH_NOT_MAIN`；
- target/config 不含 IP、password、key material；
- cache requirements exact object、顺序、路径安全和 canonical SHA；
- label exact：`候选 EXE 未构建`、`候选 EXE 取回待恢复`、`候选 EXE 已构建`；
- 输入名称 exact 为 Windows 三件套；
- Windows `plan.version` 只来自绑定 source fixture 的 `VERSION`，并与同一 fixture 的 `apps/taiji-desktop/package.json.version` 相等；target/CLI 无版本覆盖；
- fake phase 的 `create_transport()` 未显式注入 fake 时返回 `BUILDER_UNREACHABLE`，不能构造真实 SSH transport；
- `WindowsX64Adapter.online_plan_keys` exact 为 `("cache_requirements_sha256", "cache_observation", "cache_observation_sha256", "host_facts", "host_facts_sha256")`；`bind_online_plan()` 只从 ready online result 深拷贝新增这五键，逐一重算三个 SHA，且不改原 plan；
- 缺/多 online 键、host/cache canonical SHA 漂移、requirements SHA 与仓内配置不符均在确认和 state 创建前失败；测试断言原 plan 未变且无 prepare/create/transfer/build；
- CLI 通过显式 `adapter_factory`、recording `command_runner`、`input_reader`、`publisher` 运行，不启动 subprocess。

- [ ] **Step 2: 运行 RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_taiji_package_target_dispatch tests.test_taiji_package_windows_adapter
```

Expected：FAIL，Windows target/adapter/cache 尚不存在。

- [ ] **Step 3: 实现最小 target 和 adapter**

`WindowsX64Adapter` 必须满足 Plan 2 的 `CandidateAdapter` 全接口，但本 Task 只实现：target schema、local branch/clean/interface doctor、Windows 输入命名、plan 数据、上述 `bind_online_plan`/state identity patch、labels、review validator seam 和 fake transport 注入。fake online fixture 必须返回完整 cache observation、requirements/observation SHA、host facts/host SHA；`initial_state_patch()` 写入的三个 identity SHA 精确是 `asset_provenance_sha256`、`cache_requirements_sha256`、`cache_observation_sha256`，`host_facts_sha256` 由通用 `new_run_state(plan, online, adapter)` 映射，完整 cache/host object 只保留在 frozen plan。真实 transport 默认路径固定抛：

```python
PipelineError("real Windows transport is not enabled in fake phase", category="BUILDER_UNREACHABLE")
```

不得在 adapter 的 fake/remote transport 或任何 Windows PowerShell 中调用 Git，也不得调用 SSH、SCP、PowerShell 或 Inno。唯一 Git 例外是控制端 `local_doctor/build_plan` 对显式 `repo` 的只读 source identity：adapter 私有 `_controller_git(repo, argv, runner=None)` 只允许 `/usr/bin/git -C <repo> status --porcelain=v2 --branch`、`rev-parse HEAD^{commit}`、`rev-parse HEAD^{tree}`、`show <commit>:VERSION` 和 `show <commit>:apps/taiji-desktop/package.json` 参数数组；不得访问 sibling repo、写 ref 或扫描目录。默认 runner 是控制端通用 command runner，测试注入 recording runner，逐条断言 allowlist；除此之外出现 `git` 即失败。不得把 Linux/Windows 实现堆进同一个 transport 分支树。

- [ ] **Step 4: 运行 GREEN、Kylin 回归并提交**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_taiji_package_target_dispatch tests.test_taiji_package_windows_adapter tests.test_taiji_package_candidate tests.test_taiji_package_transport
git diff --check
git add packaging/windows/cache-requirements.json packaging/pipeline/targets/windows-x64.json packaging/pipeline/adapters/windows_x64.py packaging/pipeline/core/registry.py packaging/pipeline/cli.py tests/test_taiji_package_target_dispatch.py tests/test_taiji_package_windows_adapter.py
git commit -m "feat(packaging): register Windows x64 candidate adapter"
```

Expected：unittest=`OK`；Kylin 默认 target 和旧绝对 config 行为不变。

### Task 3: 参数化无 Git Windows 脚本并锁死 JSON/review 合同

**Files:**

- Create: `packaging/windows/Initialize-CandidateSession.ps1`
- Create: `packaging/windows/Stage-CandidatePayload.ps1`
- Create: `packaging/windows/Build-CandidateReview.ps1`
- Create: `packaging/windows/TaijiAgent.iss`
- Create: `tests/test_windows_packaging_script_contract.py`

本 Task 只写和静态检查文本，**禁止执行 PowerShell 或 Inno**。

测试读取每个待创建脚本前先 `self.assertTrue(path.is_file())`；RED 必须是明确的 AssertionFailure，不能把缺文件异常或 PowerShell 解析错误当成有效 RED。

- [ ] **Step 1: 写静态合同 RED**

PowerShell 参数 exact：

`Initialize-CandidateSession.ps1`：

```text
RunRoot, RunId, SourceRoot, SourceBranch, SourceCommit, SourceTree,
InputManifestPath, TargetConfigPath, AssetProvenancePath,
CacheRoot, CacheRequirementsPath, ExpectedCacheRequirementsSha256,
ExpectedCacheObservationSha256,
PowerShellPath, TarPath, NodePath, NpmPath, PythonPath, IsccPath,
SafeTarPath, ExpectedSafeTarSha256, Version
```

`Stage-CandidatePayload.ps1`：

```text
SessionPath
```

`Build-CandidateReview.ps1`：

```text
SessionPath
```

禁止出现 `RepositoryRoot`、`ProductRepository`、`GitPath`、`git archive`、`git rev-parse`、`git checkout` 或任何从 source worktree 推导 commit 的逻辑。`SourceRoot` 必须是 tar 安全解压后的全新普通目录；脚本先拒绝 `.git` 文件/目录/reparse point，再使用显式 `SourceBranch=main`、commit、tree 和输入 manifest。

session JSON exact top-level keys：

```text
schema, run_id, target_id, version, source, input,
identity, paths, tools, cache, boundaries
```

其中：

- `schema=taiji-windows-candidate-session/v1`；
- `source` exact=`branch,commit,tree`；branch 必须 `main`；
- `input` exact=`archive,manifest,sidecar`，每项 exact=`basename,bytes,sha256`；
- `identity` exact=`target_config_sha256,asset_provenance_sha256`；
- `paths` exact=`run_root,source_root,staging_root,staging_cache_root,payload_root,output_root,review_root,logs_root,remote_log`；
- `tools` exact=`powershell,tar,node,npm,python,iscc,safe_tar`；前六项为 target 提供的绝对路径，`safe_tar` 为 plan 绑定的本轮远端 bootstrap 绝对路径；
- `cache` exact=`root,requirements_path,requirements_sha256,observation_path,observation_sha256`；
- `boundaries` exact 为 installation/interactive_acceptance/production_license/signing/publication 全 false；
- session 写入 `RunRoot\session.json`，UTF-8 no BOM，先写同目录临时 regular file，再原子 rename；已存在则失败。

静态测试必须：

- 逐文件 UTF-8 strict decode 并拒绝 BOM；
- 解析参数名集合，拒绝多/少参数；
- 要求 session、cache observation、package manifest、remote state、marker schema literal；
- 要求脚本只通过单一 `Invoke-FormalCheck -Id <fixed-id> -Action <scriptblock>` 依次执行七项，helper 在 action 异常或 `$LASTEXITCODE -ne 0` 时立即 throw，且 `Write-PackageManifest`/marker 调用在七项与 SUMMARY 之后；静态测试解析七次调用及顺序，Python review fixture 再逐项构造非零/缺项/空日志证明 local validator 拒绝，不在 Mac 上冒充执行 PowerShell；
- 要求七文件 review exact set 和独立 `logs\remote-build.log`；
- 要求 `.build-success` 在所有其他 review/state 重读验证后最后创建；
- 要求 `Get-AuthenticodeSignature` 与 status exact `NotSigned`；
- 要求二进制 PE machine `0x8664`、optional magic `0x20b`；
- 要求 FileVersion/ProductVersion=`<version>.0`；
- 要求 `npm ci --offline --ignore-scripts --no-audit` 并显式使用本轮 staging npm cache；
- 要求每轮重核 requirements/observation SHA，共享 cache 只读复制到 staging；
- 拒绝 `Invoke-WebRequest`、`Start-BitsTransfer`、下载/安装 cache、签名、安装/卸载、应用启动、health、publish、Release；
- 拒绝旧固定 `D:\tw\payload`、`D:\tw\out`、`D:\tw\logs`、`D:\tw\build\python-runtime` 和共享 output。

- [ ] **Step 2: 运行 RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_windows_packaging_script_contract
```

Expected：FAIL，新脚本不存在。

- [ ] **Step 3: 依据 snapshot 派生参数化实现**

实现顺序固定为：

1. `Initialize-CandidateSession.ps1` 验证 run/source/identity/tool/cache 参数，拒绝已存在或 reparse run，验证无 `.git` source，验证输入 manifest 与三件 SHA；再次核 `SafeTarPath` bytes/SHA 等于 `ExpectedSafeTarSha256`，核 requirements 等于 `ExpectedCacheRequirementsSha256`，再按 exact schema 原子写 session；
2. 再次按 requirements 逐项读取共享 cache，生成带新 `observed_at` 的完整 observation，删除该时间字段后重算内容 identity 并核初始 `cache_observation_sha256`；不一致返回 `WINDOWS_CACHE_MISSING`；
3. `Stage-CandidatePayload.ps1` 创建本轮 staging，复制 npm cache、Electron zip、private Python 到 `staging\cache` 后重新逐文件核 SHA；共享 cache 后续不再读取；
4. 使用 target 的 Node/npm 与本轮 cache 执行离线 npm；解压本轮 Electron zip；复制私有 Python；应用旧 Stage 中仍适用的 import/menu、敏感文件、数据库/cache/Git residue 和 payload 闭包门禁；
5. 生成 canonical payload object，暂存于本轮 staging，仅将 entries 和 `manifest_sha256` 内嵌进 package manifest，不把独立 payload manifest 放入 review；
6. `Build-CandidateReview.ps1` 只把 staged payload 交给参数化 `TaijiAgent.iss`，使用 `/DMyAppVersion=<version>`、`/DPayloadRoot=<absolute run path>`、`/DOutputDir=<absolute run path>`、`/DOutputBaseFilename=<exact basename>`；
7. 严格按 `source-session-identity → offline-npm-ci → electron-win32-x64 → payload-import-menu-policy → payload-hygiene-closure → inno-compile → installer-pe-version-authenticode` 执行并仅在每项实际 exit 0/断言成立后追加 formal PASS 行；最后一项包括 EXE SHA、PE、VersionInfo、Authenticode 正向验证；七项全过后才追加 SUMMARY，生成报告、sidecar、package manifest；
8. 写 remote `run-state.json`，重新读取并验证前六个 review file；
9. 最后原子写 `.build-success`，再验证 review exact set；
10. 任一步失败保留 run 和 `logs\remote-build.log`，不得自动删除、安装、启动、签名或发布。

`TaijiAgent.iss` 只能接受四个 `/D` define：`MyAppVersion,PayloadRoot,OutputDir,OutputBaseFilename`；`ArchitecturesAllowed=x64compatible`、`ArchitecturesInstallIn64BitMode=x64compatible`，不能读取固定 drive、旧仓或共享 output。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_windows_packaging_script_contract
git diff --check
git add packaging/windows/Initialize-CandidateSession.ps1 packaging/windows/Stage-CandidatePayload.ps1 packaging/windows/Build-CandidateReview.ps1 packaging/windows/TaijiAgent.iss tests/test_windows_packaging_script_contract.py
git commit -m "feat(packaging): define isolated Windows candidate scripts"
```

Expected：unittest=`OK`。本 Task 没有运行 PowerShell/Inno，也没有生成 EXE。

### Task 4: 建立明确共享 fixture/API 和 Windows fake 全链

**Files:**

- Create: `tests/windows_pipeline_fixtures.py`
- Modify: `packaging/pipeline/adapters/windows_x64.py`
- Create: `tests/test_taiji_package_windows_transport.py`

- [ ] **Step 1: 先实现测试共享 API，不使用未定义 fixture**

`tests/windows_pipeline_fixtures.py` 只导出：

```python
def sha256_bytes(data): ...
def canonical_json_bytes(value): ...
def write_regular(path, data, mode=0o600): ...
def make_minimal_amd64_pe(version="1.0.4.0"): ...
def make_windows_plan(root, **overrides): ...
def make_windows_review(root, plan, *, corruption=None): ...

class FakeArtifactInspector:
    def __init__(self, *, file_version="1.0.4.0", product_version="1.0.4.0", authenticode_status="NotSigned"):
        ...
    def inspect(self, path): ...

class FakeWindowsTransport:
    def __init__(self, review_factory, *, failure_at=None, events=None): ...
    def online_doctor(self): ...  # 返回 Task 2 的完整 ready cache/host online identity
    def create_remote_run(self, plan): ...
    def transfer_input(self, plan): ...
    def verify_remote_input(self, plan): ...
    def build_remote_candidate(self, plan): ...
    def fetch(self, plan, staging_dir): ...
```

transport 测试不得在模块顶层 import 尚不存在的 fixture；先断言 fixture 路径存在，再用 `importlib.import_module()` 加载并逐个 `hasattr` 核对上述公开名称，确保 RED 是能力合同失败。

`make_windows_review()` 返回 exact `(review_dir, remote_log, artifact_inspector)`；只接受下列 `corruption` 值：

```text
None
missing-review-file
extra-review-file
review-symlink
sidecar-sha
manifest-source
manifest-input
manifest-payload-sha
artifact-sha
pe-machine
pe-optional-magic
file-version
product-version
authenticode-status
remote-state
marker-sha
noncanonical-json
```

未知 corruption 立即 `ValueError`。helper 必须实际创建七文件 exact set和 review 外 `logs/remote-build.log`；不能通过 mock 一个“valid=True”跳过文件、摘要和 schema。

fake EXE 至少实际包含 MZ、PE signature、COFF machine=`0x8664` 和 optional magic=`0x20b`。版本/AuthentiCode 由显式 `FakeArtifactInspector` 注入，production validator 将 inspector 结果与 manifest/filename 对比；后续真实计划替换为真实远端 evidence 与本地 PE parser。

- [ ] **Step 2: 写成功、失败和禁止外部进程 RED**

测试只调用上列共享 API，不得出现 `self.run_*_fixture()`、`self.fixture()` 或测试内临时定义另一套 review factory。

完整成功事件 exact：

```python
FULL_BUILD_EVENTS = [
    "online-doctor",
    "create-remote-run",
    "transfer-input",
    "remote-input-verify",
    "remote-candidate-build",
    "fetch-review",
    "fetch-log",
    "local-review-verify",
    "publish",
]
```

测试覆盖：

- main/clean source 的完整 fake 成功链；
- builder unreachable=`BUILDER_UNREACHABLE`；
- cache missing=`WINDOWS_CACHE_MISSING` 且无 create/transfer/build；
- input SHA=`INPUT_VERIFICATION_FAILED`；
- transfer interruption=`SCP_INTERRUPTED`；
- payload gate=`WINDOWS_PAYLOAD_FAILED`；
- Inno stage=`WINDOWS_INNO_FAILED`；
- `fetch-review` 和 `fetch-log` 分别失败；
- 七文件多/少、symlink、非 canonical JSON；
- marker、manifest、remote state、source/input/cache/payload SHA 漂移；
- EXE sidecar/SHA、PE machine、optional magic、FileVersion、ProductVersion、Authenticode 逐项错误；
- 本地 publish 目录占用=`LOCAL_OUTPUT_OCCUPIED` 且原文件 bytes 不变；
- excluded stages 不含 install、interactive acceptance、license、sign、publish-to-customer；
- recording runner 收到任何 SSH/SCP/PowerShell/Inno command 即 AssertionError。

成功 artifact exact：

```python
{
    "kind": "exe",
    "basename": "TaijiAgent-Setup-1.0.4-win-x64.exe",
    "bytes": 123,
    "sha256": "<actual lowercase sha256>",
    "path": "<absolute local run review path>",
    "relative_path": "TaijiAgent-Setup-1.0.4-win-x64.exe",
}
```

- [ ] **Step 3: 运行 RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_taiji_package_windows_transport
```

Expected：FAIL，共享 fixture、fake transport 或 validator 尚未实现；不得以 undefined fixture error 作为有效 RED。

- [ ] **Step 4: 实现 fake plan/transport/review validator**

Windows 输入 basename 固定为：

```text
taijiagent-windows-builder-input-<commit>.tar.gz
taijiagent-windows-builder-input-<commit>.manifest.json
taijiagent-windows-builder-input-<commit>.tar.gz.sha256
```

本地 `build_plan` 先绑定 source branch/commit/tree、`version`、target config SHA、asset provenance SHA、唯一 remote/local run、输入三件 basename/bytes/SHA、七文件 review、独立 remote log、三块授权和停止/恢复位置；fake online doctor 后必须通过唯一 `bind_online_plan` 得到同时绑定 cache requirements/observation 和 host facts/SHA 的 finalized plan。`make_windows_plan()` 的 `1.0.4` 只是显式 fake source fixture 值；生产 adapter 不得硬编码它。三块授权为：

1. `ssh-and-transfer`：本轮 fake 仅展示 host/方向/run/input，不执行；
2. `offline-cache-and-filesystem`：共享 cache 只读、本轮 run 新建、缺缓存 BLOCKED；
3. `candidate-build`：仅候选 EXE，排除 install/UI/license/signing/publication。

`validate_review(plan, review, remote_log)` 验证第 1 节全部合同。它不能只相信 marker、manifest 或 fake inspector 中任一单项；必须交叉验证实际 bytes/SHA、exact set、source/input/cache、payload canonical SHA、remote state、PE headers、版本和 Authenticode。

`FakeWindowsTransport.fetch()` 内必须分别追加 `fetch-review` 与 `fetch-log`；review 成功而 log 失败仍是 fetch 失败，不能进入本地验证。fake 不调用 subprocess。

- [ ] **Step 5: 运行 GREEN、Linux 回归并提交**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_taiji_package_windows_adapter tests.test_taiji_package_windows_transport tests.test_taiji_package_transport
git diff --check
git add tests/windows_pipeline_fixtures.py packaging/pipeline/adapters/windows_x64.py tests/test_taiji_package_windows_transport.py
git commit -m "feat(packaging): add fake Windows candidate transport"
```

Expected：unittest=`OK`；测试记录中没有外部进程。

### Task 5: 锁死 `FETCH_PENDING` 只取回 review/log，不重建

**Files:**

- Modify: `tests/test_taiji_package_windows_transport.py`
- Modify only if RED proves necessary: `packaging/pipeline/core/orchestration.py`

- [ ] **Step 1: 使用共享 API 写恢复 RED**

测试构造步骤固定为：

1. 用 `make_windows_plan()` 创建 v2 Windows run；
2. 用 `FakeWindowsTransport(..., failure_at="fetch-review")` 或 `failure_at="fetch-log"` 完成远端 build 后制造取回失败；
3. 断言 state=`FETCH_PENDING`、`remote_build_succeeded=true`、`fetch_allowed=true`，远端 run 未删除；
4. 新 transport 重试 `fetch`；
5. 断言 exact 事件：

```python
FETCH_ONLY_EVENTS = [
    "fetch-review",
    "fetch-log",
    "local-review-verify",
    "publish",
]
```

额外测试：

- stage 非 `FETCH_PENDING` 时 `FETCH_NOT_ALLOWED` 且 adapter/transport events=[]；
- remote build 未成功不可 fetch；
- review 或 remote log 的本地 staging 已占用时拒绝，不覆盖；
- 重试后 manifest/marker/EXE/log SHA 错仍保持 `FETCH_PENDING`，不重建；
- review 已发布后 log 发布失败、两项均发布后 success patch/state replace 失败的四个故障点均可重试收敛；已存在组件只有完整 identity 相同时可复用，漂移 1 byte 才 `LOCAL_OUTPUT_OCCUPIED`；
- 重试不得出现 online-doctor、prepare-input、create-remote-run、transfer-input、remote-input-verify、remote-candidate-build；
- Kylin v1 `FETCH_PENDING` 仍运行其旧三阶段兼容合同，不写成 v2、不改变 target。

- [ ] **Step 2: 运行 RED 或确认 core 已满足合同**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_taiji_package_windows_transport tests.test_taiji_package_transport
```

若直接 PASS，不制造 core 改动；若 FAIL，只允许修正通用 fetch 阶段机，禁止改 Kylin adapter 的平台合同。

- [ ] **Step 3: 运行 GREEN 并提交**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_taiji_package_windows_transport tests.test_taiji_package_transport tests.test_taiji_package_state_v2
git diff --check
git add tests/test_taiji_package_windows_transport.py
```

只有 `packaging/pipeline/core/orchestration.py` 确实因 RED 修改时，才额外执行：

```bash
git add packaging/pipeline/core/orchestration.py
```

提交：

```bash
git commit -m "test(packaging): lock Windows fetch-only recovery"
```

Expected：unittest=`OK`；fetch retry 不含 build 事件。

### Task 6: 文档、Python 3.8 精确清单和全本地门禁

**Files:**

- Create: `docs/runbooks/taiji-windows-candidate-pipeline.md`
- Modify: `docs/runbooks/taiji-kylin-uos-offline-delivery.md`
- Modify: `tests/python38_linux_packaging_gate.py`

- [ ] **Step 1: 写清当前证据边界**

Windows runbook 必须写明：

- 统一入口 `./taiji-package --target windows-x64 doctor|plan|build|status|fetch`；
- 本阶段只有 target/adapter/static contract/fake，真实 `doctor --online`、SSH、PowerShell、Inno、EXE 均未运行；
- source/payload/installer/installed-runtime/interactive-acceptance 五层证据分别能证明什么；
- 本阶段只证明 controller/source-contract 和 fake payload/installer orchestration，不证明真实 payload、installer、安装态或 UI；
- cache requirements/observation、七文件 review、单独 remote log、`FETCH_PENDING` 恢复合同；
- 不安装、不验收、不授权生产 license、不签名、不发布。

Linux runbook 只增加统一入口和当前暂停 handoff 链接，不改变 `99/00/01` 权威链，不声称真实 Kylin 已验证。

- [ ] **Step 2: 把所有新增 Python 文件显式加入 Python 3.8 grammar gate**

新增 exact 清单：

```text
packaging/windows/__init__.py
packaging/windows/verify_legacy_assets.py
packaging/pipeline/adapters/windows_x64.py
tests/windows_pipeline_fixtures.py
tests/test_windows_legacy_asset_provenance.py
tests/test_taiji_package_windows_adapter.py
tests/test_taiji_package_windows_transport.py
tests/test_windows_packaging_script_contract.py
```

不得用目录 glob/扫描替代清单。`registry.py`、`cli.py` 和 `orchestration.py` 已由 Plan 2 清单覆盖；若 Plan 2 实际未覆盖，先补齐显式项再继续。

不得使用 `py_compile`，因为它可能写 `.pyc`。正式 gate 使用项目现有 Python 3.8 grammar checker 对上述 exact list 解析；当前 Python 运行 unittest 时同时使用 `PYTHONDONTWRITEBYTECODE=1` 和 `-B`。

运行门禁前和后各执行一次以下只读检查，任一发现 `__pycache__` 或 `*.pyc` 立即停止并报告既有/新增来源，不自动删除归属不明文件：

```bash
find packaging/pipeline packaging/windows tests -type d -name __pycache__ -print
find packaging/pipeline packaging/windows tests -type f -name '*.pyc' -print
```

- [ ] **Step 3: 运行全部本地门禁**

```bash
bash -n taiji-package
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_windows_legacy_asset_provenance tests.test_windows_packaging_script_contract tests.test_taiji_package_target_dispatch tests.test_taiji_package_state_v2 tests.test_taiji_package_candidate tests.test_taiji_package_transport tests.test_taiji_package_windows_adapter tests.test_taiji_package_windows_transport tests.test_linux_golden_orchestrator
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_kylin_candidate_handoff tests.test_taiji_package_core_boundaries tests.test_taiji_package_orchestration
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_taiji_kylin_packaging_skill tests.test_builder_input_package_contract
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/python38_linux_packaging_gate.py
git diff --check
git status --short
```

再次运行 pycache/pyc 检查。Expected：

- shell gate exit 0；
- unittest=`OK`；
- Python 3.8 grammar gate exit 0；
- `git diff --check` 无输出；
- status 只含本计划明确路径；
- 无 SSH/SCP/PowerShell/Inno/真实输入/EXE 证据。

如果本机有真实 Python 3.8，额外对同一 exact test modules 运行 `python3.8 -B -m unittest`；没有则只报告“Python 3.8 grammar gate 通过，真实 Python 3.8 runtime 未验证”，不得用当前 Python 冒充。

- [ ] **Step 4: 提交文档/gate 并输出阶段状态**

```bash
git add docs/runbooks/taiji-windows-candidate-pipeline.md docs/runbooks/taiji-kylin-uos-offline-delivery.md tests/python38_linux_packaging_gate.py
git commit -m "docs(packaging): define fake Windows candidate workflow"
git status --short
```

Expected：worktree clean。最终报告 exact：

```text
Windows adapter 已实现，本地 fake 通过
Windows selected assets 来源已按 Git object 验证
Windows cache/review/恢复合同已固定
真实 Windows 未连接、PowerShell/Inno 未运行
候选 EXE 未构建
未安装、未验收、未授权生产化、未签名、未发布
```

禁止把上述状态升级为“Windows 制包完成”或“Windows 候选已构建”。

---

## 2. 本计划完成后的下一门禁

本计划只为后续真实 Windows 候选建立可审计接口。下一计划开始前仍必须满足：

1. 产品源码迁入正式主仓并逐 commit 验证；
2. `windows-x64` 成果已进入正式 `main`，source branch 不再是 feature branch；
3. Windows `doctor --online` 只读验证 host/tool/filesystem/cache，并生成真实 cache observation；
4. 操作员对 SSH/传输、离线 cache/run 文件系统和候选构建三块分别确认；
5. 真实 transport 继续使用七文件 exact set、单独 `fetch-log` 和 `FETCH_PENDING` 不重建合同；
6. 真实 EXE 仍只到 candidate 层，安装、交互验收、生产 license、签名和发布保持独立门禁。

没有这些证据，不得从 fake 结果推断 Windows 主机、真实 payload、真实 installer、安装态或 UI 已通过。
