import hashlib
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Dict, List, Tuple

from fastapi import APIRouter
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api", tags=["website-analysis"])


# Simple in-memory cache for Railway worker runtime.
# It avoids re-fetching the same website repeatedly during testing or repeated form use.
_ANALYSIS_CACHE: Dict[str, Dict[str, object]] = {}
_CACHE_TTL_SECONDS = 60 * 60 * 24


class WebsiteAnalysisRequest(BaseModel):
    url: str = Field(..., min_length=3, max_length=500)


class WebsiteAnalysisResponse(BaseModel):
    input_url: str
    normalized_url: str
    reachable: bool
    blocked: bool
    recommended_package: str
    risk_level: str
    reason: str
    client_message: str
    signals: Dict[str, object]


class BasicHTMLSignalsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title_parts: List[str] = []
        self.meta_descriptions: List[str] = []
        self.links: List[str] = []
        self.nav_links: List[str] = []
        self.forms_count = 0
        self.inputs_count = 0
        self.buttons_count = 0
        self.scripts_count = 0
        self.stylesheets_count = 0
        self.headings_count = 0
        self.image_count = 0
        self.video_count = 0
        self.canvas_count = 0
        self.svg_count = 0
        self._in_title = False
        self._inside_nav = False

    def handle_starttag(self, tag, attrs):
        tag = str(tag or "").lower()
        attrs_dict = {str(k).lower(): str(v or "") for k, v in attrs}

        if tag == "title":
            self._in_title = True

        if tag == "meta":
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            if name == "description" or prop == "og:description":
                content = attrs_dict.get("content", "")
                if content:
                    self.meta_descriptions.append(content)

        if tag == "nav":
            self._inside_nav = True

        if tag == "a":
            href = attrs_dict.get("href", "")
            if href:
                self.links.append(href)
                if self._inside_nav:
                    self.nav_links.append(href)

        if tag == "form":
            self.forms_count += 1

        if tag == "input":
            self.inputs_count += 1

        if tag == "button":
            self.buttons_count += 1

        if tag == "script":
            self.scripts_count += 1

        if tag == "link":
            rel = attrs_dict.get("rel", "").lower()
            href = attrs_dict.get("href", "").lower()
            if "stylesheet" in rel or href.endswith(".css"):
                self.stylesheets_count += 1

        if tag in {"h1", "h2", "h3"}:
            self.headings_count += 1

        if tag == "img":
            self.image_count += 1

        if tag == "video":
            self.video_count += 1

        if tag == "canvas":
            self.canvas_count += 1

        if tag == "svg":
            self.svg_count += 1

    def handle_endtag(self, tag):
        tag = str(tag or "").lower()
        if tag == "title":
            self._in_title = False
        if tag == "nav":
            self._inside_nav = False

    def handle_data(self, data):
        if self._in_title and data.strip():
            self.title_parts.append(data.strip())


def normalize_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        return ""

    if not re.match(r"^https?://", value, flags=re.I):
        value = "https://" + value

    parsed = urllib.parse.urlparse(value)
    if not parsed.netloc or "." not in parsed.netloc:
        return ""

    return urllib.parse.urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            "",
            parsed.query,
            "",
        )
    )


def cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()


def get_cached(url: str):
    key = cache_key(url)
    item = _ANALYSIS_CACHE.get(key)
    if not item:
        return None

    created_at = float(item.get("_created_at") or 0)
    if time.time() - created_at > _CACHE_TTL_SECONDS:
        _ANALYSIS_CACHE.pop(key, None)
        return None

    cached = dict(item)
    cached.pop("_created_at", None)
    cached["signals"] = dict(cached.get("signals") or {})
    cached["signals"]["cache_hit"] = True
    return cached


def set_cached(url: str, data: Dict[str, object]) -> None:
    if len(_ANALYSIS_CACHE) > 300:
        _ANALYSIS_CACHE.clear()

    item = dict(data)
    item["_created_at"] = time.time()
    _ANALYSIS_CACHE[cache_key(url)] = item


def fetch_html(url: str, timeout: int = 8) -> Dict[str, object]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; SiteFormoAnalyzer/2.0; "
            "+https://siteformo.com)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context()

    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read(650_000)
            text = body.decode("utf-8", errors="ignore")
            return {
                "reachable": True,
                "status_code": getattr(response, "status", 200),
                "content_type": content_type,
                "html": text,
                "error": "",
            }
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, TimeoutError) as exc:
        return {
            "reachable": False,
            "status_code": None,
            "content_type": "",
            "html": "",
            "error": str(exc),
        }


