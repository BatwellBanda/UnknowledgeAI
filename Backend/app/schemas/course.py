import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CourseCreate(BaseModel):
    code: str = Field(min_length=2, max_length=20, pattern=r"^[A-Z0-9][A-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("code", "title", "description", mode="before")
    @classmethod
    def normalise_strings(cls, value, info):
        if isinstance(value, str):
            value = value.strip()
            return value.upper() if info.field_name == "code" else value
        return value


class CourseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    title: str
    description: str | None
    instructor_id: uuid.UUID
    created_at: datetime
    # Present only for the course instructor or an admin.
    join_code: str | None = None


class EnrollmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime


class EnrollRequest(BaseModel):
    join_code: str = Field(min_length=1, max_length=16)

    @field_validator("join_code", mode="before")
    @classmethod
    def normalise_code(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class RosterEntry(BaseModel):
    user_id: uuid.UUID
    full_name: str
    email: str
    enrolled_at: datetime
