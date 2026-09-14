import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.journey.models import JourneyBase, SiteFormoJourneyProject
from app.main import app
from app.models.order import Order, OrderStatus
from app.schemas.order import Q2V2Payload
from app.services.q2_scope_planner import plan_preliminary_architecture, qualify_scope
from app.services.q2_architecture_planner import architecture_input


def factory():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    JourneyBase.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def override(maker):
    def dependency():
        with maker() as db:
            yield db
    return dependency


def q1_payload():
    return {
        "flow_version": "q1_v2", "schema_version": 2, "project_class_intent": "business_site",
        "preferred_contact": {"channel": "email", "value": "owner@example.test", "normalized_value": "owner@example.test", "purpose": "operational_communication", "display_on_generated_website": False},
        "existing_website": {"has_existing_website": True, "url": "https://example.test", "analysis": {"source": "existing_website", "status": "unconfirmed", "data": {"business_hints": ["electrical services"], "function_hints": ["booking"], "complexity_hints": ["rich navigation"]}}},
        "examples_context": {"selected_example_id": "business1", "visual_dna": {"tone": "clean"}},
        "package_browsing_context": {"package_key": "advanced", "source": "example"},
        "package_qualification": {"status": "unqualified", "candidate_package": None, "source": None},
        "assistant_context": {"current_step_id": "q1_complete", "enabled": False},
    }


def q2_payload(starting="advanced", functions=None, options=None, legal=True, package_confirmed=True):
    return {
        "flow_version": "q2_v2", "schema_version": 2,
        "business_identity": {"status": "business_name", "name": "Example Electric"},
        "business_activity": {"niche": "Electrician", "broad_model": "local_service", "other_clarification": None},
        "operating_model": "travel_to_customers",
        "location": {"public_address": None, "service_area": "Dublin", "multiple_locations_summary": None},
        "audience": "individuals", "primary_goal": "enquiries",
        "functions": functions if functions is not None else [{"key": "contact_enquiry", "confirmed": True}],
        "trust_materials": ["reviews"],
        "media": {"photos": "client_provides", "client_photos_timing": "later", "video": "none"},
        "social_presence": {"expected_channels": ["none"], "other_platform": None, "links": "pending", "presentation": "flexible"},
        "logo": {"choice": "none", "price_eur": 0, "scope": {}},
        "delivery_context": {"hosting_status": "not_sure", "hosting_help_included": True, "domain_and_hosting_client_owned": True, "installation_selected": False, "installation_price_eur": 0},
        "paid_structural_options": options or [],
        "package_context": {"starting_package": starting, "source": "package_browsing_context", "rule_version": "ireland_accepted_v1"},
        "analysis_hints": {"source": "existing_website", "status": "unconfirmed", "business_hints": ["electric services"], "function_hints": [], "complexity_hints": []},
        "assistant_context": {"current_step_id": "q2_package_confirmation", "enabled": False},
        "legal_gate_confirmed": legal, "package_and_addons_confirmed": package_confirmed,
    }


def test_scope_readiness_excludes_legacy_q2_legal_boolean():
    false_payload = Q2V2Payload.model_validate(q2_payload(starting="starter", legal=False))
    assert qualify_scope(false_payload)["checkout_ready"] is True

    absent_payload = q2_payload(starting="starter")
    absent_payload.pop("legal_gate_confirmed")
    validated = Q2V2Payload.model_validate(absent_payload)
    assert validated.legal_gate_confirmed is False
    assert qualify_scope(validated)["checkout_ready"] is True


def create_q1(client, credential):
    headers = {"Origin": "https://ie.siteformo.com", JOURNEY_CREDENTIAL_HEADER: credential}
    order_id = client.post("/api/orders/q1/project", headers=headers, json={}).json()["order_id"]
    assert client.patch(f"/api/orders/{order_id}/q1", headers=headers, json=q1_payload()).status_code == 200
    return order_id, headers


