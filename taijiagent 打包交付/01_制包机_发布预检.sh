#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${TAIJI_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CHECKSUM_FILE="$SCRIPT_DIR/SHA256SUMS.txt"
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
OFFLINE_REPO="$SCRIPT_DIR/离线依赖"
BUILD_REPORT="$OUTPUT_DIR/构建报告.txt"
BUILD_MARKER="$OUTPUT_DIR/.build-success"
MANIFEST_FILE="$OUTPUT_DIR/taiji-package-manifest.json"
ACCEPTANCE_TOOLS="$SCRIPT_DIR/验收工具"
PAYLOAD_VERIFIER="$REPO_ROOT/packaging/linux/verify-payload.py"
REQUIRE_ARTIFACTS="${TAIJI_RELEASE_REQUIRE_ARTIFACTS:-0}"
SKIP_GIT_CHECK="${TAIJI_RELEASE_SKIP_GIT_CHECK:-0}"
SOURCE_ARCHIVE=""

ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

hex64() {
  local value="${1:-}"
  [ "${#value}" -eq 64 ] || return 1
  case "$value" in
    *[!0123456789abcdefABCDEF]*) return 1 ;;
    *) return 0 ;;
  esac
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

check_git_clean_and_commit_match() {
  [ "$SKIP_GIT_CHECK" = "1" ] && return 0
  [ -e "$REPO_ROOT/.git" ] || return 0
  have git || fail "缺少 git，无法执行发布预检"

  git -C "$REPO_ROOT" diff --quiet || fail "源码仓库存在未提交改动，不能发布"
  git -C "$REPO_ROOT" diff --cached --quiet || fail "源码仓库存在已暂存未提交改动，不能发布"
  if [ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ]; then
    git -C "$REPO_ROOT" status --short --untracked-files=all >&2
    fail "源码仓库存在未跟踪文件，不能发布"
  fi

  local short source_name
  short="$(git -C "$REPO_ROOT" rev-parse --short=8 HEAD)"
  source_name="$(basename "$SOURCE_ARCHIVE")"
  case "$source_name" in
    *-"$short".tar.gz)
      ok "源码包与当前 commit 匹配：$short"
      ;;
    *)
      fail "源码包不匹配当前 commit：当前=$short 源码包=$source_name"
      ;;
  esac
}

check_source_archive_matches_git_head() {
  [ "$SKIP_GIT_CHECK" = "1" ] && return 0
  [ -e "$REPO_ROOT/.git" ] || return 0
  have git || fail "缺少 git，无法重建当前 HEAD 源码包"
  have gzip || fail "缺少 gzip，无法重建当前 HEAD 源码包"
  have cmp || fail "缺少 cmp，无法逐字节核对当前 HEAD 源码包"
  local expected_archive
  expected_archive="$(mktemp /tmp/taiji-source-head.XXXXXX.tar.gz)"
  if ! git -C "$REPO_ROOT" archive --format=tar --prefix=taiji-agentv1.0/ HEAD | gzip -n > "$expected_archive"; then
    rm -f "$expected_archive"
    fail "无法从当前 HEAD 重建确定性源码包"
  fi
  if ! cmp -s "$expected_archive" "$SOURCE_ARCHIVE"; then
    rm -f "$expected_archive"
    fail "源码包内容与当前 git HEAD 不一致"
  fi
  rm -f "$expected_archive"
  ok "源码包内容与当前 git HEAD 逐字节一致"
}

check_single_source_archive() {
  local count checksum_count checksum_archive
  count="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | wc -l | tr -d ' ')"
  [ "$count" = "1" ] || fail "交付目录必须且只能有一个源码包，当前数量：$count"

  SOURCE_ARCHIVE="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | sort | head -1)"
  [ -f "$CHECKSUM_FILE" ] || fail "缺少 SHA256SUMS.txt"

  checksum_count="$(checksum_source_archive_name | wc -l | tr -d ' ')"
  [ "$checksum_count" = "1" ] || fail "SHA256SUMS.txt 必须且只能指向一个源码包，当前数量：$checksum_count"
  checksum_archive="$(checksum_source_archive_name)"
  [ "$checksum_archive" = "$(basename "$SOURCE_ARCHIVE")" ] || fail "SHA256SUMS.txt 指向的源码包不是当前目录唯一源码包：$checksum_archive"
}

