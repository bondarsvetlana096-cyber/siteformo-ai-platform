from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import hashlib
import inspect
import json
import re
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from app.schemas.generation_context import GenerationContextV1
from app.schemas.site_plan import SitePlanV1
from app.schemas.site_planner import (
    CandidateRepairProjection, FinalSitePlannerResult, PlannerAttemptMetadata,
    RepairPageProjection, RepairSectionProjection, SitePlannerProviderRequest,
    SitePlannerProviderResult,
)
from app.services.site_plan_validator import validate_site_plan_v1
from app.services.site_planner_config import SitePlannerConfigV1
from app.services.site_planner_policy import PLANNER_CONTRACT_VERSION, planner_policy_v1
from app.services.site_planner_provider import SitePlannerProvider, SitePlannerProviderException


ContextHashVerifier = Callable[[str], str | Awaitable[str]]
_TRANSPORT_RETRYABLE = {"timeout", "rate_limit", "provider_5xx", "empty_response", "malformed_response"}
_TEMPORARY_FAILURES = {"timeout", "rate_limit", "provider_5xx", "empty_response", "truncated"}
_PROVIDER_ERROR_CATEGORIES = {
    "timeout", "rate_limit", "provider_5xx", "authentication", "configuration",
    "empty_response", "malformed_response", "refusal", "truncated",
    "content_rejection", "unknown",
}
_NON_REPAIRABLE = {
    "generation_context_hash_mismatch", "scope_conflict", "generation_context_unresolved",
    "custom_function_requires_manual_review", "unsupported_functional_component",
    "unsupported_page_role", "unconfirmed_simple_logo", "unconfirmed_video",
    "unconfirmed_client_photo", "unconfirmed_siteformo_image", "unconfirmed_trust_asset",
    "unconfirmed_video_entry", "invented_stateful_journey", "invented_persistent_system",
    "design_direction_trace_mismatch", "interaction_preference_trace_mismatch",
    "raw_code_prompt_or_url_forbidden",
}
_SECURITY_KEYS = {
    "html", "css", "javascript", "script", "prompt", "system_prompt", "chain_of_thought",
    "price", "amount", "currency", "payment_status", "legal_terms", "checkout_url",
    "external_url", "raw_url", "package_override", "interaction_style", "selected_effects",
    "candidates",
}
_SECURITY_TEXT = re.compile(
    r"(?:<\s*/?\s*(?:script|style|html)|javascript\s*:|https?://|www\.|"
    r"\b(?:system prompt|ignore previous instructions|chain[- ]of[- ]thought)\b|"
    r"\b(?:gsap|three(?:\.js|js)|lenis|framer[_ -]?motion)\b)", re.IGNORECASE,
)
_STATEFUL_REQUIREMENTS = {
    "booking", "transactional_ecommerce", "customer_account_login",
    "portal_membership", "advanced_form",
}
_SAFE_REPAIR_ROOT_FIELDS = {
    "contract_version", "generation_context_hash", "plan_status", "pages",
    "cross_page_navigation", "stateful_journeys", "shared_components",
    "content_inventory", "media_inventory", "functional_component_inventory",
    "accessibility_constraints", "generation_constraints", "unresolved_items",
    "planner_reasoning_codes", "validator_version", "site_plan_hash", "created_at",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def planner_operation_key(context: GenerationContextV1, config: SitePlannerConfigV1) -> str:
    material = {
        "generation_context_hash": context.fingerprints.context_hash,
        "planner_contract_version": PLANNER_CONTRACT_VERSION,
        "site_plan_schema_version": "v1",
        "interaction_safety_contract_version": context.constraints.interaction_safety.contract_version,
        "provider_identifier": config.provider_identifier,
        "model_identifier": config.model_identifier,
        "provider_config_version": config.provider_config_version,
        "validator_version": "v1",
        "planning_strategy_version": config.planning_strategy_version,
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _security_shaped(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(_SECURITY_KEYS & set(value)) or any(_security_shaped(item) for item in value.values())
    if isinstance(value, list):
        return any(_security_shaped(item) for item in value)
    return isinstance(value, str) and bool(_SECURITY_TEXT.search(value))


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _repair_projection(candidate: dict[str, Any]) -> CandidateRepairProjection:
    pages: list[RepairPageProjection] = []
    for raw_page in _list(candidate.get("pages"))[:100]:
        if not isinstance(raw_page, dict):
            continue
        sections: list[RepairSectionProjection] = []
        for raw_section in _list(raw_page.get("sections"))[:500]:
            if not isinstance(raw_section, dict):
                continue
            motion = raw_section.get("motion_policy")
            actions = _list(raw_section.get("primary_actions"))
            sections.append(RepairSectionProjection(
                section_key=raw_section.get("section_key") if isinstance(raw_section.get("section_key"), str) else None,
                functional_components=[str(item)[:80] for item in _list(raw_section.get("functional_components"))],
                interaction_families=[str(item)[:80] for item in _list(raw_section.get("interaction_families"))],
                motion_level=motion.get("level") if isinstance(motion, dict) and isinstance(motion.get("level"), str) else None,
                critical_action=raw_section.get("critical_action") if isinstance(raw_section.get("critical_action"), bool) else None,
                target_page_keys=[str(item.get("target_page_key"))[:80] for item in actions if isinstance(item, dict) and item.get("target_page_key")],
            ))
        pages.append(RepairPageProjection(
            page_key=raw_page.get("page_key") if isinstance(raw_page.get("page_key"), str) else None,
            page_role=raw_page.get("page_role") if isinstance(raw_page.get("page_role"), str) else None,
            route_intent=raw_page.get("route_intent") if isinstance(raw_page.get("route_intent"), str) else None,
            sections=sections,
        ))
    navigation = [
        (item.get("from_page_key"), item.get("to_page_key"))
        for item in _list(candidate.get("cross_page_navigation"))[:500] if isinstance(item, dict)
    ]
    return CandidateRepairProjection(
        generation_context_hash=candidate.get("generation_context_hash") if isinstance(candidate.get("generation_context_hash"), str) else None,
        present_root_fields=sorted(str(key) for key in candidate.keys() if key in _SAFE_REPAIR_ROOT_FIELDS),
        pages=pages,
        navigation_page_keys=navigation,
        journey_types=[str(item.get("journey_type"))[:80] for item in _list(candidate.get("stateful_journeys")) if isinstance(item, dict)],
        functional_component_inventory=[str(item)[:80] for item in _list(candidate.get("functional_component_inventory"))],
        unresolved_item_count=len(_list(candidate.get("unresolved_items"))),
    )


def _precheck(context: GenerationContextV1, config: SitePlannerConfigV1) -> list[str]:
    reasons: list[str] = []
    if context.functionality.unresolved_requirements:
        reasons.append("generation_context_unresolved")
    if "custom_other" in context.functionality.confirmed_requirements:
        reasons.append("custom_function_requires_manual_review")
    pages = context.scope.provisional_page_need
    stateful = len(_STATEFUL_REQUIREMENTS & set(context.functionality.confirmed_requirements))
    if (
        pages > config.max_single_call_pages
        or pages * config.projected_sections_per_page > config.max_projected_sections
        or stateful > config.max_stateful_requirements
    ):
        reasons.append("large_plan_requires_staged_planning")
    return reasons


def _request(
    context: GenerationContextV1, attempt_type: str, attempt_number: int,
    projection: CandidateRepairProjection | None = None, reasons: list[str] | None = None,
) -> SitePlannerProviderRequest:
    return SitePlannerProviderRequest(
        planner_contract_version="v1", site_plan_schema_version="v1", validator_version="v1",
        attempt_type=attempt_type, attempt_number=attempt_number,
        generation_context=context.model_copy(deep=True), generation_context_hash=context.fingerprints.context_hash,
        site_plan_json_schema=SitePlanV1.model_json_schema(),
        interaction_safety_contract=context.constraints.interaction_safety,
        planner_policy=planner_policy_v1(), prior_candidate_projection=projection,
        validator_reason_codes=reasons or [],
    )


async def _provider_call(
    provider: SitePlannerProvider, request: SitePlannerProviderRequest, config: SitePlannerConfigV1,
) -> tuple[SitePlannerProviderResult, int, int]:
    started = perf_counter(); calls = 0
    while True:
        calls += 1
        try:
            raw = await provider.create_structured_candidate(request)
            if isinstance(raw, dict) and raw.get("status") == "candidate" and raw.get("candidate") is None:
                result = SitePlannerProviderResult(status="failure", error_category="empty_response")
            else:
                result = raw if isinstance(raw, SitePlannerProviderResult) else SitePlannerProviderResult.model_validate(raw)
        except asyncio.TimeoutError:
            result = SitePlannerProviderResult(status="failure", error_category="timeout")
        except SitePlannerProviderException as exc:
            category = exc.category if exc.category in _PROVIDER_ERROR_CATEGORIES else "unknown"
            result = SitePlannerProviderResult(status="failure", error_category=category)
        except (ValidationError, TypeError, ValueError):
            result = SitePlannerProviderResult(status="failure", error_category="malformed_response")
        except Exception:
            result = SitePlannerProviderResult(status="failure", error_category="unknown")
        if result.status == "candidate" and result.candidate is None:
            result = SitePlannerProviderResult(status="failure", error_category="empty_response")
        category = result.error_category
        if result.status == "candidate" or category not in _TRANSPORT_RETRYABLE or calls > config.max_transport_retries:
            return result, calls, int((perf_counter() - started) * 1000)


def _base_result(context: GenerationContextV1, config: SitePlannerConfigV1, started: datetime) -> dict[str, Any]:
    return {
        "operation_key": planner_operation_key(context, config),
        "generation_context_hash": context.fingerprints.context_hash,
        "planner_contract_version": "v1", "provider_config_version": config.provider_config_version,
        "validator_version": "v1", "provider_identifier": config.provider_identifier,
        "model_identifier": config.model_identifier, "started_at": started,
    }


async def create_final_site_plan_v1(
    context: GenerationContextV1,
    provider: SitePlannerProvider,
    config: SitePlannerConfigV1,
    current_context_hash_verifier: ContextHashVerifier,
) -> FinalSitePlannerResult:
    started = datetime.now(timezone.utc); base = _base_result(context, config, started)
    precheck = _precheck(context, config)
    if precheck:
        return FinalSitePlannerResult(status="manual_review", reason_codes=precheck, attempt_count=0, provider_call_count=0, provider_error_category=None, validated_plan=None, attempts=[], completed_at=datetime.now(timezone.utc), **base)

    attempts: list[PlannerAttemptMetadata] = []; semantic_attempts = 0; provider_calls = 0
    request = _request(context, "initial", 0)
    for semantic_index in range(2):
        semantic_attempts += 1
        result, calls, latency = await _provider_call(provider, request, config); provider_calls += calls
        if result.status == "failure":
            category = result.error_category or "unknown"
            attempts.append(PlannerAttemptMetadata(attempt_type=request.attempt_type, semantic_attempt_number=semantic_index, provider_call_count=calls, latency_ms=latency, output_bytes=0, input_tokens=result.input_tokens, output_tokens=result.output_tokens, classification="provider_failure", reason_codes=[category], provider_error_category=category))
            status = "temporary_failure" if category in _TEMPORARY_FAILURES else "provider_failure"
            return FinalSitePlannerResult(status=status, reason_codes=[category], attempt_count=semantic_attempts, provider_call_count=provider_calls, provider_error_category=category, validated_plan=None, attempts=attempts, completed_at=datetime.now(timezone.utc), **base)

        candidate = result.candidate or {}
        try:
            output_bytes = len(_canonical_json(candidate).encode("utf-8"))
        except (TypeError, ValueError):
            output_bytes = 0
            category = "malformed_response"
            attempts.append(PlannerAttemptMetadata(attempt_type=request.attempt_type, semantic_attempt_number=semantic_index, provider_call_count=calls, latency_ms=latency, output_bytes=0, classification="parse_failure", reason_codes=[category], provider_error_category=category))
            return FinalSitePlannerResult(status="provider_failure", reason_codes=[category], attempt_count=semantic_attempts, provider_call_count=provider_calls, provider_error_category=category, validated_plan=None, attempts=attempts, completed_at=datetime.now(timezone.utc), **base)
        output_bytes = max(output_bytes, result.reported_output_bytes or 0)
        if output_bytes > config.max_output_bytes:
            attempts.append(PlannerAttemptMetadata(attempt_type=request.attempt_type, semantic_attempt_number=semantic_index, provider_call_count=calls, latency_ms=latency, output_bytes=output_bytes, input_tokens=result.input_tokens, output_tokens=result.output_tokens, classification="oversized", reason_codes=["output_budget_exceeded"], provider_error_category="truncated"))
            return FinalSitePlannerResult(status="temporary_failure", reason_codes=["output_budget_exceeded"], attempt_count=semantic_attempts, provider_call_count=provider_calls, provider_error_category="truncated", validated_plan=None, attempts=attempts, completed_at=datetime.now(timezone.utc), **base)
        if _security_shaped(candidate):
            attempts.append(PlannerAttemptMetadata(attempt_type=request.attempt_type, semantic_attempt_number=semantic_index, provider_call_count=calls, latency_ms=latency, output_bytes=output_bytes, input_tokens=result.input_tokens, output_tokens=result.output_tokens, classification="validation_failure", reason_codes=["security_shaped_candidate"], provider_error_category=None))
            return FinalSitePlannerResult(status="manual_review", reason_codes=["security_shaped_candidate"], attempt_count=semantic_attempts, provider_call_count=provider_calls, provider_error_category=None, validated_plan=None, attempts=attempts, completed_at=datetime.now(timezone.utc), **base)

        validation = validate_site_plan_v1(context, candidate)
        classification = "valid" if validation.status == "valid" else "parse_failure" if validation.reason_codes == ["schema_validation_failed"] else "validation_failure"
        attempts.append(PlannerAttemptMetadata(attempt_type=request.attempt_type, semantic_attempt_number=semantic_index, provider_call_count=calls, latency_ms=latency, output_bytes=output_bytes, input_tokens=result.input_tokens, output_tokens=result.output_tokens, classification=classification, reason_codes=validation.reason_codes, provider_error_category=None))
        if validation.status == "valid":
            try:
                current_hash = current_context_hash_verifier(context.fingerprints.context_hash)
                if inspect.isawaitable(current_hash): current_hash = await current_hash
            except Exception:
                return FinalSitePlannerResult(status="temporary_failure", reason_codes=["context_verifier_failed"], attempt_count=semantic_attempts, provider_call_count=provider_calls, provider_error_category="unknown", validated_plan=None, attempts=attempts, completed_at=datetime.now(timezone.utc), **base)
            if current_hash != context.fingerprints.context_hash:
                return FinalSitePlannerResult(status="stale_context", reason_codes=["stale_context"], attempt_count=semantic_attempts, provider_call_count=provider_calls, provider_error_category=None, validated_plan=None, attempts=attempts, completed_at=datetime.now(timezone.utc), **base)
            return FinalSitePlannerResult(status="valid", reason_codes=[], attempt_count=semantic_attempts, provider_call_count=provider_calls, provider_error_category=None, validated_plan=validation.plan, attempts=attempts, completed_at=datetime.now(timezone.utc), **base)

        if semantic_index == 1 or validation.status == "manual_review" or set(validation.reason_codes) & _NON_REPAIRABLE:
            return FinalSitePlannerResult(status="manual_review", reason_codes=validation.reason_codes, attempt_count=semantic_attempts, provider_call_count=provider_calls, provider_error_category=None, validated_plan=None, attempts=attempts, completed_at=datetime.now(timezone.utc), **base)
        request = _request(context, "repair", 1, _repair_projection(candidate), validation.reason_codes)

    raise AssertionError("bounded semantic loop exhausted")
