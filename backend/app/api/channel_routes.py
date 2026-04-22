from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.order import ChannelType
from app.services.chatbot_service import ChatbotService
from app.services.launch_link_service import LaunchLinkService
from app.services.telegram_service import TelegramService
from app.services.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["channels"])


@router.get("/health")
def channel_health() -> dict:
    return {
        "ok": True,
        "telegram_configured": TelegramService.is_configured(),
        "whatsapp_configured": WhatsAppService.is_configured(),
        "openai_configured": bool(settings.openai_api_key),
    }


@router.get("/launch-links")
def channel_launch_links() -> dict:
    return LaunchLinkService.build_launch_links()


@router.post("/web-chat/message")
async def web_chat_message(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    user_id = str(payload.get("user_id") or payload.get("session_id") or "anonymous-web-user")
    text = str(payload.get("text") or "").strip()

    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    try:
        reply = ChatbotService.process_message(db, ChannelType.WEB, user_id, text)
        return {"reply": reply}
    except Exception as exc:
        logger.exception("Web chat processing failed: %s", exc)
        raise HTTPException(status_code=500, detail="web chat processing failed")


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()

    message = payload.get("message") or payload.get("edited_message")
    callback_query = payload.get("callback_query")

    chat_id = None
    text = ""

    if message:
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()

    elif callback_query:
        chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
        text = (callback_query.get("data") or "").strip()

    if not chat_id or not text:
        return {"ok": True}

    try:
        reply = ChatbotService.process_message(
            db=db,
            channel=ChannelType.TELEGRAM,
            user_id=str(chat_id),
            message=text,
        )
        TelegramService.send_text(chat_id, reply)
        return {"ok": True}

    except Exception as exc:
        logger.exception("Telegram webhook processing failed: %s", exc)

        try:
            TelegramService.send_text(
                chat_id,
                "Sorry, something went wrong while processing your request. Please try again."
            )
        except Exception:
            logger.exception("Failed to send Telegram fallback message")

        return {"ok": False, "error": "telegram processing failed"}


@router.get("/whatsapp/webhook")
async def whatsapp_verify(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_webhook_verify_token:
        return int(hub_challenge)

    raise HTTPException(status_code=403, detail="verification failed")


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()

    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                for message in value.get("messages", []):
                    from_number = message.get("from")
                    text = (message.get("text") or {}).get("body", "").strip()

                    if not from_number or not text:
                        continue

                    reply = ChatbotService.process_message(
                        db=db,
                        channel=ChannelType.WHATSAPP,
                        user_id=from_number,
                        message=text,
                    )
                    WhatsAppService.send_text(from_number, reply)

        return {"ok": True}

    except Exception as exc:
        logger.exception("WhatsApp webhook processing failed: %s", exc)
        return {"ok": False, "error": "whatsapp processing failed"}