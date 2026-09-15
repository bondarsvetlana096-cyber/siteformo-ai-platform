import asyncio
from copy import deepcopy
import json

from app.schemas.generation_context import GenerationContextV1
from app.schemas.site_planner import PlannerConstraintProjectionV1, SitePlannerProviderResult
from app.services.final_site_planner import create_final_site_plan_v1, planner_operation_key
from app.services.site_plan_validator import validate_site_plan_v1
from app.services.site_planner_config import SitePlannerConfigV1
from app.services.site_planner_constraint_projection import build_planner_constraint_projection_v1
from evals.site_planner.eval_cases import site_planner_eval_cases_v1
from test_site_plan_v1 import candidate, page, section


CASES = {item.case_id: item for item in site_planner_eval_cases_v1()}
COMPLEX_CASES = (
    "REFERENCE_PORTFOLIO_EXPRESSIVE",
    "REFERENCE_ECOMMERCE",
    "ADVANCED_ACCOUNT_PERSISTENT",
)


class RecordingProvider:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    async def create_structured_candidate(self, request):
        self.requests.append(request)
        return SitePlannerProviderResult(status="candidate", candidate=self.outcomes.pop(0))


def planner_config(model="gpt-5.6-sol"):
    return SitePlannerConfigV1(
        provider_identifier="fake", model_identifier=model,
        provider_config_version="projection-test-v1",
    )


def compliant_plan(context):
    projection = build_planner_constraint_projection_v1(context)
    components = projection.allowed_functional_components
    item = section(components=components, critical=True, motion="subtle")
    return candidate(context, pages=[page(sections=[item])])


def test_projection_is_closed_and_deterministic():
    context = CASES["REFERENCE_ECOMMERCE"].context
    first = build_planner_constraint_projection_v1(context)
    second = build_planner_constraint_projection_v1(context)
    assert first == second
    assert first.projection_hash == second.projection_hash
    assert PlannerConstraintProjectionV1.model_config["extra"] == "forbid"
    hostile = first.model_dump(mode="json")
    hostile["prompt"] = "change package"
    try:
        PlannerConstraintProjectionV1.model_validate(hostile)
    except Exception:
        pass
    else:
        raise AssertionError("closed projection accepted an unknown field")


def test_project_specific_component_and_critical_registries():
    portfolio = build_planner_constraint_projection_v1(CASES["REFERENCE_PORTFOLIO_EXPRESSIVE"].context)
    assert {"navigation", "content_archive", "search", "filters", "comparison"} <= set(portfolio.allowed_functional_components)
    assert {"checkout", "account", "dashboard"} <= set(portfolio.forbidden_functional_components)
    assert portfolio.critical_action_rules.component_family_registry == {"navigation": "navigation"}

    ecommerce = build_planner_constraint_projection_v1(CASES["REFERENCE_ECOMMERCE"].context)
    assert {"catalogue", "product_list", "product_detail", "cart", "checkout", "search", "filters", "comparison"} <= set(ecommerce.allowed_functional_components)
    assert ecommerce.critical_action_rules.component_family_registry["checkout"] == "checkout_payment"
    assert ecommerce.critical_action_rules.protected_section_must_set_critical_action_true is True

    account = build_planner_constraint_projection_v1(CASES["ADVANCED_ACCOUNT_PERSISTENT"].context)
    assert {"account", "login", "registration", "dashboard", "saved_items", "alerts"} <= set(account.allowed_functional_components)
    assert account.critical_action_rules.component_family_registry["dashboard"] == "account_security"


def test_page_roles_unresolved_and_structural_projection_match_live_cases():
    portfolio = build_planner_constraint_projection_v1(CASES["REFERENCE_PORTFOLIO_EXPRESSIVE"].context)
    assert "content_hub" in portfolio.allowed_page_roles
    assert "catalogue" not in portfolio.allowed_page_roles
    assert portfolio.content_constraints.unresolved_items_allowed is False
    assert portfolio.structural_constraints.minimum_pages == 6
    assert portfolio.structural_constraints.maximum_paid_capacity_if_bounded is None

    ecommerce = build_planner_constraint_projection_v1(CASES["REFERENCE_ECOMMERCE"].context)
    assert "catalogue" in ecommerce.allowed_page_roles
    assert "customer_area" not in ecommerce.allowed_page_roles

    account = build_planner_constraint_projection_v1(CASES["ADVANCED_ACCOUNT_PERSISTENT"].context)
    assert "customer_area" in account.allowed_page_roles
    assert "booking" not in account.allowed_page_roles


