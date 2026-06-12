#!/usr/bin/env bash
# Start the Taiji Agent local web service.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-env.sh
source "$SCRIPT_DIR/runtime-env.sh"
PID_FILE="$LOG_DIR/web.pid"
LOG_FILE="$LOG_DIR/web.log"
START_TIMEOUT_SECONDS="${TAIJI_WEBUI_START_TIMEOUT:-60}"
START_POLL_INTERVAL="${TAIJI_WEBUI_START_POLL_INTERVAL:-0.5}"

case "$START_TIMEOUT_SECONDS" in
  ""|*[!0-9]*) START_TIMEOUT_SECONDS=60 ;;
esac
if [ "$START_TIMEOUT_SECONDS" -lt 1 ]; then
  START_TIMEOUT_SECONDS=60
fi

log_start_line=0
if [ -f "$LOG_FILE" ]; then
  log_start_line="$(wc -l < "$LOG_FILE" | tr -d ' ')"
fi

tail_recent_log() {
  if [ ! -f "$LOG_FILE" ]; then
    return 0
  fi
  local start_line
  local total_lines
  start_line=$((log_start_line + 1))
  total_lines="$(wc -l < "$LOG_FILE" | tr -d ' ')"
  if [ "$total_lines" -ge "$start_line" ]; then
    sed -n "${start_line},\$p" "$LOG_FILE" | tail -100
  else
    tail -100 "$LOG_FILE"
  fi
}

AGENT_API_HOST="${AGENT_API_HOST:-127.0.0.1}"
AGENT_API_PORT="${AGENT_API_PORT:-18642}"
TAIJI_WEBUI_HOST="${TAIJI_WEBUI_HOST:-${WEBUI_HOST:-127.0.0.1}}"
TAIJI_WEBUI_PORT="${TAIJI_WEBUI_PORT:-${WEBUI_PORT:-18787}}"
TAIJI_WEBUI_STATE_DIR="${TAIJI_WEBUI_STATE_DIR:-$TAIJI_RUNTIME_HOME/web}"
TAIJI_WEBUI_DEFAULT_WORKSPACE="${TAIJI_WEBUI_DEFAULT_WORKSPACE:-$TAIJI_WORKSPACE}"
TAIJI_WEBUI_AGENT_DIR="${TAIJI_WEBUI_AGENT_DIR:-$AGENT_DIR}"
TAIJI_WEBUI_PYTHON="${TAIJI_WEBUI_PYTHON:-$AGENT_DIR/venv/bin/python}"
TAIJI_WEBUI_CHAT_BACKEND="${TAIJI_WEBUI_CHAT_BACKEND:-gateway}"
TAIJI_WEBUI_GATEWAY_BASE_URL="${TAIJI_WEBUI_GATEWAY_BASE_URL:-http://$AGENT_API_HOST:$AGENT_API_PORT}"
TERMINAL_CWD="${TAIJI_TERMINAL_CWD:-${TERMINAL_CWD:-$TAIJI_WORKSPACE}}"
TAIJI_LICENSE_FILE="${TAIJI_LICENSE_FILE:-$TAIJI_CONFIG_DIR/license.jwt}"
TAIJI_LICENSE_REQUIRED="${TAIJI_LICENSE_REQUIRED:-1}"
TAIJI_LICENSE_MACHINE_BINDING_REQUIRED="${TAIJI_LICENSE_MACHINE_BINDING_REQUIRED:-1}"

if [ -z "${API_SERVER_KEY:-}" ] || [ "$API_SERVER_KEY" = "replace-with-a-random-local-dev-token" ]; then
  API_SERVER_KEY="$(openssl rand -hex 32 2>/dev/null || "$TAIJI_WEBUI_PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
  umask 077
  printf 'API_SERVER_KEY=%s\n' "$API_SERVER_KEY" > "$RUNTIME_ENV"
fi
TAIJI_WEBUI_GATEWAY_API_KEY="${TAIJI_WEBUI_GATEWAY_API_KEY:-$API_SERVER_KEY}"

mkdir -p "$TAIJI_RUNTIME_HOME/logs" "$TAIJI_WEBUI_STATE_DIR" "$TAIJI_WORKSPACE"

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo "Taiji web service already running with PID $old_pid"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if lsof -nP -iTCP:"$TAIJI_WEBUI_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $TAIJI_WEBUI_PORT is already in use. Set WEBUI_PORT/TAIJI_WEBUI_PORT in $TAIJI_ENV_FILE." >&2
  lsof -nP -iTCP:"$TAIJI_WEBUI_PORT" -sTCP:LISTEN >&2 || true
  exit 1
fi

export TAIJI_RUNTIME_HOME
export TAIJI_WORKSPACE
export TAIJI_STATE_DIR
export TAIJI_WEBUI_HOST
export TAIJI_WEBUI_PORT
export TAIJI_WEBUI_STATE_DIR
export TAIJI_WEBUI_DEFAULT_WORKSPACE
export TAIJI_WEBUI_AGENT_DIR
export TAIJI_WEBUI_PYTHON
export TAIJI_WEBUI_CHAT_BACKEND
export TAIJI_WEBUI_GATEWAY_BASE_URL
export TAIJI_WEBUI_GATEWAY_API_KEY
export TERMINAL_CWD
export TAIJI_LICENSE_FILE
export TAIJI_LICENSE_STATE_FILE
export TAIJI_LICENSE_REQUIRED
export TAIJI_LICENSE_MACHINE_BINDING_REQUIRED
unset PYTHONPATH PYTHONHOME

if [ -f "$WEBUI_DIR/server.py" ]; then
  TAIJI_WEBUI_SERVER="$WEBUI_DIR/server.py"
elif [ -f "$WEBUI_DIR/server.pyc" ]; then
  TAIJI_WEBUI_SERVER="$WEBUI_DIR/server.pyc"
else
  echo "Taiji web service entrypoint not found under $WEBUI_DIR" >&2
  exit 1
fi

cd "$WEBUI_DIR"
printf '\n[%s] Taiji WebUI startup requested\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
pid="$("$TAIJI_WEBUI_PYTHON" -c 'import os, subprocess, sys
log = open(sys.argv[1], "ab", buffering=0)
proc = subprocess.Popen(
    sys.argv[3:],
    cwd=sys.argv[2],
    stdout=log,
    stderr=subprocess.STDOUT,
    start_new_session=True,
    env=os.environ.copy(),
)
print(proc.pid)
' "$LOG_FILE" "$WEBUI_DIR" "$TAIJI_WEBUI_PYTHON" "$TAIJI_WEBUI_SERVER")"
printf '%s\n' "$pid" > "$PID_FILE"

health_url="http://$TAIJI_WEBUI_HOST:$TAIJI_WEBUI_PORT/health"
deadline=$(( $(date +%s) + START_TIMEOUT_SECONDS ))
while [ "$(date +%s)" -le "$deadline" ]; do
  if curl -fsS "$health_url" >/dev/null 2>&1; then
    echo "Taiji web service ready at http://$TAIJI_WEBUI_HOST:$TAIJI_WEBUI_PORT"
    echo "PID: $pid"
    echo "Log: $LOG_FILE"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Taiji web service exited before becoming healthy. Log: $LOG_FILE" >&2
    tail_recent_log >&2 || true
    rm -f "$PID_FILE"
    exit 1
  fi
  sleep "$START_POLL_INTERVAL"
done

echo "Taiji web service did not become healthy at $health_url within ${START_TIMEOUT_SECONDS}s. Log: $LOG_FILE" >&2
tail_recent_log >&2 || true
kill "$pid" 2>/dev/null || true
rm -f "$PID_FILE"
exit 1
