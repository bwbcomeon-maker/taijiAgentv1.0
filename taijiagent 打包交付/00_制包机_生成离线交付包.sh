#!/bin/bash -p
set -Eeuo pipefail
umask 022
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONBREAKPOINT PYTHONUSERBASE
unset LD_PRELOAD LD_LIBRARY_PATH DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
unset OPENSSL_CONF OPENSSL_MODULES
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SRC_ARCHIVE="${TAIJI_SOURCE_ARCHIVE:-}"
CHECKSUM_FILE="$SCRIPT_DIR/SHA256SUMS.txt"
SOURCE_INTEGRITY_HELPER="$SCRIPT_DIR/source-archive-integrity.py"
SOURCE_INTEGRITY_HELPER_SHA256="eaebadbe2f86d76d09f19ed210ad407e5926a242c46f53fb89e26253db8d8d7a"
BUILDER_INPUT_HELPER="$SCRIPT_DIR/builder-input-package.py"
BUILDER_INPUT_HELPER_SHA256="ddcf38f330f584770e826baaedccb27d44823d6dd7f7811add15646510562f30"
FROZEN_SOURCE_COMMIT="${TAIJI_FROZEN_SOURCE_COMMIT:-}"
FROZEN_SOURCE_HELPER_TEMP=""
FROZEN_CONTROL_TEMP_DIR=""
FROZEN_CONTROL_TEMP_PARENT=""
SOURCE_INVENTORY=""
SOURCE_INVENTORY_SHA256=""
SOURCE_ARCHIVE_SHA256=""
SOURCE_ARCHIVE_FD=""
SOURCE_INVENTORY_FD=""
SOURCE_ARCHIVE_BASENAME=""
SOURCE_INVENTORY_BASENAME=""
BUILD_ROOT="${TAIJI_BUILD_ROOT:-}"
BUILD_ROOT_OWNER_MARKER=".taiji-build-root-owner"
BUILD_ROOT_OWNER_TOKEN="taiji-agent-build-root-v1:$(id -u 2>/dev/null || printf user)"
BUILD_MIN_FREE_MIB="12288"
BUILD_MIN_FREE_INODES="100000"
BUILD_TMP_DIR=""
SRC_DIR=""
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
STATE_HOME="${XDG_STATE_HOME:-${HOME:-}/.local/state}"
LOG_DIR="$STATE_HOME/taiji-agent/build-logs"
DELIVERY_BUILD_LOG_DIR="$SCRIPT_DIR/构建日志"
VERSION=""
TOOL_ROOT=""
NODE_ROOT=""
UV_ROOT=""
UV_BIN=""
UV_ARCHIVE_PATH=""
UV_VERSION="0.12.2"
UV_ARCHIVE="uv-x86_64-unknown-linux-gnu.tar.gz"
UV_ARCHIVE_URL="https://github.com/astral-sh/uv/releases/download/0.12.2/uv-x86_64-unknown-linux-gnu.tar.gz"
UV_ARCHIVE_SHA256="d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4"
UV_PINNED_EXECUTABLE_SHA256="72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2"
UV_EXECUTABLE_SHA256=""
PYTHON_VERSION_PINNED="3.11.15"
PYTHON_BUILD_ID="20260805"
PYTHON_ARCHIVE="cpython-3.11.15+20260805-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PYTHON_ARCHIVE_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260805/cpython-3.11.15%2B20260805-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PYTHON_ARCHIVE_SHA256="2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a"
PYTHON_PINNED_EXECUTABLE_SHA256="5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba"
PYTHON_ROOT=""
PYTHON_BIN=""
PYTHON_ARCHIVE_PATH=""
NODE_VERSION="22.23.1"
NODE_ARCHIVE="node-v${NODE_VERSION}-linux-x64.tar.xz"
NODE_ARCHIVE_SHA256="9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"
NODE_PINNED_EXECUTABLE_SHA256="93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068"
NODE_NPM_CLI_ARCHIVE_SHA256=""
NODE_NPM_VERSION_ARCHIVE=""
NODE_ARCHIVE_PATH=""
FIXED_TOOL_ARCHIVE_FD_PATH=""
SOURCE_ARCHIVE_SNAPSHOT_PATH=""
BUILD_NODE_PATH=""
BUILD_NODE_HELD_PATH=""
BUILD_NPM_CLI_PATH=""
BUILD_NPM_CLI_HELD_PATH=""
BUILD_NODE_RUNTIME_SEALED=0
BUILD_NPM_USERCONFIG=""
BUILD_NPM_GLOBALCONFIG=""
SEALED_SNAPSHOT_HOLDER_PID=""
SEALED_SNAPSHOT_TRANSPORT_DIR=""
SEALED_SNAPSHOT_CONTROL_OPEN=0
SEALED_SNAPSHOT_STATUS_OPEN=0
BUILD_MARKER="$OUTPUT_DIR/.build-success"
PENDING_BUILD_MARKER=""
PENDING_BUILD_MARKER_FD=""
PENDING_BUILD_MARKER_IDENTITY=""
PENDING_BUILD_MARKER_SHA256=""
PENDING_BUILD_MARKER_POISON=""
PUBLISHED_BUILD_MARKER_IDENTITY=""
PUBLISHED_BUILD_MARKER_SHA256=""
PUBLISHED_BUILD_MARKER_POISON="$OUTPUT_DIR/.build-success.poisoned.$$"
BUILD_REPORT="$OUTPUT_DIR/构建报告.txt"
BUILD_REPORT_FD=""
BUILD_REPORT_IDENTITY=""
BUILD_REPORT_SHA256=""
MANIFEST_FILE="$OUTPUT_DIR/taiji-package-manifest.json"
SOURCE_PACKAGE_MANIFEST_FD=""
SOURCE_PACKAGE_MANIFEST_PATH=""
SOURCE_PACKAGE_MANIFEST_IDENTITY=""
SOURCE_PACKAGE_MANIFEST_SHA256=""
FORMAL_PACKAGE_MANIFEST_FD=""
FORMAL_PACKAGE_MANIFEST_READ_FD=""
FORMAL_PACKAGE_MANIFEST_IDENTITY=""
FORMAL_PACKAGE_MANIFEST_SHA256=""
FORMAL_PACKAGE_MANIFEST_COMPLETE=0
FORMAL_PACKAGE_MANIFEST_POISON="$OUTPUT_DIR/.formal-package-manifest.poisoned.$$"
FORMAL_BUILD_TEST_LOG="$OUTPUT_DIR/formal-build-tests.log"
FORMAL_BUILD_TEST_LOG_FD=""
FORMAL_BUILD_TEST_LOG_READ_FD=""
FORMAL_BUILD_TEST_LOG_IDENTITY=""
FORMAL_BUILD_TEST_LOG_POISON="$OUTPUT_DIR/.formal-build-tests.poisoned.$$"
FORMAL_BUILD_TESTS_STATUS=""
FORMAL_BUILD_TESTS_LOG_BASENAME="formal-build-tests.log"
FORMAL_BUILD_TESTS_LOG_SHA256=""
FORMAL_SOURCE_CHECKOUT_MARKER=""
FORMAL_SOURCE_CHECKOUT_MARKER_FD=""
FORMAL_SOURCE_CHECKOUT_MARKER_IDENTITY=""
FORMAL_SOURCE_CHECKOUT_MARKER_SHA256=""
FORMAL_TEST_PASSWD=""
FORMAL_TEST_PASSWD_FD=""
FORMAL_TEST_PASSWD_IDENTITY=""
FORMAL_TEST_PASSWD_SHA256=""
POLICY_FILE=""
POLICY_HELPER=""
POLICY_ID=""
POLICY_SHA256=""
POLICY_MAINTAINER=""
ELECTRON_VERSION=""
ELECTRON_ARCHIVE_SHA256=""
ELECTRON_ARCHIVE=""
ELECTRON_ARCHIVE_FD=""
ELECTRON_ARCHIVE_BASENAME=""
ELECTRON_EXECUTABLE_SHA256=""
ELECTRON_PINNED_EXECUTABLE_SHA256="c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d"
ELF_ABI_AUDIT_SHA256=""
ACCEPTANCE_BINDING_SHA256=""
ACCEPTANCE_TOOLS_MANIFEST_SHA256=""
ACCEPTANCE_ENTRYPOINT_SHA256=""
INSTALLED_RELEASE_MANIFEST_SHA256=""
PYTHON_DEPENDENCY_LOCK_STATUS="unknown"
PYTHON_LOCK_BASENAME="uv.lock"
PYTHON_LOCK_SHA256=""
PYTHON_VERSION=""
PYTHON_EXECUTABLE_SHA256=""
AGENT_PYTHON_SYMLINK_TARGET=""
NODE_EXECUTABLE_SHA256=""
FORMAL_AGENT_VENV=""
FORMAL_AGENT_SITE_PACKAGES=""
FORMAL_PYTHON_PATH=""
FORMAL_PYTHON_FD=""
FORMAL_PYTHON_IDENTITY=""
FORMAL_PYTHON_SHA256=""
FORMAL_PYTHON_LAUNCHER_PATH=""
FORMAL_PYTHON_LAUNCHER_FD=""
FORMAL_PYTHON_LAUNCHER_IDENTITY=""
FORMAL_PYTHON_LAUNCHER_SHA256=""
FORMAL_PYTHON_LAUNCHER_HELD_PATH=""
FORMAL_NODE_CURRENT_PATH=""
FORMAL_NODE_CURRENT_RAW_TARGET=""
FORMAL_NODE_CURRENT_IDENTITY=""
FORMAL_NODE_PATH=""
FORMAL_NODE_FD=""
FORMAL_NODE_IDENTITY=""
FORMAL_NODE_SHA256=""
FORMAL_NPM_CLI_PATH=""
FORMAL_NPM_CLI_FD=""
FORMAL_NPM_CLI_IDENTITY=""
FORMAL_NPM_CLI_SHA256=""
FORMAL_NPM_REGISTRY=""
FORMAL_ESLINT_PATH=""
FORMAL_ESLINT_FD=""
FORMAL_ESLINT_IDENTITY=""
FORMAL_ESLINT_SHA256=""
FORMAL_TEST_RUNTIME_SEALED=0
FORMAL_PYTHON_HELD_PATH=""
FORMAL_NODE_HELD_PATH=""
FORMAL_NPM_CLI_HELD_PATH=""
FORMAL_ESLINT_HELD_PATH=""
CANDIDATE_DEB_FIXED=0
CANDIDATE_DEB_FD=""
CANDIDATE_DEB_PATH=""
CANDIDATE_DEB_IDENTITY=""
CANDIDATE_DEB_SHA256=""
CANDIDATE_DEB_SIDECAR_FD=""
CANDIDATE_DEB_SIDECAR_PATH=""
CANDIDATE_DEB_SIDECAR_IDENTITY=""
CANDIDATE_DEB_SIDECAR_SHA256=""
CANDIDATE_DEB_SIDECAR_EXPECTED_SHA256=""
CANDIDATE_ARTIFACT_POISON="$OUTPUT_DIR/.candidate-artifacts.poisoned.$$"
MARKER_SOURCE_NAME=""
MARKER_SOURCE_SHA256=""
MARKER_SOURCE_COMMIT=""
MARKER_DEB_NAME=""
MARKER_DEB_SHA256=""
OUTPUT_ARCHIVE_DIR=""
OUTPUT_BACKUP=""
OUTPUT_REPLACEMENT_PENDING=0
ACCEPTANCE_STAGING=""
ACCEPTANCE_TARGET=""
ACCEPTANCE_ARCHIVE_DIR=""
ACCEPTANCE_BACKUP=""

LOG_DIR_REAL=""
LOG_FILE=""
FAILURE_REPORTED=0
CURRENT_STAGE="初始化"
BUILD_ROOT_PROBE_RESULTS=""

ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }
raw_system_git() {
  env -i PATH=/usr/bin:/bin LC_ALL=C LANG=C GIT_NO_REPLACE_OBJECTS=1 \
    /usr/bin/git "$@"
}
set_stage() { CURRENT_STAGE="$1"; info "阶段：$CURRENT_STAGE"; }
sha256sum() {
  if [ -x /usr/bin/sha256sum ]; then
    /usr/bin/sha256sum "$@"
  elif [ -x /usr/bin/shasum ]; then
    /usr/bin/shasum -a 256 "$@"
  else
    printf '[FAIL] 缺少受信 SHA256 工具（/usr/bin/sha256sum 或 /usr/bin/shasum）\n' >&2
    return 127
  fi
}

initialize_build_logging() {
  case "$STATE_HOME" in
    /*) ;;
    *) printf '[FAIL] XDG_STATE_HOME/HOME 必须解析为绝对路径，无法安全保存制包日志：%s\n' "$STATE_HOME" >&2; exit 1 ;;
  esac
  if [ -e "$DELIVERY_BUILD_LOG_DIR" ] || [ -L "$DELIVERY_BUILD_LOG_DIR" ]; then
    printf '[FAIL] 交付目录残留旧构建日志，请先归档并移出后重试：%s\n' "$DELIVERY_BUILD_LOG_DIR" >&2
    exit 1
  fi
  case "$LOG_DIR" in
    "$SCRIPT_DIR"|"$SCRIPT_DIR"/*)
      printf '[FAIL] 制包日志不能位于完整交付目录内：%s\n' "$LOG_DIR" >&2
      exit 1
      ;;
  esac
  mkdir -p "$LOG_DIR" "$OUTPUT_DIR" \
    || { printf '[FAIL] 无法创建制包日志或交付产物目录\n' >&2; exit 1; }
  [ -d "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ] \
    || { printf '[FAIL] 生成的安装包目录必须是真实目录：%s\n' "$OUTPUT_DIR" >&2; exit 1; }
  [ -d "$LOG_DIR" ] && [ ! -L "$LOG_DIR" ] \
    || { printf '[FAIL] 制包日志目录不是可信实体目录：%s\n' "$LOG_DIR" >&2; exit 1; }
  chmod 0700 "$LOG_DIR" \
    || { printf '[FAIL] 无法设置制包日志目录权限：%s\n' "$LOG_DIR" >&2; exit 1; }
  [ "$(stat -c '%u' "$LOG_DIR")" = "$(id -u)" ] && [ "$(stat -c '%a' "$LOG_DIR")" = "700" ] \
    || { printf '[FAIL] 制包日志目录必须由当前用户以 0700 独占：%s\n' "$LOG_DIR" >&2; exit 1; }
  LOG_DIR_REAL="$(cd "$LOG_DIR" && pwd -P)" \
    || { printf '[FAIL] 无法解析制包日志真实路径：%s\n' "$LOG_DIR" >&2; exit 1; }
  case "$LOG_DIR_REAL" in
    "$SCRIPT_DIR"|"$SCRIPT_DIR"/*)
      printf '[FAIL] 制包日志真实路径不能位于完整交付目录内：%s\n' "$LOG_DIR_REAL" >&2
      exit 1
      ;;
  esac
  LOG_FILE="$LOG_DIR/00_offline_build_$(date +%Y%m%d_%H%M%S)_$$.log"
  exec > >(tee -a "$LOG_FILE") 2>&1
}

safe_cmd_path() {
  command -v "$1" 2>/dev/null || printf 'missing'
}

write_environment_snapshot() {
  local out="$1" cmd
  {
    printf '## 环境\n'
    printf 'script=%s\n' "$0"
    printf 'stage=%s\n' "${CURRENT_STAGE:-unknown}"
    printf 'cwd=%s\n' "$(pwd)"
    printf 'uname=%s\n' "$(uname -a 2>/dev/null || true)"
    if [ -f /etc/os-release ]; then
      printf -- '-- /etc/os-release --\n'
      sed -n '1,40p' /etc/os-release
    fi
    printf 'arch=%s\n' "$(uname -m 2>/dev/null || true)"
    printf 'dpkg_arch=%s\n' "$(dpkg --print-architecture 2>/dev/null || true)"
    printf 'glibc=%s\n' "$(getconf GNU_LIBC_VERSION 2>/dev/null || true)"
    for cmd in sudo apt-get apt-cache dpkg dpkg-deb sha256sum tar gzip git curl python3 node npm uv cc df findmnt systemctl lsof readelf strings perl cmp ldd getconf desktop-file-validate; do
      printf 'cmd.%s=%s\n' "$cmd" "$(safe_cmd_path "$cmd")"
    done
    printf 'TAIJI_NODE_MIRRORS=%s\n' "${TAIJI_NODE_MIRRORS:+set}"
    printf 'TAIJI_NPM_REGISTRIES=%s\n' "${TAIJI_NPM_REGISTRIES:+set}"
    printf 'TAIJI_NPM_AUDIT_REGISTRY=%s\n' "${TAIJI_NPM_AUDIT_REGISTRY:+set}"
    printf 'TAIJI_ELECTRON_MIRRORS=%s\n' "${TAIJI_ELECTRON_MIRRORS:+set}"
    printf 'TAIJI_BUILD_ROOT=%s\n' "${TAIJI_BUILD_ROOT:-}"
    printf 'BUILD_ROOT=%s\n' "$BUILD_ROOT"
    printf 'BUILD_TMP_DIR=%s\n' "$BUILD_TMP_DIR"
    printf 'TOOL_ROOT=%s\n' "$TOOL_ROOT"
    printf 'NODE_ROOT=%s\n' "$NODE_ROOT"
    printf 'TMPDIR=%s\n' "${TMPDIR:-}"
    printf 'TMP=%s\n' "${TMP:-}"
    printf 'TEMP=%s\n' "${TEMP:-}"
    printf '%s\n' '## 构建根探针结果'
    printf '%s\n' "$BUILD_ROOT_PROBE_RESULTS"
    printf 'UV_INDEX_URL=%s\n' "${UV_INDEX_URL:-}"
    printf '\n## 交付目录\n'
    printf '\n## 最新日志\n'
    [ -f "$LOG_FILE" ] && tail -n 160 "$LOG_FILE"
  } >> "$out" 2>&1 || true
}

failure_next_steps() {
  local reason="${1:-}"
  case "$reason" in
    *"最终 DEB 必须在 Linux amd64"*|*"不是 x86_64/amd64"*|*"dpkg 架构不是 amd64"*)
      printf 'next=换到 Linux x86_64/amd64 + apt/dpkg 制包机后重新执行 /bin/bash -p ./00_制包机_生成离线交付包.sh\n'
      ;;
    *"管理员权限"*|*"sudo"*)
      printf 'next=先在制包机终端执行 sudo -v，确认当前用户具备管理员权限后重试\n'
      ;;
    *"kysec"*|*"Permission denied by kysec"*)
      printf 'next=麒麟安全策略拦截了构建脚本中的解释器写文件。新版脚本已避免用 python 写 manifest/report；请使用最新制包输入包重新构建\n'
      ;;
    *"源码包"*|*"SHA256"*|*"当前 commit"*|*"未提交改动"*|*"已暂存未提交"*)
      printf 'next=在本地重新生成唯一源码包和 SHA256SUMS.txt，并重新拷贝整个交付目录\n'
      ;;
    *"npm audit"*|*"DOCX Engine 生产依赖"*)
      printf 'next=查看 npm audit 输出；镜像不支持审计接口时设置 TAIJI_NPM_AUDIT_REGISTRY=https://registry.npmjs.org，实际存在 high/critical 漏洞时必须更新依赖后重新制包\n'
      ;;
    *"Node.js"*|*"npm ci"*|*"Electron"*)
      printf 'next=检查 DNS/代理/镜像，必要时设置 TAIJI_NODE_MIRRORS、TAIJI_NPM_REGISTRIES、TAIJI_ELECTRON_MIRRORS 后重试\n'
      ;;
    *"pyproject.toml"*|*"Permission denied"*|*"os error 13"*|*"源码权限不可读"*)
      printf 'next=构建工作区源码权限不可读。请查看新版候选目录探针和 findmnt 诊断；不要关闭麒麟安全策略，可显式设置一个经过允许的 owner-only TAIJI_BUILD_ROOT 后重试\n'
      ;;
    *"setup-local.sh"*|*"uv.lock"*|*"--locked"*|*"TAIJI_UV_LOCK_MODE"*)
      printf 'next=正式制包只允许提交态 uv.lock 的 strict 同步；请修复并提交 lock 后重新生成制包输入包，禁止现场刷新或无锁重试\n'
      ;;
    *"可用空间不足"*|*"inode 不足"*)
      printf 'next=制包文件系统资源不足。请至少准备 12 GiB 可用空间和 100000 个可用 inode；清理空间后重试，或把 TAIJI_BUILD_ROOT 指向满足条件的 owner-only taiji-agent-build-* 目录\n'
      ;;
    *)
      printf 'next=查看本诊断文件和主日志，按最后一个 [FAIL]/命令错误继续定位\n'
      ;;
  esac
}

write_failure_diagnostic() {
  local code="${1:-1}" reason="${2:-unknown}" diag
  [ "${FAILURE_REPORTED:-0}" = "1" ] && return 0
  FAILURE_REPORTED=1
  set +e
  diag="$LOG_DIR/失败诊断-$(date +%Y%m%d_%H%M%S).txt"
  {
    printf '太极 Agent 制包失败诊断\n'
    printf 'time=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    printf 'exit_code=%s\n' "$code"
    printf 'reason=%s\n' "$reason"
    failure_next_steps "$reason"
    printf '\n'
  } > "$diag"
  write_environment_snapshot "$diag"
  printf '[FAIL] 已生成失败诊断：%s\n' "$diag" >&2
  set -e
}

fail() {
  local msg="$*"
  printf '[FAIL] %s\n' "$msg" >&2
  write_failure_diagnostic 1 "$msg"
  exit 1
}

on_error() {
  local code="$1" command_text="${2:-unknown}"
  write_failure_diagnostic "$code" "命令失败：$command_text"
  printf '\n[FAIL] 离线交付包生成中断，请查看日志：%s\n' "$LOG_FILE" >&2
  exit "$code"
}

rollback_previous_build_outputs() {
  local archive_name current_uid
  [ -n "${OUTPUT_BACKUP:-}" ] || return 0
  archive_name="${OUTPUT_ARCHIVE_DIR##*/}"
  if [ "$OUTPUT_DIR" != "$SCRIPT_DIR/生成的安装包" ] \
      || [ "${OUTPUT_ARCHIVE_DIR%/*}" != "$SCRIPT_DIR/旧版备份" ] \
      || [ "$OUTPUT_BACKUP" != "$OUTPUT_ARCHIVE_DIR/生成的安装包" ]; then
    warn "旧制包产物回滚路径不属于本轮安全范围，已停止自动操作"
    return 1
  fi
  case "$archive_name" in
    制包重试-*-"$$") ;;
    *) warn "旧制包产物回滚备份不属于本轮进程，已停止自动操作"; return 1 ;;
  esac
  if [ -e "$OUTPUT_DIR" ] || [ -L "$OUTPUT_DIR" ]; then
    if [ ! -e "$OUTPUT_BACKUP" ] && [ ! -L "$OUTPUT_BACKUP" ]; then
      rmdir -- "$OUTPUT_ARCHIVE_DIR" 2>/dev/null || true
      OUTPUT_BACKUP=""
      OUTPUT_ARCHIVE_DIR=""
      OUTPUT_REPLACEMENT_PENDING=0
      return 0
    fi
    current_uid="$(id -u)"
    if [ "${OUTPUT_REPLACEMENT_PENDING:-0}" = 1 ] \
        && [ -d "$OUTPUT_DIR" ] \
        && [ ! -L "$OUTPUT_DIR" ] \
        && [ "$(stat -c '%u' "$OUTPUT_DIR")" = "$current_uid" ] \
        && [ -z "$(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
      if ! rmdir -- "$OUTPUT_DIR"; then
        warn "本轮新输出目录虽为空但无法安全移除，未覆盖任何内容：$OUTPUT_DIR"
        return 1
      fi
    else
      warn "输出目录已存在或不再满足本轮空目录条件，未覆盖任何内容：$OUTPUT_DIR"
      return 0
    fi
  fi
  [ -d "$OUTPUT_BACKUP" ] && [ ! -L "$OUTPUT_BACKUP" ] || {
    warn "旧制包产物归档中断，但本轮备份不安全，无法自动回滚：$OUTPUT_BACKUP"
    return 1
  }
  if ! mv -- "$OUTPUT_BACKUP" "$OUTPUT_DIR"; then
    warn "旧制包产物归档中断后自动回滚失败：$OUTPUT_BACKUP"
    return 1
  fi
  rmdir -- "$OUTPUT_ARCHIVE_DIR" 2>/dev/null || true
  OUTPUT_BACKUP=""
  OUTPUT_ARCHIVE_DIR=""
  OUTPUT_REPLACEMENT_PENDING=0
  warn "旧制包产物归档中断，已完整恢复上一版输出目录"
}

rollback_target_acceptance_tools() {
  local archive_name
  [ -n "${ACCEPTANCE_BACKUP:-}" ] || return 0
  archive_name="${ACCEPTANCE_ARCHIVE_DIR##*/}"
  if [ "${ACCEPTANCE_TARGET:-}" != "$SCRIPT_DIR/验收工具" ] \
      || [ "${ACCEPTANCE_ARCHIVE_DIR%/*}" != "$SCRIPT_DIR/旧版备份" ] \
      || [ "$ACCEPTANCE_BACKUP" != "$ACCEPTANCE_ARCHIVE_DIR/验收工具" ]; then
    warn "验收工具回滚路径不属于本轮安全范围，已停止自动操作"
    return 1
  fi
  case "$archive_name" in
    验收工具重试-*-"$$") ;;
    *) warn "验收工具回滚备份不属于本轮进程，已停止自动操作"; return 1 ;;
  esac
  if [ -e "$ACCEPTANCE_TARGET" ] || [ -L "$ACCEPTANCE_TARGET" ]; then
    return 0
  fi
  [ -d "$ACCEPTANCE_BACKUP" ] && [ ! -L "$ACCEPTANCE_BACKUP" ] || {
    warn "验收工具替换中断，但本轮备份不安全，无法自动回滚：$ACCEPTANCE_BACKUP"
    return 1
  }
  if ! mv -- "$ACCEPTANCE_BACKUP" "$ACCEPTANCE_TARGET"; then
    warn "验收工具替换中断后自动回滚失败：$ACCEPTANCE_BACKUP"
    return 1
  fi
  rmdir -- "$ACCEPTANCE_ARCHIVE_DIR" 2>/dev/null || true
  ACCEPTANCE_BACKUP=""
  ACCEPTANCE_ARCHIVE_DIR=""
  ACCEPTANCE_TARGET=""
  warn "验收工具替换中断，已自动恢复上一版"
}

cleanup_transient_delivery() {
  set +e
  cleanup_sealed_snapshot_transport
  close_fixed_tool_archive
  close_retained_formal_archive_snapshots
  close_build_node_runtime_fds
  if [ -n "${FORMAL_SOURCE_CHECKOUT_MARKER:-}" ]; then
    if ! close_formal_source_checkout_marker; then
      warn "正式源码测试 checkout marker 身份漂移；保留未知路径并拒绝按路径删除"
    fi
  fi
  if [ -n "${FORMAL_TEST_PASSWD:-}" ]; then
    if ! close_formal_test_passwd; then
      warn "正式 gateway 测试 passwd 身份漂移；保留未知路径并拒绝按路径删除"
    fi
  fi
  close_formal_test_runtime_fds
  if [ -n "${CANDIDATE_DEB_FD:-}" ] || [ -n "${CANDIDATE_DEB_SIDECAR_FD:-}" ]; then
    if [ "${CANDIDATE_DEB_FIXED:-0}" != 1 ] || ! candidate_deb_identity_matches; then
      poison_candidate_artifacts "cleanup observed incomplete or replaced candidate artifacts"
      warn "候选 DEB/sidecar 未完成或身份漂移；保留外来文件和失败现场"
    fi
    close_candidate_artifact_fds
  fi
  if [ -n "${FORMAL_PACKAGE_MANIFEST_FD:-}" ]; then
    if [ "${FORMAL_PACKAGE_MANIFEST_COMPLETE:-0}" != 1 ]; then
      poison_formal_package_manifest "cleanup observed an incomplete formal package manifest"
      warn "正式 package manifest 未完成；已保留失败现场"
    elif ! formal_package_manifest_identity_matches; then
      poison_formal_package_manifest "cleanup observed a replaced formal package manifest"
      warn "正式 package manifest canonical 路径已被替换；保留外来文件和失败现场"
    fi
    close_formal_package_manifest_fds
  elif [ -n "${SOURCE_PACKAGE_MANIFEST_FD:-}" ]; then
    close_formal_package_manifest_fds
  fi
  if [ -n "${FORMAL_BUILD_TEST_LOG_FD:-}" ]; then
    if ! formal_build_test_log_identity_matches; then
      poison_formal_build_test_log "cleanup observed a replaced formal build test log"
      warn "正式构建测试日志 canonical 路径已被替换；保留外来文件和失败现场"
    fi
    close_formal_build_test_log_fds
  fi
  if [ -n "${FROZEN_SOURCE_HELPER_TEMP:-}" ]; then
    case "$FROZEN_SOURCE_HELPER_TEMP" in
      "${TMPDIR:-/tmp}"/taiji-frozen-source-helper.*)
        [ -f "$FROZEN_SOURCE_HELPER_TEMP" ] && [ ! -L "$FROZEN_SOURCE_HELPER_TEMP" ] \
          && rm -f -- "$FROZEN_SOURCE_HELPER_TEMP"
        ;;
    esac
  fi
  if [ -n "${FROZEN_CONTROL_TEMP_DIR:-}" ]; then
    case "$FROZEN_CONTROL_TEMP_DIR" in
      "$FROZEN_CONTROL_TEMP_PARENT"/taiji-frozen-build-controls.*)
        [ -d "$FROZEN_CONTROL_TEMP_DIR" ] && [ ! -L "$FROZEN_CONTROL_TEMP_DIR" ] \
          && rm -rf -- "$FROZEN_CONTROL_TEMP_DIR"
        ;;
    esac
  fi
  if [ -n "${PENDING_BUILD_MARKER:-}" ]; then
    if [ -n "${PENDING_BUILD_MARKER_FD:-}" ] \
        && ! pending_build_marker_identity_matches; then
      poison_pending_build_marker "cleanup observed a replaced pending build marker"
      warn "待发布构建成功标记身份漂移；保留外来文件和失败现场"
    fi
    close_pending_build_marker_fd
    case "$PENDING_BUILD_MARKER" in
      "$OUTPUT_DIR"/.build-success.pending.*)
        poison_published_build_marker "cleanup preserved a public pending marker after failure"
        warn "已保留输出目录中的待发布标记与 poison，未按路径删除任何文件"
        ;;
      *)
        warn "已保留本轮私有构建根中的待发布标记，未做无身份绑定的删除"
        ;;
    esac
  fi
  rollback_previous_build_outputs || true
  rollback_target_acceptance_tools || true
  if [ -n "${ACCEPTANCE_STAGING:-}" ] \
      && [ "$ACCEPTANCE_STAGING" = "$SCRIPT_DIR/.验收工具.tmp-$$" ] \
      && [ -d "$ACCEPTANCE_STAGING" ] \
      && [ ! -L "$ACCEPTANCE_STAGING" ]; then
    rm -rf -- "$ACCEPTANCE_STAGING"
  fi
}

on_signal() {
  local code="$1" signal_name="$2"
  set +e
  printf '\n[FAIL] 收到信号 %s，正在恢复本轮交付目录\n' "$signal_name" >&2
  write_failure_diagnostic "$code" "收到信号：$signal_name"
  exit "$code"
}

trap cleanup_transient_delivery EXIT
trap 'on_error "$?" "$BASH_COMMAND"' ERR
trap 'on_signal 130 INT' INT
trap 'on_signal 143 TERM' TERM
trap 'on_signal 129 HUP' HUP

require_cmd() { have "$1" || fail "缺少命令：$1"; }

read_product_version() {
  local version_file="$SRC_DIR/VERSION" product_version
  [ -f "$version_file" ] || fail "源码缺少根 VERSION：$version_file"
  product_version="$(tr -d '\r\n' < "$version_file")"
  printf '%s\n' "$product_version" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' \
    || fail "根 VERSION 不是三段 SemVer：$product_version"
  if [ -n "${TAIJI_AGENT_VERSION:-}" ] && [ "$TAIJI_AGENT_VERSION" != "$product_version" ]; then
    fail "TAIJI_AGENT_VERSION 必须与根 VERSION 一致：root=$product_version env=$TAIJI_AGENT_VERSION"
  fi
  printf '%s\n' "$product_version"
}

checksum_source_archive_name() {
  [ -f "$CHECKSUM_FILE" ] || return 1
  awk '
    NF >= 2 {
      hash = $1
      if (length(hash) != 64 || hash !~ /^[0-9A-Fa-f]+$/) {
        next
      }
      path = $0
      sub(/^[^[:space:]]+[[:space:]]+\*?/, "", path)
      n = split(path, parts, "/")
      name = parts[n]
      if (name ~ /^taiji-agentv1\.0-kylin-build-src-.*\.tar\.gz$/) {
        print name
      }
    }
  ' "$CHECKSUM_FILE"
}

