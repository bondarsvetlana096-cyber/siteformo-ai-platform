from __future__ import annotations

import json
from pathlib import Path

import pytest


SOURCE = Path(__file__).parents[2] / "frontend" / "q1_v2_WPCode.html"
TOKEN = "A" * 43


def test_phase3_bootstrap_contract_is_invisible_and_normal_entry_is_unchanged():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'url.searchParams.get("sfh")' in source
    assert 'history.replaceState(history.state,"",url.pathname+url.search+url.hash)' in source
    assert 'request("/api/journey/handoff"' in source
    assert 'localStorage.setItem(VISITOR_KEY,credential)' in source
    assert 'siteformo_funnel_handoff_context_v1' in source
    assert 'await handoffBootstrap;if(state.journey.order_id)return' in source
    assert "Preparing handoff" not in source and "Transferring" not in source and "Connecting" not in source
    assert "Let’s start with a few details" in source


def test_sfh_redeems_once_scrubs_url_and_refresh_resumes_same_project():
    playwright = pytest.importorskip("playwright.sync_api")
    html = SOURCE.read_text(encoding="utf-8")
    calls = []
    response = {
        "journey_id": "11111111-1111-1111-1111-111111111111",
        "credential": "journey-credential",
        "resumed": False,
        "order_id": "order-electrician",
        "handoff_id": "22222222-2222-2222-2222-222222222222",
        "match_state": "exact",
    }
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script("window.SITEFORMO_API_BASE='https://ie.siteformo.com'")

        def route_handler(route):
            if route.request.url.endswith("/api/journey/handoff"):
                calls.append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=200, content_type="application/json", body=json.dumps(response))
            else:
                route.fulfill(status=200, content_type="text/html", body=html)

        page.route("https://ie.siteformo.com/**", route_handler)
        page.goto(f"https://ie.siteformo.com/?sfh={TOKEN}")
        page.wait_for_function("localStorage.getItem('siteformo_journey_credential_v1') === 'journey-credential'")
        assert page.url == "https://ie.siteformo.com/"
        state = page.evaluate("window.__SITEFORMO_Q1_TEST__.getState()")
        assert state["journey"] == {
            "journey_id": response["journey_id"], "order_id": response["order_id"], "handoff_id": response["handoff_id"]
        }
        assert calls == [{"handoff_token": TOKEN}]
        page.reload(); page.wait_for_function("window.__SITEFORMO_Q1_TEST__.getState().journey.order_id === 'order-electrician'")
        assert len(calls) == 1 and page.url == "https://ie.siteformo.com/"
        browser.close()


def test_receiver_failure_scrubs_token_and_leaves_normal_funnel_usable():
    playwright = pytest.importorskip("playwright.sync_api")
    html = SOURCE.read_text(encoding="utf-8")
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script("window.SITEFORMO_API_BASE='https://ie.siteformo.com'")
        page.route("https://ie.siteformo.com/api/journey/handoff", lambda route: route.fulfill(status=404, body="{}"))
        page.route("https://ie.siteformo.com/**", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        page.goto(f"https://ie.siteformo.com/?sfh={TOKEN}")
        page.wait_for_function("!location.search.includes('sfh')")
        assert page.locator("#sfq-heading").inner_text() == "Let’s start with a few details"
        assert page.locator("[data-continue]").is_enabled()
        browser.close()
