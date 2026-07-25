from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64
META_START = "<<<TAIJI_META_V1>>>"
META_END = "<<<TAIJI_META_END>>>"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _raw_manifest(payload: dict) -> str:
    meta = {
        "artifact_type": "delivery_manifest",
        "summary": "DOCX 已生成，等待本机确认。",
        "payload": payload,
        "blocking_issues": [],
    }
    return f"{META_START}\n{json.dumps(meta, ensure_ascii=False)}\n{META_END}"


def _delivery_inputs(tmp_path: Path) -> dict:
    from api.expert_teams.documents import build_render_input_binding
    from api.expert_teams.delivery_integrity import canonical_attempt_root

    root = canonical_attempt_root(tmp_path, "run-standalone", "delivery", 1)
    canonical = root / "canonical" / "document.md"
    assets = root / "assets" / "asset-manifest.json"
    gates = root / "reviews" / "semantic-gates.json"
    document = root / "delivery" / "document.docx"
    automatic = root / "delivery" / "quality-report.json"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    document.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("# 部门月度工作汇报\n\n数据缺失处：待补充。\n", encoding="utf-8")
    document.write_bytes(b"PK\x03\x04standalone-docx")
    _write_json(assets, {"schema_version": "expert-asset-manifest/v1", "assets": []})
    semantic = {
        "schema_version": "expert-semantic-gates/v1",
        "brief_status": "passed",
        "semantic_status": "passed",
        "evidence_status": "passed",
        "status": "passed",
        "artifact_id": "polish:1",
        "artifact_sha256": HEX_A,
        "brief_revision": 1,
        "brief_sha256": HEX_B,
        "issues": [],
    }
    _write_json(gates, semantic)
    quality = {
        "status": "passed_with_warnings",
        "checks": [
            {"id": "title_structure", "status": "passed", "message": "标题结构完整"},
            {"id": "missing_data_markers", "status": "passed", "message": "缺失数据已明确标识"},
            {"id": "wps_visual", "status": "not_verified", "message": "未执行 WPS 验收"},
        ],
        "automaticQuality": {"assetStatus": "passed", "renderStatus": "passed", "issues": []},
    }
    _write_json(automatic, quality)
    brief = {
        "status": "confirmed",
        "exact_title": "部门月度工作汇报",
        "document_type": "work_report",
        "confirmed_revision": 1,
        "confirmed_sha256": HEX_B,
    }
    artifact = {"artifact_id": "polish:1", "sha256": HEX_A}
    template = {"id": "standalone-work-report", "version": "1.0.0", "package_sha256": HEX_C}
    renderer = {
        "name": "docx-engine-v2",
        "version": "2.0.0",
        "build_sha256": HEX_D,
        "profile_id": "standalone-default",
        "profile_sha256": "e" * 64,
    }
    render_input = build_render_input_binding(
        brief=brief,
        artifact=artifact,
        canonical_document_path=canonical,
        asset_manifest_path=assets,
        semantic_gates_path=gates,
        template=template,
        renderer=renderer,
    )
    return {
        "root": root,
        "canonical": canonical,
        "assets": assets,
        "gates": gates,
        "document": document,
        "automatic": automatic,
        "semantic": semantic,
        "quality": quality,
        "brief": brief,
        "artifact": artifact,
        "template": template,
        "renderer": renderer,
        "render_input": render_input,
    }


def _build_standalone_binding(tmp_path: Path) -> tuple[dict, dict]:
    from api.expert_teams.documents import build_delivery_binding_v3

    inputs = _delivery_inputs(tmp_path)
    binding = build_delivery_binding_v3(
        inputs["root"],
        session_id="session-standalone",
        run_id="run-standalone",
        stage_id="delivery",
        stage_attempt=1,
        delivery_attempt=1,
        document_revision=1,
        brief=inputs["brief"],
        artifact=inputs["artifact"],
        assets=inputs["assets"],
        semantic_gates=inputs["semantic"],
        template=inputs["template"],
        renderer=inputs["renderer"],
        render_input_fingerprint=inputs["render_input"]["render_input_fingerprint"],
        document=inputs["document"],
        quality=inputs["automatic"],
    )
    return binding, inputs


