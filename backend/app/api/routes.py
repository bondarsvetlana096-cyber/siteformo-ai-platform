from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

router = APIRouter(prefix="/channels")


@router.get("/health")
async def channels_health():
    return {"status": "channels ok"}


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})
    text = message.get("text", "")

    return {
        "method": "sendMessage",
        "text": f"Ты написал: {text}",
    }


@router.get("/whatsapp/webhook")
async def whatsapp_verify():
    return {"ok": True}


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    form = await request.form()
    text = form.get("Body", "")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>Ты написал: {text}</Message>
</Response>"""

    return Response(content=twiml, media_type="application/xml")