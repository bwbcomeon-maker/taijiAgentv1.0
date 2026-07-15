"""Adversarial contracts for authoritative expert-team delivery completion."""

from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

import pytest

from tests.test_expert_team_delivery_validation_gate import (
    _approval_body,
    _awaiting_final_review,
    _complete_wps_check,
    _report,
    _validator_result,
    _write_bound_wps_sidecar,
)
from tests.test_expert_team_delivery_contract import FINAL_MARKDOWN, _generate, _ready_at_stage


@pytest.mark.parametrize("corruption", ["missing", "mismatched"])
def test_final_approval_fails_closed_when_persisted_current_stage_is_corrupt(tmp_path, corruption):
    from api import expert_teams
    from api.expert_teams.storage import read_run, write_run

    reviewed = _awaiting_final_review(
        expert_teams,
        tmp_path,
        session_id=f"sid-corrupt-stage-{corruption}",
    )
    stored = read_run(tmp_path, reviewed["run_id"])
    if corruption == "missing":
        stored.pop("current_stage", None)
    else:
        stored["current_stage"] = {
            **deepcopy(stored["current_stage"]),
            "id": "plan",
            "task_id": "plan",
            "index": 0,
        }
    write_run(tmp_path, stored)

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as raised:
        expert_teams.approve_expert_team_stage(tmp_path, _approval_body(stored))

    assert raised.value.code == "corrupt_run_state"
    authoritative = read_run(tmp_path, reviewed["run_id"])
    assert authoritative["workflow_state"] == "awaiting_review"
    assert not any(
        entry.get("idempotency_key") == "approve-final"
        for entry in authoritative.get("action_journal") or []
    )


def test_final_approval_fails_closed_when_authoritative_task_template_is_missing(tmp_path):
    from api import expert_teams
    from api.expert_teams.storage import read_run, write_run

    reviewed = _awaiting_final_review(
        expert_teams,
        tmp_path,
        session_id="sid-corrupt-template",
    )
    stored = read_run(tmp_path, reviewed["run_id"])
    stored["_tasks_template"] = []
    write_run(tmp_path, stored)

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as raised:
        expert_teams.approve_expert_team_stage(tmp_path, _approval_body(stored))

    assert raised.value.code == "corrupt_run_state"


def _real_reviewed_run(expert_teams, tmp_path: Path, *, suffix: str) -> dict:
    return _generate(
        expert_teams,
        tmp_path,
        _ready_at_stage(
            expert_teams,
            tmp_path,
            team_id="content-creator-team",
            stage_index=4,
            session_id=f"sid-integrity-{suffix}",
        ),
        FINAL_MARKDOWN,
        f"integrity-{suffix}",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _office_token_state_path(workspace: Path, token: str) -> Path:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return workspace / ".taiji" / "expert-team-office-reviews" / f"{token_hash}.json"


def test_real_final_delivery_has_canonical_identity_manifest_and_artifact_digests(tmp_path):
    from api import expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="canonical")
    run_id = reviewed["run_id"]
    attempt_root = tmp_path / ".taiji" / "expert-team-deliveries" / run_id / "delivery" / "attempt-1"
    delivery = attempt_root / "delivery"
    source = attempt_root / "final.md"
    document = delivery / "document.docx"
    binding_path = attempt_root / "expert-team-delivery.json"
    expected_paths = {
        "final_document": document.relative_to(tmp_path).as_posix(),
        "delivery_package": delivery.relative_to(tmp_path).as_posix(),
        "quality_report": (delivery / "quality-report.json").relative_to(tmp_path).as_posix(),
    }
    artifacts = {
        item["kind"]: item
        for item in reviewed["artifacts"]
        if item.get("stage") == "delivery" and int(item.get("attempt") or 0) == 1
    }

    assert binding_path.is_file()
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    expected_binding = {
        "schema_version": 1,
        "run_id": run_id,
        "session_id": reviewed["session_id"],
        "stage_id": "delivery",
        "attempt": 1,
        "source_path": source.relative_to(tmp_path).as_posix(),
        "source_sha256": _sha256(source),
        "document_path": document.relative_to(tmp_path).as_posix(),
        "document_sha256": _sha256(document),
        "delivery_dir": delivery.relative_to(tmp_path).as_posix(),
    }
    assert {key: binding.get(key) for key in expected_binding} == expected_binding
    assert binding["rich_package"] == artifacts["final_rich_draft"]["package_binding"]
    for kind, expected_path in expected_paths.items():
        artifact = artifacts[kind]
        assert artifact["path"] == expected_path
        assert artifact["run_id"] == run_id
        assert artifact["session_id"] == reviewed["session_id"]
        assert artifact["stage"] == "delivery"
        assert artifact["attempt"] == 1
        assert artifact["source_sha256"] == binding["source_sha256"]
        assert artifact["document_sha256"] == binding["document_sha256"]
        assert artifact["binding_manifest_path"] == binding_path.relative_to(tmp_path).as_posix()


