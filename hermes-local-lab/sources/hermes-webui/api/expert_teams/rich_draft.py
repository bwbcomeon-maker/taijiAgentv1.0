"""Rich draft contract for plan-like expert-team outputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path


PLAN_LIKE_MATERIAL_TYPES = {"plan", "research_report"}
PLAN_LIKE_TASK_IDS = {"draft"}
MIN_TABLES = 2
MIN_FIGURES = 1


def is_rich_draft_required(material_type: str, task_id: str, team_id: str = "") -> bool:
    material = str(material_type or "").strip()
    task = str(task_id or "").strip()
    return material in PLAN_LIKE_MATERIAL_TYPES and task in PLAN_LIKE_TASK_IDS


def markdown_table_count(text: str) -> int:
    lines = (text or "").splitlines()
    count = 0
    for index in range(len(lines) - 1):
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        if "|" not in header or "|" not in separator:
            continue
        if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", separator):
            count += 1
    return count


def figure_reference_count(text: str) -> int:
    raw = text or ""
    markdown_images = len(re.findall(r"!\[[^\]]+\]\([^)]+\)", raw))
    mermaid_blocks = len(re.findall(r"```\s*mermaid\b", raw, flags=re.IGNORECASE))
    return markdown_images + mermaid_blocks


def validate_rich_draft_text(text: str) -> dict:
    tables = markdown_table_count(text)
    figures = figure_reference_count(text)
    missing = []
    if tables < MIN_TABLES:
        missing.append(f"至少 {MIN_TABLES} 个 Markdown 表格")
    if figures < MIN_FIGURES:
        missing.append("至少 1 个架构图、流程图、用例图或图示引用")
    if missing:
        return {
            "status": "rewrite_required",
            "violations": [],
            "missing_sections": missing,
            "message": "方案类初稿必须在生成阶段包含表格和图示，请重新生成富内容初稿：" + "、".join(missing) + "。",
        }
    return {"status": "pass", "violations": [], "missing_sections": [], "message": ""}


class RichDraftPackagingError(RuntimeError):
    """The canonical rich-draft package could not be produced or verified."""


def _workspace_path(workspace: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except (OSError, ValueError):
        return str(path.resolve())


def _verified_package(
    workspace: Path,
    package_dir: Path,
    title: str,
    *,
    source_path: Path | None = None,
) -> dict:
    import json

    from .delivery_integrity import path_contains_symlink, sha256_file, workspace_relative_path

    workspace_root = Path(workspace).expanduser().resolve()
    package_root = Path(package_dir).expanduser().absolute()
    if path_contains_symlink(workspace_root, package_root):
        raise RichDraftPackagingError("富内容初稿包目录不能包含符号链接")
    try:
        package_root.resolve().relative_to(workspace_root)
    except (OSError, ValueError) as exc:
        raise RichDraftPackagingError("富内容初稿包必须位于当前工作区") from exc

    def declared_path(raw_value: object, field: str, *, directory: bool = False) -> Path:
        raw = str(raw_value or "").strip()
        relative = Path(raw)
        if not raw or relative.is_absolute() or ".." in relative.parts:
            raise RichDraftPackagingError(f"富内容初稿 {field} 必须是包内相对路径")
        target = package_root / relative
        try:
            target.resolve().relative_to(package_root.resolve())
        except (OSError, ValueError) as exc:
            raise RichDraftPackagingError(f"富内容初稿 {field} 越过了包目录") from exc
        if path_contains_symlink(workspace_root, target):
            raise RichDraftPackagingError(f"富内容初稿 {field} 路径包含符号链接")
        exists = target.is_dir() if directory else target.is_file()
        if not exists:
            expected = "目录" if directory else "文件"
            raise RichDraftPackagingError(f"富内容初稿 {field} 缺少可读{expected}")
        return target

    manifest_path = package_root / "draft.manifest.json"
    if path_contains_symlink(workspace_root, manifest_path) or not manifest_path.is_file():
        raise RichDraftPackagingError("富内容初稿 manifest 不可读或包含符号链接")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RichDraftPackagingError(f"富内容初稿 manifest 不可读：{exc}") from exc
    if manifest.get("schemaVersion") != "rich-draft-package/v2":
        raise RichDraftPackagingError("富内容初稿必须使用 rich-draft-package/v2 契约")
    input_source_sha256 = str(manifest.get("inputSourceSha256") or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", input_source_sha256):
        raise RichDraftPackagingError("富内容初稿缺少规范输入源摘要")
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    draft_path = declared_path(files.get("markdown"), "files.markdown")
    declared_manifest = declared_path(files.get("manifest"), "files.manifest")
    image_list_path = declared_path(files.get("imageList"), "files.imageList")
    if declared_manifest != manifest_path:
        raise RichDraftPackagingError("富内容初稿 files.manifest 必须指向 draft.manifest.json")

    provenance_raw = str(manifest.get("sourcePath") or "").strip()
    provenance = Path(provenance_raw).expanduser()
    if not provenance_raw:
        raise RichDraftPackagingError("富内容初稿 sourcePath 缺失")
    if not provenance.is_absolute():
        provenance = package_root.parent / provenance
    provenance = provenance.absolute()
    expected_source = Path(source_path).expanduser().absolute() if source_path is not None else package_root.parent / "draft.md"
    if provenance.resolve() != expected_source.resolve() or provenance.resolve() != (package_root.parent / "draft.md").resolve():
        raise RichDraftPackagingError("富内容初稿 sourcePath 与本次 run/stage/attempt 的规范 draft.md 不一致")
    if path_contains_symlink(workspace_root, provenance) or not provenance.is_file():
        raise RichDraftPackagingError("富内容初稿 sourcePath 不可读或包含符号链接")
    provenance_sha256 = sha256_file(provenance)
    if input_source_sha256 != provenance_sha256:
        raise RichDraftPackagingError("富内容初稿输入源摘要与规范源稿不一致")

    figures = manifest.get("figures") if isinstance(manifest.get("figures"), list) else []
    assets = []
    declared_files = {manifest_path, draft_path, image_list_path}
    figure_ids: set[str] = set()
    for figure in figures:
        if not isinstance(figure, dict):
            raise RichDraftPackagingError("富内容初稿 figures 包含非法条目")
        figure_id = str(figure.get("figureId") or "").strip()
        if not figure_id or figure_id in figure_ids:
            raise RichDraftPackagingError("富内容初稿 figureId 缺失或重复")
        figure_ids.add(figure_id)
        if figure.get("assetDir"):
            declared_path(figure.get("assetDir"), f"figures.{figure_id}.assetDir", directory=True)
        display = declared_path(figure.get("displayPath"), f"figures.{figure_id}.displayPath")
        declared_files.add(display)
        declared_source = declared_path(figure.get("sourcePath"), f"figures.{figure_id}.sourcePath")
        declared_files.add(declared_source)
        editable = figure.get("editable") if isinstance(figure.get("editable"), dict) else {}
        source = declared_path(
            editable.get("sourcePath"),
            f"figures.{figure_id}.editable.sourcePath",
        )
        declared_files.add(source)
        metadata = figure.get("metadata") if isinstance(figure.get("metadata"), dict) else {}
        if metadata.get("vectorDisplayPath"):
            declared_files.add(
                declared_path(
                    metadata.get("vectorDisplayPath"),
                    f"figures.{figure_id}.metadata.vectorDisplayPath",
                )
            )
        assets.append(
            {
                "id": figure_id,
                "kind": "figure",
                "title": str(figure.get("caption") or "图示"),
                "path": workspace_relative_path(workspace_root, display),
                "sha256": sha256_file(display),
                "source_path": workspace_relative_path(workspace_root, source),
                "source_sha256": sha256_file(source),
                "status": "ready",
                "exists": True,
            }
        )
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    if not assets:
        raise RichDraftPackagingError("富内容初稿缺少可追溯图片资产")
    package_files = {
        path.relative_to(package_root).as_posix(): sha256_file(path)
        for path in sorted(declared_files, key=lambda item: item.as_posix())
    }
    package_binding = {
        "schema_version": 1,
        "package_dir": workspace_relative_path(workspace_root, package_root),
        "source_path": workspace_relative_path(workspace_root, provenance),
        "source_sha256": provenance_sha256,
        "input_source_sha256": input_source_sha256,
        "draft_path": workspace_relative_path(workspace_root, draft_path),
        "manifest_path": workspace_relative_path(workspace_root, manifest_path),
        "image_list_path": workspace_relative_path(workspace_root, image_list_path),
        "files": package_files,
    }
    return {
        "version": 2,
        "schema_version": "rich-draft-package/v2",
        "kind": "rich_draft",
        "title": str(manifest.get("title") or title or "富内容初稿"),
        "created_at": str(manifest.get("createdAt") or ""),
        "draft_path": workspace_relative_path(workspace_root, draft_path),
        "manifest_path": workspace_relative_path(workspace_root, manifest_path),
        "image_list_path": workspace_relative_path(workspace_root, image_list_path),
        "package_dir": workspace_relative_path(workspace_root, package_root),
        "rich_source_path": workspace_relative_path(workspace_root, provenance),
        "rich_source_sha256": package_binding["source_sha256"],
        "package_files": package_files,
        "package_binding": package_binding,
        "table_count": int(quality.get("tables") or len(manifest.get("tables") or [])),
        "figure_count": int(quality.get("figures") or len(figures)),
        "assets": assets,
    }


def verify_rich_draft_package(
    workspace: Path,
    package_dir: Path,
    *,
    source_path: Path,
    title: str = "",
) -> dict:
    return _verified_package(
        Path(workspace),
        Path(package_dir),
        title,
        source_path=Path(source_path),
    )


def _publish_rich_draft_candidate(
    workspace: Path,
    *,
    candidate_dir: Path,
    package_dir: Path,
    source_path: Path,
    title: str,
) -> dict:
    """Replace the visible package only after a fresh candidate verifies."""
    backup_dir: Path | None = None
    published = False
    try:
        if package_dir.exists():
            if package_dir.is_symlink() or not package_dir.is_dir():
                raise RichDraftPackagingError("已有富内容初稿包路径不是可信目录")
            backup_dir = Path(
                tempfile.mkdtemp(prefix=".package-backup-", dir=package_dir.parent)
            )
            backup_dir.rmdir()
            os.replace(package_dir, backup_dir)
        os.replace(candidate_dir, package_dir)
        published = True
        result = _verified_package(workspace, package_dir, title, source_path=source_path)
    except BaseException:
        failed_dir: Path | None = None
        if published and package_dir.exists():
            failed_dir = Path(
                tempfile.mkdtemp(prefix=".package-failed-", dir=package_dir.parent)
            )
            failed_dir.rmdir()
            os.replace(package_dir, failed_dir)
        if backup_dir is not None and backup_dir.exists() and not package_dir.exists():
            os.replace(backup_dir, package_dir)
        if failed_dir is not None:
            shutil.rmtree(failed_dir, ignore_errors=True)
        raise
    else:
        if backup_dir is not None:
            shutil.rmtree(backup_dir, ignore_errors=True)
        return result


def build_rich_draft_package(workspace: Path, run: dict, output: dict) -> dict:
    from api import docx_engine_v2
    from .delivery_integrity import delivery_attempt_lock, safe_stage_id
    from .storage import safe_run_id

    workspace_path = Path(workspace).expanduser().resolve()
    run_id = safe_run_id(str(run.get("run_id") or ""))
    stage_id = safe_stage_id(str(output.get("stage_id") or output.get("task_id") or "draft"))
    attempt = max(1, int(output.get("stage_attempt") or output.get("attempt") or 1))
    attempt_root = (
        workspace_path
        / ".taiji"
        / "rich-drafts"
        / run_id
        / stage_id
        / f"attempt-{attempt}"
    )
    package_dir = attempt_root / "package"
    source_path = attempt_root / "draft.md"
    title = str(output.get("title") or run.get("title") or "方案初稿")
    content = str(output.get("content") or "").strip()
    if not content:
        raise RichDraftPackagingError("富内容初稿 Markdown 为空")
    expected_source = content + "\n"
    input_source_sha256 = hashlib.sha256(expected_source.encode("utf-8")).hexdigest()
    package_created_at = str(run.get("created_at") or "1970-01-01T00:00:00.000Z")
    with delivery_attempt_lock(workspace_path, run_id, stage_id, attempt):
        attempt_root.mkdir(parents=True, exist_ok=True)
        if source_path.exists() and source_path.read_text(encoding="utf-8") != expected_source:
            raise RichDraftPackagingError("同一阶段 attempt 的 Markdown 与当前输入不一致")
        if not source_path.exists():
            source_path.write_text(expected_source, encoding="utf-8")

        existing_manifest_path = package_dir / "draft.manifest.json"
        if existing_manifest_path.is_file() and not existing_manifest_path.is_symlink():
            try:
                existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing_manifest = {}
            existing_input_hash = str(existing_manifest.get("inputSourceSha256") or "").lower()
            if re.fullmatch(r"[a-f0-9]{64}", existing_input_hash) and existing_input_hash != input_source_sha256:
                raise RichDraftPackagingError("同一阶段 attempt 的输入 Markdown 已变化，拒绝复用旧包")

        candidate_dir = Path(
            tempfile.mkdtemp(prefix=".package-build-", dir=attempt_root)
        )
        try:
            payload, status = docx_engine_v2.package_rich_draft(
                {
                    "source_path": _workspace_path(workspace_path, source_path),
                    "out_dir": _workspace_path(workspace_path, candidate_dir),
                    "asset_dir": ".",
                },
                workspace_path,
            )
            if status != 200 or not payload.get("ok"):
                raise RichDraftPackagingError(
                    str(payload.get("message") or payload.get("stderr") or "富内容初稿打包失败")
                )
            candidate_manifest_path = candidate_dir / "draft.manifest.json"
            try:
                generated_manifest = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RichDraftPackagingError(f"新生成富内容初稿 manifest 不可读：{exc}") from exc
            packaged_markdown = candidate_dir / str(
                (generated_manifest.get("files") or {}).get("markdown") or ""
            )
            if (
                packaged_markdown != candidate_dir / source_path.name
                or packaged_markdown.is_symlink()
                or not packaged_markdown.is_file()
            ):
                raise RichDraftPackagingError("新生成富内容初稿 Markdown 路径不规范")
            generated_manifest["inputSourceSha256"] = input_source_sha256
            generated_manifest["createdAt"] = package_created_at
            candidate_manifest_path.write_text(
                json.dumps(generated_manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            _verified_package(
                workspace_path,
                candidate_dir,
                title,
                source_path=source_path,
            )
            return _publish_rich_draft_candidate(
                workspace_path,
                candidate_dir=candidate_dir,
                package_dir=package_dir,
                source_path=source_path,
                title=title,
            )
        finally:
            shutil.rmtree(candidate_dir, ignore_errors=True)
