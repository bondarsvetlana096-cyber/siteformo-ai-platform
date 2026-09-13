from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PaymentConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brief_confirmed: bool = False
    legal_confirmed: bool = False
    legal_terms_version: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_confirmation_event(self):
        if not self.brief_confirmed and not self.legal_confirmed:
            raise ValueError("At least one confirmation event is required")
        if self.legal_confirmed and not self.legal_terms_version:
            raise ValueError("Legal confirmation requires a terms version")
        if not self.legal_confirmed and self.legal_terms_version:
            raise ValueError("Terms version may only accompany legal confirmation")
        return self


class PaymentConfirmationResponse(BaseModel):
    order_id: str
    brief_confirmed_at: datetime | None
    legal_terms_version: str | None
    legal_confirmed_at: datetime | None
    prepayment_summary_email_status: Literal["pending", "sent", "failed"]


class CheckoutRequestV2(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckoutResponseV2(BaseModel):
    order_id: str
    attempt_id: str
    checkout_url: str
    deposit_amount_cents: int
    currency: Literal["EUR"]
    reused: bool


class PaymentStatusResponse(BaseModel):
    order_id: str
    deposit_status: Literal["not_started", "checkout_created", "pending", "paid", "failed", "cancelled", "refunded"]
    payment_confirmed: bool
    next_step: Literal["complete_payment", "await_payment_confirmation", "post_payment_pending"]
    retry_allowed: bool
