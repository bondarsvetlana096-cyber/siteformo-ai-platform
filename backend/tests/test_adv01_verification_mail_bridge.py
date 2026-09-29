from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import adv01_verification_email as api
from app.services import adv01_verification_mail as service

SECRET = "dummy-test-secret"
REQUEST_ID = "123e4567-e89b-42d3-a456-426614174000"
EMAIL = "fixture@example.com"
TOKEN = "dummy-token-abcdefghijklmnopqrstuvwxyz"
CONT = "opaque-continuation-fixture-0123456789abcdefghijklmnop"


class FakeStore:
    def __init__(self):
        self.records, self.counts = {}, {}
        self.lock = threading.Lock()

    async def claim(self, request_id, digest):
        with self.lock:
            current = self.records.get(request_id)
            if current is None:
                self.records[request_id] = {"digest": digest, "state": "pending"}
                return service.Claim("new")
            if current["digest"] != digest:
                return service.Claim("conflict", current)
            return service.Claim(current["state"], current)

    async def finalize(self, request_id, record):
        with self.lock:
            self.records[request_id] = dict(record)

    async def increment(self, key, ttl_seconds):
        with self.lock:
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setenv("ADV01_MAIL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("ADV01_MAIL_BRIDGE_HMAC_SECRET", SECRET)
    monkeypatch.setenv("REDIS_URL", "redis://fixture.invalid/0")
    store, calls = FakeStore(), []
    monkeypatch.setattr(api, "get_store", lambda config: store)

    async def accepted(**kwargs):
        calls.append(kwargs)
        return "provider-fixture-id"

    monkeypatch.setattr(service, "send_verification_email", accepted)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app), store, calls


def payload(**changes):
    uid = changes.get("wordpress_user_id", 13)
    data = {"version": "1", "purpose": service.PURPOSE, "request_id": REQUEST_ID,
            "issued_at": int(time.time()), "wordpress_user_id": uid,
            "recipient_email": EMAIL,
            "verification_url": f"https://advanced1.siteformo.com/verify-email/?uid={uid}&token={TOKEN}"}
    data.update(changes)
    return data


def send(client, data, *, signature=None, timestamp=None, request_id=None):
    raw = json.dumps(data, separators=(",", ":")).encode()
    timestamp = data["issued_at"] if timestamp is None else timestamp
    request_id = data["request_id"] if request_id is None else request_id
    signature = signature or service.expected_signature(SECRET, int(timestamp), request_id, raw)
    return client.post(service.ENDPOINT_PATH, content=raw, headers={
        "Content-Type": "application/json", "X-SiteFormo-Bridge-Version": "1",
        "X-SiteFormo-Timestamp": str(timestamp), "X-SiteFormo-Request-ID": request_id,
        "X-SiteFormo-Signature": signature})


def test_valid_signature_and_provider_acceptance(bridge):
    response = send(bridge[0], payload())
    assert response.status_code == 202 and response.json()["status"] == "provider_accepted"
    assert len(bridge[2]) == 1


def test_uid_token_and_optional_opaque_cont_are_accepted(bridge):
    uid_token = send(bridge[0], payload())
    assert uid_token.status_code == 202

    continuation_request_id = "223e4567-e89b-42d3-a456-426614174000"
    continuation = payload(
        request_id=continuation_request_id,
        verification_url=(
            "https://advanced1.siteformo.com/verify-email/"
            f"?uid=13&token={TOKEN}&cont={CONT}"
        ),
    )
    assert send(bridge[0], continuation).status_code == 202


def test_invalid_signature(bridge):
    assert send(bridge[0], payload(), signature="invalid").status_code == 401


@pytest.mark.parametrize("offset", [-301, 301])
def test_stale_and_future_timestamp(bridge, offset):
    data = payload(issued_at=int(time.time()) + offset)
    assert send(bridge[0], data).status_code == 401


def test_malformed_request_id(bridge):
    data = payload(request_id="not-a-uuid")
    assert send(bridge[0], data).status_code == 401


@pytest.mark.parametrize("kind", ["timestamp", "request_id"])
def test_header_body_mismatch(bridge, kind):
    data = payload()
    response = send(bridge[0], data, timestamp=data["issued_at"] + 1) if kind == "timestamp" else send(
        bridge[0], data, request_id="223e4567-e89b-42d3-a456-426614174000")
    assert response.status_code == 400


def test_replay_same_digest_sends_once(bridge):
    data = payload()
    assert send(bridge[0], data).status_code == 202
    replay = send(bridge[0], data)
    assert replay.status_code == 202 and replay.json()["replayed"] is True and len(bridge[2]) == 1


def test_replay_different_digest_conflicts(bridge):
    assert send(bridge[0], payload()).status_code == 202
    assert send(bridge[0], payload(recipient_email="other@example.com")).status_code == 409


