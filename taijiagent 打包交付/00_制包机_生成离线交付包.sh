#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ARCHIVE="${TAIJI_SOURCE_ARCHIVE:-}"
CHECKSUM_FILE="$SCRIPT_DIR/SHA256SUMS.txt"
BUILD_ROOT="$SCRIPT_DIR/构建工作区"
SRC_DIR="$BUILD_ROOT/taiji-agentv1.0"
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
OFFLINE_REPO="$SCRIPT_DIR/离线依赖"
LOG_DIR="$SCRIPT_DIR/构建日志"
VERSION="${TAIJI_AGENT_VERSION:-0.1.0}"
TOOL_ROOT="$SCRIPT_DIR/.构建工具"
NODE_ROOT="$TOOL_ROOT/node"
NODE_MAJOR="${TAIJI_NODE_MAJOR:-22}"
BUILD_MARKER="$OUTPUT_DIR/.build-success"
BUILD_REPORT="$OUTPUT_DIR/构建报告.txt"
MANIFEST_FILE="$OUTPUT_DIR/taiji-package-manifest.json"

mkdir -p "$LOG_DIR" "$OUTPUT_DIR" "$OFFLINE_REPO"
LOG_FILE="$LOG_DIR/00_offline_build_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
trap 'code=$?; printf "\n[FAIL] 离线交付包生成中断，请查看日志：%s\n" "$LOG_FILE" >&2; exit "$code"' ERR

ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
require_cmd() { have "$1" || fail "缺少命令：$1"; }

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

verify_source_archive_checksum() {
  local archive_name expected actual
  archive_name="$(basename "$SRC_ARCHIVE")"
  expected="$(checksum_source_archive_hash "$archive_name")"
  [ -n "$expected" ] || fail "校验文件中未找到源码包条目：$archive_name"
  actual="$(cd "$SCRIPT_DIR" && sha256sum "$archive_name" | awk '{print $1}')"
  if [ "$actual" != "$expected" ]; then
    fail "源码包 SHA256 不匹配：$archive_name"
  fi
  printf '%s  %s\n' "$actual" "$archive_name" > "$CHECKSUM_FILE"
}

create_source_archive_from_git() {
  local repo_root short archive_name
  repo_root="$(cd "$SCRIPT_DIR/.." && pwd)"
  [ -d "$repo_root/.git" ] || fail "未找到源码包，也无法从当前目录生成源码包。请先放入 taiji-agentv1.0-kylin-build-src-<hash>.tar.gz"
  git -C "$repo_root" diff --quiet || fail "源码仓库存在未提交改动，请先提交后再生成发布源码包"
  git -C "$repo_root" diff --cached --quiet || fail "源码仓库存在已暂存未提交改动，请先提交后再生成发布源码包"
  short="$(git -C "$repo_root" rev-parse --short=8 HEAD)"
  archive_name="taiji-agentv1.0-kylin-build-src-$short.tar.gz"
  info "使用 git archive 生成源码包：$archive_name"
  git -C "$repo_root" archive --format=tar --prefix=taiji-agentv1.0/ HEAD | gzip -n > "$SCRIPT_DIR/$archive_name"
  (cd "$SCRIPT_DIR" && sha256sum "$archive_name" > SHA256SUMS.txt)
  SRC_ARCHIVE="$SCRIPT_DIR/$archive_name"
  ok "源码包已生成并写入 SHA256SUMS.txt"
}

resolve_source_archive() {
  if [ -n "$SRC_ARCHIVE" ]; then
    [ -f "$SRC_ARCHIVE" ] || fail "未找到指定源码包：$SRC_ARCHIVE"
    return
  fi

  if [ -f "$CHECKSUM_FILE" ]; then
    local checksum_count checksum_archive
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

  local count
  count="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | wc -l | tr -d ' ')"
  if [ "$count" = "1" ]; then
    SRC_ARCHIVE="$(find "$SCRIPT_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | sort | tail -1)"
    return
  fi

  create_source_archive_from_git
}

