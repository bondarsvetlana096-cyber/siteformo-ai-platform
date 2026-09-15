"""Pure Generator V2 Phase C implementation specification adapter.

Only the verified Phase A snapshot is accepted.  This module intentionally has
no database, filesystem, provider, queue, worker, or legacy-generator
dependencies.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.site_plan import (
    CriticalActionFamily,
    FunctionalComponentKey,
    InteractionFamily,
    MotionLevel,
    PageRole,
)
from app.services.generator_v2_contract import (
    AuthorizedAssetReferenceV1,
    AuthorizedContentReferenceV1,
    GeneratorV2InputSnapshotV1,
    generator_v2_implementation_operation_key,
)


IMPLEMENTATION_SPEC_CONTRACT_VERSION = "v1"
IMPLEMENTATION_PRIMITIVE_REGISTRY_VERSION = "v1"


class _ClosedFrozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


ImplementationStatus = Literal["READY_TO_RENDER", "NOT_IMPLEMENTABLE", "INVALID_OR_STALE_INPUT"]
ImplementationReason = Literal[
    "unsupported_section_primitive", "unsupported_functional_primitive",
    "route_mapping_failure", "critical_action_primitive_missing",
    "media_binding_unavailable", "content_binding_unavailable", "stale_snapshot",
    "corrupt_snapshot", "page_set_mismatch", "section_inventory_mismatch",
    "functional_component_mismatch", "navigation_integrity_failure",
    "critical_action_mismatch", "interaction_safety_mismatch", "scope_expansion",
]


class GeneratorV2ImplementationActionSpecV1(_ClosedFrozen):
    action_key: str
    intent: str
    target_page_key: str | None = None
    critical_family: CriticalActionFamily | None = None


class GeneratorV2ImplementationAssetBindingV1(_ClosedFrozen):
    media_key: str
    asset_key: str | None = None
    asset_kind: str | None = None
    authorized: bool


class GeneratorV2ImplementationContentBindingV1(_ClosedFrozen):
    content_key: str
    kind: str
    source_key: str | None = None
    authorized_reference_key: str | None = None


class GeneratorV2ImplementationSectionSpecV1(_ClosedFrozen):
    section_key: str
    purpose: str
    primitive_key: str
    functional_components: tuple[FunctionalComponentKey, ...]
    primary_actions: tuple[GeneratorV2ImplementationActionSpecV1, ...]
    content_requirements: tuple[GeneratorV2ImplementationContentBindingV1, ...]
    media_requirements: tuple[GeneratorV2ImplementationAssetBindingV1, ...]
    interaction_families: tuple[InteractionFamily, ...]
    signature_interaction_keys: tuple[str, ...]
    motion_level: MotionLevel
    critical_action: bool
    implementation_validation_requirements: tuple[str, ...]


class GeneratorV2ImplementationPageSpecV1(_ClosedFrozen):
    page_key: str
    page_role: PageRole
    route_path: str
    purpose: str
    navigation_targets: tuple[str, ...]
    sections: tuple[GeneratorV2ImplementationSectionSpecV1, ...]
    mobile_strategy: str
    reduced_motion_strategy: str


class GeneratorV2ImplementationSpecV1(_ClosedFrozen):
    contract_version: Literal["v1"]
    generator_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    site_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_strategy_version: Literal["v1"]
    primitive_registry_version: Literal["v1"]
    pages: tuple[GeneratorV2ImplementationPageSpecV1, ...] = Field(min_length=1)
    navigation: tuple[tuple[str, str, str], ...]
    implementation_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GeneratorV2ImplementationResultV1(_ClosedFrozen):
    status: ImplementationStatus
    reason_codes: tuple[ImplementationReason, ...] = ()
    spec: GeneratorV2ImplementationSpecV1 | None = None


class GeneratorV2ImplementationValidationResultV1(_ClosedFrozen):
    status: Literal["valid", "invalid"]
    reason_codes: tuple[ImplementationReason, ...] = ()


_SECTION_PRIMITIVES = {
    "hero": "hero_section",
    "services": "services_section",
    "trust": "trust_section",
    "about": "about_section",
    "faq": "faq_section",
    "cta": "cta_section",
    "gallery": "gallery_section",
    "contact": "contact_section",
    "booking": "booking_section",
    "catalogue": "catalogue_section",
    "products": "products_section",
    "account": "account_section",
    "dashboard": "dashboard_section",
    "content_archive": "content_archive_section",
    "video_entry": "video_entry_section",
    "location": "location_section",
    "footer": "footer_section",
    "navigation": "navigation_section",
}

_FUNCTIONAL_PRIMITIVES = {
    "navigation": "navigation_control", "contact_form": "contact_form",
    "enquiry_form": "enquiry_form", "direct_contact": "direct_contact",
    "newsletter": "newsletter_form", "booking": "booking_form",
    "reservation": "reservation_form", "catalogue": "catalogue_view",
    "product_list": "product_list", "product_detail": "product_detail",
    "cart": "cart", "checkout": "checkout_payment", "search": "search_control",
    "filters": "filter_control", "comparison": "comparison_view",
    "file_upload": "file_upload", "content_archive": "content_archive",
    "account": "account_area", "login": "login_form", "registration": "registration_form",
    "saved_items": "saved_items", "alerts": "alerts", "dashboard": "dashboard",
    "multilingual_switcher": "language_switcher",
}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _route_path(page_key: str, route_intent: str) -> str:
    if page_key == "home":
        return "/"
    slug = route_intent.replace("_", "-").strip("-")
    if not slug:
        raise ValueError("route_mapping_failure")
    return "/" + slug


def _action(action) -> GeneratorV2ImplementationActionSpecV1:
    return GeneratorV2ImplementationActionSpecV1(
        action_key=action.action_key, intent=action.intent,
        target_page_key=action.target_page_key, critical_family=action.critical_family,
    )


def _content_bindings(section, authorized: dict[str, AuthorizedContentReferenceV1]):
    return tuple(
        GeneratorV2ImplementationContentBindingV1(
            content_key=item.content_key, kind=item.kind, source_key=item.source_key,
            authorized_reference_key=authorized[item.content_key].content_key if item.content_key in authorized else None,
        )
        for item in section.content_requirements
    )


def _asset_bindings(section, authorized: dict[str, AuthorizedAssetReferenceV1]):
    bindings = []
    for item in section.media_requirements:
        asset = authorized.get(item.media_key)
        if item.required and item.kind != "no_media" and asset is None:
            raise ValueError("media_binding_unavailable")
        bindings.append(GeneratorV2ImplementationAssetBindingV1(
            media_key=item.media_key, asset_key=asset.asset_key if asset else None,
            asset_kind=asset.kind if asset else None, authorized=asset is not None,
        ))
    return tuple(bindings)


def _spec_material(spec: dict) -> dict:
    material = dict(spec)
    material.pop("implementation_spec_hash", None)
    return material


def validate_generator_v2_implementation_spec(
    snapshot: GeneratorV2InputSnapshotV1, spec: GeneratorV2ImplementationSpecV1,
) -> GeneratorV2ImplementationValidationResultV1:
    """Independently prove that implementation did not change architecture."""
    plan = snapshot.site_plan
    if tuple(page.page_key for page in plan.pages) != tuple(page.page_key for page in spec.pages):
        return GeneratorV2ImplementationValidationResultV1(status="invalid", reason_codes=("page_set_mismatch",))
    plan_pages = {page.page_key: page for page in plan.pages}
    for page in spec.pages:
        source = plan_pages[page.page_key]
        if page.page_role != source.page_role or page.purpose != source.purpose or page.route_path != _route_path(source.page_key, source.route_intent):
            return GeneratorV2ImplementationValidationResultV1(status="invalid", reason_codes=("route_mapping_failure",))
        if tuple(section.section_key for section in source.sections) != tuple(section.section_key for section in page.sections):
            return GeneratorV2ImplementationValidationResultV1(status="invalid", reason_codes=("section_inventory_mismatch",))
        for source_section, impl_section in zip(source.sections, page.sections):
            if source_section.purpose != impl_section.purpose:
                return GeneratorV2ImplementationValidationResultV1(status="invalid", reason_codes=("section_inventory_mismatch",))
            if tuple(source_section.functional_components) != impl_section.functional_components:
                return GeneratorV2ImplementationValidationResultV1(status="invalid", reason_codes=("functional_component_mismatch",))
            if source_section.critical_action != impl_section.critical_action:
                return GeneratorV2ImplementationValidationResultV1(status="invalid", reason_codes=("critical_action_mismatch",))
            if any(action.target_page_key is not None and action.target_page_key not in plan_pages for action in source_section.primary_actions):
                return GeneratorV2ImplementationValidationResultV1(status="invalid", reason_codes=("navigation_integrity_failure",))
            if source_section.critical_action and "keyboard_operable" not in impl_section.implementation_validation_requirements:
                return GeneratorV2ImplementationValidationResultV1(status="invalid", reason_codes=("interaction_safety_mismatch",))
    expected_nav = tuple((edge.from_page_key, edge.to_page_key, edge.purpose) for edge in plan.cross_page_navigation)
    if spec.navigation != expected_nav:
        return GeneratorV2ImplementationValidationResultV1(status="invalid", reason_codes=("navigation_integrity_failure",))
    return GeneratorV2ImplementationValidationResultV1(status="valid")


def build_generator_v2_implementation_spec(
    snapshot: GeneratorV2InputSnapshotV1,
) -> GeneratorV2ImplementationResultV1:
    """Purely project the already validated plan into renderer requirements."""
    try:
        canonical = GeneratorV2InputSnapshotV1.model_validate(snapshot.model_dump(mode="json"))
    except Exception:
        return GeneratorV2ImplementationResultV1(status="INVALID_OR_STALE_INPUT", reason_codes=("corrupt_snapshot",))
    if canonical.site_plan.plan_status != "validated":
        return GeneratorV2ImplementationResultV1(status="INVALID_OR_STALE_INPUT", reason_codes=("stale_snapshot",))
    assets = {item.asset_key: item for item in canonical.authorized_assets}
    content = {item.content_key: item for item in canonical.authorized_content}
    try:
        pages = []
        routes = set()
        page_keys = {page.page_key for page in canonical.site_plan.pages}
        for page in canonical.site_plan.pages:
            route = _route_path(page.page_key, page.route_intent)
            if route in routes:
                raise ValueError("route_mapping_failure")
            routes.add(route)
            sections = []
            for section in page.sections:
                primitive = _SECTION_PRIMITIVES.get(section.section_key)
                if primitive is None:
                    raise ValueError("unsupported_section_primitive")
                if any(component not in _FUNCTIONAL_PRIMITIVES for component in section.functional_components):
                    raise ValueError("unsupported_functional_primitive")
                requirements = (
                    "keyboard_operable", "touch_safe", "no_hover_only", "reduced_motion_safe",
                    "critical_action_stable", "signature_interaction_not_required",
                ) if section.critical_action else ("reduced_motion_safe",)
                sections.append(GeneratorV2ImplementationSectionSpecV1(
                    section_key=section.section_key, purpose=section.purpose, primitive_key=primitive,
                    functional_components=tuple(section.functional_components),
                    primary_actions=tuple(_action(action) for action in section.primary_actions),
                    content_requirements=_content_bindings(section, content),
                    media_requirements=_asset_bindings(section, assets),
                    interaction_families=tuple(section.interaction_families),
                    signature_interaction_keys=tuple(item.interaction_key for item in section.signature_interactions),
                    motion_level=section.motion_policy.level, critical_action=section.critical_action,
                    implementation_validation_requirements=requirements,
                ))
            pages.append(GeneratorV2ImplementationPageSpecV1(
                page_key=page.page_key, page_role=page.page_role, route_path=route,
                purpose=page.purpose,
                navigation_targets=tuple(edge.to_page_key for edge in canonical.site_plan.cross_page_navigation if edge.from_page_key == page.page_key),
                sections=tuple(sections), mobile_strategy=page.mobile_behavior.strategy,
                reduced_motion_strategy=page.reduced_motion_behavior.strategy,
            ))
        if any(edge.from_page_key not in page_keys or edge.to_page_key not in page_keys or edge.from_page_key == edge.to_page_key for edge in canonical.site_plan.cross_page_navigation):
            raise ValueError("navigation_integrity_failure")
        if any(action.target_page_key is not None and (action.target_page_key not in page_keys or action.target_page_key == page.page_key)
               for page in canonical.site_plan.pages for section in page.sections for action in section.primary_actions):
            raise ValueError("navigation_integrity_failure")
        material = {
            "contract_version": IMPLEMENTATION_SPEC_CONTRACT_VERSION,
            "generator_input_hash": canonical.generator_input_hash,
            "implementation_operation_key": generator_v2_implementation_operation_key(canonical),
            "site_plan_hash": canonical.site_plan_hash,
            "implementation_strategy_version": "v1",
            "primitive_registry_version": IMPLEMENTATION_PRIMITIVE_REGISTRY_VERSION,
            "pages": [page.model_dump(mode="json") for page in pages],
            "navigation": [list(edge) for edge in ((e.from_page_key, e.to_page_key, e.purpose) for e in canonical.site_plan.cross_page_navigation)],
        }
        payload = dict(material)
        payload["implementation_spec_hash"] = _sha(_spec_material(material))
        spec = GeneratorV2ImplementationSpecV1.model_validate(payload)
    except ValueError as exc:
        reason = str(exc)
        allowed = {item for item in ImplementationReason.__args__}
        return GeneratorV2ImplementationResultV1(
            status="NOT_IMPLEMENTABLE" if reason in allowed else "INVALID_OR_STALE_INPUT",
            reason_codes=(reason if reason in allowed else "corrupt_snapshot",),
        )
    validation = validate_generator_v2_implementation_spec(canonical, spec)
    if validation.status != "valid":
        return GeneratorV2ImplementationResultV1(status="INVALID_OR_STALE_INPUT", reason_codes=validation.reason_codes)
    return GeneratorV2ImplementationResultV1(status="READY_TO_RENDER", spec=spec)
