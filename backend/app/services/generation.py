from __future__ import annotations

import html
import json
import logging
import re
from textwrap import dedent

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger("siteformo.ai")


BASE_RULES = dedent("""
You generate exactly ONE premium one-page landing page.
Output strict JSON only with keys: title, html.
No markdown. No code fences. No analysis. No explanations.

If factual business information is provided, preserve it exactly:
- prices
- phone numbers
- emails
- links
- videos
- contact data
- important offer constraints

Never alter those facts.
Never change the business type or niche.
Never turn the redesign into a generic agency ad.
Use the same language as the source website or business description.
If the source website is a hair salon, it must stay a hair salon.
If the source website is in Russian, the output must be in Russian.
If source images are provided, reuse at least one real source image URL in the page.
Do not invent WhatsApp or Messenger.
The result must feel like a premium custom redesign of the SAME business, not generic AI output.
Use complete HTML with inline CSS and small inline JS only.
Make it responsive and visually impressive.
""")


STYLE_PROMPTS = {
    "luxury_editorial": dedent("""
        You are designing for a high-end luxury brand.
        The page must feel exclusive, elegant, editorial, aspirational, and expensive.
        Use refined typography, strong whitespace, cinematic visual hierarchy, dark/light luxury contrast,
        premium gradients, restrained but rich motion, invitation-based CTA language, prestige-driven copy.
        Headlines should create status and desirability.
        Testimonials should feel sophisticated and premium.
    """).strip(),
    "future_saas": dedent("""
        You are designing for a modern SaaS / AI / software product.
        The page must feel sharp, advanced, clear, product-led, and high-conversion.
        Use clean grids, glow accents, premium dark UI style, crisp typography, visual proof blocks,
        modern cards, smooth reveal motion, strong CTA hierarchy, clarity-first copy.
        Headlines should emphasize transformation, speed, leverage, and business outcomes.
    """).strip(),
    "executive_b2b": dedent("""
        You are designing for a B2B / enterprise / consulting audience.
        The page must feel credible, authoritative, efficient, and premium.
        Use proof-oriented structure, confident hierarchy, cleaner motion, trust blocks, ROI language,
        decision-maker-friendly copy, low-fluff design, premium but restrained visuals.
        Headlines should focus on control, confidence, results, and reduced risk.
    """).strip(),
    "clean_trust": dedent("""
        You are designing for healthcare / wellness / trust-driven services.
        The page must feel calm, clear, warm, trustworthy, and reassuring.
        Use bright clean sections, gentle gradients, clear hierarchy, trust indicators, simplified CTA paths,
        human-centered language, soft motion, approachable visuals.
        Headlines should reduce anxiety and build confidence.
    """).strip(),
    "bold_commerce": dedent("""
        You are designing for ecommerce / product sales.
        The page must feel desirable, vivid, energetic, and conversion-focused.
        Use bold contrast, product-centric sections, urgency, strong offer framing, proof, high-intent CTA buttons,
        persuasive copy, visual emphasis on value and desire.
        Headlines should trigger want and immediate action.
    """).strip(),
    "architectural_premium": dedent("""
        You are designing for real estate / property / architecture.
        The page must feel polished, premium, spacious, aspirational, and high-value.
        Use elegant spacing, large visual moments, premium cards, appointment-driving CTA language,
        rich but controlled gradients, confidence-building copy.
        Headlines should evoke aspiration, place, and lifestyle.
    """).strip(),
    "creative_studio": dedent("""
        You are designing for an agency / studio / creative service.
        The page must feel bold, trend-aware, original, premium, and sharp.
        Use punchy typography, unexpected but tasteful composition, strong section rhythm,
        premium motion, visually distinct CTA moments, high-trust social proof.
        Headlines should feel smart, direct, and creatively magnetic.
    """).strip(),
    "premium_modern": dedent("""
        You are designing a premium modern landing page.
        It must feel polished, persuasive, visually rich, premium, and high-conversion.
        Use layered gradients, premium cards, strong hierarchy, subtle motion, elegant CTA blocks,
        believable testimonials, and clear benefit-driven copy.
    """).strip(),
}


def _escape(value: str | None) -> str:
    return html.escape(value or '', quote=True)


def _detect_language_from_text(text: str) -> str:
    if not text:
        return 'en'
    cyr = sum(1 for ch in text if 'а' <= ch.lower() <= 'я' or ch.lower() == 'ё')
    latin = sum(1 for ch in text if 'a' <= ch.lower() <= 'z')
    if cyr > latin * 0.35:
        return 'ru'
    return 'en'


