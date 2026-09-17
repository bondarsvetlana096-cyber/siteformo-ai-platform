import pytest
import gzip
import time
from pydantic import ValidationError

from app.schemas.existing_website_analysis import ExistingWebsiteAnalysisV2
from app.services.existing_website_analyzer_v2 import (
    FetchFailure, FetchResult, FailureCode, FUNCTION_SIGNALS, MAX_COMPRESSED_PAGE,
    MAX_DECOMPRESSED_PAGE, UnsafeURL, analyze_existing_website, normalize_and_pin,
    pinned_transport,
)

PUBLIC={"example.test":["93.184.216.34"]}
def resolver(host): return PUBLIC.get(host,["93.184.216.34"])

class FixtureTransport:
    def __init__(self,pages): self.pages=pages; self.calls=[]
    def __call__(self,target,*_):
        self.calls.append((target.url,target.addresses))
        item=self.pages.get(target.url)
        if isinstance(item,Exception): raise item
        status,ctype,body,*rest=item or (404,"text/html",b"")
        return FetchResult(status,ctype,body,len(body),rest[0] if rest else None)

def run(html,pages=None):
    base={"https://example.test/":(200,"text/html",html.encode())}; base.update(pages or {})
    return analyze_existing_website("order-1","https://example.test/",resolver,FixtureTransport(base))

def fn(result,key): return next(x for x in result.functions if x.function_key==key)

def test_closed_contract_and_no_commercial_authority():
    result=run("<html lang=en><title>Acme</title><h1>Plumbing services</h1></html>")
    data=result.model_dump()
    assert result.status=="COMPLETE" and data["business"]["page_title"]["value"]=="Acme"
    assert not ({"recommended_package","price","eligibility","production_risk"}&set(data))
    with pytest.raises(ValidationError): ExistingWebsiteAnalysisV2.model_validate({**data,"price":1})

@pytest.mark.parametrize("url",[
    "http://localhost/","http://127.0.0.1/","http://10.0.0.1/","http://169.254.169.254/",
    "http://100.64.0.1/","http://[::1]/","http://[fc00::1]/","ftp://example.test/",
    "https://user:pass@example.test/","https://example.test:8080/","http://2130706433/",
    "http://0x7f000001/","http://017700000001/",
])
def test_unsafe_targets_never_reach_transport(url):
    calls=[]
    with pytest.raises((UnsafeURL,Exception)): normalize_and_pin(url,lambda h:calls.append(h) or ["93.184.216.34"])

@pytest.mark.parametrize("answer",[["10.0.0.1"],["93.184.216.34","127.0.0.1"],["::1"],["fe80::1"]])
def test_unsafe_or_mixed_dns_fails_closed(answer):
    with pytest.raises(UnsafeURL): normalize_and_pin("https://example.test",lambda _ : answer)

def test_multisignal_functions_clarifications_and_material_non_authority():
    html="""<html lang='en'><title>Shop</title><nav><a href='/shop'>Shop</a><a href='/checkout'>Checkout</a></nav>
    <h1>Shop products</h1><form action='/checkout'><input><button>Checkout</button></form>
    <p>cart checkout booking book login account marketplace marketplace dashboard saas</p>
    <img class='logo' src='/logo.png'><iframe src='https://video.test/v'></iframe></html>"""
    result=run(html,{"https://example.test/shop":(200,"text/html",b"<h1>Products shop cart</h1>"),"https://example.test/checkout":(200,"text/html",b"<h1>Checkout</h1>")})
    assert fn(result,"checkout").observation in {"OBSERVED","LIKELY"}
    assert "CONFIRM_EXISTING_ECOMMERCE_REQUIRED" in result.clarification_flags
    assert "CONFIRM_EXISTING_MARKETPLACE_REQUIRED" in result.clarification_flags
    assert all(x.reuse_authority=="UNCONFIRMED" for x in result.materials.candidates)

def test_keyword_once_is_only_likely_not_observed():
    result=run("<html><p>marketplace</p></html>")
    assert fn(result,"marketplace_multi_vendor").observation=="LIKELY"

