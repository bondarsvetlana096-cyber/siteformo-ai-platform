from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.models import AssistantConversation, AssistantMessage, AssistantVisitor
from app.assistant.openai_adapter import AssistantOpenAIAdapter
from app.journey.models import SiteFormoVisitor

HISTORY_LIMIT = 40
PLACEHOLDER_INSTRUCTIONS = (
    "You are the SiteFormo Assistant. This is an infrastructure-only placeholder. "
    "Reply briefly and do not claim to perform actions or determine business state."
)


class AssistantOwnershipError(RuntimeError):
    pass


def resolve_visitor_and_conversation(
    db: Session, siteformo_visitor: SiteFormoVisitor
) -> tuple[AssistantVisitor, AssistantConversation]:
    visitor = db.execute(
        select(AssistantVisitor).where(
            AssistantVisitor.siteformo_visitor_id == siteformo_visitor.id
        )
    ).scalar_one_or_none()
    if visitor is None:
        visitor = AssistantVisitor(
            siteformo_visitor_id=siteformo_visitor.id,
            credential_hash=siteformo_visitor.credential_hash,
        )
        db.add(visitor)
        db.flush()

    conversation = db.execute(
        select(AssistantConversation)
        .where(
            AssistantConversation.visitor_id == visitor.id,
            AssistantConversation.status == "active",
        )
        .order_by(AssistantConversation.created_at.desc())
    ).scalars().first()
    if conversation is None:
        conversation = AssistantConversation(visitor_id=visitor.id, status="active")
        db.add(conversation)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        visitor = db.execute(
            select(AssistantVisitor).where(
                AssistantVisitor.siteformo_visitor_id == siteformo_visitor.id
            )
        ).scalar_one()
        conversation = db.execute(
            select(AssistantConversation).where(
                AssistantConversation.visitor_id == visitor.id,
                AssistantConversation.status == "active",
            )
        ).scalar_one()
    db.refresh(visitor)
    db.refresh(conversation)
    return visitor, conversation


def require_owned_conversation(
    db: Session, siteformo_visitor: SiteFormoVisitor, conversation_id: uuid.UUID | None = None
) -> tuple[AssistantVisitor, AssistantConversation]:
    visitor = db.execute(
        select(AssistantVisitor).where(
            AssistantVisitor.siteformo_visitor_id == siteformo_visitor.id
        )
    ).scalar_one_or_none()
    if visitor is None:
        raise AssistantOwnershipError("Assistant session is invalid")
    query = select(AssistantConversation).where(
        AssistantConversation.visitor_id == visitor.id,
        AssistantConversation.status == "active",
    )
    if conversation_id is not None:
        query = query.where(AssistantConversation.id == conversation_id)
    conversation = db.execute(query).scalars().first()
    if conversation is None:
        raise AssistantOwnershipError("Conversation was not found")
    return visitor, conversation


def load_history(db: Session, conversation_id: uuid.UUID) -> list[AssistantMessage]:
    rows = db.execute(
        select(AssistantMessage)
        .where(
            AssistantMessage.conversation_id == conversation_id,
            AssistantMessage.status == "completed",
        )
        .order_by(AssistantMessage.created_at.desc())
        .limit(HISTORY_LIMIT)
    ).scalars().all()
    return list(reversed(rows))


async def stream_message(
    db: Session,
    conversation: AssistantConversation,
    content: str,
    page_hint: str | None,
    client_message_id: uuid.UUID,
    adapter: AssistantOpenAIAdapter,
) -> AsyncIterator[str]:
    previous = load_history(db, conversation.id)
    existing_user = db.execute(
        select(AssistantMessage).where(
            AssistantMessage.conversation_id == conversation.id,
            AssistantMessage.client_message_id == client_message_id,
            AssistantMessage.role == "user",
        )
    ).scalar_one_or_none()
    existing_assistant = db.execute(
        select(AssistantMessage).where(
            AssistantMessage.conversation_id == conversation.id,
            AssistantMessage.client_message_id == client_message_id,
            AssistantMessage.role == "assistant",
        )
    ).scalar_one_or_none()
    if existing_user is not None and existing_user.content != content:
        raise AssistantOwnershipError("Client message identifier was already used")
    if existing_assistant is not None:
        yield existing_assistant.content
        return
    if existing_user is None:
        user_message = AssistantMessage(
            conversation_id=conversation.id,
            role="user",
            status="completed",
            content=content,
            page_hint=page_hint,
            client_message_id=client_message_id,
        )
        db.add(user_message)
        db.commit()
    else:
        previous = [row for row in previous if row.id != existing_user.id]

    input_items = [{"role": "system", "content": PLACEHOLDER_INSTRUCTIONS}]
    input_items.extend({"role": row.role, "content": row.content} for row in previous)
    input_items.append({"role": "user", "content": content})

    completed_parts: list[str] = []
    async for chunk in adapter.stream_response(input_items):
        completed_parts.append(chunk)
        yield chunk

    completed = "".join(completed_parts).strip()
    if completed:
        db.add(
            AssistantMessage(
                conversation_id=conversation.id,
                role="assistant",
                status="completed",
                content=completed,
                client_message_id=client_message_id,
            )
        )
        db.commit()
