from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.generator_v2_implementation import build_generator_v2_implementation_spec
from app.services.generator_v2_visual import (
    VisualImplementationCandidateV1,
    VisualImplementationPlanV1,
    VisualImplementationProviderRequestV1,
    build_default_visual_implementation_plan,
    build_visual_implementation_input,
    build_visual_implementation_provider_request,
    validate_visual_implementation_plan_v1,
    structural_fingerprint,
    derive_visual_plan_hash,
)
from test_generator_v2_contract_v1 import make_snapshot


REPRESENTATIVE = (
    "STARTER_LOCAL_SERVICE", "BUSINESS_THREE_PAGE", "REFERENCE_PORTFOLIO_EXPRESSIVE",
    "REFERENCE_BOOKING", "REFERENCE_ECOMMERCE", "ADVANCED_ACCOUNT_PERSISTENT", "ADVANCED_SUBTLE",
)


def _visual(case_id="STARTER_LOCAL_SERVICE"):
    snapshot = make_snapshot(case_id).snapshot
    implementation = build_generator_v2_implementation_spec(snapshot)
    assert implementation.status == "READY_TO_RENDER" and implementation.spec is not None
    visual_input = build_visual_implementation_input(snapshot, implementation.spec)
    assert visual_input.status == "READY" and visual_input.input is not None
    plan = build_default_visual_implementation_plan(visual_input.input)
    return snapshot, implementation.spec, visual_input.input, plan


def test_visual_contracts_are_closed_and_frozen():
    _, _, visual_input, plan = _visual()
    with pytest.raises(ValidationError):
        VisualImplementationPlanV1.model_validate({**plan.model_dump(mode="json"), "html": "<script>"})
    with pytest.raises(ValidationError):
        VisualImplementationProviderRequestV1.model_validate({
            **build_visual_implementation_provider_request(visual_input).model_dump(mode="json"),
            "raw_css": "body{}",
        })
    with pytest.raises(ValidationError):
        plan.global_style = plan.global_style


@pytest.mark.parametrize("case_id", REPRESENTATIVE)
def test_seven_representative_visual_plans_validate(case_id):
    _, _, visual_input, plan = _visual(case_id)
    result = validate_visual_implementation_plan_v1(visual_input, plan)
    assert result.status == "VALID" and not result.reason_codes
    assert tuple(page.page_key for page in plan.pages) == tuple(page.page_key for page in visual_input.pages)


def test_structural_fingerprint_is_stable_and_visual_choices_do_not_change_it():
    _, spec, visual_input, plan = _visual()
    assert visual_input.structural_fingerprint == structural_fingerprint(spec)
    changed_visual = plan.model_copy(update={"design_direction": "dark-contrast"})
    assert changed_visual.structural_fingerprint == plan.structural_fingerprint
    assert validate_visual_implementation_plan_v1(visual_input, changed_visual).reason_codes == ("input_mismatch",)
    source_page = spec.pages[0]
    source_section = source_page.sections[0]
    changed_section = source_section.model_copy(update={"section_key": "changed_section"})
    changed_page = source_page.model_copy(update={"sections": (changed_section,)})
    changed_spec = spec.model_copy(update={"pages": (changed_page,) + spec.pages[1:]})
    assert structural_fingerprint(changed_spec) != structural_fingerprint(spec)


def test_plan_cannot_add_remove_or_reorder_pages_or_sections():
    _, _, visual_input, plan = _visual("BUSINESS_THREE_PAGE")
    extra = plan.model_copy(update={"pages": plan.pages + (plan.pages[0],)})
    assert validate_visual_implementation_plan_v1(visual_input, extra).reason_codes == ("page_set_mismatch",)
    removed = plan.model_copy(update={"pages": plan.pages[:-1]})
    assert validate_visual_implementation_plan_v1(visual_input, removed).reason_codes == ("invalid_visual_token",)
    first = plan.pages[0]
    renamed = first.sections[0].model_copy(update={"section_key": "renamed_section"})
    bad_page = first.model_copy(update={"sections": (renamed,)})
    bad = plan.model_copy(update={"pages": (bad_page,) + plan.pages[1:]})
    assert validate_visual_implementation_plan_v1(visual_input, bad).reason_codes == ("section_inventory_mismatch",)


def test_structural_functionality_navigation_and_critical_actions_are_immutable():
    _, _, visual_input, plan = _visual("REFERENCE_ECOMMERCE")
    section = plan.pages[0].sections[0]
    changed = section.model_copy(update={"critical_action_keys": section.critical_action_keys + ("checkout",)})
    bad = plan.model_copy(update={"pages": (plan.pages[0].model_copy(update={"sections": (changed,)}),)})
    assert validate_visual_implementation_plan_v1(visual_input, bad).reason_codes == ("critical_action_safety_mismatch",)
    with pytest.raises(ValidationError):
        type(section).model_validate({**section.model_dump(mode="json"), "functional_components": ["checkout"]})