@pytest.mark.parametrize("intermediate", ["reviews", "delivery"])
def test_standalone_binding_rejects_intermediate_directory_symlink_before_any_write(
    tmp_path,
    intermediate,
):
    from api.expert_teams.documents import (
        FinalDocumentDeliveryError,
        build_delivery_binding_v3,
    )

    inputs = _delivery_inputs(tmp_path)
    linked = inputs["root"] / intermediate
    external = tmp_path.parent / f"{tmp_path.name}-external-{intermediate}"
    linked.rename(external)
    linked.symlink_to(external, target_is_directory=True)

    with pytest.raises(FinalDocumentDeliveryError, match="symlink"):
        build_delivery_binding_v3(
            inputs["root"],
            session_id="session-standalone",
            run_id="run-standalone",
            stage_id="delivery",
            stage_attempt=1,
            delivery_attempt=1,
            document_revision=1,
            brief=inputs["brief"],
            artifact=inputs["artifact"],
            assets=inputs["assets"],
            semantic_gates=inputs["semantic"],
            template=inputs["template"],
            renderer=inputs["renderer"],
            render_input_fingerprint=inputs["render_input"]["render_input_fingerprint"],
            document=inputs["document"],
            quality=inputs["automatic"],
        )

    assert not (inputs["root"] / "expert-team-delivery.json").exists()
    assert not (external / "standalone-quality-report.json").exists()
    assert not (inputs["root"] / "reviews" / "standalone-quality-report.json").exists()


def _current_run(tmp_path: Path, binding: dict) -> dict:
    from api.expert_teams.delivery_integrity import sha256_file, workspace_relative_path

    binding_path = tmp_path / ".taiji/expert-team-deliveries/run-standalone/delivery/attempt-1/expert-team-delivery.json"
    binding_display = workspace_relative_path(tmp_path, binding_path)
    binding_sha256 = sha256_file(binding_path)
    manifest_payload = {
        "schema_version": "delivery-manifest/v2",
        "delivery_attempt": 1,
        "delivery_binding_path": binding_display,
        "delivery_binding_sha256": binding_sha256,
        "document_sha256": binding["document"]["sha256"],
    }
    reservation = {
        "reservation_id": "delivery-attempt-1",
        "delivery_attempt": 1,
        "document_revision": 1,
        "render_input_fingerprint": binding["render_input_fingerprint"],
        "status": "generated_valid",
    }
    return {
        "product_mode": "standalone",
        "workflow_state": "awaiting_review",
        "session_id": "session-standalone",
        "run_id": "run-standalone",
        "current_delivery_manifest_ref": {
            "artifact_id": "delivery:1",
            "sha256": HEX_D,
            "stage_attempt": 1,
            "delivery_attempt": 1,
            "delivery_binding_path": binding_display,
            "delivery_binding_sha256": binding_sha256,
        },
        "current_delivery_attempt_reservation": reservation,
        "delivery_attempt_reservations": [dict(reservation)],
        "stage_artifacts": [
            {
                "artifact_id": "delivery:1",
                "sha256": HEX_D,
                "artifact_type": "delivery_manifest",
                "stage_attempt": 1,
                "payload": manifest_payload,
            }
        ],
    }


