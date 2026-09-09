from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.journey.identity import hash_journey_credential, issue_journey_credential
from app.journey.models import SiteFormoVisitor


class JourneyCredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class JourneySession:
    visitor: SiteFormoVisitor
    credential: str | None


def require_journey_visitor(db: Session, credential: str | None) -> SiteFormoVisitor:
    if not credential:
        raise JourneyCredentialError("Journey session is required")
    visitor = db.execute(
        select(SiteFormoVisitor).where(
            SiteFormoVisitor.credential_hash == hash_journey_credential(credential)
        )
    ).scalar_one_or_none()
    if visitor is None:
        raise JourneyCredentialError("Journey session is invalid")
    return visitor


def bootstrap_journey(db: Session, credential: str | None) -> JourneySession:
    if credential:
        visitor = db.execute(
            select(SiteFormoVisitor).where(
                SiteFormoVisitor.credential_hash == hash_journey_credential(credential)
            )
        ).scalar_one_or_none()
        if visitor is not None:
            return JourneySession(visitor=visitor, credential=None)

    new_credential = issue_journey_credential()
    visitor = SiteFormoVisitor(credential_hash=hash_journey_credential(new_credential))
    db.add(visitor)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise JourneyCredentialError("Journey session could not be created") from None
    db.refresh(visitor)
    return JourneySession(visitor=visitor, credential=new_credential)
