from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from pydantic import ValidationError

from app.schemas.generation_context import GenerationContextBuildResult, GenerationContextV1
from app.schemas.order import Q1V2Payload, Q2V2Payload, ScopeQualificationQ2V2
from app.services.interaction_safety_policy import interaction_safety_policy_v1


DESIGN_DIRECTION_KEYS = {
    "clean-modern", "premium-business", "bold-startup", "luxury-elite",
    "tech-minimal", "creative-studio", "nordic-soft", "dark-contrast",
}
INTERACTION_PREFERENCE_KEYS = {"subtle", "recommended", "more_expressive"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _manual(*codes: str) -> GenerationContextBuildResult:
    return GenerationContextBuildResult(status="manual_review", reason_codes=list(dict.fromkeys(codes)), context=None)


def _package_constraints(package: str) -> dict[str, Any]:
    if package == "starter":
        return {"package_contract_version": "ireland_accepted_v1", "page_capacity_kind": "included_plus_one", "included_pages": 1, "minimum_pages": None, "max_additional_pages": 1, "final_architecture_requires_site_planner": True}
    if package == "business":
        return {"package_contract_version": "ireland_accepted_v1", "page_capacity_kind": "included_plus_one", "included_pages": 3, "minimum_pages": None, "max_additional_pages": 1, "final_architecture_requires_site_planner": True}
    if package == "reference":
        return {"package_contract_version": "ireland_accepted_v1", "page_capacity_kind": "minimum_unbounded", "included_pages": None, "minimum_pages": 5, "max_additional_pages": None, "final_architecture_requires_site_planner": True}
    if package == "advanced":
        return {"package_contract_version": "ireland_accepted_v1", "page_capacity_kind": "scope_defined", "included_pages": None, "minimum_pages": None, "max_additional_pages": None, "final_architecture_requires_site_planner": True}
    raise ValueError("resolved package is not authoritative")


def _source_signals(q1: Q1V2Payload, q2: Q2V2Payload) -> dict[str, Any]:
    examples = q1.examples_context if isinstance(q1.examples_context, dict) else {}
    selected = examples.get("selected_example_id")
    selected = selected.strip()[:128] if isinstance(selected, str) and selected.strip() else None
    viewed = examples.get("viewed_examples")
    viewed_ids = list(dict.fromkeys(str(item).strip()[:128] for item in viewed if isinstance(item, str) and item.strip())) if isinstance(viewed, list) else []
    hints = q2.analysis_hints
    has_hints = bool(hints.business_hints or hints.function_hints or hints.complexity_hints)
    return {
        "selected_example_id": selected,
        "selected_example_status": "unverified_advisory" if selected else "absent",
        "viewed_example_ids": viewed_ids,
        "existing_site_hints_status": "unconfirmed_advisory" if has_hints else "absent",
        "existing_site_business_hints": list(hints.business_hints),
        "existing_site_function_hints": list(hints.function_hints),
        "existing_site_complexity_hints": list(hints.complexity_hints),
        "visual_affinities": [],
        # No collection contract exists yet. Unknown sibling payloads are deliberately ignored.
        "example_interaction_signals": {"contract_version": None, "signals": []},
    }


def build_generation_context_v1(order: Any, journey_project_id: str) -> GenerationContextBuildResult:
    """Purely normalize trusted Order state. No session, writes, jobs, providers or prompts."""
    raw_q1 = (getattr(order, "brief_answers", None) or {}).get("q1_v2")
    raw_q2 = (getattr(order, "extended_brief", None) or {}).get("q2_v2")
    if not isinstance(raw_q1, dict):
        return _manual("q1_v2_missing")
    if not isinstance(raw_q2, dict):
        return _manual("q2_v2_missing")
    try:
        q1 = Q1V2Payload.model_validate(raw_q1)
        q2 = Q2V2Payload.model_validate({key: value for key, value in raw_q2.items() if key != "scope_qualification"})
        qualification = ScopeQualificationQ2V2.model_validate(raw_q2.get("scope_qualification"))
    except ValidationError:
        return _manual("canonical_v2_validation_failed")

    reasons: list[str] = []
    if getattr(order, "deposit_status", None) != "paid": reasons.append("verified_deposit_required")
    if qualification.eligibility != "supported": reasons.append("scope_not_supported")
    if qualification.unresolved_requirements or qualification.provisional_architecture.unresolved_requirements: reasons.append("scope_unresolved")
    if not qualification.recommended_package or not qualification.minimum_package: reasons.append("resolved_package_required")
    if not qualification.checkout_ready: reasons.append("scope_confirmation_required")
    if qualification.provisional_architecture.status != "resolved": reasons.append("architecture_unresolved")
    if qualification.provisional_page_need is None: reasons.append("architecture_page_need_required")
    unconfirmed_options = [item.key for item in q2.paid_structural_options if not item.explicit_confirmed]
    if unconfirmed_options: reasons.append("structural_option_confirmation_required")
    direction = getattr(order, "design_direction", None)
    if direction not in DESIGN_DIRECTION_KEYS: reasons.append("design_direction_v1_required")
    post_payment = (getattr(order, "extended_brief", None) or {}).get("post_payment_v2") or {}
    preference = post_payment.get("interaction_preference") if isinstance(post_payment, dict) else None
    if not isinstance(preference, dict) or preference.get("contract_version") != "v1" or preference.get("value") not in INTERACTION_PREFERENCE_KEYS or not preference.get("confirmed_at"):
        reasons.append("interaction_preference_v1_required")
    if reasons:
        return _manual(*reasons)

    package = qualification.recommended_package
    architecture = qualification.provisional_architecture
    structural_addons = [{
        "addon_key": item.key,
        "quantity": item.quantity,
        "counts_as_page": True,
        "planning_requirement": "video_entry" if item.key == "video_entry_page" else "additional_capacity",
    } for item in q2.paid_structural_options if item.explicit_confirmed]
    source_signals = _source_signals(q1, q2)
    q1_business = {
        "project_class_intent": q1.project_class_intent,
        "existing_site": {"has_existing_website": q1.existing_website.has_existing_website, "url": q1.existing_website.url, "analyzer_hints_are_advisory": True},
    }
    q1_normalized = q1_business | {
        "selected_example_id": source_signals["selected_example_id"],
        "selected_example_status": source_signals["selected_example_status"],
        "viewed_example_ids": source_signals["viewed_example_ids"],
    }
    q2_normalized = {
        "business_identity": q2.business_identity.model_dump(mode="json"),
        "business_activity": q2.business_activity.model_dump(mode="json"),
        "operating_model": q2.operating_model,
        "location": q2.location.model_dump(mode="json"),
        "audience": q2.audience,
        "primary_goal": q2.primary_goal,
        "functions": [item.model_dump(mode="json") for item in q2.functions if item.confirmed],
        "trust_materials": q2.trust_materials,
        "media": q2.media.model_dump(mode="json"),
        "social_presence": q2.social_presence.model_dump(mode="json"),
        "logo": {"choice": q2.logo.choice, "scope": q2.logo.scope},
        "delivery_context": {
            "hosting_status": q2.delivery_context.hosting_status,
            "hosting_help_included": q2.delivery_context.hosting_help_included,
            "domain_and_hosting_client_owned": q2.delivery_context.domain_and_hosting_client_owned,
            "installation_selected": q2.delivery_context.installation_selected,
        },
        "paid_structural_options": [
            {"key": item.key, "quantity": item.quantity, "counts_as_page": item.counts_as_page, "explicit_confirmed": item.explicit_confirmed}
            for item in q2.paid_structural_options if item.explicit_confirmed
        ],
        "analysis_hints": q2.analysis_hints.model_dump(mode="json"),
    }
    scope = {
        "resolved_package": package,
        "eligibility": "supported",
        "minimum_package": qualification.minimum_package,
        "provisional_page_need": qualification.provisional_page_need,
        "provisional_architecture": {
            "evidence_only_not_final_sitemap": True,
            "status": "resolved",
            "minimum_coherent_pages": architecture.minimum_coherent_pages,
            "proposed_page_roles": [role.model_dump(mode="json") for role in architecture.proposed_page_roles],
            "semantic_needs": architecture.semantic_needs,
            "reasoning_codes": architecture.reasoning_codes,
            "unresolved_requirements": [],
            "input_fingerprint": architecture.input_fingerprint,
        },
        "confirmed_structural_addons": structural_addons,
        "qualification_reason_codes": [reason.reason_code for reason in qualification.required_floor_reasons + qualification.optional_recommendation_reasons],
        "contractual_constraints": _package_constraints(package),
    }
    context_without_fingerprints = {
        "contract_version": "v1",
        "order": {"order_id": str(order.id), "journey_project_id": str(journey_project_id), "source_versions": {"q1_schema_version": 2, "q2_schema_version": 2, "scope_rule_version": qualification.rule_version, "design_direction_contract_version": "v1", "interaction_preference_contract_version": "v1"}},
        "business": {"q1": q1_business, "identity": q2.business_identity.model_dump(mode="json"), "activity": q2.business_activity.model_dump(mode="json"), "operating_model": q2.operating_model, "location": q2.location.model_dump(mode="json"), "audience": q2.audience, "primary_goal": q2.primary_goal, "trust_materials": q2.trust_materials},
        "scope": scope,
        "functionality": {"confirmed_requirements": [item.key for item in q2.functions if item.confirmed], "unresolved_requirements": [], "component_constraints": ["confirmed_requirements_only", "no_function_invention"]},
        "source_signals": source_signals,
        "visual": {"design_direction": {"contract_version": "v1", "value": direction}},
        "interaction_guidance": {"client_preference": {"contract_version": "v1", "value": preference["value"]}, "example_signals": source_signals["example_interaction_signals"], "safety_contract_version": "v1"},
        "media": {"photos": q2.media.photos, "client_photos_timing": q2.media.client_photos_timing, "video": q2.media.video, "logo": {"choice": q2.logo.choice, "simple_logo_required": q2.logo.choice == "siteformo_simple_logo", "scope": q2.logo.scope}, "trust_assets": q2.trust_materials, "social_channels": q2.social_presence.expected_channels, "social_presentation": q2.social_presence.presentation},
        "delivery": {"hosting_status": q2.delivery_context.hosting_status, "hosting_help_included": q2.delivery_context.hosting_help_included, "installation_selected": q2.delivery_context.installation_selected, "domain_and_hosting_client_owned": q2.delivery_context.domain_and_hosting_client_owned},
        "constraints": {"interaction_safety": interaction_safety_policy_v1(), "prohibited_inventions": ["unconfirmed_functionality", "unsupported_addons", "package_or_price_changes", "legal_or_payment_claims", "fabricated_business_facts"]},
    }
    # Source hashes cover normalized planning data only; timestamps and legacy/raw fields are excluded.
    fingerprints = {"q1_hash": _hash(q1_normalized), "q2_hash": _hash(q2_normalized), "scope_hash": _hash(scope), "source_signals_hash": _hash(source_signals)}
    fingerprints["context_hash"] = _hash(context_without_fingerprints | {"fingerprints": fingerprints})
    context = GenerationContextV1.model_validate(context_without_fingerprints | {"fingerprints": fingerprints, "built_at": datetime.now(timezone.utc)})
    return GenerationContextBuildResult(status="ready", reason_codes=[], context=context)
