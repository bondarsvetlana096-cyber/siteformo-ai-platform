from __future__ import annotations

from dataclasses import dataclass

from app.services.whatsapp_delivery.configuration import FlagStatus, RailwayTwilioConfiguration, Readiness


@dataclass(frozen=True, slots=True)
class PublicReadinessInput:
    configuration: RailwayTwilioConfiguration
    origin_allowed: bool
    redis_available: bool
    circuit_open: bool


def require_public_readiness(value: PublicReadinessInput) -> None:
    configuration = value.configuration
    ready = all(
        (
            configuration.readiness is Readiness.READY,
            configuration.provider_status == "TWILIO",
            configuration.sender.value is not None
            or configuration.messaging_service_sid.value is not None,
            configuration.content_sid.value is not None,
            configuration.message_mode == "BUSINESS_INITIATED_APPROVED_CONTENT",
            value.origin_allowed,
            value.redis_available,
            configuration.public_demo_flag is FlagStatus.ENABLED,
            not value.circuit_open,
        )
    )
    if not ready:
        raise RuntimeError("whatsapp_public_delivery_not_ready")
