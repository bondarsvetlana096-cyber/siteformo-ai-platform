from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None


router = APIRouter()


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")

    if not settings.OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured")

    if AsyncOpenAI is None:
        raise HTTPException(status_code=500, detail="OpenAI package is not installed")

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    result = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": "You are SiteFormo AI assistant. Help users with website, demo and sales questions.",
            },
            {
                "role": "user",
                "content": payload.message,
            },
        ],
    )

    answer = result.choices[0].message.content or "No response"
    return ChatResponse(response=answer)