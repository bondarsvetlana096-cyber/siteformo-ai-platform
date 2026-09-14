from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.site_planner import SitePlannerProviderResult
from evals.site_planner.eval_cases import site_planner_eval_cases_v1
from evals.site_planner.eval_harness import (
    EvaluationBudget, EvaluationBudgetExceeded, SitePlannerEvalConfigV1,
    case_hash, matrix, real_provider_allowed, run_evaluation,
)
from evals.site_planner.eval_metrics import EvalCost, EvalRunMetrics, EvalUsage, estimate_cost
from evals.site_planner.eval_report import model_summary, write_artifacts
from tests.test_site_plan_v1 import candidate, page, reduced, section


class ScriptedProvider:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def create_structured_candidate(self, request):
        self.calls += 1
        value = self.outcomes.pop(0)
        if callable(value):
            value = value(request)
        return SitePlannerProviderResult(
            status="candidate", candidate=value, input_tokens=1000,
            cached_input_tokens=200, output_tokens=500, reasoning_tokens=100,
            total_tokens=1500, reported_output_bytes=2000,
        )


def one_model_config(**overrides):
    return SitePlannerEvalConfigV1(models=("gpt-5.6-sol",), max_real_calls=10, **overrides)


def test_exact_fixed_cases_are_typed_valid_and_non_identifying():
    cases = site_planner_eval_cases_v1()
    assert [case.case_id for case in cases] == [
        "STARTER_LOCAL_SERVICE", "BUSINESS_THREE_PAGE",
        "REFERENCE_PORTFOLIO_EXPRESSIVE", "REFERENCE_BOOKING",
        "REFERENCE_ECOMMERCE", "ADVANCED_ACCOUNT_PERSISTENT",
        "ADVANCED_SUBTLE", "LARGE_ADVANCED_BOUNDARY",
    ]
    assert len({case.context.fingerprints.context_hash for case in cases}) == 8
    assert cases[-1].expected_planning_outcome_class == "large_plan_manual_review"
    serialized = json.dumps([case.model_dump(mode="json") for case in cases]).lower()
    assert "@" not in serialized and "journey credential" not in serialized


def test_matrix_is_16_runs_and_hashes_are_stable():
    config = SitePlannerEvalConfigV1()
    first, second = matrix(config), matrix(config)
    assert len(first) == 16
    assert [run.run_hash for run in first] == [run.run_hash for run in second]
    assert case_hash(first[0].case) == first[0].case_hash


def test_only_approved_models_and_medium_reasoning_are_accepted():
    with pytest.raises(ValidationError):
        SitePlannerEvalConfigV1(models=("gpt-5.6-terra",))
    with pytest.raises(ValidationError):
        SitePlannerEvalConfigV1(reasoning_effort="high")


def test_valid_fake_run_collects_usage_cost_and_plan_without_network():
    case = site_planner_eval_cases_v1()[0]
    provider = ScriptedProvider([candidate(case.context)])
    runs = asyncio.run(run_evaluation(one_model_config(), lambda _: provider, "a" * 40, cases=(case,)))
    run = runs[0]
    assert run.first_pass_valid and run.final_valid and not run.repair_used
    assert run.semantic_attempt_count == 1 and run.transport_attempt_count == 1
    assert run.validated_plan is not None and provider.calls == 1
    assert run.usage.reasoning_tokens == 100 and run.cost.estimated_total_cost > 0


def test_one_repair_is_counted_and_no_third_call_occurs():
    case = site_planner_eval_cases_v1()[0]
    bad = candidate(case.context)
    bad["pages"][0].pop("reduced_motion_behavior")
    provider = ScriptedProvider([bad, candidate(case.context)])
    run = asyncio.run(run_evaluation(one_model_config(), lambda _: provider, "b" * 40, cases=(case,)))[0]
    assert run.final_valid and run.repair_used
    assert run.semantic_attempt_count == 2 and run.transport_attempt_count == 2
    assert provider.calls == 2


def test_manual_provider_failure_stale_and_large_boundary_scenarios():
    cases = site_planner_eval_cases_v1()
    starter, large = cases[0], cases[-1]
    failure = type("Failure", (), {"create_structured_candidate": lambda self, request: None})

    class FailedProvider:
        async def create_structured_candidate(self, request):
            return SitePlannerProviderResult(status="failure", error_category="authentication")

    failed = asyncio.run(run_evaluation(one_model_config(), lambda _: FailedProvider(), "c" * 40, cases=(starter,)))[0]
    assert failed.planner_status == "provider_failure" and failed.provider_error_category == "authentication"
    large_run = asyncio.run(run_evaluation(one_model_config(), lambda _: FailedProvider(), "c" * 40, cases=(large,)))[0]
    assert large_run.manual_review and large_run.semantic_attempt_count == 0

    stale_provider = ScriptedProvider([candidate(starter.context)])
    stale = asyncio.run(run_evaluation(
        one_model_config(), lambda _: stale_provider, "c" * 40, cases=(starter,),
        context_verifier_factory=lambda _: (lambda value: "f" * 64),
    ))[0]
    assert stale.stale_context and stale.planner_status == "stale_context"
    assert stale.validated_plan is None