def test_js_shell_is_bounded_and_no_q2_or_generator_authority():
    result=run("<html><div id='app'></div><script src='/app.js'></script></html>")
    assert result.crawl.verified_crawled_pages==1
    assert not any(hasattr(x,"confirmed") for x in result.functions)
    assert all(x.reuse_authority=="UNCONFIRMED" for x in result.materials.candidates)

def test_redirect_to_private_makes_zero_private_transport_connections():
    transport=FixtureTransport({"https://example.test/":(302,"text/html",b"","http://127.0.0.1/")})
    result=analyze_existing_website("o","https://example.test/",resolver,transport)
    assert result.safe_failure_code.value=="UNSAFE_URL" or result.safe_failure_code.value=="UNSAFE_REDIRECT"
    assert transport.calls[-1][0]=="https://example.test/"
    assert all("127.0.0.1" not in call[0] for call in transport.calls)

def test_crawl_page_and_discovery_caps():
    links="".join(f"<a href='/p{i}'>p</a>" for i in range(250))
    pages={f"https://example.test/p{i}":(200,"text/html",f"<h1>p{i}</h1>".encode()) for i in range(250)}
    result=run(links,pages)
    assert result.crawl.verified_crawled_pages<=12 and result.crawl.discovered_page_count<=200

def test_non_html_and_timeout_are_safe_failures():
    result=analyze_existing_website("o","https://example.test/",resolver,FixtureTransport({"https://example.test/":(200,"application/pdf",b"pdf")}))
    assert result.status=="UNAVAILABLE" and result.safe_failure_code.value=="NON_HTML"
    result=analyze_existing_website("o","https://example.test/",resolver,FixtureTransport({"https://example.test/":TimeoutError()}))
    assert result.safe_failure_code.value=="READ_TIMEOUT"

def test_sitemap_and_robots_discovery_are_bounded_and_representative():
    sitemap=b'<?xml version="1.0"?><urlset>'+b''.join(f'<url><loc>https://example.test/p{i}</loc></url>'.encode() for i in range(250))+b'</urlset>'
    pages={"https://example.test/robots.txt":(200,"text/plain",b"Sitemap: https://example.test/map.xml"),"https://example.test/sitemap.xml":(404,"text/plain",b""),"https://example.test/map.xml":(200,"application/xml",sitemap)}
    pages.update({f"https://example.test/p{i}":(200,"text/html",f"<h1>p{i}</h1>".encode()) for i in range(200)})
    result=run("<h1>Home</h1>",pages)
    assert result.crawl.sitemap_status=="FOUND"
    assert result.crawl.discovered_page_count==200
    assert result.crawl.verified_crawled_pages==12
    assert result.crawl.crawl_truncated is True

def test_one_level_sitemap_index_and_unsafe_child_is_never_connected():
    index=b'<?xml version="1.0"?><sitemapindex><sitemap><loc>http://127.0.0.1/private.xml</loc></sitemap></sitemapindex>'
    transport=FixtureTransport({"https://example.test/robots.txt":(404,"text/plain",b""),"https://example.test/sitemap.xml":(200,"application/xml",index),"https://example.test/":(200,"text/html",b"<h1>Home</h1>")})
    result=analyze_existing_website("o","https://example.test/",resolver,transport)
    assert result.crawl.sitemap_status in {"FOUND","UNSAFE"}
    assert all("127.0.0.1" not in url for url,_ in transport.calls)

