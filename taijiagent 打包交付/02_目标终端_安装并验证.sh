#!/usr/bin/env bash
set -Eeuo pipefail

# 这是制包/认证管理面的薄封装。它不复制任何管理脚本到客户目录，
# 不维护 apt 源，也不提供在线回退；客户交付物仍然只有固定的单一 DEB。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_SILENT_DEPLOY="$SCRIPT_DIR/../packaging/linux/deb/taiji-silent-deploy.sh"
DELIVERY_SILENT_DEPLOY="$SCRIPT_DIR/验收工具/management/taiji-silent-deploy.sh"
RECEIPT_HELPER="$SCRIPT_DIR/验收工具/management/deployment_receipt.py"
UPGRADE_TRANSACTION_HELPER="$SCRIPT_DIR/验收工具/management/upgrade_transaction.py"
UPGRADE_CONTRACT_PATH="$SCRIPT_DIR/验收工具/management/upgrade-data-contract.json"
COMPATIBILITY_HELPER="$SCRIPT_DIR/验收工具/management/compatibility_policy.py"
RELEASE_VALIDATOR="$SCRIPT_DIR/验收工具/management/validate-taiji-release-evidence.py"
[ -f "$RECEIPT_HELPER" ] || RECEIPT_HELPER="$SCRIPT_DIR/../packaging/linux/deployment_receipt.py"
[ -f "$UPGRADE_TRANSACTION_HELPER" ] || UPGRADE_TRANSACTION_HELPER="$SCRIPT_DIR/../packaging/linux/upgrade_transaction.py"
[ -f "$UPGRADE_CONTRACT_PATH" ] || UPGRADE_CONTRACT_PATH="$SCRIPT_DIR/../packaging/linux/upgrade-data-contract.json"
[ -f "$COMPATIBILITY_HELPER" ] || COMPATIBILITY_HELPER="$SCRIPT_DIR/../packaging/linux/compatibility_policy.py"
[ -f "$RELEASE_VALIDATOR" ] || RELEASE_VALIDATOR="$SCRIPT_DIR/../scripts/validate-taiji-release-evidence.py"
if [ -f "$DELIVERY_SILENT_DEPLOY" ]; then
  SILENT_DEPLOY="$DELIVERY_SILENT_DEPLOY"
else
  SILENT_DEPLOY="$SOURCE_SILENT_DEPLOY"
fi
OUTPUT_DIR="${TAIJI_OUTPUT_DIR:-$SCRIPT_DIR/生成的安装包}"
RECEIPT_PATH="${TAIJI_RECEIPT_PATH:-$SCRIPT_DIR/安装回执.json}"
ADMISSION_MODE="${TAIJI_ADMISSION_MODE:-certification}"
# certification is reserved for the controlled offline rehearsal path.  A
# customer/sales release must explicitly set TAIJI_ADMISSION_MODE=release and
# provide signed release evidence; the default is not a release claim.
OPERATION="${TAIJI_OPERATION:-fresh_install}"
MANIFEST_PATH="${TAIJI_BUILD_MANIFEST:-$OUTPUT_DIR/taiji-package-manifest.json}"
SOURCE_POLICY_PATH="$SCRIPT_DIR/../packaging/linux/compatibility-policy.json"
DELIVERY_POLICY_PATH="$SCRIPT_DIR/验收工具/management/compatibility-policy.json"
if [ -f "$DELIVERY_POLICY_PATH" ]; then
  POLICY_PATH="${TAIJI_POLICY_PATH:-$DELIVERY_POLICY_PATH}"
else
  POLICY_PATH="${TAIJI_POLICY_PATH:-$SOURCE_POLICY_PATH}"
fi

fail() {
  printf '[FAIL] %s\n' "$*" >&2
  exit 1
}

require_file() {
  [ -f "$1" ] || fail "缺少交付文件：$1"
  [ ! -L "$1" ] || fail "交付文件不能是符号链接：$1"
}