@pytest.mark.parametrize(
    "corruption",
    [
        "cross_run_binding",
        "wrong_attempt",
        "duplicate_kind",
        "absolute_path",
        "parent_segment",
        "symlink_escape",
    ],
)
def test_final_approval_rejects_noncanonical_or_ambiguous_artifacts(
    monkeypatch,
    tmp_path,
    corruption,
):
    from api import docx_engine_v2, expert_teams
    from api.expert_teams.storage import read_run, write_run

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix=corruption)
    stored = read_run(tmp_path, reviewed["run_id"])
    rows = stored["artifacts"]
    delivery_artifact = next(
        item for item in rows
        if item.get("stage") == "delivery" and item.get("kind") == "delivery_package" and item.get("attempt") == 1
    )
    document_artifact = next(
        item for item in rows
        if item.get("stage") == "delivery" and item.get("kind") == "final_document" and item.get("attempt") == 1
    )
    delivery = tmp_path / delivery_artifact["path"]
    if corruption == "cross_run_binding":
        delivery_artifact["run_id"] = "et-another-run"
    elif corruption == "wrong_attempt":
        document_artifact["attempt"] = 2
    elif corruption == "duplicate_kind":
        duplicate = deepcopy(delivery_artifact)
        duplicate["id"] = "delivery:1:delivery_package:duplicate"
        rows.append(duplicate)
    elif corruption == "absolute_path":
        delivery_artifact["path"] = str(delivery)
    elif corruption == "parent_segment":
        delivery_artifact["path"] = str(
            Path(delivery_artifact["path"]).parent / "nested" / ".." / "delivery"
        )
    else:
        outside = tmp_path / "outside-delivery"
        shutil.copytree(delivery, outside)
        shutil.rmtree(delivery)
        delivery.symlink_to(outside, target_is_directory=True)
    write_run(tmp_path, stored)
    validator_calls = []
    success = _validator_result(_report(_complete_wps_check(), status="passed"))

    def fake_validate(payload, workspace):
        validator_calls.append((payload, workspace))
        return success

    monkeypatch.setattr(docx_engine_v2, "validate_delivery", fake_validate)
    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(stored))

    assert blocked["workflow_state"] == "generated_invalid"
    assert blocked["delivery_gate"]["validator_code"] == "corrupt_delivery_artifacts"
    assert blocked["delivery_gate"]["required_action"] == "regenerate_delivery"
    assert validator_calls == []
    assert not any(
        entry.get("idempotency_key") == "approve-final"
        for entry in blocked.get("action_journal") or []
    )


def _successful_report_for_reviewed_run(reviewed: dict, tmp_path: Path) -> dict:
    wps_check = _write_bound_wps_sidecar(tmp_path, reviewed)
    return _report(wps_check, status="passed")


def test_final_approval_rechecks_document_binding_after_validator_returns(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="validator-toctou")
    document = tmp_path / next(
        item["path"]
        for item in reviewed["artifacts"]
        if item.get("stage") == "delivery" and item.get("kind") == "final_document"
    )
    success = _validator_result(_successful_report_for_reviewed_run(reviewed, tmp_path))

    def validate_then_replace(_payload, _workspace):
        document.write_bytes(document.read_bytes() + b"tampered-after-validation")
        return success

    monkeypatch.setattr(docx_engine_v2, "validate_delivery", validate_then_replace)
    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))

    assert blocked["workflow_state"] == "generated_invalid"
    assert blocked["delivery_gate"]["validator_code"] == "delivery_changed_during_validation"
    assert blocked["delivery_gate"]["required_action"] == "regenerate_delivery"
    assert not any(
        entry.get("idempotency_key") == "approve-final"
        for entry in blocked.get("action_journal") or []
    )


@pytest.mark.parametrize(
    "relative_target",
    ["delivery-package.json", "quality-report.json", "replay-report.json"],
)
def test_final_approval_rejects_nonbinding_package_file_changed_between_validations(
    monkeypatch,
    tmp_path,
    relative_target,
):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(
        expert_teams,
        tmp_path,
        suffix=f"validator-package-toctou-{Path(relative_target).stem}",
    )
    delivery = tmp_path / next(
        item["path"] for item in reviewed["artifacts"]
        if item.get("stage") == "delivery" and item.get("kind") == "delivery_package"
    )
    target = delivery / relative_target
    if not target.exists():
        target.write_text('{"initial":true}\n', encoding="utf-8")
    success = _validator_result(_successful_report_for_reviewed_run(reviewed, tmp_path))
    calls = []

    def validate_then_replace_package_file(payload, _workspace):
        calls.append(dict(payload))
        target.write_bytes(target.read_bytes() + f"changed-{len(calls)}".encode())
        return success

    monkeypatch.setattr(docx_engine_v2, "validate_delivery", validate_then_replace_package_file)
    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))

    assert len(calls) == 2
    assert calls[0]["write_report"] is True
    assert calls[1]["write_report"] is False
    assert blocked["workflow_state"] == "generated_invalid"
    assert blocked["delivery_gate"]["validator_code"] == "delivery_changed_during_validation"
    assert blocked["delivery_gate"]["required_action"] == "regenerate_delivery"


def test_completed_run_persists_complete_delivery_digest_set(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="digest-set")
    report = _successful_report_for_reviewed_run(reviewed, tmp_path)
    monkeypatch.setattr(
        docx_engine_v2,
        "validate_delivery",
        lambda _payload, _workspace: _validator_result(report),
    )

    completed = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))

    assert completed["workflow_state"] == "completed"
    digest_set = completed["delivery_gate"]["digest_set"]
    assert digest_set["run_id"] == completed["run_id"]
    assert digest_set["session_id"] == completed["session_id"]
    assert digest_set["stage_id"] == "delivery"
    assert digest_set["attempt"] == 1
    assert digest_set["source_sha256"] == next(
        item["source_sha256"] for item in completed["artifacts"]
        if item.get("stage") == "delivery" and item.get("kind") == "delivery_package"
    )
    assert digest_set["document_sha256"] == next(
        item["document_sha256"] for item in completed["artifacts"]
        if item.get("stage") == "delivery" and item.get("kind") == "delivery_package"
    )
    assert "final.md" in digest_set["files"]
    assert "delivery/document.docx" in digest_set["files"]
    assert "delivery/quality-report.json" in digest_set["files"]
    assert "delivery/delivery-package.json" in digest_set["files"]


