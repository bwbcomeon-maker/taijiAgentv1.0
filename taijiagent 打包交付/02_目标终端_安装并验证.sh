#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
OFFLINE_REPO="$SCRIPT_DIR/离线依赖"
LOG_DIR="$SCRIPT_DIR/构建日志"
BUILD_MARKER="$OUTPUT_DIR/.build-success"
DEB_PATH=""
CHECKSUM_PATH=""
MANIFEST_PATH=""
ROOT_INSTALL_STAGING=""
STAGED_BUILD_MARKER=""
STAGED_DEB_PATH=""
STAGED_CHECKSUM_PATH=""
STAGED_MANIFEST_PATH=""
OFFLINE_APT_REPO_SOURCE=""
OFFLINE_APT_SOURCE_FILE=""
OFFLINE_APT_LISTS_DIR=""
EXPECTED_DEB_NAME=""
EXPECTED_CHECKSUM_NAME=""
EXPECTED_MANIFEST_NAME=""
EXPECTED_DEB_SHA256=""
EXPECTED_PACKAGES_SHA256=""
EXPECTED_PACKAGES_GZ_SHA256=""
ONLINE_OK="${ONLINE_OK:-0}"
TAIJI_ALLOW_HEADLESS_REHEARSAL="${TAIJI_ALLOW_HEADLESS_REHEARSAL:-0}"

LEGACY_SERVICES=(
  "taiji-agent-webui.service"
  "taiji-agent-gateway.service"
)

LEGACY_PREFIX="her""mes"
LEGACY_WEBUI_DIR="${LEGACY_PREFIX}-webui"
LEGACY_AGENT_DIR="${LEGACY_PREFIX}-agent"
LEGACY_CLI_MODULE="${LEGACY_PREFIX}_cli.main"

LEGACY_PROCESS_PATTERNS=(
  "/opt/taiji-agent"
  "/opt/taiji-agent/src/${LEGACY_WEBUI_DIR}/bootstrap.py"
  "/opt/taiji-agent/src/${LEGACY_WEBUI_DIR}/server.py"
  "/opt/taiji-agent/.*/${LEGACY_WEBUI_DIR}/server.py"
  "/opt/taiji-agent/src/${LEGACY_AGENT_DIR}/venv/bin/python -m ${LEGACY_CLI_MODULE} gateway"
  "/opt/taiji-agent/.*/${LEGACY_PREFIX} gateway run"
  "/opt/taiji-agent/apps/taiji-desktop"
)

CONFLICT_PORTS=(8787 18642 18787)

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/02_install_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
FAILURE_REPORTED=0
CURRENT_STAGE="初始化"

ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }
set_stage() { CURRENT_STAGE="$1"; info "阶段：$CURRENT_STAGE"; }

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
    for cmd in sudo apt-get dpkg dpkg-query apt-mark sha256sum mktemp systemctl pgrep ps lsof taiji taiji-agent; do
      printf 'cmd.%s=%s\n' "$cmd" "$(safe_cmd_path "$cmd")"
    done
    printf 'ONLINE_OK=%s\n' "$ONLINE_OK"
    printf 'TAIJI_ALLOW_HEADLESS_REHEARSAL=%s\n' "$TAIJI_ALLOW_HEADLESS_REHEARSAL"
    printf 'DISPLAY=%s\n' "${DISPLAY:-}"
    printf 'WAYLAND_DISPLAY=%s\n' "${WAYLAND_DISPLAY:-}"
    printf '\n## 交付产物\n'
    find "$SCRIPT_DIR" -maxdepth 2 \( -name '.build-success' -o -name 'taiji-package-manifest.json' -o -name 'taiji-agent_*_amd64.deb' -o -name 'taiji-agent_*_amd64.deb.sha256' -o -name 'Packages' -o -name 'Packages.gz' -o -name 'SHA256SUMS.txt' \) -print 2>/dev/null | sort
    printf '\n## 包和服务状态\n'
    dpkg-query -W -f='${db:Status-Abbrev} ${Package} ${Version}\n' taiji-agent 2>/dev/null || true
    systemctl status taiji-agent-webui.service --no-pager 2>/dev/null | sed -n '1,60p' || true
    systemctl status taiji-agent-gateway.service --no-pager 2>/dev/null | sed -n '1,60p' || true
    printf '\n## 端口占用\n'
    for port in "${CONFLICT_PORTS[@]}"; do
      lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
    done
    printf '\n## 最新日志\n'
    [ -f "$LOG_FILE" ] && tail -n 180 "$LOG_FILE"
  } >> "$out" 2>&1 || true
}

