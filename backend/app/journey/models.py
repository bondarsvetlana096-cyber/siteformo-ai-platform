from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class JourneyBase(DeclarativeBase):
    """Metadata owned by the explicit Journey/Assistant schema installer only."""


class SiteFormoVisitor(JourneyBase):
    __tablename__ = "siteformo_visitors"
    __table_args__ = (UniqueConstraint("credential_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SiteFormoJourneyProject(JourneyBase):
    """Bind one project/order to its owning visitor without conflating the identities."""

    __tablename__ = "siteformo_journey_projects"
    __table_args__ = (
        UniqueConstraint("order_id"),
        Index(
            "uq_siteformo_journey_projects_current_visitor",
            "siteformo_visitor_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    siteformo_visitor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("siteformo_visitors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    handoff_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
