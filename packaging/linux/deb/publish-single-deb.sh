#!/bin/bash -p
# Publish one immutable customer DEB after the signed certification/release gates.
set -Eeuo pipefail
umask 077
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONBREAKPOINT PYTHONUSERBASE
unset LD_PRELOAD LD_LIBRARY_PATH DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
unset OPENSSL_CONF OPENSSL_MODULES
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
export TAIJI_PUBLISHER_REPO_ROOT="$REPO_ROOT"

usage() {
  cat >&2 <<'USAGE'
Usage: publish-single-deb.sh \
  --delivery-dir DIRECTORY \
  --candidate-deb PATH \
  --policy PATH \
  --certification-set PATH \
  --certification-signature PATH \
  --release-evidence PATH \
  --release-signature PATH \
  --output-dir NEW_DIRECTORY \
  --receipt-root DIRECTORY

The customer directory contains exactly one fixed-name amd64 DEB.
Signed evidence and policy are archived only in the internal receipt root.
USAGE
  exit 2
}

/usr/bin/python3 -I -B - "$@" <<'PY'
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(os.environ["TAIJI_PUBLISHER_REPO_ROOT"]).resolve()
PUBLIC_KEY = ROOT / "tools/taiji-release-evidence/signing-public.pem"
RELEASE_CHECK = ROOT / "scripts/taiji-release-check.sh"
RELEASE_VALIDATOR = ROOT / "scripts/validate-taiji-release-evidence.py"
LIVE_CI_REVALIDATOR = ROOT / "scripts/revalidate-taiji-github-ci-evidence.py"
TRUSTED_PYTHON_ARGV = ["/usr/bin/python3", "-I", "-B"]
OPENSSL = "/usr/bin/openssl"
DPKG_DEB = "/usr/bin/dpkg-deb"
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
FORBIDDEN_KEYS = {
    "target_baseline_profile_id",
    "target_baseline_sha256",
    "targetBaselineProfile",
    "targetBaselineSha256",
    "profile_id",
}
RECEIPT_NAMES = {
    "release-evidence.json",
    "release-evidence.json.sig",
    "certification-set.json",
    "certification-set.json.sig",
    "compatibility-policy.json",
    "deb.sha256",
    "github-ci-evidence.json",
    "github-ci-run-response.json",
    "github-ci-jobs-response.json",
}
CI_NAMES = (
    "github-ci-evidence.json",
    "github-ci-run-response.json",
    "github-ci-jobs-response.json",
)
TOOLCHAIN_FIELDS = {
    "python_dependency_lock_status",
    "python_lock_basename",
    "python_lock_sha256",
    "python_version",
    "python_archive_sha256",
    "python_executable_sha256",
    "uv_version",
    "uv_archive_sha256",
    "uv_executable_sha256",
    "node_version",
    "node_archive_sha256",
    "node_executable_sha256",
    "electron_version",
    "electron_archive_sha256",
    "electron_executable_sha256",
}
PINNED_UV_EXECUTABLE_SHA256 = "72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2"
PINNED_NODE_EXECUTABLE_SHA256 = "93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068"
PINNED_PYTHON_VERSION = "3.11.15"
PINNED_PYTHON_ARCHIVE_SHA256 = "2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a"
PINNED_PYTHON_EXECUTABLE_SHA256 = "5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba"
PINNED_ELECTRON_EXECUTABLE_SHA256 = "c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d"


class PublisherError(RuntimeError):
    pass


def fail(message: str) -> "NoReturn":
    raise PublisherError(message)


