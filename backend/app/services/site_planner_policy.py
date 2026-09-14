from __future__ import annotations

from app.schemas.site_planner import PlannerPolicyV1


PLANNER_CONTRACT_VERSION = "v1"
PLANNING_STRATEGY_VERSION = "single_call_v1"


def planner_policy_v1() -> PlannerPolicyV1:
    return PlannerPolicyV1(
        contract_version="v1",
        context_is_immutable_authority=True,
        output_contract="site_plan_v1",
        one_candidate_only=True,
        prose_reasoning_forbidden=True,
        raw_code_forbidden=True,
        arbitrary_urls_forbidden=True,
        functionality_invention_forbidden=True,
        addon_invention_forbidden=True,
        package_or_capacity_change_forbidden=True,
        payment_or_legal_decisions_forbidden=True,
        factual_sources_must_be_confirmed=True,
        mobile_coverage_required=True,
        reduced_motion_coverage_required=True,
        interaction_safety_required=True,
        max_semantic_repairs=1,
    )
