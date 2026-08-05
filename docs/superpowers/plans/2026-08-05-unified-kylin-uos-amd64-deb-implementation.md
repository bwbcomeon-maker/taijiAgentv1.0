# Unified Kylin/UOS amd64 DEB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从正式 `main` 构建一次 `taiji-agent_${VERSION}_amd64.deb`，使同一字节和 SHA256 可在第一版支持范围内的 Kylin、UOS、openKylin x86_64 图形桌面终端断网双击或静默安装，并用策略、认证集、发布回执、升级回滚和隐私证据形成销售放行闭环。

**Architecture:** 以源码受控、不可被客户环境覆盖的 `taiji-linux-compatibility-policy/v1` 取代逐终端 baseline；DEB 私有化允许捆绑的用户态运行库，但保留 glibc、loader、内核、PAM、systemd、DBus 和图形驱动等宿主边界。构建先固定 DEB SHA256，再由离线演练和六类代表环境生成单环境记录，认证集聚合同一 DEB，最终 v3 发布回执绑定源码、DEB、策略和签名认证集；认证结果永不写回 DEB。

**Tech Stack:** Bash、Python 3.11 标准库、Debian `dpkg-deb`/`dpkg`/`apt-get`、Electron 39/Node 22、`readelf`/`patchelf`、OpenSSL detached signatures、Docker amd64 rehearsal、Python `unittest`、Node test runner、GitHub Actions。

---

## 实施边界与固定接口

本计划只实现已经批准的国产 Linux 第一阶段：`amd64/x86_64 + 图形桌面 + dpkg/apt + Kylin/UOS/openKylin`。ARM、RPM-only、无桌面服务器和 Windows 不进入本轮代码或制品；Windows 只有在 Linux 同一 DEB 完成真实矩阵并由用户确认可用后才另立规格和分支。

本计划虽然横跨构建、证据和生命周期三个子系统，但不拆成独立可发布计划，因为三者共享以下不可分割的发布身份：

```text
source_commit + version + architecture=amd64
+ deb_basename + deb_sha256
+ compatibility_policy_id + compatibility_policy_sha256
+ certification_set_sha256
```

每个任务仍保持独立提交；后续任务只能消费前一任务冻结的字段，不得建立第二套同义 schema。

固定值：

```text
policy schema:    taiji-linux-compatibility-policy/v1
policy id:        taiji-linux-amd64-deb-v1
package:          taiji-agent
architecture:     amd64 / x86_64
install root:     /opt/taiji-agent
maintainer:       Taiji Agent Product Team <noreply@localhost>
customer file:    taiji-agent_${VERSION}_amd64.deb
preinst result:   COMPATIBLE | BLOCKED
certification:    CERTIFIED 仅由签名 certification set 给出
```

状态口径固定为：`分支已实现`、`已合并 main`、`制品已生成`、`离线安装已演练`、`目标机已验证`、`已发布`。前一状态不能推出后一状态。

## 文件责任图

### 新增文件

- `packaging/linux/compatibility-policy.json`：唯一兼容政策源，采用 canonical JSON。
- `packaging/linux/compatibility_policy.py`：严格加载、canonical hash、Debian Depends 和 shell 常量渲染。
- `packaging/linux/audit-elf-closure.py`：ELF 架构、ABI、SONAME、RPATH/RUNPATH 与闭包审计。
- `packaging/linux/stage-private-libraries.py`：只从策略允许集合复制私有用户态动态库。
- `packaging/linux/deployment_receipt.py`：静默部署 receipt 严格 schema 与原子写入。
- `packaging/linux/deb/taiji-silent-deploy.sh`：管理端静默安装薄入口，不进入客户单 DEB 目录。
- `packaging/linux/upgrade-data-contract.json`：配置、授权、会话、附件、workspace、Skills 和模板的数据兼容合同。
- `packaging/linux/upgrade_transaction.py`：升级 journal、快照、迁移、回滚和恢复状态机。
- `packaging/linux/support_bundle.py`、`packaging/linux/bin/taiji-agent-support`：脱敏支持包。
- `packaging/linux/certification-matrix.json`：六个正向类别和负向边界合同。
- `scripts/assemble-taiji-certification-set.py`：把多环境记录聚合为不可变认证集。
- `scripts/assemble-taiji-release-evidence.py`：生成最终 `taiji-release-evidence/v3`。
- 对应的 `tests/test_*.py` 和 `tests/fixtures/elf-audit/*`：合同与回归测试。

### 主要修改文件

- `packaging/linux/deb/preinst`、`render-preinst.py`：从精确版本比较改为能力分类。
- `packaging/linux/deb/build-deb.sh`、payload contract/validator/launchers：策略绑定、私有库和 ABI 门禁。
- `taijiagent 打包交付/00_制包机_生成离线交付包.sh`、`01_制包机_发布预检.sh`：移除客户 baseline、人工 Maintainer 和离线 apt 仓库依赖。
- `scripts/validate-taiji-release-evidence.py`、signer、release-check、publisher：迁移到 policy + certification set + v3。
- offline rehearsal、目标终端观察器、桌面验收汇编器：输出同一候选的环境记录和 lifecycle receipt。
- `postinst`、`postrm`、`02/03/04` 内部工具：升级回滚、诊断和真实验收。
- runbook、销售就绪说明、操作说明、版本信息和 CI 分类器：同步产品边界和门禁。

### 最终删除文件

- `packaging/linux/capture-target-baseline.sh`
- `packaging/linux/target_baseline.py`
- `packaging/linux/validate-approved-maintainer.py`
- `packaging/linux/approved-maintainer.example.json`
- `packaging/linux/deb/runtime-depends.txt`
- 被新合同替代的 baseline、版本化 Depends 和人工 Maintainer 测试。

## 执行前来源门禁

- [ ] 在专用 worktree 执行并保存输出：

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/linux-sales-grade-installer
pwd -P
git rev-parse --show-toplevel
git rev-parse --git-common-dir
git branch --show-current
git rev-parse HEAD
git status --short
```

预期：worktree 为上述绝对路径，分支为 `codex/linux-sales-grade-installer`，起始 HEAD 为 `494a28c46ffcc7a525d09a1a1af63b18848f7bdf`，执行首个修改前工作树干净。若任一项不一致，停止实现并先恢复来源边界。

### Task 1: Canonical 兼容策略与固定包身份

**Files:**

- Create: `packaging/linux/compatibility-policy.json`
- Create: `packaging/linux/compatibility_policy.py`
- Create: `tests/test_linux_compatibility_policy.py`

- [ ] **Step 1: 写策略合同的失败测试**

测试必须逐项断言：仓库策略是 canonical JSON；未知、重复和环境覆盖字段被拒绝；包名、架构、安装根和 Maintainer 固定；只接受三个 OS ID；private SONAME 与 system/forbidden 集合互斥且 required-system 是 forbidden-bundled 的子集；Debian Depends 不含某台终端采集的版本。

```text
test_repository_policy_is_canonical_and_hash_stable
test_policy_rejects_duplicate_unknown_and_noncanonical_fields
test_policy_fixes_product_identity_and_rejects_environment_override
test_policy_supports_only_three_deb_amd64_families
test_private_library_set_is_disjoint_and_system_core_is_forbidden
test_debian_depends_contains_no_target_captured_versions
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python3 -m unittest tests.test_linux_compatibility_policy -v`

Expected: FAIL，原因是 `packaging/linux/compatibility_policy.py` 和 policy 文件尚不存在。

- [ ] **Step 3: 写入完整 policy 与严格加载接口**

`compatibility-policy.json` 顶层 exact fields 固定为：

```json
{
  "schema": "taiji-linux-compatibility-policy/v1",
  "policy_id": "taiji-linux-amd64-deb-v1",
  "package": {
    "name": "taiji-agent",
    "architecture": "amd64",
    "install_root": "/opt/taiji-agent",
    "maintainer": "Taiji Agent Product Team <noreply@localhost>"
  },
  "architecture": {"uname_machine": ["x86_64"], "dpkg": ["amd64"]},
  "os_families": [
    {"family": "kylin", "ids": ["kylin"]},
    {"family": "uos", "ids": ["uos"]},
    {"family": "openkylin", "ids": ["openkylin"]}
  ],
  "minimum_supported": {"glibc": "2.31", "kernel": "4.19.0"},
  "system_capabilities": {
    "commands": ["/usr/bin/apt-get", "/usr/bin/dpkg", "/usr/bin/systemctl"],
    "desktop_session_dirs": ["/usr/share/xsessions", "/usr/share/wayland-sessions"],
    "loopback_path": "/sys/class/net/lo",
    "install_root_parent": "/opt",
    "disk_headroom_mib": 6144
  },
  "debian": {
    "depends": ["ca-certificates", "libc6 (>= 2.31)"]
  },
  "elf": {
    "maximum_symbol_versions": {"GLIBC": "2.31", "GLIBCXX": "3.4.28", "CXXABI": "1.3.12"},
    "private_library_dir": "/opt/taiji-agent/runtime/lib",
    "allowed_private_sonames": [
      "libasound.so.2", "libatk-1.0.so.0", "libatk-bridge-2.0.so.0", "libatspi.so.0",
      "libcairo.so.2", "libcups.so.2", "libexpat.so.1", "libfontconfig.so.1",
      "libgio-2.0.so.0", "libglib-2.0.so.0", "libgobject-2.0.so.0", "libgtk-3.so.0",
      "libnspr4.so", "libnss3.so", "libnssutil3.so", "libpango-1.0.so.0",
      "libpangocairo-1.0.so.0", "libpangoft2-1.0.so.0", "libplc4.so", "libplds4.so",
      "libsecret-1.so.0", "libuuid.so.1", "libX11.so.6", "libX11-xcb.so.1",
      "libxcb.so.1", "libXcomposite.so.1", "libXdamage.so.1", "libXext.so.6",
      "libXfixes.so.3", "libxkbcommon.so.0", "libXrandr.so.2", "libXrender.so.1",
      "libXshmfence.so.1", "libXss.so.1", "libXtst.so.6"
    ],
    "required_system_sonames": [
      "libdbus-1.so.3", "libdrm.so.2", "libgbm.so.1", "libGL.so.1", "libEGL.so.1", "libGLX.so.0"
    ],
    "forbidden_bundled_sonames": [
      "ld-linux-x86-64.so.2", "libc.so.6", "libdl.so.2", "libm.so.6", "libpthread.so.0",
      "librt.so.1", "libpam.so.0", "libsystemd.so.0", "libdbus-1.so.3", "libdrm.so.2",
      "libgbm.so.1", "libGL.so.1", "libEGL.so.1", "libGLX.so.0"
    ],
    "allowed_runpaths": ["$ORIGIN", "$ORIGIN/../lib", "/opt/taiji-agent/runtime/lib"]
  }
}
```

文件以 UTF-8、`sort_keys=True`、compact separators、末尾一个换行保存；JSON 内不放自指 hash。`compatibility_policy.py` 暴露并严格实现：

上述 glibc/kernel/SONAME 值是要由实现和矩阵验证的源码承诺，不是当前已验证事实。Task 3/4 的真实 payload audit 或 Task 18 的最低环境若不满足，必须阻断放行；只有修改 policy、重新构建并让旧认证集失效后才能继续，不能在构建后回写或放宽。

```text
load_and_validate(path: Path) -> dict[str, Any]
canonical_bytes(policy: dict[str, Any]) -> bytes
canonical_sha256(policy: dict[str, Any]) -> str
render_debian_depends(policy: dict[str, Any]) -> str
shell_exports(policy: dict[str, Any]) -> dict[str, str]
```

CLI 固定为：

```text
compatibility_policy.py validate --policy PATH
  [--print-id | --print-sha256 | --print-maintainer | --print-depends | --print-shell]
