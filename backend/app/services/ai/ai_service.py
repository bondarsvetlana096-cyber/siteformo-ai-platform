from app.core.settings import settings
from app.services.ai.lead_extractor import extract_lead_data
from app.services.ai.openai_client import create_response_with_retry
from app.services.ai.prompts import SYSTEM_PROMPT
from app.services.logging.safe_logger import get_logger, mask_sensitive
from app.services.memory.redis_memory import get_history, save_turn
from app.services.security.rate_limiter import is_rate_limited
from app.services.state.fsm import detect_next_state, get_state, set_state

logger = get_logger("siteformo.ai")


async def generate_ai_reply(user_text: str, user_id: str, channel: str) -> str:
    user_text = (user_text or "").strip()

    if not user_text:
        return "Please describe the website or landing page you want to create."

    if len(user_text) > settings.MAX_USER_MESSAGE_CHARS:
        user_text = user_text[: settings.MAX_USER_MESSAGE_CHARS]

    if is_rate_limited(user_id):
        logger.warning("rate_limited user=%s channel=%s", mask_sensitive(user_id), channel)
        return "Too many messages in a short time. Please try again in a minute."

    current_state = get_state(user_id)
    history = get_history(user_id)

    lead_data = {}
    if settings.ENABLE_LEAD_EXTRACTION:
        try:
            lead_data = await extract_lead_data(user_text, user_id=user_id, channel=channel)
        except Exception as exc:
            logger.warning("lead_extraction_failed user=%s error=%s", mask_sensitive(user_id), mask_sensitive(str(exc)))

    next_state = detect_next_state(user_text, lead_data)
    set_state(user_id, next_state)

    system_prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Channel: {channel}\n"
        f"Current funnel stage: {current_state}\n"
        f"Next expected stage: {next_state}\n"
        f"Extracted lead data: {lead_data}\n"
    )

    reply = await create_response_with_retry(
        input_data=[
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": user_text},
        ],
        fallback_text="Got it. Please share the business type, website goal, and how soon you need it.",
    )

    save_turn(user_id, user_text, reply)
    return reply
