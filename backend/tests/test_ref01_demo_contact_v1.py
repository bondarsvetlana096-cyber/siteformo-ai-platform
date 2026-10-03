import pytest

from app.services.contact_delivery.demo_adapter import (
    DemoContext,
    DemoMessageError,
    INTENTS,
    email_html,
    message_for,
    whatsapp_text,
)


@pytest.mark.parametrize("intent", INTENTS)
def test_all_intents_select_distinct_copy(intent: str) -> None:
    order = "DEMO-100" if intent in {"order", "returns"} else None
    context = DemoContext("SF_REF_01_VELAIRE", intent, "Visitor", order, "Test question")
    message = message_for(context)
    assert message.subject and message.customer_example and message.business_explanation
    assert "SiteFormo demonstration" in email_html(context)
    assert "no real operational action" in whatsapp_text(context)


@pytest.mark.parametrize("intent", ["order", "returns"])
def test_order_context_required(intent: str) -> None:
    with pytest.raises(DemoMessageError, match="order_number_required"):
        message_for(DemoContext("SF_REF_01_VELAIRE", intent))


@pytest.mark.parametrize("intent", ["product", "size", "delivery", "other"])
def test_unexpected_order_context_rejected(intent: str) -> None:
    with pytest.raises(DemoMessageError, match="order_number_not_allowed"):
        message_for(DemoContext("SF_REF_01_VELAIRE", intent, order_number="DEMO-100"))


def test_invalid_intent_rejected_and_no_false_claims() -> None:
    with pytest.raises(DemoMessageError, match="invalid_intent"):
        message_for(DemoContext("SF_REF_01_VELAIRE", "refund"))
    corpus = " ".join(
        email_html(DemoContext("SF_REF_01_VELAIRE", intent, order_number="DEMO-100" if intent in {"order", "returns"} else None))
        for intent in INTENTS
    ).lower()
    assert "your refund has been issued" not in corpus
    assert "your order has been cancelled" not in corpus
