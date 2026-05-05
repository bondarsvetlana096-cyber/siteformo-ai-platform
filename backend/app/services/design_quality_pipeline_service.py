from __future__ import annotations

from typing import Any, Dict, List

from app.services.auto_improvement_service import improve_preview_prompt
from app.services.pre_delivery_check_service import decide_preview_status
from app.services.quality_package_rules import get_package_rules
from app.services.quality_review_service import QualityReviewService
from app.services.technical_check_service import technical_check_preview


def _extract_package(order: Any, brief: Dict[str, Any]) -> str:
    return (
        brief.get("plan")
        or brief.get("package")
        or brief.get("tier")
        or getattr(order, "recommended_tier", None)
        or "starter"
    )


class DesignQualityPipelineService:
    def __init__(self) -> None:
        self.reviewer = QualityReviewService()

    def run_for_order(
        self,
        order: Any,
        preview_payload: Dict[str, Any],
        extended_brief: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        brief = extended_brief or getattr(order, "extended_brief", None) or {}
        rules = get_package_rules(_extract_package(order, brief))
        target_score = float(rules["target_score"])
        max_iterations = int(rules["max_quality_iterations"])
        max_warnings = int(rules["max_warnings"])

        source_previews: List[Dict[str, Any]] = list(preview_payload.get("design_previews") or [])
        checked_previews: List[Dict[str, Any]] = []

        for preview in source_previews:
            current = dict(preview)
            history: List[Dict[str, Any]] = []
            final_decision: Dict[str, Any] | None = None

            for attempt in range(0, max_iterations + 1):
                technical = technical_check_preview(current, brief)
                review = self.reviewer.review_preview(current, brief, rules["package"], target_score)
                decision = decide_preview_status(technical, review, target_score, max_warnings)

                history.append({
                    "attempt": attempt,
                    "technical": technical,
                    "review": review,
                    "decision": decision,
                })

                final_decision = decision
                if decision["ready_to_send"]:
                    break

                if attempt >= max_iterations:
                    break

                current = improve_preview_prompt(
                    current,
                    review.get("regeneration_prompt") or "Improve this preview quality.",
                    attempt + 1,
                )

            current["quality_report"] = {
                "status": final_decision.get("status") if final_decision else "MANUAL_REVIEW_REQUIRED",
                "overall_score": final_decision.get("overall_score") if final_decision else 0,
                "ready_to_send": bool(final_decision and final_decision.get("ready_to_send")),
                "critical_errors": final_decision.get("critical_errors") if final_decision else ["No decision produced"],
                "warnings": final_decision.get("warnings") if final_decision else [],
                "history": history,
            }
            current["status"] = current["quality_report"]["status"]
            checked_previews.append(current)

        ready = [p for p in checked_previews if (p.get("quality_report") or {}).get("ready_to_send")]
        failed = [p for p in checked_previews if not (p.get("quality_report") or {}).get("ready_to_send")]

        if ready:
            overall_status = "READY_TO_SEND"
            next_action = "SEND_PREVIEWS_EMAIL"
        else:
            overall_status = "MANUAL_REVIEW_REQUIRED"
            next_action = "MANUAL_REVIEW"

        scores = [float((p.get("quality_report") or {}).get("overall_score") or 0) for p in checked_previews]
        average_score = round(sum(scores) / len(scores), 2) if scores else 0

        return {
            "status": overall_status,
            "next_action": next_action,
            "package": rules["package"],
            "target_score": target_score,
            "average_score": average_score,
            "ready_count": len(ready),
            "failed_count": len(failed),
            "design_previews": checked_previews,
            "logo_previews": preview_payload.get("logo_previews") or [],
            "manual_review_required": overall_status == "MANUAL_REVIEW_REQUIRED",
        }
