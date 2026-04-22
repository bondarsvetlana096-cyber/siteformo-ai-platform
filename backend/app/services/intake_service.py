from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.order import ChannelType, ClientProfile, HomepageConcept, Order, OrderStatus, SupportedLanguage
from app.services.pricing_service import PricingService
from app.services.reference_analysis_service import ReferenceAnalysisService


class IntakeService:
    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def find_or_create_client(db: Session, payload) -> ClientProfile:
        filters = []
        if payload.email:
            filters.append(ClientProfile.email == payload.email)
        if payload.phone:
            filters.append(ClientProfile.phone == payload.phone)
        if payload.telegram_handle:
            filters.append(ClientProfile.telegram_handle == payload.telegram_handle)

        client = None
        if filters:
            stmt = select(ClientProfile).where(or_(*filters))
            client = db.execute(stmt).scalar_one_or_none()

        if client:
            if getattr(payload, 'preferred_language', None) in SupportedLanguage.ALL:
                client.preferred_language = payload.preferred_language
            return client

        client = ClientProfile(
            email=payload.email,
            phone=payload.phone,
            telegram_handle=payload.telegram_handle,
            fingerprint=payload.fingerprint,
            ip_hash=payload.ip_hash,
            preferred_language=payload.preferred_language if getattr(payload, 'preferred_language', None) in SupportedLanguage.ALL else SupportedLanguage.EN,
        )
        db.add(client)
        db.flush()
        return client

    @staticmethod
    def find_recent_order(db: Session, client_id: str) -> Order | None:
        cutoff = IntakeService._utcnow() - timedelta(hours=96)
        stmt = select(Order).where(Order.client_id == client_id, Order.created_at >= cutoff).order_by(Order.created_at.desc())
        return db.execute(stmt).scalars().first()

    @staticmethod
    def _normalized_reference_sites(payload) -> list[dict]:
        refs = []
        for item in getattr(payload, 'reference_sites', []) or []:
            refs.append({'url': item.url, 'notes': item.notes})
        if getattr(payload, 'reference_site_url', None):
            refs.append({'url': payload.reference_site_url, 'notes': getattr(payload, 'reference_site_notes', None)})
        return refs[:3]

    @staticmethod
    def create_order(db: Session, payload) -> tuple[Order, bool, str | None]:
        client = IntakeService.find_or_create_client(db, payload)
        reused = IntakeService.find_recent_order(db, client.id)
        recommended_tier, estimated_price_eur, pricing_reasoning = PricingService.classify(payload)
        normalized_reference_sites = IntakeService._normalized_reference_sites(payload)
        reference_analysis_summary = ReferenceAnalysisService.summarize(
            normalized_reference_sites,
            fallback_url=getattr(payload, 'reference_site_url', None),
            fallback_notes=getattr(payload, 'reference_site_notes', None),
            desired_site_description=getattr(payload, 'desired_site_description', None),
        )

        order = Order(
            intake_mode=getattr(payload, 'intake_mode', 'describe'),
            desired_site_description=getattr(payload, 'desired_site_description', None),
            client_id=client.id,
            channel=payload.channel if payload.channel in {ChannelType.WEB, ChannelType.WHATSAPP, ChannelType.TELEGRAM} else ChannelType.WEB,
            status=OrderStatus.BRIEF_COMPLETED,
            business_name=payload.business_name,
            site_type=payload.site_type,
            source_url=payload.source_url,
            reference_site_url=getattr(payload, 'reference_site_url', None),
            reference_site_notes=getattr(payload, 'reference_site_notes', None),
            reference_sites=normalized_reference_sites,
            reference_analysis_summary=reference_analysis_summary,
            preferred_language=payload.preferred_language if getattr(payload, 'preferred_language', None) in SupportedLanguage.ALL else (client.preferred_language or SupportedLanguage.EN),
            brief_answers=payload.answers,
            pages_requested=payload.pages_requested,
            services_count=payload.services_count,
            has_service_pages=payload.has_service_pages,
            wants_leads=payload.wants_leads,
            ecommerce=payload.ecommerce,
            cart=payload.cart,
            catalog=payload.catalog,
            booking=payload.booking,
            advanced_integrations=payload.advanced_integrations,
            recommended_tier=recommended_tier,
            estimated_price_eur=estimated_price_eur,
            pricing_reasoning=pricing_reasoning,
            reused_context_from_order_id=reused.id if reused else None,
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        return order, bool(reused), (reused.id if reused else None)

    @staticmethod
    def save_concepts(db: Session, order: Order, concept_a: dict, concept_b: dict) -> None:
        db.add(HomepageConcept(order_id=order.id, concept_label='A', art_direction=concept_a.get('art_direction'), html_code=concept_a['html'], summary=concept_a.get('summary')))
        db.add(HomepageConcept(order_id=order.id, concept_label='B', art_direction=concept_b.get('art_direction'), html_code=concept_b['html'], summary=concept_b.get('summary')))
        order.status = OrderStatus.PENDING_PAYMENT_APPROVAL
        db.commit()
