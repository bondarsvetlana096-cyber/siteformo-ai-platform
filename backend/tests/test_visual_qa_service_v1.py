from __future__ import annotations

import json
import builtins
import hashlib
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evals.visual_implementation import qa_service
from evals.visual_implementation.e2e_orchestrator import verify_visual_qa_export


def _wait(root: Path, statuses: set[str], timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        path = root / "qa_service_state.json"
        if path.exists():
            state = qa_service.read_qa_service_state(root)
            if state.action_status in statuses:
                return state
        time.sleep(0.03)
    return qa_service.read_qa_service_state(root)


def _start(root: Path, env: dict[str, str], *extra: str):
    values = os.environ.copy()
    values.update(env)
    return subprocess.Popen(
        [sys.executable, "-m", "evals.visual_implementation.qa_service", "--root", str(root),
         "--heartbeat-seconds", "0.05", *extra],
        cwd=Path(__file__).parents[1], env=values, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _terminate(process: subprocess.Popen):
    process.terminate()
    process.wait(timeout=5)


def _idle_env(tmp_path: Path, *, instance: str = "replica-a", source: str = "test-sha") -> dict[str, str]:
    return {
        "SITEFORMO_VISUAL_QA_ACTION": "idle",
        "SITEFORMO_VISUAL_QA_CONTROL_DIR": str(tmp_path / "control"),
        "SITEFORMO_SOURCE_IDENTITY": source,
        "SITEFORMO_VISUAL_QA_INSTANCE_ID": instance,
        "SITEFORMO_VISUAL_PLANNER_EVAL_ALLOW_REAL": "true",
        "SITEFORMO_VISUAL_PLANNER_ENABLED": "true",
    }


def _authorization_payload(*, run_id: str = "authorized-run", instance: str = "replica-a",
                           source: str = "test-sha", **updates) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    payload: dict[str, object] = {
        "contract_version": "v1",
        "action": "business_e2e",
        "run_id": run_id,
        "source_identity": source,
        "authorized_instance_id": instance,
        "authorization_nonce": "a1" * 32,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
    }
    payload.update(updates)
    return payload


def _publish_raw(control: Path, payload: object, *, name: str = qa_service.AUTHORIZATION_FILENAME) -> Path:
    control.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = control / name
    text = payload if isinstance(payload, str) else json.dumps(payload)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def test_idle_is_default_and_heartbeat_continues(tmp_path):
    process = _start(tmp_path, {})
    try:
        first = _wait(tmp_path, {"IDLE"})
        time.sleep(0.12)
        second = qa_service.read_qa_service_state(tmp_path)
        assert process.poll() is None
        assert second.action_status == "IDLE"
        assert second.heartbeat_at >= first.heartbeat_at
        assert second.provider_call_count == 0
    finally:
        _terminate(process)


def test_lifecycle_completes_primary_survives_and_status_is_independent(tmp_path):
    process = _start(tmp_path, {
        "SITEFORMO_VISUAL_QA_ACTION": "lifecycle_probe",
        "SITEFORMO_VISUAL_QA_RUN_ID": "life-2",
        "SITEFORMO_VISUAL_QA_LIFECYCLE_SECONDS": "2",
        "SITEFORMO_SOURCE_IDENTITY": "test-sha",
    })
    try:
        running = _wait(tmp_path, {"RUNNING"})
        final = _wait(tmp_path, {"LIFECYCLE_COMPLETE"}, 5)
        heartbeat = final.heartbeat_at
        time.sleep(1.05)
        later = qa_service.read_qa_service_state(tmp_path)
        status = subprocess.run(
            [sys.executable, "-m", "evals.visual_implementation.qa_service", "--status", "--root", str(tmp_path)],
            cwd=Path(__file__).parents[1], text=True, capture_output=True, check=True,
        )
        assert running.provider_call_count == final.provider_call_count == 0
        assert final.source_identity == "test-sha"
        assert (tmp_path / "runs" / "life-2" / "run_state.json").is_file()
        assert process.poll() is None
        assert later.heartbeat_at > heartbeat
        assert json.loads(status.stdout)["action_status"] == "LIFECYCLE_COMPLETE"
    finally:
        _terminate(process)


def test_fake_business_exports_once_and_primary_survives(tmp_path):
    process = _start(tmp_path, {
        "SITEFORMO_VISUAL_QA_ACTION": "business_e2e",
        "SITEFORMO_VISUAL_QA_RUN_ID": "fake-business",
        "SITEFORMO_SOURCE_IDENTITY": "test-sha",
    }, "--fake-business-for-test")
    try:
        final = _wait(tmp_path, {"VALID_RENDERED", "PROCESS_FAILURE"}, 12)
        assert final.action_status == "VALID_RENDERED"
        assert final.provider_call_count == 1
        verified = verify_visual_qa_export(tmp_path / "runs" / "fake-business" / "export")
        assert verified.c3_status == "VALID"
        assert verified.c4b_status == "READY"
        assert process.poll() is None
    finally:
        _terminate(process)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"SITEFORMO_VISUAL_QA_ACTION": "unknown"}, "INVALID_ACTION"),
        ({"SITEFORMO_VISUAL_QA_ACTION": "lifecycle_probe"}, "MISSING_RUN_ID"),
        ({"SITEFORMO_VISUAL_QA_ACTION": "business_e2e", "SITEFORMO_VISUAL_QA_RUN_ID": "real"},
         "AUTHORIZATION_REFUSED"),
    ],
)
def test_fail_closed_actions_remain_inspectable(tmp_path, env, expected):
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service,
                              kwargs={"environment": env, "root": tmp_path, "stop_event": stop,
                                      "heartbeat_seconds": 0.02})
    thread.start()
    try:
        state = _wait(tmp_path, {expected})
        assert state.provider_call_count == 0
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(2)


