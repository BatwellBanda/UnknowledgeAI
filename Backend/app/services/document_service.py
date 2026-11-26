import logging
import os
import shutil
import uuid
from pathlib import Path
from fastapi import UploadFile, HTTPException, status
from sqlalchemy import or_, and_, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.user import User
from app.services.course_service import accessible_course_ids, require_course_membership
from app.services import media_service
from app.services.extractors import UnsafeFileError, inspect_upload

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt"}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB
UPLOAD_DIR = str(settings.UPLOAD_DIR)
logger = logging.getLogger(__name__)


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


def original_document_file(db: Session, document_id, user: User):
    document = get_readable_document(db, document_id, user)
    root = Path(UPLOAD_DIR).resolve()
    path = (root / document.stored_filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Original file unavailable")
    # An uploaded filename is a display label, never a filesystem path.
    filename = document.original_filename.replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(c for c in filename if ord(c) >= 32 and ord(c) != 127)
    return path, filename or "material"


def upload_document(db: Session, file: UploadFile, owner: User, course_id=None, visibility="private",
                    captions: UploadFile | None = None) -> Document:
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
    recording = media_service.is_recording(ext.lstrip("."))

    if ext not in ALLOWED_EXTENSIONS and not recording:
        allowed = sorted(ALLOWED_EXTENSIONS | {f".{kind}" for kind in media_service.RECORDING_TYPES})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(allowed)}",
        )
    if captions is not None and not recording:
        raise HTTPException(status_code=400, detail="Caption files can only accompany a recording")

    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)

    limit_mb = settings.MAX_RECORDING_MB if recording else MAX_FILE_SIZE_BYTES // (1024 * 1024)
    if file_size > limit_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum allowed size is {limit_mb} MB{' for recordings' if recording else ''}.",
        )

    used = db.query(func.coalesce(func.sum(Document.file_size_bytes), 0)).filter(Document.owner_id == owner.id).scalar()
    if used + file_size > settings.MAX_USER_STORAGE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Storage limit of {settings.MAX_USER_STORAGE_MB} MB reached; delete materials you no longer need",
        )

    try:
        if recording:
            media_service.check_signature(file.file, ext.lstrip("."))
        else:
            inspect_upload(file.file, ext.lstrip("."))
    except (UnsafeFileError, media_service.MediaError) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{error}.")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stored_filename = f"{uuid.uuid4()}{ext}"
    stored_path = os.path.join(UPLOAD_DIR, stored_filename)

    with open(stored_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer, 1024 * 1024)

    media, cues = {}, None
    if recording:
        # FFprobe needs the file on disk; remove it again if the recording or its captions are unusable.
        from app.services import transcript_service
        try:
            info = media_service.probe(stored_path)
            media = {"media_kind": info["kind"], "media_duration_seconds": info["duration"]}
            if captions is not None:
                data, caption_type = transcript_service.caption_bytes(captions)
                cues = transcript_service.parse_captions(data, caption_type, info["duration"])
        except (media_service.MediaError, transcript_service.CaptionError) as error:
            os.remove(stored_path)
            raise HTTPException(status_code=400, detail=f"{error}.")
        except media_service.MediaToolMissing:
            os.remove(stored_path)
            logger.error("Recording upload rejected: FFmpeg/FFprobe not found")
            raise HTTPException(status_code=503, detail="Recording uploads are not available on this server right now.")

    new_document = Document(
        owner_id=owner.id,
        course_id=course_id,
        visibility=visibility,
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_type=ext.replace(".", ""),
        file_size_bytes=file_size,
        processing_status="uploaded",
        **media,
    )
    db.add(new_document)
    try:
        if cues:
            db.flush()
            transcript_service.replace_segments(db, new_document, cues, "captions")
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        os.remove(stored_path)
        raise
    db.refresh(new_document)
    return new_document


def delete_document(db: Session, document_id, user: User) -> None:
    """Owner-only removal of the record, its chunks, jobs and the stored file."""
    from app.services.processing_service import active_job, document_lock

    document = db.query(Document).filter(Document.id == document_id, Document.owner_id == user.id).first()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    with document_lock(db.get_bind(), document.id) as acquired:
        if not acquired or active_job(db, document.id):
            raise HTTPException(status_code=409, detail="This document is being processed; try again when it finishes")
        stored_filename = document.stored_filename
        db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete()
        db.delete(document)
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise
    # Remove the file only after the database no longer references it.
    root = Path(UPLOAD_DIR).resolve()
    path = (root / stored_filename).resolve()
    if path.is_relative_to(root):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove stored file for deleted document %s", document_id)
