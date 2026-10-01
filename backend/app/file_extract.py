from __future__ import annotations

import re
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

from app.config import INGEST_DATA_DIR
from app.object_store import upload_bytes

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".rst"}
HTML_SUFFIXES = {".html", ".htm"}
PDF_SUFFIXES = {".pdf"}
DOCX_SUFFIXES = {".docx"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | HTML_SUFFIXES | PDF_SUFFIXES | DOCX_SUFFIXES


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        return "\n".join(self._parts)


def save_uploaded_file(data: bytes, *, job_id: str, filename: str) -> str:
    """Save raw upload under INGEST_DATA_DIR/{job_id}/ and return relative path."""
    safe_name = Path(filename).name
    if not safe_name:
        raise ValueError("invalid filename")

    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(f"unsupported file type: {suffix or '(none)'}. Supported: {supported}")

    base = Path(INGEST_DATA_DIR).resolve()
    dest_dir = base / job_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name
    dest.write_bytes(data)

    relative_path = f"{job_id}/{safe_name}"
    upload_bytes(data, key=relative_path)
    return relative_path


def resolve_ingest_path(path_str: str) -> Path:
    """Resolve a path and ensure it stays under INGEST_DATA_DIR."""
    base = Path(INGEST_DATA_DIR).resolve()
    base.mkdir(parents=True, exist_ok=True)

    candidate = Path(path_str)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve()

    if resolved != base and base not in resolved.parents:
        raise ValueError(f"path must be under ingest directory: {base}")
    if not resolved.is_file():
        raise FileNotFoundError(f"file not found: {resolved}")
    return resolved


def extract_text_from_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(f"unsupported file type: {suffix or '(none)'}. Supported: {supported}")
    data = path.read_bytes()
    return extract_text_from_bytes(data, filename=path.name)


def extract_text_from_bytes(data: bytes, *, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _decode_text(data)
    if suffix in HTML_SUFFIXES:
        return _extract_html(data)
    if suffix in PDF_SUFFIXES:
        return _extract_pdf(data)
    if suffix in DOCX_SUFFIXES:
        return _extract_docx(data)
    supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
    raise ValueError(f"unsupported file type: {suffix or '(none)'}. Supported: {supported}")


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            text = data.decode(encoding).strip()
            if text:
                return text
        except UnicodeDecodeError:
            continue
    raise ValueError("unable to decode text file")


def _extract_html(data: bytes) -> str:
    html = _decode_text(data)
    parser = _HTMLTextExtractor()
    parser.feed(html)
    text = parser.get_text().strip()
    if text:
        return text
    return re.sub(r"<[^>]+>", " ", html)


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF support requires pypdf. Install with: pip install pypdf") from exc

    reader = PdfReader(BytesIO(data))
    pages = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            pages.append(page_text.strip())
    text = "\n\n".join(pages).strip()
    if not text:
        raise ValueError("no text extracted from PDF")
    return text


def _extract_docx(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError(
            "DOCX support requires python-docx. Install with: pip install python-docx"
        ) from exc

    document = Document(BytesIO(data))
    paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs).strip()
    if not text:
        raise ValueError("no text extracted from DOCX")
    return text
