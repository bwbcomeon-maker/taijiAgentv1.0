#!/usr/bin/env python3
"""Issue, verify, and reserve canonical Taiji signing challenges.

The replay ledger protects one controlled signing account against accidental
reuse.  The account owner can delete local ``used-nonces`` state, so this
deliberately does not claim tamper resistance, cross-host, or global uniqueness.
"""

import argparse
import hashlib
import json
import os
import pwd
import re
import secrets
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple


SCHEMA = "taiji-signing-challenge/v1"
PURPOSES = {"certification", "publication"}
FIELDS = {
    "schema",
    "purpose",
    "nonce",
    "issued_at_utc",
    "expires_at_utc",
    "source_commit",
    "deb_basename",
    "deb_sha256",
}
NONCE_RE = re.compile(r"^[0-9a-f]{64,128}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEB_RE = re.compile(
    r"^taiji-agent_[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}_amd64\.deb$"
)
MAX_ENVELOPE_BYTES = 16 * 1024
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
MAX_TTL_SECONDS = 7 * 24 * 60 * 60


class ChallengeEnvelopeError(ValueError):
    """Raised when a challenge envelope or signer state is unsafe."""


def _without_duplicates(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    value = {}  # type: Dict[str, Any]
    for key, item in pairs:
        if key in value:
            raise ChallengeEnvelopeError("challenge envelope contains duplicate fields")
        value[key] = item
    return value


def parse_envelope_bytes(payload: bytes) -> Dict[str, Any]:
    if not payload or len(payload) > MAX_ENVELOPE_BYTES:
        raise ChallengeEnvelopeError("challenge envelope size is invalid")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_without_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ChallengeEnvelopeError(
            "challenge envelope must be strict UTF-8 JSON"
        ) from exc
    if type(value) is not dict:
        raise ChallengeEnvelopeError("challenge envelope must be a JSON object")
    return value


def canonical_bytes(envelope: Dict[str, Any]) -> bytes:
    return (
        json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ChallengeEnvelopeError("{} must be a UTC ISO8601 timestamp".format(label))
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ChallengeEnvelopeError(
            "{} must be a UTC ISO8601 timestamp".format(label)
        ) from exc
    if parsed.tzinfo is None:
        raise ChallengeEnvelopeError("{} must include UTC timezone".format(label))
    return parsed.astimezone(timezone.utc)


def validate_structure(envelope: Dict[str, Any]) -> Tuple[datetime, datetime]:
    if type(envelope) is not dict or set(envelope) != FIELDS:
        raise ChallengeEnvelopeError("challenge envelope fields are not exact")
    if envelope.get("schema") != SCHEMA:
        raise ChallengeEnvelopeError("challenge envelope schema is invalid")
    if envelope.get("purpose") not in PURPOSES:
        raise ChallengeEnvelopeError("challenge envelope purpose is invalid")
    if type(envelope.get("nonce")) is not str or NONCE_RE.fullmatch(
        envelope["nonce"]
    ) is None:
        raise ChallengeEnvelopeError("challenge envelope nonce is invalid")
    if type(envelope.get("source_commit")) is not str or COMMIT_RE.fullmatch(
        envelope["source_commit"]
    ) is None:
        raise ChallengeEnvelopeError("challenge envelope source_commit is invalid")
    if type(envelope.get("deb_basename")) is not str or DEB_RE.fullmatch(
        envelope["deb_basename"]
    ) is None:
        raise ChallengeEnvelopeError("challenge envelope deb_basename is invalid")
    if type(envelope.get("deb_sha256")) is not str or SHA256_RE.fullmatch(
        envelope["deb_sha256"]
    ) is None:
        raise ChallengeEnvelopeError("challenge envelope deb_sha256 is invalid")
    issued = _parse_utc(envelope["issued_at_utc"], "issued_at_utc")
    expires = _parse_utc(envelope["expires_at_utc"], "expires_at_utc")
    if expires <= issued:
        raise ChallengeEnvelopeError("challenge envelope expiry must follow issuance")
    if expires - issued > timedelta(seconds=MAX_TTL_SECONDS):
        raise ChallengeEnvelopeError(
            "challenge envelope exceeds the fixed maximum lifetime"
        )
    return issued, expires


def verify_envelope(
    envelope: Dict[str, Any],
    *,
    purpose: str,
    source_commit: str,
    deb_basename: str,
    deb_sha256: str,
    at: Optional[datetime] = None,
    require_active: bool = False,
    evidence_times: Iterable[Any] = (),
    evidence_not_after: Optional[Any] = None,
) -> Dict[str, Any]:
    issued, expires = validate_structure(envelope)
    expected = {
        "purpose": purpose,
        "source_commit": source_commit,
        "deb_basename": deb_basename,
        "deb_sha256": deb_sha256,
    }
    for key, value in expected.items():
        if envelope[key] != value:
            raise ChallengeEnvelopeError(
                "challenge envelope {} does not match the signed artifact".format(key)
            )
    current_value = at or datetime.now(timezone.utc)
    if current_value.tzinfo is None:
        raise ChallengeEnvelopeError("challenge verification time must include timezone")
    current = current_value.astimezone(timezone.utc)
    if require_active:
        if current < issued:
            raise ChallengeEnvelopeError("challenge envelope is issued in the future")
        if current > expires:
            raise ChallengeEnvelopeError("challenge envelope is expired")
    evidence_ceiling = None
    if evidence_not_after is not None:
        if isinstance(evidence_not_after, datetime):
            if evidence_not_after.tzinfo is None:
                raise ChallengeEnvelopeError(
                    "evidence ordering time must include timezone"
                )
            evidence_ceiling = evidence_not_after.astimezone(timezone.utc)
        else:
            evidence_ceiling = _parse_utc(
                evidence_not_after,
                "evidence ordering time",
            )
    for index, value in enumerate(evidence_times):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ChallengeEnvelopeError(
                    "evidence timestamp must include timezone"
                )
            evidence_time = value.astimezone(timezone.utc)
        else:
            evidence_time = _parse_utc(value, "evidence timestamp")
        if evidence_time < issued or evidence_time > expires:
            raise ChallengeEnvelopeError(
                "evidence timestamp {} is outside the challenge window".format(index)
            )
        if require_active and evidence_time > current:
            raise ChallengeEnvelopeError(
                "evidence timestamp {} is in the future".format(index)
            )
        if evidence_ceiling is not None and evidence_time > evidence_ceiling:
            raise ChallengeEnvelopeError(
                "evidence timestamp {} violates evidence ordering".format(index)
            )
    return dict(envelope)


def _regular_bytes(path: Path, label: str, maximum: int) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise ChallengeEnvelopeError("{} must be an absolute regular file".format(label))
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size <= 0
        or before.st_size > maximum
    ):
        raise ChallengeEnvelopeError(
            "{} must be a bounded single-link regular file".format(label)
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_nlink,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    try:
        opened = os.fstat(descriptor)
        if identity(opened) != identity(before):
            raise ChallengeEnvelopeError("{} changed before open".format(label))
        chunks = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise ChallengeEnvelopeError("{} was truncated".format(label))
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ChallengeEnvelopeError("{} grew while read".format(label))
        after = os.fstat(descriptor)
        current = path.lstat()
        if identity(opened) != identity(after) or identity(opened) != identity(current):
            raise ChallengeEnvelopeError("{} changed while read".format(label))
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def load_envelope_file(path: Path) -> Dict[str, Any]:
    return parse_envelope_bytes(_regular_bytes(path, "challenge envelope", MAX_ENVELOPE_BYTES))


def _open_state_directory(
    parent_fd: int,
    name: str,
    *,
    exact_owner_only: bool,
) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise ChallengeEnvelopeError("signer state contains a symlink or unsafe node") from exc
    metadata = os.fstat(descriptor)
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or (exact_owner_only and mode != 0o700)
        or (not exact_owner_only and mode & 0o022)
    ):
        os.close(descriptor)
        raise ChallengeEnvelopeError("signer state directory must be owner-only")
    return descriptor


def reserve_nonce(
    envelope: Dict[str, Any],
    *,
    evidence_sha256: str,
    public_key_fingerprint: str,
    account_home: Optional[Path] = None,
    reserved_at: Optional[datetime] = None,
) -> Path:
    validate_structure(envelope)
    if not SHA256_RE.fullmatch(evidence_sha256 or ""):
        raise ChallengeEnvelopeError("evidence_sha256 is invalid")
    if not SHA256_RE.fullmatch(public_key_fingerprint or ""):
        raise ChallengeEnvelopeError("public-key fingerprint is invalid")
    home = account_home or Path(pwd.getpwuid(os.getuid()).pw_dir)
    if not home.is_absolute() or home.is_symlink():
        raise ChallengeEnvelopeError("signing account home is unsafe")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(str(home), flags)
    except OSError as exc:
        raise ChallengeEnvelopeError("signing account home is unsafe") from exc
    opened = [descriptor]
    components = (
        (".local", False),
        ("state", False),
        ("taiji-release-evidence", True),
        ("signers", True),
        (public_key_fingerprint, True),
        ("used-nonces", True),
    )
    try:
        for name, exact in components:
            descriptor = _open_state_directory(
                descriptor,
                name,
                exact_owner_only=exact,
            )
            opened.append(descriptor)
        record_name = envelope["nonce"] + ".used"
        record = {
            "schema": "taiji-signing-challenge-reservation/v1",
            "purpose": envelope["purpose"],
            "nonce": envelope["nonce"],
            "challenge_envelope_sha256": hashlib.sha256(
                canonical_bytes(envelope)
            ).hexdigest(),
            "evidence_sha256": evidence_sha256,
            "reserved_at_utc": (
                reserved_at or datetime.now(timezone.utc)
            ).astimezone(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
        }
        payload = canonical_bytes(record)
        try:
            record_fd = os.open(
                record_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=descriptor,
            )
        except FileExistsError as exc:
            raise ChallengeEnvelopeError("challenge nonce was already used") from exc
        try:
            view = memoryview(payload)
            while view:
                written = os.write(record_fd, view)
                if written <= 0:
                    raise ChallengeEnvelopeError("challenge reservation write failed")
                view = view[written:]
            os.fsync(record_fd)
            metadata = os.fstat(record_fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ChallengeEnvelopeError("challenge reservation is not owner-only")
        finally:
            os.close(record_fd)
        os.fsync(descriptor)
        return (
            home
            / ".local/state/taiji-release-evidence/signers"
            / public_key_fingerprint
            / "used-nonces"
            / record_name
        )
    finally:
        for item in reversed(opened):
            os.close(item)


def _write_new(path: Path, payload: bytes) -> None:
    if not path.is_absolute() or path.parent.is_symlink() or not path.parent.is_dir():
        raise ChallengeEnvelopeError("output must have an existing real parent")
    descriptor = os.open(
        str(path),
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
                raise ChallengeEnvelopeError("output write failed")
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


def _deb_identity(path: Path) -> Tuple[str, str]:
    if DEB_RE.fullmatch(path.name) is None:
        raise ChallengeEnvelopeError("candidate DEB basename is invalid")
    if not path.is_absolute() or path.is_symlink():
        raise ChallengeEnvelopeError("candidate DEB must be an absolute regular file")
    before = path.lstat()
    maximum = 2 * 1024 * 1024 * 1024
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size <= 0
        or before.st_size > maximum
    ):
        raise ChallengeEnvelopeError(
            "candidate DEB must be a bounded single-link regular file"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_nlink,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    try:
        opened = os.fstat(descriptor)
        if identity(opened) != identity(before):
            raise ChallengeEnvelopeError("candidate DEB changed before open")
        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise ChallengeEnvelopeError("candidate DEB was truncated")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ChallengeEnvelopeError("candidate DEB grew while read")
        after = os.fstat(descriptor)
        current = path.lstat()
        if identity(opened) != identity(after) or identity(opened) != identity(current):
            raise ChallengeEnvelopeError("candidate DEB changed while read")
        return path.name, digest.hexdigest()
    finally:
        os.close(descriptor)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue", allow_abbrev=False)
    issue.add_argument("--purpose", choices=sorted(PURPOSES), required=True)
    issue.add_argument("--source-commit", required=True)
    issue.add_argument("--deb", required=True, type=Path)
    issue.add_argument("--output", required=True, type=Path)
    issue.add_argument("--ttl-seconds", type=int, default=3600)
    issue.add_argument("--nonce")

    verify = commands.add_parser("verify", allow_abbrev=False)
    verify.add_argument("--envelope", required=True, type=Path)
    verify.add_argument("--purpose", choices=sorted(PURPOSES), required=True)
    verify.add_argument("--source-commit", required=True)
    verify.add_argument("--deb", required=True, type=Path)
    verify.add_argument("--evidence-time", action="append", default=[])
    verify.add_argument("--require-active", action="store_true")

    reserve = commands.add_parser("reserve", allow_abbrev=False)
    reserve.add_argument("--envelope", required=True, type=Path)
    reserve.add_argument("--evidence", required=True, type=Path)
    reserve.add_argument("--public-key-fingerprint", required=True)
    reserve.add_argument("--purpose", choices=sorted(PURPOSES), required=True)
    reserve.add_argument("--source-commit", required=True)
    reserve.add_argument("--deb-basename", required=True)
    reserve.add_argument("--deb-sha256", required=True)
    reserve.add_argument("--evidence-time", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = _parse_args(argv)
        if args.command == "issue":
            if not COMMIT_RE.fullmatch(args.source_commit or ""):
                raise ChallengeEnvelopeError("source_commit is invalid")
            if args.ttl_seconds <= 0:
                raise ChallengeEnvelopeError("ttl-seconds must be positive")
            if args.ttl_seconds > MAX_TTL_SECONDS:
                raise ChallengeEnvelopeError(
                    "ttl-seconds exceeds the fixed maximum lifetime"
                )
            deb_basename, deb_sha256 = _deb_identity(args.deb)
            now = datetime.now(timezone.utc).replace(microsecond=0)
            envelope = {
                "schema": SCHEMA,
                "purpose": args.purpose,
                "nonce": args.nonce or secrets.token_hex(32),
                "issued_at_utc": now.isoformat().replace("+00:00", "Z"),
                "expires_at_utc": (now + timedelta(seconds=args.ttl_seconds))
                .isoformat()
                .replace("+00:00", "Z"),
                "source_commit": args.source_commit,
                "deb_basename": deb_basename,
                "deb_sha256": deb_sha256,
            }
            validate_structure(envelope)
            _write_new(args.output, canonical_bytes(envelope))
            print("challenge-envelope-issued\t{}".format(args.output))
            return 0
        envelope = load_envelope_file(args.envelope)
        if args.command == "verify":
            deb_basename, deb_sha256 = _deb_identity(args.deb)
            verify_envelope(
                envelope,
                purpose=args.purpose,
                source_commit=args.source_commit,
                deb_basename=deb_basename,
                deb_sha256=deb_sha256,
                require_active=args.require_active,
                evidence_times=args.evidence_time,
            )
            print("challenge-envelope-valid\t{}".format(args.envelope))
            return 0
        evidence = _regular_bytes(args.evidence, "evidence", MAX_EVIDENCE_BYTES)
        verify_envelope(
            envelope,
            purpose=args.purpose,
            source_commit=args.source_commit,
            deb_basename=args.deb_basename,
            deb_sha256=args.deb_sha256,
            require_active=True,
            evidence_times=args.evidence_time,
        )
        record = reserve_nonce(
            envelope,
            evidence_sha256=hashlib.sha256(evidence).hexdigest(),
            public_key_fingerprint=args.public_key_fingerprint,
        )
        print("challenge-envelope-reserved\t{}".format(record))
        return 0
    except (ChallengeEnvelopeError, OSError, KeyError, TypeError, ValueError) as exc:
        print("challenge-envelope-failed\t{}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
