#!/usr/bin/env bash
set -Eeuo pipefail

# Management-plane entry point for an already fixed, signed DEB.  This file
# is intentionally not copied into the customer DEB: the customer install
# path is the package's preinst/postinst contract, while this command is only
# used by controlled certification/release automation.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if [ -f "$SCRIPT_DIR/deployment_receipt.py" ]; then
  LOCAL_MANAGEMENT_ROOT="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../deployment_receipt.py" ]; then
  LOCAL_MANAGEMENT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
else
  LOCAL_MANAGEMENT_ROOT="$SCRIPT_DIR"
fi
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
RECEIPT_HELPER="$LOCAL_MANAGEMENT_ROOT/deployment_receipt.py"
[ -f "$RECEIPT_HELPER" ] || RECEIPT_HELPER="$REPO_ROOT/packaging/linux/deployment_receipt.py"
RELEASE_VALIDATOR="$LOCAL_MANAGEMENT_ROOT/validate-taiji-release-evidence.py"
[ -f "$RELEASE_VALIDATOR" ] || RELEASE_VALIDATOR="$(cd "$SCRIPT_DIR/.." && pwd -P)/validate-taiji-release-evidence.py"
[ -f "$RELEASE_VALIDATOR" ] || RELEASE_VALIDATOR="$REPO_ROOT/scripts/validate-taiji-release-evidence.py"

DEB_PATH=""
EXPECTED_VERSION="0.0.0"
EXPECTED_SHA256="0000000000000000000000000000000000000000000000000000000000000000"
ADMISSION_MODE=""
OPERATION="fresh_install"
RECEIPT_PATH=""
BUILD_MANIFEST=""
POLICY_PATH=""
CERTIFICATION_CHALLENGE=""
RELEASE_EVIDENCE=""
RELEASE_SIGNATURE=""
BUSINESS_USER=""
PREVIOUS_DEB=""
PREVIOUS_SHA256=""
PREVIOUS_VERSION=""
PREVIOUS_MANIFEST=""

DEPLOYMENT_ID=""
SOURCE_COMMIT="0000000"
VERSION_BEFORE=""
VERSION_AFTER=""
POLICY_ID=""
POLICY_SHA256=""
DPKG_STATUS_BEFORE="unknown"
DPKG_STATUS_AFTER="unknown"
PREFLIGHT="BLOCKED"
NATIVE_VERIFY="NOT_RUN"
DPKG_MUTATION_STARTED=0
ERROR_STAGE=""
ERROR_CODE=""
RESULT="blocked"
STARTED_AT_UTC="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
FINISHED_AT_UTC=""
LOCK_FD=""
CHALLENGE_RESERVED=""
ADMISSION_CHALLENGE_DIGEST="0000000000000000000000000000000000000000000000000000000000000000"
STAGING_DIR=""
STAGED_DEB_PATH=""
RECEIPT_DEB_BASENAME=""
HAS_DEB=0
HAS_EXPECTED_VERSION=0
HAS_EXPECTED_SHA256=0
HAS_OPERATION=0

usage() {
  cat >&2 <<'EOF'
usage: taiji-silent-deploy.sh --deb PATH --expected-version VERSION
  --expected-sha256 SHA256 --admission-mode certification|release
  --operation fresh_install|reinstall|upgrade|rollback --receipt PATH
  [--build-manifest PATH --policy PATH --certification-challenge HEX]
  [--release-evidence PATH --release-signature PATH]
  [--business-user LOGIN] [--previous-deb PATH --previous-sha256 SHA256]
  [--previous-version VERSION --previous-manifest PATH]  # rollback only
EOF
}

