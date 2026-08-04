from copy import deepcopy
import hashlib

import pytest


def _v2_brief() -> dict:
    return {
        "schema_version": "document-brief/v1",
        "status": "confirmed",
        "revision": 1,
        "confirmed_revision": 1,
        "confirmed_sha256": "b" * 64,
        "exact_title": "企业本地优先 AI 助理落地研究报告",
        "document_type": "research_report",
        "task_mode": "create",
        "source_policy": {
            "mode": "automatic_fallback",
            "as_of_date": "2026-08-03",
            "citation_style": "source_id",
        },
        "content_constraints": {
            "required_sections": ["研究问题", "证据", "分析", "结论边界", "引用"],
            "must_include": [],
            "must_avoid": [],
        },
    }


def _v1_brief() -> dict:
    result = _v2_brief()
    result["source_policy"] = {
        "mode": "provided_only",
        "as_of_date": "2026-08-03",
        "citation_style": "source_id",
    }
    return result


def _snapshot(*, cutoff: str | None = None) -> dict:
    public_text = "公开资料支持该结论。"
    internal_text = "内部资料支持该结论。"
    sources = [
        {
            "source_id": "PUB-001",
            "kind": "approved_public",
            "label": "公开资料",
            "locator": "https://example.test/public",
            "source_sha256": "1" * 64,
            "content_sha256": hashlib.sha256(public_text.encode()).hexdigest(),
            "content_text": public_text,
            "segments": [
                {
                    "segment_id": "PUB-001:S0001",
                    "locator": "chars:0-12",
                    "char_start": 0,
                    "char_end": len(public_text),
                    "text": public_text,
                    "text_sha256": hashlib.sha256(public_text.encode()).hexdigest(),
                }
            ],
        },
        {
            "source_id": "INT-001",
            "kind": "approved_internal",
            "label": "内部知识资料",
            "locator": "kb://workspace/internal",
            "source_sha256": "4" * 64,
            "content_sha256": hashlib.sha256(internal_text.encode()).hexdigest(),
            "content_text": internal_text,
            "segments": [
                {
                    "segment_id": "INT-001:S0001",
                    "locator": "chars:0-12",
                    "char_start": 0,
                    "char_end": len(internal_text),
                    "text": internal_text,
                    "text_sha256": hashlib.sha256(internal_text.encode()).hexdigest(),
                }
            ],
        },
    ]
    result = {
        "schema_version": "expert-source-context/v1",
        "snapshot_id": "source-context:model-knowledge",
        "snapshot_sha256": "c" * 64,
        "sources": sources,
    }
    if cutoff is not None:
        result["trusted_provider_metadata"] = {
            "knowledge_cutoff_date": cutoff,
        }
    return result


def _claim(
    *,
    claim_id: str = "CLAIM-001",
    origin_tier: str | None = None,
    source_id: str | None = "PUB-001",
    status: str = "verified",
    statement: str = "现有资料支持该结论。",
) -> dict:
    evidence = []
    if source_id:
        evidence = [
            {
                "source_id": source_id,
                "segment_id": f"{source_id}:S0001",
                "relationship": "supports",
            }
        ]
    result = {
        "claim_id": claim_id,
        "statement": statement,
        "claim_type": "fact",
        "evidence": evidence,
        "status": status,
        "confidence": "medium",
        "notes": "仅在当前证据边界内使用。",
    }
    if origin_tier is not None:
        result["origin_tier"] = origin_tier
    return result


def _build_evidence(claims: list[dict], *, brief: dict | None = None, snapshot: dict | None = None):
    from api.expert_teams.stage_artifacts import build_stage_artifact

    source_snapshot = snapshot or _snapshot()
    parsed = {
        "artifact_type": "evidence_matrix",
        "summary": "证据矩阵",
        "payload": {"claims": claims, "contradictions": [], "gaps": []},
        "blocking_issues": [],
        "deliverable_markdown": None,
    }
    return build_stage_artifact(
        parsed,
        stage_id="evidence",
        stage_attempt=1,
        brief=brief or _v2_brief(),
        input_refs=[
            {
                "ref_type": "source_context",
                "snapshot_id": source_snapshot["snapshot_id"],
                "sha256": source_snapshot["snapshot_sha256"],
            }
        ],
        source_snapshot=source_snapshot,
        now="2026-08-03T10:00:00+08:00",
    )


