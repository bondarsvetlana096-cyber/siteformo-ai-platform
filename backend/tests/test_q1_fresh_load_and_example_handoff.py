from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).parents[2]
Q1=(ROOT/"frontend"/"q1_v2_WPCode.html").read_text(encoding="utf-8")
TRACKING=(ROOT/"frontend"/"example_tracking_engine_WPCode.html").read_text(encoding="utf-8")
TOKEN_A="a"*32
TOKEN_B="b"*32
PREFIX="siteformo_q1_handoff_"
CONTRACT="siteformo_q1_example_handoff_v1"


def browser():
    pw=sync_playwright().start()
    try:return pw,pw.chromium.launch(headless=True)
    except Exception as exc:
        pw.stop();pytest.skip(f"Playwright Chromium unavailable: {exc}")


def test_every_document_load_starts_fresh_and_ignores_old_draft_authority():
    pw,b=browser();page=b.new_page()
    page.route("**/*",lambda route:route.fulfill(status=200,content_type="text/html",body=Q1))
    page.goto("https://q1.test/start/")
    page.evaluate("localStorage.setItem('siteformo_q1_v2_draft',JSON.stringify({flow_version:'q1_v2',schema_version:2,current_step:'complete',journey:{order_id:'old-order'},existing_website:{analysis:{status:'COMPLETE'}}}))")
    page.evaluate("window.__SITEFORMO_Q1_TEST__.setStep('q2')")
    page.reload()
    state=page.evaluate("window.__SITEFORMO_Q1_TEST__.getState()")
    assert state["current_step"]=="intro"
    assert state["journey"]=={"journey_id":None,"order_id":None,"handoff_id":None}
    assert state["existing_website"]["analysis"] is None
    b.close();pw.stop()


def example_html():
    return f'''<body data-example-id="example-viewed"><a class="sf-start-questionnaire" href="" data-select-example="example-a">Start A</a><a class="sf-start-questionnaire" href="" data-select-example="example-b">Start B</a>{TRACKING}</body>'''


def test_exact_live_cta_contract_and_mailbox_schema():
    pw,b=browser();context=b.new_context();page=context.new_page()
    page.route("**/*",lambda r:r.fulfill(status=200,content_type="text/html",body=example_html()))
    page.goto("https://q1.test/examples/")
    page.evaluate("document.addEventListener('click',event=>event.preventDefault())")
    page.get_by_text("Start A").click()
    result=page.evaluate("""() => {
      const link=document.querySelector('a.sf-start-questionnaire[data-select-example="example-a"]');
      const key=Object.keys(localStorage).find(k=>k.startsWith('siteformo_q1_handoff_'));
      return {href:link.getAttribute('href'),target:link.target,rel:link.rel,key,payload:JSON.parse(localStorage.getItem(key))};
    }""")
    assert result["href"]=="/start/"
    assert result["target"].startswith("siteformo-q1-") and len(result["target"].split("-")[-1])==32
    assert "noopener" in result["rel"].split()
    assert set(result["payload"])=={"contract_version","created_at","expires_at","selected_example_id","viewed_examples"}
    assert result["payload"]["contract_version"]==CONTRACT
    assert result["payload"]["expires_at"]-result["payload"]["created_at"]==300000
    assert result["payload"]["selected_example_id"]=="example-a"
    assert all(word not in str(result["payload"]).lower() for word in ("credential","order_id","handoff_id","contact","analysis","package"))
    assert "siteformo_q1_handoff" not in result["href"] and "example-a" not in result["href"]
    context.close();b.close();pw.stop()


def test_example_a_and_b_open_independent_new_tabs_with_one_time_handoffs():
    pw,b=browser();context=b.new_context()
    def route(r):
        r.fulfill(status=200,content_type="text/html",body=Q1 if r.request.url.endswith("/start/") else example_html())
    context.route("**/*",route);origin=context.new_page();origin.goto("https://q1.test/examples/")
    origin.evaluate("localStorage.setItem('siteformo_example_tracking',JSON.stringify({viewed_examples:['example-viewed'],order_id:'old-order'}))")
    with context.expect_page() as info_a:origin.get_by_text("Start A").click()
    a=info_a.value;a.wait_for_load_state();sa=a.evaluate("window.__SITEFORMO_Q1_TEST__.getState()")
    with context.expect_page() as info_b:origin.get_by_text("Start B").click()
    bpage=info_b.value;bpage.wait_for_load_state();sb=bpage.evaluate("window.__SITEFORMO_Q1_TEST__.getState()")
    assert origin.url.endswith("/examples/")
    assert sa["examples_context"]=={"selected_example_id":"example-a","viewed_examples":["example-viewed","example-a"]}
    assert sb["examples_context"]=={"selected_example_id":"example-b","viewed_examples":["example-viewed","example-a","example-b"]}
    assert sa["journey"]["order_id"] is None and sb["journey"]["order_id"] is None
    assert a.url=="https://q1.test/start/" and bpage.url=="https://q1.test/start/"
    assert "example" not in a.url and "example" not in bpage.url
    assert a.evaluate("window.name")=="" and bpage.evaluate("window.name")==""
    assert origin.evaluate("Object.keys(localStorage).filter(k=>k.startsWith('siteformo_q1_handoff_')).length")==0
    tracking=origin.evaluate("JSON.parse(localStorage.getItem('siteformo_example_tracking'))")
    assert tracking["selected_example_id"]=="example-b"
    assert tracking["viewed_examples"]==["example-viewed","example-a","example-b"]
    context.close();b.close();pw.stop()


