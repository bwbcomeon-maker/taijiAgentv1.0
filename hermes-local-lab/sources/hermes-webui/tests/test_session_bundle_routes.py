import base64
import io
import json
from types import SimpleNamespace
from urllib.parse import urlparse


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class Handler:
    def __init__(self, body=b"", content_type="application/json"):
        self.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": content_type,
        }
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = {}
        self.close_connection = False

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        pass


def test_bundle_export_is_binary_and_json_export_remains_compatible(tmp_path, monkeypatch):
    import api.routes as routes
    from api.artifacts import ArtifactRegistry
    from api.session_bundle import inspect_session_bundle

    registry = ArtifactRegistry(tmp_path / "artifacts")
    artifact = registry.register_image_bytes(
        "sid", "turn", "tool", PNG_1X1, mime="image/png", name="image.png"
    )
    session = SimpleNamespace(
        session_id="sid", title="Bundle", model="model", messages=[{
            "role": "assistant", "content": "done", "artifacts": [artifact]
        }], tool_calls=[],
    )
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_artifact_registry", lambda: registry)

    bundle_handler = Handler()
    assert routes.handle_get(
        bundle_handler, urlparse("/api/session/export-bundle?session_id=sid")
    ) is True
    assert bundle_handler.status == 200
    assert bundle_handler.response_headers["Content-Type"] == "application/zip"
    assert bundle_handler.response_headers["Content-Disposition"] == (
        'attachment; filename="taiji-session-sid.zip"'
    )
    assert "sid" not in json.dumps(bundle_handler.response_headers).replace(
        "taiji-session-sid.zip", ""
    )
    assert inspect_session_bundle(bundle_handler.wfile.getvalue()).session["session_id"] == "sid"

    json_handler = Handler()
    assert routes.handle_get(
        json_handler, urlparse("/api/session/export?session_id=sid")
    ) is True
    assert json_handler.response_headers["Content-Type"].startswith("application/json")
    assert json.loads(json_handler.wfile.getvalue())["session_id"] == "sid"


def test_bundle_import_reads_bounded_raw_zip_without_json_parser(monkeypatch):
    import api.routes as routes
    import api.session_bundle as session_bundle

    raw = b"PK\x03\x04binary-zip"
    captured = {}
    imported = SimpleNamespace(
        session_id="new-session", messages=[], tool_calls=[],
        compact=lambda: {"session_id": "new-session", "title": "Imported"},
    )

    def fake_import(payload, registry, **kwargs):
        captured["payload"] = payload
        captured.update(kwargs)
        return imported

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "read_body", lambda _handler: (_ for _ in ()).throw(
        AssertionError("ZIP must not pass through JSON read_body")
    ))
    monkeypatch.setattr(routes, "_artifact_registry", lambda: object())
    monkeypatch.setattr(routes, "_persist_new_session_truth", lambda _session: None)
    monkeypatch.setattr(session_bundle, "import_session_bundle", fake_import)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda _reason: None)

    handler = Handler(raw, "application/zip")
    routes.handle_post(handler, urlparse("/api/session/import-bundle"))
    assert handler.status == 200
    assert captured["payload"] == raw
    assert json.loads(handler.wfile.getvalue())["session"]["session_id"] == "new-session"


def test_bundle_import_rejects_wrong_type_and_oversized_body_without_reading(monkeypatch):
    import api.routes as routes

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    wrong = Handler(b"{}", "application/json")
    routes.handle_post(wrong, urlparse("/api/session/import-bundle"))
    assert wrong.status == 415

    oversized = Handler()
    oversized.headers["Content-Type"] = "application/zip"
    oversized.headers["Content-Length"] = str(31 * 1024 * 1024)
    oversized.rfile = SimpleNamespace(read=lambda *_args: (_ for _ in ()).throw(
        AssertionError("oversized body must be rejected before read")
    ))
    routes.handle_post(oversized, urlparse("/api/session/import-bundle"))
    assert oversized.status == 413


def test_migration_routes_require_confirmation_and_hide_backup_path(monkeypatch, tmp_path):
    import api.routes as routes
    import api.legacy_session_migration as migration

    internal_report = {
        "scanned": 3, "modified": 2, "skipped": 1, "failed": 0,
        "needs_repair": True,
        "backup_path": "/Users/private/runtime/migration-backups/batch-1",
        "quarantine_count": 2,
        "quarantine_status": "manual_review_required",
        "items": [{
            "session_id": "sid", "code": "legacy_privacy_taint",
            "reason": "legacy_unbounded_taint",
        }],
    }
    monkeypatch.setattr(routes, "_artifact_registry", lambda: object())
    monkeypatch.setattr(migration, "audit_legacy_sessions", lambda *_args: internal_report)
    monkeypatch.setattr(migration, "migrate_legacy_sessions", lambda *_args, **_kwargs: internal_report)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)

    audit_handler = Handler()
    routes.handle_get(audit_handler, urlparse("/api/session/migration/audit"))
    audit_payload = json.loads(audit_handler.wfile.getvalue())
    assert audit_payload["scanned"] == 3
    assert audit_payload["quarantine_count"] == 2
    assert audit_payload["quarantine_status"] == "manual_review_required"
    assert "backup_path" not in json.dumps(audit_payload)

    monkeypatch.setattr(routes, "read_body", lambda _handler: {"confirm": False})
    rejected = Handler()
    routes.handle_post(rejected, urlparse("/api/session/migration/apply"))
    assert rejected.status == 400

    monkeypatch.setattr(routes, "read_body", lambda _handler: {"confirm": True})
    applied = Handler()
    routes.handle_post(applied, urlparse("/api/session/migration/apply"))
    payload = json.loads(applied.wfile.getvalue())
    assert payload["backup_created"] is True
    assert "backup_path" not in json.dumps(payload)
    assert "/Users/private" not in applied.wfile.getvalue().decode()


