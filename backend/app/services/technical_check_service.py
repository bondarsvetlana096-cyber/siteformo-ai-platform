from __future__ import annotations

import re
from typing import Any, Dict, List


BLOCKING_PATTERNS = [
    r"lorem\s+ipsum",
    r"your\s+company",
    r"company\s+name",
    r"insert\s+.*here",
    r"placeholder",
    r"dummy\s+text",
    r"example\s+business",
    r"coming\s+soon",
]


def _text_from_preview(preview: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ["label", "style", "color_direction", "prompt", "summary", "html", "html_code", "content"]:
        value = preview.get(key)
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def technical_check_preview(preview: Dict[str, Any], brief: Dict[str, Any] | None = None) -> Dict[str, Any]:
    brief = brief or {}
    text = _text_from_preview(preview).lower()
    errors: List[str] = []
    warnings: List[str] = []

    for pattern in BLOCKING_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            errors.append(f"Blocking placeholder text detected: {pattern}")

    if not preview.get("id"):
        errors.append("Preview is missing id")

    if not (preview.get("preview_url") or preview.get("screenshot_url") or preview.get("image_url")):
        warnings.append("Preview has no visual URL yet")

    prompt = str(preview.get("prompt") or "")
    if len(prompt.strip()) < 80:
        warnings.append("Generation prompt is too short to reliably create a strong website")

    business_name = (
        brief.get("business_name")
        or (brief.get("contact") or {}).get("business_name")
        or (brief.get("answers") or {}).get("business_name")
    )
    if business_name and str(business_name).lower() not in text:
        warnings.append("Business name from brief is not clearly reflected in preview metadata")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }
