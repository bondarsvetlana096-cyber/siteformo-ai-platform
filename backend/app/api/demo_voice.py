from __future__ import annotations

import hashlib
import os
import time

from fastapi import APIRouter, Form, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.voice_delivery import runtime
from app.services.voice_delivery.models import VoiceState, normalize_name, validate_idempotency
from app.services.voice_delivery.security import validate_twilio_signature
from app.services.voice_delivery.service import CALLBACK_STATES

TRUSTED_ORIGINS = {"https://dev.siteformo.com"}
router = APIRouter(tags=["demo-voice"])


class VoiceDemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str = Field(min_length=1, max_length=40)
    phone: str = Field(min_length=9, max_length=16)
    idempotency_key: str = Field(min_length=16, max_length=128)

    @field_validator("first_name")
    @classmethod
    def name_valid(cls, value: str) -> str:
        return normalize_name(value)

    @field_validator("idempotency_key")
    @classmethod
    def key_valid(cls, value: str) -> str:
        return validate_idempotency(value)


class VoiceDemoResponse(BaseModel):
    status: str
    request_reference: str
    scheduled_at: int
    replayed: bool


@router.post("/api/demo/voice/request", response_model=VoiceDemoResponse, status_code=202)
async def request_voice_demo(
    payload: VoiceDemoRequest, request: Request, response: Response,
    origin: str | None = Header(default=None),
) -> VoiceDemoResponse:
    if origin not in TRUSTED_ORIGINS:
        raise HTTPException(status_code=403, detail="origin_not_allowed", headers={"Cache-Control": "no-store"})
    if runtime.configuration is None or not runtime.configuration.enabled:
        raise HTTPException(status_code=503, detail="voice_demo_disabled", headers={"Cache-Control": "no-store"})
    if runtime.service is None:
        raise HTTPException(status_code=503, detail="voice_runtime_unavailable", headers={"Cache-Control": "no-store"})
    try:
        result = await runtime.service.request_call(
            first_name=payload.first_name, phone=payload.phone,
            idempotency_key=payload.idempotency_key,
            client_id=request.client.host if request.client else "unknown",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Cache-Control": "no-store"}) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Cache-Control": "no-store"}) from exc
    response.headers["Cache-Control"] = "no-store"
    result_body = VoiceDemoResponse(
        status=result.state.value, request_reference=result.request_id,
        scheduled_at=result.scheduled_at, replayed=result.replayed,
    )
    return result_body


@router.post(
    "/api/demo/voice/status",
    response_class=Response,
)
async def voice_status_callback(
    request: Request,
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
    signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
) -> Response:
    config = runtime.configuration
    if config is None or not config.enabled or runtime.service is None:
        raise HTTPException(status_code=503, detail="voice_runtime_unavailable")
    form = {key: str(value) for key, value in (await request.form()).multi_items()}
    callback_url = f"{config.public_base_url}/api/demo/voice/status"
    if not validate_twilio_signature(callback_url, form, signature, config.auth_token or ""):
        raise HTTPException(status_code=403, detail="invalid_twilio_signature")
    state = CALLBACK_STATES.get(CallStatus)
    if state is None or not CallSid.startswith("CA") or len(CallSid) != 34:
        raise HTTPException(status_code=422, detail="invalid_voice_callback")
    call_sid_hash = hashlib.sha256(CallSid.encode()).hexdigest()
    store = runtime.service.store
    await store.apply_callback(call_sid_hash, state, int(time.time()))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
