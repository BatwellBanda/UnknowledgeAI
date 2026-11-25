"""Course topics and source-backed multiple-choice questions.

A question is only ever visible to someone who can currently read its source document:
its author, or (once an instructor publishes it) students enrolled in the course.
"""
import logging
import secrets
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.concurrency import generation_slots, slot
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.learning import QuizQuestion, Topic
from app.models.user import User
from app.schemas.learning import (
    ModelQuestion, QuestionContent, QuestionCreate, QuestionGenerateRequest, QuestionGenerateResponse,
    QuestionResponse, QuestionUpdate, TopicCreate, TopicUpdate,
)
from app.schemas.search import SearchRequest
from app.services import chat_service, course_service, document_service, llm_service, search_service

logger = logging.getLogger(__name__)


def normalise(text: str) -> str:
    return " ".join(text.split())


# Topics

def list_topics(db: Session, course_id, user: User) -> list[Topic]:
    course_service.require_course_membership(db, course_id, user)
    return db.query(Topic).filter(Topic.course_id == course_id).order_by(Topic.name, Topic.id).all()


def create_topic(db: Session, course_id, data: TopicCreate, user: User) -> Topic:
    course_service.require_management(db, course_id, user)
    topic = Topic(course_id=course_id, name=data.name, description=data.description or None)
    db.add(topic)
    _commit_topic(db, course_id, topic.name)
    db.refresh(topic)
    return topic


def update_topic(db: Session, topic_id, data: TopicUpdate, user: User) -> Topic:
    topic = get_topic(db, topic_id)
    course_service.require_management(db, topic.course_id, user)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        topic.name = changes["name"]
    if "description" in changes:
        topic.description = changes["description"] or None
    _commit_topic(db, topic.course_id, topic.name)
    db.refresh(topic)
    return topic


def _commit_topic(db: Session, course_id, name: str):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"This course already has a topic called “{name}”")


def get_topic(db: Session, topic_id) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic


def member_topic(db: Session, topic_id, user: User) -> Topic:
    topic = get_topic(db, topic_id)
    course_service.require_course_membership(db, topic.course_id, user)
    return topic


# Question visibility

def readable_questions(db: Session, user: User):
    """Permissions are part of the query: authored or published, and the source must be readable."""
    readable_documents = document_service.readable_documents(db, user).with_entities(Document.id).statement
    return db.query(QuizQuestion).filter(
        QuizQuestion.document_id.in_(readable_documents),
        or_(
            QuizQuestion.owner_id == user.id,
            and_(QuizQuestion.visibility == "course",
                 QuizQuestion.course_id.in_(course_service.accessible_course_ids(user.id))),
        ),
    )


def own_question(db: Session, question_id, user: User) -> QuizQuestion:
    question = readable_questions(db, user).filter(
        QuizQuestion.id == question_id, QuizQuestion.owner_id == user.id,
    ).first()
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return question


def list_own_questions(db: Session, user: User, course_id=None, topic_id=None, offset=0, limit=50):
    query = readable_questions(db, user).filter(QuizQuestion.owner_id == user.id)
    if course_id is not None:
        query = query.filter(QuizQuestion.course_id == course_id)
    if topic_id is not None:
        query = query.filter(QuizQuestion.topic_id == topic_id)
    return query.order_by(QuizQuestion.created_at.desc(), QuizQuestion.id).offset(offset).limit(limit).all()


def reanchor(db: Session, question: QuizQuestion) -> bool:
    """After reprocessing replaced its chunk, reattach the question to the passage holding its quote."""
    if question.chunk_id is not None:
        return True
    quote = normalise(question.source_quote)
    chunks = db.query(DocumentChunk).filter(DocumentChunk.document_id == question.document_id).order_by(DocumentChunk.chunk_index)
    for chunk in chunks:
        if quote in normalise(chunk.content):
            question.chunk_id = chunk.id
            question.source_location = chunk.source_location
            return True
    return False


# Authoring

def create_question(db: Session, data: QuestionCreate, user: User) -> QuizQuestion:
    topic = member_topic(db, data.topic_id, user)
    readable = document_service.readable_documents(db, user).with_entities(Document.id).statement
    chunk = db.query(DocumentChunk).filter(DocumentChunk.id == data.chunk_id, DocumentChunk.document_id.in_(readable)).first()
    if chunk is None:
        raise HTTPException(status_code=404, detail="Source passage not found")
    quote = normalise(data.source_quote)
    if quote not in normalise(chunk.content):
        raise HTTPException(status_code=400, detail="The supporting quote must be copied exactly from the source passage")
    question = QuizQuestion(
        owner_id=user.id, course_id=topic.course_id, topic_id=topic.id, document_id=chunk.document_id,
        chunk_id=chunk.id, source_location=chunk.source_location, source_quote=quote,
        stem=data.stem, options=data.options, correct_index=data.correct_index, explanation=data.explanation,
        # The author wrote and therefore reviewed it.
        origin="manual", reviewed=True, visibility="private",
    )
    db.add(question)
    db.commit()
    db.refresh(question)
    return question


