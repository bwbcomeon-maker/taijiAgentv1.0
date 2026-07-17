#!/usr/bin/env bash
# Shared runtime path resolution for local development, desktop app launches,
# and installed Linux packages.

if /usr/bin/env | /usr/bin/grep -q '^BASH_FUNC_'; then
  _taiji_exported_function_scan_status=("${PIPESTATUS[@]}")
else
  _taiji_exported_function_scan_status=("${PIPESTATUS[@]}")
fi
case "${_taiji_exported_function_scan_status[0]}:${_taiji_exported_function_scan_status[1]}" in
  *:0)
    /usr/bin/printf '%s\n' \
      "Taiji Agent refuses to run with exported shell functions in the environment." \
      >&2
    # Parameter-expansion failure is language-level: exported functions named
    # return, exit, command, or builtin cannot intercept this fail-closed path.
    _taiji_exported_function_boundary_abort=
    : "${_taiji_exported_function_boundary_abort:?exported shell function boundary violation}"
    ;;
  0:1) ;;
  *)
    /usr/bin/printf '%s\n' \
      "Taiji Agent could not verify the exported shell function boundary." \
      >&2
    _taiji_exported_function_boundary_abort=
    : "${_taiji_exported_function_boundary_abort:?exported shell function boundary unavailable}"
    ;;
esac
unset _taiji_exported_function_scan_status

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

