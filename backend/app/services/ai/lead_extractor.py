import json
import os
from typing import Any

from openai import OpenAI

from app.services.db.models import Lead
from app.services.db.postgres import SessionLocal

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


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

    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        input=[
            {
                "role": "system",
                "content": (
                    "Извлеки лид из сообщения. Верни только JSON без markdown. "
                    "Поля: service, city, urgency, contact. "
                    "Если данных нет, используй null."
                ),
            },
            {"role": "user", "content": text},
        ],
    )

    data = _safe_json(response.output_text)
    if not data:
        return {}

    useful = any(data.get(k) for k in ["service", "city", "urgency", "contact"])
    if useful and SessionLocal is not None:
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
        finally:
            db.close()

    return data
