#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_GATE="$REPO_ROOT/scripts/check-clean-worktree.sh"
TRUSTED_GIT="$REPO_ROOT/scripts/taiji-trusted-git"
SOURCE_INTEGRITY_HELPER="$REPO_ROOT/packaging/linux/source-archive-integrity.py"
SOURCE_INTEGRITY_HELPER_SHA256="dc96ec71409a092eae6c689c5a643bd840b5cad810544b92e6931aa85bd9c2de"
BUILDER_INPUT_HELPER="$REPO_ROOT/packaging/linux/builder-input-package.py"
BUILDER_INPUT_HELPER_SHA256="fa7e01df64abe20becec4a95190864e10320fe02aa501ba44a6eba54aac0ab5f"
FROZEN_SOURCE_COMMIT=""
FROZEN_RUNTIME_DIR=""
FROZEN_DELIVERY_DIR=""
FROZEN_SOURCE_GATE=""
FROZEN_TRUSTED_GIT=""
FROZEN_SOURCE_INTEGRITY_HELPER=""
FROZEN_BUILDER_INPUT_HELPER=""
BUILDER_INPUT_STAGE=""
CHECKSUM_FILE=""

FROZEN_TRACKED_PATHS=(
  "scripts/check-clean-worktree.sh"
  "scripts/taiji-trusted-git"
  "packaging/linux/source-archive-integrity.py"
  "packaging/linux/builder-input-package.py"
  "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
  "taijiagent 打包交付/01_制包机_发布预检.sh"
  "taijiagent 打包交付/02_目标终端_安装并验证.sh"
  "taijiagent 打包交付/03_目标终端_导出诊断报告.sh"
  "taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh"
  "taijiagent 打包交付/99_本机_准备制包输入包.sh"
  "taijiagent 打包交付/操作说明.md"
  "taijiagent 打包交付/版本信息.txt"
)

FROZEN_DELIVERY_MEMBERS=(
  "00_制包机_生成离线交付包.sh"
  "01_制包机_发布预检.sh"
  "02_目标终端_安装并验证.sh"
  "03_目标终端_导出诊断报告.sh"
  "04_目标终端_桌面App验收并导出证据.sh"
  "99_本机_准备制包输入包.sh"
  "操作说明.md"
  "版本信息.txt"
)

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

raw_git() {
  env -i PATH=/usr/bin:/bin LC_ALL=C LANG=C GIT_NO_REPLACE_OBJECTS=1 \
    /usr/bin/git "$@"
}

cleanup_private_staging() {
  local status=$? cleanup_failed=0
  trap - EXIT INT TERM HUP
  if [ -n "${FROZEN_RUNTIME_DIR:-}" ]; then
    case "$FROZEN_RUNTIME_DIR" in
      "${TMPDIR:-/tmp}"/taiji-frozen-builder-input.*)
        if [ -d "$FROZEN_RUNTIME_DIR" ] && [ ! -L "$FROZEN_RUNTIME_DIR" ]; then
          rm -rf -- "$FROZEN_RUNTIME_DIR" >/dev/null 2>&1 || cleanup_failed=1
          [ ! -e "$FROZEN_RUNTIME_DIR" ] && [ ! -L "$FROZEN_RUNTIME_DIR" ] \
            || cleanup_failed=1
        fi
        ;;
    esac
  fi
  if [ "$cleanup_failed" = 1 ]; then
    printf '[FAIL] 冻结输入私有暂存目录清理不完整：%s\n' "$FROZEN_RUNTIME_DIR" >&2
    [ "$status" -ne 0 ] || status=1
  fi
  exit "$status"
}

record_triplet_member() {
  local role="$1" path="$2" digest size
  digest="$(sha256_file "$path")"
  size="$(wc -c < "$path" | tr -d '[:space:]')"
  printf '[OK] 制包输入三件套 role=%s basename=%s bytes=%s sha256=%s\n' \
    "$role" "$(basename "$path")" "$size" "$digest"
}

