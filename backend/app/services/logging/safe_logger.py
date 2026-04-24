import logging
import os
import re


SENSITIVE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]+"),
    re.compile(r"\b\+?\d{9,15}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]


def mask_sensitive(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub("[masked]", text)
    return text


def get_logger(name: str = "siteformo"):
    level = os.getenv("LOG_LEVEL", "info").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger(name)
