from __future__ import annotations

SUPPORTED_LANGUAGES = {"en", "it", "fr", "de"}
DEFAULT_LANGUAGE = "en"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "email.badge": "Siteformo Demo Ready",
        "email.title": "Your wow-page is ready.",
        "email.subtitle": "We created a premium one-page demo for you.",
        "email.open_demo": "Open my demo",
        "email.footer": "This private demo link is temporary and available for {minutes} minutes.",

        "demo.cta.order_new": "Order new website",
        "demo.cta.order_redesign": "Order redesign",
    },
    "it": {
        "email.badge": "Demo Siteformo pronta",
        "email.title": "La tua pagina è pronta.",
        "email.subtitle": "Abbiamo creato una demo premium di una pagina per te.",
        "email.open_demo": "Apri la demo",
        "email.footer": "Questo link privato alla demo è disponibile per {minutes} minuti.",

        "demo.cta.order_new": "Ordina nuovo sito",
        "demo.cta.order_redesign": "Ordina restyling",
    },
    "fr": {
        "email.badge": "Démo Siteformo prête",
        "email.title": "Votre page est prête.",
        "email.subtitle": "Nous avons créé une démo premium d’une page pour vous.",
        "email.open_demo": "Ouvrir ma démo",
        "email.footer": "Ce lien privé vers la démo est disponible pendant {minutes} minutes.",

        "demo.cta.order_new": "Commander un nouveau site",
        "demo.cta.order_redesign": "Commander une refonte",
    },
    "de": {
        "email.badge": "Siteformo-Demo bereit",
        "email.title": "Ihre Seite ist fertig.",
        "email.subtitle": "Wir haben eine hochwertige One-Page-Demo für Sie erstellt.",
        "email.open_demo": "Demo öffnen",
        "email.footer": "Dieser private Demo-Link ist {minutes} Minuten lang verfügbar.",

        "demo.cta.order_new": "Neue Website bestellen",
        "demo.cta.order_redesign": "Redesign bestellen",
    },
}


def normalize_language(language: str | None) -> str:
    if not language:
        return DEFAULT_LANGUAGE

    value = language.strip().lower().replace("_", "-")
    base = value.split("-")[0]

    if base in SUPPORTED_LANGUAGES:
        return base

    return DEFAULT_LANGUAGE


def detect_language_from_header(accept_language: str | None) -> str:
    if not accept_language:
        return DEFAULT_LANGUAGE

    parts = [chunk.strip() for chunk in accept_language.split(",") if chunk.strip()]
    for part in parts:
        lang = normalize_language(part.split(";")[0])
        if lang in SUPPORTED_LANGUAGES:
            return lang

    return DEFAULT_LANGUAGE


def t(key: str, language: str | None = None, **kwargs) -> str:
    lang = normalize_language(language)
    value = TRANSLATIONS.get(lang, {}).get(key)

    if value is None:
        value = TRANSLATIONS[DEFAULT_LANGUAGE].get(key, key)

    if kwargs:
        try:
            return value.format(**kwargs)
        except Exception:
            return value

    return value