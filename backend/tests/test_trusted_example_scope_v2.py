from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.api.demo_contact_email import ContactEmailRequest
from app.api.demo_voice import VoiceDemoRequest
from app.services.contact_delivery.canary import CLAIM_SCRIPT as EMAIL_CLAIM_SCRIPT
from app.services.contact_delivery.canary import _keys as email_keys
from app.services.contact_delivery.canary import delivery_identity
from app.services.contact_delivery.example_scope import (
    BUSINESS_1_EXAMPLE_ID,
    BUSINESS_2_EXAMPLE_ID,
    BUSINESS_3_EXAMPLE_ID,
    ExampleScopeError,
    resolve_trusted_example,
)
from app.services.delivery.contracts import DeliveryIdentity
from app.services.delivery.redis_state import CLAIM_SCRIPT as SMS_CLAIM_SCRIPT
from app.services.delivery.redis_state import RedisDeliveryState
from app.services.sms_delivery.contract import SmsDemoRequest
from app.services.voice_delivery.models import VoiceRequest, digest
from app.services.voice_delivery.store import SCHEDULE_SCRIPT


class PersistentQuotaModel:
    """Request/session-independent model of the permanent product contract."""

    def __init__(self, limit: int = 2) -> None:
        self.limit = limit
        self.accepted: dict[tuple[str, str, str], int] = {}
        self.rate: dict[tuple[str, str], int] = {}

    def request(self, example_id: str, channel: str, contact_hash: str) -> str:
        key = (example_id, channel, contact_hash)
        if self.accepted.get(key, 0) >= self.limit:
            return "quota_exhausted"
        self.accepted[key] = self.accepted.get(key, 0) + 1
        return "accepted"

    def reset_temporary_rate_limit(self) -> None:
        self.rate.clear()


def test_dev_origin_accepts_all_canonical_examples_and_rejects_arbitrary_scope() -> None:
    origin = "https://dev.siteformo.com"
    for example_id in (BUSINESS_1_EXAMPLE_ID, BUSINESS_2_EXAMPLE_ID, BUSINESS_3_EXAMPLE_ID):
        assert resolve_trusted_example(origin, example_id).example_id == example_id
    with pytest.raises(ExampleScopeError, match="example_scope_not_allowed"):
        resolve_trusted_example(origin, "ATTACKER_OVERRIDE")


def test_public_origins_are_uniquely_bound_and_migration_safe() -> None:
    cases = {
        "https://business1.siteformo.com": BUSINESS_1_EXAMPLE_ID,
        "https://business2.siteformo.com": BUSINESS_2_EXAMPLE_ID,
        "https://business3.siteformo.com": BUSINESS_3_EXAMPLE_ID,
    }
    for origin, example_id in cases.items():
        assert resolve_trusted_example(origin, example_id).example_id == example_id
        assert resolve_trusted_example(origin, None).example_id == example_id
    with pytest.raises(ExampleScopeError):
        resolve_trusted_example("https://business3.siteformo.com", BUSINESS_1_EXAMPLE_ID)


def test_legacy_dev_fallback_is_explicitly_business_1_only() -> None:
    scope = resolve_trusted_example("https://dev.siteformo.com", None)
    assert scope.example_id == BUSINESS_1_EXAMPLE_ID
    assert scope.legacy_fallback is True


def test_strict_channel_models_accept_example_id_and_reject_unknown_fields() -> None:
    email = dict(first_name="Oleh", last_name="Test", preferred_method="Email",
                 contact_value="oleh@example.com", message="Hello",
                 idempotency_key="email-key-0000001", example_id=BUSINESS_3_EXAMPLE_ID)
    sms = dict(first_name="Oleh", phone="+12025550124", customer_message="Hello",
               idempotency_key="sms-key-000000001", example_id=BUSINESS_3_EXAMPLE_ID)
    voice = dict(first_name="Oleh", phone="+353871234567",
                 idempotency_key="voice-key-0000001", example_id=BUSINESS_3_EXAMPLE_ID)
    assert ContactEmailRequest(**email).example_id == BUSINESS_3_EXAMPLE_ID
    assert SmsDemoRequest(**sms).example_id == BUSINESS_3_EXAMPLE_ID
    assert VoiceDemoRequest(**voice).example_id == BUSINESS_3_EXAMPLE_ID
    for model, payload in ((ContactEmailRequest, email), (SmsDemoRequest, sms), (VoiceDemoRequest, voice)):
        with pytest.raises(ValidationError):
            model(**payload, unknown_scope="no")


def test_email_quota_keys_isolate_examples_and_channel() -> None:
    keys = []
    for example_id in (BUSINESS_1_EXAMPLE_ID, BUSINESS_2_EXAMPLE_ID, BUSINESS_3_EXAMPLE_ID):
        identity = delivery_identity(example_id=example_id, recipient="same@example.com",
                                     idempotency_key=f"key-{example_id}", fingerprint="f", client_id="c")
        keys.append(email_keys(identity, 0)[1])
    assert len(set(keys)) == 3
    assert all(":quota:EMAIL:" in key for key in keys)


