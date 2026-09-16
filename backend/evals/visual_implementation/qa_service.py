"""QA-only long-lived control process for Railway visual evaluation sandboxes.

This module is intentionally not imported by application startup, routes, or
workers.  It opens no listener and owns only local QA state and artifacts.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evals.visual_implementation.detached_runner import (
    WINDOWS_REPLACE_RETRIES, WINDOWS_REPLACE_RETRY_SECONDS, _atomic_json,
)


SERVICE_VERSION = "visual-qa-service-v1"
DEFAULT_QA_ROOT = Path(".stage-artifacts/visual-qa-service")
SERVICE_HEARTBEAT_SECONDS = 1.0
BUSINESS_WATCHDOG_SECONDS = 300

QAAction = Literal["idle", "lifecycle_probe", "business_e2e"]
ActionStatus = Literal[
    "IDLE", "RUNNING", "LIFECYCLE_COMPLETE", "VALID_RENDERED", "MANUAL_REVIEW",
    "PROVIDER_FAILURE", "TIMEOUT", "CANCELLED", "C3_INVALID", "C4B_INVALID",
    "EXPORT_FAILURE", "INTERRUPTED", "CONFIGURATION_FAILURE", "INVALID_ACTION",
    "MISSING_RUN_ID", "DUPLICATE_RUN_ID", "AUTHORIZATION_REFUSED", "PROCESS_FAILURE",
]


class VisualQAServiceStateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal["v1"] = "v1"
    service_version: Literal["visual-qa-service-v1"] = SERVICE_VERSION
    source_identity: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=40)
    run_id: str | None = Field(default=None, max_length=80)
    service_pid: int = Field(ge=1)
    instance_identity: str = Field(min_length=1, max_length=160)
    started_at: str
    updated_at: str
    heartbeat_at: str
    action_status: ActionStatus
    completed_at: str | None = None
    reason_codes: tuple[str, ...] = ()
    provider_call_count: int = Field(default=0, ge=0, le=1)
    visual_plan_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rendered_site_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    export_path_identity: str | None = Field(default=None, max_length=240)
    authorization_nonce_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_path(root: Path) -> Path:
    return root / "qa_service_state.json"


def _run_state_path(root: Path, run_id: str) -> Path:
    return root / "runs" / run_id / "run_state.json"


def read_qa_service_state(root: str | Path = DEFAULT_QA_ROOT) -> VisualQAServiceStateV1:
    path = _state_path(Path(root))
    for attempt in range(WINDOWS_REPLACE_RETRIES + 1):
        try:
            return VisualQAServiceStateV1.model_validate_json(path.read_text(encoding="utf-8"))
        except PermissionError:
            if os.name != "nt" or attempt == WINDOWS_REPLACE_RETRIES:
                raise
            time.sleep(WINDOWS_REPLACE_RETRY_SECONDS)
    raise RuntimeError("unreachable")


def _bool(environment: Mapping[str, str], name: str) -> bool:
    return environment.get(name, "").strip().lower() == "true"


def _bounded_seconds(environment: Mapping[str, str]) -> int:
    raw = environment.get("SITEFORMO_VISUAL_QA_LIFECYCLE_SECONDS", "190").strip()
    if not raw.isdecimal() or not 1 <= int(raw) <= 600:
        raise ValueError("invalid_lifecycle_seconds")
    return int(raw)


def _claim_run(root: Path, run_id: str, action: str, nonce_hash: str | None) -> bool:
    claims = root / "claims"
    claims.mkdir(parents=True, exist_ok=True)
    path = claims / f"{run_id}.json"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"action": action, "run_id": run_id, "authorization_nonce_hash": nonce_hash}, handle,
                  sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _fake_provider_for_test():
    from app.services.generator_v2_visual import build_default_visual_implementation_plan
    from app.services.generator_v2_visual_provider import VisualProviderResultV1, VisualProviderUsageV1
    from evals.visual_implementation.eval_harness import build_business_visual_input

    plan = build_default_visual_implementation_plan(build_business_visual_input())

    class FakeProvider:
        async def create_visual_plan(self, _request: object) -> VisualProviderResultV1:
            return VisualProviderResultV1(
                status="valid", plan=plan, operation_key="e" * 64,
                structural_fingerprint=plan.structural_fingerprint, latency_ms=0,
                output_bytes=len(json.dumps(plan.model_dump(mode="json"), sort_keys=True).encode("utf-8")),
                usage=VisualProviderUsageV1(input_tokens=0, cached_input_tokens=0, output_tokens=0,
                                            reasoning_tokens=0, total_tokens=0),
            )

    return FakeProvider()


def _real_business_authorized(environment: Mapping[str, str], instance_identity: str) -> tuple[bool, str | None]:
    nonce = environment.get("SITEFORMO_VISUAL_QA_AUTHORIZATION_NONCE", "").strip()
    authorized_instance = environment.get("SITEFORMO_VISUAL_QA_AUTHORIZED_INSTANCE_ID", "").strip()
    allowed = (
        bool(nonce) and authorized_instance == instance_identity
        and _bool(environment, "SITEFORMO_VISUAL_PLANNER_EVAL_ALLOW_REAL")
        and _bool(environment, "SITEFORMO_VISUAL_PLANNER_ENABLED")
    )
    return allowed, hashlib.sha256(nonce.encode("utf-8")).hexdigest() if nonce else None


def run_qa_service(
    *, environment: Mapping[str, str] | None = None, root: str | Path = DEFAULT_QA_ROOT,
    stop_event: threading.Event | None = None, fake_business_for_test: bool = False,
    heartbeat_seconds: float = SERVICE_HEARTBEAT_SECONDS,
) -> None:
    values = dict(os.environ if environment is None else environment)
    qa_root = Path(root)
    qa_root.mkdir(parents=True, exist_ok=True)
    action = values.get("SITEFORMO_VISUAL_QA_ACTION", "idle").strip() or "idle"
    run_id = values.get("SITEFORMO_VISUAL_QA_RUN_ID", "").strip() or None
    source = values.get("SITEFORMO_SOURCE_IDENTITY", SERVICE_VERSION).strip() or SERVICE_VERSION
    instance = (values.get("RAILWAY_REPLICA_ID") or values.get("SITEFORMO_VISUAL_QA_INSTANCE_ID")
                or f"local-{os.getpid()}").strip()
    now = _now()
    state = VisualQAServiceStateV1(
        source_identity=source, action=action, run_id=run_id, service_pid=os.getpid(),
        instance_identity=instance, started_at=now, updated_at=now, heartbeat_at=now,
        action_status="IDLE",
    )
    lock = threading.Lock()
    stop = stop_event or threading.Event()

    def persist(**updates: object) -> None:
        nonlocal state
        with lock:
            stamp = _now()
            state = state.model_copy(update={"updated_at": stamp, **updates})
            _atomic_json(_state_path(qa_root), state)
            if state.run_id is not None:
                _atomic_json(_run_state_path(qa_root, state.run_id), state)

    def finish(status: ActionStatus, reason_codes: tuple[str, ...] = (), **updates: object) -> None:
        persist(action_status=status, completed_at=_now(), reason_codes=reason_codes, **updates)

    def action_worker() -> None:
        if action == "idle":
            return
        if action not in {"lifecycle_probe", "business_e2e"}:
            finish("INVALID_ACTION", ("invalid_action",))
            return
        if run_id is None:
            finish("MISSING_RUN_ID", ("missing_run_id",))
            return
        nonce_hash = None
        if action == "business_e2e" and not fake_business_for_test:
            authorized, nonce_hash = _real_business_authorized(values, instance)
            if not authorized:
                finish("AUTHORIZATION_REFUSED", ("business_authorization_required",))
                return
        if not _claim_run(qa_root, run_id, action, nonce_hash):
            finish("DUPLICATE_RUN_ID", ("duplicate_run_id_no_resume",))
            return
        persist(action_status="RUNNING", authorization_nonce_hash=nonce_hash)
        if action == "lifecycle_probe":
            try:
                deadline = time.monotonic() + _bounded_seconds(values)
                while time.monotonic() < deadline and not stop.is_set():
                    stop.wait(min(0.1, max(0.0, deadline - time.monotonic())))
                if stop.is_set():
                    finish("INTERRUPTED", ("service_stopped",))
                else:
                    finish("LIFECYCLE_COMPLETE")
            except ValueError as error:
                finish("CONFIGURATION_FAILURE", (str(error),))
            return
        try:
            from evals.visual_implementation.e2e_orchestrator import run_business_visual_qa
            from evals.visual_implementation.eval_harness import VisualEvalConfigV1

            provider = _fake_provider_for_test() if fake_business_for_test else None
            result = asyncio.run(asyncio.wait_for(run_business_visual_qa(
                config=VisualEvalConfigV1(), allow_real_provider=not fake_business_for_test,
                environment=values, export_directory=qa_root / "runs" / run_id / "export",
                provider=provider, run_id=run_id, emit=lambda line: None,
            ), timeout=BUSINESS_WATCHDOG_SECONDS))
            finish(result.status, tuple(result.reason_codes), provider_call_count=result.provider_call_count,
                   visual_plan_hash=result.visual_plan_hash, rendered_site_hash=result.rendered_site_hash,
                   export_path_identity=result.export_path_identity)
        except TimeoutError:
            finish("TIMEOUT", ("service_business_watchdog",))
        except BaseException:
            finish("PROCESS_FAILURE", ("process_failure",))

    _atomic_json(_state_path(qa_root), state)
    worker = threading.Thread(target=action_worker, name="visual-qa-action", daemon=True)
    worker.start()
    while not stop.wait(heartbeat_seconds):
        persist(heartbeat_at=_now())
    worker.join(timeout=2.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QA-only long-lived Visual QA service")
    parser.add_argument("--root", default=str(DEFAULT_QA_ROOT))
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--fake-business-for-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--heartbeat-seconds", type=float, default=SERVICE_HEARTBEAT_SECONDS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.status:
        print(read_qa_service_state(args.root).model_dump_json())
        return 0
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run_qa_service(root=args.root, stop_event=stop, fake_business_for_test=args.fake_business_for_test,
                   heartbeat_seconds=args.heartbeat_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
