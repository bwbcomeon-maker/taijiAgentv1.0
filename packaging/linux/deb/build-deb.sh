#!/bin/bash -p
# Build the single policy-bound offline Taiji Agent amd64 DEB.
set -euo pipefail
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONBREAKPOINT PYTHONUSERBASE
unset LD_PRELOAD LD_LIBRARY_PATH DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
unset OPENSSL_CONF OPENSSL_MODULES

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
LAB_DIR="$REPO_ROOT/hermes-local-lab"
SOURCE_AGENT_DIR="$LAB_DIR/sources/hermes-agent"
SOURCE_WEB_DIR="$LAB_DIR/sources/hermes-webui"
APP_DIR="$REPO_ROOT/apps/taiji-desktop"
ELECTRON_BIN="$APP_DIR/node_modules/electron/dist/electron"
DESKTOP_FILE="$REPO_ROOT/packaging/linux/taiji-agent.desktop"
APPSTREAM_FILE="$REPO_ROOT/packaging/linux/taiji-agent.metainfo.xml"
ICON_VALIDATOR="$REPO_ROOT/packaging/linux/validate_icon_assets.py"
DEFAULT_CONFIG="$LAB_DIR/config/taiji-default-config.yaml"
VERSION_FILE="$REPO_ROOT/VERSION"
POLICY_FILE="$REPO_ROOT/packaging/linux/compatibility-policy.json"
POLICY_HELPER="$REPO_ROOT/packaging/linux/compatibility_policy.py"
PAYLOAD_CONTRACT="$REPO_ROOT/packaging/linux/payload-contract.json"
PAYLOAD_VERIFIER="$REPO_ROOT/packaging/linux/verify-payload.py"
RUNTIME_STAGER="$REPO_ROOT/packaging/linux/stage-runtime-components.py"
PYTHON_RUNTIME_STAGER="$REPO_ROOT/packaging/linux/stage-python-runtime.py"
ELECTRON_RUNTIME_STAGER="$REPO_ROOT/packaging/linux/stage-electron-runtime.py"
DESKTOP_JS_STAGER="$REPO_ROOT/packaging/linux/stage-desktop-js-closure.js"
PRIVATE_LIB_STAGER="$REPO_ROOT/packaging/linux/stage-private-libraries.py"
ELF_AUDITOR="$REPO_ROOT/packaging/linux/audit-elf-closure.py"
LOCK_CONTRACT_HELPER="$REPO_ROOT/packaging/linux/verify-python-lock-contract.py"
SOURCE_INTEGRITY_HELPER="$REPO_ROOT/packaging/linux/source-archive-integrity.py"
PREINST_RENDERER="$SCRIPT_DIR/render-preinst.py"
TRUSTED_GIT="$REPO_ROOT/scripts/taiji-trusted-git"
ACCEPTANCE_HELPER="$REPO_ROOT/packaging/linux/acceptance_tools_manifest.py"
ACCEPTANCE_RUNNER_SOURCE="$REPO_ROOT/packaging/linux/acceptance_runner.py"
ACCEPTANCE_ENTRYPOINT_SOURCE="$REPO_ROOT/packaging/linux/bin/taiji-agent-acceptance"
ACCEPTANCE_LAUNCHER_SOURCE="$REPO_ROOT/taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh"
PACKAGED_NODE_ROOT="${TAIJI_PACKAGED_NODE_ROOT:-}"
PACKAGED_NODE_EXECUTABLE="${TAIJI_PACKAGED_NODE_EXECUTABLE:-}"
PRIVATE_LIBRARY_SYSROOT="${TAIJI_PRIVATE_LIBRARY_SYSROOT:-/usr/lib/x86_64-linux-gnu}"
SOURCE_COMMIT="${TAIJI_SOURCE_COMMIT:-}"
ELECTRON_ARCHIVE="${TAIJI_ELECTRON_ARCHIVE:-}"
ELECTRON_ARCHIVE_FD="${TAIJI_ELECTRON_ARCHIVE_FD:-}"
ELECTRON_ARCHIVE_BASENAME="${TAIJI_ELECTRON_ARCHIVE_BASENAME:-}"
PACKAGED_NODE_VERSION="22.23.1"
PACKAGED_NODE_ARCHIVE_SHA256="9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"
PINNED_NODE_EXECUTABLE_SHA256="93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068"
PINNED_UV_VERSION="0.12.2"
PINNED_UV_ARCHIVE_SHA256="d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4"
PINNED_UV_EXECUTABLE_SHA256="72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2"
PINNED_PYTHON_VERSION="3.11.15"
PINNED_PYTHON_ARCHIVE_SHA256="2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a"
PINNED_PYTHON_EXECUTABLE_SHA256="5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba"
PINNED_ELECTRON_EXECUTABLE_SHA256="c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d"
PINNED_LOCK_CONTRACT_HELPER_SHA256="fca76118874d3846f1bddf304de0159160beff8467bef0870c3636858dedb9e6"
PINNED_SOURCE_INTEGRITY_HELPER_SHA256="eaebadbe2f86d76d09f19ed210ad407e5926a242c46f53fb89e26253db8d8d7a"
SOURCE_ARCHIVE_PATH="${TAIJI_SOURCE_ARCHIVE_PATH:-}"
SOURCE_INVENTORY_PATH="${TAIJI_SOURCE_INVENTORY_PATH:-}"
SOURCE_ARCHIVE_FD="${TAIJI_SOURCE_ARCHIVE_FD:-}"
SOURCE_ARCHIVE_BASENAME="${TAIJI_SOURCE_ARCHIVE_BASENAME:-}"
SOURCE_INVENTORY_FD="${TAIJI_SOURCE_INVENTORY_FD:-}"
SOURCE_INVENTORY_BASENAME="${TAIJI_SOURCE_INVENTORY_BASENAME:-}"
SOURCE_INVENTORY_SHA256="${TAIJI_SOURCE_INVENTORY_SHA256:-}"
PYTHON_DEPENDENCY_LOCK_STATUS="${TAIJI_PYTHON_DEPENDENCY_LOCK_STATUS:-}"
PYTHON_LOCK_BASENAME="${TAIJI_PYTHON_LOCK_BASENAME:-}"
PYTHON_LOCK_SHA256="${TAIJI_PYTHON_LOCK_SHA256:-}"
PYTHON_ARCHIVE_PATH="${TAIJI_PYTHON_ARCHIVE_PATH:-}"
PYTHON_ARCHIVE_SHA256="${TAIJI_PYTHON_ARCHIVE_SHA256:-}"
EXPECTED_PYTHON_VERSION="${TAIJI_PYTHON_VERSION:-}"
EXPECTED_PYTHON_EXECUTABLE="${TAIJI_PYTHON_EXECUTABLE:-}"
EXPECTED_AGENT_PYTHON_SYMLINK_TARGET="${TAIJI_AGENT_PYTHON_SYMLINK_TARGET:-}"
EXPECTED_PYTHON_EXECUTABLE_SHA256="${TAIJI_PYTHON_EXECUTABLE_SHA256:-}"
UV_EXECUTABLE="${TAIJI_UV_EXECUTABLE:-}"
UV_ARCHIVE_PATH="${TAIJI_UV_ARCHIVE_PATH:-}"
UV_VERSION="${TAIJI_UV_VERSION:-}"
UV_ARCHIVE_SHA256="${TAIJI_UV_ARCHIVE_SHA256:-}"
UV_EXECUTABLE_SHA256="${TAIJI_UV_EXECUTABLE_SHA256:-}"
NODE_ARCHIVE_PATH="${TAIJI_NODE_ARCHIVE_PATH:-}"
UV_ARCHIVE_SNAPSHOT_FD=""
PYTHON_ARCHIVE_SNAPSHOT_FD=""
NODE_ARCHIVE_SNAPSHOT_FD=""
PACKAGED_NODE_EXECUTABLE_FD=""
PYTHON_VERSION=""
PYTHON_EXECUTABLE_SHA256=""
NODE_VERSION=""
NODE_ARCHIVE_SHA256=""
NODE_EXECUTABLE_SHA256=""
ELECTRON_VERSION=""
ELECTRON_ARCHIVE_SHA256=""
ISSUER_PUBLIC_KEY_FINGERPRINT="2dcff4f2b5e6f7a5e7e3f730e2f4446ad3265964431f614de7550265f7628b35"

[ -f "$VERSION_FILE" ] || { echo "Missing product VERSION: $VERSION_FILE" >&2; exit 1; }
VERSION="$(tr -d '\r\n' < "$VERSION_FILE")"
printf '%s\n' "$VERSION" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' || {
  echo "Invalid product VERSION: $VERSION" >&2
  exit 1
}
if [ -n "${TAIJI_AGENT_VERSION:-}" ] && [ "$TAIJI_AGENT_VERSION" != "$VERSION" ]; then
  echo "TAIJI_AGENT_VERSION must match root VERSION ($VERSION)" >&2
  exit 1
fi

ARCH="amd64"
BUILD_ROOT="$REPO_ROOT/runtime/package-build/deb"
PKG_ROOT="$BUILD_ROOT/root"
INSTALL_ROOT="$PKG_ROOT/opt/taiji-agent"
AGENT_RUNTIME="$INSTALL_ROOT/runtime/agent"
WEB_RUNTIME="$INSTALL_ROOT/runtime/web"
DESKTOP_RUNTIME="$INSTALL_ROOT/apps/taiji-desktop"
OUT_DIR="$REPO_ROOT/packages/麒麟操作系统安装包"
OUT_DEB="$OUT_DIR/taiji-agent_${VERSION}_${ARCH}.deb"
ARCHIVE_DIR="$OUT_DIR/旧版本归档"
POLICY_INSTALL_PATH="$INSTALL_ROOT/resources/linux-compatibility-policy.json"
LAUNCH_MANIFEST_PATH="$INSTALL_ROOT/resources/taiji-release-manifest.json"
ACCEPTANCE_ROOT="$INSTALL_ROOT/libexec/target-acceptance"
ACCEPTANCE_TOOLS_ROOT="$ACCEPTANCE_ROOT/验收工具"
ACCEPTANCE_BINDING_PATH="$INSTALL_ROOT/resources/taiji-acceptance-binding.json"
ABI_REPORT_PATH="$INSTALL_ROOT/resources/elf-abi-audit.json"
ABI_BUILD_REPORT="$BUILD_ROOT/elf-abi-audit.json"
PRIVATE_STAGE_REPORT="$BUILD_ROOT/private-library-stage.json"
MANIFEST_PATH="$OUT_DIR/taiji-package-manifest.json"
ICON_SET_SHA256=""
ACCEPTANCE_BINDING_SHA256=""
ACCEPTANCE_TOOLS_MANIFEST_SHA256=""
ACCEPTANCE_ENTRYPOINT_SHA256=""
INSTALLED_RELEASE_MANIFEST_SHA256=""
POLICY_SHA256=""
POLICY_ID=""
TAIJI_PACKAGE_NAME=""
TAIJI_PACKAGE_ARCHITECTURE=""
TAIJI_PACKAGE_MAINTAINER=""
TAIJI_DEBIAN_DEPENDS=""
TAIJI_GLIBC_MIN=""

