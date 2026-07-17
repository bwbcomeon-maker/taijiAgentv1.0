#!/usr/bin/env bash
# Check the Taiji Agent local runtime, processes, ports, and HTTP endpoints.

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

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
_taiji_source_agent="$LAB_DIR/sources/her""mes-agent"
_taiji_source_web="$LAB_DIR/sources/her""mes-webui"
if [ -d "$LAB_DIR/runtime/agent" ]; then
  AGENT_DIR="$LAB_DIR/runtime/agent"
else
  AGENT_DIR="$_taiji_source_agent"
fi
if [ -d "$LAB_DIR/runtime/web" ]; then
  WEBUI_DIR="$LAB_DIR/runtime/web"
else
  WEBUI_DIR="$_taiji_source_web"
fi
LOG_DIR="$LAB_DIR/logs"
TMP_DIR="$LAB_DIR/tmp"
RUNTIME_ENV="$TMP_DIR/runtime.env"
USER_STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/taiji-agent"
USER_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/taiji-agent"
USER_LOG_DIR="$USER_STATE_DIR/logs"
USER_TMP_DIR="$USER_STATE_DIR/tmp"

if [ "${TAIJI_AGENT_USE_USER_DIRS:-0}" = "1" ]; then
  LOG_DIR="${TAIJI_AGENT_LOG_DIR:-$USER_LOG_DIR}"
  TMP_DIR="${TAIJI_AGENT_TMP_DIR:-$USER_TMP_DIR}"
  RUNTIME_ENV="${TAIJI_AGENT_RUNTIME_ENV:-$TMP_DIR/runtime.env}"
  TAIJI_CONFIG_DIR="${TAIJI_AGENT_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/taiji-agent}"
  TAIJI_RUNTIME_HOME="${TAIJI_RUNTIME_HOME:-$USER_DATA_DIR/runtime-home}"
  TAIJI_ENV_FILE="$TAIJI_RUNTIME_HOME/.env"
  TAIJI_WORKSPACE="${TAIJI_WORKSPACE:-$USER_DATA_DIR/workspace}"
else
  TAIJI_RUNTIME_HOME="${TAIJI_RUNTIME_HOME:-$LAB_DIR/runtime-home}"
  TAIJI_ENV_FILE="$TAIJI_RUNTIME_HOME/.env"
  TAIJI_WORKSPACE="${TAIJI_WORKSPACE:-$LAB_DIR/workspace}"
fi

mkdir -p "$TMP_DIR"
_TAIJI_CANONICAL_RUNTIME_HOME="$TAIJI_RUNTIME_HOME"
_TAIJI_CANONICAL_ENV_FILE="$TAIJI_ENV_FILE"

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
readonly TAIJI_ACCOUNT_HOME TAIJI_LICENSE_FILE TAIJI_LICENSE_STATE_FILE
export TAIJI_ACCOUNT_HOME
export TAIJI_LICENSE_FILE
export TAIJI_LICENSE_STATE_FILE
unset _TAIJI_CANONICAL_ACCOUNT_HOME

AGENT_API_HOST="${AGENT_API_HOST:-127.0.0.1}"
AGENT_API_PORT="${AGENT_API_PORT:-18642}"
WEBUI_HOST="${WEBUI_HOST:-127.0.0.1}"
WEBUI_PORT="${WEBUI_PORT:-18787}"
API_SERVER_KEY="${API_SERVER_KEY:-}"

ok_count=0
warn_count=0
fail_count=0

ok() { printf '[OK] %s\n' "$*"; ok_count=$((ok_count + 1)); }
warn() { printf '[WARN] %s\n' "$*"; warn_count=$((warn_count + 1)); }
fail() { printf '[FAIL] %s\n' "$*"; fail_count=$((fail_count + 1)); }

check_pid() {
  local name="$1"
  local pid_file="$2"
  local pid_base
  pid_base="$(basename "$pid_file")"
  local user_pid_file="$USER_LOG_DIR/$pid_base"
  local candidates=("$pid_file")
  if [ "$user_pid_file" != "$pid_file" ]; then
    candidates+=("$user_pid_file")
  fi

  local found_file=0
  local candidate
  local pid
  for candidate in "${candidates[@]}"; do
    if [ ! -f "$candidate" ]; then
      continue
    fi
    found_file=1
    pid="$(cat "$candidate" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      ok "$name process running: PID $pid ($candidate)"
      return 0
    fi
  done

  if [ "$found_file" -eq 0 ]; then
    warn "$name pid file missing: $pid_file"
    return 1
  fi
  fail "$name process not running; stale or invalid pid files checked: ${candidates[*]}"
  return 1
}

