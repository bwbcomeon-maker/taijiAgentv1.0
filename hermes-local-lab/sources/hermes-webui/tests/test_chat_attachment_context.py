"""Regression coverage for chat attachment context ingestion.

The browser upload endpoint stores files successfully, but chat turns must not
leave non-image files as opaque absolute paths. The agent needs bounded
content context for documents and explicit image routing status.
"""
from pathlib import Path
from collections import OrderedDict
import json
import queue
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _make_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)


def _make_png(path: Path) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
        b"\xdc\xccY\xe7"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_document_attachments_are_extracted_into_agent_context(tmp_path, monkeypatch):
    from api.attachment_context import build_attachment_context

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))

    txt = session_dir / "说明.txt"
    txt.write_text("这是纯文本附件内容。", encoding="utf-8")

    pptx = session_dir / "国网模板.pptx"
    _make_zip(
        pptx,
        {
            "ppt/slides/slide1.xml": (
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                "<a:t>国网模板首页</a:t><a:t>项目背景与目标</a:t></p:sld>"
            )
        },
    )

    docx = session_dir / "公文手册.docx"
    _make_zip(
        docx,
        {
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:t>公文写作规范</w:t><w:t>标题、正文、落款</w:t></w:document>"
            )
        },
    )

    xlsx = session_dir / "清单.xlsx"
    _make_zip(
        xlsx,
        {
            "xl/sharedStrings.xml": (
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<si><t>事项</t></si><si><t>状态</t></si><si><t>测试附件</t></si><si><t>通过</t></si></sst>"
            ),
            "xl/worksheets/sheet1.xml": (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row r="1"><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
                '<row r="2"><c t="s"><v>2</v></c><c t="s"><v>3</v></c></row></sheetData></worksheet>'
            ),
        },
    )

    pdf = session_dir / "工具手册.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj <<>> stream\nBT (PDF manual content) Tj ET\nendstream\n%%EOF")

    result = build_attachment_context(
        [
            {"name": txt.name, "path": str(txt), "mime": "text/plain", "is_image": False},
            {"name": pptx.name, "path": str(pptx), "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "is_image": False},
            {"name": docx.name, "path": str(docx), "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "is_image": False},
            {"name": xlsx.name, "path": str(xlsx), "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "is_image": False},
            {"name": pdf.name, "path": str(pdf), "mime": "application/pdf", "is_image": False},
        ],
        workspace=str(tmp_path / "workspace"),
        cfg={},
        image_mode="native",
    )

    context = result.text_context
    assert "这是纯文本附件内容" in context
    assert "Slide 1" in context and "国网模板首页" in context and "项目背景与目标" in context
    assert "公文写作规范" in context and "标题、正文、落款" in context
    assert "Sheet sheet1" in context and "测试附件" in context and "通过" in context
    assert "PDF manual content" in context
    assert str(session_dir) not in context


def test_image_text_mode_without_vision_gets_clear_notice(tmp_path, monkeypatch):
    from api.attachment_context import build_attachment_context

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    image = session_dir / "截图.png"
    _make_png(image)

    result = build_attachment_context(
        [{"name": image.name, "path": str(image), "mime": "image/png", "is_image": True}],
        workspace=str(tmp_path / "workspace"),
        cfg={"agent": {"image_input_mode": "text"}},
        image_mode="text",
        vision_available=False,
    )

    assert result.image_paths == [str(image.resolve())]
    assert "当前模型未配置视觉理解能力" in result.text_context
    assert "截图.png" in result.text_context
    assert str(session_dir) not in result.text_context


def test_native_image_mode_keeps_image_for_multimodal_without_text_notice(tmp_path, monkeypatch):
    from api.attachment_context import build_attachment_context

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    image = session_dir / "photo.png"
    _make_png(image)

    result = build_attachment_context(
        [{"name": image.name, "path": str(image), "mime": "image/png", "is_image": True}],
        workspace=str(tmp_path / "workspace"),
        cfg={"agent": {"image_input_mode": "native"}},
        image_mode="native",
    )

    assert result.image_paths == [str(image.resolve())]
    assert "未配置视觉理解能力" not in result.text_context


def test_webui_image_auto_mode_respects_supports_vision_false():
    from api.streaming import _resolve_image_input_mode

    cfg = {
        "agent": {"image_input_mode": "auto"},
        "model": {"provider": "deepseek", "default": "deepseek-v4-pro", "supports_vision": False},
    }

    assert _resolve_image_input_mode(cfg) == "text"


def _text_vision_config() -> dict:
    return {
        "agent": {"image_input_mode": "auto"},
        "model": {
            "provider": "deepseek",
            "default": "deepseek-chat",
            "supports_vision": False,
        },
        "auxiliary": {
            "vision": {
                "provider": "alibaba",
                "model": "qwen3-vl-plus",
            }
        },
    }


def test_prepare_webui_chat_input_uses_auxiliary_vision_once_per_image_in_order(
    tmp_path, monkeypatch
):
    from api.streaming import prepare_webui_chat_input
    from tools import vision_tools

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    images = [session_dir / "first.png", session_dir / "second.png"]
    for image in images:
        _make_png(image)

    calls = []

    async def fake_vision_analyze_tool(*, image_url, user_prompt, model=None):
        calls.append((image_url, user_prompt, model))
        return json.dumps({"success": True, "analysis": f"analysis-{len(calls)}"})

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", fake_vision_analyze_tool)

    result = prepare_webui_chat_input(
        "compare them",
        [
            {"name": image.name, "path": str(image), "mime": "image/png", "is_image": True}
            for image in images
        ],
        workspace=str(tmp_path / "workspace"),
        cfg=_text_vision_config(),
        provider="deepseek",
        model="deepseek-chat",
    )

    assert isinstance(result, str)
    assert [Path(call[0]).name for call in calls] == ["first.png", "second.png"]
    assert all(call[2] is None for call in calls)
    assert all("[敏感信息已隐藏]" in call[1] for call in calls)
    assert result.index("analysis-1") < result.index("analysis-2")
    assert "compare them" in result
    assert str(session_dir) not in result
    assert "image_url" not in result
    assert "base64" not in result


@pytest.mark.parametrize(
    "vision_result",
    [
        json.dumps({"success": False, "analysis": "provider leaked /private/path"}),
        json.dumps({"success": True, "analysis": ""}),
    ],
)
def test_prepare_webui_chat_input_blocks_failed_or_empty_vision_result(
    tmp_path, monkeypatch, vision_result
):
    from api.streaming import WebUIChatInputError, prepare_webui_chat_input
    from tools import vision_tools

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    image = session_dir / "photo.png"
    _make_png(image)

    async def fake_vision_analyze_tool(**_kwargs):
        return vision_result

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", fake_vision_analyze_tool)

    with pytest.raises(WebUIChatInputError) as raised:
        prepare_webui_chat_input(
            "describe",
            [{"name": image.name, "path": str(image), "mime": "image/png", "is_image": True}],
            workspace=str(tmp_path / "workspace"),
            cfg=_text_vision_config(),
            provider="deepseek",
            model="deepseek-chat",
        )

    assert raised.value.payload["type"] == "vision_analysis_error"
    assert str(image) not in json.dumps(raised.value.payload, ensure_ascii=False)
    assert "/private/path" not in json.dumps(raised.value.payload, ensure_ascii=False)


def test_prepare_webui_chat_input_blocks_vision_exception_and_stops_later_images(
    tmp_path, monkeypatch
):
    from api.streaming import WebUIChatInputError, prepare_webui_chat_input
    from tools import vision_tools

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    images = [session_dir / "first.png", session_dir / "second.png"]
    for image in images:
        _make_png(image)
    calls = []

    async def failing_vision(**kwargs):
        calls.append(kwargs["image_url"])
        raise RuntimeError("secret provider error /private/path")

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", failing_vision)

    with pytest.raises(WebUIChatInputError) as raised:
        prepare_webui_chat_input(
            "compare",
            [
                {"name": image.name, "path": str(image), "mime": "image/png", "is_image": True}
                for image in images
            ],
            workspace=str(tmp_path / "workspace"),
            cfg=_text_vision_config(),
            provider="deepseek",
            model="deepseek-chat",
        )

    assert raised.value.payload["type"] == "vision_analysis_error"
    assert len(calls) == 1
    assert "secret provider error" not in json.dumps(raised.value.payload, ensure_ascii=False)


def test_prepare_webui_chat_input_requires_auxiliary_vision_for_text_mode(
    tmp_path, monkeypatch
):
    from api.streaming import WebUIChatInputError, prepare_webui_chat_input

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    image = session_dir / "photo.png"
    _make_png(image)

    with pytest.raises(WebUIChatInputError) as raised:
        prepare_webui_chat_input(
            "describe",
            [{"name": image.name, "path": str(image), "mime": "image/png", "is_image": True}],
            workspace=str(tmp_path / "workspace"),
            cfg={"agent": {"image_input_mode": "text"}},
            provider="deepseek",
            model="deepseek-chat",
        )

    assert raised.value.payload["type"] == "vision_configuration_error"


def test_prepare_webui_chat_input_preserves_document_and_vision_context(
    tmp_path, monkeypatch
):
    from api.streaming import prepare_webui_chat_input
    from tools import vision_tools

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    image = session_dir / "photo.png"
    doc = session_dir / "notes.txt"
    _make_png(image)
    doc.write_text("document-body", encoding="utf-8")

    async def fake_vision(**_kwargs):
        return json.dumps({"success": True, "analysis": "visual-description"})

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", fake_vision)
    result = prepare_webui_chat_input(
        "summarize",
        [
            {"name": image.name, "path": str(image), "mime": "image/png", "is_image": True},
            {"name": doc.name, "path": str(doc), "mime": "text/plain", "is_image": False},
        ],
        workspace=str(tmp_path / "workspace"),
        cfg=_text_vision_config(),
        provider="deepseek",
        model="deepseek-chat",
    )

    assert "document-body" in result
    assert "visual-description" in result
    assert "summarize" in result


def test_prepare_webui_chat_input_keeps_native_images_as_multimodal_parts(
    tmp_path, monkeypatch
):
    from api.streaming import prepare_webui_chat_input

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    images = [session_dir / "first.png", session_dir / "second.png"]
    for image in images:
        _make_png(image)

    result = prepare_webui_chat_input(
        "compare",
        [
            {"name": image.name, "path": str(image), "mime": "image/png", "is_image": True}
            for image in images
        ],
        workspace=str(tmp_path / "workspace"),
        cfg={"agent": {"image_input_mode": "native"}},
        provider="openai",
        model="gpt-4o",
    )

    assert result[0] == {"type": "text", "text": "compare"}
    assert [part["type"] for part in result[1:]] == ["image_url", "image_url"]


def test_legacy_cancellation_after_first_auxiliary_image_skips_second_and_main_model(
    tmp_path, monkeypatch
):
    import api.config as config
    import api.models as models
    import api.oauth as oauth
    import api.profiles as profiles
    import api.streaming as streaming
    from tools import vision_tools

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setattr(config, "SESSION_DIR", session_dir)
    monkeypatch.setattr(streaming, "SESSION_DIR", session_dir)
    attachment_root = tmp_path / "attachments"
    uploaded_dir = attachment_root / "session-a"
    uploaded_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    images = [uploaded_dir / "first.png", uploaded_dir / "second.png"]
    for image in images:
        _make_png(image)
    cfg = _text_vision_config()
    stream_id = "stream-legacy-cancel-after-vision"
    run_calls = []
    vision_calls = []

    class FakeAgent:
        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id")
            self.context_compressor = None
            self.ephemeral_system_prompt = None
            self.session_prompt_tokens = 0
            self.session_completion_tokens = 0
            self.session_estimated_cost_usd = 0
            self._last_error = None

        def run_conversation(self, **kwargs):
            run_calls.append(kwargs)
            return {"messages": [{"role": "assistant", "content": "must not run"}]}

        def interrupt(self, _message):
            return None

    async def vision_then_cancel(**kwargs):
        vision_calls.append(kwargs["image_url"])
        streaming.CANCEL_FLAGS[stream_id].set()
        return json.dumps({"success": True, "analysis": "vision finished"})

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", vision_then_cancel)
    monkeypatch.setattr(streaming, "_get_ai_agent", lambda: FakeAgent)
    monkeypatch.setattr(
        streaming,
        "resolve_model_provider",
        lambda *_args, **_kwargs: ("deepseek-chat", "deepseek", None),
    )
    monkeypatch.setattr(config, "get_config", lambda: cfg)
    monkeypatch.setattr(config, "_resolve_cli_toolsets", lambda _cfg: [])
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda _cfg: {
        "status": "not_configured", "source": "none", "label": "", "message_count": 0, "messages": [],
    })
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda _ctx, _cfg: [])
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda _profile: tmp_path)
    monkeypatch.setattr(profiles, "get_profile_runtime_env", lambda _home: {})
    monkeypatch.setattr(profiles, "patch_skill_home_modules", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        oauth,
        "resolve_runtime_provider_with_anthropic_env_lock",
        lambda *_args, **_kwargs: {
            "provider": "deepseek", "api_key": "synthetic-key", "base_url": None,
        },
    )

    session = models.Session(
        session_id="legacy_cancel_session",
        title="Cancel",
        workspace=str(tmp_path),
        model="deepseek-chat",
        messages=[],
    )
    session.active_stream_id = stream_id
    session.pending_user_message = "describe"
    session.pending_started_at = 1.0
    session.save(touch_updated_at=False)
    models.SESSIONS[session.session_id] = session
    event_queue = queue.Queue()
    streaming.STREAMS[stream_id] = event_queue

    streaming._run_agent_streaming(
        session.session_id,
        "describe",
        "deepseek-chat",
        str(tmp_path),
        stream_id,
        [
            {"name": image.name, "path": str(image), "mime": "image/png", "is_image": True}
            for image in images
        ],
        model_provider="deepseek",
    )

    assert [Path(path).name for path in vision_calls] == ["first.png"]
    assert run_calls == []
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    terminal = [name for name, _data in events if name in {"cancel", "apperror", "error", "done"}]
    assert terminal == ["cancel"]
    assert not any(name == "token" for name, _data in events)


