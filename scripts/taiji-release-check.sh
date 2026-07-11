#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_DIR="$ROOT_DIR/hermes-local-lab/sources/hermes-agent"
WEBUI_DIR="$ROOT_DIR/hermes-local-lab/sources/hermes-webui"
DELIVERY_DIR="$ROOT_DIR/taijiagent 打包交付"
OFFLINE_REHEARSAL_DIR="${TAIJI_OFFLINE_REHEARSAL_DIR:-$DELIVERY_DIR/offline-install-rehearsal}"
TARGET_EVIDENCE_DIR="${TAIJI_TARGET_VERIFICATION_DIR:-$DELIVERY_DIR/target-verification}"

failures=0

info() { printf '\n== %s ==\n' "$*"; }
ok() { printf '[OK] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; failures=$((failures + 1)); }

run_step() {
  local name="$1"
  local before_failures="$failures"
  shift
  info "$name"
  if "$@"; then
    ok "$name"
  else
    if [ "$failures" -eq "$before_failures" ]; then
      fail "$name"
    else
      printf '[FAIL] %s\n' "$name" >&2
    fi
  fi
}

run_root_tests() {
  cd "$ROOT_DIR"
  python3 -m unittest \
    tests.test_linux_desktop_packaging_static \
    tests.test_kylin_install_script_simulation \
    tests.test_taiji_license_issuer_gui
}

run_agent_tests() {
  cd "$AGENT_DIR"
  scripts/run_tests.sh \
    tests/tools/test_taiji_security_mode.py \
    tests/test_taiji_license.py \
    tests/gateway/test_api_server_license.py \
    tests/gateway/test_session_api.py \
    tests/tools/test_image_generation_readiness.py
}

run_webui_tests() {
  cd "$WEBUI_DIR"
  npm run lint:runtime
  ../hermes-agent/venv/bin/python -m pytest \
    tests/test_brand_privacy.py \
    tests/test_model_config_api.py \
    tests/test_model_config_frontend.py \
    tests/test_approval_queue.py \
    tests/test_approval_sse.py \
    tests/test_pr1350_sse_notify_correctness.py \
    tests/test_expert_team_frontend.py \
    tests/test_ui_visibility_config.py \
    tests/test_writeflow_frontend.py::test_taiji_shell_breakpoint_keeps_electron_1024_in_desktop_shell \
    tests/test_issue1116_composer_placeholder.py \
    -q
}

check_source_archive() {
  local commit archive count hash_line
  commit="$(git -C "$ROOT_DIR" rev-parse --short HEAD)"
  count="$(find "$DELIVERY_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | wc -l | tr -d ' ')"
  [ "$count" = "1" ] || { fail "源码包数量不是 1：$count"; return 1; }
  archive="$(find "$DELIVERY_DIR" -maxdepth 1 -type f -name 'taiji-agentv1.0-kylin-build-src-*.tar.gz' | head -n 1)"
  case "$(basename "$archive")" in
    "taiji-agentv1.0-kylin-build-src-$commit.tar.gz") ;;
    *) fail "源码包 commit 与当前 HEAD 不一致：$(basename "$archive") vs $commit"; return 1 ;;
  esac
  hash_line="$(grep -F "  $(basename "$archive")" "$DELIVERY_DIR/SHA256SUMS.txt" || true)"
  [ -n "$hash_line" ] || { fail "SHA256SUMS.txt 缺少当前源码包 basename"; return 1; }
}

