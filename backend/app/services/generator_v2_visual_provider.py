"""C4A: disabled-by-default structured OpenAI visual-plan provider.

The adapter owns transport only.  VisualImplementationPlanV1 and the C3
validator remain the authority; no HTML/CSS/JS or runtime integration lives
here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Mapping
from time import monotonic
from typing import Any, Callable, Literal

from openai import (
    APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI,
    AuthenticationError, InternalServerError, RateLimitError,
)
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

from app.services.generator_v2_visual import (
    VisualImplementationCandidateV1,
    VisualImplementationPlanV1,
    accept_visual_implementation_candidate,
    VisualImplementationProviderRequestV1,
    validate_visual_implementation_plan_v1,
)


VISUAL_POLICY_VERSION = "v1"
VISUAL_PROVIDER_CONFIG_VERSION = "openai-visual-v1"
VISUAL_PLANNER_MODEL = "gpt-5.6-sol"
VISUAL_PLANNER_REASONING = "medium"
VISUAL_PLANNER_MAX_OUTPUT_TOKENS = 16_384


class VisualPlannerProviderException(Exception):
    """Safe provider boundary exception; message is never returned to callers."""


class VisualPlannerConfigV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    provider: Literal["openai"] | None = None
    api_key: SecretStr | None = None
    base_url: str = "https://api.openai.com/v1"
    model: Literal["gpt-5.6-sol"] | None = None
    reasoning_effort: Literal["medium"] = "medium"
    timeout_seconds: int = Field(default=180, ge=30, le=600)
    max_output_tokens: int = Field(default=VISUAL_PLANNER_MAX_OUTPUT_TOKENS, ge=4096, le=32768)
    config_version: Literal["openai-visual-v1"] = VISUAL_PROVIDER_CONFIG_VERSION

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if value not in {"https://api.openai.com/v1", "https://eu.api.openai.com/v1"}:
            raise ValueError("unsupported OpenAI API base URL")
        return value

    @model_validator(mode="after")
    def _enabled_is_complete(self) -> "VisualPlannerConfigV1":
        if self.enabled and self.provider is None:
            raise ValueError("enabled visual planner requires provider")
        if self.enabled and self.model is None:
            raise ValueError("enabled visual planner requires model")
        if self.enabled and (self.api_key is None or not self.api_key.get_secret_value().strip()):
            raise ValueError("enabled visual planner requires API key")
        return self


_ENV_PREFIX = "SITEFORMO_VISUAL_PLANNER_"
_DECIMAL = re.compile(r"^[0-9]+$")


def _bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{name} must be exactly true or false")
    return normalized == "true"


def _int(name: str, value: str) -> int:
    value = value.strip()
    if not _DECIMAL.fullmatch(value):
        raise ValueError(f"{name} must be a base-10 unsigned integer")
    return int(value, 10)


def load_visual_planner_config_v1(environment: Mapping[str, str] | None = None) -> VisualPlannerConfigV1:
    values = environment if environment is not None else os.environ
    fields = {
        "enabled": "ENABLED", "provider": "PROVIDER", "api_key": "API_KEY", "base_url": "BASE_URL",
        "model": "MODEL", "reasoning_effort": "REASONING_EFFORT", "timeout_seconds": "TIMEOUT_SECONDS",
        "max_output_tokens": "MAX_OUTPUT_TOKENS", "config_version": "CONFIG_VERSION",
    }
    integers = {"timeout_seconds", "max_output_tokens"}
    payload: dict[str, object] = {}
    for field, suffix in fields.items():
        name = _ENV_PREFIX + suffix
        if name not in values:
            continue
        raw = values[name]
        if not isinstance(raw, str):
            raise ValueError(f"{name} must be supplied as an environment string")
        payload[field] = _bool(name, raw) if field == "enabled" else _int(name, raw) if field in integers else raw.strip()
    return VisualPlannerConfigV1.model_validate(payload)


class VisualProviderUsageV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


VisualProviderStatus = Literal["valid", "manual_review", "provider_failure"]
VisualProviderError = Literal[
    "timeout", "cancelled", "rate_limit", "authentication", "provider_5xx", "configuration",
    "content_rejection", "refusal", "empty_response", "malformed_response", "invalid_visual_plan", "unknown",
]


class VisualProviderResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: VisualProviderStatus
    plan: VisualImplementationPlanV1 | None = None
    error_category: VisualProviderError | None = None
    provider: Literal["openai"] = "openai"
    model: Literal["gpt-5.6-sol"] = VISUAL_PLANNER_MODEL
    operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    structural_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    latency_ms: int | None = Field(default=None, ge=0)
    output_bytes: int | None = Field(default=None, ge=0)
    usage: VisualProviderUsageV1 = VisualProviderUsageV1()
    validation_reason_codes: tuple[str, ...] = ()


VISUAL_POLICY_V1: dict[str, object] = {
    "policy_version": VISUAL_POLICY_VERSION,
    "role": "visual_planner_only",
    "rules": [
        "design_one_coherent_cross_page_visual_system",
        "respect_design_direction_and_interaction_preference_as_envelopes",
        "preserve_all_structural_authority_exactly",
        "use_only_closed_composition_and_token_vocabularies",
        "keep_critical_actions_visible_stable_keyboard_and_touch_safe",
        "design_desktop_mobile_and_reduced_motion_together",
        "provide_reduced_motion_alternative_for_every_motion_choice",
        "never_emit_html_css_javascript_urls_or_instructions",
    ],
}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _instructions() -> str:
    return _canonical(VISUAL_POLICY_V1)


def _input(request: VisualImplementationProviderRequestV1) -> list[dict[str, Any]]:
    data = {
        "visual_implementation_input": request.input.model_dump(mode="json"),
        "allowed_compositions": list(request.allowed_compositions),
        "output_contract_version": request.output_contract_version,
    }
    return [{"role": "user", "content": [{"type": "input_text", "text": _canonical(data)}]}]


def _usage(response: Any) -> VisualProviderUsageV1:
    usage = getattr(response, "usage", None)
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    values = {
        "input_tokens": getattr(usage, "input_tokens", None),
        "cached_input_tokens": getattr(input_details, "cached_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "reasoning_tokens": getattr(output_details, "reasoning_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
    return VisualProviderUsageV1.model_validate(values)


def _exception_category(error: Exception) -> VisualProviderError:
    if isinstance(error, (asyncio.TimeoutError, APITimeoutError)):
        return "timeout"
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, RateLimitError):
        return "rate_limit"
    if isinstance(error, AuthenticationError):
        return "authentication"
    if isinstance(error, (InternalServerError, APIConnectionError)):
        return "provider_5xx"
    if isinstance(error, APIStatusError):
        return "provider_5xx" if error.status_code >= 500 else "configuration"
    if isinstance(error, (ValidationError, AttributeError, TypeError, ValueError)):
        return "malformed_response"
    return "unknown"


async def _bounded(awaitable: Any, seconds: float) -> Any:
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if task in done:
        return task.result()
    task.cancel()
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    await asyncio.sleep(0)
    raise asyncio.TimeoutError


class OpenAIVisualImplementationProviderV1:
    def __init__(self, config: VisualPlannerConfigV1, client: Any, *, outer_timeout_seconds: float | None = None) -> None:
        if not config.enabled:
            raise VisualPlannerProviderException("configuration")
        self._config = config
        self._client = client
        self._outer_timeout_seconds = config.timeout_seconds if outer_timeout_seconds is None else outer_timeout_seconds
        if self._outer_timeout_seconds <= 0:
            raise ValueError("outer timeout must be positive")

    async def create_visual_plan(self, request: VisualImplementationProviderRequestV1) -> VisualProviderResultV1:
        operation_key = _sha({"generator_input_hash": request.input.generator_input_hash, "visual_policy_version": VISUAL_POLICY_VERSION})
        started = monotonic()
        try:
            response = await _bounded(self._client.responses.parse(
                model=self._config.model,
                instructions=_instructions(), input=_input(request),
                text_format=VisualImplementationCandidateV1,
                reasoning={"effort": self._config.reasoning_effort},
                max_output_tokens=self._config.max_output_tokens,
                tools=[], store=False, stream=False, truncation="disabled",
                timeout=self._config.timeout_seconds,
            ), self._outer_timeout_seconds)
            usage = _usage(response)
            latency = int((monotonic() - started) * 1000)
            base = {"operation_key": operation_key, "structural_fingerprint": request.input.structural_fingerprint,
                    "latency_ms": latency, "usage": usage}
            status = getattr(response, "status", None)
            if status == "incomplete":
                return VisualProviderResultV1(status="provider_failure", error_category="malformed_response", **base)
            output = getattr(response, "output", None) or []
            if any(getattr(c, "type", None) == "refusal" for item in output for c in (getattr(item, "content", None) or [])):
                return VisualProviderResultV1(status="provider_failure", error_category="refusal", **base)
            if status != "completed":
                return VisualProviderResultV1(status="provider_failure", error_category="unknown", **base)
            parsed = getattr(response, "output_parsed", None)
            if parsed is None:
                return VisualProviderResultV1(status="provider_failure", error_category="empty_response", **base)
            candidate = parsed if isinstance(parsed, VisualImplementationCandidateV1) else VisualImplementationCandidateV1.model_validate(parsed)
            plan = accept_visual_implementation_candidate(request.input, candidate)
            validation = validate_visual_implementation_plan_v1(request.input, plan)
            if validation.status != "VALID":
                return VisualProviderResultV1(status="manual_review", error_category="invalid_visual_plan",
                                              validation_reason_codes=tuple(validation.reason_codes), **base)
            output_bytes = len(_canonical(plan.model_dump(mode="json")).encode("utf-8"))
            return VisualProviderResultV1(status="valid", plan=plan, output_bytes=output_bytes, **base)
        except asyncio.CancelledError:
            return VisualProviderResultV1(status="provider_failure", error_category="cancelled", operation_key=operation_key,
                                          structural_fingerprint=request.input.structural_fingerprint,
                                          latency_ms=int((monotonic() - started) * 1000))
        except Exception as error:
            return VisualProviderResultV1(status="provider_failure", error_category=_exception_category(error), operation_key=operation_key,
                                          structural_fingerprint=request.input.structural_fingerprint,
                                          latency_ms=int((monotonic() - started) * 1000))

    async def create_visual_implementation_plan(self, request: VisualImplementationProviderRequestV1) -> VisualProviderResultV1:
        """Descriptive alias for callers naming the typed output contract."""
        return await self.create_visual_plan(request)


OpenAIClientFactory = Callable[..., Any]


def build_openai_visual_implementation_provider_v1(
    config: VisualPlannerConfigV1, client_factory: OpenAIClientFactory = AsyncOpenAI,
) -> OpenAIVisualImplementationProviderV1:
    if not config.enabled or config.api_key is None or config.model is None:
        raise VisualPlannerProviderException("configuration")
    client = client_factory(api_key=config.api_key.get_secret_value(), base_url=config.base_url,
                            timeout=config.timeout_seconds, max_retries=0)
    return OpenAIVisualImplementationProviderV1(config, client)


# Stable descriptive aliases keep the C4A boundary discoverable without
# introducing a second implementation.
OpenAIVisualPlanProviderV1 = OpenAIVisualImplementationProviderV1
build_openai_visual_plan_provider_v1 = build_openai_visual_implementation_provider_v1
