"""POST /payments/webhook/ - signature handling and idempotency.

This is the file that matters most: a payment provider will redeliver the
same event, out of order, more than once, and after we have already acted on
it. None of that may create a second payment or move a booking twice.
"""
from __future__ import annotations

import time
import uuid

import pytest
from django.urls import reverse

from apps.bookings.models import BookingStatus
from apps.payments.models import (
    Payment,
    PaymentStatus,
    WebhookEvent,
    WebhookEventStatus,
)
from tests.factories import PaymentFactory

pytestmark = pytest.mark.django_db


def make_event(payment, *, event_type="payment.succeeded", event_id=None, **overrides):
    """Build the body the provider would POST for this payment."""
    data = {
        "payment_reference": payment.reference,
        "provider_reference": payment.provider_reference,
        "booking_reference": payment.booking.reference,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "failure_code": "",
        "failure_reason": "",
    }
    data.update(overrides.pop("data", {}))
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex[:24]}",
        "type": event_type,
        "created_at": "2026-09-29T10:00:00Z",
        "data": data,
        **overrides,
    }


@pytest.fixture
def pending_payment(booking):
    """A payment the provider has accepted but not yet settled."""
    return PaymentFactory(
        booking=booking,
        status=PaymentStatus.PENDING,
        provider_reference=f"sim_pay_{uuid.uuid4().hex[:20]}",
    )


class TestSignatureVerification:
    def test_rejects_an_unsigned_delivery(self, post_webhook, pending_payment):
        response = post_webhook(make_event(pending_payment), sign=False)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_signature"
        assert WebhookEvent.objects.count() == 0

    def test_rejects_a_signature_made_with_the_wrong_secret(
        self, post_webhook, pending_payment
    ):
        response = post_webhook(make_event(pending_payment), secret="not-the-secret")

        assert response.status_code == 401
        assert WebhookEvent.objects.count() == 0

    def test_rejects_a_stale_timestamp(self, post_webhook, pending_payment):
        old = int(time.time()) - 3600

        response = post_webhook(make_event(pending_payment), timestamp=old)

        assert response.status_code == 401

    def test_rejects_a_tampered_body(self, api_client, webhook_url, pending_payment):
        import json

        from apps.payments.signature import build_header

        original = json.dumps(make_event(pending_payment)).encode()
        header = build_header("test-webhook-secret", original)
        tampered = json.dumps(
            make_event(pending_payment, event_type="payment.failed")
        ).encode()

        response = api_client.post(
            webhook_url,
            data=tampered,
            content_type="application/json",
            HTTP_X_WEBHOOK_SIGNATURE=header,
        )

        assert response.status_code == 401

    def test_accepts_a_correctly_signed_delivery(self, post_webhook, pending_payment):
        response = post_webhook(make_event(pending_payment))

        assert response.status_code == 200

    def test_signature_checking_can_be_disabled_for_local_experiments(
        self, settings, post_webhook, pending_payment
    ):
        settings.PAYMENT_WEBHOOK_REQUIRE_SIGNATURE = False

        response = post_webhook(make_event(pending_payment), sign=False)

        assert response.status_code == 200


