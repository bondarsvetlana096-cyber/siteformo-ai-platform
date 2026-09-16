"""QA-only C4 end-to-end visual evaluation orchestration and export.

This module is intentionally disconnected from application startup, routes,
workers, persistence, and production storage.  It joins the existing visual
evaluation harness to the pure C4B renderer for explicit synthetic QA runs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.generator_v2_visual import VisualImplementationPlanV1, derive_visual_plan_hash
from app.services.generator_v2_visual_renderer import render_generator_v2_visual_site
from evals.visual_implementation.eval_harness import (
    VisualEvalConfigV1,
    VisualEvalResultV1,
    build_business_visual_input,
    evaluate_business_visual_plan,
)
from evals.generator_v2.fixtures import build_generator_v2_fixture


E2E_MAX_DIAGNOSTIC_BYTES = 16_384
E2E_EXPORT_CONTRACT_VERSION = "v1"

VisualEndToEndQAStatus = Literal[
    "VALID_RENDERED",
    "MANUAL_REVIEW",
    "PROVIDER_FAILURE",
    "TIMEOUT",
    "CANCELLED",
    "C3_INVALID",
    "C4B_INVALID",
    "EXPORT_FAILURE",
    "INTERRUPTED",
    "CONFIGURATION_FAILURE",
]

VisualEndToEndReason = Literal[
    "valid_rendered",
    "manual_review",
    "provider_failure",
    "timeout",
    "cancelled",
    "c3_invalid",
    "c4b_invalid",
    "export_failure",
    "interrupted",
    "configuration_failure",
    "missing_visual_plan",
    "missing_artifact",
    "export_verification_failed",
    "unexpected_failure",
]


class _ClosedFrozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VisualEndToEndQAResultV1(_ClosedFrozen):
    eval_contract_version: Literal["v1"] = "v1"
    run_id: str = Field(min_length=1, max_length=80)
    case_id: Literal["BUSINESS_THREE_PAGE"] = "BUSINESS_THREE_PAGE"
    model: Literal["gpt-5.6-sol"] = "gpt-5.6-sol"
    reasoning_effort: Literal["medium"] = "medium"
    status: VisualEndToEndQAStatus
    provider_status: Literal["valid", "manual_review", "provider_failure", "not_run"]
    c3_status: Literal["VALID", "INVALID", "NOT_RUN"]
    c4b_status: Literal["READY", "NOT_RENDERABLE", "NOT_RUN"]
    reason_codes: tuple[VisualEndToEndReason, ...] = ()
    provider_call_count: int = Field(ge=0, le=1)
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    visual_plan_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rendered_site_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    export_status: Literal["NOT_REQUESTED", "INCOMPLETE", "COMPLETE", "FAILED"]
    export_path_identity: str | None = Field(default=None, max_length=240)


class VisualQAArtifactEntryV1(_ClosedFrozen):
    relative_path: str = Field(min_length=1, max_length=240)
    media_type: str = Field(min_length=1, max_length=80)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0)
    owner: str = Field(min_length=1, max_length=120)
    source_classification: str = Field(min_length=1, max_length=40)


class VisualQAArtifactManifestV1(_ClosedFrozen):
    contract_version: Literal["v1"] = "v1"
    visual_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rendered_site_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[VisualQAArtifactEntryV1, ...]
    artifact_identity: dict[str, object]


class VisualQATransferManifestV1(_ClosedFrozen):
    contract_version: Literal["v1"] = "v1"
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: tuple[VisualQAArtifactEntryV1, ...]


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_export_path(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise ValueError("export path symlink")
    return path


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _artifact_identity(artifact: object, visual_plan_hash: str | None = None) -> dict[str, object]:
    # Keep identity material separate from file contents while retaining all
    # fields needed by the deterministic artifact hash.
    pages = getattr(artifact, "pages")
    files = getattr(artifact, "artifact_manifest")
    return {
        "contract_version": artifact.contract_version,
        "generator_input_hash": artifact.generator_input_hash,
        "implementation_spec_hash": artifact.implementation_spec_hash,
        "implementation_operation_key": artifact.implementation_operation_key,
        "renderer_version": artifact.renderer_version,
        "visual_plan_hash": visual_plan_hash,
        "pages": [page.model_dump(mode="json") for page in pages],
        "files": [
            {key: value for key, value in entry.model_dump(mode="json").items() if key != "content"}
            for entry in files
        ],
        "routes": [list(route) for route in artifact.route_manifest],
    }


def _safe_entries(artifact: object) -> tuple[VisualQAArtifactEntryV1, ...]:
    entries: list[VisualQAArtifactEntryV1] = []
    for entry in artifact.artifact_manifest:
        relative = str(entry.relative_path)
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or candidate.name in {"", ".", ".."}:
            raise ValueError("unsafe artifact path")
        if "\\" in relative:
            raise ValueError("unsafe artifact path")
        entries.append(VisualQAArtifactEntryV1(
            relative_path=relative,
            media_type=entry.media_type,
            sha256=entry.sha256,
            byte_length=entry.byte_length,
            owner=entry.owner,
            source_classification=entry.source_classification,
        ))
    return tuple(entries)


def _result_from_eval(run_id: str, result: VisualEvalResultV1, *, status: VisualEndToEndQAStatus,
                      reason: VisualEndToEndReason, c4b_status: Literal["READY", "NOT_RENDERABLE", "NOT_RUN"],
                      export_status: Literal["NOT_REQUESTED", "INCOMPLETE", "COMPLETE", "FAILED"],
                      export_path: str | None = None, rendered_hash: str | None = None) -> VisualEndToEndQAResultV1:
    reasons: tuple[VisualEndToEndReason, ...] = () if status == "VALID_RENDERED" else (reason,)
    return VisualEndToEndQAResultV1(
        run_id=run_id,
        status=status,
        provider_status=result.provider_status,
        c3_status=result.c3_validator_status,
        c4b_status=c4b_status,
        reason_codes=reasons,
        provider_call_count=result.call_count,
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        cached_input_tokens=result.cached_input_tokens,
        output_tokens=result.output_tokens,
        reasoning_tokens=result.reasoning_tokens,
        total_tokens=result.total_tokens,
        estimated_cost_usd=result.estimated_cost_usd,
        visual_plan_hash=result.visual_plan_hash,
        rendered_site_hash=rendered_hash,
        export_status=export_status,
        export_path_identity=export_path,
    )


def _emit_result(result: VisualEndToEndQAResultV1, emit: Callable[[str], None]) -> None:
    payload = _canonical(result.model_dump(mode="json"))
    if len(payload.encode("utf-8")) > E2E_MAX_DIAGNOSTIC_BYTES:
        payload = _canonical({"status": "diagnostic_overflow"})
    emit("SITEFORMO_VISUAL_E2E_RESULT=" + payload)


def _map_eval_failure(run_id: str, result: VisualEvalResultV1) -> VisualEndToEndQAResultV1:
    if result.provider_status == "manual_review":
        return _result_from_eval(run_id, result, status="MANUAL_REVIEW", reason="manual_review", c4b_status="NOT_RUN", export_status="NOT_REQUESTED")
    if result.provider_status == "provider_failure":
        timeout = result.operation_id == "0" * 64
        return _result_from_eval(run_id, result, status="TIMEOUT" if timeout else "PROVIDER_FAILURE",
                                 reason="timeout" if timeout else "provider_failure", c4b_status="NOT_RUN", export_status="NOT_REQUESTED")
    if result.c3_validator_status != "VALID":
        return _result_from_eval(run_id, result, status="C3_INVALID", reason="c3_invalid", c4b_status="NOT_RUN", export_status="NOT_REQUESTED")
    return _result_from_eval(run_id, result, status="PROVIDER_FAILURE", reason="provider_failure", c4b_status="NOT_RUN", export_status="NOT_REQUESTED")


def _export_validated(
    export_directory: Path,
    result: VisualEndToEndQAResultV1,
    plan: object,
    artifact: object,
) -> None:
    target = _safe_export_path(export_directory)
    if target.exists():
        raise FileExistsError("export destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.staging-{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        _atomic_write(staging / "result.json", _canonical(result.model_dump(mode="json")))
        _atomic_write(staging / "visual_plan.json", _canonical(plan.model_dump(mode="json")))
        entries = _safe_entries(artifact)
        for entry, source in zip(entries, artifact.artifact_manifest):
            path = staging / entry.relative_path
            resolved = path.resolve()
            if staging.resolve() not in resolved.parents or path.exists() and path.is_symlink():
                raise ValueError("unsafe export target")
            _atomic_write(path, source.content)
        manifest = VisualQAArtifactManifestV1(
            visual_plan_hash=plan.visual_plan_hash,
            rendered_site_hash=artifact.rendered_site_hash,
            entries=entries,
            artifact_identity=_artifact_identity(artifact, plan.visual_plan_hash),
        )
        _atomic_write(staging / "artifact_manifest.json", _canonical(manifest.model_dump(mode="json")))
        final_result = result.model_copy(update={"export_status": "COMPLETE", "export_path_identity": target.name})
        _atomic_write(staging / "result.json", _canonical(final_result.model_dump(mode="json")))
        _atomic_write(staging / "EXPORT_COMPLETE", "v1\n")
        os.replace(staging, target)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_visual_qa_export(export_directory: str | Path) -> VisualEndToEndQAResultV1:
    """Read-back verifier for a completed, local QA export."""
    root = Path(export_directory)
    if not root.is_dir() or root.is_symlink() or not (root / "EXPORT_COMPLETE").is_file():
        raise ValueError("incomplete export")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("symlink export member")
    allowed = {"EXPORT_COMPLETE", "result.json", "visual_plan.json", "artifact_manifest.json"}
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    for relative in actual:
        if relative in allowed:
            continue
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or candidate.name in {"", "."}:
            raise ValueError("unsafe export member")
    if not {"result.json", "visual_plan.json", "artifact_manifest.json"}.issubset(actual):
        raise ValueError("missing export file")
    result = VisualEndToEndQAResultV1.model_validate(json.loads((root / "result.json").read_text(encoding="utf-8")))
    if result.status != "VALID_RENDERED" or result.export_status != "COMPLETE":
        raise ValueError("export result is not complete")
    plan = VisualImplementationPlanV1.model_validate(json.loads((root / "visual_plan.json").read_text(encoding="utf-8")))
    if plan.visual_plan_hash != result.visual_plan_hash or derive_visual_plan_hash(plan) != plan.visual_plan_hash:
        raise ValueError("visual plan identity mismatch")
    manifest = VisualQAArtifactManifestV1.model_validate(json.loads((root / "artifact_manifest.json").read_text(encoding="utf-8")))
    if manifest.visual_plan_hash != result.visual_plan_hash or manifest.rendered_site_hash != result.rendered_site_hash:
        raise ValueError("manifest identity mismatch")
    for entry in manifest.entries:
        relative = entry.relative_path
        path = root / relative
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != entry.sha256 or path.stat().st_size != entry.byte_length:
            raise ValueError("artifact file mismatch")
    if _sha(manifest.artifact_identity) != manifest.rendered_site_hash:
        raise ValueError("rendered site hash mismatch")
    expected_members = allowed | {entry.relative_path for entry in manifest.entries}
    if actual != expected_members:
        raise ValueError("unexpected export member")
    return result


def build_visual_qa_transfer_manifest(export_directory: str | Path) -> VisualQATransferManifestV1:
    """Return a safe hash manifest for an explicit future SSH/download step."""
    root = Path(export_directory)
    verify_visual_qa_export(root)
    manifest = VisualQAArtifactManifestV1.model_validate(json.loads((root / "artifact_manifest.json").read_text(encoding="utf-8")))
    files = tuple(sorted(manifest.entries, key=lambda entry: entry.relative_path))
    package_hash = _sha([entry.model_dump(mode="json") for entry in files])
    return VisualQATransferManifestV1(package_hash=package_hash, files=files)


async def run_business_visual_qa(
    *,
    config: VisualEvalConfigV1,
    allow_real_provider: bool,
    environment: Mapping[str, str],
    export_directory: str | Path,
    provider: object | None = None,
    run_id: str | None = None,
    emit: Callable[[str], None] = print,
) -> VisualEndToEndQAResultV1:
    """Run one explicit synthetic Business visual evaluation and export."""
    run = run_id or uuid.uuid4().hex
    eval_result: VisualEvalResultV1 | None = None
    emitted = False
    try:
        fixture = build_generator_v2_fixture("BUSINESS_THREE_PAGE")
        visual_input = build_business_visual_input()
        eval_result = await evaluate_business_visual_plan(
            config=config,
            allow_real_provider=allow_real_provider,
            environment=environment,
            provider=provider,
            run_id=run,
            emit=emit,
        )
        if eval_result.provider_status != "valid" or eval_result.plan is None:
            final = _map_eval_failure(run, eval_result)
            _emit_result(final, emit)
            emitted = True
            return final
        if eval_result.c3_validator_status != "VALID":
            final = _result_from_eval(run, eval_result, status="C3_INVALID", reason="c3_invalid", c4b_status="NOT_RUN", export_status="NOT_REQUESTED")
            _emit_result(final, emit)
            emitted = True
            return final
        rendered = render_generator_v2_visual_site(fixture.implementation, visual_input, eval_result.plan)
        if rendered.status != "READY" or rendered.artifact is None:
            final = _result_from_eval(run, eval_result, status="C4B_INVALID", reason="c4b_invalid", c4b_status="NOT_RENDERABLE", export_status="NOT_REQUESTED")
            _emit_result(final, emit)
            emitted = True
            return final
        provisional = _result_from_eval(run, eval_result, status="VALID_RENDERED", reason="valid_rendered", c4b_status="READY", export_status="INCOMPLETE", rendered_hash=rendered.artifact.rendered_site_hash)
        try:
            _export_validated(Path(export_directory), provisional, eval_result.plan, rendered.artifact)
            final = provisional.model_copy(update={"export_status": "COMPLETE", "export_path_identity": Path(export_directory).name})
        except Exception:
            final = provisional.model_copy(update={"status": "EXPORT_FAILURE", "reason_codes": ("export_failure",), "export_status": "FAILED"})
        _emit_result(final, emit)
        emitted = True
        return final
    except asyncio.CancelledError:
        final = VisualEndToEndQAResultV1(run_id=run, status="CANCELLED", provider_status=eval_result.provider_status if eval_result else "not_run", c3_status=eval_result.c3_validator_status if eval_result else "NOT_RUN", c4b_status="NOT_RUN", reason_codes=("cancelled",), provider_call_count=eval_result.call_count if eval_result else 0, export_status="FAILED")
        _emit_result(final, emit)
        raise
    except PermissionError:
        final = VisualEndToEndQAResultV1(run_id=run, status="CONFIGURATION_FAILURE", provider_status=eval_result.provider_status if eval_result else "not_run", c3_status=eval_result.c3_validator_status if eval_result else "NOT_RUN", c4b_status="NOT_RUN", reason_codes=("configuration_failure",), provider_call_count=eval_result.call_count if eval_result else 0, export_status="NOT_REQUESTED")
        _emit_result(final, emit)
        return final
    except (KeyboardInterrupt, SystemExit):
        final = VisualEndToEndQAResultV1(run_id=run, status="INTERRUPTED", provider_status=eval_result.provider_status if eval_result else "not_run", c3_status=eval_result.c3_validator_status if eval_result else "NOT_RUN", c4b_status="NOT_RUN", reason_codes=("interrupted",), provider_call_count=eval_result.call_count if eval_result else 0, export_status="FAILED")
        _emit_result(final, emit)
        raise
    except Exception:
        final = VisualEndToEndQAResultV1(run_id=run, status="PROVIDER_FAILURE", provider_status=eval_result.provider_status if eval_result else "not_run", c3_status=eval_result.c3_validator_status if eval_result else "NOT_RUN", c4b_status="NOT_RUN", reason_codes=("unexpected_failure",), provider_call_count=eval_result.call_count if eval_result else 0, export_status="NOT_REQUESTED")
        _emit_result(final, emit)
        return final
