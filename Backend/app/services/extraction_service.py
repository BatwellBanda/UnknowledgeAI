import logging
import multiprocessing
import os
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import settings
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.models.user import User
from app.services import extractors
from app.services.extractors import EXTRACTORS

UPLOAD_DIR = str(settings.UPLOAD_DIR)
logger = logging.getLogger(__name__)


class ExtractionTimeout(Exception):
    pass


def run_extractor(file_type: str, file_path: str) -> list[dict]:
    """Parse in a disposable child process so a hostile file cannot hang the worker."""
    extractor = EXTRACTORS[file_type]
    timeout = settings.EXTRACTION_TIMEOUT_SECONDS
    if not timeout or extractor is not extractors.EXTRACTORS_DEFAULTS.get(file_type):
        # Isolation disabled, or a test double that cannot cross a process boundary.
        return extractor(file_path)
    with multiprocessing.get_context("spawn").Pool(1, maxtasksperchild=1) as pool:
        pending = pool.apply_async(extractors.extract, (file_type, file_path))
        try:
            return pending.get(timeout)
        except multiprocessing.TimeoutError:
            raise ExtractionTimeout(f"Extraction exceeded {timeout} seconds")
        # Leaving the block terminates the child, including a hung parser.


def extract_document_text(db: Session, document_id, current_user: User) -> Document:
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this document")
    if document.file_type not in EXTRACTORS:
        raise HTTPException(status_code=400, detail="No text extractor available for this file type")

    try:
        document.processing_status = "extracting"
        db.commit()
        segments = run_extractor(document.file_type, os.path.join(UPLOAD_DIR, document.stored_filename))
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
    except Exception as error:
        db.rollback()
        logger.exception("Text extraction failed for document %s", document_id)
        document.processing_status = "failed"
        db.commit()
        if isinstance(error, ExtractionTimeout):
            detail = "The document took too long to read. It may be damaged or too complex; try a simpler export."
        elif isinstance(error, extractors.UnsafeFileError):
            detail = f"{error}."
        else:
            detail = "Could not extract readable text. Check the file format and encoding; scanned PDFs require OCR."
        raise HTTPException(status_code=422, detail=detail)
    return document
