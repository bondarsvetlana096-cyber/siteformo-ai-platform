from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import order_routes
from app.db.session import Base, get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.journey.models import JourneyBase
from app.main import app
from app.services.existing_website_analyzer_v2 import analyze_existing_website, FetchResult

ORIGIN={"Origin":"https://ie.siteformo.com"}

def factory():
    engine=create_engine("sqlite+pysqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine);JourneyBase.metadata.create_all(engine)
    return sessionmaker(bind=engine,expire_on_commit=False)

def override(maker):
    def dep():
        with maker() as db:yield db
    return dep

def q1(url="https://example.test"):
    return {"flow_version":"q1_v2","schema_version":2,"project_class_intent":"business_site","preferred_contact":{"channel":"email","value":"qa@example.test","normalized_value":"qa@example.test","purpose":"operational_communication","display_on_generated_website":False},"existing_website":{"has_existing_website":True,"url":url,"analysis":None},"examples_context":{},"package_browsing_context":None,"package_qualification":{"status":"unqualified"},"assistant_context":{"current_step_id":"q1_complete","enabled":False}}

def fake_analysis(order_id,url,*_):
    return analyze_existing_website(order_id,url,lambda _: ["93.184.216.34"],lambda target,*limits:FetchResult(200,"text/html",b"<html><h1>Acme</h1></html>",31))

@pytest.fixture
def owned(monkeypatch):
    maker=factory();app.dependency_overrides[get_db]=override(maker)
    with order_routes._ANALYZER_LOCK:
        order_routes._ANALYZER_ATTEMPTS.clear();order_routes._ANALYZER_CACHE.clear();order_routes._ANALYZER_IN_FLIGHT.clear()
    monkeypatch.setattr(order_routes,"analyze_existing_website",fake_analysis)
    client=TestClient(app,base_url="https://ie.siteformo.com")
    session=client.post("/api/journey/session",headers=ORIGIN).json();headers={**ORIGIN,JOURNEY_CREDENTIAL_HEADER:session["credential"]}
    order_id=client.post("/api/orders/q1/project",headers=headers,json={}).json()["order_id"]
    assert client.patch(f"/api/orders/{order_id}/q1",headers=headers,json=q1()).status_code==200
    yield client,headers,order_id,maker
    app.dependency_overrides.clear()

def endpoint(order_id):return f"/api/orders/{order_id}/q1/existing-website-analysis"

def test_missing_wrong_journey_wrong_order_and_url_mismatch(owned):
    client,headers,order_id,_=owned
    assert client.post(endpoint(order_id),headers=ORIGIN,json={"url":"https://example.test"}).status_code in {401,403}
    assert client.post(endpoint(order_id),headers={**ORIGIN,JOURNEY_CREDENTIAL_HEADER:"wrong"},json={"url":"https://example.test"}).status_code in {401,403}
    assert client.post(endpoint("missing"),headers=headers,json={"url":"https://example.test"}).status_code==403
    other=client.post("/api/journey/session",headers=ORIGIN).json()["credential"]
    assert client.post(endpoint(order_id),headers={**ORIGIN,JOURNEY_CREDENTIAL_HEADER:other},json={"url":"https://example.test"}).status_code==403
    assert client.post(endpoint(order_id),headers=headers,json={"url":"https://other.test"}).status_code==409

def test_identical_replay_cache_rebinds_without_identity_leakage(owned):
    client,headers,order_id,_=owned
    first=client.post(endpoint(order_id),headers=headers,json={"url":"https://example.test"});second=client.post(endpoint(order_id),headers=headers,json={"url":"https://example.test"})
    assert first.status_code==second.status_code==200
    assert first.json()["order_id"]==second.json()["order_id"]==order_id
    assert first.json()["analysis_id"]!=second.json()["analysis_id"]
    assert first.json()["input_url_hash"]==second.json()["input_url_hash"]

def test_cache_isolation_rebinds_to_second_owned_project(owned):
    client,headers,first,_=owned
    a=client.post(endpoint(first),headers=headers,json={"url":"https://example.test"}).json()
    second=client.post("/api/orders/q1/project",headers=headers,json={"start_new_project":True}).json()["order_id"]
    assert client.patch(f"/api/orders/{second}/q1",headers=headers,json=q1()).status_code==200
    b=client.post(endpoint(second),headers=headers,json={"url":"https://example.test"}).json()
    assert b["order_id"]==second and b["analysis_id"]!=a["analysis_id"]

def test_new_saved_url_creates_replacement_analysis(owned):
    client,headers,order_id,maker=owned
    first=client.post(endpoint(order_id),headers=headers,json={"url":"https://example.test"}).json()
    with maker() as db:
        order=db.get(__import__("app.models.order",fromlist=["Order"]).Order,order_id);brief=deepcopy(order.brief_answers);brief["q1_v2"]["existing_website"]["url"]="https://example.test/new";order.brief_answers=brief;db.commit()
    second=client.post(endpoint(order_id),headers=headers,json={"url":"https://example.test/new"}).json()
    assert second["analysis_id"]!=first["analysis_id"] and second["input_url_hash"]!=first["input_url_hash"]

def test_one_inflight_and_concurrent_duplicate(owned,monkeypatch):
    client,headers,order_id,_=owned;started=Event();release=Event()
    def slow(*args):started.set();release.wait(2);return fake_analysis(*args)
    monkeypatch.setattr(order_routes,"analyze_existing_website",slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(client.post,endpoint(order_id),headers=headers,json={"url":"https://example.test"})
        assert started.wait(1)
        duplicate=client.post(endpoint(order_id),headers=headers,json={"url":"https://example.test"})
        release.set();first=future.result()
    assert first.status_code==200 and duplicate.status_code==409

def test_three_success_cap_and_six_attempt_journey_cap(owned):
    client,headers,order_id,maker=owned
    # Distinct cache keys force persisted successes while retaining exact saved URL via direct test setup.
    for n in range(3):
        with maker() as db:
            order=db.get(__import__("app.models.order",fromlist=["Order"]).Order,order_id);brief=deepcopy(order.brief_answers);brief["q1_v2"]["existing_website"]["url"]=f"https://example.test/?v={n}";order.brief_answers=brief;db.commit()
        assert client.post(endpoint(order_id),headers=headers,json={"url":f"https://example.test/?v={n}"}).status_code==200
    with maker() as db:
        order=db.get(__import__("app.models.order",fromlist=["Order"]).Order,order_id);brief=deepcopy(order.brief_answers);brief["q1_v2"]["existing_website"]["url"]="https://example.test/?v=3";order.brief_answers=brief;db.commit()
    assert client.post(endpoint(order_id),headers=headers,json={"url":"https://example.test/?v=3"}).status_code==429
def test_six_attempt_limit_is_fail_closed(owned):
    client,headers,order_id,_=owned
    visitor_key=next(iter(order_routes._ANALYZER_ATTEMPTS),None)
    if visitor_key is None:
        assert client.post(endpoint(order_id),headers=headers,json={"url":"https://example.test"}).status_code==200
        visitor_key=next(iter(order_routes._ANALYZER_ATTEMPTS))
    with order_routes._ANALYZER_LOCK:
        order_routes._ANALYZER_ATTEMPTS[visitor_key]=[__import__("time").time()]*6
        order_routes._ANALYZER_CACHE.clear()
    assert client.post(endpoint(order_id),headers=headers,json={"url":"https://example.test"}).status_code==429

def test_persisted_contract_contains_no_raw_or_future_authority(owned):
    client,headers,order_id,maker=owned
    result=client.post(endpoint(order_id),headers=headers,json={"url":"https://example.test"}).json()
    flattened=str(result).lower()
    for forbidden in ("raw_html","recommended_package","production_risk","eligibility","future_pages","confirmed_function","authorized_material"):
        assert forbidden not in flattened
    assert all(item["reuse_authority"]=="UNCONFIRMED" for item in result["materials"]["candidates"])

def test_closed_analysis_can_be_persisted_inside_q1_existing_website(owned):
    client,headers,order_id,maker=owned
    analysis=client.post(endpoint(order_id),headers=headers,json={"url":"https://example.test"}).json()
    payload=q1();payload["existing_website"]["analysis"]=analysis
    saved=client.patch(f"/api/orders/{order_id}/q1",headers=headers,json=payload)
    assert saved.status_code==200
    with maker() as db:
        order=db.get(__import__("app.models.order",fromlist=["Order"]).Order,order_id)
        nested=order.brief_answers["q1_v2"]["existing_website"]["analysis"]
        assert nested["contract_version"]=="existing_website_analysis_v2" and nested["order_id"]==order_id
