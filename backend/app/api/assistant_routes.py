from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.assistant.config import AssistantSettings, get_assistant_settings
from app.assistant.openai_adapter import AssistantInferenceError, AssistantOpenAIAdapter
from app.assistant.rate_limit import assistant_rate_limiter
from app.assistant.service import (
    AssistantOwnershipError,
    load_history,
    require_owned_conversation,
    resolve_visitor_and_conversation,
    stream_message,
)
from app.db.session import get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.journey.models import SiteFormoVisitor
from app.journey.service import JourneyCredentialError, require_journey_visitor

logger = logging.getLogger("siteformo.assistant.api")
router = APIRouter(prefix="/api/assistant", tags=["assistant"])
MAX_MESSAGE_CHARS = 4000


class AssistantMessagePayload(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    client_message_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    page_hint: str | None = Field(default=None, max_length=512)

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value

    @field_validator("page_hint")
    @classmethod
    def validate_page_hint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("page_hint must be a site-relative route")
        if any(ord(character) < 32 for character in value):
            raise ValueError("page_hint contains invalid characters")
        return value


def require_enabled(settings: AssistantSettings = Depends(get_assistant_settings)) -> AssistantSettings:
    if not settings.enabled:
        raise HTTPException(status_code=404, detail="Assistant is unavailable")
    return settings


def require_ie_origin(request: Request) -> None:
    if request.headers.get("origin") != "https://ie.siteformo.com":
        raise HTTPException(status_code=403, detail="Assistant request origin is not allowed")


def require_journey(
    db: Session = Depends(get_db),
    credential: str | None = Header(default=None, alias=JOURNEY_CREDENTIAL_HEADER),
) -> SiteFormoVisitor:
    try:
        return require_journey_visitor(db, credential)
    except JourneyCredentialError:
        raise HTTPException(status_code=428, detail="Journey session is required") from None


def _serialize_history(messages: list) -> list[dict[str, str]]:
    return [
        {"id": str(message.id), "role": message.role, "content": message.content, "created_at": message.created_at.isoformat()}
        for message in messages
        if message.role in {"user", "assistant"}
    ]


@router.get("/availability")
def assistant_availability(settings: AssistantSettings = Depends(get_assistant_settings)) -> dict[str, bool]:
    return {"enabled": settings.enabled}


@router.post("/session")
def bootstrap_session(
    _: AssistantSettings = Depends(require_enabled),
    __: None = Depends(require_ie_origin),
    db: Session = Depends(get_db),
    siteformo_visitor: SiteFormoVisitor = Depends(require_journey),
) -> dict:
    _, conversation = resolve_visitor_and_conversation(db, siteformo_visitor)
    return {"conversation_id": str(conversation.id), "history": _serialize_history(load_history(db, conversation.id))}


@router.get("/history")
def get_history(
    conversation_id: uuid.UUID | None = Query(default=None),
    _: AssistantSettings = Depends(require_enabled),
    db: Session = Depends(get_db),
    siteformo_visitor: SiteFormoVisitor = Depends(require_journey),
) -> dict:
    try:
        _, conversation = require_owned_conversation(db, siteformo_visitor, conversation_id)
    except AssistantOwnershipError:
        raise HTTPException(status_code=404, detail="Conversation was not found") from None
    return {"conversation_id": str(conversation.id), "history": _serialize_history(load_history(db, conversation.id))}


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


@router.post("/message")
async def post_message(
    payload: AssistantMessagePayload,
    request: Request,
    settings: AssistantSettings = Depends(require_enabled),
    _: None = Depends(require_ie_origin),
    db: Session = Depends(get_db),
    siteformo_visitor: SiteFormoVisitor = Depends(require_journey),
) -> StreamingResponse:
    try:
        _, conversation = require_owned_conversation(db, siteformo_visitor, payload.conversation_id)
    except AssistantOwnershipError:
        raise HTTPException(status_code=404, detail="Conversation was not found") from None
    if not assistant_rate_limiter.check(siteformo_visitor.credential_hash):
        raise HTTPException(status_code=429, detail="Too many Assistant messages")
    adapter = AssistantOpenAIAdapter(settings)

    async def events() -> AsyncIterator[bytes]:
        yield _sse("start", {"conversation_id": str(conversation.id)})
        try:
            async for chunk in stream_message(
                db, conversation, payload.message, payload.page_hint, payload.client_message_id, adapter
            ):
                if await request.is_disconnected():
                    logger.info("Assistant stream disconnected conversation_id=%s", conversation.id)
                    return
                yield _sse("delta", {"text": chunk})
            yield _sse("done", {})
        except AssistantInferenceError:
            yield _sse("error", {"message": "Assistant response is temporarily unavailable"})
        except asyncio.CancelledError:
            logger.info("Assistant stream cancelled conversation_id=%s", conversation.id)
            raise
        except Exception as exc:
            logger.warning("Assistant stream stopped error_type=%s", type(exc).__name__)
            yield _sse("error", {"message": "Assistant response could not be saved"})

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
