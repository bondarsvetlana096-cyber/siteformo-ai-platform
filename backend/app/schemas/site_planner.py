from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.generation_context import GenerationContextV1, InteractionSafetyPolicy
from app.schemas.site_plan import FunctionalComponentKey, InteractionFamily, MotionLevel, PageRole, SitePlanV1


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ProviderErrorCategory = Literal[
    "timeout", "rate_limit", "provider_5xx", "authentication", "configuration",
    "empty_response", "malformed_response", "refusal", "truncated",
    "content_rejection", "unknown",
]
PlannerStatus = Literal["valid", "manual_review", "temporary_failure", "provider_failure", "stale_context"]
AttemptType = Literal["initial", "repair"]
ValidatorReasonCode = Literal[
    "schema_validation_failed", "generation_context_hash_mismatch",
    "validator_version_unsupported", "design_direction_trace_mismatch",
    "interaction_preference_trace_mismatch", "unresolved_items_present",
    "generation_context_unresolved", "duplicate_page_key", "duplicate_route_intent",
    "unsupported_page_role", "duplicate_section_key", "unresolved_content_present",
    "unconfirmed_factual_source", "unsupported_functional_component",
    "critical_action_not_marked", "unsafe_critical_action_interaction",
    "reduced_motion_missing", "invalid_page_reference", "unsafe_self_reference",
    "unconfirmed_simple_logo", "unconfirmed_video", "unconfirmed_client_photo",
    "unconfirmed_siteformo_image", "unconfirmed_trust_asset",
    "primary_conversion_path_missing", "primary_conversion_action_missing",
    "functional_inventory_mismatch", "custom_function_requires_manual_review",
    "confirmed_functionality_missing", "unreachable_page",
    "conversion_path_unreachable", "invalid_journey_page", "invented_stateful_journey",
    "journey_component_missing", "invented_persistent_system",
    "duplicate_journey_state", "invalid_journey_state_key",
    "confirmed_video_entry_missing", "unconfirmed_video_entry", "scope_conflict",
    "architecture_below_minimum_without_reason", "raw_code_prompt_or_url_forbidden",
]


class PlannerPolicyV1(ClosedModel):
    contract_version: Literal["v1"]
    context_is_immutable_authority: Literal[True]
    output_contract: Literal["site_plan_v1"]
    one_candidate_only: Literal[True]
    prose_reasoning_forbidden: Literal[True]
    raw_code_forbidden: Literal[True]
    arbitrary_urls_forbidden: Literal[True]
    functionality_invention_forbidden: Literal[True]
    addon_invention_forbidden: Literal[True]
    package_or_capacity_change_forbidden: Literal[True]
    payment_or_legal_decisions_forbidden: Literal[True]
    factual_sources_must_be_confirmed: Literal[True]
    mobile_coverage_required: Literal[True]
    reduced_motion_coverage_required: Literal[True]
    interaction_safety_required: Literal[True]
    max_semantic_repairs: Literal[1]


class RepairSectionProjection(ClosedModel):
    section_key: str | None
    functional_components: list[str]
    interaction_families: list[str]
    motion_level: str | None
    critical_action: bool | None
    target_page_keys: list[str]


class RepairPageProjection(ClosedModel):
    page_key: str | None
    page_role: str | None
    route_intent: str | None
    sections: list[RepairSectionProjection]


class CandidateRepairProjection(ClosedModel):
    generation_context_hash: str | None
    present_root_fields: list[str]
    pages: list[RepairPageProjection]
    navigation_page_keys: list[tuple[str | None, str | None]]
    journey_types: list[str]
    functional_component_inventory: list[str]
    unresolved_item_count: int


class SitePlannerProviderRequest(ClosedModel):
    planner_contract_version: Literal["v1"]
    site_plan_schema_version: Literal["v1"]
    validator_version: Literal["v1"]
    attempt_type: AttemptType
    attempt_number: int = Field(ge=0, le=1)
    generation_context: GenerationContextV1
    generation_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    site_plan_json_schema: dict[str, Any]
    interaction_safety_contract: InteractionSafetyPolicy
    planner_policy: PlannerPolicyV1
    prior_candidate_projection: CandidateRepairProjection | None = None
    validator_reason_codes: list[ValidatorReasonCode] = Field(default_factory=list)


class SitePlannerProviderResult(ClosedModel):
    status: Literal["candidate", "failure"]
    candidate: dict[str, Any] | None = None
    error_category: ProviderErrorCategory | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reported_output_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def consistent_status(self) -> "SitePlannerProviderResult":
        if self.status == "candidate" and (self.candidate is None or self.error_category is not None):
            raise ValueError("candidate result must contain only a candidate")
        if self.status == "failure" and (self.candidate is not None or self.error_category is None):
            raise ValueError("failure result must contain only an error category")
        return self


class PlannerAttemptMetadata(ClosedModel):
    attempt_type: AttemptType
    semantic_attempt_number: int = Field(ge=0, le=1)
    provider_call_count: int = Field(ge=1, le=2)
    latency_ms: int = Field(ge=0)
    output_bytes: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    classification: Literal["candidate", "parse_failure", "validation_failure", "valid", "provider_failure", "oversized"]
    reason_codes: list[str]
    provider_error_category: ProviderErrorCategory | None = None


class FinalSitePlannerResult(ClosedModel):
    status: PlannerStatus
    reason_codes: list[str]
    attempt_count: int = Field(ge=0, le=2)
    provider_call_count: int = Field(ge=0, le=4)
    operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    planner_contract_version: Literal["v1"]
    provider_config_version: str
    validator_version: Literal["v1"]
    provider_identifier: str
    model_identifier: str
    provider_error_category: ProviderErrorCategory | None = None
    validated_plan: SitePlanV1 | None = None
    attempts: list[PlannerAttemptMetadata]
    started_at: datetime
    completed_at: datetime