def test_duplicate_and_restart_never_resume_action(tmp_path):
    assert qa_service._claim_run(tmp_path, "claimed", "business_e2e", "a" * 64)
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": {"SITEFORMO_VISUAL_QA_ACTION": "business_e2e",
                        "SITEFORMO_VISUAL_QA_RUN_ID": "claimed"},
        "root": tmp_path, "stop_event": stop, "fake_business_for_test": True,
        "heartbeat_seconds": 0.02,
    })
    thread.start()
    try:
        state = _wait(tmp_path, {"DUPLICATE_RUN_ID"})
        assert state.provider_call_count == 0
        assert state.reason_codes == ("duplicate_run_id_no_resume",)
    finally:
        stop.set()
        thread.join(2)


def test_authorization_writer_is_atomic_private_and_never_returns_nonce(tmp_path):
    control = tmp_path / "control"
    path = qa_service.write_business_authorization(
        control_directory=control, run_id="safe-run", source_identity="sha",
        authorized_instance_id="replica-a", authorization_nonce="ab" * 32,
    )
    assert path == control / qa_service.AUTHORIZATION_FILENAME
    assert qa_service._read_secure_authorization(path).authorization_nonce == "ab" * 32
    assert not list(control.glob("*.tmp"))
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert control.stat().st_mode & 0o777 == 0o700


def test_startup_removes_stale_internal_raw_authorization(tmp_path):
    control = tmp_path / "control"
    stale = _publish_raw(control, _authorization_payload(), name=".consumed.crash.json")
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": _idle_env(tmp_path), "root": tmp_path / "state", "stop_event": stop,
        "heartbeat_seconds": 0.02,
    })
    thread.start()
    try:
        state = _wait(tmp_path / "state", {"IDLE"})
        assert state.provider_call_count == 0 and not stale.exists()
    finally:
        stop.set()
        thread.join(2)


def test_idle_control_file_runs_fake_business_once_and_primary_survives(tmp_path):
    env = _idle_env(tmp_path)
    process = _start(tmp_path / "state", env, "--fake-business-for-test")
    try:
        initial = _wait(tmp_path / "state", {"IDLE"})
        assert initial.control_ready and initial.instance_identity == "replica-a"
        qa_service.write_business_authorization(
            control_directory=tmp_path / "control", run_id="control-fake",
            source_identity="test-sha", authorized_instance_id="replica-a",
            authorization_nonce="cd" * 32,
        )
        final = _wait(tmp_path / "state", {"VALID_RENDERED", "PROCESS_FAILURE"}, 15)
        assert final.action_status == "VALID_RENDERED"
        assert final.authorization_consumed is True
        assert final.authorization_nonce_hash == hashlib.sha256(("cd" * 32).encode()).hexdigest()
        assert final.provider_call_count == 1
        verified = verify_visual_qa_export(tmp_path / "state" / "runs" / "control-fake" / "export")
        assert verified.c3_status == "VALID" and verified.c4b_status == "READY"
        assert process.poll() is None
        assert not (tmp_path / "control" / qa_service.AUTHORIZATION_FILENAME).exists()
        assert not list((tmp_path / "control").glob(".consumed.*"))
    finally:
        _terminate(process)