check_source_checksum() {
  local expected actual archive_name
  archive_name="$(basename "$SOURCE_ARCHIVE")"
  expected="$(checksum_source_archive_hash "$archive_name")"
  hex64 "$expected" || fail "源码包 SHA256 格式非法：$archive_name"
  actual="$(cd "$SCRIPT_DIR" && sha256sum "$archive_name" | awk '{print $1}')"
  [ "$actual" = "$expected" ] || fail "源码包 SHA256 不匹配：$archive_name"
  ok "源码包 SHA256 校验通过"
}

check_no_macos_metadata_or_stale_zip() {
  local metadata zips stale_debs
  metadata="$(find "$SCRIPT_DIR" \( -name '__MACOSX' -o -name '.DS_Store' -o -name '._*' -o -name '.AppleDouble' -o -name 'PaxHeaders*' \) -print)"
  if [ -n "$metadata" ]; then
    info "发现 macOS 拷贝元数据，将自动清理"
    printf '%s\n' "$metadata" >&2
    find "$SCRIPT_DIR" \( -name '__MACOSX' -o -name '.DS_Store' -o -name '._*' -o -name '.AppleDouble' -o -name 'PaxHeaders*' \) -exec rm -rf -- {} +
  fi

  zips="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name '*.zip' -print)"
  [ -z "$zips" ] || {
    printf '%s\n' "$zips" >&2
    fail "交付目录含旧 zip，请删除后再发布"
  }

  if [ "$REQUIRE_ARTIFACTS" != "1" ]; then
    stale_debs="$(find "$OUTPUT_DIR" -maxdepth 1 -type f \( -name '*.deb' -o -name '*.deb.sha256' \) -print 2>/dev/null || true)"
    [ -z "$stale_debs" ] || {
      printf '%s\n' "$stale_debs" >&2
      fail "发布预检发现旧安装包，请先清理 生成的安装包/"
    }
  fi
}

cleanup_payload_verification_root() {
  local root="$1"
  case "$root" in
    /tmp/taiji-payload-verify.*)
      [ "${root#/tmp/}" = "${root##*/}" ] \
        || fail "拒绝清理嵌套的 payload 校验路径：$root"
      ;;
    *) fail "拒绝清理非专用 payload 校验目录：$root" ;;
  esac
  if [ -e "$root" ] || [ -L "$root" ]; then
    [ -d "$root" ] && [ ! -L "$root" ] && [ -O "$root" ] \
      || fail "payload 校验临时路径不是当前用户的实体目录：$root"
    find "$root" -xdev -type d -exec chmod u+w {} + \
      || fail "无法恢复 payload 校验临时目录的 owner 写权限：$root"
    rm -rf -- "$root" || fail "无法清理 payload 校验临时目录：$root"
  fi
  [ ! -e "$root" ] && [ ! -L "$root" ] \
    || fail "payload 校验临时目录清理后仍存在：$root"
}

