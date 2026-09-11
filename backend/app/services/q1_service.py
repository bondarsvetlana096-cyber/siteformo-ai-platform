from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.journey.models import SiteFormoJourneyProject, SiteFormoVisitor
from app.models.order import ClientProfile, Order, OrderStatus
from app.schemas.order import Q1V2Payload


class Q1OwnershipError(RuntimeError):
    pass


def project_binding(db: Session, visitor: SiteFormoVisitor, order_id: str) -> SiteFormoJourneyProject:
    binding = db.execute(select(SiteFormoJourneyProject).where(SiteFormoJourneyProject.order_id == order_id)).scalar_one_or_none()
    if binding is None or binding.siteformo_visitor_id != visitor.id or not binding.is_current:
        raise Q1OwnershipError("Project is not owned by this Journey")
    return binding


def ensure_project(
    db: Session,
    visitor: SiteFormoVisitor,
    requested_order_id: str | None = None,
    handoff_id: str | None = None,
    start_new_project: bool = False,
) -> tuple[Order, bool]:
    # Serialize project-start decisions for one Journey. The partial unique index
    # is the database-level invariant; this row lock prevents routine races.
    db.execute(select(SiteFormoVisitor).where(SiteFormoVisitor.id == visitor.id).with_for_update()).scalar_one()
    current = db.execute(
        select(SiteFormoJourneyProject).where(
            SiteFormoJourneyProject.siteformo_visitor_id == visitor.id,
            SiteFormoJourneyProject.is_current.is_(True),
        )
    ).scalar_one_or_none()

    if requested_order_id:
        order = db.get(Order, requested_order_id)
        if order is None:
            raise Q1OwnershipError("Project does not exist")
        existing = db.execute(select(SiteFormoJourneyProject).where(SiteFormoJourneyProject.order_id == requested_order_id)).scalar_one_or_none()
        if existing is None:
            raise Q1OwnershipError("Project has no verified Journey binding")
        if existing.siteformo_visitor_id != visitor.id:
            raise Q1OwnershipError("Project is owned by another Journey")
        if existing.is_current and order.status == OrderStatus.DRAFT and not start_new_project:
            return order, False
        if order.status == OrderStatus.DRAFT and not existing.is_current:
            raise Q1OwnershipError("Project is no longer the current draft")

    if current and not start_new_project:
        order = db.get(Order, current.order_id)
        if order and order.status == OrderStatus.DRAFT:
            return order, False

    if current:
        current.is_current = False

    client = ClientProfile(preferred_language="en")
    order = Order(client=client, channel="web", status=OrderStatus.DRAFT, brief_answers={})
    db.add_all([client, order])
    db.flush()
    db.add(SiteFormoJourneyProject(
        siteformo_visitor_id=visitor.id,
        order_id=order.id,
        handoff_id=handoff_id,
        is_current=True,
    ))
    db.commit()
    db.refresh(order)
    return order, True


def save_q1(db: Session, visitor: SiteFormoVisitor, order_id: str, payload: Q1V2Payload) -> bool:
    project_binding(db, visitor, order_id)
    order = db.get(Order, order_id)
    if order is None or order.status != OrderStatus.DRAFT:
        raise Q1OwnershipError("Project does not exist")
    serialized = payload.model_dump(mode="json")
    previous = dict(order.brief_answers or {}).get("q1_v2")
    if previous == serialized:
        return True

    brief = dict(order.brief_answers or {})
    brief.update({
        "q1_v2": serialized,
        "project_class_intent": payload.project_class_intent,
        "preferred_contact": payload.preferred_contact.model_dump(mode="json"),
        "current_website_url": payload.existing_website.url,
        "project_type": {"one_page": "landing", "business_site": "business", "ecommerce_catalog": "store", "online_service_platform": "platform", "unsure": "not_sure"}[payload.project_class_intent],
    })
    order.brief_answers = brief
    order.site_type = payload.project_class_intent
    order.source_url = payload.existing_website.url
    order.desired_site_description = payload.project_class_intent
    if payload.preferred_contact.channel == "email":
        order.client.email = payload.preferred_contact.normalized_value
    elif payload.preferred_contact.channel == "whatsapp":
        order.client.phone = payload.preferred_contact.normalized_value
    else:
        order.client.telegram_handle = payload.preferred_contact.normalized_value
    db.commit()
    return False
