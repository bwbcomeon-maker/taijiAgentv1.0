#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -L)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd -L)"
AGENT_REL="hermes-local-lab/sources/hermes-agent"
WEBUI_REL="hermes-local-lab/sources/hermes-webui"
DESKTOP_REL="apps/taiji-desktop"
DOCX_REL="hermes-local-lab/sources/docx-engine-v2"
BROWSER_SMOKE_REL="hermes-local-lab/sources/hermes-webui/tests/browser_smoke.py"

BASELINE_SHELL_REL=(
  "packaging/linux/deb/preinst"
  "packaging/linux/deb/postinst"
  "packaging/linux/deb/prerm"
  "packaging/linux/deb/postrm"
  "packaging/linux/deb/publish-single-deb.sh"
  "packaging/linux/bin/taiji-agent-acceptance"
  "scripts/sign-taiji-release-evidence.sh"
  "scripts/taiji-release-check.sh"
  "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
  "taijiagent 打包交付/01_制包机_发布预检.sh"
  "taijiagent 打包交付/02_目标终端_安装并验证.sh"
  "taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh"
  "scripts/verify.sh"
)
if [ -f "$ROOT/scripts/release-check.sh" ]; then
  BASELINE_SHELL_REL[${#BASELINE_SHELL_REL[@]}]="scripts/release-check.sh"
fi

MODE_FULL=0
MODE_PLAN=0
MODE_BROWSER=0

show_help() {
  printf '%s\n' 'Usage: scripts/verify.sh [--full] [--plan] [--browser-smoke]'
  printf '%s\n' '  default          Classify staged, unstaged, and untracked paths; a clean diff runs safety, baseline, and root.'
  printf '%s\n' '  --full           Run all registered Taiji local gates available in the prepared offline environment.'
  printf '%s\n' '  --plan           Read-only: print selected interpreters and ordered cwd/argv without executing gates.'
  printf '%s\n' '  --browser-smoke  Separately run baseline plus the isolated real browser smoke; missing Python Playwright exits 3.'
  printf '%s\n' 'Registered gates: local-change-safety, baseline, root, Desktop, DOCX, Agent, WebUI, branding, bootstrap, coexistence.'
  printf 'Browser entry: %s\n' "$BROWSER_SMOKE_REL"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h)
      show_help
      exit 0
      ;;
    --full)
      MODE_FULL=1
      ;;
    --plan)
      MODE_PLAN=1
      ;;
    --browser-smoke)
      MODE_BROWSER=1
      ;;
    *)
      printf 'unknown verification option: %s\n' "$1" >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$MODE_BROWSER" -eq 1 ] && { [ "$MODE_FULL" -eq 1 ] || [ "$MODE_PLAN" -eq 1 ]; }; then
  printf '%s\n' '--browser-smoke cannot be combined with --full or --plan' >&2
  exit 2
fi

if [ -n "${TAIJI_AGENT_PYTHON:-}" ]; then
  ROOT_PYTHON="$TAIJI_AGENT_PYTHON"
elif [ -x "$ROOT/hermes-local-lab/sources/hermes-agent/venv/bin/python" ]; then
  ROOT_PYTHON="$ROOT/hermes-local-lab/sources/hermes-agent/venv/bin/python"
elif [ -x "$ROOT/hermes-local-lab/sources/hermes-agent/.venv/bin/python" ]; then
  ROOT_PYTHON="$ROOT/hermes-local-lab/sources/hermes-agent/.venv/bin/python"
else
  ROOT_PYTHON="$ROOT/hermes-local-lab/sources/hermes-agent/venv/bin/python"
fi
AGENT_RUNNER="$ROOT/$AGENT_REL/scripts/run_tests.sh"

print_resolvers() {
  printf 'INTERPRETER\troot-webui-browser=%s\n' "$ROOT_PYTHON"
  printf 'RUNNER\tagent=%s (self-resolving)\n' "$AGENT_RUNNER"
}