preflight_repo() {
  require_cmd gzip
  require_cmd python3
  require_cmd cmp
  [ -x /usr/bin/git ] || fail "缺少受信任系统 Git：/usr/bin/git"
  [ -x "$SOURCE_GATE" ] && [ ! -L "$SOURCE_GATE" ] || fail "缺少正式源码门禁：$SOURCE_GATE"
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

capture_frozen_source_identity() {
  FROZEN_SOURCE_COMMIT="$(raw_git -C "$REPO_ROOT" rev-parse --verify 'HEAD^{commit}')" \
    || fail "无法捕获冻结 source commit"
  printf '%s\n' "$FROZEN_SOURCE_COMMIT" | grep -Eq '^[0-9a-f]{40}$' \
    || fail "冻结 source commit 必须是完整小写 SHA"
  [ "$(raw_git -C "$REPO_ROOT" symbolic-ref --quiet --short HEAD)" = main ] \
    || fail "冻结源码必须来自 main"
  [ "$(raw_git -C "$REPO_ROOT" rev-parse --verify refs/heads/main)" = "$FROZEN_SOURCE_COMMIT" ] \
    || fail "main 与冻结 source commit 不一致"
  ok "已冻结唯一 source commit：$FROZEN_SOURCE_COMMIT"
}

verify_worktree_file_matches_f() {
  local relative="$1" path entry git_mode
  path="$REPO_ROOT/$relative"
  entry="$(raw_git -c core.quotePath=false -C "$REPO_ROOT" ls-tree "$FROZEN_SOURCE_COMMIT" -- "$relative")" \
    || return 1
  [ -n "$entry" ] || return 1
  git_mode="${entry%% *}"
  case "$git_mode" in 100644|100755) ;; *) return 1 ;; esac
  python3 - "$path" "$git_mode" <<'PY'
import os
import stat
import sys

path, git_mode = sys.argv[1:]
metadata = os.lstat(path)
expected = 0o755 if git_mode == "100755" else 0o644
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != os.getuid()
    or metadata.st_nlink != 1
    or stat.S_IMODE(metadata.st_mode) != expected
):
    raise SystemExit(1)
PY
  raw_git -C "$REPO_ROOT" show "$FROZEN_SOURCE_COMMIT:$relative" | cmp -s - "$path"
}

verify_frozen_source_identity() {
  local observed relative
  observed="$("$FROZEN_TRUSTED_GIT" -C "$REPO_ROOT" rev-parse --verify 'HEAD^{commit}' 2>/dev/null)" \
    || { printf '[FAIL] 无法复核 HEAD\n' >&2; return 1; }
  [ "$observed" = "$FROZEN_SOURCE_COMMIT" ] \
    || { printf '[FAIL] HEAD 已偏离冻结 source commit\n' >&2; return 1; }
  observed="$("$FROZEN_TRUSTED_GIT" -C "$REPO_ROOT" rev-parse --verify refs/heads/main 2>/dev/null)" \
    || { printf '[FAIL] 无法复核 main\n' >&2; return 1; }
  [ "$observed" = "$FROZEN_SOURCE_COMMIT" ] \
    || { printf '[FAIL] main 已偏离冻结 source commit\n' >&2; return 1; }
  [ "$("$FROZEN_TRUSTED_GIT" -C "$REPO_ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null)" = main ] \
    || { printf '[FAIL] 当前分支已偏离 main\n' >&2; return 1; }
  "$FROZEN_SOURCE_GATE" --mode formal --repo-root "$REPO_ROOT" --source-root "$REPO_ROOT" \
    || { printf '[FAIL] 冻结后的正式源码门禁复核失败\n' >&2; return 1; }
  for relative in "${FROZEN_TRACKED_PATHS[@]}"; do
    verify_worktree_file_matches_f "$relative" \
      || { printf '[FAIL] 工作树文件与冻结 commit 的 blob/mode 不一致：%s\n' "$relative" >&2; return 1; }
  done
}

stage_blob_from_f() {
  local relative="$1" destination="$2" entry git_mode
  entry="$(raw_git -c core.quotePath=false -C "$REPO_ROOT" ls-tree "$FROZEN_SOURCE_COMMIT" -- "$relative")" \
    || fail "无法读取冻结成员：$relative"
  [ -n "$entry" ] || fail "冻结 commit 缺少成员：$relative"
  git_mode="${entry%% *}"
  case "$git_mode" in
    100644) git_mode=0644 ;;
    100755) git_mode=0755 ;;
    *) fail "冻结成员类型或模式不安全：$relative" ;;
  esac
  raw_git -C "$REPO_ROOT" show "$FROZEN_SOURCE_COMMIT:$relative" > "$destination" \
    || fail "无法暂存冻结成员：$relative"
  chmod "$git_mode" "$destination" || fail "无法固定冻结成员权限：$relative"
}

