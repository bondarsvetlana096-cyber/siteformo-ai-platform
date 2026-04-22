import os
import logging
from typing import Any, Dict, Optional

from openai import OpenAI


logger = logging.getLogger(__name__)


class GenerationService:
    """
    Generates a website concept for Telegram users.

    Behavior:
    - Uses OpenAI if OPENAI_API_KEY is present
    - Falls back to a local template if OpenAI is not configured
    - Returns user-friendly plain text suitable for Telegram
    """

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_MODEL", "gpt-5").strip()
        self.client: Optional[OpenAI] = OpenAI(api_key=self.api_key) if self.api_key else None

    def generate_site_concept(
        self,
        user_input: str,
        mode: str = "describe_site",
        intake_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Main public method.

        Args:
            user_input: Raw user text or collected flow text
            mode: Flow mode, e.g. 'describe_site' or 'send_websites'
            intake_data: Optional structured dict with parsed fields

        Returns:
            Telegram-friendly generated concept text
        """
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