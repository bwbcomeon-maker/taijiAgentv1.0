# Package Core and Kylin Adapter Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` task-by-task. Every code task also uses `test-driven-development`; a RED is valid only when `unittest` reports an assertion `FAIL`, never an import/file `ERROR`.

**Goal:** 从现有 Kylin 单体控制器抽出平台中立 core 和 `kylin-amd64` adapter，同时保持顶层 `taiji-package` 启动语义、现有 Kylin CLI/fake 链、v1 `FETCH_PENDING` 恢复和失败类别兼容。

**Architecture:** 采用“可独立导入的薄 facade → 通用 CLI/阶段机 → 固定注册表中的 Kylin adapter → Kylin transport”。平台字面、v1 Linux 字段映射、`99/00/01`、DEB/review 验证只存在 Kylin adapter；core 只管 target 解析、确认、状态、锁、阶段、恢复和无覆盖发布。

**Tech Stack:** Python 3.8+ 标准库、Bash、`unittest`

---

## 0. 开工基线、不可变合同和停止条件

### 0.1 唯一位置与基线

只在以下 worktree 实施：

```text
/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller
```

开工前逐条执行：

```bash
pwd
git branch --show-current
git rev-parse HEAD
git merge-base --is-ancestor a5a36849bca009d1cfb07ac2309532a502c6bd70 HEAD
git log -1 --format=%s
git status --short
find packaging/pipeline scripts tests -name '__pycache__' -print
find packaging/pipeline scripts tests -name '*.pyc' -print
```

必须同时满足：cwd 是上述 worktree；branch 是 `codex/cross-platform-package-controller`；merge-base 命令退出 0；Plan 1 已提交且最新 subject 为 `docs(packaging): checkpoint paused Kylin candidate pipeline`；worktree clean；两条 find 均无输出。任一不满足即停止，不自动 checkout、reset、stash、删除或合并，也不擅自删除已有 pycache。

### 0.2 文件边界

允许新增：

```text
packaging/pipeline/__init__.py
packaging/pipeline/cli.py
packaging/pipeline/core/__init__.py
packaging/pipeline/core/errors.py
packaging/pipeline/core/models.py
packaging/pipeline/core/orchestration.py
packaging/pipeline/core/registry.py
packaging/pipeline/core/state.py
packaging/pipeline/adapters/__init__.py
packaging/pipeline/adapters/base.py
packaging/pipeline/adapters/kylin_amd64.py
tests/taiji_package_fixtures.py
tests/test_taiji_package_target_dispatch.py
tests/test_taiji_package_state_v2.py
tests/test_taiji_package_core_boundaries.py
tests/test_taiji_package_orchestration.py
```

允许修改：

```text
scripts/taiji-package-candidate.py
tests/test_taiji_package_candidate.py
tests/test_taiji_package_transport.py
tests/python38_linux_packaging_gate.py
```

禁止修改：顶层 `taiji-package`；`packaging/pipeline/targets/kylin-amd64.json`；`packaging/linux/**`；`taijiagent 打包交付/**`；`99/00/01` 及其打包脚本。禁止 SSH/SCP、`99/00/01`、真实制包、联网、安装、push、PR、merge。

### 0.3 必须冻结的 Kylin 合同

顶层 launcher 仍为：

```bash
/usr/bin/python3 -I -B scripts/taiji-package-candidate.py --repo <repo> <args>
```

`scripts/taiji-package-candidate.py` 最终必须保留以下 **17 个** facade 名称：

```text
PipelineError
RunStateStore
RunLock
RealSshTransport
FakeSshTransport
_online_doctor_script
load_target
local_doctor
input_triplet_paths
inspect_builder_input
build_candidate_plan
validate_candidate_review
execute_candidate_transport
run_candidate_build
fetch_candidate
parse_args
main
```

以下现有失败类别不得改名、合并或改变触发边界；本计划只允许新增 `TARGET_INVALID`：

```text
PIPELINE_BLOCKED RUN_LOCKED RUN_LOCK_FAILED STATE_WRITE_FAILED PLAN_INVALID
BUILDER_UNREACHABLE ONLINE_DOCTOR_BLOCKED CONFIRMATION_REQUIRED SSH_FAILED
SCP_INTERRUPTED REMOTE_VERIFY_FAILED REMOTE_BUILD_FAILED BUILD_00_FAILED
BUILD_01_FAILED LOCAL_OUTPUT_OCCUPIED LOCAL_OUTPUT_UNWRITABLE
LOCAL_PUBLISH_FAILED INPUT_PREPARATION_REQUIRED INPUT_PREPARATION_FAILED
INPUT_VERIFICATION_FAILED INPUT_TRIPLET_PARTIAL FETCH_NOT_ALLOWED REPO_INVALID
REPO_IDENTITY_MISMATCH BRANCH_NOT_MAIN SOURCE_COMMIT_INVALID WORKTREE_NOT_CLEAN
PACKAGING_INTERFACE_INVALID PACKAGING_ENTRYPOINT_MISSING SSH_ALIAS_MISSING
STATE_ROOT_UNWRITABLE COMPATIBILITY_POLICY_INVALID SOURCE_DRIFT
LOCAL_REVIEW_INVALID ARTIFACT_SHA_MISMATCH LOCAL_PREFLIGHT_FAILED
```

继续保留 `CANDIDATE_BUILT`、`FETCH_PENDING` 和“候选 DEB 已构建”。fake 链不得调用 SSH/SCP、安装、签名或发布脚本。每个 Task 的 RED 必须是 AssertionFailure；若是 `ModuleNotFoundError`、`ImportError`、`FileNotFoundError` 造成的 `ERROR`，先修测试的失败表达，不能开始产品实现。RED 不单独提交。

---

## Task 1：建立 `-I` 可独立启动的 facade 和 target ID 解析

**Files:**

- Create: `packaging/pipeline/__init__.py`
- Create: `packaging/pipeline/core/__init__.py`
- Create: `packaging/pipeline/core/errors.py`
- Create: `packaging/pipeline/core/registry.py`
- Create: `tests/test_taiji_package_target_dispatch.py`
- Modify: `scripts/taiji-package-candidate.py`

### Step 1.1：写可控 RED

测试先定义以下 helper，使缺 symbol 变为 FAIL 而非 ERROR：

```python
import importlib


def required(module_name, symbol):
    try:
        module = importlib.import_module(module_name)
        return getattr(module, symbol)
    except (ImportError, AttributeError) as exc:
        raise AssertionError(
            "missing production symbol {}.{}: {}".format(module_name, symbol, exc)
        )
```

新增测试：

- `test_parser_preserves_omitted_target_as_none`：`parse_args(["doctor"]).target is None`；显式 ID 保留原字符串。
- `test_registered_id_resolves_exact_builtin_file`：`kylin-amd64` 精确解析到传入 target 目录的 `kylin-amd64.json`。
- `test_absolute_config_remains_supported`：已存在的绝对 JSON 普通文件可用。
- `test_relative_unknown_and_option_like_target_are_rejected`：`windows-x64`、`../x.json`、`targets/x.json`、`-oProxyCommand=x` 均为 `TARGET_INVALID`；本计划不预注册 Windows。
- `test_isolated_facade_bootstraps_exact_repo_package_from_external_cwd`：用 `subprocess.run(check=False)` 从临时非仓库 cwd 运行下列 probe，断言 return code 0，stdout 的解析路径精确等于本 worktree 的 `packaging/pipeline/__init__.py`：

  ```python
  probe = (
      "import runpy,sys;"
      "ns=runpy.run_path(sys.argv[1],run_name='candidate_probe');"
      "print(ns['_pipeline_package'].__file__)"
  )
  completed = subprocess.run(
      ["/usr/bin/python3", "-I", "-B", "-c", probe, str(FACADE)],
      cwd=str(external_cwd), text=True,
      stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
  )
  self.assertEqual(completed.returncode, 0, completed.stderr)
  self.assertEqual(
      Path(completed.stdout.strip()).resolve(),
      (ROOT / "packaging/pipeline/__init__.py").resolve(),
  )
  ```

