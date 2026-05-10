from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.order import Order, OrderStatus
from app.services.canonical_brief_service import build_canonical_brief, store_canonical_brief_on_order
from app.services.generation_service import GenerationService

router = APIRouter()


class ApproveDesignRequest(BaseModel):
    order_id: str
    preview_id: str
    selected_design_url: Optional[str] = None


class SelectEffectsRequest(BaseModel):
    order_id: str
    interaction_style: Optional[str] = None
    selected_effects: list[Any] | None = None
    motion_level: Optional[str] = None
    start_generation: bool = True


def _get_order(db: Session, order_id: str) -> Order:
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _background_final_generation(order_id: str):
    local_db = next(get_db())
    try:
        fresh_order = local_db.query(Order).filter(Order.id == order_id).first()
        if not fresh_order:
            return
        service = GenerationService()
        service.start_full_generation_for_order(
            db=local_db,
            order=fresh_order,
            note="Full website generation started after selected design and selected interaction effects were saved.",
        )
    finally:
        local_db.close()


@router.post("/api/orders/{order_id}/approve-design")
async def approve_design(
    order_id: str,
    payload: ApproveDesignRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Save the selected design preview.

    IMPORTANT NEW FLOW:
    This endpoint does NOT start final generation anymore.
    Final generation starts after the effects/motion page is submitted.
    """
    if payload.order_id != order_id:
        raise HTTPException(status_code=400, detail="Order ID mismatch")

    order = _get_order(db, order_id)
    current_status = str(getattr(order, "status", ""))

    allowed_statuses = {
        "design_previews_ready",
        "awaiting_client_design_choice",
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
        raise HTTPException(status_code=400, detail="Design already selected")

    design_previews = getattr(order, "design_previews", None) or []
    selected_preview = None

    if isinstance(design_previews, list):
        for preview in design_previews:
            if isinstance(preview, dict) and preview.get("id") == payload.preview_id:
                selected_preview = preview
                break

    if not selected_preview:
        raise HTTPException(status_code=404, detail="Selected preview not found for this order")

    selected_url = (
        payload.selected_design_url
        or selected_preview.get("image_url")
        or selected_preview.get("preview_url")
        or selected_preview.get("screenshot_url")
        or selected_preview.get("desktop_image_url")
    )

    if not selected_url:
        raise HTTPException(status_code=400, detail="Selected preview has no image URL")

    now = datetime.now(timezone.utc)
    refund_until = now + timedelta(hours=1)

    if hasattr(order, "selected_design_id"):
        order.selected_design_id = payload.preview_id
    if hasattr(order, "selected_preview_id"):
        order.selected_preview_id = payload.preview_id
    if hasattr(order, "selected_design_url"):
        order.selected_design_url = selected_url
    if hasattr(order, "selected_design_label"):
        order.selected_design_label = selected_preview.get("label") or payload.preview_id
    if hasattr(order, "design_status"):
        order.design_status = "DESIGN_APPROVED_AWAITING_EFFECTS"
    if hasattr(order, "refund_window_started_at"):
        order.refund_window_started_at = now
    if hasattr(order, "refund_window_expires_at"):
        order.refund_window_expires_at = refund_until

    # Store canonical state now, but final generation waits for effects.
    canonical = build_canonical_brief(order)
    store_canonical_brief_on_order(order, canonical)

    try:
        order.status = OrderStatus.DESIGN_APPROVED
    except Exception:
        order.status = "design_approved"

    db.commit()
    db.refresh(order)

    return {
        "success": True,
        "message": "Design approved. Please continue to the effects selection page before final generation starts.",
        "order_id": order_id,
        "preview_id": payload.preview_id,
        "selected_design_url": selected_url,
        "next_step": "effects_selection",
        "generation_started": False,
        "refund_window_started_at": now.isoformat(),
        "refund_window_expires_at": refund_until.isoformat(),
    }


@router.post("/api/orders/{order_id}/select-effects")
async def select_effects_and_start_generation(
    order_id: str,
    payload: SelectEffectsRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Save selected effects/motion profile and then start final generation.

    This is the correct trigger point for the new flow:
    selected example + first questionnaire + qualification + second questionnaire +
    selected design + selected effects -> Canonical Brief -> Generation Orchestrator.
    """
    if payload.order_id != order_id:
        raise HTTPException(status_code=400, detail="Order ID mismatch")

    order = _get_order(db, order_id)

    if not getattr(order, "selected_design_id", None):
        raise HTTPException(status_code=400, detail="Select a design preview before choosing effects")

    brief = getattr(order, "extended_brief", None) or {}
    if not isinstance(brief, dict):
        brief = {"raw_brief": str(brief)}

    brief["interaction_style"] = payload.interaction_style or payload.motion_level
    brief["motion_level"] = payload.motion_level or payload.interaction_style
    brief["selected_effects"] = payload.selected_effects or []

    if hasattr(order, "extended_brief"):
        order.extended_brief = brief
    if hasattr(order, "interaction_style"):
        order.interaction_style = payload.interaction_style or payload.motion_level
    if hasattr(order, "design_status"):
        order.design_status = "EFFECTS_SELECTED"
    if hasattr(order, "generation_status"):
        order.generation_status = "QUEUED_FOR_FINAL_GENERATION"

    canonical = build_canonical_brief(order)
    store_canonical_brief_on_order(order, canonical)

    try:
        order.status = OrderStatus.FULL_PRODUCTION_STARTED
    except Exception:
        order.status = "full_production_started"

    db.commit()
    db.refresh(order)

    if payload.start_generation:
        background_tasks.add_task(_background_final_generation, order_id)

    return {
        "success": True,
        "message": "Effects saved. Final website generation has started with a protected regeneration limit.",
        "order_id": order_id,
        "generation_started": bool(payload.start_generation),
        "max_quality_iterations": canonical.get("quality_standards", {}).get("max_quality_iterations"),
        "max_total_generation_rounds": canonical.get("quality_standards", {}).get("max_total_generation_rounds"),
        "cost_guard": canonical.get("generation_rules", {}).get("budget", {}).get("hard_cap_reason"),
    }
