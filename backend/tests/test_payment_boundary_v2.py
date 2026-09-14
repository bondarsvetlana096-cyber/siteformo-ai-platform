import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import payment_boundary_routes, stripe_webhook
from app.db.session import Base, get_db
from app.journey.identity import JOURNEY_CREDENTIAL_HEADER
from app.journey.models import JourneyBase
from app.main import app
from app.models.order import Order
from app.models.payment import PaymentAttempt, StripeWebhookEvent
from app.services.design_direction_service import (
    DESIGN_DIRECTION_CONTRACT_VERSION,
    DESIGN_DIRECTION_KEYS,
)
from app.services.payment_boundary_service import (
    ADDON_REGISTRY_CENTS, PaymentBoundaryError, package_price_cents,
    _email_scope_signature, process_v2_checkout_completed,
)

ORIGIN = {"Origin": "https://ie.siteformo.com"}
TERMS = "local-test-terms-v1"


def _q1_payload():
    return {
        "flow_version": "q1_v2", "schema_version": 2, "project_class_intent": "business_site",
        "preferred_contact": {"channel": "email", "value": "owner@example.test", "normalized_value": "owner@example.test", "purpose": "operational_communication", "display_on_generated_website": False},
        "existing_website": {"has_existing_website": False, "url": None, "analysis": None},
        "examples_context": {}, "package_browsing_context": {"package_key": "starter", "source": "example"},
        "package_qualification": {"status": "unqualified", "candidate_package": None, "source": None},
        "assistant_context": {"current_step_id": "q1_complete", "enabled": False},
    }


def q2_payload(*, options=None, legal=True):
    return {
        "flow_version": "q2_v2", "schema_version": 2,
        "business_identity": {"status": "business_name", "name": "Example Electric"},
        "business_activity": {"niche": "Electrician", "broad_model": "local_service", "other_clarification": None},
        "operating_model": "travel_to_customers",
        "location": {"public_address": None, "service_area": "Dublin", "multiple_locations_summary": None},
        "audience": "individuals", "primary_goal": "enquiries",
        "functions": [{"key": "contact_enquiry", "confirmed": True}], "trust_materials": ["reviews"],
        "media": {"photos": "client_provides", "client_photos_timing": "later", "video": "none"},
        "social_presence": {"expected_channels": ["none"], "other_platform": None, "links": "pending", "presentation": "flexible"},
        "logo": {"choice": "none", "price_eur": 0, "scope": {}},
        "delivery_context": {"hosting_status": "not_sure", "hosting_help_included": True, "domain_and_hosting_client_owned": True, "installation_selected": False, "installation_price_eur": 0},
        "paid_structural_options": options or [],
        "package_context": {"starting_package": "starter", "source": "package_browsing_context", "rule_version": "ireland_accepted_v1"},
        "analysis_hints": {"source": None, "status": None, "business_hints": [], "function_hints": [], "complexity_hints": []},
        "assistant_context": {"current_step_id": "q2_package_confirmation", "enabled": False},
        "legal_gate_confirmed": legal, "package_and_addons_confirmed": True,
    }


def create_q1(client, credential):
    headers = {**ORIGIN, JOURNEY_CREDENTIAL_HEADER: credential}
    order_id = client.post("/api/orders/q1/project", headers=headers, json={}).json()["order_id"]
    assert client.patch(f"/api/orders/{order_id}/q1", headers=headers, json=_q1_payload()).status_code == 200
    return order_id, headers


def _factory():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine); JourneyBase.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _override(maker):
    def dependency():
        with maker() as db:
            yield db
    return dependency


def _stripe_create(**_kwargs):
    return SimpleNamespace(id=f"cs_test_{uuid.uuid4().hex}", url="https://checkout.stripe.test/session", expires_at=None)


@pytest.fixture
def boundary(monkeypatch):
    maker = _factory(); app.dependency_overrides[get_db] = _override(maker)
    monkeypatch.setenv("SITEFORMO_LEGAL_TERMS_VERSION", TERMS)
    monkeypatch.setattr(payment_boundary_routes.stripe, "api_key", "sk_test_not_a_secret")
    monkeypatch.setattr(payment_boundary_routes.stripe.checkout.Session, "create", _stripe_create)
    async def email_sender(_to, _subject, _html):
        return {"id": "email_fixture"}
    monkeypatch.setattr(payment_boundary_routes, "send_email", email_sender)
    client = TestClient(app, base_url="https://ie.siteformo.com")
    credential = client.post("/api/journey/session", headers=ORIGIN).json()["credential"]
    order_id, headers = create_q1(client, credential)
    yield client, maker, order_id, headers
    app.dependency_overrides.clear()


def _save_and_confirm(client, order_id, headers, payload=None, *, request_email=True):
    saved = client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=payload or q2_payload())
    assert saved.status_code == 200, saved.text
    confirmed = client.post(f"/api/orders/{order_id}/payment-confirmation", headers=headers, json={
        "brief_confirmed": True, "legal_confirmed": True, "legal_terms_version": TERMS,
    })
    assert confirmed.status_code == 200, confirmed.text
    if request_email:
        emailed = client.post(f"/api/orders/{order_id}/prepayment-summary-email", headers=headers, json={})
        assert emailed.status_code == 200, emailed.text


