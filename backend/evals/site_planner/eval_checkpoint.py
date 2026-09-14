from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.site_plan import SitePlanV1
from evals.site_planner.eval_metrics import EvalRunMetrics
from evals.site_planner.eval_report import model_summary


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointCompatibilityError(RuntimeError):
    pass


class InterruptedRunRequiresExplicitResume(RuntimeError):
    pass


class DurableEvaluationBudgetExceeded(RuntimeError):
    pass


class EvalManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal["v1"] = "v1"
    eval_run_id: str = Field(min_length=1, max_length=160)
    repository_sha: str
    branch: str
    pricing_version: str
    models: list[str]
    reasoning_effort: str
    trial_count: int
    configured_cases: list[dict[str, str]]
    planner_contract_version: str
    generation_context_version: str
    site_plan_schema_version: str
    validator_version: str
    provider_config_version: str
    max_real_calls: int
    max_estimated_spend_usd: float
    max_eval_wall_seconds: int | None
    started_at: str
    updated_at: str
    status: Literal[
        "running", "interrupted", "interrupted_time_budget", "completed",
        "aborted_budget", "failed",
    ]
    next_matrix_position: int
    total_matrix_entries: int
    completed_run_hashes: list[str]


class CallEventV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal["v1"] = "v1"
    call_id: str
    eval_run_id: str
    run_hash: str
    case_id: str
    model: str
    semantic_attempt: int
    transport_attempt: int
    call_started_at: str
    status: Literal[
        "started", "completed", "timeout", "provider_failure", "cancelled",
        "unknown_completion",
    ]
    completed_at: str | None = None
    elapsed_ms: int | None = None
    provider_error_category: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    output_bytes: int | None = None
    estimated_actual_cost: float | None = None
    reserved_unknown_spend: float = 0.0


def _safe_root(repository_root: Path, configured: str, eval_run_id: str) -> Path:
    root = repository_root.resolve()
    base = (root / configured).resolve()
    allowed = (root / ".stage-artifacts").resolve()
    if base != allowed and allowed not in base.parents:
        raise ValueError("evaluation checkpoints must remain under .stage-artifacts")
    return base / eval_run_id


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    payload = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                break  # an abrupt final append may leave one incomplete tail line
            if isinstance(value, dict):
                rows.append(value)
    return rows