select_deb() {
  local -a candidates=()
  [ -d "$OUTPUT_DIR" ] || fail "缺少生成的安装包目录：$OUTPUT_DIR"
  while IFS= read -r -d '' candidate; do
    candidates+=("$candidate")
  done < <(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' -print0)
  [ "${#candidates[@]}" -eq 1 ] || fail "生成的安装包必须恰好包含一个 amd64 DEB（实际 ${#candidates[@]} 个）"
  DEB_PATH="${candidates[0]}"
  require_file "$DEB_PATH"
  CHECKSUM_PATH="$DEB_PATH.sha256"
  require_file "$CHECKSUM_PATH"
}

read_manifest_value() {
  python3 - "$MANIFEST_PATH" "$1" <<'PY'
import json
import sys
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
value = manifest.get(sys.argv[2])
if not isinstance(value, str) or not value:
    raise SystemExit(2)
print(value)
PY
}

build_args() {
  local expected_version expected_sha actual_sha sidecar_sha sidecar_name challenge
  expected_version="$(read_manifest_value version)" || fail "manifest 缺少 version"
  expected_sha="$(read_manifest_value deb_sha256)" || fail "manifest 缺少 deb_sha256"
  [[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || fail "manifest deb_sha256 格式不合法"
  actual_sha="$(sha256sum -- "$DEB_PATH" | awk '{print $1}')" || fail "无法计算 DEB SHA256"
  [ "$actual_sha" = "$expected_sha" ] || fail "DEB SHA256 与 manifest 不一致"
  sidecar_sha="$(awk 'NR == 1 { print $1; exit }' "$CHECKSUM_PATH")"
  sidecar_name="$(awk 'NR == 1 { $1 = ""; sub(/^[ \t]+\*?/, ""); print; exit }' "$CHECKSUM_PATH")"
  [ "$sidecar_sha" = "$expected_sha" ] || fail "DEB SHA256 sidecar 与 manifest 不一致"
  [ "$(basename -- "$sidecar_name")" = "$(basename -- "$DEB_PATH")" ] || fail "DEB SHA256 sidecar 文件名不匹配"
  challenge="${TAIJI_CERTIFICATION_CHALLENGE:-}"
  DEPLOY_ARGS=(
    --deb "$DEB_PATH"
    --expected-version "$expected_version"
    --expected-sha256 "$expected_sha"
    --admission-mode "$ADMISSION_MODE"
    --operation "$OPERATION"
    --receipt "$RECEIPT_PATH"
    --build-manifest "$MANIFEST_PATH"
    --policy "$POLICY_PATH"
  )
  if [ "$ADMISSION_MODE" = "certification" ]; then
    [ -n "$challenge" ] || fail "认证演练必须显式提供一次性 TAIJI_CERTIFICATION_CHALLENGE"
    DEPLOY_ARGS+=(--certification-challenge "$challenge")
  elif [ "$ADMISSION_MODE" = "release" ]; then
    [ -n "${TAIJI_RELEASE_EVIDENCE:-}" ] || fail "release 模式缺少 TAIJI_RELEASE_EVIDENCE"
    [ -n "${TAIJI_RELEASE_SIGNATURE:-}" ] || fail "release 模式缺少 TAIJI_RELEASE_SIGNATURE"
    DEPLOY_ARGS+=(
      --release-evidence "$TAIJI_RELEASE_EVIDENCE"
      --release-signature "$TAIJI_RELEASE_SIGNATURE"
    )
  else
    fail "不支持的 admission mode：$ADMISSION_MODE"
  fi
  if [ -n "${TAIJI_BUSINESS_USER:-}" ]; then
    DEPLOY_ARGS+=(--business-user "$TAIJI_BUSINESS_USER")
  fi
  if [ -n "${TAIJI_PREVIOUS_DEB:-}" ]; then
    DEPLOY_ARGS+=(--previous-deb "$TAIJI_PREVIOUS_DEB")
  fi
  if [ -n "${TAIJI_PREVIOUS_SHA256:-}" ]; then
    DEPLOY_ARGS+=(--previous-sha256 "$TAIJI_PREVIOUS_SHA256")
  fi
  if [ "$OPERATION" = "upgrade" ] || [ "$OPERATION" = "rollback" ]; then
    [ -n "${TAIJI_PREVIOUS_SIGNATURE:-}" ] || fail "升级/回滚必须提供 TAIJI_PREVIOUS_SIGNATURE"
    DEPLOY_ARGS+=(--previous-signature "$TAIJI_PREVIOUS_SIGNATURE")
  fi
  if [ "$OPERATION" = "upgrade" ] || [ "$OPERATION" = "rollback" ]; then
    [ -n "${TAIJI_PREVIOUS_MANIFEST:-}" ] || fail "升级/回滚必须提供 TAIJI_PREVIOUS_MANIFEST"
    DEPLOY_ARGS+=(--previous-manifest "$TAIJI_PREVIOUS_MANIFEST")
  fi
  if [ "$OPERATION" = "rollback" ]; then
    [ -n "${TAIJI_PREVIOUS_VERSION:-}" ] || fail "rollback 模式缺少 TAIJI_PREVIOUS_VERSION"
    [ -n "${TAIJI_PREVIOUS_MANIFEST:-}" ] || fail "rollback 模式缺少 TAIJI_PREVIOUS_MANIFEST"
    DEPLOY_ARGS+=(
      --previous-version "$TAIJI_PREVIOUS_VERSION"
    )
  elif [ "$OPERATION" != "upgrade" ] && [ -n "${TAIJI_PREVIOUS_VERSION:-}${TAIJI_PREVIOUS_MANIFEST:-}" ]; then
    fail "previous candidate 只允许用于 rollback"
  fi
}

run_root_staged_management() {
  local -a elevate=()
  if [ "$(id -u)" -ne 0 ]; then
    elevate=(sudo env -i)
  else
    elevate=(env -i)
  fi
  "${elevate[@]}" \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 \
    "TAIJI_DEPLOYMENT_CHALLENGE_DIR=${TAIJI_DEPLOYMENT_CHALLENGE_DIR:-/var/lib/taiji-agent/admission-challenges}" \
    "TAIJI_RELEASE_PUBLIC_KEY=${TAIJI_RELEASE_PUBLIC_KEY:-}" \
    bash -s -- \
    "$SILENT_DEPLOY" "$RECEIPT_HELPER" "$UPGRADE_TRANSACTION_HELPER" \
    "$UPGRADE_CONTRACT_PATH" "$COMPATIBILITY_HELPER" "$POLICY_PATH" "$RELEASE_VALIDATOR" "${TAIJI_RELEASE_PUBLIC_KEY:-}" \
    "${DEPLOY_ARGS[@]}" <<'ROOT_STAGED_SCRIPT'
set -Eeuo pipefail
silent_source="$1"
receipt_source="$2"
upgrade_source="$3"
contract_source="$4"
compatibility_source="$5"
policy_source="$6"
release_validator_source="$7"
release_public_source="$8"
shift 8
stage="$(mktemp -d /var/tmp/taiji-agent-management.XXXXXX)"
cleanup() {
  find "$stage" -mindepth 1 -maxdepth 1 -type f -delete 2>/dev/null || true
  rmdir -- "$stage" 2>/dev/null || true
}
trap cleanup EXIT
chmod 0700 -- "$stage"
chown 0:0 -- "$stage"

stage_regular_file() {
  local source="$1" destination="$2" mode="$3"
  mkdir -p -- "$(dirname -- "$destination")"
  python3 - "$source" "$destination" "$mode" <<'PY'
import os
import stat
import sys
from pathlib import Path

source = Path(os.path.abspath(sys.argv[1]))
destination = Path(sys.argv[2])
mode = int(sys.argv[3], 8)
if source.is_symlink():
    raise SystemExit("source_symlink")
for parent in source.parents:
    if parent == Path(parent.anchor):
        break
    try:
        if stat.S_ISLNK(os.lstat(parent).st_mode):
            raise SystemExit("source_symlink_ancestor")
    except FileNotFoundError:
        raise SystemExit("source_parent_missing")
source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
try:
    source_info = os.fstat(source_fd)
    if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink != 1:
        raise SystemExit("source_not_private_regular_file")
    destination_fd = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise SystemExit("destination_write_failed")
                view = view[written:]
        os.fsync(destination_fd)
        current = os.fstat(source_fd)
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
            source_info.st_dev,
            source_info.st_ino,
            source_info.st_size,
            source_info.st_mtime_ns,
        ):
            raise SystemExit("source_changed_during_stage")
    finally:
        os.close(destination_fd)
    os.chmod(destination, mode)
except BaseException:
    try:
        destination.unlink()
    except FileNotFoundError:
        pass
    raise
finally:
    os.close(source_fd)
PY
}

STAGED_DEB_PATH=""
stage_deb_with_sidecar() {
  local source="$1" target
  STAGED_DEB_PATH=""
  target="$stage/$(basename -- "$source")"
  stage_regular_file "$source" "$target" 0600
  stage_regular_file "$source.sha256" "$target.sha256" 0600
  STAGED_DEB_PATH="$target"
}

stage_regular_file "$silent_source" "$stage/taiji-silent-deploy.sh" 0755
stage_regular_file "$receipt_source" "$stage/deployment_receipt.py" 0600
stage_regular_file "$upgrade_source" "$stage/upgrade_transaction.py" 0600
stage_regular_file "$contract_source" "$stage/upgrade-data-contract.json" 0600
stage_regular_file "$compatibility_source" "$stage/compatibility_policy.py" 0600
stage_regular_file "$policy_source" "$stage/compatibility-policy.json" 0600
stage_regular_file "$release_validator_source" "$stage/validate-taiji-release-evidence.py" 0600
if [ -n "$release_public_source" ]; then
  stage_regular_file "$release_public_source" "$stage/signing-public.pem" 0600
  export TAIJI_RELEASE_PUBLIC_KEY="$stage/signing-public.pem"
fi
args=("$@")
for ((index = 0; index < ${#args[@]}; index += 1)); do
  [ $((index + 1)) -lt ${#args[@]} ] || continue
  case "${args[index]}" in
    --deb)
      stage_deb_with_sidecar "${args[index + 1]}"
      args[index + 1]="$STAGED_DEB_PATH"
      ;;
    --build-manifest)
      stage_regular_file "${args[index + 1]}" "$stage/build-manifest.json" 0600
      args[index + 1]="$stage/build-manifest.json"
      ;;
    --policy)
      args[index + 1]="$stage/compatibility-policy.json"
      ;;
    --release-evidence)
      stage_regular_file "${args[index + 1]}" "$stage/release-evidence.json" 0600
      args[index + 1]="$stage/release-evidence.json"
      ;;
    --release-signature)
      stage_regular_file "${args[index + 1]}" "$stage/release-evidence.sig" 0600
      args[index + 1]="$stage/release-evidence.sig"
      ;;
    --previous-deb)
      stage_deb_with_sidecar "${args[index + 1]}"
      args[index + 1]="$STAGED_DEB_PATH"
      ;;
    --previous-signature)
      stage_regular_file "${args[index + 1]}" "$stage/previous.deb.sig" 0600
      args[index + 1]="$stage/previous.deb.sig"
      ;;
    --previous-manifest)
      stage_regular_file "${args[index + 1]}" "$stage/previous-manifest.json" 0600
      args[index + 1]="$stage/previous-manifest.json"
      ;;
  esac
done
set +e
bash "$stage/taiji-silent-deploy.sh" "${args[@]}"
status=$?
set -e
exit "$status"
ROOT_STAGED_SCRIPT
}

main() {
  require_file "$SILENT_DEPLOY"
  require_file "$RECEIPT_HELPER"
  require_file "$UPGRADE_TRANSACTION_HELPER"
  require_file "$UPGRADE_CONTRACT_PATH"
  require_file "$COMPATIBILITY_HELPER"
  require_file "$RELEASE_VALIDATOR"
  require_file "$MANIFEST_PATH"
  require_file "$POLICY_PATH"
  select_deb
  build_args
  mkdir -p -- "$(dirname -- "$RECEIPT_PATH")"
  if [ "$(id -u)" -eq 0 ]; then
    run_root_staged_management
    exit $?
  fi
  # The management wrapper is the only place that requests elevation.  The
  # silent deployer itself runs as root and always writes a 0600 receipt.
  run_root_staged_management
  exit $?
}

main "$@"