def test_complete_wps_check_without_bound_acceptance_sidecar_cannot_complete(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="missing-wps-sidecar")
    binding_path = tmp_path / next(
        item["binding_manifest_path"] for item in reviewed["artifacts"]
        if item.get("stage") == "delivery" and item.get("kind") == "delivery_package"
    )
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    wps_check = _complete_wps_check()
    wps_check["reviewedBy"] = "王审核"
    wps_check["documentSha256"] = binding["document_sha256"]
    report = _report(wps_check, status="passed")
    monkeypatch.setattr(
        docx_engine_v2,
        "validate_delivery",
        lambda _payload, _workspace: _validator_result(report),
    )

    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))

    assert blocked["workflow_state"] == "awaiting_review"
    assert blocked["delivery_gate"]["status"] == "office_acceptance_required"
    assert blocked["delivery_gate"]["required_action"] == "complete_office_acceptance"


def _wps_payload(reviewed: dict, tmp_path: Path, *, evidence: Path) -> dict:
    delivery = next(
        item for item in reviewed["artifacts"]
        if item.get("stage") == "delivery" and item.get("kind") == "delivery_package"
    )
    return {
        "session_id": reviewed["session_id"],
        "delivery_dir": delivery["path"],
        "status": "passed",
        "reviewer": "王审核",
        "note": "已在 WPS 打开文档，逐页检查目录、版式、图表和分页。",
        "visual_checks": [
            "document_opened",
            "layout_reviewed",
            "content_order_reviewed",
            "figures_reviewed",
            "tables_reviewed",
        ],
        "evidence_files": [str(evidence)],
    }


def _tokenized_wps_payload(docx_engine_v2, reviewed: dict, tmp_path: Path, *, evidence_source: Path) -> dict:
    delivery = next(item for item in reviewed["artifacts"] if item.get("kind") == "delivery_package")
    begun, status = docx_engine_v2.begin_office_review(
        {"session_id": reviewed["session_id"], "delivery_dir": delivery["path"]},
        tmp_path,
        trusted_reviewer="bwb@default",
        open_document=lambda _path: None,
    )
    assert status == 200
    evidence = tmp_path / begun["evidence_dir"] / evidence_source.name
    evidence.write_bytes(evidence_source.read_bytes())
    payload = _wps_payload(reviewed, tmp_path, evidence=evidence)
    payload.update(
        {
            "review_token": begun["review_token"],
            "attested_actual_office_review": True,
        }
    )
    return payload


