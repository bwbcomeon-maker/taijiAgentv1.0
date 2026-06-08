#!/usr/bin/env bash
# Stop Hermes Local Lab processes started by start-agent.sh and start-webui.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-env.sh
source "$SCRIPT_DIR/runtime-env.sh"

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
  echo "$name: stopping PID $pid"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 30); do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      echo "$name: stopped"
      return 0
    fi
    sleep 0.2
  done
  echo "$name: PID $pid did not exit after SIGTERM; sending SIGKILL"
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$pid_file"
}

stop_pid_file "Hermes WebUI" "$LOG_DIR/hermes-webui.pid"
stop_pid_file "Hermes Agent" "$LOG_DIR/hermes-agent.pid"
