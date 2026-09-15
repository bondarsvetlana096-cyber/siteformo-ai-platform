from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.site_plan import SitePlanV1
from app.schemas.site_planner import FinalSitePlannerResult, SitePlannerProviderResult


EVAL_PRICING_VERSION = "openai-standard-2026-09-14"
MODEL_PRICING_PER_MILLION = {
    "gpt-5.6-sol": {"input": 4.0, "cached_input": 0.40, "output": 20.0},
    "gpt-6-astra": {"input": 10.0, "cached_input": 1.0, "output": 50.0},
}

_HALLUCINATION = {
    "unsupported_functional_component", "invented_stateful_journey",
    "invented_persistent_system", "unconfirmed_simple_logo", "unconfirmed_video",
    "unconfirmed_client_photo", "unconfirmed_siteformo_image",
    "unconfirmed_trust_asset", "unconfirmed_video_entry", "scope_conflict",
}
_SAFETY = {
    "critical_action_not_marked", "unsafe_critical_action_interaction",
    "reduced_motion_missing",
}
_BROKEN_NAVIGATION = {
    "invalid_page_reference", "unsafe_self_reference", "unreachable_page",
    "conversion_path_unreachable", "primary_conversion_path_missing",
}
_UNAUTHORIZED_ADDON_OR_MEDIA = {
    "unconfirmed_simple_logo", "unconfirmed_video", "unconfirmed_client_photo",
    "unconfirmed_siteformo_image", "unconfirmed_trust_asset", "unconfirmed_video_entry",
}


class EvalUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    output_bytes: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


class EvalCost(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pricing_version: Literal["openai-standard-2026-09-14"]
    estimated_input_cost: float = Field(ge=0)
    estimated_cached_input_cost: float = Field(ge=0)
    estimated_output_cost: float = Field(ge=0)
    estimated_total_cost: float = Field(ge=0)


class EvalRunMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_hash: str
    case_hash: str
    case_id: str
    model: Literal["gpt-5.6-sol", "gpt-6-astra"]
    reasoning_effort: Literal["medium"]
    trial: int
    operation_key: str
    planner_status: str
    semantic_attempt_count: int
    transport_attempt_count: int
    repair_used: bool
    stale_context: bool
    provider_error_category: str | None
    first_pass_valid: bool
    final_valid: bool
    manual_review: bool
    validator_reason_codes: list[str]
    hallucinated_function_count: int
    unsupported_component_count: int
    unauthorized_addon_or_media_count: int = 0
    scope_conflict_count: int
    persistent_system_invention_count: int = 0
    broken_navigation_count: int
    interaction_safety_violation_count: int
    mobile_coverage_failure_count: int
    reduced_motion_failure_count: int
    usage: EvalUsage
    cost: EvalCost
    reproducibility: dict[str, str | int]
    validated_plan: SitePlanV1 | None = None


def estimate_cost(model: str, usage: EvalUsage) -> EvalCost:
    rates = MODEL_PRICING_PER_MILLION[model]
    cached = min(usage.cached_input_tokens, usage.input_tokens)
    uncached = usage.input_tokens - cached
    input_cost = uncached * rates["input"] / 1_000_000
    cached_cost = cached * rates["cached_input"] / 1_000_000
    # OpenAI output_tokens includes reasoning tokens; do not bill reasoning twice.
    output_cost = usage.output_tokens * rates["output"] / 1_000_000
    return EvalCost(
        pricing_version=EVAL_PRICING_VERSION,
        estimated_input_cost=input_cost,
        estimated_cached_input_cost=cached_cost,
        estimated_output_cost=output_cost,
        estimated_total_cost=input_cost + cached_cost + output_cost,
    )


def collect_metrics(
    *, run_hash: str, case_hash: str, case_id: str, model: str, trial: int,
    result: FinalSitePlannerResult, observations: list[tuple[SitePlannerProviderResult, int]],
    reproducibility: dict[str, str | int],
) -> EvalRunMetrics:
    reasons = list(dict.fromkeys(reason for attempt in result.attempts for reason in attempt.reason_codes))
    counts = Counter(reasons)
    usage = EvalUsage(
        input_tokens=sum(item.input_tokens or 0 for item, _ in observations),
        cached_input_tokens=sum(item.cached_input_tokens or 0 for item, _ in observations),
        output_tokens=sum(item.output_tokens or 0 for item, _ in observations),
        reasoning_tokens=sum(item.reasoning_tokens or 0 for item, _ in observations),
        total_tokens=sum(item.total_tokens or 0 for item, _ in observations),
        output_bytes=sum(item.reported_output_bytes or 0 for item, _ in observations),
        latency_ms=sum(latency for _, latency in observations),
    )
    return EvalRunMetrics(
        run_hash=run_hash, case_hash=case_hash, case_id=case_id, model=model,
        reasoning_effort="medium", trial=trial, operation_key=result.operation_key,
        planner_status=result.status, semantic_attempt_count=result.attempt_count,
        transport_attempt_count=result.provider_call_count, repair_used=result.attempt_count > 1,
        stale_context=result.status == "stale_context",
        provider_error_category=result.provider_error_category,
        first_pass_valid=bool(result.attempts and result.attempts[0].classification == "valid"),
        final_valid=result.status == "valid", manual_review=result.status == "manual_review",
        validator_reason_codes=reasons,
        hallucinated_function_count=sum(counts[reason] for reason in _HALLUCINATION),
        unsupported_component_count=counts["unsupported_functional_component"],
        unauthorized_addon_or_media_count=sum(
            counts[reason] for reason in _UNAUTHORIZED_ADDON_OR_MEDIA
        ),
        scope_conflict_count=counts["scope_conflict"],
        persistent_system_invention_count=counts["invented_persistent_system"],
        broken_navigation_count=sum(counts[reason] for reason in _BROKEN_NAVIGATION),
        interaction_safety_violation_count=sum(counts[reason] for reason in _SAFETY),
        mobile_coverage_failure_count=counts["schema_validation_failed"] if "mobile" in " ".join(reasons) else 0,
        reduced_motion_failure_count=counts["reduced_motion_missing"], usage=usage,
        cost=estimate_cost(model, usage), reproducibility=reproducibility,
        validated_plan=result.validated_plan,
    )
