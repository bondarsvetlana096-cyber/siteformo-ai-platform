from __future__ import annotations

from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.journey.models import SiteFormoJourneyProject
from app.models.order import Order, OrderStatus
from app.db.session import Base, get_db
from app.journey.models import JourneyBase
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.main import app
from fastapi.testclient import TestClient


ORIGIN = {"Origin": "https://ie.siteformo.com"}


def factory():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    JourneyBase.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def override(maker):
    def dependency():
        with maker() as db:
            yield db
    return dependency


def _session(client):
    value = client.post("/api/journey/session", headers=ORIGIN).json()
    return value, {**ORIGIN, JOURNEY_CREDENTIAL_HEADER: value["credential"]}


def _bind_handoff(maker, order_id):
    with maker() as db:
        binding = db.execute(select(SiteFormoJourneyProject).where(SiteFormoJourneyProject.order_id == order_id)).scalar_one()
        binding.handoff_id = str(uuid4())
        order = db.get(Order, order_id)
        order.desired_site_description = "Electrician"
        order.brief_answers = {"funnel_handoff_v1": {"requested_subject": "Electrician", "match_state": "exact", "shown_demo_canonical": "electrician", "handoff_id": binding.handoff_id}}
        db.commit()


def test_q1_explicit_new_reuses_same_current_handoff_draft_and_context():
    maker = factory(); app.dependency_overrides[get_db] = override(maker)
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        _, headers = _session(client)
        imported = client.post("/api/orders/q1/project", headers=headers, json={}).json()["order_id"]
        _bind_handoff(maker, imported)
        q1 = client.post("/api/orders/q1/project", headers=headers, json={"start_new_project": True}).json()
        assert q1["order_id"] == imported and q1["created"] is False
        with maker() as db:
            order = db.get(Order, imported)
            binding = db.execute(select(SiteFormoJourneyProject)).scalar_one()
            assert order.desired_site_description == "Electrician"
            assert order.brief_answers["funnel_handoff_v1"]["shown_demo_canonical"] == "electrician"
            assert binding.handoff_id is not None and binding.is_current is True
    finally:
        app.dependency_overrides.clear()


def test_q1_explicit_new_keeps_normal_non_handoff_behavior():
    maker = factory(); app.dependency_overrides[get_db] = override(maker)
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        _, headers = _session(client)
        first = client.post("/api/orders/q1/project", headers=headers, json={}).json()["order_id"]
        second = client.post("/api/orders/q1/project", headers=headers, json={"start_new_project": True}).json()["order_id"]
        assert second != first
    finally:
        app.dependency_overrides.clear()


def test_handoff_project_owned_by_another_visitor_is_never_reused():
    maker = factory(); app.dependency_overrides[get_db] = override(maker)
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        _, first_headers = _session(client); _, second_headers = _session(client)
        imported = client.post("/api/orders/q1/project", headers=first_headers, json={}).json()["order_id"]
        _bind_handoff(maker, imported)
        other = client.post("/api/orders/q1/project", headers=second_headers, json={"start_new_project": True}).json()["order_id"]
        assert other != imported
    finally:
        app.dependency_overrides.clear()


def test_finalized_handoff_project_is_not_reused():
    maker = factory(); app.dependency_overrides[get_db] = override(maker)
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        _, headers = _session(client)
        imported = client.post("/api/orders/q1/project", headers=headers, json={}).json()["order_id"]
        _bind_handoff(maker, imported)
        with maker() as db:
            db.get(Order, imported).status = OrderStatus.FINAL_APPROVED
            db.commit()
        replacement = client.post("/api/orders/q1/project", headers=headers, json={"start_new_project": True}).json()["order_id"]
        assert replacement != imported
    finally:
        app.dependency_overrides.clear()
