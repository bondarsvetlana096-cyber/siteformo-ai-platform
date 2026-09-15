from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.generation_context import GenerationContextV1, InteractionSafetyPolicy
from app.schemas.site_plan import (
    CriticalActionFamily, FunctionalComponentKey, InteractionFamily, MotionLevel,
    PageRole, SitePlanV1,
)


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


class NavigationAuthorityPolicyV1(ClosedModel):
    targets_are_page_keys_only: Literal[True]
    target_page_key_must_exist: Literal[True]
    action_target_must_differ_from_current_page: Literal[True]
    navigation_edge_endpoints_must_differ: Literal[True]
    self_or_circular_navigation_cannot_satisfy_conversion_reachability: Literal[True]


class LogoAuthorityPolicyV1(ClosedModel):
    authority_path: Literal["generation_context.media.logo.simple_logo_required"]
    simple_logo_requires_confirmed_true: Literal[True]
    business_identity_does_not_authorize_logo_generation: Literal[True]
    unconfirmed_logo_generation_or_media_requirement_forbidden: Literal[True]


class CriticalActionPolicyV1(ClosedModel):
    component_family_registry: dict[
        Literal[
            "navigation", "contact_form", "enquiry_form", "file_upload",
            "booking", "reservation", "checkout", "account", "login",
            "registration", "dashboard",
        ],
        Literal["navigation", "forms", "booking", "checkout_payment", "account_security"],
    ]
    explicit_action_families: list[Literal[
        "navigation", "forms", "booking", "checkout_payment",
        "account_security", "primary_conversion_actions",
    ]]
    protected_section_must_set_critical_action_true: Literal[True]
    signature_interactions_forbidden_on_critical_sections: Literal[True]
    allowed_critical_motion_levels: list[Literal["none", "subtle", "contextual"]]
    motion_or_interaction_required_for_completion_forbidden: Literal[True]
    hover_only_required_forbidden: Literal[True]
    static_or_reduced_motion_control_availability_required: Literal[True]
    obscure_delay_replace_or_gate_required_action_forbidden: Literal[True]
    client_preference_never_overrides_critical_safety: Literal[True]


class FactualSourcePolicyV1(ClosedModel):
    content_kinds: list[Literal[
        "confirmed_fact", "generated_copy_allowed", "client_material_required",
        "placeholder_allowed", "unsupported_unresolved",
    ]]
    confirmed_fact_source_prefixes: list[Literal[
        "business.identity", "business.activity", "business.operating_model",
        "business.location", "business.audience", "business.primary_goal",
        "business.trust_materials", "source_signals.selected_example_id",
        "source_signals.viewed_example_ids",
    ]]
    confirmed_fact_requires_allowlisted_source_key: Literal[True]
    generated_copy_cannot_create_factual_claims: Literal[True]
    missing_fact_uses_client_material_placeholder_or_unresolved_kind: Literal[True]
    invented_awards_statistics_history_addresses_credentials_testimonials_product_or_team_facts_forbidden: Literal[True]


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
    navigation_authority: NavigationAuthorityPolicyV1
    logo_authority: LogoAuthorityPolicyV1
    critical_action_policy: CriticalActionPolicyV1
    factual_source_policy: FactualSourcePolicyV1
    max_semantic_repairs: Literal[1]


class ProjectCriticalActionRulesV1(ClosedModel):
    component_family_registry: dict[FunctionalComponentKey, CriticalActionFamily]
    explicit_primary_conversion_actions_are_critical: Literal[True]
    protected_section_must_set_critical_action_true: Literal[True]


class ProjectStructuralConstraintsV1(ClosedModel):
    minimum_pages: int = Field(ge=1)
    maximum_paid_capacity_if_bounded: int | None = Field(default=None, ge=1)
    video_entry_required: bool
    additional_page_capacity_confirmed: int = Field(ge=0)


class ProjectMediaConstraintsV1(ClosedModel):
    simple_logo_allowed: bool
    client_video_available: bool
    siteformo_imagery_allowed: bool
    confirmed_trust_asset_types: list[str]


class ProjectContentConstraintsV1(ClosedModel):
    allowed_confirmed_fact_sources: list[str]
    generated_copy_allowed: Literal[True]
    unresolved_items_allowed: bool


class ProjectInteractionConstraintsV1(ClosedModel):
    allowed_families: list[InteractionFamily]
    critical_motion_values: list[Literal["none", "subtle", "contextual"]]
    signature_interaction_forbidden_on_critical: Literal[True]
    hover_only_required_forbidden: Literal[True]
    reduced_motion_required: Literal[True]
    touch_safe_required: Literal[True]


class ProjectionSourceAuthorityV1(ClosedModel):
    generation_context_contract_version: Literal["v1"]
    package_contract_version: Literal["ireland_accepted_v1"]
    design_direction_contract_version: Literal["v1"]
    interaction_preference_contract_version: Literal["v1"]
    interaction_safety_contract_version: Literal["v1"]
    validator_version: Literal["v1"]
    q1_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    q2_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_signals_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlannerConstraintProjectionV1(ClosedModel):
    contract_version: Literal["v1"]
    generation_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_page_roles: list[PageRole]
    allowed_functional_components: list[FunctionalComponentKey]
    forbidden_functional_components: list[FunctionalComponentKey]
    critical_components: list[FunctionalComponentKey]
    critical_action_rules: ProjectCriticalActionRulesV1
    allowed_stateful_journey_families: list[
        Literal["booking", "reservation", "ecommerce", "account", "application", "saved_items"]
    ]
    persistent_system_allowed: bool
    structural_constraints: ProjectStructuralConstraintsV1
    media_constraints: ProjectMediaConstraintsV1
    content_constraints: ProjectContentConstraintsV1
    interaction_constraints: ProjectInteractionConstraintsV1
    source_authority: ProjectionSourceAuthorityV1
    projection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


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
    constraint_projection: PlannerConstraintProjectionV1
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
    response_id: str | None = Field(default=None, max_length=160)
    actual_model: str | None = Field(default=None, max_length=160)
    response_status: str | None = Field(default=None, max_length=40)
    service_tier: str | None = Field(default=None, max_length=40)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)

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