@pytest.mark.parametrize("kind",["expired","malformed"])
def test_invalid_mailbox_is_rejected_deleted_and_window_name_cleared(kind):
    pw,b=browser();page=b.new_page();page.route("**/*",lambda r:r.fulfill(status=200,content_type="text/html",body=Q1));page.goto("https://q1.test/start/")
    if kind=="expired":
        payload={"contract_version":CONTRACT,"created_at":1,"expires_at":2,"selected_example_id":"example-a","viewed_examples":["example-a"]}
        page.evaluate("([k,v])=>localStorage.setItem(k,JSON.stringify(v))",[PREFIX+TOKEN_A,payload])
    else: page.evaluate("k=>localStorage.setItem(k,'{bad')",PREFIX+TOKEN_A)
    page.evaluate("name=>window.name=name","siteformo-q1-"+TOKEN_A);page.reload()
    state=page.evaluate("window.__SITEFORMO_Q1_TEST__.getState()")
    assert state["examples_context"]=={}
    assert page.evaluate("window.name")==""
    assert page.evaluate("k=>localStorage.getItem(k)",PREFIX+TOKEN_A) is None
    b.close();pw.stop()


def test_stale_sweep_deletes_bad_records_and_preserves_valid_parallel_mailbox():
    pw,b=browser();context=b.new_context();page=context.new_page();page.route("**/*",lambda r:r.fulfill(status=200,content_type="text/html",body=example_html()));page.goto("https://q1.test/examples/")
    timestamp=page.evaluate("Date.now()")
    valid={"contract_version":CONTRACT,"created_at":timestamp,"expires_at":timestamp+300000,"selected_example_id":"example-b","viewed_examples":["example-b"]}
    expired={**valid,"created_at":1,"expires_at":2}
    page.evaluate("([p,a,b])=>{localStorage.setItem(p+'"+TOKEN_A+"',JSON.stringify(a));localStorage.setItem(p+'"+TOKEN_B+"',JSON.stringify(b));localStorage.setItem(p+'malformed','{bad');localStorage.setItem('unrelated','keep')}",[PREFIX,expired,valid])
    page.evaluate("document.addEventListener('click',event=>event.preventDefault())");page.get_by_text("Start A").click()
    assert page.evaluate("k=>localStorage.getItem(k)",PREFIX+TOKEN_A) is None
    assert page.evaluate("k=>localStorage.getItem(k)",PREFIX+"malformed") is None
    assert page.evaluate("k=>localStorage.getItem(k)",PREFIX+TOKEN_B) is not None
    assert page.evaluate("localStorage.getItem('unrelated')")=="keep"
    context.close();b.close();pw.stop()


def test_direct_start_has_no_fabricated_example_context():
    pw,b=browser();context=b.new_context();context.route("**/*",lambda r:r.fulfill(status=200,content_type="text/html",body=Q1));page=context.new_page();page.goto("https://q1.test/start/")
    page.evaluate("localStorage.setItem('siteformo_journey_credential_v1','visitor-only');localStorage.setItem('siteformo_q1_v2_draft',JSON.stringify({current_step:'complete',journey:{order_id:'old-order'}}))")
    second=context.new_page();second.goto("https://q1.test/start/")
    state=page.evaluate("window.__SITEFORMO_Q1_TEST__.getState()")
    second_state=second.evaluate("window.__SITEFORMO_Q1_TEST__.getState()")
    assert state["current_step"]=="intro" and state["examples_context"]=={}
    assert second_state["current_step"]=="intro" and second_state["examples_context"]=={}
    assert second_state["journey"]=={"journey_id":None,"order_id":None,"handoff_id":None}
    assert second.evaluate("localStorage.getItem('siteformo_journey_credential_v1')")=="visitor-only"
    assert page.get_by_text("Example selected").count()==0
    context.close();b.close();pw.stop()
