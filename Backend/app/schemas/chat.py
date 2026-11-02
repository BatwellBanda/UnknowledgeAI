import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.search import SearchRequest
from app.schemas.source import SourceLocation


class ChatRequest(SearchRequest):
    pass


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=10)
    quote: str = Field(min_length=1, max_length=2000)


class AnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence] = Field(min_length=1, max_length=5)


class ModelAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "not_found"]
    claims: list[AnswerClaim] = Field(max_length=10)

    @model_validator(mode="after")
    def require_consistent_status(self):
        if self.status == "answered" and not self.claims:
            raise ValueError("An answer needs evidence-backed claims")
        if self.status == "not_found" and self.claims:
            raise ValueError("An abstention cannot contain claims")
        return self


class Citation(BaseModel):
    source_id: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    source_location: SourceLocation | None
    quote: str


class ChatResponse(BaseModel):
    status: Literal["answered", "not_found", "no_searchable_materials"]
    answer: str
    citations: list[Citation]