option_value_required() {
  if [ "$#" -lt 2 ]; then
    usage
    ERROR_STAGE="preflight"
    ERROR_CODE="ARGUMENT_INVALID"
    RESULT="blocked"
    return 1
  fi
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --deb)
        option_value_required "$@" || return 2
        DEB_PATH="$2"; HAS_DEB=1; shift 2 ;;
      --expected-version)
        option_value_required "$@" || return 2
        EXPECTED_VERSION="$2"; HAS_EXPECTED_VERSION=1; shift 2 ;;
      --expected-sha256)
        option_value_required "$@" || return 2
        EXPECTED_SHA256="$2"; HAS_EXPECTED_SHA256=1; shift 2 ;;
      --admission-mode)
        option_value_required "$@" || return 2
        ADMISSION_MODE="$2"; shift 2 ;;
      --operation)
        option_value_required "$@" || return 2
        OPERATION="$2"; HAS_OPERATION=1; shift 2 ;;
      --receipt)
        option_value_required "$@" || return 2
        RECEIPT_PATH="$2"; shift 2 ;;
      --build-manifest)
        option_value_required "$@" || return 2
        BUILD_MANIFEST="$2"; shift 2 ;;
      --policy)
        option_value_required "$@" || return 2
        POLICY_PATH="$2"; shift 2 ;;
      --certification-challenge)
        option_value_required "$@" || return 2
        CERTIFICATION_CHALLENGE="$2"; shift 2 ;;
      --release-evidence)
        option_value_required "$@" || return 2
        RELEASE_EVIDENCE="$2"; shift 2 ;;
      --release-signature)
        option_value_required "$@" || return 2
        RELEASE_SIGNATURE="$2"; shift 2 ;;
      --business-user)
        option_value_required "$@" || return 2
        BUSINESS_USER="$2"; shift 2 ;;
      --previous-deb)
        option_value_required "$@" || return 2
        PREVIOUS_DEB="$2"; shift 2 ;;
      --previous-sha256)
        option_value_required "$@" || return 2
        PREVIOUS_SHA256="$2"; shift 2 ;;
      --previous-version)
        option_value_required "$@" || return 2
        PREVIOUS_VERSION="$2"; shift 2 ;;
      --previous-manifest)
        option_value_required "$@" || return 2
        PREVIOUS_MANIFEST="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) usage; RECEIPT_PATH="${RECEIPT_PATH:-}"; ERROR_STAGE="preflight"; ERROR_CODE="ARGUMENT_INVALID"; RESULT="blocked"; return 2 ;;
    esac
  done
  if [ -z "$RECEIPT_PATH" ]; then
    usage >&2
    return 2
  fi
  [ "$HAS_DEB" = 1 ] || { ERROR_STAGE="preflight"; ERROR_CODE="ARGUMENT_INVALID"; return 2; }
  [ "$HAS_EXPECTED_VERSION" = 1 ] || { ERROR_STAGE="preflight"; ERROR_CODE="ARGUMENT_INVALID"; return 2; }
  [ "$HAS_EXPECTED_SHA256" = 1 ] || { ERROR_STAGE="preflight"; ERROR_CODE="ARGUMENT_INVALID"; return 2; }
  [ -n "$ADMISSION_MODE" ] || { ERROR_STAGE="admission"; ERROR_CODE="ADMISSION_MODE_REQUIRED"; return 2; }
  [ "$HAS_OPERATION" = 1 ] || { ERROR_STAGE="preflight"; ERROR_CODE="ARGUMENT_INVALID"; return 2; }
}

have() { command -v "$1" >/dev/null 2>&1; }

