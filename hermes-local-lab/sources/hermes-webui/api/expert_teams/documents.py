"""Canonical DOCX delivery for final expert-team stages."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from api import docx_engine_v2

from .delivery_integrity import (
    DeliveryIntegrityError,
    canonical_attempt_root,
    path_contains_symlink,
    write_binding_manifest,
)
from .storage import safe_run_id


FINAL_STAGE_BY_TEAM = {
    "content-creator-team": "delivery",
    "deep-research-team": "review",
}


class FinalDocumentDeliveryError(RuntimeError):
    """A final Markdown source could not become a verified local delivery."""


_HEX64 = re.compile(r"[0-9a-f]{64}")
_WORKFLOW_TEXT = re.compile(r"负责专家|\bStage\s*\d+|复核交付|本阶段|可直接生成\s*DOCX", re.I)
_PLACEHOLDER_TEXT = re.compile(r"待补充|待完善|暂无|TBD|TODO|XXX", re.I)
_STANDALONE_UNSAFE_PLACEHOLDER_TEXT = re.compile(r"\b(?:TBD|TODO|XXX)\b", re.I)
_POLISH_ANCHOR_PATTERNS = (
    re.compile(r"[《“「『](?P<value>[^》”」』\r\n]{2,80})[》”」』]"),
    re.compile(
        r"(?:由|向|与|和|为|在)(?P<value>[\u4e00-\u9fff]{2,20}?"
        r"(?:有限公司|公司|集团|中心|委员会|办公室|项目部|研究院|研究所|供电所|变电站))"
    ),
    re.compile(
        r"(?:^|[，。；：、\s])(?P<value>[\u4e00-\u9fff]{2,20}?"
        r"(?:有限公司|公司|集团|中心|委员会|办公室|项目部|研究院|研究所|供电所|变电站))",
        re.M,
    ),
    re.compile(r"(?P<value>[A-Za-z]{2,}[A-Za-z0-9]*(?:[-_/][A-Za-z0-9]+)+)"),
    re.compile(
        r"(?P<value>\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)"
    ),
    re.compile(
        r"(?<![A-Za-z0-9])(?P<value>\d[\d,]*(?:\.\d+)?\s*"
        r"(?:%|％|万元|亿元|元|万|亿|项|人|天|个|次|台|套|家|公里|千米|米|千瓦|兆瓦|kW|MW|kV|KV))",
        re.I,
    ),
)


def assert_standalone_delivery_write_tree(attempt_root: Path) -> Path:
    """Reject every symlink in the standalone attempt tree before a write."""

    root = Path(attempt_root).expanduser().absolute()
    candidates = (
        root,
        root / "brief.json",
        root / "canonical",
        root / "canonical" / "artifact.json",
        root / "canonical" / "document.md",
        root / "assets",
        root / "assets" / "asset-manifest.json",
        root / "reviews",
        root / "reviews" / "semantic-gates.json",
        root / "reviews" / "standalone-quality-report.json",
        root / "delivery",
        root / "delivery" / "document.docx",
        root / "delivery" / "quality-report.json",
        root / "expert-team-delivery.json",
        root / "recovery",
    )
    if any(path_contains_symlink(root, candidate) for candidate in candidates):
        raise FinalDocumentDeliveryError(
            "standalone delivery write path contains a symlink"
        )
    return root


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_payload(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _immutable_bytes(path: Path, content: bytes, *, label: str) -> None:
    path = Path(path)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise FinalDocumentDeliveryError(f"{label} immutable snapshot changed")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _immutable_json(path: Path, payload: dict, *, label: str) -> None:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    _immutable_bytes(path, content, label=label)


def _normalized_markdown(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FinalDocumentDeliveryError("canonical document is empty")
    if "\x00" in value:
        raise FinalDocumentDeliveryError("canonical document contains a NUL byte")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
    return normalized


def write_canonical_snapshot(delivery_dir: Path, *, brief: dict, artifact: dict) -> dict[str, Path]:
    """Write the sole approved business-content snapshot for a delivery attempt."""

    from .stage_artifacts import StageArtifactError, validate_stage_artifact

    root = Path(delivery_dir).expanduser().resolve()
    try:
        validate_stage_artifact(artifact, brief=brief, approved_inputs=artifact.get("input_refs") or [])
    except StageArtifactError as exc:
        raise FinalDocumentDeliveryError(f"canonical artifact is invalid: {exc.code}") from exc
    if artifact.get("artifact_type") not in {"reviewed_document", "reviewed_research_document"}:
        raise FinalDocumentDeliveryError("canonical artifact is not an approved reviewed document")
    if brief.get("status") != "confirmed" or artifact.get("brief_sha256") != brief.get("confirmed_sha256"):
        raise FinalDocumentDeliveryError("canonical artifact brief binding changed")
    markdown = _normalized_markdown(artifact.get("deliverable_markdown"))
    paths = {
        "brief": root / "brief.json",
        "artifact": root / "canonical" / "artifact.json",
        "document": root / "canonical" / "document.md",
    }
    _immutable_json(paths["brief"], deepcopy(brief), label="brief")
    _immutable_json(paths["artifact"], deepcopy(artifact), label="canonical artifact")
    _immutable_bytes(paths["document"], markdown.encode("utf-8"), label="canonical document")
    if paths["document"].read_bytes() != markdown.encode("utf-8"):
        raise FinalDocumentDeliveryError("canonical document does not equal approved artifact projection")
    return paths


def _semantic_issue(code: str, target_id: str, message: str) -> dict:
    return {
        "issue_id": f"semantic:{code}:{hashlib.sha256(target_id.encode()).hexdigest()[:12]}",
        "code": code,
        "severity": "blocking",
        "target_id": target_id,
        "owner": "document-author",
        "message": message,
        "disposition": "unresolved",
    }


def _normalized_anchor(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s,，]", "", text)


def _markdown_section_body(markdown: str, heading: str) -> str:
    matches = list(
        re.finditer(r"(?m)^(?P<marks>#{2,6})[ \t]+(?P<title>.+?)[ \t]*$", markdown)
    )
    for index, match in enumerate(matches):
        if match.group("title").strip() != heading:
            continue
        level = len(match.group("marks"))
        end = len(markdown)
        for following in matches[index + 1 :]:
            if len(following.group("marks")) <= level:
                end = following.start()
                break
        return markdown[match.end() : end]
    return ""


def _contains_exact_statement(text: object, statement: object) -> bool:
    """Match the declared claim text exactly after Unicode/whitespace normalization."""

    normalized_text = re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", str(text or ""))
    ).strip()
    normalized_statement = re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", str(statement or ""))
    ).strip()
    if not normalized_statement:
        return False
    prefix = r"(?<!\w)" if re.match(r"\w", normalized_statement[0]) else ""
    suffix = r"(?!\w)" if re.match(r"\w", normalized_statement[-1]) else ""
    return re.search(
        prefix + re.escape(normalized_statement) + suffix,
        normalized_text,
    ) is not None


def _source_anchor_fingerprints(source_context: dict | None) -> list[str]:
    """Extract deterministic high-signal literals without persisting source text."""

    anchors: dict[str, str] = {}
    sources = (
        source_context.get("sources")
        if isinstance(source_context, dict)
        and isinstance(source_context.get("sources"), list)
        else []
    )
    for source in sources:
        text = source.get("content_text") if isinstance(source, dict) else None
        if not isinstance(text, str):
            continue
        for pattern in _POLISH_ANCHOR_PATTERNS:
            for match in pattern.finditer(text):
                normalized = _normalized_anchor(match.group("value"))
                if len(normalized) < 2:
                    continue
                anchors.setdefault(
                    normalized,
                    hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16],
                )
    return list(anchors)


def _source_ref_is_bound(
    artifact: dict,
    approved_artifacts: list[dict],
    source_context: dict | None,
) -> bool:
    if not isinstance(source_context, dict):
        return False
    snapshot_id = str(source_context.get("snapshot_id") or "")
    snapshot_sha256 = str(
        source_context.get("snapshot_sha256")
        or source_context.get("sha256")
        or ""
    )
    refs = []
    for candidate in [artifact, *approved_artifacts]:
        if not isinstance(candidate, dict):
            continue
        refs.extend(
            item
            for item in candidate.get("input_refs") or []
            if isinstance(item, dict)
        )
    return any(
        ref.get("ref_type") == "source_context"
        and str(ref.get("snapshot_id") or "") == snapshot_id
        and str(ref.get("sha256") or "") == snapshot_sha256
        for ref in refs
    )


def _review_contract_issues(
    brief: dict,
    payload: dict,
    *,
    source_requirement: dict | None,
    product_mode: str,
) -> list[dict]:
    review_report = (
        payload.get("review_report")
        if isinstance(payload.get("review_report"), dict)
        else {}
    )
    checks = (
        review_report.get("checks")
        if isinstance(review_report.get("checks"), dict)
        else {}
    )
    review_issues = review_report.get("issues") or []
    from .issue_policy import (
        review_check_blocks_progress,
        review_issue_blocks_progress,
    )

    issues = []
    for check_id, status in checks.items():
        if review_check_blocks_progress(
            brief,
            source_requirement,
            check_id=str(check_id),
            status=str(status),
            review_issues=review_issues,
            product_mode=product_mode,
        ):
            issues.append(
                _semantic_issue(
                    "review_check_failed",
                    f"review-check:{check_id}",
                    f"复核检查未通过：{check_id}",
                )
            )
    task_mode = str(brief.get("task_mode") or "")
    document_type = str(brief.get("document_type") or "")
    required_passed = set()
    if task_mode == "polish":
        required_passed.update({"brief_alignment", "fact_traceability"})
    if document_type == "research_report":
        required_passed.update(
            {"brief_alignment", "citation_completeness", "unsupported_claims"}
        )
    for check_id in sorted(required_passed):
        if checks.get(check_id) != "passed":
            issues.append(
                _semantic_issue(
                    "review_attestation_missing",
                    f"review-check:{check_id}",
                    f"最终复核没有确认关键检查：{check_id}",
                )
            )
    for item in review_issues:
        if (
            isinstance(item, dict)
            and item.get("status") == "open"
            and review_issue_blocks_progress(
                brief,
                source_requirement,
                item,
                product_mode=product_mode,
            )
        ):
            issue_id = str(item.get("issue_id") or "unknown")
            description = str(
                item.get("description")
                or "最终复核仍有未解决的阻断问题"
            )
            issues.append(
                _semantic_issue(
                    "review_issue_unresolved",
                    f"review-issue:{issue_id}",
                    description,
                )
            )
    for contradiction_id in review_report.get("unresolved_contradiction_ids") or []:
        value = str(contradiction_id or "").strip()
        if value:
            issues.append(
                _semantic_issue(
                    "research_contradiction_unresolved",
                    f"contradiction:{value}",
                    "研究报告仍有未解决的证据矛盾",
                )
            )
    return issues


def _polish_preservation_result(
    *,
    brief: dict,
    artifact: dict,
    approved_artifacts: list[dict],
    source_context: dict | None,
    markdown: str,
    product_mode: str,
) -> tuple[dict, list[dict]]:
    if (
        str(brief.get("task_mode") or "") != "polish"
        or str(product_mode or "") != "standalone"
    ):
        return {
            "status": "not_applicable",
            "checked_anchor_count": 0,
            "missing_anchor_count": 0,
        }, []
    anchors = _source_anchor_fingerprints(source_context)
    polished_body = _markdown_section_body(markdown, "润色后正文")
    normalized_polished_body = _normalized_anchor(polished_body)
    missing = [
        anchor
        for anchor in anchors
        if anchor not in normalized_polished_body
    ]
    issues = []
    sources = (
        source_context.get("sources")
        if isinstance(source_context, dict)
        and isinstance(source_context.get("sources"), list)
        else []
    )
    if not sources:
        issues.append(
            _semantic_issue(
                "polish_source_context_missing",
                "source-context:polish",
                "材料润色缺少已冻结的原始材料",
            )
        )
    elif not _source_ref_is_bound(artifact, approved_artifacts, source_context):
        issues.append(
            _semantic_issue(
                "polish_source_context_unbound",
                "source-context:polish-binding",
                "润色正文没有绑定本次任务的原始材料快照",
            )
        )
    for anchor in missing:
        issues.append(
            _semantic_issue(
                "source_anchor_missing",
                f"source-anchor:{hashlib.sha256(anchor.encode('utf-8')).hexdigest()[:16]}",
                "润色正文遗漏或改写了原文中的关键数字、标识、专名或明确表述",
            )
        )
    return {
        "status": "passed" if not issues else "failed",
        "checked_anchor_count": len(anchors),
        "missing_anchor_count": len(missing),
    }, issues


def _research_citation_result(
    *,
    brief: dict,
    payload: dict,
    approved_artifacts: list[dict],
    source_context: dict | None,
    markdown: str,
    product_mode: str,
) -> tuple[dict, list[dict]]:
    strict = (
        str(brief.get("document_type") or "") == "research_report"
        and str(product_mode or "") == "standalone"
    )
    if not strict:
        return {
            "status": "not_applicable",
            "required_claim_count": 0,
            "validated_claim_count": 0,
        }, []
    v2 = (
        isinstance(brief.get("source_policy"), dict)
        and brief["source_policy"].get("mode") == "automatic_fallback"
    )
    evidence = next(
        (
            item
            for item in approved_artifacts
            if isinstance(item, dict)
            and item.get("artifact_type") == "evidence_matrix"
        ),
        None,
    )
    outline = next(
        (
            item
            for item in approved_artifacts
            if isinstance(item, dict)
            and item.get("artifact_type") == "research_outline"
        ),
        None,
    )
    issues = []
    if evidence is None or outline is None:
        issues.append(
            _semantic_issue(
                "research_input_missing",
                "research-inputs:evidence-outline",
                "研究报告缺少已确认的证据矩阵或提纲",
            )
        )
    evidence_claims = {}
    for claim in ((evidence or {}).get("payload") or {}).get("claims") or []:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "").strip()
        if claim_id:
            evidence_claims[claim_id] = claim
    required_claim_ids = {
        str(claim_id).strip()
        for section in ((outline or {}).get("payload") or {}).get("sections") or []
        if isinstance(section, dict)
        for claim_id in section.get("claim_ids") or []
        if str(claim_id).strip()
    }
    usage_rows = [
        item
        for item in payload.get("claim_usage") or []
        if isinstance(item, dict)
    ]
    usage_by_claim = {
        str(item.get("claim_id") or "").strip(): item
        for item in usage_rows
        if str(item.get("claim_id") or "").strip()
    }
    outline_headings = {
        str(section.get("section_id") or "").strip(): str(section.get("heading") or "").strip()
        for section in ((outline or {}).get("payload") or {}).get("sections") or []
        if isinstance(section, dict) and str(section.get("section_id") or "").strip()
    }
    available_sources = {
        str(source.get("source_id") or "").strip(): source
        for source in (
            source_context.get("sources")
            if isinstance(source_context, dict)
            and isinstance(source_context.get("sources"), list)
            else []
        )
        if isinstance(source, dict) and str(source.get("source_id") or "").strip()
    }
    available_source_ids = set(available_sources)
    if not _source_ref_is_bound(
        {"input_refs": []},
        approved_artifacts,
        source_context,
    ):
        issues.append(
            _semantic_issue(
                "research_source_context_unbound",
                "source-context:research-binding",
                "研究报告证据没有绑定本次任务的资料快照",
            )
        )
    usage_claim_ids = [
        str(item.get("claim_id") or "").strip()
        for item in usage_rows
        if str(item.get("claim_id") or "").strip()
    ]
    if len(usage_claim_ids) != len(set(usage_claim_ids)):
        issues.append(
            _semantic_issue(
                "claim_usage_duplicate",
                "claim-usage:duplicates",
                "研究报告重复声明了同一个 claim 的引用",
            )
        )
    for claim_id in sorted(required_claim_ids - set(usage_by_claim)):
        issues.append(
            _semantic_issue(
                "claim_usage_missing",
                f"claim:{claim_id}",
                "研究报告提纲中的 claim 未在最终正文声明引用",
            )
        )
    validated_claim_ids = set()
    model_usage_by_section = {}
    non_model_usage_sections = set()
    if v2:
        for claim_id, usage in usage_by_claim.items():
            claim = evidence_claims.get(claim_id)
            if not isinstance(claim, dict):
                continue
            section_id = str(usage.get("section_id") or "").strip()
            if claim.get("origin_tier") == "model_knowledge":
                model_usage_by_section.setdefault(section_id, []).append(claim_id)
            else:
                non_model_usage_sections.add(section_id)
        for section_id, model_claim_ids in model_usage_by_section.items():
            heading = outline_headings.get(section_id, "")
            body = _markdown_section_body(markdown, heading) if heading else ""
            if body.count("模型知识·未核验") != len(model_claim_ids):
                issues.append(
                    _semantic_issue(
                        "model_knowledge_label_count_mismatch",
                        f"section:{section_id}",
                        "每个模型知识 claim 都必须在所属章节独立标注为未核验",
                    )
                )
            if section_id in non_model_usage_sections:
                issues.append(
                    _semantic_issue(
                        "model_knowledge_section_not_isolated",
                        f"section:{section_id}",
                        "模型知识 claim 不得与可引用的公网或本地 claim 共用章节",
                    )
                )
            if re.search(
                r"https?://|\bwww\.\S+|\[\^[^\]]+\]"
                r"|\[[A-Za-z][A-Za-z0-9._:-]*\]"
                r"|(?:\[|【|（)\s*\d+\s*(?:\]|】|）)"
                r"|(?:\[|【|（)[^\]】）]*(?:来源|资料|参考|source|ref)[^\]】）]*(?:\]|】|）)"
                r"|脚注\s*[0-9一二三四五六七八九十]+",
                body,
                re.I,
            ):
                issues.append(
                    _semantic_issue(
                        "model_knowledge_citation_forbidden",
                        f"section:{section_id}",
                        "含模型知识 claim 的章节不得包含来源标记、URL 或脚注",
                    )
                )
    for claim_id, usage in usage_by_claim.items():
        claim = evidence_claims.get(claim_id)
        if claim is None:
            issues.append(
                _semantic_issue(
                    "claim_usage_unknown",
                    f"claim:{claim_id}",
                    "研究报告引用了证据矩阵中不存在的 claim",
                )
            )
            continue
        marker = str(usage.get("citation_marker") or "").strip()
        origin_tier = str(claim.get("origin_tier") or "").strip()
        if v2 and origin_tier == "model_knowledge":
            if (
                marker not in {"", "模型知识·未核验"}
                or claim.get("evidence")
                or claim.get("status") == "verified"
            ):
                issues.append(
                    _semantic_issue(
                        "model_knowledge_citation_forbidden",
                        f"claim:{claim_id}",
                        "模型知识 claim 不得绑定来源、引用标记或已核验状态",
                    )
                )
                continue
            section_heading = outline_headings.get(str(usage.get("section_id") or "").strip(), "")
            section_body = _markdown_section_body(markdown, section_heading) if section_heading else ""
            if "模型知识·未核验" not in section_body:
                issues.append(
                    _semantic_issue(
                        "model_knowledge_label_missing",
                        f"claim:{claim_id}",
                        "模型知识内容未在正文明示为未核验",
                    )
                )
                continue
            statement = str(claim.get("statement") or "").strip()
            appears_in_declared_section = _contains_exact_statement(
                section_body,
                statement,
            )
            appears_in_citable_section = any(
                _contains_exact_statement(
                    _markdown_section_body(markdown, outline_headings.get(other_section_id, "")),
                    statement,
                )
                for other_section_id in non_model_usage_sections
                if other_section_id != str(usage.get("section_id") or "").strip()
                and outline_headings.get(other_section_id, "")
            )
            if not appears_in_declared_section or appears_in_citable_section:
                issues.append(
                    _semantic_issue(
                        "model_knowledge_statement_section_mismatch",
                        f"claim:{claim_id}",
                        "模型知识 claim 的原声明必须仅出现在其声明章节，不得出现在可引用 claim 章节",
                    )
                )
                continue
            if claim_id in required_claim_ids:
                validated_claim_ids.add(claim_id)
            continue
        marker_tokens = set(
            re.findall(r"[A-Za-z0-9][A-Za-z0-9._:-]*", marker)
        )
        allowed_source_ids = {
            str(ref.get("source_id") or "").strip()
            for ref in claim.get("evidence") or []
            if isinstance(ref, dict)
            and str(claim.get("status") or "") == "verified"
            and str(ref.get("relationship") or "") in {"supports", "context"}
            and str(ref.get("source_id") or "").strip() in available_source_ids
            and (
                not v2
                or origin_tier not in {"public_web", "local_knowledge"}
                or available_sources[str(ref.get("source_id") or "").strip()].get("kind")
                == (
                    "approved_public"
                    if origin_tier == "public_web"
                    else "approved_internal"
                )
            )
        }
        valid_marker = bool(marker_tokens & allowed_source_ids)
        marker_present = bool(marker and marker in markdown)
        if not valid_marker:
            issues.append(
                _semantic_issue(
                    "citation_source_mismatch",
                    f"claim:{claim_id}",
                    "研究报告引用标记未绑定该 claim 的真实 source_id",
                )
            )
        if not marker_present:
            issues.append(
                _semantic_issue(
                    "citation_marker_missing",
                    f"citation:{claim_id}",
                    "claim_usage 中的引用标记没有出现在最终正文",
                )
            )
        if valid_marker and marker_present and claim_id in required_claim_ids:
            validated_claim_ids.add(claim_id)
    required_claims = [
        evidence_claims[claim_id]
        for claim_id in required_claim_ids
        if claim_id in evidence_claims
    ]
    model_only = bool(required_claims) and all(
        claim.get("origin_tier") == "model_knowledge"
        for claim in required_claims
    )
    has_model_knowledge = any(
        claim.get("origin_tier") == "model_knowledge"
        for claim in required_claims
    )
    if v2 and has_model_knowledge:
        time_basis = ((evidence or {}).get("payload") or {}).get(
            "model_knowledge_time_basis"
        )
        expected_time_label = (
            str(time_basis.get("label") or "").strip()
            if isinstance(time_basis, dict)
            else ""
        ) or "模型知识时效未知"
        if expected_time_label not in markdown:
            issues.append(
                _semantic_issue(
                    "model_knowledge_time_basis_missing",
                    "model-knowledge:time-basis",
                    "报告未按可信元数据声明模型知识时效",
                )
            )
    if v2 and model_only:
        match = re.search(
            r"(?ms)^##\s+引用\s*$\n(?P<body>.*?)(?=^##\s|\Z)",
            markdown,
        )
        reference_body = match.group("body").strip() if match else ""
        if not reference_body:
            issues.append(
                _semantic_issue(
                    "model_only_reference_section_empty",
                    "section:引用",
                    "纯模型知识报告的引用章节必须说明无可核验外部来源",
                )
            )
        elif re.search(
            r"https?://|\[\^[^\]]+\]|\[[A-Za-z][A-Za-z0-9._:-]*\]"
            r"|\[[^\]]*来源[^\]]*\]|【[^】]*来源[^】]*】|脚注\s*[0-9一二三四五六七八九十]+",
            markdown,
            re.I,
        ):
            issues.append(
                _semantic_issue(
                    "model_knowledge_citation_forbidden",
                    "section:引用",
                    "纯模型知识报告不得伪造 URL、脚注或 source_id",
                )
            )
    return {
        "status": "passed" if not issues else "failed",
        "required_claim_count": len(required_claim_ids),
        "validated_claim_count": len(validated_claim_ids),
    }, issues


def resolve_approved_input_artifacts(run: dict, artifact: dict) -> list[dict]:
    """Resolve immutable stage refs and prove each dependency was approved."""

    artifacts = [
        item
        for item in run.get("stage_artifacts") or []
        if isinstance(item, dict)
    ]
    approved_refs = (
        run.get("approved_stage_artifact_refs")
        if isinstance(run.get("approved_stage_artifact_refs"), dict)
        else {}
    )
    resolved = []
    for ref in artifact.get("input_refs") or []:
        if not isinstance(ref, dict) or ref.get("ref_type") != "stage_artifact":
            continue
        matches = [
            item
            for item in artifacts
            if item.get("artifact_id") == ref.get("artifact_id")
            and item.get("sha256") == ref.get("sha256")
        ]
        if len(matches) != 1:
            raise FinalDocumentDeliveryError(
                "approved input artifact is missing or ambiguous"
            )
        candidate = matches[0]
        expected = {
            "artifact_id": candidate.get("artifact_id"),
            "sha256": candidate.get("sha256"),
        }
        if approved_refs.get(str(candidate.get("stage_id") or "")) != expected:
            raise FinalDocumentDeliveryError(
                "input artifact was not approved"
            )
        resolved.append(deepcopy(candidate))
    return resolved


def evaluate_semantic_gates(
    *,
    brief: dict,
    artifact: dict,
    approved_inputs: list[dict],
    approved_artifacts: list[dict] | None = None,
    source_context: dict | None = None,
    source_requirement: dict | None = None,
    product_mode: str = "enterprise",
) -> dict:
    """Evaluate delivery semantics without mutating the delivery attempt tree."""

    from .contracts import required_sections_for_brief
    from .stage_artifacts import document_section_headings, unresolved_quality_issues

    markdown = _normalized_markdown(artifact.get("deliverable_markdown"))
    headings = re.findall(r"(?m)^#\s+(.+?)\s*$", markdown)
    required_sections = required_sections_for_brief(brief)
    document_sections = document_section_headings(markdown)
    issues = []
    if headings != [str(brief.get("exact_title") or "")]:
        issues.append(_semantic_issue("title_mismatch", "document:h1", "正文唯一 H1 与确认标题不一致"))
    if artifact.get("artifact_type") not in {"reviewed_document", "reviewed_research_document"}:
        issues.append(_semantic_issue("document_type_mismatch", "artifact:type", "交付正文不是已复核文档"))
    payload = artifact.get("payload") if isinstance(artifact.get("payload"), dict) else {}
    trusted_approved_artifacts = [
        deepcopy(item)
        for item in approved_artifacts or []
        if isinstance(item, dict)
    ]
    payload_document_type = str(payload.get("document_type") or "").strip()
    if payload_document_type and payload_document_type != str(brief.get("document_type") or ""):
        issues.append(_semantic_issue("document_type_mismatch", "payload:document_type", "正文文种与确认 Brief 不一致"))
    if _WORKFLOW_TEXT.search(markdown):
        issues.append(_semantic_issue("workflow_text_leaked", "document:body", "正文包含内部阶段或专家协作话术"))
    placeholder_pattern = (
        _STANDALONE_UNSAFE_PLACEHOLDER_TEXT
        if str(product_mode or "") == "standalone"
        else _PLACEHOLDER_TEXT
    )
    if placeholder_pattern.search(markdown):
        issues.append(_semantic_issue("placeholder_detected", "document:body", "正文包含未处置占位符"))
    document_section_set = set(document_sections)
    for section in required_sections:
        if section not in document_section_set:
            issues.append(
                _semantic_issue(
                    "required_section_missing",
                    f"section:{section}",
                    f"正文缺少必备章节：{section}",
                )
            )
    review_report = payload.get("review_report") if isinstance(payload.get("review_report"), dict) else {}
    unsupported_claim_ids = [
        str(item).strip()
        for item in review_report.get("unsupported_claim_ids") or []
        if str(item).strip()
    ]
    evidence_issues = [
        _semantic_issue("unsupported_claim", f"claim:{claim_id}", "正文包含未获得证据支持的 claim")
        for claim_id in unsupported_claim_ids
    ]
    usage = payload.get("claim_usage") if isinstance(payload.get("claim_usage"), list) else payload.get("fact_usage")
    if isinstance(usage, list) and usage and not approved_inputs:
        for item in usage:
            if not isinstance(item, dict):
                continue
            claim_id = str(item.get("claim_id") or item.get("fact_id") or "").strip()
            if claim_id:
                evidence_issues.append(
                    _semantic_issue("claim_without_approved_source", f"claim:{claim_id}", "正文 claim 未绑定批准来源")
                )
    issues.extend(
        _review_contract_issues(
            brief,
            payload,
            source_requirement=source_requirement,
            product_mode=product_mode,
        )
    )
    source_preservation, source_issues = _polish_preservation_result(
        brief=brief,
        artifact=artifact,
        approved_artifacts=trusted_approved_artifacts,
        source_context=source_context,
        markdown=markdown,
        product_mode=product_mode,
    )
    citation_validation, citation_issues = _research_citation_result(
        brief=brief,
        payload=payload,
        approved_artifacts=trusted_approved_artifacts,
        source_context=source_context,
        markdown=markdown,
        product_mode=product_mode,
    )
    issues.extend(source_issues)
    evidence_issues.extend(citation_issues)
    issues.extend(evidence_issues)
    for item in unresolved_quality_issues(artifact):
        issues.append(_semantic_issue(item["code"], item["target_id"], item["message"]))
    report = {
        "schema_version": "expert-semantic-gates/v1",
        "brief_status": "passed" if brief.get("status") == "confirmed" else "failed",
        "semantic_status": "passed" if not issues else "failed",
        "evidence_status": "passed" if not evidence_issues else "failed",
        "status": "passed" if brief.get("status") == "confirmed" and not issues else "failed",
        "artifact_id": str(artifact.get("artifact_id") or ""),
        "artifact_sha256": str(artifact.get("sha256") or ""),
        "brief_revision": int(brief.get("confirmed_revision") or 0),
        "brief_sha256": str(brief.get("confirmed_sha256") or ""),
        "required_sections": required_sections,
        "document_sections": document_sections,
        "source_preservation": source_preservation,
        "citation_validation": citation_validation,
        "issues": issues,
    }
    return report


def write_semantic_gates_snapshot(
    delivery_dir: Path,
    *,
    brief: dict,
    artifact: dict,
    approved_inputs: list[dict],
    approved_artifacts: list[dict] | None = None,
    source_context: dict | None = None,
    source_requirement: dict | None = None,
    product_mode: str = "enterprise",
) -> dict:
    """Evaluate delivery semantics and persist one immutable upstream report."""

    report = evaluate_semantic_gates(
        brief=brief,
        artifact=artifact,
        approved_inputs=approved_inputs,
        approved_artifacts=approved_artifacts,
        source_context=source_context,
        source_requirement=source_requirement,
        product_mode=product_mode,
    )
    path = Path(delivery_dir).expanduser().resolve() / "reviews" / "semantic-gates.json"
    _immutable_json(path, report, label="semantic gates")
    return report


def write_layered_quality_report(
    delivery_dir: Path,
    *,
    semantic_gates: dict,
    automatic_quality: dict,
) -> tuple[dict, Path]:
    """Persist the seven independent enterprise quality statuses and stable targets."""

    automatic = automatic_quality if isinstance(automatic_quality, dict) else {}
    issues = deepcopy(semantic_gates.get("issues") or [])
    for item in automatic.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append(
            {
                "issue_id": str(item.get("issueId") or item.get("issue_id") or ""),
                "code": str(item.get("code") or "automatic_quality_issue"),
                "severity": str(item.get("severity") or "warning"),
                "target_id": str(item.get("issueId") or item.get("issue_id") or item.get("code") or "automatic"),
                "owner": "document-renderer" if item.get("domain") == "render" else "document-author",
                "message": str(item.get("message") or item.get("code") or "automatic quality issue"),
                "disposition": "unresolved",
                "completion_blocking": True,
            }
        )
    statuses = {
        "brief": str(semantic_gates.get("brief_status") or "failed"),
        "semantic": str(semantic_gates.get("semantic_status") or "failed"),
        "evidence": str(semantic_gates.get("evidence_status") or "failed"),
        "asset": str(automatic.get("assetStatus") or "failed"),
        "render": str(automatic.get("renderStatus") or "failed"),
        "office": "pending",
        "delivery": "pending",
    }
    upstream = [statuses[key] for key in ("brief", "semantic", "evidence", "asset", "render")]
    overall = "blocked" if any(status != "passed" for status in upstream) else "pending"
    report = {
        "schema_version": "expert-enterprise-quality/v1",
        "status": overall,
        "statuses": statuses,
        "issues": issues,
    }
    report["report_sha256"] = _sha256_payload(report)
    path = Path(delivery_dir).expanduser().resolve() / "reviews" / "enterprise-quality-report.json"
    _immutable_json(path, report, label="enterprise quality report")
    return report, path


def _standalone_automatic_items(automatic_quality: dict) -> tuple[list[dict], list[dict]]:
    """Remove checks that belong exclusively to the enterprise Office workflow."""

    raw_checks = automatic_quality.get("checks") if isinstance(automatic_quality, dict) else []
    raw_layers = automatic_quality.get("automaticQuality") if isinstance(automatic_quality, dict) else {}
    enterprise_tokens = ("office", "wps", "approval", "approver")

    def applicable(item: dict) -> bool:
        identity = " ".join(
            str(item.get(field) or "")
            for field in ("id", "issueId", "issue_id", "code", "domain")
        ).lower()
        return not any(token in identity for token in enterprise_tokens)

    checks = [deepcopy(item) for item in raw_checks or [] if isinstance(item, dict) and applicable(item)]
    issues = [
        deepcopy(item)
        for item in (raw_layers.get("issues") or [] if isinstance(raw_layers, dict) else [])
        if isinstance(item, dict) and applicable(item)
    ]
    return checks, issues


def write_standalone_quality_report(
    delivery_dir: Path,
    *,
    semantic_gates: dict,
    automatic_quality: dict,
    document_sha256: str,
) -> tuple[dict, Path]:
    """Persist standalone quality facts without Office, approval, or identity semantics."""

    automatic = automatic_quality if isinstance(automatic_quality, dict) else {}
    automatic_layers = automatic.get("automaticQuality")
    if not isinstance(automatic_layers, dict):
        raise FinalDocumentDeliveryError("automatic quality layers are missing")
    if not _HEX64.fullmatch(str(document_sha256 or "")):
        raise FinalDocumentDeliveryError("standalone document digest is invalid")
    checks, automatic_issues = _standalone_automatic_items(automatic)
    issues = deepcopy(semantic_gates.get("issues") or [])
    for item in automatic_issues:
        issues.append(
            {
                "issue_id": str(item.get("issueId") or item.get("issue_id") or item.get("code") or "automatic"),
                "code": str(item.get("code") or "automatic_quality_issue"),
                "severity": str(item.get("severity") or "warning"),
                "target_id": str(item.get("issueId") or item.get("issue_id") or item.get("code") or "automatic"),
                "owner": "document-renderer" if item.get("domain") == "render" else "document-author",
                "message": str(item.get("message") or item.get("code") or "automatic quality issue"),
                "disposition": "unresolved",
                "completion_blocking": bool(item.get("completionBlocking", True)),
            }
        )
    statuses = {
        "brief": str(semantic_gates.get("brief_status") or "failed"),
        "semantic": str(semantic_gates.get("semantic_status") or "failed"),
        "evidence": str(semantic_gates.get("evidence_status") or "failed"),
        "asset": str(automatic_layers.get("assetStatus") or "failed"),
        "render": str(automatic_layers.get("renderStatus") or "failed"),
        "document": "passed",
    }
    blocking = [item for item in issues if item.get("completion_blocking", True)]
    check_ids: set[str] = set()
    checks_passed = bool(checks)
    for item in checks:
        check_id = str(item.get("id") or "").strip()
        check_status = str(item.get("status") or "").strip()
        if not check_id or check_id in check_ids or check_status != "passed":
            checks_passed = False
        check_ids.add(check_id)
    report = {
        "schema_version": "expert-standalone-quality/v1",
        "status": (
            "passed"
            if all(value == "passed" for value in statuses.values()) and checks_passed and not blocking
            else "blocked"
        ),
        "statuses": statuses,
        "document_sha256": str(document_sha256),
        "checks": checks,
        "issues": issues,
    }
    report["report_sha256"] = _sha256_payload(report)
    path = Path(delivery_dir).expanduser().resolve() / "reviews" / "standalone-quality-report.json"
    _immutable_json(path, report, label="standalone quality report")
    return report, path


def prepare_canonical_delivery_inputs(
    workspace: Path,
    run: dict,
    *,
    stage_id: str,
    delivery_attempt: int,
    asset_manifest: dict | None = None,
    source_context: dict | None = None,
) -> dict:
    """Materialize rendering inputs from the approved canonical pointer only."""

    ref = run.get("canonical_document_ref")
    if not isinstance(ref, dict):
        raise FinalDocumentDeliveryError("canonical document reference is missing")
    candidates = [
        item
        for item in run.get("stage_artifacts") or []
        if isinstance(item, dict)
        and item.get("artifact_id") == ref.get("artifact_id")
        and item.get("sha256") == ref.get("sha256")
    ]
    if len(candidates) != 1:
        raise FinalDocumentDeliveryError("canonical document reference is ambiguous or stale")
    artifact = candidates[0]
    approvals = run.get("approved_stage_artifact_refs")
    approved_ref = (
        approvals.get(str(artifact.get("stage_id") or ""))
        if isinstance(approvals, dict)
        else None
    )
    if approved_ref != {"artifact_id": artifact.get("artifact_id"), "sha256": artifact.get("sha256")}:
        raise FinalDocumentDeliveryError("canonical document artifact was not approved")
    run_id = safe_run_id(str(run.get("run_id") or ""))
    root = canonical_attempt_root(workspace, run_id, stage_id, delivery_attempt)
    standalone = str(run.get("product_mode") or "") == "standalone"
    if standalone:
        assert_standalone_delivery_write_tree(root)
    brief = run.get("document_brief") if isinstance(run.get("document_brief"), dict) else {}
    profile = (
        run.get("launch_profile_snapshot")
        if isinstance(run.get("launch_profile_snapshot"), dict)
        else {}
    )
    source_requirement = (
        profile.get("source_requirement")
        if isinstance(profile.get("source_requirement"), dict)
        else None
    )
    needs_strict_source_semantics = (
        standalone
        and (
            str(brief.get("task_mode") or "") == "polish"
            or str(brief.get("document_type") or "") == "research_report"
        )
    )
    approved_artifacts = (
        resolve_approved_input_artifacts(run, artifact)
        if needs_strict_source_semantics
        else []
    )
    paths = write_canonical_snapshot(root, brief=brief, artifact=artifact)
    semantic = write_semantic_gates_snapshot(
        root,
        brief=brief,
        artifact=artifact,
        approved_inputs=artifact.get("input_refs") or [],
        approved_artifacts=approved_artifacts,
        source_context=source_context,
        source_requirement=source_requirement,
        product_mode=str(run.get("product_mode") or "enterprise"),
    )
    assets = deepcopy(asset_manifest) if isinstance(asset_manifest, dict) else {
        "schema_version": "expert-asset-manifest/v1",
        "assets": [],
    }
    asset_path = root / "assets" / "asset-manifest.json"
    _immutable_json(asset_path, assets, label="asset manifest")
    return {
        "attempt_root": root,
        "brief": deepcopy(brief),
        "artifact": deepcopy(artifact),
        "semantic_gates": semantic,
        "asset_manifest": assets,
        "paths": {**paths, "semantic_gates": root / "reviews" / "semantic-gates.json", "asset_manifest": asset_path},
    }


def _validated_identity(value: dict, *, fields: tuple[str, ...], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise FinalDocumentDeliveryError(f"{label} identity is incomplete")
    result = {field: value[field] for field in fields}
    for field, item in result.items():
        if not isinstance(item, str) or not item.strip():
            raise FinalDocumentDeliveryError(f"{label}.{field} is missing")
        if field.endswith("sha256") and not _HEX64.fullmatch(item):
            raise FinalDocumentDeliveryError(f"{label}.{field} is invalid")
    return result


def build_render_input_binding(
    *,
    brief: dict,
    artifact: dict,
    canonical_document_path: Path,
    asset_manifest_path: Path,
    semantic_gates_path: Path,
    template: dict,
    renderer: dict,
) -> dict:
    from .delivery_integrity import sha256_file

    template_identity = _validated_identity(
        template,
        fields=("id", "version", "package_sha256"),
        label="template",
    )
    renderer_identity = _validated_identity(
        renderer,
        fields=("name", "version", "build_sha256", "profile_id", "profile_sha256"),
        label="renderer",
    )
    for path, label in (
        (canonical_document_path, "canonical document"),
        (asset_manifest_path, "asset manifest"),
        (semantic_gates_path, "semantic gates"),
    ):
        if not Path(path).is_file():
            raise FinalDocumentDeliveryError(f"{label} is missing")
    payload = {
        "schemaVersion": "render-input-binding/v1",
        "brief": {
            "revision": int(brief.get("confirmed_revision") or 0),
            "sha256": str(brief.get("confirmed_sha256") or ""),
        },
        "canonicalArtifact": {
            "artifactId": str(artifact.get("artifact_id") or ""),
            "sha256": str(artifact.get("sha256") or ""),
        },
        "canonicalMarkdownSha256": sha256_file(Path(canonical_document_path)),
        "assetManifestSha256": sha256_file(Path(asset_manifest_path)),
        "semanticGatesSha256": sha256_file(Path(semantic_gates_path)),
        "template": {
            "id": template_identity["id"],
            "version": template_identity["version"],
            "packageSha256": template_identity["package_sha256"],
        },
        "rendererIdentity": {
            "name": renderer_identity["name"],
            "version": renderer_identity["version"],
            "buildSha256": renderer_identity["build_sha256"],
            "profileId": renderer_identity["profile_id"],
            "profileSha256": renderer_identity["profile_sha256"],
        },
    }
    for field in ("sha256",):
        if not _HEX64.fullmatch(payload["brief"][field]) or not _HEX64.fullmatch(payload["canonicalArtifact"][field]):
            raise FinalDocumentDeliveryError("render input upstream binding is invalid")
    payload["render_input_fingerprint"] = _sha256_payload(payload)
    return payload


def build_delivery_binding_v2(
    delivery_dir: Path,
    *,
    session_id: str,
    run_id: str,
    stage_id: str,
    stage_attempt: int,
    delivery_attempt: int,
    document_revision: int,
    brief: dict,
    artifact: dict,
    assets: Path,
    semantic_gates: dict,
    template: dict,
    renderer: dict,
    render_input_fingerprint: str,
    document: Path,
    quality: Path,
) -> dict:
    from .delivery_integrity import sha256_file

    root = Path(delivery_dir).expanduser().resolve()
    canonical_path = root / "canonical" / "document.md"
    gates_path = root / "reviews" / "semantic-gates.json"
    render_input = build_render_input_binding(
        brief=brief,
        artifact=artifact,
        canonical_document_path=canonical_path,
        asset_manifest_path=Path(assets),
        semantic_gates_path=gates_path,
        template=template,
        renderer=renderer,
    )
    if render_input["render_input_fingerprint"] != render_input_fingerprint:
        raise FinalDocumentDeliveryError("render input fingerprint does not close over renderer and inputs")
    if semantic_gates.get("status") != "passed":
        raise FinalDocumentDeliveryError("semantic gates have not passed")
    if not str(session_id or "").strip() or not str(run_id or "").strip():
        raise FinalDocumentDeliveryError("delivery session or run identity is missing")
    if int(stage_attempt) <= 0 or int(delivery_attempt) <= 0 or int(document_revision) <= 0:
        raise FinalDocumentDeliveryError("stage attempt, delivery attempt, or document revision is invalid")
    expected_document = root / "delivery" / "document.docx"
    expected_quality = root / "delivery" / "quality-report.json"
    if Path(document).resolve() != expected_document or Path(quality).resolve() != expected_quality:
        raise FinalDocumentDeliveryError("delivery output path is not canonical")
    if not expected_document.is_file() or not expected_quality.is_file():
        raise FinalDocumentDeliveryError("delivery output is missing")
    try:
        automatic_report = json.loads(expected_quality.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalDocumentDeliveryError("automatic quality report is invalid") from exc
    automatic_quality = automatic_report.get("automaticQuality")
    if not isinstance(automatic_quality, dict):
        raise FinalDocumentDeliveryError("automatic quality layers are missing")
    layered_quality, layered_quality_path = write_layered_quality_report(
        root,
        semantic_gates=semantic_gates,
        automatic_quality=automatic_quality,
    )
    if any(layered_quality["statuses"][key] != "passed" for key in ("brief", "semantic", "evidence", "asset", "render")):
        raise FinalDocumentDeliveryError("enterprise quality gates have not passed")
    binding = {
        "schema_version": "expert-delivery-binding/v2",
        "session_id": str(session_id).strip(),
        "run_id": str(run_id).strip(),
        "stage_id": str(stage_id).strip(),
        "stage_attempt": int(stage_attempt),
        "delivery_attempt": int(delivery_attempt),
        "document_revision": int(document_revision),
        "render_input_fingerprint": render_input_fingerprint,
        "brief": render_input["brief"],
        "canonical_artifact": {
            "artifact_id": render_input["canonicalArtifact"]["artifactId"],
            "sha256": render_input["canonicalArtifact"]["sha256"],
        },
        "canonical_markdown": {"path": "canonical/document.md", "sha256": render_input["canonicalMarkdownSha256"]},
        "asset_manifest": {"path": "assets/asset-manifest.json", "sha256": render_input["assetManifestSha256"]},
        "semantic_gates": {"path": "reviews/semantic-gates.json", "sha256": render_input["semanticGatesSha256"]},
        "template": {
            "id": render_input["template"]["id"],
            "version": render_input["template"]["version"],
            "package_sha256": render_input["template"]["packageSha256"],
        },
        "renderer": {
            "name": render_input["rendererIdentity"]["name"],
            "version": render_input["rendererIdentity"]["version"],
            "build_sha256": render_input["rendererIdentity"]["buildSha256"],
            "profile_id": render_input["rendererIdentity"]["profileId"],
            "profile_sha256": render_input["rendererIdentity"]["profileSha256"],
        },
        "document": {"path": "delivery/document.docx", "sha256": sha256_file(expected_document)},
        "automatic_quality_report": {"path": "delivery/quality-report.json", "sha256": sha256_file(expected_quality)},
        "layered_quality_report": {
            "path": "reviews/enterprise-quality-report.json",
            "sha256": sha256_file(layered_quality_path),
        },
    }
    _immutable_json(root / "expert-team-delivery.json", binding, label="delivery binding")
    return binding


def build_delivery_binding_v3(
    delivery_dir: Path,
    *,
    session_id: str,
    run_id: str,
    stage_id: str,
    stage_attempt: int,
    delivery_attempt: int,
    document_revision: int,
    brief: dict,
    artifact: dict,
    assets: Path,
    semantic_gates: dict,
    template: dict,
    renderer: dict,
    render_input_fingerprint: str,
    document: Path,
    quality: Path,
) -> dict:
    """Build the standalone binding without inheriting enterprise review semantics."""

    from .delivery_integrity import sha256_file

    root = assert_standalone_delivery_write_tree(delivery_dir).resolve()
    canonical_path = root / "canonical" / "document.md"
    gates_path = root / "reviews" / "semantic-gates.json"
    render_input = build_render_input_binding(
        brief=brief,
        artifact=artifact,
        canonical_document_path=canonical_path,
        asset_manifest_path=Path(assets),
        semantic_gates_path=gates_path,
        template=template,
        renderer=renderer,
    )
    if render_input["render_input_fingerprint"] != render_input_fingerprint:
        raise FinalDocumentDeliveryError("render input fingerprint does not close over renderer and inputs")
    if semantic_gates.get("status") != "passed":
        raise FinalDocumentDeliveryError("semantic gates have not passed")
    if not str(session_id or "").strip() or not str(run_id or "").strip():
        raise FinalDocumentDeliveryError("delivery session or run identity is missing")
    if int(stage_attempt) <= 0 or int(delivery_attempt) <= 0 or int(document_revision) <= 0:
        raise FinalDocumentDeliveryError("stage attempt, delivery attempt, or document revision is invalid")
    if not str(render_input["template"]["id"]).startswith("standalone-"):
        raise FinalDocumentDeliveryError("standalone delivery requires a standalone template")
    if render_input["rendererIdentity"]["profileId"] != "standalone-default":
        raise FinalDocumentDeliveryError("standalone delivery requires the standalone renderer profile")
    expected_document = root / "delivery" / "document.docx"
    expected_quality = root / "delivery" / "quality-report.json"
    if Path(document).resolve() != expected_document or Path(quality).resolve() != expected_quality:
        raise FinalDocumentDeliveryError("delivery output path is not canonical")
    for candidate in (root, expected_document, expected_quality):
        if candidate.is_symlink():
            raise FinalDocumentDeliveryError("standalone delivery path contains a symlink")
    if not expected_document.is_file() or not expected_quality.is_file():
        raise FinalDocumentDeliveryError("delivery output is missing")
    try:
        automatic_report = json.loads(expected_quality.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalDocumentDeliveryError("automatic quality report is invalid") from exc
    document_sha256 = sha256_file(expected_document)
    standalone_quality, standalone_quality_path = write_standalone_quality_report(
        root,
        semantic_gates=semantic_gates,
        automatic_quality=automatic_report,
        document_sha256=document_sha256,
    )
    if standalone_quality["status"] != "passed":
        raise FinalDocumentDeliveryError("standalone quality gates have not passed")
    binding = {
        "schema_version": "expert-delivery-binding/v3",
        "product_mode": "standalone",
        "session_id": str(session_id).strip(),
        "run_id": str(run_id).strip(),
        "stage_id": str(stage_id).strip(),
        "stage_attempt": int(stage_attempt),
        "delivery_attempt": int(delivery_attempt),
        "document_revision": int(document_revision),
        "render_input_fingerprint": render_input_fingerprint,
        "brief": render_input["brief"],
        "canonical_artifact": {
            "artifact_id": render_input["canonicalArtifact"]["artifactId"],
            "sha256": render_input["canonicalArtifact"]["sha256"],
        },
        "canonical_markdown": {"path": "canonical/document.md", "sha256": render_input["canonicalMarkdownSha256"]},
        "asset_manifest": {"path": "assets/asset-manifest.json", "sha256": render_input["assetManifestSha256"]},
        "semantic_gates": {"path": "reviews/semantic-gates.json", "sha256": render_input["semanticGatesSha256"]},
        "template": {
            "id": render_input["template"]["id"],
            "version": render_input["template"]["version"],
            "package_sha256": render_input["template"]["packageSha256"],
        },
        "renderer": {
            "name": render_input["rendererIdentity"]["name"],
            "version": render_input["rendererIdentity"]["version"],
            "build_sha256": render_input["rendererIdentity"]["buildSha256"],
            "profile_id": render_input["rendererIdentity"]["profileId"],
            "profile_sha256": render_input["rendererIdentity"]["profileSha256"],
        },
        "document": {"path": "delivery/document.docx", "sha256": document_sha256},
        "automatic_quality_report": {"path": "delivery/quality-report.json", "sha256": sha256_file(expected_quality)},
        "standalone_quality_report": {
            "path": "reviews/standalone-quality-report.json",
            "sha256": sha256_file(standalone_quality_path),
        },
    }
    _immutable_json(root / "expert-team-delivery.json", binding, label="delivery binding")
    return binding


def build_delivery_manifest_from_binding(binding: dict, quality_report: dict) -> dict:
    """Project the public system-stage manifest from hash-bound delivery facts only."""

    if not isinstance(binding, dict) or binding.get("schema_version") not in {
        "expert-delivery-binding/v2",
        "expert-delivery-binding/v3",
    }:
        raise ValueError("delivery binding is invalid")
    standalone = binding.get("schema_version") == "expert-delivery-binding/v3"
    if standalone and binding.get("product_mode") != "standalone":
        raise ValueError("standalone delivery binding mode is invalid")
    if not isinstance(quality_report, dict):
        raise ValueError("quality report is invalid")
    binding_path = str(binding.get("_binding_path") or "").strip()
    binding_sha256 = str(binding.get("_binding_sha256") or "").strip()
    quality_sha256 = str(binding.get("_quality_report_sha256") or "").strip()
    attempt = int(binding.get("delivery_attempt") or 0)
    expected_path = (
        f".taiji/expert-team-deliveries/{binding.get('run_id')}/{binding.get('stage_id')}"
        f"/attempt-{attempt}/expert-team-delivery.json"
    )
    if binding_path != expected_path or not _HEX64.fullmatch(binding_sha256):
        raise ValueError("delivery binding path or hash is invalid")
    expected_quality = binding.get("automatic_quality_report")
    if (
        not isinstance(expected_quality, dict)
        or expected_quality.get("path") != "delivery/quality-report.json"
        or quality_sha256 != expected_quality.get("sha256")
    ):
        raise ValueError("quality report hash does not match delivery binding")
    if not _HEX64.fullmatch(str(binding.get("render_input_fingerprint") or "")):
        raise ValueError("render input fingerprint is invalid")
    if attempt <= 0 or int(binding.get("document_revision") or 0) <= 0:
        raise ValueError("delivery attempt or document revision is invalid")

    if standalone:
        checks, applicable_issues = _standalone_automatic_items(quality_report)
    else:
        checks = [
            item for item in quality_report.get("checks") or []
            if isinstance(item, dict) and item.get("id") != "wps_visual"
        ]
        applicable_issues = [
            item for item in (quality_report.get("automaticQuality") or {}).get("issues") or []
            if isinstance(item, dict)
        ]
    counts = {
        "passed_count": sum(item.get("status") == "passed" for item in checks),
        "failed_count": sum(item.get("status") == "failed" for item in checks),
        "warning_count": sum(item.get("status") in {"passed_with_warnings", "not_verified"} for item in checks),
    }
    automatic = quality_report.get("automaticQuality")
    if not isinstance(automatic, dict):
        raise ValueError("automatic quality layers are missing")
    blocking_count = sum(bool(item.get("completionBlocking")) for item in applicable_issues)
    counts["blocking_count"] = blocking_count
    counts["status"] = (
        "passed"
        if automatic.get("assetStatus") == "passed"
        and automatic.get("renderStatus") == "passed"
        and counts["failed_count"] == 0
        and counts["warning_count"] == 0
        and blocking_count == 0
        else "failed"
    )
    result = {
        "schema_version": "delivery-manifest/v2" if standalone else "delivery-manifest/v1",
        "delivery_binding_path": binding_path,
        "delivery_binding_sha256": binding_sha256,
        "render_input_fingerprint": binding["render_input_fingerprint"],
        "delivery_attempt": attempt,
        "document_revision": int(binding["document_revision"]),
        "automatic_check_summary": {
            "status": counts["status"],
            "passed_count": counts["passed_count"],
            "failed_count": counts["failed_count"],
            "warning_count": counts["warning_count"],
            "blocking_count": counts["blocking_count"],
        },
    }
    if standalone:
        document = binding.get("document")
        standalone_quality = binding.get("standalone_quality_report")
        if (
            not isinstance(document, dict)
            or document.get("path") != "delivery/document.docx"
            or not _HEX64.fullmatch(str(document.get("sha256") or ""))
            or not isinstance(standalone_quality, dict)
            or standalone_quality.get("path") != "reviews/standalone-quality-report.json"
            or not _HEX64.fullmatch(str(standalone_quality.get("sha256") or ""))
        ):
            raise ValueError("standalone delivery outputs are invalid")
        result.update(
            {
                "product_mode": "standalone",
                "document_sha256": document["sha256"],
                "standalone_quality_report_sha256": standalone_quality["sha256"],
                "local_confirmation_required": True,
            }
        )
    else:
        result["office_review_required"] = True
    return result


def is_final_delivery_stage(run: dict, stage_id: str) -> bool:
    return FINAL_STAGE_BY_TEAM.get(str(run.get("team_id") or "")) == str(stage_id or "")


def _safe_slug(value: str) -> str:
    import re

    text = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "-", str(value or "").strip()).strip("-_")
    return text[:64] or "delivery"


def _display_path(workspace: Path, target: Path) -> str:
    try:
        return str(target.resolve().relative_to(workspace.resolve()))
    except (OSError, ValueError):
        return str(target.resolve())


def template_id_for_material(material_type: str) -> str:
    return "meeting-minutes" if str(material_type or "") == "meeting_minutes" else "general-proposal"


def _artifact(
    workspace: Path,
    *,
    stage_id: str,
    attempt: int,
    kind: str,
    label: str,
    path: Path,
    status: str,
    created_at: str,
    binding: dict,
    binding_manifest_path: str,
) -> dict:
    exists = path.exists()
    return {
        "id": f"{stage_id}:{attempt}:{kind}",
        "kind": kind,
        "label": label,
        "title": label,
        "path": _display_path(workspace, path),
        "exists": exists,
        "attempt": attempt,
        "stage": stage_id,
        "status": status if exists else "missing",
        "created_at": created_at,
        "run_id": binding["run_id"],
        "session_id": binding["session_id"],
        "source_sha256": binding["source_sha256"],
        "document_sha256": binding["document_sha256"],
        "binding_manifest_path": binding_manifest_path,
    }


def build_final_document_delivery(
    workspace: Path,
    run: dict,
    output: dict,
    *,
    material_type: str,
) -> dict:
    from .rich_draft import build_rich_draft_package

    workspace_path = Path(workspace).expanduser().resolve()
    stage_id = _safe_slug(str(output.get("stage_id") or output.get("task_id") or "delivery"))
    attempt = max(1, int(output.get("stage_attempt") or output.get("attempt") or 1))
    run_id = safe_run_id(str(run.get("run_id") or ""))
    session_id = str(run.get("session_id") or "").strip()
    root = canonical_attempt_root(workspace_path, run_id, stage_id, attempt)
    source_path = root / "final.md"
    delivery_dir = root / "delivery"
    root.mkdir(parents=True, exist_ok=True)
    content = str(output.get("content") or "").strip()
    if not content:
        raise FinalDocumentDeliveryError("最终 Markdown 为空，无法生成 DOCX")
    expected_source = content + "\n"
    if source_path.exists() and source_path.read_text(encoding="utf-8") != expected_source:
        raise FinalDocumentDeliveryError("同一阶段 attempt 的最终 Markdown 内容不一致")
    if not source_path.exists():
        source_path.write_text(expected_source, encoding="utf-8")

    template_id = template_id_for_material(material_type)
    final_rich_package = None
    render_source_path = source_path
    render_asset_dir = root
    if template_id == "general-proposal":
        try:
            final_rich_package = build_rich_draft_package(workspace_path, run, output)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise FinalDocumentDeliveryError(f"最终富内容稿打包失败：{exc}") from exc
        render_source_path = workspace_path / str(final_rich_package.get("draft_path") or "")
        render_asset_dir = workspace_path / str(final_rich_package.get("package_dir") or "")
        if not render_source_path.is_file() or not render_asset_dir.is_dir():
            raise FinalDocumentDeliveryError("最终富内容稿包缺少 Markdown 或资产目录")

    payload, status = docx_engine_v2._create_expert_delivery_job(
        {
            "template_id": template_id,
            "source_path": _display_path(workspace_path, render_source_path),
            "source_type": "markdown",
            "asset_dir": _display_path(workspace_path, render_asset_dir),
            "out_dir": _display_path(workspace_path, delivery_dir),
        },
        workspace_path,
        run_id=run_id,
        stage_id=stage_id,
        attempt=attempt,
    )
    if status != 200 or not payload.get("ok"):
        raise FinalDocumentDeliveryError(
            str(payload.get("message") or payload.get("code") or "DOCX 生成失败")
        )

    document_path = Path(str(payload.get("document_path") or "")).expanduser()
    if not document_path.is_absolute():
        document_path = workspace_path / document_path
    resolved_delivery_dir = Path(str(payload.get("delivery_dir") or delivery_dir)).expanduser()
    if not resolved_delivery_dir.is_absolute():
        resolved_delivery_dir = workspace_path / resolved_delivery_dir
    quality_path = Path(str(payload.get("quality_report_path") or (resolved_delivery_dir / "quality-report.json"))).expanduser()
    if not quality_path.is_absolute():
        quality_path = workspace_path / quality_path
    canonical_document_path = delivery_dir / "document.docx"
    canonical_quality_path = delivery_dir / "quality-report.json"
    if document_path.resolve() != canonical_document_path.resolve():
        raise FinalDocumentDeliveryError(f"DOCX 引擎返回了非规范文档路径：{document_path}")
    if resolved_delivery_dir.resolve() != delivery_dir.resolve():
        raise FinalDocumentDeliveryError(f"DOCX 引擎返回了非规范交付目录：{resolved_delivery_dir}")
    if quality_path.resolve() != canonical_quality_path.resolve():
        raise FinalDocumentDeliveryError(f"DOCX 引擎返回了非规范质量报告路径：{quality_path}")
    if not document_path.is_file():
        raise FinalDocumentDeliveryError(f"DOCX 引擎未产生可打开文档：{document_path}")
    if not resolved_delivery_dir.is_dir():
        raise FinalDocumentDeliveryError(f"DOCX 交付包目录不存在：{resolved_delivery_dir}")
    if not quality_path.is_file():
        raise FinalDocumentDeliveryError(f"DOCX 质量报告不存在：{quality_path}")

    try:
        binding_path, binding = write_binding_manifest(
            workspace_path,
            run_id=run_id,
            session_id=session_id,
            stage_id=stage_id,
            attempt=attempt,
            source_path=source_path,
            document_path=document_path,
            delivery_dir=resolved_delivery_dir,
            rich_package=(
                final_rich_package.get("package_binding")
                if isinstance(final_rich_package, dict)
                else None
            ),
        )
    except DeliveryIntegrityError as exc:
        raise FinalDocumentDeliveryError(str(exc)) from exc
    binding_display_path = _display_path(workspace_path, binding_path)

    created_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    quality_status = str(payload.get("quality_status") or "generated")
    artifacts = []
    if final_rich_package is not None:
        artifacts.append({
            "id": f"{stage_id}:{attempt}:final_rich_draft",
            "kind": "final_rich_draft",
            "label": "最终富内容稿",
            "title": str(final_rich_package.get("title") or "最终富内容稿"),
            "path": str(final_rich_package.get("draft_path") or ""),
            "manifest_path": str(final_rich_package.get("manifest_path") or ""),
            "image_list_path": str(final_rich_package.get("image_list_path") or ""),
            "package_dir": str(final_rich_package.get("package_dir") or ""),
            "rich_source_path": str(final_rich_package.get("rich_source_path") or ""),
            "rich_source_sha256": str(final_rich_package.get("rich_source_sha256") or ""),
            "package_files": dict(final_rich_package.get("package_files") or {}),
            "package_binding": dict(final_rich_package.get("package_binding") or {}),
            "assets": list(final_rich_package.get("assets") or []),
            "exists": bool(
                str(final_rich_package.get("draft_path") or "")
                and (workspace_path / str(final_rich_package.get("draft_path") or "")).is_file()
            ),
            "attempt": attempt,
            "stage": stage_id,
            "status": "ready",
            "created_at": str(final_rich_package.get("created_at") or created_at),
            "run_id": binding["run_id"],
            "session_id": binding["session_id"],
            "source_sha256": binding["source_sha256"],
            "document_sha256": binding["document_sha256"],
            "binding_manifest_path": binding_display_path,
        })
    artifacts.extend([
        _artifact(
            workspace_path,
            stage_id=stage_id,
            attempt=attempt,
            kind="final_document",
            label="最终 DOCX",
            path=document_path,
            status="ready",
            created_at=created_at,
            binding=binding,
            binding_manifest_path=binding_display_path,
        ),
        _artifact(
            workspace_path,
            stage_id=stage_id,
            attempt=attempt,
            kind="delivery_package",
            label="完整交付包",
            path=resolved_delivery_dir,
            status="ready",
            created_at=created_at,
            binding=binding,
            binding_manifest_path=binding_display_path,
        ),
        _artifact(
            workspace_path,
            stage_id=stage_id,
            attempt=attempt,
            kind="quality_report",
            label="质量报告",
            path=quality_path,
            status=quality_status or "generated",
            created_at=created_at,
            binding=binding,
            binding_manifest_path=binding_display_path,
        ),
    ])
    artifact_by_kind = {str(item.get("kind") or ""): item for item in artifacts}
    return {
        "stage": stage_id,
        "attempt": attempt,
        "template_id": template_id,
        "source_path": _display_path(workspace_path, render_source_path),
        "raw_source_path": _display_path(workspace_path, source_path),
        "document_path": artifact_by_kind["final_document"]["path"],
        "delivery_dir": artifact_by_kind["delivery_package"]["path"],
        "quality_report_path": artifact_by_kind["quality_report"]["path"],
        "quality_status": quality_status,
        "quality_report": payload.get("quality_report") if isinstance(payload.get("quality_report"), dict) else {},
        "binding_manifest_path": binding_display_path,
        "source_sha256": binding["source_sha256"],
        "document_sha256": binding["document_sha256"],
        "rich_package": dict(binding.get("rich_package") or {}),
        "artifacts": artifacts,
    }
