#!/usr/bin/env bash
# Shared runtime path resolution for local development, desktop app launches,
# and installed Linux packages.

set -euo pipefail

TAIJI_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="${TAIJI_AGENT_ROOT:-$(cd "$TAIJI_SCRIPT_DIR/.." && pwd)}"

_taiji_source_agent="$LAB_DIR/sources/her""mes-agent"
_taiji_source_web="$LAB_DIR/sources/her""mes-webui"
if [ -d "$LAB_DIR/runtime/agent" ]; then
  _taiji_default_agent="$LAB_DIR/runtime/agent"
else
  _taiji_default_agent="$_taiji_source_agent"
fi
if [ -d "$LAB_DIR/runtime/web" ]; then
  _taiji_default_web="$LAB_DIR/runtime/web"
else
  _taiji_default_web="$_taiji_source_web"
fi
AGENT_DIR="${TAIJI_AGENT_AGENT_DIR:-$_taiji_default_agent}"
WEBUI_DIR="${TAIJI_AGENT_WEBUI_DIR:-$_taiji_default_web}"

_xdg_config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
_xdg_data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
_xdg_state_home="${XDG_STATE_HOME:-$HOME/.local/state}"

TAIJI_CONFIG_DIR="${TAIJI_AGENT_CONFIG_DIR:-$_xdg_config_home/taiji-agent}"
TAIJI_DATA_DIR="${TAIJI_AGENT_DATA_DIR:-$_xdg_data_home/taiji-agent}"
TAIJI_STATE_DIR="${TAIJI_AGENT_STATE_DIR:-$_xdg_state_home/taiji-agent}"

if [ "${TAIJI_AGENT_USE_USER_DIRS:-0}" = "1" ]; then
  mkdir -p "$TAIJI_CONFIG_DIR" "$TAIJI_DATA_DIR" "$TAIJI_STATE_DIR"
  TAIJI_RUNTIME_HOME="${TAIJI_RUNTIME_HOME:-$TAIJI_DATA_DIR/runtime-home}"
  TAIJI_ENV_FILE="$TAIJI_RUNTIME_HOME/.env"
  TAIJI_WORKSPACE="${TAIJI_WORKSPACE:-$TAIJI_DATA_DIR/workspace}"
  LOG_DIR="${TAIJI_AGENT_LOG_DIR:-$TAIJI_STATE_DIR/logs}"
  TMP_DIR="${TAIJI_AGENT_TMP_DIR:-$TAIJI_STATE_DIR/tmp}"
else
  TAIJI_RUNTIME_HOME="${TAIJI_RUNTIME_HOME:-$LAB_DIR/runtime-home}"
  TAIJI_ENV_FILE="$TAIJI_RUNTIME_HOME/.env"
  TAIJI_WORKSPACE="${TAIJI_WORKSPACE:-$LAB_DIR/workspace}"
  LOG_DIR="${TAIJI_AGENT_LOG_DIR:-$LAB_DIR/logs}"
  TMP_DIR="${TAIJI_AGENT_TMP_DIR:-$LAB_DIR/tmp}"
fi

mkdir -p "$LOG_DIR" "$TMP_DIR" "$TAIJI_RUNTIME_HOME" "$TAIJI_WORKSPACE" "$TAIJI_RUNTIME_HOME/skills" "$TAIJI_RUNTIME_HOME/scripts"
_TAIJI_CANONICAL_RUNTIME_HOME="$TAIJI_RUNTIME_HOME"
_TAIJI_CANONICAL_ENV_FILE="$TAIJI_ENV_FILE"

if [ "${TAIJI_AGENT_SYNC_PACKAGED_CONFIG:-${TAIJI_AGENT_SYNC_FEATURE_VISIBILITY:-1}}" = "1" ]; then
  _taiji_packaged_config="$LAB_DIR/config/taiji-default-config.yaml"
  _taiji_target_config="$TAIJI_RUNTIME_HOME/config.yaml"
  if [ -f "$_taiji_packaged_config" ]; then
    if [ -x "$AGENT_DIR/venv/bin/python" ]; then
      "$AGENT_DIR/venv/bin/python" "$LAB_DIR/scripts/sync-packaged-config.py" "$_taiji_packaged_config" "$_taiji_target_config" || true
    elif command -v python3 >/dev/null 2>&1; then
      python3 "$LAB_DIR/scripts/sync-packaged-config.py" "$_taiji_packaged_config" "$_taiji_target_config" || true
    fi
  fi
  unset _taiji_packaged_config _taiji_target_config
fi

if [ -f "$TAIJI_ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$TAIJI_ENV_FILE"
  set +a
fi

RUNTIME_ENV="${TAIJI_AGENT_RUNTIME_ENV:-$TMP_DIR/runtime.env}"
if [ -f "$RUNTIME_ENV" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$RUNTIME_ENV"
  set +a
fi

TAIJI_IGNORED_RUNTIME_SELECTOR_COUNT=0
for _taiji_legacy_runtime_selector in TAIJI_AGENT_HOME TAIJI_AGENT_RUNTIME_HOME TAIJI_AGENT_ENV_FILE HER""MES_HOME HER""MES_CONFIG_PATH HER""MES_CONFIG HER""MES_ENV; do
  if [ -n "${!_taiji_legacy_runtime_selector:-}" ]; then
    TAIJI_IGNORED_RUNTIME_SELECTOR_COUNT=$((TAIJI_IGNORED_RUNTIME_SELECTOR_COUNT + 1))
  fi
done
export TAIJI_IGNORED_RUNTIME_SELECTOR_COUNT
unset _taiji_legacy_runtime_selector

TAIJI_RUNTIME_HOME="$_TAIJI_CANONICAL_RUNTIME_HOME"
TAIJI_ENV_FILE="$_TAIJI_CANONICAL_ENV_FILE"
unset TAIJI_AGENT_HOME TAIJI_AGENT_RUNTIME_HOME TAIJI_AGENT_ENV_FILE
unset HER""MES_HOME HER""MES_CONFIG_PATH HER""MES_CONFIG HER""MES_ENV

TAIJI_SECURITY_MODE="${TAIJI_SECURITY_MODE:-restricted}"
TMP_DIR="${TAIJI_AGENT_TMP_DIR:-$TMP_DIR}"
TAIJI_AGENT_TMP_DIR="$TMP_DIR"
export TAIJI_SECURITY_MODE
export TAIJI_AGENT_TMP_DIR
export TMPDIR="$TMP_DIR"
export TMP="$TMP_DIR"
export TEMP="$TMP_DIR"

TAIJI_LICENSE_FILE="${TAIJI_LICENSE_FILE:-$TAIJI_CONFIG_DIR/license.jwt}"
TAIJI_LICENSE_STATE_FILE="${TAIJI_LICENSE_STATE_FILE:-$TAIJI_STATE_DIR/license-state.json}"
TAIJI_LICENSE_MACHINE_BINDING_REQUIRED="${TAIJI_LICENSE_MACHINE_BINDING_REQUIRED:-1}"
# TAIJI_LICENSE_REQUIRED is set by product launchers so shared env resolution stays reusable.

mkdir -p "$LOG_DIR" "$TMP_DIR" "$TAIJI_RUNTIME_HOME" "$TAIJI_WORKSPACE" "$TAIJI_RUNTIME_HOME/skills" "$TAIJI_RUNTIME_HOME/scripts"
unset _TAIJI_CANONICAL_RUNTIME_HOME _TAIJI_CANONICAL_ENV_FILE
