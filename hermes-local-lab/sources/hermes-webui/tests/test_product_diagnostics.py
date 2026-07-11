import json
from pathlib import Path


from api.product_diagnostics import (
    DIAGNOSTICS_SCHEMA,
    SUPPORT_BUNDLE_MAX_BYTES,
    SUPPORT_BUNDLE_MAX_LINE_CHARS,
    SUPPORT_BUNDLE_SCHEMA,
    build_product_diagnostics,
    build_support_bundle,
    _probe_docx,
    _probe_gateway,
    _probe_node,
    _probe_skills,
    _tree_sha256,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_ready_docx_root(root: Path) -> None:
    package = {
        "name": "docx-engine-v2",
        "version": "0.1.0",
        "dependencies": {"ajv": "1.0.0"},
    }
    registry = {
        "version": 1,
        "builtin": [
            {"templateId": "general-proposal", "path": "templates/general-proposal"},
            {"templateId": "meeting-minutes", "path": "templates/meeting-minutes"},
        ],
        "installed": [],
    }
    (root / "src/cli").mkdir(parents=True)
    (root / "src/workflow").mkdir(parents=True)
    (root / "node_modules/ajv").mkdir(parents=True)
    for template_id in ("general-proposal", "meeting-minutes"):
        template = root / "templates" / template_id / "template.docx"
        template.parent.mkdir(parents=True)
        template.write_bytes(b"PK\x03\x04" + b"fixture" * 16)
    (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (root / "template-registry.json").write_text(json.dumps(registry), encoding="utf-8")
    (root / "node_modules/ajv/package.json").write_text('{"name":"ajv"}', encoding="utf-8")
    (root / "src/cli/run-job.js").write_text("#!/usr/bin/env node\n" + "x" * 80, encoding="utf-8")
    (root / "src/workflow/run-document-job.js").write_text("'use strict';\n" + "x" * 80, encoding="utf-8")


def test_diagnostics_only_emit_fixed_public_component_fields():
    probes = {
        "webui": {"status": "ready", "version": "0.1.0", "path": "/Users/alice/app"},
        "agent": {"status": "blocked", "error": "sentinel-secret"},
        "gateway": {"status": "ready", "endpoint": "http://127.0.0.1:9999"},
        "license": {"status": "ready", "token": "sentinel-token"},
        "docx": {"status": "degraded", "detail": "HERMES_HOME=/tmp/private"},
        "skills": {"status": "ready", "names": ["private-skill"]},
        "node": {"status": "not_applicable", "binary": "/opt/node"},
        "attacker": {"status": "ready", "secret": "sentinel-extra"},
    }

    payload = build_product_diagnostics(
        probes=probes,
        now="2026-07-11T02:00:00Z",
        incident_id="inc-0123456789ab",
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["schema"] == DIAGNOSTICS_SCHEMA
    assert payload["overall"] == "blocked"
    assert [item["id"] for item in payload["components"]] == [
        "webui",
        "agent",
        "gateway",
        "license",
        "docx",
        "skills",
        "node",
    ]
    assert all(set(item) <= {"id", "label", "status", "version"} for item in payload["components"])
    for forbidden in (
        "/Users/",
        "127.0.0.1",
        "sentinel",
        "HERMES_HOME",
        "private-skill",
        "/opt/node",
        "attacker",
    ):
        assert forbidden not in rendered


def test_diagnostics_reject_unknown_status_and_unsafe_version():
    payload = build_product_diagnostics(
        probes={
            "webui": {"status": "pwned", "version": "0.1.0 /Users/alice"},
            "agent": {"status": "ready"},
        }
    )
    webui = next(item for item in payload["components"] if item["id"] == "webui")

    assert webui == {"id": "webui", "label": "桌面界面", "status": "unknown"}


def test_diagnostics_reject_secret_like_and_internal_brand_versions():
    for unsafe in (
        "sk-sentinel-secret",
        "v1.2.3-sk-sentinel",
        "PASSWORD_SECRET_123",
        "HermesAgent-1.0",
    ):
        payload = build_product_diagnostics(probes={"webui": {"status": "ready", "version": unsafe}})
        webui = next(item for item in payload["components"] if item["id"] == "webui")
        assert "version" not in webui
        assert unsafe not in json.dumps(build_support_bundle(payload), ensure_ascii=False)


def test_support_bundle_is_bounded_redacted_and_contains_no_logs():
    summary = build_product_diagnostics(
        probes={component: {"status": "ready"} for component in (
            "webui", "agent", "gateway", "license", "docx", "skills", "node"
        )},
        now="2026-07-11T02:00:00Z",
        incident_id="inc-0123456789ab",
    )

    bundle = build_support_bundle(summary)
    rendered = json.dumps(bundle, ensure_ascii=False)

    assert bundle["schema"] == SUPPORT_BUNDLE_SCHEMA
    assert bundle["manifest"] == {
        "redacted": True,
        "logs_included": False,
        "paths_included": False,
        "secrets_included": False,
    }
    assert bundle["diagnostics"] == summary
    assert len(rendered.encode("utf-8")) < SUPPORT_BUNDLE_MAX_BYTES
    assert max(len(line) for line in rendered.splitlines()) <= SUPPORT_BUNDLE_MAX_LINE_CHARS
    assert "traceback" not in rendered.lower()
    assert "/Users/" not in rendered


def test_routes_expose_diagnostics_and_export_endpoints():
    source = (ROOT / "api/routes.py").read_text(encoding="utf-8")

    assert 'parsed.path == "/api/product/diagnostics"' in source
    assert 'parsed.path == "/api/product/diagnostics/export"' in source
    assert "build_product_diagnostics" in source
    assert "build_support_bundle" in source
    assert 'product_error["incident_id"]' in source


def test_default_probes_require_real_runtime_entries(tmp_path, monkeypatch):
    docx_root = tmp_path / "docx"
    docx_root.mkdir()
    monkeypatch.setenv("TAIJI_DOCX_BUILTIN_ROOT", str(docx_root))
    assert _probe_docx() == {"status": "degraded"}

    runtime_home = tmp_path / "runtime-home"
    (runtime_home / "skills").mkdir(parents=True)
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("TAIJI_WEBUI_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setattr("api.product_diagnostics.shutil.which", lambda _name: "/usr/local/bin/node")
    monkeypatch.setattr("api.product_diagnostics._installed_production", lambda: True)
    assert _probe_skills() == {"status": "degraded"}
    assert _probe_node() == {"status": "degraded"}


def test_installed_node_probe_requires_packaged_executable(tmp_path, monkeypatch):
    runtime_home = tmp_path / "runtime-home"
    node = runtime_home / "runtime" / "node" / "bin" / "node"
    node.parent.mkdir(parents=True)
    node.write_text("#!/bin/sh\n", encoding="utf-8")
    node.chmod(0o755)
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setattr("api.product_diagnostics._installed_production", lambda: True)

    assert _probe_node() == {"status": "ready"}


def test_source_node_probe_may_use_developer_path(tmp_path, monkeypatch):
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.setattr("api.product_diagnostics._installed_production", lambda: False)
    monkeypatch.setattr("api.product_diagnostics.shutil.which", lambda _name: "/usr/local/bin/node")

    assert _probe_node() == {"status": "ready"}


def test_gateway_probe_requires_configured_auth_and_live_health(monkeypatch):
    monkeypatch.setattr(
        "api.gateway_chat.gateway_chat_config_status",
        lambda: {"enabled": True, "base_url_configured": True, "api_key_configured": False},
    )
    monkeypatch.setattr("api.gateway_chat.gateway_chat_authenticated_probe", lambda: True)
    assert _probe_gateway() == {"status": "degraded"}

    monkeypatch.setattr(
        "api.gateway_chat.gateway_chat_config_status",
        lambda: {"enabled": True, "base_url_configured": True, "api_key_configured": True},
    )
    monkeypatch.setattr("api.gateway_chat.gateway_chat_authenticated_probe", lambda: False)
    assert _probe_gateway() == {"status": "blocked"}

    monkeypatch.setattr("api.gateway_chat.gateway_chat_authenticated_probe", lambda: True)
    assert _probe_gateway() == {"status": "ready"}


def test_gateway_authenticated_probe_uses_configured_key(monkeypatch):
    from api import gateway_chat

    captured = {}

    class Response:
        status = 200

        def getcode(self):
            return self.status

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", fake_urlopen)
    environ = {
        "HERMES_WEBUI_CHAT_BACKEND": "gateway",
        "HERMES_WEBUI_GATEWAY_BASE_URL": "http://127.0.0.1:18642",
        "HERMES_WEBUI_GATEWAY_API_KEY": "sentinel-key",
    }

    assert gateway_chat.gateway_chat_authenticated_probe(environ=environ) is True
    assert captured == {
        "url": "http://127.0.0.1:18642/v1/models",
        "authorization": "Bearer sentinel-key",
        "timeout": 0.75,
    }


def test_docx_probe_rejects_empty_or_corrupt_runtime(tmp_path, monkeypatch):
    root = tmp_path / "docx"
    root.mkdir()
    monkeypatch.setenv("TAIJI_DOCX_BUILTIN_ROOT", str(root))
    _write_ready_docx_root(root)

    assert _probe_docx() == {"status": "ready"}

    (root / "src/cli/run-job.js").write_text("", encoding="utf-8")
    assert _probe_docx() == {"status": "degraded"}


def test_installed_skills_probe_requires_valid_product_manifest(tmp_path, monkeypatch):
    agent_root = tmp_path / "agent"
    skills = agent_root / "skills"
    skill = skills / "productivity" / "docx-template-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: docx-template-skill\ndescription: Generate documents\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "taiji-product-skills/v1",
        "skills": [{
            "id": "docx-template-skill",
            "path": "productivity/docx-template-skill",
            "source_sha256": "b" * 64,
            "sha256": _tree_sha256(skill.parent),
            "productization": "skill-md-visible-branding-v1",
        }],
    }
    (skills / "product-skills.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("TAIJI_WEBUI_AGENT_DIR", str(agent_root))
    monkeypatch.setattr("api.product_diagnostics._installed_production", lambda: True)

    assert _probe_skills() == {"status": "ready"}

    manifest["skills"][0]["sha256"] = "not-a-digest"
    (skills / "product-skills.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert _probe_skills() == {"status": "degraded"}

    manifest["skills"][0]["sha256"] = _tree_sha256(skill.parent)
    (skills / "product-skills.json").write_text(json.dumps(manifest), encoding="utf-8")
    skill.write_text(skill.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    assert _probe_skills() == {"status": "degraded"}
