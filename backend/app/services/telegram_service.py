from __future__ import annotations

import json
from urllib import request

from fastapi import APIRouter, Request

from app.core.config import settings


router = APIRouter()


class TelegramService:
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.telegram_bot_token)

    @staticmethod
    def send_text(chat_id: str | int, text: str) -> None:
        if not settings.telegram_bot_token:
            print("❌ NO TELEGRAM TOKEN")
            return

        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"

        payload = json.dumps({
            "chat_id": chat_id,
            "text": text,
        }).encode("utf-8")

        req = request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=20) as response:
                print("✅ MESSAGE SENT", response.read())
        except Exception as e:
            print("❌ TELEGRAM SEND ERROR:", e)


@router.post("/webhook")
async def telegram_webhook(request_obj: Request):
    data = await request_obj.json()

    print("📩 TELEGRAM UPDATE:", data)

    message = data.get("message")
    if not message:
        print("⚠️ NO MESSAGE FIELD")
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "")

    print("👤 chat_id:", chat_id)
    print("💬 text:", text)

    if not chat_id:
        return {"ok": True}

    # 🔥 ОТВЕТ БОТА
    if text == "/start":
        TelegramService.send_text(
            chat_id,
            "Привет! Бот работает 🚀\n\nНапиши, какой сайт ты хочешь или скинь примеры."
        )
    else:
        TelegramService.send_text(
            chat_id,
            f"Ты написал: {text}"
        )

    return {"ok": True}