stage_frozen_helpers() {
  local member
  FROZEN_RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/taiji-frozen-builder-input.XXXXXX")" \
    || fail "无法创建冻结输入私有暂存目录"
  chmod 0700 "$FROZEN_RUNTIME_DIR"
  FROZEN_DELIVERY_DIR="$FROZEN_RUNTIME_DIR/taijiagent 打包交付"
  mkdir -m 0700 "$FROZEN_DELIVERY_DIR"
  FROZEN_SOURCE_GATE="$FROZEN_RUNTIME_DIR/check-clean-worktree.sh"
  FROZEN_TRUSTED_GIT="$FROZEN_RUNTIME_DIR/taiji-trusted-git"
  FROZEN_SOURCE_INTEGRITY_HELPER="$FROZEN_DELIVERY_DIR/source-archive-integrity.py"
  FROZEN_BUILDER_INPUT_HELPER="$FROZEN_DELIVERY_DIR/builder-input-package.py"
  stage_blob_from_f "scripts/check-clean-worktree.sh" "$FROZEN_SOURCE_GATE"
  stage_blob_from_f "scripts/taiji-trusted-git" "$FROZEN_TRUSTED_GIT"
  stage_blob_from_f "packaging/linux/source-archive-integrity.py" "$FROZEN_SOURCE_INTEGRITY_HELPER"
  stage_blob_from_f "packaging/linux/builder-input-package.py" "$FROZEN_BUILDER_INPUT_HELPER"
  for member in "${FROZEN_DELIVERY_MEMBERS[@]}"; do
    stage_blob_from_f "taijiagent 打包交付/$member" "$FROZEN_DELIVERY_DIR/$member"
  done
  CHECKSUM_FILE="$FROZEN_DELIVERY_DIR/SHA256SUMS.txt"
  BUILDER_INPUT_STAGE="$FROZEN_RUNTIME_DIR/builder-triplet"
  mkdir -m 0700 "$BUILDER_INPUT_STAGE"
}

write_source_archive() {
  local archive archive_path inventory inventory_digest digest
  archive="taiji-agentv1.0-kylin-build-src-$FROZEN_SOURCE_COMMIT.tar.gz"
  archive_path="$FROZEN_DELIVERY_DIR/$archive"
  inventory="${archive%.tar.gz}.inventory.json"
  info "从冻结 commit 生成源码包：$archive"
  "$FROZEN_TRUSTED_GIT" -C "$REPO_ROOT" -c tar.umask=0022 archive --format=tar --prefix=taiji-agentv1.0/ "$FROZEN_SOURCE_COMMIT" \
    | gzip -n > "$archive_path"
  digest="$(sha256_file "$archive_path")"
  python3 "$FROZEN_SOURCE_INTEGRITY_HELPER" create \
    --archive "$archive_path" \
    --inventory "$FROZEN_DELIVERY_DIR/$inventory" \
    --source-commit "$FROZEN_SOURCE_COMMIT" \
    || fail "无法从冻结源码归档生成不可变成员清单"
  inventory_digest="$(sha256_file "$FROZEN_DELIVERY_DIR/$inventory")"
  {
    printf '%s  %s\n' "$digest" "$archive"
    printf '%s  %s\n' "$inventory_digest" "$inventory"
  } > "$CHECKSUM_FILE"
  chmod 0644 "$CHECKSUM_FILE"
  ok "源码包 SHA256：$digest"
  ok "源码成员清单 SHA256：$inventory_digest"
}

run_frozen_release_preflight() {
  TAIJI_RELEASE_SOURCE_ROOT="$REPO_ROOT" \
    TAIJI_REPO_ROOT="$REPO_ROOT" \
    TAIJI_SOURCE_GATE="$FROZEN_SOURCE_GATE" \
    TAIJI_TRUSTED_GIT="$FROZEN_TRUSTED_GIT" \
    TAIJI_SOURCE_INTEGRITY_HELPER="$FROZEN_SOURCE_INTEGRITY_HELPER" \
    TAIJI_FROZEN_SOURCE_COMMIT="$FROZEN_SOURCE_COMMIT" \
    TAIJI_RELEASE_REQUIRE_ARTIFACTS=0 \
    bash "$FROZEN_DELIVERY_DIR/01_制包机_发布预检.sh"
}

