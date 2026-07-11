"""Redacted product diagnostics for the Taiji desktop support workflow."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Callable, Final, Mapping

from api.product_contract import _safe_incident_id


DIAGNOSTICS_SCHEMA: Final = "taiji.product.diagnostics.v1"
SUPPORT_BUNDLE_SCHEMA: Final = "taiji.product.support-bundle.v1"
SUPPORT_BUNDLE_MAX_BYTES: Final = 64 * 1024
SUPPORT_BUNDLE_MAX_LINE_CHARS: Final = 4096

_COMPONENTS: Final = (
    ("webui", "桌面界面", True),
    ("agent", "智能体服务", True),
    ("gateway", "本地任务服务", True),
    ("license", "授权状态", True),
    ("docx", "文档引擎", False),
    ("skills", "专家能力", False),
    ("node", "运行环境", False),
)
_STATUSES: Final = frozenset({"ready", "degraded", "blocked", "not_applicable", "unknown"})
_OVERALL_STATUSES: Final = frozenset({"ready", "degraded", "blocked"})
_VERSION_RE = re.compile(
    r"^(?:"
    r"v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
    r"|[0-9a-f]{7,40}(?:-dirty(?:\.[0-9a-f]{7,40})?)?"
    r")$"
)
_UNSAFE_VERSION_RE = re.compile(
    r"(?i)(?:hermes|password|passwd|passphrase|secret|token|bearer|(?:^|[-_.])sk-|(?:^|[-_.])key(?:[-_.]|$))"
)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_PRODUCT_SKILLS_SCHEMA: Final = "taiji-product-skills/v1"
_PRODUCT_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PRODUCT_SKILL_PATH_RE = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_timestamp(value: object) -> str:
    candidate = str(value or "").strip()
    return candidate if _TIMESTAMP_RE.fullmatch(candidate) else _utc_now()


def _safe_version(value: object) -> str | None:
    candidate = str(value or "").strip()
    if _UNSAFE_VERSION_RE.search(candidate):
        return None
    return candidate if _VERSION_RE.fullmatch(candidate) else None


def _safe_probe(probe: Callable[[], Mapping[str, object]]) -> dict[str, object]:
    try:
        raw = probe()
    except Exception:
        return {"status": "unknown"}
    if not isinstance(raw, Mapping):
        return {"status": "unknown"}
    return dict(raw)


def _probe_webui() -> dict[str, object]:
    from api.updates import WEBUI_VERSION

    return {"status": "ready", "version": WEBUI_VERSION}


def _probe_agent() -> dict[str, object]:
    from api.agent_health import build_agent_health_payload

    alive = build_agent_health_payload().get("alive")
    if alive is True:
        status = "ready"
    elif alive is False:
        status = "blocked"
    else:
        status = "degraded"
    return {"status": status}


def _probe_gateway() -> dict[str, object]:
    from api.gateway_chat import gateway_chat_authenticated_probe, gateway_chat_config_status

    state = gateway_chat_config_status()
    if not isinstance(state, Mapping) or not state.get("enabled"):
        return {"status": "not_applicable"}
    if not state.get("base_url_configured") or not state.get("api_key_configured"):
        return {"status": "degraded"}
    authenticated = gateway_chat_authenticated_probe()
    if authenticated is True:
        return {"status": "ready"}
    if authenticated is False:
        return {"status": "blocked"}
    return {"status": "degraded"}


def _license_module():
    candidates = []
    configured = str(os.environ.get("TAIJI_WEBUI_AGENT_DIR") or os.environ.get("TAIJI_AGENT_AGENT_DIR") or "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(Path(__file__).resolve().parents[2] / "hermes-agent")
    for candidate in candidates:
        if candidate.exists():
            candidate_text = str(candidate)
            if candidate_text not in sys.path:
                sys.path.insert(0, candidate_text)
            break
    return importlib.import_module("taiji_license")


def _probe_license() -> dict[str, object]:
    public = _license_module().load_license_status().to_public_dict()
    status = str(public.get("status") or "").strip().lower()
    required = bool(public.get("required"))
    if not required or status in {"valid", "not_required"}:
        return {"status": "ready"}
    if status in {"missing", "invalid", "expired", "blocked"}:
        return {"status": "blocked"}
    return {"status": "degraded"}


def _probe_docx() -> dict[str, object]:
    configured = str(os.environ.get("TAIJI_DOCX_BUILTIN_ROOT") or "").strip()
    root = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[2] / "docx-engine-v2"
    package_path = root / "package.json"
    registry_path = root / "template-registry.json"
    run_job = root / "src" / "cli" / "run-job.js"
    workflow = root / "src" / "workflow" / "run-document-job.js"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        package_ready = isinstance(package, Mapping)
        registry_ready = isinstance(registry, Mapping)
        dependencies = package.get("dependencies") if isinstance(package, Mapping) else None
        builtin = registry.get("builtin") if isinstance(registry, Mapping) else None
        builtin_paths = {
            str(item.get("templateId") or ""): str(item.get("path") or "")
            for item in (builtin if isinstance(builtin, list) else [])
            if isinstance(item, Mapping)
        }
        dependencies_ready = isinstance(dependencies, Mapping) and bool(dependencies)
        if dependencies_ready:
            for name in dependencies:
                dependency_path = root / "node_modules" / str(name) / "package.json"
                try:
                    dependency = json.loads(dependency_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    dependencies_ready = False
                    break
                if not isinstance(dependency, Mapping) or str(dependency.get("name") or "") != str(name):
                    dependencies_ready = False
                    break
        templates_ready = builtin_paths == {
            "general-proposal": "templates/general-proposal",
            "meeting-minutes": "templates/meeting-minutes",
        }
        if templates_ready:
            for template_id in builtin_paths:
                template = root / "templates" / template_id / "template.docx"
                try:
                    templates_ready = template.stat().st_size >= 64 and template.read_bytes()[:4] == b"PK\x03\x04"
                except OSError:
                    templates_ready = False
                if not templates_ready:
                    break
        ready = (
            root.is_dir()
            and package_ready
            and registry_ready
            and package.get("name") == "docx-engine-v2"
            and _safe_version(package.get("version")) is not None
            and registry.get("version") == 1
            and isinstance(registry.get("installed"), list)
            and run_job.stat().st_size >= 64
            and workflow.stat().st_size >= 64
            and dependencies_ready
            and templates_ready
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        ready = False
    return {"status": "ready" if ready else "degraded"}


def _skill_markdown_is_valid(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if len(text) < 24 or not text.startswith("---\n"):
        return False
    end = text.find("\n---", 4)
    if end < 0:
        return False
    frontmatter = text[4:end]
    return bool(re.search(r"(?m)^name:\s*\S+", frontmatter)) and bool(
        re.search(r"(?m)^description:\s*\S+", frontmatter)
    )


def _agent_skills_root() -> Path:
    configured = str(
        os.environ.get("TAIJI_WEBUI_AGENT_DIR")
        or os.environ.get("TAIJI_AGENT_AGENT_DIR")
        or ""
    ).strip()
    agent_root = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[2] / "hermes-agent"
    return agent_root / "skills"


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(path).encode("utf-8") + b"\0")
        elif path.is_file():
            digest.update(b"F\0" + relative + b"\0")
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            digest.update(b"\0")
        elif path.is_dir():
            digest.update(b"D\0" + relative + b"\0")
    return digest.hexdigest()


def _installed_product_skills_are_valid(root: Path) -> bool:
    try:
        manifest = json.loads((root / "product-skills.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, Mapping):
        return False
    entries = manifest.get("skills")
    if manifest.get("schema_version") != _PRODUCT_SKILLS_SCHEMA or not isinstance(entries, list) or not entries:
        return False
    seen: set[tuple[str, str]] = set()
    for item in entries:
        if not isinstance(item, Mapping):
            return False
        skill_id = str(item.get("id") or "")
        relative = str(item.get("path") or "")
        if not _PRODUCT_SKILL_ID_RE.fullmatch(skill_id) or not _PRODUCT_SKILL_PATH_RE.fullmatch(relative):
            return False
        category, manifest_id = relative.split("/", 1)
        if manifest_id != skill_id or (category, skill_id) in seen:
            return False
        if not _SHA256_RE.fullmatch(str(item.get("sha256") or "")):
            return False
        if not _SHA256_RE.fullmatch(str(item.get("source_sha256") or "")):
            return False
        if str(item.get("productization") or "") != "skill-md-visible-branding-v1":
            return False
        skill_root = root / category / skill_id
        if not _skill_markdown_is_valid(skill_root / "SKILL.md"):
            return False
        try:
            if _tree_sha256(skill_root) != str(item.get("sha256") or ""):
                return False
        except OSError:
            return False
        seen.add((category, skill_id))
    actual = {
        path.parent.relative_to(root).as_posix()
        for path in root.rglob("SKILL.md")
        if path.is_file()
    }
    return actual == {f"{category}/{skill_id}" for category, skill_id in seen}


def _probe_skills() -> dict[str, object]:
    root = _agent_skills_root()
    if _installed_production():
        ready = root.is_dir() and _installed_product_skills_are_valid(root)
    else:
        ready = root.is_dir() and any(_skill_markdown_is_valid(path) for path in root.rglob("SKILL.md") if path.is_file())
    return {"status": "ready" if ready else "degraded"}


def _installed_production() -> bool:
    try:
        _license_module()  # Ensures the sibling Agent directory is importable.
        profile = importlib.import_module("taiji_runtime_profile")
        return bool(profile.is_installed_production())
    except Exception:
        return True


def _probe_node() -> dict[str, object]:
    runtime_home = str(os.environ.get("TAIJI_RUNTIME_HOME") or "").strip()
    packaged = Path(runtime_home).expanduser() / "runtime" / "node" / "bin" / "node" if runtime_home else None
    if _installed_production():
        available = bool(packaged and packaged.is_file() and os.access(packaged, os.X_OK))
    else:
        available = bool(packaged and packaged.is_file() and os.access(packaged, os.X_OK)) or shutil.which("node") is not None
    return {"status": "ready" if available else "degraded"}


def _default_probes() -> dict[str, dict[str, object]]:
    probes = {
        "webui": _probe_webui,
        "agent": _probe_agent,
        "gateway": _probe_gateway,
        "license": _probe_license,
        "docx": _probe_docx,
        "skills": _probe_skills,
        "node": _probe_node,
    }
    return {component_id: _safe_probe(probe) for component_id, probe in probes.items()}


def _public_component(component_id: str, label: str, raw: object) -> dict[str, object]:
    source = raw if isinstance(raw, Mapping) else {}
    status = str(source.get("status") or "unknown").strip().lower()
    if status not in _STATUSES:
        status = "unknown"
    component: dict[str, object] = {"id": component_id, "label": label, "status": status}
    version = _safe_version(source.get("version"))
    if version:
        component["version"] = version
    return component


def _overall_status(components: list[dict[str, object]]) -> str:
    required = {component_id for component_id, _label, is_required in _COMPONENTS if is_required}
    status_by_id = {str(item["id"]): str(item["status"]) for item in components}
    if any(status_by_id.get(component_id) == "blocked" for component_id in required):
        return "blocked"
    material = [str(item["status"]) for item in components if item["status"] != "not_applicable"]
    if any(status in {"blocked", "degraded", "unknown"} for status in material):
        return "degraded"
    return "ready"


def build_product_diagnostics(
    probes: Mapping[str, object] | None = None,
    *,
    now: object = None,
    incident_id: object = None,
) -> dict[str, object]:
    """Return a fixed-shape diagnostic summary with no raw probe details."""

    raw_probes = _default_probes() if probes is None else probes
    if not isinstance(raw_probes, Mapping):
        raw_probes = {}
    components = [
        _public_component(component_id, label, raw_probes.get(component_id))
        for component_id, label, _required in _COMPONENTS
    ]
    return {
        "schema": DIAGNOSTICS_SCHEMA,
        "generated_at": _safe_timestamp(now),
        "incident_id": _safe_incident_id(incident_id),
        "overall": _overall_status(components),
        "components": components,
    }


def _sanitize_diagnostics(summary: object) -> dict[str, object]:
    if not isinstance(summary, Mapping):
        return build_product_diagnostics()
    raw_components = summary.get("components")
    indexed = {}
    if isinstance(raw_components, list):
        for item in raw_components:
            if isinstance(item, Mapping) and str(item.get("id") or "") in {entry[0] for entry in _COMPONENTS}:
                indexed[str(item["id"])] = item
    components = [
        _public_component(component_id, label, indexed.get(component_id))
        for component_id, label, _required in _COMPONENTS
    ]
    claimed_overall = str(summary.get("overall") or "").strip().lower()
    calculated_overall = _overall_status(components)
    overall = claimed_overall if claimed_overall in _OVERALL_STATUSES and claimed_overall == calculated_overall else calculated_overall
    return {
        "schema": DIAGNOSTICS_SCHEMA,
        "generated_at": _safe_timestamp(summary.get("generated_at")),
        "incident_id": _safe_incident_id(summary.get("incident_id")),
        "overall": overall,
        "components": components,
    }


def build_support_bundle(summary: object) -> dict[str, object]:
    """Build a bounded JSON-only support bundle; logs and paths stay local."""

    bundle = {
        "schema": SUPPORT_BUNDLE_SCHEMA,
        "manifest": {
            "redacted": True,
            "logs_included": False,
            "paths_included": False,
            "secrets_included": False,
        },
        "diagnostics": _sanitize_diagnostics(summary),
    }
    rendered = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    if len(rendered.encode("utf-8")) >= SUPPORT_BUNDLE_MAX_BYTES:
        raise RuntimeError("support bundle exceeded public size limit")
    if any(len(line) > SUPPORT_BUNDLE_MAX_LINE_CHARS for line in rendered.splitlines()):
        raise RuntimeError("support bundle exceeded public line limit")
    return bundle
