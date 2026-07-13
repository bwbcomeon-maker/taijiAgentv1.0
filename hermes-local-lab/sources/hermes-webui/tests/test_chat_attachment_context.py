"""Regression coverage for chat attachment context ingestion.

The browser upload endpoint stores files successfully, but chat turns must not
leave non-image files as opaque absolute paths. The agent needs bounded
content context for documents and explicit image routing status.
"""
from pathlib import Path
import json
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
