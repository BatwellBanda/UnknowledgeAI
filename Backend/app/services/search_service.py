import logging
import numpy as np
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.concurrency import embedding_slots, slot
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.user import User
from app.schemas.search import SearchRequest, SearchResponse, SearchResult
from app.services import document_service, embedding_service

logger = logging.getLogger(__name__)


def query_embedding(query: str):
    try:
        model = embedding_service.get_model()
        # Reject overlong questions rather than silently losing their ending.
        if len(model.tokenizer.encode(query, add_special_tokens=True)) > model.max_seq_length:
            raise HTTPException(status_code=400, detail="Search question exceeds the embedding model's token limit; shorten it")
        with slot(embedding_slots, "Search is busy right now; please try again in a moment"):
            vector = np.asarray(model.encode([query], show_progress_bar=False)[0], dtype=np.float32)
        if vector.shape != (384,) or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0:
            raise ValueError("Invalid query embedding")
        return vector
    except HTTPException:
        raise
    except Exception:
        logger.exception("Search embedding generation failed")
        raise HTTPException(status_code=503, detail="Search model is temporarily unavailable; please try again")


def search(db: Session, request: SearchRequest, user: User) -> SearchResponse:
    documents = document_service.readable_documents(db, user).filter(Document.processing_status == "embedded")
    if request.course_id is not None:
        documents = documents.filter(Document.course_id == request.course_id)
    if request.document_id is not None:
        documents = documents.filter(Document.id == request.document_id)
    readable_ids = documents.with_entities(Document.id).statement
    eligible = db.query(DocumentChunk).filter(
        DocumentChunk.document_id.in_(readable_ids),
        DocumentChunk.embedding.is_not(None),
        func.vector_norm(DocumentChunk.embedding) > 0,
    )
    if eligible.with_entities(DocumentChunk.id).first() is None:
        return SearchResponse(query=request.query, status="no_searchable_materials", results=[])

    vector = query_embedding(request.query)
    distance = DocumentChunk.embedding.cosine_distance(vector)
    similarity = 1 - distance
    query = eligible.join(Document, Document.id == DocumentChunk.document_id).with_entities(
        DocumentChunk, Document, similarity.label("similarity"),
    )
    if request.min_similarity is not None:
        query = query.filter(similarity >= request.min_similarity)
    # Exact pgvector search provides the quality baseline before approximate indexing.
    rows = query.order_by(distance, DocumentChunk.id).limit(request.top_k).all()
    results = [SearchResult(
        chunk_id=chunk.id, document_id=document.id, course_id=document.course_id,
        document_name=document.original_filename, chunk_index=chunk.chunk_index,
        text=chunk.content, similarity=max(-1.0, min(1.0, float(score))),
        source_location=chunk.source_location,
    ) for chunk, document, score in rows]
    return SearchResponse(query=request.query, status="matches" if results else "no_matches", results=results)
