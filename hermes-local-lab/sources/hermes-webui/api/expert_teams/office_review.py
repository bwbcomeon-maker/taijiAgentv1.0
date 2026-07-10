"""Server-issued, one-time Office review attestations for expert deliveries."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .delivery_integrity import (
    OFFICE_REVIEW_PROOF_NAME,
    DeliveryIntegrityError,
    canonical_attempt_root,
    path_contains_symlink,
    sha256_file,
    validate_canonical_wps_evidence,
    workspace_relative_path,
)


TOKEN_TTL_NS = 15 * 60 * 1_000_000_000


def trusted_local_reviewer(profile: str = "") -> str:
    user = str(getpass.getuser() or "local-user").strip()
    profile_name = str(profile or "default").strip() or "default"
    return f"{user}@{profile_name}"


def open_document_with_os(path: Path) -> None:
    document = Path(path).expanduser().resolve()
    if not document.is_file():
        raise FileNotFoundError(document)
    if sys.platform == "darwin":
        subprocess.run(
            ["open", str(document)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=True,
        )
    elif os.name == "nt":  # pragma: no cover - Windows packaged runtime
        os.startfile(str(document))  # type: ignore[attr-defined]
    else:  # pragma: no cover - Linux packaged runtime
        subprocess.run(
            ["xdg-open", str(document)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=True,
        )


def issue_review_token(
    workspace: Path,
    *,
    binding: dict,
    document_path: Path,
    reviewer: str,
    open_document,
) -> tuple[str, dict, Path]:
    trusted = str(reviewer or "").strip()
    if not trusted:
        raise DeliveryIntegrityError("trusted local reviewer is unavailable")
    open_document(Path(document_path))
    opened_at_ns = time.time_ns()
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    evidence_dir = Path(workspace).expanduser().resolve() / ".taiji" / "wps-evidence" / token_hash
    evidence_dir.mkdir(parents=True, exist_ok=False)
    state = {
        "schema_version": 1,
        "token_hash": token_hash,
        "state": "issued",
        "run_id": str(binding.get("run_id") or ""),
        "session_id": str(binding.get("session_id") or ""),
        "stage_id": str(binding.get("stage_id") or ""),
        "attempt": int(binding.get("attempt") or 0),
        "document_sha256": str(binding.get("document_sha256") or ""),
        "reviewer": trusted,
        "opened_at_ns": opened_at_ns,
        "opened_at": _iso_now(),
        "expires_at_ns": opened_at_ns + TOKEN_TTL_NS,
        "evidence_dir": workspace_relative_path(workspace, evidence_dir),
    }
    state_path = _token_state_path(workspace, token_hash)
    _atomic_write_json(state_path, state)
    return token, state, state_path


def load_review_token(workspace: Path, token: str, *, binding: dict) -> tuple[dict, Path]:
    raw = str(token or "").strip()
    if not raw:
        raise DeliveryIntegrityError("office review token is required")
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    path = _token_state_path(workspace, token_hash)
    if path_contains_symlink(Path(workspace).expanduser().resolve(), path):
        raise DeliveryIntegrityError("office review token state contains a symlink")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryIntegrityError("office review token is invalid") from exc
    if not isinstance(state, dict) or state.get("token_hash") != token_hash:
        raise DeliveryIntegrityError("office review token is invalid")
    if state.get("state") != "issued":
        raise OfficeReviewTokenUsed("office review token was already used")
    if time.time_ns() > int(state.get("expires_at_ns") or 0):
        raise DeliveryIntegrityError("office review token expired")
    for key in ("run_id", "session_id", "stage_id", "attempt", "document_sha256"):
        if state.get(key) != binding.get(key):
            raise DeliveryIntegrityError("office review token does not match the current delivery")
    return state, path


def validate_token_evidence(workspace: Path, state: dict, evidence_files: list[Path]) -> None:
    root = Path(workspace).expanduser().resolve()
    evidence_relative = Path(str(state.get("evidence_dir") or ""))
    token_hash = str(state.get("token_hash") or "")
    expected_relative = Path(".taiji") / "wps-evidence" / token_hash
    if evidence_relative != expected_relative or evidence_relative.is_absolute() or ".." in evidence_relative.parts:
        raise DeliveryIntegrityError("office review evidence directory is not canonical")
    evidence_root = root / evidence_relative
    if path_contains_symlink(root, evidence_root) or not evidence_root.is_dir():
        raise DeliveryIntegrityError("office review evidence directory contains a symlink")
    opened_at_ns = int(state.get("opened_at_ns") or 0)
    if not evidence_files:
        raise DeliveryIntegrityError("office review evidence is required")
    for evidence in evidence_files:
        target = Path(evidence).expanduser()
        if not target.is_absolute():
            target = root / target
        try:
            target.absolute().relative_to(evidence_root.absolute())
        except ValueError as exc:
            raise DeliveryIntegrityError("office review evidence is outside its token directory") from exc
        if path_contains_symlink(root, target):
            raise DeliveryIntegrityError("office review evidence path contains a symlink")
        if not target.is_file() or target.stat().st_mtime_ns < opened_at_ns:
            raise DeliveryIntegrityError("office review evidence predates the document open request")


def prepare_consumed_review_state(
    state: dict,
    *,
    acceptance_manifest_path: str,
    acceptance_manifest_sha256: str,
    canonical_evidence: list[dict],
) -> dict:
    consumed = dict(state)
    consumed["state"] = "consumed"
    consumed["consumed_at"] = _iso_now()
    consumed["acceptance_manifest_path"] = str(acceptance_manifest_path or "")
    consumed["acceptance_manifest_sha256"] = str(acceptance_manifest_sha256 or "")
    consumed["canonical_evidence"] = [dict(item) for item in canonical_evidence]
    return consumed


def consume_review_token(path: Path, consumed_state: dict) -> None:
    if not isinstance(consumed_state, dict) or consumed_state.get("state") != "consumed":
        raise DeliveryIntegrityError("office review consumed state is invalid")
    _atomic_write_json(path, consumed_state)


def write_office_review_proof(workspace: Path, binding: dict, consumed_state: dict) -> Path:
    proof = _proof_payload(binding, consumed_state)
    path = (
        canonical_attempt_root(
            workspace,
            str(binding.get("run_id") or ""),
            str(binding.get("stage_id") or ""),
            int(binding.get("attempt") or 0),
        )
        / OFFICE_REVIEW_PROOF_NAME
    )
    _atomic_write_json(path, proof)
    return path


def validate_consumed_review_provenance(
    workspace: Path,
    *,
    binding: dict,
    sidecar: dict,
    delivery_dir: Path,
) -> Path:
    root = Path(workspace).expanduser().resolve()
    office = sidecar.get("office_review") if isinstance(sidecar.get("office_review"), dict) else {}
    token_hash = str(office.get("token_hash") or "").strip()
    if len(token_hash) != 64 or any(character not in "0123456789abcdef" for character in token_hash):
        raise DeliveryIntegrityError("office review token hash is invalid")
    state_path = _token_state_path(root, token_hash)
    proof_path = (
        canonical_attempt_root(
            root,
            str(binding.get("run_id") or ""),
            str(binding.get("stage_id") or ""),
            int(binding.get("attempt") or 0),
        )
        / OFFICE_REVIEW_PROOF_NAME
    )
    for path in (state_path, proof_path):
        if path_contains_symlink(root, path) or not path.is_file():
            raise DeliveryIntegrityError("office review consumed-state proof is missing or noncanonical")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryIntegrityError("office review consumed-state proof is unreadable") from exc
    expected_proof = _proof_payload(binding, state)
    if proof != expected_proof:
        raise DeliveryIntegrityError("office review proof does not match the consumed token state")
    expected_evidence_dir = (Path(".taiji") / "wps-evidence" / token_hash).as_posix()
    if (
        str(state.get("token_hash") or "") != token_hash
        or str(proof.get("token_hash") or "") != token_hash
        or state_path.stem != token_hash
        or office.get("attested_actual_office_review") is not True
        or str(office.get("opened_at") or "") != str(state.get("opened_at") or "")
        or str(office.get("evidence_dir") or "") != expected_evidence_dir
        or str(state.get("evidence_dir") or "") != expected_evidence_dir
        or str(sidecar.get("reviewer") or "") != str(state.get("reviewer") or "")
    ):
        raise DeliveryIntegrityError("office review sidecar does not match its server provenance")
    acceptance_path = root / str(state.get("acceptance_manifest_path") or "")
    if (
        acceptance_path.resolve()
        != (
            canonical_attempt_root(
                root,
                str(binding.get("run_id") or ""),
                str(binding.get("stage_id") or ""),
                int(binding.get("attempt") or 0),
            )
            / "expert-team-wps-acceptance.json"
        ).resolve()
        or path_contains_symlink(root, acceptance_path)
        or not acceptance_path.is_file()
        or sha256_file(acceptance_path) != str(state.get("acceptance_manifest_sha256") or "")
    ):
        raise DeliveryIntegrityError("office review acceptance manifest digest is stale")
    verified_evidence = validate_canonical_wps_evidence(
        root,
        delivery_dir,
        [item for item in sidecar.get("visual_evidence") or [] if isinstance(item, dict)],
    )
    if verified_evidence != state.get("canonical_evidence"):
        raise DeliveryIntegrityError("office review canonical evidence does not match its consumed proof")
    return proof_path


def _proof_payload(binding: dict, state: dict) -> dict:
    if not isinstance(state, dict) or state.get("state") != "consumed":
        raise DeliveryIntegrityError("office review token is not consumed")
    for key in ("run_id", "session_id", "stage_id", "attempt", "document_sha256"):
        if state.get(key) != binding.get(key):
            raise DeliveryIntegrityError("office review consumed state does not match the delivery binding")
    required_text = (
        "token_hash",
        "reviewer",
        "opened_at",
        "consumed_at",
        "evidence_dir",
        "acceptance_manifest_path",
        "acceptance_manifest_sha256",
    )
    if int(state.get("schema_version") or 0) != 1 or any(
        not str(state.get(key) or "").strip() for key in required_text
    ):
        raise DeliveryIntegrityError("office review consumed state is incomplete")
    return {
        "schema_version": 1,
        "token_hash": str(state["token_hash"]),
        "state": "consumed",
        "run_id": str(state["run_id"]),
        "session_id": str(state["session_id"]),
        "stage_id": str(state["stage_id"]),
        "attempt": int(state["attempt"]),
        "document_sha256": str(state["document_sha256"]),
        "reviewer": str(state["reviewer"]),
        "opened_at_ns": int(state.get("opened_at_ns") or 0),
        "opened_at": str(state["opened_at"]),
        "expires_at_ns": int(state.get("expires_at_ns") or 0),
        "consumed_at": str(state["consumed_at"]),
        "evidence_dir": str(state["evidence_dir"]),
        "acceptance_manifest_path": str(state["acceptance_manifest_path"]),
        "acceptance_manifest_sha256": str(state["acceptance_manifest_sha256"]),
        "canonical_evidence": [dict(item) for item in state.get("canonical_evidence") or []],
    }


class OfficeReviewTokenUsed(DeliveryIntegrityError):
    pass


def _token_state_path(workspace: Path, token_hash: str) -> Path:
    return (
        Path(workspace).expanduser().resolve()
        / ".taiji"
        / "expert-team-office-reviews"
        / f"{token_hash}.json"
    )


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
