from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


PlanStatus = Literal["draft", "validated", "manual_review", "invalid"]
PageRole = Literal[
    "home", "primary_conversion", "services_or_offer", "contact_or_enquiry",
    "location", "projects_or_work", "business_trust", "content_hub",
    "catalogue", "booking", "customer_area", "video_entry",
]
FunctionalComponentKey = Literal[
    "navigation", "contact_form", "enquiry_form", "direct_contact",
    "newsletter", "booking", "reservation", "catalogue", "product_list",
    "product_detail", "cart", "checkout", "search", "filters", "comparison",
    "file_upload", "content_archive", "account", "login",
    "registration", "saved_items", "alerts", "dashboard", "multilingual_switcher",
]
InteractionFamily = Literal[
    "narrative", "comparative", "exploratory", "spatial", "state_driven",
    "transactional", "editorial", "persistent_system",
]
MotionLevel = Literal["none", "subtle", "moderate", "expressive", "contextual"]
CriticalActionFamily = Literal[
    "navigation", "forms", "booking", "checkout_payment", "account_security",
    "primary_conversion_actions",
]


class ContentRequirement(ClosedModel):
    content_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal[
        "confirmed_fact", "generated_copy_allowed", "client_material_required",
        "placeholder_allowed", "unsupported_unresolved",
    ]
    source_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.]{0,127}$")
    factual_claims_must_be_confirmed: Literal[True] = True


class MediaRequirement(ClosedModel):
    media_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal[
        "client_photo", "siteformo_selected_image", "client_video", "simple_logo",
        "trust_asset", "icon_illustration", "no_media",
    ]
    required: bool


class PrimaryAction(ClosedModel):
    action_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    intent: str = Field(min_length=3, max_length=240)
    target_page_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    critical_family: CriticalActionFamily | None = None


class SignatureInteraction(ClosedModel):
    interaction_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    family: InteractionFamily
    intent: str = Field(min_length=3, max_length=240)
    required_for_completion: Literal[False] = False


class MotionPolicy(ClosedModel):
    level: MotionLevel
    required_for_completion: Literal[False] = False
    blocks_critical_action: Literal[False] = False


class MobileBehavior(ClosedModel):
    strategy: Literal["stack", "reflow", "substitute", "preserve_simple"]
    touch_safe_equivalent: Literal[True]
    hover_only_required: Literal[False]


class ReducedMotionBehavior(ClosedModel):
    strategy: Literal["no_motion", "static_equivalent", "simplified_transition"]
    preserves_content_and_functionality: Literal[True]


class PlanSection(ClosedModel):
    section_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    purpose: str = Field(min_length=3, max_length=320)
    content_requirements: list[ContentRequirement]
    media_requirements: list[MediaRequirement]
    functional_components: list[FunctionalComponentKey]
    primary_actions: list[PrimaryAction]
    interaction_families: list[InteractionFamily]
    signature_interactions: list[SignatureInteraction]
    motion_policy: MotionPolicy
    critical_action: bool
    mobile_behavior: MobileBehavior
    reduced_motion_behavior: ReducedMotionBehavior


class PlanPage(ClosedModel):
    page_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    page_role: PageRole
    purpose: str = Field(min_length=3, max_length=320)
    route_intent: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    sections: list[PlanSection] = Field(min_length=1)
    mobile_behavior: MobileBehavior
    reduced_motion_behavior: ReducedMotionBehavior


class NavigationEdge(ClosedModel):
    from_page_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    to_page_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    purpose: Literal["primary", "secondary", "conversion", "journey"]


class StatefulJourney(ClosedModel):
    journey_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    journey_type: Literal["booking", "reservation", "ecommerce", "account", "application", "saved_items"]
    state_keys: list[str] = Field(min_length=2)
    entry_page_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    completion_page_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    required_components: list[FunctionalComponentKey] = Field(min_length=1)
    persistence: Literal["none", "session", "account"]


class SharedComponent(ClosedModel):
    component_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    component: FunctionalComponentKey


class InventoryItem(ClosedModel):
    item_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    required: bool


class AccessibilityConstraints(ClosedModel):
    keyboard_operable: Literal[True]
    touch_safe: Literal[True]
    reduced_motion_supported: Literal[True]
    critical_actions_stable: Literal[True]
    no_hover_only_required_actions: Literal[True]


class GenerationConstraints(ClosedModel):
    source_design_direction: str
    source_interaction_preference: Literal["subtle", "recommended", "more_expressive"]
    no_unconfirmed_functionality: Literal[True]
    no_unsupported_addons: Literal[True]
    no_factual_claim_invention: Literal[True]
    no_raw_code: Literal[True]


class UnresolvedItem(ClosedModel):
    item_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    category: Literal["content", "functionality", "structure", "media", "scope"]
    reason_code: Literal[
        "missing_confirmed_fact", "unsupported_functionality", "structural_ambiguity",
        "missing_client_material", "scope_conflict",
    ]


PlannerReasonCode = Literal[
    "refine_provisional_architecture", "split_distinct_user_journeys",
    "protect_critical_action_stability", "protect_checkout_stability",
    "reuse_visual_affinity", "mobile_substitute_spatial_interaction",
    "respect_confirmed_scope", "preserve_reduced_motion_access",
]


class SitePlanV1(ClosedModel):
    contract_version: Literal["v1"]
    generation_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_status: PlanStatus
    pages: list[PlanPage] = Field(min_length=1)
    cross_page_navigation: list[NavigationEdge]
    stateful_journeys: list[StatefulJourney]
    shared_components: list[SharedComponent]
    content_inventory: list[InventoryItem]
    media_inventory: list[InventoryItem]
    functional_component_inventory: list[FunctionalComponentKey]
    accessibility_constraints: AccessibilityConstraints
    generation_constraints: GenerationConstraints
    unresolved_items: list[UnresolvedItem]
    planner_reasoning_codes: list[PlannerReasonCode]
    validator_version: Literal["v1"]
    site_plan_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class SitePlanValidationResult(ClosedModel):
    status: Literal["valid", "manual_review", "invalid"]
    reason_codes: list[str]
    plan: SitePlanV1 | None
