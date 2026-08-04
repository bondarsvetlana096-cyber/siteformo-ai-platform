from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrustedExample:
    example_id: str
    display_name: str
    contact_route: str


TRUSTED_EXAMPLES = {
    "https://dev.siteformo.com": TrustedExample(
        example_id="SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1",
        display_name="SiteFormo Consulting Example",
        contact_route="/contact/",
    )
}


def trusted_example_for_origin(origin: str | None) -> TrustedExample | None:
    return TRUSTED_EXAMPLES.get(origin or "")
