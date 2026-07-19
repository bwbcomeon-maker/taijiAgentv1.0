"""Tests for native multimodal image attachment support (PR #1229).

Verifies _build_native_multimodal_message, _normalize_chat_attachments,
and _attachment_name from api.streaming / api.routes behave correctly
across the workspace-path safety, size ceiling, multi-image, MIME, and
fallback cases the maintainer asked about.
"""
import base64
import copy
import os
import struct
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from api.streaming import (
    _attachment_name,
    _build_native_multimodal_message,
    _NATIVE_IMAGE_MAX_BYTES,
    _sanitize_messages_for_api,
)
from api.routes import _normalize_chat_attachments


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_png(path: Path, size: int = 0) -> Path:
    """Write a minimal valid PNG to *path* (IHDR + IDAT + IEND)."""
    if size <= 0:
        # smallest valid PNG (67 bytes)
        data = (
            b'\x89PNG\r\n\x1a\n'
            b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
            b'\x00\x00\x00\x0bIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N'
            b'\x00\x00\x00\x00IEND\xaeB`\x82'
        )
    else:
        data = b'\x89PNG\r\n\x1a\n' + b'\x00' * (size - 8)
    path.write_bytes(data)
    return path


def _make_jpeg(path: Path, size: int = 107) -> Path:
    """Write a tiny but valid JPEG."""
    data = (
        b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
        b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n'
        b'\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a'
        b'\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342'
        b'\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00'
        b'\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00'
        b'\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b'
        b'\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\x00'
        b'\xff\xd9'
    )
    if size > len(data):
        data += b'\x00' * (size - len(data))
    path.write_bytes(data[:size] if size < len(data) else data)
    return path


def _chat_inbox(tmp_path: Path, monkeypatch, session_id: str = "test-session") -> tuple[Path, Path]:
    attachment_root = tmp_path / "attachments"
    inbox = attachment_root / session_id
    workspace = tmp_path / "workspace"
    inbox.mkdir(parents=True)
    workspace.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    return inbox, workspace


# ── _attachment_name ────────────────────────────────────────────────────────

class TestAttachmentName:
    def test_dict_with_name(self):
        assert _attachment_name({'name': 'photo.png', 'path': '/tmp/x'}) == 'photo.png'

    def test_dict_with_filename_fallback(self):
        assert _attachment_name({'filename': 'img.jpg'}) == 'img.jpg'

    def test_dict_with_path_fallback(self):
        assert _attachment_name({'path': '/ws/snap.png'}) == '/ws/snap.png'

    def test_string_attachment(self):
        assert _attachment_name('readme.md') == 'readme.md'

    def test_empty_attachment(self):
        assert _attachment_name({}) == ''

    def test_none_attachment(self):
        assert _attachment_name(None) == ''


# ── _normalize_chat_attachments ─────────────────────────────────────────────

class TestNormalizeChatAttachments:
    def test_legacy_string_list(self):
        result = _normalize_chat_attachments(['a.png', 'b.txt'])
        assert result == [
            {'name': 'a.png', 'path': '', 'mime': ''},
            {'name': 'b.txt', 'path': '', 'mime': ''},
        ]

    def test_dict_with_mime_and_is_image(self):
        result = _normalize_chat_attachments([{
            'name': 'photo.png', 'path': '/ws/photo.png',
            'mime': 'image/png', 'size': 1234, 'is_image': True,
        }])
        assert result == [{
            'name': 'photo.png', 'path': '/ws/photo.png',
            'mime': 'image/png', 'size': 1234, 'is_image': True,
        }]

    def test_dict_missing_fields_defaults(self):
        result = _normalize_chat_attachments([{'path': '/x'}])
        assert result == [{'name': '/x', 'path': '/x', 'mime': ''}]

    def test_mixed_list(self):
        result = _normalize_chat_attachments([
            'old.txt',
            {'name': 'new.png', 'path': '/ws/new.png', 'mime': 'image/png'},
        ])
        assert len(result) == 2
        assert result[0] == {'name': 'old.txt', 'path': '', 'mime': ''}
        assert result[1]['name'] == 'new.png'

    def test_empty_list(self):
        assert _normalize_chat_attachments([]) == []

    def test_not_a_list(self):
        assert _normalize_chat_attachments(None) == []
        assert _normalize_chat_attachments('abc') == []


# ── _build_native_multimodal_message ────────────────────────────────────────

