from __future__ import annotations

from app.journey.identity import hash_journey_credential, issue_journey_credential


ASSISTANT_COOKIE_NAME = "sf_assistant_visitor"
ASSISTANT_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def issue_possession_credential() -> str:
    """Deprecated compatibility alias; Journey identity owns issuance."""
    return issue_journey_credential()


def hash_possession_credential(credential: str) -> str:
    """Deprecated compatibility alias; Journey identity owns hashing."""
    return hash_journey_credential(credential)