write_receipt() {
  [ -n "$RECEIPT_PATH" ] || return 0
  FINISHED_AT_UTC="${FINISHED_AT_UTC:-$(date -u '+%Y-%m-%dT%H:%M:%SZ')}"
  DEPLOYMENT_ID="${DEPLOYMENT_ID:-dep-$(python3 -c 'import secrets; print(secrets.token_hex(16))' 2>/dev/null || printf '%032d' 0 | cut -c1-32)}"
  local receipt_basename receipt_sha
  if [ -n "$RECEIPT_DEB_BASENAME" ]; then
    receipt_basename="$RECEIPT_DEB_BASENAME"
  elif [ -n "$PREVIOUS_DEB" ]; then
    receipt_basename="$(basename -- "$PREVIOUS_DEB")"
  elif [ -n "$DEB_PATH" ]; then
    receipt_basename="$(basename -- "$DEB_PATH")"
  else
    receipt_basename=""
  fi
  if [[ ! "$receipt_basename" =~ ^taiji-agent_[A-Za-z0-9.+:~_-]+_amd64\.deb$ ]]; then
    receipt_basename="taiji-agent_${EXPECTED_VERSION:-0.0.0}_amd64.deb"
  fi
  receipt_sha="${PREVIOUS_SHA256:-$EXPECTED_SHA256}"
  [[ "$receipt_sha" =~ ^[0-9a-f]{64}$ ]] || receipt_sha="0000000000000000000000000000000000000000000000000000000000000000"
  export RECEIPT_HELPER RECEIPT_PATH DEPLOYMENT_ID OPERATION RESULT SOURCE_COMMIT
  export VERSION_BEFORE VERSION_REQUESTED="$EXPECTED_VERSION" VERSION_AFTER ARCHITECTURE="amd64"
  export DEB_BASENAME="$receipt_basename"
  export DEB_SHA256="$receipt_sha"
  export POLICY_ID POLICY_SHA256 PREFLIGHT DPKG_STATUS_BEFORE DPKG_STATUS_AFTER NATIVE_VERIFY
  export STARTED_AT_UTC FINISHED_AT_UTC ERROR_STAGE ERROR_CODE ROLLBACK_TRANSACTION_ID=""
  python3 - "$RECEIPT_HELPER" <<'PY'
import importlib.util
import os
import sys
from pathlib import Path

helper_path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("taiji_deployment_receipt_writer", helper_path)
if spec is None or spec.loader is None:
    raise SystemExit(2)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

none_if_empty = lambda name: os.environ.get(name) or None
payload = {
    "schema": module.SCHEMA,
    "deployment_id": os.environ["DEPLOYMENT_ID"],
    "operation": os.environ["OPERATION"],
    "result": os.environ["RESULT"],
    "source_commit": os.environ.get("SOURCE_COMMIT", "0000000"),
    "version_before": none_if_empty("VERSION_BEFORE"),
    "version_requested": os.environ["VERSION_REQUESTED"],
    "version_after": none_if_empty("VERSION_AFTER"),
    "architecture": os.environ.get("ARCHITECTURE", "amd64"),
    "deb_basename": os.environ["DEB_BASENAME"],
    "deb_sha256": os.environ["DEB_SHA256"],
    "compatibility_policy_id": os.environ.get("POLICY_ID") or "taiji-linux-amd64-deb-v1",
    "compatibility_policy_sha256": os.environ.get("POLICY_SHA256") or ("0" * 64),
    "preflight": os.environ["PREFLIGHT"],
    "dpkg_status_before": os.environ.get("DPKG_STATUS_BEFORE", "unknown"),
    "dpkg_status_after": os.environ.get("DPKG_STATUS_AFTER", "unknown"),
    "native_verify": os.environ.get("NATIVE_VERIFY", "NOT_RUN"),
    "started_at_utc": os.environ["STARTED_AT_UTC"],
    "finished_at_utc": os.environ["FINISHED_AT_UTC"],
    "error_stage": none_if_empty("ERROR_STAGE"),
    "error_code": none_if_empty("ERROR_CODE"),
    "rollback_transaction_id": none_if_empty("ROLLBACK_TRANSACTION_ID"),
}
module.write_receipt_atomic(os.environ["RECEIPT_PATH"], payload)
PY
}

cleanup_staged_deb() {
  [ -n "$STAGING_DIR" ] || return 0
  # The staging directory is created by this process with mode 0700.  Remove
  # only the exact file and directory we created; never recurse over a caller
  # supplied path.
  [ -z "$STAGED_DEB_PATH" ] || rm -f -- "$STAGED_DEB_PATH" 2>/dev/null || true
  rmdir -- "$STAGING_DIR" 2>/dev/null || true
  STAGED_DEB_PATH=""
  STAGING_DIR=""
}

finish() {
  local exit_code="${1:-1}"
  FINISHED_AT_UTC="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  set +e
  write_receipt
  local receipt_status=$?
  if [ "$receipt_status" -ne 0 ]; then
    printf '[FAIL] 无法写入部署回执\n' >&2
    [ "$exit_code" -eq 0 ] && exit_code="$receipt_status"
  fi
  cleanup_staged_deb
  set -e
  exit "$exit_code"
}

blocked() {
  RESULT="blocked"
  PREFLIGHT="BLOCKED"
  ERROR_STAGE="${1:-preflight}"
  ERROR_CODE="${2:-PREFLIGHT_BLOCKED}"
  finish "${3:-1}"
}

manual_recovery() {
  RESULT="manual_recovery_required"
  ERROR_STAGE="${1:-dpkg}"
  ERROR_CODE="${2:-DPKG_INSTALL_FAILED}"
  finish "${3:-1}"
}

on_unexpected_error() {
  local code="$?"
  RESULT="manual_recovery_required"
  ERROR_STAGE="internal"
  ERROR_CODE="INTERNAL_ERROR"
  finish "$code"
}

on_interrupted() {
  if [ "$DPKG_MUTATION_STARTED" = "1" ]; then
    RESULT="manual_recovery_required"
    ERROR_STAGE="dpkg"
    ERROR_CODE="INTERRUPTED_DURING_DPKG"
    finish 130
  fi
  RESULT="blocked"
  PREFLIGHT="BLOCKED"
  ERROR_STAGE="lock"
  ERROR_CODE="INTERRUPTED"
  finish 130
}

trap on_unexpected_error ERR
trap on_interrupted INT TERM HUP

