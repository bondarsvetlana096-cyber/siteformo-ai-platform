from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RequestStatus:
    CREATED = 'created'
    QUEUED = 'queued'
    PROCESSING = 'processing'
    GENERATED = 'generated'
    PUBLISHED = 'published'
    EXPIRED = 'expired'
    DELETED = 'deleted'
    FAILED = 'failed'
    LIMIT_REACHED = 'limit_reached'
    COMPLETED = 'completed'


class JobStatus:
    PENDING = 'pending'
    PROCESSING = 'processing'
    DONE = 'done'
    FAILED = 'failed'


class RequestType:
    REDESIGN = 'redesign'
    CREATE = 'create'


class ContactType:
    EMAIL = 'email'
    TELEGRAM = 'telegram'
    WHATSAPP = 'whatsapp'
    MESSENGER = 'messenger'


class Request(Base):
    __tablename__ = 'requests'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    contact_type: Mapped[str] = mapped_column(String(32), nullable=False, default=ContactType.EMAIL, index=True)
    contact_value: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    contact_confirmation_token: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    contact_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    inbound_message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    outbound_message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo_token: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    demo_storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    master_storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    user_identity_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    demo_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    demo_cta_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    main_form_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    main_form_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    payment_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    payment_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_follow_up_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_follow_up_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    follow_up_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    assets: Mapped[list['DemoAsset']] = relationship(back_populates='request', cascade='all, delete-orphan')


class UserUsage(Base):
    __tablename__ = 'user_usage'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    user_identity_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    free_attempts_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_request_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_request_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Job(Base):
    __tablename__ = 'jobs'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=JobStatus.PENDING, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class DemoAsset(Base):
    __tablename__ = 'demo_assets'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('requests.id', ondelete='CASCADE'), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    request: Mapped['Request'] = relationship(back_populates='assets')


class EventLog(Base):
    __tablename__ = 'event_logs'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('requests.id', ondelete='CASCADE'), nullable=True, index=True)
    event_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
