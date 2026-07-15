from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class DocxEngineV2Error(RuntimeError):
    pass


class _ExpertDeliveryImmutable(DocxEngineV2Error):
    pass


_ACTIVE_OFFICE_REVIEW_TOKENS: dict[tuple[str, str, str], dict[str, Any]] = {}
_ACTIVE_OFFICE_REVIEW_TOKENS_LOCK = threading.RLock()
_ACTIVE_OFFICE_REVIEW_TOKEN_TTL_NS = 15 * 60 * 1_000_000_000
_ACTIVE_OFFICE_REVIEW_TOKEN_CAPACITY = 128


def _office_review_token_key(workspace: Path, run: dict) -> tuple[str, str, str]:
    return (
        str(Path(workspace).expanduser().resolve()),
        str(run.get("session_id") or "").strip(),
        str(run.get("run_id") or "").strip(),
    )


def _current_office_delivery_attempt(run: dict) -> int:
    ref = run.get("current_delivery_manifest_ref") if isinstance(run.get("current_delivery_manifest_ref"), dict) else {}
    attempt = int(ref.get("delivery_attempt") or 0)
    if attempt:
        return attempt
    attempts = [int(item.get("attempt") or 0) for item in run.get("artifacts") or [] if isinstance(item, dict) and item.get("stage") == "delivery" and item.get("kind") == "delivery_package"]
    return max(attempts, default=0)


def _prune_active_office_review_tokens_locked(now_ns: int) -> None:
    expired = [key for key, item in _ACTIVE_OFFICE_REVIEW_TOKENS.items() if int(item.get("expires_at_ns") or 0) <= now_ns]
    for key in expired:
        _ACTIVE_OFFICE_REVIEW_TOKENS.pop(key, None)
    overflow = len(_ACTIVE_OFFICE_REVIEW_TOKENS) - _ACTIVE_OFFICE_REVIEW_TOKEN_CAPACITY
    if overflow > 0:
        oldest = sorted(_ACTIVE_OFFICE_REVIEW_TOKENS, key=lambda key: int(_ACTIVE_OFFICE_REVIEW_TOKENS[key].get("created_at_ns") or 0))
        for key in oldest[:overflow]:
            _ACTIVE_OFFICE_REVIEW_TOKENS.pop(key, None)


def _remember_active_office_review_token(workspace: Path, run: dict, token: str, *, expires_at_ns: int | None = None) -> None:
    key = _office_review_token_key(workspace, run)
    if not all(key) or not str(token or "").strip():
        raise ValueError("active Office review identity is incomplete")
    now_ns = time.time_ns()
    with _ACTIVE_OFFICE_REVIEW_TOKENS_LOCK:
        _prune_active_office_review_tokens_locked(now_ns)
        _ACTIVE_OFFICE_REVIEW_TOKENS[key] = {
            "token": str(token).strip(), "created_at_ns": now_ns,
            "expires_at_ns": int(expires_at_ns or now_ns + _ACTIVE_OFFICE_REVIEW_TOKEN_TTL_NS),
        }
        _prune_active_office_review_tokens_locked(now_ns)


def _forget_active_office_review_token(key: tuple[str, str, str] | None) -> None:
    if key:
        with _ACTIVE_OFFICE_REVIEW_TOKENS_LOCK:
            _ACTIVE_OFFICE_REVIEW_TOKENS.pop(key, None)


def abandon_active_office_review(workspace: Path, run: dict) -> None:
    _forget_active_office_review_token(_office_review_token_key(workspace, run))


def active_office_review_session_status(workspace: Path, run: dict) -> str:
    key = _office_review_token_key(workspace, run)
    now_ns = time.time_ns()
    with _ACTIVE_OFFICE_REVIEW_TOKENS_LOCK:
        _prune_active_office_review_tokens_locked(now_ns)
        return "ready" if key in _ACTIVE_OFFICE_REVIEW_TOKENS else "begin_required"


