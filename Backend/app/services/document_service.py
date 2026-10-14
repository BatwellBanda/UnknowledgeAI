import os
import uuid
from fastapi import UploadFile, HTTPException, status
from sqlalchemy import or_, and_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.models.document import Document
from app.models.user import User
from app.services.course_service import accessible_course_ids, require_course_membership

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt"}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB
UPLOAD_DIR = "uploads"


def readable_documents(db: Session, user: User):
    """Apply permissions in the query; private course uploads remain owner-only."""
    return db.query(Document).filter(or_(
        Document.owner_id == user.id,
        and_(Document.visibility == "course", Document.course_id.in_(accessible_course_ids(user.id))),
    ))


def get_readable_document(db: Session, document_id, user: User) -> Document:
    document = readable_documents(db, user).filter(Document.id == document_id).first()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def upload_document(db: Session, file: UploadFile, owner: User, course_id=None, visibility="private") -> Document:
    if visibility not in {"private", "course"}:
        raise HTTPException(status_code=400, detail="Invalid document visibility")
    if visibility == "course" and course_id is None:
        raise HTTPException(status_code=400, detail="Shared materials require a course")
    if course_id is not None:
        course = require_course_membership(db, course_id, owner)
        if visibility == "course" and (
            owner.role.name not in {"LECTURER", "ADMIN"} or course.instructor_id != owner.id
        ):
            raise HTTPException(status_code=403, detail="Only the course instructor can publish shared materials")
    original_filename = file.filename
    if not original_filename or len(original_filename) > 255:
        raise HTTPException(status_code=400, detail="A filename of at most 255 characters is required")
    ext = os.path.splitext(original_filename)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File too large. Maximum allowed size is 20 MB.",
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stored_filename = f"{uuid.uuid4()}{ext}"
    stored_path = os.path.join(UPLOAD_DIR, stored_filename)

    with open(stored_path, "wb") as buffer:
        buffer.write(file.file.read())

    new_document = Document(
        owner_id=owner.id,
        course_id=course_id,
        visibility=visibility,
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_type=ext.replace(".", ""),
        file_size_bytes=file_size,
        processing_status="uploaded",
    )
    db.add(new_document)
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        os.remove(stored_path)
        raise
    db.refresh(new_document)
    return new_document
