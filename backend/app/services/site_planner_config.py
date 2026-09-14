from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SitePlannerConfigV1(BaseModel):
    """Inert planner configuration. It reads no environment and holds no credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_identifier: str = Field(min_length=1, max_length=80)
    model_identifier: str = Field(min_length=1, max_length=120)
    provider_config_version: str = Field(min_length=1, max_length=80)
    planning_strategy_version: str = Field(default="single_call_v1", min_length=1, max_length=80)
    max_output_bytes: int = Field(default=131_072, ge=4_096, le=1_048_576)
    max_transport_retries: int = Field(default=1, ge=0, le=1)
    max_single_call_pages: int = Field(default=12, ge=1, le=100)
    projected_sections_per_page: int = Field(default=6, ge=1, le=20)
    max_projected_sections: int = Field(default=72, ge=1, le=500)
    max_stateful_requirements: int = Field(default=4, ge=0, le=20)