def trusted_child_environment(extra=None) -> dict:
    environment = {
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    for name in ("HOME", "TMPDIR"):
        value = environment[name]
        if not value.startswith("/") or "\0" in value:
            environment[name] = "/tmp"
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        if "\0" in token or "\r" in token or "\n" in token:
            fail("GITHUB_TOKEN contains an invalid character")
        environment["GITHUB_TOKEN"] = token
    if extra:
        environment.update(extra)
    return environment


def lstat_regular(path: Path, label: str) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError as exc:
        fail(f"{label} is unavailable: {path}")
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or stat.S_IMODE(value.st_mode) & 0o022:
        fail(f"{label} must be a private single-link regular file: {path}")
    return value


def lstat_directory(path: Path, label: str) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError:
        fail(f"{label} is unavailable: {path}")
    if not stat.S_ISDIR(value.st_mode) or path.is_symlink() or stat.S_IMODE(value.st_mode) & 0o022:
        fail(f"{label} must be a real directory not writable by group/other: {path}")
    return value


def read_regular(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    metadata = lstat_regular(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        fail(f"{label} cannot be opened safely: {path}")
    chunks: list[bytes] = []
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        fail(f"{label} changed while being read")
    return b"".join(chunks), metadata


def strict_json(payload: bytes, label: str) -> dict:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail(f"{label} contains duplicate JSON field: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"{label} is not strict UTF-8 JSON: {exc}")
    if type(value) is not dict:
        fail(f"{label} must be a JSON object")
    return value


def reject_forbidden_keys(value, label: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_KEYS:
                fail(f"{label} contains forbidden target/profile field: {key}")
            reject_forbidden_keys(child, label)
    elif isinstance(value, list):
        for child in value:
            reject_forbidden_keys(child, label)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def snapshot(source: Path, destination: Path) -> tuple[str, dict]:
    metadata = lstat_regular(source, f"input {source.name}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, flags)
    output_fd = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    hasher = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            total += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(output_fd, view)
                if written <= 0:
                    fail("snapshot write failed")
                view = view[written:]
        os.fsync(output_fd)
        after = os.fstat(source_fd)
    finally:
        os.close(output_fd)
        os.close(source_fd)
    if total != metadata.st_size or (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        fail(f"input changed while being snapshotted: {source}")
    identity = {
        "source": str(source),
        "dev": metadata.st_dev,
        "ino": metadata.st_ino,
        "mode": metadata.st_mode,
        "nlink": metadata.st_nlink,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
        "sha256": hasher.hexdigest(),
    }
    return hasher.hexdigest(), identity


def verify_identity(identity: dict) -> None:
    source = Path(identity["source"])
    payload, metadata = read_regular(source, f"input {source.name}")
    actual = {
        "dev": metadata.st_dev,
        "ino": metadata.st_ino,
        "mode": metadata.st_mode,
        "nlink": metadata.st_nlink,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }
    expected = {key: identity[key] for key in actual}
    if actual != expected or digest(payload) != identity["sha256"]:
        fail(f"publisher input changed during formal gate: {source}")


def snapshot_directory_tree(source_root: Path, destination_root: Path, label: str) -> None:
    """Copy one closed evidence tree into the publisher's private workspace."""
    root_metadata = lstat_directory(source_root, label)
    destination_root.mkdir(mode=0o700)

    def visit(directory: Path, destination: Path, relative: str) -> None:
        before = lstat_directory(directory, f"{label} directory {relative}")
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            fail(f"{label} cannot be enumerated safely: {directory}")
        for child in children:
            child_relative = child.relative_to(source_root).as_posix()
            try:
                metadata = child.lstat()
            except OSError:
                fail(f"{label} entry is unavailable: {child}")
            if stat.S_ISLNK(metadata.st_mode):
                fail(f"{label} cannot contain symlinks: {child_relative}")
            child_destination = destination / child.name
            if stat.S_ISDIR(metadata.st_mode):
                child_destination.mkdir(mode=0o700)
                visit(child, child_destination, child_relative)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                fail(f"{label} cannot contain special files: {child_relative}")
            snapshot(child, child_destination)
        after = lstat_directory(directory, f"{label} directory {relative}")
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            fail(f"{label} changed while being snapshotted: {directory}")

    visit(source_root, destination_root, ".")
    root_after = lstat_directory(source_root, label)
    if (
        root_metadata.st_dev,
        root_metadata.st_ino,
        root_metadata.st_mode,
        root_metadata.st_mtime_ns,
        root_metadata.st_ctime_ns,
    ) != (
        root_after.st_dev,
        root_after.st_ino,
        root_after.st_mode,
        root_after.st_mtime_ns,
        root_after.st_ctime_ns,
    ):
        fail(f"{label} changed while being snapshotted: {source_root}")


def policy_identity(policy_path: Path) -> tuple[str, str, str]:
    helper_path = ROOT / "packaging/linux/compatibility_policy.py"
    if not helper_path.is_file() or helper_path.is_symlink():
        fail("canonical compatibility policy helper is unavailable")
    spec = importlib.util.spec_from_file_location("taiji_publisher_policy", helper_path)
    if spec is None or spec.loader is None:
        fail("canonical compatibility policy helper cannot be loaded")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    try:
        loaded = helper.load_and_validate(policy_path)
        if loaded["policy_id"] != "taiji-linux-amd64-deb-v1":
            fail("compatibility policy id is not canonical")
        return loaded["policy_id"], helper.canonical_sha256(loaded), loaded["package"]["maintainer"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        fail(f"compatibility policy is not canonical: {exc}")


def verify_signature(payload_path: Path, signature_path: Path, label: str) -> None:
    result = subprocess.run(
        [OPENSSL, "dgst", "-sha256", "-verify", str(PUBLIC_KEY), "-signature", str(signature_path), str(payload_path)],
        env=trusted_child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        fail(f"{label} detached signature verification failed")


def validate_ci_bundle(evidence_path: Path, source_commit: str) -> dict:
    if not RELEASE_VALIDATOR.is_file() or RELEASE_VALIDATOR.is_symlink():
        fail("source-controlled release validator is unavailable")
    spec = importlib.util.spec_from_file_location(
        "taiji_publisher_release_validator", RELEASE_VALIDATOR
    )
    if spec is None or spec.loader is None:
        fail("source-controlled release validator cannot be loaded")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    try:
        return validator.validate_github_ci_evidence_bundle(
            evidence_path, source_commit
        )
    except Exception as exc:
        fail(f"trusted GitHub CI v2 physical trio is invalid: {exc}")


def live_revalidate_ci(evidence_path: Path, source_commit: str) -> None:
    if not LIVE_CI_REVALIDATOR.is_file() or LIVE_CI_REVALIDATOR.is_symlink():
        fail("fixed GitHub CI live revalidator is unavailable")
    result = subprocess.run(
        [
            *TRUSTED_PYTHON_ARGV,
            str(LIVE_CI_REVALIDATOR),
            "--evidence",
            str(evidence_path),
            "--source-commit",
            source_commit,
        ],
        env=trusted_child_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        fail("github-ci-live-revalidation failed before publication")


def publish_noreplace(source: Path, destination: Path) -> None:
    if source.parent != destination.parent:
        fail("atomic publication requires source and destination to share a filesystem directory")
    if os.path.lexists(destination):
        fail(f"publication destination is already occupied: {destination}")
    parent_fd = os.open(source.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform.startswith("linux"):
            primitive = getattr(libc, "renameat2", None)
            if primitive is None:
                fail("renameat2 no-replace primitive is unavailable")
            primitive.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            primitive.restype = ctypes.c_int
            result = primitive(parent_fd, os.fsencode(source.name), parent_fd, os.fsencode(destination.name), 1)
        elif sys.platform == "darwin":
            primitive = getattr(libc, "renameatx_np", None)
            if primitive is None:
                fail("renameatx_np no-replace primitive is unavailable")
            primitive.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            primitive.restype = ctypes.c_int
            result = primitive(parent_fd, os.fsencode(source.name), parent_fd, os.fsencode(destination.name), 0x00000004)
        else:
            fail("no supported no-replace publication primitive")
        if result != 0:
            error = ctypes.get_errno()
            fail(f"no-replace publication failed: {os.strerror(error)}")
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def rollback_output(path: Path, identity: dict) -> None:
    try:
        value = path.lstat()
        if [value.st_dev, value.st_ino] != identity["directory"] or path.is_symlink() or sorted(item.name for item in path.iterdir()) != [identity["filename"]]:
            return
        child = path / identity["filename"]
        child_value = child.lstat()
        if [child_value.st_dev, child_value.st_ino] != identity["file"] or digest(child.read_bytes()) != identity["sha256"]:
            return
        child.unlink()
        path.rmdir()
    except OSError:
        return


def rollback_receipt(path: Path, identity: dict) -> None:
    try:
        value = path.lstat()
        if [value.st_dev, value.st_ino] != identity["directory"] or path.is_symlink() or {item.name for item in path.iterdir()} != set(identity["names"]):
            return
        for name, expected in identity["hashes"].items():
            child = path / name
            child_value = child.lstat()
            if child_value.st_nlink != 1 or digest(child.read_bytes()) != expected:
                return
        for name in identity["names"]:
            (path / name).unlink()
        path.rmdir()
    except OSError:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--delivery-dir", required=True, type=Path)
    parser.add_argument("--candidate-deb", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--certification-set", required=True, type=Path)
    parser.add_argument("--certification-signature", required=True, type=Path)
    parser.add_argument("--release-evidence", required=True, type=Path)
    parser.add_argument("--release-signature", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--receipt-root", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for command in (OPENSSL, DPKG_DEB):
        if not os.path.isfile(command) or not os.access(command, os.X_OK):
            fail(f"missing required command: {command}")
    for path, label in (
        (args.delivery_dir, "delivery directory"),
        (args.candidate_deb, "candidate DEB"),
        (args.policy, "compatibility policy"),
        (args.certification_set, "certification set"),
        (args.certification_signature, "certification signature"),
        (args.release_evidence, "release evidence"),
        (args.release_signature, "release signature"),
    ):
        if path.is_symlink() or not path.exists():
            fail(f"{label} is missing or a symlink: {path}")
    lstat_directory(args.delivery_dir, "delivery directory")
    for path, label in (
        (args.candidate_deb, "candidate DEB"),
        (args.policy, "compatibility policy"),
        (args.certification_set, "certification set"),
        (args.certification_signature, "certification signature"),
        (args.release_evidence, "release evidence"),
        (args.release_signature, "release signature"),
    ):
        lstat_regular(path, label)

    delivery_dir = args.delivery_dir.resolve()
    candidate = args.candidate_deb.resolve()
    policy = args.policy.resolve()
    cert_path = args.certification_set.resolve()
    cert_sig_path = args.certification_signature.resolve()
    release_path = args.release_evidence.resolve()
    release_sig_path = args.release_signature.resolve()
    ci_paths = {name: delivery_dir / name for name in CI_NAMES}
    for name, path in ci_paths.items():
        if path.is_symlink() or not path.exists():
            fail(f"trusted GitHub CI v2 artifact is missing or a symlink: {name}")
        lstat_regular(path, f"trusted GitHub CI v2 artifact {name}")
    output_parent = args.output_dir.parent
    if not output_parent.is_dir() or output_parent.is_symlink():
        fail("customer output parent must already be a real directory")
    output_parent = output_parent.resolve()
    output_dir = output_parent / args.output_dir.name
    if os.path.lexists(output_dir):
        fail(f"customer output directory must be new: {output_dir}")
    receipt_parent = args.receipt_root.parent
    if not receipt_parent.is_dir() or receipt_parent.is_symlink():
        fail("receipt root parent must already be a real directory")
    receipt_parent = receipt_parent.resolve()
    receipt_root = receipt_parent / args.receipt_root.name
    if os.path.lexists(receipt_root):
        lstat_directory(receipt_root, "receipt root")
    else:
        receipt_root.mkdir(mode=0o700)
    lstat_directory(receipt_root, "receipt root")

    try:
        child_env = trusted_child_environment()
        package_name = subprocess.check_output([DPKG_DEB, "-f", str(candidate), "Package"], env=child_env, text=True).strip()
        version = subprocess.check_output([DPKG_DEB, "-f", str(candidate), "Version"], env=child_env, text=True).strip()
        architecture = subprocess.check_output([DPKG_DEB, "-f", str(candidate), "Architecture"], env=child_env, text=True).strip()
        maintainer = subprocess.check_output([DPKG_DEB, "-f", str(candidate), "Maintainer"], env=child_env, text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f"cannot read candidate DEB metadata: {exc}")
    if package_name != "taiji-agent" or architecture != "amd64":
        fail("candidate DEB must be taiji-agent amd64")
    if not VERSION_RE.fullmatch(version):
        fail("candidate DEB version is invalid")
    customer_name = f"taiji-agent_{version}_amd64.deb"
    if candidate.name != customer_name:
        fail(f"candidate DEB basename must be {customer_name}")

    work = Path(tempfile.mkdtemp(prefix="taiji-single-deb-publish-")).resolve()
    os.chmod(work, 0o700)
    output_staging: Path | None = None
    receipt_staging: Path | None = None
    output_identity: dict | None = None
    receipt_identity: dict | None = None
    output_published = False
    receipt_published = False
    receipt_dir = receipt_root / "pending"
    try:
        snapshots = {}
        certification_bundle = work / "certification-bundle"
        certification_bundle.mkdir(mode=0o700)
        for source, name in (
            (candidate, "candidate.deb"),
            (policy, "compatibility-policy.json"),
            (cert_path, "certification-set.json"),
            (cert_sig_path, "certification-set.json.sig"),
            (release_path, "release-evidence.json"),
            (release_sig_path, "release-evidence.json.sig"),
            (ci_paths["github-ci-evidence.json"], "github-ci-evidence.json"),
            (ci_paths["github-ci-run-response.json"], "github-ci-run-response.json"),
            (ci_paths["github-ci-jobs-response.json"], "github-ci-jobs-response.json"),
        ):
            destination = (
                certification_bundle / name
                if name in {"certification-set.json", "certification-set.json.sig"}
                else work / name
            )
            payload_hash, identity = snapshot(source, destination)
            snapshots[name] = {"path": destination, "sha256": payload_hash, "identity": identity}
        snapshot_directory_tree(
            cert_path.parent / "records",
            certification_bundle / "records",
            "certification records",
        )
        snapshot_directory_tree(
            cert_path.parent / "offline-rehearsal",
            certification_bundle / "offline-rehearsal",
            "certification offline rehearsal",
        )

        policy_id, policy_sha, policy_maintainer = policy_identity(snapshots["compatibility-policy.json"]["path"])
        policy_document = strict_json(
            snapshots["compatibility-policy.json"]["path"].read_bytes(),
            "compatibility policy",
        )
        if maintainer != policy_maintainer:
            fail("candidate DEB maintainer does not match canonical compatibility policy")

        cert = strict_json(snapshots["certification-set.json"]["path"].read_bytes(), "certification set")
        release = strict_json(snapshots["release-evidence.json"]["path"].read_bytes(), "release evidence")
        reject_forbidden_keys(cert, "certification set")
        reject_forbidden_keys(release, "release evidence")
        if cert.get("schema") != "taiji-linux-certification-set/v1":
            fail("certification set must use taiji-linux-certification-set/v1")
        if release.get("schema") != "taiji-release-evidence/v3" or release.get("evidence_type") != "single-deb-publication":
            fail("release evidence must use taiji-release-evidence/v3")
        missing_toolchain = sorted(TOOLCHAIN_FIELDS - release.keys())
        if missing_toolchain:
            fail("release evidence is missing formal toolchain identity: " + ", ".join(missing_toolchain))
        electron = policy_document.get("elf", {}).get("electron_distribution", {})
        expected_toolchain = {
            "python_dependency_lock_status": "strict-locked",
            "python_lock_basename": "uv.lock",
            "python_version": PINNED_PYTHON_VERSION,
            "python_archive_sha256": PINNED_PYTHON_ARCHIVE_SHA256,
            "python_executable_sha256": PINNED_PYTHON_EXECUTABLE_SHA256,
            "uv_version": "0.12.2",
            "uv_archive_sha256": "d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4",
            "uv_executable_sha256": PINNED_UV_EXECUTABLE_SHA256,
            "node_version": "22.23.1",
            "node_archive_sha256": "9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578",
            "node_executable_sha256": PINNED_NODE_EXECUTABLE_SHA256,
            "electron_version": electron.get("version"),
            "electron_archive_sha256": electron.get("archive_sha256"),
            "electron_executable_sha256": PINNED_ELECTRON_EXECUTABLE_SHA256,
        }
        if any(release.get(key) != value for key, value in expected_toolchain.items()):
            fail("release evidence formal toolchain identity is not pinned")
        for key in TOOLCHAIN_FIELDS:
            if key.endswith("_sha256") and not SHA_RE.fullmatch(str(release.get(key))):
                fail(f"release evidence formal toolchain SHA256 is invalid: {key}")
        cert_challenge = cert.get("challenge_nonce")
        publication_challenge = release.get("challenge_nonce")
        if not CHALLENGE_RE.fullmatch(cert_challenge or "") or not CHALLENGE_RE.fullmatch(publication_challenge or ""):
            fail("certification/publication challenge is invalid")
        if cert_challenge == publication_challenge:
            fail("certification and publication challenges must be independent")
        cert_envelope = cert.get("challenge_envelope")
        publication_envelope = release.get("challenge_envelope")
        if (
            type(cert_envelope) is not dict
            or cert_envelope.get("purpose") != "certification"
            or cert_envelope.get("nonce") != cert_challenge
            or type(publication_envelope) is not dict
            or publication_envelope.get("purpose") != "publication"
            or publication_envelope.get("nonce") != publication_challenge
        ):
            fail("certification/publication canonical challenge envelope is invalid")
        candidate_sha = snapshots["candidate.deb"]["sha256"]
        cert_sha = snapshots["certification-set.json"]["sha256"]
        cert_sig_sha = snapshots["certification-set.json.sig"]["sha256"]
        expected_release = {
            "source_commit": release.get("source_commit"),
            "version": version,
            "architecture": "amd64",
            "deb_basename": customer_name,
            "deb_sha256": candidate_sha,
            "compatibility_policy_id": policy_id,
            "compatibility_policy_sha256": policy_sha,
            "certification_set_basename": "certification-set.json",
            "certification_set_sha256": cert_sha,
            "certification_set_signature_basename": "certification-set.json.sig",
            "certification_set_signature_sha256": cert_sig_sha,
            "customer_filename": customer_name,
            "customer_folder_contract": "exactly-one-deb",
        }
        if not COMMIT_RE.fullmatch(str(expected_release["source_commit"])):
            fail("release evidence source_commit is invalid")
        if any(release.get(key) != value for key, value in expected_release.items()):
            fail("release evidence does not bind the candidate DEB and policy")
        if release.get("maintainer") != policy_maintainer:
            fail("release evidence maintainer does not match canonical policy")
        if release.get("signing_public_key_fingerprint") != "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da":
            fail("release evidence signing trust anchor is invalid")
        ci_bundle = validate_ci_bundle(
            snapshots["github-ci-evidence.json"]["path"],
            release["source_commit"],
        )
        if (
            release.get("ci_evidence_basename") != "github-ci-evidence.json"
            or release.get("ci_evidence_sha256") != ci_bundle["evidence_sha256"]
        ):
            fail("release evidence does not bind the trusted GitHub CI v2 trio")
        live_revalidate_ci(
            snapshots["github-ci-evidence.json"]["path"],
            release["source_commit"],
        )
        cert_expected = {
            "source_commit": release["source_commit"],
            "version": version,
            "architecture": "amd64",
            "deb_basename": customer_name,
            "deb_sha256": candidate_sha,
            "compatibility_policy_id": policy_id,
            "compatibility_policy_sha256": policy_sha,
        }
        if any(cert.get(key) != value for key, value in cert_expected.items()):
            fail("certification set does not bind the candidate DEB and policy")
        if not SHA_RE.fullmatch(str(release.get("deb_sha256"))) or not SHA_RE.fullmatch(str(release.get("compatibility_policy_sha256"))):
            fail("release evidence SHA256 fields are invalid")
        verify_signature(snapshots["certification-set.json"]["path"], snapshots["certification-set.json.sig"]["path"], "certification set")
        verify_signature(snapshots["release-evidence.json"]["path"], snapshots["release-evidence.json.sig"]["path"], "release evidence")

        release_env = trusted_child_environment({
            "TAIJI_RELEASE_REPO_ROOT": str(ROOT),
            "TAIJI_RELEASE_SKIP_GIT_CHECK": "0",
            "TAIJI_RELEASE_REQUIRE_ARTIFACTS": "1",
            "TAIJI_DELIVERY_DIR": str(delivery_dir),
        })
        result = subprocess.run(
            ["/bin/bash", "-p",
                str(RELEASE_CHECK),
                "--delivery-dir",
                str(delivery_dir),
                "--certification-set",
                str(snapshots["certification-set.json"]["path"]),
                "--certification-signature",
                str(snapshots["certification-set.json.sig"]["path"]),
                "--release-evidence",
                str(snapshots["release-evidence.json"]["path"]),
                "--release-signature",
                str(snapshots["release-evidence.json.sig"]["path"]),
            ],
            env=release_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            fail("formal release-check failed")
        for item in snapshots.values():
            verify_identity(item["identity"])

        receipt_id = f"{version}-amd64-{candidate_sha[:12]}"
        receipt_dir = receipt_root / receipt_id
        if os.path.lexists(receipt_dir):
            fail(f"receipt identity is already reserved: {receipt_dir}")
        output_staging = Path(tempfile.mkdtemp(prefix=".taiji-single-deb.", dir=output_parent))
        os.chmod(output_staging, 0o700)
        shutil.copyfile(snapshots["candidate.deb"]["path"], output_staging / customer_name)
        os.chmod(output_staging / customer_name, 0o644)
        if {item.name for item in output_staging.iterdir()} != {customer_name} or digest((output_staging / customer_name).read_bytes()) != candidate_sha:
            fail("customer staging is not one bit-identical DEB")

        receipt_staging = Path(tempfile.mkdtemp(prefix=".taiji-receipt.", dir=receipt_root))
        os.chmod(receipt_staging, 0o700)
        for name, mode in (
            ("release-evidence.json", 0o600),
            ("release-evidence.json.sig", 0o600),
            ("certification-set.json", 0o600),
            ("certification-set.json.sig", 0o600),
            ("compatibility-policy.json", 0o644),
            ("github-ci-evidence.json", 0o600),
            ("github-ci-run-response.json", 0o600),
            ("github-ci-jobs-response.json", 0o600),
        ):
            shutil.copyfile(snapshots[name]["path"], receipt_staging / name)
            os.chmod(receipt_staging / name, mode)
        (receipt_staging / "deb.sha256").write_text(f"{candidate_sha}  {customer_name}\n", encoding="utf-8")
        os.chmod(receipt_staging / "deb.sha256", 0o600)
        if {item.name for item in receipt_staging.iterdir()} != RECEIPT_NAMES:
            fail("internal receipt allowlist mismatch")
        if (receipt_staging / "deb.sha256").read_text(encoding="utf-8") != f"{candidate_sha}  {customer_name}\n":
            fail("internal receipt DEB checksum mismatch")
        for name in ("certification-set.json", "release-evidence.json"):
            reject_forbidden_keys(strict_json((receipt_staging / name).read_bytes(), name), name)

        output_identity = {"filename": customer_name, "sha256": candidate_sha}
        publish_noreplace(output_staging, output_dir)
        output_staging = None
        output_published = True
        output_stat = output_dir.lstat()
        file_stat = (output_dir / customer_name).lstat()
        output_identity.update({"directory": [output_stat.st_dev, output_stat.st_ino], "file": [file_stat.st_dev, file_stat.st_ino]})

        receipt_hashes = {name: digest((receipt_staging / name).read_bytes()) for name in RECEIPT_NAMES}
        receipt_identity = {"names": sorted(RECEIPT_NAMES), "hashes": receipt_hashes}
        publish_noreplace(receipt_staging, receipt_dir)
        receipt_staging = None
        receipt_published = True
        receipt_stat = receipt_dir.lstat()
        receipt_identity["directory"] = [receipt_stat.st_dev, receipt_stat.st_ino]
        print(f"[OK] Customer single-file installer: {output_dir}/{customer_name}")
        print(f"[OK] Internal signed receipt: {receipt_dir}")
        return 0
    except (PublisherError, OSError, subprocess.CalledProcessError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        if output_published and output_identity is not None:
            rollback_output(output_dir, output_identity)
        if receipt_published and receipt_identity is not None:
            rollback_receipt(receipt_dir, receipt_identity)
        return 1
    finally:
        if output_staging is not None:
            shutil.rmtree(output_staging, ignore_errors=True)
        if receipt_staging is not None:
            shutil.rmtree(receipt_staging, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublisherError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
PY