def _event(attempt, *, event_id="evt_1", amount=None, currency="eur", payment_status="paid", scope_hash=None):
    return {"id": event_id, "type": "checkout.session.completed", "data": {"object": {
        "id": attempt.stripe_checkout_session_id, "payment_status": payment_status,
        "amount_total": attempt.expected_deposit_amount_cents if amount is None else amount,
        "currency": currency, "client_reference_id": attempt.order_id, "payment_intent": "pi_test_1",
        "metadata": {"payment_contract": "q2_v2_initial_deposit", "payment_attempt_id": attempt.id,
                     "order_id": attempt.order_id, "scope_hash": scope_hash or attempt.scope_snapshot_hash},
    }}}


def test_authoritative_prices_and_reference_block():
    assert package_price_cents("starter") // 2 == 45_000
    assert package_price_cents("business") // 2 == 75_000
    assert package_price_cents("advanced") // 2 == 225_000
    with pytest.raises(PaymentBoundaryError, match="Reference"):
        package_price_cents("reference")
    assert ADDON_REGISTRY_CENTS == {
        "additional_page": 12_000, "video_entry_page": 12_000,
        "simple_logo": 10_000, "installation": 12_500,
    }


def test_checkout_deposit_excludes_deferred_addons(boundary):
    client, maker, order_id, headers = boundary
    payload = q2_payload(options=[{"key": "video_entry_page", "quantity": 1, "price_eur_each": 120, "counts_as_page": True, "explicit_confirmed": True}])
    payload["logo"] = {"choice": "siteformo_simple_logo", "price_eur": 100, "scope": {
        "one_direction": True, "one_revision": True, "web_ready_delivery": True,
        "full_branding": False, "naming": False, "trademark_or_legal_clearance": False,
    }}
    payload["delivery_context"].update(installation_selected=True, installation_price_eur=125)
    _save_and_confirm(client, order_id, headers, payload)
    response = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={})
    assert response.status_code == 200 and response.json()["deposit_amount_cents"] == 45_000
    with maker() as db:
        attempt = db.execute(select(PaymentAttempt)).scalar_one()
        assert attempt.base_package_price_cents == 90_000
        assert attempt.scope_snapshot["addons_due_now_cents"] == 0
        assert "q2" not in attempt.scope_snapshot and len(attempt.scope_snapshot["q2_content_hash"]) == 64
        assert attempt.scope_snapshot["confirmed_addons_balance_cents"] == 34_500
        assert attempt.scope_snapshot["current_final_balance_cents"] == 79_500
        assert all(item["charge_phase"] == "final_balance" and item["confirmed_at"] for item in attempt.confirmed_addons_snapshot)


def test_browser_cannot_author_prices_or_urls(boundary):
    client, _, order_id, headers = boundary; _save_and_confirm(client, order_id, headers)
    response = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={
        "amount": 1, "package_price": 1, "success_url": "https://evil.test", "cancel_url": "https://evil.test",
    })
    assert response.status_code == 422


def test_journey_ownership_and_missing_order(boundary):
    client, _, order_id, headers = boundary; _save_and_confirm(client, order_id, headers)
    assert client.post(f"/api/orders/{order_id}/checkout", headers=ORIGIN, json={}).status_code == 403
    other = client.post("/api/journey/session", headers=ORIGIN).json()["credential"]
    other_headers = {**ORIGIN, JOURNEY_CREDENTIAL_HEADER: other}
    assert client.post(f"/api/orders/{order_id}/checkout", headers=other_headers, json={}).status_code == 403
    assert client.post(f"/api/orders/{uuid.uuid4()}/checkout", headers=headers, json={}).status_code == 403


def test_readiness_and_confirmation_gates(boundary):
    client, _, order_id, headers = boundary
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 409
    assert client.post(f"/api/orders/{order_id}/payment-confirmation", headers=headers, json={
        "brief_confirmed": True, "legal_confirmed": True, "legal_terms_version": TERMS,
    }).status_code == 409
    assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload(legal=False)).status_code == 200
    assert client.post(f"/api/orders/{order_id}/payment-confirmation", headers=headers, json={
        "brief_confirmed": True, "legal_confirmed": True, "legal_terms_version": TERMS,
    }).status_code == 200
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 409


@pytest.mark.parametrize("legacy_legal_field", [False, None])
def test_order_legal_state_is_sole_v2_checkout_authority(boundary, legacy_legal_field):
    client, maker, order_id, headers = boundary
    payload = q2_payload(legal=False)
    if legacy_legal_field is None:
        payload.pop("legal_gate_confirmed")
    saved = client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=payload)
    assert saved.status_code == 200 and saved.json()["scope_qualification"]["checkout_ready"] is True

    # The Q2 compatibility boolean cannot substitute for Order-level events.
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 409
    assert client.post(f"/api/orders/{order_id}/payment-confirmation", headers=headers, json={
        "brief_confirmed": True, "legal_confirmed": True, "legal_terms_version": TERMS,
    }).status_code == 200
    assert client.post(f"/api/orders/{order_id}/prepayment-summary-email", headers=headers, json={}).status_code == 200
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 200

    with maker() as db:
        order = db.get(Order, order_id)
        stored = order.extended_brief["q2_v2"]
        assert stored.get("legal_gate_confirmed", False) is False
        assert order.legal_confirmed_at is not None and order.legal_terms_version == TERMS


