from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/chat")
async def chat_disabled():
    raise HTTPException(
        status_code=410,
        detail="Free chat is disabled. Use POST /channels/web-chat/start and POST /channels/web-chat for the guided sales flow.",
    )
