from __future__ import annotations

from typing import Any, Dict


PACKAGE_QUALITY_RULES: Dict[str, Dict[str, Any]] = {
    "starter": {
        "variants": 1,
        "target_score": 7.5,
        "max_quality_iterations": 1,
        "max_warnings": 5,
        "critical_minimum_score": 6.5,
    },
    "business": {
        "variants": 2,
        "target_score": 8.0,
        "max_quality_iterations": 2,
        "max_warnings": 3,
        "critical_minimum_score": 7.0,
    },
    "premium": {
        "variants": 5,
        "target_score": 8.5,
        "max_quality_iterations": 3,
        "max_warnings": 2,
        "critical_minimum_score": 7.5,
    },
    "custom": {
        "variants": 5,
        "target_score": 9.0,
        "max_quality_iterations": 5,
        "max_warnings": 1,
        "critical_minimum_score": 8.0,
    },
}


def normalize_package(value: str | None) -> str:
    package = (value or "starter").strip().lower()
    if package in {"standard", "basic"}:
        return "starter"
    if package in {"pro", "professional"}:
        return "business"
    if package not in PACKAGE_QUALITY_RULES:
        return "starter"
    return package


def get_package_rules(value: str | None) -> Dict[str, Any]:
    package = normalize_package(value)
    rules = dict(PACKAGE_QUALITY_RULES[package])
    rules["package"] = package
    return rules
