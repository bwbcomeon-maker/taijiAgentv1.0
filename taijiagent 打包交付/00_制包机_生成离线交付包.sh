#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SRC_ARCHIVE="${TAIJI_SOURCE_ARCHIVE:-}"
CHECKSUM_FILE="$SCRIPT_DIR/SHA256SUMS.txt"
DEFAULT_BUILD_ROOT="/tmp/taiji-agent-build-$(id -u 2>/dev/null || printf user)"
BUILD_ROOT="${TAIJI_BUILD_ROOT:-$DEFAULT_BUILD_ROOT}"
BUILD_ROOT_OWNER_MARKER=".taiji-build-root-owner"
BUILD_ROOT_OWNER_TOKEN="taiji-agent-build-root-v1:$(id -u 2>/dev/null || printf user)"
SRC_DIR="$BUILD_ROOT/taiji-agentv1.0"
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
OFFLINE_REPO="$SCRIPT_DIR/离线依赖"
STATE_HOME="${XDG_STATE_HOME:-${HOME:-}/.local/state}"
LOG_DIR="$STATE_HOME/taiji-agent/build-logs"
DELIVERY_BUILD_LOG_DIR="$SCRIPT_DIR/构建日志"
VERSION=""
TOOL_ROOT="$SCRIPT_DIR/.构建工具"
NODE_ROOT="$TOOL_ROOT/node"
NODE_VERSION="22.23.1"
NODE_ARCHIVE="node-v${NODE_VERSION}-linux-x64.tar.xz"
NODE_ARCHIVE_SHA256="9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"
BUILD_MARKER="$OUTPUT_DIR/.build-success"
BUILD_REPORT="$OUTPUT_DIR/构建报告.txt"
MANIFEST_FILE="$OUTPUT_DIR/taiji-package-manifest.json"

LOG_DIR_REAL=""
LOG_FILE=""
FAILURE_REPORTED=0
CURRENT_STAGE="初始化"

ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }
set_stage() { CURRENT_STAGE="$1"; info "阶段：$CURRENT_STAGE"; }

