from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping


def validate_twilio_signature(url: str, params: Mapping[str, str], signature: str | None, auth_token: str) -> bool:
    if not signature or not auth_token or not url.startswith("https://"):
        return False
    material = url + "".join(key + params[key] for key in sorted(params))
    expected = base64.b64encode(hmac.new(auth_token.encode(), material.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, signature)
