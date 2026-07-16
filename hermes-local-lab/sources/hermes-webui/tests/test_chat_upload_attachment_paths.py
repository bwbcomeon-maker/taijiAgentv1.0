"""Regression coverage for WebUI chat upload path handoff."""
import io
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MESSAGES_JS = ROOT / "static" / "messages.js"
UPLOAD_PY = ROOT / "api" / "upload.py"


def test_uploads_keep_structured_attachment_payload_without_path_text():
    """The browser must not expose absolute attachment paths in user text.

    /api/upload returns a session-scoped opaque reference. The browser sends
    that reference only inside the structured attachments payload; backend
    attachment context ingestion resolves it inside the session inbox.
    """
    src = MESSAGES_JS.read_text(encoding="utf-8")

    assert "uploadedPaths=uploaded.map" not in src
    assert "I've uploaded" not in src
    assert "[Attached files:" not in src
    assert "attachments:uploaded.length?uploaded:undefined" in src

    ui_src = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    assert "ref: data.ref" in ui_src
    assert "path: data.path" not in ui_src

    upload_src = UPLOAD_PY.read_text(encoding="utf-8")
    handle_body = upload_src[upload_src.index("def handle_upload"):upload_src.index("def extract_archive", upload_src.index("def handle_upload"))]
    assert "'ref': dest.name" in handle_body
    assert "'path': str(dest)" not in handle_body


def test_attached_files_context_is_hidden_from_user_message_display():
    """Persist full attachment paths for the agent without showing them in chat."""
    ui_src = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    assert "function _stripAttachedFilesMarkerForDisplay" in ui_src
    assert "_stripAttachedFilesMarkerForDisplay(_stripWorkspaceDisplayPrefix(content))" in ui_src
    assert "dataset.rawText=String(displayContent).trim()" in ui_src


def test_attached_files_context_is_hidden_from_sidebar_titles():
    """Sidebar rows should not expose absolute uploaded image paths in titles."""
    sessions_src = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")

    assert "function _stripAttachedFilesMarker" in sessions_src
    assert "? _stripAttachedFilesMarker" in sessions_src
    assert "replace(/\\n\\n\\[Attached files: [^\\]]+\\]$/" in sessions_src


def test_server_provisional_titles_strip_attached_files_context():
    """Server-generated provisional titles must not include the path suffix."""
    from api.models import title_from

    title = title_from([
        {
            "role": "user",
            "content": "why is llm wiki not working?\n\n[Attached files: /tmp/private/Screenshot.png]",
        }
    ])

    assert title == "why is llm wiki not working?"
    assert "Attached files" not in title
    assert "/tmp/private" not in title


def test_duplicate_upload_response_reports_actual_stored_filename(tmp_path, monkeypatch):
    """Duplicate upload names should report the suffixed stored basename."""
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(tmp_path))

    from api.upload import _sanitize_upload_name, _upload_destination

    safe_name = _sanitize_upload_name("photo.png")
    first = _upload_destination("session-a", safe_name)
    first.write_bytes(b"first")
    second = _upload_destination("session-a", safe_name)

    assert first.name == "photo.png"
    assert second.name == "photo-1.png"

    src = UPLOAD_PY.read_text(encoding="utf-8")
    handle_body = src[src.index("def handle_upload"):src.index("def extract_archive", src.index("def handle_upload"))]
    assert "'filename': dest.name" in handle_body
    assert "'filename': safe_name" not in handle_body


class _UploadHandler:
    def __init__(self, body: bytes, content_type: str):
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        }
        self.status = None

    def send_response(self, status):
        self.status = status

    def send_header(self, _key, _value):
        pass

    def end_headers(self):
        pass

    def payload(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _upload_body(session_id: str, filename: str, content: bytes):
    boundary = b"attachment-ref-boundary"
    body = (
        b"--" + boundary + b"\r\n"
        + b'Content-Disposition: form-data; name="session_id"\r\n\r\n'
        + session_id.encode("utf-8") + b"\r\n"
        + b"--" + boundary + b"\r\n"
        + f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8")
        + b"Content-Type: image/png\r\n\r\n"
        + content + b"\r\n"
        + b"--" + boundary + b"--\r\n"
    )
    return body, f"multipart/form-data; boundary={boundary.decode()}"


def test_duplicate_upload_response_separates_display_name_from_storage_ref(
    tmp_path,
    monkeypatch,
):
    from api import upload

    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(tmp_path))
    monkeypatch.setattr(upload, "get_session", lambda _sid: object())
    first = upload._upload_destination("session-a", "photo.png")
    first.write_bytes(b"first")

    body, content_type = _upload_body("session-a", "photo.png", b"second")
    handler = _UploadHandler(body, content_type)
    upload.handle_upload(handler)

    assert handler.status == 200
    assert handler.payload() == {
        "name": "photo.png",
        "filename": "photo-1.png",
        "ref": "photo-1.png",
        "size": len(b"second"),
        "mime": "image/png",
        "is_image": True,
    }
    ui_src = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    assert "name: data.name||data.filename" in ui_src


@pytest.mark.parametrize(
    "raw_name",
    [r"C:\Users\alice\private\photo.png", r"\\server\share\private\photo.png"],
)
def test_upload_display_name_uses_cross_platform_basename(raw_name):
    from api.upload import _sanitize_upload_name

    safe_name = _sanitize_upload_name(raw_name)

    assert safe_name == "photo.png"
    assert "alice" not in safe_name
    assert "server" not in safe_name
