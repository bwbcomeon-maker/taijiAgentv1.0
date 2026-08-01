"""Single severity policy for expert-team stage issues.

Artifact structural validity and content risk are deliberately separate:
``validation_status`` records whether an artifact satisfies its immutable
protocol/binding contract, while this module decides whether its reported
content issues block the workflow.
"""

from __future__ import annotations

from collections.abc import Iterable


BLOCKING_SEVERITIES = frozenset({"blocking", "error"})
KNOWN_SEVERITIES = frozenset({"blocking", "error", "warning", "info"})
_ZERO_SOURCE_PLACEHOLDER_DOCUMENT_TYPES = frozenset(
    {"work_report", "meeting_minutes", "notice", "plan", "summary_plan"}
)


def _known_issues(issues: Iterable[object] | None) -> list[dict]:
    return [
        issue
        for issue in issues or []
        if isinstance(issue, dict)
        and str(issue.get("severity") or "") in KNOWN_SEVERITIES
    ]


def classify_stage_issues(issues: Iterable[object] | None) -> dict:
    """Return stable counts and the user-facing quality state."""

    known = _known_issues(issues)
    blocking_count = sum(
        1
        for issue in known
        if str(issue.get("severity") or "") in BLOCKING_SEVERITIES
    )
    warning_count = sum(
        1 for issue in known if str(issue.get("severity") or "") == "warning"
    )
    info_count = sum(
        1 for issue in known if str(issue.get("severity") or "") == "info"
    )
    state = "blocked" if blocking_count else "attention" if warning_count else "clear"
    return {
        "state": state,
        "blocking_count": blocking_count,
        "warning_count": warning_count,
        "info_count": info_count,
    }


def effective_artifact_validation(artifact: dict | None) -> dict:
    """Project legacy warning-only invalid artifacts without rewriting them.

    Older runtimes marked an otherwise structurally valid artifact ``invalid``
    whenever it contained a warning.  A non-empty warning-only signature is the
    narrow compatibility case.  Empty invalid artifacts, unknown severities,
    and any blocking/error issue remain fail closed.  Callers must still run
    the normal immutable artifact/hash/Brief validation before trusting this
    projection.
    """

    candidate = artifact if isinstance(artifact, dict) else {}
    original_status = str(candidate.get("validation_status") or "invalid")
    raw_issues = candidate.get("blocking_issues")
    issues = raw_issues if isinstance(raw_issues, list) else []
    quality = classify_stage_issues(issues)
    known = _known_issues(issues)
    legacy_warning_only = (
        original_status == "invalid"
        and bool(known)
        and len(known) == len(issues)
        and quality["blocking_count"] == 0
        and quality["warning_count"] > 0
    )
    status = "valid" if original_status == "valid" or legacy_warning_only else "invalid"
    return {
        "status": status,
        "original_status": original_status,
        "legacy_warning_only": legacy_warning_only,
        **quality,
    }


def artifact_blocks_progress(artifact: dict | None) -> bool:
    effective = effective_artifact_validation(artifact)
    return effective["status"] != "valid" or effective["blocking_count"] != 0


def brief_allows_labeled_placeholders(
    brief: dict | None,
    source_requirement: dict | None,
    *,
    product_mode: str,
) -> bool:
    """Return the frozen zero-source policy for ordinary standalone drafting."""

    candidate = brief if isinstance(brief, dict) else {}
    requirement = source_requirement if isinstance(source_requirement, dict) else {}
    source_policy = (
        candidate.get("source_policy")
        if isinstance(candidate.get("source_policy"), dict)
        else {}
    )
    minimum_ready = requirement.get("minimum_ready")
    return (
        str(product_mode or "") == "standalone"
        and type(minimum_ready) is int
        and minimum_ready == 0
        and str(candidate.get("task_mode") or "") == "create"
        and str(candidate.get("document_type") or "")
        in _ZERO_SOURCE_PLACEHOLDER_DOCUMENT_TYPES
        and source_policy.get("unknown_fact_action")
        == "allow_labeled_placeholder"
    )


def effective_review_issue_severity(
    brief: dict | None,
    source_requirement: dict | None,
    issue: dict | None,
    *,
    product_mode: str,
) -> str:
    """Project model severity through the frozen product/source contract."""

    candidate = issue if isinstance(issue, dict) else {}
    severity = str(candidate.get("severity") or "")
    if (
        severity in BLOCKING_SEVERITIES
        and str(candidate.get("category") or "") == "brief"
        and brief_allows_labeled_placeholders(
            brief,
            source_requirement,
            product_mode=product_mode,
        )
    ):
        return "warning"
    return severity


def review_issue_blocks_progress(
    brief: dict | None,
    source_requirement: dict | None,
    issue: dict | None,
    *,
    product_mode: str,
) -> bool:
    return effective_review_issue_severity(
        brief,
        source_requirement,
        issue,
        product_mode=product_mode,
    ) in BLOCKING_SEVERITIES


def public_stage_issues(issues: Iterable[object] | None) -> list[dict]:
    """Expose only user-actionable, non-sensitive issue fields."""

    return [
        {
            "severity": str(issue.get("severity") or "info"),
            "message": str(issue.get("message") or "存在待确认事项"),
            "suggested_action": str(issue.get("suggested_action") or "请人工核对"),
        }
        for issue in _known_issues(issues)
    ]
