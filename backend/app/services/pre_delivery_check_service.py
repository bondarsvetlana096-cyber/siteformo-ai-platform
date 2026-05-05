from __future__ import annotations

from typing import Any, Dict, List


def decide_preview_status(
    technical_result: Dict[str, Any],
    review_result: Dict[str, Any],
    target_score: float,
    max_warnings: int,
) -> Dict[str, Any]:
    critical_errors: List[str] = []
    warnings: List[str] = []

    critical_errors.extend(technical_result.get("errors") or [])
    warnings.extend(technical_result.get("warnings") or [])
    critical_errors.extend(review_result.get("critical_errors") or [])
    warnings.extend(review_result.get("warnings") or [])

    score = float(review_result.get("overall_score") or 0)

    if critical_errors:
        status = "NEEDS_REGENERATION"
        next_action = "AUTO_FIX"
    elif score < target_score:
        status = "NEEDS_REGENERATION"
        next_action = "AUTO_FIX"
    elif len(warnings) > max_warnings:
        status = "NEEDS_FIX"
        next_action = "AUTO_FIX"
    else:
        status = "READY_TO_SEND"
        next_action = "READY_TO_SEND"

    return {
        "status": status,
        "next_action": next_action,
        "ready_to_send": status == "READY_TO_SEND",
        "overall_score": round(score, 2),
        "critical_errors": critical_errors,
        "warnings": warnings,
    }
