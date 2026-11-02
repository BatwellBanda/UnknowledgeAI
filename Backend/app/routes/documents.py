import uuid
from typing import Literal
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database.connection import get_db
from app.core.rate_limit import limit_by_user
from app.core.security import get_current_user
from app.models.user import User
from app.models.document import Document
from app.schemas.document import DocumentResponse, DocumentTextResponse
from app.schemas.chunk import ChunkResponse
from app.schemas.processing_job import ProcessingJobResponse
from app.services import (
    document_service,
    extraction_service,
    chunking_service,
    processing_service,
)

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post("/{document_id}/process", response_model=ProcessingJobResponse, status_code=202)
def process(document_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(limit_by_user("process", 30, 3600))):
    return processing_service.enqueue(db, document_id, current_user)


@router.get("/{document_id}/processing", response_model=ProcessingJobResponse)
def processing_status(document_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return processing_service.latest_job(db, document_id, current_user)


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    course_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        document_service.readable_documents(db, current_user)
        .filter(Document.course_id == course_id if course_id is not None else True)
        .order_by(Document.created_at.desc(), Document.id)
        .offset(offset).limit(limit).all()
    )


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return document_service.get_readable_document(db, document_id, current_user)


@router.get("/{document_id}/text", response_model=DocumentTextResponse)
def read_text(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = document_service.get_readable_document(db, document_id, current_user)
    if not document.extracted_text:
        raise HTTPException(status_code=400, detail="Document has no extracted text yet")
    return DocumentTextResponse(document_id=document.id, text=document.extracted_text)


@router.get("/{document_id}/download", response_class=FileResponse)
def download_original(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    path, filename = document_service.original_document_file(db, document_id, current_user)
    return FileResponse(
        path, filename=filename, media_type="application/octet-stream",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{document_id}/chunks", response_model=list[ChunkResponse])
def read_chunks(
    document_id: uuid.UUID,
    offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    from app.models.chunk import DocumentChunk

    document_service.get_readable_document(db, document_id, current_user)
    return db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).order_by(
        DocumentChunk.chunk_index,
    ).offset(offset).limit(limit).all()


@router.post("/upload", response_model=DocumentResponse)
def upload(
    file: UploadFile = File(...),
    course_id: uuid.UUID | None = Form(None),
    visibility: Literal["private", "course"] = Form("private"),
    db: Session = Depends(get_db),
    current_user: User = Depends(limit_by_user("upload", 30, 3600)),
):
    return document_service.upload_document(db, file, current_user, course_id, visibility)


@router.delete("/{document_id}", status_code=204)
def delete(document_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    document_service.delete_document(db, document_id, current_user)
    return Response(status_code=204)


@router.post("/{document_id}/extract", response_model=DocumentResponse)
def extract(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    with processing_service.manual_processing(db, document_id, current_user):
        return extraction_service.extract_document_text(db, document_id, current_user)


@router.post("/{document_id}/chunk", response_model=list[ChunkResponse])
def chunk(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    with processing_service.manual_processing(db, document_id, current_user):
        return chunking_service.chunk_document(db, document_id, current_user)


@router.post("/{document_id}/embed", response_model=list[ChunkResponse])
def embed(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services import embedding_service

    with processing_service.manual_processing(db, document_id, current_user):
        return embedding_service.embed_document(db, document_id, current_user)
