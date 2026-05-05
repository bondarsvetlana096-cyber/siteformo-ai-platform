from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.auth import require_admin
from app.services.db.models import Lead
from app.services.db.postgres import SessionLocal

router = APIRouter(prefix="/api/leads", tags=["leads"])


def get_db():
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail="Database is not configured")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def serialize_lead(lead: Lead) -> dict:
    return {
        "id": lead.id,
        "user_id": lead.user_id,
        "channel": lead.channel,
        "contact_channel": getattr(lead, "contact_channel", None),
        "service": lead.service,
        "city": lead.city,
        "urgency": lead.urgency,
        "contact": lead.contact,
        "status": getattr(lead, "status", None) or "new",
        "is_hot": bool(getattr(lead, "is_hot", False)),
        "followup_stage": getattr(lead, "followup_stage", 0),
        "last_contacted": lead.last_contacted.isoformat() if getattr(lead, "last_contacted", None) else None,
        "history": getattr(lead, "history", []) or [],
        "estimate": getattr(lead, "estimate", None),
        "offer_url": getattr(lead, "offer_url", None),
        "raw_text": lead.raw_text,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
    }


@router.get("/", dependencies=[Depends(require_admin)])
def get_leads(
    city: str | None = None,
    service: str | None = None,
    status: str | None = None,
    channel: str | None = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(Lead)

    if city:
        query = query.filter(Lead.city.ilike(f"%{city}%"))
    if service:
        query = query.filter(Lead.service.ilike(f"%{service}%"))
    if status:
        query = query.filter(Lead.status == status)
    if channel:
        query = query.filter(Lead.channel == channel)

    leads = query.order_by(Lead.created_at.desc()).limit(limit).all()
    return [serialize_lead(lead) for lead in leads]


@router.get("/latest", dependencies=[Depends(require_admin)])
def latest_leads(db: Session = Depends(get_db)):
    leads = db.query(Lead).order_by(Lead.created_at.desc()).limit(10).all()
    return [serialize_lead(lead) for lead in leads]


@router.patch("/{lead_id}/status", dependencies=[Depends(require_admin)])
def update_lead_status(lead_id: int, status: str, db: Session = Depends(get_db)):
    allowed = {"new", "contacted", "qualified", "closed", "lost"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of: {sorted(allowed)}")

    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead.status = status
    db.commit()
    db.refresh(lead)
    return serialize_lead(lead)