def _infer_brand_profile(source: dict | None, business_description: str | None) -> dict:
    text_parts: list[str] = []
    if business_description:
        text_parts.append(business_description)
    if source:
        text_parts.extend(source.get('headings', [])[:12])
        text_parts.extend(source.get('paragraphs', [])[:24])
        if source.get('title'):
            text_parts.append(source['title'])
    text = ' '.join(text_parts).lower()

    patterns = [
        ('beauty_salon', r'hair|salon|barber|beauty|stylist|colour|coloring|cut|blowout|парикмах|салон|стриж|окрашив|уклад'),
        ('luxury_editorial', r'luxury|premium|exclusive|boutique|villa|interior|jewelry|aesthetic|spa|fine dining|resort'),
        ('future_saas', r'saas|software|platform|ai|automation|crm|analytics|dashboard|api|cloud|workflow|productivity'),
        ('executive_b2b', r'b2b|enterprise|industrial|procurement|logistics|manufacturing|consulting|compliance|operations'),
        ('clean_trust', r'clinic|health|medical|dental|wellness|therapy|doctor|care|patient'),
        ('bold_commerce', r'shop|store|ecommerce|fashion|product|shipping|cart|collection|shopify|dropshipping'),
        ('architectural_premium', r'real estate|property|apartment|homes|estate|broker|architecture|residence|commercial property'),
        ('creative_studio', r'agency|studio|creative|marketing|branding|design|production|content'),
    ]

    style = 'premium_modern'
    audience = 'broad'
    tone = 'confident, persuasive, elevated'
    visual_direction = 'premium gradients, layered depth, bold typography, subtle motion'
    cta_style = 'strong, aspirational, action-oriented'
    business_type = 'business service'

    for label, pattern in patterns:
        if re.search(pattern, text):
            if label == 'beauty_salon':
                business_type = 'hair salon'
                style = 'luxury_editorial'
            else:
                style = label
            break

    if 'hair salon' == business_type:
        audience = 'local beauty clients'
        tone = 'elegant, warm, confidence-building'
        visual_direction = 'large beauty imagery, premium editorial spacing, warm premium contrast'
        cta_style = 'practical booking-focused'
    elif style == 'luxury_editorial':
        business_type = business_type if business_type != 'business service' else 'premium service'
        audience = 'high-end buyers'
        tone = 'elegant, aspirational, exclusive'
        visual_direction = 'editorial typography, luxury spacing, rich gradients, refined contrast'
        cta_style = 'exclusive, invitation-based'
    elif style == 'future_saas':
        business_type = 'software product'
        audience = 'modern tech buyers'
        tone = 'clear, sharp, confident, outcome-driven'
        visual_direction = 'product-led layouts, clean grids, futuristic glow, smooth motion'
        cta_style = 'direct, high-clarity, high-intent'
    elif style == 'executive_b2b':
        business_type = 'business service'
        audience = 'decision makers'
        tone = 'credible, authoritative, efficient'
        visual_direction = 'structured sections, proof-driven blocks, restrained premium design'
        cta_style = 'trust-first, low-friction'
    elif style == 'clean_trust':
        business_type = 'wellness service'
        audience = 'patients and families'
        tone = 'calm, trustworthy, warm'
        visual_direction = 'soft gradients, bright surfaces, trust signals, clarity'
        cta_style = 'comforting, simple, reassuring'
    elif style == 'bold_commerce':
        business_type = 'consumer product'
        audience = 'shoppers'
        tone = 'desirable, energetic, benefit-driven'
        visual_direction = 'strong product focus, bold cards, contrast, urgency'
        cta_style = 'buy-now, desire-driven'
    elif style == 'architectural_premium':
        business_type = 'property business'
        audience = 'buyers and investors'
        tone = 'aspirational, polished, premium'
        visual_direction = 'large imagery zones, elegant layout, confident whitespace'
        cta_style = 'high-value, appointment-driven'
    elif style == 'creative_studio':
        business_type = 'creative service'
        audience = 'brand-conscious clients'
        tone = 'bold, premium, trend-aware'
        visual_direction = 'high-contrast art direction, punchy sections, smooth transitions'
        cta_style = 'portfolio-like, persuasive'

    raw_text = ' '.join(text_parts)
    language = (source or {}).get('language') or _detect_language_from_text(raw_text)

    return {
        'style': style,
        'audience': audience,
        'tone': tone,
        'visual_direction': visual_direction,
        'cta_style': cta_style,
        'business_type': business_type,
        'language': language,
    }