class TestBuildNativeMultimodalMessage:
    def test_no_attachments_returns_string(self):
        result = _build_native_multimodal_message('[WS: x]\n', 'describe', [], '/ws', session_id='test-session')
        assert result == '[WS: x]\ndescribe'

    def test_single_image_in_session_inbox(self, tmp_path, monkeypatch):
        inbox, workspace = _chat_inbox(tmp_path, monkeypatch)
        img = inbox / 'pic.png'
        _make_png(img)
        atts = _normalize_chat_attachments([{
            'name': 'pic.png', 'ref': 'pic.png',
            'mime': 'image/png', 'size': img.stat().st_size, 'is_image': True,
        }])
        result = _build_native_multimodal_message('[WS]\n', 'look', atts, str(workspace), session_id='test-session')
        assert isinstance(result, list)
        assert result[0] == {'type': 'text', 'text': '[WS]\nlook'}
        assert len(result) == 2
        assert result[1]['type'] == 'image_url'
        url = result[1]['image_url']['url']
        assert url.startswith('data:image/png;base64,')
        decoded = base64.b64decode(url.split(',', 1)[1])
        assert decoded[:4] == b'\x89PNG'

    def test_jpeg_image_in_session_inbox(self, tmp_path, monkeypatch):
        inbox, workspace = _chat_inbox(tmp_path, monkeypatch)
        img = inbox / 'photo.jpeg'
        _make_jpeg(img)
        atts = _normalize_chat_attachments([{
            'name': 'photo.jpeg', 'ref': 'photo.jpeg',
            'mime': 'image/jpeg', 'size': img.stat().st_size, 'is_image': True,
        }])
        result = _build_native_multimodal_message('', 'hi', atts, str(workspace), session_id='test-session')
        assert result[1]['image_url']['url'].startswith('data:image/jpeg;base64,')

    def test_multiple_images_become_multiple_parts(self, tmp_path, monkeypatch):
        inbox, workspace = _chat_inbox(tmp_path, monkeypatch)
        img1 = inbox / 'a.png'
        img2 = inbox / 'b.png'
        _make_png(img1)
        _make_png(img2)
        atts = _normalize_chat_attachments([
            {'name': 'a.png', 'ref': 'a.png', 'mime': 'image/png', 'size': img1.stat().st_size, 'is_image': True},
            {'name': 'b.png', 'ref': 'b.png', 'mime': 'image/png', 'size': img2.stat().st_size, 'is_image': True},
        ])
        result = _build_native_multimodal_message('', 'multi', atts, str(workspace), session_id='test-session')
        image_parts = [p for p in result if p['type'] == 'image_url']
        assert len(image_parts) == 2

    def test_non_image_attachment_stays_text_fallback(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            doc = root / 'notes.txt'
            doc.write_text('hello')
            atts = _normalize_chat_attachments([{
                'name': 'notes.txt', 'path': str(doc),
                'mime': 'text/plain', 'size': doc.stat().st_size, 'is_image': False,
            }])
            result = _build_native_multimodal_message('[WS]\n', 'read', atts, str(root), session_id='test-session')
            assert isinstance(result, str)
            assert 'read' in result

    def test_outside_workspace_path_rejected(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            outside = Path(d) / '..' / 'outside.png'
            outside = outside.resolve()
            _make_png(outside)
            atts = _normalize_chat_attachments([{
                'name': 'outside.png', 'path': str(outside),
                'mime': 'image/png', 'size': outside.stat().st_size, 'is_image': True,
            }])
            result = _build_native_multimodal_message('', 'hi', atts, str(root), session_id='test-session')
            # Should fall back to string; outside path is rejected
            assert isinstance(result, str)

    def test_symlink_inside_session_inbox_rejected(self, tmp_path, monkeypatch):
        """Chat attachment refs never follow symlinks, even within the inbox."""
        inbox, workspace = _chat_inbox(tmp_path, monkeypatch)
        real_file = inbox / 'real.png'
        _make_png(real_file)
        link = inbox / 'link.png'
        os.symlink(str(real_file), str(link))
        atts = _normalize_chat_attachments([{
            'name': 'link.png', 'ref': 'link.png',
            'mime': 'image/png', 'size': real_file.stat().st_size, 'is_image': True,
        }])
        result = _build_native_multimodal_message('', 'hi', atts, str(workspace), session_id='test-session')
        assert isinstance(result, str)

    def test_symlink_pointing_outside_workspace_rejected(self):
        """Symlink inside workspace pointing outside must be rejected by .resolve()."""
        with TemporaryDirectory() as d:
            root = Path(d)
            outside_file = Path(d) / '..' / 'escape.png'
            outside_file = outside_file.resolve()
            _make_png(outside_file)
            link = root / 'trap.link'
            os.symlink(str(outside_file), str(link))
            atts = _normalize_chat_attachments([{
                'name': 'trap.link', 'path': str(link),
                'mime': 'image/png', 'size': outside_file.stat().st_size, 'is_image': True,
            }])
            result = _build_native_multimodal_message('', 'hi', atts, str(root), session_id='test-session')
            assert isinstance(result, str)

    def test_size_above_cap_rejected(self):
        """Images larger than _NATIVE_IMAGE_MAX_BYTES must not be included."""
        with TemporaryDirectory() as d:
            root = Path(d)
            huge = root / 'huge.png'
            _make_png(huge, size=_NATIVE_IMAGE_MAX_BYTES + 1)
            atts = _normalize_chat_attachments([{
                'name': 'huge.png', 'path': str(huge),
                'mime': 'image/png', 'size': huge.stat().st_size, 'is_image': True,
            }])
            result = _build_native_multimodal_message('', 'hi', atts, str(root), session_id='test-session')
            assert isinstance(result, str)

    def test_missing_path_skipped(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            atts = _normalize_chat_attachments([{
                'name': 'ghost.png', 'path': str(root / 'no-such.png'),
                'mime': 'image/png', 'is_image': True,
            }])
            result = _build_native_multimodal_message('', 'hi', atts, str(root), session_id='test-session')
            assert isinstance(result, str)

    def test_no_mime_guessed_from_extension(self, tmp_path, monkeypatch):
        inbox, workspace = _chat_inbox(tmp_path, monkeypatch)
        img = inbox / 'pic.png'
        _make_png(img)
        atts = _normalize_chat_attachments([{
            'name': 'pic.png', 'ref': 'pic.png',
            'mime': '', 'size': img.stat().st_size, 'is_image': True,
        }])
        result = _build_native_multimodal_message('', 'hi', atts, str(workspace), session_id='test-session')
        assert isinstance(result, list)
        assert result[1]['image_url']['url'].startswith('data:image/png;base64,')

    def test_mixed_image_and_nonimage(self, tmp_path, monkeypatch):
        """Non-image is skipped; image still goes through."""
        inbox, workspace = _chat_inbox(tmp_path, monkeypatch)
        img = inbox / 'pic.png'
        _make_png(img)
        doc = inbox / 'readme.md'
        doc.write_text('# hello')
        atts = _normalize_chat_attachments([
            {'name': 'pic.png', 'ref': 'pic.png', 'mime': 'image/png', 'size': img.stat().st_size, 'is_image': True},
            {'name': 'readme.md', 'ref': 'readme.md', 'mime': 'text/markdown', 'size': doc.stat().st_size, 'is_image': False},
        ])
        result = _build_native_multimodal_message('', 'hi', atts, str(workspace), session_id='test-session')
        assert isinstance(result, list)
        image_parts = [p for p in result if p['type'] == 'image_url']
        assert len(image_parts) == 1
        assert 'hi' in result[0]['text']

    def test_upload_result_structure_roundtrip(self, tmp_path, monkeypatch):
        """Simulate the full flow: upload result → normalize → build message."""
        root = tmp_path / 'workspace'
        root.mkdir()
        attachment_root = tmp_path / 'attachments'
        session_dir = attachment_root / 'test-session'
        session_dir.mkdir(parents=True)
        monkeypatch.setenv('HERMES_WEBUI_ATTACHMENT_DIR', str(attachment_root))
        img = session_dir / 'screenshot.png'
        _make_png(img)
        upload_result = {
            'filename': 'screenshot.png',
            'ref': 'screenshot.png',
            'mime': 'image/png',
            'size': img.stat().st_size,
            'is_image': True,
        }
        frontend_payload = [{
            'name': upload_result['filename'],
            'ref': upload_result['ref'],
            'mime': upload_result['mime'],
            'size': upload_result['size'],
            'is_image': upload_result['is_image'],
        }]
        normalized = _normalize_chat_attachments(frontend_payload)
        result = _build_native_multimodal_message(
            '[WS]\n', 'describe this', normalized, str(root), session_id='test-session'
        )
        assert isinstance(result, list)
        assert result[1]['type'] == 'image_url'
        data_url = result[1]['image_url']['url']
        assert data_url.startswith('data:image/png;base64,')
        assert len(result) == 2

    def test_pure_text_history_does_not_read_image_capability_state(
        self,
        monkeypatch,
    ):
        """Pure text replay must not consult mutable image capability state."""
        calls = []
        monkeypatch.setattr(
            "api.streaming._resolve_image_input_mode",
            lambda *_args, **_kwargs: calls.append("resolved") or "text",
        )
        history = [
            {
                "role": "user",
                "content": "hello",
                "attachments": [{"name": "notes.txt"}],
                "timestamp": 123,
            },
            {"role": "assistant", "content": "hi"},
        ]

        sanitized = _sanitize_messages_for_api(
            history,
            cfg={"agent": {"image_input_mode": "text"}},
        )

        assert calls == []
        assert sanitized == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    def test_text_image_mode_strips_historical_image_url_parts(
        self,
        monkeypatch,
    ):
        """#2297: text-only providers must not replay old native image parts."""
        history = [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'what is in this image?'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAA='}},
                ],
                'attachments': [{'name': 'photo.png'}],
                'timestamp': 123,
            },
            {'role': 'assistant', 'content': 'It is a chart.'},
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'and this image?'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,BBB='}},
                ],
            },
        ]
        cfg = {'agent': {'image_input_mode': 'text'}}
        original = copy.deepcopy(history)
        calls = []
        monkeypatch.setattr(
            "api.streaming._resolve_image_input_mode",
            lambda *_args, **_kwargs: calls.append("resolved") or "text",
        )

        sanitized = _sanitize_messages_for_api(history, cfg=cfg)

        assert calls == ["resolved"]
        assert history == original
        assert sanitized[0] == {'role': 'user', 'content': 'what is in this image?'}
        assert 'image_url' not in str(sanitized)
        assert 'attachments' not in sanitized[0]
        assert sanitized[1] == {'role': 'assistant', 'content': 'It is a chart.'}
        assert sanitized[2] == {'role': 'user', 'content': 'and this image?'}

    def test_native_image_mode_keeps_historical_image_url_parts(
        self,
        monkeypatch,
    ):
        """Vision-capable/native mode keeps existing multimodal history intact."""
        content = [
            {'type': 'text', 'text': 'describe'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAA='}},
        ]
        cfg = {'agent': {'image_input_mode': 'native'}}
        monkeypatch.setattr(
            "api.streaming._resolve_image_input_mode",
            lambda *_args, **_kwargs: "native",
        )

        sanitized = _sanitize_messages_for_api([{'role': 'user', 'content': content}], cfg=cfg)

        assert sanitized == [{'role': 'user', 'content': content}]

    def test_sync_chat_history_sanitizer_receives_config(self):
        """#2398: fallback POST /api/chat must use the text-mode history sanitizer too."""
        src = Path('api/routes.py').read_text()
        call = 'conversation_history=_sanitize_messages_for_api(_previous_context_messages, cfg=get_config())'
        assert call in src, (
            'The legacy synchronous /api/chat endpoint must pass current config into '
            '_sanitize_messages_for_api so historical image_url parts are stripped '
            'for text-mode providers just like the streaming endpoint.'
        )

    def test_fake_png_rejected_by_magic_bytes(self):
        """A file named .png that is not actually an image must be rejected."""
        with TemporaryDirectory() as d:
            root = Path(d)
            fake = root / 'not-really.png'
            fake.write_text('this is plain text, not an image')
            atts = _normalize_chat_attachments([{
                'name': 'not-really.png', 'path': str(fake),
                'mime': 'image/png', 'size': fake.stat().st_size, 'is_image': True,
            }])
            result = _build_native_multimodal_message('', 'hi', atts, str(root), session_id='test-session')
            assert isinstance(result, str)


# ── _is_valid_image magic-byte checks ────────────────────────────────────────

from api.streaming import _is_valid_image


class TestIsValidImage:
    def test_valid_png(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'a.png'
            _make_png(p)
            assert _is_valid_image(p, 'image/png')

    def test_valid_jpeg(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'a.jpg'
            _make_jpeg(p)
            assert _is_valid_image(p, 'image/jpeg')

    def test_fake_png_rejected(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'fake.png'
            p.write_text('hello world')
            assert not _is_valid_image(p, 'image/png')

    def test_text_file_not_image(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'notes.txt'
            p.write_text('plain text')
            assert not _is_valid_image(p, 'image/png')
            assert not _is_valid_image(p, 'text/plain')

    def test_svg_allowed(self):
        """SVG is text-based with no binary magic, so it passes."""
        with TemporaryDirectory() as d:
            p = Path(d) / 'diagram.svg'
            p.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
            assert _is_valid_image(p, 'image/svg+xml')

    def test_missing_file(self):
        assert not _is_valid_image(Path('/no/such/file.png'), 'image/png')

    def test_mime_with_charset(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'a.png'
            _make_png(p)
            assert _is_valid_image(p, 'image/png; charset=utf-8')


class TestAttachmentRootIntegration:
    """Stage-361 regression: #2319 moved chat uploads to ~/.hermes/webui/attachments/<sid>/.

    Pre-fix, _build_native_multimodal_message required uploads to be under
    workspace_root, which silently rejected every image upload from the new
    attachment inbox (vision models silently lost image context).

    The fix adds the configured attachment root as a second allowed location
    via _attachment_root() from api.upload, single source of truth.
    """

    def test_attachment_root_path_allowed_for_native_multimodal(self, tmp_path, monkeypatch):
        """Image landing in the attachment inbox is accepted, not silently dropped."""
        # Set up isolated attachment root
        attachment_root = tmp_path / "attachments"
        attachment_root.mkdir(parents=True)
        monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))

        # The image lives in the attachment inbox, NOT in the workspace
        session_inbox = attachment_root / "sess123"
        session_inbox.mkdir()
        image_path = session_inbox / "photo.png"
        _make_png(image_path)

        # Workspace is a different, unrelated directory
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        atts = _normalize_chat_attachments([{
            "name": "photo.png",
            "ref": image_path.name,
            "mime": "image/png",
            "size": image_path.stat().st_size,
            "is_image": True,
        }])
        result = _build_native_multimodal_message(
            "", "describe this", atts, str(workspace), session_id="sess123"
        )

        # PRE-FIX: result would be a plain string (image silently rejected).
        # POST-FIX: result is a list with the image_url part included.
        assert isinstance(result, list), (
            "Stage-361 regression: image in attachment inbox was silently rejected. "
            "Expected list with image_url part, got string fallback. "
            "The pre-fix workspace_root-only guard at api/streaming.py needs to also "
            "allow paths under _attachment_root() (api/upload.py)."
        )
        assert result[1]["type"] == "image_url"
        assert result[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_other_session_attachment_rejected_for_native_multimodal(self, tmp_path, monkeypatch):
        attachment_root = tmp_path / "attachments"
        other_inbox = attachment_root / "other-session"
        other_inbox.mkdir(parents=True)
        monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
        image_path = other_inbox / "private.png"
        _make_png(image_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        atts = _normalize_chat_attachments([{
            "name": image_path.name,
            "path": str(image_path),
            "mime": "image/png",
            "size": image_path.stat().st_size,
            "is_image": True,
        }])
        result = _build_native_multimodal_message(
            "", "describe", atts, str(workspace), session_id="current-session"
        )
        assert isinstance(result, str)

    def test_path_outside_both_workspace_and_attachment_root_still_rejected(self, tmp_path, monkeypatch):
        """Paths outside BOTH allowed roots remain rejected — no security regression."""
        attachment_root = tmp_path / "attachments"
        attachment_root.mkdir()
        monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Image in /tmp or wherever — neither under workspace nor attachment root
        rogue_dir = tmp_path / "rogue"
        rogue_dir.mkdir()
        rogue_image = rogue_dir / "bad.png"
        _make_png(rogue_image)

        atts = _normalize_chat_attachments([{
            "name": "bad.png",
            "path": str(rogue_image),
            "mime": "image/png",
            "size": rogue_image.stat().st_size,
            "is_image": True,
        }])
        result = _build_native_multimodal_message(
            "", "hi", atts, str(workspace), session_id="sess123"
        )

        # Should fall back to string — rogue path rejected by both root checks
        assert isinstance(result, str), (
            "Security regression: path outside both workspace and attachment "
            "root was accepted. The _allowed_roots check should reject."
        )

    def test_workspace_absolute_path_is_not_a_chat_attachment(self, tmp_path, monkeypatch):
        """Workspace preview paths are handled by a separate endpoint, not chat."""
        attachment_root = tmp_path / "attachments"
        attachment_root.mkdir()
        monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        image_path = workspace / "ws-image.png"
        _make_png(image_path)

        atts = _normalize_chat_attachments([{
            "name": "ws-image.png",
            "path": str(image_path),
            "mime": "image/png",
            "size": image_path.stat().st_size,
            "is_image": True,
        }])
        result = _build_native_multimodal_message(
            "", "describe", atts, str(workspace), session_id="sess123"
        )

        assert isinstance(result, str)
