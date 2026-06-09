#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ARCHIVE="${TAIJI_SOURCE_ARCHIVE:-}"
CHECKSUM_FILE="$SCRIPT_DIR/SHA256SUMS.txt"
BUILD_ROOT="$SCRIPT_DIR/构建工作区"
SRC_DIR="$BUILD_ROOT/taiji-agentv1.0"
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
LOG_DIR="$SCRIPT_DIR/构建日志"
VERSION="${TAIJI_AGENT_VERSION:-0.1.0}"
TOOL_ROOT="$SCRIPT_DIR/.构建工具"
NODE_ROOT="$TOOL_ROOT/node"
NODE_MAJOR="${TAIJI_NODE_MAJOR:-22}"
BUILD_MARKER="$OUTPUT_DIR/.build-success"

mkdir -p "$LOG_DIR" "$OUTPUT_DIR"
LOG_FILE="$LOG_DIR/01_build_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
trap 'code=$?; printf "\n[FAIL] 构建中断，尚未生成安装包。请先查看本日志定位失败点：%s\n" "$LOG_FILE" >&2; exit "$code"' ERR

ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

require_cmd() {
  have "$1" || fail "缺少命令：$1"
}

cleanup_output_artifacts() {
  info "清理旧安装包输出"
  rm -f "$OUTPUT_DIR"/taiji-agent_*_amd64.deb \
    "$OUTPUT_DIR"/taiji-agent_*_amd64.deb.sha256 \
    "$BUILD_MARKER"
  ok "旧安装包输出已清理"
}

cleanup_delivery_metadata() {
  info "清理交付文件夹中的拷贝元数据"
  count=0
  while IFS= read -r -d '' path; do
    count=$((count + 1))
    rm -rf -- "$path"
  done < <(find "$SCRIPT_DIR" \( -name '.DS_Store' -o -name '._*' -o -name '.AppleDouble' -o -name 'PaxHeaders*' \) -print0)

  if [ "$count" -gt 0 ]; then
    ok "已清理 $count 个 macOS 元数据文件"
  else
    ok "未发现 macOS 元数据文件"
  fi
}

checksum_source_archive_name() {
  [ -f "$CHECKSUM_FILE" ] || return 1
  awk '$2 ~ /^taiji-agentv1\.0-kylin-build-src-.*\.tar\.gz$/ { print $2 }' "$CHECKSUM_FILE"
}

resolve_source_archive() {
  if [ -n "$SRC_ARCHIVE" ]; then
    [ -f "$SRC_ARCHIVE" ] || fail "未找到指定源码包：$SRC_ARCHIVE"
    return
  fi

  if [ -f "$CHECKSUM_FILE" ]; then
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

  count="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | wc -l | tr -d ' ')"
  [ "$count" = "1" ] || {
    find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' -print >&2
    fail "源码包数量应为 1 个，当前为：$count"
  }
  SRC_ARCHIVE="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | sort | tail -1)"
}

preflight() {
  info "检查目标终端环境"
  cleanup_delivery_metadata
  [ "$(uname -s)" = "Linux" ] || fail "只能在 Linux 目标终端构建，当前为：$(uname -s)"
  case "$(uname -m)" in
    x86_64|amd64) ok "CPU 架构符合：$(uname -m)" ;;
    *) fail "当前 CPU 架构不是 x86_64/amd64：$(uname -m)" ;;
  esac

  require_cmd apt-get
  require_cmd dpkg
  require_cmd systemctl

  arch="$(dpkg --print-architecture 2>/dev/null || true)"
  [ "$arch" = "amd64" ] || fail "dpkg 架构不是 amd64：${arch:-unknown}"
  ok "dpkg 架构符合：$arch"

  resolve_source_archive
  [ -f "$SRC_ARCHIVE" ] || fail "未找到源码包：$SRC_ARCHIVE"
  if [ -f "$CHECKSUM_FILE" ]; then
    (cd "$SCRIPT_DIR" && sha256sum -c SHA256SUMS.txt)
    ok "源码包校验通过"
  else
    warn "未找到 SHA256SUMS.txt，跳过传输校验"
  fi
}

install_build_dependencies() {
  info "安装构建依赖。这里可能需要输入 sudo 密码。"
  sudo apt-get update
  sudo apt-get install -y \
    curl ca-certificates build-essential python3-dev libffi-dev git rsync \
    dpkg-dev file desktop-file-utils lsof xz-utils tar gzip
}

ensure_uv() {
  export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
  if have uv; then
    ok "uv 已存在：$(command -v uv)"
    uv --version || true
    return
  fi
  info "安装 uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
  have uv || fail "uv 安装后仍不可用，请检查网络或构建日志"
  uv --version
}

node_major() {
  node -p "Number(process.versions.node.split('.')[0])" 2>/dev/null || printf '0\n'
}

npm_major() {
  npm -v 2>/dev/null | awk -F. '{print $1 + 0}' || printf '0\n'
}

have_modern_node() {
  have node || return 1
  have npm || return 1
  [ "$(node_major)" -ge 20 ] || return 1
  [ "$(npm_major)" -ge 9 ] || return 1
}