class TestMalformedPayloads:
    def test_rejects_a_body_that_is_not_json(self, api_client, webhook_url):
        from apps.payments.signature import build_header

        body = b"<html>not json</html>"

        response = api_client.post(
            webhook_url,
            data=body,
            content_type="application/json",
            HTTP_X_WEBHOOK_SIGNATURE=build_header("test-webhook-secret", body),
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    def test_rejects_a_json_array(self, post_webhook):
        response = post_webhook(["not", "an", "object"])

        assert response.status_code == 400

    @pytest.mark.parametrize("missing", ["id", "type"])
    def test_requires_id_and_type(self, post_webhook, pending_payment, missing):
        payload = make_event(pending_payment)
        del payload[missing]

        response = post_webhook(payload)

        assert response.status_code == 400
        assert WebhookEvent.objects.count() == 0


class TestProcessing:
    def test_a_success_event_confirms_the_payment_and_the_booking(
        self, post_webhook, pending_payment, booking
    ):
        response = post_webhook(make_event(pending_payment))

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "received": True,
            "duplicate": False,
            "event_id": body["event_id"],
            "status": WebhookEventStatus.PROCESSED,
            "result": "booking_confirmed",
        }

        pending_payment.refresh_from_db()
        booking.refresh_from_db()
        assert pending_payment.status == PaymentStatus.SUCCESS
        assert pending_payment.processed_at is not None
        assert booking.status == BookingStatus.CONFIRMED

    def test_a_failure_event_fails_the_payment_and_the_booking(
        self, post_webhook, pending_payment, booking
    ):
        payload = make_event(
            pending_payment,
            event_type="payment.failed",
            data={"failure_code": "card_declined", "failure_reason": "Declined."},
        )

        response = post_webhook(payload)

        assert response.status_code == 200
        pending_payment.refresh_from_db()
        booking.refresh_from_db()
        assert pending_payment.status == PaymentStatus.FAILED
        assert pending_payment.failure_code == "card_declined"
        assert booking.status == BookingStatus.FAILED

    def test_records_the_transition_source_as_webhook(
        self, post_webhook, pending_payment, booking
    ):
        post_webhook(make_event(pending_payment))

        change = booking.status_changes.filter(to_status=BookingStatus.CONFIRMED).first()
        assert change.source == "webhook"

    def test_resolves_the_payment_by_our_own_reference_too(
        self, post_webhook, pending_payment, booking
    ):
        payload = make_event(pending_payment, data={"provider_reference": None})

        response = post_webhook(payload)

        assert response.json()["result"] == "booking_confirmed"
        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED

    def test_an_unknown_payment_is_stored_and_ignored(self, post_webhook, pending_payment):
        payload = make_event(
            pending_payment,
            data={"payment_reference": "PAY-GHOST", "provider_reference": "sim_pay_ghost"},
        )

        response = post_webhook(payload)

        assert response.status_code == 200
        assert response.json()["result"] == "unknown_payment"
        assert WebhookEvent.objects.get().status == WebhookEventStatus.IGNORED

    def test_an_unsupported_event_type_is_ignored(self, post_webhook, pending_payment):
        response = post_webhook(
            make_event(pending_payment, event_type="invoice.generated")
        )

        assert response.status_code == 200
        assert response.json()["result"] == "unsupported_event_type"
        pending_payment.refresh_from_db()
        assert pending_payment.status == PaymentStatus.PENDING

    def test_an_amount_mismatch_is_refused(self, post_webhook, pending_payment, booking):
        response = post_webhook(make_event(pending_payment, data={"amount": "1.00"}))

        assert response.json()["result"] == "amount_mismatch"
        pending_payment.refresh_from_db()
        booking.refresh_from_db()
        assert pending_payment.status == PaymentStatus.PENDING
        assert booking.status == BookingStatus.PENDING

    def test_a_pending_event_changes_nothing(self, post_webhook, pending_payment, booking):
        post_webhook(make_event(pending_payment, event_type="payment.pending"))

        pending_payment.refresh_from_db()
        booking.refresh_from_db()
        assert pending_payment.status == PaymentStatus.PENDING
        assert booking.status == BookingStatus.PENDING


class TestIdempotency:
    """The core requirement: redelivery must never duplicate anything."""

    def test_the_same_event_delivered_five_times_changes_nothing_after_the_first(
        self, post_webhook, pending_payment, booking
    ):
        payload = make_event(pending_payment, event_id="evt_fixed_identifier")

        responses = [post_webhook(payload) for _ in range(5)]

        assert [r.status_code for r in responses] == [200] * 5
        assert responses[0].json()["duplicate"] is False
        assert all(r.json()["duplicate"] is True for r in responses[1:])

        # Exactly one of everything, with every redelivery counted.
        assert WebhookEvent.objects.count() == 1
        assert WebhookEvent.objects.get().delivery_count == 5
        assert Payment.objects.count() == 1
        assert booking.status_changes.filter(
            to_status=BookingStatus.CONFIRMED
        ).count() == 1

        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED

    def test_a_duplicate_reports_the_original_outcome(self, post_webhook, pending_payment):
        payload = make_event(pending_payment, event_id="evt_repeat")
        first = post_webhook(payload).json()

        second = post_webhook(payload).json()

        assert second["duplicate"] is True
        assert second["result"] == first["result"]
        assert second["status"] == WebhookEventStatus.PROCESSED

    def test_a_distinct_event_asserting_the_same_outcome_is_a_no_op(
        self, post_webhook, pending_payment, booking
    ):
        post_webhook(make_event(pending_payment, event_id="evt_one"))

        second = post_webhook(make_event(pending_payment, event_id="evt_two"))

        assert second.status_code == 200
        assert second.json()["duplicate"] is False
        assert second.json()["result"] == "already_success"
        assert WebhookEvent.objects.count() == 2  # both stored...
        assert booking.status_changes.filter(
            to_status=BookingStatus.CONFIRMED
        ).count() == 1  # ...but the booking moved once

    def test_a_contradictory_later_event_does_not_flip_a_settled_payment(
        self, post_webhook, pending_payment, booking
    ):
        post_webhook(make_event(pending_payment, event_type="payment.succeeded"))

        late = post_webhook(
            make_event(pending_payment, event_type="payment.failed")
        )

        assert late.status_code == 200
        assert late.json()["result"] == "conflicting_outcome_ignored"
        pending_payment.refresh_from_db()
        booking.refresh_from_db()
        assert pending_payment.status == PaymentStatus.SUCCESS
        assert booking.status == BookingStatus.CONFIRMED

    def test_a_webhook_confirming_a_charge_made_synchronously_is_a_no_op(
        self, auth_client, booking
    ):
        """The realistic sequence: POST /payments/ settles, then the provider
        calls back about the same payment."""
        from apps.payments.gateway import SimulatedPaymentGateway
        from apps.payments.services import ingest_webhook_event, process_webhook_event

        auth_client.post(
            reverse("v1:payments:payment-list"),
            {"booking": booking.reference, "outcome": "success"},
        )
        payment = Payment.objects.get()
        event = SimulatedPaymentGateway.build_event(payment)

        stored, created = ingest_webhook_event(
            event_id=event["id"], event_type=event["type"], payload=event
        )
        result = process_webhook_event(stored)

        assert created is True
        assert result == "already_success"
        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED
        assert booking.status_changes.filter(
            to_status=BookingStatus.CONFIRMED
        ).count() == 1