def test_replacement_without_ephemeral_file_remains_idle(tmp_path):
    env_a = _idle_env(tmp_path, instance="replica-a")
    stop_a = threading.Event()
    thread_a = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": env_a, "root": tmp_path / "a", "stop_event": stop_a,
        "fake_business_for_test": True, "heartbeat_seconds": 0.02,
    })
    thread_a.start()
    try:
        _wait(tmp_path / "a", {"IDLE"})
        qa_service.write_business_authorization(
            control_directory=tmp_path / "control", run_id="instance-a-run",
            source_identity="test-sha", authorized_instance_id="replica-a",
            authorization_nonce="01" * 32,
        )
        assert _wait(tmp_path / "a", {"VALID_RENDERED"}, 12).provider_call_count == 1
    finally:
        stop_a.set()
        thread_a.join(2)

    env_b = _idle_env(tmp_path, instance="replica-b")
    env_b["SITEFORMO_VISUAL_QA_CONTROL_DIR"] = str(tmp_path / "replacement-control")
    replacement_root = tmp_path / "b"
    replacement_root.mkdir()
    (replacement_root / "qa_service_state.json").write_text(
        (tmp_path / "a" / "qa_service_state.json").read_text(encoding="utf-8"), encoding="utf-8")
    stop_b = threading.Event()
    thread_b = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": env_b, "root": replacement_root, "stop_event": stop_b,
        "fake_business_for_test": True, "heartbeat_seconds": 0.02,
    })
    thread_b.start()
    try:
        state = _wait(replacement_root, {"IDLE"})
        time.sleep(0.35)
        state = qa_service.read_qa_service_state(replacement_root)
        assert state.instance_identity == "replica-b"
        assert state.action_status == "IDLE" and state.provider_call_count == 0
    finally:
        stop_b.set()
        thread_b.join(2)


@pytest.mark.parametrize(
    ("instance", "source", "reason"),
    [("replica-b", "test-sha", "authorization_instance_mismatch"),
     ("replica-a", "wrong-sha", "authorization_source_mismatch")],
)
def test_wrong_identity_or_source_is_refused_without_provider(tmp_path, instance, source, reason):
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": _idle_env(tmp_path), "root": tmp_path / "state", "stop_event": stop,
        "fake_business_for_test": True, "heartbeat_seconds": 0.02,
    })
    thread.start()
    try:
        _wait(tmp_path / "state", {"IDLE"})
        _publish_raw(tmp_path / "control", _authorization_payload(instance=instance, source=source))
        state = _wait(tmp_path / "state", {"AUTHORIZATION_REFUSED"})
        assert state.reason_codes == (reason,) and state.provider_call_count == 0
    finally:
        stop.set()
        thread.join(2)


def test_consumed_authorization_cannot_replay(tmp_path):
    env = _idle_env(tmp_path)
    process = _start(tmp_path / "state", env, "--fake-business-for-test")
    try:
        _wait(tmp_path / "state", {"IDLE"})
        kwargs = {
            "control_directory": tmp_path / "control", "run_id": "one-shot",
            "source_identity": "test-sha", "authorized_instance_id": "replica-a",
            "authorization_nonce": "ef" * 32,
        }
        qa_service.write_business_authorization(**kwargs)
        first = _wait(tmp_path / "state", {"VALID_RENDERED"}, 12)
        qa_service.write_business_authorization(**kwargs)
        time.sleep(0.5)
        later = qa_service.read_qa_service_state(tmp_path / "state")
        assert first.provider_call_count == later.provider_call_count == 1
        assert later.action_status == "VALID_RENDERED"
        assert not (tmp_path / "control" / qa_service.AUTHORIZATION_FILENAME).exists()
    finally:
        _terminate(process)


