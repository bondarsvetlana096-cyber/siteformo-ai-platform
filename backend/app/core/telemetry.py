from __future__ import annotations

import logging

import sentry_sdk
from posthog import Posthog

from app.core.config import settings

logger = logging.getLogger("siteformo.telemetry")
posthog_client: Posthog | None = None


def init_telemetry() -> None:
    global posthog_client
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=0.2,
            environment=settings.app_env,
        )
        logger.info("[TELEMETRY] sentry initialized for env=%s", settings.app_env)
    if settings.posthog_api_key:
        posthog_client = Posthog(project_api_key=settings.posthog_api_key, host=settings.posthog_host)
        logger.info("[TELEMETRY] posthog initialized host=%s", settings.posthog_host)


def capture_event(distinct_id: str, event: str, properties: dict | None = None) -> None:
    if posthog_client:
        posthog_client.capture(distinct_id=distinct_id, event=event, properties=properties or {})


def capture_exception(exc: Exception, context: dict | None = None) -> None:
    if context:
        with sentry_sdk.push_scope() as scope:
            for key, value in context.items():
                scope.set_extra(key, value)
            sentry_sdk.capture_exception(exc)
    else:
        sentry_sdk.capture_exception(exc)