def test_same_journey_same_order_q1_preserved_and_idempotent():
    maker = factory()
    app.dependency_overrides[get_db] = override(maker)
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        credential = client.post("/api/journey/session", headers={"Origin": "https://ie.siteformo.com"}).json()["credential"]
        order_id, headers = create_q1(client, credential)
        # A frontend cannot replace the Q1 browsing context by posting another starting tier.
        first = client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload(starting="starter"))
        repeat = client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload(starting="starter"))
        assert first.status_code == 200 and first.json()["idempotent"] is False
        assert repeat.status_code == 200 and repeat.json()["idempotent"] is True
        assert first.json()["order_id"] == order_id == repeat.json()["order_id"]
        assert first.json()["scope_qualification"]["starting_package"] == "advanced"
        with maker() as db:
            order = db.get(Order, order_id)
            assert order.status == OrderStatus.DRAFT
            assert order.brief_answers["q1_v2"]["project_class_intent"] == "business_site"
            assert order.extended_brief["q2_v2"]["flow_version"] == "q2_v2"
            assert order.extended_brief["q2_v2"]["analysis_hints"]["business_hints"] == ["electrical services"]
            assert order.extended_brief["q2_v2"]["analysis_hints"]["status"] == "unconfirmed"
            assert order.extended_brief["legacy_compatibility"]["pages"][0]["source"] == "pre_stripe_scope_planner"
            assert order.extended_brief["legacy_compatibility"]["visual_dna"] == {"tone": "clean"}
            assert len(db.execute(select(Order)).scalars().all()) == 1
            assert len(db.execute(select(SiteFormoJourneyProject)).scalars().all()) == 1
    finally:
        app.dependency_overrides.clear()


def test_wrong_journey_non_draft_and_missing_q1_are_rejected():
    maker = factory()
    app.dependency_overrides[get_db] = override(maker)
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        origin = {"Origin": "https://ie.siteformo.com"}
        credential = client.post("/api/journey/session", headers=origin).json()["credential"]
        order_id, headers = create_q1(client, credential)
        other = client.post("/api/journey/session", headers=origin).json()["credential"]
        assert client.patch(f"/api/orders/{order_id}/q2", headers={**origin, JOURNEY_CREDENTIAL_HEADER: other}, json=q2_payload()).status_code == 403
        with maker() as db:
            db.get(Order, order_id).status = OrderStatus.BRIEF_SUBMITTED
            db.commit()
        assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload()).status_code == 403
        third = client.post("/api/journey/session", headers=origin).json()["credential"]
        third_headers = {**origin, JOURNEY_CREDENTIAL_HEADER: third}
        draft = client.post("/api/orders/q1/project", headers=third_headers, json={}).json()["order_id"]
        assert client.patch(f"/api/orders/{draft}/q2", headers=third_headers, json=q2_payload()).status_code == 403
        with maker() as db:
            assert len(db.execute(select(Order)).scalars().all()) == 2
    finally:
        app.dependency_overrides.clear()


def test_q2_project_lifecycle_on_postgresql():
    url = os.getenv("Q2_LIFECYCLE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Q2_LIFECYCLE_TEST_DATABASE_URL is not configured")
    parsed = urlparse(url)
    assert parsed.hostname in {"localhost", "127.0.0.1"}
    assert parsed.scheme.startswith("postgresql")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    JourneyBase.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    app.dependency_overrides[get_db] = override(maker)
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        origin = {"Origin": "https://ie.siteformo.com"}
        credential = client.post("/api/journey/session", headers=origin).json()["credential"]
        order_id, headers = create_q1(client, credential)
        first = client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload())
        repeat = client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload())
        assert first.status_code == 200 and first.json()["idempotent"] is False
        assert repeat.status_code == 200 and repeat.json()["idempotent"] is True
        assert first.json()["order_id"] == repeat.json()["order_id"] == order_id

        other = client.post("/api/journey/session", headers=origin).json()["credential"]
        denied = client.patch(
            f"/api/orders/{order_id}/q2",
            headers={**origin, JOURNEY_CREDENTIAL_HEADER: other},
            json=q2_payload(),
        )
        assert denied.status_code == 403
        with maker() as db:
            orders = db.execute(select(Order)).scalars().all()
            bindings = db.execute(select(SiteFormoJourneyProject)).scalars().all()
            assert len(orders) == 1
            assert len(bindings) == 1 and bindings[0].is_current
            assert orders[0].brief_answers["q1_v2"]["project_class_intent"] == "business_site"
            assert orders[0].extended_brief["q2_v2"]["flow_version"] == "q2_v2"
    finally:
        app.dependency_overrides.clear()
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()


