from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from app.services.sms_delivery.models import COUNTRY_CALLING_CODES, SMSDeliveryMode, normalize_e164

ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
DEFAULT_AUDIT_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class SmsConfiguration:
    enabled: bool
    account_sid: str | None = None
    auth_token: str | None = field(default=None, repr=False)
    sender_e164: str | None = None
    allowed_countries: frozenset[str] = frozenset()
    audit_ttl_seconds: int = DEFAULT_AUDIT_TTL_SECONDS
    delivery_mode: SMSDeliveryMode = SMSDeliveryMode.VISITOR_NOTIFICATION
    owner_to_e164: str | None = field(default=None, repr=False)
    visitor_notifications_enabled: bool = True
    owner_requires_visitor_contact: bool = False

    def require_ready(self) -> None:
        if not self.enabled:
            raise ValueError("sms_demo_disabled")
        if not self.account_sid or not ACCOUNT_SID.fullmatch(self.account_sid):
            raise ValueError("sms_provider_not_configured")
        if not self.auth_token or not self.sender_e164:
            raise ValueError("sms_provider_not_configured")
        normalize_e164(self.sender_e164)
        if not self.allowed_countries or self.audit_ttl_seconds < 86400:
            raise ValueError("sms_provider_not_configured")
        if self.delivery_mode in {SMSDeliveryMode.OWNER_ALERT, SMSDeliveryMode.BOTH}:
            if not self.owner_to_e164:
                raise ValueError("sms_owner_recipient_not_configured")
            normalize_e164(self.owner_to_e164)
        if self.delivery_mode in {SMSDeliveryMode.VISITOR_NOTIFICATION, SMSDeliveryMode.BOTH} and not self.visitor_notifications_enabled:
            raise ValueError("sms_visitor_notifications_disabled")


def _flag(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    raise ValueError("invalid_sms_demo_enabled")


def resolve_sms_configuration(environment: Mapping[str, str]) -> SmsConfiguration:
    raw_countries = environment.get("SMS_DEMO_ALLOWED_COUNTRIES", "")
    countries = frozenset(part.strip().upper() for part in raw_countries.split(",") if part.strip())
    if countries - COUNTRY_CALLING_CODES.keys():
        raise ValueError("unsupported_sms_country")
    raw_ttl = environment.get("SMS_DEMO_AUDIT_TTL_SECONDS", str(DEFAULT_AUDIT_TTL_SECONDS))
    try:
        ttl = int(raw_ttl)
    except ValueError as exc:
        raise ValueError("invalid_sms_audit_ttl") from exc
    try:
        mode = SMSDeliveryMode(environment.get("SMS_DELIVERY_MODE", SMSDeliveryMode.VISITOR_NOTIFICATION.value).strip().upper())
    except ValueError as exc:
        raise ValueError("invalid_sms_delivery_mode") from exc
    return SmsConfiguration(
        enabled=_flag(environment.get("SMS_DEMO_ENABLED")),
        account_sid=environment.get("TWILIO_SMS_ACCOUNT_SID", "").strip() or None,
        auth_token=environment.get("TWILIO_SMS_AUTH_TOKEN", "").strip() or None,
        sender_e164=environment.get("TWILIO_SMS_FROM", "").strip() or None,
        allowed_countries=countries,
        audit_ttl_seconds=ttl,
        delivery_mode=mode,
        owner_to_e164=environment.get("SMS_OWNER_TO", "").strip() or None,
        visitor_notifications_enabled=_flag(environment.get("SMS_VISITOR_NOTIFICATIONS_ENABLED", "true")),
        owner_requires_visitor_contact=_flag(environment.get("SMS_OWNER_REQUIRES_VISITOR_CONTACT", "false")),
    )
