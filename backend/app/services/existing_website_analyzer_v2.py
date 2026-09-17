from __future__ import annotations

import gzip, hashlib, http.client, ipaddress, json, re, socket, ssl, time, uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.schemas.existing_website_analysis import *

ANALYZER_VERSION="2.1.0"; FETCH_POLICY_VERSION="2"
MAX_PAGES=12; MAX_SITEMAP_ENTRIES=200; MAX_DISCOVERED=200; MAX_DEPTH=2; MAX_CONCURRENCY=3
MAX_REDIRECTS=3; REQUEST_TIMEOUT_SECONDS=5; WALL_SECONDS=25
MAX_COMPRESSED_PAGE=1_048_576; MAX_DECOMPRESSED_PAGE=2_097_152; MAX_SITEMAP_BODY=1_048_576
MAX_TOTAL_COMPRESSED=8_388_608; MAX_TOTAL_DECOMPRESSED=16_777_216
SAFE_PORTS={None,80,443}; INTERNAL_SUFFIXES=(".internal",".localhost",".local",".railway.internal",".supabase.internal")

class UnsafeURL(ValueError): pass
class FetchFailure(RuntimeError):
    def __init__(self,code:FailureCode): self.code=code
@dataclass(frozen=True)
class PinnedTarget: url:str; hostname:str; addresses:tuple[str,...]
@dataclass(frozen=True)
class FetchResult: status:int; content_type:str; body:bytes; compressed_bytes:int; location:str|None=None
Resolver=Callable[[str],list[str]]; Transport=Callable[[PinnedTarget,int,int],FetchResult]
def _hash(v): return hashlib.sha256(str(v).encode("utf-8","ignore")).hexdigest()
def _now(): return datetime.now(timezone.utc)

def normalize_and_pin(raw:str,resolver:Resolver)->PinnedTarget:
    value=(raw or "").strip()
    if not re.match(r"^https?://",value,re.I): value="https://"+value
    try: p=urlsplit(value); port=p.port
    except ValueError: raise UnsafeURL("unsafe_url") from None
    if p.scheme.lower() not in {"http","https"} or p.username or p.password or not p.hostname or port not in SAFE_PORTS: raise UnsafeURL("unsafe_url")
    host=p.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    if host=="localhost" or host.endswith(INTERNAL_SUFFIXES): raise UnsafeURL("unsafe_url")
    try: addresses=[str(ipaddress.ip_address(host))]
    except ValueError:
        if "." not in host or re.fullmatch(r"(?:0x[0-9a-f]+|0[0-7]+|\d+)",host,re.I): raise UnsafeURL("unsafe_url")
        try: addresses=list(dict.fromkeys(resolver(host)))
        except Exception: raise FetchFailure(FailureCode.DNS_FAILURE) from None
    if not addresses: raise FetchFailure(FailureCode.DNS_FAILURE)
    for raw_ip in addresses:
        try: ip=ipaddress.ip_address(raw_ip)
        except ValueError: raise UnsafeURL("unsafe_url") from None
        if not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified: raise UnsafeURL("unsafe_url")
        if ip.version==4 and ip in ipaddress.ip_network("100.64.0.0/10"): raise UnsafeURL("unsafe_url")
    netloc=host if port is None else f"{host}:{port}"
    return PinnedTarget(urlunsplit((p.scheme.lower(),netloc,p.path or "/",p.query,"")),host,tuple(addresses))

