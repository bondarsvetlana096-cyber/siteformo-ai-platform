from __future__ import annotations

from pathlib import Path
import os
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.journey.models import JourneyBase, SiteFormoJourneyProject, SiteFormoVisitor
from app.main import app
from app.models.order import Order, OrderStatus


def factory():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine); JourneyBase.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def override(maker):
    def dependency():
        with maker() as db: yield db
    return dependency


def payload(candidate=None):
    return {
        "flow_version":"q1_v2","schema_version":2,"project_class_intent":"business_site",
        "preferred_contact":{"channel":"email","value":"owner@example.test","normalized_value":"owner@example.test","purpose":"operational_communication","display_on_generated_website":False},
        "existing_website":{"has_existing_website":True,"url":"https://example.test","analysis":{"source":"existing_website","status":"unconfirmed","data":{"recommended_package":candidate}}},
        "examples_context":{"selected_example_id":"business1"},
        "package_browsing_context":{"package_key":"starter","source":"example","observed_at":"2026-09-10T00:00:00Z"},
        "package_qualification":{"status":"candidate_unconfirmed" if candidate else "unqualified","candidate_package":candidate,"source":"existing_website_analysis" if candidate else None},
        "assistant_context":{"current_step_id":"q1_complete","enabled":False},
    }


def test_direct_q1_reuses_visitor_order_and_patch_is_idempotent():
    maker=factory(); app.dependency_overrides[get_db]=override(maker)
    try:
        client=TestClient(app,base_url="https://ie.siteformo.com"); origin={"Origin":"https://ie.siteformo.com"}
        session=client.post("/api/journey/session",headers=origin).json(); headers={**origin,JOURNEY_CREDENTIAL_HEADER:session["credential"]}
        first=client.post("/api/orders/q1/project",headers=headers,json={}).json(); second=client.post("/api/orders/q1/project",headers=headers,json={}).json()
        assert first["order_id"]==second["order_id"] and first["journey_id"]==session["journey_id"]
        saved=client.patch(f"/api/orders/{first['order_id']}/q1",headers=headers,json=payload("advanced")); repeated=client.patch(f"/api/orders/{first['order_id']}/q1",headers=headers,json=payload("advanced"))
        assert saved.status_code==200 and saved.json()["idempotent"] is False and repeated.json()["idempotent"] is True
        with maker() as db:
            assert len(db.execute(select(SiteFormoVisitor)).scalars().all())==1
            assert len(db.execute(select(SiteFormoJourneyProject)).scalars().all())==1
            orders=db.execute(select(Order)).scalars().all(); assert len(orders)==1
            q1=orders[0].brief_answers["q1_v2"]
            assert q1["package_browsing_context"]["package_key"]=="starter"
            assert q1["package_qualification"]["candidate_package"]=="advanced"
            assert "qualified_package" not in q1
    finally: app.dependency_overrides.clear()


def test_order_binding_rejects_another_journey():
    maker=factory(); app.dependency_overrides[get_db]=override(maker)
    try:
        client=TestClient(app,base_url="https://ie.siteformo.com"); origin={"Origin":"https://ie.siteformo.com"}
        a=client.post("/api/journey/session",headers=origin).json()["credential"]; b=client.post("/api/journey/session",headers=origin).json()["credential"]
        order_id=client.post("/api/orders/q1/project",headers={**origin,JOURNEY_CREDENTIAL_HEADER:a},json={}).json()["order_id"]
        assert client.post("/api/orders/q1/project",headers={**origin,JOURNEY_CREDENTIAL_HEADER:b},json={"order_id":order_id}).status_code==403
    finally: app.dependency_overrides.clear()