validate_basic_args() {
  [[ "$ADMISSION_MODE" = certification || "$ADMISSION_MODE" = release ]] || blocked admission ADMISSION_MODE_INVALID 2
  if [[ "$OPERATION" != fresh_install && "$OPERATION" != reinstall && "$OPERATION" != upgrade && "$OPERATION" != rollback ]]; then
    OPERATION="fresh_install"
    blocked preflight OPERATION_INVALID 2
  fi
  if [[ ! "$EXPECTED_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    EXPECTED_SHA256="0000000000000000000000000000000000000000000000000000000000000000"
    blocked preflight EXPECTED_SHA256_INVALID 2
  fi
  if [[ ! "$EXPECTED_VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,127}$ ]]; then
    EXPECTED_VERSION="0.0.0"
    blocked preflight VERSION_INVALID 2
  fi
  if [ "$OPERATION" = rollback ]; then
    [ -n "$PREVIOUS_DEB" ] || blocked preflight PREVIOUS_DEB_REQUIRED 2
    [[ "${PREVIOUS_SHA256:-}" =~ ^[0-9a-f]{64}$ ]] || blocked preflight PREVIOUS_SHA256_INVALID 2
    [[ "$PREVIOUS_VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,127}$ ]] || blocked preflight PREVIOUS_VERSION_REQUIRED 2
    [ -n "$PREVIOUS_MANIFEST" ] || blocked admission PREVIOUS_MANIFEST_REQUIRED 2
    EXPECTED_VERSION="$PREVIOUS_VERSION"
    BUILD_MANIFEST="$PREVIOUS_MANIFEST"
  elif [ -n "$PREVIOUS_DEB$PREVIOUS_SHA256$PREVIOUS_VERSION$PREVIOUS_MANIFEST" ]; then
    blocked preflight PREVIOUS_ONLY_ROLLBACK 2
  fi
}

validate_regular_file() {
  local path="$1" code="$2"
  [ -f "$path" ] || blocked verification "$code" 1
  [ ! -L "$path" ] || blocked verification "${code}_SYMLINK" 1
  local links
  links="$(stat -c '%h' -- "$path" 2>/dev/null || stat -f '%l' -- "$path" 2>/dev/null || printf '0')"
  [ "$links" = "1" ] || blocked verification "${code}_HARDLINK" 1
}

validate_expected_sha256_only() {
  local candidate="$DEB_PATH" expected="$EXPECTED_SHA256" actual
  if [ "$OPERATION" = rollback ]; then
    candidate="$PREVIOUS_DEB"
    expected="$PREVIOUS_SHA256"
  fi
  validate_regular_file "$candidate" DEB_NOT_REGULAR
  local sidecar="${candidate}.sha256"
  validate_regular_file "$sidecar" DEB_SHA256_SIDECAR_NOT_REGULAR
  python3 - "$sidecar" "$expected" "$(basename -- "$candidate")" <<'PY' || blocked verification DEB_SHA256_SIDECAR_MISMATCH 1
import re
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="ascii")
if not re.fullmatch(r"[0-9a-f]{64}[ \t]+\*?[^/\s]+\n?", text):
    raise SystemExit(1)
parts = text.strip().split()
name = parts[-1].lstrip("*")
if parts[0] != sys.argv[2] or name != sys.argv[3]:
    raise SystemExit(1)
PY
  actual="$(sha256sum -- "$candidate" | awk '{print $1}')" || blocked verification DEB_SHA256_READ_FAILED 1
  [ "$actual" = "$expected" ] || blocked verification DEB_SHA256_MISMATCH 1
}

selected_deb_path() {
  if [ "$OPERATION" = rollback ]; then
    printf '%s\n' "$PREVIOUS_DEB"
  else
    printf '%s\n' "$DEB_PATH"
  fi
}

selected_deb_sha256() {
  if [ "$OPERATION" = rollback ]; then
    printf '%s\n' "$PREVIOUS_SHA256"
  else
    printf '%s\n' "$EXPECTED_SHA256"
  fi
}

validate_candidate_hash_and_version() {
  local candidate="$DEB_PATH" expected_sha="$EXPECTED_SHA256" actual package version architecture
  if [ "$OPERATION" = rollback ]; then
    candidate="$PREVIOUS_DEB"
    expected_sha="$PREVIOUS_SHA256"
  fi
  validate_regular_file "$candidate" DEB_NOT_REGULAR
  actual="$(sha256sum -- "$candidate" | awk '{print $1}')" || blocked verification DEB_SHA256_READ_FAILED 1
  [ "$actual" = "$expected_sha" ] || blocked verification DEB_SHA256_MISMATCH 1
  have dpkg-deb || blocked preflight DPKG_DEB_MISSING 1
  package="$(dpkg-deb -f "$candidate" Package 2>/dev/null || true)"
  version="$(dpkg-deb -f "$candidate" Version 2>/dev/null || true)"
  architecture="$(dpkg-deb -f "$candidate" Architecture 2>/dev/null || true)"
  [ "$package" = "taiji-agent" ] || blocked verification DEB_PACKAGE_MISMATCH 1
  [ "$version" = "$EXPECTED_VERSION" ] || blocked verification DEB_VERSION_MISMATCH 1
  [ "$architecture" = "amd64" ] || blocked verification DEB_ARCHITECTURE_MISMATCH 1
  DEB_PATH="$candidate"
  EXPECTED_SHA256="$expected_sha"
}

stage_candidate_for_install() {
  local source="$DEB_PATH" expected_sha="$EXPECTED_SHA256" actual package version architecture owner_mode
  validate_regular_file "$source" DEB_NOT_REGULAR
  RECEIPT_DEB_BASENAME="$(basename -- "$source")"
  STAGING_DIR="$(mktemp -d /var/tmp/taiji-agent-deploy.XXXXXX 2>/dev/null)" || blocked staging STAGING_DIRECTORY_UNAVAILABLE 1
  chmod 0700 -- "$STAGING_DIR" || blocked staging STAGING_DIRECTORY_UNAVAILABLE 1
  chown 0:0 -- "$STAGING_DIR" || blocked staging STAGING_DIRECTORY_OWNER_INVALID 1
  STAGED_DEB_PATH="$STAGING_DIR/$RECEIPT_DEB_BASENAME"
  install -o 0 -g 0 -m 0600 -- "$source" "$STAGED_DEB_PATH" || blocked staging STAGING_COPY_FAILED 1
  validate_regular_file "$STAGED_DEB_PATH" STAGED_DEB_NOT_REGULAR
  owner_mode="$(stat -c '%u:%g:%a:%h' -- "$STAGED_DEB_PATH" 2>/dev/null || printf 'invalid')"
  [ "$owner_mode" = "0:0:600:1" ] || blocked staging STAGED_DEB_OWNERSHIP_INVALID 1
  actual="$(sha256sum -- "$STAGED_DEB_PATH" | awk '{print $1}')" || blocked staging STAGED_DEB_SHA256_READ_FAILED 1
  [ "$actual" = "$expected_sha" ] || blocked staging STAGED_DEB_SHA256_MISMATCH 1
  package="$(dpkg-deb -f "$STAGED_DEB_PATH" Package 2>/dev/null || true)"
  version="$(dpkg-deb -f "$STAGED_DEB_PATH" Version 2>/dev/null || true)"
  architecture="$(dpkg-deb -f "$STAGED_DEB_PATH" Architecture 2>/dev/null || true)"
  [ "$package" = "taiji-agent" ] || blocked staging STAGED_DEB_PACKAGE_MISMATCH 1
  [ "$version" = "$EXPECTED_VERSION" ] || blocked staging STAGED_DEB_VERSION_MISMATCH 1
  [ "$architecture" = "amd64" ] || blocked staging STAGED_DEB_ARCHITECTURE_MISMATCH 1
  DEB_PATH="$STAGED_DEB_PATH"
}

read_manifest_binding() {
  [ -n "$BUILD_MANIFEST" ] || blocked admission BUILD_MANIFEST_REQUIRED 1
  validate_regular_file "$BUILD_MANIFEST" BUILD_MANIFEST_NOT_REGULAR
  [ -n "$POLICY_PATH" ] || blocked admission POLICY_REQUIRED 1
  validate_regular_file "$POLICY_PATH" POLICY_NOT_REGULAR
  export MANIFEST_PATH="$BUILD_MANIFEST" POLICY_PATH_ENV="$POLICY_PATH" EXPECTED_DEB="$(selected_deb_path)" EXPECTED_SHA="$(selected_deb_sha256)" EXPECTED_VER="$EXPECTED_VERSION"
  local binding
  binding="$(python3 - <<'PY'
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

manifest_path = Path(os.environ["MANIFEST_PATH"])
policy_path = Path(os.environ["POLICY_PATH_ENV"])
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit("MANIFEST_INVALID")
if manifest.get("schema") != "taiji-package-manifest/v3":
    raise SystemExit("MANIFEST_SCHEMA_INVALID")
deb_name = manifest.get("deb_basename") or manifest.get("deb")
expected_deb = Path(os.environ["EXPECTED_DEB"]).name
if deb_name != expected_deb:
    raise SystemExit("MANIFEST_DEB_MISMATCH")
if manifest.get("deb_sha256") != os.environ["EXPECTED_SHA"]:
    raise SystemExit("MANIFEST_SHA256_MISMATCH")
if manifest.get("version") != os.environ["EXPECTED_VER"]:
    raise SystemExit("MANIFEST_VERSION_MISMATCH")
if manifest.get("architecture") != "amd64":
    raise SystemExit("MANIFEST_ARCHITECTURE_INVALID")
source_commit = manifest.get("source_commit")
if not isinstance(source_commit, str) or not source_commit.islower() or not (7 <= len(source_commit) <= 40) or any(c not in "0123456789abcdef" for c in source_commit):
    raise SystemExit("MANIFEST_SOURCE_COMMIT_INVALID")
helper = policy_path.with_name("compatibility_policy.py")
if not helper.exists():
    helper = Path(__file__).resolve().parents[1] / "compatibility_policy.py"
spec = importlib.util.spec_from_file_location("taiji_policy", helper)
if spec is None or spec.loader is None:
    raise SystemExit("POLICY_HELPER_INVALID")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
policy = module.load_and_validate(policy_path)
policy_sha = module.canonical_sha256(policy)
policy_id = policy["policy_id"]
if manifest.get("compatibility_policy_id") != policy_id:
    raise SystemExit("POLICY_ID_MISMATCH")
if manifest.get("compatibility_policy_sha256") != policy_sha:
    raise SystemExit("POLICY_SHA256_MISMATCH")
print("\t".join((source_commit, policy_id, policy_sha)))
PY
)" || blocked admission "${binding:-MANIFEST_INVALID}" 1
  IFS=$'\t' read -r SOURCE_COMMIT POLICY_ID POLICY_SHA256 <<< "$binding"
}

validate_certification_admission() {
  [ "$ADMISSION_MODE" = certification ] || return 0
  [[ "$CERTIFICATION_CHALLENGE" =~ ^[0-9a-f]{64,128}$ ]] || blocked admission CHALLENGE_REQUIRED 1
  [ "$(id -u)" -eq 0 ] || blocked preflight ROOT_REQUIRED 1
  ADMISSION_CHALLENGE_DIGEST="$(printf '%s' "$CERTIFICATION_CHALLENGE" | sha256sum | awk '{print $1}')" || blocked admission CHALLENGE_DIGEST_FAILED 1
  local challenge_dir="${TAIJI_DEPLOYMENT_CHALLENGE_DIR:-/var/lib/taiji-agent/admission-challenges}"
  local probe="$challenge_dir"
  while [ "$probe" != "/" ]; do
    [ ! -L "$probe" ] || blocked admission CHALLENGE_STORE_SYMLINK 1
    probe="$(dirname -- "$probe")"
  done
  local store_mode store_owner
  if [ -e "$challenge_dir" ]; then
    [ -d "$challenge_dir" ] && [ ! -L "$challenge_dir" ] || blocked admission CHALLENGE_STORE_UNAVAILABLE 1
    store_owner="$(stat -c '%u:%g' -- "$challenge_dir" 2>/dev/null || printf 'invalid')"
    [ "$store_owner" = "0:0" ] || blocked admission CHALLENGE_STORE_OWNER_INVALID 1
  else
    mkdir -p -m 0700 -- "$challenge_dir" || blocked admission CHALLENGE_STORE_UNAVAILABLE 1
    chown 0:0 -- "$challenge_dir" || blocked admission CHALLENGE_STORE_OWNER_INVALID 1
  fi
  chmod 0700 -- "$challenge_dir" || blocked admission CHALLENGE_STORE_UNAVAILABLE 1
  store_mode="$(stat -c '%u:%g:%a' -- "$challenge_dir" 2>/dev/null || printf 'invalid')"
  [ "$store_mode" = "0:0:700" ] || blocked admission CHALLENGE_STORE_PERMISSIONS_INVALID 1
  local challenge_file="$challenge_dir/$ADMISSION_CHALLENGE_DIGEST"
  if [ -e "$challenge_file" ] || [ -L "$challenge_file" ]; then
    blocked admission CHALLENGE_REPLAY 1
  fi
  ( set -o noclobber; : > "$challenge_file" ) 2>/dev/null || blocked admission CHALLENGE_REPLAY 1
  chmod 0600 -- "$challenge_file" || blocked admission CHALLENGE_STORE_UNAVAILABLE 1
  chown 0:0 -- "$challenge_file" || blocked admission CHALLENGE_STORE_OWNER_INVALID 1
  CHALLENGE_RESERVED="$challenge_file"
}

validate_release_admission() {
  [ "$ADMISSION_MODE" = release ] || return 0
  have openssl || blocked preflight OPENSSL_MISSING 1
  [ -n "$RELEASE_EVIDENCE" ] || blocked admission RELEASE_EVIDENCE_REQUIRED 1
  [ -n "$RELEASE_SIGNATURE" ] || blocked admission RELEASE_SIGNATURE_REQUIRED 1
  [ -n "${TAIJI_RELEASE_PUBLIC_KEY:-}" ] || blocked admission RELEASE_PUBLIC_KEY_REQUIRED 1
  validate_regular_file "$RELEASE_EVIDENCE" RELEASE_EVIDENCE_NOT_REGULAR
  validate_regular_file "$RELEASE_SIGNATURE" RELEASE_SIGNATURE_NOT_REGULAR
  validate_regular_file "$TAIJI_RELEASE_PUBLIC_KEY" RELEASE_PUBLIC_KEY_NOT_REGULAR
  local selected_deb selected_sha
  selected_deb="$(selected_deb_path)"
  selected_sha="$(selected_deb_sha256)"
  validate_regular_file "$selected_deb.sha256" DEB_SHA256_SIDECAR_NOT_REGULAR
  local release_challenge
  release_challenge="$(python3 - "$RELEASE_EVIDENCE" <<'PY'
import json
import re
import sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("challenge_nonce")
if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64,128}", value):
    raise SystemExit(1)
print(value)
PY
)" || blocked admission RELEASE_EVIDENCE_BINDING_INVALID 1
  ADMISSION_CHALLENGE_DIGEST="$(printf '%s' "$release_challenge" | sha256sum | awk '{print $1}')"
  python3 - "$RELEASE_EVIDENCE" <<'PY' || blocked admission RELEASE_TARGET_BASELINE_FORBIDDEN 1
import json
import sys
from pathlib import Path
evidence = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if "target_baseline_profile_id" in evidence or "target_baseline_sha256" in evidence:
    raise SystemExit(1)
PY
  python3 "$RELEASE_VALIDATOR" release \
    --evidence "$RELEASE_EVIDENCE" \
    --source-commit "$SOURCE_COMMIT" \
    --deb "$selected_deb" \
    --manifest "$BUILD_MANIFEST" \
    --checksum "$selected_deb.sha256" \
    --challenge "$release_challenge" \
    --pre-sign >/dev/null 2>&1 \
    || blocked admission RELEASE_EVIDENCE_BINDING_INVALID 1
  python3 - "$RELEASE_EVIDENCE" "$POLICY_PATH" <<'PY' || blocked admission RELEASE_MAINTAINER_INVALID 1
import importlib.util
import json
import sys
from pathlib import Path
evidence = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
spec = importlib.util.spec_from_file_location("taiji_release_policy", Path(sys.argv[2]).with_name("compatibility_policy.py"))
if spec is None or spec.loader is None:
    raise SystemExit(1)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
policy = helper.load_and_validate(Path(sys.argv[2]))
if evidence.get("maintainer") != policy["package"]["maintainer"]:
    raise SystemExit(1)
PY
  local evidence_fingerprint public_fingerprint
  evidence_fingerprint="$(python3 - "$RELEASE_EVIDENCE" <<'PY'
import json
import re
import sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("signing_public_key_fingerprint")
if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
    raise SystemExit(1)
print(value)
PY
  )" || blocked admission RELEASE_PUBLIC_KEY_FINGERPRINT_INVALID 1
  public_fingerprint="$(openssl pkey -pubin -in "$TAIJI_RELEASE_PUBLIC_KEY" -outform DER 2>/dev/null | openssl dgst -sha256 -r 2>/dev/null | awk '{print $1}')" || blocked admission RELEASE_PUBLIC_KEY_FINGERPRINT_READ_FAILED 1
  [ "$public_fingerprint" = "$evidence_fingerprint" ] || blocked admission RELEASE_PUBLIC_KEY_FINGERPRINT_MISMATCH 1
  openssl dgst -sha256 -verify "$TAIJI_RELEASE_PUBLIC_KEY" -signature "$RELEASE_SIGNATURE" "$RELEASE_EVIDENCE" >/dev/null 2>&1 || blocked admission RELEASE_SIGNATURE_INVALID 1
}

write_admission_record() {
  local record_path="$(dirname -- "$RECEIPT_PATH")/deployment-admission.json"
  export ADMISSION_RECORD_PATH="$record_path" ADMISSION_MODE SOURCE_COMMIT
  export ADMISSION_CHALLENGE_DIGEST POLICY_ID POLICY_SHA256
  export ADMISSION_DEB_BASENAME="$(basename -- "$(selected_deb_path)")" ADMISSION_DEB_SHA256="$(selected_deb_sha256)"
  export ADMISSION_GENERATED_AT_UTC="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  python3 - "$RECEIPT_HELPER" <<'PY'
import importlib.util
import os
import sys
from pathlib import Path
helper_path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("taiji_admission_record_writer", helper_path)
if spec is None or spec.loader is None:
    raise SystemExit(2)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
record = {
    "schema": module.ADMISSION_RECORD_SCHEMA,
    "admission_mode": os.environ["ADMISSION_MODE"],
    "challenge_digest": os.environ["ADMISSION_CHALLENGE_DIGEST"],
    "source_commit": os.environ["SOURCE_COMMIT"],
    "deb_basename": os.environ["ADMISSION_DEB_BASENAME"],
    "deb_sha256": os.environ["ADMISSION_DEB_SHA256"],
    "compatibility_policy_id": os.environ["POLICY_ID"],
    "compatibility_policy_sha256": os.environ["POLICY_SHA256"],
    "generated_at_utc": os.environ["ADMISSION_GENERATED_AT_UTC"],
}
module.write_admission_record_atomic(os.environ["ADMISSION_RECORD_PATH"], record)
PY
}

read_dpkg_status() {
  local status version
  if ! have dpkg-query; then
    printf 'unknown\n'
    return 0
  fi
  status="$(dpkg-query -W -f='${db:Status-Status}' taiji-agent 2>/dev/null || true)"
  case "$status" in
    installed|unpacked|half-configured|not-installed|config-files|triggers-awaited|triggers-pending) printf '%s\n' "$status" ;;
    *) printf 'unknown\n' ;;
  esac
}