fail() { echo "$*" >&2; exit 1; }
warn() { echo "Warning: $*" >&2; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"; }

adopt_sealed_build_inputs() {
  [ -n "$UV_ARCHIVE_PATH" ] \
    && [ -n "$PYTHON_ARCHIVE_PATH" ] \
    && [ -n "$NODE_ARCHIVE_PATH" ] \
    && [ -n "$PACKAGED_NODE_EXECUTABLE" ] \
    || fail "sealed tool archives and Node executable are required"
  exec {UV_ARCHIVE_SNAPSHOT_FD}< "$UV_ARCHIVE_PATH" \
    || fail "cannot adopt the sealed uv archive"
  exec {PYTHON_ARCHIVE_SNAPSHOT_FD}< "$PYTHON_ARCHIVE_PATH" \
    || fail "cannot adopt the sealed Python archive"
  exec {NODE_ARCHIVE_SNAPSHOT_FD}< "$NODE_ARCHIVE_PATH" \
    || fail "cannot adopt the sealed Node archive"
  exec {PACKAGED_NODE_EXECUTABLE_FD}< "$PACKAGED_NODE_EXECUTABLE" \
    || fail "cannot adopt the sealed Node executable"
  UV_ARCHIVE_PATH="/proc/$$/fd/$UV_ARCHIVE_SNAPSHOT_FD"
  PYTHON_ARCHIVE_PATH="/proc/$$/fd/$PYTHON_ARCHIVE_SNAPSHOT_FD"
  NODE_ARCHIVE_PATH="/proc/$$/fd/$NODE_ARCHIVE_SNAPSHOT_FD"
  PACKAGED_NODE_EXECUTABLE="/proc/$$/fd/$PACKAGED_NODE_EXECUTABLE_FD"
  /usr/bin/python3 -I -B - \
    "$UV_ARCHIVE_PATH" "$PINNED_UV_ARCHIVE_SHA256" 0400 \
    "$PYTHON_ARCHIVE_PATH" "$PINNED_PYTHON_ARCHIVE_SHA256" 0400 \
    "$NODE_ARCHIVE_PATH" "$PACKAGED_NODE_ARCHIVE_SHA256" 0400 \
    "$PACKAGED_NODE_EXECUTABLE" "$PINNED_NODE_EXECUTABLE_SHA256" 0500 <<'PY'
import fcntl
import hashlib
import os
import stat
import sys

required_names = (
    "F_GET_SEALS",
    "F_SEAL_WRITE",
    "F_SEAL_GROW",
    "F_SEAL_SHRINK",
    "F_SEAL_SEAL",
)
missing = [name for name in required_names if not hasattr(fcntl, name)]
if missing:
    raise SystemExit("Linux memfd seal verification unavailable: " + ",".join(missing))
required = (
    fcntl.F_SEAL_WRITE
    | fcntl.F_SEAL_GROW
    | fcntl.F_SEAL_SHRINK
    | fcntl.F_SEAL_SEAL
)
arguments = sys.argv[1:]
if len(arguments) != 12:
    raise SystemExit("sealed input verification argument mismatch")
for offset in range(0, len(arguments), 3):
    path, expected, expected_mode_text = arguments[offset : offset + 3]
    if expected_mode_text not in ("0400", "0500"):
        raise SystemExit("sealed input mode contract is invalid")
    expected_mode = int(expected_mode_text, 8)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise SystemExit("sealed input is not a non-empty regular file")
        if stat.S_IMODE(metadata.st_mode) != expected_mode:
            raise SystemExit("sealed input mode mismatch")
        seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
        if seals & required != required:
            raise SystemExit("sealed input is missing a required seal")
        digest = hashlib.sha256()
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise SystemExit("sealed input is truncated")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise SystemExit("sealed input grew while hashing")
        if digest.hexdigest() != expected:
            raise SystemExit("sealed input SHA256 mismatch")
    finally:
        os.close(descriptor)
PY
}

validate_source_archive_integrity() {
  local helper_sha inventory_sha agent_python actual_target python_real expected_python_real archive_ref inventory_ref
  SOURCE_ARCHIVE_FD="${SOURCE_ARCHIVE_FD:-}"
  SOURCE_INVENTORY_FD="${SOURCE_INVENTORY_FD:-}"
  SOURCE_ARCHIVE_BASENAME="${SOURCE_ARCHIVE_BASENAME:-}"
  SOURCE_INVENTORY_BASENAME="${SOURCE_INVENTORY_BASENAME:-}"
  [ -f "$SOURCE_INTEGRITY_HELPER" ] && [ ! -L "$SOURCE_INTEGRITY_HELPER" ] \
    || fail "source archive integrity helper is missing"
  helper_sha="$(sha256sum "$SOURCE_INTEGRITY_HELPER" | awk '{print $1}')"
  [ "$helper_sha" = "$PINNED_SOURCE_INTEGRITY_HELPER_SHA256" ] \
    || fail "source archive integrity helper is not the reviewed implementation"
  if [ -n "$SOURCE_ARCHIVE_FD" ] || [ -n "$SOURCE_INVENTORY_FD" ]; then
    [ -n "$SOURCE_ARCHIVE_FD" ] && [ -n "$SOURCE_INVENTORY_FD" ] \
      && [ -n "$SOURCE_ARCHIVE_BASENAME" ] && [ -n "$SOURCE_INVENTORY_BASENAME" ] \
      || fail "formal source archive/inventory FD and basename must be provided together"
    [ -z "$SOURCE_ARCHIVE_PATH" ] && [ -z "$SOURCE_INVENTORY_PATH" ] \
      || fail "source path and FD modes are mutually exclusive"
    archive_ref="/proc/self/fd/$SOURCE_ARCHIVE_FD"
    inventory_ref="/proc/self/fd/$SOURCE_INVENTORY_FD"
  else
    [ -f "$SOURCE_ARCHIVE_PATH" ] && [ ! -L "$SOURCE_ARCHIVE_PATH" ] \
      || fail "TAIJI_SOURCE_ARCHIVE_PATH is required"
    [ -f "$SOURCE_INVENTORY_PATH" ] && [ ! -L "$SOURCE_INVENTORY_PATH" ] \
      || fail "TAIJI_SOURCE_INVENTORY_PATH is required"
    archive_ref="$SOURCE_ARCHIVE_PATH"
    inventory_ref="$SOURCE_INVENTORY_PATH"
  fi
  inventory_sha="$(sha256sum "$inventory_ref" | awk '{print $1}')"
  [ "$inventory_sha" = "$SOURCE_INVENTORY_SHA256" ] \
    || fail "source inventory SHA256 mismatch"
  agent_python="$SOURCE_AGENT_DIR/venv/bin/python"
  [ -L "$agent_python" ] || fail "Agent Python must be a symlink"
  actual_target="$(readlink "$agent_python")" \
    || fail "Agent Python symlink target cannot be read"
  [ "$actual_target" = "$EXPECTED_AGENT_PYTHON_SYMLINK_TARGET" ] \
    || fail "Agent Python raw symlink target does not match TAIJI_AGENT_PYTHON_SYMLINK_TARGET"
  case "$actual_target" in
    /*) ;;
    *) fail "TAIJI_AGENT_PYTHON_SYMLINK_TARGET must be absolute" ;;
  esac
  case "$actual_target" in
    *$'\n'*|*$'\r'*) fail "TAIJI_AGENT_PYTHON_SYMLINK_TARGET contains a newline" ;;
  esac
  python_real="$(readlink -f "$agent_python")"
  expected_python_real="$(readlink -f "$EXPECTED_PYTHON_EXECUTABLE")"
  [ -n "$python_real" ] && [ "$python_real" = "$expected_python_real" ] \
    || fail "Agent Python and TAIJI_PYTHON_EXECUTABLE resolve to different files"
  if [ -n "$SOURCE_ARCHIVE_FD" ]; then
    python3 "$SOURCE_INTEGRITY_HELPER" verify \
      --archive-fd "$SOURCE_ARCHIVE_FD" \
      --archive-basename "$SOURCE_ARCHIVE_BASENAME" \
      --inventory-fd "$SOURCE_INVENTORY_FD" \
      --root "$REPO_ROOT" \
      --allow-extra-prefix "hermes-local-lab/sources/hermes-agent/venv" \
      --allow-extra-prefix "apps/taiji-desktop/node_modules" \
      --allow-extra-prefix "hermes-local-lab/sources/docx-engine-v2/node_modules" \
      --allow-extra-prefix "runtime/package-build" \
      --allow-extra-prefix "packages/麒麟操作系统安装包" \
      --allow-extra-symlink "hermes-local-lab/sources/hermes-agent/venv/bin/python" "$EXPECTED_AGENT_PYTHON_SYMLINK_TARGET" \
      || fail "source tree differs from the immutable archive inventory"
  else
    python3 "$SOURCE_INTEGRITY_HELPER" verify \
      --archive "$archive_ref" \
      --inventory "$inventory_ref" \
      --root "$REPO_ROOT" \
      --allow-extra-prefix "hermes-local-lab/sources/hermes-agent/venv" \
      --allow-extra-prefix "apps/taiji-desktop/node_modules" \
      --allow-extra-prefix "hermes-local-lab/sources/docx-engine-v2/node_modules" \
      --allow-extra-prefix "runtime/package-build" \
      --allow-extra-prefix "packages/麒麟操作系统安装包" \
      --allow-extra-symlink "hermes-local-lab/sources/hermes-agent/venv/bin/python" "$EXPECTED_AGENT_PYTHON_SYMLINK_TARGET" \
      || fail "source tree differs from the immutable archive inventory"
  fi
}

load_policy_contract() {
  [ -f "$POLICY_FILE" ] && [ ! -L "$POLICY_FILE" ] || fail "Missing compatibility policy: $POLICY_FILE"
  [ -f "$POLICY_HELPER" ] && [ ! -L "$POLICY_HELPER" ] || fail "Missing compatibility policy helper: $POLICY_HELPER"
  eval "$(python3 "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-shell)"
  POLICY_SHA256="$TAIJI_POLICY_SHA256"
  POLICY_ID="$TAIJI_POLICY_ID"
  TAIJI_PACKAGE_NAME="$TAIJI_PACKAGE_NAME"
  TAIJI_PACKAGE_ARCHITECTURE="$TAIJI_PACKAGE_ARCHITECTURE"
  TAIJI_PACKAGE_MAINTAINER="$TAIJI_PACKAGE_MAINTAINER"
  TAIJI_DEBIAN_DEPENDS="$TAIJI_DEBIAN_DEPENDS"
  TAIJI_GLIBC_MIN="$TAIJI_GLIBC_MIN"
  [ "$TAIJI_PACKAGE_NAME" = "taiji-agent" ] || fail "unexpected package name from policy"
  [ "$TAIJI_PACKAGE_ARCHITECTURE" = "amd64" ] || fail "unexpected package architecture from policy"
  [ "$POLICY_ID" = "taiji-linux-amd64-deb-v1" ] || fail "unexpected compatibility policy id"
}

resolve_source_commit() {
  local git_commit
  [ -x "$TRUSTED_GIT" ] && [ ! -L "$TRUSTED_GIT" ] || fail "Missing trusted Git boundary: $TRUSTED_GIT"
  git_commit="$("$TRUSTED_GIT" -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
  if [ -n "$git_commit" ]; then
    if [ -n "$SOURCE_COMMIT" ] && [ "$SOURCE_COMMIT" != "$git_commit" ]; then
      fail "TAIJI_SOURCE_COMMIT does not exactly match source Git HEAD"
    fi
    SOURCE_COMMIT="$git_commit"
  fi
  printf '%s\n' "$SOURCE_COMMIT" | grep -Eq '^[0-9a-f]{40}$' || fail "A full source commit is required"
}

validate_build_host_glibc() {
  local build_glibc
  build_glibc="$(ldd --version 2>&1 | awk 'NR == 1 { print }' | grep -Eo '[0-9]+(\.[0-9]+)+' | tail -n 1)"
  [ -n "$build_glibc" ] || fail "Cannot determine Linux build-host glibc version"
  dpkg --compare-versions "$build_glibc" le "$TAIJI_GLIBC_MIN" || fail "Build-host glibc $build_glibc exceeds policy $TAIJI_GLIBC_MIN"
}

validate_strict_toolchain_contract() {
  local actual lock_path python_real expected_python_real node_bin node_archive_marker actual_electron_archive_sha
  [ "$PYTHON_DEPENDENCY_LOCK_STATUS" = "strict-locked" ] \
    || fail "TAIJI_PYTHON_DEPENDENCY_LOCK_STATUS must be strict-locked"
  [ "$PYTHON_LOCK_BASENAME" = "uv.lock" ] \
    || fail "TAIJI_PYTHON_LOCK_BASENAME must be uv.lock"
  printf '%s\n' "$PYTHON_LOCK_SHA256" | grep -Eq '^[0-9a-f]{64}$' \
    || fail "TAIJI_PYTHON_LOCK_SHA256 is required"
  lock_path="$SOURCE_AGENT_DIR/$PYTHON_LOCK_BASENAME"
  [ -f "$lock_path" ] && [ ! -L "$lock_path" ] || fail "Python lock must be a regular file"
  actual="$(sha256sum "$lock_path" | awk '{print $1}')"
  [ "$actual" = "$PYTHON_LOCK_SHA256" ] || fail "Python lock SHA256 changed before DEB build"

  [ "$UV_VERSION" = "$PINNED_UV_VERSION" ] || fail "TAIJI_UV_VERSION is not the pinned uv version"
  [ "$UV_ARCHIVE_SHA256" = "$PINNED_UV_ARCHIVE_SHA256" ] || fail "TAIJI_UV_ARCHIVE_SHA256 is not pinned"
  [ "$UV_EXECUTABLE_SHA256" = "$PINNED_UV_EXECUTABLE_SHA256" ] || fail "TAIJI_UV_EXECUTABLE_SHA256 is not pinned"
  actual="$(sha256sum "$UV_ARCHIVE_PATH" | awk '{print $1}')"
  [ "$actual" = "$PINNED_UV_ARCHIVE_SHA256" ] || fail "uv archive SHA256 mismatch"
  [ -f "$UV_EXECUTABLE" ] && [ ! -L "$UV_EXECUTABLE" ] \
    && [ "$(stat -c '%h' "$UV_EXECUTABLE")" = 1 ] \
    || fail "TAIJI_UV_EXECUTABLE must be a regular single-link file"
  actual="$(sha256sum "$UV_EXECUTABLE" | awk '{print $1}')"
  [ "$actual" = "$UV_EXECUTABLE_SHA256" ] || fail "uv executable SHA256 mismatch"
  [ "$("$UV_EXECUTABLE" --version)" = "uv $PINNED_UV_VERSION (x86_64-unknown-linux-gnu)" ] || fail "uv executable version mismatch"
  file "$UV_EXECUTABLE" | grep -Eq 'ELF 64-bit.*(x86-64|X86-64|80386)' \
    || fail "uv executable is not Linux x86_64 ELF"

  python_real="$(readlink -f "$SOURCE_AGENT_DIR/venv/bin/python")"
  [ -f "$python_real" ] || fail "resolved Agent Python executable is missing"
  [ -n "$EXPECTED_PYTHON_EXECUTABLE" ] \
    || fail "TAIJI_PYTHON_EXECUTABLE is required"
  expected_python_real="$(readlink -f "$EXPECTED_PYTHON_EXECUTABLE")"
  [ "$python_real" = "$expected_python_real" ] \
    || fail "Agent Python symlink does not resolve to TAIJI_PYTHON_EXECUTABLE"
  [ "$EXPECTED_PYTHON_VERSION" = "$PINNED_PYTHON_VERSION" ] \
    || fail "TAIJI_PYTHON_VERSION is not pinned"
  [ "$PYTHON_ARCHIVE_SHA256" = "$PINNED_PYTHON_ARCHIVE_SHA256" ] \
    || fail "TAIJI_PYTHON_ARCHIVE_SHA256 is not pinned"
  actual="$(sha256sum "$PYTHON_ARCHIVE_PATH" | awk '{print $1}')"
  [ "$actual" = "$PINNED_PYTHON_ARCHIVE_SHA256" ] \
    || fail "Python archive SHA256 mismatch"
  PYTHON_EXECUTABLE_SHA256="$(sha256sum "$python_real" | awk '{print $1}')"
  [ "$EXPECTED_PYTHON_EXECUTABLE_SHA256" = "$PINNED_PYTHON_EXECUTABLE_SHA256" ] \
    || fail "TAIJI_PYTHON_EXECUTABLE_SHA256 is not pinned"
  [ "$PYTHON_EXECUTABLE_SHA256" = "$PINNED_PYTHON_EXECUTABLE_SHA256" ] \
    || fail "Python executable SHA256 is not the pinned official archive identity"
  PYTHON_VERSION="$("$SOURCE_AGENT_DIR/venv/bin/python" -c 'import platform; print(platform.python_version())')"
  [ "$PYTHON_VERSION" = "$PINNED_PYTHON_VERSION" ] \
    || fail "Agent Python version is not the pinned official archive version"

  node_bin="$PACKAGED_NODE_ROOT/bin/node"
  node_archive_marker="$PACKAGED_NODE_ROOT/.taiji-node-archive-sha256"
  [ -f "$node_bin" ] && [ ! -L "$node_bin" ] \
    && [ -f "$node_archive_marker" ] && [ ! -L "$node_archive_marker" ] \
    || fail "verified Node identity files are missing or unsafe"
  NODE_ARCHIVE_SHA256="$(tr -d '\r\n' < "$node_archive_marker")"
  [ "$NODE_ARCHIVE_SHA256" = "$PACKAGED_NODE_ARCHIVE_SHA256" ] || fail "Node archive SHA256 mismatch"
  actual="$(sha256sum "$NODE_ARCHIVE_PATH" | awk '{print $1}')"
  [ "$actual" = "$PACKAGED_NODE_ARCHIVE_SHA256" ] || fail "Node archive file SHA256 mismatch"
  NODE_EXECUTABLE_SHA256="$(sha256sum "$PACKAGED_NODE_EXECUTABLE" | awk '{print $1}')"
  [ "$NODE_EXECUTABLE_SHA256" = "$PINNED_NODE_EXECUTABLE_SHA256" ] \
    || fail "Node executable SHA256 is not the pinned official archive identity"
  actual="$(sha256sum "$node_bin" | awk '{print $1}')"
  [ "$actual" = "$NODE_EXECUTABLE_SHA256" ] \
    || fail "Node runtime tree differs from the sealed Node executable"
  NODE_VERSION="$("$PACKAGED_NODE_EXECUTABLE" --version)"
  NODE_VERSION="${NODE_VERSION#v}"
  [ "$NODE_VERSION" = "$PACKAGED_NODE_VERSION" ] || fail "Node version mismatch"

  ELECTRON_VERSION="$TAIJI_ELECTRON_VERSION"
  ELECTRON_ARCHIVE_SHA256="$TAIJI_ELECTRON_ARCHIVE_SHA256"
  [ -f "$ELECTRON_ARCHIVE" ] && [ ! -L "$ELECTRON_ARCHIVE" ] || fail "verified Electron archive is required"
  actual_electron_archive_sha="$(sha256sum "$ELECTRON_ARCHIVE" | awk '{print $1}')"
  [ "$actual_electron_archive_sha" = "$ELECTRON_ARCHIVE_SHA256" ] || fail "Electron archive SHA256 mismatch"
}

validate_locked_python_environment() {
  local helper_sha lock_before lock_after
  [ -f "$LOCK_CONTRACT_HELPER" ] && [ ! -L "$LOCK_CONTRACT_HELPER" ] \
    || fail "Python lock contract helper is missing"
  helper_sha="$(sha256sum "$LOCK_CONTRACT_HELPER" | awk '{print $1}')"
  [ "$helper_sha" = "$PINNED_LOCK_CONTRACT_HELPER_SHA256" ] \
    || fail "Python lock contract helper differs from the reviewed fixed implementation"
  lock_before="$(sha256sum "$SOURCE_AGENT_DIR/uv.lock" | awk '{print $1}')"
  [ "$lock_before" = "$PYTHON_LOCK_SHA256" ] \
    || fail "Python lock changed before installed-environment verification"
  python3 "$LOCK_CONTRACT_HELPER" \
    --pyproject "$SOURCE_AGENT_DIR/pyproject.toml" \
    --lock "$SOURCE_AGENT_DIR/uv.lock" \
    --requirements "$SOURCE_WEB_DIR/requirements.txt" \
    --verify-installed \
    --python "$SOURCE_AGENT_DIR/venv/bin/python" >/dev/null \
    || fail "Installed Python environment failed the lock contract"
  (
    cd "$SOURCE_AGENT_DIR"
    unset UV_INDEX UV_DEFAULT_INDEX UV_EXTRA_INDEX_URL UV_FIND_LINKS UV_NO_INDEX UV_INDEX_STRATEGY UV_CONFIG_FILE
    export UV_NO_CONFIG=1
    UV_OFFLINE=1 UV_PROJECT_ENVIRONMENT="$SOURCE_AGENT_DIR/venv" \
      "$UV_EXECUTABLE" sync --extra all --locked --check
  ) >/dev/null \
    || fail "Installed Python environment is not a complete locked uv sync"
  lock_after="$(sha256sum "$SOURCE_AGENT_DIR/uv.lock" | awk '{print $1}')"
  [ "$lock_after" = "$lock_before" ] && [ "$lock_after" = "$PYTHON_LOCK_SHA256" ] \
    || fail "Python lock changed during installed-environment verification"
}

validate_staged_toolchain_executables() {
  local staged_python staged_node staged_electron actual
  staged_python="$AGENT_RUNTIME/venv/bin/python"
  staged_node="$INSTALL_ROOT/runtime/node/bin/node"
  staged_electron="$DESKTOP_RUNTIME/node_modules/electron/dist/electron"
  for path in "$staged_python" "$staged_node" "$staged_electron"; do
    [ -f "$path" ] && [ ! -L "$path" ] || fail "Staged toolchain executable is not a regular file: $path"
  done
  actual="$(sha256sum "$staged_python" | awk '{print $1}')"
  [ "$actual" = "$PYTHON_EXECUTABLE_SHA256" ] \
    && [ "$actual" = "$PINNED_PYTHON_EXECUTABLE_SHA256" ] \
    || fail "Staged Python executable SHA256 mismatch"
  actual="$(sha256sum "$staged_node" | awk '{print $1}')"
  [ "$actual" = "$NODE_EXECUTABLE_SHA256" ] && [ "$actual" = "$PINNED_NODE_EXECUTABLE_SHA256" ] \
    || fail "Staged Node executable SHA256 mismatch"
  actual="$(sha256sum "$staged_electron" | awk '{print $1}')"
  [ "$actual" = "$PINNED_ELECTRON_EXECUTABLE_SHA256" ] \
    || fail "Staged Electron executable SHA256 mismatch"
}

validate_desktop_entry() {
  local desktop="$1"
  if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$desktop"
  else
    warn "desktop-file-validate not found; using structural checks"
    grep -qx 'Type=Application' "$desktop" || fail "Desktop entry missing Type=Application"
    grep -qx 'Name=太极 Agent' "$desktop" || fail "Desktop entry missing expected Name"
    grep -qx 'Exec=/usr/bin/taiji-agent' "$desktop" || fail "Desktop entry missing expected Exec"
    grep -qx 'Icon=taiji-agent' "$desktop" || fail "Desktop entry missing expected Icon"
    grep -qx 'Terminal=false' "$desktop" || fail "Desktop entry must not require a terminal"
    grep -qx 'StartupWMClass=taiji-agent' "$desktop" || fail "Desktop entry missing StartupWMClass"
    grep -qx 'X-GNOME-WMClass=taiji-agent' "$desktop" || fail "Desktop entry missing X-GNOME-WMClass"
  fi
}

validate_appstream_metadata() {
  [ -f "$APPSTREAM_FILE" ] && [ ! -L "$APPSTREAM_FILE" ] || fail "Missing AppStream metadata: $APPSTREAM_FILE"
  grep -q '<id>taiji-agent.desktop</id>' "$APPSTREAM_FILE" || fail "AppStream metadata has wrong desktop id"
  grep -q '<launchable type="desktop-id">taiji-agent.desktop</launchable>' "$APPSTREAM_FILE" || fail "AppStream metadata has no desktop launchable"
  grep -q '<icon type="cached" width="512" height="512">taiji-agent</icon>' "$APPSTREAM_FILE" || fail "AppStream metadata has no 512px cached icon"
}

verify_linux_electron_runtime() {
  [ -x "$ELECTRON_BIN" ] || fail "Missing Linux Electron runtime"
  local electron_file ldd_output
  electron_file="$(file "$ELECTRON_BIN")"
  case "$electron_file" in *ELF*64-bit*x86-64*|*ELF*64-bit*X86-64*|*ELF*64-bit*80386*) ;; *) fail "Electron runtime is not Linux x86_64: $electron_file" ;; esac
  if ! ldd_output="$(ldd "$ELECTRON_BIN" 2>&1)"; then
    fail "Cannot inspect Electron runtime shared-library output"
  fi
  if printf '%s\n' "$ldd_output" | grep -F 'not found' >/dev/null; then
    fail "Electron runtime has missing shared libraries"
  else
    [ "$?" -eq 1 ] || fail "Cannot inspect Electron runtime shared-library output"
  fi
}

validate_packaged_config_template() {
  [ -f "$DEFAULT_CONFIG" ] || fail "Missing packaged default config template: $DEFAULT_CONFIG"
  "$SOURCE_AGENT_PYTHON" - "$DEFAULT_CONFIG" <<'PY'
import sys
from pathlib import Path
import yaml
path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
markers = ("api_key", "apikey", "token", "secret", "password", "private_key", "wechat", "weixin", "corpsecret")
def scan(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            if any(marker in str(key).lower() for marker in markers):
                raise SystemExit(f"sensitive key in packaged default config: {prefix}{key}")
            scan(child, f"{prefix}{key}.")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            scan(child, f"{prefix}{idx}.")
    elif isinstance(value, str) and "BEGIN " in value and "PRIVATE KEY" in value:
        raise SystemExit(f"private key shaped value in packaged default config: {prefix.rstrip('.')}")
scan(data)
for parent, key in (("model", "provider"), ("model", "default"), ("webui", "feature_visibility")):
    if not isinstance(data.get(parent), dict) or key not in data[parent]:
        raise SystemExit(f"missing {parent}.{key} in packaged default config")
PY
}

compile_sourceless_python() {
  local target="$1" python_bin="$2"
  find "$target" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  "$python_bin" -m compileall -q -b "$target"
  find "$target" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find "$target" -type f -name '*.py' ! -path '*/venv/*' -delete
}

rename_internal_agent_modules() {
  [ -d "$AGENT_RUNTIME/hermes_cli" ] && mv "$AGENT_RUNTIME/hermes_cli" "$AGENT_RUNTIME/taiji_cli" || true
  local source target
  for source in "$AGENT_RUNTIME"/hermes_*.py; do
    [ -e "$source" ] || continue
    target="$AGENT_RUNTIME/taiji_$(basename "$source" | sed 's/^hermes_//')"
    mv "$source" "$target"
  done
  [ -f "$AGENT_RUNTIME/agent/transports/hermes_tools_mcp_server.py" ] && mv "$AGENT_RUNTIME/agent/transports/hermes_tools_mcp_server.py" "$AGENT_RUNTIME/agent/transports/taiji_tools_mcp_server.py" || true
  [ -f "$AGENT_RUNTIME/agent/transports/hermes_tools_profile_env.py" ] && mv "$AGENT_RUNTIME/agent/transports/hermes_tools_profile_env.py" "$AGENT_RUNTIME/agent/transports/taiji_tools_profile_env.py" || true
  rm -rf "$AGENT_RUNTIME/hermes" "$AGENT_RUNTIME/hermes-agent" "$AGENT_RUNTIME/hermes_agent.egg-info" "$AGENT_RUNTIME/.hermes" "$AGENT_RUNTIME/setup-hermes.sh" "$AGENT_RUNTIME/HERMES.md" "$AGENT_RUNTIME/hermes-already-has-routines.md"
}

rewrite_product_text_tokens() {
  local target="$1"
  find "$target" -type f ! -path '*/venv/*' \( -name '*.py' -o -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.json' -o -name '*.yaml' -o -name '*.yml' -o -name '*.toml' -o -name '*.txt' -o -name '*.md' \) -print0 | xargs -0 -r perl -pi -e 's/HERMES_/TAIJI_/g; s/HERMES/TAIJI/g; s/Hermes/Taiji/g; s/hermes/taiji/g'
}

write_packaged_webui_version() {
  local base digest
  base="${TAIJI_WEBUI_VERSION:-}"
  if [ -z "$base" ] && command -v git >/dev/null 2>&1; then
    base="$("$TRUSTED_GIT" -C "$SOURCE_WEB_DIR" describe --tags --always 2>/dev/null || true)"
  fi
  [ -n "$base" ] || base="taiji-webui"
  digest="$(find "$WEB_RUNTIME" -type f ! -name '_version.txt' ! -name '*.pyc' -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print substr($1,1,12)}')"
  mkdir -p "$WEB_RUNTIME/api"
  printf '%s-pkg.%s\n' "$base" "$digest" > "$WEB_RUNTIME/api/_version.txt"
}

write_installed_runtime_profile() {
  cat > "$AGENT_RUNTIME/taiji-runtime-profile.json" <<'PROFILE'
{
  "schema_version": "taiji-runtime-profile/v1",
  "profile": "installed-production"
}
PROFILE
  chmod 0644 "$AGENT_RUNTIME/taiji-runtime-profile.json"
}

write_installed_runtime_profile_module() {
  cat > "$AGENT_RUNTIME/taiji_runtime_profile.py" <<'PY'
"""Build-controlled runtime profile for an installed Taiji payload."""

from __future__ import annotations


PROFILE_SCHEMA_VERSION = "taiji-runtime-profile/v1"
INSTALLED_PRODUCTION_PROFILE = "installed-production"


def installation_profile() -> str:
    return INSTALLED_PRODUCTION_PROFILE


def is_installed_production() -> bool:
    return True
PY
  chmod 0644 "$AGENT_RUNTIME/taiji_runtime_profile.py"
}

stage_python_runtime() {
  mkdir -p "$AGENT_RUNTIME" "$WEB_RUNTIME"
  rsync -a --exclude '.git' --exclude '.github' --exclude '.DS_Store' --exclude '._*' --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' --exclude '.env.example' --exclude '.env.docker.example' --exclude '*.example' --exclude '.envrc' --exclude '.dockerignore' --exclude '.gitattributes' --exclude '.gitignore' --exclude '.hadolint.yaml' --exclude '.mailmap' --exclude 'license.jwt' --exclude '*.jwt' --exclude '.pytest_cache' --exclude '.playwright-mcp' --exclude 'docs' --exclude 'tests' --exclude 'website' --exclude 'articles' --exclude 'demos' --exclude 'datagen-config-examples' --exclude 'docker' --exclude 'nix' --exclude 'packaging' --exclude 'plugins/hermes-achievements' --exclude 'plugins/kanban/systemd' --exclude 'plugins/security-guidance' --exclude 'skills' --exclude 'scripts' --exclude 'optional-skills' --exclude 'optional-mcps' --exclude 'locales' --exclude 'ui-tui' --exclude 'web' --exclude 'venv' --exclude 'Dockerfile' --exclude 'docker-compose*' --exclude 'flake.*' --exclude 'MANIFEST.in' --exclude 'uv.lock' --exclude 'package*.json' --exclude 'pyproject.toml' --exclude 'LICENSE' --exclude '*.md' "$SOURCE_AGENT_DIR"/ "$AGENT_RUNTIME"/
  rename_internal_agent_modules
  rewrite_product_text_tokens "$AGENT_RUNTIME"
  python3 "$PYTHON_RUNTIME_STAGER" --source-venv "$SOURCE_AGENT_DIR/venv" --destination "$AGENT_RUNTIME/venv" --require-linux-x86-64 --smoke-import yaml --smoke-import fastapi --smoke-import uvicorn --smoke-import httpx --smoke-import pydantic
  assert_no_development_distributions
  rsync -a --exclude '.git' --exclude '.DS_Store' --exclude '._*' --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' --exclude '.env.example' --exclude '.env.docker.example' --exclude '*.example' --exclude 'docs' --exclude 'reports' --exclude 'scripts' --exclude 'ctl.sh' --exclude 'start.sh' --exclude 'docker_init.bash' --exclude 'docker*' --exclude '*compose*' --exclude 'start.ps1' --exclude 'eslint*' --exclude 'tests' --exclude 'node_modules' --exclude 'Dockerfile' --exclude 'package*.json' --exclude 'pyproject.toml' --exclude 'LICENSE' --exclude '*.md' "$SOURCE_WEB_DIR"/ "$WEB_RUNTIME"/
  rewrite_product_text_tokens "$WEB_RUNTIME"
  write_packaged_webui_version
  write_installed_runtime_profile
  write_installed_runtime_profile_module
  compile_sourceless_python "$AGENT_RUNTIME" "$SOURCE_AGENT_PYTHON"
  compile_sourceless_python "$WEB_RUNTIME" "$SOURCE_AGENT_PYTHON"
}

assert_no_development_distributions() {
  if ! python3 - "$AGENT_RUNTIME/venv" <<'PY'
import re
import sys
from email.parser import Parser
from pathlib import Path

runtime = Path(sys.argv[1])
site_packages_roots = sorted(runtime.glob("lib/python*/site-packages"))
if not site_packages_roots:
    raise SystemExit(f"staged Python site-packages is missing under {runtime}")

forbidden = {
    "debugpy",
    "pytest",
    "pytest-asyncio",
    "pytest-timeout",
    "ruff",
    "ty",
}
found = []
metadata_count = 0
for site_packages in site_packages_roots:
    for metadata_dir in sorted(site_packages.glob("*.dist-info")):
        if metadata_dir.is_symlink() or not metadata_dir.is_dir():
            raise SystemExit(f"invalid staged distribution metadata directory: {metadata_dir}")
        metadata_path = metadata_dir / "METADATA"
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise SystemExit(f"staged distribution metadata is missing: {metadata_path}")
        metadata_count += 1
        metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8"))
        name = metadata.get("Name")
        if not name:
            raise SystemExit(f"staged distribution has no Name metadata: {metadata_path}")
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        if normalized in forbidden:
            found.append(f"{name} ({metadata_dir.name})")

if metadata_count == 0:
    raise SystemExit(f"no staged Python distribution metadata found under {runtime}")
if found:
    raise SystemExit("forbidden development distributions in staged runtime: " + ", ".join(found))
PY
  then
    fail "Staged Python development-distribution gate failed"
  fi
}

scan_private_key_material() {
  local secret_path private_list private_path grep_status
  if secret_path="$(find "$INSTALL_ROOT" \( -name '.env' -o -name '*.jwt' -o -name 'id_rsa' -o -name 'id_ed25519' -o -name '*.key' \) -print -quit)"; then
    :
  else
    fail "Cannot scan package tree for secret-shaped files"
  fi
  [ -z "$secret_path" ] || fail "Package tree contains secret-shaped files: $secret_path"

  private_list="$(mktemp "${TMPDIR:-/tmp}/taiji-private-key-scan.XXXXXX")" \
    || fail "Cannot allocate private-key scan list"
  if ! find "$INSTALL_ROOT" -type f \( -name '*.pem' -o -name '*.crt' -o -name '*.cer' \) -print0 > "$private_list"; then
    rm -f -- "$private_list"
    fail "Cannot enumerate package public-key files"
  fi
  while IFS= read -r -d '' private_path; do
    if grep -Iq 'BEGIN .*PRIVATE KEY' -- "$private_path"; then
      rm -f -- "$private_list"
      fail "Package tree contains private key material: $private_path"
    else
      grep_status=$?
      if [ "$grep_status" -ne 1 ]; then
        rm -f -- "$private_list"
        fail "Cannot inspect package key file: $private_path"
      fi
    fi
  done < "$private_list"
  rm -f -- "$private_list"
}

scan_acceptance_privacy_compatibility() {
  local acceptance_root="$1"
  python3 - "$acceptance_root" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
allowed = {
    "04_目标终端_桌面App验收并导出证据.sh": {
        "-u HERMES_HOME \\",
        "-u HERMES_CONFIG_PATH \\",
        "-u HERMES_CONFIG \\",
        "-u HERMES_ENV \\",
        "-u HERMES_WEBUI_AGENT_DIR \\",
        "-u HERMES_WEBUI_PYTHON \\",
    },
    "验收工具/run-installed-electron-acceptance.js": {
        "const UNSAFE_VERSION_RE = /(?:hermes|password|passwd|passphrase|secret|token|bearer|(?:^|[-_.])sk-|(?:^|[-_.])key(?:[-_.]|$))/i;",
    },
    "验收工具/validate-taiji-release-evidence.py": {
        'r"(?i)(?:hermes|password|passwd|passphrase|secret|token|bearer|(?:^|[-_.])sk-|(?:^|[-_.])key(?:[-_.]|$))"',
        '"taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/uv.lock",',
    },
}
pattern = re.compile(r"hermes|Hermes|HERMES_")
observed = {name: set() for name in allowed}
for path in sorted(root.rglob("*")):
    if not path.is_file() or path.is_symlink():
        continue
    relative = path.relative_to(root).as_posix()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SystemExit("cannot inspect installed acceptance text: %s" % relative) from exc
    for line in lines:
        if pattern.search(line) is None:
            continue
        normalized = line.strip()
        if normalized not in allowed.get(relative, set()):
            raise SystemExit("unexpected legacy product marker in installed acceptance tool: %s" % relative)
        observed[relative].add(normalized)
if observed != allowed:
    raise SystemExit("installed acceptance compatibility marker allowlist is incomplete")
PY
}

scan_product_privacy() {
  local name_hit privacy_list privacy_path grep_status acceptance_root
  acceptance_root="${ACCEPTANCE_ROOT:-$INSTALL_ROOT/libexec/target-acceptance}"
  if name_hit="$(find "$INSTALL_ROOT" -path "$INSTALL_ROOT/licenses" -prune -o -path "$INSTALL_ROOT/licenses/*" -prune -o -path "$AGENT_RUNTIME/venv/lib*" -prune -o -path "$acceptance_root" -prune -o -iname '*hermes*' -print -quit)"; then
    :
  else
    fail "Cannot scan package tree for legacy product names"
  fi
  [ -z "$name_hit" ] || fail "Package tree contains legacy product names in visible paths: $name_hit"

  privacy_list="$(mktemp "${TMPDIR:-/tmp}/taiji-privacy-scan.XXXXXX")" \
    || fail "Cannot allocate privacy scan list"
  if ! find "$INSTALL_ROOT" -path "$INSTALL_ROOT/licenses" -prune -o -path "$INSTALL_ROOT/licenses/*" -prune -o -path "$AGENT_RUNTIME/venv/lib*" -prune -o -path "$acceptance_root" -prune -o -type f ! -name '*.pyc' ! -name '*.so' ! -name '*.png' ! -name '*.jpg' ! -name '*.jpeg' ! -name '*.gif' -print0 > "$privacy_list"; then
    rm -f -- "$privacy_list"
    fail "Cannot enumerate package text files for privacy scan"
  fi
  while IFS= read -r -d '' privacy_path; do
    if grep -I -n -E 'hermes|Hermes|HERMES_|hermes_cli|hermes-agent|hermes-webui|hermes-home' -- "$privacy_path" >/dev/null; then
      rm -f -- "$privacy_list"
      fail "Package tree contains legacy product names in text files: $privacy_path"
    else
      grep_status=$?
      if [ "$grep_status" -ne 1 ]; then
        rm -f -- "$privacy_list"
        fail "Cannot inspect package text file: $privacy_path"
      fi
    fi
  done < "$privacy_list"
  rm -f -- "$privacy_list"
  if [ -d "$acceptance_root" ] && [ ! -L "$acceptance_root" ]; then
    scan_acceptance_privacy_compatibility "$acceptance_root"
  fi
}

scan_package_tree() {
  local forbidden_path
  if forbidden_path="$(find "$PKG_ROOT" \( -name '.DS_Store' -o -name '._*' -o -name '__pycache__' \) -print -quit)"; then
    :
  else
    fail "Cannot scan package tree for forbidden cache metadata"
  fi
  [ -z "$forbidden_path" ] || fail "Package tree contains forbidden cache metadata: $forbidden_path"
  scan_private_key_material
  scan_product_privacy
}

scan_webui_offline_assets() {
  local static_dir="$SOURCE_WEB_DIR/static" required missing="" cdn_hits
  [ -d "$static_dir" ] || fail "Missing WebUI static directory: $static_dir"
  if cdn_hits="$(grep -RInE 'cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com' "$static_dir" --include='*.html' --include='*.js' --include='*.css' --include='*.mjs')"; then
    [ -z "$cdn_hits" ] || fail "WebUI static assets still depend on CDN"
  else
    [ "$?" -eq 1 ] || fail "Cannot scan WebUI static assets for CDN references"
  fi
  for required in "vendor/xterm/5.3.0/xterm.css" "vendor/xterm/5.3.0/xterm.js" "vendor/xterm-addon-fit/0.8.0/xterm-addon-fit.js" "vendor/xterm-addon-web-links/0.9.0/xterm-addon-web-links.js" "vendor/prismjs/1.29.0/themes/prism-tomorrow.min.css" "vendor/prismjs/1.29.0/themes/prism.min.css" "vendor/prismjs/1.29.0/prism.min.js" "vendor/pdfjs-dist/4.9.155/pdf.min.mjs" "vendor/pdfjs-dist/4.9.155/pdf.worker.min.mjs" "vendor/mermaid/10.9.3/mermaid.min.js"; do
    [ -f "$static_dir/$required" ] || missing="$missing$static_dir/$required"$'\n'
  done
  [ -z "$missing" ] || { printf '%s' "$missing" >&2; fail "WebUI offline vendor assets are incomplete"; }
}

archive_old_packages() {
  mkdir -p "$ARCHIVE_DIR"
  find "$OUT_DIR" -maxdepth 1 -type f \( -name 'taiji-agent_*_amd64.deb' -o -name 'taiji-agent_*_amd64.deb.sha256' \) ! -name "$(basename "$OUT_DEB")" ! -name "$(basename "$OUT_DEB").sha256" -exec mv {} "$ARCHIVE_DIR"/ \;
}

scan_deb_release_artifact() {
  dpkg-deb -I "$OUT_DEB" >/dev/null
  dpkg-deb -c "$OUT_DEB" >/dev/null
  local marker strings_dump
  strings_dump="$(mktemp "${TMPDIR:-/tmp}/taiji-deb-strings.XXXXXX")" \
    || fail "Cannot allocate DEB metadata scan buffer"
  if ! strings "$OUT_DEB" > "$strings_dump"; then
    rm -f -- "$strings_dump"
    fail "Cannot inspect DEB archive metadata"
  fi
  for marker in LIBARCHIVE.xattr com.apple.provenance PaxHeaders SCHILY.xattr; do
    if grep -F "$marker" "$strings_dump" >/dev/null; then
      rm -f -- "$strings_dump"
      fail "DEB contains forbidden archive metadata marker: $marker"
    else
      [ "$?" -eq 1 ] || {
        rm -f -- "$strings_dump"
        fail "Cannot inspect DEB archive metadata marker: $marker"
      }
    fi
  done
  rm -f -- "$strings_dump"
}

verify_extracted_acceptance_payload() {
  local audit_root="$1" owner_uid
  owner_uid="$(id -u)"
  python3 - \
    "$audit_root" \
    "$SOURCE_COMMIT" \
    "$VERSION" \
    "$ACCEPTANCE_BINDING_SHA256" \
    "$ACCEPTANCE_TOOLS_MANIFEST_SHA256" \
    "$ACCEPTANCE_ENTRYPOINT_SHA256" \
    "$INSTALLED_RELEASE_MANIFEST_SHA256" \
    "$ACCEPTANCE_HELPER" \
    "$owner_uid" <<'PY'
import hashlib
import importlib.util
import json
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
source_commit, version = sys.argv[2:4]
expected_binding, expected_tools, expected_entrypoint, expected_release = sys.argv[4:8]
helper_source = Path(sys.argv[8])
owner_uid = int(sys.argv[9])

def strict(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit("installed acceptance JSON contains a duplicate field")
        result[key] = value
    return result

def read_regular(relative, mode):
    path = root / relative
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise SystemExit("installed acceptance payload node is unsafe: " + relative)
    return path.read_bytes()

def canonical_json(relative, mode):
    raw = read_regular(relative, mode)
    payload = json.loads(raw.decode("utf-8"), object_pairs_hook=strict)
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if type(payload) is not dict or encoded != raw:
        raise SystemExit("installed acceptance JSON is not canonical: " + relative)
    return raw, payload

entrypoint_raw = read_regular("usr/bin/taiji-agent-acceptance", 0o755)
release_raw, release = canonical_json(
    "opt/taiji-agent/resources/taiji-release-manifest.json", 0o644
)
binding_raw, binding = canonical_json(
    "opt/taiji-agent/resources/taiji-acceptance-binding.json", 0o644
)
tools_relative = (
    "opt/taiji-agent/libexec/target-acceptance/验收工具/"
    "acceptance-tools-manifest.json"
)
tools_raw, _tools = canonical_json(tools_relative, 0o644)

for label, raw, expected in (
    ("acceptance binding", binding_raw, expected_binding),
    ("acceptance tools manifest", tools_raw, expected_tools),
    ("acceptance entrypoint", entrypoint_raw, expected_entrypoint),
    ("installed release manifest", release_raw, expected_release),
):
    if hashlib.sha256(raw).hexdigest() != expected:
        raise SystemExit(label + " digest differs from the build identity")

expected_binding_payload = {
    "schema": "taiji-installed-acceptance-binding/v1",
    "version": version,
    "source_commit": source_commit,
    "release_manifest_sha256": expected_release,
    "acceptance_tools_manifest_path": "/opt/taiji-agent/libexec/target-acceptance/验收工具/acceptance-tools-manifest.json",
    "acceptance_tools_manifest_sha256": expected_tools,
    "launcher_path": "/opt/taiji-agent/libexec/target-acceptance/04_目标终端_桌面App验收并导出证据.sh",
    "launcher_sha256": hashlib.sha256(read_regular("opt/taiji-agent/libexec/target-acceptance/04_目标终端_桌面App验收并导出证据.sh", 0o755)).hexdigest(),
    "helper_path": "/opt/taiji-agent/libexec/target-acceptance/acceptance_tools_manifest.py",
    "helper_sha256": hashlib.sha256(read_regular("opt/taiji-agent/libexec/target-acceptance/acceptance_tools_manifest.py", 0o644)).hexdigest(),
    "runner_path": "/opt/taiji-agent/libexec/target-acceptance/acceptance-runner.py",
    "runner_sha256": hashlib.sha256(read_regular("opt/taiji-agent/libexec/target-acceptance/acceptance-runner.py", 0o644)).hexdigest(),
    "entrypoint_path": "/usr/bin/taiji-agent-acceptance",
    "entrypoint_sha256": expected_entrypoint,
}
if binding != expected_binding_payload:
    raise SystemExit("installed acceptance binding does not match the extracted payload")
if release != {
    "schema": "taiji-release-manifest/v1",
    "platform": "linux",
    "arch": "amd64",
    "version": version,
    "commit": source_commit,
    "installRoot": "/opt/taiji-agent",
}:
    raise SystemExit("installed release manifest does not match the extracted payload")

spec = importlib.util.spec_from_file_location("taiji_acceptance_tools_manifest", helper_source)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load acceptance tools manifest helper")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
helper.verify_staged(
    root / "opt/taiji-agent/libexec/target-acceptance/验收工具",
    source_commit,
    expected_tools,
    owner_uid,
)
PY
}

audit_deb_payload() {
  local contents="$BUILD_ROOT/deb-contents.txt" audit_root="$BUILD_ROOT/deb-audit-root" control_root="$BUILD_ROOT/deb-audit-control" extracted_abi="$BUILD_ROOT/extracted-elf-abi-audit.json" extracted_icon_sha256 required missing="" payload_path
  dpkg-deb -c "$OUT_DEB" > "$contents"
  rm -rf "$audit_root" "$control_root"
  mkdir -p "$audit_root" "$control_root"
  dpkg-deb -x "$OUT_DEB" "$audit_root"
  dpkg-deb -e "$OUT_DEB" "$control_root"
  for required in "./opt/taiji-agent/runtime/agent/venv/bin/python" "./opt/taiji-agent/runtime/node/bin/node" "./opt/taiji-agent/runtime/lib" "./opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron" "./opt/taiji-agent/resources/payload-contract.json" "./opt/taiji-agent/resources/linux-compatibility-policy.json" "./opt/taiji-agent/resources/taiji-release-manifest.json" "./opt/taiji-agent/resources/elf-abi-audit.json" "./opt/taiji-agent/runtime/web/server.pyc" "./opt/taiji-agent/scripts/taiji-native-verify" "./opt/taiji-agent/scripts/support_bundle.py" "./opt/taiji-agent/apps/taiji-desktop/src/main.js" "./opt/taiji-agent/apps/taiji-desktop/src/preload.js" "./opt/taiji-agent/resources/icons/taiji-agent.png" "./usr/share/applications/taiji-agent.desktop" "./usr/share/metainfo/taiji-agent.metainfo.xml" "./usr/share/icons/hicolor/32x32/apps/taiji-agent.png" "./usr/share/icons/hicolor/48x48/apps/taiji-agent.png" "./usr/share/icons/hicolor/64x64/apps/taiji-agent.png" "./usr/share/icons/hicolor/128x128/apps/taiji-agent.png" "./usr/share/icons/hicolor/192x192/apps/taiji-agent.png" "./usr/share/icons/hicolor/256x256/apps/taiji-agent.png" "./usr/share/icons/hicolor/512x512/apps/taiji-agent.png" "./usr/bin/taiji" "./usr/bin/taiji-agent" "./usr/bin/taiji-agent-support"; do
    payload_path="$audit_root/${required#./}"
    [ -e "$payload_path" ] || [ -L "$payload_path" ] || missing="$missing$required"$'\n'
  done
  for required in "./usr/bin/taiji-agent-acceptance" "./opt/taiji-agent/resources/taiji-acceptance-binding.json" "./opt/taiji-agent/libexec/target-acceptance/acceptance-runner.py" "./opt/taiji-agent/libexec/target-acceptance/acceptance_tools_manifest.py" "./opt/taiji-agent/libexec/target-acceptance/04_目标终端_桌面App验收并导出证据.sh" "./opt/taiji-agent/libexec/target-acceptance/验收工具/acceptance-tools-manifest.json" "./opt/taiji-agent/libexec/target-acceptance/验收工具/run-installed-electron-acceptance.js" "./opt/taiji-agent/libexec/target-acceptance/验收工具/assemble-target-evidence.py" "./opt/taiji-agent/libexec/target-acceptance/验收工具/observe-single-deb-install.py" "./opt/taiji-agent/libexec/target-acceptance/验收工具/certification-matrix.json" "./opt/taiji-agent/libexec/target-acceptance/验收工具/validate-taiji-release-evidence.py" "./opt/taiji-agent/libexec/target-acceptance/验收工具/taiji-challenge-envelope.py" "./opt/taiji-agent/libexec/target-acceptance/验收工具/signing-public.pem"; do
    payload_path="$audit_root/${required#./}"
    [ -e "$payload_path" ] || [ -L "$payload_path" ] || missing="$missing$required"$'\n'
  done
  [ -z "$missing" ] || { printf '%s' "$missing" >&2; fail "DEB payload is missing required runtime paths"; }
  [ "$(sha256sum "$audit_root/opt/taiji-agent/runtime/agent/venv/bin/python" | awk '{print $1}')" = "$PINNED_PYTHON_EXECUTABLE_SHA256" ] \
    || fail "Extracted DEB Python executable SHA256 is not canonical"
  [ "$(sha256sum "$audit_root/opt/taiji-agent/runtime/node/bin/node" | awk '{print $1}')" = "$PINNED_NODE_EXECUTABLE_SHA256" ] \
    || fail "Extracted DEB Node executable SHA256 is not canonical"
  [ "$(sha256sum "$audit_root/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron" | awk '{print $1}')" = "$PINNED_ELECTRON_EXECUTABLE_SHA256" ] \
    || fail "Extracted DEB Electron executable SHA256 is not canonical"
  verify_extracted_acceptance_payload "$audit_root"
  cmp -s "$POLICY_FILE" "$audit_root/opt/taiji-agent/resources/linux-compatibility-policy.json" || fail "DEB policy is not byte-identical"
  cmp -s "$ABI_BUILD_REPORT" "$audit_root/opt/taiji-agent/resources/elf-abi-audit.json" || fail "DEB ABI report changed during packaging"
  grep -F "$POLICY_SHA256" "$control_root/preinst" >/dev/null || fail "DEB preinst policy hash mismatch"
  grep -F "$POLICY_ID" "$control_root/preinst" >/dev/null || fail "DEB preinst policy id mismatch"
  bash -n "$control_root/preinst"
  [ "$(dpkg-deb -f "$OUT_DEB" Package)" = "$TAIJI_PACKAGE_NAME" ] || fail "DEB package identity mismatch"
  [ "$(dpkg-deb -f "$OUT_DEB" Architecture)" = "$TAIJI_PACKAGE_ARCHITECTURE" ] || fail "DEB architecture mismatch"
  [ "$(dpkg-deb -f "$OUT_DEB" Maintainer)" = "$TAIJI_PACKAGE_MAINTAINER" ] || fail "DEB maintainer mismatch"
  [ "$(dpkg-deb -f "$OUT_DEB" Depends)" = "$TAIJI_DEBIAN_DEPENDS" ] || fail "DEB Depends mismatch"
  python3 "$PAYLOAD_VERIFIER" --root "$audit_root" >/dev/null
  extracted_icon_sha256="$(python3 "$ICON_VALIDATOR" \
    --web-static "$audit_root/opt/taiji-agent/runtime/web/static" \
    --install-icons "$audit_root/usr/share/icons/hicolor" \
    --resource-icon "$audit_root/opt/taiji-agent/resources/icons/taiji-agent.png" \
    --print-digest)"
  [ "$extracted_icon_sha256" = "$ICON_SET_SHA256" ] \
    || fail "Extracted DEB icon digest changed during packaging"
  python3 "$ELF_AUDITOR" --root "$audit_root" --policy "$POLICY_FILE" --output "$extracted_abi" >/dev/null
  cmp -s "$ABI_BUILD_REPORT" "$extracted_abi" || fail "ELF ABI audit changed after DEB extraction"
}

write_package_manifest() {
  local deb_sha256 electron_sha256 desktop_sha256 abi_sha256 upgrade_contract_sha256 icon_set_sha256 built_at_utc out_deb_name source_archive_sha256
  out_deb_name="$(basename "$OUT_DEB")"
  deb_sha256="$(sha256sum "$OUT_DEB" | awk '{print $1}')"
  local source_archive_ref source_inventory_ref
  source_archive_ref="${SOURCE_ARCHIVE_FD:+/proc/self/fd/$SOURCE_ARCHIVE_FD}"
  source_inventory_ref="${SOURCE_INVENTORY_FD:+/proc/self/fd/$SOURCE_INVENTORY_FD}"
  [ -n "$source_archive_ref" ] || source_archive_ref="$SOURCE_ARCHIVE_PATH"
  [ -n "$source_inventory_ref" ] || source_inventory_ref="$SOURCE_INVENTORY_PATH"
  source_archive_sha256="$(sha256sum "$source_archive_ref" | awk '{print $1}')"
  electron_sha256="$(sha256sum "$DESKTOP_RUNTIME/node_modules/electron/dist/electron" | awk '{print $1}')"
  [ "$electron_sha256" = "$PINNED_ELECTRON_EXECUTABLE_SHA256" ] \
    || fail "Packaged Electron executable SHA256 is not canonical"
  desktop_sha256="$(sha256sum "$DESKTOP_FILE" | awk '{print $1}')"
  abi_sha256="$(sha256sum "$ABI_REPORT_PATH" | awk '{print $1}')"
  upgrade_contract_sha256="$(sha256sum "$REPO_ROOT/packaging/linux/upgrade-data-contract.json" | awk '{print $1}')"
  icon_set_sha256="$ICON_SET_SHA256"
  printf '%s\n' "$icon_set_sha256" | grep -Eq '^[0-9a-f]{64}$' \
    || fail "Canonical icon digest is missing or invalid"
  built_at_utc="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  cat > "$MANIFEST_PATH" <<MANIFEST
{
  "schema": "taiji-package-manifest/v3",
  "package": "$TAIJI_PACKAGE_NAME",
  "version": "$VERSION",
  "architecture": "$TAIJI_PACKAGE_ARCHITECTURE",
  "source_commit": "$SOURCE_COMMIT",
  "source_archive_basename": "${SOURCE_ARCHIVE_BASENAME:-$(basename "$SOURCE_ARCHIVE_PATH")}",
  "source_archive_sha256": "$source_archive_sha256",
  "source_inventory_basename": "${SOURCE_INVENTORY_BASENAME:-$(basename "$SOURCE_INVENTORY_PATH")}",
  "source_inventory_sha256": "$SOURCE_INVENTORY_SHA256",
  "deb_basename": "$(basename "$OUT_DEB")",
  "deb_sha256": "$deb_sha256",
  "acceptance_binding_sha256": "$ACCEPTANCE_BINDING_SHA256",
  "acceptance_tools_manifest_sha256": "$ACCEPTANCE_TOOLS_MANIFEST_SHA256",
  "acceptance_entrypoint_sha256": "$ACCEPTANCE_ENTRYPOINT_SHA256",
  "installed_release_manifest_sha256": "$INSTALLED_RELEASE_MANIFEST_SHA256",
  "maintainer": "$TAIJI_PACKAGE_MAINTAINER",
  "compatibility_policy_id": "$POLICY_ID",
  "compatibility_policy_sha256": "$POLICY_SHA256",
  "upgrade_data_contract_id": "taiji-linux-upgrade-data-v1",
  "upgrade_data_contract_sha256": "$upgrade_contract_sha256",
  "elf_abi_audit_basename": "elf-abi-audit.json",
  "elf_abi_audit_sha256": "$abi_sha256",
  "python_dependency_lock_status": "$PYTHON_DEPENDENCY_LOCK_STATUS",
  "python_lock_basename": "$PYTHON_LOCK_BASENAME",
  "python_lock_sha256": "$PYTHON_LOCK_SHA256",
  "python_version": "$PYTHON_VERSION",
  "python_archive_sha256": "$PYTHON_ARCHIVE_SHA256",
  "python_executable_sha256": "$PYTHON_EXECUTABLE_SHA256",
  "uv_version": "$UV_VERSION",
  "uv_archive_sha256": "$UV_ARCHIVE_SHA256",
  "uv_executable_sha256": "$UV_EXECUTABLE_SHA256",
  "node_version": "$NODE_VERSION",
  "node_archive_sha256": "$NODE_ARCHIVE_SHA256",
  "node_executable_sha256": "$NODE_EXECUTABLE_SHA256",
  "electron_version": "$ELECTRON_VERSION",
  "electron_archive_sha256": "$ELECTRON_ARCHIVE_SHA256",
  "electron_executable_sha256": "$electron_sha256",
  "desktop_entry_sha256": "$desktop_sha256",
  "icon_set_sha256": "$icon_set_sha256",
  "built_at_utc": "$built_at_utc"
}
MANIFEST
  chmod 0644 "$MANIFEST_PATH"
  (cd "$OUT_DIR" && sha256sum "$out_deb_name" > "$out_deb_name.sha256")
}

write_launch_manifest() {
  local manifest_install_root="/opt/${INSTALL_ROOT##*/}"
  [ "$manifest_install_root" = "/opt/taiji-agent" ] || fail "Unexpected launch manifest install root: $manifest_install_root"
  python3 - "$LAUNCH_MANIFEST_PATH" "$TAIJI_PACKAGE_ARCHITECTURE" "$VERSION" "$SOURCE_COMMIT" "$manifest_install_root" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "schema": "taiji-release-manifest/v1",
    "platform": "linux",
    "arch": sys.argv[2],
    "version": sys.argv[3],
    "commit": sys.argv[4],
    "installRoot": sys.argv[5],
}
path.write_text(
    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
  chmod 0644 "$LAUNCH_MANIFEST_PATH"
}