```

策略值只能来自文件；`TAIJI_PACKAGE_MAINTAINER`、`TAIJI_TARGET_BASELINE_*` 或其它环境变量不能覆盖。

- [ ] **Step 4: 运行策略测试确认 GREEN**

Run: `python3 -m unittest tests.test_linux_compatibility_policy -v`

Expected: 输出 `Ran 6 tests` 和 `OK`。

- [ ] **Step 5: 提交策略合同**

```bash
git add packaging/linux/compatibility-policy.json packaging/linux/compatibility_policy.py tests/test_linux_compatibility_policy.py
git commit -m "feat(packaging): add canonical Linux compatibility policy"
```

### Task 2: 通用 capability preinst

**Files:**

- Modify: `packaging/linux/deb/render-preinst.py`
- Modify: `packaging/linux/deb/preinst`
- Delete: `tests/test_target_baseline_preinst.py`
- Create: `tests/test_compatibility_policy_preinst.py`

- [ ] **Step 1: 先写 COMPATIBLE/BLOCKED 失败测试**

测试通过 `ROOT_PREFIX` fixture 模拟 os-release、systemd、桌面 session、loopback、`/opt` 和磁盘；继续保留可信 `/etc/os-release` symlink、owner、mode 防护。精确测试集合：

```text
test_all_three_families_accept_arbitrary_patch_strings
test_newer_glibc_and_kernel_are_compatible
test_arm_unknown_os_and_rpm_only_are_blocked
test_old_glibc_and_kernel_have_stable_error_codes
test_missing_desktop_systemd_loopback_opt_or_disk_is_blocked
test_opt_noexec_known_kysec_or_sandbox_denial_is_blocked_before_install
test_os_release_symlink_owner_and_mode_are_hardened
test_result_never_contains_certified_or_machine_identity
test_failure_creates_no_service_or_user_business_data
```

- [ ] **Step 2: 运行测试确认精确版本旧逻辑失败**

Run: `python3 -m unittest tests.test_compatibility_policy_preinst -v`

Expected: FAIL，旧 renderer 要求 `--profile`，且 UOS/openKylin 的非精确版本被旧 baseline 逻辑拒绝。

- [ ] **Step 3: 按 policy 渲染 preinst**

renderer 参数改为：

```text
render-preinst.py --template packaging/linux/deb/preinst
  --policy packaging/linux/compatibility-policy.json
  --output "$BUILD_ROOT/DEBIAN/preinst"
```

preinst 可测试入口固定为：

```bash
verify_compatibility \
  OS_RELEASE ARCH GLIBC KERNEL ROOT_PREFIX OWNER_UID RESULT_PATH