def upload_structured_office_evidence(payload: dict, files: dict, workspace: Path, *, trusted_principal: dict | None = None) -> tuple[dict[str, Any], int]:
    """Store evidence in the active server-owned Office review directory."""
    import hashlib
    from api.expert_teams.delivery_integrity import canonical_delivery_dir, delivery_identity_from_directory, office_binding_identity, validated_binding_for_identity
    from api.expert_teams.office_review import load_review_token
    from api.expert_teams.storage import read_run

    root = Path(workspace).expanduser().resolve()
    unexpected = sorted(set(payload) - {"session_id", "run_id", "expected_version"})
    if unexpected:
        return _error_payload("office_evidence_request_invalid", f"unsupported fields: {', '.join(unexpected)}"), 400
    principal = trusted_principal if isinstance(trusted_principal, dict) else {}
    if "document-reviewer" not in (principal.get("roles") or []) or not str(principal.get("subject") or ""):
        return _error_payload("trusted_reviewer_required", "trusted document reviewer is required"), 403
    try:
        session_id = _first_text(payload, "session_id")
        run = read_run(root, _first_text(payload, "run_id"))
        if str(run.get("session_id") or "") != session_id:
            raise ValueError("Office evidence run identity mismatch")
        if int(payload.get("expected_version") or -1) != int(run.get("version") or 0):
            raise ValueError("Office evidence version conflict")
        attempt = _current_office_delivery_attempt(run)
        if attempt < 1:
            raise ValueError("Office evidence has no current delivery")
        registry_key = _office_review_token_key(root, run)
        with _ACTIVE_OFFICE_REVIEW_TOKENS_LOCK:
            _prune_active_office_review_tokens_locked(time.time_ns())
            token = str((_ACTIVE_OFFICE_REVIEW_TOKENS.get(registry_key) or {}).get("token") or "")
        if not token:
            return _error_payload("rebegin_required", "active Office review session is missing or expired"), 409
        delivery_dir = canonical_delivery_dir(root, str(run.get("run_id") or ""), "delivery", attempt)
        identity = delivery_identity_from_directory(root, delivery_dir)
        if identity is None:
            raise ValueError("Office evidence delivery identity is invalid")
        office_binding = office_binding_identity(root, identity, validated_binding_for_identity(root, identity))
        try:
            token_state, _token_path = load_review_token(root, token, binding=office_binding)
        except Exception as exc:
            if "expired" in str(exc).lower() or "already used" in str(exc).lower():
                _forget_active_office_review_token(registry_key)
                return _error_payload("rebegin_required", "active Office review session is missing or expired"), 409
            raise
        reviewer = token_state.get("reviewer_identity") if isinstance(token_state.get("reviewer_identity"), dict) else {}
        if str(reviewer.get("subject") or "") != str(principal.get("subject") or ""):
            return _error_payload("trusted_reviewer_mismatch", "active Office reviewer does not match authenticated principal"), 403
        rows = list(files.values()) if isinstance(files, dict) else []
        if not rows or len(rows) > 5:
            raise ValueError("Office evidence requires 1 to 5 files per upload")
        evidence_dir = root / str(token_state.get("evidence_dir") or "")
        expected_dir = root / ".taiji" / "wps-evidence" / str(token_state.get("token_hash") or "")
        if evidence_dir.resolve() != expected_dir.resolve() or not evidence_dir.is_dir() or evidence_dir.is_symlink():
            raise ValueError("Office evidence directory is invalid")
        existing = [item for item in evidence_dir.iterdir() if item.is_file() and not item.name.startswith(".upload-")]
        if len(existing) + len(rows) > 10:
            raise ValueError("Office evidence file count exceeds 10")
        prepared = []
        allowed_suffixes = {".png", ".jpg", ".jpeg", ".pdf"}
        executable_suffixes = {".exe", ".dll", ".com", ".bat", ".cmd", ".sh", ".app", ".js", ".jar", ".msi"}
        for row in rows:
            if not isinstance(row, tuple) or len(row) != 2:
                raise ValueError("Office evidence multipart file is invalid")
            original, content = str(row[0] or ""), row[1]
            if not isinstance(content, bytes) or not content:
                raise ValueError("Office evidence file is empty")
            if "\x00" in original or "/" in original or "\\" in original or Path(original).name != original:
                raise ValueError("Office evidence filename is unsafe")
            suffix = Path(original).suffix.lower()
            if suffix in executable_suffixes or suffix not in allowed_suffixes:
                raise ValueError("Office evidence file type is not allowed")
            if len(content) > 8 * 1024 * 1024:
                raise ValueError("Office evidence file exceeds 8 MB")
            if content.startswith((b"MZ", b"\x7fELF", b"#!")):
                raise ValueError("Office evidence executable content is not allowed")
            valid_magic = (suffix == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n")) or (suffix in {".jpg", ".jpeg"} and content.startswith(b"\xff\xd8\xff")) or (suffix == ".pdf" and content.startswith(b"%PDF-"))
            if not valid_magic:
                raise ValueError("Office evidence content does not match its file type")
            prepared.append((f"office-{uuid.uuid4().hex[:12]}{suffix}", content, hashlib.sha256(content).hexdigest()))
        if sum(item.stat().st_size for item in existing) + sum(len(item[1]) for item in prepared) > 20 * 1024 * 1024:
            raise ValueError("Office evidence total size exceeds 20 MB")
        written = []
        for safe_name, content, digest in prepared:
            target = evidence_dir / safe_name
            temporary = evidence_dir / f".upload-{uuid.uuid4().hex}.tmp"
            try:
                with temporary.open("xb") as handle:
                    handle.write(content); handle.flush(); os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            written.append({"name": safe_name, "sha256_short": digest[:12], "size_bytes": len(content)})
        return {"ok": True, "count": len(existing) + len(written), "uploaded_count": len(written), "files": written}, 200
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        conflict = "version conflict" in str(exc).lower()
        return _error_payload("office_evidence_version_conflict" if conflict else "office_evidence_invalid", str(exc)), 409 if conflict else 400


def _expand_structured_office_submission(
    payload: dict,
    workspace: Path,
    *,
    trusted_principal: dict | None,
) -> dict:
    import hashlib
    from api.expert_teams.delivery_integrity import canonical_delivery_dir
    from api.expert_teams.storage import read_run

    allowed = {
        "session_id", "run_id", "expected_version", "status", "checklist", "issues", "note", "idempotency_key",
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(f"structured Office submission contains unsupported fields: {', '.join(unexpected)}")
    principal = trusted_principal if isinstance(trusted_principal, dict) else {}
    if "document-reviewer" not in (principal.get("roles") or []):
        raise ValueError("trusted document reviewer is required")
    root = Path(workspace).expanduser().resolve()
    idempotency_key = _first_text(payload, "idempotency_key")
    if not re.fullmatch(r"[A-Za-z0-9:._-]{8,240}", idempotency_key):
        raise ValueError("Office acceptance idempotency_key is invalid")
    request_fingerprint = _structured_office_request_fingerprint(payload)
    session_id = _first_text(payload, "session_id")
    run_id = _first_text(payload, "run_id")
    run = read_run(root, run_id)
    if str(run.get("session_id") or "") != session_id:
        raise ValueError("structured Office submission session does not match run")
    if int(payload.get("expected_version") or -1) != int(run.get("version") or 0):
        raise ValueError("structured Office submission version conflict")
    attempt = _current_office_delivery_attempt(run)
    if attempt < 1:
        raise ValueError("structured Office submission has no current delivery")
    key = _office_review_token_key(root, run)
    with _ACTIVE_OFFICE_REVIEW_TOKENS_LOCK:
        _prune_active_office_review_tokens_locked(time.time_ns())
        token = str((_ACTIVE_OFFICE_REVIEW_TOKENS.get(key) or {}).get("token") or "")
    if not token:
        raise ValueError("rebegin_required: active Office review session is missing or expired")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    evidence_dir = root / ".taiji" / "wps-evidence" / token_hash
    evidence_files = sorted(
        str(path) for path in evidence_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".pdf"}
    ) if evidence_dir.is_dir() else []
    if not evidence_files:
        raise ValueError("active Office review evidence is required")
    checklist = payload.get("checklist") if isinstance(payload.get("checklist"), dict) else {}
    visual_checks = []
    mapping = {
        "document_opened": "document_opened", "title_and_cover_match": "layout_reviewed",
        "headers_footers_pagination": "layout_reviewed", "genre_and_structure_match": "content_order_reviewed",
        "content_order_correct": "content_order_reviewed", "no_placeholders_or_workflow_text": "content_order_reviewed",
        "figures_unique_and_readable": "figures_reviewed", "tables_readable": "tables_reviewed",
        "citations_readable": "citations_reviewed",
    }
    for key_name, check_name in mapping.items():
        if checklist.get(key_name) == "passed" and check_name not in visual_checks:
            visual_checks.append(check_name)
    return {
        "session_id": session_id,
        "delivery_dir": str(canonical_delivery_dir(root, run_id, "delivery", attempt)),
        "status": _first_text(payload, "status"),
        "note": _first_text(payload, "note"),
        "visual_checks": visual_checks,
        "issues": payload.get("issues") if isinstance(payload.get("issues"), list) else [],
        "review_token": token,
        "evidence_files": evidence_files,
        "attested_actual_office_review": True,
        "idempotency_key": idempotency_key,
        "request_fingerprint": request_fingerprint,
    }


def _structured_office_request_fingerprint(payload: dict) -> str:
    import hashlib
    identity = {key: payload.get(key) for key in ("session_id", "run_id", "expected_version", "status", "checklist", "issues", "note")}
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _structured_office_acceptance_replay(payload: dict, workspace: Path) -> tuple[dict[str, Any], int] | None:
    from datetime import datetime, timezone
    from api.expert_teams.delivery_integrity import canonical_attempt_root, read_binding_manifest, sha256_file
    from api.expert_teams.office_review import (
        OFFICE_ACCEPTANCE_NAME,
        _token_state_path,
        consume_review_token,
        enterprise_completion_status,
        prepare_consumed_review_state,
        reconcile_enterprise_completion,
    )
    from api.expert_teams.storage import read_run
    key = _first_text(payload, "idempotency_key")
    if not re.fullmatch(r"[A-Za-z0-9:._-]{8,240}", key):
        return _error_payload("office_acceptance_idempotency_invalid", "Office acceptance idempotency_key is invalid"), 400
    run = read_run(workspace, _first_text(payload, "run_id"))
    attempt = _current_office_delivery_attempt(run)
    path = canonical_attempt_root(workspace, str(run.get("run_id") or ""), "delivery", attempt) / OFFICE_ACCEPTANCE_NAME
    if not path.is_file():
        return None
    acceptance = json.loads(path.read_text(encoding="utf-8"))
    request = acceptance.get("request") if isinstance(acceptance.get("request"), dict) else {}
    if str(request.get("idempotency_key") or "") != key:
        return _error_payload("office_acceptance_idempotency_conflict", "Office acceptance already exists for another request"), 409
    if str(request.get("fingerprint") or "") != _structured_office_request_fingerprint(payload):
        return _error_payload("office_acceptance_idempotency_conflict", "Office acceptance idempotency key was reused with different input"), 409
    try:
        token_hash = str((acceptance.get("token_provenance") or {}).get("token_hash") or "")
        if token_hash:
            token_path = _token_state_path(workspace, token_hash)
            if not token_path.is_file():
                raise ValueError("prepared Office acceptance token state is missing")
            token_state = json.loads(token_path.read_text(encoding="utf-8"))
            if token_state.get("state") != "consumed":
                consumed = prepare_consumed_review_state(
                    token_state,
                    acceptance_manifest_path=str(path.relative_to(Path(workspace).resolve())),
                    acceptance_manifest_sha256=sha256_file(path),
                    canonical_evidence=acceptance.get("evidence") if isinstance(acceptance.get("evidence"), list) else [],
                )
                consume_review_token(token_path, consumed)
            _forget_active_office_review_token(_office_review_token_key(workspace, run))
            if str(acceptance.get("decision") or "") in {"passed", "passed_with_conditions"}:
                status = enterprise_completion_status(workspace, run)
                if status.get("status") != "passed":
                    ref = run.get("current_delivery_manifest_ref") if isinstance(run.get("current_delivery_manifest_ref"), dict) else {}
                    binding_path = Path(workspace).resolve() / str(ref.get("delivery_binding_path") or "")
                    binding = read_binding_manifest(binding_path)
                    run = reconcile_enterprise_completion(
                        workspace,
                        run=run,
                        binding=binding,
                        binding_sha256=sha256_file(binding_path),
                        now=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                    )
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        return _error_payload("office_acceptance_reconcile_failed", str(exc)), 500
    return {
        "ok": True, "office_status": str(acceptance.get("decision") or ""), "acceptance": acceptance,
        "acceptance_manifest_path": str(path.relative_to(Path(workspace).resolve())),
        "reviewer": str((acceptance.get("reviewer") or {}).get("principal_id") or ""), "idempotent_replay": True,
    }, 200


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


def describe_renderer_identity(profile_id: str = "enterprise-default") -> dict[str, Any]:
    """Return the exact side-effect-free renderer build/profile identity."""

    completed = run_engine(
        [str(_engine_cli("describe-renderer.js")), "--profile-id", str(profile_id or "enterprise-default")]
    )
    payload = _payload_from_completed(completed, default_code="renderer_identity_unavailable")
    identity = payload.get("rendererIdentity")
    if completed.returncode != 0 or not isinstance(identity, dict):
        raise DocxEngineV2Error(str(payload.get("message") or "Renderer identity is unavailable"))
    return identity


def install_template(payload: dict, workspace: Path) -> tuple[dict[str, Any], int]:
    workspace = Path(workspace).expanduser().resolve()
    try:
        package_dir = _resolve_workspace_path(
            workspace,
            _first_text(payload, "package_path", "packagePath", "package_dir", "packageDir", "package"),
            field="package_path",
            must_exist=True,
            allowed_absolute_roots=_figure_adjustment_allowed_absolute_roots(workspace),
        )
        if not package_dir.is_dir():
            return _error_payload("validation_failed", f"package_path must be a directory: {package_dir}"), 400
    except (FileNotFoundError, OSError, ValueError) as exc:
        return _error_payload("validation_failed", str(exc)), 400

    completed = run_engine([
        str(_engine_cli("install-template.js")),
        "--package",
        str(package_dir),
        "--json",
        *([] if not _first_bool(payload, "replace_existing", "replaceExisting", "replace") else ["--replace"]),
    ])
    engine_payload = _payload_from_completed(completed, default_code="template_install_failed")
    if completed.returncode != 0:
        return _known_failure_payload(engine_payload), _status_for_engine_failure(engine_payload)

    templates: list[dict[str, Any]] = []
    template_list_warning = ""
    try:
        templates_payload = list_templates()
        templates = templates_payload.get("templates", []) if isinstance(templates_payload.get("templates"), list) else []
    except (FileNotFoundError, subprocess.TimeoutExpired, DocxEngineV2Error) as exc:
        template_list_warning = str(exc)

    result: dict[str, Any] = {
        "ok": True,
        "action": engine_payload.get("action", "installed"),
        "template_id": engine_payload.get("templateId", engine_payload.get("template_id", "")),
        "package_dir": engine_payload.get("packageDir", engine_payload.get("package_dir", str(package_dir))),
        "registry_path": engine_payload.get("registryPath", engine_payload.get("registry_path", "")),
        "registry_entry": engine_payload.get("registryEntry", engine_payload.get("registry_entry", {})),
        "templates": templates,
    }
    if template_list_warning:
        result["template_list_warning"] = template_list_warning
    return result, 200


def create_job(payload: dict, workspace: Path) -> tuple[dict[str, Any], int]:
    return _create_job_impl(payload, workspace, allow_expert_delivery_output=False)


def _create_job_impl(
    payload: dict,
    workspace: Path,
    *,
    allow_expert_delivery_output: bool,
) -> tuple[dict[str, Any], int]:
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
        roots = _figure_adjustment_allowed_absolute_roots(workspace)
        source_path = _resolve_workspace_path(
            workspace,
            _first_text(payload, "source_path", "sourcePath", "source"),
            field="source_path",
            must_exist=True,
            allowed_absolute_roots=roots,
        )
        out_dir_raw = _first_text(payload, "out_dir", "outDir") or f".docx-engine-v2/{uuid.uuid4().hex}"
        out_dir = _resolve_workspace_path(
            workspace,
            out_dir_raw,
            field="out_dir",
            allowed_absolute_roots=roots,
        )
        if _path_targets_current_expert_delivery(workspace, out_dir) and not allow_expert_delivery_output:
            return _error_payload(
                "expert_delivery_writer_required",
                "generic create_job cannot write into an expert-team delivery tree",
            ), 400
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
            asset_dir = _resolve_workspace_path(
                workspace,
                asset_dir_raw,
                field="asset_dir",
                must_exist=True,
                allowed_absolute_roots=roots,
            )
            args.extend(["--asset-dir", str(asset_dir)])
        contract_json_fields = (
            ("document_metadata", "documentMetadata", "--document-metadata-json"),
            ("canonical_binding", "canonicalBinding", "--canonical-binding-json"),
            ("renderer_identity", "rendererIdentity", "--renderer-identity-json"),
            ("render_input_binding", "renderInputBinding", "--render-input-binding-json"),
        )
        for snake_name, camel_name, flag in contract_json_fields:
            value = payload.get(snake_name, payload.get(camel_name))
            if value is not None:
                if not isinstance(value, dict):
                    raise ValueError(f"{snake_name} must be an object")
                args.extend([flag, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))])
        fingerprint = _first_text(payload, "render_input_fingerprint", "renderInputFingerprint")
        if fingerprint:
            args.extend(["--render-input-fingerprint", fingerprint])
        asset_manifest_raw = _first_text(payload, "asset_manifest_path", "assetManifestPath")
        if asset_manifest_raw:
            asset_manifest = _resolve_workspace_path(
                workspace,
                asset_manifest_raw,
                field="asset_manifest_path",
                must_exist=True,
                allowed_absolute_roots=roots,
            )
            args.extend(["--asset-manifest", str(asset_manifest)])
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


