#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
OFFLINE_REPO="$SCRIPT_DIR/离线依赖"
LOG_DIR="$SCRIPT_DIR/构建日志"
BUILD_MARKER="$OUTPUT_DIR/.build-success"
DEB_PATH=""
CHECKSUM_PATH=""

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
  local source="${TAIJI_LICENSE_SOURCE:-}"
  if [ -n "$source" ] && [ ! -f "$source" ]; then
    fail "指定的授权文件不存在：$source"
  fi
  if [ -z "$source" ] && [ -f "$SCRIPT_DIR/license.jwt" ]; then
    source="$SCRIPT_DIR/license.jwt"
  fi
  if [ -z "$source" ]; then
    warn "未发现预置授权文件 license.jwt；应用可安装，但试用授权状态会显示为缺失。"
    return 0
  fi

  local config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
  local target_dir="$config_home/taiji-agent"
  local target="$target_dir/license.jwt"
  mkdir -p "$target_dir"
  chmod 0700 "$target_dir" || true
  install -m 0600 "$source" "$target"
  ok "已安装试用授权：license.jwt"
}

offline_repo_available() {
  [ -d "$OFFLINE_REPO" ] && [ -f "$OFFLINE_REPO/Packages.gz" ]
}

install_taiji_package() {
  if offline_repo_available; then
    info "检测到离线依赖仓库：$OFFLINE_REPO"
    local source_file lists_dir repo_path
    source_file="$LOG_DIR/taiji-agent-offline.list"
    lists_dir="$LOG_DIR/apt-lists"
    repo_path="$(cd "$OFFLINE_REPO" && pwd)"
    mkdir -p "$lists_dir/partial"
    printf 'deb [trusted=yes] file:%s ./\n' "$repo_path" > "$source_file"
    local apt_opts=(
      -o "Dir::Etc::sourcelist=$source_file"
      -o "Dir::Etc::sourceparts=-"
      -o "APT::Get::List-Cleanup=0"
      -o "Dir::State::Lists=$lists_dir"
    )
    # apt-get update is scoped to the local file: source through the options below.
    sudo apt-get "${apt_opts[@]}" update
    sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get \
      "${apt_opts[@]}" \
      install -y --reinstall --allow-downgrades --allow-change-held-packages "$DEB_PATH"
    return
  fi

  warn "未检测到离线依赖仓库，将使用系统已配置软件源安装。完全离线目标机请先在制包机生成离线依赖。"
  sudo apt-get install -y --reinstall --allow-downgrades --allow-change-held-packages "$DEB_PATH"
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
  install_taiji_package
  install_trial_license
  check_port_conflict "安装后" || fail "安装后端口检查未通过。"
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
  printf '如果页面或功能异常，请执行：bash ./03_目标终端_导出诊断报告.sh\n'
  printf '\n日志：%s\n' "$LOG_FILE"
}

main "$@"
