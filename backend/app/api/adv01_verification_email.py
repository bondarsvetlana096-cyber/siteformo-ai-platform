from __future__ import annotations

import logging
import re
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services import adv01_verification_mail as service

router = APIRouter(prefix="/api/v1/adv01", tags=["adv01-verification-mail"])


class VerificationMailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: str
    purpose: str
    request_id: str
    issued_at: int
    wordpress_user_id: int = Field(gt=0)
    recipient_email: str = Field(min_length=3, max_length=320)
    verification_url: str = Field(min_length=20, max_length=2_048)

    @field_validator("version")
    @classmethod
    def fixed_version(cls, value: str) -> str:
        if value != "1":
            raise ValueError("unsupported version")
        return value

    @field_validator("purpose")
    @classmethod
    def fixed_purpose(cls, value: str) -> str:
        if value != service.PURPOSE:
            raise ValueError("invalid purpose")
        return value

    @field_validator("request_id")
    @classmethod
    def valid_request_id(cls, value: str) -> str:
        if not service.UUID4.fullmatch(value):
            raise ValueError("request_id must be UUIDv4")
        return value.lower()

    @field_validator("recipient_email")
    @classmethod
    def valid_recipient(cls, value: str) -> str:
        try:
            return validate_email(value, check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            raise ValueError("invalid recipient") from exc

    @model_validator(mode="after")
    def valid_verification_url(self) -> "VerificationMailRequest":
        config = service.BridgeConfig.from_env()
        parsed = urlsplit(self.verification_url)
        if parsed.scheme != "https":
            raise ValueError("invalid verification scheme")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError("userinfo and fragments are forbidden")
        if parsed.hostname != config.allowed_host or parsed.port is not None:
            raise ValueError("invalid verification host")
        if parsed.path != service.VERIFICATION_PATH:
            raise ValueError("invalid verification path")
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise ValueError("invalid verification query") from exc
        required = {"uid", "token"}
        allowed = required | {"cont"}
        if not required.issubset(query) or not set(query).issubset(allowed):
            raise ValueError("invalid verification query")
        if any(len(values) != 1 for values in query.values()):
            raise ValueError("invalid verification query")
        uid = query["uid"][0]
        if not re.fullmatch(r"[1-9][0-9]*", uid) or int(uid) != self.wordpress_user_id:
            raise ValueError("verification UID mismatch")
        if query["token"][0] == "":
            raise ValueError("empty verification token")
        if "cont" in query and query["cont"][0] == "":
            raise ValueError("empty continuation token")
        return self


class VerificationMailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    request_id: str
    replayed: bool
    provider_message_id: str | None = None


def get_store(config: service.BridgeConfig) -> service.ReplayStore:
    return service.RedisReplayStore(config.redis_url)


def replay_response(claim: service.Claim, request_id: str) -> VerificationMailResponse:
    record = claim.record or {}
    if claim.kind == "accepted":
        return VerificationMailResponse(
            status="provider_accepted",
            request_id=request_id,
            replayed=True,
            provider_message_id=record.get("provider_message_id"),
        )
    if claim.kind == "rejected":
        raise HTTPException(
            status_code=int(record.get("status_code", 502)),
            detail=record.get("code", "provider_rejected"),
        )
    if claim.kind == "unknown":
        raise HTTPException(status_code=504, detail="provider_result_unknown")
    if claim.kind == "conflict":
        raise HTTPException(status_code=409, detail="idempotency_conflict")
    raise HTTPException(status_code=409, detail="submission_in_progress")


@router.post(
    "/verification-email",
    response_model=VerificationMailResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_adv01_verification_email(
    request: Request,
    x_siteformo_bridge_version: Annotated[str | None, Header()] = None,
    x_siteformo_timestamp: Annotated[str | None, Header()] = None,
    x_siteformo_request_id: Annotated[str | None, Header()] = None,
    x_siteformo_signature: Annotated[str | None, Header()] = None,
) -> VerificationMailResponse:
    raw_body = await request.body()
    if len(raw_body) > service.MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request_too_large")
    config = service.BridgeConfig.from_env()
    request_id_for_log = x_siteformo_request_id or "missing"
    try:
        if x_siteformo_bridge_version != "1":
            raise service.BridgeError("invalid_bridge_version", 401)
        service.authenticate(
            config=config,
            timestamp_text=x_siteformo_timestamp,
            request_id=x_siteformo_request_id,
            signature=x_siteformo_signature,
            raw_body=raw_body,
        )
    except service.BridgeError as exc:
        service.safe_log(
            logging.WARNING,
            "authentication_rejected",
            request_id=request_id_for_log,
            outcome=exc.code,
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    try:
        payload = VerificationMailRequest.model_validate_json(raw_body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_payload") from exc
    if (
        payload.request_id != x_siteformo_request_id.lower()
        or str(payload.issued_at) != x_siteformo_timestamp
    ):
        raise HTTPException(status_code=400, detail="header_body_mismatch")

    digest = service.body_digest(raw_body)
    fingerprint = service.recipient_fingerprint(config.secret, payload.recipient_email)
    try:
        store = get_store(config)
        claim = await store.claim(payload.request_id, digest)
    except service.BridgeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    if claim.kind != "new":
        return replay_response(claim, payload.request_id)

    try:
        await service.enforce_rate_limits(
            store,
            secret=config.secret,
            uid=payload.wordpress_user_id,
            recipient=payload.recipient_email,
        )
        message_id = await service.send_verification_email(
            recipient=payload.recipient_email,
            verification_url=payload.verification_url,
            request_id=payload.request_id,
            config=config,
        )
    except service.BridgeError as exc:
        state = "unknown" if exc.code == "provider_result_unknown" else "rejected"
        try:
            await store.finalize(
                payload.request_id,
                {
                    "digest": digest,
                    "state": state,
                    "code": exc.code,
                    "status_code": exc.status_code,
                },
            )
        except service.BridgeError:
            state = "unknown"
            exc = service.ProviderResultUnknown()
        service.safe_log(
            logging.WARNING,
            "delivery_failed",
            request_id=payload.request_id,
            uid=payload.wordpress_user_id,
            recipient_hash=fingerprint,
            outcome=exc.code,
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc

    try:
        await store.finalize(
            payload.request_id,
            {"digest": digest, "state": "accepted", "provider_message_id": message_id},
        )
    except service.BridgeError as exc:
        raise HTTPException(status_code=504, detail="provider_result_unknown") from exc
    service.safe_log(
        logging.INFO,
        "provider_accepted",
        request_id=payload.request_id,
        uid=payload.wordpress_user_id,
        recipient_hash=fingerprint,
        outcome="provider_accepted",
    )
    return VerificationMailResponse(
        status="provider_accepted",
        request_id=payload.request_id,
        replayed=False,
        provider_message_id=message_id,
    )
