from app.api.admin import router as admin_router
from app.api.leads import router as leads_router
from app.api.order_routes import router as order_router
from app.channels.health import router as health_router
from app.channels.telegram import router as telegram_router
from app.channels.whatsapp import router as whatsapp_router
from app.channels.web_chat import router as web_chat_router  # ✅ ДОБАВИЛИ
from app.services.db.init_db import init_db
from app.core.config import settings
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.channel_routes import router as channel_router
from app.api.routes import router as api_router


app = FastAPI(
    title="SiteFormo AI Sales Platform",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ ОСНОВНЫЕ API
app.include_router(api_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# ✅ КАНАЛЫ
app.include_router(channel_router)

# ✅ ЧАТ (НОВОЕ)
app.include_router(web_chat_router)


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup_event():
    init_db()
    if settings.enable_guided_followups:
        from app.services.lead_nurturing import followup_worker
        asyncio.create_task(followup_worker())


# Остальные роуты
app.include_router(health_router)
app.include_router(telegram_router)
app.include_router(whatsapp_router)
app.include_router(leads_router)
app.include_router(order_router)
app.include_router(admin_router)