def test_bundle_import_rollback_incomplete_returns_only_safe_public_code(monkeypatch):
    import api.routes as routes
    import api.session_bundle as session_bundle

    raw = b"PK\x03\x04binary-zip"

    def _fail_import(*_args, **_kwargs):
        raise session_bundle.BundleImportRollbackError(
            "private-session-id", ["artifact_cleanup", "/Users/private/path"]
        )

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_artifact_registry", lambda: object())
    monkeypatch.setattr(session_bundle, "import_session_bundle", _fail_import)
    handler = Handler(raw, "application/zip")
    routes.handle_post(handler, urlparse("/api/session/import-bundle"))
    payload = json.loads(handler.wfile.getvalue())
    assert handler.status == 500
    assert payload == {
        "error": "Failed to import session bundle",
        "code": "rollback_incomplete",
    }
    serialized = json.dumps(payload)
    assert "private-session-id" not in serialized
    assert "/Users/" not in serialized


def test_server_startup_contract_calls_audit_only():
    source = ( __import__("pathlib").Path(__file__).parents[1] / "server.py").read_text("utf-8")
    startup = source[source.index("# ── #1558 startup self-heal"):source.index("within_container = False")]
    assert "audit_legacy_sessions" in startup
    assert "migrate_legacy_sessions" not in startup


def test_legacy_json_import_rebuilds_a_text_only_allowlist():
    import api.routes as routes

    canary_path = "/Users/private/runtime/image.png"
    raw = [
        {
            "role": "user", "content": "keep user text", "timestamp": 123,
            "attachments": [{"path": canary_path}],
            "reasoning": "secret", "context_messages": [{"role": "user"}],
            "tool_calls": [{"name": "raw", "args": {"token": "secret"}}],
            "artifacts": [{"artifact_id": "forged"}],
        },
        {
            "role": "assistant",
            "content": f"keep assistant text\nMEDIA:{canary_path}\nend",
            "_ts": "2026-07-17T00:00:00Z",
            "artifact_errors": [{"path": canary_path}],
        },
        {"role": "tool", "content": "raw result", "args": {"token": "secret"}},
        {"role": "system", "content": "internal prompt"},
        {"role": "assistant", "content": [{"type": "text", "text": "structured"}]},
    ]

    projected = routes._legacy_json_text_messages(raw)

    assert projected == [
        {"role": "user", "content": "keep user text", "timestamp": 123},
        {
            "role": "assistant", "content": "keep assistant text\nend",
            "_ts": "2026-07-17T00:00:00Z",
        },
    ]
    serialized = json.dumps(projected, ensure_ascii=False)
    for forbidden in (
        canary_path, "MEDIA:", "attachments", "reasoning", "context_messages",
        "tool_calls", "artifacts", "artifact_errors", "raw result", "internal prompt",
    ):
        assert forbidden not in serialized


def test_legacy_json_import_route_never_copies_top_level_tool_state():
    import inspect
    import api.routes as routes

    source = inspect.getsource(routes._handle_session_import)
    assert "_legacy_json_text_messages" in source
    assert "tool_calls=[]" in source
    assert 'body.get("tool_calls"' not in source


def test_bundle_import_rejects_ambiguous_http_framing_and_closes_connection(monkeypatch):
    import api.routes as routes

    class DuplicateHeaders(dict):
        def get_all(self, name):
            if name.lower() == "content-length":
                return ["10", "10"]
            return None

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    duplicate = Handler(b"0123456789", "application/zip")
    duplicate.headers = DuplicateHeaders(duplicate.headers)
    routes.handle_post(duplicate, urlparse("/api/session/import-bundle"))
    assert duplicate.status == 400
    assert duplicate.close_connection is True

    encoded = Handler(b"chunked-body", "application/zip")
    encoded.headers["Transfer-Encoding"] = "chunked"
    routes.handle_post(encoded, urlparse("/api/session/import-bundle"))
    assert encoded.status == 400
    assert encoded.close_connection is True


def test_migration_audit_get_constructs_a_strict_read_only_registry(
    tmp_path, monkeypatch
):
    import api.artifacts as artifacts
    import api.legacy_session_migration as migration
    import api.routes as routes

    calls = []

    class ReadOnlyRegistry:
        def __init__(self, root, **kwargs):
            calls.append((root, kwargs))

        def cleanup_retired(self):
            raise AssertionError("GET audit must not run cleanup")

    monkeypatch.setattr(routes, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(artifacts, "ArtifactRegistry", ReadOnlyRegistry)
    monkeypatch.setattr(
        migration, "audit_legacy_sessions",
        lambda *_args: {
            "scanned": 0, "modified": 0, "skipped": 0, "failed": 0,
            "needs_repair": False, "backup_path": None, "items": [],
        },
    )
    handler = Handler()
    routes.handle_get(handler, urlparse("/api/session/migration/audit"))

    assert handler.status == 200
    assert calls == [(tmp_path / "state" / "artifacts", {"create_root": False})]
    assert not (tmp_path / "state" / "artifacts").exists()
