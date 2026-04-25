from html import escape

from fastapi import APIRouter, Form, Response

from app.services.ai.ai_service import generate_ai_reply

router = APIRouter()


@router.get("/twilio/webhook")
@router.get("/whatsapp/webhook")
@router.get("/channels/whatsapp/webhook")
async def whatsapp_webhook_check():
    return {"ok": True, "channel": "whatsapp"}


@router.post("/twilio/webhook")
@router.post("/whatsapp/webhook")
@router.post("/channels/whatsapp/webhook")
async def whatsapp_webhook(
    Body: str = Form(""),
    From: str = Form(""),
):
    ai_reply = await generate_ai_reply(
        user_text=Body,
        user_id=f"whatsapp:{From or 'unknown'}",
        channel="whatsapp",
    )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{escape(ai_reply)}</Message>
</Response>"""

    return Response(content=xml, media_type="application/xml")
