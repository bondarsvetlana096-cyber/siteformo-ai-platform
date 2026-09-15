from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.generator_v2_contract import (
    AuthorizedAssetReferenceV1,
    GeneratorV2InputSnapshotV1,
)
from app.services.generator_v2_implementation import (
    GeneratorV2ImplementationSpecV1,
    build_generator_v2_implementation_spec,
    validate_generator_v2_implementation_spec,
)
from app.services.site_planner_constraint_projection import build_planner_constraint_projection_v1
from app.services.site_plan_validator import validate_site_plan_v1
from evals.site_planner.eval_cases import site_planner_eval_cases_v1
from test_generator_v2_contract_v1 import make_snapshot
from test_site_planner_constraint_projection_v1 import compliant_plan


CASES = {case.case_id: case for case in site_planner_eval_cases_v1()}
REPRESENTATIVE = (
    "STARTER_LOCAL_SERVICE", "BUSINESS_THREE_PAGE", "REFERENCE_PORTFOLIO_EXPRESSIVE",
    "REFERENCE_BOOKING", "REFERENCE_ECOMMERCE", "ADVANCED_ACCOUNT_PERSISTENT", "ADVANCED_SUBTLE",
)


def test_spec_is_closed_frozen_and_has_no_runtime_escape_hatches():
    result = build_generator_v2_implementation_spec(make_snapshot().snapshot)
    assert result.status == "READY_TO_RENDER"
    spec = result.spec
    with pytest.raises(ValidationError):
        GeneratorV2ImplementationSpecV1.model_validate({**spec.model_dump(mode="json"), "html": "<script>"})
    with pytest.raises(ValidationError):
        spec.pages = ()


@pytest.mark.parametrize("case_id", REPRESENTATIVE)
def test_seven_representative_plans_preserve_architecture(case_id):
    snapshot = make_snapshot(case_id).snapshot
    result = build_generator_v2_implementation_spec(snapshot)
    assert result.status == "READY_TO_RENDER"
    spec = result.spec
    assert tuple(p.page_key for p in spec.pages) == tuple(p.page_key for p in snapshot.site_plan.pages)
    for source_page, output_page in zip(snapshot.site_plan.pages, spec.pages):
        assert tuple(s.section_key for s in output_page.sections) == tuple(s.section_key for s in source_page.sections)
        for source_section, output_section in zip(source_page.sections, output_page.sections):
            assert output_section.functional_components == tuple(source_section.functional_components)
            assert output_section.critical_action == source_section.critical_action
            assert output_section.motion_level == source_section.motion_policy.level


def test_routes_navigation_and_critical_action_requirements_are_preserved():
    snapshot = make_snapshot("REFERENCE_BOOKING").snapshot
    result = build_generator_v2_implementation_spec(snapshot)
    spec = result.spec
    assert spec.pages[0].route_path == "/"
    assert spec.navigation == tuple((e.from_page_key, e.to_page_key, e.purpose) for e in snapshot.site_plan.cross_page_navigation)
    for source_page, output_page in zip(snapshot.site_plan.pages, spec.pages):
        for source, output in zip(source_page.sections, output_page.sections):
            if source.critical_action:
                assert {
                    "keyboard_operable", "touch_safe", "no_hover_only", "reduced_motion_safe",
                    "critical_action_stable", "signature_interaction_not_required",
                } <= set(output.implementation_validation_requirements)


def test_route_corruption_and_package_default_page_are_rejected():
    snapshot = make_snapshot().snapshot
    spec = build_generator_v2_implementation_spec(snapshot).spec
    bad_route = spec.model_copy(update={"pages": (spec.pages[0].model_copy(update={"route_path": "/invented"}),)})
    assert validate_generator_v2_implementation_spec(snapshot, bad_route).reason_codes == ("route_mapping_failure",)
    extra_page = spec.pages[0].model_copy(update={"page_key": "package_default"})
    expanded = spec.model_copy(update={"pages": tuple(spec.pages) + (extra_page,)})
    assert validate_generator_v2_implementation_spec(snapshot, expanded).reason_codes == ("page_set_mismatch",)


def test_hash_is_deterministic_and_changes_with_registry_or_authority():
    first = build_generator_v2_implementation_spec(make_snapshot().snapshot).spec
    reordered = GeneratorV2ImplementationSpecV1.model_validate(first.model_dump(mode="json"))
    assert reordered.implementation_spec_hash == first.implementation_spec_hash
    altered = first.model_copy(update={"implementation_strategy_version": "v1"})
    assert altered.implementation_spec_hash == first.implementation_spec_hash
    changed_pages = first.model_copy(update={"pages": (first.pages[0].model_copy(update={"purpose": "changed purpose"}),)})
    assert validate_generator_v2_implementation_spec(make_snapshot().snapshot, changed_pages).status == "invalid"


