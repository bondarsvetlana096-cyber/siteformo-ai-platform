from fastapi import APIRouter

router = APIRouter(prefix="/channels")


@router.get("/health")
async def channels_health():
    return {"status": "channels ok"}