def test_q2_legal_true_cannot_replace_order_level_legal_confirmation(boundary):
    client, _, order_id, headers = boundary
    saved = client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload(legal=True))
    assert saved.status_code == 200 and saved.json()["scope_qualification"]["checkout_ready"] is True
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 409
    summary = client.get(f"/api/orders/{order_id}/prepayment-summary", headers=headers).json()
    assert summary["confirmation_state"]["legal_confirmed_at"] is None
    assert summary["confirmation_state"]["legal_terms_version"] is None


def test_brief_and_legal_are_separate_durable_events(boundary):
    client, _, order_id, headers = boundary
    assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload()).status_code == 200
    brief = client.post(f"/api/orders/{order_id}/payment-confirmation", headers=headers, json={
        "brief_confirmed": True, "legal_confirmed": False,
    })
    assert brief.status_code == 200 and brief.json()["brief_confirmed_at"]
    assert brief.json()["legal_confirmed_at"] is None
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 409
    legal = client.post(f"/api/orders/{order_id}/payment-confirmation", headers=headers, json={
        "brief_confirmed": False, "legal_confirmed": True, "legal_terms_version": TERMS,
    })
    assert legal.status_code == 200 and legal.json()["legal_confirmed_at"]
    assert client.post(f"/api/orders/{order_id}/prepayment-summary-email", headers=headers, json={}).status_code == 200
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 200


def test_repeated_confirmation_does_not_reset_sent_email_state(boundary):
    client, maker, order_id, headers = boundary; _save_and_confirm(client, order_id, headers)
    with maker() as db:
        order = db.get(Order, order_id); order.prepayment_summary_email_status = "sent"; db.commit()
    response = client.post(f"/api/orders/{order_id}/payment-confirmation", headers=headers, json={
        "brief_confirmed": True, "legal_confirmed": True, "legal_terms_version": TERMS,
    })
    assert response.status_code == 200
    assert response.json()["prepayment_summary_email_status"] == "sent"


def test_duplicate_checkout_reuses_same_attempt(boundary):
    client, maker, order_id, headers = boundary; _save_and_confirm(client, order_id, headers)
    first = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).json()
    second = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).json()
    assert first["attempt_id"] == second["attempt_id"] and second["reused"] is True
    with maker() as db:
        assert len(db.execute(select(PaymentAttempt)).scalars().all()) == 1


def test_changed_scope_supersedes_unpaid_attempt(boundary):
    client, maker, order_id, headers = boundary; _save_and_confirm(client, order_id, headers)
    first = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).json()
    changed = q2_payload(); changed["trust_materials"] = ["reviews", "certifications"]
    assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=changed).status_code == 200
    assert client.post(f"/api/orders/{order_id}/prepayment-summary-email", headers=headers, json={}).status_code == 200
    second = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).json()
    assert first["attempt_id"] != second["attempt_id"]
    with maker() as db:
        assert db.get(PaymentAttempt, first["attempt_id"]).status == "superseded"


def test_webhook_payment_truth_status_and_duplicate(boundary):
    client, maker, order_id, headers = boundary; _save_and_confirm(client, order_id, headers)
    attempt_id = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).json()["attempt_id"]
    pending = client.get(f"/api/orders/{order_id}/payment-status?session_id=fake&stage=paid", headers=headers).json()
    assert pending["payment_confirmed"] is False
    with maker() as db:
        attempt = db.get(PaymentAttempt, attempt_id); event = _event(attempt)
        assert process_v2_checkout_completed(db, event)["processing_result"] == "paid"
        assert process_v2_checkout_completed(db, event)["status"] == "duplicate"
        assert len(db.execute(select(StripeWebhookEvent)).scalars().all()) == 1
    paid = client.get(f"/api/orders/{order_id}/payment-status", headers=headers).json()
    assert paid == {"order_id": order_id, "deposit_status": "paid", "payment_confirmed": True,
                    "next_step": "design_direction", "retry_allowed": False}


def _mark_deposit(client, maker, order_id, headers, status="paid"):
    assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload()).status_code == 200
    with maker() as db:
        order = db.get(Order, order_id)
        order.deposit_status = status
        if status == "paid":
            order.deposit_paid_at = datetime.now(timezone.utc)
            order.deposit_amount_cents = 45_000
            order.deposit_currency = "EUR"
        db.commit()


def test_paid_v2_next_step_tracks_canonical_design_direction(boundary):
    client, maker, order_id, headers = boundary
    _mark_deposit(client, maker, order_id, headers)
    status = client.get(f"/api/orders/{order_id}/payment-status", headers=headers).json()
    assert status["payment_confirmed"] is True
    assert status["next_step"] == "design_direction"

    selected = client.post(
        f"/api/orders/{order_id}/design-direction",
        headers=headers,
        json={"direction": "clean-modern"},
    )
    assert selected.status_code == 200
    status = client.get(f"/api/orders/{order_id}/payment-status", headers=headers).json()
    assert status["next_step"] == "post_payment_pending"


@pytest.mark.parametrize("deposit_status", ["not_started", "checkout_created", "pending", "failed", "cancelled", "refunded"])
def test_unpaid_or_refunded_v2_never_enters_design_direction(boundary, deposit_status):
    client, maker, order_id, headers = boundary
    _mark_deposit(client, maker, order_id, headers, deposit_status)
    status = client.get(f"/api/orders/{order_id}/payment-status", headers=headers).json()
    assert status["payment_confirmed"] is False
    assert status["next_step"] != "design_direction"
    assert client.get(f"/api/orders/{order_id}/design-direction", headers=headers).status_code == 409


