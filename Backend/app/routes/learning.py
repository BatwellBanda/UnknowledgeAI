import uuid
from datetime import date
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session
from app.core.rate_limit import limit_by_user
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.learning import (
    QuestionCreate, QuestionGenerateRequest, QuestionGenerateResponse, QuestionResponse, QuestionUpdate,
    QuizCreate, QuizResponse, QuizSubmission, QuizSummary, StudyPlanResponse, StudyPlanSettingsUpdate,
    StudySessionResponse, TopicCreate, TopicMastery, TopicResponse, TopicUpdate,
)
from app.services import mastery_service, planner_service, question_service, quiz_service, course_service
from database.connection import get_db

topics = APIRouter(tags=["Topics"])
questions = APIRouter(prefix="/questions", tags=["Practice questions"])
quizzes = APIRouter(prefix="/quizzes", tags=["Quizzes"])
plans = APIRouter(tags=["Mastery and study plan"])
LocalDate = Query(None, description="The student's local date; must be within a day of the server date")


@topics.get("/courses/{course_id}/topics", response_model=list[TopicResponse])
def list_topics(course_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return question_service.list_topics(db, course_id, user)


@topics.post("/courses/{course_id}/topics", response_model=TopicResponse, status_code=201)
def create_topic(course_id: uuid.UUID, data: TopicCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return question_service.create_topic(db, course_id, data, user)


@topics.patch("/topics/{topic_id}", response_model=TopicResponse)
def update_topic(topic_id: uuid.UUID, data: TopicUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return question_service.update_topic(db, topic_id, data, user)


@questions.get("", response_model=list[QuestionResponse])
def list_questions(
    course_id: uuid.UUID | None = None, topic_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """Questions you authored, with their answer keys."""
    return question_service.list_own_questions(db, user, course_id, topic_id, offset, limit)


@questions.post("", response_model=QuestionResponse, status_code=201)
def create_question(data: QuestionCreate, db: Session = Depends(get_db), user: User = Depends(limit_by_user("question_create", 120, 3600))):
    return question_service.create_question(db, data, user)


@questions.post("/generate", response_model=QuestionGenerateResponse)
def generate_questions(data: QuestionGenerateRequest, db: Session = Depends(get_db), user: User = Depends(limit_by_user("question_generate", 10, 3600))):
    return question_service.generate_questions(db, data, user)


@questions.patch("/{question_id}", response_model=QuestionResponse)
def update_question(question_id: uuid.UUID, data: QuestionUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return question_service.update_question(db, question_id, data, user)


@questions.delete("/{question_id}", status_code=204)
def delete_question(question_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    question_service.delete_question(db, question_id, user)
    return Response(status_code=204)


@questions.post("/{question_id}/publish", response_model=QuestionResponse)
def publish_question(question_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return question_service.set_published(db, question_id, user, True)


@questions.delete("/{question_id}/publish", response_model=QuestionResponse)
def unpublish_question(question_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return question_service.set_published(db, question_id, user, False)


@quizzes.post("", response_model=QuizResponse, status_code=201)
def create_quiz(data: QuizCreate, db: Session = Depends(get_db), user: User = Depends(limit_by_user("quiz_create", 60, 3600))):
    return quiz_service.create_quiz(db, data, user)


@quizzes.get("", response_model=list[QuizSummary])
def list_quizzes(
    course_id: uuid.UUID | None = None, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    return quiz_service.list_quizzes(db, user, course_id, offset, limit)


@quizzes.get("/{attempt_id}", response_model=QuizResponse)
def get_quiz(attempt_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return quiz_service.get_quiz(db, attempt_id, user)


@quizzes.post("/{attempt_id}/submit", response_model=QuizResponse)
def submit_quiz(attempt_id: uuid.UUID, data: QuizSubmission, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return quiz_service.submit_quiz(db, attempt_id, data, user)


@plans.get("/courses/{course_id}/mastery", response_model=list[TopicMastery])
def mastery(course_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    course_service.require_course_membership(db, course_id, user)
    return mastery_service.topic_mastery(db, course_id, user)


@plans.get("/courses/{course_id}/study-plan", response_model=StudyPlanResponse)
def study_plan(course_id: uuid.UUID, today: date | None = LocalDate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return planner_service.get_plan(db, course_id, user, today)


@plans.post("/courses/{course_id}/study-plan/rebuild", response_model=StudyPlanResponse)
def rebuild_plan(course_id: uuid.UUID, today: date | None = LocalDate, db: Session = Depends(get_db), user: User = Depends(limit_by_user("plan_rebuild", 60, 3600))):
    return planner_service.rebuild(db, course_id, user, today)


@plans.put("/courses/{course_id}/study-plan/settings", response_model=StudyPlanResponse)
def save_plan_settings(course_id: uuid.UUID, data: StudyPlanSettingsUpdate, today: date | None = LocalDate, db: Session = Depends(get_db), user: User = Depends(limit_by_user("plan_rebuild", 60, 3600))):
    return planner_service.save_settings(db, course_id, data, user, today)


@plans.post("/study-sessions/{session_id}/complete", response_model=StudySessionResponse)
def complete_session(session_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return planner_service.complete_session(db, session_id, user)
