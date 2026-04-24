import time
import os

try:
    import redis
except ImportError:
    redis = None


_local_hits: dict[str, list[float]] = {}


def _redis_client():
    if redis is None:
        return None

    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return redis.Redis.from_url(redis_url, decode_responses=True)

    host = os.getenv("REDIS_HOST")
    if not host:
        return None

    return redis.Redis(
        host=host,
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        decode_responses=True,
    )


def is_rate_limited(user_id: str) -> bool:
    limit = int(os.getenv("RATE_LIMIT_MESSAGES", "20"))
    window = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

    if limit <= 0:
        return False

    client = _redis_client()
    key = f"rate:{user_id}"

    if client:
        count = client.incr(key)
        if count == 1:
            client.expire(key, window)
        return count > limit

    now = time.time()
    hits = _local_hits.get(user_id, [])
    hits = [t for t in hits if now - t < window]
    hits.append(now)
    _local_hits[user_id] = hits
    return len(hits) > limit
