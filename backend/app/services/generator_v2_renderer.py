"""Pure deterministic Renderer Foundation V1 for Generator V2."""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.generator_v2_implementation import (
    GeneratorV2ImplementationPageSpecV1,
    GeneratorV2ImplementationSectionSpecV1,
    GeneratorV2ImplementationSpecV1,
)

RENDERER_VERSION = "v1"


class _FrozenClosed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


RenderStatus = Literal["READY", "NOT_RENDERABLE"]
RenderReason = Literal[
    "invalid_implementation_spec", "extra_page", "missing_page", "duplicate_route",
    "unsafe_route", "broken_internal_link", "unsupported_external_url",
    "missing_required_asset", "render_validation_failed",
]


class GeneratorV2RenderedFileV1(_FrozenClosed):
    relative_path: str
    media_type: Literal["text/html", "text/css", "text/javascript"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0)
    owner: str
    source_classification: Literal["page", "shared"]
    content: str


class GeneratorV2RenderedPageV1(_FrozenClosed):
    page_key: str
    route: str
    html: str
    section_keys: tuple[str, ...]
    functional_components: tuple[str, ...]
    critical_action_keys: tuple[str, ...]
    asset_references: tuple[str, ...]
    content_reference_keys: tuple[str, ...]
    page_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GeneratorV2RenderedSiteArtifactV1(_FrozenClosed):
    contract_version: Literal["v1"]
    generator_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer_version: Literal["v1"]
    pages: tuple[GeneratorV2RenderedPageV1, ...] = Field(min_length=1)
    shared_assets: tuple[str, ...]
    shared_styles: tuple[str, ...]
    shared_scripts: tuple[str, ...]
    route_manifest: tuple[tuple[str, str], ...]
    artifact_manifest: tuple[GeneratorV2RenderedFileV1, ...]
    rendered_site_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GeneratorV2RenderedSiteResultV1(_FrozenClosed):
    status: RenderStatus
    reason_codes: tuple[RenderReason, ...] = ()
    artifact: GeneratorV2RenderedSiteArtifactV1 | None = None


class GeneratorV2RenderValidationResultV1(_FrozenClosed):
    status: Literal["valid", "invalid"]
    reason_codes: tuple[RenderReason, ...] = ()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file(path: str, media_type: str, content: str, owner: str, source: str) -> GeneratorV2RenderedFileV1:
    return GeneratorV2RenderedFileV1(
        relative_path=path, media_type=media_type,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        byte_length=len(content.encode("utf-8")), owner=owner, source_classification=source,
        content=content,
    )


SHARED_CSS = """:root{--sf-focus:#145cff;--sf-border:#ccd3dd;--sf-ink:#1f2937}*{box-sizing:border-box}html{font-family:system-ui,sans-serif;color:var(--sf-ink)}body{margin:0;line-height:1.5}main{display:block;max-width:72rem;margin:auto;padding:clamp(1rem,3vw,3rem)}section{padding:clamp(1rem,3vw,2.5rem) 0;border-bottom:1px solid var(--sf-border)}a,button{min-height:2.75rem;display:inline-flex;align-items:center}a:focus-visible,button:focus-visible{outline:3px solid var(--sf-focus);outline-offset:2px}[data-siteformo-component]{margin-top:.75rem;padding:.75rem;border:1px dashed var(--sf-border)}[data-siteformo-placeholder]{font-style:italic;color:#5b6470}@media(max-width:48rem){main{padding:1rem}nav{display:flex;flex-wrap:wrap;gap:.5rem}}@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition-duration:.01ms!important;animation-duration:.01ms!important}}\n"""
SHARED_JS = "(function(){\"use strict\";document.querySelectorAll('[data-siteformo-nav-toggle]').forEach(function(toggle){toggle.addEventListener('click',function(){var target=document.getElementById(toggle.getAttribute('aria-controls'));if(target){var open=target.hidden;target.hidden=!open;toggle.setAttribute('aria-expanded',String(open));}});});})();\n"


def _safe_path(route: str) -> str:
    if not route.startswith("/") or ".." in route or "\\" in route or re.search(r"[^a-zA-Z0-9/_-]", route):
        raise ValueError("unsafe_route")
    return "index.html" if route == "/" else f"{route.strip('/')}/index.html"


