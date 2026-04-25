from __future__ import annotations

import json
import re
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.conversation import ConversationMessage, ConversationSession
from app.services.db.models import Lead
from app.services.db.postgres import SessionLocal as LeadSessionLocal
from app.services.notifications.telegram_notifier import notify_owner_about_lead
from app.services.offer_service import calculate_estimate, generate_offer_pdf_or_html, generate_offer_text


@dataclass(frozen=True)
class FlowStep:
    key: str
    message: str
    options: list[dict[str, str]] = field(default_factory=list)
    next_step: str | None = None
    input_type: str | None = None
    fields: list[dict[str, Any]] = field(default_factory=list)


FLOW: dict[str, FlowStep] = {
    "contact_channel": FlowStep(
        key="contact_channel",
        message="How should we identify you for this project?",
        options=[
            {"label": "Email", "value": "email"},
            {"label": "WhatsApp", "value": "whatsapp"},
            {"label": "Telegram", "value": "telegram"},
            {"label": "Messenger", "value": "messenger"},
        ],
        next_step="contact_value",
    ),
    "contact_value": FlowStep(
        key="contact_value",
        message="Enter your contact details.",
        input_type="text",
        next_step="project_context",
    ),
    "contact_confirmed": FlowStep(
        key="contact_confirmed",
        message="Please send the generated message to SiteFormo, then confirm here.",
        options=[{"label": "I sent the message", "value": "sent"}],
        next_step="project_context",
    ),
    "project_context": FlowStep(
        key="project_context",
        message="Tell us what page you need.",
        input_type="fields",
        fields=[
            {"name": "topic_or_website", "label": "Business topic or existing website", "type": "textarea", "required": True, "placeholder": "Example: dental clinic in Dublin, or https://example.com"},
            {"name": "reference_sites", "label": "Optional: paste up to 3 websites you like", "type": "textarea", "required": False, "placeholder": "One link per line, maximum 3"},
        ],
        next_step="short_brief",
    ),
    "short_brief": FlowStep(
        key="short_brief",
        message="A few quick details help AI estimate the work accurately.",
        input_type="fields",
        fields=[
            {"name": "business_name", "label": "Business name", "required": False},
            {"name": "target_audience", "label": "Target audience", "required": True},
            {"name": "main_goal", "label": "Main goal of the page", "required": True},
            {"name": "offer", "label": "Offer or service description", "type": "textarea", "required": True},
            {"name": "style", "label": "Preferred style or tone", "required": False},
            {"name": "features", "label": "Special functions or integrations", "required": False},
            {"name": "deadline", "label": "Deadline / urgency", "required": False},
        ],
        next_step="done",
    ),
}

ANSWER_LABELS = {step_key: {option["value"]: option["label"] for option in step.options} for step_key, step in FLOW.items()}


