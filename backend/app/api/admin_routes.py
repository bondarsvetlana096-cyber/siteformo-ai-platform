from fastapi import APIRouter, HTTPException
from app.services.email_service import send_email
from app.core.config import settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/test-email")
async def test_email():
    await send_email(
        settings.owner_email,
        "SiteFormo test email",
        "<h1>Email works ✅</h1><p>SMTP is configured correctly.</p>",
    )

    return {
        "status": "sent",
        "message": "Test email was sent",
    }


@router.get("/approve/{order_id}")
def approve(order_id: str, token: str):
    if token != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Unauthorized")

    return {
        "status": "approved",
        "order_id": order_id,
        "message": "Approval endpoint works. Generation logic will be connected next.",
    }