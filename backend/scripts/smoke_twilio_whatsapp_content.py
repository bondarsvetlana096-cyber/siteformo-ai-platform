from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Mapping, Protocol, TextIO

import httpx

E164 = re.compile(r"^\+[1-9][0-9]{1,14}$", re.ASCII)
ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
CONTENT_SID = re.compile(r"^HX[0-9a-fA-F]{32}$")
REQUIRED_ENV = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_WHATSAPP_FROM",
    "TWILIO_WHATSAPP_CONTENT_SID",
    "TWILIO_WHATSAPP_TEST_TO",
)


class HttpClient(Protocol):
    def post(self, url: str, **kwargs: object) -> httpx.Response: ...


@dataclass(frozen=True, slots=True)
class SmokeConfig:
    account_sid: str
    auth_token: str
    sender: str
    recipient_e164: str
    content_sid: str


def normalize_whatsapp_sender(value: str) -> str:
    candidate = value.strip()
    if not candidate.startswith("whatsapp:"):
        raise ValueError("invalid_whatsapp_sender")
    number = candidate[len("whatsapp:") :]
    if not E164.fullmatch(number):
        raise ValueError("invalid_whatsapp_sender")
    return f"whatsapp:{number}"


def normalize_recipient(value: str) -> str:
    candidate = value.strip()
    if candidate.startswith("whatsapp:"):
        candidate = candidate[len("whatsapp:") :]
    if not E164.fullmatch(candidate):
        raise ValueError("invalid_test_recipient_e164")
    return candidate


def load_config(environment: Mapping[str, str]) -> SmokeConfig:
    missing = [name for name in REQUIRED_ENV if not environment.get(name, "").strip()]
    if missing:
        raise ValueError("missing_required_env:" + ",".join(missing))
    account_sid = environment["TWILIO_ACCOUNT_SID"].strip()
    content_sid = environment["TWILIO_WHATSAPP_CONTENT_SID"].strip()
    if not ACCOUNT_SID.fullmatch(account_sid):
        raise ValueError("invalid_account_sid")
    if not CONTENT_SID.fullmatch(content_sid):
        raise ValueError("invalid_content_sid")
    return SmokeConfig(
        account_sid=account_sid,
        auth_token=environment["TWILIO_AUTH_TOKEN"].strip(),
        sender=normalize_whatsapp_sender(environment["TWILIO_WHATSAPP_FROM"]),
        recipient_e164=normalize_recipient(environment["TWILIO_WHATSAPP_TEST_TO"]),
        content_sid=content_sid,
    )


def mask_phone(value: str) -> str:
    return value[:3] + "*" * max(3, len(value) - 6) + value[-3:]


def safe_plan(config: SmokeConfig, *, execute: bool) -> dict[str, object]:
    return {
        "masked_recipient": mask_phone(config.recipient_e164),
        "sender": config.sender,
        "content_sid": config.content_sid,
        "variable_keys": ["first_name"],
        "execute": execute,
    }


def run(
    *,
    environment: Mapping[str, str],
    first_name: str,
    execute: bool,
    template_approved: bool,
    client: HttpClient | None,
    output: TextIO,
) -> int:
    config = load_config(environment)
    validated_name = first_name.strip()
    if not validated_name or len(validated_name) > 100 or "\r" in validated_name or "\n" in validated_name:
        raise ValueError("invalid_first_name")
    print(json.dumps(safe_plan(config, execute=execute), sort_keys=True), file=output)
    if not execute:
        return 0
    if not template_approved:
        raise RuntimeError("template_approval_confirmation_required")
    if client is None:
        client = httpx.Client(timeout=15.0)
    response = client.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{config.account_sid}/Messages.json",
        data={
            "From": config.sender,
            "To": f"whatsapp:{config.recipient_e164}",
            "ContentSid": config.content_sid,
            "ContentVariables": json.dumps(
                {"first_name": validated_name}, separators=(",", ":"), ensure_ascii=False
            ),
        },
        auth=(config.account_sid, config.auth_token),
    )
    response.raise_for_status()
    print(json.dumps({"sent": True, "provider_status": response.status_code}), file=output)
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Dry-run-first Twilio WhatsApp Content smoke test")
    result.add_argument("--first-name", required=True)
    result.add_argument("--execute", action="store_true")
    result.add_argument(
        "--template-approved",
        action="store_true",
        help="Operator confirms Twilio/WhatsApp currently shows APPROVED; required with --execute",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if args.template_approved and not args.execute:
        raise SystemExit("--template-approved is only valid with --execute")
    try:
        return run(
            environment=os.environ,
            first_name=args.first_name,
            execute=args.execute,
            template_approved=args.template_approved,
            client=None,
            output=sys.stdout,
        )
    except httpx.HTTPError:
        # HTTP exception text can contain the Account SID in the request URL.
        print(json.dumps({"error": "twilio_request_failed"}), file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