def test_v2_requires_origin_tier_while_v1_remains_compatible():
    v1_artifact = _build_evidence([_claim()], brief=_v1_brief())
    assert "origin_tier" not in v1_artifact["payload"]["claims"][0]

    from api.expert_teams.stage_artifacts import StageArtifactError

    with pytest.raises(StageArtifactError) as error:
        _build_evidence([_claim()])
    assert error.value.code == "required_field_missing"
    assert error.value.field.endswith(".origin_tier")


@pytest.mark.parametrize(
    ("origin_tier", "source_id"),
    [("public_web", "INT-001"), ("local_knowledge", "PUB-001")],
)
def test_v2_source_backed_claims_must_match_their_origin_tier(origin_tier, source_id):
    from api.expert_teams.stage_artifacts import StageArtifactError

    with pytest.raises(StageArtifactError) as error:
        _build_evidence([_claim(origin_tier=origin_tier, source_id=source_id)])
    assert error.value.code == "origin_tier_source_mismatch"


@pytest.mark.parametrize(
    ("source_id", "status"),
    [("PUB-001", "insufficient"), (None, "verified")],
)
def test_model_knowledge_claims_are_unverified_and_citation_free(source_id, status):
    from api.expert_teams.stage_artifacts import StageArtifactError

    with pytest.raises(StageArtifactError) as error:
        _build_evidence(
            [
                _claim(
                    origin_tier="model_knowledge",
                    source_id=source_id,
                    status=status,
                )
            ]
        )
    assert error.value.code == "model_knowledge_must_be_unverified"


@pytest.mark.parametrize(
    ("cutoff", "expected_label"),
    [
        ("2025-06-01", "模型知识截止日期：2025-06-01"),
        (None, "模型知识时效未知"),
    ],
)
def test_model_knowledge_time_basis_only_comes_from_trusted_provider_metadata(
    cutoff,
    expected_label,
):
    artifact = _build_evidence(
        [
            _claim(
                origin_tier="model_knowledge",
                source_id=None,
                status="insufficient",
            )
        ],
        snapshot=_snapshot(cutoff=cutoff),
    )
    assert artifact["payload"]["model_knowledge_time_basis"] == {
        "cutoff_date": cutoff,
        "label": expected_label,
    }


@pytest.mark.parametrize(
    ("provider_metadata", "expected_cutoff"),
    [
        ({"knowledge_cutoff_date": "2025-06-01"}, "2025-06-01"),
        ({"knowledge_cutoff_date": "2025/06/01"}, None),
        (None, None),
    ],
)
def test_production_source_snapshot_freezes_trusted_provider_cutoff(
    tmp_path, provider_metadata, expected_cutoff
):
    from api.expert_teams.source_context import (
        build_source_context_snapshot,
        read_source_context_snapshot,
    )
    from api.runtime_adapter import build_strict_provider_context

    provider_context = build_strict_provider_context(
        provider="openai",
        model="gpt-runtime",
        api_mode="chat_completions",
        provider_metadata=provider_metadata,
    )
    brief = _v2_brief()
    snapshot_ref = build_source_context_snapshot(
        tmp_path,
        "et-provider-cutoff",
        brief,
        {},
        brief_sha256=brief["confirmed_sha256"],
        brief_revision=brief["confirmed_revision"],
        allow_empty=True,
        trusted_provider_context=provider_context,
    )
    snapshot = read_source_context_snapshot(
        tmp_path,
        "et-provider-cutoff",
        snapshot_ref,
    )

    assert snapshot["trusted_provider_metadata"] == {
        "knowledge_cutoff_date": expected_cutoff,
    }
    artifact = _build_evidence(
        [
            _claim(
                origin_tier="model_knowledge",
                source_id=None,
                status="insufficient",
            )
        ],
        snapshot=snapshot,
    )
    assert artifact["payload"]["model_knowledge_time_basis"]["cutoff_date"] == expected_cutoff