failure_next_steps() {
  local reason="${1:-}"
  case "$reason" in
    *"只能在 Linux"*|*"不是 x86_64/amd64"*|*"缺少 apt-get"*|*"缺少 dpkg"*)
      printf 'next=当前目标机不在 DEB 支持矩阵内；需要 Linux x86_64/amd64 + apt/dpkg + 图形桌面，RPM/.run 需单独制品\n'
      ;;
    *"离线 apt 仓库索引"*|*"无法下载 file:"*|*"无法找到文件"*"Packages"*)
      printf 'next=离线 apt 仓库索引不可读。新版安装脚本会在 root 所有的 /var/tmp staging 中同时准备 Packages 和 Packages.gz；请使用最新交付目录重试\n'
      ;;
    *"管理员权限"*|*"sudo"*)
      printf 'next=先执行 sudo -v，确认当前用户具备管理员权限后重试安装脚本\n'
      ;;
    *"缺少离线依赖仓库"*)
      printf 'next=完全离线安装必须同时包含 离线依赖/Packages 与 Packages.gz；若明确允许在线源，设置 ONLINE_OK=1 后重试但不能算离线验收\n'
      ;;
    *"构建成功标记"*|*"manifest"*|*"安装包与构建成功标记"*|*"Packages.gz"*)
      printf 'next=回到制包机重新执行 bash ./00_制包机_生成离线交付包.sh，并完整拷贝 生成的安装包/ 与 离线依赖/\n'
      ;;
    *"旧版后台服务仍"*|*"旧包状态仍存在"*)
      printf 'next=查看诊断中的 systemctl/dpkg 状态，先清理旧 taiji-agent 包状态后重试\n'
      ;;
    *"TAIJI_ALLOW_HEADLESS_REHEARSAL=1"*)
      printf 'next=请在图形桌面终端中重跑脚本；只有不带桌面验收的离线安装演练才能显式设置 TAIJI_ALLOW_HEADLESS_REHEARSAL=1\n'
      ;;
    *"taiji-native-verify"*|*"taiji 命令"*|*"taiji-agent 桌面启动"*)
      printf 'next=安装已进入运行态验证阶段，优先查看 /opt/taiji-agent 与诊断报告定位缺失运行时或入口\n'
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
    printf '太极 Agent 目标机安装失败诊断\n'
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
  printf '\n[FAIL] 安装验证中断，请查看日志：%s\n' "$LOG_FILE" >&2
  exit "$code"
}

require_cmd() { have "$1" || fail "缺少命令：$1"; }

cleanup_offline_apt_repo_mount() {
  if [ -n "${ROOT_INSTALL_STAGING:-}" ]; then
    sudo rm -rf -- "$ROOT_INSTALL_STAGING" >/dev/null 2>&1 || true
    ROOT_INSTALL_STAGING=""
  fi
}

trap cleanup_offline_apt_repo_mount EXIT
trap 'on_error "$?" "$BASH_COMMAND"' ERR

require_admin_capability() {
  require_cmd sudo
  if sudo -n true >/dev/null 2>&1; then
    ok "管理员权限预检通过：sudo 已可用"
    return
  fi
  info "需要管理员权限预检。这里可能需要输入 sudo 密码。"
  sudo -v || fail "管理员权限预检失败：当前用户不能执行 sudo，无法安装太极 Agent"
  ok "管理员权限预检通过"
}

marker_value() {
  local key="${1:-}" marker_path="${2:-$BUILD_MARKER}"
  [ -n "$key" ] || return 1
  awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }' "$marker_path"
}

json_string_value() {
  local key="$1" path="$2"
  sed -nE 's/^[[:space:]]*"'"$key"'"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p' "$path" | head -1
}

validate_release_manifest() {
  local deb_name checksum_name expected_sha manifest_name marker_packages_sha marker_packages_gz_sha
  local manifest_deb manifest_checksum manifest_deb_sha manifest_packages_sha manifest_packages_gz_sha
  local actual_packages_sha actual_packages_gz_sha
  deb_name="$(marker_value deb)"
  checksum_name="$(marker_value checksum)"
  expected_sha="$(marker_value deb_sha256)"
  manifest_name="$(marker_value manifest)"
  marker_packages_sha="$(marker_value packages_sha256)"
  marker_packages_gz_sha="$(marker_value packages_gz_sha256)"

  [ -n "$manifest_name" ] || fail "构建成功标记缺少 manifest 字段，请重新执行制包脚本"
  [ -n "$marker_packages_sha" ] || fail "构建成功标记缺少 packages_sha256 字段，请重新执行制包脚本"
  [ -n "$marker_packages_gz_sha" ] || fail "构建成功标记缺少 packages_gz_sha256 字段，请重新执行制包脚本"
  MANIFEST_PATH="$OUTPUT_DIR/$manifest_name"
  [ -f "$MANIFEST_PATH" ] || fail "构建成功标记指向的 manifest 不存在：$MANIFEST_PATH"

  manifest_deb="$(json_string_value deb "$MANIFEST_PATH")"
  manifest_checksum="$(json_string_value checksum "$MANIFEST_PATH")"
  manifest_deb_sha="$(json_string_value deb_sha256 "$MANIFEST_PATH")"
  manifest_packages_sha="$(json_string_value packages_sha256 "$MANIFEST_PATH")"
  manifest_packages_gz_sha="$(json_string_value packages_gz_sha256 "$MANIFEST_PATH")"
  [ "$manifest_deb" = "$deb_name" ] || fail "manifest 与构建标记的 DEB 名称不一致"
  [ "$manifest_checksum" = "$checksum_name" ] || fail "manifest 与构建标记的校验文件名称不一致"
  [ "$manifest_deb_sha" = "$expected_sha" ] || fail "manifest 与构建标记的 DEB SHA256 不一致"
  [ -n "$manifest_packages_sha" ] || fail "manifest 缺少 packages_sha256 字段"
  [ -n "$manifest_packages_gz_sha" ] || fail "manifest 缺少 packages_gz_sha256 字段"
  [ "$manifest_packages_sha" = "$marker_packages_sha" ] || fail "manifest 与构建标记的 Packages SHA256 不一致"
  [ "$manifest_packages_gz_sha" = "$marker_packages_gz_sha" ] || fail "manifest 与构建标记的 Packages.gz SHA256 不一致"

  if offline_repo_available; then
    actual_packages_sha="$(sha256sum "$OFFLINE_REPO/Packages" | awk '{print $1}')"
    actual_packages_gz_sha="$(sha256sum "$OFFLINE_REPO/Packages.gz" | awk '{print $1}')"
    [ "$actual_packages_sha" = "$manifest_packages_sha" ] || fail "离线依赖/Packages 与 manifest 不匹配"
    [ "$actual_packages_gz_sha" = "$manifest_packages_gz_sha" ] || fail "离线依赖/Packages.gz 与 manifest 不匹配"
  fi
  EXPECTED_MANIFEST_NAME="$manifest_name"
  EXPECTED_PACKAGES_SHA256="$manifest_packages_sha"
  EXPECTED_PACKAGES_GZ_SHA256="$manifest_packages_gz_sha"
  ok "发布 manifest 有效：$manifest_name"
}

