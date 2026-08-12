#!/bin/bash -p
# Verify one policy-bound amd64 DEB before release.
set -Eeuo pipefail
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONBREAKPOINT PYTHONUSERBASE
unset LD_PRELOAD LD_LIBRARY_PATH DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
unset OPENSSL_CONF OPENSSL_MODULES
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SOURCE_TREE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
REPO_ROOT="$(printenv TAIJI_REPO_ROOT || printf '%s' "$SOURCE_TREE_ROOT")"
SOURCE_GATE="$SOURCE_TREE_ROOT/scripts/check-clean-worktree.sh"
TRUSTED_GIT="$SOURCE_TREE_ROOT/scripts/taiji-trusted-git"
CHECKSUM_FILE="$SCRIPT_DIR/SHA256SUMS.txt"
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
BUILD_REPORT="$OUTPUT_DIR/构建报告.txt"
FORMAL_BUILD_TEST_LOG="$OUTPUT_DIR/formal-build-tests.log"
BUILD_MARKER_OVERRIDE="$(printenv TAIJI_BUILD_MARKER_PATH || true)"
BUILD_MARKER="${BUILD_MARKER_OVERRIDE:-$OUTPUT_DIR/.build-success}"
EXPECT_PUBLISHED_BUILD_MARKER="$(printenv TAIJI_EXPECT_PUBLISHED_BUILD_MARKER || printf 1)"
MANIFEST_FILE="$OUTPUT_DIR/taiji-package-manifest.json"
POLICY_FILE="$REPO_ROOT/packaging/linux/compatibility-policy.json"
POLICY_HELPER="$REPO_ROOT/packaging/linux/compatibility_policy.py"
PAYLOAD_VERIFIER="$REPO_ROOT/packaging/linux/verify-payload.py"
ICON_VALIDATOR="$REPO_ROOT/packaging/linux/validate_icon_assets.py"
ACCEPTANCE_TOOLS="$SCRIPT_DIR/验收工具"
REQUIRE_ARTIFACTS="$(printenv TAIJI_RELEASE_REQUIRE_ARTIFACTS || printf 0)"
SKIP_GIT_CHECK="$(printenv TAIJI_RELEASE_SKIP_GIT_CHECK || printf 0)"
EXTRACTED_SOURCE_ROOT="$(printenv TAIJI_EXTRACTED_SOURCE_ROOT || true)"
SOURCE_ARCHIVE=""
SOURCE_INVENTORY=""
SOURCE_INTEGRITY_HELPER="$SCRIPT_DIR/source-archive-integrity.py"
SOURCE_INTEGRITY_HELPER_SHA256="eaebadbe2f86d76d09f19ed210ad407e5926a242c46f53fb89e26253db8d8d7a"
POLICY_ID=""
POLICY_SHA256=""
POLICY_MAINTAINER=""
RELEASE_TEMP_ROOT="${TMPDIR:-/var/tmp}"
SOURCE_COMPARE_MIN_FREE_MIB="1024"
SOURCE_COMPARE_MIN_FREE_INODES="10000"
PAYLOAD_VERIFY_MIN_FREE_MIB="2048"
PAYLOAD_VERIFY_MIN_FREE_INODES="50000"
ACTIVE_RELEASE_TEMP_FILE=""
ACTIVE_RELEASE_TEMP_DIRECTORY=""

ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
hex64() { [ "$(printf '%s' "$1" | wc -c | tr -d ' ')" = 64 ] && printf '%s' "$1" | grep -Eq '^[0-9a-fA-F]{64}$'; }
sha256sum() {
  if [ -x /usr/bin/sha256sum ]; then
    /usr/bin/sha256sum "$@"
  elif [ -x /usr/bin/shasum ]; then
    /usr/bin/shasum -a 256 "$@"
  else
    fail "缺少受信 SHA256 工具（/usr/bin/sha256sum 或 /usr/bin/shasum）"
  fi
}

