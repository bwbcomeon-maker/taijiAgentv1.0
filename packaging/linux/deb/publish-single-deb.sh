#!/usr/bin/env bash
# Publish one immutable customer DEB after the signed certification/release gates.
set -Eeuo pipefail
umask 077

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

python3 - "$@" <<'PY'
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
}


class PublisherError(RuntimeError):
    pass


def fail(message: str) -> "NoReturn":
    raise PublisherError(message)


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
        ["openssl", "dgst", "-sha256", "-verify", str(PUBLIC_KEY), "-signature", str(signature_path), str(payload_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        fail(f"{label} detached signature verification failed")


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
    for command in ("openssl", "dpkg-deb", "sha256sum"):
        if shutil.which(command) is None:
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
        package_name = subprocess.check_output(["dpkg-deb", "-f", str(candidate), "Package"], text=True).strip()
        version = subprocess.check_output(["dpkg-deb", "-f", str(candidate), "Version"], text=True).strip()
        architecture = subprocess.check_output(["dpkg-deb", "-f", str(candidate), "Architecture"], text=True).strip()
        maintainer = subprocess.check_output(["dpkg-deb", "-f", str(candidate), "Maintainer"], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f"cannot read candidate DEB metadata: {exc}")
    if package_name != "taiji-agent" or architecture != "amd64":
        fail("candidate DEB must be taiji-agent amd64")
    if not VERSION_RE.fullmatch(version):
        fail("candidate DEB version is invalid")
    customer_name = f"taiji-agent_{version}_amd64.deb"
    if candidate.name != customer_name:
        fail(f"candidate DEB basename must be {customer_name}")

    work = Path(tempfile.mkdtemp(prefix="taiji-single-deb-publish-"))
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
        for source, name in (
            (candidate, "candidate.deb"),
            (policy, "compatibility-policy.json"),
            (cert_path, "certification-set.json"),
            (cert_sig_path, "certification-set.json.sig"),
            (release_path, "release-evidence.json"),
            (release_sig_path, "release-evidence.json.sig"),
        ):
            payload_hash, identity = snapshot(source, work / name)
            snapshots[name] = {"path": work / name, "sha256": payload_hash, "identity": identity}

        policy_id, policy_sha, policy_maintainer = policy_identity(snapshots["compatibility-policy.json"]["path"])
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
        cert_challenge = cert.get("challenge_nonce")
        publication_challenge = release.get("challenge_nonce")
        if not CHALLENGE_RE.fullmatch(cert_challenge or "") or not CHALLENGE_RE.fullmatch(publication_challenge or ""):
            fail("certification/publication challenge is invalid")
        if cert_challenge == publication_challenge:
            fail("certification and publication challenges must be independent")
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

        release_env = os.environ.copy()
        release_env.update({
            "TAIJI_RELEASE_REPO_ROOT": str(ROOT),
            "TAIJI_RELEASE_SKIP_GIT_CHECK": "0",
            "TAIJI_RELEASE_REQUIRE_ARTIFACTS": "1",
            "TAIJI_DELIVERY_DIR": str(delivery_dir),
            "TAIJI_CERTIFICATION_CHALLENGE": cert_challenge,
            "TAIJI_PUBLICATION_CHALLENGE": publication_challenge,
        })
        result = subprocess.run(
            ["bash", str(RELEASE_CHECK), "--delivery-dir", str(delivery_dir), "--certification-set", str(cert_path), "--certification-signature", str(cert_sig_path), "--release-evidence", str(release_path), "--release-signature", str(release_sig_path)],
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