withdraw_published_triplet() {
  local output="$1" manifest="$2" checksum="$3"
  python3 "$FROZEN_BUILDER_INPUT_HELPER" withdraw \
    --archive "$output" \
    --manifest "$manifest" \
    --checksum "$checksum"
}

write_builder_input_package() {
  local commit output manifest checksum staged_output staged_manifest staged_checksum
  commit="$FROZEN_SOURCE_COMMIT"
  output="$REPO_ROOT/taijiagent-制包机输入-$commit.tar.gz"
  manifest="$REPO_ROOT/taijiagent-制包机输入-$commit.manifest.json"
  checksum="$output.sha256"
  staged_output="$BUILDER_INPUT_STAGE/$(basename "$output")"
  staged_manifest="$BUILDER_INPUT_STAGE/$(basename "$manifest")"
  staged_checksum="$BUILDER_INPUT_STAGE/$(basename "$checksum")"
  info "在私有暂存目录生成制包机输入包：$(basename "$output")"
  python3 "$FROZEN_BUILDER_INPUT_HELPER" create \
    --source-dir "$FROZEN_DELIVERY_DIR" \
    --source-integrity-helper "$FROZEN_SOURCE_INTEGRITY_HELPER" \
    --output "$staged_output" \
    --manifest "$staged_manifest" \
    --checksum "$staged_checksum" \
    --source-commit "$commit" \
    || fail "制包机输入包固定清单生成失败"
  python3 "$FROZEN_BUILDER_INPUT_HELPER" verify \
    --archive "$staged_output" \
    --manifest "$staged_manifest" \
    --checksum "$staged_checksum" \
    || fail "制包机输入包暂存后三件套回读验证失败"
  verify_frozen_source_identity || fail "发布前冻结 source commit 复核失败"
  python3 "$FROZEN_BUILDER_INPUT_HELPER" publish \
    --archive "$staged_output" \
    --manifest "$staged_manifest" \
    --checksum "$staged_checksum" \
    --output "$output" \
    --output-manifest "$manifest" \
    --output-checksum "$checksum" \
    || fail "制包机输入三件套 no-overwrite 发布失败"
  if ! verify_frozen_source_identity; then
    withdraw_published_triplet "$output" "$manifest" "$checksum" \
      || fail "source commit 漂移且三件套安全回滚不完整，输出目录已 poisoned"
    fail "三件套发布后 source commit 漂移；已安全撤回本轮三件套"
  fi
  python3 "$FROZEN_BUILDER_INPUT_HELPER" verify \
    --archive "$output" \
    --manifest "$manifest" \
    --checksum "$checksum" \
    || fail "制包机输入包发布后三件套回读验证失败"
  if ! verify_frozen_source_identity; then
    withdraw_published_triplet "$output" "$manifest" "$checksum" \
      || fail "最终 source commit 复核失败且三件套安全回滚不完整，输出目录已 poisoned"
    fail "最终 source commit 复核失败；已安全撤回本轮三件套"
  fi
  ok "制包机输入包已生成：$output"
  record_triplet_member "archive" "$output"
  record_triplet_member "manifest" "$manifest"
  record_triplet_member "sidecar" "$checksum"
}

main() {
  preflight_repo
  capture_frozen_source_identity
  verify_frozen_source_identity_bootstrap=1
  for verify_frozen_source_identity_bootstrap_path in "${FROZEN_TRACKED_PATHS[@]}"; do
    verify_worktree_file_matches_f "$verify_frozen_source_identity_bootstrap_path" \
      || fail "工作树文件与冻结 commit 的 blob/mode 不一致：$verify_frozen_source_identity_bootstrap_path"
  done
  unset verify_frozen_source_identity_bootstrap verify_frozen_source_identity_bootstrap_path
  stage_frozen_helpers
  verify_frozen_source_identity || fail "冻结 source commit 初始复核失败"
  write_source_archive
  run_frozen_release_preflight
  verify_frozen_source_identity || fail "发布预检后冻结 source commit 复核失败"
  write_builder_input_package
  printf '\n[OK] 本机发布输入准备完成。请把同一 commit 的 tar.gz、manifest.json 和 tar.gz.sha256 一起复制到 Linux amd64 制包机。\n'
  printf '传输后先在三件套所在目录执行：sha256sum -c taijiagent-制包机输入-*.tar.gz.sha256\n'
  printf '校验通过再解压输入包并执行：\n'
  printf 'bash ./00_制包机_生成离线交付包.sh\n'
}

trap cleanup_private_staging EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
main "$@"