def contains_any(text: str, keywords: List[str]) -> List[str]:
    found = []
    source = text.lower()
    for keyword in keywords:
        if keyword.lower() in source:
            found.append(keyword)
    return found


def same_domain_link_count(url: str, links: List[str]) -> int:
    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    unique_links = set()

    for link in links:
        if not link:
            continue

        if link.startswith("#") or link.startswith("mailto:") or link.startswith("tel:"):
            continue

        absolute = urllib.parse.urljoin(url, link)
        link_domain = urllib.parse.urlparse(absolute).netloc.replace("www.", "")

        if link_domain == domain:
            normalized_path = urllib.parse.urlparse(absolute).path.rstrip("/") or "/"
            unique_links.add(normalized_path)

    return len(unique_links)


def score_matches(matches: List[str], points: int, cap: int) -> int:
    return min(len(matches) * points, cap)


def classify_scores(
    domain: str,
    combined: str,
    parser: BasicHTMLSignalsParser,
    internal_links: int,
    nav_links: int,
) -> Tuple[Dict[str, object], Dict[str, List[str]]]:
    blocked_marketplace_keywords = [
        "amazon", "airbnb", "booking.com", "uber", "ebay", "aliexpress",
        "multi-vendor", "multivendor", "seller marketplace", "vendor dashboard",
        "ride sharing", "delivery marketplace", "marketplace platform",
        "become a seller", "sell on", "host your home", "list your property",
    ]

    advanced_platform_keywords = [
        "dashboard", "workspace", "portal", "crm", "erp", "saas",
        "api integration", "developer api", "automation platform",
        "client portal", "admin panel", "team management",
        "workflow automation", "user management", "subscription platform",
        "web app", "mobile app", "manage your account", "manage projects",
        "permissions", "roles", "user roles", "team members",
    ]

    commerce_keywords = [
        "cart", "checkout", "basket", "add to cart", "shop now",
        "woocommerce", "shopify", "product category", "inventory",
        "payment method", "shipping", "returns", "product filters",
    ]

    booking_keywords = [
        "book online", "booking system", "appointment", "calendar",
        "schedule a call", "availability", "reservation", "reserve",
    ]

    auth_keywords = [
        "log in", "login", "sign in", "signin", "register", "sign up",
        "create account", "my account", "member area", "membership",
    ]

    premium_visual_keywords = [
        "agency", "studio", "creative", "portfolio", "case studies",
        "showcase", "premium", "brand", "editorial", "motion",
        "animation", "interactive", "web design", "theme builder",
        "templates", "layouts", "visual builder", "divi",
        "elegantthemes", "elementor", "framer", "webflow",
    ]

    local_business_keywords = [
        "services", "contact us", "about us", "our team", "testimonials",
        "get a quote", "request a quote", "free consultation",
        "local", "ireland", "galway", "dublin",
    ]

    landing_keywords = [
        "landing page", "hero section", "call to action", "one page",
        "single page", "get started", "learn more",
    ]

    matches = {
        "blocked": contains_any(combined, blocked_marketplace_keywords),
        "advanced": contains_any(combined, advanced_platform_keywords),
        "commerce": contains_any(combined, commerce_keywords),
        "booking": contains_any(combined, booking_keywords),
        "auth": contains_any(combined, auth_keywords),
        "premium_visual": contains_any(combined, premium_visual_keywords),
        "local_business": contains_any(combined, local_business_keywords),
        "landing": contains_any(combined, landing_keywords),
    }

    visual_score = 0
    functional_score = 0
    structure_score = 0
    production_risk_score = 0

    # Visual score: premium brand/site production difficulty.
    visual_score += score_matches(matches["premium_visual"], 2, 14)
    visual_score += 2 if parser.scripts_count >= 12 else 0
    visual_score += 3 if parser.scripts_count >= 25 else 0
    visual_score += 2 if parser.stylesheets_count >= 8 else 0
    visual_score += 2 if parser.image_count >= 20 else 0
    visual_score += 3 if parser.svg_count >= 30 else 0
    visual_score += 4 if parser.video_count >= 1 or parser.canvas_count >= 1 else 0

    # Functional score: application/transaction/workflow difficulty.
    functional_score += score_matches(matches["advanced"], 5, 30)
    functional_score += score_matches(matches["commerce"], 3, 18)
    functional_score += score_matches(matches["booking"], 4, 16)

    # Auth alone should not force Advanced. It only adds risk when paired with platform/commerce/booking signals.
    functional_score += 2 if len(matches["auth"]) >= 2 else 0
    functional_score += 2 if parser.forms_count >= 2 else 0
    functional_score += 3 if parser.inputs_count >= 8 else 0
    functional_score += 2 if parser.buttons_count >= 20 else 0

    # Structure score: pages/content/navigation scale.
    structure_score += 2 if internal_links >= 15 else 0
    structure_score += 3 if internal_links >= 35 else 0
    structure_score += 5 if internal_links >= 75 else 0
    structure_score += 2 if nav_links >= 8 else 0
    structure_score += 3 if nav_links >= 16 else 0
    structure_score += 2 if parser.headings_count >= 20 else 0
    structure_score += 3 if parser.headings_count >= 45 else 0

    # Production risk combines the things that create underpricing/refund danger.
    production_risk_score += 12 if matches["blocked"] else 0
    production_risk_score += functional_score
    production_risk_score += 5 if matches["commerce"] and matches["auth"] else 0
    production_risk_score += 5 if matches["booking"] and matches["auth"] else 0
    production_risk_score += 4 if structure_score >= 8 and functional_score >= 8 else 0

    scores = {
        "visual_complexity_score": visual_score,
        "functional_complexity_score": functional_score,
        "structure_complexity_score": structure_score,
        "production_risk_score": production_risk_score,
    }

    return scores, matches


