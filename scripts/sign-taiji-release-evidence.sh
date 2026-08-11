#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TRUSTED_GIT="$ROOT_DIR/scripts/taiji-trusted-git"
PUBLIC_KEY="$ROOT_DIR/tools/taiji-release-evidence/signing-public.pem"
EXPECTED_FINGERPRINT="839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"

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
[ -x "$TRUSTED_GIT" ] && [ ! -L "$TRUSTED_GIT" ] || fail "仓库缺少可信 Git 边界"
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

umask 077
SNAPSHOT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/taiji-sign-snapshot.XXXXXX")" \
  || fail "无法创建签名私有快照目录"
SNAPSHOT_EVIDENCE="$SNAPSHOT_ROOT/evidence.json"
tmp_signature=""
cleanup_signer() {
  [ -z "$tmp_signature" ] || rm -f -- "$tmp_signature"
  [ -z "$SNAPSHOT_ROOT" ] || rm -rf -- "$SNAPSHOT_ROOT"
}
trap cleanup_signer EXIT

python3 - "$EVIDENCE" "$SNAPSHOT_EVIDENCE" <<'PY' \
  || fail "证据无法复制到签名私有快照"
import os
import stat
import sys


source, destination = sys.argv[1:]
before = os.lstat(source)
if (
    not stat.S_ISREG(before.st_mode)
    or before.st_nlink != 1
    or before.st_size <= 0
    or before.st_size > 1024 * 1024
):
    raise SystemExit("evidence must be a bounded single-link regular file")
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(source, flags)
try:
    opened = os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
        raise SystemExit("evidence changed before snapshot")
    output = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise SystemExit("evidence changed while snapshotting")
            view = memoryview(chunk)
            while view:
                written = os.write(output, view)
                if written <= 0:
                    raise SystemExit("evidence snapshot write failed")
                view = view[written:]
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise SystemExit("evidence grew while snapshotting")
        os.fsync(output)
    finally:
        os.close(output)
    after = os.fstat(descriptor)
finally:
    os.close(descriptor)
identity = lambda value: (
    value.st_dev,
    value.st_ino,
    value.st_mode,
    value.st_nlink,
    value.st_size,
    value.st_mtime_ns,
    value.st_ctime_ns,
)
if identity(opened) != identity(after):
    raise SystemExit("evidence changed while snapshotting")
PY

metadata="$(python3 - "$SNAPSHOT_EVIDENCE" <<'PY'
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
schema = payload.get("schema")
mode = {
    "taiji-linux-certification-set/v1": "certification",
    "taiji-release-evidence/v3": "publication",
}.get(schema)
challenge = payload.get("challenge_nonce")
if mode is None or type(challenge) is not str:
    raise SystemExit("当前 signer 只接受 certification-set v1 或 release-evidence v3")
print(f"{mode}\t{challenge}")
PY
 )" || fail "证据 JSON 无法严格解析"
IFS=$'\t' read -r MODE CHALLENGE <<< "$metadata"
case "$MODE" in
  certification) EXPECTED_CHALLENGE="${TAIJI_CERTIFICATION_CHALLENGE:-}" ;;
  publication) EXPECTED_CHALLENGE="${TAIJI_PUBLICATION_CHALLENGE:-}" ;;
  *) fail "当前 signer 只接受 taiji-linux-certification-set/v1 或 taiji-release-evidence/v3" ;;
esac
case "$EXPECTED_CHALLENGE" in
  ""|*[!0-9a-f]*) fail "签名前必须独立提供本次 64-128 位小写十六进制 challenge" ;;
esac
[ "${#EXPECTED_CHALLENGE}" -ge 64 ] && [ "${#EXPECTED_CHALLENGE}" -le 128 ] \
  || fail "签名前必须独立提供本次 64-128 位小写十六进制 challenge"
[ "$CHALLENGE" = "$EXPECTED_CHALLENGE" ] \
  || fail "证据 challenge 与签名前独立提供的本次 challenge 不一致"
