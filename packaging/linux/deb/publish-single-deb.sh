#!/usr/bin/env bash
# Publish the customer-facing folder only after the complete formal delivery passes.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
FORMAL_DELIVERY_DIR="$REPO_ROOT/taijiagent 打包交付"
BASELINE_TOOL="$REPO_ROOT/packaging/linux/target_baseline.py"
RUNTIME_DEPENDS_FILE="$SCRIPT_DIR/runtime-depends.txt"
MAINTAINER_VALIDATOR="$REPO_ROOT/packaging/linux/validate-approved-maintainer.py"
APPROVED_MAINTAINER="$REPO_ROOT/packaging/linux/approved-maintainer.json"
RELEASE_CHECK="$REPO_ROOT/scripts/taiji-release-check.sh"
VERSION_FILE="$REPO_ROOT/VERSION"
DELIVERY_DIR=""
OUTPUT_DIR=""
RECEIPT_ROOT="$REPO_ROOT/runtime/release-evidence/single-deb"
WORK_ROOT=""
STAGING_DIR=""
RECEIPT_DIR=""
RECEIPT_STAGING_DIR=""
RECEIPT_STAGING_IDENTITY=""
RECEIPT_ROOT_CREATED=0
OUTPUT_PUBLISHED=0
PUBLISHED_IDENTITY=""
CUSTOMER_NAME=""

fail() {
  printf '[FAIL] %s\n' "$*" >&2
  exit 1
}

usage() {
  cat >&2 <<'USAGE'
Usage: publish-single-deb.sh --delivery-dir DIR --output-dir NEW_DIR [--receipt-root DIR]

DIR must be the complete formal delivery directory. The publisher snapshots its
single DEB before auditing, then requires formal 01 preflight, taiji-release-check,
and signed offline plus target evidence. On success NEW_DIR contains exactly one
bit-identical customer DEB; internal evidence is written below --receipt-root.
USAGE
  exit 2
}