validate_build_marker() {
  [ -f "$BUILD_MARKER" ] || fail "未找到构建成功标记，请先在制包机执行并确认成功：bash ./00_制包机_生成离线交付包.sh"

  deb_name="$(marker_value deb)"
  checksum_name="$(marker_value checksum)"
  expected_sha="$(marker_value deb_sha256)"
  [ -n "$deb_name" ] || fail "构建成功标记缺少 deb 字段，请重新执行构建脚本"
  [ -n "$checksum_name" ] || fail "构建成功标记缺少 checksum 字段，请重新执行构建脚本"
  [ -n "$expected_sha" ] || fail "构建成功标记缺少 deb_sha256 字段，请重新执行构建脚本"

  DEB_PATH="$OUTPUT_DIR/$deb_name"
  CHECKSUM_PATH="$OUTPUT_DIR/$checksum_name"
  [ -f "$DEB_PATH" ] || fail "构建成功标记指向的安装包不存在：$DEB_PATH"
  [ -f "$CHECKSUM_PATH" ] || fail "构建成功标记指向的校验文件不存在：$CHECKSUM_PATH"

  actual_sha="$(sha256sum "$DEB_PATH" | awk '{print $1}')"
  [ "$actual_sha" = "$expected_sha" ] || fail "安装包与构建成功标记不匹配，请重新执行构建脚本"
  validate_release_manifest
  EXPECTED_DEB_NAME="$deb_name"
  EXPECTED_CHECKSUM_NAME="$checksum_name"
  EXPECTED_DEB_SHA256="$expected_sha"
  ok "构建成功标记有效：$deb_name"
}

verify_deb_checksum() {
  local actual_sha checksum_file_sha checksum_file_target
  [ -n "${DEB_PATH:-}" ] || fail "内部错误：DEB_PATH 未设置"
  [ -n "${EXPECTED_DEB_SHA256:-}" ] || fail "内部错误：EXPECTED_DEB_SHA256 未设置"
  actual_sha="$(sha256sum "$DEB_PATH" | awk '{print $1}')"
  [ "$actual_sha" = "$EXPECTED_DEB_SHA256" ] || fail "安装包 SHA256 不匹配：$(basename "$DEB_PATH")"

  if [ -f "$CHECKSUM_PATH" ]; then
    checksum_file_sha="$(awk 'NR == 1 { print $1; exit }' "$CHECKSUM_PATH")"
    checksum_file_target="$(awk 'NR == 1 { $1 = ""; sub(/^[ \t]+\*?/, ""); print; exit }' "$CHECKSUM_PATH")"
    if [ -n "${checksum_file_sha:-}" ] && [ "$checksum_file_sha" != "$EXPECTED_DEB_SHA256" ]; then
      fail "校验文件 SHA256 与构建成功标记不一致：$(basename "$CHECKSUM_PATH")"
    fi
    if [ -n "${checksum_file_target:-}" ] && [ "$(basename "$checksum_file_target")" != "$(basename "$DEB_PATH")" ]; then
      fail "校验文件指向的安装包名称不一致：$checksum_file_target"
    fi
  fi
  ok "安装包 SHA256 校验通过：$(basename "$DEB_PATH")"
}