initialize_build_logging() {
  case "$STATE_HOME" in
    /*) ;;
    *) printf '[FAIL] XDG_STATE_HOME/HOME 必须解析为绝对路径，无法安全保存制包日志：%s\n' "$STATE_HOME" >&2; exit 1 ;;
  esac
  if [ -e "$DELIVERY_BUILD_LOG_DIR" ] || [ -L "$DELIVERY_BUILD_LOG_DIR" ]; then
    printf '[FAIL] 交付目录残留旧构建日志，请先归档并移出后重试：%s\n' "$DELIVERY_BUILD_LOG_DIR" >&2
    exit 1
  fi
  case "$LOG_DIR" in
    "$SCRIPT_DIR"|"$SCRIPT_DIR"/*)
      printf '[FAIL] 制包日志不能位于完整交付目录内：%s\n' "$LOG_DIR" >&2
      exit 1
      ;;
  esac
  mkdir -p "$LOG_DIR" "$OUTPUT_DIR" "$OFFLINE_REPO" \
    || { printf '[FAIL] 无法创建制包日志或交付产物目录\n' >&2; exit 1; }
  [ -d "$LOG_DIR" ] && [ ! -L "$LOG_DIR" ] \
    || { printf '[FAIL] 制包日志目录不是可信实体目录：%s\n' "$LOG_DIR" >&2; exit 1; }
  chmod 0700 "$LOG_DIR" \
    || { printf '[FAIL] 无法设置制包日志目录权限：%s\n' "$LOG_DIR" >&2; exit 1; }
  [ "$(stat -c '%u' "$LOG_DIR")" = "$(id -u)" ] && [ "$(stat -c '%a' "$LOG_DIR")" = "700" ] \
    || { printf '[FAIL] 制包日志目录必须由当前用户以 0700 独占：%s\n' "$LOG_DIR" >&2; exit 1; }
  LOG_DIR_REAL="$(cd "$LOG_DIR" && pwd -P)" \
    || { printf '[FAIL] 无法解析制包日志真实路径：%s\n' "$LOG_DIR" >&2; exit 1; }
  case "$LOG_DIR_REAL" in
    "$SCRIPT_DIR"|"$SCRIPT_DIR"/*)
      printf '[FAIL] 制包日志真实路径不能位于完整交付目录内：%s\n' "$LOG_DIR_REAL" >&2
      exit 1
      ;;
  esac
  LOG_FILE="$LOG_DIR/00_offline_build_$(date +%Y%m%d_%H%M%S)_$$.log"
  exec > >(tee -a "$LOG_FILE") 2>&1
}

safe_cmd_path() {
  command -v "$1" 2>/dev/null || printf 'missing'
}

write_environment_snapshot() {
  local out="$1" cmd
  {
    printf '## 环境\n'
    printf 'script=%s\n' "$0"
    printf 'stage=%s\n' "${CURRENT_STAGE:-unknown}"
    printf 'cwd=%s\n' "$(pwd)"
    printf 'uname=%s\n' "$(uname -a 2>/dev/null || true)"
    if [ -f /etc/os-release ]; then
      printf -- '-- /etc/os-release --\n'
      sed -n '1,40p' /etc/os-release
    fi
    printf 'arch=%s\n' "$(uname -m 2>/dev/null || true)"
    printf 'dpkg_arch=%s\n' "$(dpkg --print-architecture 2>/dev/null || true)"
    printf 'glibc=%s\n' "$(getconf GNU_LIBC_VERSION 2>/dev/null || true)"
    for cmd in sudo apt-get apt-cache dpkg dpkg-deb dpkg-scanpackages sha256sum tar gzip git curl python3 node npm uv systemctl lsof; do
      printf 'cmd.%s=%s\n' "$cmd" "$(safe_cmd_path "$cmd")"
    done
    printf 'TAIJI_NODE_MIRRORS=%s\n' "${TAIJI_NODE_MIRRORS:+set}"
    printf 'TAIJI_NPM_REGISTRIES=%s\n' "${TAIJI_NPM_REGISTRIES:+set}"
    printf 'TAIJI_ELECTRON_MIRRORS=%s\n' "${TAIJI_ELECTRON_MIRRORS:+set}"
    printf 'TAIJI_BUILD_ROOT=%s\n' "${TAIJI_BUILD_ROOT:-}"
    printf 'BUILD_ROOT=%s\n' "$BUILD_ROOT"
    printf 'UV_INDEX_URL=%s\n' "${UV_INDEX_URL:-}"
    printf '\n## 交付目录\n'
    find "$SCRIPT_DIR" -maxdepth 2 \( -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' -o -name 'SHA256SUMS.txt' -o -name '*.zip' -o -name '*.deb' -o -name 'Packages' -o -name 'Packages.gz' -o -name '.build-success' -o -name 'taiji-package-manifest.json' -o -name '构建报告.txt' \) -print 2>/dev/null | sort
    printf '\n## 最新日志\n'
    [ -f "$LOG_FILE" ] && tail -n 160 "$LOG_FILE"
  } >> "$out" 2>&1 || true
}

failure_next_steps() {
  local reason="${1:-}"
  case "$reason" in
    *"最终 DEB 必须在 Linux amd64"*|*"不是 x86_64/amd64"*|*"dpkg 架构不是 amd64"*)
      printf 'next=换到 Linux x86_64/amd64 + apt/dpkg 制包机后重新执行 bash ./00_制包机_生成离线交付包.sh\n'
      ;;
    *"管理员权限"*|*"sudo"*)
      printf 'next=先在制包机终端执行 sudo -v，确认当前用户具备管理员权限后重试\n'
      ;;
    *"kysec"*|*"Permission denied by kysec"*)
      printf 'next=麒麟安全策略拦截了构建脚本中的解释器写文件。新版脚本已避免用 python 写 manifest/report；请使用最新制包输入包重新构建\n'
      ;;
    *"源码包"*|*"SHA256"*|*"当前 commit"*|*"未提交改动"*|*"已暂存未提交"*)
      printf 'next=在本地重新生成唯一源码包和 SHA256SUMS.txt，并重新拷贝整个交付目录\n'
      ;;
    *"Node.js"*|*"npm ci"*|*"Electron"*)
      printf 'next=检查 DNS/代理/镜像，必要时设置 TAIJI_NODE_MIRRORS、TAIJI_NPM_REGISTRIES、TAIJI_ELECTRON_MIRRORS 后重试\n'
      ;;
    *"pyproject.toml"*|*"Permission denied"*|*"os error 13"*|*"源码权限不可读"*)
      printf 'next=构建工作区源码权限不可读。新版脚本默认使用 /tmp/taiji-agent-build-<uid> 并在解压后修复权限；如仍失败，检查终端安全管控/ACL，或设置 TAIJI_BUILD_ROOT=/tmp/taiji-agent-build-test 后重试\n'
      ;;
    *"setup-local.sh"*|*"uv.lock"*|*"--locked"*|*"TAIJI_UV_LOCK_MODE"*)
      printf 'next=Python 依赖 lock 漂移。新版脚本默认 TAIJI_UV_LOCK_MODE=auto 会自动重试非 locked 同步；如仍使用旧包，可先用 TAIJI_ALLOW_UV_LOCK_REFRESH=1 bash ./00_制包机_生成离线交付包.sh 临时继续\n'
      ;;
    *"离线依赖"*|*"Packages.gz"*|*"apt-get download"*)
      printf 'next=确认制包机 apt 源可访问目标机同发行版/架构依赖，重新生成离线依赖仓库\n'
      ;;
    *)
      printf 'next=查看本诊断文件和主日志，按最后一个 [FAIL]/命令错误继续定位\n'
      ;;
  esac
}

write_failure_diagnostic() {
  local code="${1:-1}" reason="${2:-unknown}" diag
  [ "${FAILURE_REPORTED:-0}" = "1" ] && return 0
  FAILURE_REPORTED=1
  set +e
  diag="$LOG_DIR/失败诊断-$(date +%Y%m%d_%H%M%S).txt"
  {
    printf '太极 Agent 制包失败诊断\n'
    printf 'time=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    printf 'exit_code=%s\n' "$code"
    printf 'reason=%s\n' "$reason"
    failure_next_steps "$reason"
    printf '\n'
  } > "$diag"
  write_environment_snapshot "$diag"
  printf '[FAIL] 已生成失败诊断：%s\n' "$diag" >&2
  set -e
}

fail() {
  local msg="$*"
  printf '[FAIL] %s\n' "$msg" >&2
  write_failure_diagnostic 1 "$msg"
  exit 1
}

on_error() {
  local code="$1" command_text="${2:-unknown}"
  write_failure_diagnostic "$code" "命令失败：$command_text"
  printf '\n[FAIL] 离线交付包生成中断，请查看日志：%s\n' "$LOG_FILE" >&2
  exit "$code"
}

trap 'on_error "$?" "$BASH_COMMAND"' ERR

require_cmd() { have "$1" || fail "缺少命令：$1"; }

read_product_version() {
  local version_file="$SRC_DIR/VERSION" product_version
  [ -f "$version_file" ] || fail "源码缺少根 VERSION：$version_file"
  product_version="$(tr -d '\r\n' < "$version_file")"
  printf '%s\n' "$product_version" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' \
    || fail "根 VERSION 不是三段 SemVer：$product_version"
  if [ -n "${TAIJI_AGENT_VERSION:-}" ] && [ "$TAIJI_AGENT_VERSION" != "$product_version" ]; then
    fail "TAIJI_AGENT_VERSION 必须与根 VERSION 一致：root=$product_version env=$TAIJI_AGENT_VERSION"
  fi
  printf '%s\n' "$product_version"
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
  require_cmd git
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
  if [ -n "$metadata" ]; then
    warn "发现 macOS 拷贝元数据，将自动清理"
    printf '%s\n' "$metadata" >&2
    find "$SCRIPT_DIR" \( -name '__MACOSX' -o -name '.DS_Store' -o -name '._*' -o -name '.AppleDouble' -o -name 'PaxHeaders*' \) -exec rm -rf -- {} +
  fi
  ok "拷贝元数据检查完成"
}

require_admin_capability() {
  require_cmd sudo
  if sudo -n true >/dev/null 2>&1; then
    ok "管理员权限预检通过：sudo 已可用"
    return
  fi
  info "需要管理员权限预检。这里可能需要输入 sudo 密码。"
  sudo -v || fail "管理员权限预检失败：当前用户不能执行 sudo，无法安装制包依赖"
  ok "管理员权限预检通过"
}

run_release_preflight() {
  local preflight_script="$SCRIPT_DIR/01_制包机_发布预检.sh"
  local repo_root="${1:-$(cd "$SCRIPT_DIR/.." && pwd)}"
  [ -x "$preflight_script" ] || fail "缺少发布预检脚本：$preflight_script"
  TAIJI_RELEASE_REQUIRE_ARTIFACTS="${TAIJI_RELEASE_REQUIRE_ARTIFACTS:-0}" \
    TAIJI_RELEASE_SKIP_GIT_CHECK="${TAIJI_RELEASE_SKIP_GIT_CHECK:-0}" \
    TAIJI_REPO_ROOT="$repo_root" \
    "$preflight_script"
}

preflight() {
  info "检查制包机环境"
  cleanup_delivery_metadata
  validate_build_root_location
  [ "$(uname -s)" = "Linux" ] || fail "最终 DEB 必须在 Linux amd64 制包机生成，当前为：$(uname -s)"
  case "$(uname -m)" in
    x86_64|amd64) ok "CPU 架构符合：$(uname -m)" ;;
    *) fail "当前 CPU 架构不是 x86_64/amd64：$(uname -m)" ;;
  esac
  require_cmd apt-get
  require_cmd apt-cache
  require_cmd dpkg
  require_cmd sha256sum
  require_admin_capability
  arch="$(dpkg --print-architecture 2>/dev/null || true)"
  [ "$arch" = "amd64" ] || fail "dpkg 架构不是 amd64：${arch:-unknown}"
}

validate_build_root_location() {
  local resolved_script resolved_build
  have readlink || fail "缺少 readlink，无法验证临时构建目录边界"
  validate_safe_build_root_path
  resolved_script="$(readlink -f -- "$SCRIPT_DIR")" || fail "无法解析交付目录真实路径：$SCRIPT_DIR"
  resolved_build="$(readlink -m -- "$BUILD_ROOT")" || fail "无法解析临时构建目录：$BUILD_ROOT"
  case "$resolved_build" in
    "$resolved_script"|"$resolved_script"/*)
      fail "TAIJI_BUILD_ROOT 不能位于交付目录内：$resolved_build"
      ;;
  esac
}

validate_safe_build_root_path() {
  local resolved_build build_basename
  case "$BUILD_ROOT" in
    /*) ;;
    *) fail "TAIJI_BUILD_ROOT 必须是绝对路径：$BUILD_ROOT" ;;
  esac
  resolved_build="$(readlink -m -- "$BUILD_ROOT")" || fail "无法解析临时构建目录：$BUILD_ROOT"
  build_basename="${resolved_build##*/}"
  case "$build_basename" in
    taiji-agent-build-?*) ;;
    *) fail "TAIJI_BUILD_ROOT 必须使用 taiji-agent-build-* 专用目录名：$resolved_build" ;;
  esac
  case "$resolved_build" in
    "/"|"/tmp"|"/home"|"/usr"|"/var")
      fail "拒绝使用危险构建目录：$resolved_build"
      ;;
  esac
  [ ! -L "$BUILD_ROOT" ] || fail "TAIJI_BUILD_ROOT 不能是符号链接：$BUILD_ROOT"
  if [ -e "$BUILD_ROOT" ] && [ ! -d "$BUILD_ROOT" ]; then
    fail "TAIJI_BUILD_ROOT 已存在但不是目录：$BUILD_ROOT"
  fi
}

