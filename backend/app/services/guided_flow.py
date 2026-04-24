from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation import ConversationMessage, ConversationSession
from app.services.db.models import Lead
from app.services.db.postgres import SessionLocal as LeadSessionLocal
from app.services.notifications.telegram_notifier import notify_owner_about_lead


@dataclass(frozen=True)
class FlowStep:
    key: str
    message: str
    options: list[dict[str, str]]
    next_step: str | None = None
    input_type: str | None = None


FLOW: dict[str, FlowStep] = {
    "start": FlowStep(
        key="start",
        message="Что вам нужно?",
        options=[
            {"label": "Новый сайт", "value": "new_site"},
            {"label": "Редизайн", "value": "redesign"},
            {"label": "AI-форма", "value": "ai_form"},
            {"label": "Интеграции", "value": "integrations"},
            {"label": "Не знаю", "value": "not_sure"},
        ],
        next_step="business_type",
    ),
    "business_type": FlowStep(
        key="business_type",
        message="Какой у вас тип бизнеса?",
        options=[
            {"label": "Услуги", "value": "services"},
            {"label": "E-commerce", "value": "ecommerce"},
            {"label": "Эксперт / блогер", "value": "creator"},
            {"label": "Образование", "value": "education"},
            {"label": "Другое", "value": "other"},
        ],
        next_step="timeline",
    ),
    "timeline": FlowStep(
        key="timeline",
        message="Когда хотите запустить?",
        options=[
            {"label": "Срочно", "value": "urgent"},
            {"label": "В течение месяца", "value": "month"},
            {"label": "Пока изучаю", "value": "research"},
        ],
        next_step="budget",
    ),
    "budget": FlowStep(
        key="budget",
        message="Какой примерный бюджет?",
        options=[
            {"label": "До €500", "value": "under_500"},
            {"label": "€500–1500", "value": "500_1500"},
            {"label": "€1500+", "value": "1500_plus"},
            {"label": "Пока не знаю", "value": "unknown"},
        ],
        next_step="contact_channel",
    ),
    "contact_channel": FlowStep(
        key="contact_channel",
        message="Куда удобно отправить расчёт?",
        options=[
            {"label": "Telegram", "value": "telegram"},
            {"label": "WhatsApp", "value": "whatsapp"},
            {"label": "Email", "value": "email"},
        ],
        next_step="contact_value",
    ),
    "contact_value": FlowStep(
        key="contact_value",
        message="Оставьте контакт: Telegram username, WhatsApp номер или Email.",
        options=[],
        next_step="done",
        input_type="text",
    ),
}

ANSWER_LABELS = {
    step_key: {option["value"]: option["label"] for option in step.options}
    for step_key, step in FLOW.items()
}

HOT_BUDGETS = {"1500_plus"}
HOT_TIMELINES = {"urgent"}


