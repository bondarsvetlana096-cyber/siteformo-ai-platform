from fastapi import Header, HTTPException

from app.core.settings import settings


def require_admin(x_admin_key: str | None = Header(default=None)):
    if not settings.ADMIN_API_KEY:
        raise HTTPException(status_code=503, detail="ADMIN_API_KEY is not configured")

    if x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")

    return True