validate_release_temp_root() {
  case "$RELEASE_TEMP_ROOT" in
    /*) ;;
    *) fail "发布预检临时目录必须是绝对路径：$RELEASE_TEMP_ROOT" ;;
  esac
  [ -d "$RELEASE_TEMP_ROOT" ] && [ ! -L "$RELEASE_TEMP_ROOT" ] && [ -w "$RELEASE_TEMP_ROOT" ] \
    || fail "发布预检临时目录不可用或不安全：$RELEASE_TEMP_ROOT"
}
require_release_temp_capacity() {
  local required_mib="$1" required_inodes="$2" available_kib available_mib available_inodes
  validate_release_temp_root
  have df || fail "缺少 df，无法执行发布预检容量门禁"
  if ! available_kib="$(LC_ALL=C df -Pk -- "$RELEASE_TEMP_ROOT" 2>/dev/null | awk 'NR == 2 {print $4}')"; then
    fail "无法读取发布预检临时目录可用空间：$RELEASE_TEMP_ROOT"
  fi
  if ! available_inodes="$(LC_ALL=C df -Pi -- "$RELEASE_TEMP_ROOT" 2>/dev/null | awk 'NR == 2 {print $(NF-2)}')"; then
    fail "无法读取发布预检临时目录可用 inode：$RELEASE_TEMP_ROOT"
  fi
  case "$available_kib" in ''|*[!0-9]*) fail "无法读取发布预检临时目录可用空间：$RELEASE_TEMP_ROOT" ;; esac
  case "$available_inodes" in ''|*[!0-9]*) fail "无法读取发布预检临时目录可用 inode：$RELEASE_TEMP_ROOT" ;; esac
  available_mib=$((available_kib / 1024))
  [ "$available_mib" -ge "$required_mib" ] \
    || fail "发布预检临时目录可用空间不足：${available_mib} MiB，至少需要 ${required_mib} MiB（${RELEASE_TEMP_ROOT}）"
  [ "$available_inodes" -ge "$required_inodes" ] \
    || fail "发布预检临时目录可用 inode 不足：${available_inodes}，至少需要 ${required_inodes}（${RELEASE_TEMP_ROOT}）"
}
new_release_temp_file() {
  local target_variable="$1" template="$2"
  validate_release_temp_root
  ACTIVE_RELEASE_TEMP_FILE="$(mktemp "$RELEASE_TEMP_ROOT/taiji-release-$template")"
  printf -v "$target_variable" '%s' "$ACTIVE_RELEASE_TEMP_FILE"
}
new_release_temp_directory() {
  local target_variable="$1" template="$2"
  validate_release_temp_root
  ACTIVE_RELEASE_TEMP_DIRECTORY="$(mktemp -d "$RELEASE_TEMP_ROOT/taiji-release-$template")"
  printf -v "$target_variable" '%s' "$ACTIVE_RELEASE_TEMP_DIRECTORY"
}
is_controlled_release_temp_file() {
  local path="${1:-}" parent name
  [ -n "$path" ] || return 1
  parent="$(dirname -- "$path")" || return 1
  name="$(basename -- "$path")" || return 1
  [ "$parent" = "${RELEASE_TEMP_ROOT%/}" ] || return 1
  case "$name" in
    taiji-release-*) return 0 ;;
    *) return 1 ;;
  esac
}
is_controlled_release_temp_directory() {
  local path="${1:-}"
  is_controlled_release_temp_file "$path" || return 1
  [ -d "$path" ] && [ ! -L "$path" ]
}
remove_release_temp_file() {
  local path="$1"
  is_controlled_release_temp_file "$path" \
    || fail "拒绝清理未受控的发布临时文件：$path"
  rm -f -- "$path" || fail "无法清理发布临时文件：$path"
  [ ! -e "$path" ] && [ ! -L "$path" ] \
    || fail "发布临时文件清理不完整：$path"
  if [ "${ACTIVE_RELEASE_TEMP_FILE:-}" = "$path" ]; then
    ACTIVE_RELEASE_TEMP_FILE=""
  fi
}
remove_release_temp_directory() {
  local path="$1"
  is_controlled_release_temp_directory "$path" \
    || fail "发布临时目录类型不安全，拒绝清理：$path"
  find -P "$path" -type d -exec chmod u+rwx {} \; \
    || fail "无法恢复发布临时目录的清理权限：$path"
  rm -rf -- "$path" || fail "无法清理发布临时目录：$path"
  [ ! -e "$path" ] && [ ! -L "$path" ] \
    || fail "发布临时目录清理不完整：$path"
  if [ "${ACTIVE_RELEASE_TEMP_DIRECTORY:-}" = "$path" ]; then
    ACTIVE_RELEASE_TEMP_DIRECTORY=""
  fi
}
cleanup_release_temp_artifacts() {
  local status=$? path
  trap - EXIT INT TERM HUP
  path="${ACTIVE_RELEASE_TEMP_FILE:-}"
  if is_controlled_release_temp_file "$path"; then
    rm -f -- "$path" >/dev/null 2>&1 || true
  fi
  path="${ACTIVE_RELEASE_TEMP_DIRECTORY:-}"
  if is_controlled_release_temp_directory "$path"; then
    find -P "$path" -type d -exec chmod u+rwx {} \; >/dev/null 2>&1 || true
    rm -rf -- "$path" >/dev/null 2>&1 || true
  fi
  exit "$status"
}

checksum_source_archive_name() {
  [ -f "$CHECKSUM_FILE" ] || return 1
  awk 'NF >= 2 { h=$1; if (length(h)!=64 || h !~ /^[0-9A-Fa-f]+$/) next; p=$0; sub(/^[^[:space:]]+[[:space:]]+\*?/, "", p); n=split(p,a,"/"); if (a[n] ~ /^taiji-agentv1\.0-kylin-build-src-.*\.tar\.gz$/) print a[n] }' "$CHECKSUM_FILE"
}
checksum_source_archive_hash() {
  local wanted="$1"
  [ -f "$CHECKSUM_FILE" ] || return 1
  awk -v wanted="$wanted" 'NF >= 2 { h=$1; if (length(h)!=64 || h !~ /^[0-9A-Fa-f]+$/) next; p=$0; sub(/^[^[:space:]]+[[:space:]]+\*?/, "", p); n=split(p,a,"/"); if (a[n] == wanted) print h }' "$CHECKSUM_FILE" | tail -1
}
check_single_source_archive() {
  local count checksum_archive
  count="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | wc -l | tr -d ' ')"
  [ "$count" = 1 ] || fail "交付目录必须且只能有一个源码包，当前数量：$count"
  SOURCE_ARCHIVE="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | sort | head -1)"
  [ -f "$CHECKSUM_FILE" ] || fail "缺少 SHA256SUMS.txt"
  [ "$(checksum_source_archive_name | wc -l | tr -d ' ')" = 1 ] || fail "SHA256SUMS.txt 必须且只能指向一个源码包"
  checksum_archive="$(checksum_source_archive_name)"
  [ "$checksum_archive" = "$(basename "$SOURCE_ARCHIVE")" ] || fail "SHA256SUMS.txt 指向的源码包不是当前唯一源码包"
}
check_source_checksum() {
  local name expected actual
  name="$(basename "$SOURCE_ARCHIVE")"; expected="$(checksum_source_archive_hash "$name")"; hex64 "$expected" || fail "源码包 SHA256 格式非法：$name"
  actual="$(cd "$SCRIPT_DIR" && sha256sum "$name" | awk '{print $1}')"; [ "$actual" = "$expected" ] || fail "源码包 SHA256 不匹配：$name"; ok "源码包 SHA256 校验通过"
}
check_source_inventory() {
  local repository_helper inventory_name expected actual
  repository_helper="$SOURCE_TREE_ROOT/packaging/linux/source-archive-integrity.py"
  if [ ! -f "$SOURCE_INTEGRITY_HELPER" ] && [ -f "$repository_helper" ]; then
    SOURCE_INTEGRITY_HELPER="$repository_helper"
  fi
  [ -f "$SOURCE_INTEGRITY_HELPER" ] && [ ! -L "$SOURCE_INTEGRITY_HELPER" ] \
    || fail "缺少可信源码归档完整性工具"
  [ "$(sha256sum "$SOURCE_INTEGRITY_HELPER" | awk '{print $1}')" = "$SOURCE_INTEGRITY_HELPER_SHA256" ] \
    || fail "源码归档完整性工具不是固定审查版本"
  inventory_name="$(basename "$SOURCE_ARCHIVE" .tar.gz).inventory.json"
  SOURCE_INVENTORY="$SCRIPT_DIR/$inventory_name"
  [ -f "$SOURCE_INVENTORY" ] && [ ! -L "$SOURCE_INVENTORY" ] \
    || fail "缺少源码 archive-derived 成员清单：$inventory_name"
  expected="$(checksum_source_archive_hash "$inventory_name")"
  hex64 "$expected" || fail "源码成员清单 SHA256 格式非法"
  actual="$(sha256sum "$SOURCE_INVENTORY" | awk '{print $1}')"
  [ "$actual" = "$expected" ] || fail "源码成员清单 SHA256 不匹配"
  /usr/bin/python3 -I -B "$SOURCE_INTEGRITY_HELPER" verify \
    --archive "$SOURCE_ARCHIVE" \
    --inventory "$SOURCE_INVENTORY" \
    || fail "源码归档与 archive-derived 成员清单不一致"
  ok "源码 archive-derived 成员清单校验通过"
}
check_extracted_source_inventory() {
  [ -n "$EXTRACTED_SOURCE_ROOT" ] || return 0
  [ -d "$EXTRACTED_SOURCE_ROOT" ] && [ ! -L "$EXTRACTED_SOURCE_ROOT" ] \
    || fail "最终门禁指定的解压源码树不安全"
  /usr/bin/python3 -I -B "$SOURCE_INTEGRITY_HELPER" verify \
    --archive "$SOURCE_ARCHIVE" \
    --inventory "$SOURCE_INVENTORY" \
    --root "$EXTRACTED_SOURCE_ROOT" \
    --allow-extra-prefix "hermes-local-lab/sources/hermes-agent/venv" \
    --allow-extra-prefix "hermes-local-lab/sources/hermes-webui/node_modules" \
    --allow-extra-prefix "apps/taiji-desktop/node_modules" \
    --allow-extra-prefix "hermes-local-lab/sources/docx-engine-v2/node_modules" \
    --allow-extra-prefix "runtime/package-build" \
    --allow-extra-prefix "packages/麒麟操作系统安装包" \
    || fail "最终发布预检发现构建源码树已偏离原始归档"
}
check_formal_source_toolchain_contract() {
  /usr/bin/python3 -I -B - "$SOURCE_ARCHIVE" <<'PY' \
    || fail "formal source toolchain contract 校验失败"
import hashlib
import re
import sys
import tarfile

archive_path = sys.argv[1]
prefix = "taiji-agentv1.0/"
required_paths = {
    "builder": prefix + "taijiagent 打包交付/00_制包机_生成离线交付包.sh",
    "setup": prefix + "hermes-local-lab/scripts/setup-local.sh",
    "requirements": prefix + "hermes-local-lab/sources/hermes-webui/requirements.txt",
    "pyproject": prefix + "hermes-local-lab/sources/hermes-agent/pyproject.toml",
    "lock": prefix + "hermes-local-lab/sources/hermes-agent/uv.lock",
    "helper": prefix + "packaging/linux/verify-python-lock-contract.py",
    "source_integrity": prefix + "packaging/linux/source-archive-integrity.py",
    "deb_builder": prefix + "packaging/linux/deb/build-deb.sh",
}

with tarfile.open(archive_path, "r:gz") as archive:
    by_name = {}
    for member in archive.getmembers():
        if member.name not in required_paths.values():
            continue
        if member.name in by_name or not member.isfile() or member.size <= 0 or member.size > 32 * 1024 * 1024:
            raise SystemExit("formal source toolchain member is duplicate, unsafe, empty, or oversized")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise SystemExit("formal source toolchain member cannot be read")
        payload = extracted.read(member.size + 1)
        if len(payload) != member.size:
            raise SystemExit("formal source toolchain member changed while reading")
        by_name[member.name] = payload

missing = sorted(set(required_paths.values()) - set(by_name))
if missing:
    raise SystemExit("formal source toolchain members are missing: " + ", ".join(missing))

def text(label):
    try:
        return by_name[required_paths[label]].decode("utf-8")
    except UnicodeError as exc:
        raise SystemExit("formal source toolchain member is not UTF-8: " + label) from exc

def require_tokens(label, tokens):
    source = text(label)
    for token in tokens:
        if token not in source:
            raise SystemExit("formal source toolchain token is missing from %s: %s" % (label, token))
    return source

builder = require_tokens(
    "builder",
    (
        'UV_VERSION="0.12.2"',
        'UV_ARCHIVE_URL="https://github.com/astral-sh/uv/releases/download/0.12.2/uv-x86_64-unknown-linux-gnu.tar.gz"',
        'UV_ARCHIVE_SHA256="d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4"',
        'UV_PINNED_EXECUTABLE_SHA256="72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2"',
        'PYTHON_VERSION_PINNED="3.11.15"',
        'PYTHON_ARCHIVE_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260805/cpython-3.11.15%2B20260805-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"',
        'PYTHON_ARCHIVE_SHA256="2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a"',
        'PYTHON_PINNED_EXECUTABLE_SHA256="5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba"',
        'NODE_VERSION="22.23.1"',
        'NODE_ARCHIVE_SHA256="9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"',
        'NODE_PINNED_EXECUTABLE_SHA256="93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068"',
        'ELECTRON_PINNED_EXECUTABLE_SHA256="c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d"',
        'validate_formal_uv_contract',
        'auto|unlocked) fail',
        'uv_lock_mode="${TAIJI_UV_LOCK_MODE:-strict}"',
        'TAIJI_UV_EXECUTABLE="$UV_BIN"',
        'TAIJI_UV_ARCHIVE_PATH="$UV_ARCHIVE_PATH"',
        'TAIJI_NODE_ARCHIVE_PATH="$NODE_ARCHIVE_PATH"',
        'TAIJI_PYTHON_DEPENDENCY_LOCK_STATUS="$PYTHON_DEPENDENCY_LOCK_STATUS"',
        'lock 在 strict sync 前后发生变化',
    ),
)
for forbidden in ("https://astral.sh/uv/install.sh", "command -v uv", "\n  uv lock\n"):
    if forbidden in builder:
        raise SystemExit("formal source builder contains a forbidden downgrade path: " + forbidden)
main_matches = re.findall(r'(?ms)^main\(\) \{\n(.*?)^\}\n\nmain "\$@"\s*$', builder)
if len(main_matches) != 1:
    raise SystemExit("formal source builder must have one canonical main entry")
main_calls = []
for raw in main_matches[0].splitlines():
    active = raw.split("#", 1)[0].strip()
    if active:
        main_calls.append(active)
if not main_calls or main_calls[0] != "validate_formal_uv_contract":
    raise SystemExit("formal source builder must enter through strict validation before side effects")

setup = require_tokens(
    "setup",
    (
        "verify-python-lock-contract.py",
        "Production dependency setup requires strict",
        '"$UV_EXECUTABLE" sync "${sync_args[@]}" --locked',
        "--verify-installed",
    ),
)
if "uv pip install" in setup:
    raise SystemExit("formal production setup contains a second unlocked pip install")

deb_builder = require_tokens(
    "deb_builder",
    (
        'PINNED_UV_VERSION="0.12.2"',
        'PINNED_UV_ARCHIVE_SHA256="d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4"',
        'PINNED_UV_EXECUTABLE_SHA256="72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2"',
        'PINNED_PYTHON_VERSION="3.11.15"',
        'PINNED_PYTHON_ARCHIVE_SHA256="2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a"',
        'PINNED_PYTHON_EXECUTABLE_SHA256="5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba"',
        'PINNED_LOCK_CONTRACT_HELPER_SHA256="fca76118874d3846f1bddf304de0159160beff8467bef0870c3636858dedb9e6"',
        'PACKAGED_NODE_VERSION="22.23.1"',
        'PACKAGED_NODE_ARCHIVE_SHA256="9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"',
        'PINNED_NODE_EXECUTABLE_SHA256="93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068"',
        'PINNED_ELECTRON_EXECUTABLE_SHA256="c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d"',
        'TAIJI_PYTHON_DEPENDENCY_LOCK_STATUS must be strict-locked',
        'sha256sum "$UV_EXECUTABLE"',
        'sha256sum "$UV_ARCHIVE_PATH"',
        'sha256sum "$node_bin"',
        'sha256sum "$NODE_ARCHIVE_PATH"',
        'validate_locked_python_environment',
        '"$UV_EXECUTABLE" sync --extra all --locked --check',
        'validate_staged_toolchain_executables',
    ),
)

requirements = {
    line.strip()
    for line in text("requirements").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
}
expected_requirements = {"cryptography==46.0.7", "pypdf==6.14.2", "pyyaml==6.0.3"}
if requirements != expected_requirements:
    raise SystemExit("formal WebUI requirements are not the exact approved Agent lock subset")

helper = text("helper")
compile(helper, required_paths["helper"], "exec")
if hashlib.sha256(by_name[required_paths["helper"]]).hexdigest() != "fca76118874d3846f1bddf304de0159160beff8467bef0870c3636858dedb9e6":
    raise SystemExit("formal Python lock helper differs from the reviewed fixed implementation")
if "import tomllib" in helper or "verify_installed" not in helper:
    raise SystemExit("formal Python lock helper is not Python 3.8-compatible or lacks installed verification")

source_integrity = text("source_integrity")
compile(source_integrity, required_paths["source_integrity"], "exec")
if hashlib.sha256(by_name[required_paths["source_integrity"]]).hexdigest() != "eaebadbe2f86d76d09f19ed210ad407e5926a242c46f53fb89e26253db8d8d7a":
    raise SystemExit("formal source integrity helper differs from the reviewed fixed implementation")

exact_requirement = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)$"
)

def normalize_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()

def parse_requested(source):
    result = {}
    for line_number, raw in enumerate(source.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = exact_requirement.fullmatch(line)
        if match is None:
            raise SystemExit("formal WebUI requirement line %d is not exact" % line_number)
        name = normalize_name(match.group(1))
        if name in result:
            raise SystemExit("formal WebUI requirements contain a duplicate: " + name)
        result[name] = match.group(2)
    if not result:
        raise SystemExit("formal WebUI requirements are empty")
    return result

def parse_direct_dependencies(source):
    in_project = False
    in_dependencies = False
    dependencies = []
    for raw in source.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            in_dependencies = False
            continue
        if not in_project:
            continue
        if not in_dependencies:
            inline = re.fullmatch(r"dependencies\s*=\s*\[(.*)\]", line)
            if inline is not None:
                dependencies.extend(re.findall(r'"([^"\\]+)"', inline.group(1)))
                break
            if re.fullmatch(r"dependencies\s*=\s*\[", line):
                in_dependencies = True
            continue
        if line == "]":
            in_dependencies = False
            break
        match = re.match(r'^"([^"\\]+)"\s*,?', line)
        if match is None:
            if not line or line.startswith("#"):
                continue
            raise SystemExit("formal pyproject dependencies contain an unsupported entry")
        dependencies.append(match.group(1))
    if in_dependencies or not dependencies:
        raise SystemExit("formal pyproject project.dependencies is missing or incomplete")
    result = {}
    for dependency in dependencies:
        match = exact_requirement.fullmatch(dependency)
        if match is None:
            continue
        name = normalize_name(match.group(1))
        if name in result and result[name] != match.group(2):
            raise SystemExit("formal pyproject has conflicting direct dependency: " + name)
        result[name] = match.group(2)
    return result

def parse_locked_packages(source):
    result = {}
    current_name = None
    current_version = None

    def finish_package():
        if current_name is not None and current_version is not None:
            result.setdefault(normalize_name(current_name), set()).add(current_version)

    for raw in source.splitlines():
        line = raw.strip()
        if line == "[[package]]":
            finish_package()
            current_name = None
            current_version = None
            continue
        match = re.fullmatch(r'name\s*=\s*"([^"]+)"', line)
        if match is not None and current_name is None:
            current_name = match.group(1)
            continue
        match = re.fullmatch(r'version\s*=\s*"([^"]+)"', line)
        if match is not None and current_version is None:
            current_version = match.group(1)
    finish_package()
    if not result:
        raise SystemExit("formal uv.lock package tables are missing")
    return result

requested = parse_requested(text("requirements"))
direct = parse_direct_dependencies(text("pyproject"))
locked = parse_locked_packages(text("lock"))
for name, version in requested.items():
    if direct.get(name) != version:
        raise SystemExit(
            "formal WebUI requirement %s==%s is not the exact Agent direct dependency"
            % (name, version)
        )
    if locked.get(name) != {version}:
        raise SystemExit(
            "formal WebUI requirement %s==%s is not uniquely fixed by uv.lock"
            % (name, version)
        )
PY
  ok "formal source toolchain contract 校验通过"
}
check_git_clean_and_commit_match() {
  [ "$SKIP_GIT_CHECK" = 1 ] && return 0
  [ -e "$REPO_ROOT/.git" ] || return 0
  have git || fail "缺少 git，无法执行发布预检"
  [ -x "$SOURCE_GATE" ] || fail "缺少正式源码门禁：$SOURCE_GATE"
  "$SOURCE_GATE" --mode formal --repo-root "$REPO_ROOT" --source-root "$SOURCE_TREE_ROOT" || fail "正式发布必须来自干净本地 main"
  [ -x "$TRUSTED_GIT" ] || fail "缺少可信 Git 边界：$TRUSTED_GIT"
  local commit; commit="$("$TRUSTED_GIT" -C "$REPO_ROOT" rev-parse HEAD)"
  case "$(basename "$SOURCE_ARCHIVE")" in *-"$commit".tar.gz) ok "源码包与当前 commit 匹配：$commit" ;; *) fail "源码包不匹配当前 commit：$commit" ;; esac
}
check_source_archive_matches_git_head() {
  [ "$SKIP_GIT_CHECK" = 1 ] && return 0
  [ -e "$REPO_ROOT/.git" ] || return 0
  have gzip && have cmp || fail "缺少 gzip/cmp，无法核对源码包"
  [ -x "$TRUSTED_GIT" ] || fail "缺少可信 Git 边界：$TRUSTED_GIT"
  local expected_archive
  require_release_temp_capacity "$SOURCE_COMPARE_MIN_FREE_MIB" "$SOURCE_COMPARE_MIN_FREE_INODES"
  new_release_temp_file expected_archive "source-head.XXXXXX.tar"
  "$TRUSTED_GIT" -C "$REPO_ROOT" archive --format=tar --prefix=taiji-agentv1.0/ HEAD > "$expected_archive" || { remove_release_temp_file "$expected_archive"; fail "无法重建当前 HEAD 源码包"; }
  gzip -dc "$SOURCE_ARCHIVE" | cmp -s "$expected_archive" - || { remove_release_temp_file "$expected_archive"; fail "源码包归档内容与当前 Git HEAD 不一致"; }
  remove_release_temp_file "$expected_archive"; ok "源码包归档与当前 Git HEAD 一致"
}
check_no_macos_metadata_or_stale_zip() {
  local metadata zips stale_entries
  metadata="$(find "$SCRIPT_DIR" \( -name '__MACOSX' -o -name '.DS_Store' -o -name '._*' -o -name '.AppleDouble' -o -name 'PaxHeaders*' \) -print)"
  [ -z "$metadata" ] || { info "发现 macOS 拷贝元数据，将自动清理"; find "$SCRIPT_DIR" \( -name '__MACOSX' -o -name '.DS_Store' -o -name '._*' -o -name '.AppleDouble' -o -name 'PaxHeaders*' \) -exec rm -rf -- {} +; }
  zips="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name '*.zip' -print)"; [ -z "$zips" ] || fail "交付目录含旧 zip：$zips"
  if [ "$REQUIRE_ARTIFACTS" != 1 ]; then
    [ ! -L "$OUTPUT_DIR" ] || fail "生成的安装包目录不能是符号链接"
    stale_entries="$(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print 2>/dev/null || true)"
    [ -z "$stale_entries" ] || fail "发布预检发现上次制包残留；请通过 00 脚本自动归档后重试：$stale_entries"
  fi
}
load_policy() {
  [ -f "$POLICY_FILE" ] && [ ! -L "$POLICY_FILE" ] || fail "缺少 canonical compatibility policy：$POLICY_FILE"
  [ -f "$POLICY_HELPER" ] && [ ! -L "$POLICY_HELPER" ] || fail "缺少 compatibility policy helper：$POLICY_HELPER"
  POLICY_ID="$(/usr/bin/python3 -I -B "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-id)"
  POLICY_SHA256="$(/usr/bin/python3 -I -B "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-sha256)"
  POLICY_MAINTAINER="$(/usr/bin/python3 -I -B "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-maintainer)"
  hex64 "$POLICY_SHA256" || fail "canonical policy SHA256 格式非法"
}
verify_deb_checksum_sidecar() {
  local deb="$1" expected target actual name; name="$(basename "$deb")"; [ -f "$deb.sha256" ] || fail "缺少 DEB SHA256 sidecar：$name.sha256"
  expected="$(awk 'NR==1 {print $1; exit}' "$deb.sha256")"; target="$(awk 'NR==1 {$1=""; sub(/^[[:space:]]+\*?/,""); print; exit}' "$deb.sha256")"; hex64 "$expected" || fail "DEB SHA256 sidecar 格式非法：$name.sha256"; [ "$target" = "$name" ] || fail "DEB SHA256 sidecar 指向错误文件：$target"; actual="$(sha256sum "$deb" | awk '{print $1}')"; [ "$actual" = "$expected" ] || fail "DEB SHA256 不匹配：$name"
}
verify_marker_and_manifest() {
  local deb="$1"
  local -a formal_log_args
  /usr/bin/python3 -I -B - "$BUILD_MARKER" "$MANIFEST_FILE" "$deb" "$SOURCE_ARCHIVE" "$POLICY_ID" "$POLICY_SHA256" "$POLICY_MAINTAINER" "$POLICY_FILE" "$SOURCE_INVENTORY" <<'PY'
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path
marker_path = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
deb_path = Path(sys.argv[3])
source_path = Path(sys.argv[4])
policy_id = sys.argv[5]
policy_sha = sys.argv[6]
maintainer = sys.argv[7]
policy_path = Path(sys.argv[8])
inventory_path = Path(sys.argv[9])
toolchain_fields = {
    "python_dependency_lock_status",
    "python_lock_basename",
    "python_lock_sha256",
    "python_version",
    "python_archive_sha256",
    "python_executable_sha256",
    "uv_version",
    "uv_archive_sha256",
    "uv_executable_sha256",
    "node_version",
    "node_archive_sha256",
    "node_executable_sha256",
    "electron_version",
    "electron_archive_sha256",
    "electron_executable_sha256",
}
acceptance_fields = {
    "acceptance_binding_sha256",
    "acceptance_tools_manifest_sha256",
    "acceptance_entrypoint_sha256",
    "installed_release_manifest_sha256",
}
formal_build_test_fields = {
    "formal_build_tests_status",
    "formal_build_tests_log_basename",
    "formal_build_tests_log_sha256",
}
required = {"version","source_archive","source_sha256","source_commit","source_inventory","source_inventory_sha256","deb","deb_sha256","checksum","built_at_utc","manifest","compatibility_policy_id","compatibility_policy_sha256","elf_abi_audit_sha256","icon_set_sha256","maintainer"} | toolchain_fields | acceptance_fields | formal_build_test_fields
marker = {}
for line in marker_path.read_text(encoding="utf-8").splitlines():
    if not line or "=" not in line:
        raise SystemExit("invalid marker line")
    key, value = line.split("=", 1)
    if key in marker or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
        raise SystemExit("duplicate or invalid marker key")
    marker[key] = value
if set(marker) != required:
    raise SystemExit("marker keys are not the exact release contract")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
expected = {
    "schema": "taiji-package-manifest/v3",
    "version": marker["version"],
    "package": "taiji-agent",
    "architecture": "amd64",
    "source_commit": marker["source_commit"],
    "source_archive_basename": marker["source_archive"],
    "source_archive_sha256": marker["source_sha256"],
    "source_inventory_basename": marker["source_inventory"],
    "source_inventory_sha256": marker["source_inventory_sha256"],
    "deb_basename": marker["deb"],
    "deb_sha256": marker["deb_sha256"],
    "maintainer": maintainer,
    "compatibility_policy_id": policy_id,
    "compatibility_policy_sha256": policy_sha,
    "elf_abi_audit_basename": "elf-abi-audit.json",
    "elf_abi_audit_sha256": marker["elf_abi_audit_sha256"],
    "icon_set_sha256": marker["icon_set_sha256"],
    **{field: marker[field] for field in formal_build_test_fields},
    **{field: marker[field] for field in toolchain_fields},
    **{field: marker[field] for field in acceptance_fields},
}
for key, value in expected.items():
    if manifest.get(key) != value:
        raise SystemExit("manifest binding mismatch: " + key)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
if marker["source_archive"] != source_path.name or marker["source_sha256"] != sha(source_path):
    raise SystemExit("marker source binding mismatch")
if marker["source_inventory"] != inventory_path.name or marker["source_inventory_sha256"] != sha(inventory_path):
    raise SystemExit("marker source inventory binding mismatch")
if marker["deb"] != deb_path.name or marker["deb_sha256"] != sha(deb_path):
    raise SystemExit("marker DEB binding mismatch")
if marker["manifest"] != manifest_path.name or marker["checksum"] != deb_path.name + ".sha256":
    raise SystemExit("marker output binding mismatch")
if marker["compatibility_policy_id"] != policy_id or marker["compatibility_policy_sha256"] != policy_sha:
    raise SystemExit("marker policy binding mismatch")
if marker["maintainer"] != maintainer:
    raise SystemExit("marker maintainer binding mismatch")
if marker["formal_build_tests_status"] != "pass":
    raise SystemExit("formal build tests did not pass")
if marker["formal_build_tests_log_basename"] != "formal-build-tests.log":
    raise SystemExit("formal build test log basename is not canonical")
if not re.fullmatch(r"[0-9a-f]{64}", marker["formal_build_tests_log_sha256"]):
    raise SystemExit("formal build test log SHA256 is invalid")
if not re.fullmatch(r"[0-9a-f]{40}", marker["source_commit"]):
    raise SystemExit("marker source_commit must be full SHA")
if not re.fullmatch(r"[0-9a-f]{64}", marker["elf_abi_audit_sha256"]):
    raise SystemExit("marker ABI audit SHA256 invalid")
if not re.fullmatch(r"[0-9a-f]{64}", marker["icon_set_sha256"]):
    raise SystemExit("marker icon set SHA256 invalid")
for field in acceptance_fields:
    if not re.fullmatch(r"[0-9a-f]{64}", marker[field]):
        raise SystemExit("marker installed acceptance SHA256 invalid: " + field)
for field in (
    "python_lock_sha256",
    "python_executable_sha256",
    "uv_archive_sha256",
    "uv_executable_sha256",
    "node_archive_sha256",
    "node_executable_sha256",
    "electron_archive_sha256",
    "electron_executable_sha256",
):
    if not re.fullmatch(r"[0-9a-f]{64}", marker[field]):
        raise SystemExit("marker toolchain SHA256 invalid: " + field)
if marker["python_dependency_lock_status"] != "strict-locked":
    raise SystemExit("formal build marker is not strict-locked")
if marker["python_lock_basename"] != "uv.lock":
    raise SystemExit("formal build marker has an unexpected Python lock basename")
if marker["python_version"] != "3.11.15" or marker["python_archive_sha256"] != "2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a" or marker["python_executable_sha256"] != "5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba":
    raise SystemExit("formal build marker Python identity is not pinned")
if marker["uv_version"] != "0.12.2" or marker["uv_archive_sha256"] != "d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4" or marker["uv_executable_sha256"] != "72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2":
    raise SystemExit("formal build marker uv identity is not pinned")
if marker["node_version"] != "22.23.1" or marker["node_archive_sha256"] != "9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578" or marker["node_executable_sha256"] != "93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068":
    raise SystemExit("formal build marker Node identity is not pinned")
policy = json.loads(policy_path.read_text(encoding="utf-8"))
electron = policy["elf"]["electron_distribution"]
if marker["electron_version"] != electron["version"] or marker["electron_archive_sha256"] != electron["archive_sha256"]:
    raise SystemExit("formal build marker Electron identity differs from canonical policy")
canonical_electron = electron["elf_files"]["opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron"]["sha256"]
if canonical_electron != "c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d" or marker["electron_executable_sha256"] != canonical_electron:
    raise SystemExit("formal build marker Electron executable identity differs from canonical policy")
lock_member = "taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/uv.lock"
with tarfile.open(source_path, "r:gz") as archive:
    matches = [candidate for candidate in archive.getmembers() if candidate.name == lock_member]
    if len(matches) != 1:
        raise SystemExit("source archive must contain exactly one uv.lock")
    member = matches[0]
    if not member.isfile() or member.size <= 0 or member.size > 32 * 1024 * 1024:
        raise SystemExit("source archive uv.lock is not a safe regular file")
    extracted = archive.extractfile(member)
    payload = extracted.read(member.size + 1) if extracted is not None else b""
    if len(payload) != member.size or hashlib.sha256(payload).hexdigest() != marker["python_lock_sha256"]:
        raise SystemExit("source archive uv.lock SHA256 differs from formal build identity")
PY
  formal_log_args=(
    formal-build-test-log
    --manifest "$MANIFEST_FILE"
    --build-marker "$BUILD_MARKER"
    --log "$FORMAL_BUILD_TEST_LOG"
  )
  if [ "$EXPECT_PUBLISHED_BUILD_MARKER" = 0 ]; then
    formal_log_args+=(--pending-marker-parent "$(dirname "$EXTRACTED_SOURCE_ROOT")")
  fi
  /usr/bin/python3 -I -B "$REPO_ROOT/scripts/validate-taiji-release-evidence.py" \
    "${formal_log_args[@]}" \
    || fail "正式构建测试日志摘要或 strict v1 语义无效"
}
verify_deb_payload() {
  local deb="$1" payload_root abi embedded_policy abi_sha icon_sha256 marker_icon_sha256
  require_release_temp_capacity "$PAYLOAD_VERIFY_MIN_FREE_MIB" "$PAYLOAD_VERIFY_MIN_FREE_INODES"
  new_release_temp_directory payload_root "payload.XXXXXX"
  dpkg-deb -x "$deb" "$payload_root" || { remove_release_temp_directory "$payload_root"; fail "DEB 真实解包失败：$(basename "$deb")"; }
  embedded_policy="$payload_root/opt/taiji-agent/resources/linux-compatibility-policy.json"
  abi="$payload_root/opt/taiji-agent/resources/elf-abi-audit.json"
  [ -f "$embedded_policy" ] && [ ! -L "$embedded_policy" ] || { remove_release_temp_directory "$payload_root"; fail "DEB 缺少 embedded compatibility policy"; }
  [ -f "$abi" ] && [ ! -L "$abi" ] || { remove_release_temp_directory "$payload_root"; fail "DEB 缺少 embedded ELF ABI audit"; }
  cmp -s "$POLICY_FILE" "$embedded_policy" || { remove_release_temp_directory "$payload_root"; fail "DEB embedded policy 与源码 policy 不一致"; }
  abi_sha="$(sha256sum "$abi" | awk '{print $1}')"
  [ "$abi_sha" = "$(awk -F= '$1=="elf_abi_audit_sha256" {print $2}' "$BUILD_MARKER")" ] || { remove_release_temp_directory "$payload_root"; fail "DEB embedded ABI audit 与 marker 不一致"; }
  [ -f "$PAYLOAD_VERIFIER" ] && [ ! -L "$PAYLOAD_VERIFIER" ] || { remove_release_temp_directory "$payload_root"; fail "缺少可信 DEB payload verifier：$PAYLOAD_VERIFIER"; }
  /usr/bin/python3 -I -B "$PAYLOAD_VERIFIER" --root "$payload_root" >/dev/null || { remove_release_temp_directory "$payload_root"; fail "DEB payload contract 验证失败"; }
  [ -f "$ICON_VALIDATOR" ] && [ ! -L "$ICON_VALIDATOR" ] || { remove_release_temp_directory "$payload_root"; fail "缺少可信图标校验器：$ICON_VALIDATOR"; }
  icon_sha256="$(/usr/bin/python3 -I -B "$ICON_VALIDATOR" \
    --web-static "$payload_root/opt/taiji-agent/runtime/web/static" \
    --install-icons "$payload_root/usr/share/icons/hicolor" \
    --resource-icon "$payload_root/opt/taiji-agent/resources/icons/taiji-agent.png" \
    --print-digest)" || { remove_release_temp_directory "$payload_root"; fail "DEB 图标链验证失败"; }
  marker_icon_sha256="$(awk -F= '$1=="icon_set_sha256" {print $2}' "$BUILD_MARKER")"
  [ "$icon_sha256" = "$marker_icon_sha256" ] || { remove_release_temp_directory "$payload_root"; fail "DEB 实际图标摘要与 marker 不一致"; }
  /usr/bin/python3 -I -B - "$payload_root" "$MANIFEST_FILE" "$REPO_ROOT/packaging/linux/acceptance_tools_manifest.py" <<'PY' || { remove_release_temp_directory "$payload_root"; fail "DEB 工具链或安装态验收信任链与 manifest 不一致"; }
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
helper_source = Path(sys.argv[3])
paths = {
    "python_executable_sha256": root / "opt/taiji-agent/runtime/agent/venv/bin/python",
    "node_executable_sha256": root / "opt/taiji-agent/runtime/node/bin/node",
    "electron_executable_sha256": root / "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron",
    "acceptance_binding_sha256": root / "opt/taiji-agent/resources/taiji-acceptance-binding.json",
    "acceptance_tools_manifest_sha256": root / "opt/taiji-agent/libexec/target-acceptance/验收工具/acceptance-tools-manifest.json",
    "acceptance_entrypoint_sha256": root / "usr/bin/taiji-agent-acceptance",
    "installed_release_manifest_sha256": root / "opt/taiji-agent/resources/taiji-release-manifest.json",
}
fixed = {
    "python_executable_sha256": "5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba",
    "node_executable_sha256": "93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068",
    "electron_executable_sha256": "c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d",
}
for field, path in paths.items():
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != manifest.get(field):
        raise SystemExit(field + " mismatch")
    if field in fixed and manifest.get(field) != fixed[field]:
        raise SystemExit(field + " is not the canonical executable")

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit("installed acceptance JSON contains a duplicate field")
        result[key] = value
    return result

def canonical_json(path):
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    canonical = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if type(payload) is not dict or raw != canonical:
        raise SystemExit("installed acceptance JSON is not canonical: " + str(path))
    return payload

release = canonical_json(paths["installed_release_manifest_sha256"])
expected_release = {
    "schema": "taiji-release-manifest/v1",
    "platform": "linux",
    "arch": "amd64",
    "version": manifest["version"],
    "commit": manifest["source_commit"],
    "installRoot": "/opt/taiji-agent",
}
if release != expected_release:
    raise SystemExit("installed release manifest identity mismatch")

binding = canonical_json(paths["acceptance_binding_sha256"])
code_root = root / "opt/taiji-agent/libexec/target-acceptance"
expected_binding = {
    "schema": "taiji-installed-acceptance-binding/v1",
    "version": manifest["version"],
    "source_commit": manifest["source_commit"],
    "release_manifest_sha256": manifest["installed_release_manifest_sha256"],
    "acceptance_tools_manifest_path": "/opt/taiji-agent/libexec/target-acceptance/验收工具/acceptance-tools-manifest.json",
    "acceptance_tools_manifest_sha256": manifest["acceptance_tools_manifest_sha256"],
    "launcher_path": "/opt/taiji-agent/libexec/target-acceptance/04_目标终端_桌面App验收并导出证据.sh",
    "launcher_sha256": hashlib.sha256((code_root / "04_目标终端_桌面App验收并导出证据.sh").read_bytes()).hexdigest(),
    "helper_path": "/opt/taiji-agent/libexec/target-acceptance/acceptance_tools_manifest.py",
    "helper_sha256": hashlib.sha256((code_root / "acceptance_tools_manifest.py").read_bytes()).hexdigest(),
    "runner_path": "/opt/taiji-agent/libexec/target-acceptance/acceptance-runner.py",
    "runner_sha256": hashlib.sha256((code_root / "acceptance-runner.py").read_bytes()).hexdigest(),
    "entrypoint_path": "/usr/bin/taiji-agent-acceptance",
    "entrypoint_sha256": manifest["acceptance_entrypoint_sha256"],
}
if binding != expected_binding:
    raise SystemExit("installed acceptance binding mismatch")

spec = importlib.util.spec_from_file_location("taiji_acceptance_tools_manifest", helper_source)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load acceptance tools manifest helper")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
helper.verify_staged(
    code_root / "验收工具",
    manifest["source_commit"],
    manifest["acceptance_tools_manifest_sha256"],
    os.geteuid(),
)
PY
  remove_release_temp_directory "$payload_root"
}
verify_package_output_allowlist() {
  local deb="$1" name
  name="$(basename -- "$deb")"
  /usr/bin/python3 -I -B - "$OUTPUT_DIR" "$name" "$EXPECT_PUBLISHED_BUILD_MARKER" <<'PY'
import os
import stat
import sys
from pathlib import Path
root = Path(sys.argv[1])
name = sys.argv[2]
published_marker = sys.argv[3]
if published_marker not in {"0", "1"}:
    raise SystemExit("invalid published marker expectation")
expected = {name, name + ".sha256", "formal-build-tests.log", "taiji-package-manifest.json", "构建报告.txt"}
if published_marker == "1":
    expected.add(".build-success")
entries = {p.name: p for p in root.iterdir()}
if set(entries) != expected:
    raise SystemExit("output allowlist mismatch")
for path in entries.values():
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or path.is_symlink() or st.st_nlink != 1:
        raise SystemExit("unsafe output entry: " + path.name)
PY
}
verify_target_acceptance_toolchain() {
  local name script source_script root_acceptance_script root_acceptance_source
  [ "$REQUIRE_ARTIFACTS" = 1 ] || return 0
  root_acceptance_script="$SCRIPT_DIR/04_目标终端_桌面App验收并导出证据.sh"
  root_acceptance_source="$REPO_ROOT/taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh"
  [ -f "$root_acceptance_script" ] && [ ! -L "$root_acceptance_script" ] \
    && [ -f "$root_acceptance_source" ] && [ ! -L "$root_acceptance_source" ] \
    || fail "缺少或不安全的根目录目标终端验收脚本"
  cmp -s "$root_acceptance_script" "$root_acceptance_source" \
    || fail "根目录目标终端验收脚本与源码不一致"
  local -a files=(
    "run-installed-electron-acceptance.js"
    "assemble-target-evidence.py"
    "observe-single-deb-install.py"
    "certification-matrix.json"
    "assemble-taiji-certification-set.py"
    "validate-taiji-release-evidence.py"
    "taiji-challenge-envelope.py"
    "signing-public.pem"
    "management/taiji-silent-deploy.sh"
    "management/deployment_receipt.py"
    "management/upgrade_transaction.py"
    "management/upgrade-data-contract.json"
    "management/compatibility_policy.py"
    "management/compatibility-policy.json"
    "management/validate-taiji-release-evidence.py"
  )
  for name in "${files[@]}"; do
    script="$SCRIPT_DIR/验收工具/$name"
    case "$name" in
      run-installed-electron-acceptance.js|assemble-target-evidence.py|observe-single-deb-install.py)
        source_script="$REPO_ROOT/tools/taiji-desktop-acceptance/$name"
        ;;
      certification-matrix.json)
        source_script="$REPO_ROOT/packaging/linux/certification-matrix.json"
        ;;
      assemble-taiji-certification-set.py)
        source_script="$REPO_ROOT/scripts/assemble-taiji-certification-set.py"
        ;;
      validate-taiji-release-evidence.py)
        source_script="$REPO_ROOT/scripts/$name"
        ;;
      taiji-challenge-envelope.py)
        source_script="$REPO_ROOT/scripts/$name"
        ;;
      signing-public.pem)
        source_script="$REPO_ROOT/tools/taiji-release-evidence/$name"
        ;;
      management/taiji-silent-deploy.sh)
        source_script="$REPO_ROOT/packaging/linux/deb/taiji-silent-deploy.sh"
        ;;
      management/deployment_receipt.py)
        source_script="$REPO_ROOT/packaging/linux/deployment_receipt.py"
        ;;
      management/upgrade_transaction.py)
        source_script="$REPO_ROOT/packaging/linux/upgrade_transaction.py"
        ;;
      management/upgrade-data-contract.json)
        source_script="$REPO_ROOT/packaging/linux/upgrade-data-contract.json"
        ;;
      management/compatibility_policy.py)
        source_script="$REPO_ROOT/packaging/linux/compatibility_policy.py"
        ;;
      management/compatibility-policy.json)
        source_script="$REPO_ROOT/packaging/linux/compatibility-policy.json"
        ;;
      management/validate-taiji-release-evidence.py)
        source_script="$REPO_ROOT/scripts/validate-taiji-release-evidence.py"
        ;;
    esac
    [ -f "$script" ] && [ ! -L "$script" ] && [ -f "$source_script" ] && [ ! -L "$source_script" ] || fail "缺少或不安全的目标终端验收工具：$name"
    cmp -s "$script" "$source_script" || fail "目标终端验收工具与源码不一致：$name"
  done
}
check_delivery_artifacts() {
  [ "$REQUIRE_ARTIFACTS" = 1 ] || return 0
  case "$EXPECT_PUBLISHED_BUILD_MARKER" in
    1)
      [ "$BUILD_MARKER" = "$OUTPUT_DIR/.build-success" ] \
        || fail "已发布产物预检必须使用 canonical .build-success"
      ;;
    0)
      [ "$SKIP_GIT_CHECK" = 1 ] && [ -n "$EXTRACTED_SOURCE_ROOT" ] \
        || fail "待发布 marker 只允许 00 构建链在解压源码最终门禁中使用"
      [ "$(basename "$BUILD_MARKER")" = ".build-success.pending" ] \
        && [ "$(dirname "$BUILD_MARKER")" = "$(dirname "$EXTRACTED_SOURCE_ROOT")" ] \
        || fail "待发布 marker 未与当前构建根绑定"
      [ ! -e "$OUTPUT_DIR/.build-success" ] && [ ! -L "$OUTPUT_DIR/.build-success" ] \
        || fail "最终门禁前不得存在已发布 .build-success"
      ;;
    *) fail "TAIJI_EXPECT_PUBLISHED_BUILD_MARKER 只允许 0/1" ;;
  esac
  load_policy
  [ -d "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ] \
    && [ -f "$BUILD_MARKER" ] && [ ! -L "$BUILD_MARKER" ] \
    && [ -f "$MANIFEST_FILE" ] && [ ! -L "$MANIFEST_FILE" ] \
    && [ -f "$BUILD_REPORT" ] && [ ! -L "$BUILD_REPORT" ] \
    && [ -f "$FORMAL_BUILD_TEST_LOG" ] && [ ! -L "$FORMAL_BUILD_TEST_LOG" ] \
    || fail "生成的安装包目录必须是真实目录且包含 marker/manifest/report/formal test log"
  local count deb; count="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' | wc -l | tr -d ' ')"; [ "$count" = 1 ] || fail "生成的安装包必须且只能有一个 amd64 DEB，当前数量：$count"; deb="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' | head -1)"
  verify_marker_and_manifest "$deb"; verify_deb_checksum_sidecar "$deb"; verify_package_output_allowlist "$deb"; verify_deb_payload "$deb"; verify_target_acceptance_toolchain; ok "单一 DEB、policy、manifest、ABI audit 和输出清单验证通过"
}
main() {
  info "执行太极 Agent 发布预检"
  check_single_source_archive
  check_source_checksum
  check_source_inventory
  check_formal_source_toolchain_contract
  check_extracted_source_inventory
  check_git_clean_and_commit_match
  check_source_archive_matches_git_head
  check_no_macos_metadata_or_stale_zip
  check_delivery_artifacts
  ok "发布预检通过"
}
trap cleanup_release_temp_artifacts EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
main "$@"