cleanup_delivery_metadata() {
  info "检查交付文件夹中的拷贝元数据"
  local metadata
  metadata="$(find "$SCRIPT_DIR" \( -name '__MACOSX' -o -name '.DS_Store' -o -name '._*' -o -name '.AppleDouble' -o -name 'PaxHeaders*' \) -print)"
  [ -z "$metadata" ] || {
    printf '%s\n' "$metadata" >&2
    fail "交付目录含 macOS 元数据，请清理后重新发布"
  }
  ok "拷贝元数据检查完成"
}

run_release_preflight() {
  local preflight_script="$SCRIPT_DIR/01_制包机_发布预检.sh"
  [ -x "$preflight_script" ] || fail "缺少发布预检脚本：$preflight_script"
  TAIJI_RELEASE_REQUIRE_ARTIFACTS="${TAIJI_RELEASE_REQUIRE_ARTIFACTS:-0}" \
    TAIJI_RELEASE_SKIP_GIT_CHECK="${TAIJI_RELEASE_SKIP_GIT_CHECK:-0}" \
    "$preflight_script"
}

preflight() {
  info "检查制包机环境"
  cleanup_delivery_metadata
  [ "$(uname -s)" = "Linux" ] || fail "最终 DEB 必须在 Linux amd64 制包机生成，当前为：$(uname -s)"
  case "$(uname -m)" in
    x86_64|amd64) ok "CPU 架构符合：$(uname -m)" ;;
    *) fail "当前 CPU 架构不是 x86_64/amd64：$(uname -m)" ;;
  esac
  require_cmd apt-get
  require_cmd apt-cache
  require_cmd dpkg
  require_cmd dpkg-deb
  require_cmd dpkg-scanpackages
  require_cmd sha256sum
  require_cmd tar
  require_cmd gzip
  require_cmd git
  arch="$(dpkg --print-architecture 2>/dev/null || true)"
  [ "$arch" = "amd64" ] || fail "dpkg 架构不是 amd64：${arch:-unknown}"
  resolve_source_archive
  [ -f "$SRC_ARCHIVE" ] || fail "未找到源码包：$SRC_ARCHIVE"
  if [ -f "$CHECKSUM_FILE" ]; then
    verify_source_archive_checksum
    ok "源码包校验通过"
  else
    warn "未找到 SHA256SUMS.txt，跳过源码包传输校验"
  fi
  run_release_preflight
}

install_build_dependencies() {
  info "安装制包依赖。这里可能需要输入 sudo 密码。"
  sudo apt-get update
  sudo apt-get install -y \
    curl ca-certificates build-essential python3-dev libffi-dev git rsync \
    dpkg-dev file desktop-file-utils lsof xz-utils tar gzip apt-rdepends
}

node_major() {
  node -p "Number(process.versions.node.split('.')[0])" 2>/dev/null || printf '0\n'
}

npm_major() {
  npm -v 2>/dev/null | awk -F. '{print $1 + 0}' || printf '0\n'
}

source_lab_dir() {
  printf '%s/%s%s%s\n' "$SRC_DIR" "her" "mes-local-" "lab"
}

source_agent_dir() {
  printf '%s/sources/%s%s%s\n' "$(source_lab_dir)" "her" "mes-" "agent"
}

