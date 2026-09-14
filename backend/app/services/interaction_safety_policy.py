from __future__ import annotations

from copy import deepcopy


INTERACTION_SAFETY_CONTRACT_VERSION = "v1"

_POLICY = {
    "contract_version": INTERACTION_SAFETY_CONTRACT_VERSION,
    "enhancement_purposes": [
        "storytelling", "exploration", "comparison", "spatial_understanding",
        "editorial_rhythm", "visual_identity",
    ],
    "protected_critical_actions": [
        "navigation", "forms", "booking", "checkout_payment",
        "account_security", "primary_conversion_actions",
    ],
    "keyboard_operability_required": True,
    "touch_safe_mobile_behavior_required": True,
    "reduced_motion_alternative_required": True,
    "critical_action_stability_required": True,
    "hover_only_required_actions_forbidden": True,
    "omit_harmful_interaction_required": True,
}


def interaction_safety_policy_v1() -> dict:
    """Return immutable-by-copy structured planner policy, never prompt text."""
    return deepcopy(_POLICY)