checksum_source_archive_hash() {
  local archive_name="$1"
  [ -f "$CHECKSUM_FILE" ] || return 1
  awk -v wanted="$archive_name" '
    NF >= 2 {
      hash = $1
      if (length(hash) != 64 || hash !~ /^[0-9A-Fa-f]+$/) {
        next
      }
      path = $0
      sub(/^[^[:space:]]+[[:space:]]+\*?/, "", path)
      n = split(path, parts, "/")
      name = parts[n]
      if (name == wanted) {
        print hash
      }
    }
  ' "$CHECKSUM_FILE" | tail -1
}

verify_source_archive_checksum() {
  local archive_name expected actual archive_input
  archive_name="$(basename "$SRC_ARCHIVE")"
  expected="$(checksum_source_archive_hash "$archive_name")"
  [ -n "$expected" ] || fail "校验文件中未找到源码包条目：$archive_name"
  if [ -n "$SOURCE_ARCHIVE_FD" ]; then
    archive_input="/proc/$$/fd/$SOURCE_ARCHIVE_FD"
  else
    archive_input="$SRC_ARCHIVE"
  fi
  actual="$(sha256sum "$archive_input" | awk '{print $1}')"
  if [ "$actual" != "$expected" ]; then
    fail "源码包 SHA256 不匹配：$archive_name"
  fi
  SOURCE_ARCHIVE_SHA256="$actual"
}

resolve_source_integrity_helper() {
  local repository_copy
  repository_copy="$SCRIPT_DIR/../packaging/linux/source-archive-integrity.py"
  if [ ! -f "$SOURCE_INTEGRITY_HELPER" ] && [ -f "$repository_copy" ]; then
    SOURCE_INTEGRITY_HELPER="$repository_copy"
  fi
  [ -f "$SOURCE_INTEGRITY_HELPER" ] && [ ! -L "$SOURCE_INTEGRITY_HELPER" ] \
    || fail "缺少可信源码归档完整性工具：$SOURCE_INTEGRITY_HELPER"
  [ "$(sha256sum "$SOURCE_INTEGRITY_HELPER" | awk '{print $1}')" = "$SOURCE_INTEGRITY_HELPER_SHA256" ] \
    || fail "源码归档完整性工具不是固定审查版本"
}

resolve_and_verify_source_inventory() {
  local expected inventory_name inventory_hash source_commit source_archive_hash
  resolve_source_integrity_helper
  inventory_name="$(basename "$SRC_ARCHIVE" .tar.gz).inventory.json"
  SOURCE_INVENTORY="$SCRIPT_DIR/$inventory_name"
  [ -f "$SOURCE_INVENTORY" ] && [ ! -L "$SOURCE_INVENTORY" ] \
    || fail "源码包缺少 archive-derived 不可变成员清单：$inventory_name"
  source_archive_hash="$(checksum_source_archive_hash "$(basename "$SRC_ARCHIVE")")"
  [ -n "$source_archive_hash" ] || fail "SHA256SUMS.txt 缺少源码包条目"
  expected="$(checksum_source_archive_hash "$inventory_name")"
  [ -n "$expected" ] || fail "SHA256SUMS.txt 缺少源码成员清单条目：$inventory_name"
  printf '%s\n' "$expected" | grep -Eq '^[0-9a-f]{64}$' \
    || fail "SHA256SUMS.txt 中源码成员清单摘要格式非法"
  inventory_hash="$expected"
  source_commit="$(basename "$SRC_ARCHIVE" | sed -E 's/^taiji-agentv1\.0-kylin-build-src-([^.]+)\.tar\.gz$/\1/')"
  SOURCE_ARCHIVE_BASENAME="$(basename "$SRC_ARCHIVE")"
  SOURCE_INVENTORY_BASENAME="$(basename "$SOURCE_INVENTORY")"
  adopt_sealed_snapshot "$SRC_ARCHIVE" "$source_archive_hash" archive \
    || fail "无法在首次源码完整性检查前固定源码归档 sealed 快照"
  exec 13< "$FIXED_TOOL_ARCHIVE_FD_PATH" \
    || fail "无法接管源码归档 sealed 快照 FD"
  SOURCE_ARCHIVE_FD=13
  close_fixed_tool_archive
  adopt_sealed_snapshot "$SOURCE_INVENTORY" "$inventory_hash" inventory \
    || fail "无法在首次源码完整性检查前固定源码清单 sealed 快照"
  python3 "$SOURCE_INTEGRITY_HELPER" verify \
    --archive-fd "$SOURCE_ARCHIVE_FD" \
    --archive-basename "$SOURCE_ARCHIVE_BASENAME" \
    --inventory-fd "$SOURCE_INVENTORY_FD" \
    || fail "源码归档与不可变成员清单不一致"
  SOURCE_INVENTORY_SHA256="$inventory_hash"
}

create_source_archive_from_git() {
  local repo_root source_gate trusted_git archive_name inventory_name archive_sha inventory_sha observed main_commit relative entry mode path actual_mode
  repo_root="$(cd "$SCRIPT_DIR/.." && pwd -P)"
  source_gate="$repo_root/scripts/check-clean-worktree.sh"
  trusted_git="$repo_root/scripts/taiji-trusted-git"
  [ -e "$repo_root/.git" ] || fail "未找到源码包，也无法从当前目录生成源码包。请先放入 taiji-agentv1.0-kylin-build-src-<hash>.tar.gz"
  require_cmd git
  [ -x /usr/bin/git ] || fail "缺少受信任系统 Git：/usr/bin/git"
  [ -x "$source_gate" ] || fail "缺少正式源码门禁：$source_gate"
  [ -x "$trusted_git" ] && [ ! -L "$trusted_git" ] || fail "缺少可信 Git 边界：$trusted_git"
  "$source_gate" \
    --mode formal \
    --repo-root "$repo_root" \
    --source-root "$repo_root" \
    || fail "发布源码包必须来自干净本地 main"
  FROZEN_SOURCE_COMMIT="$(raw_system_git -C "$repo_root" rev-parse --verify 'HEAD^{commit}')"
  [ "$(raw_system_git -C "$repo_root" symbolic-ref --quiet --short HEAD)" = main ] \
    || fail "冻结源码必须来自 main"
  main_commit="$(raw_system_git -C "$repo_root" rev-parse --verify refs/heads/main)"
  [ "$main_commit" = "$FROZEN_SOURCE_COMMIT" ] || fail "main 与冻结 source commit 不一致"
  for relative in \
    "scripts/check-clean-worktree.sh" \
    "scripts/taiji-trusted-git" \
    "packaging/linux/source-archive-integrity.py" \
    "taijiagent 打包交付/00_制包机_生成离线交付包.sh"; do
    path="$repo_root/$relative"
    entry="$(raw_system_git -c core.quotePath=false -C "$repo_root" ls-tree "$FROZEN_SOURCE_COMMIT" -- "$relative")"
    [ -n "$entry" ] || fail "冻结 commit 缺少制包参与成员：$relative"
    mode="${entry%% *}"
    [ -f "$path" ] && [ ! -L "$path" ] && [ "$(stat -c '%h' "$path")" = 1 ] \
      && [ "$(stat -c '%u' "$path")" = "$(id -u)" ] \
      || fail "冻结成员文件类型、属主或硬链接数不安全：$relative"
    actual_mode="$(stat -c '%a' "$path")"
    case "$mode" in 100644) [ "$actual_mode" = 644 ] || fail "冻结成员模式漂移：$relative" ;; 100755) [ "$actual_mode" = 755 ] || fail "冻结成员模式漂移：$relative" ;; *) fail "冻结成员模式不安全：$relative" ;; esac
    raw_system_git -C "$repo_root" show "$FROZEN_SOURCE_COMMIT:$relative" \
      | cmp -s - "$path" || fail "工作树制包参与成员与冻结 commit 不一致：$relative"
  done
  FROZEN_CONTROL_TEMP_PARENT="${TMPDIR:-/tmp}"
  FROZEN_CONTROL_TEMP_DIR="$(mktemp -d "$FROZEN_CONTROL_TEMP_PARENT/taiji-frozen-build-controls.XXXXXX")"
  chmod 0700 "$FROZEN_CONTROL_TEMP_DIR"
  raw_system_git -C "$repo_root" show "$FROZEN_SOURCE_COMMIT:scripts/taiji-trusted-git" > "$FROZEN_CONTROL_TEMP_DIR/taiji-trusted-git"
  raw_system_git -C "$repo_root" show "$FROZEN_SOURCE_COMMIT:scripts/check-clean-worktree.sh" > "$FROZEN_CONTROL_TEMP_DIR/check-clean-worktree.sh"
  chmod 0755 "$FROZEN_CONTROL_TEMP_DIR/taiji-trusted-git" "$FROZEN_CONTROL_TEMP_DIR/check-clean-worktree.sh"
  trusted_git="$FROZEN_CONTROL_TEMP_DIR/taiji-trusted-git"
  source_gate="$FROZEN_CONTROL_TEMP_DIR/check-clean-worktree.sh"
  "$source_gate" --mode formal --repo-root "$repo_root" --source-root "$repo_root" \
    || fail "冻结后的正式源码门禁复核失败"
  archive_name="taiji-agentv1.0-kylin-build-src-$FROZEN_SOURCE_COMMIT.tar.gz"
  inventory_name="${archive_name%.tar.gz}.inventory.json"
  info "使用 git archive 生成源码包：$archive_name"
  "$trusted_git" -C "$repo_root" -c tar.umask=0022 archive --format=tar --prefix=taiji-agentv1.0/ "$FROZEN_SOURCE_COMMIT" | gzip -n > "$SCRIPT_DIR/$archive_name"
  FROZEN_SOURCE_HELPER_TEMP="$FROZEN_CONTROL_TEMP_DIR/source-archive-integrity.py"
  "$trusted_git" -C "$repo_root" show "$FROZEN_SOURCE_COMMIT:packaging/linux/source-archive-integrity.py" > "$FROZEN_SOURCE_HELPER_TEMP"
  chmod 0600 "$FROZEN_SOURCE_HELPER_TEMP"
  SOURCE_INTEGRITY_HELPER="$FROZEN_SOURCE_HELPER_TEMP"
  resolve_source_integrity_helper
  rm -f -- "$SCRIPT_DIR/$inventory_name"
  python3 "$SOURCE_INTEGRITY_HELPER" create \
    --archive "$SCRIPT_DIR/$archive_name" \
    --inventory "$SCRIPT_DIR/$inventory_name" \
    --source-commit "$FROZEN_SOURCE_COMMIT" \
    || fail "无法生成源码不可变成员清单"
  archive_sha="$(sha256sum "$SCRIPT_DIR/$archive_name" | awk '{print $1}')"
  inventory_sha="$(sha256sum "$SCRIPT_DIR/$inventory_name" | awk '{print $1}')"
  {
    printf '%s  %s\n' "$archive_sha" "$archive_name"
    printf '%s  %s\n' "$inventory_sha" "$inventory_name"
  } > "$CHECKSUM_FILE"
  SRC_ARCHIVE="$SCRIPT_DIR/$archive_name"
  observed="$(raw_system_git -C "$repo_root" rev-parse --verify 'HEAD^{commit}')"
  [ "$observed" = "$FROZEN_SOURCE_COMMIT" ] \
    && [ "$(raw_system_git -C "$repo_root" rev-parse --verify refs/heads/main)" = "$FROZEN_SOURCE_COMMIT" ] \
    && [ "$(raw_system_git -C "$repo_root" symbolic-ref --quiet --short HEAD)" = main ] \
    || fail "源码归档生成期间 HEAD/main 偏离冻结 source commit"
  "$source_gate" --mode formal --repo-root "$repo_root" --source-root "$repo_root" \
    || fail "源码归档生成后正式源码门禁复核失败"
  for relative in \
    "scripts/check-clean-worktree.sh" \
    "scripts/taiji-trusted-git" \
    "packaging/linux/source-archive-integrity.py" \
    "taijiagent 打包交付/00_制包机_生成离线交付包.sh"; do
    path="$repo_root/$relative"
    entry="$(raw_system_git -c core.quotePath=false -C "$repo_root" ls-tree "$FROZEN_SOURCE_COMMIT" -- "$relative")"
    mode="${entry%% *}"
    actual_mode="$(stat -c '%a' "$path")"
    case "$mode:$actual_mode" in 100644:644|100755:755) ;; *) fail "归档后冻结成员模式漂移：$relative" ;; esac
    raw_system_git -C "$repo_root" show "$FROZEN_SOURCE_COMMIT:$relative" \
      | cmp -s - "$path" || fail "归档后工作树制包参与成员偏离冻结 commit：$relative"
  done
  ok "源码包已生成并写入 SHA256SUMS.txt"
}

resolve_source_archive() {
  if [ -n "$SRC_ARCHIVE" ]; then
    [ -f "$SRC_ARCHIVE" ] || fail "未找到指定源码包：$SRC_ARCHIVE"
    return
  fi

  if [ -f "$CHECKSUM_FILE" ]; then
    local checksum_count checksum_archive
    checksum_count="$(checksum_source_archive_name | wc -l | tr -d ' ')"
    if [ "$checksum_count" = "1" ]; then
      checksum_archive="$(checksum_source_archive_name)"
      SRC_ARCHIVE="$SCRIPT_DIR/$checksum_archive"
      [ -f "$SRC_ARCHIVE" ] || fail "校验文件指定的源码包不存在：$SRC_ARCHIVE"
      ok "使用校验文件指定的源码包：$checksum_archive"
      return
    fi
    warn "校验文件中源码包条目数量不是 1 个，将回退到目录扫描"
  fi

  local count
  count="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | wc -l | tr -d ' ')"
  if [ "$count" = "1" ]; then
    SRC_ARCHIVE="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | sort | tail -1)"
    return
  fi

  create_source_archive_from_git
}

cleanup_delivery_metadata() {
  info "检查交付文件夹中的拷贝元数据"
  local metadata
  metadata="$(find "$SCRIPT_DIR" \( -name '__MACOSX' -o -name '.DS_Store' -o -name '._*' -o -name '.AppleDouble' -o -name 'PaxHeaders*' \) -print)"
  if [ -n "$metadata" ]; then
    warn "发现 macOS 拷贝元数据，将自动清理"
    printf '%s\n' "$metadata" >&2
    find "$SCRIPT_DIR" \( -name '__MACOSX' -o -name '.DS_Store' -o -name '._*' -o -name '.AppleDouble' -o -name 'PaxHeaders*' \) -exec rm -rf -- {} +
  fi
  ok "拷贝元数据检查完成"
}

require_admin_capability() {
  require_cmd sudo
  if sudo -n true >/dev/null 2>&1; then
    ok "管理员权限预检通过：sudo 已可用"
    return
  fi
  info "需要管理员权限预检。这里可能需要输入 sudo 密码。"
  sudo -v || fail "管理员权限预检失败：当前用户不能执行 sudo，无法安装制包依赖"
  ok "管理员权限预检通过"
}

run_release_preflight() {
  local preflight_script="$SCRIPT_DIR/01_制包机_发布预检.sh"
  local repo_root="${1:-$(cd "$SCRIPT_DIR/.." && pwd)}"
  [ -x "$preflight_script" ] || fail "缺少发布预检脚本：$preflight_script"
  TAIJI_RELEASE_REQUIRE_ARTIFACTS="${TAIJI_RELEASE_REQUIRE_ARTIFACTS:-0}" \
    TAIJI_RELEASE_SKIP_GIT_CHECK="${TAIJI_RELEASE_SKIP_GIT_CHECK:-0}" \
    TAIJI_EXTRACTED_SOURCE_ROOT="${TAIJI_EXTRACTED_SOURCE_ROOT:-}" \
    TAIJI_BUILD_MARKER_PATH="${TAIJI_BUILD_MARKER_PATH:-}" \
    TAIJI_EXPECT_PUBLISHED_BUILD_MARKER="${TAIJI_EXPECT_PUBLISHED_BUILD_MARKER:-1}" \
    TAIJI_REPO_ROOT="$repo_root" \
    "$preflight_script"
}

preflight() {
  info "检查制包机环境"
  cleanup_delivery_metadata
  [ "$(uname -s)" = "Linux" ] || fail "最终 DEB 必须在 Linux amd64 制包机生成，当前为：$(uname -s)"
  case "$(uname -m)" in
    x86_64|amd64) ok "CPU 架构符合：$(uname -m)" ;;
    *) fail "当前 CPU 架构不是 x86_64/amd64：$(uname -m)" ;;
  esac
  require_cmd apt-get
  require_cmd apt-cache
  require_cmd dpkg
  require_cmd sha256sum
  require_cmd readlink
  require_admin_capability
  arch="$(dpkg --print-architecture 2>/dev/null || true)"
  [ "$arch" = "amd64" ] || fail "dpkg 架构不是 amd64：${arch:-unknown}"
}

build_root_candidates() {
  local uid cache_root home_cache candidate
  uid="$(id -u 2>/dev/null || printf user)"
  if [ -n "${TAIJI_BUILD_ROOT:-}" ]; then
    printf '%s\n' "$TAIJI_BUILD_ROOT"
    return 0
  fi

  cache_root="${XDG_CACHE_HOME:-}"
  if [ -n "$cache_root" ] && [[ "$cache_root" = /* ]]; then
    printf '%s/taiji-agent-build-%s\n' "${cache_root%/}" "$uid"
  fi
  if [ -n "${HOME:-}" ] && [[ "$HOME" = /* ]]; then
    home_cache="$HOME/.cache"
    if [ "${cache_root%/}" != "$home_cache" ]; then
      printf '%s/taiji-agent-build-%s\n' "$home_cache" "$uid"
    fi
  fi
  printf '/var/tmp/taiji-agent-build-%s\n' "$uid"
}

candidate_failure() {
  local candidate="$1" reason="$2"
  warn "候选构建根不可用：${candidate}（${reason}）"
  BUILD_ROOT_PROBE_RESULTS+="candidate=$candidate stage=validation reason=$reason"$'\n'
  return 1
}

validate_candidate_build_root() {
  local candidate="$1" resolved_candidate resolved_script basename_candidate
  case "$candidate" in
    /*) ;;
    *) candidate_failure "$candidate" "必须是绝对路径"; return 1 ;;
  esac
  resolved_candidate="$(readlink -m -- "$candidate")" || {
    candidate_failure "$candidate" "无法解析路径"
    return 1
  }
  basename_candidate="${resolved_candidate##*/}"
  case "$basename_candidate" in
    taiji-agent-build-?*) ;;
    *) candidate_failure "$resolved_candidate" "目录名必须使用 taiji-agent-build-*"; return 1 ;;
  esac
  case "$resolved_candidate" in
    "/"|"/tmp"|"/tmp/"*|"/home"|"/var"|"/usr")
      candidate_failure "$resolved_candidate" "拒绝宽泛或受限目录"
      return 1
      ;;
  esac
  [ ! -L "$candidate" ] || {
    candidate_failure "$candidate" "不能是符号链接"
    return 1
  }
  if [ -e "$candidate" ] && [ ! -d "$candidate" ]; then
    candidate_failure "$candidate" "已存在但不是目录"
    return 1
  fi
  resolved_script="$(readlink -f -- "$SCRIPT_DIR")" || {
    candidate_failure "$candidate" "无法解析交付目录"
    return 1
  }
  case "$resolved_candidate" in
    "$resolved_script"|"$resolved_script"/*)
      candidate_failure "$resolved_candidate" "不能位于交付目录内"
      return 1
      ;;
  esac
}

validate_build_root_location() {
  validate_candidate_build_root "$BUILD_ROOT" \
    || fail "显式 TAIJI_BUILD_ROOT 不符合安全路径要求：$BUILD_ROOT"
}

validate_safe_build_root_path() {
  validate_candidate_build_root "$BUILD_ROOT" \
    || fail "TAIJI_BUILD_ROOT 不符合安全路径要求：$BUILD_ROOT"
}

record_probe_failure() {
  local candidate="$1" stage="$2" output="$3" findmnt_output
  findmnt_output=""
  if have findmnt; then
    findmnt_output="$(findmnt -T "$candidate" 2>&1 || true)"
  fi
  BUILD_ROOT_PROBE_RESULTS+="candidate=$candidate stage=$stage"$'\n'
  BUILD_ROOT_PROBE_RESULTS+="output=$output"$'\n'
  BUILD_ROOT_PROBE_RESULTS+="findmnt=$findmnt_output"$'\n'
  warn "构建根探针失败：candidate=$candidate stage=$stage"
  [ -z "$output" ] || warn "$output"
  [ -z "$findmnt_output" ] || warn "findmnt -T ${candidate}：${findmnt_output}"
}

probe_build_root() {
  local candidate="$1" probe_dir probe_output marker marker_value marker_meta marker_uid marker_mode marker_links
  validate_candidate_build_root "$candidate" || return 1
  if [ -e "$candidate" ]; then
    [ -d "$candidate" ] && [ ! -L "$candidate" ] || return 1
    [ "$(stat -c '%u' "$candidate" 2>/dev/null || printf -1)" = "$(id -u)" ] || {
      candidate_failure "$candidate" "目录不属于当前用户"
      return 1
    }
    chmod 0700 "$candidate" 2>/dev/null || {
      candidate_failure "$candidate" "无法设置 0700 权限"
      return 1
    }
    marker="$candidate/$BUILD_ROOT_OWNER_MARKER"
    if [ -e "$marker" ] || [ -L "$marker" ]; then
      [ -f "$marker" ] && [ ! -L "$marker" ] || {
        candidate_failure "$candidate" "所有权标记不是普通文件"
        return 1
      }
      marker_meta="$(stat -c '%u %a %h' "$marker" 2>/dev/null || true)"
      read -r marker_uid marker_mode marker_links <<< "$marker_meta"
      marker_value="$(tr -d '\r\n' < "$marker" 2>/dev/null || true)"
      if [ "$marker_uid" != "$(id -u)" ] || [ "$marker_mode" != "600" ] || [ "$marker_links" != "1" ] || [ "$marker_value" != "$BUILD_ROOT_OWNER_TOKEN" ]; then
        candidate_failure "$candidate" "所有权标记不可信"
        return 1
      fi
    elif [ -n "$(find "$candidate" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
      candidate_failure "$candidate" "已有内容但缺少可信所有权标记"
      return 1
    fi
  else
    mkdir -p -- "$candidate" 2>/dev/null || {
      candidate_failure "$candidate" "无法创建目录"
      return 1
    }
    chmod 0700 "$candidate" 2>/dev/null || {
      candidate_failure "$candidate" "无法设置 0700 权限"
      return 1
    }
  fi

  probe_dir="$candidate/.probe-$$"
  rm -rf -- "$probe_dir"
  if ! mkdir -m 0700 -- "$probe_dir" 2>/dev/null; then
    record_probe_failure "$candidate" "mkdir" "无法创建探针目录"
    return 1
  fi
  if ! probe_output="$({
    printf '#include <stdlib.h>\nint main(void) { return 0; }\n' > "$probe_dir/probe.c"
    cc "$probe_dir/probe.c" -o "$probe_dir/probe-exec"
    "$probe_dir/probe-exec"
    cc -shared -fPIC "$probe_dir/probe.c" -o "$probe_dir/probe.so"
    python3 - "$probe_dir/probe.so" <<'PY'
import ctypes
import sys

ctypes.CDLL(sys.argv[1])
PY
  } 2>&1)"; then
    record_probe_failure "$candidate" "exec-or-dlopen" "$probe_output"
    rm -rf -- "$probe_dir"
    return 1
  fi
  rm -rf -- "$probe_dir"
  BUILD_ROOT_PROBE_RESULTS+="candidate=$candidate stage=success"$'\n'
  return 0
}

configure_build_tmp() {
  [ -n "$BUILD_ROOT" ] || fail "构建根尚未选择，不能配置临时目录"
  BUILD_TMP_DIR="$BUILD_ROOT/tmp"
  SRC_DIR="$BUILD_ROOT/taiji-agentv1.0"
  TOOL_ROOT="$BUILD_ROOT/.build-tools"
  NODE_ROOT="$TOOL_ROOT/node"
  UV_ROOT="$TOOL_ROOT/uv"
  PYTHON_ROOT="$TOOL_ROOT/python"
  UV_BIN="$UV_ROOT/current/uv"
  PYTHON_BIN="$PYTHON_ROOT/current/bin/python3.11"
  PENDING_BUILD_MARKER="$BUILD_ROOT/.build-success.pending"
  mkdir -p -- "$BUILD_TMP_DIR" "$TOOL_ROOT" || fail "无法创建构建临时目录或工具根"
  chmod 0700 "$BUILD_TMP_DIR" "$TOOL_ROOT" || fail "无法设置构建临时目录或工具根权限"
  export TMPDIR="$BUILD_TMP_DIR" TMP="$BUILD_TMP_DIR" TEMP="$BUILD_TMP_DIR"
  ok "构建临时目录已固定：$BUILD_TMP_DIR"
  ok "构建工具根已固定：$TOOL_ROOT"
}

select_build_root() {
  local candidate explicit
  explicit="${TAIJI_BUILD_ROOT:-}"
  while IFS= read -r candidate; do
    [ -n "$candidate" ] || continue
    if ! validate_candidate_build_root "$candidate"; then
      [ -n "$explicit" ] && fail "显式 TAIJI_BUILD_ROOT 不符合安全路径要求：$candidate"
      continue
    fi
    if probe_build_root "$candidate"; then
      BUILD_ROOT="$candidate"
      create_owned_build_root
      configure_build_tmp
      ok "已选择可执行且可加载动态库的构建根：$BUILD_ROOT"
      return 0
    fi
    if [ -n "$explicit" ]; then
      fail "显式 TAIJI_BUILD_ROOT 探针失败：$candidate"
    fi
  done < <(build_root_candidates)
  fail "没有候选构建根通过可执行文件和动态库加载探针；请查看失败诊断中的 findmnt 结果"
}

require_owned_build_root() {
  local marker="$BUILD_ROOT/$BUILD_ROOT_OWNER_MARKER" current_uid root_uid root_mode marker_uid marker_mode marker_links marker_value
  validate_safe_build_root_path
  [ -d "$BUILD_ROOT" ] && [ ! -L "$BUILD_ROOT" ] \
    || fail "构建工作区不是可信实体目录：$BUILD_ROOT"
  current_uid="$(id -u)"
  root_uid="$(stat -c '%u' "$BUILD_ROOT")" || fail "无法读取构建工作区所有者：$BUILD_ROOT"
  root_mode="$(stat -c '%a' "$BUILD_ROOT")" || fail "无法读取构建工作区权限：$BUILD_ROOT"
  [ "$root_uid" = "$current_uid" ] || fail "构建工作区不属于当前用户，拒绝清理：$BUILD_ROOT"
  [ "$root_mode" = "700" ] || fail "构建工作区权限必须是 0700，拒绝清理：$BUILD_ROOT"
  [ -f "$marker" ] && [ ! -L "$marker" ] || fail "构建工作区缺少可信所有权标记：$marker"
  marker_uid="$(stat -c '%u' "$marker")" || fail "无法读取构建工作区标记所有者：$marker"
  marker_mode="$(stat -c '%a' "$marker")" || fail "无法读取构建工作区标记权限：$marker"
  marker_links="$(stat -c '%h' "$marker")" || fail "无法读取构建工作区标记链接数：$marker"
  [ "$marker_uid" = "$current_uid" ] && [ "$marker_mode" = "600" ] && [ "$marker_links" = "1" ] \
    || fail "构建工作区所有权标记不可信：$marker"
  marker_value="$(tr -d '\r\n' < "$marker")"
  [ "$marker_value" = "$BUILD_ROOT_OWNER_TOKEN" ] \
    || fail "构建工作区所有权标记不匹配：$marker"
}

create_owned_build_root() {
  local marker="$BUILD_ROOT/$BUILD_ROOT_OWNER_MARKER" marker_tmp
  validate_safe_build_root_path
  mkdir -p -- "$BUILD_ROOT" || fail "无法创建构建工作区：$BUILD_ROOT"
  chmod 0700 "$BUILD_ROOT" || fail "无法设置构建工作区权限：$BUILD_ROOT"
  [ "$(stat -c '%u' "$BUILD_ROOT")" = "$(id -u)" ] \
    || fail "新建构建工作区不属于当前用户：$BUILD_ROOT"
  marker_tmp="$marker.tmp.$$"
  (umask 077; printf '%s\n' "$BUILD_ROOT_OWNER_TOKEN" > "$marker_tmp") \
    || fail "无法写入构建工作区所有权标记：$marker_tmp"
  chmod 0600 "$marker_tmp" || fail "无法设置构建工作区标记权限：$marker_tmp"
  mv -f -- "$marker_tmp" "$marker" || fail "无法发布构建工作区所有权标记：$marker"
  require_owned_build_root
}

prepare_source_release() {
  info "准备并校验源码包"
  require_cmd tar
  require_cmd gzip
  require_cmd sha256sum
  resolve_source_archive
  [ -f "$SRC_ARCHIVE" ] || fail "未找到源码包：$SRC_ARCHIVE"
  [ -f "$CHECKSUM_FILE" ] || fail "正式制包缺少 SHA256SUMS.txt"
  resolve_and_verify_source_inventory
  verify_source_archive_checksum
  ok "源码包与 archive-derived 成员清单校验通过"
  run_release_preflight
}

verify_builder_input_package() {
  local helper_sha source_name source_commit input_parent input_archive input_manifest input_checksum
  [ -f "$BUILDER_INPUT_HELPER" ] && [ ! -L "$BUILDER_INPUT_HELPER" ] \
    && [ "$(stat -c '%h' "$BUILDER_INPUT_HELPER")" = "1" ] \
    || fail "制包机输入包审计工具缺失或不安全：$BUILDER_INPUT_HELPER"
  helper_sha="$(sha256sum "$BUILDER_INPUT_HELPER" | awk '{print $1}')"
  [ "$helper_sha" = "$BUILDER_INPUT_HELPER_SHA256" ] \
    || fail "制包机输入包审计工具不是固定审查版本"

  resolve_source_archive
  [ -f "$SRC_ARCHIVE" ] && [ ! -L "$SRC_ARCHIVE" ] \
    || fail "制包机输入包中的源码包缺失或不安全：$SRC_ARCHIVE"
  [ "$(cd "$(dirname "$SRC_ARCHIVE")" && pwd -P)" = "$SCRIPT_DIR" ] \
    || fail "源码包必须来自已验证的制包机输入包解压目录"
  source_name="$(basename "$SRC_ARCHIVE")"
  source_commit="$(printf '%s\n' "$source_name" | sed -nE 's/^taiji-agentv1\.0-kylin-build-src-([0-9a-f]{40})\.tar\.gz$/\1/p')"
  printf '%s\n' "$source_commit" | grep -Eq '^[0-9a-f]{40}$' \
    || fail "无法从源码包名称解析完整 commit：$source_name"

  input_parent="$(cd "$SCRIPT_DIR/.." && pwd -P)"
  input_archive="$input_parent/taijiagent-制包机输入-$source_commit.tar.gz"
  input_manifest="$input_parent/taijiagent-制包机输入-$source_commit.manifest.json"
  input_checksum="$input_archive.sha256"
  python3 - "$input_parent" "$input_archive" "$input_manifest" "$input_checksum" <<'PY' \
    || fail "制包机输入包三件套不唯一或与源码 commit 不一致"
import re
import sys
from pathlib import Path

parent = Path(sys.argv[1])
archive, manifest, checksum = (Path(value) for value in sys.argv[2:5])
patterns = (
    re.compile(r"^taijiagent-制包机输入-[0-9a-f]{40}\.tar\.gz$"),
    re.compile(r"^taijiagent-制包机输入-[0-9a-f]{40}\.manifest\.json$"),
    re.compile(r"^taijiagent-制包机输入-[0-9a-f]{40}\.tar\.gz\.sha256$"),
)
candidates = [
    path
    for path in parent.iterdir()
    if any(pattern.fullmatch(path.name) for pattern in patterns)
]
expected = {archive.name, manifest.name, checksum.name}
if len(candidates) != 3 or {path.name for path in candidates} != expected:
    raise SystemExit("builder input triplet is not unique")
PY

  python3 "$BUILDER_INPUT_HELPER" verify \
    --archive "$input_archive" \
    --manifest "$input_manifest" \
    --checksum "$input_checksum" \
    --extracted-dir "$SCRIPT_DIR" \
    || fail "制包机输入包三件套或解压成员验证失败"
  ok "制包机输入包三件套及解压成员已绑定：$source_commit"
}

archive_previous_build_outputs() {
  local path name current_uid archive_root archive_dir entry_count=0
  [ -d "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ] \
    || fail "生成的安装包目录必须是真实目录：$OUTPUT_DIR"
  current_uid="$(id -u)"

  while IFS= read -r -d '' path; do
    entry_count=$((entry_count + 1))
    name="${path##*/}"
    case "$name" in
      .formal-package-manifest.poisoned.*|.formal-build-tests.poisoned.*|.build-success.poisoned.*|.candidate-artifacts.poisoned.*)
        fail "生成的安装包目录含上次并发替换 poison，请人工核对 canonical 文件与该 poison 后移出整个失败现场：$path"
        ;;
      .build-success|taiji-package-manifest.json|构建报告.txt|formal-build-tests.log) ;;
      taiji-agent_*_amd64.deb|taiji-agent_*_amd64.deb.sha256)
        printf '%s\n' "$name" | grep -Eq '^taiji-agent_(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)_amd64\.deb(\.sha256)?$' \
          || fail "生成的安装包目录含未知文件，未移动任何内容：$path"
        ;;
      *) fail "生成的安装包目录含未知条目，未移动任何内容：$path" ;;
    esac
    [ -f "$path" ] && [ ! -L "$path" ] \
      || fail "上次制包产物不是实体普通文件，未移动任何内容：$path"
    [ "$(stat -c '%h' "$path")" = "1" ] \
      || fail "上次制包产物存在硬链接，未移动任何内容：$path"
    [ "$(stat -c '%u' "$path")" = "$current_uid" ] \
      || fail "上次制包产物不属于当前用户，未移动任何内容：$path"
  done < <(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print0)

  [ "$entry_count" -gt 0 ] || return 0
  archive_root="$SCRIPT_DIR/旧版备份"
  [ ! -L "$archive_root" ] || fail "旧版备份目录不能是符号链接：$archive_root"
  install -d -m 0755 "$archive_root"
  archive_dir="$archive_root/制包重试-$(date -u '+%Y%m%dT%H%M%SZ')-$$"
  install -d -m 0700 "$archive_dir"
  OUTPUT_ARCHIVE_DIR="$archive_dir"
  OUTPUT_BACKUP="$archive_dir/生成的安装包"
  mv -- "$OUTPUT_DIR" "$OUTPUT_BACKUP"
  OUTPUT_REPLACEMENT_PENDING=1
  install -d -m 0755 "$OUTPUT_DIR"
  OUTPUT_BACKUP=""
  OUTPUT_ARCHIVE_DIR=""
  OUTPUT_REPLACEMENT_PENDING=0
  ok "已把上次中断留下的已知制包产物归档到：$archive_dir"
}