class TestRaces:
    def test_a_success_arriving_after_cancellation_is_flagged_for_refund(
        self, post_webhook, pending_payment, booking
    ):
        """The patient cancelled while the charge was in flight. The money was
        taken, so the payment must record SUCCESS - but a cancelled booking is
        not quietly confirmed."""
        booking.status = BookingStatus.CANCELLED
        booking.save(update_fields=["status"])

        response = post_webhook(make_event(pending_payment))

        assert response.status_code == 200
        assert response.json()["result"] == "refund_required"
        pending_payment.refresh_from_db()
        booking.refresh_from_db()
        assert pending_payment.status == PaymentStatus.SUCCESS
        assert booking.status == BookingStatus.CANCELLED

    def test_a_failure_arriving_after_cancellation_is_absorbed(
        self, post_webhook, pending_payment, booking
    ):
        booking.status = BookingStatus.CANCELLED
        booking.save(update_fields=["status"])

        response = post_webhook(make_event(pending_payment, event_type="payment.failed"))

        assert response.json()["result"] == "booking_already_terminal"
        booking.refresh_from_db()
        assert booking.status == BookingStatus.CANCELLED


class TestRetries:
    def test_a_failed_event_is_stored_and_can_be_replayed(
        self, post_webhook, pending_payment, booking, monkeypatch
    ):
        from apps.payments import services

        def explode(*args, **kwargs):
            raise RuntimeError("database went away")

        monkeypatch.setattr(services, "apply_payment_outcome", explode)

        response = post_webhook(make_event(pending_payment, event_id="evt_boom"))

        # 500 tells the provider to retry; the event survives for our own retry.
        assert response.status_code == 500
        event = WebhookEvent.objects.get(event_id="evt_boom")
        assert event.status == WebhookEventStatus.FAILED
        assert "database went away" in event.last_error
        assert event.attempts == 1

        monkeypatch.undo()
        assert services.process_webhook_event(event) == "booking_confirmed"

        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED

    def test_the_replay_command_picks_up_failed_events(self, pending_payment, booking):
        from django.core.management import call_command

        from apps.payments.services import ingest_webhook_event

        event_body = make_event(pending_payment, event_id="evt_replay")
        event, _ = ingest_webhook_event(
            event_id=event_body["id"],
            event_type=event_body["type"],
            payload=event_body,
        )
        WebhookEvent.objects.filter(pk=event.pk).update(
            status=WebhookEventStatus.FAILED
        )

        call_command("replay_failed_webhooks")

        event.refresh_from_db()
        booking.refresh_from_db()
        assert event.status == WebhookEventStatus.PROCESSED
        assert booking.status == BookingStatus.CONFIRMED


def test_the_literal_assignment_path_is_mounted(api_client, pending_payment, post_webhook):
    """The brief names POST /payments/webhook/; it is aliased at the root."""
    response = post_webhook(make_event(pending_payment), url="/payments/webhook/")

    assert response.status_code == 200
