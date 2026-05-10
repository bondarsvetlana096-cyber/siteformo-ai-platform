from __future__ import annotations

import os
from copy import deepcopy
from typing import Any, Dict

# One source of truth for SiteFormo package rules.
# IMPORTANT: These limits protect API cost. Do not raise max_quality_iterations
# without intentionally changing the commercial model.
PACKAGE_RULES: Dict[str, Dict[str, Any]] = {
    "starter": {
        "canonical_name": "starter",
        "public_name": "Starter",
        "page_limit": 1,
        "target_score": 7.5,
        "max_quality_iterations": 1,
        "max_total_generation_rounds": 2,  # initial generation + 1 improvement
        "max_warnings": 5,
        "critical_minimum_score": 6.5,
        "allow_premium_motion": False,
        "allow_system_logic": False,
    },
    "business": {
        "canonical_name": "business",
        "public_name": "Business",
        "page_limit": 3,
        "target_score": 8.0,
        "max_quality_iterations": 2,
        "max_total_generation_rounds": 3,
        "max_warnings": 3,
        "critical_minimum_score": 7.0,
        "allow_premium_motion": True,
        "allow_system_logic": False,
    },
    "reference": {
        "canonical_name": "reference",
        "public_name": "Reference",
        "page_limit": 5,
        "target_score": 8.5,
        "max_quality_iterations": 3,
        "max_total_generation_rounds": 4,
        "max_warnings": 2,
        "critical_minimum_score": 7.5,
        "allow_premium_motion": True,
        "allow_system_logic": False,
    },
    "advanced": {
        "canonical_name": "advanced",
        "public_name": "Advanced",
        "page_limit": 5,
        "target_score": 8.8,
        "max_quality_iterations": 3,
        "max_total_generation_rounds": 4,
        "max_warnings": 1,
        "critical_minimum_score": 8.0,
        "allow_premium_motion": True,
        "allow_system_logic": True,
    },
}

PACKAGE_ALIASES = {
    "standard": "starter",
    "basic": "starter",
    "simple": "starter",
    "pro": "business",
    "professional": "business",
    "premium": "reference",
    "reference": "reference",
    "custom": "advanced",
    "enterprise": "advanced",
    "advanced": "advanced",
}


def normalize_package(value: Any) -> str:
    package = str(value or "starter").strip().lower()
    package = PACKAGE_ALIASES.get(package, package)
    if package not in PACKAGE_RULES:
        return "starter"
    return package


def get_package_rules(value: Any) -> Dict[str, Any]:
    package = normalize_package(value)
    rules = deepcopy(PACKAGE_RULES[package])
    rules["package"] = package
    return rules


def hard_iteration_cap() -> int:
    """Global safety cap so review/regeneration never becomes an API bonfire."""
    raw = os.getenv("SITEFORMO_MAX_REGENERATION_ROUNDS", "3")
    try:
        value = int(raw)
    except Exception:
        value = 3
    return max(0, min(value, 3))


def get_generation_budget(package: Any) -> Dict[str, Any]:
    rules = get_package_rules(package)
    allowed_improvements = min(int(rules.get("max_quality_iterations") or 1), hard_iteration_cap())
    return {
        "package": rules["package"],
        "target_score": float(rules.get("target_score") or 7.5),
        "max_quality_iterations": allowed_improvements,
        "max_total_generation_rounds": allowed_improvements + 1,
        "max_warnings": int(rules.get("max_warnings") or 5),
        "hard_cap_reason": "Cost protection: initial generation plus a maximum of 3 improvement rounds.",
    }
