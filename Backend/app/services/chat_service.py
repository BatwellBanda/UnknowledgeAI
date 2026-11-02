import logging
import re
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse, Citation, ModelAnswer
from app.services import document_service, llm_service, search_service

logger = logging.getLogger(__name__)
NOT_FOUND = "I couldn't find enough support for an answer in your available materials."
MAX_CONTEXT_CHARACTERS = 12000


def validate_current_sources(db: Session, user: User, sources):
    """Recheck permissions and chunk content around a potentially slow model call."""
    documents = document_service.readable_documents(db, user).filter(Document.processing_status == "embedded")
    rows = db.query(DocumentChunk.id, DocumentChunk.content).filter(
        DocumentChunk.id.in_([source.chunk_id for source in sources]),
        DocumentChunk.document_id.in_(documents.with_entities(Document.id).statement),
    ).all()
    current = {chunk_id: content for chunk_id, content in rows}
    if any(current.get(source.chunk_id) != source.text for source in sources):
        raise HTTPException(status_code=409, detail="Materials or access changed during this request; please try again")


def normalise_quote(text):
    return " ".join(text.split())


def answer(db: Session, request: ChatRequest, user: User) -> ChatResponse:
    retrieved = search_service.search(db, request, user)
    if not retrieved.results:
        return ChatResponse(status="no_searchable_materials" if retrieved.status == "no_searchable_materials" else "not_found", answer=NOT_FOUND, citations=[])
    chosen = []
    characters = 0
    for source in retrieved.results:
        if characters + len(source.text) > MAX_CONTEXT_CHARACTERS:
            continue
        chosen.append(source)
        characters += len(source.text)
    if not chosen:
        raise HTTPException(status_code=400, detail="Retrieved passages exceed the answer context limit; reprocess the document")
    source_map = {f"S{index}": source for index, source in enumerate(chosen, 1)}
    validate_current_sources(db, user, chosen)
    raw = llm_service.generate_answer(request.query, [
        {"source_id": key, "text": source.text} for key, source in source_map.items()
    ])
    # Do not return a previously authorised passage after revocation or reprocessing.
    validate_current_sources(db, user, chosen)
    try:
        generated = ModelAnswer.model_validate(raw)
        if generated.status == "not_found":
            return ChatResponse(status="not_found", answer=NOT_FOUND, citations=[])
        lines = []
        citations = []
        seen = set()
        for claim in generated.claims:
            if not claim.text.strip() or re.search(r"\[S\d+\]", claim.text):
                raise ValueError("Invalid claim text")
            claim_sources = []
            for evidence in claim.evidence:
                source = source_map.get(evidence.source_id)
                quote = normalise_quote(evidence.quote)
                if source is None or not quote or quote not in normalise_quote(source.text):
                    raise ValueError("Citation is not an exact quotation of an authorised source")
                if evidence.source_id not in claim_sources:
                    claim_sources.append(evidence.source_id)
                key = (evidence.source_id, quote)
                if key not in seen:
                    citations.append(Citation(source_id=evidence.source_id, chunk_id=source.chunk_id,
                        document_id=source.document_id, document_name=source.document_name,
                        source_location=source.source_location, quote=quote))
                    seen.add(key)
            lines.append(claim.text.strip() + " " + " ".join(f"[{key}]" for key in claim_sources))
        return ChatResponse(status="answered", answer="\n\n".join(lines), citations=citations)
    except (ValidationError, ValueError, TypeError):
        logger.warning("Rejected an invalid generated answer or citation")
        raise HTTPException(status_code=502, detail="The model returned an answer whose citations could not be verified; please try again")
