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
  [ -d "$REPO_ROOT/.git" ] || return 0
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

verify_assembled_deb_payload() {
  local deb="$1" payload_root status
  have dpkg-deb || fail "缺少 dpkg-deb，无法真实解包验证 payload"
  have python3 || fail "缺少 python3，无法执行 payload contract verifier"
  [ -f "$PAYLOAD_VERIFIER" ] || fail "缺少 payload verifier：$PAYLOAD_VERIFIER"
  payload_root="$(mktemp -d /tmp/taiji-payload-verify.XXXXXX)"
  if ! dpkg-deb -x "$deb" "$payload_root"; then
    rm -rf "$payload_root"
    fail "DEB 真实解包失败：$(basename "$deb")"
  fi
  set +e
  python3 "$PAYLOAD_VERIFIER" --root "$payload_root"
  status=$?
  set -e
  rm -rf "$payload_root"
  [ "$status" -eq 0 ] || fail "DEB payload contract 验证失败：$(basename "$deb")"
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

check_delivery_artifacts() {
  [ "$REQUIRE_ARTIFACTS" = "1" ] || return 0
  [ -d "$OUTPUT_DIR" ] || fail "缺少生成的安装包/"
  [ -f "$BUILD_MARKER" ] || fail "缺少生成的安装包/.build-success"
  [ -f "$MANIFEST_FILE" ] || fail "缺少生成的安装包/taiji-package-manifest.json"
  [ -f "$BUILD_REPORT" ] || fail "缺少生成的安装包/构建报告.txt"
  [ -d "$OFFLINE_REPO" ] || fail "缺少离线依赖/"
  [ -f "$OFFLINE_REPO/Packages" ] || fail "缺少离线依赖/Packages"
  [ -f "$OFFLINE_REPO/Packages.gz" ] || fail "缺少离线依赖/Packages.gz"

  local deb_count deb
  deb_count="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' | wc -l | tr -d ' ')"
  [ "$deb_count" = "1" ] || fail "生成的安装包/ 必须且只能有一个 amd64 DEB，当前数量：$deb_count"
  deb="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' | head -1)"
  verify_deb_checksum_sidecar "$deb"
  verify_assembled_deb_payload "$deb"
  ok "交付产物完整性检查通过"
}

main() {
  info "执行太极 Agent 发布预检"
  check_single_source_archive
  check_source_checksum
  check_git_clean_and_commit_match
  check_no_macos_metadata_or_stale_zip
  check_delivery_artifacts
  ok "发布预检通过"
}

main "$@"
