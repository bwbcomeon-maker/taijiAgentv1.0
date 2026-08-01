from copy import deepcopy


def _review_checks(*, research: bool = False, failed: str | None = None) -> dict:
    keys = (
        (
            "brief_alignment",
            "citation_completeness",
            "unsupported_claims",
            "unresolved_contradictions",
            "as_of_date_compliance",
            "document_purity",
            "confidentiality",
        )
        if research
        else (
            "brief_alignment",
            "fact_traceability",
            "document_purity",
            "confidentiality",
            "document_structure",
        )
    )
    return {key: "failed" if key == failed else "passed" for key in keys}


def _brief(*, task_mode: str = "polish", research: bool = False) -> dict:
    return {
        "schema_version": "document-brief/v1",
        "status": "confirmed",
        "revision": 1,
        "confirmed_revision": 1,
        "confirmed_sha256": "b" * 64,
        "exact_title": "企业材料质量复核报告" if research else "迎峰计划润色稿",
        "document_type": "research_report" if research else "other_office_material",
        "task_mode": task_mode,
        "content_constraints": {
            "required_sections": (
                ["研究问题", "证据", "分析", "结论边界", "引用"]
                if research
                else ["润色后正文", "修改说明"]
            ),
            "must_include": [],
            "must_avoid": [],
        },
    }


def _source_context() -> dict:
    return {
        "snapshot_id": "source-context-0001",
        "snapshot_sha256": "c" * 64,
        "sources": [
            {
                "source_id": "SRC-001",
                "label": "原始材料",
                "content_text": (
                    "《迎峰计划》由国网空间公司执行，编号 SG-2026-07，"
                    "完成率 98.7%，明确结论为“保持现有方案”。"
                ),
            }
        ],
    }


def _content_artifact(markdown: str, *, failed_check: str | None = None) -> dict:
    return {
        "artifact_id": "polish:1",
        "sha256": "d" * 64,
        "artifact_type": "reviewed_document",
        "input_refs": [
            {
                "ref_type": "source_context",
                "snapshot_id": "source-context-0001",
                "sha256": "c" * 64,
            }
        ],
        "payload": {
            "document_type": "other_office_material",
            "review_report": {
                "checks": _review_checks(failed=failed_check),
                "issues": [],
                "unresolved_issue_ids": [],
            },
        },
        "deliverable_markdown": markdown,
        "blocking_issues": [],
    }


def _polish_markdown(body: str) -> str:
    return (
        "# 迎峰计划润色稿\n\n"
        "## 润色后正文\n\n"
        f"{body}\n\n"
        "## 修改说明\n\n"
        "优化段落层次和正式表达，未改变原文事实。\n"
    )


def test_polish_semantic_gate_blocks_changed_high_signal_source_anchors():
    from api.expert_teams.documents import evaluate_semantic_gates

    artifact = _content_artifact(
        _polish_markdown(
            "迎峰项目由相关单位执行，编号 SG-2026-08，完成率 97.0%，建议调整方案。"
        )
    )
    report = evaluate_semantic_gates(
        brief=_brief(),
        artifact=artifact,
        approved_inputs=artifact["input_refs"],
        source_context=_source_context(),
        product_mode="standalone",
    )

    assert report["status"] == "failed"
    assert report["source_preservation"]["status"] == "failed"
    assert report["source_preservation"]["checked_anchor_count"] >= 4
    assert report["source_preservation"]["missing_anchor_count"] >= 1
    assert "source_anchor_missing" in {item["code"] for item in report["issues"]}

    decoy = _content_artifact(
        (
            "# 迎峰计划润色稿\n\n"
            "## 润色后正文\n\n"
            "迎峰项目由相关单位执行，完成率 97.0%，建议调整方案。\n\n"
            "## 修改说明\n\n"
            "原文曾写明：《迎峰计划》由国网空间公司执行，编号 SG-2026-07，"
            "完成率 98.7%，明确结论为“保持现有方案”。\n"
        )
    )
    decoy_report = evaluate_semantic_gates(
        brief=_brief(),
        artifact=decoy,
        approved_inputs=decoy["input_refs"],
        source_context=_source_context(),
        product_mode="standalone",
    )
    assert decoy_report["source_preservation"]["status"] == "failed"
    assert "source_anchor_missing" in {
        item["code"] for item in decoy_report["issues"]
    }


