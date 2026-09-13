from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from pydantic import ValidationError

from app.schemas.order import (
    ArchitecturePlannerInputQ2V2,
    ProvisionalArchitectureQ2V2,
    Q2V2Payload,
)


UNRESOLVED_FUNCTIONS = {
    "blog_news", "newsletter", "booking", "catalogue_display",
    "transactional_ecommerce", "customer_account_login", "portal_membership",
    "multilingual", "advanced_form", "search_filter_compare", "custom_other",
}
STARTER_FUNCTIONS = {
    "standard_content_contact", "contact_enquiry", "direct_contact_channel",
}
MAX_MODEL_PROVISIONAL_PAGES = 25


class ArchitectureReasoner(Protocol):
    """Optional constrained reasoner. Package and price are absent from its input."""

    def propose(self, confirmed_input: ArchitecturePlannerInputQ2V2) -> dict[str, Any]: ...


def architecture_input(payload: Q2V2Payload) -> ArchitecturePlannerInputQ2V2:
    return ArchitecturePlannerInputQ2V2.model_validate({
        "business_activity": payload.business_activity.model_dump(mode="json"),
        "operating_model": payload.operating_model,
        "location": payload.location.model_dump(mode="json"),
        "audience": payload.audience,
        "primary_goal": payload.primary_goal,
        "functions": [
            item.model_dump(mode="json") for item in payload.functions if item.confirmed
        ],
        "trust_materials": payload.trust_materials,
        "media": payload.media.model_dump(mode="json"),
        "social_presence": payload.social_presence.model_dump(mode="json"),
        "paid_structural_options": [
            item.model_dump(mode="json")
            for item in payload.paid_structural_options if item.explicit_confirmed
        ],
    })


