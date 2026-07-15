"""Server-issued, one-time Office review attestations for expert deliveries."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
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
OFFICE_ACCEPTANCE_STATUSES = {"pending", "passed", "passed_with_conditions", "failed"}
OFFICE_ISSUE_SEVERITIES = {"condition", "blocking"}
OFFICE_POLICY_V1 = {
    "schema_version": "office-policy/v1",
    "checklist": {
        "document_opened": "required",
        "title_and_cover_match": "required",
        "genre_and_structure_match": "required",
        "content_order_correct": "required",
        "figures_unique_and_readable": "optional",
        "tables_readable": "optional",
        "headers_footers_pagination": "required",
        "no_placeholders_or_workflow_text": "required",
        "citations_readable": "optional",
    },
    "categories": {
        "file_or_hash_mismatch": "blocking",
        "required_check_failed": "blocking",
        "title_or_genre_mismatch": "blocking",
        "placeholder_content": "blocking",
        "security_issue": "blocking",
        "upstream_gate_issue": "blocking",
        "duplicate_figure": "blocking",
        "visual_alignment": "condition",
        "minor_typography": "condition",
        "pagination_preference": "condition",
    },
}
OFFICE_ACCEPTANCE_NAME = "expert-team-wps-acceptance.json"
WAIVER_LEDGER_NAME = "expert-team-waiver-ledger.json"
COMPLETION_TRANSACTION_NAME = "expert-team-completion-transaction.json"


def trusted_local_reviewer(profile: str = "") -> str:
    user = str(getpass.getuser() or "local-user").strip()
    profile_name = str(profile or "default").strip() or "default"
    return f"{user}@{profile_name}"


def build_office_acceptance(
    *,
    binding: dict,
    token_state: dict,
    status: str,
    checklist: dict,
    issues: list[dict],
    evidence: list[dict],
    note: str,
    now: str,
    idempotency_key: str = "",
    request_fingerprint: str = "",
) -> dict:
    if binding.get("schema_version") != "expert-office-binding/v1":
        raise DeliveryIntegrityError("enterprise Office binding is required")
    if status not in OFFICE_ACCEPTANCE_STATUSES:
        raise DeliveryIntegrityError("Office acceptance status is invalid")
    checklist_policy = OFFICE_POLICY_V1["checklist"]
    if not isinstance(checklist, dict) or set(checklist) != set(checklist_policy):
        raise DeliveryIntegrityError("Office acceptance checklist must be complete")
    allowed_check_values = {"not_checked", "passed", "failed", "not_applicable"}
    if any(value not in allowed_check_values for value in checklist.values()):
        raise DeliveryIntegrityError("Office acceptance checklist status is invalid")
    if status in {"passed", "passed_with_conditions"} and any(
        checklist.get(key) != "passed" for key, disposition in checklist_policy.items() if disposition == "required"
    ):
        raise DeliveryIntegrityError("Office acceptance required checklist must pass")
    if any(
        checklist.get(key) == "not_applicable" and disposition == "required"
        for key, disposition in checklist_policy.items()
    ):
        raise DeliveryIntegrityError("required Office checklist cannot be not_applicable")
    normalized_issues = []
    for issue in issues if isinstance(issues, list) else []:
        if not isinstance(issue, dict):
            raise DeliveryIntegrityError("Office acceptance issue is invalid")
        if not isinstance(issue.get("severity"), str) or issue["severity"] not in OFFICE_ISSUE_SEVERITIES:
            raise DeliveryIntegrityError("Office acceptance issue severity is invalid")
        legacy_fields = {"issue_id", "severity", "target", "message"}
        required_fields = {"issue_id", "severity", "category", "description", "expected_fix"}
        optional_fields = {"section_id", "block_id", "logical_asset_id", "page"}
        if set(issue) == legacy_fields:
            target = issue.get("target") if isinstance(issue.get("target"), dict) else {}
            if target.get("domain") != "office":
                raise DeliveryIntegrityError("Office acceptance issue target is invalid")
            raise DeliveryIntegrityError("Office acceptance issue policy category is required")
        if not required_fields <= set(issue) or set(issue) - required_fields - optional_fields:
            raise DeliveryIntegrityError("Office acceptance issue is invalid")
        if any(not isinstance(issue.get(field), str) or not issue[field].strip() for field in required_fields):
            raise DeliveryIntegrityError("Office acceptance issue fields must be non-empty strings")
        if "page" in issue and (not isinstance(issue.get("page"), int) or isinstance(issue.get("page"), bool) or issue["page"] < 1):
            raise DeliveryIntegrityError("Office acceptance issue page is invalid")
        category = str(issue.get("category") or "")
        policy_severity = OFFICE_POLICY_V1["categories"].get(category)
        if not policy_severity or issue.get("severity") != policy_severity:
            raise DeliveryIntegrityError("Office acceptance issue severity must match policy")
        normalized_issues.append(dict(issue))
    has_blocking = any(item["severity"] == "blocking" for item in normalized_issues)
    if status == "pending" and normalized_issues:
        raise DeliveryIntegrityError("pending Office acceptance cannot contain issues")
    if status == "passed" and normalized_issues:
        raise DeliveryIntegrityError("passed Office acceptance cannot contain issues")
    if status == "passed_with_conditions" and (not normalized_issues or has_blocking):
        raise DeliveryIntegrityError("passed_with_conditions requires condition-only issues")
    if status == "failed" and not normalized_issues:
        raise DeliveryIntegrityError("failed Office acceptance requires structured issues")
    if not isinstance(evidence, list) or not evidence:
        raise DeliveryIntegrityError("Office acceptance evidence is required")
    normalized_evidence = []
    for item in evidence:
        if not isinstance(item, dict) or not str(item.get("path") or "") or not str(item.get("sha256") or ""):
            raise DeliveryIntegrityError("Office acceptance evidence is invalid")
        normalized_evidence.append({
            "path": str(item["path"]),
            "sha256": str(item["sha256"]),
            "size_bytes": int(item.get("size_bytes", item.get("sizeBytes", 0)) or 0),
            "media_type": str(item.get("media_type", item.get("mediaType", "")) or ""),
        })
    reviewer = token_state.get("reviewer_identity") if isinstance(token_state.get("reviewer_identity"), dict) else {}
    if reviewer.get("role") != "document-reviewer":
        raise DeliveryIntegrityError("trusted document reviewer snapshot is missing")
    identity = {
        "delivery_binding_sha256": binding.get("delivery_binding_sha256"),
        "token_hash": token_state.get("token_hash"),
        "reviewer_subject": reviewer.get("subject"),
        "reviewed_at": str(now),
        **({"request": {"idempotency_key": str(idempotency_key), "fingerprint": str(request_fingerprint)}} if idempotency_key and request_fingerprint else {}),
    }
    return {
        "schema_version": "office-acceptance/v2",
        "policy_version": OFFICE_POLICY_V1["schema_version"],
        "review_id": "review-" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20],
        "delivery_binding_sha256": str(binding.get("delivery_binding_sha256") or ""),
        "document_id": str((binding.get("canonical_artifact") or {}).get("artifact_id") or ""),
        "document_revision": int(binding.get("document_revision") or 1),
        "canonical_sha256": str((binding.get("canonical_artifact") or {}).get("sha256") or ""),
        "document_sha256": str(binding.get("document_sha256") or ""),
        "template": dict(binding.get("template") or {}),
        "renderer": dict(binding.get("renderer") or {}),
        "decision": status,
        "validity": "active",
        "checklist": dict(checklist),
        "issues": normalized_issues,
        "evidence": normalized_evidence,
        "token_provenance": {
            "token_hash": str(token_state.get("token_hash") or ""),
            "opened_at": str(token_state.get("opened_at") or ""),
            "delivery_binding_sha256": str(binding.get("delivery_binding_sha256") or ""),
        },
        "reviewer": {
            "principal_id": str(reviewer.get("subject") or ""),
            "role": "document-reviewer",
            "auth_source": str(reviewer.get("auth_method") or ""),
            "identity_snapshot_sha256": str(reviewer.get("identity_snapshot_sha256") or ""),
        },
        "note": str(note or "").strip(),
        "opened_at": str(token_state.get("opened_at") or ""),
        "reviewed_at": str(now),
        **({
            "request": {
                "idempotency_key": str(idempotency_key),
                "fingerprint": str(request_fingerprint),
            }
        } if idempotency_key and request_fingerprint else {}),
    }


def office_acceptance_view(acceptance: dict, *, waiver_refs: list[dict] | None = None) -> dict:
    """Project an acceptance into the non-secret, policy-preserving UI contract."""
    if not isinstance(acceptance, dict) or acceptance.get("schema_version") != "office-acceptance/v2":
        raise DeliveryIntegrityError("Office acceptance v2 is required")
    issues = []
    for item in acceptance.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append({
            "issue_id": str(item.get("issue_id") or ""),
            "severity": str(item.get("severity") or ""),
            "target_domain": str(item.get("target_domain") or "office_issue"),
            "category": str(item.get("category") or ""),
            **{key: item[key] for key in ("section_id", "block_id", "logical_asset_id", "page") if item.get(key) not in (None, "")},
            "description": str(item.get("description") or ""),
            "expected_fix": str(item.get("expected_fix") or ""),
        })
    reviewer = acceptance.get("reviewer") if isinstance(acceptance.get("reviewer"), dict) else {}
    waived = sorted({
        str(item.get("target_id") or "")
        for item in waiver_refs or []
        if isinstance(item, dict) and str(item.get("target_id") or "")
    })
    return {
        "schema_version": "office-review-view/v1",
        "review_id": str(acceptance.get("review_id") or ""),
        "document_revision": int(acceptance.get("document_revision") or 1),
        "document_sha256": str(acceptance.get("document_sha256") or ""),
        "canonical_sha256": str(acceptance.get("canonical_sha256") or ""),
        "status": str(acceptance.get("decision") or "pending"),
        "decision": str(acceptance.get("decision") or "pending"),
        "validity": str(acceptance.get("validity") or "active"),
        "checklist": deepcopy(acceptance.get("checklist") or {}),
        "issues": issues,
        "issue_count": len(issues),
        "reviewer_label": str(reviewer.get("display_name") or reviewer.get("principal_id") or ""),
        "waived_issue_ids": waived,
    }


def pending_office_review_view(binding: dict, *, review_session_status: str = "begin_required") -> dict:
    """Expose a safe first-review state without inventing an acceptance artifact."""
    if not isinstance(binding, dict) or binding.get("schema_version") != "expert-office-binding/v1":
        raise DeliveryIntegrityError("enterprise Office binding is required")
    return {
        "review_id": "pending-" + str(binding.get("delivery_binding_sha256") or "")[:20],
        "document_revision": int(binding.get("document_revision") or binding.get("attempt") or 1),
        "document_sha256": str(binding.get("document_sha256") or ""),
        "canonical_sha256": str((binding.get("canonical_artifact") or {}).get("sha256") or ""),
        "status": "pending", "decision": "pending", "validity": "active",
        "review_session_status": "ready" if review_session_status == "ready" else "begin_required",
        "checklist": {key: "not_checked" for key in OFFICE_POLICY_V1["checklist"]},
        "issues": [], "issue_count": 0, "reviewer_label": "", "waived_issue_ids": [],
    }


def write_office_acceptance(workspace: Path, binding: dict, acceptance: dict) -> tuple[Path, dict]:
    path = canonical_attempt_root(
        workspace,
        str(binding.get("run_id") or ""),
        str(binding.get("stage_id") or ""),
        int(binding.get("attempt") or 0),
    ) / OFFICE_ACCEPTANCE_NAME
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != acceptance:
            raise DeliveryIntegrityError("Office acceptance is immutable")
        return path, existing
    _atomic_write_json(path, acceptance)
    return path, dict(acceptance)


def build_office_revision_request(
    *,
    acceptance: dict,
    issue_ids: list[str],
    acceptance_sha256: str,
    delivery_binding_sha256: str,
    idempotency_key: str,
    now: str,
) -> dict:
    if acceptance.get("schema_version") != "office-acceptance/v2":
        raise DeliveryIntegrityError("Office acceptance v2 is required")
    requested = [str(item or "").strip() for item in issue_ids if str(item or "").strip()]
    if not requested or len(requested) != len(set(requested)):
        raise DeliveryIntegrityError("Office revision issue_ids are required and unique")
    issues = {str(item.get("issue_id") or ""): item for item in acceptance.get("issues") or [] if isinstance(item, dict)}
    items = []
    for issue_id in requested:
        issue = issues.get(issue_id)
        if not isinstance(issue, dict):
            raise DeliveryIntegrityError("Office revision issue does not exist")
        item = {
            "issue_id": issue_id,
            "category": str(issue.get("category") or ""),
            **{
                key: issue[key]
                for key in ("section_id", "block_id", "logical_asset_id", "page")
                if issue.get(key) not in (None, "")
            },
            "expected_fix": str(issue.get("expected_fix") or ""),
        }
        if not item["category"] or not item["expected_fix"]:
            raise DeliveryIntegrityError("Office revision issue policy fields are incomplete")
        items.append(item)
    identity = {
        "acceptance_sha256": str(acceptance_sha256),
        "delivery_binding_sha256": str(delivery_binding_sha256),
        "issue_ids": requested,
        "idempotency_key": str(idempotency_key or ""),
    }
    request_fingerprint = hashlib.sha256(json.dumps({
        "acceptance_sha256": str(acceptance_sha256), "delivery_binding_sha256": str(delivery_binding_sha256),
        "issue_ids": requested,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "schema_version": "office-revision-request/v1",
        "request_id": "revision-" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20],
        "delivery_binding_sha256": str(delivery_binding_sha256),
        "office_acceptance_sha256": str(acceptance_sha256),
        "items": items,
        "idempotency_key": str(idempotency_key),
        "request_fingerprint": request_fingerprint,
        "created_at": str(now),
    }


def create_current_office_revision_request(workspace: Path, body: dict, *, now: str) -> tuple[dict, dict]:
    from .storage import read_run, run_file_lock, write_run

    forbidden = {"feedback", "note", "message", "expected_fix", "items", "issues"}
    if forbidden & set(body):
        raise DeliveryIntegrityError("Office free text and policy fields are server-derived")
    run_id = str(body.get("run_id") or "").strip()
    with run_file_lock(workspace, run_id):
        run = read_run(workspace, run_id)
        if str(body.get("session_id") or "") != str(run.get("session_id") or ""):
            raise DeliveryIntegrityError("Office revision run identity mismatch")
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9:._-]{8,240}", idempotency_key):
            raise DeliveryIntegrityError("Office revision idempotency_key is invalid")
        for ref_item in run.get("office_revision_request_refs") or []:
            if not isinstance(ref_item, dict) or not str(ref_item.get("path") or ""):
                continue
            request_path = Path(workspace).expanduser().resolve() / str(ref_item["path"])
            if not request_path.is_file():
                continue
            existing = _read_json_object(request_path, label="Office revision request")
            if str(existing.get("idempotency_key") or "") != idempotency_key:
                continue
            requested_ids = [str(item or "").strip() for item in body.get("issue_ids") or []]
            existing_ids = [str(item.get("issue_id") or "") for item in existing.get("items") or [] if isinstance(item, dict)]
            if requested_ids != existing_ids:
                raise DeliveryIntegrityError("Office revision idempotency conflict")
            return existing, run
        if int(body.get("expected_version") or -1) != int(run.get("version") or 0):
            raise DeliveryIntegrityError("Office revision version conflict")
        if str(run.get("workflow_state") or "") != "awaiting_review":
            raise DeliveryIntegrityError("Office revision is only available while awaiting review")
        ref = run.get("current_delivery_manifest_ref") if isinstance(run.get("current_delivery_manifest_ref"), dict) else {}
        attempt = int(ref.get("delivery_attempt") or 0)
        root = canonical_attempt_root(workspace, run_id, "delivery", attempt)
        binding_path = Path(workspace).expanduser().resolve() / str(ref.get("delivery_binding_path") or "")
        acceptance_path = root / OFFICE_ACCEPTANCE_NAME
        if not binding_path.is_file() or not acceptance_path.is_file():
            raise DeliveryIntegrityError("current Office revision evidence is unavailable")
        binding_sha256 = sha256_file(binding_path)
        acceptance_sha256 = sha256_file(acceptance_path)
        if binding_sha256 != str(ref.get("delivery_binding_sha256") or ""):
            raise DeliveryIntegrityError("current delivery binding changed")
        acceptance = _read_json_object(acceptance_path, label="Office acceptance")
        request = build_office_revision_request(
            acceptance=acceptance,
            issue_ids=body.get("issue_ids") if isinstance(body.get("issue_ids"), list) else [],
            acceptance_sha256=acceptance_sha256,
            delivery_binding_sha256=binding_sha256,
            idempotency_key=str(body.get("idempotency_key") or ""),
            now=now,
        )
        request_path = root / f"expert-team-office-revision-{request['request_id']}.json"
        if request_path.is_file():
            if _read_json_object(request_path, label="Office revision request") != request:
                raise DeliveryIntegrityError("Office revision request is immutable")
        else:
            _atomic_write_json(request_path, request)
        refs = [deepcopy(item) for item in run.get("office_revision_request_refs") or [] if isinstance(item, dict)]
        if not any(item.get("request_id") == request["request_id"] for item in refs):
            refs.append({
                "request_id": request["request_id"],
                "path": workspace_relative_path(workspace, request_path),
                "sha256": sha256_file(request_path),
                "delivery_attempt": attempt,
            })
        run["office_revision_request_refs"] = refs
        current = run.get("current_delivery_attempt_reservation")
        if isinstance(current, dict):
            invalidated = {**deepcopy(current), "status": "invalidated", "updated_at": now}
            run["current_delivery_attempt_reservation"] = invalidated
            reservations = [deepcopy(item) for item in run.get("delivery_attempt_reservations") or [] if isinstance(item, dict)]
            for index, item in enumerate(reservations):
                if item.get("reservation_id") == invalidated.get("reservation_id"):
                    reservations[index] = deepcopy(invalidated)
            run["delivery_attempt_reservations"] = reservations
        run["current_delivery_manifest_ref"] = None
        run["current_stage_artifact_ref"] = None
        run["pending_system_stage_result"] = None
        run["workflow_state"] = "delivery_validation_required"
        run["version"] = int(run.get("version") or 0) + 1
        run["updated_at"] = now
        return request, write_run(workspace, run)


class CompletionCrashInjected(RuntimeError):
    """Test-only deterministic fault boundary for the recoverable commit protocol."""


def _completion_paths(workspace: Path, binding: dict) -> dict[str, Path]:
    root = canonical_attempt_root(
        workspace,
        str(binding.get("run_id") or ""),
        str(binding.get("stage_id") or ""),
        int(binding.get("delivery_attempt") or binding.get("attempt") or 0),
    )
    return {
        "root": root,
        "binding": root / "expert-team-delivery.json",
        "acceptance": root / OFFICE_ACCEPTANCE_NAME,
        "waiver_ledger": root / WAIVER_LEDGER_NAME,
        "transaction": root / COMPLETION_TRANSACTION_NAME,
        "proof": root / OFFICE_REVIEW_PROOF_NAME,
    }


def _read_json_object(path: Path, *, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryIntegrityError(f"{label} is missing or unreadable") from exc
    if not isinstance(value, dict):
        raise DeliveryIntegrityError(f"{label} must be an object")
    return value


def _empty_waiver_ledger(binding_sha256: str, acceptance_sha256: str) -> dict:
    return {
        "schema_version": "expert-waiver-ledger/v1",
        "delivery_binding_sha256": binding_sha256,
        "office_acceptance_sha256": acceptance_sha256,
        "waivers": [],
    }


def _maybe_crash(fault_after: str | None, boundary: str) -> None:
    if fault_after == boundary:
        raise CompletionCrashInjected(boundary)


def _valid_waiver_refs(acceptance: dict, ledger: dict) -> bool:
    conditions = {
        str(item.get("issue_id") or "")
        for item in acceptance.get("issues") or []
        if isinstance(item, dict) and item.get("severity") == "condition"
    }
    covered = {
        str(item.get("target_id") or item.get("issue_id") or "")
        for item in ledger.get("waivers") or []
        if isinstance(item, dict)
        and item.get("schema_version") in {"expert-waiver/v1", "office-waiver/v1"}
        and item.get("delivery_binding_sha256") == acceptance.get("delivery_binding_sha256")
    }
    return conditions <= covered


def reconcile_enterprise_completion(
    workspace: Path,
    *,
    run: dict,
    binding: dict,
    binding_sha256: str,
    now: str,
    fault_after: str | None = None,
) -> dict:
    """Idempotently converge prepared Office evidence into one committed completion."""

    from .storage import write_run

    if binding.get("schema_version") != "expert-delivery-binding/v2":
        raise DeliveryIntegrityError("enterprise delivery binding is required")
    paths = _completion_paths(workspace, binding)
    acceptance = _read_json_object(paths["acceptance"], label="Office acceptance")
    acceptance_sha256 = sha256_file(paths["acceptance"])
    if (
        acceptance.get("schema_version") != "office-acceptance/v2"
        or acceptance.get("validity") != "active"
        or acceptance.get("delivery_binding_sha256") != binding_sha256
    ):
        raise DeliveryIntegrityError("Office acceptance does not match the current delivery binding")
    decision = str(acceptance.get("decision") or "pending")
    if decision not in {"passed", "passed_with_conditions"}:
        raise DeliveryIntegrityError("Office acceptance is not eligible for completion")
    if any(
        isinstance(item, dict) and item.get("severity") == "blocking"
        for item in acceptance.get("issues") or []
    ):
        raise DeliveryIntegrityError("blocking Office issues prevent completion")
    _maybe_crash(fault_after, "acceptance")

    if paths["waiver_ledger"].is_file():
        ledger = _read_json_object(paths["waiver_ledger"], label="waiver ledger")
    else:
        ledger = _empty_waiver_ledger(binding_sha256, acceptance_sha256)
        _atomic_write_json(paths["waiver_ledger"], ledger)
    if (
        ledger.get("delivery_binding_sha256") != binding_sha256
        or ledger.get("office_acceptance_sha256") != acceptance_sha256
        or not _valid_waiver_refs(acceptance, ledger)
    ):
        raise DeliveryIntegrityError("Office conditions do not have a closed waiver ledger")
    waiver_sha256 = sha256_file(paths["waiver_ledger"])
    _maybe_crash(fault_after, "waiver_ledger")

    identity = {
        "run_id": str(binding.get("run_id") or ""),
        "binding": binding_sha256,
        "acceptance": acceptance_sha256,
        "waivers": waiver_sha256,
    }
    transaction_id = "completion-" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    if paths["transaction"].is_file():
        transaction = _read_json_object(paths["transaction"], label="completion transaction")
        if transaction.get("transaction_id") != transaction_id:
            raise DeliveryIntegrityError("completion transaction identity changed")
    else:
        transaction = {
            "schema_version": "expert-completion-transaction/v1",
            "transaction_id": transaction_id,
            "state": "prepared",
            "run_id": str(binding.get("run_id") or ""),
            "expected_run_version": int(run.get("version") or 0),
            "delivery_binding_sha256": binding_sha256,
            "office_acceptance_sha256": acceptance_sha256,
            "waiver_ledger_sha256": waiver_sha256,
            "completion_proof_sha256": None,
            "prepared_at": str(now),
            "committed_at": None,
        }
        _atomic_write_json(paths["transaction"], transaction)
    completion_time = str(transaction.get("prepared_at") or now)

    token_hash = str((acceptance.get("token_provenance") or {}).get("token_hash") or "")
    if token_hash:
        token_path = _token_state_path(workspace, token_hash)
        if token_path.is_file():
            token_state = _read_json_object(token_path, label="Office token state")
            if token_state.get("state") != "consumed":
                token_state["state"] = "consumed"
                token_state["consumed_at"] = completion_time
                _atomic_write_json(token_path, token_state)
    _maybe_crash(fault_after, "token_consumed")

    proof = {
        "schema_version": "expert-completion-proof/v1",
        "session_id": str(binding.get("session_id") or ""),
        "run_id": str(binding.get("run_id") or ""),
        "stage_id": str(binding.get("stage_id") or ""),
        "delivery_attempt": int(binding.get("delivery_attempt") or 0),
        "delivery_binding_sha256": binding_sha256,
        "office_acceptance_sha256": acceptance_sha256,
        "waiver_ledger_sha256": waiver_sha256,
        "completion_transaction_id": transaction_id,
        "gate_statuses": {"content": "passed", "document": "passed", "office": "passed"},
        "reviewer": deepcopy(acceptance.get("reviewer") or {}),
        "completed_at": completion_time,
    }
    if paths["proof"].is_file():
        if _read_json_object(paths["proof"], label="completion proof") != proof:
            raise DeliveryIntegrityError("completion proof is immutable")
    else:
        _atomic_write_json(paths["proof"], proof)
    proof_sha256 = sha256_file(paths["proof"])
    if transaction.get("completion_proof_sha256") not in {None, proof_sha256}:
        raise DeliveryIntegrityError("completion proof digest changed")
    transaction["completion_proof_sha256"] = proof_sha256
    _atomic_write_json(paths["transaction"], transaction)
    _maybe_crash(fault_after, "proof")

    completed = deepcopy(run)
    completed["workflow_state"] = "completed"
    completed["version"] = max(int(run.get("version") or 0) + 1, int(completed.get("version") or 0))
    completed["updated_at"] = completion_time
    completed["completion_transaction_ref"] = {
        "transaction_id": transaction_id,
        "delivery_attempt": int(binding.get("delivery_attempt") or 0),
    }
    completed["completion_integrity"] = {
        "status": "reconciling",
        "checked_at": completion_time,
        "message": "Office completion transaction is being committed.",
    }
    write_run(workspace, completed)
    _maybe_crash(fault_after, "run_completed")

    transaction["state"] = "committed"
    transaction["committed_at"] = completion_time
    _atomic_write_json(paths["transaction"], transaction)
    completed["completion_integrity"] = enterprise_completion_status(workspace, completed)
    return write_run(workspace, completed)


def enterprise_completion_status(workspace: Path, run: dict) -> dict:
    checked_at = str(run.get("updated_at") or "")
    ref = run.get("current_delivery_manifest_ref") if isinstance(run.get("current_delivery_manifest_ref"), dict) else {}
    attempt = int(ref.get("delivery_attempt") or 0)
    binding_path = Path(workspace).expanduser().resolve() / str(ref.get("delivery_binding_path") or "")
    if not str(ref.get("delivery_binding_path") or ""):
        binding_path = canonical_attempt_root(workspace, str(run.get("run_id") or ""), "delivery", attempt) / "expert-team-delivery.json"
    pending = {
        "status": "reconciling",
        "checked_at": checked_at,
        "message": "Office completion evidence is incomplete or awaiting reconciliation.",
        "transaction_state": "missing",
        "summary_closed": False,
    }
    try:
        binding = _read_json_object(binding_path, label="delivery binding")
        paths = _completion_paths(workspace, binding)
        acceptance = _read_json_object(paths["acceptance"], label="Office acceptance")
        ledger = _read_json_object(paths["waiver_ledger"], label="waiver ledger")
        proof = _read_json_object(paths["proof"], label="completion proof")
        transaction = _read_json_object(paths["transaction"], label="completion transaction")
        expected = {
            "delivery_binding_sha256": sha256_file(binding_path),
            "office_acceptance_sha256": sha256_file(paths["acceptance"]),
            "waiver_ledger_sha256": sha256_file(paths["waiver_ledger"]),
            "completion_proof_sha256": sha256_file(paths["proof"]),
        }
        closed = all(transaction.get(key) == value for key, value in expected.items()) and all(
            proof.get(key) == expected[key]
            for key in ("delivery_binding_sha256", "office_acceptance_sha256", "waiver_ledger_sha256")
        ) and proof.get("completion_transaction_id") == transaction.get("transaction_id")
        authoritative = (
            str(run.get("workflow_state") or "") == "completed"
            and int(ref.get("delivery_attempt") or 0) == int(binding.get("delivery_attempt") or 0)
        )
        if transaction.get("state") == "committed" and closed and authoritative:
            return {
                "status": "passed",
                "checked_at": checked_at,
                "message": "Enterprise Office completion evidence is committed and hash-closed.",
                "transaction_state": "committed",
                "summary_closed": True,
            }
        pending["transaction_state"] = str(transaction.get("state") or "missing")
    except (DeliveryIntegrityError, OSError, TypeError, ValueError):
        pass
    return pending


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
    trusted_principal: dict | None = None,
) -> tuple[str, dict, Path]:
    principal = dict(trusted_principal) if isinstance(trusted_principal, dict) else {}
    trusted = str(principal.get("display_name") or reviewer or "").strip()
    if not trusted:
        raise DeliveryIntegrityError("trusted local reviewer is unavailable")
    if binding.get("schema_version") == "expert-office-binding/v1":
        if "document-reviewer" not in (principal.get("roles") or []):
            raise DeliveryIntegrityError("trusted document reviewer is required")
        required_identity = ("subject", "identity_snapshot_sha256", "auth_method")
        if any(not str(principal.get(field) or "").strip() for field in required_identity):
            raise DeliveryIntegrityError("trusted document reviewer snapshot is incomplete")
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
    if binding.get("schema_version") == "expert-office-binding/v1":
        state.update(
            {
                "schema_version": 2,
                "delivery_binding_sha256": str(binding.get("delivery_binding_sha256") or ""),
                "brief": dict(binding.get("brief") or {}),
                "canonical_artifact": dict(binding.get("canonical_artifact") or {}),
                "template": dict(binding.get("template") or {}),
                "renderer": dict(binding.get("renderer") or {}),
                "reviewer_identity": {
                    "subject": str(principal.get("subject") or ""),
                    "display_name": trusted,
                    "role": "document-reviewer",
                    "auth_method": str(principal.get("auth_method") or ""),
                    "identity_snapshot_sha256": str(principal.get("identity_snapshot_sha256") or ""),
                },
            }
        )
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
    state_version = int(state.get("schema_version") or 0)
    if state_version not in {1, 2} or any(
        not str(state.get(key) or "").strip() for key in required_text
    ):
        raise DeliveryIntegrityError("office review consumed state is incomplete")
    proof = {
        "schema_version": state_version,
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
    if state_version == 2:
        proof["delivery_binding_sha256"] = str(state.get("delivery_binding_sha256") or "")
        proof["reviewer_identity"] = dict(state.get("reviewer_identity") or {})
        if not proof["delivery_binding_sha256"] or not proof["reviewer_identity"]:
            raise DeliveryIntegrityError("enterprise Office proof identity is incomplete")
    return proof


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