def test_real_first_pending_office_lifecycle_begin_get_safe_submit_and_replay(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams
    from api import routes
    from api.expert_teams import trusted_identity
    from api.expert_teams.office_review import OFFICE_POLICY_V1
    from tests.test_expert_team_stage_artifact_contract import _brief, _contract_run_ready_for_attempt, _raw

    run = _contract_run_ready_for_attempt(tmp_path, stage_index=3)
    input_refs = [{"ref_type": "stage_artifact", "artifact_id": "materials:1", "sha256": "1" * 64}, {"ref_type": "stage_artifact", "artifact_id": "draft:1", "sha256": "2" * 64}]
    reserved = expert_teams.reserve_expert_team_execution_start(tmp_path, run["run_id"], expected_version=run["version"], runtime_adapter="RunnerRuntimeAdapter", input_refs=input_refs)
    generating = expert_teams.mark_expert_team_execution_started(tmp_path, run["run_id"], {"stream_id": "stream-first-office", "execution_start_id": reserved["execution_start_id"]})
    content = {"title": _brief()["exact_title"], "document_type": "work_report", "section_map": [{"section_id": "SEC-1", "heading": "工作开展情况"}], "fact_usage": [], "asset_requests": [], "review_report": {"schema_version": "content-review-report/v1", "checks": {key: "passed" for key in ("brief_alignment", "fact_traceability", "document_purity", "confidentiality", "document_structure")}, "issues": [], "change_summary": ["通过"], "unresolved_issue_ids": []}, "open_issues": []}
    reviewed = expert_teams.mark_expert_team_execution_complete(tmp_path, run["run_id"], {"stream_id": generating["execution_stream_id"], "stage_id": "polish", "attempt": generating["execution_attempt"], "id": "review-first-office", "kind": "chat", "content": _raw("reviewed_document", content, document=f"# {_brief()['exact_title']}\n\n## 工作开展情况\n\n正文。")})
    resolver = trusted_identity.TrustedIdentityResolver({"enabled": False}, production=False);resolver._config = {"enabled": True}
    identity_session = resolver.install_test_principal({"subject": "approver", "display_name": "审批人", "roles": ["document-approver"], "expires_at": int(time.time()) + 3600})
    monkeypatch.setattr(trusted_identity, "get_trusted_identity_resolver", lambda: resolver)
    approved = expert_teams.approve_expert_team_stage(tmp_path, {"session_id": reviewed["session_id"], "run_id": reviewed["run_id"], "stage_id": "polish", "expected_version": reviewed["version"], "idempotency_key": "approve-first-office", "trusted_identity_session_id": identity_session})
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("system delivery must not use model gateway")))
    delivered_payload, delivered_status = routes._start_expert_team_execution(tmp_path, approved, {})
    assert delivered_status == 200, delivered_payload
    reviewed = delivered_payload["run"]
    pending = expert_teams.read_expert_team_run(tmp_path, reviewed["run_id"])
    assert pending["view"]["office_review"] is not None, json.dumps({"ref": pending.get("current_delivery_manifest_ref"), "gate": pending.get("delivery_gate"), "office_view": pending.get("office_review_view"), "artifacts": pending.get("artifacts")}, ensure_ascii=False)
    assert pending["view"]["office_review"]["status"] == "pending"
    assert pending["view"]["office_review"]["review_session_status"] == "begin_required"
    assert "review_token" not in json.dumps(pending)
    from api.expert_teams.delivery_integrity import canonical_delivery_dir
    delivery_dir = canonical_delivery_dir(tmp_path, reviewed["run_id"], "delivery", 1)
    principal = {
        "subject": "reviewer-safe", "display_name": "王审核", "roles": ["document-reviewer"],
        "auth_method": "oidc_pkce", "identity_snapshot_sha256": "9" * 64,
    }
    begun, begin_status = docx_engine_v2.begin_office_review(
        {"session_id": reviewed["session_id"], "delivery_dir": str(delivery_dir)}, tmp_path,
        trusted_principal=principal, open_document=lambda _path: None,
    )
    assert begin_status == 200, begun
    screenshot = Path(__file__).resolve().parents[1] / "docs" / "images" / "update-banner-whats-new-after.png"
    registry_key = docx_engine_v2._office_review_token_key(tmp_path, reviewed)
    with docx_engine_v2._ACTIVE_OFFICE_REVIEW_TOKENS_LOCK:
        docx_engine_v2._ACTIVE_OFFICE_REVIEW_TOKENS[registry_key]["expires_at_ns"] = 0
    expired_upload, expired_upload_status = docx_engine_v2.upload_structured_office_evidence(
        {"session_id": reviewed["session_id"], "run_id": reviewed["run_id"], "expected_version": str(reviewed["version"])},
        {"file_0": ("first-page.png", screenshot.read_bytes())}, tmp_path, trusted_principal=principal,
    )
    assert expired_upload_status == 409 and expired_upload["code"] == "rebegin_required"
    begun, begin_status = docx_engine_v2.begin_office_review(
        {"session_id": reviewed["session_id"], "delivery_dir": str(delivery_dir)}, tmp_path,
        trusted_principal=principal, open_document=lambda _path: None,
    )
    assert begin_status == 200, begun
    for bad_files, bad_principal, expected_status in (
        ({"file_0": ("../escape.png", screenshot.read_bytes())}, principal, 400),
        ({"file_0": ("renamed.png", b"MZ" + b"x" * 64)}, principal, 400),
        ({"file_0": ("first-page.png", screenshot.read_bytes())}, {**principal, "subject": "other-reviewer"}, 403),
    ):
        rejected, rejected_status = docx_engine_v2.upload_structured_office_evidence(
            {"session_id": reviewed["session_id"], "run_id": reviewed["run_id"], "expected_version": str(reviewed["version"])},
            bad_files, tmp_path, trusted_principal=bad_principal,
        )
        assert rejected_status == expected_status and rejected["ok"] is False
    uploaded, upload_status = docx_engine_v2.upload_structured_office_evidence(
        {
            "session_id": reviewed["session_id"], "run_id": reviewed["run_id"],
            "expected_version": str(reviewed["version"]),
        },
        {"file_0": ("first-page.png", screenshot.read_bytes())},
        tmp_path,
        trusted_principal=principal,
    )
    assert upload_status == 200 and uploaded["ok"] is True
    assert uploaded["count"] == 1 and uploaded["files"][0]["name"].endswith(".png")
    assert len(uploaded["files"][0]["sha256_short"]) == 12
    assert "path" not in json.dumps(uploaded) and "token" not in json.dumps(uploaded)
    ready = expert_teams.read_expert_team_run(tmp_path, reviewed["run_id"])
    assert ready["view"]["office_review"]["review_session_status"] == "ready"
    payload = {
        "session_id": reviewed["session_id"], "run_id": reviewed["run_id"], "expected_version": reviewed["version"],
        "status": "passed", "checklist": {key: ("passed" if disposition == "required" else "not_applicable") for key, disposition in OFFICE_POLICY_V1["checklist"].items()},
        "issues": [], "note": "已在 WPS 打开正式文档并逐页检查目录、表格和整体版式。",
        "idempotency_key": "first-pending-safe-submit-1",
    }
    from api.expert_teams import office_review
    real_consume = office_review.consume_review_token
    consume_attempts = 0
    def fail_first_consume(path, state):
        nonlocal consume_attempts
        consume_attempts += 1
        if consume_attempts == 1:
            raise OSError("fault after acceptance prepared")
        return real_consume(path, state)
    monkeypatch.setattr(office_review, "consume_review_token", fail_first_consume)
    failed, failed_status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path, trusted_principal=principal)
    assert failed_status == 400 and failed["ok"] is False
    assert docx_engine_v2.active_office_review_session_status(tmp_path, reviewed) == "ready"
    accepted, accepted_status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path, trusted_principal=principal)
    replayed, replay_status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path, trusted_principal=principal)
    assert accepted_status == replay_status == 200 and accepted["ok"] is replayed["ok"] is True
    assert accepted["idempotent_replay"] is replayed["idempotent_replay"] is True
    assert consume_attempts == 2
    assert docx_engine_v2.active_office_review_session_status(tmp_path, reviewed) == "begin_required"
    final = expert_teams.read_expert_team_run(tmp_path, reviewed["run_id"])
    assert final["view"]["office_review"]["decision"] == "passed"


