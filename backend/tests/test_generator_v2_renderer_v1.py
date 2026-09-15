from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.generator_v2_implementation import build_generator_v2_implementation_spec
from app.services.generator_v2_contract import build_generator_v2_input_snapshot
from app.services.site_planner_constraint_projection import build_planner_constraint_projection_v1
from app.services.generator_v2_renderer import (
    GeneratorV2RenderedSiteArtifactV1,
    render_generator_v2_site,
    validate_generator_v2_rendered_site,
)
from test_generator_v2_contract_v1 import make_snapshot
from test_site_plan_v1 import candidate, page, section
from evals.site_planner.eval_cases import site_planner_eval_cases_v1


REPRESENTATIVE = (
    "STARTER_LOCAL_SERVICE", "BUSINESS_THREE_PAGE", "REFERENCE_PORTFOLIO_EXPRESSIVE",
    "REFERENCE_BOOKING", "REFERENCE_ECOMMERCE", "ADVANCED_ACCOUNT_PERSISTENT", "ADVANCED_SUBTLE",
)


def _artifact(case_id="STARTER_LOCAL_SERVICE"):
    implementation = build_generator_v2_implementation_spec(make_snapshot(case_id).snapshot)
    assert implementation.status == "READY_TO_RENDER"
    result = render_generator_v2_site(implementation.spec)
    assert result.status == "READY"
    return implementation.spec, result.artifact


def test_artifact_is_closed_and_frozen():
    _, artifact = _artifact()
    with pytest.raises(ValidationError):
        GeneratorV2RenderedSiteArtifactV1.model_validate({**artifact.model_dump(mode="json"), "raw_html": "<script>"})
    with pytest.raises(ValidationError):
        artifact.pages = ()


@pytest.mark.parametrize("case_id", REPRESENTATIVE)
def test_seven_representative_sites_render_and_validate(case_id):
    spec, artifact = _artifact(case_id)
    validation = validate_generator_v2_rendered_site(spec, artifact)
    assert validation.status == "valid"
    assert len(artifact.pages) == len(spec.pages)
    assert len(artifact.artifact_manifest) == len(spec.pages) + 2
    assert artifact.rendered_site_hash
    for source, rendered in zip(spec.pages, artifact.pages):
        assert rendered.page_key == source.page_key
        assert rendered.section_keys == tuple(section.section_key for section in source.sections)
        assert "data-siteformo-section" in rendered.html
        assert "data-siteformo-page" in rendered.html


def test_hash_is_deterministic_and_content_sensitive():
    spec, first = _artifact()
    second = render_generator_v2_site(spec).artifact
    assert first.rendered_site_hash == second.rendered_site_hash
    changed_page = first.pages[0].model_copy(update={"html": first.pages[0].html + " "})
    changed = first.model_copy(update={"pages": (changed_page,)})
    assert validate_generator_v2_rendered_site(spec, changed).status == "invalid"


def test_rendered_page_and_section_mutations_fail_validation():
    spec, artifact = _artifact()
    page = artifact.pages[0]
    extra_page = page.model_copy(update={"page_key": "unexpected"})
    assert validate_generator_v2_rendered_site(spec, artifact.model_copy(update={"pages": (page, extra_page)})).status == "invalid"
    missing = artifact.model_copy(update={"pages": ()})
    assert validate_generator_v2_rendered_site(spec, missing).status == "invalid"
    extra_section = page.model_copy(update={"section_keys": page.section_keys + ("fallback",)})
    assert validate_generator_v2_rendered_site(spec, artifact.model_copy(update={"pages": (extra_section,)})).status == "invalid"


def test_route_link_and_security_failures_are_deterministic():
    spec, artifact = _artifact()
    page = artifact.pages[0]
    broken = page.model_copy(update={"html": page.html + '<a href="#missing-page">Broken</a>'})
    assert validate_generator_v2_rendered_site(spec, artifact.model_copy(update={"pages": (broken,)})).status == "invalid"
    external = page.model_copy(update={"html": page.html + '<script src="https://evil.example/x.js"></script>'})
    assert validate_generator_v2_rendered_site(spec, artifact.model_copy(update={"pages": (external,)})).status == "invalid"
    unsafe = page.model_copy(update={"route": "/../escape"})
    assert validate_generator_v2_rendered_site(spec, artifact.model_copy(update={"pages": (unsafe,)})).reason_codes == ("unsafe_route",)