def candidate(**changes):
    data = q2_payload(starting=changes.pop("starting", "starter"), functions=changes.pop("functions", None), options=changes.pop("options", None))
    for key, value in changes.items():
        if key.startswith("business_activity."):
            data["business_activity"][key.split(".", 1)[1]] = value
        else:
            data[key] = value
    return Q2V2Payload.model_validate(data)


def _legacy_preliminary_architecture_representative_business_cases():
    # A, I: one focused local-service journey, even when the visitor browsed Advanced.
    simple = qualify_scope(candidate(starting="advanced", trust_materials=["none_yet"]))
    assert simple["provisional_page_need"] == 1
    assert simple["provisional_architecture"]["composition_class"] == "FOCUSED_ONE_PAGE"
    assert len(simple["provisional_architecture"]["semantic_needs"]) > len(simple["provisional_architecture"]["page_groups"])
    assert simple["recommended_package"] == "starter"
    assert simple["optional_recommendation_reasons"][0]["reason_code"] == "symmetric_downgrade_recommendation"

    # B/F: separators in short niche and several trust assets never create pressure.
    coherent = qualify_scope(candidate(
        starting="starter",
        **{"business_activity.niche": "Plumbing; heating; electrical"},
        trust_materials=["reviews", "certifications", "awards", "team_photos"],
    ))
    assert coherent["provisional_page_need"] == 1
    assert "multiple_offer_categories" not in coherent["provisional_architecture"]["semantic_needs"]
    assert coherent["provisional_architecture"]["evidence"]["short_niche_used_for_page_pressure"] is False
    assert coherent["provisional_architecture"]["evidence"]["trust_materials_used_for_page_pressure"] is False
    assert coherent["recommended_package"] == "starter"

    # A genuinely confirmed two-page need remains a commercial choice.
    two_page = qualify_scope(candidate(), ConfirmedStructuralNeeds(minimum_coherent_pages=2))
    assert two_page["provisional_page_need"] == 2
    assert two_page["minimum_package"] == "starter" and two_page["recommended_package"] is None
    assert {path["package"] for path in two_page["provisional_architecture"]["commercial_paths"]} == {"starter", "business"}
    assert two_page["provisional_architecture"]["proposed_paid_pages"][0]["status"] == "PROPOSED_PAID_PAGE"

    # C: project photos reserve project structure but do not raise package alone.
    project_payload = candidate(
        **{"business_activity.broad_model": "construction_renovation"},
        primary_goal="present_and_build_trust",
        trust_materials=["project_photos", "reviews"],
    )
    unconfirmed_projects = qualify_scope(project_payload)
    assert "projects_work" in unconfirmed_projects["provisional_architecture"]["semantic_needs"]
    assert unconfirmed_projects["provisional_page_need"] == 1
    projects = qualify_scope(project_payload, ConfirmedStructuralNeeds(minimum_coherent_pages=4))
    assert projects["provisional_page_need"] == 4
    assert projects["minimum_package"] == "business" and projects["recommended_package"] is None
    proposed = projects["provisional_architecture"]["proposed_paid_pages"]
    assert proposed[0]["status"] == "PROPOSED_PAID_PAGE"
    assert {path["package"] for path in projects["provisional_architecture"]["commercial_paths"]} == {"business", "reference"}
    assert projects["checkout_ready"] is False

    # D/E: public-location and service-area facts compose into the focused page.
    public = candidate(operating_model="customer_location", location={"public_address": "1 Main Street", "service_area": None, "multiple_locations_summary": None}, trust_materials=["none_yet"])
    service_area = candidate(trust_materials=["none_yet"])
    assert plan_preliminary_architecture(public)["provisional_page_need"] == 1
    assert "location_service_area" in plan_preliminary_architecture(public)["semantic_needs"]
    assert plan_preliminary_architecture(service_area)["provisional_page_need"] == 1

    # Unconfirmed Q1 analysis hints never create a page or package floor.
    hinted_data = q2_payload()
    hinted_data["analysis_hints"] = {
        "source": "existing_website", "status": "unconfirmed",
        "business_hints": [], "function_hints": ["marketplace", "blog"],
        "complexity_hints": ["enterprise"],
    }
    hinted = qualify_scope(Q2V2Payload.model_validate(hinted_data))
    assert hinted["provisional_page_need"] == qualify_scope(candidate())["provisional_page_need"]
    assert hinted["recommended_package"] == qualify_scope(candidate())["recommended_package"]

    # G/J: only a separate confirmed structural signal establishes 3 pages.
    compact = qualify_scope(candidate(starting="starter"), ConfirmedStructuralNeeds(minimum_coherent_pages=3))
    assert compact["provisional_page_need"] == 3
    assert compact["recommended_package"] == "business"
    assert compact["optional_recommendation_reasons"][0]["reason_code"] == "symmetric_upgrade_recommendation"

    # H: genuinely distinct confirmed paths establish richer 5-page architecture.
    richer = qualify_scope(candidate(
        operating_model="multiple_locations",
        location={"public_address": None, "service_area": None, "multiple_locations_summary": "Dublin, Cork and Galway"},
        audience="both",
        trust_materials=["reviews", "certifications"],
    ), ConfirmedStructuralNeeds(materially_distinct_customer_journeys=3, distinct_location_paths=3))
    assert richer["provisional_page_need"] == 5
    assert richer["provisional_architecture"]["composition_class"] == "RICHER_STRUCTURED"
    assert richer["recommended_package"] == "reference"