have_modern_node() {
  have node || return 1
  have npm || return 1
  [ "$(node_major)" -ge 20 ] || return 1
  [ "$(npm_major)" -ge 9 ] || return 1
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

curl_download() {
  local url="$1" output="$2"
  curl -fsSL --connect-timeout 15 --retry 2 --retry-delay 2 "$url" -o "$output"
}

node_mirrors() {
  if [ -n "${TAIJI_NODE_MIRRORS:-}" ]; then
    printf '%s\n' $TAIJI_NODE_MIRRORS
    return
  fi
  if [ -n "${NODE_MIRROR:-}" ]; then
    printf '%s\n' "$NODE_MIRROR"
  fi
  printf '%s\n' \
    "https://npmmirror.com/mirrors/node" \
    "https://mirrors.tuna.tsinghua.edu.cn/nodejs-release" \
    "https://mirrors.aliyun.com/nodejs-release" \
    "https://nodejs.org/dist" \
    | awk 'NF && !seen[$0]++'
}

npm_registries() {
  if [ -n "${TAIJI_NPM_REGISTRIES:-}" ]; then
    printf '%s\n' $TAIJI_NPM_REGISTRIES
    return
  fi
  if [ -n "${NPM_CONFIG_REGISTRY:-}" ]; then
    printf '%s\n' "$NPM_CONFIG_REGISTRY"
  fi
  printf '%s\n' \
    "https://registry.npmmirror.com" \
    "https://registry.npmjs.org" \
    | awk 'NF && !seen[$0]++'
}

electron_mirrors() {
  if [ -n "${TAIJI_ELECTRON_MIRRORS:-}" ]; then
    printf '%s\n' $TAIJI_ELECTRON_MIRRORS
    return
  fi
  if [ -n "${ELECTRON_MIRROR:-}" ]; then
    printf '%s\n' "$ELECTRON_MIRROR"
  fi
  printf '%s\n' \
    "https://npmmirror.com/mirrors/electron/" \
    "https://github.com/electron/electron/releases/download/" \
    | awk 'NF && !seen[$0]++'
}

install_portable_node() {
  mkdir -p "$NODE_ROOT"
  if [ -x "$NODE_ROOT/current/bin/node" ] && [ -x "$NODE_ROOT/current/bin/npm" ]; then
    export PATH="$NODE_ROOT/current/bin:$PATH"
    have_modern_node && return 0
  fi

  local mirror release_dir tmp_dir tarball downloaded
  release_dir="latest-v${NODE_MAJOR}.x"
  tmp_dir="$NODE_ROOT/download"
  tarball=""
  downloaded=0
  rm -rf "$tmp_dir"
  mkdir -p "$tmp_dir"

  info "准备 Node.js ${NODE_MAJOR}.x Linux x64 构建工具"
  for mirror in $(node_mirrors); do
    mirror="${mirror%/}"
    [ -n "$mirror" ] || continue
    rm -f "$tmp_dir/SHASUMS256.txt" "$tmp_dir"/node-v*-linux-x64.tar.xz
    info "尝试 Node.js 镜像：$mirror"
    if ! curl_download "$mirror/$release_dir/SHASUMS256.txt" "$tmp_dir/SHASUMS256.txt"; then
      warn "Node.js 校验清单下载失败，切换镜像：$mirror"
      continue
    fi
    tarball="$(awk '/linux-x64.tar.xz$/ {print $2; exit}' "$tmp_dir/SHASUMS256.txt")"
    if [ -z "$tarball" ]; then
      warn "Node.js 校验清单中没有 linux-x64 tarball，切换镜像：$mirror"
      continue
    fi
    if ! curl_download "$mirror/$release_dir/$tarball" "$tmp_dir/$tarball"; then
      warn "Node.js 安装包下载失败，切换镜像：$mirror"
      continue
    fi
    if ! (cd "$tmp_dir" && grep "  $tarball$" SHASUMS256.txt | sha256sum -c -); then
      warn "Node.js 安装包校验失败，切换镜像：$mirror"
      continue
    fi
    downloaded=1
    break
  done

  [ "$downloaded" = "1" ] || fail "无法下载 Node.js ${NODE_MAJOR}.x Linux x64 构建工具；请检查制包机 DNS/代理，或设置 TAIJI_NODE_MIRRORS 为可访问的 Node.js 镜像列表"
  rm -rf "$NODE_ROOT/${tarball%.tar.xz}"
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
  warn "系统 Node/npm 不满足要求，将使用交付目录内的隔离构建工具"
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

npm_ci_with_network_fallback() {
  local registry electron_mirror installed
  installed=0

  for registry in $(npm_registries); do
    registry="${registry%/}"
    [ -n "$registry" ] || continue
    for electron_mirror in $(electron_mirrors); do
      electron_mirror="${electron_mirror%/}/"
      [ -n "$electron_mirror" ] || continue
      info "尝试 npm registry：$registry"
      info "尝试 Electron mirror：$electron_mirror"
      rm -rf node_modules
      if NPM_CONFIG_REGISTRY="$registry" ELECTRON_MIRROR="$electron_mirror" npm ci; then
        export NPM_CONFIG_REGISTRY="$registry"
        export ELECTRON_MIRROR="$electron_mirror"
        installed=1
        break 2
      fi
      warn "npm ci 失败，切换 npm/Electron 下载源"
    done
  done

  [ "$installed" = "1" ] || fail "npm ci 失败：已尝试多个 npm registry 和 Electron mirror；请检查制包机网络、DNS、代理，或设置 TAIJI_NPM_REGISTRIES / TAIJI_ELECTRON_MIRRORS"
}

build_runtime_and_deb() {
  export PATH="$NODE_ROOT/current/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
  export UV_INDEX_URL="${UV_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

  if [ "${TAIJI_ALLOW_UV_LOCK_REFRESH:-0}" = "1" ]; then
    warn "TAIJI_ALLOW_UV_LOCK_REFRESH=1：将刷新目标构建工作区 Python lock；正式发布应使用已提交 lock。"
    cd "$(source_agent_dir)"
    uv lock
  fi

  info "生成 Linux Python venv"
  cd "$(source_lab_dir)"
  TAIJI_UV_LOCK_MODE=strict ./scripts/setup-local.sh

  info "获取 Linux Electron runtime"
  cd "$SRC_DIR/apps/taiji-desktop"
  npm --version
  npm_ci_with_network_fallback

  info "构建 DEB 安装包"
  cd "$SRC_DIR"
  TAIJI_AGENT_VERSION="$VERSION" ./packaging/linux/deb/build-deb.sh
}

collect_artifacts() {
  info "收集安装包产物"
  local src_pkg_dir deb checksum deb_name checksum_name deb_sha src_name src_sha
  src_pkg_dir="$SRC_DIR/packages/麒麟操作系统安装包"
  deb="$src_pkg_dir/taiji-agent_${VERSION}_amd64.deb"
  checksum="$deb.sha256"
  [ -f "$deb" ] || fail "未找到 DEB：$deb"
  [ -f "$checksum" ] || fail "未找到 DEB 校验文件：$checksum"

  rm -f "$OUTPUT_DIR"/taiji-agent_*_amd64.deb "$OUTPUT_DIR"/taiji-agent_*_amd64.deb.sha256 "$BUILD_MARKER" "$MANIFEST_FILE" "$BUILD_REPORT"
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
  ok "安装包已生成：$OUTPUT_DIR/$deb_name"
}

package_names_from_depends() {
  dpkg-deb -f "$1" Depends Pre-Depends 2>/dev/null \
    | tr ',' '\n' \
    | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/[[:space:]]*\\([^)]*\\)//g; s/[[:space:]]*\\[[^]]*\\]//g; s/[[:space:]]*<[^>]*>//g; s/\\|.*$//' \
    | awk 'NF { print $1 }' \
    | sort -u
}

recursive_runtime_dependencies() {
  local deb="$1" pkg
  if have apt-rdepends; then
    package_names_from_depends "$deb" | while read -r pkg; do
      [ -n "$pkg" ] || continue
      apt-rdepends "$pkg" 2>/dev/null | awk '/^[A-Za-z0-9.+:-]+$/ { print $1 }'
    done | sort -u
  else
    package_names_from_depends "$deb" | while read -r pkg; do
      [ -n "$pkg" ] || continue
      printf '%s\n' "$pkg"
      apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances "$pkg" \
        | awk '/^[[:space:]]*Depends:/ { print $2 }'
    done | sort -u
  fi
}

build_offline_dependency_repo() {
  info "生成完全离线 apt 依赖仓库"
  local deb deb_name pkg repo_sha
  deb="$OUTPUT_DIR/taiji-agent_${VERSION}_amd64.deb"
  [ -f "$deb" ] || fail "未找到待打包 DEB：$deb"
  rm -rf "$OFFLINE_REPO"
  mkdir -p "$OFFLINE_REPO"
  cp -f "$deb" "$OFFLINE_REPO/"
  recursive_runtime_dependencies "$deb" > "$OFFLINE_REPO/runtime-dependencies.txt"

  while read -r pkg; do
    [ -n "$pkg" ] || continue
    case "$pkg" in
      taiji-agent) continue ;;
    esac
    info "下载离线依赖：$pkg"
    (cd "$OFFLINE_REPO" && apt-get download "$pkg") || fail "下载依赖失败：$pkg"
  done < "$OFFLINE_REPO/runtime-dependencies.txt"

  deb_name="$(basename "$deb")"
  (cd "$OFFLINE_REPO" && dpkg-scanpackages . /dev/null | gzip -9c > Packages.gz)
  (cd "$OFFLINE_REPO" && sha256sum ./*.deb Packages.gz runtime-dependencies.txt > SHA256SUMS.txt)
  repo_sha="$(sha256sum "$OFFLINE_REPO/Packages.gz" | awk '{print $1}')"
  PACKAGES_GZ_SHA256="$repo_sha"
  ok "离线依赖仓库已生成：$OFFLINE_REPO/Packages.gz"
  ok "主安装包已纳入离线仓库：$deb_name"
  ok "Packages.gz SHA256：$repo_sha"
}

build_glibc() {
  getconf GNU_LIBC_VERSION 2>/dev/null || ldd --version 2>/dev/null | head -1 || printf 'unknown\n'
}

write_release_manifest() {
  info "生成发布 manifest"
  local src_name deb_name checksum_name source_sha deb_sha packages_gz_sha build_os build_glibc build_arch dpkg_arch source_commit
  src_name="$(basename "$SRC_ARCHIVE")"
  deb_name="taiji-agent_${VERSION}_amd64.deb"
  checksum_name="$deb_name.sha256"
  source_sha="$(cd "$SCRIPT_DIR" && sha256sum "$src_name" | awk '{print $1}')"
  deb_sha="$(sha256sum "$OUTPUT_DIR/$deb_name" | awk '{print $1}')"
  packages_gz_sha="$(sha256sum "$OFFLINE_REPO/Packages.gz" | awk '{print $1}')"
  build_os="$(. /etc/os-release 2>/dev/null && printf '%s %s' "${PRETTY_NAME:-Linux}" "${VERSION_ID:-}" || uname -a)"
  build_glibc="$(build_glibc)"
  build_arch="$(uname -m)"
  dpkg_arch="$(dpkg --print-architecture 2>/dev/null || true)"
  source_commit="$(printf '%s\n' "$src_name" | sed -E 's/^taiji-agentv1\.0-kylin-build-src-([^.]+)\.tar\.gz$/\1/')"

  export TAIJI_MANIFEST_FILE="$MANIFEST_FILE"
  export TAIJI_MANIFEST_VERSION="$VERSION"
  export TAIJI_MANIFEST_SOURCE_ARCHIVE="$src_name"
  export TAIJI_MANIFEST_SOURCE_SHA256="$source_sha"
  export TAIJI_MANIFEST_SOURCE_COMMIT="$source_commit"
  export TAIJI_MANIFEST_DEB="$deb_name"
  export TAIJI_MANIFEST_DEB_SHA256="$deb_sha"
  export TAIJI_MANIFEST_CHECKSUM="$checksum_name"
  export TAIJI_MANIFEST_PACKAGES_GZ_SHA256="$packages_gz_sha"
  export TAIJI_MANIFEST_BUILD_OS="$build_os"
  export TAIJI_MANIFEST_BUILD_GLIBC="$build_glibc"
  export TAIJI_MANIFEST_BUILD_ARCH="$build_arch"
  export TAIJI_MANIFEST_DPKG_ARCH="$dpkg_arch"

  python3 - <<'PY'
import datetime
import json
import os
from pathlib import Path

manifest = {
    "schema_version": 1,
    "package": "taiji-agent",
    "version": os.environ["TAIJI_MANIFEST_VERSION"],
    "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "source_commit": os.environ["TAIJI_MANIFEST_SOURCE_COMMIT"],
    "source_archive": os.environ["TAIJI_MANIFEST_SOURCE_ARCHIVE"],
    "source_sha256": os.environ["TAIJI_MANIFEST_SOURCE_SHA256"],
    "deb": os.environ["TAIJI_MANIFEST_DEB"],
    "deb_sha256": os.environ["TAIJI_MANIFEST_DEB_SHA256"],
    "checksum": os.environ["TAIJI_MANIFEST_CHECKSUM"],
    "packages_gz_sha256": os.environ["TAIJI_MANIFEST_PACKAGES_GZ_SHA256"],
    "build_os": os.environ["TAIJI_MANIFEST_BUILD_OS"],
    "build_glibc": os.environ["TAIJI_MANIFEST_BUILD_GLIBC"],
    "build_arch": os.environ["TAIJI_MANIFEST_BUILD_ARCH"],
    "dpkg_arch": os.environ["TAIJI_MANIFEST_DPKG_ARCH"],
    "target_matrix": [
        "Debian-like x86_64/amd64 desktop Linux",
        "Kylin V10 SP1 x86_64 desktop baseline",
        "UOS/openKylin x86_64 desktop, apt/dpkg variant"
    ],
    "support_boundary": {
        "supported": [
            "x86_64/amd64",
            "Debian-like package manager with apt-get and dpkg",
            "Graphical desktop session for Electron startup",
            "Complete delivery directory with generated DEB and local offline apt repository"
        ],
        "unsupported": [
            "RPM-only terminals without dpkg/apt",
            "ARM/aarch64 terminals",
            "Headless or strongly sandboxed terminals without desktop session",
            "Offline installations missing 离线依赖/Packages.gz"
        ]
    }
}
path = Path(os.environ["TAIJI_MANIFEST_FILE"])
path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

  {
    printf 'manifest=%s\n' "$(basename "$MANIFEST_FILE")"
    printf 'packages_gz_sha256=%s\n' "$packages_gz_sha"
  } >> "$BUILD_MARKER"
  ok "发布 manifest 已生成：$MANIFEST_FILE"
}

write_build_report() {
  local commit system_info source_line deb_line repo_line
  commit="$(tar -tzf "$SRC_ARCHIVE" 2>/dev/null | head -1 | sed 's#/.*##' || true)"
  system_info="$(. /etc/os-release 2>/dev/null && printf '%s %s' "${PRETTY_NAME:-Linux}" "${VERSION_ID:-}" || uname -a)"
  source_line="$(cd "$SCRIPT_DIR" && sha256sum "$(basename "$SRC_ARCHIVE")")"
  deb_line="$(cd "$OUTPUT_DIR" && sha256sum "taiji-agent_${VERSION}_amd64.deb")"
  repo_line="$(cd "$OFFLINE_REPO" && sha256sum Packages.gz)"
  {
    printf '太极 Agent 离线交付构建报告\n'
    printf '生成时间：%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    printf '源码包前缀：%s\n' "${commit:-unknown}"
    printf '制包机系统：%s\n' "$system_info"
    printf '制包机架构：%s / %s\n' "$(uname -m)" "$(dpkg --print-architecture 2>/dev/null || true)"
    printf '依赖源：%s\n' "$(grep -hE '^[[:space:]]*deb ' /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null | head -5 | tr '\n' '; ')"
    printf '源码包 SHA256：%s\n' "$source_line"
    printf 'DEB SHA256：%s\n' "$deb_line"
    printf 'Packages.gz SHA256：%s\n' "$repo_line"
    printf '发布 manifest：%s\n' "$(basename "$MANIFEST_FILE")"
    printf '支持边界：Debian-like x86_64/amd64 图形桌面，必须包含离线依赖/Packages.gz；RPM/.run 另行制包。\n'
    printf '目标机安装脚本：02_目标终端_安装并验证.sh\n'
    printf '目标机离线仓库：离线依赖/Packages.gz\n'
  } > "$BUILD_REPORT"
  ok "构建报告已生成：$BUILD_REPORT"
}

main() {
  preflight
  install_build_dependencies
  ensure_uv
  ensure_node
  unpack_source
  build_runtime_and_deb
  collect_artifacts
  build_offline_dependency_repo
  write_release_manifest
  write_build_report
  TAIJI_RELEASE_REQUIRE_ARTIFACTS=1 TAIJI_RELEASE_SKIP_GIT_CHECK=1 run_release_preflight
  printf '\n[OK] 离线交付包生成完成。目标机断网后执行：\n'
  printf 'bash ./02_目标终端_安装并验证.sh\n'
  printf '\n日志：%s\n' "$LOG_FILE"
}

main "$@"
