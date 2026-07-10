"""Canonical DOCX delivery for final expert-team stages."""

from __future__ import annotations

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
