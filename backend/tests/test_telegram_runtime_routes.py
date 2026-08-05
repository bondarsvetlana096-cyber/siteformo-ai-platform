from __future__ import annotations

import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import demo_telegram as api
from app.services.telegram_delivery.binding import DeepLinkService, TrustedExample
from app.services.telegram_delivery.models import BindingState
from app.services.telegram_delivery.runtime import RedisBindingQuota, RuntimeBindingService, UnifiedTelegramIngress
from app.services.telegram_delivery.service import VisitorBindingWebhookService
from app.services.telegram_delivery.transport import TransportState
from tests.test_telegram_visitor_binding import FakeTransport, MemoryAudit, MemoryStore, update

ORIGIN = "https://dev.siteformo.com"
EXAMPLE = "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1"
SECRET = "offline-runtime-secret"


class MemoryQuota:
    def __init__(self) -> None:
        self.claims: set[tuple[str, str, str]] = set()
        self.calls: list[tuple[str, str, str]] = []

    async def claim(self, *, trusted_example_id: str, idempotency_key: str, client_id: str) -> bool:
        value = (trusted_example_id, idempotency_key, client_id)
        self.calls.append(value)
        if value in self.claims:
            return False
        self.claims.add(value)
        return True


def payload(key: str = "telegram-runtime-000001") -> dict[str, str]:
    return {
        "name": "Alex", "message": "I'd like to book a meeting",
        "idempotency_key": key,
    }


class RuntimeRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.transport = FakeTransport()
        self.quota = MemoryQuota()
        links = DeepLinkService(
            store=self.store, bot_username="SiteFormoBot", ttl_seconds=300,
            trusted_origins={ORIGIN: TrustedExample(EXAMPLE, ORIGIN)}, clock=time.time,
        )
        api._binding_runtime = RuntimeBindingService(links, self.quota)
        binding_handler = VisitorBindingWebhookService(
            store=self.store, audit=MemoryAudit(), transport=self.transport, webhook_secret=SECRET
        )
        api._unified_ingress = UnifiedTelegramIngress(
            binding_handler=binding_handler, webhook_secret=SECRET,
        )
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        api._binding_runtime = None
        api._unified_ingress = None

    def create(self, key: str = "telegram-runtime-000001"):
        return self.client.post(
            "/api/demo/telegram/start", headers={"Origin": ORIGIN}, json=payload(key)
        )

    def test_route_creation_success_no_store_and_private_response(self) -> None:
        response = self.create()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(set(body), {"url", "expires_at", "binding_id"})
        self.assertTrue(body["url"].startswith("https://t.me/SiteFormoBot?start="))
        self.assertNotIn("chat_id", body)
        self.assertNotIn("token", body)

    def test_exact_origin_rejected_before_quota(self) -> None:
        response = self.client.post(
            "/api/demo/telegram/start",
            headers={"Origin": ORIGIN + ".evil.test"},
            json=payload(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.quota.calls, [])

    def test_quota_isolation_by_session_key(self) -> None:
        first = self.create("telegram-session-a-0001")
        second = self.create("telegram-session-b-0001")
        repeat = self.create("telegram-session-a-0001")
        self.assertEqual((first.status_code, second.status_code, repeat.status_code), (201, 201, 429))
        self.assertTrue(all(call[0] == EXAMPLE for call in self.quota.calls))

    def test_webhook_bad_secret(self) -> None:
        response = self.client.post(
            "/api/channels/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"update_id": 1},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.transport.calls, [])

    def test_private_start_duplicate_and_one_delivery(self) -> None:
        token = self.create().json()["url"].split("start=", 1)[1]
        telegram_update = update(token, update_id=100)
        first = self.client.post(
            "/api/channels/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}, json=telegram_update,
        )
        duplicate = self.client.post(
            "/api/channels/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}, json=telegram_update,
        )
        self.assertEqual((first.status_code, duplicate.status_code), (200, 200))
        self.assertEqual(first.json(), {"ok": True})
        self.assertEqual(len(self.transport.calls), 1)
        self.assertIn('Your message:\n\n"I\'d like to book a meeting"', self.transport.calls[0].text)

    def test_non_binding_message_fails_closed_without_provider_call(self) -> None:
        telegram_update = {
            "update_id": 200,
            "message": {"chat": {"id": 123, "type": "private"}, "text": "Hello SiteFormo"},
        }
        response = self.client.post(
            "/api/channels/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}, json=telegram_update,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.transport.calls, [])

        duplicate = self.client.post(
            "/api/channels/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}, json=telegram_update,
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(self.transport.calls, [])

    def test_provider_timeout_is_quarantined_and_hidden(self) -> None:
        self.transport.state = TransportState.AMBIGUOUS
        token = self.create().json()["url"].split("start=", 1)[1]
        response = self.client.post(
            "/api/channels/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}, json=update(token),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        record = next(iter(self.store.records.values()))
        self.assertEqual(record["status"], BindingState.QUARANTINED)
        self.assertEqual(len(self.transport.calls), 1)


class ProductionWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_canonical_telegram_webhook_is_mounted(self) -> None:
        from app.main import app as production_app

        paths = {route.path for route in production_app.routes}
        self.assertIn("/api/channels/telegram/webhook", paths)
        self.assertNotIn("/telegram/webhook", paths)
        self.assertNotIn("/channels/telegram/webhook", paths)

    async def test_redis_quota_uses_hashed_channel_isolated_keys(self) -> None:
        quota = RedisBindingQuota("redis://runtime.invalid")
        connection = AsyncMock()
        connection.eval.return_value = ["CLAIMED"]
        connection.aclose = AsyncMock()
        with patch(
            "app.services.telegram_delivery.runtime.redis.Redis.from_url",
            return_value=connection,
        ):
            allowed = await quota.claim(
                trusted_example_id=EXAMPLE,
                idempotency_key="visitor-session-0001",
                client_id="203.0.113.20",
            )
        self.assertTrue(allowed)
        arguments = connection.eval.await_args.args
        serialized = " ".join(map(str, arguments))
        self.assertIn("sf:demo-telegram:v1:visitor-binding-quota", serialized)
        self.assertNotIn("203.0.113.20", serialized)
        self.assertNotIn("visitor-session-0001", serialized)


if __name__ == "__main__":
    unittest.main()