def test_design_direction_read_is_safe_versioned_and_journey_authorized(boundary):
    client, maker, order_id, headers = boundary
    _mark_deposit(client, maker, order_id, headers)
    response = client.get(f"/api/orders/{order_id}/design-direction", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "order_id": order_id,
        "stage": "design_direction_required",
        "contract_version": DESIGN_DIRECTION_CONTRACT_VERSION,
        "available_directions": [
            {"key": key, "label": label}
            for key, label in (
                ("clean-modern", "Clean Modern"),
                ("premium-business", "Premium Business"),
                ("bold-startup", "Bold Startup"),
                ("luxury-elite", "Luxury Elite"),
                ("tech-minimal", "Tech Minimal"),
                ("creative-studio", "Creative Studio"),
                ("nordic-soft", "Nordic Soft"),
                ("dark-contrast", "Dark Contrast"),
            )
        ],
        "selected_direction": None,
        "confirmed_at": None,
        "next_step": "design_direction",
        "idempotent": False,
    }
    assert {item["key"] for item in body["available_directions"]} == DESIGN_DIRECTION_KEYS
    serialized = response.text.lower()
    assert "brief_answers" not in serialized and "extended_brief" not in serialized
    assert "owner@example.test" not in serialized and "q2_v2" not in serialized

    assert client.get(f"/api/orders/{order_id}/design-direction", headers=ORIGIN).status_code == 403
    other = client.post("/api/journey/session", headers=ORIGIN).json()["credential"]
    assert client.get(
        f"/api/orders/{order_id}/design-direction",
        headers={**ORIGIN, JOURNEY_CREDENTIAL_HEADER: other},
    ).status_code == 403
    assert client.get(f"/api/orders/{uuid.uuid4()}/design-direction", headers=headers).status_code == 403


def test_design_direction_write_is_idempotent_immutable_and_side_effect_free(boundary):
    client, maker, order_id, headers = boundary
    _mark_deposit(client, maker, order_id, headers)
    with maker() as db:
        before = db.get(Order, order_id)
        original = {
            "status": before.status,
            "design_status": before.design_status,
            "generation_status": before.generation_status,
            "full_generation_started_at": before.full_generation_started_at,
            "selected_design_id": before.selected_design_id,
            "selected_design_label": before.selected_design_label,
            "selected_design_url": before.selected_design_url,
            "selected_screenshot_url": before.selected_screenshot_url,
            "design_approved_at": before.design_approved_at,
            "refund_window_started_at": before.refund_window_started_at,
            "refund_window_expires_at": before.refund_window_expires_at,
        }

    first = client.post(
        f"/api/orders/{order_id}/design-direction", headers=headers,
        json={"direction": "nordic-soft"},
    )
    assert first.status_code == 200
    assert first.json()["stage"] == "design_direction_confirmed"
    assert first.json()["next_step"] == "post_payment_pending"
    assert first.json()["idempotent"] is False

    repeated = client.post(
        f"/api/orders/{order_id}/design-direction", headers=headers,
        json={"direction": "nordic-soft"},
    )
    assert repeated.status_code == 200 and repeated.json()["idempotent"] is True
    changed = client.post(
        f"/api/orders/{order_id}/design-direction", headers=headers,
        json={"direction": "dark-contrast"},
    )
    assert changed.status_code == 409
    assert client.post(
        f"/api/orders/{order_id}/design-direction", headers=headers,
        json={"direction": "not-a-direction"},
    ).status_code == 422

    with maker() as db:
        after = db.get(Order, order_id)
        assert after.design_direction == "nordic-soft"
        assert {
            "status": after.status,
            "design_status": after.design_status,
            "generation_status": after.generation_status,
            "full_generation_started_at": after.full_generation_started_at,
            "selected_design_id": after.selected_design_id,
            "selected_design_label": after.selected_design_label,
            "selected_design_url": after.selected_design_url,
            "selected_screenshot_url": after.selected_screenshot_url,
            "design_approved_at": after.design_approved_at,
            "refund_window_started_at": after.refund_window_started_at,
            "refund_window_expires_at": after.refund_window_expires_at,
        } == original


def test_design_direction_rejects_non_v2_order(boundary):
    client, maker, order_id, headers = boundary
    with maker() as db:
        order = db.get(Order, order_id)
        order.deposit_status = "paid"
        order.brief_answers = {}
        order.extended_brief = {}
        db.commit()
    assert client.get(f"/api/orders/{order_id}/design-direction", headers=headers).status_code == 409
    assert client.post(
        f"/api/orders/{order_id}/design-direction", headers=headers,
        json={"direction": "clean-modern"},
    ).status_code == 409

    # The unchanged legacy route remains available to a legacy Order and keeps
    # its existing status-based response instead of applying the V2 hard block.
    legacy = client.post(f"/api/orders/{order_id}/approve-design", json={})
    assert legacy.status_code == 200
    assert legacy.json()["success"] is False
    assert legacy.json()["already_selected"] is False