require_owned_build_root() {
  local marker="$BUILD_ROOT/$BUILD_ROOT_OWNER_MARKER" current_uid root_uid root_mode marker_uid marker_mode marker_links marker_value
  validate_safe_build_root_path
  [ -d "$BUILD_ROOT" ] && [ ! -L "$BUILD_ROOT" ] \
    || fail "构建工作区不是可信实体目录：$BUILD_ROOT"
  current_uid="$(id -u)"
  root_uid="$(stat -c '%u' "$BUILD_ROOT")" || fail "无法读取构建工作区所有者：$BUILD_ROOT"
  root_mode="$(stat -c '%a' "$BUILD_ROOT")" || fail "无法读取构建工作区权限：$BUILD_ROOT"
  [ "$root_uid" = "$current_uid" ] || fail "构建工作区不属于当前用户，拒绝清理：$BUILD_ROOT"
  [ "$root_mode" = "700" ] || fail "构建工作区权限必须是 0700，拒绝清理：$BUILD_ROOT"
  [ -f "$marker" ] && [ ! -L "$marker" ] || fail "构建工作区缺少可信所有权标记：$marker"
  marker_uid="$(stat -c '%u' "$marker")" || fail "无法读取构建工作区标记所有者：$marker"
  marker_mode="$(stat -c '%a' "$marker")" || fail "无法读取构建工作区标记权限：$marker"
  marker_links="$(stat -c '%h' "$marker")" || fail "无法读取构建工作区标记链接数：$marker"
  [ "$marker_uid" = "$current_uid" ] && [ "$marker_mode" = "600" ] && [ "$marker_links" = "1" ] \
    || fail "构建工作区所有权标记不可信：$marker"
  marker_value="$(tr -d '\r\n' < "$marker")"
  [ "$marker_value" = "$BUILD_ROOT_OWNER_TOKEN" ] \
    || fail "构建工作区所有权标记不匹配：$marker"
}

