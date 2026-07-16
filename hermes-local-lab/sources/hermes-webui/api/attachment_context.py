"""Build bounded agent-only context for uploaded chat attachments."""

from __future__ import annotations

from dataclasses import dataclass, field
import csv
import html
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

from api.config import MAX_UPLOAD_BYTES
from api.upload import _attachment_root, _session_attachment_dir


_MAX_FILES = int(os.getenv("HERMES_WEBUI_ATTACHMENT_CONTEXT_MAX_FILES", "20") or "20")
_MAX_FILE_BYTES = min(
    MAX_UPLOAD_BYTES,
    int(float(os.getenv("HERMES_WEBUI_ATTACHMENT_CONTEXT_MAX_FILE_MB", "20") or "20") * 1024 * 1024),
)
_MAX_CHARS_PER_FILE = int(os.getenv("HERMES_WEBUI_ATTACHMENT_CONTEXT_MAX_CHARS_PER_FILE", "12000") or "12000")
_MAX_TOTAL_CHARS = int(os.getenv("HERMES_WEBUI_ATTACHMENT_CONTEXT_MAX_TOTAL_CHARS", "50000") or "50000")

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".log"}
_SECRET_BASENAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".env.staging",
    ".envrc",
}
_SECRET_SUFFIXES = {".key"}
_IMAGE_MIME_PREFIX = "image/"
_PRIVATE_KEY_RE = re.compile(
    rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE,
)


@dataclass
class AttachmentContextResult:
    text_context: str = ""
    image_items: list[dict[str, str]] = field(default_factory=list)
    extracted_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)

    @property
    def image_paths(self) -> list[str]:
        return [item["path"] for item in self.image_items if item.get("path")]


def has_configured_vision(cfg: dict | None) -> bool:
    aux = (cfg or {}).get("auxiliary") or {}
    vision = aux.get("vision") or {}
    if not isinstance(vision, dict):
        return False
    provider = str(vision.get("provider") or "").strip().lower()
    model = str(vision.get("model") or "").strip()
    base_url = str(vision.get("base_url") or "").strip()
    return provider not in ("", "auto") or bool(model or base_url)


def build_attachment_context(
    attachments,
    *,
    workspace: str,
    session_id: str,
    cfg: dict | None = None,
    image_mode: str = "native",
    vision_available: bool | None = None,
) -> AttachmentContextResult:
    """Return bounded text context plus validated image paths for this turn.

    The context is meant for the agent request only. It intentionally avoids
    absolute local paths so assistant replies do not leak desktop internals.
    """
    result = AttachmentContextResult()
    if not attachments:
        return result

    if vision_available is None:
        vision_available = has_configured_vision(cfg)

    blocks: list[str] = []
    total_chars = 0
    for raw in list(attachments or [])[:_MAX_FILES]:
        if not isinstance(raw, dict):
            continue
        path = resolve_attachment_path(
            str(raw.get("ref") or ""),
            workspace=workspace,
            session_id=session_id,
        )
        name = _attachment_name(raw, path)
        if path is None:
            blocks.append(_format_skip(name, "附件路径不在可信上传目录或工作区内，未注入模型上下文。"))
            result.skipped_files.append(name)
            continue
        if not path.is_file():
            blocks.append(_format_skip(name, "附件文件不存在，未注入模型上下文。"))
            result.skipped_files.append(name)
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size <= 0:
            blocks.append(_format_skip(name, "附件为空，未注入模型上下文。"))
            result.skipped_files.append(name)
            continue
        if size > _MAX_FILE_BYTES:
            blocks.append(_format_skip(name, f"附件超过解析上限 {_MAX_FILE_BYTES // 1024 // 1024}MB，未注入模型上下文。"))
            result.skipped_files.append(name)
            continue

        mime = str(raw.get("mime") or "").strip() or (mimetypes.guess_type(path.name)[0] or "")
        is_image = bool(raw.get("is_image")) or mime.startswith(_IMAGE_MIME_PREFIX)
        if is_image:
            result.image_items.append({"name": name, "path": str(path), "mime": mime})
            if image_mode == "text" and not vision_available:
                blocks.append(
                    f"- {name}: 图片已上传，但当前模型未配置视觉理解能力。"
                    "请明确告知用户需要配置支持视觉的模型或辅助视觉模型后再分析图片。"
                )
            continue

        if _looks_secret_like(path):
            blocks.append(_format_skip(name, "疑似密钥、环境变量或私钥文件，出于安全保护未注入模型上下文。"))
            result.skipped_files.append(name)
            continue

        extracted = _extract_attachment_text(path, mime)
        if not extracted.strip():
            blocks.append(_format_skip(name, "当前格式未提取到可读文本，未注入模型上下文。"))
            result.skipped_files.append(name)
            continue

        extracted = _clamp_text(extracted, _MAX_CHARS_PER_FILE)
        remaining = _MAX_TOTAL_CHARS - total_chars
        if remaining <= 0:
            blocks.append(_format_skip(name, "本轮附件上下文已达到总长度上限，未继续注入。"))
            result.skipped_files.append(name)
            break
        if len(extracted) > remaining:
            extracted = _clamp_text(extracted, remaining)
        total_chars += len(extracted)
        result.extracted_files.append(name)
        blocks.append(f"- {name}:\n{_indent(extracted)}")

    if blocks:
        result.text_context = (
            "[Uploaded file context]\n"
            "The following uploaded attachments were pre-processed for this turn. "
            "Use this content when answering the user's question. Do not expose local filesystem paths.\n"
            + "\n".join(blocks)
        )
    return result


