from __future__ import annotations

import asyncio
import builtins
import json
import os
import time
from argparse import Namespace
from types import SimpleNamespace

import pytest

from evals.visual_implementation.detached_runner import (
    VisualE2ERunStateV1,
    _child_main,
    _is_process_alive,
    _run_e2e,
    launch_visual_e2e_run,
    read_visual_e2e_run_state,
)
from evals.visual_implementation.e2e_orchestrator import verify_visual_qa_export


def test_is_process_alive_windows_never_uses_os_kill(monkeypatch):
    from evals.visual_implementation import detached_runner as runner

    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(runner.os, "kill", lambda *_args: pytest.fail("os.kill must not be called on Windows"))
    monkeypatch.setattr(runner, "_is_windows_process_alive", lambda pid: pid == 42)
    assert _is_process_alive(42) is True
    assert _is_process_alive(43) is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [(ProcessLookupError(), False), (PermissionError(), True), (OSError(), False)],
)
def test_is_process_alive_posix_signal_zero_handling(monkeypatch, error, expected):
    from evals.visual_implementation import detached_runner as runner

    monkeypatch.setattr(runner.os, "name", "posix")

    def raise_error(pid, signal):
        assert pid == 42
        assert signal == 0
        raise error

    monkeypatch.setattr(runner.os, "kill", raise_error)
    assert _is_process_alive(42) is expected


def test_is_process_alive_posix_success(monkeypatch):
    from evals.visual_implementation import detached_runner as runner

    monkeypatch.setattr(runner.os, "name", "posix")
    calls = []
    monkeypatch.setattr(runner.os, "kill", lambda pid, signal: calls.append((pid, signal)))
    assert _is_process_alive(42) is True
    assert calls == [(42, 0)]


def test_atomic_state_replace_retries_transient_windows_sharing_failure(monkeypatch, tmp_path):
    from evals.visual_implementation import detached_runner as runner

    path = tmp_path / "run_state.json"
    state = VisualE2ERunStateV1(
        run_id="replace-retry", source_identity="source", status="CREATED",
        updated_at="2000-01-01T00:00:00+00:00",
    )
    real_replace = runner.os.replace
    attempts = 0

    def transient_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(5, "transient Windows sharing violation")
        real_replace(source, destination)

    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(runner.os, "replace", transient_replace)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    runner._atomic_json(path, state)
    assert attempts == 2
    assert VisualE2ERunStateV1.model_validate_json(path.read_text(encoding="utf-8")) == state


