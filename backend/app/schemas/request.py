from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator, model_validator

from app.models.request import ContactType, RequestType


class CreateRequestPayload(BaseModel):
    request_type: Literal['redesign', 'create'] | None = None
    contact_type: Literal['email', 'telegram'] = ContactType.EMAIL
    contact_value: str | None = Field(default=None, max_length=320)
    email: EmailStr | None = None
    source_url: HttpUrl | None = None
    business_description: str | None = Field(default=None, max_length=5000)
    source_input: str | None = Field(default=None, max_length=5000)
    turnstile_token: str | None = None
    fingerprint: str | None = None

    @field_validator('business_description', 'source_input')
    @classmethod
    def strip_text_fields(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator('contact_value')
    @classmethod
    def strip_contact_value(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None

    @model_validator(mode='after')
    def derive_request_shape(self):
        if self.source_input and not self.source_url and not self.business_description:
            source_input = self.source_input.strip()
            if source_input.startswith('http://') or source_input.startswith('https://'):
                self.source_url = source_input
            else:
                self.business_description = source_input

        if self.request_type is None:
            self.request_type = RequestType.REDESIGN if self.source_url else RequestType.CREATE

        if self.request_type == RequestType.REDESIGN and self.source_url is None:
            raise ValueError('source_url is required for redesign')

        if self.request_type == RequestType.CREATE and self.business_description is None:
            raise ValueError('business_description is required for create')

        if self.contact_type == ContactType.EMAIL:
            if self.email is None and not self.contact_value:
                raise ValueError('email or contact_value is required for email contact type')
            if self.email is not None:
                self.contact_value = str(self.email)
            elif self.contact_value:
                self.email = self.contact_value
        else:
            if not self.contact_value:
                raise ValueError('contact_value is required for non-email contact types')

        return self


class CreateRequestResponse(BaseModel):
    status: Literal['accepted', 'limit_reached']
    request_id: str | None = None
    message: str | None = None
    redirect_url: str | None = None
    confirmation_required: bool = False
    confirmation_token: str | None = None
    confirmation_text: str | None = None
    confirmation_link: str | None = None
    channel_contact: str | None = None


class RequestEventPayload(BaseModel):
    event_type: Literal[
        'demo_opened',
        'demo_cta_clicked',
        'main_form_started',
        'main_form_completed',
        'payment_started',
        'payment_completed',
    ]
    metadata: dict | None = None


class RequestStatusResponse(BaseModel):
    request_id: str
    status: str
    contact_type: str
    contact_value: str
    demo_url: str | None = None
    expires_at: datetime | None = None
    retention_expires_at: datetime | None = None
    error_message: str | None = None
    confirmation_required: bool = False
    confirmation_text: str | None = None
    confirmation_link: str | None = None
    demo_opened_at: datetime | None = None
    demo_cta_clicked_at: datetime | None = None
    main_form_started_at: datetime | None = None
    main_form_completed_at: datetime | None = None
    payment_started_at: datetime | None = None
    payment_completed_at: datetime | None = None
    follow_up_count: int = 0
