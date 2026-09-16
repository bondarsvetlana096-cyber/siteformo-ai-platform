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
import secrets
import signal
import stat
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.visual_implementation.detached_runner import (
    WINDOWS_REPLACE_RETRIES, WINDOWS_REPLACE_RETRY_SECONDS, _atomic_json,
)


SERVICE_VERSION = "visual-qa-service-v1"
DEFAULT_QA_ROOT = Path(".stage-artifacts/visual-qa-service")
SERVICE_HEARTBEAT_SECONDS = 1.0
BUSINESS_WATCHDOG_SECONDS = 300
CONTROL_POLL_SECONDS = 0.25
AUTHORIZATION_FILENAME = "business_authorization.json"
MAX_AUTHORIZATION_BYTES = 16 * 1024
MAX_AUTHORIZATION_LIFETIME_SECONDS = 600

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
    control_ready: bool = False
    authorization_consumed: bool = False


class VisualQABusinessAuthorizationV1(BaseModel):
    """One-shot authority delivered only to an already-running QA container."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal["v1"] = "v1"
    action: Literal["business_e2e"] = "business_e2e"
    run_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    source_identity: str = Field(min_length=1, max_length=160)
    authorized_instance_id: str = Field(min_length=1, max_length=160)
    authorization_nonce: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_window(self) -> "VisualQABusinessAuthorizationV1":
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("authorization_timestamps_require_timezone")
        lifetime = (self.expires_at - self.created_at).total_seconds()
        if not 0 < lifetime <= MAX_AUTHORIZATION_LIFETIME_SECONDS:
            raise ValueError("invalid_authorization_lifetime")
        return self


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_path(root: Path) -> Path:
    return root / "qa_service_state.json"


def _run_state_path(root: Path, run_id: str) -> Path:
    return root / "runs" / run_id / "run_state.json"


def _default_control_directory() -> Path:
    return Path(tempfile.gettempdir()) / "siteformo-visual-qa-control"


def _control_directory(environment: Mapping[str, str]) -> Path:
    configured = environment.get("SITEFORMO_VISUAL_QA_CONTROL_DIR", "").strip()
    return Path(configured) if configured else _default_control_directory()


def _authorization_path(control_directory: Path) -> Path:
    return control_directory / AUTHORIZATION_FILENAME


def _prepare_control_directory(path: Path) -> None:
    if path.exists() and path.is_symlink():
        raise ValueError("control_directory_symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("control_path_not_directory")
    if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        raise ValueError("control_directory_wrong_owner")
    if os.name != "nt":
        os.chmod(path, 0o700)
    for pattern in (".consumed.*.json", ".discarded.*"):
        for stale in path.glob(pattern):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass


def _read_secure_authorization(path: Path) -> VisualQABusinessAuthorizationV1:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("authorization_symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("authorization_not_regular_file")
    if metadata.st_size > MAX_AUTHORIZATION_BYTES:
        raise ValueError("authorization_oversized")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("authorization_permissions")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError("authorization_file_changed")
        if opened.st_size > MAX_AUTHORIZATION_BYTES:
            raise ValueError("authorization_oversized")
        chunks = []
        remaining = MAX_AUTHORIZATION_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_AUTHORIZATION_BYTES:
        raise ValueError("authorization_oversized")
    return VisualQABusinessAuthorizationV1.model_validate_json(payload)


def write_business_authorization(
    *, control_directory: str | Path, run_id: str, source_identity: str,
    authorized_instance_id: str,
) -> Path:
    """Generate and atomically publish a one-shot authorization without exposing its nonce."""
    directory = Path(control_directory)
    _prepare_control_directory(directory)
    now = datetime.now(timezone.utc)
    authorization_nonce = secrets.token_hex(32)
    authorization = VisualQABusinessAuthorizationV1(
        run_id=run_id, source_identity=source_identity,
        authorized_instance_id=authorized_instance_id,
        authorization_nonce=authorization_nonce,
        created_at=now, expires_at=now + timedelta(minutes=5),
    )
    destination = _authorization_path(directory)
    temporary = directory / f".{AUTHORIZATION_FILENAME}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(authorization.model_dump_json())
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


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


def run_qa_service(
    *, environment: Mapping[str, str] | None = None, root: str | Path = DEFAULT_QA_ROOT,
    stop_event: threading.Event | None = None, fake_business_for_test: bool = False,
    heartbeat_seconds: float = SERVICE_HEARTBEAT_SECONDS,
) -> None:
    values = dict(os.environ if environment is None else environment)
    qa_root = Path(root)
    qa_root.mkdir(parents=True, exist_ok=True)
    control_directory = _control_directory(values)
    _prepare_control_directory(control_directory)
    action = values.get("SITEFORMO_VISUAL_QA_ACTION", "idle").strip() or "idle"
    run_id = values.get("SITEFORMO_VISUAL_QA_RUN_ID", "").strip() or None
    source = values.get("SITEFORMO_SOURCE_IDENTITY", SERVICE_VERSION).strip() or SERVICE_VERSION
    instance = (values.get("RAILWAY_REPLICA_ID") or values.get("SITEFORMO_VISUAL_QA_INSTANCE_ID")
                or f"local-{os.getpid()}").strip()
    now = _now()
    state = VisualQAServiceStateV1(
        source_identity=source, action=action, run_id=run_id, service_pid=os.getpid(),
        instance_identity=instance, started_at=now, updated_at=now, heartbeat_at=now,
        action_status="IDLE", control_ready=True,
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

    def execute_business(business_run_id: str, nonce_hash: str | None) -> None:
        persist(action="business_e2e", run_id=business_run_id, action_status="RUNNING",
                authorization_nonce_hash=nonce_hash)
        try:
            from evals.visual_implementation.e2e_orchestrator import run_business_visual_qa
            from evals.visual_implementation.eval_harness import VisualEvalConfigV1

            provider = _fake_provider_for_test() if fake_business_for_test else None
            result = asyncio.run(asyncio.wait_for(run_business_visual_qa(
                config=VisualEvalConfigV1(), allow_real_provider=not fake_business_for_test,
                environment=values, export_directory=qa_root / "runs" / business_run_id / "export",
                provider=provider, run_id=business_run_id, emit=lambda line: None,
            ), timeout=BUSINESS_WATCHDOG_SECONDS))
            finish(result.status, tuple(result.reason_codes), provider_call_count=result.provider_call_count,
                   visual_plan_hash=result.visual_plan_hash, rendered_site_hash=result.rendered_site_hash,
                   export_path_identity=result.export_path_identity)
        except TimeoutError:
            finish("TIMEOUT", ("service_business_watchdog",))
        except BaseException:
            finish("PROCESS_FAILURE", ("process_failure",))

    def discard_later_authorizations() -> None:
        candidate = _authorization_path(control_directory)
        while not stop.wait(CONTROL_POLL_SECONDS):
            if not os.path.lexists(candidate):
                continue
            discarded = control_directory / f".discarded.{uuid.uuid4().hex}"
            try:
                os.replace(candidate, discarded)
            except FileNotFoundError:
                continue
            except OSError:
                continue
            finally:
                if discarded.exists() or discarded.is_symlink():
                    discarded.unlink()

    def consume_idle_authorization() -> None:
        candidate = _authorization_path(control_directory)
        while not stop.wait(CONTROL_POLL_SECONDS):
            if not os.path.lexists(candidate):
                continue
            consumed = control_directory / f".consumed.{uuid.uuid4().hex}.json"
            try:
                os.replace(candidate, consumed)
            except FileNotFoundError:
                continue
            except OSError:
                finish("AUTHORIZATION_REFUSED", ("authorization_consume_failed",),
                       authorization_consumed=False)
                return
            try:
                authorization = _read_secure_authorization(consumed)
                current = datetime.now(timezone.utc)
                if authorization.created_at > current + timedelta(seconds=30) or authorization.expires_at < current:
                    raise ValueError("authorization_expired")
                if authorization.authorized_instance_id != instance:
                    raise ValueError("authorization_instance_mismatch")
                if authorization.source_identity != source:
                    raise ValueError("authorization_source_mismatch")
                if not fake_business_for_test and not (
                    _bool(values, "SITEFORMO_VISUAL_PLANNER_EVAL_ALLOW_REAL")
                    and _bool(values, "SITEFORMO_VISUAL_PLANNER_ENABLED")
                ):
                    raise ValueError("provider_switches_disabled")
                nonce_hash = hashlib.sha256(authorization.authorization_nonce.encode("utf-8")).hexdigest()
                if not _claim_run(qa_root, authorization.run_id, authorization.action, nonce_hash):
                    finish("DUPLICATE_RUN_ID", ("duplicate_run_id_no_resume",),
                           action="business_e2e", run_id=authorization.run_id,
                           authorization_consumed=True)
                    return
                authorized_run_id = authorization.run_id
                consumed.unlink()
                del authorization
                persist(action="business_e2e", run_id=authorized_run_id,
                        authorization_consumed=True, authorization_nonce_hash=nonce_hash)
                execute_business(authorized_run_id, nonce_hash)
                discard_later_authorizations()
                return
            except (ValueError, json.JSONDecodeError) as error:
                reason = str(error)
                if not reason.startswith("authorization_") and reason != "provider_switches_disabled":
                    reason = "authorization_malformed"
                finish("AUTHORIZATION_REFUSED", (reason,), authorization_consumed=True)
                return
            finally:
                if consumed.exists() or consumed.is_symlink():
                    consumed.unlink()

    def action_worker() -> None:
        if action == "idle":
            consume_idle_authorization()
            return
        if action not in {"lifecycle_probe", "business_e2e"}:
            finish("INVALID_ACTION", ("invalid_action",))
            return
        if run_id is None:
            finish("MISSING_RUN_ID", ("missing_run_id",))
            return
        if action == "business_e2e":
            if not fake_business_for_test:
                finish("AUTHORIZATION_REFUSED", ("control_file_authorization_required",))
                return
            if not _claim_run(qa_root, run_id, action, None):
                finish("DUPLICATE_RUN_ID", ("duplicate_run_id_no_resume",))
                return
            execute_business(run_id, None)
            return
        if not _claim_run(qa_root, run_id, action, None):
            finish("DUPLICATE_RUN_ID", ("duplicate_run_id_no_resume",))
            return
        persist(action_status="RUNNING")
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
    parser.add_argument("--authorize-business", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--source-identity")
    parser.add_argument("--authorized-instance-id")
    parser.add_argument("--control-directory")
    parser.add_argument("--fake-business-for-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--heartbeat-seconds", type=float, default=SERVICE_HEARTBEAT_SECONDS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.authorize_business:
        required = {
            "--run-id": args.run_id,
            "--source-identity": args.source_identity,
            "--authorized-instance-id": args.authorized_instance_id,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            parser.error(f"required with --authorize-business: {', '.join(missing)}")
        directory = Path(args.control_directory) if args.control_directory else _default_control_directory()
        write_business_authorization(
            control_directory=directory, run_id=args.run_id,
            source_identity=args.source_identity,
            authorized_instance_id=args.authorized_instance_id,
        )
        print(json.dumps({"status": "AUTHORIZATION_WRITTEN", "run_id": args.run_id}, sort_keys=True))
        return 0
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
