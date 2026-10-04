from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import journey_routes
from app.db.session import Base, get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.journey.models import JourneyBase, SiteFormoJourneyProject
from app.main import app
from app.models.order import Order
from app.schemas.funnel_handoff import FunnelHandoffPayload
from app.services.funnel_handoff import FunnelHandoffReceiveError


def _factory():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    JourneyBase.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _override(factory):
    def dependency():
        with factory() as db:
            yield db
    return dependency


def _payload(subject="Electrician", state="exact", canonical="electrician", handoff_id=None):
    now = datetime.now(timezone.utc)
    return FunnelHandoffPayload(
        version="v1", receiver="ie", requested_subject=subject, match_state=state,
        shown_demo_canonical=canonical, source_request_id=None, created_at=now,
        expires_at=now + timedelta(minutes=15), handoff_id=handoff_id or uuid4(),
    )


def test_exact_handoff_creates_then_resumes_same_journey_and_project(monkeypatch):
    factory = _factory(); app.dependency_overrides[get_db] = _override(factory)
    payload = _payload(); calls = []
    monkeypatch.setattr(journey_routes, "redeem_from_demo", lambda token, ref: calls.append((token, ref)) or payload)
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        origin = {"Origin": "https://ie.siteformo.com"}
        first = client.post("/api/journey/handoff", headers=origin, json={"handoff_token": "x" * 43})
        assert first.status_code == 200
        body = first.json(); credential = body["credential"]
        retry = client.post("/api/journey/handoff", headers={**origin, JOURNEY_CREDENTIAL_HEADER: credential}, json={"handoff_token": "x" * 43})
        assert retry.status_code == 200 and retry.json()["order_id"] == body["order_id"]
        assert retry.json()["journey_id"] == body["journey_id"] and retry.json()["resumed"] is True
        with factory() as db:
            order = db.get(Order, body["order_id"])
            binding = db.execute(select(SiteFormoJourneyProject)).scalar_one()
            trace = order.brief_answers["funnel_handoff_v1"]
            assert order.desired_site_description == "Electrician"
            assert trace["shown_demo_canonical"] == "electrician"
            assert binding.handoff_id == str(payload.handoff_id)
        assert len(calls) == 2 and calls[0][1] == calls[1][1] == body["journey_id"]
    finally:
        app.dependency_overrides.clear()


def test_no_exact_example_keeps_null_canonical_and_never_substitutes_business_service(monkeypatch):
    factory = _factory(); app.dependency_overrides[get_db] = _override(factory)
    payload = _payload("Piano tuner", "no_exact_example", None)
    monkeypatch.setattr(journey_routes, "redeem_from_demo", lambda token, ref: payload)
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        response = client.post("/api/journey/handoff", headers={"Origin": "https://ie.siteformo.com"}, json={"handoff_token": "y" * 43})
        with factory() as db:
            order = db.get(Order, response.json()["order_id"])
            trace = order.brief_answers["funnel_handoff_v1"]
            assert order.desired_site_description == "Piano tuner"
            assert trace["shown_demo_canonical"] is None
            assert "business_service" not in json.dumps(trace)
    finally:
        app.dependency_overrides.clear()


def test_origin_invalid_payload_expiry_wrong_receiver_and_replay_fail_closed(monkeypatch):
    factory = _factory(); app.dependency_overrides[get_db] = _override(factory)
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        assert client.post("/api/journey/handoff", headers={"Origin": "https://evil.test"}, json={"handoff_token": "z" * 43}).status_code == 403
        for change in (
            {"receiver": "uk"},
            {"match_state": "exact", "shown_demo_canonical": "business_service"},
            {"match_state": "no_exact_example", "shown_demo_canonical": "electrician"},
            {"match_state": "no_exact", "shown_demo_canonical": None},
        ):
            raw = _payload().model_dump(); raw.update(change)
            with pytest.raises(ValueError): FunnelHandoffPayload.model_validate(raw)
        monkeypatch.setattr(journey_routes, "redeem_from_demo", lambda token, ref: (_ for _ in ()).throw(FunnelHandoffReceiveError()))
        assert client.post("/api/journey/handoff", headers={"Origin": "https://ie.siteformo.com"}, json={"handoff_token": "z" * 43}).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_shared_contract_fixtures_have_no_field_drift():
    fixtures = json.loads((Path(__file__).parent / "fixtures/funnel_handoff_v1.json").read_text(encoding="utf-8"))
    expected = {"version", "receiver", "requested_subject", "match_state", "shown_demo_canonical", "source_request_id", "created_at", "expires_at", "handoff_id"}
    assert set(fixtures) == {"electrician", "piano_tuner"}
    for value in fixtures.values():
        assert set(value) == expected
        FunnelHandoffPayload.model_validate(value)
