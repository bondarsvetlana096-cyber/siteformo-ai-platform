from __future__ import annotations

import hashlib
import json
from typing import get_args

from app.schemas.generation_context import GenerationContextV1
from app.schemas.site_plan import FunctionalComponentKey, InteractionFamily, PageRole
from app.schemas.site_planner import PlannerConstraintProjectionV1
from app.services.site_plan_validator import (
    CONFIRMED_FACT_SOURCE_PREFIXES,
    CRITICAL_COMPONENTS,
    JOURNEY_REQUIREMENTS,
    PAGE_ROLE_REQUIREMENTS,
    REQUIREMENT_COMPONENTS,
    SITE_PLAN_VALIDATOR_VERSION,
    UNIVERSAL_COMPONENTS,
)


PLANNER_CONSTRAINT_PROJECTION_VERSION = "v1"
_PRESENTATIONAL_PAGE_ROLES = {
    "home", "primary_conversion", "services_or_offer", "location",
    "projects_or_work", "business_trust",
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _allowed_components(context: GenerationContextV1) -> set[str]:
    allowed = set(UNIVERSAL_COMPONENTS)
    for requirement in context.functionality.confirmed_requirements:
        allowed.update(REQUIREMENT_COMPONENTS.get(requirement, set()))
    return allowed


def _allowed_page_roles(context: GenerationContextV1) -> set[str]:
    confirmed = set(context.functionality.confirmed_requirements)
    roles = set(_PRESENTATIONAL_PAGE_ROLES)
    roles.update(
        role for role, requirements in PAGE_ROLE_REQUIREMENTS.items()
        if requirements & confirmed
    )
    if any(addon.addon_key == "video_entry_page" for addon in context.scope.confirmed_structural_addons):
        roles.add("video_entry")
    return roles


def _paid_capacity(context: GenerationContextV1) -> int | None:
    constraints = context.scope.contractual_constraints
    if constraints.page_capacity_kind != "included_plus_one":
        return None
    addon_pages = sum(addon.quantity for addon in context.scope.confirmed_structural_addons)
    return int(constraints.included_pages or 0) + addon_pages


def build_planner_constraint_projection_v1(
    context: GenerationContextV1,
) -> PlannerConstraintProjectionV1:
    """Derive compact provider guidance solely from committed server authority."""
    allowed_components = _allowed_components(context)
    component_vocabulary = set(get_args(FunctionalComponentKey))
    critical_registry = {
        component: family for component, family in CRITICAL_COMPONENTS.items()
        if component in allowed_components
    }
    confirmed = set(context.functionality.confirmed_requirements)
    allowed_journeys = sorted(
        journey for journey, requirement in JOURNEY_REQUIREMENTS.items()
        if requirement in confirmed
    )
    addons = context.scope.confirmed_structural_addons
    payload = {
        "contract_version": PLANNER_CONSTRAINT_PROJECTION_VERSION,
        "generation_context_hash": context.fingerprints.context_hash,
        "allowed_page_roles": sorted(_allowed_page_roles(context)),
        "allowed_functional_components": sorted(allowed_components),
        "forbidden_functional_components": sorted(component_vocabulary - allowed_components),
        "critical_components": sorted(critical_registry),
        "critical_action_rules": {
            "component_family_registry": dict(sorted(critical_registry.items())),
            "explicit_primary_conversion_actions_are_critical": True,
            "protected_section_must_set_critical_action_true": True,
        },
        "allowed_stateful_journey_families": allowed_journeys,
        "persistent_system_allowed": bool(
            {"customer_account_login", "portal_membership"} & confirmed
        ),
        "structural_constraints": {
            "minimum_pages": context.scope.provisional_architecture.minimum_coherent_pages,
            "maximum_paid_capacity_if_bounded": _paid_capacity(context),
            "video_entry_required": any(addon.addon_key == "video_entry_page" for addon in addons),
            "additional_page_capacity_confirmed": sum(
                addon.quantity for addon in addons if addon.addon_key == "additional_page"
            ),
        },
        "media_constraints": {
            "simple_logo_allowed": context.media.logo.simple_logo_required,
            "client_video_available": context.media.video != "none",
            "siteformo_imagery_allowed": context.media.photos == "siteformo_selects",
            "confirmed_trust_asset_types": sorted(context.media.trust_assets),
        },
        "content_constraints": {
            "allowed_confirmed_fact_sources": list(CONFIRMED_FACT_SOURCE_PREFIXES),
            "generated_copy_allowed": True,
            "unresolved_items_allowed": bool(
                context.functionality.unresolved_requirements
                or context.scope.provisional_architecture.unresolved_requirements
            ),
        },
        "interaction_constraints": {
            "allowed_families": sorted(get_args(InteractionFamily)),
            "critical_motion_values": ["none", "subtle", "contextual"],
            "signature_interaction_forbidden_on_critical": True,
            "hover_only_required_forbidden": True,
            "reduced_motion_required": True,
            "touch_safe_required": True,
        },
        "source_authority": {
            "generation_context_contract_version": context.contract_version,
            "package_contract_version": context.scope.contractual_constraints.package_contract_version,
            "design_direction_contract_version": context.visual.design_direction.contract_version,
            "interaction_preference_contract_version": context.interaction_guidance.client_preference.contract_version,
            "interaction_safety_contract_version": context.constraints.interaction_safety.contract_version,
            "validator_version": SITE_PLAN_VALIDATOR_VERSION,
            "q1_hash": context.fingerprints.q1_hash,
            "q2_hash": context.fingerprints.q2_hash,
            "scope_hash": context.fingerprints.scope_hash,
            "source_signals_hash": context.fingerprints.source_signals_hash,
        },
    }
    payload["projection_hash"] = _hash(payload)
    return PlannerConstraintProjectionV1.model_validate(payload)
