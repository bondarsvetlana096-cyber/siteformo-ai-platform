from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        Index(
            "uq_payment_attempts_active_order", "order_id", unique=True,
            postgresql_where=text("status IN ('creating','checkout_created','pending')"),
            sqlite_where=text("status IN ('creating','checkout_created','pending')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    # The production migration enforces the cross-metadata visitor FK. Journey
    # models deliberately use separate metadata, so the ORM stores its UUID type
    # without duplicating Journey table ownership in Base.metadata.
    siteformo_visitor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    attempt_type: Mapped[str] = mapped_column(String(32), nullable=False, default="initial_deposit")
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255))
    checkout_url: Mapped[str | None] = mapped_column(Text)
    scope_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    base_package: Mapped[str] = mapped_column(String(32), nullable=False)
    base_package_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    expected_total_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_deposit_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_addons_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    legal_terms_version: Mapped[str] = mapped_column(String(128), nullable=False)
    legal_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="creating", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StripeWebhookEvent(Base):
    __tablename__ = "stripe_webhook_events"
    __table_args__ = (UniqueConstraint("stripe_event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_result: Mapped[str | None] = mapped_column(String(64))
    payment_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("payment_attempts.id", ondelete="SET NULL"))
    order_id: Mapped[str | None] = mapped_column(String(36))