check_delivery_artifacts() {
  local package_count
  check_source_archive
  package_count="$(find "$DELIVERY_DIR/生成的安装包" -maxdepth 1 -type f -name 'taiji-agent_*.deb' 2>/dev/null | wc -l | tr -d ' ')"
  [ "$package_count" = "1" ] || { fail "生成的安装包/ 下应有且仅有一个 taiji-agent_*.deb"; return 1; }
  [ -f "$DELIVERY_DIR/生成的安装包/.build-success" ] || { fail "缺少 生成的安装包/.build-success"; return 1; }
  find "$DELIVERY_DIR/生成的安装包" -maxdepth 1 -type f -name 'taiji-agent_*.deb.sha256' | grep -q . || {
    fail "缺少 .deb.sha256"
    return 1
  }
  [ -f "$DELIVERY_DIR/离线依赖/Packages" ] || { fail "缺少 离线依赖/Packages"; return 1; }
  [ -f "$DELIVERY_DIR/离线依赖/Packages.gz" ] || { fail "缺少 离线依赖/Packages.gz"; return 1; }
  [ -f "$DELIVERY_DIR/taiji-package-manifest.json" ] || { fail "缺少 taiji-package-manifest.json"; return 1; }
  [ -f "$DELIVERY_DIR/构建报告.txt" ] || { fail "缺少 构建报告.txt"; return 1; }
}

check_offline_install_rehearsal() {
  local evidence="$OFFLINE_REHEARSAL_DIR/offline-install-rehearsal.json"
  [ -f "$evidence" ] || {
    fail "离线安装已演练：未实时验证。缺少 $evidence"
    return 1
  }

  if ! python3 - "$evidence" <<'PY'
import json
import sys


def object_without_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle, object_pairs_hook=object_without_duplicate_keys)
except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
    print(f"offline-install-rehearsal.json 无法解析: {exc}", file=sys.stderr)
    raise SystemExit(1)

if type(data) is not dict:
    print("offline-install-rehearsal.json 顶层必须是 JSON object", file=sys.stderr)
    raise SystemExit(1)

expected_strings = {
    "platform": "linux/amd64",
    "network": "none",
}
expected_booleans = {
    "install": True,
    "uninstall": True,
    "reinstall": True,
    "target_verified": False,
}
for key, expected in expected_strings.items():
    value = data.get(key)
    if type(value) is not str or value != expected:
        print(f"offline-install-rehearsal.json 要求 {key}={expected!r}", file=sys.stderr)
        raise SystemExit(1)
for key, expected in expected_booleans.items():
    value = data.get(key)
    if type(value) is not bool or value is not expected:
        print(f"offline-install-rehearsal.json 要求 {key}={str(expected).lower()}", file=sys.stderr)
        raise SystemExit(1)
PY
  then
    fail "offline-install-rehearsal.json 未通过离线生命周期证据校验"
    return 1
  fi
  ok "离线生命周期演练证据有效：$evidence"
}

check_target_verification() {
  local evidence="$TARGET_EVIDENCE_DIR/target-verification.json"
  [ -f "$evidence" ] || {
    fail "目标机已验证：未实时验证。缺少 $evidence"
    return 1
  }
  grep -q '"target_verified"[[:space:]]*:[[:space:]]*true' "$evidence" || {
    fail "target-verification.json 未声明 target_verified=true"
    return 1
  }
  grep -q '"desktop_launch"[[:space:]]*:[[:space:]]*true' "$evidence" || {
    fail "target-verification.json 缺少 desktop_launch=true"
    return 1
  }
  grep -q '"diagnostic_export"[[:space:]]*:[[:space:]]*true' "$evidence" || {
    fail "target-verification.json 缺少 diagnostic_export=true"
    return 1
  }
}

main() {
  run_step "run_root_tests" run_root_tests
  run_step "run_agent_tests" run_agent_tests
  run_step "run_webui_tests" run_webui_tests
  run_step "check_delivery_artifacts" check_delivery_artifacts
  run_step "check_offline_install_rehearsal" check_offline_install_rehearsal
  run_step "check_target_verification" check_target_verification

  if [ "$failures" -gt 0 ]; then
    printf '\n太极 Agent 销售就绪门禁未通过：%s 项失败。\n' "$failures" >&2
    exit 1
  fi
  printf '\n太极 Agent 销售就绪门禁通过。\n'
}

main "$@"
