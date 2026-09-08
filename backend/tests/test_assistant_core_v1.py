from __future__ import annotations

import asyncio
import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import assistant_routes
from app.assistant.config import AssistantSettings
from app.assistant.identity import ASSISTANT_COOKIE_NAME, hash_possession_credential, issue_possession_credential
from app.assistant.models import AssistantBase, AssistantConversation, AssistantMessage
from app.assistant.openai_adapter import AssistantInferenceError, AssistantOpenAIAdapter
from app.assistant.service import (
    HISTORY_LIMIT,
    AssistantOwnershipError,
    load_history,
    require_owned_conversation,
    resolve_visitor_and_conversation,
    stream_message,
)
from app.db.session import Base as AppBase, get_db
from app.main import app


@pytest.fixture()
def db_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AssistantBase.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_config_is_isolated_strict_and_coexists_with_legacy(monkeypatch):
    for name in (
        "SITEFORMO_ASSISTANT_ENABLED", "SITEFORMO_ASSISTANT_OPENAI_API_KEY",
        "SITEFORMO_ASSISTANT_OPENAI_MODEL", "SITEFORMO_ASSISTANT_OPENAI_TIMEOUT_SECONDS",
        "SITEFORMO_ASSISTANT_OPENAI_MAX_RETRIES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-key")
    monkeypatch.setenv("OPENAI_MODEL", "legacy-model")
    settings = AssistantSettings(_env_file=None)
    assert settings.enabled is False
    assert settings.openai_api_key is None
    assert settings.openai_model == "gpt-5.6-luna"
    monkeypatch.setenv("SITEFORMO_ASSISTANT_OPENAI_API_KEY", "assistant-key")
    monkeypatch.setenv("SITEFORMO_ASSISTANT_OPENAI_MODEL", "assistant-model")
    isolated = AssistantSettings(_env_file=None)
    assert (isolated.openai_api_key, isolated.openai_model) == ("assistant-key", "assistant-model")
    with pytest.raises(ValueError):
        AssistantSettings(enabled="yes", _env_file=None)
    with pytest.raises(ValueError):
        AssistantSettings(openai_timeout_seconds=0, _env_file=None)
    with pytest.raises(ValueError):
        AssistantSettings(openai_max_retries=11, _env_file=None)


def test_disabled_adapter_never_constructs_client():
    calls = []
    adapter = AssistantOpenAIAdapter(
        AssistantSettings(enabled=False, _env_file=None),
        client_factory=lambda **kwargs: calls.append(kwargs),
    )

    async def invoke():
        return await anext(adapter.stream_response([{"role": "user", "content": "hello"}]))

    with pytest.raises(RuntimeError):
        asyncio.run(invoke())
    assert calls == []


def test_adapter_uses_dedicated_config_no_tools_and_sanitizes(caplog):
    captured = []

    class Responses:
        async def create(self, **kwargs):
            captured.append(kwargs)
            raise RuntimeError("raw provider detail")

    class Client:
        responses = Responses()

        async def close(self):
            return None

    constructed = []

    def factory(**kwargs):
        constructed.append(kwargs)
        return Client()

    settings = AssistantSettings(
        enabled=True, openai_api_key="dedicated-secret", openai_model="dedicated-model",
        openai_timeout_seconds=12, openai_max_retries=3, _env_file=None,
    )
    adapter = AssistantOpenAIAdapter(settings, client_factory=factory)

    async def invoke():
        return [item async for item in adapter.stream_response([{"role": "user", "content": "private body"}])]

    with pytest.raises(AssistantInferenceError, match="Assistant inference failed"):
        asyncio.run(invoke())
    assert constructed == [{"api_key": "dedicated-secret", "timeout": 12.0, "max_retries": 3}]
    assert captured[0]["model"] == "dedicated-model" and captured[0]["tools"] == []
    for secret in ("dedicated-secret", "private body", "raw provider detail"):
        assert secret not in caplog.text


def test_identity_is_high_entropy_and_only_hash_is_persisted(db_factory):
    credential = issue_possession_credential()
    assert len(credential) >= 43
    db = db_factory()
    visitor, _, issued = resolve_visitor_and_conversation(db, None)
    assert issued and visitor.credential_hash == hash_possession_credential(issued)
    assert issued != visitor.credential_hash


