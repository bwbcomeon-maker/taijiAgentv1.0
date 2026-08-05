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
ACCEPTANCE_TOOLS="$SCRIPT_DIR/验收工具"
REQUIRE_ARTIFACTS="$(printenv TAIJI_RELEASE_REQUIRE_ARTIFACTS || printf 0)"
SKIP_GIT_CHECK="$(printenv TAIJI_RELEASE_SKIP_GIT_CHECK || printf 0)"
SOURCE_ARCHIVE=""
POLICY_ID=""
POLICY_SHA256=""
POLICY_MAINTAINER=""

ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
hex64() { [ "$(printf '%s' "$1" | wc -c | tr -d ' ')" = 64 ] && printf '%s' "$1" | grep -Eq '^[0-9a-fA-F]{64}$'; }

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
  local expected_archive; expected_archive="$(mktemp /tmp/taiji-source-head.XXXXXX.tar)"
  "$TRUSTED_GIT" -C "$REPO_ROOT" archive --format=tar --prefix=taiji-agentv1.0/ HEAD > "$expected_archive" || { rm -f "$expected_archive"; fail "无法重建当前 HEAD 源码包"; }
  gzip -dc "$SOURCE_ARCHIVE" | cmp -s "$expected_archive" - || { rm -f "$expected_archive"; fail "源码包归档内容与当前 Git HEAD 不一致"; }
  rm -f "$expected_archive"; ok "源码包归档与当前 Git HEAD 一致"
}
check_no_macos_metadata_or_stale_zip() {
  local metadata zips stale_debs
  metadata="$(find "$SCRIPT_DIR" \( -name '__MACOSX' -o -name '.DS_Store' -o -name '._*' -o -name '.AppleDouble' -o -name 'PaxHeaders*' \) -print)"
  [ -z "$metadata" ] || { info "发现 macOS 拷贝元数据，将自动清理"; find "$SCRIPT_DIR" \( -name '__MACOSX' -o -name '.DS_Store' -o -name '._*' -o -name '.AppleDouble' -o -name 'PaxHeaders*' \) -exec rm -rf -- {} +; }
  zips="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name '*.zip' -print)"; [ -z "$zips" ] || fail "交付目录含旧 zip：$zips"
  if [ "$REQUIRE_ARTIFACTS" != 1 ]; then
    stale_debs="$(find "$OUTPUT_DIR" -maxdepth 1 -type f \( -name '*.deb' -o -name '*.deb.sha256' \) -print 2>/dev/null || true)"
    [ -z "$stale_debs" ] || fail "发布预检发现旧安装包，请先清理 生成的安装包/"
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
  expected="$(awk 'NR==1 {print $1; exit}' "$deb.sha256")"; target="$(awk 'NR==1 {$1=\"\"; sub(/^[ \t]+\*?/,\"\"); print; exit}' "$deb.sha256")"; hex64 "$expected" || fail "DEB SHA256 sidecar 格式非法：$name.sha256"; [ "$target" = "$name" ] || fail "DEB SHA256 sidecar 指向错误文件：$target"; actual="$(sha256sum "$deb" | awk '{print $1}')"; [ "$actual" = "$expected" ] || fail "DEB SHA256 不匹配：$name"
}
verify_marker_and_manifest() {
  local deb="$1"
  python3 - "$BUILD_MARKER" "$MANIFEST_FILE" "$deb" "$SOURCE_ARCHIVE" "$POLICY_ID" "$POLICY_SHA256" "$POLICY_MAINTAINER" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path
marker_path = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
deb_path = Path(sys.argv[3])
source_path = Path(sys.argv[4])
policy_id = sys.argv[5]
policy_sha = sys.argv[6]
maintainer = sys.argv[7]
required = {"version","source_archive","source_sha256","source_commit","deb","deb_sha256","checksum","built_at_utc","manifest","compatibility_policy_id","compatibility_policy_sha256","elf_abi_audit_sha256","maintainer"}
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
PY
}
verify_deb_payload() {
  local deb="$1" payload_root abi embedded_policy abi_sha
  payload_root="$(mktemp -d /tmp/taiji-release-payload.XXXXXX)"
  dpkg-deb -x "$deb" "$payload_root" || { rm -rf "$payload_root"; fail "DEB 真实解包失败：$(basename "$deb")"; }
  embedded_policy="$payload_root/opt/taiji-agent/resources/linux-compatibility-policy.json"
  abi="$payload_root/opt/taiji-agent/resources/elf-abi-audit.json"
  [ -f "$embedded_policy" ] && [ ! -L "$embedded_policy" ] || { rm -rf "$payload_root"; fail "DEB 缺少 embedded compatibility policy"; }
  [ -f "$abi" ] && [ ! -L "$abi" ] || { rm -rf "$payload_root"; fail "DEB 缺少 embedded ELF ABI audit"; }
  cmp -s "$POLICY_FILE" "$embedded_policy" || { rm -rf "$payload_root"; fail "DEB embedded policy 与源码 policy 不一致"; }
  abi_sha="$(sha256sum "$abi" | awk '{print $1}')"
  [ "$abi_sha" = "$(awk -F= '$1==\"elf_abi_audit_sha256\" {print $2}' "$BUILD_MARKER")" ] || { rm -rf "$payload_root"; fail "DEB embedded ABI audit 与 marker 不一致"; }
  [ -f "$PAYLOAD_VERIFIER" ] && [ ! -L "$PAYLOAD_VERIFIER" ] || { rm -rf "$payload_root"; fail "缺少可信 DEB payload verifier：$PAYLOAD_VERIFIER"; }
  python3 "$PAYLOAD_VERIFIER" --root "$payload_root" >/dev/null || { rm -rf "$payload_root"; fail "DEB payload contract 验证失败"; }
  rm -rf "$payload_root"
}
verify_package_output_allowlist() {
  local deb="$1" name="$(basename "$deb")"
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
  local script source_script
  [ "$REQUIRE_ARTIFACTS" = 1 ] || return 0
  local -a files=(
    "04_目标终端_桌面App验收并导出证据.sh"
    "run-installed-electron-acceptance.js"
    "assemble-target-evidence.py"
    "observe-single-deb-install.py"
    "validate-taiji-release-evidence.py"
    "signing-public.pem"
  )
  for script in "${files[@]}"; do
    source_script="$REPO_ROOT/taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh"
    case "$script" in
      run-installed-electron-acceptance.js|assemble-target-evidence.py|observe-single-deb-install.py)
        source_script="$REPO_ROOT/tools/taiji-desktop-acceptance/$script"
        ;;
      validate-taiji-release-evidence.py)
        source_script="$REPO_ROOT/scripts/$script"
        ;;
      signing-public.pem)
        source_script="$REPO_ROOT/tools/taiji-release-evidence/$script"
        ;;
    esac
    [ -f "$SCRIPT_DIR/验收工具/$script" ] && [ -f "$source_script" ] || fail "缺少目标终端验收工具：$script"
    cmp -s "$SCRIPT_DIR/验收工具/$script" "$source_script" || fail "目标终端验收工具与源码不一致：$script"
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
  check_git_clean_and_commit_match
  check_source_archive_matches_git_head
  check_no_macos_metadata_or_stale_zip
  check_delivery_artifacts
  ok "发布预检通过"
}
main "$@"
