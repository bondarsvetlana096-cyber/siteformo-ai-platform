import pytest
from pydantic import ValidationError

from app.schemas.site_plan import SitePlanV1
from app.services.generator_v2_contract import (
    AuthorizedAssetReferenceV1,
    AuthorizedContentReferenceV1,
    GeneratorV2InputSnapshotV1,
    GeneratorV2JobPayloadV1,
    GeneratorV2SelectedDesignIdentityV1,
    build_generator_v2_input_snapshot,
    generator_v2_implementation_operation_key,
    validate_generator_v2_snapshot_identity,
)
from app.services.site_planner_constraint_projection import build_planner_constraint_projection_v1
from app.services.site_plan_validator import validate_site_plan_v1
from evals.site_planner.eval_cases import site_planner_eval_cases_v1
from test_site_plan_v1 import candidate
from test_site_planner_constraint_projection_v1 import compliant_plan


CASES = {case.case_id: case for case in site_planner_eval_cases_v1()}
REPRESENTATIVE = (
    "STARTER_LOCAL_SERVICE", "BUSINESS_THREE_PAGE", "REFERENCE_PORTFOLIO_EXPRESSIVE",
    "REFERENCE_BOOKING", "REFERENCE_ECOMMERCE", "ADVANCED_ACCOUNT_PERSISTENT", "ADVANCED_SUBTLE",
)


def selected(context):
    return GeneratorV2SelectedDesignIdentityV1(
        design_direction=context.visual.design_direction.value,
        design_direction_contract_version="v1",
        interaction_preference=context.interaction_guidance.client_preference.value,
        interaction_preference_contract_version="v1",
    )


def make_snapshot(case_id="STARTER_LOCAL_SERVICE"):
    context = CASES[case_id].context
    projection = build_planner_constraint_projection_v1(context)
    validated = validate_site_plan_v1(context, compliant_plan(context))
    assert validated.status == "valid" and validated.plan is not None
    return build_generator_v2_input_snapshot(
        context=context, projection=projection, site_plan=validated.plan,
        planner_operation_key="a" * 64, selected_design=selected(context),
    )


def test_closed_contracts_reject_authority_escape_hatches():
    result = make_snapshot()
    assert result.status == "READY"
    data = result.snapshot.model_dump(mode="json")
    data["prompt"] = "do something else"
    with pytest.raises(ValidationError):
        GeneratorV2InputSnapshotV1.model_validate(data)
    job = GeneratorV2JobPayloadV1(
        contract_version="v1", order_id="o", journey_project_id="p",
        generator_input_hash=result.snapshot.generator_input_hash,
        site_plan_hash=result.snapshot.site_plan_hash,
        generation_context_hash=result.snapshot.generation_context_hash,
        constraint_projection_hash=result.snapshot.constraint_projection_hash,
        implementation_operation_key="b" * 64,
        generation_context_contract_version="v1", constraint_projection_contract_version="v1",
        site_plan_contract_version="v1", implementation_strategy_version="v1",
    )
    hostile = job.model_dump(mode="json")
    hostile["canonical_brief"] = {}
    with pytest.raises(ValidationError):
        GeneratorV2JobPayloadV1.model_validate(hostile)


def test_builder_requires_validated_site_plan_and_checks_projection():
    context = CASES["STARTER_LOCAL_SERVICE"].context
    projection = build_planner_constraint_projection_v1(context)
    invalid = compliant_plan(context)
    invalid["pages"][0]["sections"][0]["functional_components"] = ["checkout"]
    invalid["functional_component_inventory"] = ["checkout"]
    parsed = SitePlanV1.model_validate(invalid)
    assert build_generator_v2_input_snapshot(
        context=context, projection=projection, site_plan=parsed,
        planner_operation_key="a" * 64, selected_design=selected(context),
    ).reason_codes == ("site_plan_invalid",)
    wrong = projection.model_copy(update={"generation_context_hash": "f" * 64})
    assert build_generator_v2_input_snapshot(
        context=context, projection=wrong, site_plan=validate_site_plan_v1(context, compliant_plan(context)).plan,
        planner_operation_key="a" * 64, selected_design=selected(context),
    ).status == "STALE_OR_MISMATCH"


@pytest.mark.parametrize("case_id", REPRESENTATIVE)
def test_representative_validated_plans_build_ready(case_id):
    result = make_snapshot(case_id)
    assert result.status == "READY"
    assert result.snapshot.site_plan.plan_status == "validated"


def test_large_advanced_has_no_valid_single_plan_snapshot():
    context = CASES["LARGE_ADVANCED_BOUNDARY"].context
    projection = build_planner_constraint_projection_v1(context)
    # The fixture is only a context; V1's planner gate rejects it before a
    # single-call plan exists. A missing plan must never be synthesized here.
    result = build_generator_v2_input_snapshot(
        context=context, projection=projection, site_plan=None,
        planner_operation_key="a" * 64, selected_design=selected(context),
    )
    assert result.status == "NOT_READY" and result.reason_codes == ("site_plan_invalid",)
    assert CASES["LARGE_ADVANCED_BOUNDARY"].expected_planning_outcome_class == "large_plan_manual_review"


