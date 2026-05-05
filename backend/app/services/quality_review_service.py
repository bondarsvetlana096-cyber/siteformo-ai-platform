from __future__ import annotations

import json
import os
from typing import Any, Dict

from openai import OpenAI


CRITICAL_CATEGORIES = [
    "offer_clarity",
    "hero_quality",
    "cta_strength",
    "mobile_readiness",
    "brief_alignment",
]

CATEGORY_KEYS = [
    "offer_clarity",
    "hero_quality",
    "cta_strength",
    "trust_elements",
    "copy_quality",
    "visual_quality",
    "mobile_readiness",
    "package_fit",
    "brief_alignment",
    "ireland_eu_market_fit",
]


def _safe_json_loads(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except Exception:
                pass

    return {
        "overall_score": 0,
        "approved": False,
        "category_scores": {},
        "critical_errors": ["AI review returned invalid JSON"],
        "warnings": [],
        "regeneration_prompt": (
            "Regenerate this website with a clearer offer, stronger hero, "
            "better CTA, trust elements, mobile-first layout, and cleaner premium styling."
        ),
        "next_action": "REGENERATE",
    }


def _normalise_package(package: str) -> str:
    value = str(package or "starter").strip().lower()

    if value in {"premium", "reference"}:
        return "premium"

    if value in {"custom", "advanced"}:
        return "custom"

    if value in {"starter", "business"}:
        return value

    return "starter"


def _fallback_review(site_content: str, brief: Dict[str, Any], package: str, target_score: float) -> Dict[str, Any]:
    text = f"{site_content} {json.dumps(brief, ensure_ascii=False)}".lower()

    score = 6.8

    if "<h1" in text or "hero" in text:
        score += 0.3

    if "contact" in text or "book" in text or "quote" in text or "cta" in text:
        score += 0.4

    if "testimonial" in text or "review" in text or "guarantee" in text or "trusted" in text:
        score += 0.4

    if "ireland" in text or "irish" in text or "eu" in text:
        score += 0.3

    if "lorem ipsum" in text or "your company" in text or "placeholder" in text:
        score -= 1.5

    if package in {"premium", "custom"}:
        score -= 0.4

    score = max(0, min(round(score, 2), 8.2))

    category_scores = {key: score for key in CATEGORY_KEYS}

    approved = score >= target_score and "lorem ipsum" not in text and "your company" not in text

    return {
        "overall_score": score,
        "approved": approved,
        "category_scores": category_scores,
        "critical_errors": [] if approved else ["Fallback review could not fully verify generated website quality"],
        "warnings": ["Fallback review used because OpenAI review was unavailable"],
        "main_problems": [] if approved else ["Website requires AI review or regeneration before delivery"],
        "must_fix": [] if approved else ["Run full quality review or regenerate the website"],
        "nice_to_have": [],
        "regeneration_prompt": (
            "Regenerate this website with a stronger hero section, clearer offer, stronger CTA, "
            "more trust elements, better mobile structure, Ireland/EU market fit, and more premium visual quality."
        ),
        "next_action": "READY_TO_SEND" if approved else "REGENERATE",
        "manual_review_required": False,
    }


def decide_next_action(result: Dict[str, Any], target_score: float, package: str) -> str:
    score = float(result.get("overall_score") or 0)
    categories = result.get("category_scores") or {}
    critical_errors = result.get("critical_errors") or []

    if critical_errors:
        return "REGENERATE"

    for key in CRITICAL_CATEGORIES:
        if float(categories.get(key) or 0) < 7:
            return "REGENERATE"

    if package in {"premium", "custom"}:
        if float(categories.get("visual_quality") or 0) < 8:
            return "AUTO_FIX"
        if float(categories.get("package_fit") or 0) < 8:
            return "AUTO_FIX"

    if score >= target_score:
        return "READY_TO_SEND"

    if score >= target_score - 0.5:
        return "AUTO_FIX"

    return "REGENERATE"


class QualityReviewService:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_REVIEW_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None

    def review_site(
        self,
        site_content: str,
        brief: Dict[str, Any],
        package: str,
        target_score: float,
    ) -> Dict[str, Any]:
        package = _normalise_package(package)

        if not self.client:
            return _fallback_review(site_content, brief, package, target_score)

        prompt = f"""
You are the SiteFormo quality gate.

Review a generated website for a paying client.

Target market: Ireland / EU
Client package: {package}
Target score: {target_score}

Client brief JSON:
{json.dumps(brief, ensure_ascii=False)[:10000]}

Generated website content:
{site_content[:20000]}

Score each category from 0 to 10:
- offer_clarity
- hero_quality
- cta_strength
- trust_elements
- copy_quality
- visual_quality
- mobile_readiness
- package_fit
- brief_alignment
- ireland_eu_market_fit

Strict rules:
- If the offer is generic, offer_clarity must be below 8.
- If hero is weak or unclear, hero_quality must be below 8.
- If CTA is missing or weak, cta_strength must be below 8.
- If trust elements are missing, trust_elements must be below 8.
- If the website does not match the client brief, brief_alignment must be below 8.
- If it looks like a cheap template, visual_quality must be below 8.
- For Premium and Custom, be stricter.
- Penalize lorem ipsum, placeholders, fake claims, generic AI text, and missing contact section.

Return ONLY valid JSON.

JSON format:
{{
  "overall_score": 0,
  "approved": false,
  "category_scores": {{
    "offer_clarity": 0,
    "hero_quality": 0,
    "cta_strength": 0,
    "trust_elements": 0,
    "copy_quality": 0,
    "visual_quality": 0,
    "mobile_readiness": 0,
    "package_fit": 0,
    "brief_alignment": 0,
    "ireland_eu_market_fit": 0
  }},
  "critical_errors": [],
  "warnings": [],
  "main_problems": [],
  "must_fix": [],
  "nice_to_have": [],
  "regeneration_prompt": "",
  "next_action": "READY_TO_SEND",
  "manual_review_required": false
}}
""".strip()

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content or "{}"
            result = _safe_json_loads(raw)

        except Exception as exc:
            result = _fallback_review(site_content, brief, package, target_score)
            result.setdefault("warnings", []).append(f"AI review failed, fallback used: {str(exc)[:300]}")

        category_scores = result.get("category_scores") or {}

        for key in CATEGORY_KEYS:
            category_scores[key] = float(category_scores.get(key) or 0)

        result["category_scores"] = category_scores

        if not result.get("overall_score"):
            result["overall_score"] = round(
                sum(category_scores.values()) / len(CATEGORY_KEYS),
                2,
            )
        else:
            result["overall_score"] = round(float(result.get("overall_score") or 0), 2)

        if not result.get("regeneration_prompt"):
            result["regeneration_prompt"] = (
                "Regenerate this website with stronger offer clarity, a better hero section, "
                "clear CTA, trust elements, improved mobile layout, better package fit, "
                "and more premium visual quality."
            )

        next_action = decide_next_action(result, target_score, package)
        result["next_action"] = next_action
        result["approved"] = next_action == "READY_TO_SEND"

        if next_action == "REGENERATE" and not result.get("critical_errors"):
            result.setdefault("warnings", []).append("Quality gate requires regeneration")

        return result


# Simple helper if you prefer function-style usage
def review_site(
    site_content: str,
    brief: Dict[str, Any],
    package: str,
    target_score: float,
) -> Dict[str, Any]:
    return QualityReviewService().review_site(
        site_content=site_content,
        brief=brief,
        package=package,
        target_score=target_score,
    )