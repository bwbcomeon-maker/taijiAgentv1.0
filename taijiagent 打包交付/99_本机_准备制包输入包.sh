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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_GATE="$REPO_ROOT/scripts/check-clean-worktree.sh"
TRUSTED_GIT="$REPO_ROOT/scripts/taiji-trusted-git"
CHECKSUM_FILE="$SCRIPT_DIR/SHA256SUMS.txt"
SOURCE_INTEGRITY_HELPER="$REPO_ROOT/packaging/linux/source-archive-integrity.py"
SOURCE_INTEGRITY_HELPER_SHA256="eaebadbe2f86d76d09f19ed210ad407e5926a242c46f53fb89e26253db8d8d7a"
BUILDER_INPUT_HELPER="$REPO_ROOT/packaging/linux/builder-input-package.py"
BUILDER_INPUT_HELPER_SHA256="8c4b378bc762eb7dc10d4cb260cf5499c54f8a348f202d49fb9af754349af1dd"

ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }
require_cmd() { have "$1" || fail "缺少命令：$1"; }

sha256_file() {
  if have sha256sum; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

record_triplet_member() {
  local role="$1" path="$2" digest size
  digest="$(sha256_file "$path")"
  size="$(wc -c < "$path" | tr -d '[:space:]')"
  printf '[OK] 制包输入三件套 role=%s basename=%s bytes=%s sha256=%s\n' \
    "$role" "$(basename "$path")" "$size" "$digest"
}

preflight_repo() {
  require_cmd git
  require_cmd gzip
  [ -x /usr/bin/python3 ] || fail "缺少受信 Python 解释器：/usr/bin/python3"
  [ -x "$SOURCE_GATE" ] || fail "缺少正式源码门禁：$SOURCE_GATE"
  [ -x "$TRUSTED_GIT" ] && [ ! -L "$TRUSTED_GIT" ] || fail "缺少可信 Git 边界：$TRUSTED_GIT"
  [ -f "$SOURCE_INTEGRITY_HELPER" ] && [ ! -L "$SOURCE_INTEGRITY_HELPER" ] \
    || fail "缺少源码归档完整性工具：$SOURCE_INTEGRITY_HELPER"
  [ "$(sha256_file "$SOURCE_INTEGRITY_HELPER")" = "$SOURCE_INTEGRITY_HELPER_SHA256" ] \
    || fail "源码归档完整性工具不是固定审查版本"
  [ -f "$BUILDER_INPUT_HELPER" ] && [ ! -L "$BUILDER_INPUT_HELPER" ] \
    || fail "缺少制包输入固定清单工具：$BUILDER_INPUT_HELPER"
  [ "$(sha256_file "$BUILDER_INPUT_HELPER")" = "$BUILDER_INPUT_HELPER_SHA256" ] \
    || fail "制包输入固定清单工具不是固定审查版本"
  "$SOURCE_GATE" \
    --mode formal \
    --repo-root "$REPO_ROOT" \
    --source-root "$REPO_ROOT" \
    || fail "本机制包输入必须来自干净本地 main"
}

write_source_archive() {
  local commit archive archive_path inventory inventory_digest digest
  commit="$("$TRUSTED_GIT" -C "$REPO_ROOT" rev-parse HEAD)"
  archive="taiji-agentv1.0-kylin-build-src-$commit.tar.gz"
  archive_path="$SCRIPT_DIR/$archive"
  inventory="${archive%.tar.gz}.inventory.json"
  rm -f "$SCRIPT_DIR"/taiji-agentv1.0-kylin-build-src-*.tar.gz \
    "$SCRIPT_DIR"/taiji-agentv1.0-kylin-build-src-*.inventory.json "$CHECKSUM_FILE"
  info "生成源码包：$archive"
  "$TRUSTED_GIT" -C "$REPO_ROOT" archive --format=tar --prefix=taiji-agentv1.0/ HEAD | gzip -n > "$archive_path"
  digest="$(sha256_file "$archive_path")"
  /usr/bin/python3 -I -B "$SOURCE_INTEGRITY_HELPER" create \
    --archive "$archive_path" \
    --inventory "$SCRIPT_DIR/$inventory" \
    --source-commit "$commit" \
    || fail "无法从正式源码归档生成不可变成员清单"
  inventory_digest="$(sha256_file "$SCRIPT_DIR/$inventory")"
  {
    printf '%s  %s\n' "$digest" "$archive"
    printf '%s  %s\n' "$inventory_digest" "$inventory"
  } > "$CHECKSUM_FILE"
  ok "源码包 SHA256：$digest"
  ok "源码成员清单 SHA256：$inventory_digest"
}

write_builder_input_package() {
  local commit output manifest checksum
  commit="$("$TRUSTED_GIT" -C "$REPO_ROOT" rev-parse HEAD)"
  output="$REPO_ROOT/taijiagent-制包机输入-$commit.tar.gz"
  manifest="$REPO_ROOT/taijiagent-制包机输入-$commit.manifest.json"
  checksum="$output.sha256"
  rm -f "$REPO_ROOT"/taijiagent-制包机输入-*.tar.gz \
    "$REPO_ROOT"/taijiagent-制包机输入-*.tar.gz.sha256 \
    "$REPO_ROOT"/taijiagent-制包机输入-*.manifest.json
  info "生成制包机输入包：$(basename "$output")"
  /usr/bin/python3 -I -B "$BUILDER_INPUT_HELPER" create \
    --source-dir "$SCRIPT_DIR" \
    --source-integrity-helper "$SOURCE_INTEGRITY_HELPER" \
    --output "$output" \
    --manifest "$manifest" \
    --checksum "$checksum" \
    --source-commit "$commit" \
    || fail "制包机输入包固定清单生成失败"
  /usr/bin/python3 -I -B "$BUILDER_INPUT_HELPER" verify \
    --archive "$output" \
    --manifest "$manifest" \
    --checksum "$checksum" \
    || fail "制包机输入包生成后回读验证失败"
  ok "制包机输入包已生成：$output"
  record_triplet_member "archive" "$output"
  record_triplet_member "manifest" "$manifest"
  record_triplet_member "sidecar" "$checksum"
}

main() {
  [ "$#" -eq 0 ] || fail "99_本机_准备制包输入包.sh 不接受命令行参数"
  preflight_repo
  write_source_archive
  TAIJI_RELEASE_REQUIRE_ARTIFACTS=0 /bin/bash -p "$SCRIPT_DIR/01_制包机_发布预检.sh"
  write_builder_input_package
  printf '\n[OK] 本机发布输入准备完成。请把同一 commit 的 tar.gz、manifest.json 和 tar.gz.sha256 一起复制到 Linux amd64 制包机。\n'
  printf '传输后先在三件套所在目录执行：sha256sum -c taijiagent-制包机输入-*.tar.gz.sha256\n'
  printf '校验通过再解压输入包并执行：\n'
  printf '/bin/bash -p ./00_制包机_生成离线交付包.sh\n'
}

main "$@"