class EvalCheckpointStore:
    def __init__(self, repository_root: Path, configured_directory: str, eval_run_id: str) -> None:
        self.root = _safe_root(repository_root, configured_directory, eval_run_id)
        self.manifest_path = self.root / "run_manifest.json"
        self.calls_path = self.root / "calls.jsonl"
        self.runs_path = self.root / "runs.jsonl"
        self.validated_plans = self.root / "validated_plans"
        self.eval_run_id = eval_run_id

    def create_or_resume(self, expected: EvalManifestV1, *, resume: bool) -> EvalManifestV1:
        if self.manifest_path.exists():
            if not resume:
                raise FileExistsError("evaluation run already exists; explicit resume is required")
            existing = EvalManifestV1.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))
            immutable = set(EvalManifestV1.model_fields) - {
                "started_at", "updated_at", "status", "next_matrix_position", "completed_run_hashes",
            }
            for field in immutable:
                if getattr(existing, field) != getattr(expected, field):
                    raise CheckpointCompatibilityError(f"resume compatibility mismatch: {field}")
            self.recover_started_calls()
            return existing
        if resume:
            raise FileNotFoundError("evaluation checkpoint does not exist")
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.manifest_path, expected.model_dump(mode="json"))
        return expected

    def update_manifest(self, **changes: object) -> EvalManifestV1:
        manifest = EvalManifestV1.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))
        updated = manifest.model_copy(update={"updated_at": utc_now(), **changes})
        _atomic_json(self.manifest_path, updated.model_dump(mode="json"))
        return updated

    def call_events(self) -> list[CallEventV1]:
        return [CallEventV1.model_validate(row) for row in read_jsonl(self.calls_path)]

    def latest_calls(self) -> dict[str, CallEventV1]:
        return {event.call_id: event for event in self.call_events()}

    def recover_started_calls(self) -> list[CallEventV1]:
        recovered: list[CallEventV1] = []
        for event in self.latest_calls().values():
            if event.status == "started":
                terminal = event.model_copy(update={
                    "status": "unknown_completion", "completed_at": utc_now(),
                    "provider_error_category": "unknown",
                })
                _append_jsonl(self.calls_path, terminal.model_dump(mode="json"))
                recovered.append(terminal)
        return recovered

    def budget_state(self) -> dict[str, float | int]:
        calls = self.latest_calls().values()
        known = sum(call.estimated_actual_cost or 0.0 for call in calls)
        unknown = sum(
            call.reserved_unknown_spend for call in calls
            if call.estimated_actual_cost is None
        )
        return {
            "calls_used": len(list(calls)),
            "known_estimated_spend": known,
            "unknown_call_reserve": unknown,
            "budget_committed_total": known + unknown,
        }

    def begin_call(self, event: CallEventV1, max_calls: int, max_spend: float) -> None:
        state = self.budget_state()
        if int(state["calls_used"]) >= max_calls:
            raise DurableEvaluationBudgetExceeded("evaluation call budget exhausted")
        if float(state["budget_committed_total"]) + event.reserved_unknown_spend > max_spend:
            raise DurableEvaluationBudgetExceeded("evaluation spend budget exhausted")
        _append_jsonl(self.calls_path, event.model_dump(mode="json"))

    def finish_call(self, event: CallEventV1) -> None:
        _append_jsonl(self.calls_path, event.model_dump(mode="json"))

    def completed_metrics(self) -> dict[str, EvalRunMetrics]:
        completed: dict[str, EvalRunMetrics] = {}
        for row in read_jsonl(self.runs_path):
            metric = EvalRunMetrics.model_validate(row["metrics"])
            completed[metric.run_hash] = metric
        return completed

    def interrupted_run_hashes(self) -> set[str]:
        # A process can die after a provider result is ledgered but before the
        # terminal run record is durable. Re-inference is never implicit.
        return {event.run_hash for event in self.latest_calls().values()} - set(self.completed_metrics())

    def commit_run(self, metric: EvalRunMetrics, started_at: str, completed_at: str) -> str | None:
        plan_path: str | None = None
        if metric.final_valid and metric.validated_plan is not None:
            relative = Path("validated_plans") / f"{metric.run_hash}.json"
            _atomic_json(self.root / relative, metric.validated_plan.model_dump(mode="json"))
            plan_path = relative.as_posix()
        row = {
            "contract_version": "v1", "run_id": metric.run_hash,
            "started_at": started_at, "completed_at": completed_at,
            "validated_plan_path": plan_path,
            "metrics": metric.model_dump(mode="json", exclude={"validated_plan"}),
        }
        _append_jsonl(self.runs_path, row)
        return plan_path

    def write_summary(self, planned: int, *, final: bool = False) -> Path:
        runs = tuple(self.completed_metrics().values())
        state = self.budget_state()
        manifest = EvalManifestV1.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))
        reasons = Counter(reason for run in runs for reason in run.validator_reason_codes)
        payload = {
            "partial": not final,
            "status": "completed" if final else "running",
            "completed_matrix_entries": len(runs),
            "remaining_matrix_entries": max(0, planned - len(runs)),
            "calls_used": state["calls_used"],
            "calls_remaining": max(0, manifest.max_real_calls - int(state["calls_used"])),
            "known_estimated_spend": state["known_estimated_spend"],
            "reserved_unknown_spend": state["unknown_call_reserve"],
            "budget_committed_total": state["budget_committed_total"],
            "first_pass_valid_rate": sum(run.first_pass_valid for run in runs) / len(runs) if runs else 0.0,
            "final_valid_rate": sum(run.final_valid for run in runs) / len(runs) if runs else 0.0,
            "repair_rate": sum(run.repair_used for run in runs) / len(runs) if runs else 0.0,
            "manual_review_rate": sum(run.manual_review for run in runs) / len(runs) if runs else 0.0,
            "reason_code_frequencies": dict(sorted(reasons.items())),
            "model_summary": model_summary(runs),
        }
        path = self.root / ("summary.final.json" if final else "summary.partial.json")
        _atomic_json(path, payload)
        return path
