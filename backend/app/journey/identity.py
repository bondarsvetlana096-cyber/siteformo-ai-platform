from __future__ import annotations

import hashlib
import secrets


JOURNEY_CREDENTIAL_HEADER = "X-SiteFormo-Visitor"


def issue_journey_credential() -> str:
    """Return 256 bits of opaque possession entropy in URL-safe form."""
    return secrets.token_urlsafe(32)


def hash_journey_credential(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()
