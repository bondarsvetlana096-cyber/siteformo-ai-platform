from fastapi import FastAPI

from app.services.telegram_service import router as telegram_router

app = FastAPI(title="SiteFormo AI Sales Platform")

app.include_router(telegram_router, prefix="/channels/telegram", tags=["telegram"])


@app.get("/")
async def root():
    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "ok"}