def _render_component(component: str) -> str:
    safe = html.escape(component, quote=True)
    return f'<div data-siteformo-component="{safe}" data-siteformo-runtime="not-connected"><span data-siteformo-placeholder="true">{safe} implementation placeholder</span></div>'


def _render_action(action) -> str:
    label = html.escape(action.intent, quote=True)
    key = html.escape(action.action_key, quote=True)
    if action.target_page_key:
        target = html.escape(action.target_page_key, quote=True)
        return f'<a href="#{target}" data-siteformo-critical-action="{key}" data-siteformo-critical="true">{label}</a>'
    return f'<button type="button" data-siteformo-critical-action="{key}" data-siteformo-critical="true">{label}</button>'


def _render_section(section: GeneratorV2ImplementationSectionSpecV1) -> str:
    key = html.escape(section.section_key, quote=True)
    body = [f'<section data-siteformo-section="{key}" data-siteformo-primitive="{html.escape(section.primitive_key, quote=True)}">']
    body.append(f'<h2>{html.escape(section.purpose, quote=True)}</h2>')
    for content in section.content_requirements:
        body.append(f'<div data-siteformo-content-reference="{html.escape(content.content_key, quote=True)}" data-siteformo-content-kind="{html.escape(content.kind, quote=True)}"><span data-siteformo-placeholder="true">Authorized content placeholder</span></div>')
    for media in section.media_requirements:
        if media.asset_key:
            body.append(f'<div data-siteformo-asset="{html.escape(media.asset_key, quote=True)}" data-siteformo-media-key="{html.escape(media.media_key, quote=True)}"></div>')
        elif media.authorized:
            raise ValueError("missing_required_asset")
    for component in section.functional_components:
        body.append(_render_component(component))
    for action in section.primary_actions:
        body.append(_render_action(action))
    body.append("</section>")
    return "".join(body)


def _render_page(page: GeneratorV2ImplementationPageSpecV1):
    sections = tuple(_render_section(section) for section in page.sections)
    section_keys = tuple(section.section_key for section in page.sections)
    components = tuple(dict.fromkeys(component for section in page.sections for component in section.functional_components))
    actions = tuple(action.action_key for section in page.sections for action in section.primary_actions)
    assets = tuple(asset.asset_key for section in page.sections for asset in section.media_requirements if asset.asset_key)
    contents = tuple(content.content_key for section in page.sections for content in section.content_requirements)
    nav = "".join(f'<a href="#{html.escape(target, quote=True)}">{html.escape(target, quote=True)}</a>' for target in page.navigation_targets)
    page_key = html.escape(page.page_key, quote=True)
    page_html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="stylesheet" href="/assets/siteformo-v2.css"></head><body><main data-siteformo-page="'
        + page_key + '"><nav aria-label="Primary">' + nav + "</nav>" + "".join(sections)
        + '</main><script src="/assets/siteformo-v2.js" defer></script></body></html>'
    )
    return page_html, section_keys, components, actions, assets, contents


