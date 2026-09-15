"""C4A Visual Plan evaluation harness with a one-call safety boundary."""
from __future__ import annotations
import asyncio
import json
from collections.abc import Mapping
from time import monotonic
from typing import Any, Callable, Literal
import uuid
from pydantic import BaseModel, ConfigDict, Field
from app.services.generator_v2_contract import build_generator_v2_input_snapshot
from app.services.generator_v2_implementation import build_generator_v2_implementation_spec
from app.services.generator_v2_visual import VisualImplementationInputV1, VisualImplementationPlanV1, build_visual_implementation_input, build_visual_implementation_provider_request
from app.services.generator_v2_visual_provider import OpenAIVisualImplementationProviderV1, build_openai_visual_implementation_provider_v1, load_visual_planner_config_v1, VisualProviderResultV1
from app.services.site_plan_validator import validate_site_plan_v1
from app.services.site_planner_constraint_projection import build_planner_constraint_projection_v1
from evals.site_planner.eval_cases import site_planner_eval_cases_v1
from evals.site_planner.eval_metrics import MODEL_PRICING_PER_MILLION

MAX_REAL_CALLS = 1
MAX_SPEND_USD = 1.0
MAX_WALL_SECONDS = 600
MAX_DIAGNOSTIC_BYTES = 16_384
EvalStatus = Literal["valid", "manual_review", "provider_failure", "budget_blocked"]
EvalReason = Literal["real_switch_required", "configuration_invalid", "call_budget_exhausted", "spend_budget_exhausted", "timeout", "cancelled", "provider_failure", "schema_parse_failed", "validator_invalid", "unknown"]

class VisualEvalConfigV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    allow_real_provider: bool = False
    max_real_calls: Literal[1] = 1
    max_estimated_spend_usd: float = Field(default=MAX_SPEND_USD, gt=0, le=MAX_SPEND_USD)
    max_eval_wall_seconds: int = Field(default=MAX_WALL_SECONDS, ge=1, le=MAX_WALL_SECONDS)

class VisualEvalPreflightV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["READY", "BLOCKED"]
    case_id: Literal["BUSINESS_THREE_PAGE"] = "BUSINESS_THREE_PAGE"
    model: Literal["gpt-5.6-sol"] = "gpt-5.6-sol"
    reasoning_effort: Literal["medium"] = "medium"
    enabled: bool
    provider: Literal["openai"] | None = None
    timeout_seconds: int | None = None
    max_output_tokens: int | None = None
    real_switch: bool
    safe_reason: EvalReason | None = None

class VisualEvalResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    eval_contract_version: Literal["v1"] = "v1"
    eval_run_id: str = Field(min_length=1, max_length=80)
    case_id: Literal["BUSINESS_THREE_PAGE"] = "BUSINESS_THREE_PAGE"
    model: Literal["gpt-5.6-sol"] = "gpt-5.6-sol"
    reasoning_effort: Literal["medium"] = "medium"
    provider_status: EvalStatus
    schema_parse_status: Literal["parsed", "not_parsed"]
    c3_validator_status: Literal["VALID", "INVALID", "NOT_RUN"]
    validation_reason_codes: tuple[str, ...] = ()
    structural_fingerprint_match: bool = False
    architecture_mutation: bool = False
    visual_plan_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    plan: VisualImplementationPlanV1 | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    output_bytes: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    call_count: int = Field(ge=0, le=1)
    operation_id: str = Field(min_length=1, max_length=80)

def build_business_visual_input() -> VisualImplementationInputV1:
    cases = {item.case_id: item for item in site_planner_eval_cases_v1()}
    context = cases["BUSINESS_THREE_PAGE"].context
    from test_generator_v2_contract_v1 import selected
    from test_site_planner_constraint_projection_v1 import compliant_plan
    projection = build_planner_constraint_projection_v1(context)
    validated = validate_site_plan_v1(context, compliant_plan(context))
    if validated.status != "valid" or validated.plan is None: raise ValueError("synthetic fixture is not valid")
    snapshot = build_generator_v2_input_snapshot(context=context, projection=projection, site_plan=validated.plan, planner_operation_key="a" * 64, selected_design=selected(context))
    if snapshot.status != "READY" or snapshot.snapshot is None: raise ValueError("synthetic snapshot is not ready")
    implementation = build_generator_v2_implementation_spec(snapshot.snapshot)
    if implementation.status != "READY_TO_RENDER" or implementation.spec is None: raise ValueError("synthetic implementation is not ready")
    visual_input = build_visual_implementation_input(snapshot.snapshot, implementation.spec)
    if visual_input.status != "READY" or visual_input.input is None: raise ValueError("synthetic visual input is not ready")
    return visual_input.input

