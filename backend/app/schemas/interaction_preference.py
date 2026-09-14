from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


InteractionPreferenceKey = Literal["subtle", "recommended", "more_expressive"]


class InteractionPreferenceOption(BaseModel):
    key: InteractionPreferenceKey
    label: str
    recommended: bool


class InteractionPreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preference: InteractionPreferenceKey


class InteractionPreferenceResponse(BaseModel):
    order_id: str
    stage: Literal["interaction_preference_required", "interaction_preference_confirmed"]
    contract_version: Literal["v1"]
    available_preferences: list[InteractionPreferenceOption]
    selected_preference: InteractionPreferenceKey | None
    confirmed_at: datetime | None
    next_step: Literal["interaction_preference", "post_payment_pending"]
    idempotent: bool = False