class GuidedFlowService:
    CHANNEL = "web-chat"

    @staticmethod
    def _public_session(session: ConversationSession) -> str:
        return session.external_user_id

    @staticmethod
    def _get_or_create_session(db: Session, session_id: str | None) -> ConversationSession:
        external_user_id = (session_id or "").strip() or str(uuid.uuid4())
        stmt = select(ConversationSession).where(
            ConversationSession.channel == GuidedFlowService.CHANNEL,
            ConversationSession.external_user_id == external_user_id,
        )
        session = db.execute(stmt).scalar_one_or_none()
        if session:
            return session

        session = ConversationSession(
            channel=GuidedFlowService.CHANNEL,
            external_user_id=external_user_id,
            state="start",
            preferred_language="ru",
            collected_data={},
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def _save_message(db: Session, session: ConversationSession, direction: str, text: str, meta: dict[str, Any] | None = None) -> None:
        db.add(
            ConversationMessage(
                session_id=session.id,
                direction=direction,
                text=text,
                metadata_json=meta or {},
            )
        )
        db.commit()

    @staticmethod
    def _step_payload(session: ConversationSession, step_key: str) -> dict[str, Any]:
        step = FLOW[step_key]
        return {
            "session_id": GuidedFlowService._public_session(session),
            "step": step.key,
            "message": step.message,
            "options": step.options,
            "input_type": step.input_type,
            "is_complete": False,
            "collected_data": session.collected_data or {},
        }

    @staticmethod
    def start(db: Session, session_id: str | None = None, reset: bool = False) -> dict[str, Any]:
        session = GuidedFlowService._get_or_create_session(db, session_id)
        if reset:
            session.state = "start"
            session.collected_data = {}
            session.last_order_id = None
            db.add(session)
            db.commit()
            db.refresh(session)
        return GuidedFlowService._step_payload(session, session.state if session.state in FLOW else "start")

    @staticmethod
    def _validate_answer(step_key: str, answer: str) -> None:
        step = FLOW[step_key]
        if step.input_type == "text":
            if not answer.strip():
                raise ValueError("Контакт не может быть пустым")
            return
        allowed = {option["value"] for option in step.options}
        if answer not in allowed:
            raise ValueError("Некорректный вариант ответа")

    @staticmethod
    def _build_result(data: dict[str, Any]) -> str:
        service = ANSWER_LABELS["start"].get(data.get("start"), "задача")
        business = ANSWER_LABELS["business_type"].get(data.get("business_type"), "ваш бизнес")
        timeline = ANSWER_LABELS["timeline"].get(data.get("timeline"), "сроки уточним")
        budget = ANSWER_LABELS["budget"].get(data.get("budget"), "бюджет уточним")
        return (
            "Готово — заявка собрана. "
            f"Вам нужен: {service}. Тип бизнеса: {business}. "
            f"Сроки: {timeline}. Бюджет: {budget}. "
            "Мы подготовим следующий шаг и свяжемся с вами по выбранному контакту."
        )

    @staticmethod
    def _approval_status(data: dict[str, Any]) -> str:
        if data.get("budget") in HOT_BUDGETS or data.get("timeline") in HOT_TIMELINES:
            return "qualified"
        return "new"

    @staticmethod
    def _lead_payload(session: ConversationSession, data: dict[str, Any]) -> dict[str, Any]:
        contact_channel = data.get("contact_channel")
        contact_value = data.get("contact_value")
        raw = json.dumps(data, ensure_ascii=False)
        return {
            "user_id": session.external_user_id,
            "channel": contact_channel or GuidedFlowService.CHANNEL,
            "service": ANSWER_LABELS["start"].get(data.get("start"), data.get("start")),
            "city": ANSWER_LABELS["business_type"].get(data.get("business_type"), data.get("business_type")),
            "urgency": ANSWER_LABELS["timeline"].get(data.get("timeline"), data.get("timeline")),
            "contact": contact_value,
            "raw_text": raw,
            "status": GuidedFlowService._approval_status(data),
        }

    @staticmethod
    async def _save_lead(session: ConversationSession, data: dict[str, Any]) -> dict[str, Any] | None:
        lead_data = GuidedFlowService._lead_payload(session, data)
        lead_id = None
        if LeadSessionLocal is not None:
            lead_db = LeadSessionLocal()
            try:
                lead = Lead(**lead_data)
                lead_db.add(lead)
                lead_db.commit()
                lead_db.refresh(lead)
                lead_id = lead.id
            finally:
                lead_db.close()

        await notify_owner_about_lead(
            lead_data=lead_data,
            user_id=session.external_user_id,
            channel=lead_data.get("channel"),
            raw_text=lead_data.get("raw_text") or "",
        )
        return {"id": lead_id, **lead_data}

    @staticmethod
    async def answer(db: Session, session_id: str | None, answer: str | None) -> dict[str, Any]:
        session = GuidedFlowService._get_or_create_session(db, session_id)
        current_step = session.state if session.state in FLOW else "start"
        answer_value = (answer or "").strip()
        GuidedFlowService._validate_answer(current_step, answer_value)

        data = dict(session.collected_data or {})
        data[current_step] = answer_value
        label = ANSWER_LABELS.get(current_step, {}).get(answer_value, answer_value)
        GuidedFlowService._save_message(
            db,
            session,
            "in",
            label,
            {"step": current_step, "answer": answer_value},
        )

        next_step = FLOW[current_step].next_step or "done"
        session.collected_data = data
        session.state = next_step
        db.add(session)
        db.commit()
        db.refresh(session)

        if next_step != "done":
            payload = GuidedFlowService._step_payload(session, next_step)
            GuidedFlowService._save_message(db, session, "out", payload["message"], {"step": next_step})
            return payload

        result_message = GuidedFlowService._build_result(data)
        lead = await GuidedFlowService._save_lead(session, data)
        GuidedFlowService._save_message(db, session, "out", result_message, {"step": "done", "lead": lead})
        return {
            "session_id": GuidedFlowService._public_session(session),
            "step": "done",
            "message": result_message,
            "options": [
                {"label": "Начать заново", "value": "restart"},
            ],
            "input_type": None,
            "is_complete": True,
            "approval_status": GuidedFlowService._approval_status(data),
            "lead": lead,
            "cta": {
                "label": "Перейти на SiteFormo",
                "url": "https://siteformo.com",
            },
            "collected_data": data,
        }