def _legacy_video_entry_and_unresolved_rules_block_safely():
    video = {"key": "video_entry_page", "quantity": 1, "price_eur_each": 120, "counts_as_page": True, "explicit_confirmed": True}
    starter = qualify_scope(candidate(starting="starter", options=[video], trust_materials=["none_yet"]))
    assert starter["minimum_package"] == "starter" and starter["checkout_ready"] is True
    assert starter["provisional_architecture"]["explicit_structural_pages"] == 1
    assert starter["provisional_page_need"] == 2

    # F/G: semantic needs are retained while unresolved entitlements stay manual.
    blog = qualify_scope(candidate(functions=[{"key": "blog_news", "confirmed": True}]))
    assert "content_blog" in blog["provisional_architecture"]["semantic_needs"]
    assert blog["provisional_architecture"]["composition_class"] == "MANUAL_REVIEW"
    assert blog["eligibility"] == "manual_review" and blog["recommended_package"] is None
    commerce = qualify_scope(candidate(functions=[{"key": "transactional_ecommerce", "confirmed": True}]))
    assert "commerce" in commerce["provisional_architecture"]["semantic_needs"]
    assert commerce["eligibility"] == "manual_review" and commerce["checkout_ready"] is False
    account = qualify_scope(candidate(functions=[{"key": "customer_account_login", "confirmed": True}]))
    assert "customer_accounts" in account["provisional_architecture"]["semantic_needs"]
    assert account["eligibility"] == "manual_review" and account["recommended_package"] is None

    unconfirmed = {**video, "explicit_confirmed": False}
    pending = qualify_scope(candidate(options=[unconfirmed]))
    assert pending["checkout_ready"] is False
    assert any(x["reason_code"] == "paid_addon_not_confirmed" for x in pending["unresolved_requirements"])


def _legacy_confirmed_paid_page_resolves_four_page_business_path():
    extra = {"key": "additional_page", "quantity": 1, "price_eur_each": 120, "counts_as_page": True, "explicit_confirmed": True}
    result = qualify_scope(candidate(
        options=[extra],
        **{"business_activity.broad_model": "portfolio_creative"},
        primary_goal="present_and_build_trust",
        trust_materials=["project_photos", "reviews"],
    ), ConfirmedStructuralNeeds(minimum_coherent_pages=4))
    assert result["provisional_page_need"] == 4
    assert result["minimum_package"] == "business"
    assert result["recommended_package"] == "business"
    assert result["provisional_architecture"]["proposed_paid_pages"] == []
    assert result["checkout_ready"] is True


