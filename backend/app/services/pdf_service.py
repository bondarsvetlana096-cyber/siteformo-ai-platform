import os
import textwrap
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def _clean(value):
    if value is None:
        return "Not provided"

    if isinstance(value, dict):
        return "\n".join([f"{key}: {val}" for key, val in value.items()])

    if isinstance(value, list):
        return "\n".join([str(item) for item in value])

    return str(value)


def create_divi_pdf(order, initial_answers, extended_answers, generation_result):
    output_dir = "/tmp"
    order_id = getattr(order, "id", "unknown")
    file_path = os.path.join(output_dir, f"SiteFormo_Divi5_Order_{order_id}.pdf")

    c = canvas.Canvas(file_path, pagesize=A4)
    width, height = A4

    y = height - 50

    def new_page_if_needed(space=70):
        nonlocal y
        if y < space:
            c.showPage()
            y = height - 50

    def title(text):
        nonlocal y
        new_page_if_needed(80)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(40, y, str(text))
        y -= 28

    def line(text):
        nonlocal y
        c.setFont("Helvetica", 10)

        cleaned = _clean(text)

        for raw_line in cleaned.splitlines():
            wrapped_lines = textwrap.wrap(raw_line, width=95) or [""]

            for wrapped in wrapped_lines:
                new_page_if_needed(50)
                c.setFont("Helvetica", 10)
                c.drawString(40, y, wrapped)
                y -= 14

    def section(name, content):
        nonlocal y
        title(name)

        if isinstance(content, dict):
            for key, value in content.items():
                line(f"{key}: {value}")
        else:
            line(content)

        y -= 16

    title("SiteFormo AI — Divi 5 Website Generation")

    section("Order", {
        "Order ID": getattr(order, "id", "Not provided"),
        "Status": getattr(order, "status", "Not provided"),
        "Package": getattr(order, "tier", "Not provided"),
        "Deposit EUR": getattr(order, "deposit_eur", "Not provided"),
    })

    section("Initial Quiz Answers", initial_answers)
    section("Extended Questionnaire Answers", extended_answers)
    section("Divi 5 Ready Result", generation_result)

    c.save()

    return file_path