#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT_DIR="${TAIJI_RELEASE_REPO_ROOT:-$SCRIPT_ROOT}"
SOURCE_GATE="$SCRIPT_ROOT/scripts/check-clean-worktree.sh"
EVIDENCE_VALIDATOR="$SCRIPT_ROOT/scripts/validate-taiji-release-evidence.py"
EVIDENCE_ATTESTATION_PUBLIC_KEY="$ROOT_DIR/tools/taiji-release-evidence/signing-public.pem"
EVIDENCE_ATTESTATION_EXPECTED_FINGERPRINT="839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
AGENT_DIR="$ROOT_DIR/hermes-local-lab/sources/hermes-agent"
WEBUI_DIR="$ROOT_DIR/hermes-local-lab/sources/hermes-webui"
DELIVERY_DIR="${TAIJI_DELIVERY_DIR:-$ROOT_DIR/taijiagent 打包交付}"
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

check_canonical_source() {
  "$SOURCE_GATE" \
    --mode formal \
    --repo-root "$ROOT_DIR" \
    --source-root "$SCRIPT_ROOT"
}

run_root_tests() {
  cd "$ROOT_DIR" || return 1
  python3 -m unittest \
    tests.test_linux_desktop_packaging_static \
    tests.test_kylin_install_script_simulation \
    tests.test_taiji_license_issuer_gui \
    tests.test_offline_rehearsal_producer \
    tests.test_target_desktop_acceptance_producer \
    tests.test_release_evidence_signer_guards
}

run_desktop_evidence_tool_tests() {
  cd "$ROOT_DIR" || return 1
  node --test tools/taiji-desktop-acceptance/run-installed-electron-acceptance.test.js || return 1
  python3 -B tools/taiji-desktop-acceptance/test_assemble_target_evidence.py
}

run_agent_tests() {
  cd "$AGENT_DIR" || return 1
  scripts/run_tests.sh \
    tests/tools/test_taiji_security_mode.py \
    tests/test_taiji_license.py \
    tests/gateway/test_api_server_license.py \
    tests/gateway/test_session_api.py \
    tests/tools/test_image_generation_readiness.py
}

run_webui_tests() {
  cd "$WEBUI_DIR" || return 1
  npm run lint:runtime || return 1
  ../hermes-agent/venv/bin/python -m pytest \
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
    tests/test_issue1116_composer_placeholder.py \
    -q
}

run_delivery_preflight() {
  TAIJI_RELEASE_SKIP_GIT_CHECK=0 \
  TAIJI_RELEASE_REQUIRE_ARTIFACTS=1 \
  TAIJI_REPO_ROOT="$ROOT_DIR" \
    bash "$DELIVERY_DIR/01_制包机_发布预检.sh"
}

check_source_archive() {
  local commit archive count hash_line
  commit="$(git -C "$ROOT_DIR" rev-parse --short=8 HEAD)"
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
  check_source_archive || return 1
  package_count="$(find "$DELIVERY_DIR/生成的安装包" -maxdepth 1 -type f -name 'taiji-agent_*.deb' 2>/dev/null | wc -l | tr -d ' ')"
  [ "$package_count" = "1" ] || { fail "生成的安装包/ 下应有且仅有一个 taiji-agent_*.deb"; return 1; }
  [ -f "$DELIVERY_DIR/生成的安装包/.build-success" ] || { fail "缺少 生成的安装包/.build-success"; return 1; }
  find "$DELIVERY_DIR/生成的安装包" -maxdepth 1 -type f -name 'taiji-agent_*.deb.sha256' | grep -q . || {
    fail "缺少 .deb.sha256"
    return 1
  }
  [ -f "$DELIVERY_DIR/离线依赖/Packages" ] || { fail "缺少 离线依赖/Packages"; return 1; }
  [ -f "$DELIVERY_DIR/离线依赖/Packages.gz" ] || { fail "缺少 离线依赖/Packages.gz"; return 1; }
  [ -f "$DELIVERY_DIR/生成的安装包/taiji-package-manifest.json" ] || { fail "缺少 生成的安装包/taiji-package-manifest.json"; return 1; }
  [ -f "$DELIVERY_DIR/生成的安装包/构建报告.txt" ] || { fail "缺少 生成的安装包/构建报告.txt"; return 1; }
}