def resolve_attachment_path(
    raw_path: str,
    *,
    workspace: str,
    session_id: str,
) -> Path | None:
    """Resolve one opaque, single-file ref inside this session's upload inbox.

    Chat attachments are not workspace previews.  Absolute paths, path
    separators and traversal are rejected before touching the filesystem; the
    resolved target must also remain inside the current session directory so a
    symlink cannot escape it.
    """
    if (
        not raw_path
        or not session_id
        or raw_path in {".", ".."}
        or "/" in raw_path
        or "\\" in raw_path
        or Path(raw_path).is_absolute()
    ):
        return None
    try:
        session_root = _session_attachment_dir(str(session_id)).resolve()
        candidate = session_root / raw_path
        if candidate.is_symlink():
            return None
        path = candidate.resolve()
        return path if path.is_relative_to(session_root) else None
    except Exception:
        return None


def _attachment_name(raw: dict, path: Path | None) -> str:
    name = str(raw.get("name") or raw.get("filename") or "").strip()
    if name:
        return Path(name).name
    if path is not None:
        return path.name
    raw_path = str(raw.get("path") or raw.get("ref") or "").strip()
    return Path(raw_path).name if raw_path else "attachment"


def _looks_secret_like(path: Path) -> bool:
    if path.name in _SECRET_BASENAMES or path.suffix.lower() in _SECRET_SUFFIXES:
        return True
    try:
        head = path.read_bytes()[:65536]
    except Exception:
        return False
    return bool(_PRIVATE_KEY_RE.search(head))