def test_model_payload_cannot_override_unknown_trusted_cutoff():
    from api.expert_teams.stage_artifacts import build_stage_artifact

    source_snapshot = _snapshot()
    parsed = {
        "artifact_type": "evidence_matrix",
        "summary": "证据矩阵",
        "payload": {
            "claims": [
                _claim(
                    origin_tier="model_knowledge",
                    source_id=None,
                    status="insufficient",
                )
            ],
            "contradictions": [],
            "gaps": [],
            "model_knowledge_time_basis": {
                "cutoff_date": "2099-12-31",
                "label": "模型自称时效",
            },
        },
        "blocking_issues": [],
        "deliverable_markdown": None,
    }

    artifact = build_stage_artifact(
        parsed,
        stage_id="evidence",
        stage_attempt=1,
        brief=_v2_brief(),
        input_refs=[
            {
                "ref_type": "source_context",
                "snapshot_id": source_snapshot["snapshot_id"],
                "sha256": source_snapshot["snapshot_sha256"],
            }
        ],
        source_snapshot=source_snapshot,
        now="2026-08-03T10:00:00+08:00",
    )
    assert artifact["payload"]["model_knowledge_time_basis"] == {
        "cutoff_date": None,
        "label": "模型知识时效未知",
    }


def test_model_cannot_self_declare_a_knowledge_cutoff():
    from api.expert_teams.stage_artifacts import StageArtifactError

    claim = _claim(
        origin_tier="model_knowledge",
        source_id=None,
        status="insufficient",
    )
    claim["knowledge_cutoff_date"] = "2026-08-03"
    with pytest.raises(StageArtifactError) as error:
        _build_evidence([claim])
    assert error.value.code == "unknown_field"
    assert error.value.field.endswith(".knowledge_cutoff_date")


def test_freshness_sensitive_model_claims_must_state_that_they_cannot_be_verified():
    from api.expert_teams.stage_artifacts import StageArtifactError

    with pytest.raises(StageArtifactError) as error:
        _build_evidence(
            [
                _claim(
                    origin_tier="model_knowledge",
                    source_id=None,
                    status="insufficient",
                    statement="2026 年最新政策已经要求企业完成全面改造。",
                )
            ]
        )
    assert error.value.code == "time_sensitive_model_claim_unverifiable"

    artifact = _build_evidence(
        [
            _claim(
                origin_tier="model_knowledge",
                source_id=None,
                status="insufficient",
                statement="无法外部核验 2026 年最新政策，因此不能确认当前要求。",
            )
        ]
    )
    assert artifact["validation_status"] == "valid"


@pytest.mark.parametrize(
    "statement",
    [
        "Current regulation requires immediate compliance.",
        "The latest price is 99 dollars.",
        "Today's statistics show rapid growth.",
        "Recent market statistics confirm the result.",
        "现行法规要求立即完成整改。",
        "当前价格已上涨至 99 元。",
    ],
)
def test_bilingual_freshness_sensitive_model_claims_fail_closed(statement):
    from api.expert_teams.stage_artifacts import StageArtifactError

    with pytest.raises(StageArtifactError) as error:
        _build_evidence(
            [
                _claim(
                    origin_tier="model_knowledge",
                    source_id=None,
                    status="insufficient",
                    statement=statement,
                )
            ]
        )
    assert error.value.code == "time_sensitive_model_claim_unverifiable"


@pytest.mark.parametrize(
    "statement",
    [
        "As of 2026, market share is 42 percent.",
        "This year's statistics show rapid growth.",
        "The current year price is 99 dollars.",
        "目前市场份额已达到 42%。",
        "本月价格已上涨至 99 元。",
        "本季度统计数据显示需求增长。",
        "今年现行法规要求立即完成整改。",
    ],
)
def test_extended_freshness_phrases_fail_closed_for_model_knowledge(statement):
    from api.expert_teams.stage_artifacts import StageArtifactError

    with pytest.raises(StageArtifactError) as error:
        _build_evidence(
            [
                _claim(
                    origin_tier="model_knowledge",
                    source_id=None,
                    status="insufficient",
                    statement=statement,
                )
            ]
        )
    assert error.value.code == "time_sensitive_model_claim_unverifiable"


@pytest.mark.parametrize("origin_tier", ["public_web", "local_knowledge"])
def test_source_backed_claims_require_evidence_even_when_not_verified(origin_tier):
    from api.expert_teams.stage_artifacts import StageArtifactError

    with pytest.raises(StageArtifactError) as error:
        _build_evidence(
            [
                _claim(
                    origin_tier=origin_tier,
                    source_id=None,
                    status="insufficient",
                )
            ]
        )
    assert error.value.code == "source_backed_claim_requires_evidence"


