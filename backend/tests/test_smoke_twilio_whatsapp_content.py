from __future__ import annotations

import io
import json
import unittest

import httpx

from scripts.smoke_twilio_whatsapp_content import load_config, run

ACCOUNT = "AC" + "1" * 32
TOKEN = "offline-auth-token-never-log"
SENDER = "whatsapp:+353892373448"
CONTENT = "HX" + "2" * 32
RECIPIENT = "+15550000001"


def environment(**changes: str) -> dict[str, str]:
    result = {
        "TWILIO_ACCOUNT_SID": ACCOUNT,
        "TWILIO_AUTH_TOKEN": TOKEN,
        "TWILIO_WHATSAPP_FROM": SENDER,
        "TWILIO_WHATSAPP_CONTENT_SID": CONTENT,
        "TWILIO_WHATSAPP_TEST_TO": RECIPIENT,
    }
    result.update(changes)
    return result


class FakeResponse:
    status_code = 201

    def raise_for_status(self) -> None:
        return None


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append((url, kwargs))
        return FakeResponse()  # type: ignore[return-value]


class SmokeContentTests(unittest.TestCase):
    def test_missing_env(self) -> None:
        values = environment()
        values.pop("TWILIO_AUTH_TOKEN")
        with self.assertRaisesRegex(ValueError, "missing_required_env:TWILIO_AUTH_TOKEN"):
            load_config(values)

    def test_invalid_e164(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_test_recipient_e164"):
            load_config(environment(TWILIO_WHATSAPP_TEST_TO="086 123 4567"))

    def test_invalid_content_sid(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_content_sid"):
            load_config(environment(TWILIO_WHATSAPP_CONTENT_SID="not-a-content-sid"))

    def test_dry_run_does_not_call_twilio_and_masks_recipient(self) -> None:
        client, output = FakeClient(), io.StringIO()
        self.assertEqual(
            run(environment=environment(), first_name="Alex", execute=False,
                template_approved=False, client=client, output=output),
            0,
        )
        self.assertEqual(client.calls, [])
        plan = json.loads(output.getvalue())
        self.assertFalse(plan["execute"])
        self.assertNotIn(RECIPIENT, output.getvalue())

    def test_execute_requires_approval_confirmation(self) -> None:
        client, output = FakeClient(), io.StringIO()
        with self.assertRaisesRegex(RuntimeError, "template_approval_confirmation_required"):
            run(environment=environment(), first_name="Alex", execute=True,
                template_approved=False, client=client, output=output)
        self.assertEqual(client.calls, [])

    def test_execute_calls_twilio_once(self) -> None:
        client, output = FakeClient(), io.StringIO()
        run(environment=environment(), first_name="Alex", execute=True,
            template_approved=True, client=client, output=output)
        self.assertEqual(len(client.calls), 1)
        _, request = client.calls[0]
        self.assertEqual(request["data"], {
            "From": SENDER,
            "To": f"whatsapp:{RECIPIENT}",
            "ContentSid": CONTENT,
            "ContentVariables": '{"first_name":"Alex"}',
        })

    def test_secrets_are_not_logged(self) -> None:
        client, output = FakeClient(), io.StringIO()
        run(environment=environment(), first_name="Alex", execute=False,
            template_approved=False, client=client, output=output)
        rendered = output.getvalue()
        self.assertNotIn(TOKEN, rendered)
        self.assertNotIn(ACCOUNT, rendered)
        self.assertNotIn(RECIPIENT, rendered)


if __name__ == "__main__":
    unittest.main()