def classify_site(url: str, html: str, reachable: bool, parser: BasicHTMLSignalsParser) -> Dict[str, object]:
    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")
    title = " ".join(parser.title_parts[:2])
    description = " ".join(parser.meta_descriptions[:2])

    visible_text = re.sub(r"<[^>]+>", " ", html or "")
    visible_text = re.sub(r"\s+", " ", visible_text)
    combined = f"{domain} {url} {title} {description} {visible_text[:180000]}".lower()

    internal_links = same_domain_link_count(url, parser.links)
    nav_links = len(set(parser.nav_links))

    scores, matches = classify_scores(domain, combined, parser, internal_links, nav_links)

    visual_score = int(scores["visual_complexity_score"])
    functional_score = int(scores["functional_complexity_score"])
    structure_score = int(scores["structure_complexity_score"])
    production_risk_score = int(scores["production_risk_score"])

    blocked = False
    recommended_package = "business"
    risk_level = "low"
    reason = "standard_business_website"
    client_message = (
        "This looks close to a standard business website. "
        "The detailed questionnaire will confirm final pages and features."
    )

    # Hard stops: these are not SiteFormo production scope at this stage.
    if matches["blocked"]:
        blocked = True
        recommended_package = "blocked"
        risk_level = "blocked"
        reason = "marketplace_or_startup_platform"
        client_message = (
            "SiteFormo currently does not develop startup-level marketplaces, "
            "multi-vendor platforms or enterprise systems similar to this reference. "
            "Please choose a simpler business website reference."
        )

    # Advanced: true app/platform/system/transaction complexity.
    elif (
        production_risk_score >= 18
        or functional_score >= 14
        or len(matches["advanced"]) >= 2
        or (matches["commerce"] and functional_score >= 10)
        or (matches["booking"] and functional_score >= 10)
    ):
        recommended_package = "advanced"
        risk_level = "high"
        reason = "advanced_functionality_or_system_scope"
        client_message = (
            "This reference appears to include advanced functionality, portal, "
            "SaaS, ecommerce, booking, dashboard or system-level behaviour. "
            "It should be reviewed under the Advanced package flow."
        )

    # Reference: premium visual/marketing/content complexity without heavy system logic.
    elif (
        visual_score >= 8
        or structure_score >= 8
        or "elegantthemes" in domain
        or "elementor" in domain
        or "webflow" in domain
        or "framer" in domain
        or "divi" in combined
    ):
        recommended_package = "reference"
        risk_level = "medium"
        reason = "premium_visual_or_large_content_structure"
        client_message = (
            "This reference appears visually advanced, premium-level or content-heavy. "
            "It should be reviewed under the Reference package flow."
        )

    # Starter: only if clearly small and low risk.
    elif matches["landing"] and internal_links < 15 and functional_score < 5 and structure_score < 5:
        recommended_package = "starter"
        risk_level = "low"
        reason = "simple_landing_page"
        client_message = (
            "This looks close to a simple landing page. "
            "The Starter flow may be suitable."
        )

    # Business: normal multi-section business site.
    elif matches["local_business"] and internal_links < 35 and functional_score < 8:
        recommended_package = "business"
        risk_level = "low"
        reason = "standard_business_website"
        client_message = (
            "This looks close to a standard business website. "
            "The Business flow may be suitable."
        )

    if not reachable:
        blocked = False
        recommended_package = "business"
        risk_level = "unknown"
        reason = "website_not_reachable"
        client_message = (
            "We could not fully read this website automatically. "
            "The detailed questionnaire will ask extra questions to confirm the scope."
        )

    visual_label = "low"
    if visual_score >= 14:
        visual_label = "high"
    elif visual_score >= 8:
        visual_label = "medium"

    functional_label = "low"
    if functional_score >= 14:
        functional_label = "high"
    elif functional_score >= 8:
        functional_label = "medium"

    structure_label = "small"
    if structure_score >= 10:
        structure_label = "large"
    elif structure_score >= 6:
        structure_label = "medium"

    risk_label = "low"
    if production_risk_score >= 18:
        risk_label = "high"
    elif production_risk_score >= 10:
        risk_label = "medium"

    return {
        "blocked": blocked,
        "recommended_package": recommended_package,
        "risk_level": risk_level,
        "reason": reason,
        "client_message": client_message,
        "signals": {
            "domain": domain,
            "title": title,
            "description": description,
            "internal_links_estimate": internal_links,
            "nav_links_estimate": nav_links,
            "forms_count": parser.forms_count,
            "inputs_count": parser.inputs_count,
            "buttons_count": parser.buttons_count,
            "scripts_count": parser.scripts_count,
            "stylesheets_count": parser.stylesheets_count,
            "headings_count": parser.headings_count,
            "image_count": parser.image_count,
            "video_count": parser.video_count,
            "canvas_count": parser.canvas_count,
            "svg_count": parser.svg_count,
            "visual_complexity": visual_label,
            "functional_complexity": functional_label,
            "structure_complexity": structure_label,
            "production_risk": risk_label,
            "visual_complexity_score": visual_score,
            "functional_complexity_score": functional_score,
            "structure_complexity_score": structure_score,
            "production_risk_score": production_risk_score,
            "matched_blocked": matches["blocked"][:10],
            "matched_advanced": matches["advanced"][:10],
            "matched_commerce": matches["commerce"][:10],
            "matched_booking": matches["booking"][:10],
            "matched_auth": matches["auth"][:10],
            "matched_premium_visual": matches["premium_visual"][:10],
            "matched_local_business": matches["local_business"][:10],
            "matched_landing": matches["landing"][:10],
            "rule_version": "siteformo_qualification_v2_score_split",
            "cache_hit": False,
        },
    }


