from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.config import settings


class InMemoryRateLimiter:
    def __init__(self, limit: int, period_seconds: int = 3600) -> None:
        self.limit = limit
        self.period_seconds = period_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.time()
        queue = self.hits[key]
        while queue and queue[0] <= now - self.period_seconds:
            queue.popleft()
        if len(queue) >= self.limit:
            raise HTTPException(status_code=429, detail='Too many requests')
        queue.append(now)


limiter = InMemoryRateLimiter(limit=settings.rate_limit_per_hour)


async def rate_limit_dependency(request: Request) -> None:
    if not settings.rate_limit_enabled:
        return
    ip = request.headers.get('x-forwarded-for', request.client.host if request.client else '0.0.0.0').split(',')[0].strip()
    limiter.check(ip)
