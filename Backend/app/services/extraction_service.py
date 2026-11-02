import logging
import os
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from docx import Document as DocxDocument
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.models.user import User

UPLOAD_DIR = "uploads"
logger = logging.getLogger(__name__)


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
    return [
        {"text": text, "location": {"kind": "pdf_page", "page": index}}
        for index, page in enumerate(PdfReader(file_path).pages, 1)
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


def extract_document_text(db: Session, document_id, current_user: User) -> Document:
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this document")
    extractor = EXTRACTORS.get(document.file_type)
    if not extractor:
        raise HTTPException(status_code=400, detail="No text extractor available for this file type")

    try:
        document.processing_status = "extracting"
        db.commit()
        segments = extractor(os.path.join(UPLOAD_DIR, document.stored_filename))
        if not segments or not any(segment["text"].strip() for segment in segments):
            raise ValueError("No readable text")
        # Re-extraction replaces old chunks and vectors atomically with the new text.
        db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete()
        document.extracted_segments = segments
        document.extracted_text = "\n".join(segment["text"] for segment in segments)
        document.processing_status = "extracted"
        db.commit()
        db.refresh(document)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error while extracting document %s", document_id)
        raise HTTPException(status_code=500, detail="Document processing could not be saved")
    except Exception:
        db.rollback()
        logger.exception("Text extraction failed for document %s", document_id)
        document.processing_status = "failed"
        db.commit()
        raise HTTPException(
            status_code=422,
            detail="Could not extract readable text. Check the file format and encoding; scanned PDFs require OCR.",
        )
    return document
