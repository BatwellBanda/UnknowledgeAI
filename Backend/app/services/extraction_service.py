import os
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from docx import Document as DocxDocument
from pypdf import PdfReader
from app.models.document import Document
from app.models.user import User

UPLOAD_DIR = "uploads"


def _extract_docx_text(file_path: str) -> str:
    doc = DocxDocument(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def _extract_pdf_text(file_path: str) -> str:
    reader = PdfReader(file_path)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def _extract_txt_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


EXTRACTORS = {
    "docx": _extract_docx_text,
    "pdf": _extract_pdf_text,
    "txt": _extract_txt_text,
}


def extract_document_text(db: Session, document_id, current_user: User) -> Document:
    document = db.query(Document).filter(Document.id == document_id).first()

    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if document.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this document")

    extractor = EXTRACTORS.get(document.file_type)
    if not extractor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No text extractor available for file type '{document.file_type}'",
        )

    file_path = os.path.join(UPLOAD_DIR, document.stored_filename)

    try:
        document.processing_status = "extracting"
        db.commit()

        text = extractor(file_path)

        document.extracted_text = text
        document.processing_status = "extracted"
        db.commit()
        db.refresh(document)
    except Exception as e:
        document.processing_status = "failed"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Text extraction failed: {str(e)}",
        )

    return document