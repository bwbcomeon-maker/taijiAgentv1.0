#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PUBLIC_KEY="$ROOT_DIR/tools/taiji-release-evidence/signing-public.pem"
EXPECTED_FINGERPRINT="839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
DELIVERY_DIR="$ROOT_DIR/taijiagent 打包交付"
VALIDATOR="$ROOT_DIR/scripts/validate-taiji-release-evidence.py"

fail() {
  printf 'release-evidence-sign-failed\t%s\n' "$*" >&2
  exit 1
}

[ "$#" -eq 2 ] || fail "用法: $0 <evidence.json> <offline-release-private-key.pem>"
EVIDENCE="$1"
PRIVATE_KEY="$2"
SIGNATURE="${EVIDENCE}.sig"

command -v openssl >/dev/null 2>&1 || fail "缺少 openssl"
command -v python3 >/dev/null 2>&1 || fail "缺少 python3"
[ -f "$EVIDENCE" ] && [ ! -L "$EVIDENCE" ] || fail "证据必须是普通 JSON 文件且不能是符号链接"
[ -f "$PRIVATE_KEY" ] && [ ! -L "$PRIVATE_KEY" ] || fail "发布私钥必须是普通文件且不能是符号链接"
[ -f "$PUBLIC_KEY" ] && [ ! -L "$PUBLIC_KEY" ] || fail "仓库缺少固定验签公钥"
python3 - "$PRIVATE_KEY" <<'PY' \
  || fail "发布私钥必须由当前用户独占，权限只能是 0400/0600、不能是硬链接，且不能经过非 root 所有的祖先符号链接"
import os
import stat
import sys


key_path = os.path.abspath(sys.argv[1])
current = os.path.dirname(key_path)
while True:
    ancestor_stat = os.lstat(current)
    if stat.S_ISLNK(ancestor_stat.st_mode):
        if ancestor_stat.st_uid != 0:
            raise SystemExit("private key crosses a non-root-owned ancestor symlink")
    elif not stat.S_ISDIR(ancestor_stat.st_mode):
        raise SystemExit("private key ancestor is not a directory")
    parent = os.path.dirname(current)
    if parent == current:
        break
    current = parent

key_stat = os.lstat(key_path)
mode = stat.S_IMODE(key_stat.st_mode)
if (
    not stat.S_ISREG(key_stat.st_mode)
    or key_stat.st_uid != os.getuid()
    or key_stat.st_nlink != 1
    or mode not in {0o400, 0o600}
):
    raise SystemExit(1)
PY

metadata="$(python3 - "$EVIDENCE" <<'PY'
import json
import os
import stat
import sys


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


evidence_path = sys.argv[1]
evidence_stat = os.lstat(evidence_path)
if (
    not stat.S_ISREG(evidence_stat.st_mode)
    or evidence_stat.st_nlink != 1
    or evidence_stat.st_size <= 0
    or evidence_stat.st_size > 1024 * 1024
):
    raise SystemExit("evidence must be a bounded single-link regular file")
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(evidence_path, flags)
try:
    chunks = []
    remaining = evidence_stat.st_size
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
finally:
    os.close(descriptor)
raw = b"".join(chunks)
if len(raw) != evidence_stat.st_size:
    raise SystemExit("evidence changed while being read")
payload = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
if type(payload) is not dict:
    raise SystemExit("top-level evidence must be an object")
evidence_type = payload.get("evidence_type")
mode = {
    "offline-install-rehearsal": "offline",
    "target-desktop-verification": "target",
}.get(evidence_type)
challenge = payload.get("challenge_nonce")
if mode is None or type(challenge) is not str:
    raise SystemExit("unsupported evidence_type or missing challenge_nonce")
print(f"{mode}\t{challenge}")
PY
 )" || fail "证据 JSON 无法严格解析"
IFS=$'\t' read -r MODE CHALLENGE <<< "$metadata"
if [ "$MODE" = "offline" ]; then
  EXPECTED_CHALLENGE="${TAIJI_OFFLINE_REHEARSAL_CHALLENGE:-}"
else
  EXPECTED_CHALLENGE="${TAIJI_TARGET_ACCEPTANCE_CHALLENGE:-}"
fi
case "$EXPECTED_CHALLENGE" in
  ""|*[!0-9a-f]*) fail "签名前必须独立提供本次 64-128 位小写十六进制 challenge" ;;
esac
[ "${#EXPECTED_CHALLENGE}" -ge 64 ] && [ "${#EXPECTED_CHALLENGE}" -le 128 ] \
  || fail "签名前必须独立提供本次 64-128 位小写十六进制 challenge"
[ "$CHALLENGE" = "$EXPECTED_CHALLENGE" ] \
  || fail "证据 challenge 与签名前独立提供的本次 challenge 不一致"

if ! public_fingerprint="$(openssl pkey -pubin -in "$PUBLIC_KEY" -outform DER 2>/dev/null | openssl dgst -sha256 -r | awk '{print $1}')"; then
  fail "无法读取固定验签公钥"
fi
[ "$public_fingerprint" = "$EXPECTED_FINGERPRINT" ] || fail "固定验签公钥 fingerprint 不匹配"
if ! private_fingerprint="$(openssl pkey -in "$PRIVATE_KEY" -pubout -outform DER 2>/dev/null | openssl dgst -sha256 -r | awk '{print $1}')"; then
  fail "无法读取发布私钥"
