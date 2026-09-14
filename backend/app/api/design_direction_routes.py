from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.journey.service import JourneyCredentialError, require_journey_visitor
from app.schemas.design_direction import (
    DesignDirectionSelectionRequest,
    DesignDirectionStateResponse,
)
from app.services.design_direction_service import (
    confirm_design_direction,
    get_design_direction_state,
)
from app.services.payment_boundary_service import PaymentBoundaryError
from app.services.q1_service import Q1OwnershipError


router = APIRouter(prefix="/api/orders", tags=["design-direction-v2"])


def _visitor(request: Request, db: Session, credential: str | None):
    if request.headers.get("origin") != "https://ie.siteformo.com":
        raise HTTPException(status_code=403, detail="Design Direction request origin is not allowed")
    try:
        return require_journey_visitor(db, credential)
    except JourneyCredentialError as exc:
        raise HTTPException(status_code=403, detail="Journey credential is required") from exc


def _raise_design_error(exc: Exception) -> None:
    if isinstance(exc, PaymentBoundaryError):
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    raise HTTPException(status_code=403, detail=str(exc)) from None


@router.get("/{order_id}/design-direction", response_model=DesignDirectionStateResponse)
def read_design_direction(
    order_id: str,
    request: Request,
    db: Session = Depends(get_db),
    credential: str | None = Header(default=None, alias=JOURNEY_CREDENTIAL_HEADER),
):
    visitor = _visitor(request, db, credential)
    try:
        return DesignDirectionStateResponse(**get_design_direction_state(db, visitor, order_id))
    except (Q1OwnershipError, PaymentBoundaryError) as exc:
        _raise_design_error(exc)


@router.post("/{order_id}/design-direction", response_model=DesignDirectionStateResponse)
def write_design_direction(
    order_id: str,
    payload: DesignDirectionSelectionRequest,
    request: Request,
    db: Session = Depends(get_db),
    credential: str | None = Header(default=None, alias=JOURNEY_CREDENTIAL_HEADER),
):
    visitor = _visitor(request, db, credential)
    try:
        return DesignDirectionStateResponse(**confirm_design_direction(db, visitor, order_id, payload.direction))
    except (Q1OwnershipError, PaymentBoundaryError) as exc:
        _raise_design_error(exc)