preflight() {
  [ "$(uname -s)" = "Linux" ] || fail "只能在 Linux 目标终端安装，当前为：$(uname -s)"
  case "$(uname -m)" in
    x86_64|amd64) ok "CPU 架构符合：$(uname -m)" ;;
    *) fail "当前 CPU 架构不是 x86_64/amd64：$(uname -m)" ;;
  esac
  have apt-get || fail "缺少 apt-get"
  have dpkg || fail "缺少 dpkg"
  have sha256sum || fail "缺少 sha256sum"
  have gzip || fail "缺少 gzip"
  require_cmd mktemp
  require_cmd sudo
  require_cmd systemctl
  require_cmd pgrep
  require_cmd ps
  require_cmd lsof
  require_admin_capability
}

dpkg_has_taiji_state() {
  dpkg-query -W -f='${db:Status-Abbrev}' taiji-agent >/dev/null 2>&1
}

path_exists() {
  [ -e "$1" ] || [ -L "$1" ]
}

service_has_legacy_state() {
  local svc="$1"
  systemctl is-active "$svc" >/dev/null 2>&1 && return 0
  systemctl is-enabled "$svc" >/dev/null 2>&1 && return 0
  systemctl list-unit-files "$svc" --no-legend 2>/dev/null | grep -q "$svc" && return 0
  systemctl status "$svc" --no-pager >/dev/null 2>&1 && return 0
  return 1
}

launcher_owned_by_taiji() {
  local path="$1" target
  path_exists "$path" || return 1
  if [ -L "$path" ]; then
    target="$(readlink "$path" 2>/dev/null || true)"
    case "$target" in
      *"/opt/taiji-agent"*) return 0 ;;
      *) return 1 ;;
    esac
  fi
  grep -q '/opt/taiji-agent' "$path" 2>/dev/null
}

legacy_installation_detected() {
  dpkg_has_taiji_state && return 0
  path_exists /opt/taiji-agent && return 0
  path_exists /etc/default/taiji-agent && return 0
  launcher_owned_by_taiji /usr/bin/taiji && return 0
  launcher_owned_by_taiji /usr/bin/taiji-agent && return 0
  local svc
  for svc in "${LEGACY_SERVICES[@]}"; do
    service_has_legacy_state "$svc" && return 0
  done
  return 1
}

is_whitelisted_legacy_path() {
  case "$1" in
    /opt/taiji-agent|\
    /etc/default/taiji-agent|\
    /etc/sysconfig/taiji-agent|\
    /usr/bin/taiji|\
    /usr/bin/taiji-agent|\
    /usr/local/bin/taiji|\
    /usr/share/applications/taiji-agent.desktop|\
    /usr/share/icons/hicolor/512x512/apps/taiji-agent.png|\
    /lib/systemd/system/taiji-agent-webui.service|\
    /lib/systemd/system/taiji-agent-gateway.service|\
    /usr/lib/systemd/system/taiji-agent-webui.service|\
    /usr/lib/systemd/system/taiji-agent-gateway.service|\
    /etc/systemd/system/taiji-agent-webui.service|\
    /etc/systemd/system/taiji-agent-gateway.service|\
    /etc/systemd/system/multi-user.target.wants/taiji-agent-webui.service|\
    /etc/systemd/system/multi-user.target.wants/taiji-agent-gateway.service)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

remove_legacy_path() {
  local path="$1"
  is_whitelisted_legacy_path "$path" || fail "拒绝删除非白名单路径：$path"
  path_exists "$path" || return 0
  info "清理旧版路径：$path"
  sudo rm -rf -- "$path"
}

remove_taiji_launcher_if_owned() {
  local path="$1"
  launcher_owned_by_taiji "$path" || return 0
  remove_legacy_path "$path"
}

stop_and_disable_legacy_services() {
  info "停止并禁用旧版后台服务"
  local svc
  for svc in "${LEGACY_SERVICES[@]}"; do
    sudo systemctl stop "$svc" >/dev/null 2>&1 || true
    sudo systemctl disable "$svc" >/dev/null 2>&1 || true
    sudo systemctl reset-failed "$svc" >/dev/null 2>&1 || true
  done
}

stop_legacy_processes() {
  info "清理旧版 /opt/taiji-agent 相关进程"
  local pattern pids pid
  for pattern in "${LEGACY_PROCESS_PATTERNS[@]}"; do
    pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
    [ -n "$pids" ] || continue
    for pid in $pids; do
      [ "$pid" != "$$" ] || continue
      warn "停止旧版进程 pid=$pid pattern=$pattern"
      sudo kill "$pid" >/dev/null 2>&1 || true
    done
  done

  sleep 1

  for pattern in "${LEGACY_PROCESS_PATTERNS[@]}"; do
    pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
    [ -n "$pids" ] || continue
    for pid in $pids; do
      [ "$pid" != "$$" ] || continue
      warn "强制停止旧版进程 pid=$pid pattern=$pattern"
      sudo kill -9 "$pid" >/dev/null 2>&1 || true
    done
  done
}

