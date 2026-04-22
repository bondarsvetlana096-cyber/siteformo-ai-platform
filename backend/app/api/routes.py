from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.order import FinalPackage, HomepageConcept, Order, OrderStatus
from app.schemas.order import ApprovalResponse, IntakePayload, IntakeResponse
from app.services.approval_service import ApprovalService
from app.services.generation_service import GenerationService
from app.services.intake_service import IntakeService
from app.services.launch_link_service import LaunchLinkService

router = APIRouter()


@router.get('/health')
def health() -> dict:
    return {'ok': True, 'service': 'siteformo-ai-sales-platform'}


@router.post('/api/intake', response_model=IntakeResponse)
def intake(payload: IntakePayload, db: Session = Depends(get_db)):
    order, reused, reused_order_id = IntakeService.create_order(db, payload)
    reused_context = None
    if reused_order_id:
        old_order = db.get(Order, reused_order_id)
        reused_context = old_order.brief_answers if old_order else None

    concept_a, concept_b = GenerationService.generate_two_concepts(order, reused_context=reused_context)
    IntakeService.save_concepts(db, order, concept_a, concept_b)
    if LaunchLinkService.should_bypass_payment_approval(getattr(payload, "email", None)):
        order.status = OrderStatus.APPROVED
        order.approved_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(order)

    return IntakeResponse(
        client_id=order.client_id,
        order_id=order.id,
        reused_context=reused,
        reused_order_id=reused_order_id,
        recommended_tier=order.recommended_tier,
        estimated_price_eur=order.estimated_price_eur,
        pricing_reasoning=order.pricing_reasoning or '',
        preferred_language=order.preferred_language,
        status=order.status,
    )


@router.get('/api/orders/{order_id}')
def get_order(order_id: str, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail='Order not found')

    concepts = db.query(HomepageConcept).filter(HomepageConcept.order_id == order.id).all()
    packages = db.query(FinalPackage).filter(FinalPackage.order_id == order.id).all()

    return {
        'order_id': order.id,
        'status': order.status,
        'business_name': order.business_name,
        'intake_mode': order.intake_mode,
        'desired_site_description': order.desired_site_description,
        'preferred_language': order.preferred_language,
        'reference_site_url': order.reference_site_url,
        'reference_site_notes': order.reference_site_notes,
        'reference_sites': order.reference_sites,
        'reference_analysis_summary': order.reference_analysis_summary,
        'recommended_tier': order.recommended_tier,
        'estimated_price_eur': order.estimated_price_eur,
        'pricing_reasoning': order.pricing_reasoning,
        'reused_context_from_order_id': order.reused_context_from_order_id,
        'concepts': [
            {
                'id': c.id,
                'label': c.concept_label,
                'art_direction': c.art_direction,
                'summary': c.summary,
                'html_code': c.html_code,
            }
            for c in concepts
        ],
        'packages': [
            {'id': p.id, 'selected_concept_label': p.selected_concept_label, 'created_at': str(p.created_at)}
            for p in packages
        ],
    }


@router.get('/api/orders/{order_id}/decision', response_model=ApprovalResponse)
def owner_decision(order_id: str, action: str = Query(..., pattern='^(approve|reject)$'), token: str = Query(...), db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail='Order not found')
    if not ApprovalService.verify(order_id, action, token):
        raise HTTPException(status_code=403, detail='Invalid token')

    now = datetime.now(timezone.utc)
    if action == 'approve':
        order.status = OrderStatus.APPROVED
        order.approved_at = now
    else:
        order.status = OrderStatus.REJECTED
        order.rejected_at = now
    db.commit()

    return ApprovalResponse(order_id=order.id, status=order.status, message=f'Order {action}d')


@router.post('/api/orders/{order_id}/decision/manual', response_model=ApprovalResponse)
def owner_decision_manual(order_id: str, action: str = Query(..., pattern='^(approve|reject)$'), db: Session = Depends(get_db)):
    if not settings.allow_manual_decision_without_token:
        raise HTTPException(status_code=403, detail='Manual decision is disabled')

    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail='Order not found')

    now = datetime.now(timezone.utc)
    if action == 'approve':
        order.status = OrderStatus.APPROVED
        order.approved_at = now
    else:
        order.status = OrderStatus.REJECTED
        order.rejected_at = now
    db.commit()

    return ApprovalResponse(order_id=order.id, status=order.status, message=f'Manual order {action}d')


@router.post('/api/orders/{order_id}/finalize')
def finalize_order(order_id: str, selected_concept_label: str = Query(..., pattern='^(A|B)$'), db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail='Order not found')
    if order.status != OrderStatus.APPROVED:
        raise HTTPException(status_code=409, detail='Order is not approved')

    concept = db.query(HomepageConcept).filter(HomepageConcept.order_id == order.id, HomepageConcept.concept_label == selected_concept_label).first()
    if not concept:
        raise HTTPException(status_code=404, detail='Concept not found')

    db.query(HomepageConcept).filter(HomepageConcept.order_id == order.id).update({'is_selected': False})
    concept.is_selected = True

    divi_html, brief_markdown = GenerationService.build_final_divi_package(order, concept.html_code)
    pkg = FinalPackage(
        order_id=order.id,
        selected_concept_label=selected_concept_label,
        divi_html=divi_html,
        brief_markdown=brief_markdown,
        notes='Ready for manual review in Divi 5',
    )
    order.status = OrderStatus.FINAL_READY
    db.add(pkg)
    db.commit()
    db.refresh(pkg)

    return {
        'order_id': order.id,
        'status': order.status,
        'package_id': pkg.id,
        'selected_concept_label': selected_concept_label,
    }
