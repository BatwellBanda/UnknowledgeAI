import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel

class DocumentResponse(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    course_id: uuid.UUID | None = None
    visibility: Literal["private", "course"] = "private"
    original_filename: str
    file_type: str
    file_size_bytes: int
    processing_status: str
    created_at: datetime
    media_kind: Literal["audio", "video"] | None = None
    media_duration_seconds: float | None = None

    class Config:
        from_attributes = True


class DocumentTextResponse(BaseModel):
    document_id: uuid.UUID
    text: str