stage_installed_acceptance_toolchain() {
  local source manifest_path owner_uid
  for source in \
    "$ACCEPTANCE_HELPER" \
    "$ACCEPTANCE_RUNNER_SOURCE" \
    "$ACCEPTANCE_ENTRYPOINT_SOURCE" \
    "$ACCEPTANCE_LAUNCHER_SOURCE" \
    "$REPO_ROOT/tools/taiji-desktop-acceptance/run-installed-electron-acceptance.js" \
    "$REPO_ROOT/tools/taiji-desktop-acceptance/assemble-target-evidence.py" \
    "$REPO_ROOT/tools/taiji-desktop-acceptance/observe-single-deb-install.py" \
    "$REPO_ROOT/packaging/linux/certification-matrix.json" \
    "$REPO_ROOT/scripts/validate-taiji-release-evidence.py" \
    "$REPO_ROOT/scripts/taiji-challenge-envelope.py" \
    "$REPO_ROOT/tools/taiji-release-evidence/signing-public.pem"; do
    [ -f "$source" ] && [ ! -L "$source" ] \
      || fail "Installed acceptance source is missing or unsafe: $source"
  done

  install -d -m 0755 "$ACCEPTANCE_ROOT" "$ACCEPTANCE_TOOLS_ROOT"
  install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent-acceptance" "$PKG_ROOT/usr/bin/taiji-agent-acceptance"
  install -m 0644 "$REPO_ROOT/packaging/linux/acceptance_runner.py" "$ACCEPTANCE_ROOT/acceptance-runner.py"
  install -m 0644 "$REPO_ROOT/packaging/linux/acceptance_tools_manifest.py" "$ACCEPTANCE_ROOT/acceptance_tools_manifest.py"
  install -m 0755 "$REPO_ROOT/taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh" "$ACCEPTANCE_ROOT/04_目标终端_桌面App验收并导出证据.sh"
  install -m 0644 "$REPO_ROOT/tools/taiji-desktop-acceptance/run-installed-electron-acceptance.js" "$ACCEPTANCE_TOOLS_ROOT/run-installed-electron-acceptance.js"
  install -m 0644 "$REPO_ROOT/tools/taiji-desktop-acceptance/assemble-target-evidence.py" "$ACCEPTANCE_TOOLS_ROOT/assemble-target-evidence.py"
  install -m 0644 "$REPO_ROOT/tools/taiji-desktop-acceptance/observe-single-deb-install.py" "$ACCEPTANCE_TOOLS_ROOT/observe-single-deb-install.py"
  install -m 0644 "$REPO_ROOT/packaging/linux/certification-matrix.json" "$ACCEPTANCE_TOOLS_ROOT/certification-matrix.json"
  install -m 0644 "$REPO_ROOT/scripts/validate-taiji-release-evidence.py" "$ACCEPTANCE_TOOLS_ROOT/validate-taiji-release-evidence.py"
  install -m 0644 "$REPO_ROOT/scripts/taiji-challenge-envelope.py" "$ACCEPTANCE_TOOLS_ROOT/taiji-challenge-envelope.py"
  install -m 0644 "$REPO_ROOT/tools/taiji-release-evidence/signing-public.pem" "$ACCEPTANCE_TOOLS_ROOT/signing-public.pem"

  manifest_path="$ACCEPTANCE_TOOLS_ROOT/acceptance-tools-manifest.json"
  python3 "$ACCEPTANCE_HELPER" create \
    --repo-root "$REPO_ROOT" \
    --source-commit "$SOURCE_COMMIT" \
    --output "$manifest_path"
  ACCEPTANCE_TOOLS_MANIFEST_SHA256="$(sha256sum "$manifest_path" | awk '{print $1}')"
  owner_uid="$(id -u)"
  python3 - "$ACCEPTANCE_HELPER" "$ACCEPTANCE_TOOLS_ROOT" "$SOURCE_COMMIT" "$ACCEPTANCE_TOOLS_MANIFEST_SHA256" "$owner_uid" <<'PY'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("taiji_acceptance_tools_manifest", sys.argv[1])
if spec is None or spec.loader is None:
    raise SystemExit("cannot load acceptance tools manifest helper")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
helper.verify_staged(Path(sys.argv[2]), sys.argv[3], sys.argv[4], int(sys.argv[5]))
PY

  ACCEPTANCE_ENTRYPOINT_SHA256="$(sha256sum "$PKG_ROOT/usr/bin/taiji-agent-acceptance" | awk '{print $1}')"
  INSTALLED_RELEASE_MANIFEST_SHA256="$(sha256sum "$LAUNCH_MANIFEST_PATH" | awk '{print $1}')"
  python3 - \
    "$ACCEPTANCE_BINDING_PATH" \
    "$VERSION" \
    "$SOURCE_COMMIT" \
    "$INSTALLED_RELEASE_MANIFEST_SHA256" \
    "$ACCEPTANCE_TOOLS_MANIFEST_SHA256" \
    "$(sha256sum "$ACCEPTANCE_ROOT/04_目标终端_桌面App验收并导出证据.sh" | awk '{print $1}')" \
    "$(sha256sum "$ACCEPTANCE_ROOT/acceptance_tools_manifest.py" | awk '{print $1}')" \
    "$(sha256sum "$ACCEPTANCE_ROOT/acceptance-runner.py" | awk '{print $1}')" \
    "$ACCEPTANCE_ENTRYPOINT_SHA256" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
version, source_commit = sys.argv[2:4]
digests = sys.argv[4:10]
if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
    raise SystemExit("acceptance binding source commit is invalid")
if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in digests):
    raise SystemExit("acceptance binding digest is invalid")
