#!/usr/bin/env bash
# Check the Hermes Local Lab source, processes, ports, and HTTP endpoints.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_DIR="$LAB_DIR/sources/hermes-agent"
WEBUI_DIR="$LAB_DIR/sources/hermes-webui"
LOG_DIR="$LAB_DIR/logs"
TMP_DIR="$LAB_DIR/tmp"
RUNTIME_ENV="$TMP_DIR/runtime.env"
USER_STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/taiji-agent"
USER_LOG_DIR="$USER_STATE_DIR/logs"

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

echo "Hermes Local Lab health check"
echo "Lab: $LAB_DIR"
echo

if [ -d "$AGENT_DIR" ] && [ -f "$AGENT_DIR/pyproject.toml" ]; then
  ok "Hermes Agent source exists: $AGENT_DIR"
  agent_commit="$(git -C "$AGENT_DIR" rev-parse HEAD 2>/dev/null || true)"
  agent_tag="$(git -C "$AGENT_DIR" describe --tags --exact-match 2>/dev/null || true)"
  ok "Hermes Agent version: tag=${agent_tag:-none} commit=${agent_commit:-unknown}"
else
  fail "Hermes Agent source missing: $AGENT_DIR"
fi

HERMES_PYTHON="${HERMES_PYTHON:-$AGENT_DIR/venv/bin/python}"

if "$HERMES_PYTHON" -m hermes_cli.main --help >/dev/null 2>&1; then
  ok "Hermes Agent help command works"
else
  fail "Hermes Agent help command failed"
fi

agent_version="$("$HERMES_PYTHON" -m hermes_cli.main --version 2>/dev/null | head -1 || true)"
if [ -n "$agent_version" ]; then
  ok "Hermes Agent version command works: $agent_version"
else
  fail "Hermes Agent version command failed"
fi

check_pid "Hermes Agent" "$LOG_DIR/hermes-agent.pid" || true
if lsof -nP -iTCP:"$AGENT_API_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  ok "Hermes Agent API port listening: $AGENT_API_HOST:$AGENT_API_PORT"
else
  fail "Hermes Agent API port not listening: $AGENT_API_HOST:$AGENT_API_PORT"
fi

agent_health_body="$TMP_DIR/agent-health.json"
agent_health_code="$(http_code "$agent_health_body" "http://$AGENT_API_HOST:$AGENT_API_PORT/health")"
if [ "$agent_health_code" = "200" ] && grep -q '"status"' "$agent_health_body"; then
  ok "Hermes Agent /health returned HTTP 200"
else
  fail "Hermes Agent /health failed: HTTP ${agent_health_code:-none}"
fi

if [ -n "$API_SERVER_KEY" ]; then
  caps_code="$(curl -sS -o "$TMP_DIR/agent-capabilities.json" -w '%{http_code}' \
    -H "Authorization: Bearer $API_SERVER_KEY" \
    "http://$AGENT_API_HOST:$AGENT_API_PORT/v1/capabilities" 2>/dev/null)"
  if [ "$caps_code" = "200" ] && grep -q 'hermes.api_server.capabilities' "$TMP_DIR/agent-capabilities.json"; then
    ok "Hermes Agent /v1/capabilities returned HTTP 200"
  else
    warn "Hermes Agent /v1/capabilities did not return expected payload: HTTP ${caps_code:-none}"
  fi
else
  warn "API_SERVER_KEY unavailable; authenticated Agent API checks skipped"
fi

echo

if [ -d "$WEBUI_DIR" ] && [ -f "$WEBUI_DIR/server.py" ]; then
  ok "Hermes WebUI source exists: $WEBUI_DIR"
  webui_commit="$(git -C "$WEBUI_DIR" rev-parse HEAD 2>/dev/null || true)"
  webui_tag="$(git -C "$WEBUI_DIR" describe --tags --exact-match 2>/dev/null || true)"
  ok "Hermes WebUI version: tag=${webui_tag:-none} commit=${webui_commit:-unknown}"
else
  fail "Hermes WebUI source missing: $WEBUI_DIR"
fi

check_pid "Hermes WebUI" "$LOG_DIR/hermes-webui.pid" || true
if lsof -nP -iTCP:"$WEBUI_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  ok "Hermes WebUI port listening: $WEBUI_HOST:$WEBUI_PORT"
else
  fail "Hermes WebUI port not listening: $WEBUI_HOST:$WEBUI_PORT"
fi

webui_health_code="$(http_code "$TMP_DIR/webui-health.json" "http://$WEBUI_HOST:$WEBUI_PORT/health")"
if [ "$webui_health_code" = "200" ] && grep -q '"status"' "$TMP_DIR/webui-health.json"; then
  ok "Hermes WebUI /health returned HTTP 200"
else
  fail "Hermes WebUI /health failed: HTTP ${webui_health_code:-none}"
fi

webui_home_code="$(http_code "$TMP_DIR/webui-home.html" "http://$WEBUI_HOST:$WEBUI_PORT/")"
if [ "$webui_home_code" = "200" ] && grep -qi 'Hermes' "$TMP_DIR/webui-home.html"; then
  ok "Hermes WebUI home page returned HTTP 200 and contains Hermes HTML"
else
  fail "Hermes WebUI home page check failed: HTTP ${webui_home_code:-none}"
fi

agent_ui_code="$(http_code "$TMP_DIR/webui-agent-health.json" "http://$WEBUI_HOST:$WEBUI_PORT/api/health/agent")"
if [ "$agent_ui_code" = "200" ]; then
  if grep -q '"gateway_chat"' "$TMP_DIR/webui-agent-health.json"; then
    ok "Hermes WebUI /api/health/agent returned gateway_chat diagnostics"
  else
    warn "Hermes WebUI /api/health/agent returned HTTP 200 but no gateway_chat block"
  fi
else
  warn "Hermes WebUI /api/health/agent failed: HTTP ${agent_ui_code:-none}"
fi

onboarding_code="$(http_code "$TMP_DIR/webui-onboarding.json" "http://$WEBUI_HOST:$WEBUI_PORT/api/onboarding/status")"
if [ "$onboarding_code" = "200" ]; then
  ok "Hermes WebUI /api/onboarding/status returned HTTP 200"
else
  warn "Hermes WebUI /api/onboarding/status returned HTTP ${onboarding_code:-none}"
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
    -d '{"model":"hermes-agent","messages":[{"role":"user","content":"你好，请用一句话说明你已经连接成功。"}],"stream":false}' \
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
if [ "$fail_count" -gt 0 ]; then
  exit 1
fi
exit 0