def system_resolver(host): return [x[4][0] for x in socket.getaddrinfo(host,None,type=socket.SOCK_STREAM)]
def pinned_transport(target,max_compressed,max_decompressed):
    p=urlsplit(target.url); ip=target.addresses[0]; port=p.port or (443 if p.scheme=="https" else 80)
    try:
        sock=socket.create_connection((ip,port),timeout=REQUEST_TIMEOUT_SECONDS)
        if p.scheme=="https": sock=ssl.create_default_context().wrap_socket(sock,server_hostname=target.hostname)
        conn=http.client.HTTPConnection(target.hostname,port=port,timeout=REQUEST_TIMEOUT_SECONDS); conn.sock=sock
        conn.request("GET",urlunsplit(("","",p.path or "/",p.query,"")),headers={"Host":target.hostname,"User-Agent":"SiteFormoAnalyzerV2/2.1","Accept":"text/html,application/xhtml+xml,application/xml,text/xml,text/plain","Accept-Encoding":"gzip"})
        response=conn.getresponse(); raw=response.read(max_compressed+1); conn.close()
    except ssl.SSLError: raise FetchFailure(FailureCode.TLS_ERROR) from None
    except (TimeoutError,socket.timeout): raise FetchFailure(FailureCode.CONNECT_TIMEOUT) from None
    if len(raw)>max_compressed: raise FetchFailure(FailureCode.OVERSIZED_RESPONSE)
    if response.getheader("Content-Encoding","").lower()=="gzip":
        try:
            import io
            body=gzip.GzipFile(fileobj=io.BytesIO(raw)).read(max_decompressed+1)
        except Exception: raise FetchFailure(FailureCode.MALFORMED_CONTENT) from None
    else: body=raw
    if len(body)>max_decompressed: raise FetchFailure(FailureCode.OVERSIZED_RESPONSE)
    return FetchResult(response.status,response.getheader("Content-Type","") or "",body,len(raw),response.getheader("Location"))

class Signals(HTMLParser):
    def __init__(self):
        super().__init__(); self.title=[]; self.meta=""; self.links=[]; self.headings=[]; self.buttons=[]; self.forms=[]; self.inputs=[]; self.images=[]; self.videos=[]; self.lang=""; self.header=False; self.footer=False; self.nav=False; self.scripts=0; self.json_ld=[]; self.text=[]; self._capture=None; self._link=None
    def handle_starttag(self,tag,attrs):
        a={k.lower():v or "" for k,v in attrs}; t=tag.lower()
        if t=="html": self.lang=a.get("lang","")[:20]
        if t in {"title","h1","h2","h3","button"}: self._capture=t
        if t=="script":
            self.scripts+=1
            if "ld+json" in a.get("type","").lower(): self._capture="jsonld"
        if t=="meta" and a.get("name","").lower()=="description": self.meta=a.get("content","")[:500]
        if t=="a" and a.get("href"): self.links.append((a["href"],"")); self._link=len(self.links)-1; self._capture="link"
        if t=="form": self.forms.append(a)
        if t in {"input","select","textarea"}: self.inputs.append(a)
        if t=="img" and a.get("src"): self.images.append(a)
        if t in {"video","iframe","source"} and (a.get("src") or a.get("data-src")): self.videos.append(a.get("src") or a.get("data-src"))
        if t=="link" and "icon" in a.get("rel","").lower() and a.get("href"): self.images.append({"src":a["href"],"class":"favicon"})
        if t=="header": self.header=True
        if t=="footer": self.footer=True
        if t=="nav": self.nav=True
    def handle_data(self,data):
        v=re.sub(r"\s+"," ",data).strip()
        if not v:return
        self.text.append(v)
        if self._capture=="title": self.title.append(v)
        elif self._capture and self._capture.startswith("h"): self.headings.append(v[:200])
        elif self._capture=="button": self.buttons.append(v[:100])
        elif self._capture=="link" and self._link is not None:
            h,old=self.links[self._link]; self.links[self._link]=(h,(old+" "+v).strip()[:100])
        elif self._capture=="jsonld": self.json_ld.append(v[:10000])
    def handle_endtag(self,tag):
        if tag.lower() in {"title","h1","h2","h3","button","a","script"}: self._capture=None; self._link=None

def _origin(url): p=urlsplit(url); return p.scheme,p.hostname,p.port
def _clean(url,base,origin,assets=False):
    p=urlsplit(urljoin(base,url))
    if p.scheme not in {"http","https"} or (p.scheme,p.hostname,p.port)!=origin:return None
    if not assets and re.search(r"\.(?:jpg|jpeg|png|gif|webp|svg|css|js|woff2?|ttf|mp4|mp3|zip|pdf|docx?|xlsx?)(?:$|\?)",p.path,re.I):return None
    return urlunsplit((p.scheme,p.netloc,p.path or "/",p.query,""))