def test_design_direction_rejects_extra_fields_and_later_stage(boundary):
    client, maker, order_id, headers = boundary
    _mark_deposit(client, maker, order_id, headers)
    assert client.post(
        f"/api/orders/{order_id}/design-direction", headers=headers,
        json={"direction": "clean-modern", "stage": "paid", "generation_status": "ready"},
    ).status_code == 422
    with maker() as db:
        order = db.get(Order, order_id)
        order.generation_status = "unexpected_later_state"
        db.commit()
    status = client.get(f"/api/orders/{order_id}/payment-status", headers=headers).json()
    assert status["next_step"] == "post_payment_pending"
    assert client.get(f"/api/orders/{order_id}/design-direction", headers=headers).status_code == 409
    assert client.post(
        f"/api/orders/{order_id}/design-direction", headers=headers,
        json={"direction": "clean-modern"},
    ).status_code == 409


def test_design_direction_reads_are_stable_and_failed_commit_rolls_back(boundary, monkeypatch):
    client, maker, order_id, headers = boundary
    _mark_deposit(client, maker, order_id, headers)
    first = client.get(f"/api/orders/{order_id}/design-direction", headers=headers).json()
    second = client.get(f"/api/orders/{order_id}/design-direction", headers=headers).json()
    assert first == second

    with maker() as db:
        # Obtain the Journey visitor through the canonical binding rather than browser data.
        from app.journey.models import SiteFormoJourneyProject, SiteFormoVisitor
        binding = db.execute(
            select(SiteFormoJourneyProject).where(SiteFormoJourneyProject.order_id == order_id)
        ).scalar_one()
        visitor = db.get(SiteFormoVisitor, binding.siteformo_visitor_id)
        original_commit = db.commit
        monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failure")))
        from app.services.design_direction_service import confirm_design_direction
        with pytest.raises(RuntimeError, match="commit failure"):
            confirm_design_direction(db, visitor, order_id, "clean-modern")
        db.rollback()
        monkeypatch.setattr(db, "commit", original_commit)
        assert db.get(Order, order_id).design_direction is None


def test_signed_webhook_route_uses_persisted_attempt(boundary, monkeypatch):
    client, maker, order_id, headers = boundary; _save_and_confirm(client, order_id, headers)
    attempt_id = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).json()["attempt_id"]
    with maker() as db:
        event = _event(db.get(PaymentAttempt, attempt_id), event_id="evt_signed")
    monkeypatch.setattr(stripe_webhook.stripe.Webhook, "construct_event", lambda *_args, **_kwargs: event)
    response = client.post("/api/payments/webhook", content=b"signed-payload", headers={"stripe-signature": "test-signature"})
    assert response.status_code == 200 and response.json()["processing_result"] == "paid"


def test_changed_scope_rejects_old_webhook_and_paid_attempt_cannot_be_superseded(boundary):
    client, maker, order_id, headers = boundary; _save_and_confirm(client, order_id, headers)
    attempt_id = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).json()["attempt_id"]
    with maker() as db:
        old_attempt = db.get(PaymentAttempt, attempt_id); old_event = _event(old_attempt, event_id="evt_stale")
    changed = q2_payload(); changed["trust_materials"] = ["reviews", "certifications"]
    assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=changed).status_code == 200
    with maker() as db:
        assert process_v2_checkout_completed(db, old_event)["processing_result"] == "rejected_stale_scope"
        current = db.get(PaymentAttempt, attempt_id); current.status = "paid"; db.commit()
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 409


def test_expired_attempt_is_not_reused_and_superseded_attempt_cannot_be_paid(boundary):
    client, maker, order_id, headers = boundary; _save_and_confirm(client, order_id, headers)
    first_id = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).json()["attempt_id"]
    with maker() as db:
        first = db.get(PaymentAttempt, first_id)
        first.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1); db.commit()
    second_id = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).json()["attempt_id"]
    assert second_id != first_id
    with maker() as db:
        first = db.get(PaymentAttempt, first_id)
        assert first.status == "expired"
        second = db.get(PaymentAttempt, second_id); second.status = "superseded"; db.commit()
        result = process_v2_checkout_completed(db, _event(second, event_id="evt_superseded"))
        assert result["processing_result"] == "rejected_superseded"


def test_webhook_ledger_attempt_and_order_update_are_atomic(boundary, monkeypatch):
    client, maker, order_id, headers = boundary; _save_and_confirm(client, order_id, headers)
    attempt_id = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).json()["attempt_id"]
    with maker() as db:
        event = _event(db.get(PaymentAttempt, attempt_id), event_id="evt_atomic")
        original_commit = db.commit
        monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failure")))
        with pytest.raises(RuntimeError, match="commit failure"):
            process_v2_checkout_completed(db, event)
        db.rollback(); monkeypatch.setattr(db, "commit", original_commit)
    with maker() as db:
        assert db.get(Order, order_id).deposit_status == "checkout_created"
        assert db.get(PaymentAttempt, attempt_id).status == "checkout_created"
        assert db.execute(select(StripeWebhookEvent).where(StripeWebhookEvent.stripe_event_id == "evt_atomic")).scalar_one_or_none() is None


@pytest.mark.parametrize("change,expected", [
    ({"amount": 1}, "rejected_amount_mismatch"),
    ({"currency": "usd"}, "rejected_currency_mismatch"),
    ({"payment_status": "unpaid"}, "rejected_not_paid"),
    ({"scope_hash": "0" * 64}, "rejected_metadata_mismatch"),
])
def test_webhook_rejects_untrusted_completion(boundary, change, expected):
    client, maker, order_id, headers = boundary; _save_and_confirm(client, order_id, headers)
    attempt_id = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).json()["attempt_id"]
    with maker() as db:
        attempt = db.get(PaymentAttempt, attempt_id)
        assert process_v2_checkout_completed(db, _event(attempt, event_id=f"evt_{expected}", **change))["processing_result"] == expected
        assert db.get(Order, order_id).deposit_status != "paid"