install_build_dependencies() {
  info "安装制包依赖。这里可能需要输入 sudo 密码。"
  sudo env DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC apt-get update
  sudo env DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC apt-get install -y \
    curl ca-certificates build-essential python3 python3-dev libffi-dev git rsync \
    dpkg-dev binutils perl-base diffutils libc-bin file desktop-file-utils \
    lsof xz-utils tar gzip openssl \
    libc6 libgtk-3-0 libnss3 libnspr4 libxss1 libasound2 libatk1.0-0 \
    libatk-bridge2.0-0 libatspi2.0-0 libdrm2 libgbm1 libxkbcommon0 libx11-6 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libxrender1 \
    libxshmfence1 libxcb1 libcups2 libdbus-1-3 libglib2.0-0 libpango-1.0-0 \
    libcairo2 libexpat1 libfontconfig1 libsecret-1-0 libxtst6 libuuid1 xdg-utils
}

verify_build_command_contract() {
  local command missing=""
  for command in \
    curl git tar gzip xz sha256sum openssl python3 cc rsync dpkg dpkg-deb \
    file desktop-file-validate lsof readelf strings perl cmp ldd getconf \
    stat mktemp mkfifo date df find grep sed awk sort head tail wc tr install chmod cp mv readlink; do
    if ! have "$command"; then
      missing="$missing $command"
    fi
  done
  [ -z "$missing" ] \
    || fail "制包依赖安装后仍缺少命令：${missing# }"
  ok "制包命令依赖闭包已通过"
}

verify_trusted_system_tools() {
  local helper trusted_readelf
  helper="$SRC_DIR/packaging/linux/trusted_system_tools.py"
  [ -f "$helper" ] && [ ! -L "$helper" ] \
    || fail "源码缺少可信系统工具解析器：$helper"
  trusted_readelf="$(python3 "$helper" readelf)" \
    || fail "系统 readelf 未通过可信路径校验；请确认 binutils 安装完整"
  [ -n "$trusted_readelf" ] || fail "可信 readelf 解析结果为空"
  ok "可信 readelf 已就绪：$trusted_readelf"
}

sealed_snapshot_python_source() {
  /usr/bin/cat <<'PY'
from __future__ import annotations

import ctypes
import fcntl
import hashlib
import os
import stat
import sys


def fail(message):
    raise RuntimeError("sealed snapshot: " + message)


def required_seals():
    required_os = ("memfd_create", "MFD_ALLOW_SEALING", "MFD_CLOEXEC")
    required_fcntl = (
        "F_ADD_SEALS",
        "F_GET_SEALS",
        "F_SEAL_WRITE",
        "F_SEAL_GROW",
        "F_SEAL_SHRINK",
        "F_SEAL_SEAL",
    )
    missing = [name for name in required_os if not hasattr(os, name)]
    missing.extend(name for name in required_fcntl if not hasattr(fcntl, name))
    if missing:
        fail("Linux memfd sealing is unavailable: " + ",".join(missing))
    return (
        fcntl.F_SEAL_WRITE
        | fcntl.F_SEAL_GROW
        | fcntl.F_SEAL_SHRINK
        | fcntl.F_SEAL_SEAL
    )


def stable_identity(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def valid_sha256(value):
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def write_all(descriptor, payload):
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            fail("memfd write was truncated")
        offset += written


def hash_descriptor(descriptor, size):
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            fail("snapshot was truncated")
        digest.update(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        fail("snapshot grew while it was read")
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def verify_descriptor(descriptor, expected_sha256):
    seals = required_seals()
    metadata = os.fstat(descriptor)
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or mode not in (0o400, 0o500)
    ):
        fail("snapshot is not a non-empty regular file")
    actual_seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
    if actual_seals & seals != seals:
        fail("snapshot does not have every required write/grow/shrink/seal bit")
    actual_sha256 = hash_descriptor(descriptor, metadata.st_size)
    if actual_sha256 != expected_sha256:
        fail("snapshot SHA256 does not match the pinned identity")
    return metadata, actual_sha256, actual_seals


def create_snapshot(path, expected_sha256, mode_text):
    if not valid_sha256(expected_sha256):
        fail("expected SHA256 is invalid")
    if mode_text not in ("0400", "0500"):
        fail("snapshot mode must be 0400 or 0500")
    snapshot_mode = int(mode_text, 8)
    before = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.getuid()
        or before.st_size <= 0
    ):
        fail("source must be a current-user single-link regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    source_descriptor = os.open(path, flags)
    snapshot_descriptor = -1
    try:
        opened = os.fstat(source_descriptor)
        if stable_identity(opened) != stable_identity(before):
            fail("source changed before it was opened")
        memfd_flags = os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC
        snapshot_descriptor = os.memfd_create("taiji-fixed-snapshot", memfd_flags)
        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(source_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                fail("source was truncated while it was copied")
            digest.update(chunk)
            write_all(snapshot_descriptor, chunk)
            remaining -= len(chunk)
        if os.read(source_descriptor, 1):
            fail("source grew while it was copied")
        after = os.fstat(source_descriptor)
        current = os.stat(path, follow_symlinks=False)
        if (
            stable_identity(opened) != stable_identity(after)
            or stable_identity(opened) != stable_identity(current)
        ):
            fail("source changed while the immutable snapshot was created")
        if digest.hexdigest() != expected_sha256:
            fail("source SHA256 does not match the pinned identity")
        os.fchmod(snapshot_descriptor, snapshot_mode)
        seals = required_seals()
        fcntl.fcntl(snapshot_descriptor, fcntl.F_ADD_SEALS, seals)
        metadata, actual_sha256, actual_seals = verify_descriptor(
            snapshot_descriptor, expected_sha256
        )
        print(
            "READY\t{}\t{}\t{}\t{}".format(
                snapshot_descriptor,
                actual_sha256,
                metadata.st_size,
                actual_seals,
            ),
            flush=True,
        )
        if os.read(0, 1) != b"A":
            fail("snapshot was not adopted by the parent")
    finally:
        os.close(source_descriptor)
        if snapshot_descriptor >= 0:
            os.close(snapshot_descriptor)


def create_payload_snapshot(path, mode_text):
    if mode_text not in ("0400", "0500"):
        fail("snapshot mode must be 0400 or 0500")
    source_descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    snapshot_descriptor = -1
    try:
        snapshot_descriptor = os.memfd_create(
            "taiji-fixed-payload-snapshot",
            os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC,
        )
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 4 * 1024 * 1024:
                fail("payload snapshot exceeds 4 MiB")
            digest.update(chunk)
            write_all(snapshot_descriptor, chunk)
        if total <= 0:
            fail("payload snapshot is empty")
        expected_sha256 = digest.hexdigest()
        os.fchmod(snapshot_descriptor, int(mode_text, 8))
        seals = required_seals()
        fcntl.fcntl(snapshot_descriptor, fcntl.F_ADD_SEALS, seals)
        metadata, actual_sha256, actual_seals = verify_descriptor(
            snapshot_descriptor, expected_sha256
        )
        print(
            "READY\t{}\t{}\t{}\t{}".format(
                snapshot_descriptor,
                actual_sha256,
                metadata.st_size,
                actual_seals,
            ),
            flush=True,
        )
        if os.read(0, 1) != b"A":
            fail("snapshot was not adopted by the parent")
    finally:
        os.close(source_descriptor)
        if snapshot_descriptor >= 0:
            os.close(snapshot_descriptor)


def verify_snapshot(path, expected_sha256):
    if not valid_sha256(expected_sha256):
        fail("expected SHA256 is invalid")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        verify_descriptor(descriptor, expected_sha256)
    finally:
        os.close(descriptor)


def main(argv):
    if not argv or argv[0] not in ("create", "create-payload", "verify"):
        fail("usage: create|create-payload|verify ...")
    if argv[0] == "create":
        if len(argv) != 4:
            fail("create requires PATH EXPECTED_SHA256 MODE")
        create_snapshot(argv[1], argv[2], argv[3])
    elif argv[0] == "create-payload":
        if len(argv) != 3:
            fail("create-payload requires FD_PATH MODE")
        create_payload_snapshot(argv[1], argv[2])
    else:
        if len(argv) != 3:
            fail("verify requires PATH EXPECTED_SHA256")
        verify_snapshot(argv[1], argv[2])


try:
    main(sys.argv[1:])
except (OSError, RuntimeError) as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
PY
}

cleanup_sealed_snapshot_transport() {
  local control_fifo status_fifo
  if [ "$SEALED_SNAPSHOT_STATUS_OPEN" = 1 ]; then
    exec 8<&- 2>/dev/null || true
    SEALED_SNAPSHOT_STATUS_OPEN=0
  fi
  if [ "$SEALED_SNAPSHOT_CONTROL_OPEN" = 1 ]; then
    printf 'A' >&7 2>/dev/null || true
    exec 7>&- 2>/dev/null || true
    SEALED_SNAPSHOT_CONTROL_OPEN=0
  fi
  if [ -n "$SEALED_SNAPSHOT_HOLDER_PID" ]; then
    wait "$SEALED_SNAPSHOT_HOLDER_PID" 2>/dev/null || true
    SEALED_SNAPSHOT_HOLDER_PID=""
  fi
  if [ -n "$SEALED_SNAPSHOT_TRANSPORT_DIR" ]; then
    control_fifo="$SEALED_SNAPSHOT_TRANSPORT_DIR/control"
    status_fifo="$SEALED_SNAPSHOT_TRANSPORT_DIR/status"
    [ ! -e "$control_fifo" ] || [ -p "$control_fifo" ] || return 1
    [ ! -e "$status_fifo" ] || [ -p "$status_fifo" ] || return 1
    [ ! -p "$control_fifo" ] || rm -f -- "$control_fifo"
    [ ! -p "$status_fifo" ] || rm -f -- "$status_fifo"
    rmdir -- "$SEALED_SNAPSHOT_TRANSPORT_DIR" 2>/dev/null || return 1
    SEALED_SNAPSHOT_TRANSPORT_DIR=""
  fi
}

close_sealed_snapshot_slot() {
  case "$1" in
    archive) exec 9<&- 2>/dev/null || true ; FIXED_TOOL_ARCHIVE_FD_PATH="" ;;
    node) exec 4<&- 2>/dev/null || true ; BUILD_NODE_HELD_PATH="" ;;
    npm) exec 5<&- 2>/dev/null || true ; BUILD_NPM_CLI_HELD_PATH="" ;;
    inventory) exec 14<&- 2>/dev/null || true ; SOURCE_INVENTORY_FD="" ;;
    electron) exec 15<&- 2>/dev/null || true ; ELECTRON_ARCHIVE_FD="" ;;
    *) return 1 ;;
  esac
}

verify_sealed_snapshot() {
  local snapshot_path="$1" expected_sha256="$2" snapshot_python
  snapshot_python="$(sealed_snapshot_python_source)" || return 1
  /usr/bin/env -i \
    HOME="$BUILD_ROOT" TMPDIR="$BUILD_TMP_DIR" PATH=/usr/bin:/bin \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    /usr/bin/python3 -I -B -c "$snapshot_python" verify \
    "$snapshot_path" "$expected_sha256"
}

adopt_sealed_snapshot() {
  local source_path="$1" expected_sha256="$2" slot="$3"
  local snapshot_python control_fifo status_fifo ready snapshot_descriptor
  local reported_sha256 reported_size reported_seals holder_path adopted_path holder_status snapshot_mode
  cleanup_sealed_snapshot_transport || return 1
  close_sealed_snapshot_slot "$slot" || return 1
  snapshot_python="$(sealed_snapshot_python_source)" || return 1
  SEALED_SNAPSHOT_TRANSPORT_DIR="$(
    mktemp -d "$BUILD_TMP_DIR/taiji-sealed-snapshot.XXXXXX"
  )" || return 1
  [ -d "$SEALED_SNAPSHOT_TRANSPORT_DIR" ] \
    && [ ! -L "$SEALED_SNAPSHOT_TRANSPORT_DIR" ] \
    && [ "$(stat -c '%u' "$SEALED_SNAPSHOT_TRANSPORT_DIR")" = "$(id -u)" ] \
    && [ "$(stat -c '%a' "$SEALED_SNAPSHOT_TRANSPORT_DIR")" = 700 ] \
    || { cleanup_sealed_snapshot_transport || true; return 1; }
  control_fifo="$SEALED_SNAPSHOT_TRANSPORT_DIR/control"
  status_fifo="$SEALED_SNAPSHOT_TRANSPORT_DIR/status"
  mkfifo -m 0600 -- "$control_fifo" "$status_fifo" \
    || { cleanup_sealed_snapshot_transport || true; return 1; }
  exec 7<> "$control_fifo" \
    || { cleanup_sealed_snapshot_transport || true; return 1; }
  SEALED_SNAPSHOT_CONTROL_OPEN=1
  case "$slot" in
    node) snapshot_mode=0500 ;;
    archive|npm|inventory|electron) snapshot_mode=0400 ;;
  esac
  /usr/bin/env -i \
    HOME="$BUILD_ROOT" TMPDIR="$BUILD_TMP_DIR" PATH=/usr/bin:/bin \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    /usr/bin/python3 -I -B -c "$snapshot_python" create \
    "$source_path" "$expected_sha256" "$snapshot_mode" <&7 > "$status_fifo" &
  SEALED_SNAPSHOT_HOLDER_PID="$!"
  exec 8< "$status_fifo" \
    || { cleanup_sealed_snapshot_transport || true; return 1; }
  SEALED_SNAPSHOT_STATUS_OPEN=1
  if ! IFS=$'\t' read -r ready snapshot_descriptor reported_sha256 reported_size reported_seals <&8; then
    cleanup_sealed_snapshot_transport || true
    return 1
  fi
  exec 8<&-
  SEALED_SNAPSHOT_STATUS_OPEN=0
  [ "$ready" = READY ] \
    && printf '%s\n' "$snapshot_descriptor" | grep -Eq '^[0-9]+$' \
    && [ "$reported_sha256" = "$expected_sha256" ] \
    && printf '%s\n' "$reported_size" | grep -Eq '^[1-9][0-9]*$' \
    && printf '%s\n' "$reported_seals" | grep -Eq '^[0-9]+$' \
    || { cleanup_sealed_snapshot_transport || true; return 1; }
  holder_path="/proc/$SEALED_SNAPSHOT_HOLDER_PID/fd/$snapshot_descriptor"
  case "$slot" in
    archive)
      exec 9< "$holder_path" || { cleanup_sealed_snapshot_transport || true; return 1; }
      adopted_path="/proc/self/fd/9"
      ;;
    node)
      exec 4< "$holder_path" || { cleanup_sealed_snapshot_transport || true; return 1; }
      adopted_path="/proc/$$/fd/4"
      ;;
    npm)
      exec 5< "$holder_path" || { cleanup_sealed_snapshot_transport || true; return 1; }
      adopted_path="/proc/$$/fd/5"
      ;;
    inventory)
      exec 14< "$holder_path" || { cleanup_sealed_snapshot_transport || true; return 1; }
      adopted_path="/proc/$$/fd/14"
      ;;
    electron)
      exec 15< "$holder_path" || { cleanup_sealed_snapshot_transport || true; return 1; }
      adopted_path="/proc/$$/fd/15"
      ;;
  esac
  if ! verify_sealed_snapshot "$adopted_path" "$expected_sha256"; then
    close_sealed_snapshot_slot "$slot" || true
    cleanup_sealed_snapshot_transport || true
    return 1
  fi
  printf 'A' >&7 || {
    close_sealed_snapshot_slot "$slot" || true
    cleanup_sealed_snapshot_transport || true
    return 1
  }
  exec 7>&-
  SEALED_SNAPSHOT_CONTROL_OPEN=0
  holder_status=0
  wait "$SEALED_SNAPSHOT_HOLDER_PID" || holder_status="$?"
  SEALED_SNAPSHOT_HOLDER_PID=""
  cleanup_sealed_snapshot_transport || holder_status=1
  [ "$holder_status" = 0 ] || {
    close_sealed_snapshot_slot "$slot" || true
    return 1
  }
  case "$slot" in
    archive) FIXED_TOOL_ARCHIVE_FD_PATH="$adopted_path" ;;
    node) BUILD_NODE_HELD_PATH="$adopted_path" ;;
    npm) BUILD_NPM_CLI_HELD_PATH="$adopted_path" ;;
    inventory) SOURCE_INVENTORY_FD=14 ;;
    electron) ELECTRON_ARCHIVE_FD=15 ;;
  esac
}

close_fixed_tool_archive() {
  close_sealed_snapshot_slot archive
}

retain_fixed_tool_archive_snapshot() {
  local tool="$1" expected_sha256="$2" retained_path
  [ -n "$FIXED_TOOL_ARCHIVE_FD_PATH" ] || return 1
  case "$tool" in
    uv)
      exec 10< "$FIXED_TOOL_ARCHIVE_FD_PATH" || return 1
      retained_path="/proc/$$/fd/10"
      ;;
    python)
      exec 11< "$FIXED_TOOL_ARCHIVE_FD_PATH" || return 1
      retained_path="/proc/$$/fd/11"
      ;;
    node)
      exec 12< "$FIXED_TOOL_ARCHIVE_FD_PATH" || return 1
      retained_path="/proc/$$/fd/12"
      ;;
    *) return 1 ;;
  esac
  if ! verify_sealed_snapshot "$retained_path" "$expected_sha256"; then
    case "$tool" in
      uv) exec 10<&- ;;
      python) exec 11<&- ;;
      node) exec 12<&- ;;
    esac
    return 1
  fi
  close_fixed_tool_archive
  case "$tool" in
    uv) UV_ARCHIVE_PATH="$retained_path" ;;
    python) PYTHON_ARCHIVE_PATH="$retained_path" ;;
    node) NODE_ARCHIVE_PATH="$retained_path" ;;
  esac
}

close_retained_tool_archive_snapshots() {
  exec 10<&- 2>/dev/null || true
  exec 11<&- 2>/dev/null || true
  exec 12<&- 2>/dev/null || true
}

retain_source_archive_snapshot() {
  local retained_path
  [ -n "$FIXED_TOOL_ARCHIVE_FD_PATH" ] || return 1
  exec 13< "$FIXED_TOOL_ARCHIVE_FD_PATH" || return 1
  retained_path="/proc/$$/fd/13"
  verify_sealed_snapshot "$retained_path" "$SOURCE_ARCHIVE_SHA256" \
    || { exec 13<&-; return 1; }
  close_fixed_tool_archive
  SOURCE_ARCHIVE_SNAPSHOT_PATH="$retained_path"
}

close_retained_formal_archive_snapshots() {
  close_retained_tool_archive_snapshots
  exec 13<&- 2>/dev/null || true
  exec 14<&- 2>/dev/null || true
  exec 15<&- 2>/dev/null || true
  SOURCE_ARCHIVE_FD=""
  SOURCE_INVENTORY_FD=""
  ELECTRON_ARCHIVE_FD=""
  SOURCE_ARCHIVE_SNAPSHOT_PATH=""
}

open_fixed_tool_archive() {
  local archive_path="$1" expected_sha256="$2" snapshot_python
  snapshot_python="$(sealed_snapshot_python_source)" || return 1
  adopt_sealed_snapshot "$archive_path" "$expected_sha256" archive || return 1
  FIXED_TOOL_ARCHIVE_FD_PATH="/proc/self/fd/9"
}

require_open_fixed_tool_archive_unchanged() {
  local expected_sha256="$1"
  [ -n "$FIXED_TOOL_ARCHIVE_FD_PATH" ] && [ -r "$FIXED_TOOL_ARCHIVE_FD_PATH" ] \
    || fail "固定工具归档的 sealed memfd 快照丢失"
  verify_sealed_snapshot "$FIXED_TOOL_ARCHIVE_FD_PATH" "$expected_sha256" \
    || fail "固定工具归档不再是预期的 sealed memfd 快照"
}

source_lab_dir() {
  printf '%s/%s%s%s\n' "$SRC_DIR" "her" "mes-local-" "lab"
}

source_agent_dir() {
  printf '%s/sources/%s%s%s\n' "$(source_lab_dir)" "her" "mes-" "agent"
}

