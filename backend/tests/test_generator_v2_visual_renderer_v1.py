from __future__ import annotations

import ast

import pytest

from app.services.generator_v2_visual import (
    VisualImplementationCandidateV1,
    accept_visual_implementation_candidate,
    build_default_visual_implementation_plan,
)
from app.services.generator_v2_visual_renderer import (
    render_generator_v2_visual_site,
    validate_generator_v2_visual_rendered_site,
)
from evals.generator_v2.fixtures import build_generator_v2_fixture, generator_v2_fixture_cases_v1


CASES = (
    "STARTER_LOCAL_SERVICE", "BUSINESS_THREE_PAGE", "REFERENCE_PORTFOLIO_EXPRESSIVE",
    "REFERENCE_BOOKING", "REFERENCE_ECOMMERCE", "ADVANCED_ACCOUNT_PERSISTENT", "ADVANCED_SUBTLE",
)


@pytest.mark.parametrize("case_id", CASES)
def test_visual_renderer_preserves_all_structure(case_id):
    fixture = build_generator_v2_fixture(case_id)
    plan = build_default_visual_implementation_plan(fixture.visual_input)
    result = render_generator_v2_visual_site(fixture.implementation, fixture.visual_input, plan)
    assert result.status == "READY", result.reason_codes
    artifact = result.artifact
    assert artifact is not None
    assert tuple(page.page_key for page in artifact.pages) == tuple(page.page_key for page in fixture.implementation.pages)
    for rendered, source in zip(artifact.pages, fixture.implementation.pages):
        assert rendered.route == source.route_path
        assert rendered.section_keys == tuple(section.section_key for section in source.sections)
        assert rendered.functional_components == tuple(dict.fromkeys(c for s in source.sections for c in s.functional_components))
        assert rendered.critical_action_keys == tuple(a.action_key for s in source.sections for a in s.primary_actions)
        assert 'data-siteformo-visual-renderer="v1"' in rendered.html
    assert validate_generator_v2_visual_rendered_site(fixture.implementation, fixture.visual_input, plan, artifact) == ()


def test_visual_renderer_rejects_hash_mismatch_without_fallback():
    fixture = build_generator_v2_fixture("BUSINESS_THREE_PAGE")
    plan = build_default_visual_implementation_plan(fixture.visual_input)
    bad = plan.model_copy(update={"visual_plan_hash": "0" * 64})
    result = render_generator_v2_visual_site(fixture.implementation, fixture.visual_input, bad)
    assert result.status == "NOT_RENDERABLE"
    assert "visual_validation_failed" in result.reason_codes


def test_visual_renderer_rejects_structural_fingerprint_mismatch():
    fixture = build_generator_v2_fixture("BUSINESS_THREE_PAGE")
    plan = build_default_visual_implementation_plan(fixture.visual_input)
    bad = plan.model_copy(update={"structural_fingerprint": "f" * 64})
    result = render_generator_v2_visual_site(fixture.implementation, fixture.visual_input, bad)
    assert result.status == "NOT_RENDERABLE"


def test_visual_css_is_deterministic_and_visual_choices_change_identity():
    fixture = build_generator_v2_fixture("BUSINESS_THREE_PAGE")
    plan = build_default_visual_implementation_plan(fixture.visual_input)
    first = render_generator_v2_visual_site(fixture.implementation, fixture.visual_input, plan)
    second = render_generator_v2_visual_site(fixture.implementation, fixture.visual_input, plan)
    assert first.artifact.rendered_site_hash == second.artifact.rendered_site_hash
    replacement = "compact" if plan.global_style.typography_scale != "compact" else "expressive"
    style = plan.global_style.model_copy(update={"typography_scale": replacement})
    changed_candidate = VisualImplementationCandidateV1.model_validate(
        plan.model_dump(mode="json", exclude={"visual_plan_hash"}) | {"global_style": style.model_dump(mode="json")}
    )
    changed = accept_visual_implementation_candidate(fixture.visual_input, changed_candidate)
    changed_result = render_generator_v2_visual_site(fixture.implementation, fixture.visual_input, changed)
    assert changed_result.status == "READY"
    assert changed_result.artifact.rendered_site_hash != first.artifact.rendered_site_hash


def test_large_advanced_has_no_renderable_single_plan():
    fixture = next(item for item in generator_v2_fixture_cases_v1() if item.case_id == "LARGE_ADVANCED_BOUNDARY")
    assert fixture.implementation is None
    assert fixture.readiness == "LARGE_PLAN_REQUIRES_STAGED_PLANNING"


def test_module_has_no_provider_or_runtime_side_effect_imports():
    tree = ast.parse(open("backend/app/services/generator_v2_visual_renderer.py", encoding="utf-8").read())
    imports = [node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert not any(token in " ".join(imports).lower() for token in ("openai", "redis", "worker", "queue", "database"))
