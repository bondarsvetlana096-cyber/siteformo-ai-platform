from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlencode

from app.core.config import settings


class ApprovalService:
    @staticmethod
    def sign(order_id: str, action: str) -> str:
        raw = f'{order_id}:{action}'.encode()
        return hmac.new(settings.approval_secret.encode(), raw, hashlib.sha256).hexdigest()

    @staticmethod
    def verify(order_id: str, action: str, token: str) -> bool:
        expected = ApprovalService.sign(order_id, action)
        return hmac.compare_digest(expected, token)

    @staticmethod
    def build_action_url(order_id: str, action: str) -> str:
        token = ApprovalService.sign(order_id, action)
        query = urlencode({'action': action, 'token': token})
        return f'{settings.public_base_url}/api/orders/{order_id}/decision?{query}'