install_portable_node() {
  mkdir -p "$NODE_ROOT"
  if [ -x "$NODE_ROOT/current/bin/node" ] && [ -x "$NODE_ROOT/current/bin/npm" ]; then
    export PATH="$NODE_ROOT/current/bin:$PATH"
    have_modern_node && return 0
  fi

  mirror="${NODE_MIRROR:-https://npmmirror.com/mirrors/node}"
  release_dir="latest-v${NODE_MAJOR}.x"
  tmp_dir="$NODE_ROOT/download"
  rm -rf "$tmp_dir"
  mkdir -p "$tmp_dir"

  info "准备 Node.js ${NODE_MAJOR}.x Linux x64 构建工具"
  curl -fsSL "$mirror/$release_dir/SHASUMS256.txt" -o "$tmp_dir/SHASUMS256.txt"
  tarball="$(awk '/linux-x64.tar.xz$/ {print $2; exit}' "$tmp_dir/SHASUMS256.txt")"
  [ -n "$tarball" ] || fail "未在 Node.js 校验清单中找到 linux-x64 tarball"
  curl -fL "$mirror/$release_dir/$tarball" -o "$tmp_dir/$tarball"
  (cd "$tmp_dir" && grep "  $tarball$" SHASUMS256.txt | sha256sum -c -)
  tar -xJf "$tmp_dir/$tarball" -C "$NODE_ROOT"
  ln -sfn "$NODE_ROOT/${tarball%.tar.xz}" "$NODE_ROOT/current"
  export PATH="$NODE_ROOT/current/bin:$PATH"
}

ensure_node() {
  export PATH="$NODE_ROOT/current/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
  if have_modern_node; then
    ok "Node.js 可用：$(command -v node) ($(node --version), npm $(npm -v))"
    return
  fi

  if have node || have npm; then
    warn "系统 Node/npm 版本过旧，将使用交付目录内的隔离构建工具"
    node --version 2>/dev/null || true
    npm --version 2>/dev/null || true
  fi
  install_portable_node
  have_modern_node || fail "Node.js 构建工具不可用，请检查网络或日志"
  ok "Node.js 已准备：$(command -v node) ($(node --version), npm $(npm -v))"
}

unpack_source() {
  info "解压源码到构建工作区"
  rm -rf "$BUILD_ROOT"
  mkdir -p "$BUILD_ROOT"
  tar -xzf "$SRC_ARCHIVE" -C "$BUILD_ROOT"
  [ -d "$SRC_DIR" ] || fail "源码解压后未找到：$SRC_DIR"
  ok "源码已解压：$SRC_DIR"
}

build_runtime_and_deb() {
  export PATH="$NODE_ROOT/current/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
  export UV_INDEX_URL="${UV_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
  export NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-https://registry.npmmirror.com}"
  export ELECTRON_MIRROR="${ELECTRON_MIRROR:-https://npmmirror.com/mirrors/electron/}"

  info "刷新目标构建工作区 Python lock"
  cd "$SRC_DIR/hermes-local-lab/sources/hermes-agent"
  uv lock

  info "生成 Linux Python venv"
  cd "$SRC_DIR/hermes-local-lab"
  TAIJI_UV_LOCK_MODE=strict ./scripts/setup-local.sh

  info "获取 Linux Electron runtime"
  cd "$SRC_DIR/apps/taiji-desktop"
  npm --version
  npm ci

  info "构建 DEB 安装包"
  cd "$SRC_DIR"
  TAIJI_AGENT_VERSION="$VERSION" ./packaging/linux/deb/build-deb.sh
}

collect_artifacts() {
  info "收集安装包产物"
  src_pkg_dir="$SRC_DIR/packages/麒麟操作系统安装包"
  deb="$src_pkg_dir/taiji-agent_${VERSION}_amd64.deb"
  checksum="$deb.sha256"
  [ -f "$deb" ] || fail "未找到 DEB：$deb"
  [ -f "$checksum" ] || fail "未找到 DEB 校验文件：$checksum"

  cp -f "$deb" "$OUTPUT_DIR/"
  cp -f "$checksum" "$OUTPUT_DIR/"
  (cd "$OUTPUT_DIR" && sha256sum -c "taiji-agent_${VERSION}_amd64.deb.sha256")
  deb_name="taiji-agent_${VERSION}_amd64.deb"
  checksum_name="$deb_name.sha256"
  deb_sha="$(sha256sum "$OUTPUT_DIR/$deb_name" | awk '{print $1}')"
  src_name="$(basename "$SRC_ARCHIVE")"
  src_sha="$(cd "$SCRIPT_DIR" && sha256sum "$src_name" | awk '{print $1}')"
  {
    printf 'version=%s\n' "$VERSION"
    printf 'source_archive=%s\n' "$src_name"
    printf 'source_sha256=%s\n' "$src_sha"
    printf 'deb=%s\n' "$deb_name"
    printf 'deb_sha256=%s\n' "$deb_sha"
    printf 'checksum=%s\n' "$checksum_name"
    printf 'built_at=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')"
  } > "$BUILD_MARKER"
  ok "安装包已生成：$OUTPUT_DIR/taiji-agent_${VERSION}_amd64.deb"
}

main() {
  preflight
  cleanup_output_artifacts
  install_build_dependencies
  ensure_uv
  ensure_node
  unpack_source
  build_runtime_and_deb
  collect_artifacts
  printf '\n[OK] 构建完成。下一步执行：\n'
  printf 'bash ./02_目标终端_安装并验证.sh\n'
  printf '\n日志：%s\n' "$LOG_FILE"
}

main "$@"