def test_sitemap_redirect_private_and_size_limit_fail_closed():
    transport=FixtureTransport({"https://example.test/robots.txt":(404,"text/plain",b""),"https://example.test/sitemap.xml":(302,"application/xml",b"","http://127.0.0.1/map.xml"),"https://example.test/":(200,"text/html",b"<h1>Home</h1>")})
    result=analyze_existing_website("o","https://example.test/",resolver,transport)
    assert result.crawl.sitemap_status=="UNSAFE" and all("127.0.0.1" not in u for u,_ in transport.calls)
    class SizeBound(FixtureTransport):
        def __call__(self,target,max_c,max_d):
            if target.url.endswith("sitemap.xml"):
                assert max_c==max_d==1_048_576
                raise FetchFailure(FailureCode.OVERSIZED_RESPONSE)
            return super().__call__(target,max_c,max_d)
    result=analyze_existing_website("o","https://example.test/",resolver,SizeBound({"https://example.test/":(200,"text/html",b"<h1>Home</h1>")}))
    assert result.crawl.sitemap_status=="UNAVAILABLE"

def test_js_heavy_shell_is_explicit_partial():
    result=run("<html><div id=app></div><script src=a.js></script><script src=b.js></script></html>")
    assert result.status=="PARTIAL" and result.safe_failure_code==FailureCode.JS_HEAVY_INCONCLUSIVE

def test_every_closed_function_has_detector_output_and_evidence_when_observed():
    phrases=" ".join(" ".join(signals[0])*2 for signals in FUNCTION_SIGNALS.values())
    result=run(f"<html><h1>Functions</h1><p>{phrases}</p></html>")
    assert {x.function_key for x in result.functions}==set(FUNCTION_SIGNALS)
    assert all(x.evidence_ids for x in result.functions if x.observation in {"OBSERVED","LIKELY"})

def test_structural_business_content_and_material_evidence_is_bounded_and_advisory():
    html='''<html lang="en"><head><title>Acme | Services</title><meta name="description" content="Acme repairs homes"><link rel="icon" href="/favicon.ico"><script type="application/ld+json">{"@type":"LocalBusiness","areaServed":"Dublin","address":"1 Test Street"}</script></head><body><header><nav><a href="/about">About</a><a href="mailto:test@example.test">Email</a><a href="https://instagram.com/acme">Instagram</a></nav></header><h1>Repairs</h1><button>Get a quote</button><img class="logo" src="/logo.png"><a href="/guide.pdf">Guide</a><footer>Footer</footer></body></html>'''
    result=run(html,{"https://example.test/about":(200,"text/html",b"<h1>About Acme</h1>")})
    assert result.business.brand_names and result.business.published_locations and result.business.published_service_areas
    assert result.content.navigation_labels and result.content.cta_labels and result.content.social_profiles
    assert {m.kind for m in result.materials.candidates}>={"LOGO","FAVICON","DOCUMENT","SOCIAL_PROFILE"}
    assert all(m.reuse_authority=="UNCONFIRMED" for m in result.materials.candidates)
    known={item.evidence_id for item in result.evidence}
    def references(value):
        if isinstance(value,dict):
            yield from value.get("evidence_ids",[])
            for child in value.values():yield from references(child)
        elif isinstance(value,list):
            for child in value:yield from references(child)
    assert set(references(result.model_dump())).issubset(known)

def test_remote_status_and_tls_failures_are_safe():
    for status,code in ((403,FailureCode.ACCESS_RESTRICTED),(429,FailureCode.RATE_LIMITED_REMOTE)):
        result=analyze_existing_website("o","https://example.test/",resolver,FixtureTransport({"https://example.test/":(status,"text/html",b"")}))
        assert result.safe_failure_code==code
    result=analyze_existing_website("o","https://example.test/",resolver,FixtureTransport({"https://example.test/":FetchFailure(FailureCode.TLS_ERROR)}))
    assert result.safe_failure_code==FailureCode.TLS_ERROR

def test_compressed_and_decompressed_limits_are_passed_to_transport():
    class Limits(FixtureTransport):
        def __call__(self,target,max_c,max_d):
            if target.url.endswith("/"): assert (max_c,max_d)==(MAX_COMPRESSED_PAGE,MAX_DECOMPRESSED_PAGE)
            return super().__call__(target,max_c,max_d)
    result=analyze_existing_website("o","https://example.test/",resolver,Limits({"https://example.test/":(200,"text/html",b"<h1>x</h1>")}))
    assert result.status=="COMPLETE"

