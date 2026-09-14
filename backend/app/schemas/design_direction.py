from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


DesignDirectionKey = Literal[
    "clean-modern",
    "premium-business",
    "bold-startup",
    "luxury-elite",
    "tech-minimal",
    "creative-studio",
    "nordic-soft",
    "dark-contrast",
]


class DesignDirectionOption(BaseModel):
    key: DesignDirectionKey
    label: str


class DesignDirectionSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: DesignDirectionKey


class DesignDirectionStateResponse(BaseModel):
    order_id: str
    stage: Literal["design_direction_required", "design_direction_confirmed"]
    contract_version: Literal["v1"]
    available_directions: list[DesignDirectionOption]
    selected_direction: DesignDirectionKey | None
    confirmed_at: None = None
    next_step: Literal["design_direction", "post_payment_pending"]
    idempotent: bool = False
