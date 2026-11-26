import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _trimmed(value):
    return value.strip() if isinstance(value, str) else value


class TranscriptSegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    start_seconds: float
    end_seconds: float
    text: str
    edited: bool


class TranscriptResponse(BaseModel):
    document_id: uuid.UUID
    media_kind: Literal["audio", "video"] | None
    duration_seconds: float | None
    source: str | None
    # True when the transcript changed after the recording was last made searchable.
    stale: bool
    editable: bool
    segments: list[TranscriptSegmentResponse]


class SegmentUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text", mode="before")
    @classmethod
    def trim(cls, value):
        return _trimmed(value)


class MediaLinkResponse(BaseModel):
    url: str
    media_type: str
    expires_at: datetime


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    timestamp_seconds: float | None = Field(default=None, ge=0)

    @field_validator("body", mode="before")
    @classmethod
    def trim(cls, value):
        return _trimmed(value)


class NoteUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=5000)
    timestamp_seconds: float | None = Field(default=None, ge=0)

    @field_validator("body", mode="before")
    @classmethod
    def trim(cls, value):
        return _trimmed(value)


class NoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    timestamp_seconds: float | None
    body: str
    created_at: datetime
    updated_at: datetime