def test_v2_prompts_define_model_knowledge_boundary_and_citation_isolation():
    from api.expert_teams.prompts import _system_message

    for artifact_type in ("evidence_matrix", "research_document_draft"):
        prompt = _system_message(artifact_type, _v2_brief())
        assert "模型知识·未核验" in prompt
        assert "不得借用相邻来源" in prompt
        assert "模型知识时效未知" in prompt
        assert "最新政策" in prompt
        assert "无法核验" in prompt
    assert "origin_tier" in _system_message("evidence_matrix", _v2_brief())
    v1_prompt = _system_message("evidence_matrix", _v1_brief())
    assert "origin_tier" not in v1_prompt
    assert "模型知识·未核验" not in v1_prompt


def test_v2_document_contract_allows_empty_marker_but_v1_stays_strict():
    from api.expert_teams.stage_artifacts import StageArtifactError, build_stage_artifact

    payload = {
        "title": _v2_brief()["exact_title"],
        "section_map": [
            {"section_id": f"SEC-{index}", "heading": heading}
            for index, heading in enumerate(
                _v2_brief()["content_constraints"]["required_sections"],
                start=1,
            )
        ],
        "claim_usage": [
            {
                "claim_id": "CLAIM-MODEL",
                "section_id": "SEC-2",
                "citation_marker": "",
            }
        ],
        "open_issues": [],
    }
    markdown = (
        f"# {_v2_brief()['exact_title']}\n\n"
        "## 研究问题\n\n如何落地。\n\n"
        "## 证据\n\n模型知识·未核验。\n\n"
        "## 分析\n\n仅作方法分析。\n\n"
        "## 结论边界\n\n不作外部事实使用。\n\n"
        "## 引用\n\n无可核验外部来源。\n"
    )
    parsed = {
        "artifact_type": "research_document_draft",
        "summary": "研究报告初稿",
        "payload": payload,
        "blocking_issues": [],
        "deliverable_markdown": markdown,
    }
    artifact = build_stage_artifact(
        parsed,
        stage_id="draft",
        stage_attempt=1,
        brief=_v2_brief(),
        input_refs=[],
        now="2026-08-03T10:00:00+08:00",
    )
    assert artifact["validation_status"] == "valid"

    with pytest.raises(StageArtifactError) as error:
        build_stage_artifact(
            parsed,
            stage_id="draft",
            stage_attempt=1,
            brief=_v1_brief(),
            input_refs=[],
            now="2026-08-03T10:00:00+08:00",
        )
    assert error.value.code == "invalid_type"
    assert error.value.field.endswith(".citation_marker")


def _review_checks() -> dict:
    return {
        key: "passed"
        for key in (
            "brief_alignment",
            "citation_completeness",
            "unsupported_claims",
            "unresolved_contradictions",
            "as_of_date_compliance",
            "document_purity",
            "confidentiality",
        )
    }


def _approved_research_inputs(
    claims: list[dict], *, outline_sections: list[dict] | None = None
) -> list[dict]:
    return [
        {
            "artifact_id": "evidence:1",
            "sha256": "1" * 64,
            "artifact_type": "evidence_matrix",
            "input_refs": [
                {
                    "ref_type": "source_context",
                    "snapshot_id": "source-context:model-knowledge",
                    "sha256": "c" * 64,
                }
            ],
            "payload": {"claims": claims},
        },
        {
            "artifact_id": "outline:1",
            "sha256": "2" * 64,
            "artifact_type": "research_outline",
            "payload": {
                "sections": outline_sections
                or [
                    {
                        "section_id": "SEC-EVIDENCE",
                        "heading": "证据",
                        "claim_ids": [claim["claim_id"] for claim in claims],
                    }
                ]
            },
        },
    ]


def _research_artifact(usages: list[dict], *, evidence_text: str, references: str) -> dict:
    return {
        "artifact_id": "review:1",
        "sha256": "e" * 64,
        "artifact_type": "reviewed_research_document",
        "input_refs": [
            {"ref_type": "stage_artifact", "artifact_id": "evidence:1", "sha256": "1" * 64},
            {"ref_type": "stage_artifact", "artifact_id": "outline:1", "sha256": "2" * 64},
        ],
        "payload": {
            "document_type": "research_report",
            "claim_usage": usages,
            "review_report": {
                "checks": _review_checks(),
                "issues": [],
                "unsupported_claim_ids": [],
                "unresolved_contradiction_ids": [],
                "unresolved_issue_ids": [],
            },
        },
        "deliverable_markdown": (
            "# 企业本地优先 AI 助理落地研究报告\n\n"
            "## 研究问题\n\n如何落地本地优先 AI 助理。\n\n"
            f"## 证据\n\n{evidence_text}\n\n"
            "## 分析\n\n在证据边界内进行分析。\n\n"
            "## 结论边界\n\n模型知识不作为已外部核验事实。\n\n"
            f"## 引用\n\n{references}\n"
        ),
        "blocking_issues": [],
    }


