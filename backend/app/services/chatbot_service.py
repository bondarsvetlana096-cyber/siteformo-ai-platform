from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation import ConversationMessage, ConversationSession


class ChatbotService:
    STATE_ACTIVE = "active"

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
            state=ChatbotService.STATE_ACTIVE,
            collected_data={},
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def _save_message(db: Session, session: ConversationSession, direction: str, text: str) -> None:
        db.add(ConversationMessage(session_id=session.id, direction=direction, text=text))
        db.commit()

    @staticmethod
    def process_message(db: Session, channel: str, external_user_id: str, text: str) -> str:
        session = ChatbotService._get_or_create_session(db, channel, external_user_id)
        incoming_text = (text or "").strip()
        ChatbotService._save_message(db, session, "in", incoming_text)

        reply = f"Ты написал: {incoming_text}" if incoming_text else "Ты написал пустое сообщение"

        ChatbotService._save_message(db, session, "out", reply)
        return reply