```

`RESULT_PATH` 使用临时文件、`fsync` 和 rename 写 `taiji-install-preflight/v1`：

```json
{
  "schema": "taiji-install-preflight/v1",
  "status": "COMPATIBLE",
  "policy_id": "taiji-linux-amd64-deb-v1",
  "compatibility_policy_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "error_code": "",
  "reason_zh": "兼容能力预检通过",
  "failed_capabilities": []
}
```

失败稳定码固定为 `TAIJI-LINUX-E001-ARCH`、`TAIJI-LINUX-E002-OS`、`TAIJI-LINUX-E003-DPKG`、`TAIJI-LINUX-E004-GLIBC`、`TAIJI-LINUX-E005-KERNEL`、`TAIJI-LINUX-E006-DESKTOP`、`TAIJI-LINUX-E007-SYSTEMD`、`TAIJI-LINUX-E008-LOOPBACK`、`TAIJI-LINUX-E009-DISK`、`TAIJI-LINUX-E010-PRIVILEGE`、`TAIJI-LINUX-E011-KYSEC`、`TAIJI-LINUX-E012-OPT-NOEXEC`、`TAIJI-LINUX-E013-SANDBOX`。成功只输出 `COMPATIBLE`；不得输出 `CERTIFIED`。

- [ ] **Step 4: 运行测试与 shell 语法检查**

```bash
python3 -m unittest tests.test_compatibility_policy_preinst -v
bash -n packaging/linux/deb/preinst
```

Expected: 所有测试 OK，`bash -n` 返回 0。

- [ ] **Step 5: 提交通用预检**

```bash
git add packaging/linux/deb/render-preinst.py packaging/linux/deb/preinst tests/test_target_baseline_preinst.py tests/test_compatibility_policy_preinst.py
git commit -m "feat(packaging): make preinst capability based"
```

### Task 3: ELF/ABI 闭包与私有运行库

**Files:**

- Create: `packaging/linux/audit-elf-closure.py`
- Create: `packaging/linux/stage-private-libraries.py`
- Create: `tests/test_linux_elf_abi_closure.py`
- Create: `tests/fixtures/elf-audit/readelf-header-x86_64.txt`
- Create: `tests/fixtures/elf-audit/readelf-dynamic-safe.txt`
- Create: `tests/fixtures/elf-audit/readelf-version-info-safe.txt`
- Create: `tests/fixtures/elf-audit/readelf-version-info-too-new.txt`
- Modify: `packaging/linux/bin/taiji-agent`
- Modify: `packaging/linux/bin/taiji`
- Modify: `packaging/linux/bin/taiji-native-verify`

- [ ] **Step 1: 写 readelf 驱动的失败测试**

```text
test_scans_every_elf_native_wheel_electron_node_and_python
test_rejects_non_x86_64_and_symbol_version_above_policy
test_rejects_dt_rpath_absolute_or_escaping_runpath
test_rejects_unresolved_or_ambiguous_soname
test_rejects_bundled_glibc_loader_pam_systemd_dbus_and_driver_core
test_rejects_build_host_absolute_path_leak
test_allows_origin_runpath_and_policy_private_sonames
test_stager_rejects_symlink_hardlink_wrong_owner_and_non_allowlisted_source
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python3 -m unittest tests.test_linux_elf_abi_closure -v`

Expected: FAIL，两个新脚本不存在。

- [ ] **Step 3: 实现私有库 staging 与闭包报告**

CLI 固定为：

```text
stage-private-libraries.py --root ROOT --policy POLICY --sysroot SYSROOT --output REPORT
audit-elf-closure.py --root ROOT --policy POLICY --output REPORT [--sysroot SYSROOT]
```

审计以 `readelf -h/-d/--version-info` 为权威，`ldd` 只在 Linux 构建机对受信 payload 做二次 smoke。输出 schema：

```json
{
  "schema": "taiji-elf-abi-audit/v1",
  "policy_id": "taiji-linux-amd64-deb-v1",
  "compatibility_policy_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "max_required_versions": {"GLIBC": "2.31", "GLIBCXX": "3.4.28", "CXXABI": "1.3.12"},
  "external_sonames": [],
  "private_sonames": [],
  "files": []
}
```

`files[]` exact fields 为 `relative_path,sha256,machine,needed,runpath,version_needs`。stager 只把 root-owned、regular、single-link、allowlisted SONAME 原子复制到 `/opt/taiji-agent/runtime/lib`；不得复制 policy 的 required-system 或 forbidden 集合。launchers 只对太极子进程设置固定私有库搜索路径，不修改 `/etc/ld.so.conf*` 或全局 loader。

- [ ] **Step 4: 运行闭包单测**

Run: `python3 -m unittest tests.test_linux_elf_abi_closure -v`

Expected: 所有测试 OK。macOS fixture 只证明解析合同，真实 Linux payload 审计留到 Task 16。

- [ ] **Step 5: 提交 ELF 门禁**

```bash
git add packaging/linux/audit-elf-closure.py packaging/linux/stage-private-libraries.py packaging/linux/bin/taiji-agent packaging/linux/bin/taiji packaging/linux/bin/taiji-native-verify tests/test_linux_elf_abi_closure.py tests/fixtures/elf-audit
git commit -m "feat(packaging): enforce ELF ABI closure"
```

### Task 4: 构建一个 policy-bound amd64 DEB

**Files:**

- Modify: `packaging/linux/deb/build-deb.sh`
- Modify: `packaging/linux/payload-contract.json`
- Modify: `packaging/linux/verify-payload.py`
- Create: `tests/test_unified_deb_build_contract.py`
- Modify: `tests/test_linux_payload_contract.py`
- Modify: `tests/test_linux_desktop_packaging_static.py`
- Delete: `tests/test_versioned_runtime_depends.py`
- Delete: `tests/test_approved_release_maintainer.py`
- Delete: `packaging/linux/validate-approved-maintainer.py`
- Delete: `packaging/linux/approved-maintainer.example.json`

- [ ] **Step 1: 写统一构建 RED 合同**

测试必须断言：build 不读取 baseline 或 Maintainer env；control 身份逐字固定；Depends 只由 policy 渲染；DEB 内 policy 与源码 canonical bytes 相同；payload 含 ABI report 和私有库目录；preinst 使用同一 policy；认证集、最终证据和 publication receipt 不在 DEB 中。

```text
test_build_has_no_customer_specific_inputs
test_control_identity_and_depends_come_only_from_policy
test_deb_embeds_exact_policy_and_abi_report
test_build_host_glibc_cannot_exceed_policy_floor
test_preinst_and_manifest_bind_same_policy_hash
test_deb_never_embeds_certification_or_publication_evidence
```

- [ ] **Step 2: 运行测试确认旧 build 失败**

```bash
python3 -m unittest tests.test_unified_deb_build_contract tests.test_linux_payload_contract -v
```

Expected: FAIL，旧 build 仍引用 `TAIJI_TARGET_BASELINE_FILE`、`TAIJI_PACKAGE_MAINTAINER`、版本化 Depends 和 `target-baseline.json`。

- [ ] **Step 3: 改造 build-deb 和 payload contract**

构建顺序固定为：

```text
validate canonical policy
→ stage Electron/Node/Python/application
→ stage allowlisted private libraries
→ audit every final ELF
→ render preinst from the same policy
→ embed linux-compatibility-policy.json and elf-abi-audit.json
→ render control with fixed Maintainer/Depends
→ dpkg-deb --build
→ unpack final DEB and re-run payload/ELF/policy binding checks
```

manifest 使用 `taiji-package-manifest/v3`，至少包含：

```text
schema, package, version, architecture, source_commit,
deb_basename, deb_sha256, maintainer,
compatibility_policy_id, compatibility_policy_sha256,
elf_abi_audit_basename, elf_abi_audit_sha256,
electron_executable_sha256, desktop_entry_sha256, built_at_utc
```

DEB 内策略固定路径 `/opt/taiji-agent/resources/linux-compatibility-policy.json`，审计报告固定路径 `/opt/taiji-agent/resources/elf-abi-audit.json`。删除所有 baseline/profile/人工身份输入和嵌入逻辑。

- [ ] **Step 4: 运行构建合同与 shell 检查**

```bash
python3 -m unittest tests.test_unified_deb_build_contract tests.test_linux_payload_contract tests.test_linux_desktop_packaging_static -v
bash -n packaging/linux/deb/build-deb.sh
```

Expected: 所有测试 OK，shell 检查返回 0。此时仅是源码合同，不宣称已生成 Linux DEB。

- [ ] **Step 5: 提交 DEB 构建改造**

```bash
git add packaging/linux/deb/build-deb.sh packaging/linux/payload-contract.json packaging/linux/verify-payload.py packaging/linux/validate-approved-maintainer.py packaging/linux/approved-maintainer.example.json tests/test_unified_deb_build_contract.py tests/test_linux_payload_contract.py tests/test_linux_desktop_packaging_static.py tests/test_versioned_runtime_depends.py tests/test_approved_release_maintainer.py
git commit -m "feat(packaging): build one policy-bound amd64 deb"
```

### Task 5: 正式 00/01 构建与预检编排去终端化

**Files:**

- Modify: `taijiagent 打包交付/00_制包机_生成离线交付包.sh`
- Modify: `taijiagent 打包交付/01_制包机_发布预检.sh`
- Modify: `tests/test_single_deb_sales_contract.py`
- Modify: `tests/test_linux_desktop_packaging_static.py`

- [ ] **Step 1: 写构建编排失败测试**

```text
test_builder_uses_only_source_controlled_policy
test_builder_has_no_baseline_or_maintainer_input
test_marker_report_and_manifest_bind_policy_and_abi_audit
test_preflight_rejects_policy_or_audit_hash_drift
test_customer_contract_has_no_second_deb_or_offline_repo
test_builder_does_not_download_runtime_dependencies_after_candidate_is_fixed
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python3 -m unittest tests.test_single_deb_sales_contract tests.test_linux_desktop_packaging_static -v`

Expected: FAIL，旧 00/01 仍要求 `目标基线/target-baseline.json`、30 天年龄、人工 Maintainer 和离线 apt 依赖仓库。

- [ ] **Step 3: 删除 per-target build inputs 并升级 marker/report**

00 只从 source archive 读取 canonical policy；不再接收、复制或透传 baseline、profile、Depends 版本和 Maintainer env。`.build-success` exact keys 固定为：

```text
version, source_archive, source_sha256, source_commit,
deb, deb_sha256, checksum, built_at_utc, manifest,
compatibility_policy_id, compatibility_policy_sha256,
elf_abi_audit_sha256, maintainer
```

01 复算 source policy、DEB 内 policy、manifest、marker 和 audit 摘要的一致性，并验证 output allowlist。内部 `生成的安装包` 可以含 DEB、checksum、manifest、policy、audit 和构建报告；客户发布目录由 publisher 原子生成且只能含一个 DEB。删除 `apt-get download`、`Packages`、`Packages.gz` 和目标 profile 绑定作为客户路径前置。

- [ ] **Step 4: 运行构建链静态回归**

```bash
python3 -m unittest tests.test_single_deb_sales_contract tests.test_linux_desktop_packaging_static -v
bash -n 'taijiagent 打包交付/00_制包机_生成离线交付包.sh'
bash -n 'taijiagent 打包交付/01_制包机_发布预检.sh'
```

Expected: 所有测试 OK；两个脚本语法检查返回 0。

- [ ] **Step 5: 提交构建编排迁移**

```bash
git add 'taijiagent 打包交付/00_制包机_生成离线交付包.sh' 'taijiagent 打包交付/01_制包机_发布预检.sh' tests/test_single_deb_sales_contract.py tests/test_linux_desktop_packaging_static.py
git commit -m "refactor(packaging): remove target-bound build inputs"
```

### Task 6: Release evidence v3 与 v2 只读隔离

**Files:**

- Modify: `scripts/validate-taiji-release-evidence.py`
- Create: `tests/test_release_evidence_schema_v3.py`
- Modify: `tests/test_release_evidence_schema_v2.py`

- [ ] **Step 1: 写 v3/v2 边界 RED 测试**

```text
test_v3_build_binding_uses_compatibility_policy_identity
test_v3_rejects_target_baseline_fields
test_v3_rejects_policy_hash_mismatch
test_v2_requires_explicit_legacy_read_only
test_v2_cannot_be_pre_signed_or_used_as_current_release
```

- [ ] **Step 2: 运行测试确认旧 validator 失败**

Run: `python3 -m unittest tests.test_release_evidence_schema_v2 tests.test_release_evidence_schema_v3 -v`

Expected: FAIL，旧 validator 强制 `schema_version=2` 和 target baseline 字段。

- [ ] **Step 3: 增加命名 BuildBinding 并冻结旧证据**

`validate_build_binding()` 返回命名对象：

```python
@dataclass(frozen=True)
class BuildBinding:
    source_commit: str
    version: str
    architecture: str
    deb_basename: str
    deb_sha256: str
    compatibility_policy_id: str
    compatibility_policy_sha256: str
    electron_executable_sha256: str
    desktop_entry_sha256: str
