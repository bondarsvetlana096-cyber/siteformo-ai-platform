from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import asyncio
import hashlib
import inspect
import json
from pathlib import Path
import signal
import threading
from time import perf_counter
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.site_planner import SitePlannerProviderResult
from app.services.final_site_planner import create_final_site_plan_v1
from app.services.site_planner_config import SitePlannerConfigV1
from app.services.site_planner_provider import SitePlannerProvider
from evals.site_planner.eval_cases import EvalCase, site_planner_eval_cases_v1
from evals.site_planner.eval_checkpoint import (
    CallEventV1, DurableEvaluationBudgetExceeded, EvalCheckpointStore, EvalManifestV1,
    InterruptedRunRequiresExplicitResume, utc_now,
)
from evals.site_planner.eval_metrics import (
    EVAL_PRICING_VERSION, EvalRunMetrics, MODEL_PRICING_PER_MILLION, collect_metrics,
)
from evals.site_planner.eval_diagnostics import emit_diagnostic, run_diagnostic, summary_diagnostic


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
    max_eval_wall_seconds: int | None = Field(default=None, ge=1, le=86_400)


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
    def __init__(self, provider: SitePlannerProvider, model: str, budget: EvaluationBudget | None, context_input_tokens: int, max_output_tokens: int, *, checkpoint: EvalCheckpointStore | None = None, spec: EvalRunSpec | None = None, max_calls: int | None = None, max_spend: float | None = None) -> None:
        self.provider = provider; self.model = model; self.budget = budget
        self.context_input_tokens = context_input_tokens; self.max_output_tokens = max_output_tokens
        self.checkpoint = checkpoint; self.spec = spec
        self.max_calls = max_calls; self.max_spend = max_spend
        self.observations: list[tuple[SitePlannerProviderResult, int]] = []
        self._transport_attempts: dict[tuple[str, int], int] = {}

    async def create_structured_candidate(self, request):
        rates = MODEL_PRICING_PER_MILLION[self.model]
        ceiling = self.context_input_tokens * rates["input"] / 1_000_000 + self.max_output_tokens * rates["output"] / 1_000_000
        key = (request.attempt_type, request.attempt_number)
        transport = self._transport_attempts.get(key, 0) + 1
        self._transport_attempts[key] = transport
        call_id = uuid.uuid4().hex
        started_at = utc_now()
        event = None
        if self.checkpoint is not None and self.spec is not None:
            event = CallEventV1(
                call_id=call_id, eval_run_id=self.checkpoint.eval_run_id,
                run_hash=self.spec.run_hash, case_id=self.spec.case.case_id,
                model=self.model, semantic_attempt=request.attempt_number,
                transport_attempt=transport, call_started_at=started_at,
                status="started", reserved_unknown_spend=ceiling,
            )
            self.checkpoint.begin_call(event, self.max_calls or 0, self.max_spend or 0)
        elif self.budget is not None:
            self.budget.authorize_call(ceiling)
        started = perf_counter()
        try:
            result = await self.provider.create_structured_candidate(request)
        except asyncio.CancelledError:
            latency = int((perf_counter() - started) * 1000)
            if event is not None:
                self.checkpoint.finish_call(event.model_copy(update={"status": "cancelled", "completed_at": utc_now(), "elapsed_ms": latency, "provider_error_category": "unknown"}))
            raise
        except BaseException:
            latency = int((perf_counter() - started) * 1000)
            if event is not None:
                self.checkpoint.finish_call(event.model_copy(update={"status": "provider_failure", "completed_at": utc_now(), "elapsed_ms": latency, "provider_error_category": "unknown"}))
            raise
        latency = int((perf_counter() - started) * 1000)
        try:
            if not isinstance(result, SitePlannerProviderResult):
                result = SitePlannerProviderResult.model_validate(result)
        except Exception:
            result = SitePlannerProviderResult(status="failure", error_category="malformed_response")
        self.observations.append((result, latency))
        cached = min(result.cached_input_tokens or 0, result.input_tokens or 0)
        actual = ((result.input_tokens or 0) - cached) * rates["input"] / 1_000_000 + cached * rates["cached_input"] / 1_000_000 + (result.output_tokens or 0) * rates["output"] / 1_000_000
        if event is not None:
            category = result.error_category
            status = "completed" if result.status == "candidate" else "timeout" if category == "timeout" else "provider_failure"
            usage_known = result.input_tokens is not None or result.output_tokens is not None
            self.checkpoint.finish_call(event.model_copy(update={
                "status": status, "completed_at": utc_now(), "elapsed_ms": latency,
                "provider_error_category": category,
                "input_tokens": result.input_tokens, "cached_input_tokens": result.cached_input_tokens,
                "output_tokens": result.output_tokens, "reasoning_tokens": result.reasoning_tokens,
                "total_tokens": result.total_tokens, "output_bytes": result.reported_output_bytes,
                "estimated_actual_cost": actual if usage_known else None,
            }))
        elif self.budget is not None:
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
    repository_root: Path | None = None, eval_run_id: str | None = None,
    resume: bool = False, resume_interrupted_runs: bool = False,
    branch: str = "unknown", progress: Callable[[str], None] = print,
) -> tuple[EvalRunMetrics, ...]:
    if real_provider and not real_provider_allowed(config, environment or {}):
        raise PermissionError("both real-provider evaluation switches are required")
    specs = matrix(config, cases)
    run_id = eval_run_id or uuid.uuid4().hex
    checkpoint: EvalCheckpointStore | None = None
    durable_required = real_provider or repository_root is not None
    if real_provider and repository_root is None:
        raise ValueError("real-provider evaluation requires a durable checkpoint repository_root")
    if durable_required:
        checkpoint = EvalCheckpointStore(repository_root or Path.cwd(), config.artifact_directory, run_id)
        now = utc_now()
        manifest = EvalManifestV1(
            eval_run_id=run_id, repository_sha=repository_commit_sha, branch=branch,
            pricing_version=EVAL_PRICING_VERSION, models=list(config.models),
            reasoning_effort=config.reasoning_effort, trial_count=config.trials,
            configured_cases=[{"case_id": spec.case.case_id, "case_hash": spec.case_hash} for spec in specs[::len(config.models) * config.trials]],
            planner_contract_version="v1", generation_context_version="v1",
            site_plan_schema_version="v1", validator_version="v1",
            provider_config_version=config.planner_config_version,
            max_real_calls=config.max_real_calls,
            max_estimated_spend_usd=config.max_estimated_spend_usd,
            max_eval_wall_seconds=config.max_eval_wall_seconds,
            started_at=now, updated_at=now, status="running", next_matrix_position=0,
            total_matrix_entries=len(specs), completed_run_hashes=[],
        )
        manifest = checkpoint.create_or_resume(manifest, resume=resume)
        interrupted = checkpoint.interrupted_run_hashes()
        if interrupted and not resume_interrupted_runs:
            checkpoint.update_manifest(status="interrupted")
            raise InterruptedRunRequiresExplicitResume(
                "an interrupted provider call requires explicit resume_interrupted_runs"
            )
    budget = None if checkpoint else EvaluationBudget(config.max_real_calls, config.max_estimated_spend_usd)
    completed = checkpoint.completed_metrics() if checkpoint else {}
    results = list(completed.values())
    evaluation_started = perf_counter()
    prior_wall_seconds = 0.0
    if checkpoint:
        prior_wall_seconds = max(
            0.0,
            (datetime.now(timezone.utc) - datetime.fromisoformat(manifest.started_at)).total_seconds(),
        )
    previous_sigterm = None

    def diagnostic_budget_state() -> dict[str, float | int]:
        if checkpoint:
            return checkpoint.budget_state()
        return {
            "calls_used": budget.calls if budget else 0,
            "known_estimated_spend": budget.estimated_spend if budget else 0.0,
            "unknown_call_reserve": 0.0,
            "budget_committed_total": budget.estimated_spend if budget else 0.0,
        }

    def emit_summary(prefix: str) -> None:
        emit_diagnostic(
            prefix,
            summary_diagnostic(run_id, tuple(completed.values()), len(specs), diagnostic_budget_state()),
            progress,
        )
    if checkpoint and threading.current_thread() is threading.main_thread():
        try:
            previous_sigterm = signal.getsignal(signal.SIGTERM)
            def _interrupt_for_sigterm(signum, frame):
                checkpoint.update_manifest(status="interrupted")
                raise KeyboardInterrupt("evaluation interrupted by SIGTERM")
            signal.signal(signal.SIGTERM, _interrupt_for_sigterm)
        except (AttributeError, ValueError):
            previous_sigterm = None
    try:
      for position, spec in enumerate(specs, start=1):
        if spec.run_hash in completed:
            continue
        if config.max_eval_wall_seconds is not None and prior_wall_seconds + perf_counter() - evaluation_started >= config.max_eval_wall_seconds:
            if checkpoint:
                checkpoint.write_summary(len(specs))
                checkpoint.update_manifest(status="interrupted_time_budget", next_matrix_position=position - 1)
            break
        progress(f"{position}/{len(specs)} {spec.model} {spec.case.case_id} status=starting")
        run_started_at = utc_now()
        input_tokens = max(1, len(json.dumps(spec.case.context.model_dump(mode="json"))) // 4)
        provider = provider_factory(spec)
        if _network_capable_provider(provider) and not real_provider:
            raise PermissionError("network-capable provider requires explicit real-provider mode")
        recording = RecordingBudgetProvider(
            provider, spec.model, budget, input_tokens, config.max_output_tokens,
            checkpoint=checkpoint, spec=spec, max_calls=config.max_real_calls,
            max_spend=config.max_estimated_spend_usd,
        )
        planner_config = SitePlannerConfigV1(provider_identifier="evaluation", model_identifier=spec.model, provider_config_version=config.planner_config_version)
        verifier = context_verifier_factory(spec) if context_verifier_factory else (lambda value: value)
        outcome = await create_final_site_plan_v1(spec.case.context, recording, planner_config, verifier)
        metadata = {key: str(value) for key, value in reproducibility_metadata(spec, repository_commit_sha).items()}
        metrics = collect_metrics(run_hash=spec.run_hash, case_hash=spec.case_hash, case_id=spec.case.case_id, model=spec.model, trial=spec.trial, result=outcome, observations=recording.observations, reproducibility=metadata)
        results.append(metrics)
        completed[spec.run_hash] = metrics
        if checkpoint:
            checkpoint.commit_run(metrics, run_started_at, utc_now())
            summary = checkpoint.write_summary(len(specs))
            state = checkpoint.budget_state()
            checkpoint.update_manifest(
                status="running", next_matrix_position=position,
                completed_run_hashes=list(completed),
            )
            progress(
                f"{position}/{len(specs)} {spec.model} {spec.case.case_id} "
                f"status={metrics.planner_status} first_pass={str(metrics.first_pass_valid).lower()} "
                f"calls_used={state['calls_used']}/{config.max_real_calls} "
                f"known_spend={state['known_estimated_spend']:.6f} "
                f"budget_committed={state['budget_committed_total']:.6f} checkpoint={summary.name}"
            )
        else:
            progress(f"{position}/{len(specs)} {spec.model} {spec.case.case_id} status={metrics.planner_status}")
        emit_diagnostic("SITE_PLANNER_EVAL_RUN=", run_diagnostic(run_id, metrics), progress)
        emit_summary("SITE_PLANNER_EVAL_PARTIAL=")
    except DurableEvaluationBudgetExceeded:
        if checkpoint:
            checkpoint.write_summary(len(specs))
            checkpoint.update_manifest(status="aborted_budget")
        emit_summary("SITE_PLANNER_EVAL_PARTIAL=")
        raise
    except (KeyboardInterrupt, asyncio.CancelledError):
        if checkpoint:
            checkpoint.write_summary(len(specs))
            checkpoint.update_manifest(status="interrupted")
        emit_summary("SITE_PLANNER_EVAL_PARTIAL=")
        raise
    except BaseException:
        if checkpoint:
            checkpoint.write_summary(len(specs))
            checkpoint.update_manifest(status="failed")
        emit_summary("SITE_PLANNER_EVAL_PARTIAL=")
        raise
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
    if checkpoint and len(completed) == len(specs):
        checkpoint.write_summary(len(specs), final=True)
        checkpoint.update_manifest(status="completed", next_matrix_position=len(specs), completed_run_hashes=list(completed))
    if len(completed) == len(specs):
        emit_summary("SITE_PLANNER_EVAL_FINAL=")
    elif completed:
        emit_summary("SITE_PLANNER_EVAL_PARTIAL=")
    return tuple(results)
