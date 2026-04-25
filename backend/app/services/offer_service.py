from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from app.core.config import settings


def calculate_estimate(answers: dict[str, Any]) -> dict[str, Any]:
    base_price = 300
    service = answers.get("start")
    business = answers.get("business_type")
    timeline = answers.get("timeline")
    budget = answers.get("budget")

    if service == "new_site":
        base_price += 500
    elif service == "redesign":
        base_price += 350
    elif service == "ai_form":
        base_price += 300
    elif service == "integrations":
        base_price += 450

    if business == "ecommerce":
        base_price += 400
    elif business in {"education", "creator"}:
        base_price += 150

    if timeline == "urgent":
        base_price *= 1.3
    if budget == "1500_plus":
        base_price *= 1.2

    price = int(round(base_price / 10) * 10)
    delivery = "7-10 дней" if price <= 1200 else "2-3 недели"
    complexity = "standard" if price <= 1200 else "advanced"
    return {"price_eur": price, "timeline": delivery, "complexity": complexity}


def generate_offer_text(answers: dict[str, Any], labels: dict[str, dict[str, str]], estimate: dict[str, Any]) -> str:
    service = labels.get("start", {}).get(answers.get("start"), answers.get("start", "проект"))
    business = labels.get("business_type", {}).get(answers.get("business_type"), answers.get("business_type", "бизнес"))
    timeline = labels.get("timeline", {}).get(answers.get("timeline"), answers.get("timeline", "сроки уточним"))
    budget = labels.get("budget", {}).get(answers.get("budget"), answers.get("budget", "бюджет уточним"))
    return (
        "Коммерческое предложение SiteFormo\n\n"
        f"Задача: {service}\n"
        f"Тип бизнеса: {business}\n"
        f"Желаемые сроки: {timeline}\n"
        f"Заявленный бюджет: {budget}\n\n"
        f"Предварительная оценка: от €{estimate['price_eur']}\n"
        f"Ориентировочный срок реализации: {estimate['timeline']}\n\n"
        "Что входит:\n"
        "- структура лендинга или сайта под вашу задачу;\n"
        "- продающий сценарий и CTA;\n"
        "- AI-форма/квиз для сбора заявок;\n"
        "- базовая интеграция с каналами связи.\n\n"
        "Следующий шаг: коротко уточним детали и подготовим точный план запуска."
    )


def _safe_filename(session_id: str) -> str:
    return "".join(ch for ch in session_id if ch.isalnum() or ch in {"-", "_"})[:80] or "offer"


def generate_offer_pdf_or_html(session_id: str, text: str) -> dict[str, str]:
    output_dir = Path(settings.offer_output_dir or "app/static/offers")
    output_dir.mkdir(parents=True, exist_ok=True)
    base = _safe_filename(session_id)
    public_base = (settings.public_base_url or "").rstrip("/")

    try:
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        file_path = output_dir / f"{base}.pdf"
        doc = SimpleDocTemplate(str(file_path))
        styles = getSampleStyleSheet()
        story = [Paragraph("SiteFormo AI Proposal", styles["Title"]), Spacer(1, 12)]
        for line in text.split("\n"):
            story.append(Paragraph(html.escape(line) or " ", styles["BodyText"]))
        doc.build(story)
        return {"path": str(file_path), "url": f"{public_base}/static/offers/{file_path.name}", "type": "pdf"}
    except Exception:
        file_path = output_dir / f"{base}.html"
        file_path.write_text(
            "<!doctype html><meta charset='utf-8'><title>SiteFormo Proposal</title>"
            "<style>body{font-family:Arial,sans-serif;max-width:760px;margin:40px auto;line-height:1.55;padding:0 20px}</style>"
            f"<h1>SiteFormo AI Proposal</h1><pre style='white-space:pre-wrap'>{html.escape(text)}</pre>",
            encoding="utf-8",
        )
        return {"path": str(file_path), "url": f"{public_base}/static/offers/{file_path.name}", "type": "html"}