def test_standalone_v3_binding_and_v2_manifest_exclude_enterprise_review_contract(tmp_path):
    from api.expert_teams.documents import build_delivery_manifest_from_binding
    from api.expert_teams.delivery_integrity import (
        classify_delivery_binding,
        sha256_file,
        workspace_relative_path,
    )

    binding, inputs = _build_standalone_binding(tmp_path)
    binding_path = inputs["root"] / "expert-team-delivery.json"
    quality_path = inputs["root"] / "delivery" / "quality-report.json"
    assert binding["schema_version"] == "expert-delivery-binding/v3"
    assert binding["product_mode"] == "standalone"
    assert classify_delivery_binding(binding) == "standalone_pre_confirmation"
    assert binding["standalone_quality_report"]["path"] == "reviews/standalone-quality-report.json"
    assert "layered_quality_report" not in binding

    projected = {
        **binding,
        "_binding_path": workspace_relative_path(tmp_path, binding_path),
        "_binding_sha256": sha256_file(binding_path),
        "_quality_report_sha256": sha256_file(quality_path),
    }
    manifest = build_delivery_manifest_from_binding(projected, inputs["quality"])
    assert manifest["schema_version"] == "delivery-manifest/v2"
    assert manifest["product_mode"] == "standalone"
    assert manifest["local_confirmation_required"] is True
    assert manifest["document_sha256"] == binding["document"]["sha256"]
    assert manifest["automatic_check_summary"] == {
        "status": "passed",
        "passed_count": 2,
        "failed_count": 0,
        "warning_count": 0,
        "blocking_count": 0,
    }
    public_contract = json.dumps(
        {
            "binding": binding,
            "quality": json.loads((inputs["root"] / binding["standalone_quality_report"]["path"]).read_text()),
            "manifest": manifest,
        },
        ensure_ascii=False,
    ).lower()
    for forbidden in ("office", "wps", "approval", "approver"):
        assert forbidden not in public_contract


def test_enterprise_v2_manifest_remains_delivery_manifest_v1(tmp_path):
    from api.expert_teams.documents import build_delivery_manifest_from_binding

    binding = {
        "schema_version": "expert-delivery-binding/v2",
        "run_id": "run-enterprise",
        "stage_id": "delivery",
        "delivery_attempt": 1,
        "document_revision": 1,
        "render_input_fingerprint": HEX_A,
        "automatic_quality_report": {"path": "delivery/quality-report.json", "sha256": HEX_B},
        "_binding_path": ".taiji/expert-team-deliveries/run-enterprise/delivery/attempt-1/expert-team-delivery.json",
        "_binding_sha256": HEX_C,
        "_quality_report_sha256": HEX_B,
    }
    quality = {
        "checks": [{"id": "structure", "status": "passed"}],
        "automaticQuality": {"assetStatus": "passed", "renderStatus": "passed", "issues": []},
    }
    manifest = build_delivery_manifest_from_binding(binding, quality)
    assert manifest["schema_version"] == "delivery-manifest/v1"
    assert manifest["office_review_required"] is True
    assert "local_confirmation_required" not in manifest


def test_delivery_manifest_v2_is_strict_and_never_accepts_enterprise_fields():
    from api.expert_teams.stage_artifacts import StageArtifactError, build_stage_artifact, parse_stage_response

    manifest = {
        "schema_version": "delivery-manifest/v2",
        "product_mode": "standalone",
        "delivery_binding_path": ".taiji/expert-team-deliveries/run-standalone/delivery/attempt-1/expert-team-delivery.json",
        "delivery_binding_sha256": HEX_A,
        "render_input_fingerprint": HEX_B,
        "delivery_attempt": 1,
        "document_revision": 1,
        "document_sha256": HEX_C,
        "standalone_quality_report_sha256": HEX_D,
        "automatic_check_summary": {
            "status": "passed",
            "passed_count": 5,
            "failed_count": 0,
            "warning_count": 0,
            "blocking_count": 0,
        },
        "local_confirmation_required": True,
    }
    raw = _raw_manifest(manifest)
    brief = {"status": "confirmed", "confirmed_revision": 1, "confirmed_sha256": HEX_A}
    parsed = parse_stage_response(raw, artifact_type="delivery_manifest", requires_document=False)
    artifact = build_stage_artifact(
        parsed,
        stage_id="delivery",
        stage_attempt=1,
        brief=brief,
        input_refs=[],
        now="2026-07-25T10:00:00+08:00",
    )
    assert artifact["payload"] == manifest

    for mutation in (
        {"office_review_required": True},
        {"delivery_binding_path": "../escape.json"},
        {"local_confirmation_required": False},
        {"delivery_attempt": True},
        {
            "automatic_check_summary": {
                **manifest["automatic_check_summary"],
                "passed_count": True,
            }
        },
    ):
        bad = {**manifest, **mutation}
        parsed = parse_stage_response(_raw_manifest(bad), artifact_type="delivery_manifest", requires_document=False)
        with pytest.raises(StageArtifactError):
            build_stage_artifact(
                parsed,
                stage_id="delivery",
                stage_attempt=1,
                brief=brief,
                input_refs=[],
                now="2026-07-25T10:00:00+08:00",
            )


