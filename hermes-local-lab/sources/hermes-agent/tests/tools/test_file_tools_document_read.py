import json
import zipfile
from pathlib import Path


def _write_zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def _read(path: Path, *, offset: int = 1, limit: int = 20) -> dict:
    from tools.file_tools import read_file_tool

    return json.loads(
        read_file_tool(str(path), offset=offset, limit=limit, task_id=f"doc-{path.name}")
    )


def test_read_file_extracts_docx_with_paragraph_sources(tmp_path):
    docx = tmp_path / "company.docx"
    _write_zip(
        docx,
        {
            "word/document.xml": """
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>营业执照已提供</w:t></w:r></w:p>
                <w:p><w:r><w:t>保密承诺函缺失</w:t></w:r></w:p>
              </w:body>
            </w:document>
            """,
        },
    )

    result = _read(docx)

    assert "error" not in result
    assert result["document_type"] == "docx"
    assert result["total_lines"] == 2
    assert "Paragraph 1" in result["content"]
    assert "营业执照已提供" in result["content"]
    assert "Paragraph 2" in result["content"]
    assert "保密承诺函缺失" in result["content"]


def test_read_file_extracts_xlsx_with_sheet_and_row_sources(tmp_path):
    xlsx = tmp_path / "cases.xlsx"
    _write_zip(
        xlsx,
        {
            "xl/sharedStrings.xml": """
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>项目名称</t></si>
              <si><t>验收状态</t></si>
              <si><t>A 项目</t></si>
              <si><t>已验收</t></si>
            </sst>
            """,
            "xl/worksheets/sheet1.xml": """
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1"><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>
                <row r="2"><c t="s"><v>2</v></c><c t="s"><v>3</v></c></row>
              </sheetData>
            </worksheet>
            """,
        },
    )

    result = _read(xlsx)

    assert "error" not in result
    assert result["document_type"] == "xlsx"
    assert "Sheet sheet1 Row 1" in result["content"]
    assert "项目名称 | 验收状态" in result["content"]
    assert "Sheet sheet1 Row 2" in result["content"]
    assert "A 项目 | 已验收" in result["content"]


def test_read_file_extracts_pptx_with_slide_sources(tmp_path):
    pptx = tmp_path / "briefing.pptx"
    _write_zip(
        pptx,
        {
            "ppt/slides/slide1.xml": """
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                   xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>项目预审结论</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
            </p:sld>
            """,
            "ppt/slides/slide2.xml": """
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                   xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>废标风险</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
            </p:sld>
            """,
        },
    )

    result = _read(pptx)

    assert "error" not in result
    assert result["document_type"] == "pptx"
    assert "Slide 1" in result["content"]
    assert "项目预审结论" in result["content"]
    assert "Slide 2" in result["content"]
    assert "废标风险" in result["content"]


def test_read_file_extracts_pdf_with_page_sources_and_pagination(tmp_path):
    pdf = tmp_path / "requirements.pdf"
    pdf.write_bytes("%PDF-1.4\nBT\n(工期 180 个自然日) Tj\nET\n%%EOF\n".encode("utf-8"))

    first = _read(pdf, offset=1, limit=1)
    second = _read(pdf, offset=2, limit=1)

    assert "error" not in first
    assert first["document_type"] == "pdf"
    assert "Page 1" in first["content"]
    assert "工期 180 个自然日" in first["content"]
    assert first["truncated"] is False
    assert second["content"] == ""


def test_read_file_rejects_document_output_over_max_chars(tmp_path, monkeypatch):
    from tools import file_tools

    docx = tmp_path / "large.docx"
    _write_zip(
        docx,
        {
            "word/document.xml": """
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body><w:p><w:r><w:t>这是一个超过限制的长段落内容</w:t></w:r></w:p></w:body>
            </w:document>
            """,
        },
    )
    monkeypatch.setattr(file_tools, "_max_read_chars_cached", 20)

    result = _read(docx)

    assert "error" in result
    assert "exceeds the safety limit" in result["error"]


def test_read_file_reports_bad_document_as_document_error(tmp_path):
    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"not a zip")

    result = _read(bad)

    assert result["status"] == "error"
    assert "document" in result["error"].lower()
