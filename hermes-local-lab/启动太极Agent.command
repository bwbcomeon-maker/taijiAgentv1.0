#!/usr/bin/env bash
# Double-click launcher for taiji Agent local lab on macOS.
set -euo pipefail

LAB_DIR="/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab"
LOG_DIR="$LAB_DIR/logs"
AGENT_URL="http://127.0.0.1:18642/health"
WEBUI_URL="http://127.0.0.1:18787"
WEBUI_HEALTH_URL="$WEBUI_URL/health"

cd "$LAB_DIR"
mkdir -p "$LOG_DIR"

echo "========================================"
echo " taiji Agent local launcher"
echo "========================================"
echo "Project: $LAB_DIR"
echo

wait_for_url() {
  local name="$1"
  local url="$2"
  local seconds="${3:-20}"
  local tries=$((seconds * 2))
  for _ in $(seq 1 "$tries"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$name is ready: $url"
      return 0
    fi
    sleep 0.5
  done
  echo "$name did not become ready: $url" >&2
  return 1
}

echo "[1/3] Starting backend Agent API..."
"$LAB_DIR/scripts/start-agent.sh"
wait_for_url "Agent API" "$AGENT_URL" 25
echo

echo "[2/3] Starting frontend WebUI..."
"$LAB_DIR/scripts/start-webui.sh"
wait_for_url "WebUI" "$WEBUI_HEALTH_URL" 25
echo

echo "[3/3] Opening browser..."
open "$WEBUI_URL"
echo
echo "taiji Agent is running:"
echo "  WebUI : $WEBUI_URL"
echo "  Agent : http://127.0.0.1:18642"
echo
echo "Logs:"
echo "  $LOG_DIR/hermes-agent.log"
echo "  $LOG_DIR/hermes-webui.log"
echo
echo "To stop both services later, run:"
echo "  $LAB_DIR/scripts/stop-all.sh"
echo
echo "This window can be closed after the browser opens."