```

当前验证入口只接受 v3 合同并拒绝 `target_baseline_profile_id/sha256`。v2 只有显式 `--legacy-v2-read-only` 可检查历史文件，输出状态必须标记 `LEGACY_READ_ONLY`；该模式禁止 `--pre-sign`，且 signer、release-check 和 publisher 均不消费其结果。

- [ ] **Step 4: 运行 v2/v3 单测**

Run: `python3 -m unittest tests.test_release_evidence_schema_v2 tests.test_release_evidence_schema_v3 -v`

Expected: 所有测试 OK。

- [ ] **Step 5: 提交 v3 基础合同**

```bash
git add scripts/validate-taiji-release-evidence.py tests/test_release_evidence_schema_v2.py tests/test_release_evidence_schema_v3.py
git commit -m "feat(packaging): add release evidence v3 contracts"
```

### Task 7: 静默部署 receipt

**Files:**

- Create: `packaging/linux/deployment_receipt.py`
- Create: `packaging/linux/deb/taiji-silent-deploy.sh`
- Create: `tests/test_silent_deployment_receipt.py`
- Modify: `taijiagent 打包交付/02_目标终端_安装并验证.sh`
- Modify: `tests/test_kylin_install_script_simulation.py`

- [ ] **Step 1: 写 receipt 与 no-download RED 测试**

```text
test_success_receipt_has_exact_schema_and_no_machine_identity
test_failure_receipt_is_atomic_mode_0600_and_contains_stable_error_code
test_fresh_reinstall_upgrade_and_explicit_rollback_have_stable_results
test_hash_or_signature_failure_occurs_before_dpkg_mutation
test_certification_admission_requires_build_binding_and_one_time_challenge
test_lock_conflict_and_interruption_leave_dpkg_state_unchanged
test_silent_deploy_never_updates_sources_or_downloads
test_customer_directory_does_not_require_or_publish_management_script
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python3 -m unittest tests.test_silent_deployment_receipt tests.test_kylin_install_script_simulation -v`

Expected: FAIL，新模块和静默入口不存在，旧 02 仍是完整离线仓库安装路线。

- [ ] **Step 3: 实现 receipt 与内部薄封装**

`taiji-linux-deployment-receipt/v1` exact fields：

```text
schema, deployment_id, operation, result, source_commit,
version_before, version_requested, version_after, architecture,
deb_basename, deb_sha256, compatibility_policy_id,
compatibility_policy_sha256, preflight, dpkg_status_before,
dpkg_status_after, native_verify, started_at_utc, finished_at_utc,
error_stage, error_code, rollback_transaction_id
```

`operation` 仅为 `fresh_install|reinstall|upgrade|rollback`；`result` 仅为 `installed|reinstalled|upgraded|rolled_back|blocked|manual_recovery_required`。禁止 hostname、username、HOME、IP、MAC、序列号、原始命令行、原始异常和凭据。receipt 用同目录临时文件、`fsync`、`os.replace` 原子写，mode `0600`，失败也输出稳定结果。

静默入口在触碰 dpkg 前验签并核对期望 version/SHA；使用 `/run/lock/taiji-agent-deploy.lock`；设置 `DEBIAN_FRONTEND=noninteractive` 和 `NEEDRESTART_MODE=a`；只用本地 DEB 和 no-download 路径；禁止 `apt update`、在线回退或 `ONLINE_OK`。02 仅作为内部认证/管理薄封装，不进入客户目录。

CLI 固定为：

```text
taiji-silent-deploy.sh
  --deb PATH
  --expected-version VERSION
  --expected-sha256 SHA256
  --admission-mode certification|release
  --operation fresh_install|reinstall|upgrade|rollback
  --receipt PATH
  [--build-manifest PATH --policy PATH --certification-challenge HEX]
  [--release-evidence PATH --release-signature PATH]
  [--business-user LOGIN]
  [--previous-deb PATH --previous-sha256 SHA256]
```

`release` 模式必须验签最终 v3；`certification` 模式只供最终签名前的受控矩阵，必须同时绑定 build manifest、canonical policy、期望 SHA 和独立一次性 challenge，并把该 admission mode 写入环境记录，不能用于生产投放。这样矩阵可以验证静默路径，又不会让尚未存在的 certification set 形成签名循环。集中管理面提供的控制文件不进入客户单 DEB 目录。普通双击路径由受控分发、DEB 摘要、`preinst` 和安装后证据闭环，不虚构系统图形安装器具备自定义 detached-signature UI。

- [ ] **Step 4: 运行 receipt 与安装模拟测试**

Run: `python3 -m unittest tests.test_silent_deployment_receipt tests.test_kylin_install_script_simulation -v`

Expected: 所有测试 OK。

- [ ] **Step 5: 提交静默部署能力**

```bash
git add packaging/linux/deployment_receipt.py packaging/linux/deb/taiji-silent-deploy.sh 'taijiagent 打包交付/02_目标终端_安装并验证.sh' tests/test_silent_deployment_receipt.py tests/test_kylin_install_script_simulation.py
git commit -m "feat(linux): add deterministic silent deployment receipts"
```

### Task 8: 事务化升级、数据保护与失败回滚

**Files:**

- Create: `packaging/linux/upgrade-data-contract.json`
- Create: `packaging/linux/upgrade_transaction.py`
- Create: `tests/test_linux_upgrade_transaction.py`
- Modify: `packaging/linux/deb/taiji-silent-deploy.sh`
- Modify: `packaging/linux/deb/prerm`
- Modify: `packaging/linux/deb/postinst`
- Modify: `packaging/linux/deb/postrm`
- Modify: `tests/test_deb_maintainer_lifecycle.py`

- [ ] **Step 1: 写数据保护和恢复 RED 测试**

```text
test_snapshot_covers_config_license_sessions_attachments_workspace_skills_and_templates
test_sqlite_wal_database_uses_backup_api_and_restores_same_logical_rows
test_upgrade_state_transitions_are_fsynced_and_restart_resumable
test_missing_previous_deb_or_irreversible_migration_blocks_before_stop
test_postinst_failure_reinstalls_previous_deb_and_restores_all_hashes
test_failed_rollback_reports_manual_recovery_required
test_symlink_mountpoint_wrong_owner_and_unknown_account_fail_closed
test_same_version_reinstall_is_idempotent_without_user_data_replacement
test_successful_upgrade_then_rollback_then_upgrade_again_preserves_data
test_postinst_never_invokes_apt_or_writes_user_home
```

- [ ] **Step 2: 运行测试确认当前没有事务层**

Run: `python3 -m unittest tests.test_linux_upgrade_transaction tests.test_deb_maintainer_lifecycle -v`

Expected: FAIL，事务模块和数据合同不存在，dpkg `postinst` 失败后没有外层 N-1 恢复闭环。

- [ ] **Step 3: 实现 upgrade journal、快照与回滚**

只对命令行明确传入且经 `getent passwd` 验证的业务账号处理：

```text
~/.config/taiji-agent
~/.local/share/taiji-agent
~/.local/state/taiji-agent
```

不得从 root 的 HOME 猜用户，不跟随 symlink、不跨 mountpoint、不操作未登记账号。状态机固定为：

```text
preflight → trusted_staging → stopped → snapshotted
→ package_changed → migrated → verified → committed

failure: rolling_back → rolled_back | manual_recovery_required
```

journal 位于 `/var/lib/taiji-agent/upgrades/${TRANSACTION_ID}/journal.json`，快照位于 `/var/lib/taiji-agent/backups/${TRANSACTION_ID}/`；目录 `0700`、文件 `0600`，每次转换原子写并 `fsync`。SQLite 使用 `sqlite3.Connection.backup()`；任何状态变更前必须确认 N-1 DEB、SHA、签名和 backward-compatible 数据合同。`prerm` 只按 dpkg action 停止当前太极进程且不删除用户数据；`postinst` 只处理包内权限和 native verify，不递归 apt、不写用户 HOME；`postrm` 的 remove/purge 分支只处理明确的 root-owned 系统状态。无法闭合的恢复必须输出 `manual_recovery_required`。

- [ ] **Step 4: 运行升级生命周期测试**

Run: `python3 -m unittest tests.test_linux_upgrade_transaction tests.test_deb_maintainer_lifecycle -v`

Expected: 所有测试 OK。

- [ ] **Step 5: 提交升级事务**

```bash
git add packaging/linux/upgrade-data-contract.json packaging/linux/upgrade_transaction.py packaging/linux/deb/taiji-silent-deploy.sh packaging/linux/deb/prerm packaging/linux/deb/postinst packaging/linux/deb/postrm tests/test_linux_upgrade_transaction.py tests/test_deb_maintainer_lifecycle.py
git commit -m "feat(linux): add transactional upgrade and rollback"
```

### Task 9: 扩展真实 DEB 断网生命周期演练

**Files:**

- Modify: `tools/taiji-offline-rehearsal/Dockerfile`
- Modify: `tools/taiji-offline-rehearsal/run-lifecycle.sh`
- Modify: `scripts/produce-taiji-offline-rehearsal.py`
- Modify: `tests/test_offline_rehearsal_producer.py`

- [ ] **Step 1: 写完整 lifecycle RED 测试**

```text
test_lifecycle_runs_fresh_reinstall_upgrade_failed_rollback_and_second_upgrade
test_postinst_failure_injection_uses_same_candidate_deb_bytes
test_all_receipts_bind_same_candidate_sha_and_policy
test_data_manifest_matches_before_upgrade_after_upgrade_and_after_rollback
test_network_none_and_no_download_are_enforced_for_every_package_action
test_missing_previous_release_blocks_upgrade_rehearsal
test_power_loss_resume_never_treats_partial_journal_as_committed
```

- [ ] **Step 2: 运行测试确认旧演练缺口**

Run: `python3 -m unittest tests.test_offline_rehearsal_producer -v`

Expected: FAIL，旧演练没有 N-1 数据、升级失败恢复、再次升级和单 DEB no-download 全场景。

- [ ] **Step 3: 实现同一候选的十步断网演练**

固定顺序：fresh N、same-version reinstall N、N-1 写入真实结构 fixture、N-1→N、数据对账、对同一 N 注入确定性 `postinst` 失败、自动恢复 N-1、解除注入再次升级 N、ordinary remove 保留用户数据、purge 只清 root-owned 系统状态。

容器镜像在构建阶段预装 policy 声明的宿主核心能力；candidate 生命周期以 `--network none` 运行。安装命令不得执行 `apt-get update`、`apt-get install -f` 或任何下载。静默路径使用 Task 7 的 `certification` admission mode；challenge、build manifest、policy 和 candidate SHA 一并写入 offline evidence。注入通过 `dpkg-divert` 临时转移 N 新增且 N-1 不依赖的 policy 文件，candidate DEB 字节不得变化。

producer 最终 CLI 固定为：

```text
produce-taiji-offline-rehearsal.py
  --deb PATH
  --previous-deb PATH
  --build-manifest PATH
  --policy PATH
  --output-dir NEW_DIRECTORY
  --image IMAGE_TAG
  --challenge 64_TO_128_LOWERCASE_HEX
