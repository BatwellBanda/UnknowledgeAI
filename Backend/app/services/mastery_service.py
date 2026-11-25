"""Explainable topic mastery from a student's submitted quiz answers.

score = sum(weight * correct) / sum(weight), weight = 0.5 ** (age_days / HALF_LIFE_DAYS),
over the topic's most recent MAX_ANSWERS answers. Skipped questions count as incorrect.
Below MIN_EVIDENCE answers the topic is reported as "not enough evidence", never as weak.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.learning import QuizAttempt, QuizAttemptAnswer, Topic
from app.models.user import User
from app.schemas.learning import TopicMastery

HALF_LIFE_DAYS = 14
MIN_EVIDENCE = 4
MAX_ANSWERS = 20
WEAK_BELOW = 0.6
STRONG_FROM = 0.8
RECENT_QUIZZES = 2


@dataclass
class Evidence:
    answered_at: datetime
    correct: bool
    attempt_id: object


@dataclass
class Assessment:
    level: str
    score: float | None
    answers: int
    correct: int
    last_practised_at: datetime | None
    recent_quiz_scores: list[float] = field(default_factory=list)


def as_utc(moment: datetime) -> datetime:
    # SQLite returns naive timestamps; every stored timestamp is UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def assess(evidence: list[Evidence], now: datetime) -> Assessment:
    recent = sorted(evidence, key=lambda e: as_utc(e.answered_at), reverse=True)[:MAX_ANSWERS]
    correct = sum(e.correct for e in recent)
    last = as_utc(recent[0].answered_at) if recent else None
    by_attempt: dict = {}
    for e in recent:
        by_attempt.setdefault(e.attempt_id, []).append(e.correct)
    quiz_scores = [round(sum(marks) / len(marks), 3) for marks in list(by_attempt.values())[:RECENT_QUIZZES]]
    if len(recent) < MIN_EVIDENCE:
        return Assessment("not_enough_evidence", None, len(recent), correct, last, quiz_scores)
    weights = [0.5 ** (max(0.0, (now - as_utc(e.answered_at)).total_seconds() / 86400) / HALF_LIFE_DAYS) for e in recent]
    score = sum(w for w, e in zip(weights, recent) if e.correct) / sum(weights)
    level = "weak" if score < WEAK_BELOW else "strong" if score >= STRONG_FROM else "developing"
    return Assessment(level, round(score, 3), len(recent), correct, last, quiz_scores)


def topic_mastery(db: Session, course_id, user: User, now: datetime | None = None) -> list[TopicMastery]:
    now = now or datetime.now(timezone.utc)
    topics = db.query(Topic).filter(Topic.course_id == course_id).order_by(Topic.name, Topic.id).all()
    rows = db.query(QuizAttemptAnswer.topic_id, QuizAttemptAnswer.is_correct, QuizAttemptAnswer.answered_at, QuizAttempt.id).join(
        QuizAttempt, QuizAttempt.id == QuizAttemptAnswer.attempt_id,
    ).filter(
        QuizAttempt.user_id == user.id, QuizAttempt.course_id == course_id, QuizAttempt.status == "submitted",
        QuizAttemptAnswer.topic_id.is_not(None), QuizAttemptAnswer.answered_at.is_not(None),
    ).all()
    grouped = defaultdict(list)
    for topic_id, is_correct, answered_at, attempt_id in rows:
        grouped[topic_id].append(Evidence(answered_at, bool(is_correct), attempt_id))
    result = []
    for topic in topics:
        a = assess(grouped.get(topic.id, []), now)
        result.append(TopicMastery(topic_id=topic.id, topic_name=topic.name, level=a.level, score=a.score,
            answers=a.answers, correct=a.correct, last_practised_at=a.last_practised_at, recent_quiz_scores=a.recent_quiz_scores))
    return result
