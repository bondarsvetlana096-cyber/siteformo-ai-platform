from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.generator_v2_visual import build_default_visual_implementation_plan
from app.services.generator_v2_visual_provider import VisualProviderResultV1, VisualProviderUsageV1
from evals.generator_v2.fixtures import build_generator_v2_fixture
from evals.visual_implementation import e2e_orchestrator as e2e
from evals.visual_implementation.eval_harness import VisualEvalConfigV1, build_business_visual_input


def valid_provider():
    visual_input = build_business_visual_input()
    plan = build_default_visual_implementation_plan(visual_input)
    return VisualProviderResultV1(
        status="valid", plan=plan, operation_key="a" * 64,
        structural_fingerprint=visual_input.structural_fingerprint,
        usage=VisualProviderUsageV1(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def test_fake_valid_business_exports_and_verifies(tmp_path):
    provider = SimpleNamespace(create_visual_plan=AsyncMock(return_value=valid_provider()))
    lines: list[str] = []
    result = asyncio.run(e2e.run_business_visual_qa(
        config=VisualEvalConfigV1(), allow_real_provider=False, environment={},
        export_directory=tmp_path / "business", provider=provider, run_id="run-valid", emit=lines.append,
    ))
    assert result.status == "VALID_RENDERED"
    assert result.export_status == "COMPLETE"
    assert provider.create_visual_plan.await_count == 1
    root = tmp_path / "business"
    assert (root / "EXPORT_COMPLETE").is_file()
    assert {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} >= {
        "result.json", "visual_plan.json", "artifact_manifest.json",
        "assets/siteformo-v2.css", "assets/siteformo-v2.js", "index.html",
    }
    assert e2e.verify_visual_qa_export(root).rendered_site_hash == result.rendered_site_hash
    transfer = e2e.build_visual_qa_transfer_manifest(root)
    assert transfer.files and len(transfer.package_hash) == 64
    assert sum(line.startswith("SITEFORMO_VISUAL_E2E_RESULT=") for line in lines) == 1


@pytest.mark.parametrize("status", ["manual_review", "provider_failure"])
def test_invalid_provider_never_renders_or_retains_plan(tmp_path, status):
    failed = valid_provider().model_copy(update={"status": status, "plan": None, "error_category": "malformed_response"})
    provider = SimpleNamespace(create_visual_plan=AsyncMock(return_value=failed))
    result = asyncio.run(e2e.run_business_visual_qa(
        config=VisualEvalConfigV1(), allow_real_provider=False, environment={},
        export_directory=tmp_path / "failed", provider=provider, emit=lambda _: None,
    ))
    assert result.status in {"MANUAL_REVIEW", "PROVIDER_FAILURE"}
    assert result.c4b_status == "NOT_RUN"
    assert not (tmp_path / "failed" / "visual_plan.json").exists()
    assert provider.create_visual_plan.await_count == 1


def test_unexpected_provider_exception_is_sanitized(tmp_path):
    provider = SimpleNamespace(create_visual_plan=AsyncMock(side_effect=RuntimeError("secret prompt")))
    lines: list[str] = []
    result = asyncio.run(e2e.run_business_visual_qa(
        config=VisualEvalConfigV1(), allow_real_provider=False, environment={},
        export_directory=tmp_path / "exception", provider=provider, emit=lines.append,
    ))
    assert result.status == "PROVIDER_FAILURE"
    assert result.reason_codes == ("unexpected_failure",)
    assert "secret prompt" not in "".join(lines)
    assert sum(line.startswith("SITEFORMO_VISUAL_E2E_RESULT=") for line in lines) == 1
    assert provider.create_visual_plan.await_count == 1


def test_timeout_is_terminal_and_not_rendered(tmp_path):
    async def slow(_request):
        await asyncio.sleep(2)

    provider = SimpleNamespace(create_visual_plan=slow)
    result = asyncio.run(e2e.run_business_visual_qa(
        config=VisualEvalConfigV1(max_eval_wall_seconds=1), allow_real_provider=False, environment={},
        export_directory=tmp_path / "timeout", provider=provider, emit=lambda _: None,
    ))
    assert result.status == "TIMEOUT"
    assert result.c4b_status == "NOT_RUN"
    assert not (tmp_path / "timeout").exists()


def test_configuration_failure_emits_terminal_result(tmp_path):
    lines: list[str] = []
    result = asyncio.run(e2e.run_business_visual_qa(
        config=VisualEvalConfigV1(), allow_real_provider=True, environment={},
        export_directory=tmp_path / "config", emit=lines.append,
    ))
    assert result.status == "CONFIGURATION_FAILURE"
    assert sum(line.startswith("SITEFORMO_VISUAL_E2E_RESULT=") for line in lines) == 1


def test_c4b_failure_does_not_export(monkeypatch, tmp_path):
    provider = SimpleNamespace(create_visual_plan=AsyncMock(return_value=valid_provider()))
    monkeypatch.setattr(e2e, "render_generator_v2_visual_site", lambda *_: SimpleNamespace(status="NOT_RENDERABLE", artifact=None))
    result = asyncio.run(e2e.run_business_visual_qa(
        config=VisualEvalConfigV1(), allow_real_provider=False, environment={},
        export_directory=tmp_path / "c4b", provider=provider, emit=lambda _: None,
    ))
    assert result.status == "C4B_INVALID"
    assert not (tmp_path / "c4b").exists()


def test_c3_invalid_does_not_render(monkeypatch, tmp_path):
    fake = valid_provider()
    invalid = e2e.VisualEvalResultV1(
        eval_run_id="c3-invalid", provider_status="valid", schema_parse_status="parsed",
        c3_validator_status="INVALID", call_count=1, operation_id="a" * 64,
    )
    async def fake_eval(**_kwargs):
        return invalid
    monkeypatch.setattr(e2e, "evaluate_business_visual_plan", fake_eval)
    render = lambda *_: (_ for _ in ()).throw(AssertionError("must not render"))
    monkeypatch.setattr(e2e, "render_generator_v2_visual_site", render)
    result = asyncio.run(e2e.run_business_visual_qa(
        config=VisualEvalConfigV1(), allow_real_provider=False, environment={},
        export_directory=tmp_path / "c3", provider=SimpleNamespace(), emit=lambda _: None,
    ))
    assert result.status == "C3_INVALID"


def test_cancellation_emits_safe_terminal_line(monkeypatch, tmp_path):
    async def cancelled(**_kwargs):
        raise asyncio.CancelledError
    monkeypatch.setattr(e2e, "evaluate_business_visual_plan", cancelled)
    lines: list[str] = []
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(e2e.run_business_visual_qa(
            config=VisualEvalConfigV1(), allow_real_provider=False, environment={},
            export_directory=tmp_path / "cancelled", emit=lines.append,
        ))
    assert sum(line.startswith("SITEFORMO_VISUAL_E2E_RESULT=") for line in lines) == 1


def test_export_write_failure_is_terminal(monkeypatch, tmp_path):
    provider = SimpleNamespace(create_visual_plan=AsyncMock(return_value=valid_provider()))
    monkeypatch.setattr(e2e, "_export_validated", lambda *_: (_ for _ in ()).throw(OSError("write failure")))
    result = asyncio.run(e2e.run_business_visual_qa(
        config=VisualEvalConfigV1(), allow_real_provider=False, environment={},
        export_directory=tmp_path / "write-failure", provider=provider, emit=lambda _: None,
    ))
    assert result.status == "EXPORT_FAILURE"
    assert result.export_status == "FAILED"
    assert provider.create_visual_plan.await_count == 1


@pytest.mark.parametrize("mutation", ["plan", "rendered", "manifest", "marker", "extra"])
def test_corrupt_export_fails_closed(tmp_path, mutation):
    provider = SimpleNamespace(create_visual_plan=AsyncMock(return_value=valid_provider()))
    root = tmp_path / "corrupt"
    result = asyncio.run(e2e.run_business_visual_qa(
        config=VisualEvalConfigV1(), allow_real_provider=False, environment={},
        export_directory=root, provider=provider, emit=lambda _: None,
    ))
    assert result.status == "VALID_RENDERED"
    if mutation == "plan":
        (root / "visual_plan.json").write_text("{}", encoding="utf-8")
    elif mutation == "rendered":
        (root / "index.html").write_text("changed", encoding="utf-8")
    elif mutation == "manifest":
        payload = json.loads((root / "artifact_manifest.json").read_text(encoding="utf-8"))
        payload["entries"][0]["sha256"] = "0" * 64
        (root / "artifact_manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "marker":
        (root / "EXPORT_COMPLETE").unlink()
    else:
        (root / "unexpected.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        e2e.verify_visual_qa_export(root)


def test_missing_plan_and_traversal_export_fail_closed(tmp_path):
    provider = SimpleNamespace(create_visual_plan=AsyncMock(return_value=valid_provider()))
    root = tmp_path / "integrity"
    result = asyncio.run(e2e.run_business_visual_qa(
        config=VisualEvalConfigV1(), allow_real_provider=False, environment={},
        export_directory=root, provider=provider, emit=lambda _: None,
    ))
    assert result.status == "VALID_RENDERED"
    (root / "visual_plan.json").unlink()
    with pytest.raises(ValueError):
        e2e.verify_visual_qa_export(root)


def test_orchestrator_is_not_runtime_wired():
    import ast
    source = (Path(__file__).parents[1] / "evals/visual_implementation/e2e_orchestrator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert not any(token in " ".join(imports).lower() for token in ("openai", "redis", "worker", "queue", "database"))
