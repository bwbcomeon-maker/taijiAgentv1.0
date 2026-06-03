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

cd "$AGENT_DIR"
uv venv venv --python 3.11
UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT="$AGENT_DIR/venv" uv sync --extra all --locked

uv pip install --python "$AGENT_DIR/venv/bin/python" \
  -r "$WEBUI_DIR/requirements.txt"

echo "Local dependencies installed."
echo "Next:"
echo "  $LAB_DIR/scripts/start-agent.sh"
echo "  $LAB_DIR/scripts/start-webui.sh"
echo "  $LAB_DIR/scripts/health-check.sh"