def _fetch(url,origin,resolver,transport,max_c=MAX_COMPRESSED_PAGE,max_d=MAX_DECOMPRESSED_PAGE):
    target=normalize_and_pin(url,resolver)
    for n in range(MAX_REDIRECTS+1):
        result=transport(target,max_c,max_d)
        if result.status not in {301,302,303,307,308}:
            if result.status==403: raise FetchFailure(FailureCode.ACCESS_RESTRICTED)
            if result.status==429: raise FetchFailure(FailureCode.RATE_LIMITED_REMOTE)
            return target.url,result
        if n==MAX_REDIRECTS: raise FetchFailure(FailureCode.TOO_MANY_REDIRECTS)
        if not result.location: raise FetchFailure(FailureCode.UNSAFE_REDIRECT)
        target=normalize_and_pin(urljoin(target.url,result.location),resolver)
        if _origin(target.url)!=origin: raise FetchFailure(FailureCode.UNSAFE_REDIRECT)
    raise FetchFailure(FailureCode.TOO_MANY_REDIRECTS)

def _sitemaps(root,origin,resolver,transport):
    candidates=[urljoin(root,"/sitemap.xml")]; urls=[]; total_c=total_d=0; found=False
    try:
        _,r=_fetch(urljoin(root,"/robots.txt"),origin,resolver,transport,MAX_SITEMAP_BODY,MAX_SITEMAP_BODY); total_c+=r.compressed_bytes; total_d+=len(r.body)
        if "text/plain" in r.content_type.lower() or not r.content_type:
            for x in re.findall(r"(?im)^\s*sitemap\s*:\s*(\S+)",r.body.decode("utf-8","ignore")):
                safe=_clean(x,root,origin)
                if safe and safe not in candidates:candidates.append(safe)
    except (UnsafeURL,FetchFailure): pass
    try:
        for sm in candidates[:10]:
            final,r=_fetch(sm,origin,resolver,transport,MAX_SITEMAP_BODY,MAX_SITEMAP_BODY); total_c+=r.compressed_bytes; total_d+=len(r.body)
            if r.status>=400:continue
            if "xml" not in r.content_type.lower() and not r.body.lstrip().startswith(b"<?xml"):continue
            found=True; root_xml=ET.fromstring(r.body); locs=[n.text.strip() for n in root_xml.iter() if n.tag.rsplit("}",1)[-1]=="loc" and n.text]
            if root_xml.tag.rsplit("}",1)[-1]=="sitemapindex":
                child_locs=[]
                for child in locs[:20]:
                    safe=_clean(child,final,origin)
                    if not safe:continue
                    _,cr=_fetch(safe,origin,resolver,transport,MAX_SITEMAP_BODY,MAX_SITEMAP_BODY); total_c+=cr.compressed_bytes; total_d+=len(cr.body)
                    if "xml" not in cr.content_type.lower() and not cr.body.lstrip().startswith(b"<?xml"):continue
                    cx=ET.fromstring(cr.body); child_locs.extend(n.text.strip() for n in cx.iter() if n.tag.rsplit("}",1)[-1]=="loc" and n.text)
                locs=child_locs
            for loc in locs:
                safe=_clean(loc,final,origin)
                if safe and safe not in urls and len(urls)<MAX_SITEMAP_ENTRIES:urls.append(safe)
            if len(urls)>=MAX_SITEMAP_ENTRIES:break
    except UnsafeURL:return "UNSAFE",urls,total_c,total_d
    except FetchFailure as e:return ("UNSAFE" if e.code==FailureCode.UNSAFE_REDIRECT else "UNAVAILABLE"),urls,total_c,total_d
    except (ET.ParseError,UnicodeError):return "UNAVAILABLE",urls,total_c,total_d
    return ("FOUND" if found else "ABSENT"),urls,total_c,total_d

