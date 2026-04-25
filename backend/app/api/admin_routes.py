from fastapi import APIRouter, HTTPException
from app.core.config import settings
from app.services.approval_service import ApprovalService

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/test-email")
async def test_email():
    return {"status": "ok"}


@router.get("/approve/{order_id}")
def approve(order_id: str, token: str):
    if not ApprovalService.verify(order_id, "approve", token):
        raise HTTPException(status_code=403, detail="Invalid token")

    # пока просто ответ, позже подключим генерацию
    return {
        "status": "approved",
        "order_id": order_id,
        "message": "Approved ✅",
    }


@router.get("/reject/{order_id}")
def reject(order_id: str, token: str):
    if not ApprovalService.verify(order_id, "reject", token):
        raise HTTPException(status_code=403, detail="Invalid token")

    return {
        "status": "rejected",
        "order_id": order_id,
        "message": "Rejected ❌",
    }