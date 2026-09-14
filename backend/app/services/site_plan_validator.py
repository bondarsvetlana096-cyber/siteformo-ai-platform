from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from pydantic import ValidationError

from app.schemas.generation_context import GenerationContextV1
from app.schemas.site_plan import SitePlanV1, SitePlanValidationResult


SITE_PLAN_VALIDATOR_VERSION = "v1"

UNIVERSAL_COMPONENTS = {"navigation"}
REQUIREMENT_COMPONENTS = {
    "standard_content_contact": {"contact_form"},
    "contact_enquiry": {"enquiry_form", "contact_form"},
    "direct_contact_channel": {"direct_contact"},
    "blog_news": {"content_archive"},
    "newsletter": {"newsletter"},
    "booking": {"booking", "reservation"},
    "catalogue_display": {"catalogue", "product_list", "product_detail"},
    "transactional_ecommerce": {"catalogue", "product_list", "product_detail", "cart", "checkout"},
    "customer_account_login": {"account", "login", "registration"},
    "portal_membership": {"account", "login", "registration", "dashboard", "saved_items", "alerts"},
    "multilingual": {"multilingual_switcher"},
    "advanced_form": {"contact_form", "enquiry_form", "file_upload"},
    "search_filter_compare": {"search", "filters", "comparison"},
    "custom_other": set(),
}
CRITICAL_COMPONENTS = {
    "navigation": "navigation", "contact_form": "forms", "enquiry_form": "forms",
    "file_upload": "forms", "booking": "booking", "reservation": "booking",
    "checkout": "checkout_payment", "account": "account_security", "login": "account_security",
    "registration": "account_security", "dashboard": "account_security",
}
JOURNEY_REQUIREMENTS = {
    "booking": "booking", "reservation": "booking", "ecommerce": "transactional_ecommerce",
    "account": "customer_account_login", "application": "advanced_form", "saved_items": "portal_membership",
}
PAGE_ROLE_REQUIREMENTS = {
    "contact_or_enquiry": {"standard_content_contact", "contact_enquiry", "direct_contact_channel", "advanced_form"},
    "content_hub": {"blog_news"}, "catalogue": {"catalogue_display", "transactional_ecommerce"},
    "booking": {"booking"}, "customer_area": {"customer_account_login", "portal_membership"},
}
CONFIRMED_FACT_SOURCE_PREFIXES = (
    "business.identity", "business.activity", "business.operating_model", "business.location",
    "business.audience", "business.primary_goal", "business.trust_materials",
    "source_signals.selected_example_id", "source_signals.viewed_example_ids",
)
FORBIDDEN_TEXT = re.compile(
    r"(?:<\s*/?\s*(?:script|style|div|html)|javascript\s*:|\b(?:gsap|three(?:\.js|js)|jquery|react|vue|lenis|tilt|morph|magnetic|framer[_ -]?motion)\b|"
    r"\b(?:system prompt|ignore previous instructions|chain[- ]of[- ]thought)\b|https?://|www\.|"
    r"\b(?:document|window)\.[a-z_]|\bfunction\s*\(|=>|\{\s*[a-z-]+\s*:)",
    re.IGNORECASE,
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _all_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def _result(status: str, reasons: list[str], plan: SitePlanV1 | None = None) -> SitePlanValidationResult:
    return SitePlanValidationResult(status=status, reason_codes=list(dict.fromkeys(reasons)), plan=plan)


def _allowed_components(context: GenerationContextV1) -> set[str]:
    allowed = set(UNIVERSAL_COMPONENTS)
    for requirement in context.functionality.confirmed_requirements:
        allowed.update(REQUIREMENT_COMPONENTS.get(requirement, set()))
    return allowed


def _is_confirmed_fact_source(source_key: str | None) -> bool:
    return bool(source_key) and any(
        source_key == prefix or source_key.startswith(prefix + ".")
        for prefix in CONFIRMED_FACT_SOURCE_PREFIXES
    )


def _page_capacity(context: GenerationContextV1) -> int | None:
    constraints = context.scope.contractual_constraints
    if constraints.page_capacity_kind == "included_plus_one":
        extra = sum(item.quantity for item in context.scope.confirmed_structural_addons if item.addon_key == "additional_page")
        video = sum(item.quantity for item in context.scope.confirmed_structural_addons if item.addon_key == "video_entry_page")
        return int(constraints.included_pages or 0) + extra + video
    return None


def validate_site_plan_v1(
    context: GenerationContextV1,
    candidate: SitePlanV1 | dict[str, Any],
) -> SitePlanValidationResult:
    """Validate a candidate plan without persistence, orchestration, jobs, or providers."""
    try:
        plan = candidate if isinstance(candidate, SitePlanV1) else SitePlanV1.model_validate(candidate)
    except ValidationError:
        return _result("invalid", ["schema_validation_failed"])

    reasons: list[str] = []
    manual: list[str] = []
    if plan.generation_context_hash != context.fingerprints.context_hash:
        reasons.append("generation_context_hash_mismatch")
    if plan.validator_version != SITE_PLAN_VALIDATOR_VERSION:
        reasons.append("validator_version_unsupported")
    if plan.generation_constraints.source_design_direction != context.visual.design_direction.value:
        reasons.append("design_direction_trace_mismatch")
    if plan.generation_constraints.source_interaction_preference != context.interaction_guidance.client_preference.value:
        reasons.append("interaction_preference_trace_mismatch")
    if plan.unresolved_items:
        manual.append("unresolved_items_present")
    if context.functionality.unresolved_requirements:
        manual.append("generation_context_unresolved")

    page_keys = [page.page_key for page in plan.pages]
    route_intents = [page.route_intent for page in plan.pages]
    if len(page_keys) != len(set(page_keys)): reasons.append("duplicate_page_key")
    if len(route_intents) != len(set(route_intents)): reasons.append("duplicate_route_intent")
    known_pages = set(page_keys)
    confirmed_requirements = set(context.functionality.confirmed_requirements)
    for page in plan.pages:
        role_requirements = PAGE_ROLE_REQUIREMENTS.get(page.page_role)
        if role_requirements and not (role_requirements & confirmed_requirements): reasons.append("unsupported_page_role")
        section_keys = [section.section_key for section in page.sections]
        if len(section_keys) != len(set(section_keys)): reasons.append("duplicate_section_key")
        for section in page.sections:
            if any(item.kind == "unsupported_unresolved" for item in section.content_requirements):
                manual.append("unresolved_content_present")
            if any(
                item.kind == "confirmed_fact" and not _is_confirmed_fact_source(item.source_key)
                for item in section.content_requirements
            ):
                reasons.append("unconfirmed_factual_source")
            components = set(section.functional_components)
            if not components <= _allowed_components(context): reasons.append("unsupported_functional_component")
            critical_families = {CRITICAL_COMPONENTS[item] for item in components if item in CRITICAL_COMPONENTS}
            critical_families.update(action.critical_family for action in section.primary_actions if action.critical_family)
            if critical_families and not section.critical_action: reasons.append("critical_action_not_marked")
            if section.critical_action and (section.signature_interactions or section.motion_policy.level not in {"none", "subtle", "contextual"}):
                reasons.append("unsafe_critical_action_interaction")
            if (section.interaction_families or section.motion_policy.level != "none") and section.reduced_motion_behavior.strategy not in {"no_motion", "static_equivalent", "simplified_transition"}:
                reasons.append("reduced_motion_missing")
            for action in section.primary_actions:
                if action.target_page_key and action.target_page_key not in known_pages: reasons.append("invalid_page_reference")
                if action.target_page_key == page.page_key: reasons.append("unsafe_self_reference")
            for media in section.media_requirements:
                if media.kind == "simple_logo" and not context.media.logo.simple_logo_required: reasons.append("unconfirmed_simple_logo")
                if media.kind == "client_video" and context.media.video == "none": reasons.append("unconfirmed_video")
                if media.kind == "client_photo" and context.media.photos != "client_provides": reasons.append("unconfirmed_client_photo")
                if media.kind == "siteformo_selected_image" and context.media.photos != "siteformo_selects": reasons.append("unconfirmed_siteformo_image")
                if media.kind == "trust_asset" and not context.media.trust_assets: reasons.append("unconfirmed_trust_asset")

    for edge in plan.cross_page_navigation:
        if edge.from_page_key not in known_pages or edge.to_page_key not in known_pages: reasons.append("invalid_page_reference")
        if edge.from_page_key == edge.to_page_key: reasons.append("unsafe_self_reference")
    if not any(edge.purpose == "conversion" for edge in plan.cross_page_navigation) and len(plan.pages) > 1:
        reasons.append("primary_conversion_path_missing")
    if not any(
        action.critical_family == "primary_conversion_actions"
        for page in plan.pages for section in page.sections for action in section.primary_actions
    ):
        reasons.append("primary_conversion_action_missing")

    allowed = _allowed_components(context)
    inventory = set(plan.functional_component_inventory)
    used = {component for page in plan.pages for section in page.sections for component in section.functional_components}
    used.update(shared.component for shared in plan.shared_components)
    if not inventory <= allowed or not used <= allowed: reasons.append("unsupported_functional_component")
    if used != inventory: reasons.append("functional_inventory_mismatch")
    for requirement in confirmed_requirements:
        backed_components = REQUIREMENT_COMPONENTS.get(requirement, set())
        if requirement == "custom_other":
            manual.append("custom_function_requires_manual_review")
        elif backed_components and not (backed_components & used):
            reasons.append("confirmed_functionality_missing")

    if plan.pages:
        adjacency = {key: set() for key in known_pages}
        for edge in plan.cross_page_navigation:
            if edge.from_page_key in known_pages and edge.to_page_key in known_pages:
                adjacency[edge.from_page_key].add(edge.to_page_key)
        reachable = {page_keys[0]}
        pending = [page_keys[0]]
        while pending:
            current = pending.pop()
            for target in adjacency[current] - reachable:
                reachable.add(target)
                pending.append(target)
        if reachable != known_pages: reasons.append("unreachable_page")
        if len(plan.pages) > 1 and not any(
            edge.purpose == "conversion" and edge.from_page_key in reachable and edge.to_page_key in reachable
            for edge in plan.cross_page_navigation
        ):
            reasons.append("conversion_path_unreachable")

    for journey in plan.stateful_journeys:
        if journey.entry_page_key not in known_pages or journey.completion_page_key not in known_pages: reasons.append("invalid_journey_page")
        requirement = JOURNEY_REQUIREMENTS[journey.journey_type]
        if requirement not in context.functionality.confirmed_requirements: reasons.append("invented_stateful_journey")
        if not set(journey.required_components) <= used: reasons.append("journey_component_missing")
        if journey.persistence == "account" and not ({"customer_account_login", "portal_membership"} & set(context.functionality.confirmed_requirements)):
            reasons.append("invented_persistent_system")
        if len(journey.state_keys) != len(set(journey.state_keys)): reasons.append("duplicate_journey_state")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", state) for state in journey.state_keys): reasons.append("invalid_journey_state_key")

    video_confirmed = any(item.addon_key == "video_entry_page" for item in context.scope.confirmed_structural_addons)
    has_video_page = any(page.page_role == "video_entry" for page in plan.pages)
    if video_confirmed and not has_video_page: reasons.append("confirmed_video_entry_missing")
    if has_video_page and not video_confirmed: reasons.append("unconfirmed_video_entry")
    capacity = _page_capacity(context)
    if capacity is not None and len(plan.pages) > capacity: manual.append("scope_conflict")
    if (
        len(plan.pages) < context.scope.provisional_architecture.minimum_coherent_pages
        and "refine_provisional_architecture" not in plan.planner_reasoning_codes
    ):
        manual.append("architecture_below_minimum_without_reason")

    if any(FORBIDDEN_TEXT.search(text) for text in _all_strings(plan.model_dump(mode="json"))):
        reasons.append("raw_code_prompt_or_url_forbidden")
    if reasons:
        return _result("invalid", reasons)
    if manual:
        return _result("manual_review", manual)

    payload = plan.model_dump(mode="json", exclude={"site_plan_hash", "created_at", "plan_status"})
    payload["generation_context_hash"] = context.fingerprints.context_hash
    validated_data = deepcopy(plan.model_dump(mode="json"))
    validated_data["plan_status"] = "validated"
    validated_data["site_plan_hash"] = _hash(payload)
    return _result("valid", [], SitePlanV1.model_validate(validated_data))