def test_completed_order_is_retired_and_new_current_draft_is_created():
    maker=factory(); app.dependency_overrides[get_db]=override(maker)
    try:
        client=TestClient(app,base_url="https://ie.siteformo.com"); origin={"Origin":"https://ie.siteformo.com"}
        session=client.post("/api/journey/session",headers=origin).json(); headers={**origin,JOURNEY_CREDENTIAL_HEADER:session["credential"]}
        old_id=client.post("/api/orders/q1/project",headers=headers,json={}).json()["order_id"]
        with maker() as db:
            old=db.get(Order,old_id); old.status=OrderStatus.BRIEF_SUBMITTED; db.commit()
        new_id=client.post("/api/orders/q1/project",headers=headers,json={"order_id":old_id}).json()["order_id"]
        assert new_id!=old_id
        assert client.patch(f"/api/orders/{old_id}/q1",headers=headers,json=payload()).status_code==403
        with maker() as db:
            bindings=db.execute(select(SiteFormoJourneyProject)).scalars().all()
            assert len(bindings)==2 and sum(item.is_current for item in bindings)==1
            assert next(item.order_id for item in bindings if item.is_current)==new_id
    finally: app.dependency_overrides.clear()


def test_explicit_new_project_allows_multiple_orders_without_ambiguous_current():
    maker=factory(); app.dependency_overrides[get_db]=override(maker)
    try:
        client=TestClient(app,base_url="https://ie.siteformo.com"); origin={"Origin":"https://ie.siteformo.com"}
        credential=client.post("/api/journey/session",headers=origin).json()["credential"]; headers={**origin,JOURNEY_CREDENTIAL_HEADER:credential}
        first=client.post("/api/orders/q1/project",headers=headers,json={}).json()["order_id"]
        second=client.post("/api/orders/q1/project",headers=headers,json={"start_new_project":True}).json()["order_id"]
        resumed=client.post("/api/orders/q1/project",headers=headers,json={}).json()["order_id"]
        assert first!=second and resumed==second
        with maker() as db:
            assert len(db.execute(select(Order)).scalars().all())==2
            bindings=db.execute(select(SiteFormoJourneyProject)).scalars().all()
            assert len(bindings)==2 and [item.order_id for item in bindings if item.is_current]==[second]
    finally: app.dependency_overrides.clear()


def test_frontend_contract_and_sleeping_assistant():
    source=(Path(__file__).parents[2]/"frontend"/"q1_v2_WPCode.html").read_text(encoding="utf-8")
    assert 'STEPS=["intro","q1","q2","q3","complete"]' in source
    assert 'aria-label="Question ${n} of 3"' in source
    for value in ("one_page", "business_site", "ecommerce_catalog", "online_service_platform", "unsure"):
        assert value in source
    for value in ("whatsapp", "telegram", "email"):
        assert value in source
    assert "display_on_generated_website:false" in source
    assert 'purpose:"operational_communication"' in source
    assert 'existing_website:{has_existing_website:null,url:null,analysis:null}' in source
    assert 'analysis:null' in source and 'status:"unqualified"' in source
    assert "/api/journey/session" in source
    assert "/api/orders/q1/project" in source
    assert '/api/orders/${encodeURIComponent(state.journey.order_id)}/q1' in source
    assert '"X-SiteFormo-Visitor"' in source
    assert 'Q2_URL="/extended-questionnaire/"' in source
    assert 'entry:"q1_v2"' in source
    assert "/api/assistant/session" not in source and "/api/assistant/message" not in source
    assert "overflow-x:clip" in source and "prefers-reduced-motion:reduce" in source


def test_frontend_has_only_approved_three_question_authority():
    source=(Path(__file__).parents[2]/"frontend"/"q1_v2_WPCode.html").read_text(encoding="utf-8")
    assert "What kind of website are you looking for?" in source
    assert "How should SiteFormo contact you?" in source
    assert "Do you already have a website?" in source
    assert "Website URL" in source
    forbidden=(
        "/api/orders/intake", "siteformo_openai_brief", "Redesign existing website",
        "optional note", "Skip this website question", "qualified_package",
        "siteformo_q1_v41", "siteformo_q1_v40", "siteformo_q1_v34",
        "test-contact", "owner alert", "synthetic order",
    )
    for token in forbidden:
        assert token not in source
    assert "alert(" not in source


