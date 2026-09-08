from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Callable

from openai import AsyncOpenAI

from app.assistant.config import AssistantSettings

logger = logging.getLogger("siteformo.assistant.inference")


class AssistantInferenceError(RuntimeError):
    pass


class AssistantOpenAIAdapter:
    def __init__(self, settings: AssistantSettings, client_factory: Callable[..., Any] = AsyncOpenAI) -> None:
        self._settings = settings
        self._client_factory = client_factory

    def _create_client(self) -> Any:
        self._settings.require_inference_configuration()
        return self._client_factory(
            api_key=self._settings.openai_api_key,
            timeout=self._settings.openai_timeout_seconds,
            max_retries=self._settings.openai_max_retries,
        )

    async def stream_response(self, input_items: list[dict[str, str]]) -> AsyncIterator[str]:
        client = None
        try:
            client = self._create_client()
            logger.info("Assistant inference started model=%s input_items=%s", self._settings.openai_model, len(input_items))
            stream = await client.responses.create(
                model=self._settings.openai_model,
                input=input_items,
                stream=True,
                tools=[],
            )
            async for event in stream:
                if getattr(event, "type", None) == "response.output_text.delta":
                    delta = getattr(event, "delta", None)
                    if delta:
                        yield str(delta)
        except Exception as exc:
            logger.warning("Assistant inference failed error_type=%s", type(exc).__name__)
            raise AssistantInferenceError("Assistant inference failed") from exc
        finally:
            close = getattr(client, "close", None) if client is not None else None
            if close is not None:
                await close()
