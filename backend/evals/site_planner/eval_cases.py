from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.generation_context import GenerationContextV1
from app.services.interaction_safety_policy import interaction_safety_policy_v1


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    description: str
    expected_package: Literal["starter", "business", "reference", "advanced"]
    expected_required_capabilities: tuple[str, ...]
    expected_forbidden_capabilities: tuple[str, ...]
    expected_planning_outcome_class: Literal["planner_candidate", "large_plan_manual_review"]
    context: GenerationContextV1


_BUILT_AT = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _context(
    case_id: str, package: str, pages: int, requirements: tuple[str, ...],
    preference: str, direction: str, broad_model: str,
) -> GenerationContextV1:
    roles = [
        {"role_key": f"planning_role_{index + 1}", "purpose": "Provisional planning evidence", "requirements_served": list(requirements) or ["primary_conversion"]}
        for index in range(pages)
    ]
    q1_hash = _hash({"case_id": case_id, "source": "q1_v2"})
    q2_hash = _hash({"case_id": case_id, "source": "q2_v2", "requirements": requirements})
    scope_hash = _hash({"case_id": case_id, "package": package, "pages": pages})
    source_hash = _hash({"case_id": case_id, "source_signals": "synthetic"})
    base = {
        "contract_version": "v1",
        "order": {"order_id": f"eval-{case_id.lower().replace('_', '-')}", "journey_project_id": f"eval-project-{case_id.lower()}", "source_versions": {"q1_schema_version": 2, "q2_schema_version": 2, "scope_rule_version": "ireland_accepted_v1", "design_direction_contract_version": "v1", "interaction_preference_contract_version": "v1"}},
        "business": {
            "q1": {"project_class_intent": "business_site", "existing_site": {"has_existing_website": False, "url": None, "analyzer_hints_are_advisory": True}},
            "identity": {"status": "business_name", "name": f"Synthetic {case_id}"},
            "activity": {"niche": "Synthetic evaluation business", "broad_model": broad_model, "other_clarification": None},
            "operating_model": "evaluation_fixture", "location": {"public_address": None, "service_area": "Ireland", "multiple_locations_summary": None},
            "audience": "representative evaluation audience", "primary_goal": "clear primary conversion", "trust_materials": ["reviews"],
        },
        "scope": {
            "resolved_package": package, "eligibility": "supported", "minimum_package": package,
            "provisional_page_need": pages,
            "provisional_architecture": {"evidence_only_not_final_sitemap": True, "status": "resolved", "minimum_coherent_pages": pages, "proposed_page_roles": roles, "semantic_needs": list(requirements) or ["primary_conversion"], "reasoning_codes": ["synthetic_eval_fixture"], "unresolved_requirements": [], "input_fingerprint": scope_hash},
            "confirmed_structural_addons": [], "qualification_reason_codes": ["synthetic_eval_fixture"],
            "contractual_constraints": {"package_contract_version": "ireland_accepted_v1", "page_capacity_kind": "included_plus_one" if package in {"starter", "business"} else "minimum_unbounded", "included_pages": pages if package in {"starter", "business"} else None, "minimum_pages": pages if package in {"reference", "advanced"} else None, "max_additional_pages": 1 if package in {"starter", "business"} else None, "final_architecture_requires_site_planner": True},
        },
        "functionality": {"confirmed_requirements": list(requirements), "unresolved_requirements": [], "component_constraints": ["confirmed_requirements_only", "no_function_invention"]},
        "source_signals": {"selected_example_id": None, "selected_example_status": "absent", "viewed_example_ids": [], "existing_site_hints_status": "absent", "existing_site_business_hints": [], "existing_site_function_hints": [], "existing_site_complexity_hints": [], "visual_affinities": [], "example_interaction_signals": {"contract_version": None, "signals": []}},
        "visual": {"design_direction": {"contract_version": "v1", "value": direction}},
        "interaction_guidance": {"client_preference": {"contract_version": "v1", "value": preference}, "example_signals": {"contract_version": None, "signals": []}, "safety_contract_version": "v1"},
        "media": {"photos": "client_provides", "client_photos_timing": "later", "video": "none", "logo": {"choice": "existing", "simple_logo_required": False, "scope": {}}, "trust_assets": ["reviews"], "social_channels": [], "social_presentation": "flexible"},
        "delivery": {"hosting_status": "client_owned", "hosting_help_included": True, "installation_selected": False, "domain_and_hosting_client_owned": True},
        "constraints": {"interaction_safety": interaction_safety_policy_v1(), "prohibited_inventions": ["unconfirmed_functionality", "unconfirmed_addons", "factual_claims"]},
        "fingerprints": {"q1_hash": q1_hash, "q2_hash": q2_hash, "scope_hash": scope_hash, "source_signals_hash": source_hash, "context_hash": "0" * 64},
        "built_at": _BUILT_AT,
    }
    context_payload = {key: value for key, value in base.items() if key not in {"fingerprints", "built_at"}}
    base["fingerprints"]["context_hash"] = _hash(context_payload)
    return GenerationContextV1.model_validate(base)


def site_planner_eval_cases_v1() -> tuple[EvalCase, ...]:
    definitions = (
        ("STARTER_LOCAL_SERVICE", "Focused one-page local service", "starter", 1, ("contact_enquiry",), "recommended", "clean-modern", "local_service", "planner_candidate"),
        ("BUSINESS_THREE_PAGE", "Three-page business with broader quote journey", "business", 3, ("contact_enquiry", "advanced_form"), "recommended", "premium-business", "professional_service", "planner_candidate"),
        ("REFERENCE_PORTFOLIO_EXPRESSIVE", "Project-heavy Reference presentation", "reference", 6, ("blog_news", "search_filter_compare"), "more_expressive", "creative-studio", "portfolio", "planner_candidate"),
        ("REFERENCE_BOOKING", "Reference booking without account persistence", "reference", 7, ("booking", "contact_enquiry"), "recommended", "nordic-soft", "booking_service", "planner_candidate"),
        ("REFERENCE_ECOMMERCE", "Reference ecommerce with transactional safety", "reference", 8, ("transactional_ecommerce", "search_filter_compare"), "recommended", "bold-startup", "ecommerce", "planner_candidate"),
        ("ADVANCED_ACCOUNT_PERSISTENT", "Advanced authenticated persistent journey", "advanced", 10, ("customer_account_login", "portal_membership"), "recommended", "tech-minimal", "member_system", "planner_candidate"),
        ("ADVANCED_SUBTLE", "Advanced persistence with restrained interaction", "advanced", 10, ("customer_account_login", "portal_membership"), "subtle", "dark-contrast", "member_system", "planner_candidate"),
        ("LARGE_ADVANCED_BOUNDARY", "Advanced case beyond V1 single-call boundary", "advanced", 13, ("transactional_ecommerce", "booking", "portal_membership", "multilingual"), "recommended", "luxury-elite", "complex_platform", "large_plan_manual_review"),
    )
    cases = []
    for case_id, description, package, pages, required, preference, direction, broad_model, outcome in definitions:
        forbidden = tuple(sorted({"unconfirmed_addons", "unconfirmed_media", "unconfirmed_functionality"} | ({"account", "dashboard", "saved_items"} if "portal_membership" not in required else set())))
        cases.append(EvalCase(case_id=case_id, description=description, expected_package=package, expected_required_capabilities=required, expected_forbidden_capabilities=forbidden, expected_planning_outcome_class=outcome, context=_context(case_id, package, pages, required, preference, direction, broad_model)))
    return tuple(cases)