def _role(path):
    p=path.lower()
    for k,r in (("contact","CONTACT"),("about","ABOUT"),("service","SERVICES"),("categor","SERVICES"),("product","PRODUCT"),("shop","PRODUCT"),("book","BOOKING"),("account","ACCOUNT"),("login","ACCOUNT"),("blog","BLOG"),("news","BLOG"),("privacy","LEGAL"),("terms","LEGAL"),("cookie","LEGAL")):
        if k in p:return r
    return "HOME" if p in {"","/"} else "OTHER"
ROLE_PRIORITY={"HOME":0,"CONTACT":1,"ABOUT":2,"SERVICES":3,"PRODUCT":4,"BOOKING":5,"ACCOUNT":6,"BLOG":7,"LEGAL":8,"OTHER":9}
def _rank(url):return ROLE_PRIORITY[_role(urlsplit(url).path)],url

def analyze_existing_website(order_id,raw_url,resolver,transport):
    started=_now(); clock=time.monotonic(); input_hash=_hash(raw_url.strip())
    try:root=normalize_and_pin(raw_url,resolver)
    except UnsafeURL:return _empty(order_id,input_hash,started,"REFUSED_UNSAFE",FailureCode.UNSAFE_URL)
    except FetchFailure as e:return _empty(order_id,input_hash,started,"UNAVAILABLE",e.code)
    origin=_origin(root.url); sm_status,sm_urls,total_c,total_d=_sitemaps(root.url,origin,resolver,transport)
    discovered=list(dict.fromkeys([root.url]+sm_urls))[:MAX_DISCOVERED]; depths={root.url:0,**{u:min(2,max(1,urlsplit(u).path.count("/"))) for u in sm_urls}}
    parsers=[]; attempted=set(); failure=None
    while len(parsers)<MAX_PAGES and time.monotonic()-clock<WALL_SECONDS:
        batch=sorted((u for u in discovered if u not in attempted),key=_rank)[:min(MAX_CONCURRENCY,MAX_PAGES-len(parsers))]
        if not batch:break
        attempted.update(batch); completed=[]
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
            futures={pool.submit(_fetch,u,origin,resolver,transport):u for u in batch}
            for f in as_completed(futures):
                u=futures[f]
                try:
                    final,r=f.result()
                    if "html" not in r.content_type.lower():raise FetchFailure(FailureCode.NON_HTML)
                    p=Signals();p.feed(r.body.decode("utf-8","ignore"));completed.append((u,final,p,r,depths.get(u,0)))
                except UnsafeURL:failure=FailureCode.UNSAFE_REDIRECT
                except FetchFailure as e:failure=e.code
                except (TimeoutError,socket.timeout):failure=FailureCode.READ_TIMEOUT
                except Exception:failure=FailureCode.INTERNAL_FAILURE
        for u,final,p,r,depth in sorted(completed,key=lambda x:_rank(x[0])):
            total_c+=r.compressed_bytes;total_d+=len(r.body)
            if total_c>MAX_TOTAL_COMPRESSED or total_d>MAX_TOTAL_DECOMPRESSED:failure=FailureCode.OVERSIZED_RESPONSE;break
            parsers.append((final,p,depth))
            if depth<MAX_DEPTH:
                for href,_ in p.links:
                    clean=_clean(href,final,origin)
                    if clean and clean not in discovered and len(discovered)<MAX_DISCOVERED:discovered.append(clean);depths[clean]=depth+1
        if failure==FailureCode.OVERSIZED_RESPONSE:break
    status="UNAVAILABLE" if not parsers else "PARTIAL" if failure or time.monotonic()-clock>=WALL_SECONDS else "COMPLETE"
    return _build(order_id,input_hash,root.url,started,status,failure,parsers,discovered,sm_status)