@router.post("/analyze-website", response_model=WebsiteAnalysisResponse)
def analyze_website(payload: WebsiteAnalysisRequest):
    normalized = normalize_url(payload.url)

    if not normalized:
        return WebsiteAnalysisResponse(
            input_url=payload.url,
            normalized_url="",
            reachable=False,
            blocked=False,
            recommended_package="business",
            risk_level="unknown",
            reason="invalid_url",
            client_message=(
                "Please enter a valid website URL. If you are unsure, "
                "continue without a reference and our team will clarify the scope later."
            ),
            signals={"rule_version": "siteformo_qualification_v2_score_split"},
        )

    cached = get_cached(normalized)
    if cached:
        return WebsiteAnalysisResponse(
            input_url=payload.url,
            normalized_url=normalized,
            reachable=bool(cached.get("reachable")),
            blocked=bool(cached.get("blocked")),
            recommended_package=str(cached.get("recommended_package")),
            risk_level=str(cached.get("risk_level")),
            reason=str(cached.get("reason")),
            client_message=str(cached.get("client_message")),
            signals=dict(cached.get("signals") or {}),
        )

    fetched = fetch_html(normalized)
    parser = BasicHTMLSignalsParser()

    html = str(fetched.get("html") or "")
    if html:
        try:
            parser.feed(html)
        except Exception:
            pass

    classification = classify_site(
        url=normalized,
        html=html,
        reachable=bool(fetched.get("reachable")),
        parser=parser,
    )

    signals = dict(classification.get("signals") or {})
    signals["fetch_status_code"] = fetched.get("status_code")
    signals["fetch_content_type"] = fetched.get("content_type")
    signals["fetch_error"] = fetched.get("error")

    response_data = {
        "input_url": payload.url,
        "normalized_url": normalized,
        "reachable": bool(fetched.get("reachable")),
        "blocked": bool(classification["blocked"]),
        "recommended_package": str(classification["recommended_package"]),
        "risk_level": str(classification["risk_level"]),
        "reason": str(classification["reason"]),
        "client_message": str(classification["client_message"]),
        "signals": signals,
    }

    set_cached(normalized, response_data)

    return WebsiteAnalysisResponse(**response_data)