def preflight_visual_eval(config: VisualEvalConfigV1, environment: Mapping[str, str] | None = None) -> VisualEvalPreflightV1:
    values = environment or {}
    real_switch = values.get("SITEFORMO_VISUAL_PLANNER_EVAL_ALLOW_REAL", "").strip().lower() == "true"
    try: provider_config = load_visual_planner_config_v1(values)
    except Exception: return VisualEvalPreflightV1(status="BLOCKED", enabled=False, real_switch=real_switch, safe_reason="configuration_invalid")
    ready = config.allow_real_provider and real_switch and provider_config.enabled and provider_config.provider == "openai" and provider_config.model == "gpt-5.6-sol" and provider_config.reasoning_effort == "medium" and provider_config.timeout_seconds == 180 and provider_config.max_output_tokens == 16_384
    return VisualEvalPreflightV1(status="READY" if ready else "BLOCKED", enabled=provider_config.enabled, provider=provider_config.provider, timeout_seconds=provider_config.timeout_seconds, max_output_tokens=provider_config.max_output_tokens, real_switch=real_switch, safe_reason=None if ready else "real_switch_required")

def _cost(result: VisualProviderResultV1) -> float | None:
    if result.usage.input_tokens is None or result.usage.output_tokens is None: return None
    rates = MODEL_PRICING_PER_MILLION["gpt-5.6-sol"]; cached = min(result.usage.cached_input_tokens or 0, result.usage.input_tokens)
    return ((result.usage.input_tokens - cached) * rates["input"] + cached * rates["cached_input"] + result.usage.output_tokens * rates["output"]) / 1_000_000

def _safe_result(run_id: str, visual_input: VisualImplementationInputV1, provider_result: VisualProviderResultV1, latency_ms: int, calls: int) -> VisualEvalResultV1:
    parsed = provider_result.plan is not None; valid = provider_result.status == "valid"
    return VisualEvalResultV1(eval_run_id=run_id, provider_status=provider_result.status, schema_parse_status="parsed" if parsed else "not_parsed", c3_validator_status="VALID" if valid else "INVALID" if provider_result.status == "manual_review" else "NOT_RUN", validation_reason_codes=tuple(provider_result.validation_reason_codes), structural_fingerprint_match=bool(provider_result.plan and provider_result.plan.structural_fingerprint == visual_input.structural_fingerprint), visual_plan_hash=provider_result.plan.visual_plan_hash if valid and provider_result.plan else None, plan=provider_result.plan if valid else None, latency_ms=latency_ms, input_tokens=provider_result.usage.input_tokens, cached_input_tokens=provider_result.usage.cached_input_tokens, output_tokens=provider_result.usage.output_tokens, reasoning_tokens=provider_result.usage.reasoning_tokens, total_tokens=provider_result.usage.total_tokens, output_bytes=provider_result.output_bytes, estimated_cost_usd=_cost(provider_result), call_count=calls, operation_id=provider_result.operation_key)

async def evaluate_business_visual_plan(*, config: VisualEvalConfigV1, allow_real_provider: bool, environment: Mapping[str, str], provider: Any | None = None, run_id: str | None = None, emit: Callable[[str], None] = print) -> VisualEvalResultV1:
    visual_input = build_business_visual_input(); request = build_visual_implementation_provider_request(visual_input)
    preflight = preflight_visual_eval(config.model_copy(update={"allow_real_provider": allow_real_provider}), environment)
    emit("SITEFORMO_VISUAL_EVAL_PREFLIGHT=" + _bounded_json(preflight.model_dump(mode="json")))
    # A call with unavailable usage must reserve the full V1 evaluation cap.
    # Refuse before provider construction/invocation rather than allowing debt.
    if config.max_estimated_spend_usd < 1.0:
        blocked = VisualEvalResultV1(eval_run_id=run_id or uuid.uuid4().hex, provider_status="budget_blocked", schema_parse_status="not_parsed", c3_validator_status="NOT_RUN", validation_reason_codes=("spend_budget_exhausted",), call_count=0, operation_id=uuid.uuid4().hex)
        emit("SITEFORMO_VISUAL_EVAL_RESULT=" + _bounded_json(blocked.model_dump(mode="json")))
        return blocked
    if provider is None:
        if preflight.status != "READY": raise PermissionError("real visual evaluation preflight blocked")
        provider = build_openai_visual_implementation_provider_v1(load_visual_planner_config_v1(environment))
    calls = 1; started = monotonic()
    try: provider_result = await asyncio.wait_for(provider.create_visual_plan(request), timeout=config.max_eval_wall_seconds)
    except asyncio.TimeoutError:
        provider_result = VisualProviderResultV1(status="provider_failure", error_category="timeout", operation_key="0" * 64, structural_fingerprint=visual_input.structural_fingerprint)
    result = _safe_result(run_id or uuid.uuid4().hex, visual_input, provider_result, int((monotonic() - started) * 1000), calls)
    emit("SITEFORMO_VISUAL_EVAL_RESULT=" + _bounded_json(result.model_dump(mode="json", exclude={"plan"})))
    return result

def _bounded_json(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return encoded if len(encoded.encode("utf-8")) <= MAX_DIAGNOSTIC_BYTES else json.dumps({"status": "diagnostic_overflow"}, separators=(",", ":"))
