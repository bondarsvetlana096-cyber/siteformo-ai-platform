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
            'ru': 'ru', 'рус': 'ru', 'русский': 'ru', 'russian': 'ru',
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
            'Привет! Я помогу быстро собрать заявку на сайт.\n\n'
            'Сначала выбери язык: RU / EN / DE / FR / IT / ES'
        )

    @staticmethod
    def _mode_prompt(lang: str) -> str:
        return (
            'Отлично. Выбери удобный формат:\n'
            '1) пришли 1-3 сайта, которые нравятся\n'
            '2) просто опиши, какой сайт хочешь\n\n'
            'Ответь: 1 или 2'
        )

    @staticmethod
    def _business_prompt() -> str:
        return 'Как называется бизнес или проект?' 

    @staticmethod
    def _site_type_prompt() -> str:
        return 'Какой сайт нужен? Например: landing, business site, portfolio, ecommerce.'

    @staticmethod
    def _reference_or_description_prompt(mode: str) -> str:
        if mode == 'reference_sites':
            return 'Пришли 1-3 ссылки на сайты, которые тебе нравятся. Можно в одном сообщении.'
        return 'Коротко опиши, какой сайт ты хочешь. Например: современный, дорогой, минималистичный, с акцентом на заявки.'

    @staticmethod
    def _features_prompt() -> str:
        return 'Нужны ли специальные функции? Напиши коротко, например: booking, catalog, cart, popup, animations. Если ничего особенного нет — напиши: no.'

    @staticmethod
    def _contact_prompt() -> str:
        return 'Оставь email или телефон, чтобы сохранить заявку и потом вернуться к ней.'

    @staticmethod
    def _completion_text(order_id: str, price: int, tier: str, reasoning: str, status: str, bypassed: bool) -> str:
        status_line = 'Оплата не требуется: для этого email заявка автоматически переведена дальше.' if bypassed else ('Статус: подтверждение оплаты ожидается.' if status != 'approved' else 'Статус: заявка уже одобрена.')
        return (
            f'Готово. Заявка создана.\n\n'
            f'Order ID: {order_id}\n'
            f'Ориентировочный уровень: {tier}\n'
            f'Ориентировочная стоимость: {price}€\n'
            f'Комментарий: {reasoning}\n'
            f'{status_line}\n\n'
            'Дальше можно править именно опросник и смотреть, где люди тормозят.'
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
                return ChatbotService._reply(db, session, 'Напиши один из вариантов: RU / EN / DE / FR / IT / ES')
            data['preferred_language'] = language
            session.collected_data = data
            session.state = ChatbotService.STATE_MODE
            db.commit()
            return ChatbotService._reply(db, session, ChatbotService._mode_prompt(language))

        if session.state == ChatbotService.STATE_MODE:
            if normalized not in {'1', '2'}:
                return ChatbotService._reply(db, session, 'Ответь 1 или 2.')
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
                    return ChatbotService._reply(db, session, 'Нужна хотя бы одна ссылка вида https://...')
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
                system_prompt='You rewrite chatbot confirmations for a website-sales intake flow. Keep it short, warm, and clear. Do not invent facts. Preserve order id, price, tier, reasoning, and status. Output in Russian.',
                user_text=fallback,
                fallback_text=fallback,
            )
            return ChatbotService._reply(db, session, polished)

        return ChatbotService._reply(db, session, 'Заявка уже создана. Напиши /restart чтобы пройти опрос ещё раз.')
