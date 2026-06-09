"""Regression coverage for chat attachment context ingestion.

The browser upload endpoint stores files successfully, but chat turns must not
leave non-image files as opaque absolute paths. The agent needs bounded
content context for documents and explicit image routing status.
"""
from pathlib import Path
import zipfile


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