create_owned_build_root() {
  local marker="$BUILD_ROOT/$BUILD_ROOT_OWNER_MARKER" marker_tmp
  validate_safe_build_root_path
  mkdir -p -- "$BUILD_ROOT" || fail "无法创建构建工作区：$BUILD_ROOT"
  chmod 0700 "$BUILD_ROOT" || fail "无法设置构建工作区权限：$BUILD_ROOT"
  [ "$(stat -c '%u' "$BUILD_ROOT")" = "$(id -u)" ] \
    || fail "新建构建工作区不属于当前用户：$BUILD_ROOT"
  marker_tmp="$marker.tmp.$$"
  (umask 077; printf '%s\n' "$BUILD_ROOT_OWNER_TOKEN" > "$marker_tmp") \
    || fail "无法写入构建工作区所有权标记：$marker_tmp"
  chmod 0600 "$marker_tmp" || fail "无法设置构建工作区标记权限：$marker_tmp"
  mv -f -- "$marker_tmp" "$marker" || fail "无法发布构建工作区所有权标记：$marker"
  require_owned_build_root
}

prepare_source_release() {
  info "准备并校验源码包"
  require_cmd tar
  require_cmd gzip
  require_cmd sha256sum
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
  sudo env DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC apt-get update
  sudo env DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC apt-get install -y \
    curl ca-certificates build-essential python3-dev libffi-dev git rsync \
    dpkg-dev file desktop-file-utils lsof xz-utils tar gzip apt-rdepends openssl \
    libc6 libgtk-3-0 libnss3 libnspr4 libxss1 libasound2 libatk1.0-0 \
    libatk-bridge2.0-0 libatspi2.0-0 libdrm2 libgbm1 libxkbcommon0 libx11-6 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libxrender1 \
    libxshmfence1 libxcb1 libcups2 libdbus-1-3 libglib2.0-0 libpango-1.0-0 \
    libcairo2 libexpat1 libfontconfig1 libsecret-1-0 libxtst6 libuuid1 xdg-utils
}

source_lab_dir() {
  printf '%s/%s%s%s\n' "$SRC_DIR" "her" "mes-local-" "lab"
}

