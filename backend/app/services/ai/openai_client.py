import asyncio

from openai import OpenAI

from app.core.settings import settings
from app.services.logging.safe_logger import get_logger, mask_sensitive

logger = get_logger("siteformo.openai")

client = OpenAI(
    api_key=settings.OPENAI_API_KEY,
    timeout=settings.OPENAI_TIMEOUT_SECONDS,
    max_retries=0,
)


async def create_response_with_retry(input_data, model: str | None = None, fallback_text: str | None = None) -> str:
    model = model or settings.OPENAI_MODEL
    fallback_text = fallback_text or "Понял. Уточните, пожалуйста, город и какая услуга нужна?"

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
