"""A deterministic, explainable study planner.

Each topic gets a priority = need x difficulty weight, where need is 1 - mastery score
(at least MIN_NEED), or NO_EVIDENCE_NEED when a quiz is needed to measure the topic.
Study blocks are filled in date order; each block goes to the topic with the highest
priority / (1 + blocks already given to it), so a weak topic earns more blocks without its
priority being counted again for every extra block. A day repeats a topic only when every
topic already has a block that day. Missed sessions from the last MISSED_LOOKBACK_DAYS are
rescheduled first, once per topic, unless the topic has since been completed or is strong.
The exam date only limits the horizon; it does not change individual priorities.
"""
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models.learning import StudyPlanSettings, StudySession, Topic
from app.models.user import User
from app.schemas.learning import (
    StudyPlanResponse, StudyPlanSettingsResponse, StudyPlanSettingsUpdate, StudySessionResponse, TopicMastery,
)
from app.services import course_service, mastery_service

SESSION_MINUTES = 30
MIN_SESSION_MINUTES = 15
DEFAULT_WEEKDAY_MINUTES = [60, 60, 60, 60, 60, 0, 0]
NO_EXAM_HORIZON_DAYS = 7
MAX_HORIZON_DAYS = 28
MISSED_LOOKBACK_DAYS = 14
NO_EVIDENCE_NEED = 0.5
MIN_NEED = 0.1
DIFFICULTY_WEIGHT = {1: 0.8, 2: 1.0, 3: 1.25}
KIND = {"not_enough_evidence": "quiz", "weak": "revision", "developing": "practice", "strong": "review"}


@dataclass
class PlannedBlock:
    topic_id: uuid.UUID
    session_date: date
    minutes: int
    kind: str
    reason: str


def priority(mastery: TopicMastery, difficulty: int) -> float:
    need = NO_EVIDENCE_NEED if mastery.score is None else max(MIN_NEED, 1 - mastery.score)
    return need * DIFFICULTY_WEIGHT.get(difficulty, 1.0)


def exam_phrase(days_to_exam: int | None) -> str:
    if days_to_exam is None:
        return ""
    return "; the exam is tomorrow" if days_to_exam == 1 else f"; the exam is in {days_to_exam} days"


def reason_for(mastery: TopicMastery, difficulty: int, days_to_exam: int | None) -> str:
    name = mastery.topic_name
    percent = None if mastery.score is None else round(mastery.score * 100)
    if mastery.level == "not_enough_evidence":
        plural = "" if mastery.answers == 1 else "s"
        text = f"Not enough evidence for {name} yet ({mastery.answers} answer{plural}); a short quiz will measure it"
    elif mastery.level == "weak":
        recent = mastery.recent_quiz_scores
        if len(recent) >= 2 and all(score < mastery_service.WEAK_BELOW for score in recent[:2]):
            text = f"{name} needs revision after your last two quizzes"
        else:
            text = f"{name} is a weak topic ({percent}% across {mastery.answers} recent answers)"
    elif mastery.level == "developing":
        text = f"{name} is developing ({percent}%); practice will consolidate it"
    else:
        text = f"{name} is strong ({percent}%); a short review keeps it fresh"
    if difficulty == 3:
        text += "; you marked it as difficult"
    return text + exam_phrase(days_to_exam) + "."


