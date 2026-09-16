from __future__ import annotations

import json
import builtins
import os
import subprocess
import sys
import threading
import time
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


def test_real_authorization_is_instance_bound_and_nonce_is_hashed():
    base = {
        "SITEFORMO_VISUAL_QA_AUTHORIZATION_NONCE": "one-time-secret",
        "SITEFORMO_VISUAL_QA_AUTHORIZED_INSTANCE_ID": "replica-a",
        "SITEFORMO_VISUAL_PLANNER_EVAL_ALLOW_REAL": "true",
        "SITEFORMO_VISUAL_PLANNER_ENABLED": "true",
    }
    allowed, nonce_hash = qa_service._real_business_authorized(base, "replica-a")
    refused, _ = qa_service._real_business_authorized(base, "replica-b")
    assert allowed is True and refused is False
    assert nonce_hash and nonce_hash != "one-time-secret"


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
