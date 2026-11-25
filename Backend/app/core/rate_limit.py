"""In-process sliding-window rate limits.

Counters live in this process's memory: run one API process, or move the store to
Redis before scaling out horizontally, otherwise each process enforces its own limit.
"""
import math
import threading
import time
from collections import defaultdict, deque
from fastapi import Depends, HTTPException, Request
from app.core.config import settings
from app.core.security import get_current_user


class RateLimiter:
    def __init__(self):
        self._hits: dict[tuple, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: tuple, limit: int, window: float) -> float:
        """Record a hit; return 0 when allowed, otherwise seconds until a slot frees."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                return hits[0] + window - now
            hits.append(now)
            return 0

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # The proxy appends the address it saw; earlier entries are client-controlled.
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def enforce(key: tuple, limit: int, window: float):
    if not settings.RATE_LIMIT_ENABLED:
        return
    retry_after = limiter.hit(key, limit, window)
    if retry_after:
        raise HTTPException(
            status_code=429, detail="Too many requests; please wait and try again",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


def limit_by_ip(scope: str, limit: int, window: float):
    def dependency(request: Request):
        enforce((scope, client_ip(request)), limit, window)
    return dependency


def limit_by_user(scope: str, limit: int, window: float):
    """Authenticate, then limit per account; returns the current user."""
    def dependency(current_user=Depends(get_current_user)):
        enforce((scope, current_user.id), limit, window)
        return current_user
    return dependency
