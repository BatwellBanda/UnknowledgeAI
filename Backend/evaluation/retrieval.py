"""Dataset validation and retrieval scoring, independent of the database/model."""
import math
import statistics
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Material(Record):
    id: str = Field(min_length=1)
    course: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


class Evidence(Record):
    document: str
    quote: str = Field(min_length=1)


class Question(Record):
    id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2000)
    course: str | None = None
    kind: Literal["answerable", "unanswerable"] = "answerable"
    evidence: list[Evidence] = Field(default_factory=list)


class Dataset(Record):
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    split: Literal["development", "held_out"]
    materials: list[Material] = Field(min_length=1)
    questions: list[Question] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self):
        materials = {m.id: m for m in self.materials}
        if len(materials) != len(self.materials):
            raise ValueError("Material IDs must be unique")
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("Question IDs must be unique")
        courses = {m.course for m in self.materials}
        if not any(q.kind == "answerable" for q in self.questions):
            raise ValueError("At least one answerable question is required")
        for question in self.questions:
            if question.course is not None and question.course not in courses:
                raise ValueError(f"Unknown course in question {question.id}")
            if bool(question.evidence) != (question.kind == "answerable"):
                raise ValueError(f"Evidence does not match question kind: {question.id}")
            for evidence in question.evidence:
                material = materials.get(evidence.document)
                if material is None or evidence.quote not in material.text:
                    raise ValueError(f"Evidence is missing from its material: {question.id}")
                if question.course is not None and material.course != question.course:
                    raise ValueError(f"Evidence is outside the question course: {question.id}")
        return self


def first_relevant_rank(question: Question, results: list[dict], document_ids: dict[str, str]):
    """A hit needs both the right document and its labelled evidence passage."""
    for rank, result in enumerate(results, 1):
        for evidence in question.evidence:
            if (result["document_id"] == document_ids[evidence.document]
                    and " ".join(evidence.quote.split()) in " ".join(result["text"].split())):
                return rank
    return None


def summarise(rows: list[dict]):
    scored = [row for row in rows if row["kind"] == "answerable"]
    if not scored:
        raise ValueError("Cannot score an empty set of answerable questions")
    latencies = sorted(row["latency_ms"] for row in rows)
    ranks = [row["first_relevant_rank"] for row in scored]
    return {
        "answerable_questions": len(scored),
        "unanswerable_probes": len(rows) - len(scored),
        "hit_rate_at_1": sum(rank == 1 for rank in ranks) / len(scored),
        "hit_rate_at_5": sum(rank is not None and rank <= 5 for rank in ranks) / len(scored),
        "mrr_at_5": sum(1 / rank if rank is not None and rank <= 5 else 0 for rank in ranks) / len(scored),
        "query_latency_ms": {"median": statistics.median(latencies),
                             "p95": latencies[math.ceil(len(latencies) * .95) - 1]},
        "missed_question_ids": [row["id"] for row in scored if row["first_relevant_rank"] is None],
    }