purge_legacy_package_state() {
  dpkg_has_taiji_state || return 0
  info "清理旧版 DEB 包管理状态：taiji-agent"
  sudo apt-mark unhold taiji-agent >/dev/null 2>&1 || true
  if ! sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent; then
    warn "apt-get purge taiji-agent 失败，将继续用 dpkg 强制清理旧包状态。"
  fi
  if dpkg_has_taiji_state; then
    sudo dpkg --remove --force-remove-reinstreq taiji-agent || true
    sudo dpkg --purge --force-all taiji-agent || true
  fi
  if dpkg_has_taiji_state; then
    fail "taiji-agent 旧包状态仍存在，已停止安装，避免新旧包状态混在一起。"
  fi
}

remove_legacy_files() {
  info "清理旧版系统入口和安装目录"
  remove_taiji_launcher_if_owned /usr/bin/taiji
  remove_taiji_launcher_if_owned /usr/bin/taiji-agent
  remove_taiji_launcher_if_owned /usr/local/bin/taiji
  remove_legacy_path /etc/default/taiji-agent
  remove_legacy_path /etc/sysconfig/taiji-agent
  remove_legacy_path /etc/systemd/system/multi-user.target.wants/taiji-agent-webui.service
  remove_legacy_path /etc/systemd/system/multi-user.target.wants/taiji-agent-gateway.service
  remove_legacy_path /etc/systemd/system/taiji-agent-webui.service
  remove_legacy_path /etc/systemd/system/taiji-agent-gateway.service
  remove_legacy_path /lib/systemd/system/taiji-agent-webui.service
  remove_legacy_path /lib/systemd/system/taiji-agent-gateway.service
  remove_legacy_path /usr/lib/systemd/system/taiji-agent-webui.service
  remove_legacy_path /usr/lib/systemd/system/taiji-agent-gateway.service
  remove_legacy_path /usr/share/applications/taiji-agent.desktop
  remove_legacy_path /usr/share/icons/hicolor/512x512/apps/taiji-agent.png
  remove_legacy_path /opt/taiji-agent
}

pid_uses_taiji_install_root() {
  local pid="$1" cmdline
  cmdline="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  case "$cmdline" in
    *"/opt/taiji-agent"*) return 0 ;;
    *) return 1 ;;
  esac
}

check_port_conflict() {
  local phase="$1" port pids pid offenders cmdline
  for port in "${CONFLICT_PORTS[@]}"; do
    pids="$(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true)"
    [ -n "$pids" ] || continue
    offenders=""
    for pid in $pids; do
      if pid_uses_taiji_install_root "$pid"; then
        if [ "$phase" = "安装前" ]; then
          warn "${phase}：端口 $port 被旧版太极 Agent 进程占用，稍后会自动清理。"
        else
          cmdline="$(ps -p "$pid" -o args= 2>/dev/null || true)"
          offenders="${offenders}pid=$pid $cmdline"$'\n'
        fi
      else
        cmdline="$(ps -p "$pid" -o args= 2>/dev/null || true)"
        offenders="${offenders}pid=$pid $cmdline"$'\n'
      fi
    done
    if [ -n "$offenders" ]; then
      printf '%s' "$offenders" >&2
      warn "${phase}：端口 $port 被非预期进程占用。新版桌面端会自动选择空闲端口，安装继续；请在诊断报告中保留该信息。"
    fi
  done
  return 0
}

verify_legacy_services_inactive() {
  local svc
  for svc in "${LEGACY_SERVICES[@]}"; do
    if systemctl is-active "$svc" >/dev/null 2>&1; then
      fail "旧版后台服务仍在运行：$svc"
    fi
    if systemctl is-enabled "$svc" >/dev/null 2>&1; then
      fail "旧版后台服务仍处于开机启用状态：$svc"
    fi
  done
}

clean_reinstall_legacy_package() {
  stop_and_disable_legacy_services
  stop_legacy_processes
  purge_legacy_package_state
  remove_legacy_files
  sudo systemctl daemon-reload >/dev/null 2>&1 || true
}

prepare_legacy_replacement() {
  check_port_conflict "安装前" || fail "安装前端口检查未通过，未执行旧版替换。"
  if ! legacy_installation_detected; then
    ok "未检测到旧版 taiji-agent WebUI 安装残留"
    return 0
  fi

  warn "检测到旧版 taiji-agent WebUI/后台服务安装，将彻底清除旧系统安装后再安装新版。"
  warn "旧版 /opt/taiji-agent、系统配置、旧服务和旧入口会被删除；旧模型 Key、微信 token 和历史会话不会备份。"
  clean_reinstall_legacy_package
  check_port_conflict "安装前清理后" || fail "安装前清理后端口检查未通过，已停止安装。"
  verify_legacy_services_inactive
  ok "旧版 taiji-agent 已彻底清理完成"
}