def _extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s,]+|www\.[^\s,]+", text or "")[:3]


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
            state="contact_channel",
            preferred_language="en",
            collected_data={},
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def _save_message(db: Session, session: ConversationSession, direction: str, text: str, meta: dict[str, Any] | None = None) -> None:
        db.add(ConversationMessage(session_id=session.id, direction=direction, text=text, metadata_json=meta or {}))
        db.commit()

    @staticmethod
    def _step_payload(session: ConversationSession, step_key: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        step = FLOW[step_key]
        payload = {
            "session_id": GuidedFlowService._public_session(session),
            "step": step.key,
            "message": step.message,
            "options": step.options,
            "input_type": step.input_type,
            "fields": step.fields,
            "is_complete": False,
            "collected_data": session.collected_data or {},
        }
        if extra:
            payload.update(extra)
        return payload

    @staticmethod
    def start(db: Session, session_id: str | None = None, reset: bool = False) -> dict[str, Any]:
        session = GuidedFlowService._get_or_create_session(db, session_id)
        if reset:
            session.state = "contact_channel"
            session.collected_data = {}
            session.last_order_id = None
            db.add(session)
            db.commit()
            db.refresh(session)
        return GuidedFlowService._step_payload(session, session.state if session.state in FLOW else "contact_channel")

    @staticmethod
    def _validate_answer(step_key: str, answer: Any, data: dict[str, Any]) -> Any:
        step = FLOW[step_key]
        if step.input_type == "fields":
            if not isinstance(answer, dict):
                raise ValueError("Please complete the fields before continuing.")
            cleaned = {str(k): str(v).strip() for k, v in answer.items() if v is not None}
            for field_def in step.fields:
                if field_def.get("required") and not cleaned.get(field_def["name"]):
                    raise ValueError(f"Required field: {field_def['label']}")
            if step_key == "project_context":
                cleaned["reference_sites_list"] = _extract_urls(cleaned.get("reference_sites", ""))[:3]
            return cleaned
        value = str(answer or "").strip()
        if step.input_type == "text":
            if not value:
                raise ValueError("Contact details are required.")
            if len(value) > 255:
                raise ValueError("Contact details are too long.")
            return value
        allowed = {option["value"] for option in step.options}
        if value not in allowed:
            raise ValueError("Invalid answer option.")
        return value

    @staticmethod
    def _confirmation_cta(channel: str, contact_value: str, session_id: str) -> dict[str, str] | None:
        msg = f"SITEFORMO-{session_id[:8]}: I confirm that I want to discuss my website project. My contact is {contact_value}."
        encoded = urllib.parse.quote(msg)
        if channel == "whatsapp" and settings.whatsapp_contact_number:
            phone = "".join(ch for ch in settings.whatsapp_contact_number if ch.isdigit())
            return {"label": "Send confirmation in WhatsApp", "url": f"https://wa.me/{phone}?text={encoded}", "message": msg}
        if channel == "telegram" and settings.telegram_bot_username:
            username = settings.telegram_bot_username.lstrip("@")
            return {"label": "Send confirmation in Telegram", "url": f"https://t.me/{username}?text={encoded}", "message": msg}
        if channel == "messenger" and settings.messenger_contact_url:
            return {"label": "Open Messenger", "url": settings.messenger_contact_url, "message": msg}
        return {"label": "Copy confirmation message", "url": settings.main_site_base_url or "https://siteformo.com", "message": msg}

    @staticmethod
    def _build_result(data: dict[str, Any], estimate: dict[str, Any]) -> str:
        project = data.get("project_context", {})
        brief = data.get("short_brief", {})
        topic = project.get("topic_or_website", "your project")
        goal = brief.get("main_goal", "generate qualified leads")
        return (
            f"Great — we have enough information to estimate {topic}.\n\n"
            f"Goal: {goal}.\n"
            f"Estimated price: from €{estimate['price_eur']}. Timeline: {estimate['timeline']}.\n\n"
            "This estimate is based on the amount of content, conversion work, and likely design complexity. "
            "If this feels too high, we can simplify the page and prepare a cheaper version.\n\n"
            "Next step: review the offer and make a 50% deposit. After payment is verified, you will receive a detailed questionnaire for final generation."
        )

    @staticmethod
    def _lead_payload(session: ConversationSession, data: dict[str, Any], estimate: dict[str, Any], offer: dict[str, str]) -> dict[str, Any]:
        project = data.get("project_context", {})
        brief = data.get("short_brief", {})
        return {
            "user_id": session.external_user_id,
            "channel": GuidedFlowService.CHANNEL,
            "contact_channel": data.get("contact_channel"),
            "service": project.get("topic_or_website"),
            "city": brief.get("target_audience"),
            "urgency": brief.get("deadline"),
            "contact": data.get("contact_value"),
            "raw_text": json.dumps(data, ensure_ascii=False),
            "status": "qualified",
            "is_hot": True,
            "followup_stage": 0,
            "history": [],
            "estimate": estimate,
            "offer_url": offer.get("url"),
        }

    @staticmethod
    async def _save_lead(session: ConversationSession, data: dict[str, Any], estimate: dict[str, Any], offer: dict[str, str]) -> dict[str, Any] | None:
        lead_data = GuidedFlowService._lead_payload(session, data, estimate, offer)
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
        await notify_owner_about_lead(lead_data=lead_data, user_id=session.external_user_id, channel=lead_data.get("contact_channel") or lead_data.get("channel"), raw_text=lead_data.get("raw_text") or "")
        return {"id": lead_id, **lead_data}

    @staticmethod
    async def answer(db: Session, session_id: str | None, answer: Any) -> dict[str, Any]:
        session = GuidedFlowService._get_or_create_session(db, session_id)
        current_step = session.state if session.state in FLOW else "contact_channel"
        data = dict(session.collected_data or {})
        cleaned = GuidedFlowService._validate_answer(current_step, answer, data)
        data[current_step] = cleaned
        GuidedFlowService._save_message(db, session, "in", json.dumps(cleaned, ensure_ascii=False) if isinstance(cleaned, dict) else str(cleaned), {"step": current_step})

        next_step = FLOW[current_step].next_step or "done"
        extra: dict[str, Any] = {}
        if current_step == "contact_value" and data.get("contact_channel") != "email":
            next_step = "contact_confirmed"
            extra["confirmation"] = GuidedFlowService._confirmation_cta(data.get("contact_channel"), str(cleaned), session.external_user_id)
        elif current_step == "contact_value":
            next_step = "project_context"
        elif current_step == "contact_confirmed":
            next_step = "project_context"

        session.collected_data = data
        session.state = next_step
        db.add(session)
        db.commit()
        db.refresh(session)

        if next_step != "done":
            payload = GuidedFlowService._step_payload(session, next_step, extra=extra)
            GuidedFlowService._save_message(db, session, "out", payload["message"], {"step": next_step})
            return payload

        estimate = calculate_estimate(data)
        offer_text = generate_offer_text(data, ANSWER_LABELS, estimate)
        offer = generate_offer_pdf_or_html(session.external_user_id, offer_text)
        result_message = GuidedFlowService._build_result(data, estimate)
        lead = await GuidedFlowService._save_lead(session, data, estimate, offer)
        GuidedFlowService._save_message(db, session, "out", result_message, {"step": "done", "lead": lead, "estimate": estimate, "offer": offer})
        return {
            "session_id": GuidedFlowService._public_session(session),
            "step": "done",
            "message": result_message,
            "options": [{"label": "Start again", "value": "restart"}],
            "input_type": None,
            "fields": [],
            "is_complete": True,
            "approval_status": "qualified",
            "is_hot": True,
            "estimate": estimate,
            "deposit_due_eur": int(estimate["price_eur"] / 2),
            "lead": lead,
            "offer": {"label": "Open offer", "url": offer.get("url"), "type": offer.get("type")},
            "collected_data": data,
        }
