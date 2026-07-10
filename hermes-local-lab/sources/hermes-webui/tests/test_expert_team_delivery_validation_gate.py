"""Final expert-team delivery approval must use the authoritative DOCX validator."""

from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

import pytest


BASE_VISUAL_CHECKS = [
    "document_opened",
    "layout_reviewed",
    "content_order_reviewed",
]


def _awaiting_final_review(expert_teams, workspace: Path, *, session_id: str) -> dict:
    from api.expert_teams.delivery_integrity import canonical_attempt_root, write_binding_manifest
    from api.expert_teams.storage import write_run

    run = expert_teams.start_expert_team(
        workspace,
        {
            "session_id": session_id,
            "team_id": "content-creator-team",
            "prompt": "起草营业厅服务质效专项行动方案",
        },
    )
    stored = expert_teams.read_expert_team_run(workspace, run["run_id"])
    stored["workflow_state"] = "awaiting_review"
    stored["current_stage_index"] = 4
    final_task = dict((stored.get("_tasks_template") or stored.get("tasks") or [])[4])
    stored["current_stage"] = {
        **final_task,
        "task_id": str(final_task.get("task_id") or final_task.get("id") or "delivery"),
        "index": 4,
        "status": "awaiting_review",
    }
    stored["questions"] = [
        {**question, "status": "answered", "answer": "已确认"}
        for question in stored.get("questions") or []
    ]
    stored["validation"] = {"status": "pass", "message": ""}

    attempt_root = canonical_attempt_root(workspace, stored["run_id"], "delivery", 1)
    delivery_dir = attempt_root / "delivery"
    delivery_dir.mkdir(parents=True)
    source = attempt_root / "final.md"
    source.write_text("# 最终方案\n\n已形成完整交付。\n", encoding="utf-8")
    document = delivery_dir / "document.docx"
    document.write_bytes(b"PK\x03\x04placeholder")
    quality = delivery_dir / "quality-report.json"
    quality.write_text(json.dumps({"status": "passed_with_warnings"}), encoding="utf-8")
    (delivery_dir / "delivery-package.json").write_text('{"schemaVersion":"test"}\n', encoding="utf-8")
    binding_path, binding = write_binding_manifest(
        workspace,
        run_id=stored["run_id"],
        session_id=stored["session_id"],
        stage_id="delivery",
        attempt=1,
        source_path=source,
        document_path=document,
        delivery_dir=delivery_dir,
    )
    relative = lambda path: path.relative_to(workspace).as_posix()
    binding_relative = relative(binding_path)
    document_delivery = {
        "stage": "delivery",
        "attempt": 1,
        "raw_source_path": relative(source),
        "document_path": relative(document),
        "delivery_dir": relative(delivery_dir),
        "quality_report_path": relative(quality),
        "binding_manifest_path": binding_relative,
        "source_sha256": binding["source_sha256"],
        "document_sha256": binding["document_sha256"],
    }
    stored["stage_outputs"] = [
        {
            "task_id": "delivery",
            "stage_id": "delivery",
            "stage_attempt": 1,
            "content": "# 最终方案\n\n已形成完整交付。",
            "status": "generated",
            "document_delivery": document_delivery,
        }
    ]
    rich_draft = workspace / "final-rich.md"
    rich_draft.write_text("# 最终方案\n", encoding="utf-8")
    created_at = "2026-07-11T00:00:00+08:00"
    def delivery_artifact(kind: str, path: Path, status: str = "ready") -> dict:
        return {
            "id": f"delivery:1:{kind}",
            "kind": kind,
            "path": relative(path),
            "exists": True,
            "attempt": 1,
            "stage": "delivery",
            "status": status,
            "created_at": created_at,
            "run_id": stored["run_id"],
            "session_id": stored["session_id"],
            "source_sha256": binding["source_sha256"],
            "document_sha256": binding["document_sha256"],
            "binding_manifest_path": binding_relative,
        }

    stored["artifacts"] = [
        {
            "id": "delivery:1:final_rich_draft",
            "kind": "final_rich_draft",
            "path": "final-rich.md",
            "exists": True,
            "attempt": 1,
            "stage": "delivery",
            "status": "ready",
            "created_at": created_at,
        },
        delivery_artifact("final_document", document),
        delivery_artifact("delivery_package", delivery_dir),
        delivery_artifact("quality_report", quality, "passed_with_warnings"),
    ]
    write_run(workspace, stored)
    return expert_teams.read_expert_team_run(workspace, run["run_id"])


