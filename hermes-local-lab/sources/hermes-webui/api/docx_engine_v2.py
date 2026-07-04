from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any


class DocxEngineV2Error(RuntimeError):
    pass


def engine_root() -> Path:
    configured = os.getenv("TAIJI_DOCX_ENGINE_V2_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "docx-engine-v2"


def run_engine(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if not node:
        raise FileNotFoundError("Node.js is not available for DOCX Engine V2")
    return subprocess.run(
        [node, *[str(arg) for arg in args]],
        cwd=str(cwd or engine_root()),
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )


def list_templates() -> dict[str, Any]:
    completed = run_engine([str(_engine_cli("list-templates.js")), "--json"])
    payload = _payload_from_completed(completed, default_code="template_list_failed")
    if completed.returncode != 0:
        raise DocxEngineV2Error(str(payload.get("message") or "DOCX Engine V2 template listing failed"))
    return payload


def create_job(payload: dict, workspace: Path) -> tuple[dict[str, Any], int]:
    workspace = Path(workspace).expanduser().resolve()
    template_id = _first_text(payload, "template_id", "templateId")
    if not template_id:
        templates_payload = list_templates()
        return {
            "ok": False,
            "code": "template_selection_required",
            "templates": templates_payload.get("templates", []),
        }, 400

    try:
        source_path = _resolve_workspace_path(
            workspace,
            _first_text(payload, "source_path", "sourcePath", "source"),
            field="source_path",
            must_exist=True,
        )
        out_dir = _resolve_workspace_path(
            workspace,
            _first_text(payload, "out_dir", "outDir") or f".docx-engine-v2/{uuid.uuid4().hex}",
            field="out_dir",
        )
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        args = [
            str(_engine_cli("run-job.js")),
            "--template-id",
            template_id,
            "--source",
            str(source_path),
            "--out-dir",
            str(out_dir),
            "--json",
        ]
        source_type = _first_text(payload, "source_type", "sourceType")
        if source_type:
            args.extend(["--source-type", source_type])
        asset_dir_raw = _first_text(payload, "asset_dir", "assetDir")
        if asset_dir_raw:
            asset_dir = _resolve_workspace_path(workspace, asset_dir_raw, field="asset_dir", must_exist=True)
            args.extend(["--asset-dir", str(asset_dir)])
    except (FileNotFoundError, OSError, ValueError) as exc:
        return _error_payload("validation_failed", str(exc)), 400

    completed = run_engine(args)
    engine_payload = _payload_from_completed(completed, default_code="render_failed")
    if completed.returncode != 0:
        return _known_failure_payload(engine_payload), _status_for_engine_failure(engine_payload)

    delivery_dir_raw = str(engine_payload.get("deliveryDir", "")).strip()
    delivery_dir = Path(delivery_dir_raw).expanduser() if delivery_dir_raw else Path()
    quality_report_path = delivery_dir / "quality-report.json" if delivery_dir_raw else Path()
    quality_report = _read_quality_report(quality_report_path)

    return {
        "ok": True,
        "job_id": engine_payload.get("jobId", ""),
        "delivery_dir": engine_payload.get("deliveryDir", ""),
        "document_path": engine_payload.get("documentPath", ""),
        "quality_status": engine_payload.get("qualityStatus", ""),
        "quality_report_path": str(quality_report_path) if str(quality_report_path) else "",
        "quality_report": quality_report,
    }, 200


def package_rich_draft(payload: dict, workspace: Path) -> tuple[dict[str, Any], int]:
    workspace = Path(workspace).expanduser().resolve()
    try:
        roots = _figure_adjustment_allowed_absolute_roots(workspace)
        source_path = _resolve_workspace_path(
            workspace,
            _first_text(payload, "source_path", "sourcePath", "source"),
            field="source_path",
            must_exist=True,
            allowed_absolute_roots=roots,
        )
        out_dir = _resolve_workspace_path(
            workspace,
            _first_text(payload, "out_dir", "outDir"),
            field="out_dir",
            allowed_absolute_roots=roots,
        )
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        args = [
            str(_engine_cli("package-rich-draft.js")),
            "--source",
            str(source_path),
            "--out-dir",
            str(out_dir),
        ]
        asset_dir_raw = _first_text(payload, "asset_dir", "assetDir")
        if asset_dir_raw:
            asset_dir = _resolve_workspace_path(
                workspace,
                asset_dir_raw,
                field="asset_dir",
                must_exist=True,
                allowed_absolute_roots=roots,
            )
            args.extend(["--asset-dir", str(asset_dir)])
    except (FileNotFoundError, OSError, ValueError) as exc:
        return _error_payload("validation_failed", str(exc)), 400

    completed = run_engine(args)
    if completed.returncode != 0:
        engine_payload = _payload_from_completed(completed, default_code="validation_failed")
        return _known_failure_payload(engine_payload), 400

    return {
        "ok": True,
        "action": "package",
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
        "source_path": _display_path(workspace, source_path),
        "out_dir": _display_path(workspace, out_dir),
    }, 200


def rerender_asset(payload: dict, workspace: Path) -> tuple[dict[str, Any], int]:
    workspace = Path(workspace).expanduser().resolve()
    figure_id = _first_text(payload, "figure_id", "figureId")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", figure_id or ""):
        return _error_payload("validation_failed", "figure_id must contain only letters, numbers, underscore, or dash"), 400

    try:
        manifest_raw = _first_text(payload, "manifest_path", "manifestPath")
        if not manifest_raw:
            delivery_raw = _first_text(payload, "delivery_dir", "deliveryDir")
            if not delivery_raw:
                return _error_payload("validation_failed", "delivery_dir or manifest_path is required"), 400
            manifest_raw = str(Path(delivery_raw) / "render-plan.json")
        manifest_path = _resolve_workspace_path(
            workspace,
            manifest_raw,
            field="manifest_path",
            must_exist=True,
            allowed_absolute_roots=_figure_adjustment_allowed_absolute_roots(workspace),
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        return _error_payload("validation_failed", str(exc)), 400

    completed = run_engine([
        str(_engine_cli("render-figure-asset.js")),
        "--manifest",
        str(manifest_path),
        "--figure-id",
        figure_id,
        "--json",
    ])
    engine_payload = _payload_from_completed(completed, default_code="validation_failed")
    if completed.returncode != 0:
        return _known_failure_payload(engine_payload), 400

    return {
        "ok": True,
        "figure_id": engine_payload.get("figureId", figure_id),
        "display_path": engine_payload.get("displayPath", ""),
        "output_path": engine_payload.get("outputPath", ""),
    }, 200


def replace_asset(payload: dict, workspace: Path) -> tuple[dict[str, Any], int]:
    workspace = Path(workspace).expanduser().resolve()
    figure_id = _first_text(payload, "figure_id", "figureId")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", figure_id or ""):
        return _error_payload("validation_failed", "figure_id must contain only letters, numbers, underscore, or dash"), 400

    try:
        roots = _figure_adjustment_allowed_absolute_roots(workspace)
        docx_path = _resolve_workspace_path(
            workspace,
            _first_text(payload, "docx_path", "docxPath", "docx"),
            field="docx_path",
            must_exist=True,
            allowed_absolute_roots=roots,
        )
        image_path = _resolve_workspace_path(
            workspace,
            _first_text(payload, "image_path", "imagePath", "image"),
            field="image_path",
            must_exist=True,
            allowed_absolute_roots=roots,
        )
        out_path = _resolve_workspace_path(
            workspace,
            _first_text(payload, "out_path", "outPath", "out"),
            field="out_path",
            allowed_absolute_roots=roots,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except (FileNotFoundError, OSError, ValueError) as exc:
        return _error_payload("validation_failed", str(exc)), 400

    completed = run_engine([
        str(_engine_cli("replace-asset.js")),
        "--docx",
        str(docx_path),
        "--figure-id",
        figure_id,
        "--image",
        str(image_path),
        "--out",
        str(out_path),
    ])
    engine_payload = _payload_from_completed(completed, default_code="validation_failed")
    if completed.returncode != 0:
        return _known_failure_payload(engine_payload), 400

    return {
        "ok": True,
        "figure_id": engine_payload.get("figureId", figure_id),
        "relationship_id": engine_payload.get("relationshipId", ""),
        "media_path": engine_payload.get("mediaPath", ""),
        "output_path": engine_payload.get("outputPath", str(out_path)),
    }, 200


def _engine_cli(name: str) -> Path:
    return engine_root() / "src" / "cli" / name


def _first_text(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _payload_from_completed(completed: subprocess.CompletedProcess[str], *, default_code: str) -> dict[str, Any]:
    stdout = (completed.stdout or "").strip()
    if stdout:
        try:
            parsed = json.loads(stdout.splitlines()[-1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return _error_payload(default_code, _safe_process_message(completed))


def _safe_process_message(completed: subprocess.CompletedProcess[str]) -> str:
    text = (completed.stderr or completed.stdout or "").strip()
    if not text:
        return "DOCX Engine V2 command failed"
    return text.splitlines()[-1][:500]


def _known_failure_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "code": str(payload.get("code") or "validation_failed"),
        "message": str(payload.get("message") or ""),
        "failures": payload.get("failures", []),
        "templates": payload.get("templates", []),
        "delivery_dir": payload.get("deliveryDir", payload.get("delivery_dir", "")),
    }


def _read_quality_report(report_path: Path) -> dict[str, Any]:
    if not report_path or not report_path.exists():
        return {}
    try:
        parsed = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    checks = parsed.get("checks") if isinstance(parsed.get("checks"), list) else []
    warnings = parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []
    failures = parsed.get("failures") if isinstance(parsed.get("failures"), list) else []
    return {
        "schemaVersion": parsed.get("schemaVersion", ""),
        "status": parsed.get("status", ""),
        "checks": checks[:20],
        "warnings": [str(item) for item in warnings[:20]],
        "failures": [str(item) for item in failures[:20]],
    }


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message}


def _status_for_engine_failure(payload: dict[str, Any]) -> int:
    code = str(payload.get("code") or "")
    if code in {"template_selection_required", "validation_failed"}:
        return 400
    return 500


def _resolve_workspace_path(
    workspace: Path,
    raw: str,
    *,
    field: str,
    must_exist: bool = False,
    allowed_absolute_roots: list[Path] | None = None,
) -> Path:
    if not raw:
        raise ValueError(f"{field} is required")
    root = Path(workspace).expanduser().resolve()
    requested = Path(os.path.expandvars(raw)).expanduser()
    if requested.is_absolute():
        target = requested.resolve()
    else:
        target = (root / requested).resolve()

    allowed_roots = [root]
    if allowed_absolute_roots:
        allowed_roots.extend(Path(item).expanduser().resolve() for item in allowed_absolute_roots)
    if not any(_path_is_within(target, allowed_root) for allowed_root in allowed_roots):
        raise ValueError(f"{field} is outside the allowed local roots: {target}")
    if must_exist and not target.exists():
        raise FileNotFoundError(f"{field} not found: {target}")
    return target


def _path_is_within(target: Path, root: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _display_path(workspace: Path, target: Path) -> str:
    try:
        return str(target.resolve().relative_to(workspace.resolve()))
    except (OSError, ValueError):
        return str(target)


def _figure_adjustment_allowed_absolute_roots(workspace: Path) -> list[Path]:
    roots = [Path(workspace).expanduser().resolve()]
    try:
        roots.append(Path.home().expanduser().resolve())
    except OSError:
        pass
    return roots