# Treat dotenv files as data only. No line is sourced or evaluated, so command
# substitutions, parameter expansions, backticks, and function bodies stay literal.
_taiji_load_dotenv_file() {
  local _taiji_dotenv_file="$1"
  local _taiji_dotenv_line=""
  local _taiji_dotenv_key=""
  local _taiji_dotenv_value=""
  local _taiji_dotenv_single_quote_regex="^'(.*)'[[:space:]]*(#.*)?$"
  local _taiji_dotenv_double_quote_regex='^"(.*)"[[:space:]]*(#.*)?$'
  local _taiji_dotenv_inline_comment_regex='^(.*[^[:space:]])[[:space:]]+#.*$'

  while IFS= read -r _taiji_dotenv_line || [ -n "$_taiji_dotenv_line" ]; do
    _taiji_dotenv_line="${_taiji_dotenv_line#"${_taiji_dotenv_line%%[![:space:]]*}"}"
    case "$_taiji_dotenv_line" in
      ""|\#*) continue ;;
    esac
    if [[ "$_taiji_dotenv_line" == export[[:space:]]* ]]; then
      _taiji_dotenv_line="${_taiji_dotenv_line#export}"
      _taiji_dotenv_line="${_taiji_dotenv_line#"${_taiji_dotenv_line%%[![:space:]]*}"}"
    fi
    case "$_taiji_dotenv_line" in
      *=*) ;;
      *) continue ;;
    esac

    _taiji_dotenv_key="${_taiji_dotenv_line%%=*}"
    _taiji_dotenv_key="${_taiji_dotenv_key#"${_taiji_dotenv_key%%[![:space:]]*}"}"
    _taiji_dotenv_key="${_taiji_dotenv_key%"${_taiji_dotenv_key##*[![:space:]]}"}"
    [[ "$_taiji_dotenv_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    # These names can alter the current shell or the next executable before
    # application code starts. Provider and ordinary application keys remain allowed.
    case "$_taiji_dotenv_key" in
      _TAIJI_* | \
      HOME | PATH | IFS | BASH_* | ENV | SHELLOPTS | BASHOPTS | CDPATH | \
      GLOBIGNORE | PROMPT_COMMAND | PS[0-4] | LD_* | DYLD_* | \
      UID | EUID | PPID | PWD | OLDPWD | SHLVL | RANDOM | SECONDS | LINENO | \
      OPTARG | OPTIND | FUNCNAME | GROUPS | DIRSTACK | PIPESTATUS | _ | \
      HOSTNAME | HOSTTYPE | MACHTYPE | OSTYPE | \
      PYTHONPATH | PYTHONHOME | PYTHONSTARTUP | PYTHONINSPECT | \
      NODE_OPTIONS | RUBYOPT | RUBYLIB | PERL5OPT | PERL5LIB | \
      JAVA_TOOL_OPTIONS | CLASSPATH | LUA_PATH | LUA_CPATH | \
      TAIJI_ACCOUNT_HOME | TAIJI_LICENSE_FILE | TAIJI_LICENSE_STATE_FILE | \
      AGENT_DIR | WEBUI_DIR | LOG_DIR | TMP_DIR | SCRIPT_DIR | LAB_DIR | RUNTIME_ENV)
        continue
        ;;
    esac

    _taiji_dotenv_value="${_taiji_dotenv_line#*=}"
    _taiji_dotenv_value="${_taiji_dotenv_value#"${_taiji_dotenv_value%%[![:space:]]*}"}"
    case "$_taiji_dotenv_value" in
      \'*)
        [[ "$_taiji_dotenv_value" =~ $_taiji_dotenv_single_quote_regex ]] ||
          continue
        _taiji_dotenv_value="${BASH_REMATCH[1]}"
        ;;
      \"*)
        [[ "$_taiji_dotenv_value" =~ $_taiji_dotenv_double_quote_regex ]] ||
          continue
        _taiji_dotenv_value="${BASH_REMATCH[1]}"
        ;;
      *)
        if [[ "$_taiji_dotenv_value" =~ $_taiji_dotenv_inline_comment_regex ]]; then
          _taiji_dotenv_value="${BASH_REMATCH[1]}"
        fi
        _taiji_dotenv_value="${_taiji_dotenv_value%"${_taiji_dotenv_value##*[![:space:]]}"}"
        ;;
    esac
    export "$_taiji_dotenv_key=$_taiji_dotenv_value"
  done < "$_taiji_dotenv_file"
}

if [ -f "$TAIJI_ENV_FILE" ]; then
  _taiji_load_dotenv_file "$TAIJI_ENV_FILE"
fi

RUNTIME_ENV="${TAIJI_AGENT_RUNTIME_ENV:-$TMP_DIR/runtime.env}"
if [ -f "$RUNTIME_ENV" ]; then
  _taiji_load_dotenv_file "$RUNTIME_ENV"
fi
unset -f _taiji_load_dotenv_file

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

if ! _TAIJI_CANONICAL_ACCOUNT_HOME="$(
  /usr/bin/env -i \
    PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    /bin/bash --noprofile --norc -c '
      set -u

      emit_account_home() {
        case "${1:-}" in
          /*)
            if [ -d "$1" ]; then
              builtin printf "%s\n" "$1"
              return 0
            fi
            ;;
        esac
        return 1
      }

      uid=""
      username=""
      candidate=""
      entry=""
      line=""
      executable=""

      executable="$(command -v id 2>/dev/null || true)"
      if [ -n "$executable" ]; then
        uid="$("$executable" -u 2>/dev/null || true)"
      fi

      executable="$(command -v getent 2>/dev/null || true)"
      if [ -n "$uid" ] && [ -n "$executable" ]; then
        entry="$("$executable" passwd "$uid" 2>/dev/null || true)"
        IFS=: read -r _ _ _ _ _ candidate _ <<< "$entry"
        if emit_account_home "$candidate"; then
          exit 0
        fi
      fi

      executable="$(command -v uname 2>/dev/null || true)"
      if [ -n "$executable" ] &&
        [ "$("$executable" -s 2>/dev/null || true)" = "Darwin" ]; then
        executable="$(command -v id 2>/dev/null || true)"
        if [ -n "$executable" ]; then
          username="$("$executable" -un 2>/dev/null || true)"
        fi
        executable="$(command -v dscl 2>/dev/null || true)"
        if [ -n "$username" ] && [ -n "$executable" ]; then
          while IFS= read -r line; do
            case "$line" in
              NFSHomeDirectory:*)
                candidate="${line#NFSHomeDirectory:}"
                candidate="${candidate#"${candidate%%[![:space:]]*}"}"
                break
                ;;
            esac
          done <<< "$("$executable" . -read "/Users/$username" NFSHomeDirectory 2>/dev/null || true)"
          if emit_account_home "$candidate"; then
            exit 0
          fi
        fi
      fi

      for command_name in python3 python; do
        executable="$(command -v "$command_name" 2>/dev/null || true)"
        if [ -z "$executable" ]; then
          continue
        fi
        candidate="$(
          "$executable" -c \
            "import os, pwd; print(pwd.getpwuid(os.getuid()).pw_dir)" \
            2>/dev/null || true
        )"
        if emit_account_home "$candidate"; then
          exit 0
        fi
      done
      exit 1
    '
)"; then
  /usr/bin/printf '%s\n' \
    "Taiji Agent could not resolve the current account home from the system account database." \
    >&2
  return 1 2>/dev/null || exit 1
fi
TAIJI_ACCOUNT_HOME="$_TAIJI_CANONICAL_ACCOUNT_HOME"
TAIJI_LICENSE_FILE="$TAIJI_ACCOUNT_HOME/.config/taiji-agent/licenses/active-license.jwt"
TAIJI_LICENSE_STATE_FILE="$TAIJI_ACCOUNT_HOME/.local/state/taiji-agent/license-state.json"
_taiji_readonly_command_status=0
builtin export \
  TAIJI_ACCOUNT_HOME TAIJI_LICENSE_FILE TAIJI_LICENSE_STATE_FILE ||
  _taiji_readonly_command_status=$?
builtin readonly \
  TAIJI_ACCOUNT_HOME TAIJI_LICENSE_FILE TAIJI_LICENSE_STATE_FILE ||
  _taiji_readonly_command_status=$?

# A same-shell function may hide either "readonly" or "builtin". Do not trust
# the command status alone: assignment-only subshells are language-level probes
# of the postcondition promised by this script. Successful return means all
# three canonical paths are exported and cannot be reassigned.
_taiji_readonly_boundary_violation=0
if (TAIJI_ACCOUNT_HOME=__taiji_readonly_probe__) 2>/dev/null; then
  _taiji_readonly_boundary_violation=1
fi
if (TAIJI_LICENSE_FILE=__taiji_readonly_probe__) 2>/dev/null; then
  _taiji_readonly_boundary_violation=1
fi
if (TAIJI_LICENSE_STATE_FILE=__taiji_readonly_probe__) 2>/dev/null; then
  _taiji_readonly_boundary_violation=1
fi
if ! /usr/bin/env /bin/sh -c '
  case "${TAIJI_ACCOUNT_HOME-}" in "$1") ;; *) exit 1 ;; esac
  case "${TAIJI_LICENSE_FILE-}" in "$2") ;; *) exit 1 ;; esac
  case "${TAIJI_LICENSE_STATE_FILE-}" in "$3") ;; *) exit 1 ;; esac
' taiji-readonly-export-probe \
  "$TAIJI_ACCOUNT_HOME" "$TAIJI_LICENSE_FILE" "$TAIJI_LICENSE_STATE_FILE"; then
  _taiji_readonly_boundary_violation=1
fi
case "$_taiji_readonly_boundary_violation" in
  0) ;;
  *)
    /usr/bin/printf '%s\n' \
      "Taiji Agent could not establish the canonical license path readonly boundary." \
      >&2
    _taiji_readonly_boundary_abort=
    : "${_taiji_readonly_boundary_abort:?canonical license path readonly boundary unavailable}"
    ;;
esac

/bin/mkdir -p "$LOG_DIR" "$TMP_DIR" "$TAIJI_RUNTIME_HOME" "$TAIJI_WORKSPACE" "$TAIJI_RUNTIME_HOME/skills" "$TAIJI_RUNTIME_HOME/scripts"
unset _taiji_readonly_command_status _taiji_readonly_boundary_violation
unset _TAIJI_CANONICAL_RUNTIME_HOME _TAIJI_CANONICAL_ENV_FILE _TAIJI_CANONICAL_ACCOUNT_HOME
