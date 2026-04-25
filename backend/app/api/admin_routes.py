from fastapi import APIRouter, HTTPException
from app.services.order_service import approve_order

router = APIRouter()

@router.get("/admin/approve/{order_id}")
def approve(order_id: str, token: str):
    # простая защита
    if token != "super-secret-token":
        raise HTTPException(status_code=403, detail="Unauthorized")

    approve_order(order_id)

    return {
        "status": "approved",
        "message": "Generation started 🚀"
    }