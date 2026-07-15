import json

import pytest


TITLE = "迎峰度夏保供电重点工作月度汇报"


def _brief():
    return {
        "schema_version": "document-brief/v1",
        "revision": 3,
        "status": "confirmed",
        "confirmed_revision": 3,
        "confirmed_sha256": "b" * 64,
        "exact_title": TITLE,
        "document_type": "work_report",
        "document_control": {
            "render_template_id": "enterprise-work-report",
            "client": "国家电网有限公司",
            "issuer": "办公室",
            "compiler": "北京太极信息系统技术有限公司",
            "version_label": "V1.0",
            "classification": "internal",
            "classification_label": "内部资料",
            "document_date": "2026-07-15",
        },
        "content_constraints": {
            "required_sections": ["工作开展情况"],
            "must_include": [],
            "must_avoid": [],
        },
    }


def _artifact(markdown=None, *, artifact_sha=None):
    from api.expert_teams.stage_artifacts import artifact_digest

    artifact = {
        "schema_version": "expert-stage-artifact/v1",
        "artifact_id": "polish:1",
        "artifact_type": "reviewed_document",
        "stage_id": "polish",
        "stage_attempt": 1,
        "brief_revision": 3,
        "brief_sha256": "b" * 64,
        "input_refs": [],
        "summary": "已复核正文",
        "payload": {
            "document_type": "work_report",
            "review_summary": "已完成复核",
            "resolved_issue_ids": [],
            "remaining_issue_ids": [],
        },
        "deliverable_markdown": markdown
        or f"# {TITLE}\r\n\r\n## 工作开展情况\r\n\r\n已完成重点任务。\r\n\r\n",
        "blocking_issues": [],
        "created_at": "2026-07-15T10:00:00+08:00",
        "validation_status": "valid",
    }
    artifact["sha256"] = artifact_sha or artifact_digest(artifact)
    return artifact


def _renderer(version="2.0.0"):
    return {
        "name": "docx-engine-v2",
        "version": version,
        "build_sha256": "1" * 64,
        "profile_id": "enterprise-default",
        "profile_sha256": "2" * 64,
    }


def _template():
    return {"id": "enterprise-work-report", "version": "1.0.0", "package_sha256": "3" * 64}


def test_canonical_snapshot_is_exact_normalized_artifact_projection(tmp_path):
    from api.expert_teams.documents import FinalDocumentDeliveryError, write_canonical_snapshot

    paths = write_canonical_snapshot(tmp_path, brief=_brief(), artifact=_artifact())

    assert paths["document"].read_bytes() == (
        f"# {TITLE}\n\n## 工作开展情况\n\n已完成重点任务。\n"
    ).encode("utf-8")
    assert json.loads(paths["artifact"].read_text(encoding="utf-8"))["sha256"] == _artifact()["sha256"]

    paths["document"].write_text("# 被篡改的标题\n", encoding="utf-8")
    with pytest.raises(FinalDocumentDeliveryError, match="canonical document"):
        write_canonical_snapshot(tmp_path, brief=_brief(), artifact=_artifact())


@pytest.mark.parametrize(
    "markdown,code",
    [
        ("# 错误标题\n\n正文。\n", "title_mismatch"),
        (f"# {TITLE}\n\n本阶段由负责专家完成 Stage 4 复核交付。\n", "workflow_text_leaked"),
        (f"# {TITLE}\n\n## 工作开展情况\n\n待补充。\n", "placeholder_detected"),
    ],
)
def test_semantic_gates_reject_title_drift_and_workflow_language(tmp_path, markdown, code):
    from api.expert_teams.documents import write_semantic_gates_snapshot

    artifact = _artifact(markdown)
    report = write_semantic_gates_snapshot(
        tmp_path,
        brief=_brief(),
        artifact=artifact,
        approved_inputs=[],
    )

    assert report["status"] == "failed"
    assert code in {issue["code"] for issue in report["issues"]}


