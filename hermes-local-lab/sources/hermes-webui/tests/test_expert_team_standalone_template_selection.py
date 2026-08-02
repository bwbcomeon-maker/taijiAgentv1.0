from __future__ import annotations

import pytest


def _payload(*, product_mode: str | None, template_id: str) -> dict:
    payload = {
        "contract_version": "expert-team-contract/v1",
        "document_type": "work_report",
        "prompt": "起草月度工作汇报",
        "document_brief_seed": {
            "task_mode": "create",
            "document_control": {"render_template_id": template_id},
        },
    }
    if product_mode is not None:
        payload["product_mode"] = product_mode
    return payload


def test_standalone_brief_accepts_only_the_standalone_template():
    from api.expert_teams.contracts import build_document_brief

    brief = build_document_brief(
        "content-creator-team",
        _payload(product_mode="standalone", template_id="standalone-work-report"),
        now="2026-07-25T10:00:00+08:00",
    )

    assert brief["product_mode"] == "standalone"
    assert brief["document_control"]["render_template_id"] == "standalone-work-report"


def test_standalone_brief_rejects_an_enterprise_template():
    from api.expert_teams.contracts import ContractError, build_document_brief

    with pytest.raises(ContractError) as error:
        build_document_brief(
            "content-creator-team",
            _payload(product_mode="standalone", template_id="enterprise-work-report"),
            now="2026-07-25T10:00:00+08:00",
        )

    assert error.value.code == "render_template_mismatch"


def test_enterprise_brief_cannot_select_a_standalone_template():
    from api.expert_teams.contracts import ContractError, build_document_brief

    with pytest.raises(ContractError) as error:
        build_document_brief(
            "content-creator-team",
            _payload(product_mode=None, template_id="standalone-work-report"),
            now="2026-07-25T10:00:00+08:00",
        )

    assert error.value.code == "render_template_mismatch"
