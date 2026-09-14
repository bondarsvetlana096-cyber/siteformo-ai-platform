from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.site_planner import SitePlannerProviderResult
from app.services.final_site_planner import create_final_site_plan_v1
from app.services.site_planner_config import SitePlannerConfigV1
from app.services.site_planner_provider import SitePlannerProvider
from evals.site_planner.eval_cases import EvalCase, site_planner_eval_cases_v1
from evals.site_planner.eval_metrics import EvalRunMetrics, MODEL_PRICING_PER_MILLION, collect_metrics


EVAL_MODELS = ("gpt-5.6-sol", "gpt-6-astra")


class SitePlannerEvalConfigV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    models: tuple[Literal["gpt-5.6-sol", "gpt-6-astra"], ...] = EVAL_MODELS
    reasoning_effort: Literal["medium"] = "medium"
    trials: int = Field(default=1, ge=1, le=10)
    allow_real_provider: bool = False
    artifact_directory: str = ".stage-artifacts/site-planner-eval"
    planner_config_version: str = "openai-eval-v1"
    max_real_calls: int = Field(default=16, ge=1, le=1000)
    max_estimated_spend_usd: float = Field(default=25.0, gt=0, le=10_000)
    max_output_tokens: int = Field(default=24_576, ge=4_096, le=32_768)


class EvalRunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case: EvalCase
    model: Literal["gpt-5.6-sol", "gpt-6-astra"]
    reasoning_effort: Literal["medium"]
    trial: int
    case_hash: str
    run_hash: str


class EvaluationBudgetExceeded(RuntimeError):
    pass


class EvaluationBudget:
    def __init__(self, max_calls: int, max_spend: float) -> None:
        self.max_calls = max_calls; self.max_spend = max_spend
        self.calls = 0; self.estimated_spend = 0.0

    def authorize_call(self, estimated_ceiling: float) -> None:
        if self.calls >= self.max_calls:
            raise EvaluationBudgetExceeded("evaluation call budget exhausted")
        if self.estimated_spend + estimated_ceiling > self.max_spend:
            raise EvaluationBudgetExceeded("evaluation spend budget exhausted")
        self.calls += 1

    def record_actual(self, estimated_cost: float) -> None:
        self.estimated_spend += estimated_cost


def _hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def case_hash(case: EvalCase) -> str:
    return _hash(case.model_dump(mode="json"))


def matrix(config: SitePlannerEvalConfigV1, cases: tuple[EvalCase, ...] | None = None) -> tuple[EvalRunSpec, ...]:
    selected = cases or site_planner_eval_cases_v1(); runs = []
    for case in selected:
        fixture_hash = case_hash(case)
        for model in config.models:
            for trial in range(1, config.trials + 1):
                identity = {"case_hash": fixture_hash, "model": model, "reasoning_effort": config.reasoning_effort, "planner_config_version": config.planner_config_version, "trial": trial}
                runs.append(EvalRunSpec(case=case, model=model, reasoning_effort=config.reasoning_effort, trial=trial, case_hash=fixture_hash, run_hash=_hash(identity)))
    return tuple(runs)


def real_provider_allowed(config: SitePlannerEvalConfigV1, environment: Mapping[str, str]) -> bool:
    return config.allow_real_provider and environment.get("SITEFORMO_SITE_PLANNER_EVAL_ALLOW_REAL", "").lower() == "true"


def reproducibility_metadata(spec: EvalRunSpec, repository_commit_sha: str) -> dict[str, object]:
    return {
        "repository_commit_sha": repository_commit_sha,
        "generation_context_contract_version": spec.case.context.contract_version,
        "site_plan_schema_version": "v1", "planner_contract_version": "v1",
        "validator_version": "v1", "provider_config_version": "openai-eval-v1",
        "model": spec.model, "reasoning_effort": spec.reasoning_effort,
        "case_hash": spec.case_hash, "run_hash": spec.run_hash,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


class RecordingBudgetProvider:
    def __init__(self, provider: SitePlannerProvider, model: str, budget: EvaluationBudget, context_input_tokens: int, max_output_tokens: int) -> None:
        self.provider = provider; self.model = model; self.budget = budget
        self.context_input_tokens = context_input_tokens; self.max_output_tokens = max_output_tokens
        self.observations: list[tuple[SitePlannerProviderResult, int]] = []

    async def create_structured_candidate(self, request):
        rates = MODEL_PRICING_PER_MILLION[self.model]
        ceiling = self.context_input_tokens * rates["input"] / 1_000_000 + self.max_output_tokens * rates["output"] / 1_000_000
        self.budget.authorize_call(ceiling)
        started = perf_counter(); result = await self.provider.create_structured_candidate(request)
        latency = int((perf_counter() - started) * 1000)
        if not isinstance(result, SitePlannerProviderResult): result = SitePlannerProviderResult.model_validate(result)
        self.observations.append((result, latency))
        cached = min(result.cached_input_tokens or 0, result.input_tokens or 0)
        actual = ((result.input_tokens or 0) - cached) * rates["input"] / 1_000_000 + cached * rates["cached_input"] / 1_000_000 + (result.output_tokens or 0) * rates["output"] / 1_000_000
        self.budget.record_actual(actual)
        return result


ProviderFactory = Callable[[EvalRunSpec], SitePlannerProvider]
ContextVerifierFactory = Callable[[EvalRunSpec], Callable[[str], str]]


def _network_capable_provider(provider: SitePlannerProvider) -> bool:
    return provider.__class__.__module__ == "app.services.site_planner.providers.openai"


async def run_evaluation(
    config: SitePlannerEvalConfigV1, provider_factory: ProviderFactory,
    repository_commit_sha: str, *, real_provider: bool = False,
    environment: Mapping[str, str] | None = None, cases: tuple[EvalCase, ...] | None = None,
    context_verifier_factory: ContextVerifierFactory | None = None,
) -> tuple[EvalRunMetrics, ...]:
    if real_provider and not real_provider_allowed(config, environment or {}):
        raise PermissionError("both real-provider evaluation switches are required")
    budget = EvaluationBudget(config.max_real_calls, config.max_estimated_spend_usd)
    results = []
    for spec in matrix(config, cases):
        input_tokens = max(1, len(json.dumps(spec.case.context.model_dump(mode="json"))) // 4)
        provider = provider_factory(spec)
        if _network_capable_provider(provider) and not real_provider:
            raise PermissionError("network-capable provider requires explicit real-provider mode")
        recording = RecordingBudgetProvider(provider, spec.model, budget, input_tokens, config.max_output_tokens)
        planner_config = SitePlannerConfigV1(provider_identifier="evaluation", model_identifier=spec.model, provider_config_version=config.planner_config_version)
        verifier = context_verifier_factory(spec) if context_verifier_factory else (lambda value: value)
        outcome = await create_final_site_plan_v1(spec.case.context, recording, planner_config, verifier)
        metadata = {key: str(value) for key, value in reproducibility_metadata(spec, repository_commit_sha).items()}
        metrics = collect_metrics(run_hash=spec.run_hash, case_hash=spec.case_hash, case_id=spec.case.case_id, model=spec.model, trial=spec.trial, result=outcome, observations=recording.observations, reproducibility=metadata)
        results.append(metrics)
    return tuple(results)