safe_remove_published_output() {
  python3 - "$OUTPUT_DIR" "$CUSTOMER_NAME" "$PUBLISHED_IDENTITY" <<'PY'
import hashlib
import json
import os
import stat
import sys

output, expected_name, identity_path = sys.argv[1:]
with open(identity_path, "r", encoding="utf-8") as handle:
    expected = json.load(handle)
parent, output_name = os.path.split(output)
directory_flags = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
parent_descriptor = os.open(parent, directory_flags)
try:
    output_descriptor = os.open(output_name, directory_flags, dir_fd=parent_descriptor)
    try:
        directory_stat = os.fstat(output_descriptor)
        if [directory_stat.st_dev, directory_stat.st_ino] != expected["directory"]:
            raise SystemExit("refusing to remove a replaced customer output directory")
        if os.listdir(output_descriptor) != [expected_name]:
            raise SystemExit("refusing to remove a customer output with unknown entries")
        file_descriptor = os.open(expected_name, file_flags, dir_fd=output_descriptor)
        digest = hashlib.sha256()
        try:
            file_stat = os.fstat(file_descriptor)
            if [file_stat.st_dev, file_stat.st_ino] != expected["file"]:
                raise SystemExit("refusing to remove a replaced customer DEB")
            while True:
                chunk = os.read(file_descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        finally:
            os.close(file_descriptor)
        if digest.hexdigest() != expected["sha256"]:
            raise SystemExit("refusing to remove a modified customer DEB")
        os.unlink(expected_name, dir_fd=output_descriptor)
    finally:
        os.close(output_descriptor)
    os.rmdir(output_name, dir_fd=parent_descriptor)
finally:
    os.close(parent_descriptor)
PY
}

safe_remove_receipt_staging() {
  python3 - "$RECEIPT_STAGING_DIR" "$RECEIPT_STAGING_IDENTITY" <<'PY'
import json
import os
import stat
import sys

staging_path, identity_path = sys.argv[1:]
with open(identity_path, "r", encoding="utf-8") as handle:
    expected = json.load(handle)
if os.path.abspath(staging_path) != expected["path"]:
    raise SystemExit("refusing to remove an unbound receipt staging path")
directory_flags = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
try:
    root_descriptor = os.open(expected["root_path"], directory_flags)
except FileNotFoundError:
    raise SystemExit(0)
try:
    root_stat = os.fstat(root_descriptor)
    if [root_stat.st_dev, root_stat.st_ino] != expected["root"]:
        raise SystemExit("refusing to clean receipt staging below a replaced root")
    try:
        staging_descriptor = os.open(
            expected["name"], directory_flags, dir_fd=root_descriptor
        )
    except FileNotFoundError:
        raise SystemExit(0)
    try:
        staging_stat = os.fstat(staging_descriptor)
        if [staging_stat.st_dev, staging_stat.st_ino] != expected["directory"]:
            raise SystemExit("refusing to remove replaced receipt staging")
        allowed = {
            "publication-receipt.json",
            "target-baseline.json",
            "deb.sha256",
        }
        entries = os.listdir(staging_descriptor)
        if not set(entries).issubset(allowed):
            raise SystemExit("refusing to remove receipt staging with unknown entries")
        for name in entries:
            value = os.stat(name, dir_fd=staging_descriptor, follow_symlinks=False)
            if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
                raise SystemExit("refusing to remove unsafe receipt staging entry")
            os.unlink(name, dir_fd=staging_descriptor)
        os.fsync(staging_descriptor)
    finally:
        os.close(staging_descriptor)
    os.rmdir(expected["name"], dir_fd=root_descriptor)
    os.fsync(root_descriptor)
finally:
    os.close(root_descriptor)
PY
}

ensure_safe_directory() {
  python3 - "$1" <<'PY'
import os
import stat
import sys

target = os.path.abspath(sys.argv[1])
current = os.path.sep
for component in target.split(os.path.sep)[1:]:
    current = os.path.join(current, component)
    try:
        current_stat = os.lstat(current)
    except FileNotFoundError:
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            pass
        current_stat = os.lstat(current)
    if stat.S_ISLNK(current_stat.st_mode):
        if current_stat.st_uid != 0:
            raise SystemExit(f"unsafe receipt directory symlink: {current}")
        current = os.path.realpath(current)
        current_stat = os.lstat(current)
    if not stat.S_ISDIR(current_stat.st_mode):
        raise SystemExit(f"unsafe receipt directory component: {current}")
PY
}

cleanup() {
  local status="$?"
  trap - EXIT
  if [ -n "$STAGING_DIR" ] && [ -d "$STAGING_DIR" ] && [ ! -L "$STAGING_DIR" ]; then
    rm -rf -- "$STAGING_DIR" || true
  fi
  if [ "$status" -ne 0 ] && [ "$OUTPUT_PUBLISHED" = "1" ]; then
    safe_remove_published_output || true
  fi
  if [ "$status" -ne 0 ] && [ -n "$RECEIPT_STAGING_DIR" ] && [ -n "$RECEIPT_STAGING_IDENTITY" ]; then
    safe_remove_receipt_staging || true
  fi
  if [ "$status" -ne 0 ] && [ "$RECEIPT_ROOT_CREATED" = "1" ] && [ -d "$RECEIPT_ROOT" ] && [ ! -L "$RECEIPT_ROOT" ]; then
    rmdir -- "$RECEIPT_ROOT" 2>/dev/null || true
  fi
  if [ -n "$WORK_ROOT" ] && [ -d "$WORK_ROOT" ] && [ ! -L "$WORK_ROOT" ]; then
    rm -rf -- "$WORK_ROOT" || true
  fi
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

while [ "$#" -gt 0 ]; do
  case "$1" in
    --delivery-dir) [ "$#" -ge 2 ] || usage; DELIVERY_DIR="$2"; shift 2 ;;
    --output-dir) [ "$#" -ge 2 ] || usage; OUTPUT_DIR="$2"; shift 2 ;;
    --receipt-root) [ "$#" -ge 2 ] || usage; RECEIPT_ROOT="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) fail "unknown argument: $1; use --delivery-dir with the complete formal delivery" ;;
  esac
done

[ -n "$DELIVERY_DIR" ] || usage
[ -n "$OUTPUT_DIR" ] || usage
for command in python3 bash dpkg-deb sha256sum find install mktemp cmp; do
  command -v "$command" >/dev/null 2>&1 || fail "missing required command: $command"
done
for source_file in \
  "$BASELINE_TOOL" \
  "$RUNTIME_DEPENDS_FILE" \
  "$MAINTAINER_VALIDATOR" \
  "$APPROVED_MAINTAINER" \
  "$RELEASE_CHECK" \
  "$VERSION_FILE"; do
  [ -f "$source_file" ] && [ ! -L "$source_file" ] \
    || fail "formal source release input is missing or unsafe: $source_file"
done
[ -d "$DELIVERY_DIR" ] && [ ! -L "$DELIVERY_DIR" ] \
  || fail "delivery input must be a complete real directory: $DELIVERY_DIR"
DELIVERY_DIR="$(cd "$DELIVERY_DIR" && pwd -P)"
[ -d "$FORMAL_DELIVERY_DIR" ] && [ ! -L "$FORMAL_DELIVERY_DIR" ] \
  || fail "formal repository delivery directory is missing or unsafe: $FORMAL_DELIVERY_DIR"
FORMAL_DELIVERY_DIR="$(cd "$FORMAL_DELIVERY_DIR" && pwd -P)"
[ "$DELIVERY_DIR" = "$FORMAL_DELIVERY_DIR" ] \
  || fail "delivery input must be the formal repository delivery directory: $FORMAL_DELIVERY_DIR"

