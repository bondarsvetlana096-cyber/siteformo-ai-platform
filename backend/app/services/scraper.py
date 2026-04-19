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


def _detect_language(text: str) -> str:
    if not text:
        return 'en'
    cyr = sum(1 for ch in text if 'а' <= ch.lower() <= 'я' or ch.lower() == 'ё')
    latin = sum(1 for ch in text if 'a' <= ch.lower() <= 'z')
    if cyr > latin * 0.35:
        return 'ru'
    return 'en'


def _is_probably_content_image(src: str, alt: str, classes: str) -> bool:
    probe = f"{src} {alt} {classes}".lower()
    skip_tokens = [
        'logo', 'icon', 'favicon', 'sprite', 'avatar', 'emoji', 'banner-small',
        'facebook', 'instagram', 'whatsapp', 'telegram', 'messenger', 'linkedin',
    ]
    return not any(token in probe for token in skip_tokens)


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
    meta_description = ''
    meta_desc = soup.select_one('meta[name="description"], meta[property="og:description"]')
    if meta_desc and meta_desc.get('content'):
        meta_description = meta_desc.get('content', '').strip()

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

    images = []
    seen = set()
    for node in soup.select('img[src], source[srcset], [style*="background-image"]')[:150]:
        src = ''
        if node.name == 'source':
            srcset = node.get('srcset', '').split(',')[0].strip().split(' ')[0]
            src = srcset
        elif node.get('src'):
            src = node.get('src')
        else:
            style = node.get('style', '')
            match = re.search(r"background-image:\s*url\([\"']?([^\"')]+)", style, re.I)
            if match:
                src = match.group(1)
        if not src:
            continue
        abs_src = urljoin(final_url, src)
        if abs_src in seen:
            continue
        alt = node.get('alt', '') if hasattr(node, 'get') else ''
        classes = ' '.join(node.get('class', [])) if hasattr(node, 'get') else ''
        if not _is_probably_content_image(abs_src, alt, classes):
            continue
        seen.add(abs_src)
        images.append(abs_src)
        if len(images) >= 12:
            break

    all_text = '\n'.join([title, meta_description] + headings + paragraphs)
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

    language = _detect_language(all_text)

    logger.info(
        "[SCRAPER] done url=%s title=%s headings=%s images=%s prices=%s videos=%s language=%s",
        final_url,
        title,
        len(headings),
        len(images),
        len(prices),
        len(videos),
        language,
    )
    return {
        'title': title,
        'meta_description': meta_description,
        'final_url': final_url,
        'language': language,
        'headings': headings,
        'paragraphs': paragraphs,
        'images': images,
        'preserved_facts': preserved_facts,
        'source_html_excerpt': '\n'.join(paragraphs[:30]),
    }