ensure_uv() {
  local download_dir extract_dir extracted_bin current_uid prestaged_archive prestaged_mode
  current_uid="$(id -u)"
  download_dir="$UV_ROOT/download"
  extract_dir="$UV_ROOT/extract"
  UV_ARCHIVE_PATH="$download_dir/$UV_ARCHIVE"
  prestaged_archive="$SCRIPT_DIR/../$UV_ARCHIVE"
  [ "$UV_ROOT" = "$TOOL_ROOT/uv" ] || fail "uv 工具根未绑定受控 owner-only 工具根"
  install -d -m 0700 "$UV_ROOT" "$download_dir" "$extract_dir" "$UV_ROOT/current"
  [ "$(stat -c '%u' "$UV_ROOT")" = "$current_uid" ] \
    && [ "$(stat -c '%a' "$UV_ROOT")" = 700 ] \
    || fail "uv 工具根必须由当前用户以 0700 独占"

  if [ -e "$prestaged_archive" ] || [ -L "$prestaged_archive" ]; then
    [ -f "$prestaged_archive" ] && [ ! -L "$prestaged_archive" ] \
      && [ "$(stat -c '%h' "$prestaged_archive")" = 1 ] \
      && [ "$(stat -c '%u' "$prestaged_archive")" = "$current_uid" ] \
      || fail "预置 uv 归档不是当前用户独占的普通文件"
    prestaged_mode="$(stat -c '%a' "$prestaged_archive")"
    [ $((8#$prestaged_mode & 8#022)) -eq 0 ] \
      || fail "预置 uv 归档不允许 group/other 写入"
    info "使用已预置的固定版 uv ${UV_VERSION} Linux x86_64 GNU 归档"
    install -m 0600 -- "$prestaged_archive" "$UV_ARCHIVE_PATH"
  else
    info "下载固定版 uv ${UV_VERSION} Linux x86_64 GNU 归档"
    curl_download "$UV_ARCHIVE_URL" "$UV_ARCHIVE_PATH"
  fi
  [ -f "$UV_ARCHIVE_PATH" ] && [ ! -L "$UV_ARCHIVE_PATH" ] \
    && [ "$(stat -c '%h' "$UV_ARCHIVE_PATH")" = 1 ] \
    && [ "$(stat -c '%u' "$UV_ARCHIVE_PATH")" = "$current_uid" ] \
    || fail "uv 归档不是当前用户独占的普通文件"
  open_fixed_tool_archive "$UV_ARCHIVE_PATH" "$UV_ARCHIVE_SHA256" \
    || fail "uv 固定归档不安全或 SHA256 校验失败"
  python3 - "$FIXED_TOOL_ARCHIVE_FD_PATH" <<'PY' || fail "uv 归档成员路径不安全"
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive.getmembers():
        parts = member.name.split("/")
        if member.name.startswith("/") or ".." in parts or member.issym() or member.islnk():
            raise SystemExit("unsafe uv archive member")
PY
  tar --no-same-owner --no-same-permissions -xzf "$FIXED_TOOL_ARCHIVE_FD_PATH" -C "$extract_dir"
  require_open_fixed_tool_archive_unchanged "$UV_ARCHIVE_SHA256"
  retain_fixed_tool_archive_snapshot uv "$UV_ARCHIVE_SHA256" \
    || fail "无法保留 uv sealed memfd 归档快照"
  extracted_bin="$extract_dir/uv-x86_64-unknown-linux-gnu/uv"
  [ -f "$extracted_bin" ] && [ ! -L "$extracted_bin" ] \
    && [ "$(stat -c '%h' "$extracted_bin")" = 1 ] \
    || fail "uv 固定归档解压后缺少安全的 uv 可执行文件"
  install -m 0700 "$extracted_bin" "$UV_BIN"
  [ -f "$UV_BIN" ] && [ ! -L "$UV_BIN" ] \
    && [ "$(stat -c '%h' "$UV_BIN")" = 1 ] \
    && [ "$(stat -c '%u' "$UV_BIN")" = "$current_uid" ] \
    || fail "uv 可执行文件不是当前用户独占的普通文件"
  UV_EXECUTABLE_SHA256="$(sha256sum "$UV_BIN" | awk '{print $1}')"
  [ "$UV_EXECUTABLE_SHA256" = "$UV_PINNED_EXECUTABLE_SHA256" ] \
    || fail "uv 可执行文件 SHA256 不等于官方固定归档身份"
  [ "$("$UV_BIN" --version)" = "uv $UV_VERSION (x86_64-unknown-linux-gnu)" ] \
    || fail "uv 可执行文件版本不等于固定版本 $UV_VERSION"
  file "$UV_BIN" | grep -Eq 'ELF 64-bit.*(x86-64|X86-64|80386)' \
    || fail "uv 可执行文件不是 Linux x86_64 ELF"
  ok "固定 uv 已验证：$UV_BIN (uv $UV_VERSION)"
}

ensure_python() {
  local download_dir extract_dir actual_executable_sha current_uid prestaged_archive prestaged_mode
  local python_build_marker marker_mode
  current_uid="$(id -u)"
  download_dir="$PYTHON_ROOT/download"
  extract_dir="$PYTHON_ROOT/extract"
  PYTHON_ARCHIVE_PATH="$download_dir/$PYTHON_ARCHIVE"
  prestaged_archive="$SCRIPT_DIR/../$PYTHON_ARCHIVE"
  [ "$PYTHON_ROOT" = "$TOOL_ROOT/python" ] \
    || fail "Python 工具根未绑定受控 owner-only 工具根"
  [ "$PYTHON_ARCHIVE" = \
      "cpython-${PYTHON_VERSION_PINNED}+${PYTHON_BUILD_ID}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz" ] \
    || fail "Python 固定归档名称与 uv managed build 身份不一致"
  install -d -m 0700 "$PYTHON_ROOT" "$download_dir"
  [ "$(stat -c '%u' "$PYTHON_ROOT")" = "$current_uid" ] \
    && [ "$(stat -c '%a' "$PYTHON_ROOT")" = 700 ] \
    || fail "Python 工具根必须由当前用户以 0700 独占"

  if [ -e "$prestaged_archive" ] || [ -L "$prestaged_archive" ]; then
    [ -f "$prestaged_archive" ] && [ ! -L "$prestaged_archive" ] \
      && [ "$(stat -c '%h' "$prestaged_archive")" = 1 ] \
      && [ "$(stat -c '%u' "$prestaged_archive")" = "$current_uid" ] \
      || fail "预置 Python 归档不是当前用户独占的普通文件"
    prestaged_mode="$(stat -c '%a' "$prestaged_archive")"
    [ $((8#$prestaged_mode & 8#022)) -eq 0 ] \
      || fail "预置 Python 归档不允许 group/other 写入"
    info "使用已预置的固定版 CPython ${PYTHON_VERSION_PINNED} Linux x86_64 GNU 归档"
    install -m 0600 -- "$prestaged_archive" "$PYTHON_ARCHIVE_PATH"
  else
    info "下载固定版 CPython ${PYTHON_VERSION_PINNED} Linux x86_64 GNU 归档"
    curl_download "$PYTHON_ARCHIVE_URL" "$PYTHON_ARCHIVE_PATH"
  fi
  [ -f "$PYTHON_ARCHIVE_PATH" ] && [ ! -L "$PYTHON_ARCHIVE_PATH" ] \
    && [ "$(stat -c '%h' "$PYTHON_ARCHIVE_PATH")" = 1 ] \
    && [ "$(stat -c '%u' "$PYTHON_ARCHIVE_PATH")" = "$current_uid" ] \
    || fail "Python 归档不是当前用户独占的普通文件"
  open_fixed_tool_archive "$PYTHON_ARCHIVE_PATH" "$PYTHON_ARCHIVE_SHA256" \
    || fail "Python 固定归档不安全或 SHA256 校验失败"
  python3 - "$FIXED_TOOL_ARCHIVE_FD_PATH" <<'PY' || fail "Python 归档成员或链接路径不安全"
import posixpath
import sys
import tarfile

seen = set()
with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive:
        raw = member.name.rstrip("/")
        parts = raw.split("/")
        if (
            not raw
            or raw.startswith("/")
            or "\\" in raw
            or any(part in ("", ".", "..") for part in parts)
            or parts[0] != "python"
            or raw in seen
        ):
            raise SystemExit("unsafe member")
        seen.add(raw)
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise SystemExit("unsupported member type")
        if member.issym():
            target = posixpath.normpath(posixpath.join(posixpath.dirname(raw), member.linkname))
        elif member.islnk():
            target = posixpath.normpath(member.linkname)
        else:
            continue
        if target == "python" or not target.startswith("python/"):
            raise SystemExit("escaping link")
PY
  rm -rf -- "$extract_dir"
  install -d -m 0700 "$extract_dir"
  tar --no-same-owner --no-same-permissions -xzf "$FIXED_TOOL_ARCHIVE_FD_PATH" -C "$extract_dir"
  require_open_fixed_tool_archive_unchanged "$PYTHON_ARCHIVE_SHA256"
  retain_fixed_tool_archive_snapshot python "$PYTHON_ARCHIVE_SHA256" \
    || fail "无法保留 Python sealed memfd 归档快照"
  [ -f "$extract_dir/python/bin/python3.11" ] \
    && [ ! -L "$extract_dir/python/bin/python3.11" ] \
    && [ "$(stat -c '%h' "$extract_dir/python/bin/python3.11")" = 1 ] \
    || fail "Python 固定归档解压后缺少安全的 python3.11 可执行文件"
  ln -sfn "$extract_dir/python" "$PYTHON_ROOT/current"
  [ -x "$PYTHON_BIN" ] && [ ! -L "$PYTHON_BIN" ] \
    || fail "固定 Python 可执行文件不可用"
  actual_executable_sha="$(sha256sum "$PYTHON_BIN" | awk '{print $1}')"
  [ "$actual_executable_sha" = "$PYTHON_PINNED_EXECUTABLE_SHA256" ] \
    || fail "Python 可执行文件 SHA256 不等于官方固定归档身份"
  [ "$("$PYTHON_BIN" -c 'import platform; print(platform.python_version())')" = "$PYTHON_VERSION_PINNED" ] \
    || fail "Python 可执行文件版本不等于固定版本 $PYTHON_VERSION_PINNED"
  file "$PYTHON_BIN" | grep -Eq 'ELF 64-bit.*(x86-64|X86-64|80386)' \
    || fail "Python 可执行文件不是 Linux x86_64 ELF"
  python_build_marker="$extract_dir/python/BUILD"
  [ ! -e "$python_build_marker" ] && [ ! -L "$python_build_marker" ] \
    || fail "Python 固定归档解压根已存在非预期 BUILD 元数据"
  (
    umask 077
    set -o noclobber
    printf '%s\n' "$PYTHON_BUILD_ID" > "$python_build_marker"
  ) || fail "无法写入固定 Python uv managed BUILD 元数据"
  [ -f "$python_build_marker" ] && [ ! -L "$python_build_marker" ] \
    && [ "$(stat -c '%h' "$python_build_marker")" = 1 ] \
    && [ "$(stat -c '%u' "$python_build_marker")" = "$current_uid" ] \
    || fail "固定 Python BUILD 元数据不是当前用户独占的普通文件"
  marker_mode="$(stat -c '%a' "$python_build_marker")"
  [ "$marker_mode" = 600 ] \
    || fail "固定 Python BUILD 元数据权限不是 0600"
  [ "$(<"$python_build_marker")" = "$PYTHON_BUILD_ID" ] \
    || fail "固定 Python BUILD 元数据与归档 build 身份不一致"
  ok "固定 CPython 已验证：$PYTHON_BIN ($PYTHON_VERSION_PINNED)"
}

validate_formal_uv_contract() {
  case "${TAIJI_UV_LOCK_MODE-}" in
    ""|strict) ;;
    auto|unlocked) fail "正式制包拒绝 TAIJI_UV_LOCK_MODE=${TAIJI_UV_LOCK_MODE}；只允许 unset/strict" ;;
    *) fail "正式制包只接受 unset/strict 的 TAIJI_UV_LOCK_MODE" ;;
  esac
  if [ "${TAIJI_ALLOW_UV_LOCK_REFRESH+x}" = x ]; then
    fail "正式制包拒绝 TAIJI_ALLOW_UV_LOCK_REFRESH；禁止在制包现场刷新 lock"
  fi
}

curl_download() {
  local url="$1" output="$2"
  curl -fsSL --connect-timeout 15 --retry 2 --retry-delay 2 "$url" -o "$output"
}

node_mirrors() {
  if [ -n "${TAIJI_NODE_MIRRORS:-}" ]; then
    printf '%s\n' $TAIJI_NODE_MIRRORS
    return
  fi
  if [ -n "${NODE_MIRROR:-}" ]; then
    printf '%s\n' "$NODE_MIRROR"
  fi
  printf '%s\n' \
    "https://npmmirror.com/mirrors/node" \
    "https://mirrors.tuna.tsinghua.edu.cn/nodejs-release" \
    "https://mirrors.aliyun.com/nodejs-release" \
    "https://nodejs.org/dist" \
    | awk 'NF && !seen[$0]++'
}

npm_registries() {
  if [ -n "${TAIJI_NPM_REGISTRIES:-}" ]; then
    printf '%s\n' $TAIJI_NPM_REGISTRIES
    return
  fi
  if [ -n "${NPM_CONFIG_REGISTRY:-}" ]; then
    printf '%s\n' "$NPM_CONFIG_REGISTRY"
  fi
  printf '%s\n' \
    "https://registry.npmmirror.com" \
    "https://registry.npmjs.org" \
    | awk 'NF && !seen[$0]++'
}

electron_mirrors() {
  if [ -n "${TAIJI_ELECTRON_MIRRORS:-}" ]; then
    printf '%s\n' $TAIJI_ELECTRON_MIRRORS
    return
  fi
  if [ -n "${ELECTRON_MIRROR:-}" ]; then
    printf '%s\n' "$ELECTRON_MIRROR"
  fi
  printf '%s\n' \
    "https://npmmirror.com/mirrors/electron/" \
    "https://github.com/electron/electron/releases/download/" \
    | awk 'NF && !seen[$0]++'
}

portable_node_files_are_exact() {
  local root="$NODE_ROOT/current"
  [ -x "$root/bin/node" ] || return 1
  [ -x "$root/bin/npm" ] || return 1
  [ -f "$root/.taiji-node-version" ] || return 1
  [ -f "$root/.taiji-node-archive-sha256" ] || return 1
  [ "$(tr -d '\r\n' < "$root/.taiji-node-version")" = "$NODE_VERSION" ] || return 1
  [ "$(tr -d '\r\n' < "$root/.taiji-node-archive-sha256")" = "$NODE_ARCHIVE_SHA256" ] || return 1
  [ "$(sha256sum "$root/bin/node" | awk '{print $1}')" = "$NODE_PINNED_EXECUTABLE_SHA256" ] || return 1
  [ -n "$NODE_NPM_CLI_ARCHIVE_SHA256" ] \
    && [ -n "$NODE_NPM_VERSION_ARCHIVE" ] \
    || return 1
  [ "$(sha256sum "$root/lib/node_modules/npm/bin/npm-cli.js" | awk '{print $1}')" \
      = "$NODE_NPM_CLI_ARCHIVE_SHA256" ] \
    || return 1
  file "$root/bin/node" | grep -Eq 'ELF 64-bit.*(x86-64|X86-64|80386)' || return 1
}

prepare_build_npm_configs() {
  local config current_uid
  BUILD_NPM_USERCONFIG="$BUILD_TMP_DIR/npm-userconfig"
  BUILD_NPM_GLOBALCONFIG="$BUILD_TMP_DIR/npm-globalconfig"
  [ "$BUILD_NPM_USERCONFIG" != "$BUILD_NPM_GLOBALCONFIG" ] \
    || fail "npm user/global 配置路径必须不同"
  for config in "$BUILD_NPM_USERCONFIG" "$BUILD_NPM_GLOBALCONFIG"; do
    [ ! -e "$config" ] && [ ! -L "$config" ] \
      || fail "npm 空配置路径已被占用：$config"
  done
  (
    umask 077
    set -o noclobber
    : > "$BUILD_NPM_USERCONFIG"
    : > "$BUILD_NPM_GLOBALCONFIG"
  ) || fail "无法创建 npm 隔离空配置"
  current_uid="$(id -u)"
  for config in "$BUILD_NPM_USERCONFIG" "$BUILD_NPM_GLOBALCONFIG"; do
    [ -f "$config" ] && [ ! -L "$config" ] \
      && [ "$(stat -c '%h' "$config")" = 1 ] \
      && [ "$(stat -c '%u' "$config")" = "$current_uid" ] \
      && [ "$(stat -c '%a' "$config")" = 600 ] \
      && [ "$(stat -c '%s' "$config")" = 0 ] \
      || fail "npm 隔离空配置身份无效：$config"
  done
}

run_build_node_script() {
  local held_script="$1" canonical_script="$2"
  shift 2
  [ "$BUILD_NODE_RUNTIME_SEALED" = 1 ] || return 1
  NODE_OPTIONS= NODE_PATH= \
  NPM_CONFIG_USERCONFIG="$BUILD_NPM_USERCONFIG" \
  NPM_CONFIG_GLOBALCONFIG="$BUILD_NPM_GLOBALCONFIG" \
  npm_node_execpath="$BUILD_NODE_HELD_PATH" \
  npm_execpath="$BUILD_NPM_CLI_HELD_PATH" \
    "$BUILD_NODE_HELD_PATH" -e '
const fs = require("fs");
const Module = require("module");
const path = require("path");
const heldPath = process.argv[1];
const canonicalPath = process.argv[2];
const args = process.argv.slice(3);
let source = fs.readFileSync(heldPath, "utf8");
source = source.replace(/^#![^\n]*(?:\n|$)/, "\n");
process.argv = [process.execPath, canonicalPath, ...args];
const scriptModule = new Module(canonicalPath, module);
scriptModule.filename = canonicalPath;
scriptModule.paths = Module._nodeModulePaths(path.dirname(canonicalPath));
scriptModule._compile(source, canonicalPath);
' "$held_script" "$canonical_script" "$@"
}

run_build_node() {
  [ "$BUILD_NODE_RUNTIME_SEALED" = 1 ] || return 1
  NODE_OPTIONS= NODE_PATH= "$BUILD_NODE_HELD_PATH" "$@"
}

run_build_npm() {
  run_build_node_script \
    "$BUILD_NPM_CLI_HELD_PATH" "$BUILD_NPM_CLI_PATH" "$@"
}

portable_node_is_exact() {
  portable_node_files_are_exact || return 1
  [ "$BUILD_NODE_RUNTIME_SEALED" = 1 ] || return 1
  verify_sealed_snapshot "$BUILD_NODE_HELD_PATH" "$NODE_PINNED_EXECUTABLE_SHA256" \
    || return 1
  verify_sealed_snapshot "$BUILD_NPM_CLI_HELD_PATH" "$NODE_NPM_CLI_ARCHIVE_SHA256" \
    || return 1
  [ "$(run_build_node --version 2>/dev/null)" = "v$NODE_VERSION" ] || return 1
  [ "$(run_build_npm --version 2>/dev/null)" = "$NODE_NPM_VERSION_ARCHIVE" ] \
    || return 1
}

seal_build_node_runtime() {
  local current_uid
  current_uid="$(id -u)"
  BUILD_NODE_PATH="$(readlink -f "$NODE_ROOT/current/bin/node")" \
    || fail "无法解析候选构建 Node 实体"
  BUILD_NPM_CLI_PATH="$(readlink -f \
    "$NODE_ROOT/current/lib/node_modules/npm/bin/npm-cli.js")" \
    || fail "无法解析候选构建 npm-cli.js 实体"
  for path in "$BUILD_NODE_PATH" "$BUILD_NPM_CLI_PATH"; do
    [ -f "$path" ] && [ ! -L "$path" ] \
      && [ "$(stat -c '%h' "$path")" = 1 ] \
      && [ "$(stat -c '%u' "$path")" = "$current_uid" ] \
      || fail "候选构建 Node/npm 叶节点不是当前用户的单链接普通文件：$path"
  done
  portable_node_files_are_exact \
    || fail "候选构建 Node/npm 文件身份未通过被动校验"
  prepare_build_npm_configs
  adopt_sealed_snapshot \
    "$BUILD_NODE_PATH" "$NODE_PINNED_EXECUTABLE_SHA256" node \
    || fail "无法固定候选构建 Node sealed memfd"
  adopt_sealed_snapshot \
    "$BUILD_NPM_CLI_PATH" "$NODE_NPM_CLI_ARCHIVE_SHA256" npm \
    || fail "无法固定候选构建 npm-cli.js sealed memfd"
  BUILD_NODE_RUNTIME_SEALED=1
  portable_node_is_exact \
    || fail "候选构建 Node/npm sealed memfd 版本或身份无效"
  ok "候选构建 Node/npm 叶节点已固定为 sealed memfd"
}

close_build_node_runtime_fds() {
  exec 4<&- 2>/dev/null || true
  exec 5<&- 2>/dev/null || true
  BUILD_NODE_HELD_PATH=""
  BUILD_NPM_CLI_HELD_PATH=""
  BUILD_NODE_RUNTIME_SEALED=0
}

install_portable_node() {
  mkdir -p "$NODE_ROOT"
  NODE_ARCHIVE_PATH="$NODE_ROOT/download/$NODE_ARCHIVE"
  if portable_node_files_are_exact; then
    export PATH="$NODE_ROOT/current/bin:/usr/bin:/bin"
    return 0
  fi

  local mirror release_dir tmp_dir tarball downloaded extracted_root npm_archive_identity
  release_dir="v${NODE_VERSION}"
  tmp_dir="$NODE_ROOT/download"
  tarball="$NODE_ARCHIVE"
  downloaded=0
  rm -rf "$tmp_dir"
  mkdir -p "$tmp_dir"

  info "准备固定版 Node.js ${NODE_VERSION} Linux x64 离线运行时"
  for mirror in $(node_mirrors); do
    mirror="${mirror%/}"
    [ -n "$mirror" ] || continue
    rm -f "$tmp_dir/$tarball"
    info "尝试 Node.js 镜像：$mirror"
    if ! curl_download "$mirror/$release_dir/$tarball" "$tmp_dir/$tarball"; then
      warn "Node.js 安装包下载失败，切换镜像：$mirror"
      continue
    fi
    if ! open_fixed_tool_archive "$tmp_dir/$tarball" "$NODE_ARCHIVE_SHA256"; then
      warn "Node.js 安装包不安全或校验失败，切换镜像：$mirror"
      continue
    fi
    downloaded=1
    break
  done

  [ "$downloaded" = "1" ] || fail "无法下载 Node.js ${NODE_VERSION} Linux x64 离线运行时，或下载内容校验失败；请检查制包机 DNS/代理，或设置 TAIJI_NODE_MIRRORS"
  python3 - "$FIXED_TOOL_ARCHIVE_FD_PATH" "node-v${NODE_VERSION}-linux-x64" <<'PY' \
    || fail "Node.js 归档成员或链接路径不安全"
import posixpath
import sys
import tarfile

expected_root = sys.argv[2]
seen = set()
with tarfile.open(sys.argv[1], "r:xz") as archive:
    for member in archive:
        raw = member.name.rstrip("/")
        parts = raw.split("/")
        if (
            not raw
            or raw.startswith("/")
            or "\\" in raw
            or any(part in ("", ".", "..") for part in parts)
            or parts[0] != expected_root
            or raw in seen
        ):
            raise SystemExit("unsafe member")
        seen.add(raw)
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise SystemExit("unsupported member type")
        if member.issym():
            target = posixpath.normpath(posixpath.join(posixpath.dirname(raw), member.linkname))
        elif member.islnk():
            target = posixpath.normpath(member.linkname)
        else:
            continue
        if target != expected_root and not target.startswith(expected_root + "/"):
            raise SystemExit("escaping link")
PY
  npm_archive_identity="$(/usr/bin/python3 -I -B - \
    "$FIXED_TOOL_ARCHIVE_FD_PATH" "node-v${NODE_VERSION}-linux-x64" <<'PY'
import hashlib
import json
import re
import sys
import tarfile

archive_path, expected_root = sys.argv[1:]
cli_name = expected_root + "/lib/node_modules/npm/bin/npm-cli.js"
package_name = expected_root + "/lib/node_modules/npm/package.json"

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit("duplicate npm package field: " + key)
        result[key] = value
    return result

with tarfile.open(archive_path, "r:xz") as archive:
    members = archive.getmembers()
    cli_members = [member for member in members if member.name == cli_name]
    package_members = [member for member in members if member.name == package_name]
    if len(cli_members) != 1 or len(package_members) != 1:
        raise SystemExit("fixed Node archive does not contain exact npm identities")
    cli_member = cli_members[0]
    package_member = package_members[0]
    if (
        not cli_member.isfile()
        or cli_member.size <= 0
        or cli_member.size > 1024 * 1024
        or not package_member.isfile()
        or package_member.size <= 0
        or package_member.size > 1024 * 1024
    ):
        raise SystemExit("fixed Node archive npm identity members are unsafe")
    cli_stream = archive.extractfile(cli_member)
    package_stream = archive.extractfile(package_member)
    cli_payload = cli_stream.read(cli_member.size + 1) if cli_stream else b""
    package_payload = package_stream.read(package_member.size + 1) if package_stream else b""
    if len(cli_payload) != cli_member.size or len(package_payload) != package_member.size:
        raise SystemExit("fixed Node archive npm identity member changed while reading")

try:
    package = json.loads(package_payload.decode("utf-8"), object_pairs_hook=strict_object)
except (UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit("fixed Node archive npm package metadata is invalid") from exc
version = package.get("version") if isinstance(package, dict) else None
if not isinstance(version, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
    raise SystemExit("fixed Node archive npm version is invalid")
print(hashlib.sha256(cli_payload).hexdigest() + "\t" + version)
PY
)" || fail "无法从已固定 Node 归档派生 npm CLI 身份"
  IFS=$'\t' read -r NODE_NPM_CLI_ARCHIVE_SHA256 NODE_NPM_VERSION_ARCHIVE \
    <<< "$npm_archive_identity"
  printf '%s\n' "$NODE_NPM_CLI_ARCHIVE_SHA256" | grep -Eq '^[0-9a-f]{64}$' \
    || fail "已固定 Node 归档派生的 npm CLI SHA256 无效"
  printf '%s\n' "$NODE_NPM_VERSION_ARCHIVE" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' \
    || fail "已固定 Node 归档派生的 npm 版本无效"
  extracted_root="$NODE_ROOT/${tarball%.tar.xz}"
  rm -rf "$extracted_root"
  tar --no-same-owner --no-same-permissions -xJf "$FIXED_TOOL_ARCHIVE_FD_PATH" -C "$NODE_ROOT"
  require_open_fixed_tool_archive_unchanged "$NODE_ARCHIVE_SHA256"
  retain_fixed_tool_archive_snapshot node "$NODE_ARCHIVE_SHA256" \
    || fail "无法保留 Node sealed memfd 归档快照"
  [ -x "$extracted_root/bin/node" ] || fail "Node.js 离线运行时解压后缺少 bin/node"
  printf '%s\n' "$NODE_VERSION" > "$extracted_root/.taiji-node-version"
  printf '%s\n' "$NODE_ARCHIVE_SHA256" > "$extracted_root/.taiji-node-archive-sha256"
  ln -sfn "$extracted_root" "$NODE_ROOT/current"
  export PATH="$NODE_ROOT/current/bin:/usr/bin:/bin"
  portable_node_files_are_exact \
    || fail "Node.js ${NODE_VERSION} Linux x64 离线运行时文件身份验证失败"
}

ensure_node() {
  export PATH="$NODE_ROOT/current/bin:/usr/bin:/bin"
  install_portable_node
  portable_node_files_are_exact \
    || fail "固定版 Node.js 离线运行时文件身份不可用，禁止回退到系统 Node"
  ok "Node.js 离线运行时文件已准备，尚未执行任何 Node/npm argv"
}

restore_owned_build_root_directory_writes() {
  find "$BUILD_ROOT" -type d -exec chmod u+w {} + \
    || fail "无法恢复受控构建工作区目录的 owner 写权限：$BUILD_ROOT"
}

reset_build_root() {
  validate_safe_build_root_path
  if [ -e "$BUILD_ROOT" ] || [ -L "$BUILD_ROOT" ]; then
    require_owned_build_root
    restore_owned_build_root_directory_writes
    rm -rf -- "$BUILD_ROOT" || fail "无法以当前用户清理专用构建工作区：$BUILD_ROOT"
    [ ! -e "$BUILD_ROOT" ] && [ ! -L "$BUILD_ROOT" ] \
      || fail "专用构建工作区清理后仍存在：$BUILD_ROOT"
  fi
  create_owned_build_root
  configure_build_tmp
}

require_build_capacity() {
  local target="${1:-$BUILD_ROOT}" available_kib available_mib available_inodes
  if ! available_kib="$(LC_ALL=C df -Pk -- "$target" 2>/dev/null | awk 'NR == 2 {print $4}')"; then
    fail "无法读取构建文件系统可用空间：$target"
  fi
  if ! available_inodes="$(LC_ALL=C df -Pi -- "$target" 2>/dev/null | awk 'NR == 2 {print $(NF-2)}')"; then
    fail "无法读取构建文件系统可用 inode：$target"
  fi
  case "$available_kib" in
    ''|*[!0-9]*) fail "无法读取构建文件系统可用空间：$target" ;;
  esac
  case "$available_inodes" in
    ''|*[!0-9]*) fail "无法读取构建文件系统可用 inode：$target" ;;
  esac
  available_mib=$((available_kib / 1024))
  BUILD_ROOT_PROBE_RESULTS+="candidate=$target stage=capacity available_mib=$available_mib available_inodes=$available_inodes"$'\n'
  [ "$available_mib" -ge "$BUILD_MIN_FREE_MIB" ] \
    || fail "构建文件系统可用空间不足：${available_mib} MiB，至少需要 ${BUILD_MIN_FREE_MIB} MiB（${target}）"
  [ "$available_inodes" -ge "$BUILD_MIN_FREE_INODES" ] \
    || fail "构建文件系统可用 inode 不足：${available_inodes}，至少需要 ${BUILD_MIN_FREE_INODES}（${target}）"
  ok "构建文件系统容量门禁通过：${available_mib} MiB，${available_inodes} inodes"
}

repair_build_tree_permissions() {
  local agent_dir lab_dir setup_script pyproject
  lab_dir="$(source_lab_dir)"
  agent_dir="$(source_agent_dir)"
  setup_script="$lab_dir/scripts/setup-local.sh"
  pyproject="$agent_dir/pyproject.toml"

  chmod -R u+rwX,go+rX "$SRC_DIR" || fail "源码解压后权限修复失败：$SRC_DIR"
  [ -f "$pyproject" ] || fail "源码解压后缺少 Python 项目文件：pyproject.toml"
  [ -r "$pyproject" ] || fail "源码权限不可读：pyproject.toml"
  [ -f "$setup_script" ] || fail "源码解压后缺少初始化脚本：scripts/setup-local.sh"
  chmod +x "$setup_script" || fail "初始化脚本不可执行：scripts/setup-local.sh"
}

unpack_source() {
  info "解压源码到构建工作区"
  info "构建工作区：$BUILD_ROOT"
  reset_build_root
  require_build_capacity "$BUILD_ROOT"
  [ -n "$SOURCE_ARCHIVE_SHA256" ] || fail "源码包固定 SHA256 未加载"
  [ -n "$SOURCE_ARCHIVE_FD" ] || fail "源码 sealed 快照 FD 尚未固定"
  verify_sealed_snapshot "/proc/$$/fd/$SOURCE_ARCHIVE_FD" "$SOURCE_ARCHIVE_SHA256" \
    || fail "源码 sealed 快照在解压前已漂移"
  tar --no-same-owner --no-same-permissions -xzf "/proc/$$/fd/$SOURCE_ARCHIVE_FD" -C "$BUILD_ROOT"
  verify_sealed_snapshot "/proc/$$/fd/$SOURCE_ARCHIVE_FD" "$SOURCE_ARCHIVE_SHA256" \
    || fail "源码 sealed 快照在解压后已漂移"
  [ -d "$SRC_DIR" ] || fail "源码解压后未找到：$SRC_DIR"
  repair_build_tree_permissions
  python3 "$SOURCE_INTEGRITY_HELPER" verify \
    --archive-fd "$SOURCE_ARCHIVE_FD" \
    --archive-basename "$SOURCE_ARCHIVE_BASENAME" \
    --inventory-fd "$SOURCE_INVENTORY_FD" \
    --root "$SRC_DIR" \
    || fail "源码解压后已偏离原始归档成员清单"
  VERSION="$(read_product_version)"
  ok "源码已解压：$SRC_DIR"
}

verify_build_source_integrity() {
  local agent_python actual_target
  local -a extra_symlink_args=()
  agent_python="$(source_agent_dir)/venv/bin/python"
  if [ -e "$agent_python" ] || [ -L "$agent_python" ]; then
    [ -L "$agent_python" ] || fail "Agent 构建 Python 必须是 uv 创建的 symlink"
    [ -n "$AGENT_PYTHON_SYMLINK_TARGET" ] \
      || fail "Agent 构建 Python symlink 的固定 raw target 未加载"
    actual_target="$(readlink "$agent_python")" \
      || fail "无法读取 Agent 构建 Python symlink"
    [ "$actual_target" = "$AGENT_PYTHON_SYMLINK_TARGET" ] \
      || fail "Agent 构建 Python symlink raw target 已漂移"
    extra_symlink_args=(
      --allow-extra-symlink
      "hermes-local-lab/sources/hermes-agent/venv/bin/python"
      "$AGENT_PYTHON_SYMLINK_TARGET"
    )
  fi
  python3 "$SOURCE_INTEGRITY_HELPER" verify \
    --archive-fd "$SOURCE_ARCHIVE_FD" \
    --archive-basename "$SOURCE_ARCHIVE_BASENAME" \
    --inventory-fd "$SOURCE_INVENTORY_FD" \
    --root "$SRC_DIR" \
    --allow-extra-prefix "hermes-local-lab/sources/hermes-agent/venv" \
    --allow-extra-prefix "hermes-local-lab/sources/hermes-webui/node_modules" \
    --allow-extra-prefix "apps/taiji-desktop/node_modules" \
    --allow-extra-prefix "hermes-local-lab/sources/docx-engine-v2/node_modules" \
    --allow-extra-prefix "runtime/package-build" \
    --allow-extra-prefix "packages/麒麟操作系统安装包" \
    "${extra_symlink_args[@]}" \
    || fail "构建源码树已偏离原始归档；拒绝继续声明原始 source commit"
}

formal_source_checkout_marker_fd_path() {
  [ -n "${FORMAL_SOURCE_CHECKOUT_MARKER_FD:-}" ] || return 1
  printf '/proc/%s/fd/%s\n' "$$" "$FORMAL_SOURCE_CHECKOUT_MARKER_FD"
}

formal_source_checkout_marker_identity_matches() {
  local result identity digest
  [ -n "${FORMAL_SOURCE_CHECKOUT_MARKER:-}" ] \
    && [ -n "${FORMAL_SOURCE_CHECKOUT_MARKER_FD:-}" ] \
    && [ -n "${FORMAL_SOURCE_CHECKOUT_MARKER_IDENTITY:-}" ] \
    && [ -n "${FORMAL_SOURCE_CHECKOUT_MARKER_SHA256:-}" ] \
    || return 1
  result="$(held_file_identity_and_sha256 \
    "$(formal_source_checkout_marker_fd_path)" \
    "$FORMAL_SOURCE_CHECKOUT_MARKER")" \
    || return 1
  IFS=$'\t' read -r identity digest <<< "$result"
  [ "$identity" = "$FORMAL_SOURCE_CHECKOUT_MARKER_IDENTITY" ] \
    && [ "$digest" = "$FORMAL_SOURCE_CHECKOUT_MARKER_SHA256" ]
}

open_formal_source_checkout_marker() {
  local current_uid previous_umask had_noclobber=0 write_fd="" result
  current_uid="$(id -u)"
  FORMAL_SOURCE_CHECKOUT_MARKER="$SRC_DIR/.git"
  [ ! -e "$FORMAL_SOURCE_CHECKOUT_MARKER" ] \
    && [ ! -L "$FORMAL_SOURCE_CHECKOUT_MARKER" ] \
    || fail "archive-derived 正式源码测试根已存在 .git，拒绝覆盖"
  previous_umask="$(umask)"
  [[ -o noclobber ]] && had_noclobber=1
  umask 077
  set -o noclobber
  if ! exec {write_fd}> "$FORMAL_SOURCE_CHECKOUT_MARKER"; then
    [ "$had_noclobber" = 1 ] || set +o noclobber
    umask "$previous_umask"
    fail "无法以 no-clobber 方式创建正式源码测试 checkout marker"
  fi
  [ "$had_noclobber" = 1 ] || set +o noclobber
  umask "$previous_umask"
  printf 'taiji-archive-derived-formal-source=%s\n' \
    "$MARKER_SOURCE_COMMIT" >&"$write_fd" \
    || fail "无法写入正式源码测试 checkout marker"
  chmod 0400 "/proc/$$/fd/$write_fd" \
    || fail "无法固定正式源码测试 checkout marker 权限"
  exec {FORMAL_SOURCE_CHECKOUT_MARKER_FD}< "/proc/$$/fd/$write_fd" \
    || fail "无法持有正式源码测试 checkout marker"
  exec {write_fd}>&-
  [ "$(stat -c '%h:%u:%a' "$FORMAL_SOURCE_CHECKOUT_MARKER")" \
      = "1:$current_uid:400" ] \
    || fail "正式源码测试 checkout marker 身份不安全"
  result="$(held_file_identity_and_sha256 \
    "$(formal_source_checkout_marker_fd_path)" \
    "$FORMAL_SOURCE_CHECKOUT_MARKER")" \
    || fail "无法固定正式源码测试 checkout marker 身份"
  IFS=$'\t' read -r FORMAL_SOURCE_CHECKOUT_MARKER_IDENTITY \
    FORMAL_SOURCE_CHECKOUT_MARKER_SHA256 <<< "$result"
  formal_source_checkout_marker_identity_matches \
    || fail "正式源码测试 checkout marker 创建后已漂移"
}

close_formal_source_checkout_marker() {
  [ -n "${FORMAL_SOURCE_CHECKOUT_MARKER:-}" ] || return 0
  if ! formal_source_checkout_marker_identity_matches; then
    if [ -n "${FORMAL_SOURCE_CHECKOUT_MARKER_FD:-}" ]; then
      exec {FORMAL_SOURCE_CHECKOUT_MARKER_FD}<&-
      FORMAL_SOURCE_CHECKOUT_MARKER_FD=""
    fi
    return 1
  fi
  exec {FORMAL_SOURCE_CHECKOUT_MARKER_FD}<&-
  FORMAL_SOURCE_CHECKOUT_MARKER_FD=""
  /usr/bin/unlink -- "$FORMAL_SOURCE_CHECKOUT_MARKER" || return 1
  [ ! -e "$FORMAL_SOURCE_CHECKOUT_MARKER" ] \
    && [ ! -L "$FORMAL_SOURCE_CHECKOUT_MARKER" ] \
    || return 1
  FORMAL_SOURCE_CHECKOUT_MARKER=""
  FORMAL_SOURCE_CHECKOUT_MARKER_IDENTITY=""
  FORMAL_SOURCE_CHECKOUT_MARKER_SHA256=""
}

formal_test_passwd_fd_path() {
  [ -n "${FORMAL_TEST_PASSWD_FD:-}" ] || return 1
  printf '/proc/%s/fd/%s\n' "$$" "$FORMAL_TEST_PASSWD_FD"
}

formal_test_passwd_identity_matches() {
  local result identity digest
  [ -n "${FORMAL_TEST_PASSWD:-}" ] \
    && [ -n "${FORMAL_TEST_PASSWD_FD:-}" ] \
    && [ -n "${FORMAL_TEST_PASSWD_IDENTITY:-}" ] \
    && [ -n "${FORMAL_TEST_PASSWD_SHA256:-}" ] \
    || return 1
  result="$(held_file_identity_and_sha256 \
    "$(formal_test_passwd_fd_path)" "$FORMAL_TEST_PASSWD")" \
    || return 1
  IFS=$'\t' read -r identity digest <<< "$result"
  [ "$identity" = "$FORMAL_TEST_PASSWD_IDENTITY" ] \
    && [ "$digest" = "$FORMAL_TEST_PASSWD_SHA256" ]
}

open_formal_test_passwd() {
  local direct_work="$1" clean_home="$2" current_uid previous_umask
  local had_noclobber=0 write_fd="" result
  current_uid="$(id -u)"
  case "$clean_home" in
    ""|*:*|*$'\n'*|*$'\r'*) fail "正式 gateway 测试隔离 HOME 路径不安全" ;;
  esac
  FORMAL_TEST_PASSWD="$direct_work/passwd"
  [ ! -e "$FORMAL_TEST_PASSWD" ] && [ ! -L "$FORMAL_TEST_PASSWD" ] \
    || fail "正式 gateway 测试 passwd 路径已存在，拒绝覆盖"
  previous_umask="$(umask)"
  [[ -o noclobber ]] && had_noclobber=1
  umask 077
  set -o noclobber
  if ! exec {write_fd}> "$FORMAL_TEST_PASSWD"; then
    [ "$had_noclobber" = 1 ] || set +o noclobber
    umask "$previous_umask"
    fail "无法以 no-clobber 方式创建正式 gateway 测试 passwd"
  fi
  [ "$had_noclobber" = 1 ] || set +o noclobber
  umask "$previous_umask"
  printf 'root:x:0:0:taiji-formal-tests:%s:/usr/sbin/nologin\n' \
    "$clean_home" >&"$write_fd" \
    || fail "无法写入正式 gateway 测试 passwd"
  chmod 0400 "/proc/$$/fd/$write_fd" \
    || fail "无法固定正式 gateway 测试 passwd 权限"
  exec {FORMAL_TEST_PASSWD_FD}< "/proc/$$/fd/$write_fd" \
    || fail "无法持有正式 gateway 测试 passwd"
  exec {write_fd}>&-
  [ "$(stat -c '%h:%u:%a' "$FORMAL_TEST_PASSWD")" \
      = "1:$current_uid:400" ] \
    || fail "正式 gateway 测试 passwd 身份不安全"
  result="$(held_file_identity_and_sha256 \
    "$(formal_test_passwd_fd_path)" "$FORMAL_TEST_PASSWD")" \
    || fail "无法固定正式 gateway 测试 passwd 身份"
  IFS=$'\t' read -r FORMAL_TEST_PASSWD_IDENTITY \
    FORMAL_TEST_PASSWD_SHA256 <<< "$result"
  formal_test_passwd_identity_matches \
    || fail "正式 gateway 测试 passwd 创建后已漂移"
}

close_formal_test_passwd() {
  [ -n "${FORMAL_TEST_PASSWD:-}" ] || return 0
  if ! formal_test_passwd_identity_matches; then
    if [ -n "${FORMAL_TEST_PASSWD_FD:-}" ]; then
      exec {FORMAL_TEST_PASSWD_FD}<&-
      FORMAL_TEST_PASSWD_FD=""
    fi
    return 1
  fi
  exec {FORMAL_TEST_PASSWD_FD}<&-
  FORMAL_TEST_PASSWD_FD=""
  /usr/bin/unlink -- "$FORMAL_TEST_PASSWD" || return 1
  [ ! -e "$FORMAL_TEST_PASSWD" ] && [ ! -L "$FORMAL_TEST_PASSWD" ] \
    || return 1
  FORMAL_TEST_PASSWD=""
  FORMAL_TEST_PASSWD_IDENTITY=""
  FORMAL_TEST_PASSWD_SHA256=""
}

load_source_controlled_policy() {
  local policy_exports
  POLICY_FILE="$SRC_DIR/packaging/linux/compatibility-policy.json"
  POLICY_HELPER="$SRC_DIR/packaging/linux/compatibility_policy.py"
  [ -f "$POLICY_FILE" ] && [ ! -L "$POLICY_FILE" ] || fail "源码包缺少 canonical compatibility policy：$POLICY_FILE"
  [ -f "$POLICY_HELPER" ] && [ ! -L "$POLICY_HELPER" ] || fail "源码包缺少 compatibility policy helper：$POLICY_HELPER"
  POLICY_ID="$(python3 "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-id)"
  POLICY_SHA256="$(python3 "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-sha256)"
  POLICY_MAINTAINER="$(python3 "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-maintainer)"
  policy_exports="$(python3 "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-shell)" \
    || fail "无法读取 canonical policy 的 Electron 归档合同"
  eval "$policy_exports"
  ELECTRON_VERSION="$TAIJI_ELECTRON_VERSION"
  ELECTRON_ARCHIVE_SHA256="$TAIJI_ELECTRON_ARCHIVE_SHA256"
  printf '%s\n' "$POLICY_SHA256" | grep -Eq '^[0-9a-f]{64}$' || fail "canonical policy SHA256 格式非法"
  printf '%s\n' "$ELECTRON_VERSION" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' \
    || fail "canonical policy Electron 版本格式非法"
  printf '%s\n' "$ELECTRON_ARCHIVE_SHA256" | grep -Eq '^[0-9a-f]{64}$' \
    || fail "canonical policy Electron 归档 SHA256 格式非法"
  ok "采用源码包内 canonical policy：$POLICY_ID ($POLICY_SHA256)"
}

