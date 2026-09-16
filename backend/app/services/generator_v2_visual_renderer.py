"""Pure Generator V2 C4B visual renderer.

This layer decorates the frozen structural renderer output.  It never derives
or changes architecture; the ImplementationSpec and C3 VisualPlan remain the
only authorities for structure and visual decisions respectively.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.services.generator_v2_implementation import GeneratorV2ImplementationSpecV1
from app.services.generator_v2_renderer import (
    GeneratorV2RenderedFileV1,
    GeneratorV2RenderedPageV1,
    GeneratorV2RenderedSiteArtifactV1,
    render_generator_v2_site,
    validate_generator_v2_rendered_site,
)
from app.services.generator_v2_visual import (
    VisualImplementationInputV1,
    VisualImplementationPlanV1,
    validate_visual_implementation_plan_v1,
)

VISUAL_RENDERER_VERSION = "v1"


class _ClosedFrozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


VisualRenderStatus = Literal["READY", "NOT_RENDERABLE"]
VisualRenderReason = Literal[
    "invalid_visual_plan", "visual_plan_identity_mismatch", "visual_validation_failed",
    "structural_validation_failed", "missing_visual_treatment", "unsupported_visual_primitive",
    "critical_action_visual_safety_failure", "responsive_visual_failure",
    "reduced_motion_visual_failure", "visual_artifact_hash_mismatch",
]


class GeneratorV2VisualRenderResultV1(_ClosedFrozen):
    status: VisualRenderStatus
    reason_codes: tuple[VisualRenderReason, ...] = ()
    artifact: GeneratorV2RenderedSiteArtifactV1 | None = None


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


_COLORS = {
    "background": "#ffffff", "surface": "#f7f9fc", "surface_alt": "#eef2f7",
    "text": "#172033", "text_muted": "#526078", "accent": "#145cff",
    "accent_contrast": "#ffffff", "border": "#ccd3dd", "critical_action": "#145cff",
}
_TYPE_SCALE = {"compact": "1.05", "balanced": "1.15", "expressive": "1.3"}
_SPACING = {"compact": "0.75rem", "balanced": "1rem", "airy": "1.35rem"}
_WIDTH = {"narrow": "52rem", "standard": "72rem", "wide": "84rem"}
_RADIUS = {"none": "0", "soft": "0.5rem", "rounded": "1rem"}
_BORDER = {"none": "none", "subtle": "1px solid var(--sfv-border)", "defined": "2px solid var(--sfv-border)"}
_ELEVATION = {"flat": "none", "low": "0 2px 10px rgba(23,32,51,.08)", "medium": "0 8px 24px rgba(23,32,51,.14)"}


def _visual_css(plan: VisualImplementationPlanV1) -> str:
    style = plan.global_style
    colors = ";".join(f"--sfv-{role.replace('_', '-')}: {_COLORS[role]}" for role in style.color_roles)
    button = "var(--sfv-critical-action)" if style.button_treatment in {"solid", "prominent"} else "transparent"
    return (
        f"\n:root{{{colors};--sfv-type-scale:{_TYPE_SCALE[style.typography_scale]};"
        f"--sfv-space:{_SPACING[style.spacing_rhythm]};--sfv-width:{_WIDTH[style.content_width]};"
        f"--sfv-radius:{_RADIUS[style.radius_family]};--sfv-border:{_COLORS['border']};"
        f"--sfv-border-style:{_BORDER[style.border_treatment]};--sfv-elevation:{_ELEVATION[style.elevation_level]};"
        f"--sfv-button:{button}}}"
        f"body[data-siteformo-visual-renderer='v1']{{background:var(--sfv-background);color:var(--sfv-text)}}"
        f"body[data-siteformo-visual-renderer='v1'] main{{max-width:var(--sfv-width);gap:var(--sfv-space)}}"
        f"body[data-siteformo-visual-renderer='v1'] section[data-siteformo-visual-section]{{"
        f"border:var(--sfv-border-style);border-radius:var(--sfv-radius);box-shadow:var(--sfv-elevation);"
        f"padding:calc(var(--sfv-space) * 1.5);margin-block:var(--sfv-space)}}"
        f"body[data-siteformo-visual-renderer='v1'] [data-siteformo-critical='true']{{"
        f"background:var(--sfv-button);color:var(--sfv-accent-contrast);border-radius:var(--sfv-radius);"
        f"padding:.65rem 1rem;min-height:2.75rem;justify-content:center}}"
        f"body[data-siteformo-visual-renderer='v1'] [data-siteformo-visual-section] > h2{{"
        f"font-size:calc(1rem * var(--sfv-type-scale));line-height:1.2}}"
        "@media(max-width:48rem){body[data-siteformo-visual-renderer='v1'] section[data-siteformo-visual-section]{padding:var(--sfv-space)}"
        "body[data-siteformo-visual-renderer='v1'] nav{gap:var(--sfv-space)}}"
        "@media(prefers-reduced-motion:reduce){body[data-siteformo-visual-renderer='v1'] [data-siteformo-visual-section]{transition:none!important;animation:none!important}}\n"
    )


def _decorate_html(html: str, section_plans: dict[str, object]) -> str:
    html = html.replace("<body>", "<body data-siteformo-visual-renderer=\"v1\">")
    pattern = re.compile(r'(<section\b[^>]*data-siteformo-section="([^"]+)"[^>]*)>')

    def replace(match: re.Match[str]) -> str:
        key = match.group(2)
        section = section_plans.get(key)
        if section is None:
            return match.group(0)
        return (match.group(1) + f' data-siteformo-visual-section="{key}"'
                f' data-siteformo-composition="{section.composition}"'
                f' data-siteformo-visual-density="{section.density}"'
                f' data-siteformo-visual-alignment="{section.alignment}">')

    return pattern.sub(replace, html)


def _rebuild_artifact(base: GeneratorV2RenderedSiteArtifactV1, plan: VisualImplementationPlanV1) -> GeneratorV2RenderedSiteArtifactV1:
    css_path = "assets/siteformo-v2.css"
    pages = []
    page_plans = {page.page_key: page for page in plan.pages}
    for page in base.pages:
        visual_page = page_plans[page.page_key]
        decorated = _decorate_html(page.html, {section.section_key: section for section in visual_page.sections})
        page_hash = _sha({"page_key": page.page_key, "route": page.route, "html": decorated,
                          "sections": page.section_keys, "components": page.functional_components,
                          "actions": page.critical_action_keys, "assets": page.asset_references,
                          "contents": page.content_reference_keys})
        pages.append(GeneratorV2RenderedPageV1(
            page_key=page.page_key, route=page.route, html=decorated,
            section_keys=page.section_keys, functional_components=page.functional_components,
            critical_action_keys=page.critical_action_keys, asset_references=page.asset_references,
            content_reference_keys=page.content_reference_keys, page_hash=page_hash,
        ))
    files = []
    for entry in base.artifact_manifest:
        content = entry.content
        if entry.relative_path == css_path:
            content += _visual_css(plan)
        elif entry.source_classification == "page":
            content = next(page.html for page in pages if page.page_key == entry.owner)
        files.append(GeneratorV2RenderedFileV1(
            relative_path=entry.relative_path, media_type=entry.media_type, sha256=hashlib.sha256(content.encode()).hexdigest(),
            byte_length=len(content.encode()), owner=entry.owner, source_classification=entry.source_classification, content=content,
        ))
    material = {
        "contract_version": base.contract_version, "generator_input_hash": base.generator_input_hash,
        "implementation_spec_hash": base.implementation_spec_hash,
        "implementation_operation_key": base.implementation_operation_key,
        "renderer_version": VISUAL_RENDERER_VERSION, "visual_plan_hash": plan.visual_plan_hash,
        "pages": [page.model_dump(mode="json") for page in pages],
        "files": [{key: value for key, value in entry.model_dump(mode="json").items() if key != "content"} for entry in files],
        "routes": list(base.route_manifest),
    }
    return GeneratorV2RenderedSiteArtifactV1(
        contract_version=base.contract_version, generator_input_hash=base.generator_input_hash,
        implementation_spec_hash=base.implementation_spec_hash, implementation_operation_key=base.implementation_operation_key,
        renderer_version=base.renderer_version, pages=tuple(pages), shared_assets=base.shared_assets,
        shared_styles=base.shared_styles, shared_scripts=base.shared_scripts, route_manifest=base.route_manifest,
        artifact_manifest=tuple(files), rendered_site_hash=_sha(material),
    )


def validate_generator_v2_visual_rendered_site(
    spec: GeneratorV2ImplementationSpecV1,
    visual_input: VisualImplementationInputV1,
    plan: VisualImplementationPlanV1,
    artifact: GeneratorV2RenderedSiteArtifactV1,
) -> tuple[VisualRenderReason, ...]:
    visual_validation = validate_visual_implementation_plan_v1(visual_input, plan)
    if visual_validation.status != "VALID":
        return ("visual_validation_failed",)
    structural = validate_generator_v2_rendered_site(spec, artifact)
    if structural.status != "valid":
        return ("structural_validation_failed",)
    plan_pages = {page.page_key: page for page in plan.pages}
    for page in artifact.pages:
        if page.page_key not in plan_pages:
            return ("missing_visual_treatment",)
        expected = plan_pages[page.page_key]
        for section in expected.sections:
            marker = f'data-siteformo-visual-section="{section.section_key}"'
            composition = f'data-siteformo-composition="{section.composition}"'
            if marker not in page.html or composition not in page.html:
                return ("missing_visual_treatment",)
            if section.critical_action_keys and not section.critical_action_safe:
                return ("critical_action_visual_safety_failure",)
            if not section.responsive.preserves_sections or not section.responsive.preserves_functionality:
                return ("responsive_visual_failure",)
            if section.reduced_motion_alternative not in {"static", "instant", "opacity_only"}:
                return ("reduced_motion_visual_failure",)
    css = next((entry.content for entry in artifact.artifact_manifest if entry.relative_path == "assets/siteformo-v2.css"), "")
    if "prefers-reduced-motion" not in css or "--sfv-" not in css:
        return ("reduced_motion_visual_failure",)
    return ()


def render_generator_v2_visual_site(
    spec: GeneratorV2ImplementationSpecV1,
    visual_input: VisualImplementationInputV1,
    plan: VisualImplementationPlanV1,
) -> GeneratorV2VisualRenderResultV1:
    """Apply a C3-validated visual plan to the neutral structural artifact."""
    try:
        base_result = render_generator_v2_site(spec)
        if base_result.status != "READY" or base_result.artifact is None:
            return GeneratorV2VisualRenderResultV1(status="NOT_RENDERABLE", reason_codes=("structural_validation_failed",))
        artifact = _rebuild_artifact(base_result.artifact, plan)
        reasons = validate_generator_v2_visual_rendered_site(spec, visual_input, plan, artifact)
        if reasons:
            return GeneratorV2VisualRenderResultV1(status="NOT_RENDERABLE", reason_codes=reasons)
        return GeneratorV2VisualRenderResultV1(status="READY", artifact=artifact)
    except (TypeError, ValueError, KeyError):
        return GeneratorV2VisualRenderResultV1(status="NOT_RENDERABLE", reason_codes=("invalid_visual_plan",))
