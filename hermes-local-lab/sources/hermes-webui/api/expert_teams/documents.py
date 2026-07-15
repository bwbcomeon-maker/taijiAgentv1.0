"""Canonical DOCX delivery for final expert-team stages."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from api import docx_engine_v2

from .delivery_integrity import (
    DeliveryIntegrityError,
    canonical_attempt_root,
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


def write_semantic_gates_snapshot(
    delivery_dir: Path,
    *,
    brief: dict,
    artifact: dict,
    approved_inputs: list[dict],
) -> dict:
    """Evaluate enterprise semantics once and persist an immutable upstream report."""

    from .stage_artifacts import unresolved_quality_issues

    markdown = _normalized_markdown(artifact.get("deliverable_markdown"))
    headings = re.findall(r"(?m)^#\s+(.+?)\s*$", markdown)
    issues = []
    if headings != [str(brief.get("exact_title") or "")]:
        issues.append(_semantic_issue("title_mismatch", "document:h1", "正文唯一 H1 与确认标题不一致"))
    if artifact.get("artifact_type") not in {"reviewed_document", "reviewed_research_document"}:
        issues.append(_semantic_issue("document_type_mismatch", "artifact:type", "交付正文不是已复核文档"))
    payload = artifact.get("payload") if isinstance(artifact.get("payload"), dict) else {}
    payload_document_type = str(payload.get("document_type") or "").strip()
    if payload_document_type and payload_document_type != str(brief.get("document_type") or ""):
        issues.append(_semantic_issue("document_type_mismatch", "payload:document_type", "正文文种与确认 Brief 不一致"))
    if _WORKFLOW_TEXT.search(markdown):
        issues.append(_semantic_issue("workflow_text_leaked", "document:body", "正文包含内部阶段或专家协作话术"))
    if _PLACEHOLDER_TEXT.search(markdown):
        issues.append(_semantic_issue("placeholder_detected", "document:body", "正文包含未处置占位符"))
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
        "issues": issues,
    }
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


def prepare_canonical_delivery_inputs(
    workspace: Path,
    run: dict,
    *,
    stage_id: str,
    delivery_attempt: int,
    asset_manifest: dict | None = None,
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
    brief = run.get("document_brief") if isinstance(run.get("document_brief"), dict) else {}
    paths = write_canonical_snapshot(root, brief=brief, artifact=artifact)
    semantic = write_semantic_gates_snapshot(
        root,
        brief=brief,
        artifact=artifact,
        approved_inputs=artifact.get("input_refs") or [],
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
        "schema_version": "render-input-binding/v1",
        "brief": {
            "revision": int(brief.get("confirmed_revision") or 0),
            "sha256": str(brief.get("confirmed_sha256") or ""),
        },
        "canonical_artifact": {
            "artifact_id": str(artifact.get("artifact_id") or ""),
            "sha256": str(artifact.get("sha256") or ""),
        },
        "canonical_markdown_sha256": sha256_file(Path(canonical_document_path)),
        "asset_manifest_sha256": sha256_file(Path(asset_manifest_path)),
        "semantic_gates_sha256": sha256_file(Path(semantic_gates_path)),
        "template": template_identity,
        "renderer": renderer_identity,
    }
    for field in ("sha256",):
        if not _HEX64.fullmatch(payload["brief"][field]) or not _HEX64.fullmatch(payload["canonical_artifact"][field]):
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
        "canonical_artifact": render_input["canonical_artifact"],
        "canonical_markdown": {"path": "canonical/document.md", "sha256": render_input["canonical_markdown_sha256"]},
        "asset_manifest": {"path": "assets/asset-manifest.json", "sha256": render_input["asset_manifest_sha256"]},
        "semantic_gates": {"path": "reviews/semantic-gates.json", "sha256": render_input["semantic_gates_sha256"]},
        "template": render_input["template"],
        "renderer": render_input["renderer"],
        "document": {"path": "delivery/document.docx", "sha256": sha256_file(expected_document)},
        "automatic_quality_report": {"path": "delivery/quality-report.json", "sha256": sha256_file(expected_quality)},
        "layered_quality_report": {
            "path": "reviews/enterprise-quality-report.json",
            "sha256": sha256_file(layered_quality_path),
        },
    }
    _immutable_json(root / "expert-team-delivery.json", binding, label="delivery binding")
    return binding


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
