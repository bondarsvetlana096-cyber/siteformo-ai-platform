from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import assistant_routes
from app.assistant.config import AssistantSettings
from app.assistant.models import AssistantBase, AssistantVisitor
from app.db.session import get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER, hash_journey_credential
from app.journey.models import SiteFormoVisitor
from app.main import app


def _factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AssistantBase.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _override(factory):
    def dependency():
        db = factory()
        try:
            yield db
        finally:
            db.close()
    return dependency


def test_journey_bootstrap_resume_forgery_and_no_assistant_activity(monkeypatch):
    factory = _factory()
    app.dependency_overrides[get_db] = _override(factory)
    constructed: list[object] = []
    monkeypatch.setattr(assistant_routes, "AssistantOpenAIAdapter", lambda *_: constructed.append(True))
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        origin = {"Origin": "https://ie.siteformo.com"}
        first = client.post("/api/journey/session", headers=origin)
        assert first.status_code == 200
        credential = first.json()["credential"]
        visitor_id = first.json()["journey_id"]
        assert isinstance(credential, str) and len(credential) >= 43

        with factory() as db:
            visitors = db.execute(select(SiteFormoVisitor)).scalars().all()
            assert len(visitors) == 1
            assert str(visitors[0].id) == visitor_id
            assert visitors[0].credential_hash == hash_journey_credential(credential)
            assert credential != visitors[0].credential_hash
            assert db.execute(select(AssistantVisitor)).scalars().all() == []

        resumed = client.post(
            "/api/journey/session",
            headers={**origin, JOURNEY_CREDENTIAL_HEADER: credential},
        )
        assert resumed.json() == {"journey_id": visitor_id, "credential": None, "resumed": True}

        forged = client.post(
            "/api/journey/session",
            headers={**origin, JOURNEY_CREDENTIAL_HEADER: "forged-credential"},
        )
        assert forged.status_code == 200
        assert forged.json()["journey_id"] != visitor_id
        assert forged.json()["credential"] not in {None, "forged-credential"}
        assert constructed == []
    finally:
        app.dependency_overrides.clear()


def test_journey_and_assistant_origin_binding_and_privilege_isolation():
    factory = _factory()
    app.dependency_overrides[get_db] = _override(factory)
    app.dependency_overrides[assistant_routes.get_assistant_settings] = lambda: AssistantSettings(
        enabled=True, openai_api_key="offline-only", _env_file=None
    )
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        assert client.post("/api/journey/session", headers={"Origin": "https://evil.test"}).status_code == 403
        origin = {"Origin": "https://ie.siteformo.com"}
        credential = client.post("/api/journey/session", headers=origin).json()["credential"]
        headers = {**origin, JOURNEY_CREDENTIAL_HEADER: credential}
        first = client.post("/api/assistant/session", headers=headers)
        second = client.post("/api/assistant/session", headers=headers)
        assert first.status_code == second.status_code == 200
        assert first.json()["conversation_id"] == second.json()["conversation_id"]

        with factory() as db:
            assert len(db.execute(select(SiteFormoVisitor)).scalars().all()) == 1
            assistant_visitors = db.execute(select(AssistantVisitor)).scalars().all()
            assert len(assistant_visitors) == 1

        assert client.post("/api/assistant/session", headers={
            "Origin": "https://evil.test", JOURNEY_CREDENTIAL_HEADER: credential
        }).status_code == 403
        from pathlib import Path

        backend = Path(__file__).parents[1]
        order_source = (backend / "app/api/order_routes.py").read_text(encoding="utf-8")
        assert order_source.count("JOURNEY_CREDENTIAL_HEADER") == 3
        assert order_source.count("require_journey_visitor") == 2
        for relative in (
            "app/api/payment_routes.py",
            "app/api/admin_routes.py",
            "app/api/admin.py",
        ):
            source = (backend / relative).read_text(encoding="utf-8")
            assert JOURNEY_CREDENTIAL_HEADER not in source
            assert "require_journey" not in source
    finally:
        app.dependency_overrides.clear()


def test_availability_is_public_read_only_and_disabled_routes_fail_before_openai(monkeypatch):
    factory = _factory()
    app.dependency_overrides[get_db] = _override(factory)
    app.dependency_overrides[assistant_routes.get_assistant_settings] = lambda: AssistantSettings(
        enabled=False, _env_file=None
    )
    constructed: list[bool] = []
    monkeypatch.setattr(assistant_routes, "AssistantOpenAIAdapter", lambda *_: constructed.append(True))
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        assert client.get("/api/assistant/availability").json() == {"enabled": False}
        origin = {"Origin": "https://ie.siteformo.com"}
        credential = client.post("/api/journey/session", headers=origin).json()["credential"]
        response = client.post(
            "/api/assistant/session",
            headers={**origin, JOURNEY_CREDENTIAL_HEADER: credential},
        )
        assert response.status_code == 404
        assert constructed == []
        with factory() as db:
            assert db.execute(select(AssistantVisitor)).scalars().all() == []
    finally:
        app.dependency_overrides.clear()


def test_worker_has_no_journey_or_assistant_runtime_dependency():
    from pathlib import Path

    backend = Path(__file__).parents[1]
    for relative in ("app/workers/worker.py", "app/workers/generation_worker.py"):
        source = (backend / relative).read_text(encoding="utf-8")
        assert "app.journey" not in source
        assert "app.assistant" not in source
