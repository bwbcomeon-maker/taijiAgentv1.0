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

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TRUSTED_GIT="$ROOT_DIR/scripts/taiji-trusted-git"
LIVE_CI_REVALIDATOR="$ROOT_DIR/scripts/revalidate-taiji-github-ci-evidence.py"
CHALLENGE_HELPER="$ROOT_DIR/scripts/taiji-challenge-envelope.py"
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

[ -x /usr/bin/openssl ] || fail "缺少 /usr/bin/openssl"
[ -x /usr/bin/python3 ] || fail "缺少 /usr/bin/python3"
[ -x "$TRUSTED_GIT" ] && [ ! -L "$TRUSTED_GIT" ] || fail "仓库缺少可信 Git 边界"
[ -f "$LIVE_CI_REVALIDATOR" ] && [ ! -L "$LIVE_CI_REVALIDATOR" ] || fail "仓库缺少固定 GitHub CI 实时复验器"
[ -f "$CHALLENGE_HELPER" ] && [ ! -L "$CHALLENGE_HELPER" ] || fail "仓库缺少 canonical challenge-envelope helper"
[ -f "$EVIDENCE" ] && [ ! -L "$EVIDENCE" ] || fail "证据必须是普通 JSON 文件且不能是符号链接"
[ -f "$PRIVATE_KEY" ] && [ ! -L "$PRIVATE_KEY" ] || fail "发布私钥必须是普通文件且不能是符号链接"
[ -f "$PUBLIC_KEY" ] && [ ! -L "$PUBLIC_KEY" ] || fail "仓库缺少固定验签公钥"
/usr/bin/python3 -I -B - "$PRIVATE_KEY" <<'PY' \
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
SNAPSHOT_ENVELOPE="$SNAPSHOT_ROOT/challenge-envelope.json"
tmp_signature=""
cleanup_signer() {
  [ -z "$tmp_signature" ] || rm -f -- "$tmp_signature"
  [ -z "$SNAPSHOT_ROOT" ] || rm -rf -- "$SNAPSHOT_ROOT"
}
trap cleanup_signer EXIT

/usr/bin/python3 -I -B - "$EVIDENCE" "$SNAPSHOT_EVIDENCE" <<'PY' \
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

metadata="$(/usr/bin/python3 -I -B - "$ROOT_DIR" "$SNAPSHOT_EVIDENCE" "$SNAPSHOT_ENVELOPE" <<'PY'
import importlib.util
import json
import os
import re
import stat
import sys


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


root, evidence_path, envelope_path = sys.argv[1:]
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
helper_path = os.path.join(root, "scripts", "taiji-challenge-envelope.py")
spec = importlib.util.spec_from_file_location("taiji_signer_challenge_envelope", helper_path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load canonical challenge-envelope helper")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
schema = payload.get("schema")
mode = {
    "taiji-linux-certification-set/v1": "certification",
    "taiji-release-evidence/v3": "publication",
}.get(schema)
envelope = payload.get("challenge_envelope")
challenge = payload.get("challenge_nonce")
source_commit = payload.get("source_commit", "")
deb_basename = payload.get("deb_basename", "")
deb_sha256 = payload.get("deb_sha256", "")
generated_at = payload.get("generated_at_utc", "")
if mode is None or type(challenge) is not str or type(envelope) is not dict:
    raise SystemExit("当前 signer 只接受 certification-set v1 或 release-evidence v3")
if type(source_commit) is not str or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
    raise SystemExit("source_commit must be a complete lowercase commit")
if type(deb_basename) is not str or helper.DEB_RE.fullmatch(deb_basename) is None:
    raise SystemExit("deb_basename is invalid")
if type(deb_sha256) is not str or helper.SHA256_RE.fullmatch(deb_sha256) is None:
    raise SystemExit("deb_sha256 is invalid")
if type(generated_at) is not str:
    raise SystemExit("generated_at_utc is invalid")
helper.verify_envelope(
    envelope,
    purpose=mode,
    source_commit=source_commit,
    deb_basename=deb_basename,
    deb_sha256=deb_sha256,
    require_active=True,
    evidence_times=(generated_at,),
)
if challenge != envelope["nonce"]:
    raise SystemExit("challenge_nonce does not match canonical envelope")
descriptor = os.open(
    envelope_path,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
try:
    view = memoryview(helper.canonical_bytes(envelope))
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise SystemExit("challenge envelope snapshot write failed")
        view = view[written:]
    os.fsync(descriptor)
finally:
    os.close(descriptor)
print(f"{mode}\t{challenge}\t{source_commit}\t{deb_basename}\t{deb_sha256}\t{generated_at}")
PY
 )" || fail "证据 JSON 无法严格解析"
IFS=$'\t' read -r MODE CHALLENGE SOURCE_COMMIT DEB_BASENAME DEB_SHA256 GENERATED_AT_UTC <<< "$metadata"
case "$MODE" in
  certification|publication) ;;
  *) fail "当前 signer 只接受 taiji-linux-certification-set/v1 或 taiji-release-evidence/v3" ;;
