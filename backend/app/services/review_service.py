from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from app.core.config import settings
from app.models.order import Order, OrderStatus

REVISION_ALLOWED_FIELDS = [
    "homepage",
    "typography",
    "motion",
    "sections",
    "images",
    "layout",
    "visual_intensity",
]

REVISION_ALLOWED_LEVELS = {"keep", "small_changes", "major_changes"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def package_revision_rounds(package: str | None) -> int:
    tier = str(package or "").lower().strip()
    if tier == "starter":
        return int(getattr(settings, "starter_revision_rounds", 1) or 1)
    return int(getattr(settings, "default_revision_rounds", 2) or 2)


def normalize_interaction_style(package: str | None, value: str | None) -> str | None:
    tier = str(package or "").lower().strip()
    if tier == "starter" and getattr(settings, "starter_skips_interaction_style", True):
        return None
    style = str(value or "").lower().strip()
    if style in {"smooth", "dynamic", "premium"}:
        return style
    return None


def build_creative_payload(order: Order) -> dict[str, Any]:
    brief = getattr(order, "extended_brief", None) or getattr(order, "brief_answers", None) or {}
    if not isinstance(brief, dict):
        brief = {}

    package = (
        brief.get("package")
        or brief.get("tier")
        or getattr(order, "recommended_tier", None)
        or getattr(order, "tier", None)
    )

    selected_example_id = (
        brief.get("selected_example_id")
        or brief.get("example_id")
        or getattr(order, "selected_example_id", None)
    )
    viewed_examples = (
        brief.get("viewed_examples")
        or brief.get("examples_viewed")
        or getattr(order, "viewed_examples", None)
        or []
    )
    example_tracking = (
        brief.get("example_tracking")
        or brief.get("example_tracking_payload")
        or getattr(order, "example_tracking_payload", None)
        or {}
    )
    design_direction = (
        brief.get("design_direction")
        or brief.get("selected_design_direction")
        or getattr(order, "design_direction", None)
        or getattr(order, "selected_design_label", None)
    )
    interaction_style = normalize_interaction_style(
        package,
        brief.get("interaction_style") or getattr(order, "interaction_style", None),
    )

    return {
        "order_id": str(getattr(order, "id", "")),
        "package": package,
        "business_type": brief.get("business_type") or brief.get("industry") or getattr(order, "site_type", None),
        "entry_source": brief.get("entry_source") or getattr(order, "entry_source", None),
        "selected_example_id": selected_example_id,
        "viewed_examples": viewed_examples,
        "example_tracking": example_tracking,
        "design_direction": design_direction,
        "interaction_style": interaction_style,
        "selected_design_id": getattr(order, "selected_design_id", None),
        "selected_design_label": getattr(order, "selected_design_label", None),
        "selected_design_url": getattr(order, "selected_design_url", None),
        "features": brief.get("features") or brief.get("selected_features") or [],
        "pages": brief.get("pages") or brief.get("requested_pages") or getattr(order, "pages_requested", None),
        "positioning": "guided creative system, not a website generator or template marketplace",
        "delivery_rule": "protected preview first, final ZIP only after revisions and final approval",
    }


def apply_creative_payload(order: Order) -> dict[str, Any]:
    payload = build_creative_payload(order)
    for field, value in {
        "selected_example_id": payload.get("selected_example_id"),
        "viewed_examples": payload.get("viewed_examples"),
        "example_tracking_payload": payload.get("example_tracking"),
        "entry_source": payload.get("entry_source"),
        "design_direction": payload.get("design_direction"),
        "interaction_style": payload.get("interaction_style"),
        "production_payload": payload,
    }.items():
        if hasattr(order, field):
            setattr(order, field, value)
    if hasattr(order, "revision_rounds_allowed"):
        order.revision_rounds_allowed = package_revision_rounds(payload.get("package"))
    return payload


class ReviewService:
    @staticmethod
    def generate_token(order_id: str, email: str | None = None) -> str:
        message = f"review:{order_id}:{email or ''}"
        return hmac.new(
            settings.review_signing_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def verify(order: Order, token: str, email: str | None = None) -> bool:
        if not token:
            return False
        expected = ReviewService.generate_token(str(order.id), email)
        if hmac.compare_digest(expected, token):
            return True
        stored_hash = getattr(order, "review_token_hash", None)
        return bool(stored_hash and hmac.compare_digest(stored_hash, ReviewService.token_hash(token)))

    @staticmethod
    def build_review_url(order_id: str, email: str | None = None) -> str:
        token = ReviewService.generate_token(order_id, email)
        base = (settings.review_base_url or settings.public_base_url or "").rstrip("/")
        query = urlencode({"token": token, **({"email": email} if email else {})})
        return f"{base}/{order_id}?{query}"

    @staticmethod
    def validate_revision_payload(payload: dict[str, Any]) -> dict[str, Any]:
        structured = payload.get("structured") or payload.get("changes") or {}
        if not isinstance(structured, dict):
            structured = {}
        cleaned: dict[str, Any] = {}
        for key in REVISION_ALLOWED_FIELDS:
            item = structured.get(key, {})
            if isinstance(item, str):
                item = {"level": item, "notes": ""}
            if not isinstance(item, dict):
                item = {}
            level = str(item.get("level") or "keep").lower().strip()
            if level not in REVISION_ALLOWED_LEVELS:
                level = "small_changes"
            cleaned[key] = {
                "level": level,
                "notes": str(item.get("notes") or "").strip()[:2000],
            }
        return {
            "submitted_at": now_utc().isoformat(),
            "structured": cleaned,
            "general_notes": str(payload.get("general_notes") or payload.get("notes") or "").strip()[:3000],
        }