class StaticReasoner:
    def __init__(self, payload, pages):
        self.needs = plan_preliminary_architecture(payload)["semantic_needs"]
        self.pages = pages

    def propose(self, confirmed_input):
        role_keys = [
            "primary_conversion", "services_or_offer", "contact_or_enquiry",
            "business_trust", "location",
        ]
        groups = [[] for _ in range(self.pages)]
        for index, need in enumerate(self.needs):
            groups[index % self.pages].append(need)
        assert all(groups)
        return {
            "status": "resolved",
            "minimum_coherent_pages": self.pages,
            "proposed_page_roles": [
                {
                    "role_key": role_keys[index],
                    "purpose": f"Validated provisional role {index + 1}",
                    "requirements_served": group,
                }
                for index, group in enumerate(groups)
            ],
            "semantic_needs": self.needs,
            "reasoning_codes": ["controlled_reasoner_fixture"],
            "unresolved_requirements": [],
            "confidence": "medium",
        }


class FailingReasoner:
    def propose(self, confirmed_input):
        raise RuntimeError("controlled failure")


class InvalidReasoner:
    def propose(self, confirmed_input):
        return {
            "status": "resolved",
            "minimum_coherent_pages": 1,
            "proposed_page_roles": [{
                "role_key": "unknown_page",
                "purpose": "Invalid controlled output",
                "requirements_served": ["primary_conversion"],
            }],
            "semantic_needs": ["primary_conversion"],
            "reasoning_codes": ["invalid"],
            "unresolved_requirements": [],
            "confidence": "high",
            "package": "advanced",
        }


def test_real_architecture_planner_focused_and_compact_cases():
    # A: focused electrician.
    electrician = candidate(trust_materials=["reviews", "certifications", "project_photos"])
    focused = qualify_scope(electrician)
    architecture = focused["provisional_architecture"]
    assert architecture["status"] == "resolved"
    assert architecture["minimum_coherent_pages"] == 1
    assert architecture["proposed_page_roles"][0]["role_key"] == "primary_conversion"
    assert "projects_or_work" in architecture["semantic_needs"]
    assert focused["recommended_package"] == "starter"

    # B: broader quote journey is established by confirmed functions, never niche parsing.
    broader = candidate(
        primary_goal="quote_requests",
        functions=[
            {"key": "standard_content_contact", "confirmed": True},
            {"key": "contact_enquiry", "confirmed": True},
            {"key": "direct_contact_channel", "confirmed": True},
        ],
    )
    compact = qualify_scope(broader)
    assert compact["provisional_architecture"]["minimum_coherent_pages"] == 3
    assert [role["role_key"] for role in compact["provisional_architecture"]["proposed_page_roles"]] == [
        "primary_conversion", "services_or_offer", "contact_or_enquiry",
    ]
    assert compact["recommended_package"] == "business"

    # C: consultant lead generation remains one page.
    consultant = candidate(**{"business_activity.broad_model": "consultant_expert"})
    assert qualify_scope(consultant)["provisional_page_need"] == 1


def test_architecture_assets_location_and_audience_compose():
    # E: portfolio photos record a role need but do not force a page.
    portfolio = candidate(
        **{"business_activity.broad_model": "portfolio_creative"},
        primary_goal="present_and_build_trust",
        trust_materials=["project_photos", "awards", "client_logos"],
    )
    result = qualify_scope(portfolio)
    assert "projects_or_work" in result["provisional_architecture"]["semantic_needs"]
    assert result["provisional_page_need"] == 1

    # F: restaurant address is section content.
    restaurant = candidate(
        **{"business_activity.broad_model": "restaurant_cafe"},
        operating_model="customer_location",
        location={"public_address": "1 Main Street", "service_area": None, "multiple_locations_summary": None},
        primary_goal="physical_visits",
    )
    location_result = qualify_scope(restaurant)
    assert "location" in location_result["provisional_architecture"]["semantic_needs"]
    assert location_result["provisional_page_need"] == 1

    # G: mixed audience is context, not proof of distinct journeys.
    mixed = qualify_scope(candidate(audience="mixed_unsure"))
    assert "mixed_audience_context" in mixed["provisional_architecture"]["semantic_needs"]
    assert mixed["provisional_page_need"] == 1


