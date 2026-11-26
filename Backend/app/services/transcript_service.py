"""Timestamped transcripts for lecture recordings.

A transcript comes from an uploaded caption file (WebVTT or SubRip) or from local Whisper
speech recognition during processing. The owner can correct any line; processing then
indexes the corrected text, and each searchable passage keeps its time span for playback.
"""
import html
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import jwt
from fastapi import HTTPException, UploadFile
from sqlalchemy import update
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.security import ALGORITHM
from app.models.document import Document
from app.models.recording import TranscriptSegment
from app.models.user import User
from app.schemas.recording import TranscriptResponse, TranscriptSegmentResponse
from app.services import document_service, media_service
from app.services.media_service import SAMPLE_RATE, MediaError

logger = logging.getLogger(__name__)

CAPTION_TYPES = {"vtt", "srt"}
MAX_CAPTION_BYTES = 2 * 1024 * 1024
MAX_CUES = 20000
MAX_CUE_CHARS = 2000
# Caption times may run slightly past the measured media length.
TIME_TOLERANCE_SECONDS = 2.0
MEDIA_AUDIENCE = "uniknowledge-media"
_TIME = re.compile(r"^(?:(\d+):)?([0-5]?\d):([0-5]\d)[.,](\d{1,3})$")
_TAG = re.compile(r"<[^>]*>")
_recognizer = None


class CaptionError(ValueError):
    """The caption file cannot be used; the message is safe to show."""


class TranscriptionUnavailable(RuntimeError):
    """Speech recognition is disabled or its model cannot be loaded."""


# Caption files

def _seconds(value: str) -> float:
    match = _TIME.match(value.strip())
    if not match:
        raise CaptionError(f"Unreadable caption time '{value.strip()[:20]}'")
    hours, minutes, seconds, fraction = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction.ljust(3, "0")) / 1000


