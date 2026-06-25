#!/usr/bin/env python3
"""Read-only document text extraction for Agent file reads.

This module intentionally avoids shelling out. It is used from ``read_file`` so
strict security profiles can still inspect tender/bid documents with source
locations.
"""

from __future__ import annotations

import html
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


SUPPORTED_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx"}


@dataclass(frozen=True)
class DocumentExtraction:
    document_type: str
    lines: list[str]


def is_supported_document(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS


def extract_document(path: str | Path) -> DocumentExtraction:
    resolved = Path(path)
    ext = resolved.suffix.lower()
    if ext == ".pdf":
        return DocumentExtraction("pdf", _extract_pdf(resolved))
    if ext == ".docx":
        return DocumentExtraction("docx", _extract_docx(resolved))
    if ext == ".xlsx":
        return DocumentExtraction("xlsx", _extract_xlsx(resolved))
    if ext == ".pptx":
        return DocumentExtraction("pptx", _extract_pptx(resolved))
    raise ValueError(f"Unsupported document type: {ext}")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_nodes(root: ET.Element, local_names: set[str] | None = None) -> list[str]:
    names = local_names or {"t"}
    parts: list[str] = []
    for node in root.iter():
        if _local_name(node.tag) in names and node.text:
            text = node.text.strip()
            if text:
                parts.append(text)
    return parts


def _normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _extract_docx(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
    except Exception as exc:
        raise ValueError(f"document extraction failed for docx: {exc}") from exc

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"document XML parse failed for docx: {exc}") from exc

    lines: list[str] = []
    paragraph_index = 0
    for node in root.iter():
        if _local_name(node.tag) != "p":
            continue
        text = _normalize_ws(" ".join(_text_nodes(node, {"t"})))
        if not text:
            continue
        paragraph_index += 1
        lines.append(f"Paragraph {paragraph_index} | {text}")
    return lines


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        xml = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(xml)
    values: list[str] = []
    for si in root:
        if _local_name(si.tag) != "si":
            continue
        values.append(_normalize_ws(" ".join(_text_nodes(si, {"t"}))))
    return values


def _xlsx_sheet_names(zf: zipfile.ZipFile) -> dict[str, str]:
    try:
        workbook_xml = zf.read("xl/workbook.xml")
        rels_xml = zf.read("xl/_rels/workbook.xml.rels")
    except KeyError:
        return {}

    try:
        rels_root = ET.fromstring(rels_xml)
        rel_targets = {
            rel.attrib.get("Id"): rel.attrib.get("Target", "")
            for rel in rels_root
            if _local_name(rel.tag) == "Relationship"
        }
        workbook_root = ET.fromstring(workbook_xml)
    except ET.ParseError:
        return {}

    names: dict[str, str] = {}
    for sheet in workbook_root.iter():
        if _local_name(sheet.tag) != "sheet":
            continue
        name = sheet.attrib.get("name")
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rel_targets.get(rel_id, "")
        if name and target:
            target_path = target.lstrip("/")
            if not target_path.startswith("xl/"):
                target_path = f"xl/{target_path}"
            names[target_path] = name
    return names


def _xlsx_cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return _normalize_ws(" ".join(_text_nodes(cell, {"t"})))
    value_node = next((node for node in cell if _local_name(node.tag) == "v"), None)
    value = (value_node.text or "").strip() if value_node is not None else ""
    if cell_type == "s":
        try:
            return shared[int(value)]
        except Exception:
            return value
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def _extract_xlsx(path: Path) -> list[str]:
    lines: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            shared = _shared_strings(zf)
            sheet_names = _xlsx_sheet_names(zf)
            sheet_paths = sorted(
                (name for name in zf.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
                key=lambda name: int(re.search(r"sheet(\d+)\.xml$", name).group(1)),
            )
            for sheet_path in sheet_paths:
                sheet_name = sheet_names.get(sheet_path, Path(sheet_path).stem)
                root = ET.fromstring(zf.read(sheet_path))
                for row in root.iter():
                    if _local_name(row.tag) != "row":
                        continue
                    row_number = row.attrib.get("r", "")
                    values = [_xlsx_cell_value(cell, shared) for cell in row if _local_name(cell.tag) == "c"]
                    values = [value for value in values if value != ""]
                    if not values:
                        continue
                    source = f"Sheet {sheet_name} Row {row_number or len(lines) + 1}"
                    lines.append(f"{source} | {' | '.join(values)}")
    except Exception as exc:
        raise ValueError(f"document extraction failed for xlsx: {exc}") from exc
    return lines


def _extract_pptx(path: Path) -> list[str]:
    lines: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            slide_paths = sorted(
                (name for name in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                key=lambda name: int(re.search(r"slide(\d+)\.xml$", name).group(1)),
            )
            for slide_path in slide_paths:
                slide_number = int(re.search(r"slide(\d+)\.xml$", slide_path).group(1))
                root = ET.fromstring(zf.read(slide_path))
                text = _normalize_ws(" ".join(_text_nodes(root, {"t"})))
                if text:
                    lines.append(f"Slide {slide_number} | {text}")
    except Exception as exc:
        raise ValueError(f"document extraction failed for pptx: {exc}") from exc
    return lines


def _extract_pdf(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        lines = []
        for page_index, page in enumerate(reader.pages, start=1):
            text = _normalize_ws(page.extract_text() or "")
            if text:
                lines.append(f"Page {page_index} | {text}")
        if lines:
            return lines
    except Exception:
        pass
    return _extract_pdf_literals(path)


def _pdf_unescape_literal(value: bytes) -> str:
    value = re.sub(rb"\\([nrtbf()\\])", lambda m: {
        b"n": b"\n",
        b"r": b"\r",
        b"t": b"\t",
        b"b": b"\b",
        b"f": b"\f",
        b"(": b"(",
        b")": b")",
        b"\\": b"\\",
    }[m.group(1)], value)
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1", errors="replace")


def _extract_pdf_literals(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except Exception as exc:
        raise ValueError(f"document extraction failed for pdf: {exc}") from exc

    literals = [_normalize_ws(_pdf_unescape_literal(match)) for match in re.findall(rb"\((.*?)\)\s*Tj", data, flags=re.S)]
    literals.extend(
        _normalize_ws(_pdf_unescape_literal(part))
        for array in re.findall(rb"\[(.*?)\]\s*TJ", data, flags=re.S)
        for part in re.findall(rb"\((.*?)\)", array, flags=re.S)
    )
    text = _normalize_ws(" ".join(part for part in literals if part))
    if text:
        return [f"Page 1 | {html.unescape(text)}"]
    return []
