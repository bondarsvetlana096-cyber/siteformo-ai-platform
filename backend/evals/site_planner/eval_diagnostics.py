from __future__ import annotations

from collections import Counter
import json
from typing import Callable, get_args

from app.schemas.site_planner import ProviderErrorCategory, ValidatorReasonCode
from evals.site_planner.eval_metrics import EvalRunMetrics


MAX_DIAGNOSTIC_BYTES = 16_384
UNKNOWN_REASON_CODE = "unknown_reason_code"

_PLANNER_REASON_CODES = {
    "large_plan_requires_staged_planning", "context_verifier_failed", "stale_context",
    "output_budget_exceeded", "security_shaped_candidate",
}
_SAFE_REASON_CODES = set(get_args(ValidatorReasonCode)) | set(get_args(ProviderErrorCategory)) | _PLANNER_REASON_CODES
_COUNTER_FIELDS = (
    "hallucinated_function_count", "unsupported_component_count",
    "unauthorized_addon_or_media_count", "scope_conflict_count",
    "persistent_system_invention_count", "broken_navigation_count",
    "interaction_safety_violation_count", "mobile_coverage_failure_count",
    "reduced_motion_failure_count",
)


def safe_reason_codes(values: list[str]) -> list[str]:
    return list(dict.fromkeys(
        value if value in _SAFE_REASON_CODES else UNKNOWN_REASON_CODE for value in values
    ))


def run_diagnostic(eval_run_id: str, metric: EvalRunMetrics) -> dict[str, object]:
    return {
        "eval_run_id": eval_run_id,
        "case_id": metric.case_id,
        "model": metric.model,
        "planner_status": metric.planner_status,
        "first_pass_valid": metric.first_pass_valid,
        "final_valid": metric.final_valid,
        "repair_used": metric.repair_used,
        "manual_review": metric.manual_review,
        "semantic_attempt_count": metric.semantic_attempt_count,
        "transport_attempt_count": metric.transport_attempt_count,
        "provider_error_category": metric.provider_error_category,
        "validator_reason_codes": safe_reason_codes(metric.validator_reason_codes),
        "quality_counters": {field: getattr(metric, field) for field in _COUNTER_FIELDS},
        "latency_ms": metric.usage.latency_ms,
        "input_tokens": metric.usage.input_tokens,
        "cached_input_tokens": metric.usage.cached_input_tokens,
        "output_tokens": metric.usage.output_tokens,
        "reasoning_tokens": metric.usage.reasoning_tokens,
        "total_tokens": metric.usage.total_tokens,
        "estimated_cost_usd": metric.cost.estimated_total_cost,
    }


def summary_diagnostic(
    eval_run_id: str, runs: tuple[EvalRunMetrics, ...], planned: int,
    budget_state: dict[str, float | int],
) -> dict[str, object]:
    models: dict[str, object] = {}
    for model in sorted({run.model for run in runs}):
        selected = [run for run in runs if run.model == model]
        count = len(selected)
        reason_frequency = Counter(
            reason for run in selected for reason in safe_reason_codes(run.validator_reason_codes)
        )
        counters = {
            field: sum(getattr(run, field) for run in selected) for field in _COUNTER_FIELDS
        }
        total_latency = sum(run.usage.latency_ms for run in selected)
        first = sum(run.first_pass_valid for run in selected)
        final = sum(run.final_valid for run in selected)
        manual = sum(run.manual_review for run in selected)
        models[model] = {
            "runs": count,
            "first_pass_valid_count": first,
            "first_pass_valid_rate": first / count if count else 0.0,
            "final_valid_count": final,
            "final_valid_rate": final / count if count else 0.0,
            "manual_review_count": manual,
            "manual_review_rate": manual / count if count else 0.0,
            "repair_count": sum(run.repair_used for run in selected),
            "provider_failure_count": sum(
                run.planner_status in {"provider_failure", "temporary_failure"} for run in selected
            ),
            "reason_code_frequency": dict(sorted(reason_frequency.items())),
            "quality_counters": counters,
            "token_totals": {
                "input_tokens": sum(run.usage.input_tokens for run in selected),
                "cached_input_tokens": sum(run.usage.cached_input_tokens for run in selected),
                "output_tokens": sum(run.usage.output_tokens for run in selected),
                "reasoning_tokens": sum(run.usage.reasoning_tokens for run in selected),
                "total_tokens": sum(run.usage.total_tokens for run in selected),
            },
            "latency_total_ms": total_latency,
            "latency_average_ms": total_latency / count if count else 0.0,
            "estimated_cost_total_usd": sum(
                run.cost.estimated_total_cost for run in selected
            ),
        }
    return {
        "eval_run_id": eval_run_id,
        "completed_entries": len(runs),
        "remaining_entries": max(0, planned - len(runs)),
        "calls_used": int(budget_state["calls_used"]),
        "known_spend": float(budget_state["known_estimated_spend"]),
        "unknown_reserve": float(budget_state["unknown_call_reserve"]),
        "committed_spend": float(budget_state["budget_committed_total"]),
        "models": models,
    }


def emit_diagnostic(
    prefix: str, payload: dict[str, object], output: Callable[[str], None],
) -> None:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(serialized.encode("utf-8")) > MAX_DIAGNOSTIC_BYTES:
        serialized = json.dumps(
            {"eval_run_id": payload.get("eval_run_id"), "status": "diagnostic_overflow"},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
    output(prefix + serialized)
