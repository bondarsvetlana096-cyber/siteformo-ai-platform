from __future__ import annotations

from scripts.smoke_sms_controlled import SmokeRequest, run_smoke


def environment(**changes: str) -> dict[str, str]:
    values = {
        "TWILIO_SMS_ACCOUNT_SID": "AC" + "1" * 32,
        "TWILIO_SMS_AUTH_TOKEN": "synthetic-test-only",
        "TWILIO_SMS_FROM": "+12025550123",
        "SMS_DEMO_ENABLED": "true",
        "SMS_DEMO_ALLOWED_COUNTRIES": "US",
        "SMS_DEMO_AUDIT_TTL_SECONDS": "604800",
    }
    values.update(changes)
    return values


def request(*, execute: bool = False, authorized: bool = False, recipient: str = "+12025550124") -> SmokeRequest:
    return SmokeRequest(
        "https://siteformo-ai-platform-production.up.railway.app/api/demo/sms/start",
        "https://dev.siteformo.com",
        recipient,
        "Oleh",
        "Hi SiteFormo",
        "sms-controlled-key-0001",
        execute,
        authorized,
    )


class FakeResponse:
    status_code = 201
    def json(self):
        return {"status": "accepted", "delivery_reference": "sms_safe", "replayed": False}


class FakeClient:
    def __init__(self, calls, **kwargs):
        self.calls, self.kwargs = calls, kwargs
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()
    def close(self):
        pass


def test_dry_run_never_constructs_client_and_masks_numbers() -> None:
    created = []
    code, result = run_smoke(request(), environment(), lambda **kwargs: created.append(kwargs))
    assert code == 0 and result["typed_outcome"] == "DRY_RUN"
    assert result["provider_call_count"] == 0 and created == []
    assert "+12025550124" not in str(result) and "+12025550123" not in str(result)
    assert "Please confirm my enquiry." not in str(result)
    assert result["message_encoding"] == "GSM-7"
    assert result["message_segment_count"] == 1


def test_execute_requires_owner_authorization_before_client() -> None:
    created = []
    code, result = run_smoke(request(execute=True), environment(), lambda **kwargs: created.append(kwargs))
    assert code == 2 and result["typed_outcome"] == "BLOCKED_OWNER_AUTHORIZATION_REQUIRED"
    assert created == []


def test_execute_requires_enabled_complete_configuration() -> None:
    for env in (environment(SMS_DEMO_ENABLED="false"), environment(TWILIO_SMS_AUTH_TOKEN=""), environment(TWILIO_SMS_FROM="invalid")):
        created = []
        code, result = run_smoke(request(execute=True, authorized=True), env, lambda **kwargs: created.append(kwargs))
        assert code == 2 and result["typed_outcome"] == "BLOCKED_CONFIGURATION_NOT_READY"
        assert created == []


def test_execute_calls_endpoint_exactly_once_after_all_gates() -> None:
    calls = []
    code, result = run_smoke(request(execute=True, authorized=True), environment(), lambda **kwargs: FakeClient(calls, **kwargs))
    assert code == 0 and result["typed_outcome"] == "ACCEPTED"
    assert result["provider_call_count"] == 1 and len(calls) == 1
    assert calls[0][1]["json"]["phone"] == "+12025550124"
    assert "body" not in calls[0][1]["json"]
    assert calls[0][1]["json"]["customer_message"] == "Hi SiteFormo"


def test_invalid_or_disallowed_recipient_never_constructs_client() -> None:
    for recipient in ("2025550124", "+447700900123"):
        created = []
        try:
            run_smoke(request(execute=True, authorized=True, recipient=recipient), environment(), lambda **kwargs: created.append(kwargs))
        except ValueError:
            pass
        assert created == []
