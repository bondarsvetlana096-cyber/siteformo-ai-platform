from __future__ import annotations

import os

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.journey.service import JourneyCredentialError, require_journey_visitor
from app.schemas.payment import (
    CheckoutRequestV2,
    CheckoutResponseV2,
    PaymentConfirmationRequest,
    PaymentConfirmationResponse,
    PaymentStatusResponse,
)
from app.services.payment_boundary_service import (
    PaymentBoundaryError,
    confirm_brief_and_legal,
    create_checkout,
    payment_status,
)
from app.services.q1_service import Q1OwnershipError


router = APIRouter(prefix="/api/orders", tags=["payment-boundary-v2"])
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


def _visitor(request: Request, db: Session, credential: str | None):
    if request.headers.get("origin") != "https://ie.siteformo.com":
        raise HTTPException(status_code=403, detail="Payment request origin is not allowed")
    try:
        return require_journey_visitor(db, credential)
    except JourneyCredentialError as exc:
        raise HTTPException(status_code=403, detail="Journey credential is required") from exc


def _raise_boundary(exc: Exception) -> None:
    if isinstance(exc, PaymentBoundaryError):
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    raise HTTPException(status_code=403, detail=str(exc)) from None


@router.post("/{order_id}/payment-confirmation", response_model=PaymentConfirmationResponse)
def confirm_payment_prerequisites(
    order_id: str,
    payload: PaymentConfirmationRequest,
    request: Request,
    db: Session = Depends(get_db),
    credential: str | None = Header(default=None, alias=JOURNEY_CREDENTIAL_HEADER),
):
    visitor = _visitor(request, db, credential)
    try:
        order = confirm_brief_and_legal(
            db, visitor, order_id,
            brief_confirmed=payload.brief_confirmed,
            legal_confirmed=payload.legal_confirmed,
            legal_terms_version=payload.legal_terms_version,
        )
    except (Q1OwnershipError, PaymentBoundaryError) as exc:
        _raise_boundary(exc)
    return PaymentConfirmationResponse(
        order_id=order.id,
        brief_confirmed_at=order.brief_confirmed_at,
        legal_terms_version=order.legal_terms_version,
        legal_confirmed_at=order.legal_confirmed_at,
        prepayment_summary_email_status=order.prepayment_summary_email_status,
    )


@router.post("/{order_id}/checkout", response_model=CheckoutResponseV2)
def create_initial_deposit_checkout(
    order_id: str,
    _payload: CheckoutRequestV2,
    request: Request,
    db: Session = Depends(get_db),
    credential: str | None = Header(default=None, alias=JOURNEY_CREDENTIAL_HEADER),
):
    visitor = _visitor(request, db, credential)
    if not stripe.api_key:
        raise HTTPException(status_code=503, detail="Stripe is not configured")
    try:
        attempt, reused = create_checkout(db, visitor, order_id, stripe.checkout.Session.create)
    except (Q1OwnershipError, PaymentBoundaryError) as exc:
        _raise_boundary(exc)
    return CheckoutResponseV2(
        order_id=order_id,
        attempt_id=attempt.id,
        checkout_url=attempt.checkout_url,
        deposit_amount_cents=attempt.expected_deposit_amount_cents,
        currency="EUR",
        reused=reused,
    )


@router.get("/{order_id}/payment-status", response_model=PaymentStatusResponse)
def get_payment_status(
    order_id: str,
    request: Request,
    db: Session = Depends(get_db),
    credential: str | None = Header(default=None, alias=JOURNEY_CREDENTIAL_HEADER),
):
    visitor = _visitor(request, db, credential)
    try:
        return PaymentStatusResponse(**payment_status(db, visitor, order_id))
    except (Q1OwnershipError, PaymentBoundaryError) as exc:
        _raise_boundary(exc)