check_offline_install_rehearsal() {
  local evidence="$OFFLINE_REHEARSAL_DIR/offline-install-rehearsal.json"
  [ -f "$evidence" ] || {
    fail "离线安装已演练：未实时验证。缺少 $evidence"
    return 1
  }
  if ! validate_release_evidence offline "$evidence"; then
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
  if ! validate_release_evidence target "$evidence"; then
    fail "target-verification.json 未通过桌面 App 目标机证据校验"
    return 1
  fi
  ok "桌面 App 目标机证据有效：$evidence"
}

validate_release_evidence() {
  local mode="$1"
  local evidence="$2"
  local commit deb package_count challenge source_archive output_dir
  output_dir="$DELIVERY_DIR/生成的安装包"
  commit="$(git -C "$ROOT_DIR" rev-parse --short=8 HEAD 2>/dev/null)" || {
    printf '无法读取当前源码 commit\n' >&2
    return 1
  }
  package_count="$(find "$output_dir" -maxdepth 1 -type f -name 'taiji-agent_*.deb' 2>/dev/null | wc -l | tr -d ' ')"
  [ "$package_count" = "1" ] || {
    printf '当前 DEB 数量必须为 1，实际为 %s\n' "$package_count" >&2
    return 1
  }
  deb="$(find "$output_dir" -maxdepth 1 -type f -name 'taiji-agent_*.deb' | head -n 1)"
  source_archive="$DELIVERY_DIR/taiji-agentv1.0-kylin-build-src-$commit.tar.gz"
  if [ "$mode" = "offline" ]; then
    challenge="${TAIJI_OFFLINE_REHEARSAL_CHALLENGE:-}"
  else
    challenge="${TAIJI_TARGET_ACCEPTANCE_CHALLENGE:-}"
  fi
  python3 "$EVIDENCE_VALIDATOR" \
    "$mode" \
    --evidence "$evidence" \
    --source-commit "$commit" \
    --deb "$deb" \
    --checksum "${deb}.sha256" \
    --manifest "$output_dir/taiji-package-manifest.json" \
    --build-marker "$output_dir/.build-success" \
    --source-archive "$source_archive" \
    --packages "$DELIVERY_DIR/离线依赖/Packages" \
    --packages-gz "$DELIVERY_DIR/离线依赖/Packages.gz" \
    --delivery-dir "$DELIVERY_DIR" \
    --attestation-signature "${evidence}.sig" \
    --attestation-public-key "$EVIDENCE_ATTESTATION_PUBLIC_KEY" \
    --attestation-public-key-fingerprint "$EVIDENCE_ATTESTATION_EXPECTED_FINGERPRINT" \
    --challenge "$challenge"
}

main() {
  info "check_canonical_source"
  if ! check_canonical_source; then
    fail "正式发布必须来自干净本地 main"
    printf '\n太极 Agent 销售就绪门禁未通过：%s 项失败。\n' "$failures" >&2
    exit 1
  fi
  ok "check_canonical_source"

  run_step "run_root_tests" run_root_tests
  run_step "run_desktop_evidence_tool_tests" run_desktop_evidence_tool_tests
  run_step "run_agent_tests" run_agent_tests
  run_step "run_webui_tests" run_webui_tests
  run_step "run_delivery_preflight" run_delivery_preflight
  run_step "check_delivery_artifacts" check_delivery_artifacts
  run_step "check_offline_install_rehearsal" check_offline_install_rehearsal
  run_step "check_target_verification" check_target_verification

  if [ "$failures" -gt 0 ]; then
    printf '\n太极 Agent 销售就绪门禁未通过：%s 项失败。\n' "$failures" >&2
    exit 1
  fi
  printf '\n太极 Agent 销售就绪门禁通过。\n'
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
