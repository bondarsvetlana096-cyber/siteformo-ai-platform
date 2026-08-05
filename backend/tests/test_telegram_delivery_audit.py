from __future__ import annotations

import asyncio
import unittest

from app.services.telegram_delivery.audit import AuditOutcome
from app.services.telegram_delivery.models import BindingState
from app.services.telegram_delivery.security import private_id_hash, token_hash
from app.services.telegram_delivery.transport import TransportState
from tests.test_telegram_visitor_binding import (
    SECRET, FakeTransport, MemoryAudit, MemoryStore, create_session, update, webhook_service,
)


class TelegramDeliveryAuditTests(unittest.TestCase):
    def test_audit_contains_hashes_only_and_exactly_one_provider_call(self) -> None:
        store, audit, transport = MemoryStore(), MemoryAudit(), FakeTransport()
        result, token = asyncio.run(create_session(store))
        chat_id, update_id = 987654321, 7654321
        handled = asyncio.run(webhook_service(store, transport, audit).handle(
            update(token, update_id=update_id, chat_id=chat_id), SECRET, 1001,
        ))
        record = audit.records[result.binding_id]
        self.assertEqual(handled.state, BindingState.DELIVERED)
        self.assertEqual(record["token_hash"], token_hash(token))
        self.assertEqual(record["update_id_hash"], private_id_hash(update_id))
        self.assertEqual(record["target_chat_id_hash"], private_id_hash(chat_id))
        self.assertEqual(record["message_length"], len("I'd like to book a meeting"))
        self.assertTrue(record["message_hash"])
        self.assertEqual(record["provider_call_count"], 1)
        self.assertEqual(record["outcome"], AuditOutcome.ACCEPTED)
        self.assertEqual(record["final_state"], BindingState.DELIVERED)
        serialized = repr(record)
        self.assertNotIn(token, serialized)
        self.assertNotIn(str(chat_id), serialized)
        self.assertNotIn("offline-message-1", serialized)
        self.assertNotIn("I'd like to book a meeting", serialized)

    def test_pre_call_audit_failure_blocks_provider(self) -> None:
        for failure in ("fail_create", "fail_mark"):
            store, audit, transport = MemoryStore(), MemoryAudit(), FakeTransport()
            _, token = asyncio.run(create_session(store))
            setattr(audit, failure, True)
            with self.assertRaisesRegex(RuntimeError, "telegram_delivery_audit_unavailable"):
                asyncio.run(webhook_service(store, transport, audit).handle(update(token), SECRET, 1001))
            self.assertEqual(transport.calls, [])

    def test_finalize_failure_never_reports_false_success(self) -> None:
        store, audit, transport = MemoryStore(), MemoryAudit(), FakeTransport()
        _, token = asyncio.run(create_session(store))
        audit.fail_finalize = True
        with self.assertRaisesRegex(RuntimeError, "telegram_delivery_audit_finalize_failed"):
            asyncio.run(webhook_service(store, transport, audit).handle(update(token), SECRET, 1001))
        self.assertEqual(len(transport.calls), 1)

    def test_typed_audit_outcomes_are_distinct(self) -> None:
        cases = [
            (TransportState.AMBIGUOUS, AuditOutcome.AMBIGUOUS, BindingState.QUARANTINED),
            (TransportState.TIMEOUT, AuditOutcome.QUARANTINED, BindingState.QUARANTINED),
            (TransportState.REJECTED, AuditOutcome.REJECTED, BindingState.CONSUMED),
        ]
        for index, (transport_state, audit_outcome, binding_state) in enumerate(cases):
            store, audit, transport = MemoryStore(), MemoryAudit(), FakeTransport(transport_state)
            result, token = asyncio.run(create_session(store))
            handled = asyncio.run(webhook_service(store, transport, audit).handle(
                update(token, update_id=index + 20), SECRET, 1001,
            ))
            self.assertEqual(handled.state, binding_state)
            self.assertEqual(audit.records[result.binding_id]["outcome"], audit_outcome)
            self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
