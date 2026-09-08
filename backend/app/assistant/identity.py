from __future__ import annotations

import hashlib
import secrets


ASSISTANT_COOKIE_NAME = "sf_assistant_visitor"
ASSISTANT_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def issue_possession_credential() -> str:
    return secrets.token_urlsafe(32)


def hash_possession_credential(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()