emit_plan() {
  label="$1"
  cwd="$2"
  argv="$3"
  printf 'PLAN\t%s\tcwd=%s\targv=%s\n' "$label" "$cwd" "$argv"
}

emit_safety_plan() {
  emit_plan "local-change-safety" "." "$ROOT_PYTHON scripts/check-local-change-safety.py"
}

emit_baseline_plan() {
  emit_plan "baseline-diff-unstaged" "." "git diff --check"
  emit_plan "baseline-diff-staged" "." "git diff --cached --check"
  emit_plan "baseline-shell" "." "/bin/bash -n ${BASELINE_SHELL_REL[*]}"
}

emit_root_plan() {
  emit_plan "root" "." "$ROOT_PYTHON -m unittest discover -s tests -p test_*.py"
}

emit_desktop_plan() {
  emit_plan "desktop-check" "$DESKTOP_REL" "npm run check"
  emit_plan "desktop-node" "$DESKTOP_REL" "node --test tests/*.test.js"
}

emit_docx_plan() {
  emit_plan "docx" "$DOCX_REL" "npm test"
}

emit_agent_plan() {
  emit_plan "agent" "$AGENT_REL" "scripts/run_tests.sh tests/tools/test_taiji_security_mode.py tests/test_taiji_license.py tests/gateway/test_api_server_license.py tests/gateway/test_session_api.py tests/tools/test_image_generation_readiness.py tests/tools/test_public_chat_brand_guard.py"
}

emit_webui_plan() {
  emit_plan "webui-lint" "$WEBUI_REL" "npm run lint:runtime"
  emit_plan "webui-tests" "$WEBUI_REL" "$ROOT_PYTHON -m pytest -q tests/test_brand_privacy.py tests/test_model_config_api.py tests/test_model_config_frontend.py tests/test_approval_queue.py tests/test_approval_sse.py tests/test_pr1350_sse_notify_correctness.py tests/test_expert_team_frontend.py tests/test_ui_visibility_config.py tests/test_issue1800_file_html_interactions.py tests/test_writeflow_frontend.py::test_taiji_shell_breakpoint_keeps_electron_1024_in_desktop_shell tests/test_issue1116_composer_placeholder.py"
}

emit_extra_plan() {
  emit_plan "branding-agent" "$AGENT_REL" "scripts/run_tests.sh tests/test_cli_skin_integration.py tests/cli/test_cli_skin_integration.py"
  emit_plan "bootstrap-agent" "$AGENT_REL" "scripts/run_tests.sh tests/test_hermes_bootstrap.py"
  emit_plan "bootstrap-webui" "$WEBUI_REL" "$ROOT_PYTHON -m pytest -q tests/test_bootstrap_discover_agent.py tests/test_bootstrap_dotenv.py tests/test_bootstrap_foreground.py tests/test_bootstrap_python_selection.py"
  emit_plan "coexistence-webui" "$WEBUI_REL" "$ROOT_PYTHON -m pytest -q tests/test_taiji_single_runtime_profiles.py"
}

RUN_ROOT=0
RUN_DESKTOP=0
RUN_DOCX=0
RUN_AGENT=0
RUN_WEBUI=0
RUN_EXTRA=0

select_default_scope() {
  set +e
  classification="$(python3 "$ROOT/scripts/classify-ci-scope.py" --local-changes)"
  classifier_status=$?
  set -e
  if [ "$classifier_status" -ne 0 ]; then
    printf '%s\n' 'local change scope classification failed; refusing to select verification suites' >&2
    exit "$classifier_status"
  fi
  case "$classification" in
    *'"risk": "high"'*)
      MODE_FULL=1
      ;;
    *)
      case "$classification" in *'"run_root": true'*) RUN_ROOT=1 ;; esac
      case "$classification" in *'"run_desktop": true'*) RUN_DESKTOP=1 ;; esac
      case "$classification" in *'"run_docx": true'*) RUN_DOCX=1 ;; esac
      case "$classification" in *'"run_agent": true'*) RUN_AGENT=1 ;; esac
      case "$classification" in *'"run_webui": true'*) RUN_WEBUI=1 ;; esac
      ;;
  esac
}

