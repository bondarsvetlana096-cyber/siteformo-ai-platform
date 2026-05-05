# backend/app/services/prompt_builder_service.py

from typing import Any, Dict, List


def _safe(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, list):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip()


def _list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def build_site_generation_prompt(brief: Dict[str, Any], package: str = "starter") -> str:
    """
    Turns the extended questionnaire answers into a strong website generation prompt.
    Used after /extended-brief before generation_service.py.
    """

    package = _safe(package, "starter").lower()

    business_name = _safe(brief.get("business_name"), "the client business")
    business_description = _safe(brief.get("business_description"))
    target_audience = _safe(brief.get("target_audience"))
    services = _safe(brief.get("services"))
    pages = _list(brief.get("pages"))
    extra_pages = _safe(brief.get("extra_pages"))
    home_content = _safe(brief.get("home_content"))
    custom_content = _safe(brief.get("custom_content"))
    style = _safe(brief.get("style"), "modern")
    reference_site = _safe(brief.get("reference_site"))
    colors = _safe(brief.get("colors"))
    features = _list(brief.get("features"))
    phone = _safe(brief.get("phone"))
    email = _safe(brief.get("email"))
    address = _safe(brief.get("address"))
    instagram = _safe(brief.get("instagram"))
    facebook = _safe(brief.get("facebook"))
    linkedin = _safe(brief.get("linkedin"))
    notes = _safe(brief.get("notes"))

    package_rules = {
        "starter": {
            "quality_level": "clean, simple, fast-launch landing page",
            "depth": "keep the structure focused and conversion-oriented",
            "target_score": "7.5+",
            "iterations": "basic quality"
        },
        "business": {
            "quality_level": "professional small-business website",
            "depth": "include stronger trust, clearer sections and better conversion flow",
            "target_score": "8.0+",
            "iterations": "enhanced quality"
        },
        "premium": {
            "quality_level": "premium website with stronger UX, copy and design polish",
            "depth": "make it feel like a high-value €2450+ project",
            "target_score": "8.5+",
            "iterations": "advanced quality"
        },
        "custom": {
            "quality_level": "advanced custom business website",
            "depth": "include strategic positioning, deeper UX and premium structure",
            "target_score": "9.0+",
            "iterations": "maximum quality"
        }
    }

    rules = package_rules.get(package, package_rules["starter"])

    prompt = f"""
You are SiteFormo's senior website generation system.

Create a complete website draft for a paying client in Ireland / EU.

This is NOT a generic template.
This must be a production-quality website draft based on the client's questionnaire.

PROJECT PACKAGE:
- Package: {package}
- Expected quality: {rules["quality_level"]}
- Required depth: {rules["depth"]}
- Target internal quality score: {rules["target_score"]}

CLIENT BUSINESS:
- Business name: {business_name}
- Business description: {business_description}
- Target audience: {target_audience}
- Services / products: {services}

WEBSITE STRUCTURE:
- Requested pages: {", ".join(pages) if pages else "Use best-practice pages for this package"}
- Extra pages requested: {extra_pages or "None"}
- Homepage requirements: {home_content}
- Specific content to include: {custom_content or "None provided"}

DESIGN DIRECTION:
- Preferred style: {style}
- Preferred colors: {colors or "Choose suitable premium colors"}
- Reference website: {reference_site or "No reference provided"}
- Important: If a reference is provided, use it only for inspiration. Do not copy it directly.

FEATURES:
- Requested features: {", ".join(features) if features else "Contact form / enquiry CTA"}
- Phone: {phone or "Not provided"}
- Email: {email or "Not provided"}
- Address: {address or "Not provided"}

SOCIAL MEDIA:
- Instagram: {instagram or "Not provided"}
- Facebook: {facebook or "Not provided"}
- LinkedIn: {linkedin or "Not provided"}

ADDITIONAL CLIENT NOTES:
{notes or "None"}

WEBSITE REQUIREMENTS:
1. Strong hero section:
   - clear headline
   - clear subheadline
   - strong CTA
   - explain what the business does in 3 seconds

2. Conversion structure:
   - benefits
   - services / offer
   - trust section
   - process / how it works
   - contact CTA
   - FAQ if useful

3. Ireland / EU market fit:
   - use natural professional English
   - avoid American overhype
   - use realistic wording
   - make it suitable for Irish small businesses

4. Design quality:
   - modern responsive layout
   - mobile-first structure
   - premium spacing
   - no cheap template feeling
   - consistent typography and hierarchy

5. Content quality:
   - no lorem ipsum
   - no placeholder text
   - no AI-sounding phrases
   - no fake statistics
   - no exaggerated claims
   - no “we are the best” generic wording

6. Technical output:
   - produce a complete website file
   - mobile responsive
   - clean structure
   - all CTAs visible
   - contact section included

FINAL OUTPUT:
Return only the generated website content/code.
Do not explain what you did.
Do not include markdown comments outside the website.
"""
    return prompt.strip()


