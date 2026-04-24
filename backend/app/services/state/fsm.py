import os

try:
    import redis
except ImportError:
    redis = None


DEFAULT_STATE = "new"
TTL_SECONDS = int(os.getenv("AI_STATE_TTL_SECONDS", "86400"))
_ALLOWED_STATES = {"new", "search", "qualify", "lead"}
_local_state: dict[str, str] = {}


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
    return f"state:{user_id}"


def get_state(user_id: str) -> str:
    client = _redis_client()
    if client:
        return client.get(_key(user_id)) or DEFAULT_STATE
    return _local_state.get(user_id, DEFAULT_STATE)


def set_state(user_id: str, state: str) -> str:
    if state not in _ALLOWED_STATES:
        state = DEFAULT_STATE

    client = _redis_client()
    if client:
        client.set(_key(user_id), state, ex=TTL_SECONDS)
    else:
        _local_state[user_id] = state

    return state


def detect_next_state(user_text: str, extracted_lead: dict | None = None) -> str:
    text = (user_text or "").lower()
    extracted_lead = extracted_lead or {}

    has_service = bool(extracted_lead.get("service")) or any(
        word in text for word in [
            "нужен", "нужна", "ищу", "надо", "электрик", "сантехник",
            "ремонт", "уборка", "cleaner", "plumber", "electrician", "repair"
        ]
    )
    has_location = bool(extracted_lead.get("city")) or any(
        word in text for word in ["дублин", "dublin", "город", "район", "near me"]
    )
    has_urgency = bool(extracted_lead.get("urgency")) or any(
        word in text for word in ["срочно", "сегодня", "завтра", "urgent", "today", "asap"]
    )
    has_contact = bool(extracted_lead.get("contact")) or any(
        word in text for word in ["телефон", "номер", "whatsapp", "email", "@", "+353", "+44"]
    )

    if has_contact:
        return "lead"
    if has_service and has_location and has_urgency:
        return "qualify"
    if has_service or has_location:
        return "search"
    return "new"