def _semantic_report(
    artifact: dict,
    claims: list[dict],
    *,
    sources: list[dict],
    outline_sections: list[dict] | None = None,
) -> dict:
    from api.expert_teams.documents import evaluate_semantic_gates

    return evaluate_semantic_gates(
        brief=_v2_brief(),
        artifact=artifact,
        approved_inputs=artifact["input_refs"],
        approved_artifacts=_approved_research_inputs(
            claims, outline_sections=outline_sections
        ),
        source_context={
            "snapshot_id": "source-context:model-knowledge",
            "snapshot_sha256": "c" * 64,
            "sources": sources,
        },
        product_mode="standalone",
    )


def test_mixed_report_keeps_public_citations_and_model_claims_isolated():
    claims = [
        _claim(claim_id="CLAIM-PUB", origin_tier="public_web"),
        _claim(
            claim_id="CLAIM-MODEL",
            origin_tier="model_knowledge",
            source_id=None,
            status="insufficient",
            statement="仅作分析补充。",
        ),
    ]
    usages = [
        {"claim_id": "CLAIM-PUB", "section_id": "SEC-EVIDENCE", "citation_marker": "[PUB-001]"},
        {"claim_id": "CLAIM-MODEL", "section_id": "SEC-ANALYSIS", "citation_marker": "模型知识·未核验"},
    ]
    artifact = _research_artifact(
        usages,
        evidence_text=(
            "公开资料结论 [PUB-001]。"
        ),
        references="[PUB-001] 公开资料。",
    )
    artifact["deliverable_markdown"] = artifact["deliverable_markdown"].replace(
        "在证据边界内进行分析。",
        "模型知识·未核验：仅作分析补充。模型知识时效未知。",
    )
    sources = [{"source_id": "PUB-001", "kind": "approved_public"}]
    outline_sections = [
        {
            "section_id": "SEC-EVIDENCE",
            "heading": "证据",
            "claim_ids": ["CLAIM-PUB"],
        },
        {
            "section_id": "SEC-ANALYSIS",
            "heading": "分析",
            "claim_ids": ["CLAIM-MODEL"],
        },
    ]
    report = _semantic_report(
        artifact,
        claims,
        sources=sources,
        outline_sections=outline_sections,
    )
    assert report["status"] == "passed"
    assert report["citation_validation"] == {
        "status": "passed",
        "required_claim_count": 2,
        "validated_claim_count": 2,
    }

    borrowed = deepcopy(artifact)
    borrowed["payload"]["claim_usage"][1]["citation_marker"] = "[PUB-001]"
    borrowed_report = _semantic_report(
        borrowed,
        claims,
        sources=sources,
        outline_sections=outline_sections,
    )
    assert "model_knowledge_citation_forbidden" in {
        issue["code"] for issue in borrowed_report["issues"]
    }


@pytest.mark.parametrize("also_in_declared_section", [False, True])
def test_model_claim_statement_cannot_appear_in_a_public_claim_section(
    also_in_declared_section,
):
    model_statement = "仅作模型方法分析，不代表实时事实。"
    claims = [
        _claim(claim_id="CLAIM-PUB", origin_tier="public_web"),
        _claim(
            claim_id="CLAIM-MODEL",
            origin_tier="model_knowledge",
            source_id=None,
            status="insufficient",
            statement=model_statement,
        ),
    ]
    usages = [
        {"claim_id": "CLAIM-PUB", "section_id": "SEC-EVIDENCE", "citation_marker": "[PUB-001]"},
        {"claim_id": "CLAIM-MODEL", "section_id": "SEC-ANALYSIS", "citation_marker": "模型知识·未核验"},
    ]
    artifact = _research_artifact(
        usages,
        evidence_text=f"公开资料结论 [PUB-001]。{model_statement}",
        references="[PUB-001] 公开资料。",
    )
    analysis_text = "模型知识·未核验：模型知识时效未知。"
    if also_in_declared_section:
        analysis_text = f"模型知识·未核验：{model_statement}模型知识时效未知。"
    artifact["deliverable_markdown"] = artifact["deliverable_markdown"].replace(
        "在证据边界内进行分析。",
        analysis_text,
    )
    outline_sections = [
        {
            "section_id": "SEC-EVIDENCE",
            "heading": "证据",
            "claim_ids": ["CLAIM-PUB"],
        },
        {
            "section_id": "SEC-ANALYSIS",
            "heading": "分析",
            "claim_ids": ["CLAIM-MODEL"],
        },
    ]

    report = _semantic_report(
        artifact,
        claims,
        sources=[{"source_id": "PUB-001", "kind": "approved_public"}],
        outline_sections=outline_sections,
    )

    assert "model_knowledge_statement_section_mismatch" in {
        issue["code"] for issue in report["issues"]
    }


