import uuid
from datetime import datetime
from pydantic import BaseModel
from app.schemas.source import SourceLocation

class ChunkResponse(BaseModel):
    id: uuid.UUID
    chunk_index: int
    content: str
    source_location: SourceLocation | None = None
    created_at: datetime

    class Config:
        from_attributes = True
