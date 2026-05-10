from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from app.services.package_rules_service import get_generation_budget, get_package_rules, normalize_package


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _first(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return default


def _extract_selected_preview(order: Any) -> Dict[str, Any]:
    selected_design_id = str(_first(
        _get(order, "selected_design_id"),
        _get(order, "selected_preview_id"),
        _get(order, "selected_design_url"),
        default="",
    ))

    previews = _first(
        _get(order, "design_previews"),
        _get(order, "preview_generation_payload"),
        default=[],
    )
    if isinstance(previews, dict):
        previews = previews.get("design_previews") or []

    if isinstance(previews, list):
        for preview in previews:
            if not isinstance(preview, dict):
                continue
            preview_id = str(preview.get("id") or "")
            preview_url = str(preview.get("preview_url") or preview.get("screenshot_url") or preview.get("image_url") or "")
            if selected_design_id and selected_design_id in {preview_id, preview_url}:
                return preview
        if previews and isinstance(previews[0], dict):
            return previews[0]
    return {}


def _extract_qualification(brief: Dict[str, Any], order: Any) -> Dict[str, Any]:
    qualification = _first(
        brief.get("qualification_result"),
        brief.get("website_qualification"),
        brief.get("website_analysis"),
        brief.get("analysis_result"),
        _get(order, "qualification_result"),
        default={},
    )
    return _safe_dict(qualification)


def _extract_package(brief: Dict[str, Any], order: Any, qualification: Dict[str, Any]) -> str:
    return normalize_package(_first(
        qualification.get("recommended_package"),
        qualification.get("recommended_tier"),
        brief.get("final_package"),
        brief.get("package_key"),
        brief.get("package"),
        brief.get("tier"),
        _get(order, "recommended_tier"),
        _get(order, "package_key"),
        default="starter",
    ))


def _extract_pages(brief: Dict[str, Any], order: Any) -> Any:
    answers = _safe_dict(brief.get("answers"))
    return _first(
        brief.get("pages"),
        brief.get("requested_pages"),
        answers.get("pages"),
        answers.get("requested_pages"),
        _get(order, "pages_requested"),
        default=1,
    )


def _extract_features(brief: Dict[str, Any]) -> List[Any]:
    answers = _safe_dict(brief.get("answers"))
    return _safe_list(_first(
        brief.get("features"),
        brief.get("selected_features"),
        answers.get("features"),
        answers.get("selected_features"),
        default=[],
    ))


def _build_motion_profile(brief: Dict[str, Any], order: Any, package: str) -> Dict[str, Any]:
    effects = _first(
        brief.get("selected_effects"),
        brief.get("effects"),
        brief.get("motion_effects"),
        _get(order, "selected_effects"),
        default=[],
    )
    interaction_style = _first(
        brief.get("interaction_style"),
        brief.get("motion_level"),
        _get(order, "interaction_style"),
        default=None,
    )
    rules = get_package_rules(package)
    if package == "starter":
        interaction_style = None
        effects = []
    return {
        "interaction_style": interaction_style,
        "selected_effects": _safe_list(effects),
        "allow_premium_motion": bool(rules.get("allow_premium_motion")),
        "rule": "Starter skips motion. Business/Reference/Advanced may use restrained production-safe effects.",
    }


def build_canonical_brief(order: Any) -> Dict[str, Any]:
    first_brief = _safe_dict(_get(order, "brief_answers"))
    second_brief = _safe_dict(_get(order, "extended_brief"))
    merged_brief = {**first_brief, **second_brief}

    qualification = _extract_qualification(merged_brief, order)
    package = _extract_package(merged_brief, order, qualification)
    rules = get_package_rules(package)
    budget = get_generation_budget(package)
    selected_preview = _extract_selected_preview(order)
    preview_dna = _safe_dict(selected_preview.get("preview_dna"))
    layout_spec = _safe_dict(selected_preview.get("layout_spec"))
    design_system = _first(
        preview_dna.get("design_system"),
        selected_preview.get("design_system"),
        layout_spec.get("design_system"),
        default={},
    )
    sections = _first(
        preview_dna.get("sections"),
        selected_preview.get("sections"),
        layout_spec.get("sections"),
        default=[],
    )

    canonical = {
        "schema_version": "siteformo.canonical_brief.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "order_id": str(_get(order, "id", "")),
        "client_context": {
            "business_name": _first(_get(order, "business_name"), merged_brief.get("business_name"), default="Client business"),
            "business_type": _first(merged_brief.get("business_type"), merged_brief.get("industry"), _get(order, "site_type")),
            "market": _first(merged_brief.get("market"), default="Ireland / EU"),
            "source_url": _first(_get(order, "source_url"), merged_brief.get("source_url"), merged_brief.get("website_url")),
            "description": _first(_get(order, "desired_site_description"), merged_brief.get("description"), merged_brief.get("project_description")),
        },
        "source_signals": {
            "entry_source": _first(merged_brief.get("entry_source"), _get(order, "entry_source")),
            "selected_example_id": _first(merged_brief.get("selected_example_id"), merged_brief.get("example_id"), _get(order, "selected_example_id")),
            "viewed_examples": _safe_list(_first(merged_brief.get("viewed_examples"), merged_brief.get("examples_viewed"), _get(order, "viewed_examples"), default=[])),
            "example_tracking": _safe_dict(_first(merged_brief.get("example_tracking"), merged_brief.get("example_tracking_payload"), _get(order, "example_tracking_payload"), default={})),
        },
        "qualification": qualification,
        "commercial_scope": {
            "package": package,
            "package_rules": rules,
            "pricing_reasoning": _get(order, "pricing_reasoning"),
            "production_risk": _first(qualification.get("production_risk"), qualification.get("production_risk_score"), default=None),
        },
        "content_strategy": {
            "pages": _extract_pages(merged_brief, order),
            "features": _extract_features(merged_brief),
            "services": _safe_list(_first(merged_brief.get("services"), merged_brief.get("service_names"), default=[])),
            "goals": _safe_list(_first(merged_brief.get("goals"), merged_brief.get("main_goals"), default=[])),
            "raw_first_questionnaire": first_brief,
            "raw_second_questionnaire": second_brief,
        },
        "visual_direction": {
            "design_direction": _first(merged_brief.get("design_direction"), merged_brief.get("selected_design_direction"), _get(order, "design_direction")),
            "selected_design_id": _get(order, "selected_design_id"),
            "selected_design_url": _get(order, "selected_design_url"),
            "preview_dna": preview_dna,
            "design_system": design_system,
            "sections": sections,
            "selected_preview_prompt": _first(preview_dna.get("prompt"), selected_preview.get("prompt")),
        },
        "motion_profile": _build_motion_profile(merged_brief, order, package),
        "generation_rules": {
            "never_copy_reference_site": True,
            "reference_site_usage": "Use references only as expectation, complexity and style signals. Never clone layout, copy, brand, assets or protected design.",
            "no_ai_mentions": True,
            "english_only": True,
            "mobile_first": True,
            "divi_ready_html": True,
            "no_external_scripts": True,
            "budget": budget,
        },
        "quality_standards": {
            "target_score": budget["target_score"],
            "max_quality_iterations": budget["max_quality_iterations"],
            "max_total_generation_rounds": budget["max_total_generation_rounds"],
            "max_warnings": budget["max_warnings"],
            "manual_review_after_limit": True,
        },
    }
    return canonical


def store_canonical_brief_on_order(order: Any, canonical_brief: Dict[str, Any]) -> None:
    existing = _get(order, "production_payload") or {}
    if not isinstance(existing, dict):
        existing = {}
    existing["canonical_brief"] = canonical_brief
    existing["generation_budget"] = canonical_brief.get("generation_rules", {}).get("budget", {})
    existing["quality_standards"] = canonical_brief.get("quality_standards", {})
    if hasattr(order, "production_payload"):
        order.production_payload = existing


def get_or_build_canonical_brief(order: Any) -> Dict[str, Any]:
    production_payload = _safe_dict(_get(order, "production_payload"))
    existing = production_payload.get("canonical_brief")
    if isinstance(existing, dict) and existing.get("schema_version") == "siteformo.canonical_brief.v1":
        return existing
    return build_canonical_brief(order)