def _wait_terminal(run_id: str, root, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = read_visual_e2e_run_state(run_id, root)
        if state.status in {"LIFECYCLE_COMPLETE", "PROCESS_FAILURE", "STALE_PROCESS", "TIMEOUT"}:
            return state
        time.sleep(0.1)
    return read_visual_e2e_run_state(run_id, root)


def test_detached_lifecycle_returns_immediately_and_completes(tmp_path):
    started = time.monotonic()
    state = launch_visual_e2e_run(run_id="probe", run_root=tmp_path, lifecycle_probe_seconds=2)
    assert time.monotonic() - started < 1.0
    assert state.status == "STARTING"
    running = read_visual_e2e_run_state("probe", tmp_path)
    assert running.status in {"STARTING", "RUNNING"}
    final = _wait_terminal("probe", tmp_path)
    assert final.status == "LIFECYCLE_COMPLETE"
    assert final.completed_at is not None
    assert final.heartbeat_at is not None
    assert "stdout-safe.log" in {p.name for p in (tmp_path / "probe").iterdir()}


def test_duplicate_run_id_fails_closed(tmp_path):
    launch_visual_e2e_run(run_id="same", run_root=tmp_path, lifecycle_probe_seconds=1)
    with pytest.raises(FileExistsError, match="RUN_ALREADY_EXISTS"):
        launch_visual_e2e_run(run_id="same", run_root=tmp_path, lifecycle_probe_seconds=1)


def test_stale_running_state_is_classified_without_resume(tmp_path):
    directory = tmp_path / "stale"
    directory.mkdir()
    state = VisualE2ERunStateV1(
        run_id="stale", source_identity="visual-e2e-runner-v1", status="RUNNING",
        updated_at="2000-01-01T00:00:00+00:00", process_id=999999,
    )
    (directory / "run_state.json").write_text(json.dumps(state.model_dump(mode="json")), encoding="utf-8")
    result = read_visual_e2e_run_state("stale", tmp_path)
    assert result.status == "STALE_PROCESS"
    assert result.reason_codes == ("stale_process",)


def test_alive_pid_is_not_stale_even_with_old_heartbeat(tmp_path):
    directory = tmp_path / "alive"
    directory.mkdir()
    state = VisualE2ERunStateV1(
        run_id="alive", source_identity="source", status="RUNNING",
        updated_at="2000-01-01T00:00:00+00:00",
        heartbeat_at="2000-01-01T00:00:00+00:00", process_id=os.getpid(),
    )
    (directory / "run_state.json").write_text(json.dumps(state.model_dump(mode="json")), encoding="utf-8")
    assert read_visual_e2e_run_state("alive", tmp_path).status == "RUNNING"


def test_heartbeat_refreshes_during_long_e2e_await(monkeypatch, tmp_path):
    from evals.visual_implementation import detached_runner as runner
    from evals.visual_implementation import e2e_orchestrator

    directory = tmp_path / "heartbeat"
    directory.mkdir()
    state = VisualE2ERunStateV1(
        run_id="heartbeat", source_identity="source", status="STARTING", updated_at="2000-01-01T00:00:00+00:00",
    )
    path = directory / "run_state.json"
    path.write_text(json.dumps(state.model_dump(mode="json")), encoding="utf-8")

    async def slow(**_kwargs):
        await asyncio.sleep(0.12)
        return SimpleNamespace(
            status="PROVIDER_FAILURE", provider_call_count=1, reason_codes=("provider_failure",),
            visual_plan_hash=None, rendered_site_hash=None, export_path_identity=None,
        )

    monkeypatch.setattr(runner, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(e2e_orchestrator, "run_business_visual_qa", slow)
    asyncio.run(_run_e2e(path, False))
    final = read_visual_e2e_run_state("heartbeat", tmp_path)
    assert final.status == "PROVIDER_FAILURE"
    assert final.heartbeat_at is not None
    assert final.updated_at != "2000-01-01T00:00:00+00:00"


def test_detached_fake_success_exports_and_verifies(tmp_path):
    launch_visual_e2e_run(
        run_id="fake-success", run_root=tmp_path, fake_success_for_test=True,
        source_identity="test-source-identity",
    )
    final = _wait_terminal("fake-success", tmp_path)
    assert final.status == "VALID_RENDERED"
    assert final.provider_call_count == 1
    assert final.source_identity == "test-source-identity"
    verified = verify_visual_qa_export(tmp_path / "fake-success" / "export")
    assert verified.c3_status == "VALID"
    assert verified.c4b_status == "READY"


def test_watchdog_timeout_is_terminal_without_resume(monkeypatch, tmp_path):
    from evals.visual_implementation import detached_runner as runner

    directory = tmp_path / "watchdog"
    directory.mkdir()
    state = VisualE2ERunStateV1(
        run_id="watchdog", source_identity="source", status="STARTING", updated_at="2000-01-01T00:00:00+00:00",
    )
    path = directory / "run_state.json"
    path.write_text(json.dumps(state.model_dump(mode="json")), encoding="utf-8")

    async def timeout(*_args, **_kwargs):
        raise TimeoutError

    monkeypatch.setattr(runner, "_run_e2e", timeout)
    code = _child_main(Namespace(
        run_root=str(tmp_path), run_id="watchdog", lifecycle_probe_seconds=None,
        allow_real_provider=False, fake_success_for_test=False,
    ))
    final = read_visual_e2e_run_state("watchdog", tmp_path)
    assert code == 2
    assert final.status == "TIMEOUT"
    assert final.reason_codes == ("detached_watchdog",)
    assert final.provider_call_count == 0


def test_post_terminal_exception_cannot_downgrade_valid_rendered(monkeypatch, tmp_path):
    from evals.visual_implementation import detached_runner as runner

    directory = tmp_path / "terminal-monotonic"
    directory.mkdir()
    state = VisualE2ERunStateV1(
        run_id="terminal-monotonic", source_identity="source", status="STARTING",
        updated_at="2000-01-01T00:00:00+00:00",
    )
    path = directory / "run_state.json"
    path.write_text(json.dumps(state.model_dump(mode="json")), encoding="utf-8")

    async def persist_then_fail(*_args, **_kwargs):
        running = VisualE2ERunStateV1.model_validate_json(path.read_text(encoding="utf-8"))
        runner._write_state(
            path, running, status="VALID_RENDERED", completed_at=runner._now(),
            provider_call_count=1, visual_plan_hash="a" * 64,
            rendered_site_hash="b" * 64, export_path_identity="export",
        )
        raise RuntimeError("post-terminal test failure")

    monkeypatch.setattr(runner, "_run_e2e", persist_then_fail)
    code = _child_main(Namespace(
        run_root=str(tmp_path), run_id="terminal-monotonic", lifecycle_probe_seconds=None,
        allow_real_provider=False, fake_success_for_test=False,
    ))
    final = read_visual_e2e_run_state("terminal-monotonic", tmp_path)
    assert code == 3
    assert final.status == "VALID_RENDERED"
    assert final.provider_call_count == 1
    assert final.reason_codes == ()
    assert final.export_path_identity == "export"


def test_state_contract_is_closed_and_frozen():
    with pytest.raises(Exception):
        VisualE2ERunStateV1(run_id="x", source_identity="x", status="CREATED", updated_at="now", extra_field="nope")


def test_lifecycle_mode_has_no_provider_import(monkeypatch, tmp_path):
    directory = tmp_path / "lifecycle-imports"
    directory.mkdir()
    state = VisualE2ERunStateV1(
        run_id="lifecycle-imports", source_identity="source", status="STARTING",
        updated_at="2000-01-01T00:00:00+00:00",
    )
    (directory / "run_state.json").write_text(
        json.dumps(state.model_dump(mode="json")), encoding="utf-8",
    )
    imported: list[str] = []
    real_import = builtins.__import__

    def reject_provider_import(name, *args, **kwargs):
        if name in {
            "app.services.generator_v2_visual",
            "app.services.generator_v2_visual_provider",
            "evals.visual_implementation.eval_harness",
            "evals.visual_implementation.e2e_orchestrator",
        }:
            imported.append(name)
            raise AssertionError(f"lifecycle mode imported provider path: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_provider_import)
    code = _child_main(Namespace(
        run_root=str(tmp_path), run_id="lifecycle-imports", lifecycle_probe_seconds=0,
        allow_real_provider=False, fake_success_for_test=True,
    ))
    final = read_visual_e2e_run_state("lifecycle-imports", tmp_path)
    assert code == 0
    assert final.status == "LIFECYCLE_COMPLETE"
    assert final.provider_call_count == 0
    assert imported == []


def test_fake_success_mode_is_explicit_and_cannot_enable_real_provider(monkeypatch, tmp_path):
    from evals.visual_implementation import detached_runner as runner

    commands: list[list[str]] = []

    class FakeProcess:
        pid = 42

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("SITEFORMO_FAKE_SUCCESS_FOR_TEST", "true")
    launch_visual_e2e_run(run_id="environment", run_root=tmp_path)
    assert "--fake-success-for-test" not in commands[-1]

    launch_visual_e2e_run(run_id="explicit", run_root=tmp_path, fake_success_for_test=True)
    assert "--fake-success-for-test" in commands[-1]

    with pytest.raises(ValueError, match="CONFLICTING_PROVIDER_MODE"):
        launch_visual_e2e_run(
            run_id="conflict", run_root=tmp_path,
            allow_real_provider=True, fake_success_for_test=True,
        )
