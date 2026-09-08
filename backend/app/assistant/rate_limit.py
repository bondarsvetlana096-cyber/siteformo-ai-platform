from __future__ import annotations

import time
from collections import defaultdict, deque


class AssistantLocalRateLimiter:
    """Process-local first layer; not a distributed production control."""

    def __init__(self, limit: int = 30, period_seconds: int = 60) -> None:
        self.limit = limit
        self.period_seconds = period_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, visitor_key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[visitor_key]
        while hits and hits[0] <= now - self.period_seconds:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


assistant_rate_limiter = AssistantLocalRateLimiter()
