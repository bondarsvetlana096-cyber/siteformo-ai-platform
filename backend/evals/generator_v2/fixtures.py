"""Pure canonical synthetic Generator V2 fixture chain.

This package is intentionally independent of ``backend/tests`` and has no
runtime, persistence, provider, filesystem, or network dependencies.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.generation_context import GenerationContextV1
from app.schemas.site_plan import SitePlanV1
from app.schemas.site_planner import PlannerConstraintProjectionV1
from app.services.generator_v2_contract import (
    GeneratorV2InputSnapshotV1,
    GeneratorV2SelectedDesignIdentityV1,
    build_generator_v2_input_snapshot,
)
from app.services.generator_v2_implementation import (
    GeneratorV2ImplementationSpecV1,
    build_generator_v2_implementation_spec,
)
from app.services.generator_v2_visual import VisualImplementationInputV1, build_visual_implementation_input
from app.services.site_plan_validator import validate_site_plan_v1
from app.services.site_planner_constraint_projection import build_planner_constraint_projection_v1
from evals.site_planner.eval_cases import EvalCase, site_planner_eval_cases_v1


class SyntheticGeneratorV2FixtureV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    synthetic: Literal[True] = True
    case_id: str
    context: GenerationContextV1
    projection: PlannerConstraintProjectionV1
    site_plan: SitePlanV1 | None
    snapshot: GeneratorV2InputSnapshotV1 | None
    implementation: GeneratorV2ImplementationSpecV1 | None
    visual_input: VisualImplementationInputV1 | None
    readiness: Literal["READY", "LARGE_PLAN_REQUIRES_STAGED_PLANNING"]


def _selected(context: GenerationContextV1) -> GeneratorV2SelectedDesignIdentityV1:
    return GeneratorV2SelectedDesignIdentityV1(
        design_direction=context.visual.design_direction.value,
        design_direction_contract_version="v1",
        interaction_preference=context.interaction_guidance.client_preference.value,
        interaction_preference_contract_version="v1",
    )


def _behavior() -> dict[str, object]:
    return {"strategy": "stack", "touch_safe_equivalent": True, "hover_only_required": False}


def _reduced() -> dict[str, object]:
    return {"strategy": "static_equivalent", "preserves_content_and_functionality": True}


def _section(key: str = "hero", *, components: list[str] | None = None, critical: bool = True, motion: str = "subtle") -> dict[str, object]:
    return {
        "section_key": key,
        "purpose": "Present the confirmed offer clearly",
        "content_requirements": [{"content_key": "primary_goal", "kind": "confirmed_fact", "source_key": "business.primary_goal", "factual_claims_must_be_confirmed": True}],
        "media_requirements": [{"media_key": "hero_media", "kind": "no_media", "required": False}],
        "functional_components": components if components is not None else ["enquiry_form"],
        "primary_actions": [{"action_key": "enquire", "intent": "Send a project enquiry", "target_page_key": None, "critical_family": "primary_conversion_actions"}] if critical else [],
        "interaction_families": [], "signature_interactions": [],
        "motion_policy": {"level": motion, "required_for_completion": False, "blocks_critical_action": False},
        "critical_action": critical, "mobile_behavior": _behavior(), "reduced_motion_behavior": _reduced(),
    }


def _page(key: str = "home", role: str = "home", *, sections: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"page_key": key, "page_role": role, "purpose": f"Serve the {role} role", "route_intent": key, "sections": sections or [_section()], "mobile_behavior": _behavior(), "reduced_motion_behavior": _reduced()}


def _candidate(context: GenerationContextV1, components: list[str] | tuple[str, ...]) -> dict[str, object]:
    pages = [_page(sections=[_section(components=list(components), critical=True, motion="subtle")])]
    used = {component for item in pages for part in item["sections"] for component in part["functional_components"]}
    return {
        "contract_version": "v1", "generation_context_hash": context.fingerprints.context_hash,
        "plan_status": "draft", "pages": pages, "cross_page_navigation": [], "stateful_journeys": [], "shared_components": [],
        "content_inventory": [{"item_key": "primary_goal", "required": True}], "media_inventory": [], "functional_component_inventory": sorted(used),
        "accessibility_constraints": {"keyboard_operable": True, "touch_safe": True, "reduced_motion_supported": True, "critical_actions_stable": True, "no_hover_only_required_actions": True},
        "generation_constraints": {"source_design_direction": context.visual.design_direction.value, "source_interaction_preference": context.interaction_guidance.client_preference.value, "no_unconfirmed_functionality": True, "no_unsupported_addons": True, "no_factual_claim_invention": True, "no_raw_code": True},
        "unresolved_items": [], "planner_reasoning_codes": ["refine_provisional_architecture", "respect_confirmed_scope"], "validator_version": "v1", "site_plan_hash": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _cases() -> dict[str, EvalCase]:
    return {case.case_id: case for case in site_planner_eval_cases_v1()}


def build_generator_v2_fixture(case_id: str) -> SyntheticGeneratorV2FixtureV1:
    case = _cases()[case_id]
    context = case.context
    projection = build_planner_constraint_projection_v1(context)
    if case.expected_planning_outcome_class == "large_plan_manual_review":
        return SyntheticGeneratorV2FixtureV1(case_id=case_id, context=context, projection=projection, site_plan=None, snapshot=None, implementation=None, visual_input=None, readiness="LARGE_PLAN_REQUIRES_STAGED_PLANNING")
    validation = validate_site_plan_v1(context, _candidate(context, projection.allowed_functional_components))
    if validation.status != "valid" or validation.plan is None:
        raise ValueError("synthetic fixture site plan is not valid")
    snapshot_result = build_generator_v2_input_snapshot(context=context, projection=projection, site_plan=validation.plan, planner_operation_key="a" * 64, selected_design=_selected(context))
    if snapshot_result.status != "READY" or snapshot_result.snapshot is None:
        raise ValueError("synthetic fixture snapshot is not ready")
    implementation_result = build_generator_v2_implementation_spec(snapshot_result.snapshot)
    if implementation_result.status != "READY_TO_RENDER" or implementation_result.spec is None:
        raise ValueError("synthetic fixture implementation is not ready")
    visual_result = build_visual_implementation_input(snapshot_result.snapshot, implementation_result.spec)
    if visual_result.status != "READY" or visual_result.input is None:
        raise ValueError("synthetic fixture visual input is not ready")
    return SyntheticGeneratorV2FixtureV1(case_id=case_id, context=context, projection=projection, site_plan=validation.plan, snapshot=snapshot_result.snapshot, implementation=implementation_result.spec, visual_input=visual_result.input, readiness="READY")


def build_business_generator_v2_fixture() -> SyntheticGeneratorV2FixtureV1:
    return build_generator_v2_fixture("BUSINESS_THREE_PAGE")


def generator_v2_fixture_cases_v1() -> tuple[SyntheticGeneratorV2FixtureV1, ...]:
    """Build the seven representative cases plus the staging boundary."""
    return tuple(build_generator_v2_fixture(case.case_id) for case in site_planner_eval_cases_v1())