def test_call_and_spend_budgets_fail_before_exceeding_limit():
    budget = EvaluationBudget(1, 1.0)
    budget.authorize_call(0.5); budget.record_actual(0.4)
    with pytest.raises(EvaluationBudgetExceeded):
        budget.authorize_call(0.1)
    spend = EvaluationBudget(5, 0.25)
    with pytest.raises(EvaluationBudgetExceeded):
        spend.authorize_call(0.26)


def test_dual_real_provider_switch_defaults_closed():
    config = SitePlannerEvalConfigV1()
    assert not real_provider_allowed(config, {"SITEFORMO_SITE_PLANNER_EVAL_ALLOW_REAL": "true"})
    enabled = config.model_copy(update={"allow_real_provider": True})
    assert not real_provider_allowed(enabled, {})
    assert real_provider_allowed(enabled, {"SITEFORMO_SITE_PLANNER_EVAL_ALLOW_REAL": "true"})


def _metric(model, *, first, final, repair, manual, hallucinations=0, safety=0):
    usage = EvalUsage(input_tokens=100, cached_input_tokens=0, output_tokens=50, reasoning_tokens=10, total_tokens=150, output_bytes=500, latency_ms=20)
    return EvalRunMetrics(
        run_hash="a" * 64, case_hash="b" * 64, case_id="CASE", model=model,
        reasoning_effort="medium", trial=1, operation_key="c" * 64,
        planner_status="valid" if final else "manual_review", semantic_attempt_count=2 if repair else 1,
        transport_attempt_count=2 if repair else 1, repair_used=repair, stale_context=False,
        provider_error_category=None, first_pass_valid=first, final_valid=final,
        manual_review=manual, validator_reason_codes=[], hallucinated_function_count=hallucinations,
        unsupported_component_count=hallucinations, scope_conflict_count=0, broken_navigation_count=0,
        interaction_safety_violation_count=safety, mobile_coverage_failure_count=0,
        reduced_motion_failure_count=0, usage=usage, cost=estimate_cost(model, usage),
        reproducibility={"repository_commit_sha": "d" * 40}, validated_plan=None,
    )


def test_aggregate_metrics_are_transparent_and_correct():
    runs = (_metric("gpt-5.6-sol", first=True, final=True, repair=False, manual=False),
            _metric("gpt-5.6-sol", first=False, final=True, repair=True, manual=False, hallucinations=2, safety=1))
    summary = model_summary(runs)[0]
    assert summary["first_pass_valid_percent"] == 50.0
    assert summary["final_valid_percent"] == 100.0
    assert summary["repair_percent"] == 50.0
    assert summary["hallucination_events"] == 2 and summary["safety_violations"] == 1


def test_artifacts_retain_only_validated_plan_and_safe_metrics(tmp_path):
    case = site_planner_eval_cases_v1()[0]
    valid = asyncio.run(run_evaluation(one_model_config(), lambda _: ScriptedProvider([candidate(case.context)]), "e" * 40, cases=(case,)))[0]
    invalid_plan = candidate(case.context); invalid_plan["html"] = "<script>secret raw candidate</script>"
    invalid = asyncio.run(run_evaluation(one_model_config(), lambda _: ScriptedProvider([invalid_plan]), "e" * 40, cases=(case,)))[0]
    target = write_artifacts(tmp_path, ".stage-artifacts/site-planner-eval", (valid, invalid))
    stored = "".join(path.read_text(encoding="utf-8") for path in target.rglob("*.json*"))
    assert "secret raw candidate" not in stored
    plans = list((target / "validated-plans").glob("*.json"))
    assert len(plans) == 1
    assert '"validated_plan"' not in (target / "runs.jsonl").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        write_artifacts(tmp_path, "outside", (valid,))


def test_eval_modules_have_no_openai_or_runtime_imports():
    root = Path(__file__).parents[1] / "evals" / "site_planner"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py")).lower()
    assert "asyncopenai" not in text and "app.main" not in text
    assert "generation_service" not in text and "worker" not in text
