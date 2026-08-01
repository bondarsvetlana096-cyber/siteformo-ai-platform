import asyncio
import os
from pathlib import Path

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
from app.api.create_order import router as create_order_router
from app.api.review_routes import router as review_router
from app.api.example_routes import router as example_router
from app.api.website_analysis import router as website_analysis_router

# Safe channels
from app.channels.health import router as health_router
from app.channels.web_chat import router as web_chat_router


def env_enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


app = FastAPI(
    title="SiteFormo Production Platform",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ie.siteformo.com",
        "https://siteformo.com",
        "https://www.siteformo.com",
        "https://preview.siteformo.com",
        "https://starter.siteformo.com",
        "https://business.siteformo.com",
        "https://reference.siteformo.com",
        "https://advanced.siteformo.com",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "SiteFormo Production Platform",
        "version": "2.1.0",
        "logic": "questionnaire -> examples -> design direction -> interaction style -> production -> protected preview -> revisions -> approval -> zip delivery",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


# Core routers
app.include_router(api_router)
app.include_router(channel_router)
app.include_router(create_order_router)

app.include_router(health_router)
app.include_router(web_chat_router)

app.include_router(leads_router)
app.include_router(order_router)
app.include_router(request_router)
app.include_router(payment_router)
app.include_router(stripe_webhook_router)
app.include_router(admin_routes_router)
app.include_router(review_router)
app.include_router(example_router)
app.include_router(website_analysis_router)


# Optional legacy/integration channels.
# Disabled by default so Telegram/OpenAI cannot block payment/review/example backend startup.
if env_enabled("ENABLE_TELEGRAM_CHANNEL"):
    from app.channels.telegram import router as telegram_router

    app.include_router(telegram_router)


if env_enabled("ENABLE_WHATSAPP_CHANNEL"):
    from app.channels.whatsapp import router as whatsapp_router

    app.include_router(whatsapp_router)


@app.on_event("startup")
async def startup_event():
    init_db()

    if settings.enable_guided_followups:
        from app.services.lead_nurturing import followup_worker

        asyncio.create_task(followup_worker())