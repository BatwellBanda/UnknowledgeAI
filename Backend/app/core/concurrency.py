"""Bounded slots for slow model work, so it cannot occupy every request thread."""
import threading
from contextlib import contextmanager
from fastapi import HTTPException
from app.core.config import settings

generation_slots = threading.BoundedSemaphore(settings.MAX_CONCURRENT_GENERATIONS)
embedding_slots = threading.BoundedSemaphore(settings.MAX_CONCURRENT_EMBEDDINGS)


@contextmanager
def slot(semaphore: threading.BoundedSemaphore, busy_message: str):
    if not semaphore.acquire(timeout=settings.BUSY_WAIT_SECONDS):
        raise HTTPException(status_code=503, detail=busy_message, headers={"Retry-After": "15"})
    try:
        yield
    finally:
        semaphore.release()
