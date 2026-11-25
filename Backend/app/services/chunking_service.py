import re
import uuid
import logging
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.models.user import User
from app.services import embedding_service

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1000       # character cap in addition to the model's token limit
CHUNK_OVERLAP = 150     # maximum character overlap, reduced for short windows


def clean_text(raw_text: str) -> str:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)          # collapse repeated spaces/tabs
    text = re.sub(r"\n{3,}", "\n\n", text)        # collapse 3+ blank lines into one
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    return [text[start:end] for start, end in chunk_spans(text, chunk_size, overlap)]


def chunk_spans(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk size must be positive and overlap must be between zero and chunk size")
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text) and not text[end].isspace():
            lower = max(start + overlap + 1, start + chunk_size // 2)
            boundaries = [m.start() for m in re.finditer(r"\s", text[lower:end])]
            if boundaries:
                end = lower + boundaries[-1]
        yield start, end
        if end == len(text):
            break
        next_start = max(start + 1, end - overlap)
        while next_start < end and next_start > 0 and not text[next_start - 1].isspace():
            next_start += 1
        start = next_start


def token_chunk_spans(text: str, tokenizer, max_tokens: int,
                      chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    """Bound original-text windows by characters and untruncated model tokens.

    Keep source offsets rather than decoding token IDs back to changed text.
    Prefix token counts need not be monotonic, so only emit tested candidates;
    binary search finds a fitting window, not necessarily the longest one.
    """
    if max_tokens <= 0 or chunk_size <= 0 or not 0 <= overlap < chunk_size:
        raise ValueError("Invalid token budget, chunk size, or overlap")

    def fits(start, end):
        return len(tokenizer.encode(text[start:end].strip(), add_special_tokens=True,
                                    truncation=False)) <= max_tokens

    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if not fits(start, end):
            low, high, best = start + 1, end - 1, start
            while low <= high:
                candidate = (low + high) // 2
                if fits(start, candidate):
                    best = candidate
                    low = candidate + 1
                else:
                    high = candidate - 1
            if best == start:
                raise ValueError("Token budget cannot hold a character and special tokens")
            end = best
        if end < len(text) and not text[end].isspace():
            boundaries = list(re.finditer(r"\s", text[start + (end - start) // 2:end]))
            if boundaries:
                boundary = start + (end - start) // 2 + boundaries[-1].start()
                if boundary > start and fits(start, boundary):
                    end = boundary
        yield start, end
        if end == len(text):
            break
        # Short token-limited windows must still make progress. Preserve overlap
        # when possible, aligning to words without skipping uncovered text.
        next_start = end - min(overlap, (end - start) // 2)
        while next_start < end and not text[next_start - 1].isspace():
            next_start += 1
        start = next_start


def located_chunks(segments: list[dict], *, tokenizer=None, max_tokens=None) -> list[dict]:
    if (tokenizer is None) != (max_tokens is None):
        raise ValueError("Tokenizer and token budget must be supplied together")
    pieces = []
    for segment in segments:
        location = segment["location"]
        if location["kind"] == "txt_lines":
            # Keep newline count so source ranges refer to original file lines.
            text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in segment["text"].splitlines())
        else:
            text = clean_text(segment["text"])
        spans = chunk_spans(text) if tokenizer is None else token_chunk_spans(text, tokenizer, max_tokens)
        for start, end in spans:
            # Calculate TXT line references from the text actually returned.
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            content = text[start:end].strip()
            if not content:
                continue
            source = location.copy()
            if source["kind"] == "txt_lines":
                base = location.get("line_start", 1)
                source["line_start"] = base + text.count("\n", 0, start)
                source["line_end"] = base + text.count("\n", 0, max(start, end - 1))
            pieces.append({"content": content, "source_location": source})
    return pieces


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

    if document.processing_status == "failed":
        raise HTTPException(status_code=400, detail="Re-extract the failed document before chunking")
    segments = document.extracted_segments or [{"text": document.extracted_text, "location": {"kind": "unknown"}}]
    try:
        model = embedding_service.get_model()
        pieces = located_chunks(segments, tokenizer=model.tokenizer, max_tokens=model.max_seq_length)
    except Exception:
        logger.exception("Token-aware document chunking failed")
        raise HTTPException(status_code=503, detail="The search model is unavailable for chunking; retry processing")
    if not pieces:
        raise HTTPException(status_code=400, detail="Document has no readable text")
    db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete()

    new_chunks = []
    for index, piece in enumerate(pieces):
        chunk = DocumentChunk(document_id=document.id, chunk_index=index, **piece)
        db.add(chunk)
        new_chunks.append(chunk)

    document.processing_status = "chunked"
    db.commit()

    for chunk in new_chunks:
        db.refresh(chunk)

    return new_chunks
