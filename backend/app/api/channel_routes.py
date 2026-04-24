from fastapi import APIRouter, Request

from app.services.ai.ai_service import generate_ai_reply

router = APIRouter(prefix="/channels", tags=["web-chat"])


@router.post("/web-chat/message")
async def web_chat_message(request: Request):
    data = await request.json()
    text = data.get("message", "")
    user_id = str(data.get("user_id") or data.get("session_id") or "web_chat_unknown")

    reply = await generate_ai_reply(
        user_text=text,
        user_id=f"web-chat:{user_id}",
        channel="web-chat",
    )

    return {"reply": reply}
