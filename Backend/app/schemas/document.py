import uuid
from datetime import datetime
from pydantic import BaseModel

class DocumentResponse(BaseModel):
    id: uuid.UUID
    original_filename: str
    file_type: str
    file_size_bytes: int
    processing_status: str
    created_at: datetime

    class Config:
        from_attributes = True