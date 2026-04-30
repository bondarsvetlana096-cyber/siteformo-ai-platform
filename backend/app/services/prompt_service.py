from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _answers(data: Dict[str, Any]) -> Dict[str, Any]:
    answers = data.get("answers")
    if isinstance(answers, dict):
        return answers
    return {}


def normalize_extended_brief(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert the SiteFormo questionnaire payload into one stable AI input shape.

    The questionnaire intentionally limits free text. This normalizer turns the
    client's simple choices into useful design instructions for OpenAI.
    """
    data = _safe_dict(data)
    answers = _answers(data)
    contact = _safe_dict(data.get("contact"))

    pages = (
        _safe_list(answers.get("pages"))
        or _safe_list(data.get("additional_pages"))
        or _safe_list(data.get("pages"))
    )

    references = answers.get("references")
    if references is None:
        references = data.get("references")
    references = [str(item).strip() for item in _safe_list(references) if str(item).strip()]

    return {
        "order_id": _clean(data.get("order_id")),
        "plan": _clean(data.get("plan"), "Business"),
        "company_name": _clean(answers.get("company_name") or data.get("business_name"), "Client business"),
        "company_location": _clean(answers.get("company_location"), "Ireland"),
        "business_type": _clean(answers.get("business_type"), "Local service business"),
        "website_goal": _clean(answers.get("website_goal"), "Get more leads"),
        "design_style": _clean(answers.get("design_style"), "premium_business"),
        "design_quality": _clean(answers.get("design_quality"), "wow"),
        "improve_references": bool(answers.get("improve_references", True)),
        "photo_option": _clean(answers.get("photo_option"), "client_photos"),
        "video_option": _clean(answers.get("video_option"), "no_video"),
        "video_url": _clean(answers.get("video_url")),
        "form_protection": _clean(answers.get("form_protection"), "no_extra_protection"),
        "logo_ordered": bool(data.get("logo_ordered") or answers.get("logo") == "I need a logo"),
        "logo": _clean(answers.get("logo"), "I do not need a logo"),
        "social_networks": _safe_dict(answers.get("social_networks")),
        "pages": pages,
        "references": references,
        "hosting": _safe_dict(answers.get("hosting") or data.get("hosting")),
        "pricing": _safe_dict(data.get("pricing")),
        "contact": contact,
    }


def _style_text(style: str) -> str:
    mapping = {
        "clean_modern": "clean, modern, professional and easy to read",
        "premium_business": "premium business, polished, high-trust and professional",
        "bold_startup": "bold, modern, dynamic and conversion-focused",
        "luxury_high_end": "luxury, high-end, elegant and visually refined",
    }
    return mapping.get(style, style or "modern premium business")


def _quality_text(quality: str) -> str:
    mapping = {
        "standard": "Clean and professional. Avoid over-design, but still make the result polished.",
        "high_end": "High-end and premium. Improve spacing, typography, visual hierarchy and trust sections.",
        "wow": "WOW design. Make it visually impressive, premium, modern and stronger than typical websites in this industry.",
    }
    return mapping.get(quality, mapping["wow"])


def _page_line(page: Dict[str, Any], index: int) -> str:
    name = _clean(page.get("name"), f"Page {index}")
    page_type = _clean(page.get("type"), "business page")
    goal = _clean(page.get("goal") or page.get("purpose"), "Help visitors understand and take action")
    blocks = page.get("blocks")
    blocks_text = ", ".join(str(item) for item in blocks) if isinstance(blocks, list) else _clean(blocks)
    ai_command = _clean(page.get("ai_command"))
    extra = f" Suggested blocks: {blocks_text}." if blocks_text else ""
    command = f" AI command: {ai_command}" if ai_command else ""
    return f"- {name} ({page_type}). Goal: {goal}.{extra}{command}"


def build_ai_prompt(data: Dict[str, Any]) -> str:
    brief = normalize_extended_brief(data)

    page_lines = [
        _page_line(page, index)
        for index, page in enumerate(_safe_list(brief["pages"]), start=1)
        if isinstance(page, dict)
    ]
    pages_text = "\n".join(page_lines) or "- Home page, Services page, About page, Contact page. Build a complete business website structure."

    references = brief["references"]
    if references:
        reference_block = "\n".join(f"- {url}" for url in references)
        reference_instruction = (
            "Reference websites were provided. Use them only as inspiration. "
            "Do not copy them. Improve their layout, clarity, hierarchy, spacing and premium feel."
        )
    else:
        reference_block = "No reference websites provided."
        reference_instruction = (
            "No references were provided. Create a best-in-class design for this business type using modern UX and premium visual standards."
        )

    if brief["form_protection"] == "invisible_spam_protection":
        form_protection = "All contact/booking forms should include invisible spam protection such as honeypot or reCAPTCHA v3, with no visible friction for users."
    else:
        form_protection = "Use clean validated forms. Do not add visible CAPTCHA unless specifically required later."

    prompt = f"""
Create a high-quality SiteFormo website design preview.

Business context:
- Company/project name: {brief['company_name']}
- Location: {brief['company_location']}
- Business type: {brief['business_type']}
- Main website goal: {brief['website_goal']}
- Plan: {brief['plan']}

Design direction:
- Style: {_style_text(brief['design_style'])}
- Quality level: {_quality_text(brief['design_quality'])}

Pages to design:
{pages_text}

Media direction:
- Photos: {brief['photo_option']}
- Video: {brief['video_option']}{' (' + brief['video_url'] + ')' if brief['video_url'] else ''}
- Logo: {brief['logo']}

Form protection:
{form_protection}

Reference websites:
{reference_block}

Reference handling rule:
{reference_instruction}

Always apply the SiteFormo Enhancement Layer:
- Do not simply follow the client input literally; interpret and improve it.
- Make the design better than a basic template.
- Use strong visual hierarchy, premium spacing, modern typography and clear CTAs.
- Build trust with proof blocks, outcome-focused copy, professional section composition and conversion-focused buttons.
- Keep the website clear and simple for a small business client, but make the execution feel premium.
- Avoid outdated layouts, clutter, weak hero sections, generic stock-template composition and low-contrast design.
- Use English only. Do not mention OpenAI or AI to the client.

Primary CTA logic:
- If the goal is leads or service sales, use buttons such as "Request a Quote", "Book a Consultation" or "Get Started".
- If the goal is portfolio, use "View Our Work" plus a secondary enquiry CTA.
- If the goal is pricing/services, use "View Services" and "Request a Custom Offer".

Output requirement:
Create a polished website screenshot/design concept that can become one of 5 client-selectable previews.
"""
    return prompt.strip()


def build_preview_variation_prompts(data: Dict[str, Any]) -> List[Dict[str, str]]:
    base_prompt = build_ai_prompt(data)
    variations = [
        ("Design A", "Recommended premium direction", "clean premium business", "white, deep navy, soft green accents"),
        ("Design B", "Luxury high-end direction", "luxury dark premium", "charcoal, black, champagne gold accents"),
        ("Design C", "Bold conversion direction", "modern bold conversion", "white, electric blue, strong contrast"),
        ("Design D", "Warm trust direction", "warm local trustworthy", "cream, forest green, warm neutral tones"),
        ("Design E", "Minimal corporate direction", "minimal corporate premium", "light grey, navy, clean monochrome accents"),
    ]
    return [
        {
            "label": label,
            "style": style,
            "color_direction": colors,
            "prompt": (
                f"{base_prompt}\n\nVariation instruction: {description}. "
                f"Use the visual style '{style}' and color direction '{colors}'. "
                "Make this variation distinct from the other four previews while keeping the same business strategy."
            ),
        }
        for label, description, style, colors in variations
    ]
