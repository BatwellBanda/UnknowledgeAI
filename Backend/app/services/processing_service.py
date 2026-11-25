import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import text, or_, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.document import Document
from app.models.processing_job import ProcessingJob
from app.models.user import User
from app.services import extraction_service, chunking_service, embedding_service

logger = logging.getLogger(__name__)
ACTIVE = ("queued", "running")


def owned_document(db, document_id, user):
    document = db.query(Document).filter(Document.id == document_id, Document.owner_id == user.id).first()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def active_job(db, document_id):
    return db.query(ProcessingJob).filter(ProcessingJob.document_id == document_id, ProcessingJob.status.in_(ACTIVE)).first()


@contextmanager
def document_lock(bind, document_id):
    """Session advisory lock held across stage commits, on its own connection."""
    if bind.dialect.name != "postgresql":
        # SQLite is used only for endpoint/validation tests, not deployed workers.
        yield True
        return
    key = (document_id.int & ((1 << 64) - 1)) - (1 << 63)
    with bind.engine.connect() as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                connection.commit()


def enqueue(db: Session, document_id, user: User) -> ProcessingJob:
    owned_document(db, document_id, user)
    existing = active_job(db, document_id)
    if existing:
        return existing
    with document_lock(db.get_bind(), document_id) as acquired:
        if not acquired:
            raise HTTPException(status_code=409, detail="Document processing is already in progress")
        job = ProcessingJob(document_id=document_id)
        db.add(job)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = active_job(db, document_id)
            if existing:
                return existing
            raise
        db.refresh(job)
        return job


def latest_job(db, document_id, user):
    owned_document(db, document_id, user)
    job = db.query(ProcessingJob).filter(ProcessingJob.document_id == document_id).order_by(
        ProcessingJob.created_at.desc(), ProcessingJob.id.desc(),
    ).first()
    if job is None:
        raise HTTPException(status_code=404, detail="No processing job found")
    return job


@contextmanager
def manual_processing(db, document_id, user):
    # Preserve the established 403 response on the manual processing endpoints.
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this document")
    with document_lock(db.get_bind(), document_id) as acquired:
        if not acquired or active_job(db, document_id):
            raise HTTPException(status_code=409, detail="A processing job is active; wait for it to finish")
        yield


def process_claimed_job(db, job):
    job_id, document_id = job.id, job.document_id
    stage = job.stage
    stages = (("extract", extraction_service.extract_document_text),
              ("chunk", chunking_service.chunk_document),
              ("embed", embedding_service.embed_document))
    try:
        document = db.get(Document, document_id)
        if document is None:
            raise ValueError("Processing document no longer exists")
        owner = db.get(User, document.owner_id)
        if owner is None:
            raise ValueError("Processing owner no longer exists")
        for stage, operation in stages:
            job.stage = stage
            db.commit()
            operation(db, job.document_id, owner)
        job.status = "completed"
        job.stage = "done"
        job.error_message = None
        job.completed_at = datetime.now(timezone.utc)
        job.lease_expires_at = None
        db.commit()
    except Exception:
        logger.exception("Processing job %s failed", job_id)
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        if job is None:
            return
        job.status = "failed"
        job.stage = stage
        job.error_message = f"Processing failed during {stage}; retry this document."
        job.completed_at = datetime.now(timezone.utc)
        job.lease_expires_at = None
        document = db.get(Document, document_id)
        if document is not None:
            document.processing_status = "failed"
        db.commit()


def run_next_job(bind) -> bool:
    if bind.dialect.name != "postgresql":
        raise RuntimeError("The durable processing worker requires PostgreSQL")
    with Session(bind) as db:
        now = datetime.now(timezone.utc)
        candidates = db.query(ProcessingJob).filter(or_(
            ProcessingJob.status == "queued",
            and_(ProcessingJob.status == "running", ProcessingJob.lease_expires_at <= now),
        )).order_by(ProcessingJob.created_at, ProcessingJob.id).with_for_update(skip_locked=True).limit(20).all()
        for job in candidates:
            with document_lock(bind, job.document_id) as acquired:
                if not acquired:
                    continue
                if job.attempts >= 3:
                    job.status = "failed"
                    job.error_message = "Processing was interrupted three times; retry this document."
                    job.completed_at = now
                    job.lease_expires_at = None
                    db.get(Document, job.document_id).processing_status = "failed"
                    db.commit()
                    return True
                job.status = "running"
                job.attempts += 1
                job.started_at = now
                job.completed_at = None
                job.error_message = None
                job.lease_expires_at = now + timedelta(minutes=5)
                db.commit()
                # A live document lock prevents stale-lease takeover during slow inference.
                # If the worker dies, the connection closes and PostgreSQL releases the lock.
                process_claimed_job(db, job)
                return True
        db.rollback()
        return False
