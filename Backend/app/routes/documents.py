import uuid
from typing import Literal
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from sqlalchemy.orm import Session
from database.connection import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.document import Document
from app.schemas.document import DocumentResponse, DocumentTextResponse
from app.schemas.chunk import ChunkResponse
from app.services import (
    document_service,
    extraction_service,
    chunking_service,
)

router = APIRouter(prefix="/documents", tags=["Documents"])


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


@router.post("/upload", response_model=DocumentResponse)
def upload(
    file: UploadFile = File(...),
    course_id: uuid.UUID | None = Form(None),
    visibility: Literal["private", "course"] = Form("private"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return document_service.upload_document(db, file, current_user, course_id, visibility)


@router.post("/{document_id}/extract", response_model=DocumentResponse)
def extract(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return extraction_service.extract_document_text(db, document_id, current_user)


@router.post("/{document_id}/chunk", response_model=list[ChunkResponse])
def chunk(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return chunking_service.chunk_document(db, document_id, current_user)


@router.post("/{document_id}/embed", response_model=list[ChunkResponse])
def embed(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services import embedding_service

    return embedding_service.embed_document(db, document_id, current_user)