@pytest.mark.parametrize(
    ("corruption", "expected_code"),
    [
        ("missing_semantic_note", "wps_visual_metadata_invalid"),
        ("avatar_evidence", "wps_visual_evidence_invalid"),
        ("wrong_session", "expert_delivery_binding_invalid"),
        ("changed_document", "expert_delivery_binding_invalid"),
    ],
)
def test_expert_delivery_wps_acceptance_rejects_unbound_or_nonsemantic_evidence(
    monkeypatch,
    tmp_path,
    corruption,
    expected_code,
):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix=f"wps-{corruption}")
    screenshot = Path(__file__).resolve().parents[1] / "docs" / "images" / "update-banner-whats-new-after.png"
    avatar = Path(__file__).resolve().parents[1] / "static" / "assets" / "writeflow" / "team-content-creator.png"
    payload = _tokenized_wps_payload(
        docx_engine_v2,
        reviewed,
        tmp_path,
        evidence_source=avatar if corruption == "avatar_evidence" else screenshot,
    )
    if corruption == "missing_semantic_note":
        payload["note"] = "已检查"
    elif corruption == "wrong_session":
        payload["session_id"] = "sid-other"
    elif corruption == "changed_document":
        document = tmp_path / next(
            item["path"] for item in reviewed["artifacts"]
            if item.get("stage") == "delivery" and item.get("kind") == "final_document"
        )
        document.write_bytes(document.read_bytes() + b"changed-before-wps")
    engine_calls = []
    monkeypatch.setattr(
        docx_engine_v2,
        "run_engine",
        lambda args: engine_calls.append(args),
    )

    result, status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path)

    assert status == 400
    assert result["ok"] is False
    assert result["code"] == expected_code
    assert engine_calls == []


def test_real_expert_delivery_wps_acceptance_writes_bound_semantic_sidecar(tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="wps-valid")
    screenshot = Path(__file__).resolve().parents[1] / "docs" / "images" / "update-banner-whats-new-after.png"
    payload = _tokenized_wps_payload(docx_engine_v2, reviewed, tmp_path, evidence_source=screenshot)

    result, status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path)

    assert status == 200 and result["ok"] is True
    delivery = tmp_path / payload["delivery_dir"]
    sidecar_path = delivery.parent / "expert-team-wps-acceptance.json"
    assert sidecar_path.is_file()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["schema_version"] == 1
    assert sidecar["run_id"] == reviewed["run_id"]
    assert sidecar["session_id"] == reviewed["session_id"]
    assert sidecar["stage_id"] == "delivery"
    assert sidecar["attempt"] == 1
    assert sidecar["reviewer"] == "bwb@default"
    assert "WPS" in sidecar["note"]
    assert sidecar["document_sha256"] == next(
        item["document_sha256"] for item in reviewed["artifacts"]
        if item.get("stage") == "delivery" and item.get("kind") == "delivery_package"
    )
    assert sidecar["visual_checks"] == payload["visual_checks"]
    assert sidecar["visual_evidence"]
    assert sidecar["visual_evidence"][0]["path"].startswith("evidence/wps-visual/")


@pytest.mark.parametrize("drift", ["modified", "deleted"])
def test_completed_run_read_detects_delivery_digest_drift(monkeypatch, tmp_path, drift):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix=f"read-drift-{drift}")
    report = _successful_report_for_reviewed_run(reviewed, tmp_path)
    monkeypatch.setattr(
        docx_engine_v2,
        "validate_delivery",
        lambda _payload, _workspace: _validator_result(report),
    )
    completed = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))
    assert completed["workflow_state"] == "completed"
    document = tmp_path / next(
        item["path"] for item in completed["artifacts"]
        if item.get("stage") == "delivery" and item.get("kind") == "final_document"
    )
    if drift == "modified":
        document.write_bytes(document.read_bytes() + b"post-completion-replacement")
    else:
        document.unlink()

    reread = expert_teams.read_expert_team_run(tmp_path, completed["run_id"])

    assert reread["workflow_state"] == "completed"
    assert reread["completion_integrity"]["status"] == "drifted"
    assert reread["status"] == "error"
    assert reread["execution_status"] == "error"
    assert reread["view"]["presentation"]["state"] == "completed_invalid"
    assert reread["view"]["presentation"]["title"] == "已完成交付异常"
    assert reread["view"]["presentation"]["primary_action"]["id"] == "view_result"


def test_duplicate_completed_approval_rechecks_digest_before_replay(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="duplicate-drift")
    report = _successful_report_for_reviewed_run(reviewed, tmp_path)
    monkeypatch.setattr(docx_engine_v2, "validate_delivery", lambda _p, _w: _validator_result(report))
    body = _approval_body(reviewed, key="duplicate-drift-key")
    completed = expert_teams.approve_expert_team_stage(tmp_path, body)
    document = tmp_path / next(
        item["path"] for item in completed["artifacts"] if item.get("kind") == "final_document"
    )
    document.unlink()

    replayed = expert_teams.approve_expert_team_stage(tmp_path, body)

    assert replayed["completion_integrity"]["status"] == "drifted"
    assert replayed["status"] == "error"
    assert replayed["view"]["presentation"]["state"] == "completed_invalid"


def test_completed_read_requires_final_rich_draft_digest(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="rich-drift")
    report = _successful_report_for_reviewed_run(reviewed, tmp_path)
    monkeypatch.setattr(docx_engine_v2, "validate_delivery", lambda _p, _w: _validator_result(report))
    completed = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))
    rich = next(item for item in completed["artifacts"] if item.get("kind") == "final_rich_draft")
    (tmp_path / rich["path"]).unlink()

    reread = expert_teams.read_expert_team_run(tmp_path, completed["run_id"])

    assert reread["completion_integrity"]["status"] == "drifted"
    assert reread["view"]["presentation"]["state"] == "completed_invalid"