payload = {
    "schema": "taiji-installed-acceptance-binding/v1",
    "version": version,
    "source_commit": source_commit,
    "release_manifest_sha256": digests[0],
    "acceptance_tools_manifest_path": "/opt/taiji-agent/libexec/target-acceptance/验收工具/acceptance-tools-manifest.json",
    "acceptance_tools_manifest_sha256": digests[1],
    "launcher_path": "/opt/taiji-agent/libexec/target-acceptance/04_目标终端_桌面App验收并导出证据.sh",
    "launcher_sha256": digests[2],
    "helper_path": "/opt/taiji-agent/libexec/target-acceptance/acceptance_tools_manifest.py",
    "helper_sha256": digests[3],
    "runner_path": "/opt/taiji-agent/libexec/target-acceptance/acceptance-runner.py",
    "runner_sha256": digests[4],
    "entrypoint_path": "/usr/bin/taiji-agent-acceptance",
    "entrypoint_sha256": digests[5],
}
path.write_text(
    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
  chmod 0644 "$ACCEPTANCE_BINDING_PATH"
  ACCEPTANCE_BINDING_SHA256="$(sha256sum "$ACCEPTANCE_BINDING_PATH" | awk '{print $1}')"
  for source in \
    "$ACCEPTANCE_BINDING_SHA256" \
    "$ACCEPTANCE_TOOLS_MANIFEST_SHA256" \
    "$ACCEPTANCE_ENTRYPOINT_SHA256" \
    "$INSTALLED_RELEASE_MANIFEST_SHA256"; do
    printf '%s\n' "$source" | grep -Eq '^[0-9a-f]{64}$' \
      || fail "Installed acceptance identity is missing or invalid"
  done
}