def test_unknown_session_and_wrong_journey_status(boundary):
    client, maker, order_id, headers = boundary
    with maker() as db:
        event = {"id": "evt_unknown", "type": "checkout.session.completed", "data": {"object": {"id": "cs_unknown"}}}
        assert process_v2_checkout_completed(db, event)["processing_result"] == "rejected_unknown_session"
    other = client.post("/api/journey/session", headers=ORIGIN).json()["credential"]
    assert client.get(f"/api/orders/{order_id}/payment-status", headers={**ORIGIN, JOURNEY_CREDENTIAL_HEADER: other}).status_code == 403


def test_v2_legacy_bypass_routes_are_disabled(boundary):
    client, _, order_id, headers = boundary
    assert client.post("/api/payments/create-checkout", json={"amount": 1, "order_id": order_id}).status_code == 410
    assert client.post("/api/orders/extended-brief", json={"order_id": order_id}).status_code == 410
    assert client.post(f"/api/orders/{order_id}/payment-reported").status_code == 410
    assert client.get(f"/api/orders/confirm?order_id={order_id}").status_code == 410
    assert client.get(f"/api/orders/{order_id}").status_code == 410
    assert client.post(f"/api/orders/{order_id}/approve-design", json={}).status_code == 409
    assert client.post(f"/api/review/{order_id}/prepare").status_code == 409


def test_create_order_cannot_claim_or_mutate_v2_order(boundary, monkeypatch):
    client, maker, order_id, _headers = boundary
    created = SimpleNamespace(url="https://checkout.stripe.test/legacy")
    monkeypatch.setattr("app.api.create_order.stripe.checkout.Session.create", lambda **_kwargs: created)
    response = client.post("/api/create-order", json={"email": "other@example.com", "source": order_id})
    assert response.status_code == 200
    assert response.json()["order_id"] != order_id
    with maker() as db:
        order = db.get(Order, order_id)
        assert order.deposit_status == "not_started" and order.status == "draft"


def test_legacy_webhook_contract_cannot_mark_v2_order_paid(boundary, monkeypatch):
    client, maker, order_id, _headers = boundary
    event = {"id": "evt_legacy_for_v2", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_legacy", "amount_total": 1, "currency": "eur", "payment_status": "paid",
        "client_reference_id": order_id, "metadata": {"order_id": order_id, "type": "deposit"},
    }}}
    monkeypatch.setattr(stripe_webhook.stripe.Webhook, "construct_event", lambda *_args, **_kwargs: event)
    response = client.post("/api/payments/webhook", content=b"signed", headers={"stripe-signature": "test"})
    assert response.status_code == 200
    assert response.json()["reason"] == "v2_order_requires_persisted_payment_attempt"
    with maker() as db:
        order = db.get(Order, order_id)
        assert order.deposit_status == "not_started" and order.status == "draft"


def test_prepayment_summary_requires_journey_and_returns_no_sensitive_payload(boundary):
    client, _, order_id, headers = boundary
    assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload(legal=False)).status_code == 200
    response = client.get(f"/api/orders/{order_id}/prepayment-summary", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["order_id"] == order_id
    assert body["confirmation_state"]["brief_confirmed_at"] is None
    assert body["legal_terms_available"] is True
    serialized = response.text.lower()
    assert "owner@example.test" not in serialized
    assert "analysis_hints" not in serialized
    assert "siteformo_visitor" not in serialized
    assert "stripe" not in serialized
    assert "preferred_contact" not in serialized
    assert client.get(f"/api/orders/{order_id}/prepayment-summary", headers=ORIGIN).status_code == 403
    other = client.post("/api/journey/session", headers=ORIGIN).json()["credential"]
    assert client.get(f"/api/orders/{order_id}/prepayment-summary", headers={**ORIGIN, JOURNEY_CREDENTIAL_HEADER: other}).status_code == 403
    assert client.get(f"/api/orders/{uuid.uuid4()}/prepayment-summary", headers=headers).status_code == 403


def test_prepayment_summary_financials_match_checkout_snapshot(boundary):
    client, maker, order_id, headers = boundary
    payload = q2_payload(options=[
        {"key": "video_entry_page", "quantity": 1, "price_eur_each": 120, "counts_as_page": True, "explicit_confirmed": True},
    ])
    payload["logo"] = {"choice": "siteformo_simple_logo", "price_eur": 100, "scope": {
        "one_direction": True, "one_revision": True, "web_ready_delivery": True,
        "full_branding": False, "naming": False, "trademark_or_legal_clearance": False,
    }}
    payload["delivery_context"].update(installation_selected=True, installation_price_eur=125)
    _save_and_confirm(client, order_id, headers, payload)
    summary = client.get(f"/api/orders/{order_id}/prepayment-summary", headers=headers).json()
    payment = summary["payment_summary"]
    assert payment["base_package_price_cents"] == 90_000
    assert payment["initial_deposit_amount_cents"] == 45_000
    assert payment["addons_due_now_cents"] == 0
    assert payment["confirmed_addons_balance_cents"] == 34_500
    assert payment["current_final_balance_cents"] == 79_500
    assert {x["addon_key"] for x in payment["confirmed_addons"]} == {
        "video_entry_page", "simple_logo", "installation",
    }
    client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={})
    with maker() as db:
        snapshot = db.execute(select(PaymentAttempt)).scalar_one().scope_snapshot
    for key in (
        "base_package_price_cents", "initial_deposit_rate", "initial_deposit_amount_cents",
        "currency", "addons_due_now_cents", "remaining_base_package_balance_cents",
        "confirmed_addons_balance_cents", "current_final_balance_cents",
    ):
        assert payment[key] == snapshot[key]
    assert summary["scope"]["scope_hash"] == snapshot["scope_hash"]


