import uuid
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.schemas.source import SourceLocation

MasteryLevel = Literal["not_enough_evidence", "weak", "developing", "strong"]


def _strip(value):
    return value.strip() if isinstance(value, str) else value


# Topics

class TopicCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim(cls, value):
        return _strip(value)


class TopicUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim(cls, value):
        return _strip(value)


class TopicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    course_id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime


# Questions

class QuestionContent(BaseModel):
    stem: str = Field(min_length=5, max_length=1000)
    options: list[str] = Field(min_length=2, max_length=6)
    correct_index: int = Field(ge=0, le=5)
    explanation: str = Field(min_length=5, max_length=2000)

    @field_validator("stem", "explanation", mode="before")
    @classmethod
    def trim(cls, value):
        return _strip(value)

    @field_validator("options", mode="before")
    @classmethod
    def trim_options(cls, value):
        return [_strip(option) for option in value] if isinstance(value, list) else value

    @model_validator(mode="after")
    def check_options(self):
        if any(not option or len(option) > 300 for option in self.options):
            raise ValueError("Each option needs 1-300 characters")
        if len({" ".join(option.casefold().split()) for option in self.options}) != len(self.options):
            raise ValueError("Options must be different from each other")
        if self.correct_index >= len(self.options):
            raise ValueError("correct_index must refer to one of the options")
        return self


class QuestionCreate(QuestionContent):
    topic_id: uuid.UUID
    chunk_id: uuid.UUID
    source_quote: str = Field(min_length=3, max_length=2000)


class QuestionUpdate(BaseModel):
    topic_id: uuid.UUID | None = None
    stem: str | None = None
    options: list[str] | None = None
    correct_index: int | None = None
    explanation: str | None = None
    reviewed: bool | None = None


class QuestionGenerateRequest(BaseModel):
    topic_id: uuid.UUID
    document_id: uuid.UUID | None = None
    count: int = Field(default=3, ge=1, le=5)


class QuestionResponse(BaseModel):
    """The author's view, including the answer key; never returned to other students."""
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    owner_id: uuid.UUID
    course_id: uuid.UUID
    topic_id: uuid.UUID
    document_id: uuid.UUID
    chunk_id: uuid.UUID | None
    source_location: SourceLocation | None
    source_quote: str
    stem: str
    options: list[str]
    correct_index: int
    explanation: str
    origin: Literal["manual", "generated"]
    reviewed: bool
    visibility: Literal["private", "course"]
    created_at: datetime


class QuestionGenerateResponse(BaseModel):
    created: list[QuestionResponse]
    rejected: int
    status: Literal["generated", "no_searchable_materials", "no_matches"]


class ModelQuestion(BaseModel):
    """Structured output requested from the language model for one passage."""
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "insufficient"]
    question: str = Field(default="", max_length=1000)
    options: list[str] = Field(default_factory=list, max_length=4)
    correct_option: int = Field(default=0, ge=0, le=3)
    explanation: str = Field(default="", max_length=2000)
    quote: str = Field(default="", max_length=2000)


# Quizzes

class QuizCreate(BaseModel):
    course_id: uuid.UUID
    topic_id: uuid.UUID | None = None
    size: int = Field(default=5, ge=1, le=20)


class QuizSubmission(BaseModel):
    # One entry per position; null or omitted means the question was skipped.
    answers: dict[int, int | None] = Field(max_length=20)


class SourceReference(BaseModel):
    document_id: uuid.UUID
    document_name: str
    source_location: SourceLocation | None
    quote: str


class QuizItem(BaseModel):
    position: int
    topic_id: uuid.UUID | None
    topic_name: str | None
    stem: str
    options: list[str]
    chosen_index: int | None = None
    # The fields below are present only after submission.
    correct_index: int | None = None
    is_correct: bool | None = None
    explanation: str | None = None
    source: SourceReference | None = None
    source_unavailable: bool = False


class QuizResponse(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    topic_id: uuid.UUID | None
    status: Literal["in_progress", "submitted"]
    total_count: int
    correct_count: int | None
    created_at: datetime
    submitted_at: datetime | None
    items: list[QuizItem]


class QuizSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    course_id: uuid.UUID
    topic_id: uuid.UUID | None
    status: Literal["in_progress", "submitted"]
    total_count: int
    correct_count: int | None
    created_at: datetime
    submitted_at: datetime | None


# Mastery and plans

class TopicMastery(BaseModel):
    topic_id: uuid.UUID
    topic_name: str
    level: MasteryLevel
    score: float | None = Field(description="Recency-weighted share of correct answers; null without enough evidence")
    answers: int
    correct: int
    last_practised_at: datetime | None
    recent_quiz_scores: list[float] = Field(description="Topic accuracy in the most recent quizzes, newest first")


class StudyPlanSettingsUpdate(BaseModel):
    exam_date: date | None = None
    weekday_minutes: list[int] = Field(min_length=7, max_length=7)
    topic_difficulty: dict[uuid.UUID, int] = Field(default_factory=dict, max_length=200)

    @field_validator("weekday_minutes")
    @classmethod
    def check_minutes(cls, value):
        if any(minutes < 0 or minutes > 600 for minutes in value):
            raise ValueError("Daily availability must be 0-600 minutes")
        return value

    @field_validator("topic_difficulty")
    @classmethod
    def check_difficulty(cls, value):
        if any(level not in (1, 2, 3) for level in value.values()):
            raise ValueError("Difficulty must be 1 (easy), 2 (normal) or 3 (hard)")
        return value


class StudyPlanSettingsResponse(BaseModel):
    exam_date: date | None
    weekday_minutes: list[int]
    topic_difficulty: dict[uuid.UUID, int]
    saved: bool


class StudySessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    topic_id: uuid.UUID
    topic_name: str
    session_date: date
    minutes: int
    kind: Literal["quiz", "revision", "practice", "review"]
    reason: str
    status: Literal["planned", "completed", "missed"]
    completed_at: datetime | None


class StudyPlanResponse(BaseModel):
    course_id: uuid.UUID
    today: date
    settings: StudyPlanSettingsResponse
    mastery: list[TopicMastery]
    sessions: list[StudySessionResponse]
    notes: list[str]