[ ! -e "$SIGNATURE" ] && [ ! -L "$SIGNATURE" ] || fail "签名输出已存在，拒绝覆盖：$SIGNATURE"

if ! public_fingerprint="$(openssl pkey -pubin -in "$PUBLIC_KEY" -outform DER 2>/dev/null | openssl dgst -sha256 -r | awk '{print $1}')"; then
  fail "无法读取固定验签公钥"
fi
[ "$public_fingerprint" = "$EXPECTED_FINGERPRINT" ] || fail "固定验签公钥 fingerprint 不匹配"

if [ "$MODE" = "publication" ]; then
  python3 - "$ROOT_DIR" "$EVIDENCE" "$SNAPSHOT_ROOT" <<'PY' \
    || fail "publication physical bundle 无法创建不可替换的完整私有快照"
import os
import stat
import sys
from pathlib import Path


root = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).absolute()
snapshot = Path(sys.argv[3]).absolute()
canonical_evidence = root / "taijiagent 打包交付/release-evidence.json"
if evidence != canonical_evidence or evidence.parent.resolve() != evidence.parent:
    raise SystemExit("publication evidence is not at the canonical delivery path")
source_root = evidence.parent
destination_root = snapshot / "delivery"
excluded_root_directories = {
    "offline-install-rehearsal",
    "target-verification",
    "构建日志",
    "诊断报告",
    "旧版备份",
}
excluded_root_files = {"release-evidence.json", "release-evidence.json.sig"}
total_bytes = 0


def identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def copy_file(source, destination):
    global total_bytes
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size <= 0:
        raise SystemExit("publication snapshot source is not a single-link regular file")
    if before.st_size > 2 * 1024 * 1024 * 1024:
        raise SystemExit("publication snapshot source file is excessive")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(str(source), flags)
    try:
        opened = os.fstat(source_fd)
        if identity(before) != identity(opened):
            raise SystemExit("publication snapshot source changed before copy")
        destination_fd = os.open(
            str(destination),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            remaining = opened.st_size
            while remaining:
                chunk = os.read(source_fd, min(1024 * 1024, remaining))
                if not chunk:
                    raise SystemExit("publication snapshot source was truncated")
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise SystemExit("publication snapshot write failed")
                    view = view[written:]
                remaining -= len(chunk)
            if os.read(source_fd, 1):
                raise SystemExit("publication snapshot source grew")
            os.fchmod(destination_fd, stat.S_IMODE(opened.st_mode))
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        after = os.fstat(source_fd)
        current = source.lstat()
        if identity(opened) != identity(after) or identity(opened) != identity(current):
            raise SystemExit("publication snapshot source changed during copy")
    finally:
        os.close(source_fd)
    total_bytes += opened.st_size
    if total_bytes > 12 * 1024 * 1024 * 1024:
        raise SystemExit("publication snapshot total size is excessive")


def selected_names(source, at_root):
    names = []
    for entry in source.iterdir():
        if at_root and entry.name in excluded_root_directories and entry.is_dir():
            continue
        if at_root and entry.name in excluded_root_files:
            continue
        names.append(entry.name)
    return sorted(names)


def copy_tree(source, destination, at_root=False):
    before = source.lstat()
    if not stat.S_ISDIR(before.st_mode) or source.is_symlink():
        raise SystemExit("publication snapshot source directory is unsafe")
    destination.mkdir(mode=0o700)
    os.chmod(str(destination), stat.S_IMODE(before.st_mode))
    names = selected_names(source, at_root)
    for name in names:
        entry = source / name
        metadata = entry.lstat()
        target = destination / name
        if stat.S_ISDIR(metadata.st_mode) and not entry.is_symlink():
            copy_tree(entry, target)
        elif stat.S_ISREG(metadata.st_mode):
            copy_file(entry, target)
        else:
            raise SystemExit("publication snapshot contains an unsupported node")
    if selected_names(source, at_root) != names or identity(before) != identity(source.lstat()):
        raise SystemExit("publication snapshot source directory changed during copy")
    directory_fd = os.open(
        str(destination),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


copy_tree(source_root, destination_root, at_root=True)
PY

  python3 - "$ROOT_DIR" "$SNAPSHOT_ROOT/delivery" "$SNAPSHOT_EVIDENCE" "$EXPECTED_CHALLENGE" <<'PY' \
    || fail "publication physical bundle 未通过完整实物和签名前合同校验，拒绝读取私钥"
import argparse
import hashlib
import importlib.util
import sys
from pathlib import Path


root = Path(sys.argv[1]).resolve()
delivery = Path(sys.argv[2]).resolve()
evidence = Path(sys.argv[3]).resolve()
challenge = sys.argv[4]
validator_path = root / "scripts/validate-taiji-release-evidence.py"
spec = importlib.util.spec_from_file_location("taiji_signer_publication_validator", validator_path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load publication validator")
validator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validator
spec.loader.exec_module(validator)
payload, _ = validator.read_regular_bytes(evidence, "publication evidence")
data = validator.parse_json_bytes(payload, "publication evidence")
source_commit = data.get("source_commit")
deb_basename = data.get("deb_basename")
if type(source_commit) is not str or validator.FULL_COMMIT_RE.fullmatch(source_commit) is None:
    raise SystemExit("publication source_commit is invalid")
if type(deb_basename) is not str or Path(deb_basename).name != deb_basename:
    raise SystemExit("publication DEB basename is invalid")
package_root = delivery / "生成的安装包"
manifest = package_root / "taiji-package-manifest.json"
deb = package_root / deb_basename
args = argparse.Namespace(
    source_commit=source_commit,
    deb=deb,
    manifest=manifest,
    checksum=package_root / (deb_basename + ".sha256"),
    build_marker=package_root / ".build-success",
    source_archive=delivery / ("taiji-agentv1.0-kylin-build-src-" + source_commit + ".tar.gz"),
    delivery_dir=delivery,
    challenge=challenge,
)
binding = validator.validate_build_binding(args)
if not isinstance(binding, validator.BuildBinding):
    raise SystemExit("publication validation did not produce a v3 BuildBinding")
validator.validate_release_evidence_v3(data, args, binding)

if data.get("certification_set_basename") != "certification-set.json":
    raise SystemExit("publication certification basename is not canonical")
if data.get("certification_set_signature_basename") != "certification-set.json.sig":
    raise SystemExit("publication certification signature basename is not canonical")
certification = delivery / "certification/certification-set.json"
certification_signature = delivery / "certification/certification-set.json.sig"
certification_payload, _ = validator.read_regular_bytes(certification, "certification set")
signature_payload, _ = validator.read_regular_bytes(
    certification_signature,
    "certification signature",
    limit=64 * 1024,
)
if hashlib.sha256(certification_payload).hexdigest() != data["certification_set_sha256"]:
    raise SystemExit("publication certification hash binding mismatch")
if hashlib.sha256(signature_payload).hexdigest() != data["certification_set_signature_sha256"]:
    raise SystemExit("publication certification signature hash binding mismatch")
validator.validate_attestation(
    argparse.Namespace(
        attestation_public_key=root / "tools/taiji-release-evidence/signing-public.pem",
        attestation_signature=certification_signature,
        attestation_public_key_fingerprint=validator.PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT,
    ),
    certification_payload,
)
certification_data = validator.parse_json_bytes(certification_payload, "certification set")
certification_challenge = certification_data.get("challenge_nonce")
if (
    type(certification_challenge) is not str
    or validator.CHALLENGE_RE.fullmatch(certification_challenge) is None
    or certification_challenge == challenge
):
    raise SystemExit("certification challenge is invalid or reused for publication")
validator.validate_certification_set_v1(
    certification_data,
    certification,
    argparse.Namespace(
        challenge=certification_challenge,
        matrix=delivery / "验收工具/certification-matrix.json",
        manifest=manifest,
    ),
    binding,
)
PY
fi

if ! private_fingerprint="$(openssl pkey -in "$PRIVATE_KEY" -pubout -outform DER 2>/dev/null | openssl dgst -sha256 -r | awk '{print $1}')"; then
  fail "无法读取发布私钥"
fi
[ -n "$private_fingerprint" ] || fail "无法读取发布私钥"
[ "$private_fingerprint" = "$public_fingerprint" ] || fail "发布私钥与产品固定验签公钥不匹配"

if [ "$MODE" = "certification" ]; then
  python3 - "$EVIDENCE" "$SNAPSHOT_ROOT" <<'PY' \
    || fail "certification-set 物理证据树无法创建不可替换快照"
import os
import stat
import sys
from pathlib import Path


evidence = Path(sys.argv[1]).absolute()
snapshot = Path(sys.argv[2]).absolute()
source_root = evidence.parent
total_bytes = 0


def identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def copy_file(source, destination):
    global total_bytes
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size <= 0:
        raise SystemExit("certification snapshot source is not a single-link regular file")
    if before.st_size > 1024 * 1024 * 1024:
        raise SystemExit("certification snapshot source file is excessive")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, flags)
    try:
        opened = os.fstat(source_fd)
        if identity(before) != identity(opened):
            raise SystemExit("certification snapshot source changed before copy")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            remaining = opened.st_size
            while remaining:
                chunk = os.read(source_fd, min(1024 * 1024, remaining))
                if not chunk:
                    raise SystemExit("certification snapshot source was truncated")
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise SystemExit("certification snapshot write failed")
                    view = view[written:]
                remaining -= len(chunk)
            if os.read(source_fd, 1):
                raise SystemExit("certification snapshot source grew")
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        after = os.fstat(source_fd)
    finally:
        os.close(source_fd)
    if identity(opened) != identity(after):
        raise SystemExit("certification snapshot source changed during copy")
    total_bytes += opened.st_size
    if total_bytes > 4 * 1024 * 1024 * 1024:
        raise SystemExit("certification snapshot total size is excessive")


def copy_tree(source, destination):
    before = source.lstat()
    if not stat.S_ISDIR(before.st_mode) or source.is_symlink():
        raise SystemExit("certification snapshot source directory is unsafe")
    destination.mkdir(mode=0o700)
    entries = sorted(source.iterdir(), key=lambda item: item.name)
    for entry in entries:
        metadata = entry.lstat()
        target = destination / entry.name
        if stat.S_ISDIR(metadata.st_mode) and not entry.is_symlink():
            copy_tree(entry, target)
        elif stat.S_ISREG(metadata.st_mode):
            copy_file(entry, target)
        else:
            raise SystemExit("certification snapshot contains an unsupported node")
    after = source.lstat()
    if identity(before) != identity(after):
        raise SystemExit("certification snapshot source directory changed during copy")


for name in ("records", "offline-rehearsal"):
    copy_tree(source_root / name, snapshot / name)
PY

  python3 - "$ROOT_DIR" "$SNAPSHOT_EVIDENCE" "$EXPECTED_CHALLENGE" <<'PY' \
    || fail "certification-set physical bundle 未通过完整实物校验，拒绝签名"
import argparse
import importlib.util
import json
import sys
from pathlib import Path


root = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
challenge = sys.argv[3]
validator_path = root / "scripts/validate-taiji-release-evidence.py"
matrix_path = root / "packaging/linux/certification-matrix.json"
spec = importlib.util.spec_from_file_location("taiji_signer_certification_validator", validator_path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load certification validator")
validator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validator
spec.loader.exec_module(validator)
payload, _ = validator.read_regular_bytes(evidence, "certification set")
data = validator.parse_json_bytes(payload, "certification set")
binding = validator.BuildBinding(
    source_commit=data.get("source_commit", ""),
    version=data.get("version", ""),
    architecture=data.get("architecture", ""),
    deb_basename=data.get("deb_basename", ""),
    deb_sha256=data.get("deb_sha256", ""),
    compatibility_policy_id=data.get("compatibility_policy_id", ""),
    compatibility_policy_sha256=data.get("compatibility_policy_sha256", ""),
    electron_executable_sha256="0" * 64,
    desktop_entry_sha256="0" * 64,
)
args = argparse.Namespace(challenge=challenge, matrix=matrix_path)
validator.validate_certification_set_v1(data, evidence, args, binding)
PY
fi

if [ "$MODE" = "certification" ] || [ "$MODE" = "publication" ]; then
  # Reserve the challenge before the cryptographic write.  The record is
  # owner-only and keyed by mode, so a certification challenge cannot be
  # replayed as a publication challenge (or vice versa).
  python3 - "$PRIVATE_KEY" "$MODE" "$EXPECTED_CHALLENGE" "$SNAPSHOT_EVIDENCE" <<'PY' \
    || fail "本次 challenge 已使用或发布私钥目录不安全；请生成新 challenge 后重新验收"
import hashlib
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

private_key = Path(sys.argv[1])
mode = sys.argv[2]
challenge = sys.argv[3]
evidence = Path(sys.argv[4])
parent = private_key.parent
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
parent_fd = os.open(parent, flags)
try:
    parent_stat = os.fstat(parent_fd)
    if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) & 0o077:
        raise SystemExit("unsafe private-key directory")
    state_name = ".taiji-release-evidence-used-challenges"
    try:
        os.mkdir(state_name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    state_fd = os.open(state_name, flags, dir_fd=parent_fd)
    try:
        state_stat = os.fstat(state_fd)
        if not stat.S_ISDIR(state_stat.st_mode) or state_stat.st_uid != os.getuid() or stat.S_IMODE(state_stat.st_mode) != 0o700:
            raise SystemExit("unsafe challenge state directory")
        record = (
            f"mode={mode}\nchallenge={challenge}\nevidence_sha256={hashlib.sha256(evidence.read_bytes()).hexdigest()}\n"
            f"reserved_at_utc={datetime.now(timezone.utc).isoformat()}\n"
        ).encode("ascii")
        record_fd = os.open(
            f"{mode}-{challenge}.used",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=state_fd,
        )
        try:
            view = memoryview(record)
            while view:
                written = os.write(record_fd, view)
                if written <= 0:
                    raise SystemExit("challenge record write failed")
                view = view[written:]
            os.fsync(record_fd)
        finally:
            os.close(record_fd)
        os.fsync(state_fd)
    finally:
        os.close(state_fd)
finally:
    os.close(parent_fd)
PY

  tmp_signature="$(mktemp "${SIGNATURE}.tmp.XXXXXX")"
  openssl dgst -sha256 -sign "$PRIVATE_KEY" -out "$tmp_signature" "$SNAPSHOT_EVIDENCE" || fail "证据签名失败"
  openssl dgst -sha256 -verify "$PUBLIC_KEY" -signature "$tmp_signature" "$SNAPSHOT_EVIDENCE" >/dev/null \
    || fail "证据签名回读验证失败"
  chmod 0644 "$tmp_signature"
  python3 - "$tmp_signature" "$SIGNATURE" <<'PY' \
    || fail "签名输出已被替换，拒绝覆盖"
import os
import sys
source, destination = sys.argv[1:]
try:
    os.link(source, destination)
except FileExistsError:
    raise SystemExit(1)
os.unlink(source)
PY
  tmp_signature=""
  if ! openssl dgst -sha256 -verify "$PUBLIC_KEY" -signature "$SIGNATURE" "$EVIDENCE" >/dev/null; then
    rm -f -- "$SIGNATURE"
    fail "原证据在签名期间发生变化，已删除不再匹配的签名"
  fi
  printf 'release-evidence-signed\t%s\n' "$SIGNATURE"
  exit 0
fi

fail "当前 signer 只接受 certification-set v1 或 release-evidence v3"