def build_plan(today: date, exam_date: date | None, weekday_minutes: list[int], difficulty: dict,
               masteries: list[TopicMastery], carry_over: list[tuple[uuid.UUID, date]] = (),
               used_minutes_today: int = 0) -> tuple[list[PlannedBlock], list[str]]:
    notes = []
    if not masteries:
        return [], ["This course has no topics yet, so there is nothing to plan."]
    days_to_exam = None
    if exam_date is not None and exam_date > today:
        days_to_exam = (exam_date - today).days
        last_day = min(exam_date - timedelta(days=1), today + timedelta(days=MAX_HORIZON_DAYS - 1))
    elif exam_date is not None and exam_date == today:
        return [], ["Your exam is today, so no further sessions are planned. Good luck."]
    else:
        if exam_date is not None:
            notes.append("Your exam date has passed; set a new date to plan towards it.")
        last_day = today + timedelta(days=NO_EXAM_HORIZON_DAYS - 1)

    slots = []
    day = today
    while day <= last_day:
        capacity = weekday_minutes[day.weekday()] - (used_minutes_today if day == today else 0)
        full, remainder = divmod(max(0, capacity), SESSION_MINUTES)
        slots += [(day, SESSION_MINUTES)] * full
        if remainder >= MIN_SESSION_MINUTES:
            slots.append((day, remainder))
        day += timedelta(days=1)
    if not slots:
        return [], notes + ["No study time is available in this period; add some availability."]

    by_topic = {m.topic_id: m for m in masteries}
    priorities = {m.topic_id: priority(m, difficulty.get(m.topic_id, 2)) for m in masteries}
    ordered = sorted(masteries, key=lambda m: (m.topic_name.casefold(), str(m.topic_id)))
    carry = []
    for topic_id, missed_on in sorted(carry_over, key=lambda item: item[1]):
        if topic_id in by_topic and by_topic[topic_id].level != "strong" and topic_id not in {t for t, _ in carry}:
            carry.append((topic_id, missed_on))

    assigned = defaultdict(int)
    on_day = defaultdict(set)
    blocks = []
    for session_date, minutes in slots:
        used_today = on_day[session_date]
        rescheduled = next(((t, d) for t, d in carry if t not in used_today), None)
        if rescheduled:
            carry.remove(rescheduled)
            topic_id, missed_on = rescheduled
            mastery = by_topic[topic_id]
            reason = f"Rescheduled from your missed session on {missed_on:%a %d %b}. " + reason_for(mastery, difficulty.get(topic_id, 2), days_to_exam)
        else:
            fresh = [m for m in ordered if m.topic_id not in used_today] or ordered
            # max() keeps the first of equal scores, so ties follow the alphabetical order.
            mastery = max(fresh, key=lambda m: priorities[m.topic_id] / (1 + assigned[m.topic_id]))
            topic_id = mastery.topic_id
            reason = reason_for(mastery, difficulty.get(topic_id, 2), days_to_exam)
        assigned[topic_id] += 1
        used_today.add(topic_id)
        blocks.append(PlannedBlock(topic_id, session_date, minutes, KIND[mastery.level], reason[:400]))
    return blocks, notes


# Persistence

def server_today() -> date:
    return datetime.now(timezone.utc).date()


def resolve_today(requested: date | None) -> date:
    """Accept the student's local date, within a day of the server's UTC date."""
    today = server_today()
    if requested is None:
        return today
    if abs((requested - today).days) > 1:
        raise HTTPException(status_code=400, detail="The supplied date is too far from the current date")
    return requested


def settings_view(row: StudyPlanSettings | None) -> StudyPlanSettingsResponse:
    if row is None:
        return StudyPlanSettingsResponse(exam_date=None, weekday_minutes=DEFAULT_WEEKDAY_MINUTES, topic_difficulty={}, saved=False)
    return StudyPlanSettingsResponse(exam_date=row.exam_date, weekday_minutes=row.weekday_minutes,
        topic_difficulty={uuid.UUID(k): v for k, v in (row.topic_difficulty or {}).items()}, saved=True)


def get_settings(db: Session, course_id, user: User) -> StudyPlanSettings | None:
    return db.query(StudyPlanSettings).filter(StudyPlanSettings.user_id == user.id, StudyPlanSettings.course_id == course_id).first()


def save_settings(db: Session, course_id, data: StudyPlanSettingsUpdate, user: User, today: date | None = None) -> StudyPlanResponse:
    course_service.require_course_membership(db, course_id, user)
    today = resolve_today(today)
    if data.exam_date is not None and data.exam_date < today:
        raise HTTPException(status_code=400, detail="The exam date cannot be in the past")
    topic_ids = {topic_id for (topic_id,) in db.query(Topic.id).filter(Topic.course_id == course_id)}
    if set(data.topic_difficulty) - topic_ids:
        raise HTTPException(status_code=400, detail="Difficulty ratings must refer to this course's topics")
    row = get_settings(db, course_id, user)
    if row is None:
        row = StudyPlanSettings(user_id=user.id, course_id=course_id)
        db.add(row)
    row.exam_date = data.exam_date
    row.weekday_minutes = data.weekday_minutes
    row.topic_difficulty = {str(k): v for k, v in data.topic_difficulty.items()}
    db.flush()
    return rebuild(db, course_id, user, today)