fi
[ -n "$private_fingerprint" ] || fail "无法读取发布私钥"
[ "$private_fingerprint" = "$public_fingerprint" ] || fail "发布私钥与产品固定验签公钥不匹配"

TAIJI_RELEASE_SKIP_GIT_CHECK=0 \
TAIJI_RELEASE_REQUIRE_ARTIFACTS=1 \
TAIJI_REPO_ROOT="$ROOT_DIR" \
  bash "$DELIVERY_DIR/01_制包机_发布预检.sh" \
  || fail "真实发布预检未通过，拒绝签名"

commit="$(git -C "$ROOT_DIR" rev-parse --short=8 HEAD)"
deb_count="$(find "$DELIVERY_DIR/生成的安装包" -maxdepth 1 -type f -name 'taiji-agent_*.deb' | wc -l | tr -d ' ')"
[ "$deb_count" = "1" ] || fail "当前 DEB 数量必须为 1"
deb="$(find "$DELIVERY_DIR/生成的安装包" -maxdepth 1 -type f -name 'taiji-agent_*.deb' | head -n 1)"
validator_args=(
  "$MODE"
  --evidence "$EVIDENCE"
  --source-commit "$commit"
  --deb "$deb"
  --checksum "${deb}.sha256"
  --manifest "$DELIVERY_DIR/生成的安装包/taiji-package-manifest.json"
  --build-marker "$DELIVERY_DIR/生成的安装包/.build-success"
  --source-archive "$DELIVERY_DIR/taiji-agentv1.0-kylin-build-src-$commit.tar.gz"
  --packages "$DELIVERY_DIR/离线依赖/Packages"
  --packages-gz "$DELIVERY_DIR/离线依赖/Packages.gz"
  --delivery-dir "$DELIVERY_DIR"
  --attestation-public-key "$PUBLIC_KEY"
  --attestation-public-key-fingerprint "$EXPECTED_FINGERPRINT"
  --challenge "$EXPECTED_CHALLENGE"
)
python3 "$VALIDATOR" "${validator_args[@]}" --pre-sign \
  || fail "证据内容/交付清单预签校验未通过"

python3 - "$PRIVATE_KEY" "$MODE" "$EXPECTED_CHALLENGE" "$EVIDENCE" <<'PY' \
  || fail "本次 challenge 已使用或发布私钥目录不安全；请生成新 challenge 后重新验收"
import hashlib
import os
from pathlib import Path
import stat
import sys
from datetime import datetime, timezone


private_key = Path(sys.argv[1])
mode = sys.argv[2]
challenge = sys.argv[3]
evidence = Path(sys.argv[4])
key_parent = private_key.parent
directory_flags = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
parent_descriptor = os.open(key_parent, directory_flags)
try:
    parent_stat = os.fstat(parent_descriptor)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or stat.S_IMODE(parent_stat.st_mode) & 0o077
    ):
        raise SystemExit("unsafe private-key directory")
    state_name = ".taiji-release-evidence-used-challenges"
    try:
        os.mkdir(state_name, mode=0o700, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except FileExistsError:
        pass
    state_descriptor = os.open(state_name, directory_flags, dir_fd=parent_descriptor)
    state_stat = os.fstat(state_descriptor)
    if (
        not stat.S_ISDIR(state_stat.st_mode)
        or state_stat.st_uid != os.getuid()
        or stat.S_IMODE(state_stat.st_mode) != 0o700
    ):
        raise SystemExit("unsafe challenge state directory")
    evidence_hash = hashlib.sha256(evidence.read_bytes()).hexdigest()
    record = (
        f"mode={mode}\n"
        f"challenge={challenge}\n"
        f"evidence_sha256={evidence_hash}\n"
        f"reserved_at_utc={datetime.now(timezone.utc).isoformat()}\n"
    ).encode("ascii")
    record_name = f"{mode}-{challenge}.used"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    record_descriptor = os.open(record_name, flags, 0o600, dir_fd=state_descriptor)
    try:
        written = 0
        while written < len(record):
            written += os.write(record_descriptor, record[written:])
        os.fsync(record_descriptor)
    finally:
        os.close(record_descriptor)
    os.fsync(state_descriptor)
finally:
    if "state_descriptor" in locals():
        os.close(state_descriptor)
    os.close(parent_descriptor)
PY

umask 077
tmp_signature="$(mktemp "${SIGNATURE}.tmp.XXXXXX")"
cleanup() {
  rm -f "$tmp_signature"
}
trap cleanup EXIT
openssl dgst -sha256 -sign "$PRIVATE_KEY" -out "$tmp_signature" "$EVIDENCE" \
  || fail "证据签名失败"
openssl dgst -sha256 -verify "$PUBLIC_KEY" -signature "$tmp_signature" "$EVIDENCE" >/dev/null \
  || fail "证据签名回读验证失败"
chmod 0644 "$tmp_signature"
python3 "$VALIDATOR" "${validator_args[@]}" --attestation-signature "$tmp_signature" \
  || fail "签名后的完整证据门禁未通过"
mv -f "$tmp_signature" "$SIGNATURE"
trap - EXIT
printf 'release-evidence-signed\t%s\n' "$SIGNATURE"
