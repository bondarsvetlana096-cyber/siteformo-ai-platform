import hashlib
import hmac
from urllib.parse import urlencode

from app.core.config import settings


class DeliveryService:
    @staticmethod
    def generate_token(order_id: str, email: str) -> str:
        message = f"{order_id}:{email}"
        return hmac.new(
            settings.approval_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def verify(order_id: str, email: str, token: str) -> bool:
        expected_token = DeliveryService.generate_token(order_id, email)
        return hmac.compare_digest(expected_token, token)

    @staticmethod
    def build_delivery_url(order_id: str, email: str) -> str:
        token = DeliveryService.generate_token(order_id, email)
        query = urlencode({"email": email, "token": token})
        return f"{str(settings.public_base_url).rstrip('/')}/api/admin/delivery/{order_id}?{query}"