```

这里消费 build manifest，而不是尚未生成的最终 v3；offline evidence 由后续 certification set 聚合，避免认证循环。

- [ ] **Step 4: 运行 producer 单测**

Run: `python3 -m unittest tests.test_offline_rehearsal_producer -v`

Expected: 所有测试 OK。真实 Docker 命令只在 Task 16 的兼容 Linux amd64 环境执行。

- [ ] **Step 5: 提交 lifecycle rehearsal**

```bash
git add tools/taiji-offline-rehearsal/Dockerfile tools/taiji-offline-rehearsal/run-lifecycle.sh scripts/produce-taiji-offline-rehearsal.py tests/test_offline_rehearsal_producer.py
git commit -m "test(linux): expand offline lifecycle rehearsal"
```

### Task 10: 脱敏 support bundle

**Files:**

- Create: `packaging/linux/support_bundle.py`
- Create: `packaging/linux/bin/taiji-agent-support`
- Create: `tests/test_linux_support_bundle.py`
- Modify: `hermes-local-lab/scripts/taiji-agent-diagnose`
- Modify: `packaging/linux/bin/taiji-agent-diagnose`
- Modify: `packaging/linux/deb/build-deb.sh`
- Modify: `packaging/linux/payload-contract.json`
- Modify: `packaging/linux/deb/postinst`
- Modify: `taijiagent 打包交付/03_目标终端_导出诊断报告.sh`
- Modify: `tests/test_linux_desktop_packaging_static.py`

- [ ] **Step 1: 写隐私硬门禁 RED 测试**

```text
test_support_bundle_contains_only_allowlisted_files_and_fields
test_bundle_omits_keys_tokens_passwords_user_host_ip_mac_serial_and_paths
test_bundle_never_contains_attachment_text_database_or_browser_session
test_collection_failure_is_best_effort_and_uses_stable_codes
test_bundle_and_sidecar_are_mode_0600_with_basename_checksum
test_symlink_hardlink_fifo_and_oversize_inputs_are_rejected
test_installed_diagnose_no_longer_emits_key_suffix_base_url_or_raw_logs
test_staged_final_and_installed_privacy_scans_share_forbidden_sentinels
```

- [ ] **Step 2: 运行测试确认当前诊断泄露面**

Run: `python3 -m unittest tests.test_linux_support_bundle tests.test_linux_desktop_packaging_static -v`

Expected: FAIL；当前 diagnose 仍输出用户名、绝对路径、base URL、Key suffix、机器码或原始日志。

- [ ] **Step 3: 实现 allowlist-only bundle**

输出固定为：

```text
taiji-agent-support-${UTC_TIMESTAMP}.tar.gz
taiji-agent-support-${UTC_TIMESTAMP}.tar.gz.sha256
```

tar 只允许 `bundle-manifest.json`、`deployment-receipt.json`、安全的 `taiji.product.support-bundle.v1` JSON 和 `collection-errors.txt`。manifest 可含版本、DEB SHA、policy ID/SHA、错误阶段、稳定错误码、OS 兼容字段和依赖状态；不得含 API Key、Token、密码、附件正文、数据库、浏览器会话、用户名、主机名、IP、MAC、序列号、原始 HOME/XDG、完整路径、原始日志尾部或 `pgrep -af`。单项失败只记录固定 collector ID/error code。tar 和 sidecar mode `0600`，sidecar 只写 basename。

- [ ] **Step 4: 运行诊断和隐私测试**

Run: `python3 -m unittest tests.test_linux_support_bundle tests.test_linux_desktop_packaging_static -v`

Expected: 所有测试 OK。

- [ ] **Step 5: 提交 support bundle**

```bash
git add packaging/linux/support_bundle.py packaging/linux/bin/taiji-agent-support hermes-local-lab/scripts/taiji-agent-diagnose packaging/linux/bin/taiji-agent-diagnose packaging/linux/deb/build-deb.sh packaging/linux/payload-contract.json packaging/linux/deb/postinst 'taijiagent 打包交付/03_目标终端_导出诊断报告.sh' tests/test_linux_support_bundle.py tests/test_linux_desktop_packaging_static.py
git commit -m "feat(linux): export privacy-safe support bundles"
```

### Task 11: 代表认证矩阵与单环境记录

**Files:**

- Create: `packaging/linux/certification-matrix.json`
- Create: `tests/test_certification_matrix_contract.py`
- Modify: `taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh`
- Modify: `tools/taiji-desktop-acceptance/observe-single-deb-install.py`
- Modify: `tools/taiji-desktop-acceptance/assemble-target-evidence.py`
- Modify: `tools/taiji-desktop-acceptance/test_observe_single_deb_install.py`
- Modify: `tools/taiji-desktop-acceptance/test_assemble_target_evidence.py`
- Modify: `tools/taiji-desktop-acceptance/run-installed-electron-acceptance.js`
- Modify: `tools/taiji-desktop-acceptance/run-installed-electron-acceptance.test.js`
- Modify: `tests/test_target_desktop_acceptance_producer.py`

- [ ] **Step 1: 写矩阵和 record RED 测试**

```text
test_matrix_has_exact_six_positive_categories_and_required_negative_boundaries
test_each_positive_category_requires_full_business_and_lifecycle_checks
test_each_record_binds_source_deb_policy_and_category
test_records_never_self_claim_certified
test_matrix_rejects_duplicate_category_or_mixed_deb_hash
test_negative_samples_block_before_business_data_mutation
test_full_matrix_is_required_for_runtime_policy_or_lifecycle_changes
test_three_family_core_path_is_minimum_for_application_only_change
```

- [ ] **Step 2: 运行测试确认单 target/profile 模型失败**

```bash
python3 -m unittest tests.test_certification_matrix_contract tests.test_target_desktop_acceptance_producer -v
python3 -m unittest discover -s tools/taiji-desktop-acceptance -p 'test_*.py'
```

Expected: FAIL，旧工具仍绑定一个 target profile，且没有六类矩阵合同。

- [ ] **Step 3: 固化类别与环境证据 schema**

正向 category IDs 固定为：

```text
kylin-min-ukui
kylin-current-standard
kylin-hardened
uos-min-dde
uos-current-or-hardened
openkylin-current
```

负向至少为：

```text
arm-blocked
rpm-only-blocked
glibc-below-min-blocked
missing-core-capability-blocked
no-admin-blocked
no-graphical-desktop-blocked
```

单环境 record 使用 `taiji-linux-environment-evidence/v1`，绑定 `category_id/source_commit/version/architecture/deb_basename/deb_sha256/policy id+sha/实际 OS、桌面、安全 facts/checks/attachments`。正向机器本地只能记录 `compatibility=COMPATIBLE` 与 `checks=PASS`，负向为 `BLOCKED`；任何单机记录不得自称 `CERTIFIED`。双击仍由人工 method attestation 证明，程序只记录 dpkg/来源/业务事实。

- [ ] **Step 4: 运行矩阵、Python 和 Electron 测试**

```bash
python3 -m unittest tests.test_certification_matrix_contract tests.test_target_desktop_acceptance_producer -v
python3 -m unittest discover -s tools/taiji-desktop-acceptance -p 'test_*.py'
node --test tools/taiji-desktop-acceptance/run-installed-electron-acceptance.test.js
```

Expected: 所有测试 OK。

- [ ] **Step 5: 提交矩阵合同**

```bash
git add packaging/linux/certification-matrix.json 'taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh' tools/taiji-desktop-acceptance tests/test_certification_matrix_contract.py tests/test_target_desktop_acceptance_producer.py
git commit -m "test(linux): define representative certification lifecycle matrix"
```

### Task 12: 认证集生成与严格验证

**Files:**

- Create: `scripts/assemble-taiji-certification-set.py`
- Create: `tests/test_certification_set_v1.py`
- Modify: `scripts/validate-taiji-release-evidence.py`

- [ ] **Step 1: 写认证集完整性 RED 测试**

测试覆盖：缺少/重复类别、混用第二个 DEB、policy/版本/架构/commit 漂移、路径逃逸、symlink/hardlink、未知字段、附件篡改、正向非 PASS、缺负向边界、非 canonical 输出和覆盖已存在目录。

- [ ] **Step 2: 运行测试确认 RED**

Run: `python3 -m unittest tests.test_certification_set_v1 -v`

Expected: FAIL，assembler 尚不存在。

- [ ] **Step 3: 实现 canonical certification set**

内部结构固定为：

```text
taijiagent 打包交付/certification/
  records/${CATEGORY_ID}/
  certification-set.json
  certification-set.json.sig
```

`certification-set.json` exact fields：

```text
schema, generated_at_utc, challenge_nonce,
source_commit, version, architecture,
deb_basename, deb_sha256,
compatibility_policy_id, compatibility_policy_sha256,
certification_profile, offline_rehearsal,
environments, negative_boundaries
```

schema 为 `taiji-linux-certification-set/v1`。assembler 必须读取 Task 11 固定六类正向和六类负向记录，验证全部绑定同一 DEB/policy/source/version/amd64，并把正向聚合结果提升为 `CERTIFIED`；认证集绝不写回 DEB。

CLI 固定为：

```text
assemble-taiji-certification-set.py
  --matrix PATH
  --records-dir DIRECTORY
  --offline-evidence PATH
  --deb PATH
  --policy PATH
  --output NEW_PATH
  --challenge 64_TO_128_LOWERCASE_HEX