def test_critical_action_cannot_be_hidden_or_motion_gated():
    _, _, visual_input, plan = _visual()
    section = plan.pages[0].sections[0].model_copy(update={"interaction_realization": "rich_noncritical"})
    bad = plan.model_copy(update={"pages": (plan.pages[0].model_copy(update={"sections": (section,)}),)})
    assert validate_visual_implementation_plan_v1(visual_input, bad).reason_codes == ("critical_action_safety_mismatch",)
    section = plan.pages[0].sections[0].model_copy(update={"responsive": plan.pages[0].sections[0].responsive.model_copy(update={"preserves_functionality": False})})
    bad = plan.model_copy(update={"pages": (plan.pages[0].model_copy(update={"sections": (section,)}),)})
    assert validate_visual_implementation_plan_v1(visual_input, bad).reason_codes == ("invalid_visual_token",)


def test_authorized_media_and_content_boundaries():
    _, _, visual_input, plan = _visual()
    section = plan.pages[0].sections[0].model_copy(update={"authorized_asset_keys": ("invented_asset",)})
    bad = plan.model_copy(update={"pages": (plan.pages[0].model_copy(update={"sections": (section,)}),)})
    assert validate_visual_implementation_plan_v1(visual_input, bad).reason_codes == ("unauthorized_asset",)
    section = plan.pages[0].sections[0].model_copy(update={"content_reference_keys": ("invented_fact",)})
    bad = plan.model_copy(update={"pages": (plan.pages[0].model_copy(update={"sections": (section,)}),)})
    assert validate_visual_implementation_plan_v1(visual_input, bad).reason_codes == ("unauthorized_content",)


def test_unknown_composition_and_external_authority_fail_closed():
    _, _, visual_input, plan = _visual()
    section = plan.pages[0].sections[0]
    with pytest.raises(ValidationError):
        type(section).model_validate({**section.model_dump(mode="json"), "composition": "arbitrary_template"})
    with pytest.raises(ValidationError):
        type(plan).model_validate({**plan.model_dump(mode="json"), "external_url": "https://evil.example"})


def test_input_rejects_invalid_implementation_authority():
    snapshot, spec, _, _ = _visual()
    invalid = spec.model_copy(update={"pages": ()})
    result = build_visual_implementation_input(snapshot, invalid)
    assert result.status == "INVALID_OR_STALE_INPUT"
    assert result.reason_codes == ("implementation_spec_invalid",)


def test_visual_plan_hash_detects_mutation_and_is_order_independent():
    _, _, visual_input, plan = _visual()
    reordered = plan.model_validate(dict(reversed(list(plan.model_dump(mode="json").items()))))
    assert reordered.visual_plan_hash == plan.visual_plan_hash
    section = plan.pages[0].sections[0].model_copy(update={"density": "airy"})
    changed = plan.model_copy(update={"pages": (plan.pages[0].model_copy(update={"sections": (section,)}),)})
    assert validate_visual_implementation_plan_v1(visual_input, changed).reason_codes == ("visual_plan_hash_mismatch",)


def test_visual_plan_hash_is_server_derived_and_candidate_cannot_supply_identity():
    _, _, visual_input, plan = _visual()
    candidate_payload = plan.model_dump(mode="json")
    candidate_payload.pop("visual_plan_hash")
    candidate = VisualImplementationCandidateV1.model_validate(candidate_payload)
    assert derive_visual_plan_hash(candidate) == plan.visual_plan_hash
    with pytest.raises(ValidationError):
        VisualImplementationCandidateV1.model_validate({**candidate_payload, "visual_plan_hash": "0" * 64})


def test_provider_request_is_closed_and_contains_only_visual_boundary():
    _, _, visual_input, _ = _visual()
    request = build_visual_implementation_provider_request(visual_input)
    assert request.input.structural_fingerprint == visual_input.structural_fingerprint
    assert "architecture" in request.forbidden_authority
    assert "raw_html" in request.forbidden_authority
    assert "prompt" in request.forbidden_authority
    serialized = str(request.model_dump(mode="json"))
    assert not any(value in serialized for value in ("Canonical Brief", "preview_dna", "payment"))


def test_all_direction_and_preference_envelopes_are_representable():
    _, _, visual_input, plan = _visual()
    for direction in ("clean-modern", "premium-business", "bold-startup", "luxury-elite", "tech-minimal", "creative-studio", "nordic-soft", "dark-contrast"):
        candidate = plan.model_copy(update={"design_direction": direction})
        assert candidate.global_style.color_roles
    for preference in ("subtle", "recommended", "more_expressive"):
        candidate = plan.model_copy(update={"interaction_preference": preference})
        assert candidate.interaction_preference in {"subtle", "recommended", "more_expressive"}


def test_no_runtime_or_legacy_imports_and_no_side_effect_boundary():
    source = Path("backend/app/services/generator_v2_visual.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    forbidden = ("openai", "redis", "worker", "queue", "sqlalchemy", "filesystem", "requests", "httpx")
    assert not any(any(token in name.lower() for token in forbidden) for name in imports)
    for forbidden_text in ("brief_answers", "canonical_brief", "preview_dna", "api_key", "raw_questionnaire", "<script>"):
        assert forbidden_text not in source
