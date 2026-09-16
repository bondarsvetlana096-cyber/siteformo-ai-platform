"""Detached, QA-only runner for Visual E2E evaluations.

The launcher returns immediately after spawning a new process.  State is the
control-plane contract; stdout is deliberately only a secondary diagnostic.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RUNNER_VERSION = "visual-e2e-runner-v1"
DEFAULT_RUN_ROOT = Path(".stage-artifacts/visual-e2e-runs")
DETACHED_WATCHDOG_SECONDS = 300
STALE_AFTER_SECONDS = 330
HEARTBEAT_INTERVAL_SECONDS = 1.0
WINDOWS_REPLACE_RETRIES = 10
WINDOWS_REPLACE_RETRY_SECONDS = 0.01

RunStatus = Literal[
    "CREATED", "STARTING", "RUNNING", "VALID_RENDERED", "MANUAL_REVIEW",
    "PROVIDER_FAILURE", "TIMEOUT", "CANCELLED", "C3_INVALID", "C4B_INVALID",
    "EXPORT_FAILURE", "INTERRUPTED", "CONFIGURATION_FAILURE", "PROCESS_FAILURE",
    "LIFECYCLE_COMPLETE", "STALE_PROCESS",
]
_TERMINAL_STATUSES = {"VALID_RENDERED", "MANUAL_REVIEW", "PROVIDER_FAILURE", "TIMEOUT", "CANCELLED", "C3_INVALID", "C4B_INVALID", "EXPORT_FAILURE", "INTERRUPTED", "CONFIGURATION_FAILURE", "PROCESS_FAILURE", "LIFECYCLE_COMPLETE", "STALE_PROCESS"}


class VisualE2ERunStateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal["v1"] = "v1"
    runner_version: Literal["visual-e2e-runner-v1"] = RUNNER_VERSION
    run_id: str = Field(min_length=1, max_length=80)
    case_id: Literal["BUSINESS_THREE_PAGE"] = "BUSINESS_THREE_PAGE"
    source_identity: str = Field(min_length=1, max_length=160)
    status: RunStatus
    started_at: str | None = None
    updated_at: str
    completed_at: str | None = None
    heartbeat_at: str | None = None
    provider_call_count: int = Field(default=0, ge=0, le=1)
    reason_codes: tuple[str, ...] = ()
    visual_plan_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rendered_site_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    export_path_identity: str | None = Field(default=None, max_length=240)
    process_id: int | None = Field(default=None, ge=1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, value: BaseModel | dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(WINDOWS_REPLACE_RETRIES + 1):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == WINDOWS_REPLACE_RETRIES:
                    raise
                time.sleep(WINDOWS_REPLACE_RETRY_SECONDS)
    finally:
        if temporary.exists():
            temporary.unlink()


def _state_path(root: Path, run_id: str) -> Path:
    return root / run_id / "run_state.json"


def _write_state(path: Path, state: VisualE2ERunStateV1, **updates: object) -> VisualE2ERunStateV1:
    next_state = state.model_copy(update={"updated_at": _now(), **updates})
    _atomic_json(path, next_state)
    return next_state


def _write_failure_if_nonterminal(
    path: Path, *, status: Literal["TIMEOUT", "PROCESS_FAILURE"], reason_code: str,
) -> VisualE2ERunStateV1:
    """Persist a child failure without downgrading an already terminal state."""
    state = VisualE2ERunStateV1.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if state.status in _TERMINAL_STATUSES:
        return state
    return _write_state(
        path, state, status=status, reason_codes=(reason_code,), completed_at=_now(),
    )


def _is_windows_process_alive(pid: int) -> bool:
    """Query process state through Win32 without signalling the process."""
    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == error_access_denied:
            return True
        if error == error_invalid_parameter:
            return False
        return False
    try:
        exit_code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _is_process_alive(pid: int) -> bool:
    """Return whether *pid* exists without changing or signalling its state."""
    if pid < 1:
        return False
    if os.name == "nt":
        return _is_windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_visual_e2e_run_state(run_id: str, run_root: str | Path = DEFAULT_RUN_ROOT) -> VisualE2ERunStateV1:
    """Read a run state; classify a dead RUNNING process as stale.

    Heartbeat age is diagnostic while the recorded PID is alive.  This avoids
    declaring a healthy, long provider operation stale due to delayed writes.
    """
    path = _state_path(Path(run_root), run_id)
    state = VisualE2ERunStateV1.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if state.status == "RUNNING":
        alive = state.process_id is not None and _is_process_alive(state.process_id)
        if not alive:
            stale = state.model_copy(update={"status": "STALE_PROCESS", "reason_codes": ("stale_process",), "updated_at": _now()})
            _atomic_json(path, stale)
            return stale
    return state


def launch_visual_e2e_run(
    *, run_id: str | None = None, run_root: str | Path = DEFAULT_RUN_ROOT,
    allow_real_provider: bool = False, lifecycle_probe_seconds: int | None = None,
    source_identity: str | None = None, fake_success_for_test: bool = False,
) -> VisualE2ERunStateV1:
    """Create one immutable run identity and return without waiting for child."""
    identifier = run_id or uuid.uuid4().hex
    if Path(identifier).name != identifier or identifier in {".", ".."}:
        raise ValueError("INVALID_RUN_ID")
    root = Path(run_root)
    directory = root / identifier
    state_path = directory / "run_state.json"
    if directory.exists() or state_path.exists():
        raise FileExistsError("RUN_ALREADY_EXISTS")
    directory.mkdir(parents=True)
    if allow_real_provider and fake_success_for_test:
        raise ValueError("CONFLICTING_PROVIDER_MODE")
    identity = source_identity or os.environ.get("SITEFORMO_SOURCE_IDENTITY") or RUNNER_VERSION
    state = VisualE2ERunStateV1(run_id=identifier, source_identity=identity, status="CREATED", updated_at=_now())
    _atomic_json(state_path, state)
    command = [sys.executable, "-m", "evals.visual_implementation.detached_runner", "--run-id", identifier, "--run-root", str(root)]
    if allow_real_provider:
        command.append("--allow-real-provider")
    if lifecycle_probe_seconds is not None:
        command.extend(["--lifecycle-probe-seconds", str(lifecycle_probe_seconds)])
    if fake_success_for_test:
        command.append("--fake-success-for-test")
    command.extend(["--source-identity", identity])
    log = (directory / "stdout-safe.log").open("ab")
    try:
        process = subprocess.Popen(
            command, cwd=str(Path(__file__).resolve().parents[2]), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
        )
    finally:
        log.close()
    return _write_state(state_path, state, status="STARTING", process_id=process.pid)


async def _run_e2e(state_path: Path, allow_real_provider: bool, fake_success_for_test: bool = False) -> None:
    from evals.visual_implementation.e2e_orchestrator import run_business_visual_qa
    from evals.visual_implementation.eval_harness import VisualEvalConfigV1

    state = VisualE2ERunStateV1.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    state = _write_state(state_path, state, status="RUNNING", started_at=state.started_at or _now(), heartbeat_at=_now(), process_id=os.getpid())
    def emit_safe(line: str) -> None:
        with (state_path.parent / "stdout-safe.log").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()

    provider = None
    if fake_success_for_test:
        from app.services.generator_v2_visual import build_default_visual_implementation_plan
        from app.services.generator_v2_visual_provider import VisualProviderResultV1, VisualProviderUsageV1
        from evals.visual_implementation.eval_harness import build_business_visual_input

        plan = build_default_visual_implementation_plan(build_business_visual_input())

        class _FakeProvider:
            async def create_visual_plan(self, _request: object) -> VisualProviderResultV1:
                return VisualProviderResultV1(
                    status="valid", plan=plan, error_category=None,
                    operation_key="f" * 64,
                    structural_fingerprint=plan.structural_fingerprint,
                    latency_ms=0,
                    output_bytes=len(json.dumps(plan.model_dump(mode="json"), sort_keys=True).encode("utf-8")),
                    usage=VisualProviderUsageV1(
                        input_tokens=0, cached_input_tokens=0, output_tokens=0,
                        reasoning_tokens=0, total_tokens=0,
                    ),
                    validation_reason_codes=(),
                )

        provider = _FakeProvider()

    stop_heartbeat = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal state
        while not stop_heartbeat.is_set():
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                state = _write_state(state_path, state, heartbeat_at=_now())

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        result = await asyncio.wait_for(run_business_visual_qa(
            config=VisualEvalConfigV1(), allow_real_provider=allow_real_provider, environment=os.environ,
            export_directory=state_path.parent / "export", provider=provider, run_id=state.run_id,
            emit=emit_safe,
        ), timeout=DETACHED_WATCHDOG_SECONDS)
    finally:
        stop_heartbeat.set()
        await heartbeat_task
    status = result.status if result.status in _TERMINAL_STATUSES else "PROCESS_FAILURE"
    _write_state(state_path, state, status=status, completed_at=_now(), heartbeat_at=_now(), provider_call_count=result.provider_call_count,
                 reason_codes=tuple(result.reason_codes), visual_plan_hash=result.visual_plan_hash, rendered_site_hash=result.rendered_site_hash,
                 export_path_identity=result.export_path_identity)


def _lifecycle_probe(state_path: Path, seconds: int) -> None:
    state = VisualE2ERunStateV1.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    state = _write_state(state_path, state, status="RUNNING", started_at=_now(), heartbeat_at=_now(), process_id=os.getpid())
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        state = _write_state(state_path, state, heartbeat_at=_now())
    _write_state(state_path, state, status="LIFECYCLE_COMPLETE", completed_at=_now(), heartbeat_at=_now())
    print("PROCESS_PROBE_COMPLETE", flush=True)


def _child_main(args: argparse.Namespace) -> int:
    path = _state_path(Path(args.run_root), args.run_id)
    try:
        if args.lifecycle_probe_seconds is not None:
            _lifecycle_probe(path, args.lifecycle_probe_seconds)
        else:
            asyncio.run(_run_e2e(path, args.allow_real_provider, args.fake_success_for_test))
        return 0
    except asyncio.CancelledError:
        return 1
    except TimeoutError:
        _write_failure_if_nonterminal(
            path, status="TIMEOUT", reason_code="detached_watchdog",
        )
        return 2
    except BaseException:
        try:
            _write_failure_if_nonterminal(
                path, status="PROCESS_FAILURE", reason_code="process_failure",
            )
        except Exception:
            pass
        return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QA-only detached Visual E2E runner")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--allow-real-provider", action="store_true")
    parser.add_argument("--lifecycle-probe-seconds", type=int)
    parser.add_argument("--source-identity")
    parser.add_argument("--fake-success-for-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return _child_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