read_dpkg_version() {
  dpkg-query -W -f='${Version}' taiji-agent 2>/dev/null || true
}

preflight() {
  [ "$(uname -s)" = "Linux" ] || blocked preflight LINUX_REQUIRED 1
  case "$(uname -m)" in x86_64|amd64) ;; *) blocked preflight AMD64_REQUIRED 1 ;; esac
  [ "$(id -u)" -eq 0 ] || blocked preflight ROOT_REQUIRED 1
  for command_name in python3 sha256sum dpkg dpkg-deb dpkg-query flock stat mktemp install chown; do
    have "$command_name" || blocked preflight "${command_name^^}_MISSING" 1
  done
  [ -r /run/lock ] || blocked preflight LOCK_DIRECTORY_UNAVAILABLE 1
  PREFLIGHT="PASS"
}

acquire_lock() {
  local lock_path="/run/lock/taiji-agent-deploy.lock"
  exec {LOCK_FD}>"$lock_path" || blocked lock LOCK_UNAVAILABLE 1
  flock -n "$LOCK_FD" || blocked lock LOCK_CONFLICT 1
}

reserve_admission_and_validate() {
  read_manifest_binding
  validate_certification_admission
  validate_release_admission
  write_admission_record || blocked admission ADMISSION_RECORD_WRITE_FAILED 1
}