def test_sms_quota_keys_isolate_examples_and_channels() -> None:
    state = RedisDeliveryState("redis://example.invalid", "sf:demo-sms:v1", limit=2)
    recipient = hashlib.sha256(b"+12025550124").hexdigest()
    keys = set()
    for channel in ("SMS:VISITOR", "SMS:OWNER"):
        for example_id in (BUSINESS_1_EXAMPLE_ID, BUSINESS_2_EXAMPLE_ID, BUSINESS_3_EXAMPLE_ID):
            identity = DeliveryIdentity(channel, digest(example_id)[:32], recipient,
                                        digest(channel + example_id), "f", "c")
            keys.add(state.keys(identity, 0)[1])
    assert len(keys) == 6


def test_call_quota_identity_is_example_scoped_and_channel_specific() -> None:
    recipient = digest("+353871234567")
    keys = {
        f"sf:demo-voice:v1:quota:CALL:{digest(example_id)[:32]}:{recipient}"
        for example_id in (BUSINESS_1_EXAMPLE_ID, BUSINESS_2_EXAMPLE_ID, BUSINESS_3_EXAMPLE_ID)
    }
    assert len(keys) == 3
    request = VoiceRequest("request", digest(BUSINESS_3_EXAMPLE_ID)[:32], "Oleh",
                           "+353871234567", recipient, "i" * 64, "c" * 64, 100)
    assert request.example_hash in next(key for key in keys if request.example_hash in key)


def test_channel_namespaces_are_independent_within_one_example() -> None:
    example_hash = digest(BUSINESS_3_EXAMPLE_ID)[:32]
    recipient = digest("protected-contact")
    quota_keys = {
        f"sf:demo-email:v1:quota:EMAIL:{example_hash}:{recipient}",
        f"sf:demo-sms:v1:quota:SMS:VISITOR:{example_hash}:{recipient}",
        f"sf:demo-voice:v1:quota:CALL:{example_hash}:{recipient}",
    }
    assert len(quota_keys) == 3


def test_persistent_quota_matrix_survives_return_and_rate_reset() -> None:
    identity = digest("same-normalized-contact")
    quota = PersistentQuotaModel()

    for example_id in (BUSINESS_1_EXAMPLE_ID, BUSINESS_2_EXAMPLE_ID, BUSINESS_3_EXAMPLE_ID):
        assert quota.request(example_id, "EMAIL", identity) == "accepted"
        assert quota.request(example_id, "EMAIL", identity) == "accepted"
        assert quota.request(example_id, "EMAIL", identity) == "quota_exhausted"

    # A new page/browser session creates no new quota state; durable storage is reused.
    returned_session = quota
    assert returned_session.request(BUSINESS_3_EXAMPLE_ID, "EMAIL", identity) == "quota_exhausted"

    # Temporary anti-abuse state may reset without affecting the permanent quota.
    returned_session.reset_temporary_rate_limit()
    assert returned_session.request(BUSINESS_3_EXAMPLE_ID, "EMAIL", identity) == "quota_exhausted"

    for channel in ("SMS", "CALL"):
        assert returned_session.request(BUSINESS_3_EXAMPLE_ID, channel, identity) == "accepted"
        assert returned_session.request(BUSINESS_3_EXAMPLE_ID, channel, identity) == "accepted"
        assert returned_session.request(BUSINESS_3_EXAMPLE_ID, channel, identity) == "quota_exhausted"


def test_all_channel_quota_counters_are_persistent_and_contact_keys_are_protected() -> None:
    raw_email = "same@example.com"
    email_identity = delivery_identity(
        example_id=BUSINESS_3_EXAMPLE_ID,
        recipient=raw_email,
        idempotency_key="persistent-email-0001",
        fingerprint="fingerprint",
        client_id="client",
    )
    email_quota = email_keys(email_identity, 0)[1]
    sms_state = RedisDeliveryState("redis://example.invalid", "sf:demo-sms:v1", limit=2)
    sms_identity = DeliveryIdentity(
        "SMS:VISITOR", digest(BUSINESS_3_EXAMPLE_ID)[:32], digest("+12025550124"),
        digest("persistent-sms-0001"), "fingerprint", "client",
    )
    sms_quota = sms_state.keys(sms_identity, 0)[1]

    assert "EXPIRE', KEYS[2]" not in EMAIL_CLAIM_SCRIPT
    assert "EXPIRE', KEYS[2]" not in SMS_CLAIM_SCRIPT
    assert "SET', KEYS[2], tostring(recipient_used + 1), 'EX'" not in SCHEDULE_SCRIPT
    assert raw_email not in email_quota
    assert "+12025550124" not in sms_quota
