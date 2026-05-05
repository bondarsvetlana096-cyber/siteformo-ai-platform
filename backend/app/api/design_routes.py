from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.services.db.session import get_db
from app.models.order import Order, OrderStatus
from app.services.generation_service import GenerationService

router = APIRouter()


class ApproveDesignRequest(BaseModel):
    order_id: str
    preview_id: str
    selected_design_url: Optional[str] = None


@router.post("/api/orders/{order_id}/approve-design")
async def approve_design(
    order_id: str,
    payload: ApproveDesignRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if payload.order_id != order_id:
        raise HTTPException(status_code=400, detail="Order ID mismatch")

    order = db.query(Order).filter(Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    current_status = str(getattr(order, "status", ""))

    allowed_statuses = {
        "DESIGN_PREVIEWS_READY",
        "AWAITING_CLIENT_DESIGN_CHOICE",
        "OrderStatus.DESIGN_PREVIEWS_READY",
        "OrderStatus.AWAITING_CLIENT_DESIGN_CHOICE",
    }

    if current_status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Design cannot be approved from status: {current_status}",
        )

    if getattr(order, "selected_design_id", None) or getattr(order, "selected_preview_id", None):
        raise HTTPException(
            status_code=400,
            detail="Design already selected",
        )

    design_previews = getattr(order, "design_previews", None) or []

    selected_preview = None

    if isinstance(design_previews, list):
        for preview in design_previews:
            if isinstance(preview, dict) and preview.get("id") == payload.preview_id:
                selected_preview = preview
                break

    if not selected_preview:
        raise HTTPException(
            status_code=404,
            detail="Selected preview not found for this order",
        )

    selected_url = (
        payload.selected_design_url
        or selected_preview.get("image_url")
        or selected_preview.get("preview_url")
        or selected_preview.get("screenshot_url")
        or selected_preview.get("desktop_image_url")
    )

    if not selected_url:
        raise HTTPException(
            status_code=400,
            detail="Selected preview has no image URL",
        )

    now = datetime.now(timezone.utc)
    refund_until = now + timedelta(hours=1)

    if hasattr(order, "selected_design_id"):
        order.selected_design_id = payload.preview_id

    if hasattr(order, "selected_preview_id"):
        order.selected_preview_id = payload.preview_id

    if hasattr(order, "selected_design_url"):
        order.selected_design_url = selected_url

    if hasattr(order, "design_status"):
        order.design_status = "DESIGN_APPROVED"

    if hasattr(order, "refund_window_started_at"):
        order.refund_window_started_at = now

    if hasattr(order, "refund_window_ends_at"):
        order.refund_window_ends_at = refund_until

    try:
        order.status = OrderStatus.DESIGN_APPROVED
    except Exception:
        order.status = "DESIGN_APPROVED"

    db.commit()
    db.refresh(order)

    def run_final_generation():
        local_db = next(get_db())
        try:
            fresh_order = local_db.query(Order).filter(Order.id == order_id).first()
            if not fresh_order:
                return

            service = GenerationService()
            service.start_full_generation_for_order(
                db=local_db,
                order=fresh_order,
                note="Full generation started after client selected design preview.",
            )
        finally:
            local_db.close()

    background_tasks.add_task(run_final_generation)

    return {
        "success": True,
        "message": "Design approved. Final website generation has started.",
        "order_id": order_id,
        "preview_id": payload.preview_id,
        "selected_design_url": selected_url,
        "refund_window_started_at": now.isoformat(),
        "refund_window_ends_at": refund_until.isoformat(),
    }