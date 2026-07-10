"""Adversarial path and provenance contracts for expert-team deliveries."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from tests.test_expert_team_delivery_integrity_hardening import (
    _real_reviewed_run,
    _successful_report_for_reviewed_run,
    _wps_payload,
)
from tests.test_expert_team_delivery_validation_gate import _approval_body, _validator_result


def _screenshot_source() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "images" / "update-banner-whats-new-after.png"


def _record_real_acceptance(docx_engine_v2, reviewed: dict, workspace: Path) -> tuple[dict, dict, Path]:
    delivery = next(item for item in reviewed["artifacts"] if item.get("kind") == "delivery_package")
    begun, begin_status = docx_engine_v2.begin_office_review(
        {"session_id": reviewed["session_id"], "delivery_dir": delivery["path"]},
        workspace,
        trusted_reviewer="bwb@default",
        open_document=lambda _path: None,
    )
    assert begin_status == 200
    upload = workspace / begun["evidence_dir"] / "wps-page.png"
    upload.write_bytes(_screenshot_source().read_bytes())
    payload = _wps_payload(reviewed, workspace, evidence=upload)
    payload.update(
        {
            "review_token": begun["review_token"],
            "attested_actual_office_review": True,
        }
    )
    accepted, accept_status = docx_engine_v2.record_wps_visual_acceptance(payload, workspace)
    assert accept_status == 200 and accepted["ok"] is True
    evidence = next(
        item
        for item in accepted["quality_report"]["checks"]
        if item.get("id") == "wps_visual"
    )["visualEvidence"][0]
    stable_copy = workspace / delivery["path"] / evidence["path"]
    return accepted, begun, stable_copy


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_record_office_acceptance_rejects_symlink_in_token_evidence_path(
    monkeypatch,
    tmp_path,
    kind,
):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix=f"token-symlink-{kind}")
    delivery = next(item for item in reviewed["artifacts"] if item.get("kind") == "delivery_package")
    begun, begin_status = docx_engine_v2.begin_office_review(
        {"session_id": reviewed["session_id"], "delivery_dir": delivery["path"]},
        tmp_path,
        trusted_reviewer="bwb@default",
        open_document=lambda _path: None,
    )
    assert begin_status == 200
    evidence_root = tmp_path / begun["evidence_dir"]
    if kind == "file":
        real = evidence_root / "real.png"
        real.write_bytes(_screenshot_source().read_bytes())
        submitted = evidence_root / "linked.png"
        submitted.symlink_to(real)
    else:
        real_dir = evidence_root / "real"
        real_dir.mkdir()
        real = real_dir / "page.png"
        real.write_bytes(_screenshot_source().read_bytes())
        linked_dir = evidence_root / "linked"
        linked_dir.symlink_to(real_dir, target_is_directory=True)
        submitted = linked_dir / "page.png"
    payload = _wps_payload(reviewed, tmp_path, evidence=submitted)
    payload.update(
        {
            "review_token": begun["review_token"],
            "attested_actual_office_review": True,
        }
    )
    engine_calls = []
    monkeypatch.setattr(docx_engine_v2, "run_engine", lambda args: engine_calls.append(args))

    result, status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path)

    assert status == 400
    assert result["code"] == "office_review_evidence_invalid"
    assert engine_calls == []


def test_final_approval_rejects_forged_sidecar_without_consumed_server_proof(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="forged-office-sidecar")
    report = _successful_report_for_reviewed_run(reviewed, tmp_path)
    delivery = next(item for item in reviewed["artifacts"] if item.get("kind") == "delivery_package")
    attempt_root = (tmp_path / delivery["path"]).parent
    sidecar = json.loads((attempt_root / "expert-team-wps-acceptance.json").read_text(encoding="utf-8"))
    token_hash = sidecar["office_review"]["token_hash"]
    (attempt_root / "expert-team-office-review-proof.json").unlink()
    (tmp_path / ".taiji" / "expert-team-office-reviews" / f"{token_hash}.json").unlink()
    monkeypatch.setattr(docx_engine_v2, "validate_delivery", lambda _p, _w: _validator_result(report))

    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))

    assert blocked["workflow_state"] == "awaiting_review"
    assert blocked["delivery_gate"]["status"] == "office_acceptance_required"


@pytest.mark.parametrize("drift", ["deleted", "replaced", "symlink"])
def test_final_approval_rechecks_canonical_office_evidence_copy(monkeypatch, tmp_path, drift):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix=f"office-copy-{drift}")
    accepted, _begun, stable_copy = _record_real_acceptance(docx_engine_v2, reviewed, tmp_path)
    report = accepted["quality_report"]
    if drift == "deleted":
        stable_copy.unlink()
    elif drift == "replaced":
        stable_copy.write_bytes(stable_copy.read_bytes() + b"replacement")
    else:
        outside = tmp_path / "replacement.png"
        outside.write_bytes(_screenshot_source().read_bytes())
        stable_copy.unlink()
        stable_copy.symlink_to(outside)
    monkeypatch.setattr(docx_engine_v2, "validate_delivery", lambda _p, _w: _validator_result(report))

    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))

    assert blocked["workflow_state"] != "completed"
    assert blocked["delivery_gate"]["status"] == "office_acceptance_required"


@pytest.mark.parametrize(
    "corruption",
    ["manifest_traversal", "top_source_escape", "asset_missing", "asset_replaced", "asset_symlink"],
)
def test_final_approval_rejects_rich_package_changed_after_render(
    monkeypatch,
    tmp_path,
    corruption,
):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix=f"rich-causal-{corruption}")
    accepted, _begun, _stable_copy = _record_real_acceptance(docx_engine_v2, reviewed, tmp_path)
    rich = next(item for item in reviewed["artifacts"] if item.get("kind") == "final_rich_draft")
    manifest_path = tmp_path / rich["manifest_path"]
    package_dir = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    figure = manifest["figures"][0]
    display = package_dir / figure["displayPath"]
    outside = tmp_path / "outside-rich.png"
    outside.write_bytes(_screenshot_source().read_bytes())
    if corruption == "manifest_traversal":
        figure["displayPath"] = os.path.relpath(outside, package_dir)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    elif corruption == "top_source_escape":
        manifest["sourcePath"] = str(outside)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    elif corruption == "asset_missing":
        display.unlink()
    elif corruption == "asset_replaced":
        display.write_bytes(outside.read_bytes())
    else:
        display.unlink()
        display.symlink_to(outside)
    monkeypatch.setattr(
        docx_engine_v2,
        "validate_delivery",
        lambda _p, _w: _validator_result(accepted["quality_report"]),
    )

    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))

    assert blocked["workflow_state"] == "generated_invalid"
    assert blocked["delivery_gate"]["validator_code"] in {
        "corrupt_delivery_artifacts",
        "delivery_changed_during_validation",
    }


def test_completed_read_detects_office_proof_and_rich_asset_drift(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="proof-rich-drift")
    accepted, _begun, _stable_copy = _record_real_acceptance(docx_engine_v2, reviewed, tmp_path)
    monkeypatch.setattr(
        docx_engine_v2,
        "validate_delivery",
        lambda _p, _w: _validator_result(accepted["quality_report"]),
    )
    completed = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))
    assert completed["workflow_state"] == "completed"
    digest_files = completed["delivery_gate"]["digest_set"]["files"]
    proof_key = next(key for key in digest_files if key.endswith("office-review-proof.json"))
    proof = (
        tmp_path
        / ".taiji"
        / "expert-team-deliveries"
        / completed["run_id"]
        / "delivery"
        / "attempt-1"
        / proof_key
    )
    proof.write_bytes(proof.read_bytes() + b"drift")

    reread = expert_teams.read_expert_team_run(tmp_path, completed["run_id"])

    assert reread["completion_integrity"]["status"] == "drifted"


@pytest.mark.parametrize(
    "corruption",
    ["unconsumed", "foreign_binding", "reviewer_time", "token_filename_mismatch"],
)
def test_final_approval_requires_matching_consumed_office_provenance(
    monkeypatch,
    tmp_path,
    corruption,
):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix=f"provenance-{corruption}")
    accepted, begun, _stable_copy = _record_real_acceptance(docx_engine_v2, reviewed, tmp_path)
    token_hash = hashlib.sha256(begun["review_token"].encode("utf-8")).hexdigest()
    state_path = tmp_path / ".taiji" / "expert-team-office-reviews" / f"{token_hash}.json"
    delivery = next(item for item in reviewed["artifacts"] if item.get("kind") == "delivery_package")
    attempt_root = (tmp_path / delivery["path"]).parent
    proof_path = attempt_root / "expert-team-office-review-proof.json"
    sidecar_path = attempt_root / "expert-team-wps-acceptance.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    if corruption == "unconsumed":
        state["state"] = "issued"
        state.pop("consumed_at", None)
    elif corruption == "foreign_binding":
        state["session_id"] = "sid-foreign"
        proof["session_id"] = "sid-foreign"
    elif corruption == "reviewer_time":
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["office_review"]["opened_at"] = "2099-01-01T00:00:00+08:00"
        sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    else:
        state["token_hash"] = "d" * 64
        proof["token_hash"] = "d" * 64
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    proof_path.write_text(json.dumps(proof, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        docx_engine_v2,
        "validate_delivery",
        lambda _p, _w: _validator_result(accepted["quality_report"]),
    )

    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))

    assert blocked["workflow_state"] == "awaiting_review"
    assert blocked["delivery_gate"]["status"] == "office_acceptance_required"


def test_office_proof_write_failure_keeps_token_retryable(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams
    from api.expert_teams import office_review

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="proof-write-retry")
    delivery = next(item for item in reviewed["artifacts"] if item.get("kind") == "delivery_package")
    begun, begin_status = docx_engine_v2.begin_office_review(
        {"session_id": reviewed["session_id"], "delivery_dir": delivery["path"]},
        tmp_path,
        trusted_reviewer="bwb@default",
        open_document=lambda _path: None,
    )
    assert begin_status == 200
    upload = tmp_path / begun["evidence_dir"] / "wps-page.png"
    upload.write_bytes(_screenshot_source().read_bytes())
    payload = _wps_payload(reviewed, tmp_path, evidence=upload)
    payload.update(
        {
            "review_token": begun["review_token"],
            "attested_actual_office_review": True,
        }
    )
    real_write_proof = office_review.write_office_review_proof
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("proof disk unavailable")
        return real_write_proof(*args, **kwargs)

    monkeypatch.setattr(office_review, "write_office_review_proof", fail_once)
    failed, failed_status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path)
    token_hash = hashlib.sha256(begun["review_token"].encode("utf-8")).hexdigest()
    state_path = tmp_path / ".taiji" / "expert-team-office-reviews" / f"{token_hash}.json"
    state_after_failure = json.loads(state_path.read_text(encoding="utf-8"))
    accepted, accepted_status = docx_engine_v2.record_wps_visual_acceptance(payload, tmp_path)

    assert failed_status == 400 and failed["ok"] is False
    assert state_after_failure["state"] == "issued"
    assert accepted_status == 200 and accepted["ok"] is True


def test_token_upload_can_be_deleted_after_canonical_copy_is_recorded(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix="token-upload-disposable")
    accepted, begun, _stable_copy = _record_real_acceptance(docx_engine_v2, reviewed, tmp_path)
    shutil.rmtree(tmp_path / begun["evidence_dir"])
    monkeypatch.setattr(
        docx_engine_v2,
        "validate_delivery",
        lambda _p, _w: _validator_result(accepted["quality_report"]),
    )

    completed = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))

    assert completed["workflow_state"] == "completed"
    assert completed["completion_integrity"]["status"] == "valid"


@pytest.mark.parametrize("drift", ["deleted", "replaced"])
def test_completed_read_detects_canonical_office_evidence_drift(monkeypatch, tmp_path, drift):
    from api import docx_engine_v2, expert_teams

    reviewed = _real_reviewed_run(expert_teams, tmp_path, suffix=f"completed-office-{drift}")
    accepted, _begun, stable_copy = _record_real_acceptance(docx_engine_v2, reviewed, tmp_path)
    monkeypatch.setattr(
        docx_engine_v2,
        "validate_delivery",
        lambda _p, _w: _validator_result(accepted["quality_report"]),
    )
    completed = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))
    assert completed["workflow_state"] == "completed"
    if drift == "deleted":
        stable_copy.unlink()
    else:
        stable_copy.write_bytes(stable_copy.read_bytes() + b"post-completion")

    reread = expert_teams.read_expert_team_run(tmp_path, completed["run_id"])

    assert reread["completion_integrity"]["status"] == "drifted"


@pytest.mark.parametrize("operation", ["create", "package", "validate", "record", "rerender", "replace"])
def test_case_variant_expert_tree_alias_never_reaches_generic_engine(monkeypatch, tmp_path, operation):
    from api import docx_engine_v2

    delivery = (
        tmp_path
        / ".TAIJI"
        / "EXPERT-TEAM-DELIVERIES"
        / "et-case"
        / "delivery"
        / "attempt-1"
        / "delivery"
    )
    delivery.mkdir(parents=True)
    source = tmp_path / "source.md"
    source.write_text("# source\n", encoding="utf-8")
    document = delivery / "document.docx"
    document.write_bytes(b"PK\x03\x04case")
    quality = delivery / "quality-report.json"
    quality.write_text('{"status":"passed"}\n', encoding="utf-8")
    render_plan = delivery / "render-plan.json"
    render_plan.write_text('{"figures":[]}\n', encoding="utf-8")
    image = tmp_path / "image.png"
    image.write_bytes(_screenshot_source().read_bytes())
    engine_calls = []
    monkeypatch.setattr(docx_engine_v2, "run_engine", lambda args: engine_calls.append(args))

    if operation == "create":
        result, status = docx_engine_v2.create_job(
            {"template_id": "general-proposal", "source_path": str(source), "out_dir": str(delivery)},
            tmp_path,
        )
    elif operation == "package":
        result, status = docx_engine_v2.package_rich_draft(
            {"source_path": str(source), "out_dir": str(delivery)},
            tmp_path,
        )
    elif operation == "validate":
        result, status = docx_engine_v2.validate_delivery({"delivery_dir": str(delivery)}, tmp_path)
    elif operation == "record":
        result, status = docx_engine_v2.record_wps_visual_acceptance(
            {"delivery_dir": str(delivery), "status": "failed"},
            tmp_path,
        )
    elif operation == "rerender":
        result, status = docx_engine_v2.rerender_asset(
            {"figure_id": "figure_1", "manifest_path": str(render_plan)},
            tmp_path,
        )
    else:
        result, status = docx_engine_v2.replace_asset(
            {
                "figure_id": "figure_1",
                "docx_path": str(document),
                "image_path": str(image),
                "out_path": str(document),
            },
            tmp_path,
        )

    assert status == 400
    assert result["ok"] is False
    assert engine_calls == []


def test_case_variant_cross_workspace_expert_tree_is_rejected(monkeypatch, tmp_path):
    from api import docx_engine_v2

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    foreign = (
        tmp_path
        / "foreign"
        / ".TAIJI"
        / "EXPERT-TEAM-DELIVERIES"
        / "et-foreign"
        / "delivery"
        / "attempt-1"
        / "delivery"
    )
    foreign.mkdir(parents=True)
    (foreign / "quality-report.json").write_text('{"status":"passed"}\n', encoding="utf-8")
    monkeypatch.setattr(docx_engine_v2, "_figure_adjustment_allowed_absolute_roots", lambda _root: [tmp_path])
    engine_calls = []
    monkeypatch.setattr(docx_engine_v2, "run_engine", lambda args: engine_calls.append(args))

    result, status = docx_engine_v2.validate_delivery({"delivery_dir": str(foreign)}, workspace)

    assert status == 400
    assert result["ok"] is False
    assert engine_calls == []
