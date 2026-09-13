from __future__ import annotations

from typing import Any

from app.schemas.order import Q2V2Payload
from app.services.q2_architecture_planner import ArchitectureReasoner, plan_architecture


PACKAGE_ORDER = ("starter", "business", "reference", "advanced")
PACKAGE_RULE_VERSION = "ireland_accepted_v1"
PACKAGE_PAGE_RULES = {
    "starter": {"included_pages": 1, "max_additional_pages": 1, "additional_page_eur": 120},
    "business": {"included_pages": 3, "max_additional_pages": 1, "additional_page_eur": 120},
    "reference": {"included_pages_minimum": 5, "max_additional_pages": None, "additional_page_eur": 120},
    "advanced": {"pages": "scope_defined", "max_additional_pages": None, "additional_page_eur": None},
}
UNRESOLVED_FUNCTIONS = {
    "blog_news", "newsletter", "booking", "catalogue_display",
    "transactional_ecommerce", "customer_account_login", "portal_membership",
    "multilingual", "advanced_form", "search_filter_compare",
}
STARTER_FUNCTIONS = {
    "standard_content_contact", "contact_enquiry", "direct_contact_channel",
}


def plan_preliminary_architecture(
    payload: Q2V2Payload,
    reasoner: ArchitectureReasoner | None = None,
) -> dict[str, Any]:
    return plan_architecture(payload, reasoner)


def _commercial_plan(page_need: int, confirmed_paid_pages: int) -> dict[str, Any]:
    proposed: list[dict[str, Any]] = []
    if page_need == 1:
        return {"minimum_package": "starter", "recommended_package": "starter", "paths": [{"package": "starter", "additional_capacity_pages": 0}], "proposed": proposed}
    if page_need == 2:
        paths = [{"package": "starter", "additional_capacity_pages": 1, "additional_capacity_price_eur": 120}, {"package": "business", "additional_capacity_pages": 0}]
        if confirmed_paid_pages >= 1:
            return {"minimum_package": "starter", "recommended_package": "starter", "paths": paths, "proposed": proposed}
        proposed.append({"status": "PROPOSED_PAID_PAGE", "package": "starter", "quantity": 1, "price_eur_each": 120, "requires_client_confirmation": True})
        return {"minimum_package": "starter", "recommended_package": None, "paths": paths, "proposed": proposed}
    if page_need == 3:
        return {"minimum_package": "business", "recommended_package": "business", "paths": [{"package": "business", "additional_capacity_pages": 0}], "proposed": proposed}
    if page_need == 4:
        paths = [{"package": "business", "additional_capacity_pages": 1, "additional_capacity_price_eur": 120}, {"package": "reference", "additional_capacity_pages": 0}]
        if confirmed_paid_pages >= 1:
            return {"minimum_package": "business", "recommended_package": "business", "paths": paths, "proposed": proposed}
        proposed.append({"status": "PROPOSED_PAID_PAGE", "package": "business", "quantity": 1, "price_eur_each": 120, "requires_client_confirmation": True})
        return {"minimum_package": "business", "recommended_package": None, "paths": paths, "proposed": proposed}
    required = max(0, page_need - 5)
    paths = [{"package": "reference", "additional_capacity_pages": required, "additional_capacity_price_eur_each": 120 if required else 0}]
    if required > confirmed_paid_pages:
        proposed.append({"status": "PROPOSED_PAID_PAGE", "package": "reference", "quantity": required - confirmed_paid_pages, "price_eur_each": 120, "requires_client_confirmation": True})
        return {"minimum_package": "reference", "recommended_package": None, "paths": paths, "proposed": proposed}
    return {"minimum_package": "reference", "recommended_package": "reference", "paths": paths, "proposed": proposed}


def _confirmed_options(payload: Q2V2Payload) -> list:
    return [item for item in payload.paid_structural_options if item.explicit_confirmed]


