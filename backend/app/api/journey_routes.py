from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.journey.service import JourneyCredentialError, bootstrap_journey
from app.middleware.rate_limit import rate_limit_dependency
from app.schemas.funnel_handoff import FunnelHandoffRequest, FunnelHandoffResponse
from app.services.funnel_handoff import FunnelHandoffReceiveError, persist_handoff, redeem_from_demo


router = APIRouter(prefix="/api/journey", tags=["journey"])
IE_ORIGIN = "https://ie.siteformo.com"


def require_ie_journey_origin(request: Request) -> None:
    if request.headers.get("origin") != IE_ORIGIN:
        raise HTTPException(status_code=403, detail="Journey request origin is not allowed")


@router.post("/session")
def journey_session(
    _: None = Depends(require_ie_journey_origin),
    db: Session = Depends(get_db),
    credential: str | None = Header(default=None, alias=JOURNEY_CREDENTIAL_HEADER),
) -> dict[str, str | bool | None]:
    try:
        session = bootstrap_journey(db, credential)
    except JourneyCredentialError:
        raise HTTPException(status_code=503, detail="Journey session is temporarily unavailable") from None
    return {
        "journey_id": str(session.visitor.id),
        "credential": session.credential,
        "resumed": session.credential is None,
    }


@router.post("/handoff", response_model=FunnelHandoffResponse, dependencies=[Depends(rate_limit_dependency)])
def journey_handoff(
    request: FunnelHandoffRequest,
    _: None = Depends(require_ie_journey_origin),
    db: Session = Depends(get_db),
    credential: str | None = Header(default=None, alias=JOURNEY_CREDENTIAL_HEADER),
) -> FunnelHandoffResponse:
    try:
        session = bootstrap_journey(db, credential)
        payload = redeem_from_demo(request.handoff_token, str(session.visitor.id))
        order, _ = persist_handoff(db, session.visitor, payload)
    except (JourneyCredentialError, FunnelHandoffReceiveError):
        raise HTTPException(status_code=404, detail="Handoff is unavailable") from None
    return FunnelHandoffResponse(
        journey_id=session.visitor.id,
        credential=session.credential,
        resumed=session.credential is None,
        order_id=order.id,
        handoff_id=payload.handoff_id,
        match_state=payload.match_state,
    )