install_trial_license() {
  local source="${TAIJI_LICENSE_SOURCE:-}" candidates=()
  if [ -n "$source" ] && [ ! -f "$source" ]; then
    fail "指定的授权文件不存在：$source"
  fi
  if [ -z "$source" ] && [ -f "$SCRIPT_DIR/license.jwt" ]; then
    source="$SCRIPT_DIR/license.jwt"
  fi
  if [ -z "$source" ]; then
    shopt -s nullglob
    candidates=("$SCRIPT_DIR"/taiji-license-*.jwt)
    shopt -u nullglob
    if [ "${#candidates[@]}" -eq 1 ]; then
      source="${candidates[0]}"
    elif [ "${#candidates[@]}" -gt 1 ]; then
      printf '[FAIL] 检测到多个候选授权文件，请设置 TAIJI_LICENSE_SOURCE 指定其中一个：\n' >&2
      printf '  %s\n' "${candidates[@]}" >&2
      fail "检测到多个候选授权文件，请设置 TAIJI_LICENSE_SOURCE 指定其中一个"
    fi
  fi
  if [ -z "$source" ]; then
    warn "未发现预置授权文件 license.jwt 或 taiji-license-*.jwt；应用可安装，但试用授权状态会显示为缺失。"
    return 0
  fi

  local account_home="$HOME"
  local detected_home=""
  if command -v getent >/dev/null 2>&1; then
    detected_home="$(getent passwd "$(id -u)" 2>/dev/null | awk -F: 'NR==1 {print $6}')"
    if [ -n "$detected_home" ]; then
      account_home="$detected_home"
    fi
  fi
  local config_home="$account_home/.config"
  local target_dir="$config_home/taiji-agent/licenses"
  local target="$target_dir/active-license.jwt"
  mkdir -p "$target_dir"
  chmod 0700 "$target_dir" || true
  install -m 0600 "$source" "$target"
  ok "已安装试用授权：$(basename "$source") -> licenses/active-license.jwt"
}

offline_repo_available() {
  [ -d "$OFFLINE_REPO" ] && [ -f "$OFFLINE_REPO/Packages" ] && [ -f "$OFFLINE_REPO/Packages.gz" ]
}

validate_offline_repo_requirement() {
  if offline_repo_available; then
    ok "完全离线发布包依赖仓库有效：$OFFLINE_REPO/Packages.gz"
    return 0
  fi
  if [ "$ONLINE_OK" = "1" ]; then
    warn "ONLINE_OK=1：未检测到离线依赖仓库，将显式允许使用目标机系统已配置软件源安装。"
    return 0
  fi
  fail "缺少离线依赖仓库：完全离线发布包必须同时包含 $OFFLINE_REPO/Packages 与 Packages.gz；如明确允许目标机在线源，请设置 ONLINE_OK=1 后重试。"
}

staged_offline_repo_available() {
  [ -n "${OFFLINE_APT_REPO_SOURCE:-}" ] && \
    [ -f "$OFFLINE_APT_REPO_SOURCE/Packages" ] && \
    [ -f "$OFFLINE_APT_REPO_SOURCE/Packages.gz" ]
}

