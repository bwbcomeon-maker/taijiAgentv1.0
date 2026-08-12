#!/bin/bash -p
set -euo pipefail
umask 077
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONBREAKPOINT PYTHONUSERBASE
unset LD_PRELOAD LD_LIBRARY_PATH DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
unset OPENSSL_CONF OPENSSL_MODULES
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT_DIR="${TAIJI_RELEASE_REPO_ROOT:-$SCRIPT_ROOT}"
TRUSTED_GIT="$SCRIPT_ROOT/scripts/taiji-trusted-git"
SOURCE_GATE="$SCRIPT_ROOT/scripts/check-clean-worktree.sh"
EVIDENCE_VALIDATOR="$SCRIPT_ROOT/scripts/validate-taiji-release-evidence.py"
LIVE_CI_REVALIDATOR="$SCRIPT_ROOT/scripts/revalidate-taiji-github-ci-evidence.py"
RELEASE_TEST_RUNNER="$SCRIPT_ROOT/scripts/run-taiji-release-python-tests.py"
EVIDENCE_ATTESTATION_PUBLIC_KEY="$ROOT_DIR/tools/taiji-release-evidence/signing-public.pem"
EVIDENCE_ATTESTATION_EXPECTED_FINGERPRINT="839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
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
FORMAL_BUILD_TEST_LOG="$DELIVERY_DIR/生成的安装包/formal-build-tests.log"

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
  local isolated_root isolated_identity test_status=0 cleanup_status=0
  isolated_root="$(/usr/bin/mktemp -d /tmp/taiji-release-python.XXXXXX)" \
    || { printf '[FAIL] cannot create isolated release-test root\n' >&2; return 1; }
  isolated_root="$(cd "$isolated_root" && pwd -P)" \
    || { printf '[FAIL] cannot resolve isolated release-test root\n' >&2; return 1; }
  case "$isolated_root" in
    /private/tmp/taiji-release-python.*|/tmp/taiji-release-python.*) ;;
    *) printf '[FAIL] isolated release-test root escaped /tmp: %s\n' "$isolated_root" >&2; return 1 ;;
  esac
  /bin/chmod 0700 "$isolated_root"
  /bin/mkdir -m 0700 "$isolated_root/home" "$isolated_root/tmp"
  isolated_identity="$(/usr/bin/python3 -I -B - "$isolated_root" <<'PY'
import os
import stat
import sys

metadata = os.lstat(sys.argv[1])
if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
    raise SystemExit("isolated release-test root is not a real directory")
print("{}:{}:{}:{}".format(
    metadata.st_dev,
    metadata.st_ino,
    metadata.st_uid,
    stat.S_IMODE(metadata.st_mode),
))
PY
)" || return 1
  cd "$ROOT_DIR" || return 1
  /usr/bin/env -i \
    HOME="$isolated_root/home" \
    TMPDIR="$isolated_root/tmp" \
    PATH=/usr/bin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    /usr/bin/python3 -I -B "$RELEASE_TEST_RUNNER" \
    || test_status=$?
  /usr/bin/python3 -I -B - "$isolated_root" "$isolated_identity" <<'PY' \
    || cleanup_status=$?
import os
import stat
import sys

root, expected = sys.argv[1:]
metadata = os.lstat(root)
actual = "{}:{}:{}:{}".format(
    metadata.st_dev,
    metadata.st_ino,
    metadata.st_uid,
    stat.S_IMODE(metadata.st_mode),
)
if actual != expected or not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
    raise SystemExit("isolated release-test root identity changed; preserving it")
for name in ("home", "tmp"):
    os.rmdir(os.path.join(root, name))
os.rmdir(root)
PY
  if [ "$cleanup_status" -ne 0 ]; then
    printf '[FAIL] isolated release-test root was not empty/stable; preserved: %s\n' "$isolated_root" >&2
    return 1
  fi
  return "$test_status"
}

verify_formal_build_test_evidence() {
  local marker="$DELIVERY_DIR/生成的安装包/.build-success"
  local manifest="$DELIVERY_DIR/生成的安装包/taiji-package-manifest.json"
  /usr/bin/python3 -I -B - "$marker" "$manifest" "$FORMAL_BUILD_TEST_LOG" <<'PY'
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

marker_path, manifest_path, log_path = (Path(value) for value in sys.argv[1:])

def read_regular(path, label, limit):
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > limit
    ):
        raise SystemExit(label + " is not a bounded single-link regular file")
    payload = path.read_bytes()
    if len(payload) != metadata.st_size:
        raise SystemExit(label + " changed while reading")
    return payload

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit("duplicate JSON field: " + key)
        result[key] = value
    return result

marker = {}
for line in read_regular(marker_path, "build marker", 1024 * 1024).decode("utf-8").splitlines():
    if not line or "=" not in line:
        raise SystemExit("invalid build marker line")
    key, value = line.split("=", 1)
    if key in marker:
        raise SystemExit("duplicate build marker field: " + key)
    marker[key] = value
manifest = json.loads(
    read_regular(manifest_path, "package manifest", 1024 * 1024).decode("utf-8"),
    object_pairs_hook=strict_object,
)
expected = {
    "formal_build_tests_status": "pass",
    "formal_build_tests_log_basename": "formal-build-tests.log",
}
for field, value in expected.items():
    if marker.get(field) != value or manifest.get(field) != value:
        raise SystemExit(field + " mismatch")