def test_completed_read_revalidates_run_artifact_metadata(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams
    from api.expert_teams.storage import read_run, write_run

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="metadata-drift")
    report = _successful_report_for_reviewed_run(reviewed, tmp_path)
    monkeypatch.setattr(docx_engine_v2, "validate_delivery", lambda _p, _w: _validator_result(report))
    completed = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))
    other = tmp_path / "other.docx"
    other.write_bytes(b"PK\x03\x04other")
    stored = read_run(tmp_path, completed["run_id"])
    next(
        item for item in stored["artifacts"] if item.get("kind") == "final_document"
    )["path"] = "other.docx"
    stored["stage_outputs"][-1]["document_delivery"]["document_path"] = "other.docx"
    write_run(tmp_path, stored)

    reread = expert_teams.read_expert_team_run(tmp_path, completed["run_id"])

    assert reread["completion_integrity"]["status"] == "drifted"
    assert reread["view"]["presentation"]["state"] == "completed_invalid"


@pytest.mark.parametrize("unsafe", [".", ".."])
def test_safe_run_id_rejects_dot_segments(unsafe):
    from api.expert_teams.storage import safe_run_id

    with pytest.raises(ValueError):
        safe_run_id(unsafe)


@pytest.mark.parametrize("operation", ["replace", "rerender", "validate"])
def test_completed_expert_delivery_rejects_asset_mutation_endpoints(monkeypatch, tmp_path, operation):
    from api import docx_engine_v2, expert_teams

    real_validate_delivery = docx_engine_v2.validate_delivery
    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix=f"immutable-{operation}")
    delivery = tmp_path / next(
        item["path"] for item in reviewed["artifacts"]
        if item.get("stage") == "delivery" and item.get("kind") == "delivery_package"
    )
    render_plan = delivery / "render-plan.json"
    if operation == "rerender" and not render_plan.exists():
        render_plan.write_text('{"figures":[]}\n', encoding="utf-8")
    report = _successful_report_for_reviewed_run(reviewed, tmp_path)
    monkeypatch.setattr(
        docx_engine_v2,
        "validate_delivery",
        lambda _payload, _workspace: _validator_result(report),
    )
    completed = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))
    assert completed["workflow_state"] == "completed"
    engine_calls = []
    monkeypatch.setattr(docx_engine_v2, "run_engine", lambda args: engine_calls.append(args))
    if operation == "replace":
        document = delivery / "document.docx"
        screenshot = Path(__file__).resolve().parents[1] / "docs" / "images" / "update-banner-whats-new-after.png"
        result, status = docx_engine_v2.replace_asset(
            {
                "figure_id": "figure_1",
                "docx_path": str(document),
                "image_path": str(screenshot),
                "out_path": str(document),
            },
            tmp_path,
        )
    elif operation == "rerender":
        result, status = docx_engine_v2.rerender_asset(
            {"figure_id": "figure_1", "manifest_path": str(render_plan)},
            tmp_path,
        )
    else:
        result, status = real_validate_delivery(
            {"delivery_dir": str(delivery), "write_report": True},
            tmp_path,
        )

    assert status == 409
    assert result["code"] == "expert_delivery_immutable"
    assert engine_calls == []


def test_wps_acceptance_racing_final_approval_cannot_mutate_completed_delivery(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="concurrent-wps-approve")
    report = _successful_report_for_reviewed_run(reviewed, tmp_path)
    validator_entered = threading.Event()
    release_validator = threading.Event()
    validator_calls = []

    def blocking_validate(payload, _workspace):
        validator_calls.append(dict(payload))
        if len(validator_calls) == 1:
            validator_entered.set()
            assert release_validator.wait(timeout=10)
        return _validator_result(report)

    monkeypatch.setattr(docx_engine_v2, "validate_delivery", blocking_validate)
    engine_calls = []
    monkeypatch.setattr(docx_engine_v2, "run_engine", lambda args: engine_calls.append(args))
    screenshot = Path(__file__).resolve().parents[1] / "docs" / "images" / "update-banner-whats-new-after.png"
    wps_payload = _wps_payload(reviewed, tmp_path, evidence=screenshot)

    with ThreadPoolExecutor(max_workers=2) as pool:
        approval_future = pool.submit(
            expert_teams.approve_expert_team_stage,
            tmp_path,
            _approval_body(reviewed),
        )
        assert validator_entered.wait(timeout=10)
        wps_future = pool.submit(docx_engine_v2.record_wps_visual_acceptance, wps_payload, tmp_path)
        time.sleep(0.1)
        assert not wps_future.done()
        release_validator.set()
        completed = approval_future.result(timeout=20)
        wps_result, wps_status = wps_future.result(timeout=20)

    assert completed["workflow_state"] == "completed"
    assert wps_status == 409
    assert wps_result["code"] == "expert_delivery_immutable"
    assert engine_calls == []


