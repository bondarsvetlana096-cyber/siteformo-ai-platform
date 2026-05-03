import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.services.db.init_db import init_db

# API routers
from app.api.routes import router as api_router
from app.api.channel_routes import router as channel_router
from app.api.leads import router as leads_router
from app.api.order_routes import router as order_router
from app.api.request_routes import router as request_router
from app.api.payment_routes import router as payment_router
from app.api.stripe_webhook import router as stripe_webhook_router
from app.api.admin_routes import router as admin_routes_router

# 👉 ДОБАВЬ ЭТО
from app.api.create_order import router as create_order_router

# Channels
from app.channels.health import router as health_router
from app.channels.telegram import router as telegram_router
from app.channels.whatsapp import router as whatsapp_router
from app.channels.web_chat import router as web_chat_router


app = FastAPI(
    title="SiteFormo AI Sales Platform",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=getattr(settings, "cors_origins", None) or [
        "https://siteformo.com",
        "https://www.siteformo.com",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def root():
    return {"status": "ok", "service": "SiteFormo AI Sales Platform"}


@app.get("/health")
def health():
    return {"status": "ok"}


# Routers
app.include_router(api_router)
app.include_router(channel_router)

# 👉 ДОБАВЬ ЭТО
app.include_router(create_order_router)

app.include_router(health_router)
app.include_router(web_chat_router)
app.include_router(telegram_router)
app.include_router(whatsapp_router)

app.include_router(leads_router)
app.include_router(order_router)
app.include_router(request_router)
app.include_router(payment_router)
app.include_router(stripe_webhook_router)
app.include_router(admin_routes_router)


@app.on_event("startup")
async def startup_event():
    init_db()

    if settings.enable_guided_followups:
        from app.services.lead_nurturing import followup_worker
        asyncio.create_task(followup_worker())