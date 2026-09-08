from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class AssistantBase(DeclarativeBase):
    """Separate metadata keeps startup create_all from owning Assistant tables."""


class AssistantVisitor(AssistantBase):
    __tablename__ = "assistant_visitors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    conversations: Mapped[list["AssistantConversation"]] = relationship(back_populates="visitor")


class AssistantConversation(AssistantBase):
    __tablename__ = "assistant_conversations"
    __table_args__ = (
        Index(
            "uq_assistant_active_conversation_per_visitor",
            "visitor_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        CheckConstraint("status in ('active', 'closed')", name="ck_assistant_conversation_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assistant_visitors.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    visitor: Mapped[AssistantVisitor] = relationship(back_populates="conversations")
    messages: Mapped[list["AssistantMessage"]] = relationship(back_populates="conversation")


class AssistantMessage(AssistantBase):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        CheckConstraint("role in ('user', 'assistant', 'system')", name="ck_assistant_message_role"),
        CheckConstraint("status in ('completed', 'failed')", name="ck_assistant_message_status"),
        Index("uq_assistant_message_client_turn_role", "conversation_id", "client_message_id", "role", unique=True),
        Index("ix_assistant_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    client_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    page_hint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    conversation: Mapped[AssistantConversation] = relationship(back_populates="messages")
