from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

import httpx

from app.services.voice_delivery.configuration import resolve_configuration
from app.services.voice_delivery.service import VoiceDemoService, VoiceDispatcher
from app.services.voice_delivery.store import RedisVoiceStore
from app.services.voice_delivery.transport import TwilioVoiceTransport, TwilioVoiceTransportConfig

service: VoiceDemoService | None = None
dispatcher: VoiceDispatcher | None = None
http_client: httpx.AsyncClient | None = None
dispatcher_task: asyncio.Task | None = None
configuration = None


def configure_voice_runtime(
    environment: dict[str, str] | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> bool:
    global service, dispatcher, http_client, configuration
    values = environment if environment is not None else dict(os.environ)
    service = dispatcher = None
    try:
        configuration = resolve_configuration(values)
    except ValueError:
        configuration = None
        return False
    if not configuration.enabled:
        return False
    try:
        configuration.require_ready()
    except ValueError:
        return False
    redis_url = values.get("REDIS_URL", "").strip()
    if not redis_url:
        return False
    http_client = client_factory(timeout=10.0)
    store = RedisVoiceStore(
        redis_url, recipient_limit=configuration.recipient_limit,
        global_limit=configuration.global_limit,
    )
    transport = TwilioVoiceTransport(
        TwilioVoiceTransportConfig(
            account_sid=configuration.account_sid or "",
            auth_token=configuration.auth_token or "",
            caller_e164=configuration.caller_e164 or "",
            status_callback_url=f"{configuration.public_base_url}/api/demo/voice/status",
            voice=configuration.voice, language=configuration.language,
        ),
        http_client,
    )
    service, dispatcher = VoiceDemoService(configuration, store), VoiceDispatcher(store, transport)
    return True


async def _dispatcher_loop() -> None:
    while True:
        try:
            if dispatcher is not None:
                await dispatcher.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Claimed work is already quarantined and is never retried.
            pass
        await asyncio.sleep(1)


def start_voice_dispatcher() -> None:
    global dispatcher_task
    if dispatcher is not None and dispatcher_task is None:
        dispatcher_task = asyncio.create_task(_dispatcher_loop(), name="voice-demo-dispatcher")


async def close_voice_runtime() -> None:
    global service, dispatcher, http_client, dispatcher_task, configuration
    if dispatcher_task is not None:
        dispatcher_task.cancel()
        try:
            await dispatcher_task
        except asyncio.CancelledError:
            pass
    if http_client is not None:
        await http_client.aclose()
    service = dispatcher = http_client = dispatcher_task = configuration = None