def _create_expert_delivery_job(
    payload: dict,
    workspace: Path,
    *,
    run_id: str,
    stage_id: str,
    attempt: int,
) -> tuple[dict[str, Any], int]:
    """Internal writer used only while the caller holds the canonical attempt lock."""
    from api.expert_teams.delivery_integrity import canonical_delivery_dir

    root = Path(workspace).expanduser().resolve()
    expected = canonical_delivery_dir(root, run_id, stage_id, attempt)
    raw_out = _first_text(payload, "out_dir", "outDir")
    try:
        requested = _resolve_workspace_path(root, raw_out, field="out_dir")
    except (OSError, ValueError) as exc:
        return _error_payload("expert_delivery_binding_invalid", str(exc)), 400
    if requested != expected:
        return _error_payload(
            "expert_delivery_binding_invalid",
            "internal expert delivery writer requires the exact canonical delivery directory",
        ), 400
    return _create_job_impl(payload, root, allow_expert_delivery_output=True)


def validate_delivery(
    payload: dict,
    workspace: Path,
) -> tuple[dict[str, Any], int]:
    """Revalidate the current delivery package through the engine CLI."""
    workspace = Path(workspace).expanduser().resolve()
    delivery_raw = _first_text(payload, "delivery_dir", "deliveryDir")
    try:
        delivery_dir = _resolve_workspace_path(
            workspace,
            delivery_raw,
            field="delivery_dir",
            must_exist=True,
            allowed_absolute_roots=_figure_adjustment_allowed_absolute_roots(workspace),
        )
        if not delivery_dir.is_dir():
            return _error_payload("validation_failed", f"delivery_dir must be a directory: {delivery_dir}"), 400
    except (FileNotFoundError, OSError, ValueError) as exc:
        return _error_payload("validation_failed", str(exc)), 400

    args = [
        str(_engine_cli("validate-delivery.js")),
        "--delivery-dir",
        str(delivery_dir),
        "--json",
    ]
    if _first_bool(payload, "write_report", "writeReport"):
        args.append("--write-report")
    try:
        from api.expert_teams.delivery_integrity import (
            classify_delivery_binding,
            delivery_attempt_lock,
            delivery_identity_from_directory,
            path_targets_expert_delivery_tree,
            validated_binding_for_identity,
        )

        identity = delivery_identity_from_directory(workspace, delivery_dir)
        if path_targets_expert_delivery_tree(workspace, delivery_raw) and identity is None:
            raise ValueError("expert-team delivery directory is not canonical")
        if identity is not None:
            with delivery_attempt_lock(
                workspace,
                identity["run_id"],
                identity["stage_id"],
                identity["attempt"],
            ):
                binding = validated_binding_for_identity(workspace, identity)
                if classify_delivery_binding(binding) == "enterprise_pre_office":
                    if _first_bool(payload, "write_report", "writeReport"):
                        raise _ExpertDeliveryImmutable(
                            "enterprise automatic quality report is immutable after delivery binding"
                        )
                    args.extend(["--office-mode", "external-office"])
                if _first_bool(payload, "write_report", "writeReport"):
                    _validate_expert_wps_run_binding(
                        workspace,
                        binding=binding,
                        supplied_session_id=str(binding.get("session_id") or ""),
                    )
                completed = run_engine(args)
        else:
            completed = run_engine(args)
    except _ExpertDeliveryImmutable as exc:
        return _error_payload("expert_delivery_immutable", str(exc)), 409
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return _error_payload("delivery_validation_unavailable", str(exc)), 500
    except ValueError as exc:
        return _error_payload("expert_delivery_binding_invalid", str(exc)), 400

    engine_payload = _payload_from_completed(completed, default_code="delivery_validation_failed")
    quality_report = engine_payload.get("qualityReport", engine_payload.get("quality_report", {}))
    if not isinstance(quality_report, dict):
        quality_report = {}
    quality_report_path = str(
        engine_payload.get("qualityReportPath")
        or engine_payload.get("quality_report_path")
        or (delivery_dir / "quality-report.json")
    )
    raw_failures = engine_payload.get("failures") or quality_report.get("failures") or []
    failures = raw_failures if isinstance(raw_failures, list) else [raw_failures]
    result = {
        "ok": completed.returncode == 0 and engine_payload.get("ok") is not False,
        "code": str(engine_payload.get("code") or ""),
        "message": str(engine_payload.get("message") or ""),
        "delivery_dir": str(engine_payload.get("deliveryDir") or engine_payload.get("delivery_dir") or delivery_dir),
        "quality_report_path": quality_report_path,
        "quality_report": quality_report,
        "failures": [str(item) for item in failures if str(item or "").strip()],
    }
    if result["ok"]:
        return result, 200
    if not result["code"]:
        result["code"] = "delivery_validation_failed"
    return result, 422 if result["code"] == "delivery_validation_failed" else 500


