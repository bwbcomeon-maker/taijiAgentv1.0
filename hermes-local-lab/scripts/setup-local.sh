#!/usr/bin/env bash
# Install local Python dependencies for Hermes Agent and Hermes WebUI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_DIR="$LAB_DIR/sources/hermes-agent"
WEBUI_DIR="$LAB_DIR/sources/hermes-webui"
TAIJI_USER_BIN="${TAIJI_USER_BIN:-$HOME/.local/bin}"
LOCK_CONTRACT_HELPER="$LAB_DIR/../packaging/linux/verify-python-lock-contract.py"
UV_EXECUTABLE="${TAIJI_UV_EXECUTABLE:-uv}"

if [ "$UV_EXECUTABLE" = uv ]; then
  command -v uv >/dev/null 2>&1 || {
    echo "uv is required. Install it first: https://docs.astral.sh/uv/" >&2
    exit 1
  }
else
  [ -x "$UV_EXECUTABLE" ] && [ ! -L "$UV_EXECUTABLE" ] || {
    echo "TAIJI_UV_EXECUTABLE must be an executable regular file" >&2
    exit 1
  }
fi
[ -f "$LOCK_CONTRACT_HELPER" ] && [ ! -L "$LOCK_CONTRACT_HELPER" ] || {
  echo "Python lock contract helper is missing" >&2
  exit 1
}

sync_agent_dependencies() {
  local lock_mode="${TAIJI_UV_LOCK_MODE:-auto}"
  local dependency_profile="${TAIJI_DEPENDENCY_PROFILE:-development}"
  local -a sync_args=(--extra all)

  case "$dependency_profile" in
    development)
      sync_args+=(--extra dev)
      ;;
    production)
      ;;
    *)
      echo "Unsupported TAIJI_DEPENDENCY_PROFILE: $dependency_profile (expected development or production)" >&2
      exit 1
      ;;
  esac

  if [ "$dependency_profile" = production ] && [ "$lock_mode" != strict ]; then
    echo "Production dependency setup requires strict TAIJI_UV_LOCK_MODE" >&2
    exit 1
  fi

  case "$lock_mode" in
    strict)
      UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT="$AGENT_DIR/venv" "$UV_EXECUTABLE" sync "${sync_args[@]}" --locked
      ;;
    auto)
      if UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT="$AGENT_DIR/venv" "$UV_EXECUTABLE" sync "${sync_args[@]}" --locked; then
        return 0
      fi
      echo "Warning: uv.lock sync failed; retrying without --locked in this build workspace." >&2
      echo "Warning: rerun with TAIJI_UV_LOCK_MODE=strict to require a current hash-verified lockfile." >&2
      UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT="$AGENT_DIR/venv" "$UV_EXECUTABLE" sync "${sync_args[@]}"
      ;;
    unlocked)
      UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT="$AGENT_DIR/venv" "$UV_EXECUTABLE" sync "${sync_args[@]}"
      ;;
    *)
      echo "Unsupported TAIJI_UV_LOCK_MODE: $lock_mode (expected strict, auto, or unlocked)" >&2
      exit 1
      ;;
  esac
}

cd "$AGENT_DIR"
python3 "$LOCK_CONTRACT_HELPER" \
  --pyproject "$AGENT_DIR/pyproject.toml" \
  --lock "$AGENT_DIR/uv.lock" \
  --requirements "$WEBUI_DIR/requirements.txt" >/dev/null
"$UV_EXECUTABLE" venv venv --python 3.11
sync_agent_dependencies
python3 "$LOCK_CONTRACT_HELPER" \
  --pyproject "$AGENT_DIR/pyproject.toml" \
  --lock "$AGENT_DIR/uv.lock" \
  --requirements "$WEBUI_DIR/requirements.txt" \
  --verify-installed \
  --python "$AGENT_DIR/venv/bin/python" >/dev/null

mkdir -p "$TAIJI_USER_BIN"
ln -sfn "$LAB_DIR/scripts/taiji" "$TAIJI_USER_BIN/taiji"
hash -r

echo "Local dependencies installed."
echo "Next:"
echo "  $TAIJI_USER_BIN/taiji status"
echo "  $LAB_DIR/scripts/start-agent.sh"
echo "  $LAB_DIR/scripts/start-webui.sh"
echo "  $LAB_DIR/scripts/health-check.sh"
