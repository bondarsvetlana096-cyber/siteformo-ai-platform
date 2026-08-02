from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from pathlib import Path

SUBJECT = "Your SiteFormo demonstration enquiry"
SENDER = "SiteFormo <siteformo@siteformo.com>"
REPLY_TO = "siteformo@siteformo.com"
TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"
TOKEN = re.compile(r"{{([a-z_]+)}}")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class TemplateValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Enquiry:
    first_name: str
    last_name: str
    preferred_method: str
    contact_value: str
    message: str


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    sender: str
    reply_to: str
    html: str
    text: str


def _single_line(name: str, value: str, limit: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise TemplateValidationError(f"{name} is required")
    if "\r" in value or "\n" in value or CONTROL.search(value):
        raise TemplateValidationError(f"{name} must be a safe single-line value")
    if len(cleaned) > limit:
        raise TemplateValidationError(f"{name} exceeds maximum length")
    return cleaned


def _message(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise TemplateValidationError("message is required")
    if CONTROL.search(normalized):
        raise TemplateValidationError("message contains unsafe control characters")
    if len(normalized) > 5_000:
        raise TemplateValidationError("message exceeds maximum length")
    return normalized


def _substitute(template: str, values: dict[str, str], *, html: bool) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise TemplateValidationError(f"unknown or missing variable: {key}")
        value = values[key]
        return escape(value, quote=True) if html else value

    rendered = TOKEN.sub(replace, template)
    if TOKEN.search(rendered):
        raise TemplateValidationError("unresolved template variable")
    return rendered


def render(enquiry: Enquiry) -> RenderedEmail:
    values = {
        "first_name": _single_line("first_name", enquiry.first_name, 100),
        "last_name": _single_line("last_name", enquiry.last_name, 100),
        "preferred_method": _single_line("preferred_method", enquiry.preferred_method, 20),
        "contact_value": _single_line("contact_value", enquiry.contact_value, 320),
        "message": _message(enquiry.message),
    }
    if values["preferred_method"] != "Email":
        raise TemplateValidationError("preferred_method must be Email")

    html_template = (TEMPLATE_ROOT / "template.html").read_text(encoding="utf-8")
    text_template = (TEMPLATE_ROOT / "template.txt").read_text(encoding="utf-8")
    return RenderedEmail(
        subject=SUBJECT,
        sender=SENDER,
        reply_to=REPLY_TO,
        html=_substitute(html_template, values, html=True),
        text=_substitute(text_template, values, html=False),
    )