def _ev(code,path,role,kind,value,confidence=Confidence.MEDIUM):
    eid=_hash(f"{code}|{path}|{value}");return EvidenceRecordV2(evidence_id=eid,code=code,source_page_id=_hash(path),source_path=path,source_page_role=role,observation_type=kind,observed_value=value,confidence=confidence),eid
def _candidate(v,eid,c=Confidence.MEDIUM):return CandidateV2(value=str(v)[:500],confidence=c,evidence_ids=[eid])
def _empty(order_id,input_hash,started,status,code):
    b=ComplexityBandV2(band="INCONCLUSIVE",confidence=Confidence.INCONCLUSIVE)
    return ExistingWebsiteAnalysisV2(analyzer_version=ANALYZER_VERSION,fetch_policy_version=FETCH_POLICY_VERSION,analysis_id=str(uuid.uuid4()),order_id=order_id,status=status,input_url_hash=input_hash,normalized_url=None,started_at=started,completed_at=_now(),safe_failure_code=code,confidence=Confidence.INCONCLUSIVE,crawl=CrawlSummaryV2(sitemap_status="NOT_CHECKED",discovered_page_count=0,verified_crawled_pages=0,estimated_site_scale="INCONCLUSIVE",maximum_observed_depth=0,crawl_truncated=False),business=BusinessEvidenceV2(),structure=StructureEvidenceV2(template_group_count=0,header_observed=False,footer_observed=False,navigation_observed=False),functions=[],content=ContentEvidenceV2(),materials=MaterialEvidenceV2(),technologies=[],complexity=ComplexityEvidenceV2(structural=b,functional=b,content=b,visual=b),clarification_flags=[],evidence=[])

FUNCTION_SIGNALS={
"contact_form":(("contact","form"),("message","email")),"enquiry_form":(("enquiry","submit"),("quote","form")),"newsletter_signup":(("newsletter","email"),("subscribe","email")),"booking_reservation":(("book","appointment"),("reservation","calendar")),"catalogue":(("catalog","product"),("collection","product")),"transactional_ecommerce":(("shop","checkout"),("product","add to cart")),"cart":(("cart","add to"),),"checkout":(("checkout","payment"),),"product_search":(("search","product"),),"product_filters":(("filter","product"),("sort by","category")),"site_search":(("search","site"),("type=search","search")),"account_login":(("login","password"),("sign in","account")),"registration":(("register","password"),("create account","email")),"membership":(("member","login"),("membership","account")),"subscription":(("subscription","billing"),("subscribe","plan")),"file_upload":(("type=file","upload"),),"online_payment":(("payment","card"),("stripe","checkout")),"map_location":(("map","location"),("google maps","address")),"chat_widget":(("live chat","message"),("intercom","chat")),"multilingual_controls":(("language","locale"),("translate","language")),"gallery_portfolio":(("gallery","portfolio"),("project","gallery")),"blog_editorial":(("blog","article"),("news","post")),"reviews_testimonials":(("testimonial","review"),("rating","customer")),"calculator_configurator":(("calculator","calculate"),("configurator","configure")),"dashboard":(("dashboard","account"),("analytics","dashboard")),"customer_portal":(("portal","login"),("client area","account")),"saas_application":(("dashboard","subscription"),("app","login")),"marketplace_multi_vendor":(("seller","buyer"),("vendor","marketplace")),"user_generated_content":(("upload","profile"),("post a","account")),"api_integration_indicator":(("api","integration"),("connect","webhook"))}

