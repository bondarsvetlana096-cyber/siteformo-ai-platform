from __future__ import annotations

import asyncio
import io
import socket
import unittest
from dataclasses import dataclass
from unittest.mock import patch

import httpx

from app.services.telegram_delivery.binding import DeepLinkService, TrustedExample
from app.services.telegram_delivery.configuration import (
    Readiness,
    resolve_configuration,
    webhook_ready,
)
from app.services.telegram_delivery.models import (
    BINDING_NAMESPACE,
    DELIVERY_NAMESPACE,
    OWNER_NAMESPACE,
    ROOT_NAMESPACE,
    UPDATE_NAMESPACE,
    BindingState,
    ConsumeResult,
    TelegramMessage,
    render_demo_message,
)
from app.services.telegram_delivery.security import parse_start_update, private_id_hash, token_hash
from app.services.telegram_delivery.service import VisitorBindingWebhookService
from app.services.telegram_delivery.transport import (
    BotApiTelegramTransport,
    TelegramTransportConfig,
    TransportResult,
    TransportState,
)

ORIGIN = "https://dev.siteformo.com"
EXAMPLE = "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1"
SECRET = "offline-webhook-secret"


class MemoryStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.updates: set[str] = set()
        self.lock = asyncio.Lock()
        self.fail_consume = False

    async def create(self, **values: object) -> None:
        digest = str(values["token_digest"])
        if digest in self.records:
            raise RuntimeError("collision")
        self.records[digest] = dict(values, status=BindingState.CREATED, chat_digest=None)

    async def consume(self, **values: object) -> ConsumeResult:
        if self.fail_consume:
            raise RuntimeError("offline persistence failure")
        async with self.lock:
            update = str(values["update_digest"])
            if update in self.updates:
                return ConsumeResult(BindingState.REPLAY_BLOCKED)
            self.updates.add(update)
            record = self.records.get(str(values["token_digest"]))
            if not record or int(record["expires_at"]) <= int(values["now_seconds"]):
                return ConsumeResult(BindingState.EXPIRED)
            if record["status"] is not BindingState.CREATED:
                return ConsumeResult(BindingState.REPLAY_BLOCKED)
            record["status"] = BindingState.CONSUMING
            record["chat_digest"] = values["chat_digest"]
            record["status"] = BindingState.CONSUMED
            return ConsumeResult(
                BindingState.CONSUMED, str(record["binding_id"]),
                str(record["validated_name"]) or None,
            )

    async def finalize(self, **values: object) -> None:
        record = self.records[str(values["token_digest"])]
        if record["status"] is not BindingState.CONSUMED:
            raise RuntimeError("invalid state")
        record["status"] = values["state"]
        record["provider_reference_hash"] = values["provider_reference_hash"]


class FakeTransport:
    def __init__(self, state: TransportState = TransportState.ACCEPTED) -> None:
        self.state = state
        self.calls: list[TelegramMessage] = []

    async def send(self, message: TelegramMessage) -> TransportResult:
        self.calls.append(message)
        return TransportResult(
            self.state, "offline-message-1" if self.state is TransportState.ACCEPTED else None,
            http_status=200, provider_ok=self.state is TransportState.ACCEPTED,
            message_id_present=self.state is TransportState.ACCEPTED,
        )


class MemoryAudit:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.fail_create = self.fail_mark = self.fail_finalize = False

    async def create(self, **values: object) -> None:
        if self.fail_create:
            raise RuntimeError("audit unavailable")
        self.records[str(values["binding_id"])] = dict(
            values, transport_invoked=False, provider_call_count=0,
        )

    async def mark_transport_invoked(self, *, binding_id: str, now_seconds: int) -> None:
        if self.fail_mark:
            raise RuntimeError("audit unavailable")
        record = self.records[binding_id]
        record.update(transport_invoked=True, provider_call_count=int(record["provider_call_count"]) + 1)

    async def finalize(self, **values: object) -> None:
        if self.fail_finalize:
            raise RuntimeError("audit unavailable")
        self.records[str(values["binding_id"])].update(values)