def test_visitor_resume_forgery_and_cross_visitor_rejection(db_factory):
    db = db_factory()
    visitor_a, conversation_a, credential_a = resolve_visitor_and_conversation(db, None)
    resumed_visitor, resumed_conversation, replacement = resolve_visitor_and_conversation(db, credential_a)
    visitor_b, _, credential_b = resolve_visitor_and_conversation(db, None)
    assert (resumed_visitor.id, resumed_conversation.id) == (visitor_a.id, conversation_a.id)
    assert replacement is None and visitor_b.id != visitor_a.id
    for credential in (credential_b, "forged-unknown-token"):
        with pytest.raises(AssistantOwnershipError):
            require_owned_conversation(db, credential, conversation_a.id)


class SuccessfulAdapter:
    calls = 0

    async def stream_response(self, input_items):
        self.calls += 1
        yield "hel"
        yield "lo"


class FailingAdapter:
    async def stream_response(self, input_items):
        yield "partial"
        raise AssistantInferenceError("sanitized")


def test_stream_retry_idempotency_replay_and_partial_safety(db_factory):
    db = db_factory()
    _, conversation, _ = resolve_visitor_and_conversation(db, None)
    turn_id = uuid.uuid4()

    async def fail():
        return [chunk async for chunk in stream_message(db, conversation, "first", "/start", turn_id, FailingAdapter())]

    with pytest.raises(AssistantInferenceError):
        asyncio.run(fail())
    history = load_history(db, conversation.id)
    assert [(row.role, row.content) for row in history] == [("user", "first")]

    adapter = SuccessfulAdapter()

    async def succeed():
        return [chunk async for chunk in stream_message(db, conversation, "first", "/start", turn_id, adapter)]

    assert asyncio.run(succeed()) == ["hel", "lo"]
    assert adapter.calls == 1
    assert [(row.role, row.content) for row in load_history(db, conversation.id)] == [
        ("user", "first"), ("assistant", "hello")
    ]

    class MustNotRun:
        async def stream_response(self, input_items):
            raise AssertionError("completed replay called OpenAI")
            yield

    async def replay():
        return [chunk async for chunk in stream_message(db, conversation, "first", "/start", turn_id, MustNotRun())]

    assert asyncio.run(replay()) == ["hello"]
    assert [row.content for row in load_history(db, conversation.id)].count("first") == 1
    with pytest.raises(AssistantOwnershipError):
        asyncio.run(anext(stream_message(db, conversation, "changed", None, turn_id, MustNotRun())))


def test_history_is_bounded(db_factory):
    db = db_factory()
    _, conversation, _ = resolve_visitor_and_conversation(db, None)
    for index in range(HISTORY_LIMIT + 5):
        db.add(AssistantMessage(conversation_id=conversation.id, role="user", status="completed", content=str(index)))
        db.commit()
    history = load_history(db, conversation.id)
    assert len(history) == HISTORY_LIMIT
    assert history[0].content == "5" and history[-1].content == str(HISTORY_LIMIT + 4)


def test_one_active_conversation_uniqueness(db_factory):
    db = db_factory()
    visitor, _, _ = resolve_visitor_and_conversation(db, None)
    db.add(AssistantConversation(visitor_id=visitor.id, status="active"))
    with pytest.raises(IntegrityError):
        db.commit()


def database_override(factory):
    def override():
        db = factory()
        try:
            yield db
        finally:
            db.close()
    return override


def test_disabled_routes_stop_before_client_and_business_activity(monkeypatch, db_factory):
    invoked = []
    monkeypatch.setattr(assistant_routes, "AssistantOpenAIAdapter", lambda *_: invoked.append(True))
    monkeypatch.setattr(assistant_routes, "resolve_visitor_and_conversation", lambda *_: invoked.append("business"))
    app.dependency_overrides[get_db] = database_override(db_factory)
    app.dependency_overrides[assistant_routes.get_assistant_settings] = lambda: AssistantSettings(enabled=False, _env_file=None)
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        origin = {"Origin": "https://ie.siteformo.com"}
        assert client.post("/api/assistant/session", headers=origin).status_code == 404
        assert client.post("/api/assistant/message", headers=origin, json={
            "message": "hello", "client_message_id": str(uuid.uuid4())
        }).status_code == 404
        assert invoked == []
    finally:
        app.dependency_overrides.clear()