@pytest.mark.parametrize("payload", [
    "{not-json",
    _authorization_payload(extra_field="rejected"),
    _authorization_payload(authorization_nonce=""),
    _authorization_payload(authorization_nonce="weak"),
    _authorization_payload(action="lifecycle_probe"),
    _authorization_payload(run_id="../traversal"),
    _authorization_payload(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()),
    "x" * (qa_service.MAX_AUTHORIZATION_BYTES + 1),
])
def test_malformed_authorizations_are_refused_without_provider(tmp_path, payload):
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": _idle_env(tmp_path), "root": tmp_path / "state", "stop_event": stop,
        "fake_business_for_test": True, "heartbeat_seconds": 0.02,
    })
    thread.start()
    try:
        _wait(tmp_path / "state", {"IDLE"})
        _publish_raw(tmp_path / "control", payload)
        state = _wait(tmp_path / "state", {"AUTHORIZATION_REFUSED"})
        assert state.provider_call_count == 0
        assert state.authorization_nonce_hash is None
    finally:
        stop.set()
        thread.join(2)


def test_oversized_symlink_and_partial_temp_are_never_authority(tmp_path):
    control = tmp_path / "control"
    oversized = _publish_raw(control, "x" * (qa_service.MAX_AUTHORIZATION_BYTES + 1))
    with pytest.raises(ValueError, match="authorization_oversized"):
        qa_service._read_secure_authorization(oversized)
    oversized.unlink()
    partial = _publish_raw(control, "{", name=".business_authorization.partial.tmp")
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": _idle_env(tmp_path), "root": tmp_path / "state", "stop_event": stop,
        "fake_business_for_test": True, "heartbeat_seconds": 0.02,
    })
    thread.start()
    try:
        state = _wait(tmp_path / "state", {"IDLE"})
        time.sleep(0.35)
        assert qa_service.read_qa_service_state(tmp_path / "state").action_status == "IDLE"
        assert state.provider_call_count == 0 and partial.exists()
    finally:
        stop.set()
        thread.join(2)

    if os.name != "nt":
        target = tmp_path / "target.json"
        target.write_text(json.dumps(_authorization_payload()), encoding="utf-8")
        link = control / qa_service.AUTHORIZATION_FILENAME
        link.symlink_to(target)
        with pytest.raises(ValueError, match="authorization_symlink"):
            qa_service._read_secure_authorization(link)


def test_wrong_permissions_are_rejected_where_enforceable(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permission bits are not enforceable on Windows")
    path = _publish_raw(tmp_path / "control", _authorization_payload())
    path.chmod(0o644)
    with pytest.raises(ValueError, match="authorization_permissions"):
        qa_service._read_secure_authorization(path)


def test_no_authorization_stays_idle_without_provider_imports(monkeypatch, tmp_path):
    imported = []
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name in {
            "evals.visual_implementation.e2e_orchestrator",
            "evals.visual_implementation.eval_harness",
            "app.services.generator_v2_visual_provider",
        }:
            imported.append(name)
            raise AssertionError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": _idle_env(tmp_path), "root": tmp_path / "state", "stop_event": stop,
        "heartbeat_seconds": 0.02,
    })
    thread.start()
    try:
        initial = _wait(tmp_path / "state", {"IDLE"})
        time.sleep(0.4)
        later = qa_service.read_qa_service_state(tmp_path / "state")
        assert later.action_status == "IDLE" and later.provider_call_count == 0
        assert later.heartbeat_at >= initial.heartbeat_at and imported == []
    finally:
        stop.set()
        thread.join(2)


def test_control_file_cannot_bypass_disabled_provider_switches(monkeypatch, tmp_path):
    imported = []
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("evals.visual_implementation.e2e_") or name == "app.services.generator_v2_visual_provider":
            imported.append(name)
            raise AssertionError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    env = _idle_env(tmp_path)
    env["SITEFORMO_VISUAL_PLANNER_ENABLED"] = "false"
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": env, "root": tmp_path / "state", "stop_event": stop,
        "heartbeat_seconds": 0.02,
    })
    thread.start()
    try:
        _wait(tmp_path / "state", {"IDLE"})
        qa_service.write_business_authorization(
            control_directory=tmp_path / "control", run_id="switch-refused",
            source_identity="test-sha", authorized_instance_id="replica-a",
            authorization_nonce="23" * 32,
        )
        state = _wait(tmp_path / "state", {"AUTHORIZATION_REFUSED"})
        assert state.reason_codes == ("provider_switches_disabled",)
        assert state.provider_call_count == 0 and imported == []
    finally:
        stop.set()
        thread.join(2)


