from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean

from evals.site_planner.eval_metrics import EvalRunMetrics


def model_summary(runs: tuple[EvalRunMetrics, ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for model in sorted({run.model for run in runs}):
        selected = [run for run in runs if run.model == model]
        count = len(selected)
        pct = lambda value: round(100 * value / count, 2) if count else 0.0
        rows.append({
            "model": model,
            "runs": count,
            "first_pass_valid_percent": pct(sum(run.first_pass_valid for run in selected)),
            "final_valid_percent": pct(sum(run.final_valid for run in selected)),
            "repair_percent": pct(sum(run.repair_used for run in selected)),
            "manual_review_percent": pct(sum(run.manual_review for run in selected)),
            "hallucination_events": sum(run.hallucinated_function_count for run in selected),
            "safety_violations": sum(run.interaction_safety_violation_count for run in selected),
            "average_latency_ms": round(mean(run.usage.latency_ms for run in selected), 2) if selected else 0,
            "average_input_tokens": round(mean(run.usage.input_tokens for run in selected), 2) if selected else 0,
            "average_output_tokens": round(mean(run.usage.output_tokens for run in selected), 2) if selected else 0,
            "average_reasoning_tokens": round(mean(run.usage.reasoning_tokens for run in selected), 2) if selected else 0,
            "average_estimated_cost": round(mean(run.cost.estimated_total_cost for run in selected), 8) if selected else 0,
        })
    return rows


def report_payload(runs: tuple[EvalRunMetrics, ...]) -> dict[str, object]:
    return {
        "model_summary": model_summary(runs),
        "case_by_case": [{
            "case_id": run.case_id, "model": run.model, "planner_status": run.planner_status,
            "first_pass_valid": run.first_pass_valid, "final_valid": run.final_valid,
            "repair_used": run.repair_used, "reason_codes": run.validator_reason_codes,
            "latency_ms": run.usage.latency_ms, "estimated_cost": run.cost.estimated_total_cost,
        } for run in runs],
    }


def _artifact_root(repository_root: Path, configured: str) -> Path:
    root = repository_root.resolve()
    target = (root / configured).resolve()
    allowed = (root / ".stage-artifacts").resolve()
    if target != allowed and allowed not in target.parents:
        raise ValueError("evaluation artifacts must remain under .stage-artifacts")
    return target


def write_artifacts(repository_root: Path, configured_directory: str, runs: tuple[EvalRunMetrics, ...]) -> Path:
    target = _artifact_root(repository_root, configured_directory)
    target.mkdir(parents=True, exist_ok=True)
    safe_rows = []
    valid_dir = target / "validated-plans"
    for run in runs:
        row = run.model_dump(mode="json", exclude={"validated_plan"})
        safe_rows.append(row)
        if run.final_valid and run.validated_plan is not None:
            valid_dir.mkdir(exist_ok=True)
            (valid_dir / f"{run.run_hash}.json").write_text(
                json.dumps(run.validated_plan.model_dump(mode="json"), sort_keys=True, indent=2), encoding="utf-8"
            )
    (target / "runs.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in safe_rows), encoding="utf-8")
    payload = report_payload(runs)
    (target / "summary.json").write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    with (target / "runs.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["case_id", "model", "planner_status", "first_pass_valid", "final_valid", "repair_used", "latency_ms", "estimated_cost"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for run in runs:
            writer.writerow({"case_id": run.case_id, "model": run.model, "planner_status": run.planner_status,
                "first_pass_valid": run.first_pass_valid, "final_valid": run.final_valid, "repair_used": run.repair_used,
                "latency_ms": run.usage.latency_ms, "estimated_cost": run.cost.estimated_total_cost})
    return target