def test_each_model_claim_requires_label_at_its_own_usage_section():
    claims = [
        _claim(
            claim_id="CLAIM-MODEL-1",
            origin_tier="model_knowledge",
            source_id=None,
            status="insufficient",
            statement="方案一仅作方法分析。",
        ),
        _claim(
            claim_id="CLAIM-MODEL-2",
            origin_tier="model_knowledge",
            source_id=None,
            status="insufficient",
            statement="方案二仅作方法分析。",
        ),
    ]
    usages = [
        {"claim_id": "CLAIM-MODEL-1", "section_id": "SEC-EVIDENCE", "citation_marker": ""},
        {"claim_id": "CLAIM-MODEL-2", "section_id": "SEC-ANALYSIS", "citation_marker": ""},
    ]
    artifact = _research_artifact(
        usages,
        evidence_text="模型知识·未核验：方案一仅作方法分析。模型知识时效未知。",
        references="无可核验外部来源。",
    )
    artifact["deliverable_markdown"] = artifact["deliverable_markdown"].replace(
        "在证据边界内进行分析。",
        "方案二仅作方法分析。",
    )

    report = _semantic_report(artifact, claims, sources=[])

    assert "model_knowledge_label_missing" in {
        issue["code"] for issue in report["issues"]
        if issue["target_id"] == "claim:CLAIM-MODEL-2"
    }


def test_each_model_claim_requires_one_label_occurrence_in_the_same_section():
    claims = [
        _claim(
            claim_id="CLAIM-MODEL-1",
            origin_tier="model_knowledge",
            source_id=None,
            status="insufficient",
            statement="方案一仅作方法分析。",
        ),
        _claim(
            claim_id="CLAIM-MODEL-2",
            origin_tier="model_knowledge",
            source_id=None,
            status="insufficient",
            statement="方案二仅作方法分析。",
        ),
    ]
    artifact = _research_artifact(
        [
            {"claim_id": claim["claim_id"], "section_id": "SEC-EVIDENCE", "citation_marker": "模型知识·未核验"}
            for claim in claims
        ],
        evidence_text=(
            "模型知识·未核验：方案一仅作方法分析。"
            "方案二仅作方法分析。模型知识时效未知。"
        ),
        references="无可核验外部来源。",
    )

    report = _semantic_report(artifact, claims, sources=[])

    assert "model_knowledge_label_count_mismatch" in {
        issue["code"] for issue in report["issues"]
    }


@pytest.mark.parametrize(
    "forbidden_text",
    [
        "https://example.test/research",
        "www.example.test/research",
        "[^12]",
        "[资料一]",
        "【参考一】",
        "（来源一）",
        "[PUB-001]",
        "[1]",
        "【1】",
        "（1）",
    ],
)
def test_model_claim_section_blocks_long_distance_and_fake_source_markers(
    forbidden_text,
):
    claims = [
        _claim(
            origin_tier="model_knowledge",
            source_id=None,
            status="insufficient",
            statement="本项仅作方法分析。",
        )
    ]
    spacer = "仅作方法分析。" * 80
    artifact = _research_artifact(
        [{"claim_id": "CLAIM-001", "section_id": "SEC-EVIDENCE", "citation_marker": "模型知识·未核验"}],
        evidence_text=(
            f"模型知识·未核验：{spacer}{forbidden_text}"
            "模型知识时效未知。"
        ),
        references="无可核验外部来源。",
    )

    report = _semantic_report(artifact, claims, sources=[])

    assert "model_knowledge_citation_forbidden" in {
        issue["code"] for issue in report["issues"]
    }


