"""In-memory rate limiting architecture (swap for Redis in production)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


class RateLimiter:
    def __init__(self, limit_per_minute: int | None = None) -> None:
        settings = get_settings()
        self.limit = limit_per_minute or settings.rate_limit_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = time.time()
        window_start = now - 60
        with self._lock:
            q = self._hits[key]
            while q and q[0] < window_start:
                q.popleft()
            if len(q) >= self.limit:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
            q.append(now)


rate_limiter = RateLimiter()


async def rate_limit_dependency(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    auth = request.headers.get("authorization", "")
    key = f"{client}:{auth[:24]}"
    rate_limiter.check(key)
