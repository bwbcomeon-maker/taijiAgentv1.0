#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT_DIR="${TAIJI_RELEASE_REPO_ROOT:-$SCRIPT_ROOT}"
TRUSTED_GIT="$SCRIPT_ROOT/scripts/taiji-trusted-git"
SOURCE_GATE="$SCRIPT_ROOT/scripts/check-clean-worktree.sh"
EVIDENCE_VALIDATOR="$SCRIPT_ROOT/scripts/validate-taiji-release-evidence.py"
LIVE_CI_REVALIDATOR="$SCRIPT_ROOT/scripts/revalidate-taiji-github-ci-evidence.py"
EVIDENCE_ATTESTATION_PUBLIC_KEY="$ROOT_DIR/tools/taiji-release-evidence/signing-public.pem"
EVIDENCE_ATTESTATION_EXPECTED_FINGERPRINT="839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
AGENT_DIR="$ROOT_DIR/hermes-local-lab/sources/hermes-agent"
WEBUI_DIR="$ROOT_DIR/hermes-local-lab/sources/hermes-webui"
DELIVERY_DIR="${TAIJI_DELIVERY_DIR:-$ROOT_DIR/taijiagent 打包交付}"
CERTIFICATION_SET="${TAIJI_CERTIFICATION_SET:-$DELIVERY_DIR/certification/certification-set.json}"
CERTIFICATION_SET_SIGNATURE="${TAIJI_CERTIFICATION_SET_SIGNATURE:-${CERTIFICATION_SET}.sig}"
RELEASE_EVIDENCE="${TAIJI_RELEASE_EVIDENCE:-$DELIVERY_DIR/release-evidence.json}"
RELEASE_SIGNATURE="${TAIJI_RELEASE_SIGNATURE:-${RELEASE_EVIDENCE}.sig}"
CERTIFICATION_MATRIX="$DELIVERY_DIR/验收工具/certification-matrix.json"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --delivery-dir) [ "$#" -ge 2 ] || { printf '%s\n' '--delivery-dir requires a path' >&2; exit 2; }; DELIVERY_DIR="$2"; shift 2 ;;
    --certification-set) [ "$#" -ge 2 ] || { printf '%s\n' '--certification-set requires a path' >&2; exit 2; }; CERTIFICATION_SET="$2"; shift 2 ;;
    --certification-signature) [ "$#" -ge 2 ] || { printf '%s\n' '--certification-signature requires a path' >&2; exit 2; }; CERTIFICATION_SET_SIGNATURE="$2"; shift 2 ;;
    --release-evidence) [ "$#" -ge 2 ] || { printf '%s\n' '--release-evidence requires a path' >&2; exit 2; }; RELEASE_EVIDENCE="$2"; shift 2 ;;
    --release-signature) [ "$#" -ge 2 ] || { printf '%s\n' '--release-signature requires a path' >&2; exit 2; }; RELEASE_SIGNATURE="$2"; shift 2 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
CERTIFICATION_MATRIX="$DELIVERY_DIR/验收工具/certification-matrix.json"

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
    tests.test_target_desktop_acceptance_producer \
    tests.test_github_ci_live_revalidation \
    tests.test_release_evidence_signer_guards \
    tests.test_certification_set_v1 \
    tests.test_release_evidence_assembler_v3 \
    tests.test_release_check_v3
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
  [ -x "$TRUSTED_GIT" ] && [ ! -L "$TRUSTED_GIT" ] || { fail "缺少可信 Git 边界"; return 1; }
  commit="$("$TRUSTED_GIT" -C "$ROOT_DIR" rev-parse HEAD)"
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
  [ -f "$DELIVERY_DIR/生成的安装包/taiji-package-manifest.json" ] || { fail "缺少 生成的安装包/taiji-package-manifest.json"; return 1; }
  [ -f "$DELIVERY_DIR/生成的安装包/构建报告.txt" ] || { fail "缺少 生成的安装包/构建报告.txt"; return 1; }
}

