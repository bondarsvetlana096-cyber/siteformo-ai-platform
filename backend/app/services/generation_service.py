import logging
import os
from html import escape
from typing import Any, Dict, Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from app.models.order import FinalPackage, Order, OrderStatus

logger = logging.getLogger(__name__)


class GenerationService:
    """
    Generates website concepts and final Divi-ready packages.

    Behavior:
    - Uses OpenAI if OPENAI_API_KEY is present
    - Falls back to a local template if OpenAI is not configured
    - Supports Telegram concept generation
    - Supports order approval -> final package generation
    """

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_MODEL", "gpt-5").strip()
        self.client: Optional[OpenAI] = (
            OpenAI(api_key=self.api_key) if self.api_key else None
        )

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
        Creates a final Divi-ready package after approval.
        Safe fallback: if OpenAI fails, still creates a usable package.
        """

        existing_package = (
            db.query(FinalPackage)
            .filter(FinalPackage.order_id == order.id)
            .order_by(FinalPackage.id.desc())
            .first()
        )

        if existing_package:
            order.status = OrderStatus.FINAL_READY
            db.commit()
            db.refresh(order)
            return existing_package

        selected_concept_label = "A"
        divi_html = self._generate_divi_html(order)
        brief_markdown = self._build_brief_markdown(order)

        package = FinalPackage(
            order_id=order.id,
            selected_concept_label=selected_concept_label,
            divi_html=divi_html,
            brief_markdown=brief_markdown,
            notes=note,
        )

        db.add(package)
        order.status = OrderStatus.FINAL_READY

        db.commit()
        db.refresh(order)
        db.refresh(package)

        logger.info("Final package generated for order %s", order.id)

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
        brief_answers = getattr(order, "brief_answers", None) or {}
        pricing_reasoning = getattr(order, "pricing_reasoning", "") or ""

        return (
            "Create a Divi 5-ready homepage HTML package.\n\n"
            "Requirements:\n"
            "- English only\n"
            "- Mobile-first\n"
            "- Premium, trustworthy, conversion-focused\n"
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
        brief_answers = getattr(order, "brief_answers", None) or {}

        if not brief_answers:
            return "- Generated from the first SiteFormo brief."

        return "\n".join(f"- {key}: {value}" for key, value in brief_answers.items())

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


def generate_site(db: Session, order: Order):
    service = GenerationService()
    return service.generate_final_package_for_order(db, order)