esac
[ ! -e "$SIGNATURE" ] && [ ! -L "$SIGNATURE" ] || fail "签名输出已存在，拒绝覆盖：$SIGNATURE"

if ! public_fingerprint="$(/usr/bin/openssl pkey -pubin -in "$PUBLIC_KEY" -outform DER 2>/dev/null | /usr/bin/openssl dgst -sha256 -r | awk '{print $1}')"; then
  fail "无法读取固定验签公钥"
fi
[ "$public_fingerprint" = "$EXPECTED_FINGERPRINT" ] || fail "固定验签公钥 fingerprint 不匹配"

if [ "$MODE" = "publication" ]; then
  /usr/bin/python3 -I -B - "$EVIDENCE" "$SNAPSHOT_ROOT" <<'PY' \
    || fail "publication physical bundle 无法创建不可替换的完整私有快照"
import os
import stat
import sys
from pathlib import Path


evidence_argument = Path(sys.argv[1])
snapshot = Path(sys.argv[2]).absolute()
if not evidence_argument.is_absolute():
    raise SystemExit("publication evidence must use an absolute real delivery root")
evidence = evidence_argument
if evidence.name != "release-evidence.json":
    raise SystemExit("publication evidence must use fixed basename release-evidence.json")
if evidence.parent.resolve() != evidence.parent:
    raise SystemExit("publication evidence must use an absolute real delivery root")
source_root = evidence.parent
destination_root = snapshot / "delivery"
excluded_root_directories = {
    "offline-install-rehearsal",
    "target-verification",
    "构建日志",
    "诊断报告",
    "旧版备份",
}
excluded_root_files = {"release-evidence.json.sig"}
total_bytes = 0


# TAIJI_PYTHON38_PUBLICATION_TRUST_HELPER_BEGIN
def identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def validate_publication_delivery_root(source, expected_identity=None):
    try:
        leaf_stat = source.lstat()
    except OSError as exc:
        raise SystemExit(f"publication delivery root cannot be inspected: {exc}")
    if (
        source.is_symlink()
        or not stat.S_ISDIR(leaf_stat.st_mode)
        or leaf_stat.st_uid != os.getuid()
        or leaf_stat.st_mode & 0o022
    ):
        raise SystemExit(
            "publication delivery root must be current-user-owned and not group/other writable"
        )
    leaf_identity = identity(leaf_stat)
    if expected_identity is not None and leaf_identity != expected_identity:
        raise SystemExit("publication delivery root changed during snapshot")

    ancestor = source.parent
    while True:
        try:
            ancestor_stat = ancestor.lstat()
        except OSError as exc:
            raise SystemExit(f"publication delivery ancestor cannot be inspected: {exc}")
        if ancestor.is_symlink() or not stat.S_ISDIR(ancestor_stat.st_mode):
            raise SystemExit("publication delivery ancestor must be a real directory")
        if ancestor_stat.st_uid not in {0, os.getuid()}:
            raise SystemExit("publication delivery ancestor has an untrusted owner")
        ancestor_mode = stat.S_IMODE(ancestor_stat.st_mode)
        root_sticky_exception = ancestor_stat.st_uid == 0 and ancestor_mode == 0o1777
        if ancestor_mode & 0o022 and not root_sticky_exception:
            raise SystemExit(
                "publication delivery ancestor is writable by group or other"
            )
        if ancestor == ancestor.parent:
            break
        ancestor = ancestor.parent

    if source.resolve() != source:
        raise SystemExit("publication evidence must use an absolute real delivery root")
    return leaf_identity
# TAIJI_PYTHON38_PUBLICATION_TRUST_HELPER_END


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