http_code() {
  curl -sS -o "$1" -w '%{http_code}' "$2" 2>/dev/null
}

echo "Taiji Agent health check"
echo "Lab: $LAB_DIR"
echo

if [ -d "$AGENT_DIR" ]; then
  ok "Taiji Agent runtime exists: $AGENT_DIR"
  if git -C "$AGENT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    agent_commit="$(git -C "$AGENT_DIR" rev-parse HEAD 2>/dev/null || true)"
    agent_tag="$(git -C "$AGENT_DIR" describe --tags --exact-match 2>/dev/null || true)"
    ok "Taiji Agent version: tag=${agent_tag:-none} commit=${agent_commit:-unknown}"
  fi
else
  fail "Taiji Agent runtime missing: $AGENT_DIR"
fi

TAIJI_AGENT_PYTHON="${TAIJI_AGENT_PYTHON:-$AGENT_DIR/venv/bin/python}"

if (cd "$AGENT_DIR" && "$TAIJI_AGENT_PYTHON" -m taiji_runtime.main --help >/dev/null 2>&1); then
  ok "Taiji Agent help command works"
else
  fail "Taiji Agent help command failed"
fi

agent_version="$( (cd "$AGENT_DIR" && "$TAIJI_AGENT_PYTHON" -m taiji_runtime.main --version) 2>/dev/null | head -1 || true)"
if [ -n "$agent_version" ]; then
  agent_version="$(printf '%s\n' "$agent_version" | sed 's/Her'"mes"' Agent/Taiji Agent/g; s/Her'"mes"'/Taiji/g; s/her'"mes"'/taiji/g')"
  ok "Taiji Agent version command works: $agent_version"
else
  fail "Taiji Agent version command failed"
fi

license_status="$("$TAIJI_AGENT_PYTHON" - "$AGENT_DIR" <<'PY' 2>/dev/null || true
import sys

agent_dir = sys.argv[1]
if agent_dir:
    sys.path.insert(0, agent_dir)
try:
    import taiji_license
    data = taiji_license.load_license_status().to_public_dict()
    print("|".join(str(data.get(key) or "-") for key in ("status", "code", "expires_at", "remaining_days")))
except Exception as exc:
    print(f"invalid|license_status_unavailable|-|-")
PY
)"
case "$license_status" in
  valid\|*)
    ok "Taiji license valid: expires=$(printf '%s' "$license_status" | cut -d'|' -f3) remaining_days=$(printf '%s' "$license_status" | cut -d'|' -f4)"
    ;;
  missing\|*)
    warn "Taiji license missing; Agent execution will be blocked when license is required"
    ;;
  expired\|*)
    warn "Taiji license expired; Agent execution is blocked until license is updated"
    ;;
  invalid\|*)
    warn "Taiji license invalid; Agent execution is blocked when license is required"
    ;;
  *)
    warn "Taiji license status unavailable"
    ;;
esac

check_pid "Taiji Agent" "$LOG_DIR/agent.pid" || true
if lsof -nP -iTCP:"$AGENT_API_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  ok "Taiji Agent API port listening: $AGENT_API_HOST:$AGENT_API_PORT"
else
  fail "Taiji Agent API port not listening: $AGENT_API_HOST:$AGENT_API_PORT"
fi

agent_health_body="$TMP_DIR/agent-health.json"
agent_health_code="$(http_code "$agent_health_body" "http://$AGENT_API_HOST:$AGENT_API_PORT/health")"
if [ "$agent_health_code" = "200" ] && grep -q '"status"' "$agent_health_body"; then
  ok "Taiji Agent /health returned HTTP 200"
else
  fail "Taiji Agent /health failed: HTTP ${agent_health_code:-none}"
fi

if [ -n "$API_SERVER_KEY" ]; then
  caps_code="$(curl -sS -o "$TMP_DIR/agent-capabilities.json" -w '%{http_code}' \
    -H "Authorization: Bearer $API_SERVER_KEY" \
    "http://$AGENT_API_HOST:$AGENT_API_PORT/v1/capabilities" 2>/dev/null)"
  if [ "$caps_code" = "200" ] && grep -q 'capabilities' "$TMP_DIR/agent-capabilities.json"; then
    ok "Taiji Agent /v1/capabilities returned HTTP 200"
  else
    warn "Taiji Agent /v1/capabilities did not return expected payload: HTTP ${caps_code:-none}"
  fi
else
  warn "API_SERVER_KEY unavailable; authenticated Agent API checks skipped"
fi

echo

