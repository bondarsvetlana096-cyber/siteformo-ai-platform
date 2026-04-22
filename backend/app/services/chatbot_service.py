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

    generation_service = GenerationService()

    @staticmethod
    def _get_or_create_session(db: Session, channel: str, external_user_id: str) -> ConversationSession:
        stmt = select(ConversationSession).where(
            ConversationSession.channel == channel,
            ConversationSession.external_user_id == external_user_id,
        )
        session = db.execute(stmt).scalar_one_or_none()

        if session:
            return session

        session = ConversationSession(
            channel=channel,
            external_user_id=external_user_id,
            state=ChatbotService.STATE_START,
            collected_data={}
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def _save_message(db: Session, session: ConversationSession, direction: str, text: str) -> None:
        db.add(ConversationMessage(
            session_id=session.id,
            direction=direction,
            text=text
        ))
        db.commit()

    @staticmethod
    def _reply(db: Session, session: ConversationSession, text: str) -> str:
        ChatbotService._save_message(db, session, 'out', text)
        return text

    @staticmethod
    def _initial_prompt() -> str:
        return (
            "Hi! I will help you generate a website concept.\n\n"
            "Choose language: EN"
        )

    @staticmethod
    def _mode_prompt() -> str:
        return (
            "Choose:\n"
            "1 - send websites\n"
            "2 - describe your idea"
        )

    @staticmethod
    def _extract_urls(text: str) -> list[str]:
        return URL_RE.findall(text or '')[:3]

    @staticmethod
    def process_message(db: Session, channel: str, external_user_id: str, text: str) -> str:

        session = ChatbotService._get_or_create_session(db, channel, external_user_id)
        ChatbotService._save_message(db, session, 'in', text)

        text = (text or '').strip().lower()
        data = session.collected_data or {}

        # START
        if text.startswith('/start') or text in {'start'}:
            session.state = ChatbotService.STATE_MODE
            db.commit()
            return ChatbotService._reply(db, session, ChatbotService._mode_prompt())

        # MODE
        if session.state == ChatbotService.STATE_MODE:
            if text not in {'1', '2'}:
                return ChatbotService._reply(db, session, "Reply 1 or 2")

            data['mode'] = 'reference' if text == '1' else 'describe'
            session.collected_data = data
            session.state = ChatbotService.STATE_REFERENCE_OR_DESCRIPTION
            db.commit()

            if text == '1':
                return ChatbotService._reply(db, session, "Send 1-3 website links")
            else:
                return ChatbotService._reply(db, session, "Describe your website idea")

        # INPUT
        if session.state == ChatbotService.STATE_REFERENCE_OR_DESCRIPTION:

            if data.get('mode') == 'reference':
                urls = ChatbotService._extract_urls(text)
                if not urls:
                    return ChatbotService._reply(db, session, "Send valid links")

                data['reference_sites'] = urls
                user_input = f"Reference sites: {urls}"

            else:
                data['description'] = text
                user_input = text

            session.collected_data = data

            # 🔥 AI GENERATION HERE
            result = ChatbotService.generation_service.generate_site_concept(
                user_input=user_input,
                mode=data.get('mode', 'describe')
            )

            session.state = ChatbotService.STATE_DONE
            db.commit()

            return ChatbotService._reply(db, session, result)

        # DONE
        return ChatbotService._reply(
            db,
            session,
            "Send /start to create another concept"
        )