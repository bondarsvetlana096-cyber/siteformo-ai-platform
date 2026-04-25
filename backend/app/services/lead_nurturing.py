from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from openai import OpenAI

from app.core.config import settings
from app.services.db.models import Lead
from app.services.db.postgres import SessionLocal
from app.services.logging.safe_logger import get_logger, mask_sensitive
from app.services.notifications.telegram_notifier import notify_owner_about_lead

logger = get_logger("siteformo.lead_nurturing")

_STAGE_DELAYS = {
    0: lambda: timedelta(minutes=settings.guided_followup_stage_1_minutes),
    1: lambda: timedelta(minutes=settings.guided_followup_stage_2_minutes),
    2: lambda: timedelta(minutes=settings.guided_followup_stage_3_minutes),
    3: lambda: timedelta(minutes=settings.guided_followup_stage_4_minutes),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_answers(raw_text: str | None) -> dict[str, Any]:
    if not raw_text:
        return {}
    try:
        data = json.loads(raw_text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def generate_followup_message(lead: Lead, stage: int) -> str:
    answers = _parse_answers(lead.raw_text)
    is_hot = bool(getattr(lead, "is_hot", False) or lead.status == "qualified")
    fallback_by_stage = {
        0: "Привет! Вы оставляли заявку на SiteFormo. Хотите, покажу пример решения под ваш бизнес?",
        1: "Мы можем предложить быстрый план запуска сайта/AI-формы под вашу задачу. Написать детали?",
        2: "Могу подготовить короткий план: структура, сроки и следующий шаг. Актуально?",
        3: "Закрываю заявки на этой неделе. Если проект ещё актуален — напишите, и я помогу с запуском.",
    }
    if not settings.openai_api_key:
        return fallback_by_stage.get(stage, fallback_by_stage[3])

    prompt = f"""
Ты — аккуратный менеджер по продажам SiteFormo. Сгенерируй короткое follow-up сообщение на русском до 3 строк.
Без давления, без обещаний гарантий, с мягким CTA.

Данные лида:
- Услуга: {lead.service or answers.get('start')}
- Тип бизнеса: {lead.city or answers.get('business_type')}
- Срочность: {lead.urgency or answers.get('timeline')}
- Бюджет: {answers.get('budget')}
- Статус: {'горячий' if is_hot else 'обычный'}
- Стадия дожима: {stage + 1}

Если лид горячий — будь чуть прямее. Если обычный — дай больше ценности.
""".strip()
    try:
        client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=settings.openai_max_retries)
        response = client.chat.completions.create(model=settings.openai_model or "gpt-4o-mini", messages=[{"role": "user", "content": prompt}])
        text = (response.choices[0].message.content or "").strip()
        return text or fallback_by_stage.get(stage, fallback_by_stage[3])
    except Exception as exc:
        logger.warning("followup_ai_failed error=%s", mask_sensitive(str(exc)))
        return fallback_by_stage.get(stage, fallback_by_stage[3])


async def _notify_owner_followup(lead: Lead, message: str, stage: int) -> None:
    lead_data = {
        "service": lead.service,
        "city": lead.city,
        "urgency": lead.urgency,
        "contact": lead.contact,
        "channel": lead.contact_channel or lead.channel,
        "suggested_followup": message,
        "followup_stage": stage + 1,
    }
    await notify_owner_about_lead(lead_data=lead_data, user_id=lead.user_id, channel=lead.contact_channel or lead.channel, raw_text=f"AI follow-up suggestion #{stage + 1}: {message}")


async def _send_telegram_to_lead(contact: str, message: str) -> bool:
    token = settings.telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not contact or not contact.lstrip("-").isdigit():
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": contact, "text": message})
        return response.status_code < 400
    except Exception as exc:
        logger.warning("telegram_direct_followup_failed error=%s", mask_sensitive(str(exc)))
        return False


async def send_followup(lead: Lead, message: str, stage: int) -> None:
    sent = False
    if settings.guided_followup_send_to_lead and (lead.contact_channel or lead.channel) == "telegram":
        sent = await _send_telegram_to_lead(str(lead.contact or ""), message)
    if not sent:
        await _notify_owner_followup(lead, message, stage)


def _due(lead: Lead) -> bool:
    stage = int(lead.followup_stage or 0)
    if stage >= settings.guided_followup_max_stage:
        return False
    delay_fn = _STAGE_DELAYS.get(stage)
    if delay_fn is None:
        return False
    base = lead.last_contacted or lead.created_at
    if base is None:
        return True
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return _utcnow() - base >= delay_fn()


async def process_due_followups_once(limit: int = 25) -> int:
    if SessionLocal is None:
        return 0
    db = SessionLocal()
    processed = 0
    try:
        leads = db.query(Lead).filter(Lead.followup_stage < settings.guided_followup_max_stage).order_by(Lead.created_at.asc()).limit(limit).all()
        for lead in leads:
            if not _due(lead):
                continue
            stage = int(lead.followup_stage or 0)
            message = generate_followup_message(lead, stage)
            await send_followup(lead, message, stage)
            history = list(lead.history or [])
            history.append({"stage": stage + 1, "message": message, "sent_at": _utcnow().isoformat()})
            lead.history = history
            lead.followup_stage = stage + 1
            lead.last_contacted = _utcnow()
            db.add(lead)
            processed += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("process_due_followups_failed error=%s", mask_sensitive(str(exc)))
    finally:
        db.close()
    return processed


async def followup_worker() -> None:
    logger.info("guided_followup_worker_started")
    while True:
        try:
            await process_due_followups_once()
        except Exception as exc:
            logger.warning("guided_followup_worker_loop_failed error=%s", mask_sensitive(str(exc)))
        await asyncio.sleep(max(10, int(settings.guided_followup_poll_seconds)))
