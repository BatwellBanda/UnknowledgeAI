"""Separate process liveness from database connectivity."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.processing_job import ProcessingJob
from database.connection import get_db

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    # Async so liveness answers even when every request thread is busy.
    return {"status": "healthy"}


@router.get("/ready", responses={503: {"description": "Database unavailable"}})
def readiness_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1")).scalar_one()
        queued, oldest = db.query(func.count(ProcessingJob.id), func.min(ProcessingJob.created_at)).filter(
            ProcessingJob.status == "queued",
        ).one()
    except SQLAlchemyError:
        # Driver errors can contain credentials or infrastructure details.
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "unavailable"},
        )
    wait = 0
    if oldest is not None:
        oldest = oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc)
        wait = max(0, int((datetime.now(timezone.utc) - oldest).total_seconds()))
    # A growing wait means no worker is running (start: python -m app.worker).
    return {"status": "ready", "database": "available", "processing": {"queued_jobs": queued, "oldest_wait_seconds": wait}}
