from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from app.core.config import settings

logger = logging.getLogger("siteformo.scraper")

PRICE_RE = re.compile(r'(?:\$|€|£|₽|usd|eur|aed|sar|uah|kzt)\s?\d[\d\s.,]*(?:\s?(?:/mo|/month|monthly|per month|once|year|yr))?', re.I)
EMAIL_RE = re.compile(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}', re.I)
PHONE_RE = re.compile(r'\+?[\d][\d\s().-]{7,}\d')


async def scrape_site(url: str) -> dict:
    logger.info("[SCRAPER] start scraping: %s", url)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.playwright_headless)
        page = await browser.new_page(viewport={'width': 1440, 'height': 2200})
        await page.goto(url, wait_until='domcontentloaded', timeout=settings.playwright_timeout_ms)
        await page.wait_for_timeout(1800)
        html = await page.content()
        title = await page.title()
        final_url = page.url
        await browser.close()

    soup = BeautifulSoup(html, 'lxml')

    headings = [node.get_text(' ', strip=True) for node in soup.select('h1, h2, h3') if node.get_text(' ', strip=True)][:40]
    paragraphs = [node.get_text(' ', strip=True) for node in soup.select('p, li') if node.get_text(' ', strip=True)][:120]

    links = []
    for a in soup.select('a[href]')[:80]:
        href = a.get('href')
        if not href:
            continue
        links.append({'text': a.get_text(' ', strip=True), 'href': urljoin(final_url, href)})

    videos = []
    for node in soup.select('iframe[src], video source[src], video[src]')[:20]:
        src = node.get('src')
        if src:
            videos.append(urljoin(final_url, src))

    all_text = '\n'.join(headings + paragraphs)
    prices = sorted(set(match.group(0).strip() for match in PRICE_RE.finditer(all_text)))[:30]
    emails = sorted(set(match.group(0).strip() for match in EMAIL_RE.finditer(all_text)))[:20]
    phones = sorted(set(match.group(0).strip() for match in PHONE_RE.finditer(all_text)))[:20]

    contact_blocks = []
    for node in soup.select('footer, [class*="contact" i], [id*="contact" i], address')[:20]:
        text = node.get_text(' ', strip=True)
        if text:
            contact_blocks.append(text)
    contact_blocks = contact_blocks[:10]

    pricing_blocks = []
    for node in soup.select('[class*="price" i], [class*="plan" i], [id*="price" i], [id*="plan" i], section, article, div')[:120]:
        text = node.get_text(' ', strip=True)
        if not text:
            continue
        if PRICE_RE.search(text):
            pricing_blocks.append(text[:1000])
        if len(pricing_blocks) >= 12:
            break

    preserved_facts = {
        'prices': prices,
        'emails': emails,
        'phones': phones,
        'videos': videos,
        'links': links,
        'contact_blocks': contact_blocks,
        'pricing_blocks': pricing_blocks,
    }

    logger.info("[SCRAPER] done url=%s title=%s headings=%s prices=%s videos=%s", final_url, title, len(headings), len(prices), len(videos))
    return {
        'title': title,
        'final_url': final_url,
        'headings': headings,
        'paragraphs': paragraphs,
        'preserved_facts': preserved_facts,
        'source_html_excerpt': '\n'.join(paragraphs[:30]),
    }