```

- [ ] **Step 4: 运行认证集测试**

Run: `python3 -m unittest tests.test_certification_set_v1 -v`

Expected: 所有测试 OK。

- [ ] **Step 5: 提交认证集聚合器**

```bash
git add scripts/assemble-taiji-certification-set.py scripts/validate-taiji-release-evidence.py tests/test_certification_set_v1.py
git commit -m "feat(packaging): assemble immutable certification sets"
```

### Task 13: 最终 v3 回执、签名与 release-check

**Files:**

- Create: `scripts/assemble-taiji-release-evidence.py`
- Create: `tests/test_release_evidence_assembler_v3.py`
- Create: `tests/test_release_check_v3.py`
- Modify: `scripts/sign-taiji-release-evidence.sh`
- Modify: `scripts/taiji-release-check.sh`
- Modify: `tests/test_release_evidence_signer_guards.py`
- Modify: `tests/test_linux_desktop_packaging_static.py`

- [ ] **Step 1: 写签名、防循环和 release gate RED 测试**

```text
test_unsigned_certification_set_cannot_generate_v3
test_certification_or_signature_change_invalidates_v3
test_v2_cannot_be_resigned_as_current_release
test_existing_signature_challenge_reuse_and_wide_private_key_fail
test_assemblers_never_change_candidate_deb_sha
test_release_check_rejects_v2_only_or_incomplete_matrix
test_candidate_byte_change_invalidates_old_certification
test_policy_manifest_set_and_v3_must_match
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_release_evidence_assembler_v3 tests.test_release_check_v3 tests.test_release_evidence_signer_guards -v
```

Expected: FAIL，新 assembler/v3 release-check 尚不存在。

- [ ] **Step 3: 实现 signed certification + publication evidence**

最终 `release-evidence.json` exact fields：

```text
schema="taiji-release-evidence/v3"
evidence_type="single-deb-publication"
generated_at_utc, challenge_nonce, source_commit, version,
architecture="amd64", deb_basename, deb_sha256,
compatibility_policy_id, compatibility_policy_sha256,
certification_set_basename, certification_set_sha256,
certification_set_signature_basename, certification_set_signature_sha256,
maintainer, customer_filename,
customer_folder_contract="exactly-one-deb",
signing_public_key_fingerprint, formal_gates
```

签名文件为 `release-evidence.json.sig`；回执不记录自身签名摘要。signer 只接受 `taiji-linux-certification-set/v1` 和 `taiji-release-evidence/v3`，分别使用独立 challenge，保留私钥 mode、hardlink/symlink、公私钥 fingerprint 和 challenge 一次性门禁。

release-check 顺序固定为：当前 main/build binding → DEB/policy 原始摘要 → 环境记录 → signed certification set → signed v3 → 三处 DEB/policy 摘要一致。只有 v2 双签名仍必须阻断当前放行。

新增/修改后的 CLI 固定为：

```text
assemble-taiji-release-evidence.py
  --manifest PATH --deb PATH --policy PATH
  --certification-set PATH --certification-signature PATH
  --output NEW_PATH --challenge 64_TO_128_LOWERCASE_HEX

sign-taiji-release-evidence.sh EVIDENCE_JSON PRIVATE_KEY_PEM

taiji-release-check.sh
  --delivery-dir DIRECTORY
  --certification-set PATH --certification-signature PATH
  --release-evidence PATH --release-signature PATH
```

签认证集时环境变量为 `TAIJI_CERTIFICATION_CHALLENGE`；签最终 v3 时为 `TAIJI_PUBLICATION_CHALLENGE`。signer 根据 schema 只读取对应变量，不复用 challenge。

- [ ] **Step 4: 运行 v3 与 shell 测试**

```bash
python3 -m unittest tests.test_release_evidence_assembler_v3 tests.test_release_check_v3 tests.test_release_evidence_signer_guards tests.test_linux_desktop_packaging_static -v
bash -n scripts/sign-taiji-release-evidence.sh scripts/taiji-release-check.sh
```

Expected: 所有测试 OK，shell 检查返回 0。

- [ ] **Step 5: 提交签名发布链**

```bash
git add scripts/assemble-taiji-release-evidence.py scripts/sign-taiji-release-evidence.sh scripts/taiji-release-check.sh tests/test_release_evidence_assembler_v3.py tests/test_release_check_v3.py tests/test_release_evidence_signer_guards.py tests/test_linux_desktop_packaging_static.py
git commit -m "feat(packaging): sign certification and v3 release evidence"
```

### Task 14: 单一 DEB 原子 publisher

**Files:**

- Modify: `packaging/linux/deb/publish-single-deb.sh`
- Modify: `tests/test_single_deb_publisher_gate.py`
- Modify: `tests/test_single_deb_sales_contract.py`

- [ ] **Step 1: 写固定文件名、并发和回滚 RED 测试**

```text
test_success_directory_contains_only_fixed_basename_deb
test_customer_deb_is_bit_identical_to_internal_candidate
test_certification_is_never_written_back_into_deb
test_v2_unsigned_or_mismatched_inputs_cannot_publish
test_input_replacement_during_gate_fails_closed
test_concurrent_output_or_receipt_is_never_overwritten
test_receipt_failure_rolls_back_only_this_publication_identity
test_output_and_receipt_contain_no_profile_or_target_baseline
```

- [ ] **Step 2: 运行 publisher 测试确认旧 profile 文件名失败**

Run: `python3 -m unittest tests.test_single_deb_publisher_gate tests.test_single_deb_sales_contract -v`

Expected: FAIL，旧 publisher 仍使用 baseline/profile 输入和文件名。

- [ ] **Step 3: 迁移 publisher 输入与内部 receipt**

客户文件名严格为 `taiji-agent_${VERSION}_amd64.deb`。publisher 输入改为 candidate DEB、canonical policy、signed certification set 和 signed v3；只归档已有签名回执，不生成新的未签发布结论。内部 receipt ID 为 `${VERSION}-amd64-${DEB_SHA256:0:12}`，内部档案 allowlist：

```text
release-evidence.json
release-evidence.json.sig
certification-set.json
certification-set.json.sig
compatibility-policy.json
deb.sha256
```

客户目录严格只有一个 DEB。保留现有 no-replace rename、输入 identity snapshot 和 identity-bound 失败回滚；不得把两个目录描述为跨文件系统原子事务。

publisher 最终 CLI 固定为：

```text
publish-single-deb.sh
  --delivery-dir DIRECTORY
  --candidate-deb PATH
  --policy PATH
  --certification-set PATH
  --certification-signature PATH
  --release-evidence PATH
  --release-signature PATH
  --output-dir NEW_DIRECTORY
  --receipt-root DIRECTORY
```

- [ ] **Step 4: 运行 publisher 与 shell 测试**

```bash
python3 -m unittest tests.test_single_deb_publisher_gate tests.test_single_deb_sales_contract -v
bash -n packaging/linux/deb/publish-single-deb.sh
```

Expected: 所有测试 OK，shell 检查返回 0。

- [ ] **Step 5: 提交统一 publisher**

```bash
git add packaging/linux/deb/publish-single-deb.sh tests/test_single_deb_publisher_gate.py tests/test_single_deb_sales_contract.py
git commit -m "feat(packaging): publish one immutable unified deb"
```

### Task 15: 删除 superseded baseline 工具并更新文档/CI

**Files:**

- Delete: `packaging/linux/capture-target-baseline.sh`
- Delete: `packaging/linux/target_baseline.py`
- Delete: `packaging/linux/deb/runtime-depends.txt`
- Delete: `tests/test_target_baseline_contract.py`
- Modify: `taijiagent 打包交付/99_本机_准备制包输入包.sh`
- Modify: `docs/runbooks/taiji-kylin-uos-offline-delivery.md`
- Modify: `docs/taiji-desktop-uos-packaging.md`
- Modify: `docs/taiji-sale-readiness.md`
- Modify: `taijiagent 打包交付/操作说明.md`
- Modify: `taijiagent 打包交付/版本信息.txt`
- Modify: `.github/workflows/ci.yml`
- Modify: `scripts/classify-ci-scope.py`
- Modify: `tests/test_ci_scope_classifier.py`
- Modify: `tests/test_single_deb_sales_contract.py`
- Modify: `tests/test_linux_desktop_packaging_static.py`

- [ ] **Step 1: 写 legacy 清零、文档和 CI RED 测试**

断言当前构建/安装/发布链不再引用 `target_baseline`、profile、人工 Maintainer 或 runtime-depends；v2 只读 fixture 是唯一允许例外。CI classifier 将 policy、ELF、preinst、lifecycle、证据、publisher 和部署工具变化全部分类为 high risk，并要求 `full-ci`。

- [ ] **Step 2: 运行测试和 stale reference 扫描确认 RED**

```bash
python3 -m unittest tests.test_ci_scope_classifier tests.test_single_deb_sales_contract tests.test_linux_desktop_packaging_static -v
rg -n 'target_baseline|target-baseline|profile_id|TAIJI_PACKAGE_MAINTAINER|approved-maintainer|runtime-depends' packaging scripts tests 'taijiagent 打包交付'
```

Expected: 测试或扫描显示旧工具/当前链引用尚存。

- [ ] **Step 3: 删除旧工具并同步全部用户口径**

文档必须明确：

```text
支持：x86_64/amd64、图形桌面、dpkg/apt、Kylin/UOS/openKylin
不支持：ARM、RPM-only、无桌面、Windows
客户输入：只有 taiji-agent_${VERSION}_amd64.deb
内部材料：N-1 DEB、管理脚本、证据、签名、receipt 不属于客户安装输入
发布矩阵：六个正向类别 + 六个负向边界
灰度：Canary 至少 5 台且每类别至少 1 台 → 5% → 25% → 100%
观察：5% 和 25% 各观察一个业务日；成功率至少 99.5%
停止：数据损坏、安全问题、无法回滚、P0/P1、任一类别失败
```

删除“每台采集 baseline”“30 天”“客户 profile 文件名”“完整离线 apt 仓库作为客户输入”“Docker 等于真机”和“补丁版本变化就重新打包”等旧口径。99 源码准备脚本不再复制 baseline/maintainer 文件。

- [ ] **Step 4: 运行 legacy、文档和分类器测试**

```bash
python3 -m unittest tests.test_ci_scope_classifier tests.test_single_deb_sales_contract tests.test_linux_desktop_packaging_static -v
rg -n 'target_baseline|target-baseline|profile_id|TAIJI_PACKAGE_MAINTAINER|approved-maintainer|runtime-depends' packaging scripts tests 'taijiagent 打包交付'
```

Expected: 测试 OK；扫描只允许 v2 `--legacy-v2-read-only` 的历史 fixture/拒绝断言和“旧方案已取代”文档语境，不得存在当前执行路径引用。

- [ ] **Step 5: 提交遗留清理和文档**

```bash
git add packaging/linux/capture-target-baseline.sh packaging/linux/target_baseline.py packaging/linux/deb/runtime-depends.txt tests/test_target_baseline_contract.py 'taijiagent 打包交付/99_本机_准备制包输入包.sh' docs/runbooks/taiji-kylin-uos-offline-delivery.md docs/taiji-desktop-uos-packaging.md docs/taiji-sale-readiness.md 'taijiagent 打包交付/操作说明.md' 'taijiagent 打包交付/版本信息.txt' .github/workflows/ci.yml scripts/classify-ci-scope.py tests/test_ci_scope_classifier.py tests/test_single_deb_sales_contract.py tests/test_linux_desktop_packaging_static.py
git commit -m "docs(linux): finalize unified deb delivery contract"
```

### Task 16: 本地全量验证、独立审查和稳定分支提交

**Files:**

- Verify: all changed files
- Update only if a current test exposes a defect in this plan's scope

- [ ] **Step 1: 运行聚焦合同**

```bash
python3 -m unittest -v \
  tests.test_linux_compatibility_policy \
  tests.test_compatibility_policy_preinst \
  tests.test_linux_elf_abi_closure \
  tests.test_unified_deb_build_contract \
  tests.test_release_evidence_schema_v3 \
  tests.test_silent_deployment_receipt \
  tests.test_linux_upgrade_transaction \
  tests.test_offline_rehearsal_producer \
  tests.test_linux_support_bundle \
  tests.test_certification_matrix_contract \
  tests.test_certification_set_v1 \
  tests.test_release_evidence_assembler_v3 \
  tests.test_release_check_v3 \
  tests.test_single_deb_publisher_gate \
  tests.test_single_deb_sales_contract
