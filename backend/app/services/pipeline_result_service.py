from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.order import Order


def _set_if_exists(obj: Any, key: str, value: Any) -> None:
    if hasattr(obj, key):
        setattr(obj, key, value)


def persist_quality_pipeline_result(
    db: Session,
    order: Order,
    pipeline_result: Dict[str, Any],
) -> Order:
    previews: List[Dict[str, Any]] = pipeline_result.get("design_previews") or []
    payload = getattr(order, "preview_generation_payload", None) or {}
    if not isinstance(payload, dict):
        payload = {"previous_preview_generation_payload": payload}

    payload["quality_pipeline"] = pipeline_result
    payload["quality_checked_at"] = datetime.utcnow().isoformat()

    final_status = pipeline_result.get("status") or "MANUAL_REVIEW_REQUIRED"

    _set_if_exists(order, "design_previews", previews)
    _set_if_exists(order, "preview_generation_payload", payload)
    _set_if_exists(order, "generation_status", final_status)
    _set_if_exists(order, "design_status", final_status)

    if final_status == "READY_TO_SEND":
        _set_if_exists(order, "status", "DESIGN_PREVIEWS_READY")
    elif final_status == "MANUAL_REVIEW_REQUIRED":
        _set_if_exists(order, "status", "MANUAL_REVIEW_REQUIRED")
    else:
        _set_if_exists(order, "status", final_status)

    db.add(order)
    db.commit()
    db.refresh(order)
    return order
