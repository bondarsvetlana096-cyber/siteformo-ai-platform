import json
import os

try:
    import redis
except ImportError:
    redis = None


MAX_HISTORY = int(os.getenv("AI_MEMORY_MAX_HISTORY", "12"))
TTL_SECONDS = int(os.getenv("AI_MEMORY_TTL_SECONDS", "86400"))

_local_memory: dict[str, list[dict[str, str]]] = {}


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


def _key(user_id: str) -> str:
    return f"chat:{user_id}"


def get_history(user_id: str) -> list[dict[str, str]]:
    client = _redis_client()
    if client:
        raw = client.get(_key(user_id))
        if raw:
            try:
                return json.loads(raw)[-MAX_HISTORY:]
            except json.JSONDecodeError:
                return []
    return _local_memory.get(user_id, [])[-MAX_HISTORY:]


def save_turn(user_id: str, user_text: str, assistant_text: str) -> None:
    history = get_history(user_id)
    history.extend([
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ])
    history = history[-MAX_HISTORY:]

    client = _redis_client()
    if client:
        client.set(_key(user_id), json.dumps(history, ensure_ascii=False), ex=TTL_SECONDS)
        return

    _local_memory[user_id] = history