def _extract_attachment_text(path: Path, mime: str) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pptx":
        return _extract_pptx(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".xlsx":
        return _extract_xlsx(path)
    if suffix == ".pdf" or mime == "application/pdf":
        return _extract_pdf(path)
    if suffix == ".csv":
        return _extract_csv(path)
    if suffix in _TEXT_SUFFIXES or mime.startswith("text/") or suffix in {".json", ".yaml", ".yml"}:
        return _read_text(path)
    return ""


def _read_text(path: Path) -> str:
    raw = path.read_bytes()[: min(path.stat().st_size, _MAX_FILE_BYTES)]
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_csv(path: Path) -> str:
    text = _read_text(path)
    rows = []
    for idx, row in enumerate(csv.reader(io.StringIO(text))):
        if idx >= 40:
            rows.append("...")
            break
        rows.append(" | ".join(cell.strip() for cell in row))
    return "\n".join(row for row in rows if row)


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        pages = []
        for idx, page in enumerate(reader.pages[:20], start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"Page {idx}: {text.strip()}")
        if pages:
            return "\n".join(pages)
    except Exception:
        pass

    raw = path.read_bytes()[: min(path.stat().st_size, _MAX_FILE_BYTES)]
    text = raw.decode("latin-1", errors="ignore")
    chunks = []
    for match in re.finditer(r"\((.*?)\)\s*T[Jj]", text, flags=re.DOTALL):
        chunks.append(_decode_pdf_literal(match.group(1)))
    if not chunks:
        for match in re.finditer(r"\(([^()]{3,300})\)", text):
            chunks.append(_decode_pdf_literal(match.group(1)))
            if len(chunks) >= 80:
                break
    return "\n".join(chunk for chunk in chunks if chunk.strip())


def _decode_pdf_literal(value: str) -> str:
    value = value.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
    value = value.replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\t", "\t")
    return html.unescape(value)


def _extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        data = zf.read("word/document.xml")
    return _xml_text(data, tags={"t"})


def _extract_pptx(path: Path) -> str:
    lines = []
    with zipfile.ZipFile(path) as zf:
        slide_names = sorted(
            (name for name in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)),
            key=lambda value: int(re.search(r"slide(\d+)\.xml$", value).group(1)),
        )
        for idx, name in enumerate(slide_names[:40], start=1):
            text = _xml_text(zf.read(name), tags={"t"})
            if text:
                lines.append(f"Slide {idx}: {text}")
    return "\n".join(lines)


def _extract_xlsx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        shared = _xlsx_shared_strings(zf)
        sheet_names = sorted(
            (name for name in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", name)),
            key=lambda value: int(re.search(r"sheet(\d+)\.xml$", value).group(1)),
        )
        lines = []
        for sheet in sheet_names[:10]:
            rows = _xlsx_sheet_rows(zf.read(sheet), shared)
            if rows:
                label = Path(sheet).stem
                rendered = "\n".join(" | ".join(row) for row in rows[:30] if row)
                lines.append(f"Sheet {label}:\n{rendered}")
    return "\n".join(lines)


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    values = []
    for si in root:
        parts = []
        for node in si.iter():
            if _local_name(node.tag) == "t" and node.text:
                parts.append(node.text)
        values.append("".join(parts))
    return values


def _xlsx_sheet_rows(data: bytes, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(data)
    rows = []
    for row_node in root.iter():
        if _local_name(row_node.tag) != "row":
            continue
        row = []
        for cell in row_node:
            if _local_name(cell.tag) != "c":
                continue
            cell_type = cell.attrib.get("t", "")
            value = ""
            for child in cell.iter():
                lname = _local_name(child.tag)
                if lname == "v" and child.text is not None:
                    value = child.text
                    break
                if lname == "t" and child.text is not None:
                    value += child.text
            if cell_type == "s":
                try:
                    value = shared[int(value)]
                except Exception:
                    pass
            row.append(value.strip())
        if any(row):
            rows.append(row)
    return rows


def _xml_text(data: bytes, *, tags: set[str]) -> str:
    root = ET.fromstring(data)
    parts = []
    for node in root.iter():
        if _local_name(node.tag) in tags and node.text:
            text = node.text.strip()
            if text:
                parts.append(text)
    return " ".join(parts)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _format_skip(name: str, reason: str) -> str:
    return f"- {name}: {reason}"


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" if line else "" for line in text.splitlines())


def _clamp_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 80)].rstrip() + "\n...[truncated attachment content]"


def diagnostic_summary() -> dict:
    try:
        import pypdf  # type: ignore  # noqa: F401

        pypdf_available = True
    except Exception:
        pypdf_available = False
    return {
        "supported_documents": ["txt", "md", "csv", "json", "yaml", "pdf", "docx", "pptx", "xlsx"],
        "supported_images": ["png", "jpg", "jpeg", "gif", "webp", "svg"],
        "pypdf_available": pypdf_available,
        "max_files": _MAX_FILES,
        "max_file_mb": _MAX_FILE_BYTES // 1024 // 1024,
        "max_chars_per_file": _MAX_CHARS_PER_FILE,
        "max_total_chars": _MAX_TOTAL_CHARS,
    }
