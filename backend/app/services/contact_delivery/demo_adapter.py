from __future__ import annotations

from dataclasses import dataclass
from html import escape

INTENTS = ("order", "product", "size", "delivery", "returns", "other")
ORDER_INTENTS = frozenset({"order", "returns"})


class DemoMessageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DemoContext:
    example_id: str
    intent: str
    first_name: str | None = None
    order_number: str | None = None
    question: str | None = None


@dataclass(frozen=True, slots=True)
class DemoMessage:
    subject: str
    customer_example: str
    business_explanation: str


SUBJECTS = {
    "order": "VELAIRE example: an update about your order enquiry",
    "product": "VELAIRE example: a response to your product question",
    "size": "VELAIRE example: size and fit guidance",
    "delivery": "VELAIRE example: delivery and tracking support",
    "returns": "VELAIRE example: return request acknowledgement",
    "other": "VELAIRE example: your enquiry has been received",
}


def validate_context(context: DemoContext) -> DemoContext:
    if context.example_id != "SF_REF_01_VELAIRE":
        raise DemoMessageError("unsupported_example")
    if context.intent not in INTENTS:
        raise DemoMessageError("invalid_intent")
    order = (context.order_number or "").strip()
    if context.intent in ORDER_INTENTS and not order:
        raise DemoMessageError("order_number_required")
    if context.intent not in ORDER_INTENTS and order:
        raise DemoMessageError("order_number_not_allowed")
    return DemoContext(context.example_id, context.intent, context.first_name, order or None, context.question)


def message_for(context: DemoContext) -> DemoMessage:
    c = validate_context(context)
    order = c.order_number or ""
    examples = {
        "order": f"Thanks for asking about order {order}. In a live VELAIRE store, the team could review the order details and follow up with a clear update.",
        "product": "Thanks for your product question. In a live VELAIRE store, the team could reply with the relevant details about availability, materials or specifications.",
        "size": "Thanks for your size and fit question. In a live VELAIRE store, the team could help you compare the product guidance and choose the most appropriate fit.",
        "delivery": "Thanks for your delivery question. In a live VELAIRE store, the team could confirm the available delivery option, timing or tracking information.",
        "returns": f"Thanks for starting a return request for order {order}. In a live VELAIRE store, the team could guide the return. After the item is received and checked, an approved refund could be returned to the original payment method within five business days.",
        "other": "Thanks for getting in touch. In a live VELAIRE store, the team could review your enquiry and follow up with a clear, helpful response.",
    }
    business = {
        "order": "The business could separately receive the order number and enquiry needed to review the request.",
        "product": "The business could separately receive the product question and contact details needed to respond.",
        "size": "The business could separately receive the fit question and relevant contact details.",
        "delivery": "The business could separately receive the delivery enquiry and information needed to investigate it.",
        "returns": "The business could separately receive the return request, order number and information needed to handle it.",
        "other": "The business could separately receive the enquiry and contact details needed to follow up.",
    }
    return DemoMessage(SUBJECTS[c.intent], examples[c.intent], business[c.intent])


def email_html(context: DemoContext) -> str:
    c, m = validate_context(context), message_for(context)
    question = f"<p><strong>Your demonstration question:</strong> {escape((c.question or '').strip())}</p>" if (c.question or '').strip() else ""
    return ("<p><strong>This is a SiteFormo demonstration.</strong> No real order, delivery, return, refund or stock action has occurred.</p>"
            f"<h2>Customer message example</h2><p>{escape(m.customer_example)}</p>{question}"
            f"<h2>Business-side example</h2><p>{escape(m.business_explanation)}</p>"
            "<p>SiteFormo can adapt the wording, channels and workflow to suit the business.</p>")


def email_text(context: DemoContext) -> str:
    c, m = validate_context(context), message_for(context)
    question = f"\n\nYour demonstration question:\n{(c.question or '').strip()}" if (c.question or '').strip() else ""
    return ("This is a SiteFormo demonstration. No real order, delivery, return, refund or stock action has occurred.\n\n"
            f"Customer message example\n{m.customer_example}{question}\n\nBusiness-side example\n{m.business_explanation}\n\n"
            "SiteFormo can adapt the wording, channels and workflow to suit the business.")


def whatsapp_text(context: DemoContext) -> str:
    m = message_for(context)
    return ("SiteFormo demo — no real operational action has occurred.\n\n"
            f"VELAIRE example: {m.customer_example}\n\n{m.business_explanation}\n\n"
            "SiteFormo can adapt this wording and workflow to the business.")
