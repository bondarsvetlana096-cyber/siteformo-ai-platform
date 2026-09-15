"""Generator V2 Phase C3 visual implementation authority boundary.

This module projects visual decisions from the immutable implementation spec.
It deliberately contains no renderer, provider, filesystem, or runtime code.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.generator_v2_contract import GeneratorV2InputSnapshotV1
from app.services.generator_v2_implementation import (
    GeneratorV2ImplementationSpecV1,
    validate_generator_v2_implementation_spec,
)


VISUAL_STRATEGY_VERSION = "v1"
RENDERER_CONTRACT_VERSION = "v1"


class _ClosedFrozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


DesignDirection = Literal[
    "clean-modern", "premium-business", "bold-startup", "luxury-elite",
    "tech-minimal", "creative-studio", "nordic-soft", "dark-contrast",
]
InteractionPreference = Literal["subtle", "recommended", "more_expressive"]
CompositionPrimitive = Literal[
    "centered_hero", "split_media", "asymmetric_split", "editorial_stack",
    "card_grid", "horizontal_rail", "feature_band", "framed_media",
    "staggered_content", "comparison_layout", "dashboard_shell",
    "catalogue_grid", "focused_form", "immersive_panel",
]
Alignment = Literal["start", "center", "end"]
Density = Literal["airy", "balanced", "dense"]
Hierarchy = Literal["quiet", "clear", "emphatic"]
TypographyTreatment = Literal["display_led", "editorial", "functional", "compact"]
SurfaceTreatment = Literal["flat", "soft", "framed", "elevated", "contrast"]
MediaTreatment = Literal["none", "contained", "cover", "framed", "focal"]
DecorativeTreatment = Literal["none", "minimal", "layered", "bounded"]
InteractionRealization = Literal["static", "subtle_transition", "bounded_reveal", "rich_noncritical"]
ReducedMotionAlternative = Literal["static", "instant", "opacity_only"]
ResponsiveAdaptation = Literal["stack", "compress", "reflow", "scroll_safe"]
TypographyScale = Literal["compact", "balanced", "expressive"]
SpacingRhythm = Literal["compact", "balanced", "airy"]
ContentWidth = Literal["narrow", "standard", "wide"]
RadiusFamily = Literal["none", "soft", "rounded"]
BorderTreatment = Literal["none", "subtle", "defined"]
ElevationLevel = Literal["flat", "low", "medium"]
ImageTreatmentToken = Literal["natural", "soft_crop", "framed", "full_bleed"]
ButtonTreatment = Literal["textual", "outlined", "solid", "prominent"]
NavigationTreatment = Literal["inline", "compact", "layered"]
ColorRole = Literal[
    "background", "surface", "surface_alt", "text", "text_muted", "accent",
    "accent_contrast", "border", "critical_action",
]


class VisualSectionInputV1(_ClosedFrozen):
    section_key: str
    purpose: str
    critical_action: bool
    critical_action_keys: tuple[str, ...]
    functional_components: tuple[str, ...]
    interaction_families: tuple[str, ...]
    media_reference_keys: tuple[str, ...]
    authorized_asset_keys: tuple[str, ...]
    content_reference_keys: tuple[str, ...]
    mobile_preserves_content: Literal[True] = True
    reduced_motion_required: Literal[True] = True


class VisualPageInputV1(_ClosedFrozen):
    page_key: str
    route: str
    sections: tuple[VisualSectionInputV1, ...]


class VisualImplementationInputV1(_ClosedFrozen):
    contract_version: Literal["v1"]
    generator_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    site_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_strategy_version: Literal["v1"]
    renderer_contract_version: Literal["v1"]
    design_direction: DesignDirection
    design_direction_contract_version: Literal["v1"]
    interaction_preference: InteractionPreference
    interaction_preference_contract_version: Literal["v1"]
    visual_reference_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    authorized_asset_keys: tuple[str, ...] = ()
    authorized_content_keys: tuple[str, ...] = ()
    pages: tuple[VisualPageInputV1, ...] = Field(min_length=1)
    structural_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class VisualGlobalStyleV1(_ClosedFrozen):
    typography_scale: TypographyScale
    spacing_rhythm: SpacingRhythm
    content_width: ContentWidth
    radius_family: RadiusFamily
    border_treatment: BorderTreatment
    elevation_level: ElevationLevel
    image_treatment: ImageTreatmentToken
    button_treatment: ButtonTreatment
    navigation_treatment: NavigationTreatment
    color_roles: tuple[ColorRole, ...] = (
        "background", "surface", "surface_alt", "text", "text_muted",
        "accent", "accent_contrast", "border", "critical_action",
    )


class VisualResponsiveRuleV1(_ClosedFrozen):
    desktop: ResponsiveAdaptation
    tablet: ResponsiveAdaptation
    mobile: ResponsiveAdaptation
    preserves_sections: Literal[True] = True
    preserves_functionality: Literal[True] = True


class VisualSectionPlanV1(_ClosedFrozen):
    section_key: str
    composition: CompositionPrimitive
    alignment: Alignment
    density: Density
    hierarchy: Hierarchy
    typography: TypographyTreatment
    surface: SurfaceTreatment
    media_treatment: MediaTreatment
    decorative_treatment: DecorativeTreatment
    interaction_realization: InteractionRealization
    responsive: VisualResponsiveRuleV1
    reduced_motion_alternative: ReducedMotionAlternative
    critical_action_safe: Literal[True] = True
    critical_action_keys: tuple[str, ...]
    authorized_asset_keys: tuple[str, ...] = ()
    content_reference_keys: tuple[str, ...] = ()


class VisualPagePlanV1(_ClosedFrozen):
    page_key: str
    sections: tuple[VisualSectionPlanV1, ...]


class VisualImplementationPlanV1(_ClosedFrozen):
    contract_version: Literal["v1"]
    generator_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    site_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_strategy_version: Literal["v1"]
    renderer_contract_version: Literal["v1"]
    design_direction: DesignDirection
    interaction_preference: InteractionPreference
    structural_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    global_style: VisualGlobalStyleV1
    pages: tuple[VisualPagePlanV1, ...] = Field(min_length=1)
    visual_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class VisualImplementationProviderRequestV1(_ClosedFrozen):
    contract_version: Literal["v1"]
    visual_policy_version: Literal["v1"]
    input: VisualImplementationInputV1
    output_contract_version: Literal["v1"]
    allowed_compositions: tuple[CompositionPrimitive, ...]
    forbidden_authority: tuple[str, ...] = (
        "architecture", "functionality", "routes", "critical_actions", "media_authority",
        "raw_html", "raw_css", "javascript", "urls", "prompt", "instructions",
    )


VisualInputStatus = Literal["READY", "INVALID_OR_STALE_INPUT"]
VisualInputReason = Literal[
    "implementation_spec_invalid", "unsupported_contract_version", "structural_fingerprint_mismatch",
]


class VisualImplementationInputResultV1(_ClosedFrozen):
    status: VisualInputStatus
    reason_codes: tuple[VisualInputReason, ...] = ()
    input: VisualImplementationInputV1 | None = None


VisualPlanStatus = Literal["VALID", "INVALID"]
VisualPlanReason = Literal[
    "input_mismatch", "structural_fingerprint_mismatch", "page_set_mismatch",
    "section_inventory_mismatch", "critical_action_safety_mismatch",
    "responsive_preservation_mismatch", "reduced_motion_mismatch",
    "unauthorized_asset", "unauthorized_content", "unsupported_composition",
    "invalid_visual_token", "visual_plan_hash_mismatch",
]


class VisualImplementationPlanValidationResultV1(_ClosedFrozen):
    status: VisualPlanStatus
    reason_codes: tuple[VisualPlanReason, ...] = ()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def structural_fingerprint(spec: GeneratorV2ImplementationSpecV1) -> str:
    """Hash only structural implementation authority, never visual choices."""
    material = {
        "pages": [{
            "page_key": page.page_key,
            "page_role": page.page_role,
            "route": page.route_path,
            "purpose": page.purpose,
            "mobile_strategy": page.mobile_strategy,
            "reduced_motion_strategy": page.reduced_motion_strategy,
            "sections": [{
                "section_key": section.section_key,
                "functional_components": list(section.functional_components),
                "actions": [action.model_dump(mode="json") for action in section.primary_actions],
                "critical_action": section.critical_action,
                "interaction_families": list(section.interaction_families),
                "media": [item.model_dump(mode="json") for item in section.media_requirements],
                "content": [item.model_dump(mode="json") for item in section.content_requirements],
            } for section in page.sections],
        } for page in spec.pages],
        "navigation": [list(edge) for edge in spec.navigation],
    }
    return _sha(material)


def build_visual_implementation_input(
    snapshot: GeneratorV2InputSnapshotV1,
    implementation_spec: GeneratorV2ImplementationSpecV1,
) -> VisualImplementationInputResultV1:
    """Purely expose immutable structural and authorized visual inputs."""
    validation = validate_generator_v2_implementation_spec(snapshot, implementation_spec)
    if validation.status != "valid":
        return VisualImplementationInputResultV1(status="INVALID_OR_STALE_INPUT", reason_codes=("implementation_spec_invalid",))
    fingerprint = structural_fingerprint(implementation_spec)
    pages = []
    for page in implementation_spec.pages:
        sections = []
        for section in page.sections:
            sections.append(VisualSectionInputV1(
                section_key=section.section_key, purpose=section.purpose,
                critical_action=section.critical_action,
                critical_action_keys=tuple(action.action_key for action in section.primary_actions),
                functional_components=tuple(section.functional_components),
                interaction_families=tuple(section.interaction_families),
                media_reference_keys=tuple(item.media_key for item in section.media_requirements),
                authorized_asset_keys=tuple(item.asset_key for item in section.media_requirements if item.asset_key),
                content_reference_keys=tuple(item.content_key for item in section.content_requirements),
            ))
        pages.append(VisualPageInputV1(page_key=page.page_key, route=page.route_path, sections=tuple(sections)))
    result = VisualImplementationInputV1(
        contract_version="v1", generator_input_hash=implementation_spec.generator_input_hash,
        implementation_spec_hash=implementation_spec.implementation_spec_hash,
        site_plan_hash=implementation_spec.site_plan_hash, visual_strategy_version=VISUAL_STRATEGY_VERSION,
        renderer_contract_version=RENDERER_CONTRACT_VERSION,
        design_direction=snapshot.selected_design.design_direction,
        design_direction_contract_version="v1",
        interaction_preference=snapshot.selected_design.interaction_preference,
        interaction_preference_contract_version="v1",
        visual_reference_hash=snapshot.selected_design.visual_reference_hash,
        authorized_asset_keys=tuple(item.asset_key for item in snapshot.authorized_assets),
        authorized_content_keys=tuple(item.content_key for item in snapshot.authorized_content),
        pages=tuple(pages), structural_fingerprint=fingerprint,
    )
    return VisualImplementationInputResultV1(status="READY", input=result)


_DIRECTION_STYLE: dict[DesignDirection, dict[str, str]] = {
    "clean-modern": {"typography_scale": "balanced", "spacing_rhythm": "airy", "content_width": "standard", "surface": "flat", "elevation": "flat"},
    "premium-business": {"typography_scale": "balanced", "spacing_rhythm": "airy", "content_width": "wide", "surface": "elevated", "elevation": "low"},
    "bold-startup": {"typography_scale": "expressive", "spacing_rhythm": "balanced", "content_width": "wide", "surface": "contrast", "elevation": "medium"},
    "luxury-elite": {"typography_scale": "expressive", "spacing_rhythm": "airy", "content_width": "standard", "surface": "framed", "elevation": "low"},
    "tech-minimal": {"typography_scale": "compact", "spacing_rhythm": "balanced", "content_width": "wide", "surface": "flat", "elevation": "low"},
    "creative-studio": {"typography_scale": "expressive", "spacing_rhythm": "airy", "content_width": "wide", "surface": "elevated", "elevation": "medium"},
    "nordic-soft": {"typography_scale": "balanced", "spacing_rhythm": "airy", "content_width": "standard", "surface": "soft", "elevation": "flat"},
    "dark-contrast": {"typography_scale": "expressive", "spacing_rhythm": "balanced", "content_width": "wide", "surface": "contrast", "elevation": "medium"},
}


def _composition(section_key: str) -> CompositionPrimitive:
    return {
        "hero": "centered_hero", "services": "card_grid", "trust": "feature_band",
        "about": "split_media", "faq": "editorial_stack", "cta": "feature_band",
        "gallery": "framed_media", "contact": "focused_form", "booking": "focused_form",
        "catalogue": "catalogue_grid", "products": "catalogue_grid", "account": "dashboard_shell",
        "dashboard": "dashboard_shell", "content_archive": "editorial_stack", "video_entry": "immersive_panel",
        "location": "framed_media", "footer": "feature_band", "navigation": "horizontal_rail",
    }.get(section_key, "editorial_stack")  # ImplementationSpec has already closed the section vocabulary.


def _visual_plan_material(plan: dict) -> dict:
    material = dict(plan)
    material.pop("visual_plan_hash", None)
    return material


def build_default_visual_implementation_plan(
    visual_input: VisualImplementationInputV1,
) -> VisualImplementationPlanV1:
    """Deterministic baseline projection; future models must validate against it."""
    direction = _DIRECTION_STYLE[visual_input.design_direction]
    pref = visual_input.interaction_preference
    interaction = {"subtle": "subtle_transition", "recommended": "bounded_reveal", "more_expressive": "rich_noncritical"}[pref]
    global_style = VisualGlobalStyleV1(
        typography_scale=direction["typography_scale"], spacing_rhythm=direction["spacing_rhythm"],
        content_width=direction["content_width"], radius_family="soft", border_treatment="subtle",
        elevation_level=direction["elevation"], image_treatment="natural", button_treatment="solid",
        navigation_treatment="inline",
    )
    pages = []
    for page in visual_input.pages:
        sections = []
        for section in page.sections:
            critical = section.critical_action
            sections.append(VisualSectionPlanV1(
                section_key=section.section_key, composition=_composition(section.section_key),
                alignment="center" if section.section_key == "hero" else "start",
                density="balanced", hierarchy="emphatic" if critical else "clear",
                typography="display_led" if section.section_key == "hero" else "functional",
                surface="contrast" if critical else "soft", media_treatment="none",
                decorative_treatment="bounded" if not critical else "minimal",
                interaction_realization="static" if critical else interaction,
                responsive=VisualResponsiveRuleV1(desktop="reflow", tablet="reflow", mobile="stack"),
                reduced_motion_alternative="static" if critical else "instant",
                critical_action_keys=section.critical_action_keys,
                authorized_asset_keys=section.authorized_asset_keys,
                content_reference_keys=tuple(section.content_reference_keys),
            ))
        pages.append(VisualPagePlanV1(page_key=page.page_key, sections=tuple(sections)))
    material = {
        "contract_version": "v1", "generator_input_hash": visual_input.generator_input_hash,
        "implementation_spec_hash": visual_input.implementation_spec_hash, "site_plan_hash": visual_input.site_plan_hash,
        "visual_strategy_version": "v1", "renderer_contract_version": "v1",
        "design_direction": visual_input.design_direction, "interaction_preference": visual_input.interaction_preference,
        "structural_fingerprint": visual_input.structural_fingerprint,
        "global_style": global_style.model_dump(mode="json"), "pages": [page.model_dump(mode="json") for page in pages],
    }
    payload = dict(material); payload["visual_plan_hash"] = _sha(material)
    return VisualImplementationPlanV1.model_validate(payload)


def validate_visual_implementation_plan_v1(
    visual_input: VisualImplementationInputV1,
    plan: VisualImplementationPlanV1,
) -> VisualImplementationPlanValidationResultV1:
    try:
        canonical = VisualImplementationPlanV1.model_validate(plan.model_dump(mode="json"))
    except Exception:
        return VisualImplementationPlanValidationResultV1(status="INVALID", reason_codes=("invalid_visual_token",))
    if (
        canonical.generator_input_hash != visual_input.generator_input_hash
        or canonical.implementation_spec_hash != visual_input.implementation_spec_hash
        or canonical.site_plan_hash != visual_input.site_plan_hash
        or canonical.design_direction != visual_input.design_direction
        or canonical.interaction_preference != visual_input.interaction_preference
    ):
        return VisualImplementationPlanValidationResultV1(status="INVALID", reason_codes=("input_mismatch",))
    if canonical.structural_fingerprint != visual_input.structural_fingerprint:
        return VisualImplementationPlanValidationResultV1(status="INVALID", reason_codes=("structural_fingerprint_mismatch",))
    if tuple(page.page_key for page in canonical.pages) != tuple(page.page_key for page in visual_input.pages):
        return VisualImplementationPlanValidationResultV1(status="INVALID", reason_codes=("page_set_mismatch",))
    allowed_assets = set(visual_input.authorized_asset_keys)
    for source_page, visual_page in zip(visual_input.pages, canonical.pages):
        if tuple(section.section_key for section in visual_page.sections) != tuple(section.section_key for section in source_page.sections):
            return VisualImplementationPlanValidationResultV1(status="INVALID", reason_codes=("section_inventory_mismatch",))
        for source, section in zip(source_page.sections, visual_page.sections):
            if (
                tuple(section.critical_action_keys) != tuple(source.critical_action_keys)
                or not section.critical_action_safe
                or (source.critical_action and section.interaction_realization not in {"static", "subtle_transition"})
                or not section.responsive.preserves_sections
                or not section.responsive.preserves_functionality
                or section.reduced_motion_alternative not in {"static", "instant", "opacity_only"}
            ):
                return VisualImplementationPlanValidationResultV1(status="INVALID", reason_codes=("critical_action_safety_mismatch",))
            if not set(section.authorized_asset_keys).issubset(allowed_assets):
                return VisualImplementationPlanValidationResultV1(status="INVALID", reason_codes=("unauthorized_asset",))
            if tuple(section.content_reference_keys) != tuple(source.content_reference_keys):
                return VisualImplementationPlanValidationResultV1(status="INVALID", reason_codes=("unauthorized_content",))
    material = canonical.model_dump(mode="json"); material.pop("visual_plan_hash", None)
    if _sha(material) != canonical.visual_plan_hash:
        return VisualImplementationPlanValidationResultV1(status="INVALID", reason_codes=("visual_plan_hash_mismatch",))
    return VisualImplementationPlanValidationResultV1(status="VALID")


def build_visual_implementation_provider_request(
    visual_input: VisualImplementationInputV1,
) -> VisualImplementationProviderRequestV1:
    return VisualImplementationProviderRequestV1(
        contract_version="v1", visual_policy_version="v1", input=visual_input,
        output_contract_version="v1", allowed_compositions=tuple(CompositionPrimitive.__args__),
    )
