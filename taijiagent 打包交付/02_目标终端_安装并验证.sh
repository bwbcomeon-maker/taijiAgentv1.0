#!/usr/bin/env bash
set -Eeuo pipefail

# 这是制包/认证管理面的薄封装。它不复制任何管理脚本到客户目录，
# 不维护 apt 源，也不提供在线回退；客户交付物仍然只有固定的单一 DEB。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_SILENT_DEPLOY="$SCRIPT_DIR/../packaging/linux/deb/taiji-silent-deploy.sh"
DELIVERY_SILENT_DEPLOY="$SCRIPT_DIR/验收工具/management/taiji-silent-deploy.sh"
if [ -f "$DELIVERY_SILENT_DEPLOY" ]; then
  SILENT_DEPLOY="$DELIVERY_SILENT_DEPLOY"
else
  SILENT_DEPLOY="$SOURCE_SILENT_DEPLOY"
fi
OUTPUT_DIR="${TAIJI_OUTPUT_DIR:-$SCRIPT_DIR/生成的安装包}"
RECEIPT_PATH="${TAIJI_RECEIPT_PATH:-$SCRIPT_DIR/安装回执.json}"
ADMISSION_MODE="${TAIJI_ADMISSION_MODE:-certification}"
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
  if [ "$OPERATION" = "rollback" ]; then
    [ -n "${TAIJI_PREVIOUS_VERSION:-}" ] || fail "rollback 模式缺少 TAIJI_PREVIOUS_VERSION"
    [ -n "${TAIJI_PREVIOUS_MANIFEST:-}" ] || fail "rollback 模式缺少 TAIJI_PREVIOUS_MANIFEST"
    DEPLOY_ARGS+=(
      --previous-version "$TAIJI_PREVIOUS_VERSION"
      --previous-manifest "$TAIJI_PREVIOUS_MANIFEST"
    )
  elif [ -n "${TAIJI_PREVIOUS_VERSION:-}${TAIJI_PREVIOUS_MANIFEST:-}" ]; then
    fail "previous candidate 只允许用于 rollback"
  fi
}

main() {
  require_file "$SILENT_DEPLOY"
  require_file "$MANIFEST_PATH"
  require_file "$POLICY_PATH"
  select_deb
  build_args
  mkdir -p -- "$(dirname -- "$RECEIPT_PATH")"
  if [ "$(id -u)" -eq 0 ]; then
    exec bash "$SILENT_DEPLOY" "${DEPLOY_ARGS[@]}"
  fi
  # The management wrapper is the only place that requests elevation.  The
  # silent deployer itself runs as root and always writes a 0600 receipt.
  exec sudo env \
    "TAIJI_DEPLOYMENT_CHALLENGE_DIR=${TAIJI_DEPLOYMENT_CHALLENGE_DIR:-/var/lib/taiji-agent/admission-challenges}" \
    "TAIJI_RELEASE_PUBLIC_KEY=${TAIJI_RELEASE_PUBLIC_KEY:-}" \
    bash "$SILENT_DEPLOY" "${DEPLOY_ARGS[@]}"
}

main "$@"
