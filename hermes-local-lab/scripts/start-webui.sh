#!/usr/bin/env bash
# Start Hermes WebUI from the local source checkout.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_DIR="$LAB_DIR/sources/hermes-agent"
WEBUI_DIR="$LAB_DIR/sources/hermes-webui"
LOG_DIR="$LAB_DIR/logs"
TMP_DIR="$LAB_DIR/tmp"
PID_FILE="$LOG_DIR/hermes-webui.pid"
LOG_FILE="$LOG_DIR/hermes-webui.log"
RUNTIME_ENV="$TMP_DIR/runtime.env"

mkdir -p "$LOG_DIR" "$TMP_DIR" "$LAB_DIR/hermes-home" "$LAB_DIR/workspace"

if [ -f "$LAB_DIR/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$LAB_DIR/.env"
  set +a
fi

if [ -f "$RUNTIME_ENV" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$RUNTIME_ENV"
  set +a
fi

HERMES_HOME="${HERMES_HOME:-$LAB_DIR/hermes-home}"
HERMES_WORKSPACE="${HERMES_WORKSPACE:-$LAB_DIR/workspace}"
AGENT_API_HOST="${AGENT_API_HOST:-127.0.0.1}"
AGENT_API_PORT="${AGENT_API_PORT:-18642}"
WEBUI_HOST="${WEBUI_HOST:-127.0.0.1}"
WEBUI_PORT="${WEBUI_PORT:-18787}"
HERMES_WEBUI_HOST="${HERMES_WEBUI_HOST:-$WEBUI_HOST}"
HERMES_WEBUI_PORT="${HERMES_WEBUI_PORT:-$WEBUI_PORT}"
HERMES_WEBUI_STATE_DIR="${HERMES_WEBUI_STATE_DIR:-$HERMES_HOME/webui}"
HERMES_WEBUI_DEFAULT_WORKSPACE="${HERMES_WEBUI_DEFAULT_WORKSPACE:-$HERMES_WORKSPACE}"
HERMES_WEBUI_AGENT_DIR="${HERMES_WEBUI_AGENT_DIR:-$AGENT_DIR}"
HERMES_WEBUI_PYTHON="${HERMES_WEBUI_PYTHON:-$AGENT_DIR/venv/bin/python}"
HERMES_WEBUI_CHAT_BACKEND="${HERMES_WEBUI_CHAT_BACKEND:-gateway}"
HERMES_WEBUI_GATEWAY_BASE_URL="${HERMES_WEBUI_GATEWAY_BASE_URL:-http://$AGENT_API_HOST:$AGENT_API_PORT}"
TERMINAL_CWD="${TERMINAL_CWD:-$HERMES_WORKSPACE}"

if [ -z "${API_SERVER_KEY:-}" ] || [ "$API_SERVER_KEY" = "replace-with-a-random-local-dev-token" ]; then
  API_SERVER_KEY="$(openssl rand -hex 32 2>/dev/null || "$HERMES_WEBUI_PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
  umask 077
  printf 'API_SERVER_KEY=%s\n' "$API_SERVER_KEY" > "$RUNTIME_ENV"
fi
HERMES_WEBUI_GATEWAY_API_KEY="${HERMES_WEBUI_GATEWAY_API_KEY:-$API_SERVER_KEY}"

mkdir -p "$HERMES_HOME/logs" "$HERMES_WEBUI_STATE_DIR" "$HERMES_WORKSPACE"

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo "Hermes WebUI already running with PID $old_pid"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if lsof -nP -iTCP:"$HERMES_WEBUI_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $HERMES_WEBUI_PORT is already in use. Set WEBUI_PORT/HERMES_WEBUI_PORT in $LAB_DIR/.env." >&2
  lsof -nP -iTCP:"$HERMES_WEBUI_PORT" -sTCP:LISTEN >&2 || true
  exit 1
fi

export HERMES_HOME
export HERMES_WORKSPACE
export HERMES_WEBUI_HOST
export HERMES_WEBUI_PORT
export HERMES_WEBUI_STATE_DIR
export HERMES_WEBUI_DEFAULT_WORKSPACE
export HERMES_WEBUI_AGENT_DIR
export HERMES_WEBUI_PYTHON
export HERMES_WEBUI_CHAT_BACKEND
export HERMES_WEBUI_GATEWAY_BASE_URL
export HERMES_WEBUI_GATEWAY_API_KEY
export TERMINAL_CWD
unset PYTHONPATH PYTHONHOME

cd "$AGENT_DIR"
pid="$("$HERMES_WEBUI_PYTHON" -c 'import os, subprocess, sys
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
' "$LOG_FILE" "$AGENT_DIR" "$HERMES_WEBUI_PYTHON" "$WEBUI_DIR/server.py")"
printf '%s\n' "$pid" > "$PID_FILE"

health_url="http://$HERMES_WEBUI_HOST:$HERMES_WEBUI_PORT/health"
for _ in $(seq 1 50); do
  if curl -fsS "$health_url" >/dev/null 2>&1; then
    echo "Hermes WebUI ready at http://$HERMES_WEBUI_HOST:$HERMES_WEBUI_PORT"
    echo "PID: $pid"
    echo "Log: $LOG_FILE"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Hermes WebUI exited before becoming healthy. Log: $LOG_FILE" >&2
    tail -80 "$LOG_FILE" >&2 || true
    rm -f "$PID_FILE"
    exit 1
  fi
  sleep 0.4
done

echo "Hermes WebUI did not become healthy at $health_url. Log: $LOG_FILE" >&2
tail -100 "$LOG_FILE" >&2 || true
kill "$pid" 2>/dev/null || true
rm -f "$PID_FILE"
exit 1
