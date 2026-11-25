import uuid
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from app.schemas.source import SourceLocation


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    course_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    min_similarity: float | None = Field(default=None, ge=-1, le=1)

    @field_validator("query", mode="before")
    @classmethod
    def trim_query(cls, value):
        return value.strip() if isinstance(value, str) else value


class SearchResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    course_id: uuid.UUID | None
    document_name: str
    chunk_index: int
    text: str
    similarity: float = Field(ge=-1, le=1)
    source_location: SourceLocation | None


class SearchResponse(BaseModel):
    query: str
    status: Literal["matches", "no_matches", "no_searchable_materials"]
    results: list[SearchResult]