if [ "$(uname -s)" != "Linux" ]; then fail "Refusing to build final DEB on non-Linux host"; fi
case "$(uname -m)" in x86_64|amd64) ;; *) fail "Refusing to build on non-x86_64 host: $(uname -m)" ;; esac
for cmd in dpkg dpkg-deb rsync sha256sum file ldd strings perl python3 openssl stat mktemp cmp readelf readlink date; do require_cmd "$cmd"; done
validate_source_archive_integrity
load_policy_contract
resolve_source_commit
validate_build_host_glibc
[ -n "$PACKAGED_NODE_ROOT" ] || fail "TAIJI_PACKAGED_NODE_ROOT is required for the verified offline Node runtime"
[ -d "$PRIVATE_LIBRARY_SYSROOT" ] || fail "Private-library sysroot is missing: $PRIVATE_LIBRARY_SYSROOT"
for component in "$RUNTIME_STAGER" "$PYTHON_RUNTIME_STAGER" "$ELECTRON_RUNTIME_STAGER" "$DESKTOP_JS_STAGER" "$PRIVATE_LIB_STAGER" "$ELF_AUDITOR" "$PREINST_RENDERER"; do [ -f "$component" ] || fail "Missing packaging component: $component"; done
[ -f "$ICON_VALIDATOR" ] || fail "Missing icon validator: $ICON_VALIDATOR"
SOURCE_AGENT_PYTHON="$SOURCE_AGENT_DIR/venv/bin/python"
[ -x "$SOURCE_AGENT_PYTHON" ] || fail "Missing Linux Agent venv: $SOURCE_AGENT_PYTHON"
adopt_sealed_build_inputs
validate_strict_toolchain_contract
validate_locked_python_environment
(cd "$SOURCE_AGENT_DIR" && "$SOURCE_AGENT_PYTHON" -m taiji_runtime.main --help >/dev/null 2>&1) || fail "Linux Agent venv module entrypoint failed"
verify_linux_electron_runtime
validate_desktop_entry "$DESKTOP_FILE"
validate_appstream_metadata
validate_packaged_config_template
scan_webui_offline_assets

