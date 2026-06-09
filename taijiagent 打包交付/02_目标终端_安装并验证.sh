#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
LOG_DIR="$SCRIPT_DIR/构建日志"
BACKUP_DIR="$SCRIPT_DIR/旧版备份"
BUILD_MARKER="$OUTPUT_DIR/.build-success"
DEB_PATH=""
CHECKSUM_PATH=""

LEGACY_SERVICES=(
  "taiji-agent-webui.service"
  "taiji-agent-gateway.service"
)

LEGACY_BACKUP_PATHS=(
  "/opt/taiji-agent"
  "/etc/default/taiji-agent"
  "/etc/sysconfig/taiji-agent"
  "/lib/systemd/system/taiji-agent-webui.service"
  "/lib/systemd/system/taiji-agent-gateway.service"
  "/usr/lib/systemd/system/taiji-agent-webui.service"
  "/usr/lib/systemd/system/taiji-agent-gateway.service"
  "/etc/systemd/system/taiji-agent-webui.service"
  "/etc/systemd/system/taiji-agent-gateway.service"
)

LEGACY_PROCESS_PATTERNS=(
  "/opt/taiji-agent/src/hermes-webui/bootstrap.py"
  "/opt/taiji-agent/src/hermes-webui/server.py"
  "/opt/taiji-agent/.*/hermes-webui/server.py"
  "/opt/taiji-agent/src/hermes-agent/venv/bin/python -m hermes_cli.main gateway"
  "/opt/taiji-agent/.*/hermes gateway run"
  "/opt/taiji-agent/apps/taiji-desktop"
)

CONFLICT_PORTS=(8787 18642 18787)

mkdir -p "$LOG_DIR" "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/02_install_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
require_cmd() { have "$1" || fail "缺少命令：$1"; }

marker_value() {
  key="${1:-}"
  [ -n "$key" ] || return 1
  awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }' "$BUILD_MARKER"
}

validate_build_marker() {
  [ -f "$BUILD_MARKER" ] || fail "未找到构建成功标记，请先执行并确认成功：bash ./01_目标终端_构建安装包.sh"

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
  ok "构建成功标记有效：$deb_name"
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
  require_cmd sudo
  require_cmd tar
  require_cmd systemctl
  require_cmd pgrep
  require_cmd ps
  require_cmd lsof
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

backup_legacy_installation() {
  local backup_path tmp_backup path rel
  local -a backup_items=()
  backup_path="$BACKUP_DIR/taiji-agent-legacy-$(date +%Y%m%d_%H%M%S).tar.gz"
  tmp_backup="$backup_path.tmp"

  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR" 2>/dev/null || true

  for path in "${LEGACY_BACKUP_PATHS[@]}" /usr/bin/taiji /usr/bin/taiji-agent /usr/share/applications/taiji-agent.desktop; do
    if path_exists "$path"; then
      rel="${path#/}"
      backup_items+=("$rel")
    fi
  done

  if [ "${#backup_items[@]}" -eq 0 ]; then
    warn "未发现可备份的旧版文件，继续清理旧服务和包状态。"
    return 0
  fi

  info "备份旧版运行数据和系统入口。备份包可能包含模型 Key 或微信 token，请勿外发。"
  sudo rm -f "$tmp_backup"
  sudo tar -C / -czf "$tmp_backup" "${backup_items[@]}"
  sudo chmod 600 "$tmp_backup"
  sudo chown "$(id -u):$(id -g)" "$tmp_backup"
  mv "$tmp_backup" "$backup_path"
  chmod 600 "$backup_path"
  ok "旧版备份完成：$backup_path"
}

stop_legacy_services() {
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
  sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent || \
    sudo dpkg --purge --force-all taiji-agent || true
  if dpkg_has_taiji_state; then
    warn "taiji-agent 包管理状态仍存在，后续将由当前 DEB 执行受控覆盖安装。"
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
  sudo systemctl daemon-reload >/dev/null 2>&1 || true
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
        if [ "$phase" = "安装前清理后" ] || [ "$phase" = "安装后" ]; then
          cmdline="$(ps -p "$pid" -o args= 2>/dev/null || true)"
          offenders="${offenders}pid=$pid $cmdline"$'\n'
        else
          warn "${phase}：端口 $port 被旧版太极 Agent 进程占用，稍后会自动清理。"
        fi
      else
        cmdline="$(ps -p "$pid" -o args= 2>/dev/null || true)"
        offenders="${offenders}pid=$pid $cmdline"$'\n'
      fi
    done
    if [ -n "$offenders" ]; then
      printf '%s' "$offenders" >&2
      fail "${phase}：端口 $port 被非预期进程占用。为避免误杀其他软件，已停止安装。"
    fi
  done
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

prepare_legacy_replacement() {
  check_port_conflict "安装前"
  if ! legacy_installation_detected; then
    ok "未检测到旧版 taiji-agent WebUI 安装残留"
    return 0
  fi

  warn "检测到旧版 taiji-agent WebUI/后台服务安装，将先备份再自动替换。"
  backup_legacy_installation
  stop_legacy_services
  stop_legacy_processes
  purge_legacy_package_state
  remove_legacy_files
  check_port_conflict "安装前清理后"
  verify_legacy_services_inactive
  ok "旧版 taiji-agent 已备份并清理完成"
}

install_package() {
  validate_build_marker

  info "校验安装包"
  (cd "$OUTPUT_DIR" && sha256sum -c "$(basename "$CHECKSUM_PATH")")

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
  sudo apt-get install -y --reinstall --allow-downgrades --allow-change-held-packages "$DEB_PATH"
  check_port_conflict "安装后"
  verify_legacy_services_inactive
}

verify_installation() {
  info "运行安装态诊断"
  [ -x /opt/taiji-agent/bin/taiji-native-verify ] || fail "未找到 /opt/taiji-agent/bin/taiji-native-verify"
  /opt/taiji-agent/bin/taiji-native-verify

  if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    info "运行 Electron 图形 smoke test"
    TAIJI_VERIFY_DESKTOP_SMOKE=1 /opt/taiji-agent/bin/taiji-native-verify
  else
    warn "当前不是图形桌面会话，跳过 Electron smoke test。请在桌面终端中重跑本脚本。"
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
  preflight
  install_package
  verify_installation
  printf '\n[OK] 安装验证命令已执行完毕。\n'
  printf '请从开始菜单搜索并打开：太极 Agent\n'
  printf '打开后先确认首屏，再配置模型并发送一句测试消息。\n'
  printf '\n日志：%s\n' "$LOG_FILE"
}

main "$@"