def begin_office_review(
    payload: dict,
    workspace: Path,
    *,
    trusted_reviewer: str = "",
    trusted_principal: dict | None = None,
    open_document=None,
) -> tuple[dict[str, Any], int]:
    from api.expert_teams.delivery_integrity import (
        DeliveryIntegrityError,
        delivery_attempt_lock,
        delivery_identity_from_directory,
        validated_binding_for_identity,
        office_binding_identity,
        workspace_relative_path,
    )
    from api.expert_teams.office_review import issue_review_token, open_document_with_os

    root = Path(workspace).expanduser().resolve()
    delivery_raw = _first_text(payload, "delivery_dir", "deliveryDir")
    try:
        delivery_dir = _resolve_workspace_path(
            root,
            delivery_raw,
            field="delivery_dir",
            must_exist=True,
            allowed_absolute_roots=_figure_adjustment_allowed_absolute_roots(root),
        )
        identity = delivery_identity_from_directory(root, delivery_dir)
        if identity is None:
            raise DeliveryIntegrityError("office review is only available for canonical expert-team deliveries")
        with delivery_attempt_lock(
            root,
            identity["run_id"],
            identity["stage_id"],
            identity["attempt"],
        ):
            binding = validated_binding_for_identity(root, identity)
            _validate_expert_wps_run_binding(
                root,
                binding=binding,
                supplied_session_id=_first_text(payload, "session_id", "sessionId"),
            )
            document = delivery_dir / "document.docx"
            office_binding = office_binding_identity(root, identity, binding)
            token, state, _state_path = issue_review_token(
                root,
                binding=office_binding,
                document_path=document,
                reviewer=trusted_reviewer,
                open_document=open_document or open_document_with_os,
                trusted_principal=trusted_principal,
            )
            _remember_active_office_review_token(
                root,
                {"run_id": identity["run_id"], "session_id": binding["session_id"]},
                token,
                expires_at_ns=int(state.get("expires_at_ns") or 0),
            )
    except _ExpertDeliveryImmutable as exc:
        return _error_payload("expert_delivery_immutable", str(exc)), 409
    except (DeliveryIntegrityError, FileNotFoundError, OSError, ValueError) as exc:
        return _error_payload("expert_delivery_binding_invalid", str(exc)), 400
    return {
        "ok": True,
        "office_status": "pending",
        "review_token": token,
        "reviewer": state["reviewer"],
        "opened_at": state["opened_at"],
        "expires_at_ns": state["expires_at_ns"],
        "document_sha256": state["document_sha256"],
        "evidence_dir": state["evidence_dir"],
        "document_path": workspace_relative_path(root, document),
    }, 200


