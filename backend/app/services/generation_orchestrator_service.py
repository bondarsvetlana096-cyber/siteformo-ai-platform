from __future__ import annotations

from typing import Any, Callable, Dict, List

from app.services.auto_improvement_service import auto_improve
from app.services.canonical_brief_service import get_or_build_canonical_brief
from app.services.package_rules_service import get_generation_budget
from app.services.pre_delivery_check_service import decide_preview_status
from app.services.quality_review_service import review_site
from app.services.technical_check_service import technical_check_preview


class GenerationOrchestratorService:
    """Central SiteFormo final-generation pipeline.

    Flow:
    Canonical Brief -> Generate v1 -> Review -> Improve -> Review -> Final/Manual Review.

    Cost rule:
    The pipeline is hard-capped to max 3 improvement rounds globally.
    """

    def run_final_html_pipeline(
        self,
        order: Any,
        initial_html: str,
        improve_fn: Callable[..., str] | None = None,
    ) -> Dict[str, Any]:
        canonical_brief = get_or_build_canonical_brief(order)
        package = canonical_brief.get("commercial_scope", {}).get("package", "starter")
        budget = get_generation_budget(package)

        target_score = float(budget.get("target_score") or 7.5)
        max_iterations = int(budget.get("max_quality_iterations") or 1)
        max_warnings = int(budget.get("max_warnings") or 5)

        current_html = initial_html or ""
        history: List[Dict[str, Any]] = []
        final_decision: Dict[str, Any] = {
            "status": "MANUAL_REVIEW_REQUIRED",
            "ready_to_send": False,
            "overall_score": 0,
            "critical_errors": ["No quality decision produced"],
            "warnings": [],
        }

        for attempt in range(0, max_iterations + 1):
            preview_like_payload = {
                "id": "final_html",
                "type": "final_divi_html",
                "label": "Final generated website",
                "prompt": current_html,
                "html": current_html,
            }

            technical = technical_check_preview(preview_like_payload, canonical_brief)
            review = review_site(
                site_content=current_html,
                brief=canonical_brief,
                package=package,
                target_score=target_score,
            )
            decision = decide_preview_status(technical, review, target_score, max_warnings)

            history.append({
                "attempt": attempt,
                "round_type": "initial" if attempt == 0 else "improvement",
                "technical": technical,
                "review": review,
                "decision": decision,
            })
            final_decision = decision

            if decision.get("ready_to_send"):
                break
            if attempt >= max_iterations:
                break

            regeneration_prompt = (
                review.get("regeneration_prompt")
                or "Improve this final website with stronger offer clarity, hero, CTA, trust elements, mobile readiness, visual quality and package fit."
            )

            if improve_fn:
                current_html = improve_fn(
                    site_text=current_html,
                    feedback_prompt=regeneration_prompt,
                    brief=canonical_brief,
                    package=package,
                )
            else:
                current_html = auto_improve(
                    site_text=current_html,
                    feedback_prompt=regeneration_prompt,
                    brief=canonical_brief,
                    package=package,
                )

        ready = bool(final_decision.get("ready_to_send"))
        status = "READY_TO_SEND" if ready else "MANUAL_REVIEW_REQUIRED"
        if not ready:
            final_decision.setdefault("warnings", [])
            final_decision["warnings"] = list(final_decision.get("warnings") or []) + [
                "Generation stopped by cost guard. Manual review is required instead of additional AI regeneration."
            ]

        return {
            "status": status,
            "ready_to_send": ready,
            "package": package,
            "target_score": target_score,
            "max_iterations": max_iterations,
            "max_total_generation_rounds": max_iterations + 1,
            "cost_guard": budget.get("hard_cap_reason"),
            "final_score": float(final_decision.get("overall_score") or 0),
            "critical_errors": final_decision.get("critical_errors") or [],
            "warnings": final_decision.get("warnings") or [],
            "history": history,
            "canonical_brief": canonical_brief,
            "final_decision": final_decision,
            "html": current_html,
        }