output_parent="$(dirname "$OUTPUT_DIR")"
output_name="$(basename "$OUTPUT_DIR")"
[ "$output_name" != "." ] && [ "$output_name" != ".." ] && [ -n "$output_name" ] \
  || fail "unsafe customer output directory name"
[ -d "$output_parent" ] && [ ! -L "$output_parent" ] \
  || fail "customer output parent must already be a real directory: $output_parent"
output_parent="$(cd "$output_parent" && pwd -P)"
OUTPUT_DIR="$output_parent/$output_name"
[ ! -e "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ] \
  || fail "customer output directory must not already exist: $OUTPUT_DIR"

package_dir="$DELIVERY_DIR/生成的安装包"
profile_path="$DELIVERY_DIR/目标基线/target-baseline.json"
offline_evidence="$DELIVERY_DIR/offline-install-rehearsal/offline-install-rehearsal.json"
target_evidence="$DELIVERY_DIR/target-verification/target-verification.json"
preflight="$FORMAL_DELIVERY_DIR/01_制包机_发布预检.sh"
for required in \
  "$profile_path" \
  "$offline_evidence" \
  "${offline_evidence}.sig" \
  "$target_evidence" \
  "${target_evidence}.sig" \
  "$preflight"; do
  [ -f "$required" ] && [ ! -L "$required" ] \
    || fail "complete signed delivery input is missing or unsafe: $required"
done

deb_count="$(find "$package_dir" -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' | wc -l | tr -d ' ')"
[ "$deb_count" = "1" ] || fail "complete delivery must contain exactly one release DEB"
source_deb="$(find "$package_dir" -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' -print)"

WORK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/taiji-single-deb-publish.XXXXXX")"
[ -d "$WORK_ROOT" ] && [ ! -L "$WORK_ROOT" ] || fail "cannot create safe publication workspace"
chmod 0700 "$WORK_ROOT"
snapshot_deb="$WORK_ROOT/release.deb"
snapshot_profile="$WORK_ROOT/target-baseline.json"
snapshot_offline_evidence="$WORK_ROOT/offline-install-rehearsal.json"
snapshot_offline_signature="$WORK_ROOT/offline-install-rehearsal.json.sig"
snapshot_target_evidence="$WORK_ROOT/target-verification.json"
snapshot_target_signature="$WORK_ROOT/target-verification.json.sig"
snapshot_approved_maintainer="$WORK_ROOT/approved-maintainer.json"
deb_identity="$WORK_ROOT/deb-identity.json"
profile_identity="$WORK_ROOT/profile-identity.json"
offline_evidence_identity="$WORK_ROOT/offline-evidence-identity.json"
offline_signature_identity="$WORK_ROOT/offline-signature-identity.json"
target_evidence_identity="$WORK_ROOT/target-evidence-identity.json"
target_signature_identity="$WORK_ROOT/target-signature-identity.json"
approved_maintainer_identity="$WORK_ROOT/approved-maintainer-identity.json"

snapshot_regular() {
  python3 - "$1" "$2" "$3" <<'PY'
import hashlib
import json
import os
import stat
import sys

source, destination, identity_path = map(os.path.abspath, sys.argv[1:])
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(source, flags)
try:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size <= 0:
        raise SystemExit("source is not a non-empty single-link regular file")
    output = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(output, view)
                if written <= 0:
                    raise SystemExit("snapshot write failed")
                view = view[written:]
            digest.update(chunk)
            total += len(chunk)
        os.fsync(output)
    finally:
        os.close(output)
    after = os.fstat(descriptor)
finally:
    os.close(descriptor)

def identity(value):
    return {
        "dev": value.st_dev,
        "ino": value.st_ino,
        "mode": value.st_mode,
        "nlink": value.st_nlink,
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }

if total != before.st_size or identity(before) != identity(after):
    raise SystemExit("source changed while being snapshotted")
record = {"source": source, "sha256": digest.hexdigest(), "identity": identity(before)}
with open(identity_path, "x", encoding="utf-8") as handle:
    json.dump(record, handle, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
print(record["sha256"])
PY
}

verify_unchanged() {
  python3 - "$1" <<'PY'
import hashlib
import json
import os
import stat
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    record = json.load(handle)
path = record["source"]
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(path, flags)
digest = hashlib.sha256()
try:
    current = os.fstat(descriptor)
    actual = {
        "dev": current.st_dev,
        "ino": current.st_ino,
        "mode": current.st_mode,
        "nlink": current.st_nlink,
        "size": current.st_size,
        "mtime_ns": current.st_mtime_ns,
        "ctime_ns": current.st_ctime_ns,
    }
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
finally:
    os.close(descriptor)
if actual != record["identity"] or digest.hexdigest() != record["sha256"]:
    raise SystemExit("source changed after the publication snapshot")
PY
}

# Snapshot the customer payload before any metadata, payload, or release-gate audit.
source_sha256="$(snapshot_regular "$source_deb" "$snapshot_deb" "$deb_identity")" \
  || fail "DEB changed while creating the publication snapshot"
profile_sha256="$(snapshot_regular "$profile_path" "$snapshot_profile" "$profile_identity")" \
  || fail "target baseline changed while creating the publication snapshot"
offline_evidence_sha256="$(snapshot_regular "$offline_evidence" "$snapshot_offline_evidence" "$offline_evidence_identity")" \
  || fail "offline evidence changed while creating the publication snapshot"
offline_signature_sha256="$(snapshot_regular "${offline_evidence}.sig" "$snapshot_offline_signature" "$offline_signature_identity")" \
  || fail "offline evidence signature changed while creating the publication snapshot"
target_evidence_sha256="$(snapshot_regular "$target_evidence" "$snapshot_target_evidence" "$target_evidence_identity")" \
  || fail "target evidence changed while creating the publication snapshot"
target_signature_sha256="$(snapshot_regular "${target_evidence}.sig" "$snapshot_target_signature" "$target_signature_identity")" \
  || fail "target evidence signature changed while creating the publication snapshot"
approved_maintainer_sha256="$(snapshot_regular "$APPROVED_MAINTAINER" "$snapshot_approved_maintainer" "$approved_maintainer_identity")" \
  || fail "approved maintainer changed while creating the publication snapshot"

TAIJI_RELEASE_SKIP_GIT_CHECK=0 \
TAIJI_RELEASE_REQUIRE_ARTIFACTS=1 \
TAIJI_REPO_ROOT="$REPO_ROOT" \
  bash "$preflight" || fail "formal 01 release preflight failed"

TAIJI_RELEASE_REPO_ROOT="$REPO_ROOT" \
TAIJI_DELIVERY_DIR="$DELIVERY_DIR" \
TAIJI_OFFLINE_REHEARSAL_DIR="$(dirname "$offline_evidence")" \
TAIJI_TARGET_VERIFICATION_DIR="$(dirname "$target_evidence")" \
  bash "$RELEASE_CHECK" || fail "formal taiji-release-check failed"

verify_unchanged "$deb_identity" || fail "source DEB changed after the publication snapshot"
verify_unchanged "$profile_identity" || fail "target baseline changed after the publication snapshot"
verify_unchanged "$offline_evidence_identity" || fail "signed offline evidence changed after the publication snapshot"
verify_unchanged "$offline_signature_identity" || fail "signed offline evidence signature changed after the publication snapshot"
verify_unchanged "$target_evidence_identity" || fail "signed target evidence changed after the publication snapshot"
verify_unchanged "$target_signature_identity" || fail "signed target evidence signature changed after the publication snapshot"
verify_unchanged "$approved_maintainer_identity" || fail "approved maintainer changed after the publication snapshot"
[ ! -e "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ] \
  || fail "customer output directory was occupied concurrently: $OUTPUT_DIR"

python3 "$BASELINE_TOOL" validate \
  --profile "$snapshot_profile" \
  --depends-file "$RUNTIME_DEPENDS_FILE" \
  --max-age-days 30 >/dev/null

profile_id="$(python3 - "$snapshot_profile" <<'PY'
import json
import re
import sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    value = json.load(handle)["profile_id"]
if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", value):
    raise SystemExit("invalid target baseline profile_id")
print(value)
PY
)" || fail "target baseline profile_id is invalid"

version="$(tr -d '\r\n' < "$VERSION_FILE")"
package_name="$(dpkg-deb -f "$snapshot_deb" Package)"
package_version="$(dpkg-deb -f "$snapshot_deb" Version)"
package_arch="$(dpkg-deb -f "$snapshot_deb" Architecture)"
package_maintainer="$(dpkg-deb -f "$snapshot_deb" Maintainer)"
[ "$package_name" = "taiji-agent" ] || fail "unexpected package name: $package_name"
[ "$package_version" = "$version" ] \
  || fail "DEB version does not match root VERSION: $package_version != $version"
[ "$package_arch" = "amd64" ] || fail "unexpected DEB architecture: $package_arch"
python3 "$MAINTAINER_VALIDATOR" \
  --file "$snapshot_approved_maintainer" \
  --expect "$package_maintainer" >/dev/null \
  || fail "DEB Maintainer does not exactly match formal source approval"

extract_dir="$WORK_ROOT/extracted"
mkdir "$extract_dir"
dpkg-deb -x "$snapshot_deb" "$extract_dir"
embedded_profile="$extract_dir/opt/taiji-agent/resources/target-baseline.json"
[ -f "$embedded_profile" ] && [ ! -L "$embedded_profile" ] \
  || fail "DEB does not embed a safe target-baseline.json"
cmp -s "$snapshot_profile" "$embedded_profile" \
  || fail "embedded target baseline is not byte-identical to release input"

embedded_manifest="$extract_dir/opt/taiji-agent/resources/taiji-release-manifest.json"
python3 - "$embedded_manifest" "$profile_id" "$profile_sha256" <<'PY'
import json
import sys
from pathlib import Path

try:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid embedded release manifest: {exc}")
if manifest.get("targetBaselineProfile") != sys.argv[2]:
    raise SystemExit("release manifest targetBaselineProfile mismatch")
if manifest.get("targetBaselineSha256") != sys.argv[3]:
    raise SystemExit("release manifest targetBaselineSha256 mismatch")
PY

python3 - \
  "$snapshot_offline_evidence" \
  "$snapshot_target_evidence" \
  "$source_deb" \
  "$source_sha256" \
  "$profile_id" \
  "$profile_sha256" <<'PY'
import json
import sys

def strict(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"duplicate evidence field: {key}")
        result[key] = value
    return result

def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle, object_pairs_hook=strict)
    if not isinstance(value, dict):
        raise SystemExit("evidence must be a JSON object")
    return value

offline = load(sys.argv[1])
target = load(sys.argv[2])
expected_common = {
    "schema_version": 2,
    "deb_basename": sys.argv[3].rsplit("/", 1)[-1],
    "deb_sha256": sys.argv[4],
    "target_baseline_profile_id": sys.argv[5],
    "target_baseline_sha256": sys.argv[6],
}
for label, evidence, evidence_type in (
    ("offline", offline, "offline-install-rehearsal"),
    ("target", target, "target-desktop-verification"),
):
    expected = {**expected_common, "evidence_type": evidence_type}
    for key, value in expected.items():
        if type(evidence.get(key)) is not type(value) or evidence.get(key) != value:
            raise SystemExit(f"{label} evidence {key} is not bound to the snapshot")
target_facts = {
    "installation_method": "desktop-double-click",
    "installation_method_evidence": "human-attestation",
    "installation_method_machine_observed": False,
    "installation_network": "continuous-process-sampling-no-non-loopback-up",
    "installation_file_count": 1,
    "additional_install_files": False,
    "dpkg_status_before": "not-installed",
    "dpkg_status_after": "install ok installed",
    "first_configuration_cycle_completed": True,
    "visible_first_configuration_completion": True,
    "target_verified": True,
}
for key, value in target_facts.items():
    if type(target.get(key)) is not type(value) or target.get(key) != value:
        raise SystemExit(f"target evidence does not prove single-DEB offline install fact: {key}")
PY

customer_name="taiji-agent_${version}_${profile_id}_amd64.deb"
CUSTOMER_NAME="$customer_name"
STAGING_DIR="$(mktemp -d "$output_parent/.taiji-single-deb.XXXXXX")"
chmod 0700 "$STAGING_DIR"
install -m 0644 "$snapshot_deb" "$STAGING_DIR/$customer_name"
staged_sha256="$(sha256sum "$STAGING_DIR/$customer_name" | awk '{print $1}')"
[ "$staged_sha256" = "$source_sha256" ] || fail "staged DEB is not bit-identical"
entry_count="$(find "$STAGING_DIR" -mindepth 1 -maxdepth 1 -print | wc -l | tr -d ' ')"
[ "$entry_count" = "1" ] || fail "customer folder must contain exactly one entry"

receipt_parent="$(dirname "$RECEIPT_ROOT")"
receipt_name="$(basename "$RECEIPT_ROOT")"
ensure_safe_directory "$receipt_parent" \
  || fail "cannot create a safe receipt root parent: $receipt_parent"
[ -d "$receipt_parent" ] && [ ! -L "$receipt_parent" ] \
  || fail "receipt root parent must already be a real directory: $receipt_parent"
receipt_parent="$(cd "$receipt_parent" && pwd -P)"
RECEIPT_ROOT="$receipt_parent/$receipt_name"
if [ ! -e "$RECEIPT_ROOT" ]; then
  mkdir "$RECEIPT_ROOT"
  RECEIPT_ROOT_CREATED=1
fi
[ -d "$RECEIPT_ROOT" ] && [ ! -L "$RECEIPT_ROOT" ] \
  || fail "receipt root must be a real directory"
receipt_id="${version}-${profile_id}-${source_sha256:0:12}"
RECEIPT_DIR="$RECEIPT_ROOT/$receipt_id"
[ ! -e "$RECEIPT_DIR" ] && [ ! -L "$RECEIPT_DIR" ] \
  || fail "internal publication receipt is already reserved: $RECEIPT_DIR"
RECEIPT_STAGING_IDENTITY="$WORK_ROOT/receipt-staging-identity.json"
RECEIPT_STAGING_DIR="$(python3 - "$RECEIPT_ROOT" "$RECEIPT_STAGING_IDENTITY" <<'PY'
import json
import os
import secrets
import stat
import sys

root_path, identity_path = map(os.path.abspath, sys.argv[1:])
directory_flags = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
root_descriptor = os.open(root_path, directory_flags)
name = ""
try:
    root_stat = os.fstat(root_descriptor)
    if root_stat.st_uid != os.geteuid() or stat.S_IMODE(root_stat.st_mode) & 0o022:
        raise SystemExit("receipt root must be owned by the publisher and not group/other writable")
    for _ in range(128):
        candidate = ".taiji-receipt.{}".format(secrets.token_hex(12))
        try:
            os.mkdir(candidate, 0o700, dir_fd=root_descriptor)
            name = candidate
            break
        except FileExistsError:
            continue
    if not name:
        raise SystemExit("cannot allocate private receipt staging directory")
    staging_descriptor = os.open(name, directory_flags, dir_fd=root_descriptor)
    try:
        staging_stat = os.fstat(staging_descriptor)
        record = {
            "root_path": root_path,
            "root": [root_stat.st_dev, root_stat.st_ino],
            "name": name,
            "path": os.path.join(root_path, name),
            "directory": [staging_stat.st_dev, staging_stat.st_ino],
        }
        descriptor = os.open(
            identity_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(staging_descriptor)
    finally:
        os.close(staging_descriptor)
    os.fsync(root_descriptor)
except BaseException:
    if name:
        try:
            os.rmdir(name, dir_fd=root_descriptor)
            os.fsync(root_descriptor)
        except OSError:
            pass
    raise
finally:
    os.close(root_descriptor)
print(os.path.join(root_path, name))
PY
)" || fail "cannot create identity-bound receipt staging"

PUBLISHED_IDENTITY="$WORK_ROOT/published-output-identity.json"
python3 - "$STAGING_DIR" "$OUTPUT_DIR" "$customer_name" "$source_sha256" "$PUBLISHED_IDENTITY" <<'PY'
import ctypes
import errno
import json
import os
import stat
import sys

source, destination, expected_name, expected_hash, identity_path = sys.argv[1:]
renamed = False
try:
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            renameat2.restype = ctypes.c_int
            if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) == 0:
                renamed = True
            else:
                error = ctypes.get_errno()
                if error != errno.ENOSYS:
                    raise OSError(error, os.strerror(error), destination)
    if not renamed:
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        os.rename(source, destination)
        renamed = True
    directory_stat = os.lstat(destination)
    file_path = os.path.join(destination, expected_name)
    file_stat = os.lstat(file_path)
    if not stat.S_ISDIR(directory_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError("published output identity is unsafe")
    with open(identity_path, "x", encoding="utf-8") as handle:
        json.dump(
            {
                "directory": [directory_stat.st_dev, directory_stat.st_ino],
                "file": [file_stat.st_dev, file_stat.st_ino],
                "sha256": expected_hash,
            },
            handle,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
except BaseException:
    if renamed:
        try:
            os.unlink(os.path.join(destination, expected_name))
            os.rmdir(destination)
        except OSError:
            pass
    raise
PY
STAGING_DIR=""
OUTPUT_PUBLISHED=1

# Re-verify the published directory before generating any receipt.
python3 - "$OUTPUT_DIR" "$customer_name" "$source_sha256" <<'PY'
import hashlib
import os
import stat
import sys

directory, expected_name, expected_hash = sys.argv[1:]
directory_stat = os.lstat(directory)
if not stat.S_ISDIR(directory_stat.st_mode) or stat.S_ISLNK(directory_stat.st_mode):
    raise SystemExit("published output is not a real directory")
entries = os.listdir(directory)
if entries != [expected_name]:
    raise SystemExit("published customer folder does not contain exactly one DEB")
path = os.path.join(directory, expected_name)
file_stat = os.lstat(path)
if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
    raise SystemExit("published customer DEB is not a single-link regular file")
digest = hashlib.sha256()
with open(path, "rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
if digest.hexdigest() != expected_hash:
    raise SystemExit("published customer DEB hash changed")
PY

python3 - \
  "$RECEIPT_STAGING_DIR/publication-receipt.json" \
  "$customer_name" \
  "$source_sha256" \
  "$profile_id" \
  "$profile_sha256" \
  "$package_maintainer" \
  "$approved_maintainer_sha256" \
  "$offline_evidence_sha256" \
  "$offline_signature_sha256" \
  "$target_evidence_sha256" \
  "$target_signature_sha256" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

receipt = {
    "schema": "taiji-single-deb-publication/v2",
    "published_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "customer_filename": sys.argv[2],
    "deb_sha256": sys.argv[3],
    "target_profile_id": sys.argv[4],
    "target_baseline_sha256": sys.argv[5],
    "maintainer": sys.argv[6],
    "approved_maintainer_sha256": sys.argv[7],
    "offline_evidence_sha256": sys.argv[8],
    "offline_signature_sha256": sys.argv[9],
    "target_evidence_sha256": sys.argv[10],
    "target_signature_sha256": sys.argv[11],
    "customer_folder_contract": "exactly one DEB",
    "formal_gates": ["01", "taiji-release-check"],
    "signed_evidence_types": ["offline-install-rehearsal", "target-desktop-verification"],
}
path = sys.argv[1]
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    json.dump(receipt, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
install -m 0600 "$snapshot_profile" "$RECEIPT_STAGING_DIR/target-baseline.json"
printf '%s  %s\n' "$source_sha256" "$customer_name" > "$RECEIPT_STAGING_DIR/deb.sha256"
chmod 0600 "$RECEIPT_STAGING_DIR/deb.sha256"

# Validate and durably flush the complete private receipt bundle before publication.
python3 - \
  "$RECEIPT_STAGING_DIR" \
  "$RECEIPT_STAGING_IDENTITY" \
  "$customer_name" \
  "$source_sha256" \
  "$profile_id" \
  "$profile_sha256" \
  "$package_maintainer" \
  "$approved_maintainer_sha256" \
  "$offline_evidence_sha256" \
  "$offline_signature_sha256" \
  "$target_evidence_sha256" \
  "$target_signature_sha256" <<'PY'
import hashlib
import json
import os
import re
import stat
import sys

(
    staging_path,
    identity_path,
    customer_name,
    deb_sha256,
    profile_id,
    profile_sha256,
    maintainer,
    approved_maintainer_sha256,
    offline_evidence_sha256,
    offline_signature_sha256,
    target_evidence_sha256,
    target_signature_sha256,
) = sys.argv[1:]
with open(identity_path, "r", encoding="utf-8") as handle:
    identity = json.load(handle)
directory_flags = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(staging_path, directory_flags)
try:
    directory_stat = os.fstat(descriptor)
    if [directory_stat.st_dev, directory_stat.st_ino] != identity["directory"]:
        raise SystemExit("receipt staging identity changed before validation")
    names = sorted(os.listdir(descriptor))
    expected_names = ["deb.sha256", "publication-receipt.json", "target-baseline.json"]
    if names != expected_names:
        raise SystemExit("receipt staging is incomplete or contains unknown entries")
    payloads = {}
    for name in names:
        file_descriptor = os.open(name, file_flags, dir_fd=descriptor)
        try:
            value = os.fstat(file_descriptor)
            if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
                raise SystemExit("receipt staging entry is unsafe: {}".format(name))
            chunks = []
            while True:
                chunk = os.read(file_descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            payloads[name] = b"".join(chunks)
            os.fsync(file_descriptor)
        finally:
            os.close(file_descriptor)
    os.fsync(descriptor)
finally:
    os.close(descriptor)

def strict(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit("duplicate publication receipt field: {}".format(key))
        result[key] = value
    return result

try:
    receipt = json.loads(payloads["publication-receipt.json"].decode("utf-8"), object_pairs_hook=strict)
except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit("publication receipt is invalid JSON: {}".format(exc))
expected = {
    "schema": "taiji-single-deb-publication/v2",
    "customer_filename": customer_name,
    "deb_sha256": deb_sha256,
    "target_profile_id": profile_id,
    "target_baseline_sha256": profile_sha256,
    "maintainer": maintainer,
    "approved_maintainer_sha256": approved_maintainer_sha256,
    "offline_evidence_sha256": offline_evidence_sha256,
    "offline_signature_sha256": offline_signature_sha256,
    "target_evidence_sha256": target_evidence_sha256,
    "target_signature_sha256": target_signature_sha256,
    "customer_folder_contract": "exactly one DEB",
    "formal_gates": ["01", "taiji-release-check"],
    "signed_evidence_types": ["offline-install-rehearsal", "target-desktop-verification"],
}
if set(receipt) != set(expected) | {"published_at_utc"}:
    raise SystemExit("publication receipt field set is incomplete")
for key, value in expected.items():
    if type(receipt.get(key)) is not type(value) or receipt.get(key) != value:
        raise SystemExit("publication receipt value mismatch: {}".format(key))
timestamp = receipt.get("published_at_utc")
if not isinstance(timestamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp):
    raise SystemExit("publication receipt timestamp is invalid")
if hashlib.sha256(payloads["target-baseline.json"]).hexdigest() != profile_sha256:
    raise SystemExit("receipt target baseline hash mismatch")
expected_checksum = "{}  {}\n".format(deb_sha256, customer_name).encode("utf-8")
if payloads["deb.sha256"] != expected_checksum:
    raise SystemExit("receipt DEB checksum sidecar mismatch")
PY

# Atomically expose the validated receipt bundle without replacing a concurrent owner.
python3 - \
  "$RECEIPT_ROOT" \
  "$RECEIPT_STAGING_DIR" \
  "$RECEIPT_DIR" \
  "$RECEIPT_STAGING_IDENTITY" <<'PY'
import ctypes
import errno
import json
import os
import stat
import sys

root_path, source_path, destination_path, identity_path = map(os.path.abspath, sys.argv[1:])
with open(identity_path, "r", encoding="utf-8") as handle:
    expected = json.load(handle)
if os.path.dirname(source_path) != root_path or os.path.dirname(destination_path) != root_path:
    raise SystemExit("receipt publication paths escaped the bound receipt root")
if source_path != expected["path"]:
    raise SystemExit("receipt staging path is not identity-bound")
source_name = os.path.basename(source_path)
destination_name = os.path.basename(destination_path)
directory_flags = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
root_descriptor = os.open(root_path, directory_flags)
renamed = False
try:
    root_stat = os.fstat(root_descriptor)
    if [root_stat.st_dev, root_stat.st_ino] != expected["root"]:
        raise SystemExit("receipt root identity changed before publication")
    source_descriptor = os.open(source_name, directory_flags, dir_fd=root_descriptor)
    try:
        source_stat = os.fstat(source_descriptor)
        if [source_stat.st_dev, source_stat.st_ino] != expected["directory"]:
            raise SystemExit("receipt staging identity changed before publication")
        if sorted(os.listdir(source_descriptor)) != [
            "deb.sha256",
            "publication-receipt.json",
            "target-baseline.json",
        ]:
            raise SystemExit("receipt staging changed after validation")
    finally:
        os.close(source_descriptor)

    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise SystemExit("renameat2 is required for no-replace receipt publication")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            root_descriptor,
            os.fsencode(source_name),
            root_descriptor,
            os.fsencode(destination_name),
            1,
        )
    elif sys.platform == "darwin":
        renameatx_np = getattr(libc, "renameatx_np", None)
        if renameatx_np is None:
            raise SystemExit("renameatx_np is required for no-replace receipt publication")
        renameatx_np.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            root_descriptor,
            os.fsencode(source_name),
            root_descriptor,
            os.fsencode(destination_name),
            0x00000004,
        )
    else:
        raise SystemExit("no supported no-replace receipt publication primitive")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination_path)
    renamed = True

    destination_descriptor = os.open(destination_name, directory_flags, dir_fd=root_descriptor)
    try:
        destination_stat = os.fstat(destination_descriptor)
        if [destination_stat.st_dev, destination_stat.st_ino] != expected["directory"]:
            raise SystemExit("published receipt identity mismatch")
        if sorted(os.listdir(destination_descriptor)) != [
            "deb.sha256",
            "publication-receipt.json",
            "target-baseline.json",
        ]:
            raise SystemExit("published receipt bundle is incomplete")
        os.fsync(destination_descriptor)
    finally:
        os.close(destination_descriptor)
    os.fsync(root_descriptor)
except BaseException:
    if renamed:
        try:
            destination_descriptor = os.open(
                destination_name, directory_flags, dir_fd=root_descriptor
            )
            try:
                value = os.fstat(destination_descriptor)
                if [value.st_dev, value.st_ino] == expected["directory"]:
                    names = os.listdir(destination_descriptor)
                    allowed = {
                        "deb.sha256",
                        "publication-receipt.json",
                        "target-baseline.json",
                    }
                    if set(names).issubset(allowed):
                        for name in names:
                            item = os.stat(
                                name,
                                dir_fd=destination_descriptor,
                                follow_symlinks=False,
                            )
                            if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1:
                                raise RuntimeError("unsafe published receipt rollback entry")
                            os.unlink(name, dir_fd=destination_descriptor)
                        os.fsync(destination_descriptor)
                        os.close(destination_descriptor)
                        destination_descriptor = -1
                        os.rmdir(destination_name, dir_fd=root_descriptor)
                        os.fsync(root_descriptor)
            finally:
                if destination_descriptor >= 0:
                    os.close(destination_descriptor)
        except OSError:
            pass
    raise
finally:
    os.close(root_descriptor)
PY
RECEIPT_STAGING_DIR=""
RECEIPT_STAGING_IDENTITY=""
OUTPUT_PUBLISHED=0

printf '[OK] Customer single-file installer: %s/%s\n' "$OUTPUT_DIR" "$customer_name"
printf '[OK] Internal evidence receipt: %s/%s\n' "$RECEIPT_ROOT" "$receipt_id"