delivery_root_identity = validate_publication_delivery_root(source_root)
copy_tree(source_root, destination_root, at_root=True)
validate_publication_delivery_root(
    source_root, expected_identity=delivery_root_identity
)
PY

  /usr/bin/python3 -I -B - "$ROOT_DIR" "$SNAPSHOT_ROOT/delivery" "$SNAPSHOT_EVIDENCE" <<'PY' \
    || fail "publication physical bundle 未通过完整实物和签名前合同校验，拒绝读取私钥"
import argparse
import hashlib
import importlib.util
import sys
from pathlib import Path


root = Path(sys.argv[1]).resolve()
delivery = Path(sys.argv[2]).resolve()
evidence = Path(sys.argv[3]).resolve()
bundle_evidence = delivery / "release-evidence.json"
validator_path = root / "scripts/validate-taiji-release-evidence.py"
spec = importlib.util.spec_from_file_location("taiji_signer_publication_validator", validator_path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load publication validator")
validator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validator
spec.loader.exec_module(validator)
payload, _ = validator.read_regular_bytes(evidence, "publication signing evidence")
bundle_payload, _ = validator.read_regular_bytes(
    bundle_evidence, "publication recursive bundle evidence"
)
if bundle_payload != payload:
    raise SystemExit("signing evidence and recursive bundle snapshot differ")
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
    challenge="",
    require_active_challenge=True,
)
binding = validator.validate_build_binding(args)
if not isinstance(binding, validator.BuildBinding):
    raise SystemExit("publication validation did not produce a v3 BuildBinding")
validator.validate_release_evidence_v3(data, bundle_evidence, args, binding)

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
publication_challenge = data["challenge_envelope"]["nonce"]
certification_challenge = certification_data.get("challenge_nonce")
if (
    type(certification_challenge) is not str
    or validator.CHALLENGE_RE.fullmatch(certification_challenge) is None
    or certification_challenge == publication_challenge
):
    raise SystemExit("certification challenge is invalid or reused for publication")
validator.validate_certification_set_v1(
    certification_data,
    certification,
    argparse.Namespace(
        challenge="",
        matrix=delivery / "验收工具/certification-matrix.json",
        manifest=manifest,
    ),
    binding,
)
PY

  /usr/bin/python3 -I -B "$LIVE_CI_REVALIDATOR" \
    --evidence "$SNAPSHOT_ROOT/delivery/github-ci-evidence.json" \
    --source-commit "$SOURCE_COMMIT" \
    || fail "github-ci-live-revalidation 未通过，拒绝在签名前读取私钥"
fi

if ! private_fingerprint="$(/usr/bin/openssl pkey -in "$PRIVATE_KEY" -pubout -outform DER 2>/dev/null | /usr/bin/openssl dgst -sha256 -r | awk '{print $1}')"; then
  fail "无法读取发布私钥"
fi
[ -n "$private_fingerprint" ] || fail "无法读取发布私钥"
[ "$private_fingerprint" = "$public_fingerprint" ] || fail "发布私钥与产品固定验签公钥不匹配"

if [ "$MODE" = "certification" ]; then
  /usr/bin/python3 -I -B - "$EVIDENCE" "$SNAPSHOT_ROOT" <<'PY' \
    || fail "certification-set 物理证据树无法创建不可替换快照"
import json
import os
import stat
import sys
from pathlib import Path


evidence = Path(sys.argv[1]).absolute()
snapshot = Path(sys.argv[2]).absolute()
source_root = evidence.parent
total_bytes = 0
MAX_CERTIFICATION_SNAPSHOT_FILE_BYTES = 1024 * 1024 * 1024
MAX_PREVIOUS_RELEASE_DEB_BYTES = 2 * 1024 * 1024 * 1024
OFFLINE_EVIDENCE_BASENAME = "offline-install-rehearsal.json"


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


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit("certification offline evidence contains duplicate JSON keys")
        result[key] = value
    return result