def rebuild(db: Session, course_id, user: User, today: date | None = None) -> StudyPlanResponse:
    """Mark past unfinished sessions missed, then replan every unfinished session from today."""
    course_service.require_course_membership(db, course_id, user)
    today = resolve_today(today)
    mine = db.query(StudySession).filter(StudySession.user_id == user.id, StudySession.course_id == course_id)
    mine.filter(StudySession.status == "planned", StudySession.session_date < today).update({"status": "missed"}, synchronize_session=False)
    used_today = mine.filter(StudySession.status == "completed", StudySession.session_date == today).with_entities(
        func.coalesce(func.sum(StudySession.minutes), 0)).scalar()
    mine.filter(StudySession.status == "planned", StudySession.session_date >= today).delete(synchronize_session=False)

    row = get_settings(db, course_id, user)
    settings = settings_view(row)
    masteries = mastery_service.topic_mastery(db, course_id, user)
    lookback = today - timedelta(days=MISSED_LOOKBACK_DAYS)
    history = mine.filter(StudySession.session_date >= lookback, StudySession.status.in_(["missed", "completed"])).all()
    carry = [(s.topic_id, s.session_date) for s in history if s.status == "missed" and not any(
        done.status == "completed" and done.topic_id == s.topic_id and done.session_date >= s.session_date for done in history)]
    blocks, notes = build_plan(today, settings.exam_date, settings.weekday_minutes, settings.topic_difficulty,
                               masteries, carry, used_today)
    for block in blocks:
        db.add(StudySession(user_id=user.id, course_id=course_id, topic_id=block.topic_id, session_date=block.session_date,
                            minutes=block.minutes, kind=block.kind, reason=block.reason, status="planned"))
    db.commit()
    return plan_view(db, course_id, user, today, settings, masteries, notes)


def plan_view(db: Session, course_id, user: User, today: date, settings=None, masteries=None, notes=None) -> StudyPlanResponse:
    if settings is None:
        settings = settings_view(get_settings(db, course_id, user))
    if masteries is None:
        masteries = mastery_service.topic_mastery(db, course_id, user)
    rows = db.query(StudySession, Topic.name).join(Topic, Topic.id == StudySession.topic_id).filter(
        StudySession.user_id == user.id, StudySession.course_id == course_id,
        StudySession.session_date >= today - timedelta(days=MISSED_LOOKBACK_DAYS),
    ).order_by(StudySession.session_date, StudySession.created_at, StudySession.id).all()
    sessions = [StudySessionResponse(id=s.id, topic_id=s.topic_id, topic_name=name, session_date=s.session_date,
        minutes=s.minutes, kind=s.kind, reason=s.reason, status=s.status, completed_at=s.completed_at) for s, name in rows]
    return StudyPlanResponse(course_id=course_id, today=today, settings=settings, mastery=masteries, sessions=sessions, notes=notes or [])


def get_plan(db: Session, course_id, user: User, today: date | None = None) -> StudyPlanResponse:
    course_service.require_course_membership(db, course_id, user)
    return plan_view(db, course_id, user, resolve_today(today))


def complete_session(db: Session, session_id, user: User) -> StudySessionResponse:
    session = db.query(StudySession).filter(StudySession.id == session_id, StudySession.user_id == user.id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Study session not found")
    if session.status == "completed":
        raise HTTPException(status_code=409, detail="This session is already complete")
    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)
    db.commit()
    topic = db.get(Topic, session.topic_id)
    return StudySessionResponse(id=session.id, topic_id=session.topic_id, topic_name=topic.name, session_date=session.session_date,
        minutes=session.minutes, kind=session.kind, reason=session.reason, status=session.status, completed_at=session.completed_at)