def parse_captions(data: bytes, file_type: str, duration: float | None = None) -> list[dict]:
    """Parse WebVTT or SubRip captions into ordered cues of start, end and plain text."""
    if file_type not in CAPTION_TYPES:
        raise CaptionError("Captions must be a .vtt or .srt file")
    if len(data) > MAX_CAPTION_BYTES:
        raise CaptionError("Caption files are limited to 2 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CaptionError("Caption files must be UTF-8 encoded")
    if "\x00" in text:
        raise CaptionError("Caption files must not contain binary data")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if file_type == "vtt" and not text.lstrip("﻿").startswith("WEBVTT"):
        raise CaptionError("WebVTT files must start with WEBVTT")

    cues = []
    for block in re.split(r"\n[ \t]*\n", text):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if file_type == "vtt" and re.match(r"(WEBVTT|NOTE|STYLE|REGION)\b", lines[0].strip()):
            continue
        # A cue may start with an identifier line before its timing line.
        timing = next((index for index, line in enumerate(lines[:2]) if "-->" in line), None)
        if timing is None:
            raise CaptionError("Each caption needs a timing line such as 00:01:02.000 --> 00:01:05.000")
        start_text, end_text = lines[timing].split("-->", 1)
        start = _seconds(start_text)
        # WebVTT cue settings (position, alignment) follow the end time.
        end = _seconds(end_text.strip().split()[0] if end_text.strip() else "")
        if end < start:
            raise CaptionError("A caption ends before it starts")
        words = html.unescape(_TAG.sub("", " ".join(lines[timing + 1:])))
        words = re.sub(r"\s+", " ", words).strip()
        if not words:
            continue
        if len(words) > MAX_CUE_CHARS:
            raise CaptionError(f"Captions are limited to {MAX_CUE_CHARS} characters each")
        cues.append({"start": start, "end": end, "text": words})
        if len(cues) > MAX_CUES:
            raise CaptionError(f"Caption files are limited to {MAX_CUES} captions")
    if not cues:
        raise CaptionError("The caption file has no timed captions")
    return _normalise(cues, duration)


def _normalise(cues: list[dict], duration: float | None) -> list[dict]:
    cues = sorted(cues, key=lambda cue: (cue["start"], cue["end"]))
    if duration is not None and cues[-1]["start"] > duration + TIME_TOLERANCE_SECONDS:
        raise CaptionError("Caption times go beyond the end of the recording")
    merged = []
    for cue in cues:
        if duration is not None:
            cue["end"] = min(cue["end"], duration)
            cue["start"] = min(cue["start"], cue["end"])
        # Rolling captions often repeat a line in back-to-back cues; keep it once.
        if merged and merged[-1]["text"] == cue["text"] and cue["start"] <= merged[-1]["end"] + 0.05:
            merged[-1]["end"] = max(merged[-1]["end"], cue["end"])
            continue
        merged.append(cue)
    return merged


# Speech recognition

def get_recognizer():
    global _recognizer
    if _recognizer is None:
        from transformers import pipeline

        _recognizer = pipeline("automatic-speech-recognition", model=settings.WHISPER_MODEL, device="cpu")
    return _recognizer


def transcribe(path: str, duration: float | None) -> list[dict]:
    """Run local Whisper over the whole recording and return timed segments."""
    if settings.TRANSCRIPTION_PROVIDER == "disabled":
        raise TranscriptionUnavailable("Transcription is disabled")
    audio = media_service.decode_audio(path, duration)
    if audio.size == 0:
        raise MediaError("The recording has no audible content")
    try:
        recognizer = get_recognizer()
    except Exception as error:
        logger.exception("Speech recognition model could not be loaded")
        raise TranscriptionUnavailable("The speech recognition model is unavailable") from error
    options = {"task": "transcribe"}
    if settings.TRANSCRIPTION_LANGUAGE:
        options["language"] = settings.TRANSCRIPTION_LANGUAGE
    length = duration or audio.size / SAMPLE_RATE
    result = recognizer({"raw": audio, "sampling_rate": SAMPLE_RATE}, chunk_length_s=30,
                        return_timestamps=True, generate_kwargs=options)
    segments = []
    for chunk in result.get("chunks") or []:
        text = re.sub(r"\s+", " ", chunk.get("text") or "").strip()
        start, end = (chunk.get("timestamp") or (None, None))
        if not text:
            continue
        start = min(max(float(start or 0), 0.0), length)
        end = min(max(float(end) if end is not None else length, start), length)
        segments.append({"start": start, "end": end, "text": text[:MAX_CUE_CHARS]})
    return segments


# Stored transcripts

def segments_of(db: Session, document_id) -> list[TranscriptSegment]:
    return db.query(TranscriptSegment).filter(TranscriptSegment.document_id == document_id).order_by(TranscriptSegment.position).all()


def bump_revision(db: Session, document: Document) -> None:
    # Increment in SQL so concurrent edits never overwrite each other's count.
    db.execute(update(Document).where(Document.id == document.id).values(transcript_revision=Document.transcript_revision + 1))


def replace_segments(db: Session, document: Document, cues: list[dict], source: str) -> list[TranscriptSegment]:
    db.query(TranscriptSegment).filter(TranscriptSegment.document_id == document.id).delete()
    rows = [TranscriptSegment(document_id=document.id, position=index, start_seconds=cue["start"],
                              end_seconds=cue["end"], text=cue["text"]) for index, cue in enumerate(cues)]
    db.add_all(rows)
    document.transcript_source = source
    db.flush()
    bump_revision(db, document)
    return rows


def recording_segments(db: Session, document: Document) -> list[dict]:
    """Extraction for recordings: the stored transcript, transcribing first if there is none."""
    db.refresh(document, ["transcript_revision"])
    # Read the revision before the lines: an edit in between then shows as stale, never lost.
    revision = document.transcript_revision
    rows = segments_of(db, document.id)
    if not rows:
        path = str(Path(document_service.UPLOAD_DIR) / document.stored_filename)
        cues = transcribe(path, document.media_duration_seconds)
        if not cues:
            raise MediaError("No speech was recognised in this recording")
        rows = replace_segments(db, document, cues, f"whisper:{settings.WHISPER_MODEL}"[:120])
        db.refresh(document, ["transcript_revision"])
        revision = document.transcript_revision
    document.indexed_transcript_revision = revision
    return [{"text": row.text, "location": {"kind": "recording", "start_seconds": row.start_seconds,
                                             "end_seconds": row.end_seconds}} for row in rows]


def readable_recording(db: Session, document_id, user: User) -> Document:
    document = document_service.get_readable_document(db, document_id, user)
    if not media_service.is_recording(document.file_type):
        raise HTTPException(status_code=400, detail="This material is not a recording")
    return document


def owned_recording(db: Session, document_id, user: User) -> Document:
    document = readable_recording(db, document_id, user)
    if document.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the person who uploaded this recording can change its transcript")
    return document


def transcript_view(db: Session, document: Document, user: User) -> TranscriptResponse:
    indexed = document.processing_status in {"extracted", "chunked", "embedded"}
    return TranscriptResponse(
        document_id=document.id, media_kind=document.media_kind, duration_seconds=document.media_duration_seconds,
        source=document.transcript_source, editable=document.owner_id == user.id,
        stale=indexed and document.transcript_revision != document.indexed_transcript_revision,
        segments=[TranscriptSegmentResponse(id=row.id, position=row.position, start_seconds=row.start_seconds,
                                            end_seconds=row.end_seconds, text=row.text, edited=row.edited_at is not None)
                  for row in segments_of(db, document.id)],
    )


def get_transcript(db: Session, document_id, user: User) -> TranscriptResponse:
    return transcript_view(db, readable_recording(db, document_id, user), user)


def edit_segment(db: Session, document_id, segment_id, text: str, user: User) -> TranscriptResponse:
    document = owned_recording(db, document_id, user)
    segment = db.query(TranscriptSegment).filter(TranscriptSegment.id == segment_id,
                                                 TranscriptSegment.document_id == document.id).first()
    if segment is None:
        raise HTTPException(status_code=404, detail="Transcript line not found")
    if segment.text != text:
        segment.text = text
        segment.edited_at = datetime.now(timezone.utc)
        bump_revision(db, document)
        db.commit()
    db.refresh(document)
    return transcript_view(db, document, user)


def caption_bytes(upload: UploadFile) -> tuple[bytes, str]:
    name = upload.filename or ""
    file_type = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if file_type not in CAPTION_TYPES:
        raise CaptionError("Captions must be a .vtt or .srt file")
    data = upload.file.read(MAX_CAPTION_BYTES + 1)
    return data, file_type


def import_captions(db: Session, document_id, upload: UploadFile, user: User) -> TranscriptResponse:
    from app.services.processing_service import active_job, document_lock

    document = owned_recording(db, document_id, user)
    try:
        data, file_type = caption_bytes(upload)
        cues = parse_captions(data, file_type, document.media_duration_seconds)
    except CaptionError as error:
        raise HTTPException(status_code=400, detail=f"{error}.")
    with document_lock(db.get_bind(), document.id) as acquired:
        if not acquired or active_job(db, document.id):
            raise HTTPException(status_code=409, detail="This recording is being processed; try again when it finishes")
        replace_segments(db, document, cues, "captions")
        db.commit()
    db.refresh(document)
    return transcript_view(db, document, user)


def clear_transcript(db: Session, document_id, user: User) -> None:
    """Remove the transcript so the next processing run transcribes the recording again."""
    from app.services.processing_service import active_job, document_lock

    document = owned_recording(db, document_id, user)
    with document_lock(db.get_bind(), document.id) as acquired:
        if not acquired or active_job(db, document.id):
            raise HTTPException(status_code=409, detail="This recording is being processed; try again when it finishes")
        db.query(TranscriptSegment).filter(TranscriptSegment.document_id == document.id).delete()
        document.transcript_source = None
        bump_revision(db, document)
        db.commit()


# Playback

def media_link(db: Session, document_id, user: User) -> dict:
    """A short-lived, document-scoped playback URL, since media elements cannot send bearer tokens."""
    document = readable_recording(db, document_id, user)
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.MEDIA_LINK_MINUTES)
    token = jwt.encode({"sub": str(user.id), "doc": str(document.id), "typ": "media", "aud": MEDIA_AUDIENCE,
                        "ver": user.token_version or 0, "exp": expires}, settings.SECRET_KEY, algorithm=ALGORITHM)
    # The token travels in the query string, which access logs leave out.
    return {"url": f"/documents/{document.id}/media?token={token}",
            "media_type": media_service.MEDIA_TYPES[document.file_type], "expires_at": expires}


def media_file(db: Session, document_id, token: str):
    denied = HTTPException(status_code=401, detail="This playback link has expired; open the recording again")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM], audience=MEDIA_AUDIENCE,
                             options={"require": ["exp", "sub", "doc", "typ", "aud"]})
        if payload["typ"] != "media" or payload["doc"] != str(document_id):
            raise denied
        user = db.get(User, uuid.UUID(payload["sub"]))
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise denied
    # Signing out (or a password change) revokes playback links too.
    if user is None or payload.get("ver", 0) != (user.token_version or 0):
        raise denied
    # Access is checked again on every request, so removal from a course stops playback.
    document = readable_recording(db, document_id, user)
    path, _ = document_service.original_document_file(db, document.id, user)
    return path, media_service.MEDIA_TYPES[document.file_type]
