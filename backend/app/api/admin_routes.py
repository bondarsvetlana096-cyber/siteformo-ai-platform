import html

from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.services.approval_service import ApprovalService
from app.services.delivery_service import DeliveryService
from app.services.generation_service import generate_site
from app.models.order import Order, OrderStatus
from app.db.session import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/approve/{order_id}")
def approve(
    order_id: str,
    token: str,
    db: Session = Depends(get_db),
):
    if not ApprovalService.verify(order_id, "approve", token):
        raise HTTPException(status_code=403, detail="Invalid token")

    order = db.query(Order).filter(Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # ✅ ставим статус APPROVED
    order.status = OrderStatus.APPROVED
    db.commit()
    db.refresh(order)

    # ✅ РЕАЛЬНАЯ генерация
    generate_site(db, order)

    db.refresh(order)

    # ✅ delivery ссылка
    delivery_url = DeliveryService.build_delivery_url(
        order_id=order.id,
        email=getattr(order.client, "email", ""),
    )

    return {
        "status": "approved",
        "delivery_url": delivery_url,
    }


@router.get("/reject/{order_id}")
def reject(
    order_id: str,
    token: str,
    db: Session = Depends(get_db),
):
    if not ApprovalService.verify(order_id, "reject", token):
        raise HTTPException(status_code=403, detail="Invalid token")

    order = db.query(Order).filter(Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404)

    order.status = OrderStatus.REJECTED
    db.commit()

    return {"status": "rejected"}


@router.get("/delivery/{order_id}", response_class=HTMLResponse)
def delivery_page(
    order_id: str,
    email: str = Query(...),
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    if not DeliveryService.verify(order_id, email, token):
        raise HTTPException(status_code=403, detail="Invalid token")

    order = db.query(Order).filter(Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404)

    if getattr(order.client, "email", "") != email:
        raise HTTPException(status_code=403)

    if order.status != OrderStatus.FINAL_READY:
        return HTMLResponse("<h1>Website not ready yet</h1>")

    package = order.final_packages[-1] if order.final_packages else None

    if not package:
        raise HTTPException(status_code=404, detail="No final package")

    final_html = package.divi_html

    return HTMLResponse(f"""
    <html>
    <body style="margin:0;">
        <div style="padding:15px;background:#111;color:#fff;">
            <button onclick="copy()">Copy HTML</button>
            <a href="/api/admin/delivery/{order_id}/download?email={email}&token={token}">
                Download
            </a>
        </div>

        <iframe srcdoc="{html.escape(final_html)}"
                style="width:100%;height:90vh;border:0;"></iframe>

        <textarea id="html" style="display:none;">{html.escape(final_html)}</textarea>

        <script>
        function copy() {{
            const text = document.getElementById("html").value;
            navigator.clipboard.writeText(text);
            alert("Copied!");
        }}
        </script>
    </body>
    </html>
    """)


@router.get("/delivery/{order_id}/download")
def download(
    order_id: str,
    email: str,
    token: str,
    db: Session = Depends(get_db),
):
    if not DeliveryService.verify(order_id, email, token):
        raise HTTPException(status_code=403)

    order = db.query(Order).filter(Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404)

    package = order.final_packages[-1] if order.final_packages else None

    if not package:
        raise HTTPException(status_code=404)

    return Response(
        content=package.divi_html,
        media_type="text/html",
        headers={
            "Content-Disposition": f"attachment; filename=site-{order_id}.html"
        },
    )