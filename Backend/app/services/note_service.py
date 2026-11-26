"""Private study notes on a material, optionally pinned to a moment in a recording."""
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.document import Document
from app.models.recording import Note
from app.models.user import User
from app.schemas.recording import NoteCreate, NoteUpdate
from app.services import document_service, media_service

TIME_TOLERANCE_SECONDS = 1.0


def check_timestamp(document: Document, timestamp: float | None) -> None:
    if timestamp is None:
        return
    if not media_service.is_recording(document.file_type):
        raise HTTPException(status_code=400, detail="Only notes on recordings can have a time")
    if document.media_duration_seconds is not None and timestamp > document.media_duration_seconds + TIME_TOLERANCE_SECONDS:
        raise HTTPException(status_code=400, detail="The note's time is past the end of the recording")


def list_notes(db: Session, document_id, user: User) -> list[Note]:
    document = document_service.get_readable_document(db, document_id, user)
    return db.query(Note).filter(Note.owner_id == user.id, Note.document_id == document.id).order_by(
        Note.timestamp_seconds.is_(None), Note.timestamp_seconds, Note.created_at, Note.id).all()


def create_note(db: Session, document_id, data: NoteCreate, user: User) -> Note:
    document = document_service.get_readable_document(db, document_id, user)
    check_timestamp(document, data.timestamp_seconds)
    note = Note(owner_id=user.id, document_id=document.id, body=data.body, timestamp_seconds=data.timestamp_seconds)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def own_note(db: Session, note_id, user: User) -> Note:
    note = db.query(Note).filter(Note.id == note_id, Note.owner_id == user.id).first()
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


def update_note(db: Session, note_id, data: NoteUpdate, user: User) -> Note:
    note = own_note(db, note_id, user)
    # Notes follow the material: once access to it ends, they are hidden until it returns.
    document = document_service.get_readable_document(db, note.document_id, user)
    changes = data.model_dump(exclude_unset=True)
    if "body" in changes and changes["body"] is None:
        raise HTTPException(status_code=422, detail="A note needs some text")
    if "timestamp_seconds" in changes:
        check_timestamp(document, changes["timestamp_seconds"])
    for field, value in changes.items():
        setattr(note, field, value)
    note.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(note)
    return note


def delete_note(db: Session, note_id, user: User) -> None:
    db.delete(own_note(db, note_id, user))
    db.commit()
