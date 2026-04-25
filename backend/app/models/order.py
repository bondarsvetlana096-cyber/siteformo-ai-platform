from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class OrderStatus:
    DRAFT = 'draft'
    BRIEF_COMPLETED = 'brief_completed'
    CONCEPTS_READY = 'concepts_ready'
    PENDING_PAYMENT_APPROVAL = 'pending_payment_approval'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    FINAL_READY = 'final_ready'
    DELIVERED = 'delivered'


class ChannelType:
    WEB = 'web'
    WHATSAPP = 'whatsapp'
    TELEGRAM = 'telegram'


class SupportedLanguage:
    EN = 'en'
    DE = 'de'
    FR = 'fr'
    IT = 'it'
    ES = 'es'

    ALL = {EN, DE, FR, IT, ES}


class PricingTier:
    STARTER = 'starter'
    BUSINESS = 'business'
    PREMIUM = 'premium'


class ClientProfile(Base):
    __tablename__ = 'client_profiles'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(64), index=True)
    telegram_handle: Mapped[str | None] = mapped_column(String(128), index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(255), index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    preferred_language: Mapped[str] = mapped_column(String(8), default=SupportedLanguage.EN, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    orders: Mapped[list['Order']] = relationship(back_populates='client')


class Order(Base):
    __tablename__ = 'orders'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id: Mapped[str] = mapped_column(ForeignKey('client_profiles.id'), index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default=OrderStatus.DRAFT)
    business_name: Mapped[str | None] = mapped_column(String(255))
    site_type: Mapped[str | None] = mapped_column(String(128))
    source_url: Mapped[str | None] = mapped_column(Text)
    intake_mode: Mapped[str | None] = mapped_column(String(32), default='describe')
    desired_site_description: Mapped[str | None] = mapped_column(Text)
    reference_site_url: Mapped[str | None] = mapped_column(Text)
    reference_site_notes: Mapped[str | None] = mapped_column(Text)
    reference_sites: Mapped[list | None] = mapped_column(JSON)
    reference_analysis_summary: Mapped[str | None] = mapped_column(Text)
    preferred_language: Mapped[str] = mapped_column(String(8), default=SupportedLanguage.EN, nullable=False)
    brief_answers: Mapped[dict | None] = mapped_column(JSON)
    pages_requested: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    services_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    has_service_pages: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    wants_leads: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ecommerce: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cart: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    catalog: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    booking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    advanced_integrations: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recommended_tier: Mapped[str] = mapped_column(String(32), default=PricingTier.STARTER, nullable=False)
    estimated_price_eur: Mapped[int] = mapped_column(Integer, default=600, nullable=False)
    pricing_reasoning: Mapped[str | None] = mapped_column(Text)
    reused_context_from_order_id: Mapped[str | None] = mapped_column(String(36), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    client: Mapped['ClientProfile'] = relationship(back_populates='orders')
    concepts: Mapped[list['HomepageConcept']] = relationship(back_populates='order', cascade='all, delete-orphan')
    final_packages: Mapped[list['FinalPackage']] = relationship(back_populates='order', cascade='all, delete-orphan')

    
    def packages(self) -> list['FinalPackage']:
        return self.final_packages


class HomepageConcept(Base):
    __tablename__ = 'homepage_concepts'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    concept_label: Mapped[str] = mapped_column(String(16), nullable=False)
    art_direction: Mapped[str | None] = mapped_column(String(255))
    html_code: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    order: Mapped['Order'] = relationship(back_populates='concepts')


class FinalPackage(Base):
    __tablename__ = 'final_packages'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), index=True)
    selected_concept_label: Mapped[str] = mapped_column(String(16), nullable=False)
    divi_html: Mapped[str] = mapped_column(Text, nullable=False)
    brief_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    order: Mapped['Order'] = relationship(back_populates='final_packages')