def test_multi_page_navigation_resolves_page_keys_to_rendered_routes():
    cases = {case.case_id: case for case in site_planner_eval_cases_v1()}
    context = cases["BUSINESS_THREE_PAGE"].context
    plan_payload = candidate(context, pages=[
        page("home", "home", sections=[section("hero", components=["navigation", "enquiry_form"])]),
        page("services", "services_or_offer", sections=[section("services", components=["navigation", "enquiry_form"])]),
        page("contact", "contact_or_enquiry", sections=[section("contact", components=["navigation", "contact_form", "enquiry_form"])]),
    ])
    plan_payload["cross_page_navigation"] = [
        {"from_page_key": "home", "to_page_key": "services", "purpose": "secondary"},
        {"from_page_key": "home", "to_page_key": "contact", "purpose": "conversion"},
        {"from_page_key": "services", "to_page_key": "home", "purpose": "secondary"},
        {"from_page_key": "services", "to_page_key": "contact", "purpose": "conversion"},
        {"from_page_key": "contact", "to_page_key": "home", "purpose": "secondary"},
        {"from_page_key": "contact", "to_page_key": "services", "purpose": "secondary"},
    ]
    plan_payload["pages"][0]["sections"][0]["primary_actions"][0]["target_page_key"] = "contact"
    validated = __import__("app.services.site_plan_validator", fromlist=["validate_site_plan_v1"]).validate_site_plan_v1(context, plan_payload)
    assert validated.status == "valid" and validated.plan is not None
    snapshot = build_generator_v2_input_snapshot(
        context=context,
        projection=build_planner_constraint_projection_v1(context),
        site_plan=validated.plan,
        planner_operation_key="a" * 64,
        selected_design=__import__("test_generator_v2_contract_v1", fromlist=["selected"]).selected(context),
    )
    implementation = build_generator_v2_implementation_spec(snapshot.snapshot)
    assert implementation.status == "READY_TO_RENDER" and implementation.spec is not None
    result = render_generator_v2_site(implementation.spec)
    assert result.status == "READY" and result.artifact is not None
    by_key = {page.page_key: page for page in result.artifact.pages}
    assert 'href="/services"' in by_key["home"].html
    assert 'href="/contact"' in by_key["home"].html
    assert 'href="/"' in by_key["services"].html
    assert 'href="/contact"' in by_key["services"].html
    assert 'href="/"' in by_key["contact"].html
    assert 'href="/services"' in by_key["contact"].html
    assert 'data-siteformo-target-page="services"' in by_key["home"].html
    assert 'data-siteformo-critical-action="enquire"' in by_key["home"].html
    assert '<a href="/contact" data-siteformo-target-page="contact"' in by_key["home"].html
    assert all('href="#' not in page.html for page in result.artifact.pages)


def test_cross_page_unknown_target_fails_closed():
    spec, _ = _artifact()
    page = spec.pages[0].model_copy(update={"navigation_targets": ("missing",)})
    broken = spec.model_copy(update={"pages": (page,)})
    result = render_generator_v2_site(broken)
    assert result.status == "NOT_RENDERABLE"
    assert result.reason_codes == ("broken_internal_link",)


def test_critical_markers_and_shared_foundation_are_present():
    spec, artifact = _artifact("REFERENCE_BOOKING")
    html = artifact.pages[0].html
    assert "data-siteformo-critical-action" in html
    assert "min-height:2.75rem" in artifact.artifact_manifest[0].content
    assert "prefers-reduced-motion" in artifact.artifact_manifest[0].content
    assert "assets/siteformo-v2.css" in artifact.shared_styles
    assert "assets/siteformo-v2.js" in artifact.shared_scripts


def test_no_raw_authority_or_runtime_imports():
    source = Path("backend/app/services/generator_v2_renderer.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    forbidden = ("openai", "redis", "worker", "queue", "sqlalchemy", "canonical_brief", "generation_service")
    assert not any(any(token in name.lower() for token in forbidden) for name in imports)
    for forbidden_text in ("brief_answers", "canonical_brief", "api_key", "Journey credential"):
        assert forbidden_text not in source


def test_large_advanced_has_no_upstream_implementation_input():
    # The staged-planning fixture has no validated single-plan implementation spec;
    # C2 therefore has no renderer input and cannot fabricate an artifact.
    assert render_generator_v2_site(None).status == "NOT_RENDERABLE"