def test_frontend_examples_resume_and_review_are_fail_closed():
    source=(Path(__file__).parents[2]/"frontend"/"q1_v2_WPCode.html").read_text(encoding="utf-8")
    assert 'DRAFT_KEY="siteformo_q1_v2_draft"' in source
    assert '"siteformo_source_example_payload","siteformo_example_tracking"' in source
    assert "document.title" not in source
    assert 'selected_example_id:"example-owner-review"' in source
    assert 'if(REVIEW_MODE){state.journey={journey_id:"journey-review",order_id:"order-review"' in source
    assert 'Saved in local review mode. No production request was sent.' in source


def test_frontend_validation_accessibility_and_safe_redirect_contract():
    source=(Path(__file__).parents[2]/"frontend"/"q1_v2_WPCode.html").read_text(encoding="utf-8")
    assert 'role="radiogroup"' in source and 'role="radio"' in source
    assert 'role="alert" aria-live="assertive"' in source
    assert ":focus-visible" in source
    assert "^\\+[1-9]\\d{7,14}$" in source
    assert "^@[A-Za-z0-9_]{5,32}$" in source
    assert "u.hostname.includes" in source
    assert "order_id:state.journey.order_id" in source
    assert "project_class_intent:state.project_class_intent" in source
    redirect=source[source.index("function redirectUrl()"):source.index("async function submit()")]
    assert "preferred_contact" not in redirect and "normalized_value" not in redirect


def test_project_lifecycle_on_postgresql():
    url=os.getenv("Q1_LIFECYCLE_TEST_DATABASE_URL")
    if not url: pytest.skip("Q1_LIFECYCLE_TEST_DATABASE_URL is not configured")
    parsed=urlparse(url); assert parsed.hostname in {"localhost","127.0.0.1"} and parsed.scheme.startswith("postgresql")
    engine=create_engine(url,pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE")); connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine); JourneyBase.metadata.create_all(engine)
    maker=sessionmaker(bind=engine,expire_on_commit=False); app.dependency_overrides[get_db]=override(maker)
    try:
        client=TestClient(app,base_url="https://ie.siteformo.com"); origin={"Origin":"https://ie.siteformo.com"}
        a=client.post("/api/journey/session",headers=origin).json(); ah={**origin,JOURNEY_CREDENTIAL_HEADER:a["credential"]}
        first=client.post("/api/orders/q1/project",headers=ah,json={}).json()["order_id"]
        assert client.post("/api/orders/q1/project",headers=ah,json={}).json()["order_id"]==first
        saved=client.patch(f"/api/orders/{first}/q1",headers=ah,json=payload())
        repeated=client.patch(f"/api/orders/{first}/q1",headers=ah,json=payload())
        assert not saved.json()["idempotent"] and repeated.json()["idempotent"]
        second=client.post("/api/orders/q1/project",headers=ah,json={"start_new_project":True}).json()["order_id"]
        assert second!=first and client.post("/api/orders/q1/project",headers=ah,json={}).json()["order_id"]==second
        with maker() as db:
            bindings=db.execute(select(SiteFormoJourneyProject)).scalars().all()
            assert len(bindings)==2 and sum(x.is_current for x in bindings)==1
            db.get(Order,second).status=OrderStatus.BRIEF_SUBMITTED; db.commit()
        third=client.post("/api/orders/q1/project",headers=ah,json={}).json()["order_id"]
        assert third not in {first,second}
        assert client.patch(f"/api/orders/{second}/q1",headers=ah,json=payload()).status_code==403
        b=client.post("/api/journey/session",headers=origin).json(); bh={**origin,JOURNEY_CREDENTIAL_HEADER:b["credential"]}
        assert client.post("/api/orders/q1/project",headers=bh,json={"order_id":third}).status_code==403
        with maker() as db:
            assert len(db.execute(select(Order)).scalars().all())==3
            bindings=db.execute(select(SiteFormoJourneyProject).where(SiteFormoJourneyProject.siteformo_visitor_id==a["journey_id"])).scalars().all()
            assert len(bindings)==3 and sum(x.is_current for x in bindings)==1
    finally:
        app.dependency_overrides.clear()
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE")); connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()
