import inspect
import logging
import os
import asyncio
import threading
import base64
import uuid
from datetime import datetime
from html import escape
from urllib.parse import quote

import requests
from typing import Any, Dict, List, Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from app.models.order import FinalPackage, Order, OrderStatus
from app.services.prompt_service import build_ai_prompt, build_preview_variation_prompts
from app.services.quality_review_service import review_site
from app.services.auto_improvement_service import auto_improve
from app.services.technical_check_service import technical_check_preview
from app.services.pre_delivery_check_service import decide_preview_status
from app.services.quality_package_rules import get_package_rules
from app.services.canonical_brief_service import build_canonical_brief, store_canonical_brief_on_order, get_or_build_canonical_brief
from app.services.generation_orchestrator_service import GenerationOrchestratorService
from app.services.divi_style_generation_service import DiviStyleGenerationService

logger = logging.getLogger(__name__)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class GenerationService:
    """
    Generates website concepts, design previews and final Divi-ready packages.

    New SiteFormo flow:
    1. extended questionnaire submitted
    2. generate 5 design previews (+ 3 logo concepts if logo ordered)
    3. client selects 1 preview
    4. approve-design starts the 1-hour refund window
    5. full website generation starts only after selected design approval

    This file keeps the old methods working and adds safe preview/full-production helpers.
    """

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_MODEL", "gpt-5").strip()
        self.image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1").strip()
        self.client: Optional[OpenAI] = (
            OpenAI(api_key=self.api_key) if self.api_key else None
        )

    # ---------------------------------------------------------------------
    # NEW FLOW: DESIGN PREVIEWS
    # ---------------------------------------------------------------------

    def build_design_preview_payload(
        self,
        order: Optional[Order] = None,
        extended_brief: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Builds a structured preview payload with real OpenAI-generated homepage screenshot concepts.

        If OpenAI image generation or public asset upload is unavailable, the method falls
        back to the existing lightweight SVG previews instead of breaking the order flow.
        """
        extended_brief = extended_brief or {}
        logo_ordered = bool(extended_brief.get("logo_ordered"))

        project_summary = self._summarize_extended_brief(order, extended_brief)
        prompt_engine_prompt = build_ai_prompt(extended_brief)
        prompts = self._build_preview_prompts(project_summary, extended_brief)

        previews = []
        for index, item in enumerate(prompts, start=1):
            screenshot_url, image_source = self._generate_or_fallback_preview_image(
                project_summary=project_summary,
                item=item,
                index=index,
                image_kind="homepage",
            )
            preview_id = f"design_{index}"
            device_screenshots = self._schedule_device_screenshots_if_enabled(
                url=screenshot_url,
                order_id=str(project_summary.get("order_id") or "unknown-order"),
                preview_id=preview_id,
            )

            divi_layout_spec = DiviStyleGenerationService().generate_layout_spec(
                project_summary,
                variant=index - 1,
            )

            previews.append(
                {
                    "id": preview_id,
                    "type": "homepage_screenshot",
                    "label": item["label"],
                    "style": item.get("style") or divi_layout_spec.get("style", "modern"),
                    "color_direction": item.get("color_direction", ""),
                    "prompt": item["prompt"],
                    "preview_url": screenshot_url,
                    "screenshot_url": screenshot_url,
                    "image_url": screenshot_url,
                    "desktop_image_url": device_screenshots.get("desktop_image_url") or screenshot_url,
                    "mobile_image_url": device_screenshots.get("mobile_image_url") or screenshot_url,
                    "screenshots_status": device_screenshots.get("status"),
                    "image_source": image_source,
                    "status": "READY",

                    # SiteFormo design DNA.
                    # This keeps final generation aligned with the preview selected by the client.
                    "layout_spec": divi_layout_spec,
                    "design_system": divi_layout_spec.get("design_system", {}),
                    "sections": divi_layout_spec.get("sections", []),
                    "preview_dna": {
                        "style": item.get("style") or divi_layout_spec.get("style", "modern"),
                        "color_direction": item.get("color_direction", ""),
                        "design_system": divi_layout_spec.get("design_system", {}),
                        "sections": divi_layout_spec.get("sections", []),
                        "prompt": item.get("prompt", ""),
                    },
                }
            )

        logo_previews: List[Dict[str, Any]] = []
        if logo_ordered:
            logo_prompts = self._build_logo_prompts(project_summary)
            for index, item in enumerate(logo_prompts, start=1):
                logo_url, logo_source = self._generate_or_fallback_logo_image(
                    project_summary=project_summary,
                    item=item,
                    index=index,
                )
                logo_previews.append(
                    {
                        "id": f"logo_{index}",
                        "type": "logo_concept",
                        "label": item["label"],
                        "style": item["style"],
                        "prompt": item["prompt"],
                        "preview_url": logo_url,
                        "image_url": logo_url,
                        "image_source": logo_source,
                        "status": "READY",
                    }
                )

        return {
            "design_status": "DESIGN_PREVIEWS_READY",
            "generated_at": datetime.utcnow().isoformat(),
            "project_summary": project_summary,
            "prompt_engine_prompt": prompt_engine_prompt,
            "design_previews": previews,
            "logo_previews": logo_previews,
            "note": (
                "Screenshot previews are ready and include displayable screenshot URLs."
            ),
        }

    def generate_design_previews_for_order(
        self,
        db: Session,
        order: Order,
        extended_brief: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Sync DB version for code paths that already have a SQLAlchemy Session and Order.
        Saves preview data on the order when matching columns exist.
        """
        preview_payload = self.build_design_preview_payload(order, extended_brief)

        self._set_if_exists(order, "design_status", preview_payload["design_status"])
        self._set_if_exists(order, "design_previews", preview_payload["design_previews"])
        self._set_if_exists(order, "logo_previews", preview_payload["logo_previews"])
        self._set_if_exists(order, "preview_generation_payload", preview_payload)

        if hasattr(order, "status"):
            try:
                order.status = OrderStatus.DESIGN_PREVIEWS_READY
            except Exception:
                self._set_if_exists(order, "status", "BRIEF_SUBMITTED")

        db.commit()
        db.refresh(order)

        logger.info("Design previews prepared for order %s", _safe_get(order, "id"))
        return preview_payload


    def _extract_package_for_quality(self, order: Order) -> str:
        """
        Normalise package/tier for the final HTML quality gate.
        Supports both public package names and backend tier names.
        """
        extended_brief = (
            getattr(order, "extended_brief", None)
            or getattr(order, "brief_answers", None)
            or {}
        )

        if isinstance(extended_brief, dict):
            value = (
                extended_brief.get("package_key")
                or extended_brief.get("plan")
                or extended_brief.get("package")
                or extended_brief.get("tier")
            )
            if value:
                return str(value).strip().lower()

        value = (
            getattr(order, "package_key", None)
            or getattr(order, "recommended_tier", None)
            or getattr(order, "tier", None)
            or "starter"
        )
        return str(value).strip().lower()

    def _run_final_html_quality_pipeline(
        self,
        order: Order,
        html: str,
    ) -> Dict[str, Any]:
        """
        Central final quality pipeline.

        Flow:
        Canonical Brief -> Generate v1 -> Technical Review -> AI Review ->
        Auto Improvement -> Review again -> Ready or Manual Review.

        Cost protection:
        The orchestrator enforces the global hard cap of max 3 improvement rounds.
        """
        return GenerationOrchestratorService().run_final_html_pipeline(
            order=order,
            initial_html=html,
        )


    # ---------------------------------------------------------------------
    # NEW FLOW: FULL GENERATION AFTER DESIGN APPROVAL
    # ---------------------------------------------------------------------

    def start_full_generation_for_order(
        self,
        db: Session,
        order: Order,
        note: str = "Full generation started after client selected a design preview.",
    ) -> FinalPackage:
        """
        Starts full website generation after the client approves one preview.
        This preserves the existing final-package behavior.
        """
        selected_design = (
            _safe_get(order, "selected_design_url")
            or _safe_get(order, "selected_design_id")
            or ""
        )

        if not selected_design:
            logger.warning(
                "Starting full generation for order %s without selected_design_url. "
                "Check approve-design payload.",
                _safe_get(order, "id"),
            )

        self._set_if_exists(order, "status", "FULL_PRODUCTION_STARTED")
        self._set_if_exists(order, "design_status", "DESIGN_APPROVED")
        db.commit()
        db.refresh(order)

        return self.generate_final_package_for_order(db, order, note=note)

    # ---------------------------------------------------------------------
    # EXISTING FLOW: CONCEPT GENERATION
    # ---------------------------------------------------------------------

    def generate_site_concept(
        self,
        user_input: str,
        mode: str = "describe_site",
        intake_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        normalized_input = (user_input or "").strip()
        intake_data = intake_data or {}

        if not normalized_input and not intake_data:
            return (
                "I need a bit more information before I can generate a website concept.\n\n"
                "Please describe the business, goal, style, and what pages you need."
            )

        if not self.client:
            logger.warning("OPENAI_API_KEY is missing. Using fallback generation.")
            return self._fallback_response(normalized_input, mode, intake_data)

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(normalized_input, mode, intake_data)

        try:
            response = self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )

            output_text = getattr(response, "output_text", None)

            if output_text and output_text.strip():
                return output_text.strip()

            logger.warning("OpenAI response returned empty output_text. Using fallback.")
            return self._fallback_response(normalized_input, mode, intake_data)

        except Exception as exc:
            logger.exception("OpenAI generation failed: %s", exc)
            return (
                "I couldn't generate the website concept with AI right now.\n\n"
                "Here is a draft concept instead:\n\n"
                f"{self._fallback_response(normalized_input, mode, intake_data)}"
            )

    def generate_final_package_for_order(
        self,
        db: Session,
        order: Order,
        note: str = "Generated automatically after owner approval.",
    ) -> FinalPackage:
        """
        Creates a final Divi-ready package after design approval.

        NEW:
        The generated final HTML now passes through the SiteFormo quality pipeline:
        technical check -> AI review -> auto-improvement loop -> final gate.

        If the HTML is not ready after the package's max quality attempts, the order
        is marked MANUAL_REVIEW_REQUIRED and the best improved HTML is still saved
        for owner review.
        """

        existing_package = (
            db.query(FinalPackage)
            .filter(FinalPackage.order_id == order.id)
            .order_by(FinalPackage.id.desc())
            .first()
        )

        if existing_package:
            try:
                order.status = OrderStatus.READY_FOR_REVIEW
            except Exception:
                self._set_if_exists(order, "status", "READY_FOR_REVIEW")
            self._set_if_exists(order, "generation_status", "READY_FOR_REVIEW")
            db.commit()
            db.refresh(order)
            return existing_package

        selected_concept_label = (
            _safe_get(order, "selected_design_id")
            or _safe_get(order, "selected_design_url")
            or "A"
        )

        canonical_brief = build_canonical_brief(order)
        store_canonical_brief_on_order(order, canonical_brief)

        raw_divi_html = self._generate_divi_html(order)

        quality_result = self._run_final_html_quality_pipeline(order, raw_divi_html)
        final_divi_html = quality_result["html"]

        brief_markdown = self._build_brief_markdown(order)

        quality_report_markdown = (
            "\n\n---\n"
            "## SiteFormo Final HTML Quality Report\n"
            f"- Status: {quality_result.get('status')}\n"
            f"- Ready to send: {quality_result.get('ready_to_send')}\n"
            f"- Package: {quality_result.get('package')}\n"
            f"- Target score: {quality_result.get('target_score')}\n"
            f"- Final score: {quality_result.get('final_score')}\n"
            f"- Critical errors: {quality_result.get('critical_errors')}\n"
            f"- Warnings: {quality_result.get('warnings')}\n"
        )

        package = FinalPackage(
            order_id=order.id,
            selected_concept_label=str(selected_concept_label),
            divi_html=final_divi_html,
            brief_markdown=brief_markdown + quality_report_markdown,
            notes=(
                note
                + f"\n\nFinal HTML quality status: {quality_result.get('status')}. "
                + f"Final score: {quality_result.get('final_score')}."
            ),
        )

        db.add(package)

        self._set_if_exists(order, "final_quality_report", quality_result)

        if quality_result.get("ready_to_send"):
            # Current SiteFormo rule: completed website goes to protected review.
            # Final ZIP/source delivery is locked until revision completion and final approval.
            try:
                order.status = OrderStatus.READY_FOR_REVIEW
            except Exception:
                self._set_if_exists(order, "status", "READY_FOR_REVIEW")
            self._set_if_exists(order, "generation_status", "READY_FOR_REVIEW")
        else:
            try:
                order.status = OrderStatus.MANUAL_REVIEW_REQUIRED
            except Exception:
                self._set_if_exists(order, "status", "MANUAL_REVIEW_REQUIRED")
            self._set_if_exists(order, "generation_status", "MANUAL_REVIEW_REQUIRED")

        db.commit()
        db.refresh(order)
        db.refresh(package)

        logger.info(
            "Final package generated for order %s with quality status %s and score %s",
            order.id,
            quality_result.get("status"),
            quality_result.get("final_score"),
        )

        return package

    def _generate_divi_html(self, order: Order) -> str:
        """
        Component-builder final generation.

        Selected preview -> layout sections -> real HTML blocks.
        This keeps the final website closer to what the client selected.
        """

        business_name = escape(
            getattr(order, "business_name", None)
            or getattr(order, "source_url", None)
            or "Client business"
        )

        description = escape(
            getattr(order, "desired_site_description", None)
            or "A premium website designed to convert visitors into qualified leads."
        )

        brief_answers = (
            getattr(order, "extended_brief", None)
            or getattr(order, "brief_answers", None)
            or {}
        )
        if not isinstance(brief_answers, dict):
            brief_answers = {"raw_brief": str(brief_answers)}

        selected_design_id = (
            getattr(order, "selected_design_id", "")
            or getattr(order, "selected_design_url", "")
            or ""
        )

        design_previews = (
            getattr(order, "design_previews", None)
            or getattr(order, "preview_generation_payload", None)
            or []
        )

        if isinstance(design_previews, dict):
            design_previews = design_previews.get("design_previews") or []

        selected_preview: Dict[str, Any] = {}
        if isinstance(design_previews, list):
            for preview in design_previews:
                if not isinstance(preview, dict):
                    continue

                preview_id = str(preview.get("id") or "")
                preview_url = str(
                    preview.get("preview_url")
                    or preview.get("screenshot_url")
                    or preview.get("image_url")
                    or ""
                )

                if selected_design_id and (
                    selected_design_id == preview_id
                    or selected_design_id == preview_url
                    or selected_design_id in {preview_id, preview_url}
                ):
                    selected_preview = preview
                    break

            if not selected_preview and design_previews:
                selected_preview = design_previews[0] if isinstance(design_previews[0], dict) else {}

        preview_dna = selected_preview.get("preview_dna") or {}
        layout_spec = selected_preview.get("layout_spec") or {}

        sections = (
            preview_dna.get("sections")
            or selected_preview.get("sections")
            or layout_spec.get("sections")
            or []
        )

        design_system = (
            preview_dna.get("design_system")
            or selected_preview.get("design_system")
            or layout_spec.get("design_system")
            or {}
        )

        colors = design_system.get("colors", {}) if isinstance(design_system, dict) else {}
        primary = escape(str(colors.get("primary") or "#0A7CFF"))
        secondary = escape(str(colors.get("secondary") or "#111111"))
        background = escape(str(colors.get("background") or "#FFFFFF"))

        service_names = self._extract_services_from_brief(brief_answers)
        if not service_names:
            service_names = ["Website Design", "Conversion Strategy", "Mobile Optimization"]

        normalized_sections = [
            str(section.get("type") if isinstance(section, dict) else section).lower()
            for section in sections
        ]

        if not normalized_sections:
            normalized_sections = [
                "hero_split",
                "services_cards",
                "trust",
                "about_split",
                "faq",
                "cta_big",
            ]

        html_sections: List[str] = []
        html_sections.append(self._build_component_css(primary, secondary, background))
        html_sections.append(self._component_header(business_name))

        hero_added = False
        services_added = False
        trust_added = False
        about_added = False
        cta_added = False
        faq_added = False

        for section_type in normalized_sections:
            if "hero" in section_type and not hero_added:
                if "minimal" in section_type:
                    html_sections.append(self._component_hero_minimal(business_name, description))
                elif "center" in section_type:
                    html_sections.append(self._component_hero_center(business_name, description))
                else:
                    html_sections.append(self._component_hero_split(business_name, description))
                hero_added = True

            elif any(key in section_type for key in ["service", "feature", "benefit"]) and not services_added:
                html_sections.append(self._component_services(service_names))
                services_added = True

            elif any(key in section_type for key in ["trust", "testimonial", "stat", "logo"]) and not trust_added:
                html_sections.append(self._component_trust())
                trust_added = True

            elif any(key in section_type for key in ["about", "process", "portfolio", "gallery"]) and not about_added:
                html_sections.append(self._component_about(business_name))
                about_added = True

            elif "faq" in section_type and not faq_added:
                html_sections.append(self._component_faq())
                faq_added = True

            elif "cta" in section_type and not cta_added:
                html_sections.append(self._component_cta())
                cta_added = True

        if not hero_added:
            html_sections.append(self._component_hero_split(business_name, description))
        if not services_added:
            html_sections.append(self._component_services(service_names))
        if not trust_added:
            html_sections.append(self._component_trust())
        if not about_added:
            html_sections.append(self._component_about(business_name))
        if not faq_added:
            html_sections.append(self._component_faq())
        if not cta_added:
            html_sections.append(self._component_cta())

        html_sections.append(self._component_footer(business_name))
        html_sections.append(self._component_animation_script())

        return "\\n\\n".join(html_sections).strip()

    def _extract_services_from_brief(self, brief_answers: Dict[str, Any]) -> List[str]:
        services: List[str] = []

        if not isinstance(brief_answers, dict):
            return services

        possible_sources = [
            brief_answers.get("services"),
            brief_answers.get("service_names"),
            brief_answers.get("selected_services"),
        ]

        answers = brief_answers.get("answers")
        if isinstance(answers, dict):
            possible_sources.extend([
                answers.get("services"),
                answers.get("service_names"),
                answers.get("selected_services"),
            ])

        for source in possible_sources:
            if isinstance(source, list):
                for item in source:
                    if isinstance(item, dict):
                        value = item.get("name") or item.get("title") or item.get("selected")
                    else:
                        value = item
                    if value:
                        services.append(str(value))
            elif isinstance(source, str) and source.strip():
                services.extend([s.strip() for s in source.split(",") if s.strip()])

        clean: List[str] = []
        for item in services:
            if item not in clean:
                clean.append(item)

        return clean[:4]

    def _build_component_css(self, primary: str, secondary: str, background: str) -> str:
        return f"""
<style>
  :root {{
    --sf-primary: {primary};
    --sf-secondary: {secondary};
    --sf-bg: {background};
    --sf-muted: #64748b;
    --sf-card: rgba(255, 255, 255, .86);
    --sf-border: rgba(148, 163, 184, .28);
    --sf-radius: 28px;
    --sf-radius-xl: 40px;
    --sf-shadow-soft: 0 18px 55px rgba(15, 23, 42, .08);
    --sf-shadow-hover: 0 30px 90px rgba(15, 23, 42, .16);
    --sf-section-space: 92px;
    --sf-gradient-dark: linear-gradient(135deg, #0f172a 0%, #111827 48%, #020617 100%);
    --sf-gradient-premium: radial-gradient(circle at 20% 10%, rgba(255,255,255,.88), transparent 25%), linear-gradient(135deg, var(--sf-primary), #111827);
  }}

  * {{
    box-sizing: border-box;
  }}

  html {{
    scroll-behavior: smooth;
  }}

  .siteformo-page {{
    background:
      radial-gradient(circle at top left, color-mix(in srgb, var(--sf-primary) 10%, transparent), transparent 34%),
      radial-gradient(circle at 85% 12%, rgba(15, 23, 42, .07), transparent 24%),
      var(--sf-bg);
    color: var(--sf-secondary);
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    line-height: 1.5;
    overflow-x: hidden;
  }}

  .siteformo-container {{
    width: min(1160px, calc(100% - 40px));
    margin: 0 auto;
  }}

  .siteformo-header {{
    padding: 18px 0;
    border-bottom: 1px solid rgba(148, 163, 184, .18);
    background: rgba(255,255,255,.78);
    position: sticky;
    top: 0;
    z-index: 20;
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
  }}

  .siteformo-nav {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 24px;
  }}

  .siteformo-logo {{
    font-weight: 900;
    font-size: 20px;
    letter-spacing: -0.04em;
    color: var(--sf-secondary);
  }}

  .siteformo-menu {{
    display: flex;
    gap: 24px;
    color: var(--sf-muted);
    font-size: 14px;
    font-weight: 650;
  }}

  .siteformo-menu span {{
    position: relative;
  }}

  .siteformo-menu span::after {{
    content: "";
    position: absolute;
    left: 0;
    right: 0;
    bottom: -8px;
    height: 2px;
    border-radius: 999px;
    background: var(--sf-primary);
    transform: scaleX(0);
    transform-origin: left;
    transition: transform .25s ease;
  }}

  .siteformo-menu span:hover::after {{
    transform: scaleX(1);
  }}

  .siteformo-button,
  .siteformo-button-secondary {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 50px;
    padding: 0 24px;
    border-radius: 999px;
    text-decoration: none;
    font-weight: 800;
    letter-spacing: -.01em;
    transition: transform .25s ease, box-shadow .25s ease, border-color .25s ease, background .25s ease;
    will-change: transform;
  }}

  .siteformo-button {{
    background: linear-gradient(135deg, var(--sf-primary), color-mix(in srgb, var(--sf-primary) 62%, #111827));
    color: #fff;
    box-shadow: 0 14px 36px color-mix(in srgb, var(--sf-primary) 28%, transparent);
  }}

  .siteformo-button-secondary {{
    background: rgba(255, 255, 255, .72);
    color: var(--sf-secondary);
    border: 1px solid var(--sf-border);
    backdrop-filter: blur(10px);
  }}

  .siteformo-button:hover,
  .siteformo-button-secondary:hover {{
    transform: translateY(-3px) scale(1.025);
    box-shadow: var(--sf-shadow-hover);
  }}

  .siteformo-hero {{
    padding: 106px 0 78px;
    position: relative;
  }}

  .siteformo-hero::before {{
    content: "";
    position: absolute;
    width: 520px;
    height: 520px;
    border-radius: 999px;
    right: -220px;
    top: -190px;
    background: color-mix(in srgb, var(--sf-primary) 18%, transparent);
    filter: blur(26px);
    pointer-events: none;
  }}

  .siteformo-hero-grid {{
    display: grid;
    grid-template-columns: 1.03fr .97fr;
    gap: 58px;
    align-items: center;
    position: relative;
    z-index: 1;
  }}

  .siteformo-eyebrow {{
    color: var(--sf-primary);
    text-transform: uppercase;
    font-size: 12px;
    letter-spacing: .16em;
    font-weight: 900;
    margin-bottom: 16px;
  }}

  .siteformo-hero h1,
  .siteformo-section h2 {{
    letter-spacing: -0.06em;
    line-height: .98;
    margin: 0;
  }}

  .siteformo-hero h1 {{
    font-size: clamp(44px, 6.4vw, 82px);
    max-width: 850px;
  }}

  .siteformo-hero p {{
    color: var(--sf-muted);
    font-size: clamp(18px, 2vw, 21px);
    max-width: 640px;
    margin: 24px 0 32px;
  }}

  .siteformo-actions {{
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
  }}

  .siteformo-visual {{
    min-height: 450px;
    border-radius: var(--sf-radius-xl);
    background: var(--sf-gradient-premium);
    box-shadow: 0 34px 100px rgba(15, 23, 42, .22);
    position: relative;
    overflow: hidden;
    transform-style: preserve-3d;
    transition: transform .6s ease, box-shadow .6s ease;
  }}

  .siteformo-visual::before {{
    content: "";
    position: absolute;
    inset: 26px;
    border-radius: 32px;
    background:
      linear-gradient(180deg, rgba(255,255,255,.25), rgba(255,255,255,.08)),
      rgba(255,255,255,.12);
    border: 1px solid rgba(255,255,255,.32);
    backdrop-filter: blur(18px);
  }}

  .siteformo-visual::after {{
    content: "";
    position: absolute;
    left: 58px;
    right: 58px;
    bottom: 62px;
    height: 150px;
    border-radius: 28px;
    background:
      linear-gradient(90deg, rgba(255,255,255,.92) 0 30%, rgba(255,255,255,.38) 30% 100%);
    box-shadow: 0 22px 60px rgba(2,6,23,.22);
    opacity: .92;
  }}

  .siteformo-visual:hover {{
    transform: translateY(-8px) rotateX(1deg) rotateY(-1deg) scale(1.015);
    box-shadow: 0 42px 115px rgba(15, 23, 42, .28);
  }}

  .siteformo-section {{
    padding: var(--sf-section-space) 0;
    position: relative;
  }}

  .siteformo-section h2 {{
    font-size: clamp(34px, 4.3vw, 58px);
    max-width: 780px;
  }}

  .siteformo-section-intro {{
    color: var(--sf-muted);
    font-size: 18px;
    max-width: 700px;
    margin: 20px 0 38px;
  }}

  .siteformo-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 24px;
  }}

  .siteformo-card {{
    background: var(--sf-card);
    border: 1px solid var(--sf-border);
    border-radius: var(--sf-radius);
    padding: 30px;
    box-shadow: var(--sf-shadow-soft);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    position: relative;
    overflow: hidden;
    transition: transform .3s ease, box-shadow .3s ease, border-color .3s ease;
    will-change: transform;
  }}

  .siteformo-card::before {{
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(135deg, color-mix(in srgb, var(--sf-primary) 10%, transparent), transparent 45%);
    opacity: 0;
    transition: opacity .3s ease;
    pointer-events: none;
  }}

  .siteformo-card:hover {{
    transform: translateY(-10px);
    box-shadow: var(--sf-shadow-hover);
    border-color: color-mix(in srgb, var(--sf-primary) 36%, var(--sf-border));
  }}

  .siteformo-card:hover::before {{
    opacity: 1;
  }}

  .siteformo-card h3 {{
    font-size: 22px;
    letter-spacing: -0.03em;
    margin: 0 0 10px;
    position: relative;
  }}

  .siteformo-card p {{
    color: var(--sf-muted);
    margin: 0;
    position: relative;
  }}

  .siteformo-trust {{
    background: var(--sf-gradient-dark);
    color: #fff;
    border-radius: var(--sf-radius-xl);
    padding: 50px;
    box-shadow: 0 28px 90px rgba(2, 6, 23, .24);
    position: relative;
    overflow: hidden;
  }}

  .siteformo-trust::before {{
    content: "";
    position: absolute;
    width: 420px;
    height: 420px;
    border-radius: 999px;
    right: -140px;
    top: -180px;
    background: color-mix(in srgb, var(--sf-primary) 32%, transparent);
    filter: blur(22px);
  }}

  .siteformo-trust > * {{
    position: relative;
    z-index: 1;
  }}

  .siteformo-trust-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 18px;
  }}

  .siteformo-stat {{
    padding: 20px;
    border-radius: 24px;
    background: rgba(255,255,255,.08);
    border: 1px solid rgba(255,255,255,.12);
  }}

  .siteformo-stat strong {{
    display: block;
    font-size: 34px;
    letter-spacing: -0.04em;
  }}

  .siteformo-stat span {{
    color: #cbd5e1;
    font-size: 14px;
  }}

  .siteformo-about {{
    display: grid;
    grid-template-columns: .85fr 1.15fr;
    gap: 46px;
    align-items: center;
  }}

  .siteformo-faq-item {{
    border-top: 1px solid var(--sf-border);
    padding: 24px 0;
    transition: padding-left .25s ease, border-color .25s ease;
  }}

  .siteformo-faq-item:hover {{
    padding-left: 12px;
    border-color: color-mix(in srgb, var(--sf-primary) 42%, var(--sf-border));
  }}

  .siteformo-faq-item h3 {{
    margin: 0 0 8px;
    letter-spacing: -0.025em;
  }}

  .siteformo-faq-item p {{
    margin: 0;
    color: var(--sf-muted);
  }}

  .siteformo-final-cta {{
    background:
      radial-gradient(circle at 18% 14%, rgba(255,255,255,.35), transparent 26%),
      linear-gradient(135deg, var(--sf-primary), #111827);
    color: #fff;
    border-radius: var(--sf-radius-xl);
    padding: 66px;
    text-align: center;
    box-shadow: 0 30px 95px color-mix(in srgb, var(--sf-primary) 18%, rgba(15, 23, 42, .25));
    overflow: hidden;
    position: relative;
  }}

  .siteformo-final-cta::after {{
    content: "";
    position: absolute;
    inset: auto -90px -150px auto;
    width: 360px;
    height: 360px;
    border-radius: 999px;
    background: rgba(255,255,255,.18);
    filter: blur(12px);
  }}

  .siteformo-final-cta > * {{
    position: relative;
    z-index: 1;
  }}

  .siteformo-final-cta h2 {{
    font-size: clamp(36px, 5vw, 64px);
    letter-spacing: -0.06em;
    line-height: .98;
    margin: 0 0 16px;
  }}

  .siteformo-final-cta p {{
    color: rgba(255,255,255,.84);
    max-width: 640px;
    margin: 0 auto 30px;
    font-size: 18px;
  }}

  .siteformo-footer {{
    padding: 40px 0;
    color: var(--sf-muted);
    font-size: 14px;
  }}

  .siteformo-reveal {{
    opacity: 0;
    transform: translateY(34px);
    transition: opacity .72s ease, transform .72s ease;
  }}

  .siteformo-reveal.siteformo-visible {{
    opacity: 1;
    transform: translateY(0);
  }}

  .siteformo-reveal-delay-1 {{ transition-delay: .08s; }}
  .siteformo-reveal-delay-2 {{ transition-delay: .16s; }}
  .siteformo-reveal-delay-3 {{ transition-delay: .24s; }}

  @media (prefers-reduced-motion: reduce) {{
    html {{ scroll-behavior: auto; }}
    .siteformo-reveal,
    .siteformo-card,
    .siteformo-button,
    .siteformo-button-secondary,
    .siteformo-visual,
    .siteformo-faq-item {{
      transition: none !important;
      transform: none !important;
    }}
  }}

  @media (max-width: 920px) {{
    .siteformo-menu {{
      display: none;
    }}
    .siteformo-hero-grid,
    .siteformo-about,
    .siteformo-grid,
    .siteformo-trust-grid {{
      grid-template-columns: 1fr;
    }}
    .siteformo-hero {{
      padding-top: 62px;
    }}
    .siteformo-visual {{
      min-height: 300px;
    }}
    .siteformo-trust,
    .siteformo-final-cta {{
      padding: 38px 24px;
    }}
  }}

  @media (max-width: 520px) {{
    .siteformo-container {{
      width: min(100% - 28px, 1160px);
    }}
    .siteformo-actions {{
      flex-direction: column;
    }}
    .siteformo-button,
    .siteformo-button-secondary {{
      width: 100%;
    }}
  }}
</style>
""".strip()

    def _component_header(self, business_name: str) -> str:
        return f"""
<div class="siteformo-page">
<header class="siteformo-header">
  <div class="siteformo-container siteformo-nav">
    <div class="siteformo-logo">{business_name}</div>
    <nav class="siteformo-menu">
      <span>Services</span>
      <span>About</span>
      <span>Reviews</span>
      <span>FAQ</span>
    </nav>
    <a class="siteformo-button" href="#contact">Get a Quote</a>
  </div>
</header>
""".strip()

    def _component_hero_split(self, business_name: str, description: str) -> str:
        return f"""
<section class="siteformo-hero">
  <div class="siteformo-container siteformo-hero-grid">
    <div>
      <div class="siteformo-eyebrow">Premium Website Experience</div>
      <h1>{business_name} — built to turn visitors into customers</h1>
      <p>{description}</p>
      <div class="siteformo-actions">
        <a class="siteformo-button" href="#contact">Get a Free Quote</a>
        <a class="siteformo-button-secondary" href="#services">View Services</a>
      </div>
    </div>
    <div class="siteformo-visual" aria-hidden="true"></div>
  </div>
</section>
""".strip()

    def _component_hero_center(self, business_name: str, description: str) -> str:
        return f"""
<section class="siteformo-hero">
  <div class="siteformo-container" style="text-align:center;">
    <div class="siteformo-eyebrow">Built for Growth</div>
    <h1 style="margin-left:auto;margin-right:auto;">{business_name} websites that look premium and convert better</h1>
    <p style="margin-left:auto;margin-right:auto;">{description}</p>
    <div class="siteformo-actions" style="justify-content:center;">
      <a class="siteformo-button" href="#contact">Start Your Project</a>
      <a class="siteformo-button-secondary" href="#services">Explore Services</a>
    </div>
  </div>
</section>
""".strip()

    def _component_hero_minimal(self, business_name: str, description: str) -> str:
        return f"""
<section class="siteformo-hero">
  <div class="siteformo-container">
    <div class="siteformo-eyebrow">Clean. Clear. Effective.</div>
    <h1>{business_name} with a website that explains your value instantly</h1>
    <p>{description}</p>
    <a class="siteformo-button" href="#contact">Book a Call</a>
  </div>
</section>
""".strip()

    def _component_services(self, services: List[str]) -> str:
        cards = []
        for service in services[:4]:
            safe = escape(str(service))
            cards.append(
                f"""
      <div class="siteformo-card">
        <h3>{safe}</h3>
        <p>Clear, professional and conversion-focused delivery designed around your business goals.</p>
      </div>
""".strip()
            )

        return f"""
<section id="services" class="siteformo-section">
  <div class="siteformo-container">
    <h2>Services designed to move your business forward</h2>
    <p class="siteformo-section-intro">A clear structure helps visitors understand what you offer, why it matters and how to take action.</p>
    <div class="siteformo-grid">
      {" ".join(cards)}
    </div>
  </div>
</section>
""".strip()

    def _component_trust(self) -> str:
        return """
<section class="siteformo-section">
  <div class="siteformo-container">
    <div class="siteformo-trust">
      <h2>Built to create trust before the first message</h2>
      <p class="siteformo-section-intro" style="color:#cbd5e1;">Your website should make the business feel credible, active and easy to choose.</p>
      <div class="siteformo-trust-grid">
        <div class="siteformo-stat"><strong>4.9★</strong><span>Customer rating</span></div>
        <div class="siteformo-stat"><strong>24h</strong><span>Fast response path</span></div>
        <div class="siteformo-stat"><strong>100%</strong><span>Mobile-ready layout</span></div>
        <div class="siteformo-stat"><strong>3</strong><span>Revision rounds included</span></div>
      </div>
    </div>
  </div>
</section>
""".strip()

    def _component_about(self, business_name: str) -> str:
        return f"""
<section class="siteformo-section">
  <div class="siteformo-container siteformo-about">
    <div>
      <div class="siteformo-eyebrow">Why it works</div>
      <h2>A website structure that makes the offer easy to understand</h2>
    </div>
    <div class="siteformo-card">
      <p>{business_name} needs more than a good-looking page. The layout is built to guide visitors from first impression to trust, then toward a clear action.</p>
      <p style="margin-top:14px;">Every section has a job: explain the value, show credibility and make it simple to enquire.</p>
    </div>
  </div>
</section>
""".strip()

    def _component_faq(self) -> str:
        return """
<section class="siteformo-section">
  <div class="siteformo-container">
    <h2>Questions visitors usually ask</h2>
    <div class="siteformo-faq-item">
      <h3>What makes this business different?</h3>
      <p>The page explains the offer clearly, supports trust and gives visitors a simple next step.</p>
    </div>
    <div class="siteformo-faq-item">
      <h3>Is the website mobile-ready?</h3>
      <p>Yes. The structure is designed to work clearly across desktop, tablet and mobile screens.</p>
    </div>
    <div class="siteformo-faq-item">
      <h3>How do visitors get started?</h3>
      <p>They can use the main call-to-action buttons to request a quote, book a call or send an enquiry.</p>
    </div>
  </div>
</section>
""".strip()

    def _component_cta(self) -> str:
        return """
<section id="contact" class="siteformo-section">
  <div class="siteformo-container">
    <div class="siteformo-final-cta">
      <h2>Ready to turn more visitors into enquiries?</h2>
      <p>Use a clear, premium website to explain your offer and make the next step simple.</p>
      <a class="siteformo-button" style="background:#fff;color:#111827;" href="#contact">Contact Us Today</a>
    </div>
  </div>
</section>
""".strip()

    def _component_footer(self, business_name: str) -> str:
        return f"""
<footer class="siteformo-footer">
  <div class="siteformo-container">
    © {business_name}. Website package prepared for launch.
  </div>
</footer>
</div>
""".strip()


    def _component_animation_script(self) -> str:
        return """
<script>
(function () {
  const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const targets = document.querySelectorAll('.siteformo-hero, .siteformo-section, .siteformo-card, .siteformo-trust, .siteformo-final-cta');

  targets.forEach((element, index) => {
    element.classList.add('siteformo-reveal');
    if (index % 3 === 1) element.classList.add('siteformo-reveal-delay-1');
    if (index % 3 === 2) element.classList.add('siteformo-reveal-delay-2');
  });

  if (reduceMotion || !('IntersectionObserver' in window)) {
    targets.forEach(element => element.classList.add('siteformo-visible'));
    return;
  }

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('siteformo-visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -60px 0px' });

  targets.forEach(element => observer.observe(element));
})();
</script>
""".strip()


    def _build_final_generation_prompt(self, order: Order) -> str:
        """Build a generation prompt only from the Canonical Brief.

        This prevents prompt fragmentation: the example site, first questionnaire,
        second questionnaire, selected design preview and effects page are first
        normalized into one production brief.
        """
        import json

        canonical_brief = get_or_build_canonical_brief(order)

        return f"""
Create a Divi 5-ready website package using ONLY this Canonical Brief.

CRITICAL RULES:
- Do not copy the source/reference/example website. Use it only as a style, expectation and complexity signal.
- Follow selected preview DNA, design system, section logic and motion profile.
- Follow package constraints exactly. Do not add features outside the package.
- English only.
- Mobile-first.
- Premium, trustworthy, conversion-focused.
- Use simple semantic HTML sections.
- No external scripts.
- No markdown fences.
- Do not mention AI, OpenAI, SiteFormo internals, prompts, or generated content.

CANONICAL BRIEF JSON:
{json.dumps(canonical_brief, ensure_ascii=False, indent=2)[:22000]}
"""

    def _fallback_divi_html(self, order: Order) -> str:
        business_name = escape(
            getattr(order, "business_name", None)
            or getattr(order, "source_url", None)
            or "Client business"
        )

        description = escape(
            getattr(order, "desired_site_description", None)
            or "A premium website designed to convert visitors into qualified leads."
        )

        return f"""
<section class="siteformo-hero">
  <div class="siteformo-container">
    <p class="siteformo-eyebrow">Premium Website Experience</p>
    <h1>{business_name}</h1>
    <p>{description}</p>
    <a href="#contact" class="siteformo-button">Request a custom offer</a>
  </div>
</section>

<section class="siteformo-offer">
  <div class="siteformo-container">
    <h2>Built to explain your value clearly</h2>
    <p>This homepage is structured to help visitors understand the offer, trust the business, and take action.</p>

    <div class="siteformo-grid">
      <div>
        <h3>Clear positioning</h3>
        <p>A strong headline, simple message, and direct call to action.</p>
      </div>
      <div>
        <h3>Conversion structure</h3>
        <p>Sections are arranged to reduce friction and support decision-making.</p>
      </div>
      <div>
        <h3>Mobile-first layout</h3>
        <p>Designed for customers browsing from phones and tablets.</p>
      </div>
    </div>
  </div>
</section>

<section class="siteformo-trust">
  <div class="siteformo-container">
    <h2>Why customers should trust this business</h2>
    <ul>
      <li>Clear explanation of services or offer</li>
      <li>Trust-building proof and FAQ</li>
      <li>Simple contact path</li>
      <li>Professional visual direction</li>
    </ul>
  </div>
</section>

<section class="siteformo-faq">
  <div class="siteformo-container">
    <h2>FAQ</h2>
    <h3>What is the main goal of this page?</h3>
    <p>To turn visitors into qualified leads or customers.</p>
    <h3>Is this layout ready for editing?</h3>
    <p>Yes. The structure is prepared as clean HTML blocks for visual review and Divi editing.</p>
  </div>
</section>

<section id="contact" class="siteformo-final-cta">
  <div class="siteformo-container">
    <h2>Ready to move forward?</h2>
    <p>Request a custom offer and continue the project with a detailed brief.</p>
    <a href="#contact" class="siteformo-button">Start now</a>
  </div>
</section>
""".strip()

    def _build_brief_markdown(self, order: Order) -> str:
        brief_answers = (
            getattr(order, "extended_brief", None)
            or getattr(order, "brief_answers", None)
            or {}
        )

        if not brief_answers:
            return "- Generated from the first SiteFormo brief."

        if isinstance(brief_answers, dict):
            return "\n".join(f"- {key}: {value}" for key, value in brief_answers.items())

        return str(brief_answers)

    def _build_system_prompt(self) -> str:
        return (
            "You are a senior AI website strategist for a website sales platform.\n"
            "Your job is to generate a concise, practical website concept in English.\n\n"
            "Output rules:\n"
            "- Keep the answer client-friendly and easy to read in Telegram.\n"
            "- Be practical, not fluffy.\n"
            "- Do not mention technical implementation details.\n"
            "- Do not mention OpenAI, models, prompts, or internal reasoning.\n"
            "- Focus on business value, website structure, and conversion.\n"
            "- Keep the answer structured and compact.\n\n"
            "Return exactly these sections:\n"
            "1. Business Summary\n"
            "2. Recommended Website Structure\n"
            "3. Main Headline\n"
            "4. CTA\n"
            "5. Offer Direction\n"
            "6. Next Best Step\n"
        )

    def _build_user_prompt(
        self,
        user_input: str,
        mode: str,
        intake_data: Dict[str, Any],
    ) -> str:
        business_type = intake_data.get("business_type", "")
        goal = intake_data.get("goal", "")
        style = intake_data.get("style", "")
        pages = intake_data.get("pages", "")
        audience = intake_data.get("audience", "")
        notes = intake_data.get("notes", "")

        return (
            f"Flow mode: {mode}\n\n"
            "Structured intake:\n"
            f"- Business type: {business_type}\n"
            f"- Goal: {goal}\n"
            f"- Style: {style}\n"
            f"- Pages: {pages}\n"
            f"- Target audience: {audience}\n"
            f"- Notes: {notes}\n\n"
            "Raw user input:\n"
            f"{user_input}\n\n"
            "Create a strong website concept for this business."
        )

    def _fallback_response(
        self,
        user_input: str,
        mode: str,
        intake_data: Dict[str, Any],
    ) -> str:
        business_type = intake_data.get("business_type") or "service business"
        goal = intake_data.get("goal") or "get more qualified leads"
        style = intake_data.get("style") or "clean and modern"
        pages = intake_data.get("pages") or "Home, Services, About, Reviews, Contact"
        audience = intake_data.get("audience") or "potential customers"
        notes = intake_data.get("notes") or user_input or "No extra notes provided."

        headline = self._suggest_headline(business_type, goal)
        cta = self._suggest_cta(goal)

        return (
            "1. Business Summary\n"
            f"This project is for a {business_type} that wants to {goal}. "
            f"The website should feel {style} and speak clearly to {audience}.\n\n"
            "2. Recommended Website Structure\n"
            f"{pages}\n\n"
            "3. Main Headline\n"
            f"{headline}\n\n"
            "4. CTA\n"
            f"{cta}\n\n"
            "5. Offer Direction\n"
            "The website should build trust quickly, explain the value clearly, "
            "and guide visitors to one main action without friction.\n\n"
            "6. Next Best Step\n"
            "Confirm the business niche, target audience, and key offer so the final site "
            "structure and copy direction can be generated more precisely.\n\n"
            f"Notes used: {notes}\n"
            f"Flow mode: {mode}"
        )

    def _suggest_headline(self, business_type: str, goal: str) -> str:
        bt = business_type.lower()

        if "beauty" in bt or "salon" in bt or "studio" in bt:
            return "Feel confident with a beauty experience designed around you"
        if "restaurant" in bt or "cafe" in bt:
            return "A place worth coming back to"
        if "agency" in bt or "marketing" in bt:
            return "Growth-focused solutions that turn attention into results"
        if "real estate" in bt:
            return "Find the right property with confidence"
        if "clinic" in bt or "medical" in bt or "dental" in bt:
            return "Professional care with a personal approach"

        return f"A smarter website designed to help you {goal}"

    def _suggest_cta(self, goal: str) -> str:
        gl = goal.lower()

        if "booking" in gl or "book" in gl:
            return "Book your consultation"
        if "lead" in gl or "client" in gl:
            return "Get your free consultation"
        if "sale" in gl or "sell" in gl:
            return "Request your custom offer"
        if "call" in gl:
            return "Schedule a call"

        return "Get started today"

    def _summarize_extended_brief(
        self,
        order: Optional[Order],
        extended_brief: Dict[str, Any],
    ) -> Dict[str, Any]:
        contact = extended_brief.get("contact") or {}
        pricing = extended_brief.get("pricing") or {}
        answers = extended_brief.get("answers") if isinstance(extended_brief.get("answers"), dict) else {}
        additional_pages = (
            answers.get("pages")
            or extended_brief.get("additional_pages")
            or self._extract_answer_by_id(extended_brief, "additional_pages_builder")
            or []
        )

        return {
            "order_id": _safe_get(order, "id") or extended_brief.get("order_id"),
            "plan": extended_brief.get("plan") or _safe_get(order, "tier") or "",
            "business_name": (
                extended_brief.get("business_name")
                or answers.get("company_name")
                or _safe_get(order, "business_name")
                or _safe_get(order, "desired_site_description")
                or "Client business"
            ),
            "contact_email": contact.get("email") or _safe_get(order, "email") or "",
            "main_goal": answers.get("website_goal") or self._extract_answer_by_id(extended_brief, "website_goal"),
            "design_style": answers.get("design_style") or self._extract_answer_by_id(extended_brief, "design_style"),
            "logo_ordered": bool(extended_brief.get("logo_ordered") or answers.get("logo") == "I need a logo"),
            "pages": additional_pages,
            "pricing": pricing,
            "raw_brief": extended_brief,
        }

    def _extract_answer_by_id(self, brief: Dict[str, Any], answer_id: str) -> Any:
        answers = brief.get("answers") or []
        if not isinstance(answers, list):
            return None

        for item in answers:
            if isinstance(item, dict) and item.get("id") == answer_id:
                return item.get("selected") or item.get("extra") or item.get("other")
        return None


    def _schedule_device_screenshots_if_enabled(
        self,
        url: str,
        order_id: str,
        preview_id: str,
    ) -> Dict[str, str]:
        """
        Optional Playwright screenshot hook.

        By default this does NOT block preview generation. If you enable
        SITEFORMO_ENABLE_PLAYWRIGHT_SCREENSHOTS=true and have
        app/services/screenshot_service.py installed, screenshots are generated
        in a background thread so FastAPI/Railway does not crash from asyncio.run()
        inside an existing event loop.

        The immediate payload still returns desktop_image_url/mobile_image_url
        as the generated preview URL, so the UI and email keep working while
        background screenshots are being prepared.
        """
        enabled = os.getenv("SITEFORMO_ENABLE_PLAYWRIGHT_SCREENSHOTS", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        if not enabled:
            return {
                "status": "disabled",
                "desktop_image_url": url,
                "mobile_image_url": url,
            }

        try:
            from app.services.screenshot_service import generate_screenshots

            def _runner() -> None:
                try:
                    asyncio.run(
                        generate_screenshots(
                            url=url,
                            order_id=order_id,
                            preview_id=preview_id,
                        )
                    )
                except Exception as exc:
                    logger.exception(
                        "Background screenshot generation failed for order %s preview %s: %s",
                        order_id,
                        preview_id,
                        exc,
                    )

            threading.Thread(target=_runner, daemon=True).start()
            return {
                "status": "queued",
                "desktop_image_url": url,
                "mobile_image_url": url,
            }
        except Exception as exc:
            logger.exception(
                "Could not schedule screenshot generation for order %s preview %s: %s",
                order_id,
                preview_id,
                exc,
            )
            return {
                "status": "failed_to_schedule",
                "desktop_image_url": url,
                "mobile_image_url": url,
            }

    def _generate_or_fallback_preview_image(
        self,
        project_summary: Dict[str, Any],
        item: Dict[str, str],
        index: int,
        image_kind: str = "homepage",
    ) -> tuple[str, str]:
        """Generate one real OpenAI homepage preview image and return a public URL when possible."""
        fallback_url = self._build_screenshot_preview_url(project_summary, item, index)

        if not self.client:
            logger.warning("OPENAI_API_KEY is missing. Using SVG fallback for design preview %s.", index)
            return fallback_url, "svg_fallback_no_openai_key"

        prompt = self._build_openai_homepage_image_prompt(project_summary, item, index)

        try:
            image_bytes = self._generate_openai_image_bytes(prompt)
            if not image_bytes:
                return fallback_url, "svg_fallback_empty_openai_image"

            public_url = self._store_generated_image(
                image_bytes=image_bytes,
                order_id=str(project_summary.get("order_id") or "unknown-order"),
                filename=f"design-preview-{index}.png",
                content_type="image/png",
            )
            return public_url, "openai_image"
        except Exception as exc:
            logger.exception("OpenAI design preview image generation failed for option %s: %s", index, exc)
            return fallback_url, "svg_fallback_openai_error"

    def _build_openai_homepage_image_prompt(
        self,
        project_summary: Dict[str, Any],
        item: Dict[str, str],
        index: int,
    ) -> str:
        """
        Real business copy + offer + conversion-focused homepage screenshot prompt.
        One call = one preview prompt.
        """

        divi = DiviStyleGenerationService()
        layout_spec = divi.generate_layout_spec(project_summary, variant=index - 1)

        business_name = str(project_summary.get("business_name") or "Business")
        business_type = str(project_summary.get("business_type") or "service business")
        goal = str(project_summary.get("main_goal") or "get more clients")
        location = str(project_summary.get("location") or "Ireland")
        style = str(item.get("style") or layout_spec.get("style") or "modern")

        colors = str(
            item.get("color_direction")
            or layout_spec.get("design_system", {}).get("colors", {}).get("primary")
            or "clean palette"
        )

        plan = str(project_summary.get("plan") or "website package")
        pages = project_summary.get("pages") or []
        page_hint = (
            ", ".join([str(p.get("name") or p) for p in pages[:5]])
            if isinstance(pages, list)
            else str(pages)
        )

        detailed_brief = str(item.get("prompt") or "")
        sections = [s.get("type") for s in layout_spec.get("sections", [])]

        return f"""
Create a REALISTIC, HIGH-CONVERTING business website homepage screenshot.

CRITICAL:
This must look like a real company website that sells services.
It must not look like an AI-generated concept, poster, UI kit, or mockup.

ABSOLUTELY FORBIDDEN:
- No lorem ipsum
- No fake text
- No placeholder text
- No "Your company"
- No "Your business here"
- No blurred text
- No unreadable text
- No UI kit layout
- No device frames
- No browser mockup frame
- No abstract floating screens
- No AI-looking futuristic style
- Do not mention AI, OpenAI, SiteFormo, templates, prompts, or generated content

COPY QUALITY:
- Write like a real business, not AI
- Use clear, simple English
- No generic phrases like "we provide high quality solutions"
- No filler text
- Strong, specific headline
- Clear offer
- Short, sharp service descriptions
- Real CTA buttons such as "Get a Free Quote", "Book a Call", "Start Your Project", "Contact Us Today"

TRUST SECTION:
Include realistic credibility elements:
- Reviews
- Ratings
- Years of experience
- Client results
- Awards, stats, or proof elements where appropriate

DESIGN STYLE:
- Premium Divi / Webflow / Framer agency-level website
- Clean spacing
- Modern grid layout
- Strong typography hierarchy
- Professional alignment
- Conversion-focused
- Mobile-first feeling
- Looks ready to go live

LAYOUT STRUCTURE:
The homepage must follow this section structure:
{sections}

REQUIRED SECTIONS:
- Navigation with logo + menu
- Hero with strong headline + subheadline + CTA
- Services section with 3-4 services
- Trust / reviews / proof section
- About or explanation section
- Final CTA section

BUSINESS DETAILS:
Business: {business_name}
Type: {business_type}
Location: {location}
Goal: {goal}
Package / Plan: {plan}
Content hints: {page_hint}
Style direction: {style}
Color direction: {colors}
Detailed brief: {detailed_brief}

OUTPUT:
A realistic homepage screenshot with real business text, a clear offer, strong CTA, and premium visual quality.
"""


    def _generate_or_fallback_logo_image(
        self,
        project_summary: Dict[str, Any],
        item: Dict[str, str],
        index: int,
    ) -> tuple[str, str]:
        fallback_url = self._svg_data_url(
            item["label"],
            item["style"],
            ["#ffffff", "#f8fafc", "#111827", "#111827"],
            "Logo concept",
        )

        if not self.client:
            logger.warning("OPENAI_API_KEY is missing. Using SVG fallback for logo concept %s.", index)
            return fallback_url, "svg_fallback_no_openai_key"

        prompt = self._build_openai_logo_image_prompt(project_summary, item, index)

        try:
            image_bytes = self._generate_openai_image_bytes(prompt)
            if not image_bytes:
                return fallback_url, "svg_fallback_empty_openai_image"

            public_url = self._store_generated_image(
                image_bytes=image_bytes,
                order_id=str(project_summary.get("order_id") or "unknown-order"),
                filename=f"logo-concept-{index}.png",
                content_type="image/png",
            )
            return public_url, "openai_image"
        except Exception as exc:
            logger.exception("OpenAI logo image generation failed for option %s: %s", index, exc)
            return fallback_url, "svg_fallback_openai_error"

    def _generate_openai_image_bytes(self, prompt: str) -> Optional[bytes]:
        """
        Generate PNG bytes with the OpenAI Images API.

        Uses environment overrides:
        - OPENAI_IMAGE_MODEL, default gpt-image-1
        - OPENAI_IMAGE_SIZE, default 1024x1024
        - OPENAI_IMAGE_QUALITY, default medium
        """
        if not self.client:
            return None

        size = os.getenv("OPENAI_IMAGE_SIZE", "1024x1024").strip() or "1024x1024"
        quality = os.getenv("OPENAI_IMAGE_QUALITY", "medium").strip() or "medium"

        response = self.client.images.generate(
            model=self.image_model,
            prompt=prompt,
            size=size,
            quality=quality,
            n=1,
        )

        image_data = response.data[0]
        b64_json = getattr(image_data, "b64_json", None)
        if b64_json:
            return base64.b64decode(b64_json)

        # Some compatible providers return a URL instead of b64_json.
        image_url = getattr(image_data, "url", None)
        if image_url:
            downloaded = requests.get(image_url, timeout=45)
            downloaded.raise_for_status()
            return downloaded.content

        return None

    def _store_generated_image(
        self,
        image_bytes: bytes,
        order_id: str,
        filename: str,
        content_type: str = "image/png",
    ) -> str:
        """
        Upload generated preview images to Supabase Storage when configured.

        Email clients need a real public URL. If Supabase public storage is not
        configured, this returns a data URL fallback so the WordPress UI still works.
        """
        safe_order_id = quote(str(order_id), safe="") or "unknown-order"
        unique_name = f"{uuid.uuid4().hex}-{filename}"
        key = f"design-previews/{safe_order_id}/{unique_name}"

        supabase_url = (os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL") or "").rstrip("/")
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY") or ""
        bucket = os.getenv("SUPABASE_STORAGE_BUCKET") or os.getenv("SUPABASE_BUCKET") or "siteformo-assets"

        if supabase_url and service_key and bucket:
            upload_url = f"{supabase_url}/storage/v1/object/{bucket}/{key}"
            response = requests.post(
                upload_url,
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": content_type,
                    "x-upsert": "true",
                },
                data=image_bytes,
                timeout=45,
            )
            response.raise_for_status()
            return f"{supabase_url}/storage/v1/object/public/{bucket}/{key}"

        public_base_url = (os.getenv("SITEFORMO_PUBLIC_ASSET_BASE_URL") or "").rstrip("/")
        local_dir = os.getenv("SITEFORMO_GENERATED_ASSET_DIR")
        if public_base_url and local_dir:
            local_root = os.path.abspath(local_dir)
            local_path = os.path.join(local_root, key)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(image_bytes)
            return f"{public_base_url}/{key}"

        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    def _build_openai_logo_image_prompt(
        self,
        project_summary: Dict[str, Any],
        item: Dict[str, str],
        index: int,
    ) -> str:
        business_name = str(project_summary.get("business_name") or "Client business")
        return (
            "Create one clean logo concept on a plain white background. "
            "No mockup, no business card, no wall sign, no shadows, no 3D render. "
            "The logo should be scalable and suitable for a website header. "
            f"Business name: {business_name}. "
            f"Logo option {index}: {item.get('label')}. "
            f"Style: {item.get('style')}. "
            f"Detailed brief: {item.get('prompt')}."
        )

    def _svg_data_url(self, title: str, subtitle: str, palette: List[str], badge: str = "Homepage preview") -> str:
        """Create a lightweight screenshot-like SVG data URL."""
        bg, card, accent, text_color = palette
        safe_title = escape(title or "Website preview")
        safe_subtitle = escape(subtitle or "Custom SiteFormo design direction")
        safe_badge = escape(badge)
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="900" viewBox="0 0 1280 900">
  <rect width="1280" height="900" fill="{bg}"/>
  <rect x="86" y="74" width="1108" height="68" rx="26" fill="{card}" opacity="0.96"/>
  <circle cx="132" cy="108" r="10" fill="{accent}"/>
  <circle cx="164" cy="108" r="10" fill="{accent}" opacity="0.55"/>
  <circle cx="196" cy="108" r="10" fill="{accent}" opacity="0.28"/>
  <text x="246" y="116" font-family="Arial, sans-serif" font-size="24" font-weight="700" fill="{text_color}">{safe_badge}</text>
  <rect x="86" y="184" width="1108" height="354" rx="36" fill="{card}"/>
  <text x="136" y="266" font-family="Arial, sans-serif" font-size="25" font-weight="700" fill="{accent}">SITEFORMO DESIGN OPTION</text>
  <foreignObject x="136" y="300" width="650" height="150">
    <div xmlns="http://www.w3.org/1999/xhtml" style="font-family:Arial,sans-serif;font-size:54px;line-height:1.05;font-weight:800;color:{text_color};">{safe_title}</div>
  </foreignObject>
  <foreignObject x="136" y="456" width="560" height="70">
    <div xmlns="http://www.w3.org/1999/xhtml" style="font-family:Arial,sans-serif;font-size:24px;line-height:1.35;color:{text_color};opacity:.78;">{safe_subtitle}</div>
  </foreignObject>
  <rect x="822" y="260" width="270" height="196" rx="32" fill="{accent}" opacity="0.18"/>
  <rect x="860" y="302" width="194" height="24" rx="12" fill="{accent}"/>
  <rect x="860" y="350" width="154" height="18" rx="9" fill="{text_color}" opacity="0.30"/>
  <rect x="860" y="386" width="210" height="18" rx="9" fill="{text_color}" opacity="0.20"/>
  <rect x="136" y="594" width="302" height="148" rx="28" fill="{card}"/>
  <rect x="490" y="594" width="302" height="148" rx="28" fill="{card}"/>
  <rect x="844" y="594" width="302" height="148" rx="28" fill="{card}"/>
  <rect x="172" y="634" width="166" height="18" rx="9" fill="{accent}"/>
  <rect x="526" y="634" width="166" height="18" rx="9" fill="{accent}"/>
  <rect x="880" y="634" width="166" height="18" rx="9" fill="{accent}"/>
  <rect x="172" y="678" width="218" height="16" rx="8" fill="{text_color}" opacity="0.25"/>
  <rect x="526" y="678" width="218" height="16" rx="8" fill="{text_color}" opacity="0.25"/>
  <rect x="880" y="678" width="218" height="16" rx="8" fill="{text_color}" opacity="0.25"/>
  <rect x="136" y="790" width="264" height="54" rx="27" fill="{accent}"/>
  <text x="188" y="825" font-family="Arial, sans-serif" font-size="20" font-weight="700" fill="{card}">Select this design</text>
</svg>"""
        encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"

    def _build_screenshot_preview_url(
        self,
        project_summary: Dict[str, Any],
        item: Dict[str, str],
        index: int,
    ) -> str:
        business_name = str(project_summary.get("business_name") or "Client website")
        title = f"{business_name} — {item['style']}"
        subtitle = f"{project_summary.get('main_goal') or 'Conversion-focused homepage'} · {item['color_direction']}"
        palettes = [
            ["#f8fafc", "#ffffff", "#16a34a", "#0f172a"],
            ["#111827", "#1f2937", "#f4c430", "#f9fafb"],
            ["#eff6ff", "#ffffff", "#2563eb", "#111827"],
            ["#fff7ed", "#ffffff", "#047857", "#1f2937"],
            ["#f3f4f6", "#ffffff", "#111827", "#111827"],
        ]
        return self._svg_data_url(title, subtitle, palettes[(index - 1) % len(palettes)], item["label"])

    def _build_preview_prompts(self, project_summary: Dict[str, Any], extended_brief: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        """Build five distinct preview prompts using the SiteFormo enhancement layer."""
        if extended_brief:
            return build_preview_variation_prompts(extended_brief)

        fallback_brief = {
            "plan": project_summary.get("plan"),
            "answers": {
                "company_name": project_summary.get("business_name"),
                "website_goal": project_summary.get("main_goal"),
                "design_style": project_summary.get("design_style"),
                "design_quality": "wow",
                "pages": project_summary.get("pages") or [],
                "references": [],
            },
        }
        return build_preview_variation_prompts(fallback_brief)

    def _build_logo_prompts(self, project_summary: Dict[str, Any]) -> List[Dict[str, str]]:
        business_name = project_summary.get("business_name") or "Client business"
        return [
            {
                "label": "Logo A",
                "style": "minimal premium wordmark",
                "prompt": (
                    f"Create a minimal premium logo concept for {business_name}. "
                    "Clean wordmark, professional, scalable, white background, no mockup."
                ),
            },
            {
                "label": "Logo B",
                "style": "modern icon + wordmark",
                "prompt": (
                    f"Create a modern icon and wordmark logo concept for {business_name}. "
                    "Professional digital service style, white background, no mockup."
                ),
            },
            {
                "label": "Logo C",
                "style": "luxury refined brand mark",
                "prompt": (
                    f"Create a refined luxury logo concept for {business_name}. "
                    "Elegant typography, subtle symbol, white background, no mockup."
                ),
            },
        ]

    def _set_if_exists(self, obj: Any, field: str, value: Any) -> None:
        if hasattr(obj, field):
            setattr(obj, field, value)


# -------------------------------------------------------------------------
# BACKWARD-COMPATIBLE MODULE HELPERS
# -------------------------------------------------------------------------

def generate_site(db: Session, order: Order):
    service = GenerationService()
    return service.generate_final_package_for_order(db, order)


async def generate_design_previews(order_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Backward-compatible async helper for routes that do not have a DB session.

    It returns the same screenshot-preview payload shape used by
    generate_design_previews_for_order(), but does not try to import a missing
    orders_service module. Persisting is handled in order_routes.py.
    """
    service = GenerationService()
    preview_payload = service.build_design_preview_payload(order=None, extended_brief=payload)
    logger.info("Design screenshot preview payload prepared for order %s", order_id)
    return preview_payload

async def start_full_generation(order_id: str) -> Dict[str, Any]:
    """
    Backward-compatible async helper. DB persistence is handled by order_routes.py.
    """
    now = datetime.utcnow().isoformat()
    logger.info("Full generation start requested for order %s", order_id)
    return {
        "order_id": order_id,
        "status": "FULL_PRODUCTION_STARTED",
        "full_generation_started_at": now,
    }