def _build(order_id,input_hash,normalized,started,status,failure,parsers,discovered,sitemap_status):
    evidence=[];business=BusinessEvidenceV2();content=ContentEvidenceV2();materials=[];technologies=[];corpus=[];paths=[];roles=[];nav=[];fingerprints=set();maxdepth=0;js_shell=False
    for url,p,depth in parsers:
        path=urlsplit(url).path or "/";role=_role(path);paths.append(path);maxdepth=max(maxdepth,depth)
        text=" ".join(p.text);signals=(text+" "+" ".join(h for h,_ in p.links)+" "+" ".join(" ".join(f"{k}={v}" for k,v in x.items()) for x in p.inputs+p.forms)).lower();corpus.append(signals)
        fingerprints.add(_hash(f"{p.header}|{p.footer}|{p.nav}|{len(p.forms)}|{len(p.headings)}|{len(p.links)//5}")[:16])
        e,i=_ev("PAGE_ROLE",path,role,"ROUTE",role,Confidence.HIGH);evidence.append(e);roles.append(_candidate(role,i,Confidence.HIGH))
        if p.title:
            title=" ".join(p.title)[:200];e,i=_ev("PAGE_TITLE",path,role,"METADATA",title,Confidence.HIGH);evidence.append(e)
            if business.page_title is None:business.page_title=_candidate(title,i,Confidence.HIGH)
            if not business.brand_names:business.brand_names.append(_candidate(re.split(r"[|\-–—]",title)[0].strip(),i))
        if p.meta:
            e,i=_ev("META_DESCRIPTION",path,role,"METADATA",p.meta,Confidence.HIGH);evidence.append(e)
            if business.meta_description is None:business.meta_description=_candidate(p.meta,i,Confidence.HIGH)
            if role=="HOME":business.activity_candidates.append(_candidate(p.meta,i))
            content.short_descriptions.append(_candidate(p.meta,i))
        for h in p.headings[:max(0,20-len(content.headings))]:
            e,i=_ev("VISIBLE_HEADING",path,role,"TEXT_LABEL",h);evidence.append(e);content.headings.append(_candidate(h,i))
            if role=="SERVICES":content.service_names.append(_candidate(h,i));business.service_categories.append(_candidate(h,i))
            if role=="PRODUCT":content.product_categories.append(_candidate(h,i));business.product_categories.append(_candidate(h,i))
            if role=="ABOUT" and len(content.about_summary_candidates)<5:content.about_summary_candidates.append(_candidate(h,i))
            if "faq" in h.lower() or h.endswith("?"):content.faq_questions.append(_candidate(h,i))
        if p.lang:
            e,i=_ev("HTML_LANGUAGE",path,role,"METADATA",p.lang,Confidence.HIGH);evidence.append(e)
            if p.lang not in [x.value for x in business.languages]:business.languages.append(_candidate(p.lang,i,Confidence.HIGH))
        for href,label in p.links:
            absolute=urljoin(url,href);lower=absolute.lower()
            if label and len(content.navigation_labels)<20:e,i=_ev("NAVIGATION_LABEL",path,role,"LINK",label);evidence.append(e);content.navigation_labels.append(_candidate(label,i))
            clean=_clean(href,url,_origin(normalized))
            if clean and len(nav)<50:e,i=_ev("NAVIGATION_DESTINATION",path,role,"ROUTE",urlsplit(clean).path);evidence.append(e);nav.append(_candidate(urlsplit(clean).path,i))
            if lower.startswith(("mailto:","tel:")):
                e,i=_ev("PUBLIC_CONTACT",path,role,"LINK",href);evidence.append(e);c=_candidate(href,i);business.public_contacts.append(c);content.public_contact_candidates.append(c)
            if any(d in lower for d in ("facebook.com","instagram.com","linkedin.com","youtube.com","x.com","twitter.com","tiktok.com")):
                e,i=_ev("SOCIAL_PROFILE",path,role,"LINK",absolute);evidence.append(e);c=_candidate(absolute,i);business.social_links.append(c);content.social_profiles.append(c);materials.append(MaterialCandidateV2(material_id=_hash("SOCIAL"+absolute),kind="SOCIAL_PROFILE",source_page_id=_hash(path),reference=absolute,evidence_ids=[i]))
            if re.search(r"\.(?:pdf|docx?|xlsx?)(?:$|\?)",urlsplit(absolute).path,re.I):e,i=_ev("DOCUMENT_REFERENCE",path,role,"LINK",absolute);evidence.append(e);materials.append(MaterialCandidateV2(material_id=_hash("DOC"+absolute),kind="DOCUMENT",source_page_id=_hash(path),reference=absolute,evidence_ids=[i]))
        for label in p.buttons[:max(0,20-len(content.cta_labels))]:e,i=_ev("CTA_LABEL",path,role,"TEXT_LABEL",label);evidence.append(e);content.cta_labels.append(_candidate(label,i))
        if role=="LEGAL":e,i=_ev("LEGAL_PAGE",path,role,"ROUTE",path,Confidence.HIGH);evidence.append(e);content.legal_page_presence.append(_candidate(path,i,Confidence.HIGH))
        for img in p.images[:10]:
            ref=urljoin(url,img.get("src",""));combined=(img.get("class","")+img.get("alt","")).lower();kind="FAVICON" if img.get("class")=="favicon" else "LOGO" if "logo" in combined else "PRODUCT_IMAGE" if role=="PRODUCT" else "SERVICE_IMAGE" if role=="SERVICES" else "GALLERY_IMAGE" if "gallery" in combined else "HERO_IMAGE"
            e,i=_ev("MATERIAL_REFERENCE",path,role,"ELEMENT",ref);evidence.append(e);materials.append(MaterialCandidateV2(material_id=_hash(kind+ref),kind=kind,source_page_id=_hash(path),reference=ref,evidence_ids=[i]))
        for ref in p.videos[:5]:ref=urljoin(url,ref);e,i=_ev("VIDEO_REFERENCE",path,role,"ELEMENT",ref);evidence.append(e);materials.append(MaterialCandidateV2(material_id=_hash("VIDEO"+ref),kind="VIDEO_EMBED",source_page_id=_hash(path),reference=ref,evidence_ids=[i]))
        for raw in p.json_ld:
            try:objs=json.loads(raw);objs=objs if isinstance(objs,list) else [objs]
            except Exception:continue
            for obj in objs:
                if not isinstance(obj,dict):continue
                if obj.get("@type"):e,i=_ev("STRUCTURED_ORGANIZATION_TYPE",path,role,"STRUCTURED_DATA",str(obj["@type"]));evidence.append(e);business.structured_organization_types.append(_candidate(str(obj["@type"]),i))
                for field,target in (("address",business.published_locations),("areaServed",business.published_service_areas)):
                    if obj.get(field):v=obj[field] if isinstance(obj[field],str) else json.dumps(obj[field],sort_keys=True);e,i=_ev("PUBLISHED_"+field.upper(),path,role,"STRUCTURED_DATA",v[:500]);evidence.append(e);target.append(_candidate(v,i))
        js_shell=js_shell or (p.scripts>=2 and len(text)<80 and not p.headings)
    alltext=" ".join(corpus);observations=[];flags=[];flagmap={"transactional_ecommerce":"CONFIRM_EXISTING_ECOMMERCE_REQUIRED","cart":"CONFIRM_EXISTING_ECOMMERCE_REQUIRED","checkout":"CONFIRM_EXISTING_ECOMMERCE_REQUIRED","booking_reservation":"CONFIRM_EXISTING_BOOKING_REQUIRED","account_login":"CONFIRM_EXISTING_ACCOUNT_REQUIRED","customer_portal":"CONFIRM_EXISTING_PORTAL_REQUIRED","multilingual_controls":"CONFIRM_EXISTING_MULTILINGUAL_REQUIRED","marketplace_multi_vendor":"CONFIRM_EXISTING_MARKETPLACE_REQUIRED","saas_application":"CONFIRM_EXISTING_PLATFORM_REQUIRED"}
    for key,alts in FUNCTION_SIGNALS.items():
        matched=next((s for s in alts if all(x in alltext for x in s)),None)
        hint=next((x for signals in alts for x in signals if x in alltext),None)
        state=ObservationState.OBSERVED if matched and any(alltext.count(x)>=2 for x in matched) else ObservationState.LIKELY if matched or hint else ObservationState.NOT_OBSERVED;ids=[]
        if matched is None and hint: matched=(hint,)
        if state!=ObservationState.NOT_OBSERVED:e,i=_ev("FUNCTION_"+key.upper(),paths[0] if paths else "/","HOME","COUNT",",".join(matched));evidence.append(e);ids=[i];f=flagmap.get(key);flags.append(f) if f and f not in flags else None
        observations.append(FunctionObservationV2(function_key=key,observation=state,confidence=Confidence.HIGH if state==ObservationState.OBSERVED else Confidence.MEDIUM,evidence_ids=ids,clarification_recommended=key in flagmap and state!=ObservationState.NOT_OBSERVED))
    if content.headings:flags.append("CONFIRM_CONTENT_MIGRATION_SCOPE")
    if materials:flags.append("CONFIRM_MATERIAL_REUSE_RIGHTS")
    for tech,needle in (("wordpress","wp-content"),("woocommerce","woocommerce"),("shopify","cdn.shopify"),("webflow","webflow"),("wix","wixstatic"),("squarespace","squarespace"),("framer","framer"),("elementor","elementor"),("divi","et_pb_")):
        if needle in alltext:e,i=_ev("TECHNOLOGY_"+tech.upper(),paths[0] if paths else "/","HOME","PLATFORM_SIGNATURE",tech);evidence.append(e);technologies.append(TechnologyObservationV2(technology=tech,confidence=Confidence.MEDIUM,evidence_ids=[i],migration_relevance=True))
    count=min(MAX_DISCOVERED,len(set(discovered)));truncated=count>=MAX_DISCOVERED or len(parsers)>=MAX_PAGES;scale="SINGLE_PAGE" if count<=1 else "SMALL" if count<=10 else "MEDIUM" if count<=40 else "LARGE_OR_TRUNCATED";fcount=sum(x.observation!=ObservationState.NOT_OBSERVED for x in observations);ids=[e.evidence_id for e in evidence[:20]]
    category_candidates=[]
    for candidate in roles:
        if candidate.value not in {"HOME","OTHER"} and candidate.value not in [x.value for x in category_candidates]: category_candidates.append(candidate)
    def band(v):return ComplexityBandV2(band=v,confidence=Confidence.MEDIUM,evidence_ids=ids)
    if js_shell:status="PARTIAL";failure=FailureCode.JS_HEAVY_INCONCLUSIVE
    return ExistingWebsiteAnalysisV2(analyzer_version=ANALYZER_VERSION,fetch_policy_version=FETCH_POLICY_VERSION,analysis_id=str(uuid.uuid4()),order_id=order_id,status=status,input_url_hash=input_hash,normalized_url=normalized,started_at=started,completed_at=_now(),safe_failure_code=failure,confidence=Confidence.LOW if js_shell else Confidence.MEDIUM,crawl=CrawlSummaryV2(sitemap_status=sitemap_status,discovered_page_count=count,verified_crawled_pages=len(parsers),estimated_site_scale=scale,maximum_observed_depth=maxdepth,crawl_truncated=truncated,fetched_paths=paths),business=business,structure=StructureEvidenceV2(navigation_destinations=nav[:50],page_roles=roles[:30],major_content_categories=category_candidates[:30],template_group_count=len(fingerprints),header_observed=any(p.header for _,p,_ in parsers),footer_observed=any(p.footer for _,p,_ in parsers),navigation_observed=any(p.nav for _,p,_ in parsers)),functions=observations,content=content,materials=MaterialEvidenceV2(candidates=materials[:50]),technologies=technologies,complexity=ComplexityEvidenceV2(structural=band("HIGH" if count>40 else "MEDIUM" if count>10 else "LOW"),functional=band("HIGH" if fcount>=8 else "MEDIUM" if fcount>=3 else "LOW"),content=band("HIGH" if len(content.headings)>=16 else "MEDIUM" if len(content.headings)>=8 else "LOW"),visual=band("HIGH" if len(materials)>=20 else "MEDIUM" if len(materials)>=5 else "LOW")),clarification_flags=list(dict.fromkeys(flags)),evidence=evidence[:200])