verify_staged_install_inputs() {
  local marker_deb marker_checksum marker_manifest marker_deb_sha marker_packages_sha marker_packages_gz_sha
  local manifest_deb manifest_checksum manifest_deb_sha manifest_packages_sha manifest_packages_gz_sha
  local checksum_file_sha checksum_file_target actual_sha

  marker_deb="$(marker_value deb "$STAGED_BUILD_MARKER")"
  marker_checksum="$(marker_value checksum "$STAGED_BUILD_MARKER")"
  marker_manifest="$(marker_value manifest "$STAGED_BUILD_MARKER")"
  marker_deb_sha="$(marker_value deb_sha256 "$STAGED_BUILD_MARKER")"
  marker_packages_sha="$(marker_value packages_sha256 "$STAGED_BUILD_MARKER")"
  marker_packages_gz_sha="$(marker_value packages_gz_sha256 "$STAGED_BUILD_MARKER")"

  [ "$marker_deb" = "$EXPECTED_DEB_NAME" ] || fail "root staging 中的 DEB 名称与已校验构建标记不一致"
  [ "$marker_checksum" = "$EXPECTED_CHECKSUM_NAME" ] || fail "root staging 中的校验文件名称与已校验构建标记不一致"
  [ "$marker_manifest" = "$EXPECTED_MANIFEST_NAME" ] || fail "root staging 中的 manifest 名称与已校验构建标记不一致"
  [ "$marker_deb_sha" = "$EXPECTED_DEB_SHA256" ] || fail "root staging 中的 DEB SHA256 与已校验构建标记不一致"
  [ "$marker_packages_sha" = "$EXPECTED_PACKAGES_SHA256" ] || fail "root staging 中的 Packages SHA256 与已校验构建标记不一致"
  [ "$marker_packages_gz_sha" = "$EXPECTED_PACKAGES_GZ_SHA256" ] || fail "root staging 中的 Packages.gz SHA256 与已校验构建标记不一致"

  manifest_deb="$(json_string_value deb "$STAGED_MANIFEST_PATH")"
  manifest_checksum="$(json_string_value checksum "$STAGED_MANIFEST_PATH")"
  manifest_deb_sha="$(json_string_value deb_sha256 "$STAGED_MANIFEST_PATH")"
  manifest_packages_sha="$(json_string_value packages_sha256 "$STAGED_MANIFEST_PATH")"
  manifest_packages_gz_sha="$(json_string_value packages_gz_sha256 "$STAGED_MANIFEST_PATH")"
  [ "$manifest_deb" = "$EXPECTED_DEB_NAME" ] || fail "root staging 中的 manifest DEB 名称与已校验值不一致"
  [ "$manifest_checksum" = "$EXPECTED_CHECKSUM_NAME" ] || fail "root staging 中的 manifest 校验文件名称与已校验值不一致"
  [ "$manifest_deb_sha" = "$EXPECTED_DEB_SHA256" ] || fail "root staging 中的 manifest DEB SHA256 与已校验值不一致"
  [ "$manifest_packages_sha" = "$EXPECTED_PACKAGES_SHA256" ] || fail "root staging 中的 manifest Packages SHA256 与已校验值不一致"
  [ "$manifest_packages_gz_sha" = "$EXPECTED_PACKAGES_GZ_SHA256" ] || fail "root staging 中的 manifest Packages.gz SHA256 与已校验值不一致"

  actual_sha="$(sha256sum "$STAGED_DEB_PATH" | awk '{print $1}')"
  [ "$actual_sha" = "$EXPECTED_DEB_SHA256" ] || fail "root staging 中 DEB SHA256 重校验失败"
  checksum_file_sha="$(awk 'NR == 1 { print $1; exit }' "$STAGED_CHECKSUM_PATH")"
  checksum_file_target="$(awk 'NR == 1 { $1 = ""; sub(/^[ \t]+\*?/, ""); print; exit }' "$STAGED_CHECKSUM_PATH")"
  [ "$checksum_file_sha" = "$EXPECTED_DEB_SHA256" ] || fail "root staging 中 DEB 校验文件与已校验值不一致"
  [ "$(basename "$checksum_file_target")" = "$EXPECTED_DEB_NAME" ] || fail "root staging 中 DEB 校验文件指向了其他文件"

  if staged_offline_repo_available; then
    actual_sha="$(sha256sum "$OFFLINE_APT_REPO_SOURCE/Packages" | awk '{print $1}')"
    [ "$actual_sha" = "$EXPECTED_PACKAGES_SHA256" ] || fail "root staging 中 Packages SHA256 重校验失败"
    actual_sha="$(sha256sum "$OFFLINE_APT_REPO_SOURCE/Packages.gz" | awk '{print $1}')"
    [ "$actual_sha" = "$EXPECTED_PACKAGES_GZ_SHA256" ] || fail "root staging 中 Packages.gz SHA256 重校验失败"
  fi
  ok "root staging 安装输入 SHA256 重校验通过"
}

stage_privileged_install_inputs() {
  local file
  ROOT_INSTALL_STAGING="$(sudo mktemp -d "/var/tmp/taiji-agent-install.XXXXXX")"
  [ -n "$ROOT_INSTALL_STAGING" ] || fail "无法创建 root 安装 staging 目录"
  sudo chmod 0755 "$ROOT_INSTALL_STAGING"
  sudo install -d -m 0755 "$ROOT_INSTALL_STAGING/package" "$ROOT_INSTALL_STAGING/repo" "$ROOT_INSTALL_STAGING/apt-lists" "$ROOT_INSTALL_STAGING/apt-lists/partial"

  STAGED_BUILD_MARKER="$ROOT_INSTALL_STAGING/package/.build-success"
  STAGED_DEB_PATH="$ROOT_INSTALL_STAGING/package/$(basename "$DEB_PATH")"
  STAGED_CHECKSUM_PATH="$ROOT_INSTALL_STAGING/package/$(basename "$CHECKSUM_PATH")"
  STAGED_MANIFEST_PATH="$ROOT_INSTALL_STAGING/package/$(basename "$MANIFEST_PATH")"
  sudo install -m 0644 "$BUILD_MARKER" "$STAGED_BUILD_MARKER"
  sudo install -m 0644 "$DEB_PATH" "$STAGED_DEB_PATH"
  sudo install -m 0644 "$CHECKSUM_PATH" "$STAGED_CHECKSUM_PATH"
  sudo install -m 0644 "$MANIFEST_PATH" "$STAGED_MANIFEST_PATH"

  if offline_repo_available; then
    while IFS= read -r -d '' file; do
      sudo install -m 0644 "$file" "$ROOT_INSTALL_STAGING/repo/$(basename "$file")"
    done < <(find "$OFFLINE_REPO" -maxdepth 1 -type f -print0)
    OFFLINE_APT_REPO_SOURCE="$ROOT_INSTALL_STAGING/repo"
    OFFLINE_APT_SOURCE_FILE="$ROOT_INSTALL_STAGING/taiji-agent-offline.list"
    OFFLINE_APT_LISTS_DIR="$ROOT_INSTALL_STAGING/apt-lists"
    printf 'deb [trusted=yes] file:%s ./\n' "$OFFLINE_APT_REPO_SOURCE" | sudo tee "$OFFLINE_APT_SOURCE_FILE" >/dev/null
    sudo chmod 0644 "$OFFLINE_APT_SOURCE_FILE"
  fi

  verify_staged_install_inputs
}

