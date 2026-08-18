#!/usr/bin/env python3
"""Assemble the immutable v3 single-DEB publication evidence envelope."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "taiji-release-evidence/v3"
CERTIFICATION_SCHEMA = "taiji-linux-certification-set/v1"
POLICY_ID = "taiji-linux-amd64-deb-v1"
PUBLIC_KEY = ROOT / "tools/taiji-release-evidence/signing-public.pem"
PUBLIC_KEY_FINGERPRINT = "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
DEB_RE = re.compile(r"^taiji-agent_[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}_amd64\.deb$")
MAX_JSON_BYTES = 1024 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
FORMAL_GATES = {
    "candidate_deb_unchanged": "PASS",
    "canonical_policy": "PASS",
    "certification_set": "PASS",
    "certification_signature": "PASS",
    "github_ci_gate": "PASS",
    "manifest_binding": "PASS",
}
PINNED_UV_VERSION = "0.12.2"
PINNED_UV_ARCHIVE_SHA256 = "d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4"
PINNED_UV_EXECUTABLE_SHA256 = "72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2"
PINNED_NODE_VERSION = "22.23.1"
PINNED_NODE_ARCHIVE_SHA256 = "9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"
PINNED_NODE_EXECUTABLE_SHA256 = "93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068"
PINNED_PYTHON_VERSION = "3.11.15"
PINNED_PYTHON_ARCHIVE_SHA256 = "2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a"
PINNED_PYTHON_EXECUTABLE_SHA256 = "5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba"
PINNED_ELECTRON_EXECUTABLE_SHA256 = "c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d"
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


class ReleaseEvidenceError(ValueError):
    """Raised when a publication envelope cannot be trusted."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseEvidenceError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _read_regular(path: Path, label: str, *, maximum: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseEvidenceError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ReleaseEvidenceError(f"{label} must be a regular single-link file")
    if metadata.st_size <= 0 or metadata.st_size > maximum:
        raise ReleaseEvidenceError(f"{label} has an invalid size")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseEvidenceError(f"{label} cannot be opened safely") from exc
    try:
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise ReleaseEvidenceError(f"{label} was truncated while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (
            metadata.st_ino != after.st_ino
            or metadata.st_dev != after.st_dev
            or metadata.st_size != after.st_size
            or metadata.st_mtime_ns != after.st_mtime_ns
            or metadata.st_ctime_ns != after.st_ctime_ns
        ):
            raise ReleaseEvidenceError(f"{label} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    payload = _read_regular(path, label, maximum=MAX_JSON_BYTES)
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ReleaseEvidenceError) as exc:
        raise ReleaseEvidenceError(f"{label} is not strict UTF-8 JSON") from exc
    if type(value) is not dict:
        raise ReleaseEvidenceError(f"{label} must be a JSON object")
    return value, payload


def _sha(path: Path, label: str, *, maximum: int = 1024 * 1024 * 1024) -> str:
    return hashlib.sha256(_read_regular(path, label, maximum=maximum)).hexdigest()


def _require_sha(value: Any, label: str) -> str:
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        raise ReleaseEvidenceError(f"{label} must be a lowercase SHA256")
    return value


