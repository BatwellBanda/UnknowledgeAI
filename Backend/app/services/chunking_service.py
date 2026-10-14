import re
import uuid
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.models.user import User

CHUNK_SIZE = 1000       # characters per chunk
CHUNK_OVERLAP = 150     # characters shared between consecutive chunks


def clean_text(raw_text: str) -> str:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)          # collapse repeated spaces/tabs
    text = re.sub(r"\n{3,}", "\n\n", text)        # collapse 3+ blank lines into one
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= chunk_size:
        return [text] if text else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def chunk_document(db: Session, document_id: uuid.UUID, current_user: User) -> list[DocumentChunk]:
    document = db.query(Document).filter(Document.id == document_id).first()

    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if document.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this document")

    if not document.extracted_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document has no extracted text yet. Run /extract first.",
        )

    db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete()

    cleaned = clean_text(document.extracted_text)
    pieces = split_into_chunks(cleaned)

    new_chunks = []
    for index, piece in enumerate(pieces):
        chunk = DocumentChunk(document_id=document.id, chunk_index=index, content=piece)
        db.add(chunk)
        new_chunks.append(chunk)

    document.processing_status = "chunked"
    db.commit()

    for chunk in new_chunks:
        db.refresh(chunk)

    return new_chunks