verify_assembled_deb_payload() {
  local deb="$1" payload_root status manifest_status
  have dpkg-deb || fail "缺少 dpkg-deb，无法真实解包验证 payload"
  have python3 || fail "缺少 python3，无法执行 payload contract verifier"
  [ -f "$PAYLOAD_VERIFIER" ] || fail "缺少 payload verifier：$PAYLOAD_VERIFIER"
  payload_root="$(mktemp -d /tmp/taiji-payload-verify.XXXXXX)"
  if ! dpkg-deb -x "$deb" "$payload_root"; then
    cleanup_payload_verification_root "$payload_root"
    fail "DEB 真实解包失败：$(basename "$deb")"
  fi
  set +e
  python3 "$PAYLOAD_VERIFIER" --root "$payload_root"
  status=$?
  python3 - "$MANIFEST_FILE" \
    "$payload_root/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron" \
    "$payload_root/usr/share/applications/taiji-agent.desktop" <<'PY'
import hashlib
import json
import re
import stat
import sys
from pathlib import Path


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


manifest_path, electron_path, desktop_path = map(Path, sys.argv[1:])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


for field, path in (
    ("electron_executable_sha256", electron_path),
    ("desktop_entry_sha256", desktop_path),
):
    expected = manifest.get(field)
    if type(expected) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SystemExit(f"manifest field is missing or invalid: {field}")
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or path.is_symlink():
        raise SystemExit(f"unsafe payload file: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise SystemExit(f"payload hash does not match manifest: {field}")
PY
  manifest_status=$?
  set -e
  cleanup_payload_verification_root "$payload_root"
  [ "$status" -eq 0 ] || fail "DEB payload contract 验证失败：$(basename "$deb")"
  [ "$manifest_status" -eq 0 ] || fail "DEB 内 Electron/desktop entry 摘要与发布 manifest 不一致：$(basename "$deb")"
  ok "DEB 真实解包 payload contract 验证通过"
}

verify_deb_checksum_sidecar() {
  local deb="$1" sidecar expected target actual deb_name
  sidecar="${deb}.sha256"
  deb_name="$(basename "$deb")"
  [ -f "$sidecar" ] || fail "缺少 DEB SHA256 sidecar：$(basename "$sidecar")"

  expected="$(awk 'NR == 1 { print $1; exit }' "$sidecar")"
  target="$(awk 'NR == 1 { $1 = ""; sub(/^[ \t]+\*?/, ""); print; exit }' "$sidecar")"
  hex64 "$expected" || fail "DEB SHA256 sidecar 格式非法：$(basename "$sidecar")"
  [ "$target" = "$deb_name" ] || fail "DEB SHA256 sidecar 指向的文件不是当前 DEB：$target"

  actual="$(sha256sum "$deb" | awk '{print $1}')"
  [ "$actual" = "$expected" ] || fail "DEB SHA256 不匹配：$deb_name"
  ok "DEB SHA256 sidecar 校验通过：$deb_name"
}

verify_package_output_allowlist() {
  local deb="$1" deb_name
  deb_name="$(basename -- "$deb")"
  have python3 || fail "缺少 python3，无法核对安装包输出目录"

  python3 - "$OUTPUT_DIR" "$deb_name" <<'PY' || \
    fail "生成的安装包/ 含不允许的条目，必须严格匹配当前发布清单"
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
deb_name = sys.argv[2]
expected = {
    deb_name,
    f"{deb_name}.sha256",
    ".build-success",
    "taiji-package-manifest.json",
    "构建报告.txt",
}

root_mode = root.lstat().st_mode
if not stat.S_ISDIR(root_mode) or root.is_symlink():
    raise SystemExit("生成的安装包不是安全的真实目录")

entries = {entry.name: entry for entry in os.scandir(root)}
actual = set(entries)
if actual != expected:
    unexpected = sorted(actual - expected)
    missing = sorted(expected - actual)
    raise SystemExit(f"输出目录清单不匹配: unexpected={unexpected!r} missing={missing!r}")

for name in sorted(expected):
    entry = entries[name]
    metadata = entry.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or entry.is_symlink():
        raise SystemExit(f"输出条目不是安全的普通文件: {name}")
    if metadata.st_nlink != 1:
        raise SystemExit(f"输出条目存在硬链接: {name}")
PY
  ok "安装包输出目录与当前发布清单精确一致"
}

verify_offline_repository_integrity() {
  local release_deb="$1"
  have gzip || fail "缺少 gzip，无法验证离线仓库索引"
  have cmp || fail "缺少 cmp，无法核对离线仓库索引"
  have python3 || fail "缺少 python3，无法验证离线仓库清单"
  have dpkg-deb || fail "缺少 dpkg-deb，无法验证离线仓库 DEB"
  [ -f "$OFFLINE_REPO/SHA256SUMS.txt" ] || fail "离线仓库缺少 SHA256SUMS.txt"
  [ -f "$OFFLINE_REPO/runtime-dependencies.txt" ] || fail "离线仓库缺少 runtime-dependencies.txt"
  gzip -t "$OFFLINE_REPO/Packages.gz" || fail "离线仓库 Packages.gz 不是有效 gzip"
  gzip -dc "$OFFLINE_REPO/Packages.gz" | cmp -s - "$OFFLINE_REPO/Packages" \
    || fail "Packages.gz 解压内容与 Packages 不一致"

  python3 - "$OFFLINE_REPO" "$release_deb" <<'PY' || fail "离线仓库 SHA256/Packages 文件清单不一致"
import hashlib
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
release_deb = Path(sys.argv[2])


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


allowed_fixed = {"Packages", "Packages.gz", "runtime-dependencies.txt", "SHA256SUMS.txt"}
actual_files = set()
for entry in root.iterdir():
    mode = entry.lstat().st_mode
    if not stat.S_ISREG(mode) or entry.is_symlink() or entry.stat().st_nlink != 1:
        raise SystemExit(f"unsafe offline repository entry: {entry.name}")
    if entry.name not in allowed_fixed and not entry.name.endswith(".deb"):
        raise SystemExit(f"unexpected offline repository entry: {entry.name}")
    actual_files.add(entry.name)

checksums = {}
for raw in (root / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
    match = re.fullmatch(r"([0-9a-f]{64})[ \t]+\*?(?:\./)?([^/\s]+)", raw)
    if not match or match.group(2) == "SHA256SUMS.txt" or match.group(2) in checksums:
        raise SystemExit(f"invalid checksum line: {raw!r}")
    checksums[match.group(2)] = match.group(1)
expected_checksum_files = actual_files - {"SHA256SUMS.txt"}
if set(checksums) != expected_checksum_files:
    raise SystemExit("SHA256SUMS file set mismatch")
for name, expected in checksums.items():
    digest = sha256_file(root / name)
    if digest != expected:
        raise SystemExit(f"checksum mismatch: {name}")

indexed = set()
indexed_packages = set()
stanzas = {}
packages_text = (root / "Packages").read_text(encoding="utf-8")
for paragraph in re.split(r"\n[ \t]*\n", packages_text.strip()):
    fields = {}
    for raw in paragraph.splitlines():
        if not raw or raw[:1].isspace():
            continue
        if ":" not in raw:
            raise SystemExit(f"invalid Packages field: {raw!r}")
        key, value = raw.split(":", 1)
        if key in fields:
            raise SystemExit(f"duplicate Packages field: {key}")
        fields[key] = value.strip()
    required = {"Package", "Version", "Architecture", "Filename", "Size", "SHA256"}
    if not required <= fields.keys():
        raise SystemExit("Packages stanza is missing required integrity fields")
    value = fields["Filename"]
    value = value[2:] if value.startswith("./") else value
    if not value or "/" in value or not value.endswith(".deb") or value in indexed:
        raise SystemExit(f"unsafe or duplicate Packages Filename: {value!r}")
    if fields["Architecture"] not in {"amd64", "all"}:
        raise SystemExit(f"unsupported Packages Architecture: {fields['Architecture']!r}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*", fields["Package"]):
        raise SystemExit(f"unsafe Packages Package: {fields['Package']!r}")
    if not fields["Version"] or not re.fullmatch(r"[0-9]+", fields["Size"]):
        raise SystemExit("Packages Version/Size is invalid")
    package_path = root / value
    if int(fields["Size"]) != package_path.stat().st_size:
        raise SystemExit(f"Packages Size mismatch: {value}")
    if fields["SHA256"] != checksums.get(value):
        raise SystemExit(f"Packages SHA256 mismatch: {value}")
    indexed.add(value)
    indexed_packages.add(fields["Package"])
    stanzas[value] = fields
deb_files = {name for name in actual_files if name.endswith(".deb")}
if not deb_files or indexed != deb_files:
    raise SystemExit("Packages index does not exactly cover repository DEBs")

runtime_dependencies = set()
for raw in (root / "runtime-dependencies.txt").read_text(encoding="utf-8").splitlines():
    name = raw.strip().split(":", 1)[0]
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*", name):
        raise SystemExit(f"invalid runtime dependency: {raw!r}")
    runtime_dependencies.add(name)
if runtime_dependencies - indexed_packages:
    raise SystemExit("runtime-dependencies.txt contains packages absent from Packages")
if indexed_packages - runtime_dependencies - {"taiji-agent"}:
    raise SystemExit("Packages contains dependency packages absent from runtime-dependencies.txt")

direct_runtime_dependencies = set()
for field in ("Depends", "Pre-Depends"):
    result = subprocess.run(
        ["dpkg-deb", "-f", str(release_deb), field],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise SystemExit(f"无法读取主安装包 {field}: {result.stderr.strip()}")
    for clause in result.stdout.replace("\n", " ").split(","):
        candidate = clause.split("|", 1)[0]
        candidate = re.sub(r"\([^)]*\)|\[[^]]*\]|<[^>]*>", "", candidate).strip()
        if not candidate:
            continue
        name = candidate.split()[0].split(":", 1)[0]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*", name):
            raise SystemExit(f"主安装包含无法识别的 {field} 依赖: {clause!r}")
        direct_runtime_dependencies.add(name)
if not direct_runtime_dependencies:
    raise SystemExit("主安装包未声明可验证的直接运行依赖")
missing_direct_dependencies = direct_runtime_dependencies - runtime_dependencies
if missing_direct_dependencies:
    raise SystemExit(
        "主安装包直接依赖未被 runtime-dependencies.txt 覆盖: "
        + ", ".join(sorted(missing_direct_dependencies))
    )

for filename, fields in stanzas.items():
    package_path = root / filename
    for field in ("Package", "Version", "Architecture"):
        result = subprocess.run(
            ["dpkg-deb", "-f", str(package_path), field],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0 or result.stdout.strip() != fields[field]:
            raise SystemExit(f"Packages {field} does not match DEB control metadata: {filename}")

taiji_stanzas = [filename for filename, fields in stanzas.items() if fields["Package"] == "taiji-agent"]
if len(taiji_stanzas) != 1:
    raise SystemExit("Packages must contain exactly one taiji-agent package")
offline_taiji = root / taiji_stanzas[0]
if sha256_file(offline_taiji) != sha256_file(release_deb):
    raise SystemExit("offline taiji-agent DEB does not match release DEB")
PY

  local repo_deb
  while IFS= read -r repo_deb; do
    dpkg-deb --info "$repo_deb" >/dev/null 2>&1 \
      || fail "离线仓库包含无效 DEB：$(basename "$repo_deb")"
  done < <(find "$OFFLINE_REPO" -maxdepth 1 -type f -name '*.deb' | sort)
  ok "离线仓库 gzip、清单、索引和每个 DEB 均有效"
}

verify_target_acceptance_toolchain() {
  local script="$SCRIPT_DIR/04_目标终端_桌面App验收并导出证据.sh"
  local driver="$ACCEPTANCE_TOOLS/run-installed-electron-acceptance.js"
  local assembler="$ACCEPTANCE_TOOLS/assemble-target-evidence.py"
  local validator="$ACCEPTANCE_TOOLS/validate-taiji-release-evidence.py"
  local public_key="$ACCEPTANCE_TOOLS/signing-public.pem"
  local source_script="$REPO_ROOT/taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh"
  local source_driver="$REPO_ROOT/tools/taiji-desktop-acceptance/run-installed-electron-acceptance.js"
  local source_assembler="$REPO_ROOT/tools/taiji-desktop-acceptance/assemble-target-evidence.py"
  local source_validator="$REPO_ROOT/scripts/validate-taiji-release-evidence.py"
  local source_public_key="$REPO_ROOT/tools/taiji-release-evidence/signing-public.pem"
  local public_fingerprint expected_fingerprint file source index
  local staged_files=("$script" "$driver" "$assembler" "$validator" "$public_key")
  local source_files=("$source_script" "$source_driver" "$source_assembler" "$source_validator" "$source_public_key")
  expected_fingerprint="839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"

  [ -f "$script" ] && [ ! -L "$script" ] || fail "缺少安全的目标终端桌面 App 验收脚本"
  have cmp || fail "缺少 cmp，无法逐字节核对目标终端验收工具"
  for index in "${!staged_files[@]}"; do
    file="${staged_files[$index]}"
    source="${source_files[$index]}"
    [ -f "$file" ] && [ ! -L "$file" ] || fail "目标终端验收工具缺失或不安全：$file"
    [ -f "$source" ] && [ ! -L "$source" ] || fail "目标终端验收工具源文件缺失或不安全：$source"
    cmp -s "$source" "$file" || fail "目标终端验收工具与当前源码不一致：$(basename "$file")"
  done
  have python3 || fail "缺少 python3，无法检查目标证据工具"
  python3 - "$assembler" "$validator" <<'PY' || fail "目标证据 Python 工具语法检查失败"
import sys
from pathlib import Path

for raw in sys.argv[1:]:
    path = Path(raw)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
PY
  grep -Fq '/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron' "$driver" \
    || fail "桌面 App 验收驱动未锁定安装态 Electron"
  grep -Fq 'taiji.desktop.acceptance-driver.v1' "$driver" \
    || fail "桌面 App 验收驱动缺少固定结果 schema"
  have openssl || fail "缺少 openssl，无法核对目标验收验签公钥"
  public_fingerprint="$(openssl pkey -pubin -in "$public_key" -outform DER 2>/dev/null | openssl dgst -sha256 -r | awk '{print $1}')"
  [ "$public_fingerprint" = "$expected_fingerprint" ] || fail "目标验收验签公钥 fingerprint 不匹配"
  ok "目标终端真实 Electron 桌面 App 验收工具链完整"
}

check_delivery_artifacts() {
  [ "$REQUIRE_ARTIFACTS" = "1" ] || return 0
  [ ! -e "$SCRIPT_DIR/.构建工具" ] || fail "交付目录仍含制包缓存 .构建工具/，必须清理后再发布"
  [ ! -e "$SCRIPT_DIR/构建工作区" ] || fail "交付目录仍含临时构建工作区，必须清理后再发布"
  [ -d "$OUTPUT_DIR" ] || fail "缺少生成的安装包/"
  [ -f "$BUILD_MARKER" ] || fail "缺少生成的安装包/.build-success"
  [ -f "$MANIFEST_FILE" ] || fail "缺少生成的安装包/taiji-package-manifest.json"
  [ -f "$BUILD_REPORT" ] || fail "缺少生成的安装包/构建报告.txt"
  [ -d "$OFFLINE_REPO" ] || fail "缺少离线依赖/"
  [ -f "$OFFLINE_REPO/Packages" ] || fail "缺少离线依赖/Packages"
  [ -f "$OFFLINE_REPO/Packages.gz" ] || fail "缺少离线依赖/Packages.gz"
  verify_target_acceptance_toolchain

  local deb_count deb
  deb_count="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' | wc -l | tr -d ' ')"
  [ "$deb_count" = "1" ] || fail "生成的安装包/ 必须且只能有一个 amd64 DEB，当前数量：$deb_count"
  deb="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' | head -1)"
  verify_deb_checksum_sidecar "$deb"
  verify_package_output_allowlist "$deb"
  verify_assembled_deb_payload "$deb"
  verify_offline_repository_integrity "$deb"
  ok "交付产物完整性检查通过"
}

main() {
  info "执行太极 Agent 发布预检"
  check_single_source_archive
  check_source_checksum
  check_git_clean_and_commit_match
  check_source_archive_matches_git_head
  check_no_macos_metadata_or_stale_zip
  check_delivery_artifacts
  ok "发布预检通过"
}

main "$@"