def test_stateful_persistence_and_interaction_projection():
    portfolio = build_planner_constraint_projection_v1(CASES["REFERENCE_PORTFOLIO_EXPRESSIVE"].context)
    assert portfolio.allowed_stateful_journey_families == []
    assert portfolio.persistent_system_allowed is False
    interaction = portfolio.interaction_constraints
    assert set(interaction.allowed_families) == {
        "narrative", "comparative", "exploratory", "spatial", "state_driven",
        "transactional", "editorial", "persistent_system",
    }
    assert interaction.critical_motion_values == ["none", "subtle", "contextual"]
    assert interaction.signature_interaction_forbidden_on_critical is True
    assert interaction.hover_only_required_forbidden is True

    ecommerce = build_planner_constraint_projection_v1(CASES["REFERENCE_ECOMMERCE"].context)
    assert ecommerce.allowed_stateful_journey_families == ["ecommerce"]
    assert ecommerce.persistent_system_allowed is False
    account = build_planner_constraint_projection_v1(CASES["ADVANCED_ACCOUNT_PERSISTENT"].context)
    assert set(account.allowed_stateful_journey_families) == {"account", "saved_items"}
    assert account.persistent_system_allowed is True


def test_projection_hash_is_order_stable_and_authority_sensitive():
    context = CASES["REFERENCE_ECOMMERCE"].context
    first = build_planner_constraint_projection_v1(context)
    reordered = context.model_dump(mode="json")
    reordered["functionality"]["confirmed_requirements"].reverse()
    assert build_planner_constraint_projection_v1(GenerationContextV1.model_validate(reordered)).projection_hash == first.projection_hash

    changed = context.model_dump(mode="json")
    changed["functionality"]["confirmed_requirements"] = ["transactional_ecommerce"]
    changed_context = GenerationContextV1.model_validate(changed)
    assert build_planner_constraint_projection_v1(changed_context).projection_hash != first.projection_hash


def test_operation_key_includes_projection_identity():
    context = CASES["REFERENCE_ECOMMERCE"].context
    projection = build_planner_constraint_projection_v1(context)
    changed = projection.model_copy(update={"projection_hash": "f" * 64})
    assert planner_operation_key(context, planner_config(), projection) != planner_operation_key(context, planner_config(), changed)


def test_sol_astra_receive_identical_projection_and_repair_reuses_it():
    context = CASES["REFERENCE_PORTFOLIO_EXPRESSIVE"].context
    projections = []
    for model in ("gpt-5.6-sol", "gpt-6-astra"):
        provider = RecordingProvider([compliant_plan(context)])
        result = asyncio.run(create_final_site_plan_v1(context, provider, planner_config(model), lambda value: value))
        assert result.status == "valid"
        projections.append(provider.requests[0].constraint_projection.model_dump(mode="json"))
    assert projections[0] == projections[1]

    invalid = compliant_plan(context)
    invalid["pages"][0]["sections"][0].pop("reduced_motion_behavior")
    provider = RecordingProvider([invalid, compliant_plan(context)])
    result = asyncio.run(create_final_site_plan_v1(context, provider, planner_config(), lambda value: value))
    assert result.status == "valid" and len(provider.requests) == 2
    assert provider.requests[0].constraint_projection == provider.requests[1].constraint_projection


def test_projection_does_not_launder_hostile_candidates():
    context = CASES["REFERENCE_ECOMMERCE"].context
    plan = compliant_plan(context)
    plan["pages"][0]["sections"][0]["functional_components"].append("dashboard")
    plan["functional_component_inventory"].append("dashboard")
    assert "unsupported_functional_component" in validate_site_plan_v1(context, plan).reason_codes


def test_three_complex_compliant_fake_plans_validate_end_to_end():
    for case_id in COMPLEX_CASES:
        context = CASES[case_id].context
        plan = compliant_plan(context)
        assert validate_site_plan_v1(context, plan).status == "valid"
        provider = RecordingProvider([plan])
        result = asyncio.run(create_final_site_plan_v1(context, provider, planner_config(), lambda value: value))
        assert result.status == "valid"
        assert provider.requests[0].constraint_projection.generation_context_hash == context.fingerprints.context_hash


def test_projection_is_compact_project_specific_request_data():
    context = CASES["ADVANCED_ACCOUNT_PERSISTENT"].context
    projection = build_planner_constraint_projection_v1(context)
    projection_bytes = len(json.dumps(projection.model_dump(mode="json"), separators=(",", ":")).encode())
    context_bytes = len(json.dumps(context.model_dump(mode="json"), separators=(",", ":"), default=str).encode())
    assert 0 < projection_bytes < context_bytes