def test_mixed_public_and_model_claims_cannot_share_one_citation_section():
    claims = [
        _claim(claim_id="CLAIM-PUB", origin_tier="public_web"),
        _claim(
            claim_id="CLAIM-MODEL",
            origin_tier="model_knowledge",
            source_id=None,
            status="insufficient",
        ),
    ]
    artifact = _research_artifact(
        [
            {"claim_id": "CLAIM-PUB", "section_id": "SEC-EVIDENCE", "citation_marker": "[PUB-001]"},
            {"claim_id": "CLAIM-MODEL", "section_id": "SEC-EVIDENCE", "citation_marker": "模型知识·未核验"},
        ],
        evidence_text=(
            "公开资料结论 [PUB-001]。"
            + "边界说明。" * 80
            + "模型知识·未核验：仅作方法分析。模型知识时效未知。"
        ),
        references="[PUB-001] 公开资料。",
    )

    report = _semantic_report(
        artifact,
        claims,
        sources=[{"source_id": "PUB-001", "kind": "approved_public"}],
    )

    assert "model_knowledge_section_not_isolated" in {
        issue["code"] for issue in report["issues"]
    }


@pytest.mark.parametrize("fake_marker", ["[来源一]", "【来源一】", "见脚注1"])
def test_model_only_report_blocks_chinese_fake_citations(fake_marker):
    claims = [
        _claim(
            origin_tier="model_knowledge",
            source_id=None,
            status="insufficient",
        )
    ]
    artifact = _research_artifact(
        [{"claim_id": "CLAIM-001", "section_id": "SEC-EVIDENCE", "citation_marker": ""}],
        evidence_text="模型知识·未核验：仅作方法分析。模型知识时效未知。",
        references=f"无可核验外部来源；{fake_marker}",
    )

    report = _semantic_report(artifact, claims, sources=[])

    assert "model_knowledge_citation_forbidden" in {
        issue["code"] for issue in report["issues"]
    }


def test_model_claim_cannot_borrow_adjacent_public_citation():
    claims = [
        _claim(claim_id="CLAIM-PUB", origin_tier="public_web"),
        _claim(
            claim_id="CLAIM-MODEL",
            origin_tier="model_knowledge",
            source_id=None,
            status="insufficient",
        ),
    ]
    artifact = _research_artifact(
        [
            {"claim_id": "CLAIM-PUB", "section_id": "SEC-EVIDENCE", "citation_marker": "[PUB-001]"},
            {"claim_id": "CLAIM-MODEL", "section_id": "SEC-EVIDENCE", "citation_marker": ""},
        ],
        evidence_text=(
            "公开资料结论 [PUB-001] 模型知识·未核验：仅作分析补充。"
            "模型知识时效未知。"
        ),
        references="[PUB-001] 公开资料。",
    )

    report = _semantic_report(
        artifact,
        claims,
        sources=[{"source_id": "PUB-001", "kind": "approved_public"}],
    )

    assert "model_knowledge_citation_forbidden" in {
        issue["code"] for issue in report["issues"]
    }


def test_model_only_report_has_no_fake_citations_or_empty_reference_shell():
    claims = [
        _claim(
            origin_tier="model_knowledge",
            source_id=None,
            status="insufficient",
            statement="本项仅作方法分析。",
        )
    ]
    usage = [
        {"claim_id": "CLAIM-001", "section_id": "SEC-EVIDENCE", "citation_marker": ""}
    ]
    valid = _research_artifact(
        usage,
        evidence_text="模型知识·未核验：本项仅作方法分析。模型知识时效未知。",
        references="无可核验外部来源；本报告包含模型知识·未核验内容。",
    )
    valid_report = _semantic_report(valid, claims, sources=[])
    assert valid_report["status"] == "passed"

    fake = deepcopy(valid)
    fake["payload"]["claim_usage"][0]["citation_marker"] = "[SRC-FAKE]"
    fake["deliverable_markdown"] = fake["deliverable_markdown"].replace(
        "无可核验外部来源",
        "[SRC-FAKE] https://example.invalid [^1]",
    )
    fake_report = _semantic_report(fake, claims, sources=[])
    assert "model_knowledge_citation_forbidden" in {
        issue["code"] for issue in fake_report["issues"]
    }

    empty = _research_artifact(
        usage,
        evidence_text="模型知识·未核验：本项仅作方法分析。模型知识时效未知。",
        references="",
    )
    empty_report = _semantic_report(empty, claims, sources=[])
    assert "model_only_reference_section_empty" in {
        issue["code"] for issue in empty_report["issues"]
    }