def update_question(db: Session, question_id, data: QuestionUpdate, user: User) -> QuizQuestion:
    question = own_question(db, question_id, user)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("topic_id") is not None:
        topic = member_topic(db, changes["topic_id"], user)
        if topic.course_id != question.course_id:
            raise HTTPException(status_code=400, detail="A question can only move between topics of its own course")
        question.topic_id = topic.id
    content_fields = {"stem", "options", "correct_index", "explanation"}
    if content_fields & {key for key, value in changes.items() if value is not None}:
        merged = {field: changes.get(field) if changes.get(field) is not None else getattr(question, field) for field in content_fields}
        try:
            content = QuestionContent.model_validate(merged)
        except ValidationError as error:
            raise HTTPException(status_code=422, detail=[{"loc": e["loc"], "msg": e["msg"]} for e in error.errors()])
        for field in content_fields:
            setattr(question, field, getattr(content, field))
    if changes.get("reviewed") is not None:
        question.reviewed = changes["reviewed"]
        if not question.reviewed:
            # Unreviewed questions must not stay in front of a class.
            question.visibility = "private"
    db.commit()
    db.refresh(question)
    return question


def delete_question(db: Session, question_id, user: User):
    question = own_question(db, question_id, user)
    db.delete(question)
    db.commit()


def set_published(db: Session, question_id, user: User, published: bool) -> QuizQuestion:
    question = own_question(db, question_id, user)
    if published:
        course = course_service.get_course(db, question.course_id)
        if course.instructor_id != user.id or user.role.name not in {"LECTURER", "ADMIN"}:
            raise HTTPException(status_code=403, detail="Only the course instructor can publish questions to the class")
        if not question.reviewed:
            raise HTTPException(status_code=409, detail="Review the question and mark it reviewed before publishing")
        document = db.get(Document, question.document_id)
        if document.visibility != "course" or document.course_id != question.course_id:
            raise HTTPException(status_code=409, detail="Publish the source material to this course before publishing its questions")
        if not reanchor(db, question):
            raise HTTPException(status_code=409, detail="The supporting passage is no longer in the processed material")
    question.visibility = "course" if published else "private"
    db.commit()
    db.refresh(question)
    return question


# Generation

def topic_query(topic: Topic) -> str:
    return (topic.name + (f". {topic.description}" if topic.description else ""))[:500]


def validated_generation(raw, passage: str) -> tuple[QuestionContent, str] | None:
    """Return a checked question, or None when the model output must not be used."""
    try:
        generated = ModelQuestion.model_validate(raw)
        if generated.status != "ok" or len(generated.options) != 4:
            return None
        quote = normalise(generated.quote)
        if len(quote) < 3 or quote not in normalise(passage):
            return None
        content = QuestionContent(stem=generated.question, options=generated.options,
                                  correct_index=generated.correct_option, explanation=generated.explanation)
    except (ValidationError, ValueError, TypeError):
        return None
    # Shuffle so the model's preferred answer position carries no signal.
    order = list(range(len(content.options)))
    secrets.SystemRandom().shuffle(order)
    content.options = [content.options[index] for index in order]
    content.correct_index = order.index(content.correct_index)
    return content, quote


def generate_questions(db: Session, data: QuestionGenerateRequest, user: User) -> QuestionGenerateResponse:
    topic = member_topic(db, data.topic_id, user)
    if data.document_id is not None:
        document_service.get_readable_document(db, data.document_id, user)
    request = SearchRequest(query=topic_query(topic), top_k=data.count,
                            course_id=None if data.document_id else topic.course_id, document_id=data.document_id)
    retrieved = search_service.search(db, request, user)
    if not retrieved.results:
        return QuestionGenerateResponse(created=[], rejected=0, status="no_searchable_materials" if retrieved.status == "no_searchable_materials" else "no_matches")
    sources = retrieved.results
    chat_service.validate_current_sources(db, user, sources)
    drafts = []
    rejected = 0
    for source in sources:
        with slot(generation_slots, "The assistant is busy; please try again in a moment"):
            try:
                raw = llm_service.generate_question(topic.name, source.text)
            except HTTPException as error:
                # A provider outage stops the batch; one malformed answer does not.
                if error.status_code != 502:
                    raise
                raw = None
        checked = validated_generation(raw, source.text) if raw is not None else None
        if checked is None:
            rejected += 1
            continue
        drafts.append((source, *checked))
    # Do not save questions from material that was revoked or reprocessed during generation.
    chat_service.validate_current_sources(db, user, sources)
    if rejected:
        logger.info("Rejected %s generated question(s) that failed validation", rejected)
    created = []
    for source, content, quote in drafts:
        question = QuizQuestion(
            owner_id=user.id, course_id=topic.course_id, topic_id=topic.id, document_id=source.document_id,
            chunk_id=source.chunk_id, source_location=source.source_location.model_dump() if source.source_location else None,
            source_quote=quote, stem=content.stem, options=content.options, correct_index=content.correct_index,
            explanation=content.explanation, origin="generated", reviewed=False, visibility="private",
        )
        db.add(question)
        created.append(question)
    db.commit()
    for question in created:
        db.refresh(question)
    return QuestionGenerateResponse(created=[QuestionResponse.model_validate(q) for q in created], rejected=rejected, status="generated")
