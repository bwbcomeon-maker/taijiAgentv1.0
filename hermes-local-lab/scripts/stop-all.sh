#!/usr/bin/env bash
# Stop Taiji Agent processes started by the local product launch scripts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Stopping processes must never seed or rewrite the active runtime config.
TAIJI_AGENT_SYNC_PACKAGED_CONFIG=0
# shellcheck source=runtime-env.sh
source "$SCRIPT_DIR/runtime-env.sh"

AGENT_API_PORT="${AGENT_API_PORT:-18642}"
API_SERVER_PORT="${API_SERVER_PORT:-$AGENT_API_PORT}"
TAIJI_WEBUI_PORT="${TAIJI_WEBUI_PORT:-${WEBUI_PORT:-18787}}"

process_command() {
  local pid="$1"
  ps -p "$pid" -o command= 2>/dev/null || true
}

pid_uses_managed_runtime() {
  local pid="$1"
  local cmd
  cmd="$(process_command "$pid")"
  [ -n "$cmd" ] || return 1

  case "$cmd" in
    *"$LAB_DIR"*|*"$AGENT_DIR"*|*"$WEBUI_DIR"*|*/opt/taiji-agent*) ;;
    *) return 1 ;;
  esac

  local legacy_cli_module
  legacy_cli_module="$(printf '%s%s_cli.main' her mes)"
  case "$cmd" in
    *"taiji_runtime.main gateway run"*|*"$legacy_cli_module gateway run"*|*"$WEBUI_DIR/server.py"*|*"/runtime/web/server.py"*|*"/server.py"*)
      return 0
      ;;
  esac
  return 1
}

stop_pid() {
  local name="$1"
  local pid="$2"
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    echo "$name: not running"
    return 0
  fi
  if ! pid_uses_managed_runtime "$pid"; then
    echo "$name: PID $pid not managed by this Taiji runtime"
    return 0
  fi
  echo "$name: stopping PID $pid"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 30); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "$name: stopped"
      return 0
    fi
    sleep 0.2
  done
  echo "$name: PID $pid did not exit after SIGTERM; sending SIGKILL"
  kill -KILL "$pid" 2>/dev/null || true
}

stop_pid_file() {
  local name="$1"
  local pid_file="$2"
  if [ ! -f "$pid_file" ]; then
    echo "$name: no pid file"
    return 0
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    echo "$name: not running"
    rm -f "$pid_file"
    return 0
  fi
  if pid_uses_managed_runtime "$pid"; then
    stop_pid "$name" "$pid"
    rm -f "$pid_file"
  else
    echo "$name: PID $pid not managed by this Taiji runtime"
  fi
}

stop_port_listener() {
  local name="$1"
  local port="$2"
  [ -n "$port" ] || return 0
  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$pids" ] || return 0
  local pid
  for pid in $pids; do
    stop_pid "$name port $port" "$pid"
  done
}

legacy_pid_files=(
  "$LOG_DIR/$(printf '%s%s-webui.pid' her mes)"
  "$LOG_DIR/$(printf '%s%s-agent.pid' her mes)"
)

stop_pid_file "Taiji web service" "$LOG_DIR/web.pid"
stop_pid_file "Taiji Agent" "$LOG_DIR/agent.pid"
stop_pid_file "Taiji legacy web service" "${legacy_pid_files[0]}"
stop_pid_file "Taiji legacy Agent" "${legacy_pid_files[1]}"

stop_port_listener "Taiji Agent" "$API_SERVER_PORT"
stop_port_listener "Taiji web service" "$TAIJI_WEBUI_PORT"