select_scope() {
  if [ "$MODE_FULL" -eq 0 ]; then
    select_default_scope
  fi
  if [ "$MODE_FULL" -eq 1 ]; then
    RUN_ROOT=1
    RUN_DESKTOP=1
    RUN_DOCX=1
    RUN_AGENT=1
    RUN_WEBUI=1
    RUN_EXTRA=1
  elif [ "$RUN_AGENT" -eq 1 ] || [ "$RUN_WEBUI" -eq 1 ]; then
    RUN_EXTRA=1
  fi
}

emit_selected_plan() {
  print_resolvers
  emit_safety_plan
  emit_baseline_plan
  [ "$RUN_ROOT" -eq 0 ] || emit_root_plan
  [ "$RUN_DESKTOP" -eq 0 ] || emit_desktop_plan
  [ "$RUN_DOCX" -eq 0 ] || emit_docx_plan
  [ "$RUN_AGENT" -eq 0 ] || emit_agent_plan
  [ "$RUN_WEBUI" -eq 0 ] || emit_webui_plan
  [ "$RUN_EXTRA" -eq 0 ] || emit_extra_plan
}

require_executable() {
  executable="$1"
  description="$2"
  if [ ! -x "$executable" ]; then
    printf 'verification prerequisite missing: %s (%s)\n' "$description" "$executable" >&2
    exit 1
  fi
}

require_command() {
  command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'verification prerequisite missing: command %s\n' "$command_name" >&2
    exit 1
  fi
}

require_directory() {
  directory="$1"
  description="$2"
  if [ ! -d "$directory" ]; then
    printf 'verification prerequisite missing: %s (%s)\n' "$description" "$directory" >&2
    exit 1
  fi
}

require_file() {
  file="$1"
  description="$2"
  if [ ! -f "$file" ]; then
    printf 'verification prerequisite missing: %s (%s)\n' "$description" "$file" >&2
    exit 1
  fi
}

require_component_files() {
  component="$1"
  description="$2"
  shift 2
  for relative in "$@"; do
    require_file "$ROOT/$component/$relative" "$description: $relative"
  done
}

run_safety() {
  require_executable "$ROOT_PYTHON" "root/WebUI/browser Python"
  require_file "$ROOT/scripts/check-local-change-safety.py" "scripts/check-local-change-safety.py"
  (cd "$ROOT" && "$ROOT_PYTHON" scripts/check-local-change-safety.py)
}

run_root_git() (
  unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
  export GIT_OPTIONAL_LOCKS=0
  cd "$ROOT"
  git "$@"
)

