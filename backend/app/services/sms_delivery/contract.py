from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.sms_delivery.models import normalize_first_name, validate_idempotency_key


class SmsDemoRequest(BaseModel):
    """Future endpoint body. Trusted Example identity is intentionally absent."""

    model_config = ConfigDict(extra="forbid")
    first_name: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, min_length=9, max_length=16)
    message: str | None = Field(default=None, max_length=240)
    idempotency_key: str = Field(min_length=16, max_length=128)

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, value: str | None) -> str | None:
        normalized = normalize_first_name(value)
        return normalized or None

    @field_validator("idempotency_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return validate_idempotency_key(value)


class SmsDemoResponse(BaseModel):
    status: str
    delivery_reference: str
    replayed: bool


FAIL_CLOSED_ERRORS = {
    "sms_demo_disabled": 503,
    "sms_provider_not_configured": 503,
    "sms_audit_unavailable": 503,
    "invalid_e164_phone": 422,
    "sms_country_not_allowed": 422,
    "sms_premium_destination_blocked": 422,
    "sms_idempotency_conflict": 409,
    "sms_in_progress": 409,
    "sms_quota_exhausted": 429,
    "sms_rate_limited": 429,
    "sms_provider_ambiguous": 502,
    "sms_provider_quarantined": 502,
    "sms_provider_rejected": 502,
    "sms_message_required": 422,
    "invalid_sms_message": 422,
    "sms_owner_recipient_not_configured": 503,
    "sms_visitor_notifications_disabled": 503,
}
