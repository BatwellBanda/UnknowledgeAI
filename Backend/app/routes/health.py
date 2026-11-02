"""Separate process liveness from database connectivity."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database.connection import get_db

router = APIRouter(tags=["Health"])


@router.get("/health")
def health_check():
    return {"status": "healthy"}


@router.get("/ready", responses={503: {"description": "Database unavailable"}})
def readiness_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError:
        # Driver errors can contain credentials or infrastructure details.
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "unavailable"},
        )
    return {"status": "ready", "database": "available"}