def test_unresolved_functions_override_architecture_and_package():
    # D/H: booking, ecommerce and accounts retain semantic needs but remain manual.
    cases = [
        ("booking", "bookings", "booking"),
        ("transactional_ecommerce", "online_sales", "commerce"),
        ("customer_account_login", "enquiries", "customer_area"),
        ("portal_membership", "enquiries", "customer_area"),
        ("multilingual", "enquiries", "multilingual"),
        ("blog_news", "enquiries", "content_hub"),
        ("newsletter", "enquiries", "newsletter"),
        ("catalogue_display", "enquiries", "catalogue"),
        ("advanced_form", "enquiries", "conditional_form_flow"),
        ("search_filter_compare", "enquiries", "search_filter_compare"),
        ("custom_other", "enquiries", "custom_functionality"),
    ]
    for function_key, goal, semantic_need in cases:
        result = qualify_scope(candidate(
            primary_goal=goal,
            functions=[{"key": function_key, "confirmed": True}],
        ))
        assert result["provisional_architecture"]["status"] == "manual_review"
        assert semantic_need in result["provisional_architecture"]["semantic_needs"]
        assert result["minimum_package"] is None
        assert result["checkout_ready"] is False


def test_reasoner_validation_stability_failure_and_commercial_boundary():
    # I: identical confirmed input is deterministic and fingerprinted.
    payload = candidate()
    first = plan_preliminary_architecture(payload)
    second = plan_preliminary_architecture(payload)
    assert first == second
    assert len(first["input_fingerprint"]) == 64
    different_browsing_context = candidate(starting="advanced")
    assert (
        plan_preliminary_architecture(different_browsing_context)["input_fingerprint"]
        == first["input_fingerprint"]
    )

    # A controlled reasoner can resolve ambiguous multi-location architecture.
    ambiguous = candidate(
        operating_model="multiple_locations",
        location={"public_address": None, "service_area": None, "multiple_locations_summary": "Three distinct visitor locations"},
    )
    five_page = qualify_scope(ambiguous, StaticReasoner(ambiguous, 5))
    assert five_page["provisional_architecture"]["minimum_coherent_pages"] == 5
    assert five_page["recommended_package"] == "reference"
    planner_input = architecture_input(ambiguous).model_dump(mode="json")
    assert "package_context" not in planner_input
    assert "package" not in planner_input
    assert "price" not in planner_input

    # J: missing/failing reasoner is always manual review, never Starter fallback.
    no_reasoner = qualify_scope(ambiguous)
    failed = qualify_scope(ambiguous, FailingReasoner())
    invalid = qualify_scope(ambiguous, InvalidReasoner())
    assert no_reasoner["eligibility"] == "manual_review"
    assert failed["eligibility"] == "manual_review"
    assert invalid["eligibility"] == "manual_review"
    assert no_reasoner["recommended_package"] is None
    assert failed["recommended_package"] is None
    assert invalid["recommended_package"] is None

    # Four resolved pages expose commercial choices without selecting either.
    other = candidate(**{"business_activity.broad_model": "other", "business_activity.other_clarification": "Confirmed factual business"})
    four_page = qualify_scope(other, StaticReasoner(other, 4))
    assert four_page["minimum_package"] == "business"
    assert four_page["recommended_package"] is None
    assert {path["package"] for path in four_page["commercial_paths"]} == {"business", "reference"}
    assert four_page["proposed_paid_pages"][0]["status"] == "PROPOSED_PAID_PAGE"