def validate_generator_v2_rendered_site(
    spec: GeneratorV2ImplementationSpecV1,
    artifact: GeneratorV2RenderedSiteArtifactV1,
) -> GeneratorV2RenderValidationResultV1:
    if tuple(page.page_key for page in artifact.pages) != tuple(page.page_key for page in spec.pages):
        return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("missing_page",))
    routes = [page.route for page in artifact.pages]
    if len(routes) != len(set(routes)):
        return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("duplicate_route",))
    expected_routes = {page.page_key: page.route_path for page in spec.pages}
    page_keys = set(expected_routes)
    for page in artifact.pages:
        try:
            safe_path = _safe_path(page.route)
        except ValueError:
            return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("unsafe_route",))
        if page.route != expected_routes[page.page_key] or not safe_path:
            return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("unsafe_route",))
        source = next(item for item in spec.pages if item.page_key == page.page_key)
        expected_page_hash = _sha({"page_key": page.page_key, "route": page.route, "html": page.html, "sections": page.section_keys, "components": page.functional_components, "actions": page.critical_action_keys, "assets": page.asset_references, "contents": page.content_reference_keys})
        if page.page_hash != expected_page_hash:
            return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("render_validation_failed",))
        if page.section_keys != tuple(section.section_key for section in source.sections):
            return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("render_validation_failed",))
        if "data-siteformo-section" not in page.html or ("data-siteformo-critical-action" not in page.html and any(section.critical_action for section in source.sections)):
            return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("render_validation_failed",))
        if re.search(r"(?:href|src)=\"(?:javascript:|https?://)", page.html, re.I):
            return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("unsupported_external_url",))
        if any(target not in page_keys for target in re.findall(r'href="#([^\"]+)"', page.html)):
            return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("broken_internal_link",))
        if "<main" not in page.html or "<section" not in page.html:
            return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("render_validation_failed",))
    paths = {entry.relative_path for entry in artifact.artifact_manifest}
    if "assets/siteformo-v2.css" not in paths or "assets/siteformo-v2.js" not in paths:
        return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("render_validation_failed",))
    css = next(entry.content for entry in artifact.artifact_manifest if entry.relative_path == "assets/siteformo-v2.css")
    if "prefers-reduced-motion" not in css or "@media" not in css:
        return GeneratorV2RenderValidationResultV1(status="invalid", reason_codes=("render_validation_failed",))
    return GeneratorV2RenderValidationResultV1(status="valid")


def render_generator_v2_site(spec: GeneratorV2ImplementationSpecV1) -> GeneratorV2RenderedSiteResultV1:
    try:
        canonical = GeneratorV2ImplementationSpecV1.model_validate(spec.model_dump(mode="json"))
        pages = []
        files = [
            _file("assets/siteformo-v2.css", "text/css", SHARED_CSS, "shared", "shared"),
            _file("assets/siteformo-v2.js", "text/javascript", SHARED_JS, "shared", "shared"),
        ]
        routes = []
        for page in canonical.pages:
            path = _safe_path(page.route_path)
            page_html, sections, components, actions, assets, contents = _render_page(page)
            page_hash = _sha({"page_key": page.page_key, "route": page.route_path, "html": page_html, "sections": sections, "components": components, "actions": actions, "assets": assets, "contents": contents})
            pages.append(GeneratorV2RenderedPageV1(
                page_key=page.page_key, route=page.route_path, html=page_html,
                section_keys=sections, functional_components=components,
                critical_action_keys=actions, asset_references=assets,
                content_reference_keys=contents, page_hash=page_hash,
            ))
            files.append(_file(path, "text/html", page_html, page.page_key, "page"))
            routes.append((page.route_path, path))
        material = {
            "contract_version": "v1", "generator_input_hash": canonical.generator_input_hash,
            "implementation_spec_hash": canonical.implementation_spec_hash,
            "implementation_operation_key": canonical.implementation_operation_key,
            "renderer_version": RENDERER_VERSION,
            "pages": [page.model_dump(mode="json") for page in pages],
            "files": [{key: value for key, value in entry.model_dump(mode="json").items() if key != "content"} for entry in files],
            "routes": routes,
        }
        artifact = GeneratorV2RenderedSiteArtifactV1(
            contract_version="v1", generator_input_hash=canonical.generator_input_hash,
            implementation_spec_hash=canonical.implementation_spec_hash,
            implementation_operation_key=canonical.implementation_operation_key,
            renderer_version="v1", pages=tuple(pages), shared_assets=(),
            shared_styles=("assets/siteformo-v2.css",), shared_scripts=("assets/siteformo-v2.js",),
            route_manifest=tuple(routes), artifact_manifest=tuple(files), rendered_site_hash=_sha(material),
        )
    except (AttributeError, ValueError, TypeError):
        return GeneratorV2RenderedSiteResultV1(status="NOT_RENDERABLE", reason_codes=("invalid_implementation_spec",))
    validation = validate_generator_v2_rendered_site(canonical, artifact)
    if validation.status != "valid":
        return GeneratorV2RenderedSiteResultV1(status="NOT_RENDERABLE", reason_codes=("render_validation_failed",))
    return GeneratorV2RenderedSiteResultV1(status="READY", artifact=artifact)