if [ -d "$WEBUI_DIR" ] && { [ -f "$WEBUI_DIR/server.py" ] || [ -f "$WEBUI_DIR/server.pyc" ]; }; then
  ok "Taiji WebUI runtime exists: $WEBUI_DIR"
  if git -C "$WEBUI_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    webui_commit="$(git -C "$WEBUI_DIR" rev-parse HEAD 2>/dev/null || true)"
    webui_tag="$(git -C "$WEBUI_DIR" describe --tags --exact-match 2>/dev/null || true)"
    ok "Taiji WebUI version: tag=${webui_tag:-none} commit=${webui_commit:-unknown}"
  fi
else
  fail "Taiji WebUI runtime missing: $WEBUI_DIR"
fi

check_pid "Taiji WebUI" "$LOG_DIR/web.pid" || true
if lsof -nP -iTCP:"$WEBUI_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  ok "Taiji WebUI port listening: $WEBUI_HOST:$WEBUI_PORT"
else
  fail "Taiji WebUI port not listening: $WEBUI_HOST:$WEBUI_PORT"
fi

webui_health_code="$(http_code "$TMP_DIR/webui-health.json" "http://$WEBUI_HOST:$WEBUI_PORT/health")"
if [ "$webui_health_code" = "200" ] && grep -q '"status"' "$TMP_DIR/webui-health.json"; then
  ok "Taiji WebUI /health returned HTTP 200"
else
  fail "Taiji WebUI /health failed: HTTP ${webui_health_code:-none}"
fi

webui_home_code="$(http_code "$TMP_DIR/webui-home.html" "http://$WEBUI_HOST:$WEBUI_PORT/")"
if [ "$webui_home_code" = "200" ] && grep -Eqi 'Taiji|太极' "$TMP_DIR/webui-home.html"; then
  ok "Taiji WebUI home page returned HTTP 200"
else
  fail "Taiji WebUI home page check failed: HTTP ${webui_home_code:-none}"
fi

agent_ui_code="$(http_code "$TMP_DIR/webui-agent-health.json" "http://$WEBUI_HOST:$WEBUI_PORT/api/health/agent")"
if [ "$agent_ui_code" = "200" ]; then
  if grep -q '"gateway_chat"' "$TMP_DIR/webui-agent-health.json"; then
    ok "Taiji WebUI /api/health/agent returned gateway_chat diagnostics"
  else
    warn "Taiji WebUI /api/health/agent returned HTTP 200 but no gateway_chat block"
  fi
else
  warn "Taiji WebUI /api/health/agent failed: HTTP ${agent_ui_code:-none}"
fi

onboarding_code="$(http_code "$TMP_DIR/webui-onboarding.json" "http://$WEBUI_HOST:$WEBUI_PORT/api/onboarding/status")"
if [ "$onboarding_code" = "200" ]; then
  ok "Taiji WebUI /api/onboarding/status returned HTTP 200"
else
  warn "Taiji WebUI /api/onboarding/status returned HTTP ${onboarding_code:-none}"
fi

echo
provider_keys=(OPENAI_API_KEY DEEPSEEK_API_KEY SILICONFLOW_API_KEY OPENROUTER_API_KEY ANTHROPIC_API_KEY GOOGLE_API_KEY GEMINI_API_KEY KIMI_API_KEY MOONSHOT_API_KEY DASHSCOPE_API_KEY XAI_API_KEY HF_TOKEN)
configured_key_count=0
for key in "${provider_keys[@]}"; do
  if [ -n "${!key:-}" ]; then
    configured_key_count=$((configured_key_count + 1))
  fi
done

if [ "$configured_key_count" -gt 0 ] && [ "${RUN_MODEL_TEST:-0}" = "1" ] && [ -n "$API_SERVER_KEY" ]; then
  test_code="$(curl -sS -o "$TMP_DIR/model-test.json" -w '%{http_code}' \
    -H "Authorization: Bearer $API_SERVER_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"taiji-agent","messages":[{"role":"user","content":"你好，请用一句话说明你已经连接成功。"}],"stream":false}' \
    "http://$AGENT_API_HOST:$AGENT_API_PORT/v1/chat/completions" 2>/dev/null)"
  if [ "$test_code" = "200" ]; then
    ok "Real model chat test returned HTTP 200"
  else
    warn "Real model chat test attempted but did not return HTTP 200: HTTP ${test_code:-none}"
  fi
else
  warn "服务已启动，WebUI 可访问；当前 lab 未配置模型 API Key，未进行真实模型推理测试。"
fi

echo
echo "Summary: OK=$ok_count WARN=$warn_count FAIL=$fail_count"
unset _TAIJI_CANONICAL_RUNTIME_HOME _TAIJI_CANONICAL_ENV_FILE
unset _taiji_source_agent _taiji_source_web
if [ "$fail_count" -gt 0 ]; then
  exit 1
fi
exit 0
