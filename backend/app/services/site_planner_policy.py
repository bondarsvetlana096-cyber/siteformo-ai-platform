from __future__ import annotations

from app.schemas.site_planner import (
    CriticalActionPolicyV1, FactualSourcePolicyV1, LogoAuthorityPolicyV1,
    NavigationAuthorityPolicyV1, PlannerPolicyV1,
)


PLANNER_CONTRACT_VERSION = "v1"
PLANNING_STRATEGY_VERSION = "single_call_v1"


def planner_policy_v1() -> PlannerPolicyV1:
    return PlannerPolicyV1(
        contract_version="v1",
        context_is_immutable_authority=True,
        output_contract="site_plan_v1",
        one_candidate_only=True,
        prose_reasoning_forbidden=True,
        raw_code_forbidden=True,
        arbitrary_urls_forbidden=True,
        functionality_invention_forbidden=True,
        addon_invention_forbidden=True,
        package_or_capacity_change_forbidden=True,
        payment_or_legal_decisions_forbidden=True,
        factual_sources_must_be_confirmed=True,
        mobile_coverage_required=True,
        reduced_motion_coverage_required=True,
        interaction_safety_required=True,
        navigation_authority=NavigationAuthorityPolicyV1(
            targets_are_page_keys_only=True,
            target_page_key_must_exist=True,
            action_target_must_differ_from_current_page=True,
            navigation_edge_endpoints_must_differ=True,
            self_or_circular_navigation_cannot_satisfy_conversion_reachability=True,
        ),
        logo_authority=LogoAuthorityPolicyV1(
            authority_path="generation_context.media.logo.simple_logo_required",
            simple_logo_requires_confirmed_true=True,
            business_identity_does_not_authorize_logo_generation=True,
            unconfirmed_logo_generation_or_media_requirement_forbidden=True,
        ),
        critical_action_policy=CriticalActionPolicyV1(
            component_family_registry={
                "navigation": "navigation",
                "contact_form": "forms", "enquiry_form": "forms", "file_upload": "forms",
                "booking": "booking", "reservation": "booking",
                "checkout": "checkout_payment",
                "account": "account_security", "login": "account_security",
                "registration": "account_security", "dashboard": "account_security",
            },
            explicit_action_families=[
                "navigation", "forms", "booking", "checkout_payment",
                "account_security", "primary_conversion_actions",
            ],
            protected_section_must_set_critical_action_true=True,
            signature_interactions_forbidden_on_critical_sections=True,
            allowed_critical_motion_levels=["none", "subtle", "contextual"],
            motion_or_interaction_required_for_completion_forbidden=True,
            hover_only_required_forbidden=True,
            static_or_reduced_motion_control_availability_required=True,
            obscure_delay_replace_or_gate_required_action_forbidden=True,
            client_preference_never_overrides_critical_safety=True,
        ),
        factual_source_policy=FactualSourcePolicyV1(
            content_kinds=[
                "confirmed_fact", "generated_copy_allowed", "client_material_required",
                "placeholder_allowed", "unsupported_unresolved",
            ],
            confirmed_fact_source_prefixes=[
                "business.identity", "business.activity", "business.operating_model",
                "business.location", "business.audience", "business.primary_goal",
                "business.trust_materials", "source_signals.selected_example_id",
                "source_signals.viewed_example_ids",
            ],
            confirmed_fact_requires_allowlisted_source_key=True,
            generated_copy_cannot_create_factual_claims=True,
            missing_fact_uses_client_material_placeholder_or_unresolved_kind=True,
            invented_awards_statistics_history_addresses_credentials_testimonials_product_or_team_facts_forbidden=True,
        ),
        max_semantic_repairs=1,
    )