def _approval_body(run: dict, *, key: str = "approve-final") -> dict:
    return {
        "run_id": run["run_id"],
        "session_id": run["session_id"],
        "expected_version": run["version"],
        "stage_id": "delivery",
        "idempotency_key": key,
    }


def _report(*checks: dict, status: str = "failed") -> dict:
    return {
        "schemaVersion": "docx-engine-v2/validation-report",
        "status": status,
        "checks": list(checks),
        "warnings": [],
        "failures": [str(check.get("message") or check.get("id") or "") for check in checks if check.get("status") == "failed"],
    }


def _validator_result(report: dict) -> tuple[dict, int]:
    ok = report.get("status") != "failed"
    return {
        "ok": ok,
        "code": "" if ok else "delivery_validation_failed",
        "delivery_dir": "delivery",
        "quality_report_path": "delivery/quality-report.json",
        "quality_report": report,
        "failures": report.get("failures") or [],
    }, 200 if ok else 422


def _complete_wps_check() -> dict:
    return {
        "id": "wps_visual",
        "status": "passed",
        "reviewedAt": "2026-07-11T09:00:00+08:00",
        "reviewedBy": "王审核",
        "documentSha256": "a" * 64,
        "visualChecks": [*BASE_VISUAL_CHECKS, "figures_reviewed", "tables_reviewed"],
        "visualEvidence": [{"path": "evidence/wps-visual/page-1.png", "sha256": "b" * 64}],
    }


def _write_bound_wps_sidecar(workspace: Path, run: dict, check: dict | None = None) -> dict:
    from api.expert_teams.delivery_integrity import (
        read_binding_manifest,
        sha256_file,
        workspace_relative_path,
        write_wps_acceptance_manifest,
    )
    from api.expert_teams.office_review import (
        _token_state_path,
        consume_review_token,
        prepare_consumed_review_state,
        write_office_review_proof,
    )

    binding_path = workspace / next(
        item["binding_manifest_path"]
        for item in run["artifacts"]
        if item.get("kind") == "delivery_package"
    )
    binding = read_binding_manifest(binding_path)
    existing_acceptance = binding_path.parent / "expert-team-wps-acceptance.json"
    existing_proof = binding_path.parent / "expert-team-office-review-proof.json"
    if check is None and existing_acceptance.is_file() and existing_proof.is_file():
        existing = json.loads(existing_acceptance.read_text(encoding="utf-8"))
        return {
            "id": "wps_visual",
            "status": "passed",
            "reviewedAt": existing["reviewed_at"],
            "reviewedBy": existing["reviewer"],
            "documentSha256": existing["document_sha256"],
            "visualChecks": list(existing["visual_checks"]),
            "visualEvidence": [dict(item) for item in existing["visual_evidence"]],
        }
    bound_check = dict(check or _complete_wps_check())
    bound_check["reviewedBy"] = "王审核"
    bound_check["documentSha256"] = binding["document_sha256"]
    bound_check["visualChecks"] = list(bound_check.get("visualChecks") or [])
    delivery_dir = workspace / binding["delivery_dir"]
    evidence_path = delivery_dir / "evidence" / "wps-visual" / "page-1.png"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "docs" / "images" / "update-banner-whats-new-after.png"
    evidence_path.write_bytes(source.read_bytes())
    evidence = {
        "path": "evidence/wps-visual/page-1.png",
        "sha256": sha256_file(evidence_path),
        "sizeBytes": evidence_path.stat().st_size,
        "mediaType": "image/png",
    }
    bound_check["visualEvidence"] = [evidence]
    token_hash = hashlib.sha256(
        f"{binding['run_id']}:{binding['stage_id']}:{binding['attempt']}:test-office".encode("utf-8")
    ).hexdigest()
    evidence_dir = f".taiji/wps-evidence/{token_hash}"
    acceptance_path, _ = write_wps_acceptance_manifest(
        workspace,
        binding=binding,
        reviewer="王审核",
        note="已在 WPS 打开文档，逐页检查目录、版式、图表和分页。",
        visual_checks=list(bound_check["visualChecks"]),
        wps_check=bound_check,
        office_review={
            "token_hash": token_hash,
            "opened_at": "2026-07-11T08:59:00+08:00",
            "evidence_dir": evidence_dir,
            "attested_actual_office_review": True,
        },
    )
    issued_state = {
        "schema_version": 1,
        "token_hash": token_hash,
        "state": "issued",
        "run_id": binding["run_id"],
        "session_id": binding["session_id"],
        "stage_id": binding["stage_id"],
        "attempt": binding["attempt"],
        "document_sha256": binding["document_sha256"],
        "reviewer": "王审核",
        "opened_at_ns": 1,
        "opened_at": "2026-07-11T08:59:00+08:00",
        "expires_at_ns": 9999999999999999999,
        "evidence_dir": evidence_dir,
    }
    consumed = prepare_consumed_review_state(
        issued_state,
        acceptance_manifest_path=workspace_relative_path(workspace, acceptance_path),
        acceptance_manifest_sha256=sha256_file(acceptance_path),
        canonical_evidence=[evidence],
    )
    write_office_review_proof(workspace, binding, consumed)
    consume_review_token(_token_state_path(workspace, token_hash), consumed)
    return bound_check


