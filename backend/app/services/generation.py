from __future__ import annotations

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
The result must feel like a premium custom agency page, not generic AI output.
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


FALLBACK_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Siteformo Demo</title>
<style>
:root{--bg:#0a0a0f;--panel:rgba(255,255,255,.06);--line:rgba(255,255,255,.12);--text:#fff;--muted:#cdd3ea;--a:#7c3aed;--b:#06b6d4}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font-family:Inter,Arial,sans-serif;background:radial-gradient(circle at top right,rgba(124,58,237,.35),transparent 22%),radial-gradient(circle at left center,rgba(6,182,212,.16),transparent 24%),linear-gradient(180deg,#0b1020,#05070d 55%,#03050a);color:var(--text)}.wrap{max-width:1180px;margin:0 auto;padding:28px}.hero{min-height:100vh;display:flex;align-items:center}.badge{display:inline-block;padding:8px 14px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.05);backdrop-filter:blur(14px)}h1{font-size:clamp(48px,8vw,92px);line-height:.95;margin:20px 0 16px;letter-spacing:-.04em;max-width:950px}.lead{font-size:clamp(18px,2.2vw,24px);line-height:1.6;color:var(--muted);max-width:820px}.cta-row{display:flex;gap:14px;flex-wrap:wrap;margin-top:24px}.btn{display:inline-block;padding:16px 22px;border-radius:16px;text-decoration:none;font-weight:800}.btn.primary{background:linear-gradient(90deg,var(--a),var(--b));color:#fff}.btn.secondary{border:1px solid var(--line);color:#fff;background:rgba(255,255,255,.04)}section{padding:56px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.card{padding:22px;border:1px solid var(--line);background:var(--panel);border-radius:22px;backdrop-filter:blur(14px)}@media (max-width:900px){.grid{grid-template-columns:1fr}.hero{min-height:auto;padding:70px 0}h1{font-size:48px}}
</style>
</head>
<body>
<div class="wrap">
<section class="hero">
  <div>
    <span class="badge">Siteformo Demo</span>
    <h1>Luxury redesign. Sharper message. Stronger conversion.</h1>
    <p class="lead">We transformed the page into a more premium, persuasive, conversion-ready experience while preserving the important business facts.</p>
    <div class="cta-row">
      <a class="btn primary" href="#cta">See the offer</a>
      <a class="btn secondary" href="#contact">Contact</a>
    </div>
  </div>
</section>
<section>
  <div class="grid">
    <div class="card"><h3>Preserved important data</h3><p>Prices, links, videos, and contact facts stay intact.</p></div>
    <div class="card"><h3>Sharper messaging</h3><p>Headlines, structure, and calls to action are rebuilt for desire.</p></div>
    <div class="card"><h3>Modern premium visual style</h3><p>Motion, depth, contrast, and premium layout create the wow effect.</p></div>
  </div>
</section>
<section id="cta">
  <div class="card">
    <h2>Ready to turn your page into a conversion machine?</h2>
    <p>This demo is temporary. Review the new direction while it is still live.</p>
    <div class="cta-row"><a class="btn primary" href="#contact">Open the next step</a></div>
  </div>
</section>
<section id="contact">
  <div class="card">
    <h2>Contact</h2>
    <p>Email: hello@example.com</p>
  </div>
</section>
</div>
</body>
</html>"""


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
        ("luxury_editorial", r"luxury|premium|exclusive|boutique|villa|interior|jewelry|beauty|aesthetic|spa|fine dining|resort"),
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

    for label, pattern in patterns:
        if re.search(pattern, text):
            style = label
            break

    if style == "luxury_editorial":
        audience = "high-end buyers"
        tone = "elegant, aspirational, exclusive"
        visual_direction = "editorial typography, luxury spacing, rich gradients, refined contrast"
        cta_style = "exclusive, invitation-based"
    elif style == "future_saas":
        audience = "modern tech buyers"
        tone = "clear, sharp, confident, outcome-driven"
        visual_direction = "product-led layouts, clean grids, futuristic glow, smooth motion"
        cta_style = "direct, high-clarity, high-intent"
    elif style == "executive_b2b":
        audience = "decision makers"
        tone = "credible, authoritative, efficient"
        visual_direction = "structured sections, proof-driven blocks, restrained premium design"
        cta_style = "trust-first, low-friction"
    elif style == "clean_trust":
        audience = "patients and families"
        tone = "calm, trustworthy, warm"
        visual_direction = "soft gradients, bright surfaces, trust signals, clarity"
        cta_style = "comforting, simple, reassuring"
    elif style == "bold_commerce":
        audience = "shoppers"
        tone = "desirable, energetic, benefit-driven"
        visual_direction = "strong product focus, bold cards, contrast, urgency"
        cta_style = "buy-now, desire-driven"
    elif style == "architectural_premium":
        audience = "buyers and investors"
        tone = "aspirational, polished, premium"
        visual_direction = "large imagery zones, elegant layout, confident whitespace"
        cta_style = "high-value, appointment-driven"
    elif style == "creative_studio":
        audience = "brand-conscious clients"
        tone = "bold, premium, trend-aware"
        visual_direction = "high-contrast art direction, punchy sections, smooth transitions"
        cta_style = "portfolio-like, persuasive"

    return {
        "style": style,
        "audience": audience,
        "tone": tone,
        "visual_direction": visual_direction,
        "cta_style": cta_style,
    }


def _build_system_prompt(style: str) -> str:
    style_prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS["premium_modern"])
    return "\n\n".join([
        BASE_RULES.strip(),
        dedent("""
        Your job is NOT to make a merely beautiful page.
        Your job is to create a page that feels irresistible.
        The user must think: "I WANT THIS"

        Conversion rules:
        - improve headlines aggressively
        - sell outcomes, not features
        - remove generic phrasing
        - create aspiration, clarity, status, and desire
        - reduce friction
        - intensify CTA hierarchy
        - make the offer feel more valuable than expected

        Mandatory structure:
        1. Hero
        2. Problem / tension
        3. Solution / transformation
        4. Benefits
        5. Offer
        6. Social proof
        7. Differentiation
        8. FAQ / objection handling
        9. Strong final CTA
        10. Contact / pricing / preserved facts if provided

        Design rules:
        - premium visual hierarchy
        - layered gradients or depth
        - motion and micro-interactions
        - elevated CTA blocks
        - believable testimonials if none are provided
        - strong first screen
        - responsive production-presentable HTML

        The final result must feel like a $5,000-$15,000 landing page.
        """).strip(),
        style_prompt,
    ])


def _build_user_prompt(request_type: str, source: dict | None, business_description: str | None, profile: dict) -> str:
    payload = {
        "request_type": request_type,
        "brand_profile": profile,
        "business_description": business_description,
        "source": source,
        "requirements": {
            "goal": "wow, desire, conversion",
            "must_preserve_real_facts": True,
            "page_count": 1,
            "must_be_complete_html": True,
            "should_feel_premium": True,
            "must_include_motion": True,
            "must_strengthen_offer": True,
            "must_upgrade_headlines": True,
            "must_route_copy_to_detected_style": True,
            "must_route_visuals_to_detected_style": True,
        },
        "extra_directions": [
            "Choose the strongest possible conversion angle for this niche.",
            "Make the hero instantly impressive and emotionally charged.",
            "Use CTA copy that feels specific and high intent.",
            "Make testimonials believable, not generic.",
            "Use different visual language depending on the detected style.",
            "Design the page so it feels custom-made for this business.",
            "Avoid bland AI tone. Write like an elite agency.",
        ],
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


def _score_candidate(candidate: dict) -> int:
    html = str(candidate.get("html", "")).lower()
    score = 0
    for needle in [
        "<section", "hero", "testimonial", "faq", "contact", "pricing", "cta",
        "animation", "transition", "gradient", "button", "hover"
    ]:
        if needle in html:
            score += 1
    if "wow" in html:
        score += 1
    return score


def generate_demo_page(request_type: str, source: dict | None = None, business_description: str | None = None) -> dict:
    logger.info("[AI] generating high-conversion page...")
    profile = _infer_brand_profile(source, business_description)
    logger.info("[AI] routed style=%s audience=%s tone=%s", profile["style"], profile["audience"], profile["tone"])

    if not settings.openai_api_key:
        return {"title": "Siteformo Demo", "html": FALLBACK_HTML}

    system_prompt = _build_system_prompt(profile["style"])
    user_prompt = _build_user_prompt(request_type, source, business_description, profile)
    client = OpenAI(api_key=settings.openai_api_key)

    candidates: list[dict] = []
    for _ in range(2):
        response = client.chat.completions.create(
            model=settings.openai_model,
            temperature=0.95,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content or ""
        data = _extract_candidate_content(content)
        if data and data.get("html"):
            candidates.append(data)

    if not candidates:
        logger.info("[AI] generation fallback used")
        return {"title": "Siteformo Demo", "html": FALLBACK_HTML}

    best = max(candidates, key=_score_candidate)
    logger.info("[AI] generation complete")
    return {
        "title": str(best.get("title") or "Siteformo Demo"),
        "html": str(best.get("html") or FALLBACK_HTML),
    }
