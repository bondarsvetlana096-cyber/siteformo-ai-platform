from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas import site_plan as site_plan_schema
from app.schemas.site_plan import SitePlanV1
from app.services.generation_context_service import build_generation_context_v1
from app.services.site_plan_validator import REQUIREMENT_COMPONENTS, UNIVERSAL_COMPONENTS, validate_site_plan_v1
from test_generation_context_v1 import order, q2


def context_for(package="starter", pages=1, *, functions=None, options=None, install=False, logo=False):
    item = order()
    payload = q2(package, pages, options=options, install=install, logo=logo)
    if functions is not None:
        payload["functions"] = [{"key": key, "confirmed": True} for key in functions]
    item.extended_brief["q2_v2"] = payload
    result = build_generation_context_v1(item, "journey-project-1")
    assert result.status == "ready"
    return result.context


def behavior():
    return {"strategy": "stack", "touch_safe_equivalent": True, "hover_only_required": False}


def reduced():
    return {"strategy": "static_equivalent", "preserves_content_and_functionality": True}


def section(key="hero", *, components=None, critical=True, motion="subtle", interactions=None, signatures=None, media=None):
    return {
        "section_key": key, "purpose": "Present the confirmed offer clearly",
        "content_requirements": [{"content_key": "primary_goal", "kind": "confirmed_fact", "source_key": "business.primary_goal", "factual_claims_must_be_confirmed": True}],
        "media_requirements": media or [{"media_key": "hero_media", "kind": "no_media", "required": False}],
        "functional_components": components if components is not None else ["enquiry_form"],
        "primary_actions": [{"action_key": "enquire", "intent": "Send a project enquiry", "target_page_key": None, "critical_family": "primary_conversion_actions"}] if critical else [],
        "interaction_families": interactions or [], "signature_interactions": signatures or [],
        "motion_policy": {"level": motion, "required_for_completion": False, "blocks_critical_action": False},
        "critical_action": critical, "mobile_behavior": behavior(), "reduced_motion_behavior": reduced(),
    }


def page(key="home", role="home", *, sections=None):
    return {
        "page_key": key, "page_role": role, "purpose": f"Serve the {role} role",
        "route_intent": key, "sections": sections or [section()],
        "mobile_behavior": behavior(), "reduced_motion_behavior": reduced(),
    }