def test_current_standalone_delivery_rejects_stale_cross_run_and_digest_drift(tmp_path):
    from api.expert_teams.standalone_delivery import StandaloneDeliveryError, load_current_standalone_delivery

    binding, _inputs = _build_standalone_binding(tmp_path)
    run = _current_run(tmp_path, binding)
    loaded = load_current_standalone_delivery(tmp_path, run)
    assert loaded["binding"] == binding
    assert loaded["document_sha256"] == binding["document"]["sha256"]

    cross_run = {**run, "run_id": "other-run"}
    with pytest.raises(StandaloneDeliveryError) as cross:
        load_current_standalone_delivery(tmp_path, cross_run)
    assert cross.value.code == "delivery_binding_cross_run"

    stale = json.loads(json.dumps(run))
    stale["current_delivery_manifest_ref"]["delivery_binding_sha256"] = "f" * 64
    with pytest.raises(StandaloneDeliveryError) as stale_error:
        load_current_standalone_delivery(tmp_path, stale)
    assert stale_error.value.code == "delivery_binding_hash_mismatch"

    document = loaded["document_path"]
    document.write_bytes(document.read_bytes() + b"tampered")
    with pytest.raises(StandaloneDeliveryError) as drift:
        load_current_standalone_delivery(tmp_path, run)
    assert drift.value.code == "delivery_document_hash_mismatch"


def test_completed_delivery_context_requires_one_confirmed_reservation(tmp_path):
    from api.expert_teams.standalone_delivery import (
        StandaloneDeliveryError,
        resolve_standalone_open_target,
        validate_standalone_delivery_context,
    )

    binding, _inputs = _build_standalone_binding(tmp_path)
    run = _current_run(tmp_path, binding)
    run["workflow_state"] = "completed"
    run["current_delivery_attempt_reservation"]["status"] = "confirmed"
    run["delivery_attempt_reservations"] = [dict(run["current_delivery_attempt_reservation"])]
    assert validate_standalone_delivery_context(tmp_path, run)["delivery_attempt"] == 1
    assert resolve_standalone_open_target(tmp_path, run, "document").name == "document.docx"
    assert resolve_standalone_open_target(tmp_path, run, "folder").name == "delivery"

    run["delivery_attempt_reservations"].append(dict(run["current_delivery_attempt_reservation"]))
    with pytest.raises(StandaloneDeliveryError) as duplicate:
        validate_standalone_delivery_context(tmp_path, run)
    assert duplicate.value.code == "delivery_reservation_stale"

    run = _current_run(tmp_path, binding)
    run["current_delivery_attempt_reservation"]["status"] = "confirmed"
    run["delivery_attempt_reservations"] = [dict(run["current_delivery_attempt_reservation"])]
    with pytest.raises(StandaloneDeliveryError) as premature:
        validate_standalone_delivery_context(tmp_path, run)
    assert premature.value.code == "delivery_reservation_stale"