def test_validate_delivery_runs_authoritative_cli_with_json_and_write_report(monkeypatch, tmp_path):
    from api import docx_engine_v2

    delivery = tmp_path / "delivery"
    delivery.mkdir()
    captured: dict[str, list[str]] = {}
    report = _report(_complete_wps_check(), status="passed")

    def fake_run_engine(args):
        captured["args"] = [str(item) for item in args]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "deliveryDir": str(delivery),
                    "qualityReportPath": str(delivery / "quality-report.json"),
                    "qualityReport": report,
                    "failures": [],
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr(docx_engine_v2, "run_engine", fake_run_engine)

    payload, status = docx_engine_v2.validate_delivery(
        {"delivery_dir": "delivery", "write_report": True},
        tmp_path,
    )

    assert status == 200 and payload["ok"] is True
    assert captured["args"][0].endswith("/src/cli/validate-delivery.js")
    assert captured["args"] == [
        captured["args"][0],
        "--delivery-dir",
        str(delivery),
        "--json",
        "--write-report",
    ]
    assert payload["quality_report"] == report


def test_validate_delivery_preserves_structured_failure_report(monkeypatch, tmp_path):
    from api import docx_engine_v2

    delivery = tmp_path / "delivery"
    delivery.mkdir()
    report = _report(
        {"id": "delivery_files", "status": "failed", "message": "document.docx sha256 mismatch"}
    )
    monkeypatch.setattr(
        docx_engine_v2,
        "run_engine",
        lambda args: subprocess.CompletedProcess(
            args=args,
            returncode=3,
            stdout=json.dumps(
                {
                    "ok": False,
                    "code": "delivery_validation_failed",
                    "deliveryDir": str(delivery),
                    "qualityReport": report,
                    "failures": report["failures"],
                }
            )
            + "\n",
            stderr="",
        ),
    )

    payload, status = docx_engine_v2.validate_delivery({"delivery_dir": "delivery"}, tmp_path)

    assert status == 422
    assert payload["ok"] is False
    assert payload["code"] == "delivery_validation_failed"
    assert payload["quality_report"] == report
    assert payload["failures"] == ["document.docx sha256 mismatch"]