rm -rf "$BUILD_ROOT"
mkdir -p "$INSTALL_ROOT/bin" "$INSTALL_ROOT/config" "$INSTALL_ROOT/licenses" "$INSTALL_ROOT/resources/icons" "$INSTALL_ROOT/scripts" "$INSTALL_ROOT/runtime/lib" "$AGENT_RUNTIME" "$WEB_RUNTIME" "$PKG_ROOT/DEBIAN" "$PKG_ROOT/usr/bin" "$PKG_ROOT/usr/share/applications" "$PKG_ROOT/usr/share/metainfo" "$OUT_DIR"
for size in 32 48 64 128 192 256 512; do
  mkdir -p "$PKG_ROOT/usr/share/icons/hicolor/${size}x${size}/apps"
done
chmod 0755 "$PKG_ROOT/opt" "$INSTALL_ROOT"
chmod 0755 "$INSTALL_ROOT/resources"
chmod 0755 "$INSTALL_ROOT/runtime/lib"
archive_old_packages
stage_python_runtime
install -m 0644 "$VERSION_FILE" "$INSTALL_ROOT/VERSION"
printf '%s\n' "$("$SOURCE_AGENT_PYTHON" -c 'import platform; print(platform.python_version())')" > "$AGENT_RUNTIME/PYTHON_VERSION"
chmod 0644 "$AGENT_RUNTIME/PYTHON_VERSION"
printf '%s\n' "$VERSION" > "$WEB_RUNTIME/PRODUCT_VERSION"
chmod 0644 "$WEB_RUNTIME/PRODUCT_VERSION"
install -m 0644 "$PAYLOAD_CONTRACT" "$INSTALL_ROOT/resources/payload-contract.json"
install -m 0644 "$POLICY_FILE" "$POLICY_INSTALL_PATH"
write_launch_manifest
stage_installed_acceptance_toolchain
python3 "$RUNTIME_STAGER" --repo-root "$REPO_ROOT" --install-root "$INSTALL_ROOT" --node-root "$PACKAGED_NODE_ROOT" --public-key-fingerprint "$ISSUER_PUBLIC_KEY_FINGERPRINT"
chmod 0755 "$INSTALL_ROOT/resources/license"
[ "$(sha256sum "$INSTALL_ROOT/runtime/node/bin/node" | awk '{print $1}')" = "$PINNED_NODE_EXECUTABLE_SHA256" ] \
  || fail "Staged Node runtime identity mismatch"