run_baseline() {
  require_command git
  baseline_absolute=()
  for relative in "${BASELINE_SHELL_REL[@]}"; do
    if [ ! -f "$ROOT/$relative" ]; then
      printf 'verification prerequisite missing: baseline shell file (%s)\n' "$relative" >&2
      exit 1
    fi
    baseline_absolute[${#baseline_absolute[@]}]="$ROOT/$relative"
  done
  run_root_git diff --check
  run_root_git diff --cached --check
  /bin/bash -n "${baseline_absolute[@]}"
}

preflight_selected_suites() {
  require_executable "$ROOT_PYTHON" "root/WebUI/browser Python"
  if [ "$RUN_ROOT" -eq 1 ]; then
    require_directory "$ROOT/tests" "root tests directory"
  fi
  if [ "$RUN_DESKTOP" -eq 1 ]; then
    require_command node
    require_command npm
    require_directory "$ROOT/$DESKTOP_REL" "Desktop source directory"
    require_file "$ROOT/$DESKTOP_REL/package.json" "Desktop package.json"
    require_directory "$ROOT/$DESKTOP_REL/node_modules" "Desktop node_modules"
    require_directory "$ROOT/$DESKTOP_REL/node_modules/acorn" "Desktop acorn module"
    require_directory "$ROOT/$DESKTOP_REL/tests" "Desktop tests directory"
    desktop_tests=("$ROOT/$DESKTOP_REL"/tests/*.test.js)
    if [ ! -f "${desktop_tests[0]}" ]; then
      printf '%s\n' 'verification prerequisite missing: Desktop tests/*.test.js' >&2
      exit 1
    fi
  fi
  if [ "$RUN_DOCX" -eq 1 ]; then
    require_command npm
    require_directory "$ROOT/$DOCX_REL" "DOCX source directory"
    require_file "$ROOT/$DOCX_REL/package.json" "DOCX package.json"
    require_directory "$ROOT/$DOCX_REL/node_modules" "DOCX node_modules"
  fi
  if [ "$RUN_AGENT" -eq 1 ] || [ "$RUN_EXTRA" -eq 1 ]; then
    require_executable "$AGENT_RUNNER" "canonical Agent test runner"
  fi
  if [ "$RUN_AGENT" -eq 1 ]; then
    require_component_files "$AGENT_REL" "Agent test" \
      tests/tools/test_taiji_security_mode.py \
      tests/test_taiji_license.py \
      tests/gateway/test_api_server_license.py \
      tests/gateway/test_session_api.py \
      tests/tools/test_image_generation_readiness.py \
      tests/tools/test_public_chat_brand_guard.py
  fi
  if [ "$RUN_WEBUI" -eq 1 ] || [ "$RUN_EXTRA" -eq 1 ]; then
    require_command npm
    require_directory "$ROOT/$WEBUI_REL" "WebUI source directory"
    require_file "$ROOT/$WEBUI_REL/package.json" "WebUI package.json"
    require_directory "$ROOT/$WEBUI_REL/node_modules" "WebUI node_modules"
    require_executable "$ROOT/$WEBUI_REL/node_modules/.bin/eslint" "WebUI eslint"
  fi
  if [ "$RUN_WEBUI" -eq 1 ]; then
    require_component_files "$WEBUI_REL" "WebUI test" \
      tests/test_brand_privacy.py \
      tests/test_model_config_api.py \
      tests/test_model_config_frontend.py \
      tests/test_approval_queue.py \
      tests/test_approval_sse.py \
      tests/test_pr1350_sse_notify_correctness.py \
      tests/test_expert_team_frontend.py \
      tests/test_ui_visibility_config.py \
      tests/test_issue1800_file_html_interactions.py \
      tests/test_writeflow_frontend.py \
      tests/test_issue1116_composer_placeholder.py
  fi
  if [ "$RUN_EXTRA" -eq 1 ]; then
    require_component_files "$AGENT_REL" "Agent extra test" \
      tests/test_cli_skin_integration.py \
      tests/cli/test_cli_skin_integration.py \
      tests/test_hermes_bootstrap.py
    require_component_files "$WEBUI_REL" "WebUI extra test" \
      tests/test_bootstrap_discover_agent.py \
      tests/test_bootstrap_dotenv.py \
      tests/test_bootstrap_foreground.py \
      tests/test_bootstrap_python_selection.py \
      tests/test_taiji_single_runtime_profiles.py
  fi
}

run_root() {
  (cd "$ROOT" && "$ROOT_PYTHON" -m unittest discover -s tests -p 'test_*.py')
}

run_desktop() {
  (cd "$ROOT/$DESKTOP_REL" && npm run check)
  (cd "$ROOT/$DESKTOP_REL" && node --test tests/*.test.js)
}

run_docx() {
  (cd "$ROOT/$DOCX_REL" && npm test)
}

run_agent() {
  (cd "$ROOT/$AGENT_REL" && scripts/run_tests.sh \
    tests/tools/test_taiji_security_mode.py \
    tests/test_taiji_license.py \
    tests/gateway/test_api_server_license.py \
    tests/gateway/test_session_api.py \
    tests/tools/test_image_generation_readiness.py \
    tests/tools/test_public_chat_brand_guard.py)
}

run_webui() {
  (cd "$ROOT/$WEBUI_REL" && npm run lint:runtime)
  (cd "$ROOT/$WEBUI_REL" && \
    HERMES_WEBUI_AGENT_DIR="$ROOT/$AGENT_REL" \
    HERMES_WEBUI_PYTHON="$ROOT_PYTHON" \
    "$ROOT_PYTHON" -m pytest -q \
    tests/test_brand_privacy.py \
    tests/test_model_config_api.py \
    tests/test_model_config_frontend.py \
    tests/test_approval_queue.py \
    tests/test_approval_sse.py \
    tests/test_pr1350_sse_notify_correctness.py \
    tests/test_expert_team_frontend.py \
    tests/test_ui_visibility_config.py \
    tests/test_issue1800_file_html_interactions.py \
    tests/test_writeflow_frontend.py::test_taiji_shell_breakpoint_keeps_electron_1024_in_desktop_shell \
    tests/test_issue1116_composer_placeholder.py)
}

run_extra() {
  (cd "$ROOT/$AGENT_REL" && scripts/run_tests.sh \
    tests/test_cli_skin_integration.py \
    tests/cli/test_cli_skin_integration.py)
  (cd "$ROOT/$AGENT_REL" && scripts/run_tests.sh tests/test_hermes_bootstrap.py)
  (cd "$ROOT/$WEBUI_REL" && \
    HERMES_WEBUI_AGENT_DIR="$ROOT/$AGENT_REL" \
    HERMES_WEBUI_PYTHON="$ROOT_PYTHON" \
    "$ROOT_PYTHON" -m pytest -q \
    tests/test_bootstrap_discover_agent.py \
    tests/test_bootstrap_dotenv.py \
    tests/test_bootstrap_foreground.py \
    tests/test_bootstrap_python_selection.py)
  (cd "$ROOT/$WEBUI_REL" && \
    HERMES_WEBUI_AGENT_DIR="$ROOT/$AGENT_REL" \
    HERMES_WEBUI_PYTHON="$ROOT_PYTHON" \
    "$ROOT_PYTHON" -m pytest -q \
    tests/test_taiji_single_runtime_profiles.py)
}

run_browser_smoke() {
  print_resolvers
  run_safety
  run_baseline
  require_file "$ROOT/$BROWSER_SMOKE_REL" "browser smoke entry"
  if ! "$ROOT_PYTHON" -c 'import playwright' >/dev/null 2>&1; then
    printf '%s\n' 'browser smoke prerequisite missing: Python Playwright' >&2
    exit 3
  fi
  set +e
  "$ROOT_PYTHON" "$ROOT/$BROWSER_SMOKE_REL"
  smoke_status=$?
  set -e
  if [ "$smoke_status" -eq 0 ]; then
    printf '%s\n' 'BROWSER SMOKE PASSED'
    return 0
  fi
  if [ "$smoke_status" -eq 3 ]; then
    printf '%s\n' 'browser smoke returned reserved prerequisite status 3' >&2
    return 2
  fi
  return "$smoke_status"
}

if [ "$MODE_BROWSER" -eq 1 ]; then
  run_browser_smoke
  exit $?
fi

select_scope
if [ "$MODE_PLAN" -eq 1 ]; then
  emit_selected_plan
  exit 0
fi

print_resolvers
run_safety
run_baseline
preflight_selected_suites
[ "$RUN_ROOT" -eq 0 ] || run_root
[ "$RUN_DESKTOP" -eq 0 ] || run_desktop
[ "$RUN_DOCX" -eq 0 ] || run_docx
[ "$RUN_AGENT" -eq 0 ] || run_agent
[ "$RUN_WEBUI" -eq 0 ] || run_webui
[ "$RUN_EXTRA" -eq 0 ] || run_extra
printf '%s\n' 'verification: PASS'
