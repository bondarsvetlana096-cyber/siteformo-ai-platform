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

            previews.append(
                {
                    "id": preview_id,
                    "type": "homepage_screenshot",
                    "label": item["label"],
                    "style": item["style"],
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
        Runs quality checks against the real final generated HTML.

        This is the important production layer:
        final HTML -> technical check -> AI review -> auto-fix loop -> final decision.
        """
        extended_brief = (
            getattr(order, "extended_brief", None)
            or getattr(order, "brief_answers", None)
            or {}
        )
        if not isinstance(extended_brief, dict):
            extended_brief = {"raw_brief": str(extended_brief)}

        package_value = self._extract_package_for_quality(order)
        rules = get_package_rules(package_value)

        package = str(rules.get("package") or package_value or "starter")
        target_score = float(rules.get("target_score") or 7.5)
        max_iterations = int(rules.get("max_quality_iterations") or rules.get("max_iterations") or 1)
        max_warnings = int(rules.get("max_warnings") or 5)

        current_html = html or ""
        history: List[Dict[str, Any]] = []
        final_decision: Dict[str, Any] = {
            "status": "MANUAL_REVIEW_REQUIRED",
            "ready_to_send": False,
            "overall_score": 0,
            "critical_errors": ["No quality decision produced"],
            "warnings": [],
        }

        for attempt in range(0, max_iterations + 1):
            preview_like_payload = {
                "id": "final_html",
                "type": "final_divi_html",
                "label": "Final generated website",
                "prompt": current_html,
                "html": current_html,
            }

            # Reuse your existing technical checker safely.
            technical = technical_check_preview(preview_like_payload, extended_brief)

            # Review the actual HTML, not just the concept prompt.
            review = review_site(
                site_content=current_html,
                brief=extended_brief,
                package=package,
                target_score=target_score,
            )

            decision = decide_preview_status(
                technical,
                review,
                target_score,
                max_warnings,
            )

            history.append(
                {
                    "attempt": attempt,
                    "technical": technical,
                    "review": review,
                    "decision": decision,
                }
            )

            final_decision = decision

            if decision.get("ready_to_send"):
                break

            if attempt >= max_iterations:
                break

            regeneration_prompt = (
                review.get("regeneration_prompt")
                or "Improve this final website HTML with stronger offer clarity, hero, CTA, trust elements, mobile readiness and package fit."
            )

            current_html = auto_improve(
                site_text=current_html,
                feedback_prompt=regeneration_prompt,
                brief=extended_brief,
                package=package,
            )

        ready = bool(final_decision.get("ready_to_send"))
        status = "READY_TO_SEND" if ready else "MANUAL_REVIEW_REQUIRED"

        return {
            "status": status,
            "ready_to_send": ready,
            "package": package,
            "target_score": target_score,
            "max_iterations": max_iterations,
            "final_score": float(final_decision.get("overall_score") or 0),
            "critical_errors": final_decision.get("critical_errors") or [],
            "warnings": final_decision.get("warnings") or [],
            "history": history,
            "final_decision": final_decision,
            "html": current_html,
        }


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
                order.status = OrderStatus.FINAL_READY
            except Exception:
                self._set_if_exists(order, "status", "FINAL_READY")
            db.commit()
            db.refresh(order)
            return existing_package

        selected_concept_label = (
            _safe_get(order, "selected_design_id")
            or _safe_get(order, "selected_design_url")
            or "A"
        )

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
            try:
                order.status = OrderStatus.FINAL_PAYMENT_REQUIRED
            except Exception:
                self._set_if_exists(order, "status", "FINAL_PAYMENT_REQUIRED")
            self._set_if_exists(order, "generation_status", "FINAL_PAYMENT_REQUIRED")
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
        if not self.client:
            return self._fallback_divi_html(order)

        prompt = self._build_final_generation_prompt(order)

        try:
            response = self.client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior website conversion strategist and Divi 5 layout writer. "
                            "Generate clean, mobile-first, editable HTML sections for a homepage. "
                            "Use English only. Do not mention OpenAI. Do not include markdown fences."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            output_text = getattr(response, "output_text", None)

            if output_text and output_text.strip():
                return output_text.strip()

            return self._fallback_divi_html(order)

        except Exception as exc:
            logger.exception("Final OpenAI generation failed: %s", exc)
            return self._fallback_divi_html(order)

    def _build_final_generation_prompt(self, order: Order) -> str:
        business_name = getattr(order, "business_name", "") or "Client business"
        source_url = getattr(order, "source_url", "") or ""
        description = getattr(order, "desired_site_description", "") or ""
        brief_answers = (
            getattr(order, "extended_brief", None)
            or getattr(order, "brief_answers", None)
            or {}
        )
        pricing_reasoning = getattr(order, "pricing_reasoning", "") or ""
        selected_design = (
            getattr(order, "selected_design_url", "") or getattr(order, "selected_design_id", "") or ""
        )

        return (
            "Create a Divi 5-ready homepage HTML package based on the client's approved design direction.\n\n"
            "Requirements:\n"
            "- English only\n"
            "- Mobile-first\n"
            "- Premium, trustworthy, conversion-focused\n"
            "- Follow the selected design direction as closely as text HTML can support\n"
            "- Clear hero section\n"
            "- Services / offer section\n"
            "- Trust section\n"
            "- FAQ section\n"
            "- Final CTA section\n"
            "- Use simple semantic HTML\n"
            "- No external scripts\n"
            "- No markdown fences\n\n"
            "Project data:\n"
            f"- Business name: {business_name}\n"
            f"- Source URL / old site: {source_url}\n"
            f"- Description: {description}\n"
            f"- Pricing reasoning: {pricing_reasoning}\n"
            f"- Selected design: {selected_design}\n"
            f"- Brief answers: {brief_answers}\n"
        )

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
        business_name = str(project_summary.get("business_name") or "Client business")
        goal = str(project_summary.get("main_goal") or "get more clients")
        style = str(item.get("style") or "modern premium")
        colors = str(item.get("color_direction") or "clean premium palette")
        plan = str(project_summary.get("plan") or "website package")
        pages = project_summary.get("pages") or []
        page_hint = ", ".join([str(p.get("name") or p) for p in pages[:5]]) if isinstance(pages, list) else str(pages)
        detailed_brief = str(item.get("prompt") or "")

        return f"""
Create a HIGH-END, REALISTIC website homepage screenshot.

CRITICAL RULES:
- This must look like a REAL live website, not a concept
- No mockups
- No dribbble-style compositions
- No UI kits
- No device frames, no iPhone frames, no laptop frames
- No floating or abstract layouts
- No fake UI frames
- No blurred text
- No lorem ipsum
- No placeholders like "Your business here"
- All visible text must be readable and realistic
- Do not mention AI, OpenAI, SiteFormo, templates, prompts, or placeholders

STRUCTURE REQUIREMENTS:
- Top navigation with logo and menu items
- Hero section with strong headline, short subheadline, and primary CTA button
- Services or features section with 3-4 cards
- Trust section with reviews, stats, awards, partner logos, or credibility proof
- About or explanation block
- Final CTA section

LAYOUT RULES:
- Clean modern layout like a premium Webflow or Framer site
- Proper spacing between sections
- Clear visual hierarchy
- Large readable headings
- Professional typography
- Realistic alignment and spacing
- Mobile-first layout feeling
- Premium, trustworthy, conversion-focused look

TEXT RULES:
- Use real English marketing text
- Headlines must sound like real businesses
- CTA must be realistic, for example "Get a quote", "Book a call", or "Start your project"
- Copy should match the business goal

BUSINESS:
{business_name}

GOAL:
{goal}

PACKAGE / PLAN:
{plan}

PAGE / CONTENT HINTS:
{page_hint}

STYLE DIRECTION:
{style}

COLOR DIRECTION:
{colors}

DETAILED BRIEF:
{detailed_brief}

OUTPUT:
A realistic full homepage screenshot that looks ready to go live.
The result should look like a premium website built by a professional agency and ready to be published.
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