@pytest.mark.parametrize("package,base,deposit", [
    ("starter", 90_000, 45_000),
    ("business", 150_000, 75_000),
    ("advanced", 450_000, 225_000),
])
def test_summary_uses_authoritative_package_registry(boundary, monkeypatch, package, base, deposit):
    client, _, order_id, headers = boundary
    assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload()).status_code == 200
    original = __import__("app.services.payment_boundary_service", fromlist=["qualify_scope"]).qualify_scope
    monkeypatch.setattr("app.services.payment_boundary_service.qualify_scope", lambda payload: {
        **original(payload), "recommended_package": package, "minimum_package": package,
    })
    payment = client.get(f"/api/orders/{order_id}/prepayment-summary", headers=headers).json()["payment_summary"]
    assert payment["base_package_price_cents"] == base
    assert payment["initial_deposit_amount_cents"] == deposit


def test_reference_prepayment_summary_remains_blocked(boundary, monkeypatch):
    client, _, order_id, headers = boundary
    assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload()).status_code == 200
    original = __import__("app.services.payment_boundary_service", fromlist=["qualify_scope"]).qualify_scope
    monkeypatch.setattr("app.services.payment_boundary_service.qualify_scope", lambda payload: {
        **original(payload), "recommended_package": "reference", "minimum_package": "reference",
    })
    response = client.get(f"/api/orders/{order_id}/prepayment-summary", headers=headers)
    assert response.status_code == 409 and "Reference" in response.text


def test_prepayment_email_gates_idempotency_retry_and_scope_change(boundary, monkeypatch):
    client, maker, order_id, headers = boundary
    assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=q2_payload()).status_code == 200
    calls = []

    async def sender(to, subject, html):
        calls.append((to, subject, html))
        return {"id": "email_test"}

    monkeypatch.setattr(payment_boundary_routes, "send_email", sender)
    endpoint = f"/api/orders/{order_id}/prepayment-summary-email"
    assert client.post(endpoint, headers=headers, json={}).status_code == 409
    assert client.post(f"/api/orders/{order_id}/payment-confirmation", headers=headers, json={
        "brief_confirmed": True, "legal_confirmed": False,
    }).status_code == 200
    assert client.post(endpoint, headers=headers, json={}).status_code == 409
    assert client.post(f"/api/orders/{order_id}/payment-confirmation", headers=headers, json={
        "brief_confirmed": False, "legal_confirmed": True, "legal_terms_version": "wrong",
    }).status_code == 409
    assert client.post(f"/api/orders/{order_id}/payment-confirmation", headers=headers, json={
        "brief_confirmed": False, "legal_confirmed": True, "legal_terms_version": TERMS,
    }).status_code == 200
    sent = client.post(endpoint, headers=headers, json={})
    assert sent.status_code == 200 and sent.json()["result"] == "sent"
    assert len(calls) == 1
    assert calls[0][0] == "owner@example.test"
    assert "50% of the base package only" in calls[0][2]
    assert "Optional services are not charged" in calls[0][2]
    again = client.post(endpoint, headers=headers, json={})
    assert again.json()["result"] == "already_sent" and len(calls) == 1

    changed = q2_payload()
    changed["trust_materials"] = ["reviews", "certifications"]
    assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=changed).status_code == 200
    summary = client.get(f"/api/orders/{order_id}/prepayment-summary", headers=headers).json()
    assert summary["email_state"] == {"status": "pending", "sent_at": None}
    resent = client.post(endpoint, headers=headers, json={})
    assert resent.json()["result"] == "sent" and len(calls) == 2
    with maker() as db:
        order = db.get(Order, order_id)
        assert order.prepayment_summary_email_status == "sent"
        assert order.deposit_status == "not_started" and order.deposit_paid_at is None


def test_failed_prepayment_email_is_retryable(boundary, monkeypatch):
    client, _, order_id, headers = boundary
    _save_and_confirm(client, order_id, headers, request_email=False)
    calls = 0

    async def sender(_to, _subject, _html):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider unavailable")
        return {"id": "email_retry"}

    monkeypatch.setattr(payment_boundary_routes, "send_email", sender)
    endpoint = f"/api/orders/{order_id}/prepayment-summary-email"
    failed = client.post(endpoint, headers=headers, json={}).json()
    assert failed == {"order_id": order_id, "result": "failed", "email_status": "failed", "sent_at": None, "retry_allowed": True}
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 200
    retried = client.post(endpoint, headers=headers, json={}).json()
    assert retried["result"] == "sent" and calls == 2


def test_email_endpoint_rejects_browser_authored_summary_fields(boundary):
    client, _, order_id, headers = boundary
    _save_and_confirm(client, order_id, headers)
    response = client.post(f"/api/orders/{order_id}/prepayment-summary-email", headers=headers, json={
        "amount": 1, "recipient": "attacker@example.test", "summary": {"base_package_price": 1},
    })
    assert response.status_code == 422