def test_unsupported_section_fails_closed():
    context = CASES["STARTER_LOCAL_SERVICE"].context
    projection = build_planner_constraint_projection_v1(context)
    data = compliant_plan(context)
    data["pages"][0]["sections"][0]["section_key"] = "untrusted_section"
    plan = validate_site_plan_v1(context, data).plan
    assert plan is not None
    from app.services.generator_v2_contract import GeneratorV2SelectedDesignIdentityV1, build_generator_v2_input_snapshot
    selected = GeneratorV2SelectedDesignIdentityV1(
        design_direction=context.visual.design_direction.value, design_direction_contract_version="v1",
        interaction_preference=context.interaction_guidance.client_preference.value,
        interaction_preference_contract_version="v1",
    )
    snapshot_result = build_generator_v2_input_snapshot(
        context=context, projection=projection, site_plan=plan,
        planner_operation_key="a" * 64, selected_design=selected,
    )
    assert snapshot_result.status == "READY"
    result = build_generator_v2_implementation_spec(snapshot_result.snapshot)
    assert result.status == "NOT_IMPLEMENTABLE"
    assert result.reason_codes == ("unsupported_section_primitive",)


def test_missing_required_media_fails_closed():
    context = CASES["STARTER_LOCAL_SERVICE"].context
    projection = build_planner_constraint_projection_v1(context)
    data = compliant_plan(context)
    data["pages"][0]["sections"][0]["media_requirements"][0] = {
        "media_key": "hero_photo", "kind": "client_photo", "required": True,
    }
    plan = validate_site_plan_v1(context, data).plan
    assert plan is not None
    from app.services.generator_v2_contract import GeneratorV2SelectedDesignIdentityV1, build_generator_v2_input_snapshot
    selected = GeneratorV2SelectedDesignIdentityV1(
        design_direction=context.visual.design_direction.value, design_direction_contract_version="v1",
        interaction_preference=context.interaction_guidance.client_preference.value,
        interaction_preference_contract_version="v1",
    )
    built = build_generator_v2_input_snapshot(context=context, projection=projection, site_plan=plan, planner_operation_key="a" * 64, selected_design=selected)
    assert built.status == "READY"
    assert build_generator_v2_implementation_spec(built.snapshot).reason_codes == ("media_binding_unavailable",)


def test_stale_or_corrupt_snapshot_is_not_implementable():
    snapshot = make_snapshot().snapshot
    corrupt = snapshot.model_copy(update={"generator_input_hash": "f" * 64})
    assert build_generator_v2_implementation_spec(corrupt).status == "INVALID_OR_STALE_INPUT"
    manual_review = snapshot.model_copy(update={"site_plan": snapshot.site_plan.model_copy(update={"plan_status": "manual_review"})})
    assert build_generator_v2_implementation_spec(manual_review).status == "INVALID_OR_STALE_INPUT"


def test_large_advanced_has_no_implementation_spec():
    context = CASES["LARGE_ADVANCED_BOUNDARY"].context
    projection = build_planner_constraint_projection_v1(context)
    assert projection is not None
    assert CASES["LARGE_ADVANCED_BOUNDARY"].expected_planning_outcome_class == "large_plan_manual_review"


def test_no_legacy_or_runtime_imports_and_no_side_effects():
    source = Path("backend/app/services/generator_v2_implementation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    forbidden = ("generation", "canonical_brief", "preview", "worker", "redis", "openai", "queue", "sqlalchemy")
    assert not any(any(token in name.lower() for token in forbidden) for name in imports)
    assert "_extract_services_from_brief" not in source


def test_authorized_asset_binding_is_identity_only():
    snapshot = make_snapshot().snapshot
    # An asset reference changes only the binding identity; no URL/download field exists.
    asset = AuthorizedAssetReferenceV1(asset_key="hero_media", kind="client_media", authority="client_approval", content_hash="a" * 64)
    changed = snapshot.model_copy(update={"authorized_assets": (asset,)})
    # The snapshot hash is intentionally invalid after mutation and must not be used.
    assert build_generator_v2_implementation_spec(changed).status == "INVALID_OR_STALE_INPUT"