@pytest.mark.parametrize("writer", ["create", "package"])
def test_generic_writer_racing_approval_cannot_touch_expert_attempt(monkeypatch, tmp_path, writer):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix=f"writer-race-{writer}")
    report = _successful_report_for_reviewed_run(reviewed, tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def blocking_validate(_payload, _workspace):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(timeout=10)
        return _validator_result(report)

    monkeypatch.setattr(docx_engine_v2, "validate_delivery", blocking_validate)
    engine_calls = []
    monkeypatch.setattr(docx_engine_v2, "run_engine", lambda args: engine_calls.append(args))
    delivery = tmp_path / next(
        item["path"] for item in reviewed["artifacts"] if item.get("kind") == "delivery_package"
    )
    source = tmp_path / "generic-source.md"
    source.write_text("# generic\n", encoding="utf-8")

    with ThreadPoolExecutor(max_workers=2) as pool:
        approval = pool.submit(expert_teams.approve_expert_team_stage, tmp_path, _approval_body(reviewed))
        assert entered.wait(timeout=10)
        if writer == "create":
            blocked, status = docx_engine_v2.create_job(
                {
                    "template_id": "general-proposal",
                    "source_path": str(source),
                    "out_dir": str(delivery / "nested-create"),
                },
                tmp_path,
            )
        else:
            blocked, status = docx_engine_v2.package_rich_draft(
                {"source_path": str(source), "out_dir": str(delivery / "nested-package")},
                tmp_path,
            )
        release.set()
        completed = approval.result(timeout=20)

    assert status == 400
    assert blocked["code"] == "expert_delivery_writer_required"
    assert completed["workflow_state"] == "completed"
    assert engine_calls == []


@pytest.mark.parametrize(
    "operation",
    ["create", "package", "validate", "record", "rerender", "replace"],
)
def test_docx_endpoints_fail_closed_for_other_workspace_expert_delivery_paths(
    monkeypatch,
    tmp_path,
    operation,
):
    from api import docx_engine_v2

    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    delivery = (
        workspace_b / ".taiji" / "expert-team-deliveries" / "et-other" /
        "delivery" / "attempt-1" / "delivery"
    )
    delivery.mkdir(parents=True)
    source = workspace_a / "source.md"
    source.write_text("# source\n", encoding="utf-8")
    image = workspace_a / "evidence.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    document = delivery / "document.docx"
    document.write_bytes(b"PK\x03\x04foreign")
    quality = delivery / "quality-report.json"
    quality.write_text('{"status":"passed"}\n', encoding="utf-8")
    manifest = delivery / "render-plan.json"
    manifest.write_text('{"figures":[]}\n', encoding="utf-8")
    engine_calls = []
    monkeypatch.setattr(docx_engine_v2, "run_engine", lambda args: engine_calls.append(args))
    monkeypatch.setattr(
        docx_engine_v2,
        "_figure_adjustment_allowed_absolute_roots",
        lambda _workspace: [tmp_path],
    )

    if operation == "create":
        result, status = docx_engine_v2.create_job(
            {"template_id": "general-proposal", "source_path": str(source), "out_dir": str(delivery)},
            workspace_a,
        )
    elif operation == "package":
        result, status = docx_engine_v2.package_rich_draft(
            {"source_path": str(source), "out_dir": str(delivery)},
            workspace_a,
        )
    elif operation == "validate":
        result, status = docx_engine_v2.validate_delivery({"delivery_dir": str(delivery)}, workspace_a)
    elif operation == "record":
        result, status = docx_engine_v2.record_wps_visual_acceptance(
            {"delivery_dir": str(delivery), "status": "failed", "reviewer": "王审核", "note": "WPS 打开后检查页面版式。"},
            workspace_a,
        )
    elif operation == "rerender":
        result, status = docx_engine_v2.rerender_asset(
            {"figure_id": "figure_1", "manifest_path": str(manifest)},
            workspace_a,
        )
    else:
        result, status = docx_engine_v2.replace_asset(
            {
                "figure_id": "figure_1",
                "docx_path": str(document),
                "image_path": str(image),
                "out_path": str(document),
            },
            workspace_a,
        )

    assert status == 400
    assert result["ok"] is False
    assert engine_calls == []


def test_begin_office_review_opens_bound_document_and_issues_trusted_token(tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="begin-office")
    delivery = next(item for item in reviewed["artifacts"] if item.get("kind") == "delivery_package")
    client_reviewer = "伪造审核人"
    opened = []

    result, status = docx_engine_v2.begin_office_review(
        {
            "session_id": reviewed["session_id"],
            "delivery_dir": delivery["path"],
            "reviewer": client_reviewer,
        },
        tmp_path,
        trusted_reviewer="bwb@default",
        open_document=lambda path: opened.append(Path(path)),
    )

    assert status == 200 and result["ok"] is True
    assert opened == [tmp_path / delivery["path"] / "document.docx"]
    assert result["review_token"]
    assert result["reviewer"] == "bwb@default"
    assert result["reviewer"] != client_reviewer
    assert result["document_sha256"] == delivery["document_sha256"]
    assert (tmp_path / result["evidence_dir"]).is_dir()
    assert "token_state_path" not in result
    token_state = json.loads(_office_token_state_path(tmp_path, result["review_token"]).read_text(encoding="utf-8"))
    assert token_state["run_id"] == reviewed["run_id"]
    assert token_state["session_id"] == reviewed["session_id"]
    assert token_state["stage_id"] == "delivery"
    assert token_state["attempt"] == 1
    assert token_state["document_sha256"] == delivery["document_sha256"]
    assert token_state["reviewer"] == "bwb@default"
    assert token_state["state"] == "issued"


def test_os_office_open_must_confirm_launcher_success(monkeypatch, tmp_path):
    from api.expert_teams import office_review

    document = tmp_path / "document.docx"
    document.write_bytes(b"PK\x03\x04test")
    monkeypatch.setattr(office_review.sys, "platform", "darwin")
    monkeypatch.setattr(office_review.subprocess, "Popen", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        office_review.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, ["open", str(document)])
        ),
    )

    with pytest.raises(subprocess.CalledProcessError):
        office_review.open_document_with_os(document)