locate_verified_electron_archive() {
  local candidate actual_sha expected_name matching=0
  expected_name="electron-v${ELECTRON_VERSION}-linux-x64.zip"
  ELECTRON_ARCHIVE=""
  while IFS= read -r -d '' candidate; do
    [ -f "$candidate" ] && [ ! -L "$candidate" ] \
      || fail "Electron 缓存归档不是实体普通文件：$candidate"
    [ "$(stat -c '%h' "$candidate")" = 1 ] \
      || fail "Electron 缓存归档不能是硬链接：$candidate"
    [ "$(stat -c '%u' "$candidate")" = "$(id -u)" ] \
      || fail "Electron 缓存归档不属于当前制包用户：$candidate"
    actual_sha="$(sha256sum "$candidate" | awk '{print $1}')"
    if [ "$actual_sha" = "$ELECTRON_ARCHIVE_SHA256" ]; then
      matching=$((matching + 1))
      if [ -z "$ELECTRON_ARCHIVE" ]; then
        ELECTRON_ARCHIVE="$candidate"
      fi
    fi
  done < <(find "$electron_config_cache" -type f -name "$expected_name" -print0)
  [ "$matching" -gt 0 ] \
    || fail "npm 安装后未找到 canonical policy 绑定的 Electron ${ELECTRON_VERSION} Linux x64 归档"
  ELECTRON_ARCHIVE_BASENAME="$(basename "$ELECTRON_ARCHIVE")"
  adopt_sealed_snapshot "$ELECTRON_ARCHIVE" "$ELECTRON_ARCHIVE_SHA256" electron \
    || fail "无法在 Electron 首次 staging 前固定归档 sealed 快照"
  ok "Electron 下载归档 SHA256 已绑定：$ELECTRON_ARCHIVE_SHA256"
}

npm_ci_with_network_fallback() {
  local registry electron_mirror installed
  local -a npm_args=("$@")
  installed=0

  for registry in $(npm_registries); do
    registry="${registry%/}"
    [ -n "$registry" ] || continue
    for electron_mirror in $(electron_mirrors); do
      electron_mirror="${electron_mirror%/}/"
      [ -n "$electron_mirror" ] || continue
      info "尝试 npm registry：$registry"
      info "尝试 Electron mirror：$electron_mirror"
      rm -rf node_modules
      if NPM_CONFIG_REGISTRY="$registry" ELECTRON_MIRROR="$electron_mirror" \
        run_build_npm ci "${npm_args[@]}"; then
        export NPM_CONFIG_REGISTRY="$registry"
        export ELECTRON_MIRROR="$electron_mirror"
        installed=1
        break 2
      fi
      warn "npm ci 失败，切换 npm/Electron 下载源"
    done
  done

  [ "$installed" = "1" ] || fail "npm ci 失败：已尝试多个 npm registry 和 Electron mirror；请检查制包机网络、DNS、代理，或设置 TAIJI_NPM_REGISTRIES / TAIJI_ELECTRON_MIRRORS"
}

npm_audit_fail_closed() {
  local audit_registry audit_registry_host
  audit_registry="${TAIJI_NPM_AUDIT_REGISTRY:-https://registry.npmjs.org}"
  unset TAIJI_NPM_AUDIT_REGISTRY
  audit_registry="${audit_registry%/}"
  if ! audit_registry_host="$(python3 -c '
import sys
from urllib.parse import urlsplit

raw = sys.stdin.read()
if raw.endswith("\n"):
    raw = raw[:-1]
try:
    parsed = urlsplit(raw)
    port = parsed.port
except ValueError:
    raise SystemExit(1)

if (
    not raw.isascii()
    or any(character.isspace() for character in raw)
    or any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw)
    or parsed.scheme != "https"
    or not parsed.hostname
    or parsed.username is not None
    or parsed.password is not None
    or parsed.query
    or parsed.fragment
):
    raise SystemExit(1)

host = parsed.hostname
if ":" in host:
    host = f"[{host}]"
if port is not None:
    host = f"{host}:{port}"
print(host)
 ' <<<"$audit_registry"
)"; then
    fail "TAIJI_NPM_AUDIT_REGISTRY 必须是单个 ASCII HTTPS URL，且不得包含凭据、空白、控制字符、查询参数或片段"
  fi
  info "使用 npm audit registry 主机：$audit_registry_host"
  NPM_CONFIG_PACKAGE_LOCK_ONLY=true \
    run_build_npm audit --omit=dev --audit-level=high --registry="$audit_registry" \
    || fail "DOCX Engine 生产依赖包含 high/critical 漏洞或 npm audit 不可用，拒绝生成正式安装包"
}

run_setup_local() {
  local uv_lock_mode="$1" setup_log status lock_path lock_before lock_after python_bin python_real expected_python_real python_symlink_target
  setup_log="$LOG_DIR/setup-local-$(date +%Y%m%d_%H%M%S)_$$.log"
  lock_path="$(source_agent_dir)/$PYTHON_LOCK_BASENAME"
  [ -f "$lock_path" ] && [ ! -L "$lock_path" ] || fail "正式制包缺少普通文件 uv.lock"
  lock_before="$(sha256sum "$lock_path" | awk '{print $1}')"

  set +e
  TAIJI_DEPENDENCY_PROFILE=production \
  TAIJI_UV_LOCK_MODE="$uv_lock_mode" \
  TAIJI_UV_EXECUTABLE="$UV_BIN" \
  TAIJI_PYTHON_EXECUTABLE="$PYTHON_BIN" \
  UV_LINK_MODE=copy \
  UV_NO_EDITABLE=1 \
  UV_NO_INSTALL_PROJECT=1 \
  UV_PYTHON_DOWNLOADS=never \
    /bin/bash -p ./scripts/setup-local.sh 2>&1 | tee -a "$setup_log"
  status="${PIPESTATUS[0]}"
  set -e

  if [ "$status" -ne 0 ]; then
    if grep -qiE 'pyproject\.toml|Permission denied|os error 13' "$setup_log"; then
      fail "Python venv 生成失败：构建工作区源码权限不可读（pyproject.toml Permission denied）"
    fi
    fail "Python venv 生成失败：setup-local.sh 返回 ${status}，详见 ${setup_log}"
  fi

  lock_after="$(sha256sum "$lock_path" | awk '{print $1}')"
  [ "$lock_after" = "$lock_before" ] || fail "uv.lock 在 strict sync 前后发生变化"
  PYTHON_LOCK_SHA256="$lock_after"
  PYTHON_DEPENDENCY_LOCK_STATUS="strict-locked"
  python_bin="$(source_agent_dir)/venv/bin/python"
  [ -x "$python_bin" ] && [ -L "$python_bin" ] \
    || fail "strict sync 后 Python 必须是 uv 创建的可执行 symlink"
  python_symlink_target="$(readlink "$python_bin")" \
    || fail "strict sync 后无法读取 Python symlink raw target"
  case "$python_symlink_target" in
    /*) ;;
    *) fail "strict sync 后 Python symlink raw target 必须是绝对路径" ;;
  esac
  case "$python_symlink_target" in
    *$'\n'*|*$'\r'*) fail "strict sync 后 Python symlink raw target 含换行" ;;
  esac
  python_real="$(readlink -f "$python_bin")"
  [ -f "$python_real" ] || fail "strict sync 后 Python 真实可执行文件不存在"
  expected_python_real="$(readlink -f "$PYTHON_BIN")"
  [ -n "$expected_python_real" ] && [ "$python_real" = "$expected_python_real" ] \
    || fail "strict sync 后 Python symlink 未解析到固定 CPython"
  [ "$(readlink -f "$python_symlink_target")" = "$python_real" ] \
    || fail "strict sync 后 Python symlink raw target 与解析身份不一致"
  PYTHON_VERSION="$("$python_bin" -c 'import platform; print(platform.python_version())')"
  [ "$PYTHON_VERSION" = "$PYTHON_VERSION_PINNED" ] \
    || fail "正式 Python 运行时版本不是固定版本 $PYTHON_VERSION_PINNED：$PYTHON_VERSION"
  PYTHON_EXECUTABLE_SHA256="$(sha256sum "$python_real" | awk '{print $1}')"
  [ "$PYTHON_EXECUTABLE_SHA256" = "$PYTHON_PINNED_EXECUTABLE_SHA256" ] \
    || fail "strict sync 后 Python 可执行文件 SHA256 不等于固定官方归档身份"
  readonly AGENT_PYTHON_SYMLINK_TARGET="$python_symlink_target"
}

build_runtime_and_deb() {
  local uv_lock_mode source_commit packaged_node_root
  export PATH="$NODE_ROOT/current/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  # Keep the packaging host's shell/profile from injecting a higher-priority
  # Python package source into uv. UV_NO_CONFIG blocks config files, but these
  # environment variables otherwise still override or augment UV_INDEX_URL.
  unset UV_INDEX UV_DEFAULT_INDEX UV_EXTRA_INDEX_URL UV_FIND_LINKS UV_NO_INDEX UV_INDEX_STRATEGY UV_CONFIG_FILE
  export UV_NO_CONFIG=1
  export UV_INDEX_URL="${TAIJI_UV_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
  uv_lock_mode="${TAIJI_UV_LOCK_MODE:-strict}"
  [ "$uv_lock_mode" = strict ] || fail "正式制包只允许 strict Python lock 模式"
  portable_node_is_exact \
    || fail "候选构建开始前 Node/npm sealed memfd 已漂移"

  info "生成 Linux Python venv（TAIJI_UV_LOCK_MODE=${uv_lock_mode}）"
  cd "$(source_lab_dir)"
  run_setup_local "$uv_lock_mode"
  validate_formal_test_python_identity
  NODE_EXECUTABLE_SHA256="$(sha256sum "$BUILD_NODE_HELD_PATH" | awk '{print $1}')"
  [ "$NODE_EXECUTABLE_SHA256" = "$NODE_PINNED_EXECUTABLE_SHA256" ] \
    || fail "Node.js 可执行文件 SHA256 不等于固定官方归档身份"

  info "获取 Linux Electron runtime"
  cd "$SRC_DIR/apps/taiji-desktop"
  electron_config_cache="$BUILD_ROOT/electron-cache"
  export electron_config_cache
  install -d -m 0700 "$electron_config_cache"
  run_build_npm --version
  npm_ci_with_network_fallback --omit=dev
  locate_verified_electron_archive

  info "准备 DOCX Engine V2 生产依赖并执行源码测试"
  cd "$(source_lab_dir)/sources/docx-engine-v2"
  npm_ci_with_network_fallback --omit=dev
  npm_audit_fail_closed
  run_build_node scripts/materialize-portable-resvg-dependencies.js
  run_build_npm test

  info "复核 archive-derived 源码树（打包前）"
  verify_build_source_integrity

  info "构建 DEB 安装包"
  cd "$SRC_DIR"
  packaged_node_root="$(readlink -f "$NODE_ROOT/current")" \
    || fail "无法固定 build-deb 使用的 Node 运行时实体目录"
  [ -d "$packaged_node_root" ] && [ ! -L "$packaged_node_root" ] \
    || fail "build-deb 使用的 Node 运行时实体目录不安全"
  portable_node_is_exact \
    || fail "build-deb 执行前 Node/npm sealed memfd 已漂移"
  source_commit="$(basename "$SRC_ARCHIVE" | sed -E 's/^taiji-agentv1\.0-kylin-build-src-([^.]+)\.tar\.gz$/\1/')"
  printf '%s\n' "$source_commit" | grep -Eq '^[0-9a-f]{40}$' \
    || fail "无法从源码包名称解析发布 commit：$(basename "$SRC_ARCHIVE")"
  TAIJI_AGENT_VERSION="$VERSION" \
  TAIJI_SOURCE_COMMIT="$source_commit" \
  TAIJI_SOURCE_ARCHIVE_PATH="${SOURCE_ARCHIVE_FD:+}" \
  TAIJI_SOURCE_INVENTORY_PATH="${SOURCE_INVENTORY_FD:+}" \
  TAIJI_SOURCE_ARCHIVE_FD="$SOURCE_ARCHIVE_FD" \
  TAIJI_SOURCE_ARCHIVE_BASENAME="$SOURCE_ARCHIVE_BASENAME" \
  TAIJI_SOURCE_INVENTORY_FD="$SOURCE_INVENTORY_FD" \
  TAIJI_SOURCE_INVENTORY_BASENAME="$SOURCE_INVENTORY_BASENAME" \
  TAIJI_SOURCE_INVENTORY_SHA256="$SOURCE_INVENTORY_SHA256" \
  TAIJI_PACKAGED_NODE_ROOT="$packaged_node_root" \
  TAIJI_PACKAGED_NODE_EXECUTABLE="$BUILD_NODE_HELD_PATH" \
  TAIJI_ELECTRON_ARCHIVE="${ELECTRON_ARCHIVE_FD:+}" \
  TAIJI_ELECTRON_ARCHIVE_FD="$ELECTRON_ARCHIVE_FD" \
  TAIJI_ELECTRON_ARCHIVE_BASENAME="$ELECTRON_ARCHIVE_BASENAME" \
  TAIJI_PYTHON_DEPENDENCY_LOCK_STATUS="$PYTHON_DEPENDENCY_LOCK_STATUS" \
  TAIJI_PYTHON_LOCK_BASENAME="$PYTHON_LOCK_BASENAME" \
  TAIJI_PYTHON_LOCK_SHA256="$PYTHON_LOCK_SHA256" \
  TAIJI_PYTHON_ARCHIVE_PATH="$PYTHON_ARCHIVE_PATH" \
  TAIJI_PYTHON_VERSION="$PYTHON_VERSION_PINNED" \
  TAIJI_PYTHON_ARCHIVE_SHA256="$PYTHON_ARCHIVE_SHA256" \
  TAIJI_PYTHON_EXECUTABLE="$PYTHON_BIN" \
  TAIJI_AGENT_PYTHON_SYMLINK_TARGET="$AGENT_PYTHON_SYMLINK_TARGET" \
  TAIJI_PYTHON_EXECUTABLE_SHA256="$PYTHON_PINNED_EXECUTABLE_SHA256" \
  TAIJI_UV_EXECUTABLE="$UV_BIN" \
  TAIJI_UV_ARCHIVE_PATH="$UV_ARCHIVE_PATH" \
  TAIJI_UV_VERSION="$UV_VERSION" \
  TAIJI_UV_ARCHIVE_SHA256="$UV_ARCHIVE_SHA256" \
  TAIJI_UV_EXECUTABLE_SHA256="$UV_EXECUTABLE_SHA256" \
  TAIJI_NODE_ARCHIVE_PATH="$NODE_ARCHIVE_PATH" \
  UV_NO_EDITABLE=1 \
  UV_NO_INSTALL_PROJECT=1 \
    /bin/bash -p ./packaging/linux/deb/build-deb.sh
  portable_node_is_exact \
    || fail "build-deb 返回后 Node/npm sealed memfd 已漂移"
  validate_formal_test_python_identity
  verify_build_source_integrity
}

collect_artifacts() {
  info "收集候选 DEB 与 build-deb manifest"
  local src_pkg_dir deb manifest deb_name source_name source_sha deb_sha source_commit abi_sha icon_sha electron_sha acceptance_hashes
  local source_manifest_result source_deb_fd source_deb_result source_deb_identity
  local candidate_deb_write_fd candidate_sidecar_write_fd candidate_result previous_umask had_noclobber
  src_pkg_dir="$SRC_DIR/packages/麒麟操作系统安装包"
  deb="$src_pkg_dir/taiji-agent_${VERSION}_amd64.deb"
  manifest="$src_pkg_dir/taiji-package-manifest.json"
  [ -f "$deb" ] || fail "未找到候选 DEB：$deb"
  [ -f "$deb.sha256" ] || fail "未找到候选 DEB SHA256 sidecar：$deb.sha256"
  [ -f "$manifest" ] && [ ! -L "$manifest" ] \
    && [ "$(stat -c '%h' "$manifest")" = 1 ] \
    && [ "$(stat -c '%u' "$manifest")" = "$(id -u)" ] \
    || fail "build-deb manifest 不是当前用户单链接普通文件：$manifest"
  [ ! -e "$MANIFEST_FILE" ] && [ ! -L "$MANIFEST_FILE" ] \
    || fail "正式 package manifest 发布路径在测试前已存在"
  exec {SOURCE_PACKAGE_MANIFEST_FD}< "$manifest" \
    || fail "无法固定 build-deb manifest 文件描述符"
  SOURCE_PACKAGE_MANIFEST_PATH="$manifest"
  source_manifest_result="$(held_file_identity_and_sha256 \
    "/proc/$$/fd/$SOURCE_PACKAGE_MANIFEST_FD" "$SOURCE_PACKAGE_MANIFEST_PATH")" \
    || fail "build-deb manifest 在打开或摘要固定时发生替换"
  IFS=$'\t' read -r \
    SOURCE_PACKAGE_MANIFEST_IDENTITY SOURCE_PACKAGE_MANIFEST_SHA256 \
    <<< "$source_manifest_result"
  deb_name="taiji-agent_${VERSION}_amd64.deb"
  CANDIDATE_DEB_PATH="$OUTPUT_DIR/$deb_name"
  CANDIDATE_DEB_SIDECAR_PATH="$OUTPUT_DIR/$deb_name.sha256"
  [ ! -e "$CANDIDATE_ARTIFACT_POISON" ] && [ ! -L "$CANDIDATE_ARTIFACT_POISON" ] \
    || fail "候选制品 poison 路径已被占用"
  [ -z "$(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ] \
    || { poison_candidate_artifacts "collect found a non-empty output directory"; \
      fail "上次产物归档后输出目录被并发占用，未覆盖任何文件"; }
  exec {source_deb_fd}< "$deb" \
    || fail "无法固定 build-deb 候选 DEB held FD"
  source_deb_result="$(held_file_identity_and_sha256 "/proc/$$/fd/$source_deb_fd" "$deb")" \
    || fail "build-deb 候选 DEB 身份或摘要不稳定"
  IFS=$'\t' read -r source_deb_identity deb_sha <<< "$source_deb_result"

  previous_umask="$(umask)"
  had_noclobber=0
  [[ -o noclobber ]] && had_noclobber=1
  umask 077
  set -o noclobber
  if ! exec {candidate_deb_write_fd}> "$CANDIDATE_DEB_PATH"; then
    [ "$had_noclobber" = 1 ] || set +o noclobber
    umask "$previous_umask"
    poison_candidate_artifacts "candidate DEB path was occupied"
    fail "候选 DEB 发布路径已被占用，未覆盖外来文件"
  fi
  [ "$had_noclobber" = 1 ] || set +o noclobber
  umask "$previous_umask"
  if ! /usr/bin/cat <&"$source_deb_fd" >&"$candidate_deb_write_fd"; then
    poison_candidate_artifacts "candidate DEB copy failed"
    fail "候选 DEB held-FD 复制失败"
  fi
  chmod 0644 "/proc/$$/fd/$candidate_deb_write_fd"
  fsync_held_file "/proc/$$/fd/$candidate_deb_write_fd" \
    || { poison_candidate_artifacts "candidate DEB fsync failed"; fail "候选 DEB 持久化失败"; }
  exec {CANDIDATE_DEB_FD}< "/proc/$$/fd/$candidate_deb_write_fd" \
    || fail "无法固定输出候选 DEB held read FD"
  exec {candidate_deb_write_fd}>&-
  exec {source_deb_fd}<&-
  candidate_result="$(held_file_identity_and_sha256 \
    "/proc/$$/fd/$CANDIDATE_DEB_FD" "$CANDIDATE_DEB_PATH")" \
    || { poison_candidate_artifacts "candidate DEB identity drifted after copy"; \
      fail "输出候选 DEB 身份不稳定"; }
  IFS=$'\t' read -r CANDIDATE_DEB_IDENTITY CANDIDATE_DEB_SHA256 <<< "$candidate_result"
  [ "$CANDIDATE_DEB_SHA256" = "$deb_sha" ] \
    || { poison_candidate_artifacts "candidate DEB digest differs from source"; \
      fail "输出候选 DEB 与 build-deb held FD 摘要不一致"; }

  previous_umask="$(umask)"
  had_noclobber=0
  [[ -o noclobber ]] && had_noclobber=1
  umask 077
  set -o noclobber
  if ! exec {candidate_sidecar_write_fd}> "$CANDIDATE_DEB_SIDECAR_PATH"; then
    [ "$had_noclobber" = 1 ] || set +o noclobber
    umask "$previous_umask"
    poison_candidate_artifacts "candidate sidecar path was occupied"
    fail "候选 DEB sidecar 发布路径已被占用，未覆盖外来文件"
  fi
  [ "$had_noclobber" = 1 ] || set +o noclobber
  umask "$previous_umask"
  printf '%s  %s\n' "$CANDIDATE_DEB_SHA256" "$deb_name" \
    >&"$candidate_sidecar_write_fd"
  chmod 0644 "/proc/$$/fd/$candidate_sidecar_write_fd"
  fsync_held_file "/proc/$$/fd/$candidate_sidecar_write_fd" \
    || { poison_candidate_artifacts "candidate sidecar fsync failed"; fail "候选 DEB sidecar 持久化失败"; }
  exec {CANDIDATE_DEB_SIDECAR_FD}< "/proc/$$/fd/$candidate_sidecar_write_fd" \
    || fail "无法固定候选 DEB sidecar held read FD"
  exec {candidate_sidecar_write_fd}>&-
  candidate_result="$(held_file_identity_and_sha256 \
    "/proc/$$/fd/$CANDIDATE_DEB_SIDECAR_FD" "$CANDIDATE_DEB_SIDECAR_PATH")" \
    || { poison_candidate_artifacts "candidate sidecar identity drifted"; \
      fail "候选 DEB sidecar 身份不稳定"; }
  IFS=$'\t' read -r CANDIDATE_DEB_SIDECAR_IDENTITY CANDIDATE_DEB_SIDECAR_SHA256 \
    <<< "$candidate_result"
  CANDIDATE_DEB_SIDECAR_EXPECTED_SHA256="$(
    printf '%s  %s\n' "$CANDIDATE_DEB_SHA256" "$deb_name" | sha256sum | awk '{print $1}'
  )"
  [ "$CANDIDATE_DEB_SIDECAR_SHA256" = "$CANDIDATE_DEB_SIDECAR_EXPECTED_SHA256" ] \
    || { poison_candidate_artifacts "candidate sidecar content mismatch"; \
      fail "候选 DEB sidecar 内容不是 exact basename/SHA 合同"; }
  fsync_directory "$OUTPUT_DIR" \
    || { poison_candidate_artifacts "candidate output directory fsync failed"; \
      fail "候选 DEB 输出目录持久化失败"; }
  CANDIDATE_DEB_FIXED=1
  require_candidate_deb_fixed
  source_name="$(basename "$SRC_ARCHIVE")"
  if [ -n "${SOURCE_ARCHIVE_FD:-}" ]; then
    source_sha="$(sha256sum "/proc/$$/fd/$SOURCE_ARCHIVE_FD" | awk '{print $1}')"
  else
    source_sha="$(sha256sum "$SRC_ARCHIVE" | awk '{print $1}')"
  fi
  source_commit="$(printf '%s\n' "$source_name" | sed -E 's/^taiji-agentv1\.0-kylin-build-src-([^.]+)\.tar\.gz$/\1/')"
  abi_sha="$(python3 - "/proc/$$/fd/$SOURCE_PACKAGE_MANIFEST_FD" "$deb_name" "$deb_sha" "$source_commit" "$POLICY_ID" "$POLICY_SHA256" "$POLICY_MAINTAINER" \
    "$PYTHON_DEPENDENCY_LOCK_STATUS" "$PYTHON_LOCK_BASENAME" "$PYTHON_LOCK_SHA256" "$PYTHON_VERSION" "$PYTHON_EXECUTABLE_SHA256" \
    "$UV_VERSION" "$UV_ARCHIVE_SHA256" "$UV_EXECUTABLE_SHA256" \
    "$NODE_VERSION" "$NODE_ARCHIVE_SHA256" "$NODE_EXECUTABLE_SHA256" \
    "$ELECTRON_VERSION" "$ELECTRON_ARCHIVE_SHA256" "$PYTHON_ARCHIVE_SHA256" \
    "$source_name" "$source_sha" "$(basename "$SOURCE_INVENTORY")" "$SOURCE_INVENTORY_SHA256" <<'PY'
import json
import re
import sys
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "schema": "taiji-package-manifest/v3",
    "deb_basename": sys.argv[2],
    "deb_sha256": sys.argv[3],
    "source_commit": sys.argv[4],
    "compatibility_policy_id": sys.argv[5],
    "compatibility_policy_sha256": sys.argv[6],
    "maintainer": sys.argv[7],
    "python_dependency_lock_status": sys.argv[8],
    "python_lock_basename": sys.argv[9],
    "python_lock_sha256": sys.argv[10],
    "python_version": sys.argv[11],
    "python_executable_sha256": sys.argv[12],
    "uv_version": sys.argv[13],
    "uv_archive_sha256": sys.argv[14],
    "uv_executable_sha256": sys.argv[15],
    "node_version": sys.argv[16],
    "node_archive_sha256": sys.argv[17],
    "node_executable_sha256": sys.argv[18],
    "electron_version": sys.argv[19],
    "electron_archive_sha256": sys.argv[20],
    "python_archive_sha256": sys.argv[21],
    "source_archive_basename": sys.argv[22],
    "source_archive_sha256": sys.argv[23],
    "source_inventory_basename": sys.argv[24],
    "source_inventory_sha256": sys.argv[25],
}
for key, value in expected.items():
    if manifest.get(key) != value:
        raise SystemExit("manifest binding mismatch: " + key)
abi = manifest.get("elf_abi_audit_sha256")
if not isinstance(abi, str) or not re.fullmatch(r"[0-9a-f]{64}", abi):
    raise SystemExit("manifest elf_abi_audit_sha256 is invalid")
print(abi)
PY
  )"
  ELF_ABI_AUDIT_SHA256="$abi_sha"
  acceptance_hashes="$(python3 - "/proc/$$/fd/$SOURCE_PACKAGE_MANIFEST_FD" <<'PY'
import json
import re
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
fields = (
    "acceptance_binding_sha256",
    "acceptance_tools_manifest_sha256",
    "acceptance_entrypoint_sha256",
    "installed_release_manifest_sha256",
)
values = []
for field in fields:
    value = manifest.get(field)
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SystemExit("manifest installed acceptance SHA256 is invalid: " + field)
    values.append(value)
print(" ".join(values))
PY
)" || fail "manifest 安装态验收摘要无效"
  read -r \
    ACCEPTANCE_BINDING_SHA256 \
    ACCEPTANCE_TOOLS_MANIFEST_SHA256 \
    ACCEPTANCE_ENTRYPOINT_SHA256 \
    INSTALLED_RELEASE_MANIFEST_SHA256 <<< "$acceptance_hashes"
  icon_sha="$(python3 - "/proc/$$/fd/$SOURCE_PACKAGE_MANIFEST_FD" <<'PY'
import json
import re
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
value = manifest.get("icon_set_sha256")
if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
    raise SystemExit("manifest icon_set_sha256 is invalid")
print(value)
PY
)"
  ICON_SET_SHA256="$icon_sha"
  electron_sha="$(python3 - "/proc/$$/fd/$SOURCE_PACKAGE_MANIFEST_FD" <<'PY'
import json
import re
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("electron_executable_sha256")
if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
    raise SystemExit("manifest electron_executable_sha256 is invalid")
print(value)
PY
)"
  ELECTRON_EXECUTABLE_SHA256="$electron_sha"
  [ "$ELECTRON_EXECUTABLE_SHA256" = "$ELECTRON_PINNED_EXECUTABLE_SHA256" ] \
    || fail "manifest Electron 可执行文件 SHA256 不等于 canonical policy 固定身份"
  MARKER_SOURCE_NAME="$source_name"
  MARKER_SOURCE_SHA256="$source_sha"
  MARKER_SOURCE_COMMIT="$source_commit"
  MARKER_DEB_NAME="$deb_name"
  MARKER_DEB_SHA256="$deb_sha"
  CANDIDATE_DEB_FIXED=1
  ok "候选 DEB、manifest 和 canonical policy/ABI 摘要已绑定"
}

validate_formal_test_python_identity() {
  local agent_dir agent_python expected_real actual_real current_uid path mode actual_sha actual_version
  agent_dir="$(source_agent_dir)"
  agent_python="$agent_dir/venv/bin/python"
  current_uid="$(id -u)"
  for path in \
    "$SRC_DIR" \
    "$(source_lab_dir)" \
    "$(source_lab_dir)/sources" \
    "$agent_dir" \
    "$agent_dir/venv" \
    "$agent_dir/venv/bin" \
    "$PYTHON_ROOT" \
    "$PYTHON_ROOT/extract" \
    "$PYTHON_ROOT/extract/python" \
    "$PYTHON_ROOT/extract/python/bin"; do
    [ -d "$path" ] && [ ! -L "$path" ] \
      || fail "正式测试 Python 父链不是安全真实目录：$path"
    [ "$(stat -c '%u' "$path")" = "$current_uid" ] \
      || fail "正式测试 Python 父链不属于当前制包用户：$path"
    mode="$(stat -c '%a' "$path")"
    [ $((8#$mode & 8#022)) -eq 0 ] \
      || fail "正式测试 Python 父链允许 group/other 写入：$path"
  done
  [ -x "$agent_python" ] \
    || fail "正式构建测试缺少可执行 Agent Python：$agent_python"
  actual_real="$(readlink -f "$agent_python")" \
    || fail "无法解析正式 Agent 测试 Python"
  expected_real="$(readlink -f "$PYTHON_BIN")" \
    || fail "无法解析固定 Python 运行时"
  [ "$actual_real" = "$expected_real" ] \
    || fail "Agent venv Python 未精确指向固定 Python 运行时"
  [ -f "$actual_real" ] && [ ! -L "$actual_real" ] \
    && [ "$(stat -c '%h' "$actual_real")" = 1 ] \
    && [ "$(stat -c '%u' "$actual_real")" = "$current_uid" ] \
    || fail "固定 Agent 测试 Python 目标不是当前用户单链接普通文件"
  mode="$(stat -c '%a' "$actual_real")"
  [ $((8#$mode & 8#022)) -eq 0 ] \
    || fail "固定 Agent 测试 Python 目标允许 group/other 写入"
  actual_sha="$(sha256sum "$actual_real" | awk '{print $1}')"
  [ "$actual_sha" = "$PYTHON_PINNED_EXECUTABLE_SHA256" ] \
    || fail "正式 Agent 测试 Python 已偏离固定可执行文件身份"
  actual_version="$(/usr/bin/env -i \
    HOME="$BUILD_ROOT" TMPDIR="$BUILD_TMP_DIR" PATH=/usr/bin:/bin \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
    "$agent_python" -I -B -c 'import platform; print(platform.python_version())')"
  [ "$actual_version" = "$PYTHON_VERSION_PINNED" ] \
    || fail "正式 Agent 测试 Python 版本已偏离固定版本"
}

symlink_identity_and_target() {
  /usr/bin/python3 -I -B - "$1" <<'PY'
import os
import stat
import sys

metadata = os.lstat(sys.argv[1])
if not stat.S_ISLNK(metadata.st_mode):
    raise SystemExit("path is not a symlink")
identity = (
    metadata.st_dev,
    metadata.st_ino,
    metadata.st_mode,
    metadata.st_nlink,
    metadata.st_size,
    metadata.st_uid,
    metadata.st_gid,
    metadata.st_mtime_ns,
    metadata.st_ctime_ns,
)
target = os.readlink(sys.argv[1])
if not target or "\n" in target or "\r" in target:
    raise SystemExit("symlink target is invalid")
print(":".join(str(part) for part in identity) + "\t" + target)
PY
}

seal_formal_test_node_runtime() {
  local result version
  FORMAL_NODE_CURRENT_PATH="$NODE_ROOT/current"
  result="$(symlink_identity_and_target "$FORMAL_NODE_CURRENT_PATH")" \
    || fail "无法固定正式测试 Node current 符号链接身份"
  IFS=$'\t' read -r FORMAL_NODE_CURRENT_IDENTITY FORMAL_NODE_CURRENT_RAW_TARGET \
    <<< "$result"
  FORMAL_NODE_PATH="$(readlink -f "$FORMAL_NODE_CURRENT_PATH/bin/node")" \
    || fail "无法解析正式测试 Node 实体"
  FORMAL_NPM_CLI_PATH="$(readlink -f \
    "$FORMAL_NODE_CURRENT_PATH/lib/node_modules/npm/bin/npm-cli.js")" \
    || fail "无法解析固定 npm-cli.js 实体"
  exec {FORMAL_NODE_FD}< "$FORMAL_NODE_PATH" \
    || fail "无法固定正式测试 Node held FD"
  FORMAL_NODE_HELD_PATH="/proc/$$/fd/$FORMAL_NODE_FD"
  result="$(held_file_identity_and_sha256 "$FORMAL_NODE_HELD_PATH" \
    "$FORMAL_NODE_PATH")" \
    || fail "正式测试 Node 身份不稳定"
  IFS=$'\t' read -r FORMAL_NODE_IDENTITY FORMAL_NODE_SHA256 <<< "$result"
  [ "$FORMAL_NODE_SHA256" = "$NODE_PINNED_EXECUTABLE_SHA256" ] \
    || fail "正式测试 Node held FD 不是固定实体"
  exec {FORMAL_NPM_CLI_FD}< "$FORMAL_NPM_CLI_PATH" \
    || fail "无法固定 npm-cli.js held FD"
  FORMAL_NPM_CLI_HELD_PATH="/proc/$$/fd/$FORMAL_NPM_CLI_FD"
  result="$(held_file_identity_and_sha256 "$FORMAL_NPM_CLI_HELD_PATH" \
    "$FORMAL_NPM_CLI_PATH")" \
    || fail "npm-cli.js 身份不稳定"
  IFS=$'\t' read -r FORMAL_NPM_CLI_IDENTITY FORMAL_NPM_CLI_SHA256 <<< "$result"
  [ "$FORMAL_NPM_CLI_SHA256" = "$NODE_NPM_CLI_ARCHIVE_SHA256" ] \
    || fail "npm-cli.js held FD 不是已固定 Node 归档成员"
  version="$(/usr/bin/env -i HOME="$BUILD_ROOT/formal-build-test-home" \
    TMPDIR="$BUILD_TMP_DIR" PATH=/usr/bin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    "$FORMAL_NODE_HELD_PATH" --version)" \
    || fail "无法执行 held Node FD"
  [ "$version" = "v$NODE_VERSION" ] \
    || fail "held Node FD 版本不是固定版本"
  version="$(run_held_node_script \
    "$FORMAL_NPM_CLI_HELD_PATH" "$FORMAL_NPM_CLI_PATH" --version)" \
    || fail "无法使用 held Node/npm CLI 读取版本"
  [ "$version" = "$NODE_NPM_VERSION_ARCHIVE" ] \
    || fail "held npm CLI 版本与已固定 Node 归档不一致"
}

run_held_python() {
  local canonical_path="$1" held_path="$2"
  shift 2
  /usr/bin/env -i \
    HOME="$BUILD_ROOT/formal-build-test-home" \
    TMPDIR="$BUILD_TMP_DIR" \
    PATH=/usr/bin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    /bin/bash -p -c 'exec -a "$1" "$2" "${@:3}"' _ \
    "$canonical_path" "$held_path" "$@"
}

seal_formal_test_python_runtime() {
  local result version prefix current_uid mode previous_umask had_noclobber
  FORMAL_PYTHON_PATH="$FORMAL_AGENT_VENV/bin/python"
  FORMAL_PYTHON_LAUNCHER_PATH="$FORMAL_AGENT_VENV/bin/.taiji-python-launcher"
  current_uid="$(id -u)"
  [ -f "$FORMAL_PYTHON_PATH" ] && [ ! -L "$FORMAL_PYTHON_PATH" ] \
    && [ -x "$FORMAL_PYTHON_PATH" ] && [ "$(stat -c '%h' "$FORMAL_PYTHON_PATH")" = 1 ] \
    && [ "$(stat -c '%u' "$FORMAL_PYTHON_PATH")" = "$current_uid" ] \
    || fail "正式 Agent 测试 Python 不是 --copies 生成的单链接实体"
  mode="$(stat -c '%a' "$FORMAL_PYTHON_PATH")"
  [ $((8#$mode & 8#022)) -eq 0 ] \
    || fail "正式 Agent 测试 Python 允许 group/other 写入"
  [ -f "$FORMAL_AGENT_VENV/pyvenv.cfg" ] \
    && [ ! -L "$FORMAL_AGENT_VENV/pyvenv.cfg" ] \
    && [ "$(stat -c '%h' "$FORMAL_AGENT_VENV/pyvenv.cfg")" = 1 ] \
    && [ "$(stat -c '%u' "$FORMAL_AGENT_VENV/pyvenv.cfg")" = "$current_uid" ] \
    || fail "正式 Agent 测试 pyvenv.cfg 不安全"
  exec {FORMAL_PYTHON_FD}< "$FORMAL_PYTHON_PATH" \
    || fail "无法打开正式 Agent 测试 Python held FD"
  FORMAL_PYTHON_HELD_PATH="/proc/$$/fd/$FORMAL_PYTHON_FD"
  result="$(held_file_identity_and_sha256 "$FORMAL_PYTHON_HELD_PATH" \
    "$FORMAL_PYTHON_PATH")" \
    || fail "正式 Agent 测试 Python 身份不稳定"
  IFS=$'\t' read -r FORMAL_PYTHON_IDENTITY FORMAL_PYTHON_SHA256 <<< "$result"
  [ "$FORMAL_PYTHON_SHA256" = "$PYTHON_PINNED_EXECUTABLE_SHA256" ] \
    || fail "正式 Agent 测试 Python held FD 不是固定实体"
  [ ! -e "$FORMAL_PYTHON_LAUNCHER_PATH" ] \
    && [ ! -L "$FORMAL_PYTHON_LAUNCHER_PATH" ] \
    || fail "正式 Agent 测试 Python launcher 路径已被占用"

  previous_umask="$(umask)"
  had_noclobber=0
  [[ -o noclobber ]] && had_noclobber=1
  umask 077
  set -o noclobber
  if ! {
    printf '#!/bin/bash -p\n'
    printf 'exec -a %q %q "$@"\n' \
      "$FORMAL_PYTHON_PATH" "$FORMAL_PYTHON_HELD_PATH"
  } > "$FORMAL_PYTHON_LAUNCHER_PATH"; then
    [ "$had_noclobber" = 1 ] || set +o noclobber
    umask "$previous_umask"
    fail "无法以 no-clobber 方式创建正式 Agent 测试 Python launcher"
  fi
  [ "$had_noclobber" = 1 ] || set +o noclobber
  umask "$previous_umask"
  chmod 0500 "$FORMAL_PYTHON_LAUNCHER_PATH" \
    || fail "无法固定正式 Agent 测试 Python launcher 权限"
  [ -f "$FORMAL_PYTHON_LAUNCHER_PATH" ] \
    && [ ! -L "$FORMAL_PYTHON_LAUNCHER_PATH" ] \
    && [ -x "$FORMAL_PYTHON_LAUNCHER_PATH" ] \
    && [ "$(stat -c '%h' "$FORMAL_PYTHON_LAUNCHER_PATH")" = 1 ] \
    && [ "$(stat -c '%u:%a' "$FORMAL_PYTHON_LAUNCHER_PATH")" = "$current_uid:500" ] \
    || fail "正式 Agent 测试 Python launcher 身份不安全"
  exec {FORMAL_PYTHON_LAUNCHER_FD}< "$FORMAL_PYTHON_LAUNCHER_PATH" \
    || fail "无法固定正式 Agent 测试 Python launcher held FD"
  FORMAL_PYTHON_LAUNCHER_HELD_PATH="/proc/$$/fd/$FORMAL_PYTHON_LAUNCHER_FD"
  result="$(held_file_identity_and_sha256 "$FORMAL_PYTHON_LAUNCHER_HELD_PATH" \
    "$FORMAL_PYTHON_LAUNCHER_PATH")" \
    || fail "正式 Agent 测试 Python launcher 身份不稳定"
  IFS=$'\t' read -r FORMAL_PYTHON_LAUNCHER_IDENTITY \
    FORMAL_PYTHON_LAUNCHER_SHA256 <<< "$result"
  version="$(run_held_python "$FORMAL_PYTHON_PATH" "$FORMAL_PYTHON_LAUNCHER_HELD_PATH" \
    -I -B -c \
    'import platform; print(platform.python_version())')" \
    || fail "无法执行 held Python launcher FD"
  [ "$version" = "$PYTHON_VERSION_PINNED" ] \
    || fail "held Python launcher FD 版本不是固定版本"
  prefix="$(run_held_python "$FORMAL_PYTHON_PATH" "$FORMAL_PYTHON_LAUNCHER_HELD_PATH" \
    -I -B -c \
    'import pathlib, sys; import pytest; print(pathlib.Path(sys.prefix).resolve())')" \
    || fail "held Python launcher FD 未保留正式 venv/site-packages 语义"
  [ "$prefix" = "$(readlink -f "$FORMAL_AGENT_VENV")" ] \
    || fail "held Python launcher FD 的 sys.prefix 未精确指向正式测试 venv"
}

seal_formal_test_eslint() {
  local result
  FORMAL_ESLINT_PATH="$(readlink -f \
    "$(source_lab_dir)/sources/hermes-webui/node_modules/eslint/bin/eslint.js")" \
    || fail "无法解析正式 WebUI eslint 实体"
  exec {FORMAL_ESLINT_FD}< "$FORMAL_ESLINT_PATH" \
    || fail "无法固定 eslint.js held FD"
  FORMAL_ESLINT_HELD_PATH="/proc/$$/fd/$FORMAL_ESLINT_FD"
  result="$(held_file_identity_and_sha256 "$FORMAL_ESLINT_HELD_PATH" \
    "$FORMAL_ESLINT_PATH")" \
    || fail "eslint.js 身份不稳定"
  IFS=$'\t' read -r FORMAL_ESLINT_IDENTITY FORMAL_ESLINT_SHA256 <<< "$result"
}

run_held_node_script() {
  local held_script="$1" canonical_script="$2"
  local -a clean_environment
  shift 2
  clean_environment=(
    /usr/bin/env -i
    HOME="$BUILD_ROOT/formal-build-test-home"
    TMPDIR="$BUILD_TMP_DIR"
    PATH=/usr/bin:/bin
    LANG=C.UTF-8
    LC_ALL=C.UTF-8
    NPM_CONFIG_USERCONFIG="$BUILD_NPM_USERCONFIG"
    NPM_CONFIG_GLOBALCONFIG="$BUILD_NPM_GLOBALCONFIG"
    NPM_CONFIG_CACHE="$BUILD_ROOT/npm-formal-test-cache"
    NPM_CONFIG_AUDIT=false
    NPM_CONFIG_FUND=false
    NPM_CONFIG_UPDATE_NOTIFIER=false
    NPM_CONFIG_IGNORE_SCRIPTS=true
  )
  if [ -n "${FORMAL_NPM_REGISTRY:-}" ]; then
    clean_environment+=("NPM_CONFIG_REGISTRY=$FORMAL_NPM_REGISTRY")
  fi
  "${clean_environment[@]}" "$FORMAL_NODE_HELD_PATH" -e '
const fs = require("fs");
const Module = require("module");
const path = require("path");
const heldPath = process.argv[1];
const canonicalPath = process.argv[2];
const args = process.argv.slice(3);
let source = fs.readFileSync(heldPath, "utf8");
source = source.replace(/^#![^\n]*(?:\n|$)/, "\n");
process.argv = [process.execPath, canonicalPath, ...args];
const scriptModule = new Module(canonicalPath, module);
scriptModule.filename = canonicalPath;
scriptModule.paths = Module._nodeModulePaths(path.dirname(canonicalPath));
scriptModule._compile(source, canonicalPath);
' "$held_script" "$canonical_script" "$@"
}

validate_formal_test_runtime_identity() {
  local result identity digest symlink_result symlink_identity symlink_target version
  [ "${FORMAL_TEST_RUNTIME_SEALED:-0}" = 1 ] || return 0
  symlink_result="$(symlink_identity_and_target "$FORMAL_NODE_CURRENT_PATH")" \
    || return 1
  IFS=$'\t' read -r symlink_identity symlink_target <<< "$symlink_result"
  [ "$symlink_identity" = "$FORMAL_NODE_CURRENT_IDENTITY" ] \
    && [ "$symlink_target" = "$FORMAL_NODE_CURRENT_RAW_TARGET" ] \
    && [ "$(readlink -f "$FORMAL_NODE_CURRENT_PATH/bin/node")" = "$FORMAL_NODE_PATH" ] \
    || return 1
  result="$(held_file_identity_and_sha256 "$FORMAL_NODE_HELD_PATH" \
    "$FORMAL_NODE_PATH")" || return 1
  IFS=$'\t' read -r identity digest <<< "$result"
  [ "$identity" = "$FORMAL_NODE_IDENTITY" ] && [ "$digest" = "$FORMAL_NODE_SHA256" ] \
    || return 1
  result="$(held_file_identity_and_sha256 "$FORMAL_NPM_CLI_HELD_PATH" \
    "$FORMAL_NPM_CLI_PATH")" || return 1
  IFS=$'\t' read -r identity digest <<< "$result"
  [ "$identity" = "$FORMAL_NPM_CLI_IDENTITY" ] \
    && [ "$digest" = "$FORMAL_NPM_CLI_SHA256" ] \
    || return 1
  result="$(held_file_identity_and_sha256 "$FORMAL_PYTHON_HELD_PATH" \
    "$FORMAL_PYTHON_PATH")" || return 1
  IFS=$'\t' read -r identity digest <<< "$result"
  [ "$identity" = "$FORMAL_PYTHON_IDENTITY" ] \
    && [ "$digest" = "$FORMAL_PYTHON_SHA256" ] \
    || return 1
  result="$(held_file_identity_and_sha256 "$FORMAL_PYTHON_LAUNCHER_HELD_PATH" \
    "$FORMAL_PYTHON_LAUNCHER_PATH")" || return 1
  IFS=$'\t' read -r identity digest <<< "$result"
  [ "$identity" = "$FORMAL_PYTHON_LAUNCHER_IDENTITY" ] \
    && [ "$digest" = "$FORMAL_PYTHON_LAUNCHER_SHA256" ] \
    || return 1
  if [ -n "${FORMAL_ESLINT_FD:-}" ]; then
    result="$(held_file_identity_and_sha256 "$FORMAL_ESLINT_HELD_PATH" \
      "$FORMAL_ESLINT_PATH")" || return 1
    IFS=$'\t' read -r identity digest <<< "$result"
    [ "$identity" = "$FORMAL_ESLINT_IDENTITY" ] \
      && [ "$digest" = "$FORMAL_ESLINT_SHA256" ] \
      || return 1
  fi
  version="$(/usr/bin/env -i HOME="$BUILD_ROOT/formal-build-test-home" \
    TMPDIR="$BUILD_TMP_DIR" PATH=/usr/bin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    "$FORMAL_NODE_HELD_PATH" --version)" || return 1
  [ "$version" = "v$NODE_VERSION" ] || return 1
  version="$(run_held_node_script \
    "$FORMAL_NPM_CLI_HELD_PATH" "$FORMAL_NPM_CLI_PATH" --version)" \
    || return 1
  [ "$version" = "$NODE_NPM_VERSION_ARCHIVE" ] || return 1
  version="$(run_held_python "$FORMAL_PYTHON_PATH" "$FORMAL_PYTHON_LAUNCHER_HELD_PATH" \
    -I -B -c \
    'import platform, pytest; print(platform.python_version())')" \
    || return 1
  [ "$version" = "$PYTHON_VERSION_PINNED" ]
}

close_formal_test_runtime_fds() {
  if [ -n "${FORMAL_ESLINT_FD:-}" ]; then
    exec {FORMAL_ESLINT_FD}<&- || true
    FORMAL_ESLINT_FD=""
  fi
  if [ -n "${FORMAL_NPM_CLI_FD:-}" ]; then
    exec {FORMAL_NPM_CLI_FD}<&- || true
    FORMAL_NPM_CLI_FD=""
  fi
  if [ -n "${FORMAL_NODE_FD:-}" ]; then
    exec {FORMAL_NODE_FD}<&- || true
    FORMAL_NODE_FD=""
  fi
  if [ -n "${FORMAL_PYTHON_FD:-}" ]; then
    exec {FORMAL_PYTHON_FD}<&- || true
    FORMAL_PYTHON_FD=""
  fi
  if [ -n "${FORMAL_PYTHON_LAUNCHER_FD:-}" ]; then
    exec {FORMAL_PYTHON_LAUNCHER_FD}<&- || true
    FORMAL_PYTHON_LAUNCHER_FD=""
  fi
  FORMAL_PYTHON_HELD_PATH=""
  FORMAL_PYTHON_LAUNCHER_HELD_PATH=""
  FORMAL_PYTHON_LAUNCHER_PATH=""
  FORMAL_PYTHON_LAUNCHER_IDENTITY=""
  FORMAL_PYTHON_LAUNCHER_SHA256=""
  FORMAL_NODE_HELD_PATH=""
  FORMAL_NPM_CLI_HELD_PATH=""
  FORMAL_ESLINT_HELD_PATH=""
  FORMAL_TEST_RUNTIME_SEALED=0
}

prepare_formal_build_test_dependencies() {
  require_candidate_deb_fixed
  local agent_dir webui_dir test_home registry installed current_uid mode
  agent_dir="$(source_agent_dir)"
  webui_dir="$(source_lab_dir)/sources/hermes-webui"
  test_home="$BUILD_ROOT/formal-build-test-home"
  FORMAL_AGENT_VENV="$BUILD_ROOT/formal-agent-venv"
  FORMAL_AGENT_SITE_PACKAGES="$FORMAL_AGENT_VENV/lib/python3.11/site-packages"
  current_uid="$(id -u)"
  [ -f "$agent_dir/uv.lock" ] && [ ! -L "$agent_dir/uv.lock" ] \
    || fail "正式构建测试缺少普通文件 uv.lock"
  [ -f "$webui_dir/package-lock.json" ] && [ ! -L "$webui_dir/package-lock.json" ] \
    || fail "正式构建测试缺少普通文件 WebUI package-lock.json"
  [ ! -e "$test_home" ] && [ ! -L "$test_home" ] \
    || fail "正式构建测试 HOME 已存在：$test_home"
  [ ! -e "$FORMAL_AGENT_VENV" ] && [ ! -L "$FORMAL_AGENT_VENV" ] \
    || fail "正式 Agent 测试专用 venv 路径已存在：$FORMAL_AGENT_VENV"
  install -d -m 0700 "$test_home"

  info "在受控构建根中用固定 CPython 创建 --copies 正式测试 venv"
  /usr/bin/env -i \
    HOME="$test_home" TMPDIR="$BUILD_TMP_DIR" PATH=/usr/bin:/bin \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
    "$PYTHON_BIN" -I -B -m venv --copies "$FORMAL_AGENT_VENV" \
    || fail "固定 CPython 无法创建正式 Agent 测试专用 venv"
  [ -d "$FORMAL_AGENT_VENV" ] && [ ! -L "$FORMAL_AGENT_VENV" ] \
    && [ "$(stat -c '%u' "$FORMAL_AGENT_VENV")" = "$current_uid" ] \
    || fail "正式 Agent 测试专用 venv 不是当前制包用户的真实目录"
  mode="$(stat -c '%a' "$FORMAL_AGENT_VENV")"
  [ $((8#$mode & 8#022)) -eq 0 ] \
    || fail "正式 Agent 测试专用 venv 允许 group/other 写入"

  info "用固定 uv 和当前 uv.lock 准备正式 Agent 测试依赖"
  cd "$agent_dir"
  /usr/bin/env -i \
    HOME="$test_home" \
    TMPDIR="$BUILD_TMP_DIR" \
    PATH=/usr/bin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CONFIG=1 \
    UV_NO_EDITABLE=1 \
    UV_NO_INSTALL_PROJECT=1 \
    UV_PROJECT_ENVIRONMENT="$FORMAL_AGENT_VENV" \
    UV_PYTHON_DOWNLOADS=never \
    UV_INDEX_URL="$UV_INDEX_URL" \
    "$UV_BIN" sync --extra all --extra dev --locked \
    || fail "固定 uv 无法按当前 uv.lock 准备正式 Agent 测试依赖"
  [ -d "$FORMAL_AGENT_SITE_PACKAGES" ] \
    && [ ! -L "$FORMAL_AGENT_SITE_PACKAGES" ] \
    && [ "$(stat -c '%u' "$FORMAL_AGENT_SITE_PACKAGES")" = "$current_uid" ] \
    || fail "正式 Agent 测试 site-packages 不安全"
  seal_formal_test_python_runtime
  seal_formal_test_node_runtime
  FORMAL_TEST_RUNTIME_SEALED=1
  validate_formal_test_runtime_identity \
    || fail "正式测试 Python/Node 运行时初始固定后漂移"

  info "用固定 Node/npm 和 package-lock 准备正式 WebUI lint 依赖"
  installed=0
  cd "$webui_dir"
  for registry in $(npm_registries); do
    registry="${registry%/}"
    [ -n "$registry" ] || continue
    rm -rf -- "$webui_dir/node_modules"
    FORMAL_NPM_REGISTRY="$registry"
    if run_held_node_script \
      "$FORMAL_NPM_CLI_HELD_PATH" "$FORMAL_NPM_CLI_PATH" \
      ci --ignore-scripts; then
      installed=1
      break
    fi
    warn "正式 WebUI lint 依赖安装失败，切换受控 npm registry"
  done
  FORMAL_NPM_REGISTRY=""
  [ "$installed" = 1 ] || fail "固定 npm 无法按 WebUI package-lock.json 准备正式 lint 依赖"
  /usr/bin/python3 -I -B - "$webui_dir/package.json" <<'PY' \
    || fail "WebUI lint:runtime 脚本已偏离 canonical 正式测试命令"
import json
import sys
from pathlib import Path

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit("duplicate package.json field: " + key)
        result[key] = value
    return result

package = json.loads(
    Path(sys.argv[1]).read_text(encoding="utf-8"),
    object_pairs_hook=strict_object,
)
expected = 'eslint --no-config-lookup -c eslint.runtime-guard.config.mjs "static/**/*.js"'
if not isinstance(package, dict) or package.get("scripts", {}).get("lint:runtime") != expected:
    raise SystemExit("lint:runtime script mismatch")
PY
  seal_formal_test_eslint
  validate_formal_test_runtime_identity \
    || fail "正式测试依赖准备后 Python/Node/npm/eslint 运行时漂移"
}

