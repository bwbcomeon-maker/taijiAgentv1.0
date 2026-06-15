#!/usr/bin/env bash
# Install local Python dependencies for Hermes Agent and Hermes WebUI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_DIR="$LAB_DIR/sources/hermes-agent"
WEBUI_DIR="$LAB_DIR/sources/hermes-webui"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it first: https://docs.astral.sh/uv/" >&2
  exit 1
fi

sync_agent_dependencies() {
  local lock_mode="${TAIJI_UV_LOCK_MODE:-auto}"

  case "$lock_mode" in
    strict)
      UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT="$AGENT_DIR/venv" uv sync --extra all --locked
      ;;
    auto)
      if UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT="$AGENT_DIR/venv" uv sync --extra all --locked; then
        return 0
      fi
      echo "Warning: uv.lock sync failed; retrying without --locked in this build workspace." >&2
      echo "Warning: rerun with TAIJI_UV_LOCK_MODE=strict to require a current hash-verified lockfile." >&2
      UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT="$AGENT_DIR/venv" uv sync --extra all
      ;;
    unlocked)
      UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT="$AGENT_DIR/venv" uv sync --extra all
      ;;
    *)
      echo "Unsupported TAIJI_UV_LOCK_MODE: $lock_mode (expected strict, auto, or unlocked)" >&2
      exit 1
      ;;
  esac
}

cd "$AGENT_DIR"
uv venv venv --python 3.11
sync_agent_dependencies

uv pip install --python "$AGENT_DIR/venv/bin/python" \
  -r "$WEBUI_DIR/requirements.txt"

echo "Local dependencies installed."
echo "Next:"
echo "  $LAB_DIR/scripts/taiji --version"
echo "  $LAB_DIR/scripts/start-agent.sh"
echo "  $LAB_DIR/scripts/start-webui.sh"
echo "  $LAB_DIR/scripts/health-check.sh"
