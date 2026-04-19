from __future__ import annotations

SUPPORTED_LANGUAGES = {"en"}
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