formal_build_test_log_fd_path() {
  local descriptor="$1"
  printf '/proc/%s/fd/%s\n' "$$" "$descriptor"
}

held_file_identity_and_sha256() {
  local held_path="$1" canonical_path="${2:--}"
  /usr/bin/python3 -I -B - "$held_path" "$canonical_path" <<'PY'
import hashlib
import os
import stat
import sys

held_path, canonical_path = sys.argv[1:]

def identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_uid,
        value.st_gid,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )

descriptor = os.open(held_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
try:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise SystemExit("held file is not a single-link regular file")
    if canonical_path != "-" and identity(os.lstat(canonical_path)) != identity(before):
        raise SystemExit("canonical path is not bound to held file")
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = before.st_size
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            raise SystemExit("held file was truncated while hashing")
        digest.update(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        raise SystemExit("held file grew while hashing")
    after = os.fstat(descriptor)
    if identity(after) != identity(before):
        raise SystemExit("held file changed while hashing")
    if canonical_path != "-" and identity(os.lstat(canonical_path)) != identity(after):
        raise SystemExit("canonical path changed while hashing")
    print(":".join(str(part) for part in identity(after)) + "\t" + digest.hexdigest())
finally:
    os.close(descriptor)
PY
}

fsync_held_file() {
  /usr/bin/python3 -I -B - "$1" <<'PY'
import os
import sys

descriptor = os.open(sys.argv[1], os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

fsync_directory() {
  /usr/bin/python3 -I -B - "$1" <<'PY'
import os
import sys

descriptor = os.open(
    sys.argv[1],
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

formal_build_test_log_identity_matches() {
  local read_result write_result read_identity read_sha write_identity write_sha
  [ -n "${FORMAL_BUILD_TEST_LOG_FD:-}" ] \
    && [ -n "${FORMAL_BUILD_TEST_LOG_READ_FD:-}" ] \
    && [ -n "${FORMAL_BUILD_TEST_LOG_IDENTITY:-}" ] \
    && [ -f "$FORMAL_BUILD_TEST_LOG" ] \
    && [ ! -L "$FORMAL_BUILD_TEST_LOG" ] \
    || return 1
  read_result="$(held_file_identity_and_sha256 \
    "$(formal_build_test_log_fd_path "$FORMAL_BUILD_TEST_LOG_READ_FD")" \
    "$FORMAL_BUILD_TEST_LOG")" \
    || return 1
  write_result="$(held_file_identity_and_sha256 \
    "$(formal_build_test_log_fd_path "$FORMAL_BUILD_TEST_LOG_FD")" -)" \
    || return 1
  IFS=$'\t' read -r read_identity read_sha <<< "$read_result"
  IFS=$'\t' read -r write_identity write_sha <<< "$write_result"
  [ "$read_identity" = "$write_identity" ] \
    && [ "$read_sha" = "$write_sha" ] \
    || return 1
  if [ -n "${FORMAL_BUILD_TESTS_LOG_SHA256:-}" ]; then
    [ "$read_identity" = "$FORMAL_BUILD_TEST_LOG_IDENTITY" ] \
      && [ "$read_sha" = "$FORMAL_BUILD_TESTS_LOG_SHA256" ]
  else
    FORMAL_BUILD_TEST_LOG_IDENTITY="$read_identity"
  fi
}

poison_formal_build_test_log() {
  local reason="$1"
  [ -d "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ] \
    || return 0
  if [ ! -e "$FORMAL_BUILD_TEST_LOG_POISON" ] && [ ! -L "$FORMAL_BUILD_TEST_LOG_POISON" ]; then
    (umask 077; set -o noclobber; {
      printf 'formal_build_test_log_poisoned=1\n'
      printf 'reason=%s\n' "$reason"
    } > "$FORMAL_BUILD_TEST_LOG_POISON") 2>/dev/null || true
  fi
}

require_formal_build_test_log_identity() {
  local context="$1"
  if ! formal_build_test_log_identity_matches; then
    poison_formal_build_test_log "$context"
    fail "正式构建测试日志身份漂移：$context；已保留外来文件和失败现场"
  fi
}

open_formal_build_test_log() {
  local previous_umask had_noclobber write_identity path_identity
  if [ -e "$FORMAL_BUILD_TEST_LOG" ] || [ -L "$FORMAL_BUILD_TEST_LOG" ]; then
    poison_formal_build_test_log "canonical log path was occupied before O_EXCL publication"
    fail "正式构建测试日志已存在，拒绝覆盖；已保留外来文件并写入 poison"
  fi
  [ -d /proc/$$/fd ] \
    || fail "制包机缺少受信 /proc 文件描述符视图，无法固定正式测试日志"
  previous_umask="$(umask)"
  had_noclobber=0
  [[ -o noclobber ]] && had_noclobber=1
  umask 077
  set -o noclobber
  if ! exec {FORMAL_BUILD_TEST_LOG_FD}> "$FORMAL_BUILD_TEST_LOG"; then
    [ "$had_noclobber" = 1 ] || set +o noclobber
    umask "$previous_umask"
    poison_formal_build_test_log "canonical log path was occupied during O_EXCL publication"
    fail "无法以 no-clobber 方式创建正式构建测试日志；已保留失败现场"
  fi
  [ "$had_noclobber" = 1 ] || set +o noclobber
  umask "$previous_umask"
  write_identity="$(stat -Lc '%d:%i:%f:%h:%u' \
    "$(formal_build_test_log_fd_path "$FORMAL_BUILD_TEST_LOG_FD")")" \
    || fail "无法读取正式构建测试日志 held FD 身份"
  path_identity="$(stat -c '%d:%i:%f:%h:%u' "$FORMAL_BUILD_TEST_LOG")" \
    || fail "无法读取正式构建测试日志 canonical 身份"
  [ "$write_identity" = "$path_identity" ] \
    && [ "$(stat -c '%s:%h:%u:%a' "$FORMAL_BUILD_TEST_LOG")" = "0:1:$(id -u):600" ] \
    || fail "正式构建测试日志创建后身份不安全"
  FORMAL_BUILD_TEST_LOG_IDENTITY="$write_identity"
  exec {FORMAL_BUILD_TEST_LOG_READ_FD}< "$FORMAL_BUILD_TEST_LOG" \
    || fail "无法固定正式构建测试日志只读 FD"
  require_formal_build_test_log_identity "open"
}

close_formal_build_test_log_fds() {
  if [ -n "${FORMAL_BUILD_TEST_LOG_READ_FD:-}" ]; then
    exec {FORMAL_BUILD_TEST_LOG_READ_FD}<&- || true
    FORMAL_BUILD_TEST_LOG_READ_FD=""
  fi
  if [ -n "${FORMAL_BUILD_TEST_LOG_FD:-}" ]; then
    exec {FORMAL_BUILD_TEST_LOG_FD}>&- || true
    FORMAL_BUILD_TEST_LOG_FD=""
  fi
}

formal_package_manifest_fd_path() {
  local descriptor="$1"
  printf '/proc/%s/fd/%s\n' "$$" "$descriptor"
}

source_package_manifest_identity_matches() {
  local result identity digest
  [ -n "${SOURCE_PACKAGE_MANIFEST_FD:-}" ] \
    && [ -n "${SOURCE_PACKAGE_MANIFEST_PATH:-}" ] \
    && [ -n "${SOURCE_PACKAGE_MANIFEST_IDENTITY:-}" ] \
    && [ -n "${SOURCE_PACKAGE_MANIFEST_SHA256:-}" ] \
    || return 1
  result="$(held_file_identity_and_sha256 \
    "$(formal_package_manifest_fd_path "$SOURCE_PACKAGE_MANIFEST_FD")" \
    "$SOURCE_PACKAGE_MANIFEST_PATH")" \
    || return 1
  IFS=$'\t' read -r identity digest <<< "$result"
  [ "$identity" = "$SOURCE_PACKAGE_MANIFEST_IDENTITY" ] \
    && [ "$digest" = "$SOURCE_PACKAGE_MANIFEST_SHA256" ]
}

formal_package_manifest_identity_matches() {
  local result identity digest
  [ -n "${FORMAL_PACKAGE_MANIFEST_FD:-}" ] \
    && [ -n "${FORMAL_PACKAGE_MANIFEST_READ_FD:-}" ] \
    && [ -n "${FORMAL_PACKAGE_MANIFEST_IDENTITY:-}" ] \
    && [ -f "$MANIFEST_FILE" ] \
    && [ ! -L "$MANIFEST_FILE" ] \
    || return 1
  result="$(held_file_identity_and_sha256 \
    "$(formal_package_manifest_fd_path "$FORMAL_PACKAGE_MANIFEST_READ_FD")" \
    "$MANIFEST_FILE")" \
    || return 1
  IFS=$'\t' read -r identity digest <<< "$result"
  [ "$identity" = "$FORMAL_PACKAGE_MANIFEST_IDENTITY" ] \
    || return 1
  if [ -n "${FORMAL_PACKAGE_MANIFEST_SHA256:-}" ]; then
    [ "$digest" = "$FORMAL_PACKAGE_MANIFEST_SHA256" ]
  fi
}

poison_formal_package_manifest() {
  local reason="$1"
  [ -d "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ] || return 0
  if [ ! -e "$FORMAL_PACKAGE_MANIFEST_POISON" ] \
      && [ ! -L "$FORMAL_PACKAGE_MANIFEST_POISON" ]; then
    (umask 077; set -o noclobber; {
      printf 'formal_package_manifest_poisoned=1\n'
      printf 'reason=%s\n' "$reason"
    } > "$FORMAL_PACKAGE_MANIFEST_POISON") 2>/dev/null || true
  fi
}

require_formal_package_manifest_identity() {
  local context="$1"
  if ! formal_package_manifest_identity_matches; then
    poison_formal_package_manifest "$context"
    fail "正式 package manifest 身份漂移：$context；已保留外来文件和失败现场"
  fi
}

close_formal_package_manifest_fds() {
  if [ -n "${FORMAL_PACKAGE_MANIFEST_READ_FD:-}" ]; then
    exec {FORMAL_PACKAGE_MANIFEST_READ_FD}<&- || true
    FORMAL_PACKAGE_MANIFEST_READ_FD=""
  fi
  if [ -n "${FORMAL_PACKAGE_MANIFEST_FD:-}" ]; then
    exec {FORMAL_PACKAGE_MANIFEST_FD}>&- || true
    FORMAL_PACKAGE_MANIFEST_FD=""
  fi
  if [ -n "${SOURCE_PACKAGE_MANIFEST_FD:-}" ]; then
    exec {SOURCE_PACKAGE_MANIFEST_FD}<&- || true
    SOURCE_PACKAGE_MANIFEST_FD=""
  fi
}

bind_formal_build_test_evidence_to_manifest() {
  local previous_umask had_noclobber source_fd_path manifest_fd_path
  local path_identity write_identity final_stability current_uid manifest_result manifest_sha
  current_uid="$(id -u)"
  [ "$FORMAL_BUILD_TESTS_STATUS" = pass ] \
    || fail "正式构建测试未通过，禁止绑定 manifest"
  [ "$FORMAL_BUILD_TESTS_LOG_BASENAME" = formal-build-tests.log ] \
    || fail "正式构建测试日志 basename 不是 canonical 值"
  printf '%s\n' "$FORMAL_BUILD_TESTS_LOG_SHA256" | grep -Eq '^[0-9a-f]{64}$' \
    || fail "正式构建测试日志 SHA256 无效"
  source_package_manifest_identity_matches \
    || fail "build-deb manifest held FD 身份已漂移"
  source_fd_path="$(formal_package_manifest_fd_path "$SOURCE_PACKAGE_MANIFEST_FD")"
  ! grep -q '"formal_build_tests_' "$source_fd_path" \
    || fail "build-deb manifest 已含非本阶段生成的正式测试字段"
  if [ -e "$MANIFEST_FILE" ] || [ -L "$MANIFEST_FILE" ]; then
    poison_formal_package_manifest "canonical manifest path was occupied before O_EXCL publication"
    fail "正式 package manifest 发布路径已被占用；已保留外来文件并写入 poison"
  fi
  [ ! -e "$FORMAL_PACKAGE_MANIFEST_POISON" ] \
    && [ ! -L "$FORMAL_PACKAGE_MANIFEST_POISON" ] \
    || fail "正式 package manifest poison 路径已被占用"
  previous_umask="$(umask)"
  had_noclobber=0
  [[ -o noclobber ]] && had_noclobber=1
  umask 077
  set -o noclobber
  if ! exec {FORMAL_PACKAGE_MANIFEST_FD}> "$MANIFEST_FILE"; then
    [ "$had_noclobber" = 1 ] || set +o noclobber
    umask "$previous_umask"
    poison_formal_package_manifest "canonical manifest path was occupied during O_EXCL publication"
    fail "无法以 no-clobber 方式创建正式 package manifest；已保留失败现场"
  fi
  [ "$had_noclobber" = 1 ] || set +o noclobber
  umask "$previous_umask"
  manifest_fd_path="$(formal_package_manifest_fd_path "$FORMAL_PACKAGE_MANIFEST_FD")"
  if ! /usr/bin/awk \
    -v status="$FORMAL_BUILD_TESTS_STATUS" \
    -v basename="$FORMAL_BUILD_TESTS_LOG_BASENAME" \
    -v digest="$FORMAL_BUILD_TESTS_LOG_SHA256" '
      BEGIN { inserted = 0 }
      /^  "built_at_utc": / {
        print "  \"formal_build_tests_status\": \"" status "\","
        print "  \"formal_build_tests_log_basename\": \"" basename "\","
        print "  \"formal_build_tests_log_sha256\": \"" digest "\","
        inserted += 1
      }
      { print }
      END { if (inserted != 1) exit 42 }
    ' "$source_fd_path" >&"$FORMAL_PACKAGE_MANIFEST_FD"; then
    poison_formal_package_manifest "failed while writing held manifest FD"
    fail "无法把正式构建测试证据写入正式 package manifest held FD"
  fi
  chmod 0644 "$manifest_fd_path"
  write_identity="$(stat -Lc '%d:%i:%f:%h:%s:%u' "$manifest_fd_path")"
  path_identity="$(stat -c '%d:%i:%f:%h:%s:%u' "$MANIFEST_FILE")"
  [ "$write_identity" = "$path_identity" ] \
    && [ "$(stat -c '%h:%u:%a' "$MANIFEST_FILE")" = "1:$current_uid:644" ] \
    || { poison_formal_package_manifest "unsafe identity after held-FD write"; \
      fail "正式 package manifest 创建后身份不安全"; }
  exec {FORMAL_PACKAGE_MANIFEST_READ_FD}< "$manifest_fd_path" \
    || fail "无法固定正式 package manifest 只读 FD"
  manifest_result="$(held_file_identity_and_sha256 \
    "$(formal_package_manifest_fd_path "$FORMAL_PACKAGE_MANIFEST_READ_FD")" \
    "$MANIFEST_FILE")" \
    || fail "正式 package manifest 创建后无法固定完整身份与摘要"
  IFS=$'\t' read -r FORMAL_PACKAGE_MANIFEST_IDENTITY manifest_sha \
    <<< "$manifest_result"
  require_formal_package_manifest_identity "after single publication"
  final_stability="$FORMAL_PACKAGE_MANIFEST_IDENTITY"
  /usr/bin/env -i \
    HOME="$BUILD_ROOT" TMPDIR="$BUILD_TMP_DIR" PATH=/usr/bin:/bin \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
    /usr/bin/python3 -I -B - \
      "$(formal_package_manifest_fd_path "$FORMAL_PACKAGE_MANIFEST_READ_FD")" \
      "$FORMAL_BUILD_TESTS_LOG_SHA256" <<'PY' \
    || fail "正式构建测试字段未形成严格 package manifest v3 合同"
import json
import re
import sys
from pathlib import Path

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit("duplicate manifest field: " + key)
        result[key] = value
    return result

path = Path(sys.argv[1])
manifest = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
expected_fields = {
    "schema", "package", "version", "architecture", "source_commit",
    "source_archive_basename", "source_archive_sha256",
    "source_inventory_basename", "source_inventory_sha256", "deb_basename",
    "deb_sha256", "acceptance_binding_sha256",
    "acceptance_tools_manifest_sha256", "acceptance_entrypoint_sha256",
    "installed_release_manifest_sha256", "maintainer", "compatibility_policy_id",
    "compatibility_policy_sha256", "upgrade_data_contract_id",
    "upgrade_data_contract_sha256", "elf_abi_audit_basename",
    "elf_abi_audit_sha256", "python_dependency_lock_status",
    "python_lock_basename", "python_lock_sha256", "python_version",
    "python_archive_sha256", "python_executable_sha256", "uv_version",
    "uv_archive_sha256", "uv_executable_sha256", "node_version",
    "node_archive_sha256", "node_executable_sha256", "electron_version",
    "electron_archive_sha256", "electron_executable_sha256",
    "desktop_entry_sha256", "icon_set_sha256", "built_at_utc",
    "formal_build_tests_status", "formal_build_tests_log_basename",
    "formal_build_tests_log_sha256",
}
if type(manifest) is not dict or set(manifest) != expected_fields:
    raise SystemExit("manifest fields are not the exact formal v3 contract")
if manifest.get("schema") != "taiji-package-manifest/v3":
    raise SystemExit("manifest schema is not v3")
if manifest.get("formal_build_tests_status") != "pass":
    raise SystemExit("formal build tests did not pass")
if manifest.get("formal_build_tests_log_basename") != "formal-build-tests.log":
    raise SystemExit("formal build test log basename is not canonical")
if manifest.get("formal_build_tests_log_sha256") != sys.argv[2] or re.fullmatch(
    r"[0-9a-f]{64}", sys.argv[2]
) is None:
    raise SystemExit("formal build test log SHA256 mismatch")
PY
  manifest_result="$(held_file_identity_and_sha256 \
    "$(formal_package_manifest_fd_path "$FORMAL_PACKAGE_MANIFEST_READ_FD")" \
    "$MANIFEST_FILE")" \
    || { poison_formal_package_manifest "changed during strict held-FD readback"; \
      fail "package manifest 在严格 held-FD 读回校验期间发生变化"; }
  IFS=$'\t' read -r path_identity write_identity <<< "$manifest_result"
  [ "$path_identity" = "$final_stability" ] \
    && [ "$write_identity" = "$manifest_sha" ] \
    || { poison_formal_package_manifest "changed during strict held-FD readback"; \
      fail "package manifest 在严格 held-FD 读回校验期间发生变化"; }
  FORMAL_PACKAGE_MANIFEST_SHA256="$manifest_sha"
  require_formal_package_manifest_identity "after strict held-FD readback"
  FORMAL_PACKAGE_MANIFEST_COMPLETE=1
  exec {SOURCE_PACKAGE_MANIFEST_FD}<&-
  SOURCE_PACKAGE_MANIFEST_FD=""
}

run_formal_build_tests_direct() {
  require_candidate_deb_fixed
  [ -x /usr/bin/python3 ] || fail "正式构建测试需要受信任的 /usr/bin/python3"
  [ -x /usr/bin/unshare ] && [ -x /bin/mount ] \
    || fail "正式 gateway 测试需要受信任的 user/mount namespace 工具"
  [ -n "${FORMAL_PYTHON_FD:-}" ] \
    && [ -n "${FORMAL_PYTHON_PATH:-}" ] \
    && [ -n "${FORMAL_PYTHON_LAUNCHER_FD:-}" ] \
    && [ -n "${FORMAL_PYTHON_LAUNCHER_HELD_PATH:-}" ] \
    && [ -n "${FORMAL_NODE_FD:-}" ] \
    && [ -n "${FORMAL_NPM_CLI_FD:-}" ] && [ -n "${FORMAL_ESLINT_FD:-}" ] \
    || fail "正式构建测试工具 held FD 未完整准备"
  [ -d "$SRC_DIR" ] && [ ! -L "$SRC_DIR" ] || fail "正式构建测试源码根不安全"
  local direct_work="$BUILD_ROOT/formal-build-tests-direct" status
  mkdir -m 0700 -- "$direct_work" \
    || fail "无法创建正式构建测试隔离工作根"
  mkdir -m 0700 -- "$direct_work/home" "$direct_work/tmp" \
    || fail "无法创建正式构建测试隔离 HOME/TMPDIR"
  open_formal_build_test_log
  verify_build_source_integrity
  open_formal_source_checkout_marker
  open_formal_test_passwd "$direct_work" "$direct_work/home"
  formal_test_passwd_identity_matches \
    || fail "正式 gateway 测试 passwd 启动前已漂移"
  if /usr/bin/python3 -I -B - \
      "$SRC_DIR/scripts/run-taiji-formal-build-tests.py" \
      "$FORMAL_PYTHON_FD" \
      "$FORMAL_PYTHON_LAUNCHER_HELD_PATH" \
      "$FORMAL_PYTHON_PATH" \
      "$BUILD_NPM_USERCONFIG" \
      "$BUILD_NPM_GLOBALCONFIG" \
      "$FORMAL_TEST_PASSWD" \
      --source-root "$SRC_DIR" \
      --source-commit "$MARKER_SOURCE_COMMIT" \
      --work-root "$direct_work" \
      --python-fd "$FORMAL_PYTHON_FD" \
      --node-fd "$FORMAL_NODE_FD" \
      --npm-cli-fd "$FORMAL_NPM_CLI_FD" \
      --eslint-fd "$FORMAL_ESLINT_FD" \
      --log-fd "$FORMAL_BUILD_TEST_LOG_FD" \
      2>&1 <<'PY'
import runpy
import sys

driver_path = sys.argv[1]
python_fd = int(sys.argv[2])
launcher_held_path = sys.argv[3]
python_path = sys.argv[4]
npm_userconfig = sys.argv[5]
npm_globalconfig = sys.argv[6]
formal_passwd_path = sys.argv[7]
driver_argv = [driver_path] + sys.argv[8:]
namespace = runpy.run_path(
    driver_path, run_name="taiji_formal_held_python_driver"
)
driver_globals = namespace["run"].__globals__
original_fd_path = driver_globals["_fd_path"]
original_clean_environment = driver_globals["_clean_environment"]
original_popen = driver_globals["subprocess"].Popen


def held_python_fd_path(descriptor):
    if descriptor == python_fd:
        return launcher_held_path
    return original_fd_path(descriptor)


def controlled_npm_clean_environment(work):
    environment = original_clean_environment(work)
    environment["NPM_CONFIG_USERCONFIG"] = npm_userconfig
    environment["NPM_CONFIG_GLOBALCONFIG"] = npm_globalconfig
    return environment


def controlled_formal_popen(argv, *args, **kwargs):
    replacement = list(argv)
    if (
        len(replacement) == 6
        and replacement[1] == "-e"
        and "const {run}=require('node:test');" in replacement[2]
    ):
        replacement[2] = "process.execArgv=[];" + replacement[2]
    if (
        replacement
        and replacement[0] == launcher_held_path
        and "--formal-results-fd" in replacement
        and replacement[-1] == "tests/gateway/test_api_server_license.py"
    ):
        if python_fd not in kwargs.get("pass_fds", ()):
            raise RuntimeError("formal gateway Python FD was not inherited")
        namespace_script = (
            "set -eu\n"
            "/bin/mount --make-rprivate /\n"
            '/bin/mount --bind "$1" /etc/passwd\n'
            '/bin/mount -o remount,bind,ro /etc/passwd\n'
            "python_fd=$2\n"
            "python_argv0=$3\n"
            "shift 3\n"
            "shift\n"
            'exec -a "$python_argv0" "/proc/self/fd/$python_fd" "$@"\n'
        )
        replacement = [
            "/usr/bin/unshare",
            "--user", "--map-root-user", "--mount",
            "/bin/bash", "-p", "-c", namespace_script,
            "taiji-formal-license-home",
            formal_passwd_path,
            str(python_fd),
            python_path,
            *replacement,
        ]
    return original_popen(replacement, *args, **kwargs)


driver_globals["_fd_path"] = held_python_fd_path
driver_globals["_clean_environment"] = controlled_npm_clean_environment
driver_globals["subprocess"].Popen = controlled_formal_popen
sys.argv = driver_argv
raise SystemExit(namespace["main"]())
PY
  then
    status=0
  else
    status=$?
  fi
  formal_test_passwd_identity_matches \
    || fail "正式 gateway 测试 passwd 运行后已漂移"
  close_formal_test_passwd \
    || fail "正式 gateway 测试 passwd 无法安全移除"
  close_formal_source_checkout_marker \
    || fail "正式源码测试 checkout marker 无法安全移除"
  verify_build_source_integrity
  [ "$status" = 0 ] || fail "direct formal-build-tests/v2 失败 (exit=$status)"
  FORMAL_BUILD_TESTS_STATUS=pass
  require_formal_build_test_log_identity "after direct formal-build-tests/v2 write"
  FORMAL_BUILD_TESTS_LOG_SHA256="$(sha256sum "$(formal_build_test_log_fd_path "$FORMAL_BUILD_TEST_LOG_FD")" | awk '{print $1}')"
  require_formal_build_test_log_identity "after direct formal-build-tests/v2"
  bind_formal_build_test_evidence_to_manifest
  require_formal_package_manifest_identity "after direct formal test binding"
  require_formal_build_test_log_identity "after direct manifest binding"
  close_formal_test_runtime_fds
  ok "direct formal-build-tests/v2 全部通过并绑定日志：$FORMAL_BUILD_TESTS_LOG_SHA256"
}

pending_build_marker_identity_matches() {
  local result identity digest
  [ -n "${PENDING_BUILD_MARKER_FD:-}" ] \
    && [ -n "${PENDING_BUILD_MARKER_IDENTITY:-}" ] \
    && [ -n "${PENDING_BUILD_MARKER_SHA256:-}" ] \
    || return 1
  result="$(held_file_identity_and_sha256 "/proc/$$/fd/$PENDING_BUILD_MARKER_FD" \
    "$PENDING_BUILD_MARKER")" \
    || return 1
  IFS=$'\t' read -r identity digest <<< "$result"
  [ "$identity" = "$PENDING_BUILD_MARKER_IDENTITY" ] \
    && [ "$digest" = "$PENDING_BUILD_MARKER_SHA256" ]
}

poison_pending_build_marker() {
  local reason="$1"
  [ -n "${PENDING_BUILD_MARKER_POISON:-}" ] || return 0
  if [ ! -e "$PENDING_BUILD_MARKER_POISON" ] \
      && [ ! -L "$PENDING_BUILD_MARKER_POISON" ]; then
    (umask 077; set -o noclobber; {
      printf 'pending_build_marker_poisoned=1\n'
      printf 'reason=%s\n' "$reason"
    } > "$PENDING_BUILD_MARKER_POISON") 2>/dev/null || true
  fi
}

close_pending_build_marker_fd() {
  if [ -n "${PENDING_BUILD_MARKER_FD:-}" ]; then
    exec {PENDING_BUILD_MARKER_FD}<&- || true
    PENDING_BUILD_MARKER_FD=""
  fi
}

require_pending_build_marker_identity() {
  local context="$1"
  if ! pending_build_marker_identity_matches; then
    poison_pending_build_marker "$context"
    fail "待发布构建成功标记 held-FD 身份或摘要漂移：$context"
  fi
}

write_pending_build_marker() {
  local previous_umask had_noclobber pending_write_fd pending_result
  require_candidate_deb_fixed
  require_formal_package_manifest_identity "before pending build marker"
  require_formal_build_test_log_identity "before pending build marker"
  [ "$FORMAL_BUILD_TESTS_STATUS" = pass ] \
    && [ "$FORMAL_BUILD_TESTS_LOG_BASENAME" = formal-build-tests.log ] \
    && printf '%s\n' "$FORMAL_BUILD_TESTS_LOG_SHA256" | grep -Eq '^[0-9a-f]{64}$' \
    || fail "正式构建测试证据未闭合，禁止写入成功标记"
  case "$PENDING_BUILD_MARKER" in
    "$BUILD_ROOT/.build-success.pending") ;;
    *) fail "构建成功待发布标记未绑定受控构建根" ;;
  esac
  PENDING_BUILD_MARKER_POISON="$BUILD_ROOT/.build-success.pending.poisoned"
  [ ! -e "$PENDING_BUILD_MARKER_POISON" ] && [ ! -L "$PENDING_BUILD_MARKER_POISON" ] \
    || fail "待发布标记私有 poison 路径已被占用"
  if [ -e "$PENDING_BUILD_MARKER" ] || [ -L "$PENDING_BUILD_MARKER" ]; then
    poison_pending_build_marker "private pending marker path was occupied before O_EXCL publication"
    fail "构建成功待发布标记已存在，拒绝覆盖或跟随符号链接"
  fi
  previous_umask="$(umask)"
  had_noclobber=0
  [[ -o noclobber ]] && had_noclobber=1
  umask 077
  set -o noclobber
  if ! exec {pending_write_fd}> "$PENDING_BUILD_MARKER"; then
    [ "$had_noclobber" = 1 ] || set +o noclobber
    umask "$previous_umask"
    poison_pending_build_marker "pending marker no-clobber open failed"
    fail "构建成功待发布标记路径被占用，未跟随符号链接或覆盖外来文件"
  fi
  [ "$had_noclobber" = 1 ] || set +o noclobber
  umask "$previous_umask"
  {
    printf 'version=%s\n' "$VERSION"
    printf 'source_archive=%s\n' "$MARKER_SOURCE_NAME"
    printf 'source_sha256=%s\n' "$MARKER_SOURCE_SHA256"
    printf 'source_commit=%s\n' "$MARKER_SOURCE_COMMIT"
    printf 'source_inventory=%s\n' "$(basename "$SOURCE_INVENTORY")"
    printf 'source_inventory_sha256=%s\n' "$SOURCE_INVENTORY_SHA256"
    printf 'deb=%s\n' "$MARKER_DEB_NAME"
    printf 'deb_sha256=%s\n' "$MARKER_DEB_SHA256"
    printf 'checksum=%s\n' "$MARKER_DEB_NAME.sha256"
    printf 'built_at_utc=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'manifest=%s\n' "$(basename "$MANIFEST_FILE")"
    printf 'compatibility_policy_id=%s\n' "$POLICY_ID"
    printf 'compatibility_policy_sha256=%s\n' "$POLICY_SHA256"
    printf 'elf_abi_audit_sha256=%s\n' "$ELF_ABI_AUDIT_SHA256"
    printf 'acceptance_binding_sha256=%s\n' "$ACCEPTANCE_BINDING_SHA256"
    printf 'acceptance_tools_manifest_sha256=%s\n' "$ACCEPTANCE_TOOLS_MANIFEST_SHA256"
    printf 'acceptance_entrypoint_sha256=%s\n' "$ACCEPTANCE_ENTRYPOINT_SHA256"
    printf 'installed_release_manifest_sha256=%s\n' "$INSTALLED_RELEASE_MANIFEST_SHA256"
    printf 'icon_set_sha256=%s\n' "$ICON_SET_SHA256"
    printf 'formal_build_tests_status=%s\n' "$FORMAL_BUILD_TESTS_STATUS"
    printf 'formal_build_tests_log_basename=%s\n' "$FORMAL_BUILD_TESTS_LOG_BASENAME"
    printf 'formal_build_tests_log_sha256=%s\n' "$FORMAL_BUILD_TESTS_LOG_SHA256"
    printf 'python_dependency_lock_status=%s\n' "$PYTHON_DEPENDENCY_LOCK_STATUS"
    printf 'python_lock_basename=%s\n' "$PYTHON_LOCK_BASENAME"
    printf 'python_lock_sha256=%s\n' "$PYTHON_LOCK_SHA256"
    printf 'python_version=%s\n' "$PYTHON_VERSION"
    printf 'python_archive_sha256=%s\n' "$PYTHON_ARCHIVE_SHA256"
    printf 'python_executable_sha256=%s\n' "$PYTHON_EXECUTABLE_SHA256"
    printf 'uv_version=%s\n' "$UV_VERSION"
    printf 'uv_archive_sha256=%s\n' "$UV_ARCHIVE_SHA256"
    printf 'uv_executable_sha256=%s\n' "$UV_EXECUTABLE_SHA256"
    printf 'node_version=%s\n' "$NODE_VERSION"
    printf 'node_archive_sha256=%s\n' "$NODE_ARCHIVE_SHA256"
    printf 'node_executable_sha256=%s\n' "$NODE_EXECUTABLE_SHA256"
    printf 'electron_version=%s\n' "$ELECTRON_VERSION"
    printf 'electron_archive_sha256=%s\n' "$ELECTRON_ARCHIVE_SHA256"
    printf 'electron_executable_sha256=%s\n' "$ELECTRON_EXECUTABLE_SHA256"
    printf 'maintainer=%s\n' "$POLICY_MAINTAINER"
  } >&"$pending_write_fd"
  chmod 0600 "/proc/$$/fd/$pending_write_fd"
  fsync_held_file "/proc/$$/fd/$pending_write_fd" \
    || { poison_pending_build_marker "pending marker fsync failed"; fail "待发布标记持久化失败"; }
  exec {PENDING_BUILD_MARKER_FD}< "/proc/$$/fd/$pending_write_fd" \
    || fail "无法固定待发布标记 held read FD"
  exec {pending_write_fd}>&-
  pending_result="$(held_file_identity_and_sha256 "/proc/$$/fd/$PENDING_BUILD_MARKER_FD" \
    "$PENDING_BUILD_MARKER")" \
    || { poison_pending_build_marker "pending marker identity drifted"; fail "待发布标记身份不稳定"; }
  IFS=$'\t' read -r PENDING_BUILD_MARKER_IDENTITY PENDING_BUILD_MARKER_SHA256 \
    <<< "$pending_result"
  case "$PENDING_BUILD_MARKER_SHA256" in
    ""|*[!0-9a-f]*) fail "无法固定构建成功待发布标记 SHA256" ;;
    *) [ "${#PENDING_BUILD_MARKER_SHA256}" -eq 64 ] \
      || fail "构建成功待发布标记 SHA256 长度不合法" ;;
  esac
  require_pending_build_marker_identity "after private marker creation"
}

poison_published_build_marker() {
  local reason="$1"
  [ -d "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ] || return 0
  if [ ! -e "$PUBLISHED_BUILD_MARKER_POISON" ] \
      && [ ! -L "$PUBLISHED_BUILD_MARKER_POISON" ]; then
    (umask 077; set -o noclobber; {
      printf 'published_build_marker_poisoned=1\n'
      printf 'reason=%s\n' "$reason"
    } > "$PUBLISHED_BUILD_MARKER_POISON") 2>/dev/null || true
  fi
}

published_build_marker_identity_matches() {
  local path_identity stability_before stability_after actual_sha256
  [ -n "${PUBLISHED_BUILD_MARKER_IDENTITY:-}" ] \
    && [ -n "${PUBLISHED_BUILD_MARKER_SHA256:-}" ] \
    && [ -f "$BUILD_MARKER" ] \
    && [ ! -L "$BUILD_MARKER" ] \
    || return 1
  path_identity="$(stat -c '%d:%i:%f:%h:%s:%u' "$BUILD_MARKER")" \
    || return 1
  [ "$path_identity" = "$PUBLISHED_BUILD_MARKER_IDENTITY" ] \
    || return 1
  stability_before="$(stat -c '%d:%i:%f:%h:%s:%u:%Y:%Z' "$BUILD_MARKER")" \
    || return 1
  actual_sha256="$(sha256sum "$BUILD_MARKER" | awk '{print $1}')" \
    || return 1
  stability_after="$(stat -c '%d:%i:%f:%h:%s:%u:%Y:%Z' "$BUILD_MARKER")" \
    || return 1
  [ "$stability_after" = "$stability_before" ] \
    && [ "$actual_sha256" = "$PUBLISHED_BUILD_MARKER_SHA256" ]
}

require_published_build_marker_identity() {
  local context="$1"
  if ! published_build_marker_identity_matches; then
    poison_published_build_marker "$context"
    fail "构建成功标记身份漂移：$context；已保留外来文件和失败现场"
  fi
}

publish_build_success_marker() {
  local published_identity
  require_candidate_deb_fixed
  require_pending_build_marker_identity "before final marker publication"
  case "$PENDING_BUILD_MARKER" in
    "$OUTPUT_DIR/.build-success.pending.$$" ) ;;
    *) fail "最终构建成功待发布标记未绑定本轮输出目录" ;;
  esac
  [ -f "$PENDING_BUILD_MARKER" ] && [ ! -L "$PENDING_BUILD_MARKER" ] \
    || fail "最终门禁通过后缺少待发布构建成功标记"
  case "$PENDING_BUILD_MARKER_SHA256" in
    ""|*[!0-9a-f]*) fail "最终待发布标记缺少已验证 SHA256" ;;
    *) [ "${#PENDING_BUILD_MARKER_SHA256}" -eq 64 ] \
      || fail "最终待发布标记 SHA256 长度不合法" ;;
  esac
  [ ! -e "$BUILD_MARKER" ] && [ ! -L "$BUILD_MARKER" ] \
    || fail "构建成功标记发布位置已被占用"
  [ ! -e "$PUBLISHED_BUILD_MARKER_POISON" ] \
    && [ ! -L "$PUBLISHED_BUILD_MARKER_POISON" ] \
    || fail "构建成功标记 poison 路径已被占用"
  if ! published_identity="$(/usr/bin/python3 -I -B - \
    "$PENDING_BUILD_MARKER" "$BUILD_MARKER" "$PENDING_BUILD_MARKER_SHA256" \
    "$PUBLISHED_BUILD_MARKER_POISON" <<'PY'
import hashlib
import os
import re
import stat
import sys

source, destination, expected_sha256, poison_path = sys.argv[1:]
if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
    raise SystemExit("pending marker expected hash is invalid")
flags = os.O_RDONLY
if hasattr(os, "O_CLOEXEC"):
    flags |= os.O_CLOEXEC
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW

def identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_uid,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )

