#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECKSUM_FILE="$SCRIPT_DIR/SHA256SUMS.txt"

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

preflight_repo() {
  require_cmd git
  require_cmd gzip
  require_cmd python3
  git -C "$REPO_ROOT" diff --quiet || fail "源码仓库存在未提交改动，请先提交后再生成发布源码包"
  git -C "$REPO_ROOT" diff --cached --quiet || fail "源码仓库存在已暂存未提交改动，请先提交后再生成发布源码包"
  if [ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ]; then
    git -C "$REPO_ROOT" status --short --untracked-files=all >&2
    fail "源码仓库存在未跟踪文件，请先提交、删除或加入 .gitignore"
  fi
}

write_source_archive() {
  local short archive archive_path digest
  short="$(git -C "$REPO_ROOT" rev-parse --short=8 HEAD)"
  archive="taiji-agentv1.0-kylin-build-src-$short.tar.gz"
  archive_path="$SCRIPT_DIR/$archive"
  rm -f "$SCRIPT_DIR"/taiji-agentv1.0-kylin-build-src-*.tar.gz "$CHECKSUM_FILE"
  info "生成源码包：$archive"
  git -C "$REPO_ROOT" archive --format=tar --prefix=taiji-agentv1.0/ HEAD | gzip -n > "$archive_path"
  digest="$(sha256_file "$archive_path")"
  printf '%s  %s\n' "$digest" "$archive" > "$CHECKSUM_FILE"
  ok "源码包 SHA256：$digest"
}

write_builder_input_package() {
  local short output
  short="$(git -C "$REPO_ROOT" rev-parse --short=8 HEAD)"
  output="$REPO_ROOT/taijiagent-制包机输入-$short.tar.gz"
  rm -f "$REPO_ROOT"/taijiagent-制包机输入-*.tar.gz
  info "生成制包机输入包：$(basename "$output")"
  python3 - "$SCRIPT_DIR" "$output" <<'PY'
import os
import sys
import tarfile
from pathlib import Path

source = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
skip_dirs = {
    ".AppleDouble",
    "__MACOSX",
    ".构建工具",
    "构建工作区",
    "生成的安装包",
    "构建日志",
    "旧版备份",
    "离线依赖",
}
skip_names = {".DS_Store"}

def should_skip(path: Path) -> bool:
    name = path.name
    if name in skip_names or name in skip_dirs:
        return True
    if name.startswith("._") or name.startswith("PaxHeaders"):
        return True
    if path.suffix == ".zip":
        return True
    return False

with tarfile.open(output, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
    for root, dirs, files in os.walk(source):
        root_path = Path(root)
        dirs[:] = [name for name in dirs if not should_skip(root_path / name)]
        for name in sorted(files):
            path = root_path / name
            if should_skip(path):
                continue
            arcname = Path(source.name) / path.relative_to(source)
            archive.add(path, arcname=str(arcname), recursive=False)
PY
  ok "制包机输入包已生成：$output"
}

main() {
  preflight_repo
  write_source_archive
  bash "$SCRIPT_DIR/01_制包机_发布预检.sh"
  write_builder_input_package
  printf '\n[OK] 本机发布输入准备完成。优先把 taijiagent-制包机输入-*.tar.gz 复制到 Linux amd64 制包机并解压后执行：\n'
  printf 'bash ./00_制包机_生成离线交付包.sh\n'
}

main "$@"
