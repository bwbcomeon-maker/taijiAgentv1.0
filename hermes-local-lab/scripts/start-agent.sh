#!/usr/bin/env bash
# Start Hermes Agent API Server from the local source checkout.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=runtime-env.sh
source "$SCRIPT_DIR/runtime-env.sh"
PID_FILE="$LOG_DIR/hermes-agent.pid"
LOG_FILE="$LOG_DIR/hermes-agent.log"

AGENT_API_HOST="${AGENT_API_HOST:-127.0.0.1}"
AGENT_API_PORT="${AGENT_API_PORT:-18642}"
API_SERVER_HOST="${API_SERVER_HOST:-$AGENT_API_HOST}"
API_SERVER_PORT="${API_SERVER_PORT:-$AGENT_API_PORT}"
API_SERVER_CORS_ORIGINS="${API_SERVER_CORS_ORIGINS:-http://127.0.0.1:18787,http://localhost:18787}"
API_SERVER_MODEL_NAME="${API_SERVER_MODEL_NAME:-hermes-agent}"
TERMINAL_CWD="${TERMINAL_CWD:-$HERMES_WORKSPACE}"
HERMES_PYTHON="${HERMES_PYTHON:-$AGENT_DIR/venv/bin/python}"

if [ ! -x "$HERMES_PYTHON" ]; then
  echo "Hermes Agent Python runtime not found: $HERMES_PYTHON" >&2
  exit 1
fi

if [ -z "${API_SERVER_KEY:-}" ] || [ "$API_SERVER_KEY" = "replace-with-a-random-local-dev-token" ]; then
  API_SERVER_KEY="$(openssl rand -hex 32 2>/dev/null || "$HERMES_PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
  umask 077
  printf 'API_SERVER_KEY=%s\n' "$API_SERVER_KEY" > "$RUNTIME_ENV"
fi

mkdir -p "$HERMES_HOME/logs" "$HERMES_HOME/cron" "$HERMES_HOME/sessions" "$HERMES_HOME/memories" "$HERMES_HOME/skills" "$HERMES_WORKSPACE"

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo "Hermes Agent already running with PID $old_pid"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if lsof -nP -iTCP:"$API_SERVER_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $API_SERVER_PORT is already in use. Set AGENT_API_PORT/API_SERVER_PORT in $TAIJI_ENV_FILE." >&2
  lsof -nP -iTCP:"$API_SERVER_PORT" -sTCP:LISTEN >&2 || true
  exit 1
fi

export HERMES_HOME
export HERMES_WORKSPACE
export API_SERVER_ENABLED=true
export API_SERVER_HOST
export API_SERVER_PORT
export API_SERVER_KEY
export API_SERVER_CORS_ORIGINS
export API_SERVER_MODEL_NAME
export HERMES_ACCEPT_HOOKS=1
export TERMINAL_CWD
unset PYTHONPATH PYTHONHOME

cd "$AGENT_DIR"
pid="$("$HERMES_PYTHON" -c 'import os, subprocess, sys
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
' "$LOG_FILE" "$AGENT_DIR" "$HERMES_PYTHON" -m hermes_cli.main gateway run --accept-hooks)"
printf '%s\n' "$pid" > "$PID_FILE"

health_url="http://$API_SERVER_HOST:$API_SERVER_PORT/health"
for _ in $(seq 1 50); do
  if curl -fsS "$health_url" >/dev/null 2>&1; then
    echo "Hermes Agent API Server ready at http://$API_SERVER_HOST:$API_SERVER_PORT"
    echo "PID: $pid"
    echo "Log: $LOG_FILE"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Hermes Agent exited before becoming healthy. Log: $LOG_FILE" >&2
    tail -80 "$LOG_FILE" >&2 || true
    rm -f "$PID_FILE"
    exit 1
  fi
  sleep 0.4
done

echo "Hermes Agent did not become healthy at $health_url. Log: $LOG_FILE" >&2
tail -100 "$LOG_FILE" >&2 || true
kill "$pid" 2>/dev/null || true
rm -f "$PID_FILE"
exit 1
