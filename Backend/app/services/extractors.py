"""Pure file inspection and text extraction for untrusted uploads.

Kept free of database and application imports so extraction can run in a short-lived
child process that is killed if a crafted file makes a parser hang.
"""
import zipfile
from docx import Document as DocxDocument
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

MAX_PDF_PAGES = 2000
# Office files are ZIP archives; bound their expansion to stop decompression bombs.
MAX_ARCHIVE_ENTRIES = 10000
MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
OFFICE_MARKERS = {"docx": "word/document.xml", "pptx": "ppt/presentation.xml"}


class UnsafeFileError(ValueError):
    """The file does not match its declared type or exceeds safe parsing limits."""


def _check_office_archive(file, file_type: str):
    try:
        archive = zipfile.ZipFile(file)
    except (zipfile.BadZipFile, OSError):
        raise UnsafeFileError("The file is not a valid Office document")
    with archive:
        entries = archive.infolist()
        names = {entry.filename for entry in entries}
        if "[Content_Types].xml" not in names or OFFICE_MARKERS[file_type] not in names:
            raise UnsafeFileError("The file content does not match its extension")
        if len(entries) > MAX_ARCHIVE_ENTRIES:
            raise UnsafeFileError("The document contains too many parts")
        uncompressed = sum(entry.file_size for entry in entries)
        compressed = sum(entry.compress_size for entry in entries)
        if uncompressed > MAX_UNCOMPRESSED_BYTES or uncompressed > max(compressed, 1) * MAX_COMPRESSION_RATIO:
            raise UnsafeFileError("The document expands beyond safe processing limits")


def inspect_upload(file, file_type: str) -> None:
    """Validate a seekable binary file's content against its declared extension."""
    file.seek(0)
    if file_type == "pdf":
        if b"%PDF-" not in file.read(1024):
            raise UnsafeFileError("The file content does not match its extension")
    elif file_type in OFFICE_MARKERS:
        _check_office_archive(file, file_type)
    elif file_type == "txt":
        data = file.read()
        if b"\x00" in data:
            raise UnsafeFileError("Text files must not contain binary data")
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise UnsafeFileError("Text files must be UTF-8 encoded")
    else:
        raise UnsafeFileError("Unsupported file type")
    file.seek(0)


def _extract_docx_text(file_path: str) -> list[dict]:
    segments = []
    section = None
    paragraph_index = table_index = 0
    for block in DocxDocument(file_path).iter_inner_content():
        if isinstance(block, Paragraph):
            paragraph_index += 1
            text = block.text
            if block.style and block.style.name.startswith("Heading") and text.strip():
                section = text.strip()
            location = {"kind": "docx_paragraph", "paragraph": paragraph_index, "section": section}
        else:
            table_index += 1
            text = "\n".join(" | ".join(cell.text for cell in row.cells) for row in block.rows)
            location = {"kind": "docx_table", "table": table_index, "section": section}
        if text.strip():
            segments.append({"text": text, "location": location})
    return segments


def _extract_pdf_text(file_path: str) -> list[dict]:
    reader = PdfReader(file_path)
    if len(reader.pages) > MAX_PDF_PAGES:
        raise UnsafeFileError(f"PDFs are limited to {MAX_PDF_PAGES} pages")
    return [
        {"text": text, "location": {"kind": "pdf_page", "page": index}}
        for index, page in enumerate(reader.pages, 1)
        if (text := page.extract_text()) and text.strip()
    ]


def _slide_shape_text(shape) -> list[str]:
    if hasattr(shape, "shapes"):
        return [text for child in shape.shapes for text in _slide_shape_text(child)]
    if shape.has_table:
        return [" | ".join(cell.text for cell in row.cells) for row in shape.table.rows]
    if shape.has_text_frame:
        return [shape.text]
    return []


def _extract_pptx_text(file_path: str) -> list[dict]:
    from pptx import Presentation

    segments = []
    for index, slide in enumerate(Presentation(file_path).slides, 1):
        text = "\n".join(text for shape in slide.shapes for text in _slide_shape_text(shape) if text.strip())
        if text.strip():
            segments.append({"text": text, "location": {"kind": "pptx_slide", "slide": index}})
    return segments


def _extract_txt_text(file_path: str) -> list[dict]:
    with open(file_path, "r", encoding="utf-8-sig") as file:
        text = file.read()
    return [{"text": text, "location": {"kind": "txt_lines", "line_start": 1, "line_end": max(1, len(text.splitlines()))}}] if text.strip() else []


EXTRACTORS = {
    "docx": _extract_docx_text,
    "pdf": _extract_pdf_text,
    "pptx": _extract_pptx_text,
    "txt": _extract_txt_text,
}


def extract(file_type: str, file_path: str) -> list[dict]:
    """Entry point for the isolated child process; re-checks archives before parsing."""
    if file_type in OFFICE_MARKERS:
        with open(file_path, "rb") as file:
            _check_office_archive(file, file_type)
    return EXTRACTORS[file_type](file_path)
# Built-in extractors; replacements (such as test doubles) run in-process instead.
EXTRACTORS_DEFAULTS = dict(EXTRACTORS)
