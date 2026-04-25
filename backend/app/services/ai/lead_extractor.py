import json
from typing import Any

from app.core.settings import settings
from app.services.ai.openai_client import create_response_with_retry
from app.services.db.models import Lead
from app.services.db.postgres import SessionLocal
from app.services.logging.safe_logger import get_logger, mask_sensitive
from app.services.notifications.telegram_notifier import notify_owner_about_lead

logger = get_logger("siteformo.leads")


def _safe_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "", 1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


async def extract_lead_data(text: str, user_id: str, channel: str | None = None) -> dict[str, Any]:
    if not text:
        return {}

    output = await create_response_with_retry(
        input_data=[
            {
                "role": "system",
                "content": (
                    "Extract lead data from the message. Return JSON only with no markdown. "
                    "Fields: service, city, urgency, contact. "
                    "Use null when data is missing."
                ),
            },
            {"role": "user", "content": text},
        ],
        fallback_text="{}",
    )

    data = _safe_json(output)
    if not data:
        return {}

    useful = any(data.get(k) for k in ["service", "city", "urgency", "contact"])

    if useful and settings.ENABLE_DB_LEADS and SessionLocal is not None:
        db = SessionLocal()
        try:
            lead = Lead(
                user_id=user_id,
                channel=channel,
                service=data.get("service"),
                city=data.get("city"),
                urgency=data.get("urgency"),
                contact=data.get("contact"),
                raw_text=text,
            )
            db.add(lead)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("lead_save_failed user=%s error=%s", mask_sensitive(user_id), mask_sensitive(str(exc)))
        finally:
            db.close()

    await notify_owner_about_lead(data, user_id=user_id, channel=channel, raw_text=text)

    return data