- `test_launcher_and_shim_help_work_from_external_cwd`：从非仓库 cwd 分别运行绝对路径 launcher 和 `/usr/bin/python3 -I -B <facade> --help`，均退出 0 且无 traceback。

### Step 1.2：运行 RED

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_package_target_dispatch
```

Expected: `FAILED (failures=...)`，消息为缺 symbol、target default 或 subprocess return-code 断言；不得出现 `errors=...`。

### Step 1.3：实现精确 bootstrap

facade 在任何 `packaging.pipeline` import 之前只做：

```python
from pathlib import Path
import sys


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import packaging.pipeline as _pipeline_package


_EXPECTED_PIPELINE = (_REPO_ROOT / "packaging/pipeline/__init__.py").resolve()
_ACTUAL_PIPELINE = Path(_pipeline_package.__file__).resolve()
if _ACTUAL_PIPELINE != _EXPECTED_PIPELINE:
    raise RuntimeError("unexpected packaging.pipeline origin: {}".format(_ACTUAL_PIPELINE))
```

不读 cwd、`PYTHONPATH` 或 site-packages；不插入仓库父目录或用户目录。

### Step 1.4：实现 resolver 和 parser 原始值

`core/errors.py` 移入现有 `PipelineError`，facade 重导出同一个类对象。`core/registry.py` 固定 `SAFE_TARGET_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")` 和 `BUILTIN_TARGET_FILES = {"kylin-amd64": "kylin-amd64.json"}`，并用 `fullmatch()` 判断。

`resolve_target_reference(value, target_dir, registered)` 的规则：安全 ID 只能查固定表；非 ID 只允许已存在的绝对普通文件；未知 ID、相对路径和非文件均为 `TARGET_INVALID`。`load_target_reference()` 在本 Task 只读 JSON object，并核对 `target_id` 与注册 ID 相同；Task 3 有 adapter 后再把最终字段验证交给 `validate_target()`。JSON 不得指定 Python 类、模块或命令。

argparse 的 `--target` 不设 type，`default=None`。仅 doctor/plan/build 的分派把 `None` 转为 `kylin-amd64`；在 Task 6 通用 CLI 落地前，legacy `main()` 必须显式把这三个命令的 `None` 转成内置 Kylin 配置路径，保持中间提交可运行。status/fetch 不在 parser 注入默认 target。

### Step 1.5：GREEN 与提交

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  tests.test_taiji_package_target_dispatch \
  tests.test_taiji_package_candidate
git add packaging/pipeline/__init__.py packaging/pipeline/core/__init__.py \
  packaging/pipeline/core/errors.py packaging/pipeline/core/registry.py \
  scripts/taiji-package-candidate.py tests/test_taiji_package_target_dispatch.py
git commit -m "feat(packaging): resolve candidate targets by stable id"
```

Expected: `OK`。若 launcher 依赖 cwd、导入落到仓库外同名包，或旧 candidate 测试不 GREEN，立即停止。

## Task 2：抽取安全状态原语并完整定义 run-state v2

**Files:**

- Create: `packaging/pipeline/core/models.py`
- Create: `packaging/pipeline/core/state.py`
- Create: `tests/taiji_package_fixtures.py`
- Create: `tests/test_taiji_package_state_v2.py`
- Modify: `scripts/taiji-package-candidate.py`

### Step 2.1：先创建完整 fixture API

`tests/taiji_package_fixtures.py` 在本 Task 结束时必须提供以下可运行 API，无 `...` 或 `pass`：

```text
canonical_json_sha256_for_fixture(payload)
complete_target()
complete_input_files(root, source_commit="a" * 40)
complete_plan(root, run_id="run-1", input_status="REUSABLE")
complete_online(builder_status="BUILDER_READY")
complete_v2_payload(root, run_id="run-1", input_status="REUSABLE", **overrides)
complete_v1_fetch_pending(root, run_id="legacy-run")
write_secure_v1_state(state_root, run_id, payload)
ForbiddenExternalRunner
```

基础实现固定为：

```python
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess


def canonical_json_sha256_for_fixture(payload):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def complete_target():
    return {
        "schema": "taiji-package-target/v1",
        "target_id": "kylin-amd64",
        "host_alias": "kylin",
        "architecture": "amd64",
    }


def complete_input_files(root, source_commit="a" * 40):
    repo = Path(root).resolve() / "repo"
    archive = "taijiagent-制包机输入-{}.tar.gz".format(source_commit)
    names = {
        "archive": archive,
        "manifest": "taijiagent-制包机输入-{}.manifest.json".format(
            source_commit
        ),
        "checksum": archive + ".sha256",
    }
    hashes = {"archive": "1" * 64, "manifest": "2" * 64, "checksum": "3" * 64}
    sizes = {"archive": 101, "manifest": 202, "checksum": 303}
    return {
        role: {
            "path": str(repo / basename),
            "basename": basename,
            "bytes": sizes[role],
            "sha256": hashes[role],
            "exists": True,
        }
        for role, basename in names.items()
    }


def complete_plan(root, run_id="run-1", input_status="REUSABLE"):
    root = Path(root).resolve()
    target = complete_target()
    input_files = complete_input_files(root) if input_status == "REUSABLE" else {}
    return {
        "schema": "taiji-package-candidate-plan/v1",
        "run_id": run_id,
        "target_id": "kylin-amd64",
        "target_config": target,
        "target_adapter": target,
        "repo_root": str(root / "repo"),
        "source_branch": "main",
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "controller_commit": "c" * 40,
        "host_alias": "kylin",
        "architecture": "amd64",
        "remote_run_dir": "/home/kylin/taiji-builds/" + run_id,
        "local_run_dir": str(root / "state/runs" / run_id),
        "input": {"status": input_status, "files": input_files},
        "commands": [],
        "authorization_blocks": [],
        "boundaries": {},
    }


def complete_online(builder_status="BUILDER_READY"):
    return {
        "schema": "taiji-package-online-doctor/v1",
        "builder_status": builder_status,
        "host_facts_sha256": "d" * 64,
        "blockers": [],
    }


def complete_v2_payload(root, run_id="run-1", input_status="REUSABLE", **overrides):
    root = Path(root).resolve()
    target = complete_target()
    plan = complete_plan(root, run_id=run_id, input_status=input_status)
    manifest_sha256 = (
        plan["input"]["files"]["manifest"]["sha256"]
        if input_status == "REUSABLE" else None
    )
    payload = {
        "schema": "taiji-package-run-state/v2",
        "run_id": run_id,
        "target_id": "kylin-amd64",
        "target_config": target,
        "target_config_sha256": canonical_json_sha256_for_fixture(target),
        "source": {
            "repo_root": str(root / "repo"), "branch": "main",
            "commit": "a" * 40, "tree": "b" * 40,
        },
        "identity": {
            "controller_commit": "c" * 40,
            "asset_provenance_sha256": None,
            "input_manifest_sha256": manifest_sha256,
            "cache_requirements_sha256": None,
            "cache_observation_sha256": None,
            "host_facts_sha256": "d" * 64,
        },
        "stage": "PLANNED",
        "status_label": "候选 DEB 未构建",
        "created_at": "2026-08-20T12:00:00Z",
        "updated_at": "2026-08-20T12:00:00Z",
        "started_at": "2026-08-20T12:00:00Z",
        "finished_at": None,
        "host": {
            "alias": "kylin", "architecture": "amd64",
            "remote_run_dir": "/home/kylin/taiji-builds/" + run_id,
        },
        "paths": {"local_run_dir": str(root / "state/runs" / run_id)},
        "input": deepcopy(plan["input"]),
        "policy": {
            "kind": "canonical-compatibility-policy", "sha256": "e" * 64,
        },
        "remote_build_succeeded": False,
        "fetch_allowed": False,
        "artifact": None,
        "failure": None,
        "stage_history": [],
        "lock": {"status": "released"},
        "logs": {
            "controller": str(root / "state/runs" / run_id / "controller.log"),
            "remote_build": str(root / "state/runs" / run_id / "remote-build.log"),
        },
        "plan": plan,
    }
    payload.update(deepcopy(overrides))
    return payload


