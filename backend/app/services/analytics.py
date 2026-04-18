from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.telemetry import capture_event, capture_exception
from app.models.request import EventLog

logger = logging.getLogger("siteformo.analytics")


def log_event(db: Session, event_name: str, request_id: str | None = None, payload: dict | None = None, distinct_id: str | None = None) -> None:
    event_payload = payload or {}
    db.add(EventLog(request_id=request_id, event_name=event_name, payload=event_payload))
    db.commit()
    logger.info("[EVENT] %s request_id=%s payload=%s", event_name, request_id, event_payload)
    if distinct_id:
        capture_event(distinct_id=distinct_id, event=event_name, properties=event_payload)


def log_exception(exc: Exception, context: dict | None = None) -> None:
    logger.error("[ERROR] %s", str(exc), exc_info=True)
    capture_exception(exc, context=context or {})
