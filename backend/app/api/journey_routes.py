from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.journey.service import JourneyCredentialError, bootstrap_journey


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
