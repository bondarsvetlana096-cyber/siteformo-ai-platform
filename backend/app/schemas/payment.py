from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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


class PrepaymentAddonSummary(BaseModel):
    addon_key: Literal["additional_page", "video_entry_page", "simple_logo", "installation"]
    label: str
    confirmed_price_cents: int
    charge_phase: Literal["final_balance"]


class PrepaymentPaymentSummary(BaseModel):
    base_package: Literal["starter", "business", "advanced"]
    base_package_price_cents: int
    initial_deposit_rate: Literal["0.50"]
    initial_deposit_amount_cents: int
    currency: Literal["EUR"]
    confirmed_addons: list[PrepaymentAddonSummary]
    addons_due_now_cents: Literal[0]
    remaining_base_package_balance_cents: int
    confirmed_addons_balance_cents: int
    current_final_balance_cents: int


class PrepaymentConfirmationState(BaseModel):
    package_and_addons_confirmed: bool
    brief_confirmed_at: datetime | None
    legal_confirmed_at: datetime | None
    legal_terms_version: str | None


class PrepaymentEmailState(BaseModel):
    status: Literal["pending", "sent", "failed"]
    sent_at: datetime | None


class PrepaymentScopeSummary(BaseModel):
    q2_schema_version: int
    scope_version: str
    scope_hash: str
    summary_version: str


class PrepaymentProjectSummary(BaseModel):
    business_identity: dict[str, Any]
    business_activity: dict[str, Any]
    audience: str
    primary_goal: str
    confirmed_functions: list[str]
    provisional_architecture: dict[str, Any]
    package_qualification: dict[str, Any]


class PrepaymentSummaryResponse(BaseModel):
    order_id: str
    project_summary: PrepaymentProjectSummary
    payment_summary: PrepaymentPaymentSummary
    confirmation_state: PrepaymentConfirmationState
    email_state: PrepaymentEmailState
    scope: PrepaymentScopeSummary
    legal_terms_available: bool
    approved_legal_terms_version: str | None


class PrepaymentSummaryEmailRequest(BaseModel):
    """No browser-authored summary, amount, recipient, or legal data is accepted."""

    model_config = ConfigDict(extra="forbid")


class PrepaymentSummaryEmailResponse(BaseModel):
    order_id: str
    result: Literal["sent", "already_sent", "failed", "in_progress"]
    email_status: Literal["pending", "sent", "failed"]
    sent_at: datetime | None
    retry_allowed: bool