def test_standalone_open_target_is_an_enum_and_rejects_symlink(tmp_path):
    from api.expert_teams.standalone_delivery import StandaloneDeliveryError, resolve_standalone_open_target

    binding, _inputs = _build_standalone_binding(tmp_path)
    run = _current_run(tmp_path, binding)
    assert resolve_standalone_open_target(tmp_path, run, "document").name == "document.docx"
    assert resolve_standalone_open_target(tmp_path, run, "folder").name == "delivery"
    with pytest.raises(StandaloneDeliveryError) as arbitrary:
        resolve_standalone_open_target(tmp_path, run, "../../etc/passwd")
    assert arbitrary.value.code == "delivery_target_invalid"

    document = tmp_path / binding["document"]["path"]
    # The binding path is attempt-relative, so resolve through the canonical root.
    document = tmp_path / ".taiji/expert-team-deliveries/run-standalone/delivery/attempt-1" / binding["document"]["path"]
    outside = tmp_path / "outside.docx"
    outside.write_bytes(document.read_bytes())
    document.unlink()
    document.symlink_to(outside)
    with pytest.raises(StandaloneDeliveryError) as linked:
        resolve_standalone_open_target(tmp_path, run, "document")
    assert linked.value.code == "delivery_path_symlink"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("stage_attempt", "not-an-integer"),
        ("delivery_attempt", "not-an-integer"),
        ("stage_attempt", "1"),
        ("delivery_attempt", True),
    ),
)
def test_malformed_current_delivery_ref_fails_with_typed_contract_error(tmp_path, field, value):
    from api.expert_teams.standalone_delivery import StandaloneDeliveryError, validate_standalone_delivery_context

    binding, _inputs = _build_standalone_binding(tmp_path)
    run = _current_run(tmp_path, binding)
    run["current_delivery_manifest_ref"][field] = value
    with pytest.raises(StandaloneDeliveryError) as error:
        validate_standalone_delivery_context(tmp_path, run)
    assert error.value.code in {"delivery_manifest_ref_invalid", "delivery_attempt_stale"}


@pytest.mark.parametrize("field", ("delivery_attempt", "document_revision"))
def test_malformed_delivery_reservation_rejects_boolean_integer_aliases(tmp_path, field):
    from api.expert_teams.standalone_delivery import StandaloneDeliveryError, validate_standalone_delivery_context

    binding, _inputs = _build_standalone_binding(tmp_path)
    run = _current_run(tmp_path, binding)
    run["current_delivery_attempt_reservation"][field] = True
    run["delivery_attempt_reservations"] = [dict(run["current_delivery_attempt_reservation"])]

    with pytest.raises(StandaloneDeliveryError) as error:
        validate_standalone_delivery_context(tmp_path, run)
    assert error.value.code == "delivery_reservation_stale"


def test_office_identity_refuses_standalone_binding(tmp_path):
    from api.expert_teams.delivery_integrity import DeliveryIntegrityError, office_binding_identity

    binding, _inputs = _build_standalone_binding(tmp_path)
    with pytest.raises(DeliveryIntegrityError, match="standalone"):
        office_binding_identity(
            tmp_path,
            {"run_id": "run-standalone", "stage_id": "delivery", "attempt": 1},
            binding,
        )


def test_standalone_binding_validator_converts_malformed_numbers_to_integrity_error(tmp_path):
    from api.expert_teams.delivery_integrity import DeliveryIntegrityError, validate_standalone_delivery_binding

    binding, _inputs = _build_standalone_binding(tmp_path)
    binding["stage_attempt"] = "not-an-integer"
    with pytest.raises(DeliveryIntegrityError, match="identity"):
        validate_standalone_delivery_binding(
            tmp_path,
            {"run_id": "run-standalone", "stage_id": "delivery", "attempt": 1},
            binding,
        )


@pytest.mark.parametrize("attempt", ("1", True))
def test_standalone_binding_validator_requires_typed_identity_attempt(tmp_path, attempt):
    from api.expert_teams.delivery_integrity import (
        DeliveryIntegrityError,
        validate_standalone_delivery_binding,
        validated_binding_for_identity,
    )

    binding, _inputs = _build_standalone_binding(tmp_path)
    with pytest.raises(DeliveryIntegrityError, match="identity"):
        validate_standalone_delivery_binding(
            tmp_path,
            {"run_id": "run-standalone", "stage_id": "delivery", "attempt": attempt},
            binding,
        )
    with pytest.raises(DeliveryIntegrityError, match="identity"):
        validated_binding_for_identity(
            tmp_path,
            {"run_id": "run-standalone", "stage_id": "delivery", "attempt": attempt},
        )


