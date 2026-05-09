import asyncio
from functools import lru_cache

from openai import OpenAI

from app.core.settings import settings
from app.services.logging.safe_logger import get_logger, mask_sensitive

logger = get_logger("siteformo.openai")


@lru_cache(maxsize=1)
def get_openai_client() -> OpenAI | None:
    """
    OpenAI client is created lazily.

    This prevents the whole backend from crashing during startup when:
    - OPENAI_API_KEY is missing locally
    - AI channels are disabled
    - only payments/review/production routes are needed
    """
    api_key = settings.OPENAI_API_KEY

    if not api_key:
        logger.warning("OPENAI_API_KEY is not set. AI response fallback will be used.")
        return None

    return OpenAI(
        api_key=api_key,
        timeout=settings.OPENAI_TIMEOUT_SECONDS,
        max_retries=0,
    )


async def create_response_with_retry(
    input_data,
    model: str | None = None,
    fallback_text: str | None = None,
) -> str:
    model = model or settings.OPENAI_MODEL
    fallback_text = fallback_text or "Got it. Please share the business type and the website goal."

    client = get_openai_client()

    if client is None:
        return fallback_text

    last_error = None

    for attempt in range(settings.OPENAI_MAX_RETRIES + 1):
        try:
            response = client.responses.create(
                model=model,
                input=input_data,
            )
            return (response.output_text or "").strip()

        except Exception as exc:
            last_error = exc
            logger.warning(
                "OpenAI request failed attempt=%s error=%s",
                attempt + 1,
                mask_sensitive(str(exc)),
            )

            if attempt < settings.OPENAI_MAX_RETRIES:
                await asyncio.sleep(0.6 * (attempt + 1))

    logger.error("OpenAI fallback used error=%s", mask_sensitive(str(last_error)))
    return fallback_text