def require_bound(path, descriptor, expected_nlink):
    held = os.fstat(descriptor)
    current = os.lstat(path)
    if identity(current) != identity(held):
        raise RuntimeError("published marker canonical path changed")
    if not stat.S_ISREG(held.st_mode) or held.st_nlink != expected_nlink:
        raise RuntimeError("published marker identity is unsafe")
    if held.st_uid != os.getuid():
        raise RuntimeError("published marker owner changed")
    return held

def digest_from_descriptor(descriptor, expected_size):
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = expected_size
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            raise RuntimeError("marker was truncated")
        digest.update(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        raise RuntimeError("marker grew")
    return digest.hexdigest()

def write_poison(reason):
    poison_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    poison_flags |= getattr(os, "O_CLOEXEC", 0)
    poison_flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(poison_path, poison_flags, 0o600)
    try:
        payload = (
            "published_build_marker_poisoned=1\n"
            "reason=" + str(reason).replace("\n", " ").replace("\r", " ") + "\n"
        ).encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeError("cannot write build marker poison")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

descriptor = os.open(source, flags)
published_descriptor = None
published_created = False
try:
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
        raise SystemExit("pending marker is not a safe single-link regular file")
    if opened.st_uid != os.getuid():
        raise SystemExit("pending marker is not owned by current user")
    if digest_from_descriptor(descriptor, opened.st_size) != expected_sha256:
        raise SystemExit("pending marker content differs from the final-gate identity")
    if identity(os.lstat(source)) != identity(opened):
        raise SystemExit("pending marker path changed after validation")
    os.link(source, destination, follow_symlinks=False)
    published_created = True
    published_descriptor = os.open(destination, flags)
    published = require_bound(destination, published_descriptor, 2)
    source_after_link = require_bound(source, descriptor, 2)
    if (published.st_dev, published.st_ino) != (
        source_after_link.st_dev,
        source_after_link.st_ino,
    ):
        raise SystemExit("published marker identity changed")
    require_bound(destination, published_descriptor, 2)
    require_bound(source, descriptor, 2)
    os.unlink(source)
    final = require_bound(destination, published_descriptor, 1)
    if identity(final) != identity(os.fstat(descriptor)):
        raise SystemExit("published marker did not settle to one link")
    os.fsync(descriptor)
    require_bound(destination, published_descriptor, 1)
    directory_fd = os.open(
        os.path.dirname(destination),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    final = require_bound(destination, published_descriptor, 1)
    if digest_from_descriptor(published_descriptor, final.st_size) != expected_sha256:
        raise RuntimeError("published marker content differs from held identity")
    final = require_bound(destination, published_descriptor, 1)
    print(
        "%d:%d:%x:%d:%d:%d"
        % (
            final.st_dev,
            final.st_ino,
            final.st_mode,
            final.st_nlink,
            final.st_size,
            final.st_uid,
        )
    )
except BaseException as error:
    try:
        write_poison(error)
    except BaseException as poison_error:
        print("cannot write build marker poison: " + str(poison_error), file=sys.stderr)
    raise
finally:
    if published_descriptor is not None:
        os.close(published_descriptor)
    os.close(descriptor)
PY
  )"; then
    fail "构建成功标记无法以不覆盖方式原子发布"
  fi
  PUBLISHED_BUILD_MARKER_IDENTITY="$published_identity"
  PUBLISHED_BUILD_MARKER_SHA256="$PENDING_BUILD_MARKER_SHA256"
  require_published_build_marker_identity "publish helper return"
  require_candidate_deb_fixed
  close_pending_build_marker_fd
  PENDING_BUILD_MARKER=""
  PENDING_BUILD_MARKER_IDENTITY=""
  PENDING_BUILD_MARKER_SHA256=""
  printf '[OK] 所有最终门禁通过，已原子发布构建成功标记\n' || true
}

