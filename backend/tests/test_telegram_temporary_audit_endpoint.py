from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import demo_telegram as api

BINDING_ID = "9e0e478d3bf0453ba8c890f0edde35ca"
SECRET = "offline-cryptographically-strong-audit-secret"


class ReadOnlyAuditStore:
    def __init__(self, record: dict[str, str] | None) -> None:
        self.record = record
        self.reads: list[str] = []
        self.writes = 0
        self.provider_calls = 0

    async def read(self, binding_id: str) -> dict[str, str] | None:
        self.reads.append(binding_id)
        return self.record


def audit_record() -> dict[str, str]:
    return {
        "binding_id": BINDING_ID,
        "token_hash": "a" * 64,
        "update_id_hash": "b" * 64,
        "target_chat_id_hash": "c" * 64,
        "transport_invoked": "true",
        "provider_call_count": "1",
        "http_status": "200",
        "provider_ok": "true",
        "message_id_present": "true",
        "message_id_hash": "d" * 64,
        "typed_transport_outcome": "ACCEPTED",
        "final_binding_state": "DELIVERED",
        "created_at": "1000",
        "updated_at": "1001",
        "expires_at": "605800",
    }


class TemporaryAuditEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = ReadOnlyAuditStore(audit_record())
        api._telegram_audit_store = self.store  # type: ignore[assignment]
        api._temporary_audit_secret = SECRET
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        api._telegram_audit_store = None
        api._temporary_audit_secret = ""

    def get(self, binding_id: str = BINDING_ID, secret: str | None = SECRET):
        headers = {} if secret is None else {"X-SiteFormo-Telegram-Audit-Secret": secret}
        return self.client.get(f"/api/internal/telegram/audit/{binding_id}", headers=headers)

    def test_exact_read_returns_allowlist_only_and_performs_zero_mutations(self) -> None:
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {
            "binding_id", "token_hash", "update_id_hash", "target_chat_id_hash",
            "transport_invoked", "provider_call_count", "http_status", "provider_ok",
            "message_id_present", "message_id_hash", "typed_outcome", "final_binding_state",
            "created_at", "updated_at", "expires_at",
        })
        self.assertEqual(self.store.reads, [BINDING_ID])
        self.assertEqual(self.store.writes, 0)
        self.assertEqual(self.store.provider_calls, 0)
        serialized = response.text
        for forbidden in ("raw-token", "raw-chat", "raw-message", "bot-token", SECRET, "redis://"):
            self.assertNotIn(forbidden, serialized)

    def test_missing_and_invalid_secret_are_forbidden_before_storage(self) -> None:
        self.assertEqual(self.get(secret=None).status_code, 403)
        self.assertEqual(self.get(secret="wrong").status_code, 403)
        self.assertEqual(self.store.reads, [])

    def test_unknown_malformed_and_enumeration_are_fail_closed(self) -> None:
        self.store.record = None
        self.assertEqual(self.get("0" * 32).status_code, 404)
        self.assertEqual(self.get("not-a-binding").status_code, 400)
        self.assertEqual(self.client.get(
            "/api/internal/telegram/audit", params={"binding_id": BINDING_ID},
            headers={"X-SiteFormo-Telegram-Audit-Secret": SECRET},
        ).status_code, 404)

    def test_persistence_error_is_safe_503(self) -> None:
        async def fail(_: str):
            raise RuntimeError("private persistence detail")

        self.store.read = fail  # type: ignore[method-assign]
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "audit_unavailable"})


if __name__ == "__main__":
    unittest.main()
