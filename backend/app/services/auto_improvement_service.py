from __future__ import annotations

import os
from typing import Any, Dict

from openai import OpenAI


class AutoImprovementService:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv(
            "OPENAI_IMPROVEMENT_MODEL",
            os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        ).strip()
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None

    def improve_site(
        self,
        site_content: str,
        brief: Dict[str, Any],
        package: str,
        regeneration_prompt: str,
    ) -> str:

        if not self.client:
            return site_content

        prompt = f"""
You are SiteFormo's website improvement engine.

Improve the generated website based on the review feedback.

Client package: {package}
Market: Ireland / EU

Client brief:
{brief}

Current website content:
{site_content}

Quality review instruction:
{regeneration_prompt}

Rules:
- Keep the same client business.
- Keep all correct client information.
- Improve weak sections.
- Strengthen hero, CTA, trust, mobile structure and offer clarity.
- Remove placeholder text.
- Remove generic AI-sounding text.
- Make it more suitable for a paid website package.
- Do not explain your work.
- Return only the improved website content/code.
"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.35,
            )

            improved = response.choices[0].message.content or ""

            if improved.strip():
                return improved.strip()

            return site_content

        except Exception:
            return site_content


def auto_improve(
    site_text: str,
    feedback_prompt: str,
    brief: Dict[str, Any] | None = None,
    package: str = "starter",
) -> str:
    return AutoImprovementService().improve_site(
        site_content=site_text,
        brief=brief or {},
        package=package,
        regeneration_prompt=feedback_prompt,
    )


# ✅ ВАЖНО: функция ДОЛЖНА быть на уровне файла (НЕ внутри другой функции)
def improve_preview_prompt(original_prompt: str, feedback: str | None = None) -> str:
    """
    Improves the design preview prompt before quality generation.
    """

    if not original_prompt:
        original_prompt = ""

    improvement_instructions = """
Improve this website design preview prompt.

Make the result:
- realistic
- modern
- conversion-focused
- suitable for a real business website
- clear for desktop and mobile screenshots
- visually premium
- not generic
- not overly artistic
- not like a concept poster

The design should look like a real production website homepage.
"""

    if feedback:
        improvement_instructions += f"""

Additional quality feedback:
{feedback}
"""

    return f"""
{original_prompt}

{improvement_instructions}
""".strip()