import html

from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.services.approval_service import ApprovalService
from app.services.generation_service import generate_site
from app.services.review_service import ReviewService, apply_creative_payload
from app.models.order import Order, OrderStatus
from app.db.session import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/approve/{order_id}")
def approve(order_id: str, token: str, db: Session = Depends(get_db)):
    if not ApprovalService.verify(order_id, "approve", token):
        raise HTTPException(status_code=403, detail="Invalid token")

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status = OrderStatus.APPROVED
    apply_creative_payload(order)
    db.commit()
    db.refresh(order)

    # Keep legacy manual approval behavior, but do not expose delivery files here.
    generate_site(db, order)
    db.refresh(order)

    return {
        "status": "approved",
        "order_id": order.id,
        "message": "Order approved. Continue through design preview / production / protected review flow. Final ZIP remains locked until final approval.",
    }


@router.get("/reject/{order_id}")
def reject(order_id: str, token: str, db: Session = Depends(get_db)):
    if not ApprovalService.verify(order_id, "reject", token):
        raise HTTPException(status_code=403, detail="Invalid token")
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404)
    order.status = OrderStatus.REJECTED
    db.commit()
    return {"status": "rejected"}


@router.get("/review/{order_id}")
def create_review_link(order_id: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    email = getattr(getattr(order, "client", None), "email", None) or getattr(order, "email", None)
    token = ReviewService.generate_token(order.id, email)
    review_url = ReviewService.build_review_url(order.id, email)
    apply_creative_payload(order)
    if hasattr(order, "review_token_hash"):
        order.review_token_hash = ReviewService.token_hash(token)
    if hasattr(order, "protected_preview_url"):
        order.protected_preview_url = review_url
    order.status = getattr(OrderStatus, "READY_FOR_REVIEW", "ready_for_review")
    db.commit()
    return {"status": "ready_for_review", "review_url": review_url}


@router.get("/delivery/{order_id}", response_class=HTMLResponse)
def delivery_page(order_id: str, email: str = Query(...), token: str = Query(...), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404)
    if not ReviewService.verify(order, token, email):
        raise HTTPException(status_code=403, detail="Invalid token")
    if getattr(order, "final_approved_at", None) is None:
        return HTMLResponse("""
        <html><body style='font-family:Arial,sans-serif;padding:32px;max-width:760px;margin:auto;'>
        <h1>Final delivery is locked</h1>
        <p>This protected area is for review only. Final ZIP/source delivery becomes available after revision completion and final approval.</p>
        </body></html>
        """)
    package = order.final_packages[-1] if order.final_packages else None
    if not package:
        raise HTTPException(status_code=404, detail="No final package")
    final_html = package.divi_html
    return HTMLResponse(f"""
    <html><body style="margin:0;">
        <div style="padding:15px;background:#111;color:#fff;position:sticky;top:0;z-index:5;">
            <button onclick="copy()">Copy HTML</button>
            <a style="color:#fff;margin-left:16px" href="/api/admin/delivery/{order_id}/download?email={html.escape(email)}&token={html.escape(token)}">Download final HTML</a>
        </div>
        <iframe srcdoc="{html.escape(final_html)}" style="width:100%;height:90vh;border:0;"></iframe>
        <textarea id="html" style="display:none;">{html.escape(final_html)}</textarea>
        <script>function copy(){{navigator.clipboard.writeText(document.getElementById('html').value);alert('Copied!');}}</script>
    </body></html>
    """)


@router.get("/delivery/{order_id}/download")
def download(order_id: str, email: str, token: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404)
    if not ReviewService.verify(order, token, email):
        raise HTTPException(status_code=403)
    if getattr(order, "final_approved_at", None) is None:
        raise HTTPException(status_code=403, detail="Final ZIP/source delivery is locked until final approval")
    package = order.final_packages[-1] if order.final_packages else None
    if not package:
        raise HTTPException(status_code=404)
    return Response(
        content=package.divi_html,
        media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename=site-{order_id}.html"},
    )
