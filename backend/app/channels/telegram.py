import os

import httpx
from fastapi import APIRouter, Request

from app.services.ai.ai_service import generate_ai_reply

router = APIRouter()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


@router.post("/telegram/webhook")
@router.post("/channels/telegram/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()

    message = data.get("message") or data.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = str(sender.get("id") or chat_id or "telegram_unknown")
    text = message.get("text") or message.get("caption") or ""

    if not chat_id or not text:
        return {"ok": True}

    ai_reply = await generate_ai_reply(
        user_text=text,
        user_id=f"telegram:{user_id}",
        channel="telegram",
    )

    if TELEGRAM_BOT_TOKEN:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": ai_reply},
            )

    return {"ok": True}