@pytest.mark.parametrize(
    ("check_id", "message"),
    [
        ("docx_zip", "document.docx is not a valid ZIP package"),
        ("delivery_files", "document.docx sha256 mismatch"),
        ("replay_report", "replay-report.json is required"),
    ],
)
def test_final_approval_never_completes_when_automated_delivery_validation_fails(
    monkeypatch,
    tmp_path,
    check_id,
    message,
):
    from api import docx_engine_v2, expert_teams

    reviewed = _awaiting_final_review(expert_teams, tmp_path, session_id=f"sid-{check_id}")
    calls = []
    report = _report({"id": check_id, "status": "failed", "message": message})

    def fake_validate(payload, workspace):
        calls.append((dict(payload), Path(workspace)))
        return _validator_result(report)

    monkeypatch.setattr(docx_engine_v2, "validate_delivery", fake_validate)
    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))

    expected_delivery = next(
        item["path"] for item in reviewed["artifacts"] if item.get("kind") == "delivery_package"
    )
    assert calls == [({"delivery_dir": expected_delivery, "write_report": True}, tmp_path)]
    assert blocked["workflow_state"] == "generated_invalid"
    assert blocked["workflow_state"] != "completed"
    assert blocked["delivery_gate"]["status"] == "regeneration_required"
    assert blocked["delivery_gate"]["required_action"] == "regenerate_delivery"
    assert "重新生成" in blocked["last_validation_error"]
    assert not any(entry.get("idempotency_key") == "approve-final" for entry in blocked.get("action_journal") or [])
    persisted = expert_teams.read_expert_team_run(tmp_path, blocked["run_id"])
    assert persisted["version"] == blocked["version"]
    assert persisted["delivery_gate"] == blocked["delivery_gate"]


@pytest.mark.parametrize(
    "wps_check",
    [
        {"id": "wps_visual", "status": "failed", "message": "WPS/Word visual acceptance has not been verified."},
        {
            "id": "wps_visual",
            "status": "passed",
            "reviewedAt": "2026-07-11T09:00:00+08:00",
            "reviewedBy": "reviewer",
            "documentSha256": "a" * 64,
            "visualChecks": BASE_VISUAL_CHECKS,
            "visualEvidence": [],
        },
    ],
)
def test_final_approval_keeps_review_open_for_missing_office_evidence(monkeypatch, tmp_path, wps_check):
    from api import docx_engine_v2, expert_teams

    reviewed = _awaiting_final_review(expert_teams, tmp_path, session_id="sid-office-incomplete")
    report = _report(
        wps_check,
        status="failed" if wps_check["status"] == "failed" else "passed",
    )
    monkeypatch.setattr(docx_engine_v2, "validate_delivery", lambda _payload, _workspace: _validator_result(report))

    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed))

    assert blocked["workflow_state"] == "awaiting_review"
    assert blocked["delivery_gate"]["status"] == "office_acceptance_required"
    assert blocked["delivery_gate"]["required_action"] == "complete_office_acceptance"
    assert "WPS/Word" in blocked["last_validation_error"]
    assert "重新生成" not in blocked["last_validation_error"]
    assert "重写" not in blocked["last_validation_error"]
    assert "WPS/Word" in blocked["view"]["presentation"]["detail"]
    assert not any(entry.get("idempotency_key") == "approve-final" for entry in blocked.get("action_journal") or [])


def test_office_gate_retry_reuses_same_idempotency_key_then_completes_once(monkeypatch, tmp_path):
    from api import docx_engine_v2, expert_teams

    reviewed = _awaiting_final_review(expert_teams, tmp_path, session_id="sid-office-retry")
    incomplete = _report(
        {"id": "wps_visual", "status": "failed", "message": "visual evidence is missing"},
        status="failed",
    )
    bound_check = _write_bound_wps_sidecar(tmp_path, reviewed)
    complete = _report(bound_check, status="passed")
    results = [
        _validator_result(incomplete),
        _validator_result(complete),
        _validator_result(complete),
    ]
    calls = []

    def fake_validate(payload, workspace):
        calls.append((dict(payload), Path(workspace)))
        return results.pop(0)

    monkeypatch.setattr(docx_engine_v2, "validate_delivery", fake_validate)
    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed, key="same-key"))
    completed_body = _approval_body(blocked, key="same-key")
    completed = expert_teams.approve_expert_team_stage(tmp_path, completed_body)
    replayed = expert_teams.approve_expert_team_stage(tmp_path, completed_body)

    assert blocked["workflow_state"] == "awaiting_review"
    assert completed["workflow_state"] == "completed"
    assert completed["delivery_gate"]["status"] == "passed"
    assert replayed["workflow_state"] == "completed"
    assert replayed["version"] == completed["version"]
    assert len(calls) == 3
    assert [call[0]["write_report"] for call in calls] == [True, True, False]
    journal = [entry for entry in completed.get("action_journal") or [] if entry.get("idempotency_key") == "same-key"]
    assert len(journal) == 1