def test_routes_cookie_origin_validation_history_and_progressive_sse(monkeypatch, db_factory):
    app.dependency_overrides[get_db] = database_override(db_factory)
    app.dependency_overrides[assistant_routes.get_assistant_settings] = lambda: AssistantSettings(
        enabled=True, openai_api_key="test-only", _env_file=None
    )
    monkeypatch.setattr(assistant_routes, "AssistantOpenAIAdapter", lambda *_: SuccessfulAdapter())
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        origin = {"Origin": "https://ie.siteformo.com"}
        assert client.post("/api/assistant/session", headers={"Origin": "https://evil.test"}).status_code == 403
        first = client.post("/api/assistant/session", headers=origin)
        assert first.status_code == 200
        cookie = first.headers["set-cookie"].lower()
        assert "sf_assistant_visitor=" in cookie and "domain=" not in cookie
        assert "path=/" in cookie and "httponly" in cookie and "secure" in cookie
        assert "samesite=lax" in cookie and "max-age=31536000" in cookie
        conversation_id = first.json()["conversation_id"]
        assert client.post("/api/assistant/session", headers=origin).json()["conversation_id"] == conversation_id
        payload = {"message": "first", "client_message_id": str(uuid.uuid4()), "conversation_id": conversation_id, "page_hint": "/start?x=1"}
        assert client.post("/api/assistant/message", headers={"Origin": "https://evil.test"}, json=payload).status_code == 403
        with client.stream("POST", "/api/assistant/message", headers=origin, json=payload) as response:
            body = "".join(response.iter_text())
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        assert "event: start" in body and "event: done" in body
        assert body.index('"text": "hel"') < body.index('"text": "lo"')
        restored = client.get("/api/assistant/history", params={"conversation_id": conversation_id}).json()
        assert [row["content"] for row in restored["history"]] == ["first", "hello"]
        assert client.post("/api/assistant/message", headers=origin, json={
            "message": "x" * 4001, "client_message_id": str(uuid.uuid4())
        }).status_code == 422
        assert client.post("/api/assistant/message", headers=origin, json={
            "message": "ok", "client_message_id": str(uuid.uuid4()), "page_hint": "https://evil.test"
        }).status_code == 422
        other = TestClient(app, base_url="https://ie.siteformo.com")
        other.post("/api/assistant/session", headers=origin)
        assert other.get("/api/assistant/history", params={"conversation_id": conversation_id}).status_code == 404
        other.cookies.set(ASSISTANT_COOKIE_NAME, "forged-unknown-token", domain="ie.siteformo.com")
        assert other.get("/api/assistant/history", params={"conversation_id": conversation_id}).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_provider_failure_is_sanitized_sse(monkeypatch, db_factory):
    app.dependency_overrides[get_db] = database_override(db_factory)
    app.dependency_overrides[assistant_routes.get_assistant_settings] = lambda: AssistantSettings(
        enabled=True, openai_api_key="test-only", _env_file=None
    )
    monkeypatch.setattr(assistant_routes, "AssistantOpenAIAdapter", lambda *_: FailingAdapter())
    try:
        client = TestClient(app, base_url="https://ie.siteformo.com")
        origin = {"Origin": "https://ie.siteformo.com"}
        conversation_id = client.post("/api/assistant/session", headers=origin).json()["conversation_id"]
        response = client.post("/api/assistant/message", headers=origin, json={
            "message": "private", "client_message_id": str(uuid.uuid4()), "conversation_id": conversation_id
        })
        assert "event: error" in response.text
        assert "temporarily unavailable" in response.text
        assert "partial" in response.text
        assert "sanitized" not in response.text and "private" not in response.text
        history = client.get("/api/assistant/history", params={"conversation_id": conversation_id}).json()["history"]
        assert [row["role"] for row in history] == ["user"]
    finally:
        app.dependency_overrides.clear()


def test_router_paths_are_unique_and_worker_independent():
    paths = [(route.path, tuple(sorted(getattr(route, "methods", ())))) for route in app.routes]
    assistant_paths = [item for item in paths if item[0].startswith("/api/assistant")]
    assert len(assistant_paths) == 3 and len(assistant_paths) == len(set(assistant_paths))
    for module in (assistant_routes.__name__, stream_message.__module__, AssistantOpenAIAdapter.__module__):
        assert ".workers" not in module


def test_startup_metadata_cannot_create_assistant_tables():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AppBase.metadata.create_all(engine)
    names = set(inspect(engine).get_table_names())
    assert not {"assistant_visitors", "assistant_conversations", "assistant_messages"} & names


def test_target_migration_lineage_and_postgresql_partial_index():
    migration_path = Path(__file__).parents[1] / "alembic" / "versions" / "0007_assistant_core_v1.py"
    spec = importlib.util.spec_from_file_location("assistant_migration_0007", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0007_assistant_core_v1"
    assert migration.down_revision == "0006_design_screenshot_flow"
    source = migration_path.read_text(encoding="utf-8")
    assert 'postgresql_where=sa.text("status = \'active\'")' in source