@pytest.mark.parametrize("mutation", ("status_drift", "failed_check", "blocking_issue", "unknown_field"))
def test_standalone_quality_report_passed_state_is_internally_consistent(tmp_path, mutation):
    from api.expert_teams.delivery_integrity import (
        DeliveryIntegrityError,
        sha256_file,
        validate_standalone_delivery_binding,
    )

    binding, inputs = _build_standalone_binding(tmp_path)
    quality_path = inputs["root"] / binding["standalone_quality_report"]["path"]
    report = json.loads(quality_path.read_text(encoding="utf-8"))
    if mutation == "status_drift":
        report["statuses"]["render"] = "failed"
    elif mutation == "failed_check":
        report["checks"][0]["status"] = "failed"
    elif mutation == "blocking_issue":
        report["issues"].append(
            {
                "issue_id": "ISS-BLOCK",
                "code": "file_integrity",
                "severity": "blocking",
                "target_id": "document",
                "owner": "document-renderer",
                "message": "文件完整性失败",
                "disposition": "unresolved",
                "completion_blocking": True,
            }
        )
    else:
        report["unexpected"] = True
    report_without_digest = dict(report)
    report_without_digest.pop("report_sha256", None)
    report["report_sha256"] = hashlib.sha256(
        json.dumps(
            report_without_digest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    _write_json(quality_path, report)
    binding["standalone_quality_report"]["sha256"] = sha256_file(quality_path)

    with pytest.raises(DeliveryIntegrityError, match="quality"):
        validate_standalone_delivery_binding(
            tmp_path,
            {"run_id": "run-standalone", "stage_id": "delivery", "attempt": 1},
            binding,
        )


def test_standalone_quality_writer_never_marks_a_failed_applicable_check_as_passed(tmp_path):
    from api.expert_teams.documents import write_standalone_quality_report
    from api.expert_teams.delivery_integrity import sha256_file

    inputs = _delivery_inputs(tmp_path)
    automatic = json.loads(json.dumps(inputs["quality"]))
    automatic["checks"][0]["status"] = "failed"
    report, _path = write_standalone_quality_report(
        inputs["root"],
        semantic_gates=inputs["semantic"],
        automatic_quality=automatic,
        document_sha256=sha256_file(inputs["document"]),
    )

    assert report["status"] == "blocked"


def test_standalone_semantic_gates_allow_disclosed_missing_data_but_enterprise_still_blocks(tmp_path):
    from api.expert_teams.documents import write_semantic_gates_snapshot

    brief = {
        "status": "confirmed",
        "exact_title": "部门月度工作汇报",
        "confirmed_revision": 1,
        "confirmed_sha256": HEX_B,
    }
    artifact = {
        "artifact_id": "polish:1",
        "sha256": HEX_A,
        "artifact_type": "reviewed_document",
        "deliverable_markdown": "# 部门月度工作汇报\n\n完成量：待补充（需人工确认）\n",
        "payload": {"review_report": {}, "open_issues": []},
    }
    standalone = write_semantic_gates_snapshot(
        tmp_path / "standalone",
        brief=brief,
        artifact=artifact,
        approved_inputs=[],
        product_mode="standalone",
    )
    enterprise = write_semantic_gates_snapshot(
        tmp_path / "enterprise",
        brief=brief,
        artifact=artifact,
        approved_inputs=[],
    )
    assert standalone["status"] == "passed"
    assert all(item["code"] != "placeholder_detected" for item in standalone["issues"])
    assert enterprise["status"] == "failed"
    assert any(item["code"] == "placeholder_detected" for item in enterprise["issues"])


@pytest.mark.parametrize("placeholder", ("TODO", "TBD", "XXX"))
def test_standalone_semantic_gates_still_block_undisclosed_engineering_placeholders(tmp_path, placeholder):
    from api.expert_teams.documents import write_semantic_gates_snapshot

    brief = {
        "status": "confirmed",
        "exact_title": "部门月度工作汇报",
        "confirmed_revision": 1,
        "confirmed_sha256": HEX_B,
    }
    artifact = {
        "artifact_id": "polish:1",
        "sha256": HEX_A,
        "artifact_type": "reviewed_document",
        "deliverable_markdown": f"# 部门月度工作汇报\n\n完成量：{placeholder}\n",
        "payload": {"review_report": {}, "open_issues": []},
    }
    report = write_semantic_gates_snapshot(
        tmp_path / placeholder,
        brief=brief,
        artifact=artifact,
        approved_inputs=[],
        product_mode="standalone",
    )
    assert report["status"] == "failed"
    assert any(item["code"] == "placeholder_detected" for item in report["issues"])


def test_system_stage_validates_v3_files_and_detects_standalone_quality_drift(tmp_path):
    from api.expert_teams.system_stages import SystemStageError, _validate_delivery_binding_files

    binding, inputs = _build_standalone_binding(tmp_path)
    request = {
        "session_id": "session-standalone",
        "run_id": "run-standalone",
        "stage_id": "delivery",
        "stage_attempt": 1,
        "canonical_document_ref": {
            "artifact_id": "polish:1",
            "sha256": HEX_A,
        },
    }
    reservation = {
        "delivery_attempt": 1,
        "document_revision": 1,
        "render_input_fingerprint": inputs["render_input"]["render_input_fingerprint"],
    }
    _validate_delivery_binding_files(
        inputs["root"],
        binding,
        request=request,
        delivery_reservation=reservation,
        template=inputs["template"],
        renderer=inputs["renderer"],
    )

    quality_path = inputs["root"] / binding["standalone_quality_report"]["path"]
    quality_path.write_bytes(quality_path.read_bytes() + b"drift")
    with pytest.raises(SystemStageError) as changed:
        _validate_delivery_binding_files(
            inputs["root"],
            binding,
            request=request,
            delivery_reservation=reservation,
            template=inputs["template"],
            renderer=inputs["renderer"],
        )
    assert changed.value.code == "delivery_binding_changed"


def test_system_stage_standalone_metadata_and_renderer_have_no_enterprise_identity(monkeypatch):
    from api import docx_engine_v2
    from api.expert_teams.system_stages import _document_metadata, _renderer_identities

    calls = []

    def renderer(profile_id):
        calls.append(profile_id)
        return {
            "name": "docx-engine-v2",
            "version": "2.0.0",
            "buildSha256": HEX_A,
            "profileId": profile_id,
            "profileSha256": HEX_B,
        }

    monkeypatch.setattr(docx_engine_v2, "describe_renderer_identity", renderer)
    snake, camel = _renderer_identities(product_mode="standalone")
    metadata = _document_metadata(
        {
            "exact_title": "部门月度工作汇报",
            "document_type": "work_report",
            "document_control": {
                "issuer": "某单位",
                "compiler": "某审批人",
                "classification": "internal",
                "version_label": "V1.0",
                "document_date": "2026-07-25",
            },
        },
        product_mode="standalone",
    )
    assert calls == ["standalone-default"]
    assert snake["profile_id"] == "standalone-default"
    assert camel["profileId"] == "standalone-default"
    assert metadata == {
        "title": "部门月度工作汇报",
        "documentType": "work_report",
        "versionLabel": "V1.0",
        "documentDate": "2026-07-25",
    }


def test_system_stage_standalone_metadata_omits_empty_optional_fields():
    from api.expert_teams.system_stages import _document_metadata

    assert _document_metadata(
        {
            "exact_title": "部门月度工作汇报",
            "document_type": "work_report",
            "document_control": {},
        },
        product_mode="standalone",
    ) == {
        "title": "部门月度工作汇报",
        "documentType": "work_report",
    }