@pytest.mark.parametrize("corruption", ["non_zip_docx", "hash_change", "missing_replay"])
def test_real_validator_blocks_corrupted_latest_delivery_package(tmp_path, corruption):
    from api import expert_teams
    from tests.test_expert_team_delivery_contract import FINAL_MARKDOWN, _generate, _ready_at_stage

    reviewed = _generate(
        expert_teams,
        tmp_path,
        _ready_at_stage(
            expert_teams,
            tmp_path,
            team_id="content-creator-team",
            stage_index=4,
            session_id=f"sid-real-{corruption}",
        ),
        FINAL_MARKDOWN,
        f"real-{corruption}",
    )
    delivery = tmp_path / next(
        item["path"]
        for item in reviewed["artifacts"]
        if item.get("stage") == "delivery" and item.get("kind") == "delivery_package"
    )
    if corruption == "non_zip_docx":
        (delivery / "document.docx").write_bytes(b"not-a-zip")
    elif corruption == "hash_change":
        source = delivery / "source.md"
        source.write_text(source.read_text(encoding="utf-8") + "\n篡改\n", encoding="utf-8")
    else:
        replay = delivery / "replay-report.json"
        assert replay.is_file()
        replay.unlink()

    blocked = expert_teams.approve_expert_team_stage(tmp_path, _approval_body(reviewed, key=f"approve-{corruption}"))

    assert blocked["workflow_state"] == "generated_invalid"
    assert blocked["delivery_gate"]["status"] == "regeneration_required"
    assert "重新生成" in blocked["last_validation_error"]
    assert not any(
        entry.get("idempotency_key") == f"approve-{corruption}"
        for entry in blocked.get("action_journal") or []
    )


def test_real_office_evidence_allows_same_approval_key_to_complete(tmp_path):
    from api import docx_engine_v2, expert_teams
    from tests.test_expert_team_delivery_contract import FINAL_MARKDOWN, _generate, _ready_at_stage

    reviewed = _generate(
        expert_teams,
        tmp_path,
        _ready_at_stage(
            expert_teams,
            tmp_path,
            team_id="content-creator-team",
            stage_index=4,
            session_id="sid-real-office-gate",
        ),
        FINAL_MARKDOWN,
        "real-office-gate",
    )
    body = _approval_body(reviewed, key="same-real-key")
    blocked = expert_teams.approve_expert_team_stage(tmp_path, body)
    delivery_dir = next(
        item["path"]
        for item in blocked["artifacts"]
        if item.get("stage") == "delivery" and item.get("kind") == "delivery_package"
    )
    evidence = Path(__file__).resolve().parents[1] / "docs" / "images" / "update-banner-whats-new-after.png"
    begun, begin_status = docx_engine_v2.begin_office_review(
        {"session_id": reviewed["session_id"], "delivery_dir": delivery_dir},
        tmp_path,
        trusted_reviewer="bwb@default",
        open_document=lambda _path: None,
    )
    assert begin_status == 200
    token_evidence = tmp_path / begun["evidence_dir"] / evidence.name
    token_evidence.write_bytes(evidence.read_bytes())

    acceptance, status = docx_engine_v2.record_wps_visual_acceptance(
        {
            "delivery_dir": delivery_dir,
            "session_id": reviewed["session_id"],
            "status": "passed",
            "reviewer": "王审核",
            "note": "已在 WPS 打开文档，逐页检查目录、版式、图表和分页。",
            "review_token": begun["review_token"],
            "attested_actual_office_review": True,
            "visual_checks": [*BASE_VISUAL_CHECKS, "figures_reviewed", "tables_reviewed"],
            "evidence_files": [str(token_evidence)],
        },
        tmp_path,
    )
    retry_body = {**body, "expected_version": blocked["version"]}
    completed = expert_teams.approve_expert_team_stage(tmp_path, retry_body)
    replayed = expert_teams.approve_expert_team_stage(tmp_path, retry_body)

    assert status == 200 and acceptance["ok"] is True
    assert blocked["workflow_state"] == "awaiting_review"
    assert blocked["delivery_gate"]["status"] == "office_acceptance_required"
    assert completed["workflow_state"] == "completed"
    assert completed["delivery_gate"]["status"] == "passed"
    assert replayed["version"] == completed["version"]
    assert sum(
        entry.get("idempotency_key") == "same-real-key"
        for entry in completed.get("action_journal") or []
    ) == 1
