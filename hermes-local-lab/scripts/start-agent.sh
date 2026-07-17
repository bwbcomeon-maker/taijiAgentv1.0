#!/usr/bin/env bash
# Start the Taiji Agent local API service.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-env.sh
source "$SCRIPT_DIR/runtime-env.sh"
PID_FILE="$LOG_DIR/agent.pid"
LOG_FILE="$LOG_DIR/agent.log"
START_TIMEOUT_SECONDS="${TAIJI_AGENT_START_TIMEOUT:-90}"
START_POLL_INTERVAL="${TAIJI_AGENT_START_POLL_INTERVAL:-0.5}"

case "$START_TIMEOUT_SECONDS" in
  ""|*[!0-9]*) START_TIMEOUT_SECONDS=90 ;;
esac
if [ "$START_TIMEOUT_SECONDS" -lt 1 ]; then
  START_TIMEOUT_SECONDS=90
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
API_SERVER_HOST="${API_SERVER_HOST:-$AGENT_API_HOST}"
API_SERVER_PORT="${API_SERVER_PORT:-$AGENT_API_PORT}"
API_SERVER_CORS_ORIGINS="${API_SERVER_CORS_ORIGINS:-http://127.0.0.1:18787,http://localhost:18787}"
API_SERVER_MODEL_NAME="${API_SERVER_MODEL_NAME:-taiji-agent}"
TERMINAL_CWD="${TAIJI_TERMINAL_CWD:-${TERMINAL_CWD:-$TAIJI_WORKSPACE}}"
TAIJI_AGENT_PYTHON="${TAIJI_AGENT_PYTHON:-$AGENT_DIR/venv/bin/python}"

if [ ! -x "$TAIJI_AGENT_PYTHON" ]; then
  echo "Taiji Agent Python runtime not found: $TAIJI_AGENT_PYTHON" >&2
  exit 1
fi

if [ -z "${API_SERVER_KEY:-}" ] || [ "$API_SERVER_KEY" = "replace-with-a-random-local-dev-token" ]; then
  API_SERVER_KEY="$(openssl rand -hex 32 2>/dev/null || "$TAIJI_AGENT_PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
  umask 077
  printf 'API_SERVER_KEY=%s\n' "$API_SERVER_KEY" > "$RUNTIME_ENV"
fi

mkdir -p "$TAIJI_RUNTIME_HOME/logs" "$TAIJI_RUNTIME_HOME/cron" "$TAIJI_RUNTIME_HOME/sessions" "$TAIJI_RUNTIME_HOME/memories" "$TAIJI_RUNTIME_HOME/skills" "$TAIJI_WORKSPACE"

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo "Taiji Agent service already running"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if lsof -nP -iTCP:"$API_SERVER_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Taiji Agent service could not start because the local service slot is already in use." >&2
  exit 1
fi

export TAIJI_RUNTIME_HOME
export TAIJI_WORKSPACE
export TAIJI_STATE_DIR
export TAIJI_SECURITY_MODE
export TAIJI_AGENT_TMP_DIR
export TMPDIR
export TMP
export TEMP
export API_SERVER_ENABLED=true
export API_SERVER_HOST
export API_SERVER_PORT
export API_SERVER_KEY
export API_SERVER_CORS_ORIGINS
export API_SERVER_MODEL_NAME
export TAIJI_ACCEPT_HOOKS=1
export TERMINAL_CWD
export TAIJI_LICENSE_FILE
export TAIJI_LICENSE_STATE_FILE
unset PYTHONPATH PYTHONHOME

cd "$AGENT_DIR"
printf '\n[%s] Taiji Agent API startup requested\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
pid="$("$TAIJI_AGENT_PYTHON" -c 'import os, subprocess, sys
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
' "$LOG_FILE" "$AGENT_DIR" "$TAIJI_AGENT_PYTHON" -m taiji_runtime.main gateway run --accept-hooks)"
printf '%s\n' "$pid" > "$PID_FILE"

health_url="http://$API_SERVER_HOST:$API_SERVER_PORT/health"
deadline=$(( $(date +%s) + START_TIMEOUT_SECONDS ))
while [ "$(date +%s)" -le "$deadline" ]; do
  if curl -fsS "$health_url" >/dev/null 2>&1; then
    echo "Taiji Agent API service ready"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Taiji Agent exited before becoming healthy. Run taiji-agent-diagnose for details." >&2
    rm -f "$PID_FILE"
    exit 1
  fi
  sleep "$START_POLL_INTERVAL"
done

echo "Taiji Agent did not become healthy within ${START_TIMEOUT_SECONDS}s. Run taiji-agent-diagnose for details." >&2
kill "$pid" 2>/dev/null || true
rm -f "$PID_FILE"
exit 1
