import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api", tags=["website-analysis"])


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
        self._in_title = False
        self._inside_nav = False

    def handle_starttag(self, tag, attrs):
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

    def handle_endtag(self, tag):
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
    if not parsed.netloc:
        return ""

    return urllib.parse.urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path or "/",
        "",
        parsed.query,
        "",
    ))


def fetch_html(url: str, timeout: int = 8) -> Dict[str, object]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; SiteFormoAnalyzer/1.0; "
            "+https://siteformo.com)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context()

    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read(450_000)
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
    count = 0

    for link in links:
        if not link:
            continue

        if link.startswith("#") or link.startswith("mailto:") or link.startswith("tel:"):
            continue

        absolute = urllib.parse.urljoin(url, link)
        link_domain = urllib.parse.urlparse(absolute).netloc.replace("www.", "")

        if link_domain == domain:
            count += 1

    return count


def classify_site(url: str, html: str, reachable: bool, parser: BasicHTMLSignalsParser) -> Dict[str, object]:
    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")
    title = " ".join(parser.title_parts[:2])
    description = " ".join(parser.meta_descriptions[:2])

    visible_text = re.sub(r"<[^>]+>", " ", html or "")
    visible_text = re.sub(r"\s+", " ", visible_text)
    combined = f"{domain} {url} {title} {description} {visible_text[:120000]}".lower()

    blocked_marketplace_keywords = [
        "amazon", "airbnb", "booking.com", "uber", "ebay", "aliexpress",
        "multi-vendor", "multivendor", "seller marketplace", "vendor dashboard",
        "ride sharing", "delivery marketplace", "marketplace platform",
    ]

    advanced_platform_keywords = [
        "dashboard", "workspace", "portal", "crm", "erp", "saas",
        "api integration", "developer api", "automation platform",
        "client portal", "admin panel", "team management",
        "workflow automation", "user management", "subscription platform",
        "web app", "mobile app", "login to your account",
    ]

    commerce_keywords = [
        "cart", "checkout", "basket", "add to cart", "shop now",
        "woocommerce", "shopify", "product category", "inventory",
        "payment method", "shipping", "returns",
    ]

    booking_keywords = [
        "book online", "booking system", "appointment", "calendar",
        "schedule a call", "availability", "reservation",
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
        "elegantthemes", "framer", "webflow",
    ]

    local_business_keywords = [
        "services", "contact us", "about us", "our team", "testimonials",
        "get a quote", "request a quote", "free consultation",
        "local", "ireland", "galway", "dublin",
    ]

    found_blocked = contains_any(combined, blocked_marketplace_keywords)
    found_advanced = contains_any(combined, advanced_platform_keywords)
    found_commerce = contains_any(combined, commerce_keywords)
    found_booking = contains_any(combined, booking_keywords)
    found_auth = contains_any(combined, auth_keywords)
    found_premium = contains_any(combined, premium_visual_keywords)
    found_local = contains_any(combined, local_business_keywords)

    internal_links = same_domain_link_count(url, parser.links)
    nav_links = len(set(parser.nav_links))

    score_visual = 0
    score_functional = 0
    score_structure = 0

    score_visual += min(len(found_premium) * 2, 10)
    score_visual += 2 if parser.scripts_count >= 12 else 0
    score_visual += 2 if parser.stylesheets_count >= 8 else 0

    score_functional += min(len(found_advanced) * 4, 20)
    score_functional += min(len(found_commerce) * 2, 12)
    score_functional += min(len(found_booking) * 3, 12)
    score_functional += 3 if len(found_auth) >= 2 else 0
    score_functional += 2 if parser.forms_count >= 2 else 0
    score_functional += 2 if parser.inputs_count >= 8 else 0

    score_structure += 2 if internal_links >= 20 else 0
    score_structure += 4 if internal_links >= 60 else 0
    score_structure += 2 if nav_links >= 8 else 0
    score_structure += 3 if parser.headings_count >= 20 else 0

    blocked = False
    recommended_package = "business"
    risk_level = "low"
    reason = "business_website"
    client_message = (
        "This looks suitable for a business website review. "
        "The detailed questionnaire will confirm final pages and features."
    )

    if found_blocked:
        blocked = True
        recommended_package = "blocked"
        risk_level = "blocked"
        reason = "marketplace_or_startup_platform"
        client_message = (
            "SiteFormo currently does not develop startup-level marketplaces, "
            "multi-vendor platforms or enterprise systems similar to this reference. "
            "Please choose a simpler business website reference."
        )

    elif score_functional >= 10 or len(found_advanced) >= 2:
        recommended_package = "advanced"
        risk_level = "high"
        reason = "advanced_functionality_or_platform_logic"
        client_message = (
            "This reference appears to include advanced functionality, portal, "
            "SaaS, dashboard or system-level behaviour. It should be reviewed under "
            "the Advanced package flow."
        )

    elif found_commerce and (len(found_commerce) >= 3 or score_functional >= 7):
        recommended_package = "advanced"
        risk_level = "high"
        reason = "commerce_or_checkout_scope"
        client_message = (
            "This reference appears to include ecommerce or checkout behaviour. "
            "It should be reviewed under the Advanced package flow."
        )

    elif score_visual >= 5 or "elegantthemes" in domain or "divi" in combined:
        recommended_package = "reference"
        risk_level = "medium"
        reason = "premium_visual_or_reference_level_structure"
        client_message = (
            "This reference appears visually advanced or premium-level. "
            "It should be reviewed under the Reference package flow."
        )

    elif score_structure >= 5:
        recommended_package = "reference"
        risk_level = "medium"
        reason = "large_content_structure"
        client_message = (
            "This reference appears to have a larger content structure. "
            "It should be reviewed under the Reference package flow."
        )

    elif "landing" in combined and internal_links < 15 and score_functional < 5:
        recommended_package = "starter"
        risk_level = "low"
        reason = "simple_landing_page"
        client_message = (
            "This looks close to a simple landing page. "
            "The Starter flow may be suitable."
        )

    elif found_local and internal_links < 30 and score_functional < 6:
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
            "visual_score": score_visual,
            "functional_score": score_functional,
            "structure_score": score_structure,
            "matched_blocked": found_blocked[:10],
            "matched_advanced": found_advanced[:10],
            "matched_commerce": found_commerce[:10],
            "matched_booking": found_booking[:10],
            "matched_auth": found_auth[:10],
            "matched_premium_visual": found_premium[:10],
            "matched_local_business": found_local[:10],
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
            signals={},
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

    return WebsiteAnalysisResponse(
        input_url=payload.url,
        normalized_url=normalized,
        reachable=bool(fetched.get("reachable")),
        blocked=bool(classification["blocked"]),
        recommended_package=str(classification["recommended_package"]),
        risk_level=str(classification["risk_level"]),
        reason=str(classification["reason"]),
        client_message=str(classification["client_message"]),
        signals=signals,
    )