def webhook_service(store: MemoryStore, transport: FakeTransport, audit: MemoryAudit | None = None):
    return VisitorBindingWebhookService(
        store=store, audit=audit or MemoryAudit(), transport=transport, webhook_secret=SECRET,
    )


def update(token: str, *, update_id: int = 1, chat_id: int = 123456, chat_type: str = "private") -> dict:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id, "type": chat_type}, "text": f"/start {token}"},
    }


async def create_session(store: MemoryStore, *, now: int = 1000, name: str | None = "Alex"):
    links = DeepLinkService(
        store=store, bot_username="SiteFormoBot", ttl_seconds=300,
        trusted_origins={ORIGIN: TrustedExample(EXAMPLE, ORIGIN)}, clock=lambda: now,
    )
    result = await links.create(origin=ORIGIN, validated_name=name)
    token = result.url.split("start=", 1)[1]
    return result, token


class BindingTests(unittest.TestCase):
    def test_valid_private_start_and_exact_message(self) -> None:
        store, transport = MemoryStore(), FakeTransport()
        result, token = asyncio.run(create_session(store))
        service = webhook_service(store, transport)
        handled = asyncio.run(service.handle(update(token), SECRET, 1001))
        self.assertEqual(handled.state, BindingState.DELIVERED)
        self.assertEqual(handled.binding_id, result.binding_id)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            transport.calls[0].text,
            "Welcome to SiteFormo Bot. You can learn how SiteFormo works, ask about website examples, explore packages, pricing and timelines, and understand the first steps of starting a website project.",
        )
        self.assertNotIn("parse_mode", repr(transport.calls[0]))

    def test_neutral_message_without_name(self) -> None:
        self.assertEqual(
            render_demo_message(None),
            "Welcome to SiteFormo Bot. You can learn how SiteFormo works, ask about website examples, explore packages, pricing and timelines, and understand the first steps of starting a website project.",
        )

    def test_plain_start_malformed_oversized_and_group_are_blocked(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_start_payload"):
            parse_start_update({"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "text": "/start"}})
        for text in ["/start short", "/start " + "a" * 65, "/start " + "a" * 40 + " extra"]:
            with self.assertRaises(ValueError):
                parse_start_update({"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "text": text}})
        with self.assertRaisesRegex(ValueError, "private_chat_required"):
            parse_start_update(update("a" * 40, chat_type="supergroup"))
        with self.assertRaisesRegex(ValueError, "invalid_update"):
            parse_start_update({"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "text": "/start " + "a" * 40, "padding": "x" * 20_000}})

    def test_expired_replay_duplicate_update_and_other_chat(self) -> None:
        store, transport = MemoryStore(), FakeTransport()
        _, token = asyncio.run(create_session(store))
        service = webhook_service(store, transport)
        expired = asyncio.run(service.handle(update(token), SECRET, 1300))
        self.assertEqual(expired.state, BindingState.EXPIRED)
        store2, transport2 = MemoryStore(), FakeTransport()
        _, token2 = asyncio.run(create_session(store2))
        first = asyncio.run(webhook_service(store2, transport2).handle(update(token2), SECRET, 1001))
        duplicate = asyncio.run(webhook_service(store2, transport2).handle(update(token2), SECRET, 1001))
        other_chat = asyncio.run(webhook_service(store2, transport2).handle(update(token2, update_id=2, chat_id=999999), SECRET, 1001))
        self.assertEqual(first.state, BindingState.DELIVERED)
        self.assertEqual(duplicate.state, BindingState.REPLAY_BLOCKED)
        self.assertEqual(other_chat.state, BindingState.REPLAY_BLOCKED)
        self.assertEqual(len(transport2.calls), 1)

    def test_concurrent_consume_allows_one_initial_delivery(self) -> None:
        store, transport = MemoryStore(), FakeTransport()
        _, token = asyncio.run(create_session(store))
        service = webhook_service(store, transport)

        async def exercise():
            return await asyncio.gather(*(
                service.handle(update(token, update_id=index + 1), SECRET, 1001) for index in range(10)
            ))

        results = asyncio.run(exercise())
        self.assertEqual(sum(result.state is BindingState.DELIVERED for result in results), 1)
        self.assertEqual(len(transport.calls), 1)

    def test_invalid_secret_and_persistence_failure_block_outbound(self) -> None:
        store, transport = MemoryStore(), FakeTransport()
        _, token = asyncio.run(create_session(store))
        service = webhook_service(store, transport)
        self.assertEqual(asyncio.run(service.handle(update(token), "wrong", 1001)).state, BindingState.INVALID_UPDATE)
        self.assertEqual(transport.calls, [])
        store.fail_consume = True
        with self.assertRaisesRegex(RuntimeError, "telegram_binding_persistence_unavailable"):
            asyncio.run(service.handle(update(token, update_id=2), SECRET, 1001))
        self.assertEqual(transport.calls, [])

    def test_origin_mismatch_username_validation_and_hash_only_storage(self) -> None:
        store = MemoryStore()
        links = DeepLinkService(
            store=store, bot_username="SiteFormoBot", ttl_seconds=300,
            trusted_origins={ORIGIN: TrustedExample(EXAMPLE, ORIGIN)}, clock=lambda: 1000,
        )
        with self.assertRaisesRegex(PermissionError, "ORIGIN_MISMATCH"):
            asyncio.run(links.create(origin="https://dev.siteformo.com.evil.test", validated_name="Alex"))
        with self.assertRaisesRegex(ValueError, "invalid_telegram_bot_username"):
            DeepLinkService(store=store, bot_username="bad-name", ttl_seconds=300, trusted_origins={})
        result, token = asyncio.run(create_session(store))
        serialized = repr(store.records)
        self.assertNotIn(token, serialized)
        self.assertIn(token_hash(token), store.records)
        self.assertNotIn(ORIGIN, serialized)
        self.assertNotIn(EXAMPLE, serialized)
        self.assertNotIn(token, repr(result.binding_id))

    def test_chat_id_and_raw_token_are_absent_from_output(self) -> None:
        store, transport, output = MemoryStore(), FakeTransport(), io.StringIO()
        _, token = asyncio.run(create_session(store))
        service = webhook_service(store, transport)
        with patch("sys.stdout", output), patch("sys.stderr", output):
            asyncio.run(service.handle(update(token, chat_id=987654321), SECRET, 1001))
        self.assertNotIn(token, output.getvalue())
        self.assertNotIn("987654321", output.getvalue())

    def test_ambiguous_is_quarantined_without_retry(self) -> None:
        store, transport = MemoryStore(), FakeTransport(TransportState.AMBIGUOUS)
        _, token = asyncio.run(create_session(store))
        service = webhook_service(store, transport)
        result = asyncio.run(service.handle(update(token), SECRET, 1001))
        replay = asyncio.run(service.handle(update(token, update_id=2), SECRET, 1001))
        self.assertEqual(result.state, BindingState.QUARANTINED)
        self.assertEqual(replay.state, BindingState.REPLAY_BLOCKED)
        self.assertEqual(len(transport.calls), 1)

    def test_configuration_and_namespace_isolation(self) -> None:
        environment = {
            "TELEGRAM_BOT_TOKEN": "offline-token",
            "TELEGRAM_BOT_USERNAME": "SiteFormoBot",
            "TELEGRAM_WEBHOOK_SECRET_TOKEN": SECRET,
            "TELEGRAM_VISITOR_BINDING_NAMESPACE": BINDING_NAMESPACE,
            "TELEGRAM_VISITOR_BINDING_TTL_SECONDS": "300",
            "ENABLE_TELEGRAM_CHANNEL": "false",
        }
        disabled = resolve_configuration(environment)
        self.assertEqual(disabled.readiness, Readiness.DISABLED)
        self.assertFalse(webhook_ready(disabled, redis_available=True))
        environment["ENABLE_TELEGRAM_CHANNEL"] = "true"
        ready = resolve_configuration(environment)
        self.assertTrue(webhook_ready(ready, redis_available=True))
        environment.pop("TELEGRAM_WEBHOOK_SECRET_TOKEN")
        missing = resolve_configuration(environment)
        self.assertEqual(missing.readiness, Readiness.INCOMPLETE)
        self.assertFalse(webhook_ready(missing, redis_available=True))
        self.assertEqual(ROOT_NAMESPACE, "sf:demo-telegram:v1")
        self.assertEqual(len({BINDING_NAMESPACE, DELIVERY_NAMESPACE, OWNER_NAMESPACE, UPDATE_NAMESPACE}), 4)
        self.assertTrue(all("demo-email" not in value and "demo-whatsapp" not in value for value in {BINDING_NAMESPACE, DELIVERY_NAMESPACE, OWNER_NAMESPACE, UPDATE_NAMESPACE}))


class FakeResponse:
    def __init__(self, status_code: int, body: object = None) -> None:
        self.status_code, self.body = status_code, body

    def json(self) -> object:
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeClient:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None) -> None:
        self.response = response or FakeResponse(200, {"ok": True, "result": {"message_id": 42}})
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


class TransportTests(unittest.TestCase):
    def transport(self, client: FakeClient) -> BotApiTelegramTransport:
        return BotApiTelegramTransport(TelegramTransportConfig("offline-token", 10), client)  # type: ignore[arg-type]

    def test_success_and_plain_text_contract(self) -> None:
        client = FakeClient()
        result = asyncio.run(self.transport(client).send(TelegramMessage(123, "Server text", "corr")))
        self.assertEqual(result.state, TransportState.ACCEPTED)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][1]["json"], {"chat_id": 123, "text": "Server text"})
        self.assertNotIn("parse_mode", client.calls[0][1]["json"])

    def test_provider_response_classification(self) -> None:
        cases = [
            ({"ok": True, "result": {"message_id": 42}}, TransportState.ACCEPTED, True),
            ({"ok": True, "result": {}}, TransportState.AMBIGUOUS, False),
            ({"ok": False, "description": "offline"}, TransportState.REJECTED, False),
            ({"unexpected": []}, TransportState.AMBIGUOUS, False),
        ]
        for body, expected, message_id_present in cases:
            result = asyncio.run(self.transport(FakeClient(FakeResponse(200, body))).send(
                TelegramMessage(123, "Server text", "corr")
            ))
            self.assertEqual(result.state, expected)
            self.assertEqual(result.message_id_present, message_id_present)

    def test_timeout_4xx_5xx_and_no_retry(self) -> None:
        request = httpx.Request("POST", "https://api.telegram.org/test")
        cases = [
            (FakeClient(error=httpx.ReadTimeout("timeout", request=request)), TransportState.TIMEOUT),
            (FakeClient(FakeResponse(400, {})), TransportState.REJECTED),
            (FakeClient(FakeResponse(503, {})), TransportState.TRANSIENT_FAILURE),
        ]
        for client, expected in cases:
            result = asyncio.run(self.transport(client).send(TelegramMessage(123, "Server text", "corr")))
            self.assertEqual(result.state, expected)
            self.assertEqual(len(client.calls), 1)

    def test_external_network_is_not_used(self) -> None:
        with patch.object(socket, "create_connection", side_effect=AssertionError("network denied")):
            client = FakeClient()
            asyncio.run(self.transport(client).send(TelegramMessage(123, "Server text", "corr")))
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
