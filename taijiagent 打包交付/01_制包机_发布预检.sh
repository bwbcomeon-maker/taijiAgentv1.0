#!/usr/bin/env bash
# Verify one policy-bound amd64 DEB before release.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SOURCE_TREE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
REPO_ROOT="$(printenv TAIJI_REPO_ROOT || printf '%s' "$SOURCE_TREE_ROOT")"
SOURCE_GATE="$SOURCE_TREE_ROOT/scripts/check-clean-worktree.sh"
TRUSTED_GIT="$SOURCE_TREE_ROOT/scripts/taiji-trusted-git"
CHECKSUM_FILE="$SCRIPT_DIR/SHA256SUMS.txt"
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
BUILD_REPORT="$OUTPUT_DIR/构建报告.txt"
BUILD_MARKER="$OUTPUT_DIR/.build-success"
MANIFEST_FILE="$OUTPUT_DIR/taiji-package-manifest.json"
POLICY_FILE="$REPO_ROOT/packaging/linux/compatibility-policy.json"
POLICY_HELPER="$REPO_ROOT/packaging/linux/compatibility_policy.py"
PAYLOAD_VERIFIER="$REPO_ROOT/packaging/linux/verify-payload.py"
ICON_VALIDATOR="$REPO_ROOT/packaging/linux/validate_icon_assets.py"
ACCEPTANCE_TOOLS="$SCRIPT_DIR/验收工具"
REQUIRE_ARTIFACTS="$(printenv TAIJI_RELEASE_REQUIRE_ARTIFACTS || printf 0)"
SKIP_GIT_CHECK="$(printenv TAIJI_RELEASE_SKIP_GIT_CHECK || printf 0)"
SOURCE_ARCHIVE=""
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
check_formal_source_toolchain_contract() {
  python3 - "$SOURCE_ARCHIVE" <<'PY' \
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
        'NODE_VERSION="22.23.1"',
        'NODE_ARCHIVE_SHA256="9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"',
        'NODE_PINNED_EXECUTABLE_SHA256="93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068"',
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
        'PINNED_LOCK_CONTRACT_HELPER_SHA256="fca76118874d3846f1bddf304de0159160beff8467bef0870c3636858dedb9e6"',
        'PACKAGED_NODE_VERSION="22.23.1"',
        'PACKAGED_NODE_ARCHIVE_SHA256="9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"',
        'PINNED_NODE_EXECUTABLE_SHA256="93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068"',
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
  POLICY_ID="$(python3 "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-id)"
  POLICY_SHA256="$(python3 "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-sha256)"
  POLICY_MAINTAINER="$(python3 "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-maintainer)"
  hex64 "$POLICY_SHA256" || fail "canonical policy SHA256 格式非法"
}
verify_deb_checksum_sidecar() {
  local deb="$1" expected target actual name; name="$(basename "$deb")"; [ -f "$deb.sha256" ] || fail "缺少 DEB SHA256 sidecar：$name.sha256"
  expected="$(awk 'NR==1 {print $1; exit}' "$deb.sha256")"; target="$(awk 'NR==1 {$1=""; sub(/^[[:space:]]+\*?/,""); print; exit}' "$deb.sha256")"; hex64 "$expected" || fail "DEB SHA256 sidecar 格式非法：$name.sha256"; [ "$target" = "$name" ] || fail "DEB SHA256 sidecar 指向错误文件：$target"; actual="$(sha256sum "$deb" | awk '{print $1}')"; [ "$actual" = "$expected" ] || fail "DEB SHA256 不匹配：$name"
}
verify_marker_and_manifest() {
  local deb="$1"
  python3 - "$BUILD_MARKER" "$MANIFEST_FILE" "$deb" "$SOURCE_ARCHIVE" "$POLICY_ID" "$POLICY_SHA256" "$POLICY_MAINTAINER" "$POLICY_FILE" <<'PY'
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
toolchain_fields = {
    "python_dependency_lock_status",
    "python_lock_basename",
    "python_lock_sha256",
    "python_version",
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
required = {"version","source_archive","source_sha256","source_commit","deb","deb_sha256","checksum","built_at_utc","manifest","compatibility_policy_id","compatibility_policy_sha256","elf_abi_audit_sha256","icon_set_sha256","maintainer"} | toolchain_fields
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
    "deb_basename": marker["deb"],
    "deb_sha256": marker["deb_sha256"],
    "maintainer": maintainer,
    "compatibility_policy_id": policy_id,
    "compatibility_policy_sha256": policy_sha,
    "elf_abi_audit_basename": "elf-abi-audit.json",
    "elf_abi_audit_sha256": marker["elf_abi_audit_sha256"],
    "icon_set_sha256": marker["icon_set_sha256"],
    **{field: marker[field] for field in toolchain_fields},
}
for key, value in expected.items():
    if manifest.get(key) != value:
        raise SystemExit("manifest binding mismatch: " + key)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
if marker["source_archive"] != source_path.name or marker["source_sha256"] != sha(source_path):
    raise SystemExit("marker source binding mismatch")
if marker["deb"] != deb_path.name or marker["deb_sha256"] != sha(deb_path):
    raise SystemExit("marker DEB binding mismatch")
if marker["manifest"] != manifest_path.name or marker["checksum"] != deb_path.name + ".sha256":
    raise SystemExit("marker output binding mismatch")
if marker["compatibility_policy_id"] != policy_id or marker["compatibility_policy_sha256"] != policy_sha:
    raise SystemExit("marker policy binding mismatch")
if marker["maintainer"] != maintainer:
    raise SystemExit("marker maintainer binding mismatch")
if not re.fullmatch(r"[0-9a-f]{40}", marker["source_commit"]):
    raise SystemExit("marker source_commit must be full SHA")
if not re.fullmatch(r"[0-9a-f]{64}", marker["elf_abi_audit_sha256"]):
    raise SystemExit("marker ABI audit SHA256 invalid")
if not re.fullmatch(r"[0-9a-f]{64}", marker["icon_set_sha256"]):
    raise SystemExit("marker icon set SHA256 invalid")
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
if not re.fullmatch(r"3\.11\.[0-9]+", marker["python_version"]):
    raise SystemExit("formal build marker Python version is not 3.11.x")
if marker["uv_version"] != "0.12.2" or marker["uv_archive_sha256"] != "d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4" or marker["uv_executable_sha256"] != "72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2":
    raise SystemExit("formal build marker uv identity is not pinned")
if marker["node_version"] != "22.23.1" or marker["node_archive_sha256"] != "9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578" or marker["node_executable_sha256"] != "93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068":
    raise SystemExit("formal build marker Node identity is not pinned")
policy = json.loads(policy_path.read_text(encoding="utf-8"))
electron = policy["elf"]["electron_distribution"]
if marker["electron_version"] != electron["version"] or marker["electron_archive_sha256"] != electron["archive_sha256"]:
    raise SystemExit("formal build marker Electron identity differs from canonical policy")
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
  python3 "$PAYLOAD_VERIFIER" --root "$payload_root" >/dev/null || { remove_release_temp_directory "$payload_root"; fail "DEB payload contract 验证失败"; }
  [ -f "$ICON_VALIDATOR" ] && [ ! -L "$ICON_VALIDATOR" ] || { remove_release_temp_directory "$payload_root"; fail "缺少可信图标校验器：$ICON_VALIDATOR"; }
  icon_sha256="$(python3 "$ICON_VALIDATOR" \
    --web-static "$payload_root/opt/taiji-agent/runtime/web/static" \
    --install-icons "$payload_root/usr/share/icons/hicolor" \
    --resource-icon "$payload_root/opt/taiji-agent/resources/icons/taiji-agent.png" \
    --print-digest)" || { remove_release_temp_directory "$payload_root"; fail "DEB 图标链验证失败"; }
  marker_icon_sha256="$(awk -F= '$1=="icon_set_sha256" {print $2}' "$BUILD_MARKER")"
  [ "$icon_sha256" = "$marker_icon_sha256" ] || { remove_release_temp_directory "$payload_root"; fail "DEB 实际图标摘要与 marker 不一致"; }
  python3 - "$payload_root" "$MANIFEST_FILE" <<'PY' || { remove_release_temp_directory "$payload_root"; fail "DEB 工具链可执行文件摘要与 manifest 不一致"; }
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
paths = {
    "python_executable_sha256": root / "opt/taiji-agent/runtime/agent/venv/bin/python",
    "node_executable_sha256": root / "opt/taiji-agent/runtime/node/bin/node",
    "electron_executable_sha256": root / "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron",
}
for field, path in paths.items():
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != manifest.get(field):
        raise SystemExit(field + " mismatch")
PY
  remove_release_temp_directory "$payload_root"
}
verify_package_output_allowlist() {
  local deb="$1" name
  name="$(basename -- "$deb")"
  python3 - "$OUTPUT_DIR" "$name" <<'PY'
import os
import stat
import sys
from pathlib import Path
root = Path(sys.argv[1])
name = sys.argv[2]
expected = {name, name + ".sha256", ".build-success", "taiji-package-manifest.json", "构建报告.txt"}
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
  load_policy
  [ -d "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ] && [ -f "$BUILD_MARKER" ] && [ -f "$MANIFEST_FILE" ] && [ -f "$BUILD_REPORT" ] \
    || fail "生成的安装包目录必须是真实目录且包含 marker/manifest/report"
  local count deb; count="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' | wc -l | tr -d ' ')"; [ "$count" = 1 ] || fail "生成的安装包必须且只能有一个 amd64 DEB，当前数量：$count"; deb="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' | head -1)"
  verify_marker_and_manifest "$deb"; verify_deb_checksum_sidecar "$deb"; verify_package_output_allowlist "$deb"; verify_deb_payload "$deb"; verify_target_acceptance_toolchain; ok "单一 DEB、policy、manifest、ABI audit 和输出清单验证通过"
}
main() {
  info "执行太极 Agent 发布预检"
  check_single_source_archive
  check_source_checksum
  check_formal_source_toolchain_contract
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