def test_polish_semantic_gate_accepts_preserved_anchors_and_blocks_failed_attestation():
    from api.expert_teams.documents import evaluate_semantic_gates

    body = (
        "《迎峰计划》由国网空间公司执行，编号 SG-2026-07，"
        "完成率 98.7%，明确结论为“保持现有方案”。"
    )
    valid_artifact = _content_artifact(_polish_markdown(body))
    valid = evaluate_semantic_gates(
        brief=_brief(),
        artifact=valid_artifact,
        approved_inputs=valid_artifact["input_refs"],
        source_context=_source_context(),
        product_mode="standalone",
    )
    assert valid["status"] == "passed"
    assert valid["source_preservation"] == {
        "status": "passed",
        "checked_anchor_count": valid["source_preservation"]["checked_anchor_count"],
        "missing_anchor_count": 0,
    }

    failed_artifact = _content_artifact(
        _polish_markdown(body),
        failed_check="fact_traceability",
    )
    failed = evaluate_semantic_gates(
        brief=_brief(),
        artifact=failed_artifact,
        approved_inputs=failed_artifact["input_refs"],
        source_context=_source_context(),
        product_mode="standalone",
    )
    assert "review_check_failed" in {item["code"] for item in failed["issues"]}


def _research_artifact(marker: str, *, include_marker: bool = True) -> dict:
    citation = marker if include_marker else "引用见所附资料。"
    return {
        "artifact_id": "review:1",
        "sha256": "e" * 64,
        "artifact_type": "reviewed_research_document",
        "input_refs": [
            {"ref_type": "stage_artifact", "artifact_id": "evidence:1", "sha256": "1" * 64},
            {"ref_type": "stage_artifact", "artifact_id": "outline:1", "sha256": "2" * 64},
        ],
        "payload": {
            "claim_usage": [
                {
                    "claim_id": "CLAIM-001",
                    "section_id": "SEC-EVIDENCE",
                    "citation_marker": marker,
                }
            ],
            "review_report": {
                "checks": _review_checks(research=True),
                "issues": [],
                "unsupported_claim_ids": [],
                "unresolved_contradiction_ids": [],
                "unresolved_issue_ids": [],
            },
        },
        "deliverable_markdown": (
            "# 企业材料质量复核报告\n\n"
            "## 研究问题\n\n如何提升材料质量。\n\n"
            f"## 证据\n\n已核对资料支持当前判断。{citation}\n\n"
            "## 分析\n\n在既有资料边界内分析。\n\n"
            "## 结论边界\n\n不外推至未提供资料。\n\n"
            f"## 引用\n\n{citation}\n"
        ),
        "blocking_issues": [],
    }


def _research_inputs() -> list[dict]:
    return [
        {
            "artifact_id": "evidence:1",
            "sha256": "1" * 64,
            "artifact_type": "evidence_matrix",
            "input_refs": [
                {
                    "ref_type": "source_context",
                    "snapshot_id": "source-context-0001",
                    "sha256": "c" * 64,
                }
            ],
            "payload": {
                "claims": [
                    {
                        "claim_id": "CLAIM-001",
                        "status": "verified",
                        "evidence": [
                            {
                                "source_id": "SRC-001",
                                "segment_id": "SRC-001:SEG-0001",
                                "relationship": "supports",
                            }
                        ],
                    }
                ]
            },
        },
        {
            "artifact_id": "outline:1",
            "sha256": "2" * 64,
            "artifact_type": "research_outline",
            "payload": {
                "sections": [
                    {
                        "section_id": "SEC-EVIDENCE",
                        "claim_ids": ["CLAIM-001"],
                    }
                ]
            },
        },
    ]