@pytest.mark.parametrize("kind",["compressed","decompressed"])
def test_per_page_size_failures_are_safe(kind):
    class PageLimit(FixtureTransport):
        def __call__(self,target,max_c,max_d):
            if target.url.endswith("/"): raise FetchFailure(FailureCode.OVERSIZED_RESPONSE)
            return super().__call__(target,max_c,max_d)
    result=analyze_existing_website("o","https://example.test/",resolver,PageLimit({}))
    assert result.status=="UNAVAILABLE" and result.safe_failure_code==FailureCode.OVERSIZED_RESPONSE

def test_decompression_bomb_is_bounded_before_full_expansion(monkeypatch):
    class Response:
        status=200
        def read(self,n): return gzip.compress(b"x"*(MAX_DECOMPRESSED_PAGE+1))
        def getheader(self,k,default=None): return {"Content-Encoding":"gzip","Content-Type":"text/html"}.get(k,default)
    class Connection:
        sock=None
        def __init__(self,*a,**k): pass
        def request(self,*a,**k): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr("app.services.existing_website_analyzer_v2.socket.create_connection",lambda *a,**k:object())
    monkeypatch.setattr("app.services.existing_website_analyzer_v2.ssl.create_default_context",lambda: type("C",(),{"wrap_socket":lambda self,sock,server_hostname:sock})())
    monkeypatch.setattr("app.services.existing_website_analyzer_v2.http.client.HTTPConnection",Connection)
    with pytest.raises(FetchFailure) as exc: pinned_transport(normalize_and_pin("https://example.test",resolver),MAX_COMPRESSED_PAGE,MAX_DECOMPRESSED_PAGE)
    assert exc.value.code==FailureCode.OVERSIZED_RESPONSE

def test_dns_rebinding_cannot_change_pinned_connection_target():
    resolutions=[["93.184.216.34"],["127.0.0.1"]]
    def changing(_): return resolutions.pop(0)
    target=normalize_and_pin("https://example.test",changing)
    observed=[]
    def transport(p,*_): observed.append(p.addresses); return FetchResult(200,"text/html",b"<h1>x</h1>",10)
    transport(target,1,1)
    assert observed==[("93.184.216.34",)] and resolutions==[["127.0.0.1"]]

def test_total_compressed_and_decompressed_budgets_stop_crawl():
    links="".join(f"<a href='/p{i}'>p{i}</a>" for i in range(11))
    class Totals(FixtureTransport):
        def __call__(self,target,*limits):
            result=super().__call__(target,*limits)
            if "/p" in target.url:return FetchResult(result.status,result.content_type,b"x"*1_600_000,800_000)
            return result
    pages={"https://example.test/":(200,"text/html",links.encode())}
    pages.update({f"https://example.test/p{i}":(200,"text/html",b"") for i in range(11)})
    result=analyze_existing_website("o","https://example.test/",resolver,Totals(pages))
    assert result.status=="PARTIAL" and result.safe_failure_code==FailureCode.OVERSIZED_RESPONSE

def test_concurrent_completion_order_does_not_change_result_order():
    links="<a href='/contact'>c</a><a href='/about'>a</a><a href='/services'>s</a>"
    pages={"https://example.test/":(200,"text/html",links.encode()),"https://example.test/contact":(200,"text/html",b"<h1>Contact</h1>"),"https://example.test/about":(200,"text/html",b"<h1>About</h1>"),"https://example.test/services":(200,"text/html",b"<h1>Services</h1>")}
    class Delayed(FixtureTransport):
        def __call__(self,target,*limits):
            time.sleep({"/contact":.03,"/about":.01,"/services":.02}.get(urlsplit(target.url).path,0))
            return super().__call__(target,*limits)
    from urllib.parse import urlsplit
    first=analyze_existing_website("o","https://example.test/",resolver,Delayed(pages))
    second=analyze_existing_website("o","https://example.test/",resolver,Delayed(pages))
    assert first.crawl.fetched_paths==second.crawl.fetched_paths==["/","/contact","/about","/services"]