install_local_deb() {
  DPKG_STATUS_BEFORE="$(read_dpkg_status)"
  VERSION_BEFORE="$(read_dpkg_version)"
  local dpkg_rc=0
  DPKG_MUTATION_STARTED=1
  DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a dpkg --install --force-confold -- "$DEB_PATH" >/dev/null 2>&1 || dpkg_rc=$?
  DPKG_STATUS_AFTER="$(read_dpkg_status)"
  VERSION_AFTER="$(read_dpkg_version)"
  if [ "$dpkg_rc" -ne 0 ]; then
    manual_recovery dpkg DPKG_INSTALL_FAILED "$dpkg_rc"
  fi
  local verifier="/opt/taiji-agent/bin/taiji-native-verify"
  if [ -x "$verifier" ]; then
    if "$verifier" >/dev/null 2>&1; then
      NATIVE_VERIFY="PASS"
    else
      NATIVE_VERIFY="FAIL"
      manual_recovery native_verify NATIVE_VERIFY_FAILED 1
    fi
  else
    NATIVE_VERIFY="NOT_RUN"
  fi
  case "$OPERATION" in
    fresh_install) RESULT="installed" ;;
    reinstall) RESULT="reinstalled" ;;
    upgrade) RESULT="upgraded" ;;
    rollback) RESULT="rolled_back" ;;
  esac
  ERROR_STAGE=""
  ERROR_CODE=""
  finish 0
}

main() {
  parse_args "$@" || finish 2
  validate_basic_args
  validate_expected_sha256_only
  reserve_admission_and_validate
  preflight
  validate_candidate_hash_and_version
  acquire_lock
  stage_candidate_for_install
  install_local_deb
}

main "$@"