```

Expected: 全部 OK；任一失败都先按根因修复并重跑同组，不跳过。

- [ ] **Step 2: 运行仓库全量与跨语言回归**

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tools/taiji-desktop-acceptance -p 'test_*.py'
node --test tools/taiji-desktop-acceptance/run-installed-electron-acceptance.test.js
(cd apps/taiji-desktop && npm run check && node --test tests/*.test.js)
git diff --check
```

Expected: 全部返回 0。

- [ ] **Step 3: 运行 shell、隐私与残留扫描**

```bash
bash -n packaging/linux/deb/build-deb.sh packaging/linux/deb/preinst packaging/linux/deb/postinst packaging/linux/deb/postrm packaging/linux/deb/taiji-silent-deploy.sh packaging/linux/deb/publish-single-deb.sh scripts/sign-taiji-release-evidence.sh scripts/taiji-release-check.sh 'taijiagent 打包交付/00_制包机_生成离线交付包.sh' 'taijiagent 打包交付/01_制包机_发布预检.sh' 'taijiagent 打包交付/02_目标终端_安装并验证.sh' 'taijiagent 打包交付/03_目标终端_导出诊断报告.sh' 'taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh'
rg -n -i 'support@example|example.invalid|TAIJI_PACKAGE_MAINTAINER|TAIJI_TARGET_BASELINE|target_baseline_profile_id' packaging scripts 'taijiagent 打包交付'
git status --short
```

Expected: shell 返回 0；扫描无当前路径命中；状态只含本计划实现文件且随后提交为干净。

- [ ] **Step 4: 发起两阶段独立审查**

使用 `requesting-code-review`：第一阶段逐条对照已批准规格和本计划；第二阶段审查实现质量、安全读取、原子性、升级数据边界、隐私和测试。所有 P0/P1/P2 必须修复并重跑受影响测试；P3 记录但不得掩盖真实风险。

- [ ] **Step 5: 提交审查修复并记录状态卡**

```bash
git diff --name-only
git diff --check
git status --short
git log -1 --oneline
```

若审查产生修复，回到对应 Task，用该 Task 的精确文件列表暂存并执行 `git commit -m "fix(packaging): close unified deb review findings"`，然后重跑 Step 1–3；不得用宽泛暂存命令。如果审查无修复，不创建空提交。Expected: worktree 干净；状态只能称“分支已实现”，不能称“制品已生成”或“目标机已验证”。

### Task 17: 按已授权标准收尾进入正式 main

**Files:**

- Git/GitHub state only
- No package build, target install, deployment or publication in this task

- [ ] **Step 1: 复核提交范围和来源**

```bash
git status --short
git log --oneline 43ebed100b78ad294e0fc67d90c87474cdc2335d..HEAD
git diff --stat 43ebed100b78ad294e0fc67d90c87474cdc2335d...HEAD
```

Expected: 干净，差异只属于 unified Linux DEB 成果。

- [ ] **Step 2: push 功能分支并创建 Ready PR**

```bash
git push -u origin codex/linux-sales-grade-installer
PR_URL="$(gh pr create \
  --base main \
  --head codex/linux-sales-grade-installer \
  --title "feat(linux): deliver unified offline amd64 DEB pipeline" \
  --body $'## 结果\n实现统一国产 Linux amd64 单 DEB 源码与发布链。\n\n## 已验证\nTask 16 聚焦 unittest、tests 全量 unittest、目标验收工具 unittest、Electron node tests、desktop check、shell 语法、diff 和残留扫描均返回 0。\n\n## 未验证\n尚未从正式 main 生成 Linux DEB，尚未执行 Kylin/UOS/openKylin 真机矩阵，未发布。\n\n## 数据与回滚\n用户数据不由 maintainer scripts 修改；升级由显式事务 journal、N-1 DEB 和受控快照回滚。')"
PR_NUMBER="$(gh pr view "$PR_URL" --json number --jq .number)"
gh pr edit "$PR_NUMBER" --add-label full-ci
```

PR 说明必须包含当前本地验证、未运行的真实 Linux/目标机项、数据保护、回滚入口和“未制包/未发布”边界。

- [ ] **Step 3: 等待并处理唯一 CI Gate**

```bash
PR_NUMBER="$(gh pr view codex/linux-sales-grade-installer --json number --jq .number)"
gh pr checks "$PR_NUMBER" --watch
```

Expected: `CI Gate` success。只修本成果导致的失败；相同基础设施故障按 lifecycle 限制重跑一次，断言失败必须修复后重新 push。

- [ ] **Step 4: 合并并同步正式 main**

```bash
PR_NUMBER="$(gh pr view codex/linux-sales-grade-installer --json number --jq .number)"
gh pr merge "$PR_NUMBER" --squash --delete-branch
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git switch main
git pull --ff-only
git status --short
git rev-parse HEAD
```

正式根目录现有用户自有 `AGENTS.md` 变更必须保留，不得覆盖或夹入功能提交。若 squash，按 `development-lifecycle.md` 做 exact path/tree/blob/mode proof；不能用 branch-tip ancestor 作为唯一证明。

- [ ] **Step 5: 正式 main 非破坏性复验与安全清理**

从正式 main 重跑 Task 16 的源码合同和来源检查；完成 worktree/branch/进程/未跟踪内容/分支独有提交审计，建立需要的 `refs/backup/` 后只清理已证明安全的任务对象。此时最高状态是“已合并 main”；标准收尾不授权制包、安装、部署或发布。

### Task 18: 单独授权后的 Linux 制包、断网演练和六类真机认证

**Files/Artifacts:**

- Build from: `/Users/bwb/Documents/工作/taiji-agentv1.0` formal `main`
- Produce internally: `taiji-agent_${VERSION}_amd64.deb`, manifest, policy, ABI report, checksums
- Produce customer output: directory containing exactly `taiji-agent_${VERSION}_amd64.deb`
- Produce evidence: offline rehearsal, six positive records, six negative records, signed certification set, signed v3, internal publication receipt

本任务进入前必须由用户单独确认五类对象：精确 main commit/version、兼容 Linux amd64 制包机和六类目标环境、当前/N-1 DEB 摘要、安装/升级/卸载的数据影响、备份与回滚边界。缺一项就暂停在“已合并 main”。

- [ ] **Step 1: 在最低兼容 Linux amd64 制包环境构建一次**

```bash
: "${TAIJI_FORMAL_MAIN:?必须设置 Linux 制包机上的正式 main 绝对路径}"
cd "$TAIJI_FORMAL_MAIN"
VERSION="$(tr -d '[:space:]' < VERSION)"
BUILD_OUTPUT="$TAIJI_FORMAL_MAIN/taijiagent 打包交付/生成的安装包"
CANDIDATE_DEB="$BUILD_OUTPUT/taiji-agent_${VERSION}_amd64.deb"
git rev-parse HEAD
git status --short
bash 'taijiagent 打包交付/00_制包机_生成离线交付包.sh'
bash 'taijiagent 打包交付/01_制包机_发布预检.sh'
test -f "$CANDIDATE_DEB"
sha256sum "$CANDIDATE_DEB"
```

Expected: 源码干净；只得到一个候选 DEB；真实 payload ELF audit 满足 policy；状态升级为“制品已生成”，不称目标机通过。

- [ ] **Step 2: 对同一候选执行真实 amd64 断网生命周期演练**