def _require_text(value: Any, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value or len(value) > 256 or any(c in value for c in "\r\n\t"):
        raise ReleaseEvidenceError(f"{label} is invalid")
    if pattern is not None and not pattern.fullmatch(value):
        raise ReleaseEvidenceError(f"{label} is invalid")
    return value


def _canonical_policy(path: Path) -> tuple[str, str]:
    policy, _ = _load_json(path, "compatibility policy")
    if policy.get("policy_id") != POLICY_ID:
        raise ReleaseEvidenceError("compatibility policy id is not canonical")
    helper_path = ROOT / "packaging/linux/compatibility_policy.py"
    if not helper_path.is_file():
        raise ReleaseEvidenceError("canonical policy helper is unavailable")
    spec = importlib.util.spec_from_file_location("taiji_release_policy_identity", helper_path)
    if spec is None or spec.loader is None:
        raise ReleaseEvidenceError("cannot load canonical policy helper")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    try:
        loaded = helper.load_and_validate(path)
        return loaded["policy_id"], helper.canonical_sha256(loaded)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ReleaseEvidenceError("compatibility policy is not canonical") from exc


def _validate_ci_evidence(
    path: Path,
    *,
    source_commit: str,
    output_parent: Path,
) -> tuple[dict[str, Any], str]:
    if path.name != "github-ci-evidence.json" or path.parent.resolve() != output_parent.resolve():
        raise ReleaseEvidenceError("CI evidence must be github-ci-evidence.json beside the release evidence output")
    validator = _load_certification_validator()
    try:
        result = validator.validate_github_ci_evidence_bundle(path, source_commit)
    except Exception as exc:
        raise ReleaseEvidenceError(f"GitHub CI v2 physical trio is invalid: {exc}") from exc
    digest = result.get("evidence_sha256")
    if type(digest) is not str or SHA256_RE.fullmatch(digest) is None:
        raise ReleaseEvidenceError("GitHub CI v2 validator returned an invalid evidence hash")
    return {}, digest


def _manifest_toolchain(manifest: dict[str, Any], policy: dict[str, Any]) -> dict[str, str]:
    missing = sorted(TOOLCHAIN_FIELDS - manifest.keys())
    if missing:
        raise ReleaseEvidenceError(
            "current v3 manifest is missing formal toolchain identity: " + ", ".join(missing)
        )
    electron = policy.get("elf", {}).get("electron_distribution", {})
    expected = {
        "python_dependency_lock_status": "strict-locked",
        "python_lock_basename": "uv.lock",
        "python_version": PINNED_PYTHON_VERSION,
        "python_archive_sha256": PINNED_PYTHON_ARCHIVE_SHA256,
        "python_executable_sha256": PINNED_PYTHON_EXECUTABLE_SHA256,
        "uv_version": PINNED_UV_VERSION,
        "uv_archive_sha256": PINNED_UV_ARCHIVE_SHA256,
        "uv_executable_sha256": PINNED_UV_EXECUTABLE_SHA256,
        "node_version": PINNED_NODE_VERSION,
        "node_archive_sha256": PINNED_NODE_ARCHIVE_SHA256,
        "node_executable_sha256": PINNED_NODE_EXECUTABLE_SHA256,
        "electron_version": electron.get("version"),
        "electron_archive_sha256": electron.get("archive_sha256"),
        "electron_executable_sha256": PINNED_ELECTRON_EXECUTABLE_SHA256,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ReleaseEvidenceError(f"manifest {key} is not the pinned formal identity")
    for key in TOOLCHAIN_FIELDS:
        if key.endswith("_sha256"):
            _require_sha(manifest.get(key), f"manifest {key}")
    return {key: manifest[key] for key in TOOLCHAIN_FIELDS}


def _verify_signature(payload_path: Path, signature_path: Path) -> str:
    if not PUBLIC_KEY.is_file() or PUBLIC_KEY.is_symlink():
        raise ReleaseEvidenceError("fixed signing public key is unavailable")
    signature = _read_regular(signature_path, "certification-set signature", maximum=MAX_SIGNATURE_BYTES)
    try:
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(PUBLIC_KEY), "-signature", str(signature_path), str(payload_path)],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ReleaseEvidenceError("openssl is unavailable for certification signature verification") from exc
    if result.returncode != 0:
        raise ReleaseEvidenceError("certification-set signature verification failed")
    return hashlib.sha256(signature).hexdigest()


def _load_certification_validator():
    path = ROOT / "scripts/validate-taiji-release-evidence.py"
    spec = importlib.util.spec_from_file_location("taiji_release_certification_validator", path)
    if spec is None or spec.loader is None:
        raise ReleaseEvidenceError("cannot load certification-set validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_challenge_helper():
    candidates = (
        Path(__file__).resolve().with_name("taiji-challenge-envelope.py"),
        ROOT / "scripts/taiji-challenge-envelope.py",
    )
    for path in dict.fromkeys(candidates):
        if not path.is_file() or path.is_symlink():
            continue
        spec = importlib.util.spec_from_file_location(
            "taiji_publication_challenge_envelope",
            path,
        )
        if spec is None or spec.loader is None:
            raise ReleaseEvidenceError("cannot load challenge-envelope helper")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise ReleaseEvidenceError("challenge-envelope helper is missing")


def _validate_certification_set(
    certification_path: Path,
    certification: dict[str, Any],
    certification_signature_path: Path,
    *,
    manifest: dict[str, Any],
    deb_hash: str,
    policy_id: str,
    policy_sha: str,
    publication_challenge: str,
) -> str:
    if certification.get("schema") != CERTIFICATION_SCHEMA:
        raise ReleaseEvidenceError("current publication requires certification-set schema v1")
    cert_challenge = _require_text(certification.get("challenge_nonce"), "certification-set challenge")
    if not CHALLENGE_RE.fullmatch(cert_challenge):
        raise ReleaseEvidenceError("certification-set challenge is invalid")
    if cert_challenge == publication_challenge:
        raise ReleaseEvidenceError("certification and publication challenges must be independent")
    if certification.get("source_commit") != manifest.get("source_commit"):
        raise ReleaseEvidenceError("certification-set source_commit does not match manifest")
    if certification.get("version") != manifest.get("version"):
        raise ReleaseEvidenceError("certification-set version does not match manifest")
    if certification.get("architecture") != "amd64" or certification.get("deb_basename") != manifest.get("deb_basename"):
        raise ReleaseEvidenceError("certification-set DEB identity does not match manifest")
    if certification.get("deb_sha256") != deb_hash:
        raise ReleaseEvidenceError("certification-set DEB hash does not match candidate")
    if certification.get("compatibility_policy_id") != policy_id or certification.get("compatibility_policy_sha256") != policy_sha:
        raise ReleaseEvidenceError("certification-set policy identity does not match candidate")
    _require_sha(certification.get("deb_sha256"), "certification-set deb_sha256")
    _require_sha(certification.get("compatibility_policy_sha256"), "certification-set compatibility_policy_sha256")
    signature_hash = _verify_signature(certification_path, certification_signature_path)
    validator = _load_certification_validator()
    binding = validator.BuildBinding(
        source_commit=manifest["source_commit"],
        version=manifest["version"],
        architecture="amd64",
        deb_basename=manifest["deb_basename"],
        deb_sha256=deb_hash,
        compatibility_policy_id=policy_id,
        compatibility_policy_sha256=policy_sha,
        electron_executable_sha256=manifest["electron_executable_sha256"],
        desktop_entry_sha256=manifest["desktop_entry_sha256"],
        **{field: manifest[field] for field in TOOLCHAIN_FIELDS if field != "electron_executable_sha256"},
    )
    matrix_path = ROOT / "packaging/linux/certification-matrix.json"
    args = SimpleNamespace(challenge=cert_challenge, matrix=matrix_path)
    try:
        validator.validate_certification_set_v1(certification, certification_path, args, binding)
    except Exception as exc:
        raise ReleaseEvidenceError(f"certification-set contract is invalid: {exc}") from exc
    return signature_hash


def _write_new(path: Path, payload: bytes) -> None:
    if not path.is_absolute() or path.exists() or path.is_symlink():
        raise ReleaseEvidenceError("release evidence output must be a new absolute file")
    if path.parent.is_symlink() or not path.parent.is_dir() or path.parent.resolve() != path.parent:
        raise ReleaseEvidenceError("release evidence output parent must be a real directory")
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ReleaseEvidenceError("release evidence output write failed")
            view = view[written:]
        os.fsync(descriptor)
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)


def assemble(args: argparse.Namespace) -> Path:
    for path, label in (
        (args.manifest, "manifest"),
        (args.deb, "candidate DEB"),
        (args.policy, "compatibility policy"),
        (args.certification_set, "certification set"),
        (args.certification_signature, "certification signature"),
        (args.output, "output"),
        (args.challenge_envelope, "publication challenge envelope"),
    ):
        if not path.is_absolute():
            raise ReleaseEvidenceError(f"{label} path must be absolute")
    manifest, _ = _load_json(args.manifest, "release manifest")
    if manifest.get("schema") != "taiji-package-manifest/v3":
        raise ReleaseEvidenceError("current publication requires manifest schema v3")
    if "target_baseline_profile_id" in manifest or "target_baseline_sha256" in manifest:
        raise ReleaseEvidenceError("v3 publication must not contain target baseline fields")
    _require_text(manifest.get("package"), "manifest package")
    if manifest.get("package") != "taiji-agent" or manifest.get("architecture") != "amd64":
        raise ReleaseEvidenceError("manifest package or architecture is invalid")
    source_commit = _require_text(manifest.get("source_commit"), "manifest source_commit", COMMIT_RE)
    version = _require_text(manifest.get("version"), "manifest version", VERSION_RE)
    deb_basename = _require_text(manifest.get("deb_basename"), "manifest deb_basename", DEB_RE)
    if deb_basename != f"taiji-agent_{version}_amd64.deb" or args.deb.name != deb_basename:
        raise ReleaseEvidenceError("manifest and candidate DEB basename do not match")
    deb_hash_before = _sha(args.deb, "candidate DEB")
    if manifest.get("deb_sha256") != deb_hash_before:
        raise ReleaseEvidenceError("candidate DEB hash does not match manifest")
    challenge_helper = _load_challenge_helper()
    challenge_envelope = challenge_helper.load_envelope_file(args.challenge_envelope)
    publication_challenge = challenge_envelope.get("nonce")
    _require_sha(manifest.get("electron_executable_sha256"), "manifest electron hash")
    _require_sha(manifest.get("desktop_entry_sha256"), "manifest desktop entry hash")
    policy_document, _ = _load_json(args.policy, "compatibility policy")
    toolchain = _manifest_toolchain(manifest, policy_document)
    policy_id, policy_sha = _canonical_policy(args.policy)
    if manifest.get("compatibility_policy_id") != policy_id or manifest.get("compatibility_policy_sha256") != policy_sha:
        raise ReleaseEvidenceError("manifest policy identity does not match canonical policy")
    certification, _ = _load_json(args.certification_set, "certification set")
    certification_signature_hash = _validate_certification_set(
        args.certification_set,
        certification,
        args.certification_signature,
        manifest=manifest,
        deb_hash=deb_hash_before,
        policy_id=policy_id,
        policy_sha=policy_sha,
        publication_challenge=publication_challenge,
    )
    deb_hash_after = _sha(args.deb, "candidate DEB")
    if deb_hash_after != deb_hash_before:
        raise ReleaseEvidenceError("candidate DEB changed while assembling publication evidence")
    certification_hash = _sha(args.certification_set, "certification set")
    _ci_data, ci_evidence_hash = _validate_ci_evidence(
        args.ci_evidence,
        source_commit=source_commit,
        output_parent=args.output.parent,
    )
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    challenge_helper.verify_envelope(
        challenge_envelope,
        purpose="publication",
        source_commit=source_commit,
        deb_basename=deb_basename,
        deb_sha256=deb_hash_before,
        require_active=True,
        evidence_times=(generated,),
    )
    evidence = {
        "schema": SCHEMA,
        "evidence_type": "single-deb-publication",
        "generated_at_utc": generated,
        "challenge_nonce": publication_challenge,
        "challenge_envelope": challenge_envelope,
        "source_commit": source_commit,
        "version": version,
        "architecture": "amd64",
        "deb_basename": deb_basename,
        "deb_sha256": deb_hash_before,
        "compatibility_policy_id": policy_id,
        "compatibility_policy_sha256": policy_sha,
        **toolchain,
        "certification_set_basename": args.certification_set.name,
        "certification_set_sha256": certification_hash,
        "certification_set_signature_basename": args.certification_signature.name,
        "certification_set_signature_sha256": certification_signature_hash,
        "ci_evidence_basename": args.ci_evidence.name,
        "ci_evidence_sha256": ci_evidence_hash,
        "maintainer": _require_text(manifest.get("maintainer"), "manifest maintainer"),
        "customer_filename": deb_basename,
        "customer_folder_contract": "exactly-one-deb",
        "signing_public_key_fingerprint": PUBLIC_KEY_FINGERPRINT,
        "formal_gates": dict(FORMAL_GATES),
    }
    payload = (json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _write_new(args.output, payload)
    return args.output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--deb", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--certification-set", required=True, type=Path)
    parser.add_argument("--certification-signature", required=True, type=Path)
    parser.add_argument("--ci-evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--challenge-envelope", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        output = assemble(parse_args(argv))
    except (ReleaseEvidenceError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"release-evidence-assembly-failed\t{exc}", file=os.sys.stderr)
        return 1
    print(f"release-evidence-assembled\t{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