def test_semantic_gates_reject_document_type_drift_and_unsupported_claims(tmp_path):
    from api.expert_teams.documents import write_semantic_gates_snapshot

    brief = _brief()
    artifact = _artifact()
    artifact["payload"] = {
        "document_type": "research_report",
        "review_report": {"unsupported_claim_ids": ["claim-9"]},
    }
    report = write_semantic_gates_snapshot(
        tmp_path,
        brief=brief,
        artifact=artifact,
        approved_inputs=[],
    )

    issues = {item["code"]: item for item in report["issues"]}
    assert report["semantic_status"] == "failed"
    assert report["evidence_status"] == "failed"
    assert issues["document_type_mismatch"]["issue_id"].startswith("semantic:document_type_mismatch:")
    assert issues["unsupported_claim"]["target_id"] == "claim:claim-9"


def test_layered_quality_report_has_seven_independent_hash_bound_statuses(tmp_path):
    from api.expert_teams.documents import write_layered_quality_report

    report, path = write_layered_quality_report(
        tmp_path,
        semantic_gates={
            "brief_status": "passed",
            "semantic_status": "passed",
            "evidence_status": "passed",
            "issues": [],
        },
        automatic_quality={
            "schemaVersion": "docx-engine-v2/automatic-quality-v1",
            "assetStatus": "passed",
            "renderStatus": "passed_with_warnings",
            "issues": [{
                "issueId": "automatic:render:template_markers:123456789abc",
                "code": "template_markers",
                "severity": "warning",
                "completionBlocking": True,
                "message": "marker warning",
            }],
        },
    )

    assert path.is_file()
    assert report["statuses"] == {
        "brief": "passed",
        "semantic": "passed",
        "evidence": "passed",
        "asset": "passed",
        "render": "passed_with_warnings",
        "office": "pending",
        "delivery": "pending",
    }
    assert report["status"] == "blocked"
    assert report["report_sha256"] == __import__("hashlib").sha256(
        json.dumps({key: value for key, value in report.items() if key != "report_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_render_binding_contains_pre_office_identity_and_no_future_acceptance(tmp_path):
    from api.expert_teams.documents import (
        FinalDocumentDeliveryError,
        build_delivery_binding_v2,
        build_render_input_binding,
        write_canonical_snapshot,
        write_semantic_gates_snapshot,
    )

    brief, artifact = _brief(), _artifact()
    paths = write_canonical_snapshot(tmp_path, brief=brief, artifact=artifact)
    gates = write_semantic_gates_snapshot(tmp_path, brief=brief, artifact=artifact, approved_inputs=[])
    assets = tmp_path / "assets" / "asset-manifest.json"
    assets.parent.mkdir(parents=True)
    assets.write_text('{"schema_version":"expert-asset-manifest/v1","assets":[]}\n', encoding="utf-8")
    document = tmp_path / "delivery" / "document.docx"
    quality = tmp_path / "delivery" / "quality-report.json"
    document.parent.mkdir(parents=True)
    document.write_bytes(b"PK\x03\x04canonical-docx")
    quality.write_text(json.dumps({
        "status": "passed",
        "automaticQuality": {
            "schemaVersion": "docx-engine-v2/automatic-quality-v1",
            "assetStatus": "passed",
            "renderStatus": "passed",
            "issues": [],
        },
    }) + "\n", encoding="utf-8")
    render_input = build_render_input_binding(
        brief=brief,
        artifact=artifact,
        canonical_document_path=paths["document"],
        asset_manifest_path=assets,
        semantic_gates_path=tmp_path / "reviews" / "semantic-gates.json",
        template=_template(),
        renderer=_renderer(),
    )
    binding = build_delivery_binding_v2(
        tmp_path,
        session_id="session-1",
        run_id="run-1",
        stage_id="delivery",
        stage_attempt=4,
        delivery_attempt=2,
        document_revision=1,
        brief=brief,
        artifact=artifact,
        assets=assets,
        semantic_gates=gates,
        template=_template(),
        renderer=_renderer(),
        render_input_fingerprint=render_input["render_input_fingerprint"],
        document=document,
        quality=quality,
    )

    assert binding["schema_version"] == "expert-delivery-binding/v2"
    assert binding["session_id"] == "session-1"
    assert binding["stage_attempt"] == 4
    assert binding["delivery_attempt"] == 2
    assert binding["document_revision"] == 1
    assert binding["layered_quality_report"]["path"] == "reviews/enterprise-quality-report.json"
    assert "acceptance_sha256" not in json.dumps(binding)

    changed_renderer = _renderer("2.0.1")
    with pytest.raises(FinalDocumentDeliveryError, match="fingerprint"):
        build_delivery_binding_v2(
            tmp_path,
            session_id="session-1",
                run_id="run-1",
                stage_id="delivery",
                stage_attempt=4,
                delivery_attempt=2,
            document_revision=1,
            brief=brief,
            artifact=artifact,
            assets=assets,
            semantic_gates=gates,
            template=_template(),
            renderer=changed_renderer,
            render_input_fingerprint=render_input["render_input_fingerprint"],
            document=document,
            quality=quality,
        )


def test_delivery_allocator_is_idempotent_but_never_resurrects_superseded_attempt():
    from api.expert_teams.runtime import _reserve_document_revision_and_delivery_attempt_in_run

    run = {"run_id": "run-1"}
    canonical_a = {"artifact_id": "polish:1", "sha256": "a" * 64}
    canonical_b = {"artifact_id": "polish:2", "sha256": "b" * 64}
    run, first, created = _reserve_document_revision_and_delivery_attempt_in_run(
        run, canonical_ref=canonical_a, render_input_fingerprint="1" * 64, idempotency_key="lineage-a1"
    )
    run, replay, replay_created = _reserve_document_revision_and_delivery_attempt_in_run(
        run, canonical_ref=canonical_a, render_input_fingerprint="1" * 64, idempotency_key="lineage-a1"
    )
    assert created is True and replay_created is False and replay == first

    run["delivery_attempt_reservations"][-1]["status"] = "superseded"
    run, second, _ = _reserve_document_revision_and_delivery_attempt_in_run(
        run, canonical_ref=canonical_b, render_input_fingerprint="2" * 64, idempotency_key="lineage-b"
    )
    run["delivery_attempt_reservations"][-1]["status"] = "superseded"
    run, third, _ = _reserve_document_revision_and_delivery_attempt_in_run(
        run, canonical_ref=canonical_a, render_input_fingerprint="1" * 64, idempotency_key="lineage-a2"
    )

    assert [first["delivery_attempt"], second["delivery_attempt"], third["delivery_attempt"]] == [1, 2, 3]
    assert [first["document_revision"], second["document_revision"], third["document_revision"]] == [1, 2, 3]
    assert [item["status"] for item in run["delivery_attempt_reservations"]] == [
        "superseded", "superseded", "reserved"
    ]
    assert run["current_delivery_manifest_ref"] is None


def test_prepare_delivery_inputs_uses_only_canonical_artifact_not_raw_output(tmp_path):
    from api.expert_teams.documents import prepare_canonical_delivery_inputs

    artifact = _artifact()
    run = {
        "run_id": "run-canonical-only",
        "session_id": "session-canonical-only",
        "document_brief": _brief(),
        "canonical_document_ref": {"artifact_id": artifact["artifact_id"], "sha256": artifact["sha256"]},
        "approved_stage_artifact_refs": {
            artifact["stage_id"]: {"artifact_id": artifact["artifact_id"], "sha256": artifact["sha256"]}
        },
        "stage_artifacts": [artifact],
        "stage_outputs": [{"content": "# 最后一条聊天消息绝不能成为正文\n"}],
        "messages": [{"role": "assistant", "content": "CANARY-LAST-MESSAGE"}],
    }

    prepared = prepare_canonical_delivery_inputs(
        tmp_path,
        run,
        stage_id="delivery",
        delivery_attempt=1,
    )

    document = prepared["paths"]["document"].read_text(encoding="utf-8")
    assert document == _artifact()["deliverable_markdown"].replace("\r\n", "\n").rstrip("\n") + "\n"
    assert "CANARY-LAST-MESSAGE" not in document
    assert "最后一条聊天消息" not in document


def test_legacy_binding_is_never_upgraded_to_enterprise_verified():
    from api.expert_teams.delivery_integrity import classify_delivery_binding

    assert classify_delivery_binding({"schema_version": 1}) == "legacy_unverified"
    assert classify_delivery_binding({"schema_version": "expert-delivery-binding/v2"}) == "enterprise_pre_office"
