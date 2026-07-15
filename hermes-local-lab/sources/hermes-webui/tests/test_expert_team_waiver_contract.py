import json

import pytest


def _binding():
    return {
        "schema_version": "expert-delivery-binding/v2",
        "run_id": "run-waiver",
        "session_id": "sid-waiver",
        "stage_id": "delivery",
        "delivery_attempt": 2,
    }


def _acceptance(*, severity="condition", domain="office_issue"):
    return {
        "schema_version": "office-acceptance/v2",
        "review_id": "review-1",
        "delivery_binding_sha256": "a" * 64,
        "decision": "passed_with_conditions",
        "validity": "active",
        "reviewer": {"principal_id": "reviewer-1", "identity_snapshot_sha256": "b" * 64},
        "issues": [
            {
                "issue_id": "office-issue-1",
                "severity": severity,
                "target_domain": domain,
                "category": "visual_alignment",
                "page": 3,
                "description": "第三页表格在 WPS 中需要保留人工确认。",
                "expected_fix": "经授权保留",
            }
        ],
    }


def _authorizer(subject="authorizer-1"):
    return {
        "subject": subject,
        "display_name": "授权人",
        "roles": ["waiver-authorizer"],
        "auth_method": "oidc_pkce",
        "identity_snapshot_sha256": "c" * 64,
    }


def test_office_condition_waiver_is_separate_hash_bound_and_idempotent(tmp_path):
    from api.expert_teams.waivers import create_office_waiver

    first = create_office_waiver(
        tmp_path,
        binding=_binding(),
        binding_sha256="a" * 64,
        acceptance=_acceptance(),
        acceptance_sha256="d" * 64,
        issue_id="office-issue-1",
        authorizer=_authorizer(),
        idempotency_key="waiver-1",
        now="2026-07-15T12:00:00+08:00",
    )
    second = create_office_waiver(
        tmp_path,
        binding=_binding(),
        binding_sha256="a" * 64,
        acceptance=_acceptance(),
        acceptance_sha256="d" * 64,
        issue_id="office-issue-1",
        authorizer=_authorizer(),
        idempotency_key="waiver-1",
        now="2026-07-15T12:00:00+08:00",
    )

    assert first == second
    assert first["schema_version"] == "expert-waiver/v1"
    assert first["delivery_binding_sha256"] == "a" * 64
    assert first["acceptance_sha256"] == "d" * 64
    assert first["target_domain"] == "office_issue"
    assert first["target_id"] == "office-issue-1"
    assert first["target"] == {"page": 3}
    serialized = json.dumps(first, ensure_ascii=False)
    assert "review_token" not in serialized and "bearer" not in serialized


@pytest.mark.parametrize(
    ("acceptance,authorizer,code"),
    [
        (_acceptance(severity="blocking"), _authorizer(), "waiver_severity_not_allowed"),
        (_acceptance(domain="semantic_report"), _authorizer(), "waiver_target_not_released"),
        (_acceptance(), _authorizer("reviewer-1"), "authorizer_handoff_required"),
    ],
)
def test_waiver_rejects_blocking_non_office_and_same_person(tmp_path, acceptance, authorizer, code):
    from api.expert_teams.waivers import WaiverError, create_office_waiver

    with pytest.raises(WaiverError) as error:
        create_office_waiver(
            tmp_path,
            binding=_binding(),
            binding_sha256="a" * 64,
            acceptance=acceptance,
            acceptance_sha256="d" * 64,
            issue_id="office-issue-1",
            authorizer=authorizer,
            idempotency_key="waiver-rejected",
            now="2026-07-15T12:00:00+08:00",
        )
    assert error.value.code == code


def test_office_acceptance_requires_exact_checklist_identity_and_issue_severity():
    from api.expert_teams.office_review import build_office_acceptance

    binding = {
        "schema_version": "expert-office-binding/v1",
        "run_id": "run-1", "session_id": "sid-1", "stage_id": "delivery", "attempt": 1,
        "document_sha256": "1" * 64, "delivery_binding_sha256": "2" * 64,
        "brief": {"revision": 1, "sha256": "3" * 64},
        "canonical_artifact": {"artifact_id": "polish:1", "sha256": "4" * 64},
        "template": {"id": "enterprise-work-report", "version": "1", "package_sha256": "5" * 64},
        "renderer": {"name": "docx-engine-v2", "version": "1", "build_sha256": "6" * 64, "profile_id": "enterprise-default", "profile_sha256": "7" * 64},
    }
    token = {
        "token_hash": "8" * 64,
        "reviewer_identity": {
            "subject": "reviewer-1", "display_name": "复核人甲", "role": "document-reviewer",
            "auth_method": "oidc_pkce", "identity_snapshot_sha256": "9" * 64,
        },
        "opened_at": "2026-07-15T11:00:00+08:00",
    }
    checks = {key: "passed" for key in ("document_opened", "layout_reviewed", "content_order_reviewed")}
    acceptance = build_office_acceptance(
        binding=binding, token_state=token, status="passed", checklist=checks,
        issues=[], evidence=[{"path": "evidence/wps-visual/page-1.png", "sha256": "a" * 64}],
        note="已在 WPS 逐页检查目录、版式与分页。", now="2026-07-15T11:10:00+08:00",
    )
    assert acceptance["schema_version"] == "office-acceptance/v2"
    assert acceptance["decision"] == "passed"
    assert acceptance["validity"] == "active"
    assert acceptance["delivery_binding_sha256"] == "2" * 64
    assert acceptance["reviewer"]["principal_id"] == "reviewer-1"
    assert acceptance["template"] == binding["template"]

    with pytest.raises(ValueError, match="severity"):
        build_office_acceptance(
            binding=binding, token_state=token, status="passed_with_conditions", checklist=checks,
            issues=[{"issue_id": "i-1", "severity": "Condition", "target": {"domain": "office", "page": 1}, "message": "x"}],
            evidence=[{"path": "evidence/wps-visual/page-1.png", "sha256": "a" * 64}], note="已检查", now="2026-07-15T11:10:00+08:00",
        )

    blocking_downgrade = {
        "issue_id": "i-blocking", "severity": "condition", "category": "placeholder_content",
        "page": 1, "description": "发现流程占位话术", "expected_fix": "删除占位内容",
    }
    with pytest.raises(ValueError, match="policy"):
        build_office_acceptance(
            binding=binding, token_state=token, status="passed_with_conditions", checklist=checks,
            issues=[blocking_downgrade], evidence=[{"path": "evidence/wps-visual/page-1.png", "sha256": "a" * 64}],
            note="已检查", now="2026-07-15T11:10:00+08:00",
        )

    allowed_condition = {
        "issue_id": "i-condition", "severity": "condition", "category": "visual_alignment",
        "page": 1, "description": "表格对齐略有差异", "expected_fix": "经授权保留",
    }
    conditioned = build_office_acceptance(
        binding=binding, token_state=token, status="passed_with_conditions", checklist=checks,
        issues=[allowed_condition], evidence=[{"path": "evidence/wps-visual/page-1.png", "sha256": "a" * 64}],
        note="已检查", now="2026-07-15T11:10:00+08:00",
    )
    assert conditioned["issues"] == [allowed_condition]
