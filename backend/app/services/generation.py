from __future__ import annotations

import html
import json
import logging
import re
from textwrap import dedent
from urllib.parse import quote
import base64

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
Every final page must contain multiple images that clearly match the client's business theme.
If suitable source images are missing, generate or compose theme-matching replacement visuals instead of leaving the page without images.
Do not invent WhatsApp or Messenger.
The result must feel like a premium custom redesign of the SAME business, not generic AI output.
Use complete HTML with inline CSS and small inline JS only.
Make it responsive and visually impressive.
Design mobile-first for phone screens first, then scale up for tablet and desktop.
Never create horizontal scrolling. Use fluid grids, clamp() typography, and tap-friendly buttons.
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
    return html.escape(value or "", quote=True)


def _looks_like_url(value: str | None) -> bool:
    if not value:
        return False
    value = str(value).strip().lower()
    return value.startswith("http://") or value.startswith("https://") or value.startswith("www.")


def _detect_language_from_text(text: str) -> str:
    if not text:
        return "en"
    cyr = sum(1 for ch in text if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    if cyr > latin * 0.35:
        return "ru"
    return "en"


def _infer_brand_profile(source: dict | None, business_description: str | None) -> dict:
    text_parts: list[str] = []
    if business_description:
        text_parts.append(business_description)
    if source:
        text_parts.extend(source.get("headings", [])[:12])
        text_parts.extend(source.get("paragraphs", [])[:24])
        if source.get("title"):
            text_parts.append(source["title"])
    text = " ".join(text_parts).lower()

    patterns = [
        ("beauty_salon", r"hair|salon|barber|beauty|stylist|colour|coloring|cut|blowout|парикмах|салон|стриж|окрашив|уклад"),
        ("luxury_editorial", r"luxury|premium|exclusive|boutique|villa|interior|jewelry|aesthetic|spa|fine dining|resort"),
        ("future_saas", r"saas|software|platform|ai|automation|crm|analytics|dashboard|api|cloud|workflow|productivity"),
        ("executive_b2b", r"b2b|enterprise|industrial|procurement|logistics|manufacturing|consulting|compliance|operations"),
        ("clean_trust", r"clinic|health|medical|dental|wellness|therapy|doctor|care|patient"),
        ("bold_commerce", r"shop|store|ecommerce|fashion|product|shipping|cart|collection|shopify|dropshipping"),
        ("architectural_premium", r"real estate|property|apartment|homes|estate|broker|architecture|residence|commercial property"),
        ("creative_studio", r"agency|studio|creative|marketing|branding|design|production|content"),
    ]

    style = "premium_modern"
    audience = "broad"
    tone = "confident, persuasive, elevated"
    visual_direction = "premium gradients, layered depth, bold typography, subtle motion"
    cta_style = "strong, aspirational, action-oriented"
    business_type = "business service"
    niche_keywords: list[str] = []
    forbidden_keywords = [
        "strategy",
        "execution",
        "scale faster",
        "optimize operations",
        "business solutions",
        "consultation",
    ]

    for label, pattern in patterns:
        if re.search(pattern, text):
            if label == "beauty_salon":
                business_type = "hair salon"
                style = "luxury_editorial"
                niche_keywords = ["salon", "beauty", "hair", "stylist", "booking", "appointment", "cut", "color"]
                forbidden_keywords = [
                    "strategy",
                    "execution",
                    "scale faster",
                    "optimize operations",
                    "business solutions",
                    "consultation",
                    "agency",
                    "saas",
                    "software",
                    "platform",
                ]
            else:
                style = label
            break

    if business_type == "hair salon":
        audience = "local beauty clients"
        tone = "elegant, warm, confidence-building"
        visual_direction = "large beauty imagery, premium editorial spacing, warm premium contrast"
        cta_style = "practical booking-focused"
    elif style == "luxury_editorial":
        business_type = business_type if business_type != "business service" else "premium service"
        audience = "high-end buyers"
        tone = "elegant, aspirational, exclusive"
        visual_direction = "editorial typography, luxury spacing, rich gradients, refined contrast"
        cta_style = "exclusive, invitation-based"
    elif style == "future_saas":
        business_type = "software product"
        audience = "modern tech buyers"
        tone = "clear, sharp, confident, outcome-driven"
        visual_direction = "product-led layouts, clean grids, futuristic glow, smooth motion"
        cta_style = "direct, high-clarity, high-intent"
    elif style == "executive_b2b":
        business_type = "business service"
        audience = "decision makers"
        tone = "credible, authoritative, efficient"
        visual_direction = "structured sections, proof-driven blocks, restrained premium design"
        cta_style = "trust-first, low-friction"
    elif style == "clean_trust":
        business_type = "wellness service"
        audience = "patients and families"
        tone = "calm, trustworthy, warm"
        visual_direction = "soft gradients, bright surfaces, trust signals, clarity"
        cta_style = "comforting, simple, reassuring"
    elif style == "bold_commerce":
        business_type = "consumer product"
        audience = "shoppers"
        tone = "desirable, energetic, benefit-driven"
        visual_direction = "strong product focus, bold cards, contrast, urgency"
        cta_style = "buy-now, desire-driven"
    elif style == "architectural_premium":
        business_type = "property business"
        audience = "buyers and investors"
        tone = "aspirational, polished, premium"
        visual_direction = "large imagery zones, elegant layout, confident whitespace"
        cta_style = "high-value, appointment-driven"
    elif style == "creative_studio":
        business_type = "creative service"
        audience = "brand-conscious clients"
        tone = "bold, premium, trend-aware"
        visual_direction = "high-contrast art direction, punchy sections, smooth transitions"
        cta_style = "portfolio-like, persuasive"

    raw_text = " ".join(text_parts)
    language = (source or {}).get("language") or _detect_language_from_text(raw_text)

    return {
        "style": style,
        "audience": audience,
        "tone": tone,
        "visual_direction": visual_direction,
        "cta_style": cta_style,
        "business_type": business_type,
        "language": language,
        "niche_keywords": niche_keywords,
        "forbidden_keywords": forbidden_keywords,
    }


def _build_system_prompt(style: str) -> str:
    style_prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS["premium_modern"])
    return "\n\n".join(
        [
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
            - mobile-first responsive layout for phones, then tablet, then desktop
            - include viewport-friendly sizing, fluid images, stacked mobile sections, and tap-friendly controls
            - no fixed-width blocks wider than the viewport and no horizontal scrolling
            - no fake agency pitch about redesigning the website
            - no references to Siteformo inside the generated page itself
            - if real source images exist, use them in hero or gallery
            - preserve links, videos, payment references, contact data, and other factual data exactly
            """).strip(),
            style_prompt,
        ]
    )


def _build_user_prompt(
    request_type: str,
    source: dict | None,
    business_description: str | None,
    profile: dict,
) -> str:
    source_summary = None
    if source:
        source_summary = {
            "title": source.get("title"),
            "meta_description": source.get("meta_description"),
            "final_url": source.get("final_url"),
            "language": source.get("language"),
            "headings": source.get("headings", [])[:12],
            "paragraphs": source.get("paragraphs", [])[:18],
            "images": source.get("images", [])[:6],
            "preserved_facts": source.get("preserved_facts", {}),
        }

    payload = {
        "request_type": request_type,
        "brand_profile": profile,
        "business_description": business_description,
        "source": source_summary,
        "requirements": {
            "goal": "premium redesign of the same business",
            "must_preserve_real_facts": True,
            "page_count": 1,
            "must_be_complete_html": True,
            "must_feel_custom": True,
            "must_include_motion": True,
            "must_upgrade_headlines": True,
            "must_keep_business_type": True,
            "must_keep_source_language": True,
            "prefer_new_theme_matched_images": True,
            "use_source_images_only_if_theme_matched": True,
            "must_contain_theme_matched_images": True,
            "minimum_total_images": 3,
        },
        "extra_directions": [
            "Write in the same language as the source material.",
            "Keep the same niche and business type. Do not invent another concept.",
            "Use clear practical CTA copy such as book, call, visit, request, reserve.",
            "If the source is local, mention the location if it is available in the source.",
            "Avoid abstract phrases like sanctuary, legacy, elevated excellence unless clearly supported by the source.",
            "Do not output a site about website design or agency services unless the source business is actually an agency.",
            "The generated page must be mobile-first responsive and visually correct on phone screens.",
            "Every page must include a hero image and additional theme-matched visuals for services or gallery blocks.",
            "Do not use source images by default. Only use them if they clearly match the theme and quality bar of the redesign.",
            "Do not use generic B2B/SaaS/consulting wording unless that is clearly the source niche.",
        ],
    }
    payload["niche_lock"] = {
        "required_business_type": profile.get("business_type"),
        "required_keywords": profile.get("niche_keywords", []),
        "forbidden_keywords": profile.get("forbidden_keywords", []),
        "instruction": "If the output drifts into a generic SaaS, agency, consulting, or unrelated niche, treat that output as invalid and regenerate internally before answering.",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_candidate_content(response_text: str) -> dict | None:
    try:
        return json.loads(response_text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", response_text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def _count_images(html_text: str) -> int:
    return len(re.findall(r"<img\b", html_text or "", flags=re.I))


def _score_candidate(candidate: dict, source: dict | None = None, profile: dict | None = None) -> int:
    html_text = str(candidate.get("html", ""))
    lowered = html_text.lower()
    score = 0
    for needle in ["<section", "hero", "faq", "contact", "cta", "button", "hover"]:
        if needle in lowered:
            score += 1

    image_count = _count_images(html_text)
    if image_count:
        score += 2 + min(image_count, 4)
    else:
        score -= 18

    for responsive_signal in ["viewport", "@media", "max-width", "width:100%", "clamp(", "grid-template-columns", "flex-wrap"]:
        if responsive_signal in lowered:
            score += 2

    if source:
        for image_url in source.get("images", [])[:4]:
            if image_url and image_url in html_text and _is_theme_matched_source_image(str(image_url), profile or {}):
                score += 3
                break
        language = (source.get("language") or (profile or {}).get("language") or "en")
        if language == "ru" and re.search(r"[А-Яа-яЁё]", html_text):
            score += 3

    niche_keywords = [str(x).lower() for x in ((profile or {}).get("niche_keywords") or [])]
    forbidden_keywords = [str(x).lower() for x in ((profile or {}).get("forbidden_keywords") or [])]
    business_type = str((profile or {}).get("business_type") or "")

    if niche_keywords:
        matches = sum(1 for word in niche_keywords if word in lowered)
        score += min(matches, 6) * 3
        if matches == 0:
            score -= 10

    for word in forbidden_keywords:
        if word and word in lowered:
            score -= 6

    if business_type == "hair salon":
        for bad in [
            "strategy",
            "execution",
            "scale faster",
            "optimize operations",
            "business solutions",
            "enterprise",
            "saas",
            "software",
            "consulting",
            "consultation",
        ]:
            if bad in lowered:
                score -= 8
        for good in ["book", "appointment", "stylist", "salon", "hair", "beauty", "cut", "color"]:
            if good in lowered:
                score += 2

    if not _page_has_theme_images(html_text, profile or {}, source=source):
        score -= 20

    if "agency" in lowered and business_type != "creative service":
        score -= 8
    if "siteformo" in lowered:
        score -= 4
    return score


def _pick_hero_image(source: dict | None) -> str:
    if not source:
        return ""
    for image in source.get("images", []) or []:
        if image:
            return str(image)
    return ""




def _generate_image_data_uri(client: OpenAI | None, prompt: str, fallback_title: str, fallback_caption: str, fallback_palette: str) -> str:
    if client and settings.openai_api_key:
        try:
            response = client.images.generate(
                model='gpt-image-1',
                prompt=(
                    'Create a premium, photorealistic website hero image. '
                    'No text, no logos, no UI, no watermark. '
                    'Match this business theme exactly: ' + prompt
                ),
                size='1536x1024',
            )
            data = None
            if getattr(response, 'data', None):
                item = response.data[0]
                data = getattr(item, 'b64_json', None) or (item.get('b64_json') if isinstance(item, dict) else None)
            if data:
                return 'data:image/png;base64,' + data
        except Exception as exc:
            logger.warning('[AI] image generation failed, using fallback svg: %s', exc)
    return _svg_data_uri(fallback_title, fallback_caption, fallback_palette)


def _build_image_generation_prompt(profile: dict, business_description: str | None, source: dict | None, item: dict[str, str]) -> str:
    parts = [
        str(profile.get('business_type') or 'business service'),
        str(profile.get('visual_direction') or ''),
        str(item.get('caption') or ''),
    ]
    if business_description:
        parts.append(str(business_description)[:300])
    if source:
        parts.extend([str(x) for x in (source.get('headings') or [])[:3]])
        if source.get('title'):
            parts.append(str(source.get('title')))
    return '. '.join(part for part in parts if part)
THEME_IMAGE_LIBRARY: dict[str, list[dict[str, str]]] = {
    "hair salon": [
        {"title": "Salon interior", "caption": "Elegant salon interior with mirrors, styling chairs, warm light and premium beauty atmosphere", "palette": "#1f1a17|#c08b5c|#f5dcc3"},
        {"title": "Hair styling", "caption": "Professional hairstylist creating a modern haircut with scissors, combs and glossy hair detail", "palette": "#251a1f|#d26f7b|#f6d3c7"},
        {"title": "Beauty products", "caption": "Premium beauty products and hair care bottles arranged on a salon counter with soft glow", "palette": "#201e28|#8f75ff|#e7defc"},
    ],
    "wellness service": [
        {"title": "Wellness reception", "caption": "Clean welcoming wellness clinic reception with plants, light walls and calming premium details", "palette": "#0f3d46|#5fc5c8|#d5fbf6"},
        {"title": "Care treatment", "caption": "Professional care treatment room with reassuring atmosphere, towels and thoughtful service details", "palette": "#19434b|#7fd0b1|#e9fff4"},
        {"title": "Wellness detail", "caption": "Spa and wellness product close-up with natural textures, stones and premium clean styling", "palette": "#1e3b2f|#8fd2a1|#eff9ed"},
    ],
    "consumer product": [
        {"title": "Product showcase", "caption": "Premium product arranged for ecommerce hero shot with clean background and strong lighting", "palette": "#171a2a|#ff8f4d|#ffe3c7"},
        {"title": "Lifestyle product", "caption": "Lifestyle product photo showing packaging, use case and desirable modern styling", "palette": "#15243a|#50b7ff|#d7f0ff"},
        {"title": "Collection display", "caption": "Collection of featured products displayed in a premium retail composition", "palette": "#2a1f17|#ffb547|#fff0ca"},
    ],
    "property business": [
        {"title": "Modern property", "caption": "Architectural exterior of a premium modern property with elegant lines and large windows", "palette": "#18232f|#7da6c7|#e7f2ff"},
        {"title": "Interior living space", "caption": "Bright premium interior with furniture, natural light and spacious architectural feel", "palette": "#2b211c|#d1a87e|#f8ecd8"},
        {"title": "Property detail", "caption": "Curated architectural detail with materials, textures and luxury finish", "palette": "#1d252d|#8f9fb4|#edf2f8"},
    ],
    "creative service": [
        {"title": "Creative workspace", "caption": "Creative studio workspace with design boards, color samples and premium production mood", "palette": "#27172c|#c56bff|#f6dbff"},
        {"title": "Brand concepts", "caption": "Brand concept presentation table with sketches, materials and vivid studio energy", "palette": "#12253b|#55b6ff|#ddf4ff"},
        {"title": "Production scene", "caption": "Creative production scene with camera, lighting and polished behind the scenes aesthetic", "palette": "#281a14|#ff9b52|#ffe7d3"},
    ],
    "software product": [
        {"title": "Product dashboard", "caption": "Futuristic software dashboard on a sleek device with clear data visualization and glow accents", "palette": "#101828|#6a7bff|#dbe2ff"},
        {"title": "Automation workflow", "caption": "Abstract product workflow interface with connected nodes and premium dark UI style", "palette": "#111827|#00c2ff|#def9ff"},
        {"title": "Platform experience", "caption": "Modern app screens floating in a polished SaaS product composition", "palette": "#14162a|#7c3aed|#ece6ff"},
    ],
    "business service": [
        {"title": "Professional service", "caption": "Professional service environment with premium materials, confident atmosphere and real-world context", "palette": "#1a2231|#61a5ff|#e3efff"},
        {"title": "Client experience", "caption": "Real client-facing service moment showing premium support, welcome and expertise", "palette": "#2a1f1c|#d29b73|#fbe9d9"},
        {"title": "Service details", "caption": "Close-up of service details, tools and environment that clearly communicate the business type", "palette": "#1f2831|#6fd3c4|#e7fff9"},
    ],
}


def _svg_data_uri(title: str, caption: str, palette: str) -> str:
    c1, c2, c3 = (palette.split("|") + ["#1f2937", "#6b7280", "#e5e7eb"])[:3]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900" role="img" aria-label="{_escape(title)}">
  <defs>
    <linearGradient id="bg" x1="0" x2="1" y1="0" y2="1">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="52%" stop-color="{c2}"/>
      <stop offset="100%" stop-color="{c3}"/>
    </linearGradient>
  </defs>
  <rect width="1600" height="900" fill="url(#bg)"/>
  <circle cx="1320" cy="180" r="220" fill="rgba(255,255,255,0.10)"/>
  <circle cx="240" cy="780" r="260" fill="rgba(255,255,255,0.08)"/>
  <rect x="90" y="92" rx="34" ry="34" width="1420" height="716" fill="rgba(15,23,42,0.20)" stroke="rgba(255,255,255,0.18)"/>
  <rect x="150" y="150" rx="28" ry="28" width="520" height="600" fill="rgba(255,255,255,0.10)" stroke="rgba(255,255,255,0.24)"/>
  <rect x="720" y="150" rx="28" ry="28" width="720" height="178" fill="rgba(255,255,255,0.12)" stroke="rgba(255,255,255,0.22)"/>
  <rect x="720" y="368" rx="28" ry="28" width="720" height="178" fill="rgba(255,255,255,0.10)" stroke="rgba(255,255,255,0.20)"/>
  <rect x="720" y="586" rx="28" ry="28" width="720" height="164" fill="rgba(255,255,255,0.14)" stroke="rgba(255,255,255,0.22)"/>
  <text x="210" y="265" fill="#ffffff" font-family="Arial, Helvetica, sans-serif" font-size="78" font-weight="700">{_escape(title)}</text>
  <text x="210" y="350" fill="rgba(255,255,255,0.86)" font-family="Arial, Helvetica, sans-serif" font-size="34">Theme-matched visual</text>
  <text x="210" y="455" fill="rgba(255,255,255,0.92)" font-family="Arial, Helvetica, sans-serif" font-size="32">{_escape(caption[:76])}</text>
  <text x="210" y="500" fill="rgba(255,255,255,0.78)" font-family="Arial, Helvetica, sans-serif" font-size="28">{_escape(caption[76:152])}</text>
  <text x="774" y="230" fill="#ffffff" font-family="Arial, Helvetica, sans-serif" font-size="42" font-weight="700">Client theme required</text>
  <text x="774" y="286" fill="rgba(255,255,255,0.78)" font-family="Arial, Helvetica, sans-serif" font-size="28">Images must match the client's business niche.</text>
  <text x="774" y="448" fill="#ffffff" font-family="Arial, Helvetica, sans-serif" font-size="42" font-weight="700">Mobile-first ready</text>
  <text x="774" y="504" fill="rgba(255,255,255,0.78)" font-family="Arial, Helvetica, sans-serif" font-size="28">Responsive by default, safe for hero, gallery and cards.</text>
  <text x="774" y="660" fill="#ffffff" font-family="Arial, Helvetica, sans-serif" font-size="42" font-weight="700">Generated fallback visual</text>
  <text x="774" y="716" fill="rgba(255,255,255,0.78)" font-family="Arial, Helvetica, sans-serif" font-size="28">Use when source assets are missing or irrelevant.</text>
</svg>"""
    return "data:image/svg+xml;charset=UTF-8," + quote(svg, safe="")


def _is_theme_matched_source_image(image_url: str, profile: dict) -> bool:
    lowered = str(image_url or "").strip().lower()
    if not lowered:
        return False

    blocked_parts = [
        "logo", "icon", "favicon", "avatar", "sprite", "thumb", "thumbnail", "small",
        "placeholder", "banner", "promo", "screenshot", "mockup", "before-after", "before_after",
        "stock", "team", "map", "qr", "svg", "webp?", "data:image",
    ]
    if any(part in lowered for part in blocked_parts):
        return False

    dimension_matches = re.findall(r'(\d{2,4})[xX](\d{2,4})', lowered)
    for w_raw, h_raw in dimension_matches:
        try:
            width = int(w_raw)
            height = int(h_raw)
        except ValueError:
            continue
        if width < 900 or height < 600:
            return False

    business_type = str(profile.get("business_type") or "").lower()
    niche_keywords = [str(x).lower() for x in (profile.get("niche_keywords") or [])]
    thematic_terms = niche_keywords + business_type.split()
    if thematic_terms and any(term and term in lowered for term in thematic_terms):
        return True

    # Allow neutral high-resolution photography folders/paths if they are not obviously wrong.
    trusted_neutral_terms = ["hero", "gallery", "portfolio", "service", "location", "interior"]
    if any(term in lowered for term in trusted_neutral_terms):
        return True

    return False


def _themed_image_assets(
    profile: dict,
    source: dict | None = None,
    allow_source_images: bool = False,
    client: OpenAI | None = None,
    business_description: str | None = None,
) -> list[dict[str, str]]:
    source_images: list[str] = []
    if allow_source_images:
        source_images = [
            str(img)
            for img in ((source or {}).get("images") or [])
            if img and _is_theme_matched_source_image(str(img), profile)
        ][:3]

    business_type = str(profile.get("business_type") or "business service")
    presets = THEME_IMAGE_LIBRARY.get(business_type) or THEME_IMAGE_LIBRARY["business service"]
    themed_assets: list[dict[str, str]] = []
    for idx, item in enumerate(presets):
        use_source = idx < len(source_images)
        themed_assets.append(
            {
                "src": source_images[idx] if use_source else _generate_image_data_uri(client, _build_image_generation_prompt(profile, business_description, source, item), item["title"], item["caption"], item["palette"]),
                "alt": item["caption"],
                "kind": "source" if use_source else "generated",
            }
        )
    return themed_assets


def _page_has_theme_images(html_text: str, profile: dict, source: dict | None = None) -> bool:
    if _count_images(html_text) == 0:
        return False
    for image_url in ((source or {}).get("images") or [])[:3]:
        if image_url and str(image_url) in (html_text or "") and _is_theme_matched_source_image(str(image_url), profile):
            return True
    lowered = (html_text or "").lower()
    themed_signals = [str(x).lower() for x in (profile.get("niche_keywords") or [])]
    business_type = str(profile.get("business_type") or "")
    if business_type == "hair salon":
        themed_signals.extend(["salon", "beauty", "hair", "stylist", "gallery"])
    return any(signal in lowered for signal in themed_signals if signal)


def _language_pack(language: str) -> dict[str, str]:
    return {
        "badge": "Refreshed design",
        "primary_cta": "Request a consultation",
        "secondary_cta": "View services",
        "services": "Services",
        "benefits": "Why clients choose this business",
        "proof": "Why this works",
        "faq": "Frequently asked questions",
        "facts": "Preserved facts",
        "contact": "Contact",
        "faq_q1": "How do I get started?",
        "faq_a1": "Send a request through the form or contact the business directly.",
        "faq_q2": "Are contacts and prices preserved?",
        "faq_a2": "Yes, key factual data is preserved without changes.",
        "hero_fallback": "A modern website for your business",
        "hero_sub_fallback": "Clearer structure, stronger offer, and an easier next step for clients.",
    }
def _source_guided_fallback(source: dict | None, business_description: str | None, profile: dict, client: OpenAI | None = None) -> dict:
    language = profile.get("language", "en")
    pack = _language_pack(language)

    title = ""
    if source and source.get("title") and not _looks_like_url(str(source.get("title"))):
        title = str(source["title"])
    elif business_description and not _looks_like_url(business_description):
        title = business_description[:80]
    else:
        title = pack["hero_fallback"]

    headings = source.get("headings", [])[:6] if source else []
    paragraphs = source.get("paragraphs", [])[:10] if source else []
    facts = (source or {}).get("preserved_facts", {})
    themed_assets = _themed_image_assets(profile, source=source, allow_source_images=True, client=client, business_description=business_description)
    image_url = themed_assets[0]["src"] if themed_assets else _pick_hero_image(source)

    hero_title = headings[0] if headings else title
    hero_subtitle = paragraphs[0] if paragraphs else pack["hero_sub_fallback"]

    service_items = headings[1:4] or paragraphs[1:4] or [business_description or profile.get("business_type", "service")]
    benefit_items = paragraphs[2:5] or headings[1:4] or [pack["hero_sub_fallback"]]

    links = facts.get("links", [])[:3]
    prices = facts.get("prices", [])[:4]
    phones = facts.get("phones", [])[:2]
    emails = facts.get("emails", [])[:2]
    videos = facts.get("videos", [])[:2]

    link_markup = "".join(
        f'<li><a href="{_escape(item.get("href"))}">{_escape(item.get("text") or item.get("href"))}</a></li>'
        for item in links
        if item.get("href")
    )
    price_markup = "".join(f"<li>{_escape(item)}</li>" for item in prices)
    phone_markup = "".join(f"<li>{_escape(item)}</li>" for item in phones)
    email_markup = "".join(f"<li>{_escape(item)}</li>" for item in emails)
    video_markup = "".join(f'<li><a href="{_escape(item)}">{_escape(item)}</a></li>' for item in videos)

    gallery_assets = themed_assets[1:] if len(themed_assets) > 1 else themed_assets
    service_markup = "".join(
        f'<div class="mini-card">'
        + (
            f'<img src="{_escape(gallery_assets[idx % len(gallery_assets)]["src"])}" alt="{_escape(gallery_assets[idx % len(gallery_assets)]["alt"])}" />'
            if gallery_assets
            else ""
        )
        + f"<h3>{_escape(str(item)[:80])}</h3></div>"
        for idx, item in enumerate(service_items)
        if item
    )
    gallery_markup = "".join(
        f'<figure class="gallery-card"><img src="{_escape(asset["src"])}" alt="{_escape(asset["alt"])}" /><figcaption>{_escape(asset["alt"])}</figcaption></figure>'
        for asset in themed_assets[:3]
    )
    benefit_markup = "".join(f"<li>{_escape(str(item)[:180])}</li>" for item in benefit_items if item)
    hero_image_markup = (
        f'<img src="{_escape(image_url)}" alt="{_escape(themed_assets[0]["alt"] if themed_assets else hero_title)}" />'
        if image_url
        else ""
    )

    html_doc = f"""<!doctype html>
<html lang="{language}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_escape(title)}</title>
<style>
:root{{--bg:#090b11;--panel:rgba(255,255,255,.06);--line:rgba(255,255,255,.12);--text:#f8fafc;--muted:#cfd7e6;--accent:#7c3aed;--accent2:#22d3ee;--accent3:#f59e0b}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:Inter,Arial,sans-serif;background:radial-gradient(circle at top,rgba(124,58,237,.22),transparent 22%),radial-gradient(circle at 80% 10%,rgba(34,211,238,.18),transparent 20%),linear-gradient(180deg,#090b11,#0f172a 52%,#10131b);color:var(--text)}}
a{{color:inherit}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px}}
.hero{{display:grid;grid-template-columns:1.02fr .98fr;gap:34px;align-items:center;min-height:100vh;padding-top:34px}}
.badge{{display:inline-flex;align-items:center;gap:10px;padding:10px 16px;border-radius:999px;background:rgba(255,255,255,.08);border:1px solid var(--line);color:#fff;box-shadow:0 16px 38px rgba(0,0,0,.18)}}
h1{{font-size:clamp(42px,6vw,78px);line-height:.95;margin:20px 0 16px;letter-spacing:-.04em;max-width:720px}}
.lead{{font-size:clamp(18px,2vw,24px);line-height:1.6;color:var(--muted);max-width:720px}}
.cta-row{{display:flex;gap:14px;flex-wrap:wrap;margin-top:28px}}
.btn{{display:inline-flex;align-items:center;justify-content:center;padding:16px 22px;border-radius:16px;text-decoration:none;font-weight:800}}
.btn.primary{{background:linear-gradient(90deg,var(--accent),var(--accent2));color:#081018;box-shadow:0 18px 40px rgba(34,211,238,.2)}}
.btn.secondary{{border:1px solid var(--line);background:rgba(255,255,255,.04);color:#fff}}
.hero-visual{{min-height:560px;border-radius:32px;overflow:hidden;background:linear-gradient(135deg,#1d1b32,#15364b);box-shadow:0 34px 100px rgba(0,0,0,.34);border:1px solid rgba(255,255,255,.12)}}
.hero-visual img{{display:block;width:100%;height:100%;object-fit:cover}}
section{{padding:40px 0}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}}
.card,.mini-card,.gallery-card{{padding:22px;border-radius:24px;border:1px solid var(--line);background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.04));backdrop-filter:blur(8px);box-shadow:0 20px 50px rgba(0,0,0,.18)}}
.list{{margin:0;padding-left:20px;color:var(--muted);line-height:1.7}}
.mini-card img,.gallery-card img{{display:block;width:100%;height:auto;aspect-ratio:4/3;object-fit:cover;border-radius:16px;margin-bottom:14px}}
.gallery-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}}
.gallery-card{{padding:14px}}
.gallery-card figcaption{{margin-top:10px;color:var(--muted);font-size:14px;line-height:1.5}}
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
.kicker{{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#f3d08b;margin-bottom:10px;font-weight:700}}
@media (max-width: 900px){{.hero{{grid-template-columns:1fr;min-height:auto;padding:70px 0 24px}}.hero-visual{{min-height:320px}}.grid,.two-col,.gallery-grid{{grid-template-columns:1fr}}h1{{font-size:46px}}}}
</style>
</head>
<body>
<div class="wrap">
<section class="hero">
  <div>
    <span class="badge">{_escape(pack["badge"])}</span>
    <h1>{_escape(hero_title)}</h1>
    <p class="lead">{_escape(hero_subtitle)}</p>
    <div class="cta-row">
      <a class="btn primary" href="#contact">{_escape(pack["primary_cta"])}</a>
      <a class="btn secondary" href="#services">{_escape(pack["secondary_cta"])}</a>
    </div>
  </div>
  <div class="hero-visual">{hero_image_markup}</div>
</section>
<section id="services">
  <div class="card" style="margin-bottom:18px;background:linear-gradient(135deg,rgba(124,58,237,.18),rgba(34,211,238,.10));"><strong>Premium demo preview.</strong> This page is generated for review, contains only the homepage concept, and preserves core business facts from the original source.</div>
  <div class="kicker">{_escape(pack["services"])}</div>
  <div class="grid">{service_markup}</div>
</section>
<section>
  <div class="kicker">Visual preview</div>
  <div class="gallery-grid">{gallery_markup}</div>
</section>
<section>
  <div class="two-col">
    <div class="card">
      <div class="kicker">{_escape(pack["benefits"])}</div>
      <ul class="list">{benefit_markup}</ul>
    </div>
    <div class="card">
      <div class="kicker">{_escape(pack["faq"])}</div>
      <p><strong>{_escape(pack["faq_q1"])}</strong><br />{_escape(pack["faq_a1"])}</p>
      <p><strong>{_escape(pack["faq_q2"])}</strong><br />{_escape(pack["faq_a2"])}</p>
    </div>
  </div>
</section>
<section id="contact">
  <div class="card">
    <div class="kicker">{_escape(pack["facts"])}</div>
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

    return {"title": title or "Siteformo Demo", "html": html_doc}


def generate_demo_page(
    request_type: str,
    source: dict | None = None,
    business_description: str | None = None,
    forced_business_type: str | None = None,
) -> dict:
    logger.info("[AI] generating high-conversion page...")
    profile = _infer_brand_profile(source, business_description)

    forced_business_type = str(forced_business_type or "").strip().lower()
    if forced_business_type:
        profile["business_type"] = forced_business_type

        if forced_business_type in {"salon", "hair salon", "beauty salon", "barbershop", "barber"}:
            profile["business_type"] = "hair salon"
            profile["style"] = "luxury_editorial"
            profile["audience"] = "local beauty clients"
            profile["tone"] = "elegant, warm, confidence-building"
            profile["visual_direction"] = "large beauty imagery, premium editorial spacing, warm premium contrast"
            profile["cta_style"] = "practical booking-focused"
            profile["niche_keywords"] = ["salon", "beauty", "hair", "stylist", "booking", "appointment", "cut", "color"]
            profile["forbidden_keywords"] = [
                "strategy", "execution", "scale faster", "optimize operations", "business solutions",
                "consultation", "agency", "saas", "software", "platform", "enterprise", "consulting",
            ]

    logger.info(
        "[AI] forced business_type=%s resolved profile business_type=%s niche_keywords=%s forbidden_keywords=%s",
        forced_business_type,
        profile.get("business_type"),
        profile.get("niche_keywords"),
        profile.get("forbidden_keywords"),
    )
    logger.info(
        "[AI] routed style=%s audience=%s tone=%s language=%s business_type=%s",
        profile["style"],
        profile["audience"],
        profile["tone"],
        profile["language"],
        profile["business_type"],
    )
    logger.info(
        "[AI] source analysis: title=%s headings=%s paragraphs=%s images=%s niche_keywords=%s forbidden_keywords=%s",
        (source or {}).get("title"),
        len((source or {}).get("headings", []) or []),
        len((source or {}).get("paragraphs", []) or []),
        len((source or {}).get("images", []) or []),
        profile.get("niche_keywords"),
        profile.get("forbidden_keywords"),
    )

    client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
    if not settings.openai_api_key:
        return _source_guided_fallback(source, business_description, profile, client=None)

    system_prompt = _build_system_prompt(profile["style"])
    user_prompt = _build_user_prompt(request_type, source, business_description, profile)
    logger.info("[AI] final generation prompt=%s", user_prompt)

    candidates: list[dict] = []
    try:
        for _ in range(2):
            response = client.chat.completions.create(
                model=settings.openai_model,
                temperature=0.7,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content or ""
            data = _extract_candidate_content(content)
            if data and data.get("html"):
                candidates.append(data)
    except Exception as exc:
        logger.warning("[AI] chat generation failed, using source-guided fallback: %s", exc)
        return _source_guided_fallback(source, business_description, profile, client=client)

    if not candidates:
        logger.info("[AI] generation fallback used")
        return _source_guided_fallback(source, business_description, profile, client=client)

    scored_candidates = [(item, _score_candidate(item, source=source, profile=profile)) for item in candidates]
    for idx, (_, candidate_score) in enumerate(scored_candidates, start=1):
        logger.info("[AI] scoring result candidate=%s score=%s", idx, candidate_score)

    best, best_score = max(scored_candidates, key=lambda pair: pair[1])
    if best_score < 6:
        logger.info("[AI] best candidate score too low (%s), using source-guided fallback", best_score)
        return _source_guided_fallback(source, business_description, profile, client=client)

    best_html = str(best.get("html") or "")
    if not _page_has_theme_images(best_html, profile, source=source) or _count_images(best_html) < 3:
        logger.info("[AI] candidate missing required theme-matched images, using source-guided fallback")
        return _source_guided_fallback(source, business_description, profile, client=client)

    logger.info("[AI] generation complete score=%s images=%s", best_score, _count_images(best_html))
    return {
        "title": str(best.get("title") or "Siteformo Demo"),
        "html": best_html or _source_guided_fallback(source, business_description, profile, client=client)["html"],
    }
