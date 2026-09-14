from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.journey.service import JourneyCredentialError, require_journey_visitor
from app.schemas.interaction_preference import (
    InteractionPreferenceRequest,
    InteractionPreferenceResponse,
)
from app.services.interaction_preference_service import (
    confirm_interaction_preference,
    get_interaction_preference,
)
from app.services.payment_boundary_service import PaymentBoundaryError
from app.services.q1_service import Q1OwnershipError


router = APIRouter(prefix="/api/orders", tags=["interaction-preference-v1"])


def _visitor(request: Request, db: Session, credential: str | None):
    if request.headers.get("origin") != "https://ie.siteformo.com":
        raise HTTPException(status_code=403, detail="Interaction Preference request origin is not allowed")
    try:
        return require_journey_visitor(db, credential)
    except JourneyCredentialError as exc:
        raise HTTPException(status_code=403, detail="Journey credential is required") from exc


def _raise_interaction_error(exc: Exception) -> None:
    if isinstance(exc, PaymentBoundaryError):
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    raise HTTPException(status_code=403, detail=str(exc)) from None


@router.get("/{order_id}/interaction-preference", response_model=InteractionPreferenceResponse)
def read_interaction_preference(
    order_id: str,
    request: Request,
    db: Session = Depends(get_db),
    credential: str | None = Header(default=None, alias=JOURNEY_CREDENTIAL_HEADER),
):
    visitor = _visitor(request, db, credential)
    try:
        return InteractionPreferenceResponse(**get_interaction_preference(db, visitor, order_id))
    except (Q1OwnershipError, PaymentBoundaryError) as exc:
        _raise_interaction_error(exc)


@router.post("/{order_id}/interaction-preference", response_model=InteractionPreferenceResponse)
def write_interaction_preference(
    order_id: str,
    payload: InteractionPreferenceRequest,
    request: Request,
    db: Session = Depends(get_db),
    credential: str | None = Header(default=None, alias=JOURNEY_CREDENTIAL_HEADER),
):
    visitor = _visitor(request, db, credential)
    try:
        return InteractionPreferenceResponse(**confirm_interaction_preference(db, visitor, order_id, payload.preference))
    except (Q1OwnershipError, PaymentBoundaryError) as exc:
        _raise_interaction_error(exc)