def test_concurrent_duplicate_sends_once(bridge, monkeypatch):
    calls = 0
    async def delayed(**kwargs):
        nonlocal calls
        calls += 1
        time.sleep(.05)
        return "provider-fixture-id"
    monkeypatch.setattr(service, "send_verification_email", delayed)
    data = payload()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: send(bridge[0], data), range(2)))
    assert sorted(r.status_code for r in responses) == [202, 409] and calls == 1


@pytest.mark.parametrize("changes", [
    {"unexpected": "field"}, {"recipient_email": "invalid"},
    {"verification_url": f"https://example.test/verify-email/?uid=13&token={TOKEN}"},
    {"verification_url": f"http://advanced1.siteformo.com/verify-email/?uid=13&token={TOKEN}"},
    {"verification_url": f"https://advanced1.siteformo.com/wrong/?uid=13&token={TOKEN}"},
    {"verification_url": f"https://advanced1.siteformo.com/verify-email/?uid=14&token={TOKEN}"},
    {"verification_url": f"https://advanced1.siteformo.com/verify-email/?uid=0&token={TOKEN}"},
    {"verification_url": f"https://advanced1.siteformo.com/verify-email/?uid=-1&token={TOKEN}"},
    {"verification_url": f"https://advanced1.siteformo.com/verify-email/?uid=abc&token={TOKEN}"},
    {"verification_url": f"https://advanced1.siteformo.com/verify-email/?uid=13&token={TOKEN}&extra=x"},
    {"verification_url": f"https://advanced1.siteformo.com/verify-email/?uid=13&uid=13&token={TOKEN}"},
    {"verification_url": f"https://advanced1.siteformo.com/verify-email/?uid=13&token={TOKEN}&token=x"},
    {"verification_url": f"https://advanced1.siteformo.com/verify-email/?uid=13&token={TOKEN}&cont={CONT}&cont=x"},
    {"verification_url": "https://advanced1.siteformo.com/verify-email/?uid=13&token="},
    {"verification_url": f"https://advanced1.siteformo.com/verify-email/?uid=13&token={TOKEN}&cont="},
    {"verification_url": f"https://user@advanced1.siteformo.com/verify-email/?uid=13&token={TOKEN}"},
    {"verification_url": f"https://advanced1.siteformo.com/verify-email/?uid=13&token={TOKEN}#x"},
    {"subject": "caller"}, {"body": "caller"}, {"sender": "caller@example.test"},
])
def test_schema_url_and_relay_restrictions(bridge, changes):
    assert send(bridge[0], payload(**changes)).status_code == 400


def test_feature_disabled(bridge, monkeypatch):
    monkeypatch.setenv("ADV01_MAIL_BRIDGE_ENABLED", "false")
    assert send(bridge[0], payload()).status_code == 503


def test_rate_limit(bridge):
    key = f"sf:adv01-mail:v1:rate:uid:13:{int(time.time()) // 3600}"
    bridge[1].counts[key] = 3
    assert send(bridge[0], payload()).status_code == 429


@pytest.mark.parametrize("error,status", [(service.ProviderRejected(), 502), (service.ProviderResultUnknown(), 504)])
def test_provider_failures_are_cached(bridge, monkeypatch, error, status):
    calls = 0
    async def fail(**kwargs):
        nonlocal calls
        calls += 1
        raise error
    monkeypatch.setattr(service, "send_verification_email", fail)
    data = payload()
    assert send(bridge[0], data).status_code == status
    assert send(bridge[0], data).status_code == status and calls == 1


def test_log_redaction(bridge, caplog):
    caplog.set_level(logging.INFO, logger="siteformo.adv01_verification_mail")
    verification_url = (
        "https://advanced1.siteformo.com/verify-email/"
        f"?uid=13&token={TOKEN}&cont={CONT}"
    )
    assert send(bridge[0], payload(verification_url=verification_url)).status_code == 202
    assert all(value not in caplog.text for value in (
        SECRET, EMAIL, TOKEN, CONT, verification_url, "verify-email"
    ))


def test_fixed_provider_envelope(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "dummy-provider-key")
    captured = {}
    class Response:
        status_code = 200
        def json(self): return {"id": "fixture-id"}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, **json})
            return Response()
    monkeypatch.setattr(service.httpx, "AsyncClient", Client)
    config = service.BridgeConfig(True, SECRET, "redis://fixture", "advanced1.siteformo.com",
                                  "Fixed Sender <sender@example.test>", "reply@example.test")
    result = asyncio.run(service.send_verification_email(recipient=EMAIL,
        verification_url=f"https://advanced1.siteformo.com/verify-email/?uid=13&token={TOKEN}",
        request_id=REQUEST_ID, config=config))
    assert result == "fixture-id" and captured["from"] == config.sender
    assert captured["reply_to"] == config.reply_to and captured["to"] == [EMAIL]
    assert captured["subject"] == "Confirm your NORTH / KEY account"


def test_production_app_import_and_route_registration():
    from app.main import app
    assert service.ENDPOINT_PATH in {route.path for route in app.routes}