def architecture_input_fingerprint(payload: Q2V2Payload) -> str:
    canonical = json.dumps(
        architecture_input(payload).model_dump(mode="json"),
        sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _semantic_needs(payload: Q2V2Payload) -> list[str]:
    functions = {item.key for item in payload.functions if item.confirmed}
    needs = ["primary_conversion", "services_or_offer"]
    if payload.primary_goal in {"enquiries", "calls", "quote_requests"} or functions & {
        "contact_enquiry", "direct_contact_channel"
    }:
        needs.append("contact_or_enquiry")
    if [item for item in payload.trust_materials if item != "none_yet"]:
        needs.append("business_trust")
    if payload.operating_model in {"customer_location", "travel_to_customers", "both"}:
        needs.append("location")
    if payload.operating_model == "multiple_locations":
        needs.append("multiple_location_context")
    if payload.audience in {"both", "mixed_unsure"}:
        needs.append("mixed_audience_context")
    if "project_photos" in payload.trust_materials:
        needs.append("projects_or_work")
    if payload.media.photos != "none" or payload.media.video != "none":
        needs.append("media_slots")
    if payload.social_presence.expected_channels != ["none"]:
        needs.append("social_presence")
    mapping = {
        "booking": "booking", "catalogue_display": "catalogue",
        "transactional_ecommerce": "commerce", "blog_news": "content_hub",
        "customer_account_login": "customer_area",
        "portal_membership": "customer_area", "multilingual": "multilingual",
        "advanced_form": "conditional_form_flow",
        "search_filter_compare": "search_filter_compare", "newsletter": "newsletter",
        "custom_other": "custom_functionality",
    }
    needs.extend(value for key, value in mapping.items() if key in functions)
    if any(
        item.key == "video_entry_page" and item.explicit_confirmed
        for item in payload.paid_structural_options
    ):
        needs.append("video_entry")
    return list(dict.fromkeys(needs))


def _manual(payload: Q2V2Payload, codes: list[str], needs: list[str]) -> dict[str, Any]:
    return ProvisionalArchitectureQ2V2(
        status="manual_review",
        minimum_coherent_pages=None,
        proposed_page_roles=[],
        semantic_needs=needs,
        reasoning_codes=codes,
        unresolved_requirements=codes,
        confidence="low",
        input_fingerprint=architecture_input_fingerprint(payload),
    ).model_dump(mode="json")


def _role(role_key: str, purpose: str, requirements: list[str]) -> dict[str, Any]:
    return {"role_key": role_key, "purpose": purpose, "requirements_served": requirements}


def _focused(payload: Q2V2Payload, needs: list[str]) -> dict[str, Any]:
    roles = [_role(
        "primary_conversion",
        "Compose offer, proof, relevant location and primary action into one destination.",
        [need for need in needs if need != "video_entry"],
    )]
    if "video_entry" in needs:
        roles.append(_role(
            "video_entry",
            "Provide the explicitly confirmed video-led entry experience.",
            ["video_entry"],
        ))
    return ProvisionalArchitectureQ2V2(
        status="resolved",
        minimum_coherent_pages=len(roles),
        proposed_page_roles=roles,
        semantic_needs=needs,
        reasoning_codes=["focused_requirements_composable", "assets_are_section_content"],
        unresolved_requirements=[],
        confidence="high",
        input_fingerprint=architecture_input_fingerprint(payload),
    ).model_dump(mode="json")


def _compact_quote(payload: Q2V2Payload, needs: list[str]) -> dict[str, Any]:
    roles = [
        _role(
            "primary_conversion",
            "Introduce the business and guide visitors to the principal action.",
            [need for need in needs if need in {
                "primary_conversion", "business_trust", "location", "media_slots",
                "social_presence", "mixed_audience_context",
            }],
        ),
        _role(
            "services_or_offer",
            "Explain the confirmed offer independently from the quote action.",
            ["services_or_offer"],
        ),
        _role(
            "contact_or_enquiry",
            "Give the confirmed quote and direct-contact journey a dedicated destination.",
            ["contact_or_enquiry"],
        ),
    ]
    if "video_entry" in needs:
        roles.append(_role(
            "video_entry",
            "Provide the explicitly confirmed video-led entry experience.",
            ["video_entry"],
        ))
    return ProvisionalArchitectureQ2V2(
        status="resolved",
        minimum_coherent_pages=len(roles),
        proposed_page_roles=roles,
        semantic_needs=needs,
        reasoning_codes=[
            "confirmed_quote_journey_requires_offer_and_contact_separation",
            "assets_are_section_content",
        ],
        unresolved_requirements=[],
        confidence="medium",
        input_fingerprint=architecture_input_fingerprint(payload),
    ).model_dump(mode="json")


def _validated_reasoner_result(
    payload: Q2V2Payload, candidate: dict[str, Any], needs: list[str]
) -> dict[str, Any]:
    supplied = dict(candidate)
    supplied["input_fingerprint"] = architecture_input_fingerprint(payload)
    result = ProvisionalArchitectureQ2V2.model_validate(supplied)
    if result.status != "resolved":
        raise ValueError("Reasoner may only return a resolved candidate")
    if result.minimum_coherent_pages is None or result.minimum_coherent_pages > MAX_MODEL_PROVISIONAL_PAGES:
        raise ValueError("Reasoner page count is outside the safe planning range")
    if len(result.proposed_page_roles) != result.minimum_coherent_pages:
        raise ValueError("Each provisional page must have exactly one semantic role")
    if set(result.semantic_needs) != set(needs):
        raise ValueError("Reasoner changed authoritative semantic needs")
    for role in result.proposed_page_roles:
        if not set(role.requirements_served).issubset(set(needs)):
            raise ValueError("Reasoner role contains an unknown requirement")
    return result.model_dump(mode="json")


def plan_architecture(
    payload: Q2V2Payload,
    reasoner: ArchitectureReasoner | None = None,
) -> dict[str, Any]:
    needs = _semantic_needs(payload)
    functions = {item.key for item in payload.functions if item.confirmed}
    unresolved = sorted(functions & UNRESOLVED_FUNCTIONS)
    unknown = sorted(functions - STARTER_FUNCTIONS - UNRESOLVED_FUNCTIONS)
    if unresolved or unknown:
        return _manual(
            payload,
            [f"function_contract_unresolved:{key}" for key in unresolved + unknown],
            needs,
        )

    required_function = {
        "bookings": "booking", "online_sales": "transactional_ecommerce"
    }.get(payload.primary_goal)
    if required_function and required_function not in functions:
        return _manual(payload, [f"goal_function_unconfirmed:{required_function}"], needs)

    broader_quote = (
        payload.primary_goal == "quote_requests"
        and {"standard_content_contact", "contact_enquiry", "direct_contact_channel"}
        .issubset(functions)
    )
    if broader_quote:
        return _compact_quote(payload, needs)

    if (
        payload.operating_model != "multiple_locations"
        and payload.business_activity.broad_model != "other"
        and payload.primary_goal in {
            "enquiries", "calls", "quote_requests", "physical_visits",
            "present_and_build_trust",
        }
    ):
        return _focused(payload, needs)

    if reasoner is None:
        return _manual(payload, ["architecture_reasoner_required"], needs)
    try:
        return _validated_reasoner_result(
            payload, reasoner.propose(architecture_input(payload)), needs
        )
    except (ValidationError, ValueError, TypeError, RuntimeError):
        return _manual(payload, ["architecture_reasoner_failed_validation"], needs)
