from __future__ import annotations

import re

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.contact_delivery.canary import (
    ClaimKind,
    ProviderError,
    claim_once,
    delivery_identity,
    enabled,
    finalize_accepted,
    finalize_failed,
    request_fingerprint,
    send_with_resend,
    trusted_example_for_origin,
)
from app.services.contact_delivery.template import (
    Enquiry,
    TemplateValidationError,
    render,
)

IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
router = APIRouter(prefix="/api/v1/demo-contact", tags=["demo-contact"])


class ContactEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    preferred_method: str = Field(min_length=1, max_length=20)
    contact_value: str = Field(min_length=3, max_length=320)
    message: str = Field(min_length=1, max_length=5_000)
    idempotency_key: str = Field(min_length=16, max_length=128)

    @field_validator("first_name", "last_name", "preferred_method", "contact_value", "idempotency_key")
    @classmethod
    def reject_unsafe_single_line(cls, value: str) -> str:
        if "\r" in value or "\n" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("unsafe control character")
        return value

    @field_validator("message")
    @classmethod
    def reject_unsafe_message_controls(cls, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        if any((ord(char) < 32 and char not in {"\n", "\t"}) or ord(char) == 127 for char in normalized):
            raise ValueError("unsafe control character")
        return normalized

    @model_validator(mode="after")
    def validate_email_contract(self) -> ContactEmailRequest:
        if self.preferred_method.lower() != "email":
            raise ValueError("preferred_method must be Email")
        if not IDEMPOTENCY.fullmatch(self.idempotency_key):
            raise ValueError("invalid idempotency_key")
        try:
            validated = validate_email(self.contact_value, check_deliverability=False)
        except EmailNotValidError as exc:
            raise ValueError("invalid email address") from exc
        self.contact_value = validated.normalized.lower()
        self.preferred_method = "Email"
        return self


class ContactEmailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    message_id: str
    replayed: bool = False
    remaining_deliveries: int = Field(ge=0, le=2)


@router.post(
    "/email",
    response_model=ContactEmailResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_demo_contact_email(
    payload: ContactEmailRequest,
    request: Request,
    origin: str | None = Header(default=None),
    content_length: int | None = Header(default=None),
) -> ContactEmailResponse:
    example_id = trusted_example_for_origin(origin)
    if not example_id:
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    if content_length is not None and content_length > 16_384:
        raise HTTPException(status_code=413, detail="request_too_large")
    if not enabled():
        raise HTTPException(status_code=503, detail="live_email_disabled")
    canonical = payload.model_dump()
    fingerprint = request_fingerprint(canonical)
    identity = delivery_identity(
        example_id=example_id,
        recipient=payload.contact_value,
        idempotency_key=payload.idempotency_key,
        fingerprint=fingerprint,
        client_id=request.client.host if request.client else "unknown",
    )
    try:
        claim = await claim_once(identity)
    except (RuntimeError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="delivery_state_unavailable") from exc
    if claim.kind is ClaimKind.QUOTA_EXHAUSTED:
        raise HTTPException(status_code=429, detail="quota_exhausted")
    if claim.kind is ClaimKind.RATE_LIMITED:
        raise HTTPException(status_code=429, detail="rate_limited")
    if claim.kind is ClaimKind.CONFLICT:
        raise HTTPException(status_code=409, detail="idempotency_conflict")
    if claim.kind is ClaimKind.IN_PROGRESS:
        raise HTTPException(status_code=409, detail="submission_in_progress")
    if claim.kind is ClaimKind.REPLAY_FAILED:
        raise HTTPException(status_code=502, detail=claim.failure_code or "provider_failed")
    if claim.kind is ClaimKind.REPLAY_ACCEPTED and claim.provider_message_id:
        return ContactEmailResponse(
            status="provider_accepted",
            message_id=claim.provider_message_id,
            replayed=True,
            remaining_deliveries=claim.remaining_deliveries or 0,
        )

    try:
        rendered = render(
            Enquiry(
                first_name=payload.first_name,
                last_name=payload.last_name,
                preferred_method=payload.preferred_method,
                contact_value=payload.contact_value,
                message=payload.message,
            )
        )
        acceptance = await send_with_resend(
            rendered, payload.contact_value, payload.idempotency_key, example_id
        )
    except TemplateValidationError as exc:
        await finalize_failed(identity, "template_invalid")
        raise HTTPException(status_code=422, detail="template_invalid") from exc
    except ProviderError as exc:
        await finalize_failed(identity, exc.code)
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc

    remaining_deliveries = await finalize_accepted(identity, acceptance.message_id)
    return ContactEmailResponse(
        status="provider_accepted",
        message_id=acceptance.message_id,
        remaining_deliveries=remaining_deliveries,
    )
