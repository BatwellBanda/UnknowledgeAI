from __future__ import annotations
import uuid
from typing import TYPE_CHECKING
import numpy as np
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.models.user import User

_model = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed_document(db: Session, document_id: uuid.UUID, current_user: User) -> list[DocumentChunk]:
    document = db.query(Document).filter(Document.id == document_id).first()

    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if document.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this document")

    if document.processing_status == "failed":
        raise HTTPException(status_code=400, detail="Re-extract and chunk the failed document before embedding")

    chunks = db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).order_by(DocumentChunk.chunk_index).all()

    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document has no chunks yet. Run /chunk first.",
        )

    model = get_model()
    texts = [chunk.content for chunk in chunks]
    if any(len(model.tokenizer.encode(content, add_special_tokens=True, truncation=False)) > model.max_seq_length
           for content in texts):
        raise HTTPException(status_code=400, detail="Document chunks exceed the embedding token limit; re-chunk or process this document again")
    vectors = np.asarray(model.encode(texts, show_progress_bar=False), dtype=np.float32)
    if vectors.shape != (len(chunks), 384) or not np.isfinite(vectors).all() or (np.linalg.norm(vectors, axis=1) == 0).any():
        raise HTTPException(status_code=503, detail="The embedding model returned invalid vectors; retry processing")

    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = vector

    document.processing_status = "embedded"
    db.commit()

    for chunk in chunks:
        db.refresh(chunk)

    return chunks
