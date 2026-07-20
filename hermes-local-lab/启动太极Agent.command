#!/usr/bin/env bash
# Double-click launcher for taiji Agent local lab on macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LAB_DIR="$SCRIPT_DIR"
REPO_DIR="$(cd "$LAB_DIR/.." && pwd -P)"
LOG_DIR="$LAB_DIR/logs"
AGENT_URL="http://127.0.0.1:18642/health"
WEBUI_URL="http://127.0.0.1:18787"
WEBUI_HEALTH_URL="$WEBUI_URL/health"
SOURCE_GATE="$REPO_DIR/scripts/check-clean-worktree.sh"
TAIJI_SOURCE_MODE="${TAIJI_SOURCE_MODE:-formal}"

"$SOURCE_GATE" \
  --mode "$TAIJI_SOURCE_MODE" \
  --repo-root "$REPO_DIR" \
  --source-root "$REPO_DIR"

TAIJI_SOURCE_ROOT="$REPO_DIR"
TAIJI_SOURCE_COMMIT="$(/usr/bin/git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || printf 'unknown')"
if [[ -n "$(/usr/bin/git -C "$REPO_DIR" status --short --untracked-files=normal 2>/dev/null || true)" ]]; then
  TAIJI_SOURCE_DIRTY=1
else
  TAIJI_SOURCE_DIRTY=0
fi
export TAIJI_SOURCE_ROOT TAIJI_SOURCE_COMMIT TAIJI_SOURCE_DIRTY

cd "$LAB_DIR"
mkdir -p "$LOG_DIR"

echo "========================================"
echo " taiji Agent local launcher"
echo "========================================"
echo "Project: $LAB_DIR"
echo "Source root: $TAIJI_SOURCE_ROOT"
echo "Source commit: $TAIJI_SOURCE_COMMIT"
echo "Source dirty: $TAIJI_SOURCE_DIRTY"
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