def test_research_semantic_gate_binds_claim_markers_to_real_source_ids_and_body():
    from api.expert_teams.documents import evaluate_semantic_gates

    valid_artifact = _research_artifact("[SRC-001]")
    valid = evaluate_semantic_gates(
        brief=_brief(task_mode="create", research=True),
        artifact=valid_artifact,
        approved_inputs=valid_artifact["input_refs"],
        approved_artifacts=_research_inputs(),
        source_context=_source_context(),
        product_mode="standalone",
    )
    assert valid["status"] == "passed"
    assert valid["citation_validation"] == {
        "status": "passed",
        "required_claim_count": 1,
        "validated_claim_count": 1,
    }

    wrong_source = deepcopy(valid_artifact)
    wrong_source["payload"]["claim_usage"][0]["citation_marker"] = "[SRC-999]"
    wrong_source["deliverable_markdown"] = wrong_source["deliverable_markdown"].replace(
        "[SRC-001]",
        "[SRC-999]",
    )
    wrong = evaluate_semantic_gates(
        brief=_brief(task_mode="create", research=True),
        artifact=wrong_source,
        approved_inputs=wrong_source["input_refs"],
        approved_artifacts=_research_inputs(),
        source_context=_source_context(),
        product_mode="standalone",
    )
    assert "citation_source_mismatch" in {item["code"] for item in wrong["issues"]}

    missing_marker = _research_artifact("[SRC-001]", include_marker=False)
    missing = evaluate_semantic_gates(
        brief=_brief(task_mode="create", research=True),
        artifact=missing_marker,
        approved_inputs=missing_marker["input_refs"],
        approved_artifacts=_research_inputs(),
        source_context=_source_context(),
        product_mode="standalone",
    )
    assert "citation_marker_missing" in {item["code"] for item in missing["issues"]}

    unknown_claim = deepcopy(valid_artifact)
    unknown_claim["payload"]["claim_usage"].append(
        {
            "claim_id": "CLAIM-999",
            "section_id": "SEC-EVIDENCE",
            "citation_marker": "[SRC-001]",
        }
    )
    unknown = evaluate_semantic_gates(
        brief=_brief(task_mode="create", research=True),
        artifact=unknown_claim,
        approved_inputs=unknown_claim["input_refs"],
        approved_artifacts=_research_inputs(),
        source_context=_source_context(),
        product_mode="standalone",
    )
    assert "claim_usage_unknown" in {item["code"] for item in unknown["issues"]}


def test_zero_source_standalone_brief_placeholders_do_not_become_delivery_blockers():
    from api.expert_teams.documents import evaluate_semantic_gates

    brief = {
        "schema_version": "document-brief/v1",
        "status": "confirmed",
        "revision": 1,
        "confirmed_revision": 1,
        "confirmed_sha256": "b" * 64,
        "exact_title": "关于开展专家团功能试运行的通知",
        "document_type": "notice",
        "task_mode": "create",
        "source_policy": {
            "mode": "provided_only",
            "unknown_fact_action": "allow_labeled_placeholder",
        },
        "content_constraints": {
            "required_sections": [
                "背景与总体要求",
                "通知事项",
                "时间安排",
                "责任分工",
                "报送要求",
            ],
            "must_include": [],
            "must_avoid": [],
        },
    }
    unresolved = {
        "issue_id": "ISSUE-ROLE-001",
        "severity": "blocking",
        "category": "brief",
        "section_id": "SEC-RESPONSIBILITY",
        "description": "各实施小组负责人尚未明确",
        "resolution": None,
        "status": "open",
    }
    artifact = {
        "artifact_id": "polish:1",
        "sha256": "d" * 64,
        "artifact_type": "reviewed_document",
        "input_refs": [],
        "payload": {
            "title": brief["exact_title"],
            "document_type": "notice",
            "section_map": [],
            "fact_usage": [],
            "asset_requests": [],
            "open_issues": [unresolved],
            "review_report": {
                "checks": _review_checks(),
                "issues": [unresolved],
                "unresolved_issue_ids": [unresolved["issue_id"]],
            },
        },
        "deliverable_markdown": (
            f"# {brief['exact_title']}\n\n"
            "## 背景与总体要求\n\n明确试运行要求。\n\n"
            "## 通知事项\n\n按计划开展功能试运行。\n\n"
            "## 时间安排\n\n2026年8月1日至8月7日。\n\n"
            "## 责任分工\n\n各实施小组负责人需人工确认。\n\n"
            "## 报送要求\n\n报送方式待补充。\n"
        ),
        "blocking_issues": [],
    }

    allowed = evaluate_semantic_gates(
        brief=brief,
        artifact=artifact,
        approved_inputs=[],
        source_requirement={"minimum_ready": 0},
        product_mode="standalone",
    )
    assert allowed["status"] == "passed"
    assert "review_issue_unresolved" not in {
        item["code"] for item in allowed["issues"]
    }

    strict = deepcopy(artifact)
    strict["payload"]["review_report"]["issues"][0]["category"] = "security"
    blocked = evaluate_semantic_gates(
        brief=brief,
        artifact=strict,
        approved_inputs=[],
        source_requirement={"minimum_ready": 0},
        product_mode="standalone",
    )
    assert blocked["status"] == "failed"
    assert "review_issue_unresolved" in {
        item["code"] for item in blocked["issues"]
    }
    assert "各实施小组负责人尚未明确" in {
        item["message"] for item in blocked["issues"]
    }
