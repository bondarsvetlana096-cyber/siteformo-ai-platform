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
from evals.site_planner.eval_checkpoint import (
    CallEventV1, CheckpointCompatibilityError, DurableEvaluationBudgetExceeded, EvalCheckpointStore,
    EvalManifestV1, InterruptedRunRequiresExplicitResume, read_jsonl, utc_now,
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


def checkpoint_config(**overrides):
    values = {"models": ("gpt-5.6-sol",), "max_real_calls": 10,
              "max_estimated_spend_usd": 10.0}
    values.update(overrides)
    return SitePlannerEvalConfigV1(**values)


def run_checkpointed(tmp_path, cases, factory, *, run_id="checkpoint-test", resume=False,
                     resume_interrupted_runs=False, config=None, sha="f" * 40,
                     progress=lambda value: None):
    return asyncio.run(run_evaluation(
        config or checkpoint_config(), factory, sha, cases=cases,
        repository_root=tmp_path, eval_run_id=run_id, resume=resume,
        resume_interrupted_runs=resume_interrupted_runs,
        branch="site-planner-eval", progress=progress,
    ))


def test_incremental_runs_survive_interruption_and_resume_skips_completed(tmp_path):
    cases = site_planner_eval_cases_v1()[:4]
    providers = {}

    def first_factory(spec):
        if spec.case.case_id == cases[3].case_id:
            raise KeyboardInterrupt()
        provider = ScriptedProvider([candidate(spec.case.context)])
        providers[spec.run_hash] = provider
        return provider

    with pytest.raises(KeyboardInterrupt):
        run_checkpointed(tmp_path, cases, first_factory)
    root = tmp_path / ".stage-artifacts" / "site-planner-eval" / "checkpoint-test"
    assert len(read_jsonl(root / "runs.jsonl")) == 3
    assert json.loads((root / "run_manifest.json").read_text())["status"] == "interrupted"

    resumed_calls = []
    def resumed_factory(spec):
        resumed_calls.append(spec.case.case_id)
        return ScriptedProvider([candidate(spec.case.context)])
    results = run_checkpointed(tmp_path, cases, resumed_factory, resume=True)
    assert len(results) == 4
    assert resumed_calls == [cases[3].case_id]
    assert len({row["run_id"] for row in read_jsonl(root / "runs.jsonl")}) == 4


def test_write_ahead_started_call_recovers_unknown_and_consumes_budget(tmp_path):
    case = site_planner_eval_cases_v1()[0]
    config = checkpoint_config(max_real_calls=2)
    specs = matrix(config, (case,))
    now = utc_now()
    manifest = EvalManifestV1(
        eval_run_id="lost-call", repository_sha="a" * 40, branch="site-planner-eval",
        pricing_version="openai-standard-2026-09-14", models=list(config.models),
        reasoning_effort="medium", trial_count=1,
        configured_cases=[{"case_id": case.case_id, "case_hash": specs[0].case_hash}],
        planner_contract_version="v1", generation_context_version="v1",
        site_plan_schema_version="v1", validator_version="v1",
        provider_config_version=config.planner_config_version,
        max_real_calls=2, max_estimated_spend_usd=10,
        max_eval_wall_seconds=None, started_at=now, updated_at=now,
        status="running", next_matrix_position=0, total_matrix_entries=1,
        completed_run_hashes=[],
    )
    store = EvalCheckpointStore(tmp_path, config.artifact_directory, "lost-call")
    store.create_or_resume(manifest, resume=False)
    store.begin_call(CallEventV1(
        call_id="call-1", eval_run_id="lost-call", run_hash=specs[0].run_hash,
        case_id=case.case_id, model=specs[0].model, semantic_attempt=0,
        transport_attempt=1, call_started_at=now, status="started",
        reserved_unknown_spend=1.25,
    ), 2, 10)
    store.create_or_resume(manifest, resume=True)
    state = store.budget_state()
    assert state == {"calls_used": 1, "known_estimated_spend": 0, "unknown_call_reserve": 1.25, "budget_committed_total": 1.25}
    assert next(iter(store.latest_calls().values())).status == "unknown_completion"
    with pytest.raises(InterruptedRunRequiresExplicitResume):
        run_checkpointed(tmp_path, (case,), lambda _: ScriptedProvider([candidate(case.context)]),
                         run_id="lost-call", resume=True, config=config, sha="a" * 40)


def test_usage_unknown_reserves_spend_without_fabricating_tokens(tmp_path):
    case = site_planner_eval_cases_v1()[0]
    class NoUsageProvider:
        async def create_structured_candidate(self, request):
            return SitePlannerProviderResult(status="failure", error_category="timeout")
    result = run_checkpointed(tmp_path, (case,), lambda _: NoUsageProvider())[0]
    root = tmp_path / ".stage-artifacts" / "site-planner-eval" / "checkpoint-test"
    latest = EvalCheckpointStore(tmp_path, checkpoint_config().artifact_directory, "checkpoint-test").latest_calls()
    assert len(latest) == 2  # producer's one bounded transport retry
    assert all(call.estimated_actual_cost is None for call in latest.values())
    assert all(call.reserved_unknown_spend > 0 for call in latest.values())
    assert result.usage.total_tokens == 0


def test_call_and_spend_budgets_are_durable_across_store_restart(tmp_path):
    store = EvalCheckpointStore(tmp_path, ".stage-artifacts/site-planner-eval", "budget-run")
    # The ledger itself is the authority; reconstruction uses no in-memory counter.
    now = utc_now()
    event = CallEventV1(call_id="one", eval_run_id="budget-run", run_hash="r",
        case_id="CASE", model="gpt-5.6-sol", semantic_attempt=0,
        transport_attempt=1, call_started_at=now, status="started",
        reserved_unknown_spend=0.6)
    store.begin_call(event, 1, 1.0)
    restarted = EvalCheckpointStore(tmp_path, ".stage-artifacts/site-planner-eval", "budget-run")
    with pytest.raises(DurableEvaluationBudgetExceeded, match="call budget"):
        restarted.begin_call(event.model_copy(update={"call_id": "two"}), 1, 1.0)
    with pytest.raises(DurableEvaluationBudgetExceeded, match="spend budget"):
        restarted.begin_call(event.model_copy(update={"call_id": "three", "reserved_unknown_spend": 0.5}), 3, 1.0)


def test_repair_and_transport_calls_each_have_write_ahead_records(tmp_path):
    case = site_planner_eval_cases_v1()[0]
    bad = candidate(case.context); bad["pages"][0].pop("reduced_motion_behavior")
    run = run_checkpointed(tmp_path, (case,), lambda _: ScriptedProvider([bad, candidate(case.context)]))[0]
    calls = EvalCheckpointStore(tmp_path, checkpoint_config().artifact_directory, "checkpoint-test").latest_calls()
    assert run.repair_used and len(calls) == 2
    assert sorted(call.semantic_attempt for call in calls.values()) == [0, 1]


def test_validated_plan_only_atomic_retention_and_partial_then_final_summary(tmp_path):
    cases = site_planner_eval_cases_v1()[:2]
    bad = candidate(cases[1].context); bad["html"] = "<script>rejected raw payload</script>"
    scripts = {cases[0].case_id: [candidate(cases[0].context)], cases[1].case_id: [bad]}
    progress = []
    run_checkpointed(tmp_path, cases, lambda spec: ScriptedProvider(scripts[spec.case.case_id]), progress=progress.append)
    root = tmp_path / ".stage-artifacts" / "site-planner-eval" / "checkpoint-test"
    plans = list((root / "validated_plans").glob("*.json"))
    assert len(plans) == 1
    stored = "".join(path.read_text(encoding="utf-8") for path in root.rglob("*.json*"))
    assert "rejected raw payload" not in stored
    assert json.loads((root / "summary.partial.json").read_text())["partial"] is True
    assert json.loads((root / "summary.final.json").read_text())["partial"] is False
    assert any("status=starting" in line for line in progress)
    assert any("calls_used=" in line and "budget_committed=" in line for line in progress)
    assert not list(root.rglob("*.tmp"))


def test_half_written_jsonl_tail_is_ignored(tmp_path):
    path = tmp_path / "runs.jsonl"
    path.write_text('{"valid":1}\n{"cut":', encoding="utf-8")
    assert read_jsonl(path) == [{"valid": 1}]


def test_resume_rejects_reproducibility_or_budget_changes(tmp_path):
    case = site_planner_eval_cases_v1()[0]
    run_checkpointed(tmp_path, (case,), lambda _: ScriptedProvider([candidate(case.context)]))
    with pytest.raises(CheckpointCompatibilityError, match="repository_sha"):
        run_checkpointed(tmp_path, (case,), lambda _: ScriptedProvider([candidate(case.context)]),
                         resume=True, sha="0" * 40)
    with pytest.raises(CheckpointCompatibilityError, match="max_real_calls"):
        run_checkpointed(tmp_path, (case,), lambda _: ScriptedProvider([candidate(case.context)]),
                         resume=True, config=checkpoint_config(max_real_calls=9))


@pytest.mark.parametrize("field,replacement", [
    ("pricing_version", "changed-pricing"),
    ("models", ["gpt-6-astra"]),
    ("reasoning_effort", "changed-effort"),
    ("planner_contract_version", "changed-planner"),
    ("generation_context_version", "changed-context"),
    ("site_plan_schema_version", "changed-schema"),
    ("validator_version", "changed-validator"),
    ("provider_config_version", "changed-provider-config"),
])
def test_resume_rejects_each_critical_version_dimension(tmp_path, field, replacement):
    case = site_planner_eval_cases_v1()[0]
    run_id = f"compat-{field}"
    run_checkpointed(tmp_path, (case,), lambda _: ScriptedProvider([candidate(case.context)]), run_id=run_id)
    manifest_path = tmp_path / ".stage-artifacts" / "site-planner-eval" / run_id / "run_manifest.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed = dict(original); changed[field] = replacement
    manifest_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(CheckpointCompatibilityError, match=field):
        run_checkpointed(tmp_path, (case,), lambda _: ScriptedProvider([candidate(case.context)]),
                         run_id=run_id, resume=True)


def test_resume_rejects_changed_fixture_hash(tmp_path):
    case = site_planner_eval_cases_v1()[0]
    run_checkpointed(tmp_path, (case,), lambda _: ScriptedProvider([candidate(case.context)]))
    manifest_path = tmp_path / ".stage-artifacts" / "site-planner-eval" / "checkpoint-test" / "run_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["configured_cases"][0]["case_hash"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CheckpointCompatibilityError, match="configured_cases"):
        run_checkpointed(tmp_path, (case,), lambda _: ScriptedProvider([candidate(case.context)]), resume=True)


def test_global_wall_budget_stops_before_next_call_and_keeps_partial(tmp_path):
    cases = site_planner_eval_cases_v1()[:2]
    config = checkpoint_config(max_eval_wall_seconds=1)
    class SlowProvider(ScriptedProvider):
        async def create_structured_candidate(self, request):
            await asyncio.sleep(1.05)
            return await super().create_structured_candidate(request)
    results = run_checkpointed(tmp_path, cases, lambda spec: SlowProvider([candidate(spec.case.context)]), config=config)
    assert len(results) == 1
    root = tmp_path / ".stage-artifacts" / "site-planner-eval" / "checkpoint-test"
    assert json.loads((root / "run_manifest.json").read_text())["status"] == "interrupted_time_budget"
    assert not (root / "summary.final.json").exists()


def test_checkpoint_artifacts_exclude_context_secrets_and_client_text(tmp_path):
    case = site_planner_eval_cases_v1()[0]
    run_checkpointed(tmp_path, (case,), lambda _: ScriptedProvider([candidate(case.context)]))
    root = tmp_path / ".stage-artifacts" / "site-planner-eval" / "checkpoint-test"
    stored = "".join(path.read_text(encoding="utf-8") for path in root.rglob("*.*") if path.is_file())
    assert '"generation_context":' not in stored.lower()
    assert case.context.business.activity.niche not in stored
    assert "api_key" not in stored.lower() and "prompt" not in stored.lower()