python3 "$PRIVATE_LIB_STAGER" --root "$PKG_ROOT" --policy "$POLICY_FILE" --sysroot "$PRIVATE_LIBRARY_SYSROOT" --output "$PRIVATE_STAGE_REPORT" >/dev/null
rsync -a "$LAB_DIR/config"/ "$INSTALL_ROOT/config"/
install -m 0755 "$LAB_DIR/scripts/runtime-env.sh" "$INSTALL_ROOT/scripts/runtime-env.sh"
install -m 0755 "$LAB_DIR/scripts/start-agent.sh" "$INSTALL_ROOT/scripts/start-agent.sh"
install -m 0755 "$LAB_DIR/scripts/start-webui.sh" "$INSTALL_ROOT/scripts/start-webui.sh"
install -m 0755 "$LAB_DIR/scripts/stop-all.sh" "$INSTALL_ROOT/scripts/stop-all.sh"
install -m 0755 "$LAB_DIR/scripts/health-check.sh" "$INSTALL_ROOT/scripts/health-check.sh"
install -m 0755 "$LAB_DIR/scripts/taiji-native-verify" "$INSTALL_ROOT/scripts/taiji-native-verify"
install -m 0755 "$LAB_DIR/scripts/taiji-agent-diagnose" "$INSTALL_ROOT/scripts/taiji-agent-diagnose"
install -m 0755 "$REPO_ROOT/packaging/linux/support_bundle.py" "$INSTALL_ROOT/scripts/support_bundle.py"
install -m 0644 "$LAB_DIR/scripts/sync-packaged-config.py" "$INSTALL_ROOT/scripts/sync-packaged-config.py"
rewrite_product_text_tokens "$INSTALL_ROOT/scripts"
[ -f "$SOURCE_AGENT_DIR/LICENSE" ] && install -m 0644 "$SOURCE_AGENT_DIR/LICENSE" "$INSTALL_ROOT/licenses/agent-runtime.LICENSE" || true
[ -f "$SOURCE_WEB_DIR/LICENSE" ] && install -m 0644 "$SOURCE_WEB_DIR/LICENSE" "$INSTALL_ROOT/licenses/web-runtime.LICENSE" || true
mkdir -p "$DESKTOP_RUNTIME/src" "$DESKTOP_RUNTIME/node_modules"
install -m 0644 "$APP_DIR/package.json" "$DESKTOP_RUNTIME/package.json"
"$PACKAGED_NODE_EXECUTABLE" "$DESKTOP_JS_STAGER" \
  --source "$APP_DIR/src" \
  --destination "$DESKTOP_RUNTIME/src" \
  --entry main.js \
  --entry preload.js