def _build_system_prompt(style: str) -> str:
    style_prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS['premium_modern'])
    return "\n\n".join([
        BASE_RULES.strip(),
        dedent("""
        Your job is to redesign an EXISTING business page so it becomes clearer, more persuasive, and more premium.
        It must still obviously be the same business.

        Conversion rules:
        - improve headlines aggressively, but keep the same business reality
        - sell outcomes, not vague abstractions
        - remove generic phrasing and empty luxury words
        - increase clarity, trust, and desire
        - reduce friction
        - intensify CTA hierarchy
        - make the offer feel more valuable without changing core facts

        Mandatory structure:
        1. Hero
        2. Services / offer summary
        3. Transformation / benefits
        4. Trust / proof
        5. FAQ or objections
        6. Final CTA
        7. Preserved facts block if provided

        Design rules:
        - premium visual hierarchy
        - strong first screen
        - responsive production-presentable HTML
        - no fake agency pitch about redesigning the website
        - no references to Siteformo inside the generated page itself
        - if real source images exist, use them in hero or gallery
        - preserve links, videos, payment references, contact data, and other factual data exactly
        """).strip(),
        style_prompt,
    ])


def _build_user_prompt(request_type: str, source: dict | None, business_description: str | None, profile: dict) -> str:
    source_summary = None
    if source:
        source_summary = {
            'title': source.get('title'),
            'meta_description': source.get('meta_description'),
            'final_url': source.get('final_url'),
            'language': source.get('language'),
            'headings': source.get('headings', [])[:12],
            'paragraphs': source.get('paragraphs', [])[:18],
            'images': source.get('images', [])[:6],
            'preserved_facts': source.get('preserved_facts', {}),
        }

    payload = {
        'request_type': request_type,
        'brand_profile': profile,
        'business_description': business_description,
        'source': source_summary,
        'requirements': {
            'goal': 'premium redesign of the same business',
            'must_preserve_real_facts': True,
            'page_count': 1,
            'must_be_complete_html': True,
            'must_feel_custom': True,
            'must_include_motion': True,
            'must_upgrade_headlines': True,
            'must_keep_business_type': True,
            'must_keep_source_language': True,
            'must_use_source_images_if_present': True,
        },
        'extra_directions': [
            'Write in the same language as the source material.',
            'Keep the same niche and business type. Do not invent another concept.',
            'Use clear practical CTA copy such as book, call, visit, request, reserve.',
            'If the source is local, mention the location if it is available in the source.',
            'Avoid abstract phrases like sanctuary, legacy, elevated excellence unless clearly supported by the source.',
            'Do not output a site about website design or agency services unless the source business is actually an agency.',
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_candidate_content(response_text: str) -> dict | None:
    try:
        return json.loads(response_text)
    except Exception:
        pass
    match = re.search(r'\{.*\}', response_text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def _score_candidate(candidate: dict, source: dict | None = None, profile: dict | None = None) -> int:
    html_text = str(candidate.get('html', ''))
    lowered = html_text.lower()
    score = 0
    for needle in ['<section', 'hero', 'faq', 'contact', 'cta', 'button', 'hover']:
        if needle in lowered:
            score += 1
    if '<img' in lowered:
        score += 2
    if source:
        for image_url in source.get('images', [])[:3]:
            if image_url and image_url in html_text:
                score += 4
                break
        language = (source.get('language') or (profile or {}).get('language') or 'en')
        if language == 'ru' and re.search(r'[А-Яа-яЁё]', html_text):
            score += 3
    if 'agency' in lowered and (profile or {}).get('business_type') != 'creative service':
        score -= 5
    if 'siteformo' in lowered:
        score -= 4
    return score


def _pick_hero_image(source: dict | None) -> str:
    if not source:
        return ''
    for image in source.get('images', []) or []:
        if image:
            return str(image)
    return ''


def _language_pack(language: str) -> dict[str, str]:
    if language == 'ru':
        return {
            'badge': 'Новый дизайн',
            'primary_cta': 'Оставить заявку',
            'secondary_cta': 'Посмотреть услуги',
            'services': 'Услуги',
            'benefits': 'Почему выбирают нас',
            'proof': 'Почему это удобно',
            'faq': 'Частые вопросы',
            'facts': 'Сохранённые данные',
            'contact': 'Контакты',
            'faq_q1': 'Как записаться?',
            'faq_a1': 'Оставьте заявку через форму или свяжитесь по указанным контактам.',
            'faq_q2': 'Сохраняются ли контакты и цены?',
            'faq_a2': 'Да, ключевые фактические данные сохраняются без изменений.',
            'hero_fallback': 'Современный сайт для вашего бизнеса',
            'hero_sub_fallback': 'Более ясная структура, сильнее оффер и понятный следующий шаг для клиента.',
        }
    return {
        'badge': 'Refreshed design',
        'primary_cta': 'Request a consultation',
        'secondary_cta': 'View services',
        'services': 'Services',
        'benefits': 'Why clients choose this business',
        'proof': 'Why this works',
        'faq': 'Frequently asked questions',
        'facts': 'Preserved facts',
        'contact': 'Contact',
        'faq_q1': 'How do I get started?',
        'faq_a1': 'Use the main contact path on the page to request the service.',
        'faq_q2': 'Are prices and contacts preserved?',
        'faq_a2': 'Yes. Key factual data is kept intact.',
        'hero_fallback': 'A clearer, stronger website for this business',
        'hero_sub_fallback': 'Sharper positioning, better structure, and a more persuasive next step for visitors.',
    }


def _source_guided_fallback(source: dict | None, business_description: str | None, profile: dict) -> dict:
    language = profile.get('language', 'en')
    pack = _language_pack(language)

    title = ''
    if source and source.get('title'):
        title = str(source['title'])
    elif business_description:
        title = business_description[:80]
    else:
        title = pack['hero_fallback']

    headings = source.get('headings', [])[:6] if source else []
    paragraphs = source.get('paragraphs', [])[:10] if source else []
    facts = (source or {}).get('preserved_facts', {})
    image_url = _pick_hero_image(source)

    hero_title = headings[0] if headings else title
    hero_subtitle = paragraphs[0] if paragraphs else pack['hero_sub_fallback']

    service_items = headings[1:4] or paragraphs[1:4] or [business_description or profile.get('business_type', 'service')]
    benefit_items = paragraphs[2:5] or headings[1:4] or [pack['hero_sub_fallback']]

    links = facts.get('links', [])[:3]
    prices = facts.get('prices', [])[:4]
    phones = facts.get('phones', [])[:2]
    emails = facts.get('emails', [])[:2]
    videos = facts.get('videos', [])[:2]

    link_markup = ''.join(
        f'<li><a href="{_escape(item.get("href"))}">{_escape(item.get("text") or item.get("href"))}</a></li>'
        for item in links if item.get('href')
    )
    price_markup = ''.join(f'<li>{_escape(item)}</li>' for item in prices)
    phone_markup = ''.join(f'<li>{_escape(item)}</li>' for item in phones)
    email_markup = ''.join(f'<li>{_escape(item)}</li>' for item in emails)
    video_markup = ''.join(f'<li><a href="{_escape(item)}">{_escape(item)}</a></li>' for item in videos)

    service_markup = ''.join(f'<div class="mini-card"><h3>{_escape(str(item)[:80])}</h3></div>' for item in service_items if item)
    benefit_markup = ''.join(f'<li>{_escape(str(item)[:180])}</li>' for item in benefit_items if item)
    hero_image_markup = f'<img src="{_escape(image_url)}" alt="{_escape(hero_title)}" />' if image_url else ''

    html_doc = f"""<!doctype html>
<html lang="{language}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_escape(title)}</title>
<style>
:root{{--bg:#0f1115;--panel:#171a21;--line:rgba(255,255,255,.12);--text:#f8fafc;--muted:#c9d1df;--accent:#d4a24c;--accent2:#f3d08b}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:Inter,Arial,sans-serif;background:linear-gradient(180deg,#0f1115,#151922 55%,#111318);color:var(--text)}}
a{{color:inherit}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px}}
.hero{{display:grid;grid-template-columns:1.05fr .95fr;gap:32px;align-items:center;min-height:100vh}}
.badge{{display:inline-flex;align-items:center;gap:10px;padding:8px 14px;border-radius:999px;background:rgba(255,255,255,.06);border:1px solid var(--line);color:#fff}}
h1{{font-size:clamp(42px,6vw,78px);line-height:.95;margin:20px 0 16px;letter-spacing:-.04em;max-width:720px}}
.lead{{font-size:clamp(18px,2vw,24px);line-height:1.6;color:var(--muted);max-width:720px}}
.cta-row{{display:flex;gap:14px;flex-wrap:wrap;margin-top:28px}}
.btn{{display:inline-flex;align-items:center;justify-content:center;padding:16px 22px;border-radius:16px;text-decoration:none;font-weight:800}}
.btn.primary{{background:linear-gradient(90deg,var(--accent),var(--accent2));color:#141414}}
.btn.secondary{{border:1px solid var(--line);background:rgba(255,255,255,.04);color:#fff}}
.hero-visual{{min-height:540px;border-radius:28px;overflow:hidden;background:radial-gradient(circle at 40% 30%,rgba(212,162,76,.35),transparent 36%),linear-gradient(135deg,#1c1208,#6b4a18);box-shadow:0 30px 80px rgba(0,0,0,.28)}}
.hero-visual img{{display:block;width:100%;height:100%;object-fit:cover}}
section{{padding:40px 0}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}}
.card,.mini-card{{padding:22px;border-radius:22px;border:1px solid var(--line);background:rgba(255,255,255,.04)}}
.list{{margin:0;padding-left:20px;color:var(--muted);line-height:1.7}}
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
.kicker{{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#f3d08b;margin-bottom:10px;font-weight:700}}
@media (max-width: 900px){{.hero{{grid-template-columns:1fr;min-height:auto;padding:70px 0 24px}}.hero-visual{{min-height:320px}}.grid,.two-col{{grid-template-columns:1fr}}h1{{font-size:46px}}}}
</style>
</head>
<body>
<div class="wrap">
<section class="hero">
  <div>
    <span class="badge">{_escape(pack['badge'])}</span>
    <h1>{_escape(hero_title)}</h1>
    <p class="lead">{_escape(hero_subtitle)}</p>
    <div class="cta-row">
      <a class="btn primary" href="#contact">{_escape(pack['primary_cta'])}</a>
      <a class="btn secondary" href="#services">{_escape(pack['secondary_cta'])}</a>
    </div>
  </div>
  <div class="hero-visual">{hero_image_markup}</div>
</section>
<section id="services">
  <div class="kicker">{_escape(pack['services'])}</div>
  <div class="grid">{service_markup}</div>
</section>
<section>
  <div class="two-col">
    <div class="card">
      <div class="kicker">{_escape(pack['benefits'])}</div>
      <ul class="list">{benefit_markup}</ul>
    </div>
    <div class="card">
      <div class="kicker">{_escape(pack['faq'])}</div>
      <p><strong>{_escape(pack['faq_q1'])}</strong><br />{_escape(pack['faq_a1'])}</p>
      <p><strong>{_escape(pack['faq_q2'])}</strong><br />{_escape(pack['faq_a2'])}</p>
    </div>
  </div>
</section>
<section id="contact">
  <div class="card">
    <div class="kicker">{_escape(pack['facts'])}</div>
    <div class="two-col">
      <div>
        {'<h3>Links</h3><ul class="list">' + link_markup + '</ul>' if link_markup else ''}
        {'<h3>Prices</h3><ul class="list">' + price_markup + '</ul>' if price_markup else ''}
      </div>
      <div>
        {'<h3>Phones</h3><ul class="list">' + phone_markup + '</ul>' if phone_markup else ''}
        {'<h3>Emails</h3><ul class="list">' + email_markup + '</ul>' if email_markup else ''}
        {'<h3>Videos</h3><ul class="list">' + video_markup + '</ul>' if video_markup else ''}
      </div>
    </div>
  </div>
</section>
</div>
</body>
</html>"""

    return {'title': title or 'Siteformo Demo', 'html': html_doc}


def generate_demo_page(request_type: str, source: dict | None = None, business_description: str | None = None) -> dict:
    logger.info('[AI] generating high-conversion page...')
    profile = _infer_brand_profile(source, business_description)
    logger.info('[AI] routed style=%s audience=%s tone=%s language=%s business_type=%s', profile['style'], profile['audience'], profile['tone'], profile['language'], profile['business_type'])

    if not settings.openai_api_key:
        return _source_guided_fallback(source, business_description, profile)

    system_prompt = _build_system_prompt(profile['style'])
    user_prompt = _build_user_prompt(request_type, source, business_description, profile)
    client = OpenAI(api_key=settings.openai_api_key)

    candidates: list[dict] = []
    for _ in range(2):
        response = client.chat.completions.create(
            model=settings.openai_model,
            temperature=0.7,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        content = response.choices[0].message.content or ''
        data = _extract_candidate_content(content)
        if data and data.get('html'):
            candidates.append(data)

    if not candidates:
        logger.info('[AI] generation fallback used')
        return _source_guided_fallback(source, business_description, profile)

    best = max(candidates, key=lambda item: _score_candidate(item, source=source, profile=profile))
    logger.info('[AI] generation complete')
    return {
        'title': str(best.get('title') or 'Siteformo Demo'),
        'html': str(best.get('html') or _source_guided_fallback(source, business_description, profile)['html']),
    }