def complete_v1_fetch_pending(root, run_id="legacy-run"):
    root = Path(root).resolve()
    target = complete_target()
    return {
        "schema": "taiji-package-run-state/v1",
        "run_id": run_id,
        "created_at": "2026-08-20T12:00:00Z",
        "updated_at": "2026-08-20T12:00:00Z",
        "stage": "FETCH_PENDING",
        "status_label": "候选 DEB 取回待恢复",
        "source_commit": "a" * 40,
        "canonical_policy_sha256": "e" * 64,
        "remote_build_succeeded": True,
        "fetch_allowed": True,
        "failure": {"category": "SCP_INTERRUPTED", "detail": "fixture"},
        "plan": {
            "run_id": run_id,
            "target_adapter": target,
            "repo_root": str(root / "repo"),
            "source_commit": "a" * 40,
            "canonical_policy_sha256": "e" * 64,
            "remote_run_dir": "/home/kylin/taiji-builds/" + run_id,
            "local_run_dir": str(root / "state/runs" / run_id),
            "input": {"status": "REUSABLE", "files": {}},
        },
    }


def write_secure_v1_state(state_root, run_id, payload):
    state_root = Path(state_root)
    state_root.mkdir(parents=True, mode=0o700)
    state_root.chmod(0o700)
    runs = state_root / "runs"
    runs.mkdir(mode=0o700, exist_ok=True)
    runs.chmod(0o700)
    run_dir = runs / run_id
    run_dir.mkdir(mode=0o700)
    run_dir.chmod(0o700)
    path = run_dir / "run-state.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


class ForbiddenExternalRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        normalized = [str(item) for item in argv]
        self.calls.append(normalized)
        if normalized and normalized[0] in ("/usr/bin/ssh", "/usr/bin/scp", "ssh", "scp"):
            raise AssertionError("external transport is forbidden in unit tests")
        return subprocess.CompletedProcess(normalized, 0, "", "")
```

### Step 2.2：写 v2 工厂、必填字段和保护规则 RED

`tests/test_taiji_package_state_v2.py` 使用 Task 1 的 `required()` 模式，新增：

- `test_new_run_state_populates_complete_v2_contract`：断言下文所有顶层键、source/identity/host/logs 子键、target SHA 和 initial patch。
- `test_target_config_sha_uses_validated_canonical_json`：键顺序不同 SHA 相同；加入一个已验证字段后 SHA 改变。
- `test_create_rejects_every_missing_required_top_level_field`：遍历必填集，每次删除一键均为 `PLAN_INVALID`。
- `test_create_rejects_invalid_required_nested_field_type`：分别损坏 `source.commit`、`identity.controller_commit`、`host.alias`、`paths.local_run_dir`、`logs.controller`，均为 `PLAN_INVALID`。
- `test_update_rejects_each_frozen_identity_path`：逐一修改下文 frozen path，均为 `PLAN_INVALID`。
- `test_nullable_identity_can_move_null_to_sha_once`：`None -> 64hex` 成功；再改值或改回 `None` 均 `PLAN_INVALID`。
- `test_missing_input_can_bind_once_before_input_verified`：用 `complete_v2_payload(root, input_status="MISSING")` 创建完整 `PLANNED/MISSING` fixture，一次写入 `complete_input_files()` 和对应 manifest SHA 成功；第二次改成不同 identity 为 `PLAN_INVALID`。
- `test_missing_input_binds_top_level_and_execution_plan_atomically`：调用唯一 `bind_verified_input()` 后断言 `state.input == state.plan.input`，manifest SHA 与两者一致；故障注入在 replace 前失败时磁盘三处仍全部为 MISSING/null，不允许半更新。
- `test_reusable_input_rewrite_requires_identical_identity`：`PLANNED/REUSABLE` 只允许深拷贝后完全相同的三件套 identity，任一 basename/bytes/SHA 改变都为 `PLAN_INVALID`。
- `test_input_is_frozen_after_input_verified`：先用完整三件套进入 `INPUT_VERIFIED`，再改任一 input 字段均为 `PLAN_INVALID`，且状态文件 bytes 不变。
- `test_v1_load_and_update_preserve_schema_and_bytes_until_update`：load 后字节不变；update 仍是 v1 且不添加 v2 键。
- `test_core_has_no_v1_linux_field_mapping`：`core/models.py`、`core/state.py` 不包含 `canonical_policy_sha256`、`deb_sha256` 或 `normalize_legacy_state`。通用 v2 工厂允许读取 plan 的平台中立 `source_commit`，不能把该必要字段误判为 legacy 映射。

### Step 2.3：RED

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_package_state_v2
```

Expected: `FAILED (failures=...)`；缺 symbol 经 `required()` 转成 AssertionFailure，不允许 `errors=...`。

### Step 2.4：实现完整 v2 工厂

`core/models.py` 定义：

```python
CURRENT_STATE_SCHEMA = "taiji-package-run-state/v2"
LEGACY_STATE_SCHEMA = "taiji-package-run-state/v1"
SUPPORTED_STATE_SCHEMAS = (LEGACY_STATE_SCHEMA, CURRENT_STATE_SCHEMA)

V2_REQUIRED_TOP_LEVEL = {
    "schema", "run_id", "target_id", "target_config", "target_config_sha256",
    "source", "identity", "stage", "status_label", "created_at", "updated_at",
    "started_at", "finished_at", "host", "paths", "input", "policy",
    "remote_build_succeeded", "fetch_allowed", "artifact", "failure",
    "stage_history", "lock", "logs", "plan",
}
```

`canonical_json_sha256(payload)` 必须只对 adapter 验证后的完整 target object 使用：

```python
json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
```

不加换行。`new_run_state(plan, online, adapter)` 的完整映射固定为：

| v2 路径 | 来源 |
|---|---|
| `run_id` | `plan.run_id` |
| `target_id/config` | `plan.target_id/target_config` |
| `target_config_sha256` | `canonical_json_sha256(plan.target_config)` |
| `source.repo_root/branch/commit/tree` | `plan.repo_root/source_branch/source_commit/source_tree` |
| `identity.controller_commit` | `plan.controller_commit` |
| `identity.host_facts_sha256` | `online.host_facts_sha256`，缺失则 `None` |
| identity 其余四键 | `None`，再与 initial patch 的同名键合并 |
| `stage/status_label` | `PLANNED` / `adapter.not_built_label` |
| `created/updated/started_at` | 同一次 `utc_now()`；`finished_at=None` |
| `host.alias/architecture/remote_run_dir` | plan 同名字段 |
| `paths.local_run_dir` | `plan.local_run_dir` |
| `input` | `plan.input` 深拷贝 |
| `policy` | `None`，可由 initial patch 设定 |
| `remote_build_succeeded/fetch_allowed/artifact` | `False` / `False` / `None` |
| `failure/history/lock` | `None` / `[]` / `{"status":"released"}` |
| `logs.controller/remote_build` | local run 下两个固定日志路径 |
| `plan` | plan 深拷贝 |

`adapter.initial_state_patch(plan, online)` 只允许返回 `identity` 和 `policy` 两个顶层键；identity 只允许补入已定义的 nullable 键。返回其他键、未知 identity 键或覆盖 controller commit 均 `PLAN_INVALID`。

core 在成功时自行写标准 `artifact`、stage、label、finished/fetch/failure 字段；`adapter.success_state_patch(artifact)` 只允许返回不与 `V2_REQUIRED_TOP_LEVEL` 冲突的平台兼容附加键。任何覆盖标准或 frozen 键的 patch 均 `PLAN_INVALID`，磁盘状态不变。Kylin adapter 固定只返回 `{"deb": deepcopy(artifact)}`，以保持现有 status/测试读取兼容；通用 core 不解释该键。

`RunStateStore.create()` 只接收完整并通过类型、路径、SHA 检查的 v2；不允许残缺 v2 fixture。现有测试中所有 `store.create(run_id, {"stage": ...})` 必须逐处改为 `complete_v2_payload()`；若测试目的明确是 legacy load/update，则只能用 `write_secure_v1_state()` 直接写安全 v1，不能让 `create()` 创建 v1。`load()` 接受 v1/v2；`update()` 保留原 schema。

创建后冻结的 path 精确为：

