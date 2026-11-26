import uuid
from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database.connection import get_db
from app.core.rate_limit import limit_by_user
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.recording import (
    MediaLinkResponse, NoteCreate, NoteResponse, NoteUpdate, SegmentUpdate, TranscriptResponse,
)
from app.services import note_service, transcript_service

recordings = APIRouter(prefix="/documents", tags=["Recordings"])
notes = APIRouter(prefix="/notes", tags=["Notes"])


@recordings.get("/{document_id}/transcript", response_model=TranscriptResponse)
def get_transcript(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return transcript_service.get_transcript(db, document_id, user)


@recordings.put("/{document_id}/transcript", response_model=TranscriptResponse)
def replace_transcript(document_id: uuid.UUID, captions: UploadFile = File(...), db: Session = Depends(get_db),
                       user: User = Depends(limit_by_user("captions", 30, 3600))):
    return transcript_service.import_captions(db, document_id, captions, user)


@recordings.delete("/{document_id}/transcript", status_code=204)
def clear_transcript(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    transcript_service.clear_transcript(db, document_id, user)
    return Response(status_code=204)


@recordings.patch("/{document_id}/transcript/segments/{segment_id}", response_model=TranscriptResponse)
def edit_segment(document_id: uuid.UUID, segment_id: uuid.UUID, data: SegmentUpdate, db: Session = Depends(get_db),
                 user: User = Depends(limit_by_user("transcript_edit", 600, 3600))):
    return transcript_service.edit_segment(db, document_id, segment_id, data.text, user)


@recordings.post("/{document_id}/media-link", response_model=MediaLinkResponse)
def media_link(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return transcript_service.media_link(db, document_id, user)


@recordings.get("/{document_id}/media", response_class=FileResponse)
def stream_media(document_id: uuid.UUID, token: str = Query(min_length=1, max_length=2000), db: Session = Depends(get_db)):
    # Range requests are served by FileResponse, so players can seek without downloading everything.
    path, media_type = transcript_service.media_file(db, document_id, token)
    return FileResponse(path, media_type=media_type, content_disposition_type="inline",
                        headers={"Cache-Control": "private, no-store"})


@recordings.get("/{document_id}/notes", response_model=list[NoteResponse])
def list_notes(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return note_service.list_notes(db, document_id, user)


@recordings.post("/{document_id}/notes", response_model=NoteResponse, status_code=201)
def create_note(document_id: uuid.UUID, data: NoteCreate, db: Session = Depends(get_db),
                user: User = Depends(limit_by_user("note_create", 300, 3600))):
    return note_service.create_note(db, document_id, data, user)


@notes.patch("/{note_id}", response_model=NoteResponse)
def update_note(note_id: uuid.UUID, data: NoteUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return note_service.update_note(db, note_id, data, user)


@notes.delete("/{note_id}", status_code=204)
def delete_note(note_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    note_service.delete_note(db, note_id, user)
    return Response(status_code=204)
