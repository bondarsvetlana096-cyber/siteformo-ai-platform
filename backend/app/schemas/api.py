from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, HttpUrl, model_validator


class CreateRequestIn(BaseModel):
    request_type: Literal["redesign", "create"]
    email: EmailStr
    source_url: Optional[HttpUrl] = None
    business_description: Optional[str] = None
    turnstile_token: Optional[str] = None

    @model_validator(mode="after")
    def validate_mode_fields(self):
        if self.request_type == "redesign" and not self.source_url:
            raise ValueError("source_url is required for redesign mode")
        if self.request_type == "create" and not (self.business_description or "").strip():
            raise ValueError("business_description is required for create mode")
        return self


class CreateRequestOut(BaseModel):
    status: str
    message: str
    request_id: Optional[str]
    redirect_url: Optional[str] = None


class RequestStatusOut(BaseModel):
    request_id: str
    status: str
    demo_url: Optional[str] = None
    expires_at: Optional[datetime] = None