```bash
: "${TAIJI_FORMAL_MAIN:?必须设置正式 main 绝对路径}"
: "${TAIJI_PREVIOUS_DEB:?必须设置已签名 N-1 DEB 绝对路径}"
: "${TAIJI_OFFLINE_OUTPUT:?必须设置尚不存在的离线证据目录}"
cd "$TAIJI_FORMAL_MAIN"
VERSION="$(tr -d '[:space:]' < VERSION)"
CANDIDATE_DEB="$TAIJI_FORMAL_MAIN/taijiagent 打包交付/生成的安装包/taiji-agent_${VERSION}_amd64.deb"
BUILD_MANIFEST="$TAIJI_FORMAL_MAIN/taijiagent 打包交付/生成的安装包/taiji-package-manifest.json"
POLICY="$TAIJI_FORMAL_MAIN/packaging/linux/compatibility-policy.json"
OFFLINE_CHALLENGE="$(openssl rand -hex 32)"
docker build --platform linux/amd64 -t "taiji-offline-rehearsal:${VERSION}" tools/taiji-offline-rehearsal
python3 scripts/produce-taiji-offline-rehearsal.py \
  --deb "$CANDIDATE_DEB" \
  --previous-deb "$TAIJI_PREVIOUS_DEB" \
  --build-manifest "$BUILD_MANIFEST" \
  --policy "$POLICY" \
  --output-dir "$TAIJI_OFFLINE_OUTPUT" \
  --image "taiji-offline-rehearsal:${VERSION}" \
  --challenge "$OFFLINE_CHALLENGE"
```

Expected: fresh/reinstall/upgrade/failure rollback/second upgrade/remove 全部通过且 no-download；状态可称“离线安装已演练”。Docker 不证明国产真机、CPU、kysec、桌面、模型或 Office。

- [ ] **Step 3: 六类正向和六类负向环境使用同一 SHA**

每个正向类别在干净快照分别完成：断网双击 + 人工见证、独立快照静默安装、首次配置/授权/真实模型、对话、附件、专家团队、DOCX、WPS/Word 人工打开、support bundle、关窗进程退出、同版本重装、N-1→N、故障注入/rollback/再次 upgrade、remove/purge 数据边界，以及该类别的 kysec/白名单/X11/Wayland 风险。

每台执行前后均运行：

```bash
: "${TAIJI_CANDIDATE_DEB:?必须设置同一候选 DEB 绝对路径}"
: "${TAIJI_SUPPORT_OUTPUT:?必须设置受控诊断输出目录}"
sha256sum "$TAIJI_CANDIDATE_DEB"
dpkg --print-architecture
ldd --version | head -n 1
sudo dpkg -i "$TAIJI_CANDIDATE_DEB"
dpkg-query -W -f='${Status} ${Version}\n' taiji-agent
/opt/taiji-agent/bin/taiji-native-verify
taiji-agent-support --output-dir "$TAIJI_SUPPORT_OUTPUT"
```

负向环境必须在业务数据变化前以稳定码 BLOCKED。任一环境 SHA 不同或候选被修复，全部适用认证重新开始。

- [ ] **Step 4: 聚合、签名并发布客户单文件目录**

```bash
: "${TAIJI_FORMAL_MAIN:?必须设置正式 main 绝对路径}"
: "${TAIJI_CERT_RECORDS:?必须设置完整矩阵 records 目录}"
: "${TAIJI_OFFLINE_EVIDENCE:?必须设置离线演练 evidence 文件}"
: "${TAIJI_RELEASE_PRIVATE_KEY:?必须设置 mode 0400 或 0600 的发布私钥}"
: "${TAIJI_CUSTOMER_OUTPUT:?必须设置尚不存在的客户输出目录}"
: "${TAIJI_RECEIPT_ROOT:?必须设置内部发布回执目录}"
cd "$TAIJI_FORMAL_MAIN"
VERSION="$(tr -d '[:space:]' < VERSION)"
DELIVERY_DIR="$TAIJI_FORMAL_MAIN/taijiagent 打包交付"
BUILD_OUTPUT="$DELIVERY_DIR/生成的安装包"
CANDIDATE_DEB="$BUILD_OUTPUT/taiji-agent_${VERSION}_amd64.deb"
POLICY="$TAIJI_FORMAL_MAIN/packaging/linux/compatibility-policy.json"
CERT_SET="$DELIVERY_DIR/certification/certification-set.json"
RELEASE_EVIDENCE="$DELIVERY_DIR/release-evidence.json"
CERT_CHALLENGE="$(openssl rand -hex 32)"
PUBLICATION_CHALLENGE="$(openssl rand -hex 32)"
python3 scripts/assemble-taiji-certification-set.py \
  --matrix packaging/linux/certification-matrix.json \
  --records-dir "$TAIJI_CERT_RECORDS" \
  --offline-evidence "$TAIJI_OFFLINE_EVIDENCE" \
  --deb "$CANDIDATE_DEB" \
  --policy "$POLICY" \
  --output "$CERT_SET" \
  --challenge "$CERT_CHALLENGE"
TAIJI_CERTIFICATION_CHALLENGE="$CERT_CHALLENGE" \
  bash scripts/sign-taiji-release-evidence.sh "$CERT_SET" "$TAIJI_RELEASE_PRIVATE_KEY"
python3 scripts/assemble-taiji-release-evidence.py \
  --manifest "$BUILD_OUTPUT/taiji-package-manifest.json" \
  --deb "$CANDIDATE_DEB" \
  --policy "$POLICY" \
  --certification-set "$CERT_SET" \
  --certification-signature "${CERT_SET}.sig" \
  --output "$RELEASE_EVIDENCE" \
  --challenge "$PUBLICATION_CHALLENGE"
TAIJI_PUBLICATION_CHALLENGE="$PUBLICATION_CHALLENGE" \
  bash scripts/sign-taiji-release-evidence.sh "$RELEASE_EVIDENCE" "$TAIJI_RELEASE_PRIVATE_KEY"
bash scripts/taiji-release-check.sh \
  --delivery-dir "$DELIVERY_DIR" \
  --certification-set "$CERT_SET" \
  --certification-signature "${CERT_SET}.sig" \
  --release-evidence "$RELEASE_EVIDENCE" \
  --release-signature "${RELEASE_EVIDENCE}.sig"
bash packaging/linux/deb/publish-single-deb.sh \
  --delivery-dir "$DELIVERY_DIR" \
  --candidate-deb "$CANDIDATE_DEB" \
  --policy "$POLICY" \
  --certification-set "$CERT_SET" \
  --certification-signature "${CERT_SET}.sig" \
  --release-evidence "$RELEASE_EVIDENCE" \
  --release-signature "${RELEASE_EVIDENCE}.sig" \
  --output-dir "$TAIJI_CUSTOMER_OUTPUT" \
  --receipt-root "$TAIJI_RECEIPT_ROOT"
find "$TAIJI_CUSTOMER_OUTPUT" -maxdepth 1 -type f -print
sha256sum "$TAIJI_CUSTOMER_OUTPUT/taiji-agent_${VERSION}_amd64.deb"
```

Expected: release-check 全绿；客户目录 `find` 仅一行；客户 DEB SHA 与所有证据逐字一致。

- [ ] **Step 5: 销售放行判定与 Windows 触发条件**

只有六个正向类别、负向边界、升级回滚、隐私、签名和客户目录合同全部闭合，才可写“统一国产 Linux 安装包可交付”。随后由用户拿该单 DEB 在指定 Kylin/UOS 设备验收并确认；只有该确认完成，才开始 Windows 安装包的独立 brainstorming、规格和实施计划。

## 实施中不可降级的风险门禁

1. 图形安装器可能在 `preinst` 前解析 `Depends` 并尝试软件源；必须用最小 Depends、空源/network trap、真实断网双击共同证明，preinst 单测不能代替。
2. `ldd` 可能执行 loader；闭包权威数据来自 `readelf`，`ldd` 仅对受信 payload 做 smoke。
3. GTK/NSS/Mesa 盲目私有化会破坏桌面模块、驱动或安全策略；只复制 policy allowlist，Mesa/DBus/systemd/PAM/glibc/loader 保持宿主边界。
4. macOS 单测和 Docker amd64 仿真不证明国产 x86 CPU、kysec、桌面、真实模型或 WPS/Word。
5. `postinst` 失败不会自动恢复旧二进制；没有外层 journal、N-1 归档、签名和数据兼容合同就不得宣称回滚。
6. 历史包升级会先执行旧 `prerm`，新包不能消除其副作用；真实矩阵必须覆盖首次从既有 1.0 包升级。
7. 多用户终端不得猜默认账号；升级只处理显式提供且验证过的业务账号。
8. policy、Electron、Node、Python、native wheel、私有库或生命周期变化会使完整矩阵重跑；DEB SHA 变化使旧认证集立即失效。
9. customer directory 只有一个 DEB；N-1、管理脚本、证据和 receipt 是内部发布/运维材料，不得混入客户安装输入。
10. Windows 在 Linux 真机验收前保持冻结，不在本分支夹带 Windows 文件或行为。

## 完成判定

- 源码阶段：Task 1–17 完成后，只能报告“已合并 main”，并列出未执行的 Linux 制包/目标机门禁。
- 制品阶段：Task 18 Step 1 通过后，可报告“制品已生成”。
- 演练阶段：Task 18 Step 2 通过后，可报告“离线安装已演练”。
- 目标机阶段：六类正向和负向边界全部绑定同一 SHA 后，可报告“目标机已验证”。
- 发布阶段：signed certification set、signed v3、release-check、单文件客户目录和用户授权渠道全部闭合后，才可报告“已发布”。