def record_wps_visual_acceptance(
    payload: dict,
    workspace: Path,
    *,
    trusted_principal: dict | None = None,
) -> tuple[dict[str, Any], int]:
    workspace = Path(workspace).expanduser().resolve()
    structured_token_key = None
    if _first_text(payload, "run_id") and not _first_text(payload, "delivery_dir", "deliveryDir"):
        try:
            replay = _structured_office_acceptance_replay(payload, workspace)
            if replay is not None:
                return replay
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
            return _error_payload("office_acceptance_idempotency_invalid", str(exc)), 400
        structured_token_key = (str(workspace), _first_text(payload, "session_id"), _first_text(payload, "run_id"))
        try:
            payload = _expand_structured_office_submission(
                payload,
                workspace,
                trusted_principal=trusted_principal,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            return _error_payload("office_review_session_invalid", str(exc)), 409 if "version conflict" in str(exc) else 400
    delivery_raw = _first_text(payload, "delivery_dir", "deliveryDir")
    try:
        delivery_dir = _resolve_workspace_path(
            workspace,
            delivery_raw,
            field="delivery_dir",
            must_exist=True,
            allowed_absolute_roots=_figure_adjustment_allowed_absolute_roots(workspace),
        )
        if not delivery_dir.is_dir():
            return _error_payload("validation_failed", f"delivery_dir must be a directory: {delivery_dir}"), 400
        status = _first_text(payload, "status") or "passed"
        if status not in {"passed", "passed_with_warnings", "passed_with_conditions", "failed"}:
            return _error_payload("validation_failed", f"Invalid WPS visual status: {status}"), 400
        raw_visual_checks = payload.get("visual_checks", payload.get("visualChecks", []))
        visual_checks = [
            str(item).strip()
            for item in (raw_visual_checks if isinstance(raw_visual_checks, list) else [raw_visual_checks])
            if str(item or "").strip()
        ]
        raw_evidence_files = payload.get("evidence_files", payload.get("evidenceFiles", []))
        evidence_files = []
        evidence_input_paths = []
        for item in raw_evidence_files if isinstance(raw_evidence_files, list) else [raw_evidence_files]:
            if not str(item or "").strip():
                continue
            lexical = Path(os.path.expandvars(str(item))).expanduser()
            if not lexical.is_absolute():
                lexical = workspace / lexical
            evidence_input_paths.append(lexical.absolute())
            evidence = _resolve_workspace_path(
                workspace,
                str(item),
                field="evidence_files",
                must_exist=True,
                allowed_absolute_roots=_figure_adjustment_allowed_absolute_roots(workspace),
            )
            if not evidence.is_file():
                return _error_payload("validation_failed", f"evidence file is not a file: {evidence}"), 400
            evidence_files.append(evidence)
    except (FileNotFoundError, OSError, ValueError) as exc:
        return _error_payload("validation_failed", str(exc)), 400

    reviewer = _first_text(payload, "reviewer", "reviewed_by", "reviewedBy") or "user"
    note = _first_text(payload, "note", "message")
    args = [
        str(_engine_cli("record-wps-visual.js")),
        "--delivery-dir",
        str(delivery_dir),
        "--status",
        status,
        "--reviewer",
        reviewer,
        "--json",
    ]
    if note:
        args.extend(["--note", note])
    for check in visual_checks:
        args.extend(["--visual-check", check])
    for evidence in evidence_files:
        args.extend(["--evidence-file", str(evidence)])

    def record_with_engine() -> tuple[dict[str, Any], int]:
        completed = run_engine(args)
        engine_payload = _payload_from_completed(completed, default_code="wps_visual_record_failed")
        if completed.returncode != 0:
            return _known_failure_payload(engine_payload), _status_for_engine_failure(engine_payload)

        quality_report_path = Path(str(engine_payload.get("qualityReportPath", ""))).expanduser()
        engine_report = engine_payload.get("qualityReport")
        quality_report = engine_report if isinstance(engine_report, dict) else _read_quality_report(quality_report_path)
        return {
            "ok": True,
            "delivery_dir": str(delivery_dir),
            "quality_status": quality_report.get("status", ""),
            "quality_report_path": str(quality_report_path),
            "quality_report": quality_report,
        }, 200

    # Generic DOCX Engine V2 deliveries retain their original behavior.  Expert-team
    # deliveries have a stronger identity contract and must be checked while holding
    # the same attempt lock used by final approval.
    from api.expert_teams.delivery_integrity import (
        DeliveryIntegrityError,
        delivery_attempt_lock,
        delivery_identity_from_directory,
        path_targets_expert_delivery_tree,
        office_binding_identity,
        validated_binding_for_identity,
        workspace_relative_path,
        sha256_file,
        validate_canonical_wps_evidence,
        write_wps_acceptance_manifest,
    )

    expert_path_hint = path_targets_expert_delivery_tree(workspace, delivery_raw)
    try:
        expert_identity = delivery_identity_from_directory(workspace, delivery_dir)
    except (DeliveryIntegrityError, OSError, ValueError) as exc:
        return _error_payload("expert_delivery_binding_invalid", str(exc)), 400
    if expert_path_hint and expert_identity is None:
        return _error_payload(
            "expert_delivery_binding_invalid",
            "expert-team delivery directory is not canonical",
        ), 400
    if expert_identity is None:
        return record_with_engine()

    with delivery_attempt_lock(
        workspace,
        expert_identity["run_id"],
        expert_identity["stage_id"],
        expert_identity["attempt"],
    ):
        from api.expert_teams.office_review import (
            OfficeReviewTokenUsed,
            consume_review_token,
            load_review_token,
            prepare_consumed_review_state,
            validate_token_evidence,
            write_office_review_proof,
        )

        try:
            binding = validated_binding_for_identity(workspace, expert_identity)
            office_binding = office_binding_identity(workspace, expert_identity, binding)
            _validate_expert_wps_run_binding(
                workspace,
                binding=binding,
                supplied_session_id=_first_text(payload, "session_id", "sessionId"),
            )
        except _ExpertDeliveryImmutable as exc:
            return _error_payload("expert_delivery_immutable", str(exc)), 409
        except (DeliveryIntegrityError, FileNotFoundError, OSError, ValueError) as exc:
            return _error_payload("expert_delivery_binding_invalid", str(exc)), 400
        try:
            if payload.get("attested_actual_office_review") is not True:
                return _error_payload(
                    "office_review_token_required",
                    "explicit Office review attestation is required",
                ), 400
            token_state, token_state_path = load_review_token(
                workspace,
                _first_text(payload, "review_token", "reviewToken"),
                binding=office_binding,
            )
            if isinstance(trusted_principal, dict):
                trusted_subject = str(trusted_principal.get("subject") or "")
                token_subject = str((token_state.get("reviewer_identity") or {}).get("subject") or "")
                if not trusted_subject or trusted_subject != token_subject:
                    return _error_payload(
                        "trusted_reviewer_mismatch",
                        "active Office reviewer does not match the authenticated principal",
                    ), 403
        except OfficeReviewTokenUsed as exc:
            return _error_payload("office_review_token_used", str(exc)), 409
        except (DeliveryIntegrityError, FileNotFoundError, OSError, ValueError) as exc:
            return _error_payload("office_review_token_required", str(exc)), 400

        reviewer = str(token_state.get("reviewer") or "").strip()
        metadata_error = _expert_wps_metadata_error(reviewer=reviewer, note=note)
        if metadata_error:
            return _error_payload("wps_visual_metadata_invalid", metadata_error), 400
        try:
            validate_token_evidence(workspace, token_state, evidence_input_paths)
        except (DeliveryIntegrityError, OSError, ValueError) as exc:
            return _error_payload("office_review_evidence_invalid", str(exc)), 400
        evidence_error = _expert_wps_evidence_error(evidence_files)
        if evidence_error:
            return _error_payload("wps_visual_evidence_invalid", evidence_error), 400
        if office_binding.get("schema_version") == "expert-office-binding/v1":
            from datetime import datetime, timezone
            from api.expert_teams.office_review import build_office_acceptance, write_office_acceptance

            if status == "passed_with_warnings":
                return _error_payload("office_acceptance_status_invalid", "enterprise Office uses passed_with_conditions"), 400
            canonical_evidence = []
            evidence_dir = expert_identity["delivery_dir"] / "evidence" / "wps-visual"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            for index, source in enumerate(evidence_files, 1):
                suffix = source.suffix.lower() if source.suffix else ".bin"
                target = evidence_dir / f"office-{index}{suffix}"
                if target.resolve() != source.resolve():
                    shutil.copyfile(source, target)
                canonical_evidence.append({
                    "path": target.relative_to(expert_identity["delivery_dir"]).as_posix(),
                    "sha256": sha256_file(target),
                    "sizeBytes": target.stat().st_size,
                    "mediaType": _visual_evidence_media_type(target),
                })
            visual_set = set(visual_checks)
            checklist = {
                "document_opened": "passed" if "document_opened" in visual_set else "not_checked",
                "title_and_cover_match": "passed" if "layout_reviewed" in visual_set else "not_checked",
                "genre_and_structure_match": "passed" if "content_order_reviewed" in visual_set else "not_checked",
                "content_order_correct": "passed" if "content_order_reviewed" in visual_set else "not_checked",
                "figures_unique_and_readable": "passed" if "figures_reviewed" in visual_set else "not_applicable",
                "tables_readable": "passed" if "tables_reviewed" in visual_set else "not_applicable",
                "headers_footers_pagination": "passed" if "layout_reviewed" in visual_set else "not_checked",
                "no_placeholders_or_workflow_text": "passed" if "content_order_reviewed" in visual_set else "not_checked",
                "citations_readable": "passed" if "citations_reviewed" in visual_set else "not_applicable",
            }
            try:
                acceptance = build_office_acceptance(
                    binding=office_binding,
                    token_state=token_state,
                    status=status,
                    checklist=checklist,
                    issues=payload.get("issues") if isinstance(payload.get("issues"), list) else [],
                    evidence=canonical_evidence,
                    note=note,
                    now=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                    idempotency_key=_first_text(payload, "idempotency_key"),
                    request_fingerprint=_first_text(payload, "request_fingerprint"),
                )
                manifest_path, _manifest = write_office_acceptance(workspace, office_binding, acceptance)
                consumed_state = prepare_consumed_review_state(
                    token_state,
                    acceptance_manifest_path=workspace_relative_path(workspace, manifest_path),
                    acceptance_manifest_sha256=sha256_file(manifest_path),
                    canonical_evidence=canonical_evidence,
                )
                consume_review_token(token_state_path, consumed_state)
            except (DeliveryIntegrityError, OSError, ValueError) as exc:
                return _error_payload("office_acceptance_invalid", str(exc)), 400
            _forget_active_office_review_token(structured_token_key)
            return {
                "ok": True,
                "office_status": acceptance["decision"],
                "acceptance": acceptance,
                "acceptance_manifest_path": workspace_relative_path(workspace, manifest_path),
                "reviewer": str(token_state.get("reviewer") or ""),
            }, 200
        reviewer_index = args.index("--reviewer") + 1
        args[reviewer_index] = reviewer

        result, result_status = record_with_engine()
        if result_status != 200 or result.get("ok") is not True:
            return result, result_status
        try:
            refreshed_binding = validated_binding_for_identity(workspace, expert_identity)
            if refreshed_binding != binding:
                raise DeliveryIntegrityError("expert-team delivery changed while recording WPS acceptance")
            checks = [
                item
                for item in (result.get("quality_report") or {}).get("checks") or []
                if isinstance(item, dict)
            ]
            wps_check = next(
                (item for item in checks if str(item.get("id") or "") == "wps_visual"),
                None,
            )
            if not isinstance(wps_check, dict):
                raise DeliveryIntegrityError("WPS acceptance record is missing from quality-report.json")
            if str(wps_check.get("reviewedBy") or "").strip() != reviewer:
                raise DeliveryIntegrityError("WPS acceptance reviewer does not match the trusted local identity")
            if str(wps_check.get("documentSha256") or "") != office_binding["document_sha256"]:
                raise DeliveryIntegrityError("WPS acceptance is bound to a different document digest")
            canonical_evidence = validate_canonical_wps_evidence(
                workspace,
                expert_identity["delivery_dir"],
                [
                    item
                    for item in wps_check.get("visualEvidence") or []
                    if isinstance(item, dict)
                ],
            )
            wps_check["visualEvidence"] = canonical_evidence
            manifest_path, _manifest = write_wps_acceptance_manifest(
                workspace,
                binding=office_binding,
                reviewer=reviewer,
                note=note,
                visual_checks=[
                    str(item).strip()
                    for item in wps_check.get("visualChecks") or []
                    if str(item or "").strip()
                ],
                wps_check=wps_check,
                office_review={
                    "token_hash": token_state.get("token_hash"),
                    "opened_at": token_state.get("opened_at"),
                    "evidence_dir": token_state.get("evidence_dir"),
                    "attested_actual_office_review": True,
                },
            )
            consumed_state = prepare_consumed_review_state(
                token_state,
                acceptance_manifest_path=workspace_relative_path(workspace, manifest_path),
                acceptance_manifest_sha256=sha256_file(manifest_path),
                canonical_evidence=canonical_evidence,
            )
            write_office_review_proof(workspace, office_binding, consumed_state)
            consume_review_token(token_state_path, consumed_state)
        except (DeliveryIntegrityError, OSError, ValueError) as exc:
            return _error_payload("expert_delivery_binding_invalid", str(exc)), 400
        result["acceptance_manifest_path"] = workspace_relative_path(workspace, manifest_path)
        result["reviewer"] = reviewer
        return result, result_status


def _expert_wps_metadata_error(*, reviewer: str, note: str) -> str:
    normalized_reviewer = str(reviewer or "").strip()
    placeholder_reviewers = {
        "user",
        "reviewer",
        "test",
        "integration-test",
        "unknown",
        "anonymous",
        "匿名",
        "系统",
        "审核人",
        "用户",
    }
    if len(normalized_reviewer) < 2 or normalized_reviewer.lower() in placeholder_reviewers:
        return "expert-team WPS acceptance requires an identifiable reviewer"
    normalized_note = str(note or "").strip()
    lowered = normalized_note.lower()
    mentions_office = any(token in lowered for token in ("wps", "word"))
    mentions_action = any(token in lowered for token in ("打开", "页面", "逐页", "分页", "导出"))
    mentions_layout = any(
        token in lowered
        for token in ("版式", "布局", "目录", "图表", "图片", "表格", "页眉", "页脚", "字体")
    )
    if len(normalized_note) < 10 or not (mentions_office and mentions_action and mentions_layout):
        return "expert-team WPS acceptance note must describe the Office review and checked layout areas"
    return ""


def _expert_wps_evidence_error(evidence_files: list[Path]) -> str:
    if not evidence_files:
        return "expert-team WPS acceptance requires screenshot or PDF evidence"
    for evidence in evidence_files:
        try:
            size = evidence.stat().st_size
            if size <= 0 or size > 50 * 1024 * 1024:
                return f"WPS evidence must be between 1 byte and 50 MiB: {evidence}"
            with evidence.open("rb") as handle:
                header = handle.read(32)
            if header.startswith(b"\x89PNG\r\n\x1a\n"):
                if len(header) < 24:
                    return f"invalid PNG evidence: {evidence}"
                width = int.from_bytes(header[16:20], "big")
                height = int.from_bytes(header[20:24], "big")
                if width < 800 or height < 500:
                    return f"WPS screenshot evidence is too small to show a reviewed document: {evidence}"
                continue
            if header.startswith(b"\xff\xd8\xff"):
                dimensions = _jpeg_dimensions(evidence)
                if dimensions is None or dimensions[0] < 800 or dimensions[1] < 500:
                    return f"WPS screenshot evidence is too small or invalid: {evidence}"
                continue
            if header.startswith(b"%PDF-"):
                pdf_bytes = evidence.read_bytes()
                if len(pdf_bytes) < 1024 or b"/Type /Page" not in pdf_bytes:
                    return f"WPS PDF evidence does not contain a plausible rendered page: {evidence}"
                continue
            return f"unsupported WPS evidence type: {evidence}"
        except OSError as exc:
            return f"cannot inspect WPS evidence {evidence}: {exc}"
    return ""


def _visual_evidence_media_type(path: Path) -> str:
    suffix = Path(path).suffix.lower()
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".pdf": "application/pdf"}.get(
        suffix, "application/octet-stream"
    )