electron_archive_args=(--archive "$ELECTRON_ARCHIVE")
if [ -n "$ELECTRON_ARCHIVE_FD" ]; then
  [ -z "$ELECTRON_ARCHIVE" ] && [ -n "$ELECTRON_ARCHIVE_BASENAME" ] \
    || fail "Electron archive path and FD modes are mutually exclusive"
  electron_archive_args=(--archive-fd "$ELECTRON_ARCHIVE_FD" --archive-basename "$ELECTRON_ARCHIVE_BASENAME")
fi
python3 "$ELECTRON_RUNTIME_STAGER" \
  --source "$APP_DIR/node_modules/electron" \
  --destination "$DESKTOP_RUNTIME/node_modules/electron" \
  "${electron_archive_args[@]}" \
  --policy "$POLICY_FILE" \
  --require-linux-x86-64
validate_staged_toolchain_executables
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent" "$PKG_ROOT/usr/bin/taiji-agent"
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji" "$PKG_ROOT/usr/bin/taiji"
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent-diagnose" "$PKG_ROOT/usr/bin/taiji-agent-diagnose"
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent-support" "$PKG_ROOT/usr/bin/taiji-agent-support"
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-native-verify" "$INSTALL_ROOT/bin/taiji-native-verify"
install -m 0644 "$DESKTOP_FILE" "$PKG_ROOT/usr/share/applications/taiji-agent.desktop"
install -m 0644 "$SOURCE_WEB_DIR/static/favicon-512.png" "$INSTALL_ROOT/resources/icons/taiji-agent.png"
for size in 32 48 64 128 192 256 512; do
  install -m 0644 "$SOURCE_WEB_DIR/static/favicon-$size.png" "$PKG_ROOT/usr/share/icons/hicolor/${size}x${size}/apps/taiji-agent.png"
done
install -m 0644 "$APPSTREAM_FILE" "$PKG_ROOT/usr/share/metainfo/taiji-agent.metainfo.xml"
ICON_SET_SHA256="$(python3 "$ICON_VALIDATOR" \
  --web-static "$SOURCE_WEB_DIR/static" \
  --install-icons "$PKG_ROOT/usr/share/icons/hicolor" \
  --resource-icon "$INSTALL_ROOT/resources/icons/taiji-agent.png" \
  --print-digest)"
printf '%s\n' "$ICON_SET_SHA256" | grep -Eq '^[0-9a-f]{64}$' \
  || fail "Canonical icon digest is invalid"
python3 "$ELF_AUDITOR" --root "$PKG_ROOT" --policy "$POLICY_FILE" --output "$ABI_BUILD_REPORT" >/dev/null
install -m 0644 "$ABI_BUILD_REPORT" "$ABI_REPORT_PATH"
python3 "$PREINST_RENDERER" --template "$SCRIPT_DIR/preinst" --policy "$POLICY_FILE" --output "$PKG_ROOT/DEBIAN/preinst"
install -m 0755 "$SCRIPT_DIR/postinst" "$PKG_ROOT/DEBIAN/postinst"
install -m 0755 "$SCRIPT_DIR/prerm" "$PKG_ROOT/DEBIAN/prerm"
install -m 0755 "$SCRIPT_DIR/postrm" "$PKG_ROOT/DEBIAN/postrm"
installed_size="$(du -sk "$PKG_ROOT" | awk '{print $1}')"
cat > "$PKG_ROOT/DEBIAN/control" <<CONTROL
Package: $TAIJI_PACKAGE_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $TAIJI_PACKAGE_ARCHITECTURE
Installed-Size: $installed_size
Maintainer: $TAIJI_PACKAGE_MAINTAINER
Depends: $TAIJI_DEBIAN_DEPENDS
Description: Taiji Agent local desktop app
 Local desktop shell and offline runtime for Taiji Agent WebUI and Agent API.
CONTROL
python3 "$PAYLOAD_VERIFIER" --root "$PKG_ROOT" >/dev/null
scan_package_tree
validate_source_archive_integrity
dpkg-deb --root-owner-group -Zxz --build "$PKG_ROOT" "$OUT_DEB" >/dev/null
scan_deb_release_artifact
audit_deb_payload
write_package_manifest
validate_source_archive_integrity
echo "Built: $OUT_DEB"
echo "Checksum: $OUT_DEB.sha256"
echo "Manifest: $MANIFEST_PATH"