def qualify_scope(
    payload: Q2V2Payload,
    reasoner: ArchitectureReasoner | None = None,
) -> dict[str, Any]:
    """Resolve architecture first; apply package capacity only to validated output."""
    architecture = plan_preliminary_architecture(payload, reasoner)
    starting = payload.package_context.starting_package
    options = _confirmed_options(payload)
    if architecture["status"] == "manual_review":
        return {
            "rule_version": PACKAGE_RULE_VERSION,
            "eligibility": "manual_review",
            "starting_package": starting,
            "minimum_package": None,
            "recommended_package": None,
            "required_floor_reasons": [],
            "optional_recommendation_reasons": [],
            "provisional_architecture": architecture,
            "provisional_page_need": None,
            "commercial_paths": [],
            "proposed_paid_pages": [],
            "confirmed_paid_addons": [item.model_dump(mode="json") for item in options],
            "unresolved_requirements": [
                {
                    "reason_code": "architecture_manual_review",
                    "requirement": reason,
                    "resolution": "manual_review",
                }
                for reason in architecture["unresolved_requirements"]
            ],
            "checkout_ready": False,
        }

    page_need = architecture["minimum_coherent_pages"]
    confirmed_paid_pages = sum(item.quantity for item in options if item.counts_as_page)
    commercial = _commercial_plan(page_need, confirmed_paid_pages)
    minimum_package = commercial["minimum_package"]
    recommended = commercial["recommended_package"]
    unresolved = []
    for item in payload.paid_structural_options:
        if not item.explicit_confirmed:
            unresolved.append({
                "reason_code": "paid_addon_not_confirmed",
                "requirement": item.key,
                "resolution": "client_confirmation",
            })
    for proposed in commercial["proposed"]:
        unresolved.append({
            "reason_code": "proposed_paid_page_requires_confirmation",
            "requirement": proposed["quantity"],
            "resolution": "client_confirmation",
        })

    video_entry = next((item for item in options if item.key == "video_entry_page"), None)
    manual_review = False
    if video_entry and minimum_package not in {"starter", "business"}:
        manual_review = True
        unresolved.append({
            "reason_code": "video_entry_page_package_applicability",
            "requirement": "video_entry_page",
            "resolution": "manual_review",
        })
    generic_extra = sum(item.quantity for item in options if item.key == "additional_page")
    video_pages = 1 if video_entry else 0
    if minimum_package == "starter" and generic_extra + video_pages > 1:
        manual_review = True
        unresolved.append({
            "reason_code": "starter_additional_page_capacity_exceeded",
            "requirement": generic_extra + video_pages,
            "resolution": "raise_scope_or_manual_review",
        })
    if minimum_package == "business" and generic_extra + video_pages > 1:
        manual_review = True
        unresolved.append({
            "reason_code": "business_additional_page_capacity_exceeded",
            "requirement": generic_extra + video_pages,
            "resolution": "raise_scope_or_manual_review",
        })
    eligibility = "manual_review" if manual_review else "supported"
    if manual_review:
        minimum_package = None
        recommended = None
    checkout_ready = bool(
        eligibility == "supported" and not unresolved and recommended
        and payload.legal_gate_confirmed and payload.package_and_addons_confirmed
        and all(item.explicit_confirmed for item in payload.paid_structural_options)
    )

    optional_reasons = []
    if recommended and starting and recommended != starting:
        direction = "downgrade" if PACKAGE_ORDER.index(recommended) < PACKAGE_ORDER.index(starting) else "upgrade"
        optional_reasons.append({
            "reason_code": f"symmetric_{direction}_recommendation",
            "evidence": {"starting_package": starting, "calculated_fit": recommended},
            "package_effect": recommended,
        })
    return {
        "rule_version": PACKAGE_RULE_VERSION,
        "eligibility": eligibility,
        "starting_package": starting,
        "minimum_package": minimum_package,
        "recommended_package": recommended,
        "required_floor_reasons": [{
            "reason_code": "validated_provisional_architecture_capacity",
            "evidence": {
                "minimum_coherent_pages": page_need,
                "reasoning_codes": architecture["reasoning_codes"],
            },
            "package_effect": minimum_package,
        }],
        "optional_recommendation_reasons": optional_reasons,
        "provisional_architecture": architecture,
        "provisional_page_need": page_need,
        "commercial_paths": commercial["paths"],
        "proposed_paid_pages": commercial["proposed"],
        "confirmed_paid_addons": [item.model_dump(mode="json") for item in options],
        "unresolved_requirements": unresolved,
        "checkout_ready": checkout_ready,
    }


def legacy_q2_adapter(payload: Q2V2Payload, qualification: dict[str, Any], q1: dict[str, Any]) -> dict[str, Any]:
    """One derived compatibility view; canonical Q2 remains the only authored truth."""
    page_count = qualification["provisional_page_need"]
    identity = payload.business_identity.name
    functions = [item.key for item in payload.functions if item.confirmed]
    return {
        "compatibility_version": "q2_v2_to_legacy_v1",
        "company_name": identity,
        "business_identity_status": payload.business_identity.status,
        "business_type": payload.business_activity.broad_model,
        "business_niche": payload.business_activity.niche,
        "company_location": payload.location.model_dump(mode="json"),
        "website_goal": payload.primary_goal,
        "site_functions": functions,
        "pages": [
            {"page_number": index + 1, "type": "planner_pending", "source": "pre_stripe_scope_planner"}
            for index in range(page_count)
        ],
        "recommended_tier": qualification["recommended_package"],
        "starting_package": qualification["starting_package"],
        "media": payload.media.model_dump(mode="json"),
        "social_presence": payload.social_presence.model_dump(mode="json"),
        "logo": payload.logo.model_dump(mode="json"),
        "delivery_context": payload.delivery_context.model_dump(mode="json"),
        "examples_context": q1.get("examples_context", {}),
        "package_browsing_context": q1.get("package_browsing_context"),
        "visual_dna": q1.get("examples_context", {}).get("visual_dna"),
    }
