from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.sms_delivery.models import (
    normalize_first_name,
    validate_idempotency_key,
    validate_sms_example_customer_message,
)


class SmsDemoRequest(BaseModel):
    """Public fields; example_id is validated against the exact request Origin."""

    model_config = ConfigDict(extra="forbid")
    example_id: str | None = Field(default=None, min_length=1, max_length=128)
    first_name: str = Field(min_length=1, max_length=40)
    phone: str = Field(min_length=9, max_length=16)
    customer_message: str = Field(min_length=1, max_length=30)
    idempotency_key: str = Field(min_length=16, max_length=128)

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, value: str | None) -> str:
        normalized = normalize_first_name(value)
        if not normalized:
            raise ValueError("invalid_first_name")
        return normalized

    @field_validator("idempotency_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return validate_idempotency_key(value)

    @field_validator("customer_message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        return validate_sms_example_customer_message(value)

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
    "sms_message_too_long": 422,
    "sms_owner_recipient_not_configured": 503,
    "sms_visitor_notifications_disabled": 503,
}
