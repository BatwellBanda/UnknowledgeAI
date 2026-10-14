import uuid
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sentence_transformers import SentenceTransformer
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.models.user import User

_model = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed_document(db: Session, document_id: uuid.UUID, current_user: User) -> list[DocumentChunk]:
    document = db.query(Document).filter(Document.id == document_id).first()

    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if document.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this document")

    chunks = db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).order_by(DocumentChunk.chunk_index).all()

    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document has no chunks yet. Run /chunk first.",
        )

    model = get_model()
    texts = [chunk.content for chunk in chunks]
    vectors = model.encode(texts, show_progress_bar=False)

    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = vector

    document.processing_status = "embedded"
    db.commit()

    for chunk in chunks:
        db.refresh(chunk)

    return chunks