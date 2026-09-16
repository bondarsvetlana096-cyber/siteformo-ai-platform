from __future__ import annotations

import asyncio
import ast
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.generator_v2_implementation import build_generator_v2_implementation_spec
from app.services.generator_v2_visual import (
    build_default_visual_implementation_plan,
    build_visual_implementation_input,
    build_visual_implementation_provider_request,
)
from app.services.generator_v2_visual_provider import (
    VISUAL_POLICY_V1,
    OpenAIVisualImplementationProviderV1,
    VisualPlannerConfigV1,
    VisualPlannerProviderException,
    build_openai_visual_implementation_provider_v1,
    load_visual_planner_config_v1,
)
from test_generator_v2_contract_v1 import make_snapshot


def visual_request(case_id="BUSINESS_THREE_PAGE"):
    snapshot = make_snapshot(case_id).snapshot
    implementation = build_generator_v2_implementation_spec(snapshot)
    assert implementation.spec is not None
    visual_input = build_visual_implementation_input(snapshot, implementation.spec)
    assert visual_input.input is not None
    return build_visual_implementation_provider_request(visual_input.input), build_default_visual_implementation_plan(visual_input.input)


def response(plan=None, **changes):
    values = {
        "status": "completed", "output_parsed": plan, "output": [],
        "usage": SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150,
                                  input_tokens_details=SimpleNamespace(cached_tokens=10),
                                  output_tokens_details=SimpleNamespace(reasoning_tokens=5)),
    }
    values.update(changes)
    return SimpleNamespace(**values)


class FakeResponses:
    def __init__(self, outcome): self.outcome = outcome; self.calls = []
    async def parse(self, **kwargs): self.calls.append(kwargs); return self.outcome


class FakeClient:
    def __init__(self, outcome): self.responses = FakeResponses(outcome)


def config(**changes):
    values = {"enabled": True, "provider": "openai", "api_key": "fake-key", "model": "gpt-5.6-sol"}
    values.update(changes)
    return VisualPlannerConfigV1(**values)


def test_config_is_disabled_by_default_and_strictly_namespaced():
    assert VisualPlannerConfigV1().enabled is False
    assert load_visual_planner_config_v1({}).enabled is False
    with pytest.raises(VisualPlannerProviderException):
        OpenAIVisualImplementationProviderV1(VisualPlannerConfigV1(), FakeClient(None))
    with pytest.raises(ValidationError):
        VisualPlannerConfigV1(enabled=True, provider="openai", api_key="x", model="gpt-6-astra")


@pytest.mark.parametrize(("name", "value"), [
    ("SITEFORMO_VISUAL_PLANNER_ENABLED", "yes"),
    ("SITEFORMO_VISUAL_PLANNER_TIMEOUT_SECONDS", "180.0"),
    ("SITEFORMO_VISUAL_PLANNER_MAX_OUTPUT_TOKENS", ""),
])
def test_environment_values_are_typed_fail_closed(name, value):
    with pytest.raises((ValueError, ValidationError)):
        load_visual_planner_config_v1({name: value})


def test_fake_business_plan_is_valid_and_request_has_structured_boundary():
    request, plan = visual_request()
    bad_hash_plan = plan.model_copy(update={"visual_plan_hash": "0" * 64})
    client = FakeClient(response(bad_hash_plan))
    result = asyncio.run(OpenAIVisualImplementationProviderV1(config(), client).create_visual_plan(request))
    assert result.status == "valid" and result.plan is not None
    assert result.plan.visual_plan_hash == plan.visual_plan_hash
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6-sol" and call["text_format"].__name__ == "VisualImplementationCandidateV1"
    assert "visual_plan_hash" not in call["text_format"].model_fields
    assert call["reasoning"] == {"effort": "medium"}
    assert call["tools"] == [] and call["store"] is False and call["stream"] is False
    assert call["truncation"] == "disabled" and "temperature" not in call and "top_p" not in call
    payload = json.loads(call["input"][0]["content"][0]["text"])
    assert payload["visual_implementation_input"]["structural_fingerprint"] == request.input.structural_fingerprint
    assert json.loads(call["instructions"]) == VISUAL_POLICY_V1
    assert result.usage.total_tokens == 150 and result.output_bytes is not None


@pytest.mark.parametrize("mutation", [
    lambda plan: plan.model_copy(update={"pages": plan.pages + (plan.pages[0],)}),
    lambda plan: plan.model_copy(update={"structural_fingerprint": "0" * 64}),
    lambda plan: plan.model_copy(update={"design_direction": "dark-contrast"}),
])
def test_hostile_or_architecture_mutation_is_rejected(mutation):
    request, plan = visual_request()
    result = asyncio.run(OpenAIVisualImplementationProviderV1(config(), FakeClient(response(mutation(plan)))).create_visual_plan(request))
    assert result.status == "manual_review" and result.plan is None
    assert result.error_category == "invalid_visual_plan"
    assert result.validation_reason_codes


def test_missing_reduced_motion_and_unsafe_critical_treatment_are_rejected():
    request, plan = visual_request()
    section = plan.pages[0].sections[0]
    unsafe = section.model_copy(update={"interaction_realization": "rich_noncritical"})
    candidate = plan.model_copy(update={"pages": (plan.pages[0].model_copy(update={"sections": (unsafe,) + plan.pages[0].sections[1:]}),) + plan.pages[1:]})
    result = asyncio.run(OpenAIVisualImplementationProviderV1(config(), FakeClient(response(candidate))).create_visual_plan(request))
    assert result.status == "manual_review" and "critical_action_safety_mismatch" in result.validation_reason_codes


def test_timeout_refusal_empty_and_malformed_are_sanitized():
    request, _ = visual_request()

    class Blocking:
        def __init__(self): self.responses = self
        async def parse(self, **kwargs): await asyncio.Event().wait()

    timeout = asyncio.run(OpenAIVisualImplementationProviderV1(config(), Blocking(), outer_timeout_seconds=0.01).create_visual_plan(request))
    assert timeout.status == "provider_failure" and timeout.error_category == "timeout"
    refusal = response(None, output=[SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="secret")])])
    refused = asyncio.run(OpenAIVisualImplementationProviderV1(config(), FakeClient(refusal)).create_visual_plan(request))
    assert refused.error_category == "refusal" and "secret" not in str(refused.model_dump())
    empty = asyncio.run(OpenAIVisualImplementationProviderV1(config(), FakeClient(response(None))).create_visual_plan(request))
    assert empty.error_category == "empty_response"
    malformed = asyncio.run(OpenAIVisualImplementationProviderV1(config(), FakeClient(response({"html": "<script>"}))).create_visual_plan(request))
    assert malformed.error_category == "malformed_response" and "<script>" not in str(malformed.model_dump())


def test_provider_factory_only_uses_dedicated_config_and_disables_retries():
    captured = {}
    def factory(**kwargs): captured.update(kwargs); return FakeClient(None)
    build_openai_visual_implementation_provider_v1(config(), factory)
    assert captured == {"api_key": "fake-key", "base_url": "https://api.openai.com/v1", "timeout": 180, "max_retries": 0}


def test_no_renderer_runtime_or_legacy_imports():
    tree = ast.parse(open("backend/app/services/generator_v2_visual_provider.py", encoding="utf-8").read())
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(token in " ".join(imports).lower() for token in ("redis", "worker", "queue", "sqlalchemy", "httpx"))