def test_legacy_environment_authorization_is_not_accepted(tmp_path):
    env = {
        "SITEFORMO_VISUAL_QA_ACTION": "business_e2e",
        "SITEFORMO_VISUAL_QA_RUN_ID": "legacy",
        "SITEFORMO_VISUAL_QA_AUTHORIZATION_NONCE": "45" * 32,
        "SITEFORMO_VISUAL_QA_AUTHORIZED_INSTANCE_ID": "replica-a",
        "SITEFORMO_VISUAL_QA_INSTANCE_ID": "replica-a",
        "SITEFORMO_VISUAL_PLANNER_EVAL_ALLOW_REAL": "true",
        "SITEFORMO_VISUAL_PLANNER_ENABLED": "true",
    }
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": env, "root": tmp_path, "stop_event": stop, "heartbeat_seconds": 0.02,
    })
    thread.start()
    try:
        state = _wait(tmp_path, {"AUTHORIZATION_REFUSED"})
        assert state.reason_codes == ("control_file_authorization_required",)
        assert state.provider_call_count == 0
    finally:
        stop.set()
        thread.join(2)


def test_lifecycle_imports_no_provider_or_e2e(monkeypatch, tmp_path):
    imported = []
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name in {
            "evals.visual_implementation.e2e_orchestrator",
            "evals.visual_implementation.eval_harness",
            "app.services.generator_v2_visual_provider",
        }:
            imported.append(name)
            raise AssertionError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": {"SITEFORMO_VISUAL_QA_ACTION": "lifecycle_probe",
                        "SITEFORMO_VISUAL_QA_RUN_ID": "no-provider",
                        "SITEFORMO_VISUAL_QA_LIFECYCLE_SECONDS": "1"},
        "root": tmp_path, "stop_event": stop, "heartbeat_seconds": 0.02,
    })
    thread.start()
    try:
        assert _wait(tmp_path, {"LIFECYCLE_COMPLETE"}, 3).provider_call_count == 0
        assert imported == []
    finally:
        stop.set()
        thread.join(2)


@pytest.mark.parametrize("status", [
    "PROVIDER_FAILURE", "C3_INVALID", "C4B_INVALID", "EXPORT_FAILURE", "CONFIGURATION_FAILURE",
])
def test_business_terminal_failure_matrix_keeps_service_alive(monkeypatch, tmp_path, status):
    from evals.visual_implementation import e2e_orchestrator

    async def result(**_kwargs):
        return type("Result", (), {
            "status": status, "reason_codes": (status.lower(),), "provider_call_count": 1,
            "visual_plan_hash": None, "rendered_site_hash": None, "export_path_identity": None,
        })()

    monkeypatch.setattr(e2e_orchestrator, "run_business_visual_qa", result)
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": {"SITEFORMO_VISUAL_QA_ACTION": "business_e2e",
                        "SITEFORMO_VISUAL_QA_RUN_ID": f"failure-{status.lower()}"},
        "root": tmp_path, "stop_event": stop, "fake_business_for_test": True,
        "heartbeat_seconds": 0.02,
    })
    thread.start()
    try:
        state = _wait(tmp_path, {status})
        assert state.provider_call_count == 1
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(2)


def test_business_timeout_keeps_service_alive(monkeypatch, tmp_path):
    from evals.visual_implementation import e2e_orchestrator

    async def timeout(**_kwargs):
        raise TimeoutError

    monkeypatch.setattr(e2e_orchestrator, "run_business_visual_qa", timeout)
    stop = threading.Event()
    thread = threading.Thread(target=qa_service.run_qa_service, kwargs={
        "environment": {"SITEFORMO_VISUAL_QA_ACTION": "business_e2e",
                        "SITEFORMO_VISUAL_QA_RUN_ID": "timeout"},
        "root": tmp_path, "stop_event": stop, "fake_business_for_test": True,
        "heartbeat_seconds": 0.02,
    })
    thread.start()
    try:
        assert _wait(tmp_path, {"TIMEOUT"}).provider_call_count == 0
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(2)


def test_module_is_private_and_contains_no_sensitive_state_fields():
    source = Path(qa_service.__file__).read_text(encoding="utf-8")
    assert "FastAPI" not in source and "APIRouter" not in source
    assert "raw_response" not in source and "prompt" not in VisualStateFields


VisualStateFields = set(qa_service.VisualQAServiceStateV1.model_fields)