stage_pending_build_marker_for_publication() {
  local staged_marker="$OUTPUT_DIR/.build-success.pending.$$" pending_result staged_sha
  require_candidate_deb_fixed
  require_pending_build_marker_identity "before public staging"
  case "$PENDING_BUILD_MARKER" in
    "$BUILD_ROOT/.build-success.pending") ;;
    *) fail "构建成功待发布标记不在受控构建根" ;;
  esac
  [ -f "$PENDING_BUILD_MARKER" ] && [ ! -L "$PENDING_BUILD_MARKER" ] \
    && [ "$(stat -c '%h' "$PENDING_BUILD_MARKER")" = 1 ] \
    || fail "构建成功待发布标记不安全"
  [ "$(sha256sum "$PENDING_BUILD_MARKER" | awk '{print $1}')" = "$PENDING_BUILD_MARKER_SHA256" ] \
    || fail "构建成功待发布标记在最终预检后发生变化"
  /usr/bin/python3 -I -B - \
    "$PENDING_BUILD_MARKER" "$staged_marker" "$PENDING_BUILD_MARKER_SHA256" \
    "$PUBLISHED_BUILD_MARKER_POISON" <<'PY' \
    || fail "构建成功待发布标记无法以不覆盖方式转移到输出目录"
import hashlib
import os
import re
import stat
import sys

source, destination, expected_sha256, poison_path = sys.argv[1:]
if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
    raise SystemExit("pending marker expected hash is invalid")
read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
write_flags = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
source_descriptor = os.open(source, read_flags)
destination_descriptor = -1

def write_poison(reason):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(poison_path, flags, 0o600)
    try:
        payload = (
            "published_build_marker_poisoned=1\n"
            "reason=" + str(reason).replace("\n", " ").replace("\r", " ") + "\n"
        ).encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeError("cannot write pending marker poison")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

try:
    opened = os.fstat(source_descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
        raise SystemExit("pending marker is not a safe single-link regular file")
    if opened.st_uid != os.getuid():
        raise SystemExit("pending marker is not owned by current user")
    destination_descriptor = os.open(destination, write_flags, 0o600)
    os.fchmod(destination_descriptor, 0o600)
    digest = hashlib.sha256()
    remaining = opened.st_size
    while remaining:
        chunk = os.read(source_descriptor, min(1024 * 1024, remaining))
        if not chunk:
            raise SystemExit("pending marker was truncated while staging")
        digest.update(chunk)
        view = memoryview(chunk)
        while view:
            written = os.write(destination_descriptor, view)
            if written <= 0:
                raise SystemExit("pending marker staging write failed")
            view = view[written:]
        remaining -= len(chunk)
    if os.read(source_descriptor, 1):
        raise SystemExit("pending marker grew while staging")
    if digest.hexdigest() != expected_sha256:
        raise SystemExit("pending marker differs from the final-gate identity")
    source_after = os.fstat(source_descriptor)
    source_current = os.lstat(source)
    opened_identity = (
        opened.st_dev,
        opened.st_ino,
        opened.st_mode,
        opened.st_nlink,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    )
    current_identity = (
        source_current.st_dev,
        source_current.st_ino,
        source_current.st_mode,
        source_current.st_nlink,
        source_current.st_size,
        source_current.st_mtime_ns,
        source_current.st_ctime_ns,
    )
    after_identity = (
        source_after.st_dev,
        source_after.st_ino,
        source_after.st_mode,
        source_after.st_nlink,
        source_after.st_size,
        source_after.st_mtime_ns,
        source_after.st_ctime_ns,
    )
    if current_identity != opened_identity or after_identity != opened_identity:
        raise SystemExit("pending marker changed while staging")
    os.fsync(destination_descriptor)
    destination_after = os.fstat(destination_descriptor)
    destination_current = os.lstat(destination)
    if (
        not stat.S_ISREG(destination_after.st_mode)
        or destination_after.st_nlink != 1
        or destination_after.st_size != opened.st_size
        or (destination_current.st_dev, destination_current.st_ino)
        != (destination_after.st_dev, destination_after.st_ino)
    ):
        raise SystemExit("staged pending marker identity is invalid")
    descriptor = os.open(
        os.path.dirname(destination),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    destination_after = os.fstat(destination_descriptor)
    destination_current = os.lstat(destination)
    if (
        destination_current.st_dev,
        destination_current.st_ino,
        destination_current.st_mode,
        destination_current.st_nlink,
        destination_current.st_size,
        destination_current.st_uid,
        destination_current.st_mtime_ns,
        destination_current.st_ctime_ns,
    ) != (
        destination_after.st_dev,
        destination_after.st_ino,
        destination_after.st_mode,
        destination_after.st_nlink,
        destination_after.st_size,
        destination_after.st_uid,
        destination_after.st_mtime_ns,
        destination_after.st_ctime_ns,
    ):
        raise RuntimeError("staged pending marker canonical path changed")
except BaseException as error:
    try:
        write_poison(error)
    except BaseException as poison_error:
        print("cannot write pending marker poison: " + str(poison_error), file=sys.stderr)
    raise
finally:
    if destination_descriptor >= 0:
        os.close(destination_descriptor)
    os.close(source_descriptor)
PY
  staged_sha="$PENDING_BUILD_MARKER_SHA256"
  close_pending_build_marker_fd
  PENDING_BUILD_MARKER="$staged_marker"
  PENDING_BUILD_MARKER_POISON="$PUBLISHED_BUILD_MARKER_POISON"
  exec {PENDING_BUILD_MARKER_FD}< "$PENDING_BUILD_MARKER" \
    || { poison_pending_build_marker "cannot hold public pending marker"; \
      fail "无法固定输出目录待发布标记 held FD"; }
  pending_result="$(held_file_identity_and_sha256 "/proc/$$/fd/$PENDING_BUILD_MARKER_FD" \
    "$PENDING_BUILD_MARKER")" \
    || { poison_pending_build_marker "public pending marker identity drifted"; \
      fail "输出目录待发布标记身份不稳定"; }
  IFS=$'\t' read -r PENDING_BUILD_MARKER_IDENTITY PENDING_BUILD_MARKER_SHA256 \
    <<< "$pending_result"
  [ "$PENDING_BUILD_MARKER_SHA256" = "$staged_sha" ] \
    || fail "构建成功待发布标记在转移到输出目录时发生变化"
  require_pending_build_marker_identity "after public staging"
  require_candidate_deb_fixed
}

poison_candidate_artifacts() {
  local reason="$1"
  [ -d "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ] || return 0
  if [ ! -e "$CANDIDATE_ARTIFACT_POISON" ] \
      && [ ! -L "$CANDIDATE_ARTIFACT_POISON" ]; then
    (umask 077; set -o noclobber; {
      printf 'candidate_artifacts_poisoned=1\n'
      printf 'reason=%s\n' "$reason"
    } > "$CANDIDATE_ARTIFACT_POISON") 2>/dev/null || true
  fi
}

candidate_deb_identity_matches() {
  local deb_result sidecar_result report_result deb_identity deb_sha sidecar_identity sidecar_sha
  local report_identity report_sha
  [ "${CANDIDATE_DEB_FIXED:-0}" = 1 ] \
    && [ -n "${CANDIDATE_DEB_FD:-}" ] \
    && [ -n "${CANDIDATE_DEB_SIDECAR_FD:-}" ] \
    && [ -n "${CANDIDATE_DEB_IDENTITY:-}" ] \
    && [ -n "${CANDIDATE_DEB_SHA256:-}" ] \
    && [ -n "${CANDIDATE_DEB_SIDECAR_IDENTITY:-}" ] \
    && [ -n "${CANDIDATE_DEB_SIDECAR_EXPECTED_SHA256:-}" ] \
    || return 1
  deb_result="$(held_file_identity_and_sha256 "/proc/$$/fd/$CANDIDATE_DEB_FD" \
    "$CANDIDATE_DEB_PATH")" \
    || return 1
  sidecar_result="$(held_file_identity_and_sha256 \
    "/proc/$$/fd/$CANDIDATE_DEB_SIDECAR_FD" "$CANDIDATE_DEB_SIDECAR_PATH")" \
    || return 1
  IFS=$'\t' read -r deb_identity deb_sha <<< "$deb_result"
  IFS=$'\t' read -r sidecar_identity sidecar_sha <<< "$sidecar_result"
  [ "$deb_identity" = "$CANDIDATE_DEB_IDENTITY" ] \
    && [ "$deb_sha" = "$CANDIDATE_DEB_SHA256" ] \
    && [ "$sidecar_identity" = "$CANDIDATE_DEB_SIDECAR_IDENTITY" ] \
    && [ "$sidecar_sha" = "$CANDIDATE_DEB_SIDECAR_SHA256" ] \
    && [ "$sidecar_sha" = "$CANDIDATE_DEB_SIDECAR_EXPECTED_SHA256" ] \
    || return 1
  if [ -n "${BUILD_REPORT_FD:-}" ]; then
    report_result="$(held_file_identity_and_sha256 "/proc/$$/fd/$BUILD_REPORT_FD" \
      "$BUILD_REPORT")" \
      || return 1
    IFS=$'\t' read -r report_identity report_sha <<< "$report_result"
    [ "$report_identity" = "$BUILD_REPORT_IDENTITY" ] \
      && [ "$report_sha" = "$BUILD_REPORT_SHA256" ]
  fi
}

close_candidate_artifact_fds() {
  if [ -n "${BUILD_REPORT_FD:-}" ]; then
    exec {BUILD_REPORT_FD}<&- || true
    BUILD_REPORT_FD=""
  fi
  if [ -n "${CANDIDATE_DEB_SIDECAR_FD:-}" ]; then
    exec {CANDIDATE_DEB_SIDECAR_FD}<&- || true
    CANDIDATE_DEB_SIDECAR_FD=""
  fi
  if [ -n "${CANDIDATE_DEB_FD:-}" ]; then
    exec {CANDIDATE_DEB_FD}<&- || true
    CANDIDATE_DEB_FD=""
  fi
}

require_candidate_deb_fixed() {
  if ! candidate_deb_identity_matches; then
    poison_candidate_artifacts "candidate DEB or sidecar identity/content drifted"
    fail "候选 DEB/sidecar held-FD 身份或 exact 内容漂移"
  fi
}

build_glibc() {
  getconf GNU_LIBC_VERSION 2>/dev/null || ldd --version 2>/dev/null | head -1 || printf 'unknown\n'
}

write_build_report() {
  local source_name deb_name source_line deb_line previous_umask had_noclobber
  local report_write_fd report_result
  require_candidate_deb_fixed
  source_name="$(basename "$SRC_ARCHIVE")"
  deb_name="taiji-agent_$VERSION"_amd64.deb
  if [ -n "${SOURCE_ARCHIVE_FD:-}" ]; then
    source_line="$(sha256sum "/proc/$$/fd/$SOURCE_ARCHIVE_FD")"
  else
    source_line="$(sha256sum "$SRC_ARCHIVE")"
  fi
  deb_line="$(cd "$OUTPUT_DIR" && sha256sum "$deb_name")"
  [ ! -e "$BUILD_REPORT" ] && [ ! -L "$BUILD_REPORT" ] \
    || { poison_candidate_artifacts "build report path was occupied"; \
      fail "构建报告路径已被占用，未覆盖外来文件"; }
  previous_umask="$(umask)"
  had_noclobber=0
  [[ -o noclobber ]] && had_noclobber=1
  umask 077
  set -o noclobber
  if ! exec {report_write_fd}> "$BUILD_REPORT"; then
    [ "$had_noclobber" = 1 ] || set +o noclobber
    umask "$previous_umask"
    poison_candidate_artifacts "build report no-clobber open failed"
    fail "构建报告无法以 no-clobber 方式创建"
  fi
  [ "$had_noclobber" = 1 ] || set +o noclobber
  umask "$previous_umask"
  {
    printf '太极 Agent 单一 DEB 构建报告\n'
    printf '生成时间：%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    printf '源码包：%s\n' "$source_name"
    printf '源码包 SHA256：%s\n' "$source_line"
    printf '候选 DEB：%s\n' "$deb_name"
    printf 'DEB SHA256：%s\n' "$deb_line"
    printf 'compatibility policy id：%s\n' "$POLICY_ID"
    printf 'compatibility policy SHA256：%s\n' "$POLICY_SHA256"
    printf 'ELF ABI audit SHA256：%s\n' "$ELF_ABI_AUDIT_SHA256"
    printf '安装态验收绑定 SHA256：%s\n' "$ACCEPTANCE_BINDING_SHA256"
    printf '安装态验收工具 manifest SHA256：%s\n' "$ACCEPTANCE_TOOLS_MANIFEST_SHA256"
    printf '安装态验收入口 SHA256：%s\n' "$ACCEPTANCE_ENTRYPOINT_SHA256"
    printf '安装态 release manifest SHA256：%s\n' "$INSTALLED_RELEASE_MANIFEST_SHA256"
    printf '图标集合 SHA256：%s\n' "$ICON_SET_SHA256"
    printf '正式构建测试：%s log=%s SHA256=%s\n' "$FORMAL_BUILD_TESTS_STATUS" "$FORMAL_BUILD_TESTS_LOG_BASENAME" "$FORMAL_BUILD_TESTS_LOG_SHA256"
    printf 'Python 依赖锁状态：%s\n' "$PYTHON_DEPENDENCY_LOCK_STATUS"
    printf 'Python lock：%s %s\n' "$PYTHON_LOCK_BASENAME" "$PYTHON_LOCK_SHA256"
    printf 'Python：%s %s\n' "$PYTHON_VERSION" "$PYTHON_EXECUTABLE_SHA256"
    printf 'uv：%s archive=%s executable=%s\n' "$UV_VERSION" "$UV_ARCHIVE_SHA256" "$UV_EXECUTABLE_SHA256"
    printf 'Node.js：%s archive=%s executable=%s\n' "$NODE_VERSION" "$NODE_ARCHIVE_SHA256" "$NODE_EXECUTABLE_SHA256"
    printf 'Electron：%s archive=%s executable=%s\n' "$ELECTRON_VERSION" "$ELECTRON_ARCHIVE_SHA256" "$ELECTRON_EXECUTABLE_SHA256"
    printf 'Maintainer（源码 policy 固定）：%s\n' "$POLICY_MAINTAINER"
    printf '客户交付边界：发布预检通过后只交付一个逐字节固定的 amd64 DEB，不附带第二个安装包或 apt 仓库。\n'
    printf '候选 DEB 固定后不再下载运行时依赖。\n'
  } >&"$report_write_fd"
  chmod 0644 "/proc/$$/fd/$report_write_fd"
  fsync_held_file "/proc/$$/fd/$report_write_fd" \
    || { poison_candidate_artifacts "build report fsync failed"; fail "构建报告持久化失败"; }
  exec {BUILD_REPORT_FD}< "/proc/$$/fd/$report_write_fd" \
    || fail "无法固定构建报告 held read FD"
  exec {report_write_fd}>&-
  report_result="$(held_file_identity_and_sha256 "/proc/$$/fd/$BUILD_REPORT_FD" "$BUILD_REPORT")" \
    || { poison_candidate_artifacts "build report identity drifted"; fail "构建报告身份不稳定"; }
  IFS=$'\t' read -r BUILD_REPORT_IDENTITY BUILD_REPORT_SHA256 <<< "$report_result"
  fsync_directory "$OUTPUT_DIR" \
    || { poison_candidate_artifacts "build report output directory fsync failed"; \
      fail "构建报告输出目录持久化失败"; }
  require_candidate_deb_fixed
  ok "构建报告已生成：$BUILD_REPORT"
}


archive_stale_acceptance_staging() {
  local current_uid path archive_root archive_dir index=0
  local -a stale_paths=()
  current_uid="$(id -u)"
  while IFS= read -r -d '' path; do
    [ -d "$path" ] && [ ! -L "$path" ] \
      || fail "验收工具临时残留不是可安全归档的实体目录：$path"
    python3 - "$path" "$current_uid" <<'PY' \
      || fail "验收工具临时残留含不安全节点，拒绝自动归档：$path"
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected_uid = int(sys.argv[2])
for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
    current_path = Path(current)
    current_stat = current_path.lstat()
    if not stat.S_ISDIR(current_stat.st_mode) or current_path.is_symlink() or current_stat.st_uid != expected_uid:
        raise SystemExit("unsafe staging directory")
    for name in directories + filenames:
        candidate = current_path / name
        metadata = candidate.lstat()
        if candidate.is_symlink() or metadata.st_uid != expected_uid:
            raise SystemExit("unsafe staging node")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise SystemExit("unsafe staging file")
PY
    stale_paths+=("$path")
  done < <(find "$SCRIPT_DIR" -mindepth 1 -maxdepth 1 -name '.验收工具.tmp-*' -print0)

  [ "${#stale_paths[@]}" -gt 0 ] || return 0
  archive_root="$SCRIPT_DIR/旧版备份"
  [ ! -L "$archive_root" ] || fail "旧版备份目录不能是符号链接：$archive_root"
  install -d -m 0755 "$archive_root"
  for path in "${stale_paths[@]}"; do
    index=$((index + 1))
    archive_dir="$archive_root/验收工具临时残留-$(date -u '+%Y%m%dT%H%M%SZ')-$$-$index"
    mkdir -m 0700 -- "$archive_dir" \
      || fail "无法创建验收工具临时残留归档：$archive_dir"
    mv -- "$path" "$archive_dir/${path##*/}" \
      || fail "无法归档验收工具临时残留：$path"
    ok "已自动归档上次中断的验收工具临时目录：$archive_dir"
  done
}

publish_target_acceptance_tools() {
  local target="$1" target_staging="$2"
  local archive_root archive_dir
  ACCEPTANCE_TARGET="$target"
  if [ -e "$target" ] || [ -L "$target" ]; then
    archive_root="$SCRIPT_DIR/旧版备份"
    [ ! -L "$archive_root" ] || fail "旧版备份目录不能是符号链接：$archive_root"
    install -d -m 0755 "$archive_root"
    archive_dir="$archive_root/验收工具重试-$(date -u '+%Y%m%dT%H%M%SZ')-$$"
    mkdir -m 0700 -- "$archive_dir" \
      || fail "无法创建验收工具重试备份：$archive_dir"
    ACCEPTANCE_ARCHIVE_DIR="$archive_dir"
    ACCEPTANCE_BACKUP="$archive_dir/验收工具"
    mv -- "$target" "$ACCEPTANCE_BACKUP"
  fi
  mv -- "$target_staging" "$target"
  ACCEPTANCE_STAGING=""
  ACCEPTANCE_BACKUP=""
  ACCEPTANCE_ARCHIVE_DIR=""
  ACCEPTANCE_TARGET=""
}

stage_target_acceptance_tools() {
  require_candidate_deb_fixed
  local target="$SCRIPT_DIR/验收工具"
  local target_staging="$SCRIPT_DIR/.验收工具.tmp-$$"
  local management="$target_staging/management"
  local driver="$SRC_DIR/tools/taiji-desktop-acceptance/run-installed-electron-acceptance.js"
  local assembler="$SRC_DIR/tools/taiji-desktop-acceptance/assemble-target-evidence.py"
  local install_observer="$SRC_DIR/tools/taiji-desktop-acceptance/observe-single-deb-install.py"
  local certification_matrix="$SRC_DIR/packaging/linux/certification-matrix.json"
  local certification_set_assembler="$SRC_DIR/scripts/assemble-taiji-certification-set.py"
  local validator="$SRC_DIR/scripts/validate-taiji-release-evidence.py"
  local challenge_helper="$SRC_DIR/scripts/taiji-challenge-envelope.py"
  local public_key="$SRC_DIR/tools/taiji-release-evidence/signing-public.pem"
  local public_fingerprint expected_fingerprint
  expected_fingerprint="839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
  archive_stale_acceptance_staging
  ACCEPTANCE_STAGING="$target_staging"
  [ ! -e "$target_staging" ] && [ ! -L "$target_staging" ] \
    || fail "验收工具临时目录已存在：$target_staging"

  info "收集目标终端真实 Electron 桌面 App 验收工具"
  [ -f "$driver" ] && [ ! -L "$driver" ] || fail "源码缺少桌面 App 验收驱动：$driver"
  [ -f "$assembler" ] && [ ! -L "$assembler" ] || fail "源码缺少目标证据组装器：$assembler"
  [ -f "$install_observer" ] && [ ! -L "$install_observer" ] || fail "源码缺少单 DEB 安装前观察器：$install_observer"
  [ -f "$certification_matrix" ] && [ ! -L "$certification_matrix" ] || fail "源码缺少认证类别矩阵：$certification_matrix"
  [ -f "$certification_set_assembler" ] && [ ! -L "$certification_set_assembler" ] || fail "源码缺少认证集组装器：$certification_set_assembler"
  [ -f "$validator" ] && [ ! -L "$validator" ] || fail "源码缺少发布证据校验器：$validator"
  [ -f "$challenge_helper" ] && [ ! -L "$challenge_helper" ] || fail "源码缺少 challenge envelope helper：$challenge_helper"
  [ -f "$public_key" ] && [ ! -L "$public_key" ] || fail "源码缺少发布证据验签公钥：$public_key"
  run_build_node --check "$driver" >/dev/null \
    || fail "桌面 App 验收驱动 JavaScript 语法检查失败"
  python3 - "$assembler" "$install_observer" "$validator" "$certification_set_assembler" "$challenge_helper" <<'PY' || fail "目标证据 Python 工具语法检查失败"
import sys
from pathlib import Path

for raw in sys.argv[1:]:
    path = Path(raw)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
PY
  public_fingerprint="$(openssl pkey -pubin -in "$public_key" -outform DER 2>/dev/null | openssl dgst -sha256 -r | awk '{print $1}')"
  [ "$public_fingerprint" = "$expected_fingerprint" ] || fail "目标验收验签公钥 fingerprint 不匹配"

  install -d -m 0755 "$target_staging"
  install -m 0644 "$driver" "$target_staging/run-installed-electron-acceptance.js"
  install -m 0644 "$assembler" "$target_staging/assemble-target-evidence.py"
  install -m 0644 "$install_observer" "$target_staging/observe-single-deb-install.py"
  install -m 0644 "$certification_matrix" "$target_staging/certification-matrix.json"
  install -m 0755 "$certification_set_assembler" "$target_staging/assemble-taiji-certification-set.py"
  install -m 0644 "$validator" "$target_staging/validate-taiji-release-evidence.py"
  install -m 0644 "$challenge_helper" "$target_staging/taiji-challenge-envelope.py"
  install -m 0644 "$public_key" "$target_staging/signing-public.pem"
  # These are management-plane helpers for the copied 02 wrapper.  They are
  # deliberately outside the customer DEB and are never staged into
  # 生成的安装包/; a delivery directory can therefore run 02 without a
  # source checkout while keeping the customer payload a single DEB.
  install -d -m 0755 "$management"
  install -m 0755 "$SRC_DIR/packaging/linux/deb/taiji-silent-deploy.sh" "$management/taiji-silent-deploy.sh"
  install -m 0644 "$SRC_DIR/packaging/linux/deployment_receipt.py" "$management/deployment_receipt.py"
  install -m 0644 "$SRC_DIR/packaging/linux/upgrade_transaction.py" "$management/upgrade_transaction.py"
  install -m 0644 "$SRC_DIR/packaging/linux/upgrade-data-contract.json" "$management/upgrade-data-contract.json"
  install -m 0644 "$SRC_DIR/packaging/linux/compatibility_policy.py" "$management/compatibility_policy.py"
  install -m 0644 "$SRC_DIR/packaging/linux/compatibility-policy.json" "$management/compatibility-policy.json"
  install -m 0644 "$SRC_DIR/scripts/validate-taiji-release-evidence.py" "$management/validate-taiji-release-evidence.py"

  if [ -e "$target" ] || [ -L "$target" ]; then
    [ -d "$target" ] && [ ! -L "$target" ] \
      || fail "验收工具目录不是可信实体目录，未覆盖：$target"
    python3 - "$target" "$(id -u)" <<'PY' \
      || fail "验收工具目录含未知或不安全内容，未覆盖：$target"
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected_uid = int(sys.argv[2])
expected_files = {
    "run-installed-electron-acceptance.js",
    "assemble-target-evidence.py",
    "observe-single-deb-install.py",
    "certification-matrix.json",
    "assemble-taiji-certification-set.py",
    "validate-taiji-release-evidence.py",
    "taiji-challenge-envelope.py",
    "signing-public.pem",
    "management/taiji-silent-deploy.sh",
    "management/deployment_receipt.py",
    "management/upgrade_transaction.py",
    "management/upgrade-data-contract.json",
    "management/compatibility_policy.py",
    "management/compatibility-policy.json",
    "management/validate-taiji-release-evidence.py",
}
expected_dirs = {"management"}
actual_files = set()
actual_dirs = set()
for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
    current_path = Path(current)
    for name in directories:
        path = current_path / name
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink() or metadata.st_uid != expected_uid:
            raise SystemExit("unsafe directory: " + relative)
        actual_dirs.add(relative)
    for name in filenames:
        path = current_path / name
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or metadata.st_nlink != 1
            or metadata.st_uid != expected_uid
        ):
            raise SystemExit("unsafe file: " + relative)
        actual_files.add(relative)
if actual_dirs != expected_dirs or actual_files != expected_files:
    raise SystemExit("验收工具目录含未知或缺失条目")
PY
  fi
  publish_target_acceptance_tools "$target" "$target_staging"
  ok "目标终端桌面 App 验收工具已收集：$target"
}

cleanup_delivery_build_cache() {
  local agent_venv
  require_candidate_deb_fixed
  info "清理构建根中的制包工具缓存"
  [ -n "$BUILD_ROOT" ] && [ "$TOOL_ROOT" = "$BUILD_ROOT/.build-tools" ] \
    || fail "制包工具根未绑定到受控构建根：$TOOL_ROOT"
  close_build_node_runtime_fds
  require_owned_build_root
  restore_owned_build_root_directory_writes
  rm -rf -- "$TOOL_ROOT" || fail "无法清理制包工具缓存：$TOOL_ROOT"
  [ ! -e "$TOOL_ROOT" ] || fail "制包缓存仍然存在：$TOOL_ROOT"
  agent_venv="$(source_agent_dir)/venv"
  [ -d "$agent_venv" ] && [ ! -L "$agent_venv" ] \
    || fail "Agent 构建 venv 不是可安全清理的真实目录：$agent_venv"
  rm -rf -- "$agent_venv" || fail "无法清理 Agent 构建 venv：$agent_venv"
  [ ! -e "$agent_venv" ] && [ ! -L "$agent_venv" ] \
    || fail "Agent 构建 venv 清理后仍然存在：$agent_venv"
  ok "构建根制包工具缓存已清理"
}

normalize_delivery_permissions() {
  local unsafe_node
  require_candidate_deb_fixed
  info "归一化交付目录权限"
  unsafe_node="$(find "$SCRIPT_DIR" -xdev -mindepth 1 \( -type l -o \( -type f -links +1 \) \) -print -quit)"
  [ -z "$unsafe_node" ] || fail "交付目录含符号链接或硬链接，拒绝修改其权限：$unsafe_node"
  chmod go-w "$SCRIPT_DIR"
  find "$SCRIPT_DIR" -xdev -mindepth 1 \( -type d -o -type f \) \
    -perm /022 -exec chmod go-w -- {} +
  for script in \
    "$SCRIPT_DIR/00_制包机_生成离线交付包.sh" \
    "$SCRIPT_DIR/01_制包机_发布预检.sh" \
    "$SCRIPT_DIR/02_目标终端_安装并验证.sh" \
    "$SCRIPT_DIR/03_目标终端_导出诊断报告.sh" \
    "$SCRIPT_DIR/04_目标终端_桌面App验收并导出证据.sh" \
    "$SCRIPT_DIR/99_本机_准备制包输入包.sh"; do
    [ -f "$script" ] || fail "交付目录缺少脚本：$script"
    chmod 0755 "$script"
  done
  ok "交付目录权限已归一化"
}

cleanup_temporary_build_root() {
  info "清理最终预检使用完毕的临时构建工作区"
  if [ -e "$BUILD_ROOT" ] || [ -L "$BUILD_ROOT" ]; then
    require_owned_build_root
    restore_owned_build_root_directory_writes
    rm -rf -- "$BUILD_ROOT" || fail "无法以当前用户清理专用构建工作区：$BUILD_ROOT"
  fi
  [ ! -e "$BUILD_ROOT" ] && [ ! -L "$BUILD_ROOT" ] \
    || fail "临时构建工作区仍然存在：$BUILD_ROOT"
  ok "临时构建工作区已清理"
}

apt_source_summary() {
  awk '
    /^[[:space:]]*deb[[:space:]]/ {
      out = out $0 "; "
      count += 1
      if (count >= 5) {
        print out
        exit
      }
    }
    END {
      if (count > 0 && count < 5) {
        print out
      }
    }
  ' /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null || true
}

main() {
  validate_formal_uv_contract
  initialize_build_logging
  set_stage "制包机预检"
  preflight
  set_stage "归档上次中断产物"
  archive_previous_build_outputs
  set_stage "安装制包依赖"
  install_build_dependencies
  set_stage "校验制包命令依赖闭包"
  verify_build_command_contract
  set_stage "校验制包机输入包三件套"
  verify_builder_input_package
  set_stage "选择安全构建根并配置临时目录"
  select_build_root
  set_stage "源码包发布预检"
  prepare_source_release
  set_stage "解压源码并加载 canonical policy"
  unpack_source
  load_source_controlled_policy
  set_stage "校验可信系统构建工具"
  verify_trusted_system_tools
  set_stage "准备 Python 构建工具"
  ensure_uv
  ensure_python
  set_stage "准备 Node/Electron 构建工具"
  ensure_node
  seal_build_node_runtime
  set_stage "构建运行时和 DEB"
  build_runtime_and_deb
  set_stage "收集并绑定候选产物"
  collect_artifacts
  set_stage "使用固定工具链执行正式构建测试"
  prepare_formal_build_test_dependencies
  run_formal_build_tests_direct
  write_pending_build_marker
  set_stage "生成 manifest 和报告"
  write_build_report
  set_stage "收集目标终端桌面 App 验收工具"
  stage_target_acceptance_tools
  set_stage "清理制包缓存"
  cleanup_delivery_build_cache
  set_stage "归一化交付权限"
  normalize_delivery_permissions
  set_stage "最终发布预检"
  require_candidate_deb_fixed
  verify_build_source_integrity
  require_formal_package_manifest_identity "before final release preflight"
  require_formal_build_test_log_identity "before final release preflight"
  TAIJI_EXTRACTED_SOURCE_ROOT="$SRC_DIR" \
    TAIJI_BUILD_MARKER_PATH="$PENDING_BUILD_MARKER" \
    TAIJI_EXPECT_PUBLISHED_BUILD_MARKER=0 \
    TAIJI_RELEASE_REQUIRE_ARTIFACTS=1 \
    TAIJI_RELEASE_SKIP_GIT_CHECK=1 \
  run_release_preflight "$SRC_DIR"
  require_candidate_deb_fixed
  require_formal_package_manifest_identity "after final release preflight"
  require_formal_build_test_log_identity "after final release preflight"
  stage_pending_build_marker_for_publication
  set_stage "清理临时构建工作区"
  cleanup_temporary_build_root
  set_stage "发布构建成功标记"
  printf '\n[INFO] 全部门禁与临时工作区清理已完成，正在原子发布单一 DEB 候选：\n'
  printf '%s\n' "$OUTPUT_DIR/taiji-agent_${VERSION}_amd64.deb"
  printf '请将该 DEB 和当前验收工具用于干净目标机验收；此状态尚不代表真实麒麟/统信目标机已验收。\n'
  printf '\n日志：%s\n' "$LOG_FILE"
  require_formal_build_test_log_identity "before published build marker"
  require_formal_package_manifest_identity "before published build marker"
  require_candidate_deb_fixed
  publish_build_success_marker
  require_published_build_marker_identity "main after publish"
  require_formal_package_manifest_identity "main after publish"
  require_formal_build_test_log_identity "after published build marker"
  require_candidate_deb_fixed
  close_candidate_artifact_fds
  close_formal_package_manifest_fds
  close_formal_build_test_log_fds
}

main "$@"
