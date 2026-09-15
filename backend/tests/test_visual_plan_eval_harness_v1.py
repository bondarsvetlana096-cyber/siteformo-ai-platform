from __future__ import annotations
import asyncio
from types import SimpleNamespace
import pytest
from app.services.generator_v2_visual import build_default_visual_implementation_plan
from app.services.generator_v2_visual_provider import VisualProviderResultV1, VisualProviderUsageV1
from evals.visual_implementation.eval_harness import VisualEvalConfigV1, build_business_visual_input, evaluate_business_visual_plan, preflight_visual_eval

class FakeProvider:
    def __init__(self, result): self.result = result; self.calls = 0
    async def create_visual_plan(self, request): self.calls += 1; return self.result

def fake_result(status="valid"):
    visual_input = build_business_visual_input(); plan = build_default_visual_implementation_plan(visual_input)
    return VisualProviderResultV1(status=status, plan=plan if status == "valid" else None, error_category=None if status == "valid" else "malformed_response", operation_key="a" * 64, structural_fingerprint=visual_input.structural_fingerprint, usage=VisualProviderUsageV1(input_tokens=100, cached_input_tokens=10, output_tokens=50, reasoning_tokens=5, total_tokens=150), output_bytes=1000)

def test_preflight_requires_both_switches_and_exact_settings():
    env = {"SITEFORMO_VISUAL_PLANNER_EVAL_ALLOW_REAL":"true","SITEFORMO_VISUAL_PLANNER_ENABLED":"true","SITEFORMO_VISUAL_PLANNER_PROVIDER":"openai","SITEFORMO_VISUAL_PLANNER_MODEL":"gpt-5.6-sol","SITEFORMO_VISUAL_PLANNER_REASONING_EFFORT":"medium","SITEFORMO_VISUAL_PLANNER_TIMEOUT_SECONDS":"180","SITEFORMO_VISUAL_PLANNER_MAX_OUTPUT_TOKENS":"16384","SITEFORMO_VISUAL_PLANNER_API_KEY":"fake"}
    assert preflight_visual_eval(VisualEvalConfigV1(), env).status == "BLOCKED"
    assert preflight_visual_eval(VisualEvalConfigV1(allow_real_provider=True), env).status == "READY"

def test_fake_valid_business_emits_safe_result_and_one_call():
    provider = FakeProvider(fake_result()); lines=[]
    result = asyncio.run(evaluate_business_visual_plan(config=VisualEvalConfigV1(), allow_real_provider=False, environment={}, provider=provider, emit=lines.append))
    assert result.provider_status == "valid" and result.c3_validator_status == "VALID" and result.call_count == 1 and provider.calls == 1
    assert lines[0].startswith("SITEFORMO_VISUAL_EVAL_PREFLIGHT=") and lines[1].startswith("SITEFORMO_VISUAL_EVAL_RESULT=")

@pytest.mark.parametrize("status", ["manual_review", "provider_failure"])
def test_failure_never_retries(status):
    provider=FakeProvider(fake_result(status)); result=asyncio.run(evaluate_business_visual_plan(config=VisualEvalConfigV1(), allow_real_provider=False, environment={}, provider=provider, emit=lambda _: None))
    assert result.provider_status == status and provider.calls == 1

def test_spend_guard_refuses_before_provider_call():
    provider = FakeProvider(fake_result())
    result = asyncio.run(evaluate_business_visual_plan(config=VisualEvalConfigV1(max_estimated_spend_usd=0.5), allow_real_provider=False, environment={}, provider=provider, emit=lambda _: None))
    assert result.provider_status == "budget_blocked" and result.call_count == 0 and provider.calls == 0

def test_privacy_and_no_runtime_imports():
    text=open("backend/evals/visual_implementation/eval_harness.py", encoding="utf-8").read()
    assert "raw_questionnaire" not in text
    assert "from app.services.generator_v2_visual_provider" in text