def read_previous_deb_basename(path):
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size <= 0
        or before.st_size > 1024 * 1024
    ):
        raise SystemExit("certification offline evidence is not a bounded single-link file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if identity(before) != identity(opened):
            raise SystemExit("certification offline evidence changed before read")
        chunks = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise SystemExit("certification offline evidence was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise SystemExit("certification offline evidence grew")
        after = os.fstat(descriptor)
        current = path.lstat()
        if identity(opened) != identity(after) or identity(opened) != identity(current):
            raise SystemExit("certification offline evidence changed during read")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeError, ValueError) as exc:
        raise SystemExit(f"certification offline evidence is not strict JSON: {exc}")
    previous = payload.get("previous_release") if type(payload) is dict else None
    previous_deb_basename = previous.get("deb_basename") if type(previous) is dict else None
    if (
        type(previous_deb_basename) is not str
        or not previous_deb_basename
        or Path(previous_deb_basename).name != previous_deb_basename
        or "/" in previous_deb_basename
        or "\\" in previous_deb_basename
        or payload.get("previous_deb_basename") != previous_deb_basename
    ):
        raise SystemExit("certification offline previous DEB basename is unsafe or ambiguous")
    return previous_deb_basename


offline_root = source_root / "offline-rehearsal"
previous_deb_basename = read_previous_deb_basename(
    offline_root / OFFLINE_EVIDENCE_BASENAME
)
previous_deb_path = offline_root / previous_deb_basename


def copy_file(source, destination):
    global total_bytes
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size <= 0:
        raise SystemExit("certification snapshot source is not a single-link regular file")
    max_bytes = (
        MAX_PREVIOUS_RELEASE_DEB_BYTES
        if source == previous_deb_path
        else MAX_CERTIFICATION_SNAPSHOT_FILE_BYTES
    )
    if before.st_size > max_bytes:
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
        current = source.lstat()
    finally:
        os.close(source_fd)
    if identity(opened) != identity(after) or identity(opened) != identity(current):
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
if read_previous_deb_basename(
    snapshot / "offline-rehearsal" / OFFLINE_EVIDENCE_BASENAME
) != previous_deb_basename:
    raise SystemExit("certification offline previous DEB changed during snapshot")
PY

  /usr/bin/python3 -I -B - "$ROOT_DIR" "$SNAPSHOT_EVIDENCE" <<'PY' \
    || fail "certification-set physical bundle 未通过完整实物校验，拒绝签名"
import argparse
import importlib.util
import json
import sys
from pathlib import Path


root = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
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
args = argparse.Namespace(
    challenge="",
    matrix=matrix_path,
    require_active_challenge=True,
)
validator.validate_certification_set_v1(data, evidence, args, binding)
PY
fi

if [ "$MODE" = "certification" ] || [ "$MODE" = "publication" ]; then
  # Reserve once for this public-key identity before the cryptographic write.
  # The nonce filename intentionally omits purpose, so cross-purpose replay in
  # this controlled signing account fails closed.  This is not a global ledger.
  /usr/bin/python3 -I -B "$CHALLENGE_HELPER" reserve --envelope "$SNAPSHOT_ENVELOPE" \
    --evidence "$SNAPSHOT_EVIDENCE" \
    --public-key-fingerprint "$public_fingerprint" \
    --purpose "$MODE" \
    --source-commit "$SOURCE_COMMIT" \
    --deb-basename "$DEB_BASENAME" \
    --deb-sha256 "$DEB_SHA256" \
    --evidence-time "$GENERATED_AT_UTC" \
    || fail "本次 challenge 已使用、已过期或固定 signer state 不安全；请签发新 envelope 后重新验收"

  tmp_signature="$(mktemp "${SIGNATURE}.tmp.XXXXXX")"
  /usr/bin/openssl dgst -sha256 -sign "$PRIVATE_KEY" -out "$tmp_signature" "$SNAPSHOT_EVIDENCE" || fail "证据签名失败"
  /usr/bin/openssl dgst -sha256 -verify "$PUBLIC_KEY" -signature "$tmp_signature" "$SNAPSHOT_EVIDENCE" >/dev/null \
    || fail "证据签名回读验证失败"
  chmod 0644 "$tmp_signature"
  /usr/bin/python3 -I -B - "$tmp_signature" "$SIGNATURE" <<'PY' \
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
  if ! /usr/bin/openssl dgst -sha256 -verify "$PUBLIC_KEY" -signature "$SIGNATURE" "$EVIDENCE" >/dev/null; then
    rm -f -- "$SIGNATURE"
    fail "原证据在签名期间发生变化，已删除不再匹配的签名"
  fi
  printf 'release-evidence-signed\t%s\n' "$SIGNATURE"
  exit 0
fi

fail "当前 signer 只接受 certification-set v1 或 release-evidence v3"