```text
schema
run_id
target_id
target_config
target_config_sha256
created_at
source.repo_root
source.branch
source.commit
source.tree
identity.controller_commit
host.alias
host.remote_run_dir
paths.local_run_dir
```

`plan` 除 exact `plan.input` 外的全部路径同样冻结；不得用替换整个 plan 的方式绕过。state store 公开 `bind_verified_input(run_id, inspected_input, manifest_sha256)`，只在 `PLANNED` 下执行一次 canonical 深拷贝和单次原子 state replace，同时写 `input`、`plan.input`、`identity.input_manifest_sha256`；返回写后 state 的 `plan` 深拷贝供本轮后续阶段使用。任何一处原值不是允许的 MISSING/相同 REUSABLE、三处新 identity 不相等或 manifest SHA 不匹配，都在落盘前 `PLAN_INVALID`。

`identity.asset_provenance_sha256`、`input_manifest_sha256`、`cache_requirements_sha256`、`cache_observation_sha256`、`host_facts_sha256` 只能 `null -> 64 lowercase hex` 一次；初始已有 SHA 时立即冻结。

`input` 在 `PLANNED` 阶段只允许一次 `MISSING -> REUSABLE` 或以相同 REUSABLE identity 重写；进入 `INPUT_VERIFIED` 前必须包含实际三件套 basename/bytes/SHA，且 `identity.input_manifest_sha256` 已写入。stage 达到 `INPUT_VERIFIED` 后，完整 `input` object 视为 frozen，任何后续修改均 `PLAN_INVALID`。测试分别覆盖 MISSING prepare、REUSABLE 原样绑定和远端阶段后的篡改拒绝。

`update()` 必须先将 changes 深合并到内存中的 prospective state，再逐个 dotted path 与原 state 比较；不得只检查 changes 的顶层键而漏过 `source`、`identity`、`host`、`paths` 子字段。验证全部通过后才做一次原子写，验证失败时磁盘字节不变。

从旧脚本原样搬迁权限、symlink、hardlink、原子写、lock、controller log 检查，不借抽取改变安全语义。

### Step 2.5：GREEN 与提交

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  tests.test_taiji_package_state_v2 \
  tests.test_taiji_package_candidate
git add packaging/pipeline/core/models.py packaging/pipeline/core/state.py \
  tests/taiji_package_fixtures.py tests/test_taiji_package_state_v2.py \
  scripts/taiji-package-candidate.py tests/test_taiji_package_candidate.py