def test_commercial_capacity_for_one_through_five_pages():
    one_page = qualify_scope(candidate(starting="advanced"))
    assert one_page["provisional_page_need"] == 1
    assert one_page["recommended_package"] == "starter"

    broader = candidate(
        primary_goal="quote_requests",
        functions=[
            {"key": "standard_content_contact", "confirmed": True},
            {"key": "contact_enquiry", "confirmed": True},
            {"key": "direct_contact_channel", "confirmed": True},
        ],
    )
    three_page = qualify_scope(broader)
    assert three_page["provisional_page_need"] == 3
    assert three_page["recommended_package"] == "business"

    ambiguous = candidate(
        operating_model="multiple_locations",
        location={"public_address": None, "service_area": None, "multiple_locations_summary": "Distinct locations"},
        **{
            "business_activity.broad_model": "other",
            "business_activity.other_clarification": "Confirmed factual business",
        }
    )
    two_page = qualify_scope(ambiguous, StaticReasoner(ambiguous, 2))
    assert two_page["minimum_package"] == "starter"
    assert two_page["recommended_package"] is None
    assert {path["package"] for path in two_page["commercial_paths"]} == {"starter", "business"}
    assert two_page["proposed_paid_pages"][0]["requires_client_confirmation"] is True
    assert two_page["checkout_ready"] is False

    four_page = qualify_scope(ambiguous, StaticReasoner(ambiguous, 4))
    assert four_page["minimum_package"] == "business"
    assert four_page["recommended_package"] is None
    assert {path["package"] for path in four_page["commercial_paths"]} == {"business", "reference"}
    assert four_page["proposed_paid_pages"][0]["requires_client_confirmation"] is True
    assert four_page["checkout_ready"] is False

    five_page = qualify_scope(ambiguous, StaticReasoner(ambiguous, 5))
    assert five_page["minimum_package"] == "reference"
    assert five_page["recommended_package"] == "reference"
    assert five_page["commercial_paths"] == [{
        "package": "reference",
        "additional_capacity_pages": 0,
        "additional_capacity_price_eur_each": 0,
    }]


def test_explicit_paid_page_resolves_two_and_four_page_paths_only_after_confirmation():
    extra = {
        "key": "additional_page",
        "quantity": 1,
        "price_eur_each": 120,
        "counts_as_page": True,
        "explicit_confirmed": True,
    }
    ambiguous = candidate(
        options=[extra],
        operating_model="multiple_locations",
        location={"public_address": None, "service_area": None, "multiple_locations_summary": "Distinct locations"},
        **{
            "business_activity.broad_model": "other",
            "business_activity.other_clarification": "Confirmed factual business",
        },
    )
    two_page = qualify_scope(ambiguous, StaticReasoner(ambiguous, 2))
    assert two_page["recommended_package"] == "starter"
    assert two_page["proposed_paid_pages"] == []
    assert two_page["checkout_ready"] is True

    four_page = qualify_scope(ambiguous, StaticReasoner(ambiguous, 4))
    assert four_page["recommended_package"] == "business"
    assert four_page["proposed_paid_pages"] == []
    assert four_page["checkout_ready"] is True


def test_video_entry_is_explicit_additional_page():
    video = {"key": "video_entry_page", "quantity": 1, "price_eur_each": 120, "counts_as_page": True, "explicit_confirmed": True}
    result = qualify_scope(candidate(options=[video], trust_materials=["none_yet"]))
    assert result["provisional_architecture"]["minimum_coherent_pages"] == 2
    assert result["provisional_architecture"]["proposed_page_roles"][-1]["role_key"] == "video_entry"
    assert result["minimum_package"] == "starter"
    assert result["checkout_ready"] is True


def test_frontend_contract_and_sleeping_assistant_hooks():
    source = (Path(__file__).parents[2] / "frontend" / "q2_v2_WPCode.html").read_text(encoding="utf-8")
    for hook in ("q2_intro", "q2_business_identity", "q2_business_activity", "q2_location", "q2_audience_goal", "q2_functions", "q2_assets", "q2_delivery", "q2_review", "q2_package_confirmation"):
        assert hook in source
    assert 'Q2_PATH="/extended-questionnaire"' in source
    assert "/api/orders/intake" not in source and "/api/orders/extended-brief" not in source
    assert "/api/assistant/session" not in source and "/api/assistant/message" not in source and "OpenAI" not in source
    assert "Page 1" not in source and "page-type" not in source
    assert "stock photos +60" not in source and "custom/AI photos +120" not in source
    assert "create-checkout" not in source
    assert "siteformo:assistant-collapse" in source and "terminateConversation:false" in source
    assert "overflow-x:clip" in source