install_taiji_package() {
  [ -n "${STAGED_DEB_PATH:-}" ] || fail "内部错误：root staging DEB 未设置"
  if staged_offline_repo_available; then
    info "使用 root staging 离线依赖仓库：$OFFLINE_APT_REPO_SOURCE"
    local source_file lists_dir
    source_file="$ROOT_INSTALL_STAGING/taiji-agent-offline.list"
    lists_dir="$ROOT_INSTALL_STAGING/apt-lists"
    local apt_opts=(
      -o "Dir::Etc::sourcelist=$source_file"
      -o "Dir::Etc::sourceparts=-"
      -o "APT::Get::List-Cleanup=0"
      -o "Dir::State::Lists=$lists_dir"
    )
    # apt-get update is scoped to the local file: source through the options below.
    if ! sudo apt-get "${apt_opts[@]}" update; then
      fail "离线 apt 仓库索引更新失败：apt-get update 无法读取本地 Packages/Packages.gz"
    fi
    sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get \
      "${apt_opts[@]}" \
      install -y --reinstall --allow-downgrades --allow-change-held-packages "$STAGED_DEB_PATH"
    return
  fi

  [ "$ONLINE_OK" = "1" ] || fail "缺少离线依赖仓库：必须同时包含 $OFFLINE_REPO/Packages 与 Packages.gz。完全离线发布包不能回退到目标机在线源。"
  warn "ONLINE_OK=1：使用系统已配置软件源安装；这不是完全离线发布包验收路径。"
  sudo apt-get install -y --reinstall --allow-downgrades --allow-change-held-packages "$STAGED_DEB_PATH"
}

install_package() {
  validate_build_marker

  info "校验安装包"
  verify_deb_checksum
  validate_offline_repo_requirement
  stage_privileged_install_inputs

  prepare_legacy_replacement

  current_version="$(dpkg-query -W -f='${Version}' taiji-agent 2>/dev/null || true)"
  held_packages="$(apt-mark showhold 2>/dev/null | awk '$0 == "taiji-agent" { print; exit }' || true)"
  if [ -n "$current_version" ]; then
    warn "检测到已安装版本：taiji-agent ${current_version}，本脚本将自动用当前生成的安装包覆盖安装。"
  fi
  if [ -n "$held_packages" ]; then
    warn "检测到 taiji-agent 处于 hold 状态，本脚本将允许本次安装替换该包。"
  fi

  info "安装太极 Agent。这里可能需要输入 sudo 密码。"
  install_taiji_package
  install_trial_license
  check_port_conflict "安装后" || fail "安装后端口检查未通过。"
  verify_legacy_services_inactive
}

verify_installation() {
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ] && [ "$TAIJI_ALLOW_HEADLESS_REHEARSAL" != "1" ]; then
    fail "未检测到图形桌面会话；桌面 App/目标机验证必须在 DISPLAY 或 WAYLAND_DISPLAY 可用时执行。如果只做离线安装演练，可显式设置 TAIJI_ALLOW_HEADLESS_REHEARSAL=1。"
  fi

  info "运行安装态诊断"
  [ -x /opt/taiji-agent/bin/taiji-native-verify ] || fail "未找到 /opt/taiji-agent/bin/taiji-native-verify"
  /opt/taiji-agent/bin/taiji-native-verify

  if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    info "运行 Electron 图形 smoke test"
    TAIJI_VERIFY_DESKTOP_SMOKE=1 /opt/taiji-agent/bin/taiji-native-verify
  else
    warn "TAIJI_ALLOW_HEADLESS_REHEARSAL=1：跳过 Electron smoke test，本轮只能记录为离线安装演练。"
  fi

  if have taiji; then
    taiji --help >/dev/null
    ok "taiji 命令可用"
  else
    fail "taiji 命令不可用"
  fi

  if have taiji-agent; then
    ok "taiji-agent 桌面启动命令可用：$(command -v taiji-agent)"
  else
    fail "taiji-agent 桌面启动命令不可用"
  fi
}

main() {
  set_stage "目标机预检"
  preflight
  set_stage "安装太极 Agent"
  install_package
  set_stage "安装后验证"
  verify_installation
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    printf '\n[PARTIAL] 仅离线安装演练，不是桌面 App/目标机验证。\n'
    printf '已执行：安装态非 GUI 诊断和 CLI 入口检查。\n'
    printf '未执行：Electron 桌面 App、真实模型对话和目标机验证：未验证。\n'
  else
    printf '\n[OK] 安装验证命令已执行完毕。\n'
    printf '请从开始菜单搜索并打开：太极 Agent\n'
    printf '打开后先确认首屏，再配置模型并发送一句测试消息。\n'
  fi
  printf '如果页面或功能异常，请执行：bash ./03_目标终端_导出诊断报告.sh\n'
  printf '\n日志：%s\n' "$LOG_FILE"
}

main "$@"
