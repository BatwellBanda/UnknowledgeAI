from typing import Literal
from pydantic import BaseModel, Field


class SourceLocation(BaseModel):
    kind: Literal["pdf_page", "pptx_slide", "docx_paragraph", "docx_table", "txt_lines", "unknown"]
    page: int | None = Field(default=None, ge=1)
    slide: int | None = Field(default=None, ge=1)
    paragraph: int | None = Field(default=None, ge=1)
    table: int | None = Field(default=None, ge=1)
    section: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