check_certification_and_publication() {
  local commit deb manifest checksum source_archive
  [ -f "$CERTIFICATION_SET" ] && [ ! -L "$CERTIFICATION_SET" ] || { fail "缺少 certification-set.json"; return 1; }
  [ -f "$CERTIFICATION_SET_SIGNATURE" ] && [ ! -L "$CERTIFICATION_SET_SIGNATURE" ] || { fail "缺少 certification-set.json.sig"; return 1; }
  [ -f "$RELEASE_EVIDENCE" ] && [ ! -L "$RELEASE_EVIDENCE" ] || { fail "缺少 release-evidence.json"; return 1; }
  [ -f "$RELEASE_SIGNATURE" ] && [ ! -L "$RELEASE_SIGNATURE" ] || { fail "缺少 release-evidence.json.sig"; return 1; }
  [ -f "$CERTIFICATION_MATRIX" ] && [ ! -L "$CERTIFICATION_MATRIX" ] || { fail "缺少认证矩阵"; return 1; }
  command -v openssl >/dev/null 2>&1 || { fail "缺少 openssl"; return 1; }
  [ -f "$LIVE_CI_REVALIDATOR" ] && [ ! -L "$LIVE_CI_REVALIDATOR" ] \
    || { fail "缺少固定 GitHub CI 实时复验器"; return 1; }
  commit="$($TRUSTED_GIT -C "$ROOT_DIR" rev-parse HEAD)" || return 1
  deb="$(find "$DELIVERY_DIR/生成的安装包" -maxdepth 1 -type f -name 'taiji-agent_*.deb' | head -n 1)"
  manifest="$DELIVERY_DIR/生成的安装包/taiji-package-manifest.json"
  checksum="${deb}.sha256"
  source_archive="$DELIVERY_DIR/taiji-agentv1.0-kylin-build-src-$commit.tar.gz"
  python3 "$LIVE_CI_REVALIDATOR" \
    --evidence "$DELIVERY_DIR/github-ci-evidence.json" \
    --source-commit "$commit" \
    || { fail "github-ci-live-revalidation 未通过"; return 1; }
  openssl dgst -sha256 -verify "$EVIDENCE_ATTESTATION_PUBLIC_KEY" -signature "$CERTIFICATION_SET_SIGNATURE" "$CERTIFICATION_SET" >/dev/null \
    || { fail "certification-set.json.sig 验签失败"; return 1; }
  openssl dgst -sha256 -verify "$EVIDENCE_ATTESTATION_PUBLIC_KEY" -signature "$RELEASE_SIGNATURE" "$RELEASE_EVIDENCE" >/dev/null \
    || { fail "release-evidence.json.sig 验签失败"; return 1; }
  python3 - "$CERTIFICATION_SET" "$CERTIFICATION_SET_SIGNATURE" "$RELEASE_EVIDENCE" "$deb" "$manifest" <<'PY' || { fail "认证集、v3 回执、DEB 和 policy 摘要不一致"; return 1; }
import hashlib, json, sys
from pathlib import Path

cert_path, cert_sig_path, release_path, deb_path, manifest_path = map(Path, sys.argv[1:])
cert = json.loads(cert_path.read_text(encoding="utf-8"))
release = json.loads(release_path.read_text(encoding="utf-8"))
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
deb_sha = hashlib.sha256(deb_path.read_bytes()).hexdigest()
cert_sha = hashlib.sha256(cert_path.read_bytes()).hexdigest()
cert_sig_sha = hashlib.sha256(cert_sig_path.read_bytes()).hexdigest()
if manifest.get("schema") != "taiji-package-manifest/v3":
    raise SystemExit("current release requires manifest v3; schema_version=2 is historical/read-only")
if release.get("schema") != "taiji-release-evidence/v3":
    raise SystemExit("current release requires taiji-release-evidence/v3")
if release.get("certification_set_basename") != cert_path.name or release.get("certification_set_sha256") != cert_sha:
    raise SystemExit("certification set hash binding mismatch")
if release.get("certification_set_signature_basename") != cert_sig_path.name or release.get("certification_set_signature_sha256") != cert_sig_sha:
    raise SystemExit("certification signature hash binding mismatch")
for key in ("source_commit", "version", "deb_basename", "compatibility_policy_id", "compatibility_policy_sha256"):
    if cert.get(key) != release.get(key) or release.get(key) != manifest.get({"deb_basename": "deb_basename"}.get(key, key)):
        raise SystemExit("release identity mismatch: " + key)
if release.get("deb_sha256") != deb_sha or cert.get("deb_sha256") != deb_sha:
    raise SystemExit("DEB hash mismatch")
if release.get("customer_folder_contract") != "exactly-one-deb" or release.get("customer_filename") != deb_path.name:
    raise SystemExit("customer folder contract mismatch")
PY
  python3 "$EVIDENCE_VALIDATOR" certification \
    --evidence "$CERTIFICATION_SET" \
    --source-commit "$commit" \
    --deb "$deb" \
    --checksum "$checksum" \
    --manifest "$manifest" \
    --build-marker "$DELIVERY_DIR/生成的安装包/.build-success" \
    --source-archive "$source_archive" \
    --delivery-dir "$DELIVERY_DIR" \
    --matrix "$CERTIFICATION_MATRIX" || return 1
  python3 "$EVIDENCE_VALIDATOR" release \
    --evidence "$RELEASE_EVIDENCE" \
    --source-commit "$commit" \
    --deb "$deb" \
    --checksum "$checksum" \
    --manifest "$manifest" \
    --build-marker "$DELIVERY_DIR/生成的安装包/.build-success" \
    --source-archive "$source_archive" \
    --delivery-dir "$DELIVERY_DIR" \
    --attestation-signature "$RELEASE_SIGNATURE" \
    --attestation-public-key "$EVIDENCE_ATTESTATION_PUBLIC_KEY" \
    --attestation-public-key-fingerprint "$EVIDENCE_ATTESTATION_EXPECTED_FINGERPRINT" || return 1
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
  run_step "check_certification_and_publication" check_certification_and_publication

  if [ "$failures" -gt 0 ]; then
    printf '\n太极 Agent 销售就绪门禁未通过：%s 项失败。\n' "$failures" >&2
    exit 1
  fi
  printf '\n太极 Agent 销售就绪门禁通过。\n'
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
