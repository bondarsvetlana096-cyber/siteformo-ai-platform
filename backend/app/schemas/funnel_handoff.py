from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


_CANONICAL = re.compile(r"^[a-z][a-z0-9_]{1,159}$")


class FunnelHandoffRequest(BaseModel):
    handoff_token: str = Field(min_length=40, max_length=128)


class FunnelHandoffPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["v1"]
    receiver: Literal["ie"]
    requested_subject: str = Field(min_length=2, max_length=160)
    match_state: Literal["exact", "no_exact_example"]
    shown_demo_canonical: str | None
    source_request_id: UUID | None
    created_at: datetime
    expires_at: datetime
    handoff_id: UUID

    @model_validator(mode="after")
    def validate_contract(self):
        if self.expires_at - self.created_at != timedelta(minutes=15):
            raise ValueError("invalid handoff lifetime")
        if self.match_state == "exact":
            if (
                not self.shown_demo_canonical
                or self.shown_demo_canonical == "business_service"
                or _CANONICAL.fullmatch(self.shown_demo_canonical) is None
            ):
                raise ValueError("invalid exact canonical")
        elif self.shown_demo_canonical is not None:
            raise ValueError("no_exact_example cannot include a canonical")
        return self


class FunnelHandoffResponse(BaseModel):
    journey_id: UUID
    credential: str | None
    resumed: bool
    order_id: str
    handoff_id: UUID
    match_state: Literal["exact", "no_exact_example"]
