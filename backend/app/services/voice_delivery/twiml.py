from __future__ import annotations

from xml.etree import ElementTree as ET

from app.services.voice_delivery.models import normalize_name


def spoken_script(first_name: str) -> str:
    name = normalize_name(first_name)
    return (
        f"Hello, {name}. Welcome to SiteFormo. "
        "This is how your future customers can request a phone call from your website. "
        "Thank you."
    )


def render_twiml(first_name: str, *, voice: str, language: str) -> str:
    response = ET.Element("Response")
    say = ET.SubElement(response, "Say", {"voice": voice, "language": language})
    say.text = spoken_script(first_name)
    ET.SubElement(response, "Hangup")
    return ET.tostring(response, encoding="unicode", short_empty_elements=True)