digest = marker.get("formal_build_tests_log_sha256")
if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
    raise SystemExit("formal build test log SHA256 is invalid")
if manifest.get("formal_build_tests_log_sha256") != digest:
    raise SystemExit("formal build test log SHA256 binding mismatch")
log_payload = read_regular(log_path, "formal build test log", 32 * 1024 * 1024)
if log_path.name != "formal-build-tests.log" or hashlib.sha256(log_payload).hexdigest() != digest:
    raise SystemExit("formal build test log content mismatch")
PY
  /usr/bin/python3 -I -B "$EVIDENCE_VALIDATOR" \
    formal-build-test-log \
    --manifest "$manifest" \
    --build-marker "$marker" \
    --log "$FORMAL_BUILD_TEST_LOG"
}

run_delivery_preflight() {
  TAIJI_RELEASE_SKIP_GIT_CHECK=0 \
  TAIJI_RELEASE_REQUIRE_ARTIFACTS=1 \
  TAIJI_REPO_ROOT="$ROOT_DIR" \
    /bin/bash -p "$DELIVERY_DIR/01_制包机_发布预检.sh"
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
  [ -f "$FORMAL_BUILD_TEST_LOG" ] && [ ! -L "$FORMAL_BUILD_TEST_LOG" ] || { fail "缺少正式构建测试日志"; return 1; }
  [ -f "$DELIVERY_DIR/生成的安装包/构建报告.txt" ] || { fail "缺少 生成的安装包/构建报告.txt"; return 1; }
}

check_certification_and_publication() {
  local commit deb manifest checksum source_archive
  [ -f "$CERTIFICATION_SET" ] && [ ! -L "$CERTIFICATION_SET" ] || { fail "缺少 certification-set.json"; return 1; }
  [ -f "$CERTIFICATION_SET_SIGNATURE" ] && [ ! -L "$CERTIFICATION_SET_SIGNATURE" ] || { fail "缺少 certification-set.json.sig"; return 1; }
  [ -f "$RELEASE_EVIDENCE" ] && [ ! -L "$RELEASE_EVIDENCE" ] || { fail "缺少 release-evidence.json"; return 1; }
  [ -f "$RELEASE_SIGNATURE" ] && [ ! -L "$RELEASE_SIGNATURE" ] || { fail "缺少 release-evidence.json.sig"; return 1; }
  [ -f "$CERTIFICATION_MATRIX" ] && [ ! -L "$CERTIFICATION_MATRIX" ] || { fail "缺少认证矩阵"; return 1; }
  [ -x /usr/bin/openssl ] || { fail "缺少 /usr/bin/openssl"; return 1; }
  [ -f "$LIVE_CI_REVALIDATOR" ] && [ ! -L "$LIVE_CI_REVALIDATOR" ] \
    || { fail "缺少固定 GitHub CI 实时复验器"; return 1; }
  commit="$($TRUSTED_GIT -C "$ROOT_DIR" rev-parse HEAD)" || return 1
  deb="$(find "$DELIVERY_DIR/生成的安装包" -maxdepth 1 -type f -name 'taiji-agent_*.deb' | head -n 1)"
  manifest="$DELIVERY_DIR/生成的安装包/taiji-package-manifest.json"
  checksum="${deb}.sha256"
  source_archive="$DELIVERY_DIR/taiji-agentv1.0-kylin-build-src-$commit.tar.gz"
  /usr/bin/python3 -I -B "$LIVE_CI_REVALIDATOR" \
    --evidence "$DELIVERY_DIR/github-ci-evidence.json" \
    --source-commit "$commit" \
    || { fail "github-ci-live-revalidation 未通过"; return 1; }
  /usr/bin/openssl dgst -sha256 -verify "$EVIDENCE_ATTESTATION_PUBLIC_KEY" -signature "$CERTIFICATION_SET_SIGNATURE" "$CERTIFICATION_SET" >/dev/null \
    || { fail "certification-set.json.sig 验签失败"; return 1; }
  /usr/bin/openssl dgst -sha256 -verify "$EVIDENCE_ATTESTATION_PUBLIC_KEY" -signature "$RELEASE_SIGNATURE" "$RELEASE_EVIDENCE" >/dev/null \
    || { fail "release-evidence.json.sig 验签失败"; return 1; }
  /usr/bin/python3 -I -B - "$CERTIFICATION_SET" "$CERTIFICATION_SET_SIGNATURE" "$RELEASE_EVIDENCE" "$deb" "$manifest" <<'PY' || { fail "认证集、v3 回执、DEB 和 policy 摘要不一致"; return 1; }
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
  /usr/bin/python3 -I -B "$EVIDENCE_VALIDATOR" certification \
    --evidence "$CERTIFICATION_SET" \
    --source-commit "$commit" \
    --deb "$deb" \
    --checksum "$checksum" \
    --manifest "$manifest" \
    --build-marker "$DELIVERY_DIR/生成的安装包/.build-success" \
    --source-archive "$source_archive" \
    --delivery-dir "$DELIVERY_DIR" \
    --matrix "$CERTIFICATION_MATRIX" || return 1
  /usr/bin/python3 -I -B "$EVIDENCE_VALIDATOR" release \
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
  run_step "verify_formal_build_test_evidence" verify_formal_build_test_evidence
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