def test_office_review_note_requires_office_action_and_layout_semantics():
    from api import docx_engine_v2
    from api.expert_teams import runtime

    assert docx_engine_v2._expert_wps_metadata_error(
        reviewer="bwb@default",
        note="WPS Word测试测试测试",
    )
    assert not docx_engine_v2._expert_wps_metadata_error(
        reviewer="bwb@default",
        note="已在 WPS 打开文档，逐页检查目录和版式。",
    )
    assert not runtime._semantic_wps_note("WPS Word测试测试测试")
    assert runtime._semantic_wps_note("已在 WPS 打开文档，逐页检查目录和版式。")


def test_office_acceptance_requires_one_time_token_attestation_and_fresh_token_evidence(tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="office-token")
    delivery = next(item for item in reviewed["artifacts"] if item.get("kind") == "delivery_package")
    begun, begin_status = docx_engine_v2.begin_office_review(
        {"session_id": reviewed["session_id"], "delivery_dir": delivery["path"]},
        tmp_path,
        trusted_reviewer="bwb@default",
        open_document=lambda _path: None,
    )
    assert begin_status == 200
    screenshot_source = Path(__file__).resolve().parents[1] / "docs" / "images" / "update-banner-whats-new-after.png"
    evidence = tmp_path / begun["evidence_dir"] / "wps-page.png"
    evidence.write_bytes(screenshot_source.read_bytes())
    payload = _wps_payload(reviewed, tmp_path, evidence=evidence)
    payload.update(
        {
            "review_token": begun["review_token"],
            "attested_actual_office_review": True,
            "reviewer": "伪造审核人",
        }
    )

    accepted, status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path)
    replayed, replay_status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path)

    assert status == 200 and accepted["ok"] is True
    assert accepted["reviewer"] == "bwb@default"
    sidecar = json.loads((tmp_path / accepted["acceptance_manifest_path"]).read_text(encoding="utf-8"))
    assert sidecar["reviewer"] == "bwb@default"
    assert sidecar["office_review"]["attested_actual_office_review"] is True
    assert len(sidecar["office_review"]["token_hash"]) == 64
    assert replay_status == 409
    assert replayed["code"] == "office_review_token_used"


def test_office_review_token_survives_engine_failure_and_is_consumed_only_after_success(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="office-token-retry")
    delivery = next(item for item in reviewed["artifacts"] if item.get("kind") == "delivery_package")
    begun, status = docx_engine_v2.begin_office_review(
        {"session_id": reviewed["session_id"], "delivery_dir": delivery["path"]},
        tmp_path,
        trusted_reviewer="bwb@default",
        open_document=lambda _path: None,
    )
    assert status == 200
    source = Path(__file__).resolve().parents[1] / "docs" / "images" / "update-banner-whats-new-after.png"
    evidence = tmp_path / begun["evidence_dir"] / "wps-page.png"
    evidence.write_bytes(source.read_bytes())
    payload = _wps_payload(reviewed, tmp_path, evidence=evidence)
    payload.update(
        {
            "review_token": begun["review_token"],
            "attested_actual_office_review": True,
        }
    )
    real_run_engine = docx_engine_v2.run_engine
    calls = 0

    def fail_once(args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(
                args=args,
                returncode=3,
                stdout='{"ok":false,"code":"wps_visual_record_failed","message":"temporary failure"}\n',
                stderr="",
            )
        return real_run_engine(args)

    monkeypatch.setattr(docx_engine_v2, "run_engine", fail_once)
    failed, failed_status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path)
    token_after_failure = json.loads(
        _office_token_state_path(tmp_path, begun["review_token"]).read_text(encoding="utf-8")
    )
    accepted, accepted_status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path)
    replayed, replayed_status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path)

    assert failed_status == 400 and failed["ok"] is False
    assert token_after_failure["state"] == "issued"
    assert accepted_status == 200 and accepted["ok"] is True
    assert replayed_status == 409 and replayed["code"] == "office_review_token_used"


@pytest.mark.parametrize("corruption", ["missing_token", "missing_attestation", "outside_evidence", "stale_evidence"])
def test_office_acceptance_rejects_untrusted_or_stale_review_inputs(tmp_path, corruption):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix=f"office-token-{corruption}")
    delivery = next(item for item in reviewed["artifacts"] if item.get("kind") == "delivery_package")
    begun, status = docx_engine_v2.begin_office_review(
        {"session_id": reviewed["session_id"], "delivery_dir": delivery["path"]},
        tmp_path,
        trusted_reviewer="bwb@default",
        open_document=lambda _path: None,
    )
    assert status == 200
    screenshot_source = Path(__file__).resolve().parents[1] / "docs" / "images" / "update-banner-whats-new-after.png"
    evidence = tmp_path / begun["evidence_dir"] / "wps-page.png"
    evidence.write_bytes(screenshot_source.read_bytes())
    payload = _wps_payload(reviewed, tmp_path, evidence=evidence)
    payload.update({"review_token": begun["review_token"], "attested_actual_office_review": True})
    if corruption == "missing_token":
        payload.pop("review_token")
    elif corruption == "missing_attestation":
        payload["attested_actual_office_review"] = False
    elif corruption == "outside_evidence":
        payload["evidence_files"] = [str(screenshot_source)]
    else:
        token_state = json.loads(
            _office_token_state_path(tmp_path, begun["review_token"]).read_text(encoding="utf-8")
        )
        stale_ns = int(token_state["opened_at_ns"]) - 1_000_000_000
        os.utime(evidence, ns=(stale_ns, stale_ns))

    result, result_status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path)

    assert result_status == 400
    assert result["ok"] is False
    assert result["code"] in {"office_review_token_required", "office_review_evidence_invalid"}
