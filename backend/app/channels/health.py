from fastapi import APIRouter

from app.core.settings import settings
from app.services.db.postgres import engine

router = APIRouter()


@router.get("/health")
@router.get("/channels/health")
async def health():
    return {
        "ok": True,
        "service": "siteformo-ai-platform",
        "env": settings.ENV,
        "openai_configured": bool(settings.OPENAI_API_KEY),
        "database_configured": engine is not None,
        "model": settings.OPENAI_MODEL,
    }
