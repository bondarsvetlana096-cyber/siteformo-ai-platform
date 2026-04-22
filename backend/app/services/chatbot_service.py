from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation import ConversationMessage, ConversationSession
from app.models.order import ChannelType, Order, OrderStatus
from app.schemas.order import IntakePayload, ReferenceSiteInput
from app.services.generation_service import GenerationService
from app.services.intake_service import IntakeService
from app.services.launch_link_service import LaunchLinkService
from app.services.openai_service import OpenAIService

URL_RE = re.compile(r'https?://\S+', re.I)


class ChatbotService:
    STATE_START = 'start'
    STATE_LANGUAGE = 'language'
    STATE_MODE = 'mode'
    STATE_BUSINESS = 'business'
    STATE_SITE_TYPE = 'site_type'
    STATE_REFERENCE_OR_DESCRIPTION = 'reference_or_description'
    STATE_FEATURES = 'features'
    STATE_CONTACT = 'contact'
    STATE_DONE = 'done'

    @staticmethod
    def _get_or_create_session(db: Session, channel: str, external_user_id: str) -> ConversationSession:
        stmt = select(ConversationSession).where(
            ConversationSession.channel == channel,
            ConversationSession.external_user_id == external_user_id,
        )
        session = db.execute(stmt).scalar_one_or_none()
        if session:
            return session
        session = ConversationSession(channel=channel, external_user_id=external_user_id, state=ChatbotService.STATE_START, collected_data={})
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def _save_message(db: Session, session: ConversationSession, direction: str, text: str, metadata: dict[str, Any] | None = None) -> None:
        db.add(ConversationMessage(session_id=session.id, direction=direction, text=text, metadata_json=metadata))
        db.commit()

    @staticmethod
    def _reply(db: Session, session: ConversationSession, text: str) -> str:
        ChatbotService._save_message(db, session, 'out', text)
        return text

    @staticmethod
    def _normalize_language(text: str) -> str | None:
        value = text.strip().lower()
        mapping = {
            'ru': 'en', 'рус': 'en', 'русский': 'en', 'russian': 'en',
            'en': 'en', 'english': 'en',
            'de': 'de', 'german': 'de',
            'fr': 'fr', 'french': 'fr',
            'it': 'it', 'italian': 'it',
            'es': 'es', 'spanish': 'es',
        }
        return mapping.get(value)

    @staticmethod
    def _initial_prompt() -> str:
        return (
            'Hi! I will help you quickly create a website request.\n\n'
            'Choose a language: EN / DE / FR / IT / ES\n'
            'Note: the current bot flow replies in English.'
        )

    @staticmethod
    def _mode_prompt(lang: str) -> str:
        return (
            'Great. Choose the easiest format:\n'
            '1) send 1-3 websites you like\n'
            '2) describe the site you want\n\n'
            'Reply with: 1 or 2'
        )

    @staticmethod
    def _business_prompt() -> str:
        return 'What is the name of your business or project?'

    @staticmethod
    def _site_type_prompt() -> str:
        return 'What kind of website do you need? Example: landing page, business site, portfolio, ecommerce.'

    @staticmethod
    def _reference_or_description_prompt(mode: str) -> str:
        if mode == 'reference_sites':
            return 'Send 1-3 website links you like. You can paste them in one message.'
        return 'Briefly describe the website you want. Example: modern, premium, minimalist, focused on leads.'

    @staticmethod
    def _features_prompt() -> str:
        return 'Do you need any special features? Example: booking, catalog, cart, popup, animations. If not, reply: no.'

    @staticmethod
    def _contact_prompt() -> str:
        return 'Leave your email or phone number so I can save your request and you can return to it later.'

    @staticmethod
    def _completion_text(order_id: str, price: int, tier: str, reasoning: str, status: str, bypassed: bool) -> str:
        status_line = (
            'Payment is not required for this email. The request has been moved forward automatically.'
            if bypassed
            else ('Status: payment approval is pending.' if status != 'approved' else 'Status: the request is already approved.')
        )
        return (
            f'Done. Your request has been created.\n\n'
            f'Order ID: {order_id}\n'
            f'Recommended tier: {tier}\n'
            f'Estimated price: {price} EUR\n'
            f'Pricing note: {reasoning}\n'
            f'{status_line}\n\n'
            'Next step: we can improve the intake flow and connect AI generation.'
        )

    @staticmethod
    def _extract_urls(text: str) -> list[str]:
        return URL_RE.findall(text or '')[:3]

    @staticmethod
    def _feature_flags(text: str) -> dict[str, bool]:
        raw = (text or '').lower()
        return {
            'ecommerce': 'ecommerce' in raw or 'shop' in raw or 'store' in raw,
            'cart': 'cart' in raw,
            'catalog': 'catalog' in raw,
            'booking': 'booking' in raw,
            'advanced_integrations': 'integration' in raw or 'crm' in raw or 'api' in raw,
        }

    @staticmethod
    def _build_intake_payload(channel: str, data: dict[str, Any], external_user_id: str) -> IntakePayload:
        refs = [ReferenceSiteInput(url=url) for url in data.get('reference_sites', [])]
        flags = ChatbotService._feature_flags(data.get('features', ''))
        contact = data.get('contact', '')
        email = contact if '@' in contact else None
        phone = contact if email is None else None
        preferred_language = data.get('preferred_language', 'en')
        if preferred_language == 'ru':
            preferred_language = 'en'
        return IntakePayload(
            channel=channel,
            intake_mode=data.get('intake_mode', 'describe'),
            preferred_language=preferred_language,
            email=email,
            phone=phone,
            telegram_handle=external_user_id if channel == ChannelType.TELEGRAM else None,
            business_name=data.get('business_name'),
            site_type=data.get('site_type'),
            desired_site_description=data.get('desired_site_description'),
            reference_sites=refs,
            answers={
                'features': data.get('features'),
                'source': 'chatbot',
            },
            pages_requested=1,
            services_count=1,
            wants_leads=True,
            **flags,
        )

    @staticmethod
    def process_message(db: Session, channel: str, external_user_id: str, text: str) -> str:
        session = ChatbotService._get_or_create_session(db, channel, external_user_id)
        ChatbotService._save_message(db, session, 'in', text)

        normalized = (text or '').strip()
        lower = normalized.lower()

        if lower in {'start', 'restart', '/restart'} or lower.startswith('/start'):
            session.state = ChatbotService.STATE_LANGUAGE
            session.collected_data = {}
            db.commit()
            return ChatbotService._reply(db, session, ChatbotService._initial_prompt())

        if session.state == ChatbotService.STATE_START:
            session.state = ChatbotService.STATE_LANGUAGE
            db.commit()
            return ChatbotService._reply(db, session, ChatbotService._initial_prompt())

        data = session.collected_data or {}

        if session.state == ChatbotService.STATE_LANGUAGE:
            language = ChatbotService._normalize_language(normalized)
            if not language:
                return ChatbotService._reply(db, session, 'Please reply with one of these options: EN / DE / FR / IT / ES')
            data['preferred_language'] = language
            session.collected_data = data
            session.state = ChatbotService.STATE_MODE
            db.commit()
            return ChatbotService._reply(db, session, ChatbotService._mode_prompt(language))

        if session.state == ChatbotService.STATE_MODE:
            if normalized not in {'1', '2'}:
                return ChatbotService._reply(db, session, 'Please reply with 1 or 2.')
            data['intake_mode'] = 'reference_sites' if normalized == '1' else 'describe'
            session.collected_data = data
            session.state = ChatbotService.STATE_BUSINESS
            db.commit()
            return ChatbotService._reply(db, session, ChatbotService._business_prompt())

        if session.state == ChatbotService.STATE_BUSINESS:
            data['business_name'] = normalized
            session.collected_data = data
            session.state = ChatbotService.STATE_SITE_TYPE
            db.commit()
            return ChatbotService._reply(db, session, ChatbotService._site_type_prompt())

        if session.state == ChatbotService.STATE_SITE_TYPE:
            data['site_type'] = normalized
            session.collected_data = data
            session.state = ChatbotService.STATE_REFERENCE_OR_DESCRIPTION
            db.commit()
            return ChatbotService._reply(db, session, ChatbotService._reference_or_description_prompt(data.get('intake_mode', 'describe')))

        if session.state == ChatbotService.STATE_REFERENCE_OR_DESCRIPTION:
            if data.get('intake_mode') == 'reference_sites':
                urls = ChatbotService._extract_urls(normalized)
                if not urls:
                    return ChatbotService._reply(db, session, 'Please send at least one link like https://example.com')
                data['reference_sites'] = urls
            else:
                data['desired_site_description'] = normalized
            session.collected_data = data
            session.state = ChatbotService.STATE_FEATURES
            db.commit()
            return ChatbotService._reply(db, session, ChatbotService._features_prompt())

        if session.state == ChatbotService.STATE_FEATURES:
            data['features'] = normalized
            session.collected_data = data
            session.state = ChatbotService.STATE_CONTACT
            db.commit()
            return ChatbotService._reply(db, session, ChatbotService._contact_prompt())

        if session.state == ChatbotService.STATE_CONTACT:
            data['contact'] = normalized
            payload = ChatbotService._build_intake_payload(channel=channel, data=data, external_user_id=external_user_id)
            order, reused, reused_order_id = IntakeService.create_order(db, payload)
            reused_context = None
            if reused_order_id:
                old_order = db.get(Order, reused_order_id)
                reused_context = old_order.brief_answers if old_order else None
            concept_a, concept_b = GenerationService.generate_two_concepts(order, reused_context=reused_context)
            IntakeService.save_concepts(db, order, concept_a, concept_b)
            bypassed = LaunchLinkService.should_bypass_payment_approval(payload.email)
            if bypassed:
                order.status = OrderStatus.APPROVED
                order.approved_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(order)
            session.last_order_id = order.id
            session.collected_data = data
            session.state = ChatbotService.STATE_DONE
            db.commit()
            fallback = ChatbotService._completion_text(order.id, order.estimated_price_eur, order.recommended_tier, order.pricing_reasoning or '', order.status, bypassed)
            polished = OpenAIService.refine_reply(
                system_prompt='You rewrite chatbot confirmations for a website sales intake flow. Keep it short, warm, and clear. Do not invent facts. Preserve order id, price, tier, reasoning, and status. Output in English only.',
                user_text=fallback,
                fallback_text=fallback,
            )
            return ChatbotService._reply(db, session, polished)

        return ChatbotService._reply(db, session, 'A request has already been created. Send /restart to start a new one.')