source_agent_dir() {
  printf '%s/sources/%s%s%s\n' "$(source_lab_dir)" "her" "mes-" "agent"
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

portable_node_is_exact() {
  local root="$NODE_ROOT/current"
  [ -x "$root/bin/node" ] || return 1
  [ -x "$root/bin/npm" ] || return 1
  [ -f "$root/.taiji-node-version" ] || return 1
  [ -f "$root/.taiji-node-archive-sha256" ] || return 1
  [ "$(tr -d '\r\n' < "$root/.taiji-node-version")" = "$NODE_VERSION" ] || return 1
  [ "$(tr -d '\r\n' < "$root/.taiji-node-archive-sha256")" = "$NODE_ARCHIVE_SHA256" ] || return 1
  [ "$("$root/bin/node" --version 2>/dev/null)" = "v$NODE_VERSION" ] || return 1
  file "$root/bin/node" | grep -Eq 'ELF 64-bit.*(x86-64|X86-64|80386)' || return 1
}

install_portable_node() {
  mkdir -p "$NODE_ROOT"
  if portable_node_is_exact; then
    export PATH="$NODE_ROOT/current/bin:$PATH"
    return 0
  fi

  local mirror release_dir tmp_dir tarball downloaded actual_sha extracted_root
  release_dir="v${NODE_VERSION}"
  tmp_dir="$NODE_ROOT/download"
  tarball="$NODE_ARCHIVE"
  downloaded=0
  rm -rf "$tmp_dir"
  mkdir -p "$tmp_dir"

  info "准备固定版 Node.js ${NODE_VERSION} Linux x64 离线运行时"
  for mirror in $(node_mirrors); do
    mirror="${mirror%/}"
    [ -n "$mirror" ] || continue
    rm -f "$tmp_dir/$tarball"
    info "尝试 Node.js 镜像：$mirror"
    if ! curl_download "$mirror/$release_dir/$tarball" "$tmp_dir/$tarball"; then
      warn "Node.js 安装包下载失败，切换镜像：$mirror"
      continue
    fi
    actual_sha="$(sha256sum "$tmp_dir/$tarball" | awk '{print $1}')"
    if [ "$actual_sha" != "$NODE_ARCHIVE_SHA256" ]; then
      warn "Node.js 安装包校验失败，切换镜像：$mirror"
      continue
    fi
    downloaded=1
    break
  done

  [ "$downloaded" = "1" ] || fail "无法下载 Node.js ${NODE_VERSION} Linux x64 离线运行时，或下载内容校验失败；请检查制包机 DNS/代理，或设置 TAIJI_NODE_MIRRORS"
  extracted_root="$NODE_ROOT/${tarball%.tar.xz}"
  rm -rf "$extracted_root"
  tar -xJf "$tmp_dir/$tarball" -C "$NODE_ROOT"
  [ -x "$extracted_root/bin/node" ] || fail "Node.js 离线运行时解压后缺少 bin/node"
  printf '%s\n' "$NODE_VERSION" > "$extracted_root/.taiji-node-version"
  printf '%s\n' "$NODE_ARCHIVE_SHA256" > "$extracted_root/.taiji-node-archive-sha256"
  ln -sfn "$extracted_root" "$NODE_ROOT/current"
  export PATH="$NODE_ROOT/current/bin:$PATH"
  portable_node_is_exact || fail "Node.js ${NODE_VERSION} Linux x64 离线运行时验证失败"
}

ensure_node() {
  export PATH="$NODE_ROOT/current/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
  install_portable_node
  portable_node_is_exact || fail "固定版 Node.js 离线运行时不可用，禁止回退到系统 Node"
  ok "Node.js 离线运行时已准备：$NODE_ROOT/current ($(node --version), npm $(npm -v))"
}

restore_owned_build_root_directory_writes() {
  find "$BUILD_ROOT" -type d -exec chmod u+w {} + \
    || fail "无法恢复受控构建工作区目录的 owner 写权限：$BUILD_ROOT"
}

reset_build_root() {
  validate_safe_build_root_path
  if [ -e "$BUILD_ROOT" ] || [ -L "$BUILD_ROOT" ]; then
    require_owned_build_root
    restore_owned_build_root_directory_writes
    rm -rf -- "$BUILD_ROOT" || fail "无法以当前用户清理专用构建工作区：$BUILD_ROOT"
    [ ! -e "$BUILD_ROOT" ] && [ ! -L "$BUILD_ROOT" ] \
      || fail "专用构建工作区清理后仍存在：$BUILD_ROOT"
  fi
  create_owned_build_root
}

repair_build_tree_permissions() {
  local agent_dir lab_dir setup_script pyproject
  lab_dir="$(source_lab_dir)"
  agent_dir="$(source_agent_dir)"
  setup_script="$lab_dir/scripts/setup-local.sh"
  pyproject="$agent_dir/pyproject.toml"

  chmod -R u+rwX,go+rX "$SRC_DIR" || fail "源码解压后权限修复失败：$SRC_DIR"
  [ -f "$pyproject" ] || fail "源码解压后缺少 Python 项目文件：pyproject.toml"
  [ -r "$pyproject" ] || fail "源码权限不可读：pyproject.toml"
  [ -f "$setup_script" ] || fail "源码解压后缺少初始化脚本：scripts/setup-local.sh"
  chmod +x "$setup_script" || fail "初始化脚本不可执行：scripts/setup-local.sh"
}

unpack_source() {
  info "解压源码到构建工作区"
  info "构建工作区：$BUILD_ROOT"
  reset_build_root
  tar -xzf "$SRC_ARCHIVE" -C "$BUILD_ROOT"
  [ -d "$SRC_DIR" ] || fail "源码解压后未找到：$SRC_DIR"
  repair_build_tree_permissions
  VERSION="$(read_product_version)"
  ok "源码已解压：$SRC_DIR"
}

npm_ci_with_network_fallback() {
  local registry electron_mirror installed
  local -a npm_args=("$@")
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
      if NPM_CONFIG_REGISTRY="$registry" ELECTRON_MIRROR="$electron_mirror" npm ci "${npm_args[@]}"; then
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

run_setup_local() {
  local uv_lock_mode="$1" setup_log status
  setup_log="$LOG_DIR/setup-local-$(date +%Y%m%d_%H%M%S)_$$.log"

  set +e
  TAIJI_UV_LOCK_MODE="$uv_lock_mode" ./scripts/setup-local.sh 2>&1 | tee -a "$setup_log"
  status="${PIPESTATUS[0]}"
  set -e

  if [ "$status" -ne 0 ]; then
    if grep -qiE 'pyproject\.toml|Permission denied|os error 13' "$setup_log"; then
      fail "Python venv 生成失败：构建工作区源码权限不可读（pyproject.toml Permission denied）"
    fi
    fail "Python venv 生成失败：setup-local.sh 返回 $status，详见 $setup_log"
  fi
}

build_runtime_and_deb() {
  local uv_lock_mode
  export PATH="$NODE_ROOT/current/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
  export UV_INDEX_URL="${UV_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
  uv_lock_mode="${TAIJI_UV_LOCK_MODE:-auto}"

  if [ "${TAIJI_ALLOW_UV_LOCK_REFRESH:-0}" = "1" ]; then
    warn "TAIJI_ALLOW_UV_LOCK_REFRESH=1：将刷新目标构建工作区 Python lock；正式发布应使用已提交 lock。"
    cd "$(source_agent_dir)"
    uv lock
  fi

  info "生成 Linux Python venv（TAIJI_UV_LOCK_MODE=$uv_lock_mode）"
  cd "$(source_lab_dir)"
  run_setup_local "$uv_lock_mode"

  info "获取 Linux Electron runtime"
  cd "$SRC_DIR/apps/taiji-desktop"
  npm --version
  npm_ci_with_network_fallback --omit=dev

  info "准备 DOCX Engine V2 生产依赖并执行源码测试"
  cd "$(source_lab_dir)/sources/docx-engine-v2"
  npm_ci_with_network_fallback --omit=dev
  node scripts/materialize-portable-resvg-dependencies.js
  npm test

  info "构建 DEB 安装包"
  cd "$SRC_DIR"
  TAIJI_AGENT_VERSION="$VERSION" \
  TAIJI_PACKAGED_NODE_ROOT="$NODE_ROOT/current" \
    ./packaging/linux/deb/build-deb.sh
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
  deb_name="taiji-agent_${VERSION}_amd64.deb"
  checksum_name="$deb_name.sha256"
  deb_sha="$(sha256sum "$OUTPUT_DIR/$deb_name" | awk '{print $1}')"
  printf '%s  %s\n' "$deb_sha" "$deb_name" > "$OUTPUT_DIR/$checksum_name"
  (cd "$OUTPUT_DIR" && sha256sum -c "$checksum_name")
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
  local deb deb_name pkg packages_sha packages_gz_sha
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
  (cd "$OFFLINE_REPO" && dpkg-scanpackages . /dev/null > Packages)
  (cd "$OFFLINE_REPO" && gzip -9c Packages > Packages.gz)
  (cd "$OFFLINE_REPO" && sha256sum ./*.deb Packages Packages.gz runtime-dependencies.txt > SHA256SUMS.txt)
  packages_sha="$(sha256sum "$OFFLINE_REPO/Packages" | awk '{print $1}')"
  packages_gz_sha="$(sha256sum "$OFFLINE_REPO/Packages.gz" | awk '{print $1}')"
  ok "离线依赖仓库已生成：$OFFLINE_REPO/Packages.gz"
  ok "主安装包已纳入离线仓库：$deb_name"
  ok "Packages SHA256：$packages_sha"
  ok "Packages.gz SHA256：$packages_gz_sha"
}

build_glibc() {
  getconf GNU_LIBC_VERSION 2>/dev/null || ldd --version 2>/dev/null | head -1 || printf 'unknown\n'
}

cleanup_release_manifest_payload() {
  local root="$1"
  case "$root" in
    /tmp/taiji-release-manifest.*) ;;
    *) fail "拒绝清理非专用 manifest 临时目录：$root" ;;
  esac
  if [ -e "$root" ] || [ -L "$root" ]; then
    [ -d "$root" ] && [ ! -L "$root" ] || fail "manifest 临时路径不是实体目录：$root"
    find "$root" -type d -exec chmod u+w {} + \
      || fail "无法恢复 manifest 临时目录的 owner 写权限：$root"
    rm -rf -- "$root" || fail "无法清理 manifest 临时目录：$root"
  fi
  [ ! -e "$root" ] && [ ! -L "$root" ] || fail "manifest 临时目录清理后仍存在：$root"
}

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '%s' "$value"
}

json_string() {
  printf '"%s"' "$(json_escape "$1")"
}

write_release_manifest() {
  info "生成发布 manifest"
  local src_name deb_name checksum_name source_sha deb_sha packages_sha packages_gz_sha build_os build_glibc build_arch dpkg_arch source_commit
  local payload_root electron_executable_sha desktop_entry_sha
  src_name="$(basename "$SRC_ARCHIVE")"
  deb_name="taiji-agent_${VERSION}_amd64.deb"
  checksum_name="$deb_name.sha256"
  source_sha="$(cd "$SCRIPT_DIR" && sha256sum "$src_name" | awk '{print $1}')"
  deb_sha="$(sha256sum "$OUTPUT_DIR/$deb_name" | awk '{print $1}')"
  packages_sha="$(sha256sum "$OFFLINE_REPO/Packages" | awk '{print $1}')"
  packages_gz_sha="$(sha256sum "$OFFLINE_REPO/Packages.gz" | awk '{print $1}')"
  build_os="$(. /etc/os-release 2>/dev/null && printf '%s %s' "${PRETTY_NAME:-Linux}" "${VERSION_ID:-}" || uname -a)"
  build_glibc="$(build_glibc)"
  build_arch="$(uname -m)"
  dpkg_arch="$(dpkg --print-architecture 2>/dev/null || true)"
  source_commit="$(printf '%s\n' "$src_name" | sed -E 's/^taiji-agentv1\.0-kylin-build-src-([^.]+)\.tar\.gz$/\1/')"
  payload_root="$(mktemp -d /tmp/taiji-release-manifest.XXXXXX)"
  if ! dpkg-deb -x "$OUTPUT_DIR/$deb_name" "$payload_root"; then
    cleanup_release_manifest_payload "$payload_root"
    fail "无法解包当前 DEB 以绑定 Electron/desktop entry 摘要"
  fi
  if [ ! -f "$payload_root/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron" ] \
    || [ -L "$payload_root/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron" ] \
    || [ ! -f "$payload_root/usr/share/applications/taiji-agent.desktop" ] \
    || [ -L "$payload_root/usr/share/applications/taiji-agent.desktop" ]; then
    cleanup_release_manifest_payload "$payload_root"
    fail "当前 DEB 缺少可绑定的安装态 Electron 或 desktop entry"
  fi
  electron_executable_sha="$(sha256sum "$payload_root/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron" | awk '{print $1}')"
  desktop_entry_sha="$(sha256sum "$payload_root/usr/share/applications/taiji-agent.desktop" | awk '{print $1}')"
  cleanup_release_manifest_payload "$payload_root"

  {
    printf '{\n'
    printf '  "build_arch": %s,\n' "$(json_string "$build_arch")"
    printf '  "build_glibc": %s,\n' "$(json_string "$build_glibc")"
    printf '  "build_os": %s,\n' "$(json_string "$build_os")"
    printf '  "built_at": %s,\n' "$(json_string "$(date -u '+%Y-%m-%dT%H:%M:%S%z')")"
    printf '  "checksum": %s,\n' "$(json_string "$checksum_name")"
    printf '  "deb": %s,\n' "$(json_string "$deb_name")"
    printf '  "deb_sha256": %s,\n' "$(json_string "$deb_sha")"
    printf '  "desktop_entry_sha256": %s,\n' "$(json_string "$desktop_entry_sha")"
    printf '  "dpkg_arch": %s,\n' "$(json_string "$dpkg_arch")"
    printf '  "electron_executable_sha256": %s,\n' "$(json_string "$electron_executable_sha")"
    printf '  "package": "taiji-agent",\n'
    printf '  "packages_sha256": %s,\n' "$(json_string "$packages_sha")"
    printf '  "packages_gz_sha256": %s,\n' "$(json_string "$packages_gz_sha")"
    printf '  "schema_version": 1,\n'
    printf '  "source_archive": %s,\n' "$(json_string "$src_name")"
    printf '  "source_commit": %s,\n' "$(json_string "$source_commit")"
    printf '  "source_sha256": %s,\n' "$(json_string "$source_sha")"
    printf '  "support_boundary": {\n'
    printf '    "supported": [\n'
    printf '      "x86_64/amd64",\n'
    printf '      "Debian-like package manager with apt-get and dpkg",\n'
    printf '      "Graphical desktop session for Electron startup",\n'
    printf '      "Complete delivery directory with generated DEB and local offline apt repository"\n'
    printf '    ],\n'
    printf '    "unsupported": [\n'
    printf '      "RPM-only terminals without dpkg/apt",\n'
    printf '      "ARM/aarch64 terminals",\n'
    printf '      "Headless or strongly sandboxed terminals without desktop session",\n'
    printf '      "Offline installations missing 离线依赖/Packages or Packages.gz"\n'
    printf '    ]\n'
    printf '  },\n'
    printf '  "target_matrix": [\n'
    printf '    "Debian-like x86_64/amd64 desktop Linux",\n'
    printf '    "Kylin V10 SP1 x86_64 desktop baseline",\n'
    printf '    "UOS/openKylin x86_64 desktop, apt/dpkg variant"\n'
    printf '  ],\n'
    printf '  "version": %s\n' "$(json_string "$VERSION")"
    printf '}\n'
  } > "$MANIFEST_FILE"

  {
    printf 'manifest=%s\n' "$(basename "$MANIFEST_FILE")"
    printf 'packages_sha256=%s\n' "$packages_sha"
    printf 'packages_gz_sha256=%s\n' "$packages_gz_sha"
  } >> "$BUILD_MARKER"
  ok "发布 manifest 已生成：$MANIFEST_FILE"
}

write_build_report() {
  local commit system_info source_line deb_line packages_line packages_gz_line
  commit="$(tar -tzf "$SRC_ARCHIVE" 2>/dev/null | head -1 | sed 's#/.*##' || true)"
  system_info="$(. /etc/os-release 2>/dev/null && printf '%s %s' "${PRETTY_NAME:-Linux}" "${VERSION_ID:-}" || uname -a)"
  source_line="$(cd "$SCRIPT_DIR" && sha256sum "$(basename "$SRC_ARCHIVE")")"
  deb_line="$(cd "$OUTPUT_DIR" && sha256sum "taiji-agent_${VERSION}_amd64.deb")"
  packages_line="$(cd "$OFFLINE_REPO" && sha256sum Packages)"
  packages_gz_line="$(cd "$OFFLINE_REPO" && sha256sum Packages.gz)"
  {
    printf '太极 Agent 离线交付构建报告\n'
    printf '生成时间：%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    printf '源码包前缀：%s\n' "${commit:-unknown}"
    printf '制包机系统：%s\n' "$system_info"
    printf '制包机架构：%s / %s\n' "$(uname -m)" "$(dpkg --print-architecture 2>/dev/null || true)"
    printf 'glibc：%s\n' "$(build_glibc)"
    printf 'sudo：%s\n' "$(safe_cmd_path sudo)"
    printf 'apt-get：%s\n' "$(safe_cmd_path apt-get)"
    printf 'dpkg-deb：%s\n' "$(safe_cmd_path dpkg-deb)"
    printf 'python3：%s\n' "$(python3 --version 2>/dev/null || true)"
    printf 'uv：%s\n' "$(uv --version 2>/dev/null || true)"
    printf 'uv lock 模式：%s\n' "${TAIJI_UV_LOCK_MODE:-auto}"
    printf 'node：%s\n' "$(node --version 2>/dev/null || true)"
    printf 'npm：%s\n' "$(npm -v 2>/dev/null || true)"
    printf 'Node 镜像覆盖：%s\n' "${TAIJI_NODE_MIRRORS:+已设置}"
    printf 'npm 镜像覆盖：%s\n' "${TAIJI_NPM_REGISTRIES:+已设置}"
    printf 'Electron 镜像覆盖：%s\n' "${TAIJI_ELECTRON_MIRRORS:+已设置}"
    printf '依赖源：%s\n' "$(apt_source_summary)"
    printf '源码包 SHA256：%s\n' "$source_line"
    printf 'DEB SHA256：%s\n' "$deb_line"
    printf 'Packages SHA256：%s\n' "$packages_line"
    printf 'Packages.gz SHA256：%s\n' "$packages_gz_line"
    printf '发布 manifest：%s\n' "$(basename "$MANIFEST_FILE")"
    printf '支持边界：Debian-like x86_64/amd64 图形桌面，必须同时包含离线依赖/Packages 与 Packages.gz；RPM/.run 另行制包。\n'
    printf '目标机安装脚本：02_目标终端_安装并验证.sh\n'
    printf '目标机离线仓库：离线依赖/Packages 与 Packages.gz\n'
  } > "$BUILD_REPORT"
  ok "构建报告已生成：$BUILD_REPORT"
}

stage_target_acceptance_tools() {
  local target="$SCRIPT_DIR/验收工具"
  local driver="$SRC_DIR/tools/taiji-desktop-acceptance/run-installed-electron-acceptance.js"
  local assembler="$SRC_DIR/tools/taiji-desktop-acceptance/assemble-target-evidence.py"
  local validator="$SRC_DIR/scripts/validate-taiji-release-evidence.py"
  local public_key="$SRC_DIR/tools/taiji-release-evidence/signing-public.pem"
  local public_fingerprint expected_fingerprint
  expected_fingerprint="839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"

  info "收集目标终端真实 Electron 桌面 App 验收工具"
  [ -f "$driver" ] && [ ! -L "$driver" ] || fail "源码缺少桌面 App 验收驱动：$driver"
  [ -f "$assembler" ] && [ ! -L "$assembler" ] || fail "源码缺少目标证据组装器：$assembler"
  [ -f "$validator" ] && [ ! -L "$validator" ] || fail "源码缺少发布证据校验器：$validator"
  [ -f "$public_key" ] && [ ! -L "$public_key" ] || fail "源码缺少发布证据验签公钥：$public_key"
  node --check "$driver" >/dev/null || fail "桌面 App 验收驱动 JavaScript 语法检查失败"
  python3 - "$assembler" "$validator" <<'PY' || fail "目标证据 Python 工具语法检查失败"
import sys
from pathlib import Path

for raw in sys.argv[1:]:
    path = Path(raw)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
PY
  public_fingerprint="$(openssl pkey -pubin -in "$public_key" -outform DER 2>/dev/null | openssl dgst -sha256 -r | awk '{print $1}')"
  [ "$public_fingerprint" = "$expected_fingerprint" ] || fail "目标验收验签公钥 fingerprint 不匹配"

  rm -rf -- "$target"
  install -d -m 0755 "$target"
  install -m 0644 "$driver" "$target/run-installed-electron-acceptance.js"
  install -m 0644 "$assembler" "$target/assemble-target-evidence.py"
  install -m 0644 "$validator" "$target/validate-taiji-release-evidence.py"
  install -m 0644 "$public_key" "$target/signing-public.pem"
  ok "目标终端桌面 App 验收工具已收集：$target"
}

cleanup_delivery_build_cache() {
  info "清理交付目录中的制包缓存"
  rm -rf "$TOOL_ROOT" || fail "无法清理制包缓存：$TOOL_ROOT"
  [ ! -e "$TOOL_ROOT" ] || fail "制包缓存仍然存在：$TOOL_ROOT"
  ok "交付目录制包缓存已清理"
}

normalize_delivery_permissions() {
  local unsafe_node
  info "归一化交付目录权限"
  unsafe_node="$(find "$SCRIPT_DIR" -xdev -mindepth 1 \( -type l -o \( -type f -links +1 \) \) -print -quit)"
  [ -z "$unsafe_node" ] || fail "交付目录含符号链接或硬链接，拒绝修改其权限：$unsafe_node"
  chmod go-w "$SCRIPT_DIR"
  find "$SCRIPT_DIR" -xdev -mindepth 1 \( -type d -o -type f \) -exec chmod go-w -- {} +
  for script in \
    "$SCRIPT_DIR/00_制包机_生成离线交付包.sh" \
    "$SCRIPT_DIR/01_制包机_发布预检.sh" \
    "$SCRIPT_DIR/02_目标终端_安装并验证.sh" \
    "$SCRIPT_DIR/03_目标终端_导出诊断报告.sh" \
    "$SCRIPT_DIR/04_目标终端_桌面App验收并导出证据.sh" \
    "$SCRIPT_DIR/99_本机_准备制包输入包.sh"; do
    [ -f "$script" ] || fail "交付目录缺少脚本：$script"
    chmod 0755 "$script"
  done
  ok "交付目录权限已归一化"
}

cleanup_temporary_build_root() {
  info "清理最终预检使用完毕的临时构建工作区"
  if [ -e "$BUILD_ROOT" ] || [ -L "$BUILD_ROOT" ]; then
    require_owned_build_root
    restore_owned_build_root_directory_writes
    rm -rf -- "$BUILD_ROOT" || fail "无法以当前用户清理专用构建工作区：$BUILD_ROOT"
  fi
  [ ! -e "$BUILD_ROOT" ] && [ ! -L "$BUILD_ROOT" ] \
    || fail "临时构建工作区仍然存在：$BUILD_ROOT"
  ok "临时构建工作区已清理"
}

apt_source_summary() {
  awk '
    /^[[:space:]]*deb[[:space:]]/ {
      out = out $0 "; "
      count += 1
      if (count >= 5) {
        print out
        exit
      }
    }
    END {
      if (count > 0 && count < 5) {
        print out
      }
    }
  ' /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null || true
}

main() {
  initialize_build_logging
  set_stage "制包机预检"
  preflight
  set_stage "安装制包依赖"
  install_build_dependencies
  set_stage "源码包发布预检"
  prepare_source_release
  set_stage "准备 Python/Node/Electron 构建工具"
  ensure_uv
  ensure_node
  set_stage "解压源码"
  unpack_source
  set_stage "构建运行时和 DEB"
  build_runtime_and_deb
  set_stage "收集安装包产物"
  collect_artifacts
  set_stage "生成离线依赖仓库"
  build_offline_dependency_repo
  set_stage "生成 manifest 和报告"
  write_release_manifest
  write_build_report
  set_stage "收集目标终端桌面 App 验收工具"
  stage_target_acceptance_tools
  set_stage "清理制包缓存"
  cleanup_delivery_build_cache
  set_stage "归一化交付权限"
  normalize_delivery_permissions
  set_stage "最终发布预检"
  TAIJI_RELEASE_REQUIRE_ARTIFACTS=1 TAIJI_RELEASE_SKIP_GIT_CHECK=1 run_release_preflight "$SRC_DIR"
  set_stage "清理临时构建工作区"
  cleanup_temporary_build_root
  printf '\n[OK] 离线交付包生成完成。目标机断网后执行：\n'
  printf 'bash ./02_目标终端_安装并验证.sh\n'
  printf '\n日志：%s\n' "$LOG_FILE"
}

main "$@"
