from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


PackageKey = Literal["starter", "business", "reference", "advanced"]
DesignDirectionKey = Literal[
    "clean-modern", "premium-business", "bold-startup", "luxury-elite",
    "tech-minimal", "creative-studio", "nordic-soft", "dark-contrast",
]
InteractionPreferenceKey = Literal["subtle", "recommended", "more_expressive"]


class SourceVersions(ClosedModel):
    q1_schema_version: Literal[2]
    q2_schema_version: Literal[2]
    scope_rule_version: Literal["ireland_accepted_v1"]
    design_direction_contract_version: Literal["v1"]
    interaction_preference_contract_version: Literal["v1"]


class OrderContext(ClosedModel):
    order_id: str
    journey_project_id: str
    source_versions: SourceVersions


class ExistingSiteContext(ClosedModel):
    has_existing_website: bool
    url: str | None
    analyzer_hints_are_advisory: Literal[True] = True


class Q1PlanningContext(ClosedModel):
    project_class_intent: str
    existing_site: ExistingSiteContext


class BusinessIdentityContext(ClosedModel):
    status: str
    name: str | None


class BusinessActivityContext(ClosedModel):
    niche: str
    broad_model: str
    other_clarification: str | None


class LocationContext(ClosedModel):
    public_address: str | None
    service_area: str | None
    multiple_locations_summary: str | None


class BusinessContext(ClosedModel):
    q1: Q1PlanningContext
    identity: BusinessIdentityContext
    activity: BusinessActivityContext
    operating_model: str
    location: LocationContext
    audience: str
    primary_goal: str
    trust_materials: list[str]


class ProvisionalPageRole(ClosedModel):
    role_key: str
    purpose: str
    requirements_served: list[str]


class ProvisionalArchitectureEvidence(ClosedModel):
    evidence_only_not_final_sitemap: Literal[True] = True
    status: Literal["resolved"]
    minimum_coherent_pages: int = Field(ge=1)
    proposed_page_roles: list[ProvisionalPageRole]
    semantic_needs: list[str]
    reasoning_codes: list[str]
    unresolved_requirements: list[str]
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class StructuralAddon(ClosedModel):
    addon_key: Literal["additional_page", "video_entry_page"]
    quantity: int = Field(ge=1)
    counts_as_page: Literal[True]
    planning_requirement: Literal["additional_capacity", "video_entry"]


class ContractualConstraints(ClosedModel):
    package_contract_version: Literal["ireland_accepted_v1"]
    page_capacity_kind: Literal["included_plus_one", "minimum_unbounded", "scope_defined"]
    included_pages: int | None
    minimum_pages: int | None
    max_additional_pages: int | None
    final_architecture_requires_site_planner: Literal[True] = True


class ScopeContext(ClosedModel):
    resolved_package: PackageKey
    eligibility: Literal["supported"]
    minimum_package: PackageKey
    provisional_page_need: int = Field(ge=1)
    provisional_architecture: ProvisionalArchitectureEvidence
    confirmed_structural_addons: list[StructuralAddon]
    qualification_reason_codes: list[str]
    contractual_constraints: ContractualConstraints


class FunctionalityContext(ClosedModel):
    confirmed_requirements: list[str]
    unresolved_requirements: list[str]
    component_constraints: list[Literal["confirmed_requirements_only", "no_function_invention"]]


class ExampleInteractionSignals(ClosedModel):
    contract_version: None = None
    signals: list[None] = Field(default_factory=list)


class SourceSignals(ClosedModel):
    selected_example_id: str | None
    selected_example_status: Literal["unverified_advisory", "absent"]
    viewed_example_ids: list[str]
    existing_site_hints_status: Literal["unconfirmed_advisory", "absent"]
    existing_site_business_hints: list[str]
    existing_site_function_hints: list[str]
    existing_site_complexity_hints: list[str]
    visual_affinities: list[str]
    example_interaction_signals: ExampleInteractionSignals


class VersionedDesignDirection(ClosedModel):
    contract_version: Literal["v1"]
    value: DesignDirectionKey


class VersionedInteractionPreference(ClosedModel):
    contract_version: Literal["v1"]
    value: InteractionPreferenceKey


class VisualContext(ClosedModel):
    design_direction: VersionedDesignDirection


class InteractionGuidance(ClosedModel):
    client_preference: VersionedInteractionPreference
    example_signals: ExampleInteractionSignals
    safety_contract_version: Literal["v1"]


class SimpleLogoScope(ClosedModel):
    one_direction: bool | None = None
    one_revision: bool | None = None
    web_ready_delivery: bool | None = None
    full_branding: bool | None = None
    naming: bool | None = None
    trademark_or_legal_clearance: bool | None = None


class LogoRequirement(ClosedModel):
    choice: str
    simple_logo_required: bool
    scope: SimpleLogoScope


class MediaContext(ClosedModel):
    photos: str
    client_photos_timing: str | None
    video: str
    logo: LogoRequirement
    trust_assets: list[str]
    social_channels: list[str]
    social_presentation: Literal["flexible"]


class DeliveryContext(ClosedModel):
    hosting_status: str
    hosting_help_included: bool
    installation_selected: bool
    domain_and_hosting_client_owned: bool


class InteractionSafetyPolicy(ClosedModel):
    contract_version: Literal["v1"]
    enhancement_purposes: list[str]
    protected_critical_actions: list[str]
    keyboard_operability_required: Literal[True]
    touch_safe_mobile_behavior_required: Literal[True]
    reduced_motion_alternative_required: Literal[True]
    critical_action_stability_required: Literal[True]
    hover_only_required_actions_forbidden: Literal[True]
    omit_harmful_interaction_required: Literal[True]


class PlanningConstraints(ClosedModel):
    interaction_safety: InteractionSafetyPolicy
    prohibited_inventions: list[str]


class Fingerprints(ClosedModel):
    q1_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    q2_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_signals_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationContextV1(ClosedModel):
    contract_version: Literal["v1"]
    order: OrderContext
    business: BusinessContext
    scope: ScopeContext
    functionality: FunctionalityContext
    source_signals: SourceSignals
    visual: VisualContext
    interaction_guidance: InteractionGuidance
    media: MediaContext
    delivery: DeliveryContext
    constraints: PlanningConstraints
    fingerprints: Fingerprints
    built_at: datetime


class GenerationContextBuildResult(ClosedModel):
    status: Literal["ready", "manual_review"]
    reason_codes: list[str]
    context: GenerationContextV1 | None