git commit -m "feat(packaging): add backward-compatible candidate state v2"
```

Expected: `OK`，现有权限、symlink、hardlink、lock、无覆盖测试不减少。若必须让 core 解释 Linux v1 字段才能 GREEN，停止；该映射属于 Task 3。

## Task 3：建立精确的 11-hook adapter 合同和 Kylin-only v1 映射

**Files:**

- Create: `packaging/pipeline/adapters/__init__.py`
- Create: `packaging/pipeline/adapters/base.py`
- Create: `packaging/pipeline/adapters/kylin_amd64.py`
- Modify: `packaging/pipeline/core/registry.py`
- Modify: `tests/test_taiji_package_target_dispatch.py`
- Modify: `tests/test_taiji_package_state_v2.py`

### Step 3.1：写 adapter/legacy RED

新增：

- `test_candidate_adapter_exposes_exact_eleven_hooks`：逐一断言下列 11 个方法存在，不接受七方法、十方法版本或别名。
- `test_registry_returns_only_kylin_adapter`：`create_adapter("kylin-amd64")` 成功，`windows-x64` 为 `TARGET_INVALID`。
- `test_registry_never_imports_class_from_target_json`：target 中加入 `python_class`/`module` 不会触发 import，而是 target 验证失败。
- `test_kylin_adapter_normalizes_v1_without_mutating_source`：输入含 `source_commit`、`canonical_policy_sha256`、`deb` 的 v1，返回通用视图；原 dict 深拷贝前后相等。
- `test_no_non_kylin_module_contains_legacy_linux_mapping_literals`：本 Task 只检查已存在的 `packaging/pipeline/core/*.py` 和 `packaging/pipeline/adapters/base.py`，断言不含上述三个 legacy 字面。`cli.py` 与 `core/orchestration.py` 尚未创建，对它们的同等边界断言在 Task 6 的 `test_final_common_modules_have_no_platform_build_literals` 中强制；本 Task 不用 `if exists` 跳过应存在的 core/base 文件。

### Step 3.2：RED

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  tests.test_taiji_package_target_dispatch \
  tests.test_taiji_package_state_v2
```

Expected: AssertionFailure RED，不得以缺模块 ERROR 作为 RED。

### Step 3.3：实现精确 11-hook 接口

`CandidateAdapter` 的公共平台合同有且只有以下十一个 hook，五个标签属性和固定 tuple `online_plan_keys` 另计；具体 adapter 可有以下划线开头的私有 helper，但不得增加另一个由 core 条件调用的公共平台 hook：

```python
class CandidateAdapter:
    target_id = None
    artifact_kind = None
    success_label = None
    pending_label = None
    not_built_label = None
    online_plan_keys = ()

    def validate_target(self, payload):
        raise NotImplementedError

    def local_doctor(self, repo, target, state_root, *, ssh_config):
        raise NotImplementedError

    def inspect_input(self, repo, source_commit):
        raise NotImplementedError

    def build_plan(self, repo, target, state_root, *, run_id, ssh_config):
        raise NotImplementedError

    def bind_online_plan(self, plan, online):
        raise NotImplementedError

    def prepare_input(self, plan, command_runner):
        raise NotImplementedError

    def create_transport(self, repo, target, *, ssh_config, command_runner):
        raise NotImplementedError

    def validate_review(self, plan, review, remote_log):
        raise NotImplementedError

    def initial_state_patch(self, plan, online):
        raise NotImplementedError

    def success_state_patch(self, artifact):
        raise NotImplementedError

    def normalize_legacy_state(self, state):
        raise NotImplementedError
```

`bind_online_plan(plan, online)` 的通用合同固定为：只在 `online.builder_status=BUILDER_READY` 后调用一次；输入和返回均为 dict，adapter 必须返回深拷贝，不得原地修改。core 要求 `adapter.online_plan_keys` 中每一键在原 plan 不存在、返回对象新增键集合与 tuple 精确相等；然后从返回对象删除这组新增键，剩余完整 object 必须与原 plan 深相等，不允许只比较一份易遗漏的字段 allowlist。Kylin 的 tuple 为空并只返回深拷贝；Windows 的 tuple 和 schema 留到 Plan 4 定义。返回的 online-finalized plan 用于展示/确认和 `new_run_state()`；创建后只有 `bind_verified_input()` 可在 PLANNED 阶段把 `state.input` 与 `state.plan.input` 同步 MISSING→REUSABLE 一次，形成 bound execution plan，其他 plan 字段始终冻结。

`registry.py` 当前只允许：

```python
ADAPTER_FACTORIES = {"kylin-amd64": KylinAmd64Adapter}
```

不扫描目录、entry point 或 JSON 指定的代码。Windows adapter 完成前不注册空占位。

### Step 3.4：把 v1 映射限定在 Kylin adapter

`KylinAmd64Adapter.normalize_legacy_state(state)` 仅对 schema v1 做深拷贝视图：

| 通用视图 | v1 来源 |
|---|---|
| `target_id` | 固定 `kylin-amd64` |
| `target_config` | `state.plan.target_adapter` |
| `target_config_sha256` | 对上述 object 做 canonical JSON SHA |
| `source.commit` | `state.source_commit`，缺失时取 `state.plan.source_commit` |
| `source.repo_root` | `state.plan.repo_root` |
| `policy.kind` | 固定 `canonical-compatibility-policy` |
| `policy.sha256` | `state.canonical_policy_sha256`，缺失时取 plan 同名键 |
| `artifact` | `state.artifact`，缺失时从 `state.deb` 映射 `kind=deb/basename/bytes/sha256/path` |

该方法对 v2 只返回深拷贝，不改文件、不调用 store update。core 只消费通用视图，不出现 Linux legacy 键。缺 target adapter、commit、policy SHA 或恢复路径时以 `PLAN_INVALID` 停止，不猜默认值。

### Step 3.5：GREEN 与提交

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  tests.test_taiji_package_target_dispatch \
  tests.test_taiji_package_state_v2
git add packaging/pipeline/adapters packaging/pipeline/core/registry.py \
  tests/test_taiji_package_target_dispatch.py tests/test_taiji_package_state_v2.py
git commit -m "refactor(packaging): define Kylin candidate adapter contract"
```

Expected: `OK`。core 出现 v1 Linux 映射字面即停止。

---

## Task 4：搬迁 Kylin 实现，保留 17 个 facade 名称和 monkeypatch DI seam

**Files:**

- Modify: `packaging/pipeline/adapters/kylin_amd64.py`
- Modify: `scripts/taiji-package-candidate.py`
- Create: `tests/test_taiji_package_core_boundaries.py`
- Modify: `tests/test_taiji_package_candidate.py`
- Modify: `tests/test_taiji_package_transport.py`

### Step 4.1：写 facade/boundary RED

`tests/test_taiji_package_core_boundaries.py` 用 `subprocess.run(check=False)` 或 `required()` 防止 import ERROR，新增：

- `test_facade_exports_exact_legacy_compatibility_set`：断言 17 个名称全部存在。
- `test_facade_types_are_same_core_or_adapter_objects`：`PipelineError`、`RunStateStore`、`RunLock` 是 core 原对象；transport 是 Kylin 原对象。
- `test_legacy_function_signatures_remain_compatible`：用 `inspect.signature` 断言下文精确签名。
- `test_facade_factory_reads_transport_and_validator_globals_at_call_time`：patch facade 的 `RealSshTransport` 和 `validate_candidate_review`，调用 `_facade_adapter_factory()` 后再调用 adapter hooks，传 `ForbiddenExternalRunner`，断言使用 patch 对象而非真实 SSH。
- `test_extracted_common_modules_have_no_platform_build_literals`：检查本 Task 已存在的 `core/models.py`、`core/state.py`、`core/errors.py`、`core/registry.py` 不含 `99_本机`、`00_制包机`、`01_制包机`、`dpkg`、`apt`、`.deb`、`canonical_policy_sha256`、`deb_sha256`；不对 Kylin adapter/facade 做此断言。`cli.py` 和 `core/orchestration.py` 的同类断言在 Task 6 创建文件后加入。

facade 保留函数的精确签名：

```text
_online_doctor_script(target)
load_target(path)
local_doctor(repo, target, state_root, *, ssh_config=None)
input_triplet_paths(repo, source_commit)
inspect_builder_input(repo, source_commit)
build_candidate_plan(repo, target, state_root, *, run_id=None, ssh_config=None)
validate_candidate_review(plan, review_path, remote_log_path, *, command_runner=_run_command)
execute_candidate_transport(plan, transport, staging_dir, *, confirmed, prepare_input=None)
run_candidate_build(plan, store, transport, *, confirmed, online_result=None, prepare_input=None, command_runner=_run_command, review_validator=None)
fetch_candidate(store, run_id, transport, *, review_validator=None)
parse_args(argv=None)
main(argv=None)
```

五个 class 名称直接重导出同一对象，不写相似但 identity 不同的包装类。

### Step 4.2：RED

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_package_core_boundaries
```

Expected: AssertionFailure RED。`cli.py` 尚不存在时必须先以 `assertTrue(path.is_file())` 产生 FAIL，不能直接 `read_text()` 造成 ERROR。

### Step 4.3：按原函数边界搬迁

不重写算法，从旧脚本移入 `KylinAmd64Adapter`/同模块：target 校验；local doctor；三件套/verifier/99 准备回调；canonical policy；online doctor script；Kylin 两种 transport；Kylin plan/remote command；DEB review/preflight；Kylin initial/success state patch。

Kylin plan 继续保留全部现有键，并为 v2 **增量补齐且冻结**：`target_config=dict(validated_target)`、`source_branch`（必须为 `main`）、`source_tree`（`git rev-parse HEAD^{tree}` 的 40 位 SHA）、`controller_commit`（控制器 checkout 的 `git rev-parse HEAD`）、`architecture=target["architecture"]`。`target_id`、`repo_root`、`source_commit`、`host_alias`、`remote_run_dir`、`local_run_dir` 已有键不得删除或改义。任一新增 identity 取不到或不是所需完整 SHA 时以 `PLAN_INVALID` 停止，不写残缺 v2。

为避免 generic build 把 local doctor 跑两次，把旧 `build_candidate_plan()` 拆成私有 `_build_plan_from_doctor(doctor_result, ...)`。`KylinAmd64Adapter.local_doctor()` 保存一份与 `(repo, target, state_root, ssh_config)` 精确绑定的只读结果；紧接的 `build_plan()` 只在 key 完全相同时消费一次该结果，key 不同或没有缓存时自行做一次 local doctor。facade `build_candidate_plan()` 用同一个临时 adapter 先 local doctor 再 build plan。不得跨参数、跨命令或跨 adapter 实例复用 doctor 结果。

标签固定：

```python
target_id = "kylin-amd64"
artifact_kind = "deb"
success_label = "候选 DEB 已构建"
pending_label = "候选 DEB 取回待恢复"
not_built_label = "候选 DEB 未构建"
```

### Step 4.4：先建立 facade DI seam，不提前切换 CLI

本 Task 保留现有可工作的 legacy `main()`，只新增并测试运行时 factory；真正把 facade `main()` 切换到通用 CLI 留给 Task 6。这样 Task 4 的旧 CLI GREEN 不依赖尚未创建的 `cli.py`。factory 固定为：

```python
def _facade_adapter_factory(target_id):
    adapter = create_adapter(target_id)
    if target_id == "kylin-amd64":
        adapter.transport_factory = (
            lambda repo, target, ssh_config, command_runner: RealSshTransport(
                repo, target, ssh_config=ssh_config, command_runner=command_runner
            )
        )
        adapter.review_validator = (
            lambda plan, review, remote_log: validate_candidate_review(
                plan, review, remote_log
            )
        )
    return adapter


```

`KylinAmd64Adapter.create_transport()` 用实例上的 `transport_factory`；`validate_review()` 用实例上的 `review_validator`。Task 4 的 seam 测试直接在 patch context 内调用 `_facade_adapter_factory("kylin-amd64")`，再调用该 adapter 的 transport/validator hook，证明读取到 patch 对象。Task 6 改 facade `main()` 后，现有 CLI patch 测试再证明整链仍有效。不得在 import 时捕获到 default argument 或单例。

`KylinAmd64Adapter.__init__(transport_factory=None, review_validator=None)` 在参数为 `None` 时，于实例创建时选用 Kylin 模块当前的 `RealSshTransport` 和 review validator；显式依赖优先。registry 的普通调用因此可用，facade 又能在每次命令调用时覆盖依赖。不得把 transport 实例跨 run 缓存。

### Step 4.5：GREEN 与提交

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  tests.test_taiji_package_core_boundaries \
  tests.test_taiji_package_candidate \
  tests.test_taiji_package_transport \
  tests.test_linux_golden_orchestrator
git add packaging/pipeline/adapters/kylin_amd64.py \
  scripts/taiji-package-candidate.py tests/test_taiji_package_core_boundaries.py \
  tests/test_taiji_package_candidate.py tests/test_taiji_package_transport.py
git commit -m "refactor(packaging): isolate Kylin candidate contracts"
```

Expected: `OK` 且 runner 无 SSH/SCP。任一 facade 名称/签名消失、monkeypatch 失效或 fake 尝试真实 SSH，立即停止。

## Task 5：完成 recording fixtures，先写出精确阶段序列

**Files:**

- Modify: `tests/taiji_package_fixtures.py`
- Create: `tests/test_taiji_package_orchestration.py`

### Step 5.1：补全 fixture API

在 Task 2 API 上新增下列完整实现；禁止 `...`、`pass` 或真实网络：

```python
class RecordingTransport:
    def __init__(self, events, builder_status="BUILDER_READY"):
        self.events = events
        self.builder_status = builder_status

    def online_doctor(self):
        self.events.append("online_doctor")
        return complete_online(self.builder_status)

    def create_remote_run(self, plan):
        self.events.append("create_remote_run")

    def transfer_input(self, plan):
        self.events.append("transfer_input")

    def verify_remote_input(self, plan):
        self.events.append("verify_remote_input")

    def build_remote_candidate(self, plan):
        self.events.append("build_remote_candidate")

    def fetch(self, plan, staging_dir):
        staging_dir = Path(staging_dir)
        review = staging_dir / "review"
        review.mkdir(parents=True, mode=0o700)
        artifact = review / "candidate.bin"
        artifact.write_bytes(b"candidate")
        artifact.chmod(0o600)
        self.events.append("fetch-review")
        remote_log = staging_dir / "remote-build.log"
        remote_log.write_text("fake remote build\n", encoding="utf-8")
        remote_log.chmod(0o600)
        self.events.append("fetch-log")
        return {"review_path": str(review), "remote_log_path": str(remote_log)}


class RecordingAdapter:
    target_id = "kylin-amd64"
    artifact_kind = "bin"
    success_label = "candidate built"
    pending_label = "candidate fetch pending"
    not_built_label = "candidate not built"
    online_plan_keys = ()

    def __init__(self, root, events, input_status="REUSABLE", builder_status="BUILDER_READY"):
        self.root = Path(root)
        self.events = events
        self.input_status = input_status
        self.transport = RecordingTransport(events, builder_status)
        self.transport_repo = None

    def validate_target(self, payload):
        self.events.append("validate_target")
        if payload.get("target_id") != self.target_id:
            raise AssertionError("fixture target id mismatch")
        return deepcopy(payload)

    def local_doctor(self, repo, target, state_root, *, ssh_config):
        self.events.append("local_doctor")
        return {
            "controller_status": "CONTROLLER_READY",
            "builder_status": "BUILDER_UNREACHABLE",
            "blockers": [],
        }

    def inspect_input(self, repo, source_commit):
        self.events.append("inspect_input")
        files = complete_input_files(self.root, source_commit) \
            if self.input_status == "REUSABLE" else {}
        return {"status": self.input_status, "files": files}

    def build_plan(self, repo, target, state_root, *, run_id, ssh_config):
        self.events.append("build_plan")
        plan = complete_plan(
            self.root, run_id=run_id or "run-1", input_status=self.input_status
        )
        plan["repo_root"] = str(Path(repo).resolve())
        plan["target_config"] = deepcopy(target)
        plan["target_adapter"] = deepcopy(target)
        plan["local_run_dir"] = str(
            Path(state_root).resolve() / "runs" / plan["run_id"]
        )
        return plan

    def bind_online_plan(self, plan, online):
        self.events.append("bind_online_plan")
        return deepcopy(plan)

    def prepare_input(self, plan, command_runner):
        self.events.append("prepare_input")
        self.input_status = "REUSABLE"

    def create_transport(self, repo, target, *, ssh_config, command_runner):
        self.events.append("create_transport")
        self.transport_repo = str(Path(repo).resolve())
        return self.transport

    def validate_review(self, plan, review, remote_log):
        self.events.append("validate_review")
        artifact = Path(review) / "candidate.bin"
        return {
            "kind": "bin",
            "basename": artifact.name,
            "bytes": artifact.stat().st_size,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "path": str(artifact),
            "relative_path": "candidate.bin",
        }

    def initial_state_patch(self, plan, online):
        self.events.append("initial_state_patch")
        return {"policy": None, "identity": {}}

    def success_state_patch(self, artifact):
        self.events.append("success_state_patch")
        return {}

    def normalize_legacy_state(self, state):
        self.events.append("normalize_legacy_state")
        normalized = deepcopy(state)
        normalized["target_id"] = "kylin-amd64"
        normalized["target_config"] = deepcopy(state["plan"]["target_adapter"])
        normalized["target_config_sha256"] = canonical_json_sha256_for_fixture(
            normalized["target_config"]
        )
        return normalized


class RecordingPublisher:
    def __init__(self, events):
        self.events = events

    def __call__(self, store, run_id, fetched, artifact):
        self.events.append("publish")
        published = deepcopy(artifact)
        review = store.run_dir(run_id) / "review"
        if review.exists():
            raise AssertionError("recording publisher would overwrite output")
        review.mkdir(mode=0o700)
        destination = review / artifact["basename"]
        shutil.copy2(artifact["path"], str(destination))
        destination.chmod(0o600)
        published["path"] = str(destination)
        return published
```

### Step 5.2：写三条 build 顺序 RED

`tests/test_taiji_package_orchestration.py` 固定以下测试和完整 event 序列。

`test_reusable_build_has_exact_stage_order`：

```python
[
    "validate_target", "local_doctor", "build_plan", "create_transport",
    "online_doctor", "bind_online_plan", "initial_state_patch", "inspect_input",
    "create_remote_run", "transfer_input", "verify_remote_input",
    "build_remote_candidate", "fetch-review", "fetch-log",
    "validate_review", "publish", "success_state_patch",
]
```

该测试还必须读取最终 state，断言成功路径的 stage/history 尾部精确包含 `REMOTE_BUILD_SUCCEEDED → REVIEW_FETCHED → LOCAL_REVIEW_VERIFIED → CANDIDATE_BUILT`，每项只出现一次。

`test_missing_build_prepares_only_after_online_confirmation`：

```python
[
    "validate_target", "local_doctor", "build_plan", "create_transport",
    "online_doctor", "bind_online_plan", "initial_state_patch", "prepare_input", "inspect_input",
    "create_remote_run", "transfer_input", "verify_remote_input",
    "build_remote_candidate", "fetch-review", "fetch-log",
    "validate_review", "publish", "success_state_patch",
]
```

同时断言 input reader 精确调用一次且只接受 `BUILD`；state 在确认前不存在；`prepare_input` 前 online doctor 和确认均已完成。

`test_unreachable_build_stops_before_confirmation_state_and_input`：

```python
[
    "validate_target", "local_doctor", "build_plan", "create_transport",
    "online_doctor",
]
```

并断言类别 `BUILDER_UNREACHABLE`；input reader 调用 0；无 run dir/state；无 prepare、inspect 或 transport 变更。

### Step 5.3：写 fetch 边界 RED

- `test_v2_fetch_without_target_uses_frozen_target`：parser 的 target 为 `None`，不读内置默认 JSON；由 state 冻结 target 构造 adapter。事件精确为 `create_transport, fetch-review, fetch-log, validate_review, publish, success_state_patch`。
- `test_v2_fetch_with_matching_explicit_target_succeeds`：最前多一个 `validate_target`，ID 和 canonical SHA 均相等才继续。
- `test_explicit_fetch_rejects_id_or_sha_drift_before_transport`：分别改 ID/非 ID 字段，均 `PLAN_INVALID`；事件最多只有 `validate_target`，无 `create_transport`。
- `test_fetch_uses_frozen_repo_root_not_cli_repo`：向 CLI 传另一个 `--repo`，记录 adapter `create_transport` 收到的 repo，必须仍等于 state source/legacy plan 冻结路径。
- `test_v1_fetch_uses_only_kylin_normalizer_and_preserves_file_schema`：事件精确为 `normalize_legacy_state, create_transport, fetch-review, fetch-log, validate_review, publish, success_state_patch`；恢复后 schema 仍 v1，不加 v2 顶层键。
- `test_fetch_pending_never_repeats_online_prepare_or_build`：v1/v2 均不含 online、initial patch、prepare、inspect、remote create/transfer/verify/build。
- `test_fetch_stage_persistence_is_recoverable`：分别让 fetch-log、local validate、publisher 失败，state 均保持 `FETCH_PENDING` 且 `fetch_allowed=true`；两项 fetch 成功后才记录 `REVIEW_FETCHED`，validate 成功后才记录 `LOCAL_REVIEW_VERIFIED`，失败重试不得重复构建。
- `test_publish_and_final_state_failures_are_idempotently_recoverable`：故障注入分别发生在 review 发布后、remote log 发布后、success patch 计算时和最终 state replace 时；每次 state 仍为 `FETCH_PENDING`，下一次 fetch 对已存在且 identity 完全相同的组件幂等接受、只补缺项，最终进入 `CANDIDATE_BUILT`。把任一既有文件改 1 byte 后才报 `LOCAL_OUTPUT_OCCUPIED`，且不覆盖。
- `test_non_fetch_pending_is_rejected_before_adapter_factory`：factory 一调用就抛 AssertionError；预期 `FETCH_NOT_ALLOWED`，证明先 load/check stage。

### Step 5.4：RED

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_taiji_package_orchestration
```

Expected: AssertionFailure RED，不允许 fixture import/file ERROR。fixture 必须先可 import 再运行 RED。本 Task 不单独提交；与 Task 6 GREEN 一起提交，避免仓库留下必失败测试。

---

## Task 6：实现通用 orchestrator/CLI，证明 Kylin v2 与 v1 fetch 不回归

**Files:**

- Create: `packaging/pipeline/core/orchestration.py`
- Create: `packaging/pipeline/cli.py`
- Modify: `scripts/taiji-package-candidate.py`
- Modify: `packaging/pipeline/adapters/kylin_amd64.py`
- Modify: `tests/taiji_package_fixtures.py`
- Modify: `tests/test_taiji_package_orchestration.py`
- Modify: `tests/test_taiji_package_candidate.py`
- Modify: `tests/test_taiji_package_transport.py`

### Step 6.1：实现唯一 build 阶段机

`packaging.pipeline.cli.main` 的最终内部签名固定为 `main(argv=None, *, adapter_factory, command_runner, input_reader, publisher)`；函数体必须实现下列 1–12 步，不得保留 `NotImplementedError` 或占位实现。

把现有平台中立的 `_final_output_paths`、`_publish_fetched_outputs`、`_fetch_staging_path`、失败状态和 recorded-stage helper 移到 `core/orchestration.py`；把原先“一旦 final 存在就拒绝”的 `_assert_final_outputs_absent` 改为下述严格 identity 比较，不把这些私有名加入 17-name 公共合同。除本计划明确修复的可重入发布外，不改变私有目录和无覆盖语义。

`_publish_fetched_outputs` 固定处理两个组件：完整 review tree 与单个 remote-build.log。每个组件发布前用 `lstat` 拒绝 symlink/hardlink/非 owner/错误 mode，并对 staged/final 递归 regular-file 相对路径、bytes、SHA256 生成排序 identity；final 不存在则从本 run 的同文件系统 staging 用 `os.rename` 无覆盖发布，已存在且 identity 完全相同则幂等跳过，已存在但不同才 `LOCAL_OUTPUT_OCCUPIED`。review 先于 log；任一异常不删除已发布组件。publisher 返回后先计算 adapter success patch，再用一次 state store 原子 replace 同时写 artifact/patch/`CANDIDATE_BUILT`；任何失败保留 `FETCH_PENDING`，重试必须通过同一算法收敛。

通用 `publisher` 的签名固定为 `publisher(store, run_id, fetched, artifact) -> published_artifact`。facade 先适配现有三参数 `_publish_fetched_outputs`，再改为薄 wrapper；不得写静态别名 `main = pipeline_cli.main`：

```python
def _facade_publisher(store, run_id, fetched, artifact):
    published_paths = _publish_fetched_outputs(store, run_id, fetched)
    published_artifact = dict(artifact)
    published_artifact["path"] = str(
        Path(published_paths["review_path"]) / artifact["relative_path"]
    )
    return published_artifact


def main(argv=None):
    return pipeline_cli.main(
        argv,
        adapter_factory=_facade_adapter_factory,
        command_runner=_run_command,
        input_reader=input,
        publisher=_facade_publisher,
    )
```

在 `tests/test_taiji_package_core_boundaries.py` 增加 `test_final_common_modules_have_no_platform_build_literals`：先断言 `cli.py`、`core/orchestration.py` 存在，再应用 Task 4 的同一 forbidden literal 集；缺文件必须是 FAIL，不是 `read_text()` ERROR。

CLI `build` 固定顺序：

1. 未提供 target 时解析为 `kylin-amd64`；加载 JSON，创建 adapter，`validate_target`。
2. `adapter.local_doctor`；非 `CONTROLLER_READY` 按旧类别停止。
3. `adapter.build_plan`；只生成计划，不准备输入、不建 state、不 SSH。
4. `adapter.create_transport` 和 `online_doctor`；非 `BUILDER_READY` 立即以 `BUILDER_UNREACHABLE` 或 `ONLINE_DOCTOR_BLOCKED` 停止。
5. 调用一次 `adapter.bind_online_plan(plan, online)` 并执行上述 identity-diff 校验；输出 finalized plan + online 边界，调用 input reader 一次，只接受精确 `BUILD`。
6. 确认后才用 finalized plan 调用 `new_run_state()` 与 `RunStateStore.create()` 创建私有 run/state，然后按现有语义获取该 run 的 `RunLock`；确认前两者均不得发生。
7. finalized plan input 为 `MISSING` 才调用 `prepare_input`；`REUSABLE` 跳过；其他状态按旧类别停止。
8. 无论 MISSING/REUSABLE，远程变更前都再 `inspect_input`，只接受 `REUSABLE`；调用 `bind_verified_input()` 把实际三件套 identity 同时写入顶层 state input、state.plan.input 与 `identity.input_manifest_sha256`，使用它返回的 bound execution plan 进入 `INPUT_VERIFIED`，此后所有 transport/validator 调用都只传该 plan。测试必须断言 MISSING build 的 transfer/verify/build 和后续 v2 fetch 都看到非空三件套。
9. 依次 remote create、transfer、verify、build，每步使用 bound execution plan 并按旧 stage 原子写状态。
10. build 成功立即写 `REMOTE_BUILD_SUCCEEDED`、`remote_build_succeeded=true`、`fetch_allowed=true`；此前失败进 `FAILED`，此后失败进 `FETCH_PENDING`。
11. `fetch-review` 与 `fetch-log` 都成功后写 `REVIEW_FETCHED`；adapter 本地验证成功后写 `LOCAL_REVIEW_VERIFIED`。任一步失败保持 `FETCH_PENDING`，不得发布。
12. publisher 按上述 identity 合同可重入发布，计算 adapter success patch，再以一次 state 原子 replace 写 artifact/patch/`CANDIDATE_BUILT`；发布、patch 或最终 state 写失败仍为可 fetch 的 `FETCH_PENDING`，重试可接受自己留下的完全相同输出。

`plan` 只做步骤 1–3；`doctor` 只做 target + local，`--online` 才建 transport；`status` 只 `RunStateStore.load()` 并输出，不解析默认 target、不读 target JSON、不建 adapter/transport。为保持现有 CLI 兼容，即使调用者对 `status` 传了全局 `--target`，也不解析、不比较且不让它影响输出；新增 `test_status_never_resolves_implicit_or_explicit_target` 用一个一调用就 AssertionError 的 resolver/factory 证明这一点。

### Step 6.2：实现显式/隐式 fetch

fetch 先 load state，检查 `stage == FETCH_PENDING`、`remote_build_succeeded is True`、`fetch_allowed is True`。不满足就 `FETCH_NOT_ALLOWED`，不调用 adapter factory。

- **v2 + 未显式 target：**只用 state 冻结的 `target_id/config/SHA`；重算 SHA 不等为 `PLAN_INVALID`；不读内置 JSON。
- **v2 + 显式 target：**按 Task 1 解析并验证；ID 和 canonical SHA 必须与 state 完全相同，否则 `PLAN_INVALID` 且不建 transport。
- **v1 + 未显式 target：**按 schema 只建 Kylin adapter，调用 `normalize_legacy_state()`；不读当前内置 JSON，使用 v1 plan 冻结 target。
- **v1 + 显式 target：**先 normalizer 得到通用视图，再比显式 ID/SHA；不一致为 `PLAN_INVALID`。

fetch 构造 transport 时，repo 必须取 normalized state 冻结的 `source.repo_root`（v1 为 plan repo root），不得使用当前 cwd 或全局 `--repo` 覆盖历史 run；`--ssh-config` 仍只作为本次连接参数。v2 必须使用 state 中已绑定、`plan.input == input` 的 execution plan；不一致在 transport 前 `PLAN_INVALID`。检查通过后只允许：create transport → fetch review → fetch log → validate → publisher → success patch。不得 online doctor、输入检查/准备、remote create/transfer/verify/build。本地目标已存在且 identity 不同才以 `LOCAL_OUTPUT_OCCUPIED` 拒绝；完全相同或合法的单组件部分发布按 Step 6.1 幂等收敛。v1 更新只写旧 schema/键，不后台升级；Windows 永不创建 v1。

### Step 6.3：通用 GREEN

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  tests.test_taiji_package_orchestration \
  tests.test_taiji_package_target_dispatch \
  tests.test_taiji_package_state_v2 \
  tests.test_taiji_package_core_boundaries
```

Expected: `OK`；三条 build 事件严格等于 Task 5 列表；fetch 无额外远程阶段。

### Step 6.4：Kylin 兼容 GREEN

在旧测试中保留或新增：

- `test_build_cli_displays_plan_confirms_once_and_persists_result`：继续 patch facade globals；新 state 为 v2 且 stage 为 `CANDIDATE_BUILT`。
- `test_fetch_cli_rejects_target_adapter_drift_before_transport`：显式 target SHA drift 在 transport 前 `PLAN_INVALID`。
- `test_v1_fetch_pending_recovers_without_rebuild_or_schema_upgrade`：用 secure v1 fixture；无 online/prepare/00/01；schema 仍 v1。
- `test_reusable_kylin_input_skips_99`：REUSABLE 时 prepare 次数 0。
- `test_missing_kylin_input_is_not_prepared_when_builder_unreachable`：UNREACHABLE 时 input runner 0，无 state/run dir。
- `test_existing_kylin_failure_categories_are_unchanged`：对 0.3 旧类别做精确集合断言；`TARGET_INVALID` 单独断言。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  tests.test_taiji_package_candidate \
  tests.test_taiji_package_transport \
  tests.test_linux_golden_orchestrator
```

Expected: `OK`。

### Step 6.5：提交 Task 5/6

```bash
git add packaging/pipeline/core/orchestration.py packaging/pipeline/cli.py \
  packaging/pipeline/adapters/kylin_amd64.py scripts/taiji-package-candidate.py \
  tests/taiji_package_fixtures.py tests/test_taiji_package_orchestration.py \
  tests/test_taiji_package_candidate.py tests/test_taiji_package_transport.py
git commit -m "refactor(packaging): dispatch candidate CLI through Kylin adapter"
```

若为了 GREEN 调整事件列表、让 fetch 重做 build、让 v1 升 v2、让隐式 fetch 读当前 target JSON，或让 facade patch 失效，必须停止。

## Task 7：Python 3.8、isolated import、pycache-safe 和全回归门禁

**Files:**

- Modify: `tests/python38_linux_packaging_gate.py`
- Verify: Tasks 1–6 所有文件

### Step 7.1：更新 Python 3.8 显式清单

grammar gate 必须显式列出，不用 glob/目录扫描代替：

```text
scripts/taiji-package-candidate.py
packaging/pipeline/__init__.py
packaging/pipeline/cli.py
packaging/pipeline/core/__init__.py
packaging/pipeline/core/errors.py
packaging/pipeline/core/models.py
packaging/pipeline/core/orchestration.py
packaging/pipeline/core/registry.py
packaging/pipeline/core/state.py
packaging/pipeline/adapters/__init__.py
packaging/pipeline/adapters/base.py
packaging/pipeline/adapters/kylin_amd64.py
```

禁止 built-in generic、`X | Y`、`match`、`removeprefix` 等 Python 3.9+ 语法/API。

### Step 7.2：使用不产生 pycache 的 compile gate

不使用 `python -m py_compile`；它会显式写 `.pyc`，不能把 `PYTHONDONTWRITEBYTECODE=1` 当作无写入证据。改用内存 `compile()`：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
from pathlib import Path

paths = (
    "scripts/taiji-package-candidate.py",
    "packaging/pipeline/__init__.py",
    "packaging/pipeline/cli.py",
    "packaging/pipeline/core/__init__.py",
    "packaging/pipeline/core/errors.py",
    "packaging/pipeline/core/models.py",
    "packaging/pipeline/core/orchestration.py",
    "packaging/pipeline/core/registry.py",
    "packaging/pipeline/core/state.py",
    "packaging/pipeline/adapters/__init__.py",
    "packaging/pipeline/adapters/base.py",
    "packaging/pipeline/adapters/kylin_amd64.py",
)
for name in paths:
    compile(Path(name).read_bytes(), name, "exec")
print("COMPILE_OK")
PY
```

Expected: 只输出 `COMPILE_OK`。

### Step 7.3：实际 isolated import gate

从非仓库 cwd 运行真 launcher、真 shim 和来源 probe：

```bash
cd /private/tmp
/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller/taiji-package --help
/usr/bin/python3 -I -B /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller/scripts/taiji-package-candidate.py --help
/usr/bin/python3 -I -B -c "import runpy,sys;ns=runpy.run_path(sys.argv[1],run_name='candidate_probe');print(ns['_pipeline_package'].__file__)" /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller/scripts/taiji-package-candidate.py
```

Expected: 前两条退出 0；第三条只输出该 worktree 的 `packaging/pipeline/__init__.py`，不是 system/site-packages。三条只运行 help/import，不 SSH。

回到 worktree 后检查：

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller
find packaging/pipeline scripts tests -name '__pycache__' -print
find packaging/pipeline scripts tests -name '*.pyc' -print
```

Expected: 均无输出。若开工前已有 pycache，不擅自删除；停止并说明无法得出“本轮无产生”证据。

### Step 7.4：全量本地门禁

```bash
bash -n taiji-package
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  tests.test_taiji_package_target_dispatch \
  tests.test_taiji_package_state_v2 \
  tests.test_taiji_package_core_boundaries \
  tests.test_taiji_package_orchestration \
  tests.test_taiji_package_candidate \
  tests.test_taiji_package_transport \
  tests.test_linux_golden_orchestrator
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  tests.test_taiji_kylin_packaging_skill \
  tests.test_builder_input_package_contract
PYTHONDONTWRITEBYTECODE=1 python3 tests/python38_linux_packaging_gate.py
git diff --check
git status --short
```

Expected: shell/gates 退出 0；unittest 全部 `OK`；diff check 无输出；提交前 status 只显示本 Task 的 gate 文件。若有其他路径，先审计归属并停止。

若本机有真实 Python 3.8，再用它执行 target/state/orchestration 测试；没有时只报告“3.8 grammar gate 通过，真实 3.8 runtime 未验证”，不以当前 Python 冒充。

### Step 7.5：提交并复跑

```bash
git add tests/python38_linux_packaging_gate.py
git commit -m "test(packaging): gate cross-platform candidate core"
```

提交后重跑 Step 7.2–7.4，最终 `git status --short` 必须无输出。

---

## 最终验收和交接口径

只有以下全部成立才可标记本计划完成：

- launcher 从仓库外 cwd 在 `-I -B` 下可用，并证明导入来自该 worktree；
- parser 保留 `target=None`，仅 doctor/plan/build 应用 Kylin 默认；
- 新 run 只用完整 v2 工厂，target SHA 和 frozen identity 受保护；
- v1 Linux 映射只在 `KylinAmd64Adapter.normalize_legacy_state()`，文件不升级；
- adapter 精确实现 11 hooks，facade 保留 17 个名称和现有 monkeypatch seam；
- REUSABLE/MISSING/UNREACHABLE 顺序与 Task 5 精确相等；
- v1/v2 fetch 只取回、验证和无覆盖发布，不重建；
- 所有 RED 都是 AssertionFailure，所有 GREEN 和旧 Kylin 模拟门禁有当前 `OK`；
- 没有真实 SSH、`99/00/01`、DEB 构建、安装、签名或发布。

最终状态只允许写：

```text
跨平台 core/Kylin adapter 已实现，本地模拟通过
真实 Kylin 未验证
Windows adapter 尚未实现
候选 DEB/EXE 未构建
```

任一验收不成立时，保留本地分支和失败证据，不 push、不 PR、不 merge；在 handoff 写明具体阶段、命令和第一个失败断言。
