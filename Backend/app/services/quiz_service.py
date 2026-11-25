"""Adaptive quizzes over readable, source-backed questions."""
import random
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.document import Document
from app.models.learning import QuizAttempt, QuizAttemptAnswer, QuizQuestion, Topic
from app.models.user import User
from app.schemas.learning import QuizCreate, QuizItem, QuizResponse, QuizSubmission, SourceReference
from app.services import course_service, mastery_service, planner_service, question_service

MAX_CANDIDATES = 500


def selection_order(questions, history: dict, topic_scores: dict, rng: random.Random):
    """Previously missed questions first, then unseen ones, then the least recently answered correct ones.

    Within each group, questions from weaker topics come first. Random order breaks ties.
    """
    def key(question):
        last = history.get(question.id)
        group = 1 if last is None else 0 if not last[1] else 2
        answered = mastery_service.as_utc(last[0]).timestamp() if last else 0
        return group, topic_scores.get(question.topic_id, mastery_service.WEAK_BELOW), answered
    shuffled = list(questions)
    rng.shuffle(shuffled)
    return sorted(shuffled, key=key)


def create_quiz(db: Session, data: QuizCreate, user: User) -> QuizResponse:
    course_service.require_course_membership(db, data.course_id, user)
    candidates = question_service.readable_questions(db, user).filter(QuizQuestion.course_id == data.course_id)
    if data.topic_id is not None:
        topic = db.get(Topic, data.topic_id)
        if topic is None or topic.course_id != data.course_id:
            raise HTTPException(status_code=404, detail="Topic not found in this course")
        candidates = candidates.filter(QuizQuestion.topic_id == data.topic_id)
    # Only questions whose supporting passage still exists are asked.
    questions = [q for q in candidates.limit(MAX_CANDIDATES).all() if question_service.reanchor(db, q)]
    if not questions:
        raise HTTPException(status_code=409, detail="There are no practice questions here yet; create or generate some first")
    rows = db.query(QuizAttemptAnswer.question_id, QuizAttemptAnswer.answered_at, QuizAttemptAnswer.is_correct).join(
        QuizAttempt, QuizAttempt.id == QuizAttemptAnswer.attempt_id,
    ).filter(QuizAttempt.user_id == user.id, QuizAttempt.status == "submitted",
             QuizAttemptAnswer.question_id.in_([q.id for q in questions])).all()
    history = {}
    for question_id, answered_at, is_correct in rows:
        if question_id not in history or mastery_service.as_utc(answered_at) > mastery_service.as_utc(history[question_id][0]):
            history[question_id] = (answered_at, bool(is_correct))
    scores = {m.topic_id: m.score for m in mastery_service.topic_mastery(db, data.course_id, user) if m.score is not None}
    rng = random.Random()
    chosen = selection_order(questions, history, scores, rng)[:data.size]
    rng.shuffle(chosen)
    attempt = QuizAttempt(user_id=user.id, course_id=data.course_id, topic_id=data.topic_id, total_count=len(chosen))
    for position, q in enumerate(chosen):
        attempt.answers.append(QuizAttemptAnswer(
            position=position, question_id=q.id, topic_id=q.topic_id, stem=q.stem, options=list(q.options),
            correct_index=q.correct_index, explanation=q.explanation, source_quote=q.source_quote, source_location=q.source_location,
        ))
    db.add(attempt)
    db.commit()
    return quiz_view(db, attempt, user)


def own_attempt(db: Session, attempt_id, user: User) -> QuizAttempt:
    attempt = db.query(QuizAttempt).filter(QuizAttempt.id == attempt_id, QuizAttempt.user_id == user.id).first()
    if attempt is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return attempt


def quiz_view(db: Session, attempt: QuizAttempt, user: User) -> QuizResponse:
    topic_ids = {a.topic_id for a in attempt.answers if a.topic_id}
    names = dict(db.query(Topic.id, Topic.name).filter(Topic.id.in_(topic_ids)).all()) if topic_ids else {}
    submitted = attempt.status == "submitted"
    sources = {}
    if submitted:
        # Explanations and passages are shown only while the student can still read the source.
        question_ids = [a.question_id for a in attempt.answers if a.question_id]
        rows = question_service.readable_questions(db, user).filter(QuizQuestion.id.in_(question_ids)).join(
            Document, Document.id == QuizQuestion.document_id).with_entities(QuizQuestion.id, Document.id, Document.original_filename).all()
        sources = {question_id: (document_id, name) for question_id, document_id, name in rows}
    items = []
    for a in attempt.answers:
        item = QuizItem(position=a.position, topic_id=a.topic_id, topic_name=names.get(a.topic_id), stem=a.stem,
                        options=a.options, chosen_index=a.chosen_index)
        if submitted:
            item.correct_index = a.correct_index
            item.is_correct = bool(a.is_correct)
            if a.question_id in sources:
                document_id, name = sources[a.question_id]
                item.explanation = a.explanation
                item.source = SourceReference(document_id=document_id, document_name=name,
                                              source_location=a.source_location, quote=a.source_quote)
            else:
                item.source_unavailable = True
        items.append(item)
    return QuizResponse(id=attempt.id, course_id=attempt.course_id, topic_id=attempt.topic_id, status=attempt.status,
        total_count=attempt.total_count, correct_count=attempt.correct_count, created_at=attempt.created_at,
        submitted_at=attempt.submitted_at, items=items)


def get_quiz(db: Session, attempt_id, user: User) -> QuizResponse:
    return quiz_view(db, own_attempt(db, attempt_id, user), user)


def list_quizzes(db: Session, user: User, course_id=None, offset=0, limit=20):
    query = db.query(QuizAttempt).filter(QuizAttempt.user_id == user.id)
    if course_id is not None:
        query = query.filter(QuizAttempt.course_id == course_id)
    return query.order_by(QuizAttempt.created_at.desc(), QuizAttempt.id).offset(offset).limit(limit).all()


def submit_quiz(db: Session, attempt_id, data: QuizSubmission, user: User) -> QuizResponse:
    attempt = own_attempt(db, attempt_id, user)
    by_position = {a.position: a for a in attempt.answers}
    unknown = set(data.answers) - set(by_position)
    if unknown:
        raise HTTPException(status_code=422, detail="Answers refer to questions that are not in this quiz")
    for position, choice in data.answers.items():
        if choice is not None and not 0 <= choice < len(by_position[position].options):
            raise HTTPException(status_code=422, detail="An answer refers to an option that does not exist")
    # A conditional update makes a second, concurrent submission fail instead of regrading.
    claimed = db.query(QuizAttempt).filter(QuizAttempt.id == attempt.id, QuizAttempt.status == "in_progress").update(
        {"status": "submitted"}, synchronize_session=False)
    if not claimed:
        db.rollback()
        raise HTTPException(status_code=409, detail="This quiz has already been submitted")
    now = datetime.now(timezone.utc)
    correct = 0
    for answer in attempt.answers:
        answer.chosen_index = data.answers.get(answer.position)
        answer.is_correct = answer.chosen_index == answer.correct_index
        answer.answered_at = now
        correct += answer.is_correct
    attempt.correct_count = correct
    attempt.submitted_at = now
    db.commit()
    db.refresh(attempt)
    # A new result changes mastery, so the plan adapts straight away. The grade is already
    # saved, so a lost enrolment must not turn the submission into an error.
    try:
        planner_service.rebuild(db, attempt.course_id, user)
    except HTTPException:
        db.rollback()
    return quiz_view(db, attempt, user)