def _jpeg_dimensions(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    start_of_frame_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while index + 3 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index:index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in start_of_frame_markers and segment_length >= 7:
            height = int.from_bytes(data[index + 3:index + 5], "big")
            width = int.from_bytes(data[index + 5:index + 7], "big")
            return width, height
        index += segment_length
    return None


def _validate_expert_wps_run_binding(
    workspace: Path,
    *,
    binding: dict,
    supplied_session_id: str,
) -> None:
    from api.expert_teams.delivery_integrity import (
        DeliveryIntegrityError,
        binding_manifest_path,
        classify_delivery_binding,
        sha256_file,
    )
    from api.expert_teams.storage import read_run

    run = read_run(workspace, str(binding.get("run_id") or ""))
    if str(run.get("workflow_state") or "") == "completed":
        raise _ExpertDeliveryImmutable("completed expert-team delivery cannot be changed")
    session_id = str(binding.get("session_id") or "")
    if not supplied_session_id or supplied_session_id != session_id:
        raise DeliveryIntegrityError("WPS acceptance session id does not match the delivery binding")
    if str(run.get("session_id") or "") != session_id:
        raise DeliveryIntegrityError("expert-team run session id does not match the delivery binding")
    try:
        current_index = int(run.get("current_stage_index"))
        tasks = run.get("_tasks_template")
        if not isinstance(tasks, list) or current_index < 0 or current_index >= len(tasks):
            raise DeliveryIntegrityError("expert-team authoritative stage is missing")
        task = tasks[current_index]
        if not isinstance(task, dict):
            raise DeliveryIntegrityError("expert-team authoritative stage is invalid")
        authoritative_stage_id = str(task.get("task_id") or task.get("id") or "")
        current_stage = run.get("current_stage")
        if (
            authoritative_stage_id != str(binding.get("stage_id") or "")
            or not isinstance(current_stage, dict)
            or str(current_stage.get("task_id") or current_stage.get("id") or "") != authoritative_stage_id
            or int(current_stage.get("index")) != current_index
        ):
            raise DeliveryIntegrityError("expert-team current stage does not match the delivery binding")
    except (TypeError, ValueError) as exc:
        raise DeliveryIntegrityError("expert-team authoritative stage is corrupt") from exc
    if classify_delivery_binding(binding) == "enterprise_pre_office":
        ref = (
            run.get("current_delivery_manifest_ref")
            if isinstance(run.get("current_delivery_manifest_ref"), dict)
            else {}
        )
        try:
            delivery_attempt = int(binding.get("delivery_attempt") or 0)
            if (
                not ref
                or delivery_attempt < 1
                or int(ref.get("delivery_attempt") or 0) != delivery_attempt
                or int(ref.get("stage_attempt") or 0) < 1
                or not str(ref.get("artifact_id") or "")
                or not str(ref.get("sha256") or "")
                or not str(ref.get("delivery_binding_path") or "")
                or not str(ref.get("delivery_binding_sha256") or "")
            ):
                raise DeliveryIntegrityError("expert-team delivery manifest authority is missing or stale")
        except (TypeError, ValueError) as exc:
            raise DeliveryIntegrityError("expert-team delivery manifest authority is corrupt") from exc
        binding_path = workspace / str(ref["delivery_binding_path"])
        try:
            binding_path.resolve().relative_to(workspace.resolve())
        except (OSError, ValueError) as exc:
            raise DeliveryIntegrityError("expert-team delivery manifest path escapes the workspace") from exc
        expected_binding_path = binding_manifest_path(
            workspace,
            str(binding.get("run_id") or ""),
            str(binding.get("stage_id") or ""),
            delivery_attempt,
        )
        if (
            binding_path.resolve() != expected_binding_path.resolve()
            or not binding_path.is_file()
            or sha256_file(binding_path) != str(ref["delivery_binding_sha256"])
        ):
            raise DeliveryIntegrityError("expert-team delivery manifest binding is missing or stale")
        artifacts = [
            item
            for item in run.get("stage_artifacts") or []
            if isinstance(item, dict)
            and item.get("artifact_type") == "delivery_manifest"
            and item.get("artifact_id") == ref["artifact_id"]
        ]
        if not artifacts:
            raise DeliveryIntegrityError("expert-team delivery manifest artifact is missing")
        artifact = artifacts[-1]
        payload = artifact.get("payload") if isinstance(artifact.get("payload"), dict) else {}
        try:
            if (
                str(artifact.get("sha256") or "") != str(ref["sha256"])
                or int(artifact.get("stage_attempt") or 0) != int(ref["stage_attempt"])
                or int(payload.get("delivery_attempt") or 0) != delivery_attempt
                or str(payload.get("delivery_binding_path") or "") != str(ref["delivery_binding_path"])
                or str(payload.get("delivery_binding_sha256") or "") != str(ref["delivery_binding_sha256"])
                or payload.get("office_review_required") is not True
            ):
                raise DeliveryIntegrityError("expert-team delivery manifest artifact is stale")
        except (TypeError, ValueError) as exc:
            raise DeliveryIntegrityError("expert-team delivery manifest artifact is corrupt") from exc
        return
    outputs = [
        item
        for item in run.get("stage_outputs") or []
        if isinstance(item, dict)
        and str(item.get("task_id") or item.get("stage_id") or "") == authoritative_stage_id
    ]
    if not outputs:
        raise DeliveryIntegrityError("expert-team delivery stage output is missing")
    latest = outputs[-1]
    document_delivery = latest.get("document_delivery")
    try:
        if (
            int(latest.get("stage_attempt")) != int(binding.get("attempt") or 0)
            or not isinstance(document_delivery, dict)
            or int(document_delivery.get("attempt")) != int(binding.get("attempt") or 0)
            or str(document_delivery.get("source_sha256") or "") != str(binding.get("source_sha256") or "")
            or str(document_delivery.get("document_sha256") or "") != str(binding.get("document_sha256") or "")
            or str(document_delivery.get("delivery_dir") or "") != str(binding.get("delivery_dir") or "")
        ):
            raise DeliveryIntegrityError("expert-team stage output does not match the delivery binding")
    except (TypeError, ValueError) as exc:
        raise DeliveryIntegrityError("expert-team delivery attempt metadata is corrupt") from exc


def _expert_mutation_identity(
    workspace: Path,
    paths: list[tuple[str, Path]],
) -> dict | None:
    from api.expert_teams.delivery_integrity import (
        DeliveryIntegrityError,
        delivery_identity_from_path,
        path_targets_expert_delivery_tree,
    )

    identities = []
    for raw, resolved in paths:
        hinted = path_targets_expert_delivery_tree(workspace, raw)
        identity = delivery_identity_from_path(workspace, resolved)
        if hinted and identity is None:
            raise DeliveryIntegrityError("expert-team mutation path is not canonical")
        if identity is not None:
            identities.append(identity)
    if not identities:
        return None
    expected = {
        key: identities[0][key]
        for key in ("run_id", "stage_id", "attempt")
    }
    if any(
        any(identity[key] != value for key, value in expected.items())
        for identity in identities[1:]
    ):
        raise DeliveryIntegrityError("expert-team mutation paths belong to different delivery attempts")
    return identities[0]


def _assert_expert_delivery_mutable(workspace: Path, identity: dict) -> None:
    from api.expert_teams.delivery_integrity import validated_binding_for_identity

    binding = validated_binding_for_identity(workspace, identity)
    _validate_expert_wps_run_binding(
        workspace,
        binding=binding,
        supplied_session_id=str(binding.get("session_id") or ""),
    )


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
        out_dir_raw = _first_text(payload, "out_dir", "outDir")
        out_dir = _resolve_workspace_path(
            workspace,
            out_dir_raw,
            field="out_dir",
            allowed_absolute_roots=roots,
        )
        if _path_targets_current_expert_delivery(workspace, out_dir):
            return _error_payload(
                "expert_delivery_writer_required",
                "generic package_rich_draft cannot write into an expert-team delivery tree",
            ), 400
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

    args = [
        str(_engine_cli("render-figure-asset.js")),
        "--manifest",
        str(manifest_path),
        "--figure-id",
        figure_id,
        "--json",
    ]
    from api.expert_teams.delivery_integrity import DeliveryIntegrityError, delivery_attempt_lock

    try:
        identity = _expert_mutation_identity(workspace, [(manifest_raw, manifest_path)])
        if identity is None:
            completed = run_engine(args)
        else:
            with delivery_attempt_lock(
                workspace,
                identity["run_id"],
                identity["stage_id"],
                identity["attempt"],
            ):
                _assert_expert_delivery_mutable(workspace, identity)
                completed = run_engine(args)
    except _ExpertDeliveryImmutable as exc:
        return _error_payload("expert_delivery_immutable", str(exc)), 409
    except (DeliveryIntegrityError, FileNotFoundError, OSError, ValueError) as exc:
        return _error_payload("expert_delivery_binding_invalid", str(exc)), 400
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
        docx_raw = _first_text(payload, "docx_path", "docxPath", "docx")
        image_raw = _first_text(payload, "image_path", "imagePath", "image")
        out_raw = _first_text(payload, "out_path", "outPath", "out")
        docx_path = _resolve_workspace_path(
            workspace,
            docx_raw,
            field="docx_path",
            must_exist=True,
            allowed_absolute_roots=roots,
        )
        image_path = _resolve_workspace_path(
            workspace,
            image_raw,
            field="image_path",
            must_exist=True,
            allowed_absolute_roots=roots,
        )
        out_path = _resolve_workspace_path(
            workspace,
            out_raw,
            field="out_path",
            allowed_absolute_roots=roots,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except (FileNotFoundError, OSError, ValueError) as exc:
        return _error_payload("validation_failed", str(exc)), 400

    args = [
        str(_engine_cli("replace-asset.js")),
        "--docx",
        str(docx_path),
        "--figure-id",
        figure_id,
        "--image",
        str(image_path),
        "--out",
        str(out_path),
    ]
    from api.expert_teams.delivery_integrity import DeliveryIntegrityError, delivery_attempt_lock

    try:
        identity = _expert_mutation_identity(
            workspace,
            [(docx_raw, docx_path), (out_raw, out_path)],
        )
        if identity is None:
            completed = run_engine(args)
        else:
            with delivery_attempt_lock(
                workspace,
                identity["run_id"],
                identity["stage_id"],
                identity["attempt"],
            ):
                _assert_expert_delivery_mutable(workspace, identity)
                completed = run_engine(args)
    except _ExpertDeliveryImmutable as exc:
        return _error_payload("expert_delivery_immutable", str(exc)), 409
    except (DeliveryIntegrityError, FileNotFoundError, OSError, ValueError) as exc:
        return _error_payload("expert_delivery_binding_invalid", str(exc)), 400
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


def _first_bool(payload: dict, *keys: str) -> bool:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return False


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
    result = {
        "ok": False,
        "code": str(payload.get("code") or "validation_failed"),
        "message": str(payload.get("message") or ""),
        "failures": payload.get("failures", []),
        "templates": payload.get("templates", []),
        "delivery_dir": payload.get("deliveryDir", payload.get("delivery_dir", "")),
    }
    optional_fields = {
        "stage": ("stage",),
        "job_manifest_path": ("jobManifestPath", "job_manifest_path"),
        "failure_report_path": ("failureReportPath", "failure_report_path"),
        "failure_report": ("failureReport", "failure_report"),
    }
    for output_key, input_keys in optional_fields.items():
        for input_key in input_keys:
            value = payload.get(input_key)
            if value:
                result[output_key] = value
                break
    return result


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
    if code in {
        "template_selection_required",
        "template_install_failed",
        "validation_failed",
        "wps_visual_record_failed",
        "brief_incomplete",
        "canonical_binding_invalid",
        "canonical_hash_mismatch",
        "asset_manifest_hash_mismatch",
        "renderer_identity_invalid",
        "renderer_identity_changed",
        "render_input_binding_invalid",
        "render_input_fingerprint_mismatch",
    }:
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

    if _path_has_expert_delivery_semantics(requested) or _path_has_expert_delivery_semantics(target):
        expert_root = root / ".taiji" / "expert-team-deliveries"
        if not _path_is_within(target, expert_root):
            raise ValueError(f"{field} targets an expert-team delivery outside the current workspace")

    allowed_roots = [root]
    if allowed_absolute_roots:
        allowed_roots.extend(Path(item).expanduser().resolve() for item in allowed_absolute_roots)
    if not any(_path_is_within(target, allowed_root) for allowed_root in allowed_roots):
        raise ValueError(f"{field} is outside the allowed local roots: {target}")
    if must_exist and not target.exists():
        raise FileNotFoundError(f"{field} not found: {target}")
    return target


def _path_is_within(target: Path, root: Path) -> bool:
    from api.expert_teams.delivery_integrity import path_is_within_filesystem

    try:
        return path_is_within_filesystem(target, root)
    except OSError:
        return False


def _path_has_expert_delivery_semantics(path: Path | str) -> bool:
    from api.expert_teams.delivery_integrity import path_has_expert_delivery_semantics

    return path_has_expert_delivery_semantics(path)


def _path_targets_current_expert_delivery(workspace: Path, path: Path) -> bool:
    from api.expert_teams.delivery_integrity import path_targets_expert_delivery_tree

    return path_targets_expert_delivery_tree(workspace, str(path))


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