def test_legacy_vision_failure_survives_session_reload_with_user_turn_and_typed_error(
    tmp_path, monkeypatch
):
    import api.config as config
    import api.models as models
    import api.oauth as oauth
    import api.profiles as profiles
    import api.streaming as streaming
    from tools import vision_tools

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setattr(config, "SESSION_DIR", session_dir)
    monkeypatch.setattr(streaming, "SESSION_DIR", session_dir)
    attachment_root = tmp_path / "attachments"
    uploaded_dir = attachment_root / "session-a"
    uploaded_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    old_image = uploaded_dir / "old.png"
    image = uploaded_dir / "current.png"
    _make_png(old_image)
    _make_png(image)
    old_attachment = {"name": old_image.name, "path": str(old_image), "mime": "image/png", "is_image": True}
    attachment = {"name": image.name, "path": str(image), "mime": "image/png", "is_image": True}
    cfg = _text_vision_config()
    stream_id = "stream-legacy-vision-failure-reload"
    run_calls = []

    class FakeAgent:
        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id")
            self.context_compressor = None
            self.ephemeral_system_prompt = None
            self.session_prompt_tokens = 0
            self.session_completion_tokens = 0
            self.session_estimated_cost_usd = 0
            self._last_error = None

        def run_conversation(self, **kwargs):
            run_calls.append(kwargs)
            return {"messages": [{"role": "assistant", "content": "must not run"}]}

        def interrupt(self, _message):
            return None

    async def failed_vision(**_kwargs):
        return json.dumps({"success": False, "analysis": "secret /private/provider/path"})

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", failed_vision)
    monkeypatch.setattr(streaming, "_get_ai_agent", lambda: FakeAgent)
    monkeypatch.setattr(streaming, "resolve_model_provider", lambda *_args, **_kwargs: ("deepseek-chat", "deepseek", None))
    monkeypatch.setattr(config, "get_config", lambda: cfg)
    monkeypatch.setattr(config, "_resolve_cli_toolsets", lambda _cfg: [])
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda _cfg: {
        "status": "not_configured", "source": "none", "label": "", "message_count": 0, "messages": [],
    })
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda _ctx, _cfg: [])
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda _profile: tmp_path)
    monkeypatch.setattr(profiles, "get_profile_runtime_env", lambda _home: {})
    monkeypatch.setattr(profiles, "patch_skill_home_modules", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        oauth,
        "resolve_runtime_provider_with_anthropic_env_lock",
        lambda *_args, **_kwargs: {"provider": "deepseek", "api_key": "synthetic-key", "base_url": None},
    )

    session = models.Session(
        session_id="legacy_vision_failure_reload",
        title="Vision failure",
        workspace=str(tmp_path),
        model="deepseek-chat",
        messages=[
            {"role": "user", "content": "describe this image", "attachments": [old_attachment], "timestamp": 1},
            {"role": "assistant", "content": "old answer", "timestamp": 2},
        ],
    )
    session.active_stream_id = stream_id
    session.pending_user_message = "describe this image"
    session.pending_attachments = [attachment]
    session.pending_started_at = 1.0
    session.save(touch_updated_at=False)
    models.SESSIONS[session.session_id] = session
    event_queue = queue.Queue()
    streaming.STREAMS[stream_id] = event_queue

    streaming._run_agent_streaming(
        session.session_id,
        "describe this image",
        "deepseek-chat",
        str(tmp_path),
        stream_id,
        [attachment],
        model_provider="deepseek",
    )

    assert run_calls == []
    reloaded = models.Session.load(session.session_id)
    assert [message["role"] for message in reloaded.messages] == ["user", "assistant", "user", "assistant"]
    assert reloaded.messages[2]["content"] == "describe this image"
    assert reloaded.messages[2]["attachments"] == [attachment]
    assert reloaded.messages[3]["_error"] is True
    assert reloaded.messages[3]["error_type"] == "vision_analysis_error"
    serialized = json.dumps(reloaded.messages, ensure_ascii=False)
    assert "Response interrupted" not in serialized
    assert "/private/provider/path" not in serialized


def test_secret_like_attachment_is_not_extracted(tmp_path, monkeypatch):
    from api.attachment_context import build_attachment_context

    attachment_root = tmp_path / "attachments"
    session_dir = attachment_root / "session-a"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    env_file = session_dir / ".env"
    env_file.write_text("API_KEY=should-not-leak", encoding="utf-8")

    result = build_attachment_context(
        [{"name": env_file.name, "path": str(env_file), "mime": "text/plain", "is_image": False}],
        workspace=str(tmp_path / "workspace"),
        cfg={},
        image_mode="native",
    )

    assert "should-not-leak" not in result.text_context
    assert "出于安全保护未注入" in result.text_context


def test_frontend_does_not_send_absolute_paths_as_user_text():
    src = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")

    assert "I've uploaded" not in src
    assert "[Attached files:" not in src
    assert "uploadedPaths=uploaded.map" not in src
    assert "attachments:uploaded.length?uploaded:undefined" in src


def test_file_picker_accepts_powerpoint_and_rejects_env_shortcut():
    src = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert ".pptx" in src
    assert ".ppt" in src
    assert "application/vnd.openxmlformats-officedocument.presentationml.presentation" in src
    assert "application/vnd.ms-powerpoint" in src
    assert ".env" not in src