def build_regeneration_prompt(
    brief: Dict[str, Any],
    current_site: str,
    review_result: Dict[str, Any],
    package: str = "starter"
) -> str:
    """
    Creates a stronger regeneration prompt after AI review finds problems.
    """

    base_prompt = build_site_generation_prompt(brief, package)

    issues = review_result.get("issues") or review_result.get("main_problems") or []
    must_fix = review_result.get("must_fix") or []
    regeneration_prompt = review_result.get("regeneration_prompt") or ""

    if isinstance(issues, list):
        issues_text = "\n".join(f"- {item}" for item in issues)
    else:
        issues_text = str(issues)

    if isinstance(must_fix, list):
        must_fix_text = "\n".join(f"- {item}" for item in must_fix)
    else:
        must_fix_text = str(must_fix)

    return f"""
{base_prompt}

The previous generated version was reviewed and needs improvement.

CURRENT SITE:
{current_site}

REVIEW ISSUES:
{issues_text or "No specific issues listed"}

MUST FIX:
{must_fix_text or "No must-fix items listed"}

REGENERATION INSTRUCTION:
{regeneration_prompt or "Improve the website quality, clarity, hero section, CTA strength, trust elements, mobile structure and visual polish."}

IMPORTANT:
- Keep the same client business.
- Keep the same package level.
- Do not remove valid client information.
- Improve weak sections instead of rewriting randomly.
- Make the next version stronger, clearer and more premium.
- Remove any placeholder, generic or AI-sounding text.

Return only the improved website content/code.
""".strip()


def build_mobile_review_prompt(brief: Dict[str, Any], package: str = "starter") -> str:
    """
    Prompt for screenshot-based mobile review.
    Use with screenshots from Playwright or another screenshot service.
    """

    business_name = _safe(brief.get("business_name"), "client business")

    return f"""
You are reviewing the mobile version of a generated SiteFormo website.

Client business: {business_name}
Package: {package}
Market: Ireland / EU

Check the screenshot for:
- broken layout
- text overflowing
- buttons too small
- hero section too tall or unclear
- bad spacing
- hidden CTA
- unreadable text
- sections visually broken
- cheap template appearance

Return ONLY valid JSON:

{{
  "mobile_passed": true,
  "mobile_score": 0,
  "critical_errors": [],
  "warnings": [],
  "fix_prompt": ""
}}
""".strip()


def build_final_delivery_prompt(
    brief: Dict[str, Any],
    site_content: str,
    package: str = "starter"
) -> str:
    """
    Final pre-delivery check prompt.
    """

    business_name = _safe(brief.get("business_name"), "client business")

    return f"""
You are performing the final pre-delivery check for a SiteFormo website.

Client business: {business_name}
Package: {package}
Market: Ireland / EU

Website content:
{site_content}

Check:
1. No lorem ipsum
2. No placeholder company text
3. Clear hero
4. Strong CTA
5. Contact section exists
6. Website matches client brief
7. Text sounds natural
8. No obvious legal or trust problems
9. Looks suitable for a paid website package
10. Ready to send to client

Return ONLY valid JSON:

{{
  "ready_to_send": true,
  "score": 0,
  "critical_errors": [],
  "warnings": [],
  "next_action": "SEND_TO_CLIENT"
}}

Possible next_action:
- SEND_TO_CLIENT
- AUTO_FIX
- REGENERATE
- MANUAL_REVIEW
""".strip()