def test_hash_is_order_stable_and_authority_sensitive():
    result = make_snapshot()
    snapshot = result.snapshot
    reordered = snapshot.model_dump(mode="json")
    reordered["selected_design"] = {"interaction_preference_contract_version": "v1", "interaction_preference": snapshot.selected_design.interaction_preference, "design_direction_contract_version": "v1", "design_direction": snapshot.selected_design.design_direction, "visual_reference_hash": None}
    reordered["generator_input_hash"] = snapshot.generator_input_hash
    assert GeneratorV2InputSnapshotV1.model_validate(reordered).generator_input_hash == snapshot.generator_input_hash
    assert generator_v2_implementation_operation_key(snapshot) == generator_v2_implementation_operation_key(snapshot)

    context = CASES["STARTER_LOCAL_SERVICE"].context
    projection = build_planner_constraint_projection_v1(context)
    alternate_design = selected(context).model_copy(update={"design_direction": "dark-contrast"})
    changed = build_generator_v2_input_snapshot(
        context=context, projection=projection, site_plan=snapshot.site_plan,
        planner_operation_key=snapshot.planner_operation_key, selected_design=alternate_design,
    )
    assert changed.status == "READY"
    assert changed.snapshot.generator_input_hash != snapshot.generator_input_hash
    assert generator_v2_implementation_operation_key(changed.snapshot) != generator_v2_implementation_operation_key(snapshot)


def test_stale_identity_never_replans():
    snapshot = make_snapshot().snapshot
    context = CASES["STARTER_LOCAL_SERVICE"].context
    assert validate_generator_v2_snapshot_identity(
        snapshot, generation_context_hash="f" * 64,
        constraint_projection_hash=snapshot.constraint_projection_hash,
        site_plan_hash=snapshot.site_plan_hash, planner_operation_key=snapshot.planner_operation_key,
        selected_design=snapshot.selected_design,
    ).status == "STALE_OR_MISMATCH"
    assert validate_generator_v2_snapshot_identity(
        snapshot, generation_context_hash=context.fingerprints.context_hash,
        constraint_projection_hash=snapshot.constraint_projection_hash,
        site_plan_hash=snapshot.site_plan_hash, planner_operation_key=snapshot.planner_operation_key,
        selected_design=snapshot.selected_design,
    ).status == "READY"
    changed_design = snapshot.selected_design.model_copy(update={"interaction_preference": "subtle"})
    assert validate_generator_v2_snapshot_identity(
        snapshot, generation_context_hash=snapshot.generation_context_hash,
        constraint_projection_hash=snapshot.constraint_projection_hash,
        site_plan_hash=snapshot.site_plan_hash, planner_operation_key=snapshot.planner_operation_key,
        selected_design=changed_design,
    ).reason_codes == ("interaction_identity_mismatch",)


def test_authorized_asset_and_content_boundaries_are_closed_and_hash_sensitive():
    context = CASES["STARTER_LOCAL_SERVICE"].context
    projection = build_planner_constraint_projection_v1(context)
    plan = validate_site_plan_v1(context, compliant_plan(context)).plan
    base = dict(context=context, projection=projection, site_plan=plan, planner_operation_key="a" * 64, selected_design=selected(context))
    first = build_generator_v2_input_snapshot(**base)
    asset = AuthorizedAssetReferenceV1(asset_key="hero_image", kind="client_media", authority="client_approval", content_hash="1" * 64)
    content = AuthorizedContentReferenceV1(content_key="primary_goal", kind="confirmed_fact", source_key="business.primary_goal", content_hash="2" * 64)
    second = build_generator_v2_input_snapshot(**base, authorized_assets=(asset,), authorized_content=(content,))
    assert first.snapshot.generator_input_hash != second.snapshot.generator_input_hash
    with pytest.raises(ValidationError):
        AuthorizedAssetReferenceV1.model_validate({"asset_key": "x", "kind": "client_media", "authority": "client_approval", "url": "https://evil.example"})


def test_no_legacy_data_is_accepted_and_serialized_snapshot_is_minimal():
    snapshot = make_snapshot().snapshot
    dumped = snapshot.model_dump_json()
    forbidden = ("brief_answers", "extended_brief", "canonical_brief", "recommended_tier", "price", "email", "phone", "prompt", "api_key")
    assert not any(value in dumped for value in forbidden)
    assert "site_plan" in dumped and "generation_context_hash" in dumped


def test_job_payload_is_identity_only_and_operation_key_is_deterministic():
    snapshot = make_snapshot().snapshot
    key = generator_v2_implementation_operation_key(snapshot)
    assert key == generator_v2_implementation_operation_key(snapshot)
    payload = GeneratorV2JobPayloadV1(
        contract_version="v1", order_id=snapshot.order_id, journey_project_id=snapshot.journey_project_id,
        generator_input_hash=snapshot.generator_input_hash, site_plan_hash=snapshot.site_plan_hash,
        generation_context_hash=snapshot.generation_context_hash, constraint_projection_hash=snapshot.constraint_projection_hash,
        implementation_operation_key=key, generation_context_contract_version="v1",
        constraint_projection_contract_version="v1", site_plan_contract_version="v1",
        implementation_strategy_version="v1",
    )
    assert "canonical_brief" not in payload.model_dump()
