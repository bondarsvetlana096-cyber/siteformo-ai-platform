from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
@router.get("/channels/health")
async def health():
    return {"ok": True, "service": "siteformo-ai-platform"}
