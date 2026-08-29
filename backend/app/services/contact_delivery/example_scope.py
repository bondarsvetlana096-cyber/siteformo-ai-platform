from __future__ import annotations

from dataclasses import dataclass

BUSINESS_1_EXAMPLE_ID = "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1"
BUSINESS_2_EXAMPLE_ID = "SF_BU_02_VOLTINK_EXAMPLE_V1"
BUSINESS_3_EXAMPLE_ID = "SF_BU_03_NORTHFORM_EXAMPLE_V1"
BUSINESS_4_EXAMPLE_ID = "SF_BU_04_NEXORA_EXAMPLE_V1"

CANONICAL_EXAMPLE_IDS = frozenset(
    {
        BUSINESS_1_EXAMPLE_ID,
        BUSINESS_2_EXAMPLE_ID,
        BUSINESS_3_EXAMPLE_ID,
        BUSINESS_4_EXAMPLE_ID,
    }
)

EXAMPLES_BY_ORIGIN = {
    "https://dev.siteformo.com": CANONICAL_EXAMPLE_IDS,
    "https://business1.siteformo.com": frozenset({BUSINESS_1_EXAMPLE_ID}),
    "https://business2.siteformo.com": frozenset({BUSINESS_2_EXAMPLE_ID}),
    "https://business3.siteformo.com": frozenset({BUSINESS_3_EXAMPLE_ID}),
}

# Explicit, bounded compatibility for the original Business 01 caller only.
# Shared dev origin requests for Business 02/03 must send example_id.
LEGACY_DEFAULT_BY_ORIGIN = {
    "https://dev.siteformo.com": BUSINESS_1_EXAMPLE_ID,
    "https://business1.siteformo.com": BUSINESS_1_EXAMPLE_ID,
    "https://business2.siteformo.com": BUSINESS_2_EXAMPLE_ID,
    "https://business3.siteformo.com": BUSINESS_3_EXAMPLE_ID,
}


@dataclass(frozen=True, slots=True)
class TrustedExampleScope:
    example_id: str
    origin: str
    legacy_fallback: bool = False


class ExampleScopeError(ValueError):
    pass


def resolve_trusted_example(
    origin: str | None, requested_example_id: str | None
) -> TrustedExampleScope:
    exact_origin = origin or ""
    allowed = EXAMPLES_BY_ORIGIN.get(exact_origin)
    if allowed is None:
        raise ExampleScopeError("origin_not_allowed")

    requested = (requested_example_id or "").strip()
    if not requested:
        fallback = LEGACY_DEFAULT_BY_ORIGIN.get(exact_origin)
        if fallback is None:
            raise ExampleScopeError("example_scope_required")
        return TrustedExampleScope(fallback, exact_origin, legacy_fallback=True)

    if requested not in allowed:
        raise ExampleScopeError("example_scope_not_allowed")
    return TrustedExampleScope(requested, exact_origin)
