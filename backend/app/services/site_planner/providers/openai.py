from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from openai import (
    APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI,
    AuthenticationError, InternalServerError, RateLimitError,
)
from pydantic import ValidationError

from app.schemas.site_plan import SitePlanV1
from app.schemas.site_planner import SitePlannerProviderRequest, SitePlannerProviderResult
from app.services.site_planner_config import OpenAISitePlannerConfigV1
from app.services.site_planner_provider import SitePlannerProviderException


OpenAIClientFactory = Callable[..., Any]


class _OuterDeadlineExpired(Exception):
    """Private control-flow signal; never exposed in provider results."""


def _consume_cancelled_task(task: asyncio.Task[Any]) -> None:
    """Retrieve a late task result without retaining or logging provider data."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def _await_with_outer_deadline(awaitable: Any, timeout_seconds: float) -> Any:
    """Bound the entire SDK coroutine independently of HTTP phase timeouts.

    ``asyncio.wait_for`` can wait beyond its timeout while a cancellation-hostile
    coroutine cleans up.  Waiting on the task and cancelling it ourselves keeps
    the caller's deadline authoritative without adding a second grace window.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    except asyncio.CancelledError:
        task.cancel()
        task.add_done_callback(_consume_cancelled_task)
        raise
    if task in done:
        return task.result()
    task.cancel()
    task.add_done_callback(_consume_cancelled_task)
    # Deliver cancellation once, but never await provider cleanup indefinitely.
    await asyncio.sleep(0)
    raise _OuterDeadlineExpired


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _instructions(request: SitePlannerProviderRequest) -> str:
    authority = {
        "planner_contract_version": request.planner_contract_version,
        "site_plan_schema_version": request.site_plan_schema_version,
        "validator_version": request.validator_version,
        "planner_policy": request.planner_policy.model_dump(mode="json"),
        "interaction_safety_contract": request.interaction_safety_contract.model_dump(mode="json"),
    }
    return _canonical_json(authority)


def _input_data(request: SitePlannerProviderRequest) -> list[dict[str, Any]]:
    data = {
        "generation_context": request.generation_context.model_dump(mode="json"),
        "generation_context_hash": request.generation_context_hash,
        "constraint_projection": request.constraint_projection.model_dump(mode="json"),
        "attempt_type": request.attempt_type,
        "attempt_number": request.attempt_number,
        "prior_candidate_projection": (
            request.prior_candidate_projection.model_dump(mode="json")
            if request.prior_candidate_projection else None
        ),
        "validator_reason_codes": request.validator_reason_codes,
    }
    return [{"role": "user", "content": [{"type": "input_text", "text": _canonical_json(data)}]}]


def _usage(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "cached_input_tokens": getattr(input_details, "cached_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "reasoning_tokens": getattr(output_details, "reasoning_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _metadata(response: Any) -> dict[str, Any]:
    return {
        "response_id": getattr(response, "id", None),
        "actual_model": getattr(response, "model", None),
        "response_status": getattr(response, "status", None),
        "service_tier": getattr(response, "service_tier", None),
        **_usage(response),
    }


def _has_refusal(response: Any) -> bool:
    for output in getattr(response, "output", None) or []:
        for content in getattr(output, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                return True
    return False


def _response_failure(response: Any) -> str | None:
    status = getattr(response, "status", None)
    if status == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        return "truncated" if reason == "max_output_tokens" else "unknown"
    if _has_refusal(response):
        return "refusal"
    error = getattr(response, "error", None)
    error_code = str(getattr(error, "code", "") or "").lower()
    if any(marker in error_code for marker in ("content", "safety", "policy")):
        return "content_rejection"
    if status != "completed":
        return "unknown"
    return None


def _exception_category(error: Exception) -> str:
    if isinstance(error, (asyncio.TimeoutError, APITimeoutError)):
        return "timeout"
    if isinstance(error, RateLimitError):
        return "rate_limit"
    if isinstance(error, AuthenticationError):
        return "authentication"
    if isinstance(error, (InternalServerError, APIConnectionError)):
        return "provider_5xx"
    if isinstance(error, APIStatusError):
        if error.status_code >= 500:
            return "provider_5xx"
        error_code = str(getattr(error, "code", "") or "").lower()
        if any(marker in error_code for marker in ("content", "safety", "policy")):
            return "content_rejection"
        return "configuration" if error.status_code in {400, 404, 422} else "unknown"
    if isinstance(error, (ValidationError, AttributeError, TypeError, ValueError)):
        return "malformed_response"
    return "unknown"


class OpenAISitePlannerProvider:
    """One-call OpenAI transport adapter; authority remains with the producer/validator."""

    def __init__(
        self, config: OpenAISitePlannerConfigV1, client: Any,
        *, outer_timeout_seconds: float | None = None,
    ) -> None:
        if not config.enabled:
            raise SitePlannerProviderException("configuration")
        self._config = config
        self._client = client
        self._outer_timeout_seconds = (
            config.timeout_seconds if outer_timeout_seconds is None else outer_timeout_seconds
        )
        if self._outer_timeout_seconds <= 0:
            raise ValueError("outer timeout must be positive")

    async def create_structured_candidate(
        self, request: SitePlannerProviderRequest,
    ) -> SitePlannerProviderResult:
        try:
            response = await _await_with_outer_deadline(self._client.responses.parse(
                model=self._config.model,
                instructions=_instructions(request),
                input=_input_data(request),
                text_format=SitePlanV1,
                reasoning={"effort": self._config.reasoning_effort},
                max_output_tokens=self._config.max_output_tokens,
                tools=[],
                store=False,
                stream=False,
                truncation="disabled",
                service_tier=self._config.service_tier,
                timeout=self._config.timeout_seconds,
            ), self._outer_timeout_seconds)
            metadata = _metadata(response)
            failure = _response_failure(response)
            if failure:
                return SitePlannerProviderResult(status="failure", error_category=failure, **metadata)
            parsed = getattr(response, "output_parsed", None)
            if parsed is None:
                return SitePlannerProviderResult(status="failure", error_category="empty_response", **metadata)
            candidate = parsed.model_dump(mode="json") if isinstance(parsed, SitePlanV1) else SitePlanV1.model_validate(parsed).model_dump(mode="json")
            output_bytes = len(_canonical_json(candidate).encode("utf-8"))
            return SitePlannerProviderResult(
                status="candidate", candidate=candidate, reported_output_bytes=output_bytes,
                **metadata,
            )
        except _OuterDeadlineExpired:
            return SitePlannerProviderResult(status="failure", error_category="timeout")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return SitePlannerProviderResult(status="failure", error_category=_exception_category(error))


def build_openai_site_planner_provider(
    config: OpenAISitePlannerConfigV1,
    client_factory: OpenAIClientFactory = AsyncOpenAI,
) -> OpenAISitePlannerProvider:
    if not config.enabled:
        raise SitePlannerProviderException("configuration")
    client = client_factory(
        api_key=config.api_key.get_secret_value(),
        base_url=config.base_url,
        timeout=config.timeout_seconds,
        max_retries=0,
    )
    return OpenAISitePlannerProvider(config, client)
