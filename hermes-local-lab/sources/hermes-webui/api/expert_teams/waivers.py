"""Narrow, auditable waiver ledger for Office-only condition findings."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .delivery_integrity import canonical_attempt_root


class WaiverError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_office_waiver(
    workspace: Path,
    *,
    binding: dict,
    binding_sha256: str,
    acceptance: dict,
    acceptance_sha256: str,
    issue_id: str,
    authorizer: dict,
    idempotency_key: str,
    now: str,
    reason: str = "",
) -> dict:
    if binding.get("schema_version") != "expert-delivery-binding/v2":
        raise WaiverError("waiver_binding_invalid", "仅企业交付绑定支持条件豁免")
    if acceptance.get("schema_version") != "office-acceptance/v2":
        raise WaiverError("waiver_acceptance_invalid", "Office acceptance 合同无效")
    if acceptance.get("delivery_binding_sha256") != binding_sha256:
        raise WaiverError("waiver_binding_changed", "Office acceptance 不属于当前交付绑定")
    if not str(idempotency_key or "").strip():
        raise WaiverError("waiver_idempotency_required", "豁免幂等键缺失")
    roles = authorizer.get("roles") if isinstance(authorizer, dict) else []
    if "waiver-authorizer" not in (roles or []):
        raise WaiverError("trusted_authorizer_required", "需要可信豁免授权人")
    reviewer = acceptance.get("reviewer") if isinstance(acceptance.get("reviewer"), dict) else {}
    if str(authorizer.get("subject") or "") == str(reviewer.get("principal_id") or ""):
        raise WaiverError("authorizer_handoff_required", "复核人与豁免授权人必须职责分离")
    issues = [item for item in acceptance.get("issues") or [] if isinstance(item, dict)]
    matches = [item for item in issues if item.get("issue_id") == issue_id]
    if len(matches) != 1:
        raise WaiverError("waiver_issue_not_found", "Office condition issue 不存在或不唯一")
    issue = matches[0]
    severity = issue.get("severity")
    if severity != "condition":
        code = "waiver_severity_not_allowed" if severity in {"blocking", "warning", "info"} else "waiver_severity_invalid"
        raise WaiverError(code, "只有精确 condition 严重级别允许申请豁免")
    target_domain = str(issue.get("target_domain") or "office_issue")
    if target_domain != "office_issue":
        raise WaiverError("waiver_target_not_released", "阶段、语义和自动检查目标不开放豁免")
    target = {
        key: issue[key]
        for key in ("section_id", "block_id", "logical_asset_id", "page")
        if issue.get(key) not in (None, "")
    }

    identity = {
        "binding_sha256": str(binding_sha256),
        "acceptance_sha256": str(acceptance_sha256),
        "issue_id": str(issue_id),
        "target_domain": "office_issue",
        "target_id": str(issue_id),
        "target_sha256": str(acceptance_sha256),
        "authorizer_subject": str(authorizer.get("subject") or ""),
        "idempotency_key": str(idempotency_key),
        "reason": str(reason or "").strip(),
    }
    waiver = {
        "schema_version": "expert-waiver/v1",
        "waiver_id": "waiver-" + _digest(identity)[:20],
        "run_id": str(binding.get("run_id") or ""),
        "stage_id": str(binding.get("stage_id") or ""),
        "delivery_attempt": int(binding.get("delivery_attempt") or 0),
        "delivery_binding_sha256": str(binding_sha256),
        "review_id": str(acceptance.get("review_id") or ""),
        "acceptance_sha256": str(acceptance_sha256),
        "issue_id": str(issue_id),
        "target_domain": "office_issue",
        "target_id": str(issue_id),
        "target_sha256": str(acceptance_sha256),
        "target": deepcopy(target),
        "authorizer": {
            "subject": str(authorizer.get("subject") or ""),
            "display_name": str(authorizer.get("display_name") or ""),
            "role": "waiver-authorizer",
            "auth_method": str(authorizer.get("auth_method") or ""),
            "identity_snapshot_sha256": str(authorizer.get("identity_snapshot_sha256") or ""),
        },
        "authorized_at": str(now),
        "reason": str(reason or "").strip(),
        "idempotency_key_sha256": hashlib.sha256(str(idempotency_key).encode("utf-8")).hexdigest(),
    }
    root = canonical_attempt_root(
        workspace,
        waiver["run_id"],
        waiver["stage_id"],
        waiver["delivery_attempt"],
    )
    ledger_path = root / "expert-team-waiver-ledger.json"
    if ledger_path.is_file():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    else:
        ledger = {
            "schema_version": "expert-waiver-ledger/v1",
            "delivery_binding_sha256": str(binding_sha256),
            "office_acceptance_sha256": str(acceptance_sha256),
            "waivers": [],
        }
    existing = next(
        (item for item in ledger.get("waivers") or [] if item.get("waiver_id") == waiver["waiver_id"]),
        None,
    )
    if existing is not None:
        if existing != waiver:
            raise WaiverError("waiver_idempotency_conflict", "豁免幂等身份发生冲突")
        return deepcopy(existing)
    ledger["waivers"] = [*ledger.get("waivers", []), waiver]
    ledger["ledger_sha256"] = _digest({key: value for key, value in ledger.items() if key != "ledger_sha256"})
    _write_json(ledger_path, ledger)
    return deepcopy(waiver)


def create_current_office_waiver(workspace: Path, body: dict, *, authorizer: dict, now: str) -> tuple[dict, dict]:
    """Resolve the current immutable delivery/acceptance; clients submit only a target ref."""

    from .delivery_integrity import sha256_file
    from .office_review import OFFICE_ACCEPTANCE_NAME
    from .storage import read_run, run_file_lock, write_run

    run_id = str(body.get("run_id") or "").strip()
    session_id = str(body.get("session_id") or "").strip()
    with run_file_lock(workspace, run_id):
        run = read_run(workspace, run_id)
        if session_id != str(run.get("session_id") or ""):
            raise WaiverError("waiver_run_identity_mismatch", "session does not own this run")
        if int(body.get("expected_version") or -1) != int(run.get("version") or 0):
            raise WaiverError("version_conflict", "expert team run version changed")
        if str(body.get("target_domain") or "office_issue") != "office_issue":
            raise WaiverError("waiver_target_not_released", "only Office issue targets are released")
        ref = run.get("current_delivery_manifest_ref") if isinstance(run.get("current_delivery_manifest_ref"), dict) else {}
        attempt = int(ref.get("delivery_attempt") or 0)
        expected_root = canonical_attempt_root(workspace, run_id, "delivery", attempt)
        binding_path = Path(workspace).expanduser().resolve() / str(ref.get("delivery_binding_path") or "")
        if binding_path.resolve() != (expected_root / "expert-team-delivery.json").resolve() or not binding_path.is_file():
            raise WaiverError("waiver_binding_invalid", "current enterprise delivery binding is unavailable")
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding_sha256 = sha256_file(binding_path)
        if binding_sha256 != str(ref.get("delivery_binding_sha256") or ""):
            raise WaiverError("waiver_binding_changed", "current delivery binding changed")
        acceptance_path = expected_root / OFFICE_ACCEPTANCE_NAME
        if not acceptance_path.is_file():
            raise WaiverError("waiver_acceptance_invalid", "current Office acceptance is unavailable")
        acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
        waiver = create_office_waiver(
            workspace,
            binding=binding,
            binding_sha256=binding_sha256,
            acceptance=acceptance,
            acceptance_sha256=sha256_file(acceptance_path),
            issue_id=str(body.get("target_id") or body.get("issue_id") or ""),
            authorizer=authorizer,
            idempotency_key=str(body.get("idempotency_key") or ""),
            reason=str(body.get("reason") or ""),
            now=now,
        )
        rows = [deepcopy(item) for item in run.get("waiver_refs") or [] if isinstance(item, dict)]
        if not any(item.get("waiver_id") == waiver["waiver_id"] for item in rows):
            rows.append({
                "waiver_id": waiver["waiver_id"],
                "target_id": waiver["issue_id"],
                "delivery_binding_sha256": waiver["delivery_binding_sha256"],
                "acceptance_sha256": waiver["acceptance_sha256"],
            })
            run["waiver_refs"] = rows
            run["version"] = int(run.get("version") or 0) + 1
            run = write_run(workspace, run)
        return waiver, run
