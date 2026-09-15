from __future__ import annotations

import ast
from pathlib import Path

from evals.generator_v2.fixtures import build_generator_v2_fixture
from evals.visual_implementation.eval_harness import build_business_visual_input


REPRESENTATIVE = (
    "STARTER_LOCAL_SERVICE", "BUSINESS_THREE_PAGE", "REFERENCE_PORTFOLIO_EXPRESSIVE",
    "REFERENCE_BOOKING", "REFERENCE_ECOMMERCE", "ADVANCED_ACCOUNT_PERSISTENT", "ADVANCED_SUBTLE",
)


def test_canonical_fixture_chain_is_typed_and_synthetic():
    fixture = build_generator_v2_fixture("BUSINESS_THREE_PAGE")
    assert fixture.synthetic is True
    assert fixture.readiness == "READY"
    assert fixture.context.fingerprints.context_hash == fixture.snapshot.generation_context_hash
    assert fixture.snapshot.site_plan_hash == fixture.site_plan.site_plan_hash
    assert fixture.implementation is not None and fixture.visual_input is not None


def test_all_representative_cases_round_trip_and_large_boundary_stays_blocked():
    for case_id in REPRESENTATIVE:
        fixture = build_generator_v2_fixture(case_id)
        assert fixture.readiness == "READY"
        assert fixture.snapshot is not None and fixture.implementation is not None
    large = build_generator_v2_fixture("LARGE_ADVANCED_BOUNDARY")
    assert large.readiness == "LARGE_PLAN_REQUIRES_STAGED_PLANNING"
    assert large.snapshot is None and large.implementation is None


def test_visual_harness_build_works_without_test_module_imports():
    visual_input = build_business_visual_input()
    assert visual_input.structural_fingerprint == build_generator_v2_fixture("BUSINESS_THREE_PAGE").visual_input.structural_fingerprint
    source = Path("backend/evals/visual_implementation/eval_harness.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert not any(name == "tests" or name.startswith("tests.") or name.startswith("test_") for name in imported)
    assert "test_generator_v2_contract_v1" not in source


def test_fixture_package_has_no_side_effect_or_secret_dependencies():
    source = Path("backend/evals/generator_v2/fixtures.py").read_text(encoding="utf-8")
    forbidden = ("os.environ", "requests", "httpx", "redis", "sqlalchemy", "OPENAI_API_KEY", "Canonical Brief")
    assert not any(value in source for value in forbidden)