def test_prepayment_email_requires_current_journey(boundary):
    client, _, order_id, headers = boundary
    _save_and_confirm(client, order_id, headers)
    endpoint = f"/api/orders/{order_id}/prepayment-summary-email"
    assert client.post(endpoint, headers=ORIGIN, json={}).status_code == 403
    other = client.post("/api/journey/session", headers=ORIGIN).json()["credential"]
    assert client.post(endpoint, headers={**ORIGIN, JOURNEY_CREDENTIAL_HEADER: other}, json={}).status_code == 403
    assert client.post(f"/api/orders/{uuid.uuid4()}/prepayment-summary-email", headers=headers, json={}).status_code == 403


def test_changed_approved_legal_version_invalidates_previous_email(boundary, monkeypatch):
    client, _, order_id, headers = boundary
    _save_and_confirm(client, order_id, headers, request_email=False)

    async def sender(_to, _subject, _html):
        return {"id": "email_legal_version"}

    monkeypatch.setattr(payment_boundary_routes, "send_email", sender)
    endpoint = f"/api/orders/{order_id}/prepayment-summary-email"
    assert client.post(endpoint, headers=headers, json={}).json()["result"] == "sent"
    monkeypatch.setenv("SITEFORMO_LEGAL_TERMS_VERSION", "local-test-terms-v2")
    summary = client.get(f"/api/orders/{order_id}/prepayment-summary", headers=headers).json()
    assert summary["email_state"] == {"status": "pending", "sent_at": None}
    assert summary["approved_legal_terms_version"] == "local-test-terms-v2"
    blocked = client.post(endpoint, headers=headers, json={})
    assert blocked.status_code == 409 and "no longer current" in blocked.text
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 409


@pytest.mark.parametrize("operation_state,provider_attempted,allowed", [
    ("pending", False, False),
    ("sending", False, False),
    ("failed", False, False),
    ("failed", True, True),
    ("sent", True, True),
])
def test_checkout_requires_proven_current_scope_email_attempt(
    boundary, operation_state, provider_attempted, allowed,
):
    client, maker, order_id, headers = boundary
    _save_and_confirm(client, order_id, headers, request_email=False)
    summary = client.get(f"/api/orders/{order_id}/prepayment-summary", headers=headers).json()
    signature = _email_scope_signature(summary["scope"]["scope_hash"], TERMS)
    with maker() as db:
        order = db.get(Order, order_id)
        extended = dict(order.extended_brief or {})
        meta = {"signature": signature, "operation_state": operation_state}
        if provider_attempted:
            meta["provider_attempted_at"] = datetime.now(timezone.utc).isoformat()
        if operation_state == "sending":
            meta["requested_at"] = datetime.now(timezone.utc).isoformat()
        extended["payment_boundary_v2_email"] = meta
        order.extended_brief = extended
        order.prepayment_summary_email_status = operation_state if operation_state in {"sent", "failed"} else "pending"
        db.commit()
    response = client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={})
    assert (response.status_code == 200) is allowed


def test_checkout_blocks_when_email_was_never_attempted(boundary):
    client, _, order_id, headers = boundary
    _save_and_confirm(client, order_id, headers, request_email=False)
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 409


def test_email_validation_failure_is_not_provider_attempt_proof(boundary):
    client, maker, order_id, headers = boundary
    _save_and_confirm(client, order_id, headers, request_email=False)
    with maker() as db:
        order = db.get(Order, order_id)
        order.client.email = None
        db.commit()
    email = client.post(f"/api/orders/{order_id}/prepayment-summary-email", headers=headers, json={})
    assert email.status_code == 409
    with maker() as db:
        order = db.get(Order, order_id)
        assert not (order.extended_brief or {}).get("payment_boundary_v2_email")
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 409


@pytest.mark.parametrize("old_state", ["sent", "failed"])
def test_stale_email_attempt_cannot_checkout_changed_q2(boundary, old_state):
    client, maker, order_id, headers = boundary
    _save_and_confirm(client, order_id, headers)
    if old_state == "failed":
        with maker() as db:
            order = db.get(Order, order_id)
            order.prepayment_summary_email_status = "failed"
            extended = dict(order.extended_brief or {})
            meta = dict(extended["payment_boundary_v2_email"])
            meta["operation_state"] = "failed"
            extended["payment_boundary_v2_email"] = meta
            order.extended_brief = extended
            db.commit()
    changed = q2_payload()
    changed["trust_materials"] = ["reviews", "certifications"]
    assert client.patch(f"/api/orders/{order_id}/q2", headers=headers, json=changed).status_code == 200
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 409


def test_checkout_never_triggers_email_provider(boundary, monkeypatch):
    client, _, order_id, headers = boundary
    calls = 0

    async def sender(_to, _subject, _html):
        nonlocal calls
        calls += 1
        return {"id": "one_email"}

    monkeypatch.setattr(payment_boundary_routes, "send_email", sender)
    _save_and_confirm(client, order_id, headers, request_email=False)
    assert client.post(f"/api/orders/{order_id}/prepayment-summary-email", headers=headers, json={}).status_code == 200
    assert calls == 1
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 200
    assert client.post(f"/api/orders/{order_id}/checkout", headers=headers, json={}).status_code == 200
    assert calls == 1
