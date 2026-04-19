from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger("siteformo.scraper")


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

PHONE_RE = re.compile(r"(\+?\d[\d\-\s\(\)]{7,}\d)")
EMAIL_RE = re.compile(r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", re.I)
PRICE_RE = re.compile(
    r"(?:(?:\$|€|£|₽|руб\.?|usd|eur|gbp)\s?\d[\d\s,\.]*|\d[\d\s,\.]*\s?(?:\$|€|£|₽|руб\.?|usd|eur|gbp))",
    re.I,
)


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _detect_language_from_text(text: str) -> str:
    if not text:
        return "en"
    cyr = sum(1 for ch in text if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    if cyr > latin * 0.35:
        return "ru"
    return "en"


def _is_probably_bad_image(url: str) -> bool:
    lowered = url.lower()
    bad_parts = [
        "icon",
        "logo",
        "favicon",
        "sprite",
        "avatar",
        ".svg",
        "data:image",
        "base64,",
    ]
    return any(part in lowered for part in bad_parts)


def _unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _extract_headings(soup: BeautifulSoup) -> list[str]:
    results: list[str] = []
    for tag_name in ["h1", "h2", "h3"]:
        for el in soup.find_all(tag_name):
            text = _clean_text(el.get_text(" ", strip=True))
            if 3 <= len(text) <= 160:
                results.append(text)
    return _unique_keep_order(results)


def _extract_paragraphs(soup: BeautifulSoup) -> list[str]:
    results: list[str] = []
    for el in soup.find_all(["p", "li"]):
        text = _clean_text(el.get_text(" ", strip=True))
        if 30 <= len(text) <= 400:
            results.append(text)
    return _unique_keep_order(results)


def _extract_links(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for el in soup.find_all("a", href=True):
        href = _clean_text(el.get("href"))
        if not href:
            continue
        full_href = urljoin(base_url, href)
        parsed = urlparse(full_href)
        if parsed.scheme not in {"http", "https", "mailto", "tel"}:
            continue
        text = _clean_text(el.get_text(" ", strip=True))
        links.append({
            "href": full_href,
            "text": text[:160],
        })
    dedup: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in links:
        key = item["href"]
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
    return dedup[:20]


def _extract_images(soup: BeautifulSoup, base_url: str) -> list[str]:
    images: list[str] = []

    for el in soup.find_all("img"):
        for attr in ["src", "data-src", "data-original", "data-lazy-src"]:
            raw = el.get(attr)
            if not raw:
                continue
            full = urljoin(base_url, raw.strip())
            if _is_probably_bad_image(full):
                continue
            images.append(full)
            break

    for el in soup.find_all("meta"):
        prop = (el.get("property") or el.get("name") or "").strip().lower()
        if prop in {"og:image", "twitter:image"}:
            content = el.get("content")
            if content:
                full = urljoin(base_url, content.strip())
                if not _is_probably_bad_image(full):
                    images.append(full)

    return _unique_keep_order(images)[:12]


def _extract_videos(soup: BeautifulSoup, base_url: str) -> list[str]:
    videos: list[str] = []

    for el in soup.find_all(["video", "source"]):
        src = el.get("src")
        if src:
            videos.append(urljoin(base_url, src.strip()))

    for el in soup.find_all("iframe", src=True):
        src = el["src"].strip()
        full = urljoin(base_url, src)
        if any(host in full.lower() for host in ["youtube", "vimeo", "loom"]):
            videos.append(full)

    return _unique_keep_order(videos)[:10]


def _extract_phones(text: str) -> list[str]:
    results = [_clean_text(m.group(1)) for m in PHONE_RE.finditer(text)]
    cleaned = [x for x in results if len(re.sub(r"\D", "", x)) >= 8]
    return _unique_keep_order(cleaned)[:10]


def _extract_emails(text: str) -> list[str]:
    results = [_clean_text(m.group(1)) for m in EMAIL_RE.finditer(text)]
    return _unique_keep_order(results)[:10]


def _extract_prices(text: str) -> list[str]:
    results = [_clean_text(m.group(0)) for m in PRICE_RE.finditer(text)]
    return _unique_keep_order(results)[:20]


def _extract_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        return _clean_text(soup.title.string)
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return _clean_text(og["content"])
    return ""


def _extract_meta_description(soup: BeautifulSoup) -> str:
    for attrs in [
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"},
    ]:
        el = soup.find("meta", attrs=attrs)
        if el and el.get("content"):
            return _clean_text(el["content"])
    return ""


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    return _clean_text(soup.get_text(" ", strip=True))


async def scrape_site(url: str) -> dict[str, Any]:
    logger.info("[SCRAPER] start url=%s", url)

    timeout = httpx.Timeout(20.0, connect=10.0)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }

    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        response = await client.get(url)

    response.raise_for_status()

    final_url = str(response.url)
    html_text = response.text

    soup = BeautifulSoup(html_text, "html.parser")

    title = _extract_title(soup)
    meta_description = _extract_meta_description(soup)
    headings = _extract_headings(soup)
    paragraphs = _extract_paragraphs(soup)
    images = _extract_images(soup, final_url)
    links = _extract_links(soup, final_url)
    videos = _extract_videos(soup, final_url)

    page_text = _visible_text(soup)
    phones = _extract_phones(page_text)
    emails = _extract_emails(page_text)
    prices = _extract_prices(page_text)

    language = _detect_language_from_text(
        " ".join(
            [
                title,
                meta_description,
                *headings[:10],
                *paragraphs[:10],
            ]
        )
    )

    result: dict[str, Any] = {
        "requested_url": url,
        "final_url": final_url,
        "title": title,
        "meta_description": meta_description,
        "language": language,
        "headings": headings[:20],
        "paragraphs": paragraphs[:30],
        "images": images,
        "preserved_facts": {
            "links": links,
            "phones": phones,
            "emails": emails,
            "prices": prices,
            "videos": videos,
        },
        "raw_text_excerpt": page_text[:4000],
    }

    logger.info(
        "[SCRAPER] done final_url=%s title=%s headings=%s paragraphs=%s images=%s phones=%s emails=%s prices=%s videos=%s language=%s",
        final_url,
        title,
        len(headings),
        len(paragraphs),
        len(images),
        len(phones),
        len(emails),
        len(prices),
        len(videos),
        language,
    )

    return result