def candidate(context, *, pages=None, journeys=None, shared=None, inventory=None):
    pages = pages or [page()]
    used = {component for item in pages for part in item["sections"] for component in part["functional_components"]}
    shared = shared or []
    used.update(item["component"] for item in shared)
    return {
        "contract_version": "v1", "generation_context_hash": context.fingerprints.context_hash,
        "plan_status": "draft", "pages": pages, "cross_page_navigation": [],
        "stateful_journeys": journeys or [], "shared_components": shared,
        "content_inventory": [{"item_key": "primary_goal", "required": True}],
        "media_inventory": [], "functional_component_inventory": sorted(used) if inventory is None else inventory,
        "accessibility_constraints": {"keyboard_operable": True, "touch_safe": True, "reduced_motion_supported": True, "critical_actions_stable": True, "no_hover_only_required_actions": True},
        "generation_constraints": {"source_design_direction": context.visual.design_direction.value, "source_interaction_preference": context.interaction_guidance.client_preference.value, "no_unconfirmed_functionality": True, "no_unsupported_addons": True, "no_factual_claim_invention": True, "no_raw_code": True},
        "unresolved_items": [], "planner_reasoning_codes": ["refine_provisional_architecture", "respect_confirmed_scope"],
        "validator_version": "v1", "site_plan_hash": None, "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.parametrize(("package", "pages"), [("starter", 1), ("business", 3), ("reference", 5), ("advanced", 8)])
def test_valid_plan_for_every_package_without_legacy_five_page_rule(package, pages):
    context = context_for(package, pages)
    result = validate_site_plan_v1(context, candidate(context))
    assert result.status == "valid" and result.plan.plan_status == "validated"


def test_every_site_plan_model_is_closed_and_forbidden_fields_cannot_serialize():
    models = set(); pending = [site_plan_schema.ClosedModel]
    while pending:
        parent = pending.pop()
        for child in parent.__subclasses__():
            if child not in models: models.add(child); pending.append(child)
    assert models and all(model.model_config.get("extra") == "forbid" for model in models)
    context = context_for(); valid = validate_site_plan_v1(context, candidate(context)).plan.model_dump(mode="json")
    forbidden = {"html", "css", "javascript", "script", "prompt", "system_prompt", "chain_of_thought", "price", "amount", "currency", "payment_status", "legal_terms", "checkout_url", "external_url", "raw_url", "package_override", "interaction_style", "selected_effects"}
    assert not (forbidden & set(str(valid).replace("'", '"').split('"')))
    for field in forbidden:
        hostile = deepcopy(valid); hostile[field] = "unsafe"
        with pytest.raises(ValidationError): SitePlanV1.model_validate(hostile)


def test_plan_cannot_cross_contexts_even_when_visible_business_fields_match():
    context_a = context_for(); data = context_a.model_dump(mode="json")
    data["order"]["order_id"] = "different-order"
    data["fingerprints"]["context_hash"] = "e" * 64
    context_b = type(context_a).model_validate(data)
    plan = candidate(context_a)
    assert context_a.business == context_b.business
    assert "generation_context_hash_mismatch" in validate_site_plan_v1(context_b, plan).reason_codes


def test_provisional_architecture_can_be_refined_without_being_copied_as_final_pages():
    context = context_for("business", 3)
    plan = candidate(context)
    assert plan["pages"][0]["page_key"] != context.scope.provisional_architecture.proposed_page_roles[0].role_key
    assert validate_site_plan_v1(context, plan).status == "valid"


@pytest.mark.parametrize("mutation,reason", [
    (lambda plan: plan["pages"].append(deepcopy(plan["pages"][0])), "duplicate_page_key"),
    (lambda plan: plan["pages"][0]["sections"].append(deepcopy(plan["pages"][0]["sections"][0])), "duplicate_section_key"),
    (lambda plan: plan["pages"][0]["sections"][0]["primary_actions"][0].update(target_page_key="missing"), "invalid_page_reference"),
])
def test_duplicate_keys_and_invalid_page_references_rejected(mutation, reason):
    context = context_for(); plan = candidate(context); mutation(plan)
    assert reason in validate_site_plan_v1(context, plan).reason_codes


def test_duplicate_route_empty_sections_self_loop_and_disconnected_page_rejected():
    context = context_for("business", 3)
    duplicate = candidate(context, pages=[page(), page("contact", "contact_or_enquiry")]); duplicate["pages"][1]["route_intent"] = "home"
    assert "duplicate_route_intent" in validate_site_plan_v1(context, duplicate).reason_codes
    empty = candidate(context); empty["pages"][0]["sections"] = []
    assert validate_site_plan_v1(context, empty).reason_codes == ["schema_validation_failed"]
    loop = candidate(context); loop["pages"][0]["sections"][0]["primary_actions"][0]["target_page_key"] = "home"
    assert "unsafe_self_reference" in validate_site_plan_v1(context, loop).reason_codes
    disconnected = candidate(context, pages=[page(), page("contact", "contact_or_enquiry")]); disconnected["cross_page_navigation"] = [{"from_page_key": "contact", "to_page_key": "home", "purpose": "conversion"}]
    assert "unreachable_page" in validate_site_plan_v1(context, disconnected).reason_codes


def test_confirmed_component_allowed_but_invented_component_rejected():
    context = context_for()
    assert validate_site_plan_v1(context, candidate(context)).status == "valid"
    plan = candidate(context); plan["pages"][0]["sections"][0]["functional_components"] = ["checkout"]; plan["functional_component_inventory"] = ["checkout"]
    assert "unsupported_functional_component" in validate_site_plan_v1(context, plan).reason_codes


@pytest.mark.parametrize("component", sorted(set(site_plan_schema.FunctionalComponentKey.__args__) - UNIVERSAL_COMPONENTS))
def test_every_nonuniversal_component_is_rejected_without_backing_functionality(component):
    context = context_for(functions=[]); plan = candidate(context)
    plan["pages"][0]["sections"][0]["functional_components"] = [component]
    plan["functional_component_inventory"] = [component]
    assert "unsupported_functional_component" in validate_site_plan_v1(context, plan).reason_codes


def test_component_registry_has_no_unbacked_member_and_custom_other_authorizes_none():
    registry = set(site_plan_schema.FunctionalComponentKey.__args__)
    backed = set().union(*REQUIREMENT_COMPONENTS.values()) | UNIVERSAL_COMPONENTS
    assert registry == backed
    assert REQUIREMENT_COMPONENTS["custom_other"] == set()
    context = context_for(functions=["custom_other"])
    result = validate_site_plan_v1(context, candidate(context, pages=[page(sections=[section(components=[], critical=True)])]))
    assert result.status == "manual_review" and "custom_function_requires_manual_review" in result.reason_codes


def test_unresolved_content_or_plan_item_requires_manual_review():
    context = context_for(); plan = candidate(context)
    plan["unresolved_items"] = [{"item_key": "missing_fact", "category": "content", "reason_code": "missing_confirmed_fact"}]
    assert validate_site_plan_v1(context, plan).status == "manual_review"


def test_confirmed_fact_must_reference_allowlisted_generation_context_fact():
    context = context_for(); plan = candidate(context)
    plan["pages"][0]["sections"][0]["content_requirements"][0]["source_key"] = "model.claims.award_winner"
    assert "unconfirmed_factual_source" in validate_site_plan_v1(context, plan).reason_codes


def test_capability_specific_page_roles_require_confirmed_functionality():
    context = context_for(); plan = candidate(context, pages=[page("book", "booking")])
    assert "unsupported_page_role" in validate_site_plan_v1(context, plan).reason_codes
    booking = context_for(functions=["booking"]); assert validate_site_plan_v1(booking, candidate(booking, pages=[page("book", "booking", sections=[section(components=["booking"])])])).status == "valid"


@pytest.mark.parametrize("preference", ["subtle", "recommended", "more_expressive"])
def test_all_interaction_preferences_are_guidance_not_effect_lists(preference):
    context = context_for(); data = context.model_dump(mode="json"); data["interaction_guidance"]["client_preference"]["value"] = preference
    context = type(context).model_validate(data); plan = candidate(context)
    result = validate_site_plan_v1(context, plan)
    assert result.status == "valid" and "selected_effects" not in str(result.plan.model_dump())


def test_typed_interaction_family_allowed_unknown_family_schema_rejected():
    context = context_for(); plan = candidate(context)
    editorial = section("story", components=[], critical=False, motion="moderate", interactions=["editorial"])
    plan["pages"][0]["sections"].append(editorial)
    assert validate_site_plan_v1(context, plan).status == "valid"
    plan["pages"][0]["sections"][1]["interaction_families"] = ["parallax"]
    assert validate_site_plan_v1(context, plan).reason_codes == ["schema_validation_failed"]


@pytest.mark.parametrize("component", ["navigation", "contact_form", "enquiry_form", "booking", "checkout", "account", "login"])
def test_unsafe_critical_form_booking_checkout_account_interaction_rejected(component):
    requirement = {"navigation": "contact_enquiry", "contact_form": "contact_enquiry", "enquiry_form": "contact_enquiry", "booking": "booking", "checkout": "transactional_ecommerce", "account": "customer_account_login", "login": "customer_account_login"}[component]
    context = context_for(functions=[requirement])
    unsafe = section(components=[component], critical=True, motion="expressive", interactions=["transactional"], signatures=[{"interaction_key": "blocking_motion", "family": "transactional", "intent": "Animate completion", "required_for_completion": False}])
    result = validate_site_plan_v1(context, candidate(context, pages=[page(sections=[unsafe])]))
    assert "unsafe_critical_action_interaction" in result.reason_codes


def test_mobile_hover_only_and_missing_reduced_motion_are_schema_rejected():
    context = context_for(); plan = candidate(context)
    plan["pages"][0]["sections"][0]["mobile_behavior"]["hover_only_required"] = True
    assert validate_site_plan_v1(context, plan).reason_codes == ["schema_validation_failed"]
    plan = candidate(context); plan["pages"][0]["sections"][0]["reduced_motion_behavior"] = None
    assert validate_site_plan_v1(context, plan).reason_codes == ["schema_validation_failed"]


def test_page_mobile_and_reduced_motion_and_preservation_are_mandatory():
    context = context_for()
    for path in ["mobile_behavior", "reduced_motion_behavior"]:
        plan = candidate(context); plan["pages"][0].pop(path)
        assert validate_site_plan_v1(context, plan).reason_codes == ["schema_validation_failed"]
    plan = candidate(context); plan["pages"][0]["sections"][0]["reduced_motion_behavior"]["preserves_content_and_functionality"] = False
    assert validate_site_plan_v1(context, plan).reason_codes == ["schema_validation_failed"]


def test_more_expressive_never_weakens_critical_action_safety():
    context = context_for(); data = context.model_dump(mode="json"); data["interaction_guidance"]["client_preference"]["value"] = "more_expressive"; context = type(context).model_validate(data)
    unsafe = section(motion="expressive", interactions=["transactional"], signatures=[{"interaction_key": "hide_control", "family": "transactional", "intent": "Hide the required control during motion", "required_for_completion": False}])
    assert "unsafe_critical_action_interaction" in validate_site_plan_v1(context, candidate(context, pages=[page(sections=[unsafe])])).reason_codes


def test_required_signature_or_blocking_motion_is_schema_rejected():
    context = context_for()
    plan = candidate(context); plan["pages"][0]["sections"][0]["signature_interactions"] = [{"interaction_key": "required_motion", "family": "transactional", "intent": "Complete the task", "required_for_completion": True}]
    assert validate_site_plan_v1(context, plan).reason_codes == ["schema_validation_failed"]
    plan = candidate(context); plan["pages"][0]["sections"][0]["motion_policy"]["blocks_critical_action"] = True
    assert validate_site_plan_v1(context, plan).reason_codes == ["schema_validation_failed"]


def test_cross_page_navigation_requires_existing_keys_and_conversion_path():
    context = context_for("business", 3)
    pages = [page(), page("contact", "contact_or_enquiry")]
    plan = candidate(context, pages=pages)
    assert "primary_conversion_path_missing" in validate_site_plan_v1(context, plan).reason_codes
    plan["cross_page_navigation"] = [{"from_page_key": "home", "to_page_key": "contact", "purpose": "conversion"}]
    assert validate_site_plan_v1(context, plan).status == "valid"
    plan["cross_page_navigation"][0]["to_page_key"] = "missing"
    assert "invalid_page_reference" in validate_site_plan_v1(context, plan).reason_codes


def test_primary_conversion_action_is_required_even_on_single_page_plan():
    context = context_for(); plan = candidate(context)
    plan["pages"][0]["sections"][0]["primary_actions"] = []
    assert "primary_conversion_action_missing" in validate_site_plan_v1(context, plan).reason_codes


@pytest.mark.parametrize(("package", "requirement", "journey_type", "components", "persistence"), [
    ("reference", "booking", "booking", ["booking"], "session"),
    ("advanced", "customer_account_login", "account", ["account", "login"], "account"),
])
def test_valid_reference_and_advanced_stateful_journeys(package, requirement, journey_type, components, persistence):
    context = context_for(package, 5 if package == "reference" else 8, functions=[requirement])
    parts = [section(components=components, critical=True)]
    journey = {"journey_key": f"{journey_type}_flow", "journey_type": journey_type, "state_keys": ["start", "complete"], "entry_page_key": "home", "completion_page_key": "home", "required_components": components, "persistence": persistence}
    assert validate_site_plan_v1(context, candidate(context, pages=[page(sections=parts)], journeys=[journey])).status == "valid"


def test_reference_can_use_confirmed_sophisticated_nonaccount_capabilities():
    requirements = ["booking", "transactional_ecommerce", "search_filter_compare", "advanced_form", "multilingual"]
    context = context_for("reference", 5, functions=requirements)
    components = ["booking", "reservation", "product_list", "product_detail", "cart", "checkout", "search", "filters", "comparison", "file_upload", "multilingual_switcher"]
    plan = candidate(context, pages=[page(sections=[section(components=components, critical=True)])])
    assert validate_site_plan_v1(context, plan).status == "valid"
    assert not ({"account", "saved_items", "alerts", "dashboard"} & set(plan["functional_component_inventory"]))


@pytest.mark.parametrize("mutation,reason", [
    (lambda journey: journey.update(entry_page_key="missing"), "invalid_journey_page"),
    (lambda journey: journey.update(completion_page_key="missing"), "invalid_journey_page"),
    (lambda journey: journey.update(required_components=["navigation"]), "journey_component_missing"),
    (lambda journey: journey.update(state_keys=["start", "start"]), "duplicate_journey_state"),
    (lambda journey: journey.update(persistence="database"), "schema_validation_failed"),
])
def test_stateful_journey_integrity_adversarial(mutation, reason):
    context = context_for("reference", 5, functions=["booking"])
    journey = {"journey_key": "booking_flow", "journey_type": "booking", "state_keys": ["start", "complete"], "entry_page_key": "home", "completion_page_key": "home", "required_components": ["booking"], "persistence": "session"}
    mutation(journey)
    result = validate_site_plan_v1(context, candidate(context, pages=[page(sections=[section(components=["booking"])])], journeys=[journey]))
    assert reason in result.reason_codes


def test_invented_persistent_journey_rejected():
    context = context_for("advanced", 8)
    journey = {"journey_key": "account_flow", "journey_type": "account", "state_keys": ["login", "complete"], "entry_page_key": "home", "completion_page_key": "home", "required_components": ["navigation"], "persistence": "account"}
    result = validate_site_plan_v1(context, candidate(context, journeys=[journey]))
    assert "invented_stateful_journey" in result.reason_codes and "invented_persistent_system" in result.reason_codes


def test_video_entry_required_when_confirmed_and_forbidden_when_unconfirmed():
    video = {"key": "video_entry_page", "quantity": 1, "price_eur_each": 120, "counts_as_page": True, "explicit_confirmed": True}
    context = context_for("starter", 2, options=[video])
    assert "confirmed_video_entry_missing" in validate_site_plan_v1(context, candidate(context)).reason_codes
    pages = [page(), page("video", "video_entry", sections=[section("video_intro")])]
    plan = candidate(context, pages=pages); plan["cross_page_navigation"] = [{"from_page_key": "home", "to_page_key": "video", "purpose": "conversion"}]
    assert validate_site_plan_v1(context, plan).status == "valid"
    no_video = context_for("business", 3); invented = candidate(no_video, pages=[page(), pages[1]]); invented["cross_page_navigation"] = plan["cross_page_navigation"]
    result = validate_site_plan_v1(no_video, invented)
    assert "unconfirmed_video_entry" in result.reason_codes


def test_simple_logo_requires_confirmation_and_installation_has_no_structural_effect():
    context = context_for(); plan = candidate(context)
    plan["pages"][0]["sections"][0]["media_requirements"] = [{"media_key": "logo", "kind": "simple_logo", "required": True}]
    assert "unconfirmed_simple_logo" in validate_site_plan_v1(context, plan).reason_codes
    logo_context = context_for(logo=True); assert validate_site_plan_v1(logo_context, candidate(logo_context, pages=plan["pages"])).status == "valid"
    installed = context_for(install=True); assert validate_site_plan_v1(installed, candidate(installed)).status == "valid"


@pytest.mark.parametrize(("kind", "photos", "expected"), [
    ("client_photo", "client_provides", "valid"),
    ("siteformo_selected_image", "siteformo_selects", "valid"),
    ("client_photo", "none", "invalid"),
    ("siteformo_selected_image", "none", "invalid"),
])
def test_photo_requirements_match_canonical_media_decision(kind, photos, expected):
    item = order(); payload = q2(); payload["media"] = {"photos": photos, "client_photos_timing": "later" if photos == "client_provides" else None, "video": "none"}; item.extended_brief["q2_v2"] = payload
    context = build_generation_context_v1(item, "jp").context
    plan = candidate(context); plan["pages"][0]["sections"][0]["media_requirements"] = [{"media_key": "photo", "kind": kind, "required": True}]
    assert validate_site_plan_v1(context, plan).status == expected


def test_video_logo_and_trust_assets_cannot_be_invented():
    context = context_for(); plan = candidate(context)
    plan["pages"][0]["sections"][0]["media_requirements"] = [{"media_key": "video", "kind": "client_video", "required": True}]
    assert "unconfirmed_video" in validate_site_plan_v1(context, plan).reason_codes
    data = context.model_dump(mode="json"); data["media"]["trust_assets"] = []; no_trust = type(context).model_validate(data)
    plan = candidate(no_trust); plan["pages"][0]["sections"][0]["media_requirements"] = [{"media_key": "proof", "kind": "trust_asset", "required": True}]
    assert "unconfirmed_trust_asset" in validate_site_plan_v1(no_trust, plan).reason_codes


def test_confirmed_additional_page_expands_capacity_without_auto_upgrade():
    extra = {"key": "additional_page", "quantity": 1, "price_eur_each": 120, "counts_as_page": True, "explicit_confirmed": True}
    context = context_for("starter", 2, options=[extra])
    pages = [page(), page("services", "services_or_offer")]
    plan = candidate(context, pages=pages); plan["cross_page_navigation"] = [{"from_page_key": "home", "to_page_key": "services", "purpose": "conversion"}]
    assert validate_site_plan_v1(context, plan).status == "valid"


def test_scope_capacity_conflict_is_manual_review_not_upgrade():
    context = context_for("starter", 1)
    pages = [page(), page("extra", "services_or_offer")]
    plan = candidate(context, pages=pages); plan["cross_page_navigation"] = [{"from_page_key": "home", "to_page_key": "extra", "purpose": "conversion"}]
    result = validate_site_plan_v1(context, plan)
    assert result.status == "manual_review" and "scope_conflict" in result.reason_codes


@pytest.mark.parametrize("category", ["content", "functionality", "structure", "media", "scope"])
def test_every_unresolved_category_is_preserved_as_manual_review(category):
    context = context_for(); plan = candidate(context)
    plan["unresolved_items"] = [{"item_key": f"unresolved_{category}", "category": category, "reason_code": "scope_conflict" if category == "scope" else "structural_ambiguity" if category == "structure" else "missing_client_material" if category == "media" else "unsupported_functionality" if category == "functionality" else "missing_confirmed_fact"}]
    result = validate_site_plan_v1(context, plan)
    assert result.status == "manual_review" and result.plan is None


def test_architecture_merge_requires_reason_and_confirmed_functionality_cannot_be_ignored():
    context = context_for("business", 3)
    plan = candidate(context); plan["planner_reasoning_codes"] = ["respect_confirmed_scope"]
    result = validate_site_plan_v1(context, plan)
    assert result.status == "manual_review" and "architecture_below_minimum_without_reason" in result.reason_codes
    booking = context_for("reference", 5, functions=["booking"])
    assert "confirmed_functionality_missing" in validate_site_plan_v1(booking, candidate(booking)).reason_codes


def test_direction_and_preference_trace_mismatch_rejected_without_second_authority():
    context = context_for(); plan = candidate(context); plan["generation_constraints"]["source_design_direction"] = "dark-contrast"
    assert "design_direction_trace_mismatch" in validate_site_plan_v1(context, plan).reason_codes
    plan = candidate(context); plan["generation_constraints"]["source_interaction_preference"] = "more_expressive"
    assert "interaction_preference_trace_mismatch" in validate_site_plan_v1(context, plan).reason_codes


def test_unknown_or_freeform_reasoning_code_rejected():
    context = context_for(); plan = candidate(context); plan["planner_reasoning_codes"] = ["Here is my hidden chain of thought"]
    assert validate_site_plan_v1(context, plan).reason_codes == ["schema_validation_failed"]


def test_hash_stable_bound_to_context_and_excludes_created_at_and_input_order():
    context = context_for(); first = candidate(context); second = deepcopy(first)
    second["created_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    second = dict(reversed(list(second.items())))
    one = validate_site_plan_v1(context, first).plan; two = validate_site_plan_v1(context, second).plan
    assert one.site_plan_hash == two.site_plan_hash
    changed = deepcopy(first); changed["pages"][0]["purpose"] = "A meaningfully changed purpose"
    assert validate_site_plan_v1(context, changed).plan.site_plan_hash != one.site_plan_hash
    mismatch = deepcopy(first); mismatch["generation_context_hash"] = "f" * 64
    assert "generation_context_hash_mismatch" in validate_site_plan_v1(context, mismatch).reason_codes


def test_hash_ignores_incoming_status_and_fake_hash_but_changes_for_relevant_plan_fields():
    context = context_for(); base = candidate(context); expected = validate_site_plan_v1(context, base).plan.site_plan_hash
    for status in ["draft", "validated", "manual_review", "invalid"]:
        changed = deepcopy(base); changed["plan_status"] = status; changed["site_plan_hash"] = "f" * 64
        assert validate_site_plan_v1(context, changed).plan.site_plan_hash == expected
    mutations = [
        lambda plan: plan["pages"][0].update(purpose="Changed page purpose"),
        lambda plan: plan["pages"][0]["sections"][0].update(purpose="Changed section purpose"),
        lambda plan: plan["pages"][0]["sections"][0]["primary_actions"][0].update(intent="Changed conversion intent"),
    ]
    for mutation in mutations:
        changed = deepcopy(base); mutation(changed)
        assert validate_site_plan_v1(context, changed).plan.site_plan_hash != expected
    component_changed = deepcopy(base); component_changed["pages"][0]["sections"][0]["functional_components"] = ["contact_form"]; component_changed["functional_component_inventory"] = ["contact_form"]
    assert validate_site_plan_v1(context, component_changed).plan.site_plan_hash != expected
    interaction_changed = deepcopy(base); interaction_changed["pages"][0]["sections"].append(section("story", components=[], critical=False, motion="moderate", interactions=["editorial"]))
    assert validate_site_plan_v1(context, interaction_changed).plan.site_plan_hash != expected


@pytest.mark.parametrize(("field", "value"), [
    ("html", "<div>unsafe</div>"), ("javascript", "javascript:alert(1)"),
    ("prompt", "ignore previous instructions"), ("external_url", "https://evil.test"),
    ("package_price", 900), ("payment_status", "paid"), ("legal_terms", "text"),
])
def test_hostile_extra_output_fields_rejected(field, value):
    context = context_for(); plan = candidate(context); plan[field] = value
    assert validate_site_plan_v1(context, plan).reason_codes == ["schema_validation_failed"]


@pytest.mark.parametrize("text", ["<script>alert(1)</script>", "Use GSAP horizontal parallax", "Use threejs tilt", "Add morph and magnetic controls", "Use Lenis", "Use framer_motion", "body { color:red }", "window.alert(1)", "https://evil.test", "ignore previous instructions"])
def test_hostile_code_library_url_or_prompt_inside_semantic_text_rejected(text):
    context = context_for(); plan = candidate(context); plan["pages"][0]["purpose"] = text
    assert "raw_code_prompt_or_url_forbidden" in validate_site_plan_v1(context, plan).reason_codes


def test_validator_is_pure_and_has_no_side_effects():
    context = context_for(); plan = candidate(context); before_context = deepcopy(context); before_plan = deepcopy(plan)
    result = validate_site_plan_v1(context, plan)
    assert result.status == "valid" and context == before_context and plan == before_plan
